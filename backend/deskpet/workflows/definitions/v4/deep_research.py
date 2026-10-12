"""Immutable DeepResearch v4 graph with honest conditional terminal paths."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Mapping

from ...contracts import ChannelSpec, JsonType, JsonValue, ReducerKind, RetryPolicy, WorkflowState
from ...definition import (
    END_NODE,
    ConditionalEdge,
    Edge,
    NodeDefinition,
    NodeDispatch,
    WorkflowDefinition,
    compile_workflow,
)
from ..deep_research_v3_contracts import BRANCH_IDS, BRANCH_STAGES
from ..deep_research_v4_nodes import (
    PUBLIC_STAGE_IDS,
    cite_handler,
    finalize_handler,
    gap_handler,
    insufficient_evidence_finalize,
    make_branch_handler,
    make_join_handler,
    no_results_finalize,
    normalize_handler,
    persist_handler,
    plan_handler,
    post_cite_route,
    post_direct_route,
    research_continue_handler,
    rerank_handler,
    synth_handler,
)
from ..v3.deep_research import initial_state as v3_initial_state


WORKFLOW_NAME = "deep_research"
WORKFLOW_VERSION = "v4"
STATE_SCHEMA_VERSION = 4

_PUBLIC_NODE_HANDLERS = {
    "normalize": normalize_handler,
    "plan": plan_handler,
    "gap": gap_handler,
    "rerank": rerank_handler,
    "synth": synth_handler,
    "cite": cite_handler,
    "persist": persist_handler,
    "finalize": finalize_handler,
    "no_results_finalize": no_results_finalize,
    "insufficient_evidence_finalize": insufficient_evidence_finalize,
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
    result.append(NodeDefinition("research", research_continue_handler))
    for stage in BRANCH_STAGES:
        result.extend(
            NodeDefinition(
                f"{stage}_{branch_id}",
                make_branch_handler(stage, branch_id),
                retry_policy=_RETRY,
                dispatch=NodeDispatch.PARALLEL,
            )
            for branch_id in BRANCH_IDS
        )
        result.append(NodeDefinition(f"{stage}_join", make_join_handler(stage)))
    return tuple(result)


def _edges() -> tuple[Edge, ...]:
    result: list[Edge] = [Edge("normalize", "plan")]
    previous = "plan"
    for stage in ("expand", "search", "direct"):
        branches = tuple(f"{stage}_{branch_id}" for branch_id in BRANCH_IDS)
        result.extend(Edge(previous, node_id) for node_id in branches)
        result.append(Edge(branches, f"{stage}_join"))
        previous = f"{stage}_join"
    result.extend(Edge("research", f"fetch_{branch_id}") for branch_id in BRANCH_IDS)
    fetch_branches = tuple(f"fetch_{branch_id}" for branch_id in BRANCH_IDS)
    result.append(Edge(fetch_branches, "fetch_join"))
    result.extend(Edge("fetch_join", f"score_{branch_id}") for branch_id in BRANCH_IDS)
    score_branches = tuple(f"score_{branch_id}" for branch_id in BRANCH_IDS)
    result.extend(
        (
            Edge(score_branches, "score_join"),
            Edge("score_join", "gap"),
            Edge("gap", "rerank"),
            Edge("rerank", "synth"),
            Edge("synth", "cite"),
            Edge("persist", "finalize"),
            Edge("finalize", END_NODE),
            Edge("no_results_finalize", END_NODE),
            Edge("insufficient_evidence_finalize", END_NODE),
        )
    )
    return tuple(result)


def _channels() -> dict[str, ChannelSpec]:
    channels = {
        "values": ChannelSpec(JsonType.OBJECT, ReducerKind.SINGLE_WRITER, _PUBLIC_WRITERS),
        "branch_events": ChannelSpec(JsonType.ARRAY, ReducerKind.STABLE_LIST, frozenset(_BRANCH_NODE_IDS)),
        "blob_refs": ChannelSpec(
            JsonType.ARRAY,
            ReducerKind.STABLE_LIST,
            frozenset({"gap", *(f"fetch_{branch_id}" for branch_id in BRANCH_IDS)}),
        ),
    }
    for stage in BRANCH_STAGES:
        channels[f"branch_{stage}"] = ChannelSpec(
            JsonType.OBJECT,
            ReducerKind.DICT_DISJOINT,
            frozenset(f"{stage}_{branch_id}" for branch_id in BRANCH_IDS),
        )
    return channels


DEEP_RESEARCH_V4_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=_nodes(),
    channels=_channels(),
    edges=_edges(),
    conditional_edges=(
        ConditionalEdge(
            "direct_join",
            post_direct_route,
            {"research": "research", "no_results": "no_results_finalize"},
        ),
        ConditionalEdge(
            "cite",
            post_cite_route,
            {"success": "persist", "insufficient_evidence": "insufficient_evidence_finalize"},
        ),
    ),
    recursion_limit=128,
    max_supersteps=64,
    prompt_manifest={
        "research_core": "v4",
        "branch_slots": list(BRANCH_IDS),
        "branch_stages": list(BRANCH_STAGES),
        "public_stages": list(PUBLIC_STAGE_IDS),
        "intent_profiles": ["generic", "technology_intelligence"],
    },
    policy_manifest={
        "implementation": "deep-research-graph-v4.0.0",
        "v3_adapter": "public-handlers-only",
        "failure_delivery": "terminal-public-no-artifact-v1",
        "technology_ranking": "evidence-only-deterministic-v1",
    },
)

DEEP_RESEARCH_V4 = compile_workflow(DEEP_RESEARCH_V4_DEFINITION)


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
    state = copy.deepcopy(
        v3_initial_state(
            topic=topic,
            run_id=run_id,
            thread_id=thread_id,
            session_id=session_id,
            mode=mode,
            research_config=research_config,
            blob_root=blob_root,
        )
    )
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["workflow_version"] = WORKFLOW_VERSION
    return state


__all__ = [
    "DEEP_RESEARCH_V4", "DEEP_RESEARCH_V4_DEFINITION", "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME", "WORKFLOW_VERSION", "initial_state",
]
