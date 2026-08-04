# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Single-process authority and revocation gates for Companion growth.

The durable ``growth_authority_state`` row is the source of truth.  This
module deliberately contains no SQLite knowledge: :class:`GrowthAuthorityRouter`
depends on the small store port below so the composition root cannot grow a
second flag-based authority decision.
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, TypeVar, cast


class GrowthAuthorityPhase(StrEnum):
    LEGACY = "legacy"
    PREPARING = "preparing"
    COMPANION = "companion"
    PAUSED = "paused"


class GrowthIngressKind(StrEnum):
    PREFERENCE_READ = "preference_read"
    PREFERENCE_WRITE = "preference_write"
    CODIFY = "codify"
    REMINDER_READ = "reminder_read"
    REMINDER_WRITE = "reminder_write"

    @property
    def is_write(self) -> bool:
        return self in {
            self.PREFERENCE_WRITE,
            self.CODIFY,
            self.REMINDER_WRITE,
        }


@dataclass(frozen=True, slots=True)
class GrowthAuthorityState:
    phase: GrowthAuthorityPhase
    generation: int
    roll_forward_required: bool = False
    migration_version: str | None = None
    migration_hash: str | None = None
    cutover_operation_id: str | None = None
    drain_started: bool = False
    drain_completed: bool = False
    last_error: str | None = None

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("authority generation must be non-negative")


@dataclass(frozen=True, slots=True)
class GrowthIngressRequest:
    """Typed ingress accepted by the only growth authority router."""

    kind: GrowthIngressKind
    profile_id: str
    profile_generation: int
    authority_generation: int
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        if self.profile_generation < 0:
            raise ValueError("profile_generation must be non-negative")
        if self.authority_generation < 0:
            raise ValueError("authority_generation must be non-negative")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


class GrowthAuthorityError(RuntimeError):
    """Base class for typed, fail-closed authority failures."""


class GrowthAuthorityNotStarted(GrowthAuthorityError):
    pass


class GrowthAuthorityGenerationMismatch(GrowthAuthorityError):
    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(
            f"growth authority generation mismatch: expected={expected} actual={actual}"
        )
        self.expected = expected
        self.actual = actual


class GrowthWritesPaused(GrowthAuthorityError):
    pass


class GrowthIngressUnavailable(GrowthAuthorityError):
    pass


class IllegalGrowthAuthorityTransition(GrowthAuthorityError):
    pass


class RevocationBarrierTimeout(GrowthAuthorityError):
    pass


class GrowthAuthorityStorePort(Protocol):
    async def get_growth_authority_state(self) -> object: ...

    async def transition_growth_authority_state(
        self,
        expected_phase: str,
        next_phase: str,
        *,
        migration_generation: int,
        marker_committed: bool = False,
        journal_payload: Mapping[str, object],
    ) -> object: ...


class GrowthAuthorityCutoverSession:
    """Exclusive access to the Router's existing durable pointer."""

    def __init__(self, router: "GrowthAuthorityRouter") -> None:
        self._router = router

    @property
    def current(self) -> GrowthAuthorityState:
        return self._router.current

    async def transition(
        self,
        next_phase: GrowthAuthorityPhase,
        *,
        expected_generation: int,
        marker_committed: bool | None = None,
        preflight_passed: bool = False,
        reason: str,
        journal_payload: Mapping[str, object] | None = None,
    ) -> GrowthAuthorityState:
        return await self._router._transition_locked(
            next_phase,
            expected_generation=expected_generation,
            marker_committed=marker_committed,
            preflight_passed=preflight_passed,
            reason=reason,
            journal_payload=journal_payload,
        )


class GrowthAuthorityPort(Protocol):
    async def read(self, request: GrowthIngressRequest) -> object: ...

    async def write(self, request: GrowthIngressRequest) -> object: ...


@dataclass(frozen=True, slots=True)
class SharedRevocationLease:
    barrier_id: int
    epoch: int


@dataclass(frozen=True, slots=True)
class ExclusiveRevocationLease:
    barrier_id: int
    epoch: int


