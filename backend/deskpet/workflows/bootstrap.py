"""Product bootstrap for the local durable workflow service."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .definitions.v1 import register_v1_workflows
from .definitions.v2 import register_v2_workflows
from .definitions.v3 import register_v3_workflows
from .definitions.v4 import register_v4_workflows
from .definitions.v5 import register_v5_workflows
from .definitions.v6 import register_v6_workflows
from .definitions.v7 import register_v7_workflows
from .retention import ClockPort, RetentionPolicy, SystemClock, WorkflowRetentionManager
from .runner import WorkflowRegistry, WorkflowRunner
from .execution_ports import WorkflowExecutionPorts
from .runtime_adapters import RuntimeIdentity, WorkflowRuntimeAdapterRegistry
from .service import WorkflowService
from .store import NativeCheckpointStore, RegisteredBlobStore, WorkflowRunStore
from .store.checkpoint_execution import SqliteCheckpointExecutionAdapter
from .store.execution_uow import SqliteExecutionUnitOfWork
from .store.research_repository import ResearchWorkflowRepository


async def build_workflow_service(
    user_data_dir: str | Path,
    *,
    delivery_handlers: Mapping[str, Callable[..., Any | Awaitable[Any]]] | None = None,
    retention_policy: RetentionPolicy | None = None,
    retention_clock: ClockPort | None = None,
    retention_dry_run: bool = False,
    workflow_blob_root: str | Path | None = None,
    session_delivery_state_reader: Callable[[str], Awaitable[Mapping[str, Any]]] | None = None,
    activate: bool = True,
    required_runtime_identities: Sequence[RuntimeIdentity] = (),
) -> WorkflowService:
    """Initialize local storage and register every recoverable graph version."""

    root = Path(user_data_dir)
    db_path = root / "data" / "workflow.db"
    store = WorkflowRunStore(db_path)
    await store.initialize()
    execution_uow = SqliteExecutionUnitOfWork(db_path)
    checkpoint_execution = SqliteCheckpointExecutionAdapter(execution_uow)
    execution_ports = WorkflowExecutionPorts(
        unit_of_work=execution_uow,
        checkpoint=checkpoint_execution,
    )
    saver = NativeCheckpointStore(
        db_path, execution_adapter=checkpoint_execution
    )
    registry = WorkflowRegistry()
    register_v1_workflows(registry)
    register_v2_workflows(registry)
    register_v3_workflows(registry)
    register_v4_workflows(registry)
    register_v5_workflows(registry)
    register_v6_workflows(registry)
    register_v7_workflows(registry)
    runner = WorkflowRunner(
        store, saver, registry, execution_ports=execution_ports
    )
    selected_blob_root = Path(workflow_blob_root or root / "workflows" / "blobs")
    research_blobs = RegisteredBlobStore(
        selected_blob_root,
        db_path,
    )
    runtime_adapters = WorkflowRuntimeAdapterRegistry()

    async def _activate_foundations() -> None:
        await store.block_legacy_nonterminal_runs(
            native_implementation_hashes=registry.implementation_hashes()
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
        await service.recover_v6_continuation_snapshots()

    service = WorkflowService(
        run_store=store,
        runner=runner,
        delivery_handlers=delivery_handlers,
        session_delivery_state_reader=session_delivery_state_reader,
        research_repository=ResearchWorkflowRepository(
            db_path, blob_root=selected_blob_root
        ),
        research_snapshot_loader=research_blobs.get,
        runtime_adapters=runtime_adapters,
        execution_ports=execution_ports,
        owns_execution_uow=True,
        runtime_activation_required=True,
        runtime_activation_hooks=(_activate_foundations,),
    )
    if activate:
        await service.activate_runtime(
            required_runtime_identities=required_runtime_identities
        )
    return service


__all__ = ["build_workflow_service"]
