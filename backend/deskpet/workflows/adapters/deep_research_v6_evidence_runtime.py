"""Bounded official-source retrieval port for the DeepResearch v6 graph.

The semantic compiler owns the question decomposition.  This adapter only
consumes frozen ``ResearchSpecV1.work_dimensions`` and turns each dimension
into a bounded official-site search.  It deliberately never reads
``normalized_question`` and contains no answer values, years, or answer URLs.

The public ``load_pages`` method returns ref-only graph inputs.  Page bodies and
source locators (the only objects containing raw URLs) are first persisted in a
``RegisteredBlobStore``.  Only pages whose *final* fetched URL still belongs to
the resolved authority are returned.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
import structlog
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, Literal, Mapping, Protocol, Sequence
from urllib.parse import urljoin, urlsplit

from deskpet.execution.dispatch import dispatch_with_run_fence

from ...retrieval.contracts import (
    EvidenceDocument,
    FetchRequest,
    RetrievalCandidate,
    SearchRequest,
    SearchResponse,
)
from ...retrieval.official_sources import (
    DEFAULT_OFFICIAL_SOURCE_RESOLVER,
    OfficialSourceRequestV1,
    OfficialSourceResolver,
    OfficialSourceTargetV1,
)
from ..contracts import (
    JsonValue,
    NodeExecutionIdentity,
    canonical_json,
)
from ..deadlines import (
    DeadlineValidationError,
    DurableDeadlineV1,
    checkpoint_deadline,
    create_child_deadline,
    remaining_timeout_seconds,
    resume_deadline,
)
from ..definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    RouteDecisionV1,
    build_route_decision_from_spec,
    conditional_fanout_enabled,
    format_blob_ref,
    sha256_json,
)
from ..definitions.deep_research_v6_evidence_contracts import V6FetchedPageRefPayloadV1
from ..definitions.deep_research_v6_retrieval_contracts import (
    OfficialSearchResultV1,
    PageExtractionResultV1,
    SearchCandidateV1,
    SourceLocatorV1,
)
from ..store import BlobRef, RegisteredBlobStore
from ..trace.context import current_span
from ..trace.models import SpanKind, SpanStatus
from ..trace.store import TraceStore


PARENT_DEADLINE_MS = 120_000
PAGE_DEADLINE_MS = 20_000
MAX_QUERY_COUNT = 6
MAX_FETCH_COUNT = 8
MAX_FETCH_CONCURRENCY = 4
MAX_QUERY_CHARS = 240

_SPACE = re.compile(r"\s+")
logger = structlog.get_logger(__name__)


class _SearchPort(Protocol):
    async def search(self, request: SearchRequest) -> SearchResponse: ...


class _FetchPort(Protocol):
    async def fetch(self, request: FetchRequest) -> EvidenceDocument: ...


class _ControlSignalPort(Protocol):
    async def cancelled(self, run_id: str) -> bool: ...

    async def generate_now_requested(self, run_id: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class DeepResearchV6RetrievalResult:
    """Inspectable result while ``load_pages`` exposes only graph page inputs."""

    pages: tuple["V6FetchedPageRefPayloadV1", ...]
    parent_deadline: DurableDeadlineV1
    search_count: int
    fetch_count: int
    timeout_count: int
    rejected_count: int
    failed_count: int
    route_decision: RouteDecisionV1
    page_result_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _GeneralSourceTarget:
    target_id: str
    authority_id: str
    jurisdiction: str
    organization_name: str
    source_types: tuple[str, ...]
    verification_policy_hash: str
    requested_host: str


_SourceTarget = OfficialSourceTargetV1 | _GeneralSourceTarget


@dataclass(frozen=True, slots=True)
class _QueryPlan:
    ordinal: int
    dimension_id: str
    requirement_ids: tuple[str, ...]
    query_terms: tuple[str, ...]
    query: str
    request_id: str
    target: _SourceTarget
    discovery_years: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _CandidatePlan:
    ordinal: int
    dimension_id: str
    candidate_id: str
    requested_url: str
    title: str
    target: _SourceTarget
    discovery_years: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _VerifiedPage:
    source_locator_ref: str
    canonical_url_hash: str
    final_url_hash: str
    body_ref: str
    body_hash: str
    authority_id: str
    title_hash: str
    admission_reason: str


@dataclass(frozen=True, slots=True)
class _FetchOutcome:
    ordinal: int
    status: Literal["verified", "rejected", "timeout", "failed"]
    upstream_called: bool
    page: _VerifiedPage | None = None
    page_result_ref: str | None = None


@dataclass(frozen=True, slots=True)
class _SeedDiscoveryOutcome:
    status: Literal["discovered", "empty", "rejected", "timeout", "failed"]
    upstream_called: bool
    candidates: tuple[_CandidatePlan, ...] = ()
    page_result_ref: str | None = None


class _AnchorCollector(HTMLParser):
    """Collect bounded anchor href/text pairs without retaining a DOM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._text: list[str] = []
        self.anchors: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = next((value for key, value in attrs if key.casefold() == "href"), None)
        self._href = href.strip() if isinstance(href, str) else None
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None and len(self._text) < 8:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "a" or self._href is None:
            return
        self.anchors.append((self._href, _SPACE.sub(" ", "".join(self._text)).strip()))
        self._href = None
        self._text = []


def _canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + _canonical_hash(value)[:24]


