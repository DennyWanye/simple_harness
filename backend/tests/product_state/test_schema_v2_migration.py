from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

import deskpet.product_state.database as product_database_module
from deskpet.product_state.backup import (
    ProductStateBackupError,
    restore_pre_v2_backup_offline,
)
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.schema import SCHEMA_V1_PARTS


def _create_v1(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    for part in SCHEMA_V1_PARTS:
        connection.executescript(part)
    connection.execute(
        "INSERT INTO product_schema_meta(singleton,schema_version) VALUES(1,1)"
    )
    tables = tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )
    connection.execute(
        "INSERT INTO product_schema_manifest(singleton,schema_hash,tables_json) "
        "VALUES(1,?,?)",
        (
            hashlib.sha256("\n".join(SCHEMA_V1_PARTS).encode()).hexdigest(),
            json.dumps(tables, separators=(",", ":")),
        ),
    )
    connection.execute("PRAGMA user_version=1")
    digest = "1" * 64
    connection.execute(
        """INSERT INTO capability_versions(
            pack_id,version,manifest_hash,descriptor_json,source_json,
            install_path,validation_status,expected_tool_fingerprints_json,
            created_at
        ) VALUES('legacy-pack','1.0.0',?,'{}','{}','/legacy','healthy','[]',1)""",
        (digest,),
    )
    connection.execute(
        """INSERT INTO capability_bindings(
            binding_id,scope,scope_key,pack_id,active_version,
            active_manifest_hash,generation,enabled,updated_at
        ) VALUES('binding-1','project','project-key','legacy-pack','1.0.0',?,1,1,2)""",
        (digest,),
    )
    connection.execute(
        """INSERT INTO capability_operations(
            operation_id,idempotency_key,kind,pack_id,requested_scope,
            requested_scope_key,phase,status,request_json,started_at,updated_at,ended_at
        ) VALUES('operation-1','idem-1','install','legacy-pack','project',
            'project-key','published','succeeded','{}',3,4,4)"""
    )
    connection.execute(
        "INSERT INTO capability_operation_receipts(operation_id,receipt_hash,receipt_json,created_at) "
        "VALUES('operation-1',?,'{}',4)",
        ("2" * 64,),
    )
    connection.commit()
    connection.close()


def _legacy_hash(path: Path) -> str:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    payload = {}
    for table in (
        "capability_versions",
        "capability_bindings",
        "capability_operations",
        "capability_operation_receipts",
    ):
        payload[table] = [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
    connection.close()
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_real_v1_migrates_idempotently_and_preserves_legacy_rows(tmp_path: Path) -> None:
    path = tmp_path / "product.db"
    _create_v1(path)
    before = _legacy_hash(path)
    database = ProductStateDatabase(path)
    database.initialize()
    assert database.schema_version == 3
    assert _legacy_hash(path) == before
    assert database.connection.execute("PRAGMA foreign_key_check").fetchone() is None
    backup = path.with_name(f"{path.name}.pre-v2.sqlite3")
    assert backup.exists() and backup.stat().st_mode & 0o777 == 0o600
    database.close()
    reopened = ProductStateDatabase(path)
    reopened.initialize()
    assert _legacy_hash(path) == before
    reopened.close()


def _migration_points(path: Path) -> tuple[str, ...]:
    _create_v1(path)
    points: list[str] = []
    database = ProductStateDatabase(path)
    database.initialize(fault=points.append)
    database.close()
    return tuple(point for point in points if point.startswith("migration."))


MIGRATION_FAULT_POINTS = (
    "migration.after_begin",
    "migration.after_rename:capability_operations",
    "migration.after_copy:capability_operations",
    "migration.after_drop:capability_operations",
    "migration.after_rename:capability_publish_intents",
    "migration.after_copy:capability_publish_intents",
    "migration.after_drop:capability_publish_intents",
    *(f"migration.ddl:{index}" for index in range(37)),
    "migration.before_manifest",
    "migration.after_manifest",
    "migration.after_meta",
    "migration.after_user_version",
    "migration.before_commit",
)


@pytest.mark.parametrize("point", MIGRATION_FAULT_POINTS)
def test_every_migration_fault_rolls_back_to_exact_v1(
    tmp_path: Path, point: str
) -> None:
    source = tmp_path / "source.db"
    points = _migration_points(source)
    assert points == MIGRATION_FAULT_POINTS
    path = tmp_path / "fault.db"
    shutil.copy2(source.with_name(f"{source.name}.pre-v2.sqlite3"), path)
    before = _legacy_hash(path)
    database = ProductStateDatabase(path)
    with pytest.raises(RuntimeError, match="fault"):
        database.initialize(
            fault=lambda current: (_ for _ in ()).throw(RuntimeError("fault"))
            if current == point
            else None
        )
    ProductStateDatabase._validate_v1_connection(database.connection)
    assert _legacy_hash(path) == before
    database.close()


def test_v1_validator_rejects_v2_and_partial_v2_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "product.db"
    _create_v1(path)
    database = ProductStateDatabase(path)
    database.initialize()
    with pytest.raises(RuntimeError):
        ProductStateDatabase._validate_v1_connection(database.connection)
    database.connection.execute("PRAGMA user_version=1")
    database.close()
    with pytest.raises(RuntimeError, match="user_version"):
        ProductStateDatabase(path).initialize()


def test_backup_failure_stops_before_any_migration_ddl(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "product.db"
    _create_v1(path)
    before = _legacy_hash(path)
    monkeypatch.setattr(
        product_database_module,
        "create_verified_backup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("backup failed")),
    )
    database = ProductStateDatabase(path)
    with pytest.raises(RuntimeError, match="backup failed"):
        database.initialize()
    ProductStateDatabase._validate_v1_connection(database.connection)
    assert _legacy_hash(path) == before
    database.close()


def test_verified_offline_restore_preserves_v2_recovery_artifact(tmp_path: Path) -> None:
    path = tmp_path / "product.db"
    _create_v1(path)
    database = ProductStateDatabase(path)
    database.initialize()
    backup = path.with_name(f"{path.name}.pre-v2.sqlite3")
    database.close()
    recovery = restore_pre_v2_backup_offline(
        database_path=path,
        backup_path=backup,
        validate_v1=ProductStateDatabase._validate_v1_connection,
    )
    assert recovery.exists()
    restored = sqlite3.connect(path)
    ProductStateDatabase._validate_v1_connection(restored)
    restored.close()


def test_offline_restore_rejects_tampered_or_permissive_backup(tmp_path: Path) -> None:
    path = tmp_path / "product.db"
    _create_v1(path)
    database = ProductStateDatabase(path)
    database.initialize()
    backup = path.with_name(f"{path.name}.pre-v2.sqlite3")
    database.close()
    backup.chmod(0o644)
    with pytest.raises(ProductStateBackupError, match="permissions"):
        restore_pre_v2_backup_offline(
            database_path=path,
            backup_path=backup,
            validate_v1=ProductStateDatabase._validate_v1_connection,
        )
