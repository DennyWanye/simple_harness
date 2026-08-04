# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S1 tasks 3.1 + 3.3 + 3.4 — SessionDB 单元测试。

覆盖：
  * initialize 幂等 + WAL 模式实际生效
  * create_session / append_message / get_messages 基本 CRUD
  * FTS5 trigger 同步（append → search_fts 命中；delete → 不命中）
  * FTS5 中文 + 英文同时 MATCH
  * update_salience 更新 salience 列 + decay_last_touch 可选跳过
  * tool_calls JSON 序列化 + 反序列化 roundtrip
  * session_id 过滤在 search_fts 生效

perf 测试（10K insert + concurrent write retry）在
``test_deskpet_session_db_perf.py``，默认 ``-m "not perf"`` 跳过。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from deskpet.memory.session_db import SessionDB
from memory.base import ConversationTurn, MemoryStore, StoredTurn


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    """一次性初始化好的 SessionDB，tmp_path 隔离。"""
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    yield session_db
    await session_db.close()


# ---- 3.1.a 生命周期 --------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_is_idempotent(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    await db.initialize()  # 第二次不应抛也不应重复迁移
    assert db._initialized is True
    await db.close()


@pytest.mark.asyncio
async def test_initialize_enables_wal_mode(tmp_path: Path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    # 直接 open 同步 conn 验证 journal_mode 已切到 wal
    conn = sqlite3.connect(tmp_path / "state.db")
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        conn.close()
    assert mode == "wal"
    await db.close()


# ---- 3.1.b CRUD ------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_returns_uuid(db: SessionDB):
    sid = await db.create_session({"origin": "pytest"})
    assert isinstance(sid, str)
    # UUID 标准形式 36 字符（含 4 个短横）
    assert len(sid) == 36 and sid.count("-") == 4


@pytest.mark.asyncio
async def test_ensure_session_persists_caller_supplied_uuid(db: SessionDB):
    sid = "11111111-1111-4111-8111-111111111111"
    stored = await db.ensure_session(sid, {"origin": "pytest"})
    stored_again = await db.ensure_session(sid, {"origin": "ignored"})
    assert stored == sid
    assert stored_again == sid
    async with aiosqlite.connect(db._db_path) as conn:
        cur = await conn.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (sid,))
        row = await cur.fetchone()
        await cur.close()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_context_usage_history_is_durable_ordered_and_idempotent(
    db: SessionDB,
):
    sid = await db.ensure_session("context-history-session")
    await db.record_context_usage_sample(
        {
            "sample_id": "provider-1",
            "session_id": sid,
            "run_id": "run-1",
            "event_type": "provider_attempt",
            "tokens_after": 8_000,
            "prompt_tokens": 8_000,
            "context_window": 128_000,
            "effective_ceiling": 115_200,
            "created_at": 10.0,
        }
    )
    await db.record_context_usage_sample(
        {
            "sample_id": "compaction-1",
            "session_id": sid,
            "run_id": "run-1",
            "event_type": "compaction",
            "tokens_before": 12_000,
            "tokens_after": 3_200,
            "prompt_tokens": 3_200,
            "context_window": 128_000,
            "effective_ceiling": 115_200,
            "metadata": {"reason": "threshold"},
            "created_at": 20.0,
        }
    )
    with pytest.raises(
        RuntimeError, match="context_usage_sample_conflict"
    ):
        await db.record_context_usage_sample(
            {
                "sample_id": "provider-1",
                "session_id": sid,
                "run_id": "run-1",
                "event_type": "provider_attempt",
                "tokens_after": 8_400,
                "prompt_tokens": 8_400,
                "context_window": 128_000,
                "effective_ceiling": 115_200,
                "created_at": 10.0,
            }
        )

    history = await db.list_context_usage_history(sid)

    assert [item["sample_id"] for item in history] == [
        "provider-1",
        "compaction-1",
    ]
    assert history[0]["tokens_after"] == 8_000
    assert history[1]["tokens_before"] == 12_000
    assert history[1]["tokens_after"] == 3_200
    assert history[1]["metadata"] == {"reason": "threshold"}


