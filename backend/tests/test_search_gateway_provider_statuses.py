from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from config import SearchGatewayConfig
from deskpet.retrieval.cache import FailurePolicy, ProviderHealth
from deskpet.retrieval.contracts import (
    AttemptStatus,
    PublicErrorCode,
    RetrievalCandidate,
    SearchRequest,
)
from deskpet.retrieval.providers.base import ProviderFailure
from deskpet.retrieval.search_gateway import SearchGateway


def _policy(*, threshold: int = 1, open_s: float = 30, probe_s: float = 2) -> FailurePolicy:
    return FailurePolicy(threshold, open_s, probe_s, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_class", "threshold"),
    [
        ("timeout", 2),
        ("http", 2),
        ("invalid_response", 2),
        ("blocked", 1),
        ("captcha", 1),
        ("rate_limit", 1),
    ],
)
async def test_failure_class_threshold_open_and_probe_backoff_matrix(
    failure_class: str,
    threshold: int,
) -> None:
    now = {"value": 0.0}
    policy = FailurePolicy(threshold, 100, 2, 16)
    health = ProviderHealth(
        policies={failure_class: policy},
        clock=lambda: now["value"],
    )

    for _ in range(threshold - 1):
        assert await health.record_failure("p", failure_class)
        assert (await health.snapshot("p")).state == "closed"
    assert await health.record_failure("p", failure_class)
    opened = await health.snapshot("p")
    assert opened.state == "open"
    assert opened.failure_class == failure_class
    assert opened.eligible_at == 2

    now["value"] = 2
    probe = await health.acquire_permit("p", allow_probe=True)
    assert probe.probe_owner
    assert await health.complete_probe(
        "p", probe, "failure", failure_class=failure_class
    )
    backed_off = await health.snapshot("p")
    assert backed_off.state == "open"
    assert backed_off.generation == 2
    assert backed_off.eligible_at == 6


@pytest.mark.asyncio
async def test_provider_health_generation_token_and_cancel_are_fail_closed() -> None:
    now = {"value": 100.0}
    health = ProviderHealth(
        policies={"blocked": _policy(open_s=60, probe_s=5)},
        clock=lambda: now["value"],
    )
    stale_closed = await health.acquire_permit("p")
    await health.record_failure("p", "blocked")
    opened = await health.snapshot("p")
    assert opened.state == "open"
    assert opened.generation == 1
    assert opened.eligible_at == 105
    assert await health.record_success("p", permit=stale_closed) is False
    assert (await health.snapshot("p")).state == "open"

    early = await health.acquire_permit("p", allow_probe=True)
    assert early.kind == "open" and early.wait_s == 5
    now["value"] = 105
    contenders = await asyncio.gather(
        *(health.acquire_permit("p", allow_probe=True) for _ in range(8))
    )
    owners = [permit for permit in contenders if permit.probe_owner]
    assert len(owners) == 1
    assert sum(permit.kind == "half_open_busy" for permit in contenders) == 7

    owner = owners[0]
    assert await health.complete_probe("p", owner, "cancel") is True
    assert await health.complete_probe("p", owner, "hit") is False
    after_cancel = await health.acquire_permit("p", allow_probe=True)
    assert after_cancel.kind == "open"
    assert after_cancel.wait_s == 5


@pytest.mark.asyncio
async def test_natural_probe_cancel_rearms_interval_without_fake_success() -> None:
    now = {"value": 0.0}
    health = ProviderHealth(
        policies={"blocked": _policy(open_s=30, probe_s=5)},
        clock=lambda: now["value"],
    )
    await health.record_failure("p", "blocked")
    now["value"] = 30

    natural = await health.acquire_permit("p")
    assert natural.probe_owner
    assert await health.complete_probe("p", natural, "cancel") is True

    rearmed = await health.snapshot("p")
    assert rearmed.state == "open"
    assert rearmed.generation == 2
    assert rearmed.failure_class == "blocked"
    assert rearmed.eligible_at == 35
    immediate = await health.acquire_permit("p")
    assert immediate.kind == "open"
    assert immediate.wait_s == 5
    assert await health.complete_probe("p", natural, "hit") is False

    now["value"] = 35
    next_owner = await health.acquire_permit("p")
    assert next_owner.probe_owner


