# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Migration 31 (2026-10-02): the candidate-comparison and fragment-validation tables
are dropped.  Their creating migrations (12 and 14) keep their original text, because
an existing library is opened by comparing every recorded migration's checksum — so a
library at version 30 must open, upgrade, and lose exactly those tables."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_orchestrator.storage import schema
from agent_orchestrator.storage.store import Store

DROPPED = {"selection_candidates", "selection_rounds", "search_bindings", "fragment_validations"}


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
    store = Store.open(tmp_path / "orchestrator.db")
    store.close()
    tables = _tables(tmp_path / "orchestrator.db")
    assert not tables & DROPPED
    assert {"missions", "tasks", "claims", "policy_versions"} <= tables  # kept


def test_a_version_30_library_opens_and_loses_exactly_those_tables(tmp_path) -> None:
    """A version-30 library, rebuilt from a current one: the two old migrations' own
    text creates the tables again and migrations 31 and later are not yet recorded."""

    path = tmp_path / "orchestrator.db"
    Store.open(path).close()
    connection = sqlite3.connect(path)
    connection.executescript(schema.MIGRATIONS[11].ddl)  # migration 12, unchanged text
    connection.executescript(schema.MIGRATIONS[13].ddl)  # migration 14, unchanged text
    connection.execute("DELETE FROM orch_schema_migrations WHERE version >= 31")
    connection.commit()
    connection.close()
    assert [m.version for m in schema.MIGRATIONS[11:14:2]] == [12, 14]
    before = _tables(path)
    assert DROPPED <= before

    Store.open(path).close()

    after = _tables(path)
    assert after == before - DROPPED
    assert (tmp_path / f"orchestrator.db.pre-schema-{schema.SCHEMA_VERSION}.backup").is_file()
