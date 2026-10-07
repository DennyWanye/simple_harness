# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Migration 32 (2026-10-02): the conflict table, the graph change ledger and the two
Mission system-pool tables are dropped.  Their creating migrations (2, 3 and 15) keep
their original text, because an existing library is opened by comparing every recorded
migration's checksum — so a library at version 31 must open, upgrade, and lose exactly
those tables."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_orchestrator.storage import schema
from agent_orchestrator.storage.store import Store

DROPPED = {"conflicts", "graph_changes", "mission_system_tail_pools", "mission_system_tail_tasks"}


def _tables(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        connection.close()


def test_a_new_library_has_none_of_the_dropped_tables(tmp_path) -> None:
    Store.open(tmp_path / "orchestrator.db").close()
    tables = _tables(tmp_path / "orchestrator.db")
    assert not tables & DROPPED
    assert {"missions", "tasks", "claims", "knowledge", "budget_tail_holds"} <= tables  # kept


def test_a_version_31_library_opens_and_loses_exactly_those_tables(tmp_path, monkeypatch) -> None:
    """A real version-31 library: migrations 1..31 only, so the old migrations' own text
    created the tables and migration 32 is not yet recorded.

    2026-10-08 改造库方式：原先在当前库上重放旧迁移再删 32 以后的记录，迁移 38（f64e8adbe 删金额列）
    与迁移 40（0f8d8d9fd 建全库做法表）不能在已跑过它们的库上重放。改与迁移 33 的测试同法：
    只跑前 31 条迁移造一个真的第 31 版库。先只升到下一版，断言这一条迁移删掉的恰好是这几张表；再升到当前版，断言迁移链走得通。"""

    path = tmp_path / "orchestrator.db"
    assert schema.MIGRATIONS[30].version == 31
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:31])
        Store.open(path).close()
    before = _tables(path)
    assert DROPPED <= before

    # 只升到下一版：这一条迁移删掉的恰好是这几张表，不多不少（2026-10-08）。
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:32])
        Store.open(path).close()
    assert _tables(path) == before - DROPPED
    assert (tmp_path / "orchestrator.db.pre-schema-32.backup").is_file()

    # 再按当前全部迁移打开：迁移链走得通，删掉的表不会回来。
    Store.open(path).close()
    assert not _tables(path) & DROPPED
    assert schema.SCHEMA_VERSION >= 32