@pytest.mark.asyncio
async def test_code_session_registration_exposes_base_session_to_chat(db: SessionDB):
    await db.upsert_code_session(
        base_session_id="code-project-base",
        code_session_id="code-project-memory",
        project_root=r"F:\projects\deskpet",
        project_name="deskpet",
    )

    async with aiosqlite.connect(db._db_path) as conn:
        session = await (
            await conn.execute(
                "SELECT metadata FROM sessions WHERE id = ?",
                ("code-project-base",),
            )
        ).fetchone()
        delivery = await (
            await conn.execute(
                "SELECT epoch, deleted_at FROM session_delivery_state WHERE session_id = ?",
                ("code-project-base",),
            )
        ).fetchone()

    assert session is not None
    assert '"origin": "code_mode"' in session[0]
    assert delivery == (0, None)


@pytest.mark.asyncio
async def test_append_and_get_messages(db: SessionDB):
    sid = await db.create_session()
    id1 = await db.append_message(sid, "user", "hello world")
    id2 = await db.append_message(sid, "assistant", "hi there, python user")
    assert id2 > id1

    msgs = await db.get_messages(sid, limit=10)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello world"
    assert msgs[0]["id"] == id1
    # salience 默认 0.5
    assert msgs[0]["salience"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_get_messages_returns_stable_workflow_event_id(db: SessionDB):
    sid = await db.create_session()
    await db.append_message(
        sid,
        "assistant",
        "PPT进度：生成完整页面（8/12）",
        workflow_event_id="workflow-progress-event-1",
    )

    messages = await db.get_messages(sid)

    assert messages[0]["workflow_event_id"] == "workflow-progress-event-1"


@pytest.mark.asyncio
async def test_get_messages_exposes_derived_summary_metadata(db: SessionDB):
    sid = await db.create_session()
    message_id = await db.append_message(sid, "assistant", "legacy derived row")
    async with aiosqlite.connect(db._db_path) as conn:
        await conn.execute(
            "UPDATE messages SET is_summary = 1, summary_of = ? WHERE id = ?",
            ("1-4", message_id),
        )
        await conn.commit()

    messages = await db.get_messages(sid)

    assert messages[0]["is_summary"] is True
    assert messages[0]["summary_of"] == "1-4"


@pytest.mark.asyncio
async def test_get_recent_messages_selects_tail_and_restores_chronological_order(
    db: SessionDB,
):
    sid = await db.create_session()
    for index in range(8):
        await db.append_message(sid, "user", f"message-{index}")

    recent = await db.get_recent_messages(sid, limit=3)

    assert [row["content"] for row in recent] == [
        "message-5",
        "message-6",
        "message-7",
    ]
    # The legacy transcript API remains oldest-first pagination.
    oldest = await db.get_messages(sid, limit=3)
    assert [row["content"] for row in oldest] == [
        "message-0",
        "message-1",
        "message-2",
    ]


@pytest.mark.asyncio
async def test_get_recent_messages_does_not_orphan_tool_result_boundary(db: SessionDB):
    sid = await db.create_session()
    await db.append_message(sid, "user", "old")
    await db.append_message(
        sid,
        "assistant",
        "calling",
        tool_calls=[{"id": "call-1", "name": "lookup", "arguments": {}}],
    )
    await db.append_message(
        sid,
        "tool",
        '{"ok":true}',
        tool_call_id="call-1",
    )
    await db.append_message(sid, "assistant", "done")

    recent = await db.get_recent_messages(sid, limit=2)

    assert [row["role"] for row in recent] == ["assistant", "tool", "assistant"]
    assert recent[0]["tool_calls"]


@pytest.mark.asyncio
async def test_get_recent_messages_expands_large_tool_group_by_exact_call_id(
    db: SessionDB,
):
    sid = await db.create_session()
    calls = [
        {"id": f"call-{index}", "name": "lookup", "arguments": {}}
        for index in range(25)
    ]
    await db.append_message(sid, "assistant", "calling many", tool_calls=calls)
    for index in range(25):
        await db.append_message(
            sid,
            "tool",
            f'{{"ok":true,"index":{index}}}',
            tool_call_id=f"call-{index}",
        )
    await db.append_message(sid, "assistant", "done")

    recent = await db.get_recent_messages(sid, limit=2)

    assert recent[0]["role"] == "assistant"
    assert len(recent[0]["tool_calls"]) == 25
    assert recent[-1]["content"] == "done"
    assert len(recent) == 27


@pytest.mark.asyncio
async def test_memory_store_protocol_and_admin_surface(db: SessionDB):
    assert isinstance(db, MemoryStore)

    await db.append("proto-a", "user", "hello from protocol")
    await db.append("proto-a", "assistant", "reply from protocol")
    await db.append("proto-b", "user", "other session")

    recent = await db.get_recent("proto-a", limit=10)
    assert all(isinstance(turn, ConversationTurn) for turn in recent)
    assert [(turn.role, turn.content) for turn in recent] == [
        ("user", "hello from protocol"),
        ("assistant", "reply from protocol"),
    ]
    assert recent[0].created_at <= recent[1].created_at

    turns = await db.list_turns("proto-a")
    assert all(isinstance(turn, StoredTurn) for turn in turns)
    assert [turn.content for turn in turns] == [
        "hello from protocol",
        "reply from protocol",
    ]

    assert await db.delete_turn(turns[0].id) is True
    assert await db.delete_turn(turns[0].id) is False
    assert [turn.content for turn in await db.list_turns("proto-a")] == [
        "reply from protocol"
    ]

    sessions = await db.list_sessions()
    by_id = {session.session_id: session for session in sessions}
    assert by_id["proto-a"].turn_count == 1
    assert by_id["proto-b"].turn_count == 1

    await db.clear("proto-a")
    assert await db.get_recent("proto-a") == []
    assert await db.clear_all() == 1
    assert await db.list_turns(None) == []


@pytest.mark.asyncio
async def test_get_messages_pagination(db: SessionDB):
    sid = await db.create_session()
    for i in range(5):
        await db.append_message(sid, "user", f"msg-{i}")
    first_two = await db.get_messages(sid, limit=2, offset=0)
    last_three = await db.get_messages(sid, limit=10, offset=2)
    assert [m["content"] for m in first_two] == ["msg-0", "msg-1"]
    assert [m["content"] for m in last_three] == ["msg-2", "msg-3", "msg-4"]


# ---- 3.4 FTS5 triggers ----------------------------------------------


@pytest.mark.asyncio
async def test_fts5_insert_indexed_immediately(db: SessionDB):
    sid = await db.create_session()
    await db.append_message(sid, "user", "hello world python")
    await db.append_message(sid, "user", "completely unrelated content")
    hits = await db.search_fts("python")
    assert len(hits) == 1
    assert hits[0]["content"] == "hello world python"
    # rank 列应存在（FTS5 built-in）
    assert "rank" in hits[0]


@pytest.mark.asyncio
async def test_fts5_delete_removes_from_index(db: SessionDB, tmp_path: Path):
    sid = await db.create_session()
    msg_id = await db.append_message(sid, "user", "transient content xyzzy")
    hits = await db.search_fts("xyzzy")
    assert len(hits) == 1

    # 直接用 sqlite3 DELETE（触发器应同步移除 fts 索引项）
    conn = sqlite3.connect(tmp_path / "state.db")
    try:
        conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        conn.commit()
    finally:
        conn.close()

    hits_after = await db.search_fts("xyzzy")
    assert hits_after == []


@pytest.mark.asyncio
async def test_fts5_update_re_indexes(db: SessionDB, tmp_path: Path):
    sid = await db.create_session()
    msg_id = await db.append_message(sid, "user", "original term alpha")
    assert len(await db.search_fts("alpha")) == 1

    # 改内容：触发 messages_au trigger
    conn = sqlite3.connect(tmp_path / "state.db")
    try:
        conn.execute(
            "UPDATE messages SET content = ? WHERE id = ?",
            ("replaced text beta", msg_id),
        )
        conn.commit()
    finally:
        conn.close()

    assert await db.search_fts("alpha") == []
    hits = await db.search_fts("beta")
    assert len(hits) == 1
    assert hits[0]["content"] == "replaced text beta"


@pytest.mark.asyncio
async def test_fts5_chinese_and_english(db: SessionDB):
    """FTS5 trigram 分词：≥3 字符的中英文子串都能 MATCH。

    spec 要求 "一起学 python" → search_fts("python") 能命中；
    trigram 额外让"纯中文"这类无空格中文也能子串召回。
    """
    sid = await db.create_session()
    await db.append_message(sid, "user", "一起学 python")
    await db.append_message(sid, "user", "我喜欢 go 语言")
    await db.append_message(sid, "user", "纯中文的句子")

    py_hits = await db.search_fts("python")
    assert len(py_hits) == 1
    assert "python" in py_hits[0]["content"]

    # trigram 需要 ≥3 字符才能匹配（这是 trigram 的本质约束，≤2 字符
    # 无 3-gram 单元可比较）。agent 实际 query 长度几乎都 ≥3。
    zh_hits = await db.search_fts("中文的")
    assert len(zh_hits) == 1
    assert zh_hits[0]["content"] == "纯中文的句子"

    # 3 字符英文子串也应命中
    prefix_hits = await db.search_fts("pyt")
    assert any("python" in h["content"] for h in prefix_hits)


@pytest.mark.asyncio
async def test_search_fts_filters_by_session_id(db: SessionDB):
    sid_a = await db.create_session()
    sid_b = await db.create_session()
    await db.append_message(sid_a, "user", "alpha session one")
    await db.append_message(sid_b, "user", "alpha session two")

    all_hits = await db.search_fts("alpha")
    assert len(all_hits) == 2

    only_a = await db.search_fts("alpha", session_id=sid_a)
    assert len(only_a) == 1
    assert only_a[0]["session_id"] == sid_a


# ---- 3.1.c salience 更新 --------------------------------------------


@pytest.mark.asyncio
async def test_update_salience_touches_timestamp(db: SessionDB):
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "salience target")

    before = (await db.get_messages(sid))[0]
    assert before["decay_last_touch"] is None
    assert before["salience"] == pytest.approx(0.5)

    await db.update_salience(mid, 0.55, touch=True)
    after = (await db.get_messages(sid))[0]
    assert after["salience"] == pytest.approx(0.55)
    assert after["decay_last_touch"] is not None


