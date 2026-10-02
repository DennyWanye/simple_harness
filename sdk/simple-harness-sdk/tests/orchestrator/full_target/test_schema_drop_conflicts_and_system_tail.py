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


def test_a_version_31_library_opens_and_loses_exactly_those_tables(tmp_path) -> None:
    """A version-31 library, rebuilt from a current one: the old migrations' own text
    creates the tables again and migration 32 is not yet recorded.  Migration 2 also
    alters ``claims``, so only its conflict statements are replayed."""

    path = tmp_path / "orchestrator.db"
    Store.open(path).close()
    v2 = schema.MIGRATIONS[1].ddl
    conflicts = v2[v2.index("CREATE TABLE conflicts") : v2.index("ALTER TABLE claims")]
    connection = sqlite3.connect(path)
    connection.executescript(conflicts)  # migration 2, its conflict table and index
    connection.executescript(schema.MIGRATIONS[2].ddl)  # migration 3, unchanged text
    connection.executescript(schema.MIGRATIONS[14].ddl)  # migration 15, unchanged text
    connection.execute("DELETE FROM orch_schema_migrations WHERE version = 32")
    connection.commit()
    connection.close()
    assert [schema.MIGRATIONS[i].version for i in (1, 2, 14)] == [2, 3, 15]
    before = _tables(path)
    assert DROPPED <= before

    Store.open(path).close()

    assert _tables(path) == before - DROPPED
    assert schema.SCHEMA_VERSION == 32
    assert (tmp_path / "orchestrator.db.pre-schema-32.backup").is_file()
