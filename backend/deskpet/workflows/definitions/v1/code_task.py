# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Version 1 durable Complex Code workflow graph."""

from __future__ import annotations

from typing import Mapping, Sequence

from ...contracts import ChannelSpec, JsonType, JsonValue, ReducerKind, RetryPolicy, WorkflowState
from ...definition import (
    END_NODE,
    CompiledWorkflow,
    ConditionalEdge,
    Edge,
    NodeDefinition,
    WorkflowDefinition,
    compile_workflow,
)
from .. import code_nodes
from ..code_nodes import (
    CapabilitySnapshotV1,
    TaskSessionRefV1,
    WorkflowSessionRefV1,
)


WORKFLOW_NAME = "code_complex"
WORKFLOW_VERSION = "v1"
STATE_SCHEMA_VERSION = 1
RECURSION_LIMIT = 128
MAX_SUPERSTEPS = 96

_VALUE_WRITERS = frozenset(
    {
        "intake",
        "clarify",
        "plan",
        "wait_approval",
        "llm_proposal",
        "tool_execution",
        "completion_decision",
        "test",
        "audit",
        "finalize",
    }
)


def _single(value_type: JsonType, writers: frozenset[str]) -> ChannelSpec:
    return ChannelSpec(
        value_type=value_type,
        reducer=ReducerKind.SINGLE_WRITER,
        allowed_writers=writers,
    )


CODE_COMPLEX_V1_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="intake",
    nodes=(
        NodeDefinition("intake", code_nodes.intake_handler),
        NodeDefinition(
            "clarify",
            code_nodes.clarify_handler,
            interrupt_capable=True,
            barrier=True,
            exclusive_superstep=True,
        ),
        NodeDefinition("plan", code_nodes.plan_handler),
        NodeDefinition(
            "wait_approval",
            code_nodes.wait_approval_handler,
            interrupt_capable=True,
            barrier=True,
            exclusive_superstep=True,
        ),
        NodeDefinition(
            "llm_proposal",
            code_nodes.llm_proposal_handler,
            retry_policy=RetryPolicy(
                max_attempts=3,
                retryable_codes=frozenset({"retryable_provider"}),
            ),
        ),
        NodeDefinition(
            "tool_execution",
            code_nodes.tool_execution_handler,
            retry_policy=RetryPolicy(
                max_attempts=2,
                retryable_codes=frozenset({"retryable_network", "retryable_provider"}),
            ),
            interrupt_capable=True,
            barrier=True,
            exclusive_superstep=True,
        ),
        NodeDefinition("completion_decision", code_nodes.completion_decision_handler),
        NodeDefinition("test", code_nodes.test_handler),
        NodeDefinition("audit", code_nodes.audit_handler),
        NodeDefinition("finalize", code_nodes.finalize_handler),
    ),
    channels={
        "values": _single(JsonType.OBJECT, _VALUE_WRITERS),
        "loop_counters": _single(
            JsonType.OBJECT, frozenset({"intake", "llm_proposal", "audit"})
        ),
        "budgets": _single(JsonType.OBJECT, frozenset({"intake"})),
    },
    edges=(
        Edge("intake", "clarify"),
        Edge("clarify", "plan"),
        Edge("plan", "wait_approval"),
        Edge("llm_proposal", "tool_execution"),
        Edge("tool_execution", "completion_decision"),
        Edge("test", "audit"),
        Edge("finalize", END_NODE),
    ),
    conditional_edges=(
        ConditionalEdge(
            "wait_approval",
            code_nodes.approval_route,
            {"approved": "llm_proposal", "finalize": "finalize"},
        ),
        ConditionalEdge(
            "completion_decision",
            code_nodes.completion_route,
            {
                "loop": "llm_proposal",
                "test": "test",
                "audit": "audit",
                "finalize": "finalize",
            },
        ),
        ConditionalEdge(
            "audit",
            code_nodes.audit_route,
            {"fix": "llm_proposal", "finalize": "finalize"},
        ),
    ),
    recursion_limit=RECURSION_LIMIT,
    max_supersteps=MAX_SUPERSTEPS,
    loop_budgets={
        "proposal_turns": code_nodes.MAX_PROPOSAL_TURNS,
        "fix_rounds": code_nodes.MAX_FIX_ROUNDS,
    },
    loop_budget_bindings={
        "completion_decision->llm_proposal": "proposal_turns",
        "audit->llm_proposal": "fix_rounds",
    },
    prompt_manifest={
        "proposal_contract": "ProposalStateV1->ProposalOutcomeV1",
        "nodes": [
            "intake",
            "clarify",
            "plan",
            "wait_approval",
            "llm_proposal",
            "tool_execution",
            "completion_decision",
            "test",
            "audit",
            "finalize",
        ],
    },
    policy_manifest={
        "implementation": "complex-code-graph-v1.0.0",
        "proposal_budget": code_nodes.MAX_PROPOSAL_TURNS,
        "fix_budget": code_nodes.MAX_FIX_ROUNDS,
        "proposal_effect_boundary": "separate-sync-checkpoints",
        "dynamic_tool_policy": "durable-hitl-allow-once-opaque-or-skip-or-cancel",
        "excluded_tools": sorted(code_nodes.FORBIDDEN_DURABLE_TOOLS),
        "todo_projection": "graph-owned-stable-workflow-step-id",
        "terminal_contract": "delivery-intents-only",
    },
)

