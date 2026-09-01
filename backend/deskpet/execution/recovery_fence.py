# SPDX-License-Identifier: BUSL-1.1

"""Durable human-memory recovery fence and deterministic emergency export."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.memory.writer_fence import (
    INGRESS_FENCED_CODE,
    HumanMemoryIngressFenced,
    assert_human_memory_ingress_open_tx,
)
from deskpet.task_scope.protocol import canonical_hash, canonical_json, identifier

_AUTHORITY = object()
_OWNED_PREFIXES = ("human_memory_", "task_scope_", "task_workspace_", "foreground_")
_PRIVATE_NAMES = frozenset(
    {"password", "token", "secret", "cookie", "api_key", "credential", "authorization"}
)
_WORKER_SPECS = {
    "projection-source": ("task_scope_projection_source_outbox", "outbox_id"),
    "projection-legacy": ("task_scope_projection_outbox", "outbox_id"),
    "search": ("task_scope_search_outbox", "outbox_id"),
    "foreground-signal": ("foreground_signal_outbox", "signal_id"),
    "foreground-lease": ("foreground_run_heads", "host_run_id"),
}


class HumanMemoryRecoveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RecoveryFenceSnapshot:
    state: str
    generation: int
    cutoff: str | None
    transition_hash: str
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class RecoveryManifestReceipt:
    manifest_id: str
    generation: int
    cutoff: str
    db_instance_id: str
    migration_chain_hash: str
    table_set_hash: str
    overall_root: str
    manifest_hash: str


@dataclass(frozen=True, slots=True)
class EmergencyExportReceipt:
    receipt_id: str
    export_id: str
    manifest_id: str
    artifact_path: Path
    artifact_sha256: str
    artifact_size: int
    overall_root: str
    receipt_hash: str


class HumanMemoryRecoveryCoordinator:
    """Host-lifecycle capability; callers cannot select tables or DB paths."""

    def __init__(
        self,
        db_path: Path,
        export_root: Path,
        *,
        _authority: object,
    ) -> None:
        if _authority is not _AUTHORITY:
            raise HumanMemoryRecoveryError("human_memory_recovery_authority_required")
        self._db_path = db_path
        self._export_root = export_root

    @classmethod
    async def bind_for_host(
        cls, db_path: str | Path, *, export_root: str | Path
    ) -> HumanMemoryRecoveryCoordinator:
        path = Path(db_path)
        await initialize_human_memory_program_state_db(path)
        root = Path(export_root)
        root.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(path) as db:
            mode_cursor = await db.execute("PRAGMA journal_mode=WAL")
            mode = await mode_cursor.fetchone()
            await mode_cursor.close()
            if mode is None or str(mode[0]).lower() != "wal":
                raise HumanMemoryRecoveryError("human_memory_recovery_wal_unavailable")
        return cls(path, root, _authority=_AUTHORITY)

    async def snapshot(self) -> RecoveryFenceSnapshot:
        async with self._connection() as db:
            row = await self._fence_tx(db)
        return _fence_snapshot(row)

    async def begin_close(self) -> RecoveryFenceSnapshot:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                current = await self._fence_tx(db)
                if current["state"] == "CLOSING":
                    await db.rollback()
                    return _fence_snapshot(current)
                if current["state"] != "OPEN":
                    raise HumanMemoryRecoveryError("human_memory_recovery_not_open")
                generation = int(current["generation"]) + 1
                registry = await self._registry_tx(db)
                counts: list[dict[str, object]] = []
                for name, spec in registry.items():
                    if spec["taxonomy"] == "A":
                        cursor = await db.execute(f'SELECT COUNT(*) FROM "{name}"')
                        count = int((await cursor.fetchone())[0])
                        await cursor.close()
                        counts.append({"table": name, "row_count": count})
                cutoff = canonical_hash(
                    {"generation": generation, "authority_counts": counts}
                )
                await self._transition_tx(
                    db,
                    current=current,
                    to_state="CLOSING",
                    generation=generation,
                    cutoff=cutoff,
                    reason_code="recovery_requested",
                )
                await db.commit()
            except Exception as exc:
                await db.rollback()
                await self._record_step_failure(exc)
                raise
        return await self.snapshot()

    async def drain_or_park(self, *, action: str = "park") -> tuple[str, ...]:
        if action not in {"drain", "park"}:
            raise HumanMemoryRecoveryError("human_memory_recovery_worker_action_invalid")
        receipt_hashes: list[str] = []
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                fence = await self._fence_tx(db)
                if fence["state"] != "CLOSING" or fence["cutoff"] is None:
                    raise HumanMemoryRecoveryError("human_memory_recovery_not_closing")
                items = await self._current_work_items_tx(db)
                for worker_kind in _WORKER_SPECS:
                    worker_items = [
                        item for item in items if item["worker_kind"] == worker_kind
                    ]
                    item_receipt_hashes: list[str] = []
                    gap_count = 0
                    for item in worker_items:
                        proof = await self._durable_proof_tx(db, item)
                        if proof is None and action == "drain":
                            gap_count += 1
                            continue
                        disposition = (
                            ("acked" if worker_kind == "foreground-signal" else "delivered")
                            if proof is not None
                            else (
                                "lease_parked"
                                if worker_kind == "foreground-lease"
                                else "parked"
                            )
                        )
                        item_payload = {
                            "schema_version": 1,
                            "generation": int(fence["generation"]),
                            "worker_kind": worker_kind,
                            "source_table": item["source_table"],
                            "item_pk": item["item_pk"],
                            "item_content_hash": item["item_content_hash"],
                            "disposition": disposition,
                            "proof_ref": None if proof is None else proof["proof_ref"],
                            "proof_hash": None if proof is None else proof["proof_hash"],
                            "durable_watermark": item["watermark"],
                            "lease_owner_id": (
                                item["lease_owner_id"]
                                if disposition == "lease_parked"
                                else None
                            ),
                            "lease_generation": (
                                item["lease_generation"]
                                if disposition == "lease_parked"
                                else None
                            ),
                            "cutoff": str(fence["cutoff"]),
                        }
                        item_receipt_hash = canonical_hash(item_payload)
                        await self._insert_or_verify_work_item_tx(
                            db, item_payload, item_receipt_hash
                        )
                        item_receipt_hashes.append(item_receipt_hash)
                    if gap_count:
                        raise HumanMemoryRecoveryError(
                            "human_memory_recovery_outbox_gap"
                        )
                    payload = {
                        "schema_version": 2,
                        "generation": int(fence["generation"]),
                        "worker_kind": worker_kind,
                        "action": action,
                        "cutoff": str(fence["cutoff"]),
                        "item_count": len(worker_items),
                        "item_root": canonical_hash(item_receipt_hashes),
                        "gap_count": gap_count,
                    }
                    receipt_hash = canonical_hash(payload)
                    cursor = await db.execute(
                        "SELECT receipt_hash,receipt_json FROM human_memory_recovery_worker_receipts "
                        "WHERE generation=? AND worker_kind=?",
                        (payload["generation"], worker_kind),
                    )
                    existing = await cursor.fetchone()
                    await cursor.close()
                    if existing is None:
                        await db.execute(
                            "INSERT INTO human_memory_recovery_worker_receipts("
                        "receipt_id,generation,worker_kind,action,cutoff,item_count,"
                        "item_root,gap_count,receipt_hash,receipt_json,recorded_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            _uuid(f"recovery-worker:{receipt_hash}"),
                            payload["generation"],
                            worker_kind,
                            action,
                            payload["cutoff"],
                            payload["item_count"],
                            payload["item_root"],
                            gap_count,
                            receipt_hash,
                            canonical_json(payload),
                            time.time(),
                        ),
                        )
                    elif (
                        str(existing["receipt_hash"]) != receipt_hash
                        or str(existing["receipt_json"]) != canonical_json(payload)
                    ):
                        raise HumanMemoryRecoveryError(
                            "human_memory_recovery_worker_receipt_conflict"
                        )
                    receipt_hashes.append(receipt_hash)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                await self._record_step_failure(exc)
                raise
        return tuple(receipt_hashes)

    async def quiesce(self) -> RecoveryFenceSnapshot:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                fence = await self._fence_tx(db)
                if fence["state"] != "CLOSING":
                    raise HumanMemoryRecoveryError("human_memory_recovery_not_closing")
                await self._verify_quiescence_tx(db, fence)
                await self._transition_tx(
                    db,
                    current=fence,
                    to_state="QUIESCED",
                    generation=int(fence["generation"]),
                    cutoff=str(fence["cutoff"]),
                    reason_code="workers_quiesced",
                )
                await db.commit()
            except Exception as exc:
                await db.rollback()
                await self._record_step_failure(exc)
                raise
        return await self.snapshot()

    async def _current_work_items_tx(
        self, db: aiosqlite.Connection
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for worker_kind, (table, primary_key) in _WORKER_SPECS.items():
            where = ""
            if worker_kind == "foreground-lease":
                where = (
                    " WHERE current_state NOT IN "
                    "('COMPLETED','FAILED','STOPPED','CANCELLED') "
                    "AND owner_id IS NOT NULL"
                )
            cursor = await db.execute(
                f'SELECT * FROM "{table}"{where} ORDER BY "{primary_key}"'
            )
            rows = await cursor.fetchall()
            columns = [str(item[0]) for item in cursor.description or ()]
            await cursor.close()
            if not columns and rows:
                info = await _column_signature_tx(db, table)
                columns = [str(item["name"]) for item in info]
            for row in rows:
                row_values = [
                    _typed_value(row[index]) for index in range(len(row))
                ]
                item: dict[str, Any] = {
                    "worker_kind": worker_kind,
                    "source_table": table,
                    "item_pk": str(row[primary_key]),
                    "item_content_hash": canonical_hash(
                        {"table": table, "columns": columns, "values": row_values}
                    ),
                    "row": row,
                    "watermark": self._item_watermark(worker_kind, row),
                    "lease_owner_id": None,
                    "lease_generation": None,
                }
                if worker_kind == "foreground-lease":
                    item["lease_owner_id"] = str(row["owner_id"])
                    item["lease_generation"] = int(row["generation"])
                items.append(item)
        return items

    @staticmethod
    def _item_watermark(worker_kind: str, row: aiosqlite.Row) -> int:
        if worker_kind == "projection-source":
            return int(row["covered_through_sequence"])
        if worker_kind in {"projection-legacy", "search"}:
            return int(row["canonical_revision"])
        return int(row["generation"])

    async def _durable_proof_tx(
        self, db: aiosqlite.Connection, item: Mapping[str, Any]
    ) -> dict[str, str] | None:
        row = item["row"]
        worker_kind = str(item["worker_kind"])
        if worker_kind == "projection-source":
            cursor = await db.execute(
                "SELECT receipt_id,receipt_hash FROM "
                "task_scope_projection_materialization_receipts "
                "WHERE source_id=? AND source_hash=?",
                (row["source_id"], row["source_hash"]),
            )
        elif worker_kind == "projection-legacy":
            cursor = await db.execute(
                "SELECT projection_revision_id,projection_hash FROM "
                "task_scope_projection_revisions WHERE task_scope_id=? "
                "AND canonical_revision=? AND projection_hash=?",
                (row["task_scope_id"], row["canonical_revision"], row["projection_hash"]),
            )
        elif worker_kind == "search":
            cursor = await db.execute(
                "SELECT d.document_id,d.document_hash FROM task_scope_projection_sources s "
                "JOIN task_scope_search_documents d ON d.source_id=s.source_id "
                "AND d.source_hash=s.source_hash WHERE s.task_scope_id=? "
                "AND s.canonical_revision=? AND s.state_hash=?",
                (row["task_scope_id"], row["canonical_revision"], row["state_hash"]),
            )
        elif worker_kind == "foreground-signal":
            cursor = await db.execute(
                "SELECT ack_id,ack_hash FROM foreground_signal_acks "
                "WHERE signal_id=? AND generation=?",
                (row["signal_id"], row["generation"]),
            )
        else:
            return None
        proof = await cursor.fetchone()
        await cursor.close()
        if proof is None:
            return None
        return {"proof_ref": str(proof[0]), "proof_hash": str(proof[1])}

    async def _insert_or_verify_work_item_tx(
        self,
        db: aiosqlite.Connection,
        payload: Mapping[str, Any],
        receipt_hash: str,
    ) -> None:
        cursor = await db.execute(
            "SELECT receipt_hash,receipt_json FROM human_memory_recovery_work_items "
            "WHERE generation=? AND worker_kind=? AND source_table=? AND item_pk=?",
            (
                payload["generation"],
                payload["worker_kind"],
                payload["source_table"],
                payload["item_pk"],
            ),
        )
        existing = await cursor.fetchone()
        await cursor.close()
        encoded = canonical_json(payload)
        if existing is not None:
            if (
                str(existing["receipt_hash"]) != receipt_hash
                or str(existing["receipt_json"]) != encoded
            ):
                raise HumanMemoryRecoveryError(
                    "human_memory_recovery_work_item_conflict"
                )
            return
        await db.execute(
            "INSERT INTO human_memory_recovery_work_items("
            "item_receipt_id,generation,worker_kind,source_table,item_pk,"
            "item_content_hash,disposition,proof_ref,proof_hash,durable_watermark,"
            "lease_owner_id,lease_generation,cutoff,receipt_hash,receipt_json,recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _uuid(f"recovery-work-item:{receipt_hash}"),
                payload["generation"],
                payload["worker_kind"],
                payload["source_table"],
                payload["item_pk"],
                payload["item_content_hash"],
                payload["disposition"],
                payload["proof_ref"],
                payload["proof_hash"],
                payload["durable_watermark"],
                payload["lease_owner_id"],
                payload["lease_generation"],
                payload["cutoff"],
                receipt_hash,
                encoded,
                time.time(),
            ),
        )

    async def _verify_quiescence_tx(
        self, db: aiosqlite.Connection, fence: aiosqlite.Row
    ) -> None:
        current = await self._current_work_items_tx(db)
        expected = {
            (item["worker_kind"], item["source_table"], item["item_pk"]): item
            for item in current
        }
        cursor = await db.execute(
            "SELECT * FROM human_memory_recovery_work_items WHERE generation=? "
            "ORDER BY worker_kind,source_table,item_pk",
            (fence["generation"],),
        )
        receipts = await cursor.fetchall()
        await cursor.close()
        actual = {
            (str(row["worker_kind"]), str(row["source_table"]), str(row["item_pk"])): row
            for row in receipts
        }
        if set(actual) != set(expected):
            raise HumanMemoryRecoveryError("human_memory_recovery_outbox_gap")
        by_worker: dict[str, list[str]] = {kind: [] for kind in _WORKER_SPECS}
        for key, item in expected.items():
            receipt = actual[key]
            try:
                payload = json.loads(str(receipt["receipt_json"]))
            except json.JSONDecodeError as exc:
                raise HumanMemoryRecoveryError(
                    "human_memory_recovery_work_item_invalid"
                ) from exc
            receipt_hash = canonical_hash(payload)
            if (
                receipt_hash != receipt["receipt_hash"]
                or canonical_json(payload) != receipt["receipt_json"]
                or int(receipt["generation"]) != int(fence["generation"])
                or receipt["cutoff"] != fence["cutoff"]
                or receipt["item_content_hash"] != item["item_content_hash"]
                or int(receipt["durable_watermark"]) != int(item["watermark"])
                or payload.get("generation") != int(receipt["generation"])
                or payload.get("worker_kind") != receipt["worker_kind"]
                or payload.get("source_table") != receipt["source_table"]
                or payload.get("item_pk") != receipt["item_pk"]
                or payload.get("item_content_hash") != receipt["item_content_hash"]
                or payload.get("disposition") != receipt["disposition"]
                or payload.get("proof_ref") != receipt["proof_ref"]
                or payload.get("proof_hash") != receipt["proof_hash"]
                or payload.get("durable_watermark")
                != receipt["durable_watermark"]
                or payload.get("lease_owner_id") != receipt["lease_owner_id"]
                or payload.get("lease_generation") != receipt["lease_generation"]
                or payload.get("cutoff") != receipt["cutoff"]
            ):
                raise HumanMemoryRecoveryError(
                    "human_memory_recovery_work_item_invalid"
                )
            disposition = str(receipt["disposition"])
            proof = await self._durable_proof_tx(db, item)
            if disposition in {"delivered", "acked"}:
                if (
                    proof is None
                    or receipt["proof_ref"] != proof["proof_ref"]
                    or receipt["proof_hash"] != proof["proof_hash"]
                ):
                    raise HumanMemoryRecoveryError(
                        "human_memory_recovery_durable_proof_missing"
                    )
            elif disposition == "parked":
                if item["worker_kind"] == "foreground-lease":
                    raise HumanMemoryRecoveryError(
                        "human_memory_recovery_lease_unfenced"
                    )
            elif disposition == "lease_parked":
                if (
                    item["worker_kind"] != "foreground-lease"
                    or receipt["lease_owner_id"] != item["lease_owner_id"]
                    or int(receipt["lease_generation"])
                    != int(item["lease_generation"])
                ):
                    raise HumanMemoryRecoveryError(
                        "human_memory_recovery_lease_unfenced"
                    )
            else:
                raise HumanMemoryRecoveryError(
                    "human_memory_recovery_work_item_invalid"
                )
            by_worker[str(item["worker_kind"])].append(receipt_hash)
        aggregate_cursor = await db.execute(
            "SELECT * FROM human_memory_recovery_worker_receipts WHERE generation=?",
            (fence["generation"],),
        )
        aggregates = await aggregate_cursor.fetchall()
        await aggregate_cursor.close()
        aggregate_map = {str(row["worker_kind"]): row for row in aggregates}
        if set(aggregate_map) != set(_WORKER_SPECS):
            raise HumanMemoryRecoveryError("human_memory_recovery_outbox_gap")
        for worker_kind, hashes in by_worker.items():
            aggregate = aggregate_map[worker_kind]
            try:
                payload = json.loads(str(aggregate["receipt_json"]))
            except json.JSONDecodeError as exc:
                raise HumanMemoryRecoveryError(
                    "human_memory_recovery_worker_receipt_invalid"
                ) from exc
            if (
                canonical_hash(payload) != aggregate["receipt_hash"]
                or canonical_json(payload) != aggregate["receipt_json"]
                or payload.get("schema_version") != 2
                or payload.get("generation") != int(fence["generation"])
                or payload.get("worker_kind") != worker_kind
                or payload.get("action") != aggregate["action"]
                or payload.get("cutoff") != fence["cutoff"]
                or payload.get("item_count") != len(hashes)
                or payload.get("item_root") != canonical_hash(hashes)
                or payload.get("gap_count") != 0
                or int(aggregate["generation"]) != int(fence["generation"])
                or aggregate["cutoff"] != fence["cutoff"]
                or int(aggregate["item_count"]) != len(hashes)
                or aggregate["item_root"] != canonical_hash(hashes)
                or int(aggregate["gap_count"]) != 0
            ):
                raise HumanMemoryRecoveryError(
                    "human_memory_recovery_worker_receipt_invalid"
                )

    async def checkpoint_wal(self) -> str:
        try:
            return await self._checkpoint_wal()
        except Exception as exc:
            await self._record_step_failure(exc)
            raise

    async def _checkpoint_wal(self) -> str:
        snapshot = await self.snapshot()
        if snapshot.state != "QUIESCED":
            raise HumanMemoryRecoveryError("human_memory_recovery_not_quiesced")
        async with aiosqlite.connect(self._db_path, isolation_level=None) as db:
            await db.execute("PRAGMA busy_timeout=5000")
            mode_cursor = await db.execute("PRAGMA journal_mode")
            mode = await mode_cursor.fetchone()
            await mode_cursor.close()
            if mode is None or str(mode[0]).lower() != "wal":
                await self.fail_closed("human_memory_recovery_wal_unavailable")
                raise HumanMemoryRecoveryError("human_memory_recovery_wal_unavailable")
            cursor = await db.execute("PRAGMA wal_checkpoint(FULL)")
            row = await cursor.fetchone()
            await cursor.close()
        if row is None or len(row) != 3:
            await self.fail_closed("human_memory_recovery_checkpoint_unknown")
            raise HumanMemoryRecoveryError("human_memory_recovery_checkpoint_unknown")
        busy, log_frames, checkpointed = map(int, row)
        if busy != 0 or log_frames != checkpointed:
            await self.fail_closed("human_memory_recovery_checkpoint_busy")
            raise HumanMemoryRecoveryError("human_memory_recovery_checkpoint_busy")
        payload = {
            "schema_version": 1,
            "generation": snapshot.generation,
            "busy": busy,
            "log_frames": log_frames,
            "checkpointed_frames": checkpointed,
        }
        receipt_hash = canonical_hash(payload)
        async with self._connection() as db:
            await db.execute(
                "INSERT OR IGNORE INTO human_memory_recovery_wal_receipts("
                "receipt_id,generation,busy,log_frames,checkpointed_frames,"
                "receipt_hash,receipt_json,recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    _uuid(f"recovery-wal:{receipt_hash}"),
                    snapshot.generation,
                    busy,
                    log_frames,
                    checkpointed,
                    receipt_hash,
                    canonical_json(payload),
                    time.time(),
                ),
            )
            await db.commit()
        return receipt_hash

    async def seal(self) -> RecoveryManifestReceipt:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                fence = await self._fence_tx(db)
                if fence["state"] != "QUIESCED" or fence["cutoff"] is None:
                    raise HumanMemoryRecoveryError("human_memory_recovery_not_quiesced")
                wal = await db.execute(
                    "SELECT receipt_hash FROM human_memory_recovery_wal_receipts WHERE generation=?",
                    (fence["generation"],),
                )
                wal_row = await wal.fetchone()
                await wal.close()
                if wal_row is None:
                    raise HumanMemoryRecoveryError("human_memory_recovery_checkpoint_missing")
                receipt = await self._create_manifest_tx(db, fence)
                await self._transition_tx(
                    db,
                    current=fence,
                    to_state="SEALED",
                    generation=int(fence["generation"]),
                    cutoff=str(fence["cutoff"]),
                    reason_code="manifest_sealed",
                )
                await db.commit()
            except Exception as exc:
                await db.rollback()
                await self._record_step_failure(exc)
                raise
        return receipt

    async def verify_manifest(self, manifest_id: str) -> None:
        try:
            await self._verify_manifest(manifest_id)
        except Exception as exc:
            await self._record_step_failure(exc)
            raise

    async def _verify_manifest(self, manifest_id: str) -> None:
        async with self._connection() as db:
            fence = await self._fence_tx(db)
            if fence["state"] != "SEALED":
                raise HumanMemoryRecoveryError("human_memory_recovery_not_sealed")
            registry = await self._registry_tx(db)
            cursor = await db.execute(
                "SELECT * FROM human_memory_recovery_manifest_tables WHERE manifest_id=?",
                (manifest_id,),
            )
            records = {str(row["table_name"]): row for row in await cursor.fetchall()}
            await cursor.close()
            for name, spec in registry.items():
                if spec["taxonomy"] not in {"A", "B"}:
                    continue
                current = await self._table_snapshot_tx(db, name, spec)
                prior = records.get(name)
                if prior is None or (
                    prior["schema_hash"], int(prior["row_count"]), prior["row_root"]
                ) != (current["schema_hash"], current["row_count"], current["row_root"]):
                    raise HumanMemoryRecoveryError("human_memory_recovery_manifest_mismatch")
            violations = await db.execute("PRAGMA foreign_key_check")
            violation = await violations.fetchone()
            await violations.close()
            if violation is not None:
                raise HumanMemoryRecoveryError("human_memory_recovery_coordination_invalid")

    async def current_manifest(self) -> RecoveryManifestReceipt:
        """Return and verify the sealed manifest for the durable generation."""

        async with self._connection() as db:
            fence = await self._fence_tx(db)
            if fence["state"] != "SEALED":
                raise HumanMemoryRecoveryError("human_memory_recovery_not_sealed")
            cursor = await db.execute(
                "SELECT * FROM human_memory_recovery_manifests WHERE generation=?",
                (fence["generation"],),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise HumanMemoryRecoveryError("human_memory_recovery_manifest_missing")
        receipt = RecoveryManifestReceipt(
            str(row["manifest_id"]),
            int(row["generation"]),
            str(row["cutoff"]),
            str(row["db_instance_id"]),
            str(row["migration_chain_hash"]),
            str(row["table_set_hash"]),
            str(row["overall_root"]),
            str(row["manifest_hash"]),
        )
        await self.verify_manifest(receipt.manifest_id)
        return receipt

    async def emergency_export(self, *, export_id: str) -> EmergencyExportReceipt:
        try:
            return await self._emergency_export(export_id=export_id)
        except Exception as exc:
            await self._record_step_failure(exc)
            raise

    async def _emergency_export(self, *, export_id: str) -> EmergencyExportReceipt:
        export_id = identifier(export_id, "export_id", 256)
        snapshot = await self.snapshot()
        if snapshot.state != "SEALED":
            raise HumanMemoryRecoveryError("human_memory_recovery_not_sealed")
        async with self._connection() as db:
            existing = await db.execute(
                "SELECT * FROM human_memory_emergency_exports WHERE export_id=?",
                (export_id,),
            )
            existing_row = await existing.fetchone()
            await existing.close()
            if existing_row is not None:
                return self._export_receipt(existing_row)
            manifest_cursor = await db.execute(
                "SELECT * FROM human_memory_recovery_manifests WHERE generation=?",
                (snapshot.generation,),
            )
            manifest = await manifest_cursor.fetchone()
            await manifest_cursor.close()
            if manifest is None:
                raise HumanMemoryRecoveryError("human_memory_recovery_manifest_missing")
            manifest_id = str(manifest["manifest_id"])
        await self.verify_manifest(manifest_id)
        async with self._connection() as db:
            registry = await self._registry_tx(db)
            rows_payload: list[dict[str, object]] = []
            for name, spec in registry.items():
                if spec["taxonomy"] not in {"A", "B"}:
                    continue
                rows_payload.extend(await self._export_rows_tx(db, name, spec))
            lineage_cursor = await db.execute(
                "SELECT transition_json FROM human_memory_recovery_transitions "
                "WHERE generation=? ORDER BY recorded_at,transition_id",
                (snapshot.generation,),
            )
            lineage = [json.loads(row[0]) for row in await lineage_cursor.fetchall()]
            await lineage_cursor.close()
            table_cursor = await db.execute(
                "SELECT table_name,taxonomy,schema_hash,row_count,row_root "
                "FROM human_memory_recovery_manifest_tables WHERE manifest_id=? "
                "ORDER BY table_name",
                (manifest_id,),
            )
            table_roots = [dict(row) for row in await table_cursor.fetchall()]
            await table_cursor.close()
            chain_cursor = await db.execute(
                "SELECT migration_id,schema_version,migration_sha256 FROM human_memory_migration_chain "
                "ORDER BY schema_version"
            )
            chain = [dict(row) for row in await chain_cursor.fetchall()]
            await chain_cursor.close()
        payload_bytes = canonical_json(
            {"rows": rows_payload, "recovery_lineage": lineage}
        ).encode("utf-8")
        chunk_records = []
        for offset in range(0, len(payload_bytes), 20 * 1024):
            chunk = payload_bytes[offset : offset + 20 * 1024]
            chunk_records.append(
                {
                    "sha256": hashlib.sha256(chunk).hexdigest(),
                    "byte_length": len(chunk),
                    "content_base64": base64.b64encode(chunk).decode("ascii"),
                }
            )
        chunk_refs = [
            {"sha256": item["sha256"], "byte_length": item["byte_length"]}
            for item in chunk_records
        ]
        header = {
            "schema_version": 1,
            "format_epoch": "human-memory-v1",
            "manifest_id": manifest_id,
            "db_instance_id": manifest["db_instance_id"],
            "generation": snapshot.generation,
            "cutoff": snapshot.cutoff,
            "migration_chain": chain,
            "table_roots": table_roots,
            "manifest_overall_root": manifest["overall_root"],
            "chunks": chunk_refs,
            "chunk_root": canonical_hash(chunk_refs),
            "overall_root": manifest["overall_root"],
        }
        artifact = b"\n".join(
            [canonical_json({"header": header}).encode("utf-8")]
            + [canonical_json({"chunk": item}).encode("utf-8") for item in chunk_records]
        ) + b"\n"
        artifact_name = f"{export_id}.hmexport"
        artifact_path = self._export_root / artifact_name
        artifact_hash = hashlib.sha256(artifact).hexdigest()
        _atomic_publish(artifact_path, artifact)
        receipt_payload = {
            "schema_version": 1,
            "export_id": export_id,
            "manifest_id": manifest_id,
            "generation": snapshot.generation,
            "artifact_name": artifact_name,
            "artifact_sha256": artifact_hash,
            "artifact_size": len(artifact),
            "overall_root": header["overall_root"],
        }
        receipt_hash = canonical_hash(receipt_payload)
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                fence = await self._fence_tx(db)
                if fence["state"] != "SEALED" or int(fence["generation"]) != snapshot.generation:
                    raise HumanMemoryRecoveryError("human_memory_recovery_generation_stale")
                receipt_id = _uuid(f"recovery-export:{receipt_hash}")
                await db.execute(
                    "INSERT INTO human_memory_emergency_exports("
                    "receipt_id,export_id,manifest_id,generation,artifact_name,"
                    "artifact_sha256,artifact_size,overall_root,receipt_hash,receipt_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        receipt_id,
                        export_id,
                        manifest_id,
                        snapshot.generation,
                        artifact_name,
                        artifact_hash,
                        len(artifact),
                        header["overall_root"],
                        receipt_hash,
                        canonical_json(receipt_payload),
                        time.time(),
                    ),
                )
                row_cursor = await db.execute(
                    "SELECT * FROM human_memory_emergency_exports WHERE receipt_id=?",
                    (receipt_id,),
                )
                row = await row_cursor.fetchone()
                await row_cursor.close()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        assert row is not None
        return self._export_receipt(row)

    async def reopen(self) -> RecoveryFenceSnapshot:
        try:
            return await self._reopen()
        except Exception as exc:
            await self._record_step_failure(exc)
            raise

    async def _reopen(self) -> RecoveryFenceSnapshot:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                fence = await self._fence_tx(db)
                if fence["state"] != "SEALED":
                    raise HumanMemoryRecoveryError("human_memory_recovery_not_sealed")
                await self._transition_tx(
                    db,
                    current=fence,
                    to_state="OPEN",
                    generation=int(fence["generation"]),
                    cutoff=None,
                    reason_code="recovery_reopened",
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return await self.snapshot()

    async def fail_closed(self, code: str) -> RecoveryFenceSnapshot:
        code = identifier(code, "failure_code", 512)
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                fence = await self._fence_tx(db)
                if fence["state"] != "FAILED_CLOSED":
                    await self._transition_tx(
                        db,
                        current=fence,
                        to_state="FAILED_CLOSED",
                        generation=int(fence["generation"]),
                        cutoff=fence["cutoff"],
                        reason_code=code,
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return await self.snapshot()

    async def _record_step_failure(self, exc: Exception) -> None:
        code = (
            exc.code
            if isinstance(exc, HumanMemoryRecoveryError)
            else "human_memory_recovery_step_failed"
        )
        snapshot = await self.snapshot()
        if snapshot.state != "FAILED_CLOSED":
            await self.fail_closed(code)

    async def _create_manifest_tx(
        self, db: aiosqlite.Connection, fence: aiosqlite.Row
    ) -> RecoveryManifestReceipt:
        registry = await self._registry_tx(db)
        table_records = []
        for name, spec in registry.items():
            if spec["taxonomy"] in {"A", "B", "C"}:
                table_records.append(await self._table_snapshot_tx(db, name, spec))
        table_set_hash = canonical_hash(
            [{"table": item["table_name"], "taxonomy": item["taxonomy"], "schema_hash": item["schema_hash"]} for item in table_records]
        )
        overall_root = canonical_hash(
            [{"table": item["table_name"], "row_count": item["row_count"], "row_root": item["row_root"]} for item in table_records]
        )
        chain_cursor = await db.execute(
            "SELECT migration_id,schema_version,migration_sha256 FROM human_memory_migration_chain ORDER BY schema_version"
        )
        chain = [dict(row) for row in await chain_cursor.fetchall()]
        await chain_cursor.close()
        migration_chain_hash = canonical_hash(chain)
        bootstrap_cursor = await db.execute(
            "SELECT created_at FROM human_memory_program_bootstrap WHERE singleton=1"
        )
        bootstrap = await bootstrap_cursor.fetchone()
        await bootstrap_cursor.close()
        db_instance_id = _uuid(
            f"human-memory-db:{bootstrap['created_at']}:{migration_chain_hash}"
        )
        manifest = {
            "schema_version": 1,
            "generation": int(fence["generation"]),
            "cutoff": str(fence["cutoff"]),
            "db_instance_id": db_instance_id,
            "migration_chain_hash": migration_chain_hash,
            "table_set_hash": table_set_hash,
            "overall_root": overall_root,
        }
        manifest_hash = canonical_hash(manifest)
        manifest_id = _uuid(f"recovery-manifest:{manifest_hash}")
        created_at = time.time()
        await db.execute(
            "INSERT INTO human_memory_recovery_manifests("
            "manifest_id,generation,cutoff,db_instance_id,migration_chain_hash,"
            "table_set_hash,overall_root,manifest_hash,manifest_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                manifest_id,
                fence["generation"],
                fence["cutoff"],
                db_instance_id,
                migration_chain_hash,
                table_set_hash,
                overall_root,
                manifest_hash,
                canonical_json(manifest),
                created_at,
            ),
        )
        for item in table_records:
            row_manifest = {
                "schema_version": 1,
                "manifest_id": manifest_id,
                **item,
            }
            row_hash = canonical_hash(row_manifest)
            await db.execute(
                "INSERT INTO human_memory_recovery_manifest_tables("
                "table_manifest_id,manifest_id,table_name,taxonomy,schema_hash,"
                "primary_key_json,row_count,row_root,manifest_hash,manifest_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    _uuid(f"recovery-table-manifest:{manifest_id}:{item['table_name']}"),
                    manifest_id,
                    item["table_name"],
                    item["taxonomy"],
                    item["schema_hash"],
                    canonical_json(item["primary_key"]),
                    item["row_count"],
                    item["row_root"],
                    row_hash,
                    canonical_json(row_manifest),
                    created_at,
                ),
            )
        return RecoveryManifestReceipt(
            manifest_id,
            int(fence["generation"]),
            str(fence["cutoff"]),
            db_instance_id,
            migration_chain_hash,
            table_set_hash,
            overall_root,
            manifest_hash,
        )

    async def _registry_tx(self, db: aiosqlite.Connection) -> dict[str, dict[str, Any]]:
        cursor = await db.execute(
            "SELECT table_name,taxonomy,columns_json FROM human_memory_recovery_table_registry ORDER BY table_name"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        registry = {
            str(row["table_name"]): {
                "taxonomy": str(row["taxonomy"]),
                "columns": json.loads(str(row["columns_json"])),
            }
            for row in rows
        }
        tables_cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        actual_tables = {str(row[0]) for row in await tables_cursor.fetchall()}
        await tables_cursor.close()
        unknown = sorted(
            name for name in actual_tables if name.startswith(_OWNED_PREFIXES) and name not in registry
        )
        if unknown:
            raise HumanMemoryRecoveryError("human_memory_recovery_unknown_protected_table")
        for name, spec in registry.items():
            columns = await _column_signature_tx(db, name)
            if columns != spec["columns"]:
                raise HumanMemoryRecoveryError("human_memory_recovery_unknown_protected_column")
        return registry

    async def _table_snapshot_tx(
        self, db: aiosqlite.Connection, name: str, spec: Mapping[str, Any]
    ) -> dict[str, Any]:
        columns = list(spec["columns"])
        primary = [item["name"] for item in sorted(columns, key=lambda item: item["pk"]) if item["pk"]]
        if not primary:
            raise HumanMemoryRecoveryError("human_memory_recovery_primary_key_missing")
        column_names = [item["name"] for item in columns]
        select = ",".join(f'"{item}"' for item in column_names)
        order = ",".join(f'"{item}"' for item in primary)
        cursor = await db.execute(f'SELECT {select} FROM "{name}" ORDER BY {order}')
        rows = await cursor.fetchall()
        await cursor.close()
        root = hashlib.sha256(f"human-memory-table:{name}\0".encode()).hexdigest()
        for row in rows:
            leaf = canonical_hash(
                {"table": name, "values": [_typed_value(row[index]) for index in range(len(column_names))]}
            )
            root = canonical_hash({"prior_root": root, "leaf_hash": leaf})
        return {
            "table_name": name,
            "taxonomy": spec["taxonomy"],
            "schema_hash": canonical_hash(columns),
            "primary_key": primary,
            "row_count": len(rows),
            "row_root": root,
        }

    async def _export_rows_tx(
        self, db: aiosqlite.Connection, name: str, spec: Mapping[str, Any]
    ) -> list[dict[str, object]]:
        columns = list(spec["columns"])
        primary = [item["name"] for item in sorted(columns, key=lambda item: item["pk"]) if item["pk"]]
        names = [item["name"] for item in columns if not _is_private_name(item["name"])]
        select_sql = ",".join(f'"{item}"' for item in names)
        order_sql = ",".join(f'"{item}"' for item in primary)
        cursor = await db.execute(
            f'SELECT {select_sql} FROM "{name}" ORDER BY {order_sql}'
        )
        rows = await cursor.fetchall()
        await cursor.close()
        result: list[dict[str, object]] = []
        for row in rows:
            values: dict[str, object] = {}
            for index, name_column in enumerate(names):
                value = row[index]
                typed = _typed_value(value)
                if name_column.endswith("_json") and isinstance(value, str):
                    try:
                        decoded = json.loads(value)
                    except json.JSONDecodeError:
                        decoded = None
                    if decoded is not None and _contains_private_json(decoded):
                        typed = {
                            "type": "redacted-json",
                            "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                        }
                values[name_column] = typed
            record: dict[str, object] = {
                "table": name,
                "values": values,
            }
            result.append(record)
        return result

    async def _transition_tx(
        self,
        db: aiosqlite.Connection,
        *,
        current: aiosqlite.Row,
        to_state: str,
        generation: int,
        cutoff: str | None,
        reason_code: str,
    ) -> None:
        payload = {
            "schema_version": 1,
            "generation": generation,
            "from_state": str(current["state"]),
            "to_state": to_state,
            "cutoff": cutoff,
            "reason_code": reason_code,
        }
        transition_hash = canonical_hash(payload)
        transition_id = _uuid(f"recovery-transition:{transition_hash}")
        now = time.time()
        await db.execute(
            "INSERT INTO human_memory_recovery_transitions("
            "transition_id,generation,from_state,to_state,cutoff,reason_code,"
            "transition_hash,transition_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                transition_id,
                generation,
                current["state"],
                to_state,
                cutoff,
                reason_code,
                transition_hash,
                canonical_json(payload),
                now,
            ),
        )
        cursor = await db.execute(
            "UPDATE human_memory_recovery_fence SET state=?,generation=?,cutoff=?,"
            "last_transition_id=?,last_transition_hash=?,failure_code=?,updated_at=? "
            "WHERE singleton=1 AND state=? AND generation=?",
            (
                to_state,
                generation,
                cutoff,
                transition_id,
                transition_hash,
                reason_code if to_state == "FAILED_CLOSED" else None,
                now,
                current["state"],
                current["generation"],
            ),
        )
        if cursor.rowcount != 1:
            raise HumanMemoryRecoveryError("human_memory_recovery_cas_failed")
        await cursor.close()

    @staticmethod
    async def _fence_tx(db: aiosqlite.Connection) -> aiosqlite.Row:
        cursor = await db.execute(
            "SELECT * FROM human_memory_recovery_fence WHERE singleton=1"
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise HumanMemoryRecoveryError("human_memory_recovery_fence_missing")
        return row

    def _export_receipt(self, row: aiosqlite.Row) -> EmergencyExportReceipt:
        path = self._export_root / str(row["artifact_name"])
        if not path.is_file():
            raise HumanMemoryRecoveryError("human_memory_export_artifact_missing")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["artifact_sha256"] or len(content) != int(row["artifact_size"]):
            raise HumanMemoryRecoveryError("human_memory_export_artifact_mismatch")
        return EmergencyExportReceipt(
            str(row["receipt_id"]),
            str(row["export_id"]),
            str(row["manifest_id"]),
            path,
            str(row["artifact_sha256"]),
            int(row["artifact_size"]),
            str(row["overall_root"]),
            str(row["receipt_hash"]),
        )

    @asynccontextmanager
    async def _connection(self):
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            yield db


async def _column_signature_tx(db: aiosqlite.Connection, name: str) -> list[dict[str, object]]:
    if re.fullmatch(r"[a-z0-9_]+", name) is None:
        raise HumanMemoryRecoveryError("human_memory_recovery_registry_invalid")
    cursor = await db.execute(f'PRAGMA table_info("{name}")')
    rows = await cursor.fetchall()
    await cursor.close()
    if not rows:
        raise HumanMemoryRecoveryError("human_memory_recovery_registered_table_missing")
    return [
        {
            "cid": int(row[0]),
            "name": str(row[1]),
            "type": str(row[2]),
            "notnull": int(row[3]),
            "default": row[4],
            "pk": int(row[5]),
        }
        for row in rows
    ]


def _typed_value(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bytes):
        return {"type": "blob", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "real", "value": format(value, ".17g")}
    return {"type": "text", "value": str(value)}


def _is_private_name(name: str) -> bool:
    normalized = name.lower()
    return any(part in normalized for part in _PRIVATE_NAMES)


def _contains_private_json(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _is_private_name(str(key)) or _contains_private_json(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_json(child) for child in value)
    return False


def _atomic_publish(path: Path, content: bytes) -> None:
    if path.exists():
        existing = path.read_bytes()
        if existing != content:
            raise HumanMemoryRecoveryError("human_memory_export_id_conflict")
        return
    token = uuid.uuid4().hex
    temp = path.with_name(f".{path.name}.{os.getpid()}.{token}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        if path.read_bytes() != content:
            raise HumanMemoryRecoveryError("human_memory_export_id_conflict")
    finally:
        temp.unlink(missing_ok=True)


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))


def _fence_snapshot(row: aiosqlite.Row) -> RecoveryFenceSnapshot:
    return RecoveryFenceSnapshot(
        str(row["state"]),
        int(row["generation"]),
        None if row["cutoff"] is None else str(row["cutoff"]),
        str(row["last_transition_hash"]),
        None if row["failure_code"] is None else str(row["failure_code"]),
    )


__all__ = [
    "INGRESS_FENCED_CODE",
    "EmergencyExportReceipt",
    "HumanMemoryIngressFenced",
    "HumanMemoryRecoveryCoordinator",
    "HumanMemoryRecoveryError",
    "RecoveryFenceSnapshot",
    "RecoveryManifestReceipt",
    "assert_human_memory_ingress_open_tx",
]
