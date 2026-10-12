"""Version 1 durable, domain-neutral multi-step task workflow."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from ...contracts import JsonValue, WorkflowState
from ...definition import CompiledWorkflow, compile_workflow
from ..code_nodes import (
    MAX_DURABLE_PROPOSAL_TURNS,
    MAX_FIX_ROUNDS,
    CapabilitySnapshotV1,
    TaskSessionRefV1,
)
from .code_task import CODE_COMPLEX_V1_DEFINITION
from .code_task import initial_state as _legacy_initial_state

WORKFLOW_NAME = "durable_task"
WORKFLOW_VERSION = "v1"
DURABLE_TASK_MAX_SUPERSTEPS = 192
DURABLE_TASK_RECURSION_LIMIT = 256

DURABLE_TASK_V1_DEFINITION = replace(
    CODE_COMPLEX_V1_DEFINITION,
    name=WORKFLOW_NAME,
    recursion_limit=DURABLE_TASK_RECURSION_LIMIT,
    max_supersteps=DURABLE_TASK_MAX_SUPERSTEPS,
    prompt_manifest={
        **dict(CODE_COMPLEX_V1_DEFINITION.prompt_manifest),
        "profile": "workflow.durable_task",
        "legacy_source": "code_complex@v1",
    },
    policy_manifest={
        **dict(CODE_COMPLEX_V1_DEFINITION.policy_manifest),
        "implementation": "durable-task-graph-v1.0.0",
        "product_mode": "none",
        "workspace_owner": "task_work_context",
        "proposal_budget": MAX_DURABLE_PROPOSAL_TURNS,
        "max_supersteps": DURABLE_TASK_MAX_SUPERSTEPS,
    },
    loop_budgets={
        **dict(CODE_COMPLEX_V1_DEFINITION.loop_budgets),
        "proposal_turns": MAX_DURABLE_PROPOSAL_TURNS,
        "fix_rounds": MAX_FIX_ROUNDS,
    },
)

DURABLE_TASK_V1: CompiledWorkflow = compile_workflow(DURABLE_TASK_V1_DEFINITION)


def initial_state(
    *,
    request: str,
    run_id: str,
    session_ref: TaskSessionRefV1 | Mapping[str, object],
    capability_snapshot: Sequence[
        CapabilitySnapshotV1 | Mapping[str, object]
    ],
    thread_id: str | None = None,
    session_id: str = "",
    messages: Sequence[Mapping[str, JsonValue]] | None = None,
    plan_steps: Sequence[str | Mapping[str, JsonValue]] = (),
    clarification_required: bool = False,
    clarification_question: str = "",
    approval_required: bool = True,
    proposal_budget: int = 40,
    fix_budget: int = 3,
    started_at: float = 0.0,
    request_id: str = "",
    turn_id: str = "",
    provider_snapshot: Mapping[str, JsonValue] | None = None,
    model_snapshot: Mapping[str, JsonValue] | None = None,
    output_contract: Mapping[str, JsonValue] | None = None,
) -> WorkflowState:
    state = _legacy_initial_state(
        request=request,
        run_id=run_id,
        session_ref=session_ref,
        capability_snapshot=capability_snapshot,
        thread_id=thread_id,
        session_id=session_id,
        messages=messages,
        plan_steps=plan_steps,
        clarification_required=clarification_required,
        clarification_question=clarification_question,
        approval_required=approval_required,
        proposal_budget=proposal_budget,
        fix_budget=fix_budget,
        started_at=started_at,
        request_id=request_id,
        turn_id=turn_id,
        provider_snapshot=provider_snapshot,
        model_snapshot=model_snapshot,
        output_contract=output_contract,
    )
    state["workflow_name"] = WORKFLOW_NAME
    return state


__all__ = [
    "DURABLE_TASK_V1",
    "DURABLE_TASK_V1_DEFINITION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "initial_state",
]
