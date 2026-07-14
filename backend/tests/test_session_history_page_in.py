from __future__ import annotations

import json
import sqlite3

import pytest
import pytest_asyncio

from deskpet.memory.context_segment_store import (
    ContextSegmentStore,
    recover_broken_causal_messages,
)
from deskpet.memory.migrator import run_migrations
from deskpet.memory.session_db import SessionDB
from deskpet.tools.session_history_tools import (
    SESSION_HISTORY_PAGE_IN_SCHEMA,
    build_session_history_page_in_handler,
    register_session_history_page_in,
)


def _one_per_message(messages):
    return len(messages)


@pytest_asyncio.fixture
async def runtime(tmp_path):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    session_db = SessionDB(db_path)
    await session_db.initialize()
    segment_store = ContextSegmentStore(db_path)
    return db_path, session_db, segment_store


@pytest.mark.asyncio
async def test_page_in_returns_complete_tool_group(runtime):
    _, session_db, segment_store = runtime
    await session_db.append_message(
        "s1",
        "assistant",
        "calling",
        tool_calls=[
            {"id": "a", "type": "function", "function": {"name": "read_a"}},
            {"id": "b", "type": "function", "function": {"name": "read_b"}},
        ],
    )
    await session_db.append_message("s1", "tool", "A", tool_call_id="a")
    await session_db.append_message("s1", "tool", "B", tool_call_id="b")
    messages = await session_db.get_messages("s1", limit=50)
    segment = (
        await segment_store.replace_raw_index(
            "s1", messages, max_messages=2, token_estimator=_one_per_message
        )
    )[0]
    handler = build_session_history_page_in_handler(
        segment_store,
        session_db,
        session_id_getter=lambda: "s1",
        budget_getter=lambda: 3,
        token_estimator=_one_per_message,
    )

    result = json.loads(await handler({"segment_id": segment.segment_id}, "task"))

    assert result["ok"] is True
    assert result["message_count"] == 3
    assert [item["role"] for item in result["messages"]] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert result["next_cursor"] is None


@pytest.mark.asyncio
async def test_page_in_is_bound_to_runtime_session(runtime):
    _, session_db, segment_store = runtime
    await session_db.append_message("s1", "user", "secret")
    segment = (
        await segment_store.replace_raw_index(
            "s1", await session_db.get_messages("s1", limit=50)
        )
    )[0]
    handler = build_session_history_page_in_handler(
        segment_store,
        session_db,
        session_id_getter=lambda: "s2",
        budget_getter=lambda: 100,
    )
    result = json.loads(await handler({"segment_id": segment.segment_id}, "task"))
    assert result == {"ok": False, "error": "segment_scope_denied", "retriable": False}


@pytest.mark.asyncio
async def test_page_in_detects_source_hash_drift_and_marks_stale(runtime):
    db_path, session_db, segment_store = runtime
    message_id = await session_db.append_message("s1", "user", "original")
    segment = (
        await segment_store.replace_raw_index(
            "s1", await session_db.get_messages("s1", limit=50)
        )
    )[0]
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE messages SET content='edited' WHERE id=?", (message_id,))
        conn.commit()
    handler = build_session_history_page_in_handler(
        segment_store,
        session_db,
        session_id_getter=lambda: "s1",
        budget_getter=lambda: 100,
    )

    result = json.loads(await handler({"segment_id": segment.segment_id}, "task"))

    assert result["error"] == "segment_stale"
    assert (await segment_store.get(segment.segment_id)).status == "stale"


@pytest.mark.asyncio
async def test_page_in_cursor_pages_only_at_group_boundaries(runtime):
    _, session_db, segment_store = runtime
    await session_db.append_message("s1", "user", "one")
    await session_db.append_message("s1", "assistant", "two")
    messages = await session_db.get_messages("s1", limit=50)
    segment = (await segment_store.replace_raw_index("s1", messages))[0]
    handler = build_session_history_page_in_handler(
        segment_store,
        session_db,
        session_id_getter=lambda: "s1",
        budget_getter=lambda: 1,
        token_estimator=_one_per_message,
    )

    first = json.loads(await handler({"segment_id": segment.segment_id}, "task"))
    second = json.loads(
        await handler(
            {"segment_id": segment.segment_id, "cursor": first["next_cursor"]},
            "task",
        )
    )
    assert [item["content"] for item in first["messages"]] == ["one"]
    assert [item["content"] for item in second["messages"]] == ["two"]
    assert second["next_cursor"] is None


@pytest.mark.asyncio
async def test_page_in_replays_recovered_projection_for_incomplete_tool_history(runtime):
    _, session_db, segment_store = runtime
    await session_db.append_message(
        "s1",
        "assistant",
        "",
        tool_calls=[
            {
                "id": "missing-call",
                "type": "function",
                "function": {"name": "web_search", "arguments": "{}"},
            }
        ],
    )
    await session_db.append_message("s1", "assistant", "stream interrupted")
    await session_db.append_message(
        "s1", "tool", "detached result", tool_call_id="orphan-call"
    )
    raw = await session_db.get_messages("s1", limit=50)
    recovered, errors = recover_broken_causal_messages(raw)
    assert errors
    segment = (
        await segment_store.replace_raw_index("s1", recovered, max_messages=10)
    )[0]
    handler = build_session_history_page_in_handler(
        segment_store,
        session_db,
        session_id_getter=lambda: "s1",
        budget_getter=lambda: 10_000,
    )

    result = json.loads(await handler({"segment_id": segment.segment_id}, "task"))

    assert result["ok"] is True
    assert [message["id"] for message in result["messages"]] == [1, 2, 3]
    assert all(message["role"] == "assistant" for message in result["messages"])
    assert all(not message.get("tool_calls") for message in result["messages"])
    assert all(not message.get("tool_call_id") for message in result["messages"])
    assert "detached result" not in json.dumps(result, ensure_ascii=False)


def test_tool_schema_has_no_session_parameter_and_registers_conditionally(runtime=None):
    properties = SESSION_HISTORY_PAGE_IN_SCHEMA["parameters"]["properties"]
    assert "session_id" not in properties

    class Registry:
        def __init__(self):
            self.kwargs = None

        def register(self, **kwargs):
            self.kwargs = kwargs

    registry = Registry()
    # Registration wiring itself is dependency-injected; production main
    # connects it only when the summary path finalizes the conditional tool.
    register_session_history_page_in(
        registry,
        object(),
        object(),
        session_id_getter=lambda: "s1",
        budget_getter=lambda: 100,
    )
    assert registry.kwargs["name"] == "session_history_page_in"
