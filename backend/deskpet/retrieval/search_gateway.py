"""In-process search orchestration with request-local budgets and shared bulkheads."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Iterable

import httpx

from .cache import AsyncTTLCache, CircuitPermit, FailurePolicy, ProviderHealth
from .budget import (
    DimensionBatchBudgetAllocator,
    QueryBudgetLease,
    SearchBudgetAllocator,
    SearchBudgetCoverageError,
    SingleQueryBudgetAllocator,
)
from .contracts import (
    AttemptStatus,
    DimensionBatchResponse,
    DimensionSearchRequest,
    DimensionSearchResult,
    ProviderAttempt,
    PublicErrorCode,
    RetrievalCandidate,
    SearchBudget,
    SearchRequest,
    SearchResponse,
)
from .providers.base import ProviderFailure, SearchProvider
from .limiter import ProviderLimiter
from .query_terms import extract_query_terms
from .ranking import dedupe_candidates, normalized_host, rank_candidates
from .routing import stable_batches


def _metric(event: str, detail: dict) -> None:
    try:
        from observability.metrics_sink import record
        record(event, detail)
    except Exception:
        pass


_FAILURE_STATUS = {
    PublicErrorCode.TIMEOUT: AttemptStatus.TIMEOUT,
    PublicErrorCode.BLOCKED: AttemptStatus.BLOCKED,
    PublicErrorCode.CAPTCHA: AttemptStatus.CAPTCHA,
    PublicErrorCode.RATE_LIMIT: AttemptStatus.RATE_LIMIT,
    PublicErrorCode.COOLDOWN: AttemptStatus.COOLDOWN,
    PublicErrorCode.HALF_OPEN_BUSY: AttemptStatus.HALF_OPEN_BUSY,
    PublicErrorCode.UNAVAILABLE: AttemptStatus.UNAVAILABLE,
    PublicErrorCode.BUDGET_EXHAUSTED: AttemptStatus.BUDGET_EXHAUSTED,
    PublicErrorCode.QUEUE_TIMEOUT: AttemptStatus.QUEUE_TIMEOUT,
}

_FAILURE_CLASS = {
    PublicErrorCode.TIMEOUT: "timeout",
    PublicErrorCode.BLOCKED: "blocked",
    PublicErrorCode.CAPTCHA: "captcha",
    PublicErrorCode.RATE_LIMIT: "rate_limit",
    PublicErrorCode.HTTP_ERROR: "http",
    PublicErrorCode.PARSE_ERROR: "invalid_response",
    PublicErrorCode.INVALID_RESPONSE: "invalid_response",
}


class SearchGateway:
    def __init__(
        self,
        *,
        config,
        providers: Iterable[SearchProvider],
        client: httpx.AsyncClient | None = None,
        fetch_service=None,
    ) -> None:
        self.config = config
        self.providers = {provider.name: provider for provider in providers}
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 DeskPetSearch/1.0"},
            follow_redirects=True,
        )
        self.fetch_service = fetch_service
        self.cache: AsyncTTLCache[tuple[RetrievalCandidate, ...]] = AsyncTTLCache(
            max_size=config.cache_size, ttl_s=config.cache_ttl_s
        )
        self.health = ProviderHealth(
            policies=self._circuit_policies(config),
            threshold=config.cooldown_threshold,
            cooldown_s=config.cooldown_ttl_s,
        )
        # Circuit state belongs to one research run.  Keeping it process-global
        # made a provider failure in one session suppress healthy calls in the
        # next session ("cooldown 连坐").  The limiter below intentionally stays
        # process-global so concurrent runs still share upstream backpressure.
        self._run_health: dict[str, tuple[float, ProviderHealth]] = {}
        self.limiter = ProviderLimiter(
            self.providers,
            max_concurrency=int(getattr(config, "provider_max_concurrency", 1)),
            max_wait_s=float(getattr(config, "provider_queue_max_wait_s", 8.0)),
        )
        self._google_reachable: bool | None = None
        self._google_probe_lock = asyncio.Lock()
        self._google_probe_task: asyncio.Task[bool] | None = None
        self._closed = False
        self._tasks: set[asyncio.Task] = set()

    def _new_health(self) -> ProviderHealth:
        return ProviderHealth(
            policies=self._circuit_policies(self.config),
            threshold=self.config.cooldown_threshold,
            cooldown_s=self.config.cooldown_ttl_s,
        )

    def _health_for(self, request: SearchRequest) -> ProviderHealth:
        run_id = str(request.run_id or "").strip()
        if not run_id:
            return self.health
        now = time.monotonic()
        current = self._run_health.get(run_id)
        if current is not None:
            self._run_health[run_id] = (now, current[1])
            return current[1]
        # Workflows complete in minutes.  Prune only stale entries so an active
        # run cannot lose its circuit generation while requests are in flight.
        stale_before = now - 3600.0
        self._run_health = {
            key: value for key, value in self._run_health.items()
            if value[0] >= stale_before
        }
        health = self._new_health()
        self._run_health[run_id] = (now, health)
        return health

    def _cache_key(self, request: SearchRequest) -> str:
        parts = [
            request.query.casefold(), request.region or "auto", request.mode,
            str(request.max_results), str(request.hydrate_top),
        ]
        if request.dimension_id is not None or request.query_terms:
            parts.extend((request.dimension_id or "", *request.query_terms))
        return "|".join(parts)

    @staticmethod
    def _circuit_policies(config) -> dict[str, FailurePolicy]:
        legacy_override = (
            int(getattr(config, "cooldown_threshold", 2)) != 2
            or float(getattr(config, "cooldown_ttl_s", 300.0)) != 300.0
        )

        def policy(name: str, defaults: tuple[int, float, float, float], *, retry_max: float = 3600.0) -> FailurePolicy:
            threshold, open_s, probe_base_s, probe_max_s = defaults
            if legacy_override:
                threshold = int(config.cooldown_threshold)
                open_s = float(config.cooldown_ttl_s)
            else:
                threshold = int(getattr(config, f"circuit_{name}_threshold", threshold))
                open_s = float(getattr(config, f"circuit_{name}_open_s", open_s))
            probe_base_s = float(getattr(config, f"circuit_{name}_probe_base_s", probe_base_s))
            probe_max_s = float(getattr(config, f"circuit_{name}_probe_max_s", probe_max_s))
            return FailurePolicy(threshold, open_s, probe_base_s, probe_max_s, retry_max)

        return {
            "timeout": policy("timeout", (2, 30.0, 2.0, 30.0)),
            "http": policy("http", (2, 30.0, 2.0, 30.0)),
            "invalid_response": policy("invalid_response", (2, 30.0, 2.0, 30.0)),
            "blocked": policy("blocked", (1, 300.0, 15.0, 120.0)),
            "captcha": policy("captcha", (1, 600.0, 30.0, 300.0)),
            "rate_limit": policy(
                "rate_limit",
                (1, 60.0, 15.0, 120.0),
                retry_max=float(getattr(config, "circuit_rate_limit_retry_after_max_s", 300.0)),
            ),
        }

    async def search(self, request: SearchRequest) -> SearchResponse:
        """Compatibility single-query API backed by one explicit allocator."""

        total_timeout = request.total_timeout_s or (
            self.config.quick_total_timeout_s
            if request.mode == "quick"
            else self.config.research_total_timeout_s
        )
        configured_count = max(1, len(self.config.providers))
        allocator = SingleQueryBudgetAllocator(
            deadline=time.monotonic() + total_timeout,
            query_slots=1,
            provider_call_slots=configured_count + 1,
            # Preserve legacy multi-provider diversity before final max_results.
            result_slots=request.max_results * (configured_count + 1),
            cdp_slots=self.config.request_cdp_budget,
            hydrate_slots=min(
                request.hydrate_top, self.config.request_hydrate_budget
            ),
        )
        lease = await allocator.reserve_query(
            dimension_id="quick",
            query_key=request.request_id,
            core=False,
        )
        if lease is None:
            raise SearchBudgetCoverageError("single-query budget could not be reserved")
        try:
            return await self._execute_query(request, lease, allocator)
        finally:
            await allocator.complete_query(lease)

    async def search_dimension_batch(
        self,
        requests: Iterable[DimensionSearchRequest],
        *,
        total_timeout_s: float | None = None,
        query_slots: int | None = None,
        provider_call_slots: int | None = None,
        result_slots: int | None = None,
        cdp_slots: int | None = None,
        hydrate_slots: int | None = None,
        min_results_per_core: int = 1,
    ) -> DimensionBatchResponse:
        """Execute a core-first dimension round on one parent allocator."""

        raw_items = tuple(requests)
        if not raw_items:
            return DimensionBatchResponse((), {"remaining": {}})
        seen_query_keys: set[str] = set()
        deduplicated: list[DimensionSearchRequest] = []
        for item in raw_items:
            if item.budget_query_key in seen_query_keys:
                continue
            seen_query_keys.add(item.budget_query_key)
            deduplicated.append(item)
        items = tuple(deduplicated)
        if min_results_per_core < 0:
            raise ValueError("min_results_per_core must be non-negative")
        timeout = (
            float(total_timeout_s)
            if total_timeout_s is not None
            else float(self.config.research_total_timeout_s)
        )
        query_cap = len(items) if query_slots is None else int(query_slots)
        provider_cap = (
            query_cap * (max(1, len(self.config.providers)) + 1)
            if provider_call_slots is None
            else int(provider_call_slots)
        )
        result_cap = (
            sum(item.request.max_results for item in items)
            if result_slots is None
            else int(result_slots)
        )
        allocator = DimensionBatchBudgetAllocator(
            deadline=time.monotonic() + timeout,
            query_slots=query_cap,
            provider_call_slots=provider_cap,
            result_slots=result_cap,
            cdp_slots=(
                int(self.config.request_cdp_budget)
                if cdp_slots is None
                else int(cdp_slots)
            ),
            hydrate_slots=(
                int(self.config.request_hydrate_budget)
                if hydrate_slots is None
                else int(hydrate_slots)
            ),
        )

        dimension_order: list[str] = []
        queues: dict[str, list[DimensionSearchRequest]] = {}
        for item in items:
            if item.dimension_id not in queues:
                dimension_order.append(item.dimension_id)
                queues[item.dimension_id] = []
            queues[item.dimension_id].append(item)
        for queue in queues.values():
            queue.sort(key=lambda item: item.priority, reverse=True)

        first_core: list[DimensionSearchRequest] = []
        for dimension_id in dimension_order:
            queue = queues[dimension_id]
            core_index = next(
                (index for index, item in enumerate(queue) if item.core), None
            )
            if core_index is not None:
                first_core.append(queue.pop(core_index))
        core_leases = await allocator.reserve_core_round(
            [
                (item.dimension_id, item.budget_query_key)
                for item in first_core
            ],
            min_results_per_core=min_results_per_core,
        )
        scheduled: list[
            tuple[DimensionSearchRequest, QueryBudgetLease]
        ] = list(zip(first_core, core_leases, strict=True))

        exhausted = False
        while not exhausted and any(queues.values()):
            progressed = False
            for dimension_id in dimension_order:
                queue = queues[dimension_id]
                if not queue:
                    continue
                item = queue.pop(0)
                lease = await allocator.reserve_query(
                    dimension_id=item.dimension_id,
                    query_key=item.budget_query_key,
                    core=item.core,
                )
                if lease is None:
                    exhausted = True
                    break
                scheduled.append((item, lease))
                progressed = True
            if not progressed:
                break

        results: list[DimensionSearchResult] = []
        for item, lease in scheduled:
            request = replace(
                item.request,
                mode="research",
                dimension_id=item.dimension_id,
                query_terms=(
                    item.query_terms or extract_query_terms(item.request.query)
                ),
            )
            try:
                response = await self._execute_query(request, lease, allocator)
            finally:
                await allocator.complete_query(lease)
            results.append(DimensionSearchResult(item.dimension_id, response))
        return DimensionBatchResponse(tuple(results), await allocator.snapshot())

    async def _execute_query(
        self,
        request: SearchRequest,
        budget_lease: QueryBudgetLease,
        allocator: SearchBudgetAllocator,
    ) -> SearchResponse:
        if self._closed:
            raise RuntimeError("search gateway is closed")
        started = time.monotonic()
        if not request.query:
            await allocator.commit_query(budget_lease)
            response = SearchResponse(
                query="",
                results=(),
                degraded=True,
                request_id=request.request_id,
                run_id=request.run_id,
            )
            await allocator.validate_attempt_coverage(
                budget_lease, response.attempts
            )
            return response
        if not self.config.enabled:
            attempt = ProviderAttempt(
                "gateway",
                AttemptStatus.UNAVAILABLE,
                0,
                public_error_code=PublicErrorCode.UNAVAILABLE,
            )
            await allocator.commit_query(budget_lease)
            response = SearchResponse(
                request.query,
                (),
                (attempt,),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                degraded=True,
                request_id=request.request_id,
                run_id=request.run_id,
            )
            await allocator.validate_attempt_coverage(
                budget_lease, response.attempts
            )
            return response
        cache_key = self._cache_key(request)
        cached = await self.cache.get(cache_key)
        if cached is not None:
            accepted = await allocator.commit_cache_hit(
                budget_lease, len(cached)
            )
            cached = cached[:accepted]
            attempt = ProviderAttempt(
                "gateway", AttemptStatus.CACHE_HIT, 0, len(cached)
            )
            _metric("search_gateway_cache", {
                "request_id": request.request_id,
                "run_id": request.run_id,
                "cache_hit": True,
                "count": len(cached),
                "mode": request.mode,
            })
            response = SearchResponse(
                request.query,
                cached,
                (attempt,),
                cache_hit=True,
                request_id=request.request_id,
                run_id=request.run_id,
            )
            await allocator.validate_attempt_coverage(
                budget_lease, response.attempts
            )
            return response
        _metric("search_gateway_cache", {
            "request_id": request.request_id,
            "run_id": request.run_id,
            "cache_hit": False,
            "mode": request.mode,
        })

        budget = SearchBudget(
            deadline=budget_lease.deadline,
            provider_concurrency=min(2, self.config.max_concurrency) if request.mode == "quick" else self.config.max_concurrency,
            per_provider_timeout_s=self.config.per_provider_timeout_s,
            cdp_remaining=0,
            hydrate_remaining=0,
            parent_lease=budget_lease,
        )
        region = request.region or self._region(request.query)
        request = replace(request, region=region)
        attempts: list[ProviderAttempt] = []
        candidates: list[RetrievalCandidate] = []
        configured = [name for name in self.config.providers if name in self.providers]
        availability = await self._provider_availability(configured)
        available_names = [name for name in configured if availability.get(name, False)]
        probe_candidate = await self._all_open_probe_candidate(available_names, request)
        probe_task: asyncio.Task[bool] | None = None
        if "google-cdp" in configured and self._google_reachable is None:
            probe_task = asyncio.create_task(
                self._ensure_google_reachable(),
                name=f"search-probe-{request.request_id}",
            )
            self._track(probe_task)

        batches = stable_batches(
            request.query, configured,
            searxng="searxng" in configured,
            google_reachable=self._google_reachable is True,
        )
        executed: set[str] = set()
        try:
            for index, batch in enumerate(batches):
                if budget.remaining_s <= 0:
                    break
                batch = [name for name in batch if name not in executed]
                if batch:
                    rows, batch_attempts = await self._run_batch(
                        batch,
                        request,
                        budget,
                        budget_lease,
                        allocator,
                        availability=availability,
                        probe_candidate=probe_candidate,
                    )
                    candidates.extend(rows)
                    attempts.extend(batch_attempts)
                    executed.update(batch)
                    before = len(candidates)
                    candidates = dedupe_candidates(candidates)
                    _metric("search_gateway_dedupe", {
                        "candidates": before, "kept": len(candidates),
                        "dropped": before - len(candidates),
                        "domains": len({normalized_host(c.canonical_url) for c in candidates}),
                    })
                if self._enough(request, candidates):
                    break
                if index == 0 and probe_task is not None:
                    try:
                        self._google_reachable = await probe_task
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self._google_reachable = False
                    if self._google_reachable and "google-cdp" in configured and "google-cdp" not in executed:
                        probe_wave = ["google-cdp"]
                        if "bing-cdp" in configured and "bing-cdp" not in executed:
                            probe_wave.append("bing-cdp")
                        rows, batch_attempts = await self._run_batch(
                            probe_wave,
                            request,
                            budget,
                            budget_lease,
                            allocator,
                            availability=availability,
                            probe_candidate=probe_candidate,
                        )
                        candidates.extend(rows)
                        attempts.extend(batch_attempts)
                        executed.update(probe_wave)
                        candidates = dedupe_candidates(candidates)
                        if self._enough(request, candidates):
                            break
            # A configured provider must never disappear merely because routing
            # filtered an unavailable/cooldown item from its preferred wave.
            leftovers = [name for name in configured if name not in executed]
            for start in range(0, len(leftovers), budget.provider_concurrency):
                if budget.remaining_s <= 0 or self._enough(request, candidates):
                    break
                batch = leftovers[start:start + budget.provider_concurrency]
                rows, batch_attempts = await self._run_batch(
                    batch,
                    request,
                    budget,
                    budget_lease,
                    allocator,
                    availability=availability,
                    probe_candidate=probe_candidate,
                )
                candidates.extend(rows)
                attempts.extend(batch_attempts)
                candidates = dedupe_candidates(candidates)
        finally:
            if probe_task is not None and not probe_task.done():
                probe_task.cancel()
                await asyncio.gather(probe_task, return_exceptions=True)

        rescue_status = "not_needed"
        rescue_error_code: PublicErrorCode | None = None
        rescue_upstream_called = False
        if not candidates:
            (
                rescue_rows,
                rescue_attempt,
                rescue_status,
                rescue_error_code,
                rescue_upstream_called,
            ) = await self._run_rescue(
                request,
                budget,
                budget_lease,
                allocator,
                configured=configured,
                availability=availability,
                attempts=attempts,
            )
            if rescue_attempt is not None:
                attempts.append(rescue_attempt)
            if rescue_rows:
                candidates.extend(rescue_rows)

        ranked = rank_candidates(
            request.query,
            dedupe_candidates(candidates),
            query_terms=request.query_terms or None,
            dimension_id=request.dimension_id,
        )[:request.max_results]
        if request.hydrate_top and self.fetch_service is not None:
            ranked = await self._hydrate(ranked, request, budget)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        degraded = not self._enough(request, ranked) or any(
            a.status not in {AttemptStatus.HIT, AttemptStatus.CACHE_HIT} for a in attempts
        )
        response = SearchResponse(
            request.query,
            tuple(ranked),
            tuple(attempts),
            elapsed_ms,
            False,
            degraded,
            request.request_id,
            request.run_id,
            rescue_status,
            rescue_error_code,
            rescue_upstream_called,
        )
        await allocator.commit_query(budget_lease)
        await allocator.validate_attempt_coverage(
            budget_lease, response.attempts
        )
        _metric("search_gateway_request", {
            "request_id": request.request_id,
            "run_id": request.run_id,
            "mode": request.mode, "duration_ms": elapsed_ms,
            "count": len(ranked), "degraded": degraded,
        })
        if ranked:
            await self.cache.put(cache_key, response.results)
        return response

    async def _run_rescue(
        self,
        request: SearchRequest,
        budget: SearchBudget,
        budget_lease: QueryBudgetLease,
        allocator: SearchBudgetAllocator,
        *,
        configured: list[str],
        availability: dict[str, bool],
        attempts: list[ProviderAttempt],
    ) -> tuple[
        list[RetrievalCandidate],
        ProviderAttempt | None,
        str,
        PublicErrorCode | None,
        bool,
    ]:
        """Execute at most one empty-aware rescue for this request."""

        if (
            not bool(getattr(self.config, "empty_rescue_enabled", True))
            or int(getattr(self.config, "empty_rescue_max_per_request", 1)) < 1
        ):
            return [], None, "not_needed", None, False
        if budget.remaining_s <= 0:
            return [], None, "deadline_exhausted", None, False

        available_names = [
            name for name in configured if availability.get(name, False)
        ]
        latest = {attempt.provider: attempt for attempt in attempts}
        health = self._health_for(request)
        snapshots = {name: await health.snapshot(name) for name in available_names}
        closed_names = [
            name for name, snapshot in snapshots.items() if snapshot.state == "closed"
        ]
        if not closed_names or any(
            latest.get(name) is None
            or latest[name].status is not AttemptStatus.EMPTY
            or not latest[name].upstream_called
            for name in closed_names
        ):
            return [], None, "ineligible", None, False
        non_closed = [
            name for name, snapshot in snapshots.items() if snapshot.state != "closed"
        ]
        if not non_closed:
            return [], None, "ineligible", None, False

        candidate = await health.select_probe_candidate(
            non_closed,
            budget.deadline,
        )
        if candidate is None:
            if any(
                snapshot.eligible_at > budget.deadline
                for name, snapshot in snapshots.items()
                if name in non_closed
            ):
                return [], None, "deadline_exhausted", None, False
            return [], None, "ineligible", None, False

        wait_s = max(0.0, candidate.eligible_at - time.monotonic())
        if wait_s >= budget.remaining_s:
            return [], None, "deadline_exhausted", None, False
        if wait_s > 0:
            try:
                await asyncio.sleep(wait_s)
            except asyncio.CancelledError:
                _metric("search_gateway_cancelled_wait", {
                    "request_id": request.request_id,
                    "run_id": request.run_id,
                    "provider": candidate.provider,
                    "status": "cancelled_rescue_eligibility",
                    "is_rescue": True,
                })
                raise

        rows, attempt = await self._run_provider(
            candidate.provider,
            request,
            budget,
            budget_lease,
            allocator,
            available=True,
            allow_probe=True,
            is_rescue=True,
            expected_generation=candidate.generation,
        )
        if attempt.upstream_called:
            if attempt.status is AttemptStatus.HIT:
                status = "hit"
            elif attempt.status is AttemptStatus.EMPTY:
                status = "empty"
            else:
                status = "failed"
        elif attempt.status in {
            AttemptStatus.COOLDOWN,
            AttemptStatus.HALF_OPEN_BUSY,
        }:
            status = "lost_race"
        elif attempt.status in {
            AttemptStatus.QUEUE_TIMEOUT,
            AttemptStatus.BUDGET_EXHAUSTED,
        }:
            status = "deadline_exhausted"
        else:
            status = "ineligible"
        return (
            rows,
            attempt,
            status,
            attempt.public_error_code,
            attempt.upstream_called,
        )

    async def _provider_availability(self, names: list[str]) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for name in names:
            try:
                result[name] = bool(await self.providers[name].is_available())
            except asyncio.CancelledError:
                raise
            except Exception:
                result[name] = False
        return result

    async def _all_open_probe_candidate(
        self,
        names: list[str],
        request: SearchRequest | None = None,
    ) -> str | None:
        if not names:
            return None
        health = self._health_for(request) if request is not None else self.health
        snapshots = [await health.snapshot(name) for name in names]
        if any(snapshot.state == "closed" for snapshot in snapshots):
            return None
        order = {name: index for index, name in enumerate(self.config.providers)}
        return min(
            zip(names, snapshots, strict=True),
            key=lambda item: (item[1].eligible_at, order.get(item[0], len(order))),
        )[0]

    async def _ensure_google_reachable(self) -> bool:
        async with self._google_probe_lock:
            if self._google_reachable is not None:
                return self._google_reachable
            task = self._google_probe_task
            if task is None:
                google = self.providers.get("google-cdp")
                probe = getattr(google, "probe", None)
                if probe is None:
                    self._google_reachable = False
                    return False
                task = asyncio.create_task(probe(self.client), name="search-google-reachability")
                self._google_probe_task = task
                self._track(task)
        try:
            result = bool(await asyncio.shield(task))
        except asyncio.CancelledError:
            raise
        except Exception:
            result = False
        async with self._google_probe_lock:
            if self._google_probe_task is task:
                self._google_reachable = result
                self._google_probe_task = None
        return result

    async def _run_batch(
        self,
        names: list[str],
        request: SearchRequest,
        budget: SearchBudget,
        budget_lease: QueryBudgetLease,
        allocator: SearchBudgetAllocator,
        *,
        availability: dict[str, bool],
        probe_candidate: str | None,
    ):
        tasks = [
            asyncio.create_task(
                self._run_provider(
                    name,
                    request,
                    budget,
                    budget_lease,
                    allocator,
                    available=availability.get(name, False),
                    allow_probe=name == probe_candidate,
                ),
                name=f"search-{request.request_id}-{name}",
            )
            for name in names
        ]
        for task in tasks: self._track(task)
        try:
            results = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        rows: list[RetrievalCandidate] = []
        attempts: list[ProviderAttempt] = []
        for found, attempt in results:
            rows.extend(found)
            attempts.append(attempt)
        return rows, attempts

    def _attempt_metric(
        self,
        request: SearchRequest,
        *,
        provider: str,
        status: str,
        elapsed_ms: int,
        permit: CircuitPermit,
        count: int = 0,
        error_code: str | None = None,
        probe_outcome: str | None = None,
        failure_class: str | None = None,
        transition: str | None = None,
        upstream_called: bool = False,
        is_rescue: bool = False,
    ) -> None:
        detail = {
            "request_id": request.request_id,
            "run_id": request.run_id,
            "provider": provider,
            "status": status,
            "duration_ms": elapsed_ms,
            "count": count,
            "permit": permit.kind,
            "circuit_generation": permit.generation,
            "probe_outcome": probe_outcome,
            "upstream_called": upstream_called,
            "is_rescue": is_rescue,
        }
        if error_code:
            detail["error_code"] = error_code
        if failure_class:
            detail["failure_class"] = failure_class
        if transition:
            detail["transition"] = transition
        _metric("search_gateway_attempt", detail)

    async def _circuit_metric(
        self,
        request: SearchRequest,
        *,
        provider: str,
        permit: CircuitPermit,
        before_state: str,
        status: str,
        failure_class: str | None = None,
        probe_outcome: str | None = None,
    ) -> str:
        """Emit a privacy-safe circuit transition and return the new state."""

        after = await self._health_for(request).snapshot(provider)
        effective_class = failure_class or after.failure_class or permit.failure_class
        detail = {
            "request_id": request.request_id,
            "run_id": request.run_id,
            "provider": provider,
            "status": status,
            "permit": permit.kind,
            "circuit_generation": after.generation,
            "transition": f"{before_state}_to_{after.state}",
            "probe_outcome": probe_outcome,
        }
        if effective_class:
            detail["failure_class"] = effective_class
        _metric("search_gateway_cooldown", detail)
        return after.state

    async def _run_provider(
        self,
        name: str,
        request: SearchRequest,
        budget: SearchBudget,
        budget_lease: QueryBudgetLease,
        allocator: SearchBudgetAllocator,
        *,
        available: bool,
        allow_probe: bool,
        is_rescue: bool = False,
        expected_generation: int | None = None,
    ):
        started = time.monotonic()
        try:
            lease = await self.limiter.acquire(name, budget.deadline)
        except asyncio.CancelledError:
            _metric("search_gateway_cancelled_wait", {
                "request_id": request.request_id,
                "run_id": request.run_id,
                "provider": name,
                "status": "cancelled",
                "is_rescue": is_rescue,
            })
            raise
        if lease is None:
            elapsed = int((time.monotonic() - started) * 1000)
            permit = CircuitPermit("closed", 0)
            self._attempt_metric(
                request,
                provider=name,
                status=AttemptStatus.QUEUE_TIMEOUT.value,
                elapsed_ms=elapsed,
                permit=permit,
                error_code=PublicErrorCode.QUEUE_TIMEOUT.value,
                is_rescue=is_rescue,
            )
            return [], ProviderAttempt(
                name,
                AttemptStatus.QUEUE_TIMEOUT,
                elapsed,
                public_error_code=PublicErrorCode.QUEUE_TIMEOUT,
                upstream_called=False,
                is_rescue=is_rescue,
            )
        try:
            return await self._run_provider_in_slot(
                name,
                request,
                budget,
                budget_lease,
                allocator,
                available=available,
                allow_probe=allow_probe,
                is_rescue=is_rescue,
                expected_generation=expected_generation,
                started=started,
            )
        finally:
            lease.release()

    async def _run_provider_in_slot(
        self,
        name: str,
        request: SearchRequest,
        budget: SearchBudget,
        budget_lease: QueryBudgetLease,
        allocator: SearchBudgetAllocator,
        *,
        available: bool,
        allow_probe: bool,
        is_rescue: bool,
        expected_generation: int | None,
        started: float,
    ):
        provider = self.providers[name]
        health = self._health_for(request)
        if available:
            try:
                available = bool(await provider.is_available())
            except asyncio.CancelledError:
                raise
            except Exception:
                available = False
        if not available:
            permit = CircuitPermit("closed", 0)
            self._attempt_metric(
                request, provider=name, status="unavailable", elapsed_ms=0,
                permit=permit, error_code="unavailable", is_rescue=is_rescue,
            )
            return [], ProviderAttempt(
                name, AttemptStatus.UNAVAILABLE, 0,
                public_error_code=PublicErrorCode.UNAVAILABLE,
                upstream_called=False,
                is_rescue=is_rescue,
            )

        before_permit = await health.snapshot(name)
        if (
            expected_generation is not None
            and (
                before_permit.generation != expected_generation
                or before_permit.state == "closed"
            )
        ):
            busy = before_permit.state == "half_open"
            status = AttemptStatus.HALF_OPEN_BUSY if busy else AttemptStatus.COOLDOWN
            code = PublicErrorCode.HALF_OPEN_BUSY if busy else PublicErrorCode.COOLDOWN
            permit = CircuitPermit(
                "half_open_busy" if busy else "open",
                before_permit.generation,
                failure_class=before_permit.failure_class,
            )
            self._attempt_metric(
                request,
                provider=name,
                status=status.value,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                permit=permit,
                error_code=code.value,
                failure_class=before_permit.failure_class,
                is_rescue=is_rescue,
            )
            return [], ProviderAttempt(
                name,
                status,
                int((time.monotonic() - started) * 1000),
                public_error_code=code,
                permit=permit.kind,
                circuit_generation=permit.generation,
                upstream_called=False,
                is_rescue=is_rescue,
            )
        permit = await health.acquire_permit(name, allow_probe=allow_probe)
        if permit.kind == "open" and allow_probe and permit.wait_s > 0:
            if permit.wait_s < budget.remaining_s:
                margin = min(
                    0.001,
                    max(0.0, (budget.remaining_s - permit.wait_s) / 2),
                )
                await asyncio.sleep(permit.wait_s + margin)
                permit = await health.acquire_permit(name, allow_probe=True)
        if permit.kind != "closed":
            await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state=before_permit.state,
                status=permit.kind,
                failure_class=permit.failure_class,
            )
        if permit.kind in {"open", "half_open_busy"}:
            busy = permit.kind == "half_open_busy"
            status = AttemptStatus.HALF_OPEN_BUSY if busy else AttemptStatus.COOLDOWN
            code = PublicErrorCode.HALF_OPEN_BUSY if busy else PublicErrorCode.COOLDOWN
            self._attempt_metric(
                request, provider=name, status=status.value, elapsed_ms=0,
                permit=permit, error_code=code.value,
                failure_class=permit.failure_class,
                is_rescue=is_rescue,
            )
            return [], ProviderAttempt(
                name,
                status,
                0,
                public_error_code=code,
                permit=permit.kind,
                circuit_generation=permit.generation,
                upstream_called=False,
                is_rescue=is_rescue,
            )
        timeout = min(budget.per_provider_timeout_s, budget.remaining_s)
        if timeout <= 0:
            if permit.probe_owner:
                await health.complete_probe(name, permit, "cancel")
                transition = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state="half_open",
                    status="budget_exhausted",
                    failure_class=permit.failure_class,
                    probe_outcome="cancel",
                )
                self._attempt_metric(
                    request,
                    provider=name,
                    status="budget_exhausted",
                    elapsed_ms=0,
                    permit=permit,
                    error_code="budget_exhausted",
                    probe_outcome="cancel",
                    failure_class=permit.failure_class,
                    transition=f"half_open_to_{transition}",
                    is_rescue=is_rescue,
                )
            return [], ProviderAttempt(
                name, AttemptStatus.BUDGET_EXHAUSTED, 0,
                public_error_code=PublicErrorCode.BUDGET_EXHAUSTED,
                permit=permit.kind,
                circuit_generation=permit.generation,
                upstream_called=False,
                is_rescue=is_rescue,
            )
        budget_attempt = await allocator.claim_provider_attempt(
            budget_lease,
            provider=name,
            generation=permit.generation,
            is_rescue=is_rescue,
        )
        if budget_attempt is None:
            if permit.probe_owner:
                await health.complete_probe(name, permit, "cancel")
            return [], ProviderAttempt(
                name,
                AttemptStatus.BUDGET_EXHAUSTED,
                int((time.monotonic() - started) * 1000),
                public_error_code=PublicErrorCode.BUDGET_EXHAUSTED,
                permit=permit.kind,
                circuit_generation=permit.generation,
                upstream_called=False,
                is_rescue=is_rescue,
            )

        async def commit_transport(
            rows: list[RetrievalCandidate], attempt: ProviderAttempt
        ) -> tuple[list[RetrievalCandidate], ProviderAttempt]:
            accepted = await allocator.commit_provider_attempt(
                budget_lease,
                budget_attempt.attempt_id,
                outcome=attempt.status.value,
                result_count=len(rows),
            )
            return rows[:accepted], replace(
                attempt,
                result_count=accepted,
                upstream_called=True,
                budget_attempt_id=budget_attempt.attempt_id,
            )
        try:
            rows = await asyncio.wait_for(provider.search(request, budget, self.client), timeout=timeout)
            elapsed = int((time.monotonic() - started) * 1000)
            if rows:
                if permit.probe_owner:
                    await health.complete_probe(name, permit, "hit")
                    after_state = await self._circuit_metric(
                        request,
                        provider=name,
                        permit=permit,
                        before_state="half_open",
                        status="hit",
                        failure_class=permit.failure_class,
                        probe_outcome="hit",
                    )
                else:
                    await health.record_success(name, permit=permit)
                    after_state = "closed"
                self._attempt_metric(
                    request, provider=name, status="hit", elapsed_ms=elapsed,
                    count=len(rows), permit=permit,
                    probe_outcome="hit" if permit.probe_owner else None,
                    failure_class=permit.failure_class,
                    transition=f"half_open_to_{after_state}" if permit.probe_owner else None,
                    upstream_called=True,
                    is_rescue=is_rescue,
                )
                return await commit_transport(
                    rows,
                    ProviderAttempt(
                        name, AttemptStatus.HIT, elapsed, len(rows),
                        permit=permit.kind,
                        circuit_generation=permit.generation,
                        probe_outcome="hit" if permit.probe_owner else None,
                        upstream_called=True,
                        is_rescue=is_rescue,
                    ),
                )
            if permit.probe_owner:
                await health.complete_probe(name, permit, "empty")
                after_state = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state="half_open",
                    status="empty",
                    failure_class=permit.failure_class,
                    probe_outcome="empty",
                )
            else:
                await health.record_success(name, permit=permit)
                after_state = "closed"
            self._attempt_metric(
                request, provider=name, status="empty", elapsed_ms=elapsed,
                permit=permit,
                probe_outcome="empty" if permit.probe_owner else None,
                failure_class=permit.failure_class,
                transition=f"half_open_to_{after_state}" if permit.probe_owner else None,
                upstream_called=True,
                is_rescue=is_rescue,
            )
            return await commit_transport(
                [],
                ProviderAttempt(
                    name, AttemptStatus.EMPTY, elapsed,
                    permit=permit.kind,
                    circuit_generation=permit.generation,
                    probe_outcome="empty" if permit.probe_owner else None,
                    upstream_called=True,
                    is_rescue=is_rescue,
                ),
            )
        except asyncio.CancelledError:
            if permit.probe_owner:
                await health.complete_probe(name, permit, "cancel")
                after_state = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state="half_open",
                    status="cancelled",
                    failure_class=permit.failure_class,
                    probe_outcome="cancel",
                )
                self._attempt_metric(
                    request,
                    provider=name,
                    status="cancelled",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    permit=permit,
                    probe_outcome="cancel",
                    failure_class=permit.failure_class,
                    transition=f"half_open_to_{after_state}",
                    upstream_called=True,
                    is_rescue=is_rescue,
                )
            else:
                self._attempt_metric(
                    request,
                    provider=name,
                    status="cancelled",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    permit=permit,
                    upstream_called=True,
                    is_rescue=is_rescue,
                )
            await asyncio.shield(
                allocator.commit_provider_attempt(
                    budget_lease,
                    budget_attempt.attempt_id,
                    outcome="cancelled",
                    result_count=0,
                )
            )
            raise
        except asyncio.TimeoutError:
            if permit.probe_owner:
                await health.complete_probe(
                    name, permit, "failure", failure_class="timeout",
                )
                after_state = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state="half_open",
                    status="timeout",
                    failure_class="timeout",
                    probe_outcome="failure",
                )
            else:
                await health.record_failure(name, "timeout", permit=permit)
                after_state = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state="closed",
                    status="timeout",
                    failure_class="timeout",
                )
            elapsed = int((time.monotonic() - started) * 1000)
            self._attempt_metric(
                request, provider=name, status="timeout", elapsed_ms=elapsed,
                permit=permit, error_code="timeout",
                probe_outcome="failure" if permit.probe_owner else None,
                failure_class="timeout",
                transition=f"{'half_open' if permit.probe_owner else 'closed'}_to_{after_state}",
                upstream_called=True,
                is_rescue=is_rescue,
            )
            return await commit_transport(
                [],
                ProviderAttempt(
                    name, AttemptStatus.TIMEOUT, elapsed,
                    public_error_code=PublicErrorCode.TIMEOUT,
                    permit=permit.kind,
                    circuit_generation=permit.generation,
                    probe_outcome="failure" if permit.probe_owner else None,
                    upstream_called=True,
                    is_rescue=is_rescue,
                ),
            )
        except ProviderFailure as exc:
            failure_class = _FAILURE_CLASS.get(exc.code)
            if failure_class is not None:
                if permit.probe_owner:
                    await health.complete_probe(
                        name,
                        permit,
                        "failure",
                        failure_class=failure_class,
                        retry_after_s=exc.retry_after_s,
                    )
                    before_state = "half_open"
                else:
                    await health.record_failure(
                        name,
                        failure_class,
                        retry_after_s=exc.retry_after_s,
                        permit=permit,
                    )
                    before_state = "closed"
                after_state = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state=before_state,
                    status=_FAILURE_STATUS.get(exc.code, AttemptStatus.ERROR).value,
                    failure_class=failure_class,
                    probe_outcome="failure" if permit.probe_owner else None,
                )
            elif permit.probe_owner:
                await health.complete_probe(name, permit, "cancel")
                before_state = "half_open"
                after_state = await self._circuit_metric(
                    request,
                    provider=name,
                    permit=permit,
                    before_state=before_state,
                    status=_FAILURE_STATUS.get(exc.code, AttemptStatus.ERROR).value,
                    failure_class=permit.failure_class,
                    probe_outcome="cancel",
                )
            else:
                before_state = "closed"
                after_state = "closed"
            elapsed = int((time.monotonic() - started) * 1000)
            status = _FAILURE_STATUS.get(exc.code, AttemptStatus.ERROR)
            upstream_called = True
            self._attempt_metric(
                request, provider=name, status=status.value, elapsed_ms=elapsed,
                permit=permit, error_code=exc.code.value,
                probe_outcome="failure" if permit.probe_owner and failure_class else None,
                failure_class=failure_class or permit.failure_class,
                transition=f"{before_state}_to_{after_state}" if failure_class or permit.probe_owner else None,
                upstream_called=upstream_called,
                is_rescue=is_rescue,
            )
            return await commit_transport(
                [],
                ProviderAttempt(
                    name, status, elapsed,
                    public_error_code=exc.code,
                    permit=permit.kind,
                    circuit_generation=permit.generation,
                    probe_outcome="failure" if permit.probe_owner and failure_class else None,
                    upstream_called=upstream_called,
                    is_rescue=is_rescue,
                ),
            )
        except Exception:
            if permit.probe_owner:
                await health.complete_probe(
                    name, permit, "failure", failure_class="http",
                )
                before_state = "half_open"
            else:
                await health.record_failure(name, "http", permit=permit)
                before_state = "closed"
            after_state = await self._circuit_metric(
                request,
                provider=name,
                permit=permit,
                before_state=before_state,
                status="error",
                failure_class="http",
                probe_outcome="failure" if permit.probe_owner else None,
            )
            elapsed = int((time.monotonic() - started) * 1000)
            self._attempt_metric(
                request, provider=name, status="error", elapsed_ms=elapsed,
                permit=permit, error_code="http_error",
                probe_outcome="failure" if permit.probe_owner else None,
                failure_class="http",
                transition=f"{before_state}_to_{after_state}",
                upstream_called=True,
                is_rescue=is_rescue,
            )
            return await commit_transport(
                [],
                ProviderAttempt(
                    name, AttemptStatus.ERROR, elapsed,
                    public_error_code=PublicErrorCode.HTTP_ERROR,
                    permit=permit.kind,
                    circuit_generation=permit.generation,
                    probe_outcome="failure" if permit.probe_owner else None,
                    upstream_called=True,
                    is_rescue=is_rescue,
                ),
            )

    async def _hydrate(self, ranked, request, budget):
        from .contracts import FetchRequest
        hydrated: list[RetrievalCandidate] = []
        for index, item in enumerate(ranked):
            if index >= request.hydrate_top or not await budget.claim_hydrate():
                hydrated.append(item)
                continue
            try:
                document = await self.fetch_service.fetch(FetchRequest(item.url, request_budget=budget))
                hydrated.append(replace(item, text=document.text[:4000]))
            except Exception:
                hydrated.append(replace(item, hydration_error=PublicErrorCode.FETCH_FAILED.value))
        _metric("search_gateway_hydrate", {
            "count": sum(1 for item in hydrated if item.text),
            "dropped": sum(1 for item in hydrated if item.hydration_error),
        })
        return hydrated

    @staticmethod
    def _region(query: str) -> str:
        from deskpet.tools.search_provider import region_for_query
        return region_for_query(query)

    def _enough(self, request: SearchRequest, candidates) -> bool:
        target_results = self.config.quick_min_results if request.mode == "quick" else self.config.research_target_results
        target_domains = self.config.quick_min_domains if request.mode == "quick" else self.config.research_target_domains
        return len(candidates) >= target_results and len({normalized_host(c.canonical_url) for c in candidates}) >= target_domains

    def _track(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        if self._closed: return
        self._closed = True
        pending = list(self._tasks)
        for task in pending: task.cancel()
        if pending: await asyncio.gather(*pending, return_exceptions=True)
        await self.cache.clear()
        await self.health.clear()
        run_health = [value[1] for value in self._run_health.values()]
        self._run_health.clear()
        for health in run_health:
            await health.clear()
        if self.fetch_service is not None:
            await self.fetch_service.close()
        if self._owns_client: await self.client.aclose()

    close = shutdown
