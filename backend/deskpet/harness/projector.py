"""Fenced durable delivery dispatch without a second product projector."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from deskpet.execution.contracts import DeliveryPolicy, DeliveryRecord, ExecutionError, RunEvent
from deskpet.execution.ports import ExecutionUnitOfWork

_BOUND_POLICIES = frozenset({DeliveryPolicy.RETRY_WHILE_BOUND, DeliveryPolicy.BEST_EFFORT})


class DeliverySink(Protocol):
    async def is_bound(self, target_id: str) -> bool: ...

    async def deliver(self, event: RunEvent, target_id: str) -> None: ...


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
        store: ExecutionUnitOfWork,
        registrations: Sequence[SinkRegistration],
        *,
        owner_generation: int,
        clock: Callable[[], float] = time.time,
        claim_ttl_seconds: float = 30.0,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 60.0,
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
        self._clock = clock
        self._claim_ttl_seconds = float(claim_ttl_seconds)
        self._retry_base_seconds = float(retry_base_seconds)
        self._retry_max_seconds = float(retry_max_seconds)

    def _sink(self, delivery: DeliveryRecord) -> DeliverySink:
        key = delivery.sink_kind, delivery.sink_instance
        try:
            return self._sinks[key]
        except KeyError as exc:
            raise ExecutionError("sink_not_registered",
                                 f"delivery sink is not registered: {key!r}") from exc

    async def dispatch(self, delivery: DeliveryRecord) -> bool:
        sink = self._sink(delivery)
        if delivery.policy in _BOUND_POLICIES and not await sink.is_bound(delivery.target_id):
            return False
        event = await self._store.get_event(delivery.event_id)
        await sink.deliver(event, delivery.target_id)
        return True

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
        try:
            delivered = await self.dispatch(claim)
        except Exception as exc:
            discard = claim.policy is DeliveryPolicy.BEST_EFFORT
            await self._store.release_delivery(
                claim.delivery_id,
                expected_version=claim.delivery_version,
                owner_generation=self._owner_generation,
                error=f"{type(exc).__name__}: {exc}",
                retry_at=None if discard else self._retry_at(claim.attempts),
                discard=discard,
            )
        else:
            if delivered:
                await self._store.complete_delivery(
                    claim.delivery_id,
                    expected_version=claim.delivery_version,
                    owner_generation=self._owner_generation,
                )
            else:
                await self._store.release_delivery(
                    claim.delivery_id,
                    expected_version=claim.delivery_version,
                    owner_generation=self._owner_generation,
                    error="delivery sink is no longer bound",
                    retry_at=None,
                    discard=True,
                )
        return True
