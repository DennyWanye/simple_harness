# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Version 1 durable DeepResearch workflow graph."""

from __future__ import annotations

import copy
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse

from ....tools import research_tools as legacy
from ...contracts import (
    ChannelSpec,
    JsonType,
    JsonValue,
    ReducerKind,
    RetryPolicy,
    StatePatch,
    WorkflowContext,
    WorkflowState,
    validate_json_value,
)
from ...definition import (
    END_NODE,
    CompiledWorkflow,
    ConditionalEdge,
    Edge,
    NodeDefinition,
    WorkflowDefinition,
    compile_workflow,
)
from ...store import BlobStore
from .. import deep_research_nodes as research_nodes
from ..research_core import ResearchCoreConfig, ResearchCoreState, ResearchPorts


WORKFLOW_NAME = "deep_research"
WORKFLOW_VERSION = "v1"
STATE_SCHEMA_VERSION = 1
MAX_GAP_ITERATIONS = 1

_CORE_WRITERS = frozenset(
    {
        "normalize",
        "plan",
        "expand",
        "search",
        "direct",
        "fetch",
        "score",
        "gap",
        "rerank",
        "synth",
        "cite",
    }
)
_VALUE_WRITERS = _CORE_WRITERS | frozenset({"persist", "finalize"})
_CONFIG_FIELDS = {item.name for item in fields(ResearchCoreConfig)}
_INTEGER_LIMITS = {
    "max_sub_questions": (1, 10),
    "max_urls_per_query": (1, 10),
    "max_total_passages": (1, 50),
    "min_passage_chars": (1, 20_000),
    "max_rounds": (1, MAX_GAP_ITERATIONS + 1),
}
_BOOLEAN_FIELDS = {
    "query_expansion",
    "site_directed",
    "source_packs",
    "direct_sources",
}


def _input_value(state: Mapping[str, object], name: str, default: object) -> object:
    value = state.get(name)
    if value is not None:
        return value
    values = state.get("values")
    if isinstance(values, Mapping) and name in values:
        return values[name]
    return default


def _effective_state(state: Mapping[str, object]) -> dict[str, object]:
    effective = dict(state)
    values = state.get("values")
    if isinstance(values, Mapping):
        effective.update(values)
    return effective


