"""Reentrant DeepResearch stages with explicit I/O ports.

The functions in this module deliberately stop before persistence, artifact
publication, receipts, and UI delivery.  They can therefore be called by the
legacy blocking tool today and by a durable graph in a later task.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ...tools import research_scoring
from ...tools import research_tools as legacy
from ..contracts import NodeExecutionIdentity
from .deep_research_v5_contracts import ResearchLLMResult


class LLMCall(Protocol):
    async def __call__(self, prompt: str) -> str: ...


class LLMCallV2(Protocol):
    async def __call__(
        self,
        prompt: str,
        *,
        max_output_tokens: int,
        stable_call_id: str,
        response_format: Mapping[str, Any] | None = None,
    ) -> ResearchLLMResult: ...


class ResearchEffectCall(Protocol):
    async def __call__(
        self,
        *,
        role: str,
        payload_ref: str,
        max_output_tokens: int,
        stable_call_id: str,
        execution_identity: NodeExecutionIdentity,
    ) -> ResearchLLMResult: ...


class ResearchStageOutputError(ValueError):
    """A committed provider response could not satisfy a stage output contract."""


class ResearchStageCancelled(RuntimeError):
    """A control fence cancelled an in-flight stage at a checkpoint-safe seam."""


class SearchCall(Protocol):
    async def __call__(
        self, query: str, *, max_results: int
    ) -> list[dict[str, Any]]: ...


class ExtractCall(Protocol):
    async def __call__(self, url: str) -> dict[str, Any]: ...


class DirectCall(Protocol):
    async def __call__(
        self, query: str, source: str
    ) -> object: ...


class ArtifactSaveCall(Protocol):
    async def __call__(
        self,
        *,
        topic: str,
        report_md: str,
        report_hash: str,
        run_id: str,
    ) -> Mapping[str, Any]: ...


MaybeAsync = Callable[[], object | Awaitable[object]]


async def _maybe_await(value: object | Awaitable[object]) -> object:
    if asyncio.iscoroutine(value) or isinstance(value, Awaitable):
        return await value
    return value


@dataclass(slots=True)
class FetchPort:
    """Run-owned extraction port and JS-render budget.

    ``claim_js_render`` is synchronized because one run fetches many URLs in
    parallel.  Two runs always receive distinct instances, so neither can
    consume the other's fallback allowance.
    """

    extractor: ExtractCall | None = None
    max_js_renders: int = legacy._JS_RENDER_MAX_PER_RUN
    js_renders_used: int = 0
    _budget_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def claim_js_render(self) -> bool:
        async with self._budget_lock:
            if self.js_renders_used >= self.max_js_renders:
                return False
            self.js_renders_used += 1
            return True

    async def extract(self, url: str) -> dict[str, Any]:
        if self.extractor is None or self.extractor is legacy.default_extract:
            return await legacy.default_extract(url, fetch_port=self)
        return await self.extractor(url)


@dataclass(frozen=True, slots=True)
class ResearchLLMPort:
    complete: LLMCall
    rerank: LLMCall | None = None
    semantic_score: Callable[[str, list[str]], object] | None = None


@dataclass(frozen=True, slots=True)
class ResearchLLMPortV2:
    """Raw at-most-once LLM transport used behind the durable effect port."""

    complete: LLMCallV2


RESEARCH_LLM_ROLES = frozenset(
    {
        "modeling",
        "query_strategy",
        "dimension_analysis",
        "report_synthesis",
        "quality_audit",
        "targeted_repair",
        "evidence_candidate_extract",
        "evidence_inference_synthesize",
        "evidence_structured_repair",
    }
)


@dataclass(frozen=True, slots=True)
class ResearchCallEffectPort:
    """Node-facing surface; durable implementation lands with the journal slice."""

    complete_call: ResearchEffectCall

    async def complete(
        self,
        *,
        role: str,
        payload_ref: str,
        max_output_tokens: int,
        stable_call_id: str,
        execution_identity: NodeExecutionIdentity | None,
    ) -> ResearchLLMResult:
        if role not in RESEARCH_LLM_ROLES:
            raise ValueError(f"unsupported research LLM role: {role}")
        if not payload_ref:
            raise ValueError("payload_ref is required")
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens < 1
        ):
            raise ValueError("max_output_tokens must be a positive integer")
        if not stable_call_id:
            raise ValueError("stable_call_id is required")
        if not isinstance(execution_identity, NodeExecutionIdentity):
            raise TypeError("research effect call requires context.identity")
        return await self.complete_call(
            role=role,
            payload_ref=payload_ref,
            max_output_tokens=max_output_tokens,
            stable_call_id=stable_call_id,
            execution_identity=execution_identity,
        )


@dataclass(frozen=True, slots=True)
class ResearchArtifactPort:
    """Run-owned adapter that persists the final Markdown report."""

    save_call: ArtifactSaveCall

    async def save(
        self,
        *,
        topic: str,
        report_md: str,
        report_hash: str,
        run_id: str,
    ) -> Mapping[str, Any]:
        return await self.save_call(
            topic=topic,
            report_md=report_md,
            report_hash=report_hash,
            run_id=run_id,
        )


@dataclass(frozen=True, slots=True)
class DirectOutcome:
    source: str
    items: list[dict[str, Any]]
    status: str
    backend: str | None = None
    reason_code: str | None = None
    observation: Mapping[str, Any] = field(default_factory=dict)


class ResearchSearchResults(list[dict[str, Any]]):
    """List-compatible search rows carrying request-local diagnostics.

    The legacy workflow still sees a plain list contract, while concurrent v2
    branches can inspect the observation that belongs to their own request
    instead of racing on a shared "last search" dictionary.
    """

    def __init__(
        self,
        values: Iterable[dict[str, Any]] = (),
        *,
        observation: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(values)
        self.observation = dict(observation or {})


@dataclass(frozen=True, slots=True)
class ResearchSearchPort:
    search_call: SearchCall
    direct_call: DirectCall | None = None
    reset_runtime: MaybeAsync | None = None
    observe_runtime: Callable[[], Mapping[str, Any]] | None = None

    async def search(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        return await self.search_call(query, max_results=max_results)

    async def direct(
        self, query: str, source: str
    ) -> DirectOutcome:
        if self.direct_call is None:
            return DirectOutcome(source, [], "empty")
        result = await self.direct_call(query, source)
        if isinstance(result, DirectOutcome):
            return result
        if isinstance(result, tuple) and len(result) == 2:
            result_source, items = result
            normalized = list(items or [])
            return DirectOutcome(
                str(result_source), normalized, "hit" if normalized else "empty"
            )
        raise TypeError("direct source must return DirectOutcome or (source, items)")

    async def reset(self) -> None:
        if self.reset_runtime is not None:
            await _maybe_await(self.reset_runtime())

    def observation(self) -> Mapping[str, Any]:
        if self.observe_runtime is None:
            return {}
        return self.observe_runtime()


@dataclass(frozen=True, slots=True)
class ResearchPorts:
    llm: ResearchLLMPort
    search: ResearchSearchPort
    fetch: FetchPort

    @classmethod
    def from_workflow_context(cls, context: object) -> ResearchPorts:
        """Read the three research ports from a ``WorkflowContext``-like object."""

        port = getattr(context, "port", None)
        if port is None:
            raise TypeError("workflow context must provide port(name)")
        llm = port("llm")
        search = port("search")
        fetch = port("fetch")
        if not isinstance(llm, ResearchLLMPort):
            raise TypeError("workflow llm port must be ResearchLLMPort")
        if not isinstance(search, ResearchSearchPort):
            raise TypeError("workflow search port must be ResearchSearchPort")
        if not isinstance(fetch, FetchPort):
            raise TypeError("workflow fetch port must be FetchPort")
        return cls(llm=llm, search=search, fetch=fetch)


@dataclass(frozen=True, slots=True)
class ResearchCoreConfig:
    max_sub_questions: int = 5
    max_urls_per_query: int = 4
    max_total_passages: int = 12
    min_passage_chars: int = 250
    max_rounds: int = 1
    query_expansion: bool = True
    site_directed: bool = True
    source_packs: bool = True
    direct_sources: bool = True
    direct_timeout: float = 45.0
    rerank_mode: str = "llm"

    @classmethod
    def from_legacy(
        cls,
        *,
        max_sub_questions: int,
        max_urls_per_query: int,
        max_total_passages: int,
        min_passage_chars: int,
        max_rounds: int,
    ) -> ResearchCoreConfig:
        raw = legacy._research_raw()
        try:
            direct_timeout = float(raw.get("direct_timeout", 45.0))
        except (TypeError, ValueError):
            direct_timeout = 45.0
        return cls(
            max_sub_questions=max_sub_questions,
            max_urls_per_query=max_urls_per_query,
            max_total_passages=max_total_passages,
            min_passage_chars=min_passage_chars,
            max_rounds=max_rounds,
            query_expansion=legacy._query_expansion_enabled(),
            site_directed=legacy._site_directed_enabled(),
            source_packs=legacy._source_packs_enabled(),
            direct_sources=legacy._direct_sources_enabled(),
            direct_timeout=direct_timeout,
            rerank_mode=legacy._rerank_mode(),
        )


def _empty_stage_times() -> dict[str, int]:
    return {
        "plan": 0,
        "search": 0,
        "fetch": 0,
        "score": 0,
        "synth": 0,
        "direct": 0,
    }


def _empty_drop_counts() -> dict[str, int]:
    return {
        "ai_generated": 0,
        "low_quality": 0,
        "mojibake": 0,
        "too_short": 0,
        "direct_source_empty": 0,
    }


@dataclass(slots=True)
class ResearchCoreState:
    request_topic: str
    llm_topic: str
    mode: str
    route: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    dropped_by_reason: dict[str, int] = field(default_factory=_empty_drop_counts)
    elapsed_ms_per_stage: dict[str, int] = field(default_factory=_empty_stage_times)
    sub_questions: list[str] = field(default_factory=list)
    expansion_queries: list[str] = field(default_factory=list)
    search_specs: list[tuple[str, str]] = field(default_factory=list)
    url_to_question: dict[str, str] = field(default_factory=dict)
    candidate_urls: list[str] = field(default_factory=list)
    extracted: list[Any] = field(default_factory=list)
    passages: list[legacy.Passage] = field(default_factory=list)
    velocity: str = ""
    rounds: int = 1
    reranker: str = "off"
    report_md: str = ""
    citations: list[legacy.Citation] = field(default_factory=list)
    cite_result: dict[str, Any] = field(
        default_factory=lambda: {"ok": True, "missing": [], "unused": []}
    )

    def observability_coverage(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "mode": self.mode,
            "n_dropped_by_reason": dict(self.dropped_by_reason),
            "elapsed_ms_per_stage": dict(self.elapsed_ms_per_stage),
        }


def _stage_ms(start: float) -> int:
    return max(0, int(round((time.perf_counter() - start) * 1000)))


def _initialize_agent_reach_route(state: ResearchCoreState, config: ResearchCoreConfig) -> None:
    from ...tools import research_sources
    from ...tools.agent_reach_port import agent_reach_port

    planned: list[str] = []
    planned_urls: list[str] = []
    if config.direct_sources and "agent_reach" in research_sources._direct_source_types():
        for text in [state.request_topic, *state.sub_questions]:
            for url in research_sources._agent_reach_urls(text):
                if url not in planned_urls and len(planned_urls) < 4:
                    planned_urls.append(url)
                if url not in planned_urls:
                    continue
                name = agent_reach_port.channel_name_for_url(url.rstrip(")]}'\""))
                if name not in planned:
                    planned.append(name)
            if len(planned_urls) >= 4:
                break
    state.route["agent_reach"] = {
        "planned_channels": planned,
        "planned_urls": planned_urls,
        "doctor": agent_reach_port.doctor(names=[*planned, "web"] if planned else ["web"]),
        "hits": [],
        "degraded": [],
    }


async def plan_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
    *,
    skip_plan: bool = False,
) -> ResearchCoreState:
    start = time.perf_counter()
    if skip_plan:
        state.sub_questions = [state.request_topic]
    else:
        try:
            raw = await ports.llm.complete(
                legacy._PLAN_PROMPT.format(
                    topic=state.llm_topic,
                    user_request=state.request_topic,
                )
            )
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"plan_llm: {exc}")
            raw = ""
        state.sub_questions = legacy.parse_sub_questions(
            raw, max_questions=config.max_sub_questions
        )
    if not state.sub_questions:
        state.sub_questions = [state.request_topic]
        state.errors.append("plan_fallback: using topic verbatim")
    _initialize_agent_reach_route(state, config)
    state.elapsed_ms_per_stage["plan"] = _stage_ms(start)
    return state


async def expand_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
) -> ResearchCoreState:
    state.expansion_queries = []
    if config.query_expansion:
        state.expansion_queries = await legacy._expand_queries(
            ports.llm.complete,
            state.request_topic,
            state.sub_questions,
            state.errors,
        )
    return state


def _stable_search_specs(
    state: ResearchCoreState, config: ResearchCoreConfig
) -> tuple[list[tuple[str, str]], list[str], int]:
    specs: list[tuple[str, str]] = []
    seen: set[str] = set()
    packs: set[str] = set()
    pack_queries = 0

    def append(query: str, owner: str) -> bool:
        normalized = (query or "").strip()
        key = normalized.lower()
        if not normalized or key in seen:
            return False
        seen.add(key)
        specs.append((normalized, owner))
        return True

    for question in state.sub_questions:
        append(question, question)
        site = legacy._site_directive_for(question) if config.site_directed else None
        if site:
            append(f"{question} {site}", question)
        if config.source_packs:
            for pack_name, source_query in legacy._source_pack_queries_for(question):
                if append(source_query, question):
                    packs.add(pack_name)
                    pack_queries += 1
    for query in state.expansion_queries:
        append(query, state.request_topic)
    return specs, sorted(packs), pack_queries


async def search_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
) -> ResearchCoreState:
    start = time.perf_counter()
    specs, packs, pack_queries = _stable_search_specs(state, config)
    state.search_specs = specs
    state.route["source_packs_hit"] = packs
    state.route["source_pack_queries"] = pack_queries
    results = await legacy._gather_safe(
        [
            ports.search.search(query, max_results=config.max_urls_per_query)
            for query, _ in specs
        ],
        label="search",
    )
    state.elapsed_ms_per_stage["search"] = _stage_ms(start)

    url_to_question: dict[str, str] = {}
    for (query, owner), hits in zip(specs, results):
        if isinstance(hits, BaseException):
            state.errors.append(f"search:{query!r}: {hits}")
            continue
        for hit in hits or []:
            url = hit.get("url")
            if url and url not in url_to_question:
                url_to_question[url] = owner
    state.url_to_question = url_to_question

    observation = ports.search.observation()
    state.route["engines_hit"] = list(observation.get("engines_hit", []))
    for error in observation.get("errors", []):
        if error not in state.errors:
            state.errors.append(str(error))
    if not url_to_question:
        state.errors.append("no search results")
    return state


async def fetch_extract_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
) -> ResearchCoreState:
    limit = config.max_urls_per_query * len(state.search_specs)
    state.candidate_urls = list(state.url_to_question)[:limit]
    start = time.perf_counter()
    state.extracted = await legacy._gather_safe(
        [ports.fetch.extract(url) for url in state.candidate_urls],
        label="extract",
    )
    state.elapsed_ms_per_stage["fetch"] = _stage_ms(start)
    return state


def _keywords(state: ResearchCoreState) -> list[str]:
    raw = legacy._topic_keywords(state.request_topic) + [
        keyword
        for question in state.sub_questions
        for keyword in legacy._topic_keywords(question)
    ]
    # Repeated plan wording must not dilute the deterministic relevance score.
    return list(dict.fromkeys(raw))


_GENERIC_ASCII_RESEARCH_WORDS = {
    "best", "deep", "latest", "practices", "report", "research", "study",
    "topic", "update",
}
_GENERIC_CJK_BIGRAMS = {
    "什么", "哪些", "如何", "怎么", "最佳", "实践", "报告", "调研",
    "深度", "来源", "引用", "最后", "一份", "帮我", "请问",
}


def _topic_anchors(topic: str) -> list[str]:
    anchors = [
        token
        for token in re.findall(r"[a-z][a-z0-9-]{2,}", topic.lower())
        if token not in _GENERIC_ASCII_RESEARCH_WORDS and not token.isdigit()
    ]
    for chunk in re.findall(r"[一-鿿]+", topic):
        for index in range(max(0, len(chunk) - 1)):
            token = chunk[index : index + 2]
            if token not in _GENERIC_CJK_BIGRAMS:
                anchors.append(token)
    return list(dict.fromkeys(anchors))


def _passes_topic_anchor_gate(
    state: ResearchCoreState, passage: legacy.Passage
) -> bool:
    """Reject obvious cross-topic fallback pages without relying on an LLM.

    Both ASCII terms and CJK bigrams participate. Ordinary lexical relevance
    cannot rescue a weak match; only an explicit semantic scorer or successful
    LLM reranker may bridge terminology variants.
    """

    # Planner output is untrusted: a drifted subquestion must not be able to
    # validate its own off-topic evidence.
    anchors = _topic_anchors(state.request_topic)
    if not anchors:
        return True
    haystack = f"{passage.citation.title}\n{passage.text}".lower()
    def anchored(anchor: str) -> bool:
        variants = {anchor}
        if anchor.endswith("ies") and len(anchor) > 5:
            variants.add(f"{anchor[:-3]}y")
        for suffix in ("ing", "ed", "es", "s"):
            if anchor.endswith(suffix) and len(anchor) > len(suffix) + 3:
                variants.add(anchor[: -len(suffix)])
        return any(variant in haystack for variant in variants)

    hits = sum(1 for anchor in anchors if anchored(anchor))
    if hits >= min(2, len(anchors)):
        return True
    dims = passage.dims or {}
    if hits >= 1 and dims.get("direct_source_verified"):
        return True
    if float(dims.get("semantic_relevance", 0.0)) >= 5.0:
        return True
    return bool(dims.get("llm_rerank_verified")) and float(
        dims.get("relevance", 0.0)
    ) >= 5.0


def _passage_from_extract(
    state: ResearchCoreState,
    config: ResearchCoreConfig,
    url: str,
    payload: Any,
) -> legacy.Passage | None:
    if isinstance(payload, BaseException):
        state.errors.append(f"extract:{url}: {payload}")
        return None
    if not isinstance(payload, dict):
        state.errors.append(f"extract:{url}: unknown")
        return None
    # Legacy extractors return ``ok`` explicitly, while FetchDocument.to_dict()
    # uses the presence of non-empty ``text`` as its success contract.  The
    # production v2+ fetch adapter returns the latter shape, so requiring the
    # legacy flag silently discarded successfully fetched documents.
    explicit_ok = payload.get("ok")
    extract_ok = bool(explicit_ok) if explicit_ok is not None else bool(payload.get("text"))
    if not extract_ok:
        error = (
            payload.get("error", "extract failed")
        )
        state.errors.append(f"extract:{url}: {error}")
        return None
    if research_scoring.is_low_quality(url):
        state.dropped_by_reason["low_quality"] += 1
        state.errors.append(f"dropped_low_quality:{url}")
        return None
    text = (payload.get("text") or "").strip()
    if len(text) < config.min_passage_chars:
        state.dropped_by_reason["too_short"] += 1
        return None
    if payload.get("ai_generated") or research_scoring.is_ai_generated(text):
        state.dropped_by_reason["ai_generated"] += 1
        state.errors.append(f"dropped_ai_generated:{url}")
        return None
    if research_scoring.is_mojibake(text):
        state.dropped_by_reason["mojibake"] += 1
        state.errors.append(f"dropped_mojibake:{url}")
        return None

    keywords = _keywords(state)
    authority = research_scoring.score_authority(url)
    recency = research_scoring.score_recency(
        str(payload.get("date") or ""), topic_velocity=state.velocity
    )
    relevance = legacy._relevance_score(text, keywords=keywords)
    depth = min(len(text) / 2000.0, 1.0) * 10.0
    score = research_scoring.composite_score(
        authority=authority,
        recency=recency,
        relevance=relevance,
        depth=depth,
        topic_velocity=state.velocity,
    )
    try:
        fetched_at = float(payload.get("fetched_at", time.time()))
    except (TypeError, ValueError):
        # FetchDocument emits ISO-8601 timestamps. Citation freshness is
        # informational here; recency scoring above uses the document date.
        fetched_at = time.time()
    return legacy.Passage(
        citation=legacy.Citation(
            n=0,
            url=url,
            title=(payload.get("title") or url)[:200],
            snippet=text[: config.min_passage_chars].replace("\n", " ").strip(),
            fetched_at=fetched_at,
            authority=authority,
        ),
        text=text,
        score=score,
        dims={
            "authority": authority,
            "recency": recency,
            "relevance": relevance,
            "depth": depth,
        },
    )


async def direct_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
) -> ResearchCoreState:
    if not config.direct_sources or ports.search.direct_call is None:
        return state
    from ...tools import research_sources

    start = time.perf_counter()
    specs: list[tuple[str, str]] = []
    seen_specs: set[tuple[str, str]] = set()
    agent_reach_route = state.route.get("agent_reach", {})
    agent_reach_urls = (
        list(agent_reach_route.get("planned_urls", []))
        if isinstance(agent_reach_route, dict)
        else []
    )
    for url in agent_reach_urls:
        key = (" ".join(str(url).lower().split()), "agent_reach")
        if key not in seen_specs:
            seen_specs.add(key)
            specs.append((str(url), "agent_reach"))
    for question in [state.request_topic, *state.sub_questions]:
        for source in research_sources.direct_source_for(question):
            if source == "agent_reach":
                continue
            key = (" ".join(question.lower().split()), source)
            if key not in seen_specs:
                seen_specs.add(key)
                specs.append((question, source))

    async def run_direct(
        question: str, source: str
    ) -> DirectOutcome:
        try:
            return await ports.search.direct(question, source)
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"direct:{source}:{question!r}: {exc}")
            return DirectOutcome(source, [], "degraded", reason_code="adapter_error")

    results: list[Any]
    if specs:
        async def execute_direct_calls() -> list[Any]:
            reach_results: list[Any] = []
            other_tasks = []
            for question, source in specs:
                if source == "agent_reach":
                    reach_results.append(await run_direct(question, source))
                else:
                    other_tasks.append(run_direct(question, source))
            other_results = (
                await legacy._gather_safe(other_tasks, label="direct")
                if other_tasks
                else []
            )
            return [*reach_results, *other_results]

        try:
            results = await asyncio.wait_for(
                execute_direct_calls(),
                timeout=config.direct_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"direct_stage: {exc}")
            results = []
    else:
        results = []

    seen = {legacy._norm_url(url) for url in state.candidate_urls}
    seen.update(legacy._norm_url(p.citation.url) for p in state.passages)
    hits: list[str] = []
    keywords = _keywords(state)
    for result in results:
        if isinstance(result, BaseException) or not result:
            continue
        if not isinstance(result, DirectOutcome):
            continue
        source, items = result.source, result.items
        agent_reach_record: dict[str, object] | None = None
        agent_reach_route: dict[str, object] | None = None
        if source == "agent_reach":
            route = state.route.setdefault(
                "agent_reach",
                {"planned_channels": [], "doctor": {}, "hits": [], "degraded": []},
            )
            if isinstance(route, dict):
                observation = dict(result.observation)
                if isinstance(observation.get("doctor"), dict):
                    doctor = route.setdefault("doctor", {})
                    if isinstance(doctor, dict):
                        doctor.update(observation["doctor"])
                agent_reach_record = {
                    "url": str(observation.get("url") or ""),
                    "channel": str(observation.get("channel") or "web"),
                    "active_backend": result.backend,
                    "status": result.status,
                    "reason_code": result.reason_code,
                }
                agent_reach_route = route
        added = 0
        for payload in items:
            url = payload.get("url", "")
            text = (payload.get("text") or "").strip()
            normalized = legacy._norm_url(url)
            if not url or not text or normalized in seen:
                continue
            seen.add(normalized)
            authority = research_scoring.score_authority(url)
            relevance = legacy._relevance_score(text, keywords=keywords)
            depth = min(len(text) / 2000.0, 1.0) * 10.0
            score = research_scoring.composite_score(
                authority=authority,
                recency=8.0,
                relevance=relevance,
                depth=depth,
                topic_velocity=state.velocity,
            )
            state.passages.append(
                legacy.Passage(
                    citation=legacy.Citation(
                        n=0,
                        url=url,
                        title=(payload.get("title") or url)[:200],
                        snippet=text[:250].replace("\n", " ").strip(),
                        fetched_at=float(payload.get("fetched_at", time.time())),
                        authority=authority,
                    ),
                    text=text,
                    score=score,
                    dims={
                        "authority": authority,
                        "recency": 8.0,
                        "relevance": relevance,
                        "depth": depth,
                        "direct_source_verified": True,
                    },
                )
            )
            added += 1
        if added:
            if source not in hits:
                hits.append(source)
            if agent_reach_route is not None and agent_reach_record is not None:
                rows = agent_reach_route.setdefault("hits", [])
                if isinstance(rows, list) and agent_reach_record not in rows:
                    rows.append(agent_reach_record)
        else:
            state.dropped_by_reason["direct_source_empty"] += 1
            if (
                agent_reach_route is not None
                and agent_reach_record is not None
                and result.status in {"degraded", "error", "off"}
            ):
                rows = agent_reach_route.setdefault("degraded", [])
                if isinstance(rows, list) and agent_reach_record not in rows:
                    rows.append(agent_reach_record)
    state.route["direct_sources_hit"] = hits
    if hits:
        state.errors = [error for error in state.errors if error != "no search results"]
    state.elapsed_ms_per_stage["direct"] = _stage_ms(start)
    return state


def _stable_sort_passages(passages: Iterable[legacy.Passage]) -> list[legacy.Passage]:
    """Sort by score while preserving deterministic discovery order on ties."""

    indexed = list(enumerate(passages))
    indexed.sort(key=lambda item: (-item[1].score, item[0]))
    return [passage for _, passage in indexed]


async def score_rerank_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
    *,
    finalize: bool,
) -> ResearchCoreState:
    start = time.perf_counter()
    if not state.velocity:
        state.velocity = research_scoring.infer_topic_velocity(state.request_topic)
    if state.extracted:
        for url, payload in zip(state.candidate_urls, state.extracted):
            passage = _passage_from_extract(state, config, url, payload)
            if passage is not None:
                state.passages.append(passage)
        state.extracted = []
    if not finalize:
        state.elapsed_ms_per_stage["score"] = _stage_ms(start)
        return state

    scorer = ports.llm.semantic_score
    if scorer is not None and state.passages:
        try:
            similarities = await _maybe_await(
                scorer(
                    state.request_topic,
                    [passage.text[:2000] for passage in state.passages],
                )
            )
        except Exception as exc:  # noqa: BLE001
            similarities = None
            legacy.log.debug("semantic relevance skipped: %s", exc)
        if similarities and len(similarities) == len(state.passages):
            for passage, similarity in zip(state.passages, similarities):
                semantic = max(0.0, min(1.0, float(similarity))) * 10.0
                dims = passage.dims or {}
                relevance = max(float(dims.get("relevance", 0.0)), semantic)
                dims["relevance"] = relevance
                dims["semantic_relevance"] = semantic
                passage.dims = dims
                passage.score = research_scoring.composite_score(
                    authority=float(dims.get("authority", 3.0)),
                    recency=float(dims.get("recency", 3.0)),
                    relevance=relevance,
                    depth=float(dims.get("depth", 0.0)),
                    topic_velocity=state.velocity,
                )

    state.passages = _stable_sort_passages(state.passages)
    state.reranker = "off"
    if (
        config.rerank_mode != "off"
        and state.passages
        and ports.llm.rerank is not None
    ):
        pool_size = min(
            len(state.passages),
            max(
                config.max_total_passages,
                min(config.max_total_passages * 2, 24),
            ),
        )
        pool = state.passages[:pool_size]
        applied = await legacy._llm_rerank(
            state.request_topic,
            pool,
            ports.llm.rerank,
            state.errors,
            velocity=state.velocity,
        )
        if applied:
            state.passages = _stable_sort_passages(pool)
            state.reranker = "llm"
        else:
            state.reranker = "llm_failed"

    relevant: list[legacy.Passage] = []
    for passage in state.passages:
        if _passes_topic_anchor_gate(state, passage):
            relevant.append(passage)
            continue
        state.errors.append(f"dropped_irrelevant:{passage.citation.url}")
    state.passages = relevant

    state.passages = state.passages[: config.max_total_passages]
    for index, passage in enumerate(state.passages, start=1):
        citation = passage.citation
        passage.citation = legacy.Citation(
            n=index,
            url=citation.url,
            title=citation.title,
            snippet=citation.snippet,
            fetched_at=citation.fetched_at,
            authority=citation.authority,
        )
    state.elapsed_ms_per_stage["score"] = _stage_ms(start)
    return state


async def gap_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
) -> ResearchCoreState:
    if config.max_rounds < 2 or not state.passages:
        return state
    followups = await legacy._gap_followup_queries(
        ports.llm.complete,
        state.request_topic,
        state.sub_questions,
        state.passages,
        state.errors,
    )
    if not followups:
        return state
    state.rounds = 2
    hits = await legacy._gather_safe(
        [
            ports.search.search(query, max_results=config.max_urls_per_query)
            for query in followups
        ],
        label="search2",
    )
    seen = set(state.candidate_urls)
    urls: list[str] = []
    for batch in hits:
        if isinstance(batch, BaseException):
            continue
        for hit in batch or []:
            url = hit.get("url")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    urls = urls[: config.max_urls_per_query * len(followups)]
    if not urls:
        return state
    extracted = await legacy._gather_safe(
        [ports.fetch.extract(url) for url in urls], label="extract2"
    )
    for url, payload in zip(urls, extracted):
        passage = _passage_from_extract(state, config, url, payload)
        if passage is not None:
            state.passages.append(passage)
    return state


async def synth_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
) -> ResearchCoreState:
    del config
    start = time.perf_counter()
    passage_block = legacy._format_passages_for_llm(state.passages)
    try:
        report_md = await ports.llm.complete(
            legacy._SYNTH_PROMPT.format(
                topic=state.llm_topic,
                request_topic=state.request_topic,
                user_request=state.request_topic,
                n_passages=len(state.passages),
                passages=passage_block,
            )
        )
    except Exception as exc:  # noqa: BLE001
        state.errors.append(f"synth_llm: {exc}")
        report_md = legacy._passages_only_fallback(
            state.request_topic, state.passages
        )
    state.report_md = (report_md or "").strip()
    if not state.report_md:
        state.report_md = legacy._passages_only_fallback(
            state.request_topic, state.passages
        )
    state.elapsed_ms_per_stage["synth"] = _stage_ms(start)
    return state


async def citation_stage(
    state: ResearchCoreState,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
) -> ResearchCoreState:
    del ports, config
    citations = [passage.citation for passage in state.passages]
    state.report_md, state.citations, state.cite_result = legacy._finalize_report_md(
        state.report_md, citations, state.errors
    )
    return state


def _coverage(state: ResearchCoreState) -> dict[str, Any]:
    domains = {
        legacy._host(citation.url)
        for citation in state.citations
        if legacy._host(citation.url)
    }
    diversity = research_scoring.diversity_report(
        [citation.url for citation in state.citations]
    )
    return {
        "n_sources": len(state.citations),
        "n_domains": len(domains),
        "n_sub_questions": len(state.sub_questions),
        "cite_check_ok": state.cite_result["ok"],
        "cite_missing": state.cite_result["missing"],
        "cite_unused": state.cite_result["unused"],
        "topic_velocity": state.velocity,
        "unique_domains": diversity["unique_domains"],
        "max_single_domain_share": diversity["max_single_domain_share"],
        "source_types": diversity["source_types"],
        "diversity_ok": diversity["passes"],
        "rounds": state.rounds,
        "reranker": state.reranker,
        **state.observability_coverage(),
    }


async def _default_direct_call(
    query: str, source: str
) -> DirectOutcome:
    from ...tools import research_sources

    backend = None
    if source == "agent_reach":
        items, status, reason, backend, channel, doctor = (
            await research_sources.agent_reach_search_with_status(query, max_results=3)
        )
        return DirectOutcome(
            source,
            list(items),
            status,
            backend,
            reason,
            {"url": query, "channel": channel, "doctor": doctor},
        )
    if source == "cninfo":
        items = await research_sources.cninfo_search(query, max_results=3)
        if not items:
            items = await research_sources.edgar_search(query, max_results=1)
            if items:
                backend = "sec_edgar"
    else:
        fetcher = research_sources.DIRECT_FETCHERS.get(source)
        items = await fetcher(query, max_results=3) if fetcher else []
    normalized = list(items or [])
    return DirectOutcome(
        source, normalized, "hit" if normalized else "empty", backend
    )


def _legacy_search_reset() -> None:
    try:
        from ...tools import search_provider

        search_provider.reset_search_runtime_state()
        search_provider.reset_search_cdp_budget()
    except Exception:  # noqa: BLE001
        pass


def _legacy_search_observation() -> Mapping[str, Any]:
    try:
        from ...tools import search_provider

        return {
            "engines_hit": search_provider.get_last_engines_hit(),
            "errors": search_provider.get_last_search_errors(),
        }
    except Exception:  # noqa: BLE001
        return {}


def legacy_ports(
    *,
    llm_call: LLMCall,
    search: SearchCall | None,
    extract: ExtractCall | None,
) -> ResearchPorts:
    search_call = search or legacy.default_search
    return ResearchPorts(
        llm=ResearchLLMPort(
            complete=llm_call,
            rerank=legacy._RERANK_LLM_CALL,
            semantic_score=legacy._SEMANTIC_SCORER,
        ),
        search=ResearchSearchPort(
            search_call=search_call,
            direct_call=_default_direct_call,
            reset_runtime=_legacy_search_reset,
            observe_runtime=_legacy_search_observation,
        ),
        fetch=FetchPort(extractor=extract),
    )


async def run_research_core(
    topic: str,
    *,
    ports: ResearchPorts,
    config: ResearchCoreConfig,
    mode: str = "standard",
    user_request: str | None = None,
    scheduler: object | None = None,
    parent_sid: str = "default",
    depth: int = 0,
    skip_plan: bool = False,
) -> legacy.ResearchReport:
    """Compose all reusable stages and return the legacy report DTO."""

    if not topic or not topic.strip():
        return legacy.ResearchReport(
            topic="",
            summary="",
            report_md="",
            citations=[],
            sub_questions=[],
            coverage={"n_sources": 0, "n_domains": 0, "n_sub_questions": 0},
            errors=["empty topic"],
        )
    clean_topic = topic.strip()
    request_topic = (user_request or clean_topic).strip()
    search_call = ports.search.search_call
    route_fallback = str(
        getattr(search_call, "route", None)
        or getattr(search_call, "provider", None)
        or getattr(search_call, "engine", None)
        or "ddg"
    )
    state = ResearchCoreState(
        request_topic=request_topic,
        llm_topic=clean_topic,
        mode=(mode or "standard").lower(),
        route={
            "engines_hit": [],
            "direct_sources_hit": [],
            "fallback": route_fallback,
            "source_packs_enabled": config.source_packs,
            "source_packs_hit": [],
            "source_pack_queries": 0,
        },
    )
    await ports.search.reset()
    await plan_stage(state, ports, config, skip_plan=skip_plan)

    fanout = (
        scheduler is not None
        and depth == 0
        and len(state.sub_questions) >= legacy._fanout_min_subquestions()
        and legacy._fanout_enabled()
        and not state.route.get("agent_reach", {}).get("planned_urls")
    )
    if fanout:
        return await legacy._run_subagent_fanout(
            topic=request_topic,
            sub_questions=state.sub_questions,
            llm_call=ports.llm.complete,
            search=ports.search.search_call,
            extract=ports.fetch.extractor or legacy.default_extract,
            scheduler=scheduler,
            parent_sid=parent_sid,
            mode=state.mode,
            user_request=request_topic,
            errors=state.errors,
            route=state.route,
        )

    await expand_stage(state, ports, config)
    await search_stage(state, ports, config)
    await fetch_extract_stage(state, ports, config)
    await score_rerank_stage(state, ports, config, finalize=False)
    await direct_stage(state, ports, config)
    await gap_stage(state, ports, config)
    await score_rerank_stage(state, ports, config, finalize=True)

    if not state.passages:
        state.errors.append("no usable passages")
        return legacy.ResearchReport(
            topic=request_topic,
            summary="",
            report_md=legacy._no_results_template(
                request_topic, state.sub_questions
            ),
            citations=[],
            sub_questions=state.sub_questions,
            coverage={
                "n_sources": 0,
                "n_domains": 0,
                "n_sub_questions": len(state.sub_questions),
                **state.observability_coverage(),
            },
            errors=state.errors,
        )

    await synth_stage(state, ports, config)
    await citation_stage(state, ports, config)
    return legacy.ResearchReport(
        topic=request_topic,
        summary=legacy._extract_summary(state.report_md),
        report_md=state.report_md,
        citations=state.citations,
        sub_questions=state.sub_questions,
        coverage=_coverage(state),
        errors=state.errors,
    )


__all__ = [
    "RESEARCH_LLM_ROLES",
    "FetchPort",
    "ResearchArtifactPort",
    "ResearchCallEffectPort",
    "ResearchCoreConfig",
    "ResearchCoreState",
    "ResearchLLMPort",
    "ResearchLLMPortV2",
    "ResearchLLMResult",
    "ResearchPorts",
    "ResearchSearchPort",
    "ResearchSearchResults",
    "citation_stage",
    "direct_stage",
    "expand_stage",
    "fetch_extract_stage",
    "gap_stage",
    "legacy_ports",
    "plan_stage",
    "run_research_core",
    "score_rerank_stage",
    "search_stage",
    "synth_stage",
]
