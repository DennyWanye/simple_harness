# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Exact, session-bound page-in tool for Context OS coverage references."""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Mapping, Sequence

from deskpet.memory.context_segment_store import (
    ContextSegmentStore,
    canonical_message_hash,
    conservative_token_estimate,
    eligible_session_messages,
    group_causal_messages,
    recover_broken_causal_messages,
)
from deskpet.tools.public_projection import (
    project_public_tool_arguments,
    project_public_tool_result,
)


SESSION_HISTORY_PAGE_IN_SCHEMA: dict[str, Any] = {
    "name": "session_history_page_in",
    "description": (
        "Load exact original messages for one summary reference in the current "
        "session. The session is supplied by the host and cannot be overridden."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "segment_id": {"type": "string", "minLength": 1},
            "cursor": {"type": "integer", "minimum": 0},
        },
        "required": ["segment_id"],
        "additionalProperties": False,
    },
}

RUN_DETAILS_INSPECT_SCHEMA: dict[str, Any] = {
    "name": "run_details_inspect",
    "description": (
        "Read durable public execution summaries and tool activity for one Run "
        "in the current session. This is an explicit page-in operation: the "
        "records are not part of normal conversation context, and hidden model "
        "chain-of-thought is never returned."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
}


async def _load_all(session_db: object, session_id: str, *, page_size: int = 500):
    getter = getattr(session_db, "get_messages", None)
    if not callable(getter):
        raise RuntimeError("SessionDB does not provide get_messages")
    rows: list[Mapping[str, Any]] = []
    offset = 0
    while True:
        page = await getter(session_id, limit=page_size, offset=offset)
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)


def build_session_history_page_in_handler(
    segment_store: ContextSegmentStore,
    session_db: object,
    *,
    session_id_getter: Callable[[], Any],
    budget_getter: Callable[[], Any],
    token_estimator: Callable[[Sequence[Mapping[str, Any]]], int] | None = None,
) -> Callable[[dict[str, Any], str], Any]:
    estimate = token_estimator or conservative_token_estimate

    async def handle(args: dict[str, Any], task_id: str) -> str:  # noqa: ARG001
        runtime = session_id_getter()
        if inspect.isawaitable(runtime):
            runtime = await runtime
        session_id = (
            str(getattr(runtime, "session_id", "") or "")
            if not isinstance(runtime, str)
            else runtime
        ).strip()
        if not session_id:
            return _error("session_scope_missing")
        segment_id = str(args.get("segment_id", "") or "").strip()
        if not segment_id:
            return _error("segment_id_required")
        try:
            cursor = int(args.get("cursor", 0) or 0)
        except (TypeError, ValueError):
            return _error("invalid_cursor")
        if cursor < 0:
            return _error("invalid_cursor")

        segment = await segment_store.get(segment_id)
        if segment is None:
            return _error("segment_not_found")
        if segment.session_id != session_id:
            return _error("segment_scope_denied")
        if segment.status != "valid":
            return _error("segment_stale")

        rows = await _load_all(session_db, session_id)
        raw_messages = [
            message
            for message in eligible_session_messages(rows)
            if segment.first_message_id
            <= int(message.get("id", message.get("message_id", 0)) or 0)
            <= segment.last_message_id
        ]
        messages, _recovery_errors = recover_broken_causal_messages(raw_messages)
        if (
            len(messages) != segment.message_count
            or not messages
            or canonical_message_hash(messages) != segment.source_hash
        ):
            try:
                await segment_store.mark_stale(
                    segment.segment_id,
                    expected_revision=segment.revision,
                )
            except Exception:
                pass
            return _error("segment_stale")

        groups, errors = group_causal_messages(messages)
        if errors:
            return _error("segment_causal_group_invalid")
        if cursor > len(groups):
            return _error("cursor_out_of_range")
        budget_value = budget_getter()
        if inspect.isawaitable(budget_value):
            budget_value = await budget_value
        budget = max(0, int(budget_value or 0))
        selected = []
        used = 0
        next_cursor = cursor
        for group in groups[cursor:]:
            group_cost = int(estimate(group.messages))
            if group_cost > budget and not selected:
                return _error("page_in_group_exceeds_budget")
            if used + group_cost > budget:
                break
            selected.append(group)
            used += group_cost
            next_cursor += 1
        flattened = [message for group in selected for message in group.messages]
        return json.dumps(
            {
                "ok": True,
                "segment_id": segment.segment_id,
                "source_hash": segment.source_hash,
                "cursor": cursor,
                "next_cursor": next_cursor if next_cursor < len(groups) else None,
                "messages": flattened,
                "message_count": len(flattened),
                "estimated_tokens": used,
            },
            ensure_ascii=False,
        )

    return handle


def register_session_history_page_in(
    registry: object,
    segment_store: ContextSegmentStore,
    session_db: object,
    *,
    session_id_getter: Callable[[], Any],
    budget_getter: Callable[[], Any],
    token_estimator: Callable[[Sequence[Mapping[str, Any]]], int] | None = None,
) -> None:
    register = getattr(registry, "register", None)
    if not callable(register):
        raise TypeError("registry does not support register")
    register(
        name="session_history_page_in",
        toolset="memory",
        schema=SESSION_HISTORY_PAGE_IN_SCHEMA,
        handler=build_session_history_page_in_handler(
            segment_store,
            session_db,
            session_id_getter=session_id_getter,
            budget_getter=budget_getter,
            token_estimator=token_estimator,
        ),
        permission_category="read_file",
        source="builtin",
        concurrency_safe=True,
    )


