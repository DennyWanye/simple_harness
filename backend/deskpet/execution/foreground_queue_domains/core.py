# SPDX-License-Identifier: BUSL-1.1

"""Connection, transitions, receipts and shared validation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from deskpet.memory.recovery_work_items import is_human_memory_work_item_parked_tx
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx, human_memory_connection
from deskpet.task_scope.protocol import canonical_hash, canonical_json, digest, identifier

from .support import (
    TERMINAL_STATES,
    AdmissionReceipt,
    ContextLineage,
    ControlKind,
    ControlReceipt,
    EnqueueReceipt,
    ExecutionAuditReceipt,
    ForegroundQueueError,
    ForegroundRunSnapshot,
    LeaseReceipt,
    PreparationCandidate,
    PreparationDraftReceipt,
    RunState,
    SdkRunBindingReceipt,
    SignalAckReceipt,
    SignalEnvelope,
    TerminalReceipt,
    _clock_value,
    _uuid,
)


class ForegroundQueueCore:
    """Connection, transitions, receipts and shared validation."""

    if TYPE_CHECKING:

        async def _record_sdk_state(
            self,
            *,
            host_run_id: str,
            sdk_run_id: str,
            owner_id: str,
            generation: int,
            sdk_event_id: str,
            idempotency_key: str,
            allowed_from: set[str],
            to_state: RunState,
            clear_pause: bool,
        ) -> ForegroundRunSnapshot: ...

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._clock = clock
        self._fault_hook = fault_hook

    async def initialize(self) -> None:
        async with self._connection() as db:
            row = await self._fetchone(
                db,
                "SELECT format_epoch,schema_version FROM foreground_queue_marker WHERE singleton=1",
                (),
            )
            execution_row = await self._fetchone(
                db,
                "SELECT format_epoch,schema_version FROM foreground_execution_marker WHERE singleton=1",
                (),
            )
        if (
            row is None
            or row["format_epoch"] != "human-memory-v1"
            or int(row["schema_version"]) != 1
        ):
            raise ForegroundQueueError("foreground_queue_schema_uninitialized")
        if (
            execution_row is None
            or execution_row["format_epoch"] != "human-memory-v1"
            or int(execution_row["schema_version"]) != 1
        ):
            raise ForegroundQueueError("foreground_execution_schema_uninitialized")

    async def _preparation_candidate_tx(
        self, db: aiosqlite.Connection, subject: str
    ) -> PreparationCandidate | None:
        turn = await self._fetchone(
            db,
            "SELECT t.* FROM foreground_turns t "
            "JOIN foreground_turn_heads h ON h.turn_id=t.turn_id "
            "WHERE t.subject=? AND h.current_state='QUEUED' "
            "ORDER BY t.enqueue_sequence,t.turn_id LIMIT 1",
            (subject,),
        )
        if turn is None:
            return None
        # 按需导入：循环依赖（foreground_queue ↔ admission_rejection）
        from deskpet.execution.admission_rejection import read_admission_rejection_tx

        while turn is not None:
            rejected = await read_admission_rejection_tx(
                db, turn_id=turn["turn_id"], subject=subject
            )
            if rejected is None:
                break
            turn = await self._fetchone(
                db,
                "SELECT t.* FROM foreground_turns t JOIN foreground_turn_heads h ON h.turn_id=t.turn_id "
                "WHERE t.subject=? AND h.current_state='QUEUED' AND t.enqueue_sequence>? "
                "ORDER BY t.enqueue_sequence,t.turn_id LIMIT 1",
                (subject, turn["enqueue_sequence"]),
            )
        if turn is None:
            return None
        binding_revision = 0
        binding_receipt_id: str | None = None
        binding_receipt_hash: str | None = None
        if turn["task_scope_id"] is not None:
            binding = await self._fetchone(
                db,
                "SELECT current_revision,current_receipt_id,current_receipt_hash "
                "FROM task_workspace_binding_heads WHERE task_scope_id=? AND subject=?",
                (turn["task_scope_id"], subject),
            )
            if binding is not None:
                binding_revision = int(binding["current_revision"])
                binding_receipt_id = str(binding["current_receipt_id"])
                binding_receipt_hash = str(binding["current_receipt_hash"])
        try:
            turn_payload = json.loads(str(turn["turn_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ForegroundQueueError("foreground_turn_payload_corrupt") from exc
        if not isinstance(turn_payload, dict):
            raise ForegroundQueueError("foreground_turn_payload_corrupt")
        payload = {
            "schema_version": 1,
            "subject": subject,
            "turn_id": turn["turn_id"],
            "primary_conversation_id": turn["primary_conversation_id"],
            "task_scope_id": turn["task_scope_id"],
            "evidence_id": turn["evidence_id"],
            "evidence_hash": turn["evidence_hash"],
            "enqueue_sequence": int(turn["enqueue_sequence"]),
            "turn_hash": turn["turn_hash"],
            "turn": turn_payload,
            "binding_set_revision": binding_revision,
            "binding_set_receipt_id": binding_receipt_id,
            "binding_set_receipt_hash": binding_receipt_hash,
        }
        candidate_json = canonical_json(payload)
        return PreparationCandidate(
            subject=subject,
            turn_id=str(turn["turn_id"]),
            primary_conversation_id=str(turn["primary_conversation_id"]),
            task_scope_id=(
                str(turn["task_scope_id"]) if turn["task_scope_id"] is not None else None
            ),
            evidence_id=str(turn["evidence_id"]),
            evidence_hash=str(turn["evidence_hash"]),
            enqueue_sequence=int(turn["enqueue_sequence"]),
            turn_hash=str(turn["turn_hash"]),
            binding_set_revision=binding_revision,
            binding_set_receipt_id=binding_receipt_id,
            binding_set_receipt_hash=binding_receipt_hash,
            candidate_hash=canonical_hash(payload),
            candidate_json=candidate_json,
        )

    @staticmethod
    def _candidate_from_json(candidate_json: str) -> PreparationCandidate:
        try:
            value = json.loads(candidate_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ForegroundQueueError("foreground_claimed_execution_corrupt") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ForegroundQueueError("foreground_claimed_execution_corrupt")
        try:
            return PreparationCandidate(
                subject=str(value["subject"]),
                turn_id=str(value["turn_id"]),
                primary_conversation_id=str(value["primary_conversation_id"]),
                task_scope_id=(
                    str(value["task_scope_id"]) if value["task_scope_id"] is not None else None
                ),
                evidence_id=str(value["evidence_id"]),
                evidence_hash=str(value["evidence_hash"]),
                enqueue_sequence=int(value["enqueue_sequence"]),
                turn_hash=str(value["turn_hash"]),
                binding_set_revision=int(value["binding_set_revision"]),
                binding_set_receipt_id=(
                    str(value["binding_set_receipt_id"])
                    if value["binding_set_receipt_id"] is not None
                    else None
                ),
                binding_set_receipt_hash=(
                    str(value["binding_set_receipt_hash"])
                    if value["binding_set_receipt_hash"] is not None
                    else None
                ),
                candidate_hash=canonical_hash(value),
                candidate_json=canonical_json(value),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ForegroundQueueError("foreground_claimed_execution_corrupt") from exc

    @staticmethod
    def _preparation_draft(row: aiosqlite.Row) -> PreparationDraftReceipt:
        return PreparationDraftReceipt(
            draft_id=str(row["draft_id"]),
            subject=str(row["subject"]),
            turn_id=str(row["turn_id"]),
            candidate_hash=str(row["candidate_hash"]),
            context_snapshot_id=str(row["context_snapshot_id"]),
            context_snapshot_revision=int(row["context_snapshot_revision"]),
            context_snapshot_hash=str(row["context_snapshot_hash"]),
            draft_hash=str(row["draft_hash"]),
            candidate_json=str(row["candidate_json"]),
        )

    @staticmethod
    def _audit_receipt(
        row: aiosqlite.Row,
        *,
        phase: str,
        id_column: str,
        hash_column: str,
    ) -> ExecutionAuditReceipt:
        return ExecutionAuditReceipt(
            receipt_id=str(row[id_column]),
            host_run_id=str(row["host_run_id"]),
            generation=int(row["generation"]),
            phase=phase,
            receipt_hash=str(row[hash_column]),
        )

    async def _record_execution_event(
        self,
        *,
        table: str,
        phase: str,
        id_prefix: str,
        id_column: str,
        hash_column: str,
        json_column: str,
        state_column: str,
        state_value: str,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        evidence_ref: str | None,
        evidence_hash: str | None,
        error_code: str | None,
        idempotency_key: str,
    ) -> ExecutionAuditReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        if (evidence_ref is None) != (evidence_hash is None):
            raise ForegroundQueueError("foreground_execution_evidence_incomplete")
        if evidence_ref is not None:
            evidence_ref = identifier(evidence_ref, "evidence_ref", 1024)
            evidence_hash = digest(evidence_hash, "evidence_hash")
        if error_code is not None:
            error_code = identifier(error_code, "error_code", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                intent = await self._fetchone(
                    db,
                    "SELECT sdk_run_id FROM foreground_execution_start_intents WHERE host_run_id=?",
                    (host_run_id,),
                )
                if intent is None or intent["sdk_run_id"] != sdk_run_id:
                    raise ForegroundQueueError("foreground_execution_start_intent_missing")
                receipt_id = _uuid(f"{id_prefix}:{host_run_id}:{idempotency_key}")
                payload: dict[str, object] = {
                    "schema_version": 1,
                    id_column: receipt_id,
                    "host_run_id": host_run_id,
                    "sdk_run_id": sdk_run_id,
                    "owner_id": owner_id,
                    "generation": generation,
                    state_column: state_value,
                    "idempotency_key": idempotency_key,
                }
                if phase == "start_observation":
                    payload["result_ref"] = evidence_ref
                    payload["result_hash"] = evidence_hash
                    payload["error_code"] = error_code
                else:
                    payload["evidence_ref"] = evidence_ref
                    payload["evidence_hash"] = evidence_hash
                receipt_hash = canonical_hash(payload)
                existing = await self._fetchone(
                    db,
                    f"SELECT * FROM {table} WHERE host_run_id=? AND idempotency_key=?",
                    (host_run_id, idempotency_key),
                )
                if existing is not None:
                    if existing[hash_column] != receipt_hash:
                        raise ForegroundQueueError(
                            f"foreground_execution_{phase}_idempotency_conflict"
                        )
                    await db.commit()
                    return self._audit_receipt(
                        existing,
                        phase=phase,
                        id_column=id_column,
                        hash_column=hash_column,
                    )
                if phase == "start_observation":
                    await db.execute(
                        "INSERT INTO foreground_execution_start_observations("
                        "observation_id,host_run_id,sdk_run_id,owner_id,generation,outcome,"
                        "result_ref,result_hash,error_code,idempotency_key,observation_hash,"
                        "observation_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            receipt_id,
                            host_run_id,
                            sdk_run_id,
                            owner_id,
                            generation,
                            state_value,
                            evidence_ref,
                            evidence_hash,
                            error_code,
                            idempotency_key,
                            receipt_hash,
                            canonical_json(payload),
                            now,
                        ),
                    )
                else:
                    await db.execute(
                        "INSERT INTO foreground_execution_reconciliations("
                        "reconciliation_id,host_run_id,sdk_run_id,owner_id,generation,"
                        "observed_state,evidence_ref,evidence_hash,idempotency_key,"
                        "reconciliation_hash,reconciliation_json,recorded_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            receipt_id,
                            host_run_id,
                            sdk_run_id,
                            owner_id,
                            generation,
                            state_value,
                            evidence_ref,
                            evidence_hash,
                            idempotency_key,
                            receipt_hash,
                            canonical_json(payload),
                            now,
                        ),
                    )
                self._fault(f"{phase}.before_commit")
                row = await self._fetchone(
                    db,
                    f"SELECT * FROM {table} WHERE {id_column}=?",
                    (receipt_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault(f"{phase}.after_commit")
        assert row is not None
        return self._audit_receipt(
            row,
            phase=phase,
            id_column=id_column,
            hash_column=hash_column,
        )

    async def _verify_evidence_tx(
        self,
        db: aiosqlite.Connection,
        *,
        subject: str,
        primary_conversation_id: str,
        evidence_id: str,
        evidence_hash: str,
    ) -> None:
        row = await self._fetchone(
            db,
            "SELECT p.subject AS primary_subject,p.writable,e.subject AS evidence_subject,e.primary_conversation_id,e.envelope_sha256 FROM human_memory_primary_conversations p JOIN human_memory_evidence e ON e.primary_conversation_id=p.primary_conversation_id WHERE p.primary_conversation_id=? AND e.evidence_id=?",
            (primary_conversation_id, evidence_id),
        )
        if (
            row is None
            or row["primary_subject"] != subject
            or row["evidence_subject"] != subject
            or row["primary_conversation_id"] != primary_conversation_id
            or int(row["writable"]) != 1
            or row["envelope_sha256"] != evidence_hash
        ):
            raise ForegroundQueueError("foreground_evidence_authority_mismatch")

    async def _validate_lease_tx(
        self,
        db: aiosqlite.Connection,
        host_run_id: str,
        owner_id: str,
        generation: int,
        now: float,
    ) -> aiosqlite.Row:
        head = await self._head_tx(db, host_run_id)
        if await is_human_memory_work_item_parked_tx(
            db,
            worker_kind="foreground-lease",
            source_table="foreground_run_heads",
            primary_key="host_run_id",
            item_pk=host_run_id,
        ):
            raise ForegroundQueueError("foreground_lease_recovery_parked")
        if str(head["current_state"]) in TERMINAL_STATES:
            raise ForegroundQueueError("foreground_run_already_terminal")
        if head["owner_id"] != owner_id or int(head["generation"]) != generation:
            raise ForegroundQueueError("foreground_generation_stale")
        if float(head["lease_expires_at"]) <= now:
            raise ForegroundQueueError("foreground_lease_expired")
        return head

    async def _validate_sdk_binding_tx(
        self, db: aiosqlite.Connection, host_run_id: str, sdk_run_id: str
    ) -> aiosqlite.Row:
        binding = await self._fetchone(
            db,
            "SELECT * FROM foreground_run_sdk_bindings WHERE host_run_id=?",
            (host_run_id,),
        )
        if binding is None or binding["sdk_run_id"] != sdk_run_id:
            raise ForegroundQueueError("foreground_sdk_run_binding_mismatch")
        return binding

    async def _insert_turn_transition_tx(
        self,
        db: aiosqlite.Connection,
        *,
        turn_id: str,
        subject: str,
        from_state: str,
        to_state: str,
        host_run_id: str | None,
        recorded_at: float,
    ) -> tuple[str, str]:
        transition_id = _uuid(f"foreground-turn-transition:{turn_id}:{to_state}")
        payload = {
            "schema_version": 1,
            "transition_id": transition_id,
            "turn_id": turn_id,
            "subject": subject,
            "from_state": from_state,
            "to_state": to_state,
            "host_run_id": host_run_id,
            "recorded_at": recorded_at,
        }
        transition_hash = canonical_hash(payload)
        await db.execute(
            "INSERT INTO foreground_turn_transitions(transition_id,turn_id,subject,from_state,to_state,host_run_id,transition_hash,transition_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                transition_id,
                turn_id,
                subject,
                from_state,
                to_state,
                host_run_id,
                transition_hash,
                canonical_json(payload),
                recorded_at,
            ),
        )
        return transition_id, transition_hash

    async def _insert_run_transition_tx(
        self,
        db: aiosqlite.Connection,
        *,
        host_run_id: str,
        subject: str,
        from_state: str,
        to_state: str,
        generation: int,
        owner_id: str | None,
        sdk_event_id: str | None,
        idempotency_key: str,
        causal_evidence_ref: str | None,
        causal_evidence_hash: str | None,
        recorded_at: float,
        preparation_rejection: Mapping[str, object] | None = None,
    ) -> tuple[str, str]:
        transition_id = _uuid(f"foreground-run-transition:{host_run_id}:{idempotency_key}")
        payload = {
            "schema_version": 1,
            "transition_id": transition_id,
            "host_run_id": host_run_id,
            "subject": subject,
            "from_state": from_state,
            "to_state": to_state,
            "generation": generation,
            "owner_id": owner_id,
            "sdk_event_id": sdk_event_id,
            "idempotency_key": idempotency_key,
            "causal_evidence_ref": causal_evidence_ref,
            "causal_evidence_hash": causal_evidence_hash,
            "recorded_at": recorded_at,
        }
        if preparation_rejection is not None:
            payload["preparation_rejection"] = dict(preparation_rejection)
        transition_hash = canonical_hash(payload)
        await db.execute(
            "INSERT INTO foreground_run_transitions(transition_id,host_run_id,subject,from_state,to_state,generation,owner_id,sdk_event_id,idempotency_key,causal_evidence_ref,causal_evidence_hash,transition_hash,transition_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                transition_id,
                host_run_id,
                subject,
                from_state,
                to_state,
                generation,
                owner_id,
                sdk_event_id,
                idempotency_key,
                causal_evidence_ref,
                causal_evidence_hash,
                transition_hash,
                canonical_json(payload),
                recorded_at,
            ),
        )
        return transition_id, transition_hash

    async def _insert_lease_receipt_tx(
        self,
        db: aiosqlite.Connection,
        *,
        host_run_id: str,
        owner_id: str,
        generation: int,
        prior_generation: int | None,
        action: str,
        expires_at: float | None,
        idempotency_key: str,
        recorded_at: float,
    ) -> LeaseReceipt:
        lease_id = _uuid(f"foreground-lease:{host_run_id}:{idempotency_key}")
        payload = {
            "schema_version": 1,
            "lease_receipt_id": lease_id,
            "host_run_id": host_run_id,
            "owner_id": owner_id,
            "generation": generation,
            "prior_generation": prior_generation,
            "action": action,
            "expires_at": expires_at,
            "idempotency_key": idempotency_key,
            "recorded_at": recorded_at,
        }
        lease_hash = canonical_hash(payload)
        await db.execute(
            "INSERT INTO foreground_lease_receipts(lease_receipt_id,host_run_id,owner_id,generation,prior_generation,action,expires_at,idempotency_key,lease_hash,lease_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                lease_id,
                host_run_id,
                owner_id,
                generation,
                prior_generation,
                action,
                expires_at,
                idempotency_key,
                lease_hash,
                canonical_json(payload),
                recorded_at,
            ),
        )
        return LeaseReceipt(
            lease_id,
            host_run_id,
            owner_id,
            generation,
            prior_generation,
            action,
            expires_at,
            lease_hash,
        )

    async def _insert_signal_tx(
        self,
        db: aiosqlite.Connection,
        *,
        control_id: str,
        host_run_id: str,
        generation: int,
        control_kind: ControlKind,
        recorded_at: float,
    ) -> str:
        signal_id = _uuid(f"foreground-signal:{control_id}:{generation}")
        payload = {
            "schema_version": 1,
            "signal_id": signal_id,
            "control_id": control_id,
            "host_run_id": host_run_id,
            "generation": generation,
            "control_kind": control_kind.value,
            "created_at": recorded_at,
        }
        signal_hash = canonical_hash(payload)
        await db.execute(
            "INSERT INTO foreground_signal_outbox(signal_id,control_id,host_run_id,generation,control_kind,signal_hash,signal_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                signal_id,
                control_id,
                host_run_id,
                generation,
                control_kind.value,
                signal_hash,
                canonical_json(payload),
                recorded_at,
            ),
        )
        return signal_id

    async def _reissue_desired_control_tx(
        self,
        db: aiosqlite.Connection,
        old_head: aiosqlite.Row,
        generation: int,
        now: float,
    ) -> None:
        desired = old_head["desired_control"]
        if desired is None:
            return
        control = await self._fetchone(
            db,
            "SELECT control_id FROM foreground_control_intents WHERE host_run_id=? AND control_kind=? AND outcome='signalled' ORDER BY recorded_at DESC,control_id DESC LIMIT 1",
            (old_head["host_run_id"], desired),
        )
        if control is not None:
            await self._insert_signal_tx(
                db,
                control_id=str(control["control_id"]),
                host_run_id=str(old_head["host_run_id"]),
                generation=generation,
                control_kind=ControlKind(str(desired)),
                recorded_at=now,
            )

    async def _snapshot_tx(
        self, db: aiosqlite.Connection, host_run_id: str
    ) -> ForegroundRunSnapshot:
        row = await self._fetchone(
            db,
            "SELECT r.*,h.current_state,h.desired_control,h.sdk_run_id,h.owner_id,h.generation,h.lease_expires_at FROM foreground_runs r JOIN foreground_run_heads h ON h.host_run_id=r.host_run_id WHERE r.host_run_id=?",
            (host_run_id,),
        )
        if row is None:
            raise ForegroundQueueError("foreground_run_not_found")
        if row["owner_id"] is None or row["lease_expires_at"] is None:
            raise ForegroundQueueError("foreground_snapshot_not_active")
        payload = {
            "schema_version": 1,
            "host_run_id": row["host_run_id"],
            "sdk_run_id": row["sdk_run_id"],
            "subject": row["subject"],
            "primary_conversation_id": row["primary_conversation_id"],
            "turn_id": row["turn_id"],
            "task_scope_id": row["task_scope_id"],
            "binding_set_revision": int(row["binding_set_revision"]),
            "binding_set_receipt_id": row["binding_set_receipt_id"],
            "binding_set_receipt_hash": row["binding_set_receipt_hash"],
            "context_snapshot_id": row["context_snapshot_id"],
            "context_snapshot_revision": int(row["context_snapshot_revision"]),
            "context_snapshot_hash": row["context_snapshot_hash"],
            "state": row["current_state"],
            "desired_control": row["desired_control"],
            "owner_id": row["owner_id"],
            "generation": int(row["generation"]),
            "lease_expires_at": float(row["lease_expires_at"]),
            "lineage_hash": row["lineage_hash"],
        }
        return ForegroundRunSnapshot(
            host_run_id=str(row["host_run_id"]),
            sdk_run_id=str(row["sdk_run_id"]) if row["sdk_run_id"] is not None else None,
            subject=str(row["subject"]),
            primary_conversation_id=str(row["primary_conversation_id"]),
            turn_id=str(row["turn_id"]),
            task_scope_id=str(row["task_scope_id"]) if row["task_scope_id"] is not None else None,
            binding_set_revision=int(row["binding_set_revision"]),
            binding_set_receipt_id=str(row["binding_set_receipt_id"])
            if row["binding_set_receipt_id"] is not None
            else None,
            binding_set_receipt_hash=str(row["binding_set_receipt_hash"])
            if row["binding_set_receipt_hash"] is not None
            else None,
            context_snapshot_id=str(row["context_snapshot_id"]),
            context_snapshot_revision=int(row["context_snapshot_revision"]),
            context_snapshot_hash=str(row["context_snapshot_hash"]),
            state=RunState(str(row["current_state"])),
            desired_control=ControlKind(str(row["desired_control"]))
            if row["desired_control"] is not None
            else None,
            owner_id=str(row["owner_id"]),
            generation=int(row["generation"]),
            lease_expires_at=float(row["lease_expires_at"]),
            lineage_hash=str(row["lineage_hash"]),
            snapshot_hash=canonical_hash(payload),
        )

    async def _active_head_tx(self, db: aiosqlite.Connection, subject: str) -> aiosqlite.Row | None:
        return await self._fetchone(
            db,
            "SELECT * FROM foreground_run_heads WHERE subject=? AND current_state NOT IN ('COMPLETED','FAILED','STOPPED','CANCELLED')",
            (subject,),
        )

    async def _head_tx(self, db: aiosqlite.Connection, host_run_id: str) -> aiosqlite.Row:
        row = await self._fetchone(
            db, "SELECT * FROM foreground_run_heads WHERE host_run_id=?", (host_run_id,)
        )
        if row is None:
            raise ForegroundQueueError("foreground_run_not_found")
        return row

    async def _run_tx(self, db: aiosqlite.Connection, host_run_id: str) -> aiosqlite.Row:
        row = await self._fetchone(
            db, "SELECT * FROM foreground_runs WHERE host_run_id=?", (host_run_id,)
        )
        if row is None:
            raise ForegroundQueueError("foreground_run_not_found")
        return row

    async def _lease_by_key_tx(
        self, db: aiosqlite.Connection, host_run_id: str, key: str
    ) -> aiosqlite.Row | None:
        return await self._fetchone(
            db,
            "SELECT * FROM foreground_lease_receipts WHERE host_run_id=? AND idempotency_key=?",
            (host_run_id, key),
        )

    @staticmethod
    def _validate_context(context: ContextLineage) -> ContextLineage:
        if not isinstance(context, ContextLineage):
            raise ForegroundQueueError("foreground_context_lineage_invalid")
        identifier(context.context_snapshot_id, "context_snapshot_id", 512)
        if (
            isinstance(context.context_snapshot_revision, bool)
            or not isinstance(context.context_snapshot_revision, int)
            or context.context_snapshot_revision < 1
        ):
            raise ForegroundQueueError("foreground_context_revision_invalid")
        digest(context.context_snapshot_hash, "context_snapshot_hash")
        return context

    @staticmethod
    def _control_priority(kind: str | None) -> int:
        return {None: 0, "pause": 1, "stop": 2, "cancel": 3}[kind]

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    @staticmethod
    async def _fetchone(
        db: aiosqlite.Connection, sql: str, parameters: tuple[object, ...]
    ) -> aiosqlite.Row | None:
        cursor = await db.execute(sql, parameters)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    @asynccontextmanager
    async def _connection(self):
        async with human_memory_connection(self._db_path) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("PRAGMA busy_timeout=5000")
            yield connection

    @staticmethod
    def _enqueue_receipt(row: aiosqlite.Row) -> EnqueueReceipt:
        return EnqueueReceipt(
            str(row["turn_id"]),
            str(row["subject"]),
            str(row["primary_conversation_id"]),
            str(row["task_scope_id"]) if row["task_scope_id"] is not None else None,
            str(row["evidence_id"]),
            str(row["evidence_hash"]),
            int(row["enqueue_sequence"]),
            str(row["turn_hash"]),
        )

    @staticmethod
    def _admission_receipt(
        run: aiosqlite.Row, head: aiosqlite.Row, *, recovered: bool
    ) -> AdmissionReceipt:
        return AdmissionReceipt(
            str(run["admission_receipt_id"]),
            str(run["admission_receipt_hash"]),
            str(run["host_run_id"]),
            str(run["turn_id"]),
            str(run["subject"]),
            int(run["enqueue_sequence"]),
            RunState(str(head["current_state"])),
            str(head["owner_id"]),
            int(head["generation"]),
            float(head["lease_expires_at"]),
            str(run["lineage_hash"]),
            recovered,
        )

    @staticmethod
    def _binding_receipt(row: aiosqlite.Row) -> SdkRunBindingReceipt:
        return SdkRunBindingReceipt(
            str(row["binding_id"]),
            str(row["host_run_id"]),
            str(row["sdk_run_id"]),
            str(row["binding_hash"]),
        )

    @staticmethod
    def _lease_receipt(row: aiosqlite.Row) -> LeaseReceipt:
        return LeaseReceipt(
            str(row["lease_receipt_id"]),
            str(row["host_run_id"]),
            str(row["owner_id"]),
            int(row["generation"]),
            int(row["prior_generation"]) if row["prior_generation"] is not None else None,
            str(row["action"]),
            float(row["expires_at"]) if row["expires_at"] is not None else None,
            str(row["lease_hash"]),
        )

    async def _control_receipt_from_row(
        self, db: aiosqlite.Connection, row: aiosqlite.Row
    ) -> ControlReceipt:
        signal = await self._fetchone(
            db,
            "SELECT signal_id FROM foreground_signal_outbox WHERE control_id=? AND generation=?",
            (row["control_id"], row["requested_generation"]),
        )
        return ControlReceipt(
            str(row["control_id"]),
            str(row["host_run_id"]),
            ControlKind(str(row["control_kind"])),
            str(row["outcome"]),
            RunState(str(row["reduced_state"])),
            int(row["requested_generation"]),
            str(row["control_hash"]),
            str(signal["signal_id"]) if signal is not None else None,
        )

    @staticmethod
    def _signal(row: aiosqlite.Row) -> SignalEnvelope:
        return SignalEnvelope(
            str(row["signal_id"]),
            str(row["control_id"]),
            str(row["host_run_id"]),
            str(row["sdk_run_id"]) if row["sdk_run_id"] is not None else None,
            int(row["generation"]),
            ControlKind(str(row["control_kind"])),
            str(row["signal_hash"]),
        )

    @staticmethod
    def _signal_ack(row: aiosqlite.Row) -> SignalAckReceipt:
        return SignalAckReceipt(
            str(row["ack_id"]),
            str(row["signal_id"]),
            str(row["host_run_id"]),
            str(row["sdk_run_id"]),
            int(row["generation"]),
            str(row["sdk_signal_id"]),
            str(row["ack_hash"]),
        )

    @staticmethod
    def _terminal_receipt(row: aiosqlite.Row) -> TerminalReceipt:
        return TerminalReceipt(
            str(row["terminal_receipt_id"]),
            str(row["host_run_id"]),
            str(row["sdk_run_id"]),
            RunState(str(row["terminal_state"])),
            int(row["generation"]),
            str(row["sdk_event_id"]),
            str(row["sdk_event_hash"]),
            str(row["receipt_hash"]),
        )
