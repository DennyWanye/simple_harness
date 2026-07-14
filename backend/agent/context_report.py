# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Bounded, request/attempt keyed Context OS diagnostics.

The store is intentionally in-memory and body-free.  It records the request
that was actually prepared for a provider transport, not a reconstruction
from the registry or from a later context snapshot.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping, Optional, Sequence

from agent.context_messages import (
    ProviderAttemptOptions,
    context_attempt_scope,
    current_provider_attempt_options,
    normalize_provider_purpose,
    split_wire_messages,
)


_TERMINAL = {"succeeded", "failed", "cancelled", "cancelled_before_send"}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _token_estimate(value: Any) -> int:
    # Conservative local estimate used only when the provider does not expose
    # its tokenizer.  The estimate method is explicit in the report.
    text = _canonical_json(value)
    return max(1, (len(text.encode("utf-8")) + 2) // 3)


def estimate_selected_attempt_budget(
    *,
    provider: Any,
    model_id: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
    generation_reserve: int,
    attachment_tokens: int = 0,
) -> Any:
    """Re-budget against the selected provider/model window."""

    from deskpet.agent.context_budget import RequestBudget
    from deskpet.agent.tokens import count_messages_tokens

    resolved_model = str(getattr(provider, "model", "") or model_id or "")
    context_window = int(getattr(provider, "context_window", 0) or 0)
    effective_pct = float(getattr(provider, "effective_pct", 0.0) or 0.0)
    if context_window <= 0 or effective_pct <= 0:
        try:
            from llm.model_info import resolve

            info = resolve(resolved_model)
            context_window = context_window or int(info.context_window)
            effective_pct = effective_pct or float(info.effective_pct)
        except Exception:
            context_window = context_window or 32_000
            effective_pct = effective_pct or 0.95
    wire_tools = list(tools or ())
    return RequestBudget(
        context_window=max(1, context_window),
        effective_pct=max(0.01, min(effective_pct, 1.0)),
        generation_reserve=max(0, int(generation_reserve)),
        messages_tokens=count_messages_tokens([dict(item) for item in messages]),
        tool_tokens=_token_estimate(wire_tools) if wire_tools else 0,
        attachment_tokens=max(0, int(attachment_tokens)),
    )


@dataclass(frozen=True)
class FragmentAttemptFact:
    fragment_id: str
    action: str
    reason: str = ""
    estimated_tokens: int = 0
    cache_scope: str = ""
    cache_hash: str = ""


@dataclass(frozen=True)
class CoverageAttemptEntry:
    kind: str
    message_ids: tuple[int, ...]
    segment_id: str = ""
    source_hash: str = ""


@dataclass(frozen=True)
class ContextAttemptReport:
    session_id: str
    request_id: str
    attempt_id: str
    purpose: str
    state: str = "planned"
    provider_id: str = ""
    model_id: str = ""
    adapter_id: str = ""
    adapter_version: str = ""
    message_hash: str = ""
    logical_tool_hash: str = ""
    wire_tool_hash: str = ""
    schema_fingerprint: str = ""
    policy_fingerprint: str = ""
    registry_revision: int = 0
    tool_scope_revision: int = 0
    direct_tool_count: int = 0
    activated_tool_count: int = 0
    deferred_tool_count: int = 0
    schema_tokens_by_name: tuple[tuple[str, int], ...] = ()
    selection_reasons: tuple[str, ...] = ()
    fragments: tuple[FragmentAttemptFact, ...] = ()
    coverage_entries: tuple[CoverageAttemptEntry, ...] = ()
    coverage_valid: Optional[bool] = None
    coverage_gaps: tuple[int, ...] = ()
    coverage_overlaps: tuple[int, ...] = ()
    coverage_stale_segment_ids: tuple[str, ...] = ()
    coverage_broken_causal_groups: tuple[str, ...] = ()
    coverage_page_in_refs: int = 0
    context_page_in_refs: int = 0
    context_page_in_kinds: tuple[str, ...] = ()
    tool_tokens: int = 0
    message_tokens: int = 0
    attachment_tokens: int = 0
    reserve_tokens: int = 0
    effective_input_budget: int = 0
    context_window: int = 0
    planned_tokens: int = 0
    estimate_method: str = "canonical_json_conservative_v1"
    cache_boundary: Optional[int] = None
    cache_fingerprint: str = ""
    actual_input_tokens: Optional[int] = None
    actual_output_tokens: Optional[int] = None
    actual_cache_read_tokens: Optional[int] = None
    actual_cache_write_tokens: Optional[int] = None
    transport_retry_count: int = 0
    requested_compression_model: str = ""
    resolved_compression_model: str = ""
    actual_compression_model: str = ""
    compression_provider: str = ""
    compression_source: str = ""
    compression_failure: str = ""
    reasons: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.monotonic)
    updated_at: float = field(default_factory=time.monotonic)

    def to_public_dict(self) -> dict[str, Any]:
        """Return a body-free payload safe for ContextTrace."""

        value = asdict(self)
        value["created_at"] = self.created_at
        value["updated_at"] = self.updated_at
        return value