CODE_COMPLEX_V1: CompiledWorkflow = compile_workflow(CODE_COMPLEX_V1_DEFINITION)


def initial_state(
    *,
    request: str,
    run_id: str,
    session_ref: WorkflowSessionRefV1 | TaskSessionRefV1 | Mapping[str, object],
    capability_snapshot: Sequence[CapabilitySnapshotV1 | Mapping[str, object]],
    thread_id: str | None = None,
    session_id: str = "",
    messages: Sequence[Mapping[str, JsonValue]] | None = None,
    plan_steps: Sequence[str | Mapping[str, JsonValue]] = (),
    clarification_required: bool = False,
    clarification_question: str = "",
    approval_required: bool = True,
    proposal_budget: int = code_nodes.MAX_PROPOSAL_TURNS,
    fix_budget: int = code_nodes.MAX_FIX_ROUNDS,
    started_at: float = 0.0,
    request_id: str = "",
    turn_id: str = "",
    provider_snapshot: Mapping[str, JsonValue] | None = None,
    model_snapshot: Mapping[str, JsonValue] | None = None,
    output_contract: Mapping[str, JsonValue] | None = None,
) -> WorkflowState:
    """Build the strict JSON input envelope expected by the v1 graph."""

    if isinstance(session_ref, (WorkflowSessionRefV1, TaskSessionRefV1)):
        resolved_session = session_ref
    elif "task_scope_id" in session_ref:
        resolved_session = TaskSessionRefV1.from_dict(session_ref)
    else:
        resolved_session = WorkflowSessionRefV1.from_dict(session_ref)
    resolved_capabilities = [
        item if isinstance(item, CapabilitySnapshotV1) else CapabilitySnapshotV1.from_dict(item)
        for item in capability_snapshot
    ]
    values: dict[str, JsonValue] = {
        "request": request,
        "messages": [dict(value) for value in (messages or [{"role": "user", "content": request}])],
        "plan_steps": [dict(value) if isinstance(value, Mapping) else str(value) for value in plan_steps],
        "clarification_required": clarification_required,
        "clarification_question": clarification_question,
        "approval_required": approval_required,
        "proposal_budget": proposal_budget,
        "fix_budget": fix_budget,
        "started_at": started_at,
        "request_id": request_id,
        "turn_id": turn_id,
        "session_ref": resolved_session.to_dict(),
        "capability_snapshot": [item.to_dict() for item in resolved_capabilities],
        "provider_snapshot": dict(provider_snapshot or {}),
        "model_snapshot": dict(model_snapshot or {}),
        "output_contract": dict(output_contract or {}),
    }
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
        "values": values,
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {"proposal_turns": 0, "fix_rounds": 0},
        "budgets": {
            "proposal_turns": min(code_nodes.MAX_PROPOSAL_TURNS, max(1, proposal_budget)),
            "fix_rounds": min(code_nodes.MAX_FIX_ROUNDS, max(0, fix_budget)),
        },
        "errors": [],
    }


__all__ = [
    "CODE_COMPLEX_V1",
    "CODE_COMPLEX_V1_DEFINITION",
    "MAX_SUPERSTEPS",
    "RECURSION_LIMIT",
    "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "initial_state",
]
