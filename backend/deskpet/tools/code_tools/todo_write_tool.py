# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""todo_write — replace the entire task list for the current session.

Idempotent full-list semantics: every call
overwrites the list. The LLM doesn't track diffs; it just keeps sending
the latest version of all items.

The tool persists to SessionDB's ``code_todos`` table (migration v11)
**and** broadcasts the new state to the control WebSocket so the
frontend's TodoListPanel updates live without polling.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..capabilities import ToolExecutionContext

log = logging.getLogger(__name__)


def build_todo_write_tool(
    session_db,
    code_session_id_resolver: Callable[[], str | None] | None = None,
    broadcaster: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    *,
    session_id_resolver: Callable[[], str | None] | None = None,
):
    """Construct the tool handler closure.

    Args:
        session_db: A ``SessionDB`` instance with the v11 ``code_todos``
            table available.
        session_id_resolver: Callable returning the current ordinary session
            id for legacy calls that do not carry ToolExecutionContext.
        code_session_id_resolver: Deprecated alias retained for frozen tests
            and old integrations.
        broadcaster: Async callable that ships ``{type, payload}`` JSON
            to subscribed control WebSockets. Pass None to skip broadcast
            (e.g. in tests).

    Returns:
        ``(handler, schema)`` — handler matches the standard
        ``(args, task_id) -> str`` shape; schema is the OpenAI function
        descriptor.
    """

    schema = {
        "name": "todo_write",
        "description": (
            "Replace the entire task list for the current task "
            "session. Provide all items every call (idempotent). Each "
            "item needs `content` (imperative form, e.g. 'Run tests'), "
            "`activeForm` (present-continuous, 'Running tests'), and "
            "`status` (pending|in_progress|completed). Exactly one item "
            "should be in_progress at a time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "activeForm": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                        "required": ["content", "activeForm", "status"],
                    },
                },
            },
            "required": ["items"],
        },
    }

    def _handler(
        args: dict[str, Any],
        task_id: str = "",
        *,
        execution_context: ToolExecutionContext | None = None,
    ) -> str:
        items = args.get("items")
        if not isinstance(items, list):
            return json.dumps({"error": "items (list) is required"})

        # Light validation — let the LLM see actionable errors instead
        # of silent drops.
        cleaned: list[dict[str, str]] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                return json.dumps({"error": f"items[{i}] must be object"})
            content = item.get("content")
            active_form = item.get("activeForm") or item.get("active_form")
            status = item.get("status", "pending")
            if not content or not isinstance(content, str):
                return json.dumps({"error": f"items[{i}].content is required"})
            if not active_form or not isinstance(active_form, str):
                # Tolerant fallback: derive from content (uppercase verb hint)
                active_form = content
            if status not in {"pending", "in_progress", "completed"}:
                status = "pending"
            cleaned.append(
                {"content": content, "activeForm": active_form, "status": status}
            )

        resolver = session_id_resolver or code_session_id_resolver
        sid = (
            execution_context.session_id
            if execution_context is not None
            else resolver()
            if resolver is not None
            else None
        )
        if not sid:
            return json.dumps(
                {"error": "session identity is unavailable"}
            )

        # SessionDB API is async; tool handlers are sync. Bridge with
        # ``asyncio.run`` if no loop is running, otherwise schedule on
        # the current loop. Most chat handlers run inside FastAPI's
        # async loop, so we use ``asyncio.run_coroutine_threadsafe``-like
        # pattern via a synchronous wrapper helper.
        async def _persist():
            await session_db.replace_session_todos(sid, cleaned)
            if broadcaster is not None:
                try:
                    await broadcaster(
                        {
                            "type": "session_todo_update",
                            "payload": {
                                "session_id": sid,
                                "items": cleaned,
                            },
                        }
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("todo_write broadcast failed: %s", e)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is None or not loop.is_running():
            asyncio.run(_persist())
        else:
            # We're inside an event loop. The tool dispatcher uses
            # run_in_executor to call sync tools, so the loop IS running
            # but we're on a worker thread without one of our own.
            # ``asyncio.run_coroutine_threadsafe`` against the captured
            # event loop is the supported bridge.
            fut = asyncio.run_coroutine_threadsafe(_persist(), loop)
            fut.result(timeout=10)

        return json.dumps(
            {
                "ok": True,
                "session_id": sid,
                "count": len(cleaned),
            }
        )

    return _handler, schema
