# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from llm.context_messages import CONTEXT_MESSAGE_META_KEY, append_control, append_transcript

logger = logging.getLogger("deskpet.agent.loop")


def _context_fragment_id(message: dict[str, Any] | None) -> str | None:
    if not isinstance(message, dict):
        return None
    metadata = message.get(CONTEXT_MESSAGE_META_KEY)
    if not isinstance(metadata, dict):
        return None
    fragment_id = metadata.get("fragment_id")
    return str(fragment_id) if fragment_id else None


def _latest_context_anchor(messages: list[dict[str, Any]], *, fallback: str) -> str:
    for message in reversed(messages):
        fragment_id = _context_fragment_id(message)
        if fragment_id:
            return fragment_id
    return fallback


def _append_loop_transcript(
    messages: list[dict[str, Any]],
    value: dict[str, Any] | str,
    *,
    metadata_enabled: bool,
    source: str,
    role: str,
    fragment_id: str,
    causal_group_id: str | None = None,
) -> dict[str, Any]:
    if metadata_enabled:
        return append_transcript(
            messages,
            value,
            source=source,
            role=role,
            fragment_id=fragment_id,
            causal_group_id=causal_group_id,
        )
    message = {"role": role, "content": value} if isinstance(value, str) else value
    messages.append(message)
    return message


def _append_loop_control(
    messages: list[dict[str, Any]],
    value: dict[str, Any] | str,
    *,
    metadata_enabled: bool,
    source: str,
    anchor_after: str,
    fragment_id: str,
    protected: bool = False,
    trim_policy: Literal["never", "truncate", "drop", "summarize", "page_in"] = "drop",
) -> dict[str, Any]:
    if metadata_enabled:
        return append_control(
            messages,
            value,
            source=source,
            anchor_after=anchor_after,
            fragment_id=fragment_id,
            protected=protected,
            trim_policy=trim_policy,
        )
    message = {"role": "system", "content": value} if isinstance(value, str) else value
    messages.append(message)
    return message


_SELFCHECK_TIER3_AT = 30  # iter >= this → forced stop


