# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""根 ``MISSION_FINAL`` 终审：切包、重切、上限与修复请求，在产品同形世界里（HTN 补齐阶段 A′）。

终审要守的两件事没有变：

* **系统从不替审阅员写结论**（AER I05）：切包不是下结论；只有审阅员说通过，根结论才形成；
  审阅员打回，任务不完成，只把一条修复请求交给规划器，不再拿同一个包问第二遍；
* **包是对当时世界的承诺，世界会动**：包切下之后资料变了、终审调用被打断用完、管理范围被
  重新打开，旧包都要带记录作废、重切一个新包；同一版要求重切有上限，用完就停下并记一次。

每条用例都是产品那一份部署组装上的真实主循环（建任务即绑定执行图、保证通道、原生执行池），
种子见 :mod:`root_review_world`：两条要求、两步并行做法。替身只有模型回复、终审调用挂住或以
服务商协议错误结束、以及在终审挂着的窗口里外界做的事（用户经门面登记一份资料；范围纪元由它
唯一的写入函数 ``bump_epoch`` 抬一次——分诊裁决①c，桌面观察与要求修订是它排定的写入方）。

**原 75 条的去向**（分诊表第三节第 7 小节；分诊裁决⑧-3）：

* 旧根审阅员一整套（``request()`` / ``record_review`` / ``record_unreadable`` / ``_child_review`` /
  旧模板 / ``_ask_root_reviewer`` 非保证段 / ``_collect_root_review``）26 条随删（F）。
* 第 10 节解析器负向 5 条随 ``parse_critic_verdict`` 删除（裁决⑧-3）。
* 切包前缺包、绑当时要求版本、贡献集、终审许可、锚点形成结论、逐准则复述、义务终结 7 条
  删除，由【整圈】``product_world/test_full_circle.py`` 覆盖；其中可观察的部分（要求版本、贡献集、
  逐准则复述、结论取自终审记录）一并写进下面的 RA/RB 用例。
* 自证 3 条、生产者自审不能通过、切包后叶子不能再验收、READY 说明文字 3 条删除（前者由
  ``test_acceptance_rules.py`` 钉住，后两者主循环到不了）。
* 根准则 3 条（原"改 E"）删除：``root_criteria`` 已随旧根审阅员删掉，产品终审包的准则就是用户
  逐条写的成功条件（RB 断言了编号与原文）。
* 包规则 2/3（手插第二个包与切包事件）属裁决①b2"手写产品造不出的合法状态"，删除；自然路径上
  "重切后旧包作废、新包是活的"由 RC 断言。"要求变了"这条重切通道产品上没有写入方（用户改要求
  在阶段 E），删除；"没人判的准则记 NOT_RUN / 判过的记 SUCCEEDED"两条钉的是旧
  ``record_review``，保证通道把逐条判定放在评估表里，终审记录本身每条都是
  ``ASSURANCE_SEMANTIC_GRADE_IN_BOUND_MANIFEST``，删除（偏离，均无目录覆盖）。
* "切包时发许可 / 重切在新纪元取新许可"：保证通道不经验收许可见证，根结论由
  ``AssuranceUseCertified`` 准入；改为断言新包记下的是新纪元（RC）。
* 修复请求了结范围两条并成 RE；"系统在叶子重新验收后自己了结"那半条产品上要带引用的资料
  换版本才触发，本轮不写（偏离，建议并进 ``taskgraph_exec/test_source_change_replan.py``）。
* 保留 E 2 条：默认切包上限、只有被打断的错误码算打断。
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from h1i_seed import root_duty, root_task, run_until  # noqa: E402
from root_review_world import (  # noqa: E402
    CRITERIA,
    GOAL,
    FinalReviewProvider,
    events,
    open_repairs,
    run_for,
)

