from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.retrieval.contracts import (
    EvidenceDocument,
    FetchRequest,
    RetrievalCandidate,
    SearchRequest,
    SearchResponse,
)
from deskpet.retrieval.official_sources import (
    DEFAULT_OFFICIAL_SOURCE_REGISTRY,
    DEFAULT_OFFICIAL_SOURCE_RESOLVER,
    OfficialSourceRegistryV1,
    OfficialSourceResolver,
)
from deskpet.workflows import NodeExecutionIdentity
from deskpet.workflows.adapters.deep_research_v6_evidence_runtime import (
    DeepResearchV6EvidenceRuntime,
    V6FetchedPageRefPayloadV1,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import (
    compile_official_exact_fact,
    compile_research_spec,
)
from deskpet.workflows.definitions.deep_research_v6_contracts import (
    RouteDecisionV1,
    parse_blob_ref,
)
from deskpet.workflows.store import RegisteredBlobStore
from deskpet.workflows.trace import SpanContext, TraceStore, use_span
from deskpet.workflows.trace.models import SpanKind, SpanStatus


QUESTION = (
    "What were China's total population and number of births in 2024? "
    "Prefer the National Bureau of Statistics."
)


def _identity(run_id: str = "run-v6-retrieval") -> NodeExecutionIdentity:
    return NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v6",
        thread_id="thread-v6-retrieval",
        run_id=run_id,
        checkpoint_id="checkpoint-1",
        checkpoint_ns="",
        task_id="task-1",
        node_id="collect_pages",
        attempt=1,
    )


def _candidate(url: str, ordinal: int = 0) -> RetrievalCandidate:
    return RetrievalCandidate(
        stable_id=f"candidate-{ordinal}",
        url=url,
        canonical_url=url,
        title=f"Official bulletin {ordinal}",
        provider="fake",
        providers=("fake",),
        provider_rank=ordinal,
        searched_at="2026-07-18T00:00:00+00:00",
    )


def _document(
    requested_url: str,
    *,
    final_url: str | None = None,
    body: str,
    html: str | None = None,
) -> EvidenceDocument:
    final = final_url or requested_url
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return EvidenceDocument(
        stable_id="document-" + digest[:12],
        url=requested_url,
        canonical_url=final,
        title="National Bureau of Statistics bulletin",
        text=body,
        published_at=None,
        provider="fetch",
        providers=("fetch",),
        provider_rank=0,
        score=1.0,
        searched_at="2026-07-18T00:00:00+00:00",
        source_kind="web",
        content_hash=digest,
        fetcher="fake",
        extractor="fake",
        fetched_at="2026-07-18T00:00:01+00:00",
        html=html,
    )


class FakeFetchService:
    def __init__(
        self,
        documents: dict[str, EvidenceDocument],
        *,
        delays: dict[str, float] | None = None,
    ) -> None:
        self.documents = documents
        self.delays = delays or {}
        self.requests: list[FetchRequest] = []
        self.active = 0
        self.max_active = 0

    async def fetch(self, request: FetchRequest) -> EvidenceDocument:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            delay = self.delays.get(request.url, 0.0)
            if delay:
                await asyncio.sleep(delay)
            return self.documents[request.url]
        finally:
            self.active -= 1


class FakeGateway:
    def __init__(
        self,
        results: tuple[RetrievalCandidate, ...],
        fetch_service: FakeFetchService,
    ) -> None:
        self.results = results
        self.fetch_service = fetch_service
        self.requests: list[SearchRequest] = []

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        return SearchResponse(
            query=request.query,
            results=self.results,
            request_id=request.request_id,
            run_id=request.run_id,
        )


class SlowCountingGateway(FakeGateway):
    def __init__(self, results, fetch_service, *, delay: float = 0.02) -> None:
        super().__init__(results, fetch_service)
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return await super().search(request)
        finally:
            self.active -= 1


NO_SEED_SOURCE_RESOLVER = OfficialSourceResolver(
    OfficialSourceRegistryV1(
        policy_id="test-no-seed-official-source-registry",
        policy_version=1,
        entries=tuple(
            replace(entry, seed_urls=())
            for entry in DEFAULT_OFFICIAL_SOURCE_REGISTRY.entries
        ),
    )
)


def _runtime(
    tmp_path: Path,
    gateway: FakeGateway,
    **kwargs,
) -> tuple[DeepResearchV6EvidenceRuntime, RegisteredBlobStore]:
    blobs = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    kwargs.setdefault("source_resolver", NO_SEED_SOURCE_RESOLVER)
    return (
        DeepResearchV6EvidenceRuntime(gateway, blobs=blobs, **kwargs),
        blobs,
    )


