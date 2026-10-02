# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P33-08/09 · record_result 的集中领域闸门（code-v1 一支）。

合法 Mission/Task/Attempt 经公共 commit 入口推进至 RUNNING，产物来自真实 CAS。
code-v1 的旧证据字符串不作全域收紧。

2026-10-02 删旧平面模式第三刀（严格引用选 A）：文档领域两支删；任务行改由分层
``leaf_world`` 提供。
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from leaf_world import leaf_world

from agent_orchestrator.artifacts.store import ArtifactStore
from agent_orchestrator.contracts import (
    Artifact,
    AttemptStatus,
    ClaimProposal,
    ResultEnvelope,
)
from agent_orchestrator.orchestrator.commit_service import CommitService, Reservation
from agent_orchestrator.runtime.output_blocks import PortClaim


@pytest.fixture
def running(tmp_path):
    world = leaf_world(tmp_path, key="evidence-gate", goal="核对报告", clock=lambda: 1_000.0)
    store = world.store
    cas = ArtifactStore(tmp_path / "artifacts")
    service = CommitService(store, artifact_store=cas, deployed_layers=frozenset({"code_test"}))

    def make():
        mission = world.mission
        task = world.task("a")
        attempt, intent = service.create_attempt(
            task.id,
            role="worker",
            model="fixture-model",
            prompt_version="worker-v2",
            context_version="ctx",
            reservation=Reservation(1_000, 0),
            intent_config={"agent_config": {}, "message": "核对报告"},
            input_hash="fixture",
        )
        turn_id = "turn:" + attempt.id
        service.claim_intent(intent.intent_id, owner="fixture", lease_seconds=60)
        service.record_agent_created(
            intent.intent_id, agent_id="agent:" + attempt.id, expected_turn_id=turn_id
        )
        service.record_submitted(intent.intent_id, receipt={"turn_id": turn_id, "seq": 1})
        data = b"report\n"
        digest = cas.put_bytes(data)
        artifact = Artifact(
            id="artifact:" + attempt.id,
            mission_id=mission.id,
            task_id=task.id,
            attempt_id=attempt.id,
            type="file",
            path="REPORT.md",
            version=1,
            content_hash=digest,
            size_bytes=len(data),
            produced_by="agent:" + attempt.id,
            storage_uri=str(cas.path_for(digest)),
        )
        envelope = ResultEnvelope(
            id="result:" + attempt.id,
            mission_id=mission.id,
            task_id=task.id,
            attempt_id=attempt.id,
            outcome="candidate",
            summary="报告候选",
            claims=(ClaimProposal(content="资料中有该陈述", confidence=0.8),),
            evidence=("artifact:REPORT.md",),
            artifacts=("REPORT.md",),
            proposed_tasks=(),
            used_knowledge=(),
            risks=(),
            cost={},
        )
        assert store.get_attempt(attempt.id).status is AttemptStatus.RUNNING
        return mission, attempt, turn_id, artifact, envelope

    try:
        yield service, make
    finally:
        store.close()


def _record(service, attempt, turn_id, artifact, envelope):
    return service.record_result(
        attempt.id,
        envelope=envelope,
        turn_id=turn_id,
        artifacts=(artifact,),
        usage_refs=(),
        port_claims=(PortClaim(port_key="result", path=artifact.path),),
    )


@pytest.mark.parametrize(
    "reference", ["pytest:tests/test_legacy.py", "tool-run:legacy-call", "legacy-custom:opaque"]
)
def test_code_record_result_keeps_legacy_evidence_admission(running, reference):
    service, make = running
    mission, attempt, turn_id, artifact, envelope = make()
    envelope = replace(
        envelope,
        evidence=(reference,),
        claims=(replace(envelope.claims[0], evidence=(reference,)),),
    )
    stored = _record(service, attempt, turn_id, artifact, envelope)
    assert stored.envelope.evidence == (reference,)
    assert service.store.list_claims(envelope.id)[0].evidence == (reference,)
    assert service.store.get_attempt(attempt.id).status is AttemptStatus.SUBMITTED
