from __future__ import annotations

import re
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, ensure_v9
from deskpet.memory.project_session_reset import RESET_MANIFEST
from deskpet.memory.schema import InitializeError, initialize_state_db


async def _build_v32_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    user_data = tmp_path / "user-data"
    data_dir = user_data / "data"
    data_dir.mkdir(parents=True)
    old_migrations = tmp_path / "migrations-v32"
    old_migrations.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        if not source.name.startswith("025_"):
            shutil.copy2(source, old_migrations / source.name)
    db_path = data_dir / "state.db"
    await ensure_v9(db_path, migrations_dir=old_migrations)

    session_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,1)", (session_id,))
        db.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) "
            "VALUES(1,?,'user','legacy-message',2)",
            (session_id,),
        )
        db.execute(
            "INSERT INTO projects(project_id,display_name,canonical_root,root_kind,"
            "filesystem_identity,project_revision,created_at,updated_at,last_opened_at) "
            "VALUES(?, 'legacy-project', ?, 'folder', 'legacy-fs', 1, 1, 1, 1)",
            (project_id, str(tmp_path / "real-project")),
        )
        db.execute(
            "INSERT INTO session_project_bindings(session_id,project_id,execution_kind,"
            "binding_version,created_at) VALUES(?,?,'project_root',1,1)",
            (session_id, project_id),
        )

    def seed_db(name: str, statements: tuple[str, ...]) -> None:
        with sqlite3.connect(data_dir / name) as db:
            for statement in statements:
                db.execute(statement)

    seed_db(
        "workflow.db",
        (
            "CREATE TABLE workflow_schema_migrations(version TEXT)",
            "INSERT INTO workflow_schema_migrations VALUES('keep')",
            "CREATE TABLE execution_runs(id TEXT)",
            "INSERT INTO execution_runs VALUES('legacy-run')",
            "CREATE TABLE task_grants(id TEXT)",
            "INSERT INTO task_grants VALUES('legacy-grant')",
        ),
    )
    seed_db(
        "companion.db",
        (
            "CREATE TABLE companion_settings(key TEXT, value TEXT)",
            "INSERT INTO companion_settings VALUES('appearance','keep')",
            "CREATE TABLE session_events(id TEXT)",
            "INSERT INTO session_events VALUES('legacy-event')",
        ),
    )
    seed_db(
        "sdk-product-state.db",
        (
            "CREATE TABLE authorization_sagas(id TEXT)",
            "INSERT INTO authorization_sagas VALUES('legacy-saga')",
            "CREATE TABLE global_preferences(key TEXT, value TEXT)",
            "INSERT INTO global_preferences VALUES('model','keep')",
        ),
    )
    for name in RESET_MANIFEST["delete_databases"]:
        path = data_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE legacy_payload(value TEXT)")
            db.execute("INSERT INTO legacy_payload VALUES('delete-me')")

    blob_dir = user_data / "workflows" / "blobs"
    blob_dir.mkdir(parents=True)
    (blob_dir / "legacy-run.bin").write_bytes(b"delete-me")
    (user_data / "config.toml").write_text("default_model = 'keep'\n", encoding="utf-8")
    real_project = tmp_path / "real-project"
    real_project.mkdir()
    (real_project / "source.txt").write_text("keep", encoding="utf-8")
    return db_path, user_data, session_id


def test_reset_manifest_matches_v33_state_sql() -> None:
    sql = (DEFAULT_MIGRATIONS_DIR / "025_legacy_session_reset_v33.sql").read_text(
        encoding="utf-8"
    )
    deleted = set(re.findall(r"DELETE FROM ([a-zA-Z0-9_]+)", sql))
    deleted.discard("sqlite_sequence")
    deleted.difference_update(RESET_MANIFEST["state.db"]["drop_tables"])
    assert deleted == set(RESET_MANIFEST["state.db"]["empty_tables"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_version", "last_migration"),
    ((9, "001_"), (17, "009_"), (23, "015_"), (31, "023_")),
)
async def test_representative_legacy_versions_upgrade_to_empty_v33(
    tmp_path: Path, expected_version: int, last_migration: str
) -> None:
    old_migrations = tmp_path / f"migrations-v{expected_version}"
    old_migrations.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        shutil.copy2(source, old_migrations / source.name)
        if source.name.startswith(last_migration):
            break
    db_path = tmp_path / "user-data" / "data" / "state.db"
    db_path.parent.mkdir(parents=True)
    await ensure_v9(db_path, migrations_dir=old_migrations)
    session_id = str(uuid.uuid4())
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == expected_version
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,1)", (session_id,))
        db.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) "
            "VALUES(1,?,'user','legacy-v31',2)",
            (session_id,),
        )
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 33
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert db.execute(
            "SELECT phase FROM legacy_session_reset_state"
        ).fetchone()[0] == "completed"


