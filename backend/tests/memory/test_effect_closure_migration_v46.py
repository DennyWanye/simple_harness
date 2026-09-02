# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 2：v46 迁移 ``038_effect_closure_memory_v46.sql``（design-freeze §5 全部 7 张表）。

- v45 库打开升 v46（前向迁移，旧数据不动）；
- 旧 runtime（target=45）打开 v46 库 → 稳定拒绝；
- 7 张表 append-only 触发器 + 状态单调守卫。
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory import migrator, schema
from deskpet.memory.migrator import (
    DEFAULT_MIGRATIONS_DIR,
    EFFECT_CLOSURE_MIGRATION,
    EFFECT_CLOSURE_SCHEMA_VERSION,
    HUMAN_MEMORY_TARGET_SCHEMA_VERSION,
)
from deskpet.memory.schema import (
    HumanMemoryProgramEpochError,
    StartupEpoch,
    initialize_human_memory_program_state_db,
    inspect_startup_epoch,
)

V46_TABLES = (
    "task_scope_closure_receipts",
    "harness_evidence_reservations",
    "memory_ingestion_outbox",
    "memory_ingestion_evidence_links",
    "post_turn_invocation_attempts",
    "post_turn_invocation_members",
    "effect_gate_rejections",
    "host_pre_admission_audit",
)


def test_v46_is_the_registered_target() -> None:
    assert EFFECT_CLOSURE_MIGRATION == "038_effect_closure_memory_v46.sql"
    assert EFFECT_CLOSURE_SCHEMA_VERSION == 46 == HUMAN_MEMORY_TARGET_SCHEMA_VERSION
    assert migrator.MIGRATION_STEPS[EFFECT_CLOSURE_MIGRATION] == 46
    assert (DEFAULT_MIGRATIONS_DIR / EFFECT_CLOSURE_MIGRATION).is_file()


