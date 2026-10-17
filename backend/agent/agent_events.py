# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Agent run event types consumed by the product presenter and SDK delivery adapters.

The legacy backend ``AgentLoop`` that used to emit these was removed in
code-quality phase 8 (the SDK ReAct driver owns the loop); the event shapes stay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from llm.types import ToolCall


@dataclass
class AgentEvent:
    """Base event emitted by the agent loop.

    All fields default so subclasses can freely add non-default fields
    without hitting python's "non-default follows default" check. Each
    subclass overrides `type` in __post_init__ via a class-level default.
    """

    type: str = ""
    task_id: str = ""
    iteration: int = 0


@dataclass
class FinalEvent(AgentEvent):
    content: str = ""
    # passed through so `_run_chat` can persist it on the final
    # assistant turn (DB row's reasoning_content column).
    reasoning_content: str = ""
    stop_reason: str = "end_turn"
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "final"


@dataclass
class ErrorEvent(AgentEvent):
    reason: str = ""
    detail: str = ""
    # WI-R5: structured relay error code propagated from
    # LLMProviderError.error_class — "insufficient_balance" /
    # "relay_key_invalid". "" for non-relay / unclassified failures.
    # The frontend renders a friendly message + 充值 hint for it.
    error_class: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "error"


@dataclass
class PipelineEvent(AgentEvent):
    """七步问题处理流水线观测事件（plans/2026-06-24-...）。

    type ∈ {chat_v2_intent, chat_v2_contradiction, chat_v2_evidence_gate,
    chat_v2_selfcheck, chat_v2_convergence}。仅 pipeline_observability=True 时 yield；
    main.py 据 .type 直接 send_json。flag off 时**永不构造**（零开销，BC）。"""

    payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # type 由构造方显式给（多种事件复用本类），不在此覆盖。
        pass


@dataclass
class AssistantMessageEvent(AgentEvent):
    content: str = ""
    # chain-of-thought from thinking-mode models. Persisted to
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


@dataclass
class ContextCompactedEvent(AgentEvent):
    """WI-1B-2 压缩可观测 — flag ``features.ctx_observability`` ON 时,上下文
    压缩命中后发出一条轻量事件。main.py 转成 ws
    ``{type: "context_compacted", reduction, tokens_in, tokens_out, model}``,
    前端在圈圈 gauge 附近浮一条 toast「已压缩,省 N token」(N = tokens_in -
    tokens_out)。flag OFF 时本事件**永不构造**(零开销,字节级 BC)。"""

    reduction: float = 0.0  # 节省比 0~1（middle 段 reduction_ratio）
    tokens_in: int = 0  # 压缩前 middle 段 token
    tokens_out: int = 0  # 压缩后摘要 token
    model: str = ""  # 摘要所用模型（可选）
    source_event_id: str = ""  # durable producer identity
    based_on_sample_id: str = ""  # exact measured sample compacted
    occurred_at: float = 0.0  # producer time for observability; not lineage authority

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "context_compacted"


class DispatchOutcome(str, Enum):
    CONTINUE = "continue"
    ASYNC_HANDOFF = "async_handoff"


@dataclass
class ToolCallEvent(AgentEvent):
    tool_call: ToolCall | None = None

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_call"


@dataclass
class ToolResultEvent(AgentEvent):
    tool_call_id: str = ""
    tool_name: str = ""
    result: str = ""  # JSON string
    # Typed harness outcomes keep their non-success status through the legacy
    # product presenter.  Legacy AgentLoop callers omit these fields and retain
    # the historical succeeded behavior.
    outcome_status: str = "succeeded"
    outcome_error: dict | None = None
    # 七步流水线 Step5 标签（plans/2026-06-24-...）：{"step":5,"observation_summary":"..."}。
    # pipeline off → None = BC（前端不读即无影响）。
    pipeline_label: dict | None = None

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "tool_result"


@dataclass
class AsyncHandoffEvent(AgentEvent):
    """Terminal ReAct event after a durable workflow accepted the request."""

    tool_call_id: str = ""
    tool_name: str = ""
    result: str = ""
    event_id: str = ""
    run_id: str = ""
    request_id: str = ""
    turn_id: str = ""
    dispatch_outcome: DispatchOutcome = DispatchOutcome.ASYNC_HANDOFF

    def __post_init__(self) -> None:
        if not self.type:
            self.type = "async_handoff"
