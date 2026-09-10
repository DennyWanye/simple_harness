"""A factual current-USER rejection before any SDK start; no fake SDK terminal."""

from __future__ import annotations

import json
from dataclasses import dataclass

from deskpet.task_scope.protocol import (
    canonical_hash,
    canonical_json,
    digest,
    identifier,
)

REASONS = frozenset({"history_suppressed", "history_source_cut_unverifiable"})


@dataclass(frozen=True, slots=True)
class PreparationRejection:
    host_run_id: str
    subject: str
    turn_id: str
    turn_hash: str
    evidence_id: str
    evidence_hash: str
    candidate_hash: str
    binding_hash: str
    snapshot_hash: str
    snapshot_json: str
    reason: str

    def __post_init__(self):
        for name in ("host_run_id", "subject", "turn_id", "evidence_id"):
            identifier(getattr(self, name), name, 512)
        for name in ("turn_hash", "evidence_hash", "candidate_hash", "binding_hash", "snapshot_hash"):
            digest(getattr(self, name), name)
        if self.reason not in REASONS:
            raise ValueError("preparation_rejection_reason_invalid")
        snapshot = json.loads(self.snapshot_json)
        if (
            snapshot.get("subject") != self.subject
            or canonical_hash({"domain": "memory.history.visibility.snapshot.v1", "payload": snapshot}) != self.snapshot_hash
            or not any(item == {"binding_hash": self.binding_hash, "visible": False, "reason": self.reason}
                       for item in snapshot.get("items", ()))
        ):
            raise ValueError("preparation_rejection_snapshot_invalid")

    def to_json(self):
        return {
            "schema": "foreground-preparation-rejection/v1", "phase": "context_prepare",
            **{name: getattr(self, name) for name in (
                "host_run_id", "subject", "turn_id", "turn_hash", "evidence_id", "evidence_hash",
                "candidate_hash", "binding_hash", "snapshot_hash", "reason",
            )},
            "visibility_snapshot": json.loads(self.snapshot_json),
        }

    @property
    def rejection_ref(self):
        return "foreground-preparation-rejection:" + self.host_run_id

    @property
    def rejection_hash(self):
        return canonical_hash(self.to_json())

    @classmethod
    def from_json(cls, value):
        if not isinstance(value, dict) or value.get("schema") != "foreground-preparation-rejection/v1" or value.get("phase") != "context_prepare":
            raise ValueError("preparation_rejection_schema_invalid")
        data = dict(value)
        data.pop("schema")
        data.pop("phase")
        data["snapshot_json"] = canonical_json(data.pop("visibility_snapshot"))
        return cls(**data)


class PreparationDisclosureRejected(RuntimeError):
    code = "foreground_current_user_not_disclosable"

    def __init__(self, rejection: PreparationRejection):
        self.rejection = rejection
        super().__init__(self.code)


async def _verify_source_binding_tx(db, rejection, primary_ref):
    """Re-prove a recorded rejection's source against the live S1 bytes.

    2026-09-10：``binding_hash`` 原本是记忆 SDK 的 ``HistoryEvidenceBinding``
    公开承诺；SDK 移除后 Host 无法（也不应假装能）重算它，所以这里只保留仍然
    成立的两条 Host 判据：来源种类必须是 ``user_message``，envelope 承诺必须与
    记录一致。``binding_hash`` 只做存在性/形状检查。

    注意本构建下 ``PrimaryHistoryPolicy.current_user_denial`` 恒为 ``None``，
    不会再产生新的拒绝记录，本函数只服务于旧 userdata 里的历史行。
    """

    from deskpet.memory.primary_visibility import read_evidence_pair

    envelope, _receipt = await read_evidence_pair(
        db=db, subject=rejection.subject, primary_ref=primary_ref, evidence_id=rejection.evidence_id,
    )
    if (envelope.source_kind.value != "user_message" or envelope.envelope_hash != rejection.evidence_hash
            or not isinstance(rejection.binding_hash, str) or not rejection.binding_hash):
        raise ValueError("preparation_rejection_source_binding_mismatch")


