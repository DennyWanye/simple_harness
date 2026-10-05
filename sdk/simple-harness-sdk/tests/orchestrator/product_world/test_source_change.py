# SPDX-License-Identifier: Apache-2.0
"""任务跑到一半资料换了版本（2026-10-05 真机 ``mission-d507…`` 与随后的裁决）。

真机上：三步都通过或在跑时资料换版，任务没有重做，旧版内容照样判完成——系统只把"结果里引用了这份
资料"的步骤算受影响（普通任务的执行者不写引用），审阅员手里也没有资料正文。现在：凡是派发时挂着旧版
的已通过步骤，连同新旧差异，一条请求交给规划器判；它判"不受影响"也是一种了结；终审等这件事问完再开；
审阅员的审查包写明执行者用的版本与现行版本，现行版本的正文在它的证据里。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.orchestrator.planning_repair_requests import (
    ADDRESSED,
    REQUESTED,
    SOURCE_CHANGE_ASSESSED,
)
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    REVIEWER,
    LayeredScriptedProvider,
    decision,
    planner_reply,
    review_input,
    reviewer_reply,
)

PATH = "sources/spec.md"
OLD, NEW = "# 规格\n价格：每月 18 元\n", "# 规格\n价格：每月 25 元\n"


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _events(world: Any, mission_id: str, event_type: str) -> list[Any]:
    return [e for e in world.store.list_events(mission_id) if e.type == event_type]


def _source_requests(world: Any, mission_id: str) -> list[Any]:
    return [e for e in _events(world, mission_id, REQUESTED) if str(e.payload["source_key"]).startswith("source:")]


def _supersede(world: Any, mission_id: str, expected: str, content: str = NEW, key: str = "1") -> dict[str, Any]:
    proposal = world.control.supersede_source({
        "mission_id": mission_id, "path": PATH, "content": content, "kind": "markdown",
        "idempotency_key": "sup-spec-" + key, "expected_version_hash": expected})
    world.control.decide(proposal["request_id"], "approve", nonce="approve-sup-" + key)
    return proposal


def _source_evidence(review: dict[str, Any]) -> list[str]:
    return [row["content"] for row in review["evidence"] if row["ref"]["kind"] == "source"]


def test_a_source_replaced_under_an_accepted_step_is_put_to_the_planner_and_the_final_review_waits(tmp_path):
    """一步拿着第 1 版资料在跑时资料换成第 2 版，这一步随后通过验收。

    - 跑着的时候不问（等它跑完）；跑完后**一条**请求交给规划器：这一步挂的是旧版、没有引用过、新旧差异；
      不记"评估过、没影响"。
    - 规划器被问到的那一刻任务没有判完成、终审还没开。
    - 规划器答"不改，这一步不受影响"：请求了结，之后才终审、任务完成。
    - 这一步的审阅员看到两个版本号不同，证据里是现行版正文；终审的证据里也有现行版正文。

    **改坏检验**：只把引用过资料的结果算受影响（SRC-01）→ 不发请求 → 变红；终审不等这件事问完
    （SRC-02）→ 规划器被问到时终审已开 → 变红；"不改"不能了结这类请求（SRC-03）→ 任务到不了完成 → 变红。"""
    seen: dict[str, Any] = {"requests": [], "reviews": [], "answered": False}

    def planner(request: Any):  # type: ignore[no-untyped-def]
        package = package_of(request)
        sourced = [entry for entry in package.get("repair_requests") or ()
                   if str(entry.get("source_key")).startswith("source:")]
        if not sourced:
            return planner_reply(request)
        seen["requests"].append(sourced[0])
        if not seen["answered"]:
            return None  # 第一次被问到：测试先看一眼当时的状态
        step = sourced[0]["request"]["context"]["steps_on_old_version"][0]["task_id"]
        subject = next(s["subject_key"] for s in package["planning_subjects"] if s["task_id"] == step)
        return decision(subject, "NO_CHANGE", {"reason": "这一步写的是说明的结构，没有用到价格。"},
                        "资料只改了价格，这一步的产出不涉及价格。")

    def reviewer(request: Any):  # type: ignore[no-untyped-def]
        seen["reviews"].append(review_input(request))
        return reviewer_reply(request)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "按规格写一份说明", "success_criteria": ["file:notes/a.md"],
                                       "idempotency_key": "source-change-1"})["mission_id"]
            first = world.control.register_source({"mission_id": mission_id, "path": PATH, "content": OLD,
                                                   "kind": "markdown", "idempotency_key": "reg-spec-1"})
            for _ in range(20):
                await world.drain(timeout=5)
                if provider.entered.is_set():
                    break
            assert provider.entered.is_set()
            proposal = _supersede(world, mission_id, first["version_hash"])
            await world.drain(timeout=5)
            # 拿着旧版的尝试还在跑：不问、也不记"没影响"
            assert not _source_requests(world, mission_id) and not _events(world, mission_id, SOURCE_CHANGE_ASSESSED)

            provider.release.set()
            for _ in range(20):
                try:
                    await world.drain(timeout=10)
                except AssertionError:
                    pass
                if seen["requests"]:  # 规划器第一次被问到资料变更（脚本这一次不作答）
                    break
            assert len(_source_requests(world, mission_id)) == 1
            [asked] = _source_requests(world, mission_id)
            context = asked.payload["request"]["context"]
            [step] = context["steps_on_old_version"]
            assert (step["status"], step["cited"]) == ("ACCEPTED", False)
            assert asked.payload["request"]["trigger_refs"] == [step["result_id"]]
            assert (context["reason"], context["path"]) == ("source_superseded", PATH)
            assert (context["old_version"], context["new_version"]) == (first["version_hash"], proposal["version_hash"])
            assert "-价格：每月 18 元" in context["diff_excerpt"] and "+价格：每月 25 元" in context["diff_excerpt"]
            assert seen["requests"][0]["request"]["context"] == context  # 规划器看到的就是这些
            assert not _events(world, mission_id, SOURCE_CHANGE_ASSESSED)
            # 这时任务没有判完成，终审也没开：只有那一步的内容审查发生过
            assert str(world.store.get_mission(mission_id).status.value) == "ACTIVE"
            assert not _events(world, mission_id, "GoalResolutionCommitted")
            leaf_reviews = [r for r in seen["reviews"] if r and r["package"].get("source_versions")]
            assert [r["package"]["purpose"] for r in seen["reviews"] if r] == ["METHOD_PLAN", "TASK_CONTENT"]
            [leaf] = leaf_reviews
            assert leaf["package"]["source_versions"] == [{
                "path": PATH, "used_version": first["version_hash"], "current_version": proposal["version_hash"]}]
            assert _source_evidence(leaf) == [NEW]

            seen["answered"] = True
            status = ""
            for _ in range(30):
                await world.drain(timeout=20)
                status = str(world.store.get_mission(mission_id).status.value)
                if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
            assert status == "COMPLETED"
            [addressed] = [e for e in _events(world, mission_id, ADDRESSED)
                           if asked.payload["request_id"] in e.payload["repair_request_ids"]]
            assert (addressed.payload["decision_type"], addressed.payload["status"]) == ("NO_CHANGE", "NO_STATE_CHANGE")
            [resolved] = _events(world, mission_id, "GoalResolutionCommitted")
            assert addressed.seq < resolved.seq
            assert len(_source_requests(world, mission_id)) == 1
            final = [r for r in seen["reviews"] if r and r["package"]["purpose"] == "MISSION_FINAL"]
            assert final and all(_source_evidence(r) == [NEW] for r in final)

    asyncio.run(case())


class _HoldLeafReview(LayeredScriptedProvider):
    """这一步的内容审查停在半路（模拟审阅员还在读），直到 ``review_release`` 置位。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.review_entered, self.review_release = asyncio.Event(), asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        review = review_input(request)
        if review and review["package"]["purpose"] == "TASK_CONTENT" and not self.review_release.is_set():
            self.review_entered.set()
            await self.review_release.wait()
        return await super().invoke(request, cancel=cancel)


