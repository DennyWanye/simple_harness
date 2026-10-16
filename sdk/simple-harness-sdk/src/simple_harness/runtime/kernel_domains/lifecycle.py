# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Start, recovery, reconciliation, diagnostics and shutdown."""

from __future__ import annotations

import asyncio
import time
from typing import Self

from simple_harness.contracts import RunId
from simple_harness.execution.uow import RunState, UnitOfWorkConflict
from simple_harness.observability import Outcome

from ..orchestration import RuntimeActivationClaim
from .core import RuntimeCore
from .support import (
    BASE_AGENT_DRIVER_KIND,
    WORKFLOW_DRIVER_KIND,
    RuntimeLifecycleState,
    _run_id,
    logger,
)


class RuntimeLifecycleMixin(RuntimeCore):
    """Start, recovery, reconciliation, diagnostics and shutdown."""

    __slots__ = ()

    def diagnostics_snapshot(self) -> dict[str, object]:
        """Return the stable observability snapshot base without business payloads."""

        if self._observability is None:
            return {
                "schema_version": 1,
                "sdk_version": "unconfigured",
                "lifecycle": "disabled",
                "health": "healthy",
                "queue_depth": 0,
                "queue_capacity": 0,
                "counters": {},
                "active_runs": len(self._live.active_run_ids()),
                "authorities": self._authority_diagnostics_snapshot(),
            }
        snapshot = getattr(self._observability, "diagnostics_snapshot", None)
        if not callable(snapshot):
            return {
                "schema_version": 1,
                "sdk_version": "unknown",
                "lifecycle": "degraded",
                "health": "degraded",
                "queue_depth": 0,
                "queue_capacity": 0,
                "counters": {},
                "active_runs": len(self._live.active_run_ids()),
                "authorities": self._degraded_authority_snapshot(
                    "observability_snapshot_unavailable"
                ),
            }
        try:
            value = snapshot()
            result = dict(value)
            result["active_runs"] = len(self._live.active_run_ids())
            result["authorities"] = self._authority_diagnostics_snapshot()
            return result
        except BaseException:
            logger.debug("diagnostics_snapshot_failed", exc_info=True)
            return {
                "schema_version": 1,
                "sdk_version": "unknown",
                "lifecycle": "degraded",
                "health": "degraded",
                "queue_depth": 0,
                "queue_capacity": 0,
                "counters": {},
                "active_runs": 0,
                "authorities": self._degraded_authority_snapshot("snapshot_failed"),
            }

    @staticmethod
    def _degraded_authority_snapshot(error_code: str) -> dict[str, object]:
        empty: dict[str, object] = {
            "health": "degraded",
            "counts": {},
            "oldest_age_ms": None,
        }
        return {
            "health": "degraded",
            "error_code": error_code,
            "commands": dict(empty),
            "context": dict(empty),
            "outbox": dict(empty),
            "recovery": dict(empty),
            "recent_error_codes": {},
        }

    def _authority_diagnostics_snapshot(self) -> dict[str, object]:
        staging = self._ports.context_staging
        owner = self._uow.transaction_owner
        try:
            database_connection = getattr(owner, "connection", None)
        except BaseException:
            logger.debug("authority_diagnostics_snapshot_getattr_failed", exc_info=True)
            return self._degraded_authority_snapshot("authority_query_failed")
        command_rows = (
            []
            if database_connection is None
            else database_connection.execute(
                "SELECT state,COUNT(*),MIN(created_at) FROM conversation_commands GROUP BY state"
            ).fetchall()
        )
        command_counts = {str(row[0]): int(row[1]) for row in command_rows[:16]}
        command_oldest = min(
            (float(row[2]) for row in command_rows[:16] if row[2] is not None),
            default=None,
        )
        commands = {
            "health": "healthy",
            "counts": command_counts,
            "oldest_age_ms": (
                None
                if command_oldest is None
                else max(0, int((self._now() - command_oldest) * 1000))
            ),
        }
        if staging is None:
            return {
                "health": "healthy",
                "commands": commands,
                "context": {"health": "disabled", "counts": {}, "oldest_age_ms": None},
                "outbox": {"health": "disabled", "counts": {}, "oldest_age_ms": None},
                "recovery": {"health": "disabled", "counts": {}, "oldest_age_ms": None},
                "recent_error_codes": {},
            }
        started = time.monotonic()
        try:
            connection = staging.database.connection
            now = self._now()

            def group(table: str, state: str, created: str) -> dict[str, object]:
                rows = connection.execute(
                    f"SELECT {state},COUNT(*),MIN({created}) FROM {table} GROUP BY {state}"
                ).fetchall()
                counts = {str(row[0]): int(row[1]) for row in rows[:16]}
                oldest = min(
                    (float(row[2]) for row in rows[:16] if row[2] is not None),
                    default=None,
                )
                return {
                    "health": "healthy",
                    "counts": counts,
                    "oldest_age_ms": (
                        None if oldest is None else max(0, int((now - oldest) * 1000))
                    ),
                }

            context = group("context_preparation_staging", "state", "created_at")
            outbox = group("memory_outbox", "state", "created_at")
            recovery_rows = connection.execute(
                "SELECT CASE WHEN resolved_at IS NULL THEN 'observed' "
                "WHEN wake_consumed=0 THEN 'resolved' ELSE 'consumed' END AS status,"
                "COUNT(*),MIN(created_at) FROM run_wait_blockers GROUP BY status"
            ).fetchall()
            recovery_counts = {str(row[0]): int(row[1]) for row in recovery_rows[:8]}
            recovery_oldest = min(
                (float(row[2]) for row in recovery_rows[:8] if row[2] is not None),
                default=None,
            )
            errors = connection.execute(
                "SELECT error_code,COUNT(*) FROM ("
                "SELECT error_code FROM memory_outbox WHERE error_code IS NOT NULL UNION ALL "
                "SELECT error_code FROM context_preparation_staging WHERE error_code IS NOT NULL"
                ") GROUP BY error_code ORDER BY COUNT(*) DESC,error_code LIMIT 20"
            ).fetchall()
            if time.monotonic() - started > 0.25:
                return self._degraded_authority_snapshot("snapshot_deadline")
            return {
                "health": "healthy",
                "commands": commands,
                "context": context,
                "outbox": outbox,
                "recovery": {
                    "health": "healthy",
                    "counts": recovery_counts,
                    "oldest_age_ms": (
                        None
                        if recovery_oldest is None
                        else max(0, int((now - recovery_oldest) * 1000))
                    ),
                },
                "recent_error_codes": {str(row[0]): int(row[1]) for row in errors},
            }
        except BaseException:
            logger.debug("authority_diagnostics_snapshot_failed", exc_info=True)
            return self._degraded_authority_snapshot("authority_query_failed")

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._state is RuntimeLifecycleState.READY:
                return
            if self._state in {
                RuntimeLifecycleState.CLOSING,
                RuntimeLifecycleState.CLOSED,
                RuntimeLifecycleState.FAILED,
            }:
                raise RuntimeError(f"Runtime cannot start from {self._state.value}")
            if self._state is RuntimeLifecycleState.NEW:
                self._state = RuntimeLifecycleState.STARTING
                self._startup_task = asyncio.create_task(
                    self._start_once(), name="simple-harness-startup"
                )
            startup = self._startup_task
        assert startup is not None
        await asyncio.shield(startup)

    async def _start_once(self) -> None:
        try:
            validate_context_use = getattr(self._uow, "validate_context_use_recovery", None)
            if validate_context_use is not None:
                validate_context_use()
            await self._ports.reconciliation.reconcile()
            await self._drain_commands_bounded(100)
            await self.recover(_startup=True)
            await self._drain_resolved_waits_once()
            await self._drain_deliveries_bounded(100)
            await self._drain_memory_bounded(100)
            await self._drain_recall_releases(100)
            for hook in self._start_hooks:
                await hook()
                self._started_hooks += 1
            async with self._lifecycle_lock:
                if self._state is not RuntimeLifecycleState.STARTING:
                    raise asyncio.CancelledError
                self._wake_drain_task = asyncio.create_task(
                    self._wake_drain(), name="simple-harness-wake-drain"
                )
                self._command_pump_task = asyncio.create_task(
                    self._command_pump(), name="simple-harness-command-pump"
                )
                self._delivery_pump_task = asyncio.create_task(
                    self._delivery_pump(), name="simple-harness-delivery-pump"
                )
                if self._ports.memory_dispatcher is not None:
                    self._memory_pump_task = asyncio.create_task(
                        self._memory_pump(), name="simple-harness-memory-pump"
                    )
                self._started = True
                self._closing = False
                self._state = RuntimeLifecycleState.READY
        except asyncio.CancelledError:
            raise
        except BaseException:
            async with self._lifecycle_lock:
                if self._state is RuntimeLifecycleState.STARTING:
                    self._state = RuntimeLifecycleState.FAILED
            await self._stop_background_tasks()
            raise

    async def recover(self, *, _startup: bool = False) -> None:
        if not _startup:
            self._require_started()
        root_runs = self._uow.list_recoverable_root_runs()
        child_runs = self._uow.list_recoverable_child_runs()
        # BaseAgent Runs rest in WAITING between turns, so queued / running /
        # result_pending turns must be woken explicitly (finalize-first in _drive).
        recovered_ids = {run.run_id for run in (*root_runs, *child_runs)}
        for run_id in self._uow.list_runs_with_open_agent_turns():
            if run_id in recovered_ids or run_id in self._live.active_run_ids():
                continue
            await self._wake_continuation(run_id)
        logger.info(
            "reconcile.recovered",
            extra={"roots": len(root_runs), "children": len(child_runs)},
        )
        for run in (*root_runs, *child_runs):
            if run.run_id in self._live.active_run_ids():
                continue
            self._emit_transition(
                "recovery.observed_state",
                run_id=run.run_id,
                outcome=Outcome.STARTED,
                operation="recovery",
                attributes={
                    "entity_kind": "run",
                    "entity_id": run.run_id,
                    "to_state": run.state.value,
                    "replayed": True,
                    "history_complete": False,
                    "state_version": run.version,
                },
            )
            retry_on_conflict = False
            try:
                prior_ready = await self._uow.run_atomic(
                    lambda tx, run_id=run.run_id: self._uow.read_spawn_ready_activation(  # type: ignore[misc]
                        tx, run_id
                    ),
                    fault_label="runtime:spawn_ready:read",
                )
                if prior_ready is None:
                    if (
                        run.driver_kind == BASE_AGENT_DRIVER_KIND
                        and self._uow.read_pending_agent_turn(run.run_id) is not None
                    ):
                        # BaseAgent RESULT_PENDING: finalize first, never re-drive.
                        retry_on_conflict = True  # a later drive also finalizes first
                        await self._activate(run.run_id)
                        await self._finalize_pending_agent_turn(run.run_id)
                        activated = self._uow.read_run(run.run_id)
                        if activated is None:
                            raise UnitOfWorkConflict("BaseAgent Run disappeared during recovery")
                    elif (
                        run.parent_run_id is not None
                        and run.driver_kind == WORKFLOW_DRIVER_KIND
                        and self._uow.is_workflow_spawn_child(run.run_id)
                    ):
                        admission = await self._uow.run_atomic(
                            lambda tx, run_id=run.run_id: self._uow.resume_spawn_child_start(  # type: ignore[misc]
                                tx,
                                run_id,
                                RuntimeActivationClaim(
                                    self._ports.owner_id,
                                    lease_ttl_seconds=(self._ports.lease_ttl_seconds),
                                ),
                                now=self._now(),
                            ),
                            fault_label="runtime:spawn_child:resume",
                        )
                        if not self._register_recovered_spawn_child(admission):
                            continue
                        activated = self._uow.read_run(run.run_id)
                        if activated is None:
                            raise UnitOfWorkConflict("workflow spawn child Run disappeared")
                    else:
                        retry_on_conflict = True
                        activated = await self._activate(run.run_id)
                else:
                    ready_activation = await self._uow.run_atomic(
                        lambda tx, prior=prior_ready: self._uow.reclaim_spawn_ready_activation(  # type: ignore[misc]
                            tx,
                            prior,
                            self._ports.owner_id,
                            now=self._now(),
                            ttl_seconds=self._ports.lease_ttl_seconds,
                        ),
                        fault_label="runtime:spawn_ready:reclaim",
                    )
                    self._register_spawn_ready_activation(ready_activation)
                    activated = self._uow.read_run(run.run_id)
                    if activated is None:
                        raise UnitOfWorkConflict("workflow spawn parent Run disappeared")
            except UnitOfWorkConflict:
                if retry_on_conflict:
                    # The previous owner's lease has not expired yet (a quick restart
                    # after a crash).  Keep the wake so the drain loop takes the Run
                    # over once it expires; startup recovery runs only once.
                    self._pending_wakes.add(run.run_id)
                continue
            if activated.state is RunState.CANCEL_REQUESTED:
                await self._terminalize_cancelled(activated, reason="startup_recovery")
            else:
                self._schedule(activated.run_id)
            self._emit_transition(
                "recovery.resolved",
                run_id=run.run_id,
                outcome=Outcome.SUCCEEDED,
                operation="recovery",
                attributes={
                    "entity_kind": "run",
                    "entity_id": run.run_id,
                    "recovery_result": "scheduled",
                    "history_complete": False,
                    "state_version": activated.version,
                },
            )

    async def reconcile(self) -> None:
        self._require_started()
        await self._ports.reconciliation.reconcile()
        await self._drain_resolved_waits_once()
        await self.recover()

    async def close(self) -> None:
        async with self._lifecycle_lock:
            if self._state is RuntimeLifecycleState.CLOSED:
                return
            if self._state is RuntimeLifecycleState.NEW:
                self._state = RuntimeLifecycleState.CLOSED
                for hook in reversed(self._async_close_hooks):
                    await hook()
                if self._ports.memory_dispatcher is not None:
                    await self._ports.memory_dispatcher.close()
                if self._close_hook is not None:
                    self._close_hook()
                return
            self._state = RuntimeLifecycleState.CLOSING
            self._closing = True
            startup = self._startup_task
        if startup is not None and not startup.done():
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
        await self._drain_deliveries_bounded(100)
        await self._drain_memory_bounded(100)
        await self._stop_background_tasks()
        for token in self._cancels.values():
            token.cancel()
        await self._live.close(timeout_seconds=self._ports.close_timeout_seconds)
        heartbeat_tasks = tuple(task for task in self._heartbeats.values() if not task.done())
        for task in heartbeat_tasks:
            task.cancel()
        if heartbeat_tasks:
            await asyncio.gather(*heartbeat_tasks, return_exceptions=True)
        self._heartbeats.clear()
        now = self._now()
        for run_id, fence in tuple(self._fences.items()):
            try:
                await self._uow.release(fence)
            except UnitOfWorkConflict:
                pass
            self._fences.pop(run_id, None)
        for run_id, lease in tuple(self._leases.items()):
            try:
                self._uow.release_runtime_lease(lease, now=now)
            except UnitOfWorkConflict:
                pass
            self._leases.pop(run_id, None)
        self._cancels.clear()
        self._workflow_spawn_ready_activations.clear()
        self._workflow_start_dispatches.clear()
        self._workflow_recovery_work.clear()
        self._started = False
        for hook in reversed(self._async_close_hooks):
            await hook()
        self._started_hooks = 0
        if self._ports.memory_dispatcher is not None:
            await self._ports.memory_dispatcher.close()
        if self._close_hook is not None:
            self._close_hook()
        async with self._lifecycle_lock:
            self._state = RuntimeLifecycleState.CLOSED

    async def _stop_background_tasks(self) -> None:
        tasks = tuple(
            task
            for task in (
                self._wake_drain_task,
                self._command_pump_task,
                self._delivery_pump_task,
                self._memory_pump_task,
            )
            if task is not None and not task.done()
        )
        self._wake_drain_task = None
        self._command_pump_task = None
        self._delivery_pump_task = None
        self._memory_pump_task = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def wait_idle(self, run_id: RunId) -> None:
        value = _run_id(run_id)
        run = self._uow.read_run(value)
        parent_run_id = None if run is None else run.parent_run_id
        handoff = object()
        if parent_run_id is not None:
            self._child_signal_wait_handoffs[parent_run_id] = handoff
        try:
            await self._live.wait(value)
        finally:
            if parent_run_id is not None:
                asyncio.create_task(self._release_child_signal_wait_handoff(parent_run_id, handoff))

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()