async def _v45_database(tmp_path: Path, monkeypatch) -> Path:
    db = tmp_path / "state.db"
    monkeypatch.setattr(
        migrator, "MIGRATION_STEPS",
        {k: v for k, v in migrator.MIGRATION_STEPS.items() if v <= 45},
    )
    monkeypatch.setattr(migrator, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 45)
    monkeypatch.setattr(schema, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 45)
    monkeypatch.setattr(migrator, "_S4_HUMAN_MIGRATIONS", migrator._S4_HUMAN_MIGRATIONS - {EFFECT_CLOSURE_MIGRATION})
    legacy_dir = tmp_path / "migrations-v45"
    legacy_dir.mkdir()
    for source in sorted(DEFAULT_MIGRATIONS_DIR.glob("*.sql")):
        if source.name.startswith("038_"):
            continue
        shutil.copy2(source, legacy_dir / source.name)
    monkeypatch.setattr(migrator, "DEFAULT_MIGRATIONS_DIR", legacy_dir)
    await initialize_human_memory_program_state_db(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 45
        conn.execute(
            "INSERT INTO context_route_decisions(decision_id,sdk_run_id,provider_turn_ordinal,route,origin,"
            "task_scope_id,binding_set_revision,binding_set_receipt_id,binding_set_receipt_hash,recall_refs_json,"
            "receipt_id,receipt_hash,receipt_json,raw_call_id,effect_id,request_fingerprint,idempotency_key,"
            "decision_hash,recorded_at) VALUES ('d1','run-1',1,'direct_standalone','host_initial',NULL,NULL,NULL,NULL,"
            "'[]','r1',?, '{}',NULL,NULL,NULL,'k1',?,1.0)",
            ("a" * 64, "b" * 64),
        )
        conn.commit()
    monkeypatch.undo()
    return db


@pytest.mark.asyncio
async def test_v45_database_migrates_forward_to_v46_keeping_ledger_rows(tmp_path: Path, monkeypatch) -> None:
    db = await _v45_database(tmp_path, monkeypatch)
    decision = inspect_startup_epoch(db, approved_fresh_lane=False)
    assert decision.epoch is StartupEpoch.HUMAN_RESUME
    await initialize_human_memory_program_state_db(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 46
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(V46_TABLES) <= tables
        for table in V46_TABLES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0  # noqa: S608
        # v45 行原样保留（append-only 前向迁移）。
        assert conn.execute("SELECT COUNT(*) FROM context_route_decisions").fetchone()[0] == 1
        # 迁移链 + marker + 恢复表注册。
        chain = dict(conn.execute("SELECT migration_id,schema_version FROM human_memory_migration_chain").fetchall())
        assert chain[EFFECT_CLOSURE_MIGRATION] == 46
        assert conn.execute("SELECT schema_version,migration_id FROM effect_closure_marker WHERE singleton=1").fetchone() == (1, EFFECT_CLOSURE_MIGRATION)
        registry = dict(conn.execute(
            "SELECT table_name,taxonomy FROM human_memory_recovery_table_registry WHERE table_name IN "
            "(%s)" % ",".join("?" * len(V46_TABLES)), V46_TABLES,  # noqa: S608
        ).fetchall())
        assert registry == {name: "A" for name in V46_TABLES}
    # 再次打开：幂等 resume。
    await initialize_human_memory_program_state_db(db)
    assert inspect_startup_epoch(db, approved_fresh_lane=False).epoch is StartupEpoch.HUMAN_RESUME


@pytest.mark.asyncio
async def test_old_runtime_rejects_v46_database_stably(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 46
    # 旧 runtime：目标 45 → FUTURE / 拒绝，不改库。
    before = db.read_bytes()
    future = inspect_startup_epoch(db, approved_fresh_lane=True, maximum_human_schema_version=45)
    assert future.epoch is StartupEpoch.FUTURE and future.reason_code == "human_memory_future_epoch_unsupported"
    monkeypatch.setattr(schema, "HUMAN_MEMORY_TARGET_SCHEMA_VERSION", 45)
    with pytest.raises(HumanMemoryProgramEpochError, match="human_memory_program_future_database_unsupported"):
        await initialize_human_memory_program_state_db(db)
    assert db.read_bytes() == before


@pytest.mark.asyncio
async def test_v46_tables_are_append_only_with_monotonic_state_guards(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO task_scopes(task_scope_id,subject,title,created_at) VALUES ('s1','actor-1','t',1.0)")
        conn.execute(
            "INSERT INTO task_scope_closure_receipts(receipt_id,task_scope_id,sdk_run_id,host_run_id,closure_watermark,"
            "outcome,plan_id,reason_code,attempt_id,created_at) VALUES ('c1','s1','run-1','host-1',3,'pending',NULL,'r',NULL,1.0)"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            conn.execute("UPDATE task_scope_closure_receipts SET outcome='mutate' WHERE receipt_id='c1'")
        with pytest.raises(sqlite3.IntegrityError):  # CHECK outcome
            conn.execute(
                "INSERT INTO task_scope_closure_receipts(receipt_id,task_scope_id,sdk_run_id,host_run_id,closure_watermark,"
                "outcome,plan_id,reason_code,attempt_id,created_at) VALUES ('c2','s1','run-1','host-1',3,'bogus',NULL,'r',NULL,1.0)"
            )
        # reservations：UNIQUE(run_id, source_sequence) + status 单调 reserved→ingested/abandoned。
        conn.execute(
            "INSERT INTO harness_evidence_reservations(reservation_id,run_id,task_scope_id,source_sequence,source_event_id,"
            "kind,status,reserved_at,resolved_at) VALUES ('r1','run-1','s1',1,'e1','tool_invocation','reserved',1.0,NULL)"
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(
                "INSERT INTO harness_evidence_reservations(reservation_id,run_id,task_scope_id,source_sequence,source_event_id,"
                "kind,status,reserved_at,resolved_at) VALUES ('r2','run-1','s1',1,'e2','tool_invocation','reserved',1.0,NULL)"
            )
        conn.execute("UPDATE harness_evidence_reservations SET status='ingested',resolved_at=2.0 WHERE reservation_id='r1'")
        with pytest.raises(sqlite3.IntegrityError, match="harness_evidence_reservation_monotonic"):
            conn.execute("UPDATE harness_evidence_reservations SET status='abandoned' WHERE reservation_id='r1'")
        with pytest.raises(sqlite3.IntegrityError, match="harness_evidence_reservation_identity_immutable"):
            conn.execute("UPDATE harness_evidence_reservations SET source_sequence=9 WHERE reservation_id='r1'")
        # outbox：pending→claimed→delivered/dead_letter；claimed→pending 允许（lease 到期）；delivered 终态。
        conn.execute(
            "INSERT INTO memory_ingestion_outbox(outbox_id,host_run_id,sdk_run_id,turn_id,subject,evidence_ids_json,"
            "envelope_hash,model_config_hash,analysis_lineage_json,state,attempts,lease_owner,lease_expires_at,receipt_json,"
            "last_error,created_at,updated_at) VALUES ('o1','host-1','run-1','turn-1','actor-1','[]',?,?,'{}','pending',0,"
            "NULL,NULL,NULL,NULL,1.0,1.0)",
            ("a" * 64, "b" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError, match="memory_ingestion_outbox_monotonic"):
            conn.execute("UPDATE memory_ingestion_outbox SET state='delivered' WHERE outbox_id='o1'")
        conn.execute("UPDATE memory_ingestion_outbox SET state='claimed',lease_owner='w1',lease_expires_at=5.0 WHERE outbox_id='o1'")
        conn.execute("UPDATE memory_ingestion_outbox SET state='pending',lease_owner=NULL,lease_expires_at=NULL WHERE outbox_id='o1'")
        conn.execute("UPDATE memory_ingestion_outbox SET state='claimed',lease_owner='w2',lease_expires_at=9.0 WHERE outbox_id='o1'")
        conn.execute("UPDATE memory_ingestion_outbox SET state='delivered' WHERE outbox_id='o1'")
        with pytest.raises(sqlite3.IntegrityError, match="memory_ingestion_outbox_monotonic"):
            conn.execute("UPDATE memory_ingestion_outbox SET state='claimed' WHERE outbox_id='o1'")
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            conn.execute("DELETE FROM memory_ingestion_outbox")
        conn.execute("INSERT INTO memory_ingestion_evidence_links(outbox_id,evidence_id) VALUES ('o1','ev-1')")
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            conn.execute("DELETE FROM memory_ingestion_evidence_links")
        # attempts：reserved→handed_off→{succeeded,failed,unknown}；reserved→failed 允许；UNIQUE(request_hash, ordinal)。
        conn.execute(
            "INSERT INTO post_turn_invocation_attempts(attempt_id,purpose,host_run_id,sdk_run_id,generation,task_scope_id,"
            "closure_watermark,request_hash,attempt_ordinal,evidence_set_key,status,unknown_class,provider_id,model_id,"
            "model_config_hash,provider_request_id,result_hash,plan_id,reserved_at,handed_off_at,settled_at,reason_code) "
            "VALUES ('a1','closure','host-1','run-1',1,'s1',3,?,1,?,'reserved',NULL,'p','m',?,NULL,NULL,NULL,1.0,NULL,NULL,NULL)",
            ("c" * 64, "d" * 64, "e" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(
                "INSERT INTO post_turn_invocation_attempts(attempt_id,purpose,host_run_id,sdk_run_id,generation,task_scope_id,"
                "closure_watermark,request_hash,attempt_ordinal,evidence_set_key,status,unknown_class,provider_id,model_id,"
                "model_config_hash,provider_request_id,result_hash,plan_id,reserved_at,handed_off_at,settled_at,reason_code) "
                "VALUES ('a2','closure','host-1','run-1',1,'s1',3,?,1,?,'reserved',NULL,'p','m',?,NULL,NULL,NULL,1.0,NULL,NULL,NULL)",
                ("c" * 64, "d" * 64, "e" * 64),
            )
        with pytest.raises(sqlite3.IntegrityError, match="post_turn_invocation_attempt_monotonic"):
            conn.execute("UPDATE post_turn_invocation_attempts SET status='succeeded',settled_at=2.0 WHERE attempt_id='a1'")
        conn.execute("UPDATE post_turn_invocation_attempts SET status='handed_off',handed_off_at=2.0 WHERE attempt_id='a1'")
        with pytest.raises(sqlite3.IntegrityError, match="post_turn_invocation_attempt_monotonic"):
            conn.execute("UPDATE post_turn_invocation_attempts SET status='reserved' WHERE attempt_id='a1'")
        conn.execute("UPDATE post_turn_invocation_attempts SET status='unknown',unknown_class='sent_unknown',settled_at=3.0 WHERE attempt_id='a1'")
        with pytest.raises(sqlite3.IntegrityError, match="post_turn_invocation_attempt_monotonic"):
            conn.execute("UPDATE post_turn_invocation_attempts SET status='succeeded' WHERE attempt_id='a1'")
        conn.execute(
            "INSERT INTO post_turn_invocation_attempts(attempt_id,purpose,host_run_id,sdk_run_id,generation,task_scope_id,"
            "closure_watermark,request_hash,attempt_ordinal,evidence_set_key,status,unknown_class,provider_id,model_id,"
            "model_config_hash,provider_request_id,result_hash,plan_id,reserved_at,handed_off_at,settled_at,reason_code) "
            "VALUES ('a3','analysis','host-1','run-1',1,NULL,NULL,?,2,?,'reserved',NULL,'p','m',?,NULL,NULL,NULL,1.0,NULL,NULL,NULL)",
            ("c" * 64, "d" * 64, "e" * 64),
        )
        conn.execute("UPDATE post_turn_invocation_attempts SET status='failed',unknown_class='not_sent',settled_at=2.0,reason_code='x' WHERE attempt_id='a3'")
        conn.execute("INSERT INTO post_turn_invocation_members(attempt_id,subject,run_id,evidence_id) VALUES ('a1','actor-1','run-1','ev-1')")
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            conn.execute("UPDATE post_turn_invocation_members SET evidence_id='ev-2'")
        conn.execute("INSERT INTO effect_gate_rejections(rejection_id,sdk_run_id,route_receipt_id,reason_code,created_at) VALUES ('g1','run-1','rr1','x',1.0)")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute("INSERT INTO effect_gate_rejections(rejection_id,sdk_run_id,route_receipt_id,reason_code,created_at) VALUES ('g2','run-1','rr1','y',1.0)")
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            conn.execute("DELETE FROM effect_gate_rejections")
        conn.execute("INSERT INTO host_pre_admission_audit(audit_id,sdk_run_id,payload_kind,reason_code,payload_hash,created_at) VALUES ('h1',NULL,'context_route','x',?,1.0)", ("f" * 64,))
        with pytest.raises(sqlite3.IntegrityError):  # CHECK payload_kind
            conn.execute("INSERT INTO host_pre_admission_audit(audit_id,sdk_run_id,payload_kind,reason_code,payload_hash,created_at) VALUES ('h2',NULL,'bogus','x',?,1.0)", ("f" * 64,))
        with pytest.raises(sqlite3.IntegrityError, match="append_only"):
            conn.execute("UPDATE host_pre_admission_audit SET reason_code='y'")
        conn.commit()