@pytest.mark.asyncio
async def test_first_party_archive_discovers_frozen_year_without_search_or_answer_oracle(
    tmp_path: Path,
) -> None:
    archive = "https://www.stats.gov.cn/sj/tjgb/ndtjgb/qgndtjgb/"
    target = "https://www.stats.gov.cn/sj/zxfb/example-current-page.html"
    listing_html = (
        '<html><body><a href="/sj/zxfb/example-current-page.html">2024年</a>'
        '<a href="https://stats.gov.cn.evil.example/trap">2024年镜像</a></body></html>'
    )
    target_body = "Official annual population and births bulletin body."
    fetch = FakeFetchService(
        {
            archive: _document(
                archive,
                final_url="https://stats.gov.cn/sj/tjgb/ndtjgb/qgndtjgb/",
                body="Annual bulletin index",
                html=listing_html,
            ),
            target: _document(target, body=target_body),
        }
    )
    gateway = FakeGateway((), fetch)
    runtime, blobs = _runtime(
        tmp_path,
        gateway,
        source_resolver=DEFAULT_OFFICIAL_SOURCE_RESOLVER,
    )
    spec = compile_official_exact_fact(QUESTION)

    result = await runtime.retrieve(spec, identity=_identity("run-archive"))

    assert result.search_count == 0
    assert result.fetch_count == 2
    assert result.failed_count == result.rejected_count == result.timeout_count == 0
    assert [request.url for request in fetch.requests] == [archive, target]
    assert fetch.requests[0].include_html is True
    assert fetch.requests[1].include_html is False
    assert fetch.requests[0].prefer_httpx is False
    assert fetch.requests[1].prefer_httpx is True
    assert len(result.pages) == 1
    assert await blobs.get(parse_blob_ref(result.pages[0].body_ref)) == target_body.encode()


@pytest.mark.asyncio
async def test_archive_redirect_alias_keeps_registry_canonical_origin_without_probe(
    tmp_path: Path,
) -> None:
    archive = "https://www.stats.gov.cn/sj/tjgb/ndtjgb/"
    redirected_alias = "https://stats.gov.cn/sj/tjgb/ndtjgb/"
    target = "https://www.stats.gov.cn/sj/zxfb/202502/t20250228_1958817.html"
    listing_html = (
        '<a href="/sj/zxfb/202502/t20250228_1958817.html">2024年</a>'
    )
    redirected_document = replace(
        _document(
            archive,
            final_url=redirected_alias,
            body="全国年度统计公报 index",
            html=listing_html,
        ),
        # Durable reconstruction exposes the final locator for both fields;
        # resolution must still use the original candidate/registry origin.
        url=redirected_alias,
    )
    fetch = FakeFetchService(
        {
            archive: redirected_document,
            target: _document(target, body="Official 2024 bulletin."),
        }
    )
    resolver = OfficialSourceResolver(
        OfficialSourceRegistryV1(
            policy_id="test-canonical-origin-registry",
            policy_version=1,
            entries=tuple(
                replace(entry, seed_urls=(archive,))
                for entry in DEFAULT_OFFICIAL_SOURCE_REGISTRY.entries
            ),
        )
    )
    runtime, blobs = _runtime(
        tmp_path,
        FakeGateway((), fetch),
        source_resolver=resolver,
    )
    spec = compile_official_exact_fact(QUESTION)
    identity = _identity("run-redirect-alias")
    frozen_route = runtime.plan_route(spec, identity=identity)

    result = await runtime.retrieve(
        spec,
        identity=identity,
        route_decision=frozen_route,
    )

    assert result.route_decision.to_json() == frozen_route.to_json()
    assert result.fetch_count == 2
    assert [request.url for request in fetch.requests] == [archive, target]
    assert all(
        not request.url.startswith("https://stats.gov.cn/sj/zxfb/")
        for request in fetch.requests
    )
    assert len(result.pages) == 1
    assert await blobs.get(parse_blob_ref(result.pages[0].body_ref)) == (
        b"Official 2024 bulletin."
    )


