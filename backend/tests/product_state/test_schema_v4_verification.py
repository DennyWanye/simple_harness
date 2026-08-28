from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from deskpet.capabilities.store import CapabilityStore, CapabilityStoreConflict
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.downgrade import execute_product_v4_to_v3_downgrade
from deskpet.product_state.schema import SCHEMA_V3_PARTS
from deskpet.product_state.schema_integrity import expected_semantic_fingerprint


def _v3(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    for part in SCHEMA_V3_PARTS:
        db.executescript(part)
    db.execute("INSERT INTO product_schema_meta VALUES(1,3)")
    tables = tuple(row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ))
    db.execute("INSERT INTO product_schema_manifest VALUES(1,?,?)", (
        expected_semantic_fingerprint(SCHEMA_V3_PARTS),
        json.dumps(tables, separators=(",", ":")),
    ))
    db.execute("PRAGMA user_version=3")
    return db


def _intent(db: sqlite3.Connection, intent_id: str, *, status: str) -> None:
    values = (
        intent_id, f"effect-{intent_id}", f"call-{intent_id}", "root", "run", "chat",
        "project:v1:p:r", "principal", "{}", "a" * 40, "archive", "tree", "members",
        "permissions", f"nonce-{intent_id}", 1, 999.0, status, 1, "receipt", None, None, None,
        1.0, 2.0, 1, None, None,
    )
    db.execute(
        "INSERT INTO capability_skill_install_intents VALUES("
        + ",".join("?" for _ in values) + ")", values,
    )


def _attempt(db: sqlite3.Connection, intent_id: str, old_status: str) -> None:
    attempt_id = f"attempt-{intent_id}"
    db.execute(
        "INSERT INTO capability_skill_install_verification_attempts("
        "attempt_id,intent_id,attempt_generation,state_version,status,verifier_session_id,"
        "request_id,turn_id,expected_run_id,manager_operation_id,manager_receipt_hash,"
        "committed_set_stamp,project_scope_key,expected_member_set_stamp,created_at,updated_at)"
        " VALUES(?,?,1,1,?,'session','request','turn',?,'operation','receipt',"
        "'committed','project:v1:p:r','members',1,2)",
        (attempt_id, intent_id, old_status, f"run-{intent_id}"),
    )
    db.execute(
        "UPDATE capability_skill_install_intents SET current_verification_attempt_id=? "
        "WHERE intent_id=?", (attempt_id, intent_id),
    )


@pytest.mark.parametrize(("old_status", "new_status"), (
    ("prepared", "allocated"),
    ("launching", "unknown"),
    ("running", "unknown"),
    ("unknown", "unknown"),
    ("terminal_failed", "terminal_failed"),
))
def test_v3_backfill_is_conservative_and_restartable(
    tmp_path: Path, old_status: str, new_status: str,
) -> None:
    path = tmp_path / "product.db"
    db = _v3(path)
    _intent(db, "one", status="published_pending_runtime_verification")
    _attempt(db, "one", old_status)
    db.commit()
    db.close()
    owner = ProductStateDatabase(path)
    owner.initialize()
    row = owner.connection.execute(
        "SELECT status,migration_classification_hash FROM "
        "capability_skill_install_verification_attempts"
    ).fetchone()
    assert tuple(row) == (new_status, row[1])
    assert len(str(row[1])) == 64
    owner.close()
    reopened = ProductStateDatabase(path)
    reopened.initialize()
    assert reopened.schema_version == 4
    reopened.close()


def test_v3_unattested_success_is_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "product.db"
    db = _v3(path)
    _intent(db, "one", status="published_pending_runtime_verification")
    _attempt(db, "one", "terminal_succeeded")
    db.commit()
    db.close()
    owner = ProductStateDatabase(path)
    owner.initialize()
    assert owner.connection.execute(
        "SELECT status FROM capability_skill_install_verification_attempts"
    ).fetchone()[0] == "quarantined"
    assert owner.connection.execute(
        "SELECT reason FROM capability_skill_install_verification_migration_quarantine"
    ).fetchone()[0] == "legacy_success_missing_or_invalid_attestation"
    owner.close()


