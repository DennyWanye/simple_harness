from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from config import SearchGatewayConfig
from deskpet.retrieval.contracts import (
    AttemptStatus,
    ProviderAttempt,
    PublicErrorCode,
    RetrievalCandidate,
    SearchRequest,
    SearchResponse,
)
from deskpet.retrieval.providers.base import ProviderFailure
from deskpet.retrieval.search_gateway import SearchGateway


class _ObservedProvider:
    capabilities = frozenset({"html"})

    def __init__(
        self,
        name: str,
        *,
        delay_s: float = 0.01,
        failure: PublicErrorCode | None = None,
    ) -> None:
        self.name = name
        self.delay_s = delay_s
        self.failure = failure
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def is_available(self) -> bool:
        return True

    async def search(self, request, budget, client):
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.entered.set()
        try:
            if self.release is not None:
                await self.release.wait()
            elif self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.failure is not None:
                raise ProviderFailure(self.failure)
            return [
                RetrievalCandidate(
                    f"{self.name}-{request.request_id}",
                    f"https://{self.name}.example.test/item",
                    f"https://{self.name}.example.test/item",
                    "provider limiter fixture",
                    provider=self.name,
                    providers=(self.name,),
                )
            ]
        finally:
            self.in_flight -= 1


def _config(*providers: str, **changes) -> SearchGatewayConfig:
    values = {
        "providers": list(providers),
        "provider_max_concurrency": 1,
        "provider_queue_max_wait_s": 0.2,
        "quick_min_results": 1,
        "quick_min_domains": 1,
    }
    values.update(changes)
    return replace(
        SearchGatewayConfig(),
        **values,
    )


@pytest.mark.asyncio
async def test_same_provider_ten_concurrent_requests_never_exceed_slot_cap() -> None:
    provider = _ObservedProvider("duckduckgo")
    client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=_config(provider.name), providers=[provider], client=client
    )
    try:
        responses = await asyncio.gather(*(
            gateway.search(SearchRequest(f"fingerprint-{index}"))
            for index in range(10)
        ))
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert provider.calls == 10
    assert provider.max_in_flight == 1
    assert all(response.coverage()["actual_requests"] == 1 for response in responses)


@pytest.mark.asyncio
async def test_different_provider_slots_overlap() -> None:
    searxng = _ObservedProvider("searxng", delay_s=0.04)
    duckduckgo = _ObservedProvider("duckduckgo", delay_s=0.04)
    active = 0
    max_active = 0

    for provider in (searxng, duckduckgo):
        original = provider.search

        async def observed(request, budget, client, *, original=original):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                return await original(request, budget, client)
            finally:
                active -= 1

        provider.search = observed

    client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=_config(
            searxng.name,
            duckduckgo.name,
            quick_min_results=10,
            quick_min_domains=10,
        ),
        providers=[searxng, duckduckgo],
        client=client,
    )
    try:
        await gateway.search(SearchRequest("provider-overlap"))
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert max_active == 2
    assert searxng.max_in_flight == duckduckgo.max_in_flight == 1


@pytest.mark.asyncio
async def test_queued_call_rechecks_circuit_after_first_opens_it() -> None:
    provider = _ObservedProvider(
        "duckduckgo", delay_s=0.02, failure=PublicErrorCode.BLOCKED
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=_config(provider.name), providers=[provider], client=client
    )
    try:
        responses = await asyncio.gather(*(
            gateway.search(SearchRequest(f"queued-{index}")) for index in range(6)
        ))
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert provider.calls == 1
    statuses = [response.attempts[0].status for response in responses]
    assert statuses.count(AttemptStatus.BLOCKED) == 1
    assert statuses.count(AttemptStatus.COOLDOWN) == 5


