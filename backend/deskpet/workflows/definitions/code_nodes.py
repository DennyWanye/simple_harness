# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable node handlers for the Complex Code v1 workflow."""

from __future__ import annotations

import copy
import hashlib
import inspect
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import httpx

from ...execution.provider_invocations import (
    ProviderDispatchNotSentError,
    ProviderDispatchUnknownError,
)
from ..contracts import JsonValue, StatePatch, WorkflowContext, WorkflowState, canonical_json, validate_json_value
from ..control import workflow_interrupt
from ..effects import PreparedToolCall
from ..errors import WorkflowErrorCode, WorkflowNodeError
from ..proposal_state import (
    ConvergenceStateV1,
    GateConfigV1,
    GateStateV1,
    ProposalOutcomeV1,
    ProposalStateV1,
)


MAX_PROPOSAL_TURNS = 20
MAX_DURABLE_PROPOSAL_TURNS = 40
MAX_FIX_ROUNDS = 3
FORBIDDEN_DURABLE_TOOLS = frozenset(
    {"spawn_subagents", "await_subagents", "spawn_team", "todo_write"}
)
DYNAMIC_SOURCES = frozenset({"plugin", "mcp"})
_EXPLICIT_TOOL_FREE_REQUEST = re.compile(
    r"(?:"
    r"只(?:需|要)?(?:回复|回答)|仅(?:回复|回答)|直接(?:回复|回答)|"
    r"不(?:要|需)?调用(?:任何)?(?:写)?工具|无需(?:调用)?工具|"
    r"\b(?:only|just)\s+(?:reply|respond|answer)\b|"
    r"\b(?:do\s+not|don't|without)\s+(?:(?:call|use|using)\s+)?(?:any\s+)?tools?\b"
    r")",
    re.IGNORECASE,
)
_APPROVAL_STEP = re.compile(
    r"(?:等待|等候).*(?:批准|确认)|(?:批准|确认).*(?:后|再)|"
    r"\bwait(?:ing)?\s+for\s+(?:approval|confirmation)\b|"
    r"\b(?:approve|approval|confirmation)\s+gate\b",
    re.IGNORECASE,
)
_TEST_EXECUTION_REQUEST = re.compile(
    r"(?:"
    r"\bpytest\b|\brun\s+(?:the\s+)?tests?\b|\b(?:launch|run|verify|validate)\b|"
    r"运行.{0,12}测试|执行.{0,12}测试|启动|运行|验证|确认"
    r")",
    re.IGNORECASE,
)
_EXPLICIT_TEST_EXECUTION_REQUEST = re.compile(
    r"(?:\bpytest\b|\brun\s+(?:the\s+)?tests?\b|"
    r"运行.{0,12}测试|执行.{0,12}测试)",
    re.IGNORECASE,
)
_WRITE_EXECUTION_REQUEST = re.compile(
    r"(?:"
    r"\b(?:copy|create|fix|repair|modify|update|write|edit|implement)\b|"
    r"复制|创建|生成|修复|修改|改写|实现"
    r")",
    re.IGNORECASE,
)
_NEGATED_WRITE_EXECUTION_REQUEST = re.compile(
    r"(?:"
    r"(?:不要|不需要|无需|不用|禁止|严禁|别|不).{0,16}"
    r"(?:复制|创建|生成|修复|修改|改写|实现|改动|写入|删除)|"
    r"\b(?:do\s+not|don't|without|must\s+not|never)\b.{0,24}"
    r"\b(?:copy|creat\w*|fix\w*|repair\w*|modif\w*|updat\w*|"
    r"writ\w*|edit\w*|implement\w*|chang\w*|delet\w*)\b"
    r")",
    re.IGNORECASE,
)
_WRITE_TOOL_NAMES = frozenset(
    {
        "apply_patch",
        "desktop_create_file",
        "edit_file",
        "file_write",
        "run_command",
        "run_shell",
        "write_file",
    }
)
_TEST_TOOL_NAMES = frozenset(
    {
        "godot_run",
        "godot_run_project",
        "godot_validate_project",
        "run_command",
        "run_shell",
    }
)
_DISCOVERY_ONLY_TOOL_NAMES = frozenset(
    {
        "capability_search",
        "list_directory",
        "memory_read",
        "memory_search",
        "read_file",
        "tool_describe",
        "tool_search",
        "workspace_prepare",
    }
)
_REPEATABLE_DISCOVERY_SEARCH_TOOL_NAMES = frozenset(
    {"capability_search", "tool_search"}
)
_MAX_CONSECUTIVE_DISCOVERY_SEARCHES = 3
_MAX_CONSECUTIVE_FAILED_DESCRIBES = 2
_PROVIDER_BALANCE_FAILURE = re.compile(
    r"(?:\bstatus(?:_code)?[=:\s]+402\b|"
    r"\bhttp/\S+\s+402\b|"
    r"\"(?:status|code)\"\s*:\s*402\b|"
    r"insufficient[_ -]?balance|余额不足|费用不足)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CapabilitySnapshotV1:
    tool_name: str
    source: str
    schema_hash: str
    spec_version: str
    effect_policy: Mapping[str, JsonValue] | None
    lifecycle_hash: str | None
    outcome_parser_hash: str | None

    def __post_init__(self) -> None:
        if not self.tool_name or not self.source or not self.schema_hash or not self.spec_version:
            raise ValueError("capability snapshot identity is required")
        if self.effect_policy is not None:
            policy = copy.deepcopy(dict(self.effect_policy))
            validate_json_value(policy)
            object.__setattr__(self, "effect_policy", policy)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tool_name": self.tool_name,
            "source": self.source,
            "schema_hash": self.schema_hash,
            "spec_version": self.spec_version,
            "effect_policy": dict(self.effect_policy) if self.effect_policy is not None else None,
            "lifecycle_hash": self.lifecycle_hash,
            "outcome_parser_hash": self.outcome_parser_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CapabilitySnapshotV1":
        raw_policy = value.get("effect_policy")
        return cls(
            tool_name=str(value["tool_name"]),
            source=str(value.get("source", "builtin")),
            schema_hash=str(value["schema_hash"]),
            spec_version=str(value["spec_version"]),
            effect_policy=dict(raw_policy) if isinstance(raw_policy, Mapping) else None,
            lifecycle_hash=(str(value["lifecycle_hash"]) if value.get("lifecycle_hash") is not None else None),
            outcome_parser_hash=(str(value["outcome_parser_hash"]) if value.get("outcome_parser_hash") is not None else None),
        )


@dataclass(frozen=True, slots=True)
class WorkflowSessionRefV1:
    base_session_id: str
    code_session_id: str
    delivery_session_id: str
    project_root_hash: str
    base_epoch: int
    code_epoch: int

    def __post_init__(self) -> None:
        if not all((self.base_session_id, self.code_session_id, self.delivery_session_id, self.project_root_hash)):
            raise ValueError("Complex Code session references are required")
        if self.base_epoch < 0 or self.code_epoch < 0:
            raise ValueError("session epochs must not be negative")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "base_session_id": self.base_session_id,
            "code_session_id": self.code_session_id,
            "delivery_session_id": self.delivery_session_id,
            "project_root_hash": self.project_root_hash,
            "base_epoch": self.base_epoch,
            "code_epoch": self.code_epoch,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "WorkflowSessionRefV1":
        return cls(
            base_session_id=str(value["base_session_id"]),
            code_session_id=str(value["code_session_id"]),
            delivery_session_id=str(value["delivery_session_id"]),
            project_root_hash=str(value["project_root_hash"]),
            base_epoch=int(value.get("base_epoch", 0)),
            code_epoch=int(value.get("code_epoch", 0)),
        )


@dataclass(frozen=True, slots=True)
class TaskSessionRefV1:
    """Session identity for new single-session durable task runs."""

    session_id: str
    task_scope_id: str
    delivery_session_id: str
    workspace_hash: str
    session_epoch: int
    task_epoch: int

    def __post_init__(self) -> None:
        if not all(
            (
                self.session_id,
                self.task_scope_id,
                self.delivery_session_id,
                self.workspace_hash,
            )
        ):
            raise ValueError("durable task session references are required")
        if self.session_epoch < 0 or self.task_epoch < 0:
            raise ValueError("session epochs must not be negative")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "session_id": self.session_id,
            "task_scope_id": self.task_scope_id,
            "delivery_session_id": self.delivery_session_id,
            "workspace_hash": self.workspace_hash,
            "session_epoch": self.session_epoch,
            "task_epoch": self.task_epoch,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TaskSessionRefV1":
        return cls(
            session_id=str(value["session_id"]),
            task_scope_id=str(value["task_scope_id"]),
            delivery_session_id=str(value["delivery_session_id"]),
            workspace_hash=str(value["workspace_hash"]),
            session_epoch=int(value.get("session_epoch", 0)),
            task_epoch=int(value.get("task_epoch", 0)),
        )


def _input_value(state: Mapping[str, object], name: str, default: object) -> object:
    values = state.get("values")
    if isinstance(values, Mapping) and name in values:
        return values[name]
    return state.get(name, default)


def _effective(state: Mapping[str, object]) -> dict[str, object]:
    effective = dict(state)
    values = state.get("values")
    if isinstance(values, Mapping):
        effective.update(values)
    return effective


def _merged_values(state: Mapping[str, object], updates: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    current = state.get("values")
    merged = copy.deepcopy(dict(current)) if isinstance(current, Mapping) else {}
    merged.update(copy.deepcopy(dict(updates)))
    validate_json_value(merged)
    return merged


def _stable_id(*parts: object, prefix: str) -> str:
    payload: list[JsonValue] = [str(part) for part in parts]
    digest = hashlib.sha256(canonical_json(payload).encode("ascii")).hexdigest()
    return f"{prefix}-{digest[:24]}"


async def _call(value: object, *args: object, method: str | None = None, **kwargs: object) -> object:
    target = getattr(value, method) if method and hasattr(value, method) else value
    if not callable(target):
        raise TypeError(f"workflow port does not provide callable {method or 'entrypoint'}")
    result = target(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _provider_failure_is_retryable(exc: BaseException) -> bool:
    """Classify transport failures without ever retrying an explicit 402."""

    current: BaseException | None = exc
    seen: set[int] = set()
    retryable = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            if current.response.status_code == 402:
                return False
        if _PROVIDER_BALANCE_FAILURE.search(str(current)):
            return False
        if isinstance(
            current,
            (
                ProviderDispatchNotSentError,
                ProviderDispatchUnknownError,
                httpx.TransportError,
            ),
        ):
            retryable = True
        current = current.__cause__ or current.__context__
    return retryable


def _provider_failure_message_ref(exc: BaseException) -> str:
    """Reduce a provider exception chain to a safe, user-renderable code."""

    safe_runtime_codes = {
        "tool_activation_revision_conflict",
        "tool_activation_scope_conflict",
    }
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        error_class = str(getattr(current, "error_class", "") or "")
        status_code = getattr(current, "status_code", None)
        if error_class == "insufficient_balance" or status_code == 402:
            return "provider:insufficient_balance"
        if error_class in {"relay_key_invalid", "empty_api_key"}:
            return f"provider:{error_class}"
        runtime_code = str(current).strip()
        if runtime_code in safe_runtime_codes:
            return f"workflow_node:llm_proposal:{runtime_code}"
        current = current.__cause__ or current.__context__
    return "workflow_node:llm_proposal:provider_failure"


async def _clock_now(context: WorkflowContext) -> float:
    clock = context.ports.get("clock")
    if clock is None:
        return 0.0
    if hasattr(clock, "time"):
        return float(await _call(clock, method="time"))
    return float(await _call(clock))


def _interrupt(payload: Mapping[str, JsonValue]) -> JsonValue:
    return workflow_interrupt(copy.deepcopy(dict(payload)))


def _proposal_state(effective: Mapping[str, object]) -> ProposalStateV1:
    raw = effective.get("proposal_state")
    if not isinstance(raw, Mapping):
        raise ValueError("proposal_state is missing from the code workflow checkpoint")
    return ProposalStateV1.from_dict(raw)


def _outcome(effective: Mapping[str, object]) -> ProposalOutcomeV1:
    raw = effective.get("proposal_outcome")
    if not isinstance(raw, Mapping):
        raise ValueError("proposal_outcome is missing from the code workflow checkpoint")
    return ProposalOutcomeV1.from_dict(raw)


def _capabilities(effective: Mapping[str, object]) -> dict[str, CapabilitySnapshotV1]:
    raw = effective.get("capability_snapshot", [])
    if not isinstance(raw, list):
        raise ValueError("capability_snapshot must be an array")
    capabilities = [CapabilitySnapshotV1.from_dict(item) for item in raw]
    if len({item.tool_name for item in capabilities}) != len(capabilities):
        raise ValueError("capability_snapshot contains duplicate tool names")
    return {item.tool_name: item for item in capabilities}


def _session_ref(
    effective: Mapping[str, object],
) -> WorkflowSessionRefV1 | TaskSessionRefV1:
    raw = effective.get("session_ref")
    if not isinstance(raw, Mapping):
        raise ValueError("session_ref is required for durable workflows")
    if "task_scope_id" in raw:
        return TaskSessionRefV1.from_dict(raw)
    return WorkflowSessionRefV1.from_dict(raw)


def _todo_items(effective: Mapping[str, object]) -> list[dict[str, JsonValue]]:
    raw = effective.get("todos", [])
    if not isinstance(raw, list):
        raise ValueError("todos must be an array")
    return [copy.deepcopy(dict(item)) for item in raw]


def _todo_intents(run_id: str, todos: Sequence[Mapping[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    return [
        {
            "intent_id": f"{run_id}:todo:{item['workflow_step_id']}",
            "kind": "todo_upsert",
            "workflow_run_id": run_id,
            "workflow_step_id": str(item["workflow_step_id"]),
            "payload": copy.deepcopy(dict(item)),
        }
        for item in todos
    ]


async def intake_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    session_ref = _session_ref(effective)
    capabilities = list(_capabilities(effective).values())
    request = str(effective.get("request", "")).strip()
    if not request:
        raise ValueError("Complex Code request must not be empty")
    started_at = float(effective.get("started_at", 0.0) or 0.0)
    if started_at <= 0:
        started_at = await _clock_now(context)
    max_proposal_turns = (
        MAX_DURABLE_PROPOSAL_TURNS
        if str(effective.get("workflow_name") or "") == "durable_task"
        else MAX_PROPOSAL_TURNS
    )
    proposal_budget = min(
        max_proposal_turns,
        max(
            1,
            int(effective.get("proposal_budget", max_proposal_turns)),
        ),
    )
    fix_budget = min(MAX_FIX_ROUNDS, max(0, int(effective.get("fix_budget", MAX_FIX_ROUNDS))))
    messages = effective.get("messages")
    if not isinstance(messages, list):
        messages = [{"role": "user", "content": request}]
    proposal_state = ProposalStateV1(
        messages=messages,
        original_request=request,
        request_id=str(context.request_id or effective.get("request_id", "")),
        turn_id=str(context.turn_id or effective.get("turn_id", "")),
        system_prompt_ref=(str(effective["system_prompt_ref"]) if effective.get("system_prompt_ref") else None),
        prompt_ref=(str(effective["prompt_ref"]) if effective.get("prompt_ref") else None),
        skill_refs=list(effective.get("skill_refs", [])),
        compaction_summary=(str(effective["compaction_summary"]) if effective.get("compaction_summary") else None),
        compaction_ref=(str(effective["compaction_ref"]) if effective.get("compaction_ref") else None),
        token_estimate=int(effective.get("token_estimate", 0)),
        iteration=0,
        proposal_turns_used=0,
        fix_rounds_used=0,
        tools_used=0,
        active_plan_id=None,
        active_step_id=None,
        active_todo_ids=[],
        tool_signature_repeat_window=[],
        completion_attempts=0,
        verify_attempts=0,
        self_check_attempts=0,
        completion_outcomes=[],
        verify_outcomes=[],
        self_check_outcomes=[],
        evidence_refs=[],
        provider_snapshot=dict(effective.get("provider_snapshot", {})),
        model_snapshot=dict(effective.get("model_snapshot", {})),
        fallback_attempts=[],
        last_error=None,
        pending_tool_results={},
        committed_tool_results={},
        gate_config=GateConfigV1(max_turns=proposal_budget),
        gate_state=GateStateV1(started_at=started_at),
        convergence=ConvergenceStateV1(),
    )
    return StatePatch(
        {
            "values": {
                **copy.deepcopy(dict(state.get("values", {}))),
                "request": request,
                "session_ref": session_ref.to_dict(),
                "capability_snapshot": [item.to_dict() for item in capabilities],
                "proposal_state": proposal_state.to_dict(),
                "phase": "execution",
                "workflow_status": "running",
            },
            "loop_counters": {"proposal_turns": 0, "fix_rounds": 0},
            "budgets": {"proposal_turns": proposal_budget, "fix_rounds": fix_budget},
        }
    )


async def clarify_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    if not bool(effective.get("clarification_required", False)):
        return StatePatch({"values": _merged_values(state, {"clarification_status": "not_required"})})
    response = effective.get("clarification_response")
    if response is None:
        response = _interrupt(
            {
                "kind": "clarification",
                "question": str(effective.get("clarification_question", "Please clarify the requested code change.")),
                "options": ["continue", "cancel"],
            }
        )
    if isinstance(response, Mapping):
        if str(response.get("action", "continue")) == "cancel":
            return StatePatch({"values": _merged_values(state, {"workflow_status": "cancelled", "clarification_status": "cancelled"})})
        answer = str(response.get("answer", ""))
    else:
        answer = str(response)
    proposal = _proposal_state(effective).to_dict()
    messages = list(proposal["messages"])
    messages.append({"role": "user", "content": answer, "message_id": "clarification-response"})
    proposal["messages"] = messages
    return StatePatch({"values": _merged_values(state, {"clarification_response": answer, "clarification_status": "resolved", "proposal_state": proposal})})


async def plan_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    if effective.get("workflow_status") == "cancelled":
        return StatePatch({"values": _merged_values(state, {"plan": {}, "todos": [], "todo_intents": []})})
    run_id = str(state.get("run_id", ""))
    request = str(effective.get("request", ""))
    raw_steps = effective.get("plan_steps", [])
    if not isinstance(raw_steps, list) or not raw_steps:
        raw_steps = [request]
    steps: list[dict[str, JsonValue]] = []
    for index, raw in enumerate(raw_steps):
        title = str(raw.get("title", "")) if isinstance(raw, Mapping) else str(raw)
        step_id = _stable_id(run_id, index, title, prefix="step")
        steps.append({"workflow_step_id": step_id, "index": index, "title": title, "status": "pending"})
    plan_id = _stable_id(run_id, request, prefix="plan")
    proposal = _proposal_state(effective).to_dict()
    proposal["active_plan_id"] = plan_id
    proposal["active_step_id"] = str(steps[0]["workflow_step_id"])
    proposal["active_todo_ids"] = [str(item["workflow_step_id"]) for item in steps]
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "plan": {"plan_id": plan_id, "steps": steps},
                    "todos": steps,
                    "todo_intents": _todo_intents(run_id, steps),
                    "proposal_state": proposal,
                },
            )
        }
    )


async def wait_approval_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    del context
    effective = _effective(state)
    if effective.get("workflow_status") == "cancelled":
        return StatePatch({"values": _merged_values(state, {"approval_status": "cancelled"})})
    if not bool(effective.get("approval_required", True)):
        decision: object = {"approved": True}
    else:
        decision = effective.get("approval_response")
        if decision is None:
            decision = _interrupt(
                {
                    "kind": "plan_approval",
                    "plan": copy.deepcopy(effective.get("plan", {})),
                    "options": ["approve", "revise", "cancel"],
                }
            )
    approved = bool(decision.get("approved", False)) if isinstance(decision, Mapping) else bool(decision)
    action = str(decision.get("action", "approve" if approved else "cancel")) if isinstance(decision, Mapping) else ("approve" if approved else "cancel")
    if approved or action == "approve":
        status = "approved"
        workflow_status = "running"
    elif action == "revise":
        status = "revision_required"
        workflow_status = "blocked"
    else:
        status = "cancelled"
        workflow_status = "cancelled"
    updates: dict[str, JsonValue] = {
        "approval_response": copy.deepcopy(decision),
        "approval_status": status,
        "workflow_status": workflow_status,
    }
    if status == "approved":
        proposal = _proposal_state(effective).to_dict()
        messages = list(proposal["messages"])
        messages.append(
            {
                "role": "system",
                "content": (
                    "The user approved the displayed plan. Continue executing the "
                    "remaining steps now; do not ask for plan approval again. Tool-level "
                    "permission prompts, if any, are handled separately by the host."
                ),
                "message_id": "workflow-plan-approved",
            }
        )
        if str(effective.get("workflow_name") or "") == "durable_task":
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Use the proposal-turn budget for execution, not repeated "
                        "discovery. Keep each discovery query short and specific. "
                        "After one useful search result, copy its exact capability_id "
                        "and immediately follow the returned lifecycle (describe, "
                        "activate, then call); do not repeat semantically equivalent "
                        "searches. Treat tool results as authoritative. Once relevant "
                        "inputs or files are located, perform the requested action and "
                        "then a real verification, reserving at least one turn for each. "
                        "Discovery and workspace-setup receipts do not prove task "
                        "completion."
                    ),
                    "message_id": "durable-task-execution-discipline",
                }
            )
        proposal["messages"] = messages
        todos = _todo_items(effective)
        for item in todos:
            if _APPROVAL_STEP.search(str(item.get("title", ""))):
                item["status"] = "completed"
        updates.update(
            {
                "proposal_state": proposal,
                "todos": todos,
                "todo_intents": _todo_intents(str(state.get("run_id", "")), todos),
            }
        )
    return StatePatch({"values": _merged_values(state, updates)})