@pytest.mark.asyncio
async def test_official_search_archive_hit_follows_frozen_year_in_remaining_budget(
    tmp_path: Path,
) -> None:
    archive = "https://www.stats.gov.cn/sj/tjgb/ndtjgb/"
    target = "https://www.stats.gov.cn/sj/zxfb/202502/t20250228_1958817.html"
    listing_html = (
        '<html><body><a href="/sj/zxfb/202502/t20250228_1958817.html">'
        "2024年</a></body></html>"
    )
    fetch = FakeFetchService(
        {
            archive: _document(
                archive,
                body="全国年度统计公报 index",
                html=listing_html,
            ),
            target: _document(
                target,
                body="Official 2024 population and births bulletin.",
            ),
        }
    )
    gateway = FakeGateway((_candidate(archive),), fetch)
    runtime, blobs = _runtime(tmp_path, gateway)
    spec = compile_official_exact_fact(QUESTION)
    identity = _identity("run-search-archive")
    frozen_route = runtime.plan_route(spec, identity=identity)

    result = await runtime.retrieve(
        spec,
        identity=identity,
        route_decision=frozen_route,
    )

    assert result.route_decision.to_json() == frozen_route.to_json()
    assert result.route_decision.to_json()["spec_hash"] == spec.spec_hash
    assert 0 < result.search_count <= frozen_route.to_json()["budget"]["query"]
    assert result.fetch_count == 2
    assert [request.url for request in fetch.requests] == [archive, target]
    assert fetch.requests[0].include_html is True
    assert fetch.requests[1].include_html is False
    assert len(result.pages) == 1
    assert await blobs.get(parse_blob_ref(result.pages[0].body_ref)) == (
        b"Official 2024 population and births bulletin."
    )


@pytest.mark.asyncio
async def test_direct_target_miss_escalates_to_bounded_official_search(
    tmp_path: Path,
) -> None:
    archive = "https://www.stats.gov.cn/sj/tjgb/ndtjgb/qgndtjgb/"
    direct_target = "https://www.stats.gov.cn/sj/zxfb/missing-direct.html"
    search_target = "https://www.stats.gov.cn/bulletin/search-hit.html"
    listing_html = (
        '<html><body><a href="/sj/zxfb/missing-direct.html">2024</a></body></html>'
    )
    fetch = FakeFetchService(
        {
            archive: _document(
                archive,
                final_url="https://stats.gov.cn/sj/tjgb/ndtjgb/qgndtjgb/",
                body="Annual bulletin index",
                html=listing_html,
            ),
            search_target: _document(
                search_target,
                body="Official search fallback population and births bulletin.",
            ),
        }
    )
    gateway = FakeGateway((_candidate(search_target),), fetch)
    runtime, blobs = _runtime(
        tmp_path,
        gateway,
        source_resolver=DEFAULT_OFFICIAL_SOURCE_RESOLVER,
    )
    spec = compile_official_exact_fact(QUESTION)
    identity = _identity("run-direct-miss-search")
    frozen_route = runtime.plan_route(spec, identity=identity)

    result = await runtime.retrieve(
        spec, identity=identity, route_decision=frozen_route
    )

    assert result.route_decision.to_json()["spec_hash"] == spec.spec_hash
    assert (
        result.route_decision.to_json()["route_id"]
        == frozen_route.to_json()["route_id"]
    )
    assert result.route_decision.to_json()["initial_route"] == "official_source_search"
    assert 0 < result.search_count <= result.route_decision.to_json()["budget"]["query"]
    assert [request.url for request in fetch.requests] == [
        archive,
        direct_target,
        search_target,
    ]
    assert result.fetch_count == 3
    assert result.failed_count == 1
    assert len(result.pages) == 1
    assert await blobs.get(parse_blob_ref(result.pages[0].body_ref)) == (
        b"Official search fallback population and births bulletin."
    )


@pytest.mark.asyncio
async def test_frozen_dimensions_produce_site_directives_and_ref_only_pages(
    tmp_path: Path,
) -> None:
    url = "https://www.stats.gov.cn/bulletin/2024.html"
    body = "Official population and births bulletin body."
    fetch = FakeFetchService({url: _document(url, body=body)})
    gateway = FakeGateway((_candidate(url),), fetch)
    runtime, blobs = _runtime(tmp_path, gateway)
    spec = compile_official_exact_fact(QUESTION)

    result = await runtime.retrieve(spec, identity=_identity())

    assert result.parent_deadline.budget_ms == 120_000
    assert result.search_count == len(spec.work_dimensions) * 2 == 4
    assert len(gateway.requests) == 4
    for request, dimension in zip(gateway.requests[::2], spec.work_dimensions, strict=True):
        expected_parts = [
            *dimension["search_concepts"],
            dimension["time_scope"]["label"],
            "site:stats.gov.cn",
        ]
        assert request.query == " ".join(expected_parts)
        assert len(request.query) <= 240
        assert spec.normalized_question not in request.query
        assert request.mode == "research"
        assert request.total_timeout_s is not None
        assert 0 < request.total_timeout_s <= 120
    for request in gateway.requests[1::2]:
        assert "统计公报" in request.query
        assert "2024" in request.query
        assert request.query.endswith("site:stats.gov.cn")
        assert spec.normalized_question not in request.query

    assert result.fetch_count == 1
    assert len(result.pages) == 1
    page = result.pages[0]
    serialized = page.to_json()
    assert V6FetchedPageRefPayloadV1.from_json(serialized) == page
    assert set(serialized) == {
        "schema_version",
        "page_id",
        "ordinal",
        "source_locator_ref",
        "body_ref",
        "body_hash",
        "canonical_url_hash",
        "final_url_hash",
        "authority_id",
        "title_hash",
        "media_type",
        "admission_status",
        "reason_codes",
    }
    assert not {"body", "url", "title"} & set(serialized)
    assert await blobs.get(parse_blob_ref(page.body_ref)) == body.encode("utf-8")
    locator = json.loads(
        (await blobs.get(parse_blob_ref(page.source_locator_ref))).decode("utf-8")
    )
    assert locator["canonical_url"] == url
    assert locator["final_url"] == url
    assert locator["verification_status"] == "verified"
    assert locator["authority_id"] == "cn.nbs"


