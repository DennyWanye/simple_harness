"""Ordered workflow startup recovery for durable Session deletion fences."""
from __future__ import annotations

from typing import Any, Iterable


async def activate_and_recover_workflows(
    *,
    project_bindings: Any,
    workflow_service: Any,
    workflow_launcher: Any,
    required_runtime_identities: Iterable[str],
) -> dict[str, Any]:
    """Reconcile cross-DB deletes before any activation or recovery ingress."""

    delete_reconcile = {"attempted": 0, "completed": 0, "failed": 0}
    if project_bindings is not None:
        async def cancel_deleted_session_runs(session_id: str) -> object:
            return await workflow_service.cancel_runs_for_session(
                session_id,
                reason="session_deleted_startup_reconcile",
            )

        delete_reconcile = await project_bindings.reconcile_deleted_session_runs(
            cancel_deleted_session_runs
        )
    await workflow_service.activate_runtime(
        required_runtime_identities=required_runtime_identities
    )
    startup_recoveries = await workflow_service.runner.recover_expired()
    recovered_decisions = await workflow_launcher.recover_open_decision_events()
    recovered_deliveries = await workflow_launcher.recover_due_deliveries(
        recover_claimed=True
    )
    recovered_runs = await workflow_launcher.recover_pending()
    workflow_launcher.start_dispatcher()
    return {
        "delete_reconcile": delete_reconcile,
        "startup_recoveries": startup_recoveries,
        "recovered_decisions": recovered_decisions,
        "recovered_deliveries": recovered_deliveries,
        "recovered_runs": recovered_runs,
    }


__all__ = ["activate_and_recover_workflows"]
