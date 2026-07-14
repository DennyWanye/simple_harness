# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Prepare one immutable Context OS request from fragments and one tool draft."""
from __future__ import annotations

import re

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from agent.context_messages import ContextMessageMeta, tag_message
from deskpet.agent.assembler.bundle import (
    AttachmentRef,
    ContextBundle,
    ContextDecision,
    PageInRef,
    PreparedContext,
)
from deskpet.agent.context_budget import (
    RequestBudget,
    estimate_request_budget,
    prepare_openai_tool_payload,
)
from deskpet.tools.capabilities import (
    PreparedToolPayload,
    ToolCapabilityResolver,
    ToolEligibilityContext,
    ToolExposureIntent,
)


class ContextBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class PlannedContextRequest:
    prepared_context: PreparedContext
    tool_payload: PreparedToolPayload
    budget: RequestBudget


class ContextRequestPlanner:
    def __init__(
        self,
        resolver: ToolCapabilityResolver,
        *,
        history_planner: Any = None,
        snapshot_store: Any = None,
        page_in_store: Any = None,
    ) -> None:
        self._resolver = resolver
        self._history_planner = history_planner
        self._snapshot_store = snapshot_store
        self._page_in_store = page_in_store

    async def prepare_initial(
        self,
        bundle: ContextBundle,
        *,
        base_system: str,
        history: list[dict[str, Any]],
        user_message: str,
        eligibility: ToolEligibilityContext,
        context_window: int,
        effective_pct: float,
        generation_reserve: int,
        conditional_direct_names: tuple[str, ...] = (
            "session_history_page_in",
            "context_page_in",
        ),
        active_snapshot: Any = None,
        prebuilt_messages: Optional[list[dict[str, Any]]] = None,
        attachment_refs: tuple[AttachmentRef, ...] = (),
        attachment_tokens: int = 0,
        current_message_id: int | None = None,
    ) -> PlannedContextRequest:
        normalized_attachment_tokens = max(
            max(0, int(attachment_tokens)),
            sum(max(0, int(ref.estimated_tokens)) for ref in attachment_refs),
        )
        # Freeze the DB compare-and-swap base before any catalog, history, or
        # budget work. Re-reading the latest revision immediately before the
        # write would let a concurrent activation commit and then be
        # overwritten by this older base toolset with a valid CAS.
        snapshot_start = None
        if active_snapshot is not None and self._snapshot_store is not None:
            snapshot_start = await self._snapshot_store.get(
                active_snapshot.session_id,
                active_snapshot.task_scope_id,
            )
        intent = bundle.tool_exposure_intent or ToolExposureIntent(
            direct_selectors=tuple(_schema_names(bundle.tool_schemas))
        )
        explicit_tool_names = _explicit_tool_name_candidates(user_message)
        conditional_direct_names = tuple(dict.fromkeys(
            (*conditional_direct_names, *explicit_tool_names)
        ))
        # Exactly one catalog/policy read for the initial request.
        draft = self._resolver.resolve_draft(
            intent,
            eligibility=eligibility,
            conditional_direct_names=conditional_direct_names,
        )
        conditional_canonical_names = tuple(
            cap.ref.name for cap in draft.conditional_direct
        )
        page_in_candidates = _page_in_candidates(bundle)
        prefix, decisions, stable_boundary, stable_fingerprint = _render_prefix(
            bundle, base_system
        )
        explicit_prepared = tuple(dict.fromkeys(
            canonical_name
            for requested_name, canonical_name in draft.conditional_name_map
            if requested_name in explicit_tool_names
        ))
        base_set = draft.finalize(required_conditional_names=explicit_prepared)
        base_payload = prepare_openai_tool_payload(base_set)
        selected_set = base_set
        selected_payload = base_payload
        required_names: set[str] = set(explicit_prepared)
        if page_in_candidates:
            required_names.add("context_page_in")
            if not any(
                cap.ref.name == "context_page_in" for cap in draft.conditional_direct
            ):
                raise ContextBudgetExceeded("context_page_in_not_prepared")
            selected_set = draft.finalize(
                required_conditional_names=("context_page_in",),
                scope_id=base_set.scope_id,
            )
            selected_payload = prepare_openai_tool_payload(selected_set)
        history_plan = None
        prefix_messages, post_history_messages, current_message = _fixed_message_parts(
            prefix=prefix,
            prebuilt_messages=prebuilt_messages,
            late_system_nudge=bundle.late_system_nudge,
            user_message=user_message,
        )

        async def _plan_history(
            payload: PreparedToolPayload,
            *,
            target_context_window: int = context_window,
            target_effective_pct: float = effective_pct,
            target_generation_reserve: int = generation_reserve,
        ):
            fixed_budget = estimate_request_budget(
                [*prefix_messages, *post_history_messages, current_message],
                payload,
                context_window=target_context_window,
                effective_pct=target_effective_pct,
                generation_reserve=target_generation_reserve,
                attachment_tokens=normalized_attachment_tokens,
            )
            available_tokens = max(
                0,
                fixed_budget.effective_input_budget
                - fixed_budget.messages_tokens
                - payload.wire_tokens
                - normalized_attachment_tokens,
            )
            return await self._history_planner.plan(
                eligibility.session_id,
                available_tokens=available_tokens,
                current_message_id=current_message_id,
            )

        if self._history_planner is not None:
            # SessionDB is authoritative even when the assembler's prebuilt L2
            # tail happens to fit.  Never let a fixed top-k shortcut masquerade
            # as complete Session coverage.
            history_plan = await _plan_history(selected_payload)
            _validate_history_plan(history_plan, allow_compaction_jobs=True)
            required_names.update(history_plan.required_direct_tools)
            if history_plan.compaction_jobs:
                required_names.add("session_history_page_in")
            required = tuple(
                name for name in conditional_canonical_names if name in required_names
            )
            if required and set(required) != {
                cap.ref.name for cap in selected_set.direct
                if cap.ref.name in conditional_canonical_names
            }:
                selected_set = draft.finalize(
                    required_conditional_names=required,
                    scope_id=base_set.scope_id,
                )
                selected_payload = prepare_openai_tool_payload(selected_set)
                history_plan = await _plan_history(selected_payload)
                _validate_history_plan(history_plan, allow_compaction_jobs=True)
            selected_history = _wire_history_messages(history_plan.messages)
            selected_messages = [
                *prefix_messages,
                *selected_history,
                *post_history_messages,
                current_message,
            ]
        else:
            selected_messages = (
                list(prebuilt_messages)
                if prebuilt_messages is not None
                else [*prefix, *history, *post_history_messages, current_message]
            )

        selected_budget = estimate_request_budget(
            selected_messages,
            selected_payload,
            context_window=context_window,
            effective_pct=effective_pct,
            generation_reserve=generation_reserve,
            attachment_tokens=normalized_attachment_tokens,
        )
        page_in_refs: tuple[PageInRef, ...] = ()
        if page_in_candidates:
            if self._page_in_store is None:
                raise ContextBudgetExceeded("context_page_in_store_unavailable")
            page_in_refs = tuple(
                _bind_page_in_candidate(
                    self._page_in_store,
                    candidate,
                    eligibility=eligibility,
                    scope_id=selected_set.scope_id,
                )
                for candidate in page_in_candidates
            )
        if not selected_budget.fits:
            prefix, decisions, stable_boundary, stable_fingerprint = _render_prefix(
                bundle,
                base_system,
                page_in_refs={ref.fragment_id: ref for ref in page_in_refs},
            )
            prefix_messages, post_history_messages, current_message = _fixed_message_parts(
                prefix=prefix,
                prebuilt_messages=prebuilt_messages,
                late_system_nudge=bundle.late_system_nudge,
                user_message=user_message,
            )
            selected_messages = [
                *prefix_messages,
                *(_wire_history_messages(history_plan.messages) if history_plan is not None else history),
                *post_history_messages,
                current_message,
            ]
            selected_budget = estimate_request_budget(
                selected_messages, selected_payload,
                context_window=context_window, effective_pct=effective_pct,
                generation_reserve=generation_reserve,
                attachment_tokens=normalized_attachment_tokens,
            )
        if not selected_budget.fits:
            raise ContextBudgetExceeded(
                "context_budget_exceeded "
                f"planned={selected_budget.planned_input_tokens} "
                f"effective={selected_budget.effective_input_budget} "
                f"messages={selected_budget.messages_tokens} "
                f"tools={selected_budget.tool_tokens} "
                f"attachments={selected_budget.attachment_tokens}"
            )

        prepared = PreparedContext(
            messages=selected_messages,
            tool_set=selected_set,
            stable_prefix_boundary=stable_boundary,
            stable_prefix_fingerprint=stable_fingerprint,
            assembly_decisions=decisions,
            coverage_report=(
                history_plan.coverage_report if history_plan is not None else None
            ),
            coverage_compaction_jobs=(
                history_plan.compaction_jobs if history_plan is not None else ()
            ),
            attachment_refs=tuple(attachment_refs),
            attachment_tokens=normalized_attachment_tokens,
            request_budget=selected_budget,
            page_in_refs=page_in_refs,
            page_in_store=self._page_in_store,
        )
        if history_plan is not None:
            async def _replan_for_budget(
                *,
                context_window: int,
                effective_pct: float,
                generation_reserve: int,
            ) -> PlannedContextRequest:
                refreshed = await _plan_history(
                    selected_payload,
                    target_context_window=context_window,
                    target_effective_pct=effective_pct,
                    target_generation_reserve=generation_reserve,
                )
                _validate_history_plan(refreshed, allow_compaction_jobs=True)
                missing_tools = set(refreshed.required_direct_tools) - {
                    cap.ref.name for cap in selected_set.direct
                }
                if missing_tools:
                    raise ContextBudgetExceeded(
                        "session_history_page_in_not_prepared"
                    )
                refreshed_messages = [
                    *prefix_messages,
                    *_wire_history_messages(refreshed.messages),
                    *post_history_messages,
                    current_message,
                ]
                refreshed_budget = estimate_request_budget(
                    refreshed_messages,
                    selected_payload,
                    context_window=context_window,
                    effective_pct=effective_pct,
                    generation_reserve=generation_reserve,
                    attachment_tokens=normalized_attachment_tokens,
                )
                if not refreshed.compaction_jobs and not refreshed_budget.fits:
                    raise ContextBudgetExceeded("context_budget_exceeded_after_replan")
                prepared.messages = refreshed_messages
                prepared.coverage_report = refreshed.coverage_report
                prepared.coverage_compaction_jobs = tuple(
                    refreshed.compaction_jobs or ()
                )
                prepared.request_budget = refreshed_budget
                return PlannedContextRequest(
                    prepared,
                    selected_payload,
                    refreshed_budget,
                )

            prepared.replan_for_budget = _replan_for_budget
        if history_plan is not None and history_plan.compaction_jobs:
            async def _replan_after_compaction() -> PlannedContextRequest:
                refreshed = await _plan_history(selected_payload)
                _validate_history_plan(refreshed, allow_compaction_jobs=False)
                missing_tools = set(refreshed.required_direct_tools) - {
                    cap.ref.name for cap in selected_set.direct
                }
                if missing_tools:
                    raise ContextBudgetExceeded(
                        "session_history_page_in_not_prepared"
                    )
                refreshed_messages = [
                    *prefix_messages,
                    *_wire_history_messages(refreshed.messages),
                    *post_history_messages,
                    current_message,
                ]
                refreshed_budget = estimate_request_budget(
                    refreshed_messages,
                    selected_payload,
                    context_window=context_window,
                    effective_pct=effective_pct,
                    generation_reserve=generation_reserve,
                    attachment_tokens=normalized_attachment_tokens,
                )
                if not refreshed_budget.fits:
                    raise ContextBudgetExceeded("context_budget_exceeded_after_coverage")
                prepared.messages = refreshed_messages
                prepared.coverage_report = refreshed.coverage_report
                prepared.coverage_compaction_jobs = ()
                prepared.request_budget = refreshed_budget
                return PlannedContextRequest(
                    prepared,
                    selected_payload,
                    refreshed_budget,
                )

            prepared.replan_after_compaction = _replan_after_compaction
        if active_snapshot is not None and self._snapshot_store is not None:
            summary = _tool_summary(selected_set, selected_payload)
            expected_row_revision = (
                int(snapshot_start.handle.row_revision)
                if snapshot_start is not None
                else 0
            )
            receipt = await self._snapshot_store.persist_projection_with_tool_context_cas(
                active_snapshot,
                expected_row_revision=expected_row_revision,
                prepared_toolset_summary=summary,
            )
            prepared.active_snapshot_handle = receipt.new_handle
        return PlannedContextRequest(prepared, selected_payload, selected_budget)

    def replan(
        self,
        prepared: PreparedContext,
        *,
        context_window: int,
        effective_pct: float,
        generation_reserve: int,
    ) -> PlannedContextRequest:
        if prepared.tool_set is None:
            raise ValueError("existing_tool_set is required")
        payload = prepare_openai_tool_payload(prepared.tool_set)
        budget = estimate_request_budget(
            prepared.messages,
            payload,
            context_window=context_window,
            effective_pct=effective_pct,
            generation_reserve=generation_reserve,
            attachment_tokens=max(0, int(prepared.attachment_tokens)),
        )
        if not budget.fits:
            raise ContextBudgetExceeded("context_budget_exceeded")
        prepared.request_budget = budget
        return PlannedContextRequest(prepared, payload, budget)


