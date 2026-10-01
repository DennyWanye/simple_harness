# SPDX-License-Identifier: Apache-2.0
"""Durable solver results for the existing Planner decision/Commit transaction."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from ..contracts.models import ContractError
from ..planning.htn.backend_port import BackendStatus, CandidatePlanWitness, PlanningBackendResult
from .hierarchical_dispatch import append_hierarchical_event


def deployment_identity(handler: Any) -> dict[str, Any]:
    """What this process plans with: native or solver, its limits, the selection policy."""
    backend = handler._config.planning_backend
    limits = handler._config.planning_backend_limits
    if (backend is None) != (limits is None):
        raise ContractError("solver deployment requires both backend and limits")
    from ..planning.htn.method_selection import SelectionPolicyMode
    selection_policy = str(SelectionPolicyMode(handler._config.method_selection_policy))
    return {"backend_id": "native" if backend is None else backend.backend_id,
            "limits": None if limits is None else limits.to_json(),
            "method_selection_policy": selection_policy}


def frozen_deployment_conflict(handler: Any, mission_id: str) -> str | None:
    """Why the deployment this Mission froze is not this process's, or ``None``.

    A Mission freezes its planning deployment at its first round.  One frozen under
    another identity (a different policy or backend, or an identity an older build
    wrote) is not replanned here: the loop stops it by name at its entry
    (``Orchestrator._refuse_unsupported_contract``) instead of letting the mismatch
    raise out of ``run()`` on its next round.
    """
    identity = deployment_identity(handler)
    for event in handler.store.iter_events(mission_id):
        if event.type == "PlanningDeploymentBound" and dict(event.payload) != identity:
            return ("planning deployment differs from this Mission's frozen deployment: "
                    f"frozen {dict(event.payload)!r}, this process {identity!r}")
        if event.type == "PlanningBackendBound" and (
            event.payload.get("backend_id") != identity["backend_id"]
            or event.payload.get("limits") != identity["limits"]
        ):
            return "planning backend differs from this Mission's frozen deployment"
    return None


def bind_deployment(handler: Any, mission_id: str) -> None:
    """Freeze native/solver selection before the first request, including recovery."""
    identity = deployment_identity(handler)
    with handler.store.transaction():
        conflict = frozen_deployment_conflict(handler, mission_id)
        if conflict is not None:
            raise ContractError(conflict)
        if not any(event.type == "PlanningDeploymentBound"
                   for event in handler.store.iter_events(mission_id)):
            append_hierarchical_event(handler.store, "PlanningDeploymentBound", mission_id,
                                      key=mission_id, payload=identity)


async def solve_decision(handler: Any, *, mission_id: str, decision_id: str,
                         lane: Any) -> tuple[Any, PlanningBackendResult]:
    bind_deployment(handler, mission_id)
    backend = handler._config.planning_backend
    limits = handler._config.planning_backend_limits
    if backend is None or limits is None:
        raise ContractError("solver deployment requires its backend and limits")
    identity = {"backend_id": backend.backend_id, "limits": limits.to_json()}
    for event in handler.store.iter_events(mission_id):
        if event.type == "PlanningBackendBound" and dict(event.payload) != identity:
            raise ContractError("planning backend differs from this Mission's frozen deployment")
    append_hierarchical_event(handler.store, "PlanningBackendBound", mission_id,
                              key=mission_id, payload=identity)
    snapshot = lane.snapshot()
    for event in handler.store.iter_events(mission_id):
        if event.type != "PlanningBackendReturned" or event.payload.get("decision_id") != decision_id:
            continue
        if event.payload["snapshot_digest"] != snapshot.digest:
            raise ContractError("solver decision snapshot changed on recovery")
        raw = event.payload.get("witness")
        witness = None if raw is None else CandidatePlanWitness(
            backend_id=raw["backend_id"], snapshot_digest=raw["snapshot_digest"],
            steps=tuple(raw["steps"]), plan_hash=raw["plan_hash"], hierarchy_json=raw["hierarchy_json"])
        return snapshot, PlanningBackendResult(BackendStatus(event.payload["status"]), snapshot.digest,
                                              witness, event.payload.get("detail", ""))
    result = await asyncio.to_thread(backend.solve, snapshot, limits)
    if result.snapshot_digest != snapshot.digest:
        raise ContractError("solver returned a result for another snapshot")
    append_hierarchical_event(handler.store, "PlanningBackendReturned", mission_id,
        key=decision_id, payload={"decision_id": decision_id, "snapshot_digest": snapshot.digest,
            "status": str(result.status), "detail": result.detail,
            "witness": None if result.witness is None else {
                **asdict(result.witness), "steps": list(result.witness.steps)}})
    return snapshot, result