def _merged_values(
    state: Mapping[str, object], updates: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    values = state.get("values")
    merged = dict(values) if isinstance(values, Mapping) else {}
    merged.update(copy.deepcopy(dict(updates)))
    validate_json_value(merged)
    return merged


def _normalized_config(state: Mapping[str, object]) -> ResearchCoreConfig:
    defaults = asdict(ResearchCoreConfig())
    raw = _input_value(state, "research_config", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("research_config must be an object")
    unknown = sorted(set(raw) - _CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"unknown research config keys: {', '.join(unknown)}")
    values = {**defaults, **dict(raw)}
    for name, (lower, upper) in _INTEGER_LIMITS.items():
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"research config {name} must be an integer")
        values[name] = max(lower, min(upper, value))
    for name in _BOOLEAN_FIELDS:
        if not isinstance(values[name], bool):
            raise ValueError(f"research config {name} must be a boolean")
    timeout = values["direct_timeout"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("research config direct_timeout must be numeric")
    values["direct_timeout"] = max(0.1, min(300.0, float(timeout)))
    rerank_mode = str(values["rerank_mode"] or "off").strip().lower()
    values["rerank_mode"] = rerank_mode if rerank_mode in {"off", "llm"} else "off"
    return ResearchCoreConfig(**values)


def _blob_store(state: Mapping[str, object]) -> BlobStore | None:
    root = state.get("blob_root")
    if not isinstance(root, str) or not root.strip():
        return None
    return BlobStore(Path(root))


def _json_safe_extract(value: object) -> JsonValue:
    if isinstance(value, BaseException):
        return {"ok": False, "error": f"{type(value).__name__}: {value}"}
    copied = copy.deepcopy(value)
    try:
        validate_json_value(copied)
    except Exception:
        return {"ok": False, "error": f"non_json_extract:{type(value).__name__}"}
    return copied  # type: ignore[return-value]


def _citation_payload(citation: legacy.Citation) -> dict[str, JsonValue]:
    payload = asdict(citation)
    validate_json_value(payload)
    return payload


def _passage_payload(passage: legacy.Passage) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "citation": _citation_payload(passage.citation),
        "text": passage.text,
        "score": passage.score,
        "dims": copy.deepcopy(passage.dims),
    }
    validate_json_value(payload)
    return payload


def _encode_core(state: ResearchCoreState, blob_store: BlobStore | None) -> dict[str, JsonValue]:
    extracted = [_json_safe_extract(value) for value in state.extracted]
    passages = [_passage_payload(value) for value in state.passages]
    payload: dict[str, JsonValue] = {
        "request_topic": state.request_topic,
        "llm_topic": state.llm_topic,
        "mode": state.mode,
        "route": copy.deepcopy(state.route),
        "errors": list(state.errors),
        "dropped_by_reason": dict(state.dropped_by_reason),
        "elapsed_ms_per_stage": dict(state.elapsed_ms_per_stage),
        "sub_questions": list(state.sub_questions),
        "expansion_queries": list(state.expansion_queries),
        "search_specs": [list(spec) for spec in state.search_specs],
        "url_to_question": dict(state.url_to_question),
        "candidate_urls": list(state.candidate_urls),
        "extracted": research_nodes.encode_large_value(extracted, blob_store=blob_store),
        "passages": research_nodes.encode_large_value(passages, blob_store=blob_store),
        "velocity": state.velocity,
        "rounds": state.rounds,
        "reranker": state.reranker,
        "report_md": state.report_md,
        "citations": [_citation_payload(value) for value in state.citations],
        "cite_result": copy.deepcopy(state.cite_result),
    }
    validate_json_value(payload)
    return payload


def _decode_citation(raw: object) -> legacy.Citation:
    if not isinstance(raw, Mapping):
        raise ValueError("citation checkpoint value must be an object")
    return legacy.Citation(
        n=int(raw["n"]),
        url=str(raw["url"]),
        title=str(raw["title"]),
        snippet=str(raw["snippet"]),
        fetched_at=float(raw["fetched_at"]),
        authority=float(raw.get("authority", 5.0)),
    )


def _decode_core(state: Mapping[str, object]) -> ResearchCoreState:
    payload = state.get("research_state")
    if not isinstance(payload, Mapping):
        raise ValueError("research_state checkpoint value is missing")
    store = _blob_store(state)
    raw_extracted = research_nodes.decode_large_value(
        payload.get("extracted", []), blob_store=store
    )
    raw_passages = research_nodes.decode_large_value(
        payload.get("passages", []), blob_store=store
    )
    if not isinstance(raw_extracted, list) or not isinstance(raw_passages, list):
        raise ValueError("research checkpoint payload lists are invalid")
    passages: list[legacy.Passage] = []
    for raw in raw_passages:
        if not isinstance(raw, Mapping):
            raise ValueError("passage checkpoint value must be an object")
        dims = raw.get("dims", {})
        if not isinstance(dims, Mapping):
            raise ValueError("passage dims checkpoint value must be an object")
        passages.append(
            legacy.Passage(
                citation=_decode_citation(raw["citation"]),
                text=str(raw["text"]),
                score=float(raw["score"]),
                dims=dict(dims),
            )
        )
    raw_specs = payload.get("search_specs", [])
    if not isinstance(raw_specs, list):
        raise ValueError("search_specs checkpoint value must be an array")
    return ResearchCoreState(
        request_topic=str(payload.get("request_topic", "")),
        llm_topic=str(payload.get("llm_topic", "")),
        mode=str(payload.get("mode", "standard")),
        route=dict(payload.get("route", {})),
        errors=[str(value) for value in payload.get("errors", [])],
        dropped_by_reason={
            str(key): int(value)
            for key, value in dict(payload.get("dropped_by_reason", {})).items()
        },
        elapsed_ms_per_stage={
            str(key): int(value)
            for key, value in dict(payload.get("elapsed_ms_per_stage", {})).items()
        },
        sub_questions=[str(value) for value in payload.get("sub_questions", [])],
        expansion_queries=[str(value) for value in payload.get("expansion_queries", [])],
        search_specs=[(str(spec[0]), str(spec[1])) for spec in raw_specs],
        url_to_question={
            str(key): str(value)
            for key, value in dict(payload.get("url_to_question", {})).items()
        },
        candidate_urls=[str(value) for value in payload.get("candidate_urls", [])],
        extracted=raw_extracted,
        passages=passages,
        velocity=str(payload.get("velocity", "")),
        rounds=int(payload.get("rounds", 1)),
        reranker=str(payload.get("reranker", "off")),
        report_md=str(payload.get("report_md", "")),
        citations=[_decode_citation(raw) for raw in payload.get("citations", [])],
        cite_result=dict(payload.get("cite_result", {})),
    )


async def _run_core_stage(
    state: WorkflowState,
    context: WorkflowContext,
    stage: Callable[
        [ResearchCoreState, ResearchPorts, ResearchCoreConfig],
        Awaitable[ResearchCoreState],
    ],
) -> StatePatch:
    effective = _effective_state(state)
    core = _decode_core(effective)
    if core.request_topic:
        await stage(
            core,
            ResearchPorts.from_workflow_context(context),
            _normalized_config(effective),
        )
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {"research_state": _encode_core(core, _blob_store(effective))},
            )
        }
    )