async def approval_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "approved" if _effective(state).get("approval_status") == "approved" else "finalize"


def _proposal_raw_by_call(outcome: ProposalOutcomeV1) -> dict[str, Mapping[str, JsonValue]]:
    result: dict[str, Mapping[str, JsonValue]] = {}
    for raw in outcome.raw_tool_proposals:
        call_id = str(raw.get("stable_call_id") or raw.get("call_id") or "")
        if call_id:
            result[call_id] = raw
    return result


def _dynamic_review(
    outcome: ProposalOutcomeV1,
    capabilities: Mapping[str, CapabilitySnapshotV1],
) -> list[dict[str, JsonValue]]:
    raw_by_call = _proposal_raw_by_call(outcome)
    reviews: list[dict[str, JsonValue]] = []
    for call in outcome.prepared_calls:
        raw = raw_by_call.get(call.stable_call_id, {})
        capability = capabilities.get(call.tool_name)
        source = str(raw.get("source") or (capability.source if capability else ""))
        dynamic = source in DYNAMIC_SOURCES or call.tool_name.startswith(("plugin:", "mcp:"))
        if not dynamic:
            continue
        access = str(raw.get("access", "write" if call.prepared_targets else "read"))
        policy_kind = ""
        if capability is not None and capability.effect_policy is not None:
            policy_kind = str(capability.effect_policy.get("kind", ""))
        safe_read = access == "read" and policy_kind == "idempotent_read" and capability is not None and capability.outcome_parser_hash is not None
        safe_write = access == "write" and capability is not None and capability.lifecycle_hash is not None and capability.outcome_parser_hash is not None and bool(policy_kind)
        if not (safe_read or safe_write):
            reviews.append(
                {
                    "stable_call_id": call.stable_call_id,
                    "tool_name": call.tool_name,
                    "source": source or "unknown_dynamic",
                    "access": access,
                    "reason": "unsupported_dynamic_tool",
                }
            )
    return reviews


