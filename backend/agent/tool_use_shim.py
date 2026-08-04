# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S20 Wave 2b — thin LLM shim for AgentLoop.

AgentLoop expects a ``chat_with_fallback(messages, tools=, ...)``
returning an ``llm.types.ChatResponse``. The deskpet runtime currently
uses ``OpenAICompatibleProvider`` (which does ``chat_stream`` for the
chat panel, plus the new ``chat_with_tools`` non-streaming method).

This shim wires the two together so we can drive the new tool-use loop
without spinning up the full ``LLMRegistry`` (which would require its
own anthropic/openai/gemini API keys).

P4-S25 A1: also exposes ``chat_with_fallback_stream`` for the streaming
path. Same return shape (ChatResponse on completion) but yields
intermediate delta events the agent loop can forward to the WS so the
user sees text/tool calls trickle in instead of waiting silently for
30+ seconds on thinking-mode models.

Production wiring:
    shim = OpenAICompatibleAgentLLM(provider=cloud_or_local_provider)
    loop = AgentLoop(llm_registry=shim, tool_registry=registry_v2)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Sequence

if TYPE_CHECKING:
    from deskpet.execution.contracts import ProviderLaunchSnapshot

from llm.errors import LLMProviderError
from deskpet.execution.dispatch import current_dispatch_handoff

from llm.types import ChatResponse, ChatUsage, ToolCall


def _launch_kwargs(
    operation_id: str | None, snapshot: ProviderLaunchSnapshot | None
) -> dict[str, Any]:
    if (operation_id is None) != (snapshot is None):
        raise ValueError("launch operation id and snapshot must be provided together")
    return {} if operation_id is None else {
        "launch_operation_id": operation_id,
        "provider_launch_snapshot": snapshot,
    }


class OpenAICompatibleAgentLLM:
    """Adapter: ``OpenAICompatibleProvider`` → AgentLoop LLM protocol."""

    def __init__(self, provider) -> None:  # type: ignore[no-untyped-def]
        self._provider = provider

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        launch_operation_id: str | None = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        # ``model`` is ignored — provider already locked to a model at
        # construction time. (The agent loop passes ``model=None`` by
        # default, and the upstream chat handler can swap providers
        # rather than re-binding model on the fly.)
        max_tokens = int(kwargs.get("max_tokens", 2048))
        temperature = kwargs.get("temperature")
        # P4-S25: structured output pass-through (response_format), so
        # callers like the plan-mode phase can demand JSON schema.
        response_format = kwargs.get("response_format")
        method = (
            self._provider.chat_with_tools_at_most_once
            if current_dispatch_handoff() is not None
            else self._provider.chat_with_tools
        )
        raw = await method(
            messages, tools=tools, max_tokens=max_tokens, temperature=temperature,
            response_format=response_format,
            **_launch_kwargs(launch_operation_id, provider_launch_snapshot),
        )
        return _raw_to_response(raw)

    async def chat_with_fallback_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        launch_operation_id: str | None = None,
        provider_launch_snapshot: ProviderLaunchSnapshot | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        """Stream one provider, retrying only before the first event."""
        import asyncio as _aio

        max_tokens = int(kwargs.get("max_tokens", 2048))
        temperature = kwargs.get("temperature")
        response_format = kwargs.get("response_format")
        launch = _launch_kwargs(launch_operation_id, provider_launch_snapshot)
        max_retries = (
            1
            if current_dispatch_handoff() is not None
            else
            3
            if provider_launch_snapshot is None
            or provider_launch_snapshot.supports_idempotent_launch
            else 1
        )
        for attempt in range(1, max_retries + 1):
            yielded_any = False
            try:
                iterator = self._provider.chat_stream_with_tools(
                    messages, tools=tools, max_tokens=max_tokens,
                    temperature=temperature, response_format=response_format,
                    **launch,
                )
                async for event in iterator:
                    yielded_any = True
                    yield event
                return
            except Exception as exc:  # noqa: BLE001
                name = type(exc).__name__
                transient = (
                    "Timeout" in name
                    or name
                    in {
                        "ReadError",
                        "ConnectError",
                        "RemoteProtocolError",
                        "ProtocolError",
                        "ConnectionError",
                        "APIConnectionError",
                        "APITimeoutError",
                        "WriteError",
                        "PoolTimeout",
                    }
                    or isinstance(exc, (TimeoutError, ConnectionError))
                )
                if yielded_any or not transient or attempt >= max_retries:
                    raise
                await _aio.sleep(0.5 * (2 ** (attempt - 1)))


class OpenAICompatibleAgentLLMChain:
    """Adapter for an ordered chain of OpenAI-compatible providers.

    Durable workflows call the registry-shaped ``chat_with_fallback`` API.
    Walking the complete resolved chain here keeps their failover behavior in
    line with AgentLoop instead of silently pinning them to the first entry.
    """

    def __init__(self, providers: Sequence[Any]) -> None:
        self._providers = list(providers)
        if not self._providers:
            raise ValueError("provider chain must not be empty")
        self._active_provider = self._providers[0]
        self._provider = self._active_provider

    @property
    def name(self) -> str:
        return str(
            getattr(self._active_provider, "provider_id", "")
            or getattr(self._active_provider, "name", "")
            or "openai_compatible"
        )

    @property
    def model(self) -> str:
        return str(getattr(self._active_provider, "model", "") or "")

    async def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        errors: list[str] = []
        providers = (
            self._providers[:1]
            if current_dispatch_handoff() is not None
            else self._providers
        )
        for provider in providers:
            try:
                response = await OpenAICompatibleAgentLLM(provider).chat_with_fallback(
                    messages, tools=tools, model=model, **kwargs,
                )
            except LLMProviderError as exc:
                if kwargs.get("launch_operation_id") is not None:
                    raise
                errors.append(
                    f"{getattr(provider, 'provider_id', '') or getattr(provider, 'name', 'openai_compatible')}: {exc}"
                )
                continue
            self._active_provider = provider
            self._provider = provider
            return response
        raise LLMProviderError(
            "all providers failed: " + "; ".join(errors),
            provider="provider_chain",
        )

    async def chat_with_fallback_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        async for event in OpenAICompatibleAgentLLM(
            self._provider
        ).chat_with_fallback_stream(messages, tools=tools, model=model, **kwargs):
            yield event


def _raw_to_response(raw: dict) -> ChatResponse:
    """Translate provider's chat_with_tools dict into a ChatResponse."""
    usage = raw.get("usage") or {}
    return ChatResponse(
        content=raw.get("content", "") or "",
        reasoning_content=raw.get("reasoning_content", "") or "",
        tool_calls=[
            ToolCall(
                id=tc.get("id", "") or "",
                name=tc.get("name", "") or "",
                arguments=tc.get("arguments", {}) or {},
                # P5-S2: forward malformed-args metadata if present so
                # AgentLoop can short-circuit dispatch with a useful
                # error message back to the model.
                args_parse_error=tc.get("_args_parse_error"),
                args_raw=tc.get("_args_raw"),
            )
            for tc in (raw.get("tool_calls") or [])
        ],
        stop_reason=raw.get("stop_reason", "end_turn"),
        model=raw.get("model", ""),
        usage=ChatUsage(
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cache_read_tokens=int(
                (usage.get("prompt_tokens_details") or {}).get(
                    "cached_tokens"
                )
                or 0
            ),
            cache_write_tokens=0,
        ),
    )
