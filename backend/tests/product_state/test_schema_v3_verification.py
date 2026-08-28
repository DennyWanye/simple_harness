from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from deskpet.capabilities.store import (
    CapabilityStore,
    CapabilityStoreConflict,
    initialize_capability_database,
)
from deskpet.product_state.backup import restore_migration_backup_offline
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.schema import SCHEMA_V2_PARTS
from deskpet.product_state.schema_integrity import expected_semantic_fingerprint


def _create_v2(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    for part in SCHEMA_V2_PARTS:
        connection.executescript(part)
    connection.execute("INSERT INTO product_schema_meta VALUES(1,2)")
    tables = tuple(row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ))
    connection.execute(
        "INSERT INTO product_schema_manifest VALUES(1,?,?)",
        (expected_semantic_fingerprint(SCHEMA_V2_PARTS), json.dumps(tables, separators=(",", ":"))),
    )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    return connection


def _insert_intent(connection: sqlite3.Connection, intent_id: str, status: str, *, settlement: str | None = None, verification: str | None = None) -> None:
    values = (
        intent_id, f"effect-{intent_id}", f"call-{intent_id}", "root", "run", "chat",
        "project:v1:p:r", "principal", "{}", "a" * 40, "archive", "tree", "members",
        "permissions", f"nonce-{intent_id}", 1, 999.0, status, 1, settlement, None,
        verification, None, 1.0, 2.0,
    )
    connection.execute(
        "INSERT INTO capability_skill_install_intents("
        "intent_id,effect_id,call_id,root_run_id,run_id,channel,project_scope_key,principal_id,"
        "source_json,exact_commit,archive_hash,raw_tree_hash,member_set_stamp,permission_set_hash,"
        "confirmation_nonce,confirmation_version,expires_at,status,state_version,settlement_ref,"
        "cleanup_ref,verification_ref,error_json,created_at,updated_at) VALUES("
        + ",".join("?" for _ in values) + ")",
        values,
    )


def test_v2_backfill_is_conservative_and_backup_is_offline_restorable(tmp_path: Path) -> None:
    path = tmp_path / "product.db"
    connection = _create_v2(path)
    _insert_intent(connection, "done", "succeeded", settlement="receipt", verification="legacy-proof")
    _insert_intent(connection, "idle", "awaiting_confirmation")
    connection.commit()
    connection.close()

    database = ProductStateDatabase(path)
    database.initialize()
    assert database.schema_version == 4
    assert database.connection.execute(
        "SELECT migrated_verification_provenance FROM capability_skill_install_intents WHERE intent_id='done'"
    ).fetchone()[0] == "legacy_v2"
    legacy = database.connection.execute(
        "SELECT provenance,runtime_proof_valid,attempt_id FROM capability_skill_install_verification_attestations"
    ).fetchone()
    assert tuple(legacy) == ("legacy_v2", 0, None)
    assert database.connection.execute(
        "SELECT COUNT(*) FROM capability_skill_install_verification_attempts"
    ).fetchone()[0] == 0
    backup = next(tmp_path.glob("product.db.pre-v3.*.sqlite3"))
    digest = backup.name.split(".")[-2]
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == digest
    database.close()

    restore_migration_backup_offline(
        database_path=path, backup_path=backup, expected_source_version=2,
        validate=ProductStateDatabase._validate_v2_connection,
    )
    restored = sqlite3.connect(path)
    ProductStateDatabase._validate_v2_connection(restored)
    restored.close()


def test_ambiguous_v2_pending_fails_closed_and_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "product.db"
    connection = _create_v2(path)
    _insert_intent(connection, "pending", "published_pending_runtime_verification", settlement="receipt")
    connection.commit()
    connection.close()
    database = ProductStateDatabase(path)
    with pytest.raises(RuntimeError, match="ambiguous"):
        database.initialize()
    ProductStateDatabase._validate_v2_connection(database.connection)
    database.close()


async def _insert_execution_pending(path: Path) -> None:
    await initialize_capability_database(path)
    connection = sqlite3.connect(path)
    _insert_intent(
        connection,
        "pending",
        "published_pending_runtime_verification",
        settlement="receipt",
    )
    connection.commit()
    connection.close()


@pytest.mark.asyncio
async def test_attempt_allocation_cas_and_atomic_supersession(tmp_path: Path) -> None:
    path = tmp_path / "execution.db"
    await _insert_execution_pending(path)
    store = CapabilityStore(path, clock=lambda: 10.0)
    arguments = dict(
        expected_state_version=1, manager_operation_id="operation",
        manager_receipt_hash="receipt", committed_set_stamp="committed",
        project_scope_key="project:v1:p:r", expected_member_set_stamp="members",
        verifier_session_id="verify-session",
    )
    results = await asyncio.gather(*(
        store.allocate_skill_install_verification_attempt("pending", **arguments)
        for _ in range(8)
    ))
    assert len({item.attempt_id for item in results}) == 1
    attempt = results[0]
    unknown = await store.cas_skill_install_verification_attempt(
        attempt.attempt_id, expected_state_version=1, status="unknown",
        error={"code": "crash"},
    )
    with pytest.raises(CapabilityStoreConflict):
        await store.cas_skill_install_verification_attempt(
            attempt.attempt_id, expected_state_version=1, status="launching"
        )
    successor = await store.supersede_skill_install_verification_attempt(
        "pending", expected_intent_state_version=2, attempt_id=attempt.attempt_id,
        expected_attempt_state_version=unknown.state_version,
        verifier_session_id="verify-session-2",
    )
    assert successor.attempt_generation == 2
    assert (await store.get_current_skill_install_verification_attempt("pending")).attempt_id == successor.attempt_id
    old = await store.get_skill_install_verification_attempt(attempt.attempt_id)
    assert old.status == "superseded" and old.superseded_by_attempt_id == successor.attempt_id


V3_MIGRATION_FAULT_POINTS = (
    "migration_v3.after_begin",
    *(f"migration_v3.ddl:0:{index}" for index in range(3)),
    *(f"migration_v3.ddl:1:{index}" for index in range(6)),
    "migration_v3.after_backfill",
    "migration_v3.before_commit",
)


@pytest.mark.parametrize("fault_point", V3_MIGRATION_FAULT_POINTS)
def test_every_v3_migration_fault_rolls_back_exactly_to_v2(
    tmp_path: Path, fault_point: str
) -> None:
    path = tmp_path / "product.db"
    connection = _create_v2(path)
    _insert_intent(connection, "idle", "awaiting_confirmation")
    connection.commit()
    connection.close()
    database = ProductStateDatabase(path)
    with pytest.raises(RuntimeError, match="fault"):
        database.initialize(fault=lambda point: (_ for _ in ()).throw(RuntimeError("fault")) if point == fault_point else None)
    ProductStateDatabase._validate_v2_connection(database.connection)
    database.close()
