"""SQLite owner for product policy state and semantic schema migration."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path

from deskpet.execution.contracts import root_idempotency_key

from .backup import create_verified_backup, create_verified_migration_backup
from .schema import (
    CAPABILITY_SCHEMA_SQL, SCHEMA_V1_PARTS, SCHEMA_V2_PARTS, SCHEMA_V3_PARTS,
    SCHEMA_V4_PARTS, SCHEMA_VERSION, VERIFICATION_V3_COLUMNS_SQL,
    VERIFICATION_V3_SCHEMA_SQL, VERIFICATION_V4_QUARANTINE_SQL,
    VERIFICATION_V4_SCHEMA_SQL,
)
from .schema_integrity import expected_semantic_fingerprint, run_check_probes, semantic_fingerprint

_V1_DDL_HASH = hashlib.sha256("\n".join(SCHEMA_V1_PARTS).encode()).hexdigest()
_V1_SEMANTIC_HASH = expected_semantic_fingerprint(SCHEMA_V1_PARTS)
_V2_SEMANTIC_HASH = expected_semantic_fingerprint(SCHEMA_V2_PARTS)
_V3_SEMANTIC_HASH = expected_semantic_fingerprint(SCHEMA_V3_PARTS)
_V4_SEMANTIC_HASH = expected_semantic_fingerprint(SCHEMA_V4_PARTS)


class ProductStateDatabase:
    is_product_state_owner = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=5000")

    def initialize(self, *, fault: Callable[[str], None] | None = None) -> None:
        existing = self.table_names()
        if not existing:
            self._initialize_fresh(fault)
            return
        if not {"product_schema_meta", "product_schema_manifest"}.issubset(existing):
            raise RuntimeError("partial or foreign product state schema")
        version = self.schema_version
        if version == 4:
            self._validate_v4_connection(self.connection)
            return
        if version == 3:
            self._validate_v3_connection(self.connection)
        elif version == 2:
            self._validate_v2_connection(self.connection)
        elif version == 1:
            self._validate_v1_connection(self.connection)
            create_verified_backup(
                self.connection, database_path=self.path,
                validate_v1=self._validate_v1_connection,
            )
            self._migrate_v1_to_v2(fault)
            self._validate_v2_connection(self.connection)
        else:
            raise RuntimeError("unsupported product state schema")
        if self.schema_version == 2:
            create_verified_migration_backup(
                self.connection, database_path=self.path, source_version=2,
                validate=self._validate_v2_connection,
            )
            self._migrate_v2_to_v3(fault)
            self._validate_v3_connection(self.connection)
        create_verified_migration_backup(
            self.connection, database_path=self.path, source_version=3,
            validate=self._validate_v3_connection,
        )
        self._migrate_v3_to_v4(fault)
        self._validate_v4_connection(self.connection)

    def _initialize_fresh(self, fault: Callable[[str], None] | None) -> None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._hit(fault, "schema.after_begin")
            for part_index, part in enumerate(SCHEMA_V4_PARTS):
                for statement_index, statement in enumerate(self._statements(part)):
                    self.connection.execute(statement)
                    self._hit(fault, f"schema.ddl:{part_index}:{statement_index}")
            self.connection.execute(
                "INSERT INTO product_schema_meta(singleton,schema_version) VALUES(1,4)"
            )
            self.connection.execute(
                "INSERT INTO product_schema_manifest(singleton,schema_hash,tables_json) VALUES(1,?,?)",
                (_V4_SEMANTIC_HASH, json.dumps(self.table_names(), separators=(",", ":"))),
            )
            self.connection.execute("PRAGMA user_version=4")
            self._validate_live_integrity(4, _V4_SEMANTIC_HASH)
            self._hit(fault, "schema.before_commit")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _migrate_v1_to_v2(self, fault: Callable[[str], None] | None) -> None:
        expected = sqlite3.connect(":memory:")
        try:
            for part in SCHEMA_V2_PARTS:
                expected.executescript(part)
            create_sql = {
                name: str(expected.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone()[0])
                for name in ("capability_operations", "capability_publish_intents")
            }
        finally:
            expected.close()
        self.connection.execute("PRAGMA foreign_keys=OFF")
        self.connection.execute("PRAGMA legacy_alter_table=ON")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._hit(fault, "migration.after_begin")
            for table in ("capability_operations", "capability_publish_intents"):
                legacy = f"{table}__product_v1"
                before = int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                self.connection.execute(f"ALTER TABLE {table} RENAME TO {legacy}")
                self._hit(fault, f"migration.after_rename:{table}")
                self.connection.execute(create_sql[table])
                columns = [str(row[1]) for row in self.connection.execute(
                    f"PRAGMA table_xinfo('{legacy}')"
                ).fetchall() if int(row[6]) == 0]
                names = ",".join(f'"{name}"' for name in columns)
                self.connection.execute(f"INSERT INTO {table}({names}) SELECT {names} FROM {legacy}")
                self._hit(fault, f"migration.after_copy:{table}")
                after = int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                if after != before:
                    raise RuntimeError(f"migration row count changed for {table}")
                self.connection.execute(f"DROP TABLE {legacy}")
                self._hit(fault, f"migration.after_drop:{table}")
            for index, statement in enumerate(self._statements(CAPABILITY_SCHEMA_SQL)):
                self.connection.execute(statement)
                self._hit(fault, f"migration.ddl:{index}")
            self._hit(fault, "migration.before_manifest")
            self.connection.execute(
                "UPDATE product_schema_manifest SET schema_hash=?,tables_json=? WHERE singleton=1",
                (_V2_SEMANTIC_HASH, json.dumps(self.table_names(), separators=(",", ":"))),
            )
            self._hit(fault, "migration.after_manifest")
            self.connection.execute("UPDATE product_schema_meta SET schema_version=2 WHERE singleton=1")
            self._hit(fault, "migration.after_meta")
            self.connection.execute("PRAGMA user_version=2")
            self._hit(fault, "migration.after_user_version")
            self._validate_live_integrity(2, _V2_SEMANTIC_HASH)
            self._hit(fault, "migration.before_commit")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA legacy_alter_table=OFF")
            self.connection.execute("PRAGMA foreign_keys=ON")

    def _migrate_v2_to_v3(self, fault: Callable[[str], None] | None) -> None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._hit(fault, "migration_v3.after_begin")
            for part_index, part in enumerate(
                (VERIFICATION_V3_COLUMNS_SQL, VERIFICATION_V3_SCHEMA_SQL)
            ):
                for statement_index, statement in enumerate(self._statements(part)):
                    self.connection.execute(statement)
                    self._hit(fault, f"migration_v3.ddl:{part_index}:{statement_index}")
            rows = self.connection.execute(
                "SELECT * FROM capability_skill_install_intents ORDER BY intent_id"
            ).fetchall()
            for row in rows:
                status, intent_id = str(row["status"]), str(row["intent_id"])
                settlement = None if row["settlement_ref"] is None else str(row["settlement_ref"])
                verification = None if row["verification_ref"] is None else str(row["verification_ref"])
                now = float(row["updated_at"])
                if status == "succeeded":
                    if not settlement or not verification:
                        raise RuntimeError("v2 succeeded intent lacks verification provenance")
                    attestation_id = hashlib.sha256(
                        f"legacy-v2:{intent_id}:{verification}".encode()
                    ).hexdigest()
                    self.connection.execute(
                        "INSERT INTO capability_skill_install_verification_attestations("
                        "attestation_id,intent_id,attempt_id,provenance,runtime_proof_valid,"
                        "verification_ref,evidence_hash,attestation_json,created_at) "
                        "VALUES(?,?,NULL,'legacy_v2',0,?,NULL,?,?)",
                        (attestation_id, intent_id, verification, json.dumps({
                            "schema": "skill-install-legacy-v2-verification/v1",
                            "verification_ref": verification,
                        }, sort_keys=True, separators=(",", ":")), now),
                    )
                    self.connection.execute(
                        "UPDATE capability_skill_install_intents SET "
                        "migrated_verification_provenance='legacy_v2' WHERE intent_id=?",
                        (intent_id,),
                    )
                elif status == "published_pending_runtime_verification":
                    handoff = self.connection.execute(
                        "SELECT operation_id,member_set_stamp FROM capability_skill_install_handoffs "
                        "WHERE intent_id=?", (intent_id,),
                    ).fetchone()
                    if handoff is None or not settlement:
                        raise RuntimeError("v2 pending verification intent is ambiguous")
                    operation_id = str(handoff["operation_id"])
                    if self.connection.execute(
                        "SELECT COUNT(*) FROM capability_operation_receipts WHERE operation_id=?",
                        (operation_id,),
                    ).fetchone()[0] != 1:
                        raise RuntimeError("v2 pending verification receipt is ambiguous")
                    generation = 1
                    session = f"skill-install-verifier:{hashlib.sha256(intent_id.encode()).hexdigest()}"
                    payload = {
                        "domain": "skill-install-verification-attempt-v1",
                        "intent_id": intent_id, "attempt_generation": generation,
                        "manager_operation_id": operation_id,
                        "manager_receipt_hash": settlement,
                        "committed_set_stamp": str(handoff["member_set_stamp"]),
                        "project_scope_key": str(row["project_scope_key"]),
                        "expected_member_set_stamp": str(row["member_set_stamp"]),
                        "verifier_session_id": session,
                    }
                    attempt_id = hashlib.sha256(json.dumps(
                        payload, sort_keys=True, separators=(",", ":")
                    ).encode()).hexdigest()
                    request_id, turn_id = f"skill-install-verify-request:{attempt_id}", f"skill-install-verify-turn:{attempt_id}"
                    root_key = root_idempotency_key(session, request_id, turn_id)
                    expected_run_id = uuid.uuid5(
                        uuid.NAMESPACE_URL, f"deskpet:{root_key}"
                    ).hex
                    self.connection.execute(
                        "INSERT INTO capability_skill_install_verification_attempts("
                        "attempt_id,intent_id,attempt_generation,state_version,status,"
                        "verifier_session_id,request_id,turn_id,expected_run_id,"
                        "manager_operation_id,manager_receipt_hash,committed_set_stamp,"
                        "project_scope_key,expected_member_set_stamp,created_at,updated_at) "
                        "VALUES(?,?,1,1,'unknown',?,?,?,?,?,?,?,?,?,?,?)",
                        (attempt_id,intent_id,session,request_id,turn_id,expected_run_id,
                         operation_id,settlement,str(handoff["member_set_stamp"]),
                         str(row["project_scope_key"]),str(row["member_set_stamp"]),now,now),
                    )
                    self.connection.execute(
                        "UPDATE capability_skill_install_intents SET "
                        "verification_attempt_generation=1,current_verification_attempt_id=? "
                        "WHERE intent_id=?", (attempt_id,intent_id),
                    )
                elif status not in {
                    "staging","awaiting_confirmation","publishing",
                    "stage_failed_cleanup_pending","denied_cleanup_pending",
                    "expired_cleanup_pending","stage_failed","denied","expired","unknown",
                }:
                    raise RuntimeError("v2 install intent status is unsupported")
            self._hit(fault, "migration_v3.after_backfill")
            self.connection.execute(
                "UPDATE product_schema_manifest SET schema_hash=?,tables_json=? WHERE singleton=1",
                (_V3_SEMANTIC_HASH, json.dumps(self.table_names(), separators=(",", ":"))),
            )
            self.connection.execute("UPDATE product_schema_meta SET schema_version=3 WHERE singleton=1")
            self.connection.execute("PRAGMA user_version=3")
            self._validate_live_integrity(3, _V3_SEMANTIC_HASH)
            self._hit(fault, "migration_v3.before_commit")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _migrate_v3_to_v4(self, fault: Callable[[str], None] | None) -> None:
        """Rebuild v3 verification children without inventing cross-DB facts."""

        self.connection.execute("PRAGMA foreign_keys=OFF")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._hit(fault, "migration_v4.after_begin")
            for name in (
                "capability_skill_install_one_unresolved_attempt",
                "capability_skill_install_one_legacy_attestation",
                "capability_skill_install_current_attempt_update",
                "capability_skill_install_current_attempt_delete",
            ):
                kind = "TRIGGER" if "current_attempt" in name else "INDEX"
                self.connection.execute(f"DROP {kind} IF EXISTS {name}")
            self.connection.execute(
                "ALTER TABLE capability_skill_install_verification_attestations "
                "RENAME TO capability_skill_install_verification_attestations_v3"
            )
            self.connection.execute(
                "ALTER TABLE capability_skill_install_verification_attempts "
                "RENAME TO capability_skill_install_verification_attempts_v3"
            )
            self._hit(fault, "migration_v4.after_rename")
            for script in (VERIFICATION_V4_SCHEMA_SQL, VERIFICATION_V4_QUARANTINE_SQL):
                for statement in self._statements(script):
                    self.connection.execute(statement)
            attempts = self.connection.execute(
                "SELECT * FROM capability_skill_install_verification_attempts_v3 "
                "ORDER BY intent_id,attempt_generation"
            ).fetchall()
            attestations = self.connection.execute(
                "SELECT * FROM capability_skill_install_verification_attestations_v3 "
                "ORDER BY intent_id,attestation_id"
            ).fetchall()
            by_attempt: dict[str, list[sqlite3.Row]] = {}
            for attestation in attestations:
                if attestation["attempt_id"] is not None:
                    by_attempt.setdefault(str(attestation["attempt_id"]), []).append(attestation)
            now = float(self.connection.execute("SELECT CAST(strftime('%s','now') AS REAL)").fetchone()[0])
            for row in attempts:
                source = {key: row[key] for key in row.keys()}
                source_hash = hashlib.sha256(json.dumps(
                    source, sort_keys=True, separators=(",", ":"), default=str
                ).encode()).hexdigest()
                old_status = str(row["status"])
                classification = {
                    "prepared": "allocated",
                    "launching": "unknown",
                    "running": "unknown",
                    "unknown": "unknown",
                    "terminal_failed": "terminal_failed",
                    "superseded": "superseded",
                }.get(old_status, "quarantined")
                reason = {
                    "launching": "legacy_start_submission_ambiguous",
                    "running": "legacy_runtime_phase_ambiguous",
                    "terminal_succeeded": "legacy_success_missing_or_invalid_attestation",
                }.get(old_status)
                if old_status == "terminal_succeeded":
                    matches = by_attempt.get(str(row["attempt_id"]), [])
                    intent = self.connection.execute(
                        "SELECT status,current_verification_attempt_id,verification_ref "
                        "FROM capability_skill_install_intents WHERE intent_id=?",
                        (row["intent_id"],),
                    ).fetchone()
                    if len(matches) == 1 and intent is not None:
                        att = matches[0]
                        valid = (
                            str(intent["status"]) == "succeeded"
                            and str(intent["current_verification_attempt_id"] or "") == str(row["attempt_id"])
                            and str(intent["verification_ref"] or "") == str(att["verification_ref"])
                            and int(att["runtime_proof_valid"]) == 1
                            and bool(row["evidence_hash"])
                            and str(row["evidence_hash"]) == str(att["evidence_hash"] or "")
                        )
                        if valid:
                            classification = "attested"
                            reason = None
                classification_hash = hashlib.sha256(json.dumps({
                    "attempt_id": str(row["attempt_id"]),
                    "source_hash": source_hash,
                    "classification": classification,
                    "reason": reason,
                }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                columns = tuple(row.keys())
                values = [row[key] for key in columns]
                values[columns.index("status")] = classification
                self.connection.execute(
                    "INSERT INTO capability_skill_install_verification_attempts("
                    + ",".join(columns)
                    + ",migration_classification,migration_classification_hash) VALUES("
                    + ",".join("?" for _ in range(len(columns) + 2)) + ")",
                    tuple(values) + (classification, classification_hash),
                )
                if classification == "quarantined":
                    self.connection.execute(
                        "INSERT INTO capability_skill_install_verification_migration_quarantine "
                        "VALUES('attempt',?,?,?,?,?)",
                        (row["attempt_id"], row["intent_id"], reason or "legacy_attempt_corrupt", source_hash, now),
                    )
            for row in attestations:
                columns = tuple(row.keys())
                provenance = str(row["provenance"])
                if row["attempt_id"] is not None:
                    migrated = self.connection.execute(
                        "SELECT status FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
                        (row["attempt_id"],),
                    ).fetchone()
                    if migrated is not None and str(migrated[0]) == "attested":
                        provenance = "v3_grandfathered_attested"
                values = [row[key] for key in columns]
                values[columns.index("provenance")] = provenance
                self.connection.execute(
                    "INSERT INTO capability_skill_install_verification_attestations("
                    + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
                    tuple(values),
                )
            self._hit(fault, "migration_v4.after_backfill")
            self.connection.execute("DROP TABLE capability_skill_install_verification_attestations_v3")
            self.connection.execute("DROP TABLE capability_skill_install_verification_attempts_v3")
            self.connection.execute(
                "UPDATE product_schema_manifest SET schema_hash=?,tables_json=? WHERE singleton=1",
                (_V4_SEMANTIC_HASH, json.dumps(self.table_names(), separators=(",", ":"))),
            )
            self.connection.execute("UPDATE product_schema_meta SET schema_version=4 WHERE singleton=1")
            self.connection.execute("PRAGMA user_version=4")
            self._validate_live_integrity(4, _V4_SEMANTIC_HASH)
            self._hit(fault, "migration_v4.before_commit")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys=ON")

    @classmethod
    def _validate_v1_connection(cls, connection: sqlite3.Connection) -> None:
        cls._validate_common_version(connection, 1)
        row = connection.execute(
            "SELECT schema_hash,tables_json FROM product_schema_manifest WHERE singleton=1"
        ).fetchone()
        if row is None or str(row[0]) != _V1_DDL_HASH:
            raise RuntimeError("product state v1 schema manifest differs")
        if cls._table_names_for(connection) != tuple(json.loads(str(row[1]))):
            raise RuntimeError("product state v1 table manifest differs")
        if semantic_fingerprint(connection) != _V1_SEMANTIC_HASH:
            raise RuntimeError("product state v1 semantic schema differs")
        cls._integrity_checks(connection, 1)

    @classmethod
    def _validate_v2_connection(cls, connection: sqlite3.Connection) -> None:
        cls._validate_common_version(connection, 2)
        row = connection.execute(
            "SELECT schema_hash,tables_json FROM product_schema_manifest WHERE singleton=1"
        ).fetchone()
        if row is None or str(row[0]) != _V2_SEMANTIC_HASH:
            raise RuntimeError("product state v2 schema manifest differs")
        if cls._table_names_for(connection) != tuple(json.loads(str(row[1]))):
            raise RuntimeError("product state v2 table manifest differs")
        if semantic_fingerprint(connection) != _V2_SEMANTIC_HASH:
            raise RuntimeError("product state v2 semantic schema differs")
        cls._integrity_checks(connection, 2)

    @classmethod
    def _validate_v3_connection(cls, connection: sqlite3.Connection) -> None:
        cls._validate_common_version(connection, 3)
        row = connection.execute(
            "SELECT schema_hash,tables_json FROM product_schema_manifest WHERE singleton=1"
        ).fetchone()
        if row is None or str(row[0]) != _V3_SEMANTIC_HASH:
            raise RuntimeError("product state v3 schema manifest differs")
        if cls._table_names_for(connection) != tuple(json.loads(str(row[1]))):
            raise RuntimeError("product state v3 table manifest differs")
        if semantic_fingerprint(connection) != _V3_SEMANTIC_HASH:
            raise RuntimeError("product state v3 semantic schema differs")
        cls._integrity_checks(connection, 3)

    @classmethod
    def _validate_v4_connection(cls, connection: sqlite3.Connection) -> None:
        cls._validate_common_version(connection, 4)
        row = connection.execute(
            "SELECT schema_hash,tables_json FROM product_schema_manifest WHERE singleton=1"
        ).fetchone()
        if row is None or str(row[0]) != _V4_SEMANTIC_HASH:
            raise RuntimeError("product state v4 schema manifest differs")
        if cls._table_names_for(connection) != tuple(json.loads(str(row[1]))):
            raise RuntimeError("product state v4 table manifest differs")
        if semantic_fingerprint(connection) != _V4_SEMANTIC_HASH:
            raise RuntimeError("product state v4 semantic schema differs")
        cls._integrity_checks(connection, 4)

    def _validate_live_integrity(self, version: int, expected: str) -> None:
        if semantic_fingerprint(self.connection) != expected:
            raise RuntimeError("product state live semantic schema differs")
        self._integrity_checks(self.connection, version)

    @staticmethod
    def _validate_common_version(connection: sqlite3.Connection, version: int) -> None:
        row = connection.execute(
            "SELECT schema_version FROM product_schema_meta WHERE singleton=1"
        ).fetchone()
        if row is None or int(row[0]) != version:
            raise RuntimeError("unsupported product state schema")
        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != version:
            raise RuntimeError("product state user_version differs")

    @staticmethod
    def _integrity_checks(connection: sqlite3.Connection, version: int) -> None:
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("product state foreign key check failed")
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]) != "ok":
            raise RuntimeError("product state quick_check failed")
        run_check_probes(connection, version=version)

    @staticmethod
    def _statements(script: str) -> tuple[str, ...]:
        statements, pending = [], ""
        for line in script.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                if pending.strip():
                    statements.append(pending.strip())
                pending = ""
        if pending.strip():
            statements.append(pending.strip())
        return tuple(statements)

    @staticmethod
    def _hit(fault: Callable[[str], None] | None, point: str) -> None:
        if fault is not None:
            fault(point)

    @property
    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT schema_version FROM product_schema_meta WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("product state schema is not initialized")
        return int(row[0])

    @staticmethod
    def _table_names_for(connection: sqlite3.Connection) -> tuple[str, ...]:
        return tuple(str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall())

    def table_names(self) -> tuple[str, ...]:
        return self._table_names_for(self.connection)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ("ProductStateDatabase",)
