"""H1-H commit guards on the product's deployment (HTN 补齐阶段 A′ 迁移，2026-10-03).

* 预览读集身份不符 → 预览身份闸按名拒绝、一字未写，真命令照常提交（裁决①a）；
* O08：规划器的计划改动在收集器里、预览之后提交之前，人批准了等着的发布申请单：提交在它自己的
  事务里把操作来源整套重读，判过期、不提交；
* O03：发布服务调用中掉线、结果未知（UNKNOWN）：碰到这个操作的计划改动只能等操作对账，
  不提交、不出新版本。

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
from publishing_round import publishing_round, run_rounds

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
            # 是另一件事（它的交接照常发生），所以对外交接不在这里比。
            after = safety_facts(round_.loop.store, round_.mission_id)
            assert {k: v for k, v in after.items() if k != "external_handoffs"} == {
                k: v for k, v in before.items() if k != "external_handoffs"}
            assert "TASKGRAPH_PLAN_SOURCE_CHANGED" in json.dumps(row["detail"]), row
            assert round_.plan_revision() == 1
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]

    asyncio.run(case())


def test_o03_an_unknown_publish_outcome_holds_the_plan_change_back_without_a_revision(
        tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mission-wide UNKNOWN stays authoritative: the plan change waits for the operation to be
    reconciled and never commits on its own.

    While the publishing service cannot be asked, the change waits.  Once the service answers
    that the publish never happened (阶段 B：登记的对账器给出"确实没发生"的证明), that answer is a
    fact the waiting change was not made on: it is refused back to the Planner, still without a
    revision."""
    from agent_orchestrator.runtime import operation_reconciliation_file_publish as reconciliation

    down = {"on": True}
    observe = reconciliation.FilePublishReconciliationAdapter.observe

    def unreachable(self, **kwargs):  # type: ignore[no-untyped-def]
        if down["on"]:
            raise ConnectionError("the publishing service cannot be reached")
        return observe(self, **kwargs)

    monkeypatch.setattr(reconciliation.FilePublishReconciliationAdapter, "observe", unreachable)

    def advanced(round_) -> list[str]:  # type: ignore[no-untyped-def]
        return [event.payload["to_state"] for event in round_.loop.store.list_events(round_.mission_id)
                if event.type == "TaskGraphConvergenceAdvanced"]

    async def case() -> None:
        async with publishing_round(tmp_path, key="h1h-o03", unknown_outcome=True) as round_:
            row = await run_rounds(round_, rounds=10)
            assert row["status"] == "COMPILED", row
            assert round_.plan_revision() == 1
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]
            assert round_.action_states() == ["UNKNOWN"]
            events = round_.loop.store.list_events(round_.mission_id)
            assert [event.payload["kind"] for event in events
                    if event.type == "TaskGraphConvergenceCommandRequested"] == ["RECONCILE_OPERATION"]
            assert advanced(round_)[-1] == "WAITING"
            down["on"] = False
            [action] = round_.loop.store.list_actions(round_.mission_id)
            # the same question the waiting change asked, asked again now that the service answers
            await round_.loop._actions.reconcile_one(str(action["action_key"]), allow_rehandoff=False)
            row = await run_rounds(round_, rounds=10)
            assert row["status"] == "COMMIT_REJECTED", row
            assert "TASKGRAPH_RESUME_SEMANTIC_SOURCE_CHANGED" in json.dumps(row["detail"]), row
            assert advanced(round_)[-1] == "ABANDONED"
            assert _committed_revisions(round_.loop, round_.mission_id) == [1]

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
