from __future__ import annotations

import sqlite3

import pytest

from deskpet.companion.schema import (
    COMPANION_SCHEMA_VERSION,
    CONTROL_PLANE_TABLES,
    REQUIRED_TABLES,
    initialize_schema,
)
from deskpet.companion.store import CompanionStore


def test_new_and_repeat_initialization_install_the_complete_schema(tmp_path) -> None:
    path = tmp_path / "companion.db"
    CompanionStore(path)
    CompanionStore(path)

    with sqlite3.connect(path) as db:
        names = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert REQUIRED_TABLES <= names
        assert (
            db.execute("PRAGMA user_version").fetchone()[0]
            == COMPANION_SCHEMA_VERSION
        )
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        state = db.execute(
            "SELECT phase,generation,roll_forward_required FROM growth_authority_state"
        ).fetchone()
        assert state == ("legacy", 1, 0)


def test_v1_upgrade_preserves_growth_dependencies_and_adds_memory_scope(
    tmp_path,
) -> None:
    path = tmp_path / "companion-v1.db"
    from deskpet.companion.schema import MIGRATION_RESOURCES

    with sqlite3.connect(path) as db:
        db.executescript(MIGRATION_RESOURCES[1].read_text(encoding="utf-8"))
        db.execute("PRAGMA user_version=1")
        db.execute(
            """INSERT INTO profiles(
                 profile_id,generation,identity_namespace_hash,status,reason_code,
                 schema_version,created_at,updated_at
               ) VALUES ('p',1,'identity','active','test',1,'n','n')"""
        )
        db.execute(
            """INSERT INTO run_growth_snapshots(
                 profile_id,profile_generation,snapshot_id,request_id,run_id,
                 snapshot_generation,snapshot_hash,status,reason_code,schema_version,
                 created_at,updated_at
               ) VALUES ('p',1,'s','q','r',0,'h','prepared','test',1,'n','n')"""
        )
        db.execute(
            """INSERT INTO run_growth_dependency_items(
                 profile_id,profile_generation,snapshot_id,dependency_kind,
                 dependency_id,content_hash,reason_code,schema_version,created_at
               ) VALUES ('p',1,'s','preference','pref','ph','test',1,'n')"""
        )
        db.commit()

    CompanionStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (
            COMPANION_SCHEMA_VERSION,
        )
        assert db.execute(
            """SELECT dependency_kind,dependency_id
               FROM run_growth_dependency_items"""
        ).fetchall() == [("preference", "pref")]
        ddl = db.execute(
            """SELECT sql FROM sqlite_master
               WHERE type='table' AND name='run_growth_dependency_items'"""
        ).fetchone()[0]
        assert "memory_scope" in ddl
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema_initialization_does_not_leave_database_locked(tmp_path) -> None:
    path = tmp_path / "companion-close.db"
    CompanionStore(path)

    # sqlite3.Connection's context manager does not close the Windows handle.
    # Profile/user-data deletion must be able to remove the initialized DB.
    path.unlink()
    assert not path.exists()


def test_all_owner_domain_tables_have_generation_partition_prefix(tmp_path) -> None:
    path = tmp_path / "companion.db"
    CompanionStore(path)

    with sqlite3.connect(path) as db:
        for table in sorted(REQUIRED_TABLES - CONTROL_PLANE_TABLES - {"companion_schema"}):
            columns = db.execute(f'PRAGMA table_info("{table}")').fetchall()
            names = {str(row[1]) for row in columns}
            assert {"profile_id", "profile_generation"} <= names, table
            pk = [str(row[1]) for row in sorted(columns, key=lambda row: int(row[5])) if row[5]]
            assert pk[:2] == ["profile_id", "profile_generation"], (table, pk)