@pytest.mark.asyncio
async def test_update_salience_without_touch(db: SessionDB):
    sid = await db.create_session()
    mid = await db.append_message(sid, "user", "salience no-touch")
    # 先 touch 一次留下时间戳
    await db.update_salience(mid, 0.6, touch=True)
    first_touch = (await db.get_messages(sid))[0]["decay_last_touch"]

    await db.update_salience(mid, 0.7, touch=False)
    row = (await db.get_messages(sid))[0]
    assert row["salience"] == pytest.approx(0.7)
    # touch=False 不应改 decay_last_touch
    assert row["decay_last_touch"] == first_touch


# ---- 3.1.d tool_calls JSON roundtrip --------------------------------


@pytest.mark.asyncio
async def test_tool_calls_json_roundtrip(db: SessionDB):
    sid = await db.create_session()
    calls = [
        {"id": "call_1", "type": "function", "function": {"name": "web_fetch", "arguments": "{}"}},
    ]
    mid = await db.append_message(
        sid, "assistant", "thinking...", tool_calls=calls
    )
    msgs = await db.get_messages(sid)
    assert len(msgs) == 1
    stored = msgs[0]["tool_calls"]
    assert isinstance(stored, list)
    assert stored[0]["id"] == "call_1"
    assert stored[0]["function"]["name"] == "web_fetch"
    assert msgs[0]["id"] == mid


