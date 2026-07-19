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
    OfficialSourceRegistryV1,
    OfficialSourceResolver,
)
from deskpet.workflows import NodeExecutionIdentity
from deskpet.workflows.adapters.deep_research_v6_evidence_runtime import (
    DeepResearchV6EvidenceRuntime,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import (
    compile_official_exact_fact,
    compile_research_spec,
)
from deskpet.workflows.definitions.deep_research_v6_contracts import parse_blob_ref
from deskpet.workflows.store import RegisteredBlobStore


NO_SEED_SOURCE_RESOLVER = OfficialSourceResolver(
    OfficialSourceRegistryV1(
        policy_id="test-v6-lanes-no-seed",
        policy_version=1,
        entries=tuple(
            replace(entry, seed_urls=())
            for entry in DEFAULT_OFFICIAL_SOURCE_REGISTRY.entries
        ),
    )
)


def _identity(run_id: str) -> NodeExecutionIdentity:
    return NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v6",
        thread_id="thread-lane-runtime",
        run_id=run_id,
        checkpoint_id="checkpoint-1",
        checkpoint_ns="",
        task_id="task-1",
        node_id="collect_pages",
        attempt=1,
    )


class DynamicFetch:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.requests: list[FetchRequest] = []
        self.active = 0
        self.max_active = 0

    async def fetch(self, request: FetchRequest) -> EvidenceDocument:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            body = f"evidence for {hashlib.sha256(request.url.encode()).hexdigest()[:12]}"
            digest = hashlib.sha256(body.encode()).hexdigest()
            return EvidenceDocument(
                stable_id="document-" + digest[:12],
                url=request.url,
                canonical_url=request.url,
                title="General source",
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
            )
        finally:
            self.active -= 1


class DimensionGateway:
    def __init__(self, fetch_service: DynamicFetch, *, delay: float = 0.0) -> None:
        self.fetch_service = fetch_service
        self.delay = delay
        self.requests: list[SearchRequest] = []
        self.active = 0
        self.max_active = 0

    async def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if "site:stats.gov.cn" in request.query:
                url = f"https://www.stats.gov.cn/{request.dimension_id}.html"
            else:
                url = f"https://{request.dimension_id}.example.test/source.html"
            candidate = RetrievalCandidate(
                stable_id="candidate-" + hashlib.sha256(url.encode()).hexdigest()[:12],
                url=url,
                canonical_url=url,
                title="Search result",
                provider="fake",
                providers=("fake",),
                provider_rank=0,
                searched_at="2026-07-18T00:00:00+00:00",
            )
            return SearchResponse(
                query=request.query,
                results=(candidate,),
                request_id=request.request_id,
                run_id=request.run_id,
            )
        finally:
            self.active -= 1


def _runtime(
    tmp_path: Path,
    *,
    search_delay: float = 0.01,
    fetch_delay: float = 0.01,
    **kwargs,
) -> tuple[DeepResearchV6EvidenceRuntime, DimensionGateway, DynamicFetch]:
    fetch = DynamicFetch(delay=fetch_delay)
    gateway = DimensionGateway(fetch, delay=search_delay)
    runtime = DeepResearchV6EvidenceRuntime(
        gateway,
        blobs=RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db"),
        source_resolver=NO_SEED_SOURCE_RESOLVER,
        **kwargs,
    )
    return runtime, gateway, fetch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,expected_groups",
    [
        ("人工智能会怎样影响设计行业？", 4),
        ("最新人工智能政策方向及影响是什么？", 5),
    ],
)
async def test_open_and_policy_enable_conditional_lane_search(
    tmp_path: Path, question: str, expected_groups: int
) -> None:
    runtime, gateway, _ = _runtime(tmp_path)
    spec = compile_research_spec(question, as_of_date="2026-07-18")

    result = await runtime.retrieve(spec, identity=_identity("run-fanout-" + spec.intent_type))

    assert result.route_decision.to_json()["initial_route"] == "conditional_fanout"
    assert result.search_count == expected_groups
    assert gateway.max_active > 1
    assert result.pages
    assert all(page.authority_id.startswith("web_") for page in result.pages)
    assert all(page.authority_id != "cn.nbs" for page in result.pages)
    locator = json.loads(
        (
            await runtime.blobs.get(
                parse_blob_ref(result.pages[0].source_locator_ref)
            )
        ).decode("utf-8")
    )
    assert locator["verification_status"] == "verified"
    assert locator["authority_id"].startswith("web_")


