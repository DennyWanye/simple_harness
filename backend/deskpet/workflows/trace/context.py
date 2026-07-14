from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True, slots=True)
class SpanContext:
    trace_id: str
    span_id: str
    run_id: str | None = None


_CURRENT_SPAN: ContextVar[SpanContext | None] = ContextVar("deskpet_workflow_span", default=None)


def current_span() -> SpanContext | None:
    return _CURRENT_SPAN.get()


@contextmanager
def use_span(context: SpanContext) -> Iterator[SpanContext]:
    token = _CURRENT_SPAN.set(context)
    try:
        yield context
    finally:
        _CURRENT_SPAN.reset(token)