def test_a_review_in_flight_when_its_source_is_replaced_is_voided_with_the_reason(tmp_path):
    """审阅员手里有现行版资料（执行者用的是更早一版），审阅进行中资料又换了一版：这次审阅看的已不是
    现行资料，作废；失败说明里写的是真正的原因，导入不空转。

    **改坏检验**（SRC-04）：不认"审阅证据里的资料已换版" → 导入空转重试到放弃，说明变成
    "需要人工处理" → 变红。"""

    async def case():
        provider = _HoldLeafReview()
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "按规格写一份说明", "success_criteria": ["file:notes/a.md"],
                                       "idempotency_key": "source-change-2"})["mission_id"]
            first = world.control.register_source({"mission_id": mission_id, "path": PATH, "content": OLD,
                                                   "kind": "markdown", "idempotency_key": "reg-spec-1"})
            for _ in range(30):
                await world.drain(timeout=5)
                if provider.entered.is_set():
                    break
            assert provider.entered.is_set()
            second = _supersede(world, mission_id, first["version_hash"])  # 执行期间换成第 2 版
            provider.release.set()
            for _ in range(30):
                await world.drain(timeout=5)
                if provider.review_entered.is_set():
                    break
            assert provider.review_entered.is_set()  # 审阅员拿着第 2 版在审
            _supersede(world, mission_id, second["version_hash"], "# 规格\n价格：每月 30 元\n", "2")
            provider.review_release.set()
            for _ in range(30):
                try:
                    await world.drain(timeout=10)
                except AssertionError:
                    pass
                if _events(world, mission_id, "VerificationFailed") or _events(world, mission_id, "AcceptanceCommitted"):
                    break
            [failed] = _events(world, mission_id, "VerificationFailed")
            [layer] = failed.payload["failures"]
            assert layer["layer"] == "critic_review" and "资料换了版本或被撤销" in layer["summary"]
            assert not _events(world, mission_id, "AcceptanceCommitted")
            [late] = _events(world, mission_id, "AssuranceReviewLateTurn")
            assert late.payload["reason"] == "REVIEW_SOURCE_REPLACED"
            # 作废是一次了结，不是空转：导入这件事没有重试过
            assert [tuple(row) for row in world.store.connection.execute(
                "SELECT state, rechecks FROM assurance_pending_work WHERE mission_id=? AND consumer='REVIEW' "
                "AND work_key LIKE 'review-import:assurance-content:%'", (mission_id,))] == [("DONE", 0)]

    asyncio.run(case())


