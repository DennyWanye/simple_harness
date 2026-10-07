# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Migration 39 (2026-10-03, HTN 补齐阶段 D): the two support-set tables that never had a
production writer (``justification_sets`` / ``support_members``) are dropped together with
their barrier triggers, and the obligations table loses its three always-zero spend columns.
Migrations 16 and 26 that created them keep their original text; the obligations barrier
trigger is rebuilt from migration 38's text with only the three column clauses removed, and
a library at version 38 opens, upgrades and keeps its obligation rows."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_orchestrator.storage import schema
from agent_orchestrator.storage.store import Store

TABLES = ("justification_sets", "support_members")
COLUMNS = ("failure_count", "spent_tokens", "spent_attempts")
TRIGGER = "assurance_source_obligations_update"


def _names(path: Path, sql: str) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [str(row[0]) for row in connection.execute(sql)]
    finally:
        connection.close()


def _columns(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [row[1] for row in connection.execute("PRAGMA table_info(obligations)")]
    finally:
        connection.close()


def _gone(path: Path) -> None:
    referencing = _names(
        path,
        "SELECT name FROM sqlite_master WHERE "
        + " OR ".join(f"sql LIKE '%{name}%'" for name in (*TABLES, *COLUMNS)),
    )
    assert referencing == []
    assert not set(COLUMNS) & set(_columns(path))
    assert TRIGGER in _names(path, "SELECT name FROM sqlite_master WHERE type='trigger'")


def test_a_new_library_has_neither_table_nor_the_spend_columns(tmp_path) -> None:
    path = tmp_path / "orchestrator.db"
    Store.open(path).close()
    _gone(path)


def test_published_migration_text_is_unchanged() -> None:
    texts = {migration.version: migration.ddl for migration in schema.MIGRATIONS}
    assert "CREATE TABLE justification_sets" in texts[16]
    assert "failure_count INTEGER" in texts[16]
    assert "assurance_source_support_members_insert" in texts[26]
    rebuilt = texts[39][texts[39].index(f"CREATE TRIGGER {TRIGGER}"):]
    original = texts[38][texts[38].index(f"CREATE TRIGGER {TRIGGER}"):]
    assert rebuilt == original.replace(
        " OR NEW.failure_count IS NOT OLD.failure_count"
        " OR NEW.spent_tokens IS NOT OLD.spent_tokens"
        " OR NEW.spent_attempts IS NOT OLD.spent_attempts",
        "",
    )


def test_a_version_38_library_opens_and_keeps_its_duties(tmp_path, monkeypatch) -> None:
    path = tmp_path / "orchestrator.db"
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:38])
        Store.open(path).close()
    connection = sqlite3.connect(path)  # foreign keys are off on a raw connection
    connection.execute(
        "INSERT INTO obligations(mission_id,obligation_id,goal_signature_id,scope,requiredness,"
        "lifecycle,fuel_limit,fuel_used,fuel_remaining,obligation_json,created_at,updated_at)"
        " VALUES ('m1','o1','g','mission','required','UNSATISFIED',3,1,2,'{}',0,0)"
    )
    connection.commit()
    connection.close()
    assert set(COLUMNS) <= set(_columns(path))

    Store.open(path).close()

    _gone(path)
    # 备份按"这次升到的最后一个迁移号"命名（D4-15），不写死号码
    assert path.with_name(f"orchestrator.db.pre-schema-{schema.MIGRATIONS[-1].version}.backup").is_file()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT obligation_id,fuel_limit,fuel_used,fuel_remaining FROM obligations"
        ).fetchall() == [("o1", 3, 1, 2)]
        assert max(row[0] for row in connection.execute(
            "SELECT version FROM orch_schema_migrations")) == 39
    finally:
        connection.close()
