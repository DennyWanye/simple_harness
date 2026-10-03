# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""BW09: the real ``SkillAcceptancePort`` over the Assurance 1.1 ledger.

2026-10-03（HTN 补齐阶段 A′）迁到产品同形世界：评测任务经产品那一份部署组装、以评测自己的
幂等键建出（Host 派发评测时就是这样建的），主循环真跑：规划 → 执行者 → 独立内容审阅 → 原版
验收写入（签 ACCEPT 使用许可证）→ 终审 → 根结论（签 ROOT_RESOLUTION 许可证）。ARP 运行时（真实
的目录、生命周期、派发链接）只凭这些许可证准入技能。没有任何替身签发 PASS；模型回复是脚本。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from arp_fixture import build, trusted_caller
from provider_fixture import ScriptedProvider
from skill_fixture import activation_of, import_skill, md_bundle, trial_command

from simple_harness.agents.arp.assurance_acceptance import ASSURANCE_1_1, AssuranceSkillAcceptance, evaluation_mission_key
from simple_harness.agents.arp.errors import ArpError
from simple_harness.agents.arp.pins import Pin

SDK_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SDK_ROOT / "tests/orchestrator/full_target/assurance_exec"))

from _review_world import ReviewScript, quick_waits  # noqa: E402

from agent_orchestrator.testing.product_world import USER_GOAL_NAMES, product_world  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def _admit(binding: dict, acceptance: Pin, revision) -> dict:  # type: ignore[no-untyped-def]
    return {
        "schema_version": 1, "skill_ref": revision.pin.to_json(), "evaluation_acceptance_ref": acceptance.to_json(),
        "evaluation_policy_ref": binding["policy_ref"], "scope_ref": binding["scope_ref"],
    }


def _certificate(store, mission_id: str, consumer_kind: str) -> Pin:  # type: ignore[no-untyped-def]
    rows = store.connection.execute(
        "SELECT certificate_id, certificate_hash, purpose FROM assurance_use_certificates "
        "WHERE mission_id=? AND consumer_kind=?", (mission_id, consumer_kind)).fetchall()
    assert len(rows) == 1 and rows[0][2] == "ACCEPT", [tuple(r) for r in rows]
    return Pin("acceptance", str(rows[0][0]), 0, str(rows[0][1]))


class Evaluation:
    """A product world, an ARP runtime and one Skill in trial whose evaluation Mission the
    product created under the evaluation's own key."""

    def __init__(self, world, runtime, reader, revision, binding, evaluation, mission_id, provider):  # type: ignore[no-untyped-def]
        self.world, self.runtime, self.reader = world, runtime, reader
        self.revision, self.binding, self.evaluation = revision, binding, evaluation
        self.mission_id, self.provider = mission_id, provider

    @property
    def lifecycle(self):  # type: ignore[no-untyped-def]
        return self.runtime.arp.lifecycle

    def leaf_task(self) -> str:
        [task] = [t.id for t in self.world.store.list_tasks(self.mission_id)
                  if not t.id.startswith(USER_GOAL_NAMES.task_prefix)]
        return task

    async def run_until(self, done, *, timeout: float = 30.0) -> None:  # type: ignore[no-untyped-def]
        async def drive() -> None:
            while True:
                await self.world.loop.run()
                await self.world.deployment.between_cycles(auto=self.world.auto)
                await asyncio.sleep(0.01)

        task = asyncio.create_task(drive())
        try:
            async with asyncio.timeout(timeout):
                while not done():
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def status(self) -> str:
        return str(self.world.store.get_mission(self.mission_id).status.value)