@pytest.mark.asyncio
async def test_probe_failure_advances_generation_and_bounds_sequential_retries() -> None:
    now = {"value": 0.0}
    health = ProviderHealth(
        policies={"timeout": _policy(open_s=30, probe_s=2)},
        clock=lambda: now["value"],
    )
    await health.record_failure("p", "timeout")
    now["value"] = 2
    owner = await health.acquire_permit("p", allow_probe=True)
    assert owner.probe_owner
    assert await health.complete_probe(
        "p", owner, "failure", failure_class="timeout"
    )
    reopened = await health.snapshot("p")
    assert reopened.generation == 2
    assert reopened.eligible_at == 6

    for _ in range(10):
        permit = await health.acquire_permit("p", allow_probe=True)
        assert permit.kind == "open"
    assert (await health.snapshot("p")).generation == 2


@pytest.mark.asyncio
async def test_probe_candidate_selection_is_atomic_ordered_and_deadline_bounded() -> None:
    now = {"value": 0.0}
    health = ProviderHealth(
        policies={"blocked": _policy(open_s=30, probe_s=5)},
        clock=lambda: now["value"],
    )
    await health.record_failure("first", "blocked")
    await health.record_failure("second", "blocked")

    assert await health.select_probe_candidate(["first", "second"], 4.99) is None
    selected = await health.select_probe_candidate(["first", "second"], 5.0)
    assert selected is not None
    assert selected.provider == "first"
    assert selected.generation == 1
    assert selected.eligible_at == 5.0


class _FailOnceThenHit:
    name = "duckduckgo"
    capabilities = frozenset({"html"})

    def __init__(self) -> None:
        self.calls = 0

    async def is_available(self) -> bool:
        return True

    async def search(self, request, budget, client):
        self.calls += 1
        if self.calls == 1:
            raise ProviderFailure(PublicErrorCode.BLOCKED, 403)
        return [
            RetrievalCandidate(
                "id",
                "https://example.test/result",
                "https://example.test/result",
                f"result for {request.query}",
                provider=self.name,
                providers=(self.name,),
            )
        ]


@pytest.mark.asyncio
async def test_all_open_eligible_owner_probes_and_emits_safe_request_diagnostics() -> None:
    provider = _FailOnceThenHit()
    config = replace(
        SearchGatewayConfig(),
        providers=[provider.name],
        quick_min_results=1,
        quick_min_domains=1,
        circuit_blocked_probe_base_s=0.01,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[provider], client=client)
    try:
        first = await gateway.search(SearchRequest("first", request_id="request-1", run_id="run-1"))
        second = await gateway.search(SearchRequest("second", request_id="request-2", run_id="run-1"))
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert first.attempts[0].status is AttemptStatus.BLOCKED
    assert second.attempts[0].status is AttemptStatus.HIT
    assert second.attempts[0].permit == "half_open"
    assert second.attempts[0].probe_outcome == "hit"
    assert provider.calls == 2
    payload = second.to_dict()
    assert payload["request_id"] == "request-2"
    assert payload["run_id"] == "run-1"
    assert payload["attempts"][0]["circuit_generation"] == 1
    assert "query" in payload  # public response BC; diagnostics add no URL/body fields


@pytest.mark.asyncio
async def test_circuit_metrics_include_safe_transition_class_permit_and_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "deskpet.retrieval.search_gateway._metric",
        lambda event, detail: events.append((event, dict(detail))),
    )
    provider = _FailOnceThenHit()
    config = replace(
        SearchGatewayConfig(),
        providers=[provider.name],
        quick_min_results=1,
        quick_min_domains=1,
        circuit_blocked_probe_base_s=0.01,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[provider], client=client)
    try:
        await gateway.search(
            SearchRequest("private first query", request_id="req-a", run_id="run-a")
        )
        await gateway.search(
            SearchRequest("private second query", request_id="req-b", run_id="run-a")
        )
    finally:
        await gateway.shutdown()
        await client.aclose()

    cooldown = [detail for event, detail in events if event == "search_gateway_cooldown"]
    attempts = [detail for event, detail in events if event == "search_gateway_attempt"]
    assert any(row["transition"] == "closed_to_open" for row in cooldown)
    assert any(row["transition"] == "open_to_half_open" for row in cooldown)
    assert any(row["transition"] == "half_open_to_closed" for row in cooldown)
    assert any(row.get("failure_class") == "blocked" for row in cooldown)
    assert any(
        row.get("permit") == "half_open"
        and row.get("probe_outcome") == "hit"
        and row.get("transition") == "half_open_to_closed"
        for row in attempts
    )
    for _, detail in events:
        assert "query" not in detail
        assert "url" not in detail
        assert "private first query" not in str(detail)
        assert "private second query" not in str(detail)


