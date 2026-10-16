# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Streaming tool calls and single stream attempts."""

from __future__ import annotations

import asyncio
import asyncio as _asyncio
import json as _json
import re as _re
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from agent.context_report import (
    auto_context_attempt_iter,
    mark_current_attempt_sent,
    mark_current_attempt_transport_retry,
)
from llm.context_messages import current_provider_attempt_options, split_wire_messages
from llm.errors import LLMProviderError

from providers._response_sanitizer import sanitize_response
from providers.dispatch_handoff import current_dispatch_handoff

if TYPE_CHECKING:
    from deskpet.execution.contracts import ProviderLaunchSnapshot
from .core import OpenAICompatibleCore
from .support import (
    _is_anthropic_endpoint,
    _is_tool_choice_400,
    _log_cache_hit_rate,
    _merge_code_params,
    _record_tool_choice_none_unsupported,
    _stabilize_prefix,
    _stamp_cache_control,
    _temperature_for_model,
    logger,
)

# Keep this threshold aligned with
# ``harness.drivers.react._MAX_PROVIDER_TOOL_CALLS_PER_BATCH``.  The provider
# retains one extra call as an overflow sentinel, then closes the SSE stream
# early so the driver can reject the whole batch and ask the model to replan.
# Without this transport-side bound, a malformed upstream response can keep
# dripping hundreds of calls indefinitely before the driver's guard runs.
_MAX_PROVIDER_TOOL_CALLS_BEFORE_REPLAN = 32


@dataclass
class _StreamAttemptState:
    """StreamingToolMixin._stream_one_attempt 的运行状态：原函数的局部变量与参数（切段用）。"""

    _args_str: Any = None
    _buf: Any = None
    _cand: Any = None
    _content: Any = None
    _extracted: Any = None
    _idx: Any = None
    _invalid_esc: Any = None
    _label: Any = None
    _parse_ok: Any = None
    _r: Any = None
    args: Any = None
    args_buf: Any = None
    args_parse_error: Any = None
    assembled_tools: Any = None
    body_bytes: Any = None
    body_text: Any = None
    buf: Any = None
    c0: Any = None
    choices: Any = None
    client: Any = None
    content_type: Any = None
    data: Any = None
    data_str: Any = None
    delta: Any = None
    delta_keys_seen: Any = None
    empty_response: Any = None
    ev_line: Any = None
    event: Any = None
    events: Any = None
    final_model: Any = None
    final_stop: Any = None
    final_usage: Any = None
    finish_reason: Any = None
    fn: Any = None
    fr: Any = None
    full_content: Any = None
    full_reasoning: Any = None
    idx: Any = None
    is_new_tool_call: Any = None
    j: Any = None
    joined: Any = None
    line: Any = None
    msg: Any = None
    parsed: Any = None
    payload: Any = None
    payload_str: Any = None
    raw_lines: Any = None
    recovered_chunks: Any = None
    repair_candidates: Any = None
    repair_label: Any = None
    repaired: Any = None
    request_headers: Any = None
    response: Any = None
    rtxt: Any = None
    sse_lines: Any = None
    tc: Any = None
    tc_dict: Any = None
    tcd: Any = None
    tool_batch_overflow: Any = None
    tool_buffers: Any = None
    transport_retries: Any = None
    txt: Any = None
    usage: Any = None


@dataclass
class _StreamToolsCallState:
    """StreamingToolMixin._chat_stream_with_tools_impl 的运行状态：原函数的局部变量与参数（切段用）。"""

    flow: Any = None
    _: Any = None
    _advice: Any = None
    _attempt_options: Any = None
    _cache_boundary: Any = None
    _human_msg: Any = None
    _human_type: Any = None
    _msg: Any = None
    _phase: Any = None
    attempt: Any = None
    backoffs: Any = None
    body: Any = None
    content: Any = None
    delay: Any = None
    ev: Any = None
    event: Any = None
    fallback_result: Any = None
    idx: Any = None
    last_exc: Any = None
    launch_headers: Any = None
    launch_operation_id: Any = None
    launch_retry_mode: Any = None
    max_tokens: Any = None
    messages: Any = None
    msg: Any = None
    payload: Any = None
    preview: Any = None
    provider_launch_snapshot: Any = None
    response_format: Any = None
    retry_mode: Any = None
    rf_type: Any = None
    role: Any = None
    stripped_already: Any = None
    temp: Any = None
    temperature: Any = None
    tool_choice: Any = None
    tool_choice_stripped: Any = None
    tools: Any = None
    transient: Any = None
    used_messages: Any = None
    used_payload: Any = None


