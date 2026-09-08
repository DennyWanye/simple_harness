"""Derive short-index authority from immutable completed primary Host facts.

No Memory SQL, new evidence, timestamp refresh, or second registration ledger.
The v1 two-message producer remains exact. New v2 text tool groups carry real
per-message sources and explicit Host tool settlement attestations.
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
    # Actual Host S1 ancestor of the message envelopes; not a conversation item.
    terminal_source: tuple[h.SanitizedEvidenceEnvelope, h.SanitizedEvidenceReceipt]

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
    def __init__(self, db_path: str | Path, *, subject: str, primary_ref: str | None = None):
        identifier(subject, "subject")
        if primary_ref is not None:
            identifier(primary_ref, "primary_ref")
        self.db_path, self.subject = Path(db_path), subject
        self._expected_primary = primary_ref
        self._binding = None

    @property
    def primary_ref(self):
        return self._binding[0] if self._binding is not None else self._expected_primary

    async def _bind_primary_tx(self, db):
        from deskpet.memory.history_source_authority import history_namespace_tx

        namespace, primary = await history_namespace_tx(db, self.subject)
        if self.primary_ref is not None and primary != self.primary_ref:
            raise ConversationRegistrationUnavailable("conversation_primary_mismatch")
        binding = (primary, namespace["store_epoch"])
        if self._binding is not None and binding != self._binding:
            raise ConversationRegistrationUnavailable("conversation_epoch_mismatch")
        # No await or replacement after this one-time pin. Every read transaction
        # still recomputes and compares the current authoritative initialization.
        if self._binding is None:
            self._binding = binding

    async def bind_primary(self):
        """Resolve actual initialization lazily; factory construction never writes it."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            await self._bind_primary_tx(db)

    def _connect(self):
        return aiosqlite.connect(f"file:{self.db_path}?mode=ro", uri=True)

    async def page_turns(self, *, after: int, upper: int | None, limit: int = 16):
        """All turn identities, including pending; cursor is never a success claim."""
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError("conversation_page_invalid")
        if upper is not None and (type(upper) is not int or upper < after):
            raise ValueError("conversation_page_invalid")
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            await self._bind_primary_tx(db)
            if upper is None:
                row = await (await db.execute(
                    "SELECT COALESCE(MAX(enqueue_sequence),0) FROM foreground_turns "
                    "WHERE subject=? AND primary_conversation_id=?",
                    (self.subject, self.primary_ref),
                )).fetchone()
                upper = row[0]
            rows = await (await db.execute(
                "SELECT t.enqueue_sequence,r.host_run_id,f.terminal_state "
                "FROM foreground_turns t LEFT JOIN foreground_runs r ON r.turn_id=t.turn_id "
                "AND r.subject=t.subject AND r.primary_conversation_id=t.primary_conversation_id "
                "LEFT JOIN foreground_terminal_receipts f ON f.host_run_id=r.host_run_id "
                "WHERE t.subject=? AND t.primary_conversation_id=? "
                "AND t.enqueue_sequence>? AND t.enqueue_sequence<=? "
                "ORDER BY t.enqueue_sequence LIMIT ?",
                (self.subject, self.primary_ref, after, upper, limit),
            )).fetchall()
            return upper, tuple(tuple(row) for row in rows)

    async def completed_run_ids(self):
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            await self._bind_primary_tx(db)
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
            await self._bind_primary_tx(db)
            return await self._group_tx(db, host_run_id)

    async def resolve_conversation_registration(self, reference):
        if type(reference) is not h.ConversationEvidenceRegistrationRef:
            raise TypeError("reference must use ConversationEvidenceRegistrationRef")
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            await self._bind_primary_tx(db)
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
        v3 = payload.get("message_source_contract") == "primary-message-v3"
        v2 = v3 or payload.get("message_source_contract") == "primary-message-v2"
        if v2:
            from deskpet.memory.primary_message_v2 import representable
            if not representable(messages, payload.get("tool_causal_sources")):
                raise RuntimeError("primary_message_v2_source_mismatch")
        elif not isinstance(messages, list) or len(messages) != 2:
            raise ConversationRegistrationUnavailable("terminal_multiple_items_not_representable")
        if (set(messages[0]) != {"role", "content"} or messages[0]["role"] != "user"
                or messages[0]["content"] != user.sanitized_payload.get("text")
                or messages[0]["content"] != json.loads(run["turn_json"])["payload"]["text"]
                or (not v2 and (set(messages[1]) != {"role", "content"} or messages[1]["role"] != "assistant"
                    or not all(isinstance(m["content"], str) and m["content"] for m in messages)))):
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
        if v2:
            from deskpet.memory import primary_message_v2, primary_message_v3
            verify = primary_message_v3.verify if v3 else primary_message_v2.verify
            children = await verify(db, primary_ref=self.primary_ref, host_run_id=host_run_id,
                                   terminal=terminal, terminal_receipt=terminal_receipt, user=user)
        else:
            assistant = await verify_primary_message_evidence_tx(db, primary_ref=self.primary_ref,
                host_run_id=host_run_id, terminal_envelope=terminal, terminal_receipt=terminal_receipt, user=user)
            if assistant is None:
                if payload.get("message_source_contract") == "primary-message-v1":
                    raise RuntimeError("primary_message_source_missing")
                raise ConversationRegistrationUnavailable("conversation_message_source_missing")
            children = (assistant,)
        sources = ((user, user_receipt, "/text"),
                   *((envelope, receipt, "/source/message/content") for envelope, receipt in children))
        # A tool call denied before dispatch is archived in ``messages`` but is
        # not one of the group's evidence items, so the group's item ordinals
        # can be shorter than the transcript. Roles and Scope sources must still
        # be read at each item's own transcript ordinal.
        transcript = (1, 2)
        if v2:
            from deskpet.memory.primary_message_v2 import item_ordinals
            transcript = item_ordinals(messages, payload["tool_causal_sources"])
            if len(transcript) != len(sources):
                raise RuntimeError("primary_message_v2_source_mismatch")
        manifest = canonical_hash({"domain": ISSUER + "/group", "payload": {
            "host_run_id": host_run_id, "terminal_receipt_hash": identity.host_receipt_hash,
            "sdk_event_hash": identity.raw_sdk_event_hash,
            "items": [{"evidence_id": env.evidence_id, "envelope_hash": env.envelope_hash,
                       "pointer": pointer, "role": messages[transcript[i] - 1]["role"], "ordinal": i + 1}
                      for i, (env, _, pointer) in enumerate(sources)],
        }})
        scope_map = {item["item_ordinal"]: item["task_scope_id"] for item in payload.get("tool_scope_sources", ())} if v3 else {}
        group_ordinals = {ordinal: index + 1 for index, ordinal in enumerate(transcript)}
        return ConversationGroup(host_run_id, tuple(self._registration(run, source, i + 1, manifest, len(sources),
            task_scope_id=scope_map.get(transcript[i], run["task_scope_id"]), group_ordinals=group_ordinals)
            for i, source in enumerate(sources)), user_lineage, (terminal, terminal_receipt))

    def _registration(self, run, source, ordinal, manifest, group_count=2, *, task_scope_id=None,
                      group_ordinals=None):
        envelope, receipt, pointer = source
        kind = envelope.source_kind.value
        role, actor, provenance = {
            "user_message": (h.ConversationEvidenceRole.USER, h.EvidenceActorRole.USER, h.EvidenceProvenance.AUTHENTICATED_USER),
            "assistant_message": (h.ConversationEvidenceRole.ASSISTANT, h.EvidenceActorRole.ASSISTANT, h.EvidenceProvenance.MODEL_OUTPUT),
            "tool_result": (h.ConversationEvidenceRole.TOOL, h.EvidenceActorRole.TOOL, h.EvidenceProvenance.TRUSTED_TOOL),
        }[kind]
        link = None
        if kind == "tool_result":
            from deskpet.memory.primary_message_v2 import tool_link
            link = tool_link(envelope, group_ordinals)
        item = h.EvidenceItemAuthority(
            schema_version=h.EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
            authority_id=_stable("item", [envelope.envelope_hash, pointer]),
            evidence_id=envelope.evidence_id, envelope_hash=envelope.envelope_hash,
            sanitized_hash=envelope.sanitized_hash, source_hash=envelope.source_hash,
            source_kind=envelope.source_kind, item_ordinal=ordinal,
            item_id=_stable("item-id", [envelope.evidence_id, pointer]), item_json_pointer=pointer,
            normalization_version=h.EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
            actor_role=actor, provenance=provenance,
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
            causal_group_sequence=run["enqueue_sequence"], item_ordinal=ordinal, group_item_count=group_count,
            ordered_group_manifest_hash=manifest, role=role, occurred_at=receipt.admitted_at,
            task_scope_id=task_scope_id, tool_causal_link=link, entities=(),
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