class ContextAttemptStore:
    def __init__(self, *, max_per_session: int = 32, ttl_seconds: float = 1800.0) -> None:
        self._max = max(1, int(max_per_session))
        self._ttl = max(1.0, float(ttl_seconds))
        self._reports: dict[tuple[str, str, str], ContextAttemptReport] = {}
        self._order: dict[str, deque[tuple[str, str, str]]] = defaultdict(deque)
        self._orphans: list[dict[str, Any]] = []

    def _key(self, session_id: str, request_id: str, attempt_id: str):
        return (session_id or "global", request_id, attempt_id)

    def _purge(self) -> None:
        cutoff = time.monotonic() - self._ttl
        for key, report in list(self._reports.items()):
            if report.updated_at < cutoff:
                self._reports.pop(key, None)
        for session_id, queue in list(self._order.items()):
            retained = deque(key for key in queue if key in self._reports)
            if retained:
                self._order[session_id] = retained
            else:
                self._order.pop(session_id, None)

    def plan(self, report: ContextAttemptReport) -> None:
        self._purge()
        key = self._key(report.session_id, report.request_id, report.attempt_id)
        if key in self._reports:
            raise ValueError("attempt already planned")
        if report.state != "planned":
            raise ValueError("new attempt must be planned")
        self._reports[key] = report
        queue = self._order[key[0]]
        queue.append(key)
        while len(queue) > self._max:
            self._reports.pop(queue.popleft(), None)

    def get(
        self, session_id: str, request_id: str, attempt_id: str
    ) -> Optional[ContextAttemptReport]:
        self._purge()
        return self._reports.get(self._key(session_id, request_id, attempt_id))

    def update_prepared(
        self,
        session_id: str,
        request_id: str,
        attempt_id: str,
        **updates: Any,
    ) -> Optional[ContextAttemptReport]:
        """CAS-like prepared fact update; terminal attempts are immutable."""

        key = self._key(session_id, request_id, attempt_id)
        current = self._reports.get(key)
        if current is None:
            self._orphans.append({"key": key, "state": "prepared", "reason": "missing"})
            return None
        if current.state != "planned":
            self._orphans.append(
                {"key": key, "state": "prepared", "reason": f"invalid_from:{current.state}"}
            )
            return current
        updated = replace(current, updated_at=time.monotonic(), **updates)
        self._reports[key] = updated
        return updated

    def transition(
        self,
        session_id: str,
        request_id: str,
        attempt_id: str,
        state: str,
        **updates: Any,
    ) -> Optional[ContextAttemptReport]:
        key = self._key(session_id, request_id, attempt_id)
        current = self._reports.get(key)
        if current is None:
            self._orphans.append({"key": key, "state": state, "reason": "missing"})
            return None
        allowed = {
            "planned": {"sent", "cancelled_before_send", "failed"},
            "sent": {"succeeded", "failed", "cancelled"},
        }
        if state not in allowed.get(current.state, set()):
            self._orphans.append(
                {"key": key, "state": state, "reason": f"invalid_from:{current.state}"}
            )
            return current
        updated = replace(current, state=state, updated_at=time.monotonic(), **updates)
        self._reports[key] = updated
        return updated

    def add_transport_retry(self, session_id: str, request_id: str, attempt_id: str) -> None:
        key = self._key(session_id, request_id, attempt_id)
        current = self._reports.get(key)
        if current is not None and current.state == "sent":
            self._reports[key] = replace(
                current,
                transport_retry_count=current.transport_retry_count + 1,
                updated_at=time.monotonic(),
            )

    def list_for_session(self, session_id: str) -> list[ContextAttemptReport]:
        self._purge()
        return [
            self._reports[key]
            for key in self._order.get(session_id or "global", ())
            if key in self._reports
        ]

    def public_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return [report.to_public_dict() for report in self.list_for_session(session_id)]

    def purge_session(self, session_id: str) -> None:
        for key in self._order.pop(session_id or "global", ()):
            self._reports.pop(key, None)

    @property
    def orphans(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._orphans)