class RevocationBarrier:
    """Writer-preferring asynchronous shared/exclusive barrier.

    Execution boundaries take a short shared lease.  Forget/profile lifecycle
    mutations take an exclusive lease.  Waiting writers prevent new readers
    from starving a revocation.  The token's epoch lets callers revalidate
    after releasing a short lease without holding it across a long provider or
    health-check wait.
    """

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._bound_loop: asyncio.AbstractEventLoop | None = None
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0
        self._epoch = 0

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def identity(self) -> int:
        return id(self)

    async def _wait(self, predicate: Any, timeout: float | None) -> None:
        try:
            if timeout is None:
                await self._condition.wait_for(predicate)
            else:
                async with asyncio.timeout(timeout):
                    await self._condition.wait_for(predicate)
        except TimeoutError as exc:
            raise RevocationBarrierTimeout("revocation barrier acquisition timed out") from exc

    def _bind_current_loop(self) -> None:
        """Lazily bind an idle process barrier to the active event loop.

        Production has one application loop.  Test hosts and controlled
        restarts can create a replacement loop in the same process; an idle
        barrier has no waiter state to preserve, so rebinding is safe.  An
        active barrier must never cross loops.
        """

        loop = asyncio.get_running_loop()
        if self._bound_loop is loop:
            return
        if (
            self._readers
            or self._writer
            or self._waiting_writers
        ):
            raise RuntimeError(
                "active revocation barrier cannot move between event loops"
            )
        self._condition = asyncio.Condition()
        self._bound_loop = loop

    @asynccontextmanager
    async def shared(
        self, *, timeout: float | None = 5.0
    ) -> AsyncIterator[SharedRevocationLease]:
        self._bind_current_loop()
        async with self._condition:
            await self._wait(
                lambda: not self._writer and self._waiting_writers == 0,
                timeout,
            )
            self._readers += 1
            token = SharedRevocationLease(self.identity, self._epoch)
        try:
            yield token
        finally:
            async with self._condition:
                self._readers -= 1
                if self._readers == 0:
                    self._condition.notify_all()

    @asynccontextmanager
    async def exclusive(
        self, *, timeout: float | None = 5.0
    ) -> AsyncIterator[ExclusiveRevocationLease]:
        self._bind_current_loop()
        acquired = False
        async with self._condition:
            self._waiting_writers += 1
            try:
                await self._wait(
                    lambda: not self._writer and self._readers == 0,
                    timeout,
                )
                self._writer = True
                self._epoch += 1
                acquired = True
                token = ExclusiveRevocationLease(self.identity, self._epoch)
            finally:
                self._waiting_writers -= 1
                if not acquired:
                    self._condition.notify_all()
        try:
            yield token
        finally:
            async with self._condition:
                self._writer = False
                self._condition.notify_all()

    def is_current(self, lease: SharedRevocationLease | ExclusiveRevocationLease) -> bool:
        return lease.barrier_id == self.identity and lease.epoch == self._epoch


_PROCESS_REVOCATION_BARRIER = RevocationBarrier()


def get_process_revocation_barrier() -> RevocationBarrier:
    """Return the one production barrier shared by all execution fences."""

    return _PROCESS_REVOCATION_BARRIER


_T = TypeVar("_T")


async def _await_if_needed(value: _T) -> _T:
    if inspect.isawaitable(value):
        return cast(_T, await cast(Any, value))
    return value


def _field(row: object, name: str, default: object = None) -> object:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _coerce_state(row: object) -> GrowthAuthorityState:
    if isinstance(row, GrowthAuthorityState):
        return row
    phase = GrowthAuthorityPhase(str(_field(row, "phase")))
    generation_raw = _field(row, "generation", _field(row, "migration_generation", 0))
    marker = _field(
        row,
        "roll_forward_required",
        _field(row, "marker_committed", False),
    )
    return GrowthAuthorityState(
        phase=phase,
        generation=int(cast(int, generation_raw)),
        roll_forward_required=bool(marker),
        migration_version=cast(str | None, _field(row, "migration_version")),
        migration_hash=cast(str | None, _field(row, "migration_hash")),
        cutover_operation_id=cast(str | None, _field(row, "cutover_operation_id")),
        drain_started=bool(
            _field(
                row,
                "drain_started",
                _field(row, "drain_started_at", None),
            )
        ),
        drain_completed=bool(
            _field(
                row,
                "drain_completed",
                _field(row, "drain_completed_at", None),
            )
        ),
        last_error=cast(str | None, _field(row, "last_error")),
    )


