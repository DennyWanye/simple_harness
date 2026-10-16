# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Provider context attempts: identity, begin, run and iterate."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from agent.context_report import (
    build_prepared_attempt_report,
    estimate_selected_attempt_budget,
    finish_current_attempt,
)
from deskpet.execution.provider_invocations import (
    coordinate_provider_call,
    coordinate_provider_stream,
)
from llm.context_messages import ProviderAttemptOptions, context_attempt_scope

from .core import AgentLoopCore
from .support import logger


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


class ContextAttemptMixin(AgentLoopCore):
    """Provider context attempts: identity, begin, run and iterate."""

    __slots__ = ()

    @staticmethod
    def _provider_attempt_identity(
        provider: Any, fallback_model: str = ""
    ) -> tuple[str, str, str, str]:
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

        provider_id, model_id, adapter_id, adapter_version = self._provider_attempt_identity(
            provider, fallback_model
        )
        initial_budget = getattr(prepared_context, "request_budget", None)
        actual_budget = estimate_selected_attempt_budget(
            provider=provider,
            model_id=model_id,
            messages=messages,
            tools=tools,
            generation_reserve=int(getattr(initial_budget, "generation_reserve", 0) or 0),
            attachment_tokens=int(getattr(prepared_context, "attachment_tokens", 0) or 0),
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
            cache_fingerprint=getattr(prepared_context, "stable_prefix_fingerprint", None),
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
    ) -> Any:
        options = await self._begin_context_attempt(
            provider=provider,
            session_id=session_id,
            request_id=request_id,
            attempt_id=attempt_id,
            purpose=purpose,
            messages=messages,
            tools=tools,
            fallback_model=fallback_model,
            prepared_context=prepared_context,
            current_tool_set=current_tool_set,
            compression=compression,
        )
        raw_invoke = invoke
        invoke = lambda: _coordinate_agent_provider_call(
            self,
            provider,
            request_id,
            attempt_id,
            purpose,
            messages,
            tools,
            fallback_model,
            raw_invoke,
        )
        if options is None:
            return await invoke()

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
    ) -> AsyncIterator[Any]:
        options = await self._begin_context_attempt(
            provider=provider,
            session_id=session_id,
            request_id=request_id,
            attempt_id=attempt_id,
            purpose=purpose,
            messages=messages,
            tools=tools,
            fallback_model=fallback_model,
            prepared_context=prepared_context,
            current_tool_set=current_tool_set,
            compression=compression,
        )
        raw_iterator = iterator
        iterator = lambda: _coordinate_agent_provider_stream(
            self,
            provider,
            request_id,
            attempt_id,
            purpose,
            messages,
            tools,
            fallback_model,
            raw_iterator,
        )
        if options is None:
            async for item in iterator():
                yield item
            return

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
