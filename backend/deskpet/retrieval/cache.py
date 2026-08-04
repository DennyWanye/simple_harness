"""Process-shared bounded cache and provider circuit state."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Generic, Literal, Mapping, TypeVar

T = TypeVar("T")
CircuitState = Literal["closed", "open", "half_open"]
PermitKind = Literal["closed", "open", "half_open", "half_open_busy"]
ProbeOutcome = Literal["hit", "empty", "failure", "cancel"]


class AsyncTTLCache(Generic[T]):
    def __init__(self, *, max_size: int, ttl_s: float) -> None:
        self.max_size = max(1, int(max_size))
        self.ttl_s = max(0.01, float(ttl_s))
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> T | None:
        now = time.monotonic()
        async with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= now:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value

    async def put(self, key: str, value: T) -> None:
        async with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_s, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_size:
                self._items.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()

    async def __len_async__(self) -> int:
        async with self._lock:
            return len(self._items)


@dataclass(frozen=True, slots=True)
class FailurePolicy:
    threshold: int
    open_s: float
    probe_base_s: float
    probe_max_s: float
    retry_after_max_s: float = 3600.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "threshold", max(1, int(self.threshold)))
        object.__setattr__(self, "open_s", max(0.01, float(self.open_s)))
        object.__setattr__(self, "probe_base_s", max(0.01, float(self.probe_base_s)))
        object.__setattr__(
            self,
            "probe_max_s",
            max(float(self.probe_base_s), float(self.probe_max_s)),
        )
        object.__setattr__(
            self,
            "retry_after_max_s",
            max(0.01, float(self.retry_after_max_s)),
        )


@dataclass(frozen=True, slots=True)
class CircuitPermit:
    kind: PermitKind
    generation: int
    token: str | None = None
    wait_s: float = 0.0
    failure_class: str | None = None

    @property
    def probe_owner(self) -> bool:
        return self.kind == "half_open" and bool(self.token)


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    state: CircuitState
    generation: int
    eligible_at: float
    failure_class: str | None
    probe_in_flight: bool


@dataclass(frozen=True, slots=True)
class ProbeCandidate:
    provider: str
    generation: int
    eligible_at: float


@dataclass(slots=True)
class _Health:
    state: CircuitState = "closed"
    generation: int = 0
    failures: int = 0
    failure_class: str | None = None
    open_until: float = 0.0
    next_forced_at: float = 0.0
    probe_failures: int = 0
    probe_token: str | None = None
    forced_consumed: bool = False


class ProviderHealth:
    """Atomic per-provider circuit with a leased half-open probe.

    Request budgets and diagnostics deliberately do not live here.  Only the
    process-shared provider health state is protected by this lock.
    """

    def __init__(
        self,
        *,
        policies: Mapping[str, FailurePolicy] | None = None,
        threshold: int = 2,
        cooldown_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        legacy = FailurePolicy(threshold, cooldown_s, min(2.0, cooldown_s), cooldown_s)
        self._policies = dict(policies or {"http": legacy})
        self._fallback_policy = self._policies.get("http", legacy)
        self._states: dict[str, _Health] = {}
        self._lock = asyncio.Lock()
        self._clock = clock

    def _policy(self, failure_class: str | None) -> FailurePolicy:
        return self._policies.get(str(failure_class or "http"), self._fallback_policy)

    def _eligible_at(self, state: _Health) -> float:
        if state.state == "closed":
            return self._clock()
        if state.state == "half_open":
            return max(self._clock(), state.open_until)
        if state.forced_consumed:
            return state.open_until
        return min(state.open_until, state.next_forced_at)

    async def snapshot(self, provider: str) -> CircuitSnapshot:
        async with self._lock:
            state = self._states.get(provider, _Health())
            return CircuitSnapshot(
                state=state.state,
                generation=state.generation,
                eligible_at=self._eligible_at(state),
                failure_class=state.failure_class,
                probe_in_flight=state.state == "half_open",
            )

    async def select_probe_candidate(
        self,
        names: list[str],
        deadline: float,
    ) -> ProbeCandidate | None:
        """Select an eligible open/busy provider without issuing a permit.

        The caller-provided order is the stable tie breaker.  Permit issuance
        remains a separate operation performed only after the provider slot is
        acquired, so a queued request cannot hold a stale closed lease.
        """

        async with self._lock:
            open_candidates: list[tuple[float, int, str, _Health]] = []
            busy_candidates: list[tuple[float, int, str, _Health]] = []
            for order, provider in enumerate(names):
                state = self._states.get(provider)
                if state is None or state.state == "closed":
                    continue
                # Prefer a real open circuit.  A half-open lease is represented
                # as immediately inspectable only when no open peer exists;
                # the later acquire will honestly return half_open_busy/lost
                # race instead of waiting until the old open window expires.
                eligible_at = (
                    self._clock()
                    if state.state == "half_open"
                    else self._eligible_at(state)
                )
                if eligible_at <= float(deadline):
                    target = (
                        busy_candidates
                        if state.state == "half_open"
                        else open_candidates
                    )
                    target.append((eligible_at, order, provider, state))
            candidates = open_candidates or busy_candidates
            if not candidates:
                return None
            eligible_at, _order, provider, state = min(candidates)
            return ProbeCandidate(provider, state.generation, eligible_at)

    async def cooling_down(self, provider: str) -> bool:
        """Compatibility view for callers that only understand cooldown."""

        async with self._lock:
            state = self._states.get(provider)
            return bool(
                state
                and state.state != "closed"
                and state.open_until > self._clock()
            )

    async def acquire_permit(
        self,
        provider: str,
        *,
        allow_probe: bool = False,
    ) -> CircuitPermit:
        async with self._lock:
            now = self._clock()
            state = self._states.setdefault(provider, _Health())
            if state.state == "closed":
                return CircuitPermit("closed", state.generation)
            if state.state == "half_open":
                return CircuitPermit(
                    "half_open_busy",
                    state.generation,
                    wait_s=max(0.0, state.open_until - now),
                    failure_class=state.failure_class,
                )

            natural_probe = now >= state.open_until
            forced_probe = (
                allow_probe
                and not state.forced_consumed
                and now >= state.next_forced_at
            )
            if not natural_probe and not forced_probe:
                eligible_at = (
                    state.next_forced_at
                    if allow_probe and not state.forced_consumed
                    else state.open_until
                )
                return CircuitPermit(
                    "open",
                    state.generation,
                    wait_s=max(0.0, eligible_at - now),
                    failure_class=state.failure_class,
                )

            token = secrets.token_urlsafe(24)
            if forced_probe and not natural_probe:
                state.forced_consumed = True
            state.state = "half_open"
            state.probe_token = token
            return CircuitPermit(
                "half_open",
                state.generation,
                token=token,
                failure_class=state.failure_class,
            )

    async def record_failure(
        self,
        provider: str,
        failure_class: str = "http",
        *,
        retry_after_s: float | None = None,
        permit: CircuitPermit | None = None,
    ) -> bool:
        async with self._lock:
            state = self._states.setdefault(provider, _Health())
            if permit is not None and (
                permit.kind != "closed"
                or permit.generation != state.generation
                or state.state != "closed"
            ):
                return False
            policy = self._policy(failure_class)
            if state.failure_class == failure_class:
                state.failures += 1
            else:
                state.failure_class = failure_class
                state.failures = 1
            if state.failures < policy.threshold:
                return True
            now = self._clock()
            bounded_retry = min(
                policy.retry_after_max_s,
                max(0.0, float(retry_after_s or 0.0)),
            )
            state.state = "open"
            state.generation += 1
            state.open_until = now + max(policy.open_s, bounded_retry)
            state.next_forced_at = now + max(policy.probe_base_s, bounded_retry)
            state.probe_failures = 0
            state.probe_token = None
            state.forced_consumed = False
            return True

    async def record_success(
        self,
        provider: str,
        *,
        permit: CircuitPermit | None = None,
    ) -> bool:
        async with self._lock:
            previous = self._states.get(provider)
            if permit is not None and previous is not None and (
                permit.kind != "closed"
                or permit.generation != previous.generation
                or previous.state != "closed"
            ):
                return False
            generation = previous.generation if previous is not None else 0
            self._states[provider] = _Health(generation=generation)
            return True

    async def complete_probe(
        self,
        provider: str,
        permit: CircuitPermit,
        outcome: ProbeOutcome,
        *,
        failure_class: str | None = None,
        retry_after_s: float | None = None,
    ) -> bool:
        """Commit a probe outcome iff the generation and lease still match."""

        if not permit.probe_owner:
            return False
        async with self._lock:
            state = self._states.get(provider)
            if (
                state is None
                or state.state != "half_open"
                or state.generation != permit.generation
                or state.probe_token != permit.token
            ):
                return False
            if outcome in {"hit", "empty"}:
                self._states[provider] = _Health(generation=state.generation)
                return True

            now = self._clock()
            state.state = "open"
            state.probe_token = None
            if outcome == "cancel":
                # Cancellation is neither success nor provider failure.  It
                # releases this lease into a fresh generation and rearms a
                # small safety interval.  In particular, a *natural* probe is
                # acquired after ``open_until`` has elapsed; leaving that
                # expired timestamp unchanged would let every following
                # request immediately become the new probe owner.
                policy = self._policy(state.failure_class)
                rearm_at = now + policy.probe_base_s
                state.generation += 1
                state.open_until = max(state.open_until, rearm_at)
                state.next_forced_at = max(state.next_forced_at, rearm_at)
                state.forced_consumed = False
                return True

            effective_class = str(failure_class or state.failure_class or "http")
            policy = self._policy(effective_class)
            state.failure_class = effective_class
            state.failures = max(state.failures, policy.threshold)
            state.probe_failures += 1
            state.generation += 1
            bounded_retry = min(
                policy.retry_after_max_s,
                max(0.0, float(retry_after_s or 0.0)),
            )
            backoff = min(
                policy.probe_max_s,
                policy.probe_base_s * (2 ** state.probe_failures),
            )
            state.open_until = now + max(policy.open_s, bounded_retry)
            state.next_forced_at = now + max(backoff, bounded_retry)
            state.forced_consumed = False
            return True

    async def clear(self) -> None:
        async with self._lock:
            self._states.clear()


__all__ = [
    "AsyncTTLCache",
    "CircuitPermit",
    "CircuitSnapshot",
    "FailurePolicy",
    "ProviderHealth",
    "ProbeCandidate",
]