@pytest.mark.asyncio
async def test_v32_upgrade_deletes_legacy_data_and_preserves_global_files(
    tmp_path: Path,
) -> None:
    db_path, user_data, _ = await _build_v32_fixture(tmp_path)
    await initialize_state_db(db_path)

    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 33
        assert db.execute(
            "SELECT phase FROM legacy_session_reset_state"
        ).fetchone()[0] == "completed"
        existing = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in RESET_MANIFEST["state.db"]["empty_tables"]:
            assert table in existing
            assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        for table in RESET_MANIFEST["state.db"]["drop_tables"]:
            assert table not in existing

    with sqlite3.connect(user_data / "data" / "workflow.db") as db:
        assert db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM task_grants").fetchone()[0] == 0
        assert db.execute(
            "SELECT version FROM workflow_schema_migrations"
        ).fetchone()[0] == "keep"
    with sqlite3.connect(user_data / "data" / "companion.db") as db:
        assert db.execute("SELECT COUNT(*) FROM session_events").fetchone()[0] == 0
        assert db.execute(
            "SELECT value FROM companion_settings WHERE key='appearance'"
        ).fetchone()[0] == "keep"
    with sqlite3.connect(user_data / "data" / "sdk-product-state.db") as db:
        assert db.execute("SELECT COUNT(*) FROM authorization_sagas").fetchone()[0] == 0
        assert db.execute(
            "SELECT value FROM global_preferences WHERE key='model'"
        ).fetchone()[0] == "keep"
    for name in RESET_MANIFEST["delete_databases"]:
        assert not (user_data / "data" / name).exists()
    assert not (user_data / "workflows" / "blobs").exists()
    assert (user_data / "config.toml").read_text(encoding="utf-8") == "default_model = 'keep'\n"
    assert (tmp_path / "real-project" / "source.txt").read_text(encoding="utf-8") == "keep"
    assert not list((user_data / "data").glob("state.db.bak.*"))


@pytest.mark.asyncio
async def test_reset_runs_once_and_new_session_data_survives_restart(tmp_path: Path) -> None:
    db_path, _, old_session_id = await _build_v32_fixture(tmp_path)
    await initialize_state_db(db_path)
    new_session_id = str(uuid.uuid4())
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,10)", (new_session_id,))
        db.execute(
            "INSERT INTO messages(id,session_id,role,content,created_at) "
            "VALUES(2,?,'user','new-message',11)",
            (new_session_id,),
        )
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM sessions WHERE id=?", (old_session_id,)
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM sessions WHERE id=?", (new_session_id,)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT content FROM messages WHERE session_id=?", (new_session_id,)
        ).fetchone()[0] == "new-message"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_stage",
    (
        "before_legacy_reset_commit",
        "after_legacy_reset_commit",
        "before_external_store_reset",
        "after_external_store_reset",
        "before_vector_reset",
        "after_vector_reset",
        "before_backup_cleanup",
        "after_backup_cleanup",
        "before_completion_commit",
        "after_completion_commit",
    ),
)
async def test_reset_faults_fail_closed_then_resume(
    tmp_path: Path, fault_stage: str
) -> None:
    db_path, _, _ = await _build_v32_fixture(tmp_path)
    fired = False

    def crash(point: str) -> None:
        nonlocal fired
        if point == fault_stage and not fired:
            fired = True
            raise RuntimeError(f"crash:{fault_stage}")

    with pytest.raises(InitializeError, match=f"crash:{fault_stage}"):
        await initialize_state_db(db_path, fault_inject=crash)
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT phase FROM legacy_session_reset_state"
        ).fetchone()[0] == "completed"
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
