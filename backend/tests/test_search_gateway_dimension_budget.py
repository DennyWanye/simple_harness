from __future__ import annotations

import time
from dataclasses import replace

import httpx
import pytest

from config import SearchGatewayConfig
from deskpet.retrieval.budget import (
    DimensionBatchBudgetAllocator,
    SearchBudgetCoverageError,
)
from deskpet.retrieval.contracts import (
    AttemptStatus,
    DimensionSearchRequest,
    ProviderAttempt,
    RetrievalCandidate,
    SearchRequest,
)
from deskpet.retrieval.query_terms import extract_query_terms
from deskpet.retrieval.search_gateway import SearchGateway
from deskpet.workflows.contracts import validate_json_value


def _candidate(query: str, index: int) -> RetrievalCandidate:
    url = f"https://source-{index}.test/{query}"
    return RetrievalCandidate(
        stable_id=f"{query}-{index}",
        url=url,
        canonical_url=url,
        title=f"{query} result {index}",
        snippet=f"evidence for {query}",
        provider="duckduckgo",
        providers=("duckduckgo",),
        provider_rank=index,
    )


class _BurstProvider:
    name = "duckduckgo"

    def __init__(self, count: int = 8) -> None:
        self.count = count
        self.calls: list[str] = []

    async def is_available(self) -> bool:
        return True

    async def search(self, request, budget, client):
        self.calls.append(request.query)
        return [_candidate(request.query, index) for index in range(self.count)]


class _FlappingProvider(_BurstProvider):
    def __init__(self) -> None:
        super().__init__(1)
        self.availability_checks = 0

    async def is_available(self) -> bool:
        self.availability_checks += 1
        return self.availability_checks == 1


class _ChineseProvider(_BurstProvider):
    async def search(self, request, budget, client):
        self.calls.append(request.query)
        return [
            _candidate("天气与体育赛事", 0),
            _candidate("小学教育现状与双减课后服务", 1),
        ]


def _gateway(provider: _BurstProvider) -> SearchGateway:
    config = replace(
        SearchGatewayConfig(),
        providers=[provider.name],
        research_target_results=1,
        research_target_domains=1,
        empty_rescue_enabled=False,
    )
    return SearchGateway(
        config=config,
        providers=[provider],
        client=httpx.AsyncClient(),
    )


@pytest.mark.asyncio
async def test_core_round_robin_reserves_result_floor_before_shared_remainder() -> None:
    provider = _BurstProvider()
    gateway = _gateway(provider)
    requests = [
        DimensionSearchRequest(
            "dimension-a",
            SearchRequest("a-first", max_results=8, mode="research"),
            priority=10,
        ),
        DimensionSearchRequest(
            "dimension-a",
            SearchRequest("a-second", max_results=8, mode="research"),
            priority=1,
        ),
        DimensionSearchRequest(
            "dimension-b",
            SearchRequest("b-first", max_results=8, mode="research"),
            priority=10,
        ),
        DimensionSearchRequest(
            "dimension-b",
            SearchRequest("b-second", max_results=8, mode="research"),
            priority=1,
        ),
    ]
    try:
        batch = await gateway.search_dimension_batch(
            requests,
            query_slots=4,
            provider_call_slots=4,
            result_slots=4,
            min_results_per_core=2,
        )
    finally:
        await gateway.close()

    # One floor-bearing query per core runs first, then remaining work is
    # scheduled dimension round-robin. The first producer cannot consume B's floor.
    assert provider.calls == ["a-first", "b-first", "a-second", "b-second"]
    assert [item.response.count for item in batch.results] == [2, 2, 0, 0]
    assert [item.dimension_id for item in batch.results[:2]] == [
        "dimension-a",
        "dimension-b",
    ]


