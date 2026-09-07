"""Synthetic typed-observation fixtures, constructed before public ingestion.

This is fixture authority issuance, not a way to upgrade real user/model quotes.
Only package-root DTOs are used. No candidate result, oracle, or SDK hash helper
is consulted. Callers must register the returned admission/item authority and
resolve the returned typed receipt from their trusted fixture authority port.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace

import simple_harness as h

SCHEMA_ID = "observation/typed-recall-public-text"
SCHEMA_VERSION = 1
_SCHEMA_CANONICAL = (
    b'{"description":"Admitted public memory assertion text","type":"string"}'
)
SCHEMA_HASH = hashlib.sha256(_SCHEMA_CANONICAL).hexdigest()


def typed_observation_schema() -> dict:
    """Return a fresh copy of the fixture authority's independently defined schema."""
    return json.loads(_SCHEMA_CANONICAL)


def typed_observation_ref(
    receipt: h.TypedObservationAuthorityReceipt,
) -> h.ProposedTypedObservationRef:
    return h.ProposedTypedObservationRef(
        schema_id=receipt.schema_id,
        schema_version=receipt.schema_version,
        registered_schema_hash=receipt.registered_schema_hash,
        observation_receipt_id=receipt.receipt_id,
        observation_receipt_hash=receipt.receipt_hash,
        authority_issuer_id=receipt.issuer_ref,
        json_pointer=receipt.json_pointer,
        value_hash=receipt.value_hash,
    )


def _text_at(payload, pointer):
    value = payload
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, Mapping):
            value = value[token]
        elif isinstance(value, (list, tuple)) and token.isdecimal():
            value = value[int(token)]
        else:
            raise ValueError("fixture observation pointer does not resolve")
    if not isinstance(value, str):
        raise TypeError("fixture observation schema requires a string")
    return value


def bind_typed_observation_fixture(
    envelope: h.SanitizedEvidenceEnvelope,
    receipt: h.SanitizedEvidenceReceipt,
    span: h.EvidenceSpanRef,
    *,
    epistemic_status: h.EpistemicStatus | str,
    verification_state: h.VerificationState | str,
    issuer_ref: str = "host-typed-observation-authority",
    observation_receipt_id: str | None = None,
) -> tuple[
    h.SanitizedEvidenceEnvelope,
    h.SanitizedEvidenceReceipt,
    h.EvidenceSpanRef,
    h.TypedObservationAuthorityReceipt,
]:
    """Rebind an un-ingested synthetic fixture to external or trusted Tool evidence.

    verified_external requires source_verified; internal verification supports
    source_verified/repeated_observation. No requested status is changed.
    For explicit_user, this returned TOOL span is *additional* verification
    evidence: retain the original USER span separately,
    use a different evidence_id for this fixture, and include both in the plan.
    llm_inference/unknown cannot become verified by adding authority evidence.
    Do not overwrite already ingested envelopes or their authority registrations.
    Text, source hash, item identity, byte range and admitted_at are preserved.
    """
    epistemic = h.EpistemicStatus(epistemic_status)
    verification = h.VerificationState(verification_state)
    if epistemic in {h.EpistemicStatus.LLM_INFERENCE, h.EpistemicStatus.UNKNOWN}:
        raise ValueError("inference/unknown cannot use a verified fixture")
    external = epistemic is h.EpistemicStatus.VERIFIED_EXTERNAL
    if external and verification is not h.VerificationState.SOURCE_VERIFIED:
        raise ValueError("verified_external requires source_verified state")
    if not external and verification not in {
        h.VerificationState.SOURCE_VERIFIED,
        h.VerificationState.REPEATED_OBSERVATION,
    }:
        raise ValueError(
            "internal typed fixture requires a verified verification state"
        )
    receipt.verify(envelope)
    if (
        span.evidence_id,
        span.envelope_hash,
        span.sanitized_hash,
        span.source_hash,
        span.source_kind,
        span.admission_receipt_id,
        span.admission_receipt_hash,
    ) != (
        envelope.evidence_id,
        envelope.envelope_hash,
        envelope.sanitized_hash,
        envelope.source_hash,
        envelope.source_kind,
        receipt.receipt_id,
        receipt.receipt_hash,
    ):
        raise ValueError("fixture input span differs from admission")
    text = _text_at(envelope.sanitized_payload, span.item_json_pointer)
    if span.normalization_version != h.EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1:
        raise ValueError("unsupported fixture normalization")
    encoded = text.encode("utf-8")
    if (
        span.end_byte > len(encoded)
        or encoded[span.start_byte : span.end_byte].decode() != span.exact_quote
    ):
        raise ValueError("fixture quote differs from admitted text")
    source_kind = (
        h.EvidenceSourceKind.PROVIDER_RECORD
        if external
        else h.EvidenceSourceKind.TOOL_RESULT
    )
    rebound = replace(
        envelope,
        source_kind=source_kind,
        source_ref=f"{issuer_ref}/{envelope.evidence_id}",
    )
    admitted = replace(receipt, envelope_hash=rebound.envelope_hash)
    value_hash = hashlib.sha256(
        json.dumps(
            text,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    observation = h.TypedObservationAuthorityReceipt(
        receipt_id=f"{envelope.evidence_id}-typed"
        if observation_receipt_id is None
        else observation_receipt_id,
        evidence_id=rebound.evidence_id,
        envelope_hash=rebound.envelope_hash,
        sanitized_hash=rebound.sanitized_hash,
        admission_receipt_id=admitted.receipt_id,
        admission_receipt_hash=admitted.receipt_hash,
        item_ordinal=span.item_ordinal,
        item_id=span.item_id,
        item_json_pointer=span.item_json_pointer,
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        registered_schema_hash=SCHEMA_HASH,
        json_pointer=span.item_json_pointer,
        value_hash=value_hash,
        accepted=True,
        issuer_ref=issuer_ref,
    )
    proposed = replace(
        span,
        source_kind=source_kind,
        envelope_hash=rebound.envelope_hash,
        admission_receipt_hash=admitted.receipt_hash,
        actor_role=h.EvidenceActorRole.EXTERNAL
        if external
        else h.EvidenceActorRole.TOOL,
        provenance=h.EvidenceProvenance.EXTERNAL_SOURCE
        if external
        else h.EvidenceProvenance.TRUSTED_TOOL,
        support_kind=h.EvidenceSupportKind.TYPED_OBSERVATION,
        typed_observation=typed_observation_ref(observation),
    )
    return rebound, admitted, proposed, observation
