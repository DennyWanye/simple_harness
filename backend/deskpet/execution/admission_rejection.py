"""Verified Host-only terminal disposition of an unclaimed stale-policy turn."""
import json

from deskpet.memory.trusted_disclosure import bound_record_tx, binding_token, current_record_tx
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_hash, canonical_json, digest


async def _facts(db, turn):
    body = json.loads(turn["turn_json"])
    if canonical_hash(body) != turn["turn_hash"] or any(body.get(key) != turn[key] for key in (
            "subject", "primary_conversation_id", "task_scope_id", "evidence_id", "evidence_hash", "idempotency_key")):
        raise ValueError("foreground_rejection_turn_corrupt")
    token = body.get("disclosure_binding")
    if "disclosure_binding" in body:
        await bound_record_tx(db, subject=turn["subject"], token=token)
    return token


async def read_admission_rejection_tx(db, *, turn_id, subject):
    cursor = await db.execute("SELECT * FROM foreground_admission_rejections WHERE turn_id=?", (turn_id,))
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    async with db.execute("SELECT t.*,h.current_state,h.host_run_id FROM foreground_turns t "
                          "JOIN foreground_turn_heads h ON h.turn_id=t.turn_id WHERE t.turn_id=?", (turn_id,)) as cur:
        turn = await cur.fetchone()
    raw = json.loads(row["rejection_json"])
    digest(raw.get("candidate_hash"), "candidate_hash")
    keys = {"schema", "turn_id", "subject", "turn_hash", "candidate_hash", "bound_token",
            "observed_current_token", "reason", "recorded_at"}
    if (set(raw) != keys or raw["schema"] != "foreground-admission-rejection/v1"
            or row["subject"] != subject or turn["subject"] != subject
            or raw["turn_id"] != turn_id or raw["subject"] != subject
            or raw["turn_hash"] != turn["turn_hash"] or canonical_hash(raw) != row["rejection_hash"]
            or turn["current_state"] != "QUEUED" or turn["host_run_id"] is not None):
        raise ValueError("foreground_admission_rejection_corrupt")
    token = await _facts(db, turn)
    observed = await bound_record_tx(db, subject=subject, token=raw["observed_current_token"])
    if (token != raw["bound_token"] or (token is not None and (
            token == raw["observed_current_token"] or observed["policy_generation"] <= token["policy_generation"]))
            or raw["reason"] != ("host_disclosure_legacy_policy_changed" if token is None else "host_disclosure_binding_stale")
            or (token is None and observed["source_origin"] != "authenticated_control")):
        raise ValueError("foreground_admission_rejection_corrupt")
    async with db.execute("SELECT 1 FROM foreground_runs WHERE turn_id=?", (turn_id,)) as cur:
        if await cur.fetchone() is not None:
            raise ValueError("foreground_admission_rejection_run_conflict")
    # Recorded head is an immutable observation, never replaced by today's head.
    return raw


async def reject_stale_candidate(store, candidate):
    """Re-prove staleness under writer lock; never settle on exception text alone."""
    from deskpet.execution.foreground_queue import ForegroundQueueError, _clock_value
    async with store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await assert_human_memory_ingress_open_tx(db)
            existing = await read_admission_rejection_tx(db, turn_id=candidate.turn_id, subject=candidate.subject)
            if existing is not None:
                if existing["candidate_hash"] != candidate.candidate_hash:
                    raise ForegroundQueueError("foreground_preparation_candidate_stale")
                await db.commit()
                return existing
            current_candidate = await store._preparation_candidate_tx(db, candidate.subject)
            if current_candidate != candidate:
                raise ForegroundQueueError("foreground_preparation_candidate_stale")
            turn = await store._fetchone(db, "SELECT * FROM foreground_turns WHERE turn_id=?", (candidate.turn_id,))
            token = await _facts(db, turn)
            current = await current_record_tx(db, candidate.subject)
            if (current is None or (token is not None and token == binding_token(current))
                    or (token is None and current["source_origin"] != "authenticated_control")):
                raise ForegroundQueueError("foreground_admission_rejection_not_proven")
            raw = {"schema": "foreground-admission-rejection/v1", "turn_id": candidate.turn_id,
                   "subject": candidate.subject, "turn_hash": candidate.turn_hash,
                   "candidate_hash": candidate.candidate_hash, "bound_token": token,
                   "observed_current_token": binding_token(current),
                   "reason": "host_disclosure_legacy_policy_changed" if token is None else "host_disclosure_binding_stale",
                   "recorded_at": _clock_value(store._clock)}
            await db.execute("INSERT INTO foreground_admission_rejections VALUES (?,?,?,?)",
                (candidate.turn_id, candidate.subject, canonical_hash(raw), canonical_json(raw)))
            await read_admission_rejection_tx(db, turn_id=candidate.turn_id, subject=candidate.subject)
            store._fault("admission_rejection.before_commit")
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    store._fault("admission_rejection.after_commit")
    return raw
