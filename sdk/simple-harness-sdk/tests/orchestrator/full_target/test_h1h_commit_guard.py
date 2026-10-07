"""H1-H commit guards on the product's deployment (HTN 补齐阶段 A′ 迁移，2026-10-03).

* 预览读集身份不符 → 预览身份闸按名拒绝、一字未写，真命令照常提交（裁决①a）；
* O08：规划器的计划改动在收集器里、预览之后提交之前，人批准了等着的发布申请单：提交在它自己的
  事务里把操作来源整套重读，判过期、不提交；
* O03：发布服务调用中掉线、结果未知（UNKNOWN）：根结论先形成，对根的修复当场被拒，不出新版本。
* O03b：同样结果未知，但终审判不通过、根职责还开着：碰到这个操作的计划改动先经执行图收敛
  "先对账未决操作"，等待；对账说"确实没发生"后放开，等着的改动按"来源已变"退回规划器，
  规划器在新事实上重交，提交成功。

两条操作用例跑在 ``publishing_round``（产品同形代表用例三走到"申请单等人批准"，再让规划器给
写文件那一步提后继步骤）。偏离分诊表：产品上这两种情形分别由执行图参与方的来源重读
（``TASKGRAPH_PLAN_SOURCE_CHANGED``）和执行图收敛"先对账未决操作"挡下，走不到 H1-H 自己的
``OPERATION_SNAPSHOT_STALE`` / ``OPERATION_UNRESOLVED``。原 A05 / I06（授权变更）在
test_h1h_authority_matrix::test_a04_*，A08（撤销后重放）在
test_h1i_decision_replay::test_committed_refine_replays_after_grant_revocation_*（偏离：删 2）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest
from h1i_seed import plan_reply, refuse_tampered_first, reviewed
from publishing_round import publishing_round, refused_final_round, run_rounds

from agent_orchestrator.storage.htn_store import HtnStore


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _committed_revisions(loop, mission_id: str) -> list[int]:
    return [event.payload["plan_revision"] for event in loop.store.list_events(mission_id)
            if event.type == "PlanRevisionCommitted"]


def test_o08_an_approval_landing_between_preview_and_commit_makes_the_plan_change_stale(tmp_path) -> None:
    async def case() -> None:
        async with publishing_round(tmp_path, key="h1h-o08") as round_:
            dispatch, approved = round_.dispatch, {}
            original = dispatch.preview_plan_proposal

            def preview_then_approve(proposal, *, inputs):  # type: ignore[no-untyped-def]
                result = original(proposal, inputs=inputs)
                if not approved:
                    approved.update(round_.world.control.decide(round_.approval_id, "approve"))
                return result

            from safety_facts import safety_facts

            before = safety_facts(round_.loop.store, round_.mission_id)
            dispatch.preview_plan_proposal = preview_then_approve  # type: ignore[method-assign]
            try:
                row = await run_rounds(round_)
            finally:
                dispatch.preview_plan_proposal = original  # type: ignore[method-assign]
            assert approved["request_state"] == "GRANTED", approved
            assert row["status"] == "COMMIT_REJECTED", row
            # 被拒的改计划没有留下任何东西：没有新计划、没有新派发、没有预留被放掉。人批准的那次发布
            # 是另一件事（它的交接与它那条预留照常发生），所以这两样不在这里比。
            def without_the_publish(facts):  # type: ignore[no-untyped-def]
                return {**{k: v for k, v in facts.items() if k != "external_handoffs"},
                        "reservations": [row for row in facts["reservations"]
                                         if not row[0].startswith("reservation-action:")]}

            assert without_the_publish(safety_facts(round_.loop.store, round_.mission_id)) == without_the_publish(before)
            assert "TASKGRAPH_PLAN_SOURCE_CHANGED" in json.dumps(row["detail"]), row
            assert round_.plan_revision() == 1
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]

    asyncio.run(case())


def test_o03_an_unknown_publish_outcome_holds_the_plan_change_back_without_a_revision(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mission-wide UNKNOWN stays authoritative: a plan change never commits on its own while the
    publish outcome is unknown, and the outcome is only ever settled by asking the publisher.

    2026-10-06 第 2～4 批车道 O（A48，原计划 Assurance §7.2）起：发布结果不明时根结论先形成、收尾
    以 BLOCKED_UNKNOWN 等效果收敛，根职责已了结。原先"修复提交等对账、对账说没发生再退回规划器"
    的局面不再出现：对这个根的修复提交当场按职责已了结被拒（OBLIGATION_NOT_OPEN），不编修订；
    发布服务恢复、登记的对账器给出"确实没发生"之后，计划仍只有第 1 版——没生效的发布由系统操作线
    处理（重交 / 问规划器 / 具名停下），不是这次被拒的修复。"""
    from agent_orchestrator.runtime import operation_reconciliation_file_publish as reconciliation

    down = {"on": True}
    observe = reconciliation.FilePublishReconciliationAdapter.observe

    def unreachable(self, **kwargs):  # type: ignore[no-untyped-def]
        if down["on"]:
            raise ConnectionError("the publishing service cannot be reached")
        return observe(self, **kwargs)

    monkeypatch.setattr(reconciliation.FilePublishReconciliationAdapter, "observe", unreachable)

    async def case() -> None:
        async with publishing_round(tmp_path, key="h1h-o03", unknown_outcome=True) as round_:
            store = round_.loop.store
            assert any(event.type == "GoalResolutionCommitted" and event.payload.get("is_mission_root")
                       for event in store.list_events(round_.mission_id)), "根结论先于效果形成（§7.2）"
            assert round_.plan_revision() == 1
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]
            assert round_.action_states() == ["UNKNOWN"]
            await run_rounds(round_, rounds=5)
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]  # 结果不明期间不编修订
            down["on"] = False
            [action] = store.list_actions(round_.mission_id)
            await round_.loop._actions.reconcile_one(str(action["action_key"]), allow_rehandoff=False)
            await run_rounds(round_, rounds=5)
            assert round_.decision()["status"] == "REJECTED"
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]  # 对账之后也没有修订

    asyncio.run(case())


