# SPDX-License-Identifier: BUSL-1.1
"""WI-1.1 — session_goals DDL + SessionDB thin-method round-trip."""
from __future__ import annotations

import aiosqlite
import pytest

from deskpet.memory.memory_v2_schema import (
    ensure_memory_v2_tables,
    _reset_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_schema_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


@pytest.mark.asyncio
async def test_session_goals_table_created_with_frozen_columns(tmp_path):
    db = str(tmp_path / "state.db")
    await ensure_memory_v2_tables(db)
    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute("PRAGMA table_info(session_goals)")
        cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "goal_id", "session_id", "text", "status", "progress",
        "criteria", "max_iterations", "iterations_used",
        "set_at", "updated_at",
    }


@pytest.mark.asyncio
async def test_ensure_tables_idempotent(tmp_path):
    db = str(tmp_path / "state.db")
    await ensure_memory_v2_tables(db)
    _reset_cache_for_tests()
    await ensure_memory_v2_tables(db)
