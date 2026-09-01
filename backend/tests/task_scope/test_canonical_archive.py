from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from deskpet.execution.evidence_ingress import (
    ExecutionEvidenceIngress,
    TerminalWatermarkPending,
)
from deskpet.task_scope.protocol import TaskScopeProtocolError, canonical_hash
from deskpet.task_scope.store import (
    CanonicalTaskScopeStore,
    TaskEventRecorder,
    TaskScopeConflict,
)
from deskpet.memory.schema import (
    HumanMemoryProgramEpochError,
    InitializeError,
    initialize_human_memory_program_state_db,
    initialize_state_db,
)


def _disclosure(run_id: str, subject: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "subject": subject,
        "recipient": "user_self",
        "recipient_id": subject,
        "intended_audience": "user_self",
        "purpose": "task_continuity",
        "source": "authenticated_host",
        "trust": "trusted_authority",
        "generation": "current",
        "authority_ref": "host-task-scope",
        "reason_codes": ["minimum_necessary"],
    }


class _Dto:
    def __init__(self, raw: dict[str, Any], hash_name: str) -> None:
        self._raw = raw
        for key, value in raw.items():
            setattr(self, key, value)
        setattr(self, hash_name, canonical_hash(raw))

    def to_json(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._raw))


def _ref(evidence_id: str, content_hash: str) -> dict[str, object]:
    return {"evidence_id": evidence_id, "content_hash": content_hash, "ordinal": 1}


def _plan(
    *,
    plan_id: str,
    base_revision: int,
    content_hash: str,
    operations: list[dict[str, Any]],
    outcome: str = "mutate",
    closure_reason: str | None = None,
) -> _Dto:
    raw = {
        "schema_version": 1,
        "plan_id": plan_id,
        "run_id": "run-1",
        "subject": "actor-1",
        "task_scope_id": "scope-1",
        "base_revision": base_revision,
        "outcome": outcome,
        "operations": operations,
        "closure_reason": closure_reason,
        "source_turn_id": "turn-1",
        "disclosure_context": _disclosure("run-1", "actor-1"),
        "evidence_refs": [_ref("raw-1", content_hash)],
        "idempotency_key": f"idem-{plan_id}",
    }
    return _Dto(raw, "plan_hash")


def _operation(
    operation_id: str,
    kind: str,
    value: str,
    reason: str,
    content_hash: str,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "kind": kind,
        "value": value,
        "evidence_refs": [_ref("raw-1", content_hash)],
        "reason_code": reason,
    }


async def _ready(tmp_path: Path) -> tuple[Path, CanonicalTaskScopeStore, str]:
    db_path = tmp_path / "state.db"
    store = CanonicalTaskScopeStore(db_path)
    await store.create_task_scope(task_scope_id="scope-1", subject="actor-1", title="Task One")
    envelope_hash = "e" * 64
    with sqlite3.connect(db_path) as db:
        primary_id = db.execute(
            "SELECT primary_conversation_id FROM human_memory_primary_conversations WHERE subject='actor-1'"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO human_memory_sanitization_receipts(receipt_id,evidence_id,subject,run_id,envelope_sha256,source_sha256,sanitized_sha256,filter_policy_version,receipt_sha256,receipt_json,admitted_at,committed_at) VALUES ('raw-receipt','raw-1','actor-1','run-1',?,?,?,?,?,'{}',1,1)",
            (envelope_hash, "a" * 64, "b" * 64, "test/v1", "c" * 64),
        )
        db.execute(
            "INSERT INTO human_memory_evidence(evidence_id,primary_conversation_id,subject,run_id,source_kind,source_ref,source_sha256,sanitized_sha256,envelope_sha256,receipt_id,payload_json,envelope_json,occurred_at,committed_at) VALUES ('raw-1',?,'actor-1','run-1','typed_observation','test/raw-1',?,?,?,'raw-receipt','{}','{}',1,1)",
            (primary_id, "a" * 64, "b" * 64, envelope_hash),
        )
        db.commit()
    return db_path, store, envelope_hash


@pytest.mark.asyncio
async def test_host_event_order_and_idempotent_source_hash(tmp_path: Path) -> None:
    _, store, _ = await _ready(tmp_path)
    recorder = TaskEventRecorder(store)
    turn = await recorder.record_turn(task_scope_id="scope-1", source_event_id="turn-1", payload={"turn_id": "turn-1"})
    file_event = await recorder.record_file(task_scope_id="scope-1", source_event_id="file-1", payload={"path": "README.md", "action": "read"})
    test_event = await recorder.record_test(task_scope_id="scope-1", source_event_id="test-1", payload={"command": "pytest", "result": "passed"})
    assert [turn.event_sequence, file_event.event_sequence, test_event.event_sequence] == [1, 2, 3]
    assert await recorder.record_turn(task_scope_id="scope-1", source_event_id="turn-1", payload={"turn_id": "turn-1"}) == turn
    with pytest.raises(TaskScopeConflict, match="source_event_payload_conflict"):
        await recorder.record_turn(task_scope_id="scope-1", source_event_id="turn-1", payload={"turn_id": "different"})


