"""Derive short-index authority from immutable completed primary Host facts.

No Memory SQL, new evidence, timestamp refresh, or second registration ledger.
The frozen single-pointer SDK can represent exactly USER + one assistant item
from the new-terminal producer. Other complete transcripts stay archived, unindexed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
import simple_harness as h

from deskpet.execution.primary_history import terminal_observation_tx
from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
from deskpet.memory.primary_message_evidence import verify_primary_message_evidence_tx
from deskpet.memory.primary_visibility import _dependencies, read_evidence_pair
from deskpet.task_scope.protocol import canonical_hash, identifier

ISSUER = "host:primary-conversation/v1"
CLASSIFICATION = "host:classification/v1"


class ConversationRegistrationUnavailable(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ConversationGroup:
    host_run_id: str
    registrations: tuple[h.ConversationEvidenceRegistration, ...]
    user_analysis_lineage: object

    @property
    def references(self):
        return tuple(registration_ref(registration) for registration in self.registrations)


def registration_ref(registration):
    return h.ConversationEvidenceRegistrationRef(
        registration.registration_id, registration.registration_hash,
        registration.envelope.evidence_id, registration.envelope.envelope_hash,
    )


def _stable(kind, payload):
    return kind + ":" + canonical_hash({"domain": ISSUER + "/" + kind, "payload": payload})


class PrimaryConversationAuthority:
    def __init__(self, db_path: str | Path, *, subject: str, primary_ref: str):
        identifier(subject, "subject")
        identifier(primary_ref, "primary_ref")
        self.db_path, self.subject, self.primary_ref = Path(db_path), subject, primary_ref

    def _connect(self):
        return aiosqlite.connect(f"file:{self.db_path}?mode=ro", uri=True)

    async def completed_run_ids(self):
        async with self._connect() as db:
            cursor = await db.execute(
                "SELECT r.host_run_id FROM foreground_runs r "
                "JOIN foreground_turns t ON t.turn_id=r.turn_id "
                "JOIN foreground_terminal_receipts f ON f.host_run_id=r.host_run_id "
                "WHERE r.subject=? AND r.primary_conversation_id=? AND f.terminal_state='COMPLETED' "
                "ORDER BY t.enqueue_sequence", (self.subject, self.primary_ref),
            )
            return tuple(row[0] for row in await cursor.fetchall())

    async def registrations_for_run(self, host_run_id: str) -> ConversationGroup:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            return await self._group_tx(db, host_run_id)

    async def resolve_conversation_registration(self, reference):
        if type(reference) is not h.ConversationEvidenceRegistrationRef:
            raise TypeError("reference must use ConversationEvidenceRegistrationRef")
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            cursor = await db.execute(
                "SELECT DISTINCT r.host_run_id FROM foreground_runs r "
                "JOIN foreground_turns t ON t.turn_id=r.turn_id "
                "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
                "JOIN human_memory_evidence e ON (e.evidence_id=t.evidence_id OR e.run_id=b.sdk_run_id) "
                "WHERE r.subject=? AND r.primary_conversation_id=? AND e.evidence_id=?",
                (self.subject, self.primary_ref, reference.evidence_id),
            )
            rows = await cursor.fetchall()
            if len(rows) != 1:
                raise ConversationRegistrationUnavailable("conversation_reference_unknown")
            group = await self._group_tx(db, rows[0][0])
            for registration in group.registrations:
                if registration_ref(registration) == reference:
                    return registration
        raise ConversationRegistrationUnavailable("conversation_reference_mismatch")

    async def _group_tx(self, db, host_run_id):
        cursor = await db.execute(
            "SELECT r.*,t.enqueue_sequence,t.evidence_id,t.evidence_hash,t.turn_json,b.sdk_run_id "
            "FROM foreground_runs r JOIN foreground_turns t ON t.turn_id=r.turn_id "
            "AND t.subject=r.subject AND t.primary_conversation_id=r.primary_conversation_id "
            "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
            "WHERE r.host_run_id=? AND r.subject=? AND r.primary_conversation_id=?",
            (host_run_id, self.subject, self.primary_ref),
        )
        run = await cursor.fetchone()
        if run is None:
            raise ConversationRegistrationUnavailable("conversation_run_unavailable")
        identity = await read_primary_terminal_identity_tx(db, subject=self.subject,
            primary_ref=self.primary_ref, host_run_id=host_run_id, sdk_run_id=run["sdk_run_id"])
        if identity is None or identity.terminal_state != "COMPLETED":
            raise ConversationRegistrationUnavailable("conversation_group_not_complete")
        found = await terminal_observation_tx(db, host_run_id=host_run_id,
            sdk_run_id=run["sdk_run_id"], subject=self.subject)
        if found is None:
            raise ConversationRegistrationUnavailable("conversation_terminal_s1_missing")
        terminal_row, payload = found
        user, user_receipt = await read_evidence_pair(db=db, subject=self.subject,
            primary_ref=self.primary_ref, evidence_id=run["evidence_id"])
        terminal, terminal_receipt = await read_evidence_pair(db=db, subject=self.subject,
            primary_ref=self.primary_ref, evidence_id=terminal_row["evidence_id"])
        if user.envelope_hash != run["evidence_hash"] or user.source_kind.value != "user_message":
            raise ConversationRegistrationUnavailable("conversation_user_binding_mismatch")
        messages = payload["messages"]
        if not isinstance(messages, list) or len(messages) != 2:
            raise ConversationRegistrationUnavailable("terminal_multiple_items_not_representable")
        if (set(messages[0]) != {"role", "content"} or messages[0]["role"] != "user"
                or messages[0]["content"] != user.sanitized_payload.get("text")
                or messages[0]["content"] != json.loads(run["turn_json"])["payload"]["text"]
                or set(messages[1]) != {"role", "content"} or messages[1]["role"] != "assistant"
                or not all(isinstance(m["content"], str) and m["content"] for m in messages)):
            raise ConversationRegistrationUnavailable("conversation_transcript_not_representable")
        dependencies, _ = _dependencies(payload.get("visibility_dependencies"))
        if dependencies.get(user.evidence_id) != user.envelope_hash:
            raise ConversationRegistrationUnavailable("conversation_input_dependency_unproved")
        # Never win the first-ingest race with a NULL analysis lineage. The real
        # outbox must first persist the original USER's authenticated lineage.
        cursor = await db.execute(
            "SELECT o.analysis_lineage_json FROM memory_ingestion_outbox o "
            "JOIN memory_ingestion_evidence_links l ON l.outbox_id=o.outbox_id "
            "WHERE l.evidence_id=? AND o.host_run_id=? AND o.sdk_run_id=? "
            "AND o.subject=? AND o.state='delivered'",
            (user.evidence_id, host_run_id, run["sdk_run_id"], self.subject),
        )
        outbox = await cursor.fetchone()
        if outbox is None:
            raise ConversationRegistrationUnavailable("conversation_user_ingestion_pending")
        from simple_harness_memory.core.jobs import AnalysisLineage
        user_lineage = AnalysisLineage.from_json(json.loads(outbox["analysis_lineage_json"]))
        assistant = await verify_primary_message_evidence_tx(db, primary_ref=self.primary_ref,
            host_run_id=host_run_id, terminal_envelope=terminal, terminal_receipt=terminal_receipt, user=user)
        if assistant is None:
            if payload.get("message_source_contract") == "primary-message-v1":
                raise RuntimeError("primary_message_source_missing")
            raise ConversationRegistrationUnavailable("conversation_message_source_missing")
        sources = ((user, user_receipt, "/text"), (*assistant, "/source/message/content"))
        manifest = canonical_hash({"domain": ISSUER + "/group", "payload": {
            "host_run_id": host_run_id, "terminal_receipt_hash": identity.host_receipt_hash,
            "sdk_event_hash": identity.raw_sdk_event_hash,
            "items": [{"evidence_id": env.evidence_id, "envelope_hash": env.envelope_hash,
                       "pointer": pointer, "role": messages[i]["role"], "ordinal": i + 1}
                      for i, (env, _, pointer) in enumerate(sources)],
        }})
        return ConversationGroup(host_run_id, tuple(self._registration(run, source, i + 1, manifest)
            for i, source in enumerate(sources)), user_lineage)

    def _registration(self, run, source, ordinal, manifest):
        envelope, receipt, pointer = source
        role = h.ConversationEvidenceRole.USER if ordinal == 1 else h.ConversationEvidenceRole.ASSISTANT
        item = h.EvidenceItemAuthority(
            schema_version=h.EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
            authority_id=_stable("item", [envelope.envelope_hash, pointer]),
            evidence_id=envelope.evidence_id, envelope_hash=envelope.envelope_hash,
            sanitized_hash=envelope.sanitized_hash, source_hash=envelope.source_hash,
            source_kind=envelope.source_kind, item_ordinal=ordinal,
            item_id=_stable("item-id", [envelope.evidence_id, pointer]), item_json_pointer=pointer,
            normalization_version=h.EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
            actor_role=h.EvidenceActorRole.USER if ordinal == 1 else h.EvidenceActorRole.ASSISTANT,
            provenance=h.EvidenceProvenance.AUTHENTICATED_USER if ordinal == 1 else h.EvidenceProvenance.MODEL_OUTPUT,
            required_privacy_class=h.PrivacyClass.PERSONAL, required_information_attributes=(),
            classification_authority_ref=CLASSIFICATION, issuer_ref=ISSUER,
        )
        metadata = h.ConversationEvidenceMetadata(
            metadata_id=_stable("metadata", [manifest, ordinal]), authority_issuer_id=ISSUER,
            evidence_id=envelope.evidence_id, envelope_hash=envelope.envelope_hash,
            admission_receipt_id=receipt.receipt_id, admission_receipt_hash=receipt.receipt_hash,
            run_id=envelope.run_id, subject=self.subject, source_hash=envelope.source_hash,
            sanitized_hash=envelope.sanitized_hash, conversation_id=self.primary_ref,
            primary_conversation_id=self.primary_ref, causal_group_id=run["host_run_id"],
            causal_group_sequence=run["enqueue_sequence"], item_ordinal=ordinal, group_item_count=2,
            ordered_group_manifest_hash=manifest, role=role, occurred_at=receipt.admitted_at,
            task_scope_id=run["task_scope_id"], tool_causal_link=None, entities=(),
        )
        metadata = h.authorize_conversation_public_text(metadata, h.AdmittedEvidenceAuthority(envelope, receipt, item))
        metadata_receipt = h.ConversationEvidenceMetadataReceipt(
            receipt_id=_stable("metadata-receipt", metadata.metadata_hash), metadata_id=metadata.metadata_id,
            authority_issuer_id=ISSUER, evidence_id=metadata.evidence_id, envelope_hash=metadata.envelope_hash,
            admission_receipt_id=metadata.admission_receipt_id, admission_receipt_hash=metadata.admission_receipt_hash,
            run_id=metadata.run_id, subject=metadata.subject, source_hash=metadata.source_hash,
            sanitized_hash=metadata.sanitized_hash, metadata_hash=metadata.metadata_hash,
            issuer_ref=ISSUER, accepted=True,
        )
        return h.ConversationEvidenceRegistration(_stable("registration", [manifest, ordinal]),
            envelope, receipt, metadata, metadata_receipt, item)
