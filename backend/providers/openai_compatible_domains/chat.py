# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Chat streams, tool calls and the non-streaming tool path."""

from __future__ import annotations

import asyncio as _asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import httpx
from agent.context_report import (
    auto_context_attempt_call,
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


class ChatMixin(OpenAICompatibleCore):
    """Chat streams, tool calls and the non-streaming tool path."""

    __slots__ = ()

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 8192,
    ) -> AsyncIterator[str]:
        async for token in auto_context_attempt_iter(
            provider=self,
            messages=messages,
            tools=None,
            model_id=self.model,
            mark_sent_at_dispatch=False,
            generation_reserve=max_tokens,
            iterator=lambda: self._chat_stream_impl(
                messages, temperature=temperature, max_tokens=max_tokens
            ),
        ):
            yield token

    async def _chat_stream_impl(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 8192,
    ) -> AsyncIterator[str]:
        temp = _temperature_for_model(
            self.model,
            temperature if temperature is not None else self.temperature,
        )
        messages, _ = split_wire_messages(messages)
        _attempt_options = current_provider_attempt_options()
        _cache_boundary = _attempt_options.cache_boundary if _attempt_options is not None else None
        # P2-1-S8: reset per-call so stale data from the previous stream
        # never leaks into billing when the current stream carries no usage.
        self.last_usage = None
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            # S8: ask OpenAI-compat servers to emit a terminal chunk with
            # a `usage` field so BillingLedger can record prompt/completion
            # tokens. Harmless on servers that ignore it (Ollama).
            "stream_options": {"include_usage": True},
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        payload = _merge_code_params(payload, self.code_params)
        if (
            _cache_boundary is not None
            and messages
            and any(m.get("role") == "system" for m in messages)
            and _is_anthropic_endpoint(self.base_url)
        ):
            payload["messages"] = _stamp_cache_control(messages, boundary_index=_cache_boundary)
        mark_current_attempt_sent()
        async with self._client(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
            ) as response:
                # Read structured error bodies before leaving the streaming
                # response context.  Otherwise ``response.text`` raises
                # ResponseNotRead in the outer HTTPStatusError handler and
                # request-authority codes such as tool_catalog_stale are lost.
                if response.status_code >= 400:
                    await response.aread()
                response.raise_for_status()
                # Log the upstream's self-reported identity on the first SSE
                # frame. The `model`/`id`/`system_fingerprint` come straight
                # from the server, so this is unforgeable proof of which
                # endpoint actually answered — invaluable when debugging
                # routing between local/cloud providers that use the same
                # wire protocol. Debug-level: off by default, opt-in via
                # DESKPET_LOG_LEVEL=DEBUG.
                _dumped_server_id = False
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:") :].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.warning("openai_compat_bad_sse_frame", raw=data_str)
                        continue
                    if not _dumped_server_id:
                        logger.debug(
                            "provider_response_identity",
                            url=f"{self.base_url}/chat/completions",
                            configured_model=self.model,
                            server_model=data.get("model"),
                            server_id=data.get("id"),
                            system_fingerprint=data.get("system_fingerprint"),
                        )
                        _dumped_server_id = True
                    # P2-1-S8: the usage chunk typically arrives as the
                    # terminal frame (choices=[], usage={...}). Capture it
                    # regardless of whether choices is empty.
                    usage = data.get("usage")
                    if usage:
                        self.last_usage = usage
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    token = delta.get("content")
                    if token:
                        yield token

    async def chat_with_tools(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
        tool_choice: str | None = None,
        launch_operation_id: str | None = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
    ) -> dict:
        coordinated = current_dispatch_handoff() is not None
        return await auto_context_attempt_call(
            provider=self,
            messages=messages,
            tools=tools,
            model_id=self.model,
            mark_sent_at_dispatch=False,
            generation_reserve=max_tokens,
            invoke=lambda: self._chat_with_tools_impl(
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                tool_choice=tool_choice,
                launch_operation_id=launch_operation_id,
                provider_launch_snapshot=provider_launch_snapshot,
                retry_mode="at_most_once" if coordinated else "resilient",
            ),
        )

    async def chat_with_tools_at_most_once(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
        tool_choice: str | None = None,
        launch_operation_id: str | None = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
    ) -> dict:
        """Aggregate one provider transport attempt without fallback retries.

        Durable opaque effects use this entrypoint because retrying after an
        ambiguous transport failure could duplicate a completed upstream LLM
        call.  The regular :meth:`chat_with_tools` path intentionally retains
        its resilient retry and stream-to-nonstream fallback behaviour.
        """

        return await auto_context_attempt_call(
            provider=self,
            messages=messages,
            tools=tools,
            model_id=self.model,
            mark_sent_at_dispatch=False,
            generation_reserve=max_tokens,
            invoke=lambda: self._chat_with_tools_impl(
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                tool_choice=tool_choice,
                retry_mode="at_most_once",
                launch_operation_id=launch_operation_id,
                provider_launch_snapshot=provider_launch_snapshot,
            ),
        )

    async def _chat_with_tools_impl(
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
    ) -> dict:
        """P4-S25 fix: HTTP-stream-as-transport, response-as-aggregate.

        Public contract: returns the same dict shape callers always saw —
            {content, reasoning_content, tool_calls, stop_reason, model, usage}

        Internal: delegates to :meth:`chat_stream_with_tools` so the HTTP
        request is ALWAYS ``stream: True``. We discard delta events and
        return only the final aggregate. This fixes the recurring the relay
        ``RemoteProtocolError("Server disconnected without sending a
        response")`` — proxies kill idle ``stream: False`` requests while
        thinking-mode models (deepseek-v4-pro etc.) are still reasoning
        for 30+ seconds, but happily forward SSE the moment tokens start
        flowing. So even callers that don't care about deltas (plan
        extractor, agent_loop's non-stream fallback, ad-hoc tools) now
        ride the streaming transport for reliability.

        Retry / reasoning_content-400 / response_format / cache_control
        handling is centralised inside ``chat_stream_with_tools`` — this
        wrapper intentionally has no error-handling logic of its own.
        """
        final_dict: dict | None = None
        async for ev in self.chat_stream_with_tools(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
            tool_choice=tool_choice,
            retry_mode=retry_mode,
            launch_operation_id=launch_operation_id,
            provider_launch_snapshot=provider_launch_snapshot,
        ):
            if ev.get("type") == "final":
                final_dict = ev
            # delta / delta_reasoning events are intentionally ignored —
            # callers of chat_with_tools want a single dict, not a stream.
        if final_dict is None:
            # chat_stream_with_tools always yields a final event on
            # success (and raises on failure). If we somehow got here
            # without one, surface a stable error.
            raise LLMProviderError("LLM 调用失败 (stream 未返回 final 事件)。")
        return {
            "content": final_dict.get("content") or "",
            "reasoning_content": final_dict.get("reasoning_content") or "",
            "tool_calls": final_dict.get("tool_calls") or [],
            "stop_reason": final_dict.get("stop_reason", "end_turn"),
            "model": final_dict.get("model", self.model),
            "usage": final_dict.get("usage") or {},
        }

    async def _legacy_chat_with_tools_nonstream(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
        tool_choice: str | None = None,
    ) -> dict:
        return await auto_context_attempt_call(
            provider=self,
            messages=messages,
            tools=tools,
            model_id=self.model,
            mark_sent_at_dispatch=False,
            generation_reserve=max_tokens,
            invoke=lambda: self._legacy_chat_with_tools_nonstream_impl(
                messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
                tool_choice=tool_choice,
            ),
        )

    async def _legacy_chat_with_tools_nonstream_impl(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = 8192,
        temperature: float | None = None,
        response_format: dict | None = None,
        tool_choice: str | None = None,
    ) -> dict:
        """DEPRECATED: original ``stream: False`` implementation.

        Preserved temporarily in case we need to A/B compare against
        the streaming-as-transport path. Not wired anywhere — callers
        go through :meth:`chat_with_tools` which now streams.

        The original docstring follows.

        P4-S20: non-streaming chat with optional tool_calls.

        Returns the parsed OpenAI choice payload:
            {
              "content": str,
              "tool_calls": [{id, name, arguments(dict)}],
              "stop_reason": "end_turn" | "tool_use" | "max_tokens" | "error",
              "model": str,
              "usage": {input_tokens, output_tokens, ...},
            }

        Tool_calls' ``arguments`` are pre-parsed from the JSON string the
        OpenAI protocol returns; agent loop can dispatch directly.

        ``response_format`` (P4-S25): OpenAI structured-output spec, e.g.
        ``{"type": "json_schema", "json_schema": {...}}`` or the simpler
        ``{"type": "json_object"}``. Forwarded verbatim. Endpoints that
        don't support it usually ignore the field; Ollama specifically
        wants ``format: "json"`` instead, so we shim that case below.
        """
        temp = _temperature_for_model(
            self.model,
            temperature if temperature is not None else self.temperature,
        )
        # Phase 1.3 (D4) 前缀稳定纪律 —— 与流式路径一致地抹平历史抖动
        # 字段。即便此方法当前只作流式 fallback 用，调用方传进来的
        # messages 已是流式路径 stabilize 过的副本；这里再 stabilize
        # 一次是幂等的，且保证此方法被直接调用时同样有前缀稳定保证。
        messages, _ = split_wire_messages(messages)
        _attempt_options = current_provider_attempt_options()
        _cache_boundary = _attempt_options.cache_boundary if _attempt_options is not None else None
        messages = _stabilize_prefix(messages)

        # 临时调试：打印实际发送的消息
        logger.info(f"[DEBUG_PERSONA] Sending to model={self.model}, base_url={self.base_url}")
        for idx, msg in enumerate(messages):
            role = msg.get("role", "?")
            content = msg.get("content", "")
            preview = content[:300] if isinstance(content, str) else str(content)[:300]
            logger.info(f"[DEBUG_PERSONA] Message {idx} ({role}): {preview}")

        payload = self._nonstream_payload(
            _cache_boundary,
            max_tokens=max_tokens,
            messages=messages,
            response_format=response_format,
            temp=temp,
            tool_choice=tool_choice,
            tools=tools,
        )
        # P4-S22 fix: wrap httpx errors so AgentLoop's existing
        # `except LLMProviderError` catches them and emits a clean
        # ErrorEvent instead of the bare ConnectError bubbling all the
        # way up to the chat handler (which surfaces as "chat_v2 错误:
        # unknown" in the UI because httpx.ConnectError on Windows
        # often has empty `str(exc)` when the remote sends RST mid
        # keep-alive). the relay specifically does this between turns.
        # P4-S24: thinking-mode round-trip requirement. Some endpoints
        # (DeepSeek V4 Pro / your-llm-relay.example.com proxy / Qwen3 thinking) reject
        # with HTTP 400 if a prior assistant message in `messages` is
        # missing its `reasoning_content`. This happens for rows
        # persisted before the round-trip fix landed (or any row where
        # storage dropped the field). On detection, strip the offending
        # rows and retry once. If the retry succeeds we move on; if it
        # fails again we surface the original error so the user sees
        # something stable.
        # P4-S24 transient-retry: your-llm-relay.example.com / sealos / various OpenAI-
        # compat proxies sometimes drop the connection mid-request
        # (httpx surfaces this as RemoteProtocolError "Server
        # disconnected without sending a response" — common right
        # before a thinking-mode model emits its reasoning, since the
        # proxy's idle timeout fires while the upstream is still
        # computing). Auto-retry twice with short backoff before
        # surfacing the error to the user — fixes ~90% of one-off
        # blips without making the UI feel stuck. 4xx are NOT retried
        # here (HTTPStatusError raised by raise_for_status doesn't get
        # caught in this loop) so reasoning_content 400s still flow to
        # the strip-and-retry handler below.

        async def _send(_msgs: list[dict]) -> dict:
            _wire_msgs = _msgs
            if (
                _wire_msgs
                and any(m.get("role") == "system" for m in _wire_msgs)
                and _is_anthropic_endpoint(self.base_url)
            ):
                _wire_msgs = _stamp_cache_control(_wire_msgs, boundary_index=_cache_boundary)
            transient = (
                httpx.RemoteProtocolError,
                httpx.ConnectTimeout,
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
            )
            last_exc: Exception | None = None
            # P4-S25: 3 attempts with growing backoff. RemoteProtocolError
            # / connection drops on the relay come in clusters — first 0.5s
            # backoff was too short. Failed attempts after the first
            # typically RST quickly so the worst-case wait stays bounded
            # (60s for the slow first attempt + a few seconds for the
            # quick fails on retries 2/3, NOT 3×60).
            # The durable ProviderInvocationCoordinator owns retry authority.
            # Once its handoff scope is active, a second POST could duplicate
            # a request whose first outcome is merely unknown.  Legacy /
            # auxiliary calls outside that scope retain their transient
            # retries.
            backoffs = (0.0,) if current_dispatch_handoff() is not None else (0.0, 1.0, 3.0)
            for attempt, delay in enumerate(backoffs):
                if delay:
                    await _asyncio.sleep(delay)
                try:
                    async with self._client(timeout=self.timeout) as _client:
                        if attempt == 0:
                            # Exact actual-send boundary: the transport
                            # coroutine has control and is about to perform its
                            # first network await/write.
                            mark_current_attempt_sent()
                        else:
                            mark_current_attempt_transport_retry()
                        _r = await _client.post(
                            f"{self.base_url}/chat/completions",
                            json={**payload, "messages": _wire_msgs},
                        )
                        _r.raise_for_status()
                        return _r.json()
                except httpx.HTTPStatusError:
                    # 4xx/5xx body present — propagate, retry happens at
                    # a different layer (reasoning_content 400 strip).
                    raise
                except transient as exc:
                    last_exc = exc
                    logger.warning(
                        "p4s24_transient_retry",
                        attempt=attempt + 1,
                        of=len(backoffs),
                        next_delay=backoffs[attempt + 1] if attempt + 1 < len(backoffs) else None,
                        error_type=type(exc).__name__,
                        error_msg=(str(exc) or type(exc).__name__)[:200],
                    )
                    continue
            # All retries exhausted.
            assert last_exc is not None
            raise last_exc

        try:
            data = await _send(messages)
        except httpx.HTTPStatusError as exc:
            body_snippet = ""
            try:
                body_snippet = exc.response.text[:600]
            except Exception:  # noqa: BLE001
                pass
            # 400 + the specific marker → retry once with assistant rows
            # missing reasoning_content stripped from history.
            should_retry = (
                exc.response.status_code == 400
                and "reasoning_content" in body_snippet
                and any(
                    m.get("role") == "assistant" and not m.get("reasoning_content")
                    for m in messages
                )
            )
            if should_retry:
                stripped = [
                    m
                    for m in messages
                    if not (m.get("role") == "assistant" and not m.get("reasoning_content"))
                ]
                logger.warning(
                    "p4s24_reasoning_400_retry",
                    original_history_size=len(messages),
                    stripped_history_size=len(stripped),
                    dropped=len(messages) - len(stripped),
                )
                try:
                    data = await _send(stripped)
                except httpx.HTTPStatusError as exc2:
                    body2 = ""
                    try:
                        body2 = exc2.response.text[:300]
                    except Exception:  # noqa: BLE001
                        pass
                    raise LLMProviderError(
                        f"LLM HTTP {exc2.response.status_code} {exc2.response.reason_phrase}: "
                        f"{body2}",
                        status_code=exc2.response.status_code,
                        error_class=self._relay_error_class(
                            exc2.response.status_code, body2, exc2.response
                        ),
                    ) from exc2
            elif "tool_choice" in payload and _is_tool_choice_400(exc, body_snippet):
                payload = dict(payload)
                payload.pop("tool_choice", None)
                _record_tool_choice_none_unsupported(self.base_url, self.model)
                logger.warning(
                    "openai_compat_tool_choice_unsupported_retry",
                    model=self.model,
                    base_url=self.base_url,
                )
                try:
                    data = await _send(messages)
                except httpx.HTTPStatusError as exc2:
                    body2 = ""
                    try:
                        body2 = exc2.response.text[:300]
                    except Exception:  # noqa: BLE001
                        pass
                    raise LLMProviderError(
                        f"LLM HTTP {exc2.response.status_code} {exc2.response.reason_phrase}: "
                        f"{body2}",
                        status_code=exc2.response.status_code,
                        error_class=self._relay_error_class(
                            exc2.response.status_code, body2, exc2.response
                        ),
                    ) from exc2
            else:
                # WI-R5: classify relay 402 (余额不足) / 401 (key 失效) into
                # a structured error_class so the chat layer can surface a
                # friendly message + 充值 link / drive the key-rotation
                # retry loop, instead of bubbling a half-raw HTTP error.
                raise LLMProviderError(
                    f"LLM HTTP {exc.response.status_code} {exc.response.reason_phrase}: "
                    f"{body_snippet[:300]}",
                    status_code=exc.response.status_code,
                    error_class=self._relay_error_class(
                        exc.response.status_code, body_snippet, exc.response
                    ),
                ) from exc
        except (
            httpx.ConnectTimeout,
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ) as exc:
            # Network-layer failure — typical when the relay keep-alive
            # socket gets reset between turns. P5-S2 A2: classify by
            # phase so supervisor / UI can show actionable hints.
            _human_type = type(exc).__name__
            _human_msg = (str(exc) or _human_type).replace("\n", " ")[:300]
            _phase = (
                "pre-handshake"
                if isinstance(exc, (httpx.ConnectTimeout, httpx.ConnectError))
                else "read-timeout"
                if isinstance(exc, httpx.ReadTimeout)
                else "transient-other"
            )
            _advice = {
                "pre-handshake": (
                    f"无法连接到 LLM endpoint {self.base_url}（DNS/TCP/TLS 握手失败）。"
                    "请检查 base_url 是否拼写正确（如 .invalid 是占位符），"
                    "或上游服务是否在线。"
                ),
                "read-timeout": (
                    f"等待 {self.base_url} 返回数据超时。模型生成可能过慢或上游排队。"
                ),
                "transient-other": (f"连接 {self.base_url} 时遇到 {_human_type}。"),
            }.get(_phase, f"连接 {self.base_url} 失败：{_human_type}")
            raise LLMProviderError(
                f"LLM 调用失败 [{_phase}]: {_advice} 原始错误: {_human_msg}"
            ) from exc
        except httpx.HTTPError as exc:
            # Catch-all for any other transport / parsing httpx error.
            human = str(exc) or type(exc).__name__
            raise LLMProviderError(f"LLM 调用失败 ({human})。") from exc
        return self._nonstream_result(data, messages)

    def _nonstream_payload(
        self,
        _cache_boundary,
        *,
        max_tokens: int,
        messages: list[dict],
        response_format: dict | None,
        temp,
        tool_choice: str | None,
        tools: list[dict] | None,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temp,
            "max_tokens": max_tokens,
        }
        payload = _merge_code_params(payload, self.code_params)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        # P4-S25: structured output. OpenAI / the relay / DashScope use
        # `response_format`; Ollama ignores that and reads `format`.
        # We pass the OpenAI-shape through and ALSO emit Ollama's
        # `format` so users can switch endpoints without code changes.
        if response_format is not None:
            payload["response_format"] = response_format
            rf_type = response_format.get("type") if isinstance(response_format, dict) else None
            if rf_type in ("json_object", "json_schema"):
                payload["format"] = "json"
        # P4-S25 A4: prompt caching — Anthropic-style cache_control
        # marker only fires for Anthropic-native endpoints. the relay and
        # some sealos proxies treat unknown message fields strictly
        # and respond with empty content; OpenAI gpt-4o auto-caches
        # without the marker. So gate on URL keyword.
        if (
            messages
            and any(m.get("role") == "system" for m in messages)
            and _is_anthropic_endpoint(self.base_url)
        ):
            payload["messages"] = _stamp_cache_control(messages, boundary_index=_cache_boundary)
        return payload

    def _nonstream_result(self, data, messages: list[dict]) -> dict:
        choices = data.get("choices") or []
        if not choices:
            return {
                "content": "",
                "reasoning_content": "",
                "tool_calls": [],
                "stop_reason": "error",
                "model": data.get("model", self.model),
                "usage": data.get("usage") or {},
            }
        c0 = choices[0]
        msg = c0.get("message") or {}
        finish = c0.get("finish_reason", "stop")
        # P4-S24: thinking-mode models (DeepSeek V4 Pro, Qwen3 thinking,
        # GLM-4.5, etc.) return a `reasoning_content` field alongside
        # `content`. Multi-turn chats MUST round-trip this field back to
        # the API or it rejects with HTTP 400 ("The reasoning_content
        # in the thinking mode must be passed back to the API"). Most
        # OpenAI-compat servers either omit the field or use the
        # canonical name; some non-conforming proxies use `reasoning`.
        reasoning_content = msg.get("reasoning_content") or msg.get("reasoning") or ""
        # Diagnostic: echo what we extracted + what's actually in the
        # outgoing request so multi-turn round-trip bugs are visible
        # in logs without needing a proxy / packet capture. INFO level
        # so it shows up in the default tauri-dev log.
        _has_assistant_history = any(m.get("role") == "assistant" for m in messages)
        logger.info(
            "p4s24_reasoning_extract",
            url=f"{self.base_url}/chat/completions",
            extracted_chars=len(reasoning_content),
            response_message_keys=list(msg.keys()),
            outgoing_history_size=len(messages),
            outgoing_has_assistant=_has_assistant_history,
            outgoing_assistant_has_reasoning=any(
                m.get("role") == "assistant" and m.get("reasoning_content") for m in messages
            ),
        )
        # OpenAI returns finish_reason="tool_calls" when tools were called.
        stop_reason = (
            "tool_use"
            if finish == "tool_calls" or msg.get("tool_calls")
            else ("end_turn" if finish == "stop" else finish)
        )
        tcs_raw = msg.get("tool_calls") or []
        tcs: list[dict] = []
        for tc in tcs_raw:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw or {})
            except (json.JSONDecodeError, TypeError):
                args = {}
            tcs.append(
                {
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "arguments": args,
                }
            )
        usage = data.get("usage") or {}
        # Stash so billing.ledger can debit (matches stream behavior).
        self.last_usage = usage
        # Phase 1.3.4: prompt-cache 命中率埋点（非流路径）。
        _log_cache_hit_rate(usage, where="nonstream")
        # 2026-05-17: strip inline CoT + extract inline DSML tool calls so
        # deepseek-v4-pro markup never leaks into write_file content.
        # Structured reasoning_content/tcs are left untouched.
        _content, tcs, _extracted = sanitize_response(
            msg.get("content") or "",
            tcs,
            enabled=self.sanitize_inline_cot_dsml,
        )
        if _extracted:
            stop_reason = "tool_use"
        return {
            "content": _content,
            "reasoning_content": reasoning_content,
            "tool_calls": tcs,
            "stop_reason": stop_reason,
            "model": data.get("model", self.model),
            "usage": usage,
        }
