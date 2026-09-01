# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Detached DeepResearch v7-sdk1 definition.

The definition deliberately depends on the SDK workflow surface and the
product's typed ports only.  Historic DeskPet workflow engines and definition
packages are not part of this module's import graph.
"""

from __future__ import annotations

from simple_harness.contracts import JsonValue
from simple_harness.workflow import (
    ChannelSpec,
    Edge,
    JsonType,
    NodeDefinition,
    ReducerKind,
    RetryPolicy,
    WorkflowDefinition,
)

from deskpet.sdk_adapters.product_workflows import research_stages as stages

WORKFLOW_NAME = "deep_research"
WORKFLOW_VERSION = "v7-sdk1"
STATE_SCHEMA_VERSION = 8
NODE_IDS = ("normalize", "plan", "search", "synth", "persist", "finalize")

_RETRY = RetryPolicy(
    max_attempts=2,
    retryable_codes=frozenset({"provider_unavailable", "transport_error"}),
)


DEEP_RESEARCH_V7_SDK1_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="normalize",
    nodes=(
        NodeDefinition("normalize", stages.normalize_handler),
        NodeDefinition("plan", stages.plan_handler, retry_policy=_RETRY),
        NodeDefinition("search", stages.search_handler, retry_policy=_RETRY),
        NodeDefinition("synth", stages.synth_handler, retry_policy=_RETRY),
        NodeDefinition("persist", stages.persist_handler, retry_policy=_RETRY),
        NodeDefinition("finalize", stages.finalize_handler),
    ),
    channels={
        "values": ChannelSpec(
            JsonType.OBJECT,
            ReducerKind.SINGLE_WRITER,
            allowed_writers=frozenset(NODE_IDS),
        )
    },
    edges=(
        Edge("normalize", "plan"),
        Edge("plan", "search"),
        Edge("search", "synth"),
        Edge("synth", "persist"),
        Edge("persist", "finalize"),
    ),
    recursion_limit=33,
    max_supersteps=32,
    durability="sync",
    policy_manifest={
        "authority": "simple_harness.workflow",
        "physical_effects": "typed_product_ports",
        "terminal_delivery": "delivery_intents",
    },
)


def deep_research_initial_state(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Return the closed start-state projection for a new research run."""

    return {"values": dict(payload)}


__all__ = (
    "DEEP_RESEARCH_V7_SDK1_DEFINITION",
    "NODE_IDS",
    "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "deep_research_initial_state",
)
