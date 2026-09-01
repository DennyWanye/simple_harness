# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from deskpet.execution import (
    ContextLineage,
    ControlKind,
    ForegroundQueueError,
    ForegroundQueueStore,
    RunState,
)
from deskpet.execution.evidence_ingress import (
    ExecutionEvidenceIngress,
    TerminalWatermarkPending,
)
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.task_scope.protocol import canonical_hash
from deskpet.task_scope.store import CanonicalTaskScopeStore

MIGRATION = (
    Path(__file__).parents[2]
    / "deskpet"
    / "memory"
    / "migrations"
    / "033_foreground_queue_v41.sql"
)
SUBJECT = "actor-1"
SCOPE = "scope-1"
CONTEXT = ContextLineage("context-1", 1, "c" * 64)


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class OneShotFault:
    def __init__(self, point: str) -> None:
        self.point = point
        self.fired = False

    def __call__(self, point: str) -> None:
        if point == self.point and not self.fired:
            self.fired = True
            raise RuntimeError(f"injected:{point}")


async def _ready(tmp_path: Path, evidence_count: int = 3) -> tuple[Path, str, Clock]:
    db_path = tmp_path / "state.db"
    await CanonicalTaskScopeStore(db_path).create_task_scope(
        task_scope_id=SCOPE, subject=SUBJECT, title="Foreground task"
    )
    primary = await HumanMemoryProgramStore(db_path).initialize_subject(SUBJECT)
    with sqlite3.connect(db_path) as db:
        for index in range(1, evidence_count + 1):
            evidence_id = f"evidence-{index}"
            receipt_id = f"receipt-{index}"
            envelope_hash = f"{index:x}" * 64
            db.execute(
                "INSERT INTO human_memory_sanitization_receipts("
                "receipt_id,evidence_id,subject,run_id,envelope_sha256,source_sha256,"
                "sanitized_sha256,filter_policy_version,receipt_sha256,receipt_json,"
                "admitted_at,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt_id,
                    evidence_id,
                    SUBJECT,
                    f"source-run-{index}",
                    envelope_hash,
                    "a" * 64,
                    "b" * 64,
                    "test/v1",
                    f"{index + 3:x}" * 64,
                    "{}",
                    float(index),
                    float(index),
                ),
            )
            db.execute(
                "INSERT INTO human_memory_evidence("
                "evidence_id,primary_conversation_id,subject,run_id,source_kind,source_ref,"
                "source_sha256,sanitized_sha256,envelope_sha256,receipt_id,payload_json,"
                "envelope_json,occurred_at,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id,
                    primary.primary_conversation_id,
                    SUBJECT,
                    f"source-run-{index}",
                    "typed_observation",
                    f"test/{evidence_id}",
                    "a" * 64,
                    "b" * 64,
                    envelope_hash,
                    receipt_id,
                    "{}",
                    "{}",
                    float(index),
                    float(index),
                ),
            )
        sql = MIGRATION.read_text(encoding="utf-8")
        marker_table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='foreground_queue_marker'"
        ).fetchone()
        if marker_table is None:
            db.executescript(sql)
        db.execute(
            "INSERT OR IGNORE INTO foreground_queue_marker(singleton,format_epoch,schema_version,"
            "migration_id,migration_sha256,initialized_at) VALUES (1,'human-memory-v1',1,?,?,?)",
            (
                MIGRATION.name,
                hashlib.sha256(MIGRATION.read_bytes()).hexdigest(),
                100.0,
            ),
        )
        db.commit()
    clock = Clock()
    await ForegroundQueueStore(db_path, clock=clock).initialize()
    return db_path, primary.primary_conversation_id, clock


async def _enqueue(
    store: ForegroundQueueStore, primary_id: str, index: int
):
    return await store.enqueue_turn(
        subject=SUBJECT,
        primary_conversation_id=primary_id,
        task_scope_id=SCOPE,
        evidence_id=f"evidence-{index}",
        evidence_hash=f"{index:x}" * 64,
        idempotency_key=f"enqueue-{index}",
        turn_payload={"text": f"turn {index}"},
    )