@pytest.mark.asyncio
async def test_circuit_cooldown_is_isolated_between_workflow_runs() -> None:
    provider = _FailOnceThenHit()
    config = replace(
        SearchGatewayConfig(),
        providers=[provider.name],
        quick_min_results=1,
        quick_min_domains=1,
        circuit_blocked_probe_base_s=30.0,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[provider], client=client)
    try:
        first = await gateway.search(
            SearchRequest("first run query", request_id="req-run-a", run_id="run-a")
        )
        second = await gateway.search(
            SearchRequest("second run query", request_id="req-run-b", run_id="run-b")
        )
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert first.attempts[0].status is AttemptStatus.BLOCKED
    assert second.attempts[0].status is AttemptStatus.HIT
    assert second.attempts[0].permit == "closed"
    assert second.attempts[0].circuit_generation == 0
    assert provider.calls == 2


class _AlwaysEmpty:
    name = "duckduckgo"
    capabilities = frozenset({"html"})

    def __init__(self) -> None:
        self.calls = 0

    async def is_available(self) -> bool:
        return True

    async def search(self, request, budget, client):
        self.calls += 1
        return []


@pytest.mark.asyncio
async def test_validated_empty_is_neutral_and_never_opens_circuit() -> None:
    provider = _AlwaysEmpty()
    config = replace(SearchGatewayConfig(), providers=[provider.name])
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[provider], client=client)
    try:
        one = await gateway.search(SearchRequest("one"))
        two = await gateway.search(SearchRequest("two"))
    finally:
        await gateway.shutdown()
        await client.aclose()
    assert one.attempts[0].status is AttemptStatus.EMPTY
    assert two.attempts[0].status is AttemptStatus.EMPTY
    assert provider.calls == 2


class _FailThen:
    capabilities = frozenset({"html"})

    def __init__(self, name: str, *, rescue_rows: bool) -> None:
        self.name = name
        self.rescue_rows = rescue_rows
        self.calls = 0

    async def is_available(self) -> bool:
        return True

    async def search(self, request, budget, client):
        self.calls += 1
        if self.calls == 1:
            raise ProviderFailure(PublicErrorCode.BLOCKED, 403)
        if not self.rescue_rows:
            return []
        return [
            RetrievalCandidate(
                "rescue-id",
                "https://rescue.example.test/result",
                "https://rescue.example.test/result",
                "rescue result",
                provider=self.name,
                providers=(self.name,),
            )
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rescue_rows", "expected_status"),
    [(True, "hit"), (False, "empty")],
)
async def test_closed_empty_plus_open_provider_runs_one_bounded_rescue(
    rescue_rows: bool,
    expected_status: str,
) -> None:
    empty = _AlwaysEmpty()
    empty.name = "baidu"
    recoverable = _FailThen("duckduckgo", rescue_rows=rescue_rows)
    config = replace(
        SearchGatewayConfig(),
        providers=[empty.name, recoverable.name],
        quick_min_results=10,
        quick_min_domains=10,
        circuit_blocked_probe_base_s=0.01,
        provider_max_concurrency=1,
        provider_queue_max_wait_s=0.2,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=config, providers=[empty, recoverable], client=client
    )
    try:
        response = await gateway.search(SearchRequest("empty-aware-rescue"))
        final_snapshot = await gateway.health.snapshot(recoverable.name)
    finally:
        await gateway.shutdown()
        await client.aclose()

    rescue_attempts = [attempt for attempt in response.attempts if attempt.is_rescue]
    assert len(rescue_attempts) == 1
    assert rescue_attempts[0].upstream_called is True
    assert recoverable.calls == 2
    assert response.rescue_status == expected_status
    assert response.rescue_upstream_called is True
    assert response.coverage()["rescue_executed"] is True
    if rescue_rows:
        assert response.count == 1
    else:
        assert response.count == 0
        assert final_snapshot.state == "closed"