@pytest.mark.asyncio
async def test_retrieval_writes_durable_child_spans_without_raw_fields(
    tmp_path: Path,
) -> None:
    url = "https://www.stats.gov.cn/bulletin/2024.html"
    body = "Official population and births bulletin body."
    fetch = FakeFetchService({url: _document(url, body=body)})
    gateway = FakeGateway((_candidate(url),), fetch)
    blobs = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    traces = TraceStore(tmp_path / "workflow.db")
    await traces.start_run(
        trace_id="trace-v6-timing",
        run_id="run-v6-timing",
        session_id="session-v6-timing",
        kind="workflow",
        workflow_name="deep_research",
        workflow_version="v6",
    )
    parent = await traces.start_span(
        trace_id="trace-v6-timing",
        span_id="parent-v6-collect-pages",
        run_id="run-v6-timing",
        workflow_name="deep_research",
        workflow_version="v6",
        node_id="collect_pages",
        lifecycle_stage="node",
        name="collect_pages",
        kind=SpanKind.NODE,
    )
    runtime = DeepResearchV6EvidenceRuntime(
        gateway,
        blobs=blobs,
        trace_store=traces,
        source_resolver=NO_SEED_SOURCE_RESOLVER,
    )
    with use_span(SpanContext(parent.trace_id, parent.span_id, parent.run_id)):
        await runtime.retrieve(
            compile_official_exact_fact(QUESTION),
            identity=_identity("run-v6-timing"),
        )
    assert await traces.finish_span(parent.span_id, SpanStatus.OK) is True

    tree = await traces.tree("trace-v6-timing")
    assert tree is not None
    children = [
        row for row in tree["spans"] if row["parent_span_id"] == parent.span_id
    ]
    assert {row["name"] for row in children} == {"official_search", "page_fetch"}
    assert all(row["status"] == "ok" for row in children)
    serialized = json.dumps(children, ensure_ascii=False)
    assert url not in serialized
    assert body not in serialized
    assert QUESTION not in serialized