async def _claim_and_bind(
    store: ForegroundQueueStore,
    *,
    owner: str = "owner-1",
    claim_key: str = "claim-1",
    sdk_run_id: str = "sdk-run-1",
):
    admission = await store.claim_next(
        subject=SUBJECT,
        owner_id=owner,
        claim_idempotency_key=claim_key,
        context=CONTEXT,
        lease_seconds=10,
    )
    assert admission is not None
    await store.bind_sdk_run(
        host_run_id=admission.host_run_id,
        sdk_run_id=sdk_run_id,
        owner_id=owner,
        generation=admission.generation,
        idempotency_key=f"bind-{claim_key}",
    )
    return admission


class _ExecutionEvidence:
    def __init__(self, raw: dict[str, object]) -> None:
        self._raw = raw
        for key, value in raw.items():
            setattr(self, key, value)
        self.evidence_hash = canonical_hash(raw)

    def to_json(self) -> dict[str, object]:
        return json.loads(json.dumps(self._raw))


def _execution_evidence(
    *,
    sdk_run_id: str,
    source_event_id: str,
    kind: str,
    source_sequence: int,
    generation: int = 1,
    terminal_state: RunState | None = None,
) -> _ExecutionEvidence:
    public_payload: dict[str, object] = {
        "generation": generation,
        "sequence": source_sequence,
    }
    if terminal_state is not None:
        public_payload["terminal_state"] = terminal_state.value
    return _ExecutionEvidence(
        {
            "schema_version": 1,
            "event_id": source_event_id,
            "run_id": sdk_run_id,
            "subject": SUBJECT,
            "kind": kind,
            "public_payload": public_payload,
            "disclosure_context": {
                "schema_version": 1,
                "run_id": sdk_run_id,
                "subject": SUBJECT,
                "recipient": "user_self",
                "recipient_id": SUBJECT,
                "intended_audience": "user_self",
                "purpose": "task_continuity",
                "source": "authenticated_host",
                "trust": "trusted_authority",
                "generation": str(generation),
                "authority_ref": "foreground-queue-test",
                "reason_codes": ["minimum_necessary"],
            },
            "evidence_refs": [
                {"evidence_id": "evidence-1", "content_hash": "1" * 64, "ordinal": 1}
            ],
            "idempotency_key": f"ingest-{source_event_id}",
            "occurred_at": float(source_sequence),
        }
    )


async def _ingest_execution(
    db_path: Path,
    *,
    sdk_run_id: str,
    source_event_id: str,
    kind: str,
    source_sequence: int,
    generation: int = 1,
    terminal_state: RunState | None = None,
) -> _ExecutionEvidence:
    evidence = _execution_evidence(
        sdk_run_id=sdk_run_id,
        source_event_id=source_event_id,
        kind=kind,
        source_sequence=source_sequence,
        generation=generation,
        terminal_state=terminal_state,
    )
    await ExecutionEvidenceIngress(db_path).ingest(
        task_scope_id=SCOPE,
        source_sequence=source_sequence,
        evidence=evidence,
    )
    return evidence


async def _authorized_terminal_evidence(
    db_path: Path,
    *,
    sdk_run_id: str,
    source_event_id: str,
    terminal_state: RunState,
    generation: int = 1,
) -> _ExecutionEvidence:
    evidence = await _ingest_execution(
        db_path,
        sdk_run_id=sdk_run_id,
        source_event_id=source_event_id,
        kind="run_terminal",
        source_sequence=1,
        generation=generation,
        terminal_state=terminal_state,
    )
    await ExecutionEvidenceIngress(db_path).authorize_terminal(sdk_run_id)
    return evidence


def _assert_code(exc: pytest.ExceptionInfo[ForegroundQueueError], code: str) -> None:
    assert exc.value.code == code


