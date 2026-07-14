"""Product bootstrap for the local durable workflow service."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .definitions.v1 import register_v1_workflows
from .retention import ClockPort, RetentionPolicy, SystemClock, WorkflowRetentionManager
from .runner import WorkflowRegistry, WorkflowRunner
from .service import WorkflowService
from .store import NativeCheckpointStore, WorkflowRunStore


async def build_workflow_service(
    user_data_dir: str | Path,
    *,
    delivery_handlers: Mapping[str, Callable[..., Any | Awaitable[Any]]] | None = None,
    retention_policy: RetentionPolicy | None = None,
    retention_clock: ClockPort | None = None,
    retention_dry_run: bool = False,
    workflow_blob_root: str | Path | None = None,
) -> WorkflowService:
    """Initialize local storage and register every recoverable graph version."""

    root = Path(user_data_dir)
    db_path = root / "data" / "workflow.db"
    store = WorkflowRunStore(db_path)
    await store.initialize()
    saver = NativeCheckpointStore(db_path)
    registry = WorkflowRegistry()
    register_v1_workflows(registry)
    await store.block_legacy_nonterminal_runs(
        native_implementation_hashes=registry.implementation_hashes()
    )
    runner = WorkflowRunner(store, saver, registry)
    service = WorkflowService(
        run_store=store,
        runner=runner,
        delivery_handlers=delivery_handlers,
    )
    if retention_policy is not None:
        retention = WorkflowRetentionManager(
            db_path,
            workflow_blob_root or root / "workflows" / "blobs",
            policy=retention_policy,
            clock=retention_clock or SystemClock(),
        )
        service.retention_diagnostics = await retention.reconcile_startup(
            dry_run=retention_dry_run
        )
    await runner.recover_expired()
    return service


__all__ = ["build_workflow_service"]