async def read_preparation_rejection_tx(db, *, host_run_id, subject, primary_ref):
    """Verify this distinct Host terminal variant without inventing an SDK event."""
    row = await (await db.execute(
        "SELECT x.*,h.last_transition_hash,h.current_state,h.sdk_run_id,"
        "h.owner_id AS current_owner,h.generation AS head_generation,h.lease_expires_at,t.evidence_id,t.evidence_hash,"
        "t.turn_id,t.turn_hash,q.current_state AS turn_state,p.candidate_hash "
        "FROM foreground_run_heads h JOIN foreground_run_transitions x "
        "ON x.transition_id=h.last_transition_id AND x.host_run_id=h.host_run_id "
        "JOIN foreground_turns t ON t.turn_id=h.turn_id "
        "JOIN foreground_turn_heads q ON q.turn_id=t.turn_id "
        "JOIN foreground_run_preparation_bindings p ON p.host_run_id=h.host_run_id "
        "WHERE h.host_run_id=? AND h.subject=? AND h.primary_conversation_id=? "
        "AND x.idempotency_key='preparation-rejected:v1'",
        (host_run_id, subject, primary_ref),
    )).fetchone()
    if row is None:
        return None
    body = json.loads(row["transition_json"])
    rejection = PreparationRejection.from_json(body.get("preparation_rejection"))
    if (
        row["current_state"] != "FAILED" or row["turn_state"] != "SETTLED"
        or row["sdk_run_id"] is not None or row["current_owner"] is not None
        or row["lease_expires_at"] is not None or row["sdk_event_id"] is not None
        or canonical_hash(body) != row["transition_hash"]
        or row["last_transition_hash"] != row["transition_hash"]
        or row["head_generation"] != row["generation"]
        or any(body.get(key) != row[key] for key in (
            "transition_id", "host_run_id", "subject", "from_state", "to_state",
            "generation", "owner_id", "sdk_event_id", "idempotency_key",
            "causal_evidence_ref", "causal_evidence_hash", "recorded_at",
        ))
        or body["from_state"] != "CLAIMED" or body["to_state"] != "FAILED"
        or body["causal_evidence_ref"] != rejection.rejection_ref
        or body["causal_evidence_hash"] != rejection.rejection_hash
        or (rejection.host_run_id, rejection.subject, rejection.turn_id, rejection.turn_hash,
            rejection.evidence_id, rejection.evidence_hash, rejection.candidate_hash) != (
            host_run_id, subject, row["turn_id"], row["turn_hash"],
            row["evidence_id"], row["evidence_hash"], row["candidate_hash"],
        )
    ):
        raise ValueError("preparation_rejection_receipt_mismatch")
    started = await (await db.execute(
        "SELECT host_run_id FROM foreground_run_sdk_bindings WHERE host_run_id=? UNION "
        "SELECT host_run_id FROM foreground_execution_start_intents WHERE host_run_id=?",
        (host_run_id, host_run_id),
    )).fetchone()
    if started is not None:
        raise ValueError("preparation_rejection_start_conflict")
    await _verify_source_binding_tx(db, rejection, primary_ref)
    return body