async def normalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective_state(state)
    topic = str(_input_value(effective, "topic", "") or "").strip()
    mode = str(_input_value(effective, "mode", "standard") or "standard").strip().lower()
    if mode not in {"light", "standard", "deep"}:
        mode = "standard"
    config = _normalized_config(effective)
    root = str(_input_value(effective, "blob_root", "") or "").strip()
    ports = ResearchPorts.from_workflow_context(context)
    await ports.search.reset()
    search_call = ports.search.search_call
    fallback = str(
        getattr(search_call, "route", None)
        or getattr(search_call, "provider", None)
        or getattr(search_call, "engine", None)
        or "ddg"
    )
    core = ResearchCoreState(
        request_topic=topic,
        llm_topic=topic,
        mode=mode,
        route={
            "engines_hit": [],
            "direct_sources_hit": [],
            "fallback": fallback,
            "source_packs_enabled": config.source_packs,
            "source_packs_hit": [],
            "source_pack_queries": 0,
        },
        errors=[] if topic else ["empty topic"],
    )
    normalized_state = {**effective, "blob_root": root}
    return StatePatch(
        {
            "values": {
                "topic": topic,
                "mode": mode,
                "research_config": asdict(config),
                "blob_root": root,
                "research_state": _encode_core(core, _blob_store(normalized_state)),
            },
            "loop_counters": {"gap_iterations": 0},
            "budgets": {"gap_iterations": MAX_GAP_ITERATIONS},
        }
    )


async def plan_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _run_core_stage(state, context, research_nodes.plan_node)


async def expand_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _run_core_stage(state, context, research_nodes.expand_node)


async def search_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _run_core_stage(state, context, research_nodes.search_node)


async def direct_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _run_core_stage(state, context, research_nodes.direct_node)


async def fetch_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _run_core_stage(state, context, research_nodes.fetch_extract_node)


async def score_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    async def score(
        core: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
    ) -> ResearchCoreState:
        return await research_nodes.score_rerank_node(core, ports, config, finalize=False)

    return await _run_core_stage(state, context, score)


async def gap_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective_state(state)
    core = _decode_core(effective)
    config = _normalized_config(effective)
    counters = state.get("loop_counters", {})
    used = int(counters.get("gap_iterations", 0)) if isinstance(counters, Mapping) else 0
    if (
        core.request_topic
        and core.passages
        and config.max_rounds > 1
        and used < MAX_GAP_ITERATIONS
    ):
        await research_nodes.gap_node(
            core, ResearchPorts.from_workflow_context(context), config
        )
        used += 1
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {"research_state": _encode_core(core, _blob_store(effective))},
            ),
            "loop_counters": {"gap_iterations": used},
        }
    )


async def gap_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    effective = _effective_state(state)
    core = _decode_core(effective)
    if not core.passages:
        return "no_results"
    config = _normalized_config(effective)
    budget = min(MAX_GAP_ITERATIONS, max(0, config.max_rounds - 1))
    counters = state.get("loop_counters", {})
    used = int(counters.get("gap_iterations", 0)) if isinstance(counters, Mapping) else 0
    if used < budget:
        return "continue"
    return "done"


