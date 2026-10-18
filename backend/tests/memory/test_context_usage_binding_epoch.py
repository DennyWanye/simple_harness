"""上下文用量：样本自带的绑定纪元比状态新时，状态跟到样本纪元再计入实测。

2026-10-18 真机：主对话每轮一个执行会话，没有会话绑定记录，状态按纪元 0 建立；
运行绑定的纪元是 1，实测样本一律被当作旧纪元丢掉，用量环永远显示"尚无真实模型请求"。
"""

from __future__ import annotations

import time

import aiosqlite
import pytest
from deskpet.memory.session_db import SessionDB


def _sample(session_id: str, event_id: str, *, epoch: int, tokens: int) -> dict:
    now = time.time()
    return {
        "session_id": session_id,
        "source_event_id": event_id,
        "run_id": "run-1",
        "request_id": "req-1",
        "attempt_id": event_id,
        "event_type": "provider_attempt",
        "tokens_after": tokens,
        "prompt_tokens": tokens,
        "completion_tokens": 9,
        "cached_tokens": 0,
        "context_window": 1_000_000,
        "effective_ceiling": 1_000_000,
        "estimate_method": "provider_usage",
        "provider_id": "primary",
        "model_id": "deepseek-v4.1-flash",
        "binding_epoch": epoch,
        "metadata": {},
        "completed_at": now,
        "created_at": now,
    }


@pytest.mark.asyncio
async def test_sample_from_a_newer_binding_is_measured(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    sid = "foreground-execution-a"
    before = await db.get_context_usage_state(sid)  # 读一次就按纪元 0 落库
    assert before["binding_epoch"] == 0 and not before["has_measurement"]

    await db.record_context_usage_sample(_sample(sid, "ev-1", epoch=1, tokens=6678))

    state = await db.get_context_usage_state(sid)
    assert state["has_measurement"] and state["binding_epoch"] == 1
    assert state["prompt_tokens"] == 6678 and state["model_id"] == "deepseek-v4.1-flash"


@pytest.mark.asyncio
async def test_rebuild_from_history_also_measures_newer_binding(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(path)
    await db.initialize()
    sid = "foreground-execution-b"
    await db.record_context_usage_sample(_sample(sid, "ev-1", epoch=1, tokens=500))
    async with aiosqlite.connect(path) as raw:
        await raw.execute("DELETE FROM session_context_usage_state_v2 WHERE session_id=?", (sid,))
        await raw.commit()

    state = await db.get_context_usage_state(sid)
    assert (
        state["has_measurement"] and state["prompt_tokens"] == 500 and state["binding_epoch"] == 1
    )


@pytest.mark.asyncio
async def test_sample_from_an_older_binding_stays_out(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    await db.initialize()
    sid = "code-session"
    await db.set_context_usage_binding_state(
        sid, binding_epoch=3, provider_id="primary", model_id="deepseek-v4.1-flash"
    )

    await db.record_context_usage_sample(_sample(sid, "ev-old", epoch=2, tokens=999))

    state = await db.get_context_usage_state(sid)
    assert state["binding_epoch"] == 3 and not state["has_measurement"]
