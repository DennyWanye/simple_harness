"""New-terminal-only per-message S1 production in the observer's transaction.

The caller MUST invoke this only after appending a new terminal observation,
never in its existing-observation/replay branch. Existing terminal evidence is
not backfilled. All writes share the caller's terminal transaction and fence.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import simple_harness as h

from deskpet.execution.primary_history import terminal_observation_tx
from deskpet.memory.primary_visibility import _dependencies, read_evidence_pair
from deskpet.task_scope.protocol import canonical_hash


@dataclass(frozen=True)
class PrimaryMessageProduction:
    evidence_ids: tuple[str, ...]
    blocked_reason: str | None = None


def message_evidence_id(sdk_run_id: str, ordinal: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"primary-message-v1:{sdk_run_id}:{ordinal}"))


def assistant_message_pair(terminal, terminal_receipt, user, *, host_run_id):
    """Deterministic S1 bytes for the actual new terminal's one assistant item.

    Also used to VERIFY persisted source bytes; derivation alone is never an
    admission and cannot make a legacy terminal eligible for registration.
    """
    terminal_receipt.verify(terminal)
    payload = terminal.to_json()["sanitized_payload"]
    messages = payload["messages"]
    source = {
        "terminal_evidence_id": terminal.evidence_id,
        "terminal_envelope_hash": terminal.envelope_hash,
        "sdk_event_id": payload["sdk_event_id"], "sdk_event_hash": payload["sdk_event_hash"],
        "terminal_json_pointer": "/messages/1", "message": messages[1],
    }
    body = {"schema_version": 1, "kind": "primary_message", "host_run_id": host_run_id,
            "sdk_run_id": terminal.run_id, "transcript_ordinal": 2, "source": source}
    refs = (h.EvidenceRef(user.evidence_id, user.envelope_hash, 1),
            h.EvidenceRef(terminal.evidence_id, terminal.envelope_hash, 2))
    evidence_id = message_evidence_id(terminal.run_id, 2)
    envelope = h.SanitizedEvidenceEnvelope(
        evidence_id=evidence_id, run_id=terminal.run_id, subject=terminal.subject,
        source_kind=h.EvidenceSourceKind.ASSISTANT_MESSAGE,
        source_ref=f"primary-message:{terminal.run_id}:2", source_hash=canonical_hash(source),
        sanitized_payload=body, sanitized_hash=canonical_hash(body),
        filter_policy_version=terminal.filter_policy_version, removed_spans=(),
        disclosure_context=terminal.disclosure_context, evidence_refs=refs,
    )
    receipt = h.SanitizedEvidenceReceipt(
        receipt_id=f"primary-message-receipt:{evidence_id}", run_id=envelope.run_id,
        subject=envelope.subject, evidence_id=evidence_id, envelope_hash=envelope.envelope_hash,
        source_hash=envelope.source_hash, sanitized_hash=envelope.sanitized_hash,
        filter_policy_version=envelope.filter_policy_version, accepted=True,
        reason_codes=(h.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=envelope.disclosure_context, evidence_refs=refs,
        admitted_at=terminal_receipt.admitted_at,
    )
    return envelope, receipt


async def _prepare_message_tx(
    db, *, host_run_id: str, terminal_envelope, terminal_receipt,
):
    """Append new public-message sources; do not commit the caller's transaction.

    Call only in record_terminal_observation's *new* row path, after the exact
    terminal S1 append and before COMMIT. Its existing-observation branch must
    return without calling this function. No work is queued for legacy rows.
    """
    if not db.in_transaction:
        raise RuntimeError("primary_message_requires_terminal_transaction")
    subject, sdk_run_id = terminal_envelope.subject, terminal_envelope.run_id
    found = await terminal_observation_tx(db, host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject)
    if found is None:
        raise RuntimeError("primary_message_terminal_unavailable")
    row, payload = found
    original, admission = await read_evidence_pair(db=db, subject=subject,
        primary_ref=row["primary_conversation_id"], evidence_id=terminal_envelope.evidence_id)
    if original != terminal_envelope or admission != terminal_receipt:
        raise RuntimeError("primary_message_terminal_mismatch")
    cursor = await db.execute(
        "SELECT t.evidence_id,t.evidence_hash FROM foreground_runs r "
        "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject "
        "AND t.primary_conversation_id=r.primary_conversation_id WHERE r.host_run_id=? AND r.subject=?",
        (host_run_id, subject),
    )
    turn = await cursor.fetchone()
    if turn is None:
        raise RuntimeError("primary_message_user_unavailable")
    user, _ = await read_evidence_pair(db=db, subject=subject,
        primary_ref=row["primary_conversation_id"], evidence_id=turn["evidence_id"])
    if user.envelope_hash != turn["evidence_hash"]:
        raise RuntimeError("primary_message_user_mismatch")
    messages = payload["messages"]
    if payload["terminal_state"] != "COMPLETED":
        return PrimaryMessageProduction((), "conversation_group_not_complete")
    if payload.get("message_source_contract") == "primary-message-v2":
        from deskpet.memory.primary_message_v2 import pairs
        try:
            dependencies, _ = _dependencies(payload.get("visibility_dependencies"))
        except ValueError:
            return PrimaryMessageProduction((), "conversation_terminal_proof_missing")
        if dependencies.get(user.evidence_id) != user.envelope_hash:
            return PrimaryMessageProduction((), "conversation_input_dependency_unproved")
        return row, user, pairs(original, admission, user, host_run_id=host_run_id), None
    if not isinstance(messages, list) or len(messages) != 2:
        return PrimaryMessageProduction((), "terminal_multiple_items_not_representable")
    if (messages[0] != {"role": "user", "content": user.sanitized_payload.get("text")}
            or set(messages[1]) != {"role", "content"} or messages[1]["role"] != "assistant"
            or not all(isinstance(m["content"], str) and m["content"] for m in messages)):
        return PrimaryMessageProduction((), "conversation_transcript_not_representable")
    try:
        dependencies, _ = _dependencies(payload.get("visibility_dependencies"))
    except ValueError:
        return PrimaryMessageProduction((), "conversation_terminal_proof_missing")
    if dependencies.get(user.evidence_id) != user.envelope_hash:
        return PrimaryMessageProduction((), "conversation_input_dependency_unproved")
    message, receipt = assistant_message_pair(original, admission, user, host_run_id=host_run_id)
    return row, user, message, receipt


async def append_new_primary_message_evidence_tx(
    db, *, store, host_run_id: str, terminal_envelope, terminal_receipt,
) -> PrimaryMessageProduction:
    """Only new observer branch: append under its transaction, never commit."""
    prepared = await _prepare_message_tx(db, host_run_id=host_run_id,
        terminal_envelope=terminal_envelope, terminal_receipt=terminal_receipt)
    if isinstance(prepared, PrimaryMessageProduction):
        return prepared
    row, user, message, receipt = prepared
    sources = message if receipt is None else ((message, receipt),)
    for envelope, admission in sources:
        await store.append_evidence_tx(db, envelope, admission, primary_conversation_id=row["primary_conversation_id"],
            committed_at=float(row["committed_at"]))
    return PrimaryMessageProduction((user.evidence_id, *(source.evidence_id for source, _ in sources)))


async def verify_new_primary_message_evidence_tx(
    db, *, host_run_id: str, terminal_envelope, terminal_receipt,
) -> PrimaryMessageProduction:
    """For marked new-producer observation replay: read-only, missing is corruption.

    Legacy observations have no producer marker and the caller must NOT call
    this function for them. Unsupported groups return their original reason.
    """
    prepared = await _prepare_message_tx(db, host_run_id=host_run_id,
        terminal_envelope=terminal_envelope, terminal_receipt=terminal_receipt)
    if isinstance(prepared, PrimaryMessageProduction):
        return prepared
    row, user, _, _ = prepared
    if terminal_envelope.sanitized_payload.get("message_source_contract") == "primary-message-v2":
        from deskpet.memory.primary_message_v2 import verify
        found = await verify(db, primary_ref=row["primary_conversation_id"], host_run_id=host_run_id,
                             terminal=terminal_envelope, terminal_receipt=terminal_receipt, user=user)
        return PrimaryMessageProduction((user.evidence_id, *(source.evidence_id for source, _ in found)))
    found = await verify_primary_message_evidence_tx(db, primary_ref=row["primary_conversation_id"],
        host_run_id=host_run_id, terminal_envelope=terminal_envelope, terminal_receipt=terminal_receipt, user=user)
    if found is None:
        raise RuntimeError("primary_message_source_missing")
    return PrimaryMessageProduction((user.evidence_id, found[0].evidence_id))


async def verify_primary_message_evidence_tx(db, *, primary_ref, host_run_id, terminal_envelope, terminal_receipt, user):
    """Read-only exact replay check; None means legacy/unproduced, never repair.

    Caller has already checked the completed transcript shape and original
    terminal/USER facts. A present but altered child is corruption, not absence.
    """
    from deskpet.memory.primary_visibility import PrimaryVisibilityError
    expected, expected_receipt = assistant_message_pair(terminal_envelope, terminal_receipt, user,
        host_run_id=host_run_id)
    try:
        actual, receipt = await read_evidence_pair(db=db, subject=user.subject, primary_ref=primary_ref,
            evidence_id=expected.evidence_id)
    except PrimaryVisibilityError as exc:
        if exc.code == "primary_source_missing":
            return None
        raise
    if actual != expected or receipt != expected_receipt:
        raise RuntimeError("primary_message_source_corrupt")
    return actual, receipt