def test_a_source_replaced_after_the_final_review_holds_the_closeout_until_the_planner_answers(tmp_path, monkeypatch):
    """终审已通过、任务还没正式完成时资料换了版本（Assurance 原计划 §7.2"最终事务重读"）。

    终审等资料变更问完才开；终审之后才换的，收尾同样等：收尾记"资料变更还没问完"，规划器被问到时
    任务没有完成；它答"不改"之后才完成。

    **改坏检验**：收尾不看资料变更（SRC-05）→ 规划器还没被问到任务就完成了 → 变红。"""
    from agent_orchestrator.assurance.codec import decode
    from agent_orchestrator.orchestrator.resolution_commits import ResolutionCommitsMixin as ResolutionCommits

    seen: dict[str, Any] = {"asked_while": None, "world": None, "first": None, "mission": None, "replaced": False}

    def planner(request: Any):  # type: ignore[no-untyped-def]
        package = package_of(request)
        sourced = [entry for entry in package.get("repair_requests") or ()
                   if str(entry.get("source_key")).startswith("source:")]
        if not sourced:
            return planner_reply(request)
        world = seen["world"]
        closeout = world.store.connection.execute(
            "SELECT state, check_body_json FROM assurance_closeouts WHERE mission_id=?", (seen["mission"],)).fetchone()
        seen["asked_while"] = (str(world.store.get_mission(seen["mission"]).status.value),
                               None if closeout is None else closeout["state"],
                               [] if closeout is None else decode(closeout["check_body_json"])["reasons"])
        step = sourced[0]["request"]["context"]["steps_on_old_version"][0]["task_id"]
        subject = next(s["subject_key"] for s in package["planning_subjects"] if s["task_id"] == step)
        return decision(subject, "NO_CHANGE", {"reason": "这一步没有用到价格。"}, "资料只改了价格。")

    original = ResolutionCommits.commit_goal_resolution

    def commit_then_replace(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        out = original(self, *args, **kwargs)
        world = seen["world"]
        if not seen["replaced"] and _events(world, seen["mission"], "GoalResolutionCommitted"):
            seen["replaced"] = True
            _supersede(world, seen["mission"], seen["first"]["version_hash"])
        return out

    monkeypatch.setattr(ResolutionCommits, "commit_goal_resolution", commit_then_replace)

    async def case():
        provider = LayeredScriptedProvider(planner=planner)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "按规格写一份说明", "success_criteria": ["file:notes/a.md"],
                                       "idempotency_key": "source-change-late"})["mission_id"]
            seen.update(world=world, mission=mission_id)
            seen["first"] = world.control.register_source({
                "mission_id": mission_id, "path": PATH, "content": OLD, "kind": "markdown",
                "idempotency_key": "reg-spec-late"})
            status = ""
            for _ in range(40):
                await world.drain(timeout=20)
                status = str(world.store.get_mission(mission_id).status.value)
                if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                    break
            assert seen["replaced"]
            assert seen["asked_while"] is not None, "任务完成前规划器没有被问到资料变更"
            asked_status, closeout_state, reasons = seen["asked_while"]
            assert asked_status == "ACTIVE"
            assert closeout_state == "NOT_READY" and "SOURCE_CHANGE_OPEN" in reasons
            assert status == "COMPLETED"
            [asked] = _source_requests(world, mission_id)
            [addressed] = [e for e in _events(world, mission_id, ADDRESSED)
                           if asked.payload["request_id"] in e.payload["repair_request_ids"]]
            [completed] = _events(world, mission_id, "MissionCompleted")
            [resolved] = _events(world, mission_id, "GoalResolutionCommitted")
            assert resolved.seq < asked.seq < addressed.seq < completed.seq

    asyncio.run(case())
