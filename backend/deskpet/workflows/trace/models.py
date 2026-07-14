from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SpanKind(StrEnum):
    WORKFLOW = "workflow"
    NODE = "node"
    LLM = "llm"
    TOOL = "tool"
    GATE = "gate"
    EVALUATOR = "evaluator"
    DELIVERY = "delivery"
    CHAT = "chat"
    VOICE = "voice"


class SpanStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class TraceRun:
    trace_id: str
    session_id: str
    kind: str
    status: str
    started_at: float
    run_id: str | None = None
    request_id: str | None = None
    turn_id: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None


@dataclass(frozen=True, slots=True)
class TraceSpan:
    span_id: str
    trace_id: str
    name: str
    kind: str
    status: str
    started_at: float
    parent_span_id: str | None = None
    run_id: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    node_id: str | None = None
    lifecycle_stage: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    input_ref: str | None = None
    privacy_class: str = "internal"