def _clean_part(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return _SPACE.sub(" ", value).strip()[:limit].strip()


def _bounded_query(
    concepts: Sequence[str],
    time_label: str | None,
    directive: str,
    *,
    max_chars: int,
) -> str:
    """Build a query from frozen semantic fields while preserving ``site:``."""

    site = _clean_part(directive, limit=max_chars)
    if not site.startswith("site:") or len(site) >= max_chars:
        raise ValueError("official source directive does not fit the query bound")
    semantic: list[str] = []
    for raw in (*concepts, time_label):
        part = _clean_part(raw, limit=80)
        if part and part not in semantic:
            semantic.append(part)
    available = max_chars - len(site) - 1
    selected: list[str] = []
    for part in semantic:
        separator = 1 if selected else 0
        if available <= separator:
            break
        bounded = part[: available - separator].rstrip()
        if not bounded:
            break
        selected.append(bounded)
        available -= len(bounded) + separator
    return " ".join((*selected, site))


def _bounded_general_query(
    concepts: Sequence[str],
    time_label: str | None,
    *,
    max_chars: int,
) -> tuple[str, tuple[str, ...]]:
    """Build a provider-neutral query only from frozen work-dimension fields."""

    selected: list[str] = []
    remaining = max_chars
    for raw in (*concepts, time_label):
        part = _clean_part(raw, limit=80)
        if not part or part in selected:
            continue
        separator = 1 if selected else 0
        if remaining <= separator:
            break
        bounded = part[: remaining - separator].rstrip()
        if not bounded:
            break
        selected.append(bounded)
        remaining -= len(bounded) + separator
    if not selected:
        raise ValueError("general search requires frozen semantic concepts")
    return " ".join(selected), tuple(selected)


_GENERAL_VERIFICATION_POLICY_HASH = sha256_json(
    {
        "schema_version": 1,
        "policy_id": "deep-research-v6-general-same-host-v1",
        "redirect_policy": "exact_host",
    }
)


def _general_source_target(
    url: str,
    *,
    source_types: Sequence[str],
) -> _GeneralSourceTarget:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("general search candidate URL is not verifiable")
    host_hash = hashlib.sha256(host.encode("utf-8")).hexdigest()
    return _GeneralSourceTarget(
        target_id="general_" + host_hash[:24],
        authority_id="web_" + host_hash[:24],
        jurisdiction="wt-wt",
        organization_name=host,
        source_types=tuple(sorted(set(source_types))),
        verification_policy_hash=_GENERAL_VERIFICATION_POLICY_HASH,
        requested_host=host,
    )


def _candidate_url(candidate: RetrievalCandidate) -> str:
    canonical = candidate.canonical_url.strip()
    return canonical or candidate.url.strip()


def _compact_official_query(
    concepts: Sequence[str],
    time_scope: Mapping[str, Any],
    directive: str,
    *,
    locale: str,
    max_chars: int,
) -> tuple[str, tuple[str, ...]]:
    """Build a short, generic official-publication discovery query.

    This is a semantic fallback for annual exact facts, not a question or page
    oracle: it uses only the frozen time scope, localized concepts and source
    type vocabulary.
    """

    date_values = tuple(
        str(time_scope.get(key) or "") for key in ("as_of", "start", "end")
    )
    years = tuple(
        dict.fromkeys(
            value[:4]
            for value in date_values
            if len(value) >= 4 and value[:4].isdigit()
        )
    )
    localized = tuple(
        part
        for part in (_clean_part(value, limit=40) for value in concepts)
        if part and any("\u4e00" <= char <= "\u9fff" for char in part)
    )[:2]
    publication_term = "统计公报" if str(locale).casefold().startswith("zh") else "statistical communique"
    terms = tuple(dict.fromkeys((*years, *localized, publication_term)))
    return _bounded_query(terms, None, directive, max_chars=max_chars), terms


def _time_scope_years(time_scope: Mapping[str, Any]) -> tuple[str, ...]:
    values = tuple(str(time_scope.get(key) or "") for key in ("as_of", "start", "end"))
    return tuple(
        dict.fromkeys(
            value[:4]
            for value in values
            if len(value) >= 4 and value[:4].isdigit()
        )
    )


def _archive_links(
    html: str,
    *,
    base_urls: Sequence[str],
    years: Sequence[str],
    target: OfficialSourceTargetV1,
    resolver: OfficialSourceResolver,
    limit: int,
) -> tuple[str, ...]:
    """Resolve same-authority annual links selected only by frozen years."""

    if not html.strip() or not years or limit <= 0:
        return ()
    parser = _AnchorCollector()
    try:
        parser.feed(html)
    except Exception:
        return ()
    selected: list[str] = []
    seen: set[str] = set()
    for href, label in parser.anchors:
        compact_label = _SPACE.sub("", label)
        if not any(year in compact_label for year in years):
            continue
        for base_url in base_urls:
            resolved = urljoin(base_url, href)
            parsed = urlsplit(resolved)
            if parsed.scheme.casefold() not in {"http", "https"}:
                continue
            resolved = parsed._replace(fragment="").geturl()
            if resolved in seen or not resolver.verify(target, resolved).matched:
                continue
            seen.add(resolved)
            selected.append(resolved)
            # One anchor contributes one candidate.  Base order expresses the
            # preference; alternate bases are only fallbacks for resolution.
            break
        if len(selected) >= limit:
            break
    return tuple(selected)


def _looks_like_archive_locator(candidate: _CandidatePlan) -> bool:
    """Classify a same-authority listing without answer-specific URLs."""

    if not isinstance(candidate.target, OfficialSourceTargetV1):
        return False
    if not candidate.discovery_years:
        return False
    path = urlsplit(candidate.requested_url).path.casefold()
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    return path.endswith("/") or leaf in {
        "index.htm",
        "index.html",
        "default.htm",
        "default.html",
    }


def _archive_resolution_bases(
    candidate: _CandidatePlan,
    *,
    final_url: str,
) -> tuple[str, ...]:
    """Prefer registry canonical origins without probing host aliases."""

    requested = urlsplit(candidate.requested_url)
    bases: list[str] = []
    if isinstance(candidate.target, OfficialSourceTargetV1):
        for seed_url in candidate.target.seed_urls:
            seed = urlsplit(seed_url)
            # Preserve the fetched listing path/query while replacing only
            # the redirect-selected alias with the registry-declared origin.
            bases.append(
                requested._replace(
                    scheme=seed.scheme,
                    netloc=seed.netloc,
                    fragment="",
                ).geturl()
            )
    bases.extend((candidate.requested_url, final_url))
    return tuple(dict.fromkeys(base.strip() for base in bases if base.strip()))


class DeepResearchV6EvidenceRuntime:
    """Real SearchGateway/fetch bridge with one parent and bounded page leases."""

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
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
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
            f"{parent.span_id}|{stage}|{request_id}|{ordinal}".encode("utf-8")
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

        research_spec = (
            spec if isinstance(spec, ResearchSpecV1) else ResearchSpecV1.from_json(spec)
        )
        if not isinstance(identity, NodeExecutionIdentity):
            raise TypeError("v6 route planning requires a bound NodeExecutionIdentity")
        work_group_count = max(1, len(research_spec.to_json()["work_dimensions"]))
        llm_budget = (
            0
            if research_spec.intent_type == "official_exact_fact"
            else work_group_count * 4
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

    async def retrieve(
        self,
        spec: ResearchSpecV1 | Mapping[str, Any],
        *,
        identity: object | None = None,
        route_decision: RouteDecisionV1 | Mapping[str, Any] | None = None,
    ) -> DeepResearchV6RetrievalResult:
        research_spec = (
            spec if isinstance(spec, ResearchSpecV1) else ResearchSpecV1.from_json(spec)
        )
        if not isinstance(identity, NodeExecutionIdentity):
            raise TypeError("v6 retrieval requires a bound NodeExecutionIdentity")
        run_id = identity.run_id
        if route_decision is None:
            frozen_route = self.plan_route(research_spec, identity=identity)
        else:
            frozen_route = (
                route_decision
                if isinstance(route_decision, RouteDecisionV1)
                else RouteDecisionV1.from_json(route_decision)
            )
            route_value = frozen_route.to_json()
            if (
                route_value["run_id"] != run_id
                or route_value["spec_hash"] != research_spec.spec_hash
                or route_value["policy_hash"] != self._policy_hash
            ):
                raise ValueError("persisted v6 route decision identity mismatch")
        if self.durable_reads is not None:
            parent, parent_lease = await self.durable_reads.deadline_port.resume_root(
                identity=identity,
                route_id=str(frozen_route.to_json()["route_id"]),
                policy_hash=self._policy_hash,
                budget_ms=self.parent_deadline_ms,
            )
        else:
            parent = DurableDeadlineV1.create(
                owner_id=run_id,
                logical_scope="deep_research_v6.official_retrieval",
                policy_hash=self._policy_hash,
                budget_ms=self.parent_deadline_ms,
                now_wall=self.wall_clock(),
            )
            resumed = resume_deadline(
                parent,
                now_wall=self.wall_clock(),
                now_monotonic_ns=self.monotonic_ns(),
            )
            parent = resumed.state
            parent_lease = resumed.lease
        if parent_lease is None:
            return DeepResearchV6RetrievalResult(
                (), parent, 0, 0, 0, 0, 0, frozen_route
            )

        route_budget = frozen_route.to_json()["budget"]
        self._active_route_id = str(frozen_route.to_json()["route_id"])
        self._active_route_budget = copy.deepcopy(route_budget)
        query_limit = min(self.max_queries, int(route_budget["query"]))
        fetch_limit = min(self.max_fetches, int(route_budget["fetch"]))
        # Execution consumes the immutable route decision. ``self.max_lanes``
        # is a planner input only; consulting it again here would silently
        # re-plan a persisted route after restart or configuration drift.
        lane_limit = max(1, min(MAX_QUERY_COUNT, int(route_budget["lane"])))
        query_plans, seed_buckets = self._build_query_plans(research_spec)
        query_plans = query_plans[:query_limit]
        candidate_buckets: list[tuple[_CandidatePlan, ...]] = []
        discovery_outcomes: tuple[_SeedDiscoveryOutcome, ...] = ()
        seed_candidates = self._select_candidates(seed_buckets)
        if seed_candidates:
            semaphore = asyncio.Semaphore(min(self.max_fetch_concurrency, lane_limit))
            discovery_tasks = [
                asyncio.create_task(
                    self._discover_seed(
                        candidate,
                        parent=parent,
                        run_id=run_id,
                        identity=identity,
                        semaphore=semaphore,
                    )
                )
                for candidate in seed_candidates
            ]
            try:
                discovery_outcomes = tuple(await asyncio.gather(*discovery_tasks))
            except asyncio.CancelledError:
                for task in discovery_tasks:
                    task.cancel()
                await asyncio.gather(*discovery_tasks, return_exceptions=True)
                raise
            transition = checkpoint_deadline(
                parent,
                parent_lease,
                now_wall=self.wall_clock(),
                now_monotonic_ns=self.monotonic_ns(),
            )
            parent, parent_lease = transition.state, transition.lease
            candidate_buckets.extend(
                outcome.candidates
                for outcome in discovery_outcomes
                if outcome.candidates
            )

        discovery_fetch_count = sum(item.upstream_called for item in discovery_outcomes)
        if await self._control_fenced(run_id):
            return DeepResearchV6RetrievalResult(
                (), parent, 0, discovery_fetch_count,
                sum(item.status == "timeout" for item in discovery_outcomes),
                sum(item.status == "rejected" for item in discovery_outcomes),
                sum(item.status == "failed" for item in discovery_outcomes),
                frozen_route,
            )
        # A matching first-party archive link is stronger and faster than a
        # third-party search result.  Search remains the bounded fallback when
        # the archive is unavailable or has no matching frozen year.
        use_search_fallback = not candidate_buckets
        search_count = 0
        fanout = conditional_fanout_enabled(
            frozen_route, intent_type=research_spec.intent_type
        )

        async def _search_plan(
            plan: _QueryPlan, *, query_timeout_s: float
        ) -> tuple[_CandidatePlan, ...]:
            request = SearchRequest(
                query=plan.query,
                max_results=min(5, self.max_fetches),
                region=(
                    plan.target.jurisdiction.casefold()
                    if isinstance(plan.target, OfficialSourceTargetV1)
                    else "wt-wt"
                ),
                mode="research",
                hydrate_top=0,
                total_timeout_s=query_timeout_s,
                request_id=plan.request_id,
                run_id=run_id,
                dimension_id=plan.dimension_id,
                query_terms=plan.query_terms,
            )
            response: SearchResponse | None = None
            search_span = await self._start_child_span(
                identity=identity,
                request_id=plan.request_id,
                stage=(
                    "official_search"
                    if isinstance(plan.target, OfficialSourceTargetV1)
                    else "general_search"
                ),
                ordinal=plan.ordinal,
            )
            try:
                if self.durable_reads is None:
                    response = await asyncio.wait_for(
                        dispatch_with_run_fence(
                            acquire_fence=self.dispatch_fence_acquirer,
                            run_id=run_id,
                            operation_kind="workflow.research.search",
                            operation_id=plan.request_id,
                            invoke=lambda: self.search_gateway.search(request),
                        ),
                        timeout=query_timeout_s,
                    )
                else:
                    async def _transport(attempt) -> OfficialSearchResultV1:
                        try:
                            attempt_timeout_s = query_timeout_s
                            if attempt.deadline_monotonic_ns is not None:
                                attempt_timeout_s = min(
                                    attempt_timeout_s,
                                    max(0.0, (attempt.deadline_monotonic_ns - self.monotonic_ns()) / 1_000_000_000),
                                )
                            item = await asyncio.wait_for(
                                dispatch_with_run_fence(
                                    acquire_fence=self.dispatch_fence_acquirer,
                                    run_id=run_id,
                                    operation_kind="workflow.research.search",
                                    operation_id=attempt.logical_effect_id,
                                    invoke=lambda: self.search_gateway.search(request),
                                ),
                                timeout=attempt_timeout_s,
                            )
                        except asyncio.CancelledError:
                            raise
                        except TimeoutError:
                            return OfficialSearchResultV1.create(
                                logical_effect_id=attempt.logical_effect_id,
                                attempt_no=attempt.attempt_no,
                                request_id=plan.request_id,
                                query_hash=hashlib.sha256(plan.query.encode("utf-8")).hexdigest(),
                                target_id=plan.target.target_id,
                                candidates=(), outcome="timeout",
                                error_code="deadline_expired", deadline_id=attempt.deadline_id,
                            )
                        except Exception:
                            return OfficialSearchResultV1.create(
                                logical_effect_id=attempt.logical_effect_id,
                                attempt_no=attempt.attempt_no,
                                request_id=plan.request_id,
                                query_hash=hashlib.sha256(plan.query.encode("utf-8")).hexdigest(),
                                target_id=plan.target.target_id,
                                candidates=(), outcome="failed",
                                error_code="search_failed", deadline_id=attempt.deadline_id,
                            )
                        typed_candidates: list[SearchCandidateV1] = []
                        for raw_candidate in item.results:
                            candidate_url = _candidate_url(raw_candidate)
                            if not candidate_url.startswith(("http://", "https://")):
                                continue
                            locator = await self._persist_locator(
                                candidate=_CandidatePlan(
                                    ordinal=len(typed_candidates),
                                    dimension_id=plan.dimension_id,
                                    candidate_id=raw_candidate.stable_id,
                                    requested_url=candidate_url,
                                    title=raw_candidate.title,
                                    target=plan.target,
                                    discovery_years=plan.discovery_years,
                                ),
                                final_url=candidate_url,
                                verification_status="unverified",
                                identity=identity,
                            )
                            typed_candidates.append(SearchCandidateV1.create(
                                ordinal=len(typed_candidates),
                                source_locator_ref=locator["source_locator_ref"],
                                title_hash=hashlib.sha256(raw_candidate.title.encode("utf-8")).hexdigest(),
                                snippet_hash=hashlib.sha256(raw_candidate.snippet.encode("utf-8")).hexdigest(),
                                authority_match="unverified",
                                reason_codes=("candidate_unverified",),
                            ))
                        return OfficialSearchResultV1.create(
                            logical_effect_id=attempt.logical_effect_id,
                            attempt_no=attempt.attempt_no,
                            request_id=plan.request_id,
                            query_hash=hashlib.sha256(plan.query.encode("utf-8")).hexdigest(),
                            target_id=plan.target.target_id,
                            candidates=tuple(typed_candidates),
                            outcome="succeeded" if typed_candidates else "empty",
                            error_code=None, deadline_id=attempt.deadline_id,
                        )
                    durable = await self.durable_reads.execute(
                        operation_kind="official_search",
                        route_id=str(frozen_route.to_json()["route_id"]),
                        target_or_page_id=plan.target.target_id,
                        ordinal=plan.ordinal,
                        resource_kind="query",
                        resource_hard_limit=int(route_budget["query"]),
                        resource_policy_hash=self._policy_hash,
                        deadline=parent,
                        identity=identity,
                        transport=_transport,
                    )
                    typed = OfficialSearchResultV1.from_json(durable)
                    if typed.outcome != "succeeded":
                        raise TimeoutError if typed.outcome == "timeout" else RuntimeError
                    replayed: list[RetrievalCandidate] = []
                    for candidate in typed.candidates:
                        raw_locator = await self.blobs.get(
                            candidate.source_locator_ref.removeprefix("sha256:")
                        )
                        locator_value = json.loads(raw_locator.decode("utf-8"))
                        if not isinstance(locator_value, Mapping):
                            raise ValueError("source locator blob must be an object")
                        locator = SourceLocatorV1.from_json(locator_value)
                        if locator.verification_status != candidate.authority_match:
                            raise ValueError("search candidate/locator authority mismatch")
                        replayed.append(RetrievalCandidate(
                            stable_id=candidate.candidate_id,
                            url=locator.canonical_url,
                            canonical_url=locator.canonical_url,
                            title="",
                        ))
                    response = SearchResponse(
                        query=plan.query,
                        results=tuple(replayed),
                        request_id=typed.request_id,
                        run_id=run_id,
                    )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(
                        search_span, SpanStatus.CANCELLED, outcome="cancelled"
                    )
                )
                raise
            except TimeoutError:
                await self._finish_child_span(
                    search_span, SpanStatus.ERROR, outcome="timeout"
                )
                response = None
            except Exception:
                # Search failures are evidence gaps.  Other frozen dimensions
                # can still return useful pages under the same parent budget.
                await self._finish_child_span(
                    search_span, SpanStatus.ERROR, outcome="error"
                )
                response = None
            else:
                await self._finish_child_span(
                    search_span,
                    SpanStatus.OK,
                    outcome="hit" if response.results else "empty",
                    count=len(response.results),
                )
            if isinstance(response, SearchResponse):
                candidates: list[_CandidatePlan] = []
                for candidate in response.results:
                    candidate_url = _candidate_url(candidate)
                    if not candidate_url.startswith(("http://", "https://")):
                        continue
                    try:
                        target = (
                            plan.target
                            if isinstance(plan.target, OfficialSourceTargetV1)
                            else _general_source_target(
                                candidate_url, source_types=plan.target.source_types
                            )
                        )
                    except ValueError:
                        continue
                    candidates.append(
                        _CandidatePlan(
                            ordinal=0,
                            dimension_id=plan.dimension_id,
                            candidate_id=candidate.stable_id,
                            requested_url=candidate_url,
                            title=candidate.title,
                            target=target,
                            discovery_years=plan.discovery_years,
                        )
                    )
                return tuple(candidates)
            return ()

        active_plans = query_plans if use_search_fallback else ()
        if fanout and active_plans and parent_lease is not None:
            remaining_s = remaining_timeout_seconds(
                parent_lease, now_monotonic_ns=self.monotonic_ns()
            )
            if remaining_s > 0:
                query_semaphore = asyncio.Semaphore(lane_limit)

                async def _bounded_search(plan: _QueryPlan) -> tuple[_CandidatePlan, ...]:
                    async with query_semaphore:
                        return await _search_plan(plan, query_timeout_s=remaining_s)

                tasks = [
                    asyncio.create_task(_bounded_search(plan))
                    for plan in active_plans
                ]
                search_count = len(tasks)
                try:
                    candidate_buckets.extend(await asyncio.gather(*tasks))
                except asyncio.CancelledError:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
                if self.durable_reads is not None:
                    parent, parent_lease = await self.durable_reads.deadline_port.checkpoint(
                        identity=identity, state=parent, lease=parent_lease
                    )
                else:
                    transition = checkpoint_deadline(
                        parent,
                        parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    parent, parent_lease = transition.state, transition.lease
        else:
            for index, plan in enumerate(active_plans):
                if parent_lease is None:
                    break
                remaining_s = remaining_timeout_seconds(
                    parent_lease, now_monotonic_ns=self.monotonic_ns()
                )
                if remaining_s <= 0:
                    break
                query_timeout_s = remaining_s / max(1, len(active_plans) - index)
                candidate_buckets.append(
                    await _search_plan(plan, query_timeout_s=query_timeout_s)
                )
                search_count += 1
                if self.durable_reads is not None:
                    parent, parent_lease = await self.durable_reads.deadline_port.checkpoint(
                        identity=identity, state=parent, lease=parent_lease
                    )
                else:
                    transition = checkpoint_deadline(
                        parent,
                        parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    parent, parent_lease = transition.state, transition.lease

        if await self._control_fenced(run_id):
            return DeepResearchV6RetrievalResult(
                (), parent, search_count, discovery_fetch_count,
                sum(item.status == "timeout" for item in discovery_outcomes),
                sum(item.status == "rejected" for item in discovery_outcomes),
                sum(item.status == "failed" for item in discovery_outcomes),
                frozen_route,
            )
        remaining_fetches = max(0, fetch_limit - discovery_fetch_count)
        selected = self._select_candidates(candidate_buckets, limit=remaining_fetches)
        # Official search can return an archive/index locator rather than the
        # annual publication itself.  Resolve such locators through the same
        # frozen-year, same-authority rule used by registry seeds.  The index
        # consumes one existing fetch lease; discovered targets consume only
        # the remaining frozen fetch budget.
        searched_archives = tuple(
            candidate for candidate in selected if _looks_like_archive_locator(candidate)
        )
        if searched_archives and parent_lease is not None:
            archive_semaphore = asyncio.Semaphore(
                min(self.max_fetch_concurrency, lane_limit, len(searched_archives))
            )
            archive_tasks = [
                asyncio.create_task(
                    self._discover_seed(
                        candidate,
                        parent=parent,
                        run_id=run_id,
                        identity=identity,
                        semaphore=archive_semaphore,
                    )
                )
                for candidate in searched_archives
            ]
            try:
                searched_outcomes = tuple(await asyncio.gather(*archive_tasks))
            except asyncio.CancelledError:
                for task in archive_tasks:
                    task.cancel()
                await asyncio.gather(*archive_tasks, return_exceptions=True)
                raise
            discovery_outcomes = (*discovery_outcomes, *searched_outcomes)
            discovery_fetch_count = sum(
                item.upstream_called for item in discovery_outcomes
            )
            if self.durable_reads is not None:
                parent, parent_lease = await self.durable_reads.deadline_port.checkpoint(
                    identity=identity, state=parent, lease=parent_lease
                )
            else:
                transition = checkpoint_deadline(
                    parent,
                    parent_lease,
                    now_wall=self.wall_clock(),
                    now_monotonic_ns=self.monotonic_ns(),
                )
                parent, parent_lease = transition.state, transition.lease
            direct = tuple(
                candidate
                for candidate in selected
                if not _looks_like_archive_locator(candidate)
            )
            discovered = tuple(
                outcome.candidates
                for outcome in searched_outcomes
                if outcome.candidates
            )
            selected = self._select_candidates(
                (*discovered, direct),
                limit=max(0, fetch_limit - discovery_fetch_count),
            )
        if not selected or parent_lease is None:
            return DeepResearchV6RetrievalResult(
                (),
                parent,
                search_count,
                discovery_fetch_count,
                sum(item.status == "timeout" for item in discovery_outcomes),
                sum(item.status == "rejected" for item in discovery_outcomes),
                sum(item.status == "failed" for item in discovery_outcomes),
                frozen_route,
                tuple(
                    sorted(
                        {
                            item.page_result_ref
                            for item in discovery_outcomes
                            if item.page_result_ref is not None
                            and item.status in {"timeout", "rejected", "failed"}
                        }
                    )
                ),
            )

        async def _fetch_candidates(
            candidates: Sequence[_CandidatePlan],
        ) -> tuple[_FetchOutcome, ...]:
            concurrency = min(
                self.max_fetch_concurrency, lane_limit, len(candidates)
            )
            semaphore = asyncio.Semaphore(concurrency)
            cursor = 0
            cursor_lock = asyncio.Lock()
            completed: list[_FetchOutcome] = []

            async def _fetch_worker() -> None:
                nonlocal cursor
                while True:
                    # Pull at most one new page after each durable signal
                    # check. In-flight pages still settle canonical outcomes.
                    if await self._control_fenced(run_id):
                        return
                    async with cursor_lock:
                        if cursor >= len(candidates):
                            return
                        candidate = candidates[cursor]
                        cursor += 1
                    completed.append(
                        await self._fetch_one(
                            candidate,
                            parent=parent,
                            run_id=run_id,
                            identity=identity,
                            semaphore=semaphore,
                        )
                    )

            tasks = [
                asyncio.create_task(_fetch_worker()) for _ in range(concurrency)
            ]
            try:
                await asyncio.gather(*tasks)
                return tuple(completed)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        outcomes = await _fetch_candidates(selected)

        if self.durable_reads is not None:
            parent, parent_lease = await self.durable_reads.deadline_port.checkpoint(
                identity=identity, state=parent, lease=parent_lease
            )
        else:
            transition = checkpoint_deadline(
                parent,
                parent_lease,
                now_wall=self.wall_clock(),
                now_monotonic_ns=self.monotonic_ns(),
            )
            parent, parent_lease = transition.state, transition.lease

        # Archive/direct discovery is an optimization inside the frozen
        # official-source route.  If every discovered target settles to an
        # honest negative page result, consume the route's remaining query and
        # fetch budgets through official search.  Replays reuse the same
        # durable search/page effect identities, so this escalation neither
        # mutates the ResearchSpec nor double-consumes journaled resources.
        if (
            research_spec.intent_type == "official_exact_fact"
            and not use_search_fallback
            and not self._canonical_pages(outcomes)
            and query_plans
            and parent_lease is not None
            and (
                discovery_fetch_count
                + sum(item.upstream_called for item in outcomes)
                < fetch_limit
            )
            and not await self._control_fenced(run_id)
        ):
            escalation_buckets: list[tuple[_CandidatePlan, ...]] = []
            for index, plan in enumerate(query_plans):
                if parent_lease is None or await self._control_fenced(run_id):
                    break
                remaining_s = remaining_timeout_seconds(
                    parent_lease, now_monotonic_ns=self.monotonic_ns()
                )
                if remaining_s <= 0:
                    break
                escalation_buckets.append(
                    await _search_plan(
                        plan,
                        query_timeout_s=(
                            remaining_s / max(1, len(query_plans) - index)
                        ),
                    )
                )
                search_count += 1
                if self.durable_reads is not None:
                    parent, parent_lease = (
                        await self.durable_reads.deadline_port.checkpoint(
                            identity=identity, state=parent, lease=parent_lease
                        )
                    )
                else:
                    transition = checkpoint_deadline(
                        parent,
                        parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    parent, parent_lease = transition.state, transition.lease

            attempted_urls = {candidate.requested_url for candidate in selected}
            escalation_buckets = [
                tuple(
                    candidate
                    for candidate in bucket
                    if candidate.requested_url not in attempted_urls
                )
                for bucket in escalation_buckets
            ]
            remaining_fetches = max(
                0,
                fetch_limit
                - discovery_fetch_count
                - sum(item.upstream_called for item in outcomes),
            )
            escalated = self._select_candidates(
                escalation_buckets, limit=remaining_fetches
            )
            escalation_archives = tuple(
                candidate
                for candidate in escalated
                if _looks_like_archive_locator(candidate)
            )
            if escalation_archives and parent_lease is not None:
                archive_semaphore = asyncio.Semaphore(
                    min(
                        self.max_fetch_concurrency,
                        lane_limit,
                        len(escalation_archives),
                    )
                )
                archive_tasks = [
                    asyncio.create_task(
                        self._discover_seed(
                            candidate,
                            parent=parent,
                            run_id=run_id,
                            identity=identity,
                            semaphore=archive_semaphore,
                        )
                    )
                    for candidate in escalation_archives
                ]
                try:
                    escalation_discovery = tuple(
                        await asyncio.gather(*archive_tasks)
                    )
                except asyncio.CancelledError:
                    for task in archive_tasks:
                        task.cancel()
                    await asyncio.gather(*archive_tasks, return_exceptions=True)
                    raise
                discovery_outcomes = (
                    *discovery_outcomes,
                    *escalation_discovery,
                )
                discovery_fetch_count = sum(
                    item.upstream_called for item in discovery_outcomes
                )
                if self.durable_reads is not None:
                    parent, parent_lease = (
                        await self.durable_reads.deadline_port.checkpoint(
                            identity=identity,
                            state=parent,
                            lease=parent_lease,
                        )
                    )
                else:
                    transition = checkpoint_deadline(
                        parent,
                        parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    parent, parent_lease = transition.state, transition.lease
                direct_escalation = tuple(
                    candidate
                    for candidate in escalated
                    if not _looks_like_archive_locator(candidate)
                )
                discovered_escalation = tuple(
                    outcome.candidates
                    for outcome in escalation_discovery
                    if outcome.candidates
                )
                already_fetched = sum(item.upstream_called for item in outcomes)
                escalated = self._select_candidates(
                    (*discovered_escalation, direct_escalation),
                    limit=max(
                        0,
                        fetch_limit
                        - discovery_fetch_count
                        - already_fetched,
                    ),
                )
            if escalated and parent_lease is not None:
                outcomes = (*outcomes, *(await _fetch_candidates(escalated)))
                if self.durable_reads is not None:
                    parent, parent_lease = (
                        await self.durable_reads.deadline_port.checkpoint(
                            identity=identity, state=parent, lease=parent_lease
                        )
                    )
                else:
                    transition = checkpoint_deadline(
                        parent,
                        parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    parent, parent_lease = transition.state, transition.lease
        pages = self._canonical_pages(outcomes)
        page_result_refs = tuple(
            sorted(
                {
                    *self._canonical_page_result_refs(outcomes),
                    *(
                        item.page_result_ref
                        for item in discovery_outcomes
                        if item.page_result_ref is not None
                        and item.status in {"timeout", "rejected", "failed"}
                    ),
                }
            )
        )
        if self.durable_reads is not None:
            # Every dispatched durable page attempt commits a typed result,
            # including honest negative outcomes.  Therefore result refs may
            # outnumber admitted pages.  The only required invariant is that
            # each canonical verified page still has its own canonical result
            # ref; failed/timeout/empty refs remain in the frontier so the
            # semantic and terminal stages can report an evidence gap.
            page_to_ref: dict[tuple[str, str], str] = {}
            ref_to_page: dict[str, tuple[str, str]] = {}
            for outcome in sorted(outcomes, key=lambda item: item.ordinal):
                if outcome.status != "verified" or outcome.page is None:
                    continue
                page_key = (outcome.page.final_url_hash, outcome.page.body_hash)
                if page_key in page_to_ref:
                    continue
                result_ref = outcome.page_result_ref
                if result_ref is None:
                    raise ValueError("canonical page is missing canonical result ref")
                prior_page = ref_to_page.get(result_ref)
                if prior_page is not None and prior_page != page_key:
                    raise ValueError("canonical page result ref aliases distinct pages")
                page_to_ref[page_key] = result_ref
                ref_to_page[result_ref] = page_key
            canonical_page_keys = {
                (page.final_url_hash, page.body_hash) for page in pages
            }
            if canonical_page_keys != set(page_to_ref):
                raise ValueError("canonical page is missing canonical result ref")
        return DeepResearchV6RetrievalResult(
            pages=pages,
            parent_deadline=parent,
            search_count=search_count,
            fetch_count=discovery_fetch_count + sum(item.upstream_called for item in outcomes),
            timeout_count=(
                sum(item.status == "timeout" for item in discovery_outcomes)
                + sum(item.status == "timeout" for item in outcomes)
            ),
            rejected_count=(
                sum(item.status == "rejected" for item in discovery_outcomes)
                + sum(item.status == "rejected" for item in outcomes)
            ),
            failed_count=(
                sum(item.status == "failed" for item in discovery_outcomes)
                + sum(item.status == "failed" for item in outcomes)
            ),
            route_decision=frozen_route,
            page_result_refs=page_result_refs,
        )

    def _build_query_plans(
        self,
        spec: ResearchSpecV1,
    ) -> tuple[tuple[_QueryPlan, ...], tuple[tuple[_CandidatePlan, ...], ...]]:
        raw = spec.to_json()
        requirements = {
            str(item["requirement_id"]): item for item in raw["requirements"]
        }
        user_constraints = raw["user_constraints"]
        user_jurisdictions = tuple(user_constraints["jurisdictions"])
        user_preferred = tuple(user_constraints["preferred_authority_ids"])
        query_plans: list[_QueryPlan] = []
        seeds: list[tuple[_CandidatePlan, ...]] = []

        if spec.intent_type != "official_exact_fact":
            for dimension in sorted(
                raw["work_dimensions"], key=lambda item: item["ordinal"]
            ):
                if len(query_plans) >= self.max_queries:
                    break
                requirement_ids = tuple(sorted(dimension["requirement_ids"]))
                concepts = tuple(
                    part
                    for part in (
                        _clean_part(value, limit=80)
                        for value in dimension["search_concepts"]
                    )
                    if part
                )
                time_label = dimension["time_scope"].get("label")
                query, query_terms = _bounded_general_query(
                    concepts,
                    time_label if isinstance(time_label, str) else None,
                    max_chars=self.max_query_chars,
                )
                source_types = tuple(
                    sorted(dimension["source_constraint"]["eligible_source_types"])
                )
                target_identity: dict[str, JsonValue] = {
                    "spec_hash": spec.spec_hash,
                    "dimension_id": str(dimension["dimension_id"]),
                    "source_types": list(source_types),
                }
                target = _GeneralSourceTarget(
                    target_id=_stable_id("general_query_", target_identity),
                    authority_id="general_search_pending",
                    jurisdiction="wt-wt",
                    organization_name="General web source",
                    source_types=source_types,
                    verification_policy_hash=_GENERAL_VERIFICATION_POLICY_HASH,
                    requested_host="",
                )
                query_identity: dict[str, JsonValue] = {
                    **target_identity,
                    "query": query,
                }
                query_plans.append(
                    _QueryPlan(
                        ordinal=len(query_plans),
                        dimension_id=str(dimension["dimension_id"]),
                        requirement_ids=requirement_ids,
                        query_terms=query_terms,
                        query=query,
                        request_id=_stable_id("gsq_", query_identity),
                        target=target,
                    )
                )
            return tuple(query_plans), ()

        for dimension in sorted(raw["work_dimensions"], key=lambda item: item["ordinal"]):
            requirement_ids = tuple(sorted(dimension["requirement_ids"]))
            referenced = [requirements[item] for item in requirement_ids]
            jurisdictions = sorted(
                {
                    value
                    for value in (
                        requirement.get("scope", {}).get("jurisdiction")
                        for requirement in referenced
                    )
                    if isinstance(value, str) and value.strip()
                }
                or {
                    value
                    for value in user_jurisdictions
                    if isinstance(value, str) and value.strip()
                }
            )
            source = dimension["source_constraint"]
            roles = tuple(source["authority_roles"])
            if not jurisdictions or not roles:
                continue
            source_types = tuple(source["eligible_source_types"])
            preferred = tuple(
                sorted(
                    set(source["preferred_authority_ids"]) | set(user_preferred)
                )
            )
            concepts = tuple(
                part
                for part in (
                    _clean_part(value, limit=80)
                    for value in dimension["search_concepts"]
                )
                if part
            )
            time_label = dimension["time_scope"].get("label")
            discovery_years = _time_scope_years(dimension["time_scope"])
            for jurisdiction in jurisdictions:
                request_identity = {
                    "spec_hash": spec.spec_hash,
                    "dimension_id": dimension["dimension_id"],
                    "requirement_ids": list(requirement_ids),
                    "jurisdiction": jurisdiction,
                    "authority_roles": list(roles),
                    "source_types": list(source_types),
                    "preferred_authority_ids": list(preferred),
                }
                targets = self.source_resolver.resolve(
                    OfficialSourceRequestV1(
                        request_id=_stable_id("osr_", request_identity),
                        requirement_ids=requirement_ids,
                        jurisdiction=jurisdiction,
                        authority_roles=roles,
                        source_types=source_types,
                        preferred_authority_ids=preferred,
                        locale=spec.answer_locale,
                    )
                )
                for target in targets:
                    if target.seed_urls:
                        seeds.append(
                            tuple(
                                _CandidatePlan(
                                    ordinal=0,
                                    dimension_id=str(dimension["dimension_id"]),
                                    candidate_id=_stable_id(
                                        "seed_",
                                        {
                                            "target_id": target.target_id,
                                            "url": url,
                                        },
                                    ),
                                    requested_url=url,
                                    title=target.organization_name,
                                    target=target,
                                    discovery_years=discovery_years,
                                )
                                for url in target.seed_urls
                            )
                        )
                    for directive in target.search_directives:
                        compact_query, compact_terms = _compact_official_query(
                            concepts,
                            dimension["time_scope"],
                            directive,
                            locale=spec.answer_locale,
                            max_chars=self.max_query_chars,
                        )
                        variants = (
                            (
                                _bounded_query(
                                    concepts,
                                    time_label if isinstance(time_label, str) else None,
                                    directive,
                                    max_chars=self.max_query_chars,
                                ),
                                concepts,
                            ),
                            (compact_query, compact_terms),
                        )
                        for query, query_terms in variants:
                            if len(query_plans) >= self.max_queries:
                                break
                            if any(
                                existing.dimension_id == str(dimension["dimension_id"])
                                and existing.target.target_id == target.target_id
                                and existing.query == query
                                for existing in query_plans
                            ):
                                continue
                            query_identity = {
                                "spec_hash": spec.spec_hash,
                                "dimension_id": dimension["dimension_id"],
                                "target_id": target.target_id,
                                "query": query,
                            }
                            query_plans.append(
                                _QueryPlan(
                                    ordinal=len(query_plans),
                                    dimension_id=str(dimension["dimension_id"]),
                                    requirement_ids=requirement_ids,
                                    query_terms=query_terms,
                                    query=query,
                                    request_id=_stable_id("osq_", query_identity),
                                    target=target,
                                    discovery_years=discovery_years,
                                )
                            )
        return tuple(query_plans), tuple(seeds)

    def _select_candidates(
        self,
        buckets: Sequence[Sequence[_CandidatePlan]],
        *,
        limit: int | None = None,
    ) -> tuple[_CandidatePlan, ...]:
        """Round-robin query buckets so one dimension cannot consume all fetches."""

        effective_limit = self.max_fetches if limit is None else max(0, min(limit, self.max_fetches))
        ordered_buckets = tuple(
            (
                tuple(bucket)
                if all(
                    isinstance(candidate.target, OfficialSourceTargetV1)
                    for candidate in bucket
                )
                else tuple(
                    sorted(
                        bucket,
                        key=lambda candidate: (
                            candidate.dimension_id,
                            hashlib.sha256(
                                candidate.requested_url.encode("utf-8")
                            ).hexdigest(),
                            candidate.candidate_id,
                        ),
                    )
                )
            )
            for bucket in buckets
        )
        selected: list[_CandidatePlan] = []
        seen_urls: set[str] = set()
        offset = 0
        while len(selected) < effective_limit:
            advanced = False
            for bucket in ordered_buckets:
                if offset >= len(bucket):
                    continue
                advanced = True
                candidate = bucket[offset]
                if candidate.requested_url in seen_urls:
                    continue
                seen_urls.add(candidate.requested_url)
                selected.append(
                    _CandidatePlan(
                        ordinal=len(selected),
                        dimension_id=candidate.dimension_id,
                        candidate_id=candidate.candidate_id,
                        requested_url=candidate.requested_url,
                        title=candidate.title,
                        target=candidate.target,
                        discovery_years=candidate.discovery_years,
                    )
                )
                if len(selected) >= effective_limit:
                    break
            if not advanced:
                break
            offset += 1
        return tuple(selected)

    async def _discover_seed(
        self,
        candidate: _CandidatePlan,
        *,
        parent: DurableDeadlineV1,
        run_id: str,
        identity: NodeExecutionIdentity,
        semaphore: asyncio.Semaphore,
    ) -> _SeedDiscoveryOutcome:
        """Fetch an authority-owned index and follow year-labelled links only."""

        async with semaphore:
            span = await self._start_child_span(
                identity=identity,
                request_id=candidate.candidate_id,
                stage="official_archive_discovery",
                ordinal=candidate.ordinal,
            )

            async def _done(outcome: _SeedDiscoveryOutcome) -> _SeedDiscoveryOutcome:
                status = (
                    SpanStatus.OK
                    if outcome.status in {"discovered", "empty"}
                    else SpanStatus.ERROR
                )
                await self._finish_child_span(
                    span,
                    status,
                    outcome=outcome.status,
                    count=len(outcome.candidates),
                )
                return outcome

            try:
                logical_key = f"archive:{candidate.candidate_id}:{candidate.ordinal}"
                if self.durable_reads is not None:
                    child, lease = await self.durable_reads.deadline_port.resume_child(
                        identity=identity,
                        parent=parent,
                        logical_key=logical_key,
                        logical_scope="deep_research_v6.official_archive_discovery",
                        budget_ms=self.page_deadline_ms,
                        policy_hash=self._policy_hash,
                    )
                    transition = None
                else:
                    child = create_child_deadline(
                        parent,
                        logical_key=logical_key,
                        logical_scope="deep_research_v6.official_archive_discovery",
                        budget_ms=self.page_deadline_ms,
                        now_wall=self.wall_clock(),
                        policy_hash=self._policy_hash,
                    )
                    transition = resume_deadline(
                        child,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    lease = transition.lease
            except DeadlineValidationError:
                return await _done(_SeedDiscoveryOutcome("timeout", False))
            if lease is None:
                return await _done(_SeedDiscoveryOutcome("timeout", False))
            timeout_s = remaining_timeout_seconds(
                lease,
                now_monotonic_ns=self.monotonic_ns(),
                stage_cap_ms=self.page_deadline_ms,
            )
            if timeout_s <= 0:
                return await _done(_SeedDiscoveryOutcome("timeout", False))
            request = FetchRequest(
                url=candidate.requested_url,
                timeout=timeout_s,
                extract=True,
                render_policy="auto",
                include_html=True,
                max_chars=100_000,
                allow_jina=False,
                run_id=run_id,
                deadline_monotonic=lease.deadline_monotonic_ns / 1_000_000_000,
            )
            try:
                if self.durable_reads is None:
                    document = await asyncio.wait_for(
                        self.fetch_service.fetch(request), timeout=timeout_s
                    )
                else:
                    typed = await self._durable_page_result(
                        candidate=candidate, request=request, timeout_s=timeout_s,
                        deadline=child, identity=identity, prefer_html=True,
                    )
                    typed_ref = format_blob_ref(
                        hashlib.sha256(
                            canonical_json(typed.to_json()).encode("utf-8")
                        ).hexdigest()
                    )
                    child, _ = await self.durable_reads.deadline_port.checkpoint(
                        identity=identity, state=child, lease=lease
                    )
                    if typed.outcome != "succeeded" or typed.page_record is None:
                        status = (
                            "timeout"
                            if typed.outcome == "timeout"
                            else "rejected"
                            if typed.outcome in {"blocked", "empty"}
                            else "failed"
                        )
                        return await _done(
                            _SeedDiscoveryOutcome(
                                status, True, page_result_ref=typed_ref
                            )
                        )
                    page = typed.page_record
                    raw_body = await self.blobs.get(str(page["body_ref"]).removeprefix("sha256:"))
                    raw_locator = await self.blobs.get(typed.source_locator_ref.removeprefix("sha256:"))
                    locator_value = json.loads(raw_locator.decode("utf-8"))
                    if not isinstance(locator_value, Mapping):
                        raise ValueError("source locator blob must be an object")
                    locator = SourceLocatorV1.from_json(locator_value)
                    body = raw_body.decode("utf-8")
                    document = EvidenceDocument(
                        stable_id=str(page["page_id"]), url=locator.final_url,
                        canonical_url=locator.final_url, title="", text=body,
                        published_at=None, provider="durable_v6", providers=("durable_v6",),
                        provider_rank=0, score=0.0, searched_at="", source_kind="official",
                        content_hash=str(page["body_hash"]), fetcher="durable_v6",
                        extractor="durable_v6", fetched_at=str(page["fetched_at"]),
                        html=body,
                    )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(span, SpanStatus.CANCELLED, outcome="cancelled")
                )
                raise
            except TimeoutError:
                return await _done(_SeedDiscoveryOutcome("timeout", True))
            except Exception:
                return await _done(_SeedDiscoveryOutcome("failed", True))
            if not isinstance(document, EvidenceDocument):
                return await _done(_SeedDiscoveryOutcome("failed", True))
            final_url = document.canonical_url.strip() or document.url.strip()
            if not self.source_resolver.verify(candidate.target, final_url).matched:
                return await _done(_SeedDiscoveryOutcome("rejected", True))
            links = _archive_links(
                document.html or "",
                # A redirect alias may be authority-valid but unusable for a
                # subsequent TLS connection.  Relative links keep the
                # registry canonical origin (or the original requested
                # origin when no seed exists); the final alias is a fallback.
                base_urls=_archive_resolution_bases(
                    candidate,
                    final_url=final_url,
                ),
                years=candidate.discovery_years,
                target=candidate.target,
                resolver=self.source_resolver,
                limit=self.max_fetches - 1,
            )
            discovered = tuple(
                _CandidatePlan(
                    ordinal=index,
                    dimension_id=candidate.dimension_id,
                    candidate_id=_stable_id(
                        "archive_",
                        {
                            "target_id": candidate.target.target_id,
                            "url": url,
                            "years": list(candidate.discovery_years),
                        },
                    ),
                    requested_url=url,
                    title=document.title or candidate.title,
                    target=candidate.target,
                )
                for index, url in enumerate(links)
            )
            return await _done(
                _SeedDiscoveryOutcome(
                    "discovered" if discovered else "empty",
                    True,
                    discovered,
                    typed_ref if self.durable_reads is not None else None,
                )
            )

    async def _fetch_one(
        self,
        candidate: _CandidatePlan,
        *,
        parent: DurableDeadlineV1,
        run_id: str,
        identity: NodeExecutionIdentity,
        semaphore: asyncio.Semaphore,
    ) -> _FetchOutcome:
        async with semaphore:
            fetch_span = await self._start_child_span(
                identity=identity,
                request_id=candidate.candidate_id,
                stage="page_fetch",
                ordinal=candidate.ordinal,
            )

            async def _done(outcome: _FetchOutcome) -> _FetchOutcome:
                status = (
                    SpanStatus.OK
                    if outcome.status == "verified"
                    else SpanStatus.ERROR
                )
                await self._finish_child_span(
                    fetch_span,
                    status,
                    outcome=outcome.status,
                    count=1 if outcome.status == "verified" else 0,
                )
                return outcome

            try:
                await self._persist_locator(
                    candidate=candidate,
                    final_url=candidate.requested_url,
                    verification_status="unverified",
                    identity=identity,
                )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(
                        fetch_span, SpanStatus.CANCELLED, outcome="cancelled"
                    )
                )
                raise
            except Exception:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", False))
            try:
                logical_page_key = (
                    f"{candidate.dimension_id}:{candidate.candidate_id}:{candidate.ordinal}"
                )
                if self.durable_reads is not None:
                    child, lease = await self.durable_reads.deadline_port.resume_child(
                        identity=identity,
                        parent=parent,
                        logical_key=logical_page_key,
                        logical_scope="deep_research_v6.page_fetch",
                        budget_ms=self.page_deadline_ms,
                        policy_hash=self._policy_hash,
                    )
                    transition = None
                else:
                    child = create_child_deadline(
                        parent,
                        logical_key=logical_page_key,
                        logical_scope="deep_research_v6.page_fetch",
                        budget_ms=self.page_deadline_ms,
                        now_wall=self.wall_clock(),
                        policy_hash=self._policy_hash,
                    )
                    transition = resume_deadline(
                        child,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    lease = transition.lease
            except DeadlineValidationError:
                return await _done(_FetchOutcome(candidate.ordinal, "timeout", False))
            if lease is None:
                return await _done(_FetchOutcome(candidate.ordinal, "timeout", False))
            timeout_s = remaining_timeout_seconds(
                lease,
                now_monotonic_ns=self.monotonic_ns(),
                stage_cap_ms=self.page_deadline_ms,
            )
            if timeout_s <= 0:
                return await _done(_FetchOutcome(candidate.ordinal, "timeout", False))
            request = FetchRequest(
                url=candidate.requested_url,
                timeout=timeout_s,
                extract=True,
                render_policy="auto",
                include_html=False,
                max_chars=50_000,
                allow_jina=False,
                run_id=run_id,
                deadline_monotonic=lease.deadline_monotonic_ns / 1_000_000_000,
                prefer_httpx=True,
            )
            try:
                if self.durable_reads is None:
                    document = await asyncio.wait_for(
                        self.fetch_service.fetch(request), timeout=timeout_s
                    )
                else:
                    typed = await self._durable_page_result(
                        candidate=candidate, request=request, timeout_s=timeout_s,
                        deadline=child, identity=identity, prefer_html=False,
                    )
                    typed_ref = format_blob_ref(hashlib.sha256(
                        canonical_json(typed.to_json()).encode("utf-8")
                    ).hexdigest())
                    if typed.outcome == "timeout":
                        return await _done(_FetchOutcome(
                            candidate.ordinal, "timeout", True,
                            page_result_ref=typed_ref,
                        ))
                    if typed.outcome != "succeeded" or typed.page_record is None:
                        return await _done(_FetchOutcome(
                            candidate.ordinal,
                            "rejected" if typed.outcome in {"blocked", "empty"} else "failed",
                            True,
                            page_result_ref=typed_ref,
                        ))
                    page = typed.page_record
                    return await _done(_FetchOutcome(
                        ordinal=candidate.ordinal, status="verified", upstream_called=True,
                        page=_VerifiedPage(
                            source_locator_ref=typed.source_locator_ref,
                            canonical_url_hash=str(page["canonical_url_hash"]),
                            final_url_hash=str(page["final_url_hash"]),
                            body_ref=str(page["body_ref"]), body_hash=str(page["body_hash"]),
                            authority_id=str(page["authority_id"]),
                            title_hash=hashlib.sha256((candidate.title or candidate.target.organization_name).encode("utf-8")).hexdigest(),
                            admission_reason=str(page["reason_codes"][0]),
                        ),
                        page_result_ref=typed_ref,
                    ))
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(
                        fetch_span, SpanStatus.CANCELLED, outcome="cancelled"
                    )
                )
                raise
            except TimeoutError:
                return await _done(_FetchOutcome(candidate.ordinal, "timeout", True))
            except Exception:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            if not isinstance(document, EvidenceDocument):
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            final_url = document.canonical_url.strip() or document.url.strip()
            if isinstance(candidate.target, OfficialSourceTargetV1):
                authority = self.source_resolver.verify(candidate.target, final_url)
                matched = authority.matched
                authority_id = authority.authority_id
                admission_reason = "official_domain_match"
            else:
                final_host = (urlsplit(final_url).hostname or "").casefold().rstrip(".")
                matched = bool(final_host) and final_host == candidate.target.requested_host
                authority_id = candidate.target.authority_id if matched else None
                admission_reason = "general_same_host_verified"
            verification_status = "verified" if matched else "rejected"
            try:
                locator = await self._persist_locator(
                    candidate=candidate,
                    final_url=final_url,
                    verification_status=verification_status,
                    identity=identity,
                )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(
                        fetch_span, SpanStatus.CANCELLED, outcome="cancelled"
                    )
                )
                raise
            except Exception:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            if not matched or authority_id is None:
                return await _done(_FetchOutcome(candidate.ordinal, "rejected", True))
            body = document.text
            if not body.strip():
                return await _done(_FetchOutcome(candidate.ordinal, "rejected", True))
            body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            title = (
                document.title.strip()
                or candidate.title.strip()
                or candidate.target.organization_name
            )
            try:
                body_blob = await self._put_blob(
                    body.encode("utf-8"),
                    identity,
                    media_type="text/plain; charset=utf-8",
                )
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._finish_child_span(
                        fetch_span, SpanStatus.CANCELLED, outcome="cancelled"
                    )
                )
                raise
            except Exception:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            if body_blob.sha256 != body_hash:
                return await _done(_FetchOutcome(candidate.ordinal, "failed", True))
            return await _done(_FetchOutcome(
                ordinal=candidate.ordinal,
                status="verified",
                upstream_called=True,
                page=_VerifiedPage(
                    source_locator_ref=locator["source_locator_ref"],
                    canonical_url_hash=locator["canonical_url_hash"],
                    final_url_hash=locator["final_url_hash"],
                    body_ref=format_blob_ref(body_blob.sha256),
                    body_hash=body_hash,
                    authority_id=authority_id,
                    title_hash=hashlib.sha256(title.encode("utf-8")).hexdigest(),
                    admission_reason=admission_reason,
                ),
            ))

    async def _durable_page_result(
        self,
        *,
        candidate: _CandidatePlan,
        request: FetchRequest,
        timeout_s: float,
        deadline: DurableDeadlineV1,
        identity: NodeExecutionIdentity,
        prefer_html: bool,
    ) -> PageExtractionResultV1:
        """Own fetch, redirect validation and body persistence in one attempt."""

        if self.durable_reads is None:
            raise RuntimeError("durable page result requires the read-effect adapter")
        input_locator = await self._persist_locator(
            candidate=candidate,
            final_url=candidate.requested_url,
            verification_status="unverified",
            identity=identity,
        )

        async def _transport(attempt) -> PageExtractionResultV1:
            common = {
                "logical_page_id": candidate.candidate_id,
                "logical_effect_id": attempt.logical_effect_id,
                "attempt_no": attempt.attempt_no,
                "deadline_id": attempt.deadline_id,
                "control_command_id": None,
            }
            try:
                attempt_timeout_s = timeout_s
                if attempt.deadline_monotonic_ns is not None:
                    attempt_timeout_s = min(
                        attempt_timeout_s,
                        max(0.0, (attempt.deadline_monotonic_ns - self.monotonic_ns()) / 1_000_000_000),
                    )
                document = await asyncio.wait_for(
                    self.fetch_service.fetch(request), timeout=attempt_timeout_s
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                return PageExtractionResultV1.create(
                    **common, source_locator_ref=input_locator["source_locator_ref"],
                    page_record=None, spans=(), bindings=(), outcome="timeout",
                    error_code="deadline_expired",
                )
            except Exception:
                return PageExtractionResultV1.create(
                    **common, source_locator_ref=input_locator["source_locator_ref"],
                    page_record=None, spans=(), bindings=(), outcome="failed",
                    error_code="fetch_failed",
                )
            if not isinstance(document, EvidenceDocument):
                return PageExtractionResultV1.create(
                    **common, source_locator_ref=input_locator["source_locator_ref"],
                    page_record=None, spans=(), bindings=(), outcome="failed",
                    error_code="invalid_fetch_result",
                )
            final_url = document.canonical_url.strip() or document.url.strip()
            if isinstance(candidate.target, OfficialSourceTargetV1):
                authority = self.source_resolver.verify(candidate.target, final_url)
                matched = authority.matched
                authority_id = authority.authority_id or candidate.target.authority_id
                admission_reason = "official_domain_match"
                source_tier = "first_party"
            else:
                final_host = (urlsplit(final_url).hostname or "").casefold().rstrip(".")
                matched = bool(final_host) and final_host == candidate.target.requested_host
                authority_id = candidate.target.authority_id
                admission_reason = "general_same_host_verified"
                source_tier = "general"
            locator = await self._persist_locator(
                candidate=candidate, final_url=final_url,
                verification_status="verified" if matched else "rejected",
                identity=identity,
            )
            if not matched:
                return PageExtractionResultV1.create(
                    **common, source_locator_ref=locator["source_locator_ref"],
                    page_record=None, spans=(), bindings=(), outcome="blocked",
                    error_code="redirect_authority_rejected",
                )
            body = (document.html if prefer_html and document.html else document.text).strip()
            if not body:
                return PageExtractionResultV1.create(
                    **common, source_locator_ref=locator["source_locator_ref"],
                    page_record=None, spans=(), bindings=(), outcome="empty",
                    error_code=None,
                )
            body_bytes = body.encode("utf-8")
            body_hash = hashlib.sha256(body_bytes).hexdigest()
            body_blob = await self._put_blob(
                body_bytes, identity,
                media_type="text/html; charset=utf-8" if prefer_html else "text/plain; charset=utf-8",
            )
            if body_blob.sha256 != body_hash:
                raise ValueError("registered page body digest mismatch")
            page_id = "page_" + hashlib.sha256(
                (locator["final_url_hash"] + body_hash).encode("ascii")
            ).hexdigest()[:24]
            page_record: dict[str, JsonValue] = {
                "schema_version": 1,
                "page_id": page_id,
                "source_locator_ref": locator["source_locator_ref"],
                "canonical_url_hash": locator["canonical_url_hash"],
                "final_url_hash": locator["final_url_hash"],
                "authority_id": authority_id,
                "source_family_id": authority_id,
                "source_tier": source_tier,
                "body_ref": format_blob_ref(body_blob.sha256),
                "body_hash": body_hash,
                "fetched_at": document.fetched_at or datetime.now(timezone.utc).isoformat(),
                "media_type": "text/html; charset=utf-8" if prefer_html else "text/plain; charset=utf-8",
                "admission_status": "admitted",
                "reason_codes": [admission_reason],
            }
            return PageExtractionResultV1.create(
                **common, source_locator_ref=locator["source_locator_ref"],
                page_record=page_record, spans=(), bindings=(), outcome="succeeded",
                error_code=None,
            )

        raw = await self.durable_reads.execute(
            operation_kind="page_fetch",
            route_id=str(self._active_route_id),
            target_or_page_id=candidate.candidate_id,
            ordinal=candidate.ordinal,
            resource_kind="fetch",
            resource_hard_limit=int(self._active_route_budget["fetch"]),
            resource_policy_hash=self._policy_hash,
            deadline=deadline,
            identity=identity,
            transport=_transport,
            input_source_locator_ref=input_locator["source_locator_ref"],
        )
        return PageExtractionResultV1.from_json(raw)

    async def _persist_locator(
        self,
        *,
        candidate: _CandidatePlan,
        final_url: str,
        verification_status: Literal["verified", "rejected", "unverified"],
        identity: NodeExecutionIdentity,
    ) -> dict[str, str]:
        canonical_url = candidate.requested_url
        if verification_status == "unverified":
            final_url = canonical_url
        canonical_url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        final_url_hash = hashlib.sha256(final_url.encode("utf-8")).hexdigest()
        locator = SourceLocatorV1.create(
            canonical_url=canonical_url,
            final_url=final_url,
            canonical_url_hash=canonical_url_hash,
            final_url_hash=final_url_hash,
            authority_id=candidate.target.authority_id,
            verification_status=verification_status,
            verification_policy_hash=(
                self.source_resolver.registry.policy_hash
                if isinstance(candidate.target, OfficialSourceTargetV1)
                else candidate.target.verification_policy_hash
            ),
            redirect_chain_hashes=(
                () if final_url_hash == canonical_url_hash else (final_url_hash,)
            ),
        )
        blob = await self._put_blob(
            canonical_json(locator.to_json()).encode("utf-8"),
            identity,
            media_type="application/vnd.deskpet.source-locator.v1+json",
        )
        return {
            "source_locator_ref": format_blob_ref(blob.sha256),
            "canonical_url_hash": canonical_url_hash,
            "final_url_hash": final_url_hash,
        }

    async def _put_blob(
        self,
        data: bytes,
        identity: NodeExecutionIdentity,
        *,
        media_type: str,
    ) -> BlobRef:
        async with self._blob_put_lock:
            return await self.blobs.put(
                data,
                identity,
                media_type=media_type,
            )

    @staticmethod
    def _canonical_page_result_refs(
        outcomes: Sequence[_FetchOutcome],
    ) -> tuple[str, ...]:
        refs: list[str] = []
        seen_final_urls: set[str] = set()
        seen_refs: set[str] = set()
        for outcome in sorted(outcomes, key=lambda item: item.ordinal):
            if outcome.page_result_ref is None or outcome.page_result_ref in seen_refs:
                continue
            if outcome.status == "verified" and outcome.page is not None:
                if outcome.page.final_url_hash in seen_final_urls:
                    continue
                seen_final_urls.add(outcome.page.final_url_hash)
            seen_refs.add(outcome.page_result_ref)
            refs.append(outcome.page_result_ref)
        return tuple(refs)

    @staticmethod
    def _canonical_pages(
        outcomes: Sequence[_FetchOutcome],
    ) -> tuple[V6FetchedPageRefPayloadV1, ...]:
        pages: list[V6FetchedPageRefPayloadV1] = []
        seen_final_urls: set[str] = set()
        for outcome in sorted(outcomes, key=lambda item: item.ordinal):
            page = outcome.page
            if outcome.status != "verified" or page is None:
                continue
            if page.final_url_hash in seen_final_urls:
                continue
            seen_final_urls.add(page.final_url_hash)
            page_id = "page_" + hashlib.sha256(
                (page.final_url_hash + page.body_hash).encode("ascii")
            ).hexdigest()[:24]
            pages.append(
                V6FetchedPageRefPayloadV1(
                    page_id=page_id,
                    ordinal=len(pages),
                    source_locator_ref=page.source_locator_ref,
                    body_ref=page.body_ref,
                    body_hash=page.body_hash,
                    canonical_url_hash=page.canonical_url_hash,
                    final_url_hash=page.final_url_hash,
                    authority_id=page.authority_id,
                    title_hash=page.title_hash,
                    media_type="text/plain; charset=utf-8",
                    admission_status="admitted",
                    reason_codes=(page.admission_reason,),
                )
            )
        return tuple(pages)


__all__ = [
    "DeepResearchV6EvidenceRuntime",
    "DeepResearchV6RetrievalResult",
    "V6FetchedPageRefPayloadV1",
    "MAX_FETCH_CONCURRENCY",
    "MAX_FETCH_COUNT",
    "MAX_QUERY_COUNT",
    "PAGE_DEADLINE_MS",
    "PARENT_DEADLINE_MS",
]
