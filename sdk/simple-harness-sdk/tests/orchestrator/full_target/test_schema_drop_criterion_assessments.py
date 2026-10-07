# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Migration 33 (2026-10-02, strict citation option A): the document domain's
per-criterion citation receipts are dropped.  Migration 10 that created the table and
migration 26 whose import barrier put triggers on it keep their original text (the
barrier is frozen as literal SQL), so a library at version 32 must open, upgrade and
lose exactly this table, its index and its triggers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_orchestrator.storage import schema
from agent_orchestrator.storage.store import Store

TABLE = "criterion_assessments"


def _objects(path: Path, kind: str) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type=?", (kind,)
            )
        }
    finally:
        connection.close()


def test_a_new_library_has_no_criterion_assessments(tmp_path) -> None:
    Store.open(tmp_path / "orchestrator.db").close()
    assert TABLE not in _objects(tmp_path / "orchestrator.db", "table")
    assert not {
        name for name in _objects(tmp_path / "orchestrator.db", "trigger") if TABLE in name
    }


def test_migration_26_text_still_puts_its_barrier_on_the_table() -> None:
    """The frozen barrier is byte-for-byte the shipped one: it still names the table."""
    m26 = next(m for m in schema.MIGRATIONS if m.version == 26)
    assert f"ON {TABLE}" in m26.ddl


def test_a_version_32_library_opens_and_loses_exactly_that_table(tmp_path, monkeypatch) -> None:
    # A real version-32 library: migrations 1..32 only (a later migration such as 38's
    # DROP COLUMN cannot be replayed on a library that already ran it).
    path = tmp_path / "orchestrator.db"
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:32])
        Store.open(path).close()
    before = _objects(path, "table")
    assert TABLE in before

    # 只升到下一版：迁移 33 删掉的恰好是这一张表，不多不少。原断言"升级后表集合减去它是升级前的子集"
    # 在迁移 40（0f8d8d9fd 全库做法表）、迁移 46（0a10df5e0c 恢复表）加表后不再成立（2026-10-08）。
    with monkeypatch.context() as patch:
        patch.setattr(schema, "MIGRATIONS", schema.MIGRATIONS[:33])
        Store.open(path).close()
    assert _objects(path, "table") == before - {TABLE}

    # 再按当前全部迁移打开：迁移链走得通，这张表不会回来。
    Store.open(path).close()
    assert TABLE not in _objects(path, "table")
    assert schema.SCHEMA_VERSION >= 33
