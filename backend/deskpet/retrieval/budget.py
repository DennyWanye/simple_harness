"""Atomic parent budgets shared by quick and dimension-batch search."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass


class SearchBudgetCoverageError(RuntimeError):
    """Allocator facts and public provider accounting disagree."""


@dataclass(frozen=True, slots=True)
class ProviderBudgetAttempt:
    attempt_id: str
    lease_id: str
    dimension_id: str
    provider: str
    generation: int
    is_rescue: bool
    upstream_started: bool
    outcome: str | None = None
    result_count: int = 0


@dataclass(frozen=True, slots=True)
class QueryBudgetLease:
    allocator: "SearchBudgetAllocator"
    lease_id: str
    dimension_id: str
    query_key: str
    deadline: float
    core: bool

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    async def claim_cdp(self) -> bool:
        return await self.allocator.claim_cdp(self)

    async def claim_hydrate(self) -> bool:
        return await self.allocator.claim_hydrate(self)


class SearchBudgetAllocator:
    """One lock owns query/provider/result/CDP/hydrate accounting."""

    def __init__(
        self,
        *,
        deadline: float,
        query_slots: int,
        provider_call_slots: int,
        result_slots: int,
        cdp_slots: int,
        hydrate_slots: int,
    ) -> None:
        values = (
            query_slots,
            provider_call_slots,
            result_slots,
            cdp_slots,
            hydrate_slots,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("search budget slots must be non-negative integers")
        self.deadline = float(deadline)
        self._remaining = {
            "query": query_slots,
            "provider": provider_call_slots,
            "result": result_slots,
            "cdp": cdp_slots,
            "hydrate": hydrate_slots,
        }
        self._lock = asyncio.Lock()
        self._leases: dict[str, QueryBudgetLease] = {}
        self._query_keys: set[str] = set()
        self._query_committed: set[str] = set()
        self._reserved_results: dict[str, int] = {}
        self._committed_results: dict[str, int] = {}
        self._attempts: dict[str, ProviderBudgetAttempt] = {}
        self._rescue_claimed: set[str] = set()

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    async def reserve_query(
        self,
        *,
        dimension_id: str,
        query_key: str,
        core: bool,
        min_results: int = 0,
    ) -> QueryBudgetLease | None:
        if not dimension_id or not query_key:
            raise ValueError("dimension_id and query_key are required")
        if isinstance(min_results, bool) or not isinstance(min_results, int) or min_results < 0:
            raise ValueError("min_results must be a non-negative integer")
        async with self._lock:
            if query_key in self._query_keys:
                raise SearchBudgetCoverageError(
                    f"duplicate query budget key: {query_key}"
                )
            if (
                self.remaining_s <= 0
                or self._remaining["query"] < 1
                or self._remaining["result"] < min_results
            ):
                return None
            lease_id = uuid.uuid4().hex
            lease = QueryBudgetLease(
                self, lease_id, dimension_id, query_key, self.deadline, core
            )
            self._remaining["query"] -= 1
            self._remaining["result"] -= min_results
            self._reserved_results[lease_id] = min_results
            self._committed_results[lease_id] = 0
            self._leases[lease_id] = lease
            self._query_keys.add(query_key)
            return lease

    async def reserve_core_round(
        self,
        items: list[tuple[str, str]],
        *,
        min_results_per_core: int,
    ) -> list[QueryBudgetLease]:
        """Atomically reserve one query and result floor for every core dimension."""

        if (
            isinstance(min_results_per_core, bool)
            or not isinstance(min_results_per_core, int)
            or min_results_per_core < 0
        ):
            raise ValueError("min_results_per_core must be a non-negative integer")
        if len({dimension for dimension, _ in items}) != len(items):
            raise ValueError("core reservation round contains duplicate dimensions")
        query_keys = [query_key for _, query_key in items]
        if len(set(query_keys)) != len(query_keys):
            raise SearchBudgetCoverageError("core reservation contains duplicate query keys")
        required_results = len(items) * min_results_per_core
        async with self._lock:
            if any(query_key in self._query_keys for query_key in query_keys):
                raise SearchBudgetCoverageError("query budget key was already reserved")
            if (
                self.remaining_s <= 0
                or self._remaining["query"] < len(items)
                or self._remaining["result"] < required_results
            ):
                raise SearchBudgetCoverageError(
                    "parent budget cannot reserve every core dimension"
                )
            leases: list[QueryBudgetLease] = []
            for dimension_id, query_key in items:
                lease_id = uuid.uuid4().hex
                lease = QueryBudgetLease(
                    self, lease_id, dimension_id, query_key, self.deadline, True
                )
                self._remaining["query"] -= 1
                self._remaining["result"] -= min_results_per_core
                self._reserved_results[lease_id] = min_results_per_core
                self._committed_results[lease_id] = 0
                self._leases[lease_id] = lease
                self._query_keys.add(query_key)
                leases.append(lease)
            return leases

    def _assert_lease(self, lease: QueryBudgetLease) -> None:
        if lease.allocator is not self or self._leases.get(lease.lease_id) != lease:
            raise SearchBudgetCoverageError("query lease does not belong to allocator")

    def _commit_results_locked(self, lease: QueryBudgetLease, requested: int) -> int:
        reserved = self._reserved_results[lease.lease_id]
        accepted_reserved = min(requested, reserved)
        self._reserved_results[lease.lease_id] -= accepted_reserved
        remaining_request = requested - accepted_reserved
        accepted_shared = min(remaining_request, self._remaining["result"])
        self._remaining["result"] -= accepted_shared
        accepted = accepted_reserved + accepted_shared
        self._committed_results[lease.lease_id] += accepted
        return accepted

    async def commit_cache_hit(
        self, lease: QueryBudgetLease, result_count: int
    ) -> int:
        async with self._lock:
            self._assert_lease(lease)
            self._query_committed.add(lease.lease_id)
            return self._commit_results_locked(lease, max(0, int(result_count)))

    async def commit_query(self, lease: QueryBudgetLease) -> None:
        async with self._lock:
            self._assert_lease(lease)
            self._query_committed.add(lease.lease_id)

    async def complete_query(self, lease: QueryBudgetLease) -> None:
        """Return only this query's unused result floor to the shared pool."""

        async with self._lock:
            self._assert_lease(lease)
            self._query_committed.add(lease.lease_id)
            unused = self._reserved_results[lease.lease_id]
            self._reserved_results[lease.lease_id] = 0
            self._remaining["result"] += unused

    async def claim_provider_attempt(
        self,
        lease: QueryBudgetLease,
        *,
        provider: str,
        generation: int,
        is_rescue: bool,
    ) -> ProviderBudgetAttempt | None:
        async with self._lock:
            self._assert_lease(lease)
            if is_rescue:
                if lease.lease_id in self._rescue_claimed:
                    return None
                self._rescue_claimed.add(lease.lease_id)
            if self.remaining_s <= 0 or self._remaining["provider"] < 1:
                return None
            self._remaining["provider"] -= 1
            attempt = ProviderBudgetAttempt(
                attempt_id=uuid.uuid4().hex,
                lease_id=lease.lease_id,
                dimension_id=lease.dimension_id,
                provider=provider,
                generation=int(generation),
                is_rescue=is_rescue,
                upstream_started=True,
            )
            self._attempts[attempt.attempt_id] = attempt
            return attempt

    async def commit_provider_attempt(
        self,
        lease: QueryBudgetLease,
        attempt_id: str,
        *,
        outcome: str,
        result_count: int,
    ) -> int:
        async with self._lock:
            self._assert_lease(lease)
            attempt = self._attempts.get(attempt_id)
            if attempt is None or attempt.lease_id != lease.lease_id:
                raise SearchBudgetCoverageError("provider attempt is not owned by query lease")
            if attempt.outcome is not None:
                raise SearchBudgetCoverageError("provider attempt was committed twice")
            accepted = self._commit_results_locked(lease, max(0, int(result_count)))
            self._query_committed.add(lease.lease_id)
            self._attempts[attempt_id] = ProviderBudgetAttempt(
                attempt.attempt_id,
                attempt.lease_id,
                attempt.dimension_id,
                attempt.provider,
                attempt.generation,
                attempt.is_rescue,
                True,
                outcome,
                accepted,
            )
            return accepted

    async def claim_cdp(self, lease: QueryBudgetLease) -> bool:
        return await self._claim_aux(lease, "cdp")

    async def claim_hydrate(self, lease: QueryBudgetLease) -> bool:
        return await self._claim_aux(lease, "hydrate")

    async def _claim_aux(self, lease: QueryBudgetLease, slot: str) -> bool:
        async with self._lock:
            self._assert_lease(lease)
            if self.remaining_s <= 0 or self._remaining[slot] < 1:
                return False
            self._remaining[slot] -= 1
            return True

    async def validate_attempt_coverage(
        self, lease: QueryBudgetLease, attempts: tuple[object, ...]
    ) -> None:
        async with self._lock:
            self._assert_lease(lease)
            seen: set[str] = set()
            for public in attempts:
                upstream_called = bool(getattr(public, "upstream_called", False))
                attempt_id = getattr(public, "budget_attempt_id", None)
                allocator_attempt = (
                    self._attempts.get(str(attempt_id)) if attempt_id else None
                )
                allocator_started = bool(
                    allocator_attempt is not None
                    and allocator_attempt.lease_id == lease.lease_id
                    and allocator_attempt.upstream_started
                )
                if upstream_called != allocator_started:
                    raise SearchBudgetCoverageError(
                        "ProviderAttempt.upstream_called disagrees with allocator"
                    )
                if allocator_attempt is not None:
                    if allocator_attempt.attempt_id in seen:
                        raise SearchBudgetCoverageError(
                            "allocator attempt appears more than once"
                        )
                    seen.add(allocator_attempt.attempt_id)
            expected = {
                attempt.attempt_id
                for attempt in self._attempts.values()
                if attempt.lease_id == lease.lease_id
            }
            if seen != expected:
                raise SearchBudgetCoverageError(
                    "allocator upstream attempts are missing from response coverage"
                )

    async def snapshot(self) -> dict[str, object]:
        async with self._lock:
            return {
                "remaining": dict(self._remaining),
                "query_committed": len(self._query_committed),
                "committed_results": dict(self._committed_results),
                "reserved_results": dict(self._reserved_results),
                "attempts": tuple(self._attempts.values()),
            }


class SingleQueryBudgetAllocator(SearchBudgetAllocator):
    """Compatibility allocator used by the existing public quick-search API."""


class DimensionBatchBudgetAllocator(SearchBudgetAllocator):
    """The sole parent allocator for every query in one dimension batch."""


__all__ = [
    "DimensionBatchBudgetAllocator",
    "ProviderBudgetAttempt",
    "QueryBudgetLease",
    "SearchBudgetAllocator",
    "SearchBudgetCoverageError",
    "SingleQueryBudgetAllocator",
]