@pytest.mark.asyncio
async def test_evidence_first_concurrent_enqueue_and_fifo_claim(tmp_path: Path) -> None:
    db_path, primary_id, clock = await _ready(tmp_path)
    store = ForegroundQueueStore(db_path, clock=clock)

    with pytest.raises(ForegroundQueueError) as invalid:
        await store.enqueue_turn(
            subject=SUBJECT,
            primary_conversation_id=primary_id,
            task_scope_id=SCOPE,
            evidence_id="evidence-1",
            evidence_hash="f" * 64,
            idempotency_key="bad-evidence",
            turn_payload={"text": "must not enqueue"},
        )
    _assert_code(invalid, "foreground_evidence_authority_mismatch")

    receipts = await asyncio.gather(*(_enqueue(store, primary_id, i) for i in (1, 2, 3)))
    assert sorted(receipt.enqueue_sequence for receipt in receipts) == [1, 2, 3]
    replay = await _enqueue(store, primary_id, 1)
    assert replay == next(item for item in receipts if item.turn_id == replay.turn_id)
    with pytest.raises(ForegroundQueueError) as conflict:
        await store.enqueue_turn(
            subject=SUBJECT,
            primary_conversation_id=primary_id,
            task_scope_id=SCOPE,
            evidence_id="evidence-1",
            evidence_hash="1" * 64,
            idempotency_key="enqueue-1",
            turn_payload={"text": "changed"},
        )
    _assert_code(conflict, "foreground_turn_idempotency_conflict")

    claimed = await store.claim_next(
        subject=SUBJECT,
        owner_id="owner-1",
        claim_idempotency_key="claim-1",
        context=CONTEXT,
        lease_seconds=10,
    )
    assert claimed is not None
    assert claimed.enqueue_sequence == 1
    recovered = await store.claim_next(
        subject=SUBJECT,
        owner_id="owner-1",
        claim_idempotency_key="claim-1",
        context=ContextLineage("ignored-on-retry", 9, "d" * 64),
        lease_seconds=99,
    )
    assert recovered is not None
    assert recovered.host_run_id == claimed.host_run_id
    assert recovered.lineage_hash == claimed.lineage_hash
    assert recovered.recovered is True
    with pytest.raises(ForegroundQueueError) as duplicate:
        await store.claim_next(
            subject=SUBJECT,
            owner_id="owner-2",
            claim_idempotency_key="claim-2",
            context=CONTEXT,
            lease_seconds=10,
        )
    _assert_code(duplicate, "foreground_run_already_active")
    with pytest.raises(ForegroundQueueError) as background:
        await store.claim_next(
            subject=SUBJECT,
            owner_id="projection-worker",
            claim_idempotency_key="background-claim",
            context=CONTEXT,
            lease_seconds=10,
            worker_kind="projection_worker",
        )
    _assert_code(background, "foreground_background_claim_rejected")


