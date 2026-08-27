"""Semantic SQLite schema fingerprints and named constraint probes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable


def _rows(connection: sqlite3.Connection, sql: str, args=()) -> list[list[object]]:
    return [list(row) for row in connection.execute(sql, args).fetchall()]


def semantic_schema(connection: sqlite3.Connection) -> dict[str, object]:
    objects = _rows(
        connection,
        """SELECT type,name,tbl_name FROM sqlite_master
           WHERE name NOT LIKE 'sqlite_%'
           ORDER BY type,name""",
    )
    tables: dict[str, object] = {}
    indexes: dict[str, object] = {}
    for object_type, name, _table_name in objects:
        if object_type == "table":
            escaped = str(name).replace("'", "''")
            tables[str(name)] = {
                "columns": _rows(connection, f"PRAGMA table_xinfo('{escaped}')"),
                "foreign_keys": _rows(
                    connection, f"PRAGMA foreign_key_list('{escaped}')"
                ),
                "indexes": _rows(connection, f"PRAGMA index_list('{escaped}')"),
            }
        elif object_type == "index":
            escaped = str(name).replace("'", "''")
            indexes[str(name)] = _rows(
                connection, f"PRAGMA index_xinfo('{escaped}')"
            )
    return {"objects": objects, "tables": tables, "indexes": indexes}


def semantic_fingerprint(connection: sqlite3.Connection) -> str:
    payload = json.dumps(
        semantic_schema(connection),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def expected_semantic_fingerprint(parts: Iterable[str]) -> str:
    connection = sqlite3.connect(":memory:")
    try:
        for part in parts:
            connection.executescript(part)
        return semantic_fingerprint(connection)
    finally:
        connection.close()


def run_check_probes(connection: sqlite3.Connection, *, version: int) -> None:
    """Exercise named CHECK constraints without retaining probe rows."""

    connection.execute("SAVEPOINT product_schema_check_probes")
    try:
        valid_phase = "planned" if version == 1 else "batch_staged"
        valid_kind = "install" if version == 1 else "skill_install_batch"
        connection.execute(
            """INSERT INTO capability_operations(
                operation_id,idempotency_key,kind,phase,status,request_json,
                started_at,updated_at
            ) VALUES('__schema_probe_operation__','__schema_probe_idempotency__',
                ?,?,'running','{}',0,0)""",
            (valid_kind, valid_phase),
        )
        try:
            connection.execute(
                """INSERT INTO capability_operations(
                    operation_id,idempotency_key,kind,phase,status,request_json,
                    started_at,updated_at
                ) VALUES('__schema_probe_invalid__','__schema_probe_invalid_idem__',
                    'invalid','invalid','running','{}',0,0)"""
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise RuntimeError("operation kind/phase CHECK probe accepted invalid values")
        if version == 2:
            values = (
                "__schema_probe_intent__",
                "effect",
                "call",
                "root",
                "run",
                "chat",
                "project",
                "principal",
                "{}",
                "a" * 40,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "nonce",
                1,
                1.0,
                "awaiting_confirmation",
                1,
                0.0,
                0.0,
            )
            connection.execute(
                """INSERT INTO capability_skill_install_intents(
                    intent_id,effect_id,call_id,root_run_id,run_id,channel,
                    project_scope_key,principal_id,source_json,exact_commit,
                    archive_hash,raw_tree_hash,member_set_stamp,permission_set_hash,
                    confirmation_nonce,confirmation_version,expires_at,status,
                    state_version,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            invalid = list(values)
            invalid[0] = "__schema_probe_invalid_intent__"
            invalid[1] = "effect-invalid"
            invalid[2] = "call-invalid"
            invalid[14] = "nonce-invalid"
            invalid[17] = "not_a_state"
            try:
                connection.execute(
                    """INSERT INTO capability_skill_install_intents(
                        intent_id,effect_id,call_id,root_run_id,run_id,channel,
                        project_scope_key,principal_id,source_json,exact_commit,
                        archive_hash,raw_tree_hash,member_set_stamp,permission_set_hash,
                        confirmation_nonce,confirmation_version,expires_at,status,
                        state_version,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    invalid,
                )
            except sqlite3.IntegrityError:
                pass
            else:
                raise RuntimeError("skill install status CHECK probe accepted invalid state")
    finally:
        connection.execute("ROLLBACK TO product_schema_check_probes")
        connection.execute("RELEASE product_schema_check_probes")


__all__ = (
    "expected_semantic_fingerprint",
    "run_check_probes",
    "semantic_fingerprint",
    "semantic_schema",
)
