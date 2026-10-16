# SPDX-License-Identifier: BUSL-1.1

"""Turn enqueue and preparation candidates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from deskpet.memory import trusted_disclosure
from deskpet.memory.current_input_source import build_input_use, validate_input_use
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.writer_fence import (
    assert_human_memory_ingress_open_tx,
    require_authenticated_host_snapshot,
)
from deskpet.task_scope.protocol import (
    canonical_hash,
    canonical_json,
    digest,
    identifier,
    reject_private_payload,
)

if TYPE_CHECKING:
    from deskpet.memory.human_memory_program import (
        SanitizedEvidenceEnvelopeLike,
        SanitizedEvidenceReceiptLike,
    )
from .core import ForegroundQueueCore
from .support import (
    ContextLineage,
    EnqueueReceipt,
    ForegroundQueueError,
    PreparationCandidate,
    PreparationDraftReceipt,
    TurnState,
    _clock_value,
    _uuid,
)


class EnqueueMixin(ForegroundQueueCore):
    """Turn enqueue and preparation candidates."""

    __slots__ = ()

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
        disclosure_binding_ref: str | None = None,
        input_declaration: dict | None = None,
        input_auth: object | None = None,
        admitted_evidence_pair: tuple[SanitizedEvidenceEnvelopeLike, SanitizedEvidenceReceiptLike]
        | None = None,
    ) -> EnqueueReceipt:
        subject = identifier(subject, "subject", 512)
        primary_conversation_id = identifier(
            primary_conversation_id, "primary_conversation_id", 512
        )
        evidence_id = identifier(evidence_id, "evidence_id", 512)
        evidence_hash = digest(evidence_hash, "evidence_hash")
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        if task_scope_id is not None:
            task_scope_id = identifier(task_scope_id, "task_scope_id", 512)
        if disclosure_binding_ref is not None:
            disclosure_binding_ref = identifier(
                disclosure_binding_ref, "disclosure_binding_ref", 512
            )
        if not isinstance(turn_payload, Mapping):
            raise ForegroundQueueError("foreground_turn_payload_invalid")
        payload = dict(turn_payload)
        reject_private_payload(payload, "foreground_turn")
        if admitted_evidence_pair is not None:
            envelope, receipt = admitted_evidence_pair
            if (
                envelope.subject != subject
                or envelope.evidence_id != evidence_id
                or envelope.envelope_hash != evidence_hash
                or dict(envelope.sanitized_payload) != payload
            ):
                raise ForegroundQueueError("foreground_evidence_pair_mismatch")
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

                existing_request = (
                    None if existing is None else json.loads(str(existing["turn_json"]))
                )
                if existing_request is not None and "disclosure_binding" in existing_request:
                    current = await trusted_disclosure.current_record_tx(db, subject)
                    selected_ref = disclosure_binding_ref or (
                        None if current is None else current["binding_ref"]
                    )
                    if selected_ref != existing_request["disclosure_binding"]["binding_ref"]:
                        raise ForegroundQueueError("foreground_turn_idempotency_conflict")
                elif existing_request is not None and disclosure_binding_ref is not None:
                    raise ForegroundQueueError("foreground_turn_idempotency_conflict")
                input_use = None
                if input_declaration is not None:
                    require_authenticated_host_snapshot(input_auth)
                    current = await trusted_disclosure.current_record_tx(db, subject)
                    if (
                        current is None
                        or disclosure_binding_ref != current["binding_ref"]
                        or admitted_evidence_pair is None
                        or task_scope_id is not None
                    ):
                        raise ForegroundQueueError("foreground_input_configuration_unavailable")
                    input_use = build_input_use(
                        auth=input_auth,
                        envelope=envelope,
                        receipt=receipt,
                        primary_ref=primary_conversation_id,
                        delivery_key=idempotency_key,
                        config=current,
                        declared=input_declaration,
                    )
                    if existing_request is not None:
                        original = existing_request.get("input_use")
                        if original is None:
                            raise ForegroundQueueError("foreground_turn_idempotency_conflict")
                        validate_input_use(
                            original,
                            envelope=envelope,
                            receipt=receipt,
                            primary_ref=primary_conversation_id,
                            delivery_key=idempotency_key,
                            config=current,
                        )
                        # A verified reconnect preserves the original admission lease.
                        comparable = dict(input_use)
                        comparable["control"] = dict(
                            input_use["control"], lease_ref=original["control"]["lease_ref"]
                        )
                        if comparable != original:
                            raise ForegroundQueueError("foreground_turn_idempotency_conflict")
                        input_use = original
                    request["input_use"] = input_use
                token = await trusted_disclosure.enqueue_binding_tx(
                    db,
                    subject=subject,
                    requested_ref=disclosure_binding_ref,
                    legacy=existing_request is not None
                    and "disclosure_binding" not in existing_request,
                    input_use=input_use,
                )
                if token is not None:
                    request["disclosure_binding"] = token
                turn_hash = canonical_hash(request)
                if existing is not None:
                    # Replay the actual persisted format, never upgrade an old
                    # split-admission row into a new atomic-origin assertion.
                    existing_request = json.loads(str(existing["turn_json"]))
                    if existing_request.get("schema_version") in (2, 3):
                        request["schema_version"] = existing_request["schema_version"]
                        request["source_admission"] = "atomic-evidence-and-turn/v1"
                        turn_hash = canonical_hash(request)
                    if (
                        existing["turn_hash"] != turn_hash
                        or canonical_hash(existing_request) != turn_hash
                    ):
                        raise ForegroundQueueError("foreground_turn_idempotency_conflict")
                    if admitted_evidence_pair is not None:
                        # Even a replay must validate the complete supplied pair;
                        # an existing turn cannot launder a forged S1 receipt.
                        await HumanMemoryProgramStore(self._db_path).append_evidence_tx(
                            db,
                            envelope,
                            receipt,
                            primary_conversation_id=primary_conversation_id,
                            committed_at=now,
                        )
                    await db.commit()
                    return self._enqueue_receipt(existing)
                if admitted_evidence_pair is not None:
                    # Only a real insertion in THIS transaction can establish
                    # atomic source order. Already admitted evidence stays legacy.
                    prior_source = await self._fetchone(
                        db,
                        "SELECT evidence_id FROM human_memory_evidence WHERE evidence_id=?",
                        (evidence_id,),
                    )
                    if input_use is not None and prior_source is not None:
                        raise ForegroundQueueError("foreground_input_source_not_new")
                    await HumanMemoryProgramStore(self._db_path).append_evidence_tx(
                        db,
                        envelope,
                        receipt,
                        primary_conversation_id=primary_conversation_id,
                        committed_at=now,
                    )
                    self._fault("enqueue.after_evidence_insert")
                    if prior_source is None:
                        request["schema_version"] = 3 if input_use is not None else 2
                        request["source_admission"] = "atomic-evidence-and-turn/v1"
                        turn_hash = canonical_hash(request)
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
                row = await self._fetchone(
                    db, "SELECT * FROM foreground_turns WHERE turn_id=?", (turn_id,)
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("enqueue.after_commit")
        assert row is not None
        return self._enqueue_receipt(row)

    async def read_next_preparation_candidate(self, subject: str) -> PreparationCandidate | None:
        """Read the exact oldest turn without minting any execution authority."""

        subject = identifier(subject, "subject", 512)
        await self.initialize()
        async with self._connection() as db:
            return await self._preparation_candidate_tx(db, subject)

    async def prepare_candidate(
        self,
        *,
        subject: str,
        expected_candidate_hash: str,
        context: ContextLineage,
        idempotency_key: str,
    ) -> PreparationDraftReceipt:
        """Persist an inert pre-claim draft for the current oldest candidate."""

        subject = identifier(subject, "subject", 512)
        expected_candidate_hash = digest(expected_candidate_hash, "expected_candidate_hash")
        context = self._validate_context(context)
        idempotency_key = identifier(idempotency_key, "idempotency_key", 512)
        now = _clock_value(self._clock)
        await self.initialize()
        async with self._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_preparation_drafts "
                    "WHERE subject=? AND idempotency_key=?",
                    (subject, idempotency_key),
                )
                if existing is not None:
                    if (
                        str(existing["candidate_hash"]) != expected_candidate_hash
                        or str(existing["context_snapshot_id"]) != context.context_snapshot_id
                        or int(existing["context_snapshot_revision"])
                        != context.context_snapshot_revision
                        or str(existing["context_snapshot_hash"]) != context.context_snapshot_hash
                    ):
                        raise ForegroundQueueError("foreground_preparation_idempotency_conflict")
                    await db.commit()
                    return self._preparation_draft(existing)
                candidate = await self._preparation_candidate_tx(db, subject)
                if candidate is None:
                    raise ForegroundQueueError("foreground_preparation_candidate_missing")
                if candidate.candidate_hash != expected_candidate_hash:
                    raise ForegroundQueueError("foreground_preparation_candidate_stale")
                draft_id = _uuid(f"foreground-preparation:{subject}:{idempotency_key}")
                payload = {
                    "schema_version": 1,
                    "draft_id": draft_id,
                    "subject": subject,
                    "turn_id": candidate.turn_id,
                    "turn_hash": candidate.turn_hash,
                    "candidate_hash": candidate.candidate_hash,
                    "context_snapshot_id": context.context_snapshot_id,
                    "context_snapshot_revision": context.context_snapshot_revision,
                    "context_snapshot_hash": context.context_snapshot_hash,
                    "idempotency_key": idempotency_key,
                }
                draft_hash = canonical_hash(payload)
                await db.execute(
                    "INSERT INTO foreground_preparation_drafts("
                    "draft_id,subject,turn_id,turn_hash,candidate_hash,"
                    "context_snapshot_id,context_snapshot_revision,context_snapshot_hash,"
                    "idempotency_key,draft_hash,candidate_json,draft_json,prepared_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        draft_id,
                        subject,
                        candidate.turn_id,
                        candidate.turn_hash,
                        candidate.candidate_hash,
                        context.context_snapshot_id,
                        context.context_snapshot_revision,
                        context.context_snapshot_hash,
                        idempotency_key,
                        draft_hash,
                        candidate.candidate_json,
                        canonical_json(payload),
                        now,
                    ),
                )
                self._fault("prepare.before_commit")
                row = await self._fetchone(
                    db,
                    "SELECT * FROM foreground_preparation_drafts WHERE draft_id=?",
                    (draft_id,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        self._fault("prepare.after_commit")
        assert row is not None
        return self._preparation_draft(row)
