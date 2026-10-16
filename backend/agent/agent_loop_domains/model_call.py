# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Calling the model and handling its reply: provider chain, streaming, usage, empty replies and the tool batch."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Any

from agent.session_activity import args_hash
from agent.termination import TerminationReason
from agent.tool_use_shim import _raw_to_response
from agent.workflow_trace import traced_call, traced_iterate
from deskpet.workflows.trace import SpanKind
from llm.errors import LLMBudgetExceededError, LLMProviderError
from llm.types import ChatResponse, ToolCall

from .core import AgentLoopCore
from .support import (
    AgentEvent,
    ErrorEvent,
    FinalEvent,
    _append_loop_control,
    _append_loop_transcript,
    _context_fragment_id,
    _latest_context_anchor,
    _RunState,
    logger,
)


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
    loop_user_request: str | None,
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
        spec = next((item for item in all_specs() if getattr(item, "name", None) == name), None)
        semantics = getattr(spec, "completion_semantics", None)
        if semantics in {"sync", "accepted_async"}:
            return semantics
    return "sync"


async def _suppress_repeated_tool_batch(
    loop: Any,
    calls: list[ToolCall],
    messages: list[dict[str, Any]],
    *,
    session_id: str,
    task_id: str,
    iteration: int,
    metadata_enabled: bool,
) -> bool:
    if loop.activity_store is None:
        return False

    activity = await loop.activity_store.get(session_id)
    signatures = {} if activity is None else dict(activity.tool_signature_window)
    repeated = next(
        (
            (call.name, int(signatures.get(f"{call.name}:{args_hash(call.arguments)}", 0)) + 1)
            for call in calls
            if int(signatures.get(f"{call.name}:{args_hash(call.arguments)}", 0))
            >= loop._signature_repeat_threshold - 1
        ),
        None,
    )
    if repeated is None:
        return False
    name, count = repeated
    anchor = _latest_context_anchor(
        messages, fallback=f"agent-loop:{task_id}:iteration:{iteration}:response"
    )
    _append_loop_control(
        messages,
        {"role": "system", "content": _REPEAT_NUDGE_MSG.format(name=name, count=count)},
        metadata_enabled=metadata_enabled,
        source="agent_loop.signature_repeat_nudge",
        anchor_after=anchor,
        fragment_id=f"agent-loop:{task_id}:iteration:{iteration}:signature-repeat-nudge:{count}",
    )
    return True


def _append_tool_batch_message(
    response: ChatResponse,
    messages: list[dict[str, Any]],
    *,
    task_id: str,
    iteration: int,
    metadata_enabled: bool,
) -> str:
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": response.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in response.tool_calls
        ],
    }
    if response.reasoning_content:
        assistant["reasoning_content"] = response.reasoning_content
    group_id = f"agent-loop:{task_id}:iteration:{iteration}:tool-group"
    _append_loop_transcript(
        messages,
        assistant,
        metadata_enabled=metadata_enabled,
        source="agent_loop.assistant_tool_group",
        role="assistant",
        fragment_id=f"{group_id}:assistant",
        causal_group_id=group_id,
    )
    return group_id


# Module-level template so tests can pin it. Filled with ``.format(
# name=..., count=...)`` at injection time.
_REPEAT_NUDGE_MSG = (
    "你刚连续 {count} 次用同一个工具 {name} 调用同样的参数，"
    "结果不会变。看一下 tool_result 的 hint 字段，"
    "或者换个思路 / 换个工具。"
)


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
class ToolBatchEvent(AgentEvent):
    """Pause point for an external harness-owned tool executor."""

    tool_calls: tuple[ToolCall, ...] = ()
    canonical_messages: tuple[Mapping[str, Any], ...] = ()
    feedback_state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_batch"


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


