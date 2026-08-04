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


def test_v6_upgrade_rewrites_companion_action_lease_label_to_main(
    tmp_path,
) -> None:
    """007 迁移语义（Workbench 改版 B5）：

    - 非 challenged 行的 window_label 'message-panel'→'main'（active 与
      revoked 都改写，新 CHECK 覆盖所有非 challenged 行）；
    - challenged 行标签保持 NULL（双臂 CHECK 的 challenged 臂原样保留）；
    - requested_window_label 审计列不改写（保留历史原值）；
    - 外键子表 profile_control_commands 引用保持完整，
      foreign_key_check 零违例；
    - 新 CHECK 拒绝 ('message-panel','companion_action') 的非 challenged 行。
    """
    path = tmp_path / "companion-v6.db"
    from deskpet.companion.schema import MIGRATION_RESOURCES

    with sqlite3.connect(path) as db:
        # 迁移脚本引用 configure_connection 注册的 deskpet_sha256；
        # 这里按 schema.configure_connection 同款注册（保持 FK OFF，
        # 与 initialize_schema 的迁移包裹一致）。
        import hashlib

        db.create_function(
            "deskpet_sha256",
            1,
            lambda value: hashlib.sha256(
                str(value).encode("utf-8")
            ).hexdigest(),
            deterministic=True,
        )
        for version in range(1, 7):
            db.executescript(
                MIGRATION_RESOURCES[version].read_text(encoding="utf-8")
            )
        db.execute("PRAGMA user_version=6")
        lease_sql = """INSERT INTO profile_control_leases(
             device_scope,connection_id,requested_window_label,requested_scope,
             window_label,scope,control_epoch,challenge_hash,last_seq,
             backend_process_instance_id,issued_at,expires_at,revoked_at,
             status,reason_code,schema_version
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)"""
        db.execute(
            lease_sql,
            ("dev", "conn-action", "message-panel", "companion_action",
             "message-panel", "companion_action", 1, "hash-a", 1,
             "proc", "t0", "t9", None, "active", "test"),
        )
        db.execute(
            lease_sql,
            ("dev", "conn-bind", "main", "identity_bind",
             "main", "identity_bind", 2, "hash-b", 1,
             "proc", "t0", "t9", None, "active", "test"),
        )
        db.execute(
            lease_sql,
            ("dev", "conn-challenged", "message-panel", "companion_action",
             None, None, 3, "hash-c", 0,
             "proc", "t0", "t9", None, "challenged", "test"),
        )
        db.execute(
            lease_sql,
            ("dev", "conn-revoked", "message-panel", "companion_action",
             "message-panel", "companion_action", 4, "hash-d", 1,
             "proc", "t0", "t1", "t1", "revoked", "test"),
        )
        db.execute(
            """INSERT INTO profile_control_commands(
                 device_scope,connection_id,request_seq,
                 backend_process_instance_id,credential_nonce_hash,command_kind,
                 canonical_schema,request_hash,binding_epoch,status,result_ref,
                 result_hash,reason_code,schema_version,created_at,updated_at
               ) VALUES ('dev','conn-action',1,'proc','nonce-hash',
                         'companion_action_ready','control-command-canonical-v1',
                         'req-hash',1,'claimed',NULL,NULL,'test',1,'t0','t0')"""
        )
        db.commit()

    CompanionStore(path)

    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (
            COMPANION_SCHEMA_VERSION,
        )
        rows = {
            str(row[0]): (row[1], row[2], row[3])
            for row in db.execute(
                """SELECT connection_id,window_label,scope,
                          requested_window_label
                   FROM profile_control_leases"""
            )
        }
        # 非 challenged 行改写；requested_window_label 审计列保留原值。
        assert rows["conn-action"] == ("main", "companion_action", "message-panel")
        assert rows["conn-revoked"] == ("main", "companion_action", "message-panel")
        # identity_bind 行不受影响。
        assert rows["conn-bind"] == ("main", "identity_bind", "main")
        # challenged 行照旧 NULL 标签。
        assert rows["conn-challenged"] == (None, None, "message-panel")
        # 外键子表引用完整（schema.py 迁移后也强制检查，这里显式复核）。
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute(
            """SELECT COUNT(*) FROM profile_control_commands
               WHERE device_scope='dev' AND connection_id='conn-action'"""
        ).fetchone() == (1,)
        # 部分唯一索引随重建存在。
        assert db.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='index' AND name='uq_profile_control_active'"""
        ).fetchone() == (1,)
        # 新 CHECK：('message-panel','companion_action') 非 challenged 行被拒。
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """INSERT INTO profile_control_leases(
                     device_scope,connection_id,requested_window_label,
                     requested_scope,window_label,scope,control_epoch,
                     challenge_hash,last_seq,backend_process_instance_id,
                     issued_at,expires_at,revoked_at,status,reason_code,
                     schema_version
                   ) VALUES ('dev','conn-reject','message-panel',
                             'companion_action','message-panel',
                             'companion_action',9,'hash-x',0,'proc',
                             't0','t9',NULL,'active','test',1)"""
            )
        # 提升 challenged 行为 ('main','companion_action') 满足新约束
        # （先按真实流程 revoke 旧 active 租约，避开 uq_profile_control_active）。
        db.execute(
            """UPDATE profile_control_leases
               SET status='revoked',revoked_at='t2'
               WHERE connection_id='conn-action'"""
        )
        db.execute(
            """UPDATE profile_control_leases
               SET window_label='main',scope='companion_action',status='active'
               WHERE connection_id='conn-challenged'"""
        )


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
