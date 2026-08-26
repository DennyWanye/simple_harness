from __future__ import annotations

import re
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

import deskpet.memory.project_session_reset as project_session_reset
from deskpet.memory.migrator import DEFAULT_MIGRATIONS_DIR, ensure_v9
from deskpet.memory.project_session_reset import RESET_MANIFEST, _clear_selected_tables
from deskpet.memory.schema import InitializeError, initialize_state_db
from deskpet.memory.memory_v2_schema import ensure_memory_v2_tables
from deskpet.companion.store import CompanionStore
from deskpet.workflows.store.schema import initialize_workflow_db


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
    # facts was historically lazy/optional.  This fixture models an install
    # that used conversation Memory; representative-version tests below also
    # cover old databases where the table never existed.
    await ensure_memory_v2_tables(db_path)

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
            "INSERT INTO facts(category,subject,key,value,created_at,updated_at) "
            "VALUES('conversation','legacy','sentinel','delete-me',1,1)"
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

    workflow_path = data_dir / "workflow.db"
    await initialize_workflow_db(workflow_path)
    with sqlite3.connect(workflow_path) as db:
        runtime_state_before = db.execute(
            "SELECT generation,phase FROM execution_runtime_state WHERE singleton_id=1"
        ).fetchone()
        assert runtime_state_before == (0, "legacy")
        db.execute(
            "INSERT INTO workflow_start_requests VALUES"
            "('legacy-key',?,'request','turn','profile','capability','legacy-run',1)",
            (session_id,),
        )
        db.execute(
            "INSERT INTO capability_run_catalog_snapshots VALUES"
            "('legacy-stamp','owner','{}','scope-hash','{}','vector-hash',"
            "'entry-hash',0,1)"
        )
        db.execute(
            "INSERT INTO capability_runtime_leases(lease_id,pack_id,version,"
            "manifest_hash,run_id,state,heartbeat_at,created_at) "
            "VALUES('legacy-lease','pack','1','manifest','legacy-run','ready',1,1)"
        )
        hash64 = "a" * 64
        db.execute(
            "INSERT INTO execution_candidate_draft_receipts VALUES("
            "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "keep-receipt",
                "launch",
                "child",
                hash64,
                "proposal",
                hash64,
                hash64,
                hash64,
                hash64,
                hash64,
                hash64,
                hash64,
                hash64,
                hash64,
                "now",
            ),
        )
        db.execute(
            "INSERT INTO execution_candidate_draft_materials VALUES(?,?,?,?,?,?,?,?)",
            ("keep-receipt", b"draft", hash64, hash64, hash64, hash64, hash64, "now"),
        )

    companion_path = data_dir / "companion.db"
    CompanionStore(companion_path)
    with sqlite3.connect(companion_path) as db:
        db.execute(
            "INSERT INTO profiles(profile_id,generation,identity_namespace_hash,status,"
            "reason_code,schema_version,created_at,updated_at) "
            "VALUES('p',1,'identity','active','test',1,'n','n')"
        )
        db.execute(
            "INSERT INTO candidate_packages(profile_id,profile_generation,package_id,"
            "candidate_mode,pack_id,version,candidate_content_hash,candidate_manifest_hash,"
            "candidate_package_hash,archive_hash,source_facts_json,target_facts_json,"
            "effect_topology_hash,governance_domain,content_state,blob_cleanup_state,"
            "reason_code,schema_version,created_at,updated_at) VALUES"
            "('p',1,'pkg','genesis','pack','1','content','manifest','package','archive',"
            "'{}','{}','effects','companion_growth','live','live','test',1,'n','n')"
        )
        db.execute(
            "INSERT INTO run_growth_snapshots(profile_id,profile_generation,snapshot_id,"
            "request_id,run_id,snapshot_generation,snapshot_hash,status,reason_code,"
            "schema_version,created_at,updated_at) "
            "VALUES('p',1,'snapshot','request','legacy-run',0,'hash','prepared','test',1,'n','n')"
        )
        db.execute(
            "INSERT INTO run_growth_dependency_items(profile_id,profile_generation,"
            "snapshot_id,dependency_kind,dependency_id,content_hash,reason_code,"
            "schema_version,created_at) "
            "VALUES('p',1,'snapshot','preference','pref','content','test',1,'n')"
        )
        db.execute(
            "INSERT INTO companion_run_bindings(profile_id,profile_generation,run_id,"
            "request_id,root_run_id,snapshot_generation,snapshot_id,snapshot_hash,"
            "all_generations_root_hash,start_fingerprint,status,reason_code,schema_version,"
            "created_at,updated_at) VALUES"
            "('p',1,'legacy-run','request','legacy-root',0,'snapshot','hash','all','start',"
            "'active','test',1,'n','n')"
        )
        db.execute(
            "INSERT INTO growth_events VALUES("
            "'p',1,'legacy-event','event-hash','conversation','legacy-message',"
            "'legacy-context','legacy-run',NULL,'{\"session_id\":\"legacy-session\"}',"
            "'live','test',1,'n','n')"
        )
        db.execute(
            "INSERT INTO preferences VALUES("
            "'p',1,'legacy-preference','{\"message\":\"legacy-message\"}',NULL,NULL,NULL,"
            "'inferred',1,NULL,'test',1,'n','n')"
        )
        db.execute(
            "INSERT INTO preference_evidence VALUES("
            "'p',1,'legacy-preference','legacy-event','legacy-context',1.0,'test',1,'n')"
        )
        db.execute(
            "INSERT INTO owner_memory_read_scopes VALUES("
            "'p',1,'legacy-scope','scope-hash',1,1,'set-hash',1,"
            "'[\"legacy-session\"]','test',1,'n')"
        )
        db.execute(
            "INSERT INTO preference_turn_decision_receipts VALUES("
            "'p',1,'legacy-message','message-hash','input-hash','{}','decision-hash',"
            "'interpreter','1','test',1,'n')"
        )
        db.execute(
            "INSERT INTO jobs(profile_id,profile_generation,job_id,kind,dedupe_key,"
            "payload_json,payload_hash,status,reason_code,schema_version,created_at,updated_at) "
            "VALUES('p',1,'legacy-job','reflection','legacy-job',"
            "'{\"run_id\":\"legacy-run\"}','payload-hash','queued','test',1,'n','n')"
        )
    seed_db(
        "sdk-product-state.db",
        (
            "CREATE TABLE authorization_sagas(id TEXT)",
            "INSERT INTO authorization_sagas VALUES('legacy-saga')",
            "CREATE TABLE global_preferences(key TEXT, value TEXT)",
            "INSERT INTO global_preferences VALUES('model','keep')",
            "CREATE TABLE capability_runtime_leases(id TEXT)",
            "INSERT INTO capability_runtime_leases VALUES('legacy-runtime')",
            "CREATE TABLE capability_versions(id TEXT)",
            "INSERT INTO capability_versions VALUES('keep-version')",
        ),
    )
    seed_db(
        "billing.db",
        (
            "CREATE TABLE billing_sentinel(value TEXT)",
            "INSERT INTO billing_sentinel VALUES('keep')",
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
    for relative in ("skills/keep.txt", "plugins/keep.txt"):
        path = user_data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep", encoding="utf-8")
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


def test_selected_table_cleanup_restores_immutable_delete_trigger(tmp_path: Path) -> None:
    path = tmp_path / "triggered.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE execution_rows(id TEXT PRIMARY KEY);
            CREATE TRIGGER execution_rows_no_delete
            BEFORE DELETE ON execution_rows
            BEGIN SELECT RAISE(ABORT,'immutable'); END;
            INSERT INTO execution_rows VALUES('legacy');
            """
        )

    _clear_selected_tables(path, select=lambda name: name == "execution_rows")

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM execution_rows").fetchone()[0] == 0
        db.execute("INSERT INTO execution_rows VALUES('new')")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM execution_rows")


def test_selected_table_cleanup_rolls_back_trigger_and_rows_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "trigger-rollback.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE execution_a(id TEXT PRIMARY KEY);
            CREATE TABLE execution_b(id TEXT PRIMARY KEY);
            CREATE TRIGGER execution_a_no_delete
            BEFORE DELETE ON execution_a
            BEGIN SELECT RAISE(ABORT,'immutable'); END;
            INSERT INTO execution_a VALUES('a');
            INSERT INTO execution_b VALUES('b');
            """
        )

    original_quote = project_session_reset._quote_identifier
    calls = 0

    def fail_during_second_delete(value: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("injected_delete_failure")
        return original_quote(value)

    monkeypatch.setattr(
        project_session_reset, "_quote_identifier", fail_during_second_delete
    )
    with pytest.raises(RuntimeError, match="injected_delete_failure"):
        _clear_selected_tables(path, select=lambda name: name.startswith("execution_"))

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id FROM execution_a").fetchone()[0] == "a"
        assert db.execute("SELECT id FROM execution_b").fetchone()[0] == "b"
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM execution_a")


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
        facts_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='facts'"
        ).fetchone()
        if facts_exists:
            db.execute(
                "INSERT INTO facts(category,subject,key,value,created_at,updated_at) "
                "VALUES('conversation','legacy','sentinel','delete-me',1,1)"
            )
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 33
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        facts_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='facts'"
        ).fetchone()
        if facts_exists:
            assert db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
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
        for table in RESET_MANIFEST["state.db"]["optional_empty_tables"]:
            assert table in existing
            assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        for table in RESET_MANIFEST["state.db"]["drop_tables"]:
            assert table not in existing

    with sqlite3.connect(user_data / "data" / "workflow.db") as db:
        assert db.execute("SELECT COUNT(*) FROM workflow_start_requests").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM capability_run_catalog_snapshots"
        ).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM capability_runtime_leases").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM workflow_schema_migrations").fetchone()[0] > 0
        assert db.execute(
            "SELECT generation,phase FROM execution_runtime_state WHERE singleton_id=1"
        ).fetchone() == (0, "legacy")
        assert db.execute(
            "SELECT receipt_id FROM execution_candidate_draft_receipts"
        ).fetchone()[0] == "keep-receipt"
        assert db.execute(
            "SELECT receipt_id FROM execution_candidate_draft_materials"
        ).fetchone()[0] == "keep-receipt"
    with sqlite3.connect(user_data / "data" / "companion.db") as db:
        for table in RESET_MANIFEST["companion.db"]["delete_tables"]:
            assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        assert db.execute(
            "SELECT candidate_package_hash FROM candidate_packages"
        ).fetchone()[0] == "package"
        assert db.execute(
            "SELECT phase FROM growth_authority_state WHERE authority_key='growth'"
        ).fetchone()[0] == "legacy"
    with sqlite3.connect(user_data / "data" / "sdk-product-state.db") as db:
        assert db.execute("SELECT COUNT(*) FROM authorization_sagas").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM capability_runtime_leases").fetchone()[0] == 0
        assert db.execute(
            "SELECT value FROM global_preferences WHERE key='model'"
        ).fetchone()[0] == "keep"
        assert db.execute("SELECT id FROM capability_versions").fetchone()[0] == "keep-version"
    for name in RESET_MANIFEST["delete_databases"]:
        assert not (user_data / "data" / name).exists()
    with sqlite3.connect(user_data / "data" / "billing.db") as db:
        assert db.execute("SELECT value FROM billing_sentinel").fetchone()[0] == "keep"
    assert (user_data / "skills" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert (user_data / "plugins" / "keep.txt").read_text(encoding="utf-8") == "keep"
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
        for table in RESET_MANIFEST["state.db"]["empty_tables"]:
            assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        for table in RESET_MANIFEST["state.db"]["optional_empty_tables"]:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0

    # A third startup must not re-run the reset or erase valid post-upgrade data.
    new_session_id = str(uuid.uuid4())
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO sessions(id,created_at) VALUES(?,10)", (new_session_id,))
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM sessions WHERE id=?", (new_session_id,)
        ).fetchone()[0] == 1