async def llm_proposal_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    proposal_state = _proposal_state(effective)
    budgets = state.get("budgets", {})
    proposal_budget = int(budgets.get("proposal_turns", MAX_PROPOSAL_TURNS)) if isinstance(budgets, Mapping) else MAX_PROPOSAL_TURNS
    if proposal_state.proposal_turns_used >= proposal_budget:
        return StatePatch({"values": _merged_values(state, {"workflow_status": "blocked", "blocked_reason": "proposal_budget_exhausted"})})
    try:
        proposal_port = context.port("llm")
        if context.identity is not None and hasattr(
            proposal_port, "propose_for_execution"
        ):
            outcome_raw = await _call(
                proposal_port,
                proposal_state,
                method="propose_for_execution",
                execution_identity=context.identity,
            )
        else:
            outcome_raw = await _call(
                proposal_port, proposal_state, method="propose"
            )
    except Exception as exc:
        if not _provider_failure_is_retryable(exc):
            raise WorkflowNodeError(
                code=WorkflowErrorCode.PERMANENT,
                message_ref=_provider_failure_message_ref(exc),
                node_id="llm_proposal",
            ) from exc
        raise WorkflowNodeError(
            code=WorkflowErrorCode.RETRYABLE_PROVIDER,
            message_ref="workflow_node:llm_proposal:retryable_provider",
            node_id="llm_proposal",
        ) from exc
    if not isinstance(outcome_raw, ProposalOutcomeV1):
        if not isinstance(outcome_raw, Mapping):
            raise TypeError("propose port must return ProposalOutcomeV1 or its JSON form")
        outcome = ProposalOutcomeV1.from_dict(outcome_raw)
    else:
        outcome = outcome_raw
    proposal = proposal_state.to_dict()
    if outcome.compacted_messages is not None:
        proposal["messages"] = [
            dict(message) for message in outcome.compacted_messages
        ]
        proposal["compaction_summary"] = outcome.compaction_summary
        proposal["compaction_ref"] = outcome.compaction_ref
    proposal["token_estimate"] = outcome.token_estimate
    proposal["iteration"] = proposal_state.iteration + 1
    proposal["proposal_turns_used"] = proposal_state.proposal_turns_used + 1
    proposal["fix_rounds_used"] = proposal_state.fix_rounds_used
    proposal["gate_state"] = {
        **proposal_state.gate_state.to_dict(),
        "turns_used": proposal_state.gate_state.turns_used + 1,
        "last_transition": "llm_proposal",
    }
    proposal["last_error"] = outcome.error.to_dict() if outcome.error else None
    capabilities = _capabilities(effective)
    raw_by_call = _proposal_raw_by_call(outcome)
    forbidden_ids = [call.stable_call_id for call in outcome.prepared_calls if call.tool_name in FORBIDDEN_DURABLE_TOOLS]
    unsupported_ids = [
        call.stable_call_id
        for call in outcome.prepared_calls
        if (
            call.tool_name not in capabilities
            and str(
                raw_by_call.get(call.stable_call_id, {}).get(
                    "capability_admission", ""
                )
            )
            != "context_os_activated"
            and not call.tool_name.startswith(("plugin:", "mcp:"))
        )
    ]
    rejected = {
        call_id: {
            "stable_call_id": call_id,
            "status": "failed",
            "code": "unsupported_in_durable_workflow",
        }
        for call_id in forbidden_ids + unsupported_ids
    }
    proposal["committed_tool_results"] = {**dict(proposal["committed_tool_results"]), **rejected}
    review = _dynamic_review(outcome, capabilities)
    counters = {
        "proposal_turns": int(proposal["proposal_turns_used"]),
        "fix_rounds": int(proposal["fix_rounds_used"]),
    }
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "proposal_state": proposal,
                    "proposal_outcome": outcome.to_dict(),
                    "dispatch_call_ids": [
                        call.stable_call_id
                        for call in outcome.prepared_calls
                        if call.stable_call_id not in rejected
                    ],
                    "dynamic_tool_review": review,
                },
            ),
            "loop_counters": counters,
        }
    )


