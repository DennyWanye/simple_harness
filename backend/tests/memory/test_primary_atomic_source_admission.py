"""Source order must describe original USER admission, not delayed delivery."""

import json
import sqlite3
from dataclasses import replace

import pytest
from deskpet.execution.foreground_queue import ForegroundQueueError
from deskpet.memory.human_memory_service import (
    QueueTurnRequest,
    build_foreground_turn_evidence,
)
from tests.memory.test_primary_cognitive_evidence import fixture


def persisted(path):
    with sqlite3.connect(path) as db:
        return (
            db.execute("SELECT evidence_id FROM human_memory_evidence").fetchall(),
            db.execute("SELECT enqueue_sequence,turn_json FROM foreground_turns").fetchall(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["enqueue.after_evidence_insert", "enqueue.before_commit", "enqueue.after_commit"])
async def test_user_evidence_and_queue_commit_together_and_retry_keeps_order(tmp_path, point):
    path, _, service, _ = await fixture(tmp_path)

    def crash(where):
        if where == point:
            raise RuntimeError("simulated_enqueue_boundary_crash")

    service._foreground._fault_hook = crash
    request = QueueTurnRequest(None, "same-delivery", "A genuinely new assertion")
    with pytest.raises(RuntimeError, match="simulated_enqueue_boundary_crash"):
        await service.enqueue_turn(request)
    evidence, turns = persisted(path)
    assert len(evidence) == len(turns) == int(point == "enqueue.after_commit")
    service._foreground._fault_hook = None
    first = await service.enqueue_turn(request)
    again = await service.enqueue_turn(request)
    assert first == again
    evidence, turns = persisted(path)
    assert len(evidence) == len(turns) == 1
    assert turns[0][0] == 1
    body = json.loads(turns[0][1])
    assert body["schema_version"] == 2
    assert body["source_admission"] == "atomic-evidence-and-turn/v1"


@pytest.mark.asyncio
async def test_late_enqueue_of_previously_admitted_source_cannot_mint_atomic_origin(tmp_path):
    path, auth, service, actions = await fixture(tmp_path)
    envelope, receipt = build_foreground_turn_evidence(
        subject=auth.subject, authority_ref=auth.authority_ref,
        delivery_key="old-delivery", text="An old assertion delivered late",
    )
    # Reproduce the old producer's crash window: S1 is durable before queue
    # insertion. A subsequent forget must not make this source a fresh assertion.
    await service._program.append_evidence(envelope, receipt)
    await actions.admit_action(
        payload={"memory_id": "remembered", "expected_revision": 1,
                 "expected_content_hash": "a" * 64},
        idempotency_key="forget-between-source-and-queue",
    )
    old = persisted(path)[0]
    queued = await service.enqueue_turn(
        QueueTurnRequest(None, "old-delivery", "An old assertion delivered late")
    )
    assert queued["enqueue_sequence"] == 1
    evidence, turns = persisted(path)
    assert evidence == old
    body = json.loads(turns[0][1])
    assert body["schema_version"] == 1
    assert "source_admission" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["subject", "evidence_id", "evidence_hash", "turn_payload"])
async def test_pair_must_match_queue_admission_before_any_write(tmp_path, field):
    path, auth, service, _ = await fixture(tmp_path)
    primary = await service.open_primary()
    pair = build_foreground_turn_evidence(
        subject=auth.subject, authority_ref=auth.authority_ref,
        delivery_key="pair-binding", text="Actual source",
    )
    envelope, _ = pair
    args = {
        "subject": auth.subject, "primary_conversation_id": primary["primary_ref"],
        "evidence_id": envelope.evidence_id, "evidence_hash": envelope.envelope_hash,
        "idempotency_key": "pair-binding", "turn_payload": dict(envelope.sanitized_payload),
        "admitted_evidence_pair": pair,
    }
    args[field] = {"text": "changed"} if field == "turn_payload" else (
        "f" * 64 if field == "evidence_hash" else "other-binding"
    )
    with pytest.raises(ForegroundQueueError, match="foreground_evidence_pair_mismatch"):
        await service._foreground.enqueue_turn(**args)
    assert persisted(path) == ([], [])


@pytest.mark.asyncio
async def test_existing_turn_cannot_bypass_complete_receipt_validation(tmp_path):
    path, auth, service, _ = await fixture(tmp_path)
    first = await service.enqueue_turn(QueueTurnRequest(None, "same", "Actual source"))
    original = persisted(path)
    primary = await service.open_primary()
    envelope, receipt = build_foreground_turn_evidence(
        subject=auth.subject, authority_ref=auth.authority_ref,
        delivery_key="same", text="Actual source",
    )
    forged_receipt = replace(receipt, envelope_hash="f" * 64)
    with pytest.raises(ValueError, match="sanitization_receipt_verification_failed"):
        await service._foreground.enqueue_turn(
            subject=auth.subject, primary_conversation_id=primary["primary_ref"],
            evidence_id=envelope.evidence_id, evidence_hash=envelope.envelope_hash,
            idempotency_key="same", turn_payload=dict(envelope.sanitized_payload),
            admitted_evidence_pair=(envelope, forged_receipt),
        )
    assert persisted(path) == original
    assert await service.enqueue_turn(QueueTurnRequest(None, "same", "Actual source")) == first
