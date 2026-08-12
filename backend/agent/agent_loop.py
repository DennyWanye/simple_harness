# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""LLM collaborator for DeskPet's ReAct driver.

Provider turns and completion gates stay here. Tool requests cross a typed
``ToolBatchEvent`` boundary; Harness alone executes them and resumes the run.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Literal,
    Mapping,
    Optional,
    Protocol,
    TYPE_CHECKING,
    Union,
)

if TYPE_CHECKING:
    from deskpet.execution.contracts import ProviderLaunchSnapshot

from agent import errors as agent_errors
from agent.context_messages import (
    CONTEXT_MESSAGE_META_KEY,
    ContextMessageMeta,
    ProviderAttemptOptions,
    append_control,
    append_transcript,
    context_attempt_scope,
    tag_message,
)
from agent.task_id import new_task_id
from agent.context_manager import ContextManager
from agent.skill_remount import (
    frozen_skill_scope,
    prepare_frozen_skill_remounts,
    remount_skills,
    skill_scope_candidates,
)
from agent.termination import GateConfig, TerminationGate, TerminationReason
from llm.budget import DailyBudget
from llm.errors import LLMBudgetExceededError, LLMProviderError
from llm.types import ChatResponse, ToolCall
from deskpet.execution.provider_invocations import (
    coordinate_provider_call,
    coordinate_provider_stream,
)

from agent.workflow_trace import (
    HarnessTraceSession,
    TraceInstrumentationError,
    traced_call,
    traced_iterate,
)
from deskpet.workflows.trace import SpanContext, SpanKind, SpanStatus, TraceStore

logger = logging.getLogger("deskpet.agent.loop")


def _planned_generation_reserve(
    prepared_context: Any,
    llm_kwargs: Mapping[str, Any],
) -> int:
    """Reuse the reserve chosen by Context OS for every in-request replan.

    A missing ``max_tokens`` must not imply an 8192-token reserve: on an 8K
    model that leaves zero input budget and makes every progressive tool
    activation fail even though the original request fit.  The prepared
    request budget is authoritative; an explicit provider override is only a
    fallback for legacy callers without prepared metadata.
    """

    initial_budget = getattr(prepared_context, "request_budget", None)
    return int(
        getattr(initial_budget, "generation_reserve", 0)
        or llm_kwargs.get("max_tokens", 0)
        or 0
    )


async def _coordinate_agent_provider_call(
    loop: Any,
    provider: Any,
    request_id: str,
    attempt_id: str,
    purpose: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    fallback_model: str,
    invoke: Callable[[], Awaitable[Any]],
) -> Any:
    return await coordinate_provider_call(
        coordinator=getattr(loop, "_provider_invocation_coordinator", None),
        acquire_fence=getattr(loop, "_provider_fence_acquirer", None),
        run_id=getattr(loop, "_provider_run_id", None) or request_id,
        session_id=getattr(loop, "_provider_session_id", None),
        root_run_id=getattr(loop, "_provider_root_run_id", None),
        parent_run_id=getattr(loop, "_provider_parent_run_id", None),
        profile_key=getattr(loop, "_provider_profile_key", None),
        provider=provider,
        attempt_id=attempt_id,
        purpose=purpose,
        messages=messages,
        tools=tools,
        fallback_model=fallback_model,
        invoke=invoke,
    )


def _coordinate_agent_provider_stream(
    loop: Any,
    provider: Any,
    request_id: str,
    attempt_id: str,
    purpose: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    fallback_model: str,
    iterator: Callable[[], AsyncIterator[Any]],
) -> AsyncIterator[Any]:
    return coordinate_provider_stream(
        coordinator=getattr(loop, "_provider_invocation_coordinator", None),
        acquire_fence=getattr(loop, "_provider_fence_acquirer", None),
        run_id=getattr(loop, "_provider_run_id", None) or request_id,
        session_id=getattr(loop, "_provider_session_id", None),
        root_run_id=getattr(loop, "_provider_root_run_id", None),
        parent_run_id=getattr(loop, "_provider_parent_run_id", None),
        profile_key=getattr(loop, "_provider_profile_key", None),
        provider=provider,
        attempt_id=attempt_id,
        purpose=purpose,
        messages=messages,
        tools=tools,
        fallback_model=fallback_model,
        iterator=iterator,
    )


def _context_fragment_id(message: dict[str, Any] | None) -> str | None:
    if not isinstance(message, dict):
        return None
    metadata = message.get(CONTEXT_MESSAGE_META_KEY)
    if not isinstance(metadata, dict):
        return None
    fragment_id = metadata.get("fragment_id")
    return str(fragment_id) if fragment_id else None


def _latest_context_anchor(
    messages: list[dict[str, Any]], *, fallback: str
) -> str:
    for message in reversed(messages):
        fragment_id = _context_fragment_id(message)
        if fragment_id:
            return fragment_id
    return fallback


def _append_loop_transcript(
    messages: list[dict[str, Any]],
    value: dict[str, Any] | str,
    *,
    metadata_enabled: bool,
    source: str,
    role: str,
    fragment_id: str,
    causal_group_id: str | None = None,
) -> dict[str, Any]:
    if metadata_enabled:
        return append_transcript(
            messages,
            value,
            source=source,
            role=role,
            fragment_id=fragment_id,
            causal_group_id=causal_group_id,
        )
    message = {"role": role, "content": value} if isinstance(value, str) else value
    messages.append(message)
    return message


def _append_loop_control(
    messages: list[dict[str, Any]],
    value: dict[str, Any] | str,
    *,
    metadata_enabled: bool,
    source: str,
    anchor_after: str,
    fragment_id: str,
    protected: bool = False,
    trim_policy: Literal[
        "never", "truncate", "drop", "summarize", "page_in"
    ] = "drop",
) -> dict[str, Any]:
    if metadata_enabled:
        return append_control(
            messages,
            value,
            source=source,
            anchor_after=anchor_after,
            fragment_id=fragment_id,
            protected=protected,
            trim_policy=trim_policy,
        )
    message = {"role": "system", "content": value} if isinstance(value, str) else value
    messages.append(message)
    return message


def _tag_run_input_messages(
    messages: list[dict[str, Any]], *, task_id: str
) -> list[dict[str, Any]]:
    """Add host-only identity to untagged prepared input without wire changes."""

    tagged: list[dict[str, Any]] = []
    open_group: str | None = None
    open_tool_ids: set[str] = set()
    for index, message in enumerate(messages):
        if CONTEXT_MESSAGE_META_KEY in message:
            tagged.append(message)
            continue
        role = str(message.get("role") or "user")
        fragment_id = f"agent-loop:{task_id}:input:{index}"
        if role == "assistant" and message.get("tool_calls"):
            open_group = f"agent-loop:{task_id}:input-group:{index}"
            open_tool_ids = {
                str(call.get("id"))
                for call in message.get("tool_calls") or ()
                if isinstance(call, dict) and call.get("id")
            }
        causal_group_id = (
            open_group
            if role == "assistant" or (
                role == "tool" and str(message.get("tool_call_id")) in open_tool_ids
            )
            else None
        )
        if role == "system":
            metadata = ContextMessageMeta(
                placement="prefix",
                lifetime="stable",
                source="agent_loop.prepared_input",
                protected=True,
                trim_policy="never",
                fragment_id=fragment_id,
            )
        else:
            metadata = ContextMessageMeta(
                placement="transcript",
                lifetime="history",
                source="agent_loop.prepared_input",
                trim_policy="summarize",
                fragment_id=fragment_id,
                causal_group_id=causal_group_id,
            )
        tagged.append(tag_message(message, metadata))
        if role == "tool":
            open_tool_ids.discard(str(message.get("tool_call_id")))
            if not open_tool_ids:
                open_group = None
    return tagged


def _context_message_signature(message: Mapping[str, Any]) -> str:
    wire = {
        key: value
        for key, value in message.items()
        if key != CONTEXT_MESSAGE_META_KEY
    }
    metadata = message.get(CONTEXT_MESSAGE_META_KEY)
    identity = {
        "wire": wire,
        "fragment_id": (
            metadata.get("fragment_id") if isinstance(metadata, Mapping) else None
        ),
        "causal_group_id": (
            metadata.get("causal_group_id")
            if isinstance(metadata, Mapping)
            else None
        ),
    }
    return json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str)


def _capture_context_remount(
    messages: list[dict[str, Any]], *, recent_groups: int = 6
) -> dict[str, tuple[Any, ...]]:
    protected: list[dict[str, Any]] = []
    transcript_groups: list[list[dict[str, Any]]] = []
    open_group_id: str | None = None
    open_group: list[dict[str, Any]] = []

    def flush_group() -> None:
        nonlocal open_group_id, open_group
        if open_group:
            transcript_groups.append(open_group)
        open_group_id = None
        open_group = []

    for message in messages:
        metadata = message.get(CONTEXT_MESSAGE_META_KEY)
        if not isinstance(metadata, Mapping):
            continue
        placement = str(metadata.get("placement") or "")
        lifetime = str(metadata.get("lifetime") or "")
        source = str(metadata.get("source") or "")
        if placement == "prefix" and (
            bool(metadata.get("protected"))
            or lifetime in {"platform", "stable", "task"}
            or source.startswith("project_rules")
        ):
            protected.append(dict(message))
        if placement != "transcript" or metadata.get("trim_policy") == "page_in":
            continue
        causal_group_id = str(metadata.get("causal_group_id") or "")
        if causal_group_id:
            if open_group_id and open_group_id != causal_group_id:
                flush_group()
            if not open_group:
                open_group_id = causal_group_id
            open_group.append(dict(message))
        else:
            flush_group()
            transcript_groups.append([dict(message)])
    flush_group()
    tail = transcript_groups[-max(1, int(recent_groups)) :]
    return {
        "protected": tuple(protected),
        "tail_groups": tuple(tuple(group) for group in tail),
    }


def _remount_context_after_compaction(
    messages: list[dict[str, Any]],
    remount: Mapping[str, Any],
) -> list[dict[str, Any]]:
    restored = [dict(message) for message in messages]
    existing = {_context_message_signature(message) for message in restored}
    missing_prefix = [
        dict(message)
        for message in tuple(remount.get("protected", ()) or ())
        if _context_message_signature(message) not in existing
    ]
    if missing_prefix:
        insert_at = next(
            (
                index
                for index, message in enumerate(restored)
                if str(message.get("role") or "") != "system"
            ),
            len(restored),
        )
        restored[insert_at:insert_at] = missing_prefix
        existing.update(_context_message_signature(item) for item in missing_prefix)

    for raw_group in tuple(remount.get("tail_groups", ()) or ()):
        group = [dict(message) for message in raw_group]
        signatures = {_context_message_signature(message) for message in group}
        if signatures and signatures.issubset(existing):
            continue
        # A partial tool group is more dangerous than a duplicate: remove the
        # surviving fragment, then restore the original group atomically.
        restored = [
            message
            for message in restored
            if _context_message_signature(message) not in signatures
        ]
        insert_at = len(restored)
        while insert_at > 0:
            metadata = restored[insert_at - 1].get(CONTEXT_MESSAGE_META_KEY)
            if not (
                isinstance(metadata, Mapping)
                and metadata.get("placement") == "control"
            ):
                break
            insert_at -= 1
        restored[insert_at:insert_at] = group
        existing = {_context_message_signature(message) for message in restored}
    return restored


class DispatchOutcome(str, Enum):
    CONTINUE = "continue"
    ASYNC_HANDOFF = "async_handoff"


def _decode_tool_envelope(result: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _async_handoff_details(result: str) -> dict[str, str] | None:
    """Return stable workflow refs only for a successful registry envelope."""

    envelope = _decode_tool_envelope(result)
    if envelope is None or envelope.get("ok") is not True:
        return None
    payload: Any = envelope.get("result")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, dict):
        return None
    accepted_event_id = str(
        payload.get("accepted_event_id")
        or envelope.get("accepted_event_id")
        or ""
    )
    if (
        payload.get("ok") is False
        or payload.get("accepted") is False
        or envelope.get("accepted") is False
    ):
        return None
    if payload.get("accepted") is not True and not accepted_event_id:
        return None
    return {
        "event_id": accepted_event_id,
        "run_id": str(payload.get("run_id") or envelope.get("run_id") or ""),
        "request_id": str(
            payload.get("request_id") or envelope.get("request_id") or ""
        ),
        "turn_id": str(payload.get("turn_id") or envelope.get("turn_id") or ""),
    }


def _tool_dispatch_failed(result: Any) -> bool:
    if not isinstance(result, str):
        return False
    payload = _decode_tool_envelope(result)
    if payload is None:
        return False
    if payload.get("ok") is False:
        return True
    nested = payload.get("result")
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except ValueError:
            nested = None
    if isinstance(nested, dict) and nested.get("ok") is False:
        return True
    return bool(payload.get("error")) and payload.get("ok") is not True


def _tool_declares_user_request(name: str, schemas: list[dict[str, Any]]) -> bool:
    """Return True when a tool schema exposes a ``user_request`` property."""
    for schema in schemas or []:
        function_schema = schema.get("function") if isinstance(schema, dict) else None
        candidate = function_schema if isinstance(function_schema, dict) else schema
        if not isinstance(candidate, dict):
            continue
        if candidate.get("name") != name:
            continue
        parameters = candidate.get("parameters")
        if not isinstance(parameters, dict):
            return False
        properties = parameters.get("properties")
        return isinstance(properties, dict) and "user_request" in properties
    return False


def _inject_loop_user_request(
    tc: ToolCall,
    schemas: list[dict[str, Any]],
    *,
    loop_user_request: Optional[str],
) -> None:
    """Inject this loop's original user request into opted-in tool calls."""
    if not loop_user_request or not isinstance(tc.arguments, dict):
        return
    if _tool_declares_user_request(tc.name, schemas):
        tc.arguments["user_request"] = loop_user_request
        # Task-drift observability anchor (grep `task_drift_user_request_injected`
        # in the tauri-dev stderr log). Confirms Fix B injected this loop's
        # original user request into the tool args — p5s2 dump truncates at
        # 100 chars so user_request itself isn't visible there.
        logger.info(
            "task_drift_user_request_injected tool=%s req_len=%d",
            tc.name,
            len(loop_user_request),
        )


def _tool_completion_semantics(tools: Any, name: str) -> str:
    get_spec = getattr(tools, "get", None)
    if callable(get_spec):
        semantics = getattr(get_spec(name), "completion_semantics", None)
        if semantics in {"sync", "accepted_async"}:
            return semantics
    all_specs = getattr(tools, "all_specs", None)
    if callable(all_specs):
        spec = next((item for item in all_specs()
                     if getattr(item, "name", None) == name), None)
        semantics = getattr(spec, "completion_semantics", None)
        if semantics in {"sync", "accepted_async"}:
            return semantics
    return "sync"


async def _suppress_repeated_tool_batch(
    loop: Any, calls: list[ToolCall], messages: list[dict[str, Any]], *,
    session_id: str, task_id: str, iteration: int, metadata_enabled: bool,
) -> bool:
    if loop.activity_store is None:
        return False
    from agent.session_activity import args_hash
    activity = await loop.activity_store.get(session_id)
    signatures = {} if activity is None else dict(activity.tool_signature_window)
    repeated = next((
        (call.name, int(signatures.get(
            f"{call.name}:{args_hash(call.arguments)}", 0
        )) + 1)
        for call in calls
        if int(signatures.get(
            f"{call.name}:{args_hash(call.arguments)}", 0
        )) >= loop._signature_repeat_threshold - 1
    ), None)
    if repeated is None:
        return False
    name, count = repeated
    anchor = _latest_context_anchor(
        messages, fallback=f"agent-loop:{task_id}:iteration:{iteration}:response"
    )
    _append_loop_control(
        messages,
        {"role": "system", "content": _REPEAT_NUDGE_MSG.format(
            name=name, count=count
        )},
        metadata_enabled=metadata_enabled,
        source="agent_loop.signature_repeat_nudge",
        anchor_after=anchor,
        fragment_id=f"agent-loop:{task_id}:iteration:{iteration}:signature-repeat-nudge:{count}",
    )
    return True


def _append_tool_batch_message(
    response: ChatResponse, messages: list[dict[str, Any]], *,
    task_id: str, iteration: int, metadata_enabled: bool,
) -> str:
    assistant: dict[str, Any] = {
        "role": "assistant", "content": response.content or "",
        "tool_calls": [{
            "id": call.id, "type": "function",
            "function": {"name": call.name, "arguments": json.dumps(
                call.arguments, ensure_ascii=False
            )},
        } for call in response.tool_calls],
    }
    if response.reasoning_content:
        assistant["reasoning_content"] = response.reasoning_content
    group_id = f"agent-loop:{task_id}:iteration:{iteration}:tool-group"
    _append_loop_transcript(
        messages, assistant, metadata_enabled=metadata_enabled,
        source="agent_loop.assistant_tool_group", role="assistant",
        fragment_id=f"{group_id}:assistant", causal_group_id=group_id,
    )
    return group_id


# ───────────────────── P5-S2 Phase 3 constants ─────────────────────


# Repeat-detection threshold: if the LLM emits the SAME (tool_name,
# args_hash) signature ``>= _REPEAT_THRESHOLD`` times in a row, the
# loop short-circuits the dispatch and injects a system message asking
# the LLM to look at the prior tool_result hint or change tactics.
# Keep at 3 to match the OpenSpec proposal default.
_REPEAT_THRESHOLD = 3

# ───────────────────── WI-1.3 goal-anchor constants ─────────────────────
#
# Every ``_GOAL_ANCHOR_EVERY`` iterations, when there is an active goal,
# inject a brief ``[目标锚定]`` system message into working_messages so the
# model is reminded of the original objective and doesn't drift into
# intermediate side-tasks.  This is ORTHOGONAL to the goal_checker nudge:
#   anchor = "don't drift away" (preventive, periodic)
#   nudge  = "you haven't finished yet" (reactive, on end_turn)
_GOAL_ANCHOR_EVERY = 5

# Module-level template so tests can pin it. Filled with ``.format(
# name=..., count=...)`` at injection time.
_REPEAT_NUDGE_MSG = (
    "你刚连续 {count} 次用同一个工具 {name} 调用同样的参数，"
    "结果不会变。看一下 tool_result 的 hint 字段，"
    "或者换个思路 / 换个工具。"
)

# ───────────────────── P5-S2 Phase 7: in-loop self-check ─────────────
#
# Every ``_SELFCHECK_EVERY`` iterations, BEFORE the LLM call, we inject
# a system message. The message ESCALATES based on iteration count:
#
#   tier 1 (iter=10): gentle reflection — "consider committing to a finish"
#   tier 2 (iter=20): firm — "you MUST justify each remaining tool call"
#   tier 3 (iter=30+): forced — "your NEXT response MUST be stop_reason=end_turn"
#
# Empirical: tier 1 alone wasn't enough — the LLM sees "considered" and
# keeps going. Forced tier at 30 reliably breaks "infinite content
# generation" patterns where each iteration writes another section,
# fix, refactor, etc.
#
# P6 Phase 6: the legacy soft tool-budget injection (``_TOOL_BUDGET_HARD_MSG``
# + the >= _TOOL_BUDGET_HARD branch) was removed — the TerminationGate
# now enforces tool_budget_hard with a HARD break that emits
# ErrorEvent(error_tool_budget). Only the iteration-based selfcheck
# tiers remain as soft nudges.

_SELFCHECK_EVERY = 10  # base interval — tier 1 fires here
_SELFCHECK_TIER2_AT = 20  # iter >= this → escalate
_SELFCHECK_TIER3_AT = 30  # iter >= this → forced stop
_TODO_SYNC_EVERY = 8
_TODO_SYNC_MSG = "[当前任务进度]\n{body}\n请优先完成未完成项，勿遗漏。"

_SELFCHECK_TIER1 = (
    "[self-check 第 1 级 / 第 {iter}/{max_iter} 轮 — 已用 {tools_used} 次工具调用]\n"
    "你已经跑了 {iter} 轮 ReAct 循环。在下一轮回复中先反思 3 行：\n"
    "1. 用户原问题的核心需求是什么？\n"
    "2. 你已经完成了哪些核心成果（具体改了什么文件、跑了什么命令、产出了什么结果）？\n"
    "3. 剩余工作是否真的必须？若不必须立即 stop_reason=end_turn 把可改进点写进 todo_write。\n"
    "记住：用户期望答完原问题，不是追求完美。剩 {budget} 轮预算。"
)

_SELFCHECK_TIER2 = (
    "[self-check 第 2 级 — 你已经跑了 {iter} 轮，使用了 {tools_used} 次工具调用]\n"
    "**警告**：你已经超过了正常任务的迭代量。不再允许『探索式』的工具调用。\n"
    "下一轮你**必须**做以下其中之一：\n"
    "  (A) 立即 stop_reason=end_turn，给用户一段总结：你做完了什么、剩余什么留给下次。\n"
    "  (B) 如果**真的**必须继续，先用一句话写清楚『为什么再来一轮就能收尾』，"
    "然后这一轮必须是**最后一轮**——只调一个工具，下下轮一定 end_turn。\n"
    "如果你又想『再写一节/再修一处/再扫一遍』——选 (A)，把它写进 todo 留给下次。"
)

_SELFCHECK_TIER3 = (
    "[STRICT STOP — 第 {iter}/{max_iter} 轮，已用 {tools_used} 次工具调用]\n"
    "你已经达到迭代预算的临界点。**禁止任何新的 tool_call**。\n"
    "你的下一轮回复**必须**：\n"
    "  1. 不包含任何 tool_calls 字段（即 stop_reason=end_turn）\n"
    "  2. 用 markdown 段落直接给用户写：\n"
    "     - 已完成的工作清单（带文件路径、改动概要）\n"
    "     - 已知未完成 / 待改进事项（用户可以下次让我做）\n"
    "  3. 用 todo_write 之前已经做过的，不要重复写\n"
    "如果你违反这条规则继续 tool_call，下一轮 supervisor 会强制中断你。"
)

def _build_selfcheck_message(iteration: int, max_iter: int, tools_used: int) -> str:
    """Choose the right self-check tier based on iteration count.

    Tier escalation handles the "LLM ignores gentle reminder" problem:
    we get progressively harsher until the LLM has no choice but to
    stop_reason=end_turn.
    """
    budget = max(0, max_iter - iteration)
    fmt = {
        "iter": iteration,
        "max_iter": max_iter,
        "budget": budget,
        "tools_used": tools_used,
    }
    if iteration >= _SELFCHECK_TIER3_AT:
        return _SELFCHECK_TIER3.format(**fmt)
    if iteration >= _SELFCHECK_TIER2_AT:
        return _SELFCHECK_TIER2.format(**fmt)
    return _SELFCHECK_TIER1.format(**fmt)


# ───────────────────── P5-S2 Phase 2 helpers ─────────────────────


