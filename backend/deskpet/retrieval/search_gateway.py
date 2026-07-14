"""In-process, request-local asynchronous search orchestration."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Iterable

import httpx

from .cache import AsyncTTLCache, ProviderHealth
from .contracts import (
    AttemptStatus,
    ProviderAttempt,
    PublicErrorCode,
    RetrievalCandidate,
    SearchBudget,
    SearchRequest,
    SearchResponse,
)
from .providers.base import ProviderFailure, SearchProvider
from .ranking import dedupe_candidates, normalized_host, rank_candidates
from .routing import stable_batches


_FAILURE_STATUS = {
    PublicErrorCode.TIMEOUT: AttemptStatus.TIMEOUT,
    PublicErrorCode.BLOCKED: AttemptStatus.BLOCKED,
    PublicErrorCode.CAPTCHA: AttemptStatus.CAPTCHA,
    PublicErrorCode.COOLDOWN: AttemptStatus.COOLDOWN,
    PublicErrorCode.UNAVAILABLE: AttemptStatus.UNAVAILABLE,
    PublicErrorCode.BUDGET_EXHAUSTED: AttemptStatus.BUDGET_EXHAUSTED,
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
            threshold=config.cooldown_threshold, cooldown_s=config.cooldown_ttl_s
        )
        self._google_reachable: bool | None = None
        self._closed = False
        self._tasks: set[asyncio.Task] = set()

    def _cache_key(self, request: SearchRequest) -> str:
        return "|".join((
            request.query.casefold(), request.region or "auto", request.mode,
            str(request.max_results), str(request.hydrate_top),
        ))

    async def search(self, request: SearchRequest) -> SearchResponse:
        if self._closed:
            raise RuntimeError("search gateway is closed")
        started = time.monotonic()
        if not request.query:
            return SearchResponse(query="", results=(), degraded=True)
        cache_key = self._cache_key(request)
        cached = await self.cache.get(cache_key)
        if cached is not None:
            attempt = ProviderAttempt("gateway", AttemptStatus.CACHE_HIT, 0, len(cached))
            return SearchResponse(request.query, cached, (attempt,), cache_hit=True)

        total_timeout = request.total_timeout_s or (
            self.config.quick_total_timeout_s if request.mode == "quick"
            else self.config.research_total_timeout_s
        )
        budget = SearchBudget.create(
            total_timeout_s=total_timeout,
            provider_concurrency=min(2, self.config.max_concurrency) if request.mode == "quick" else self.config.max_concurrency,
            per_provider_timeout_s=self.config.per_provider_timeout_s,
            cdp_budget=self.config.request_cdp_budget,
            hydrate_budget=min(request.hydrate_top, self.config.request_hydrate_budget),
        )
        region = request.region or self._region(request.query)
        request = replace(request, region=region)
        attempts: list[ProviderAttempt] = []
        candidates: list[RetrievalCandidate] = []
        configured = [name for name in self.config.providers if name in self.providers]
        probe_task: asyncio.Task[bool] | None = None
        if "google-cdp" in configured and self._google_reachable is None:
            google = self.providers.get("google-cdp")
            probe = getattr(google, "probe", None)
            if probe is not None:
                probe_task = asyncio.create_task(probe(self.client), name=f"search-probe-{request.request_id}")
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
                    rows, batch_attempts = await self._run_batch(batch, request, budget)
                    candidates.extend(rows)
                    attempts.extend(batch_attempts)
                    executed.update(batch)
                    candidates = dedupe_candidates(candidates)
                if self._enough(request, candidates):
                    break
                if index == 0 and probe_task is not None:
                    try:
                        self._google_reachable = await probe_task
                    except (asyncio.CancelledError, Exception):
                        self._google_reachable = False
                    if self._google_reachable and "google-cdp" in configured and "google-cdp" not in executed:
                        rows, batch_attempts = await self._run_batch(["google-cdp"], request, budget)
                        candidates.extend(rows)
                        attempts.extend(batch_attempts)
                        executed.add("google-cdp")
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
                rows, batch_attempts = await self._run_batch(batch, request, budget)
                candidates.extend(rows)
                attempts.extend(batch_attempts)
                candidates = dedupe_candidates(candidates)
        finally:
            if probe_task is not None and not probe_task.done():
                probe_task.cancel()
                await asyncio.gather(probe_task, return_exceptions=True)

        ranked = rank_candidates(request.query, dedupe_candidates(candidates))[:request.max_results]
        if request.hydrate_top and self.fetch_service is not None:
            ranked = await self._hydrate(ranked, request, budget)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        degraded = not self._enough(request, ranked) or any(
            a.status not in {AttemptStatus.HIT, AttemptStatus.CACHE_HIT} for a in attempts
        )
        response = SearchResponse(request.query, tuple(ranked), tuple(attempts), elapsed_ms, False, degraded)
        if ranked:
            await self.cache.put(cache_key, response.results)
        return response

    async def _run_batch(self, names: list[str], request: SearchRequest, budget: SearchBudget):
        tasks = [asyncio.create_task(self._run_provider(name, request, budget), name=f"search-{request.request_id}-{name}") for name in names]
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

    async def _run_provider(self, name: str, request: SearchRequest, budget: SearchBudget):
        started = time.monotonic()
        provider = self.providers[name]
        if await self.health.cooling_down(name):
            return [], ProviderAttempt(name, AttemptStatus.COOLDOWN, 0, public_error_code=PublicErrorCode.COOLDOWN)
        if not await provider.is_available():
            return [], ProviderAttempt(name, AttemptStatus.UNAVAILABLE, 0, public_error_code=PublicErrorCode.UNAVAILABLE)
        timeout = min(budget.per_provider_timeout_s, budget.remaining_s)
        if timeout <= 0:
            return [], ProviderAttempt(name, AttemptStatus.BUDGET_EXHAUSTED, 0, public_error_code=PublicErrorCode.BUDGET_EXHAUSTED)
        try:
            rows = await asyncio.wait_for(provider.search(request, budget, self.client), timeout=timeout)
            elapsed = int((time.monotonic() - started) * 1000)
            if rows:
                await self.health.record_success(name)
                return rows, ProviderAttempt(name, AttemptStatus.HIT, elapsed, len(rows))
            await self.health.record_failure(name)
            return [], ProviderAttempt(name, AttemptStatus.EMPTY, elapsed)
        except asyncio.TimeoutError:
            await self.health.record_failure(name)
            elapsed = int((time.monotonic() - started) * 1000)
            return [], ProviderAttempt(name, AttemptStatus.TIMEOUT, elapsed, public_error_code=PublicErrorCode.TIMEOUT)
        except ProviderFailure as exc:
            await self.health.record_failure(name)
            elapsed = int((time.monotonic() - started) * 1000)
            return [], ProviderAttempt(name, _FAILURE_STATUS.get(exc.code, AttemptStatus.ERROR), elapsed, public_error_code=exc.code)
        except Exception:
            await self.health.record_failure(name)
            elapsed = int((time.monotonic() - started) * 1000)
            return [], ProviderAttempt(name, AttemptStatus.ERROR, elapsed, public_error_code=PublicErrorCode.HTTP_ERROR)

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
        if self._owns_client: await self.client.aclose()

    close = shutdown
