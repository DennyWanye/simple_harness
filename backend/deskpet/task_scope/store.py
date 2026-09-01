# SPDX-License-Identifier: BUSL-1.1

"""Canonical, append-only TaskScope authority owned by the Host."""

from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import (
    TaskScopeProtocolError,
    canonical_hash,
    canonical_json,
    identifier,
    reject_private_payload,
    validate_mutation_plan,
    validate_refs,
)


class TaskScopeConflict(RuntimeError):
    code = "task_scope_conflict"


class TaskScopeNotFound(LookupError):
    code = "task_scope_not_found"


@dataclass(frozen=True, slots=True)
class TaskScopeReceipt:
    task_scope_id: str
    subject: str
    revision: int
    state_hash: str


@dataclass(frozen=True, slots=True)
class TaskEventReceipt:
    event_id: str
    task_scope_id: str
    event_sequence: int
    source_event_id: str
    payload_hash: str


@dataclass(frozen=True, slots=True)
class MutationApplyReceipt:
    decision_id: str
    plan_id: str
    plan_hash: str
    task_scope_id: str
    prior_revision: int
    committed_revision: int
    state_hash: str
    event_id: str


@dataclass(frozen=True, slots=True)
class CheckpointReceipt:
    checkpoint_id: str
    task_scope_id: str
    revision: int
    checkpoint_hash: str
    event_watermark: int


@dataclass(frozen=True, slots=True)
class DeterministicEventBatchReceipt:
    task_scope_id: str
    count: int
    first_event_id: str
    first_event_sequence: int
    last_event_id: str
    last_event_sequence: int
    source_id: str
    source_hash: str


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))


class CanonicalTaskScopeStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def initialize(self) -> None:
        # TaskScope is an opt-in continuation of the same fresh data epoch.
        await initialize_human_memory_program_state_db(self._db_path)

    async def create_task_scope(
        self, *, task_scope_id: str, subject: str, title: str
    ) -> TaskScopeReceipt:
        task_scope_id = identifier(task_scope_id, "task_scope_id", 512)
        subject = identifier(subject, "subject", 512)
        title = identifier(title, "title", 4096)
        await HumanMemoryProgramStore(self._db_path).initialize_subject(subject)
        created_at = time.time()
        state = {
            "schema_version": 1,
            "task_scope_id": task_scope_id,
            "subject": subject,
            "title": title,
            "status": "active",
            "goal": None,
            "resume": None,
            "operations": [],
        }
        state_json = canonical_json(state)
        state_hash = canonical_hash(state)
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                row = await self._fetchone(
                    db,
                    "SELECT subject,title FROM task_scopes WHERE task_scope_id=?",
                    (task_scope_id,),
                )
                if row is not None:
                    if row["subject"] != subject or row["title"] != title:
                        raise TaskScopeConflict("task_scope_identity_conflict")
                    head = await self._head_tx(db, task_scope_id)
                    await db.commit()
                    return TaskScopeReceipt(task_scope_id, subject, int(head["current_revision"]), str(head["state_hash"]))
                await db.execute(
                    "INSERT INTO task_scopes(task_scope_id,subject,title,created_at) VALUES (?,?,?,?)",
                    (task_scope_id, subject, title, created_at),
                )
                await db.execute(
                    "INSERT INTO task_scope_canonical_revisions(task_scope_id,revision,prior_revision,decision_id,state_hash,state_json,event_watermark,created_at) VALUES (?,1,NULL,NULL,?,?,0,?)",
                    (task_scope_id, state_hash, state_json, created_at),
                )
                await db.execute(
                    "INSERT INTO task_scope_heads(task_scope_id,current_revision,event_watermark,state_hash,updated_at) VALUES (?,1,0,?,?)",
                    (task_scope_id, state_hash, created_at),
                )
                await self._write_projection_tx(db, task_scope_id, 1, state, created_at)
                from deskpet.task_scope.projection_sources import append_projection_source_tx

                await append_projection_source_tx(db, task_scope_id, now=created_at)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return TaskScopeReceipt(task_scope_id, subject, 1, state_hash)

    async def append_host_event(
        self,
        *,
        task_scope_id: str,
        event_kind: str,
        source_event_id: str,
        payload: Mapping[str, object],
        occurred_at: float | None = None,
        reason_code: str | None = None,
    ) -> TaskEventReceipt:
        if event_kind not in {"host.turn", "host.file", "host.test"}:
            raise TaskScopeProtocolError("host_event_kind_rejected")
        identifier(task_scope_id, "task_scope_id", 512)
        identifier(source_event_id, "source_event_id", 512)
        if not isinstance(payload, Mapping):
            raise TaskScopeProtocolError("host_event_payload_invalid")
        payload_dict = dict(payload)
        reject_private_payload(payload_dict)
        payload_hash = canonical_hash(payload_dict)
        event_time = time.time() if occurred_at is None else float(occurred_at)
        if not math.isfinite(event_time) or event_time < 0:
            raise TaskScopeProtocolError("host_event_occurred_at_invalid")
        if reason_code is not None:
            identifier(reason_code, "reason_code", 512)
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._event_by_source_tx(db, source_event_id)
                if existing is not None:
                    if existing["payload_hash"] != payload_hash or existing["task_scope_id"] != task_scope_id:
                        raise TaskScopeConflict("source_event_payload_conflict")
                    await db.commit()
                    return self._event_receipt(existing)
                receipt = await self._append_event_tx(
                    db,
                    task_scope_id=task_scope_id,
                    event_kind=event_kind,
                    source_kind="host",
                    source_event_id=source_event_id,
                    payload=payload_dict,
                    payload_hash=payload_hash,
                    occurred_at=event_time,
                    reason_code=reason_code,
                )
                await db.execute(
                    "UPDATE task_scope_heads SET event_watermark=?,updated_at=? "
                    "WHERE task_scope_id=? AND event_watermark<?",
                    (receipt.event_sequence, time.time(), task_scope_id, receipt.event_sequence),
                )
                from deskpet.task_scope.projection_sources import append_projection_source_tx

                await append_projection_source_tx(db, task_scope_id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return receipt

    async def apply_mutation_plan(self, plan: object) -> MutationApplyReceipt:
        raw, plan_hash = validate_mutation_plan(plan)
        task_scope_id = str(raw["task_scope_id"])
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM task_scope_mutation_attempts WHERE plan_id=?",
                    (raw["plan_id"],),
                )
                if existing is not None:
                    if existing["plan_hash"] != plan_hash:
                        raise TaskScopeConflict("mutation_plan_id_hash_conflict")
                    if existing["result"] == "cas_conflict":
                        await db.commit()
                        raise TaskScopeConflict("mutation_base_revision_conflict")
                    result = await self._mutation_receipt_tx(db, str(raw["plan_id"]))
                    await db.commit()
                    return result
                scope = await self._fetchone(
                    db, "SELECT subject FROM task_scopes WHERE task_scope_id=?", (task_scope_id,)
                )
                if scope is None:
                    raise TaskScopeNotFound(TaskScopeNotFound.code)
                if scope["subject"] != raw["subject"]:
                    raise TaskScopeProtocolError("mutation_subject_binding_mismatch")
                head = await self._head_tx(db, task_scope_id)
                observed = int(head["current_revision"])
                attempt_id = _uuid(f"task-scope-mutation-attempt:{raw['plan_id']}")
                created_at = time.time()
                if int(raw["base_revision"]) != observed:
                    await db.execute(
                        "INSERT INTO task_scope_mutation_attempts(attempt_id,task_scope_id,plan_id,plan_hash,requested_base_revision,observed_revision,result,plan_json,created_at) VALUES (?,?,?,?,?,?,'cas_conflict',?,?)",
                        (attempt_id, task_scope_id, raw["plan_id"], plan_hash, raw["base_revision"], observed, canonical_json(raw), created_at),
                    )
                    await db.commit()
                    raise TaskScopeConflict("mutation_base_revision_conflict")

                refs = self._merged_refs(raw)
                await self._verify_refs_tx(db, refs)
                event = await self._append_event_tx(
                    db,
                    task_scope_id=task_scope_id,
                    event_kind="mutation.plan",
                    source_kind="mutation",
                    source_event_id=f"mutation-plan:{raw['plan_id']}",
                    payload=raw,
                    payload_hash=plan_hash,
                    occurred_at=created_at,
                    reason_code=str(raw["closure_reason"]) if raw["closure_reason"] is not None else None,
                )
                await self._link_refs_tx(db, task_scope_id, event.event_id, refs, created_at)
                current_row = await self._fetchone(
                    db,
                    "SELECT state_json FROM task_scope_canonical_revisions WHERE task_scope_id=? AND revision=?",
                    (task_scope_id, observed),
                )
                assert current_row is not None
                state = json.loads(str(current_row["state_json"]))
                state = self._reduce(state, raw)
                committed = observed + 1
                state_hash = canonical_hash(state)
                decision_id = _uuid(f"task-scope-decision:{raw['plan_id']}")
                await db.execute(
                    "INSERT INTO task_scope_mutation_attempts(attempt_id,task_scope_id,plan_id,plan_hash,requested_base_revision,observed_revision,result,plan_json,created_at) VALUES (?,?,?,?,?,?,'applied',?,?)",
                    (attempt_id, task_scope_id, raw["plan_id"], plan_hash, observed, observed, canonical_json(raw), created_at),
                )
                await db.execute(
                    "INSERT INTO task_scope_mutation_decisions(decision_id,task_scope_id,plan_id,plan_hash,base_revision,committed_revision,outcome,plan_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (decision_id, task_scope_id, raw["plan_id"], plan_hash, observed, committed, raw["outcome"], canonical_json(raw), created_at),
                )
                await db.execute(
                    "INSERT INTO task_scope_canonical_revisions(task_scope_id,revision,prior_revision,decision_id,state_hash,state_json,event_watermark,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (task_scope_id, committed, observed, decision_id, state_hash, canonical_json(state), event.event_sequence, created_at),
                )
                for operation in raw["operations"]:
                    assert isinstance(operation, Mapping)
                    if str(operation["kind"]).startswith("plan."):
                        await db.execute(
                            "INSERT INTO task_scope_steps(step_record_id,task_scope_id,event_id,operation_id,operation_kind,value,reason_code,created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (_uuid(f"task-scope-step:{raw['plan_id']}:{operation['operation_id']}"), task_scope_id, event.event_id, operation["operation_id"], operation["kind"], operation["value"], operation["reason_code"], created_at),
                        )
                updated = await db.execute(
                    "UPDATE task_scope_heads SET current_revision=?,event_watermark=?,state_hash=?,updated_at=? WHERE task_scope_id=? AND current_revision=?",
                    (committed, event.event_sequence, state_hash, created_at, task_scope_id, observed),
                )
                if updated.rowcount != 1:
                    raise TaskScopeConflict("mutation_base_revision_conflict")
                await self._write_projection_tx(db, task_scope_id, committed, state, created_at)
                from deskpet.task_scope.projection_sources import append_projection_source_tx

                await append_projection_source_tx(db, task_scope_id, now=created_at)
                await db.commit()
            except TaskScopeConflict:
                # A recorded CAS conflict is committed above; other conflicts
                # leave no partial canonical transaction behind.
                if db.in_transaction:
                    await db.rollback()
                raise
            except Exception:
                await db.rollback()
                raise
        return MutationApplyReceipt(decision_id, str(raw["plan_id"]), plan_hash, task_scope_id, observed, committed, state_hash, event.event_id)

    async def create_checkpoint(
        self,
        *,
        checkpoint_id: str,
        task_scope_id: str,
        revision: int | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> CheckpointReceipt:
        identifier(checkpoint_id, "checkpoint_id", 512)
        identifier(task_scope_id, "task_scope_id", 512)
        payload_meta = dict(metadata or {})
        reject_private_payload(payload_meta)
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(db, "SELECT * FROM task_scope_checkpoints WHERE checkpoint_id=?", (checkpoint_id,))
                head = await self._head_tx(db, task_scope_id)
                if revision is not None and (
                    isinstance(revision, bool) or not isinstance(revision, int) or revision < 1
                ):
                    raise TaskScopeProtocolError("checkpoint_revision_invalid")
                target_revision = int(head["current_revision"]) if revision is None else revision
                canonical = await self._fetchone(
                    db,
                    "SELECT state_hash,state_json,event_watermark FROM task_scope_canonical_revisions WHERE task_scope_id=? AND revision=?",
                    (task_scope_id, target_revision),
                )
                if canonical is None:
                    raise TaskScopeNotFound("canonical_revision_not_found")
                payload = {
                    "schema_version": 1,
                    "task_scope_id": task_scope_id,
                    "revision": target_revision,
                    "state_hash": canonical["state_hash"],
                    "state": json.loads(str(canonical["state_json"])),
                    "metadata": payload_meta,
                }
                checkpoint_hash = canonical_hash(payload)
                if existing is not None:
                    if existing["checkpoint_hash"] != checkpoint_hash or existing["task_scope_id"] != task_scope_id:
                        raise TaskScopeConflict("checkpoint_id_hash_conflict")
                    await db.commit()
                    return self._checkpoint_receipt(existing)
                created_at = time.time()
                await db.execute(
                    "INSERT INTO task_scope_checkpoints(checkpoint_id,task_scope_id,revision,checkpoint_hash,checkpoint_json,event_watermark,created_at) VALUES (?,?,?,?,?,?,?)",
                    (checkpoint_id, task_scope_id, target_revision, checkpoint_hash, canonical_json(payload), head["event_watermark"], created_at),
                )
                from deskpet.task_scope.projection_sources import append_projection_source_tx

                await append_projection_source_tx(db, task_scope_id, now=created_at)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return CheckpointReceipt(checkpoint_id, task_scope_id, target_revision, checkpoint_hash, int(head["event_watermark"]))

    async def append_deterministic_events(
        self,
        *,
        task_scope_id: str,
        subject: str,
        count: int,
        canary: str,
        batch_size: int = 1000,
    ) -> DeterministicEventBatchReceipt:
        """Public test seam for large deterministic histories.

        Rows are inserted in bounded batches inside one transaction and produce
        exactly one source-change/outbox identity. It deliberately accepts no
        arbitrary SQL or payload callback.
        """

        identifier(task_scope_id, "task_scope_id", 512)
        identifier(subject, "subject", 512)
        identifier(canary, "canary", 4096)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100_000:
            raise ValueError("deterministic_event_count_invalid")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 5000:
            raise ValueError("deterministic_event_batch_size_invalid")
        canary_hash = canonical_hash({"canary": canary})
        first_event_id = ""
        last_event_id = ""
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                head = await self._head_tx(db, task_scope_id)
                scope = await self._fetchone(
                    db, "SELECT subject FROM task_scopes WHERE task_scope_id=?", (task_scope_id,)
                )
                if scope is None or scope["subject"] != subject:
                    raise TaskScopeNotFound("deterministic_event_scope_subject_mismatch")
                first_sequence = int(head["event_watermark"]) + 1
                now = time.time()
                for offset in range(0, count, batch_size):
                    rows: list[tuple[object, ...]] = []
                    for index in range(offset, min(offset + batch_size, count)):
                        sequence = first_sequence + index
                        source_event_id = f"deterministic:{canary_hash}:{index + 1}"
                        payload = {
                            "schema_version": 1,
                            "canary": canary,
                            "event_index": index + 1,
                            "unicode": "记忆-🧠" if (index + 1) % 97 == 0 else "",
                        }
                        payload_hash = canonical_hash(payload)
                        event_id = _uuid(f"task-scope-event:{source_event_id}")
                        if index == 0:
                            first_event_id = event_id
                        last_event_id = event_id
                        rows.append(
                            (
                                event_id,
                                task_scope_id,
                                sequence,
                                "host.turn",
                                "host",
                                source_event_id,
                                payload_hash,
                                canonical_json(payload),
                                None,
                                float(index + 1),
                                now,
                            )
                        )
                    await db.executemany(
                        "INSERT INTO task_scope_events(event_id,task_scope_id,event_sequence,"
                        "event_kind,source_kind,source_event_id,payload_hash,payload_json,"
                        "reason_code,occurred_at,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        rows,
                    )
                last_sequence = first_sequence + count - 1
                await db.execute(
                    "UPDATE task_scope_heads SET event_watermark=?,updated_at=? WHERE task_scope_id=?",
                    (last_sequence, now, task_scope_id),
                )
                from deskpet.task_scope.projection_sources import append_projection_source_tx

                source = await append_projection_source_tx(db, task_scope_id, now=now)
                assert source is not None
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return DeterministicEventBatchReceipt(
            task_scope_id,
            count,
            first_event_id,
            first_sequence,
            last_event_id,
            last_sequence,
            source.source_id,
            source.source_hash,
        )

    async def rebuild_projection(self, task_scope_id: str, revision: int) -> str:
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                row = await self._fetchone(db, "SELECT state_json FROM task_scope_canonical_revisions WHERE task_scope_id=? AND revision=?", (task_scope_id, revision))
                if row is None:
                    raise TaskScopeNotFound("canonical_revision_not_found")
                state = json.loads(str(row["state_json"]))
                projection_hash = canonical_hash(state)
                await db.execute(
                    "INSERT INTO task_scope_projection_cache(task_scope_id,canonical_revision,projection_hash,projection_json,rebuilt_at) VALUES (?,?,?,?,?) ON CONFLICT(task_scope_id,canonical_revision) DO UPDATE SET projection_hash=excluded.projection_hash,projection_json=excluded.projection_json,rebuilt_at=excluded.rebuilt_at",
                    (task_scope_id, revision, projection_hash, canonical_json(state), time.time()),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return projection_hash

    async def _write_projection_tx(self, db: aiosqlite.Connection, task_scope_id: str, revision: int, state: Mapping[str, object], now: float) -> None:
        projection_hash = canonical_hash(state)
        projection_id = _uuid(f"task-scope-projection:{task_scope_id}:{revision}")
        await db.execute(
            "INSERT INTO task_scope_projection_revisions(projection_revision_id,task_scope_id,canonical_revision,projection_hash,created_at) VALUES (?,?,?,?,?)",
            (projection_id, task_scope_id, revision, projection_hash, now),
        )
        await db.execute(
            "INSERT INTO task_scope_projection_cache(task_scope_id,canonical_revision,projection_hash,projection_json,rebuilt_at) VALUES (?,?,?,?,?)",
            (task_scope_id, revision, projection_hash, canonical_json(state), now),
        )
        await db.execute(
            "INSERT INTO task_scope_projection_outbox(outbox_id,task_scope_id,canonical_revision,projection_hash,created_at) VALUES (?,?,?,?,?)",
            (_uuid(f"task-scope-projection-outbox:{task_scope_id}:{revision}"), task_scope_id, revision, projection_hash, now),
        )
        await db.execute(
            "INSERT INTO task_scope_search_outbox(outbox_id,task_scope_id,canonical_revision,state_hash,created_at) VALUES (?,?,?,?,?)",
            (_uuid(f"task-scope-search-outbox:{task_scope_id}:{revision}"), task_scope_id, revision, projection_hash, now),
        )

    async def _append_event_tx(self, db: aiosqlite.Connection, *, task_scope_id: str, event_kind: str, source_kind: str, source_event_id: str, payload: Mapping[str, object], payload_hash: str, occurred_at: float, reason_code: str | None) -> TaskEventReceipt:
        scope = await self._fetchone(db, "SELECT 1 FROM task_scopes WHERE task_scope_id=?", (task_scope_id,))
        if scope is None:
            raise TaskScopeNotFound(TaskScopeNotFound.code)
        row = await self._fetchone(db, "SELECT COALESCE(MAX(event_sequence),0)+1 AS next_sequence FROM task_scope_events WHERE task_scope_id=?", (task_scope_id,))
        sequence = int(row["next_sequence"])
        event_id = _uuid(f"task-scope-event:{source_event_id}")
        committed_at = time.time()
        await db.execute(
            "INSERT INTO task_scope_events(event_id,task_scope_id,event_sequence,event_kind,source_kind,source_event_id,payload_hash,payload_json,reason_code,occurred_at,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, task_scope_id, sequence, event_kind, source_kind, source_event_id, payload_hash, canonical_json(payload), reason_code, occurred_at, committed_at),
        )
        return TaskEventReceipt(event_id, task_scope_id, sequence, source_event_id, payload_hash)

    async def _verify_refs_tx(self, db: aiosqlite.Connection, refs: Sequence[Mapping[str, object]]) -> None:
        for ref in refs:
            row = await self._fetchone(db, "SELECT envelope_sha256 FROM human_memory_evidence WHERE evidence_id=?", (ref["evidence_id"],))
            if row is None or row["envelope_sha256"] != ref["content_hash"]:
                raise TaskScopeProtocolError("evidence_ref_authority_mismatch")

    async def _link_refs_tx(self, db: aiosqlite.Connection, task_scope_id: str, event_id: str, refs: Sequence[Mapping[str, object]], now: float) -> None:
        for ordinal, ref in enumerate(refs, 1):
            await db.execute(
                "INSERT INTO task_scope_evidence_links(link_id,task_scope_id,event_id,evidence_id,content_hash,ordinal,created_at) VALUES (?,?,?,?,?,?,?)",
                (_uuid(f"task-scope-evidence-link:{event_id}:{ref['evidence_id']}"), task_scope_id, event_id, ref["evidence_id"], ref["content_hash"], ordinal, now),
            )

    @staticmethod
    def _merged_refs(raw: Mapping[str, object]) -> list[dict[str, object]]:
        candidates = list(validate_refs(raw["evidence_refs"], required=True))
        for operation in raw["operations"]:  # type: ignore[index]
            candidates.extend(validate_refs(operation["evidence_refs"], required=True))
        unique: dict[str, dict[str, object]] = {}
        for item in candidates:
            previous = unique.get(str(item["evidence_id"]))
            if previous is not None and previous["content_hash"] != item["content_hash"]:
                raise TaskScopeProtocolError("evidence_ref_hash_conflict")
            unique[str(item["evidence_id"])] = item
        return list(unique.values())

    @staticmethod
    def _reduce(state: dict[str, Any], plan: Mapping[str, object]) -> dict[str, Any]:
        result = json.loads(canonical_json(state))
        for raw_operation in plan["operations"]:  # type: ignore[index]
            operation = dict(raw_operation)
            entry = {
                "operation_id": operation["operation_id"],
                "kind": operation["kind"],
                "value": operation["value"],
                "reason_code": operation["reason_code"],
                "plan_id": plan["plan_id"],
            }
            result["operations"].append(entry)
            kind = operation["kind"]
            if kind in {"goal.set", "goal.revise"}:
                result["goal"] = operation["value"]
            elif kind == "resume.update":
                result["resume"] = operation["value"]
            elif kind == "task.pause":
                result["status"] = "paused"
            elif kind == "task.block":
                result["status"] = "blocked"
            elif kind == "task.resume":
                result["status"] = "active"
            elif kind == "task.complete":
                result["status"] = "complete"
        result["last_plan_id"] = plan["plan_id"]
        result["last_outcome"] = plan["outcome"]
        result["closure_reason"] = plan["closure_reason"]
        return result

    async def _mutation_receipt_tx(self, db: aiosqlite.Connection, plan_id: str) -> MutationApplyReceipt:
        row = await self._fetchone(
            db,
            "SELECT d.*,r.prior_revision,r.state_hash,e.event_id FROM task_scope_mutation_decisions d JOIN task_scope_canonical_revisions r ON r.decision_id=d.decision_id JOIN task_scope_events e ON e.source_event_id='mutation-plan:' || d.plan_id WHERE d.plan_id=?",
            (plan_id,),
        )
        assert row is not None
        return MutationApplyReceipt(str(row["decision_id"]), plan_id, str(row["plan_hash"]), str(row["task_scope_id"]), int(row["prior_revision"]), int(row["committed_revision"]), str(row["state_hash"]), str(row["event_id"]))

    async def _head_tx(self, db: aiosqlite.Connection, task_scope_id: str) -> aiosqlite.Row:
        row = await self._fetchone(db, "SELECT * FROM task_scope_heads WHERE task_scope_id=?", (task_scope_id,))
        if row is None:
            raise TaskScopeNotFound(TaskScopeNotFound.code)
        return row

    async def _event_by_source_tx(self, db: aiosqlite.Connection, source_event_id: str) -> aiosqlite.Row | None:
        return await self._fetchone(db, "SELECT * FROM task_scope_events WHERE source_event_id=?", (source_event_id,))

    @staticmethod
    async def _fetchone(db: aiosqlite.Connection, sql: str, parameters: tuple[object, ...]) -> aiosqlite.Row | None:
        cursor = await db.execute(sql, parameters)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    @staticmethod
    def _event_receipt(row: aiosqlite.Row) -> TaskEventReceipt:
        return TaskEventReceipt(str(row["event_id"]), str(row["task_scope_id"]), int(row["event_sequence"]), str(row["source_event_id"]), str(row["payload_hash"]))

    @staticmethod
    def _checkpoint_receipt(row: aiosqlite.Row) -> CheckpointReceipt:
        return CheckpointReceipt(str(row["checkpoint_id"]), str(row["task_scope_id"]), int(row["revision"]), str(row["checkpoint_hash"]), int(row["event_watermark"]))

    @asynccontextmanager
    async def _connection(self):
        async with aiosqlite.connect(self._db_path) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA busy_timeout=5000")
            yield connection


class TaskEventRecorder:
    """Records Host-native facts without routing them through an LLM."""

    def __init__(self, store: CanonicalTaskScopeStore) -> None:
        self._store = store

    async def record_turn(self, **kwargs: object) -> TaskEventReceipt:
        return await self._store.append_host_event(event_kind="host.turn", **kwargs)  # type: ignore[arg-type]

    async def record_file(self, **kwargs: object) -> TaskEventReceipt:
        return await self._store.append_host_event(event_kind="host.file", **kwargs)  # type: ignore[arg-type]

    async def record_test(self, **kwargs: object) -> TaskEventReceipt:
        return await self._store.append_host_event(event_kind="host.test", **kwargs)  # type: ignore[arg-type]
