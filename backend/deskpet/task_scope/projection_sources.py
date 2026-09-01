# SPDX-License-Identifier: BUSL-1.1

"""Exact, monotonic source identities for deterministic TaskScope projections."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

import aiosqlite

from deskpet.task_scope.protocol import canonical_hash, canonical_json
from deskpet.task_scope.store import TaskScopeNotFound, _uuid

RENDERER_CONTRACT_VERSION = "task-scope-views/v1"


@dataclass(frozen=True, slots=True)
class ProjectionSourceReceipt:
    source_id: str
    task_scope_id: str
    source_sequence: int
    canonical_revision: int
    state_hash: str
    event_watermark: int
    event_prefix_root: str
    checkpoint_sequence: int
    checkpoint_set_root: str
    binding_set_revision: int
    binding_receipt_hash: str | None
    renderer_contract_version: str
    source_hash: str


async def projection_source_schema_ready(db: aiosqlite.Connection) -> bool:
    cursor = await db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='task_scope_projection_sources'"
    )
    result = await cursor.fetchone()
    await cursor.close()
    return result is not None


def _fold_hash(domain: str, hashes: list[str]) -> str:
    digest = hashlib.sha256(f"{domain}\0".encode("utf-8")).hexdigest()
    for value in hashes:
        digest = canonical_hash(
            {"domain": domain, "prior_root": digest, "item_hash": value}
        )
    return digest


async def append_projection_source_tx(
    db: aiosqlite.Connection,
    task_scope_id: str,
    *,
    now: float | None = None,
) -> ProjectionSourceReceipt | None:
    """Append the exact current source and outbox row in the caller transaction.

    Older v38 databases intentionally return ``None``. Once v39 is installed,
    every producer calls this before committing its canonical transaction.
    """

    if not await projection_source_schema_ready(db):
        return None
    head_cursor = await db.execute(
        "SELECT h.current_revision,h.event_watermark,h.state_hash "
        "FROM task_scope_heads h WHERE h.task_scope_id=?",
        (task_scope_id,),
    )
    head = await head_cursor.fetchone()
    await head_cursor.close()
    if head is None:
        raise TaskScopeNotFound(TaskScopeNotFound.code)
    revision = int(head["current_revision"])
    event_watermark = int(head["event_watermark"])
    current_cursor = await db.execute(
        "SELECT s.* FROM task_scope_projection_source_heads h "
        "JOIN task_scope_projection_sources s ON s.source_id=h.source_id "
        "WHERE h.task_scope_id=?",
        (task_scope_id,),
    )
    current = await current_cursor.fetchone()
    await current_cursor.close()
    prior_event_watermark = 0 if current is None else int(current["event_watermark"])
    if prior_event_watermark > event_watermark:
        raise RuntimeError("task_scope_projection_event_watermark_regressed")

    event_cursor = await db.execute(
        "SELECT e.event_id,e.event_sequence,e.event_kind,e.source_kind,"
        "e.source_event_id,e.payload_hash,e.payload_json,e.reason_code,"
        "e.occurred_at,e.committed_at "
        "FROM task_scope_events e WHERE e.task_scope_id=? "
        "AND e.event_sequence>? AND e.event_sequence<=? "
        "ORDER BY e.event_sequence",
        (task_scope_id, prior_event_watermark, event_watermark),
    )
    events = await event_cursor.fetchall()
    await event_cursor.close()
    step_cursor = await db.execute(
        "SELECT s.event_id,s.step_record_id,s.operation_id,s.operation_kind,s.value,s.reason_code "
        "FROM task_scope_steps s JOIN task_scope_events e ON e.event_id=s.event_id "
        "WHERE e.task_scope_id=? AND e.event_sequence>? AND e.event_sequence<=? "
        "ORDER BY s.event_id,s.step_record_id",
        (task_scope_id, prior_event_watermark, event_watermark),
    )
    step_map: dict[str, list[dict[str, object]]] = {}
    for row in await step_cursor.fetchall():
        item = dict(row)
        step_map.setdefault(str(item.pop("event_id")), []).append(item)
    await step_cursor.close()
    link_cursor = await db.execute(
        "SELECT l.event_id,l.link_id,l.evidence_id,l.content_hash,l.ordinal "
        "FROM task_scope_evidence_links l JOIN task_scope_events e ON e.event_id=l.event_id "
        "WHERE e.task_scope_id=? AND e.event_sequence>? AND e.event_sequence<=? "
        "ORDER BY l.event_id,l.ordinal,l.link_id",
        (task_scope_id, prior_event_watermark, event_watermark),
    )
    link_map: dict[str, list[dict[str, object]]] = {}
    for row in await link_cursor.fetchall():
        item = dict(row)
        link_map.setdefault(str(item.pop("event_id")), []).append(item)
    await link_cursor.close()
    event_hashes: list[str] = []
    for event in events:
        steps = step_map.get(str(event["event_id"]), [])
        links = link_map.get(str(event["event_id"]), [])
        event_hashes.append(
            canonical_hash(
                {
                    "event": {
                        "event_id": event["event_id"],
                        "event_sequence": event["event_sequence"],
                        "event_kind": event["event_kind"],
                        "source_kind": event["source_kind"],
                        "source_event_id": event["source_event_id"],
                        "payload_hash": event["payload_hash"],
                        "payload": json.loads(str(event["payload_json"])),
                        "reason_code": event["reason_code"],
                        "occurred_at": event["occurred_at"],
                        "committed_at": event["committed_at"],
                    },
                    "steps": steps,
                    "evidence_links": links,
                }
            )
        )
    event_prefix_root = (
        _fold_hash("task-scope-events/v1", [])
        if current is None
        else str(current["event_prefix_root"])
    )
    for event_hash in event_hashes:
        event_prefix_root = canonical_hash(
            {
                "domain": "task-scope-events/v1",
                "prior_root": event_prefix_root,
                "item_hash": event_hash,
            }
        )

    prior_checkpoint_sequence = 0 if current is None else int(current["checkpoint_sequence"])
    checkpoint_cursor = await db.execute(
        "SELECT checkpoint_id,checkpoint_hash FROM task_scope_checkpoints "
        "WHERE task_scope_id=? ORDER BY created_at,checkpoint_id LIMIT -1 OFFSET ?",
        (task_scope_id, prior_checkpoint_sequence),
    )
    checkpoints = await checkpoint_cursor.fetchall()
    await checkpoint_cursor.close()
    checkpoint_hashes = [
        canonical_hash(
            {"checkpoint_id": row["checkpoint_id"], "checkpoint_hash": row["checkpoint_hash"]}
        )
        for row in checkpoints
    ]
    checkpoint_set_root = (
        _fold_hash("task-scope-checkpoints/v1", [])
        if current is None
        else str(current["checkpoint_set_root"])
    )
    for checkpoint_hash in checkpoint_hashes:
        checkpoint_set_root = canonical_hash(
            {
                "domain": "task-scope-checkpoints/v1",
                "prior_root": checkpoint_set_root,
                "item_hash": checkpoint_hash,
            }
        )
    checkpoint_sequence = prior_checkpoint_sequence + len(checkpoints)

    binding_cursor = await db.execute(
        "SELECT current_revision,current_receipt_hash FROM task_workspace_binding_heads "
        "WHERE task_scope_id=?",
        (task_scope_id,),
    )
    binding = await binding_cursor.fetchone()
    await binding_cursor.close()
    binding_revision = 0 if binding is None else int(binding["current_revision"])
    binding_hash = None if binding is None else str(binding["current_receipt_hash"])

    source = {
        "schema_version": 1,
        "task_scope_id": task_scope_id,
        "canonical_revision": revision,
        "state_hash": str(head["state_hash"]),
        "event_watermark": event_watermark,
        "event_prefix_root": event_prefix_root,
        "checkpoint_sequence": checkpoint_sequence,
        "checkpoint_set_root": checkpoint_set_root,
        "binding_set_revision": binding_revision,
        "binding_receipt_hash": binding_hash,
        "renderer_contract_version": RENDERER_CONTRACT_VERSION,
    }
    source_hash = canonical_hash(source)
    if current is not None and current["source_hash"] == source_hash:
        return _receipt(current)
    sequence = 1 if current is None else int(current["source_sequence"]) + 1
    source_id = _uuid(f"task-scope-projection-source:{task_scope_id}:{sequence}:{source_hash}")
    created_at = time.time() if now is None else now
    await db.execute(
        "INSERT INTO task_scope_projection_sources("
        "source_id,task_scope_id,source_sequence,canonical_revision,state_hash,"
        "event_watermark,event_prefix_root,checkpoint_sequence,checkpoint_set_root,"
        "binding_set_revision,binding_receipt_hash,renderer_contract_version,"
        "source_hash,source_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            task_scope_id,
            sequence,
            revision,
            source["state_hash"],
            event_watermark,
            event_prefix_root,
            checkpoint_sequence,
            checkpoint_set_root,
            binding_revision,
            binding_hash,
            RENDERER_CONTRACT_VERSION,
            source_hash,
            canonical_json(source),
            created_at,
        ),
    )
    await db.execute(
        "INSERT INTO task_scope_projection_source_outbox("
        "outbox_id,task_scope_id,source_id,covered_from_sequence,"
        "covered_through_sequence,source_hash,created_at) VALUES (?,?,?,?,?,?,?)",
        (
            _uuid(f"task-scope-projection-source-outbox:{source_id}"),
            task_scope_id,
            source_id,
            sequence,
            sequence,
            source_hash,
            created_at,
        ),
    )
    await db.execute(
        "INSERT INTO task_scope_projection_source_heads("
        "task_scope_id,source_sequence,source_id,source_hash,updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(task_scope_id) DO UPDATE SET source_sequence=excluded.source_sequence,"
        "source_id=excluded.source_id,source_hash=excluded.source_hash,updated_at=excluded.updated_at",
        (task_scope_id, sequence, source_id, source_hash, created_at),
    )
    row_cursor = await db.execute(
        "SELECT * FROM task_scope_projection_sources WHERE source_id=?", (source_id,)
    )
    row = await row_cursor.fetchone()
    await row_cursor.close()
    assert row is not None
    return _receipt(row)


async def load_projection_source_tx(
    db: aiosqlite.Connection, *, source_id: str | None = None, task_scope_id: str | None = None
) -> ProjectionSourceReceipt:
    if (source_id is None) == (task_scope_id is None):
        raise ValueError("projection_source_selector_invalid")
    if source_id is not None:
        cursor = await db.execute(
            "SELECT * FROM task_scope_projection_sources WHERE source_id=?", (source_id,)
        )
    else:
        cursor = await db.execute(
            "SELECT s.* FROM task_scope_projection_source_heads h "
            "JOIN task_scope_projection_sources s ON s.source_id=h.source_id "
            "WHERE h.task_scope_id=?",
            (task_scope_id,),
        )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        raise TaskScopeNotFound("task_scope_projection_source_not_found")
    return _receipt(row)


def _receipt(row: aiosqlite.Row) -> ProjectionSourceReceipt:
    return ProjectionSourceReceipt(
        source_id=str(row["source_id"]),
        task_scope_id=str(row["task_scope_id"]),
        source_sequence=int(row["source_sequence"]),
        canonical_revision=int(row["canonical_revision"]),
        state_hash=str(row["state_hash"]),
        event_watermark=int(row["event_watermark"]),
        event_prefix_root=str(row["event_prefix_root"]),
        checkpoint_sequence=int(row["checkpoint_sequence"]),
        checkpoint_set_root=str(row["checkpoint_set_root"]),
        binding_set_revision=int(row["binding_set_revision"]),
        binding_receipt_hash=(
            None if row["binding_receipt_hash"] is None else str(row["binding_receipt_hash"])
        ),
        renderer_contract_version=str(row["renderer_contract_version"]),
        source_hash=str(row["source_hash"]),
    )