def build_prepared_attempt_report(
    *,
    session_id: str,
    request_id: str,
    attempt_id: str,
    purpose: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
    provider_id: str = "",
    model_id: str = "",
    adapter_id: str = "",
    adapter_version: str = "",
    prepared_context: Any = None,
    budget: Any = None,
    compression: Mapping[str, Any] | None = None,
) -> ContextAttemptReport:
    wire_messages, _ = split_wire_messages(messages)
    wire_tools = list(tools or ())
    tool_set = getattr(prepared_context, "tool_set", None)
    schemas_by_name: list[tuple[str, int]] = []
    for schema in wire_tools:
        function = schema.get("function", schema) if isinstance(schema, Mapping) else {}
        name = str(function.get("name", "") or "") if isinstance(function, Mapping) else ""
        schemas_by_name.append((name or "unknown", _token_estimate(schema)))
    decisions = tuple(getattr(prepared_context, "assembly_decisions", ()) or ())
    fragments = tuple(
        FragmentAttemptFact(
            fragment_id=str(getattr(item, "fragment_id", "") or ""),
            action=str(getattr(item, "action", "loaded") or "loaded"),
            reason=str(getattr(item, "reason", "") or ""),
            estimated_tokens=max(0, int(getattr(item, "estimated_tokens", 0) or 0)),
        )
        for item in decisions
    )
    coverage = getattr(prepared_context, "coverage_report", None)
    coverage_entries = tuple(
        CoverageAttemptEntry(
            kind=str(getattr(entry, "kind", "") or ""),
            message_ids=tuple(int(item) for item in getattr(entry, "message_ids", ()) or ()),
            segment_id=str(getattr(entry, "segment_id", "") or ""),
            source_hash=str(getattr(entry, "source_hash", "") or ""),
        )
        for entry in (getattr(coverage, "entries", ()) or ())
    )
    page_in_refs = tuple(getattr(prepared_context, "page_in_refs", ()) or ())
    tool_decisions = tuple(getattr(tool_set, "decisions", ()) or ())
    compression = compression or {}
    message_tokens = int(getattr(budget, "messages_tokens", 0) or _token_estimate(wire_messages))
    tool_tokens = int(getattr(budget, "tool_tokens", 0) or (_token_estimate(wire_tools) if wire_tools else 0))
    attachment_tokens = int(getattr(budget, "attachment_tokens", 0) or 0)
    generation_reserve = int(getattr(budget, "generation_reserve", 0) or 0)
    context_window = int(getattr(budget, "context_window", 0) or 0)
    effective_input_budget = int(getattr(budget, "effective_input_budget", 0) or 0)
    return ContextAttemptReport(
        session_id=session_id,
        request_id=request_id,
        attempt_id=attempt_id,
        purpose=purpose,
        provider_id=provider_id,
        model_id=model_id,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        message_hash=_digest(wire_messages),
        logical_tool_hash=str(getattr(tool_set, "schema_fingerprint", "") or ""),
        wire_tool_hash=_digest(wire_tools),
        schema_fingerprint=str(getattr(tool_set, "schema_fingerprint", "") or ""),
        policy_fingerprint=str(getattr(tool_set, "policy_fingerprint", "") or ""),
        registry_revision=int(getattr(tool_set, "registry_revision", 0) or 0),
        tool_scope_revision=int(getattr(tool_set, "revision", 0) or 0),
        direct_tool_count=len(tuple(getattr(tool_set, "direct", ()) or ())),
        activated_tool_count=len(tuple(getattr(tool_set, "activated", ()) or ())),
        deferred_tool_count=len(tuple(getattr(tool_set, "deferred", ()) or ())),
        schema_tokens_by_name=tuple(schemas_by_name),
        selection_reasons=tuple(
            f"{getattr(item, 'name', '')}:{getattr(item, 'reason', '')}"
            for item in tool_decisions
        ),
        fragments=fragments,
        coverage_entries=coverage_entries,
        coverage_valid=(
            bool(getattr(coverage, "valid")) if coverage is not None else None
        ),
        coverage_gaps=tuple(int(item) for item in getattr(coverage, "gaps", ()) or ()),
        coverage_overlaps=tuple(
            int(item) for item in getattr(coverage, "overlaps", ()) or ()
        ),
        coverage_stale_segment_ids=tuple(
            str(item)
            for item in getattr(coverage, "stale_segment_ids", ()) or ()
        ),
        coverage_broken_causal_groups=tuple(
            str(item)
            for item in getattr(coverage, "broken_causal_groups", ()) or ()
        ),
        coverage_page_in_refs=sum(
            1
            for entry in coverage_entries
            if entry.kind == "summary" and entry.segment_id
        ),
        context_page_in_refs=len(page_in_refs),
        context_page_in_kinds=tuple(sorted({
            str(getattr(ref, "kind", "") or "") for ref in page_in_refs
            if getattr(ref, "kind", "")
        })),
        tool_tokens=tool_tokens,
        message_tokens=message_tokens,
        attachment_tokens=attachment_tokens,
        reserve_tokens=generation_reserve,
        effective_input_budget=effective_input_budget,
        context_window=context_window,
        planned_tokens=message_tokens + tool_tokens + attachment_tokens,
        cache_boundary=getattr(prepared_context, "stable_prefix_boundary", None),
        cache_fingerprint=str(
            getattr(prepared_context, "stable_prefix_fingerprint", "") or ""
        ),
        requested_compression_model=str(compression.get("requested_model", "") or ""),
        resolved_compression_model=str(compression.get("resolved_model", "") or ""),
        actual_compression_model=str(compression.get("actual_model", "") or ""),
        compression_provider=str(compression.get("provider", "") or ""),
        compression_source=str(compression.get("source", "") or ""),
        compression_failure=str(compression.get("failure", "") or ""),
    )


