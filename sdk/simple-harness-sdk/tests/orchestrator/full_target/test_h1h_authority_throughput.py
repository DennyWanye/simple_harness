"""A01: authenticated grant through the real collector and Plan Commit.

The planning round is the adoption round the main loop opened after the planner's
proposed method passed its independent review (``h1i_seed.reviewed``); its authority
was issued by the deployment's duty for the authenticated person (auto permission mode,
the product's default), through the facade's planning-authorization command.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from h1i_seed import events as _events
from h1i_seed import plan_reply, reviewed

from agent_orchestrator.contracts.planning_decisions import PlanningDecisionStatus
from agent_orchestrator.orchestrator.commit_service import CommitService
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore


def test_a01_authenticated_grant_binds_request_and_real_refine_commit_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def case() -> None:
        captured: list[tuple[Any, Any]] = []
        original = CommitService.commit_planning_revision

        def observe_commit(self, command, principal, **kwargs):  # type: ignore[no-untyped-def]
            captured.append((command, principal))
            return original(self, command, principal, **kwargs)

        monkeypatch.setattr(CommitService, "commit_planning_revision", observe_commit)
        async with reviewed(tmp_path, key="h1h-a01") as ((loop, mission, _world, _root, dispatch, product), intent, _provider):
            issuer = product.deployment.principal
            request = PlanningDecisionStore(loop.store).get_planning_request(intent.intent_id)
            binding = PlanningAdmissionStore(loop.store).get_request_binding(intent.intent_id)
            assert request is not None and binding is not None
            planner_principal_id = binding["planner_principal_id"]
            assert planner_principal_id == issuer.principal_id
            assert request.mission_id == mission.id
            assert binding["mission_id"] == mission.id
            assert binding["tenant_id"] == mission.tenant_id
            assert binding["scope_id"] == "mission"
            grant = PlanningAdmissionStore(loop.store).get_grant(
                binding["grant_id"], binding["grant_revision"]
            )
            assert grant is not None
            assert binding["grant_hash"] == grant["content_hash"]

            durable_grant = grant
            assert durable_grant is not None
            assert durable_grant["issuer_id"] == issuer.principal_id
            assert durable_grant["planner_principal_id"] == planner_principal_id
            assert durable_grant["mission_id"] == mission.id
            assert durable_grant["tenant_id"] == mission.tenant_id
            assert durable_grant["issuer_command_id"] == f"host-auto-planning:{intent.intent_id}"

            await loop._collect_plan_decision(
                intent,
                object(),
                mission,
                plan_reply(intent.config["planning_package"]),
                dispatch,
            )

            assert _events(loop, mission.id, "PlanningDecisionEvaluated")[-1].payload[
                "status"
            ] == str(PlanningDecisionStatus.COMMITTED)
            assert len(_events(loop, mission.id, "PlanRevisionCommitted")) == 1
            assert dispatch.network(mission.id).plan_revision == 1
            assert len(captured) == 1
            command, commit_principal = captured[0]
            assert command.mission_id == request.mission_id == durable_grant["mission_id"]
            assert command.scope_id == binding["scope_id"] == durable_grant["scope_id"]
            assert command.issued_by == commit_principal.principal_id == planner_principal_id

    asyncio.run(case())