@pytest.mark.asyncio
async def test_tool_call_id_persists_for_tool_role(db: SessionDB):
    sid = await db.create_session()
    await db.append_message(
        sid, "tool", "{\"ok\":true}", tool_call_id="call_xyz"
    )
    msgs = await db.get_messages(sid)
    assert msgs[0]["tool_call_id"] == "call_xyz"
    assert msgs[0]["role"] == "tool"


# ---- busy retry helper (unit-level，不强依赖并发) -------------------


@pytest.mark.asyncio
async def test_busy_retry_helper_classifies_errors(tmp_path: Path):
    """验证 _is_busy_error 的分类逻辑 —— 正向/反向样本各一。"""
    from deskpet.memory.session_db import _is_busy_error

    busy = sqlite3.OperationalError("database is locked")
    other = sqlite3.OperationalError("no such table: ghost")
    not_op = ValueError("unrelated")

    assert _is_busy_error(busy) is True
    assert _is_busy_error(other) is False
    assert _is_busy_error(not_op) is False


# ── 自定义会话标题（消息面板「重命名话题」）─────────────────────────


@pytest.mark.asyncio
async def test_session_title_set_and_listed(db: SessionDB):
    await db.append("task-1", "user", "hello")
    stored = await db.set_session_title("task-1", "我的话题")
    assert stored == "我的话题"
    rows = await db.list_sessions_with_preview()
    row = next(r for r in rows if r["session_id"] == "task-1")
    assert row["title"] == "我的话题"
    # 自定义标题不影响 preview（仍由 messages 派生）。
    assert row["preview"] == "hello"


