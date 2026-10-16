"""Retrieval, query plans and candidate selection."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from providers.dispatch_handoff import dispatch_with_run_fence

from ....retrieval.contracts import RetrievalCandidate, SearchRequest, SearchResponse
from ....retrieval.official_sources import OfficialSourceRequestV1, OfficialSourceTargetV1
from ...contracts import JsonValue, NodeExecutionIdentity
from ...deadlines import (
    DurableDeadlineV1,
    checkpoint_deadline,
    remaining_timeout_seconds,
    resume_deadline,
)
from ...definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    RouteDecisionV1,
    conditional_fanout_enabled,
    sha256_json,
)
from ...definitions.deep_research_v6_retrieval_contracts import (
    OfficialSearchResultV1,
    SearchCandidateV1,
    SourceLocatorV1,
)
from ...trace.models import SpanStatus
from .core import EvidenceRuntimeCore
from .support import (
    _SPACE,
    MAX_QUERY_COUNT,
    DeepResearchV6RetrievalResult,
    _CandidatePlan,
    _FetchOutcome,
    _GeneralSourceTarget,
    _SeedDiscoveryOutcome,
    _SourceTarget,
    _stable_id,
)


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

    date_values = tuple(str(time_scope.get(key) or "") for key in ("as_of", "start", "end"))
    years = tuple(
        dict.fromkeys(value[:4] for value in date_values if len(value) >= 4 and value[:4].isdigit())
    )
    localized = tuple(
        part
        for part in (_clean_part(value, limit=40) for value in concepts)
        if part and any("\u4e00" <= char <= "\u9fff" for char in part)
    )[:2]
    publication_term = (
        "统计公报" if str(locale).casefold().startswith("zh") else "statistical communique"
    )
    terms = tuple(dict.fromkeys((*years, *localized, publication_term)))
    return _bounded_query(terms, None, directive, max_chars=max_chars), terms


def _time_scope_years(time_scope: Mapping[str, Any]) -> tuple[str, ...]:
    values = tuple(str(time_scope.get(key) or "") for key in ("as_of", "start", "end"))
    return tuple(
        dict.fromkeys(value[:4] for value in values if len(value) >= 4 and value[:4].isdigit())
    )


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


@dataclass
class _RetrieveState:
    """RetrievalMixin.retrieve 的运行状态：原函数的局部变量与参数（切段用）。"""

    _bounded_search: Any = None
    active_plans: Any = None
    already_fetched: Any = None
    archive_semaphore: Any = None
    archive_tasks: Any = None
    attempted_urls: Any = None
    candidate_buckets: Any = None
    canonical_page_keys: Any = None
    direct: Any = None
    direct_escalation: Any = None
    discovered: Any = None
    discovered_escalation: Any = None
    discovery_fetch_count: Any = None
    discovery_outcomes: Any = None
    discovery_tasks: Any = None
    escalated: Any = None
    escalation_archives: Any = None
    escalation_buckets: Any = None
    escalation_discovery: Any = None
    fanout: Any = None
    fetch_limit: Any = None
    frozen_route: Any = None
    identity: Any = None
    index: Any = None
    lane_limit: Any = None
    outcome: Any = None
    outcomes: Any = None
    page_key: Any = None
    page_result_refs: Any = None
    page_to_ref: Any = None
    pages: Any = None
    parent: Any = None
    parent_lease: Any = None
    plan: Any = None
    prior_page: Any = None
    query_limit: Any = None
    query_plans: Any = None
    query_semaphore: Any = None
    query_timeout_s: Any = None
    ref_to_page: Any = None
    remaining_fetches: Any = None
    remaining_s: Any = None
    research_spec: Any = None
    result_ref: Any = None
    resumed: Any = None
    route_budget: Any = None
    route_decision: Any = None
    route_value: Any = None
    run_id: Any = None
    search_count: Any = None
    searched_archives: Any = None
    searched_outcomes: Any = None
    seed_buckets: Any = None
    seed_candidates: Any = None
    selected: Any = None
    semaphore: Any = None
    spec: Any = None
    task: Any = None
    tasks: Any = None
    transition: Any = None
    use_search_fallback: Any = None


@dataclass
class _SearchPlanState:
    """RetrievalMixin._search_plan 的运行状态：原函数的局部变量与参数（切段用）。"""

    _transport: Any = None
    candidate: Any = None
    candidate_url: Any = None
    candidates: Any = None
    durable: Any = None
    locator: Any = None
    locator_value: Any = None
    plan: Any = None
    query_timeout_s: Any = None
    raw_locator: Any = None
    replayed: Any = None
    request: Any = None
    response: Any = None
    search_span: Any = None
    st: Any = None
    target: Any = None
    typed: Any = None


class RetrievalMixin(EvidenceRuntimeCore):
    """Retrieval, query plans and candidate selection."""

    __slots__ = ()

    async def retrieve(
        self,
        spec: ResearchSpecV1 | Mapping[str, Any],
        *,
        identity: object | None = None,
        route_decision: RouteDecisionV1 | Mapping[str, Any] | None = None,
    ) -> DeepResearchV6RetrievalResult:
        st = _RetrieveState(spec=spec, identity=identity, route_decision=route_decision)
        st.research_spec = (
            st.spec if isinstance(st.spec, ResearchSpecV1) else ResearchSpecV1.from_json(st.spec)
        )
        if not isinstance(st.identity, NodeExecutionIdentity):
            raise TypeError("v6 retrieval requires a bound NodeExecutionIdentity")
        st.run_id = st.identity.run_id
        await self._resolve_route_and_lease(st)
        if st.parent_lease is None:
            return DeepResearchV6RetrievalResult((), st.parent, 0, 0, 0, 0, 0, st.frozen_route)

        st.route_budget = st.frozen_route.to_json()["budget"]
        self._active_route_id = str(st.frozen_route.to_json()["route_id"])
        self._active_route_budget = copy.deepcopy(st.route_budget)
        st.query_limit = min(self.max_queries, int(st.route_budget["query"]))
        st.fetch_limit = min(self.max_fetches, int(st.route_budget["fetch"]))
        # Execution consumes the immutable route decision. ``self.max_lanes``
        # is a planner input only; consulting it again here would silently
        # re-plan a persisted route after restart or configuration drift.
        st.lane_limit = max(1, min(MAX_QUERY_COUNT, int(st.route_budget["lane"])))
        st.query_plans, st.seed_buckets = self._build_query_plans(st.research_spec)
        st.query_plans = st.query_plans[: st.query_limit]
        st.candidate_buckets: list[tuple[_CandidatePlan, ...]] = []
        st.discovery_outcomes: tuple[_SeedDiscoveryOutcome, ...] = ()
        st.seed_candidates = self._select_candidates(st.seed_buckets)
        await self._discover_seed_candidates(st)

        st.discovery_fetch_count = sum(item.upstream_called for item in st.discovery_outcomes)
        if await self._control_fenced(st.run_id):
            return DeepResearchV6RetrievalResult(
                (),
                st.parent,
                0,
                st.discovery_fetch_count,
                sum(item.status == "timeout" for item in st.discovery_outcomes),
                sum(item.status == "rejected" for item in st.discovery_outcomes),
                sum(item.status == "failed" for item in st.discovery_outcomes),
                st.frozen_route,
            )
        # A matching first-party archive link is stronger and faster than a
        # third-party search result.  Search remains the bounded fallback when
        # the archive is unavailable or has no matching frozen year.
        st.use_search_fallback = not st.candidate_buckets
        st.search_count = 0
        st.fanout = conditional_fanout_enabled(
            st.frozen_route, intent_type=st.research_spec.intent_type
        )

        st.active_plans = st.query_plans if st.use_search_fallback else ()
        await self._search_active_plans(st)

        if await self._control_fenced(st.run_id):
            return DeepResearchV6RetrievalResult(
                (),
                st.parent,
                st.search_count,
                st.discovery_fetch_count,
                sum(item.status == "timeout" for item in st.discovery_outcomes),
                sum(item.status == "rejected" for item in st.discovery_outcomes),
                sum(item.status == "failed" for item in st.discovery_outcomes),
                st.frozen_route,
            )
        st.remaining_fetches = max(0, st.fetch_limit - st.discovery_fetch_count)
        st.selected = self._select_candidates(st.candidate_buckets, limit=st.remaining_fetches)
        # Official search can return an archive/index locator rather than the
        # annual publication itself.  Resolve such locators through the same
        # frozen-year, same-authority rule used by registry seeds.  The index
        # consumes one existing fetch lease; discovered targets consume only
        # the remaining frozen fetch budget.
        st.searched_archives = tuple(
            candidate for candidate in st.selected if _looks_like_archive_locator(candidate)
        )
        await self._discover_searched_archives(st)
        if not st.selected or st.parent_lease is None:
            return DeepResearchV6RetrievalResult(
                (),
                st.parent,
                st.search_count,
                st.discovery_fetch_count,
                sum(item.status == "timeout" for item in st.discovery_outcomes),
                sum(item.status == "rejected" for item in st.discovery_outcomes),
                sum(item.status == "failed" for item in st.discovery_outcomes),
                st.frozen_route,
                tuple(
                    sorted(
                        {
                            item.page_result_ref
                            for item in st.discovery_outcomes
                            if item.page_result_ref is not None
                            and item.status in {"timeout", "rejected", "failed"}
                        }
                    )
                ),
            )

        st.outcomes = await self._fetch_candidates(st, st.selected)

        if self.durable_reads is not None:
            st.parent, st.parent_lease = await self.durable_reads.deadline_port.checkpoint(
                identity=st.identity, state=st.parent, lease=st.parent_lease
            )
        else:
            st.transition = checkpoint_deadline(
                st.parent,
                st.parent_lease,
                now_wall=self.wall_clock(),
                now_monotonic_ns=self.monotonic_ns(),
            )
            st.parent, st.parent_lease = st.transition.state, st.transition.lease

        await self._escalate_official_exact_fact(st)
        st.pages = self._canonical_pages(st.outcomes)
        self._record_page_results(st)
        return DeepResearchV6RetrievalResult(
            pages=st.pages,
            parent_deadline=st.parent,
            search_count=st.search_count,
            fetch_count=st.discovery_fetch_count
            + sum(item.upstream_called for item in st.outcomes),
            timeout_count=(
                sum(item.status == "timeout" for item in st.discovery_outcomes)
                + sum(item.status == "timeout" for item in st.outcomes)
            ),
            rejected_count=(
                sum(item.status == "rejected" for item in st.discovery_outcomes)
                + sum(item.status == "rejected" for item in st.outcomes)
            ),
            failed_count=(
                sum(item.status == "failed" for item in st.discovery_outcomes)
                + sum(item.status == "failed" for item in st.outcomes)
            ),
            route_decision=st.frozen_route,
            page_result_refs=st.page_result_refs,
        )

    async def _resolve_route_and_lease(self, st: _RetrieveState) -> None:
        """取冻结路由与父级租约（持久读取时先查已记录的路由）。"""

        if st.route_decision is None:
            st.frozen_route = self.plan_route(st.research_spec, identity=st.identity)
        else:
            st.frozen_route = (
                st.route_decision
                if isinstance(st.route_decision, RouteDecisionV1)
                else RouteDecisionV1.from_json(st.route_decision)
            )
            st.route_value = st.frozen_route.to_json()
            if (
                st.route_value["run_id"] != st.run_id
                or st.route_value["spec_hash"] != st.research_spec.spec_hash
                or st.route_value["policy_hash"] != self._policy_hash
            ):
                raise ValueError("persisted v6 route decision identity mismatch")
        if self.durable_reads is not None:
            st.parent, st.parent_lease = await self.durable_reads.deadline_port.resume_root(
                identity=st.identity,
                route_id=str(st.frozen_route.to_json()["route_id"]),
                policy_hash=self._policy_hash,
                budget_ms=self.parent_deadline_ms,
            )
        else:
            st.parent = DurableDeadlineV1.create(
                owner_id=st.run_id,
                logical_scope="deep_research_v6.official_retrieval",
                policy_hash=self._policy_hash,
                budget_ms=self.parent_deadline_ms,
                now_wall=self.wall_clock(),
            )
            st.resumed = resume_deadline(
                st.parent,
                now_wall=self.wall_clock(),
                now_monotonic_ns=self.monotonic_ns(),
            )
            st.parent = st.resumed.state
            st.parent_lease = st.resumed.lease

    async def _discover_seed_candidates(self, st: _RetrieveState) -> None:
        """种子候选并发展开，推进父级截止时间。"""

        if st.seed_candidates:
            st.semaphore = asyncio.Semaphore(min(self.max_fetch_concurrency, st.lane_limit))
            st.discovery_tasks = [
                asyncio.create_task(
                    self._discover_seed(
                        candidate,
                        parent=st.parent,
                        run_id=st.run_id,
                        identity=st.identity,
                        semaphore=st.semaphore,
                    )
                )
                for candidate in st.seed_candidates
            ]
            try:
                st.discovery_outcomes = tuple(await asyncio.gather(*st.discovery_tasks))
            except asyncio.CancelledError:
                for st.task in st.discovery_tasks:
                    st.task.cancel()
                await asyncio.gather(*st.discovery_tasks, return_exceptions=True)
                raise
            st.transition = checkpoint_deadline(
                st.parent,
                st.parent_lease,
                now_wall=self.wall_clock(),
                now_monotonic_ns=self.monotonic_ns(),
            )
            st.parent, st.parent_lease = st.transition.state, st.transition.lease
            st.candidate_buckets.extend(
                outcome.candidates for outcome in st.discovery_outcomes if outcome.candidates
            )

    async def _search_active_plans(self, st: _RetrieveState) -> None:
        """按查询计划搜索：可并发时限时并发，否则逐条搜。"""

        if st.fanout and st.active_plans and st.parent_lease is not None:
            st.remaining_s = remaining_timeout_seconds(
                st.parent_lease, now_monotonic_ns=self.monotonic_ns()
            )
            if st.remaining_s > 0:
                st.query_semaphore = asyncio.Semaphore(st.lane_limit)

                async def _bounded_search(plan: _QueryPlan) -> tuple[_CandidatePlan, ...]:
                    async with st.query_semaphore:
                        return await self._search_plan(st, plan, query_timeout_s=st.remaining_s)

                st._bounded_search = _bounded_search

                st.tasks = [
                    asyncio.create_task(st._bounded_search(plan)) for plan in st.active_plans
                ]
                st.search_count = len(st.tasks)
                try:
                    st.candidate_buckets.extend(await asyncio.gather(*st.tasks))
                except asyncio.CancelledError:
                    for st.task in st.tasks:
                        st.task.cancel()
                    await asyncio.gather(*st.tasks, return_exceptions=True)
                    raise
                if self.durable_reads is not None:
                    st.parent, st.parent_lease = await self.durable_reads.deadline_port.checkpoint(
                        identity=st.identity, state=st.parent, lease=st.parent_lease
                    )
                else:
                    st.transition = checkpoint_deadline(
                        st.parent,
                        st.parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    st.parent, st.parent_lease = st.transition.state, st.transition.lease
        else:
            for st.index, st.plan in enumerate(st.active_plans):
                if st.parent_lease is None:
                    break
                st.remaining_s = remaining_timeout_seconds(
                    st.parent_lease, now_monotonic_ns=self.monotonic_ns()
                )
                if st.remaining_s <= 0:
                    break
                st.query_timeout_s = st.remaining_s / max(1, len(st.active_plans) - st.index)
                st.candidate_buckets.append(
                    await self._search_plan(st, st.plan, query_timeout_s=st.query_timeout_s)
                )
                st.search_count += 1
                if self.durable_reads is not None:
                    st.parent, st.parent_lease = await self.durable_reads.deadline_port.checkpoint(
                        identity=st.identity, state=st.parent, lease=st.parent_lease
                    )
                else:
                    st.transition = checkpoint_deadline(
                        st.parent,
                        st.parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    st.parent, st.parent_lease = st.transition.state, st.transition.lease

    async def _discover_searched_archives(self, st: _RetrieveState) -> None:
        """搜到的归档地址先展开成候选，再与直连候选合并挑选。"""

        if st.searched_archives and st.parent_lease is not None:
            st.archive_semaphore = asyncio.Semaphore(
                min(self.max_fetch_concurrency, st.lane_limit, len(st.searched_archives))
            )
            st.archive_tasks = [
                asyncio.create_task(
                    self._discover_seed(
                        candidate,
                        parent=st.parent,
                        run_id=st.run_id,
                        identity=st.identity,
                        semaphore=st.archive_semaphore,
                    )
                )
                for candidate in st.searched_archives
            ]
            try:
                st.searched_outcomes = tuple(await asyncio.gather(*st.archive_tasks))
            except asyncio.CancelledError:
                for st.task in st.archive_tasks:
                    st.task.cancel()
                await asyncio.gather(*st.archive_tasks, return_exceptions=True)
                raise
            st.discovery_outcomes = (*st.discovery_outcomes, *st.searched_outcomes)
            st.discovery_fetch_count = sum(item.upstream_called for item in st.discovery_outcomes)
            if self.durable_reads is not None:
                st.parent, st.parent_lease = await self.durable_reads.deadline_port.checkpoint(
                    identity=st.identity, state=st.parent, lease=st.parent_lease
                )
            else:
                st.transition = checkpoint_deadline(
                    st.parent,
                    st.parent_lease,
                    now_wall=self.wall_clock(),
                    now_monotonic_ns=self.monotonic_ns(),
                )
                st.parent, st.parent_lease = st.transition.state, st.transition.lease
            st.direct = tuple(
                candidate for candidate in st.selected if not _looks_like_archive_locator(candidate)
            )
            st.discovered = tuple(
                outcome.candidates for outcome in st.searched_outcomes if outcome.candidates
            )
            st.selected = self._select_candidates(
                (*st.discovered, st.direct),
                limit=max(0, st.fetch_limit - st.discovery_fetch_count),
            )

    async def _escalate_official_exact_fact(self, st: _RetrieveState) -> None:
        """精确官方事实题：未命中时逐条再搜、补抓归档与直连候选。"""

        # Archive/direct discovery is an optimization inside the frozen
        # official-source route.  If every discovered target settles to an
        # honest negative page result, consume the route's remaining query and
        # fetch budgets through official search.  Replays reuse the same
        # durable search/page effect identities, so this escalation neither
        # mutates the ResearchSpec nor double-consumes journaled resources.
        if (
            st.research_spec.intent_type == "official_exact_fact"
            and not st.use_search_fallback
            and not self._canonical_pages(st.outcomes)
            and st.query_plans
            and st.parent_lease is not None
            and (
                st.discovery_fetch_count + sum(item.upstream_called for item in st.outcomes)
                < st.fetch_limit
            )
            and not await self._control_fenced(st.run_id)
        ):
            st.escalation_buckets: list[tuple[_CandidatePlan, ...]] = []
            for st.index, st.plan in enumerate(st.query_plans):
                if st.parent_lease is None or await self._control_fenced(st.run_id):
                    break
                st.remaining_s = remaining_timeout_seconds(
                    st.parent_lease, now_monotonic_ns=self.monotonic_ns()
                )
                if st.remaining_s <= 0:
                    break
                st.escalation_buckets.append(
                    await self._search_plan(
                        st,
                        st.plan,
                        query_timeout_s=(st.remaining_s / max(1, len(st.query_plans) - st.index)),
                    )
                )
                st.search_count += 1
                if self.durable_reads is not None:
                    st.parent, st.parent_lease = await self.durable_reads.deadline_port.checkpoint(
                        identity=st.identity, state=st.parent, lease=st.parent_lease
                    )
                else:
                    st.transition = checkpoint_deadline(
                        st.parent,
                        st.parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    st.parent, st.parent_lease = st.transition.state, st.transition.lease

            st.attempted_urls = {candidate.requested_url for candidate in st.selected}
            st.escalation_buckets = [
                tuple(
                    candidate
                    for candidate in bucket
                    if candidate.requested_url not in st.attempted_urls
                )
                for bucket in st.escalation_buckets
            ]
            st.remaining_fetches = max(
                0,
                st.fetch_limit
                - st.discovery_fetch_count
                - sum(item.upstream_called for item in st.outcomes),
            )
            st.escalated = self._select_candidates(
                st.escalation_buckets, limit=st.remaining_fetches
            )
            st.escalation_archives = tuple(
                candidate for candidate in st.escalated if _looks_like_archive_locator(candidate)
            )
            if st.escalation_archives and st.parent_lease is not None:
                st.archive_semaphore = asyncio.Semaphore(
                    min(
                        self.max_fetch_concurrency,
                        st.lane_limit,
                        len(st.escalation_archives),
                    )
                )
                st.archive_tasks = [
                    asyncio.create_task(
                        self._discover_seed(
                            candidate,
                            parent=st.parent,
                            run_id=st.run_id,
                            identity=st.identity,
                            semaphore=st.archive_semaphore,
                        )
                    )
                    for candidate in st.escalation_archives
                ]
                try:
                    st.escalation_discovery = tuple(await asyncio.gather(*st.archive_tasks))
                except asyncio.CancelledError:
                    for st.task in st.archive_tasks:
                        st.task.cancel()
                    await asyncio.gather(*st.archive_tasks, return_exceptions=True)
                    raise
                st.discovery_outcomes = (
                    *st.discovery_outcomes,
                    *st.escalation_discovery,
                )
                st.discovery_fetch_count = sum(
                    item.upstream_called for item in st.discovery_outcomes
                )
                if self.durable_reads is not None:
                    st.parent, st.parent_lease = await self.durable_reads.deadline_port.checkpoint(
                        identity=st.identity,
                        state=st.parent,
                        lease=st.parent_lease,
                    )
                else:
                    st.transition = checkpoint_deadline(
                        st.parent,
                        st.parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    st.parent, st.parent_lease = st.transition.state, st.transition.lease
                st.direct_escalation = tuple(
                    candidate
                    for candidate in st.escalated
                    if not _looks_like_archive_locator(candidate)
                )
                st.discovered_escalation = tuple(
                    outcome.candidates for outcome in st.escalation_discovery if outcome.candidates
                )
                st.already_fetched = sum(item.upstream_called for item in st.outcomes)
                st.escalated = self._select_candidates(
                    (*st.discovered_escalation, st.direct_escalation),
                    limit=max(
                        0,
                        st.fetch_limit - st.discovery_fetch_count - st.already_fetched,
                    ),
                )
            if st.escalated and st.parent_lease is not None:
                st.outcomes = (*st.outcomes, *(await self._fetch_candidates(st, st.escalated)))
                if self.durable_reads is not None:
                    st.parent, st.parent_lease = await self.durable_reads.deadline_port.checkpoint(
                        identity=st.identity, state=st.parent, lease=st.parent_lease
                    )
                else:
                    st.transition = checkpoint_deadline(
                        st.parent,
                        st.parent_lease,
                        now_wall=self.wall_clock(),
                        now_monotonic_ns=self.monotonic_ns(),
                    )
                    st.parent, st.parent_lease = st.transition.state, st.transition.lease

    def _record_page_results(self, st: _RetrieveState) -> None:
        """整理页面结果引用；有持久读取时登记页面与引用的对应关系。"""

        st.page_result_refs = tuple(
            sorted(
                {
                    *self._canonical_page_result_refs(st.outcomes),
                    *(
                        item.page_result_ref
                        for item in st.discovery_outcomes
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
            st.page_to_ref: dict[tuple[str, str], str] = {}
            st.ref_to_page: dict[str, tuple[str, str]] = {}
            for st.outcome in sorted(st.outcomes, key=lambda item: item.ordinal):
                if st.outcome.status != "verified" or st.outcome.page is None:
                    continue
                st.page_key = (st.outcome.page.final_url_hash, st.outcome.page.body_hash)
                if st.page_key in st.page_to_ref:
                    continue
                st.result_ref = st.outcome.page_result_ref
                if st.result_ref is None:
                    raise ValueError("canonical page is missing canonical result ref")
                st.prior_page = st.ref_to_page.get(st.result_ref)
                if st.prior_page is not None and st.prior_page != st.page_key:
                    raise ValueError("canonical page result ref aliases distinct pages")
                st.page_to_ref[st.page_key] = st.result_ref
                st.ref_to_page[st.result_ref] = st.page_key
            st.canonical_page_keys = {(page.final_url_hash, page.body_hash) for page in st.pages}
            if st.canonical_page_keys != set(st.page_to_ref):
                raise ValueError("canonical page is missing canonical result ref")

    async def _fetch_candidates(
        self,
        st: _RetrieveState,
        candidates: Sequence[_CandidatePlan],
    ) -> tuple[_FetchOutcome, ...]:
        concurrency = min(self.max_fetch_concurrency, st.lane_limit, len(candidates))
        semaphore = asyncio.Semaphore(concurrency)
        cursor = 0
        cursor_lock = asyncio.Lock()
        completed: list[_FetchOutcome] = []

        async def _fetch_worker() -> None:
            nonlocal cursor
            while True:
                # Pull at most one new page after each durable signal
                # check. In-flight pages still settle canonical outcomes.
                if await self._control_fenced(st.run_id):
                    return
                async with cursor_lock:
                    if cursor >= len(candidates):
                        return
                    candidate = candidates[cursor]
                    cursor += 1
                completed.append(
                    await self._fetch_one(
                        candidate,
                        parent=st.parent,
                        run_id=st.run_id,
                        identity=st.identity,
                        semaphore=semaphore,
                    )
                )

        tasks = [asyncio.create_task(_fetch_worker()) for _ in range(concurrency)]
        try:
            await asyncio.gather(*tasks)
            return tuple(completed)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _search_plan(
        self, st: _RetrieveState, plan: _QueryPlan, *, query_timeout_s: float
    ) -> tuple[_CandidatePlan, ...]:
        sp = _SearchPlanState(st=st, plan=plan, query_timeout_s=query_timeout_s)
        sp.request = SearchRequest(
            query=sp.plan.query,
            max_results=min(5, self.max_fetches),
            region=(
                sp.plan.target.jurisdiction.casefold()
                if isinstance(sp.plan.target, OfficialSourceTargetV1)
                else "wt-wt"
            ),
            mode="research",
            hydrate_top=0,
            total_timeout_s=sp.query_timeout_s,
            request_id=sp.plan.request_id,
            run_id=sp.st.run_id,
            dimension_id=sp.plan.dimension_id,
            query_terms=sp.plan.query_terms,
        )
        sp.response: SearchResponse | None = None
        sp.search_span = await self._start_child_span(
            identity=sp.st.identity,
            request_id=sp.plan.request_id,
            stage=(
                "official_search"
                if isinstance(sp.plan.target, OfficialSourceTargetV1)
                else "general_search"
            ),
            ordinal=sp.plan.ordinal,
        )
        try:
            if self.durable_reads is None:
                sp.response = await asyncio.wait_for(
                    dispatch_with_run_fence(
                        acquire_fence=self.dispatch_fence_acquirer,
                        run_id=sp.st.run_id,
                        operation_kind="workflow.research.search",
                        operation_id=sp.plan.request_id,
                        invoke=lambda: self.search_gateway.search(sp.request),
                    ),
                    timeout=sp.query_timeout_s,
                )
            else:
                await self._search_plan_durable(sp)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._finish_child_span(sp.search_span, SpanStatus.CANCELLED, outcome="cancelled")
            )
            raise
        except TimeoutError:
            await self._finish_child_span(sp.search_span, SpanStatus.ERROR, outcome="timeout")
            sp.response = None
        except Exception:
            # Search failures are evidence gaps.  Other frozen dimensions
            # can still return useful pages under the same parent budget.
            await self._finish_child_span(sp.search_span, SpanStatus.ERROR, outcome="error")
            sp.response = None
        else:
            await self._finish_child_span(
                sp.search_span,
                SpanStatus.OK,
                outcome="hit" if sp.response.results else "empty",
                count=len(sp.response.results),
            )
        if isinstance(sp.response, SearchResponse):
            sp.candidates: list[_CandidatePlan] = []
            for sp.candidate in sp.response.results:
                sp.candidate_url = _candidate_url(sp.candidate)
                if not sp.candidate_url.startswith(("http://", "https://")):
                    continue
                try:
                    sp.target = (
                        sp.plan.target
                        if isinstance(sp.plan.target, OfficialSourceTargetV1)
                        else _general_source_target(
                            sp.candidate_url, source_types=sp.plan.target.source_types
                        )
                    )
                except ValueError:
                    continue
                sp.candidates.append(
                    _CandidatePlan(
                        ordinal=0,
                        dimension_id=sp.plan.dimension_id,
                        candidate_id=sp.candidate.stable_id,
                        requested_url=sp.candidate_url,
                        title=sp.candidate.title,
                        target=sp.target,
                        discovery_years=sp.plan.discovery_years,
                    )
                )
            return tuple(sp.candidates)
        return ()

    async def _search_plan_durable(self, sp: _SearchPlanState) -> None:
        """持久读取路径：经持久读取执行官方搜索并回放候选。"""

        async def _transport(attempt) -> OfficialSearchResultV1:
            try:
                attempt_timeout_s = sp.query_timeout_s
                if attempt.deadline_monotonic_ns is not None:
                    attempt_timeout_s = min(
                        attempt_timeout_s,
                        max(
                            0.0,
                            (attempt.deadline_monotonic_ns - self.monotonic_ns()) / 1_000_000_000,
                        ),
                    )
                item = await asyncio.wait_for(
                    dispatch_with_run_fence(
                        acquire_fence=self.dispatch_fence_acquirer,
                        run_id=sp.st.run_id,
                        operation_kind="workflow.research.search",
                        operation_id=attempt.logical_effect_id,
                        invoke=lambda: self.search_gateway.search(sp.request),
                    ),
                    timeout=attempt_timeout_s,
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                return OfficialSearchResultV1.create(
                    logical_effect_id=attempt.logical_effect_id,
                    attempt_no=attempt.attempt_no,
                    request_id=sp.plan.request_id,
                    query_hash=hashlib.sha256(sp.plan.query.encode("utf-8")).hexdigest(),
                    target_id=sp.plan.target.target_id,
                    candidates=(),
                    outcome="timeout",
                    error_code="deadline_expired",
                    deadline_id=attempt.deadline_id,
                )
            except Exception:
                return OfficialSearchResultV1.create(
                    logical_effect_id=attempt.logical_effect_id,
                    attempt_no=attempt.attempt_no,
                    request_id=sp.plan.request_id,
                    query_hash=hashlib.sha256(sp.plan.query.encode("utf-8")).hexdigest(),
                    target_id=sp.plan.target.target_id,
                    candidates=(),
                    outcome="failed",
                    error_code="search_failed",
                    deadline_id=attempt.deadline_id,
                )
            typed_candidates: list[SearchCandidateV1] = []
            for raw_candidate in item.results:
                candidate_url = _candidate_url(raw_candidate)
                if not candidate_url.startswith(("http://", "https://")):
                    continue
                locator = await self._persist_locator(
                    candidate=_CandidatePlan(
                        ordinal=len(typed_candidates),
                        dimension_id=sp.plan.dimension_id,
                        candidate_id=raw_candidate.stable_id,
                        requested_url=candidate_url,
                        title=raw_candidate.title,
                        target=sp.plan.target,
                        discovery_years=sp.plan.discovery_years,
                    ),
                    final_url=candidate_url,
                    verification_status="unverified",
                    identity=sp.st.identity,
                )
                typed_candidates.append(
                    SearchCandidateV1.create(
                        ordinal=len(typed_candidates),
                        source_locator_ref=locator["source_locator_ref"],
                        title_hash=hashlib.sha256(raw_candidate.title.encode("utf-8")).hexdigest(),
                        snippet_hash=hashlib.sha256(
                            raw_candidate.snippet.encode("utf-8")
                        ).hexdigest(),
                        authority_match="unverified",
                        reason_codes=("candidate_unverified",),
                    )
                )
            return OfficialSearchResultV1.create(
                logical_effect_id=attempt.logical_effect_id,
                attempt_no=attempt.attempt_no,
                request_id=sp.plan.request_id,
                query_hash=hashlib.sha256(sp.plan.query.encode("utf-8")).hexdigest(),
                target_id=sp.plan.target.target_id,
                candidates=tuple(typed_candidates),
                outcome="succeeded" if typed_candidates else "empty",
                error_code=None,
                deadline_id=attempt.deadline_id,
            )

        sp._transport = _transport

        sp.durable = await self.durable_reads.execute(
            operation_kind="official_search",
            route_id=str(sp.st.frozen_route.to_json()["route_id"]),
            target_or_page_id=sp.plan.target.target_id,
            ordinal=sp.plan.ordinal,
            resource_kind="query",
            resource_hard_limit=int(sp.st.route_budget["query"]),
            resource_policy_hash=self._policy_hash,
            deadline=sp.st.parent,
            identity=sp.st.identity,
            transport=sp._transport,
        )
        sp.typed = OfficialSearchResultV1.from_json(sp.durable)
        if sp.typed.outcome != "succeeded":
            raise TimeoutError if sp.typed.outcome == "timeout" else RuntimeError
        sp.replayed: list[RetrievalCandidate] = []
        for sp.candidate in sp.typed.candidates:
            sp.raw_locator = await self.blobs.get(
                sp.candidate.source_locator_ref.removeprefix("sha256:")
            )
            sp.locator_value = json.loads(sp.raw_locator.decode("utf-8"))
            if not isinstance(sp.locator_value, Mapping):
                raise ValueError("source locator blob must be an object")
            sp.locator = SourceLocatorV1.from_json(sp.locator_value)
            if sp.locator.verification_status != sp.candidate.authority_match:
                raise ValueError("search candidate/locator authority mismatch")
            sp.replayed.append(
                RetrievalCandidate(
                    stable_id=sp.candidate.candidate_id,
                    url=sp.locator.canonical_url,
                    canonical_url=sp.locator.canonical_url,
                    title="",
                )
            )
        sp.response = SearchResponse(
            query=sp.plan.query,
            results=tuple(sp.replayed),
            request_id=sp.typed.request_id,
            run_id=sp.st.run_id,
        )

    def _build_query_plans(
        self,
        spec: ResearchSpecV1,
    ) -> tuple[tuple[_QueryPlan, ...], tuple[tuple[_CandidatePlan, ...], ...]]:
        raw = spec.to_json()
        requirements = {str(item["requirement_id"]): item for item in raw["requirements"]}
        user_constraints = raw["user_constraints"]
        user_jurisdictions = tuple(user_constraints["jurisdictions"])
        user_preferred = tuple(user_constraints["preferred_authority_ids"])
        query_plans: list[_QueryPlan] = []
        seeds: list[tuple[_CandidatePlan, ...]] = []

        if spec.intent_type != "official_exact_fact":
            for dimension in sorted(raw["work_dimensions"], key=lambda item: item["ordinal"]):
                if len(query_plans) >= self.max_queries:
                    break
                requirement_ids = tuple(sorted(dimension["requirement_ids"]))
                concepts = tuple(
                    part
                    for part in (
                        _clean_part(value, limit=80) for value in dimension["search_concepts"]
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
            preferred = tuple(sorted(set(source["preferred_authority_ids"]) | set(user_preferred)))
            concepts = tuple(
                part
                for part in (_clean_part(value, limit=80) for value in dimension["search_concepts"])
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

        effective_limit = (
            self.max_fetches if limit is None else max(0, min(limit, self.max_fetches))
        )
        ordered_buckets = tuple(
            (
                tuple(bucket)
                if all(isinstance(candidate.target, OfficialSourceTargetV1) for candidate in bucket)
                else tuple(
                    sorted(
                        bucket,
                        key=lambda candidate: (
                            candidate.dimension_id,
                            hashlib.sha256(candidate.requested_url.encode("utf-8")).hexdigest(),
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
