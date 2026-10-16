# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""LLM collaborator for DeskPet's ReAct driver.

Provider turns and completion gates stay here. Tool requests cross a typed
``ToolBatchEvent`` boundary; Harness alone executes them and resumes the run.
"""

from __future__ import annotations

import asyncio
import json
import json as _json
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Union

from deskpet.workflows.trace import SpanContext, SpanStatus
from llm.types import ToolCall

from agent import errors as agent_errors
from agent.task_id import new_task_id
from agent.tool_context_persistence import ToolContextPersistenceMixin
from agent.workflow_trace import HarnessTraceSession, TraceInstrumentationError

if TYPE_CHECKING:
    from deskpet.execution.contracts import ProviderLaunchSnapshot
# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）
# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）
from .agent_loop_domains.compaction import CompactionMixin
from .agent_loop_domains.compaction import ContextCompactedEvent as ContextCompactedEvent
from .agent_loop_domains.context_attempts import ContextAttemptMixin
from .agent_loop_domains.core import PipelineEvent as PipelineEvent
from .agent_loop_domains.final_gates import FinalGateMixin
from .agent_loop_domains.model_call import _REPEAT_NUDGE_MSG as _REPEAT_NUDGE_MSG
from .agent_loop_domains.model_call import AssistantDeltaEvent as AssistantDeltaEvent
from .agent_loop_domains.model_call import AssistantMessageEvent, ModelCallMixin, ToolBatchEvent
from .agent_loop_domains.model_call import ProviderChainFallbackEvent as ProviderChainFallbackEvent
from .agent_loop_domains.model_call import _inject_loop_user_request as _inject_loop_user_request
from .agent_loop_domains.model_call import (
    _invalidate_stale_mcp_catalog as _invalidate_stale_mcp_catalog,
)
from .agent_loop_domains.model_call import (
    _tool_declares_user_request as _tool_declares_user_request,
)
from .agent_loop_domains.run_setup import RunSetupMixin
from .agent_loop_domains.support import _SELFCHECK_TIER3_AT as _SELFCHECK_TIER3_AT
from .agent_loop_domains.support import AgentEvent, ErrorEvent, FinalEvent, _RunState, logger
from .agent_loop_domains.turn_prep import _SELFCHECK_EVERY as _SELFCHECK_EVERY
from .agent_loop_domains.turn_prep import _SELFCHECK_TIER2_AT as _SELFCHECK_TIER2_AT
from .agent_loop_domains.turn_prep import TurnPrepMixin
from .agent_loop_domains.turn_prep import _build_selfcheck_message as _build_selfcheck_message
from .agent_loop_domains.verify_gates import VerifyGateMixin


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
        getattr(initial_budget, "generation_reserve", 0) or llm_kwargs.get("max_tokens", 0) or 0
    )


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
        payload.get("accepted_event_id") or envelope.get("accepted_event_id") or ""
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
        "request_id": str(payload.get("request_id") or envelope.get("request_id") or ""),
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


# ───────────────────── WI-1.3 goal-anchor constants ─────────────────────
#
# Every ``_GOAL_ANCHOR_EVERY`` iterations, when there is an active goal,
# inject a brief ``[目标锚定]`` system message into working_messages so the
# model is reminded of the original objective and doesn't drift into
# intermediate side-tasks.  This is ORTHOGONAL to the goal_checker nudge:
#   anchor = "don't drift away" (preventive, periodic)
#   nudge  = "you haven't finished yet" (reactive, on end_turn)
_GOAL_ANCHOR_EVERY = 5


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


@dataclass
class ToolCallEvent(AgentEvent):
    tool_call: ToolCall | None = None

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_call"


@dataclass
class ToolResultEvent(AgentEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    result: str = ""  # JSON string
    # Typed harness outcomes keep their non-success status through the legacy
    # product presenter.  Legacy AgentLoop callers omit these fields and retain
    # the historical succeeded behavior.
    outcome_status: str = "succeeded"
    outcome_error: dict | None = None
    # 七步流水线 Step5 标签（plans/2026-06-24-...）：{"step":5,"observation_summary":"..."}。
    # pipeline off → None = BC（前端不读即无影响）。
    pipeline_label: dict | None = None

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


class AgentLoop(
    ContextAttemptMixin,
    RunSetupMixin,
    TurnPrepMixin,
    CompactionMixin,
    ModelCallMixin,
    FinalGateMixin,
    VerifyGateMixin,
    ToolContextPersistenceMixin,
):
    """ReAct-style driver around LLMRegistry + ToolRegistry."""

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        task_id: str | None = None,
        tools_filter: list[str] | None = None,
        tool_names_filter: list[str] | None = None,
        model: str | None = None,
        session_id: str = "default",
        stream: bool = False,
        provider_chain: list[Any] | None = None,
        loop_user_request: str | None = None,
        is_sentinel_run: bool = False,
        trace_context: SpanContext | None = None,
        trace_request_id: str | None = None,
        trace_turn_id: str | None = None,
        prepared_context: Any | None = None,
        context_request_id: str | None = None,
        launch_operation_id: str | None = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
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
            terminal_status = SpanStatus.OK if terminal_event_seen else SpanStatus.CANCELLED
            terminal_error = (
                None
                if terminal_event_seen
                else {
                    "type": type(exc).__name__,
                    "reason": "consumer_cancelled_before_terminal",
                }
            )
            raise
        except GeneratorExit as exc:
            terminal_status = SpanStatus.OK if terminal_event_seen else SpanStatus.CANCELLED
            terminal_error = (
                None
                if terminal_event_seen
                else {
                    "type": type(exc).__name__,
                    "reason": "consumer_closed_before_terminal",
                }
            )
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
        task_id: str | None = None,
        tools_filter: list[str] | None = None,
        tool_names_filter: list[str] | None = None,
        model: str | None = None,
        session_id: str = "default",
        stream: bool = False,
        provider_chain: list[Any] | None = None,
        loop_user_request: str | None = None,
        is_sentinel_run: bool = False,
        prepared_context: Any | None = None,
        context_request_id: str | None = None,
        launch_operation_id: str | None = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
        **llm_kwargs: Any,
    ) -> AsyncIterator[AgentEvent]:
        """Drive ReAct; ``stream`` emits deltas when the registry supports
        ``chat_with_fallback_stream`` and otherwise preserves non-streaming.
        """
        state = _RunState(
            messages=messages,
            task_id=task_id,
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
            llm_kwargs=llm_kwargs,
        )
        state.flow = None
        async with aclosing(self._begin_run(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            return

        state.flow = None
        async with aclosing(self._plan_chain_budget(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            return

        await self._init_turn_state(state)

        state.flow = None
        async with aclosing(self._run_initial_coverage(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            return

        state.iteration_offset = max(0, int(state.external_feedback.get("iteration", 0)))
        for state.iteration in range(state.iteration_offset + 1, self.max_iterations + 1):
            state.flow = None
            async with aclosing(self._prepare_turn(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow == "stop":
                return

            # P6 Phase 6: escalating in-loop self-check (soft nudge).
            # The legacy _TOOL_BUDGET_HARD_MSG soft cap is GONE — the
            # TerminationGate now enforces tool_budget_hard with a HARD
            # break and emits ErrorEvent(error_tool_budget). The selfcheck
            # tier injection below is complementary (gentle reflection
            # nudge to coax stop_reason=end_turn earlier).

            state.flow = None
            async with aclosing(self._model_turn(state)) as _phase:
                async for _event in _phase:
                    yield _event
            if state.flow == "continue":
                continue
            if state.flow == "stop":
                return

        state.flow = None
        async with aclosing(self._finish_max_iterations(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            return


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