@pytest.mark.asyncio
async def test_control_priority_sdk_terminal_and_atomic_next_admission(tmp_path: Path) -> None:
    db_path, primary_id, clock = await _ready(tmp_path, evidence_count=2)
    store = ForegroundQueueStore(db_path, clock=clock)
    await _enqueue(store, primary_id, 1)
    await _enqueue(store, primary_id, 2)
    admission = await _claim_and_bind(store)
    binding = await store.bind_sdk_run(
        host_run_id=admission.host_run_id,
        sdk_run_id="sdk-run-1",
        owner_id="owner-1",
        generation=1,
        idempotency_key="bind-retry",
    )
    assert binding.sdk_run_id == "sdk-run-1"
    with pytest.raises(ForegroundQueueError) as rebind:
        await store.bind_sdk_run(
            host_run_id=admission.host_run_id,
            sdk_run_id="sdk-run-new",
            owner_id="owner-1",
            generation=1,
            idempotency_key="bind-conflict",
        )
    _assert_code(rebind, "foreground_sdk_run_binding_conflict")
    await store.record_sdk_started(
        host_run_id=admission.host_run_id,
        sdk_run_id="sdk-run-1",
        owner_id="owner-1",
        generation=1,
        sdk_event_id="started-1",
        idempotency_key="started-1",
    )
    pause = await store.request_control(
        host_run_id=admission.host_run_id,
        subject=SUBJECT,
        generation=1,
        control_kind=ControlKind.PAUSE,
        reason="user_pause",
        idempotency_key="control-pause",
    )
    stop = await store.request_control(
        host_run_id=admission.host_run_id,
        subject=SUBJECT,
        generation=1,
        control_kind=ControlKind.STOP,
        reason="user_stop",
        idempotency_key="control-stop",
    )
    weaker = await store.request_control(
        host_run_id=admission.host_run_id,
        subject=SUBJECT,
        generation=1,
        control_kind=ControlKind.PAUSE,
        reason="late_pause",
        idempotency_key="control-late-pause",
    )
    cancel = await store.request_control(
        host_run_id=admission.host_run_id,
        subject=SUBJECT,
        generation=1,
        control_kind=ControlKind.CANCEL,
        reason="user_cancel",
        idempotency_key="control-cancel",
    )
    assert pause.outcome == stop.outcome == cancel.outcome == "signalled"
    assert weaker.outcome == "superseded"
    assert cancel.reduced_state is RunState.CANCEL_REQUESTED
    signals = await store.pending_signals(admission.host_run_id)
    assert len(signals) == 1
    assert signals[0].control_kind is ControlKind.CANCEL
    cancel_signal = signals[0]
    restarted = ForegroundQueueStore(db_path, clock=clock)
    assert await restarted.pending_signals(admission.host_run_id) == signals
    for superseded_signal in (pause.signal_id, stop.signal_id):
        assert superseded_signal is not None
        with pytest.raises(ForegroundQueueError) as superseded_ack:
            await restarted.acknowledge_signal(
                signal_id=superseded_signal,
                sdk_run_id="sdk-run-1",
                owner_id="owner-1",
                generation=1,
                sdk_signal_id=f"sdk-{superseded_signal}",
            )
        _assert_code(superseded_ack, "foreground_signal_superseded")
    concurrent_acks = await asyncio.gather(
        *(
            restarted.acknowledge_signal(
                signal_id=cancel_signal.signal_id,
                sdk_run_id="sdk-run-1",
                owner_id="owner-1",
                generation=1,
                sdk_signal_id="sdk-signal-cancel",
            )
            for _ in range(2)
        )
    )
    assert concurrent_acks[0] == concurrent_acks[1]
    ack = concurrent_acks[0]
    assert (
        await restarted.acknowledge_signal(
            signal_id=cancel_signal.signal_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-1",
            generation=1,
            sdk_signal_id="sdk-signal-cancel",
        )
    ) == ack

    terminal_hash = "9" * 64
    with pytest.raises(ForegroundQueueError) as unauthenticated_terminal:
        await store.record_sdk_terminal(
            host_run_id=admission.host_run_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-1",
            generation=1,
            terminal_state=RunState.CANCELLED,
            sdk_event_id="sdk-terminal-1",
            sdk_event_hash=terminal_hash,
            idempotency_key="terminal-1",
        )
    _assert_code(
        unauthenticated_terminal, "foreground_terminal_sdk_evidence_missing"
    )
    snapshot_before_evidence = await store.current_snapshot(SUBJECT)
    assert snapshot_before_evidence is not None
    assert snapshot_before_evidence.state is RunState.CANCEL_REQUESTED
    terminal_evidence = await _authorized_terminal_evidence(
        db_path,
        sdk_run_id="sdk-run-1",
        source_event_id="sdk-terminal-1",
        terminal_state=RunState.CANCELLED,
    )
    terminal_hash = terminal_evidence.evidence_hash
    terminal = await store.record_sdk_terminal(
        host_run_id=admission.host_run_id,
        sdk_run_id="sdk-run-1",
        owner_id="owner-1",
        generation=1,
        terminal_state=RunState.CANCELLED,
        sdk_event_id="sdk-terminal-1",
        sdk_event_hash=terminal_hash,
        idempotency_key="terminal-1",
    )
    assert terminal.terminal_state is RunState.CANCELLED
    assert await store.current_snapshot(SUBJECT) is None
    with pytest.raises(ForegroundQueueError) as immutable:
        await store.record_sdk_terminal(
            host_run_id=admission.host_run_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-1",
            generation=1,
            terminal_state=RunState.COMPLETED,
            sdk_event_id="sdk-terminal-1",
            sdk_event_hash=terminal_hash,
            idempotency_key="terminal-changed",
        )
    _assert_code(immutable, "foreground_terminal_immutable")
    next_admission = await store.claim_next(
        subject=SUBJECT,
        owner_id="owner-2",
        claim_idempotency_key="claim-2",
        context=CONTEXT,
        lease_seconds=10,
    )
    assert next_admission is not None
    assert next_admission.enqueue_sequence == 2