def build_run_details_inspect_handler(
    session_db: object,
    *,
    session_id_getter: Callable[[], Any],
) -> Callable[[dict[str, Any], str], Any]:
    async def handle(args: dict[str, Any], task_id: str) -> str:  # noqa: ARG001
        runtime = session_id_getter()
        if inspect.isawaitable(runtime):
            runtime = await runtime
        session_id = (
            str(getattr(runtime, "session_id", "") or "")
            if not isinstance(runtime, str)
            else runtime
        ).strip()
        if not session_id:
            return _error("session_scope_missing")
        run_id = str(args.get("run_id", "") or "").strip()
        if not run_id:
            return _error("run_id_required")
        try:
            limit = max(1, min(200, int(args.get("limit", 100) or 100)))
        except (TypeError, ValueError):
            return _error("invalid_limit")
        getter = getattr(session_db, "get_messages", None)
        if not callable(getter):
            return _error("session_history_unavailable")
        rows = await getter(
            session_id,
            limit=limit,
            root_run_id=run_id,
        )
        if not rows:
            return _error("run_not_found")

        call_names: dict[str, str] = {}
        summaries: list[dict[str, Any]] = []
        tool_events: list[dict[str, Any]] = []
        final_response = ""
        for row in rows:
            event_id = str(row.get("workflow_event_id") or "")
            if (
                row.get("projection_kind") == "workflow_progress"
                and event_id.startswith("reasoning-summary:")
            ):
                summaries.append(
                    {
                        "summary_id": event_id,
                        "text": str(row.get("content") or ""),
                        "created_at": row.get("created_at"),
                    }
                )
                continue
            calls = row.get("tool_calls")
            if isinstance(calls, str):
                try:
                    calls = json.loads(calls)
                except (TypeError, ValueError, json.JSONDecodeError):
                    calls = None
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, Mapping):
                        continue
                    function = call.get("function")
                    function = function if isinstance(function, Mapping) else call
                    call_id = str(call.get("id") or "")
                    name = str(function.get("name") or "")
                    raw_arguments = function.get("arguments")
                    if isinstance(raw_arguments, str):
                        try:
                            raw_arguments = json.loads(raw_arguments)
                        except (TypeError, ValueError, json.JSONDecodeError):
                            raw_arguments = {}
                    arguments = (
                        dict(raw_arguments)
                        if isinstance(raw_arguments, Mapping)
                        else {}
                    )
                    if call_id:
                        call_names[call_id] = name
                    tool_events.append(
                        {
                            "kind": "call",
                            "call_id": call_id,
                            "tool_name": name,
                            "arguments": project_public_tool_arguments(
                                name, arguments
                            ),
                            "created_at": row.get("created_at"),
                        }
                    )
            if row.get("role") == "tool":
                call_id = str(row.get("tool_call_id") or "")
                name = call_names.get(call_id, "")
                raw_result: Any = row.get("content")
                if isinstance(raw_result, str):
                    try:
                        raw_result = json.loads(raw_result)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                tool_events.append(
                    {
                        "kind": "result",
                        "call_id": call_id,
                        "tool_name": name,
                        "result": project_public_tool_result(name, raw_result),
                        "created_at": row.get("created_at"),
                    }
                )
            elif (
                row.get("role") == "assistant"
                and not calls
                and row.get("context_visibility") == "conversation"
            ):
                final_response = str(row.get("content") or final_response)

        return json.dumps(
            {
                "ok": True,
                "run_id": run_id,
                "reasoning_summaries": summaries,
                "tool_events": tool_events,
                "final_response": final_response,
                "records_returned": len(rows),
                "truncated": len(rows) >= limit,
            },
            ensure_ascii=False,
        )

    return handle


def register_run_details_inspect(
    registry: object,
    session_db: object,
    *,
    session_id_getter: Callable[[], Any],
) -> None:
    register = getattr(registry, "register", None)
    if not callable(register):
        raise TypeError("registry does not support register")
    register(
        name="run_details_inspect",
        toolset="memory",
        schema=RUN_DETAILS_INSPECT_SCHEMA,
        handler=build_run_details_inspect_handler(
            session_db,
            session_id_getter=session_id_getter,
        ),
        permission_category="read_file",
        source="builtin",
        concurrency_safe=True,
    )


def _error(code: str) -> str:
    return json.dumps(
        {"ok": False, "error": code, "retriable": code in {"segment_stale"}},
        ensure_ascii=False,
    )


__all__ = [
    "RUN_DETAILS_INSPECT_SCHEMA",
    "SESSION_HISTORY_PAGE_IN_SCHEMA",
    "build_run_details_inspect_handler",
    "build_session_history_page_in_handler",
    "register_run_details_inspect",
    "register_session_history_page_in",
]
