# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""OCC-02: actual Host dispatcher/facade confirms an existing requirements mapping.

This is a source integration check. It is not native UI or an operation-outcome
test; the requirements fixture is persisted by the SDK's real requirements Store.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.contracts.resolution import (
    Criterion, CriterionExpr, CriterionOrigin, EvaluationKind,
    RequirementClass, RequirementsRevision,
)
from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind
from agent_orchestrator.orchestrator.operation_completion import OperationCompletionReader
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore

from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


@pytest.mark.asyncio
async def test_completion_confirmation_uses_actual_host_principal(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(),
        principal=principal, drive=False,
    )
    await service.start()
    try:
        created = await handle(service, "mission_create", notes_request("completion-host"))
        assert created["payload"]["ok"], created
        mission_id = created["payload"]["data"]["mission_id"]
        store = service._orchestrator.store
        PlanningDecisionStore(store).bind_mission_protocol(
            mission_id, protocol_version="planning-decision-v1", package_version=6,
            prompt_version="planner-hierarchical-v9", binding_hash="a" * 64,
        )
        requirements = RequirementsRevision(
            revision_id="req-host-completion", mission_id=mission_id, revision=1,
            criteria=(Criterion(
                criterion_id="report", revision=1, origin=CriterionOrigin.USER_EXPLICIT,
                statement="file:NOTES.md", requirement_class=RequirementClass.REQUIRED_OUTCOME,
                evaluation_kind=EvaluationKind.DETERMINISTIC,
            ),),
            success_expression=CriterionExpr("report"),
        )
        HtnStore(store).insert_requirements_revision(requirements)
        ref = TypedRef(
            kind=TypedRefKind.REQUIREMENTS, id=str(requirements.revision_id), revision=1,
            content_hash=requirements.content_hash(),
        )
        body = {
            "mission_id": mission_id, "command_id": "confirm-host-completion",
            "expected_requirements_ref": ref.to_json(),
            "proposal": {
                "schema_version": 1, "mission_id": mission_id,
                "requirements_ref": {
                    "id": ref.id, "revision": ref.revision, "content_hash": ref.content_hash,
                },
                "mode": "CONTENT_ONLY", "content_criterion_ids": ["report"], "effects": [],
            },
        }
        result = await handle(service, "mission_operation_completion_approve", body)
        assert result["payload"]["ok"], result
        receipt = result["payload"]["data"]
        assert receipt["authority"]["issuer_id"] == principal.principal_id
        assert receipt["authority"]["tenant_id"] == service.tenant_id
        assert receipt["authority"]["kind"] == "USER_CONFIRMED"
        assert OperationCompletionReader(store).read_requirements(
            mission_id, ref
        ).content_hash() == receipt["spec_hash"]
        before = store.connection.total_changes
        repeated = await handle(service, "mission_operation_completion_approve", body)
        assert repeated["payload"]["data"] == receipt
        assert store.connection.total_changes == before
        for field in ("principal", "approved", "requirement_authority"):
            rejected = await handle(
                service, "mission_operation_completion_approve", {**body, field: True},
            )
            assert rejected["payload"]["ok"] is False
            assert rejected["payload"]["error_code"] == "invalid_request"
        assert store.connection.total_changes == before
        assert service._decision is None
        await service._rebuild()
        assert service._decision is None
        recovered = await handle(service, "mission_operation_completion_approve", body)
        assert recovered["payload"]["ok"], recovered
        assert recovered["payload"]["data"] == receipt
        recovered_store = service._orchestrator.store
        assert OperationCompletionReader(recovered_store).read_requirements(
            mission_id, ref
        ).content_hash() == receipt["spec_hash"]
    finally:
        await service.close()