async def rerank_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    async def rerank(
        core: ResearchCoreState, ports: ResearchPorts, config: ResearchCoreConfig
    ) -> ResearchCoreState:
        return await research_nodes.score_rerank_node(core, ports, config, finalize=True)

    return await _run_core_stage(state, context, rerank)


async def synth_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _run_core_stage(state, context, research_nodes.synth_node)


async def cite_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    return await _run_core_stage(state, context, research_nodes.citation_node)


def _coverage(core: ResearchCoreState) -> dict[str, JsonValue]:
    domains = {
        urlparse(citation.url).netloc.lower()
        for citation in core.citations
        if urlparse(citation.url).netloc
    }
    coverage: dict[str, JsonValue] = {
        "n_sources": len(core.citations),
        "n_domains": len(domains),
        "n_sub_questions": len(core.sub_questions),
        "cite_check_ok": bool(core.cite_result.get("ok", False)),
        "cite_missing": list(core.cite_result.get("missing", [])),
        "cite_unused": list(core.cite_result.get("unused", [])),
        "topic_velocity": core.velocity,
        "rounds": core.rounds,
        "reranker": core.reranker,
        **core.observability_coverage(),
    }
    validate_json_value(coverage)
    return coverage


async def persist_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective_state(state)
    core = _decode_core(effective)
    no_results = not core.passages
    if no_results and not core.report_md:
        core.report_md = legacy._no_results_template(core.request_topic, core.sub_questions)
        if "no usable passages" not in core.errors:
            core.errors.append("no usable passages")
    report: dict[str, JsonValue] = {
        "topic": core.request_topic,
        "summary": legacy._extract_summary(core.report_md),
        "report_md": core.report_md,
        "citations": [_citation_payload(value) for value in core.citations],
        "sub_questions": list(core.sub_questions),
        "coverage": _coverage(core),
        "errors": list(core.errors),
    }
    encoded = research_nodes.encode_large_value(report, blob_store=_blob_store(effective))
    blob_refs: list[str] = []
    if isinstance(encoded, dict) and research_nodes.BLOB_REF_KEY in encoded:
        raw_ref = encoded[research_nodes.BLOB_REF_KEY]
        if isinstance(raw_ref, dict):
            blob_refs.append(str(raw_ref["sha256"]))
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "report_payload": {
                        "schema_version": 1,
                        "status": "no_results" if no_results else "completed",
                        "report": encoded,
                    }
                },
            ),
            "blob_refs": blob_refs,
        }
    )


async def finalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective_state(state)
    report = effective.get("report_payload")
    if not isinstance(report, dict):
        raise ValueError("persisted report payload is missing")
    core = _decode_core(effective)
    run_id = context.identity.run_id if context.identity is not None else str(state.get("run_id", ""))
    if not run_id:
        raise ValueError("workflow run id is missing")
    summary = legacy._extract_summary(core.report_md)
    if not summary:
        summary = f"DeepResearch completed with status {report.get('status', 'unknown')}."
    intents: list[dict[str, JsonValue]] = [
        {
            "intent_id": f"{run_id}:report",
            "kind": "report",
            "channel": "workflow_report",
            "payload": {"report": copy.deepcopy(report)},
        },
        {
            "intent_id": f"{run_id}:artifact",
            "kind": "artifact_card",
            "channel": "artifact",
            "payload": {
                "artifact_type": "research_report",
                "report": copy.deepcopy(report),
                "preview": summary,
            },
        },
        {
            "intent_id": f"{run_id}:final",
            "kind": "final_assistant",
            "channel": "final_assistant",
            # Session delivery cannot dereference workflow blobs. Project the
            # complete report text while retaining the blob-backed payload for
            # checkpoints, replay, and evaluation.
            "payload": {
                "text": core.report_md or summary,
                "summary": summary,
                "report": copy.deepcopy(report),
            },
        },
    ]
    return StatePatch({"values": {"delivery_intents": intents}})