@pytest.mark.asyncio
async def test_terminal_waits_for_contiguous_gate_and_settles_once(
    tmp_path: Path,
) -> None:
    db_path, primary_id, clock = await _ready(tmp_path, evidence_count=2)
    store = ForegroundQueueStore(db_path, clock=clock)
    await _enqueue(store, primary_id, 1)
    await _enqueue(store, primary_id, 2)
    admission = await _claim_and_bind(store)
    terminal_evidence = await _ingest_execution(
        db_path,
        sdk_run_id="sdk-run-1",
        source_event_id="sdk-terminal-gap",
        kind="run_terminal",
        source_sequence=3,
        terminal_state=RunState.COMPLETED,
    )

    with pytest.raises(ForegroundQueueError) as pending:
        await store.record_sdk_terminal(
            host_run_id=admission.host_run_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-1",
            generation=1,
            terminal_state=RunState.COMPLETED,
            sdk_event_id="sdk-terminal-gap",
            sdk_event_hash=terminal_evidence.evidence_hash,
            idempotency_key="terminal-gap",
        )
    _assert_code(pending, "foreground_terminal_gate_pending")
    assert await store.current_snapshot(SUBJECT) is not None
    with pytest.raises(ForegroundQueueError) as still_active:
        await store.claim_next(
            subject=SUBJECT,
            owner_id="owner-2",
            claim_idempotency_key="claim-2",
            context=CONTEXT,
            lease_seconds=10,
        )
    _assert_code(still_active, "foreground_run_already_active")
    with pytest.raises(TerminalWatermarkPending):
        await ExecutionEvidenceIngress(db_path).authorize_terminal("sdk-run-1")

    await _ingest_execution(
        db_path,
        sdk_run_id="sdk-run-1",
        source_event_id="sdk-event-1",
        kind="provider_invocation",
        source_sequence=1,
    )
    await _ingest_execution(
        db_path,
        sdk_run_id="sdk-run-1",
        source_event_id="sdk-event-2",
        kind="tool_invocation",
        source_sequence=2,
    )
    await ExecutionEvidenceIngress(db_path).authorize_terminal("sdk-run-1")

    with pytest.raises(ForegroundQueueError) as mismatched_state:
        await store.record_sdk_terminal(
            host_run_id=admission.host_run_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-1",
            generation=1,
            terminal_state=RunState.FAILED,
            sdk_event_id="sdk-terminal-gap",
            sdk_event_hash=terminal_evidence.evidence_hash,
            idempotency_key="terminal-state-mismatch",
        )
    _assert_code(mismatched_state, "foreground_terminal_state_mismatch")
    assert await store.current_snapshot(SUBJECT) is not None

    terminal_receipts = await asyncio.gather(
        *(
            store.record_sdk_terminal(
                host_run_id=admission.host_run_id,
                sdk_run_id="sdk-run-1",
                owner_id="owner-1",
                generation=1,
                terminal_state=RunState.COMPLETED,
                sdk_event_id="sdk-terminal-gap",
                sdk_event_hash=terminal_evidence.evidence_hash,
                idempotency_key="terminal-gap",
            )
            for _ in range(2)
        )
    )
    assert terminal_receipts[0] == terminal_receipts[1]
    assert await store.current_snapshot(SUBJECT) is None
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM foreground_terminal_receipts"
        ).fetchone()[0] == 1
        terminal_json = json.loads(
            db.execute(
                "SELECT receipt_json FROM foreground_terminal_receipts"
            ).fetchone()[0]
        )
        gate_id = db.execute(
            "SELECT gate_receipt_id FROM task_scope_terminal_gate_receipts "
            "WHERE run_id='sdk-run-1'"
        ).fetchone()[0]
        assert terminal_json["terminal_gate_receipt_id"] == gate_id
        assert terminal_json["terminal_source_sequence"] == 3
        assert terminal_json["terminal_gate_durable_source_sequence"] == 3
        assert db.execute(
            "SELECT COUNT(*) FROM foreground_turn_heads WHERE current_state='SETTLED'"
        ).fetchone()[0] == 1
    next_admission = await store.claim_next(
        subject=SUBJECT,
        owner_id="owner-2",
        claim_idempotency_key="claim-2",
        context=CONTEXT,
        lease_seconds=10,
    )
    assert next_admission is not None
    assert next_admission.enqueue_sequence == 2


