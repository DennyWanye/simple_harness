# SPDX-License-Identifier: BUSL-1.1

"""Deterministic bounded TaskScope read views over exact source identities."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.memory.recovery_work_items import is_human_memory_work_item_parked_tx
from deskpet.task_scope.projection_sources import (
    ProjectionSourceReceipt,
    load_projection_source_tx,
)
from deskpet.task_scope.protocol import canonical_hash, canonical_json
from deskpet.task_scope.store import TaskScopeNotFound, _uuid

VIEW_KINDS = ("README", "PLAN", "STATUS", "DECISIONS", "RESUME", "EVIDENCE")
VIEW_LIMITS = {
    "README": 16 * 1024,
    "PLAN": 32 * 1024,
    "STATUS": 12 * 1024,
    "DECISIONS": 32 * 1024,
    "RESUME": 24 * 1024,
    "EVIDENCE": 16 * 1024,
}
MAX_BLOCK_BYTES = 32 * 1024
TARGET_BLOCK_BYTES = 31 * 1024
LOGICAL_EVIDENCE_GROUP_SIZE = 500


class ProjectionIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReadBlockRef:
    block_id: str
    block_kind: str
    content_sha256: str
    byte_length: int


@dataclass(frozen=True, slots=True)
class TaskScopeReadView:
    task_scope_id: str
    source_id: str
    source_hash: str
    view_kind: str
    content: str
    content_sha256: str
    root_block_id: str | None
    block_count: int
    receipt_hash: str


@dataclass(frozen=True, slots=True)
class CheckpointDriftReport:
    task_scope_id: str
    source_id: str
    checkpoint_id: str | None
    checkpoint_hash: str | None
    drifted: bool
    changed_fields: tuple[str, ...]
    report_hash: str


class TaskScopeProjectionStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def materialize(
        self, *, task_scope_id: str | None = None, source_id: str | None = None
    ) -> dict[str, TaskScopeReadView]:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                source = await load_projection_source_tx(
                    db, source_id=source_id, task_scope_id=task_scope_id
                )
                outbox_cursor = await db.execute(
                    "SELECT outbox_id FROM task_scope_projection_source_outbox "
                    "WHERE source_id=?",
                    (source.source_id,),
                )
                outbox = await outbox_cursor.fetchone()
                await outbox_cursor.close()
                if outbox is None:
                    raise ProjectionIntegrityError(
                        "task_scope_projection_source_outbox_missing"
                    )
                if await is_human_memory_work_item_parked_tx(
                    db,
                    worker_kind="projection-source",
                    source_table="task_scope_projection_source_outbox",
                    primary_key="outbox_id",
                    item_pk=str(outbox["outbox_id"]),
                ):
                    raise ProjectionIntegrityError(
                        "human_memory_projection_work_item_parked"
                    )
                model = await self._load_model_tx(db, source)
                rendered = await self._render_tx(db, source, model)
                projection_root = canonical_hash(
                    {kind: rendered[kind].receipt_hash for kind in VIEW_KINDS}
                )
                receipt = {
                    "schema_version": 1,
                    "source_id": source.source_id,
                    "source_hash": source.source_hash,
                    "renderer_contract_version": source.renderer_contract_version,
                    "projection_root_hash": projection_root,
                }
                receipt_hash = canonical_hash(receipt)
                await db.execute(
                    "INSERT OR IGNORE INTO task_scope_projection_materialization_receipts("
                    "receipt_id,source_id,source_hash,renderer_contract_version,"
                    "projection_root_hash,receipt_hash,receipt_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        _uuid(f"task-scope-materialization:{source.source_hash}"),
                        source.source_id,
                        source.source_hash,
                        source.renderer_contract_version,
                        projection_root,
                        receipt_hash,
                        canonical_json(receipt),
                        time.time(),
                    ),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return rendered

    async def read_view(
        self,
        view_kind: str,
        *,
        task_scope_id: str | None = None,
        source_id: str | None = None,
    ) -> TaskScopeReadView:
        kind = view_kind.upper()
        if kind not in VIEW_KINDS:
            raise ValueError("task_scope_view_kind_invalid")
        return (await self.materialize(task_scope_id=task_scope_id, source_id=source_id))[kind]

    async def read_block(self, block_id: str) -> bytes:
        async with self._connection() as db:
            cursor = await db.execute(
                "SELECT content,content_sha256 FROM task_scope_read_blocks WHERE block_id=?",
                (block_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise TaskScopeNotFound("task_scope_projection_block_not_found")
        content = bytes(row["content"])
        if hashlib.sha256(content).hexdigest() != row["content_sha256"]:
            raise ProjectionIntegrityError("task_scope_projection_block_hash_mismatch")
        return content

    async def list_evidence_groups(
        self, *, task_scope_id: str | None = None, source_id: str | None = None
    ) -> tuple[dict[str, object], ...]:
        """Return logical 500-event page descriptors in stable order."""

        async with self._connection() as db:
            source = await load_projection_source_tx(
                db, source_id=source_id, task_scope_id=task_scope_id
            )
            cursor = await db.execute(
                "SELECT block_id,block_kind,content,content_sha256 FROM task_scope_read_blocks "
                "WHERE source_id=? AND view_kind='EVIDENCE' AND block_kind='group'",
                (source.source_id,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        groups: list[dict[str, object]] = []
        for row in rows:
            content = bytes(row["content"])
            if hashlib.sha256(content).hexdigest() != row["content_sha256"]:
                raise ProjectionIntegrityError("task_scope_projection_block_hash_mismatch")
            group = json.loads(content)
            group["block_id"] = str(row["block_id"])
            group["block_kind"] = str(row["block_kind"])
            group["content_sha256"] = str(row["content_sha256"])
            group["byte_length"] = len(content)
            groups.append(group)
        groups.sort(key=lambda item: int(str(item["logical_group"])))
        return tuple(groups)

    async def read_evidence_group(
        self, group_block_id: str
    ) -> tuple[dict[str, Any], ...]:
        """Recover one logical page and verify its complete block DAG."""

        async with self._connection() as db:
            cursor = await db.execute(
                "SELECT * FROM task_scope_read_blocks WHERE block_id=?",
                (group_block_id,),
            )
            group_row = await cursor.fetchone()
            await cursor.close()
            if group_row is None:
                raise ProjectionIntegrityError("task_scope_projection_block_missing")
            if group_row["view_kind"] != "EVIDENCE" or group_row["block_kind"] != "group":
                raise ProjectionIntegrityError("task_scope_projection_group_kind_mismatch")
            try:
                group = json.loads(_verified_content(group_row))
                leaves_root = group["leaves_root"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ProjectionIntegrityError("task_scope_projection_group_manifest_invalid") from exc
            source_id = str(group_row["source_id"])
            leaf_refs = await self._collect_leaf_refs_tx(
                db,
                source_id=source_id,
                raw_ref=leaves_root,
                ancestry=frozenset({group_block_id}),
                depth=0,
            )
            events: list[dict[str, Any]] = []
            for leaf_ref in leaf_refs:
                _, leaf_content = await self._load_ref_tx(
                    db,
                    source_id=source_id,
                    raw_ref=leaf_ref,
                    allowed_kinds={"leaf"},
                )
                try:
                    leaf = json.loads(leaf_content)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProjectionIntegrityError("task_scope_projection_leaf_invalid") from exc
                if isinstance(leaf.get("events"), list):
                    for event in leaf["events"]:
                        if not isinstance(event, dict):
                            raise ProjectionIntegrityError("task_scope_projection_event_invalid")
                        events.append(event)
                    continue
                chunks = leaf.get("chunks")
                if not isinstance(chunks, list) or not chunks:
                    raise ProjectionIntegrityError("task_scope_projection_leaf_invalid")
                assembled = bytearray()
                for chunk_ref in chunks:
                    _, chunk_content = await self._load_ref_tx(
                        db,
                        source_id=source_id,
                        raw_ref=chunk_ref,
                        allowed_kinds={"chunk"},
                    )
                    assembled.extend(chunk_content)
                if (
                    not isinstance(leaf.get("byte_length"), int)
                    or len(assembled) != leaf["byte_length"]
                    or hashlib.sha256(assembled).hexdigest() != leaf.get("content_sha256")
                ):
                    raise ProjectionIntegrityError("task_scope_projection_event_chunks_invalid")
                try:
                    event = json.loads(bytes(assembled))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ProjectionIntegrityError("task_scope_projection_event_chunks_invalid") from exc
                if not isinstance(event, dict) or event.get("event_sequence") != leaf.get("event_sequence"):
                    raise ProjectionIntegrityError("task_scope_projection_event_chunks_invalid")
                events.append(event)
            sequences = [event.get("event_sequence") for event in events]
            expected_count = group.get("event_count")
            first = group.get("first_event_sequence")
            last = group.get("last_event_sequence")
            if (
                not isinstance(expected_count, int)
                or not isinstance(first, int)
                or not isinstance(last, int)
                or expected_count < 1
                or len(events) != expected_count
                or sequences != list(range(first, last + 1))
            ):
                raise ProjectionIntegrityError("task_scope_projection_group_sequence_invalid")
            return tuple(events)

    async def _collect_leaf_refs_tx(
        self,
        db: aiosqlite.Connection,
        *,
        source_id: str,
        raw_ref: object,
        ancestry: frozenset[str],
        depth: int,
    ) -> list[dict[str, object]]:
        if depth > 64:
            raise ProjectionIntegrityError("task_scope_projection_index_depth_invalid")
        row, content = await self._load_ref_tx(
            db,
            source_id=source_id,
            raw_ref=raw_ref,
            allowed_kinds={"leaf", "index"},
        )
        block_id = str(row["block_id"])
        if block_id in ancestry:
            raise ProjectionIntegrityError("task_scope_projection_index_cycle")
        if row["block_kind"] == "leaf":
            assert isinstance(raw_ref, dict)
            return [raw_ref]
        try:
            index = json.loads(content)
            children = index["children"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectionIntegrityError("task_scope_projection_index_invalid") from exc
        if not isinstance(children, list) or not children:
            raise ProjectionIntegrityError("task_scope_projection_index_invalid")
        result: list[dict[str, object]] = []
        branch = ancestry | {block_id}
        for child in children:
            result.extend(
                await self._collect_leaf_refs_tx(
                    db,
                    source_id=source_id,
                    raw_ref=child,
                    ancestry=branch,
                    depth=depth + 1,
                )
            )
        return result

    async def _load_ref_tx(
        self,
        db: aiosqlite.Connection,
        *,
        source_id: str,
        raw_ref: object,
        allowed_kinds: set[str],
    ) -> tuple[aiosqlite.Row, bytes]:
        if not isinstance(raw_ref, dict):
            raise ProjectionIntegrityError("task_scope_projection_ref_invalid")
        block_id = raw_ref.get("block_id")
        block_kind = raw_ref.get("block_kind")
        content_sha256 = raw_ref.get("content_sha256")
        byte_length = raw_ref.get("byte_length")
        if (
            not isinstance(block_id, str)
            or block_kind not in allowed_kinds
            or not isinstance(content_sha256, str)
            or len(content_sha256) != 64
            or isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or not 0 <= byte_length <= MAX_BLOCK_BYTES
        ):
            raise ProjectionIntegrityError("task_scope_projection_ref_invalid")
        cursor = await db.execute(
            "SELECT * FROM task_scope_read_blocks WHERE block_id=?", (block_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise ProjectionIntegrityError("task_scope_projection_block_missing")
        content = _verified_content(row)
        if (
            row["source_id"] != source_id
            or row["view_kind"] != "EVIDENCE"
            or row["block_kind"] != block_kind
            or row["content_sha256"] != content_sha256
            or len(content) != byte_length
        ):
            raise ProjectionIntegrityError("task_scope_projection_ref_mismatch")
        return row, content

    async def verify_checkpoint(
        self,
        *,
        task_scope_id: str | None = None,
        source_id: str | None = None,
        live_probe: Mapping[str, object],
    ) -> CheckpointDriftReport:
        async with self._connection() as db:
            source = await load_projection_source_tx(
                db, source_id=source_id, task_scope_id=task_scope_id
            )
            cursor = await db.execute(
                "SELECT checkpoint_id,checkpoint_hash,checkpoint_json "
                "FROM task_scope_checkpoints WHERE task_scope_id=? "
                "ORDER BY created_at,checkpoint_id LIMIT 1 OFFSET ?",
                (
                    source.task_scope_id,
                    max(0, source.checkpoint_sequence - 1),
                ),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if source.checkpoint_sequence == 0:
                row = None
        checkpoint_id = None if row is None else str(row["checkpoint_id"])
        checkpoint_hash = None if row is None else str(row["checkpoint_hash"])
        expected = {} if row is None else json.loads(str(row["checkpoint_json"])).get("metadata", {})
        fields = sorted(set(expected) | set(live_probe))
        changed = tuple(
            field for field in fields if expected.get(field) != live_probe.get(field)
        )
        payload = {
            "schema_version": 1,
            "task_scope_id": source.task_scope_id,
            "source_id": source.source_id,
            "checkpoint_id": checkpoint_id,
            "checkpoint_hash": checkpoint_hash,
            "drifted": bool(changed),
            "changed_fields": list(changed),
        }
        return CheckpointDriftReport(
            source.task_scope_id,
            source.source_id,
            checkpoint_id,
            checkpoint_hash,
            bool(changed),
            changed,
            canonical_hash(payload),
        )

    async def _load_model_tx(
        self, db: aiosqlite.Connection, source: ProjectionSourceReceipt
    ) -> dict[str, Any]:
        state_cursor = await db.execute(
            "SELECT state_json,state_hash,event_watermark FROM task_scope_canonical_revisions "
            "WHERE task_scope_id=? AND revision=?",
            (source.task_scope_id, source.canonical_revision),
        )
        state_row = await state_cursor.fetchone()
        await state_cursor.close()
        if state_row is None or state_row["state_hash"] != source.state_hash:
            raise ProjectionIntegrityError("task_scope_projection_source_state_mismatch")
        event_cursor = await db.execute(
            "SELECT * FROM task_scope_events WHERE task_scope_id=? AND event_sequence<=? "
            "ORDER BY event_sequence",
            (source.task_scope_id, source.event_watermark),
        )
        events = [dict(row) for row in await event_cursor.fetchall()]
        await event_cursor.close()
        step_cursor = await db.execute(
            "SELECT s.* FROM task_scope_steps s JOIN task_scope_events e ON e.event_id=s.event_id "
            "WHERE e.task_scope_id=? AND e.event_sequence<=? ORDER BY e.event_sequence,s.step_record_id",
            (source.task_scope_id, source.event_watermark),
        )
        steps = [dict(row) for row in await step_cursor.fetchall()]
        await step_cursor.close()
        link_cursor = await db.execute(
            "SELECT l.* FROM task_scope_evidence_links l JOIN task_scope_events e ON e.event_id=l.event_id "
            "WHERE e.task_scope_id=? AND e.event_sequence<=? ORDER BY e.event_sequence,l.ordinal,l.link_id",
            (source.task_scope_id, source.event_watermark),
        )
        links = [dict(row) for row in await link_cursor.fetchall()]
        await link_cursor.close()
        checkpoint_cursor = await db.execute(
            "SELECT * FROM task_scope_checkpoints WHERE task_scope_id=? "
            "ORDER BY created_at,checkpoint_id LIMIT ?",
            (source.task_scope_id, source.checkpoint_sequence),
        )
        checkpoints = [dict(row) for row in await checkpoint_cursor.fetchall()]
        await checkpoint_cursor.close()
        binding = None
        roots: list[dict[str, object]] = []
        if source.binding_set_revision:
            binding_cursor = await db.execute(
                "SELECT * FROM task_workspace_binding_revisions "
                "WHERE task_scope_id=? AND binding_set_revision=?",
                (source.task_scope_id, source.binding_set_revision),
            )
            binding_row = await binding_cursor.fetchone()
            await binding_cursor.close()
            if binding_row is None or binding_row["receipt_hash"] != source.binding_receipt_hash:
                raise ProjectionIntegrityError("task_scope_projection_binding_mismatch")
            binding = dict(binding_row)
            root_cursor = await db.execute(
                "SELECT * FROM task_workspace_binding_roots WHERE task_scope_id=? "
                "AND first_binding_set_revision<=? ORDER BY first_binding_set_revision,root_id",
                (source.task_scope_id, source.binding_set_revision),
            )
            roots = [dict(row) for row in await root_cursor.fetchall()]
            await root_cursor.close()
        step_map: dict[str, list[dict[str, object]]] = {}
        for step in steps:
            step_map.setdefault(str(step["event_id"]), []).append(step)
        link_map: dict[str, list[dict[str, object]]] = {}
        for link in links:
            link_map.setdefault(str(link["event_id"]), []).append(link)
        canonical_events = []
        for event in events:
            item = dict(event)
            item["payload"] = json.loads(str(item.pop("payload_json")))
            item["steps"] = step_map.get(str(event["event_id"]), [])
            item["evidence_links"] = link_map.get(str(event["event_id"]), [])
            canonical_events.append(item)
        return {
            "state": json.loads(str(state_row["state_json"])),
            "events": canonical_events,
            "checkpoints": [
                {**row, "checkpoint": json.loads(str(row["checkpoint_json"]))}
                for row in checkpoints
            ],
            "binding": binding,
            "roots": roots,
        }

    async def _render_tx(
        self, db: aiosqlite.Connection, source: ProjectionSourceReceipt, model: dict[str, Any]
    ) -> dict[str, TaskScopeReadView]:
        state = model["state"]
        events = model["events"]
        checkpoints = model["checkpoints"]
        operations = list(state.get("operations", []))
        latest_checkpoint = None if not checkpoints else checkpoints[-1]
        contents = {
            "README": "\n".join(
                [
                    f"# {state.get('title', source.task_scope_id)}",
                    "",
                    f"Status: {state.get('status', 'unknown')}",
                    f"Goal: {state.get('goal') or 'Not set'}",
                    f"TaskScope: {source.task_scope_id}",
                    f"Source: {source.source_hash}",
                ]
            ),
            "PLAN": canonical_json(
                {
                    "schema_version": 1,
                    "task_scope_id": source.task_scope_id,
                    "canonical_revision": source.canonical_revision,
                    "operations": operations,
                }
            ),
            "STATUS": canonical_json(
                {
                    "schema_version": 1,
                    "task_scope_id": source.task_scope_id,
                    "status": state.get("status"),
                    "goal": state.get("goal"),
                    "event_watermark": source.event_watermark,
                    "checkpoint_sequence": source.checkpoint_sequence,
                    "binding_set_revision": source.binding_set_revision,
                }
            ),
            "DECISIONS": canonical_json(
                {
                    "schema_version": 1,
                    "task_scope_id": source.task_scope_id,
                    "decisions": [
                        item for item in operations if str(item.get("kind", "")).startswith("decision.")
                    ],
                }
            ),
            "RESUME": canonical_json(
                {
                    "schema_version": 1,
                    "task_scope_id": source.task_scope_id,
                    "source_id": source.source_id,
                    "source_hash": source.source_hash,
                    "status": state.get("status"),
                    "goal": state.get("goal"),
                    "resume": state.get("resume"),
                    "checkpoint": latest_checkpoint,
                    "binding": model["binding"],
                    "roots": model["roots"],
                }
            ),
            "EVIDENCE": "",
        }
        evidence_root, evidence_count, groups, archive_ref, groups_root = await self._store_evidence_tree_tx(
            db, source, events, model
        )
        contents["EVIDENCE"] = canonical_json(
            {
                "schema_version": 1,
                "task_scope_id": source.task_scope_id,
                "source_id": source.source_id,
                "source_hash": source.source_hash,
                "event_count": len(events),
                "logical_group_size": LOGICAL_EVIDENCE_GROUP_SIZE,
                "logical_group_count": len(groups),
                "canonical_archive_block_id": archive_ref.block_id,
                "logical_groups_root_block_id": groups_root.block_id,
                "root_block_id": None if evidence_root is None else evidence_root.block_id,
            }
        )
        rendered: dict[str, TaskScopeReadView] = {}
        for kind in VIEW_KINDS:
            content = _bounded_view(kind, contents[kind], VIEW_LIMITS[kind])
            content_bytes = content.encode("utf-8")
            content_hash = hashlib.sha256(content_bytes).hexdigest()
            root = evidence_root if kind == "EVIDENCE" else None
            block_count = evidence_count if kind == "EVIDENCE" else 0
            receipt = {
                "schema_version": 1,
                "task_scope_id": source.task_scope_id,
                "source_id": source.source_id,
                "source_hash": source.source_hash,
                "view_kind": kind,
                "content_sha256": content_hash,
                "byte_length": len(content_bytes),
                "root_block_id": None if root is None else root.block_id,
                "block_count": block_count,
            }
            receipt_hash = canonical_hash(receipt)
            view_id = _uuid(f"task-scope-view:{source.source_hash}:{kind}")
            await db.execute(
                "INSERT OR IGNORE INTO task_scope_read_view_revisions("
                "view_revision_id,source_id,task_scope_id,view_kind,content_sha256,content,"
                "root_block_id,block_count,receipt_hash,receipt_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    view_id,
                    source.source_id,
                    source.task_scope_id,
                    kind,
                    content_hash,
                    content_bytes,
                    None if root is None else root.block_id,
                    block_count,
                    receipt_hash,
                    canonical_json(receipt),
                    time.time(),
                ),
            )
            rendered[kind] = TaskScopeReadView(
                source.task_scope_id,
                source.source_id,
                source.source_hash,
                kind,
                content,
                content_hash,
                None if root is None else root.block_id,
                block_count,
                receipt_hash,
            )
        return rendered

    async def _store_evidence_tree_tx(
        self,
        db: aiosqlite.Connection,
        source: ProjectionSourceReceipt,
        events: list[dict[str, Any]],
        model: dict[str, Any],
    ) -> tuple[ReadBlockRef, int, list[dict[str, object]], ReadBlockRef, ReadBlockRef]:
        total = 0
        group_refs: list[ReadBlockRef] = []
        group_descriptors: list[dict[str, object]] = []
        for start in range(0, len(events), LOGICAL_EVIDENCE_GROUP_SIZE):
            group = events[start : start + LOGICAL_EVIDENCE_GROUP_SIZE]
            leaf_refs: list[ReadBlockRef] = []
            pending: list[dict[str, Any]] = []
            for event in group:
                candidate = {"schema_version": 1, "events": [*pending, event]}
                if len(canonical_json(candidate).encode("utf-8")) <= TARGET_BLOCK_BYTES:
                    pending.append(event)
                    continue
                if pending:
                    leaf_refs.append(await self._store_json_block_tx(db, source, "leaf", {"schema_version": 1, "events": pending}))
                    total += 1
                    pending = []
                event_bytes = canonical_json(event).encode("utf-8")
                if len(event_bytes) <= TARGET_BLOCK_BYTES:
                    pending = [event]
                else:
                    chunks: list[ReadBlockRef] = []
                    for offset in range(0, len(event_bytes), TARGET_BLOCK_BYTES):
                        chunks.append(await self._store_block_tx(db, source, "chunk", event_bytes[offset : offset + TARGET_BLOCK_BYTES]))
                        total += 1
                    descriptor = {
                        "schema_version": 1,
                        "event_sequence": event["event_sequence"],
                        "byte_length": len(event_bytes),
                        "content_sha256": hashlib.sha256(event_bytes).hexdigest(),
                        "chunks": [_ref_json(ref) for ref in chunks],
                    }
                    leaf_refs.append(await self._store_json_block_tx(db, source, "leaf", descriptor))
                    total += 1
            if pending:
                leaf_refs.append(await self._store_json_block_tx(db, source, "leaf", {"schema_version": 1, "events": pending}))
                total += 1
            leaves_root, added = await self._store_index_tree_tx(db, source, leaf_refs, "group-leaves")
            total += added
            group_number = start // LOGICAL_EVIDENCE_GROUP_SIZE + 1
            group_payload = {
                "schema_version": 1,
                "logical_group": group_number,
                "first_event_sequence": group[0]["event_sequence"],
                "last_event_sequence": group[-1]["event_sequence"],
                "event_count": len(group),
                "leaves_root": _ref_json(leaves_root),
            }
            group_ref = await self._store_json_block_tx(db, source, "group", group_payload)
            total += 1
            group_refs.append(group_ref)
            group_descriptors.append(
                {
                    "logical_group": group_number,
                    "first_event_sequence": group[0]["event_sequence"],
                    "last_event_sequence": group[-1]["event_sequence"],
                    "event_count": len(group),
                    "block_id": group_ref.block_id,
                    "content_sha256": group_ref.content_sha256,
                }
            )
        if group_refs:
            groups_root, added = await self._store_index_tree_tx(
                db, source, group_refs, "evidence-groups"
            )
            total += added
        else:
            groups_root = await self._store_json_block_tx(
                db,
                source,
                "index",
                {"schema_version": 1, "domain": "evidence-groups", "children": []},
            )
            total += 1
        archive = {
            "schema_version": 1,
            "source": {
                "source_id": source.source_id,
                "source_hash": source.source_hash,
                "canonical_revision": source.canonical_revision,
                "state_hash": source.state_hash,
                "event_watermark": source.event_watermark,
                "event_prefix_root": source.event_prefix_root,
                "checkpoint_sequence": source.checkpoint_sequence,
                "checkpoint_set_root": source.checkpoint_set_root,
                "binding_set_revision": source.binding_set_revision,
                "binding_receipt_hash": source.binding_receipt_hash,
            },
            "state": model["state"],
            "checkpoints": model["checkpoints"],
            "binding": model["binding"],
            "roots": model["roots"],
        }
        archive_bytes = canonical_json(archive).encode("utf-8")
        archive_chunks: list[ReadBlockRef] = []
        for offset in range(0, len(archive_bytes), TARGET_BLOCK_BYTES):
            archive_chunks.append(
                await self._store_block_tx(
                    db, source, "chunk", archive_bytes[offset : offset + TARGET_BLOCK_BYTES]
                )
            )
            total += 1
        chunks_root, added = await self._store_index_tree_tx(
            db, source, archive_chunks, "canonical-archive-chunks"
        )
        total += added
        archive_ref = await self._store_json_block_tx(
            db,
            source,
            "index",
            {
                "schema_version": 1,
                "domain": "canonical-archive",
                "byte_length": len(archive_bytes),
                "content_sha256": hashlib.sha256(archive_bytes).hexdigest(),
                "chunks_root": _ref_json(chunks_root),
            },
        )
        total += 1
        group_manifest_ref = await self._store_json_block_tx(
            db,
            source,
            "index",
            {
                "schema_version": 1,
                "domain": "logical-evidence-groups",
                "logical_group_size": LOGICAL_EVIDENCE_GROUP_SIZE,
                "logical_group_count": len(group_descriptors),
                "event_count": len(events),
                "groups_root": _ref_json(groups_root),
            },
        )
        total += 1
        root, added = await self._store_index_tree_tx(
            db, source, [archive_ref, group_manifest_ref], "evidence-root"
        )
        return root, total + added, group_descriptors, archive_ref, groups_root

    async def _store_index_tree_tx(
        self,
        db: aiosqlite.Connection,
        source: ProjectionSourceReceipt,
        refs: list[ReadBlockRef],
        domain: str,
    ) -> tuple[ReadBlockRef, int]:
        level = 0
        current = refs
        added = 0
        while len(current) > 1:
            next_level: list[ReadBlockRef] = []
            pending: list[dict[str, object]] = []
            for ref in current:
                item = _ref_json(ref)
                candidate = {"schema_version": 1, "domain": domain, "level": level, "children": [*pending, item]}
                if pending and len(canonical_json(candidate).encode("utf-8")) > TARGET_BLOCK_BYTES:
                    next_level.append(await self._store_json_block_tx(db, source, "index", {"schema_version": 1, "domain": domain, "level": level, "children": pending}))
                    added += 1
                    pending = [item]
                else:
                    pending.append(item)
            if pending:
                next_level.append(await self._store_json_block_tx(db, source, "index", {"schema_version": 1, "domain": domain, "level": level, "children": pending}))
                added += 1
            current = next_level
            level += 1
        if not current:
            raise ProjectionIntegrityError("task_scope_projection_empty_index")
        return current[0], added

    async def _store_json_block_tx(
        self, db: aiosqlite.Connection, source: ProjectionSourceReceipt, kind: str, payload: object
    ) -> ReadBlockRef:
        return await self._store_block_tx(db, source, kind, canonical_json(payload).encode("utf-8"))

    async def _store_block_tx(
        self, db: aiosqlite.Connection, source: ProjectionSourceReceipt, kind: str, content: bytes
    ) -> ReadBlockRef:
        if len(content) > MAX_BLOCK_BYTES:
            raise ProjectionIntegrityError("task_scope_projection_block_too_large")
        content_hash = hashlib.sha256(content).hexdigest()
        block_id = _uuid(f"task-scope-read-block:{source.source_hash}:EVIDENCE:{kind}:{content_hash}")
        await db.execute(
            "INSERT OR IGNORE INTO task_scope_read_blocks("
            "block_id,source_id,view_kind,block_kind,content_sha256,content,created_at) "
            "VALUES (?,?, 'EVIDENCE',?,?,?,?)",
            (block_id, source.source_id, kind, content_hash, content, time.time()),
        )
        return ReadBlockRef(block_id, kind, content_hash, len(content))

    @asynccontextmanager
    async def _connection(self):
        async with aiosqlite.connect(self._db_path) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA busy_timeout=5000")
            yield connection


def _ref_json(ref: ReadBlockRef) -> dict[str, object]:
    return {
        "block_id": ref.block_id,
        "block_kind": ref.block_kind,
        "content_sha256": ref.content_sha256,
        "byte_length": ref.byte_length,
    }


def _verified_content(row: aiosqlite.Row) -> bytes:
    content = bytes(row["content"])
    if len(content) > MAX_BLOCK_BYTES or hashlib.sha256(content).hexdigest() != row["content_sha256"]:
        raise ProjectionIntegrityError("task_scope_projection_block_hash_mismatch")
    return content


def _bounded_view(kind: str, value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    if kind != "README":
        return canonical_json(
            {
                "schema_version": 1,
                "bounded": True,
                "view_kind": kind,
                "full_content_sha256": hashlib.sha256(encoded).hexdigest(),
                "full_byte_length": len(encoded),
                "details_view": "EVIDENCE",
            }
        )
    suffix = "\n…[bounded; details are content-addressed in EVIDENCE]\n".encode()
    prefix = encoded[: limit - len(suffix)]
    while True:
        try:
            return prefix.decode("utf-8") + suffix.decode("utf-8")
        except UnicodeDecodeError:
            prefix = prefix[:-1]