@pytest.mark.asyncio
async def test_final_redirect_to_lookalike_domain_is_rejected_and_not_returned(
    tmp_path: Path,
) -> None:
    requested = "https://www.stats.gov.cn/bulletin/result.html"
    evil = "https://stats.gov.cn.evil.example/stolen.html"
    fetch = FakeFetchService(
        {requested: _document(requested, final_url=evil, body="plausible fake body")}
    )
    gateway = FakeGateway((_candidate(requested),), fetch)
    runtime, blobs = _runtime(tmp_path, gateway)

    result = await runtime.retrieve(
        compile_official_exact_fact(QUESTION), identity=_identity("run-evil")
    )

    assert result.pages == ()
    assert result.rejected_count == 1
    assert result.fetch_count == 1
    locator_payloads: list[dict[str, object]] = []
    for path in (tmp_path / "blobs").glob("*/*"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and "verification_status" in value:
            locator_payloads.append(value)
    rejected = [
        value for value in locator_payloads if value["verification_status"] == "rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["final_url"] == evil
    assert rejected[0]["final_url_hash"] == hashlib.sha256(evil.encode()).hexdigest()
    # Reading through the registered store proves the rejected locator is not
    # merely an unregistered file-system side effect.
    rejected_digest = hashlib.sha256(
        json.dumps(
            rejected[0],
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert await blobs.get(rejected_digest)


@pytest.mark.asyncio
async def test_one_page_timeout_keeps_other_verified_page(
    tmp_path: Path,
) -> None:
    fast = "https://www.stats.gov.cn/fast.html"
    slow = "https://www.stats.gov.cn/slow.html"
    fetch = FakeFetchService(
        {
            fast: _document(fast, body="fast official body"),
            slow: _document(slow, body="slow official body"),
        },
        delays={slow: 0.05},
    )
    gateway = FakeGateway((_candidate(fast, 0), _candidate(slow, 1)), fetch)
    runtime, _ = _runtime(tmp_path, gateway, page_deadline_ms=10)

    result = await runtime.retrieve(
        compile_official_exact_fact(QUESTION), identity=_identity("run-partial")
    )

    assert len(result.pages) == 1
    assert result.timeout_count == 1
    assert result.failed_count == 0
    assert result.fetch_count == 2
    assert len(fetch.requests) == 2
    assert all(0 < request.timeout <= 0.010 for request in fetch.requests)
    assert all(request.deadline_monotonic is not None for request in fetch.requests)


@pytest.mark.asyncio
async def test_parent_and_child_deadlines_bound_eight_fetches_to_four_concurrent(
    tmp_path: Path,
) -> None:
    urls = tuple(f"https://www.stats.gov.cn/page-{index}.html" for index in range(8))
    fetch = FakeFetchService(
        {url: _document(url, body=f"official body {index}") for index, url in enumerate(urls)},
        delays={url: 0.10 for url in urls},
    )
    gateway = FakeGateway(tuple(_candidate(url, index) for index, url in enumerate(urls)), fetch)
    runtime, _ = _runtime(tmp_path, gateway, page_deadline_ms=1_000)

    result = await runtime.retrieve(
        compile_official_exact_fact(QUESTION), identity=_identity("run-concurrency")
    )

    assert result.parent_deadline.budget_ms == 120_000
    assert len(fetch.requests) == 8
    assert len(result.pages) == 8
    assert fetch.max_active == 4
    assert all(0 < request.timeout <= 1.0 for request in fetch.requests)
    assert all(request.deadline_monotonic is not None for request in fetch.requests)


@pytest.mark.asyncio
async def test_conditional_fanout_never_exceeds_frozen_lane_budget(tmp_path: Path) -> None:
    fetch = FakeFetchService({})
    gateway = SlowCountingGateway((), fetch)
    runtime, _ = _runtime(
        tmp_path,
        gateway,
        max_lanes=3,
        max_queries=6,
    )
    spec = compile_research_spec(
        "深入研究生成式 AI 对设计行业的影响",
        as_of_date="2026-07-18",
    )
    identity = _identity("run-fanout-cap")
    base = runtime.plan_route(spec, identity=identity).to_json()
    requirement_id = spec.to_json()["requirements"][0]["requirement_id"]
    route = RouteDecisionV1.create(
        run_id=identity.run_id,
        spec_hash=spec.spec_hash,
        policy_ref=base["policy_ref"],
        policy_hash=base["policy_hash"],
        capability_snapshot_hash=base["capability_snapshot_hash"],
        source_health=base["source_health"],
        budget={**base["budget"], "lane": 3},
        work_groups=[
            {
                "work_group_id": f"wg-{index}",
                "requirement_ids": [requirement_id],
                "merge_key": f"test:{index}",
            }
            for index in range(3)
        ],
        initial_route="conditional_fanout",
        allowed_escalations=base["allowed_escalations"],
        reason_codes=["conditional_fanout_eligible"],
    )
    result = await runtime.retrieve(spec, identity=identity, route_decision=route)

    assert result.route_decision.to_json()["initial_route"] == "conditional_fanout"
    assert result.search_count <= result.route_decision.to_json()["budget"]["query"]
    assert gateway.max_active == min(3, result.search_count)
    assert gateway.max_active <= result.route_decision.to_json()["budget"]["lane"]


@pytest.mark.asyncio
async def test_fetch_cancelled_error_is_not_converted_to_partial_result(
    tmp_path: Path,
) -> None:
    url = "https://www.stats.gov.cn/cancelled.html"

    class CancellingFetch(FakeFetchService):
        async def fetch(self, request: FetchRequest) -> EvidenceDocument:
            self.requests.append(request)
            raise asyncio.CancelledError

    fetch = CancellingFetch({url: _document(url, body="unused")})
    gateway = FakeGateway((_candidate(url),), fetch)
    runtime, _ = _runtime(tmp_path, gateway)

    with pytest.raises(asyncio.CancelledError):
        await runtime.retrieve(
            compile_official_exact_fact(QUESTION),
            identity=_identity("run-cancelled"),
        )