@pytest.mark.asyncio
async def test_session_title_empty_clears(db: SessionDB):
    await db.append("task-2", "user", "hi")
    await db.set_session_title("task-2", "named")
    cleared = await db.set_session_title("task-2", "   ")
    assert cleared == ""
    rows = await db.list_sessions_with_preview()
    row = next(r for r in rows if r["session_id"] == "task-2")
    assert row["title"] == ""


@pytest.mark.asyncio
async def test_session_title_trimmed_and_clamped(db: SessionDB):
    await db.append("task-3", "user", "x")
    stored = await db.set_session_title("task-3", "  " + "a" * 200 + "  ")
    assert stored == "a" * 80  # trim + clamp 到 MAX_TITLE_LEN
    rows = await db.list_sessions_with_preview()
    row = next(r for r in rows if r["session_id"] == "task-3")
    assert row["title"] == "a" * 80


@pytest.mark.asyncio
async def test_delete_session_clears_custom_title(db: SessionDB):
    await db.append("task-4", "user", "msg")
    await db.set_session_title("task-4", "to be deleted")
    await db.clear("task-4")
    # 重建同名会话 → 不能继承旧标题（删除时已连带清掉）。
    await db.append("task-4", "user", "fresh")
    rows = await db.list_sessions_with_preview()
    row = next(r for r in rows if r["session_id"] == "task-4")
    assert row["title"] == ""


@pytest.mark.asyncio
async def test_set_session_title_blank_sid_is_noop(db: SessionDB):
    assert await db.set_session_title("", "x") == ""
    assert await db.set_session_title("   ", "x") == ""


@pytest.mark.asyncio
async def test_session_title_upsert_overwrites(db: SessionDB):
    await db.append("task-5", "user", "m")
    await db.set_session_title("task-5", "first")
    await db.set_session_title("task-5", "second")
    rows = await db.list_sessions_with_preview()
    row = next(r for r in rows if r["session_id"] == "task-5")
    assert row["title"] == "second"