class StreamingToolMixin(OpenAICompatibleCore):
    """Streaming tool calls and single stream attempts."""

    __slots__ = ()

    async def chat_stream_with_tools(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
        tool_choice: str | None = None,
        retry_mode: str = "resilient",
        launch_operation_id: str | None = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
    ):
        if retry_mode not in {"resilient", "at_most_once"}:
            raise ValueError(f"unsupported retry_mode: {retry_mode}")
        if current_dispatch_handoff() is not None:
            retry_mode = "at_most_once"
        async for event in auto_context_attempt_iter(
            provider=self,
            messages=messages,
            tools=tools,
            model_id=self.model,
            mark_sent_at_dispatch=False,
            generation_reserve=max_tokens,
            iterator=lambda: self._chat_stream_with_tools_impl(
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                tool_choice=tool_choice,
                retry_mode=retry_mode,
                launch_operation_id=launch_operation_id,
                provider_launch_snapshot=provider_launch_snapshot,
            ),
        ):
            yield event

    async def _chat_stream_with_tools_impl(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
        tool_choice: str | None = None,
        retry_mode: str = "resilient",
        launch_operation_id: str | None = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
    ):
        """P4-S25 A1: streaming version of chat_with_tools.

        Yields events as they arrive from the SSE stream:

            {"type": "delta", "content": str}           # text token chunk
            {"type": "delta_reasoning", "content": str} # thinking-mode chunk
            {"type": "final",                            # terminal event
             "content": str,
             "reasoning_content": str,
             "tool_calls": [...],
             "stop_reason": str,
             "model": str,
             "usage": {...}}

        The `final` event always fires once at the end (success or error
        — but on error we raise instead of yielding final). Tool calls
        are emitted only via `final.tool_calls` (assembled from streamed
        deltas) — not as their own event — so callers can treat them as
        a regular function-call response. Streaming JUST the content is
        the user-facing win: thinking-mode models like deepseek-v4-pro
        spend 30+ seconds in reasoning, and now the user sees the visible
        content trickle in as it generates instead of staring at a
        blank screen until the whole response lands.
        """

        state = _StreamToolsCallState(
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            tool_choice=tool_choice,
            retry_mode=retry_mode,
            launch_operation_id=launch_operation_id,
            provider_launch_snapshot=provider_launch_snapshot,
        )
        state.launch_headers, state.launch_retry_mode = self._launch_request_parts(
            state.launch_operation_id,
            state.provider_launch_snapshot,
        )
        if state.launch_retry_mode == "at_most_once":
            state.retry_mode = "at_most_once"
        state.temp = _temperature_for_model(
            self.model,
            state.temperature if state.temperature is not None else self.temperature,
        )
        self.last_usage = None
        # Phase 1.3 (D4): 前缀稳定纪律。在装 payload 之前先抹平历史里
        # 会抖动的字段（tool-call-only assistant 的 content / 空
        # reasoning_content），让出站字节前缀跨轮稳定 —— prefix cache
        # 命中的硬前提。下游所有分支（reasoning-400 strip 重试 /
        # _stamp_cache_control / 非流 fallback）都建立在这个稳定副本上。
        state.messages, state._ = split_wire_messages(state.messages)
        state._attempt_options = current_provider_attempt_options()
        state._cache_boundary = (
            state._attempt_options.cache_boundary if state._attempt_options is not None else None
        )
        state.messages = _stabilize_prefix(state.messages)

        self._log_outbound_messages(state)

        self._build_stream_payload(state)

        state.flow = None
        async with aclosing(self._stream_at_most_once(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            return

        # Streaming retry layer: same transient-error policy as the
        # non-streaming path. Bumped to 2 retries since SSE is more
        # exposed to mid-stream disconnects.
        state.transient = (
            httpx.RemoteProtocolError,
            httpx.ConnectTimeout,
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        )
        # P4-S25 streaming retry: the relay / sealos proxies sometimes drop
        # the connection mid-stream (RemoteProtocolError "Server
        # disconnected without sending a response"). Two attempts
        # weren't enough — log captured a real run with both retries
        # failing 60s apart. Three attempts with longer backoff give
        # the proxy actual recovery time. RemoteProtocolError on a
        # closed socket fails fast, so worst-case isn't 60×3 — it's
        # 60s on the first attempt + a few seconds for each subsequent
        # quick-fail.
        state.backoffs = (0.0,) if current_dispatch_handoff() is not None else (0.0, 1.0, 3.0)
        state.last_exc: Exception | None = None
        # Used messages — may swap to a stripped version if we hit the
        # reasoning_content 400 mid-stream (rare, but old sessions still
        # have NULL reasoning_content rows).
        state.used_messages = state.messages
        state.used_payload = state.payload
        state.tool_choice_stripped = False
        state.flow = None
        async with aclosing(self._stream_with_transient_retries(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            return
        assert state.last_exc is not None
        # P5-S2 A2: include actionable diagnosis in the user-facing error.
        # The base "LLM 调用失败 (ConnectError)" was opaque — users couldn't
        # tell apart "base_url wrong" from "upstream proxy dropped me".
        state._human_type = type(state.last_exc).__name__
        state._human_msg = (str(state.last_exc) or state._human_type).replace("\n", " ")[:300]
        state._phase = (
            "pre-handshake"
            if isinstance(state.last_exc, (httpx.ConnectTimeout, httpx.ConnectError))
            else "mid-stream-drop"
            if isinstance(state.last_exc, httpx.RemoteProtocolError)
            else "read-timeout"
            if isinstance(state.last_exc, httpx.ReadTimeout)
            else "transient-other"
        )
        state.flow = None
        async with aclosing(self._fallback_to_nonstream(state)) as _phase:
            async for _event in _phase:
                yield _event
        if state.flow == "stop":
            return
        self._raise_retries_exhausted(state)

    def _log_outbound_messages(self, state: _StreamToolsCallState) -> None:
        """调试日志：逐条打印发往模型的消息预览。"""

        # 临时调试：打印实际发送的消息
        logger.info(
            f"[DEBUG_PERSONA] STREAM Sending to model={self.model}, base_url={self.base_url}"
        )
        for state.idx, state.msg in enumerate(state.messages):
            state.role = state.msg.get("role", "?")
            state.content = state.msg.get("content", "")
            state.preview = (
                state.content[:300] if isinstance(state.content, str) else str(state.content)[:300]
            )
            logger.info(
                f"[DEBUG_PERSONA] STREAM Message {state.idx} ({state.role}): {state.preview}"
            )

    def _raise_retries_exhausted(self, state: _StreamToolsCallState) -> None:
        """重试用尽：按失败阶段给出可操作的说明并抛错。"""

        # Continue to raise the original stream error with diagnosis.
        state._advice = {
            "pre-handshake": (
                f"无法连接到 LLM endpoint {self.base_url}（DNS/TCP/TLS 握手失败）。"
                "可能原因：base_url 拼写错（如 .invalid 占位符）、上游服务 down、"
                "或本机网络/防火墙阻塞。请在 Settings → LLM Providers 检查 base_url 并"
                "可考虑添加备用 provider。"
            ),
            "mid-stream-drop": (
                f"流式响应被 {self.base_url} 中途切断（连续 {len(state.backoffs)} 次）。"
                "通常是中转站负载均衡 idle-timeout 或长响应触发 proxy reset。"
                "降低 max_tokens、缩短 conversation history、或添加备用 provider 可缓解。"
            ),
            "read-timeout": (
                f"等待 {self.base_url} 返回数据超时（连续 {len(state.backoffs)} 次）。"
                "模型生成可能过慢或上游排队。可降级到更快的模型或增加 timeout。"
            ),
            "transient-other": (f"连接 {self.base_url} 时遇到 {state._human_type}。"),
        }.get(state._phase, f"连接 {self.base_url} 失败：{state._human_type}")
        raise LLMProviderError(
            f"LLM 调用失败 [{state._phase}]: {state._advice} 原始错误: {state._human_msg}"
        ) from state.last_exc

    def _build_stream_payload(self, state: _StreamToolsCallState) -> None:
        """组装流式请求体：工具、输出格式与缓存标记。"""

        state.payload: dict = {
            "model": self.model,
            "messages": state.messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": state.temp,
            "max_tokens": state.max_tokens,
        }
        state.payload = _merge_code_params(state.payload, self.code_params)
        logger.info(
            "openai_compat_outbound fn=chat_stream_with_tools model=%s "
            "reasoning_effort=%s extra_body=%s",
            state.payload.get("model"),
            state.payload.get("reasoning_effort"),
            state.payload.get("extra_body"),
        )
        if state.tools:
            state.payload["tools"] = state.tools
            state.payload["tool_choice"] = state.tool_choice or "auto"
        if state.response_format is not None:
            state.payload["response_format"] = state.response_format
            state.rf_type = (
                state.response_format.get("type")
                if isinstance(state.response_format, dict)
                else None
            )
            if state.rf_type in ("json_object", "json_schema"):
                state.payload["format"] = "json"
        if (
            state.messages
            and any(m.get("role") == "system" for m in state.messages)
            and _is_anthropic_endpoint(self.base_url)
        ):
            state.payload["messages"] = _stamp_cache_control(
                state.messages, boundary_index=state._cache_boundary
            )

    async def _fallback_to_nonstream(self, state: _StreamToolsCallState):
        """流式中途断开时退回非流式调用补出最终结果。"""

        # P5-S2 D1: stream → non-stream same-provider fallback.
        # Relay-style OpenAI-compatible proxies idle-timeout long SSE streams (>~30s) and reset
        # the connection. A single POST that returns the whole completion
        # in one body completes inside the idle window, so falling back to
        # non-streaming after stream retries are exhausted typically
        # recovers without ever touching another provider.
        # Skip fallback only for `pre-handshake` (base_url wrong → non-stream
        # would fail identically) and HTTP-4xx (already raised above).
        if not state.launch_headers and state._phase in (
            "mid-stream-drop",
            "read-timeout",
            "transient-other",
        ):
            try:
                logger.warning(
                    "p5s2_stream_fallback_to_nonstream phase=%s base_url=%s",
                    state._phase,
                    self.base_url,
                )
                # Use the REAL non-stream path (_legacy_chat_with_tools_nonstream).
                # chat_with_tools is actually stream-as-transport internally
                # (see its docstring), so it would re-enter the same broken
                # SSE path. _legacy_chat_with_tools_nonstream issues a single
                # `stream: false` POST + JSON parse — which completes inside
                # the the relay proxy idle window and recovers cleanly.
                state.fallback_result = await self._legacy_chat_with_tools_nonstream(
                    state.messages,
                    tools=state.tools,
                    max_tokens=state.max_tokens,
                    temperature=state.temperature,
                    response_format=state.response_format,
                    tool_choice=state.tool_choice,
                )
                # Yield a synthetic terminal event so the streaming consumer
                # can treat this exactly like a completed SSE stream. No
                # delta events — the UI flips straight from "streaming" to
                # "done" but the final content is correct, which is better
                # than the alternative ("provider unavailable").
                yield {
                    "type": "final",
                    "content": state.fallback_result.get("content", ""),
                    "reasoning_content": state.fallback_result.get("reasoning_content", ""),
                    "tool_calls": state.fallback_result.get("tool_calls", []),
                    "stop_reason": state.fallback_result.get("stop_reason", "end_turn"),
                    "model": state.fallback_result.get("model", self.model),
                    "usage": state.fallback_result.get("usage", {}),
                    "fallback_used": "non_stream",
                }
                state.flow = "stop"
                return
            except Exception as _fb_exc:  # noqa: BLE001
                logger.warning(
                    "p5s2_stream_fallback_to_nonstream_failed err=%s",
                    str(_fb_exc)[:200],
                    exc_info=True,
                )

    async def _stream_at_most_once(self, state: _StreamToolsCallState):
        """派发交接禁止重试时只试一次，HTTP 错误归一化后抛出。"""

        if state.retry_mode == "at_most_once":
            try:
                async for state.event in self._stream_one_attempt_with_deadline(
                    state.payload,
                    transport_retries=0,
                    request_headers=state.launch_headers,
                ):
                    yield state.event
            except httpx.HTTPStatusError as exc:
                # The dispatch handoff deliberately disables retries, but an
                # HTTP response is still authoritative diagnostic evidence.
                # Normalize it before crossing the provider boundary so the
                # durable audit and UI keep the relay's actual error body
                # instead of the opaque ``400 Bad Request`` status line.
                state.body = ""
                try:
                    state.body = exc.response.text[:600]
                except Exception:  # noqa: BLE001 - 错误响应正文读不出来时不带
                    logger.debug("provider_error_body_read_failed", exc_info=True)
                raise LLMProviderError(
                    f"LLM HTTP {exc.response.status_code} "
                    f"{exc.response.reason_phrase}: {state.body[:300]}",
                    status_code=exc.response.status_code,
                    error_class=self._relay_error_class(
                        exc.response.status_code, state.body, exc.response
                    ),
                ) from exc
            state.flow = "stop"
            return

    async def _stream_with_transient_retries(self, state: _StreamToolsCallState):
        """按退避表逐次尝试流式请求；处理 400 改写重试与瞬时错误。"""

        for state.attempt, state.delay in enumerate(state.backoffs):
            if state.delay:
                await _asyncio.sleep(state.delay)
            try:
                if state.attempt > 0:
                    mark_current_attempt_transport_retry()
                async for state.ev in self._stream_one_attempt_with_deadline(
                    state.used_payload,
                    request_headers=state.launch_headers,
                ):
                    yield state.ev
                state.flow = "stop"
                return
            except httpx.HTTPStatusError as exc:
                state.body = ""
                try:
                    state.body = exc.response.text[:600]
                except Exception:  # noqa: BLE001 - 错误响应正文读不出来时不带
                    logger.debug("provider_error_body_read_failed", exc_info=True)
                # P4-S24 mirror in streaming: 400 reasoning_content →
                # strip stale assistant rows + retry once. Only retry
                # if we haven't already stripped (avoid infinite loop
                # if the retry also 400s for a different reason).
                state.stripped_already = state.used_messages is not state.messages
                if (
                    exc.response.status_code == 400
                    and "reasoning_content" in state.body
                    and not state.stripped_already
                    and any(
                        m.get("role") == "assistant" and not m.get("reasoning_content")
                        for m in state.used_messages
                    )
                ):
                    state.used_messages = [
                        m
                        for m in state.used_messages
                        if not (m.get("role") == "assistant" and not m.get("reasoning_content"))
                    ]
                    state.used_payload = {**state.payload, "messages": state.used_messages}
                    if state.used_messages and any(
                        m.get("role") == "system" for m in state.used_messages
                    ):
                        state.used_payload["messages"] = _stamp_cache_control(
                            state.used_messages, boundary_index=state._cache_boundary
                        )
                    logger.warning(
                        "p4s25_stream_reasoning_400_retry",
                        original=len(state.messages),
                        stripped=len(state.used_messages),
                    )
                    continue  # retry the loop with stripped messages
                if (
                    not state.tool_choice_stripped
                    and "tool_choice" in state.used_payload
                    and _is_tool_choice_400(exc, state.body)
                ):
                    state.tool_choice_stripped = True
                    state.used_payload = dict(state.used_payload)
                    state.used_payload.pop("tool_choice", None)
                    _record_tool_choice_none_unsupported(self.base_url, self.model)
                    logger.warning(
                        "openai_compat_stream_tool_choice_unsupported_retry",
                        model=self.model,
                        base_url=self.base_url,
                    )
                    continue
                # WI-R5: classify relay 402 / 401 into a structured
                # error_class so the chat layer can show
                # a friendly message + 充值 link / drive the key-rotation
                # retry, instead of bubbling a half-raw HTTP error.
                raise LLMProviderError(
                    f"LLM HTTP {exc.response.status_code} {exc.response.reason_phrase}: {state.body[:300]}",
                    status_code=exc.response.status_code,
                    error_class=self._relay_error_class(
                        exc.response.status_code, state.body, exc.response
                    ),
                ) from exc
            except state.transient as exc:
                state.last_exc = exc
                # P5-S2 A2: classify transient errors so callers (supervisor,
                # UI, user) see a real cause instead of a bare "ConnectError".
                # Phase tags map to actionable user advice:
                #   pre-handshake   : DNS / TCP / TLS handshake fail → base_url
                #                     wrong, upstream completely down, or DNS
                #                     blocked (e.g. .invalid TLD).
                #   mid-stream-drop : SSE was started, proxy closed the socket
                #                     mid-flight → upstream proxy idle timeout
                #                     or load-balancer reset on long responses.
                #   read-timeout    : socket open but no bytes arrived in time
                #                     → model generation too slow / upstream
                #                     queueing.
                #   transient-other : write/pool timeouts, rare.
                state._phase = (
                    "pre-handshake"
                    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError))
                    else "mid-stream-drop"
                    if isinstance(exc, httpx.RemoteProtocolError)
                    else "read-timeout"
                    if isinstance(exc, httpx.ReadTimeout)
                    else "transient-other"
                )
                state._msg = (str(exc) or type(exc).__name__).replace("\n", " ")[:300]
                logger.warning(
                    "p4s25_stream_transient_retry",
                    attempt=state.attempt + 1,
                    of=len(state.backoffs),
                    error_type=type(exc).__name__,
                    phase=state._phase,
                    error_msg=state._msg,
                    base_url=self.base_url,
                )
                continue

    async def _stream_one_attempt_with_deadline(
        self,
        payload: dict,
        *,
        transport_retries: int = 1,
        request_headers: dict[str, str] | None = None,
    ):
        """Stop only when the model stream makes no meaningful progress.

        ``httpx`` already limits the gap between transport bytes.  This
        second, sliding deadline operates on parsed provider events so relay
        heartbeat comments cannot keep an otherwise dead model invocation
        alive forever.  Every content, reasoning, tool-call, usage, or final
        event resets the deadline.
        """

        stream = self._stream_one_attempt(
            payload,
            transport_retries=transport_retries,
            request_headers=request_headers,
        )
        try:
            while True:
                try:
                    async with asyncio.timeout(self._stream_attempt_timeout_s):
                        event = await anext(stream)
                except StopAsyncIteration:
                    break
                yield event
        except TimeoutError as exc:
            raise httpx.ReadTimeout(
                f"SSE stream made no model progress within ({self._stream_attempt_timeout_s:.0f}s)"
            ) from exc
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()

    async def _stream_one_attempt(
        self,
        payload: dict,
        *,
        transport_retries: int = 1,
        request_headers: dict[str, str] | None = None,
    ):
        """Single-attempt SSE consumer. Used by chat_stream_with_tools."""
        # Context OS send boundary: this is the first synchronous statement
        # after the transport coroutine receives control and before any
        # network await/write.  Re-entry is an internal transport retry, not a
        # new logical provider attempt.
        state = _StreamAttemptState(
            payload=payload, transport_retries=transport_retries, request_headers=request_headers
        )
        mark_current_attempt_sent()

        # Accumulators for assembling the final event.
        state.full_content = ""
        state.full_reasoning = ""
        # Partial tool_calls indexed by `index` field. Each entry holds
        # {id, name, args_buf}; on stream end we json.loads(args_buf).
        state.tool_buffers: dict[int, dict] = {}
        state.final_stop = "end_turn"
        state.final_model = self.model
        state.final_usage: dict = {}
        state.tool_batch_overflow = False
        # P4-S25 diagnostic
        state.sse_lines = 0
        state.delta_keys_seen: set[str] = set()

        async with aclosing(self._open_and_consume_stream(state)) as _phase:
            async for _event in _phase:
                yield _event

        # P4-S25 fix-2: post-loop manual SSE parser.
        #
        # Root cause discovered 2026-05-09: httpx.aiter_lines() against
        # your-llm-relay.example.com sometimes returns the ENTIRE SSE body as a single
        # line — multiple `data: {chunk}\n\ndata: {chunk}\n\n...` events
        # smushed together without splitting on the inner newlines. So
        # the streaming-line loop above sees just one line, fails the
        # `data:` prefix check on the whole concat, and yields nothing.
        # When that happens, we fall back here: take the joined raw
        # body and manually split it into SSE events on `\n\n` block
        # boundaries (the SSE spec separator), then parse each event.
        state.empty_response = (
            state.sse_lines != -1
            and not state.full_content
            and not state.full_reasoning
            and not state.tool_buffers
        )
        async with aclosing(self._recover_empty_stream(state)) as _phase:
            async for _event in _phase:
                yield _event

        # P5-S2 Phase 1 — diagnostic dump for every accumulated tool_call
        # buffer. the relay / sealos sometimes truncate the SSE mid-frame
        # leaving us with half-baked args (e.g. `{"path": "fo`); without
        # this log we can't tell at debug-time whether the model emitted
        # broken JSON, the proxy chopped the stream, or our parser ate
        # fragments. Emit BEFORE assembly so the raw buf is unambiguous.
        # Pure observation — no behaviour change.
        for state._idx in sorted(state.tool_buffers.keys()):
            state._buf = state.tool_buffers[state._idx]
            state._args_str = state._buf.get("args_buf", "") or ""
            try:
                # Empty buf {} is a legal "no-arg call" — treat as parseable.
                _json.loads(state._args_str) if state._args_str else _json.loads("{}")
                state._parse_ok = True
            except _json.JSONDecodeError:
                state._parse_ok = False
            logger.info(
                "p5s2_tool_call_args_dump",
                idx=state._idx,
                name=state._buf.get("name", "") or "",
                args_len=len(state._args_str),
                args_preview=state._args_str[:100],
                parse_ok=state._parse_ok,
            )

        # Assemble tool_calls from buffers.
        #
        # P5-S2 (2026-05-11) fix: when the model emits malformed JSON for
        # tool_call.arguments (deepseek-v4-pro on long markdown content
        # frequently mis-escapes \n / " / \\), we used to silently swallow
        # the JSONDecodeError and pass `args = {}` to the dispatcher. The
        # tool then reports "missing required parameter" → LLM retries
        # with the same broken JSON → 3 strikes → circuit breaker OPEN →
        # user gets popup. The model never learns what went wrong.
        #
        # Now: stash the raw args_buf + parse error on the assembled
        # tool_call dict (keys ``_args_raw`` + ``_args_parse_error``).
        # Downstream (``_raw_to_response`` → ChatResponse → AgentLoop)
        # detects these and short-circuits dispatch with a structured
        # tool_result that tells the LLM exactly what's wrong, so it can
        # regenerate the call with valid JSON.
        state.assembled_tools: list[dict] = []
        self._finalize_tool_buffers(state)
        # If there were tool_calls, override stop_reason — some servers
        # don't set finish_reason="tool_calls" on the streaming end frame.
        if state.assembled_tools and state.final_stop == "end_turn":
            state.final_stop = "tool_use"

        logger.info(
            "p4s25_stream_summary",
            sse_lines=state.sse_lines,
            content_chars=len(state.full_content),
            reasoning_chars=len(state.full_reasoning),
            tool_calls=len(state.assembled_tools),
            delta_keys=sorted(state.delta_keys_seen),
            stop_reason=state.final_stop,
        )

        # Phase 1.3.4: prompt-cache 命中率埋点。回包带
        # prompt_tokens_details.cached_tokens（OpenAI / deepseek）→
        # 记命中率；the relay 现状常不回该字段 → 记 unknown，绝不崩。
        _log_cache_hit_rate(state.final_usage, where="stream")

        # 2026-05-17 deepseek-inline-cot-dsml-sanitize: sanitize the
        # *accumulated* content + recover inline DSML tool calls at the
        # single final-frame chokepoint (per-delta yields are UI-cosmetic
        # and intentionally left raw; correctness is this final value the
        # agent loop / write_file consume). reasoning_content untouched.
        state._content, state.assembled_tools, state._extracted = sanitize_response(
            state.full_content,
            state.assembled_tools,
            enabled=self.sanitize_inline_cot_dsml,
        )
        if state._extracted and state.final_stop != "tool_use":
            state.final_stop = "tool_use"

        yield {
            "type": "final",
            "content": state._content,
            "reasoning_content": state.full_reasoning,
            "tool_calls": state.assembled_tools,
            "stop_reason": state.final_stop,
            "model": state.final_model,
            "usage": state.final_usage,
        }

    async def _open_and_consume_stream(self, state: _StreamAttemptState):
        """打开流式请求并逐行消费响应。"""

        async with self._client(
            timeout=self.timeout,
            transport_retries=state.transport_retries,
        ) as state.client:
            async with state.client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=state.payload,
                headers=state.request_headers,
            ) as state.response:
                # Unlike MockTransport, a real streamed error response is not
                # buffered.  Read it while the response context is still
                # open so the outer HTTPStatusError path can retain its JSON
                # error code and must-not-fallback classification.
                if state.response.status_code >= 400:
                    await state.response.aread()
                state.response.raise_for_status()
                # P4-S25 fix: the relay / sealos / DashScope sometimes ignore
                # `stream: True` for thinking-mode models and return a
                # plain JSON body (Content-Type: application/json) holding
                # the complete response. aiter_lines() yields ONE line of
                # raw JSON which doesn't start with "data:" — we'd skip
                # it and end up with empty final content. Detect that
                # case via content-type and parse the body as a single
                # non-streaming response, then synthesize a final event
                # without yielding any deltas (we have no incremental
                # data anyway).
                state.content_type = (state.response.headers.get("content-type") or "").lower()
                async with aclosing(self._handle_non_sse_response(state)) as _phase:
                    async for _event in _phase:
                        yield _event
                # P4-S25 fix-2: capture every raw line so we can fall
                # back to "treat the whole body as JSON" if the SSE loop
                # produces nothing useful. Some the relay responses come
                # back with Content-Type: text/event-stream but the body
                # is a single JSON object (no `data:` prefix) — the
                # content-type check above won't catch that.
                # Capture EVERY line including empty ones so we can
                # accurately diagnose what the relay actually sent.
                state.raw_lines: list[str] = []
                async with aclosing(self._consume_stream_lines(state)) as _phase:
                    async for _event in _phase:
                        yield _event

    async def _handle_non_sse_response(self, state: _StreamAttemptState):
        """上游没按事件流返回：按普通 JSON 回复处理。"""

        if "text/event-stream" not in state.content_type:
            state.body_bytes = await state.response.aread()
            state.body_text = state.body_bytes.decode("utf-8", errors="replace")
            try:
                state.data = _json.loads(state.body_text)
            except _json.JSONDecodeError:
                logger.warning(
                    "p4s25_stream_unexpected_body",
                    content_type=state.content_type,
                    body_preview=state.body_text[:300],
                )
                # Fall through to the SSE-line loop with an
                # empty body — will emit an empty final.
                state.data = None
            if isinstance(state.data, dict):
                state.choices = state.data.get("choices") or []
                state.c0 = state.choices[0] if state.choices else {}
                state.msg = state.c0.get("message") or {}
                state.full_content = state.msg.get("content") or ""
                state.full_reasoning = (
                    state.msg.get("reasoning_content") or state.msg.get("reasoning") or ""
                )
                for state.j, state.tc in enumerate(state.msg.get("tool_calls") or []):
                    state.fn = state.tc.get("function") or {}
                    state.tool_buffers[state.j] = {
                        "id": state.tc.get("id", "") or "",
                        "name": state.fn.get("name", "") or "",
                        "args_buf": state.fn.get("arguments", "") or "",
                    }
                state.finish_reason = state.c0.get("finish_reason") or "stop"
                state.final_stop = (
                    "tool_use"
                    if state.finish_reason == "tool_calls"
                    else "end_turn"
                    if state.finish_reason == "stop"
                    else state.finish_reason
                )
                state.final_model = state.data.get("model") or state.final_model
                state.usage = state.data.get("usage") or {}
                if state.usage:
                    state.final_usage = state.usage
                    self.last_usage = state.usage
                # Emit one synthetic delta so callers that depend
                # on `delta_count > 0` (agent_loop's empty-stream
                # fallback) treat this as a real response.
                if state.full_content:
                    yield {"type": "delta", "content": state.full_content}
                if state.full_reasoning:
                    yield {"type": "delta_reasoning", "content": state.full_reasoning}
                logger.info(
                    "p4s25_nonstream_body_via_stream_endpoint",
                    content_type=state.content_type,
                    content_chars=len(state.full_content),
                    reasoning_chars=len(state.full_reasoning),
                    tool_calls=len(state.tool_buffers),
                )
                # Skip the SSE-line loop entirely — we already
                # have everything.
                state.sse_lines = -1  # marker for "non-stream body"

    async def _consume_stream_lines(self, state: _StreamAttemptState):
        """逐行读事件流，转发文本增量并累积工具调用片段。"""

        async for state.line in state.response.aiter_lines():
            if state.sse_lines == -1:
                # Non-stream body branch already consumed
                # response.aread(); aiter_lines is empty here
                # but defend against the iterator yielding
                # anything anyway.
                break
            state.sse_lines += 1
            state.raw_lines.append(state.line)  # capture even empty lines
            if not state.line or not state.line.startswith("data:"):
                continue
            state.data_str = state.line[len("data:") :].strip()
            if not state.data_str or state.data_str == "[DONE]":
                if state.data_str == "[DONE]":
                    break
                continue
            try:
                state.data = _json.loads(state.data_str)
            except _json.JSONDecodeError:
                continue
            state.final_model = state.data.get("model") or state.final_model
            state.usage = state.data.get("usage")
            if state.usage:
                state.final_usage = state.usage
                self.last_usage = state.usage
            state.choices = state.data.get("choices") or []
            if not state.choices:
                continue
            state.delta = state.choices[0].get("delta") or {}
            state.delta_keys_seen.update(state.delta.keys())
            # P4-S25 diagnostic: some non-conforming proxies put
            # the full message in `choices[0].message` (only at
            # the end) instead of streaming via `delta`. Capture
            # that as a fallback so we don't end up with empty
            # final_content. the relay seems to do this for some
            # thinking-mode models.
            state.msg = state.choices[0].get("message") or {}
            self._absorb_full_message_frame(state)
            state.finish_reason = state.choices[0].get("finish_reason")
            # Visible content delta.
            state.txt = state.delta.get("content")
            if state.txt:
                state.full_content += state.txt
                yield {"type": "delta", "content": state.txt}
            # Thinking-mode reasoning delta (DeepSeek V4 Pro etc.).
            state.rtxt = state.delta.get("reasoning_content") or state.delta.get("reasoning")
            if state.rtxt:
                state.full_reasoning += state.rtxt
                yield {"type": "delta_reasoning", "content": state.rtxt}
            self._accumulate_tool_call_deltas(state)
            if state.finish_reason:
                state.final_stop = (
                    "tool_use"
                    if state.finish_reason == "tool_calls"
                    else "end_turn"
                    if state.finish_reason == "stop"
                    else state.finish_reason
                )
            if state.tool_batch_overflow:
                state.final_stop = "tool_use"
                break

    def _absorb_full_message_frame(self, state: _StreamAttemptState) -> None:
        """非流式整包帧：把完整消息里的内容、思考与工具调用补进累积状态（只在还没收到对应增量时）。"""

        if state.msg.get("content") and not state.full_content:
            state.full_content = state.msg["content"]
        if state.msg.get("reasoning_content") and not state.full_reasoning:
            state.full_reasoning = state.msg["reasoning_content"]
        if state.msg.get("tool_calls") and not state.tool_buffers:
            for state.j, state.tc in enumerate(state.msg.get("tool_calls") or []):
                state.fn = state.tc.get("function") or {}
                state.tool_buffers[state.j] = {
                    "id": state.tc.get("id", "") or "",
                    "name": state.fn.get("name", "") or "",
                    "args_buf": state.fn.get("arguments", "") or "",
                }

    def _accumulate_tool_call_deltas(self, state: _StreamAttemptState) -> None:
        """把一帧里的工具调用增量累积进缓冲；超过重规划上限时标记溢出。"""

        # Tool_call deltas: name on first chunk, args dripped
        # across many. Buffer per-index until stream ends.
        for state.tcd in state.delta.get("tool_calls") or []:
            state.idx = state.tcd.get("index", 0) or 0
            state.is_new_tool_call = state.idx not in state.tool_buffers
            state.buf = state.tool_buffers.setdefault(
                state.idx, {"id": "", "name": "", "args_buf": ""}
            )
            if state.tcd.get("id"):
                state.buf["id"] = state.tcd["id"]
            state.fn = state.tcd.get("function") or {}
            if state.fn.get("name"):
                state.buf["name"] = state.fn["name"]
            if state.fn.get("arguments"):
                state.buf["args_buf"] += state.fn["arguments"]
            if (
                state.is_new_tool_call
                and len(state.tool_buffers) > _MAX_PROVIDER_TOOL_CALLS_BEFORE_REPLAN
            ):
                state.tool_batch_overflow = True
                logger.warning(
                    "provider_stream_tool_batch_overflow",
                    retained_tool_calls=len(state.tool_buffers),
                    replan_threshold=(_MAX_PROVIDER_TOOL_CALLS_BEFORE_REPLAN),
                    sse_lines=state.sse_lines,
                )
                break

    async def _recover_empty_stream(self, state: _StreamAttemptState):
        """流里一个增量都没有时，从原始事件里补救内容与工具调用。"""

        if state.empty_response:
            state.joined = "\n".join(state.raw_lines)
            # Always log raw evidence on empty-response so we can keep
            # diagnosing if this branch ALSO breaks.
            logger.warning(
                "p4s25_stream_empty_response_raw_dump",
                sse_lines=state.sse_lines,
                raw_lines_count=len(state.raw_lines),
                body_preview=state.joined[:400],
            )
            # 2026-05-09 the relay quirk: their thinking-mode endpoint
            # returns the ENTIRE SSE body as a single JSON-encoded
            # string — i.e. the bytes on the wire look like
            #     "data: {...}\n\ndata: {...}\n\n..."
            # (note the leading + trailing `"`, and `\n` is a 2-byte
            # backslash-n escape, NOT a real newline byte). aiter_lines
            # therefore yields ONE line and our split-on-newline parser
            # finds zero events. Detect and unwrap.
            if (
                state.joined.startswith('"')
                and state.joined.endswith('"')
                and "\\n" in state.joined
            ):
                try:
                    state.joined = _json.loads(state.joined)
                    logger.info(
                        "p4s25_stream_unwrapped_json_string_body",
                        unwrapped_chars=len(state.joined),
                    )
                except _json.JSONDecodeError:
                    pass  # leave joined as-is; manual split may still work
            # SSE event delimiter is a blank line.
            state.events = [ev.strip() for ev in state.joined.split("\n\n") if ev.strip()]
            state.recovered_chunks = 0
            self._parse_recovered_events(state)
            if state.recovered_chunks or state.tool_buffers:
                # Emit a single delta so callers waiting for stream
                # output (delta_count) treat this as a real response.
                if state.full_content:
                    yield {"type": "delta", "content": state.full_content}
                if state.full_reasoning:
                    yield {"type": "delta_reasoning", "content": state.full_reasoning}
                logger.info(
                    "p4s25_stream_postloop_sse_recovered",
                    events=len(state.events),
                    chunks=state.recovered_chunks,
                    content_chars=len(state.full_content),
                    reasoning_chars=len(state.full_reasoning),
                    tool_calls=len(state.tool_buffers),
                )
            else:
                logger.warning(
                    "p4s25_stream_empty_body_unparseable",
                    sse_lines=state.sse_lines,
                    events_count=len(state.events),
                    body_preview=state.joined[:300],
                )

    def _parse_recovered_events(self, state: _StreamAttemptState) -> None:
        """空流兜底：逐个 SSE 事件解析内容、思考、用量与工具调用。"""

        for state.event in state.events:
            # Each event may have multiple lines; collect data: ones.
            for state.ev_line in state.event.splitlines():
                if not state.ev_line.startswith("data:"):
                    continue
                state.payload_str = state.ev_line[len("data:") :].strip()
                if not state.payload_str or state.payload_str == "[DONE]":
                    continue
                try:
                    state.data = _json.loads(state.payload_str)
                except _json.JSONDecodeError:
                    continue
                state.final_model = state.data.get("model") or state.final_model
                state.usage = state.data.get("usage")
                if state.usage:
                    state.final_usage = state.usage
                    self.last_usage = state.usage
                state.choices = state.data.get("choices") or []
                if not state.choices:
                    continue
                state.c0 = state.choices[0]
                state.delta = state.c0.get("delta") or {}
                state.msg = state.c0.get("message") or {}
                state.txt = state.delta.get("content") or state.msg.get("content")
                if state.txt:
                    state.full_content += state.txt
                    state.recovered_chunks += 1
                state.rtxt = (
                    state.delta.get("reasoning_content")
                    or state.delta.get("reasoning")
                    or state.msg.get("reasoning_content")
                    or state.msg.get("reasoning")
                )
                if state.rtxt:
                    state.full_reasoning += state.rtxt
                    state.recovered_chunks += 1
                for state.tcd in state.delta.get("tool_calls") or state.msg.get("tool_calls") or []:
                    state.idx = state.tcd.get("index", 0) or 0
                    state.buf = state.tool_buffers.setdefault(
                        state.idx, {"id": "", "name": "", "args_buf": ""}
                    )
                    if state.tcd.get("id"):
                        state.buf["id"] = state.tcd["id"]
                    state.fn = state.tcd.get("function") or {}
                    if state.fn.get("name"):
                        state.buf["name"] = state.fn["name"]
                    if state.fn.get("arguments"):
                        state.buf["args_buf"] += state.fn["arguments"]
                state.fr = state.c0.get("finish_reason")
                if state.fr:
                    state.final_stop = (
                        "tool_use"
                        if state.fr == "tool_calls"
                        else "end_turn"
                        if state.fr == "stop"
                        else state.fr
                    )

    def _finalize_tool_buffers(self, state: _StreamAttemptState) -> None:
        """把累积的工具调用参数解析成工具调用（坏 JSON 尝试修复）。"""

        for state.idx in sorted(state.tool_buffers.keys()):
            state.buf = state.tool_buffers[state.idx]
            state.args_buf = state.buf["args_buf"] or ""
            state.args: dict = {}
            state.args_parse_error: str | None = None
            if state.args_buf:
                try:
                    state.parsed = _json.loads(state.args_buf)
                    state.args = state.parsed if isinstance(state.parsed, dict) else {}
                except _json.JSONDecodeError as _exc:
                    # P6 bugfix 2026-05-14 (live-test): thinking-mode LLMs
                    # writing React/JS code commonly escape single quotes
                    # as ``\'`` — invalid JSON (single quotes don't need
                    # escaping). The whole 6KB write_file tool_call then
                    # fails parse and the agent gets stuck retrying the
                    # same broken JSON until circuit-breaker opens. Try
                    # cheap repairs before giving up: any successful
                    # repair short-circuits to a valid args dict.
                    state.args = {}
                    state.args_parse_error = str(_exc)
                    state.repaired: dict | None = None
                    state.repair_label: str | None = None
                    # Common LLM escape mistakes (safe to apply because
                    # valid JSON never contains these sequences). Try
                    # repairs in escalating aggressiveness; first success
                    # short-circuits.
                    #
                    # Repair 1 (cheap, common): ``\'`` → ``'`` —
                    #   thinking-mode LLMs writing React/JS code routinely
                    #   pseudo-escape apostrophes.
                    state.repair_candidates: list[tuple[str, str]] = []
                    if "\\'" in state.args_buf:
                        state.repair_candidates.append(
                            (
                                "stripped_backslash_apostrophe",
                                state.args_buf.replace("\\'", "'"),
                            )
                        )
                    # Repair 2 (broad, regex): drop any invalid ``\X``
                    #   escape (X not in legal JSON escape set
                    #   ``"\/ bfnrtu``). This catches \', \;, \ , \., etc.
                    #   Crucially, leaves legal escapes (\\, \", \n, \t,
                    #   \uXXXX, …) intact because the regex's negated
                    #   class excludes the legal followers.

                    state._invalid_esc = _re.compile(r'\\([^"\\/bfnrtu])')
                    if state._invalid_esc.search(state.args_buf):
                        state.repair_candidates.append(
                            (
                                "stripped_invalid_escapes",
                                state._invalid_esc.sub(r"\1", state.args_buf),
                            )
                        )
                    # Repair 3 (last resort): permissive parse with
                    #   strict=False — accepts unescaped control chars
                    #   (tabs, newlines) inside strings. Doesn't fix
                    #   bad escapes but does handle raw \n in content.
                    state.repair_candidates.append(
                        (
                            "strict_false",
                            state.args_buf,  # decoder will use strict=False
                        )
                    )
                    for state._label, state._cand in state.repair_candidates:
                        try:
                            if state._label == "strict_false":
                                state._r = _json.JSONDecoder(strict=False).decode(state._cand)
                            else:
                                state._r = _json.loads(state._cand)
                            if isinstance(state._r, dict):
                                state.repaired = state._r
                                state.repair_label = state._label
                                break
                        except _json.JSONDecodeError:
                            continue
                    if state.repaired is not None:
                        state.args = state.repaired
                        state.args_parse_error = None
                        logger.info(
                            "p5s2_tool_call_args_repaired",
                            idx=state.idx,
                            name=state.buf.get("name", "") or "",
                            args_len=len(state.args_buf),
                            repair=state.repair_label,
                            orig_parse_error=str(_exc),
                        )
                    else:
                        # Dump the FULL args_buf (capped at 5KB to keep log
                        # readable) when repair also fails. This is the only
                        # way to diagnose new model-side JSON-escape bugs
                        # vs proxy-side truncation.
                        logger.warning(
                            "p5s2_tool_call_args_malformed",
                            idx=state.idx,
                            name=state.buf.get("name", "") or "",
                            args_len=len(state.args_buf),
                            args_full=state.args_buf[:5000],
                            parse_error=state.args_parse_error,
                        )
            state.tc_dict = {
                "id": state.buf["id"] or f"call_{state.idx}",
                "name": state.buf["name"],
                "arguments": state.args,
            }
            if state.args_parse_error:
                state.tc_dict["_args_raw"] = state.args_buf
                state.tc_dict["_args_parse_error"] = state.args_parse_error
            state.assembled_tools.append(state.tc_dict)