from agent_orchestrator.contracts.evidence_state import Validity  # noqa: E402
from agent_orchestrator.contracts.resolution import (  # noqa: E402
    CriterionVerdict,
    ReviewAccount,
    ReviewPurpose,
    ReviewVerdict,
)
from agent_orchestrator.contracts.semantic_base import Provenance, TypedRef, TypedRefKind  # noqa: E402
from agent_orchestrator.orchestrator.planning_repair_requests import pending_requests  # noqa: E402
from agent_orchestrator.orchestrator.root_review import (  # noqa: E402
    DEFAULT_MAX_CUTS_PER_REVISION,
    ROOT_REVIEW_CUT,
    ROOT_REVIEW_CUT_BUDGET_SPENT,
    ROOT_REVIEW_POLICY,
    ROOT_REVIEW_SUPERSEDED,
)
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of, role_of  # noqa: E402
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import retry_same_method  # noqa: E402

DONE = {"COMPLETED", "FAILED", "CANCELLED"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _create(world: Any, key: str) -> str:
    return world.create({"goal": GOAL, "idempotency_key": key, "success_criteria": list(CRITERIA)})["mission_id"]


def _status(world: Any, mission_id: str) -> str:
    return str(world.store.get_mission(mission_id).status.value)


def _coordinator(world: Any, mission_id: str) -> Any:
    """The deployment's own root-review coordinator for this Mission (reads only here)."""
    mission = world.loop.store.get_mission(mission_id)
    return world.loop._root_review(mission, world.loop._new_mode(mission))


def _current_acceptances(semantics: HtnStore, mission_id: str) -> set[str]:
    return {str(item.acceptance_id) for item in semantics.list_acceptances(mission_id)
            if item.validity is Validity.CURRENT}


def _task_of_output(provider: FinalReviewProvider, path: str) -> str:
    return str(provider.worker_packages[path]["task_contract"]["task_id"])


# ======================================================================================
# RA + RB：终审时序、只有审阅员的通过才形成根结论；完成后包的形状
# ======================================================================================


@pytest.mark.parametrize("verdict", ["ACCEPT", "REJECTED"])
def test_the_final_review_is_cut_over_every_contribution_and_only_its_pass_resolves_the_root(
    tmp_path, verdict: str
) -> None:
    async def case() -> None:
        provider = FinalReviewProvider(final_verdict=verdict)
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = _create(world, f"final-review-{verdict.lower()}")
            store, semantics = world.store, HtnStore(world.store)
            try:
                if verdict == "ACCEPT":
                    await run_until(world, lambda: _status(world, mission_id) in DONE, timeout=40)
                else:
                    await run_until(world, provider.repair_asked.is_set, timeout=40)
                    await run_for(world, 1.0)  # the reviewer that answered is never asked again
            finally:
                provider.close()

            [package] = semantics.list_review_packages(mission_id, purpose=ReviewPurpose.MISSION_FINAL)
            [cut] = events(store, mission_id, ROOT_REVIEW_CUT)
            accepted = events(store, mission_id, "AcceptanceCommitted")
            record = semantics.official_review_record(str(package.package_id))
            resolved = events(store, mission_id, "GoalResolutionCommitted")
            # RA：两步都验收之后才切包；终审只问了一次；结论是审阅员说的那个。
            assert len(accepted) == 2 and max(item.seq for item in accepted) < cut.seq
            assert provider.final_calls == 1
            assert record is not None and record.verdict is ReviewVerdict(verdict)
            [imported] = [item for item in events(store, mission_id, "AssuranceReviewImported")
                          if item.payload.get("record_id") == str(record.record_id)]
            assert cut.seq < imported.seq

            if verdict == "REJECTED":
                assert _status(world, mission_id) not in DONE
                assert resolved == [] and semantics.adopted_goal_resolution(mission_id, root_duty(mission_id)) is None
                # 打回只交给规划器一条修复请求（不替规划器改做法、不重问审阅员）。
                requested = [item for item in events(store, mission_id, "PlanningRepairRequested")
                             if str(item.payload["source_key"]).startswith("root-review:")]
                assert [item.payload["source_key"] for item in requested] == ["root-review:" + str(record.record_id)]
                assert [row["request_id"] for row in pending_requests(store, mission_id)] == [
                    requested[0].payload["request_id"]]
                [asked] = provider.repair_packages
                assert [entry["request_id"] for entry in open_repairs(asked)] == [requested[0].payload["request_id"]]
                assert len(events(store, mission_id, ROOT_REVIEW_CUT)) == 1
                return

            # RA：切包 → 终审记录导入 → 根结论 → 任务完成；根结论取自这份终审记录、逐条复述。
            [completed] = events(store, mission_id, "MissionCompleted")
            assert imported.seq < resolved[0].seq < completed.seq
            resolution = semantics.adopted_goal_resolution(mission_id, root_duty(mission_id))
            assert resolution is not None and resolution.verdict is ReviewVerdict.ACCEPT
            assert str(resolution.review_receipt_id) == str(record.record_id)
            assert int(resolution.requirements_version) == int(package.binding.requirements_revision)
            assert {str(item.criterion_id): item.verdict for item in resolution.criteria} == {
                "c-user-1": CriterionVerdict.PASS, "c-user-2": CriterionVerdict.PASS}

            # RB：包绑在根上，准则是用户逐条写的成功条件，要求版本是当时在用的那一版。
            assert package.purpose is ReviewPurpose.MISSION_FINAL
            assert str(package.binding.subject_ref.id) == root_task(mission_id)
            assert str(package.binding.obligation_id) == root_duty(mission_id)
            assert str(package.binding.policy_ref.id) == ROOT_REVIEW_POLICY
            assert [(str(item.criterion_id), str(item.statement)) for item in package.criteria] == [
                ("c-user-1", CRITERIA[0]), ("c-user-2", CRITERIA[1])]
            latest = semantics.latest_requirements_revision(mission_id)
            assert latest is not None
            assert int(package.binding.requirements_revision) == int(latest.revision)
            assert package.requirements_content_hash == latest.content_hash()
            # 贡献集 = 当前有效的验收；作者 = 真正干活的执行者，终审员不在其中。
            current = _current_acceptances(semantics, mission_id)
            assert {str(item.id) for item in package.child_acceptance_refs} == current
            assert {str(item.id) for item in package.candidate_refs} == current
            assert all(item.kind is TypedRefKind.ACCEPTANCE for item in package.candidate_refs)
            workers = {str(attempt.agent_id) for task in store.list_tasks(mission_id)
                       for attempt in store.list_attempts(task.id) if attempt.agent_id}
            assert set(package.producer_agent_ids) == workers and len(workers) == 2
            assert str(record.reviewer_agent_id) not in workers
            # 费用记在任务账上，不记在哪一步的账上。
            row = store.connection.execute("SELECT review_account FROM review_packages WHERE package_id = ?",
                                           (str(package.package_id),)).fetchone()
            assert row[0] == str(ReviewAccount.MISSION)
            # 切包事件写明它是对着什么切的；只切了一次（包是活的，没人重切）。
            assert cut.payload["package_id"] == str(package.package_id)
            assert cut.payload["review_account"] == str(ReviewAccount.MISSION)
            assert cut.payload["requirements_revision"] == int(package.binding.requirements_revision)
            assert sorted(cut.payload["contributions"]) == sorted(current)
            assert cut.payload["superseded"] is None and cut.payload["recut_reasons"] == []
            # 贡献集变动是一条单独的重切通道（只读：对着改过的包问"过期了吗"）。
            coordinator = _coordinator(world, mission_id)
            assert coordinator.stale_reasons(mission_id, package) == ()
            later = TypedRef(kind=TypedRefKind.ACCEPTANCE, id="acc-from-later", revision=0,
                             content_hash="a" * 64, produced_by=Provenance.TOOL)
            moved = dataclasses.replace(package, child_acceptance_refs=(*package.child_acceptance_refs, later))
            assert coordinator.stale_reasons(mission_id, moved) == ("CONTRIBUTIONS_MOVED",)
            # 叶子的审阅包只带这一步自己承接的准则，不带根的全部准则。
            for path, criterion in (("facts.md", "c-user-1"), ("NOTES.md", "c-user-2")):
                task_id = _task_of_output(provider, path)
                [acceptance] = [item for item in semantics.list_acceptances(mission_id) if str(item.task_id) == task_id]
                leaf_record = semantics.get_review_record(str(acceptance.review_record_id)).record
                leaf_package = semantics.get_review_package(str(leaf_record.package_id))
                assert {str(item.criterion_id) for item in leaf_package.criteria} == {criterion}
                assert {str(item.criterion_id) for item in leaf_record.criteria} == {criterion}

    asyncio.run(case())


# ======================================================================================
# RC：世界在切包之后动了 → 旧包带记录作废、重切；新包绑当时在用的要求与贡献
# ======================================================================================


CHANNELS = {
    # 终审挂着时，用户经门面登记了一份资料（终审要看现行资料版本集）。
    "SOURCES_MOVED": {"hold_final_first": True},
    # 终审两次调用都以服务商协议错误结束：审阅员没说上话，这不是结论（2026-09-29 真机第七局一类）。
    "REVIEW_INTERRUPTED": {"interrupt_final": 2},
    # 终审挂着时，管理范围被重新打开（纪元抬一次）：旧纪元里取的一切作废。
    "SCOPE_EPOCH_MOVED": {"hold_final_first": True},
}


@pytest.mark.parametrize("reason", list(CHANNELS))
def test_a_world_that_moves_under_the_final_review_supersedes_the_package_and_recuts(tmp_path, reason: str) -> None:
    async def case() -> None:
        provider = FinalReviewProvider(**CHANNELS[reason])
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = _create(world, f"recut-{reason.lower()}")
            store, semantics = world.store, HtnStore(world.store)
            try:
                if provider.hold_final_first:
                    await run_until(world, provider.final_held.is_set, timeout=40)
                    if reason == "SOURCES_MOVED":
                        world.control.register_source({"mission_id": mission_id, "path": "sources/late.md",
                                                       "content": "后来补的资料", "kind": "markdown",
                                                       "idempotency_key": "late-source-1"})
                    else:
                        semantics.bump_epoch(mission_id, "mission", bumped_by="test-scope-reopened")
                    provider.final_release.set()
                if reason == "SCOPE_EPOCH_MOVED":
                    # 新纪元里旧验收的效力要重新确立，任务不在这里完成；看到重切即可。
                    await run_until(world, lambda: len(events(store, mission_id, ROOT_REVIEW_CUT)) >= 2, timeout=40)
                else:
                    await run_until(world, lambda: _status(world, mission_id) in DONE, timeout=40)
            finally:
                provider.close()

            first, second = events(store, mission_id, ROOT_REVIEW_CUT)
            [superseded] = events(store, mission_id, ROOT_REVIEW_SUPERSEDED)
            first_id, second_id = first.payload["package_id"], second.payload["package_id"]
            assert first_id != second_id
            assert superseded.payload["package_id"] == first_id and superseded.payload["reasons"] == [reason]
            assert first.seq < superseded.seq < second.seq
            assert second.payload["superseded"] == first_id and second.payload["recut_reasons"] == [reason]
            # 重切绑的是当时在用的要求版本（没人改要求，与旧包同一版）和当前有效的贡献集。
            assert second.payload["requirements_revision"] == first.payload["requirements_revision"]
            assert sorted(second.payload["contributions"]) == sorted(_current_acceptances(semantics, mission_id))
            # 旧包仍存着（锚点从不改写），只是不再是这个任务据以下结论的那个。
            coordinator = _coordinator(world, mission_id)
            assert semantics.get_review_package(first_id) is not None
            assert str(coordinator.live_package(mission_id).package_id) == second_id
            assert coordinator.superseded_package_ids(mission_id) == frozenset({first_id})
            if reason == "SCOPE_EPOCH_MOVED":
                assert (first.payload["scope_epoch"], second.payload["scope_epoch"]) == (
                    0, semantics.epoch(mission_id, "mission"))
                assert second.payload["scope_epoch"] > 0
                return
            assert _status(world, mission_id) == "COMPLETED"
            # 根结论从新包的终审记录形成。
            resolution = semantics.adopted_goal_resolution(mission_id, root_duty(mission_id))
            record = semantics.official_review_record(second_id)
            assert resolution is not None and record is not None
            assert str(resolution.review_receipt_id) == str(record.record_id)
            if reason == "REVIEW_INTERRUPTED":
                assert provider.final_calls == 3  # two interrupted calls on the old package, one on the new
                assert semantics.official_review_record(first_id) is None
                interrupted = store.connection.execute(
                    "SELECT COUNT(*) FROM commit_receipts WHERE kind = 'AssuranceReviewTurnInterrupted'").fetchone()[0]
                assert interrupted == 2

    asyncio.run(case())


# ======================================================================================
# RD：同一版要求重切有上限；用完停下，只记一次，不以通过的根结论收尾
# ======================================================================================


def test_a_spent_recut_budget_stops_the_final_review_and_is_recorded_once(tmp_path) -> None:
    async def case() -> None:
        provider = FinalReviewProvider(hold_final_first=True)
        async with product_world(tmp_path / "root", provider, max_root_review_cuts=1) as world:
            mission_id = _create(world, "recut-budget")
            store, semantics = world.store, HtnStore(world.store)
            try:
                await run_until(world, provider.final_held.is_set, timeout=40)
                semantics.bump_epoch(mission_id, "mission", bumped_by="test-scope-reopened")
                provider.final_release.set()
                await run_until(world, lambda: bool(events(store, mission_id, ROOT_REVIEW_CUT_BUDGET_SPENT)),
                                timeout=40)
                await run_for(world, 1.0)  # later cycles do not record it again
            finally:
                provider.close()

            [cut] = events(store, mission_id, ROOT_REVIEW_CUT)
            [spent] = events(store, mission_id, ROOT_REVIEW_CUT_BUDGET_SPENT)
            assert spent.payload["bound"] == 1 and spent.payload["cuts_used"] == 1
            assert spent.payload["stale_reasons"] == ["SCOPE_EPOCH_MOVED"]
            assert spent.payload["package_id"] == cut.payload["package_id"]
            assert spent.payload["requirements_revision"] == cut.payload["requirements_revision"]
            assert events(store, mission_id, ROOT_REVIEW_SUPERSEDED) == []
            assert provider.final_calls == 1
            # 2026-10-03 阶段 B 收尾裁决（实施记录"根终审切包用完如实写"）：切包用完不再沉默——停滞路径
            # 把事实交给规划器，再按停止规则收口。所以这里可以有一条根结论，但绝不能是通过；任务不得完成。
            resolution = semantics.adopted_goal_resolution(mission_id, root_duty(mission_id))
            assert resolution is None or resolution.verdict is not ReviewVerdict.ACCEPT
            assert _status(world, mission_id) != "COMPLETED"

    asyncio.run(case())


# ======================================================================================
# RE：一条修复决定只了结它所处理那一步的修复请求
# ======================================================================================


def test_a_repair_decision_addresses_only_the_request_about_its_own_step(tmp_path) -> None:
    """2026-09-30 真机（结构修复第 2 局）：规划器只重做了一步，系统把两条请求都记成已处理，
    另一步从没重做。两步的内容都被打回、各开一条修复请求；规划器第一轮只对其中一步答
    "同一做法再试一次"——了结的只有那一步的请求，另一条仍挂着。"""

    class OrderedRepairs(FinalReviewProvider):
        """修复轮的规划器调用先等两步的打回都记下（两条审阅调用谁先回来是并发的，固定这个次序
        用例才确定）；答过第一轮之后，后面的规划器调用一律扣住，另一条请求就一直挂着。"""

        def __init__(self) -> None:
            super().__init__(reject_content=("facts.md", "NOTES.md"), repair=retry_same_method)
            self.both_recorded = asyncio.Event()
            self.answered = 0

        async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
            if role_of(request) == "planner" and open_repairs(package_of(request)):
                if self.answered:
                    await self._forever.wait()
                await self.both_recorded.wait()
                self.answered += 1
            return await super().invoke(request, cancel=cancel)

    async def case() -> None:
        provider = OrderedRepairs()
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = _create(world, "repair-scope")
            store = world.store

            def recorded() -> bool:
                rejections = [item for item in events(store, mission_id, "PlanningRepairRequested")
                              if str(item.payload["source_key"]).startswith("event:")]
                if len(rejections) >= 2:
                    provider.both_recorded.set()
                return bool(events(store, mission_id, "PlanningRepairAddressed"))

            try:
                await run_until(world, recorded, timeout=60)
            finally:
                provider.close()

            [addressed] = events(store, mission_id, "PlanningRepairAddressed")
            requested = [item for item in events(store, mission_id, "PlanningRepairRequested")
                         if item.seq < addressed.seq and str(item.payload["source_key"]).startswith("event:")]
            assert len(requested) == 2, "both steps' rejections were on record when the Planner answered"
            [decided] = [item for item in events(store, mission_id, "PlanningDecisionEvaluated")
                         if item.payload.get("decision_id") == addressed.payload["decision_id"]]
            task_id = str(decided.payload["detail"]["failed_attempt_id"]).rpartition(":attempt-")[0]
            mine = [item.payload["request_id"] for item in requested if task_id in item.payload["trigger_scope"]]
            others = [item.payload["request_id"] for item in requested if task_id not in item.payload["trigger_scope"]]
            assert len(mine) == 1 and len(others) == 1
            assert addressed.payload["repair_request_ids"] == mine
            assert others[0] in {row["request_id"] for row in pending_requests(store, mission_id)}

    asyncio.run(case())


# ======================================================================================
# E：纯函数
# ======================================================================================


def test_the_default_bound_is_the_configured_one() -> None:
    from agent_orchestrator.runtime.assembly import OrchestratorConfig

    assert DEFAULT_MAX_CUTS_PER_REVISION == 3
    assert (
        OrchestratorConfig(evidence_root=Path("/tmp/unused")).max_root_review_cuts
        == DEFAULT_MAX_CUTS_PER_REVISION
    )
    with pytest.raises(ValueError, match="max_root_review_cuts"):
        OrchestratorConfig(evidence_root=Path("/tmp/unused"), max_root_review_cuts=0)


def test_only_an_interrupted_turn_code_counts_as_interrupted() -> None:
    from agent_orchestrator.orchestrator.failure_classes import review_turn_interrupted

    assert review_turn_interrupted({"error_code": "base_agent_driver_exception"})
    assert review_turn_interrupted({"error_code": "react_wall_clock_exceeded"})
    # 2026-09-29 第十五局：审阅调用以服务商协议错误结束（工具调用解析不了）——执行尝试那边
    # 这类算"服务出错"不扣次数，审阅这边同样不是审阅员的错。
    assert review_turn_interrupted({"error_code": "provider_protocol_error", "source_kind": "tool_parse"})
    assert review_turn_interrupted({"error_code": "provider_server_error"})
    assert not review_turn_interrupted({"error_code": "react_max_turns_exceeded"})
    assert not review_turn_interrupted({"error_code": "invalid_tool_arguments"})
    assert not review_turn_interrupted(None)
