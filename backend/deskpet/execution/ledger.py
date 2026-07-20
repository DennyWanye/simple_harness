"""Single execution authority spanning bounded active state and durable storage."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Callable, Sequence

from .contracts import (
    ActiveRunCapacityExceeded,
    ActorAction,
    ActorContext,
    AuthorizationError,
    CreateRunResult,
    DeliverySpec,
    FinalizeRunResult,
    IdempotencyConflict,
    LiveCursor,
    OutcomeStatus,
    PersistenceLevel,
    PersistenceRequired,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    RunIdentityConflict,
    RunLinkSpec,
    RunRecord,
    RunRef,
    RunStatus,
    TERMINAL_RUN_STATUSES,
    TerminalConflict,
    VersionConflict,
    assert_idempotent_run_intent,
    stable_event_id,
)
from .ports import ExecutionUnitOfWork, RunView


class ExecutionLedger:
    """Own coarse lifecycle without imposing SQLite latency on short ReAct runs.

    Ephemeral rows live in one bounded index.  Crossing a durable boundary moves
    the same run id into the injected UoW and removes the active copy only after
    the durable transaction commits.
    """

    def __init__(
        self,
        durable: ExecutionUnitOfWork,
        *,
        max_active_runs: int = 4096,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_active_runs < 1:
            raise ValueError("max_active_runs must be positive")
        self._durable = durable
        self._max_active_runs = max_active_runs
        self._clock = clock
        self._lock = asyncio.Lock()
        self._active_by_id: OrderedDict[str, RunRecord] = OrderedDict()
        self._active_by_key: dict[str, str] = {}
        self._events_by_run: dict[str, dict[str, RunEvent]] = {}

    async def initialize(self) -> None:
        await self._durable.initialize()

    def _evict_one_terminal(self) -> bool:
        for run_id, record in tuple(self._active_by_id.items()):
            if record.status not in TERMINAL_RUN_STATUSES:
                continue
            self._active_by_id.pop(run_id, None)
            self._active_by_key.pop(record.spec.idempotency_key, None)
            self._events_by_run.pop(run_id, None)
            return True
        return False

    def _ensure_capacity(self) -> None:
        if len(self._active_by_id) < self._max_active_runs:
            return
        if self._evict_one_terminal():
            return
        raise ActiveRunCapacityExceeded(
            "active_run_capacity_exceeded",
            "bounded active ledger is full; caller must finish or promote a run",
        )

    def _touch(self, run_id: str) -> None:
        if run_id in self._active_by_id:
            self._active_by_id.move_to_end(run_id)

    @staticmethod
    def _new_record(spec: RunCreate, now: float) -> RunRecord:
        return RunRecord(
            spec=spec,
            status=spec.status,
            persistence_level=PersistenceLevel.EPHEMERAL,
            version=0,
            durable_seq=0,
            terminal_event_id=None,
            created_at=now,
            started_at=now if spec.status is RunStatus.RUNNING else None,
            updated_at=now,
        )

    async def create(self, spec: RunCreate) -> CreateRunResult:
        if spec.persistence_level is PersistenceLevel.DURABLE:
            return await self._durable.create(spec)
        async with self._lock:
            existing_id = self._active_by_key.get(spec.idempotency_key)
            if existing_id is not None:
                existing = self._active_by_id[existing_id]
                assert_idempotent_run_intent(existing.spec, spec)
                self._touch(existing_id)
                return CreateRunResult(existing, False)
            if spec.run_id in self._active_by_id:
                raise RunIdentityConflict(
                    "run_identity_conflict",
                    "run_id already names another idempotency intent",
                )
            self._ensure_capacity()
            record = self._new_record(spec, float(self._clock()))
            self._active_by_id[spec.run_id] = record
            self._active_by_key[spec.idempotency_key] = spec.run_id
            return CreateRunResult(record, True)

    async def promote(
        self,
        spec: RunCreate,
        *,
        expected_version: int,
    ) -> CreateRunResult:
        if spec.persistence_level is not PersistenceLevel.DURABLE:
            raise PersistenceRequired(
                "invalid_promotion", "promotion target must be durable"
            )
        async with self._lock:
            active_id = self._active_by_key.get(spec.idempotency_key)
            if active_id is None:
                return await self._durable.promote(
                    spec, expected_version=expected_version
                )
            active = self._active_by_id[active_id]
            assert_idempotent_run_intent(
                active.spec, spec, allow_persistence_upgrade=True
            )
            if active.run_id != spec.run_id:
                context = spec.context
                if context.parent_run_id is None:
                    context = replace(context, root_run_id=active.run_id)
                spec = replace(spec, run_id=active.run_id, context=context)
            if active.version != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {active.version}",
                )
            if active.status in TERMINAL_RUN_STATUSES:
                raise PersistenceRequired(
                    "terminal_promotion_forbidden",
                    "an ephemeral terminal run cannot cross a later durable boundary",
                )
            promoted_spec = replace(spec, status=active.status)
            result = await self._durable.promote(
                promoted_spec, expected_version=expected_version
            )
            self._active_by_id.pop(active_id, None)
            self._active_by_key.pop(active.spec.idempotency_key, None)
            self._events_by_run.pop(active_id, None)
            return result

    @staticmethod
    def _terminal_outcome(status: RunStatus) -> OutcomeStatus:
        return {
            RunStatus.COMPLETED: OutcomeStatus.SUCCEEDED,
            RunStatus.FAILED: OutcomeStatus.FAILED,
            RunStatus.CANCELLED: OutcomeStatus.CANCELLED,
        }[status]

    @staticmethod
    def _same_candidate(left: RunEventCandidate, right: RunEventCandidate) -> bool:
        return left == right

    async def finalize(
        self,
        run_id: str,
        *,
        expected_version: int,
        terminal_status: RunStatus,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> FinalizeRunResult:
        terminal_status = RunStatus(terminal_status)
        if terminal_status not in TERMINAL_RUN_STATUSES:
            raise TerminalConflict(
                "non_terminal_finalize", "finalize requires a terminal status"
            )
        if event.status != self._terminal_outcome(terminal_status):
            raise TerminalConflict(
                "terminal_outcome_mismatch",
                "terminal event outcome differs from run status",
            )
        async with self._lock:
            record = self._active_by_id.get(run_id)
            if record is None:
                return await self._durable.finalize(
                    run_id,
                    expected_version=expected_version,
                    terminal_status=terminal_status,
                    event=event,
                    deliveries=deliveries,
                )
            if deliveries:
                raise PersistenceRequired(
                    "ephemeral_delivery_forbidden",
                    "an outbox delivery requires promotion before finalization",
                )
            existing_event = self._events_by_run.get(run_id, {}).get(event.event_key)
            if record.status in TERMINAL_RUN_STATUSES:
                if (
                    record.status != terminal_status
                    or existing_event is None
                    or not self._same_candidate(existing_event.candidate, event)
                ):
                    raise TerminalConflict(
                        "terminal_conflict", "another terminal intent already won"
                    )
                return FinalizeRunResult(record, existing_event, True)
            if record.version != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {record.version}",
                )
            if event.driver_kind != record.spec.driver_kind:
                raise RunIdentityConflict(
                    "driver_event_conflict", "terminal event driver differs from run owner"
                )
            now = float(self._clock())
            event_id = stable_event_id(run_id, event.event_key)
            terminal_event = RunEvent(
                event_id=event_id,
                run_id=run_id,
                root_run_id=record.context.root_run_id,
                session_id=record.context.session_id,
                durable_seq=None,
                live_cursor=LiveCursor(
                    stream_epoch=f"ephemeral-terminal:{run_id}", live_seq=1
                ),
                candidate=event,
                created_at=now,
            )
            terminal = replace(
                record,
                status=terminal_status,
                version=record.version + 1,
                terminal_event_id=event_id,
                updated_at=now,
                ended_at=now,
            )
            self._active_by_id[run_id] = terminal
            self._events_by_run.setdefault(run_id, {})[event.event_key] = terminal_event
            self._touch(run_id)
            return FinalizeRunResult(terminal, terminal_event, False)

    async def request_cancel(
        self,
        run_id: str,
        *,
        expected_version: int,
        reason: str,
        event: RunEventCandidate,
        deliveries: Sequence[DeliverySpec] = (),
    ) -> RunRecord:
        reason = str(reason).strip()
        if not reason or event.status is not OutcomeStatus.CANCEL_REQUESTED:
            raise TerminalConflict(
                "invalid_cancel_request",
                "cancel requires a reason and cancel_requested event",
            )
        async with self._lock:
            record = self._active_by_id.get(run_id)
            if record is None:
                return await self._durable.request_cancel(
                    run_id,
                    expected_version=expected_version,
                    reason=reason,
                    event=event,
                    deliveries=deliveries,
                )
            if deliveries:
                raise PersistenceRequired(
                    "ephemeral_delivery_forbidden",
                    "an outbox delivery requires promotion before cancellation",
                )
            if record.status in TERMINAL_RUN_STATUSES:
                raise TerminalConflict(
                    "run_already_terminal", "a terminal run cannot be cancelled"
                )
            existing_event = self._events_by_run.get(run_id, {}).get(event.event_key)
            if record.status is RunStatus.CANCEL_REQUESTED:
                if (
                    record.cancel_reason != reason
                    or existing_event is None
                    or existing_event.candidate != event
                ):
                    raise IdempotencyConflict(
                        "cancel_intent_conflict", "cancel replay differs from the first intent"
                    )
                return record
            if record.version != expected_version:
                raise VersionConflict(
                    "stale_run_version",
                    f"expected run version {expected_version}, found {record.version}",
                )
            if event.driver_kind != record.spec.driver_kind:
                raise RunIdentityConflict(
                    "driver_event_conflict", "cancel event differs from run owner"
                )
            now = float(self._clock())
            cancel_event = RunEvent(
                event_id=stable_event_id(run_id, event.event_key),
                run_id=run_id,
                root_run_id=record.context.root_run_id,
                session_id=record.context.session_id,
                durable_seq=None,
                live_cursor=LiveCursor(
                    stream_epoch=f"ephemeral-cancel:{run_id}", live_seq=1
                ),
                candidate=event,
                created_at=now,
            )
            cancelled = replace(
                record,
                status=RunStatus.CANCEL_REQUESTED,
                version=record.version + 1,
                cancel_reason=reason,
                updated_at=now,
            )
            self._active_by_id[run_id] = cancelled
            self._events_by_run.setdefault(run_id, {})[event.event_key] = cancel_event
            self._touch(run_id)
            return cancelled

    async def link(
        self,
        link: RunLinkSpec,
        *,
        expected_child_version: int,
    ) -> RunRecord:
        async with self._lock:
            if (
                link.parent_run_id in self._active_by_id
                or link.child_run_id in self._active_by_id
            ):
                raise PersistenceRequired(
                    "durable_link_required",
                    "runs must be promoted before a durable parent/domain link is created",
                )
            return await self._durable.link(
                link, expected_child_version=expected_child_version
            )

    def _authorize_active(
        self,
        ref: RunRef,
        actor: ActorContext,
        record: RunRecord,
    ) -> None:
        context = record.context
        if (
            ref.expected_session_id != context.session_id
            or actor.session_id != context.session_id
        ):
            raise AuthorizationError(
                "actor_not_authorized", "actor does not own the run session"
            )
        if actor.internal:
            if (
                actor.root_run_id != context.root_run_id
                or actor.capability_hash != context.capability_hash
                or actor.expires_at is None
                or actor.expires_at <= self._clock()
            ):
                raise AuthorizationError(
                    "actor_not_authorized",
                    "internal authority scope is invalid or expired",
                )
            return
        if (
            actor.principal_id != context.principal_id
            or actor.auth_epoch != context.auth_epoch
        ):
            raise AuthorizationError(
                "actor_not_authorized",
                "actor principal or authentication epoch differs",
            )

    async def authorize(
        self,
        ref: RunRef,
        actor: ActorContext,
        action: ActorAction | str,
    ) -> RunView:
        ActorAction(action)
        async with self._lock:
            record = self._active_by_id.get(ref.run_id)
            if record is not None:
                self._authorize_active(ref, actor, record)
                self._touch(ref.run_id)
                return record
        return await self._durable.authorize(ref, actor, action)

    async def query(self, ref: RunRef, actor: ActorContext) -> RunView:
        return await self.authorize(ref, actor, ActorAction.OBSERVE)

    async def list_children(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> tuple[RunRecord, ...]:
        async with self._lock:
            record = self._active_by_id.get(ref.run_id)
            if record is not None:
                self._authorize_active(ref, actor, record)
                return ()
        return await self._durable.list_children(ref, actor)

    async def list_child_links(
        self,
        ref: RunRef,
        actor: ActorContext,
    ) -> tuple[RunLinkSpec, ...]:
        async with self._lock:
            record = self._active_by_id.get(ref.run_id)
            if record is not None:
                self._authorize_active(ref, actor, record)
                return ()
        return await self._durable.list_child_links(ref, actor)


__all__ = ["ExecutionLedger"]
