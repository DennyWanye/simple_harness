"""Transparent child-span instrumentation for workflow operation ports."""

from __future__ import annotations

import inspect
import copy
from collections.abc import Mapping
from dataclasses import is_dataclass, replace
from typing import Any

from .models import SpanKind, SpanStatus
from .store import TraceStore


_KINDS = {
    "llm": SpanKind.LLM,
    "evaluator": SpanKind.EVALUATOR,
    "tool": SpanKind.TOOL,
    "effect": SpanKind.TOOL,
    "search": SpanKind.TOOL,
    "fetch": SpanKind.TOOL,
}


class TracedPort:
    def __init__(self, target: object, *, name: str, store: TraceStore, row: Mapping[str, Any]) -> None:
        self._target = target
        self._name = name
        self._store = store
        self._row = row

    async def _call(self, method: str, callable_obj, *args, **kwargs):
        span = await self._store.start_span(
            trace_id=str(self._row["trace_id"]),
            run_id=str(self._row["run_id"]),
            workflow_name=str(self._row["workflow_name"]),
            workflow_version=str(self._row["workflow_version"]),
            name=f"{self._name}.{method}",
            kind=_KINDS[self._name],
            lifecycle_stage=self._name,
            attributes={"method": method},
        )
        try:
            result = callable_obj(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except BaseException as exc:
            await self._store.finish_span(span.span_id, SpanStatus.ERROR, error=exc)
            raise
        await self._store.finish_span(span.span_id, SpanStatus.OK)
        return result

    async def __call__(self, *args, **kwargs):
        return await self._call("call", self._target, *args, **kwargs)

    def __getattr__(self, method: str):
        value = getattr(self._target, method)
        if not callable(value):
            return value

        async def traced(*args, **kwargs):
            return await self._call(method, value, *args, **kwargs)

        return traced


def instrument_ports(
    ports: Mapping[str, object], store: TraceStore, row: Mapping[str, Any]
) -> dict[str, object]:
    instrumented: dict[str, object] = {}
    for name, port in ports.items():
        if name not in _KINDS:
            instrumented[name] = port
            continue
        tracer = TracedPort(port, name=name, store=store, row=row)

        def traced_callable(method: str, callable_obj):
            async def call(*args, **kwargs):
                return await tracer._call(method, callable_obj, *args, **kwargs)

            return call

        if is_dataclass(port) and name == "llm" and hasattr(port, "complete"):
            changes = {"complete": traced_callable("complete", getattr(port, "complete"))}
            for field_name in ("rerank", "semantic_score"):
                value = getattr(port, field_name, None)
                if callable(value):
                    changes[field_name] = traced_callable(field_name, value)
            instrumented[name] = replace(port, **changes)
        elif is_dataclass(port) and name == "search" and hasattr(port, "search_call"):
            changes = {"search_call": traced_callable("search", getattr(port, "search_call"))}
            for field_name in ("direct_call", "reset_runtime"):
                value = getattr(port, field_name, None)
                if callable(value):
                    changes[field_name] = traced_callable(field_name, value)
            instrumented[name] = replace(port, **changes)
        elif is_dataclass(port) and name == "fetch" and hasattr(port, "extractor"):
            clone = copy.copy(port)
            extractor = getattr(port, "extractor", None)
            if callable(extractor):
                clone.extractor = traced_callable("extract", extractor)
            instrumented[name] = clone
        else:
            instrumented[name] = tracer
    return instrumented


__all__ = ["TracedPort", "instrument_ports"]