@pytest.mark.asyncio
async def test_v4_phase_cas_freezes_receipts_and_rejects_skips(tmp_path: Path) -> None:
    path = tmp_path / "product.db"
    owner = ProductStateDatabase(path)
    owner.initialize()
    _intent(owner.connection, "one", status="published_pending_runtime_verification")
    owner.connection.commit()
    owner.close()
    store = CapabilityStore(path)
    attempt = await store.allocate_skill_install_verification_attempt(
        "one", expected_state_version=1, manager_operation_id="operation",
        manager_receipt_hash="receipt", committed_set_stamp="committed",
        project_scope_key="project:v1:p:r", expected_member_set_stamp="members",
        verifier_session_id="session",
    )
    submitted = await store.cas_skill_install_verification_attempt(
        attempt.attempt_id, expected_state_version=attempt.state_version,
        status="start_submitted", lease_intent_id="lease", lease_intent_hash="lease-hash",
        capability_snapshot_ref="snapshot", run_catalog_content_stamp="catalog",
        process_catalog_stamp="process", projection_receipt_id="projection",
        projection_receipt_hash="projection-hash",
    )
    with pytest.raises(CapabilityStoreConflict):
        await store.cas_skill_install_verification_attempt(
            attempt.attempt_id, expected_state_version=submitted.state_version,
            status="catalog_ready", actual_run_id="run",
        )
    durable = await store.cas_skill_install_verification_attempt(
        attempt.attempt_id, expected_state_version=submitted.state_version,
        status="run_durable", actual_run_id="run",
    )
    ready = await store.cas_skill_install_verification_attempt(
        attempt.attempt_id, expected_state_version=durable.state_version,
        status="catalog_ready",
    )
    assert ready.projection_receipt_hash == "projection-hash"
    with pytest.raises(CapabilityStoreConflict, match="immutable"):
        await store.cas_skill_install_verification_attempt(
            attempt.attempt_id, expected_state_version=ready.state_version,
            status="page_in_proven", evidence_hash="evidence",
            lease_intent_hash="changed",
        )


V4_FAULTS = (
    "migration_v4.after_begin", "migration_v4.after_rename",
    "migration_v4.after_backfill", "migration_v4.before_commit",
)


@pytest.mark.parametrize("point", V4_FAULTS)
def test_v4_faults_roll_back_exactly_to_v3(tmp_path: Path, point: str) -> None:
    path = tmp_path / "product.db"
    db = _v3(path)
    db.commit()
    db.close()
    owner = ProductStateDatabase(path)
    with pytest.raises(RuntimeError, match="fault"):
        owner.initialize(fault=lambda value: (_ for _ in ()).throw(RuntimeError("fault")) if value == point else None)
    ProductStateDatabase._validate_v3_connection(owner.connection)
    owner.close()


def test_offline_dual_database_v4_to_v3_restore(tmp_path: Path) -> None:
    product = tmp_path / "product.db"
    db = _v3(product)
    db.commit()
    db.close()
    owner = ProductStateDatabase(product)
    owner.initialize()
    owner.close()
    pre_v4 = next(tmp_path.glob("product.db.pre-v4.*.sqlite3"))
    execution = tmp_path / "execution.db"
    sqlite3.connect(execution).close()
    receipt = execute_product_v4_to_v3_downgrade(
        product_database=product, execution_database=execution,
        product_pre_v4_backup=pre_v4, ingress_closed=True, app_stopped=True,
        run_probe=lambda _attempt: None,
        sdk_reopen_probe=lambda _path: (True, "sdk-reopen-ok"),
    )
    restored = sqlite3.connect(product)
    ProductStateDatabase._validate_v3_connection(restored)
    restored.close()
    assert receipt.outcome == "restored"
    assert receipt.execution_backup.exists()