@pytest.mark.asyncio
async def test_batch_uses_one_provider_cap_and_budget_skip_never_calls_upstream() -> None:
    provider = _BurstProvider(count=1)
    gateway = _gateway(provider)
    requests = [
        DimensionSearchRequest(
            f"dimension-{index}",
            SearchRequest(f"query-{index}", mode="research"),
        )
        for index in range(3)
    ]
    try:
        batch = await gateway.search_dimension_batch(
            requests,
            query_slots=3,
            provider_call_slots=2,
            result_slots=3,
            min_results_per_core=1,
        )
    finally:
        await gateway.close()

    assert provider.calls == ["query-0", "query-1"]
    skipped = batch.results[2].response.attempts[0]
    assert skipped.status is AttemptStatus.BUDGET_EXHAUSTED
    assert skipped.upstream_called is False
    assert skipped.budget_attempt_id is None
    attempts = batch.budget["attempts"]
    assert len(attempts) == 2
    assert all(attempt.upstream_started for attempt in attempts)
    projected_budget = batch.to_dict()["budget"]
    validate_json_value(projected_budget)
    assert isinstance(projected_budget["attempts"], list)


@pytest.mark.asyncio
async def test_batch_cache_hit_commits_results_without_provider_attempt_slot() -> None:
    provider = _BurstProvider(count=2)
    gateway = _gateway(provider)
    first_request = DimensionSearchRequest(
        "dimension", SearchRequest("cache-me", max_results=2, mode="research")
    )
    try:
        first = await gateway.search_dimension_batch(
            [first_request],
            query_slots=1,
            provider_call_slots=1,
            result_slots=2,
            min_results_per_core=2,
        )
        second = await gateway.search_dimension_batch(
            [
                DimensionSearchRequest(
                    "dimension",
                    SearchRequest("cache-me", max_results=2, mode="research"),
                )
            ],
            query_slots=1,
            provider_call_slots=0,
            result_slots=2,
            min_results_per_core=2,
        )
    finally:
        await gateway.close()

    assert first.results[0].response.cache_hit is False
    cached = second.results[0].response
    assert cached.cache_hit is True
    assert cached.count == 2
    assert cached.attempts[0].status is AttemptStatus.CACHE_HIT
    assert cached.attempts[0].upstream_called is False
    assert second.budget["attempts"] == ()
    assert provider.calls == ["cache-me"]


@pytest.mark.asyncio
async def test_allocator_started_attempts_are_never_refunded_and_coverage_fails_closed() -> None:
    allocator = DimensionBatchBudgetAllocator(
        deadline=time.monotonic() + 10,
        query_slots=1,
        provider_call_slots=1,
        result_slots=1,
        cdp_slots=1,
        hydrate_slots=1,
    )
    lease = await allocator.reserve_query(
        dimension_id="dimension",
        query_key="query",
        core=True,
    )
    assert lease is not None
    attempt = await allocator.claim_provider_attempt(
        lease,
        provider="duckduckgo",
        generation=0,
        is_rescue=False,
    )
    assert attempt is not None and attempt.upstream_started
    await allocator.commit_provider_attempt(
        lease, attempt.attempt_id, outcome="timeout", result_count=0
    )
    assert (
        await allocator.claim_provider_attempt(
            lease,
            provider="duckduckgo",
            generation=0,
            is_rescue=False,
        )
        is None
    )
    assert await lease.claim_cdp() is True
    assert await lease.claim_cdp() is False
    assert await lease.claim_hydrate() is True
    assert await lease.claim_hydrate() is False

    with pytest.raises(SearchBudgetCoverageError, match="disagrees"):
        await allocator.validate_attempt_coverage(
            lease,
            (
                ProviderAttempt(
                    "duckduckgo",
                    AttemptStatus.TIMEOUT,
                    1,
                    upstream_called=False,
                    budget_attempt_id=attempt.attempt_id,
                ),
            ),
        )