def _result_success(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    return (
        bool(value.get("ok", False))
        or value.get("state") == "success"
        or value.get("status") in {"success", "completed", "committed"}
        or value.get("domain_status") == "success"
    )


def _execution_obligations(request: str) -> tuple[bool, bool]:
    """Return concrete write/test obligations without promoting prohibitions.

    Durable tasks frequently contain authority boundaries such as ``禁止修改``
    or ``without editing files``.  Those words describe operations the agent
    must *not* perform, so treating their action token as a required receipt
    makes a correct read-only answer loop until its proposal budget or model
    context is exhausted.  Remove only the matched negative write clauses,
    then keep the existing fail-closed checks for any positive action that
    remains.  A request with no positive write action only keeps an explicit
    test obligation.  This prevents generic reporting words such as ``确认``
    from becoming a shell/test obligation without consulting the retired
    semantic ingress router from a new durable task.
    """

    positive_request = _NEGATED_WRITE_EXECUTION_REQUEST.sub(" ", request)
    requires_write = bool(_WRITE_EXECUTION_REQUEST.search(positive_request))
    requires_test = bool(_TEST_EXECUTION_REQUEST.search(positive_request))
    if not requires_write and not _EXPLICIT_TEST_EXECUTION_REQUEST.search(
        positive_request
    ):
        requires_test = False
    return requires_write, requires_test


def _tool_free_read_only_completed(
    outcome: ProposalOutcomeV1,
    proposal_state: ProposalStateV1,
) -> bool:
    """Accept a terminal answer as completion for a genuinely read-only step.

    Complex Code is sometimes selected explicitly by the Code entry even for
    response-only requests.  Such a step has no tool result that can advance
    its todo, so the old graph looped until the proposal budget was exhausted.
    Keep write tasks fail-closed: a bare ``end_turn`` is sufficient only when
    deterministic routing classifies the original request as read-only, or the
    request explicitly forbids tool use.
    """

    if (
        outcome.stop_reason != "end_turn"
        or outcome.prepared_calls
        or outcome.error is not None
        or not outcome.assistant_content.strip()
    ):
        return False
    from ..routing import WorkflowRoute, route_task

    request = proposal_state.original_request.strip()
    routed = route_task(request, workspace_context=True)
    return (
        routed.route is WorkflowRoute.REACT
        or bool(_EXPLICIT_TOOL_FREE_REQUEST.search(request))
    )


def _tool_free_evidence_backed_completed(
    outcome: ProposalOutcomeV1,
    proposal_state: ProposalStateV1,
) -> bool:
    """Accept a final answer only after requested code effects have receipts.

    A code plan commonly ends with conditional cleanup and reporting steps that
    need no additional tool call.  Those steps may collapse into one terminal
    answer, but only when the durable tool journal proves that every explicitly
    requested mutation/test capability actually ran successfully.
    """

    if (
        outcome.stop_reason != "end_turn"
        or outcome.prepared_calls
        or outcome.error is not None
        or not outcome.assistant_content.strip()
    ):
        return False
    successful_tools = {
        str(result.get("tool_name", ""))
        for result in proposal_state.committed_tool_results.values()
        if _result_success(result)
    }
    requires_write, requires_test = _execution_obligations(
        proposal_state.original_request
    )
    if requires_write and not (
        successful_tools & _WRITE_TOOL_NAMES
    ):
        return False
    if requires_test and not (
        successful_tools & _TEST_TOOL_NAMES
    ):
        return False
    return bool(successful_tools)


def _successful_receipts(
    proposal_state: ProposalStateV1,
) -> list[Mapping[str, JsonValue]]:
    return [
        result
        for result in proposal_state.committed_tool_results.values()
        if isinstance(result, Mapping) and _result_success(result)
    ]


def _consecutive_discovery_searches(
    proposal_state: ProposalStateV1,
) -> int:
    count = 0
    for result in reversed(list(proposal_state.committed_tool_results.values())):
        if not isinstance(result, Mapping):
            break
        if str(result.get("tool_name", "")) not in (
            _REPEATABLE_DISCOVERY_SEARCH_TOOL_NAMES
        ):
            break
        count += 1
    return count


def _consecutive_failed_describes(
    proposal_state: ProposalStateV1,
) -> int:
    count = 0
    for result in reversed(list(proposal_state.committed_tool_results.values())):
        if not isinstance(result, Mapping):
            break
        if str(result.get("tool_name", "")) != "tool_describe":
            break
        if _result_success(result):
            break
        count += 1
    return count


def _drop_superseded_discovery_search_messages(
    messages: Sequence[Mapping[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    """Keep only the newest search result in the model conversation.

    Capability search payloads are authoritative but can be large. Repeating
    them verbatim grows a durable workflow prompt quadratically and can make a
    local provider reject the next proposal before transport starts. Exact
    describe/activate history is preserved because activation replay depends
    on it.
    """

    removed_call_ids: set[str] = set()
    compacted: list[dict[str, JsonValue]] = []
    for message in messages:
        copied = copy.deepcopy(dict(message))
        if str(copied.get("role") or "") == "assistant":
            raw_calls = copied.get("tool_calls")
            if isinstance(raw_calls, list) and raw_calls:
                names: set[str] = set()
                call_ids: set[str] = set()
                for raw_call in raw_calls:
                    if not isinstance(raw_call, Mapping):
                        names.add("")
                        continue
                    function = raw_call.get("function")
                    names.add(
                        str(function.get("name") or "")
                        if isinstance(function, Mapping)
                        else ""
                    )
                    call_ids.add(str(raw_call.get("id") or ""))
                if names and names.issubset(
                    _REPEATABLE_DISCOVERY_SEARCH_TOOL_NAMES
                ):
                    removed_call_ids.update(call_ids)
                    continue
        if (
            str(copied.get("role") or "") == "tool"
            and str(copied.get("tool_call_id") or "") in removed_call_ids
        ):
            continue
        compacted.append(copied)
    return compacted


def _durable_task_evidence_satisfied(
    proposal_state: ProposalStateV1,
) -> bool:
    """Require receipts that match an effectful durable task's own request.

    Discovery and workspace setup calls are useful progress, but they cannot
    prove that a requested mutation or verification happened.  When one
    objective asks for both change and verification, require two distinct
    successful action receipts so a single setup/copy command cannot satisfy
    both obligations.
    """

    request = proposal_state.original_request.strip()
    requires_write, requires_test = _execution_obligations(
        request
    )
    successful = _successful_receipts(proposal_state)
    if not successful:
        return (
            not requires_write
            and not requires_test
            and bool(_EXPLICIT_TOOL_FREE_REQUEST.search(request))
        )
    if not requires_write and not requires_test:
        return True

    action_receipts = [
        result
        for result in successful
        if str(result.get("tool_name", "")) not in _DISCOVERY_ONLY_TOOL_NAMES
    ]
    write_receipts = [
        result
        for result in action_receipts
        if str(result.get("tool_name", "")) in _WRITE_TOOL_NAMES
    ]
    test_receipts = [
        result
        for result in action_receipts
        if str(result.get("tool_name", "")) in _TEST_TOOL_NAMES
    ]
    if requires_write and not write_receipts:
        return False
    if requires_test and not test_receipts:
        return False
    if requires_write and requires_test:
        return any(
            str(write.get("stable_call_id", ""))
            and str(test.get("stable_call_id", ""))
            and str(write["stable_call_id"]) != str(test["stable_call_id"])
            for write in write_receipts
            for test in test_receipts
        )
    return bool(action_receipts)


def _tool_free_receipt_backed_completed(
    outcome: ProposalOutcomeV1,
    proposal_state: ProposalStateV1,
) -> bool:
    """Let the model close a new durable task only after a successful receipt.

    This is the single-session production rule.  It deliberately avoids
    guessing task semantics from the user's words: the model owns the plan,
    while the host requires concrete successful tool evidence before it lets a
    multi-step durable task mark all remaining steps complete.
    """

    if (
        outcome.stop_reason != "end_turn"
        or outcome.prepared_calls
        or outcome.error is not None
        or not outcome.assistant_content.strip()
    ):
        return False
    # A durable child may legitimately exist only to reason or return a
    # compact textual result.  ``_durable_task_evidence_satisfied`` admits the
    # no-receipt case only when the objective explicitly forbids tools and has
    # no write/test obligation; all effectful objectives remain fail-closed.
    return _durable_task_evidence_satisfied(proposal_state)


async def tool_execution_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    if effective.get("workflow_status") in {"blocked", "cancelled"}:
        return StatePatch({"values": _merged_values(state, {})})
    outcome = _outcome(effective)
    proposal_state = _proposal_state(effective)
    dispatch_ids = {str(value) for value in effective.get("dispatch_call_ids", [])}
    calls = [call for call in outcome.prepared_calls if call.stable_call_id in dispatch_ids]
    authorizations: dict[str, JsonValue] = {}
    if effective.get("approval_status") == "approved":
        for call in calls:
            if call.effect_type != "idempotent_read":
                # The user-approved Code plan is the durable consent boundary
                # for its validated mutating/command calls, including Context
                # OS tools activated after the workflow started.  The dispatch
                # adapter binds this grant to the exact effect, args and
                # trusted scope.
                authorizations[call.stable_call_id] = {
                    "action": "allow_once_opaque"
                }
    review = effective.get("dynamic_tool_review", [])
    if isinstance(review, list) and review:
        decision = _interrupt(
            {
                "kind": "unsupported_dynamic_tool",
                "tools": copy.deepcopy(review),
                "options": ["allow_once_opaque", "continue_without", "cancel"],
            }
        )
        action = str(decision.get("action", "cancel")) if isinstance(decision, Mapping) else str(decision)
        review_ids = {str(item["stable_call_id"]) for item in review}
        if action == "cancel":
            return StatePatch({"values": _merged_values(state, {"workflow_status": "cancelled", "cancel_reason": "dynamic_tool_rejected"})})
        if action == "continue_without":
            calls = [call for call in calls if call.stable_call_id not in review_ids]
            reviewed_by_id = {
                str(item["stable_call_id"]): item for item in review
            }
            skipped = {
                call_id: {
                    "stable_call_id": call_id,
                    "tool_name": str(reviewed_by_id[call_id]["tool_name"]),
                    "status": "failed",
                    "code": "unsupported_dynamic_tool",
                    "retryable": False,
                }
                for call_id in review_ids
            }
            proposal_payload = proposal_state.to_dict()
            proposal_payload["committed_tool_results"] = {**dict(proposal_payload["committed_tool_results"]), **skipped}
            proposal_state = ProposalStateV1.from_dict(proposal_payload)
        elif action == "allow_once_opaque":
            authorizations.update(
                {
                    call_id: {
                        "action": "allow_once_opaque",
                        "effect_type": "opaque_manual",
                    }
                    for call_id in review_ids
                }
            )
        else:
            raise ValueError("invalid unsupported_dynamic_tool decision")
    blocked_search_ids: set[str] = set()
    if (
        str(effective.get("workflow_name") or "") == "durable_task"
        and calls
        and all(
            call.tool_name in _REPEATABLE_DISCOVERY_SEARCH_TOOL_NAMES
            for call in calls
        )
        and _consecutive_discovery_searches(proposal_state)
        >= _MAX_CONSECUTIVE_DISCOVERY_SEARCHES
    ):
        blocked_search_ids = {call.stable_call_id for call in calls}
    blocked_describe_ids: set[str] = set()
    if (
        str(effective.get("workflow_name") or "") == "durable_task"
        and calls
        and all(call.tool_name == "tool_describe" for call in calls)
        and _consecutive_failed_describes(proposal_state)
        >= _MAX_CONSECUTIVE_FAILED_DESCRIBES
    ):
        blocked_describe_ids = {call.stable_call_id for call in calls}
    dispatchable_calls = [
        call
        for call in calls
        if call.stable_call_id not in blocked_search_ids
        and call.stable_call_id not in blocked_describe_ids
    ]
    raw_results: dict[str, JsonValue] = {
        call.stable_call_id: {
            "ok": False,
            "status": "failed",
            "code": "discovery_loop_blocked",
            "message": (
                "The durable workflow already used three consecutive search "
                "turns. Use an exact capability_id and next_action from the "
                "latest result, or execute an already discovered capability."
            ),
        }
        for call in calls
        if call.stable_call_id in blocked_search_ids
    }
    raw_results.update(
        {
            call.stable_call_id: {
                "ok": False,
                "status": "failed",
                "code": "capability_describe_loop_blocked",
                "message": (
                    "The durable workflow already made two consecutive failed "
                    "tool_describe calls. Stop guessing capability ids. Call "
                    "tool_search with a short query and copy one returned full "
                    "capability_id exactly."
                ),
            }
            for call in calls
            if call.stable_call_id in blocked_describe_ids
        }
    )
    if dispatchable_calls:
        dispatched = await _call(
            context.port("tool"),
            dispatchable_calls,
            method="dispatch",
            workflow_step_id=str(proposal_state.active_step_id or ""),
            prior_results=proposal_state.committed_tool_results,
            authorizations=authorizations,
        )
        if not isinstance(dispatched, Mapping):
            raise TypeError("dispatch port must return a mapping keyed by stable call id")
        raw_results.update(dispatched)
    call_by_id = {call.stable_call_id: call for call in calls}
    normalized: dict[str, JsonValue] = {}
    for call_id, raw in raw_results.items():
        call = call_by_id.get(str(call_id))
        if call is None:
            raise ValueError(f"dispatch returned unknown call id: {call_id}")
        payload = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else {"result": copy.deepcopy(raw)}
        payload.update(
            {
                "stable_call_id": call.stable_call_id,
                "tool_name": call.tool_name,
                "workflow_step_id": str(proposal_state.active_step_id or ""),
                "args_hash": call.args_hash,
            }
        )
        validate_json_value(payload)
        normalized[call.stable_call_id] = payload
    missing = [call.stable_call_id for call in calls if call.stable_call_id not in normalized]
    if missing:
        raise ValueError(f"dispatch omitted prepared call results: {', '.join(missing)}")
    proposal = proposal_state.to_dict()
    committed = {**dict(proposal["committed_tool_results"]), **normalized}
    proposal["committed_tool_results"] = committed
    proposal["pending_tool_results"] = {}
    proposal["tools_used"] = proposal_state.tools_used + len(calls)
    proposal["gate_state"] = {
        **proposal_state.gate_state.to_dict(),
        "tools_used": proposal_state.gate_state.tools_used + len(calls),
        "last_transition": "tool_execution",
    }
    messages = list(proposal["messages"])
    if (
        str(effective.get("workflow_name") or "") == "durable_task"
        and calls
        and not blocked_search_ids
        and all(
            call.tool_name in _REPEATABLE_DISCOVERY_SEARCH_TOOL_NAMES
            for call in calls
        )
    ):
        messages = _drop_superseded_discovery_search_messages(messages)
    if calls:
        raw_by_id = {
            str(item.get("stable_call_id", "")): item
            for item in outcome.raw_tool_proposals
        }
        messages.append(
            {
                "role": "assistant",
                "content": outcome.assistant_content,
                "tool_calls": [
                    {
                        "id": call.stable_call_id,
                        "type": "function",
                        "function": {
                            "name": call.tool_name,
                            "arguments": canonical_json(
                                raw_by_id.get(call.stable_call_id, {}).get(
                                    "raw_params", call.arguments_json()
                                )
                            ),
                        },
                    }
                    for call in calls
                ],
            }
        )
    elif outcome.assistant_content:
        messages.append(
            {
                "role": "assistant",
                "content": outcome.assistant_content,
            }
        )
    for call in calls:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.stable_call_id,
                "name": call.tool_name,
                "content": canonical_json(normalized[call.stable_call_id]),
            }
        )
    if outcome.error is not None:
        # A rejected provider proposal has no executable tool result, but the
        # next model turn still needs the host's structured reason.  Without
        # this feedback the model can repeat the same unavailable tool until
        # the durable proposal budget is exhausted.
        messages.append(
            {
                "role": "system",
                "content": canonical_json(
                    {
                        "type": "host_tool_proposal_rejected",
                        "error": outcome.error.to_dict(),
                        "instruction": (
                            "Do not repeat the rejected call unchanged. "
                            "Follow the error recovery details, then continue "
                            "the existing task."
                        ),
                    }
                ),
            }
        )
    proposal["messages"] = messages
    todos = _todo_items(effective)
    all_success = bool(calls) and all(_result_success(normalized[call.stable_call_id]) for call in calls)
    if str(effective.get("workflow_name") or "") == "durable_task":
        tool_free_complete = False
        evidence_backed_complete = _tool_free_receipt_backed_completed(
            outcome, proposal_state
        )
        step_receipt_backed = all_success and any(
            call.tool_name not in _DISCOVERY_ONLY_TOOL_NAMES
            for call in calls
        )
    else:
        # Frozen code_complex@v1 checkpoints retain their historical
        # completion behavior.  New durable_task runs never enter this branch.
        tool_free_complete = _tool_free_read_only_completed(outcome, proposal_state)
        evidence_backed_complete = _tool_free_evidence_backed_completed(
            outcome, proposal_state
        )
        step_receipt_backed = all_success
    active_step = proposal_state.active_step_id
    if evidence_backed_complete:
        for item in todos:
            item["status"] = "completed"
        proposal["active_step_id"] = None
    elif (step_receipt_backed or tool_free_complete) and active_step:
        for item in todos:
            if item.get("workflow_step_id") == active_step:
                item["status"] = "completed"
                break
        next_todo = next((item for item in todos if item.get("status") != "completed"), None)
        proposal["active_step_id"] = str(next_todo["workflow_step_id"]) if next_todo else None
    run_id = str(state.get("run_id", ""))
    return StatePatch(
        {
            "values": _merged_values(
                state,
                {
                    "proposal_state": proposal,
                    "tool_results": normalized,
                    "todos": todos,
                    "todo_intents": _todo_intents(run_id, todos),
                    "dynamic_tool_review": [],
                },
            )
        }
    )


async def completion_decision_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    proposal_state = _proposal_state(effective)
    outcome = _outcome(effective)
    todos = _todo_items(effective)
    incomplete = [str(item["workflow_step_id"]) for item in todos if item.get("status") != "completed"]
    budgets = state.get("budgets", {})
    budget = int(budgets.get("proposal_turns", MAX_PROPOSAL_TURNS)) if isinstance(budgets, Mapping) else MAX_PROPOSAL_TURNS
    if effective.get("workflow_status") in {"cancelled", "blocked"}:
        decision: dict[str, JsonValue] = {"route": "finalize", "reason": str(effective.get("blocked_reason", "cancelled")), "incomplete_todo_ids": incomplete}
    elif proposal_state.proposal_turns_used >= budget:
        decision = {"route": "audit", "reason": "proposal_budget_exhausted", "incomplete_todo_ids": incomplete}
    elif outcome.prepared_calls:
        decision = {"route": "loop", "reason": "tool_results_available", "incomplete_todo_ids": incomplete}
    elif incomplete:
        decision = {"route": "loop", "reason": "incomplete_todos", "incomplete_todo_ids": incomplete}
    else:
        decision = {"route": "test", "reason": "proposal_complete", "incomplete_todo_ids": []}
    evaluator = context.ports.get("evaluator")
    if evaluator is not None and hasattr(evaluator, "completion_decision"):
        override = await _call(evaluator, copy.deepcopy(decision), proposal_state, method="completion_decision")
        if isinstance(override, Mapping):
            decision.update(copy.deepcopy(dict(override)))
    route = str(decision.get("route", "loop"))
    if incomplete and route == "test":
        route = "loop" if proposal_state.proposal_turns_used < budget else "audit"
        decision["route"] = route
        decision["reason"] = "incomplete_todos"
    proposal = proposal_state.to_dict()
    proposal["completion_attempts"] = proposal_state.completion_attempts + 1
    proposal["completion_outcomes"] = [*proposal_state.completion_outcomes, decision]
    return StatePatch({"values": _merged_values(state, {"proposal_state": proposal, "completion_decision": decision, "next_route": route})})


async def completion_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    route = str(_effective(state).get("next_route", "loop"))
    return route if route in {"loop", "test", "audit", "finalize"} else "loop"


async def test_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    proposal_state = _proposal_state(effective)
    evaluator = context.ports.get("evaluator")
    if evaluator is not None and hasattr(evaluator, "run_tests"):
        raw = await _call(evaluator, proposal_state, method="run_tests")
        if not isinstance(raw, Mapping):
            raise TypeError("run_tests must return a JSON object")
        result = copy.deepcopy(dict(raw))
    elif str(effective.get("workflow_name") or "") == "durable_task":
        successful = _successful_receipts(proposal_state)
        result = {
            "passed": _durable_task_evidence_satisfied(proposal_state),
            "evidence_refs": [
                str(receipt.get("stable_call_id", ""))
                for receipt in successful
                if str(receipt.get("stable_call_id", ""))
            ],
            "source": "durable_tool_receipts",
        }
    else:
        result = {"passed": True, "evidence_refs": []}
    output_contract_port = context.ports.get("output_contract")
    if (
        str(effective.get("workflow_name") or "") == "durable_task"
        and output_contract_port is not None
    ):
        contract_result = await _call(
            output_contract_port,
            method="audit",
        )
        if not isinstance(contract_result, Mapping):
            raise TypeError("output contract audit must return a JSON object")
        contract_payload = copy.deepcopy(dict(contract_result))
        result["output_contract"] = contract_payload
        result["passed"] = bool(result.get("passed", False)) and bool(
            contract_payload.get("passed", False)
        )
        if not bool(contract_payload.get("passed", False)):
            result["failure_code"] = "task_output_contract_failed"
    validate_json_value(result)
    evidence = [str(value) for value in result.get("evidence_refs", [])]
    proposal = proposal_state.to_dict()
    proposal["verify_attempts"] = proposal_state.verify_attempts + 1
    proposal["verify_outcomes"] = [*proposal_state.verify_outcomes, result]
    proposal["evidence_refs"] = list(dict.fromkeys([*proposal_state.evidence_refs, *evidence]))
    return StatePatch({"values": _merged_values(state, {"proposal_state": proposal, "test_result": result})})


async def audit_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    proposal_state = _proposal_state(effective)
    todos = _todo_items(effective)
    incomplete = [str(item["workflow_step_id"]) for item in todos if item.get("status") != "completed"]
    test_result = effective.get("test_result", {})
    test_passed = isinstance(test_result, Mapping) and bool(test_result.get("passed", False))
    audit: dict[str, JsonValue] = {
        "passed": test_passed and not incomplete,
        "test_passed": test_passed,
        "incomplete_todo_ids": incomplete,
        "reason": "complete" if test_passed and not incomplete else ("incomplete_todos" if incomplete else "tests_failed"),
    }
    if isinstance(test_result, Mapping):
        if isinstance(test_result.get("output_contract"), Mapping):
            audit["output_contract"] = copy.deepcopy(
                dict(test_result["output_contract"])
            )
        if test_result.get("failure_code"):
            audit["failure_code"] = str(test_result["failure_code"])
    evaluator = context.ports.get("evaluator")
    if evaluator is not None and hasattr(evaluator, "audit"):
        override = await _call(evaluator, copy.deepcopy(audit), proposal_state, method="audit")
        if isinstance(override, Mapping):
            audit.update(copy.deepcopy(dict(override)))
    if incomplete:
        audit["passed"] = False
        audit["reason"] = "incomplete_todos"
    budgets = state.get("budgets", {})
    proposal_budget = int(budgets.get("proposal_turns", MAX_PROPOSAL_TURNS)) if isinstance(budgets, Mapping) else MAX_PROPOSAL_TURNS
    fix_budget = int(budgets.get("fix_rounds", MAX_FIX_ROUNDS)) if isinstance(budgets, Mapping) else MAX_FIX_ROUNDS
    can_fix = proposal_state.proposal_turns_used < proposal_budget and proposal_state.fix_rounds_used < fix_budget
    if bool(audit.get("passed", False)):
        route = "finalize"
        workflow_status = "completed"
    elif can_fix:
        route = "fix"
        workflow_status = "running"
    else:
        route = "finalize"
        workflow_status = "blocked"
        audit["budget_exhausted"] = True
    proposal = proposal_state.to_dict()
    if route == "fix":
        proposal["fix_rounds_used"] = proposal_state.fix_rounds_used + 1
        repair_step = next(
            (item for item in todos if item.get("status") != "completed"),
            todos[-1] if todos else None,
        )
        if repair_step is not None:
            repair_step["status"] = "pending"
            proposal["active_step_id"] = str(repair_step["workflow_step_id"])
    proposal["self_check_attempts"] = proposal_state.self_check_attempts + 1
    proposal["self_check_outcomes"] = [*proposal_state.self_check_outcomes, audit]
    patch: dict[str, JsonValue] = {
        "values": _merged_values(
            state,
            {
                "proposal_state": proposal,
                "audit_result": audit,
                "phase": "fix" if route == "fix" else effective.get("phase", "execution"),
                "workflow_status": workflow_status,
                "audit_route": route,
                "todos": todos,
                "todo_intents": _todo_intents(str(state.get("run_id", "")), todos),
            },
        )
    }
    if route == "fix":
        patch["loop_counters"] = {
            "proposal_turns": proposal_state.proposal_turns_used,
            "fix_rounds": proposal_state.fix_rounds_used + 1,
        }
    return StatePatch(patch)


async def audit_route(state: WorkflowState, context: WorkflowContext) -> str:
    del context
    return "fix" if _effective(state).get("audit_route") == "fix" else "finalize"


async def finalize_handler(state: WorkflowState, context: WorkflowContext) -> StatePatch:
    effective = _effective(state)
    run_id = context.identity.run_id if context.identity is not None else str(state.get("run_id", ""))
    if not run_id:
        raise ValueError("workflow run id is missing")
    workflow_status = str(effective.get("workflow_status", "blocked"))
    audit = copy.deepcopy(effective.get("audit_result", {}))
    todos = _todo_items(effective)
    outcome_raw = effective.get("proposal_outcome")
    assistant_content = ""
    if isinstance(outcome_raw, Mapping):
        assistant_content = str(outcome_raw.get("assistant_content", ""))
    if not assistant_content:
        workflow_label = (
            "durable task"
            if str(effective.get("workflow_name") or "") == "durable_task"
            else "code workflow"
        )
        assistant_content = {
            "completed": f"The {workflow_label} completed and passed its audit.",
            "cancelled": f"The {workflow_label} was cancelled before completion.",
        }.get(
            workflow_status,
            f"The {workflow_label} stopped without claiming completion.",
        )
    summary: dict[str, JsonValue] = {
        "status": workflow_status,
        "audit": audit if isinstance(audit, dict) else {},
        "todos": todos,
    }
    intents: list[dict[str, JsonValue]] = [
        {
            "intent_id": f"{run_id}:workflow-report",
            "kind": "workflow_report",
            "channel": "workflow_report",
            "payload": copy.deepcopy(summary),
        },
        {
            "intent_id": f"{run_id}:final",
            "kind": "final_assistant",
            "channel": "final_assistant",
            "payload": {"text": assistant_content, "workflow": copy.deepcopy(summary)},
        },
    ]
    return StatePatch({"values": {"delivery_intents": intents}})


__all__ = [
    "CapabilitySnapshotV1",
    "FORBIDDEN_DURABLE_TOOLS",
    "MAX_FIX_ROUNDS",
    "MAX_PROPOSAL_TURNS",
    "TaskSessionRefV1",
    "WorkflowSessionRefV1",
    "approval_route",
    "audit_handler",
    "audit_route",
    "clarify_handler",
    "completion_decision_handler",
    "completion_route",
    "finalize_handler",
    "intake_handler",
    "llm_proposal_handler",
    "plan_handler",
    "test_handler",
    "tool_execution_handler",
    "wait_approval_handler",
]