_ATTEMPT_STORE: Optional[ContextAttemptStore] = None


def set_context_attempt_store(store: Optional[ContextAttemptStore]) -> None:
    global _ATTEMPT_STORE
    _ATTEMPT_STORE = store


def get_context_attempt_store() -> Optional[ContextAttemptStore]:
    return _ATTEMPT_STORE


def _provider_identity(provider: Any, model_id: str = "") -> tuple[str, str, str, str]:
    provider_id = str(
        getattr(provider, "provider_id", "")
        or getattr(provider, "id", "")
        or getattr(provider, "name", "")
        or type(provider).__name__
    )
    resolved_model = str(getattr(provider, "model", "") or model_id or "")
    adapter_id = str(
        getattr(provider, "adapter_id", "")
        or ("openai-compatible" if hasattr(provider, "base_url") else type(provider).__name__)
    )
    adapter_version = str(getattr(provider, "adapter_version", "") or "v1")
    return provider_id, resolved_model, adapter_id, adapter_version


def _has_complete_attempt(options: ProviderAttemptOptions | None) -> bool:
    return bool(
        options
        and options.session_id
        and options.request_id
        and options.attempt_id
    )


def _rebudget_current_attempt(
    *,
    options: ProviderAttemptOptions,
    provider: Any,
    model_id: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
    generation_reserve: int,
    attachment_tokens: int,
) -> None:
    if _ATTEMPT_STORE is None:
        return
    current = _ATTEMPT_STORE.get(
        str(options.session_id), str(options.request_id), str(options.attempt_id)
    )
    if current is None or current.state != "planned":
        return
    provider_id, resolved_model, adapter_id, adapter_version = _provider_identity(
        provider, model_id
    )
    budget = estimate_selected_attempt_budget(
        provider=provider,
        model_id=resolved_model,
        messages=messages,
        tools=tools,
        generation_reserve=(generation_reserve or current.reserve_tokens),
        attachment_tokens=(attachment_tokens or current.attachment_tokens),
    )
    wire_messages, _ = split_wire_messages(messages)
    wire_tools = list(tools or ())
    _ATTEMPT_STORE.update_prepared(
        str(options.session_id),
        str(options.request_id),
        str(options.attempt_id),
        provider_id=provider_id,
        model_id=resolved_model,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        message_hash=_digest(wire_messages),
        wire_tool_hash=_digest(wire_tools),
        message_tokens=budget.messages_tokens,
        tool_tokens=budget.tool_tokens,
        attachment_tokens=budget.attachment_tokens,
        reserve_tokens=budget.generation_reserve,
        effective_input_budget=budget.effective_input_budget,
        context_window=budget.context_window,
        planned_tokens=budget.planned_input_tokens,
    )
    if not budget.fits:
        _ATTEMPT_STORE.transition(
            str(options.session_id),
            str(options.request_id),
            str(options.attempt_id),
            "failed",
            reasons=(*current.reasons, "provider_context_budget_exceeded"),
        )
        raise RuntimeError("provider_context_budget_exceeded")


