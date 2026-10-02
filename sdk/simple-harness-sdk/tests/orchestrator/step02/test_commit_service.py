# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402, E501

"""Step 2 · slice A3/A4: the Commit Service is the single writer — idempotent mission
creation, checked task proposals with replayable receipts, Reserve on attempt
creation, dispatch identity recording, result acceptance / rejection, stop paths and
the Mission-level judgment that is independent of the Task PASS (D21)."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "full_target"))

from leaf_world import leaf_world, loop_config, loop_leaf  # noqa: E402

from agent_orchestrator.contracts import (
    Artifact,
    AttemptStatus,
    Budget,
    ClaimStatus,
    MissionStatus,
    MissionStopReason,
    ResultEnvelope,
    TaskStatus,
)
from agent_orchestrator.governance.budgets import UsageFact
from agent_orchestrator.orchestrator.commit_service import (
    CommitRejected,
    CommitService,
    MissionConflict,
    MissionSpec,
    Reservation,
    task_account,
)
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.output_blocks import PortClaim
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.fixtures import RoleScriptedProvider

SPEC = MissionSpec(
    goal="在隔离工作区实现 parse_kv 并通过测试",
    success_criteria=("pytest:tests/test_parse_kv.py",),
    tenant_id="tenant-a",
    idempotency_key="mission-1",
    allowed_tools=("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests"),
    budget=Budget(max_tokens=10_000, max_attempts=2, max_cost_micros=None),
)


def _service(tmp_path):
    return CommitService(Store.open(tmp_path / "orchestrator.db"))


def _leaf(tmp_path):
    """一个分层任务里的一个步骤（删旧平面模式 第 2 步）：预算与平面夹具相同——任务 1 万
    token、2 次尝试，这一步 8000 token；这一步自己的判据是那个 pytest 目标。"""

    world = leaf_world(
        tmp_path,
        key="mission-1",
        tenant_id="tenant-a",
        goal=SPEC.goal,
        success_criteria=SPEC.success_criteria,
        tools=SPEC.allowed_tools,
        budget=Budget(max_tokens=10_000, max_attempts=2, max_cost_micros=None),
        task_max_tokens=8_000,
        root_statement=SPEC.success_criteria[0],
    )
    return world.service, world.mission, world.tasks["a"]


def _record(service, attempt, task, mission, **kwargs):
    """记录一份结果，认领这一步声明的输出端口（分层步骤的结果必须认领）。"""

    kwargs.setdefault("envelope", _envelope(attempt.id, task.id, mission.id))
    kwargs.setdefault("turn_id", "turn-1")
    kwargs.setdefault("artifacts", [_artifact(attempt.id, task.id, mission.id)])
    kwargs.setdefault("usage_refs", ())
    return service.record_result(
        attempt.id, port_claims=(PortClaim(port_key="result", path="parse_kv.py"),), **kwargs
    )


def _start_verification(service, attempt, stored):
    """主循环收到结果后的顺序：结清这次派发 → 开始核验。"""

    service.settle_intent(service.store.get_intent_for_subject(attempt.id).intent_id, "SETTLED")
    service.start_verification(stored.envelope.id)


def _envelope(attempt_id, task_id, mission_id, artifact_ids=("artifact-x",)):
    return ResultEnvelope(
        id="result-tmp",
        mission_id=mission_id,
        task_id=task_id,
        attempt_id=attempt_id,
        outcome="candidate",
        summary="实现完成",
        claims=(),
        evidence=list(artifact_ids),
        artifacts=list(artifact_ids),
        proposed_tasks=(),
        used_knowledge=(),
        risks=(),
        cost={},
    )


def _artifact(attempt_id, task_id, mission_id, artifact_id="artifact-x"):
    return Artifact(
        id=artifact_id,
        mission_id=mission_id,
        task_id=task_id,
        attempt_id=attempt_id,
        type="file",
        path="parse_kv.py",
        version=1,
        content_hash="0" * 64,
        size_bytes=10,
        produced_by="agent-1",
    )


def _drive_to_running(service, mission_id, task):
    attempt, intent = service.create_attempt(
        task.id,
        role="worker",
        model="agent-model",
        prompt_version="worker-v1",
        context_version="ctx",
        reservation=Reservation(tokens=4_000, cost_micros=0),
        intent_config={"config": {}, "message": "do"},
        input_hash="h",
    )
    service.claim_intent(intent.intent_id, owner="orch-1", lease_seconds=60)
    service.record_agent_created(intent.intent_id, agent_id="agent-1", expected_turn_id="turn-1")
    service.record_submitted(intent.intent_id, receipt={"turn_id": "turn-1", "seq": 1})
    return service.store.get_attempt(attempt.id)


def test_mission_creation_is_idempotent_and_conflict_checked(tmp_path):
    service = _service(tmp_path)
    mission, created = service.create_mission(SPEC)
    assert created and mission.status is MissionStatus.CREATED
    again, created_again = service.create_mission(SPEC)
    assert not created_again and again == mission
    assert service.store.count_events(mission.id, "MissionCreated") == 1
    with pytest.raises(MissionConflict):
        service.create_mission(replace(SPEC, goal="别的目标"))


def test_attempt_reserve_dispatch_identity_and_result_acceptance(tmp_path):
    service, mission, task = _leaf(tmp_path)
    attempt = _drive_to_running(service, mission.id, task)
    assert attempt.status is AttemptStatus.RUNNING
    assert attempt.agent_id == "agent-1" and attempt.turn_id == "turn-1"
    assert attempt.task_version == task.version
    assert service.store.get_task(task.id).status is TaskStatus.ACTIVE
    with service.store.transaction():
        assert service.ledger.account(task_account(task.id)).reserved_tokens == 4_000
    with pytest.raises(CommitRejected):  # only one open Attempt per Task in step 2
        service.create_attempt(
            task.id,
            role="worker",
            model="m",
            prompt_version="v",
            context_version="c",
            reservation=Reservation(1, 0),
            intent_config={},
            input_hash="h",
        )
    envelope = _envelope(attempt.id, task.id, mission.id)
    with pytest.raises(CommitRejected):  # identity forged
        _record(service, attempt, task, mission, envelope=replace(envelope, attempt_id="someone-else"))
    stored = _record(service, attempt, task, mission, usage_refs=("provider-request:r1",))
    duplicate = _record(service, attempt, task, mission, artifacts=[])
    assert duplicate == stored
    assert service.store.count_events(mission.id, "ResultSubmitted") == 1
    assert service.store.get_task(task.id).status is TaskStatus.VERIFYING
    _start_verification(service, attempt, stored)
    assert service.store.get_attempt(attempt.id).status is AttemptStatus.VERIFYING
    service.import_usage(attempt.id, mission.id, [UsageFact("inv-1", 100, 50, None)])
    # 分层步骤一律带内容审查这一层：接受之前要有它的通过记录。
    service.record_verification_layer(stored.envelope.id, layer="critic_review", status="PASS", detail={})
    completed = service.accept_result(
        stored.envelope.id, verifier_results=[{"layer": "code_test", "status": "PASS"}]
    )
    assert completed.status is TaskStatus.COMPLETED and completed.accepted_artifacts == (
        "artifact-x",
    )
    assert service.store.get_attempt(attempt.id).status is AttemptStatus.COMPLETED
    # Mission is NOT completed by the Task PASS alone (D21)
    assert service.store.get_mission(mission.id).status is MissionStatus.ACTIVE
    with service.store.transaction():
        account = service.ledger.account(task_account(task.id))
    assert (
        account.reserved_tokens == 0
        and account.settled_tokens == 150
        and account.unpriced_settlements == 1
    )
    assert service.store.count_events(mission.id, "BudgetReleased") == 1
    # 分层任务不由"逐条判据"直接判完成：那要等根结论（平面的直接判定入口对它关着）。
    with pytest.raises(CommitRejected):
        service.judge_mission(
            mission.id,
            judgments=[{"criterion": SPEC.success_criteria[0], "met": True, "layer": "code_test"}],
            summary="全部通过",
        )
    assert service.store.get_mission(mission.id).status is MissionStatus.ACTIVE


def _in_a_loop(tmp_path, case):
    """真实主循环里一个分层任务的一个步骤：失败后的再试一次要先有"原样重试"的规划决定。"""

    async def run():
        async with Orchestrator(loop_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            await case(await loop_leaf(loop, tmp_path, key="mission-1"))

    asyncio.run(run())


def _leaf_result(leaf, attempt):
    """这次尝试的一份结果，认领这一步声明的必需输出端口。"""

    declared = leaf.dispatch.declared_output_ports_for(leaf.mission.id, leaf.task_id)
    claims = tuple(PortClaim(port_key=item["port"], path="parse_kv.py") for item in declared if item.get("required", True))
    return leaf.service.record_result(
        attempt.id,
        envelope=_envelope(attempt.id, leaf.task_id, leaf.mission.id),
        turn_id=leaf.turn_of(attempt),
        artifacts=[_artifact(attempt.id, leaf.task_id, leaf.mission.id)],
        usage_refs=(),
        port_claims=claims,
    )


def test_failed_verification_retry_and_stop_paths(tmp_path):
    async def case(leaf):
        service, mission_id, task_id = leaf.service, leaf.mission.id, leaf.task_id
        attempt = leaf.running()
        stored = _leaf_result(leaf, attempt)
        service.settle_intent(service.store.get_intent_for_subject(attempt.id).intent_id, "SETTLED")
        service.start_verification(stored.envelope.id)
        claims = service.store.list_claims(stored.envelope.id)
        active = service.fail_result(
            stored.envelope.id,
            failures=[{"layer": "code_test", "status": "FAIL", "summary": "1 failed"}],
        )
        assert active.status is TaskStatus.ACTIVE
        first = service.store.get_attempt(attempt.id)
        assert (
            first.status is AttemptStatus.RETRY_WAIT
            and first.failure["reason"] == "verification_failed"
        )
        assert (
            all(c.status is ClaimStatus.REJECTED for c in service.store.list_claims(stored.envelope.id))
            or not claims
        )
        # 分层任务：没有"原样重试"的决定，下一次尝试建不出来
        with pytest.raises(CommitRejected, match="RETRY_SAME_METHOD"):
            leaf.running(retry_of=first.id)
        assert len(service.store.list_attempts(task_id)) == 1
        # second (retry) attempt with feedback and retry_of
        await leaf.authorize_retry(first)
        second, _ = service.create_attempt(
            task_id,
            role="worker",
            model="m",
            prompt_version="v",
            context_version="c",
            reservation=Reservation(4_000, 0),
            intent_config={},
            input_hash="h2",
            retry_of=first.id,
            feedback=("code_test: 1 failed",),
        )
        assert (
            second.retry_of == first.id
            and second.ordinal == 2
            and second.feedback == ("code_test: 1 failed",)
        )
        assert service.store.get_task(task_id).attempt_count == 2  # 模型做错的那次照常计数
        # 这一步被判停（次数用完）：步骤失败，任务随之以这个原因失败
        service.mark_attempt_lost(second.id, reason="test")
        stopped = service.stop_task(
            task_id, stop_reason=MissionStopReason.MAX_ATTEMPTS_REACHED, detail={"max_attempts": 2}
        )
        assert stopped.status is TaskStatus.FAILED
        failed_mission = service.store.get_mission(mission_id)
        assert failed_mission.status is MissionStatus.FAILED
        assert failed_mission.stop_reason == "max_attempts_reached"
        assert service.store.count_events(mission_id, "MissionFailed") == 1

    _in_a_loop(tmp_path, case)


def test_reject_result_and_cancel_from_verifying(tmp_path):
    async def case(leaf):
        service, mission_id, task_id = leaf.service, leaf.mission.id, leaf.task_id
        attempt = leaf.running()
        rejected = service.reject_result(
            attempt.id,
            turn_id=leaf.turn_of(attempt),
            reason="envelope_invalid",
            detail={"error": "no envelope block"},
        )
        assert (
            rejected.status is AttemptStatus.RETRY_WAIT
            and rejected.failure["reason"] == "envelope_invalid"
        )
        assert service.store.get_task(task_id).status is TaskStatus.ACTIVE  # formal Task unchanged
        assert service.store.count_events(mission_id, "ResultRejected") == 1
        # cancel while a second attempt is verifying: VERIFYING → ACTIVE → CANCELLED (D15')
        await leaf.authorize_retry(attempt)
        second = leaf.running(retry_of=attempt.id)
        stored = _leaf_result(leaf, second)
        service.settle_intent(service.store.get_intent_for_subject(second.id).intent_id, "SETTLED")
        service.start_verification(stored.envelope.id)
        cancelled = service.cancel_mission(mission_id)
        assert cancelled.status is MissionStatus.CANCELLED
        assert service.store.get_task(task_id).status is TaskStatus.CANCELLED
        assert service.store.get_attempt(second.id).status is AttemptStatus.CANCELLED

    _in_a_loop(tmp_path, case)


# --------------------------------------------------------------------------------------
# 迁自 step03/test_static_dag_closure.py（删旧平面模式 第三刀）：两条只需要任务行的
# Commit Service 机制测试，换成分层步骤。
# --------------------------------------------------------------------------------------


def _attempt_kwargs():
    return dict(
        role="worker",
        model="agent-model",
        prompt_version="w",
        context_version="c",
        reservation=Reservation(tokens=1000, cost_micros=0),
        intent_config={"agent_config": {}, "message": {}},
        input_hash="h",
    )


def test_r1_mission_wide_concurrency_is_enforced_in_the_commit(tmp_path):
    """P1-11: the Mission-wide open-Attempt bound is checked inside `create_attempt`."""

    # two independent steps: one open Attempt per step, so the Mission-wide bound is
    # exercised across steps
    world = leaf_world(tmp_path, key="r1-conc", leaves=("a", "b"))
    service, a, b = world.service, world.tasks["a"], world.tasks["b"]
    kwargs = _attempt_kwargs()
    service.create_attempt(a.id, max_open_attempts=1, **kwargs)
    with pytest.raises(CommitRejected) as exc:
        service.create_attempt(b.id, max_open_attempts=1, **kwargs)
    assert "max_concurrency" in str(exc.value)
    with pytest.raises(CommitRejected, match="already has an open Attempt"):
        service.create_attempt(a.id, max_open_attempts=3, **kwargs)  # one per step
    attempt2, intent2 = service.create_attempt(b.id, max_open_attempts=2, **kwargs)
    assert intent2.config["attempt_id"] == attempt2.id  # P1-7: authoritative id in the intent


def test_r1_stale_owner_cannot_commit_a_verdict(tmp_path):
    """P1-3: accept/fail are refused for an owner whose lease lapsed and was taken over."""

    clock = {"now": 1000.0}
    world = leaf_world(tmp_path, key="r1-stale", clock=lambda: clock["now"])
    service, mission, a = world.service, world.mission, world.tasks["a"]
    attempt, intent = service.create_attempt(a.id, **_attempt_kwargs())
    service.claim_intent(intent.intent_id, owner="orch-1", lease_seconds=10)
    service.record_agent_created(intent.intent_id, agent_id="agent-x", expected_turn_id="turn-x")
    service.record_submitted(intent.intent_id, receipt={"turn_id": "turn-x"})
    stored = service.record_result(
        attempt.id,
        envelope=replace(_envelope(attempt.id, a.id, mission.id), id="result-1"),
        turn_id="turn-x",
        artifacts=[_artifact(attempt.id, a.id, mission.id)],
        usage_refs=(),
        port_claims=(PortClaim(port_key="result", path="parse_kv.py"),),
    )
    _start_verification(service, attempt, stored)
    service.record_verification_layer(
        stored.envelope.id, layer="critic_review", status="PASS", detail={}
    )
    clock["now"] += 20  # orch-1's lease lapsed
    service.renew_lease(attempt.id, owner="orch-2", lease_seconds=10, liveness={})  # takeover
    with pytest.raises(CommitRejected):
        service.accept_result(stored.envelope.id, verifier_results=[], owner="orch-1")
    with pytest.raises(CommitRejected):
        service.fail_result(stored.envelope.id, failures=[], owner="orch-1")
    assert (
        service.accept_result(stored.envelope.id, verifier_results=[], owner="orch-2").status
        is TaskStatus.COMPLETED
    )