@pytest.mark.asyncio
async def test_mutation_cas_and_cancel_supersede_reasons_are_canonical(tmp_path: Path) -> None:
    db_path, store, evidence_hash = await _ready(tmp_path)
    operations = [
        _operation("step-add", "plan.step.add", "implement archive", "user_confirmed", evidence_hash),
        _operation("step-cancel", "plan.step.cancel", "step-add", "requirement_changed", evidence_hash),
        _operation("decision-1", "decision.record", "keep raw forever", "user_confirmed", evidence_hash),
        _operation("decision-2", "decision.supersede", "decision-1", "newer_user_direction", evidence_hash),
    ]
    receipt = await store.apply_mutation_plan(_plan(plan_id="plan-1", base_revision=1, content_hash=evidence_hash, operations=operations))
    assert receipt.committed_revision == 2
    with sqlite3.connect(db_path) as db:
        state = json.loads(db.execute("SELECT state_json FROM task_scope_canonical_revisions WHERE task_scope_id='scope-1' AND revision=2").fetchone()[0])
        assert [item["reason_code"] for item in state["operations"]] == [
            "user_confirmed", "requirement_changed", "user_confirmed", "newer_user_direction"
        ]
        assert db.execute("SELECT COUNT(*) FROM task_scope_steps").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM task_scope_mutation_decisions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM task_scope_projection_outbox").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM task_scope_search_outbox").fetchone()[0] == 2
    stale = _plan(plan_id="plan-stale", base_revision=1, content_hash=evidence_hash, operations=[_operation("goal", "goal.set", "stale", "model_proposed", evidence_hash)])
    with pytest.raises(TaskScopeConflict, match="mutation_base_revision_conflict"):
        await store.apply_mutation_plan(stale)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT result FROM task_scope_mutation_attempts WHERE plan_id='plan-stale'").fetchone()[0] == "cas_conflict"
        assert db.execute("SELECT COUNT(*) FROM task_scope_canonical_revisions").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_checkpoint_is_immutable_and_projection_rebuilds_from_canonical(tmp_path: Path) -> None:
    db_path, store, _ = await _ready(tmp_path)
    await TaskEventRecorder(store).record_test(
        task_scope_id="scope-1",
        source_event_id="checkpoint-test-event",
        payload={"command": "pytest", "result": "passed"},
    )
    checkpoint = await store.create_checkpoint(checkpoint_id="checkpoint-1", task_scope_id="scope-1", metadata={"why": "pause"})
    duplicate = await store.create_checkpoint(checkpoint_id="checkpoint-1", task_scope_id="scope-1", metadata={"why": "pause"})
    assert duplicate == checkpoint
    with sqlite3.connect(db_path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="task_scope_append_only"):
            db.execute("UPDATE task_scope_checkpoints SET checkpoint_json='{}' WHERE checkpoint_id='checkpoint-1'")
        with pytest.raises(sqlite3.IntegrityError, match="task_scope_append_only"):
            db.execute("DELETE FROM task_scope_checkpoints WHERE checkpoint_id='checkpoint-1'")
        with pytest.raises(sqlite3.IntegrityError, match="task_scope_append_only"):
            db.execute("UPDATE task_scope_events SET payload_json='{}'")
        with pytest.raises(sqlite3.IntegrityError, match="task_scope_append_only"):
            db.execute("DELETE FROM task_scope_canonical_revisions WHERE task_scope_id='scope-1'")
        expected = db.execute("SELECT projection_hash FROM task_scope_projection_cache WHERE task_scope_id='scope-1' AND canonical_revision=1").fetchone()[0]
        db.execute("DELETE FROM task_scope_projection_cache WHERE task_scope_id='scope-1'")
        db.commit()
    assert await store.rebuild_projection("scope-1", 1) == expected
    with pytest.raises(TaskScopeConflict, match="checkpoint_id_hash_conflict"):
        await store.create_checkpoint(checkpoint_id="checkpoint-1", task_scope_id="scope-1", metadata={"why": "different"})