def _classify_tool_result(result_str: str) -> type[Exception]:
    """Classify a tool result JSON string into an error class.

    Handles two layouts emitted by the dispatch path:
      1. v2 envelope: ``{"ok": false, "result": null, "error": "..."}``
      2. legacy / hand-shaped: ``{"error": "...", "retriable": ...}``
      3. nested — handler put a structured envelope into ``result`` as
         a JSON string: ``{"ok": true, "result": "{\"ok\":false,\"error\":...}"}``

    Always defaults to :class:`agent_errors.TransientToolError` on parse
    failure / unknown shape (conservative — never break a turn over
    something we can't read).
    """
    import json as _json

    try:
        payload = _json.loads(result_str) if isinstance(result_str, str) else result_str
    except (ValueError, TypeError):
        return agent_errors.TransientToolError

    if not isinstance(payload, dict):
        return agent_errors.TransientToolError

    # v2 envelope success path: ok=True with nested result. Inspect the
    # nested structure (handlers can return {"ok": false, ...} inside
    # result even when execute_tool considered the call "successful"
    # at the dispatch layer).
    if payload.get("ok") is True and isinstance(payload.get("result"), str):
        nested_str = payload["result"]
        try:
            nested = _json.loads(nested_str)
        except (ValueError, TypeError):
            nested = None
        if isinstance(nested, dict) and nested.get("ok") is False:
            return agent_errors.classify(nested)
        # Successful, no nested error → no classification needed.
        return agent_errors.TransientToolError

    # Failure envelopes (top-level error string).
    return agent_errors.classify(payload)


