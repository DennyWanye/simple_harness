"""Accepted-async bridge from ToolRegistry calls to durable graph runs."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from deskpet.execution import RecoveryLease

from .contracts import JsonValue, TERMINAL_RUN_STATUSES, WorkflowContext
from .progress import WorkflowProgressReporter
from .runner import WorkflowRunResult
from .runtime_adapters import (
    ContextFactory,
    DEEP_RESEARCH_EXTENSION,
    DeepResearchRuntimeExtension,
    StateFactory,
    WorkflowRuntimeAdapter,
    WorkflowRuntimeAdapterError,
    WorkflowRuntimeAdapterRegistry,
)
from .service import WorkflowService
from .execution_ports import WorkflowExecutionPorts

_DELIVERY_PAGE_SIZE = 100
_RUN_SCAN_LIMIT = 10_000
_DISPATCHER_SAFETY_INTERVAL = 30.0


class WorkflowLauncher:
    """Create one run, retain its background task, and close its outbox once."""

    def __init__(
        self,
        service: WorkflowService,
        *,
        execution_ports: WorkflowExecutionPorts | None = None,
    ) -> None:
        self.service = service
        self.execution_ports = execution_ports or getattr(service, "execution_ports", None)
        if self.execution_ports is not None:
            if getattr(service, "execution_ports", None) is None:
                service.execution_ports = self.execution_ports
            runner = getattr(service, "runner", None)
            configure = getattr(runner, "configure_execution_ports", None)
            if not callable(configure):
                raise ValueError("precreated launcher requires an execution-aware runner")
            configure(self.execution_ports)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._scheduled_run_ids: set[str] = set()
        self._run_tasks: dict[str, asyncio.Task[Any]] = {}
        self._continuation_launch_locks: dict[str, asyncio.Lock] = {}
        runtime_adapters = getattr(service, "runtime_adapters", None)
        if not isinstance(runtime_adapters, WorkflowRuntimeAdapterRegistry):
            runtime_adapters = WorkflowRuntimeAdapterRegistry()
            try:
                setattr(service, "runtime_adapters", runtime_adapters)
            except Exception:  # pragma: no cover - very small protocol fakes
                pass
        self.runtime_adapters = runtime_adapters
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._dispatcher_interval = _DISPATCHER_SAFETY_INTERVAL
        self._dispatcher_wakeup = asyncio.Event()
        self._delivery_dispatch_lock = asyncio.Lock()
        self._next_deadline: float | None = None

    async def retry_from_start_locked(
        self,
        source: Mapping[str, Any],
        *,
        retry_key: str,
        session_lock: asyncio.Lock,
    ) -> dict[str, Any]:
        """Launch a versioned research retry from an immutable start reference."""

        if not session_lock.locked():
            raise RuntimeError("retry_from_start_locked requires the session lock")
        source_run_id = str(source.get("run_id") or "")
        identity_value = source.get("identity")
        start_payload = source.get("start_payload")
        capabilities = source.get("original_capabilities")
        if not source_run_id or not isinstance(identity_value, Mapping):
            raise RuntimeError("workflow retry start reference is invalid")
        if not isinstance(start_payload, Mapping) or not isinstance(capabilities, Mapping):
            raise RuntimeError("workflow retry payload or capabilities are invalid")
        key = (str(source.get("workflow_name") or ""), str(source.get("workflow_version") or ""))
        adapter = self.runtime_adapters.get(*key)
        extension = adapter.deep_research if adapter is not None else None
        if key[0] != "deep_research" or extension is None or not extension.retry_from_start:
            raise RuntimeError("workflow research retry adapter is unavailable")
        state_factory, context_factory = adapter.state_factory, adapter.context_factory
        identity = dict(identity_value)
        request_id = f"retry:{retry_key}"
        logical_slot = f"retry:{source_run_id}:{retry_key}"
        targets = (
            ("session_message", str(identity["delivery_session_id"])),
            ("websocket", str(identity["delivery_session_id"])),
        )
        accepted = await self.service.start_workflow(
            venue=str(identity["venue"]),
            base_session_id=str(identity["base_session_id"]),
            code_session_id=str(identity.get("code_session_id") or "") or None,
            delivery_session_id=str(identity["delivery_session_id"]),
            request_id=request_id,
            turn_id=str(identity["turn_id"]),
            workflow_name="deep_research",
            workflow_version=key[1],
            capability_snapshot=dict(capabilities),
            start_payload=dict(start_payload),
            base_epoch=int(identity["base_epoch"]),
            code_epoch=int(identity["code_epoch"]),
            logical_slot=logical_slot,
            delivery_targets=(*targets, ("receipt", str(identity["delivery_session_id"]))),
        )
        async with self._delivery_dispatch_lock:
            await self.service.deliver_event_once(accepted["accepted_event_id"])
        self.notify_dispatcher()
        if accepted["created"]:
            task = asyncio.create_task(
                self._drive(
                    run_id=str(accepted["run_id"]),
                    start_payload=dict(start_payload),
                    state_factory=state_factory,
                    context_factory=context_factory,
                    targets=targets,
                ),
                name=f"workflow:retry:deep_research:{accepted['run_id']}",
            )
            self._track_run_task(str(accepted["run_id"]), task)
        return {
            "run_id": str(accepted["run_id"]),
            "source_run_id": source_run_id,
            "created": bool(accepted["created"]),
            "accepted": True,
            "completion_semantics": "accepted_async",
            "action_id": "retry_from_start",
            "retry_key": retry_key,
        }

    def register_adapter(
        self,
        workflow_name: str,
        workflow_version: str,
        *,
        state_factory: StateFactory,
        context_factory: ContextFactory,
        extensions: Mapping[str, object] | None = None,
    ) -> None:
        selected_extensions = dict(extensions or {})
        if workflow_name == "deep_research" and DEEP_RESEARCH_EXTENSION not in selected_extensions:
            selected_extensions[DEEP_RESEARCH_EXTENSION] = self._legacy_deep_extension(
                workflow_version
            )
        self.runtime_adapters.register(
            WorkflowRuntimeAdapter(
                workflow_name=workflow_name,
                workflow_version=workflow_version,
                state_factory=state_factory,
                context_factory=context_factory,
                extensions=selected_extensions,
            )
        )

    @staticmethod
    def _legacy_deep_extension(workflow_version: str) -> DeepResearchRuntimeExtension:
        version = str(workflow_version)
        if version == "v5":
            return DeepResearchRuntimeExtension(
                new_runs_enabled=True,
                retry_from_start=True,
                action_ids=(
                    "cancel_settle",
                    "continue_research",
                    "generate_now",
                    "retry_from_start",
                ),
            )
        if version == "v4":
            return DeepResearchRuntimeExtension(
                new_runs_enabled=True,
                retry_from_start=True,
                action_ids=("retry_from_start",),
            )
        if version == "v6":
            return DeepResearchRuntimeExtension(new_runs_enabled=False)
        return DeepResearchRuntimeExtension(new_runs_enabled=True)

    def _resolve_launch_adapter(
        self,
        workflow_name: str,
        workflow_version: str,
        *,
        state_factory: StateFactory,
        context_factory: ContextFactory,
    ) -> WorkflowRuntimeAdapter:
        adapter = self.runtime_adapters.get(workflow_name, workflow_version)
        if adapter is None:
            if self.runtime_adapters.sealed:
                raise WorkflowRuntimeAdapterError(
                    "runtime_adapter_unavailable",
                    "workflow runtime adapter was not registered before activation",
                )
            self.register_adapter(
                workflow_name,
                workflow_version,
                state_factory=state_factory,
                context_factory=context_factory,
            )
            adapter = self.runtime_adapters.require(workflow_name, workflow_version)
        return adapter

    def _require_runtime_active(self) -> None:
        ensure = getattr(self.service, "ensure_runtime_active", None)
        if callable(ensure):
            ensure()

    async def wake_run_control(self, run_id: str) -> dict[str, Any]:
        """Wake the local driver after a durable v5 control CAS.

        A live driver polls the durable command before/after each atomic effect,
        so it only needs a dispatcher wakeup.  A created/retryable run that lost
        its local task is safely reconstructed from its checkpoint.
        """

        task = self._run_tasks.get(str(run_id))
        if task is not None and not task.done():
            self.notify_dispatcher()
            return {"run_id": str(run_id), "active": True, "recovered": False}
        row = await self.service.run_store.get_run(str(run_id))
        if row is None:
            raise RuntimeError("workflow control run was not found")
        if str(row.get("status")) in {"created", "retryable"}:
            recovered = await self.recover_pending(only_run_ids={str(run_id)})
            self.notify_dispatcher()
            return {
                "run_id": str(run_id),
                "active": str(run_id) in self._run_tasks,
                "recovered": str(run_id) in recovered,
            }
        # A running run retains its lease-owning driver.  Starting another local
        # task here would race that owner; durable recovery takes over only after
        # the lease expires.
        self.notify_dispatcher()
        return {"run_id": str(run_id), "active": False, "recovered": False}

    async def launch_existing_run(
        self,
        run_id: str,
        *,
        start_payload: Mapping[str, JsonValue],
    ) -> dict[str, Any]:
        """Schedule an atomically-created continuation run exactly once."""

        run_id = str(run_id)
        lock = self._continuation_launch_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            row = await self.service.run_store.get_run(run_id)
            if row is None:
                raise RuntimeError("continuation run was not found")
            key = (str(row["workflow_name"]), str(row["workflow_version"]))
            adapter = self.runtime_adapters.get(*key)
            extension = adapter.deep_research if adapter is not None else None
            if key[0] != "deep_research" or extension is None:
                raise RuntimeError("deep_research continuation adapter is unavailable")
            if key[1] != "v5" and extension.continuation is None:
                raise RuntimeError("deep_research continuation codec is unavailable")
            existing = self._run_tasks.get(run_id)
            if existing is not None and not existing.done():
                return {"run_id": run_id, "created": False, "accepted": True}
            if str(row.get("status") or "") not in {"created", "retryable"}:
                return {"run_id": run_id, "created": False, "accepted": True}
            targets = await self._persisted_delivery_targets(run_id)
            if not targets:
                raise RuntimeError("continuation delivery session is unavailable")
            state_factory, context_factory = adapter.state_factory, adapter.context_factory
            task = asyncio.create_task(
                self._drive(
                    run_id=run_id,
                    start_payload=dict(start_payload),
                    state_factory=state_factory,
                    context_factory=context_factory,
                    targets=targets,
                ),
                name=f"workflow:continue:deep_research:{run_id}",
            )
            self._track_run_task(run_id, task)
            self.notify_dispatcher()
            return {"run_id": run_id, "created": True, "accepted": True}

    async def launch(
        self,
        *,
        workflow_name: str,
        workflow_version: str,
        session_id: str,
        request_id: str,
        turn_id: str,
        start_payload: Mapping[str, JsonValue],
        capability_snapshot: Mapping[str, JsonValue],
        state_factory: StateFactory,
        context_factory: ContextFactory,
        venue: str = "chat",
        code_session_id: str | None = None,
        delivery_session_id: str | None = None,
        base_epoch: int = 0,
        code_epoch: int = 0,
        logical_slot: str = "accepted_async:0",
        delivery_targets: Sequence[tuple[str, str]] | None = None,
        authorize_disabled_deep_research_v6_new_root: bool = False,
    ) -> dict[str, Any]:
        adapter = self._resolve_launch_adapter(
            workflow_name,
            workflow_version,
            state_factory=state_factory,
            context_factory=context_factory,
        )
        if workflow_name == "deep_research":
            extension = adapter.deep_research
            if extension is None:
                raise WorkflowRuntimeAdapterError(
                    "deep_research_extension_unavailable",
                    "deep_research new roots require a registered runtime extension",
                )
            v6_override = bool(
                authorize_disabled_deep_research_v6_new_root
                and workflow_version == "v6"
            )
            if not extension.new_runs_enabled and not v6_override:
                raise WorkflowRuntimeAdapterError(
                    "deep_research_new_runs_disabled",
                    f"deep_research new runs are disabled for {workflow_version!r}",
                )
        self._require_runtime_active()
        resolved_delivery_session_id = delivery_session_id or session_id
        targets = tuple(
            delivery_targets
            or (
                ("session_message", resolved_delivery_session_id),
                ("websocket", resolved_delivery_session_id),
            )
        )
        receipt_target = ("receipt", resolved_delivery_session_id)
        accepted_targets = (*targets, receipt_target)
        accepted = await self.service.start_workflow(
            venue=venue,
            base_session_id=session_id,
            code_session_id=code_session_id,
            delivery_session_id=resolved_delivery_session_id,
            request_id=request_id,
            turn_id=turn_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            capability_snapshot=capability_snapshot,
            start_payload=start_payload,
            base_epoch=base_epoch,
            code_epoch=code_epoch,
            logical_slot=logical_slot,
            delivery_targets=accepted_targets,
        )
        async with self._delivery_dispatch_lock:
            await self.service.deliver_event_once(accepted["accepted_event_id"])
        self.notify_dispatcher()
        if accepted["created"]:
            # DeepResearch has one sealed version-owned runtime adapter.  The
            # explicit launch factories remain in the public API for callers
            # and for Code's request-scoped tool context, but they are not the
            # semantic owner of a DeepResearch version.
            drive_state_factory = (
                adapter.state_factory if workflow_name == "deep_research" else state_factory
            )
            drive_context_factory = (
                adapter.context_factory if workflow_name == "deep_research" else context_factory
            )
            task = asyncio.create_task(
                self._drive(
                    run_id=accepted["run_id"],
                    start_payload=dict(start_payload),
                    # The accepted request may carry a more specific live
                    # context (notably Code tool exposure).  Durable recovery
                    # always returns to the sealed registry adapter.
                    state_factory=drive_state_factory,
                    context_factory=drive_context_factory,
                    targets=targets,
                ),
                name=f"workflow:{workflow_name}:{accepted['run_id']}",
            )
            self._track_run_task(str(accepted["run_id"]), task)
        return {
            "ok": True,
            "accepted": True,
            "completion_semantics": "accepted_async",
            "run_id": accepted["run_id"],
            "request_id": request_id,
            "turn_id": turn_id,
            "workflow_name": workflow_name,
            "workflow_version": workflow_version,
            "accepted_event_id": accepted["accepted_event_id"],
        }

    async def launch_precreated(
        self,
        *,
        workflow_name: str,
        workflow_version: str,
        session_id: str,
        request_id: str,
        turn_id: str,
        start_payload: Mapping[str, JsonValue],
        capability_snapshot: Mapping[str, JsonValue],
        state_factory: StateFactory,
        context_factory: ContextFactory,
        venue: str = "chat",
        code_session_id: str | None = None,
        delivery_session_id: str | None = None,
        base_epoch: int = 0,
        code_epoch: int = 0,
        logical_slot: str = "accepted_async:0",
        delivery_targets: Sequence[tuple[str, str]] | None = None,
        principal_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Exercise generic ownership without changing the production launch path."""

        if self.execution_ports is None:
            raise RuntimeError("launch_precreated requires explicit WorkflowExecutionPorts")
        adapter = self._resolve_launch_adapter(
            workflow_name,
            workflow_version,
            state_factory=state_factory,
            context_factory=context_factory,
        )
        resolved_delivery_session_id = delivery_session_id or session_id
        targets = tuple(
            delivery_targets
            or (
                ("session_message", resolved_delivery_session_id),
                ("websocket", resolved_delivery_session_id),
            )
        )
        prepared = self.service.prepare_start(
            venue=venue,
            base_session_id=session_id,
            code_session_id=code_session_id,
            delivery_session_id=resolved_delivery_session_id,
            request_id=request_id,
            turn_id=turn_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            capability_snapshot=capability_snapshot,
            start_payload=start_payload,
            base_epoch=base_epoch,
            code_epoch=code_epoch,
            logical_slot=logical_slot,
            delivery_targets=(*targets, ("receipt", resolved_delivery_session_id)),
            run_id=run_id,
            trace_id=trace_id,
        )
        spec = self.service.execution_spec(prepared, principal_id=principal_id)
        accepted = await self.service.start_prepared(
            prepared, spec, execution_ports=self.execution_ports
        )
        if accepted["created"]:
            drive_state_factory = (
                adapter.state_factory if workflow_name == "deep_research" else state_factory
            )
            drive_context_factory = (
                adapter.context_factory if workflow_name == "deep_research" else context_factory
            )
            task = asyncio.create_task(
                self._drive(
                    run_id=str(accepted["run_id"]),
                    start_payload=dict(start_payload),
                    state_factory=drive_state_factory,
                    context_factory=drive_context_factory,
                    targets=targets,
                    precreated=True,
                ),
                name=f"workflow:precreated:{workflow_name}:{accepted['run_id']}",
            )
            self._track_run_task(str(accepted["run_id"]), task)
        return {
            "ok": True,
            "accepted": True,
            "completion_semantics": "accepted_async",
            "run_id": accepted["run_id"],
            "request_id": request_id,
            "turn_id": turn_id,
            "workflow_name": workflow_name,
            "workflow_version": workflow_version,
            "accepted_event_id": accepted["accepted_event_id"],
        }

    async def cancel_precreated(self, run_id: str, reason: str = "user") -> dict[str, Any]:
        if self.execution_ports is None:
            raise RuntimeError("cancel_precreated requires explicit WorkflowExecutionPorts")
        row = await self.service.cancel_precreated(str(run_id), reason)
        self.notify_dispatcher()
        return row

    async def resume_precreated(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
    ) -> Any:
        """Resume only a generic execution-owned workflow interrupt."""

        if self.execution_ports is None:
            raise RuntimeError("resume_precreated requires explicit WorkflowExecutionPorts")
        if not await self._is_precreated(run_id):
            raise RuntimeError("resume_precreated rejects a legacy workflow run")
        row = await self.service.run_store.get_run(str(run_id))
        if row is None:
            raise KeyError(run_id)
        adapter = self.runtime_adapters.get(
            str(row["workflow_name"]), str(row["workflow_version"])
        )
        if adapter is None:
            raise RuntimeError("workflow adapter is unavailable for generic resume")
        snapshot = await self.service.run_store.get_capability_snapshot(str(run_id))
        metadata = snapshot.get("_workflow_start", {}) if isinstance(snapshot, Mapping) else {}
        start_payload = metadata.get("start_payload", {}) if isinstance(metadata, Mapping) else {}
        if not isinstance(start_payload, Mapping):
            raise RuntimeError("workflow start payload is unavailable for generic resume")
        targets = await self._persisted_delivery_targets(str(run_id))
        if not targets:
            raise RuntimeError("workflow delivery session is unavailable for generic resume")
        context = await self._build_context(adapter.context_factory, row, dict(start_payload))
        result = await self.service.resume_precreated(
            str(run_id),
            dict(responses),
            self._with_progress(context, targets),
        )
        await self.recover_due_deliveries(run_id=str(run_id))
        return result

    async def recover_pending(self, *, only_run_ids: set[str] | None = None, recovery_lease: RecoveryLease | None = None) -> list[str]:
        """Recreate background drivers from persisted start snapshots."""

        self._require_runtime_active()
        recovered: list[str] = []
        for row in await self.service.run_store.list_runs(limit=_RUN_SCAN_LIMIT):
            run_id = str(row["run_id"])
            if only_run_ids is not None and run_id not in only_run_ids:
                continue
            if run_id in self._scheduled_run_ids:
                continue
            if str(row["status"]) not in {"created", "retryable"}:
                continue
            key = (str(row["workflow_name"]), str(row["workflow_version"]))
            adapter = self.runtime_adapters.get(*key)
            if adapter is None:
                continue
            snapshot = await self.service.run_store.get_capability_snapshot(str(row["run_id"]))
            metadata = snapshot.get("_workflow_start", {}) if isinstance(snapshot, Mapping) else {}
            start_payload = metadata.get("start_payload", {}) if isinstance(metadata, Mapping) else {}
            if not isinstance(start_payload, Mapping):
                continue
            state_factory, context_factory = adapter.state_factory, adapter.context_factory
            targets = await self._persisted_delivery_targets(run_id)
            if not targets:
                continue
            active_lease = None
            if recovery_lease is not None:
                active_lease = await self.service.runner.claim_execution_recovery(
                    run_id, recovery_lease
                )
            task = asyncio.create_task(
                self._drive(
                    run_id=run_id,
                    start_payload=dict(start_payload),
                    state_factory=state_factory,
                    context_factory=context_factory,
                    targets=targets,
                    resume_from_checkpoint=bool(row.get("head_checkpoint_id")),
                    precreated=await self._is_precreated(run_id),
                    active_lease=active_lease,
                ),
                name=f"workflow:recover:{run_id}",
            )
            self._track_run_task(run_id, task)
            recovered.append(run_id)
        return recovered

    async def recover_due_deliveries(
        self,
        *,
        run_id: str | None = None,
        now: float | None = None,
        recover_claimed: bool = False,
    ) -> list[str]:
        """Redeliver unfinished outbox events without recreating them."""

        async with self._delivery_dispatch_lock:
            return await self._recover_due_deliveries(
                run_id=run_id,
                now=now,
                recover_claimed=recover_claimed,
            )

    async def recover_open_decision_events(self) -> list[str]:
        """Backfill actionable decision cards created before durable decision events."""

        event_ids: list[str] = []
        for decision in await self.service.human_store.list_open_decisions(limit=1000):
            row = await self.service.run_store.get_run(decision.run_id)
            targets = await self._persisted_delivery_targets(decision.run_id)
            if row is None or str(row.get("status") or "") != "waiting" or not targets:
                continue
            event = await self.service.outbox.ensure_event(
                run_id=decision.run_id,
                event_key=f"decision:{decision.decision_id}:open:v{decision.version}:projection:v2",
                event_type="workflow.decision",
                payload={
                    "kind": "decision",
                    "status": "open",
                    "decision_id": decision.decision_id,
                    "decision_kind": decision.kind,
                    "nonce": decision.nonce,
                    "version": decision.version,
                    "prompt": decision.prompt,
                    "run_id": decision.run_id,
                    "request_id": row.get("request_id"),
                    "turn_id": row.get("turn_id"),
                    "workflow_name": row.get("workflow_name"),
                    "workflow_version": row.get("workflow_version"),
                },
                deliveries=targets,
            )
            event_ids.append(str(event["event_id"]))
        if event_ids:
            await self.recover_due_deliveries()
        return event_ids

    async def _recover_due_deliveries(
        self,
        *,
        run_id: str | None,
        now: float | None,
        recover_claimed: bool = False,
    ) -> list[str]:

        current_time = time.time() if now is None else float(now)
        if recover_claimed:
            cursor: str | None = None
            while True:
                page = await self.service.outbox.list_deliveries(
                    run_id=run_id,
                    status="delivering",
                    cursor=cursor,
                    limit=_DELIVERY_PAGE_SIZE,
                )
                for delivery in page.get("items", ()):
                    if delivery.get("manifest_ref") is not None:
                        claim_expires_at = delivery.get("claim_expires_at")
                        if claim_expires_at is None or float(claim_expires_at) > current_time:
                            continue
                        await self.service.outbox.mutate_delivery(
                            str(delivery["delivery_id"]),
                            action="failed",
                            expected_version=int(delivery["version"]),
                            reason="claim_expired",
                        )
                    else:
                        await self.service.outbox.retry_delivery(
                            str(delivery["delivery_id"]),
                            expected_version=int(delivery["version"]),
                            reason="startup_recover_orphaned_claim",
                        )
                cursor = page.get("next_cursor")
                if not cursor:
                    break
        event_ids: list[str] = []
        seen: set[str] = set()
        next_deadline: float | None = None
        for status in ("pending", "failed"):
            cursor: str | None = None
            while True:
                page = await self.service.outbox.list_deliveries(
                    run_id=run_id,
                    status=status,
                    cursor=cursor,
                    limit=_DELIVERY_PAGE_SIZE,
                )
                for delivery in page.get("items", ()):
                    due_at = delivery.get("next_attempt_at")
                    if (
                        delivery.get("manifest_ref") is not None
                        and status == "failed"
                        and (due_at is None or int(delivery["attempts"]) >= 5)
                    ):
                        continue
                    if status == "failed" and due_at is not None and float(due_at) > current_time:
                        deadline = float(due_at)
                        next_deadline = (
                            deadline if next_deadline is None else min(next_deadline, deadline)
                        )
                        continue
                    event_id = str(delivery["event_id"])
                    if event_id not in seen:
                        seen.add(event_id)
                        event_ids.append(event_id)
                cursor = page.get("next_cursor")
                if not cursor:
                    break

        for event_id in event_ids:
            await self.service.deliver_event_once(event_id)
        self._next_deadline = next_deadline
        return event_ids

    async def dispatch_due_work(self) -> list[str]:
        """Recover leases, due retries, and resolved decisions after crashes."""

        await self.service.runner.recover_expired()
        now = time.time()
        due_run_ids: set[str] = set()
        waiting_rows: list[Mapping[str, Any]] = []
        next_run_deadline: float | None = None
        for row in await self.service.run_store.list_runs(limit=_RUN_SCAN_LIMIT):
            run_id = str(row["run_id"])
            status = str(row["status"])
            if status == "created":
                due_run_ids.add(run_id)
            elif status == "retryable":
                due_at = await self.service.run_store.next_retry_at(run_id)
                if due_at is None or due_at <= now:
                    due_run_ids.add(run_id)
                else:
                    next_run_deadline = (
                        due_at if next_run_deadline is None else min(next_run_deadline, due_at)
                    )
            elif status == "waiting" and run_id not in self._scheduled_run_ids:
                waiting_rows.append(row)

        dispatched = await self.recover_pending(only_run_ids=due_run_ids)
        for row in waiting_rows:
            run_id = str(row["run_id"])
            responses = await self.service.human_store.build_run_resume_payload(run_id)
            if not responses:
                continue
            task = asyncio.create_task(
                self.resume_run(run_id, responses),
                name=f"workflow:decision-recover:{run_id}",
            )
            self._track_run_task(run_id, task)
            dispatched.append(run_id)
        await self.recover_due_deliveries(now=now)
        deadlines = tuple(
            deadline
            for deadline in (next_run_deadline, self._next_deadline)
            if deadline is not None
        )
        self._next_deadline = min(deadlines) if deadlines else None
        return dispatched

    def notify_dispatcher(self) -> None:
        """Wake the dispatcher after new local workflow or outbox work appears."""

        self._dispatcher_wakeup.set()

    def _track_run_task(self, run_id: str, task: asyncio.Task[Any]) -> None:
        """Keep one authoritative driver chain per run until its latest task ends."""

        self._tasks.add(task)
        self._scheduled_run_ids.add(run_id)
        self._run_tasks[run_id] = task

        def _done(done: asyncio.Task[Any], *, rid: str = run_id) -> None:
            self._tasks.discard(done)
            if self._run_tasks.get(rid) is done:
                self._run_tasks.pop(rid, None)
                self._scheduled_run_ids.discard(rid)
                self.notify_dispatcher()

        task.add_done_callback(_done)

    async def schedule_resume(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
    ) -> dict[str, Any]:
        """Accept a durable decision immediately and resume in a tracked task."""

        predecessor = self._run_tasks.get(run_id)
        if predecessor is not None and not predecessor.done():
            name = predecessor.get_name()
            if name.startswith(
                (
                    "workflow:recover:",
                    "workflow:decision-recover:",
                    "workflow:resume:",
                )
            ):
                return {
                    "accepted": True,
                    "created": False,
                    "completion_semantics": "accepted_async",
                    "run_id": run_id,
                }

        async def _resume_after_predecessor() -> WorkflowRunResult | None:
            try:
                if predecessor is not None and not predecessor.done():
                    await asyncio.gather(
                        asyncio.shield(predecessor), return_exceptions=True
                    )
                return await self.resume_run(run_id, responses)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - publish a durable terminal error
                targets = await self._persisted_delivery_targets(run_id)
                if targets:
                    await self._publish_error(run_id, type(exc).__name__, targets)
                return None

        task = asyncio.create_task(
            _resume_after_predecessor(),
            name=f"workflow:resume:{run_id}",
        )
        self._track_run_task(run_id, task)
        return {
            "accepted": True,
            "created": True,
            "completion_semantics": "accepted_async",
            "run_id": run_id,
        }

    def start_dispatcher(
        self, *, interval_seconds: float = _DISPATCHER_SAFETY_INTERVAL
    ) -> None:
        if self._dispatcher_task is not None and not self._dispatcher_task.done():
            return
        self._dispatcher_interval = max(0.1, float(interval_seconds))
        self._dispatcher_task = asyncio.create_task(
            self._dispatcher_loop(), name="workflow:due-dispatcher"
        )

    async def _dispatcher_loop(self) -> None:
        while True:
            self._dispatcher_wakeup.clear()
            try:
                await self.dispatch_due_work()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            timeout = self._dispatcher_interval
            if self._next_deadline is not None:
                timeout = min(timeout, max(0.0, self._next_deadline - time.time()))
            try:
                await asyncio.wait_for(self._dispatcher_wakeup.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def resume_run(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
    ) -> WorkflowRunResult:
        """Resume a persisted interrupt with the production adapter context."""

        row = await self.service.run_store.get_run(run_id)
        if row is None:
            raise KeyError(run_id)
        self._require_runtime_active()
        adapter = self.runtime_adapters.get(
            str(row["workflow_name"]), str(row["workflow_version"])
        )
        if adapter is None:
            raise RuntimeError("workflow adapter is unavailable for resume")
        snapshot = await self.service.run_store.get_capability_snapshot(run_id)
        metadata = snapshot.get("_workflow_start", {}) if isinstance(snapshot, Mapping) else {}
        start_payload = metadata.get("start_payload", {}) if isinstance(metadata, Mapping) else {}
        if not isinstance(start_payload, Mapping):
            raise RuntimeError("workflow start payload is unavailable for resume")
        context_factory = adapter.context_factory
        targets = await self._persisted_delivery_targets(run_id)
        if not targets:
            raise RuntimeError("workflow delivery session is unavailable for resume")
        context = await self._build_context(context_factory, row, dict(start_payload))
        context = self._with_progress(context, targets)
        result = await self.service.runner.resume(run_id, dict(responses), context)
        if result.status in TERMINAL_RUN_STATUSES or result.status.value == "waiting":
            if result.status.value == "completed":
                persist_snapshot = getattr(
                    self.service, "persist_v6_continuation_snapshot", None
                )
                if callable(persist_snapshot):
                    await persist_snapshot(run_id)
            await self.recover_due_deliveries(run_id=run_id)
            self.notify_dispatcher()
        return result

    @staticmethod
    async def _build_context(
        context_factory: ContextFactory,
        row: Mapping[str, Any],
        start_payload: dict[str, JsonValue],
    ) -> WorkflowContext:
        parameters = inspect.signature(context_factory).parameters
        context = (
            context_factory(row, start_payload)
            if "row" in parameters and "start_payload" in parameters
            else context_factory()
        )
        if inspect.isawaitable(context):
            context = await context
        return context

    async def _persisted_delivery_targets(
        self, run_id: str
    ) -> tuple[tuple[str, str], ...]:
        ref = await self.service.run_store.get_session_ref(run_id, "delivery")
        if ref is None or ref.get("deleted_at") is not None:
            return ()
        session_id = str(ref.get("session_id") or "").strip()
        if not session_id:
            return ()
        return (("session_message", session_id), ("websocket", session_id))

    def _with_progress(
        self,
        context: WorkflowContext,
        targets: Sequence[tuple[str, str]],
    ) -> WorkflowContext:
        return replace(
            context,
            ports={
                **context.ports,
                "progress": WorkflowProgressReporter(
                    self.service, targets, notify_dispatcher=self.notify_dispatcher
                ),
            },
        )

    async def _is_precreated(self, run_id: str) -> bool:
        if self.execution_ports is None:
            return False
        await self.service.run_store.initialize()
        db = await self.service.run_store._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT 1 FROM execution_runs WHERE run_id=?", (str(run_id),)
                )
            ).fetchone()
            return row is not None
        finally:
            await db.close()

    async def _drive(
        self,
        *,
        run_id: str,
        start_payload: dict[str, JsonValue],
        state_factory: StateFactory,
        context_factory: ContextFactory,
        targets: Sequence[tuple[str, str]],
        resume_from_checkpoint: bool = False,
        precreated: bool = False,
        active_lease: Any | None = None,
    ) -> None:
        try:
            row = await self.service.run_store.get_run(run_id)
            if row is None:
                return
            state = None
            if not resume_from_checkpoint:
                state = state_factory(
                    run_id=run_id,
                    thread_id=str(row["thread_id"]),
                    session_id=str(row["session_id"]),
                    **start_payload,
                )
                # A continuation's canonical start payload intentionally carries
                # only the parent id and semantic snapshot hash.  Its versioned
                # state factory may therefore hydrate the server-owned snapshot
                # asynchronously before the first checkpoint.
                if inspect.isawaitable(state):
                    state = await state
            context = await self._build_context(context_factory, row, start_payload)
            context = self._with_progress(context, targets)
            responses = (
                await self.service.human_store.build_run_resume_payload(run_id)
                if resume_from_checkpoint and hasattr(self.service, "human_store")
                else {}
            )
            if precreated:
                result = (
                    await self.service.resume_precreated(
                        run_id, responses, context, active_lease=active_lease)
                    if responses
                    else await self.service.run_precreated(
                        run_id, state, context, active_lease=active_lease)
                )
            else:
                result = (
                    await self.service.runner.resume(run_id, responses, context)
                    if responses
                    else await self.service.runner.run(run_id, state, context)
                )
            if result.status in TERMINAL_RUN_STATUSES or result.status.value == "waiting":
                if result.status.value == "completed":
                    persist_snapshot = getattr(
                        self.service, "persist_v6_continuation_snapshot", None
                    )
                    if callable(persist_snapshot):
                        await persist_snapshot(run_id)
                await self.recover_due_deliveries(run_id=run_id)
                self.notify_dispatcher()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - terminal event is mandatory
            await self._publish_error(
                run_id, type(exc).__name__, targets, precreated=precreated
            )

    async def _publish_error(
        self,
        run_id: str,
        error_type: str,
        targets: Sequence[tuple[str, str]],
        *,
        precreated: bool = False,
    ) -> None:
        result = await self.service.runner.saver.commit_launch_failure(
            run_id,
            error={"code": "launcher_error", "type": error_type},
            recovery_action="inspect_or_cancel",
        )
        if not precreated:
            for event_id in result["event_ids"]:
                await self.service.deliver_event_once(str(event_id))

    async def shutdown(self) -> None:
        if self._dispatcher_task is not None:
            self._dispatcher_task.cancel()
            await asyncio.gather(self._dispatcher_task, return_exceptions=True)
            self._dispatcher_task = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["WorkflowLauncher"]