async def auto_context_attempt_call(
    *,
    invoke: Any,
    provider: Any,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
    model_id: str = "",
    purpose: str | None = None,
    mark_sent_at_dispatch: bool,
    generation_reserve: int = 0,
    attachment_tokens: int = 0,
) -> Any:
    """Create a bounded auxiliary attempt when no complete outer scope exists."""

    current = current_provider_attempt_options()
    if _ATTEMPT_STORE is None:
        return await invoke()
    if _has_complete_attempt(current):
        # The outer AgentLoop/provider-dispatch seam already planned and
        # re-budgeted this exact selected attempt. Re-budgeting again here
        # with the adapter's default max_tokens creates a second budget owner
        # (notably 8192 reserve on an 8K model) and can block a valid request.
        if mark_sent_at_dispatch:
            mark_current_attempt_sent()
        return await invoke()

    session_id = str((current.session_id if current else None) or "global")
    request_id = str((current.request_id if current else None) or f"aux:{uuid.uuid4().hex}")
    attempt_id = str((current.attempt_id if current else None) or f"attempt:{uuid.uuid4().hex}")
    resolved_purpose = normalize_provider_purpose(
        (current.purpose if current else None) or purpose
    )
    provider_id, resolved_model, adapter_id, adapter_version = _provider_identity(
        provider, model_id
    )
    budget = estimate_selected_attempt_budget(
        provider=provider,
        model_id=resolved_model,
        messages=messages,
        tools=tools,
        generation_reserve=generation_reserve,
        attachment_tokens=attachment_tokens,
    )
    report = build_prepared_attempt_report(
        session_id=session_id,
        request_id=request_id,
        attempt_id=attempt_id,
        purpose=resolved_purpose,
        messages=messages,
        tools=tools,
        provider_id=provider_id,
        model_id=resolved_model,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        budget=budget,
    )
    _ATTEMPT_STORE.plan(report)
    if not budget.fits:
        _ATTEMPT_STORE.transition(
            session_id,
            request_id,
            attempt_id,
            "failed",
            reasons=("provider_context_budget_exceeded",),
        )
        raise RuntimeError("provider_context_budget_exceeded")
    options = ProviderAttemptOptions(
        cache_boundary=current.cache_boundary if current else None,
        cache_fingerprint=current.cache_fingerprint if current else None,
        purpose=resolved_purpose,
        session_id=session_id,
        request_id=request_id,
        attempt_id=attempt_id,
    )
    with context_attempt_scope(options):
        try:
            if mark_sent_at_dispatch:
                mark_current_attempt_sent()
            result = await invoke()
        except BaseException as exc:
            finish_current_attempt(
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                reason=type(exc).__name__,
            )
            raise
        usage = getattr(result, "usage", None)
        if usage is None and isinstance(result, Mapping):
            usage = result.get("usage")
        finish_current_attempt("succeeded", usage=usage)
        return result