@pytest.mark.asyncio
async def test_concurrent_rescue_loser_does_not_call_closed_provider_again() -> None:
    class _SlowRescue(_FailThen):
        async def search(self, request, budget, client):
            if self.calls == 1:
                return await super().search(request, budget, client)
            self.calls += 1
            await asyncio.sleep(0.03)
            return [
                RetrievalCandidate(
                    "winner",
                    "https://winner.example.test/result",
                    "https://winner.example.test/result",
                    "winner",
                    provider=self.name,
                    providers=(self.name,),
                )
            ]

    empty = _AlwaysEmpty()
    empty.name = "baidu"
    recoverable = _SlowRescue("duckduckgo", rescue_rows=True)
    config = replace(
        SearchGatewayConfig(),
        providers=[empty.name, recoverable.name],
        quick_min_results=10,
        quick_min_domains=10,
        circuit_blocked_probe_base_s=0.01,
        provider_max_concurrency=1,
        provider_queue_max_wait_s=0.2,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=config, providers=[empty, recoverable], client=client
    )
    # Establish one open provider before both requests observe it.
    await gateway.health.record_failure(recoverable.name, "blocked")
    await asyncio.sleep(0.011)
    try:
        first, second = await asyncio.gather(
            gateway.search(SearchRequest("rescue-race-a")),
            gateway.search(SearchRequest("rescue-race-b")),
        )
    finally:
        await gateway.shutdown()
        await client.aclose()

    responses = (first, second)
    assert recoverable.calls == 1
    assert sum(response.rescue_upstream_called for response in responses) == 1
    assert sorted(response.rescue_status for response in responses) == [
        "hit",
        "lost_race",
    ]


class _AlwaysBlocked:
    capabilities = frozenset({"html"})

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def is_available(self) -> bool:
        return True

    async def search(self, request, budget, client):
        self.calls += 1
        raise ProviderFailure(PublicErrorCode.BLOCKED, 403)


@pytest.mark.asyncio
async def test_rescue_candidate_after_deadline_is_not_called() -> None:
    empty = _AlwaysEmpty()
    empty.name = "baidu"
    blocked = _AlwaysBlocked("duckduckgo")
    config = replace(
        SearchGatewayConfig(),
        providers=[empty.name, blocked.name],
        quick_min_results=10,
        quick_min_domains=10,
        circuit_blocked_probe_base_s=1.0,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[empty, blocked], client=client)
    try:
        response = await gateway.search(
            SearchRequest("rescue-deadline", total_timeout_s=0.05)
        )
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert blocked.calls == 1
    assert response.rescue_status == "deadline_exhausted"
    assert response.rescue_upstream_called is False
    assert not [attempt for attempt in response.attempts if attempt.is_rescue]


@pytest.mark.asyncio
async def test_rescue_eligibility_wait_is_cancellable_without_probe_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "deskpet.retrieval.search_gateway._metric",
        lambda event, detail: events.append((event, dict(detail))),
    )
    empty = _AlwaysEmpty()
    empty.name = "baidu"
    blocked = _AlwaysBlocked("duckduckgo")
    config = replace(
        SearchGatewayConfig(),
        providers=[empty.name, blocked.name],
        quick_min_results=10,
        quick_min_domains=10,
        circuit_blocked_probe_base_s=0.2,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[empty, blocked], client=client)
    task = asyncio.create_task(
        gateway.search(SearchRequest("cancel-rescue-wait", total_timeout_s=1.0))
    )
    for _ in range(50):
        if blocked.calls == 1 and empty.calls == 1:
            break
        await asyncio.sleep(0.002)
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    snapshot = await gateway.health.snapshot(blocked.name)
    await gateway.shutdown()
    await client.aclose()

    assert blocked.calls == 1
    assert snapshot.state == "open"
    assert snapshot.probe_in_flight is False
    assert any(
        event == "search_gateway_cancelled_wait"
        and detail.get("status") == "cancelled_rescue_eligibility"
        for event, detail in events
    )


@pytest.mark.asyncio
async def test_ten_sequential_requests_obey_probe_interval_call_upper_bound() -> None:
    provider = _AlwaysBlocked("duckduckgo")
    config = replace(
        SearchGatewayConfig(),
        providers=[provider.name],
        quick_total_timeout_s=0.05,
        circuit_blocked_probe_base_s=2,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[provider], client=client)
    try:
        responses = [
            await gateway.search(
                SearchRequest(f"bounded-{index}", total_timeout_s=0.05)
            )
            for index in range(10)
        ]
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert provider.calls == 1
    assert responses[0].attempts[0].status is AttemptStatus.BLOCKED
    assert all(
        response.attempts[0].status is AttemptStatus.COOLDOWN
        for response in responses[1:]
    )