# ───────────────────── event dataclasses ─────────────────────


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
    # P4-S24: passed through so `_run_chat` can persist it on the final
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
class _RunState:
    """AgentLoop._run_impl 的运行状态：原函数的局部变量与参数（阶段 6B 切段用）。"""

    flow: Any = None
    _AGENTIC_TOOL_THRESHOLD: Any = None
    _active_compressor: Any = None
    _actual_compaction_model: Any = None
    _agentic: Any = None
    _always_goal: Any = None
    _anchor_lines: Any = None
    _attempt_provider: Any = None
    _attempt_purpose: Any = None
    _attempt_tools: Any = None
    _authority_error: Any = None
    _block_detail: Any = None
    _budget: Any = None
    _budget_block_pending: Any = None
    _budget_model: Any = None
    _budget_warn_emitted: Any = None
    _candidate: Any = None
    _cap_summary: Any = None
    _chain_budget_rescue_pending: Any = None
    _compaction_cycle_id: Any = None
    _compaction_cycle_index: Any = None
    _compaction_source_event_id: Any = None
    _compaction_warn_logged: Any = None
    _completion_anchor: Any = None
    _completion_reply: Any = None
    _compress_kwargs: Any = None
    _compression_resolution: Any = None
    _context_remount: Any = None
    _cost_delta: Any = None
    _cresult: Any = None
    _ctoken_est: Any = None
    _ctx_cfg: Any = None
    _ctx_cfg_for_compression: Any = None
    _ctx_compact_at: Any = None
    _ctx_in: Any = None
    _ctx_out: Any = None
    _ctx_ratio: Any = None
    _ctx_should_compress: Any = None
    _cur_replan: Any = None
    _done: Any = None
    _ev_dec: Any = None
    _ev_result: Any = None
    _eval_goal_text: Any = None
    _eval_ledger: Any = None
    _evidence_anchor: Any = None
    _evidence_reply: Any = None
    _evidence_str: Any = None
    _final_content: Any = None
    _first: Any = None
    _flush_body: Any = None
    _flush_parts: Any = None
    _for_context: Any = None
    _for_fn: Any = None
    _force_finish_next: Any = None
    _force_finish_queued: Any = None
    _ga_fn: Any = None
    _goal: Any = None
    _goal_anchor: Any = None
    _goal_reply: Any = None
    _gt: Any = None
    _gt_fn: Any = None
    _hint: Any = None
    _last_real_prompt_tokens: Any = None
    _last_user: Any = None
    _latest_compression_report: Any = None
    _ledger: Any = None
    _m: Any = None
    _maybe_cost: Any = None
    _model_info_for_compression: Any = None
    _ok: Any = None
    _pd: Any = None
    _pending: Any = None
    _pg_fn: Any = None
    _pit: Any = None
    _post_budget: Any = None
    _preflush_done: Any = None
    _prev_task_replanning: Any = None
    _reason: Any = None
    _record: Any = None
    _refl: Any = None
    _requested_compression_model: Any = None
    _resolved_candidate: Any = None
    _resolved_model: Any = None
    _run_tool_calls: Any = None
    _sc: Any = None
    _scope_store: Any = None
    _self_check_anchor: Any = None
    _self_check_reply: Any = None
    _skill_compaction_happened: Any = None
    _stagnant: Any = None
    _still_blocked: Any = None
    _usage: Any = None
    _v: Any = None
    _verdict: Any = None
    _verify_anchor: Any = None
    _verify_final_done: Any = None
    _verify_reply: Any = None
    _vg_goal_text: Any = None
    _wm_tokens: Any = None
    allowed_tool_names: Any = None
    attachment_tokens: Any = None
    budget_left: Any = None
    bullet_list: Any = None
    call: Any = None
    call_llm_kwargs: Any = None
    candidate_budgets: Any = None
    chain_cycle_id: Any = None
    chain_mode: Any = None
    completion_nudges_used: Any = None
    content: Any = None
    context_metadata_enabled: Any = None
    context_request_id: Any = None
    coverage: Any = None
    coverage_still_pending: Any = None
    current_tool_set: Any = None
    delta_count: Any = None
    empty_reason: Any = None
    ephemeral_pass: Any = None
    ev: Any = None
    ev_type: Any = None
    exclusive_call: Any = None
    external_feedback: Any = None
    final_budget_fits: Any = None
    final_budgets: Any = None
    final_dict: Any = None
    ga: Any = None
    group_id: Any = None
    has_hidden_reasoning: Any = None
    host_validated_zero_tool_terminal: Any = None
    idx: Any = None
    incomplete: Any = None
    initial_budget: Any = None
    initial_coverage_jobs: Any = None
    initial_cycle_id: Any = None
    initial_resolution: Any = None
    is_sentinel_run: Any = None
    iteration: Any = None
    iteration_offset: Any = None
    last_exc: Any = None
    last_text: Any = None
    launch_call_kwargs: Any = None
    launch_operation_id: Any = None
    ledger: Any = None
    lines: Any = None
    llm_kwargs: Any = None
    loop_user_request: Any = None
    mark: Any = None
    max_reasoning_only_nudges: Any = None
    messages: Any = None
    model: Any = None
    msg: Any = None
    needs_nonstream_fallback: Any = None
    next_id: Any = None
    next_idx: Any = None
    next_prov: Any = None
    prepared_context: Any = None
    prov: Any = None
    prov_id: Any = None
    provider_chain: Any = None
    provider_launch_snapshot: Any = None
    raw: Any = None
    reason: Any = None
    reasoning_only_nudges_used: Any = None
    reasoning_reply: Any = None
    rebound: Any = None
    rejection: Any = None
    replan: Any = None
    replan_for_budget: Any = None
    replanned: Any = None
    replanned_context: Any = None
    replanned_tool_set: Any = None
    request_budget: Any = None
    reserve: Any = None
    resolution: Any = None
    response: Any = None
    safe_budget: Any = None
    scoped_evidence: Any = None
    selected_calls: Any = None
    session_id: Any = None
    status: Any = None
    stream: Any = None
    stream_capable: Any = None
    stream_failed_with: Any = None
    t: Any = None
    task_id: Any = None
    tid: Any = None
    tier: Any = None
    todos: Any = None
    tool_call: Any = None
    tool_names_filter: Any = None
    tool_schemas: Any = None
    tools_filter: Any = None
    tools_used: Any = None
    tools_used_count: Any = None
    totals: Any = None
    tried: Any = None
    unmatched_lines: Any = None
    use_model: Any = None
    used_common_replan: Any = None
    v_outcome: Any = None
    verify_nudges_used: Any = None
    working_messages: Any = None
