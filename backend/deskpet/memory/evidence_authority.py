# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5b Task 4 — Host ``EvidenceAuthorityPort`` over the durable state.db evidence ledger.

Memory 0.6.1 materializes an accepted analysis plan **inside its own write lock**
and resolves every ``EvidenceSpanRef`` through this port (design-freeze §8.3,
spike A2 fact 9 / GOTCHA A).  Therefore this resolver reads **only** the Host
state.db rows written by ``HumanMemoryProgramStore.append_evidence``
(``human_memory_evidence.envelope_json`` + ``human_memory_sanitization_receipts.
receipt_json``) and never calls back into the Memory backend (a locked read
there deadlocks — observed in the spec-value spike).

The same durable read backs the analysis executor's prompt (which evidence text
the model may quote) so derivation and verification see one envelope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

HOST_CLASSIFICATION_AUTHORITY_REF = "host:classification/v1"
HOST_EVIDENCE_ISSUER_REF = "host:evidence-authority/v1"


class HostEvidenceUnavailable(ValueError):
    """The span points at evidence the Host never admitted (or a tampered copy)."""

    code = "host_evidence_not_admitted"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class HostEvidenceAuthority:
    """Resolve admitted evidence from Host state.db; issue the item authority for ``/text``."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self.resolutions = 0

    async def read_admitted(self, evidence_id: str) -> tuple[Any, Any]:
        """(envelope, receipt) exactly as committed by the Host, or ``HostEvidenceUnavailable``."""

        from simple_harness.runtime import (
            SanitizedEvidenceEnvelope,
            SanitizedEvidenceReceipt,
        )

        evidence_id = str(evidence_id or "").strip()
        if not evidence_id:
            raise HostEvidenceUnavailable("host_evidence_id_invalid")
        async with aiosqlite.connect(f"file:{self._db_path}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT e.envelope_json, e.envelope_sha256, r.receipt_json, r.receipt_sha256 "
                "FROM human_memory_evidence e "
                "JOIN human_memory_sanitization_receipts r ON r.receipt_id=e.receipt_id "
                "WHERE e.evidence_id=?",
                (evidence_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise HostEvidenceUnavailable()
        try:
            envelope = SanitizedEvidenceEnvelope.from_json(json.loads(str(row["envelope_json"])))
            receipt = SanitizedEvidenceReceipt.from_json(json.loads(str(row["receipt_json"])))
        except (TypeError, ValueError, KeyError) as exc:
            raise HostEvidenceUnavailable("host_evidence_envelope_invalid") from exc
        if envelope.envelope_hash != str(row["envelope_sha256"]) or receipt.receipt_hash != str(row["receipt_sha256"]):
            raise HostEvidenceUnavailable("host_evidence_hash_mismatch")
        if receipt.evidence_id != envelope.evidence_id or receipt.envelope_hash != envelope.envelope_hash:
            raise HostEvidenceUnavailable("host_evidence_receipt_mismatch")
        return envelope, receipt

    async def resolve_admitted_evidence(self, span: Any) -> Any:
        from simple_harness.runtime import (
            EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
            AdmittedEvidenceAuthority,
            EvidenceItemAuthority,
            PrivacyClass,
        )

        self.resolutions += 1
        envelope, receipt = await self.read_admitted(span.evidence_id)
        if (
            span.envelope_hash != envelope.envelope_hash
            or span.sanitized_hash != envelope.sanitized_hash
            or span.source_hash != envelope.source_hash
            or span.admission_receipt_id != receipt.receipt_id
            or span.admission_receipt_hash != receipt.receipt_hash
        ):
            raise HostEvidenceUnavailable("host_evidence_span_lineage_mismatch")
        return AdmittedEvidenceAuthority(
            envelope,
            receipt,
            EvidenceItemAuthority(
                schema_version=EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
                authority_id=f"host-item-authority:{envelope.evidence_id}:{span.item_ordinal}",
                evidence_id=envelope.evidence_id,
                envelope_hash=envelope.envelope_hash,
                sanitized_hash=envelope.sanitized_hash,
                source_hash=envelope.source_hash,
                source_kind=envelope.source_kind,
                item_ordinal=span.item_ordinal,
                item_id=span.item_id,
                item_json_pointer=span.item_json_pointer,
                normalization_version=span.normalization_version,
                actor_role=span.actor_role,
                provenance=span.provenance,
                required_privacy_class=PrivacyClass.PERSONAL,
                required_information_attributes=(),
                classification_authority_ref=HOST_CLASSIFICATION_AUTHORITY_REF,
                issuer_ref=HOST_EVIDENCE_ISSUER_REF,
            ),
        )

    async def resolve_typed_observation(self, reference: Any) -> Any:
        # S5c: typed observation / memory action / prospective signal authorities.
        raise ValueError("host_typed_observation_not_registered")


__all__ = [
    "HOST_CLASSIFICATION_AUTHORITY_REF",
    "HOST_EVIDENCE_ISSUER_REF",
    "HostEvidenceAuthority",
    "HostEvidenceUnavailable",
]