class ModelCallMixin(AgentLoopCore):
    """Calling the model and handling its reply: provider chain, streaming, usage, empty replies and the tool batch."""

    __slots__ = ()

    async def _model_turn(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """定期检查、调用模型、处理回复。"""

        # Count tools used so far in this loop run (tracked at each
        # tool dispatch). `tools_used` mirrors gate.state.tools_used.
        state.tools_used = state.tools_used_count

        await self._periodic_checks(state)

        # WI-4a: 周期性 [目标锚定] 注入已删除 —— 改由循环前的 always-on 单点注入
        # (见上方 wi4a_goal_anchor_always_on)。always-on 那条 role=system 常驻、
        # 不被压、整轮恒 ≤1 条,周期重注是冗余且会堆多条同文 system。防 drift 由
        # always-on 常驻 + WI-3 结构化摘要保任务共同覆盖,能力不丢。

        state.flow = None
        async with aclosing(self._call_provider(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            state.flow = "stop"
            return

        state.flow = None
        async with aclosing(self._handle_model_reply(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "continue":
            state.flow = "continue"
            return
        if state.flow == "stop":
            state.flow = "stop"
            return

    async def _handle_model_reply(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """记用量、处理空回复、交出助手消息，再按有无工具调用走收尾闸门或工具批。"""

        self._record_usage(state)

        state.flow = None
        async with aclosing(self._repair_empty_response(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "continue":
            state.flow = "continue"
            return
        if state.flow == "stop":
            state.flow = "stop"
            return

        # The guard is intentionally consecutive. A real tool call or
        # user-visible answer proves that the provider recovered, so a
        # later isolated reasoning-only turn starts a fresh recovery
        # window instead of inheriting old nudges from earlier work.
        state.reasoning_only_nudges_used = 0
        yield AssistantMessageEvent(
            type="assistant_message",
            task_id=state.tid,
            iteration=state.iteration,
            content=state.response.content,
            reasoning_content=state.response.reasoning_content,
            tool_calls=list(state.response.tool_calls),
            stop_reason=state.response.stop_reason,
            model=state.response.model,
        )

        state.flow = None
        async with aclosing(self._handle_final_response(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "continue":
            state.flow = "continue"
            return
        if state.flow == "stop":
            state.flow = "stop"
            return

        state.flow = None
        async with aclosing(self._dispatch_tool_batch(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "continue":
            state.flow = "continue"
            return
        if state.flow == "stop":
            state.flow = "stop"
            return
        state.flow = "stop"
        return

    async def _call_provider(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """调用模型（含流式、超时、重试与回落）。"""

        try:
            if state.chain_mode:
                # ─── Phase 3: walk the chain ───
                # On transient LLMProviderError, yield a
                # ProviderChainFallbackEvent and try the next
                # provider. On success, accept the response and
                # break. If every provider fails, emit
                # ErrorEvent(reason="all_providers_failed").

                state.flow = None
                async with aclosing(self._call_provider_chain(state)) as _phase:
                    async for _event in _phase:
                        yield _event
                if state.flow is not None:
                    return
            elif state.stream_capable:
                async with aclosing(self._call_provider_streaming(state)) as _phase:
                    async for _event in _phase:
                        yield _event
            else:
                state.call_llm_kwargs = (
                    {**state.llm_kwargs, "tool_choice": "none"}
                    if state._force_finish_next
                    else state.llm_kwargs
                )
                state.call_llm_kwargs = {**state.launch_call_kwargs, **state.call_llm_kwargs}
                state._attempt_tools = (
                    None if state._force_finish_next else (state.tool_schemas or None)
                )
                state._attempt_provider = getattr(
                    self.llm, "_provider", getattr(self.llm, "_active_provider", self.llm)
                )
                state._attempt_purpose = (
                    "force_finish" if state._force_finish_next else "agent_response"
                )
                state.response = await self._run_context_attempt(
                    provider=state._attempt_provider,
                    session_id=state.session_id,
                    request_id=state.context_request_id or state.tid,
                    attempt_id=f"{state.context_request_id or state.tid}:iter:{state.iteration}:nonstream",
                    purpose=state._attempt_purpose,
                    messages=state.working_messages,
                    tools=state._attempt_tools,
                    fallback_model=state.use_model or "",
                    prepared_context=state.prepared_context,
                    current_tool_set=state.current_tool_set,
                    compression=state._latest_compression_report,
                    invoke=lambda: traced_call(
                        name="llm.chat_with_fallback",
                        kind=SpanKind.LLM,
                        lifecycle_stage="llm",
                        attributes={
                            "iteration": state.iteration,
                            "model": str(state.use_model or ""),
                            "stream": False,
                            "message_count": len(state.working_messages),
                            "tool_schema_count": len(state.tool_schemas),
                        },
                        invoke=lambda: self.llm.chat_with_fallback(
                            state.working_messages,
                            tools=state._attempt_tools,
                            model=state.use_model,
                            **state.call_llm_kwargs,
                        ),
                    ),
                )
        except LLMBudgetExceededError as exc:
            yield ErrorEvent(
                type="error",
                task_id=state.tid,
                iteration=state.iteration,
                reason="budget_exceeded",
                detail=str(exc),
            )
            state.flow = "stop"
            return
        except RuntimeError as exc:
            if not str(exc).startswith(
                ("tool_context_persist_failed", "provider_context_budget_exceeded")
            ):
                raise
            yield ErrorEvent(
                type="error",
                task_id=state.tid,
                iteration=state.iteration,
                reason=(
                    "provider_context_budget_exceeded"
                    if str(exc).startswith("provider_context_budget_exceeded")
                    else "tool_context_persist_failed"
                ),
                detail=str(exc),
            )
            state.flow = "stop"
            return
        except LLMProviderError as exc:
            yield ErrorEvent(
                type="error",
                task_id=state.tid,
                iteration=state.iteration,
                reason="llm_error",
                detail=str(exc),
                # WI-R5: carry the relay error code so the frontend
                # can show 余额不足 / key 失效 friendly messages.
                error_class=getattr(exc, "error_class", "") or "",
            )
            state.flow = "stop"
            return

    async def _call_provider_chain(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """链式模式：按提供方链逐个尝试，直到拿到回复。"""

        state.response = None  # type: ignore[assignment]
        state.last_exc: Exception | None = None
        for state.idx, state.prov in enumerate(state.provider_chain):  # type: ignore[arg-type]
            try:
                state._attempt_tools = (
                    None if state._force_finish_next else (state.tool_schemas or None)
                )
                state._attempt_purpose = (
                    "force_finish" if state._force_finish_next else "agent_response"
                )
                state.raw = await self._run_context_attempt(
                    provider=state.prov,
                    session_id=state.session_id,
                    request_id=state.context_request_id or state.tid,
                    attempt_id=(
                        f"{state.context_request_id or state.tid}:iter:{state.iteration}:provider:{state.idx}"
                    ),
                    purpose=state._attempt_purpose,
                    messages=state.working_messages,
                    tools=state._attempt_tools,
                    fallback_model=state.use_model or "",
                    prepared_context=state.prepared_context,
                    current_tool_set=state.current_tool_set,
                    compression=state._latest_compression_report,
                    invoke=lambda prov=state.prov: traced_call(
                        name="llm.chat_with_tools",
                        kind=SpanKind.LLM,
                        lifecycle_stage="llm",
                        attributes={
                            "iteration": state.iteration,
                            "provider": str(getattr(prov, "id", f"provider_{state.idx}")),
                            "stream": False,
                            "message_count": len(state.working_messages),
                            "tool_schema_count": len(state.tool_schemas),
                        },
                        invoke=lambda: prov.chat_with_tools(
                            state.working_messages,
                            tools=state._attempt_tools,
                            max_tokens=int(state.llm_kwargs.get("max_tokens", 8192)),
                            temperature=state.llm_kwargs.get("temperature"),
                            response_format=state.llm_kwargs.get("response_format"),
                            tool_choice=("none" if state._force_finish_next else None),
                            **state.launch_call_kwargs,
                        ),
                    ),
                )
            except (LLMProviderError, RuntimeError) as exc:
                state._authority_error = _non_fallback_provider_error(exc)
                if _should_raise_provider_runtime_error(exc, state._authority_error):
                    raise
                if state._authority_error is not None:
                    if state._authority_error == "tool_catalog_stale":
                        _invalidate_stale_mcp_catalog(self.tools, state.current_tool_set)
                    yield ErrorEvent(
                        type="error",
                        task_id=state.tid,
                        iteration=state.iteration,
                        reason=state._authority_error,
                        detail=str(exc),
                        error_class=state._authority_error,
                    )
                    state.flow = "stop"
                    return
                if state.launch_call_kwargs:
                    raise
                state.last_exc = exc
                state.prov_id = getattr(state.prov, "id", f"provider_{state.idx}")
                state.next_idx = state.idx + 1
                if state.next_idx < len(state.provider_chain):  # type: ignore[arg-type]
                    state.next_prov = state.provider_chain[state.next_idx]  # type: ignore[index]
                    state.next_id = getattr(state.next_prov, "id", f"provider_{state.next_idx}")
                    state.reason = str(exc) or type(exc).__name__
                    logger.warning(
                        "provider_chain_fallback from=%s to=%s reason=%s sid=%s tid=%s",
                        state.prov_id,
                        state.next_id,
                        state.reason[:200],
                        state.session_id,
                        state.tid,
                    )
                    yield ProviderChainFallbackEvent(
                        type="provider_chain_fallback",
                        task_id=state.tid,
                        iteration=state.iteration,
                        session_id=state.session_id,
                        from_=state.prov_id,
                        to=state.next_id,
                        reason=state.reason,
                    )
                else:
                    logger.warning(
                        "provider_chain_last_provider_failed id=%s reason=%s sid=%s",
                        state.prov_id,
                        str(exc)[:200],
                        state.session_id,
                    )
                continue
            else:
                state.response = _raw_to_response(state.raw)
                break

        if state.response is None:
            # All providers in the chain raised.
            state.tried = len(state.provider_chain)  # type: ignore[arg-type]
            state.last_text = str(state.last_exc) if state.last_exc else "unknown"
            if isinstance(state.last_exc, RuntimeError) and str(state.last_exc).startswith(
                "provider_context_budget_exceeded"
            ):
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=state.iteration,
                    reason="provider_context_budget_exceeded",
                    detail=state.last_text,
                )
                state.flow = "stop"
                return
            # P6 Phase 6 — record terminal error reason on the
            # gate so callers reading summary() see the real
            # cause (otherwise the gate would stay "running").
            self._gate.record_error(TerminationReason.ALL_PROVIDERS_FAILED)
            yield ErrorEvent(
                type="error",
                task_id=state.tid,
                iteration=state.iteration,
                reason="all_providers_failed",
                detail=(f"tried {state.tried}, last_error: {state.last_text}"),
                # 把最后一个 provider 的结构化 error_class 透传给前端，
                # 让 chain 全失败路径也能 surface 友好分类（如
                # empty_api_key→"请重新登录" / relay 402→充值），而不是
                # 退化成无分类的通用错误。
                error_class=getattr(state.last_exc, "error_class", "") or "",
            )
            state.flow = "stop"
            return

    async def _call_provider_streaming(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """单个提供方的流式调用；流式失败时回落到非流式。"""

        # Streaming path: forward delta events, accumulate
        # the final dict, then build the same ChatResponse
        # the non-streaming path would have produced.
        state.final_dict: dict | None = None
        state.delta_count = 0
        state.stream_failed_with: Exception | None = None
        state.call_llm_kwargs = (
            {**state.llm_kwargs, "tool_choice": "none"}
            if state._force_finish_next
            else state.llm_kwargs
        )
        state.call_llm_kwargs = {**state.launch_call_kwargs, **state.call_llm_kwargs}
        state._attempt_tools = None if state._force_finish_next else (state.tool_schemas or None)
        state._attempt_provider = getattr(
            self.llm, "_provider", getattr(self.llm, "_active_provider", self.llm)
        )
        state._attempt_purpose = "force_finish" if state._force_finish_next else "agent_response"
        async with aclosing(self._stream_provider_reply(state)) as _phase:
            async for _event in _phase:
                yield _event

        state.needs_nonstream_fallback = (
            state.stream_failed_with is not None
            or state.final_dict is None
            or (
                state.delta_count == 0
                and not state.final_dict.get("content")
                and not state.final_dict.get("tool_calls")
            )
        )
        if (
            state.launch_call_kwargs
            and not state.provider_launch_snapshot.supports_idempotent_launch
        ):
            state.needs_nonstream_fallback = False
            if state.stream_failed_with is not None:
                raise state.stream_failed_with
        if state.needs_nonstream_fallback:
            if state.stream_failed_with is None:
                # Empty-stream case (the relay didn't actually stream).
                logger.warning(
                    "agent_loop_stream_fallback_to_nonstream delta_count=%d",
                    state.delta_count,
                )
            state.response = await self._run_context_attempt(
                provider=state._attempt_provider,
                session_id=state.session_id,
                request_id=state.context_request_id or state.tid,
                attempt_id=f"{state.context_request_id or state.tid}:iter:{state.iteration}:nonstream-fallback",
                purpose=state._attempt_purpose,
                messages=state.working_messages,
                tools=state._attempt_tools,
                fallback_model=state.use_model or "",
                prepared_context=state.prepared_context,
                current_tool_set=state.current_tool_set,
                compression=state._latest_compression_report,
                invoke=lambda: traced_call(
                    name="llm.chat_with_fallback",
                    kind=SpanKind.LLM,
                    lifecycle_stage="llm",
                    attributes={
                        "iteration": state.iteration,
                        "model": str(state.use_model or ""),
                        "stream": False,
                        "stream_fallback": True,
                        "message_count": len(state.working_messages),
                        "tool_schema_count": len(state.tool_schemas),
                    },
                    invoke=lambda: self.llm.chat_with_fallback(
                        state.working_messages,
                        tools=state._attempt_tools,
                        model=state.use_model,
                        **state.call_llm_kwargs,
                    ),
                ),
            )
        else:
            # Convert to ChatResponse to share the rest of the
            # iteration code with the non-streaming path.

            state.response = _raw_to_response(state.final_dict)

    async def _stream_provider_reply(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """流式调用：转发增量事件并累积最终回复。"""

        try:
            async for state.ev in self._iterate_context_attempt(
                provider=state._attempt_provider,
                session_id=state.session_id,
                request_id=state.context_request_id or state.tid,
                attempt_id=f"{state.context_request_id or state.tid}:iter:{state.iteration}:stream",
                purpose=state._attempt_purpose,
                messages=state.working_messages,
                tools=state._attempt_tools,
                fallback_model=state.use_model or "",
                prepared_context=state.prepared_context,
                current_tool_set=state.current_tool_set,
                compression=state._latest_compression_report,
                iterator=lambda: traced_iterate(
                    name="llm.chat_with_fallback_stream",
                    kind=SpanKind.LLM,
                    lifecycle_stage="llm",
                    attributes={
                        "iteration": state.iteration,
                        "model": str(state.use_model or ""),
                        "stream": True,
                        "message_count": len(state.working_messages),
                        "tool_schema_count": len(state.tool_schemas),
                    },
                    iterator=lambda: self.llm.chat_with_fallback_stream(  # type: ignore[attr-defined]
                        state.working_messages,
                        tools=state._attempt_tools,
                        model=state.use_model,
                        **state.call_llm_kwargs,
                    ),
                ),
            ):
                state.ev_type = state.ev.get("type")
                if state.ev_type == "delta":
                    state.delta_count += 1
                    yield AssistantDeltaEvent(
                        type="assistant_delta",
                        task_id=state.tid,
                        iteration=state.iteration,
                        content=state.ev.get("content", ""),
                        kind="content",
                        invocation_id=state.ev.get("invocation_id"),
                        stream_epoch=state.ev.get("stream_epoch"),
                        provisional=bool(state.ev.get("provisional", False)),
                    )
                elif state.ev_type == "delta_reasoning":
                    state.delta_count += 1
                    yield AssistantDeltaEvent(
                        type="assistant_delta",
                        task_id=state.tid,
                        iteration=state.iteration,
                        content=state.ev.get("content", ""),
                        kind="reasoning",
                        invocation_id=state.ev.get("invocation_id"),
                        stream_epoch=state.ev.get("stream_epoch"),
                        provisional=bool(state.ev.get("provisional", False)),
                    )
                elif state.ev_type == "retract":
                    yield AssistantDeltaEvent(
                        type="assistant_delta",
                        task_id=state.tid,
                        iteration=state.iteration,
                        invocation_id=state.ev.get("invocation_id"),
                        stream_epoch=state.ev.get("stream_epoch"),
                        provisional=True,
                        retract_provisional=True,
                    )
                elif state.ev_type == "final":
                    state.final_dict = state.ev
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
            state.stream_failed_with = stream_exc
            logger.warning(
                "agent_loop_stream_failed_falling_back error=%s",
                str(stream_exc)[:200],
            )

    def _record_usage(self, state: _RunState) -> None:
        """累计本轮用量、记录追踪与费用。"""

        state.launch_call_kwargs = {}
        state.totals["input"] += state.response.usage.input_tokens
        state.totals["output"] += state.response.usage.output_tokens
        state.totals["cache_read"] += state.response.usage.cache_read_tokens
        state.totals["cache_write"] += state.response.usage.cache_write_tokens
        # 第 3 刀: 记录 relay 真实 prompt 大小,喂下一轮 compaction 判定。
        if state.response.usage.input_tokens > state._last_real_prompt_tokens:
            state._last_real_prompt_tokens = state.response.usage.input_tokens
        if self._tracer:
            self._tracer.record(
                {
                    "kind": "llm_out",
                    "iter": state.iteration,
                    "stop_reason": state.response.stop_reason,
                    "content_preview": (state.response.content or "")[:200],
                    "tool_calls": [
                        {"name": tc.name, "args": tc.arguments} for tc in state.response.tool_calls
                    ],
                    "usage": (
                        state.response.usage.model_dump()
                        if hasattr(state.response.usage, "model_dump")
                        else {}
                    ),
                }
            )

        # P6 Phase 6 — record the turn (advances turns_used and
        # optionally adds to cost_usd if the response carries a
        # cost_usd extra; ChatUsage has no such field today so we
        # pass 0.0). The gate's allows_call check at top of next
        # iteration uses turns_used to decide whether to keep going.
        state._cost_delta = 0.0
        state._usage = getattr(state.response, "usage", None)
        if state._usage is not None:
            state._maybe_cost = getattr(state._usage, "cost_usd", None)
            if isinstance(state._maybe_cost, (int, float)):
                state._cost_delta = float(state._maybe_cost)
        self._gate.record_turn(cost_delta_usd=state._cost_delta)

    async def _repair_empty_response(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """模型回复为空：按次数上限补提示重来。"""

        if not (state.response.content or "").strip() and not state.response.tool_calls:
            state.has_hidden_reasoning = bool((state.response.reasoning_content or "").strip())
            state.empty_reason = (
                "model_reasoning_only_response"
                if state.has_hidden_reasoning
                else "model_empty_response"
            )
            state.max_reasoning_only_nudges = 3
            if state.reasoning_only_nudges_used >= state.max_reasoning_only_nudges:
                logger.warning(
                    "model_non_actionable_response_exhausted sid=%s tid=%s iteration=%d reason=%s",
                    state.session_id,
                    state.tid,
                    state.iteration,
                    state.empty_reason,
                )
                yield ErrorEvent(
                    type="error",
                    task_id=state.tid,
                    iteration=state.iteration,
                    reason=state.empty_reason,
                    detail=(
                        "The model exhausted three recovery nudges after "
                        "ending turns with no tool call or user-visible "
                        "answer."
                    ),
                )
                state.flow = "stop"
                return

            state.reasoning_only_nudges_used += 1
            state.reasoning_reply = _append_loop_transcript(
                state.working_messages,
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": state.response.reasoning_content,
                },
                metadata_enabled=state.context_metadata_enabled,
                source="agent_loop.reasoning_only.trigger",
                role="assistant",
                fragment_id=(
                    f"agent-loop:{state.tid}:iteration:{state.iteration}:reasoning-only-trigger"
                ),
            )
            _append_loop_control(
                state.working_messages,
                {
                    "role": "system",
                    "content": (
                        f"Recovery attempt {state.reasoning_only_nudges_used} of "
                        f"{state.max_reasoning_only_nudges}. "
                        "Your previous turn contained "
                        + ("only internal reasoning" if state.has_hidden_reasoning else "no output")
                        + " and no user-visible answer or tool call. "
                        "Do not end a turn this way. If your reasoning "
                        "selected an available tool, emit the actual "
                        "structured tool call now. Otherwise provide a "
                        "concise user-facing answer."
                    ),
                },
                metadata_enabled=state.context_metadata_enabled,
                source="agent_loop.reasoning_only.nudge",
                anchor_after=(
                    _context_fragment_id(state.reasoning_reply)
                    or _latest_context_anchor(
                        state.working_messages,
                        fallback=(
                            f"agent-loop:{state.tid}:iteration:{state.iteration}:reasoning-only-trigger"
                        ),
                    )
                ),
                fragment_id=(
                    f"agent-loop:{state.tid}:iteration:{state.iteration}:reasoning-only-nudge"
                ),
            )
            logger.info(
                "model_non_actionable_response_retried sid=%s tid=%s iteration=%d reason=%s",
                state.session_id,
                state.tid,
                state.iteration,
                state.empty_reason,
            )
            state.flow = "continue"
            return

    async def _dispatch_tool_batch(self, state: _RunState) -> AsyncGenerator[AgentEvent, None]:
        """本轮有工具调用：去重、排他调用分批，交出工具批事件。"""

        for state.tool_call in state.response.tool_calls:
            _inject_loop_user_request(
                state.tool_call, state.tool_schemas, loop_user_request=state.loop_user_request
            )
        if state.is_sentinel_run and any(
            call.name == "deepresearch" for call in state.response.tool_calls
        ):
            yield FinalEvent(
                type="final",
                task_id=state.tid,
                iteration=state.iteration,
                content=(
                    "Auto-resume sentinel cannot start deepresearch. "
                    "Please explicitly send a research request."
                ),
                total_input_tokens=state.totals["input"],
                total_output_tokens=state.totals["output"],
                total_cache_read_tokens=state.totals["cache_read"],
                total_cache_write_tokens=state.totals["cache_write"],
            )
            state.flow = "stop"
            return
        if await _suppress_repeated_tool_batch(
            self,
            state.response.tool_calls,
            state.working_messages,
            session_id=state.session_id,
            task_id=state.tid,
            iteration=state.iteration,
            metadata_enabled=state.context_metadata_enabled,
        ):
            state.flow = "continue"
            return
        state.group_id = _append_tool_batch_message(
            state.response,
            state.working_messages,
            task_id=state.tid,
            iteration=state.iteration,
            metadata_enabled=state.context_metadata_enabled,
        )
        state.exclusive_call = next(
            (
                call
                for call in state.response.tool_calls
                if (
                    _tool_completion_semantics(self.tools, call.name) == "accepted_async"
                    or call.name == "tool_activate"
                )
            ),
            None,
        )
        state.selected_calls = list(state.response.tool_calls)
        if state.exclusive_call is not None and len(state.response.tool_calls) != 1:
            # accepted_async and tool_activate hand control to another
            # durable boundary.  A model can still return one of them in a
            # larger parallel batch.  Rejecting the whole batch asks the
            # model to repair its own wire format and can loop forever when
            # the provider repeats the same batch.  Execute the first
            # exclusive call deterministically and mark every sibling as
            # not executed; the next model turn receives the real
            # exclusive result and can retry only the siblings it still
            # needs.
            state.selected_calls = [state.exclusive_call]
            for state.call in state.response.tool_calls:
                if state.call.id == state.exclusive_call.id:
                    continue
                state.rejection = json.dumps(
                    {
                        "ok": False,
                        "error": "deferred_by_exclusive_tool",
                        "executed": False,
                        "exclusive_tool_call_id": state.exclusive_call.id,
                        "exclusive_tool_name": state.exclusive_call.name,
                        "hint": (
                            "This sibling call was not executed. Retry it only "
                            "if it is still needed after the exclusive result."
                        ),
                    },
                    ensure_ascii=False,
                )
                _append_loop_transcript(
                    state.working_messages,
                    {
                        "role": "tool",
                        "tool_call_id": state.call.id,
                        "name": state.call.name,
                        "content": state.rejection,
                    },
                    metadata_enabled=state.context_metadata_enabled,
                    source="agent_loop.tool_result",
                    role="tool",
                    fragment_id=f"{state.group_id}:tool:{state.call.id}",
                    causal_group_id=state.group_id,
                )
        yield ToolBatchEvent(
            task_id=state.tid,
            iteration=state.iteration,
            tool_calls=tuple(state.selected_calls),
            canonical_messages=tuple(copy.deepcopy(message) for message in state.working_messages),
            feedback_state={
                "iteration": state.iteration,
                "evidence_gathered": self._evidence_gathered,
                "evidence_nudges_used": self._evidence_nudges_used,
                "tools_used_count": state.tools_used_count,
                "skills_used_order": list(self._skills_used_order),
                "force_finish_queued": state._force_finish_queued,
            },
        )