@pytest.mark.asyncio
async def test_availability_is_rechecked_after_limiter_before_budget_claim() -> None:
    provider = _FlappingProvider()
    gateway = _gateway(provider)
    try:
        batch = await gateway.search_dimension_batch(
            [
                DimensionSearchRequest(
                    "dimension", SearchRequest("flaps", mode="research")
                )
            ],
            query_slots=1,
            provider_call_slots=1,
            result_slots=1,
            min_results_per_core=1,
        )
    finally:
        await gateway.close()

    attempt = batch.results[0].response.attempts[0]
    assert provider.availability_checks == 2
    assert provider.calls == []
    assert attempt.status is AttemptStatus.UNAVAILABLE
    assert attempt.upstream_called is False
    assert attempt.budget_attempt_id is None
    assert batch.budget["remaining"]["provider"] == 1


@pytest.mark.asyncio
async def test_parent_budget_fails_closed_when_all_core_floors_cannot_be_reserved() -> None:
    provider = _BurstProvider(count=1)
    gateway = _gateway(provider)
    requests = [
        DimensionSearchRequest("a", SearchRequest("query-a", mode="research")),
        DimensionSearchRequest("b", SearchRequest("query-b", mode="research")),
    ]
    try:
        with pytest.raises(SearchBudgetCoverageError, match="every core dimension"):
            await gateway.search_dimension_batch(
                requests,
                query_slots=2,
                provider_call_slots=2,
                result_slots=1,
                min_results_per_core=1,
            )
    finally:
        await gateway.close()
    assert provider.calls == []


@pytest.mark.asyncio
async def test_negative_core_floor_and_duplicate_allocator_key_fail_fast() -> None:
    allocator = DimensionBatchBudgetAllocator(
        deadline=time.monotonic() + 10,
        query_slots=2,
        provider_call_slots=2,
        result_slots=2,
        cdp_slots=0,
        hydrate_slots=0,
    )
    with pytest.raises(ValueError, match="non-negative integer"):
        await allocator.reserve_core_round(
            [("dimension", "fingerprint")], min_results_per_core=-1
        )
    lease = await allocator.reserve_query(
        dimension_id="dimension",
        query_key="fingerprint",
        core=True,
    )
    assert lease is not None
    with pytest.raises(SearchBudgetCoverageError, match="duplicate query"):
        await allocator.reserve_query(
            dimension_id="dimension",
            query_key="fingerprint",
            core=True,
        )


@pytest.mark.asyncio
async def test_batch_deduplicates_same_dimension_query_fingerprint() -> None:
    provider = _BurstProvider(count=1)
    gateway = _gateway(provider)
    duplicated = [
        DimensionSearchRequest(
            "dimension",
            SearchRequest("same query", mode="research"),
        ),
        DimensionSearchRequest(
            "dimension",
            SearchRequest("same query", mode="research"),
        ),
    ]
    try:
        batch = await gateway.search_dimension_batch(
            duplicated,
            query_slots=2,
            provider_call_slots=2,
            result_slots=2,
            min_results_per_core=1,
        )
    finally:
        await gateway.close()

    assert provider.calls == ["same query"]
    assert len(batch.results) == 1


@pytest.mark.asyncio
async def test_batch_passes_dimension_and_chinese_terms_into_shared_ranking() -> None:
    provider = _ChineseProvider(count=2)
    gateway = _gateway(provider)
    query = "2024年小学教育现状 双减课后服务"
    request = DimensionSearchRequest(
        "edu_double_reduction_after_school_burden",
        SearchRequest(query, max_results=2, mode="research"),
        query_terms=extract_query_terms(query),
    )
    try:
        batch = await gateway.search_dimension_batch(
            [request],
            query_slots=1,
            provider_call_slots=1,
            result_slots=2,
            min_results_per_core=1,
        )
    finally:
        await gateway.close()

    ranked = batch.results[0].response.results
    assert ranked[0].title == "小学教育现状与双减课后服务 result 1"
    assert ranked[0].score > ranked[1].score