@pytest.mark.asyncio
async def test_top_n_is_serial_and_exact_fact_forbids_fanout(tmp_path: Path) -> None:
    runtime, gateway, _ = _runtime(tmp_path / "topn")
    topn = compile_research_spec(
        "当前最值得关注的 10 个 AI 产品及其优缺点",
        as_of_date="2026-07-18",
    )
    topn_result = await runtime.retrieve(topn, identity=_identity("run-topn"))
    assert topn_result.route_decision.to_json()["initial_route"] == "general_search"
    assert gateway.max_active == 1

    exact_runtime, exact_gateway, _ = _runtime(tmp_path / "exact")
    exact = compile_official_exact_fact(
        "2024年中国总人口和出生人口分别是多少？优先国家统计局。"
    )
    exact_result = await exact_runtime.retrieve(exact, identity=_identity("run-exact"))
    assert exact_result.route_decision.to_json()["initial_route"] == "official_source_search"
    assert exact_gateway.max_active == 1


@pytest.mark.asyncio
async def test_lane_budget_insufficient_falls_back_to_serial(tmp_path: Path) -> None:
    runtime, gateway, _ = _runtime(tmp_path, max_lanes=2)
    spec = compile_research_spec(
        "人工智能会怎样影响设计行业？", as_of_date="2026-07-18"
    )

    result = await runtime.retrieve(spec, identity=_identity("run-lane-budget"))

    assert result.route_decision.to_json()["initial_route"] == "general_search"
    assert gateway.max_active == 1


def test_plan_route_freezes_llm_budget_by_intent(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    exact = compile_official_exact_fact(
        "2024年中国总人口和出生人口分别是多少？优先国家统计局。"
    )
    generic = compile_research_spec(
        "人工智能会怎样影响设计行业？", as_of_date="2026-07-18"
    )

    exact_route = runtime.plan_route(exact, identity=_identity("run-route-exact"))
    generic_route = runtime.plan_route(
        generic, identity=_identity("run-route-generic")
    )

    assert exact_route.to_json()["budget"]["llm"] == 0
    assert generic_route.to_json()["budget"]["llm"] == (
        len(generic.to_json()["work_dimensions"]) * 4
    )


@pytest.mark.asyncio
async def test_retrieve_consumes_persisted_route_without_replanning(
    tmp_path: Path,
) -> None:
    runtime, gateway, _ = _runtime(tmp_path, max_lanes=6)
    spec = compile_research_spec(
        "人工智能会怎样影响设计行业？", as_of_date="2026-07-18"
    )
    identity = _identity("run-persisted-route")
    frozen = runtime.plan_route(spec, identity=identity)
    assert frozen.to_json()["initial_route"] == "conditional_fanout"
    runtime.max_lanes = 1

    result = await runtime.retrieve(
        spec,
        identity=identity,
        route_decision=frozen.to_json(),
    )

    assert result.route_decision.to_json() == frozen.to_json()
    assert gateway.max_active > 1


@pytest.mark.asyncio
async def test_retrieve_rejects_route_from_another_run(tmp_path: Path) -> None:
    runtime, _, _ = _runtime(tmp_path)
    spec = compile_research_spec(
        "人工智能会怎样影响设计行业？", as_of_date="2026-07-18"
    )
    frozen = runtime.plan_route(spec, identity=_identity("run-route-a"))

    with pytest.raises(ValueError, match="route decision identity mismatch"):
        await runtime.retrieve(
            spec,
            identity=_identity("run-route-b"),
            route_decision=frozen,
        )


@pytest.mark.asyncio
async def test_lane_merge_is_replay_stable_and_budgets_are_shared(tmp_path: Path) -> None:
    question = "比较 ChatGPT 和 Claude，按功能、价格、易用性、性能、安全性、生态六个轴比较。"
    spec = compile_research_spec(question, as_of_date="2026-07-18")
    runtime, gateway, fetch = _runtime(
        tmp_path,
        max_queries=4,
        max_fetches=3,
        max_fetch_concurrency=2,
        max_lanes=6,
    )

    first = await runtime.retrieve(spec, identity=_identity("run-replay-1"))
    second = await runtime.retrieve(spec, identity=_identity("run-replay-2"))

    assert first.route_decision.to_json()["initial_route"] == "conditional_fanout"
    assert first.search_count == second.search_count == 4
    assert first.fetch_count == second.fetch_count == 3
    assert len(gateway.requests) == 8
    assert len(fetch.requests) == 6
    assert fetch.max_active <= 2
    assert [page.to_json() for page in first.pages] == [
        page.to_json() for page in second.pages
    ]
