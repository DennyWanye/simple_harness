"""Checkpointed DeepResearch v5 graph with adaptive gap and repair loops."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Mapping

from ...contracts import ChannelSpec, JsonType, JsonValue, ReducerKind, WorkflowState
from ...definition import END_NODE, ConditionalEdge, Edge, NodeDefinition, WorkflowDefinition, compile_workflow
from ..deep_research_v5_nodes import (
    direct_handler,
    expand_handler,
    fetch_handler,
    finalize_handler,
    gap_evaluate_handler,
    gap_evaluate_route,
    gap_join_handler,
    gap_join_route,
    gap_work_handler,
    insufficient_finalize_handler,
    model_handler,
    normalize_handler,
    persist_handler,
    plan_handler,
    quality_audit_handler,
    quality_route,
    repair_join_handler,
    repair_route,
    repair_work_handler,
    rerank_handler,
    rerank_route,
    score_handler,
    search_handler,
    synth_handler,
)
from ..deep_research_v5_contracts import ResearchEvidenceSnapshot
from ..deep_research_v5_evidence import POLICY_HASH as EVIDENCE_ADMISSION_POLICY_HASH
from ..deep_research_v5_report import REPORT_QUALITY_RUBRIC_V1_HASH


WORKFLOW_NAME = "deep_research"
WORKFLOW_VERSION = "v5"
STATE_SCHEMA_VERSION = 5
CONTRACT_SCHEMA_VERSION = 1

NODE_IDS = (
    "normalize", "model", "plan", "expand", "search", "direct", "fetch", "score",
    "gap_evaluate", "gap_work", "gap_join", "rerank", "synth", "quality_audit",
    "repair_work", "repair_join", "persist", "finalize", "insufficient_finalize",
)
PUBLIC_STAGE_IDS = (
    "normalize", "plan", "research", "gap", "rerank", "synth", "quality", "persist", "finalize",
)


_HANDLERS = {
    "normalize": normalize_handler,
    "model": model_handler,
    "plan": plan_handler,
    "expand": expand_handler,
    "search": search_handler,
    "direct": direct_handler,
    "fetch": fetch_handler,
    "score": score_handler,
    "gap_evaluate": gap_evaluate_handler,
    "gap_work": gap_work_handler,
    "gap_join": gap_join_handler,
    "rerank": rerank_handler,
    "synth": synth_handler,
    "quality_audit": quality_audit_handler,
    "repair_work": repair_work_handler,
    "repair_join": repair_join_handler,
    "persist": persist_handler,
    "finalize": finalize_handler,
    "insufficient_finalize": insufficient_finalize_handler,
}


def _model_route(state: WorkflowState, _context: object) -> str:
    values = state.get("values", {})
    return (
        "gap_evaluate"
        if isinstance(values, Mapping) and values.get("only_gaps") is True
        else "plan"
    )


DEEP_RESEARCH_V5_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=tuple(NodeDefinition(node_id, _HANDLERS[node_id]) for node_id in NODE_IDS),
    channels={
        "values": ChannelSpec(
            JsonType.OBJECT,
            ReducerKind.SINGLE_WRITER,
            frozenset(NODE_IDS),
        ),
        "loop_counters": ChannelSpec(
            JsonType.OBJECT,
            ReducerKind.SINGLE_WRITER,
            frozenset({"gap_join", "repair_join"}),
        ),
    },
    edges=(
        Edge("normalize", "model"),
        Edge("plan", "expand"),
        Edge("expand", "search"),
        Edge("search", "direct"),
        Edge("direct", "fetch"),
        Edge("fetch", "score"),
        Edge("score", "gap_evaluate"),
        Edge("gap_work", "gap_join"),
        Edge("synth", "quality_audit"),
        Edge("repair_work", "repair_join"),
        Edge("persist", "finalize"),
        Edge("finalize", END_NODE),
        Edge("insufficient_finalize", END_NODE),
    ),
    conditional_edges=(
        ConditionalEdge(
            "model",
            _model_route,
            {"plan": "plan", "gap_evaluate": "gap_evaluate"},
        ),
        ConditionalEdge(
            "gap_evaluate",
            gap_evaluate_route,
            {"gap_work": "gap_work", "synth": "rerank"},
        ),
        ConditionalEdge(
            "gap_join",
            gap_join_route,
            {"gap_evaluate": "gap_evaluate", "synth": "rerank"},
        ),
        ConditionalEdge(
            "rerank",
            rerank_route,
            {"synth": "synth", "insufficient_finalize": "insufficient_finalize"},
        ),
        ConditionalEdge(
            "quality_audit",
            quality_route,
            {
                "repair_work": "repair_work",
                "persist": "persist",
                "insufficient_finalize": "insufficient_finalize",
            },
        ),
        ConditionalEdge(
            "repair_join",
            repair_route,
            {
                "quality_audit": "quality_audit",
                "persist": "persist",
                "insufficient_finalize": "insufficient_finalize",
            },
        ),
    ),
    recursion_limit=512,
    max_supersteps=384,
    loop_budgets={"gap_work_iterations": 64, "repair_iterations": 16},
    loop_budget_bindings={"gap_join": "gap_work_iterations", "repair_join": "repair_iterations"},
    prompt_manifest={
        "research_core": "v5",
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "public_stages": list(PUBLIC_STAGE_IDS),
        "profiles": ["generic_research", "policy_education", "technology_intelligence"],
        "llm_roles": [
            "modeling",
            "query_strategy",
            "dimension_analysis",
            "report_synthesis",
            "quality_audit",
            "targeted_repair",
        ],
    },
    policy_manifest={
        "implementation": "deep-research-graph-v5.1.0",
        "quality_protocol": "dimension-source-family-dual-terminal-v1",
        "evidence_admission_policy_hash": EVIDENCE_ADMISSION_POLICY_HASH,
        "report_quality_rubric_hash": REPORT_QUALITY_RUBRIC_V1_HASH,
        "time_ledger": "wall-clock-anchor-plus-active-duration-v1",
        "adaptive_loop": {
            "soft_checkpoint_seconds": 300,
            "lease_seconds": 120,
            "automatic_cap_seconds": 900,
            "plateau_rounds": 2,
            "gap_work_limit": 64,
            "repair_limit": 16,
        },
        "control_fence": "context-control-port-generate-now-cancel-settle-v1",
        "external_ports": ["llm", "search", "fetch", "artifact", "control", "clock"],
        "delivery_statuses": ["completed", "partial", "insufficient_evidence"],
        "engine_terminal_statuses": ["completed", "error", "cancelled"],
    },
)

DEEP_RESEARCH_V5 = compile_workflow(DEEP_RESEARCH_V5_DEFINITION)


def initial_state(
    *,
    topic: str,
    run_id: str,
    thread_id: str | None = None,
    session_id: str = "",
    mode: str = "standard",
    research_config: Mapping[str, JsonValue] | None = None,
    blob_root: str | Path | None = None,
    operation_id: str | None = None,
    continuation_snapshot: Mapping[str, JsonValue] | None = None,
    parent_operation_id: str | None = None,
    only_gaps: bool = False,
) -> WorkflowState:
    if not topic.strip() or not run_id.strip():
        raise ValueError("topic and run_id are required")
    snapshot = (
        ResearchEvidenceSnapshot.from_json(continuation_snapshot)
        if continuation_snapshot is not None
        else None
    )
    if only_gaps and snapshot is None:
        raise ValueError("only_gaps continuation requires a canonical snapshot")
    state: WorkflowState = {
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
            "research_config": copy.deepcopy(dict(research_config or {})),
            "blob_root": str(blob_root) if blob_root is not None else "",
            "contract_schema_version": CONTRACT_SCHEMA_VERSION,
            "operation_id": operation_id or run_id,
            "parent_operation_id": parent_operation_id,
            "continuation": snapshot is not None,
            "only_gaps": bool(only_gaps),
            "continuation_snapshot_hash": snapshot.snapshot_hash if snapshot else None,
            "stage": "pending",
            "research_brief": None,
            "dimension_coverages": [],
            "initial_queries": [],
            "executed_query_fingerprints": [],
            "executed_source_family_ids": [],
            "loop_policy": None,
            "loop_decision": None,
            "control_command": None,
            "active_gap_work": None,
            "active_gap_fingerprint": None,
            "active_gap_mode": None,
            "gap_work_result": None,
            "committed_gap_work_ids": [],
            "query_strategy_result": None,
            "query_strategy_queries": [],
            "query_strategy_called": False,
            "query_strategy_work_item_id": None,
            "active_repair_id": None,
            "repair_result": None,
            "committed_repair_ids": [],
            "synthesis_route": None,
            "delivery_status": None,
            "delivery_decision": None,
            "terminal_public": None,
            "terminal_status": None,
        },
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {
            "gap_work_iterations": 0,
            "repair_iterations": 0,
        },
        "budgets": {
            "gap_work_iterations": 64,
            "repair_iterations": 16,
        },
        "errors": [],
    }
    if snapshot is not None:
        values = state["values"]
        assert isinstance(values, dict)
        values["dimension_coverages"] = [
            item.to_json() for item in snapshot.dimension_coverages
        ]
        values["passage_blob_refs"] = list(snapshot.passage_blob_refs)
        values["source_families"] = [item.to_json() for item in snapshot.source_families]
        values["executed_source_family_ids"] = [
            item.family_id for item in snapshot.source_families
        ]
        values["executed_query_fingerprints"] = list(snapshot.query_fingerprints)
        values["budget_summary"] = copy.deepcopy(snapshot.budget_summary)
    return state


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "DEEP_RESEARCH_V5",
    "DEEP_RESEARCH_V5_DEFINITION",
    "NODE_IDS",
    "PUBLIC_STAGE_IDS",
    "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "initial_state",
]
