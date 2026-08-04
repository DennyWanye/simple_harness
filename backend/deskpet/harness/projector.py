"""Fenced durable delivery dispatch without a second product projector."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from deskpet.execution.contracts import (
    DeliveryPolicy, DeliverySpec, ExecutionError,
    OutcomeStatus, RunEvent, RunEventCandidate, RunRecord,
)
from deskpet.execution.uow_ports import HarnessDeliveryUnitOfWork
from deskpet.execution.fences import (
    TerminalDeliveryFencePort,
    UnboundTerminalDeliveryFence,
)

_BOUND_POLICIES = frozenset({DeliveryPolicy.RETRY_WHILE_BOUND, DeliveryPolicy.BEST_EFFORT})


class DeliverySink(Protocol):
    async def is_bound(self, target_id: str) -> bool: ...

    async def deliver(self, event: RunEvent, target_id: str) -> None: ...


class DeliveryDiscarded(RuntimeError):
    """A sink fenced the projection and it must not be retried."""


@dataclass(frozen=True, slots=True)
class SinkRegistration:
    sink_kind: str
    sink_instance: str
    sink: DeliverySink

    def __post_init__(self) -> None:
        if not self.sink_kind.strip() or not self.sink_instance.strip():
            raise ValueError("sink registration requires kind and instance")


class GoalTerminalProjection:
    """Strict-session Goal association plus its durable terminal sink."""

    def __init__(self, goal_store: object) -> None:
        self._goals = goal_store

    def resolve(self, session_id: str) -> str | None:
        context = self._goals.get_active_goal_context_for_session(session_id)
        return context[0] if context is not None and context[1] == session_id else None

    def association_event(self, spec: RunCreate, target_id: str) -> RunEventCandidate:
        return RunEventCandidate(
            event_key=f"goal:{target_id}", kind="goal_associated",
            status=OutcomeStatus.ACCEPTED, driver_kind=spec.driver_kind,
            payload={"target_id": target_id},
        )

    def deliveries(self, record: RunRecord, events: Sequence[RunEvent]) -> Sequence[DeliverySpec]:
        target = next((str(event.candidate.payload["target_id"]) for event in events
                       if event.kind == "goal_associated"), None)
        return () if target is None else (DeliverySpec(
            "goal_projection", "session-goals", target,
            DeliveryPolicy.DURABLE_REQUIRED,
        ),)

    def requires_durable(self, _request: object, host: object) -> bool:
        session_id = str(getattr(host, "session_id", "") or "")
        return bool(session_id and self.resolve(session_id) is not None)

    def freeze_deliveries(
        self, _request: object, host: object
    ) -> Sequence[DeliverySpec]:
        session_id = str(getattr(host, "session_id", "") or "")
        target = self.resolve(session_id) if session_id else None
        if target is None:
            return ()
        return (
            DeliverySpec(
                "goal_projection",
                "session-goals",
                target,
                DeliveryPolicy.DURABLE_REQUIRED,
            ),
        )

    async def is_bound(self, target_id: str) -> bool:
        return bool(target_id)

    async def deliver(self, event: RunEvent, target_id: str) -> None:
        if event.run_id != event.root_run_id or event.kind not in {"final", "run.final"}:
            raise ExecutionError("invalid_goal_projection", "only root terminal events project Goals")
        status = "done" if event.status is OutcomeStatus.SUCCEEDED else "abandoned"
        if not await self._goals.project_terminal(target_id, status):
            raise ExecutionError(
                "goal_projection_conflict", "Goal target is absent or changed"
            )

class ExecutionDeliveryDispatcher:
    """The sole execution-row delivery owner, fenced by activation generation."""

    def __init__(
        self,
        store: HarnessDeliveryUnitOfWork,
        registrations: Sequence[SinkRegistration],
        *,
        owner_generation: int,
        terminal_delivery_fence: TerminalDeliveryFencePort | None = None,
        clock: Callable[[], float] = time.time,
        claim_ttl_seconds: float = 30.0,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 60.0,
        retry_wakeup: Callable[[float], None] | None = None,
    ) -> None:
        timing = (claim_ttl_seconds, retry_base_seconds, retry_max_seconds)
        if any(not math.isfinite(float(value)) or value <= 0 for value in timing):
            raise ValueError("delivery timing values must be positive")
        if isinstance(owner_generation, bool) or owner_generation < 1:
            raise ValueError("owner_generation must be a positive kernel generation")
        self._store = store
        items = tuple(registrations)
        self._sinks = {(item.sink_kind, item.sink_instance): item.sink for item in items}
        if len(self._sinks) != len(items):
            raise ValueError("delivery sink registrations must be unique")
        self._owner_generation = owner_generation
        self._terminal_delivery_fence = (
            terminal_delivery_fence or UnboundTerminalDeliveryFence()
        )
        self._clock = clock
        self._claim_ttl_seconds = float(claim_ttl_seconds)
        self._retry_base_seconds = float(retry_base_seconds)
        self._retry_max_seconds = float(retry_max_seconds)
        self._retry_wakeup = retry_wakeup

    def _retry_at(self, attempts: int) -> float:
        delay = min(self._retry_max_seconds,
                    self._retry_base_seconds * (2 ** min(30, max(0, attempts - 1))))
        return float(self._clock()) + delay

    async def run_once(self) -> bool:
        claim = await self._store.claim_delivery(
            owner_generation=self._owner_generation,
            sink_keys=tuple(self._sinks),
            claim_ttl_seconds=self._claim_ttl_seconds,
        )
        if claim is None:
            return False
        error, retry_at, discard = None, None, False
        try:
            key = claim.sink_kind, claim.sink_instance
            sink = self._sinks.get(key)
            if sink is None:
                raise ExecutionError("sink_not_registered",
                                     f"delivery sink is not registered: {key!r}")
            release_receipt_ref = (
                claim.release_receipt_ref
                or f"unbound-terminal:{claim.run_id}:{claim.delivery_id}"
            )
            if not await self._terminal_delivery_fence.authorize(
                run_id=claim.run_id,
                delivery_id=claim.delivery_id,
                release_receipt_ref=release_receipt_ref,
            ):
                raise DeliveryDiscarded("terminal_delivery_fenced")
            delivered = not (
                claim.policy in _BOUND_POLICIES
                and not await sink.is_bound(claim.target_id)
            )
            if delivered:
                await sink.deliver(await self._store.get_event(claim.event_id), claim.target_id)
        except DeliveryDiscarded as exc:
            error, discard = f"{type(exc).__name__}: {exc}", True
        except Exception as exc:
            discard = claim.policy is DeliveryPolicy.BEST_EFFORT
            error = f"{type(exc).__name__}: {exc}"
            retry_at = None if discard else self._retry_at(claim.attempts)
        else:
            if not delivered:
                error, discard = "delivery sink is no longer bound", True
        await self._store.settle_delivery(
            claim.delivery_id,
            expected_version=claim.delivery_version,
            owner_generation=self._owner_generation,
            error=error,
            retry_at=retry_at,
            discard=discard,
        )
        if retry_at is not None and self._retry_wakeup is not None:
            self._retry_wakeup(max(0.0, retry_at - float(self._clock())))
        return True
