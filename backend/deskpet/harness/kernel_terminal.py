"""Terminal lifecycle collaborator inherited by the single RunKernel owner."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Mapping
from dataclasses import replace

from deskpet.execution.contracts import (
    LiveCursor,
    OutcomeStatus,
    PersistenceLevel,
    RecoveryLease,
    RunEvent,
    RunEventCandidate,
    RunNotFound,
    RunRecord,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TerminalConflict,
    VersionConflict,
    stable_event_id,
    thaw_json,
)
from deskpet.execution.fences import release_run_execution_fence
from deskpet.execution.run_block_signals import (
    RootBlockReasonV1,
    RunBlockReporter,
    block_signal_for_terminal,
)

from .contracts import HostExtensionRefV1
from .start_snapshot import terminal_deliveries_from_snapshot

logger = logging.getLogger(__name__)


class KernelTerminalLifecycle:
    """Private terminal mechanics; RunKernel remains the only lifecycle owner."""

    async def _notify_terminal(self, record: RunRecord, event: RunEvent) -> None:
        observer = self._terminal_observer
        if observer is None:
            return
        try:
            value = observer(record, event)
            if inspect.isawaitable(value):
                await value
        except Exception:
            logger.exception(
                "terminal observer failed after run commit",
                extra={"run_id": record.run_id},
            )

    async def _drain_active(self, timeout: float) -> bool:
        async with self._lock:
            tasks = tuple(
                active.task
                for active in self._live.values()
                if active.task is not None and not active.task.done()
            )
        if not tasks:
            return True
        _, pending = await asyncio.wait(tasks, timeout=max(0.0, timeout))
        if not pending:
            return True
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return False

    @staticmethod
    def _ephemeral_record(spec):
        now = time.time()
        return RunRecord(
            spec=spec,
            status=spec.status,
            persistence_level=PersistenceLevel.EPHEMERAL,
            version=0,
            durable_seq=0,
            terminal_event_id=None,
            created_at=now,
            updated_at=now,
            started_at=now if spec.status is RunStatus.RUNNING else None,
        )

    async def _finalize(
        self,
        record: RunRecord,
        *,
        expected_version: int,
        status: RunStatus,
        event: RunEventCandidate,
        recovery_lease: RecoveryLease | None = None,
    ) -> RunEvent:
        if record.persistence_level is PersistenceLevel.DURABLE:
            command = (
                None
                if record.context.parent_run_id is None
                else await self._uow.get_child_command_for_run(record.run_id)
            )
            finalize = (
                self._uow.finalize_child_and_enqueue_parent_signal
                if command is not None
                else self._uow.commit_run_outcome
            )
            kwargs = dict(
                expected_version=expected_version,
                terminal_status=status,
                event=event,
                recovery_lease=recovery_lease,
            )
            active = self._live.get(record.run_id)
            prepared = None if active is None else active.prepared_context
            terminal_extensions = list(
                () if prepared is None else prepared.terminal_commit_extensions
            )
            external_block = event.correlation.get("run_block_signal")
            if external_block is not None:
                if (
                    command is not None
                    or status is not RunStatus.FAILED
                    or not isinstance(external_block, Mapping)
                    or external_block.get("reason_code")
                    != RootBlockReasonV1.EXTERNAL_DEPENDENCY_UNAVAILABLE.value
                    or external_block.get("retry_exhausted") is not True
                    or external_block.get("replacement_pending") is not False
                ):
                    raise TerminalConflict(
                        "invalid_external_dependency_block",
                        "external dependency block evidence is incomplete",
                    )
                raw_refs = external_block.get("evidence_refs")
                if (
                    isinstance(raw_refs, (str, bytes))
                    or not isinstance(raw_refs, (list, tuple))
                    or not raw_refs
                ):
                    raise TerminalConflict(
                        "invalid_external_dependency_block",
                        "external dependency block requires evidence refs",
                    )
                terminal_extensions.append(
                    RunBlockReporter(
                        block_signal_for_terminal(
                            root_run_id=record.run_id,
                            event_key=event.event_key,
                            reason_code=RootBlockReasonV1.EXTERNAL_DEPENDENCY_UNAVAILABLE,
                            evidence_refs=tuple(str(item) for item in raw_refs),
                            created_at=record.created_at,
                        )
                    )
                )
            if terminal_extensions:
                kwargs["terminal_commit_extensions"] = tuple(terminal_extensions)
            if command is None:
                kwargs["deliveries"] = await self._terminal_deliveries(record)
            else:
                kwargs["value"] = thaw_json(event.payload)
            execution_fence = await self._acquire_run_execution_fence(record.run_id)
            try:
                terminal_fence = getattr(execution_fence, "terminal_commit_fence", None)
                if (
                    command is None
                    and kwargs.get("deliveries")
                    and terminal_fence is not None
                ):
                    kwargs["delivery_fence"] = terminal_fence.to_uow_fence()
                    kwargs["release_receipt_kind"] = terminal_fence.release_receipt_kind
                result = await finalize(
                    command.operation_id if command is not None else record.run_id,
                    **kwargs,
                )
            finally:
                await release_run_execution_fence(execution_fence)
            async with self._lock:
                self._live.adopt(result.record)
            await self._notify_terminal(result.record, result.event)
            if command is None:
                await self._cleanup_after_terminal_commit(result.record, result.event)
            return result.event
        async with self._lock:
            active = self._live.get(record.run_id)
            current = active.record if active is not None else None
            if current is None:
                raise RunNotFound(
                    "run_not_found", f"live run does not exist: {record.run_id}"
                )
            if current.status in TERMINAL_RUN_STATUSES:
                existing = next(
                    (
                        item
                        for item in active.events
                        if item.event_id == current.terminal_event_id
                    ),
                    None,
                )
                if (
                    current.status is status
                    and existing is not None
                    and existing.candidate == event
                ):
                    return existing
                raise TerminalConflict(
                    "terminal_conflict", "another terminal intent already won"
                )
            if current.version != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {current.version}",
                )
            now = time.time()
            terminal = RunEvent(
                event_id=stable_event_id(record.run_id, event.event_key),
                run_id=record.run_id,
                root_run_id=record.context.root_run_id,
                session_id=record.context.session_id,
                durable_seq=None,
                live_cursor=LiveCursor(
                    f"kernel:{record.run_id}", len(active.events) + 1
                ),
                candidate=event,
                created_at=now,
            )
            active.record = replace(
                current,
                status=status,
                version=current.version + 1,
                terminal_event_id=terminal.event_id,
                updated_at=now,
                ended_at=now,
            )
            finalized_record = active.record
        await self._notify_terminal(finalized_record, terminal)
        await self._cleanup_after_terminal_commit(finalized_record, terminal)
        return terminal

    async def _cleanup_after_terminal_commit(
        self, record: RunRecord, event: RunEvent
    ) -> None:
        active = self._live.get(record.run_id)
        prepared = None if active is None else active.prepared_context
        if prepared is None or not prepared.after_terminal_commit_cleanup:
            return
        reader = getattr(self._uow, "read_terminal_extension_receipts", None)
        receipts: tuple[HostExtensionRefV1, ...] = ()
        if callable(reader) and record.persistence_level is PersistenceLevel.DURABLE:
            raw = await reader(record.run_id, event.event_id)
            receipts = tuple(
                item
                if isinstance(item, HostExtensionRefV1)
                else HostExtensionRefV1(
                    kind=str(item["kind"] if isinstance(item, Mapping) else item.kind),
                    ref=str(item["ref"] if isinstance(item, Mapping) else item.ref),
                    content_hash=str(
                        item["content_hash"]
                        if isinstance(item, Mapping)
                        else item.content_hash
                    ),
                )
                for item in raw
            )
        for cleanup in prepared.after_terminal_commit_cleanup:
            try:
                await cleanup.cleanup_after_terminal(
                    record, event, extension_receipts=receipts
                )
            except Exception:
                logger.exception(
                    "terminal cleanup failed after authoritative commit",
                    extra={
                        "run_id": record.run_id,
                        "extension_kind": cleanup.descriptor.kind,
                    },
                )

    async def _terminal_deliveries(self, record: RunRecord):
        frozen = ()
        active = self._live.get(record.run_id)
        prepared = None if active is None else active.prepared_context
        if prepared is not None:
            frozen = prepared.frozen_terminal_deliveries
        elif record.persistence_level is PersistenceLevel.DURABLE:
            snapshot = await self._uow.read_run_start_snapshot(record.run_id)
            if snapshot is not None:
                frozen = terminal_deliveries_from_snapshot(snapshot)
        projected = (
            ()
            if self._terminal_projection is None
            else self._terminal_projection.deliveries(
                record, await self._uow.list_events(record.run_id)
            )
        )
        merged = {
            (item.sink_kind, item.sink_instance, item.target_id, item.policy.value): item
            for item in (*frozen, *projected)
        }
        return tuple(merged[key] for key in sorted(merged))


__all__ = ["KernelTerminalLifecycle"]
