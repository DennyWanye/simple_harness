"""Fenced durable delivery dispatch without a second product projector."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from deskpet.execution.contracts import DeliveryPolicy, ExecutionError
from deskpet.execution.delivery_contracts import RunEvent
from deskpet.execution.fences import (
    TerminalDeliveryFencePort,
    UnboundTerminalDeliveryFence,
)
from deskpet.execution.uow_ports import HarnessDeliveryUnitOfWork

logger = logging.getLogger(__name__)


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
        self._terminal_delivery_fence = terminal_delivery_fence or UnboundTerminalDeliveryFence()
        self._clock = clock
        self._claim_ttl_seconds = float(claim_ttl_seconds)
        self._retry_base_seconds = float(retry_base_seconds)
        self._retry_max_seconds = float(retry_max_seconds)
        self._retry_wakeup = retry_wakeup

    def _retry_at(self, attempts: int) -> float:
        delay = min(
            self._retry_max_seconds, self._retry_base_seconds * (2 ** min(30, max(0, attempts - 1)))
        )
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
                raise ExecutionError(
                    "sink_not_registered", f"delivery sink is not registered: {key!r}"
                )
            release_receipt_ref = (
                claim.release_receipt_ref or f"unbound-terminal:{claim.run_id}:{claim.delivery_id}"
            )
            if not await self._terminal_delivery_fence.authorize(
                run_id=claim.run_id,
                delivery_id=claim.delivery_id,
                release_receipt_ref=release_receipt_ref,
            ):
                raise DeliveryDiscarded("terminal_delivery_fenced")
            delivered = not (
                claim.policy in _BOUND_POLICIES and not await sink.is_bound(claim.target_id)
            )
            if delivered:
                await sink.deliver(await self._store.get_event(claim.event_id), claim.target_id)
        except DeliveryDiscarded as exc:
            error, discard = f"{type(exc).__name__}: {exc}", True
        except Exception as exc:
            logger.debug("run_once_failed", exc_info=True)
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
