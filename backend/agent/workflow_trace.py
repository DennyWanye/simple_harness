# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Optional structured tracing adapter for the legacy AgentLoop harness."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Iterator

from deskpet.workflows.trace import (
    SpanContext,
    SpanKind,
    SpanStatus,
    TraceStore,
    current_span,
    use_span,
)


class TraceInstrumentationError(RuntimeError):
    """A core trace write failed and the harness must stop this run."""


_ACTIVE_HARNESS_TRACE: ContextVar["HarnessTraceSession | None"] = ContextVar(
    "deskpet_active_harness_trace", default=None
)


def active_harness_trace() -> "HarnessTraceSession | None":
    return _ACTIVE_HARNESS_TRACE.get()


@dataclass(slots=True)
class HarnessTraceSession:
    store: TraceStore
    trace_id: str
    run_id: str
    root_span_id: str
    root_context: SpanContext
    owns_run: bool
    _closed: bool = False

    @classmethod
    async def start(
        cls,
        store: TraceStore,
        *,
        session_id: str,
        task_id: str,
        parent_context: SpanContext | None = None,
        request_id: str | None = None,
        turn_id: str | None = None,
        message_count: int = 0,
    ) -> "HarnessTraceSession":
        parent = parent_context or current_span()
        owns_run = parent is None
        trace_id = parent.trace_id if parent is not None else ""
        run_id = (parent.run_id or task_id) if parent is not None else task_id

        try:
            if owns_run:
                trace_run = await store.start_run(
                    session_id=session_id,
                    kind=SpanKind.CHAT,
                    run_id=run_id,
                    request_id=request_id,
                    turn_id=turn_id,
                )
                trace_id = trace_run.trace_id
            root = await store.start_span(
                trace_id=trace_id,
                name="agent_loop.run",
                kind=SpanKind.CHAT,
                parent_span_id=parent.span_id if parent is not None else None,
                run_id=run_id,
                lifecycle_stage="agent_loop",
                attributes={
                    "harness": "react",
                    "task_id": task_id,
                    "message_count": message_count,
                },
            )
        except Exception as exc:  # noqa: BLE001 - converted to a stable harness error
            if owns_run and trace_id:
                try:
                    await store.finish_run(trace_id, SpanStatus.ERROR, error=exc)
                except Exception:  # noqa: BLE001 - original trace failure is authoritative
                    pass
            raise TraceInstrumentationError(str(exc)) from exc

        return cls(
            store=store,
            trace_id=trace_id,
            run_id=run_id,
            root_span_id=root.span_id,
            root_context=store.context(root),
            owns_run=owns_run,
        )

    @contextmanager
    def activate(self) -> Iterator["HarnessTraceSession"]:
        token = _ACTIVE_HARNESS_TRACE.set(self)
        try:
            with use_span(self.root_context):
                yield self
        finally:
            _ACTIVE_HARNESS_TRACE.reset(token)

    async def call(
        self,
        *,
        name: str,
        kind: str,
        lifecycle_stage: str,
        invoke: Callable[[], Any],
        attributes: dict[str, Any] | None = None,
        result_is_error: Callable[[Any], bool] | None = None,
    ) -> Any:
        span = await self._start_child(
            name=name,
            kind=kind,
            lifecycle_stage=lifecycle_stage,
            attributes=attributes,
        )
        try:
            with use_span(self.store.context(span)):
                result = invoke()
                if inspect.isawaitable(result):
                    result = await result
        except asyncio.CancelledError as exc:
            await self._finish_child(span.span_id, SpanStatus.CANCELLED, error=exc)
            raise
        except BaseException as exc:
            await self._finish_child(span.span_id, SpanStatus.ERROR, error=exc)
            raise
        else:
            failed = bool(result_is_error and result_is_error(result))
            await self._finish_child(
                span.span_id,
                SpanStatus.ERROR if failed else SpanStatus.OK,
                output_ref=_result_summary(result),
                error={"reason": "domain_error"} if failed else None,
            )
            return result

    async def iterate(
        self,
        *,
        name: str,
        kind: str,
        lifecycle_stage: str,
        iterator: Callable[[], AsyncIterator[Any]],
        attributes: dict[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        span = await self._start_child(
            name=name,
            kind=kind,
            lifecycle_stage=lifecycle_stage,
            attributes=attributes,
        )
        status = SpanStatus.OK
        error: BaseException | None = None
        item_count = 0
        try:
            with use_span(self.store.context(span)):
                async for item in iterator():
                    item_count += 1
                    yield item
        except asyncio.CancelledError as exc:
            status = SpanStatus.CANCELLED
            error = exc
            raise
        except BaseException as exc:
            status = SpanStatus.ERROR
            error = exc
            raise
        finally:
            await self._finish_child(
                span.span_id,
                status,
                output_ref=f"stream_events:{item_count}" if error is None else None,
                error=error,
            )

    async def close(self, status: str, *, error: Any = None) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.store.finish_span(self.root_span_id, status, error=error)
            if self.owns_run:
                await self.store.finish_run(self.trace_id, status, error=error)
        except Exception as exc:  # noqa: BLE001 - caller decides how to surface close failures
            raise TraceInstrumentationError(str(exc)) from exc

    async def _start_child(
        self,
        *,
        name: str,
        kind: str,
        lifecycle_stage: str,
        attributes: dict[str, Any] | None,
    ):
        try:
            return await self.store.start_span(
                trace_id=self.trace_id,
                name=name,
                kind=kind,
                parent_span_id=self.root_span_id,
                run_id=self.run_id,
                lifecycle_stage=lifecycle_stage,
                attributes=attributes,
            )
        except Exception as exc:  # noqa: BLE001
            raise TraceInstrumentationError(str(exc)) from exc

    async def _finish_child(
        self,
        span_id: str,
        status: str,
        *,
        output_ref: str | None = None,
        error: Any = None,
    ) -> None:
        try:
            await self.store.finish_span(
                span_id, status, output_ref=output_ref, error=error
            )
        except Exception as exc:  # noqa: BLE001
            raise TraceInstrumentationError(str(exc)) from exc


async def traced_call(
    *,
    name: str,
    kind: str,
    lifecycle_stage: str,
    invoke: Callable[[], Any],
    attributes: dict[str, Any] | None = None,
    result_is_error: Callable[[Any], bool] | None = None,
) -> Any:
    trace = active_harness_trace()
    if trace is None:
        result = invoke()
        return await result if inspect.isawaitable(result) else result
    return await trace.call(
        name=name,
        kind=kind,
        lifecycle_stage=lifecycle_stage,
        invoke=invoke,
        attributes=attributes,
        result_is_error=result_is_error,
    )


async def traced_iterate(
    *,
    name: str,
    kind: str,
    lifecycle_stage: str,
    iterator: Callable[[], AsyncIterator[Any]],
    attributes: dict[str, Any] | None = None,
) -> AsyncIterator[Any]:
    trace = active_harness_trace()
    if trace is None:
        async for item in iterator():
            yield item
        return
    async for item in trace.iterate(
        name=name,
        kind=kind,
        lifecycle_stage=lifecycle_stage,
        iterator=iterator,
        attributes=attributes,
    ):
        yield item


def _result_summary(result: Any) -> str:
    stop_reason = getattr(result, "stop_reason", None)
    if stop_reason is not None:
        tool_calls = getattr(result, "tool_calls", ()) or ()
        return f"chat_response:{stop_reason}:tool_calls={len(tool_calls)}"
    if isinstance(result, tuple) and result and isinstance(result[0], bool):
        return "gate:allowed" if result[0] else "gate:blocked"
    if isinstance(result, list):
        return f"list:count={len(result)}"
    if isinstance(result, str):
        try:
            payload = json.loads(result)
        except ValueError:
            return "text_result"
        if isinstance(payload, dict) and isinstance(payload.get("ok"), bool):
            return f"tool_result:ok={str(payload['ok']).lower()}"
        return "json_result"
    if isinstance(result, bool):
        return "gate:allowed" if result else "gate:blocked"
    return type(result).__name__