async def auto_context_attempt_iter(
    *,
    iterator: Any,
    provider: Any,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
    model_id: str = "",
    purpose: str | None = None,
    mark_sent_at_dispatch: bool,
    generation_reserve: int = 0,
    attachment_tokens: int = 0,
):
    current = current_provider_attempt_options()
    if _ATTEMPT_STORE is None:
        async for item in iterator():
            yield item
        return
    if _has_complete_attempt(current):
        # A complete outer scope owns the selected-provider budget. The
        # transport wrapper only marks sent/terminal and must not overwrite it.
        if mark_sent_at_dispatch:
            mark_current_attempt_sent()
        async for item in iterator():
            yield item
        return

    session_id = str((current.session_id if current else None) or "global")
    request_id = str((current.request_id if current else None) or f"aux:{uuid.uuid4().hex}")
    attempt_id = str((current.attempt_id if current else None) or f"attempt:{uuid.uuid4().hex}")
    resolved_purpose = normalize_provider_purpose(
        (current.purpose if current else None) or purpose
    )
    provider_id, resolved_model, adapter_id, adapter_version = _provider_identity(
        provider, model_id
    )
    budget = estimate_selected_attempt_budget(
        provider=provider,
        model_id=resolved_model,
        messages=messages,
        tools=tools,
        generation_reserve=generation_reserve,
        attachment_tokens=attachment_tokens,
    )
    _ATTEMPT_STORE.plan(
        build_prepared_attempt_report(
            session_id=session_id,
            request_id=request_id,
            attempt_id=attempt_id,
            purpose=resolved_purpose,
            messages=messages,
            tools=tools,
            provider_id=provider_id,
            model_id=resolved_model,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            budget=budget,
        )
    )
    if not budget.fits:
        _ATTEMPT_STORE.transition(
            session_id,
            request_id,
            attempt_id,
            "failed",
            reasons=("provider_context_budget_exceeded",),
        )
        raise RuntimeError("provider_context_budget_exceeded")
    options = ProviderAttemptOptions(
        cache_boundary=current.cache_boundary if current else None,
        cache_fingerprint=current.cache_fingerprint if current else None,
        purpose=resolved_purpose,
        session_id=session_id,
        request_id=request_id,
        attempt_id=attempt_id,
    )
    final_usage: Any = None
    with context_attempt_scope(options):
        try:
            if mark_sent_at_dispatch:
                mark_current_attempt_sent()
            async for item in iterator():
                if isinstance(item, Mapping) and item.get("type") == "final":
                    final_usage = item.get("usage")
                yield item
        except BaseException as exc:
            finish_current_attempt(
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                reason=type(exc).__name__,
            )
            raise
        if final_usage is None:
            final_usage = getattr(provider, "last_usage", None)
        finish_current_attempt("succeeded", usage=final_usage)


def mark_current_attempt_sent() -> None:
    """Transport coroutine first-statement marker; intentionally no await."""

    options = current_provider_attempt_options()
    if _ATTEMPT_STORE is None or options is None:
        return
    if not (options.session_id and options.request_id and options.attempt_id):
        return
    current = _ATTEMPT_STORE.get(
        options.session_id, options.request_id, options.attempt_id
    )
    if current is None:
        return
    if current.state == "planned":
        _ATTEMPT_STORE.transition(
            options.session_id, options.request_id, options.attempt_id, "sent"
        )


def mark_current_attempt_transport_retry() -> None:
    options = current_provider_attempt_options()
    if _ATTEMPT_STORE is None or not _has_complete_attempt(options):
        return
    assert options is not None
    _ATTEMPT_STORE.add_transport_retry(
        str(options.session_id), str(options.request_id), str(options.attempt_id)
    )


def finish_current_attempt(state: str, *, usage: Any = None, reason: str = "") -> None:
    options = current_provider_attempt_options()
    if _ATTEMPT_STORE is None or options is None:
        return
    if not (options.session_id and options.request_id and options.attempt_id):
        return
    current = _ATTEMPT_STORE.get(options.session_id, options.request_id, options.attempt_id)
    if current is None or current.state in _TERMINAL:
        return
    terminal = state
    if state == "cancelled" and current.state == "planned":
        terminal = "cancelled_before_send"
    updates: dict[str, Any] = {}
    if reason:
        updates["reasons"] = (*current.reasons, reason[:200])
    if usage is not None:
        def _read(*names: str) -> Optional[int]:
            for name in names:
                value = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
                if value is not None:
                    return int(value)
            return None
        updates.update(
            actual_input_tokens=_read("input_tokens", "prompt_tokens"),
            actual_output_tokens=_read("output_tokens", "completion_tokens"),
            actual_cache_read_tokens=_read("cache_read_tokens", "cached_tokens"),
            actual_cache_write_tokens=_read("cache_write_tokens"),
        )
    _ATTEMPT_STORE.transition(
        options.session_id,
        options.request_id,
        options.attempt_id,
        terminal,
        **updates,
    )


__all__ = [
    "auto_context_attempt_call",
    "auto_context_attempt_iter",
    "ContextAttemptReport",
    "CoverageAttemptEntry",
    "ContextAttemptStore",
    "FragmentAttemptFact",
    "build_prepared_attempt_report",
    "finish_current_attempt",
    "get_context_attempt_store",
    "mark_current_attempt_sent",
    "mark_current_attempt_transport_retry",
    "estimate_selected_attempt_budget",
    "set_context_attempt_store",
]
