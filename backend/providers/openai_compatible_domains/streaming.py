# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Streaming tool calls and single stream attempts."""

from __future__ import annotations

import asyncio
import asyncio as _asyncio
import json as _json
import re as _re
from typing import TYPE_CHECKING

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

        launch_headers, launch_retry_mode = self._launch_request_parts(
            launch_operation_id,
            provider_launch_snapshot,
        )
        if launch_retry_mode == "at_most_once":
            retry_mode = "at_most_once"
        temp = _temperature_for_model(
            self.model,
            temperature if temperature is not None else self.temperature,
        )
        self.last_usage = None
        # Phase 1.3 (D4): 前缀稳定纪律。在装 payload 之前先抹平历史里
        # 会抖动的字段（tool-call-only assistant 的 content / 空
        # reasoning_content），让出站字节前缀跨轮稳定 —— prefix cache
        # 命中的硬前提。下游所有分支（reasoning-400 strip 重试 /
        # _stamp_cache_control / 非流 fallback）都建立在这个稳定副本上。
        messages, _ = split_wire_messages(messages)
        _attempt_options = current_provider_attempt_options()
        _cache_boundary = _attempt_options.cache_boundary if _attempt_options is not None else None
        messages = _stabilize_prefix(messages)

        # 临时调试：打印实际发送的消息
        logger.info(
            f"[DEBUG_PERSONA] STREAM Sending to model={self.model}, base_url={self.base_url}"
        )
        for idx, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            preview = content[:300] if isinstance(content, str) else str(content)[:300]
            logger.info(f"[DEBUG_PERSONA] STREAM Message {idx} ({role}): {preview}")

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        payload = _merge_code_params(payload, self.code_params)
        logger.info(
            "openai_compat_outbound fn=chat_stream_with_tools model=%s "
            "reasoning_effort=%s extra_body=%s",
            payload.get("model"),
            payload.get("reasoning_effort"),
            payload.get("extra_body"),
        )
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format is not None:
            payload["response_format"] = response_format
            rf_type = response_format.get("type") if isinstance(response_format, dict) else None
            if rf_type in ("json_object", "json_schema"):
                payload["format"] = "json"
        if (
            messages
            and any(m.get("role") == "system" for m in messages)
            and _is_anthropic_endpoint(self.base_url)
        ):
            payload["messages"] = _stamp_cache_control(messages, boundary_index=_cache_boundary)

        if retry_mode == "at_most_once":
            try:
                async for event in self._stream_one_attempt_with_deadline(
                    payload,
                    transport_retries=0,
                    request_headers=launch_headers,
                ):
                    yield event
            except httpx.HTTPStatusError as exc:
                # The dispatch handoff deliberately disables retries, but an
                # HTTP response is still authoritative diagnostic evidence.
                # Normalize it before crossing the provider boundary so the
                # durable audit and UI keep the relay's actual error body
                # instead of the opaque ``400 Bad Request`` status line.
                body = ""
                try:
                    body = exc.response.text[:600]
                except Exception:  # noqa: BLE001
                    pass
                raise LLMProviderError(
                    f"LLM HTTP {exc.response.status_code} "
                    f"{exc.response.reason_phrase}: {body[:300]}",
                    status_code=exc.response.status_code,
                    error_class=self._relay_error_class(
                        exc.response.status_code, body, exc.response
                    ),
                ) from exc
            return

        # Streaming retry layer: same transient-error policy as the
        # non-streaming path. Bumped to 2 retries since SSE is more
        # exposed to mid-stream disconnects.
        transient = (
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
        backoffs = (0.0,) if current_dispatch_handoff() is not None else (0.0, 1.0, 3.0)
        last_exc: Exception | None = None
        # Used messages — may swap to a stripped version if we hit the
        # reasoning_content 400 mid-stream (rare, but old sessions still
        # have NULL reasoning_content rows).
        used_messages = messages
        used_payload = payload
        tool_choice_stripped = False
        for attempt, delay in enumerate(backoffs):
            if delay:
                await _asyncio.sleep(delay)
            try:
                if attempt > 0:
                    mark_current_attempt_transport_retry()
                async for ev in self._stream_one_attempt_with_deadline(
                    used_payload,
                    request_headers=launch_headers,
                ):
                    yield ev
                return
            except httpx.HTTPStatusError as exc:
                body = ""
                try:
                    body = exc.response.text[:600]
                except Exception:  # noqa: BLE001
                    pass
                # P4-S24 mirror in streaming: 400 reasoning_content →
                # strip stale assistant rows + retry once. Only retry
                # if we haven't already stripped (avoid infinite loop
                # if the retry also 400s for a different reason).
                stripped_already = used_messages is not messages
                if (
                    exc.response.status_code == 400
                    and "reasoning_content" in body
                    and not stripped_already
                    and any(
                        m.get("role") == "assistant" and not m.get("reasoning_content")
                        for m in used_messages
                    )
                ):
                    used_messages = [
                        m
                        for m in used_messages
                        if not (m.get("role") == "assistant" and not m.get("reasoning_content"))
                    ]
                    used_payload = {**payload, "messages": used_messages}
                    if used_messages and any(m.get("role") == "system" for m in used_messages):
                        used_payload["messages"] = _stamp_cache_control(
                            used_messages, boundary_index=_cache_boundary
                        )
                    logger.warning(
                        "p4s25_stream_reasoning_400_retry",
                        original=len(messages),
                        stripped=len(used_messages),
                    )
                    continue  # retry the loop with stripped messages
                if (
                    not tool_choice_stripped
                    and "tool_choice" in used_payload
                    and _is_tool_choice_400(exc, body)
                ):
                    tool_choice_stripped = True
                    used_payload = dict(used_payload)
                    used_payload.pop("tool_choice", None)
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
                    f"LLM HTTP {exc.response.status_code} {exc.response.reason_phrase}: {body[:300]}",
                    status_code=exc.response.status_code,
                    error_class=self._relay_error_class(
                        exc.response.status_code, body, exc.response
                    ),
                ) from exc
            except transient as exc:
                last_exc = exc
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
                _phase = (
                    "pre-handshake"
                    if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError))
                    else "mid-stream-drop"
                    if isinstance(exc, httpx.RemoteProtocolError)
                    else "read-timeout"
                    if isinstance(exc, httpx.ReadTimeout)
                    else "transient-other"
                )
                _msg = (str(exc) or type(exc).__name__).replace("\n", " ")[:300]
                logger.warning(
                    "p4s25_stream_transient_retry",
                    attempt=attempt + 1,
                    of=len(backoffs),
                    error_type=type(exc).__name__,
                    phase=_phase,
                    error_msg=_msg,
                    base_url=self.base_url,
                )
                continue
        assert last_exc is not None
        # P5-S2 A2: include actionable diagnosis in the user-facing error.
        # The base "LLM 调用失败 (ConnectError)" was opaque — users couldn't
        # tell apart "base_url wrong" from "upstream proxy dropped me".
        _human_type = type(last_exc).__name__
        _human_msg = (str(last_exc) or _human_type).replace("\n", " ")[:300]
        _phase = (
            "pre-handshake"
            if isinstance(last_exc, (httpx.ConnectTimeout, httpx.ConnectError))
            else "mid-stream-drop"
            if isinstance(last_exc, httpx.RemoteProtocolError)
            else "read-timeout"
            if isinstance(last_exc, httpx.ReadTimeout)
            else "transient-other"
        )
        # P5-S2 D1: stream → non-stream same-provider fallback.
        # Relay-style OpenAI-compatible proxies idle-timeout long SSE streams (>~30s) and reset
        # the connection. A single POST that returns the whole completion
        # in one body completes inside the idle window, so falling back to
        # non-streaming after stream retries are exhausted typically
        # recovers without ever touching another provider.
        # Skip fallback only for `pre-handshake` (base_url wrong → non-stream
        # would fail identically) and HTTP-4xx (already raised above).
        if not launch_headers and _phase in ("mid-stream-drop", "read-timeout", "transient-other"):
            try:
                logger.warning(
                    "p5s2_stream_fallback_to_nonstream phase=%s base_url=%s",
                    _phase,
                    self.base_url,
                )
                # Use the REAL non-stream path (_legacy_chat_with_tools_nonstream).
                # chat_with_tools is actually stream-as-transport internally
                # (see its docstring), so it would re-enter the same broken
                # SSE path. _legacy_chat_with_tools_nonstream issues a single
                # `stream: false` POST + JSON parse — which completes inside
                # the the relay proxy idle window and recovers cleanly.
                fallback_result = await self._legacy_chat_with_tools_nonstream(
                    messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                    tool_choice=tool_choice,
                )
                # Yield a synthetic terminal event so the streaming consumer
                # can treat this exactly like a completed SSE stream. No
                # delta events — the UI flips straight from "streaming" to
                # "done" but the final content is correct, which is better
                # than the alternative ("provider unavailable").
                yield {
                    "type": "final",
                    "content": fallback_result.get("content", ""),
                    "reasoning_content": fallback_result.get("reasoning_content", ""),
                    "tool_calls": fallback_result.get("tool_calls", []),
                    "stop_reason": fallback_result.get("stop_reason", "end_turn"),
                    "model": fallback_result.get("model", self.model),
                    "usage": fallback_result.get("usage", {}),
                    "fallback_used": "non_stream",
                }
                return
            except Exception as _fb_exc:  # noqa: BLE001
                logger.warning(
                    "p5s2_stream_fallback_to_nonstream_failed err=%s",
                    str(_fb_exc)[:200],
                )
                # Continue to raise the original stream error with diagnosis.
        _advice = {
            "pre-handshake": (
                f"无法连接到 LLM endpoint {self.base_url}（DNS/TCP/TLS 握手失败）。"
                "可能原因：base_url 拼写错（如 .invalid 占位符）、上游服务 down、"
                "或本机网络/防火墙阻塞。请在 Settings → LLM Providers 检查 base_url 并"
                "可考虑添加备用 provider。"
            ),
            "mid-stream-drop": (
                f"流式响应被 {self.base_url} 中途切断（连续 {len(backoffs)} 次）。"
                "通常是中转站负载均衡 idle-timeout 或长响应触发 proxy reset。"
                "降低 max_tokens、缩短 conversation history、或添加备用 provider 可缓解。"
            ),
            "read-timeout": (
                f"等待 {self.base_url} 返回数据超时（连续 {len(backoffs)} 次）。"
                "模型生成可能过慢或上游排队。可降级到更快的模型或增加 timeout。"
            ),
            "transient-other": (f"连接 {self.base_url} 时遇到 {_human_type}。"),
        }.get(_phase, f"连接 {self.base_url} 失败：{_human_type}")
        raise LLMProviderError(
            f"LLM 调用失败 [{_phase}]: {_advice} 原始错误: {_human_msg}"
        ) from last_exc

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
        mark_current_attempt_sent()

        # Accumulators for assembling the final event.
        full_content = ""
        full_reasoning = ""
        # Partial tool_calls indexed by `index` field. Each entry holds
        # {id, name, args_buf}; on stream end we json.loads(args_buf).
        tool_buffers: dict[int, dict] = {}
        final_stop = "end_turn"
        final_model = self.model
        final_usage: dict = {}
        tool_batch_overflow = False
        # P4-S25 diagnostic
        sse_lines = 0
        delta_keys_seen: set[str] = set()

        async with self._client(
            timeout=self.timeout,
            transport_retries=transport_retries,
        ) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=request_headers,
            ) as response:
                # Unlike MockTransport, a real streamed error response is not
                # buffered.  Read it while the response context is still
                # open so the outer HTTPStatusError path can retain its JSON
                # error code and must-not-fallback classification.
                if response.status_code >= 400:
                    await response.aread()
                response.raise_for_status()
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
                content_type = (response.headers.get("content-type") or "").lower()
                if "text/event-stream" not in content_type:
                    body_bytes = await response.aread()
                    body_text = body_bytes.decode("utf-8", errors="replace")
                    try:
                        data = _json.loads(body_text)
                    except _json.JSONDecodeError:
                        logger.warning(
                            "p4s25_stream_unexpected_body",
                            content_type=content_type,
                            body_preview=body_text[:300],
                        )
                        # Fall through to the SSE-line loop with an
                        # empty body — will emit an empty final.
                        data = None
                    if isinstance(data, dict):
                        choices = data.get("choices") or []
                        c0 = choices[0] if choices else {}
                        msg = c0.get("message") or {}
                        full_content = msg.get("content") or ""
                        full_reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
                        for j, tc in enumerate(msg.get("tool_calls") or []):
                            fn = tc.get("function") or {}
                            tool_buffers[j] = {
                                "id": tc.get("id", "") or "",
                                "name": fn.get("name", "") or "",
                                "args_buf": fn.get("arguments", "") or "",
                            }
                        finish_reason = c0.get("finish_reason") or "stop"
                        final_stop = (
                            "tool_use"
                            if finish_reason == "tool_calls"
                            else "end_turn"
                            if finish_reason == "stop"
                            else finish_reason
                        )
                        final_model = data.get("model") or final_model
                        usage = data.get("usage") or {}
                        if usage:
                            final_usage = usage
                            self.last_usage = usage
                        # Emit one synthetic delta so callers that depend
                        # on `delta_count > 0` (agent_loop's empty-stream
                        # fallback) treat this as a real response.
                        if full_content:
                            yield {"type": "delta", "content": full_content}
                        if full_reasoning:
                            yield {"type": "delta_reasoning", "content": full_reasoning}
                        logger.info(
                            "p4s25_nonstream_body_via_stream_endpoint",
                            content_type=content_type,
                            content_chars=len(full_content),
                            reasoning_chars=len(full_reasoning),
                            tool_calls=len(tool_buffers),
                        )
                        # Skip the SSE-line loop entirely — we already
                        # have everything.
                        sse_lines = -1  # marker for "non-stream body"
                # P4-S25 fix-2: capture every raw line so we can fall
                # back to "treat the whole body as JSON" if the SSE loop
                # produces nothing useful. Some the relay responses come
                # back with Content-Type: text/event-stream but the body
                # is a single JSON object (no `data:` prefix) — the
                # content-type check above won't catch that.
                # Capture EVERY line including empty ones so we can
                # accurately diagnose what the relay actually sent.
                raw_lines: list[str] = []
                async for line in response.aiter_lines():
                    if sse_lines == -1:
                        # Non-stream body branch already consumed
                        # response.aread(); aiter_lines is empty here
                        # but defend against the iterator yielding
                        # anything anyway.
                        break
                    sse_lines += 1
                    raw_lines.append(line)  # capture even empty lines
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if not data_str or data_str == "[DONE]":
                        if data_str == "[DONE]":
                            break
                        continue
                    try:
                        data = _json.loads(data_str)
                    except _json.JSONDecodeError:
                        continue
                    final_model = data.get("model") or final_model
                    usage = data.get("usage")
                    if usage:
                        final_usage = usage
                        self.last_usage = usage
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    delta_keys_seen.update(delta.keys())
                    # P4-S25 diagnostic: some non-conforming proxies put
                    # the full message in `choices[0].message` (only at
                    # the end) instead of streaming via `delta`. Capture
                    # that as a fallback so we don't end up with empty
                    # final_content. the relay seems to do this for some
                    # thinking-mode models.
                    msg = choices[0].get("message") or {}
                    if msg.get("content") and not full_content:
                        full_content = msg["content"]
                    if msg.get("reasoning_content") and not full_reasoning:
                        full_reasoning = msg["reasoning_content"]
                    if msg.get("tool_calls") and not tool_buffers:
                        for j, tc in enumerate(msg.get("tool_calls") or []):
                            fn = tc.get("function") or {}
                            tool_buffers[j] = {
                                "id": tc.get("id", "") or "",
                                "name": fn.get("name", "") or "",
                                "args_buf": fn.get("arguments", "") or "",
                            }
                    finish_reason = choices[0].get("finish_reason")
                    # Visible content delta.
                    txt = delta.get("content")
                    if txt:
                        full_content += txt
                        yield {"type": "delta", "content": txt}
                    # Thinking-mode reasoning delta (DeepSeek V4 Pro etc.).
                    rtxt = delta.get("reasoning_content") or delta.get("reasoning")
                    if rtxt:
                        full_reasoning += rtxt
                        yield {"type": "delta_reasoning", "content": rtxt}
                    # Tool_call deltas: name on first chunk, args dripped
                    # across many. Buffer per-index until stream ends.
                    for tcd in delta.get("tool_calls") or []:
                        idx = tcd.get("index", 0) or 0
                        is_new_tool_call = idx not in tool_buffers
                        buf = tool_buffers.setdefault(idx, {"id": "", "name": "", "args_buf": ""})
                        if tcd.get("id"):
                            buf["id"] = tcd["id"]
                        fn = tcd.get("function") or {}
                        if fn.get("name"):
                            buf["name"] = fn["name"]
                        if fn.get("arguments"):
                            buf["args_buf"] += fn["arguments"]
                        if (
                            is_new_tool_call
                            and len(tool_buffers) > _MAX_PROVIDER_TOOL_CALLS_BEFORE_REPLAN
                        ):
                            tool_batch_overflow = True
                            logger.warning(
                                "provider_stream_tool_batch_overflow",
                                retained_tool_calls=len(tool_buffers),
                                replan_threshold=(_MAX_PROVIDER_TOOL_CALLS_BEFORE_REPLAN),
                                sse_lines=sse_lines,
                            )
                            break
                    if finish_reason:
                        final_stop = (
                            "tool_use"
                            if finish_reason == "tool_calls"
                            else "end_turn"
                            if finish_reason == "stop"
                            else finish_reason
                        )
                    if tool_batch_overflow:
                        final_stop = "tool_use"
                        break

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
        empty_response = (
            sse_lines != -1 and not full_content and not full_reasoning and not tool_buffers
        )
        if empty_response:
            joined = "\n".join(raw_lines)
            # Always log raw evidence on empty-response so we can keep
            # diagnosing if this branch ALSO breaks.
            logger.warning(
                "p4s25_stream_empty_response_raw_dump",
                sse_lines=sse_lines,
                raw_lines_count=len(raw_lines),
                body_preview=joined[:400],
            )
            # 2026-05-09 the relay quirk: their thinking-mode endpoint
            # returns the ENTIRE SSE body as a single JSON-encoded
            # string — i.e. the bytes on the wire look like
            #     "data: {...}\n\ndata: {...}\n\n..."
            # (note the leading + trailing `"`, and `\n` is a 2-byte
            # backslash-n escape, NOT a real newline byte). aiter_lines
            # therefore yields ONE line and our split-on-newline parser
            # finds zero events. Detect and unwrap.
            if joined.startswith('"') and joined.endswith('"') and "\\n" in joined:
                try:
                    joined = _json.loads(joined)
                    logger.info(
                        "p4s25_stream_unwrapped_json_string_body",
                        unwrapped_chars=len(joined),
                    )
                except _json.JSONDecodeError:
                    pass  # leave joined as-is; manual split may still work
            # SSE event delimiter is a blank line.
            events = [ev.strip() for ev in joined.split("\n\n") if ev.strip()]
            recovered_chunks = 0
            for event in events:
                # Each event may have multiple lines; collect data: ones.
                for ev_line in event.splitlines():
                    if not ev_line.startswith("data:"):
                        continue
                    payload_str = ev_line[len("data:") :].strip()
                    if not payload_str or payload_str == "[DONE]":
                        continue
                    try:
                        data = _json.loads(payload_str)
                    except _json.JSONDecodeError:
                        continue
                    final_model = data.get("model") or final_model
                    usage = data.get("usage")
                    if usage:
                        final_usage = usage
                        self.last_usage = usage
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    c0 = choices[0]
                    delta = c0.get("delta") or {}
                    msg = c0.get("message") or {}
                    txt = delta.get("content") or msg.get("content")
                    if txt:
                        full_content += txt
                        recovered_chunks += 1
                    rtxt = (
                        delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or msg.get("reasoning_content")
                        or msg.get("reasoning")
                    )
                    if rtxt:
                        full_reasoning += rtxt
                        recovered_chunks += 1
                    for tcd in delta.get("tool_calls") or msg.get("tool_calls") or []:
                        idx = tcd.get("index", 0) or 0
                        buf = tool_buffers.setdefault(idx, {"id": "", "name": "", "args_buf": ""})
                        if tcd.get("id"):
                            buf["id"] = tcd["id"]
                        fn = tcd.get("function") or {}
                        if fn.get("name"):
                            buf["name"] = fn["name"]
                        if fn.get("arguments"):
                            buf["args_buf"] += fn["arguments"]
                    fr = c0.get("finish_reason")
                    if fr:
                        final_stop = (
                            "tool_use" if fr == "tool_calls" else "end_turn" if fr == "stop" else fr
                        )
            if recovered_chunks or tool_buffers:
                # Emit a single delta so callers waiting for stream
                # output (delta_count) treat this as a real response.
                if full_content:
                    yield {"type": "delta", "content": full_content}
                if full_reasoning:
                    yield {"type": "delta_reasoning", "content": full_reasoning}
                logger.info(
                    "p4s25_stream_postloop_sse_recovered",
                    events=len(events),
                    chunks=recovered_chunks,
                    content_chars=len(full_content),
                    reasoning_chars=len(full_reasoning),
                    tool_calls=len(tool_buffers),
                )
            else:
                logger.warning(
                    "p4s25_stream_empty_body_unparseable",
                    sse_lines=sse_lines,
                    events_count=len(events),
                    body_preview=joined[:300],
                )

        # P5-S2 Phase 1 — diagnostic dump for every accumulated tool_call
        # buffer. the relay / sealos sometimes truncate the SSE mid-frame
        # leaving us with half-baked args (e.g. `{"path": "fo`); without
        # this log we can't tell at debug-time whether the model emitted
        # broken JSON, the proxy chopped the stream, or our parser ate
        # fragments. Emit BEFORE assembly so the raw buf is unambiguous.
        # Pure observation — no behaviour change.
        for _idx in sorted(tool_buffers.keys()):
            _buf = tool_buffers[_idx]
            _args_str = _buf.get("args_buf", "") or ""
            try:
                # Empty buf {} is a legal "no-arg call" — treat as parseable.
                _json.loads(_args_str) if _args_str else _json.loads("{}")
                _parse_ok = True
            except _json.JSONDecodeError:
                _parse_ok = False
            logger.info(
                "p5s2_tool_call_args_dump",
                idx=_idx,
                name=_buf.get("name", "") or "",
                args_len=len(_args_str),
                args_preview=_args_str[:100],
                parse_ok=_parse_ok,
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
        assembled_tools: list[dict] = []
        for idx in sorted(tool_buffers.keys()):
            buf = tool_buffers[idx]
            args_buf = buf["args_buf"] or ""
            args: dict = {}
            args_parse_error: str | None = None
            if args_buf:
                try:
                    parsed = _json.loads(args_buf)
                    args = parsed if isinstance(parsed, dict) else {}
                except _json.JSONDecodeError as _exc:
                    # P6 bugfix 2026-05-14 (live-test): thinking-mode LLMs
                    # writing React/JS code commonly escape single quotes
                    # as ``\'`` — invalid JSON (single quotes don't need
                    # escaping). The whole 6KB write_file tool_call then
                    # fails parse and the agent gets stuck retrying the
                    # same broken JSON until circuit-breaker opens. Try
                    # cheap repairs before giving up: any successful
                    # repair short-circuits to a valid args dict.
                    args = {}
                    args_parse_error = str(_exc)
                    repaired: dict | None = None
                    repair_label: str | None = None
                    # Common LLM escape mistakes (safe to apply because
                    # valid JSON never contains these sequences). Try
                    # repairs in escalating aggressiveness; first success
                    # short-circuits.
                    #
                    # Repair 1 (cheap, common): ``\'`` → ``'`` —
                    #   thinking-mode LLMs writing React/JS code routinely
                    #   pseudo-escape apostrophes.
                    repair_candidates: list[tuple[str, str]] = []
                    if "\\'" in args_buf:
                        repair_candidates.append(
                            (
                                "stripped_backslash_apostrophe",
                                args_buf.replace("\\'", "'"),
                            )
                        )
                    # Repair 2 (broad, regex): drop any invalid ``\X``
                    #   escape (X not in legal JSON escape set
                    #   ``"\/ bfnrtu``). This catches \', \;, \ , \., etc.
                    #   Crucially, leaves legal escapes (\\, \", \n, \t,
                    #   \uXXXX, …) intact because the regex's negated
                    #   class excludes the legal followers.

                    _invalid_esc = _re.compile(r'\\([^"\\/bfnrtu])')
                    if _invalid_esc.search(args_buf):
                        repair_candidates.append(
                            (
                                "stripped_invalid_escapes",
                                _invalid_esc.sub(r"\1", args_buf),
                            )
                        )
                    # Repair 3 (last resort): permissive parse with
                    #   strict=False — accepts unescaped control chars
                    #   (tabs, newlines) inside strings. Doesn't fix
                    #   bad escapes but does handle raw \n in content.
                    repair_candidates.append(
                        (
                            "strict_false",
                            args_buf,  # decoder will use strict=False
                        )
                    )
                    for _label, _cand in repair_candidates:
                        try:
                            if _label == "strict_false":
                                _r = _json.JSONDecoder(strict=False).decode(_cand)
                            else:
                                _r = _json.loads(_cand)
                            if isinstance(_r, dict):
                                repaired = _r
                                repair_label = _label
                                break
                        except _json.JSONDecodeError:
                            continue
                    if repaired is not None:
                        args = repaired
                        args_parse_error = None
                        logger.info(
                            "p5s2_tool_call_args_repaired",
                            idx=idx,
                            name=buf.get("name", "") or "",
                            args_len=len(args_buf),
                            repair=repair_label,
                            orig_parse_error=str(_exc),
                        )
                    else:
                        # Dump the FULL args_buf (capped at 5KB to keep log
                        # readable) when repair also fails. This is the only
                        # way to diagnose new model-side JSON-escape bugs
                        # vs proxy-side truncation.
                        logger.warning(
                            "p5s2_tool_call_args_malformed",
                            idx=idx,
                            name=buf.get("name", "") or "",
                            args_len=len(args_buf),
                            args_full=args_buf[:5000],
                            parse_error=args_parse_error,
                        )
            tc_dict = {
                "id": buf["id"] or f"call_{idx}",
                "name": buf["name"],
                "arguments": args,
            }
            if args_parse_error:
                tc_dict["_args_raw"] = args_buf
                tc_dict["_args_parse_error"] = args_parse_error
            assembled_tools.append(tc_dict)
        # If there were tool_calls, override stop_reason — some servers
        # don't set finish_reason="tool_calls" on the streaming end frame.
        if assembled_tools and final_stop == "end_turn":
            final_stop = "tool_use"

        logger.info(
            "p4s25_stream_summary",
            sse_lines=sse_lines,
            content_chars=len(full_content),
            reasoning_chars=len(full_reasoning),
            tool_calls=len(assembled_tools),
            delta_keys=sorted(delta_keys_seen),
            stop_reason=final_stop,
        )

        # Phase 1.3.4: prompt-cache 命中率埋点。回包带
        # prompt_tokens_details.cached_tokens（OpenAI / deepseek）→
        # 记命中率；the relay 现状常不回该字段 → 记 unknown，绝不崩。
        _log_cache_hit_rate(final_usage, where="stream")

        # 2026-05-17 deepseek-inline-cot-dsml-sanitize: sanitize the
        # *accumulated* content + recover inline DSML tool calls at the
        # single final-frame chokepoint (per-delta yields are UI-cosmetic
        # and intentionally left raw; correctness is this final value the
        # agent loop / write_file consume). reasoning_content untouched.
        _content, assembled_tools, _extracted = sanitize_response(
            full_content,
            assembled_tools,
            enabled=self.sanitize_inline_cot_dsml,
        )
        if _extracted and final_stop != "tool_use":
            final_stop = "tool_use"

        yield {
            "type": "final",
            "content": _content,
            "reasoning_content": full_reasoning,
            "tool_calls": assembled_tools,
            "stop_reason": final_stop,
            "model": final_model,
            "usage": final_usage,
        }