async def _evaluation(tmp_path, provider, body) -> None:  # type: ignore[no-untyped-def]
    async with product_world(tmp_path / "product", provider) as world:
        gate = world.loop._assurance_root_gate
        reader = AssuranceSkillAcceptance(store=world.store, clock_ms=lambda: int(world.store.now * 1000),
                                          root_incarnation=gate.require_execution)
        runtime = build(tmp_path / "arp", ScriptedProvider([]), acceptance=reader)
        async with runtime:
            lifecycle = runtime.arp.lifecycle
            assert reader.dispatches is not None  # bound by the runtime factory
            revision = import_skill(runtime, md_bundle("assured-skill"), command="i1").revision
            binding = lifecycle.begin_trial(trial_command(runtime, revision), caller=trusted_caller(), command_id="t1")
            evaluation = Pin.from_json(binding["evaluation_ref"])
            created = world.create({"goal": "按技能写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                                    "idempotency_key": evaluation_mission_key(evaluation)})
            try:
                await body(Evaluation(world, runtime, reader, revision, binding, evaluation, created["mission_id"],
                                      provider))
            finally:
                provider.close()


def test_skill_is_admitted_only_through_the_official_assurance_acceptance(tmp_path) -> None:
    provider = ReviewScript(hold="TASK_CONTENT")

    async def body(ev: Evaluation) -> None:
        lifecycle, mission_id = ev.lifecycle, ev.mission_id
        await ev.run_until(provider.review_entered.is_set)
        task_id = ev.leaf_task()
        # Before the acceptance writer ran there is no certificate at all: a pin that names an
        # absent certificate is refused by name, nothing is admitted.
        absent = Pin("acceptance", "cert-absent", 0, "0" * 64)
        with pytest.raises(ArpError) as no_task:
            lifecycle.record_evaluation_dispatch(ev.evaluation, mission_id=mission_id, task_id="",
                                                 caller=trusted_caller(), command_id="d0")
        assert no_task.value.code == "MISSING_FIELD"
        lifecycle.record_evaluation_dispatch(ev.evaluation, mission_id=mission_id, task_id=task_id,
                                             caller=trusted_caller(), command_id="d1")
        with pytest.raises(ArpError) as missing:
            lifecycle.admit(_admit(ev.binding, absent, ev.revision), caller=trusted_caller(), command_id="a0")
        assert missing.value.code == "SKILL_EVALUATION_INCOMPLETE" and missing.value.detail["reason"] == "CERTIFICATE_MISSING"
        assert missing.value.detail["successor"] == ASSURANCE_1_1

        # The independent review answers; the original acceptance writer commits Acceptance +
        # certificate + receipt together.
        provider.go.set()
        await ev.run_until(lambda: ev.world.store.connection.execute(
            "SELECT count(*) FROM assurance_use_certificates WHERE mission_id=? AND consumer_kind='ACCEPTANCE'",
            (mission_id,)).fetchone()[0] == 1)
        acceptance = _certificate(ev.world.store, mission_id, "ACCEPTANCE")

        # A pin with the right id but another hash cannot pass as the certificate.
        with pytest.raises(ArpError) as forged:
            lifecycle.admit(_admit(ev.binding, Pin("acceptance", acceptance.id, 0, "f" * 64), ev.revision),
                            caller=trusted_caller(), command_id="a1")
        assert forged.value.code == "SOURCE_HASH_CONFLICT"
        assert activation_of(ev.runtime, ev.revision).state == "TRIAL"

        # Under the current root, with the dispatch link and the USABLE ACCEPT certificate, the
        # Skill is admitted; the admission records that certificate.
        admitted = lifecycle.admit(_admit(ev.binding, acceptance, ev.revision), caller=trusted_caller(), command_id="a2")
        assert admitted.state == "ADMITTED" and lifecycle.admission_for(ev.revision, ev.evaluation) == acceptance
        view = ev.reader.verify(ev.binding, acceptance)
        assert view["accepted"] is True and view["mission_id"] == mission_id and view["task_id"] == task_id
        assert view["certificate_hash"] == acceptance.content_hash and view["successor"] == ASSURANCE_1_1
        # Re-sent admit: the same admission, no second receipt.
        assert lifecycle.admit(_admit(ev.binding, acceptance, ev.revision), caller=trusted_caller(),
                               command_id="a2").row_version == admitted.row_version

        # A second evaluation cannot borrow the same mission's certificate: the dispatch link
        # is one mission per evaluation.
        other = import_skill(ev.runtime, md_bundle("other-skill"), command="i2").revision
        other_binding = lifecycle.begin_trial(trial_command(ev.runtime, other), caller=trusted_caller(), command_id="t2")
        other_evaluation = Pin.from_json(other_binding["evaluation_ref"])
        with pytest.raises(ArpError) as undispatched:
            lifecycle.admit(_admit(other_binding, acceptance, other), caller=trusted_caller(), command_id="a3")
        assert undispatched.value.detail["reason"] == "EVALUATION_NOT_DISPATCHED"
        with pytest.raises(ArpError) as taken:
            lifecycle.record_evaluation_dispatch(other_evaluation, mission_id=mission_id, task_id=task_id,
                                                 caller=trusted_caller(), command_id="d2")
        assert taken.value.code == "SOURCE_HASH_CONFLICT"
        # Dispatched to a mission that does not exist in the ledger: refused by name.
        lifecycle.record_evaluation_dispatch(other_evaluation, mission_id="mission-elsewhere", task_id=task_id,
                                             caller=trusted_caller(), command_id="d2")
        with pytest.raises(ArpError) as mismatch:
            lifecycle.admit(_admit(other_binding, acceptance, other), caller=trusted_caller(), command_id="a3")
        assert mismatch.value.detail["reason"] == "MISSION_MISSING"
        assert activation_of(ev.runtime, other).state == "TRIAL"
        # The dispatch link is immutable: re-sent returns it, another mission is refused.
        assert lifecycle.record_evaluation_dispatch(other_evaluation, mission_id="mission-elsewhere", task_id=task_id,
                                                    caller=trusted_caller(), command_id="d2")["mission_id"] == "mission-elsewhere"
        with pytest.raises(ArpError) as moved:
            lifecycle.record_evaluation_dispatch(other_evaluation, mission_id="mission-third", task_id=task_id,
                                                 caller=trusted_caller(), command_id="d3")
        assert moved.value.code == "SOURCE_HASH_CONFLICT"

        # A certificate issued under another root incarnation is refused: a restored root must
        # re-authorize, it does not inherit admissions.
        stale_root = AssuranceSkillAcceptance(store=ev.world.store, clock_ms=ev.reader.clock_ms,
                                              root_incarnation=lambda: "root-restored")
        stale_root.bind_lifecycle(lifecycle)
        with pytest.raises(ArpError) as restored:
            stale_root.verify(ev.binding, acceptance)
        assert restored.value.detail["reason"] == "ROOT_CHANGED"
        # An expired certificate is refused by the clock.
        late = AssuranceSkillAcceptance(store=ev.world.store, clock_ms=lambda: 2**53, root_incarnation=None)
        late.bind_lifecycle(lifecycle)
        not_after = ev.world.store.connection.execute(
            "SELECT not_after_ms FROM assurance_use_certificates WHERE certificate_id=?", (acceptance.id,)).fetchone()[0]
        if not_after is not None:
            with pytest.raises(ArpError) as expired:
                late.verify(ev.binding, acceptance)
            assert expired.value.detail["reason"] == "CERTIFICATE_EXPIRED"
        # An unbound reader cannot answer at all.
        with pytest.raises(ArpError) as unbound:
            AssuranceSkillAcceptance(store=ev.world.store, clock_ms=ev.reader.clock_ms).verify(ev.binding, acceptance)
        assert unbound.value.code == "SOURCE_UNAVAILABLE"

    asyncio.run(_evaluation(tmp_path, provider, body))


def test_an_acceptance_issued_before_the_dispatch_was_recorded_cannot_admit(tmp_path) -> None:
    """Review finding (2026-09-24): linking an evaluation to an already-accepted Mission after
    the fact must not promote the Skill, even when the Mission carries the evaluation's key."""

    provider = ReviewScript()

    async def body(ev: Evaluation) -> None:
        await ev.run_until(lambda: ev.status() == "COMPLETED")
        acceptance = _certificate(ev.world.store, ev.mission_id, "ACCEPTANCE")
        ev.lifecycle.record_evaluation_dispatch(ev.evaluation, mission_id=ev.mission_id, task_id=ev.leaf_task(),
                                                caller=trusted_caller(), command_id="d1")
        with pytest.raises(ArpError) as late:
            ev.lifecycle.admit(_admit(ev.binding, acceptance, ev.revision), caller=trusted_caller(), command_id="a1")
        assert late.value.code == "SKILL_EVALUATION_INCOMPLETE" and late.value.detail["reason"] == "ACCEPTANCE_BEFORE_DISPATCH"
        assert activation_of(ev.runtime, ev.revision).state == "TRIAL"

    asyncio.run(_evaluation(tmp_path, provider, body))


async def _root_evaluation(tmp_path, link):  # type: ignore[no-untyped-def]
    """``link(ev)`` records the dispatch before anything ran (None: after the root resolution)."""

    out: dict = {}
    provider = ReviewScript()

    async def body(ev: Evaluation) -> None:
        if link is not None:
            link(ev)
        await ev.run_until(lambda: ev.status() == "COMPLETED")
        certificate = _certificate(ev.world.store, ev.mission_id, "ROOT_RESOLUTION")
        goal_task = USER_GOAL_NAMES.task_prefix + ev.mission_id
        if link is None:
            ev.lifecycle.record_evaluation_dispatch(ev.evaluation, mission_id=ev.mission_id, task_id=goal_task,
                                                    caller=trusted_caller(), command_id="d-late")
        out.update(goal_task=goal_task, certificate=certificate)
        try:
            out["admitted"] = ev.lifecycle.admit(_admit(ev.binding, certificate, ev.revision), caller=trusted_caller(),
                                                 command_id="a1")
            out["view"] = ev.reader.verify(ev.binding, certificate)
        except ArpError as error:
            out["refused"] = error
        out["state"] = activation_of(ev.runtime, ev.revision).state
        other = import_skill(ev.runtime, md_bundle("other-skill"), command="i2").revision
        other_binding = ev.lifecycle.begin_trial(trial_command(ev.runtime, other), caller=trusted_caller(), command_id="t2")
        try:
            ev.lifecycle.admit(_admit(other_binding, certificate, other), caller=trusted_caller(), command_id="a2")
        except ArpError as error:
            out["borrowed"] = error

    await _evaluation(tmp_path, provider, body)
    return out


def _link_root(ev: Evaluation) -> None:
    # The Mission-scope root binding exists from creation (the Host links its fixed root the
    # same way, before anything ran).
    root = USER_GOAL_NAMES.task_prefix + ev.mission_id
    ev.lifecycle.record_evaluation_dispatch(ev.evaluation, mission_id=ev.mission_id, task_id=root,
                                            caller=trusted_caller(), command_id="d1")


def test_a_content_evaluation_is_admitted_through_its_root_resolution(tmp_path) -> None:
    """NEXT-TG-1.0 §11 (2026-09-29 真机): the evaluation linked to the root task is admitted
    through the independent final review's ROOT_RESOLUTION certificate; another evaluation
    cannot borrow it."""
    out = asyncio.run(_root_evaluation(tmp_path, _link_root))
    assert "refused" not in out, out.get("refused") and out["refused"].detail
    assert out["state"] == "ADMITTED"
    view = out["view"]
    assert view["accepted"] is True and view["consumer_kind"] == "ROOT_RESOLUTION" and view["task_id"] == out["goal_task"]
    assert out["borrowed"].detail["reason"] == "EVALUATION_NOT_DISPATCHED"


def test_a_root_resolution_of_another_task_is_refused(tmp_path) -> None:
    def link_elsewhere(ev: Evaluation) -> None:
        ev.lifecycle.record_evaluation_dispatch(ev.evaluation, mission_id=ev.mission_id, task_id="task-elsewhere",
                                                caller=trusted_caller(), command_id="d1")

    out = asyncio.run(_root_evaluation(tmp_path, link_elsewhere))
    assert out["refused"].detail["reason"] == "TASK_MISMATCH" and out["state"] == "TRIAL"


def test_a_root_resolution_issued_before_the_dispatch_was_recorded_cannot_admit(tmp_path) -> None:
    out = asyncio.run(_root_evaluation(tmp_path, None))
    assert out["refused"].detail["reason"] == "ACCEPTANCE_BEFORE_DISPATCH" and out["state"] == "TRIAL"
