"""Immutable fixed-slot DeepResearch v2 workflow definition."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ...contracts import (
    ChannelSpec,
    JsonType,
    JsonValue,
    ReducerKind,
    RetryPolicy,
    WorkflowState,
)
from ...definition import (
    END_NODE,
    Edge,
    NodeDefinition,
    NodeDispatch,
    WorkflowDefinition,
    compile_workflow,
)
from ..deep_research_v2_contracts import BRANCH_IDS, BRANCH_STAGES
from ..deep_research_v2_nodes import (
    PUBLIC_STAGE_IDS,
    cite_handler,
    finalize_handler,
    gap_handler,
    make_branch_handler,
    make_join_handler,
    normalize_handler,
    persist_handler,
    plan_handler,
    rerank_handler,
    synth_handler,
)

WORKFLOW_NAME = "deep_research"
WORKFLOW_VERSION = "v2"
STATE_SCHEMA_VERSION = 2

_PUBLIC_NODE_HANDLERS = {
    "normalize": normalize_handler,
    "plan": plan_handler,
    "gap": gap_handler,
    "rerank": rerank_handler,
    "synth": synth_handler,
    "cite": cite_handler,
    "persist": persist_handler,
    "finalize": finalize_handler,
}
_JOIN_NODE_IDS = tuple(f"{stage}_join" for stage in BRANCH_STAGES)
_PUBLIC_WRITERS = frozenset((*_PUBLIC_NODE_HANDLERS, *_JOIN_NODE_IDS))
_BRANCH_NODE_IDS = tuple(f"{stage}_{branch_id}" for stage in BRANCH_STAGES for branch_id in BRANCH_IDS)
_RETRY = RetryPolicy(
    max_attempts=2,
    initial_delay_seconds=0.1,
    max_delay_seconds=2.0,
    retryable_codes=frozenset({"retryable_provider", "retryable_tool"}),
)


def _nodes() -> tuple[NodeDefinition, ...]:
    result = [NodeDefinition(node_id, handler) for node_id, handler in _PUBLIC_NODE_HANDLERS.items()]
    for stage in BRANCH_STAGES:
        result.extend(
            NodeDefinition(
                f"{stage}_{branch_id}", make_branch_handler(stage, branch_id),
                retry_policy=_RETRY, dispatch=NodeDispatch.PARALLEL,
            )
            for branch_id in BRANCH_IDS
        )
        result.append(NodeDefinition(f"{stage}_join", make_join_handler(stage)))
    return tuple(result)


def _edges() -> tuple[Edge, ...]:
    result: list[Edge] = [Edge("normalize", "plan")]
    previous = "plan"
    for stage in BRANCH_STAGES:
        branch_nodes = tuple(f"{stage}_{branch_id}" for branch_id in BRANCH_IDS)
        result.extend(Edge(previous, node_id) for node_id in branch_nodes)
        result.append(Edge(branch_nodes, f"{stage}_join"))
        previous = f"{stage}_join"
    result.extend(
        (
            Edge(previous, "gap"), Edge("gap", "rerank"), Edge("rerank", "synth"),
            Edge("synth", "cite"), Edge("cite", "persist"), Edge("persist", "finalize"),
            Edge("finalize", END_NODE),
        )
    )
    return tuple(result)


def _channels() -> dict[str, ChannelSpec]:
    channels = {
        "values": ChannelSpec(JsonType.OBJECT, ReducerKind.SINGLE_WRITER, _PUBLIC_WRITERS),
        "branch_events": ChannelSpec(JsonType.ARRAY, ReducerKind.STABLE_LIST, frozenset(_BRANCH_NODE_IDS)),
        "blob_refs": ChannelSpec(
            JsonType.ARRAY, ReducerKind.STABLE_LIST,
            frozenset({"gap", *(f"fetch_{branch_id}" for branch_id in BRANCH_IDS)}),
        ),
    }
    for stage in BRANCH_STAGES:
        channels[f"branch_{stage}"] = ChannelSpec(
            JsonType.OBJECT, ReducerKind.DICT_DISJOINT,
            frozenset(f"{stage}_{branch_id}" for branch_id in BRANCH_IDS),
        )
    return channels


DEEP_RESEARCH_V2_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=_nodes(),
    channels=_channels(),
    edges=_edges(),
    recursion_limit=128,
    max_supersteps=64,
    prompt_manifest={"research_core": "v2", "branch_slots": list(BRANCH_IDS), "branch_stages": list(BRANCH_STAGES), "public_stages": list(PUBLIC_STAGE_IDS)},
    policy_manifest={
        "implementation": "deep-research-graph-v2.0.0",
        "fanout_strategy": "fixed-six-slot-five-phase",
        "completed_progress": "atomic-frontier-intents-v2",
        "claim_support": "deterministic-v1",
    },
)

DEEP_RESEARCH_V2 = compile_workflow(DEEP_RESEARCH_V2_DEFINITION)


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
    state: dict[str, JsonValue] = {
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
            "topic": topic, "mode": mode,
            "research_config": dict(research_config or {}),
            "blob_root": str(blob_root) if blob_root is not None else "",
        },
        "branch_events": [],
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {},
        "budgets": {},
        "errors": [],
    }
    state.update({f"branch_{stage}": {} for stage in BRANCH_STAGES})
    return state  # type: ignore[return-value]


__all__ = ["DEEP_RESEARCH_V2", "DEEP_RESEARCH_V2_DEFINITION", "STATE_SCHEMA_VERSION", "WORKFLOW_NAME", "WORKFLOW_VERSION", "initial_state"]
