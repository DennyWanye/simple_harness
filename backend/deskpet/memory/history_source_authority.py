"""Read-only original Host source order and durable first-action cutoff facts.

No Memory database is read here. Source equality and suppression decisions belong
to the SDK; this authority supplies only exact Host admission facts.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.primary_visibility import read_evidence_pair
from deskpet.task_scope.protocol import canonical_hash


class HostHistorySourceError(ValueError):
    code = "host_history_source_unverifiable"


async def history_namespace_tx(db, subject):
    """Recompute the existing immutable initialization receipt, not a new epoch."""
    row = await (await db.execute(
        "SELECT i.*,p.writable FROM human_memory_init_receipts i "
        "JOIN human_memory_primary_conversations p "
        "ON p.primary_conversation_id=i.primary_conversation_id AND p.subject=i.subject "
        "WHERE i.subject=?", (subject,),
    )).fetchone()
    if row is None or row["writable"] != 1:
        raise HostHistorySourceError("host_history_primary_unverifiable")
    marker = await HumanMemoryProgramStore._marker_tx(db)
    payload = {key: row[key] for key in (
        "subject", "primary_conversation_id", "format_epoch", "marker_sha256", "created_at",
    )}
    if (
        canonical_hash(marker) != row["marker_sha256"]
        or canonical_hash(payload) != row["receipt_sha256"]
        or row["format_epoch"] != "human-memory-v1"
    ):
        raise HostHistorySourceError("host_history_epoch_unverifiable")
    return {
        "store_epoch": row["receipt_sha256"], "subject": subject,
        "source_stream": f"primary:{row['primary_conversation_id']}:foreground_turns",
    }, row["primary_conversation_id"]


class HostHistorySourceAuthority:
    def __init__(self, path):
        self._path = Path(path)

    async def resolve_history_source(self, *, principal, envelope, receipt):
        from simple_harness_memory import (
            HistorySourceNamespace,
            HistorySourceOriginReceipt,
        )

        if principal.actor_id != envelope.subject:
            raise HostHistorySourceError("host_history_subject_mismatch")
        async with aiosqlite.connect(f"file:{self._path}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            namespace, primary = await history_namespace_tx(db, principal.actor_id)
            actual, admitted = await read_evidence_pair(
                db=db, subject=principal.actor_id, primary_ref=primary,
                evidence_id=envelope.evidence_id,
            )
            if actual.to_json() != envelope.to_json() or admitted.to_json() != receipt.to_json():
                raise HostHistorySourceError("host_history_pair_mismatch")
            payload = dict(actual.sanitized_payload)
            if (
                actual.source_kind.value != "user_message"
                or actual.filter_policy_version != "host-public-turn/v1"
                or set(payload) != {"schema_version", "delivery_key", "text"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or not isinstance(payload["text"], str)
                or actual.source_ref != f"foreground-turn:{payload['delivery_key']}"
            ):
                return None
            rows = await (await db.execute(
                "SELECT * FROM foreground_turns WHERE evidence_id=? AND subject=? "
                "AND primary_conversation_id=? LIMIT 2",
                (actual.evidence_id, principal.actor_id, primary),
            )).fetchall()
            if not rows:
                return None
            if len(rows) != 1:
                raise HostHistorySourceError("host_history_source_ambiguous")
            row = rows[0]
            body = json.loads(row["turn_json"])
            expected = {
                "schema_version": 1, "subject": principal.actor_id,
                "primary_conversation_id": primary, "task_scope_id": row["task_scope_id"],
                "evidence_id": actual.evidence_id, "evidence_hash": actual.envelope_hash,
                "idempotency_key": payload["delivery_key"], "payload": payload,
            }
            proof_kind = "legacy_before_only"
            if type(body.get("schema_version")) is int and body["schema_version"] in (2, 3):
                expected.update(schema_version=body["schema_version"], source_admission="atomic-evidence-and-turn/v1")
                proof_kind = "atomic"
            if "disclosure_binding" in body:
                from deskpet.memory.trusted_disclosure import bound_record_tx

                # The exact bound configuration is a durable admission fact.
                # Its head may since have changed; visibility checks that head
                # separately and must not rewrite historical source ordering.
                try:
                    original_config = await bound_record_tx(db, subject=principal.actor_id, token=body["disclosure_binding"])
                except (ValueError, TypeError, KeyError, RuntimeError) as exc:
                    raise HostHistorySourceError("host_history_turn_binding_mismatch") from exc
                expected["disclosure_binding"] = body["disclosure_binding"]
                if body.get("schema_version") == 3:
                    from deskpet.memory.current_input_source import read_input_use_tx
                    # Origin is immutable: use its ORIGINAL config, not the current head.
                    await read_input_use_tx(db, turn=row, config=original_config)
                    expected["input_use"] = body["input_use"]
            if (
                body != expected or canonical_hash(expected) != row["turn_hash"]
                or row["evidence_hash"] != actual.envelope_hash
                or row["idempotency_key"] != payload["delivery_key"]
            ):
                raise HostHistorySourceError("host_history_turn_binding_mismatch")
            return HistorySourceOriginReceipt(
                namespace=HistorySourceNamespace.from_json(namespace),
                source_sequence=row["enqueue_sequence"], evidence_id=actual.evidence_id,
                envelope_hash=actual.envelope_hash, admission_receipt_id=admitted.receipt_id,
                admission_receipt_hash=admitted.receipt_hash, proof_kind=proof_kind,
            )

    async def resolve_history_forget_cut(self, *, principal, decision):
        from simple_harness_memory import (
            HistoryForgetCutReceipt,
            HistorySourceNamespace,
        )

        if decision.subject != principal.actor_id:
            raise HostHistorySourceError("host_history_subject_mismatch")
        if decision.scope_kind.value != "memory" or not decision.request_id.startswith("primary-forget:"):
            return None
        evidence_id = decision.request_id.removeprefix("primary-forget:")
        async with aiosqlite.connect(f"file:{self._path}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            namespace, primary = await history_namespace_tx(db, principal.actor_id)
            envelope, _ = await read_evidence_pair(
                db=db, subject=principal.actor_id, primary_ref=primary, evidence_id=evidence_id,
            )
            payload = envelope.to_json()["sanitized_payload"]
            row = await (await db.execute(
                "SELECT committed_at FROM human_memory_evidence WHERE evidence_id=?", (evidence_id,),
            )).fetchone()
            if (
                envelope.filter_policy_version != "host-typed-ingress/v1"
                or not envelope.source_ref.startswith("host-cognitive-action/forget/v1:")
                or decision.reason_code != "user_forget"
                or payload.get("memory_id") != decision.scope_ref
                or decision.effective_at != row["committed_at"]
            ):
                raise HostHistorySourceError("host_history_action_mismatch")
            if payload.get("schema") == "primary-memory-forget/v1":
                return None  # An old action has no provable original cutoff.
            if (
                payload.get("schema") != "primary-memory-forget/v2"
                or set(payload) != {"schema", "memory_id", "expected_revision", "expected_content_hash", "history_cut"}
            ):
                raise HostHistorySourceError("host_history_action_schema_unverifiable")
            cut = payload["history_cut"]
            if not isinstance(cut, dict) or set(cut) != {"namespace", "through_sequence"} or cut["namespace"] != namespace:
                raise HostHistorySourceError("host_history_cut_binding_mismatch")
            return HistoryForgetCutReceipt(
                namespace=HistorySourceNamespace.from_json(namespace),
                through_sequence=cut["through_sequence"], request_id=decision.request_id,
                scope_kind=decision.scope_kind, scope_ref=decision.scope_ref,
                action_ref=evidence_id, action_hash=envelope.envelope_hash,
            )