def test_schema_does_not_duplicate_capability_or_tool_authority(tmp_path) -> None:
    path = tmp_path / "companion.db"
    CompanionStore(path)

    with sqlite3.connect(path) as db:
        ddl = "\n".join(
            str(row[0] or "").lower()
            for row in db.execute(
                "SELECT sql FROM sqlite_master WHERE type IN ('table','index','trigger')"
            )
        )
    assert "active_capability_versions" not in ddl
    assert "installed_capability_versions" not in ddl
    assert "tool_specs" not in ddl


@pytest.mark.parametrize(
    ("mode", "source_owner", "source_scope", "source_version", "source_manifest", "source_gen",
     "target_owner", "target_scope", "expected_absent", "expected_gen", "reservation"),
    [
        ("genesis", "someone", None, None, None, None, "companion:p:1", "user", 1, 0, 1),
        ("update", "other", "user", "1", "m", 1, "companion:p:1", "user", 0, 1, None),
        ("builtin_override", "builtin", "builtin", "1", "m", 1, "companion:p:1", "user", 0, 0, None),
    ],
)
def test_candidate_mode_check_rejects_mixed_source_target_fences(
    tmp_path,
    mode,
    source_owner,
    source_scope,
    source_version,
    source_manifest,
    source_gen,
    target_owner,
    target_scope,
    expected_absent,
    expected_gen,
    reservation,
) -> None:
    path = tmp_path / "companion.db"
    store = CompanionStore(path)
    store.create_profile(profile_id="p", generation=1, identity_namespace_hash="identity")
    with store._write() as db:  # direct DDL contract probe
        db.execute(
            """INSERT INTO growth_targets(
                 profile_id,profile_generation,target_id,kind,stable_name,pack_id,
                 governance_domain,reason_code,schema_version,created_at,updated_at
               ) VALUES ('p',1,'t','skill','name','pack','companion_growth','test',1,'n','n')"""
        )
        db.execute(
            """INSERT INTO candidate_packages(
                 profile_id,profile_generation,package_id,candidate_mode,pack_id,version,
                 candidate_content_hash,candidate_manifest_hash,candidate_package_hash,archive_hash,
                 source_facts_json,target_facts_json,effect_topology_hash,governance_domain,
                 content_state,blob_cleanup_state,reason_code,schema_version,created_at,updated_at
               ) VALUES ('p',1,'pkg',?,'pack','1','c','m','ph','a','{}','{}','e',
                         'companion_growth','live','live','test',1,'n','n')""",
            (mode,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO candidate_artifacts(
                     profile_id,profile_generation,candidate_id,candidate_attempt_key,package_id,
                     proposal_source_kind,proposal_source_ref,proposal_source_hash,candidate_mode,
                     target_id,source_owner_key,source_scope,source_scope_key,source_version,
                     source_manifest_hash,source_binding_generation,target_owner_key,target_scope,
                     target_scope_key,target_expected_absent,target_expected_binding_generation,
                     evidence_set_hash,reservation_version,attempt_generation,status,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES ('p',1,'c','ak','pkg','reflection','r','h',?,'t',?,?,?,?,?,?,
                             ?,?,'profile',?,?, 'es',?,1,'proposed','test',1,'n','n')""",
                (
                    mode,
                    source_owner,
                    source_scope,
                    "profile" if source_scope else None,
                    source_version,
                    source_manifest,
                    source_gen,
                    target_owner,
                    target_scope,
                    expected_absent,
                    expected_gen,
                    reservation,
                ),
            )


def test_evaluation_launch_has_no_prepared_state_and_unsafe_retry_trigger_exists(tmp_path) -> None:
    path = tmp_path / "companion.db"
    CompanionStore(path)
    with sqlite3.connect(path) as db:
        ddl = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='evaluation_case_launches'"
        ).fetchone()[0]
        trigger = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trg_evaluation_launch_no_unsafe_retry'"
        ).fetchone()[0]
    assert "'prepared'" not in ddl
    assert "'claimed'" in ddl
    assert "cleanup_required" in trigger
