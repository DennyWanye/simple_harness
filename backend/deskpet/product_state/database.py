"""SQLite owner for product policy state and semantic schema migration."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .backup import create_verified_backup
from .schema import CAPABILITY_SCHEMA_SQL, SCHEMA_V1_PARTS, SCHEMA_V2_PARTS, SCHEMA_VERSION
from .schema_integrity import expected_semantic_fingerprint, run_check_probes, semantic_fingerprint

_V1_DDL_HASH = hashlib.sha256("\n".join(SCHEMA_V1_PARTS).encode()).hexdigest()
_V1_SEMANTIC_HASH = expected_semantic_fingerprint(SCHEMA_V1_PARTS)
_V2_SEMANTIC_HASH = expected_semantic_fingerprint(SCHEMA_V2_PARTS)


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
        if version == 2:
            self._validate_v2_connection(self.connection)
            return
        if version != 1:
            raise RuntimeError("unsupported product state schema")
        self._validate_v1_connection(self.connection)
        create_verified_backup(
            self.connection,
            database_path=self.path,
            validate_v1=self._validate_v1_connection,
        )
        self._migrate_v1_to_v2(fault)
        self._validate_v2_connection(self.connection)

    def _initialize_fresh(self, fault: Callable[[str], None] | None) -> None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._hit(fault, "schema.after_begin")
            for part_index, part in enumerate(SCHEMA_V2_PARTS):
                for statement_index, statement in enumerate(self._statements(part)):
                    self.connection.execute(statement)
                    self._hit(fault, f"schema.ddl:{part_index}:{statement_index}")
            self.connection.execute(
                "INSERT INTO product_schema_meta(singleton,schema_version) VALUES(1,2)"
            )
            self.connection.execute(
                "INSERT INTO product_schema_manifest(singleton,schema_hash,tables_json) VALUES(1,?,?)",
                (_V2_SEMANTIC_HASH, json.dumps(self.table_names(), separators=(",", ":"))),
            )
            self.connection.execute("PRAGMA user_version=2")
            self._validate_live_integrity(2, _V2_SEMANTIC_HASH)
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