@pytest.mark.asyncio
async def test_terminal_rejects_canonical_event_from_other_generation(
    tmp_path: Path,
) -> None:
    db_path, primary_id, clock = await _ready(tmp_path, evidence_count=1)
    store = ForegroundQueueStore(db_path, clock=clock)
    await _enqueue(store, primary_id, 1)
    admission = await _claim_and_bind(store)
    terminal_evidence = await _authorized_terminal_evidence(
        db_path,
        sdk_run_id="sdk-run-1",
        source_event_id="sdk-terminal-generation-2",
        terminal_state=RunState.COMPLETED,
        generation=2,
    )
    with pytest.raises(ForegroundQueueError) as mismatch:
        await store.record_sdk_terminal(
            host_run_id=admission.host_run_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-1",
            generation=1,
            terminal_state=RunState.COMPLETED,
            sdk_event_id="sdk-terminal-generation-2",
            sdk_event_hash=terminal_evidence.evidence_hash,
            idempotency_key="terminal-generation-mismatch",
        )
    _assert_code(mismatch, "foreground_terminal_generation_mismatch")
    assert await store.current_snapshot(SUBJECT) is not None


@pytest.mark.asyncio
async def test_pause_resume_reclaim_and_stale_generation_fences(tmp_path: Path) -> None:
    db_path, primary_id, clock = await _ready(tmp_path, evidence_count=1)
    store = ForegroundQueueStore(db_path, clock=clock)
    await _enqueue(store, primary_id, 1)
    admission = await _claim_and_bind(store)
    await store.record_sdk_started(
        host_run_id=admission.host_run_id,
        sdk_run_id="sdk-run-1",
        owner_id="owner-1",
        generation=1,
        sdk_event_id="started-1",
        idempotency_key="started-1",
    )
    pause = await store.request_control(
        host_run_id=admission.host_run_id,
        subject=SUBJECT,
        generation=1,
        control_kind="pause",
        reason="pause_for_user",
        idempotency_key="pause-1",
    )
    assert pause.signal_id is not None
    await store.record_pause_outcome(
        host_run_id=admission.host_run_id,
        sdk_run_id="sdk-run-1",
        owner_id="owner-1",
        generation=1,
        sdk_event_id="paused-1",
        paused=True,
        idempotency_key="paused-1",
    )
    resumed = await store.resume_paused(
        host_run_id=admission.host_run_id,
        owner_id="owner-2",
        expected_generation=1,
        lease_seconds=10,
        idempotency_key="resume-1",
    )
    assert resumed.state is RunState.RUNNING
    assert resumed.generation == 2
    assert (
        await store.resume_paused(
            host_run_id=admission.host_run_id,
            owner_id="owner-2",
            expected_generation=1,
            lease_seconds=999,
            idempotency_key="resume-1",
        )
    ) == resumed
    with pytest.raises(ForegroundQueueError) as stale_effect:
        await store.authorize_effect(
            host_run_id=admission.host_run_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-1",
            generation=1,
        )
    _assert_code(stale_effect, "foreground_generation_stale")
    effect = await store.authorize_effect(
        host_run_id=admission.host_run_id,
        sdk_run_id="sdk-run-1",
        owner_id="owner-2",
        generation=2,
    )
    assert effect.generation == 2

    stop = await store.request_control(
        host_run_id=admission.host_run_id,
        subject=SUBJECT,
        generation=2,
        control_kind="stop",
        reason="stop_before_crash",
        idempotency_key="stop-2",
    )
    assert stop.signal_id is not None
    clock.now = resumed.lease_expires_at + 1
    reclaimed = await store.reclaim_expired(
        host_run_id=admission.host_run_id,
        new_owner_id="owner-3",
        expected_generation=2,
        lease_seconds=10,
        idempotency_key="reclaim-3",
    )
    assert reclaimed.generation == 3
    snapshot = await store.current_snapshot(SUBJECT)
    assert snapshot is not None
    assert snapshot.host_run_id == admission.host_run_id
    assert snapshot.sdk_run_id == "sdk-run-1"
    assert snapshot.generation == 3
    assert snapshot.owner_id == "owner-3"
    signals = await store.pending_signals(admission.host_run_id)
    assert len(signals) == 1
    assert signals[0].generation == 3
    assert signals[0].control_kind is ControlKind.STOP
    with pytest.raises(ForegroundQueueError) as stale_ack:
        await store.acknowledge_signal(
            signal_id=stop.signal_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-2",
            generation=2,
            sdk_signal_id="late-old-owner",
        )
    _assert_code(stale_ack, "foreground_generation_stale")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_point,committed", [("claim.before_commit", False), ("claim.after_commit", True)])
