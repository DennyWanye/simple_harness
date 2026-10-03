# SPDX-License-Identifier: Apache-2.0
"""代表用例 3（带发布的任务）的变体：产品同形世界（HTN 补齐阶段 A′，对外操作族）。

主线见 ``tests/orchestrator/product_world/test_operation.py``：建任务 → 人确认完成映射（发布是必须
完成的效果）→ 内容步骤写出文件 → 系统按已批准效果准备申请单、审阅通过 → 人批准 → 真实的
``FilePublishConnector`` 发布 → 读回核对、结果审阅 → 任务完成。这里守它的各个岔路口：

1. 内容验收只是"准备好了"：等人批准期间，内容可读、顺序与终结仍关着；根不完成、没有根结论、
   不重派、不卡死；批准后效果的证明链（效果验收 + 交付回执）才把根补完整。
2. 服务端已发布但回执丢了、核对时服务又连不上：动作停在"结果未知"，全任务的操作读侧把它当作
   未决，计划变更的总闸关着；整个过程不重发；服务恢复后核对成成功，任务完成，始终只有一次发布。
3. 人在审批卡上拒绝：什么都不发布，任务不会完成。部署的"每个任务最多交接几个动作"为 0 时：
   交接前就拒绝，连接器没被碰，任务以动作失败结束。
3b. 请求根本没到服务端（连接断了）：发布台账里没有这条意图，登记的对账适配器据此证明"没开始"，
   原地重交一次，任务完成、只发布一次（阶段 B 裁决第 3 类）。发布后文件被用户删掉、回执又丢了：
   谁也判不了，等人裁决；人裁"没生效"即是证明，闸门打开，系统按原内容出新卡（要有依据，记
   HumanOverride，只裁这一次）。
4. 审阅员不认可系统准备的申请单：停给人判断（不重交）；判断不了：替代重交两次后停下。
5. 计划里没有任何步骤写出要发布的文件：系统把事实交给规划器（修复请求点名缺的文件），不建申请单。
6. 最终审查看得到根自己已验收的发布效果和读回核对。
7. 两个必须完成的发布各走各的证明链：先批准的那个发布、验收了，另一个仍等批准，根不完成。
8. 结果审阅进行中，磁盘上那份结果审阅绑定被改坏（字节损坏，裁决①b1）：效果验收被推迟，不写交付
   回执，不重发，任务不完成。
9. 效果验收写到一半数据库写失败（触发器注入，外界的写入故障）：效果验收、交付回执整体回滚，不重发；
   故障排除、进程重启后效果验收照常写一次，任务完成，始终只有一次发布。

11. 申请单物化成动作时，动作—意图链接那一行写失败（触发器注入）：动作与链接一起回滚、按名推迟；
   故障排除、重启后只物化一次，批准后只发布一次（原 test_h1h_operation_current_gates 的 O09）。
替身只有模型回复和发布服务那一侧的意外（:mod:`publish_world`）；产品闸门一个没关。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.contracts.resolution import DeliveryStage
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.completion_status import (
    read_current_effect,
    read_occurrence_completion,
)
from agent_orchestrator.runtime.planning_operations import (
    OperationEffect,
    SourceUnavailable,
    StoreOperationReader,
    build_operation_snapshot,
    operation_gate,
)
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.operation_intent_store import OperationIntentStore
from agent_orchestrator.storage.store import StoreConflict
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    planner_reply,
    review_input,
    review_reply,
)

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from publish_world import (  # noqa: E402
    TARGET,
    Faults,
    Publishing,
    publishing,
    quick_waits,
    root_scope,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def _count(case: Publishing, table: str) -> int:
    return int(case.store.connection.execute(
        f"SELECT count(*) FROM {table} WHERE mission_id=?", (case.mission_id,)).fetchone()[0])  # noqa: S608


def _rows(case: Publishing) -> dict[str, int]:
    return {table: _count(case, table) for table in (
        "attempts", "results", "acceptances", "goal_resolutions", "dispatch_intents", "actions")}


def test_content_acceptance_is_preparation_only_until_the_publish_completes(tmp_path):
    """A13～A17 / E01 / E03 / 准备好的范围数据可读、顺序与终结仍关 / 效果待办时不空转不卡死。"""

    async def run() -> None:
        provider = LayeredScriptedProvider()
        async with publishing(tmp_path, provider=provider) as case:
            approval = await case.until_approval()
            scope = root_scope(case)["document"]
            dispatch = case.world.loop._dispatch_for(case.mission_id)
            network = dispatch.network(case.mission_id)
            # 根（复合目标）负责发布效果；内容那一步已验收，它的产物按端口可读（数据），
            # 但根没完成、没有根结论，任务仍在进行（顺序与终结关着）。
            root = read_occurrence_completion(case.store, case.mission_id, scope.occurrence_id)
            assert scope.required_effect_keys == ("publish-weekly",) and scope.content_criterion_ids == ("c-user-1",)
            assert not root.effects_ready and not root.complete
            index = dispatch.accepted_outputs(case.mission_id, network)
            [leaf] = [t for t in case.store.list_tasks(case.mission_id) if t.id != scope.task_ref.id]
            assert leaf.status.value == "COMPLETED"
            assert [a.id for a in case.store.list_mission_artifacts(case.mission_id) if a.path == TARGET] == [
                item.artifact_id for item in index.outputs]
            assert not dispatch.terminal(case.mission_id) and not dispatch.root_review_ready(case.mission_id)
            effect = read_current_effect(case.store, case.mission_id, scope.spec_hash, "publish-weekly")
            assert effect["state"] == "AWAITING_APPROVAL" and effect["complete"] is False
            assert case.status() == "ACTIVE" and _count(case, "goal_resolutions") == 0
            assert case.published_files() == [] and approval["summary"]["connector"] == "file_publish"
            assert HtnStore(case.store).list_delivery_receipts(case.mission_id) == ()

            # 等人批准期间反复跑：不重派、不再调模型、不判卡死，任务仍在等。
            rows, asked = _rows(case), len(provider.asked)
            for _ in range(3):
                await case.world.drain()
            assert _rows(case) == rows and len(provider.asked) == asked
            assert case.status() == "ACTIVE" and not case.events("MissionStalled")

            # 确认页的只读投影：读它不写库；别的租户经门面看不到这个任务。
            before = case.store.connection.total_changes
            page = case.world.control.snapshot(case.mission_id)["snapshot"]["operation_workspace"]
            assert page["state"] == "APPROVED" and page["spec_hash"] == scope.spec_hash
            assert [item["state"] for item in page["intents"]] == ["AWAITING_APPROVAL"]
            foreign = MissionControlV1(case.world.loop, tenant_id="another-tenant", principal=Principal("foreign-user"))
            with pytest.raises(FacadeError) as hidden:
                foreign.snapshot(case.mission_id)
            assert hidden.value.code == "not_found"
            assert case.store.connection.total_changes == before

            # 规划授权与动作批准互不顶替：别的租户拿不到这个任务的规划授权（什么都不写）；
            # 批准发布也不产生规划授权。
            grants = _count(case, "planning_lane_grants")
            outsider = PlanningAuthorizationApi(case.world.loop.commit, tenant_id="foreign-tenant",
                                                principal=Principal("foreign-planner"))
            before = case.store.connection.total_changes
            with pytest.raises(StoreConflict, match="not available to this caller"):
                outsider.issue(case.mission_id, command_id="o04-foreign-grant")
            assert case.store.connection.total_changes == before

            case.approve(approval)
            mission = await case.world.run_until_settled(case.mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert _count(case, "planning_lane_grants") == grants

            # 效果的证明链把根补完整：效果验收 + 带操作号的"已落盘"交付回执，恰好一份。
            root = read_occurrence_completion(case.store, case.mission_id, scope.occurrence_id)
            assert root.effects_ready and root.complete
            [delivery] = HtnStore(case.store).list_delivery_receipts(case.mission_id)
            assert delivery.stage is DeliveryStage.PERSISTED and delivery.operation_id is not None
            assert len(case.published_files()) == 1 and [a["handoffs"] for a in case.actions()] == [1]

    asyncio.run(run())


def test_a_lost_reply_is_reconciled_and_never_resent(tmp_path):
    """服务端已发布但回执丢了、核对又连不上：只核对不重发，期间全任务的操作总闸关着。"""

    faults = Faults(lose_replies=1, unreachable_lookups=10_000)

    async def run() -> None:
        async with publishing(tmp_path, faults=faults) as case:
            case.approve(await case.until_approval())
            await case.drain_until(lambda: any(a["state"] == "UNKNOWN" for a in case.actions()), rounds=10)
            [action] = case.actions()
            assert action["state"] == "UNKNOWN" and not action.get("receipt"), action
            for _ in range(3):  # 核对一直连不上：仍只核对
                await case.world.drain()
            assert faults.executed == 1 and faults.lookups >= 1 and "reply-lost" in faults.log
            assert case.status() == "ACTIVE" and case.store.get_action(action["action_key"])["state"] == "UNKNOWN"
            # 全任务的操作读侧把它当作未决：计划变更的总闸关着（与哪个计划成员产生它无关）。
            snapshot = build_operation_snapshot(case.mission_id, reader=StoreOperationReader(case.store))
            assert [effect for _, effect in snapshot.effects] == [OperationEffect.UNRESOLVED]
            with pytest.raises(SourceUnavailable, match="operation_unresolved"):
                operation_gate(snapshot)

            faults.unreachable_lookups = 0  # 服务恢复
            mission = await case.world.run_until_settled(case.mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert faults.executed == 1 and len(case.published_files()) == 1
            [action] = case.actions()
            assert action["state"] == "SUCCEEDED" and action["handoffs"] == 1
            assert case.events("ActionOutcomeUnknown") and case.events("ActionReconciled")
            assert mission.final_report["budget_conserved"] is True

    asyncio.run(run())


def test_a_handoff_changes_the_operation_snapshot_a_plan_preview_read(tmp_path):
    """计划预览时读下的操作快照，在执行器真把动作交出去以后就不再是当前的（计划提交按摘要复核，
    摘要变了即判 OPERATION_SNAPSHOT_STALE）。产品上这个窗口里没有计划提交可做，这里只钉读侧事实。"""

    async def run() -> None:
        async with publishing(tmp_path) as case:
            approval = await case.until_approval()
            reader = StoreOperationReader(case.store)
            before = build_operation_snapshot(case.mission_id, reader=reader)
            assert not before.unresolved
            case.approve(approval)
            await case.drain_until(lambda: any(a["handoffs"] for a in case.actions()), rounds=10)
            after = build_operation_snapshot(case.mission_id, reader=reader)
            assert after.read_digest != before.read_digest

    asyncio.run(run())


def test_a_person_rejecting_the_publish_publishes_nothing(tmp_path):
    async def run() -> None:
        async with publishing(tmp_path) as case:
            approval = await case.until_approval()
            decided = case.world.control.decide(approval["request_id"], "reject", reason="这份周报先不发")
            assert decided["request_state"] == "REJECTED"
            for _ in range(5):
                await case.world.drain()
            [action] = case.actions()
            assert action["state"] == "REJECTED" and action["handoffs"] == 0
            assert case.published_files() == [] and not case.ledger_dir.joinpath("ledger.jsonl").exists()
            assert case.status() != "COMPLETED"

    asyncio.run(run())


def test_the_deployment_handoff_cap_refuses_before_anything_leaves(tmp_path):
    from agent_orchestrator.governance.policies import DeploymentPolicy

    capped = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2",
                              max_action_handoffs_per_mission=0)
    faults = Faults()

    async def run() -> None:
        async with publishing(tmp_path, faults=faults, deployment_policy=capped) as case:
            case.approve(await case.until_approval())
            mission = await case.world.run_until_settled(case.mission_id, rounds=10)
            assert str(mission.status.value) == "FAILED" and str(mission.stop_reason) == "action_failed"
            assert mission.final_report["detail"]["reason"] == "handoff_refused:handoff_cap_reached"
            [refused] = case.events("ActionHandoffRefused")
            assert refused.payload["reason"] == "handoff_cap_reached"
            assert [a["handoffs"] for a in case.actions()] == [0]
            assert faults.executed == 0 and faults.lookups == 0 and case.published_files() == []

    asyncio.run(run())


def test_a_publish_lost_before_the_service_is_handed_off_again(tmp_path):
    faults = Faults(drop_requests=1)

    async def run() -> None:
        async with publishing(tmp_path, faults=faults) as case:
            case.approve(await case.until_approval())
            await case.drain_until(lambda: case.status() == "COMPLETED", rounds=20)
            assert case.status() == "COMPLETED"
            [action] = case.actions()
            assert action["state"] == "SUCCEEDED" and action["handoffs"] == 2, action  # 原地重交一次
            assert faults.executed == 1 and len(case.published_files()) == 1
            [proof] = [e.payload for e in case.events("ActionScopedReconciled")]
            assert proof["outcome"] == "NOT_APPLIED_FINAL"
            assert len(case.events("ApprovalRequested")) == 1  # 人只批准过一次

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ("succeeded", "failed"))
def test_a_person_rules_on_a_publish_nobody_can_settle(tmp_path, outcome):
    """发布落盘后文件被用户删掉、回执又丢了：台账说"链接过"，文件却不在——谁也判不了，等人。"""
    from agent_orchestrator.api.facade import FacadeError

    faults = Faults(lose_replies=1, remove_published=True)

    async def run() -> None:
        async with publishing(tmp_path, faults=faults) as case:
            case.approve(await case.until_approval())
            await case.drain_until(lambda: any(a["state"] == "UNKNOWN" and a.get("needs_human")
                                               for a in case.actions()), rounds=10)
            [action] = case.actions()
            assert action["state"] == "UNKNOWN" and action["handoffs"] == 1, action  # 不重交
            control = case.world.control
            with pytest.raises(FacadeError):
                control.resolve_unknown(action["action_key"], outcome=outcome, basis="")
            ruled = control.resolve_unknown(action["action_key"], outcome=outcome, basis="我查了发布目录，文件不在")
            assert ruled["state"] == ("SUCCEEDED" if outcome == "succeeded" else "FAILED")
            [override] = case.events("HumanOverride")
            assert override.payload["subject"] == action["action_key"]
            with pytest.raises(FacadeError):
                control.resolve_unknown(action["action_key"], outcome=outcome, basis="again")
            if outcome == "succeeded":
                return
            # 人裁"没生效"就是证明：闸门打开，系统按原内容出新卡，卡上写着上次是人裁定没生效
            [proof] = [e.payload for e in case.events("ActionScopedReconciled")]
            assert proof["outcome"] == "NOT_APPLIED_FINAL"
            await case.drain_until(lambda: any(a["state"] == "AWAITING_APPROVAL" for a in case.actions()), rounds=20)
            [card] = [a for a in case.actions() if a["state"] == "AWAITING_APPROVAL"]
            assert card["previous_attempt"]["outcome"] == "human_ruled_not_applied"
            faults.remove_published = False
            case.approve(await case.until_approval())
            await case.drain_until(lambda: case.status() == "COMPLETED", rounds=20)
            assert case.status() == "COMPLETED" and len(case.published_files()) == 1

    asyncio.run(run())


def _refusing_reviewer(verdict: str, grade: str, purposes: list[str]):
    def reviewer(request: Any) -> Any:
        package = review_input(request)
        if package is None:
            return None
        purposes.append(package["package"].get("purpose"))
        if package["package"].get("purpose") == "ACTION_PROPOSAL":
            return review_reply(package, verdict=verdict, grade=grade, reason="脚本化审阅：申请单不成立/判断不了。")
        return review_reply(package)

    return reviewer


@pytest.mark.parametrize("verdict,grade,intents,reviews,explanation", (
    ("REJECTED", "FAIL", 1, 1, "需要人来判断"),
    ("INCONCLUSIVE", "UNKNOWN", 3, 6, "3 次没能完成"),
))
def test_a_prepared_publish_the_reviewer_does_not_pass_stops_for_a_person(tmp_path, verdict, grade, intents,
                                                                            reviews, explanation):
    """审阅员不认可：不重交，停下给人判断；判断不了：每份申请单先换会话复审一次，替代重交两次
    （共三份申请单），仍不行就停下。"""

    purposes: list[str] = []

    async def run() -> None:
        provider = LayeredScriptedProvider(reviewer=_refusing_reviewer(verdict, grade, purposes))
        async with publishing(tmp_path, provider=provider) as case:
            mission = await case.world.run_until_settled(case.mission_id, rounds=20)
            assert str(mission.status.value) == "FAILED", mission.final_report
            detail = mission.final_report["detail"]
            assert detail["reason"] == "system_operation_blocked" and detail["verdict"] == verdict
            assert explanation in detail["explanation"] and detail["target"] == TARGET
            rows = OperationIntentStore(case.store).for_mission(case.mission_id)
            assert len(rows) == intents
            assert sum(1 for row in rows if row["supersedes_intent_id"]) == intents - 1
            assert case.actions() == [] and case.published_files() == []
            assert purposes.count("ACTION_PROPOSAL") == reviews and "OPERATION_OUTCOME" not in purposes

    asyncio.run(run())


def test_a_publish_no_step_produces_asks_the_planner(tmp_path):
    """要发布的文件计划里没有步骤写：系统不猜，把事实交给规划器——修复请求点名缺的文件。"""

    seen: list[dict[str, Any]] = []

    def planner(request: Any) -> Any:
        package = package_of(request)
        for entry in package.get("repair_requests") or ():
            context = (entry.get("request") or {}).get("context") or {}
            if context.get("reason") == "system_operation_source_unresolved":
                seen.append(context)
                provider.held.add("planner")  # 后面怎么补步骤由规划器决定，这里不替它答
        return planner_reply(request)

    provider = LayeredScriptedProvider(planner=planner)

    async def run() -> None:
        try:
            async with publishing(tmp_path, provider=provider, criteria=("file:notes.md",
                                                                          "action:file_publish.publish:" + TARGET)) as case:
                await case.run_until(lambda: bool(seen), timeout=60)
                [context] = seen[:1]
                assert context["target"] == TARGET and context["matches"] == []
                assert TARGET in context["explanation"]
                assert OperationIntentStore(case.store).for_mission(case.mission_id) == ()
                assert case.actions() == [] and case.published_files() == []
        finally:
            provider.release.set()

    asyncio.run(run())


def test_the_final_review_sees_the_accepted_root_effect_and_its_readback(tmp_path):
    """真机 2026-09-28：发布跑完、读回核对、结果验收都在根上，最终审查却只拿到子步骤的验收，判
    UNKNOWN 让任务失败。现在根自己已验收的效果和读回核对都是最终审查的材料。"""

    finals: list[dict[str, Any]] = []

    def reviewer(request: Any) -> Any:
        package = review_input(request)
        if package is None:
            return None
        if package["package"].get("purpose") == "MISSION_FINAL":
            finals.append(package)
        return review_reply(package)

    async def run() -> None:
        provider = LayeredScriptedProvider(reviewer=reviewer)
        async with publishing(tmp_path, provider=provider) as case:
            case.approve(await case.until_approval())
            mission = await case.world.run_until_settled(case.mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            [final] = finals
            kinds: dict[str, set[str]] = {}
            for item in final["evidence"]:
                ref = item["ref"]
                kinds.setdefault(ref["kind"], set()).add(ref["pin"]["id"])
            outcome = case.store.connection.execute(
                "SELECT binding_id, document_json FROM operation_outcome_review_bindings WHERE mission_id=?",
                (case.mission_id,)).fetchone()
            assert "acc-" + outcome["binding_id"] in kinds.get("acceptance", set()), kinds
            observations = {str(ref["id"]) for ref in json.loads(outcome["document_json"])["source_receipt_refs"]}
            assert observations and observations <= kinds.get("commit_receipt", set()), kinds

    asyncio.run(run())


def test_two_required_publishes_each_need_their_own_chain(tmp_path):
    """E05 / OCC-06：一份效果的证明链补不了另一份；各自的阶段都看得见。"""

    weekly, summary = TARGET, "reports/summary.md"
    criteria = ("file:" + weekly, "file:" + summary, "action:file_publish.publish:" + weekly,
                "action:file_publish.publish:" + summary)

    async def run() -> None:
        async with publishing(tmp_path, criteria=criteria, confirm=False) as case:
            await case.world.drain()
            page = case.world.control.snapshot(case.mission_id)["snapshot"]["operation_workspace"]
            actions = [c for c in page["criteria"] if c["statement"].startswith("action:")]
            [obligation] = page["obligations"]
            policy = next(m for m in page["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
            ref = page["requirements_ref"]
            keys = {"action:file_publish.publish:" + weekly: "publish-weekly",
                    "action:file_publish.publish:" + summary: "publish-summary"}
            case.world.control.approve_operation_completion_spec({
                "mission_id": case.mission_id, "command_id": "confirm-two-publishes", "expected_requirements_ref": ref,
                "proposal": {
                    "schema_version": 1, "mission_id": case.mission_id,
                    "requirements_ref": {key: ref[key] for key in ("id", "revision", "content_hash")},
                    "mode": "REQUIRED_EFFECTS",
                    "content_criterion_ids": [c["id"] for c in page["criteria"] if c not in actions],
                    "effects": [{"effect_key": keys[c["statement"]], "source_slot_key": keys[c["statement"]],
                                 "obligation_id": obligation["id"], "criterion_ids": [c["id"]],
                                 "required_milestone": policy["id"],
                                 "milestone_policy_ref": policy["milestone_policy_ref"],
                                 "evidence_policy_ref": policy["evidence_policy_ref"]} for c in actions],
                }})
            await case.drain_until(lambda: len(case.pending_approvals()) == 2, rounds=20)
            approvals = {a["summary"]["target"]: a for a in case.pending_approvals()}
            assert set(approvals) == {weekly, summary}
            spec_hash = case.store.connection.execute(
                "SELECT spec_hash FROM operation_completion_specs WHERE mission_id=?", (case.mission_id,)).fetchone()[0]
            scope = root_scope(case)["document"]
            assert set(scope.required_effect_keys) == {"publish-weekly", "publish-summary"}

            case.approve(approvals[summary])
            for _ in range(3):
                await case.world.drain()
            states = {key: read_current_effect(case.store, case.mission_id, spec_hash, key)
                      for key in ("publish-weekly", "publish-summary")}
            assert states["publish-summary"]["complete"] is True
            assert states["publish-weekly"]["state"] == "AWAITING_APPROVAL" and not states["publish-weekly"]["complete"]
            root = read_occurrence_completion(case.store, case.mission_id, scope.occurrence_id)
            assert not root.effects_ready and not root.complete and case.status() == "ACTIVE"
            assert len(case.published_files()) == 1 and len(HtnStore(case.store).list_delivery_receipts(case.mission_id)) == 1

            case.approve(approvals[weekly])
            mission = await case.world.run_until_settled(case.mission_id, rounds=20)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert len(case.published_files()) == 2
            assert len(HtnStore(case.store).list_delivery_receipts(case.mission_id)) == 2
            assert sorted(a["handoffs"] for a in case.actions()) == [1, 1]

    asyncio.run(run())


@pytest.mark.parametrize("field", ("action_version", "params_hash", "target_identity_hash"))
def test_a_tampered_outcome_binding_is_never_accepted(tmp_path, field):
    """OCC-04 / E04：结果审阅绑定的任何一个身份字段在磁盘上被改，效果验收按名推迟，不写交付回执，
    不重发，任务不完成。"""

    from publish_world import ReviewHeld

    from simple_harness.contracts import canonical_json

    faults = Faults()
    provider = ReviewHeld("OPERATION_OUTCOME")

    async def run() -> None:
        try:
            async with publishing(tmp_path, faults=faults, provider=provider) as case:
                case.approve(await case.until_approval())
                await case.run_until(provider.review_entered.is_set, timeout=40)
                row = case.store.connection.execute(
                    "SELECT binding_id, document_json FROM operation_outcome_review_bindings WHERE mission_id=?",
                    (case.mission_id,)).fetchone()
                document = json.loads(row["document_json"])
                document[field] = document[field] + 1 if field == "action_version" else "f" * 64
                with case.store.transaction():
                    case.store.connection.execute("DROP TRIGGER operation_outcome_bindings_immutable_update")
                    case.store.connection.execute(
                        "UPDATE operation_outcome_review_bindings SET document_json=? WHERE binding_id=?",
                        (canonical_json(document), row["binding_id"]))
                provider.go.set()
                await case.run_until(lambda: bool(case.events("OperationOutcomeDeferred")), timeout=40)
                assert case.events("OperationOutcomeDeferred")[0].payload["reason"] == "StoreConflict"
                assert HtnStore(case.store).list_delivery_receipts(case.mission_id) == ()
                assert not case.events("OperationOutcomeAccepted")
                assert faults.executed == 1 and len(case.published_files()) == 1
                assert case.status() != "COMPLETED"
        finally:
            provider.go.set()

    asyncio.run(run())


@pytest.mark.parametrize("table", ("delivery_receipts", "operation_acceptance_scopes"))
def test_an_effect_acceptance_write_failure_rolls_back_and_is_written_once_after_restart(tmp_path, table):
    """OCC-09 / OCC-11：效果验收那次写入里任何一处写失败，整份回滚；排除故障、重启后只写一次。"""

    faults = Faults()
    state: dict[str, str] = {}

    async def first() -> None:
        async with publishing(tmp_path, faults=faults) as case:
            state["mission_id"] = case.mission_id
            approval = await case.until_approval()
            case.store.connection.execute(
                f"CREATE TRIGGER fail_effect_acceptance BEFORE INSERT ON {table} "  # noqa: S608
                "BEGIN SELECT RAISE(ABORT,'disk write failed'); END;")
            case.approve(approval)
            # 写失败按名推迟（记一条推迟事件），不是吞掉。
            await case.run_until(lambda: bool(case.events("OperationOutcomeDeferred")), timeout=40)
            assert case.events("OperationOutcomeDeferred")[0].payload["reason"] == "StoreConflict"
            [action] = case.actions()
            assert action["state"] == "SUCCEEDED" and faults.executed == 1
            assert HtnStore(case.store).list_delivery_receipts(case.mission_id) == ()
            assert not case.events("OperationOutcomeAccepted") and case.status() == "ACTIVE"
            case.store.connection.execute("DROP TRIGGER fail_effect_acceptance")

    async def second() -> None:
        async with publishing(tmp_path, faults=faults, reopen=state["mission_id"]) as case:
            await case.run_until(lambda: case.status() != "ACTIVE", timeout=60)
            assert case.status() == "COMPLETED", case.mission().final_report
            assert faults.executed == 1 and len(case.published_files()) == 1
            assert len(HtnStore(case.store).list_delivery_receipts(case.mission_id)) == 1
            assert len(case.events("OperationOutcomeAccepted")) == 1

    asyncio.run(first())
    asyncio.run(second())


def test_a_materialization_link_write_failure_rolls_back_and_materializes_once_after_restart(tmp_path):
    """O09（原 test_h1h_operation_current_gates）：申请单审过、物化成动作时，动作—意图链接那一行写
    失败（触发器注入，外界的写入故障）：动作和链接一起回滚，按名推迟，没有半个动作可被交接；故障
    排除、重启后只物化一次，批准后只发布一次。"""

    faults = Faults()
    state: dict[str, str] = {}

    async def first() -> None:
        async with publishing(tmp_path, faults=faults) as case:
            state["mission_id"] = case.mission_id
            case.store.connection.execute(
                "CREATE TRIGGER fail_operation_link BEFORE INSERT ON planning_operation_action_links "
                "BEGIN SELECT RAISE(ABORT,'disk write failed'); END;")
            await case.run_until(lambda: bool(case.events("OperationMaterializationDeferred")), timeout=40)
            assert case.actions() == [] and case.pending_approvals() == []
            assert case.store.connection.execute(
                "SELECT count(*) FROM planning_operation_action_links WHERE mission_id=?",
                (case.mission_id,)).fetchone()[0] == 0
            assert len(OperationIntentStore(case.store).for_mission(case.mission_id)) == 1
            assert faults.executed == 0 and case.published_files() == []
            case.store.connection.execute("DROP TRIGGER fail_operation_link")

    async def second() -> None:
        async with publishing(tmp_path, faults=faults, reopen=state["mission_id"]) as case:
            case.approve(await case.until_approval())
            await case.run_until(lambda: case.status() != "ACTIVE", timeout=60)
            assert case.status() == "COMPLETED", case.mission().final_report
            assert len(case.actions()) == 1 and faults.executed == 1 and len(case.published_files()) == 1
            assert case.store.connection.execute(
                "SELECT count(*) FROM planning_operation_action_links WHERE mission_id=?",
                (case.mission_id,)).fetchone()[0] == 1

    asyncio.run(first())
    asyncio.run(second())