def _validate_history_plan(plan: Any, *, allow_compaction_jobs: bool) -> None:
    jobs = tuple(getattr(plan, "compaction_jobs", ()) or ())
    report = getattr(plan, "coverage_report", None)
    if jobs and allow_compaction_jobs:
        if not getattr(plan, "blocked", False):
            raise ContextBudgetExceeded("coverage_jobs_without_block")
        return
    if jobs:
        raise ContextBudgetExceeded("coverage_compaction_incomplete")
    if getattr(plan, "blocked", False):
        raise ContextBudgetExceeded(
            getattr(plan, "blocked_reason", "") or "session_history_coverage_invalid"
        )
    if report is None or not bool(getattr(report, "valid", False)):
        raise ContextBudgetExceeded("session_history_coverage_invalid")
    if (
        tuple(getattr(report, "gaps", ()) or ())
        or tuple(getattr(report, "overlaps", ()) or ())
        or tuple(getattr(report, "stale_segment_ids", ()) or ())
        or tuple(getattr(report, "broken_causal_groups", ()) or ())
    ):
        raise ContextBudgetExceeded("session_history_coverage_invalid")


def _fixed_message_parts(
    *,
    prefix: list[dict[str, Any]],
    prebuilt_messages: Optional[list[dict[str, Any]]],
    late_system_nudge: str,
    user_message: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if prebuilt_messages is None:
        trailing = (
            [{"role": "system", "content": late_system_nudge}]
            if late_system_nudge
            else []
        )
        return list(prefix), trailing, {"role": "user", "content": user_message}

    messages = [dict(message) for message in prebuilt_messages]
    prefix_end = 0
    while prefix_end < len(messages) and messages[prefix_end].get("role") == "system":
        prefix_end += 1
    current_index = next(
        (
            index
            for index in range(len(messages) - 1, prefix_end - 1, -1)
            if messages[index].get("role") == "user"
        ),
        None,
    )
    current = (
        messages[current_index]
        if current_index is not None
        else {"role": "user", "content": user_message}
    )
    end = current_index if current_index is not None else len(messages)
    trailing = [
        message
        for message in messages[prefix_end:end]
        if message.get("role") == "system"
        or (
            isinstance(message.get("__deskpet_context"), dict)
            and message["__deskpet_context"].get("placement") == "control"
        )
    ]
    return messages[:prefix_end], trailing, current


def _wire_history_messages(
    messages: Any,
) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = []
    active_tool_groups: dict[str, str] = {}
    for raw in messages or ():
        role = str(raw.get("role", ""))
        message: dict[str, Any] = {
            "role": role,
            "content": raw.get("content", ""),
        }
        for key in ("tool_call_id", "reasoning_content", "name"):
            if raw.get(key) is not None:
                message[key] = raw.get(key)
        raw_calls = raw.get("tool_calls")
        if raw_calls:
            if isinstance(raw_calls, str):
                try:
                    raw_calls = json.loads(raw_calls)
                except json.JSONDecodeError:
                    raw_calls = []
            message["tool_calls"] = raw_calls
        message_id = int(raw.get("id", raw.get("message_id", 0)) or 0)
        causal_group_id = None
        if role == "assistant" and raw_calls:
            causal_group_id = f"session-history:{message_id}"
            for call in raw_calls if isinstance(raw_calls, list) else ():
                if isinstance(call, dict) and call.get("id"):
                    active_tool_groups[str(call["id"])] = causal_group_id
        elif role == "tool" and raw.get("tool_call_id"):
            causal_group_id = active_tool_groups.get(str(raw.get("tool_call_id")))
        wire.append(
            tag_message(
                message,
                ContextMessageMeta(
                    placement="transcript",
                    lifetime="history",
                    source="session_history_planner",
                    protected=False,
                    trim_policy=("page_in" if message_id == 0 else "summarize"),
                    causal_group_id=causal_group_id,
                    fragment_id=(
                        f"session-history:{message_id}" if message_id else None
                    ),
                    reason="gap-free SessionDB coverage",
                ),
            )
        )
    return wire


def _explicit_tool_name_candidates(user_message: str) -> tuple[str, ...]:
    """Extract exact tool-like identifiers without reading the catalog twice.

    The resolver still applies eligibility, deny policy and session visibility;
    unmatched ordinary words are therefore harmless candidates, not grants.
    """
    return tuple(dict.fromkeys(re.findall(
        r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]{2,127})(?![A-Za-z0-9_])",
        user_message or "",
    )))


