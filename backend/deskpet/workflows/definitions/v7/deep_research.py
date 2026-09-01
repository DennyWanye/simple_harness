"""DeepResearch v7 manager/child simplified durable workflow."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from typing import Any

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
    Edge,
    NodeDefinition,
    WorkflowDefinition,
    compile_workflow,
)
from ...store import BlobStore
from .. import deep_research_nodes as payload_codec
from ..research_core import ResearchArtifactPort, ResearchPorts

WORKFLOW_NAME = "deep_research"
WORKFLOW_VERSION = "v7"
STATE_SCHEMA_VERSION = 7
NODE_IDS = ("normalize", "plan", "search", "synth", "persist", "finalize")


def _values(state: WorkflowState) -> dict[str, JsonValue]:
    raw = state.get("values")
    return copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {}


def _merged(state: WorkflowState, changes: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    values = _values(state)
    values.update(copy.deepcopy(dict(changes)))
    validate_json_value(values)
    return values


def _blob_store(context: WorkflowContext) -> BlobStore | None:
    value = context.ports.get("blob")
    return value if isinstance(value, BlobStore) else None


def _encode(value: JsonValue, context: WorkflowContext) -> tuple[JsonValue, list[str]]:
    encoded = payload_codec.encode_large_value(value, blob_store=_blob_store(context))
    refs: list[str] = []
    if isinstance(encoded, dict) and payload_codec.BLOB_REF_KEY in encoded:
        raw = encoded[payload_codec.BLOB_REF_KEY]
        if isinstance(raw, Mapping):
            refs.append(str(raw["sha256"]))
    return encoded, refs


def _decode(value: JsonValue, context: WorkflowContext) -> JsonValue:
    return payload_codec.decode_large_value(value, blob_store=_blob_store(context))


def _citation(raw: Mapping[str, Any]) -> legacy.Citation:
    return legacy.Citation(
        n=int(raw["n"]),
        url=str(raw["url"]),
        title=str(raw["title"]),
        snippet=str(raw.get("snippet") or ""),
        fetched_at=float(raw.get("fetched_at") or 0.0),
        authority=float(raw.get("authority") or 1.0),
    )


def _report(raw: Mapping[str, Any]) -> legacy.ResearchReport:
    return legacy.ResearchReport(
        topic=str(raw.get("topic") or ""),
        summary=str(raw.get("summary") or ""),
        report_md=str(raw.get("report_md") or ""),
        citations=[_citation(value) for value in raw.get("citations", [])],
        sub_questions=[str(value) for value in raw.get("sub_questions", [])],
        coverage=dict(raw.get("coverage") or {}),
        errors=[str(value) for value in raw.get("errors", [])],
    )


async def normalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    values = _values(state)
    topic = str(values.get("topic") or "").strip()
    if not topic:
        raise ValueError("deepresearch topic is required")
    mode = str(values.get("mode") or "standard").strip().lower()
    if mode not in {"light", "standard", "deep"}:
        mode = "standard"
    raw_config = values.get("research_config")
    config = dict(raw_config) if isinstance(raw_config, Mapping) else {}
    max_questions = max(2, min(6, int(config.get("max_sub_questions", 4))))
    return StatePatch({
        "values": _merged(state, {
            "topic": topic,
            "mode": mode,
            "max_sub_questions": max_questions,
            "max_child_attempts": 2,
            "sub_questions": [],
            "business_status": "researching",
        })
    })


async def plan_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    topic = str(values["topic"])
    max_questions = int(values["max_sub_questions"])
    ports = ResearchPorts.from_workflow_context(context)
    try:
        raw = await ports.llm.complete(
            legacy._PLAN_PROMPT.format(topic=topic, user_request=topic)
        )
    except Exception as exc:  # noqa: BLE001 - bounded fallback keeps the run useful
        raw = ""
        errors = [f"plan_llm:{type(exc).__name__}"]
    else:
        errors = []
    questions = legacy.parse_sub_questions(raw, max_questions=max_questions)
    if len(questions) < 2:
        questions = [
            f"{topic} 的当前事实、背景和主要参与者是什么？",
            f"{topic} 的关键争议、风险、替代方案和未来趋势是什么？",
        ][:max_questions]
        errors.append("plan_fallback:two_complementary_directions")
    reporter = context.ports.get("progress")
    identity = context.identity
    publish = getattr(reporter, "report_deep_research_v7_children", None)
    if identity is not None and callable(publish):
        await publish(identity, [
            {
                "child_id": f"dr-{index}",
                "question": question,
                "status": "queued",
                "attempt": 0,
                "max_attempts": int(values.get("max_child_attempts") or 2),
                "n_sources": 0,
                "reason_code": "",
            }
            for index, question in enumerate(questions)
        ])
    return StatePatch({
        "values": _merged(state, {
            "sub_questions": questions,
            "plan_errors": errors,
        })
    })


async def search_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    ports = ResearchPorts.from_workflow_context(context)
    scheduler = context.ports.get("subagent_scheduler")
    identity = context.identity
    parent_id = identity.run_id if identity is not None else str(state.get("run_id") or "")
    reporter = context.ports.get("progress")
    publish = getattr(reporter, "report_deep_research_v7_children", None)

    async def _publish_children(children: list[dict[str, Any]]) -> None:
        if identity is not None and callable(publish):
            await publish(identity, children)

    collection = await legacy.collect_subagent_research(
        topic=str(values["topic"]),
        sub_questions=[str(value) for value in values.get("sub_questions", [])],
        llm_call=ports.llm.complete,
        search=ports.search.search_call,
        extract=ports.fetch.extractor or legacy.default_extract,
        scheduler=scheduler,
        parent_sid=parent_id,
        mode=str(values["mode"]),
        route={},
        max_attempts=int(values.get("max_child_attempts") or 2),
        simple_children=True,
        progress_callback=_publish_children,
    )
    payload: dict[str, JsonValue] = {
        "sub_reports": [
            {"question": question, "report": report.as_dict()}
            for question, report in collection.sub_reports
        ],
        "child_records": copy.deepcopy(collection.child_records),
        "errors": list(collection.errors),
        "route": copy.deepcopy(collection.route),
        "observation": collection.observation,
    }
    validate_json_value(payload)
    encoded, refs = _encode(payload, context)
    return StatePatch({
        "values": _merged(state, {"fanout_payload": encoded}),
        "blob_refs": refs,
    })


async def synth_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    raw_payload = _decode(values.get("fanout_payload", {}), context)
    if not isinstance(raw_payload, Mapping):
        raise ValueError("fanout payload is missing")
    sub_reports = [
        (str(item["question"]), _report(item["report"]))
        for item in raw_payload.get("sub_reports", [])
        if isinstance(item, Mapping) and isinstance(item.get("report"), Mapping)
    ]
    errors = [str(value) for value in raw_payload.get("errors", [])]
    topic = str(values["topic"])
    ports = ResearchPorts.from_workflow_context(context)
    if sub_reports:
        report_md, citations = await legacy._fanout_synthesize(
            topic, topic, sub_reports, ports.llm.complete, errors
        )
    else:
        citations = []
        report_md = legacy._no_results_template(
            topic, [str(value) for value in values.get("sub_questions", [])]
        )
    child_records = [
        dict(value) for value in raw_payload.get("child_records", [])
        if isinstance(value, Mapping)
    ]
    insufficient = [record for record in child_records if record.get("status") != "valid"]
    if insufficient:
        report_md = report_md.rstrip() + "\n\n## 调研局限\n\n" + "\n".join(
            f"- {record.get('question')}: 子代理在 {record.get('attempt', 0)} 次尝试后仍证据不足。"
            for record in insufficient
        ) + "\n"
    business_status = (
        "insufficient_evidence" if not sub_reports
        else "partial" if insufficient
        else "completed"
    )
    report = legacy.ResearchReport(
        topic=topic,
        summary=legacy._extract_summary(report_md),
        report_md=report_md,
        citations=citations,
        sub_questions=[str(value) for value in values.get("sub_questions", [])],
        coverage={
            "mode": "manager_fanout_v7",
            "n_sources": len(citations),
            "n_domains": len({legacy._host(c.url) for c in citations if legacy._host(c.url)}),
            "subagent_fanout": copy.deepcopy(raw_payload.get("observation", {})),
            "route": copy.deepcopy(raw_payload.get("route", {})),
            "business_status": business_status,
        },
        errors=errors,
    )
    encoded, refs = _encode(report.as_dict(), context)
    return StatePatch({
        "values": _merged(state, {
            "report_payload": encoded,
            "business_status": business_status,
        }),
        "blob_refs": refs,
    })


async def persist_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    raw_report = _decode(values.get("report_payload", {}), context)
    if not isinstance(raw_report, Mapping):
        raise ValueError("report payload is missing")
    report = _report(raw_report)
    run_id = context.identity.run_id if context.identity is not None else str(state.get("run_id") or "")
    artifact: dict[str, JsonValue] | None = None
    if report.citations and report.report_md:
        port = context.ports.get("artifact")
        if not isinstance(port, ResearchArtifactPort):
            raise RuntimeError("research artifact port is unavailable")
        report_hash = hashlib.sha256(report.report_md.encode("utf-8")).hexdigest()
        saved = await port.save(
            topic=report.topic,
            report_md=report.report_md,
            report_hash=report_hash,
            run_id=run_id,
        )
        artifact = {str(key): copy.deepcopy(value) for key, value in saved.items()}
        validate_json_value(artifact)
    return StatePatch({
        "values": _merged(state, {"artifact_payload": artifact})
    })


async def finalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    values = _values(state)
    raw_report = _decode(values.get("report_payload", {}), context)
    if not isinstance(raw_report, Mapping):
        raise ValueError("report payload is missing")
    report = _report(raw_report)
    run_id = context.identity.run_id if context.identity is not None else str(state.get("run_id") or "")
    status = str(values.get("business_status") or "insufficient_evidence")
    report_envelope: dict[str, JsonValue] = {
        "schema_version": 1,
        "status": status,
        "report": copy.deepcopy(dict(raw_report)),
    }
    intents: list[dict[str, JsonValue]] = []
    if status != "insufficient_evidence":
        artifact_payload = values.get("artifact_payload")
        artifacts = [copy.deepcopy(dict(artifact_payload))] if isinstance(artifact_payload, Mapping) else []
        intents.extend([
            {
                "intent_id": f"{run_id}:report",
                "kind": "report",
                "channel": "workflow_report",
                "payload": {"report": copy.deepcopy(report_envelope)},
            },
            {
                "intent_id": f"{run_id}:artifact",
                "kind": "artifact_card",
                "channel": "artifact",
                "payload": {
                    "artifact_type": "research_report",
                    "report": copy.deepcopy(report_envelope),
                    "artifact": copy.deepcopy(artifact_payload),
                    "artifacts": artifacts,
                    "preview": report.summary,
                },
            },
        ])
    intents.append({
        "intent_id": f"{run_id}:final",
        "kind": "final_assistant",
        "channel": "final_assistant",
        "payload": {
            "text": report.report_md or report.summary,
            "summary": report.summary,
            "business_status": status,
            "report": copy.deepcopy(report_envelope),
        },
    })
    validate_json_value(intents)
    return StatePatch({"values": _merged(state, {"delivery_intents": intents})})


DEEP_RESEARCH_V7_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=(
        NodeDefinition("normalize", normalize_handler),
        NodeDefinition("plan", plan_handler),
        NodeDefinition(
            "search",
            search_handler,
            retry_policy=RetryPolicy(
                max_attempts=2,
                retryable_codes=frozenset({"retryable_network", "retryable_provider"}),
            ),
        ),
        NodeDefinition("synth", synth_handler),
        NodeDefinition("persist", persist_handler),
        NodeDefinition("finalize", finalize_handler),
    ),
    channels={
        "values": ChannelSpec(JsonType.OBJECT, ReducerKind.SINGLE_WRITER, frozenset(NODE_IDS)),
        "blob_refs": ChannelSpec(JsonType.ARRAY, ReducerKind.STABLE_LIST, frozenset({"search", "synth"})),
    },
    edges=tuple(
        Edge(source, target)
        for source, target in zip(NODE_IDS, (*NODE_IDS[1:], END_NODE), strict=True)
    ),
    recursion_limit=24,
    max_supersteps=12,
    prompt_manifest={
        "manager_pattern": "agents_as_tools",
        "stages": ["decompose", "research_children", "synthesize", "deliver"],
        "child_result_statuses": ["valid", "retryable", "insufficient", "fatal"],
    },
    policy_manifest={
        "implementation": "deep-research-v7-manager-fanout-v1",
        "child_attempts": 2,
        "child_failure_isolation": True,
        "terminal_contract": "delivery-intents-only",
    },
)

DEEP_RESEARCH_V7 = compile_workflow(DEEP_RESEARCH_V7_DEFINITION)


def initial_state(
    *,
    topic: str,
    run_id: str,
    thread_id: str | None = None,
    session_id: str = "",
    mode: str = "standard",
    research_config: Mapping[str, JsonValue] | None = None,
    blob_root: str | None = None,
) -> WorkflowState:
    del blob_root
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
        },
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {},
        "budgets": {},
        "errors": [],
    }


__all__ = [
    "DEEP_RESEARCH_V7", "DEEP_RESEARCH_V7_DEFINITION", "NODE_IDS",
    "STATE_SCHEMA_VERSION", "WORKFLOW_NAME", "WORKFLOW_VERSION", "initial_state",
]