async def test_claim_transaction_fault_is_rollback_or_exact_recovery(
    tmp_path: Path, fault_point: str, committed: bool
) -> None:
    db_path, primary_id, clock = await _ready(tmp_path, evidence_count=1)
    plain = ForegroundQueueStore(db_path, clock=clock)
    await _enqueue(plain, primary_id, 1)
    faulted = ForegroundQueueStore(db_path, clock=clock, fault_hook=OneShotFault(fault_point))
    with pytest.raises(RuntimeError, match=fault_point):
        await faulted.claim_next(
            subject=SUBJECT,
            owner_id="owner-1",
            claim_idempotency_key="claim-1",
            context=CONTEXT,
            lease_seconds=10,
        )
    recovered = await plain.claim_next(
        subject=SUBJECT,
        owner_id="owner-1",
        claim_idempotency_key="claim-1",
        context=CONTEXT,
        lease_seconds=10,
    )
    assert recovered is not None
    assert recovered.recovered is committed
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM foreground_runs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM foreground_lease_receipts").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_point,committed",
    [("terminal.before_commit", False), ("terminal.after_commit", True)],
)
async def test_terminal_transaction_fault_never_partially_settles(
    tmp_path: Path, fault_point: str, committed: bool
) -> None:
    db_path, primary_id, clock = await _ready(tmp_path, evidence_count=1)
    plain = ForegroundQueueStore(db_path, clock=clock)
    await _enqueue(plain, primary_id, 1)
    admission = await _claim_and_bind(plain)
    terminal_hash = "9" * 64
    terminal_evidence = await _authorized_terminal_evidence(
        db_path,
        sdk_run_id="sdk-run-1",
        source_event_id="sdk-terminal-1",
        terminal_state=RunState.COMPLETED,
    )
    terminal_hash = terminal_evidence.evidence_hash
    faulted = ForegroundQueueStore(
        db_path, clock=clock, fault_hook=OneShotFault(fault_point)
    )
    with pytest.raises(RuntimeError, match=fault_point):
        await faulted.record_sdk_terminal(
            host_run_id=admission.host_run_id,
            sdk_run_id="sdk-run-1",
            owner_id="owner-1",
            generation=1,
            terminal_state=RunState.COMPLETED,
            sdk_event_id="sdk-terminal-1",
            sdk_event_hash=terminal_hash,
            idempotency_key="terminal-1",
        )
    with sqlite3.connect(db_path) as db:
        terminal_count = db.execute(
            "SELECT COUNT(*) FROM foreground_terminal_receipts"
        ).fetchone()[0]
        turn_state = db.execute(
            "SELECT current_state FROM foreground_turn_heads"
        ).fetchone()[0]
    assert terminal_count == int(committed)
    assert turn_state == ("SETTLED" if committed else "CLAIMED")
    receipt = await plain.record_sdk_terminal(
        host_run_id=admission.host_run_id,
        sdk_run_id="sdk-run-1",
        owner_id="owner-1",
        generation=1,
        terminal_state=RunState.COMPLETED,
        sdk_event_id="sdk-terminal-1",
        sdk_event_hash=terminal_hash,
        idempotency_key="terminal-1",
    )
    assert receipt.terminal_state is RunState.COMPLETED
    with sqlite3.connect(db_path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM foreground_terminal_receipts"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT current_state FROM foreground_turn_heads"
        ).fetchone()[0] == "SETTLED"
