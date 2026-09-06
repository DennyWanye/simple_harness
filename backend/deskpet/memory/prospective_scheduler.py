"""Single bounded one-shot time/event scheduler; Memory owns occurrence identity.

Composition supplies the public-source adapter and schema51 signal journal.
No occurrence is represented as a Memory outbox command. Event source composition
is optional and never substitutes a test authority. Recurrence remains excluded.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from simple_harness.runtime import ProspectiveSignalAuthority, ProspectiveSignalAuthorityRef
from deskpet.memory.prospective_event_codec import PreparedEvent
from deskpet.memory.writer_fence import assert_human_memory_ingress_open


@dataclass(frozen=True)
class TimerClaim:
    signal_id: str
    owner: str
    epoch: int
    authority: ProspectiveSignalAuthority
    handed_off: bool

    @property
    def reference(self):
        return ProspectiveSignalAuthorityRef.from_authority(self.authority)


@dataclass(frozen=True)
class PreparedTimer:
    authority: ProspectiveSignalAuthority
    observation: dict


class TimerSource(Protocol):
    async def prepare_due(self, *, now: float, limit: int) -> tuple[PreparedTimer, ...]:
        """Only due time targets from actual Host ACKed registrations.

        Each grant binds exact source Run/operation and first observation.
        Existing durable timer grants must be recovered, never freshly issued.
        """
        ...

    async def registration_is_live(self, authority: ProspectiveSignalAuthority) -> bool:
        """Recheck Host ACK registration and absence of ACKed invalidation.

        SDK current revision/lifecycle/suppression are checked by apply, not here."""
        ...


class TimerJournal(Protocol):
    path: object
    principal: object

    async def get_prepared(self, signal_id: str) -> PreparedTimer | None: ...
    async def prepare(self, prepared: PreparedTimer) -> None: ...
    async def handoff(self, claim: TimerClaim, *, now: float) -> TimerClaim: ...
    async def claim(self, *, owner: str, now: float, lease_seconds: float) -> TimerClaim | None: ...
    async def assert_claim(self, claim: TimerClaim, *, now: float) -> None: ...
    async def invalidate(self, claim: TimerClaim, *, now: float) -> None: ...
    async def applied(self, claim: TimerClaim, result, *, now: float) -> None: ...


class ProspectiveScheduler:
    def __init__(self, *, store: TimerJournal, source: TimerSource, memory,
                 clock: Callable[[], float] = time.time, lease_seconds: float = 30.0, event_source=None):
        if not callable(clock) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError('prospective_timer_clock_or_lease_invalid')
        self.store, self.source, self.memory = store, source, memory
        self.clock, self.lease_seconds = clock, lease_seconds
        self._tick_lock = asyncio.Lock()
        self.event_source = event_source
        self._event_first = False

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value) or value < 0:
            raise ValueError('prospective_timer_clock_invalid')
        return value

    async def tick(self, *, claim_owner: str, limit: int = 32) -> int:
        from simple_harness_memory import MemoryScope
        if type(limit) is not int or not 1 <= limit <= 100 or not claim_owner:
            raise ValueError('prospective_timer_tick_arguments_invalid')
        async with self._tick_lock:
            await assert_human_memory_ingress_open(self.store.path)
            handled = 0
            delivered = 0
            # Recover existing durable work before scanning new source facts.
            # A source scan failure must not strand a possibly committed call.
            async def drain():
                nonlocal handled, delivered
                while handled < limit:
                    claim = await self.store.claim(owner=claim_owner, now=self._now(),
                                                   lease_seconds=self.lease_seconds)
                    if claim is None:
                        break
                    handled += 1
                    if not claim.handed_off:
                        kind = claim.authority.intent.signal_kind.value
                        source = self.source if kind == 'time_due' else self.event_source if kind == 'event_occurred' else None
                        if source is None:
                            # No proof of invalidation: leave the durable claim pending.
                            raise ValueError('prospective_signal_source_unavailable')
                        if not await source.registration_is_live(claim.authority):
                            await self.store.invalidate(claim, now=self._now())
                            continue
                    await assert_human_memory_ingress_open(self.store.path)
                    await self.store.assert_claim(claim, now=self._now())
                    claim = await self.store.handoff(claim, now=self._now())
                    scope = claim.authority.intent.scope
                    # Exceptions/cancellation leave the handoff durable. The SDK
                    # returns consumed results before checking current state.
                    result = await self.memory.apply_prospective_signal(
                        principal=self.store.principal,
                        scope=MemoryScope(scope.kind.value, scope.owner_id),
                        reference=claim.reference,
                    )
                    await self.store.applied(claim, result, now=self._now())
                    delivered += 1

            await drain()
            sources = [(self.source, PreparedTimer, 'time_due')]
            if self.event_source is not None:
                event = (self.event_source, PreparedEvent, 'event_occurred')
                sources = [event, *sources] if self._event_first else [*sources, event]
                self._event_first = not self._event_first
            for source, expected_type, expected_kind in sources:
                if handled >= limit:
                    break
                prepared_signals = await source.prepare_due(now=self._now(), limit=limit-handled)
                if len(prepared_signals) > limit-handled:
                    raise ValueError('prospective_timer_source_page_exceeds_limit')
                for prepared in prepared_signals:
                    if type(prepared) is not expected_type or prepared.authority.intent.signal_kind.value != expected_kind:
                        raise ValueError('prospective_signal_source_domain_differs')
                    await self.store.prepare(prepared)
                await drain()
            return delivered
