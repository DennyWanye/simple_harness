# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Shared drivers for the step-4 unit tests: two parallel hierarchical leaf steps (a ‖ b) on
a bare Commit Service, Attempts driven to RUNNING, results with typed claims."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_orchestrator.contracts import Artifact, Budget, ClaimProposal, ResultEnvelope
from agent_orchestrator.orchestrator.commit_service import CommitService, Reservation

TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")
HASH_A = "a" * 64
HASH_B = "b" * 64


def two_leaf_service(
    tmp_path, *, key: str = "k-1", check: str = "pytest:tests/probe/test_impl_a.py", **spec_overrides
):
    """分层版的"两个并列步骤"（删旧平面模式 第 2 步）：一个已提交计划的分层 Mission，根做法
    是 a、b 两个互不依赖的原子步骤；返回 (service, mission, [步骤 a 的任务, 步骤 b 的任务])。

    ``check`` 是根要求的原文；写成 pytest 目标时两个步骤都带上这条检查，和平面夹具里每个
    任务自带 ``pytest:tests/probe/…`` 判据是同一回事——没有它，"这条结论有没有被那次测试
    证实"这类断言在分层步骤上会空过。"""

    import sys
    from pathlib import Path

    full_target = Path(__file__).resolve().parents[1] / "full_target"
    if str(full_target) not in sys.path:
        sys.path.append(str(full_target))
    from leaf_world import leaf_world

    world = leaf_world(
        tmp_path,
        key=key,
        leaves=("a", "b"),
        goal="比较两个实现对同一输入合同的支持程度",
        success_criteria=("pytest:tests/test_comparison.py",),
        tenant_id="tenant-4",
        budget=Budget(max_tokens=100_000, max_attempts=6),
        root_statement=check,
        spec_overrides={"untrusted_sources": ("docs/",), **spec_overrides},
    )
    return world.service, world.mission, [world.tasks["a"], world.tasks["b"]]


def drive_to_running(
    service: CommitService,
    task,
    *,
    owner: str = "orch-1",
    agent: str = "agent-1",
    turn: str = "turn-1",
):
    attempt, intent = service.create_attempt(
        task.id,
        role="worker",
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


def claim(content: str, **fields: Any) -> ClaimProposal:
    return ClaimProposal(content=content, confidence=fields.pop("confidence", 0.8), **fields)


def envelope(
    attempt,
    *,
    claims: Sequence[ClaimProposal],
    artifacts: Sequence[str] = ("tests/probe/test_impl_a.py",),
    evidence: Sequence[str] | None = None,
    used_knowledge: Sequence[str] = (),
    summary: str = "探针完成",
) -> ResultEnvelope:
    return ResultEnvelope(
        id=f"result-{attempt.id}",
        mission_id=attempt.mission_id,
        task_id=attempt.task_id,
        attempt_id=attempt.id,
        outcome="candidate",
        summary=summary,
        claims=tuple(claims),
        evidence=tuple(artifacts if evidence is None else evidence),
        artifacts=tuple(artifacts),
        proposed_tasks=(),
        used_knowledge=tuple(used_knowledge),
        risks=(),
        cost={},
    )


def artifact(attempt, path: str, content_hash: str = HASH_A, version: int = 1) -> Artifact:
    from agent_orchestrator.contracts import ids

    return Artifact(
        id=ids.artifact_id(attempt.id, path, content_hash),
        mission_id=attempt.mission_id,
        task_id=attempt.task_id,
        attempt_id=attempt.id,
        type="file",
        path=path,
        version=version,
        content_hash=content_hash,
        size_bytes=10,
        produced_by=attempt.agent_id or attempt.id,
    )


def passed_layers(*targets: str) -> list[Mapping[str, Any]]:
    """What the Verifier Router hands to ``accept_result``: PASS layers with detail."""

    runs = [{"target": target, "passed": True, "stdout": "1 passed"} for target in targets]
    return [
        {"layer": "format_check", "status": "PASS", "summary": "ok", "detail": {}},
        {"layer": "rule_check", "status": "PASS", "summary": "ok", "detail": {}},
        {"layer": "code_test", "status": "PASS", "summary": "ok", "detail": {"runs": runs}},
    ]


def submit(
    service,
    attempt,
    env: ResultEnvelope,
    *,
    artifact_paths=("tests/probe/test_impl_a.py",),
    turn="turn-1",
    hashes=None,
):
    hashes = hashes or {}
    extra: dict[str, Any] = {}
    if artifact_paths:
        # 分层步骤声明了一个必需的输出端口：结果要认领它（生产里由执行者在结果里写）。
        from agent_orchestrator.runtime.output_blocks import PortClaim

        extra["port_claims"] = (PortClaim(port_key="result", path=artifact_paths[0]),)
    stored = service.record_result(
        attempt.id,
        envelope=env,
        turn_id=turn,
        artifacts=[artifact(attempt, path, hashes.get(path, HASH_A)) for path in artifact_paths],
        usage_refs=(),
        **extra,
    )
    # 主循环收到结果后先结清这次派发，再开始核验。
    service.settle_intent(service.store.get_intent_for_subject(attempt.id).intent_id, "SETTLED")
    service.start_verification(stored.envelope.id)
    # 分层步骤一律带内容审查这一层；接受结果之前必须已有它的通过记录。
    service.record_verification_layer(
        stored.envelope.id, layer="critic_review", status="PASS", detail={"producer": "fixture"}
    )
    return stored
