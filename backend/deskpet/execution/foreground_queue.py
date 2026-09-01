# SPDX-License-Identifier: BUSL-1.1

"""Durable Host authority for one foreground Run and ordinary-turn FIFO.

This module intentionally does not execute an Agent loop.  It owns durable
admission, FIFO ordering, controls and lease generations, while the published
Harness SDK Runtime remains the sole owner of actual Agent execution.  A Host
Run can bind exactly one SDK Run and every lifecycle write is fenced by the
current lease generation.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import aiosqlite

from deskpet.memory.recovery_work_items import is_human_memory_work_item_parked_tx
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import (
    canonical_hash,
    canonical_json,
    digest,
    identifier,
    reject_private_payload,
)

TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "STOPPED", "CANCELLED"})
FOREGROUND_SCHEDULER_KIND = "foreground_scheduler"


class TurnState(StrEnum):
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    SETTLED = "SETTLED"


class RunState(StrEnum):
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    STOP_REQUESTED = "STOP_REQUESTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


class ControlKind(StrEnum):
    PAUSE = "pause"
    STOP = "stop"
    CANCEL = "cancel"


class ForegroundQueueError(RuntimeError):
    code = "foreground_queue_rejected"

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ContextLineage:
    context_snapshot_id: str
    context_snapshot_revision: int
    context_snapshot_hash: str


@dataclass(frozen=True, slots=True)
class EnqueueReceipt:
    turn_id: str
    subject: str
    primary_conversation_id: str
    task_scope_id: str | None
    evidence_id: str
    evidence_hash: str
    enqueue_sequence: int
    turn_hash: str


@dataclass(frozen=True, slots=True)
class AdmissionReceipt:
    admission_receipt_id: str
    admission_receipt_hash: str
    host_run_id: str
    turn_id: str
    subject: str
    enqueue_sequence: int
    state: RunState
    owner_id: str
    generation: int
    lease_expires_at: float
    lineage_hash: str
    recovered: bool


@dataclass(frozen=True, slots=True)
class SdkRunBindingReceipt:
    binding_id: str
    host_run_id: str
    sdk_run_id: str
    binding_hash: str


@dataclass(frozen=True, slots=True)
class LeaseReceipt:
    lease_receipt_id: str
    host_run_id: str
    owner_id: str
    generation: int
    prior_generation: int | None
    action: str
    expires_at: float | None
    lease_hash: str


@dataclass(frozen=True, slots=True)
class ControlReceipt:
    control_id: str
    host_run_id: str
    control_kind: ControlKind
    outcome: str
    reduced_state: RunState
    generation: int
    control_hash: str
    signal_id: str | None


@dataclass(frozen=True, slots=True)
class SignalEnvelope:
    signal_id: str
    control_id: str
    host_run_id: str
    sdk_run_id: str | None
    generation: int
    control_kind: ControlKind
    signal_hash: str


@dataclass(frozen=True, slots=True)
class SignalAckReceipt:
    ack_id: str
    signal_id: str
    host_run_id: str
    sdk_run_id: str
    generation: int
    sdk_signal_id: str
    ack_hash: str


@dataclass(frozen=True, slots=True)
class TerminalReceipt:
    terminal_receipt_id: str
    host_run_id: str
    sdk_run_id: str
    terminal_state: RunState
    generation: int
    sdk_event_id: str
    sdk_event_hash: str
    receipt_hash: str


@dataclass(frozen=True, slots=True)
class ForegroundRunSnapshot:
    host_run_id: str
    sdk_run_id: str | None
    subject: str
    primary_conversation_id: str
    turn_id: str
    task_scope_id: str | None
    binding_set_revision: int
    binding_set_receipt_id: str | None
    binding_set_receipt_hash: str | None
    context_snapshot_id: str
    context_snapshot_revision: int
    context_snapshot_hash: str
    state: RunState
    desired_control: ControlKind | None
    owner_id: str
    generation: int
    lease_expires_at: float
    lineage_hash: str
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class EffectAdmissionReceipt:
    host_run_id: str
    sdk_run_id: str
    generation: int
    snapshot_hash: str


def _uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))


def _clock_value(clock: Callable[[], float]) -> float:
    value = float(clock())
    if not math.isfinite(value) or value < 0:
        raise ForegroundQueueError("foreground_clock_invalid")
    return value


def _duration(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForegroundQueueError("foreground_lease_duration_invalid")
    duration = float(value)
    if not math.isfinite(duration) or duration <= 0 or duration > 86400:
        raise ForegroundQueueError("foreground_lease_duration_invalid")
    return duration


class ForegroundQueueStore:
    """SQLite-backed reducer for the frozen S4 foreground coordination AC."""

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
        if row is None or row["format_epoch"] != "human-memory-v1" or int(row["schema_version"]) != 1:
            raise ForegroundQueueError("foreground_queue_schema_uninitialized")

    async def enqueue_turn(
        self,
        *,
        subject: str,
        primary_conversation_id: str,
        evidence_id: str,
        evidence_hash: str,
        idempotency_key: str,
        turn_payload: Mapping[str, object],
        task_scope_id: str | None = None,
    ) -> EnqueueReceipt:
        subject = identifier(subject, "subject", 512)
        primary_conversation_id = identifier(primary_conversation_id, "primary_conversation_id", 512)
        evidence_id = identifier(evidence_id, "evidence_id", 512)
        evidence_hash = digest(evidence_hash, "evidence_hash")
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        if task_scope_id is not None:
            task_scope_id = identifier(task_scope_id, "task_scope_id", 512)
        if not isinstance(turn_payload, Mapping):
            raise ForegroundQueueError("foreground_turn_payload_invalid")
        payload = dict(turn_payload)
        reject_private_payload(payload, "foreground_turn")
        request = {
            "schema_version": 1,
            "subject": subject,
            "primary_conversation_id": primary_conversation_id,
            "task_scope_id": task_scope_id,
            "evidence_id": evidence_id,
            "evidence_hash": evidence_hash,
            "idempotency_key": idempotency_key,
            "payload": payload,
        }
        turn_hash = canonical_hash(request)
        turn_id = _uuid(f"foreground-turn:{subject}:{idempotency_key}")
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_turns WHERE subject=? AND idempotency_key=?",
                    (subject, idempotency_key),
                )
                if existing is not None:
                    if existing["turn_hash"] != turn_hash:
                        raise ForegroundQueueError("foreground_turn_idempotency_conflict")
                    await db.commit()
                    return self._enqueue_receipt(existing)
                await self._verify_evidence_tx(
                    db,
                    subject=subject,
                    primary_conversation_id=primary_conversation_id,
                    evidence_id=evidence_id,
                    evidence_hash=evidence_hash,
                )
                if task_scope_id is not None:
                    scope = await self._fetchone(
                        db,
                        "SELECT subject FROM task_scopes WHERE task_scope_id=?",
                        (task_scope_id,),
                    )
                    if scope is None or scope["subject"] != subject:
                        raise ForegroundQueueError("foreground_scope_authority_mismatch")
                sequence_row = await self._fetchone(
                    db,
                    "SELECT COALESCE(MAX(enqueue_sequence),0)+1 AS next_sequence FROM foreground_turns WHERE subject=?",
                    (subject,),
                )
                assert sequence_row is not None
                sequence = int(sequence_row["next_sequence"])
                await db.execute(
                    "INSERT INTO foreground_turns(turn_id,subject,primary_conversation_id,task_scope_id,evidence_id,evidence_hash,idempotency_key,enqueue_sequence,turn_hash,turn_json,enqueued_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        turn_id,
                        subject,
                        primary_conversation_id,
                        task_scope_id,
                        evidence_id,
                        evidence_hash,
                        idempotency_key,
                        sequence,
                        turn_hash,
                        canonical_json(request),
                        now,
                    ),
                )
                transition_id, transition_hash = await self._insert_turn_transition_tx(
                    db,
                    turn_id=turn_id,
                    subject=subject,
                    from_state="ABSENT",
                    to_state=TurnState.QUEUED.value,
                    host_run_id=None,
                    recorded_at=now,
                )
                await db.execute(
                    "INSERT INTO foreground_turn_heads(turn_id,subject,current_state,host_run_id,last_transition_id,last_transition_hash,updated_at) VALUES (?,?,?,NULL,?,?,?)",
                    (turn_id, subject, TurnState.QUEUED.value, transition_id, transition_hash, now),
                )
                self._fault("enqueue.before_commit")
                row = await self._fetchone(db, "SELECT * FROM foreground_turns WHERE turn_id=?", (turn_id,))
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("enqueue.after_commit")
        assert row is not None
        return self._enqueue_receipt(row)

    async def claim_next(
        self,
        *,
        subject: str,
        owner_id: str,
        claim_idempotency_key: str,
        context: ContextLineage,
        lease_seconds: float,
        worker_kind: str = FOREGROUND_SCHEDULER_KIND,
    ) -> AdmissionReceipt | None:
        if worker_kind != FOREGROUND_SCHEDULER_KIND:
            raise ForegroundQueueError("foreground_background_claim_rejected")
        subject = identifier(subject, "subject", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        claim_idempotency_key = identifier(claim_idempotency_key, "claim_idempotency_key", 512)
        context = self._validate_context(context)
        duration = _duration(lease_seconds)
        now = _clock_value(self._clock)
        expires_at = now + duration
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                active = await self._active_head_tx(db, subject)
                if active is not None:
                    run = await self._run_tx(db, str(active["host_run_id"]))
                    if (
                        run["claim_idempotency_key"] == claim_idempotency_key
                        and active["owner_id"] == owner_id
                    ):
                        await db.commit()
                        return self._admission_receipt(run, active, recovered=True)
                    raise ForegroundQueueError("foreground_run_already_active")
                turn = await self._fetchone(
                    db,
                    "SELECT t.* FROM foreground_turns t JOIN foreground_turn_heads h ON h.turn_id=t.turn_id WHERE t.subject=? AND h.current_state='QUEUED' ORDER BY t.enqueue_sequence,t.turn_id LIMIT 1",
                    (subject,),
                )
                if turn is None:
                    await db.commit()
                    return None
                binding_revision = 0
                binding_receipt_id: str | None = None
                binding_receipt_hash: str | None = None
                if turn["task_scope_id"] is not None:
                    binding = await self._fetchone(
                        db,
                        "SELECT current_revision,current_receipt_id,current_receipt_hash FROM task_workspace_binding_heads WHERE task_scope_id=? AND subject=?",
                        (turn["task_scope_id"], subject),
                    )
                    if binding is not None:
                        binding_revision = int(binding["current_revision"])
                        binding_receipt_id = str(binding["current_receipt_id"])
                        binding_receipt_hash = str(binding["current_receipt_hash"])
                host_run_id = _uuid(f"foreground-run:{turn['turn_id']}")
                lineage = {
                    "task_scope_id": turn["task_scope_id"],
                    "binding_set_revision": binding_revision,
                    "binding_set_receipt_id": binding_receipt_id,
                    "binding_set_receipt_hash": binding_receipt_hash,
                    "context_snapshot_id": context.context_snapshot_id,
                    "context_snapshot_revision": context.context_snapshot_revision,
                    "context_snapshot_hash": context.context_snapshot_hash,
                }
                lineage_hash = canonical_hash(lineage)
                admission_id = _uuid(f"foreground-admission:{host_run_id}")
                admission = {
                    "schema_version": 1,
                    "admission_receipt_id": admission_id,
                    "host_run_id": host_run_id,
                    "turn_id": turn["turn_id"],
                    "subject": subject,
                    "primary_conversation_id": turn["primary_conversation_id"],
                    "claim_idempotency_key": claim_idempotency_key,
                    "lineage": lineage,
                    "lineage_hash": lineage_hash,
                    "owner_id": owner_id,
                    "generation": 1,
                    "lease_expires_at": expires_at,
                    "admitted_at": now,
                }
                admission_hash = canonical_hash(admission)
                await db.execute(
                    "INSERT INTO foreground_runs(host_run_id,subject,primary_conversation_id,turn_id,enqueue_sequence,task_scope_id,binding_set_revision,binding_set_receipt_id,binding_set_receipt_hash,context_snapshot_id,context_snapshot_revision,context_snapshot_hash,lineage_hash,claim_idempotency_key,admission_receipt_id,admission_receipt_hash,admission_json,admitted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        host_run_id,
                        subject,
                        turn["primary_conversation_id"],
                        turn["turn_id"],
                        turn["enqueue_sequence"],
                        turn["task_scope_id"],
                        binding_revision,
                        binding_receipt_id,
                        binding_receipt_hash,
                        context.context_snapshot_id,
                        context.context_snapshot_revision,
                        context.context_snapshot_hash,
                        lineage_hash,
                        claim_idempotency_key,
                        admission_id,
                        admission_hash,
                        canonical_json(admission),
                        now,
                    ),
                )
                run_transition_id, run_transition_hash = await self._insert_run_transition_tx(
                    db,
                    host_run_id=host_run_id,
                    subject=subject,
                    from_state="ABSENT",
                    to_state=RunState.CLAIMED.value,
                    generation=1,
                    owner_id=owner_id,
                    sdk_event_id=None,
                    idempotency_key=f"admission:{claim_idempotency_key}",
                    causal_evidence_ref=None,
                    causal_evidence_hash=None,
                    recorded_at=now,
                )
                await db.execute(
                    "INSERT INTO foreground_run_heads(host_run_id,subject,primary_conversation_id,turn_id,current_state,desired_control,sdk_run_id,owner_id,generation,lease_expires_at,last_transition_id,last_transition_hash,updated_at) VALUES (?,?,?,?,?,NULL,NULL,?,1,?,?,?,?)",
                    (
                        host_run_id,
                        subject,
                        turn["primary_conversation_id"],
                        turn["turn_id"],
                        RunState.CLAIMED.value,
                        owner_id,
                        expires_at,
                        run_transition_id,
                        run_transition_hash,
                        now,
                    ),
                )
                turn_transition_id, turn_transition_hash = await self._insert_turn_transition_tx(
                    db,
                    turn_id=str(turn["turn_id"]),
                    subject=subject,
                    from_state=TurnState.QUEUED.value,
                    to_state=TurnState.CLAIMED.value,
                    host_run_id=host_run_id,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_turn_heads SET current_state='CLAIMED',host_run_id=?,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE turn_id=? AND current_state='QUEUED'",
                    (host_run_id, turn_transition_id, turn_transition_hash, now, turn["turn_id"]),
                )
                await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=1,
                    prior_generation=None,
                    action="acquire",
                    expires_at=expires_at,
                    idempotency_key=f"lease-acquire:{claim_idempotency_key}",
                    recorded_at=now,
                )
                self._fault("claim.before_commit")
                head = await self._head_tx(db, host_run_id)
                run = await self._run_tx(db, host_run_id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("claim.after_commit")
        return self._admission_receipt(run, head, recovered=False)

    async def bind_sdk_run(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        idempotency_key: str,
    ) -> SdkRunBindingReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_run_sdk_bindings WHERE host_run_id=?",
                    (host_run_id,),
                )
                if existing is not None:
                    if existing["sdk_run_id"] != sdk_run_id:
                        raise ForegroundQueueError("foreground_sdk_run_binding_conflict")
                    await db.commit()
                    return self._binding_receipt(existing)
                sdk_owner = await self._fetchone(
                    db,
                    "SELECT host_run_id FROM foreground_run_sdk_bindings WHERE sdk_run_id=?",
                    (sdk_run_id,),
                )
                if sdk_owner is not None:
                    raise ForegroundQueueError("foreground_sdk_run_binding_conflict")
                head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                if head["sdk_run_id"] is not None and head["sdk_run_id"] != sdk_run_id:
                    raise ForegroundQueueError("foreground_sdk_run_binding_conflict")
                payload = {
                    "schema_version": 1,
                    "host_run_id": host_run_id,
                    "sdk_run_id": sdk_run_id,
                    "idempotency_key": idempotency_key,
                    "bound_at": now,
                }
                binding_hash = canonical_hash(payload)
                binding_id = _uuid(f"foreground-sdk-binding:{host_run_id}")
                await db.execute(
                    "INSERT INTO foreground_run_sdk_bindings(binding_id,host_run_id,sdk_run_id,idempotency_key,binding_hash,binding_json,bound_at) VALUES (?,?,?,?,?,?,?)",
                    (binding_id, host_run_id, sdk_run_id, idempotency_key, binding_hash, canonical_json(payload), now),
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET sdk_run_id=?,updated_at=? WHERE host_run_id=?",
                    (sdk_run_id, now, host_run_id),
                )
                row = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_run_sdk_bindings WHERE host_run_id=?",
                    (host_run_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        assert row is not None
        return self._binding_receipt(row)

    async def record_sdk_started(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        sdk_event_id: str,
        idempotency_key: str,
    ) -> ForegroundRunSnapshot:
        return await self._record_sdk_state(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=owner_id,
            generation=generation,
            sdk_event_id=sdk_event_id,
            idempotency_key=idempotency_key,
            allowed_from={RunState.CLAIMED.value},
            to_state=RunState.RUNNING,
            clear_pause=False,
        )

    async def record_pause_outcome(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        sdk_event_id: str,
        paused: bool,
        idempotency_key: str,
    ) -> ForegroundRunSnapshot:
        return await self._record_sdk_state(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            owner_id=owner_id,
            generation=generation,
            sdk_event_id=sdk_event_id,
            idempotency_key=idempotency_key,
            allowed_from={RunState.PAUSE_REQUESTED.value},
            to_state=RunState.PAUSED if paused else RunState.RUNNING,
            clear_pause=not paused,
        )

    async def request_control(
        self,
        *,
        host_run_id: str,
        subject: str,
        generation: int,
        control_kind: ControlKind | str,
        reason: str,
        idempotency_key: str,
    ) -> ControlReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        subject = identifier(subject, "subject", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        reason = identifier(reason, "control_reason", 2048)
        try:
            kind = ControlKind(control_kind)
        except ValueError as exc:
            raise ForegroundQueueError("foreground_control_kind_invalid") from exc
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ForegroundQueueError("foreground_generation_invalid")
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_control_intents WHERE host_run_id=? AND idempotency_key=?",
                    (host_run_id, idempotency_key),
                )
                if existing is not None:
                    if (
                        existing["subject"] != subject
                        or existing["control_kind"] != kind.value
                        or existing["reason"] != reason
                        or int(existing["requested_generation"]) != generation
                    ):
                        raise ForegroundQueueError("foreground_control_idempotency_conflict")
                    await db.commit()
                    return await self._control_receipt_from_row(db, existing)
                head = await self._head_tx(db, host_run_id)
                if head["subject"] != subject:
                    raise ForegroundQueueError("foreground_run_subject_mismatch")
                if int(head["generation"]) != generation:
                    raise ForegroundQueueError("foreground_generation_stale")
                current_state = str(head["current_state"])
                signal_id: str | None = None
                if current_state in TERMINAL_STATES:
                    outcome = "already_terminal"
                    reduced_state = current_state
                else:
                    current_kind = str(head["desired_control"]) if head["desired_control"] is not None else None
                    current_priority = self._control_priority(current_kind)
                    requested_priority = self._control_priority(kind.value)
                    if requested_priority < current_priority:
                        outcome = "superseded"
                        reduced_state = current_state
                    elif requested_priority == current_priority and current_kind is not None:
                        outcome = "already_requested"
                        reduced_state = current_state
                    else:
                        outcome = "signalled"
                        reduced_state = {
                            ControlKind.PAUSE: RunState.PAUSE_REQUESTED.value,
                            ControlKind.STOP: RunState.STOP_REQUESTED.value,
                            ControlKind.CANCEL: RunState.CANCEL_REQUESTED.value,
                        }[kind]
                control_id = _uuid(f"foreground-control:{host_run_id}:{idempotency_key}")
                control_payload = {
                    "schema_version": 1,
                    "control_id": control_id,
                    "host_run_id": host_run_id,
                    "subject": subject,
                    "control_kind": kind.value,
                    "reason": reason,
                    "requested_generation": generation,
                    "outcome": outcome,
                    "reduced_state": reduced_state,
                    "idempotency_key": idempotency_key,
                    "recorded_at": now,
                }
                control_hash = canonical_hash(control_payload)
                if outcome == "signalled":
                    transition_id, transition_hash = await self._insert_run_transition_tx(
                        db,
                        host_run_id=host_run_id,
                        subject=subject,
                        from_state=current_state,
                        to_state=reduced_state,
                        generation=generation,
                        owner_id=str(head["owner_id"]),
                        sdk_event_id=None,
                        idempotency_key=f"control-transition:{control_id}",
                        causal_evidence_ref=None,
                        causal_evidence_hash=None,
                        recorded_at=now,
                    )
                    await db.execute(
                        "UPDATE foreground_run_heads SET current_state=?,desired_control=?,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                        (reduced_state, kind.value, transition_id, transition_hash, now, host_run_id),
                    )
                await db.execute(
                    "INSERT INTO foreground_control_intents(control_id,host_run_id,subject,control_kind,reason,requested_generation,outcome,reduced_state,idempotency_key,control_hash,control_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        control_id,
                        host_run_id,
                        subject,
                        kind.value,
                        reason,
                        generation,
                        outcome,
                        reduced_state,
                        idempotency_key,
                        control_hash,
                        canonical_json(control_payload),
                        now,
                    ),
                )
                if outcome == "signalled":
                    signal_id = await self._insert_signal_tx(
                        db,
                        control_id=control_id,
                        host_run_id=host_run_id,
                        generation=generation,
                        control_kind=kind,
                        recorded_at=now,
                    )
                self._fault("control.before_commit")
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("control.after_commit")
        return ControlReceipt(
            control_id,
            host_run_id,
            kind,
            outcome,
            RunState(reduced_state),
            generation,
            control_hash,
            signal_id,
        )

    async def pending_signals(self, host_run_id: str) -> tuple[SignalEnvelope, ...]:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        await self.initialize()
        async with self._connection() as db:
            head = await self._head_tx(db, host_run_id)
            if (
                str(head["current_state"]) in TERMINAL_STATES
                or head["desired_control"] is None
            ):
                return ()
            cursor = await db.execute(
                "SELECT s.*,b.sdk_run_id FROM foreground_signal_outbox s "
                "LEFT JOIN foreground_signal_acks a ON a.signal_id=s.signal_id "
                "LEFT JOIN foreground_run_sdk_bindings b ON b.host_run_id=s.host_run_id "
                "WHERE s.host_run_id=? AND s.generation=? AND s.control_kind=? "
                "AND a.signal_id IS NULL ORDER BY s.created_at,s.signal_id",
                (
                    host_run_id,
                    int(head["generation"]),
                    str(head["desired_control"]),
                ),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            visible_rows = []
            for row in rows:
                if not await is_human_memory_work_item_parked_tx(
                    db,
                    worker_kind="foreground-signal",
                    source_table="foreground_signal_outbox",
                    primary_key="signal_id",
                    item_pk=str(row["signal_id"]),
                ):
                    visible_rows.append(row)
        return tuple(self._signal(row) for row in visible_rows)

    async def acknowledge_signal(
        self,
        *,
        signal_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        sdk_signal_id: str,
    ) -> SignalAckReceipt:
        signal_id = identifier(signal_id, "signal_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        sdk_signal_id = identifier(sdk_signal_id, "sdk_signal_id", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                signal = await self._fetchone(db, "SELECT * FROM foreground_signal_outbox WHERE signal_id=?", (signal_id,))
                if signal is None:
                    raise ForegroundQueueError("foreground_signal_not_found")
                head = await self._head_tx(db, str(signal["host_run_id"]))
                if (
                    int(signal["generation"]) != generation
                    or int(head["generation"]) != generation
                ):
                    raise ForegroundQueueError("foreground_generation_stale")
                if (
                    str(head["current_state"]) in TERMINAL_STATES
                    or head["desired_control"] != signal["control_kind"]
                ):
                    raise ForegroundQueueError("foreground_signal_superseded")
                existing = await self._fetchone(db, "SELECT * FROM foreground_signal_acks WHERE signal_id=?", (signal_id,))
                if existing is not None:
                    if existing["sdk_signal_id"] != sdk_signal_id or existing["sdk_run_id"] != sdk_run_id:
                        raise ForegroundQueueError("foreground_signal_ack_conflict")
                    await db.commit()
                    return self._signal_ack(existing)
                await self._validate_lease_tx(db, str(signal["host_run_id"]), owner_id, generation, now)
                await self._validate_sdk_binding_tx(db, str(signal["host_run_id"]), sdk_run_id)
                payload = {
                    "schema_version": 1,
                    "signal_id": signal_id,
                    "host_run_id": signal["host_run_id"],
                    "sdk_run_id": sdk_run_id,
                    "generation": generation,
                    "sdk_signal_id": sdk_signal_id,
                    "acknowledged_at": now,
                }
                ack_hash = canonical_hash(payload)
                ack_id = _uuid(f"foreground-signal-ack:{signal_id}")
                await db.execute(
                    "INSERT INTO foreground_signal_acks(ack_id,signal_id,host_run_id,sdk_run_id,generation,sdk_signal_id,ack_hash,ack_json,acknowledged_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ack_id, signal_id, signal["host_run_id"], sdk_run_id, generation, sdk_signal_id, ack_hash, canonical_json(payload), now),
                )
                row = await self._fetchone(db, "SELECT * FROM foreground_signal_acks WHERE ack_id=?", (ack_id,))
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        assert row is not None
        return self._signal_ack(row)

    async def heartbeat(
        self,
        *,
        host_run_id: str,
        owner_id: str,
        generation: int,
        lease_seconds: float,
        idempotency_key: str,
    ) -> LeaseReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        duration = _duration(lease_seconds)
        now = _clock_value(self._clock)
        expires_at = now + duration
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._lease_by_key_tx(db, host_run_id, idempotency_key)
                if existing is not None:
                    if (
                        existing["owner_id"] != owner_id
                        or int(existing["generation"]) != generation
                        or existing["action"] != "heartbeat"
                    ):
                        raise ForegroundQueueError("foreground_lease_idempotency_conflict")
                    await db.commit()
                    return self._lease_receipt(existing)
                await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                receipt = await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=generation,
                    prior_generation=generation,
                    action="heartbeat",
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET lease_expires_at=?,updated_at=? WHERE host_run_id=?",
                    (expires_at, now, host_run_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return receipt

    async def reclaim_expired(
        self,
        *,
        host_run_id: str,
        new_owner_id: str,
        expected_generation: int,
        lease_seconds: float,
        idempotency_key: str,
    ) -> LeaseReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        new_owner_id = identifier(new_owner_id, "new_owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        duration = _duration(lease_seconds)
        now = _clock_value(self._clock)
        expires_at = now + duration
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._lease_by_key_tx(db, host_run_id, idempotency_key)
                if existing is not None:
                    if (
                        existing["owner_id"] != new_owner_id
                        or int(existing["generation"]) != expected_generation + 1
                        or int(existing["prior_generation"]) != expected_generation
                        or existing["action"] != "reclaim"
                    ):
                        raise ForegroundQueueError("foreground_lease_idempotency_conflict")
                    await db.commit()
                    return self._lease_receipt(existing)
                head = await self._head_tx(db, host_run_id)
                if str(head["current_state"]) in TERMINAL_STATES:
                    raise ForegroundQueueError("foreground_run_already_terminal")
                if int(head["generation"]) != expected_generation:
                    raise ForegroundQueueError("foreground_generation_stale")
                if float(head["lease_expires_at"]) > now:
                    raise ForegroundQueueError("foreground_lease_not_expired")
                new_generation = expected_generation + 1
                receipt = await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=new_owner_id,
                    generation=new_generation,
                    prior_generation=expected_generation,
                    action="reclaim",
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET owner_id=?,generation=?,lease_expires_at=?,updated_at=? WHERE host_run_id=?",
                    (new_owner_id, new_generation, expires_at, now, host_run_id),
                )
                await self._reissue_desired_control_tx(db, head, new_generation, now)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return receipt

    async def resume_paused(
        self,
        *,
        host_run_id: str,
        owner_id: str,
        expected_generation: int,
        lease_seconds: float,
        idempotency_key: str,
    ) -> ForegroundRunSnapshot:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        duration = _duration(lease_seconds)
        now = _clock_value(self._clock)
        expires_at = now + duration
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._lease_by_key_tx(db, host_run_id, idempotency_key)
                if existing is not None:
                    if (
                        existing["owner_id"] != owner_id
                        or int(existing["generation"]) != expected_generation + 1
                        or int(existing["prior_generation"]) != expected_generation
                        or existing["action"] != "resume"
                    ):
                        raise ForegroundQueueError("foreground_lease_idempotency_conflict")
                    snapshot = await self._snapshot_tx(db, host_run_id)
                    await db.commit()
                    return snapshot
                head = await self._head_tx(db, host_run_id)
                if head["current_state"] != RunState.PAUSED.value:
                    raise ForegroundQueueError("foreground_resume_state_invalid")
                if head["desired_control"] not in (None, ControlKind.PAUSE.value):
                    raise ForegroundQueueError("foreground_resume_strong_control_pending")
                if int(head["generation"]) != expected_generation:
                    raise ForegroundQueueError("foreground_generation_stale")
                if head["sdk_run_id"] is None:
                    raise ForegroundQueueError("foreground_sdk_run_binding_missing")
                new_generation = expected_generation + 1
                transition_id, transition_hash = await self._insert_run_transition_tx(
                    db,
                    host_run_id=host_run_id,
                    subject=str(head["subject"]),
                    from_state=RunState.PAUSED.value,
                    to_state=RunState.RUNNING.value,
                    generation=new_generation,
                    owner_id=owner_id,
                    sdk_event_id=None,
                    idempotency_key=f"resume-transition:{idempotency_key}",
                    causal_evidence_ref=None,
                    causal_evidence_hash=None,
                    recorded_at=now,
                )
                await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=new_generation,
                    prior_generation=expected_generation,
                    action="resume",
                    expires_at=expires_at,
                    idempotency_key=idempotency_key,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET current_state='RUNNING',desired_control=NULL,owner_id=?,generation=?,lease_expires_at=?,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                    (owner_id, new_generation, expires_at, transition_id, transition_hash, now, host_run_id),
                )
                snapshot = await self._snapshot_tx(db, host_run_id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return snapshot

    async def record_sdk_terminal(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
        terminal_state: RunState | str,
        sdk_event_id: str,
        sdk_event_hash: str,
        idempotency_key: str,
    ) -> TerminalReceipt:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        sdk_event_id = identifier(sdk_event_id, "sdk_event_id", 512)
        sdk_event_hash = digest(sdk_event_hash, "sdk_event_hash")
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ForegroundQueueError("foreground_generation_invalid")
        try:
            terminal = RunState(terminal_state)
        except ValueError as exc:
            raise ForegroundQueueError("foreground_terminal_state_invalid") from exc
        if terminal.value not in TERMINAL_STATES:
            raise ForegroundQueueError("foreground_terminal_state_invalid")
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_terminal_receipts WHERE host_run_id=?",
                    (host_run_id,),
                )
                if existing is not None:
                    if (
                        existing["sdk_run_id"] != sdk_run_id
                        or existing["terminal_state"] != terminal.value
                        or int(existing["generation"]) != generation
                        or existing["sdk_event_id"] != sdk_event_id
                        or existing["sdk_event_hash"] != sdk_event_hash
                    ):
                        raise ForegroundQueueError("foreground_terminal_immutable")
                    await db.commit()
                    return self._terminal_receipt(existing)
                head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                await self._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
                run = await self._run_tx(db, host_run_id)
                receipt = await self._fetchone(
                    db,
                    "SELECT r.task_scope_id,r.source_sequence,r.event_id,e.payload_json "
                    "FROM task_scope_execution_ingest_receipts r "
                    "JOIN task_scope_events e ON e.event_id=r.event_id "
                    "AND e.task_scope_id=r.task_scope_id AND e.payload_hash=r.evidence_hash "
                    "WHERE r.source_event_id=? AND r.run_id=? AND r.evidence_hash=? "
                    "AND r.evidence_kind='run_terminal'",
                    (sdk_event_id, sdk_run_id, sdk_event_hash),
                )
                if receipt is None:
                    raise ForegroundQueueError("foreground_terminal_sdk_evidence_missing")
                if (
                    run["task_scope_id"] is None
                    or receipt["task_scope_id"] != run["task_scope_id"]
                ):
                    raise ForegroundQueueError("foreground_terminal_scope_mismatch")
                gate = await self._fetchone(
                    db,
                    "SELECT g.gate_receipt_id,g.terminal_source_sequence,"
                    "g.durable_source_sequence,w.durable_source_sequence AS current_durable_sequence,"
                    "w.terminal_source_sequence AS current_terminal_sequence "
                    "FROM task_scope_terminal_gate_receipts g "
                    "JOIN task_scope_run_watermarks w ON w.run_id=g.run_id "
                    "AND w.task_scope_id=g.task_scope_id "
                    "WHERE g.run_id=? AND g.task_scope_id=? "
                    "AND g.terminal_source_sequence=? "
                    "AND g.durable_source_sequence>=g.terminal_source_sequence "
                    "AND w.terminal_source_sequence=g.terminal_source_sequence "
                    "AND w.durable_source_sequence>=g.terminal_source_sequence",
                    (sdk_run_id, receipt["task_scope_id"], receipt["source_sequence"]),
                )
                if gate is None:
                    raise ForegroundQueueError("foreground_terminal_gate_pending")
                try:
                    evidence = json.loads(str(receipt["payload_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ForegroundQueueError(
                        "foreground_terminal_sdk_evidence_invalid"
                    ) from exc
                if (
                    not isinstance(evidence, dict)
                    or evidence.get("event_id") != sdk_event_id
                    or evidence.get("run_id") != sdk_run_id
                    or evidence.get("subject") != head["subject"]
                    or evidence.get("kind") != "run_terminal"
                ):
                    raise ForegroundQueueError("foreground_terminal_sdk_evidence_invalid")
                public_payload = evidence.get("public_payload")
                if not isinstance(public_payload, dict):
                    raise ForegroundQueueError("foreground_terminal_sdk_evidence_invalid")
                payload_generation = public_payload.get("generation")
                if (
                    isinstance(payload_generation, bool)
                    or not isinstance(payload_generation, int)
                    or payload_generation != generation
                ):
                    raise ForegroundQueueError("foreground_terminal_generation_mismatch")
                payload_terminal = public_payload.get("terminal_state")
                if (
                    not isinstance(payload_terminal, str)
                    or payload_terminal.upper() != terminal.value
                ):
                    raise ForegroundQueueError("foreground_terminal_state_mismatch")
                transition_id, transition_hash = await self._insert_run_transition_tx(
                    db,
                    host_run_id=host_run_id,
                    subject=str(head["subject"]),
                    from_state=str(head["current_state"]),
                    to_state=terminal.value,
                    generation=generation,
                    owner_id=owner_id,
                    sdk_event_id=sdk_event_id,
                    idempotency_key=idempotency_key,
                    causal_evidence_ref=sdk_event_id,
                    causal_evidence_hash=sdk_event_hash,
                    recorded_at=now,
                )
                terminal_id = _uuid(f"foreground-terminal:{host_run_id}")
                terminal_payload = {
                    "schema_version": 1,
                    "terminal_receipt_id": terminal_id,
                    "host_run_id": host_run_id,
                    "sdk_run_id": sdk_run_id,
                    "terminal_state": terminal.value,
                    "generation": generation,
                    "sdk_event_id": sdk_event_id,
                    "sdk_event_hash": sdk_event_hash,
                    "terminal_gate_receipt_id": gate["gate_receipt_id"],
                    "terminal_source_sequence": int(
                        gate["terminal_source_sequence"]
                    ),
                    "terminal_gate_durable_source_sequence": int(
                        gate["durable_source_sequence"]
                    ),
                    "recorded_at": now,
                }
                terminal_hash = canonical_hash(terminal_payload)
                await db.execute(
                    "INSERT INTO foreground_terminal_receipts(terminal_receipt_id,host_run_id,sdk_run_id,terminal_state,generation,sdk_event_id,sdk_event_hash,receipt_hash,receipt_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (terminal_id, host_run_id, sdk_run_id, terminal.value, generation, sdk_event_id, sdk_event_hash, terminal_hash, canonical_json(terminal_payload), now),
                )
                await self._insert_lease_receipt_tx(
                    db,
                    host_run_id=host_run_id,
                    owner_id=owner_id,
                    generation=generation,
                    prior_generation=generation,
                    action="close",
                    expires_at=None,
                    idempotency_key=f"terminal-close:{sdk_event_id}",
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_run_heads SET current_state=?,owner_id=NULL,lease_expires_at=NULL,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                    (terminal.value, transition_id, transition_hash, now, host_run_id),
                )
                turn_transition_id, turn_transition_hash = await self._insert_turn_transition_tx(
                    db,
                    turn_id=str(head["turn_id"]),
                    subject=str(head["subject"]),
                    from_state=TurnState.CLAIMED.value,
                    to_state=TurnState.SETTLED.value,
                    host_run_id=host_run_id,
                    recorded_at=now,
                )
                await db.execute(
                    "UPDATE foreground_turn_heads SET current_state='SETTLED',last_transition_id=?,last_transition_hash=?,updated_at=? WHERE turn_id=? AND current_state='CLAIMED'",
                    (turn_transition_id, turn_transition_hash, now, head["turn_id"]),
                )
                self._fault("terminal.before_commit")
                row = await self._fetchone(db, "SELECT * FROM foreground_terminal_receipts WHERE host_run_id=?", (host_run_id,))
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("terminal.after_commit")
        assert row is not None
        return self._terminal_receipt(row)

    async def current_snapshot(self, subject: str) -> ForegroundRunSnapshot | None:
        subject = identifier(subject, "subject", 512)
        await self.initialize()
        async with self._connection() as db:
            head = await self._active_head_tx(db, subject)
            if head is None:
                return None
            return await self._snapshot_tx(db, str(head["host_run_id"]))

    async def authorize_effect(
        self,
        *,
        host_run_id: str,
        sdk_run_id: str,
        owner_id: str,
        generation: int,
    ) -> EffectAdmissionReceipt:
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
            if head["current_state"] != RunState.RUNNING.value:
                raise ForegroundQueueError("foreground_effect_state_rejected")
            await self._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
            snapshot = await self._snapshot_tx(db, host_run_id)
        return EffectAdmissionReceipt(host_run_id, sdk_run_id, generation, snapshot.snapshot_hash)

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
    ) -> ForegroundRunSnapshot:
        host_run_id = identifier(host_run_id, "host_run_id", 512)
        sdk_run_id = identifier(sdk_run_id, "sdk_run_id", 512)
        owner_id = identifier(owner_id, "owner_id", 512)
        sdk_event_id = identifier(sdk_event_id, "sdk_event_id", 512)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                replay = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_run_transitions WHERE host_run_id=? AND idempotency_key=?",
                    (host_run_id, idempotency_key),
                )
                if replay is not None:
                    if replay["to_state"] != to_state.value or replay["sdk_event_id"] != sdk_event_id:
                        raise ForegroundQueueError("foreground_state_idempotency_conflict")
                    snapshot = await self._snapshot_tx(db, host_run_id)
                    await db.commit()
                    return snapshot
                head = await self._validate_lease_tx(db, host_run_id, owner_id, generation, now)
                await self._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
                if str(head["current_state"]) not in allowed_from:
                    raise ForegroundQueueError("foreground_state_transition_invalid")
                transition_id, transition_hash = await self._insert_run_transition_tx(
                    db,
                    host_run_id=host_run_id,
                    subject=str(head["subject"]),
                    from_state=str(head["current_state"]),
                    to_state=to_state.value,
                    generation=generation,
                    owner_id=owner_id,
                    sdk_event_id=sdk_event_id,
                    idempotency_key=idempotency_key,
                    causal_evidence_ref=None,
                    causal_evidence_hash=None,
                    recorded_at=now,
                )
                desired = None if clear_pause else head["desired_control"]
                await db.execute(
                    "UPDATE foreground_run_heads SET current_state=?,desired_control=?,last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                    (to_state.value, desired, transition_id, transition_hash, now, host_run_id),
                )
                snapshot = await self._snapshot_tx(db, host_run_id)
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return snapshot

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
            (transition_id, turn_id, subject, from_state, to_state, host_run_id, transition_hash, canonical_json(payload), recorded_at),
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
        transition_hash = canonical_hash(payload)
        await db.execute(
            "INSERT INTO foreground_run_transitions(transition_id,host_run_id,subject,from_state,to_state,generation,owner_id,sdk_event_id,idempotency_key,causal_evidence_ref,causal_evidence_hash,transition_hash,transition_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (transition_id, host_run_id, subject, from_state, to_state, generation, owner_id, sdk_event_id, idempotency_key, causal_evidence_ref, causal_evidence_hash, transition_hash, canonical_json(payload), recorded_at),
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
            (lease_id, host_run_id, owner_id, generation, prior_generation, action, expires_at, idempotency_key, lease_hash, canonical_json(payload), recorded_at),
        )
        return LeaseReceipt(lease_id, host_run_id, owner_id, generation, prior_generation, action, expires_at, lease_hash)

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
            (signal_id, control_id, host_run_id, generation, control_kind.value, signal_hash, canonical_json(payload), recorded_at),
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

    async def _snapshot_tx(self, db: aiosqlite.Connection, host_run_id: str) -> ForegroundRunSnapshot:
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
            binding_set_receipt_id=str(row["binding_set_receipt_id"]) if row["binding_set_receipt_id"] is not None else None,
            binding_set_receipt_hash=str(row["binding_set_receipt_hash"]) if row["binding_set_receipt_hash"] is not None else None,
            context_snapshot_id=str(row["context_snapshot_id"]),
            context_snapshot_revision=int(row["context_snapshot_revision"]),
            context_snapshot_hash=str(row["context_snapshot_hash"]),
            state=RunState(str(row["current_state"])),
            desired_control=ControlKind(str(row["desired_control"])) if row["desired_control"] is not None else None,
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
        row = await self._fetchone(db, "SELECT * FROM foreground_run_heads WHERE host_run_id=?", (host_run_id,))
        if row is None:
            raise ForegroundQueueError("foreground_run_not_found")
        return row

    async def _run_tx(self, db: aiosqlite.Connection, host_run_id: str) -> aiosqlite.Row:
        row = await self._fetchone(db, "SELECT * FROM foreground_runs WHERE host_run_id=?", (host_run_id,))
        if row is None:
            raise ForegroundQueueError("foreground_run_not_found")
        return row

    async def _lease_by_key_tx(self, db: aiosqlite.Connection, host_run_id: str, key: str) -> aiosqlite.Row | None:
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
        async with aiosqlite.connect(self._db_path) as connection:
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
    def _admission_receipt(run: aiosqlite.Row, head: aiosqlite.Row, *, recovered: bool) -> AdmissionReceipt:
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
            str(row["binding_id"]), str(row["host_run_id"]), str(row["sdk_run_id"]), str(row["binding_hash"])
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


__all__ = [
    "FOREGROUND_SCHEDULER_KIND",
    "AdmissionReceipt",
    "ContextLineage",
    "ControlKind",
    "ControlReceipt",
    "EffectAdmissionReceipt",
    "EnqueueReceipt",
    "ForegroundQueueError",
    "ForegroundQueueStore",
    "ForegroundRunSnapshot",
    "LeaseReceipt",
    "RunState",
    "SdkRunBindingReceipt",
    "SignalAckReceipt",
    "SignalEnvelope",
    "TerminalReceipt",
    "TurnState",
]