class _HitProvider:
    capabilities = frozenset({"html"})

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    async def is_available(self) -> bool:
        return True

    async def search(self, request, budget, client):
        self.calls += 1
        return [
            RetrievalCandidate(
                f"{self.name}-id",
                f"https://{self.name}.example.test/result",
                f"https://{self.name}.example.test/result",
                "bounded probe result",
                provider=self.name,
                providers=(self.name,),
            )
        ]


@pytest.mark.asyncio
async def test_cache_hit_does_not_mutate_open_circuit_state() -> None:
    provider = _HitProvider("duckduckgo")
    config = replace(
        SearchGatewayConfig(),
        providers=[provider.name],
        quick_min_results=1,
        quick_min_domains=1,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=[provider], client=client)
    try:
        first = await gateway.search(SearchRequest("cache-stability"))
        assert first.cache_hit is False
        await gateway.health.record_failure(provider.name, "blocked")
        before = await gateway.health.snapshot(provider.name)

        cached = await gateway.search(SearchRequest("cache-stability"))
        after = await gateway.health.snapshot(provider.name)
    finally:
        await gateway.shutdown()
        await client.aclose()

    assert cached.cache_hit is True
    assert provider.calls == 1
    assert after == before


@pytest.mark.asyncio
async def test_multiple_open_providers_choose_earliest_then_route_loser_as_cooldown() -> None:
    now = {"value": 0.0}
    baidu = _HitProvider("baidu")
    duckduckgo = _HitProvider("duckduckgo")
    config = replace(
        SearchGatewayConfig(),
        providers=[baidu.name, duckduckgo.name],
        quick_min_results=1,
        quick_min_domains=1,
        circuit_blocked_probe_base_s=5,
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(
        config=config,
        providers=[baidu, duckduckgo],
        client=client,
    )
    gateway.health = ProviderHealth(
        policies={"blocked": _policy(open_s=60, probe_s=5)},
        clock=lambda: now["value"],
    )
    await gateway.health.record_failure("baidu", "blocked")
    now["value"] = 1
    await gateway.health.record_failure("duckduckgo", "blocked")

    assert await gateway._all_open_probe_candidate(["baidu", "duckduckgo"]) == "baidu"
    now["value"] = 5
    try:
        response = await gateway.search(SearchRequest("english routing"))
    finally:
        await gateway.shutdown()
        await client.aclose()

    by_provider = {attempt.provider: attempt for attempt in response.attempts}
    assert by_provider["duckduckgo"].status is AttemptStatus.COOLDOWN
    assert by_provider["baidu"].status is AttemptStatus.HIT
    assert baidu.calls == 1
    assert duckduckgo.calls == 0


@pytest.mark.asyncio
async def test_probe_failure_rotates_earliest_candidate_instead_of_starving_peer() -> None:
    now = {"value": 0.0}
    health = ProviderHealth(
        policies={"blocked": _policy(open_s=60, probe_s=5)},
        clock=lambda: now["value"],
    )
    providers = [_HitProvider("baidu"), _HitProvider("duckduckgo")]
    config = replace(
        SearchGatewayConfig(), providers=[provider.name for provider in providers]
    )
    client = httpx.AsyncClient()
    gateway = SearchGateway(config=config, providers=providers, client=client)
    gateway.health = health
    try:
        await health.record_failure("baidu", "blocked")
        now["value"] = 1
        await health.record_failure("duckduckgo", "blocked")
        assert await gateway._all_open_probe_candidate(
            ["baidu", "duckduckgo"]
        ) == "baidu"

        now["value"] = 5
        first = await health.acquire_permit("baidu", allow_probe=True)
        assert first.probe_owner
        assert await health.complete_probe(
            "baidu", first, "failure", failure_class="blocked"
        )

        assert await gateway._all_open_probe_candidate(
            ["baidu", "duckduckgo"]
        ) == "duckduckgo"
    finally:
        await gateway.shutdown()
        await client.aclose()