def _execution(event_id: str, sequence: int, kind: str, content_hash: str) -> _Dto:
    raw = {
        "schema_version": 1,
        "event_id": event_id,
        "run_id": "run-1",
        "subject": "actor-1",
        "kind": kind,
        "public_payload": {"sequence": sequence, "status": "public"},
        "disclosure_context": _disclosure("run-1", "actor-1"),
        "evidence_refs": [_ref("raw-1", content_hash)],
        "idempotency_key": f"idem-{event_id}",
        "occurred_at": float(sequence),
    }
    return _Dto(raw, "evidence_hash")


@pytest.mark.asyncio
async def test_execution_ingress_contiguous_watermark_and_terminal_gate(tmp_path: Path) -> None:
    db_path, _, evidence_hash = await _ready(tmp_path)
    ingress = ExecutionEvidenceIngress(db_path)
    terminal = await ingress.ingest(task_scope_id="scope-1", source_sequence=3, evidence=_execution("exec-3", 3, "run_terminal", evidence_hash))
    assert terminal.durable_source_sequence == 0
    assert terminal.terminal_source_sequence == 3
    with pytest.raises(TerminalWatermarkPending):
        await ingress.authorize_terminal("run-1")
    first = await ingress.ingest(task_scope_id="scope-1", source_sequence=1, evidence=_execution("exec-1", 1, "route_decision", evidence_hash))
    assert first.durable_source_sequence == 1
    second_evidence = _execution("exec-2", 2, "tool_invocation", evidence_hash)
    second = await ingress.ingest(task_scope_id="scope-1", source_sequence=2, evidence=second_evidence)
    assert second.durable_source_sequence == 3
    assert await ingress.ingest(task_scope_id="scope-1", source_sequence=2, evidence=second_evidence) == second
    gate = await ingress.authorize_terminal("run-1")
    assert gate.durable_source_sequence == gate.terminal_source_sequence == 3
    with pytest.raises(TaskScopeConflict, match="execution_after_terminal_rejected"):
        await ingress.ingest(
            task_scope_id="scope-1",
            source_sequence=4,
            evidence=_execution("exec-4", 4, "context_snapshot", evidence_hash),
        )
    changed = _execution("exec-2", 2, "tool_invocation", evidence_hash)
    changed._raw["public_payload"] = {"sequence": 2, "status": "changed"}
    changed.public_payload = changed._raw["public_payload"]
    changed.evidence_hash = canonical_hash(changed._raw)
    with pytest.raises(TaskScopeConflict, match="execution_source_event_hash_conflict"):
        await ingress.ingest(task_scope_id="scope-1", source_sequence=2, evidence=changed)


@pytest.mark.asyncio
async def test_structural_protocol_rejects_bool_schema_and_credential_value(tmp_path: Path) -> None:
    db_path, _, evidence_hash = await _ready(tmp_path)
    ingress = ExecutionEvidenceIngress(db_path)
    invalid_schema = _execution("exec-bool", 1, "route_decision", evidence_hash)
    invalid_schema._raw["schema_version"] = True
    with pytest.raises(TaskScopeProtocolError, match="schema_rejected"):
        await ingress.ingest(task_scope_id="scope-1", source_sequence=1, evidence=invalid_schema)
    secret = _execution("exec-secret", 1, "route_decision", evidence_hash)
    secret._raw["public_payload"] = {"message": "Bearer abcdefghijklmnop"}
    secret.public_payload = secret._raw["public_payload"]
    secret.evidence_hash = canonical_hash(secret._raw)
    with pytest.raises(TaskScopeProtocolError, match="credential_value_rejected"):
        await ingress.ingest(task_scope_id="scope-1", source_sequence=1, evidence=secret)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault_stage", ["before_task_scope_archive_commit", "after_task_scope_archive_commit"])
async def test_v36_fault_restart_and_marker_integrity(tmp_path: Path, fault_stage: str) -> None:
    db_path = tmp_path / "state.db"

    def crash(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"crash:{stage}")

    with pytest.raises(InitializeError, match="human memory program initialization failed"):
        await initialize_human_memory_program_state_db(db_path, fault_inject=crash)
    await initialize_human_memory_program_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 42
        assert db.execute("SELECT COUNT(*) FROM task_scope_archive_marker").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version='028_task_scope_archive_v36.sql'").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_task_scope_entry_refuses_old_v34_without_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    before = db_path.read_bytes()
    with pytest.raises(HumanMemoryProgramEpochError):
        await CanonicalTaskScopeStore(db_path).create_task_scope(
            task_scope_id="scope-legacy", subject="actor-1", title="Legacy"
        )
    assert db_path.read_bytes() == before
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 34
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'task_scope_%'").fetchone()[0] == 0
