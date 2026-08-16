"""SQLite owner for product policy state, separate from SDK execution state."""

from __future__ import annotations

import sqlite3
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

from .schema import SCHEMA_V1_PARTS, SCHEMA_VERSION


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
        if existing:
            self._validate_existing(existing)
            return
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if fault is not None:
                fault("schema.after_begin")
            for part in SCHEMA_V1_PARTS:
                for statement in self._statements(part):
                    self.connection.execute(statement)
            self.connection.execute(
                "INSERT INTO product_schema_meta(singleton,schema_version) VALUES(1,?)",
                (SCHEMA_VERSION,),
            )
            tables = self.table_names()
            schema_hash = hashlib.sha256(
                "\n".join(SCHEMA_V1_PARTS).encode()
            ).hexdigest()
            self.connection.execute(
                "INSERT INTO product_schema_manifest(singleton,schema_hash,tables_json) "
                "VALUES(1,?,?)",
                (schema_hash, json.dumps(tables, separators=(",", ":"))),
            )
            violations = self.connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError("product state foreign key check failed")
            self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            if fault is not None:
                fault("schema.before_commit")
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _validate_existing(self, existing: tuple[str, ...]) -> None:
        required = {"product_schema_meta", "product_schema_manifest"}
        if not required.issubset(existing):
            raise RuntimeError("partial or foreign product state schema")
        if self.schema_version != SCHEMA_VERSION:
            raise RuntimeError("unsupported product state schema")
        user_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if user_version != SCHEMA_VERSION:
            raise RuntimeError("product state user_version differs")
        row = self.connection.execute(
            "SELECT schema_hash,tables_json FROM product_schema_manifest WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("product state schema manifest is missing")
        expected_hash = hashlib.sha256("\n".join(SCHEMA_V1_PARTS).encode()).hexdigest()
        expected_tables = tuple(json.loads(str(row["tables_json"])))
        if str(row["schema_hash"]) != expected_hash or existing != expected_tables:
            raise RuntimeError("product state schema manifest differs")
        if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("product state foreign key check failed")

    @staticmethod
    def _statements(script: str) -> tuple[str, ...]:
        statements: list[str] = []
        pending = ""
        for line in script.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                if pending.strip():
                    statements.append(pending.strip())
                pending = ""
        if pending.strip():
            statements.append(pending.strip())
        return tuple(statements)

    @property
    def schema_version(self) -> int:
        row = self.connection.execute(
            "SELECT schema_version FROM product_schema_meta WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("product state schema is not initialized")
        return int(row[0])

    def table_names(self) -> tuple[str, ...]:
        return tuple(
            str(row[0])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ("ProductStateDatabase",)