async def settle_preparation_rejection(store, *, rejection, owner_id, generation):
    from deskpet.execution.foreground_queue import ForegroundQueueError, _clock_value
    from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx

    if type(rejection) is not PreparationRejection:
        raise TypeError("preparation rejection must use the Host typed receipt")
    identifier(owner_id, "owner_id", 512)
    if type(generation) is not int or generation < 1:
        raise ForegroundQueueError("foreground_generation_stale")
    key = "preparation-rejected:v1"
    now = _clock_value(store._clock)
    async with store._connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        await assert_human_memory_ingress_open_tx(db)
        try:
            existing = await store._fetchone(
                db, "SELECT * FROM foreground_run_transitions WHERE host_run_id=? AND idempotency_key=?",
                (rejection.host_run_id, key),
            )
            if existing is not None:
                body = json.loads(existing["transition_json"])
                if (
                    body.get("preparation_rejection") != rejection.to_json()
                    or body.get("owner_id") != owner_id or body.get("generation") != generation
                    or canonical_hash(body) != existing["transition_hash"]
                ):
                    raise ForegroundQueueError("foreground_preparation_rejection_conflict")
                head = await store._head_tx(db, rejection.host_run_id)
                verified = await read_preparation_rejection_tx(
                    db, host_run_id=rejection.host_run_id, subject=rejection.subject,
                    primary_ref=head["primary_conversation_id"],
                )
                if verified != body:
                    raise ForegroundQueueError("foreground_preparation_rejection_conflict")
                await db.commit()
                return body
            head = await store._validate_lease_tx(db, rejection.host_run_id, owner_id, generation, now)
            admitted = await store._run_tx(db, rejection.host_run_id)
            proof = await store._fetchone(
                db, "SELECT b.candidate_hash,t.evidence_id,t.evidence_hash,t.turn_hash "
                "FROM foreground_run_preparation_bindings b JOIN foreground_runs r ON r.host_run_id=b.host_run_id "
                "JOIN foreground_turns t ON t.turn_id=r.turn_id WHERE b.host_run_id=?",
                (rejection.host_run_id,),
            )
            started = await store._fetchone(
                db, "SELECT host_run_id FROM foreground_execution_start_intents WHERE host_run_id=? "
                "UNION SELECT host_run_id FROM foreground_run_sdk_bindings WHERE host_run_id=?",
                (rejection.host_run_id, rejection.host_run_id),
            )
            if head["current_state"] != "CLAIMED" or head["sdk_run_id"] is not None or started is not None:
                raise ForegroundQueueError("foreground_preparation_rejection_start_possible")
            if proof is None or (
                head["subject"], admitted["turn_id"], proof["candidate_hash"],
                proof["evidence_id"], proof["evidence_hash"], proof["turn_hash"],
            ) != (
                rejection.subject, rejection.turn_id, rejection.candidate_hash,
                rejection.evidence_id, rejection.evidence_hash, rejection.turn_hash,
            ):
                raise ForegroundQueueError("foreground_preparation_rejection_binding_mismatch")
            await _verify_source_binding_tx(db, rejection, head["primary_conversation_id"])
            transition_id, transition_hash = await store._insert_run_transition_tx(
                db, host_run_id=rejection.host_run_id, subject=rejection.subject,
                from_state="CLAIMED", to_state="FAILED", generation=generation,
                owner_id=owner_id, sdk_event_id=None, idempotency_key=key,
                causal_evidence_ref=rejection.rejection_ref, causal_evidence_hash=rejection.rejection_hash,
                recorded_at=now, preparation_rejection=rejection.to_json(),
            )
            await store._insert_lease_receipt_tx(
                db, host_run_id=rejection.host_run_id, owner_id=owner_id, generation=generation,
                prior_generation=generation, action="close", expires_at=None,
                idempotency_key=key, recorded_at=now,
            )
            await db.execute(
                "UPDATE foreground_run_heads SET current_state='FAILED',owner_id=NULL,lease_expires_at=NULL,"
                "last_transition_id=?,last_transition_hash=?,updated_at=? WHERE host_run_id=?",
                (transition_id, transition_hash, now, rejection.host_run_id),
            )
            tid, thash = await store._insert_turn_transition_tx(
                db, turn_id=rejection.turn_id, subject=rejection.subject,
                from_state="CLAIMED", to_state="SETTLED", host_run_id=rejection.host_run_id, recorded_at=now,
            )
            await db.execute(
                "UPDATE foreground_turn_heads SET current_state='SETTLED',last_transition_id=?,"
                "last_transition_hash=?,updated_at=? WHERE turn_id=? AND current_state='CLAIMED'",
                (tid, thash, now, rejection.turn_id),
            )
            store._fault("preparation_rejection.before_commit")
            row = await store._fetchone(db, "SELECT transition_json FROM foreground_run_transitions WHERE transition_id=?", (transition_id,))
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
    store._fault("preparation_rejection.after_commit")
    return json.loads(row["transition_json"])