def test_preview_read_set_identity_mismatch_refuses_before_writes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delivery whose admission names another preview read set than the command's is
    refused by the preview identity gate before anything is written; the real delivery
    of the same round then commits (adjudication ①a)."""

    refusals = refuse_tampered_first(
        monkeypatch,
        lambda command, principal, kwargs: (
            command,
            principal,
            {**kwargs, "admission": dataclasses.replace(
                kwargs["admission"], preview_read_set_hash="0" * 64)},
        ),
        "PREVIEW_IDENTITY_STALE",
    )

    async def case() -> None:
        async with reviewed(tmp_path, key="h1h-preview-read-set") as ((loop, mission, _world, _root, dispatch, _product), opener, _provider):
            await loop._collect_plan_decision(
                opener, object(), mission, plan_reply(opener.config["planning_package"]), dispatch
            )
            assert len(refusals) == 1
            assert len(HtnStore(loop.store).list_plan_revisions(mission.id)) == 1

    asyncio.run(case())


# =====================================================================================
# 暂留，供他人导入（2026-10-03）：旧的手搭准入快照，本文件已不再使用；
# test_h1h_operation_current_gates 还在导入 ``_setup``，它迁完后整节删除。
# =====================================================================================
import hashlib  # noqa: E402

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi  # noqa: E402
from agent_orchestrator.contracts.planning_decisions import (  # noqa: E402
    PLANNING_DECISION_V1,
    PlanningRequestBinding,
)
from agent_orchestrator.contracts.semantic_base import content_hash_of  # noqa: E402
from agent_orchestrator.governance.permissions import Principal  # noqa: E402
from agent_orchestrator.governance.planning_authorization import (  # noqa: E402
    PlanningAuthorizationSnapshot,
    StorePlanningAuthorityReader,
    build_planning_authorization,
    planning_policy_for_mission,
)
from agent_orchestrator.orchestrator.planning_admission_commits import (  # noqa: E402
    PlanningCommitAdmission,
)
from agent_orchestrator.planning.plan_preview import _source_snapshot_payload  # noqa: E402
from agent_orchestrator.runtime.planning_operations import (  # noqa: E402
    StoreOperationReader,
    build_operation_snapshot,
    read_running_work,
)
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore  # noqa: E402
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402
from simple_harness.contracts import canonical_json  # noqa: E402


def _setup(world, *, command=None):
    """The world is bound and its root requirements confirmed when it is built."""

    command = command or world.command
    request = PlanningRequestBinding(
        request_id="request-h1h-guard",
        mission_id=world.mission.id,
        protocol_version=PLANNING_DECISION_V1,
        package_version=6,
        package_hash="a" * 64,
        base_plan_revision=0,
        requirements_revision=int(command.read_set.requirements_revision),
        scope_epoch_digest="b" * 64,
        subject_bindings_hash="c" * 64,
        visible_refs_digest="d" * 64,
        prompt_version="planner-hierarchical-v9",
        prompt_hash="e" * 64,
        created_at=world.store.now,
        intent_id="intent-h1h-guard",
    )
    PlanningDecisionStore(world.store).insert_planning_request(request)
    api = PlanningAuthorizationApi(
        world.store,
        tenant_id=world.mission.tenant_id,
        principal=Principal(world.principal.principal_id),
    )
    grant = api.issue(
        world.mission.id,
        command_id="grant-h1h-guard",
        request_id=request.request_id,
    )
    authority = build_planning_authorization(
        request.request_id,
        read=StorePlanningAuthorityReader(PlanningAdmissionStore(world.store), world.store),
        caller=world.principal,
        policy=planning_policy_for_mission(world.store, world.mission.id),
        now_ms=int(world.store.now * 1000),
    )
    assert isinstance(authority, PlanningAuthorizationSnapshot)
    operations = build_operation_snapshot(
        world.mission.id,
        reader=StoreOperationReader(world.store),
    )
    runtime_work = read_running_work(
        world.mission.id,
        (),
        reader=StoreOperationReader(world.store),
    )
    compilation_hash = hashlib.sha256(
        canonical_json(
            {
                "delta": command.delta.to_json(),
                "network": _source_snapshot_payload(command.network),
            }
        )
        .encode("utf-8")
    ).hexdigest()
    admission = PlanningCommitAdmission(
        request_id=request.request_id,
        decision_hash="f" * 64,
        decision_key="REFINE",
        authority=authority,
        operations=operations,
        runtime_work=runtime_work,
        preview_request_id=request.request_id,
        preview_decision_hash="f" * 64,
        preview_compilation_hash=compilation_hash,
        preview_read_set_hash=content_hash_of(command.read_set.to_json()),
    )
    return api, grant, admission


def _plan_revision_count(world) -> int:
    return len(HtnStore(world.store).list_plan_revisions(world.mission.id))


def test_o03b_a_plan_change_touching_an_unknown_publish_waits_for_reconciliation_then_is_handed_back(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """终审判不通过时根职责还开着，规划器换掉发布操作的生产者（写文件那一步）。

    执行图收敛先问发布服务这次发布到底有没有发生（RECONCILE_OPERATION）：服务答不上来就一直等，
    不提交、不出新版本；服务答"确实没发生"后，等着的改动是在旧事实上做的，按
    TASKGRAPH_RESUME_SEMANTIC_SOURCE_CHANGED 退回规划器（收敛作业 ABANDONED）；规划器在新事实上
    重交同样的修复，这次没有未决操作挡着，第 2 版计划提交。"""
    from safety_facts import safety_facts

    from agent_orchestrator.runtime import operation_reconciliation_file_publish as reconciliation

    down = {"on": True}
    original = reconciliation.FilePublishReconciliationAdapter.observe

    def unreachable(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if down["on"]:
            raise ConnectionError("the publishing service cannot be reached")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(reconciliation.FilePublishReconciliationAdapter, "observe", unreachable)

    def events(round_, kind: str) -> list[dict]:  # type: ignore[no-untyped-def]
        return [event.payload for event in round_.loop.store.list_events(round_.mission_id) if event.type == kind]

    def held_reservations(round_) -> list[tuple]:  # type: ignore[no-untyped-def]
        return [row for row in safety_facts(round_.loop.store, round_.mission_id)["reservations"]
                if row[0].startswith("reservation-action:")]

    async def rounds(round_, n: int) -> None:  # type: ignore[no-untyped-def]
        for _ in range(n):
            await round_.world.deployment.between_cycles(auto=True)
            await round_.loop._cycle()
            await asyncio.sleep(0.01)

    async def case() -> None:
        async with refused_final_round(tmp_path, key="h1h-o03b") as round_:
            store = round_.loop.store
            assert not any(item.get("is_mission_root") for item in events(round_, "GoalResolutionCommitted"))
            assert [action["state"] for action in store.list_actions(round_.mission_id)] == ["UNKNOWN"]
            [first] = round_.repairs()
            assert first["status"] == "COMPILED", first
            assert [item["kind"] for item in events(round_, "TaskGraphConvergenceCommandRequested")] == ["RECONCILE_OPERATION"]
            assert events(round_, "TaskGraphConvergenceAdvanced")[-1]["to_state"] == "WAITING"
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]
            held = held_reservations(round_)
            assert held, "结果不明的发布动作有一条预留"

            await rounds(round_, 5)  # 服务答不上来：一直等，不提交、不出新版本
            assert round_.repairs()[0]["status"] == "COMPILED"
            assert events(round_, "TaskGraphConvergenceAdvanced")[-1]["to_state"] == "WAITING"
            assert held_reservations(round_) == held  # 等待期间原动作的预留原样保留（K10）
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]

            down["on"] = False
            [action] = store.list_actions(round_.mission_id)
            await round_.loop._actions.reconcile_one(str(action["action_key"]), allow_rehandoff=False)
            assert [item["outcome"] for item in events(round_, "ActionScopedReconciled")] == ["NOT_APPLIED_FINAL"]
            await rounds(round_, 10)

            first = round_.repairs()[0]
            assert first["status"] == "COMMIT_REJECTED", first
            assert "TASKGRAPH_RESUME_SEMANTIC_SOURCE_CHANGED" in json.dumps(first["detail"]), first
            by_job: dict[str, list[str]] = {}
            for item in events(round_, "TaskGraphConvergenceAdvanced"):
                by_job.setdefault(item["job_id"], []).append(item["to_state"])
            [refused, committed] = by_job.values()
            assert refused[-1] == "ABANDONED" and "WAITING" in refused, by_job
            assert "WAITING" not in committed, by_job  # 重交时操作已了结，不再等
            assert len(round_.answered) == 2  # 规划器被退回后在新事实上重交了一次
            assert round_.repairs()[-1]["status"] == "COMMITTED", round_.repairs()[-1]
            assert _committed_revisions(round_.loop, round_.mission_id) == [1, 2]
            assert events(round_, "PlanningRepairAddressed")
            # 原动作保留原身份，没有被再交出一次（K10：替代动作不重复执行原发布）
            assert store.list_actions(round_.mission_id)[0]["action_key"] == action["action_key"]
            assert [item["action_key"] for item in events(round_, "ActionHandedOff")].count(action["action_key"]) == 1

    asyncio.run(case())
