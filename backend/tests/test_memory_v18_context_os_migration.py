from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory.memory_v2_schema import (
    CANONICAL_STATE_TABLES,
    CONTEXT_OS_V18_INDEXES,
    CONTEXT_OS_V18_TABLES,
)
from deskpet.memory.migrator import (
    DEFAULT_MIGRATIONS_DIR,
    MigrationError,
    TARGET_SCHEMA_VERSION,
    run_migrations,
)


def _objects(db_path: Path, kind: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type=?", (kind,)
            )
        }


async def _build_v17(db_path: Path, migrations_dir: Path) -> None:
    migrations_dir.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("00[1-9]_*.sql")):
        shutil.copyfile(source, migrations_dir / source.name)
    applied = await run_migrations(db_path, migrations_dir=migrations_dir)
    assert applied[-1] == "009_memory_v2_v17.sql"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17


@pytest.mark.asyncio
async def test_fresh_db_migrates_to_v18_with_context_tables(tmp_path: Path):
    db_path = tmp_path / "state.db"

    applied = await run_migrations(db_path)

    assert applied[-1] == "011_message_projection_visibility_v19.sql"
    assert TARGET_SCHEMA_VERSION == 19
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
    tables = _objects(db_path, "table")
    indexes = _objects(db_path, "index")
    assert set(CONTEXT_OS_V18_TABLES).issubset(tables)
    assert set(CONTEXT_OS_V18_INDEXES).issubset(indexes)
    assert set(CONTEXT_OS_V18_TABLES).issubset(set(CANONICAL_STATE_TABLES))


@pytest.mark.asyncio
async def test_v17_to_v18_preserves_existing_rows(tmp_path: Path):
    db_path = tmp_path / "state.db"
    await _build_v17(db_path, tmp_path / "v17-migrations")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions(id, created_at, metadata) VALUES ('s1', 1, '{}')"
        )
        conn.commit()

    applied = await run_migrations(db_path)

    assert applied == [
        "010_context_os_v18.sql",
        "011_message_projection_visibility_v19.sql",
    ]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
        assert conn.execute("SELECT id FROM sessions WHERE id='s1'").fetchone() == ("s1",)


@pytest.mark.asyncio
async def test_v18_rerun_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "state.db"
    first = await run_migrations(db_path)
    second = await run_migrations(db_path)

    assert first[-1] == "011_message_projection_visibility_v19.sql"
    assert second == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE version='010_context_os_v18.sql'"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_v18_ddl_marker_and_user_version_roll_back_together(tmp_path: Path):
    db_path = tmp_path / "state.db"
    migrations_dir = tmp_path / "migrations"
    await _build_v17(db_path, migrations_dir)
    (migrations_dir / "010_context_os_v18.sql").write_text(
        "CREATE TABLE session_context_snapshots(id TEXT PRIMARY KEY);\n"
        "THIS IS NOT SQL;\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError):
        await run_migrations(db_path, migrations_dir=migrations_dir)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='session_context_snapshots'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migrations "
            "WHERE version='010_context_os_v18.sql'"
        ).fetchone() is None