class GrowthAuthorityRouter:
    """The only process-level switch between legacy and Companion writers."""

    def __init__(
        self,
        *,
        store: GrowthAuthorityStorePort,
        legacy: GrowthAuthorityPort,
        companion: GrowthAuthorityPort | None = None,
        ingress_timeout: float = 5.0,
    ) -> None:
        self._store = store
        self._legacy = legacy
        self._companion = companion
        # The Router, execution fences, forget and profile lifecycle all use
        # the same process barrier. Separate locks would permit a provider or
        # legacy writer to cross a revocation/cutover boundary.
        self._ingress_gate = get_process_revocation_barrier()
        self._state_lock = asyncio.Lock()
        self._state: GrowthAuthorityState | None = None
        self._ingress_timeout = ingress_timeout

    @property
    def current(self) -> GrowthAuthorityState:
        if self._state is None:
            raise GrowthAuthorityNotStarted("growth authority has not been recovered")
        return self._state

    @property
    def ordinary_chat_allowed(self) -> bool:
        """Authority failures pause growth only; ordinary chat stays available."""

        return True

    async def start(self) -> GrowthAuthorityState:
        """Recover the durable pointer before opening any growth ingress.

        A pre-marker ``preparing`` state is safe to roll back to legacy because
        no externally visible fact has changed.  A committed marker is never
        guessed backwards; it remains closed for the cutover reconciler.
        """

        async with self._ingress_gate.exclusive(timeout=self._ingress_timeout):
            async with self._state_lock:
                state = _coerce_state(
                    await _await_if_needed(self._store.get_growth_authority_state())
                )
                self._state = state
                if (
                    state.phase is GrowthAuthorityPhase.PREPARING
                    and not state.roll_forward_required
                ):
                    state = await self._transition_locked(
                        GrowthAuthorityPhase.LEGACY,
                        expected_generation=state.generation,
                        marker_committed=False,
                        reason="startup_pre_marker_rollback",
                    )
                elif (
                    state.phase is GrowthAuthorityPhase.COMPANION
                    and self._companion is None
                ):
                    state = await self._transition_locked(
                        GrowthAuthorityPhase.PAUSED,
                        expected_generation=state.generation,
                        marker_committed=state.roll_forward_required,
                        reason="startup_companion_adapter_missing",
                        journal_payload={
                            "error_code": "companion_authority_adapter_missing"
                        },
                    )
                return state

    async def refresh(self) -> GrowthAuthorityState:
        async with self._ingress_gate.exclusive(timeout=self._ingress_timeout):
            async with self._state_lock:
                state = _coerce_state(
                    await _await_if_needed(self._store.get_growth_authority_state())
                )
                if (
                    self._state is not None
                    and state.generation < self._state.generation
                ):
                    raise GrowthAuthorityError(
                        "durable authority generation moved backwards"
                    )
                self._state = state
                return state

    async def dispatch(self, request: GrowthIngressRequest) -> object:
        async with self._ingress_gate.shared(timeout=self._ingress_timeout):
            async with self._state_lock:
                state = self.current
                self._assert_generation(request.authority_generation, state)
                authority = self._select_authority(state, is_write=request.kind.is_write)
            if request.kind.is_write:
                return await authority.write(request)
            return await authority.read(request)

    async def delete_profile_generation(
        self, owner: object, *, reason_code: str
    ) -> object:
        """Serialize profile deletion with every ingress/execution lease."""

        delete = getattr(self._store, "delete_profile_generation", None)
        if delete is None:
            raise GrowthIngressUnavailable(
                "growth authority store has no profile lifecycle port"
            )
        async with self._ingress_gate.exclusive(
            timeout=self._ingress_timeout
        ):
            return await _await_if_needed(
                delete(owner, reason_code=reason_code)
            )

    async def transition(
        self,
        next_phase: GrowthAuthorityPhase,
        *,
        expected_generation: int,
        marker_committed: bool | None = None,
        preflight_passed: bool = False,
        reason: str,
        journal_payload: Mapping[str, object] | None = None,
    ) -> GrowthAuthorityState:
        async with self._ingress_gate.exclusive(timeout=self._ingress_timeout):
            async with self._state_lock:
                return await self._transition_locked(
                    next_phase,
                    expected_generation=expected_generation,
                    marker_committed=marker_committed,
                    preflight_passed=preflight_passed,
                    reason=reason,
                    journal_payload=journal_payload,
                )

    @asynccontextmanager
    async def cutover_session(
        self,
    ) -> AsyncIterator[GrowthAuthorityCutoverSession]:
        """Drain ingress and hold the one authority gate for a whole cutover.

        The returned session calls the same private transition primitive used
        by :meth:`transition`; it cannot create another Router or pointer.
        """

        async with self._ingress_gate.exclusive(
            timeout=self._ingress_timeout
        ):
            async with self._state_lock:
                if self._state is None:
                    raise GrowthAuthorityNotStarted(
                        "growth authority has not been recovered"
                    )
                yield GrowthAuthorityCutoverSession(self)

    async def _transition_locked(
        self,
        next_phase: GrowthAuthorityPhase,
        *,
        expected_generation: int,
        marker_committed: bool | None,
        reason: str,
        preflight_passed: bool = False,
        journal_payload: Mapping[str, object] | None = None,
    ) -> GrowthAuthorityState:
        state = self.current
        self._assert_generation(expected_generation, state)
        marker = (
            state.roll_forward_required
            if marker_committed is None
            else marker_committed
        )
        self._validate_transition(
            state,
            next_phase,
            marker_committed=marker,
            preflight_passed=preflight_passed,
        )
        if next_phase is state.phase and marker == state.roll_forward_required:
            return state

        payload: dict[str, object] = {
            "reason": reason,
            "from_phase": state.phase.value,
            "to_phase": next_phase.value,
            "old_generation": state.generation,
            "new_generation": state.generation + 1,
            "roll_forward_required": marker,
            "preflight_passed": preflight_passed,
        }
        if journal_payload:
            payload.update(journal_payload)
        row = await _await_if_needed(
            self._store.transition_growth_authority_state(
                state.phase.value,
                next_phase.value,
                migration_generation=state.generation + 1,
                marker_committed=marker,
                journal_payload=MappingProxyType(payload),
            )
        )
        if row is None:
            row = await _await_if_needed(self._store.get_growth_authority_state())
        next_state = _coerce_state(row)
        if next_state.generation <= state.generation:
            raise GrowthAuthorityError(
                "authority transition did not advance durable generation"
            )
        if next_state.phase is not next_phase:
            raise GrowthAuthorityError("authority store returned the wrong phase")
        if state.roll_forward_required and not next_state.roll_forward_required:
            raise GrowthAuthorityError("authority store cleared the committed marker")
        self._state = next_state
        return next_state

    @staticmethod
    def _assert_generation(expected: int, state: GrowthAuthorityState) -> None:
        if expected != state.generation:
            raise GrowthAuthorityGenerationMismatch(
                expected=expected,
                actual=state.generation,
            )

    def _select_authority(
        self, state: GrowthAuthorityState, *, is_write: bool
    ) -> GrowthAuthorityPort:
        if state.phase is GrowthAuthorityPhase.LEGACY:
            return self._legacy
        if state.phase is GrowthAuthorityPhase.COMPANION:
            if self._companion is None:
                raise GrowthIngressUnavailable(
                    "Companion authority is active but no Companion adapter is installed"
                )
            return self._companion
        if state.phase is GrowthAuthorityPhase.PAUSED and is_write:
            raise GrowthWritesPaused("Companion growth writes are paused")
        if state.phase is GrowthAuthorityPhase.PREPARING:
            if not state.roll_forward_required and not is_write:
                return self._legacy
            raise GrowthIngressUnavailable(
                "growth ingress is closed while authority cutover is preparing"
            )
        raise GrowthIngressUnavailable(
            f"growth ingress is unavailable in {state.phase.value}"
        )

    @staticmethod
    def _validate_transition(
        state: GrowthAuthorityState,
        next_phase: GrowthAuthorityPhase,
        *,
        marker_committed: bool,
        preflight_passed: bool,
    ) -> None:
        current = state.phase
        if state.roll_forward_required and not marker_committed:
            raise IllegalGrowthAuthorityTransition(
                "a committed roll-forward marker cannot be cleared"
            )
        if current is GrowthAuthorityPhase.LEGACY:
            legal = (
                next_phase is GrowthAuthorityPhase.PREPARING
                and not marker_committed
            )
        elif current is GrowthAuthorityPhase.PREPARING:
            if state.roll_forward_required:
                legal = next_phase in {
                    GrowthAuthorityPhase.PREPARING,
                    GrowthAuthorityPhase.COMPANION,
                    GrowthAuthorityPhase.PAUSED,
                }
            else:
                legal = next_phase in {
                    GrowthAuthorityPhase.LEGACY,
                    GrowthAuthorityPhase.PREPARING,
                    GrowthAuthorityPhase.PAUSED,
                }
                if next_phase is GrowthAuthorityPhase.COMPANION:
                    legal = False
            if next_phase is GrowthAuthorityPhase.LEGACY and marker_committed:
                legal = False
        elif current is GrowthAuthorityPhase.COMPANION:
            legal = next_phase is GrowthAuthorityPhase.PAUSED
        else:
            legal = (
                next_phase is GrowthAuthorityPhase.COMPANION
                and preflight_passed
                and marker_committed
            )
        if not legal:
            raise IllegalGrowthAuthorityTransition(
                f"illegal growth authority transition: {current.value}->{next_phase.value}"
            )


__all__ = [
    "ExclusiveRevocationLease",
    "GrowthAuthorityError",
    "GrowthAuthorityGenerationMismatch",
    "GrowthAuthorityCutoverSession",
    "GrowthAuthorityNotStarted",
    "GrowthAuthorityPhase",
    "GrowthAuthorityPort",
    "GrowthAuthorityRouter",
    "GrowthAuthorityState",
    "GrowthAuthorityStorePort",
    "GrowthIngressKind",
    "GrowthIngressRequest",
    "GrowthIngressUnavailable",
    "GrowthWritesPaused",
    "IllegalGrowthAuthorityTransition",
    "RevocationBarrier",
    "RevocationBarrierTimeout",
    "SharedRevocationLease",
    "get_process_revocation_barrier",
]
