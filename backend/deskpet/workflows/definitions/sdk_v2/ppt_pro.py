# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Detached PPT v2 graph with a pure durable decision barrier."""

from __future__ import annotations

from simple_harness.contracts import JsonValue
from simple_harness.workflow import (
    ChannelSpec,
    ConditionalEdge,
    Edge,
    JsonType,
    NodeDefinition,
    ReducerKind,
    RetryPolicy,
    WorkflowDefinition,
)

from deskpet.sdk_adapters.product_workflows import ppt_nodes as nodes

WORKFLOW_NAME = "ppt_pro"
WORKFLOW_VERSION = "v2"
STATE_SCHEMA_VERSION = 2
NODE_IDS = (
    "normalize",
    "research_plan",
    "research_expand",
    "research_search",
    "research_direct",
    "research_fetch",
    "research_score",
    "research_gap",
    "research_rerank",
    "research_synth",
    "research_cite",
    "outline",
    "outline_ready",
    "wait_outline_decision",
    "apply_outline_decision",
    "revise_outline",
    "preflight",
    "image_probe",
    "prepare_slides",
    "image_map",
    "render",
    "preview",
    "visual_evaluate",
    "visual_revise",
    "publish",
    "terminal",
)

_RETRY = RetryPolicy(
    max_attempts=2,
    retryable_codes=frozenset({"provider_unavailable", "transport_error"}),
)


PPT_PRO_V2_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=(
        NodeDefinition("normalize", nodes.normalize_handler),
        NodeDefinition("research_plan", nodes.research_plan_handler),
        NodeDefinition("research_expand", nodes.research_expand_handler),
        NodeDefinition("research_search", nodes.research_search_handler, retry_policy=_RETRY),
        NodeDefinition("research_direct", nodes.research_direct_handler, retry_policy=_RETRY),
        NodeDefinition("research_fetch", nodes.research_fetch_handler, retry_policy=_RETRY),
        NodeDefinition("research_score", nodes.research_score_handler),
        NodeDefinition("research_gap", nodes.research_gap_handler),
        NodeDefinition("research_rerank", nodes.research_rerank_handler),
        NodeDefinition("research_synth", nodes.research_synth_handler, retry_policy=_RETRY),
        NodeDefinition("research_cite", nodes.research_cite_handler),
        NodeDefinition("outline", nodes.outline_handler, retry_policy=_RETRY),
        NodeDefinition("outline_ready", nodes.outline_ready_handler),
        NodeDefinition(
            "wait_outline_decision",
            nodes.wait_outline_decision_handler,
            interrupt_capable=True,
            barrier=True,
            exclusive_superstep=True,
            pre_interrupt_effect_policy="pure",
        ),
        NodeDefinition("apply_outline_decision", nodes.apply_outline_decision_handler),
        NodeDefinition("revise_outline", nodes.revise_outline_handler, retry_policy=_RETRY),
        NodeDefinition("preflight", nodes.preflight_handler),
        NodeDefinition("image_probe", nodes.image_probe_handler, retry_policy=_RETRY),
        NodeDefinition("prepare_slides", nodes.prepare_slides_handler),
        NodeDefinition("image_map", nodes.image_map_handler, retry_policy=_RETRY),
        NodeDefinition("render", nodes.render_handler, retry_policy=_RETRY),
        NodeDefinition("preview", nodes.preview_handler, retry_policy=_RETRY),
        NodeDefinition("visual_evaluate", nodes.visual_evaluate_handler, retry_policy=_RETRY),
        NodeDefinition("visual_revise", nodes.visual_revise_handler, retry_policy=_RETRY),
        NodeDefinition("publish", nodes.publish_handler, retry_policy=_RETRY),
        NodeDefinition("terminal", nodes.terminal_handler),
    ),
    channels={
        "values": ChannelSpec(
            JsonType.OBJECT,
            ReducerKind.SINGLE_WRITER,
            allowed_writers=frozenset(NODE_IDS),
        ),
        "loop_counters": ChannelSpec(
            JsonType.OBJECT,
            ReducerKind.SINGLE_WRITER,
            allowed_writers=frozenset(
                {"normalize", "research_gap", "revise_outline", "image_map", "visual_revise"}
            ),
        ),
        "budgets": ChannelSpec(
            JsonType.OBJECT,
            ReducerKind.SINGLE_WRITER,
            allowed_writers=frozenset({"normalize"}),
        ),
        "artifact_refs": ChannelSpec(
            JsonType.ARRAY,
            ReducerKind.SINGLE_WRITER,
            allowed_writers=frozenset({"publish"}),
        ),
        "receipt_refs": ChannelSpec(
            JsonType.ARRAY,
            ReducerKind.SINGLE_WRITER,
            allowed_writers=frozenset({"terminal"}),
        ),
    },
    edges=(
        Edge("normalize", "research_plan"),
        Edge("research_plan", "research_expand"),
        Edge("research_expand", "research_search"),
        Edge("research_search", "research_direct"),
        Edge("research_direct", "research_fetch"),
        Edge("research_fetch", "research_score"),
        Edge("research_score", "research_gap"),
        Edge("research_rerank", "research_synth"),
        Edge("research_synth", "research_cite"),
        Edge("research_cite", "outline"),
        Edge("outline", "outline_ready"),
        Edge("revise_outline", "outline_ready"),
        Edge("outline_ready", "wait_outline_decision"),
        Edge("wait_outline_decision", "apply_outline_decision"),
        Edge("image_probe", "prepare_slides"),
        Edge("preview", "visual_evaluate"),
        Edge("visual_revise", "image_map"),
        Edge("publish", "terminal"),
    ),
    conditional_edges=(
        ConditionalEdge(
            "research_gap", nodes.research_gap_route,
            {"continue": "research_gap", "done": "research_rerank", "outline": "outline"}, "pure"
        ),
        ConditionalEdge(
            "apply_outline_decision",
            nodes.outline_decision_route,
            {"preflight": "preflight", "revise": "revise_outline", "terminal": "terminal"},
            "pure",
        ),
        ConditionalEdge("preflight", nodes.preflight_route, {"probe": "image_probe", "terminal": "terminal"}, "pure"),
        ConditionalEdge("prepare_slides", nodes.prepare_slides_route, {"images": "image_map", "terminal": "terminal"}, "pure"),
        ConditionalEdge("image_map", nodes.image_map_route, {"pending": "image_map", "done": "render", "terminal": "terminal"}, "pure"),
        ConditionalEdge("render", nodes.render_route, {"preview": "preview", "terminal": "terminal"}, "pure"),
        ConditionalEdge("visual_evaluate", nodes.visual_route, {"revise": "visual_revise", "publish": "publish", "terminal": "terminal"}, "pure"),
    ),
    recursion_limit=256,
    max_supersteps=192,
    loop_budgets={
        "gap_iterations": 2,
        "outline_revisions": 2,
        "visual_revisions": 2,
        "image_iterations": 80,
    },
    loop_budget_bindings={
        "research_gap->research_gap": "gap_iterations",
        "apply_outline_decision->revise_outline": "outline_revisions",
        "visual_evaluate->visual_revise": "visual_revisions",
        "image_map->image_map": "image_iterations",
    },
    durability="sync",
    policy_manifest={
        "authority": "simple_harness.workflow",
        "outline_barrier": "pure",
        "decision_effect_node": "apply_outline_decision",
    },
)


def ppt_pro_initial_state(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return the closed start-state projection for a new presentation run."""

    return {"values": dict(payload)}


__all__ = (
    "NODE_IDS",
    "PPT_PRO_V2_DEFINITION",
    "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "ppt_pro_initial_state",
)