def _extract_break_detail(result_str: str, tool_name: str) -> str:
    """Pull a short human-readable detail string out of a tool result
    JSON for the ``ErrorEvent.detail`` field. Prefers a ``hint``
    (Phase 0 sensor feedback) over the raw ``error`` so the message we
    surface is actionable.
    """
    import json as _json

    try:
        payload = _json.loads(result_str) if isinstance(result_str, str) else result_str
    except (ValueError, TypeError):
        return f"tool {tool_name} returned unparseable error"

    if not isinstance(payload, dict):
        return f"tool {tool_name}: {str(payload)[:200]}"

    # Try nested first (envelope.result is a JSON string with the
    # actual handler error including hint).
    nested_str = payload.get("result")
    if isinstance(nested_str, str):
        try:
            nested = _json.loads(nested_str)
        except (ValueError, TypeError):
            nested = None
        if isinstance(nested, dict):
            for key in ("hint", "error", "message"):
                value = nested.get(key)
                if isinstance(value, str) and value:
                    return f"tool {tool_name}: {value}"[:300]

    # Fall back to top-level fields.
    for key in ("hint", "error", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return f"tool {tool_name}: {value}"[:300]

    return f"tool {tool_name} permanent error (no detail)"


_DEEPRESEARCH_FINALIZE_MSG = (
    "[deepresearch 已完成]\n"
    "上一条 deepresearch 工具结果已经产出调研报告/引用/文件。"
    "请不要再调用 web_search、web_fetch、web_extract_article 或 deepresearch；"
    "直接基于该报告回答用户，说明关键结论、来源质量和必要限制。"
)


def _deepresearch_result_is_complete(result_str: str) -> bool:
    """Return True when a deepresearch result is good enough to finalize.

    The v2 registry usually wraps tool output as ``{"ok": true,
    "result": "<handler json>"}``, while some tests/legacy paths return
    the handler JSON directly. Only a successful payload with report text
    plus citations or a saved artifact should force the next turn to stop
    using tools; failed/empty reports may still need follow-up search.
    """
    import json as _json

    try:
        payload = _json.loads(result_str) if isinstance(result_str, str) else result_str
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False

    if payload.get("ok") is True and isinstance(payload.get("result"), str):
        try:
            nested = _json.loads(payload["result"])
        except (TypeError, ValueError):
            nested = None
        if isinstance(nested, dict):
            payload = nested

    if payload.get("ok") is not True:
        return False
    report_md = str(payload.get("report_md") or "").strip()
    citations = payload.get("citations")
    has_citations = isinstance(citations, list) and bool(citations)
    has_artifact = bool(payload.get("path") or payload.get("artifacts"))
    return bool(report_md and (has_citations or has_artifact))


# ───────────────────── event dataclasses ─────────────────────


@dataclass
class AgentEvent:
    """Base event emitted by the agent loop.

    All fields default so subclasses can freely add non-default fields
    without hitting python's "non-default follows default" check. Each
    subclass overrides `type` in __post_init__ via a class-level default.
    """

    type: str = ""
    task_id: str = ""
    iteration: int = 0


@dataclass
class AssistantMessageEvent(AgentEvent):
    content: str = ""
    # P4-S24: chain-of-thought from thinking-mode models. Persisted to
    # SessionDB so cross-session history rebuilds satisfy the
    # "reasoning_content must be passed back" API constraint. Empty
    # for non-thinking models — safe to ignore.
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "assistant_message"


@dataclass
class AssistantDeltaEvent(AgentEvent):
    """P4-S25 A1: incremental token chunk during streaming.

    Emitted only when the loop runs in streaming mode (caller uses a
    shim with ``chat_with_fallback_stream``). Aggregating these gives
    the same string as the AssistantMessageEvent emitted at the end of
    each iteration, but the user sees text trickle in rather than wait
    for the whole turn.

    ``kind`` distinguishes visible content vs hidden thinking-mode
    reasoning; the frontend can render reasoning in a faded panel and
    content in the main bubble.
    """
    content: str = ""
    kind: str = "content"  # "content" | "reasoning"
    invocation_id: str | None = None
    stream_epoch: str | None = None
    provisional: bool = False
    retract_provisional: bool = False

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "assistant_delta"


@dataclass
class ToolCallEvent(AgentEvent):
    tool_call: Optional[ToolCall] = None

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_call"


@dataclass
class ToolBatchEvent(AgentEvent):
    """Pause point for an external harness-owned tool executor."""

    tool_calls: tuple[ToolCall, ...] = ()
    canonical_messages: tuple[Mapping[str, Any], ...] = ()
    feedback_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_batch"


@dataclass
class ToolResultEvent(AgentEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    result: str = ""  # JSON string
    # Typed harness outcomes keep their non-success status through the legacy
    # product presenter.  Legacy AgentLoop callers omit these fields and retain
    # the historical succeeded behavior.
    outcome_status: str = "succeeded"
    outcome_error: Optional[dict] = None
    # 七步流水线 Step5 标签（plans/2026-06-24-...）：{"step":5,"observation_summary":"..."}。
    # pipeline off → None = BC（前端不读即无影响）。
    pipeline_label: Optional[dict] = None

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_result"


@dataclass
class AsyncHandoffEvent(AgentEvent):
    """Terminal ReAct event after a durable workflow accepted the request."""

    tool_call_id: str = ""
    tool_name: str = ""
    result: str = ""
    event_id: str = ""
    run_id: str = ""
    request_id: str = ""
    turn_id: str = ""
    dispatch_outcome: DispatchOutcome = DispatchOutcome.ASYNC_HANDOFF

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "async_handoff"


# A short alias for callers that describe this as a generic handoff event.
HandoffEvent = AsyncHandoffEvent


@dataclass
class FinalEvent(AgentEvent):
    content: str = ""
    # P4-S24: passed through so `_run_chat` can persist it on the final
    # assistant turn (DB row's reasoning_content column).
    reasoning_content: str = ""
    stop_reason: str = "end_turn"
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "final"


@dataclass
class ErrorEvent(AgentEvent):
    reason: str = ""
    detail: str = ""
    # WI-R5: structured relay error code propagated from
    # LLMProviderError.error_class — "insufficient_balance" /
    # "relay_key_invalid". "" for non-relay / unclassified failures.
    # The frontend renders a friendly message + 充值 hint for it.
    error_class: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "error"


def _non_fallback_provider_error(exc: BaseException) -> str | None:
    """Return a request-authority error that must not cross providers.

    A fallback provider receives the same immutable PreparedToolSet.  Catalog
    or policy invalidation therefore cannot be repaired by changing models;
    sending the payload again would leak the already-stale schema a second
    time.  Prefer the structured class, while retaining message matching for
    OpenAI-compatible endpoints that do not preserve upstream error codes.
    """

    error_class = str(getattr(exc, "error_class", "") or "")
    error_code = str(getattr(exc, "code", "") or "")
    detail = str(exc)
    if (
        type(exc).__name__ == "ProviderDispatchUnknownError"
        or "provider_dispatch_unknown_after_handoff" in detail
        or error_code == "provider_invocation_conflict"
    ):
        return "provider_dispatch_unknown"
    for reason in ("tool_catalog_stale", "tool_policy_unavailable"):
        if error_class == reason or reason in detail:
            return reason
    return None


def _should_raise_provider_runtime_error(
    exc: BaseException,
    authority_error: str | None,
) -> bool:
    return (
        isinstance(exc, RuntimeError)
        and authority_error is None
        and not str(exc).startswith("provider_context_budget_exceeded")
    )


def _invalidate_stale_mcp_catalog(tool_registry: Any, tool_set: Any) -> None:
    """Drop every MCP source represented by the stale prepared payload."""

    invalidate = getattr(tool_registry, "invalidate_mcp_catalog_sources", None)
    if not callable(invalidate) or tool_set is None:
        return
    sources = {
        str(cap.ref.source)
        for cap in (*tool_set.direct, *tool_set.activated)
        if str(cap.ref.source).startswith("mcp:")
    }
    if sources:
        invalidate(sources)


@dataclass
class SubagentCompletionEvent(AgentEvent):
    """子代理并发驱动 WI-3.3 — 一个非阻塞子代理完成、其摘要在回合边界注入
    父上下文时发出。main.py 可转成 ws ``subagent_completion`` 供前端展示。"""

    run_id: str = ""
    task_id: str = ""
    kind: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "subagent_completion"


@dataclass
class ProviderChainFallbackEvent(AgentEvent):
    """P5-S2 Phase 3.6 — emitted when a provider in the chain fails
    transiently and the loop moves to the next provider.

    main.py forwards this as
    ``{type: "provider_chain_fallback", session_id, from, to, reason}``
    over the ws so the frontend can pop a diagnostic banner ("auto
    switched to backup B because A timed out").

    ``from_`` uses the trailing underscore because ``from`` is a Python
    keyword. Frontend serializer renames it to ``from`` over the wire
    (see main.py forwarder).
    """

    session_id: str = ""
    from_: str = ""
    to: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "provider_chain_fallback"


@dataclass
class ContextCompactedEvent(AgentEvent):
    """WI-1B-2 压缩可观测 — flag ``features.ctx_observability`` ON 时,上下文
    压缩命中后发出一条轻量事件。main.py 转成 ws
    ``{type: "context_compacted", reduction, tokens_in, tokens_out, model}``,
    前端在圈圈 gauge 附近浮一条 toast「已压缩,省 N token」(N = tokens_in -
    tokens_out)。flag OFF 时本事件**永不构造**(零开销,字节级 BC)。"""

    reduction: float = 0.0   # 节省比 0~1（middle 段 reduction_ratio）
    tokens_in: int = 0       # 压缩前 middle 段 token
    tokens_out: int = 0      # 压缩后摘要 token
    model: str = ""          # 摘要所用模型（可选）
    source_event_id: str = ""  # durable producer identity
    based_on_sample_id: str = ""  # exact measured sample compacted
    occurred_at: float = 0.0  # producer time for observability; not lineage authority

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "context_compacted"


@dataclass
class PipelineEvent(AgentEvent):
    """七步问题处理流水线观测事件（plans/2026-06-24-...）。

    type ∈ {chat_v2_intent, chat_v2_contradiction, chat_v2_evidence_gate,
    chat_v2_selfcheck, chat_v2_convergence}。仅 pipeline_observability=True 时 yield；
    main.py 据 .type 直接 send_json。flag off 时**永不构造**（零开销，BC）。"""

    payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # type 由构造方显式给（多种事件复用本类），不在此覆盖。
        pass


# ───────────────────── protocols for caller dependencies ─────────────────────


class _LLMRegistryProto(Protocol):
    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> ChatResponse: ...


class _ToolRegistryProto(Protocol):
    def schemas(self, enabled_toolsets: Optional[list[str]] = None) -> list[dict[str, Any]]: ...
    def dispatch(self, name: str, args: dict[str, Any], task_id: str) -> Any: ...


# ───────────────────── agent loop ─────────────────────

def _host_validated_terminal_event(
    loop: Any,
    response: ChatResponse,
    task_id: str,
    iteration: int,
    totals: dict[str, int],
) -> FinalEvent:
    """Finish a trusted zero-tool background Run after its host validation."""
    loop._gate.record_final_answer()
    if loop._tracer:
        loop._tracer.record({
            "kind": "end",
            "iter": iteration,
            "reason": response.stop_reason or "end_turn",
            "gate_summary": loop._gate.summary(),
            "host_validated_zero_tool_terminal": True,
        })
    return FinalEvent(
        type="final",
        task_id=task_id,
        iteration=iteration,
        content=response.content,
        reasoning_content=response.reasoning_content,
        stop_reason=response.stop_reason or "end_turn",
        total_input_tokens=totals["input"],
        total_output_tokens=totals["output"],
        total_cache_read_tokens=totals["cache_read"],
        total_cache_write_tokens=totals["cache_write"],
    )


async def _review_final_response_quality(
    loop: Any,
    response: ChatResponse,
    *,
    user_request: str | None,
    session_id: str,
    request_id: str,
    iteration: int,
    model: str,
    prepared_context: Any,
    current_tool_set: Any,
    totals: dict[str, int],
) -> str:
    """Apply the optional semantic concise-completeness gate outside AgentLoop."""
    final_content = response.content or ""
    gate = loop._response_quality_gate
    if gate is None or not user_request or not final_content:
        return final_content
    try:
        quality_messages = gate.build_messages(
            user_request=user_request,
            candidate_response=final_content,
        )
        quality_provider = getattr(
            loop.llm,
            "_provider",
            getattr(loop.llm, "_active_provider", loop.llm),
        )
        quality_response = await loop._run_context_attempt(
            provider=quality_provider,
            session_id=session_id,
            request_id=request_id,
            attempt_id=f"{request_id}:iter:{iteration}:response-quality",
            purpose="response_quality",
            messages=quality_messages,
            tools=None,
            fallback_model=model,
            prepared_context=prepared_context,
            current_tool_set=current_tool_set,
            invoke=lambda: traced_call(
                name="response_quality.check",
                kind=SpanKind.LLM,
                lifecycle_stage="llm",
                attributes={
                    "iteration": iteration,
                    "gate": "response_quality",
                    "model": model,
                },
                invoke=lambda: loop.llm.chat_with_fallback(
                    quality_messages,
                    tools=None,
                    model=model,
                    max_tokens=1024,
                    temperature=0.0,
                    response_format=gate.response_format(),
                ),
            ),
        )
        usage = getattr(quality_response, "usage", None)
        if usage is not None:
            totals["input"] += int(getattr(usage, "input_tokens", 0) or 0)
            totals["output"] += int(getattr(usage, "output_tokens", 0) or 0)
            totals["cache_read"] += int(
                getattr(usage, "cache_read_tokens", 0) or 0
            )
            totals["cache_write"] += int(
                getattr(usage, "cache_write_tokens", 0) or 0
            )
        decision = gate.resolve(
            original_response=final_content,
            review_response=str(getattr(quality_response, "content", "") or ""),
        )
        logger.info(
            "response_quality_checked sid=%s checked=%s revised=%s "
            "missing=%d reason=%s",
            session_id,
            decision.checked,
            decision.revised,
            len(decision.missing_items),
            decision.reason,
        )
        return decision.text
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "response_quality_check_failed sid=%s err=%s "
            "— preserving original response",
            session_id,
            str(exc)[:200],
        )
        return final_content


class AgentLoop:
    """ReAct-style driver around LLMRegistry + ToolRegistry."""

    def __init__(
        self,
        llm_registry: _LLMRegistryProto,
        tool_registry: _ToolRegistryProto,
        *,
        max_iterations: int = 20,
        budget_checker: Optional[DailyBudget] = None,
        default_model: Optional[str] = None,
        completion_probe: Optional[
            Callable[[str], Awaitable[list[dict[str, Any]]]]
        ] = None,
        max_completion_nudges: int = 2,
        activity_store: Optional[Any] = None,
        signature_repeat_threshold: Optional[int] = None,
        termination_gate: Optional[TerminationGate] = None,
        context_manager: Optional[ContextManager] = None,
        # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门 + ReceiptStore 拿 ledger
        verify_gate: Optional[Any] = None,        # deskpet.agent.verify_gate.VerifyGate
        receipt_store: Optional[Any] = None,      # deskpet.tools.receipt_store.ReceiptStore
        max_verify_nudges: int = 2,               # PRD D6: 3 次失败强退
        # WI-B3 Companion+Code v1: /goal command 末轮 LLM-judged rebound.
        # 两个都 None (默认) → BC，跳过整段 goal-check 块。
        session_goal_store: Optional[Any] = None,  # deskpet.agent.goal_store.SessionGoalStore
        goal_checker: Optional[Any] = None,        # deskpet.agent.goal_checker.GoalChecker
        # WI-2.1 structured reflection: when True, _REFLECTION_INSTRUCTION is
        # appended to verify-gate rebound + selfcheck tier2/tier3 system msgs.
        # Default False = BC (flag off → byte-identical behaviour to pre-WI-2.1).
        structured_reflection: bool = False,
        # WI-2.4 external evaluator: cross-persona quality judge for high-consequence
        # goals. None (default) = BC (0 extra LLM calls, skip entirely).
        external_evaluator: Optional[Any] = None,  # deskpet.agent.external_evaluator.ExternalEvaluator
        # WI-4.0 compaction: ContextCompressor to call when prompt tokens near cap.
        # None (default) = BC (compressor not injected → zero new behaviour).
        # When non-None, should_compress() and compress() are called in the loop.
        compressor: Optional[Any] = None,  # deskpet.agent.context_compressor.ContextCompressor
        context_projector: Optional[Any] = None,
        context_snapshot_store: Optional[Any] = None,
        context_segment_store: Optional[Any] = None,
        context_attempt_store: Optional[Any] = None,
        compression_model_resolver: Optional[Any] = None,
        compression_model: str = "follow_session",
        compression_model_provider: Optional[Callable[[], str]] = None,
        # WI-1B-2 压缩可观测 (features.ctx_observability). False (默认) = 字节级 BC:
        # 压缩成功路径不 emit metrics、不 yield ContextCompactedEvent。True 时压缩
        # 命中额外 record 一条 metrics + yield 一条轻量事件供 main.py 转 ws → 前端 toast。
        ctx_observability: bool = False,
        # WI-4.2 skill remount: inject SkillLoader + SkillMatcher for post-compaction
        # skill body re-inline.  Both default None → BC (no remount, zero overhead).
        # When non-None and compaction fires, _remount_skills() is called to re-insert
        # the bodies of skills used this run as a single role=system block.
        skill_loader: Optional[Any] = None,   # deskpet.skills.loader.SkillLoader
        skill_matcher: Optional[Any] = None,  # deskpet.skills.skill_matcher.SkillMatcher
        # WI-4b pre-flush: 压缩真正摘掉中段前,把"当前任务态"写进 L1 文件记忆,
        # 跨 session 记住任务(frozen-snapshot → 只对下个 session 生效)。None (默认)
        # → 不 flush(BC)。每个 run 最多 flush 一次(限频,防刷爆 MEMORY.md 50KB cap)。
        file_memory: Optional[Any] = None,  # deskpet.memory.file_memory.FileMemory
        force_finish_via_tool_choice: bool = True,
        tracer: Optional[Any] = None,
        trace_store: Optional[TraceStore] = None,
        workflow_service: Optional[Any] = None,
        session_todo_getter: Optional[
            Callable[[str], Awaitable[list[dict[str, Any]]]]
        ] = None,
        # WI-OH-4 记忆 self-curation nudge: MemoryCurator + 频率门控。
        # curator None (默认) → 不调 nudge（BC，零行为变更）。非 None 时每
        # curation_nudge_every_n_turns 个回合在 FinalEvent 后 fire-and-forget
        # 一次 nudge（异步，不挡主回合，照 vector_worker 模式）。
        memory_curator: Optional[Any] = None,  # deskpet.memory.curation.MemoryCurator
        curation_nudge_every_n_turns: int = 8,
        provider_workload_context_factory: Optional[Callable[[str], Any]] = None,
        provider_workload_router: Optional[Any] = None,
        # ─── 七步问题处理流水线 IN-LOOP 三闸（plans/2026-06-24-problem-pipeline）。
        # 全 None/False → 跳过所有 pipeline 分支（kill-switch 回退到今天的链路，BC）。
        evidence_gate: Optional[Any] = None,           # deskpet.agent.evidence_gate.EvidenceGate
        self_check_gate: Optional[Any] = None,         # deskpet.agent.self_check_gate.SelfCheckGate（build_agent 内构造后传）
        convergence_report_on_stop: bool = False,      # Step7：True → __init__ 末尾用 self._gate 自建 ConvergenceController
        pipeline_problem_type: Optional[str] = None,   # Step1 产出的 problem_type（喂 Step6 选档）
        pipeline_needs_investigation: bool = False,    # IntentCard.needs_investigation（喂 Step2）
        pipeline_observability: bool = False,          # 发 chat_v2_evidence_gate / _selfcheck / _convergence 事件
        response_quality_gate: Optional[Any] = None,
    ) -> None:
        self.llm = llm_registry
        self.tools = tool_registry
        # WI-OH-4 记忆自策展 nudge（BC: None → 不调 nudge）。每 session 轮次
        # 计数器现挂在 curator 单例上（见 curation.py bump_turn）——本 loop 每回合
        # 重建，计数器若挂这里会每回合归零、永不到阈值（真测抓出）。阈值仍留这里。
        self._memory_curator = memory_curator
        self._curation_every = max(1, int(curation_nudge_every_n_turns or 8))
        self._provider_workload_context_factory = provider_workload_context_factory
        self._provider_workload_router = provider_workload_router
        self.max_iterations = max_iterations
        self.budget_checker = budget_checker
        self.default_model = default_model
        # P5-S2 Hook A: completion guard. If supplied, ``completion_probe``
        # is called when the LLM tries to finalize (stop_reason ≠ tool_use)
        # to check whether session-level work (todos) is actually done.
        # The probe receives ``session_id`` and returns the list of
        # incomplete todo dicts ({content, activeForm, status, ...}).
        # If non-empty AND we still have nudges in budget, the loop
        # injects a system message reminding the LLM and re-runs the
        # iteration instead of finalizing. ``max_completion_nudges``
        # caps the rebound count to prevent infinite loops when the LLM
        # genuinely refuses to continue. Set to 0 to disable the hook.
        self.completion_probe = completion_probe
        self.max_completion_nudges = max_completion_nudges
        # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门
        self.verify_gate = verify_gate
        self.receipt_store = receipt_store
        self.max_verify_nudges = max_verify_nudges
        # WI-B3 Companion+Code v1: /goal store + checker (BC: 两者皆 None → skip)
        self.session_goal_store = session_goal_store
        self.goal_checker = goal_checker
        # WI-2.1 structured reflection flag (BC: False → no injection)
        self.structured_reflection = structured_reflection
        # WI-2.4 external evaluator: cross-persona quality judge for high-consequence
        # goals (BC: None → skip entirely, 0 extra LLM calls).
        self.external_evaluator = external_evaluator
        # WI-4.0 compaction: ContextCompressor (BC: None → skip entirely).
        # When non-None, loop calls should_compress() + compress() after budget check.
        self.compressor = compressor
        self.context_projector = context_projector
        self.context_snapshot_store = context_snapshot_store
        self.context_segment_store = context_segment_store
        self.context_attempt_store = context_attempt_store
        self.compression_model_resolver = compression_model_resolver
        self.compression_model = str(compression_model or "follow_session")
        self.compression_model_provider = compression_model_provider
        # WI-1B-2 压缩可观测 flag (BC: False → 压缩路径零额外行为)。
        self.ctx_observability = bool(ctx_observability)
        # WI-4.2 skill remount (BC: both None → skip entirely).
        # When non-None and compaction fires, _remount_skills() re-inlines skill bodies.
        self.skill_loader = skill_loader
        self.skill_matcher = skill_matcher
        # WI-4b pre-flush L1 句柄（BC: None → 不 flush）。
        self.file_memory = file_memory
        self.force_finish_via_tool_choice = force_finish_via_tool_choice
        self._tracer = tracer
        self._trace_store = trace_store
        self._workflow_service = workflow_service
        self.session_todo_getter = session_todo_getter
        # P5-S2 Phase 3.3: same-(name, args) repeat detection. When set,
        # the loop checks the activity store's per-session
        # ``tool_signature_window`` BEFORE dispatching each tool_call —
        # if the same signature already shows ``>= _REPEAT_THRESHOLD - 1``
        # consecutive prior calls, we suppress the dispatch and inject a
        # system nudge so the LLM gets a chance to change tactics.
        # Pre-Phase-3 callers leave this as None and the branch is a
        # no-op (verified by ``test_no_activity_store_means_no_repeat_detection``).
        self.activity_store = activity_store
        # P5-S2 Phase 6: per-instance override for the consecutive
        # same-(name, args) repeat threshold. Defaults to module
        # constant ``_REPEAT_THRESHOLD`` (3) so legacy callers keep
        # the existing behaviour. main.py wires
        # ``[supervisor].tool_signature_repeat_threshold`` here.
        self._signature_repeat_threshold = (
            int(signature_repeat_threshold)
            if signature_repeat_threshold is not None
            else _REPEAT_THRESHOLD
        )
        # P6 Phase 6 — TerminationGate is always wired in. Callers may
        # inject an explicit gate; otherwise a default gate is created
        # with max_turns = self.max_iterations. The legacy ``_gate is
        # None`` branch is gone — every code path goes through the gate.
        #
        # The soft selfcheck tier messages (_SELFCHECK_*) are kept in
        # place — they are complementary to the gate, not replaced by it.
        # The gate provides HARD termination semantics on top of those
        # nudges. The legacy _TOOL_BUDGET_HARD_MSG soft cap was removed
        # in Phase 6 (gate handles tool budget hard cap directly).
        self._gate: TerminationGate = (
            termination_gate
            if termination_gate is not None
            else TerminationGate(GateConfig(max_turns=self.max_iterations))
        )

        # P6 Phase 6 — ContextManager is always wired in. Same shape as
        # _gate above: caller may inject one, otherwise a default is
        # created. The tool-result write site delegates truncation to
        # ctx.record_tool_result (which honours skip_truncation_for_tools
        # — the G1 fix). The legacy ``_ctx is None`` branch is gone.
        self._ctx: ContextManager = (
            context_manager
            if context_manager is not None
            else ContextManager()
        )

        # ─── 七步问题处理流水线 IN-LOOP 三闸赋值（plans/2026-06-24-problem-pipeline）。
        # 必须在 self._gate 构造（上方）之后——ConvergenceController 依赖 self._gate。
        self._evidence_gate = evidence_gate
        self._self_check_gate = self_check_gate
        self._pipeline_problem_type = pipeline_problem_type
        self._pipeline_needs_investigation = bool(pipeline_needs_investigation)
        self._pipeline_observability = bool(pipeline_observability)
        self._response_quality_gate = response_quality_gate
        self._evidence_nudges_used = 0   # Step2 nudge 计数
        # ⚠️ R1（round-2）：弃用绝对长度切片基线 → 改布尔累积标志（对 compaction 免疫）。
        # run() 开头重设；本 run dispatch 出取证工具 → 置 True。
        self._evidence_gathered = False
        self._history_tool_names: set[str] = set()
        # Step7 ConvergenceController 自建（依赖 self._gate）：
        self._convergence_controller = None
        if convergence_report_on_stop:
            from deskpet.agent.convergence_controller import ConvergenceController  # noqa: PLC0415
            self._convergence_controller = ConvergenceController(
                self._gate, report_on_stop=True,
            )

    @staticmethod
    def _provider_attempt_identity(provider: Any, fallback_model: str = "") -> tuple[str, str, str, str]:
        provider_id = str(
            getattr(provider, "provider_id", "")
            or getattr(provider, "id", "")
            or getattr(provider, "name", "")
            or type(provider).__name__
        )
        model_id = str(getattr(provider, "model", "") or fallback_model or "")
        adapter_id = str(
            getattr(provider, "adapter_id", "")
            or ("openai-compatible" if hasattr(provider, "base_url") else type(provider).__name__)
        )
        adapter_version = str(getattr(provider, "adapter_version", "") or "v1")
        return provider_id, model_id, adapter_id, adapter_version

    async def _persist_attempt_tool_context(
        self,
        *,
        session_id: str,
        request_id: str,
        attempt_id: str,
        prepared_context: Any,
        current_tool_set: Any,
        report: Any,
    ) -> None:
        """Persist the exact selected adapter/wire facts before transport.

        A failed or conflicting CAS is fail-closed: the caller must not invoke
        the provider.  Capability and DB revisions stay in their independent
        domains.
        """

        if (
            self.context_snapshot_store is None
            or prepared_context is None
            or current_tool_set is None
        ):
            return
        scope_store = getattr(self.tools, "capability_scope_store", None)
        if scope_store is None:
            return
        record = scope_store.get(
            current_tool_set.scope_id,
            session_id=session_id,
            request_id=request_id,
        )
        if record is None:
            raise RuntimeError("tool_context_persist_failed:scope_missing")
        handle = record.snapshot_handle or getattr(
            prepared_context, "active_snapshot_handle", None
        )
        # Ordinary conversations intentionally have no durable task snapshot.
        if handle is None:
            return

        schema_hashes = {
            cap.ref.name: cap.ref.schema_hash
            for cap in (*current_tool_set.direct, *current_tool_set.activated)
        }
        summary = {
            "direct_names": [cap.ref.name for cap in current_tool_set.direct],
            "activated_names": [cap.ref.name for cap in current_tool_set.activated],
            "schema_hashes": schema_hashes,
            "selection_reasons": [
                {
                    "name": decision.name,
                    "disposition": decision.disposition,
                    "reason": decision.reason,
                }
                for decision in current_tool_set.decisions
            ],
            "schema_tokens": int(report.tool_tokens),
            "provider_adapter_id": report.provider_id or report.adapter_id,
            "provider_adapter_version": report.adapter_version,
            "wire_payload_hash": report.wire_tool_hash,
            "wire_tokens": int(report.tool_tokens),
            "attempt_id": attempt_id,
            "adapter_state": "prepared",
            "persisted_tool_scope_revision": int(current_tool_set.revision),
            "registry_revision": int(current_tool_set.registry_revision),
            "policy_fingerprint": current_tool_set.policy_fingerprint,
            "schema_fingerprint": current_tool_set.schema_fingerprint,
        }

        from deskpet.memory.context_snapshot_store import (
            SnapshotCommitCancelled,
            SnapshotConflictError,
            await_snapshot_commit_ack,
        )

        async with scope_store.lock_for(current_tool_set.scope_id):
            current_record = scope_store.get(
                current_tool_set.scope_id,
                session_id=session_id,
                request_id=request_id,
            )
            if current_record is None:
                raise RuntimeError("tool_context_persist_failed:scope_expired")
            handle = current_record.snapshot_handle or handle

            async def _write(expected_revision: int):
                task = asyncio.create_task(
                    self.context_snapshot_store.update_tool_context_cas(
                        session_id,
                        handle.task_scope_id,
                        expected_row_revision=expected_revision,
                        prepared_toolset_summary=summary,
                    )
                )
                return await await_snapshot_commit_ack(task)

            try:
                receipt = await _write(int(handle.row_revision))
            except SnapshotConflictError:
                latest = await self.context_snapshot_store.get(
                    session_id, handle.task_scope_id
                )
                if latest is None:
                    raise RuntimeError("tool_context_persist_failed:snapshot_missing")
                persisted = latest.prepared_toolset_summary
                if (
                    persisted.get("schema_fingerprint")
                    not in (None, "", current_tool_set.schema_fingerprint)
                    or persisted.get("persisted_tool_scope_revision")
                    not in (None, current_tool_set.revision)
                ):
                    raise RuntimeError("tool_context_persist_failed:cas_conflict")
                try:
                    handle = latest.handle
                    receipt = await _write(int(handle.row_revision))
                except SnapshotConflictError as exc:
                    raise RuntimeError("tool_context_persist_failed:cas_conflict") from exc
            except SnapshotCommitCancelled as exc:
                if exc.receipt is not None:
                    scope_store.advance_snapshot_handle_prevalidated(
                        current_tool_set.scope_id, exc.receipt.new_handle
                    )
                    prepared_context.active_snapshot_handle = exc.receipt.new_handle
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError("tool_context_persist_failed") from exc

            scope_store.advance_snapshot_handle_prevalidated(
                current_tool_set.scope_id, receipt.new_handle
            )
            prepared_context.active_snapshot_handle = receipt.new_handle

    async def _persist_activation_tool_context_locked(
        self,
        *,
        session_id: str,
        scope_store: Any,
        scope_record: Any,
        candidate: Any,
        prepared_context: Any,
        tool_payload: Any,
    ) -> Any:
        """Persist one candidate activation before making it authoritative.

        The caller owns the capability-scope lock.  A failed CAS leaves the
        authoritative scope and the caller's local tool-set untouched.  If
        cancellation arrives after SQLite committed, only the diagnostic
        snapshot handle advances; the candidate is deliberately not activated.
        """

        if self.context_snapshot_store is None or prepared_context is None:
            return None
        record_handle = getattr(scope_record, "snapshot_handle", None)
        prepared_handle = getattr(prepared_context, "active_snapshot_handle", None)
        handles = [item for item in (record_handle, prepared_handle) if item is not None]
        if not handles:
            return None
        handle = max(handles, key=lambda item: int(getattr(item, "row_revision", 0)))
        summary = self._prepared_toolset_summary(candidate)
        summary.update(
            {
                "selection_reasons": [
                    {
                        "name": decision.name,
                        "disposition": decision.disposition,
                        "reason": decision.reason,
                    }
                    for decision in tuple(getattr(candidate, "decisions", ()) or ())
                ],
                "schema_tokens": int(getattr(tool_payload, "wire_tokens", 0) or 0),
                "wire_tokens": int(getattr(tool_payload, "wire_tokens", 0) or 0),
                # Snapshot schema intentionally exposes only the stable
                # canonical/prepared adapter states.  The candidate is fully
                # prepared before this CAS and becomes authoritative only
                # after the write receipt is acknowledged below.
                "adapter_state": "prepared",
            }
        )
        from deskpet.memory.context_snapshot_store import (
            SnapshotCommitCancelled,
            SnapshotConflictError,
            await_snapshot_commit_ack,
        )

        write_task = asyncio.create_task(
            self.context_snapshot_store.update_tool_context_cas(
                session_id,
                handle.task_scope_id,
                expected_row_revision=int(handle.row_revision),
                prepared_toolset_summary=summary,
            )
        )
        try:
            return await await_snapshot_commit_ack(write_task)
        except SnapshotConflictError as exc:
            raise RuntimeError("tool_activation_snapshot_conflict") from exc
        except SnapshotCommitCancelled as exc:
            if exc.receipt is not None:
                scope_store.advance_snapshot_handle_prevalidated(
                    candidate.scope_id, exc.receipt.new_handle
                )
                prepared_context.active_snapshot_handle = exc.receipt.new_handle
            raise

    async def _begin_context_attempt(
        self,
        *,
        provider: Any,
        session_id: str,
        request_id: str,
        attempt_id: str,
        purpose: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        fallback_model: str,
        prepared_context: Any,
        current_tool_set: Any,
        compression: Mapping[str, Any] | None = None,
    ) -> ProviderAttemptOptions | None:
        if self.context_attempt_store is None:
            return None
        from agent.context_report import (
            build_prepared_attempt_report,
            estimate_selected_attempt_budget,
        )

        provider_id, model_id, adapter_id, adapter_version = (
            self._provider_attempt_identity(provider, fallback_model)
        )
        initial_budget = getattr(prepared_context, "request_budget", None)
        actual_budget = estimate_selected_attempt_budget(
            provider=provider,
            model_id=model_id,
            messages=messages,
            tools=tools,
            generation_reserve=int(
                getattr(initial_budget, "generation_reserve", 0) or 0
            ),
            attachment_tokens=int(
                getattr(prepared_context, "attachment_tokens", 0) or 0
            ),
        )
        report = build_prepared_attempt_report(
            session_id=session_id,
            request_id=request_id,
            attempt_id=attempt_id,
            purpose=purpose,
            messages=messages,
            tools=tools,
            provider_id=provider_id,
            model_id=model_id,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            prepared_context=prepared_context,
            budget=actual_budget,
            compression=compression,
        )
        self.context_attempt_store.plan(report)
        if not actual_budget.fits:
            logger.warning(
                "provider_context_budget_exceeded_detail sid=%s request=%s "
                "planned=%d effective=%d messages=%d tools=%d attachments=%d reserve=%d",
                session_id,
                request_id,
                actual_budget.planned_input_tokens,
                actual_budget.effective_input_budget,
                actual_budget.messages_tokens,
                actual_budget.tool_tokens,
                actual_budget.attachment_tokens,
                actual_budget.generation_reserve,
            )
            self.context_attempt_store.transition(
                session_id,
                request_id,
                attempt_id,
                "failed",
                reasons=("provider_context_budget_exceeded",),
            )
            raise RuntimeError("provider_context_budget_exceeded")
        try:
            await self._persist_attempt_tool_context(
                session_id=session_id,
                request_id=request_id,
                attempt_id=attempt_id,
                prepared_context=prepared_context,
                current_tool_set=current_tool_set,
                report=report,
            )
        except asyncio.CancelledError:
            self.context_attempt_store.transition(
                session_id, request_id, attempt_id, "cancelled_before_send"
            )
            raise
        except Exception as exc:
            self.context_attempt_store.transition(
                session_id,
                request_id,
                attempt_id,
                "failed",
                reasons=(str(exc)[:200],),
            )
            raise
        return ProviderAttemptOptions(
            cache_boundary=getattr(prepared_context, "stable_prefix_boundary", None),
            cache_fingerprint=getattr(
                prepared_context, "stable_prefix_fingerprint", None
            ),
            purpose=purpose,
            session_id=session_id,
            request_id=request_id,
            attempt_id=attempt_id,
        )

    async def _run_context_attempt(
        self,
        *,
        invoke: Callable[[], Awaitable[Any]],
        provider: Any,
        session_id: str, request_id: str,
        attempt_id: str, purpose: str,
        messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
        fallback_model: str,
        prepared_context: Any, current_tool_set: Any,
        compression: Mapping[str, Any] | None = None,
    ) -> Any:
        options = await self._begin_context_attempt(
            provider=provider, session_id=session_id,
            request_id=request_id, attempt_id=attempt_id,
            purpose=purpose, messages=messages, tools=tools,
            fallback_model=fallback_model,
            prepared_context=prepared_context, current_tool_set=current_tool_set,
            compression=compression,
        )
        raw_invoke = invoke
        invoke = lambda: _coordinate_agent_provider_call(
            self, provider, request_id, attempt_id, purpose,
            messages, tools, fallback_model, raw_invoke,
        )
        if options is None:
            return await invoke()
        from agent.context_report import finish_current_attempt
        with context_attempt_scope(options):
            try:
                result = await invoke()
            except asyncio.CancelledError:
                finish_current_attempt("cancelled")
                raise
            except BaseException as exc:
                finish_current_attempt("failed", reason=type(exc).__name__)
                raise
            usage = getattr(result, "usage", None)
            if usage is None and isinstance(result, Mapping):
                usage = result.get("usage")
            finish_current_attempt("succeeded", usage=usage)
            return result

    async def _iterate_context_attempt(
        self,
        *,
        iterator: Callable[[], AsyncIterator[Any]],
        provider: Any,
        session_id: str, request_id: str,
        attempt_id: str, purpose: str,
        messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
        fallback_model: str,
        prepared_context: Any, current_tool_set: Any,
        compression: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        options = await self._begin_context_attempt(
            provider=provider, session_id=session_id,
            request_id=request_id, attempt_id=attempt_id,
            purpose=purpose, messages=messages, tools=tools,
            fallback_model=fallback_model,
            prepared_context=prepared_context, current_tool_set=current_tool_set,
            compression=compression,
        )
        raw_iterator = iterator
        iterator = lambda: _coordinate_agent_provider_stream(
            self, provider, request_id, attempt_id, purpose,
            messages, tools, fallback_model, raw_iterator,
        )
        if options is None:
            async for item in iterator():
                yield item
            return
        from agent.context_report import finish_current_attempt

        final_usage: Any = None
        with context_attempt_scope(options):
            try:
                async for item in iterator():
                    if isinstance(item, Mapping) and item.get("type") == "final":
                        final_usage = item.get("usage")
                    yield item
            except asyncio.CancelledError:
                finish_current_attempt("cancelled")
                raise
            except BaseException as exc:
                finish_current_attempt("failed", reason=type(exc).__name__)
                raise
            finish_current_attempt("succeeded", usage=final_usage)

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        task_id: Optional[str] = None,
        tools_filter: Optional[list[str]] = None,
        tool_names_filter: Optional[list[str]] = None,
        model: Optional[str] = None,
        session_id: str = "default",
        stream: bool = False,
        provider_chain: Optional[list[Any]] = None,
        loop_user_request: Optional[str] = None,
        is_sentinel_run: bool = False,
        trace_context: Optional[SpanContext] = None,
        trace_request_id: Optional[str] = None,
        trace_turn_id: Optional[str] = None,
        prepared_context: Optional[Any] = None,
        context_request_id: Optional[str] = None,
        launch_operation_id: Optional[str] = None,
        provider_launch_snapshot: Optional[ProviderLaunchSnapshot] = None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Run the harness, optionally recording one closed structured trace tree."""

        tid = task_id or new_task_id()
        if self._trace_store is None:
            async for event in self._run_impl(
                messages,
                task_id=tid,
                tools_filter=tools_filter,
                tool_names_filter=tool_names_filter,
                model=model,
                session_id=session_id,
                stream=stream,
                provider_chain=provider_chain,
                loop_user_request=loop_user_request,
                is_sentinel_run=is_sentinel_run,
                prepared_context=prepared_context,
                context_request_id=context_request_id,
                launch_operation_id=launch_operation_id,
                provider_launch_snapshot=provider_launch_snapshot,
                **llm_kwargs,
            ):
                yield event
            return

        try:
            trace = await HarnessTraceSession.start(
                self._trace_store,
                session_id=session_id,
                task_id=tid,
                parent_context=trace_context,
                request_id=trace_request_id or context_request_id or tid,
                turn_id=trace_turn_id,
                message_count=len(messages),
            )
        except TraceInstrumentationError as exc:
            yield ErrorEvent(
                type="error",
                task_id=tid,
                iteration=0,
                reason="trace_store_unavailable",
                detail=str(exc),
            )
            return

        impl = self._run_impl(
            messages,
            task_id=tid,
            tools_filter=tools_filter,
            tool_names_filter=tool_names_filter,
            model=model,
            session_id=session_id,
            stream=stream,
            provider_chain=provider_chain,
            loop_user_request=loop_user_request,
            is_sentinel_run=is_sentinel_run,
            prepared_context=prepared_context,
            context_request_id=context_request_id,
            launch_operation_id=launch_operation_id,
            provider_launch_snapshot=provider_launch_snapshot,
            **llm_kwargs,
        )
        terminal_status: str = SpanStatus.OK
        terminal_error: BaseException | dict[str, str] | None = None
        terminal_event_seen = False
        try:
            while True:
                try:
                    with trace.activate():
                        event = await anext(impl)
                except StopAsyncIteration:
                    break
                if isinstance(event, ErrorEvent):
                    terminal_status = SpanStatus.ERROR
                    terminal_error = {
                        "reason": event.reason,
                        "detail": event.detail,
                    }
                    terminal_event_seen = True
                elif isinstance(event, FinalEvent):
                    terminal_event_seen = True
                yield event
        except asyncio.CancelledError as exc:
            terminal_status = (
                SpanStatus.OK if terminal_event_seen else SpanStatus.CANCELLED
            )
            terminal_error = None if terminal_event_seen else {
                "type": type(exc).__name__,
                "reason": "consumer_cancelled_before_terminal",
            }
            raise
        except GeneratorExit as exc:
            terminal_status = (
                SpanStatus.OK if terminal_event_seen else SpanStatus.CANCELLED
            )
            terminal_error = None if terminal_event_seen else {
                "type": type(exc).__name__,
                "reason": "consumer_closed_before_terminal",
            }
            raise
        except BaseException as exc:
            terminal_status = SpanStatus.ERROR
            terminal_error = exc
            raise
        finally:
            try:
                with trace.activate():
                    await impl.aclose()
            finally:
                try:
                    await trace.close(terminal_status, error=terminal_error)
                except TraceInstrumentationError as exc:
                    logger.error(
                        "agent_loop_trace_close_failed sid=%s tid=%s error=%s",
                        session_id,
                        tid,
                        exc,
                    )

    async def _run_impl(
        self,
        messages: list[dict[str, Any]],
        *,
        task_id: Optional[str] = None,
        tools_filter: Optional[list[str]] = None,
        tool_names_filter: Optional[list[str]] = None,
        model: Optional[str] = None,
        session_id: str = "default",
        stream: bool = False,
        provider_chain: Optional[list[Any]] = None,
        loop_user_request: Optional[str] = None,
        is_sentinel_run: bool = False,
        prepared_context: Optional[Any] = None,
        context_request_id: Optional[str] = None,
        launch_operation_id: Optional[str] = None,
        provider_launch_snapshot: Optional[ProviderLaunchSnapshot] = None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Drive ReAct; ``stream`` emits deltas when the registry supports
        ``chat_with_fallback_stream`` and otherwise preserves non-streaming.
        """
        host_validated_zero_tool_terminal = bool(llm_kwargs.pop("host_validated_zero_tool_terminal", False))
        if (launch_operation_id is None) != (provider_launch_snapshot is None):
            raise ValueError("launch operation id and snapshot must be paired")
        launch_call_kwargs = {} if launch_operation_id is None else dict(
            launch_operation_id=launch_operation_id,
            provider_launch_snapshot=provider_launch_snapshot,
        )
        scoped_evidence = llm_kwargs.pop("_scoped_evidence", None)
        external_feedback = dict(llm_kwargs.pop("_external_tool_feedback", {}) or {})
        tid = task_id or new_task_id()
        self._current_tid = tid  # 供 _pipeline_event 构造观测事件用（plans/2026-06-24-...）
        working_messages: list[dict[str, Any]] = list(messages)
        context_metadata_enabled = prepared_context is not None
        if context_metadata_enabled:
            working_messages = _tag_run_input_messages(
                working_messages, task_id=tid
            )
        # ⚠️ R1（round-2，plans/2026-06-24-...）：Step2 取证布尔累积标志，对 compaction 免疫
        # （compaction 整体替换 working_messages 不会改这个已置位的布尔）。run() 开头重设 +
        # 快照 history 注入的旧 tool name → 本 run 只认「新 dispatch 且不在 history 快照里」的取证。
        self._evidence_gathered = bool(external_feedback.get("evidence_gathered"))
        self._evidence_nudges_used = int(external_feedback.get("evidence_nudges_used", 0))
        self._history_tool_names = set(self._collect_tool_names(working_messages))
        current_tool_set = (
            getattr(prepared_context, "tool_set", None)
            if prepared_context is not None
            else None
        )
        if current_tool_set is not None:
            tool_schemas = list(current_tool_set.logical_schemas())
        else:
            tool_schemas = self.tools.schemas(enabled_toolsets=tools_filter)
        if tool_names_filter is not None:
            allowed_tool_names = set(tool_names_filter)
            tool_schemas = [
                schema
                for schema in tool_schemas
                if str(
                    (
                        schema.get("function", {})
                        if isinstance(schema.get("function"), dict)
                        else schema
                    ).get("name", "")
                )
                in allowed_tool_names
            ]
        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        use_model = model or self.default_model

        # ──────────────── P5-S2 Phase 3: provider chain mode ────────────────
        #
        # When ``provider_chain`` is supplied, AgentLoop walks the chain
        # itself instead of delegating to ``self.llm.chat_with_fallback``.
        # Each provider is tried in order; LLMProviderError (transient)
        # falls through to the next; permanent errors (args_parse_error
        # etc. — surfaced via ChatResponse, NOT exceptions) short-circuit
        # the chain because the next provider would just see the same
        # broken request and fail identically.
        #
        # Empty chain is an actionable user-facing error — emit it once
        # at the top of run() so the loop never tries to call anything.
        chain_mode = provider_chain is not None
        if chain_mode and not provider_chain:
            yield ErrorEvent(
                type="error",
                task_id=tid,
                iteration=0,
                reason="no_provider_configured",
                detail=(
                    "未配置任何 LLM provider。"
                    "请打开设置 → LLM Providers → 添加"
                ),
            )
            return
        # Detect streaming capability lazily — agent loop tests use a
        # mock registry that may only define chat_with_fallback.
        stream_capable = stream and callable(
            getattr(self.llm, "chat_with_fallback_stream", None)
        )

        # Context OS plans one common transcript against the smallest usable
        # provider window before the first transport attempt. This keeps
        # fallback reversible: a smaller backup never receives a request that
        # was only valid for the primary, and it does not terminate the chain
        # merely because its window was discovered late.
        _chain_budget_rescue_pending = False
        if context_metadata_enabled and chain_mode and provider_chain:
            from agent.context_report import estimate_selected_attempt_budget

            initial_budget = getattr(prepared_context, "request_budget", None)
            reserve = int(
                getattr(initial_budget, "generation_reserve", 0)
                or llm_kwargs.get("max_tokens", 0)
                or 0
            )
            attachment_tokens = int(
                getattr(prepared_context, "attachment_tokens", 0) or 0
            )
            candidate_budgets = [
                estimate_selected_attempt_budget(
                    provider=provider,
                    model_id=str(getattr(provider, "model", "") or use_model or ""),
                    messages=working_messages,
                    tools=tool_schemas or None,
                    generation_reserve=reserve,
                    attachment_tokens=attachment_tokens,
                )
                for provider in provider_chain
            ]
            safe_budget = min(
                candidate_budgets,
                key=lambda item: item.effective_input_budget,
            )
            try:
                replan_for_budget = getattr(
                    prepared_context, "replan_for_budget", None
                )
                used_common_replan = callable(replan_for_budget)
                if callable(replan_for_budget):
                    replanned = await asyncio.wait_for(
                        replan_for_budget(
                            context_window=safe_budget.context_window,
                            effective_pct=safe_budget.effective_pct,
                            generation_reserve=safe_budget.generation_reserve,
                        ),
                        timeout=10.0,
                    )
                    prepared_context = getattr(
                        replanned, "prepared_context", replanned
                    )
                    if tuple(
                        getattr(prepared_context, "coverage_compaction_jobs", ())
                        or ()
                    ):
                        if self.compressor is None:
                            raise RuntimeError("coverage_compaction_owner_unavailable")
                        chain_cycle_id = (
                            f"{context_request_id or tid}:chain-safe:1"
                        )
                        resolution = self._resolve_compression_model(
                            provider_chain,
                            requested_model=self._requested_compression_model(),
                        )
                        await self._flush_context_snapshot(
                            cycle_id=chain_cycle_id,
                            session_id=session_id,
                            request_id=context_request_id or tid,
                            working_messages=_tag_run_input_messages(
                                list(prepared_context.messages), task_id=tid
                            ),
                            prepared_context=prepared_context,
                            current_tool_set=getattr(
                                prepared_context, "tool_set", None
                            ),
                        )
                        await self._execute_coverage_compaction_jobs(
                            cycle_id=chain_cycle_id,
                            prepared_context=prepared_context,
                            resolution=resolution,
                        )
                        logger.info(
                            "coverage_replan_started sid=%s request_id=%s phase=chain_safe",
                            session_id,
                            context_request_id or tid,
                        )
                        replanned = await asyncio.wait_for(
                            replan_for_budget(
                                context_window=safe_budget.context_window,
                                effective_pct=safe_budget.effective_pct,
                                generation_reserve=safe_budget.generation_reserve,
                            ),
                            timeout=10.0,
                        )
                        logger.info(
                            "coverage_replan_completed sid=%s request_id=%s phase=chain_safe",
                            session_id,
                            context_request_id or tid,
                        )
                        prepared_context = getattr(
                            replanned, "prepared_context", replanned
                        )
                    working_messages = _tag_run_input_messages(
                        list(prepared_context.messages), task_id=tid
                    )
                current_tool_set = getattr(prepared_context, "tool_set", None)
                if current_tool_set is not None:
                    tool_schemas = list(current_tool_set.logical_schemas())
                final_budgets = [
                    estimate_selected_attempt_budget(
                        provider=provider,
                        model_id=str(
                            getattr(provider, "model", "") or use_model or ""
                        ),
                        messages=working_messages,
                        tools=tool_schemas or None,
                        generation_reserve=reserve,
                        attachment_tokens=attachment_tokens,
                    )
                    for provider in provider_chain
                ]
                coverage_still_pending = bool(
                    used_common_replan
                    and tuple(
                        getattr(
                            prepared_context,
                            "coverage_compaction_jobs",
                            (),
                        )
                        or ()
                    )
                )
                final_budget_fits = all(item.fits for item in final_budgets)
                if coverage_still_pending or not final_budget_fits:
                    logger.warning(
                        "provider_chain_common_budget_detail sid=%s tools=%d "
                        "budgets=%s",
                        session_id,
                        len(tool_schemas or ()),
                        [
                            {
                                "planned": item.planned_input_tokens,
                                "effective": item.effective_input_budget,
                                "messages": item.messages_tokens,
                                "tools": item.tool_tokens,
                                "reserve": item.generation_reserve,
                            }
                            for item in final_budgets
                        ],
                    )
                    if coverage_still_pending or self.compressor is None:
                        raise RuntimeError(
                            "provider_chain_context_budget_exceeded"
                        )
                    # Replanning has already exhausted the lossless options.
                    # Let the normal in-loop compactor make exactly one lossy,
                    # goal-preserving rescue attempt before any provider send.
                    _chain_budget_rescue_pending = True
                self._history_tool_names = set(
                    self._collect_tool_names(working_messages)
                )
            except Exception as exc:  # noqa: BLE001 - recoverable BLOCK
                logger.warning(
                    "provider_context_budget_blocked sid=%s request_id=%s "
                    "error_type=%s error=%r",
                    session_id,
                    context_request_id or tid,
                    type(exc).__name__,
                    exc,
                )
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=0,
                    reason="provider_context_budget_exceeded",
                    detail=str(exc),
                )
                return

        # P5-S2 Hook A: per-run nudge counter for the completion guard.
        # Reset every fresh ``run`` invocation so each chat turn gets a
        # fresh nudge budget — we don't want stale "already nudged 2x"
        # state leaking between turns.
        completion_nudges_used = 0
        # Some reasoning-capable OpenAI-compatible models can end a turn
        # after writing only hidden reasoning, without either user-visible
        # content or a structured tool call. Treat that as an incomplete
        # provider turn: give the model one focused repair attempt, then fail
        # structurally instead of silently completing with an empty answer.
        reasoning_only_nudges_used = 0
        # WI-T2.6: 同 completion_nudges_used 模式 — 本轮起算
        verify_nudges_used = 0
        # WI-2.2: track previous task_replanning for stagnation detection
        _prev_task_replanning: str = ""
        tools_used_count = int(external_feedback.get("tools_used_count", 0))
        _verify_final_done = False
        _force_finish_queued = bool(external_feedback.get("force_finish_queued"))

        # P5-S2 B3: warn-once latch so we don't spam the WARN log every
        # iteration once we're in the 80-95% band.
        _budget_warn_emitted = False

        # WI-4a 目标 always-on 单点注入(对标 Claude Code 钉死 CLAUDE.md)。
        # 唯一注入点 = 这里(循环前注一次),role=system → context_compressor._partition
        # 永久排除、永不被压;compress() 见到它就不再自注(去重),整轮恒 ≤1 条 [目标锚定]。
        # 取代了原"周期性 anchor(每 _GOAL_ANCHOR_EVERY 轮重注)"+"压缩后 _build_goal_anchor
        # 注入"两处冗余。目标 + 子目标都从 session_goal_store 取。
        if self.session_goal_store is not None:
            _ga_fn = getattr(self.session_goal_store, "get_goal_text", None)
            _always_goal = _ga_fn(session_id) if callable(_ga_fn) else None
            if _always_goal:
                _anchor_lines = [f"[目标锚定] 当前目标：{_always_goal}"]
                _pg_fn = getattr(self.session_goal_store, "get_pending_tasks", None)
                _pending = _pg_fn(session_id) if callable(_pg_fn) else None
                if _pending:
                    _anchor_lines.append(f"[当前子目标] {_pending[0]}")
                _anchor_lines.append(
                    "请确保接下来的动作仍服务于上述目标，不要被中间步骤带偏。"
                )
                _append_loop_control(
                    working_messages,
                    {"role": "system", "content": "\n".join(_anchor_lines)},
                    metadata_enabled=context_metadata_enabled,
                    source="agent_loop.goal_anchor",
                    anchor_after=_latest_context_anchor(
                        working_messages,
                        fallback=f"agent-loop:{tid}:run-start",
                    ),
                    fragment_id=f"agent-loop:{tid}:goal-anchor",
                    protected=True,
                    trim_policy="never",
                )
                logger.info(
                    "wi4a_goal_anchor_always_on sid=%s tid=%s", session_id, tid
                )

        # WI-4.0 compaction: warn-once latch so we don't spam the log every
        # iteration when the compressor fires (long multi-tool tasks may cross
        # the threshold repeatedly; we log the first fire and stay quiet after).
        _compaction_warn_logged: bool = False

        # WI-4b pre-flush 限频 latch: 每个 run 最多把任务态 flush 进 L1 一次
        # (防长 agentic 任务反复触发压缩时刷爆 MEMORY.md 50KB cap → 驱逐真实记忆)。
        _preflush_done: bool = False
        _compaction_cycle_index = 0

        # WI-1B-3 自适应触发线: 本 run 累计工具调用数 ≥ 阈值 → 视为 "agentic"
        # (多工具长任务,提前压留 buffer);否则纯对话(延后压)。仅 adaptive_compact_pct
        # ON 时影响触发线,OFF 时只是个没人读的计数器(字节级 BC)。
        _run_tool_calls: int = tools_used_count
        _AGENTIC_TOOL_THRESHOLD = 3

        # FP-2 TC-2.1 第 3 刀: relay 真实 prompt_tokens 反馈回路。char-based
        # 估算对中文/markdown 系统性低估(真机 real 32.9k 时 estimate <24k),
        # 纯系数追不上内容分布 → 用上一轮 response.usage.input_tokens 兜底,
        # compaction 判定取 max(estimate, real)。0 = 本 run 还没有真实值。
        _last_real_prompt_tokens: int = 0
        _latest_compression_report: dict[str, Any] = {}

        self._skills_used_order = list(external_feedback.get("skills_used_order", ()))
        self._skills_used_this_run = set(self._skills_used_order)
        self._frozen_skill_remounts: dict[str, tuple[Any, str]] = {}
        await self._prepare_frozen_skill_remounts(
            working_messages,
            prepared_context=prepared_context,
            external_feedback=external_feedback,
        )
        _skill_compaction_happened = False

        initial_coverage_jobs = tuple(
            getattr(prepared_context, "coverage_compaction_jobs", ()) or ()
        )
        if context_metadata_enabled and initial_coverage_jobs:
            try:
                if self.compressor is None:
                    raise RuntimeError("coverage_compaction_owner_unavailable")
                _compaction_cycle_index += 1
                initial_cycle_id = (
                    f"{context_request_id or tid}:coverage:{_compaction_cycle_index}"
                )
                initial_resolution = self._resolve_compression_model(
                    provider_chain,
                    requested_model=self._requested_compression_model(),
                )
                await self._flush_context_snapshot(
                    cycle_id=initial_cycle_id,
                    session_id=session_id,
                    request_id=context_request_id or tid,
                    working_messages=working_messages,
                    prepared_context=prepared_context,
                    current_tool_set=current_tool_set,
                )
                await self._execute_coverage_compaction_jobs(
                    cycle_id=initial_cycle_id,
                    prepared_context=prepared_context,
                    resolution=initial_resolution,
                )
                replan = getattr(
                    prepared_context, "replan_after_compaction", None
                )
                if not callable(replan):
                    raise RuntimeError("coverage_replan_unavailable")
                logger.info(
                    "coverage_replan_started sid=%s request_id=%s phase=initial",
                    session_id,
                    context_request_id or tid,
                )
                replanned = await asyncio.wait_for(replan(), timeout=10.0)
                logger.info(
                    "coverage_replan_completed sid=%s request_id=%s phase=initial",
                    session_id,
                    context_request_id or tid,
                )
                replanned_context = getattr(
                    replanned, "prepared_context", replanned
                )
                coverage = getattr(replanned_context, "coverage_report", None)
                if (
                    coverage is None
                    or not bool(getattr(coverage, "valid", False))
                    or tuple(getattr(coverage, "gaps", ()) or ())
                    or tuple(getattr(coverage, "overlaps", ()) or ())
                    or tuple(getattr(coverage, "stale_segment_ids", ()) or ())
                    or tuple(getattr(coverage, "broken_causal_groups", ()) or ())
                    or tuple(
                        getattr(replanned_context, "coverage_compaction_jobs", ())
                        or ()
                    )
                ):
                    raise RuntimeError("session_history_coverage_invalid")
                request_budget = getattr(replanned_context, "request_budget", None)
                if request_budget is None or not bool(
                    getattr(request_budget, "fits", False)
                ):
                    raise RuntimeError("context_budget_exceeded_after_coverage")
                replanned_tool_set = getattr(replanned_context, "tool_set", None)
                if current_tool_set is not None and (
                    replanned_tool_set is None
                    or replanned_tool_set.scope_id != current_tool_set.scope_id
                    or replanned_tool_set.schema_fingerprint
                    != current_tool_set.schema_fingerprint
                ):
                    raise RuntimeError("coverage_replan_changed_tool_set")
                prepared_context = replanned_context
                working_messages = _tag_run_input_messages(
                    list(prepared_context.messages), task_id=tid
                )
                current_tool_set = replanned_tool_set
                if current_tool_set is not None:
                    tool_schemas = list(current_tool_set.logical_schemas())
                self._history_tool_names = set(
                    self._collect_tool_names(working_messages)
                )
            except Exception as coverage_exc:  # noqa: BLE001 - fail closed
                logger.warning(
                    "coverage_compaction_blocked sid=%s request_id=%s error=%s",
                    session_id,
                    context_request_id or tid,
                    str(coverage_exc),
                )
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=0,
                    reason="context_coverage_block",
                    detail=str(coverage_exc),
                )
                return

        iteration_offset = max(0, int(external_feedback.get("iteration", 0)))
        for iteration in range(iteration_offset + 1, self.max_iterations + 1):
            if current_tool_set is not None:
                _scope_store = getattr(self.tools, "capability_scope_store", None)
                if _scope_store is None or not hasattr(
                    self.tools, "validate_prepared_tool_set"
                ):
                    yield ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason="tool_capability_runtime_unavailable",
                        detail="Context OS requires a capability-aware registry",
                    )
                    return
                _record = (
                    _scope_store.get(
                        current_tool_set.scope_id,
                        session_id=session_id,
                        request_id=context_request_id or tid,
                    )
                )
                if _record is None:
                    yield ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason="tool_capability_scope_expired",
                        detail="Context OS capability scope is missing or expired",
                    )
                    return
                try:
                    self.tools.validate_prepared_tool_set(
                        current_tool_set,
                        eligibility=_record.eligibility,
                    )
                except Exception as _stale_exc:  # noqa: BLE001
                    yield ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason=(
                            "tool_policy_unavailable"
                            if "tool_policy_unavailable" in str(_stale_exc)
                            else "tool_catalog_stale"
                        ),
                        detail=str(_stale_exc),
                    )
                    return
            _force_finish_next = False
            if _force_finish_queued:
                _force_finish_next = True
                _force_finish_queued = False
            # Context OS requests reserve the last loop iteration for an
            # honest, tool-free final response once at least one tool has
            # already run.  Without this reservation, a tool-using model can
            # consume the final iteration with another tool call and the loop
            # exits through max_iterations without ever getting a chance to
            # summarize the completed work.  Keep the legacy path byte-for-byte
            # compatible: callers that do not provide PreparedContext retain
            # the historical max_iterations error/stop-loss behavior.
            if (
                not _force_finish_next
                and prepared_context is not None
                and self.force_finish_via_tool_choice
                and tools_used_count > 0
                and iteration == self.max_iterations
            ):
                _force_finish_next = True
                logger.info(
                    "context_os_final_iteration_force_finish "
                    "sid=%s tid=%s iter=%d tools_used=%d",
                    session_id,
                    tid,
                    iteration,
                    tools_used_count,
                )
            if self._tracer:
                self._tracer.record({
                    "kind": "iter_start",
                    "iter": iteration,
                    "msg_count": len(working_messages),
                })
            # P6 Phase 6 — TerminationGate.allows_call() is always run.
            # Checks hard limits (turns, wall-clock, cost) BEFORE we burn
            # another LLM call. The gate is the single source of truth
            # for "should this loop keep going?".
            _ok, _reason = await traced_call(
                name="termination_gate.allows_call",
                kind=SpanKind.GATE,
                lifecycle_stage="gate",
                attributes={"iteration": iteration, "gate": "termination"},
                invoke=self._gate.allows_call,
            )
            if not _ok:
                # ─── Step7 止损（闸③，plans/2026-06-24-...）：硬上限/资源触顶 → 不硬撑，
                # 产出诚实止损报告（桌宠交代"卡在哪+建议"，而非只发 error）。
                # pipeline off 时 self._convergence_controller=None → 走原 ErrorEvent return（字节级 BC）。
                if self._convergence_controller is not None:
                    # ⚠️ allows_call() 返回 (False, reason) 但**不调 terminate()**（已核实 termination.py:142）
                    # → 此刻 gate.summary()["reason"] 仍是合成 "running"。真实触顶 reason 在 _reason 里，
                    # 必须用它覆盖 summary 的 reason，否则 resource_capped 判 False → 止损永不触发。
                    _cap_summary = dict(self._gate.summary())
                    if _reason is not None:
                        _cap_summary["reason"] = _reason.value
                    _verdict = self._convergence_controller.evaluate(
                        principal_resolved=False,           # 触顶即未收敛
                        unverified_claims=0,
                        gate_summary=_cap_summary,
                    )
                    if self._pipeline_observability:
                        yield self._pipeline_event("chat_v2_convergence", iteration, {
                            "converged": _verdict.converged,
                            "principal_resolved": _verdict.principal_resolved,
                            "stop_reason": _verdict.stop_reason,
                            "report": _verdict.report,
                        })
                    if _verdict.should_stop_loss and _verdict.report:
                        self._gate.record_final_answer()
                        yield FinalEvent(
                            type="final",
                            task_id=tid,
                            iteration=iteration,
                            content=_verdict.report,
                            stop_reason="stop_loss",
                        )
                        return
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason=(_reason.value if _reason is not None else "unknown"),
                    detail="Termination gate blocked LLM call",
                )
                return

            # P5-S2 B3: token budget guard. Runs BEFORE the LLM call so
            # we can surface an actionable error (BLOCK) or pre-emptive
            # warning (WARN) instead of waiting for the model to choke
            # on context_length_exceeded.
            #
            # P6 Phase 6 — always delegate to self._ctx.check_budget so
            # chat handler + AgentLoop share one budget evaluator.
            _budget_block_pending = _chain_budget_rescue_pending
            _chain_budget_rescue_pending = False
            try:
                from agent.token_budget import (
                    BudgetCheck as _BudgetCheck,
                )
                # Use the FIRST provider's model as the context-window
                # reference (chain mode). In single-provider mode fall
                # back to default_model.
                _budget_model = use_model
                if chain_mode and provider_chain:
                    _first = provider_chain[0]
                    _budget_model = getattr(_first, "model", None) or use_model
                _resolved_model = _budget_model or "unknown"
                # Risk-2 fix: floor the estimate with the last real prompt
                # size (response.usage.input_tokens) so the gate accounts for
                # the fixed system/tool-schema base that estimate_tokens(
                # working_messages) misses — otherwise a genuinely over-window
                # prompt (real 113%) reads as ~16% and never BLOCKs. Default
                # floor 0 on iter-0 → BC. Mirrors the compaction trigger.
                _budget = self._ctx.check_budget(
                    working_messages, model=_resolved_model,
                    real_prompt_tokens_floor=_last_real_prompt_tokens,
                )
                if _budget.verdict is _BudgetCheck.BLOCK:
                    logger.error(
                        "p5s2_token_budget_block sid=%s tid=%s iter=%d "
                        "tokens=%d window=%d ratio=%.2f",
                        session_id, tid, iteration,
                        _budget.estimated_tokens, _budget.context_window,
                        _budget.ratio,
                    )
                    if self.compressor is not None and (
                        context_metadata_enabled or _budget_block_pending
                    ):
                        # Context OS owns the only lossy recovery path.  Defer
                        # the terminal BLOCK until snapshot flush + compact +
                        # re-estimate have had one chance to recover.
                        _budget_block_pending = True
                    else:
                        # Legacy/OFF behavior remains byte-for-byte identical.
                        self._gate.record_error(
                            TerminationReason.CONTEXT_BUDGET_BLOCK
                        )
                        yield ErrorEvent(
                            type="error",
                            task_id=tid,
                            iteration=iteration,
                            reason="context_budget_block",
                            detail=_budget.advice,
                        )
                        return
                elif _budget.verdict is _BudgetCheck.WARN and not _budget_warn_emitted:
                    logger.warning(
                        "p5s2_token_budget_warn sid=%s tid=%s iter=%d "
                        "tokens=%d window=%d ratio=%.2f",
                        session_id, tid, iteration,
                        _budget.estimated_tokens, _budget.context_window,
                        _budget.ratio,
                    )
                    _budget_warn_emitted = True
            except Exception as _b_exc:  # noqa: BLE001
                # Budget check is advisory — never fail the loop on it.
                logger.debug("token_budget_check_failed err=%s", str(_b_exc)[:100])

            # Budget gate BEFORE the call — can't take back tokens after the fact.
            if self.budget_checker is not None and not self.budget_checker.check_allowed():
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="budget_exceeded",
                    detail=f"daily budget cap reached (${self.budget_checker.cap_usd:.2f})",
                )
                return

            # WI-4.0 compaction: after budget guard, before LLM call.
            # Reuses _budget.estimated_tokens (just computed above).
            # compressor=None (flag off) → short-circuit, zero overhead.
            # Operates in-place on working_messages (not assemble — BC).
            # System messages (skill_prelude/persona/frozen) are kept verbatim
            # by _partition inside compress() — "不绕 assemble" constraint met.
            if self.compressor is not None:
                # The compressor service is process-wide, while ContextManager
                # is frozen per Run from the selected model.  Use an isolated
                # routed view so concurrent Sessions cannot bleed limits.
                _active_compressor = self.compressor
                try:
                    _ctx_cfg_for_compression = getattr(self._ctx, "config", None)
                    _model_info_for_compression = getattr(
                        _ctx_cfg_for_compression, "model_info", None
                    )
                    _for_context = getattr(self.compressor, "for_context", None)
                    if (
                        callable(_for_context)
                        and _model_info_for_compression is not None
                    ):
                        _active_compressor = _for_context(
                            context_window=int(
                                _model_info_for_compression.context_window
                            ),
                            threshold_percent=min(
                                float(_model_info_for_compression.compact_at_pct),
                                0.70,
                            ),
                            effective_pct=float(
                                _model_info_for_compression.effective_pct
                            ),
                        )
                except Exception:  # noqa: BLE001 -- legacy fake/BC compressor
                    _active_compressor = self.compressor
                try:
                    _ctoken_est = getattr(_budget, "estimated_tokens", 0)
                except Exception:  # noqa: BLE001
                    _ctoken_est = 0
                # 第 3 刀: real usage 兜底(见 _last_real_prompt_tokens 注释)。
                if _last_real_prompt_tokens > _ctoken_est:
                    _ctoken_est = _last_real_prompt_tokens
                # ★ 第 4 刀(真机测压缩发现的核心 bug 修复): 直接数【即将发送的
                # working_messages】的 token。原来只用 _budget.estimated_tokens(被
                # BudgetAllocator 压到 window×0.6,低于压缩阈值 window×0.8)+
                # _last_real_prompt_tokens(每条消息开头重置 0、且单轮聊天不迭代第二次
                # 拿不到真值) → 两路都够不到阈值 → 压缩**永不触发**(实测 12 消息/
                # 1746 真 token 仍不压)。直接数 working_messages 调用前可得、不受
                # allocator 截断、不延迟,是最可靠的触发信号(复用统一计数,优化 #1+#3)。
                try:
                    from deskpet.agent.tokens import count_messages_tokens as _cmt
                    _wm_tokens = _cmt(working_messages)
                    if _wm_tokens > _ctoken_est:
                        _ctoken_est = _wm_tokens
                except Exception:  # noqa: BLE001
                    pass
                _ctx_should_compress = False
                try:
                    _ctx_cfg = getattr(self._ctx, "config", None)
                    # WI-1B-3: adaptive_compact_pct ON → 按本 run 是否 agentic
                    # (工具调用计数 ≥ 阈值) 取微调后的触发线;OFF → for() 直接
                    # 返回原 compact_at_tokens 属性(字节级 BC,等价旧分支)。
                    _ctx_compact_at = None
                    _for_fn = getattr(_ctx_cfg, "compact_at_tokens_for", None)
                    if callable(_for_fn):
                        _agentic = _run_tool_calls >= _AGENTIC_TOOL_THRESHOLD
                        _ctx_compact_at = _for_fn(_agentic)
                    else:
                        _ctx_compact_at = getattr(_ctx_cfg, "compact_at_tokens", None)
                    if _ctx_compact_at is not None:
                        _ctx_should_compress = (
                            _ctoken_est >= int(_ctx_compact_at) > 0
                        )
                except Exception:  # noqa: BLE001
                    _ctx_should_compress = False
                if (
                    _budget_block_pending
                    or _active_compressor.should_compress(_ctoken_est)
                    or _ctx_should_compress
                ):
                    try:
                        _gt = None
                        if self.session_goal_store is not None:
                            _gt_fn = getattr(self.session_goal_store, "get_goal_text", None)
                            if callable(_gt_fn):
                                _gt = _gt_fn(session_id)
                        _compression_resolution = None
                        _compaction_cycle_id = None
                        _requested_compression_model = (
                            self._requested_compression_model()
                        )
                        if context_metadata_enabled:
                            _compaction_cycle_index += 1
                            _compaction_cycle_id = (
                                f"{context_request_id or tid}:compaction:"
                                f"{_compaction_cycle_index}"
                            )
                            await self._flush_context_snapshot(
                                cycle_id=_compaction_cycle_id,
                                session_id=session_id,
                                request_id=context_request_id or tid,
                                working_messages=working_messages,
                                prepared_context=prepared_context,
                                current_tool_set=current_tool_set,
                            )
                            _compression_resolution = self._resolve_compression_model(
                                provider_chain,
                                requested_model=_requested_compression_model,
                            )
                            from deskpet.context_os_e2e_hooks import (
                                consume_context_os_e2e_fault as _consume_ctx_fault,
                            )
                            if _consume_ctx_fault("compactor_model_error"):
                                raise RuntimeError("compactor_model_error")
                            await self._execute_coverage_compaction_jobs(
                                cycle_id=_compaction_cycle_id,
                                prepared_context=prepared_context,
                                resolution=_compression_resolution,
                            )
                        # WI-4b pre-flush: 摘掉中段前把任务态写进 L1(跨 session 记任务)。
                        # best-effort + 每 run 限一次(latch),失败绝不阻断压缩。
                        if (
                            not context_metadata_enabled
                            and self.file_memory is not None
                            and not _preflush_done
                        ):
                            _preflush_done = True
                            try:
                                _last_user = ""
                                for _m in reversed(working_messages):
                                    if _m.get("role") == "user":
                                        _last_user = str(_m.get("content") or "")[:500]
                                        break
                                _flush_parts = []
                                if _gt:
                                    _flush_parts.append(f"目标: {_gt}")
                                if _last_user:
                                    _flush_parts.append(f"最近请求: {_last_user}")
                                if _flush_parts:
                                    _flush_body = (
                                        "[任务态快照/task-state] " + "; ".join(_flush_parts)
                                    )
                                    await self.file_memory.append(
                                        "memory", _flush_body, salience=0.6
                                    )
                                    logger.info(
                                        "wi4b_preflush_l1 sid=%s tid=%s chars=%d",
                                        session_id, tid, len(_flush_body),
                                    )
                            except Exception as _pf_exc:  # noqa: BLE001
                                logger.debug(
                                    "wi4b_preflush_failed sid=%s err=%s",
                                    session_id, str(_pf_exc)[:120],
                                )
                        _compress_kwargs: dict[str, Any] = {"goal_text": _gt}
                        if _compression_resolution is not None:
                            _candidate = _compression_resolution.candidates[0]
                            _compress_kwargs.update(
                                resolved_provider=_candidate.provider,
                                resolved_model=_candidate.model_id,
                                compaction_cycle_id=_compaction_cycle_id,
                            )
                        if self._provider_workload_context_factory is not None:
                            _compress_kwargs["workload_context"] = (
                                self._provider_workload_context_factory(
                                    "agent.context_compaction"
                                )
                            )
                        _context_remount = (
                            _capture_context_remount(working_messages)
                            if context_metadata_enabled
                            else None
                        )
                        if not context_metadata_enabled:
                            from deskpet.context_os_e2e_hooks import (
                                consume_context_os_e2e_fault as _consume_ctx_fault,
                            )
                            if _consume_ctx_fault("compactor_model_error"):
                                raise RuntimeError("compactor_model_error")
                        _cresult = await _active_compressor.compress(
                            working_messages, **_compress_kwargs
                        )
                        _resolved_candidate = (
                            _compression_resolution.candidates[0]
                            if _compression_resolution is not None
                            else None
                        )
                        _latest_compression_report = {
                            "requested_model": _requested_compression_model,
                            "resolved_model": (
                                _resolved_candidate.model_id
                                if _resolved_candidate is not None
                                else str(getattr(self.compressor, "model", "") or "")
                            ),
                            "actual_model": (
                                _resolved_candidate.model_id
                                if _resolved_candidate is not None
                                else str(getattr(self.compressor, "model", "") or "")
                            ),
                            "provider": (
                                _resolved_candidate.provider_id
                                if _resolved_candidate is not None
                                else ""
                            ),
                            "source": (
                                _compression_resolution.source
                                if _compression_resolution is not None
                                else "legacy"
                            ),
                            "failure": str(getattr(_cresult, "error", "") or "")[:200],
                        }
                        if getattr(_cresult, "compressed", False):
                            _skill_compaction_happened = True
                            working_messages = _cresult.messages
                            if _context_remount is not None:
                                working_messages = _remount_context_after_compaction(
                                    working_messages,
                                    _context_remount,
                                )
                            # WI-4.2: re-inline skill bodies after compaction so
                            # the LLM doesn't lose skill step details that were
                            # in the compressed "middle" messages.
                            working_messages = self._remount_skills(
                                working_messages,
                                session_id,
                                prepared_context=prepared_context,
                            )
                            if not _compaction_warn_logged:
                                logger.info(
                                    "p1_4_compaction_fired sid=%s tid=%s iter=%d "
                                    "reduction=%s",
                                    session_id, tid, iteration,
                                    getattr(_cresult, "reduction_ratio", "?"),
                                )
                                _compaction_warn_logged = True
                            # WI-1B-2 压缩可观测: flag ON 时额外 emit metrics +
                            # yield 轻量事件。flag OFF → 整块 short-circuit(BC,
                            # 既不 record 也不构造/yield ContextCompactedEvent)。
                            if self.ctx_observability:
                                _ctx_in = int(getattr(_cresult, "input_tokens", 0) or 0)
                                _ctx_out = int(getattr(_cresult, "output_tokens", 0) or 0)
                                _ctx_ratio = float(
                                    getattr(_cresult, "reduction_ratio", 0.0) or 0.0
                                )
                                try:
                                    from observability.metrics_sink import (
                                        record as _ctx_metric,
                                    )
                                    _ctx_metric("context_compacted", {
                                        "ratio": round(_ctx_ratio, 3),
                                        "model": getattr(self.compressor, "model", "")
                                        or "",
                                        "count": _ctx_in - _ctx_out,
                                    })
                                except Exception:  # noqa: BLE001
                                    pass
                                _actual_compaction_model = (
                                    _resolved_candidate.model_id
                                    if _resolved_candidate is not None
                                    else str(
                                        getattr(self.compressor, "model", "") or ""
                                    )
                                )
                                _compaction_source_event_id = (
                                    f"context-compaction:{session_id}:"
                                    f"{tid}:{iteration}"
                                )
                                yield ContextCompactedEvent(
                                    task_id=tid,
                                    iteration=iteration,
                                    reduction=round(_ctx_ratio, 3),
                                    tokens_in=_ctx_in,
                                    tokens_out=_ctx_out,
                                    model=_actual_compaction_model,
                                    source_event_id=(
                                        _compaction_source_event_id
                                    ),
                                    based_on_sample_id=str(
                                        getattr(
                                            self,
                                            "_context_usage_basis_sample_id",
                                            "",
                                        )
                                        or ""
                                    ),
                                    occurred_at=time.time(),
                                )
                                from deskpet.agent.context_usage import (
                                    context_usage_sample_id,
                                )
                                self._context_usage_basis_sample_id = (
                                    context_usage_sample_id(
                                        session_id,
                                        _compaction_source_event_id,
                                    )
                                )
                    except Exception as _cmp_exc:  # noqa: BLE001
                        # Compaction is advisory — never abort the loop on it.
                        _latest_compression_report = {
                            "requested_model": self._requested_compression_model(),
                            "failure": str(_cmp_exc)[:200],
                        }
                        logger.debug(
                            "p1_4_compaction_failed sid=%s iter=%d err=%s",
                            session_id, iteration, str(_cmp_exc)[:200],
                        )

            if _budget_block_pending:
                try:
                    _post_budget = self._ctx.check_budget(
                        working_messages,
                        model=_resolved_model,
                        real_prompt_tokens_floor=0,
                    )
                    _still_blocked = _post_budget.verdict is _BudgetCheck.BLOCK
                    _block_detail = _post_budget.advice
                except Exception as _post_exc:  # noqa: BLE001
                    _still_blocked = True
                    _block_detail = f"context re-budget failed: {_post_exc}"
                if _still_blocked:
                    self._gate.record_error(
                        TerminationReason.CONTEXT_BUDGET_BLOCK
                    )
                    yield ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason="context_budget_block",
                        detail=_block_detail,
                    )
                    return

            if _skill_compaction_happened and self._skills_used_this_run:
                working_messages = self._remount_skills(
                    working_messages,
                    session_id,
                    prepared_context=prepared_context,
                )

            # P6 Phase 6: escalating in-loop self-check (soft nudge).
            # The legacy _TOOL_BUDGET_HARD_MSG soft cap is GONE — the
            # TerminationGate now enforces tool_budget_hard with a HARD
            # break and emits ErrorEvent(error_tool_budget). The selfcheck
            # tier injection below is complementary (gentle reflection
            # nudge to coax stop_reason=end_turn earlier).

            # Count tools used so far in this loop run (tracked at each
            # tool dispatch). `tools_used` mirrors gate.state.tools_used.
            tools_used = locals().get("tools_used_count", 0)

            if iteration > 0 and iteration % _SELFCHECK_EVERY == 0:
                budget_left = self.max_iterations - iteration
                msg = _build_selfcheck_message(iteration, self.max_iterations, tools_used)
                # WI-2.1: append reflection instruction at tier2/tier3 only
                # (zero overhead on normal tier1 path; flag off = BC).
                if self.structured_reflection and iteration >= _SELFCHECK_TIER2_AT:
                    from deskpet.agent.reflection import _REFLECTION_INSTRUCTION
                    msg = msg + _REFLECTION_INSTRUCTION
                _append_loop_control(
                    working_messages,
                    {"role": "system", "content": msg},
                    metadata_enabled=context_metadata_enabled,
                    source="agent_loop.self_check",
                    anchor_after=_latest_context_anchor(
                        working_messages,
                        fallback=f"agent-loop:{tid}:iteration:{iteration}:start",
                    ),
                    fragment_id=(
                        f"agent-loop:{tid}:iteration:{iteration}:self-check"
                    ),
                )
                tier = (
                    3 if iteration >= _SELFCHECK_TIER3_AT
                    else 2 if iteration >= _SELFCHECK_TIER2_AT
                    else 1
                )
                logger.info(
                    "p5s2_selfcheck_injected sid=%s tid=%s iter=%d budget=%d "
                    "tier=%d tools_used=%d structured_reflection=%s",
                    session_id, tid, iteration, budget_left, tier, tools_used,
                    self.structured_reflection,
                )
                if (
                    self.force_finish_via_tool_choice
                    and iteration >= _SELFCHECK_TIER3_AT
                ):
                    _force_finish_next = True

            if (
                self.session_todo_getter is not None
                and iteration % _TODO_SYNC_EVERY == 0
                and iteration < _SELFCHECK_TIER3_AT
            ):
                try:
                    todos = await self.session_todo_getter(session_id)
                except Exception:
                    todos = []
                if todos:
                    lines = []
                    for t in todos:
                        content = (t.get("content") or "")[:80]
                        status = (t.get("status") or "").lower()
                        mark = {"completed": "✓", "in_progress": "🔄"}.get(
                            status, "⏳"
                        )
                        lines.append(f"  {mark} {content}")
                    _append_loop_control(
                        working_messages,
                        {
                            "role": "system",
                            "content": _TODO_SYNC_MSG.format(
                                body="\n".join(lines)
                            ),
                        },
                        metadata_enabled=context_metadata_enabled,
                        source="agent_loop.todo_sync",
                        anchor_after=_latest_context_anchor(
                            working_messages,
                            fallback=(
                                f"agent-loop:{tid}:iteration:{iteration}:start"
                            ),
                        ),
                        fragment_id=(
                            f"agent-loop:{tid}:iteration:{iteration}:todo-sync"
                        ),
                    )
                    logger.info(
                        "wi4_todo_sync sid=%s iter=%d n=%d",
                        session_id, iteration, len(todos),
                    )

            # WI-4a: 周期性 [目标锚定] 注入已删除 —— 改由循环前的 always-on 单点注入
            # (见上方 wi4a_goal_anchor_always_on)。always-on 那条 role=system 常驻、
            # 不被压、整轮恒 ≤1 条,周期重注是冗余且会堆多条同文 system。防 drift 由
            # always-on 常驻 + WI-3 结构化摘要保任务共同覆盖,能力不丢。

            try:
                if chain_mode:
                    # ─── Phase 3: walk the chain ───
                    # On transient LLMProviderError, yield a
                    # ProviderChainFallbackEvent and try the next
                    # provider. On success, accept the response and
                    # break. If every provider fails, emit
                    # ErrorEvent(reason="all_providers_failed").
                    from agent.tool_use_shim import _raw_to_response

                    response = None  # type: ignore[assignment]
                    last_exc: Optional[Exception] = None
                    for idx, prov in enumerate(provider_chain):  # type: ignore[arg-type]
                        try:
                            _attempt_tools = (
                                None if _force_finish_next else (tool_schemas or None)
                            )
                            _attempt_purpose = (
                                "force_finish" if _force_finish_next else "agent_response"
                            )
                            raw = await self._run_context_attempt(
                                provider=prov,
                                session_id=session_id,
                                request_id=context_request_id or tid,
                                attempt_id=(
                                    f"{context_request_id or tid}:iter:{iteration}:provider:{idx}"
                                ),
                                purpose=_attempt_purpose,
                                messages=working_messages,
                                tools=_attempt_tools,
                                fallback_model=use_model or "",
                                prepared_context=prepared_context,
                                current_tool_set=current_tool_set,
                                compression=_latest_compression_report,
                                invoke=lambda prov=prov: traced_call(
                                    name="llm.chat_with_tools",
                                    kind=SpanKind.LLM,
                                    lifecycle_stage="llm",
                                    attributes={
                                        "iteration": iteration,
                                        "provider": str(getattr(prov, "id", f"provider_{idx}")),
                                        "stream": False,
                                        "message_count": len(working_messages),
                                        "tool_schema_count": len(tool_schemas),
                                    },
                                    invoke=lambda: prov.chat_with_tools(
                                        working_messages,
                                        tools=_attempt_tools,
                                        max_tokens=int(llm_kwargs.get("max_tokens", 8192)),
                                        temperature=llm_kwargs.get("temperature"),
                                        response_format=llm_kwargs.get("response_format"),
                                        tool_choice=("none" if _force_finish_next else None),
                                        **launch_call_kwargs,
                                    ),
                                ),
                            )
                        except (LLMProviderError, RuntimeError) as exc:
                            _authority_error = _non_fallback_provider_error(exc)
                            if _should_raise_provider_runtime_error(
                                exc, _authority_error
                            ):
                                raise
                            if _authority_error is not None:
                                if _authority_error == "tool_catalog_stale":
                                    _invalidate_stale_mcp_catalog(
                                        self.tools, current_tool_set
                                    )
                                yield ErrorEvent(
                                    type="error",
                                    task_id=tid,
                                    iteration=iteration,
                                    reason=_authority_error,
                                    detail=str(exc),
                                    error_class=_authority_error,
                                )
                                return
                            if launch_call_kwargs:
                                raise
                            last_exc = exc
                            prov_id = getattr(prov, "id", f"provider_{idx}")
                            next_idx = idx + 1
                            if next_idx < len(provider_chain):  # type: ignore[arg-type]
                                next_prov = provider_chain[next_idx]  # type: ignore[index]
                                next_id = getattr(
                                    next_prov, "id", f"provider_{next_idx}"
                                )
                                reason = str(exc) or type(exc).__name__
                                logger.warning(
                                    "provider_chain_fallback from=%s to=%s "
                                    "reason=%s sid=%s tid=%s",
                                    prov_id, next_id, reason[:200],
                                    session_id, tid,
                                )
                                yield ProviderChainFallbackEvent(
                                    type="provider_chain_fallback",
                                    task_id=tid,
                                    iteration=iteration,
                                    session_id=session_id,
                                    from_=prov_id,
                                    to=next_id,
                                    reason=reason,
                                )
                            else:
                                logger.warning(
                                    "provider_chain_last_provider_failed "
                                    "id=%s reason=%s sid=%s",
                                    prov_id, str(exc)[:200], session_id,
                                )
                            continue
                        else:
                            response = _raw_to_response(raw)
                            break

                    if response is None:
                        # All providers in the chain raised.
                        tried = len(provider_chain)  # type: ignore[arg-type]
                        last_text = str(last_exc) if last_exc else "unknown"
                        if isinstance(last_exc, RuntimeError) and str(
                            last_exc
                        ).startswith("provider_context_budget_exceeded"):
                            yield ErrorEvent(
                                type="error",
                                task_id=tid,
                                iteration=iteration,
                                reason="provider_context_budget_exceeded",
                                detail=last_text,
                            )
                            return
                        # P6 Phase 6 — record terminal error reason on the
                        # gate so callers reading summary() see the real
                        # cause (otherwise the gate would stay "running").
                        self._gate.record_error(
                            TerminationReason.ALL_PROVIDERS_FAILED
                        )
                        yield ErrorEvent(
                            type="error",
                            task_id=tid,
                            iteration=iteration,
                            reason="all_providers_failed",
                            detail=(
                                f"tried {tried}, last_error: {last_text}"
                            ),
                            # 把最后一个 provider 的结构化 error_class 透传给前端，
                            # 让 chain 全失败路径也能 surface 友好分类（如
                            # empty_api_key→"请重新登录" / relay 402→充值），而不是
                            # 退化成无分类的通用错误。
                            error_class=getattr(last_exc, "error_class", "") or "",
                        )
                        return
                elif stream_capable:
                    # Streaming path: forward delta events, accumulate
                    # the final dict, then build the same ChatResponse
                    # the non-streaming path would have produced.
                    final_dict: dict | None = None
                    delta_count = 0
                    stream_failed_with: Exception | None = None
                    call_llm_kwargs = (
                        {**llm_kwargs, "tool_choice": "none"}
                        if _force_finish_next else llm_kwargs
                    )
                    call_llm_kwargs = {**launch_call_kwargs, **call_llm_kwargs}
                    _attempt_tools = (
                        None if _force_finish_next else (tool_schemas or None)
                    )
                    _attempt_provider = getattr(
                        self.llm, "_provider", getattr(self.llm, "_active_provider", self.llm)
                    )
                    _attempt_purpose = (
                        "force_finish" if _force_finish_next else "agent_response"
                    )
                    try:
                        async for ev in self._iterate_context_attempt(
                            provider=_attempt_provider,
                            session_id=session_id,
                            request_id=context_request_id or tid,
                            attempt_id=f"{context_request_id or tid}:iter:{iteration}:stream",
                            purpose=_attempt_purpose,
                            messages=working_messages,
                            tools=_attempt_tools,
                            fallback_model=use_model or "",
                            prepared_context=prepared_context,
                            current_tool_set=current_tool_set,
                            compression=_latest_compression_report,
                            iterator=lambda: traced_iterate(
                                name="llm.chat_with_fallback_stream",
                                kind=SpanKind.LLM,
                                lifecycle_stage="llm",
                                attributes={
                                    "iteration": iteration,
                                    "model": str(use_model or ""),
                                    "stream": True,
                                    "message_count": len(working_messages),
                                    "tool_schema_count": len(tool_schemas),
                                },
                                iterator=lambda: self.llm.chat_with_fallback_stream(  # type: ignore[attr-defined]
                                    working_messages,
                                    tools=_attempt_tools,
                                    model=use_model,
                                    **call_llm_kwargs,
                                ),
                            ),
                        ):
                            ev_type = ev.get("type")
                            if ev_type == "delta":
                                delta_count += 1
                                yield AssistantDeltaEvent(
                                    type="assistant_delta",
                                    task_id=tid,
                                    iteration=iteration,
                                    content=ev.get("content", ""),
                                    kind="content",
                                    invocation_id=ev.get("invocation_id"),
                                    stream_epoch=ev.get("stream_epoch"),
                                    provisional=bool(ev.get("provisional", False)),
                                )
                            elif ev_type == "delta_reasoning":
                                delta_count += 1
                                yield AssistantDeltaEvent(
                                    type="assistant_delta",
                                    task_id=tid,
                                    iteration=iteration,
                                    content=ev.get("content", ""),
                                    kind="reasoning",
                                    invocation_id=ev.get("invocation_id"),
                                    stream_epoch=ev.get("stream_epoch"),
                                    provisional=bool(ev.get("provisional", False)),
                                )
                            elif ev_type == "retract":
                                yield AssistantDeltaEvent(
                                    type="assistant_delta",
                                    task_id=tid,
                                    iteration=iteration,
                                    invocation_id=ev.get("invocation_id"),
                                    stream_epoch=ev.get("stream_epoch"),
                                    provisional=True,
                                    retract_provisional=True,
                                )
                            elif ev_type == "final":
                                final_dict = ev
                    except LLMProviderError as stream_exc:
                        # P4-S25 fix: streaming raised after exhausting its
                        # own retry budget (typically RemoteProtocolError
                        # 3-in-a-row from the relay). Fall back to the non-
                        # streaming path instead of bubbling the error
                        # to the user — non-stream is more reliable on
                        # the relay in our observed traffic, AND it has its
                        # own independent retry budget so we effectively
                        # double the resilience without hard-coding 6
                        # retries.
                        stream_failed_with = stream_exc
                        logger.warning(
                            "agent_loop_stream_failed_falling_back "
                            "error=%s", str(stream_exc)[:200],
                        )

                    needs_nonstream_fallback = (
                        stream_failed_with is not None
                        or final_dict is None
                        or (
                            delta_count == 0
                            and not final_dict.get("content")
                            and not final_dict.get("tool_calls")
                        )
                    )
                    if launch_call_kwargs and not provider_launch_snapshot.supports_idempotent_launch:
                        needs_nonstream_fallback = False
                        if stream_failed_with is not None:
                            raise stream_failed_with
                    if needs_nonstream_fallback:
                        if stream_failed_with is None:
                            # Empty-stream case (the relay didn't actually stream).
                            logger.warning(
                                "agent_loop_stream_fallback_to_nonstream "
                                "delta_count=%d", delta_count,
                            )
                        response = await self._run_context_attempt(
                            provider=_attempt_provider,
                            session_id=session_id,
                            request_id=context_request_id or tid,
                            attempt_id=f"{context_request_id or tid}:iter:{iteration}:nonstream-fallback",
                            purpose=_attempt_purpose,
                            messages=working_messages,
                            tools=_attempt_tools,
                            fallback_model=use_model or "",
                            prepared_context=prepared_context,
                            current_tool_set=current_tool_set,
                            compression=_latest_compression_report,
                            invoke=lambda: traced_call(
                                name="llm.chat_with_fallback",
                                kind=SpanKind.LLM,
                                lifecycle_stage="llm",
                                attributes={
                                    "iteration": iteration,
                                    "model": str(use_model or ""),
                                    "stream": False,
                                    "stream_fallback": True,
                                    "message_count": len(working_messages),
                                    "tool_schema_count": len(tool_schemas),
                                },
                                invoke=lambda: self.llm.chat_with_fallback(
                                    working_messages,
                                    tools=_attempt_tools,
                                    model=use_model,
                                    **call_llm_kwargs,
                                ),
                            ),
                        )
                    else:
                        # Convert to ChatResponse to share the rest of the
                        # iteration code with the non-streaming path.
                        from agent.tool_use_shim import _raw_to_response
                        response = _raw_to_response(final_dict)
                else:
                    call_llm_kwargs = (
                        {**llm_kwargs, "tool_choice": "none"}
                        if _force_finish_next else llm_kwargs
                    )
                    call_llm_kwargs = {**launch_call_kwargs, **call_llm_kwargs}
                    _attempt_tools = (
                        None if _force_finish_next else (tool_schemas or None)
                    )
                    _attempt_provider = getattr(
                        self.llm, "_provider", getattr(self.llm, "_active_provider", self.llm)
                    )
                    _attempt_purpose = (
                        "force_finish" if _force_finish_next else "agent_response"
                    )
                    response = await self._run_context_attempt(
                        provider=_attempt_provider,
                        session_id=session_id,
                        request_id=context_request_id or tid,
                        attempt_id=f"{context_request_id or tid}:iter:{iteration}:nonstream",
                        purpose=_attempt_purpose,
                        messages=working_messages,
                        tools=_attempt_tools,
                        fallback_model=use_model or "",
                        prepared_context=prepared_context,
                        current_tool_set=current_tool_set,
                        compression=_latest_compression_report,
                        invoke=lambda: traced_call(
                            name="llm.chat_with_fallback",
                            kind=SpanKind.LLM,
                            lifecycle_stage="llm",
                            attributes={
                                "iteration": iteration,
                                "model": str(use_model or ""),
                                "stream": False,
                                "message_count": len(working_messages),
                                "tool_schema_count": len(tool_schemas),
                            },
                            invoke=lambda: self.llm.chat_with_fallback(
                                working_messages,
                                tools=_attempt_tools,
                                model=use_model,
                                **call_llm_kwargs,
                            ),
                        ),
                    )
            except LLMBudgetExceededError as exc:
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="budget_exceeded",
                    detail=str(exc),
                )
                return
            except RuntimeError as exc:
                if not str(exc).startswith(
                    ("tool_context_persist_failed", "provider_context_budget_exceeded")
                ):
                    raise
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason=(
                        "provider_context_budget_exceeded"
                        if str(exc).startswith("provider_context_budget_exceeded")
                        else "tool_context_persist_failed"
                    ),
                    detail=str(exc),
                )
                return
            except LLMProviderError as exc:
                yield ErrorEvent(
                    type="error",
                    task_id=tid,
                    iteration=iteration,
                    reason="llm_error",
                    detail=str(exc),
                    # WI-R5: carry the relay error code so the frontend
                    # can show 余额不足 / key 失效 friendly messages.
                    error_class=getattr(exc, "error_class", "") or "",
                )
                return

            launch_call_kwargs = {}
            totals["input"] += response.usage.input_tokens
            totals["output"] += response.usage.output_tokens
            totals["cache_read"] += response.usage.cache_read_tokens
            totals["cache_write"] += response.usage.cache_write_tokens
            # 第 3 刀: 记录 relay 真实 prompt 大小,喂下一轮 compaction 判定。
            if response.usage.input_tokens > _last_real_prompt_tokens:
                _last_real_prompt_tokens = response.usage.input_tokens
            if self._tracer:
                self._tracer.record({
                    "kind": "llm_out",
                    "iter": iteration,
                    "stop_reason": response.stop_reason,
                    "content_preview": (response.content or "")[:200],
                    "tool_calls": [
                        {"name": tc.name, "args": tc.arguments}
                        for tc in response.tool_calls
                    ],
                    "usage": (
                        response.usage.model_dump()
                        if hasattr(response.usage, "model_dump")
                        else {}
                    ),
                })

            # P6 Phase 6 — record the turn (advances turns_used and
            # optionally adds to cost_usd if the response carries a
            # cost_usd extra; ChatUsage has no such field today so we
            # pass 0.0). The gate's allows_call check at top of next
            # iteration uses turns_used to decide whether to keep going.
            _cost_delta = 0.0
            _usage = getattr(response, "usage", None)
            if _usage is not None:
                _maybe_cost = getattr(_usage, "cost_usd", None)
                if isinstance(_maybe_cost, (int, float)):
                    _cost_delta = float(_maybe_cost)
            self._gate.record_turn(cost_delta_usd=_cost_delta)

            if (
                not (response.content or "").strip()
                and not response.tool_calls
            ):
                has_hidden_reasoning = bool(
                    (response.reasoning_content or "").strip()
                )
                empty_reason = (
                    "model_reasoning_only_response"
                    if has_hidden_reasoning
                    else "model_empty_response"
                )
                max_reasoning_only_nudges = 3
                if reasoning_only_nudges_used >= max_reasoning_only_nudges:
                    logger.warning(
                        "model_non_actionable_response_exhausted "
                        "sid=%s tid=%s iteration=%d reason=%s",
                        session_id,
                        tid,
                        iteration,
                        empty_reason,
                    )
                    yield ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason=empty_reason,
                        detail=(
                            "The model exhausted three recovery nudges after "
                            "ending turns with no tool call or user-visible "
                            "answer."
                        ),
                    )
                    return

                reasoning_only_nudges_used += 1
                reasoning_reply = _append_loop_transcript(
                    working_messages,
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": response.reasoning_content,
                    },
                    metadata_enabled=context_metadata_enabled,
                    source="agent_loop.reasoning_only.trigger",
                    role="assistant",
                    fragment_id=(
                        f"agent-loop:{tid}:iteration:{iteration}:"
                        "reasoning-only-trigger"
                    ),
                )
                _append_loop_control(
                    working_messages,
                    {
                        "role": "system",
                        "content": (
                            f"Recovery attempt {reasoning_only_nudges_used} of "
                            f"{max_reasoning_only_nudges}. "
                            "Your previous turn contained "
                            + (
                                "only internal reasoning"
                                if has_hidden_reasoning
                                else "no output"
                            )
                            + " and no user-visible answer or tool call. "
                            "Do not end a turn this way. If your reasoning "
                            "selected an available tool, emit the actual "
                            "structured tool call now. Otherwise provide a "
                            "concise user-facing answer."
                        ),
                    },
                    metadata_enabled=context_metadata_enabled,
                    source="agent_loop.reasoning_only.nudge",
                    anchor_after=(
                        _context_fragment_id(reasoning_reply)
                        or _latest_context_anchor(
                            working_messages,
                            fallback=(
                                f"agent-loop:{tid}:iteration:{iteration}:"
                                "reasoning-only-trigger"
                            ),
                        )
                    ),
                    fragment_id=(
                        f"agent-loop:{tid}:iteration:{iteration}:"
                        "reasoning-only-nudge"
                    ),
                )
                logger.info(
                    "model_non_actionable_response_retried "
                    "sid=%s tid=%s iteration=%d reason=%s",
                    session_id,
                    tid,
                    iteration,
                    empty_reason,
                )
                continue

            # The guard is intentionally consecutive. A real tool call or
            # user-visible answer proves that the provider recovered, so a
            # later isolated reasoning-only turn starts a fresh recovery
            # window instead of inheriting old nudges from earlier work.
            reasoning_only_nudges_used = 0
            yield AssistantMessageEvent(
                type="assistant_message",
                task_id=tid,
                iteration=iteration,
                content=response.content,
                reasoning_content=response.reasoning_content,
                tool_calls=list(response.tool_calls),
                stop_reason=response.stop_reason,
                model=response.model,
            )

            # End of conversation — emit final and stop.
            if response.stop_reason != "tool_use" or not response.tool_calls:
                if host_validated_zero_tool_terminal:
                    yield _host_validated_terminal_event(self, response, tid, iteration, totals)
                    return
                # WI-1 §17.6 — verify_exhausted final-summary turn: when this
                # is the single forced "summarize then stop" turn granted after
                # verify-gate exhaustion (_verify_final_done set + force_finish
                # consumed this iteration), the model has now produced its
                # plain-text summary. Surface it as the terminal
                # ErrorEvent(verify_exhausted) (summary carried in detail) so the
                # failure is still reported while the user gets the summary.
                # Without this, the verify block's own entry guard
                # (verify_nudges_used < max) skips re-entry on this turn and the
                # run would silently end as a FinalEvent, hiding the failure.
                if _verify_final_done and _force_finish_next:
                    if self._tracer:
                        self._tracer.record({
                            "kind": "end",
                            "iter": iteration,
                            "reason": "verify_exhausted",
                            "gate_summary": self._gate.summary(),
                        })
                    yield ErrorEvent(
                        type="error",
                        task_id=tid,
                        iteration=iteration,
                        reason="verify_exhausted",
                        detail=(
                            response.content
                            or "verify-gate: all retries exhausted"
                        ),
                    )
                    return
                # ─── Step2 EvidenceGate（取证门控，核心闸①，plans/2026-06-24-...）。
                # needs_investigation 且本 run 尚未取证就想 end_turn → 拦截注入 <调查> nudge。
                # flag off 时 self._evidence_gate=None → 跳过整段（BC）。判定走 self._evidence_gathered
                # 布尔（dispatch 时按白名单+history 快照置位），对 compaction 整体替换 working_messages 免疫（R1）。
                if (
                    iteration < _SELFCHECK_TIER3_AT
                    and self._evidence_gate is not None
                    and self._pipeline_needs_investigation
                ):
                    _ev_dec = await traced_call(
                        name="evidence_gate.check",
                        kind=SpanKind.GATE,
                        lifecycle_stage="gate",
                        attributes={"iteration": iteration, "gate": "evidence"},
                        invoke=lambda: self._evidence_gate.check(
                            needs_investigation=self._pipeline_needs_investigation,
                            evidence_gathered=self._evidence_gathered,
                            nudges_used=self._evidence_nudges_used,
                        ),
                    )
                    if self._pipeline_observability:
                        yield self._pipeline_event(
                            "chat_v2_evidence_gate", iteration,
                            {"blocked": _ev_dec.blocked, "reason": _ev_dec.reason,
                             "nudge_count": _ev_dec.nudge_count},
                        )
                    if _ev_dec.blocked:
                        self._evidence_nudges_used = _ev_dec.nudge_count
                        _evidence_anchor = _latest_context_anchor(
                            working_messages,
                            fallback=f"agent-loop:{tid}:iteration:{iteration}:response",
                        )
                        if response.content:
                            _evidence_reply = _append_loop_transcript(
                                working_messages,
                                {"role": "assistant", "content": response.content},
                                metadata_enabled=context_metadata_enabled,
                                source="agent_loop.evidence_gate.trigger",
                                role="assistant",
                                fragment_id=(
                                    f"agent-loop:{tid}:iteration:{iteration}:"
                                    "evidence-trigger"
                                ),
                            )
                            _evidence_anchor = (
                                _context_fragment_id(_evidence_reply)
                                or _evidence_anchor
                            )
                        _append_loop_control(
                            working_messages,
                            {"role": "system", "content": _ev_dec.nudge},
                            metadata_enabled=context_metadata_enabled,
                            source="agent_loop.evidence_nudge",
                            anchor_after=_evidence_anchor,
                            fragment_id=(
                                f"agent-loop:{tid}:iteration:{iteration}:"
                                f"evidence-nudge:{_ev_dec.nudge_count}"
                            ),
                        )
                        logger.info(
                            "evidence_gate_nudge_injected sid=%s nudge=%d",
                            session_id, _ev_dec.nudge_count,
                        )
                        continue

                # P5-S2 Hook A: completion guard. Before truly finalizing,
                # ask the caller (via ``completion_probe``) whether session-
                # level work (todos) is actually finished. If the LLM said
                # "I'm done" but the SessionDB still has incomplete todos,
                # rebound with a system message reminding it to either
                # finish them or explicitly mark them cancelled. Capped at
                # ``max_completion_nudges`` so we can't loop forever when
                # the LLM digs in.
                if (
                    iteration < _SELFCHECK_TIER3_AT
                    and
                    self.completion_probe is not None
                    and self.max_completion_nudges > 0
                    and completion_nudges_used < self.max_completion_nudges
                ):
                    try:
                        incomplete = await traced_call(
                            name="completion_probe.check",
                            kind=SpanKind.GATE,
                            lifecycle_stage="gate",
                            attributes={"iteration": iteration, "gate": "completion"},
                            invoke=lambda: self.completion_probe(session_id),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "p5s2_completion_probe_failed sid=%s err=%s",
                            session_id, str(exc)[:200],
                        )
                        incomplete = []
                    if incomplete:
                        if self._tracer:
                            self._tracer.record({
                                "kind": "gate",
                                "iter": iteration,
                                "which": "completion",
                                "passed": False,
                                "reason": "incomplete_todos",
                            })
                        completion_nudges_used += 1
                        # Build the rebound system message. Keep it brief
                        # — long prompts crowd the context window.
                        bullet_list = "\n".join(
                            f"  - {(t.get('content') or '').strip()[:120]}"
                            for t in incomplete[:8]
                        )
                        rebound = (
                            f"你声明已完成（stop_reason={response.stop_reason or 'end_turn'}），"
                            f"但 todos 里还有 {len(incomplete)} 项未完成：\n"
                            f"{bullet_list}\n\n"
                            "请继续执行剩余 todos。如果某项确实做不了 / 不该做，"
                            "请用 todo_write 把它标成 completed 并简述原因（或者改文案明确说明放弃理由）。"
                            "不要再次空口说『我做完了』。"
                        )
                        # Append the assistant message that triggered this
                        # so the LLM sees its own prior end_turn output —
                        # otherwise the rebound system message has no
                        # context for "what did I just stop on".
                        _completion_anchor = _latest_context_anchor(
                            working_messages,
                            fallback=f"agent-loop:{tid}:iteration:{iteration}:response",
                        )
                        if response.content:
                            _completion_reply = _append_loop_transcript(
                                working_messages,
                                {
                                    "role": "assistant",
                                    "content": response.content,
                                },
                                metadata_enabled=context_metadata_enabled,
                                source="agent_loop.completion_probe.trigger",
                                role="assistant",
                                fragment_id=(
                                    f"agent-loop:{tid}:iteration:{iteration}:"
                                    "completion-trigger"
                                ),
                            )
                            _completion_anchor = (
                                _context_fragment_id(_completion_reply)
                                or _completion_anchor
                            )
                        _append_loop_control(
                            working_messages,
                            {"role": "system", "content": rebound},
                            metadata_enabled=context_metadata_enabled,
                            source="agent_loop.completion_nudge",
                            anchor_after=_completion_anchor,
                            fragment_id=(
                                f"agent-loop:{tid}:iteration:{iteration}:"
                                f"completion-nudge:{completion_nudges_used}"
                            ),
                        )
                        logger.info(
                            "p5s2_completion_nudge_injected "
                            "sid=%s nudge=%d/%d incomplete=%d",
                            session_id,
                            completion_nudges_used,
                            self.max_completion_nudges,
                            len(incomplete),
                        )
                        # Skip the final emission and re-iterate. The
                        # next LLM call sees the nudge.
                        continue
                    if self._tracer:
                        self._tracer.record({
                            "kind": "gate",
                            "iter": iteration,
                            "which": "completion",
                            "passed": True,
                            "reason": "",
                        })

                # ─── Step6 SelfCheckGate（整合 verify_gate + reflection + 异体评分，
                # plans/2026-06-24-...）。pipeline on 时走编排；off 时落到下方 elif 原 verify_gate 块（BC）。
                # ⚠️ R2：补 `and response.content` 守卫，与原 verify_gate 块准入对齐。
                if (
                    iteration < _SELFCHECK_TIER3_AT
                    and self._self_check_gate is not None
                    and self._pipeline_problem_type is not None
                    and response.content
                ):
                    _ledger = (
                        self.receipt_store.load_session(session_id)
                        if self.receipt_store is not None else []
                    )
                    if scoped_evidence is not None:
                        _ledger = list(scoped_evidence.records)
                    _sc = await traced_call(
                        name="self_check_gate.check",
                        kind=SpanKind.GATE,
                        lifecycle_stage="gate",
                        attributes={"iteration": iteration, "gate": "self_check"},
                        invoke=lambda: self._self_check_gate.check(
                            problem_type=self._pipeline_problem_type,
                            assistant_text=response.content or "",
                            ledger=_ledger,
                            goal_text=self._extract_goal_text(working_messages),
                            failure_count=verify_nudges_used,
                            produced_artifacts=[
                                getattr(r, "tool_name", "?") for r in _ledger
                            ],
                            objective_evidence=[
                                f"receipt ok: tool={getattr(r, 'tool_name', '?')}"
                                for r in _ledger if getattr(r, "ok", True)
                            ],
                            workload_context=(
                                self._provider_workload_context_factory(
                                    "agent.external_evaluator"
                                )
                                if self._provider_workload_context_factory is not None
                                else None
                            ),
                        ),
                    )
                    if self._pipeline_observability:
                        yield self._pipeline_event("chat_v2_selfcheck", iteration, {
                            "passed": _sc.passed, "mode": _sc.mode,
                            "heterogeneous": _sc.heterogeneous,
                            "claims_unverified": _sc.claims_unverified,
                        })
                    if not _sc.passed and verify_nudges_used < self.max_verify_nudges:
                        verify_nudges_used += 1
                        _self_check_anchor = _latest_context_anchor(
                            working_messages,
                            fallback=f"agent-loop:{tid}:iteration:{iteration}:response",
                        )
                        if response.content:
                            _self_check_reply = _append_loop_transcript(
                                working_messages,
                                {"role": "assistant", "content": response.content},
                                metadata_enabled=context_metadata_enabled,
                                source="agent_loop.self_check_gate.trigger",
                                role="assistant",
                                fragment_id=(
                                    f"agent-loop:{tid}:iteration:{iteration}:"
                                    "self-check-trigger"
                                ),
                            )
                            _self_check_anchor = (
                                _context_fragment_id(_self_check_reply)
                                or _self_check_anchor
                            )
                        _append_loop_control(
                            working_messages,
                            {"role": "system", "content": (
                                "<自检> 自检未通过：声明与凭据不符。"
                                + _sc.reflection_instruction
                            )},
                            metadata_enabled=context_metadata_enabled,
                            source="agent_loop.self_check_nudge",
                            anchor_after=_self_check_anchor,
                            fragment_id=(
                                f"agent-loop:{tid}:iteration:{iteration}:"
                                f"self-check-nudge:{verify_nudges_used}"
                            ),
                        )
                        logger.info(
                            "self_check_nudge_injected sid=%s mode=%s nudge=%d",
                            session_id, _sc.mode, verify_nudges_used,
                        )
                        continue
                    # ─── 耗尽分支（MINOR①：复用 verify-exhausted 终态，勿静默放过）。
                    if (
                        not _sc.passed
                        and verify_nudges_used >= self.max_verify_nudges
                        and self.force_finish_via_tool_choice
                        and not _verify_final_done
                    ):
                        _verify_final_done = True
                        _force_finish_queued = True
                        _append_loop_control(
                            working_messages,
                            {"role": "system", "content": (
                                "自检多次未通过（声明与凭据不符），本轮必须 end_turn："
                                "向用户如实总结做了什么、哪些未能验证、建议下一步。不要再调用任何工具。"
                            )},
                            metadata_enabled=context_metadata_enabled,
                            source="agent_loop.self_check_exhausted",
                            anchor_after=_latest_context_anchor(
                                working_messages,
                                fallback=(
                                    f"agent-loop:{tid}:iteration:{iteration}:response"
                                ),
                            ),
                            fragment_id=(
                                f"agent-loop:{tid}:iteration:{iteration}:"
                                "self-check-exhausted"
                            ),
                            protected=True,
                        )
                        logger.warning(
                            "self_check_exhausted sid=%s nudge=%d/%d → force_finish",
                            session_id, verify_nudges_used, self.max_verify_nudges,
                        )
                        continue
                    # force_finish_via_tool_choice=False 时无强制总结轮 → 直发 verify_exhausted 终态。
                    if not _sc.passed and verify_nudges_used >= self.max_verify_nudges:
                        if self._tracer:
                            self._tracer.record({
                                "kind": "end", "iter": iteration,
                                "reason": "verify_exhausted",
                                "gate_summary": self._gate.summary(),
                            })
                        yield ErrorEvent(
                            type="error", task_id=tid, iteration=iteration,
                            reason="verify_exhausted",
                            detail=(
                                response.content
                                or f"self-check: all retries exhausted after "
                                   f"{verify_nudges_used} nudge(s)"
                            ),
                        )
                        return
                    # passed → 落到下面（不再走旧 verify_gate 块）

                # WI-T2.6 last-mile P0-3: VerifyGate end_turn 守门（PRD §3 D6）。
                # 同 completion_probe 模式 — 守门返回 outcome.passed=False 时
                # 回灌 D8 schema system message + continue；max_verify_nudges
                # 控制重试上限（PRD: failure_count==3 时调度 ephemeral → 仍
                # fail 才强退）。flag-off 时 verify_gate=None 跳过整段（BC）。
                # ⚠️ pipeline on 时上方 SelfCheckGate if 已处理 → 此 elif 跳过；off 时走原块（BC）。
                elif (
                    iteration < _SELFCHECK_TIER3_AT
                    and
                    self.verify_gate is not None
                    and getattr(self.verify_gate, "mode", "off") != "off"
                    and verify_nudges_used < self.max_verify_nudges
                    and response.content
                ):
                    try:
                        # 拉取本 session 的 sig-filtered ledger（N1 信任面已在
                        # ReceiptStore.load_session 内强制 hmac_verify）
                        ledger = (
                            self.receipt_store.load_session(session_id)
                            if self.receipt_store is not None else []
                        )
                        # WI-2.3: thread goal_text into verify_gate.check so
                        # GoalAlignment is populated when an active goal exists.
                        # BC: goal_text=None (no store / no active goal) → byte-identical
                        # to pre-WI-2.3 (check ignores None goal_text).
                        _vg_goal_text: Optional[str] = None
                        if self.session_goal_store is not None:
                            _gt_fn = getattr(
                                self.session_goal_store, "get_goal_text", None
                            )
                            if callable(_gt_fn):
                                _vg_goal_text = _gt_fn(session_id)
                        v_outcome = await traced_call(
                            name="verify_gate.check",
                            kind=SpanKind.GATE,
                            lifecycle_stage="gate",
                            attributes={"iteration": iteration, "gate": "verify"},
                            invoke=lambda: self.verify_gate.check(
                                assistant_text=response.content,
                                ledger=ledger,
                                goal_text=_vg_goal_text,
                                **({"scoped_evidence": scoped_evidence} if scoped_evidence is not None else {}),
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "verify_gate.check failed (sid=%s): %s — passing through",
                            session_id, exc,
                        )
                        v_outcome = None

                    if (v_outcome is not None and not v_outcome.passed
                            and v_outcome.unmatched_claims):
                        if self._tracer:
                            self._tracer.record({
                                "kind": "gate",
                                "iter": iteration,
                                "which": "verify",
                                "passed": False,
                                "reason": "unmatched_claims",
                            })
                        verify_nudges_used += 1

                        # WI-2.2: stagnation detection — if 2nd+ rebound and
                        # task_replanning text is nearly identical to previous
                        # round (difflib ratio > 0.85), the LLM is stuck in
                        # copy-paste instead of genuinely replanning. Skip to
                        # ephemeral immediately (don't waste the 2nd nudge).
                        _stagnant = False
                        if verify_nudges_used >= 2 and self.structured_reflection:
                            try:
                                from deskpet.agent.reflection import parse_reflection
                                import difflib as _difflib
                                _refl = parse_reflection(response.content or "")
                                _cur_replan = str(_refl.task_replanning) if _refl else ""
                                if (
                                    _prev_task_replanning
                                    and _cur_replan
                                    and _difflib.SequenceMatcher(
                                        None, _prev_task_replanning, _cur_replan
                                    ).ratio() > 0.85
                                ):
                                    _stagnant = True
                                    logger.info(
                                        "verify_replan_stagnant sid=%s nudge=%d "
                                        "ratio=%.2f — escalating to ephemeral",
                                        session_id, verify_nudges_used,
                                        _difflib.SequenceMatcher(
                                            None, _prev_task_replanning, _cur_replan
                                        ).ratio(),
                                    )
                                    try:
                                        from observability.metrics_sink import (
                                            record as _vg_metric,
                                        )
                                        _vg_metric("verify_replan_stagnant", {
                                            "nudge_count": int(verify_nudges_used),
                                        })
                                    except Exception:  # noqa: BLE001
                                        pass
                                if _refl:
                                    _prev_task_replanning = _cur_replan
                            except Exception:  # noqa: BLE001 — safe-fail
                                pass

                        # 失败计数达 max 或 stagnation → 调 ephemeral 救援
                        ephemeral_pass = False
                        if _stagnant or verify_nudges_used >= self.max_verify_nudges:
                            try:
                                ephemeral_pass = await (
                                    self.verify_gate.consult_ephemeral_subagent(
                                        ledger=ledger,
                                        failed_claims=v_outcome.unmatched_claims,
                                        assistant_text=response.content,
                                        workload_context=(
                                            self._provider_workload_context_factory(
                                                "agent.verify_ephemeral"
                                            )
                                            if self._provider_workload_context_factory is not None
                                            else None
                                        ),
                                    )
                                )
                            except Exception as exc:  # noqa: BLE001
                                logger.warning("ephemeral consult failed: %s", exc)
                            # WI-HM-1: emit ephemeral 救援判定到 metrics.jsonl
                            # （之前只 logger.info，未计数化 → 监控看不到救援率）.
                            try:
                                from observability.metrics_sink import (  # noqa: PLC0415
                                    record as _eph_metric,
                                )
                                _eph_metric(
                                    "ephemeral_rescued"
                                    if ephemeral_pass else "ephemeral_pass",
                                    {
                                        "nudge_count": int(verify_nudges_used),
                                        "ok": bool(ephemeral_pass),
                                    },
                                )
                            except Exception:  # noqa: BLE001 — metric 失败不阻 dispatch
                                pass

                        if not ephemeral_pass:
                            # WI-2.2: verify_exhausted — all layers (nudges +
                            # ephemeral) exhausted → emit terminal error + return.
                            # Only when we've either stagnated or used all nudges
                            # AND ephemeral failed.
                            if _stagnant or verify_nudges_used >= self.max_verify_nudges:
                                logger.warning(
                                    "verify_exhausted sid=%s nudge=%d/%d "
                                    "stagnant=%s",
                                    session_id, verify_nudges_used,
                                    self.max_verify_nudges, _stagnant,
                                )
                                try:
                                    from observability.metrics_sink import (
                                        record as _vg_metric,
                                    )
                                    _vg_metric("verify_exhausted", {
                                        "nudge_count": int(verify_nudges_used),
                                        "stagnant": bool(_stagnant),
                                    })
                                except Exception:  # noqa: BLE001
                                    pass
                                if (
                                    self.force_finish_via_tool_choice
                                    and not _verify_final_done
                                ):
                                    _verify_final_done = True
                                    _force_finish_queued = True
                                    _append_loop_control(
                                        working_messages,
                                        {
                                            "role": "system",
                                            "content": (
                                                "verify 多次未对齐 ledger，本轮必须 end_turn："
                                                "向用户如实总结做了什么、哪些未能验证、建议下一步。"
                                                "不要再调用任何工具。"
                                            ),
                                        },
                                        metadata_enabled=context_metadata_enabled,
                                        source="agent_loop.verify_exhausted",
                                        anchor_after=_latest_context_anchor(
                                            working_messages,
                                            fallback=(
                                                f"agent-loop:{tid}:iteration:"
                                                f"{iteration}:response"
                                            ),
                                        ),
                                        fragment_id=(
                                            f"agent-loop:{tid}:iteration:{iteration}:"
                                            "verify-exhausted"
                                        ),
                                        protected=True,
                                    )
                                    continue
                                if self._tracer:
                                    self._tracer.record({
                                        "kind": "end",
                                        "iter": iteration,
                                        "reason": "verify_exhausted",
                                        "gate_summary": self._gate.summary(),
                                    })
                                yield ErrorEvent(
                                    type="error",
                                    task_id=tid,
                                    iteration=iteration,
                                    reason="verify_exhausted",
                                    detail=(
                                        f"verify-gate: all retries exhausted after "
                                        f"{verify_nudges_used} nudge(s). "
                                        f"stagnant={_stagnant}"
                                    ),
                                )
                                return

                            # Still have nudges: inject rebound + 回灌 D8 schema
                            unmatched_lines = "\n".join(
                                f"  {i+1}. [unmatched_claim] {c.raw_text!r} — "
                                f"no receipt matched ({c.reason})"
                                for i, c in enumerate(v_outcome.unmatched_claims[:5])
                            )
                            rebound = (
                                f"[verify-gate] iteration={iteration} blocked end_turn.\n"
                                f"Failures:\n{unmatched_lines}\n"
                                f"Classification: unmatched_claim\n"
                                f"Next: please call the missing tool to actually "
                                f"perform the action you claimed, then end_turn again."
                            )
                            # WI-2.3: append goal alignment context to rebound
                            # when goal_text was provided and GoalAlignment is
                            # populated. goal_text=None → rebound unchanged (BC).
                            if (
                                v_outcome.goal_alignment is not None
                                and not v_outcome.goal_alignment.aligned
                            ):
                                ga = v_outcome.goal_alignment
                                _evidence_str = "\n".join(
                                    f"  - {e}"
                                    for e in ga.objective_evidence[:5]
                                ) or "  (无客观证据)"
                                rebound = (
                                    rebound
                                    + f"\n原始目标: {ga.goal_text}\n"
                                    + f"客观证据: {_evidence_str}\n"
                                    + f"缺口: {ga.gap}\n"
                                    + "→ 请重规划（结构化反思）后补齐，"
                                    + "使产物真满足原目标，而非换个说法过关。"
                                )
                            # WI-2.1: append reflection instruction when flag on.
                            # Flag off (default) → rebound string unchanged (BC).
                            if self.structured_reflection:
                                from deskpet.agent.reflection import _REFLECTION_INSTRUCTION
                                rebound = rebound + _REFLECTION_INSTRUCTION
                            _verify_anchor = _latest_context_anchor(
                                working_messages,
                                fallback=(
                                    f"agent-loop:{tid}:iteration:{iteration}:response"
                                ),
                            )
                            if response.content:
                                _verify_reply = _append_loop_transcript(
                                    working_messages,
                                    {
                                        "role": "assistant",
                                        "content": response.content,
                                    },
                                    metadata_enabled=context_metadata_enabled,
                                    source="agent_loop.verify_gate.trigger",
                                    role="assistant",
                                    fragment_id=(
                                        f"agent-loop:{tid}:iteration:{iteration}:"
                                        "verify-trigger"
                                    ),
                                )
                                _verify_anchor = (
                                    _context_fragment_id(_verify_reply)
                                    or _verify_anchor
                                )
                            _append_loop_control(
                                working_messages,
                                {"role": "system", "content": rebound},
                                metadata_enabled=context_metadata_enabled,
                                source="agent_loop.verify_nudge",
                                anchor_after=_verify_anchor,
                                fragment_id=(
                                    f"agent-loop:{tid}:iteration:{iteration}:"
                                    f"verify-nudge:{verify_nudges_used}"
                                ),
                            )
                            logger.info(
                                "verify_gate_nudge_injected sid=%s nudge=%d/%d "
                                "unmatched=%d ephemeral_pass=%s",
                                session_id, verify_nudges_used,
                                self.max_verify_nudges,
                                len(v_outcome.unmatched_claims),
                                ephemeral_pass,
                            )
                            # WI-T2.1 v3：真 emit 到 metrics.jsonl（MR-T-8 拦截
                            # 硬证据 — 用户 goal "verify_* event in metrics.jsonl"
                            # 把 fake-completion 拦截事件计数化，供监控面板看健康率）
                            try:
                                from observability.metrics_sink import (
                                    record as _verify_metric,
                                )
                                _verify_metric("verify_gate_nudge_injected", {
                                    "nudge_count": int(verify_nudges_used),
                                    "count": int(len(v_outcome.unmatched_claims)),
                                    "ok": bool(ephemeral_pass),
                                })
                            except Exception:  # noqa: BLE001 — metric 失败不阻 dispatch
                                pass
                            continue
                        else:
                            logger.info(
                                "verify_gate.ephemeral_rescued sid=%s", session_id,
                            )
                    elif self._tracer and v_outcome is not None:
                        self._tracer.record({
                            "kind": "gate",
                            "iter": iteration,
                            "which": "verify",
                            "passed": True,
                            "reason": "",
                        })

                # WI-B3 Companion+Code v1 — /goal end_turn rebound. Same
                # safe-fail pattern as completion_probe / verify_gate:
                # checker exception or done=False → inject a system
                # message and ``continue`` the loop so the next LLM call
                # sees the hint. Cap at ``goal.max_iterations`` so the
                # checker can't loop forever. flag-off (store / checker
                # = None, or no active goal, or goal.done already) →
                # skip the block entirely (BC).
                if (
                    iteration < _SELFCHECK_TIER3_AT
                    and
                    self.session_goal_store is not None
                    and self.goal_checker is not None
                ):
                    _goal = self.session_goal_store.get(session_id)
                    if (
                        _goal is not None
                        and not _goal.done
                        and _goal.iterations_used < _goal.max_iterations
                    ):
                        try:
                            _done, _hint = await traced_call(
                                name="goal_checker.check",
                                kind=SpanKind.GATE,
                                lifecycle_stage="gate",
                                attributes={"iteration": iteration, "gate": "goal"},
                                invoke=lambda: self.goal_checker.check(
                                    _goal.text,
                                    working_messages,
                                    **(
                                        {
                                            "workload_context": self._provider_workload_context_factory(
                                                "agent.goal_check"
                                            )
                                        }
                                        if self._provider_workload_context_factory is not None
                                        else {}
                                    ),
                                ),
                            )
                        except Exception as exc:  # noqa: BLE001 — safe-fail
                            logger.warning(
                                "goal_checker raised (sid=%s): %s — skipping check",
                                session_id, exc,
                            )
                            # R-T3 §15.4: 兜底也用 skipped 语义（check() 内已 safe-fail，
                            # 这里只作双重防护）。不默认 done=True。
                            _done, _hint = False, "goal_check=skipped"
                        # metric emit (best-effort, not blocking)
                        try:
                            from observability.metrics_sink import (  # noqa: PLC0415
                                record as _goal_metric,
                            )
                            _goal_metric("goal_checker_invoked", {
                                "ok": bool(_done),
                                "count": int(_goal.iterations_used),
                            })
                        except Exception:  # noqa: BLE001 — metric 失败不阻
                            pass
                        # R-T3 §15.4: goal_check=skipped → checker 降级，
                        # 无法正向确认 → 不 mark_done，但也不注入"未达成"nudge
                        # （我们不知道目标是否真达成，不能给 LLM 假信号）。
                        # 直接 fall-through 到 FinalEvent / evaluator gate。
                        if _hint == "goal_check=skipped":
                            logger.info(
                                "goal_checker.skipped sid=%s — "
                                "checker degraded, proceeding without goal confirmation",
                                session_id,
                            )
                            # 不 continue，让正常 FinalEvent 流程继续
                        elif not _done:
                            if self._tracer:
                                self._tracer.record({
                                    "kind": "gate",
                                    "iter": iteration,
                                    "which": "goal",
                                    "passed": False,
                                    "reason": str(_hint or "")[:200],
                                })
                            self.session_goal_store.increment_iteration(session_id)
                            # T1：落库 iterations_used，重启不归零。safe-fail
                            # 内置于 persist_iteration；getattr 兜底旧 store。
                            _pit = getattr(
                                self.session_goal_store,
                                "persist_iteration", None,
                            )
                            if _pit is not None:
                                await _pit(session_id)
                            # Append the assistant message that triggered
                            # this so the LLM sees its own prior end_turn
                            # output — symmetric with completion_probe /
                            # verify_gate rebound shape.
                            _goal_anchor = _latest_context_anchor(
                                working_messages,
                                fallback=(
                                    f"agent-loop:{tid}:iteration:{iteration}:response"
                                ),
                            )
                            if response.content:
                                _goal_reply = _append_loop_transcript(
                                    working_messages,
                                    {
                                        "role": "assistant",
                                        "content": response.content,
                                    },
                                    metadata_enabled=context_metadata_enabled,
                                    source="agent_loop.goal_checker.trigger",
                                    role="assistant",
                                    fragment_id=(
                                        f"agent-loop:{tid}:iteration:{iteration}:"
                                        "goal-trigger"
                                    ),
                                )
                                _goal_anchor = (
                                    _context_fragment_id(_goal_reply)
                                    or _goal_anchor
                                )
                            _append_loop_control(
                                working_messages,
                                {
                                    "role": "system",
                                    "content": (
                                        f"[goal] 未达成 "
                                        f"({_goal.iterations_used}/{_goal.max_iterations}): "
                                        f"{_hint}\n继续工作直到目标完成。"
                                    ),
                                },
                                metadata_enabled=context_metadata_enabled,
                                source="agent_loop.goal_nudge",
                                anchor_after=_goal_anchor,
                                fragment_id=(
                                    f"agent-loop:{tid}:iteration:{iteration}:"
                                    f"goal-nudge:{_goal.iterations_used}"
                                ),
                            )
                            logger.info(
                                "goal_checker_nudge_injected sid=%s "
                                "iter=%d/%d",
                                session_id,
                                _goal.iterations_used,
                                _goal.max_iterations,
                            )
                            continue
                        else:
                            if self._tracer:
                                self._tracer.record({
                                    "kind": "gate",
                                    "iter": iteration,
                                    "which": "goal",
                                    "passed": True,
                                    "reason": "",
                                })
                            self.session_goal_store.mark_done(session_id)
                            # 落库 done 终态（与 increment 落库对称）：否则
                            # load_persisted 只召回 status='active'，已完成目标
                            # 重启会复活成 active。getattr 兜底旧 store。
                            _pd = getattr(
                                self.session_goal_store,
                                "persist_done", None,
                            )
                            if _pd is not None:
                                await _pd(session_id)
                            logger.info(
                                "goal_checker.marked_done sid=%s",
                                session_id,
                            )

                # WI-2.4: external evaluator gate — BEFORE FinalEvent.
                # Only fires when:
                #   a) external_evaluator is wired (flag on), AND
                #   b) is_high_consequence_goal() returns True.
                # If evaluator returns verdict=revise → emit ErrorEvent
                # ("evaluator_revise") instead of FinalEvent so auto_resume
                # can spawn a replan. Runs at most ONCE per goal (here,
                # before FinalEvent — revise → replan → new run, no loop).
                # flag off (external_evaluator=None) → skip entirely (BC, 0 calls).
                # ⚠️ plans/2026-06-24-...：pipeline on（self._self_check_gate 非 None）时
                # 异体评分已由 Step6 SelfCheckGate 编排 → 此处跳过避免双重评分。
                if self.external_evaluator is not None and self._self_check_gate is None:
                    try:
                        from deskpet.agent.external_evaluator import (  # noqa: PLC0415
                            is_high_consequence_goal as _is_hcg,
                        )
                        _eval_ledger = (
                            self.receipt_store.load_session(session_id)
                            if self.receipt_store is not None else []
                        )
                        # Extract goal_text from session_goal_store if wired,
                        # else fall back to first user message text.
                        _eval_goal_text = ""
                        if self.session_goal_store is not None:
                            _gt_fn = getattr(
                                self.session_goal_store, "get_goal_text", None
                            )
                            if callable(_gt_fn):
                                _eval_goal_text = _gt_fn(session_id) or ""
                        if not _eval_goal_text:
                            # BC fallback: use first user message content
                            for _m in working_messages:
                                if _m.get("role") == "user":
                                    _eval_goal_text = str(_m.get("content") or "")
                                    break
                        if _is_hcg(_eval_goal_text, _eval_ledger, []):
                            _ev_result = await traced_call(
                                name="external_evaluator.evaluate",
                                kind=SpanKind.GATE,
                                lifecycle_stage="gate",
                                attributes={"iteration": iteration, "gate": "external_evaluator"},
                                invoke=lambda: self.external_evaluator.evaluate(
                                    original_goal=_eval_goal_text,
                                    produced_artifacts=[
                                        getattr(r, "tool_name", "unknown")
                                        for r in _eval_ledger
                                    ],
                                    objective_evidence=[
                                        f"receipt ok: tool={getattr(r, 'tool_name', '?')}"
                                        for r in _eval_ledger if getattr(r, "ok", True)
                                    ],
                                    conversation_summary=str(response.content or "")[:512],
                                    workload_context=(
                                        self._provider_workload_context_factory(
                                            "agent.external_evaluator"
                                        )
                                        if self._provider_workload_context_factory is not None
                                        else None
                                    ),
                                ),
                            )
                            if (
                                _ev_result.get("verdict") == "revise"
                                and _ev_result.get("quality_score", 10) < 6
                            ):
                                logger.info(
                                    "external_evaluator verdict=revise sid=%s "
                                    "quality_score=%d issues=%d → emit evaluator_revise",
                                    session_id,
                                    _ev_result["quality_score"],
                                    len(_ev_result.get("issues", [])),
                                )
                                try:
                                    from observability.metrics_sink import (  # noqa: PLC0415
                                        record as _eval_metric,
                                    )
                                    _eval_metric("evaluator_revise_triggered", {
                                        "quality_score": _ev_result["quality_score"],
                                        "issues_count": len(_ev_result.get("issues", [])),
                                    })
                                except Exception:  # noqa: BLE001 — metric 失败不阻
                                    pass
                                yield ErrorEvent(
                                    type="error",
                                    task_id=tid,
                                    iteration=iteration,
                                    reason="evaluator_revise",
                                    detail=(
                                        f"[external_evaluator] 质量不足 "
                                        f"(score={_ev_result['quality_score']}/10): "
                                        + "; ".join(_ev_result.get("issues", []))
                                    ),
                                )
                                return
                    except Exception as exc:  # noqa: BLE001 — safe-fail
                        logger.warning(
                            "external_evaluator gate failed (sid=%s): %s — passing through",
                            session_id, exc,
                        )
                _final_content = await _review_final_response_quality(self, response, user_request=loop_user_request, session_id=session_id, request_id=context_request_id or tid, iteration=iteration, model=use_model or "", prepared_context=prepared_context, current_tool_set=current_tool_set, totals=totals)
                # ─── Step7 收敛标记（自然收尾路径，仅观测；止损在循环顶部 2d-① 处理）。
                # plans/2026-06-24-...：模型自己 end_turn 正常收尾时 gate 尚未 terminate
                # （reason="running"），此处只发 converged 观测事件，**不触发止损**（止损只在触顶分支）。
                if self._convergence_controller is not None and self._pipeline_observability:
                    _v = self._convergence_controller.evaluate(
                        principal_resolved=True, unverified_claims=0,
                        gate_summary=self._gate.summary(),
                    )
                    yield self._pipeline_event("chat_v2_convergence", iteration, {
                        "converged": _v.converged,
                        "principal_resolved": _v.principal_resolved,
                        "stop_reason": _v.stop_reason, "report": "",
                    })

                # P6 Phase 6 — gate records the natural terminal state
                # before we emit the FinalEvent so consumers reading
                # gate.summary() after a run see SUCCESS / matching reason.
                self._gate.record_final_answer()
                if self._tracer:
                    self._tracer.record({
                        "kind": "end",
                        "iter": iteration,
                        "reason": response.stop_reason or "end_turn",
                        "gate_summary": self._gate.summary(),
                    })

                # WI-OH-4: 记忆 self-curation nudge — 在 FinalEvent 之后周期性
                # fire-and-forget 触发（curator None → BC，跳过整段；非 None 时
                # 每 _curation_every 回合调一次，异步不挡主回合）。
                self._maybe_fire_curation_nudge(session_id, working_messages)

                yield FinalEvent(
                    type="final",
                    task_id=tid,
                    iteration=iteration,
                    content=_final_content,
                    reasoning_content=response.reasoning_content,
                    stop_reason=response.stop_reason or "end_turn",
                    total_input_tokens=totals["input"],
                    total_output_tokens=totals["output"],
                    total_cache_read_tokens=totals["cache_read"],
                    total_cache_write_tokens=totals["cache_write"],
                )
                return

            for tool_call in response.tool_calls:
                _inject_loop_user_request(
                    tool_call, tool_schemas, loop_user_request=loop_user_request
                )
            if is_sentinel_run and any(
                call.name == "deepresearch" for call in response.tool_calls
            ):
                yield FinalEvent(
                    type="final", task_id=tid, iteration=iteration,
                    content=("Auto-resume sentinel cannot start deepresearch. "
                             "Please explicitly send a research request."),
                    total_input_tokens=totals["input"],
                    total_output_tokens=totals["output"],
                    total_cache_read_tokens=totals["cache_read"],
                    total_cache_write_tokens=totals["cache_write"],
                )
                return
            if await _suppress_repeated_tool_batch(
                self, response.tool_calls, working_messages,
                session_id=session_id, task_id=tid, iteration=iteration,
                metadata_enabled=context_metadata_enabled,
            ):
                continue
            group_id = _append_tool_batch_message(
                response, working_messages, task_id=tid, iteration=iteration,
                metadata_enabled=context_metadata_enabled,
            )
            exclusive_call = next((
                call for call in response.tool_calls
                if (
                    _tool_completion_semantics(self.tools, call.name)
                    == "accepted_async"
                    or call.name == "tool_activate"
                )
            ), None)
            selected_calls = list(response.tool_calls)
            if exclusive_call is not None and len(response.tool_calls) != 1:
                # accepted_async and tool_activate hand control to another
                # durable boundary.  A model can still return one of them in a
                # larger parallel batch.  Rejecting the whole batch asks the
                # model to repair its own wire format and can loop forever when
                # the provider repeats the same batch.  Execute the first
                # exclusive call deterministically and mark every sibling as
                # not executed; the next model turn receives the real
                # exclusive result and can retry only the siblings it still
                # needs.
                selected_calls = [exclusive_call]
                for call in response.tool_calls:
                    if call.id == exclusive_call.id:
                        continue
                    rejection = json.dumps({
                        "ok": False,
                        "error": "deferred_by_exclusive_tool",
                        "executed": False,
                        "exclusive_tool_call_id": exclusive_call.id,
                        "exclusive_tool_name": exclusive_call.name,
                        "hint": (
                            "This sibling call was not executed. Retry it only "
                            "if it is still needed after the exclusive result."
                        ),
                    }, ensure_ascii=False)
                    _append_loop_transcript(
                        working_messages,
                        {"role": "tool", "tool_call_id": call.id,
                         "name": call.name, "content": rejection},
                        metadata_enabled=context_metadata_enabled,
                        source="agent_loop.tool_result", role="tool",
                        fragment_id=f"{group_id}:tool:{call.id}",
                        causal_group_id=group_id,
                    )
            yield ToolBatchEvent(
                task_id=tid, iteration=iteration,
                tool_calls=tuple(selected_calls),
                canonical_messages=tuple(
                    copy.deepcopy(message) for message in working_messages
                ),
                feedback_state={
                    "iteration": iteration,
                    "evidence_gathered": self._evidence_gathered,
                    "evidence_nudges_used": self._evidence_nudges_used,
                    "tools_used_count": tools_used_count,
                    "skills_used_order": list(self._skills_used_order),
                    "force_finish_queued": _force_finish_queued,
                },
            )
            return

        # Hit max_iterations — spec §11.4 says emit warning + final.
        logger.warning("agent loop task %s hit max_iterations=%d", tid, self.max_iterations)
        # ─── Step7 止损（WI-2，plans/2026-06-24-...）：loop range 耗尽（每轮都 tool_use 跑满
        # max_iterations）也接 ConvergenceController，产出诚实止损报告，而非只发 error。
        # 此出口 gate 不一定 terminate()，summary()["reason"] 多半还是合成 "running" → 必须手动
        # 覆盖成 HARD_MAX_TURNS.value（"error_max_turns"），否则 resource_capped 判 False → 止损不触发。
        # pipeline off 时 self._convergence_controller=None → 保持原 warning+ErrorEvent（字节级 BC）。
        if self._convergence_controller is not None:
            _cap_summary = dict(self._gate.summary())
            _cap_summary["reason"] = TerminationReason.HARD_MAX_TURNS.value
            _verdict = self._convergence_controller.evaluate(
                principal_resolved=False,   # 跑满 range 必是每轮都在调工具没收尾 → 未收敛
                unverified_claims=0,
                gate_summary=_cap_summary,
            )
            if self._pipeline_observability:
                yield self._pipeline_event("chat_v2_convergence", self.max_iterations, {
                    "converged": _verdict.converged,
                    "principal_resolved": _verdict.principal_resolved,
                    "stop_reason": _verdict.stop_reason,
                    "report": _verdict.report,
                })
            if _verdict.should_stop_loss and _verdict.report:
                self._gate.record_final_answer()
                yield FinalEvent(
                    type="final",
                    task_id=tid,
                    iteration=self.max_iterations,
                    content=_verdict.report,
                    stop_reason="stop_loss",
                )
                return
        yield ErrorEvent(
            type="error",
            task_id=tid,
            iteration=self.max_iterations,
            reason="max_iterations",
            detail=f"exceeded {self.max_iterations} iterations without terminal stop_reason",
        )

    # ------------------------------------------------------------------
    # WI-OH-4 — memory self-curation nudge (fire-and-forget)
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_tool_names(messages: list[dict[str, Any]]) -> list[str]:
        """收集 working_messages 里 tool 调用名（role=='tool' 的 name）。run() 开头用它快照
        history 旧 tool（含 bundle.history 注入的带 name 旧 tool 消息），喂 self._history_tool_names。
        plans/2026-06-24-... §M2 改动 2b-1。"""
        return [m.get("name", "") for m in messages if m.get("role") == "tool"]

    @staticmethod
    def _extract_goal_text(working_messages: list[dict[str, Any]]) -> str:
        """取首个 user 消息作 goal_text（复用 external_evaluator 块的 BC fallback 逻辑）。"""
        for m in working_messages:
            if m.get("role") == "user":
                return str(m.get("content") or "")
        return ""

    def _requested_compression_model(self) -> str:
        provider = self.compression_model_provider
        if callable(provider):
            value = str(provider() or "").strip()
            if value:
                return value
        return self.compression_model

    def _resolve_compression_model(
        self,
        provider_chain: Any,
        *,
        requested_model: str | None = None,
    ) -> Any:
        """Resolve one exact compaction provider; never walk a fallback chain."""

        if self.compression_model_resolver is None:
            return None
        providers = provider_chain
        if not providers:
            providers = getattr(self.llm, "providers", ()) or ()
            if isinstance(providers, dict):
                providers = tuple(providers.values())
        from deskpet.agent.compression_model_resolver import (
            CompressionModelCandidate,
        )

        candidates = tuple(
            CompressionModelCandidate(
                provider_id=str(
                    getattr(provider, "id", None)
                    or getattr(provider, "provider_id", None)
                    or type(provider).__name__
                ),
                model_id=str(getattr(provider, "model", None) or "unknown"),
                provider=provider,
            )
            for provider in providers
        )
        return self.compression_model_resolver.resolve(
            str(requested_model or self._requested_compression_model()),
            session_chain=candidates,
        )

    @staticmethod
    def _prepared_toolset_summary(current_tool_set: Any) -> dict[str, Any]:
        if current_tool_set is None:
            return {"adapter_state": "canonical"}
        direct = tuple(getattr(current_tool_set, "direct", ()) or ())
        activated = tuple(getattr(current_tool_set, "activated", ()) or ())
        all_caps = (*direct, *activated)
        return {
            "direct_names": [str(cap.ref.name) for cap in direct],
            "activated_names": [str(cap.ref.name) for cap in activated],
            "schema_hashes": {
                str(cap.ref.name): str(cap.ref.schema_hash) for cap in all_caps
            },
            "persisted_tool_scope_revision": int(
                getattr(current_tool_set, "revision", 0) or 0
            ),
            "registry_revision": int(
                getattr(current_tool_set, "registry_revision", 0) or 0
            ),
            "policy_fingerprint": str(
                getattr(current_tool_set, "policy_fingerprint", "") or ""
            ),
            "schema_fingerprint": str(
                getattr(current_tool_set, "schema_fingerprint", "") or ""
            ),
            "adapter_state": "canonical",
        }

    async def _flush_context_snapshot(
        self,
        *,
        cycle_id: str,
        session_id: str,
        request_id: str,
        working_messages: list[dict[str, Any]],
        prepared_context: Any,
        current_tool_set: Any,
    ) -> Any:
        """Project authorities and durably flush one derived cycle snapshot."""

        if self.context_projector is None or self.context_snapshot_store is None:
            return None
        from deskpet.context_os_e2e_hooks import consume_context_os_e2e_fault
        if consume_context_os_e2e_fault("snapshot_cas_timeout"):
            raise TimeoutError("snapshot_cas_timeout")
        last_user = next(
            (
                str(message.get("content") or "")
                for message in reversed(working_messages)
                if message.get("role") == "user"
            ),
            "",
        )

        async def _project() -> Any:
            return await self.context_projector.project(
                effective_sid=session_id,
                request_id=request_id,
                user_text=last_user,
            )

        snapshot = await _project()
        if snapshot is None:
            return None
        handle = getattr(prepared_context, "active_snapshot_handle", None)
        if (
            handle is not None
            and getattr(handle, "session_id", None) == snapshot.session_id
            and getattr(handle, "task_scope_id", None) == snapshot.task_scope_id
        ):
            expected_revision = int(handle.row_revision)
        else:
            stored = await self.context_snapshot_store.get(
                snapshot.session_id, snapshot.task_scope_id
            )
            expected_revision = (
                int(stored.handle.row_revision) if stored is not None else 0
            )
        tool_summary = self._prepared_toolset_summary(current_tool_set)

        async def _write(projection: Any, expected: int) -> Any:
            from deskpet.memory.context_snapshot_store import (
                await_snapshot_commit_ack,
            )

            task = asyncio.create_task(
                self.context_snapshot_store.flush_once(
                    cycle_id,
                    projection,
                    expected_row_revision=expected,
                    prepared_toolset_summary=tool_summary,
                )
            )
            return await await_snapshot_commit_ack(task)

        try:
            receipt = await _write(snapshot, expected_revision)
        except Exception as exc:
            from deskpet.memory.context_snapshot_store import (
                SnapshotConflictError,
            )

            if not isinstance(exc, SnapshotConflictError):
                raise
            # One bounded re-read/re-project retry.  No authority is mutated;
            # the store remains a derived CAS projection only.
            latest = await self.context_snapshot_store.get(
                snapshot.session_id, snapshot.task_scope_id
            )
            refreshed = await _project()
            if refreshed is None or latest is None:
                raise
            if latest.last_compaction_cycle_id != cycle_id:
                # Another compaction cycle won the CAS. Replaying this older
                # cycle on top of it can make stale authority look current;
                # keep the raw context and let the next request replan.
                raise
            receipt = await _write(refreshed, latest.handle.row_revision)
        prepared_context.active_snapshot_handle = receipt.new_handle
        return receipt

    async def _execute_coverage_compaction_jobs(
        self,
        *,
        cycle_id: str,
        prepared_context: Any,
        resolution: Any,
    ) -> None:
        jobs = tuple(
            getattr(prepared_context, "coverage_compaction_jobs", ()) or ()
        )
        if not jobs:
            return
        if self.context_segment_store is None or resolution is None:
            raise RuntimeError("coverage_compaction_owner_unavailable")
        candidate = resolution.candidates[0]
        committed_ranges: set[tuple[int, int, str]] = set()
        from deskpet.memory.context_segment_store import CoverageCommitProof

        for job in jobs:
            source_range = (
                int(job.first_message_id),
                int(job.last_message_id),
                str(job.source_hash),
            )
            if source_range in committed_ranges:
                continue
            summary = await self.compressor.compress_coverage_job(
                job,
                resolved_provider=candidate.provider,
                resolved_model=candidate.model_id,
                compaction_cycle_id=cycle_id,
                **(
                    {
                        "workload_context": self._provider_workload_context_factory(
                            "agent.context_compaction"
                        )
                    }
                    if self._provider_workload_context_factory is not None
                    else {}
                ),
            )
            proof = CoverageCommitProof(
                message_ids=tuple(int(item) for item in job.message_ids),
                source_hash=str(job.source_hash),
                valid=True,
                child_source_hashes=tuple(job.child_source_hashes),
            )
            logger.info(
                "coverage_commit_started cycle_id=%s first_message_id=%s last_message_id=%s",
                cycle_id,
                job.first_message_id,
                job.last_message_id,
            )
            await asyncio.wait_for(
                self.context_segment_store.commit_summary(
                    str(job.session_id),
                    tuple(job.child_segment_ids),
                    summary_text=summary,
                    proof=proof,
                    provider_id=str(candidate.provider_id),
                    model_id=str(candidate.model_id),
                    token_estimates={
                        "deskpet-conservative-v1": max(1, len(summary) // 3)
                    },
                ),
                timeout=10.0,
            )
            logger.info(
                "coverage_commit_completed cycle_id=%s first_message_id=%s last_message_id=%s",
                cycle_id,
                job.first_message_id,
                job.last_message_id,
            )
            committed_ranges.add(source_range)

    def _pipeline_event(self, ev_type: str, iteration: int, payload: dict) -> "PipelineEvent":
        """构造七步流水线 WS 观测事件（plans/2026-06-24-... §M2 改动 2f）。"""
        return PipelineEvent(
            type=ev_type,
            task_id=getattr(self, "_current_tid", None) or "",
            iteration=iteration,
            payload=payload,
        )

    def _maybe_fire_curation_nudge(
        self,
        session_id: str,
        working_messages: list[dict[str, Any]],
    ) -> None:
        """Periodically kick off a memory self-curation nudge.

        BC: ``self._memory_curator is None`` → return immediately (zero
        behaviour change). When a curator is wired, a per-session turn counter
        is bumped each terminal turn; once it hits ``_curation_every`` the
        nudge is scheduled as a fire-and-forget asyncio task so it never blocks
        the FinalEvent / user turn (照 vector_worker 异步模式).

        Any failure here is swallowed — the curation path must never affect the
        chat turn.
        """
        curator = self._memory_curator
        if curator is None:
            return
        try:
            # WI-OH-4 fix (2026-06-23, real-E2E caught): the per-session count
            # lives on the curator SINGLETON, not on this _AgentLoop. main.py
            # rebuilds a fresh loop per chat turn (build_agent in _run_chat),
            # so an in-loop counter reset every turn and never reached the
            # threshold in production (the unit test passed only because it
            # reused one loop instance). The threshold itself stays here (from
            # config). Defensive getattr: curators without bump_turn (old/mock)
            # fall back to firing every terminal turn rather than never.
            _bump = getattr(curator, "bump_turn", None)
            count = _bump(session_id) if callable(_bump) else self._curation_every
            if count % self._curation_every != 0:
                return
            # Snapshot the conversation so the background task reads a stable
            # list even if `working_messages` keeps mutating after we return.
            recent = list(working_messages)

            async def _run_curation() -> None:
                try:
                    decisions = await curator.nudge(
                        recent,
                        **(
                            {
                                "workload_context": self._provider_workload_context_factory(
                                    "memory.curation"
                                )
                            }
                            if self._provider_workload_context_factory is not None
                            else {}
                        ),
                    )
                    logger.info(
                        "oh4_curation_nudge sid=%s turn=%d decisions=%d remembered=%d",
                        session_id,
                        count,
                        len(decisions),
                        sum(1 for d in decisions if getattr(d, "should_remember", False)),
                    )
                except Exception as exc:  # noqa: BLE001 — never escape bg task
                    logger.debug(
                        "oh4_curation_nudge_failed sid=%s: %s", session_id, exc
                    )

            task = asyncio.create_task(_run_curation())
            if (
                self._provider_workload_router is not None
                and self._provider_workload_context_factory is not None
            ):
                self._provider_workload_router.track_task(
                    self._provider_workload_context_factory("memory.curation"),
                    task,
                )
        except Exception as exc:  # noqa: BLE001 — scheduling must not break turn
            logger.debug(
                "oh4_curation_schedule_failed sid=%s: %s", session_id, exc
            )

    # ------------------------------------------------------------------
    # WI-4.2 — post-compaction skill body remount
    # ------------------------------------------------------------------

    _REMOUNT_MARKER = "[已重挂技能 / remounted skills]"
    _REMOUNT_TOKEN_BUDGET = 25_000  # chars (proxy for tokens; 1 token ≈ 4 chars)

    _frozen_skill_scope = staticmethod(frozen_skill_scope)
    _skill_scope_candidates = staticmethod(skill_scope_candidates)

    async def _prepare_frozen_skill_remounts(
        self,
        messages: list[dict[str, Any]],
        *,
        prepared_context: Any,
        external_feedback: Mapping[str, Any],
    ) -> None:
        await prepare_frozen_skill_remounts(
            messages,
            loader=self.skill_loader,
            prepared_context=prepared_context,
            external_feedback=external_feedback,
            target=self._frozen_skill_remounts,
        )

    def _remount_skills(self, messages: list[dict], session_id: str, *, prepared_context: Any = None) -> list[dict]:
        return remount_skills(self, messages, session_id, prepared_context=prepared_context)


# Runtime AgentEvent union type for callers that want isinstance checks.
AgentEventUnion = Union[
    AssistantMessageEvent,
    ToolCallEvent,
    ToolBatchEvent,
    ToolResultEvent,
    AsyncHandoffEvent,
    FinalEvent,
    ErrorEvent,
    SubagentCompletionEvent,
]