def _schema_names(schemas: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for schema in schemas:
        function = schema.get("function", schema)
        if isinstance(function, dict) and function.get("name"):
            names.append(str(function["name"]))
    return names


def _render_prefix(
    bundle: ContextBundle,
    base_system: str,
    *,
    page_in_refs: Optional[dict[str, PageInRef]] = None,
) -> tuple[list[dict[str, Any]], list[ContextDecision], Optional[int], Optional[str]]:
    if not bundle.fragments:
        messages = bundle.build_messages(base_system)
        fingerprint = _fingerprint(messages) if messages else None
        return messages, [], len(messages) - 1 if messages else None, fingerprint
    lifetime_order = {"platform": 0, "stable": 1, "task": 2, "retrieved": 3}
    fragments = sorted(
        (fragment for fragment in bundle.fragments if fragment.placement == "prefix"),
        key=lambda fragment: (lifetime_order.get(fragment.lifetime, 9), -fragment.priority),
    )
    messages: list[dict[str, Any]] = []
    if base_system:
        messages.append({"role": "system", "content": base_system})
    decisions: list[ContextDecision] = []
    stable_boundary: Optional[int] = len(messages) - 1 if messages else None
    for fragment in fragments:
        ref = (page_in_refs or {}).get(fragment.fragment_id)
        content = fragment.content
        action = "loaded"
        reason = fragment.reason
        if ref is not None:
            content = (
                f"[Context page-in reference: id={ref.reference_id} "
                f"hash={ref.source_hash} kind={ref.kind} source={ref.source}. "
                "Use context_page_in to load the exact body.]"
            )
            action = "trimmed"
            reason = "over_window_page_in_reference"
        messages.append(tag_message(
            {"role": fragment.role, "content": content},
            ContextMessageMeta(
                placement=fragment.placement,
                lifetime=fragment.lifetime,
                source=fragment.source,
                protected=fragment.protected,
                trim_policy=fragment.trim_policy,
                fragment_id=fragment.fragment_id,
                priority=fragment.priority,
                reason=reason,
            ),
        ))
        decisions.append(ContextDecision(fragment.fragment_id, action, reason))
        if fragment.lifetime in {"platform", "stable"}:
            stable_boundary = len(messages) - 1
    stable_messages = (
        messages[: stable_boundary + 1] if stable_boundary is not None else []
    )
    return messages, decisions, stable_boundary, _fingerprint(stable_messages)


def _page_in_candidates(bundle: ContextBundle) -> list[tuple[Any, str, str, str]]:
    candidates: list[tuple[Any, str, str, str]] = []
    for fragment in bundle.fragments:
        if fragment.trim_policy != "page_in":
            continue
        meta = dict(fragment.meta or {})
        content = str(meta.get("page_in_content", fragment.content) or "")
        if not content:
            continue
        candidates.append((
            fragment,
            str(meta.get("page_in_kind", "memory_l3")),
            str(meta.get("page_in_source", fragment.source)),
            content,
        ))
    return candidates


def _bind_page_in_candidate(store: Any, candidate: tuple[Any, str, str, str], *,
                            eligibility: ToolEligibilityContext, scope_id: str) -> PageInRef:
    fragment, kind, source, content = candidate
    authority = store.put(
        kind=kind,
        source=source,
        content=content,
        session_id=eligibility.session_id,
        request_id=eligibility.request_id,
        scope_id=scope_id,
    )
    return PageInRef(
        reference_id=authority.reference_id,
        kind=authority.kind,
        source=authority.source,
        source_hash=authority.source_hash,
        fragment_id=fragment.fragment_id,
    )


def _fingerprint(messages: list[dict[str, Any]]) -> Optional[str]:
    if not messages:
        return None
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tool_summary(tool_set: Any, payload: PreparedToolPayload) -> dict[str, Any]:
    return {
        "direct_names": [cap.ref.name for cap in tool_set.direct],
        "activated_names": [cap.ref.name for cap in tool_set.activated],
        "schema_hashes": {
            cap.ref.name: cap.ref.schema_hash
            for cap in (*tool_set.direct, *tool_set.activated)
        },
        "selection_reasons": [
            {
                "name": decision.name,
                "disposition": decision.disposition,
                "reason": decision.reason,
            }
            for decision in tool_set.decisions
        ],
        "schema_tokens": payload.wire_tokens,
        "provider_adapter_id": "",
        "provider_adapter_version": "",
        "wire_payload_hash": "",
        "wire_tokens": 0,
        "attempt_id": "",
        "adapter_state": "canonical",
        "persisted_tool_scope_revision": tool_set.revision,
        "registry_revision": tool_set.registry_revision,
        "policy_fingerprint": tool_set.policy_fingerprint,
        "schema_fingerprint": tool_set.schema_fingerprint,
    }
