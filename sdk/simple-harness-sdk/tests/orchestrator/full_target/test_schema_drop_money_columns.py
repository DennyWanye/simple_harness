# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Migration 38 (2026-10-03, HTN 补齐阶段 C): orchestration records tokens only, so the
13 money columns of five tables are dropped.  The migrations that created them (1, 13,
16) and migration 26 whose barrier trigger named one of them keep their original text;
a library at version 37 must open, upgrade, lose exactly these columns, keep its rows,
and still carry the obligations barrier trigger without the dropped column."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_orchestrator.storage import schema
from agent_orchestrator.storage.store import Store

DROPPED = {
    "budget_accounts": {"reserved_cost_micros", "settled_cost_micros", "unpriced_settlements"},
    "budget_reservations": {"reserved_cost_micros", "settled_cost_micros", "unpriced"},
    "imported_usage": {"cost_micros", "unpriced"},
    "provider_token_grants": {"price_json", "price_digest", "cost_upper_micros",
                              "actual_cost_micros"},
    "obligations": {"spent_cost_micros"},
}
TRIGGER = "assurance_source_obligations_update"


def _columns(path: Path, table: str) -> list[str]:
    connection = sqlite3.connect(path)
    try:
        return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    finally:
        connection.close()


def _trigger_sql(path: Path) -> str | None:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (TRIGGER,)
        ).fetchone()
        return None if row is None else str(row[0])
    finally:
        connection.close()


def test_a_new_library_has_no_money_columns(tmp_path) -> None:
    path = tmp_path / "orchestrator.db"
    Store.open(path).close()
    for table, columns in DROPPED.items():
        assert not columns & set(_columns(path, table)), table
    sql = _trigger_sql(path)
    assert sql is not None and "spent_cost_micros" not in sql
    assert "NEW.fuel_used IS NOT OLD.fuel_used" in sql


def test_published_migration_text_is_unchanged() -> None:
    """The columns are dropped by 38, never by editing the migrations that made them."""
    texts = {m.version: m.ddl for m in schema.MIGRATIONS}
    assert "unpriced_settlements" in texts[1] and "cost_upper_micros" in texts[13]
    assert "spent_cost_micros" in texts[16]
    assert "OR NEW.spent_cost_micros IS NOT OLD.spent_cost_micros" in texts[26]
    rebuilt = texts[38][texts[38].index(f"CREATE TRIGGER {TRIGGER}"):]
    original = texts[26][texts[26].index(f"CREATE TRIGGER {TRIGGER}"):]
    original = original[: original.index("\n END;\n") + len("\n END;\n")]
    assert rebuilt == original.replace(
        " OR NEW.spent_cost_micros IS NOT OLD.spent_cost_micros", ""
    )


def test_a_version_37_library_opens_and_loses_exactly_those_columns(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "orchestrator.db"
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:37])
        Store.open(path).close()
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO budget_accounts(account_id,scope,parent_id,mission_id,limits_json,"
        "reserved_tokens,settled_tokens,reserved_cost_micros,settled_cost_micros,"
        "unpriced_settlements,version,updated_at)"
        " VALUES ('budget:m1','mission',NULL,'m1','{}',5,7,11,13,1,1,0)"
    )
    connection.execute(
        "INSERT INTO imported_usage(usage_ref,subject_id,mission_id,input_tokens,output_tokens,"
        "cost_micros,unpriced,unknown,imported_at) VALUES ('u1','s1','m1',3,4,NULL,1,0,0)"
    )
    connection.commit()
    connection.close()
    before = {table: _columns(path, table) for table in DROPPED}
    assert "spent_cost_micros" in (_trigger_sql(path) or "")

    # Only migration 38 is under test here; 39 (stage D) has its own file.
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:38])
        Store.open(path).close()

    for table, columns in DROPPED.items():
        assert columns <= set(before[table])
        assert _columns(path, table) == [c for c in before[table] if c not in columns]
    sql = _trigger_sql(path)
    assert sql is not None and "spent_cost_micros" not in sql
    assert path.with_name("orchestrator.db.pre-schema-38.backup").is_file()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT reserved_tokens,settled_tokens FROM budget_accounts"
        ).fetchall() == [(5, 7)]
        assert connection.execute(
            "SELECT usage_ref,input_tokens,output_tokens,unknown FROM imported_usage"
        ).fetchall() == [("u1", 3, 4, 0)]
        assert max(row[0] for row in connection.execute(
            "SELECT version FROM orch_schema_migrations")) == 38
    finally:
        connection.close()
