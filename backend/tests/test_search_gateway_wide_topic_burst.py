from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from config import SearchGatewayConfig
from deskpet.retrieval.contracts import (
    PublicErrorCode,
    RetrievalCandidate,
    SearchRequest,
)
from deskpet.retrieval.providers.base import ProviderFailure
from deskpet.retrieval.search_gateway import SearchGateway


class _BurstProvider:
    capabilities = frozenset({"html"})

    def __init__(self, name: str, behavior: str) -> None:
        self.name = name
        self.behavior = behavior
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def is_available(self) -> bool:
        return True

    async def search(self, request, budget, client):
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.003 if self.behavior != "slow" else 0.015)
            if self.behavior == "empty":
                return []
            if self.behavior == "blocked":
                raise ProviderFailure(PublicErrorCode.BLOCKED, 403)
            if self.behavior == "timeout":
                raise asyncio.TimeoutError
            if request.query.endswith("-00"):
                return [
                    RetrievalCandidate(
                        "wide-topic-hit",
                        "https://fixture.example.test/wide-topic-hit",
                        "https://fixture.example.test/wide-topic-hit",
                        "wide topic result",
                        provider=self.name,
                        providers=(self.name,),
                    )
                ]
            return []
        finally:
            self.in_flight -= 1


@pytest.mark.asyncio
async def test_five_branch_thirteen_query_burst_is_bounded_and_recovers() -> None:
    providers = [
        _BurstProvider("baidu", "empty"),
        _BurstProvider("duckduckgo", "slow"),
        _BurstProvider("searxng", "blocked"),
        _BurstProvider("bing-cdp", "timeout"),
    ]
    config = replace(
        SearchGatewayConfig(),
        providers=[provider.name for provider in providers],
        provider_max_concurrency=1,
        provider_queue_max_wait_s=1.0,
        quick_total_timeout_s=2.0,
        per_provider_timeout_s=0.2,
        quick_min_results=10,
        quick_min_domains=10,
        circuit_blocked_probe_base_s=0.01,
        circuit_timeout_threshold=1,
        circuit_timeout_probe_base_s=0.01,
        empty_rescue_enabled=True,
        empty_rescue_max_per_request=1,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=providers, client=client)
    fingerprints = [f"wide-topic-{index:02d}" for index in range(13)]
    branch_inputs = [fingerprints[index::5] for index in range(5)]

    async def run_branch(items: list[str]):
        responses = []
        for item in items:
            responses.append(
                await gateway.search(
                    SearchRequest(item, request_id=f"req-{item}", run_id="burst-run")
                )
            )
        return responses

    try:
        nested = await asyncio.gather(*(run_branch(items) for items in branch_inputs))
    finally:
        await gateway.shutdown()
        await client.aclose()

    responses = [response for branch in nested for response in branch]
    transport_calls = sum(provider.calls for provider in providers)
    probe_pairs = [
        (attempt.provider, attempt.circuit_generation)
        for response in responses
        for attempt in response.attempts
        if attempt.permit == "half_open" and attempt.upstream_called
    ]

    assert len(responses) == 13
    assert any(response.count > 0 for response in responses)
    assert transport_calls <= 13 * 4 + 13 * 1 == 65
    assert all(provider.max_in_flight <= 1 for provider in providers)
    assert len(probe_pairs) == len(set(probe_pairs))
    assert all(
        sum(attempt.is_rescue for attempt in response.attempts) <= 1
        for response in responses
    )
    assert all(
        int(response.coverage()["rescue_executed"]) <= 1
        for response in responses
    )
