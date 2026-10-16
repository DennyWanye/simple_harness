"""Construction, route policy, control fences, spans, page loads and route planning."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from ....retrieval.contracts import EvidenceDocument, FetchRequest, SearchRequest, SearchResponse
from ....retrieval.official_sources import DEFAULT_OFFICIAL_SOURCE_RESOLVER, OfficialSourceResolver
from ...contracts import NodeExecutionIdentity
from ...deadlines import DurableDeadlineV1
from ...definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    RouteDecisionV1,
    build_route_decision_from_spec,
)
from ...definitions.deep_research_v6_evidence_contracts import V6FetchedPageRefPayloadV1
from ...store import RegisteredBlobStore
from ...trace.context import current_span
from ...trace.models import SpanKind, SpanStatus
from ...trace.store import TraceStore
from .support import (
    MAX_QUERY_COUNT,
    DeepResearchV6RetrievalResult,
    _CandidatePlan,
    _canonical_hash,
    _FetchOutcome,
    _SeedDiscoveryOutcome,
    logger,
)

PARENT_DEADLINE_MS = 120_000
PAGE_DEADLINE_MS = 20_000
MAX_FETCH_COUNT = 8
MAX_FETCH_CONCURRENCY = 4
MAX_QUERY_CHARS = 240


class _SearchPort(Protocol):
    async def search(self, request: SearchRequest) -> SearchResponse: ...


class _FetchPort(Protocol):
    async def fetch(self, request: FetchRequest) -> EvidenceDocument: ...


class _ControlSignalPort(Protocol):
    async def cancelled(self, run_id: str) -> bool: ...

    async def generate_now_requested(self, run_id: str) -> str | None: ...


class EvidenceRuntimeCore:
    """Construction, route policy, control fences, spans, page loads and route planning."""

    if TYPE_CHECKING:

        @staticmethod
        def _canonical_page_result_refs(outcomes: Sequence[_FetchOutcome]) -> tuple[str, ...]: ...
        @staticmethod
        def _canonical_pages(
            outcomes: Sequence[_FetchOutcome],
        ) -> tuple[V6FetchedPageRefPayloadV1, ...]: ...
        async def _discover_seed(
            self,
            candidate: _CandidatePlan,
            *,
            parent: DurableDeadlineV1,
            run_id: str,
            identity: NodeExecutionIdentity,
            semaphore: asyncio.Semaphore,
        ) -> _SeedDiscoveryOutcome: ...
        async def _fetch_one(
            self,
            candidate: _CandidatePlan,
            *,
            parent: DurableDeadlineV1,
            run_id: str,
            identity: NodeExecutionIdentity,
            semaphore: asyncio.Semaphore,
        ) -> _FetchOutcome: ...
        async def _persist_locator(
            self,
            *,
            candidate: _CandidatePlan,
            final_url: str,
            verification_status: Literal["verified", "rejected", "unverified"],
            identity: NodeExecutionIdentity,
        ) -> dict[str, str]: ...
        async def retrieve(
            self,
            spec: ResearchSpecV1 | Mapping[str, Any],
            *,
            identity: object | None = ...,
            route_decision: RouteDecisionV1 | Mapping[str, Any] | None = ...,
        ) -> DeepResearchV6RetrievalResult: ...

    def __init__(
        self,
        search_gateway: _SearchPort,
        *,
        blobs: RegisteredBlobStore,
        fetch_service: _FetchPort | None = None,
        source_resolver: OfficialSourceResolver = DEFAULT_OFFICIAL_SOURCE_RESOLVER,
        parent_deadline_ms: int = PARENT_DEADLINE_MS,
        page_deadline_ms: int = PAGE_DEADLINE_MS,
        max_queries: int = MAX_QUERY_COUNT,
        max_fetches: int = MAX_FETCH_COUNT,
        max_fetch_concurrency: int = MAX_FETCH_CONCURRENCY,
        max_lanes: int = MAX_QUERY_COUNT,
        max_query_chars: int = MAX_QUERY_CHARS,
        wall_clock: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
        trace_store: TraceStore | None = None,
        durable_reads: object | None = None,
        control_signals: _ControlSignalPort | None = None,
        dispatch_fence_acquirer: Callable[[str], Awaitable[Any]] | None = None,
    ) -> None:
        selected_fetch = fetch_service or getattr(search_gateway, "fetch_service", None)
        if not callable(getattr(search_gateway, "search", None)):
            raise TypeError("search_gateway must expose async search()")
        if selected_fetch is None or not callable(getattr(selected_fetch, "fetch", None)):
            raise TypeError("SearchGateway must have a fetch service with async fetch()")
        if not isinstance(blobs, RegisteredBlobStore):
            raise TypeError("blobs must be a RegisteredBlobStore")
        if not 1 <= int(parent_deadline_ms) <= PARENT_DEADLINE_MS:
            raise ValueError("parent_deadline_ms must be in 1..120000")
        if not 1 <= int(page_deadline_ms) <= PAGE_DEADLINE_MS:
            raise ValueError("page_deadline_ms must be in 1..20000")
        if not 1 <= int(max_queries) <= MAX_QUERY_COUNT:
            raise ValueError("max_queries must be in 1..6")
        if not 1 <= int(max_fetches) <= MAX_FETCH_COUNT:
            raise ValueError("max_fetches must be in 1..8")
        if not 1 <= int(max_fetch_concurrency) <= MAX_FETCH_CONCURRENCY:
            raise ValueError("max_fetch_concurrency must be in 1..4")
        if not 0 <= int(max_lanes) <= MAX_QUERY_COUNT:
            raise ValueError("max_lanes must be in 0..6")
        if not 32 <= int(max_query_chars) <= MAX_QUERY_CHARS:
            raise ValueError("max_query_chars must be in 32..240")
        self.search_gateway = search_gateway
        self.fetch_service = selected_fetch
        self.blobs = blobs
        # RegisteredBlobStore uses one SQLite transaction per put.  Keep that
        # short write section serial without reducing upstream fetch parallelism.
        self._blob_put_lock = asyncio.Lock()
        self.source_resolver = source_resolver
        self.parent_deadline_ms = int(parent_deadline_ms)
        self.page_deadline_ms = int(page_deadline_ms)
        self.max_queries = int(max_queries)
        self.max_fetches = int(max_fetches)
        self.max_fetch_concurrency = int(max_fetch_concurrency)
        self.max_lanes = int(max_lanes)
        self.max_query_chars = int(max_query_chars)
        self.wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self.monotonic_ns = monotonic_ns or time.monotonic_ns
        self.trace_store = trace_store or TraceStore(blobs.database)
        self.durable_reads = durable_reads
        self.dispatch_fence_acquirer = dispatch_fence_acquirer
        self.control_signals = control_signals
        self._policy: dict[str, Any] = {
            "policy_id": "deep-research-v6-official-retrieval-v1",
            "parent_deadline_ms": self.parent_deadline_ms,
            "page_deadline_ms": self.page_deadline_ms,
            "max_queries": self.max_queries,
            "max_fetches": self.max_fetches,
            "max_fetch_concurrency": self.max_fetch_concurrency,
            "max_lanes": self.max_lanes,
            "official_source_policy_hash": self.source_resolver.registry.policy_hash,
        }
        self._policy_hash = _canonical_hash(self._policy)

    @property
    def route_policy(self) -> dict[str, Any]:
        """Return the exact bytes source bound into every persisted route."""

        return copy.deepcopy(self._policy)

    async def _control_fenced(self, run_id: str) -> bool:
        if self.control_signals is None:
            return False
        generate_now = getattr(self.control_signals, "generate_now_requested", None)
        if callable(generate_now):
            command = generate_now(run_id)
            if hasattr(command, "__await__"):
                command = await command
            if command is not None:
                return True
        return bool(await self.control_signals.cancelled(run_id))

    async def _start_child_span(
        self,
        *,
        identity: NodeExecutionIdentity,
        request_id: str,
        stage: str,
        ordinal: int,
    ) -> str | None:
        parent = current_span()
        if parent is None:
            return None
        span_id = hashlib.sha256(
            f"{parent.span_id}|{stage}|{request_id}|{ordinal}".encode()
        ).hexdigest()
        try:
            await self.trace_store.start_span(
                trace_id=parent.trace_id,
                span_id=span_id,
                parent_span_id=parent.span_id,
                run_id=identity.run_id,
                workflow_name="deep_research",
                workflow_version="v6",
                node_id=identity.node_id,
                lifecycle_stage=stage,
                name=stage,
                kind=SpanKind.TOOL,
                attributes={
                    "request_id": request_id,
                    "ordinal": ordinal,
                    "stage": stage,
                    "attempt": identity.attempt,
                },
            )
        except Exception:
            return None
        return span_id

    async def _finish_child_span(
        self,
        span_id: str | None,
        status: SpanStatus,
        *,
        outcome: str,
        count: int = 0,
    ) -> None:
        if span_id is None:
            return
        try:
            updated = await self.trace_store.finish_span(
                span_id,
                status,
                attributes={"outcome": outcome, "count": max(0, int(count))},
            )
            if not updated:
                return
            completed = await self.trace_store.span(span_id)
            if completed is None:
                return
            detail = {
                "run_id": str(completed.get("run_id") or ""),
                "workflow_version": "v6",
                "stage": str(completed.get("name") or ""),
                "status": str(status),
                "outcome": outcome,
                "duration_ms": float(completed.get("duration_ms") or 0.0),
                "count": max(0, int(count)),
            }
            logger.info("deepresearch_stage_timing", **detail)
            try:
                # 按需导入：测试替身（observability.metrics_sink）
                from observability.metrics_sink import record

                record("deepresearch_stage_timing", detail)
            except Exception:
                pass
        except Exception:
            return

    async def load_pages(
        self,
        *,
        spec: Mapping[str, Any],
        identity: object | None,
        route_decision: RouteDecisionV1 | Mapping[str, Any] | None = None,
    ) -> tuple[V6FetchedPageRefPayloadV1, ...]:
        """Return only registered refs and bounded metadata for graph state."""

        return (
            await self.retrieve(
                spec,
                identity=identity,
                route_decision=route_decision,
            )
        ).pages

    def plan_route(
        self,
        spec: ResearchSpecV1 | Mapping[str, Any],
        *,
        identity: object | None,
    ) -> RouteDecisionV1:
        """Freeze the route before retrieval so restart never recomputes it."""

        research_spec = spec if isinstance(spec, ResearchSpecV1) else ResearchSpecV1.from_json(spec)
        if not isinstance(identity, NodeExecutionIdentity):
            raise TypeError("v6 route planning requires a bound NodeExecutionIdentity")
        work_group_count = max(1, len(research_spec.to_json()["work_dimensions"]))
        llm_budget = (
            0 if research_spec.intent_type == "official_exact_fact" else work_group_count * 4
        )
        return build_route_decision_from_spec(
            research_spec,
            run_id=identity.run_id,
            policy_hash=self._policy_hash,
            capability_snapshot_hash=_canonical_hash(
                {
                    "max_queries": self.max_queries,
                    "max_fetches": self.max_fetches,
                    "max_fetch_concurrency": self.max_fetch_concurrency,
                    "max_lanes": self.max_lanes,
                }
            ),
            budget={
                "query": self.max_queries,
                "fetch": self.max_fetches,
                "browser": 0,
                "llm": llm_budget,
                "lane": self.max_lanes,
            },
        )
