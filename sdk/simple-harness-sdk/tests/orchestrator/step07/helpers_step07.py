# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Shared builders for the step-7 tests: a bare Commit Service with one ACTIVE hierarchical
Mission and its leaf steps, the test configuration service and the people who decide.

删旧平面模式 第三刀：``ledger_service`` 的底座从平面任务图（A→B→C、A→D）换成
``full_target/leaf_world.py`` 的分层世界——四个原子步骤 a、b、c、d（b、d 在 a 之后，c 在 b
之后），返回的字典仍按 ``"A"``…``"D"`` 取任务行，调用方不用改。原 ``graph_helpers7``
里的中性部分（``drive_to_running``、``complete`` 的分层版、``TOOLS``/``HASH``）也挪到这里，
平面的 ``spec``/``node``/``DIAMOND``/``graph_service`` 随平面删。"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_orchestrator.contracts import Artifact, ClaimProposal, ResultEnvelope, ids
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.orchestrator.commit_service import CommitService, Reservation
from agent_orchestrator.runtime.connectors import PaymentConnectorStub, TestConfigService
from agent_orchestrator.runtime.output_blocks import PortClaim

_FULL_TARGET = Path(__file__).resolve().parents[1] / "full_target"
if str(_FULL_TARGET) not in sys.path:
    sys.path.append(str(_FULL_TARGET))

TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")
HASH = "c" * 64

ALICE = Principal("alice", "Alice")
BOB = Principal("bob", "Bob")

# D7-3': a deployment enables nothing by default; the tests enable the test service
ENABLED = DeploymentPolicy(enabled_connectors=("test_config",))
# the Mission charter's action scope (its action criteria) for the ledger tests
LEDGER_CRITERIA = (
    "file:CHANGE.md",
    "action:test_config.set:feature_flags.new_ui",
    "action:test_config.read:feature_flags.new_ui",
    "action:test_config.delete:feature_flags.new_ui",
    "action:test_config.set:a",
    "action:test_config.set:b",
    "action:test_config.set:c",
)


def ledger_service(
    tmp_path, *, deployment: DeploymentPolicy | None = None, clock=None, criteria=LEDGER_CRITERIA
):
    """A Commit Service with one ACTIVE hierarchical Mission whose charter carries
    ``criteria`` (its action scope), its leaf steps keyed ``"A"``…``"D"``, plus the
    connectors.  Returns ``(service, mission, tasks, config, connectors, deployment)``."""

    from leaf_world import leaf_world  # noqa: PLC0415 - test-local helper

    world = leaf_world(
        tmp_path,
        key="g-1",
        leaves=("a", "b", "c", "d"),
        ordering=(("a", "b"), ("b", "c"), ("a", "d")),
        goal="实现记录器并验证",
        success_criteria=tuple(criteria),
        tenant_id="tenant-5",
        clock=clock,
    )
    service, mission = world.service, world.mission
    tasks = {name.upper(): task for name, task in world.tasks.items()}
    config = TestConfigService(Path(tmp_path) / "test-services" / "config.json")
    connectors = {"test_config": config, "payment": PaymentConnectorStub()}
    return service, mission, tasks, config, connectors, deployment or ENABLED


def candidate(
    target="feature_flags.new_ui", value="on", *, operation="set", connector="test_config"
):
    params = {} if operation in {"read", "delete"} else {"value": value}
    return {
        "connector": connector,
        "operation": operation,
        "target": target,
        "params": params,
        "reason": "按需求修改测试配置",
    }


def drive_to_running(
    service: CommitService, task, *, owner="orch-1", agent="agent-1", turn="turn-1", role="worker"
):
    """Create a step's Attempt and drive it to "submitted to the executor" (RUNNING)."""

    attempt, intent = service.create_attempt(
        task.id,
        role=role,
        model="agent-model",
        prompt_version="worker-v2",
        context_version="ctx",
        reservation=Reservation(tokens=4_000, cost_micros=0),
        intent_config={"agent_config": {}, "message": "do"},
        input_hash="h",
    )
    service.claim_intent(intent.intent_id, owner=owner, lease_seconds=60)
    service.record_agent_created(intent.intent_id, agent_id=agent, expected_turn_id=turn)
    service.record_submitted(intent.intent_id, receipt={"turn_id": turn, "seq": 1})
    return service.store.get_attempt(attempt.id)


def complete(service: CommitService, task, *, agent="agent-1", turn="turn-1", path="out.md"):
    """Drive a hierarchical leaf step to COMPLETED the way the main loop does: the result
    claims the step's output port, the dispatch is settled, verification starts, the
    content-review layer passes, then the result is accepted."""

    attempt = drive_to_running(service, task, agent=agent, turn=turn)
    envelope = ResultEnvelope(
        id=f"result-{attempt.id}",
        mission_id=task.mission_id,
        task_id=task.id,
        attempt_id=attempt.id,
        outcome="candidate",
        summary="done",
        claims=(ClaimProposal(content="done", confidence=0.9),),
        evidence=(path,),
        artifacts=(path,),
        proposed_tasks=(),
        used_knowledge=(),
        risks=(),
        cost={},
    )
    artifact = Artifact(
        id=ids.artifact_id(attempt.id, path, HASH),
        mission_id=task.mission_id,
        task_id=task.id,
        attempt_id=attempt.id,
        type="file",
        path=path,
        version=1,
        content_hash=HASH,
        size_bytes=3,
        produced_by=agent,
    )
    stored = service.record_result(
        attempt.id,
        envelope=envelope,
        turn_id=turn,
        artifacts=[artifact],
        usage_refs=(),
        port_claims=(PortClaim(port_key="result", path=path),),
    )
    service.settle_intent(service.store.get_intent_for_subject(attempt.id).intent_id, "SETTLED")
    service.start_verification(stored.envelope.id)
    service.record_verification_layer(
        stored.envelope.id, layer="critic_review", status="PASS", detail={"producer": "fixture"}
    )
    return service.accept_result(
        stored.envelope.id, verifier_results=[{"layer": "rule_check", "status": "PASS"}]
    )


__all__ = (
    "ALICE",
    "BOB",
    "ENABLED",
    "HASH",
    "LEDGER_CRITERIA",
    "TOOLS",
    "candidate",
    "complete",
    "drive_to_running",
    "ledger_service",
)