@pytest.mark.asyncio
async def test_queue_timeout_does_not_mutate_provider_health() -> None:
    provider = _ObservedProvider("duckduckgo", delay_s=0)
    provider.release = asyncio.Event()
    client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=_config(provider.name, provider_queue_max_wait_s=0.01),
        providers=[provider],
        client=client,
    )
    holder = asyncio.create_task(gateway.search(SearchRequest("holder")))
    await provider.entered.wait()
    try:
        queued = await gateway.search(
            SearchRequest("queued-timeout", total_timeout_s=0.03)
        )
        snapshot = await gateway.health.snapshot(provider.name)
    finally:
        provider.release.set()
        await holder
        await gateway.shutdown()
        await client.aclose()

    assert queued.attempts[0].status is AttemptStatus.QUEUE_TIMEOUT
    assert queued.attempts[0].upstream_called is False
    assert snapshot.state == "closed"
    assert snapshot.generation == 0


@pytest.mark.asyncio
async def test_wait_and_transport_cancellation_release_slots_and_re_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "deskpet.retrieval.search_gateway._metric",
        lambda event, detail: events.append((event, dict(detail))),
    )
    provider = _ObservedProvider("duckduckgo", delay_s=0)
    provider.release = asyncio.Event()
    client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=_config(provider.name), providers=[provider], client=client
    )
    holder = asyncio.create_task(gateway.search(SearchRequest("holder")))
    await provider.entered.wait()
    waiter = asyncio.create_task(gateway.search(SearchRequest("waiter")))
    await asyncio.sleep(0.01)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    holder.cancel()
    with pytest.raises(asyncio.CancelledError):
        await holder
    provider.release.set()
    try:
        final = await gateway.search(SearchRequest("after-cancel"))
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert final.attempts[0].upstream_called is True
    assert provider.max_in_flight == 1
    assert any(event == "search_gateway_cancelled_wait" for event, _ in events)


@pytest.mark.asyncio
async def test_half_open_transport_cancel_releases_lease_and_slot() -> None:
    provider = _ObservedProvider("duckduckgo", delay_s=0)
    provider.release = asyncio.Event()
    config = _config(
        provider.name,
        circuit_blocked_probe_base_s=0.01,
        circuit_blocked_open_s=1.0,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[provider], client=client)
    await gateway.health.record_failure(provider.name, "blocked")
    await asyncio.sleep(0.011)
    task = asyncio.create_task(gateway.search(SearchRequest("half-open-cancel")))
    await provider.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snapshot = await gateway.health.snapshot(provider.name)
    provider.release.set()
    try:
        followup = await gateway.search(
            SearchRequest("half-open-followup", total_timeout_s=0.005)
        )
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert snapshot.state == "open"
    assert snapshot.probe_in_flight is False
    assert followup.attempts[0].status is AttemptStatus.COOLDOWN


def test_search_response_coverage_uses_mutually_exclusive_equations() -> None:
    attempts = (
        ProviderAttempt("hit", AttemptStatus.HIT, 1, upstream_called=True),
        ProviderAttempt("empty", AttemptStatus.EMPTY, 1, upstream_called=True),
        ProviderAttempt(
            "error",
            AttemptStatus.ERROR,
            1,
            public_error_code=PublicErrorCode.PARSE_ERROR,
            upstream_called=True,
        ),
        ProviderAttempt("cooldown", AttemptStatus.COOLDOWN, 0),
        ProviderAttempt("busy", AttemptStatus.HALF_OPEN_BUSY, 0),
        ProviderAttempt("queue", AttemptStatus.QUEUE_TIMEOUT, 0),
        ProviderAttempt("unavailable", AttemptStatus.UNAVAILABLE, 0),
        ProviderAttempt("budget", AttemptStatus.BUDGET_EXHAUSTED, 0),
        ProviderAttempt("gateway", AttemptStatus.CACHE_HIT, 0),
    )
    response = SearchResponse(
        "fixture",
        (),
        attempts,
        rescue_status="failed",
        rescue_error_code=PublicErrorCode.PARSE_ERROR,
        rescue_upstream_called=True,
    )
    coverage = response.coverage()

    assert coverage["actual_requests"] == 3
    assert coverage["provider_attempt_count"] == 3
    assert coverage["routing_decisions"] == 9
    assert coverage["errors"] == 1
    assert coverage["parse_errors"] == 1
    assert coverage["other_errors"] == 0
    assert coverage["probes"] == 0
    assert coverage["rescue_considered"] is True
    assert coverage["rescue_executed"] is True