def _single(value_type: JsonType, writers: frozenset[str]) -> ChannelSpec:
    return ChannelSpec(
        value_type=value_type,
        reducer=ReducerKind.SINGLE_WRITER,
        allowed_writers=writers,
    )


DEEP_RESEARCH_V1_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=(
        NodeDefinition("normalize", normalize_handler),
        NodeDefinition("plan", plan_handler),
        NodeDefinition("expand", expand_handler),
        NodeDefinition("search", search_handler),
        NodeDefinition("direct", direct_handler),
        NodeDefinition(
            "fetch",
            fetch_handler,
            retry_policy=RetryPolicy(
                max_attempts=2,
                retryable_codes=frozenset({"retryable_network", "retryable_provider"}),
            ),
        ),
        NodeDefinition("score", score_handler),
        NodeDefinition("gap", gap_handler),
        NodeDefinition("rerank", rerank_handler),
        NodeDefinition("synth", synth_handler),
        NodeDefinition("cite", cite_handler),
        NodeDefinition("persist", persist_handler),
        NodeDefinition("finalize", finalize_handler),
    ),
    channels={
        "values": _single(JsonType.OBJECT, _VALUE_WRITERS),
        "loop_counters": _single(
            JsonType.OBJECT, frozenset({"normalize", "gap"})
        ),
        "budgets": _single(JsonType.OBJECT, frozenset({"normalize"})),
        "blob_refs": _single(JsonType.ARRAY, frozenset({"persist"})),
    },
    edges=(
        Edge("normalize", "plan"),
        Edge("plan", "expand"),
        Edge("expand", "search"),
        Edge("search", "direct"),
        Edge("direct", "fetch"),
        Edge("fetch", "score"),
        Edge("score", "gap"),
        Edge("rerank", "synth"),
        Edge("synth", "cite"),
        Edge("cite", "persist"),
        Edge("persist", "finalize"),
        Edge("finalize", END_NODE),
    ),
    conditional_edges=(
        ConditionalEdge(
            "gap",
            gap_route,
            {"continue": "gap", "done": "rerank", "no_results": "persist"},
        ),
    ),
    recursion_limit=64,
    max_supersteps=32,
    loop_budgets={"gap_iterations": MAX_GAP_ITERATIONS},
    loop_budget_bindings={"gap->gap": "gap_iterations"},
    prompt_manifest={
        "research_core": "v1",
        "stages": [
            "plan",
            "expand",
            "search",
            "direct",
            "fetch",
            "score",
            "gap-check",
            "rerank",
            "synthesize",
            "citation-check",
        ],
    },
    policy_manifest={
        "implementation": "deep-research-graph-v1.0.0",
        "checkpoint_payload": "strict-json-with-content-addressed-large-values",
        "gap_budget": MAX_GAP_ITERATIONS,
        "terminal_contract": "delivery-intents-only",
        "fanout_strategy": "deterministic-staged-search-then-direct",
    },
)

DEEP_RESEARCH_V1: CompiledWorkflow = compile_workflow(DEEP_RESEARCH_V1_DEFINITION)


def initial_state(
    *,
    topic: str,
    run_id: str,
    thread_id: str | None = None,
    session_id: str = "",
    mode: str = "standard",
    research_config: Mapping[str, JsonValue] | None = None,
    blob_root: str | Path | None = None,
) -> WorkflowState:
    """Build the strict JSON input envelope expected by the v1 graph."""

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "workflow_name": WORKFLOW_NAME,
        "workflow_version": WORKFLOW_VERSION,
        "thread_id": thread_id or run_id,
        "run_id": run_id,
        "session_id": session_id,
        "active_nodes": [],
        "active_step_id": None,
        "status": "pending",
        "values": {
            "topic": topic,
            "mode": mode,
            "research_config": dict(research_config or {}),
            "blob_root": str(blob_root) if blob_root is not None else "",
        },
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {"gap_iterations": 0},
        "budgets": {"gap_iterations": MAX_GAP_ITERATIONS},
        "errors": [],
    }


__all__ = [
    "DEEP_RESEARCH_V1",
    "DEEP_RESEARCH_V1_DEFINITION",
    "MAX_GAP_ITERATIONS",
    "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "initial_state",
]
