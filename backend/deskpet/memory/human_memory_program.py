# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Fresh-only primary conversation and permanent Host evidence foundation.

This module is intentionally not the S1 protocol owner.  Until the Host pins
the S1 wheel in S5, callers must provide objects with the frozen S1
``SanitizedEvidenceEnvelope`` / ``SanitizedEvidenceReceipt`` surface.  The
store independently verifies their canonical JSON and binding and fails
closed for any structural drift.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiosqlite

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx


_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "evidence_id",
        "run_id",
        "subject",
        "source_kind",
        "source_ref",
        "source_hash",
        "sanitized_payload",
        "sanitized_hash",
        "filter_policy_version",
        "removed_spans",
        "disclosure_context",
        "evidence_refs",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "receipt_id",
        "run_id",
        "subject",
        "evidence_id",
        "envelope_hash",
        "source_hash",
        "sanitized_hash",
        "filter_policy_version",
        "accepted",
        "reason_codes",
        "disclosure_context",
        "evidence_refs",
        "admitted_at",
    }
)
_DISCLOSURE_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "subject",
        "recipient",
        "recipient_id",
        "intended_audience",
        "purpose",
        "source",
        "trust",
        "generation",
        "authority_ref",
        "reason_codes",
    }
)
_SOURCE_KINDS = frozenset(
    {
        "user_message",
        "assistant_message",
        "tool_result",
        "provider_record",
        "runtime_event",
        "typed_observation",
    }
)
_FORBIDDEN_DURABLE_KEYS = frozenset(
    {
        "api_key",
        "access_token",
        "authorization",
        "authorization_header",
        "cookie",
        "password",
        "private_key",
        "reasoning",
        "reasoning_content",
        "thinking",
        "chain_of_thought",
    }
)
_PROVIDER_PAYLOAD_ALLOWED = frozenset(
    {
        "provider_id",
        "provider_type",
        "model_id",
        "request_hash",
        "response_hash",
        "public_content",
        "usage",
        "cost",
        "finish_reason",
        "provider_request_id",
        "opaque_continuation_ref",
    }
)
_PROVIDER_PAYLOAD_REQUIRED = frozenset(
    {"provider_id", "provider_type", "request_hash", "response_hash"}
)
_PROVIDER_USAGE_ALLOWED = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_tokens",
        "reasoning_tokens",
    }
)
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\b(?:tsk|key)_[A-Za-z0-9_-]{8,}\b"),
)


class HumanMemoryProgramConflict(RuntimeError):
    code = "human_memory_program_conflict"


class HumanMemoryProgramProtocolError(ValueError):
    code = "human_memory_program_protocol_rejected"


class PrimaryConversationMismatch(PermissionError):
    code = "primary_conversation_only"


@runtime_checkable
class SanitizedEvidenceEnvelopeLike(Protocol):
    evidence_id: str
    run_id: str
    subject: str
    source_kind: object
    source_ref: str
    source_hash: str
    sanitized_hash: str
    filter_policy_version: str
    envelope_hash: str
    sanitized_payload: Mapping[str, object]

    def to_json(self) -> Mapping[str, object]: ...


@runtime_checkable
class SanitizedEvidenceReceiptLike(Protocol):
    receipt_id: str
    run_id: str
    subject: str
    evidence_id: str
    envelope_hash: str
    source_hash: str
    sanitized_hash: str
    filter_policy_version: str
    accepted: bool
    admitted_at: float
    receipt_hash: str

    def verify(self, envelope: SanitizedEvidenceEnvelopeLike) -> None: ...

    def to_json(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class PrimaryConversationReceipt:
    receipt_id: str
    subject: str
    primary_conversation_id: str
    format_epoch: str
    marker_sha256: str
    receipt_sha256: str
    created_at: float


@dataclass(frozen=True, slots=True)
class CommittedHostEvidence:
    evidence_id: str
    primary_conversation_id: str
    subject: str
    run_id: str
    source_kind: str
    source_ref: str
    envelope_sha256: str
    receipt_id: str
    committed_at: float


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256_domain_json(domain: str, value: object) -> str:
    return _sha256_json({"domain": domain, "payload": value})


def _bounded_identifier(value: object, name: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise HumanMemoryProgramProtocolError(f"{name}_invalid")
    if len(value.encode("utf-8")) > maximum:
        raise HumanMemoryProgramProtocolError(f"{name}_too_large")
    return value


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise HumanMemoryProgramProtocolError(f"{name}_invalid")
    return value


def _require_schema_v1(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise HumanMemoryProgramProtocolError(f"{name}_schema_rejected")


def _evidence_schema(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in {1, 2}:
        raise HumanMemoryProgramProtocolError(f"{name}_schema_rejected")
    return value


def _reject_private_keys(value: object, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise HumanMemoryProgramProtocolError(f"{path}_key_invalid")
            normalized = raw_key.strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_DURABLE_KEYS:
                raise HumanMemoryProgramProtocolError(
                    f"forbidden_durable_field:{path}.{raw_key}"
                )
            _reject_private_keys(child, f"{path}.{raw_key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_private_keys(child, f"{path}[{index}]")
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS
    ):
        raise HumanMemoryProgramProtocolError(
            f"credential_value_rejected:{path}"
        )


def _validate_provider_payload(payload: Mapping[str, object]) -> None:
    keys = frozenset(payload)
    if not _PROVIDER_PAYLOAD_REQUIRED.issubset(keys):
        raise HumanMemoryProgramProtocolError("provider_public_fields_missing")
    if not keys.issubset(_PROVIDER_PAYLOAD_ALLOWED):
        raise HumanMemoryProgramProtocolError("provider_private_field_rejected")
    for name in ("provider_id", "provider_type"):
        _bounded_identifier(payload[name], name, maximum=256)
    for name in ("request_hash", "response_hash"):
        _digest(payload[name], name)
    for name in ("model_id", "provider_request_id", "opaque_continuation_ref"):
        if payload.get(name) is not None:
            _bounded_identifier(payload[name], name)
    usage = payload.get("usage")
    if usage is not None:
        if not isinstance(usage, Mapping) or not set(usage).issubset(
            _PROVIDER_USAGE_ALLOWED
        ):
            raise HumanMemoryProgramProtocolError("provider_usage_fields_rejected")
        if any(
            value is not None
            and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
            for value in usage.values()
        ):
            raise HumanMemoryProgramProtocolError("provider_usage_value_invalid")


def _source_kind(value: object) -> str:
    normalized = getattr(value, "value", value)
    if not isinstance(normalized, str) or normalized not in _SOURCE_KINDS:
        raise HumanMemoryProgramProtocolError("evidence_source_kind_rejected")
    return normalized


def _validate_disclosure(value: object, *, run_id: str, subject: str) -> None:
    if not isinstance(value, Mapping) or frozenset(value) != _DISCLOSURE_KEYS:
        raise HumanMemoryProgramProtocolError("disclosure_context_fields_differ")
    _require_schema_v1(value.get("schema_version"), "disclosure_context")
    if value.get("run_id") != run_id or value.get("subject") != subject:
        raise HumanMemoryProgramProtocolError("disclosure_context_binding_mismatch")
    for name in (
        "recipient",
        "intended_audience",
        "purpose",
        "source",
        "trust",
        "generation",
    ):
        _bounded_identifier(value.get(name), f"disclosure_{name}", maximum=256)
    for name in ("recipient_id", "authority_ref"):
        if value.get(name) is not None:
            _bounded_identifier(value[name], f"disclosure_{name}", maximum=256)
    reasons = value.get("reason_codes")
    if (
        not isinstance(reasons, list)
        or not all(isinstance(reason, str) and reason for reason in reasons)
        or len(set(reasons)) != len(reasons)
    ):
        raise HumanMemoryProgramProtocolError("disclosure_reason_codes_invalid")


def _validate_evidence_refs(value: object) -> None:
    if not isinstance(value, list):
        raise HumanMemoryProgramProtocolError("evidence_refs_invalid")
    ordinals: list[int] = []
    ids: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "evidence_id",
            "content_hash",
            "ordinal",
        }:
            raise HumanMemoryProgramProtocolError("evidence_ref_fields_differ")
        ids.append(_bounded_identifier(item["evidence_id"], "evidence_ref_id"))
        _digest(item["content_hash"], "evidence_ref_content_hash")
        ordinal = item["ordinal"]
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
            raise HumanMemoryProgramProtocolError("evidence_ref_ordinal_invalid")
        ordinals.append(ordinal)
    if len(set(ids)) != len(ids) or ordinals != list(range(1, len(ordinals) + 1)):
        raise HumanMemoryProgramProtocolError("evidence_refs_order_invalid")


def _validate_removed_spans(value: object) -> None:
    if not isinstance(value, list):
        raise HumanMemoryProgramProtocolError("removed_spans_invalid")
    types: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"span_type", "count"}:
            raise HumanMemoryProgramProtocolError("removed_span_fields_differ")
        types.append(_bounded_identifier(item["span_type"], "removed_span_type"))
        count = item["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise HumanMemoryProgramProtocolError("removed_span_count_invalid")
    if len(set(types)) != len(types):
        raise HumanMemoryProgramProtocolError("removed_span_type_duplicate")


def _validate_protocol_pair(
    envelope: SanitizedEvidenceEnvelopeLike,
    receipt: SanitizedEvidenceReceiptLike,
) -> tuple[dict[str, object], dict[str, object], str]:
    if not isinstance(envelope, SanitizedEvidenceEnvelopeLike):
        raise HumanMemoryProgramProtocolError("s1_evidence_envelope_required")
    if not isinstance(receipt, SanitizedEvidenceReceiptLike):
        raise HumanMemoryProgramProtocolError("s1_evidence_receipt_required")
    envelope_json = dict(envelope.to_json())
    receipt_json = dict(receipt.to_json())
    if frozenset(envelope_json) != _ENVELOPE_KEYS:
        raise HumanMemoryProgramProtocolError("evidence_envelope_fields_differ")
    if frozenset(receipt_json) != _RECEIPT_KEYS:
        raise HumanMemoryProgramProtocolError("evidence_receipt_fields_differ")

    envelope_schema = _evidence_schema(
        envelope_json.get("schema_version"), "evidence_envelope"
    )
    receipt_schema = _evidence_schema(
        receipt_json.get("schema_version"), "evidence_receipt"
    )
    if envelope_schema != receipt_schema:
        raise HumanMemoryProgramProtocolError("evidence_schema_binding_mismatch")
    envelope_bindings = {
        "evidence_id": envelope.evidence_id,
        "run_id": envelope.run_id,
        "subject": envelope.subject,
        "source_kind": _source_kind(envelope.source_kind),
        "source_ref": envelope.source_ref,
        "source_hash": envelope.source_hash,
        "sanitized_hash": envelope.sanitized_hash,
        "filter_policy_version": envelope.filter_policy_version,
    }
    receipt_bindings = {
        "receipt_id": receipt.receipt_id,
        "run_id": receipt.run_id,
        "subject": receipt.subject,
        "evidence_id": receipt.evidence_id,
        "envelope_hash": receipt.envelope_hash,
        "source_hash": receipt.source_hash,
        "sanitized_hash": receipt.sanitized_hash,
        "filter_policy_version": receipt.filter_policy_version,
        "accepted": receipt.accepted,
        "admitted_at": receipt.admitted_at,
    }
    if any(envelope_json.get(key) != value for key, value in envelope_bindings.items()):
        raise HumanMemoryProgramProtocolError("evidence_object_json_mismatch")
    if any(receipt_json.get(key) != value for key, value in receipt_bindings.items()):
        raise HumanMemoryProgramProtocolError("receipt_object_json_mismatch")
    for value, name, maximum in (
        (envelope.evidence_id, "evidence_id", 256),
        (envelope.run_id, "run_id", 256),
        (envelope.subject, "subject", 256),
        (envelope.source_ref, "source_ref", 1024),
        (receipt.receipt_id, "receipt_id", 256),
        (receipt.filter_policy_version, "filter_policy_version", 256),
    ):
        _bounded_identifier(value, name, maximum=maximum)
    _digest(envelope.source_hash, "source_hash")
    _digest(envelope.sanitized_hash, "sanitized_hash")
    _validate_removed_spans(envelope_json.get("removed_spans"))
    _validate_evidence_refs(envelope_json.get("evidence_refs"))
    _validate_evidence_refs(receipt_json.get("evidence_refs"))
    _validate_disclosure(
        envelope_json.get("disclosure_context"),
        run_id=envelope.run_id,
        subject=envelope.subject,
    )
    _validate_disclosure(
        receipt_json.get("disclosure_context"),
        run_id=receipt.run_id,
        subject=receipt.subject,
    )
    reasons = receipt_json.get("reason_codes")
    if (
        not isinstance(reasons, list)
        or not reasons
        or not all(isinstance(reason, str) and reason for reason in reasons)
        or len(set(reasons)) != len(reasons)
        or receipt.accepted
        != ("evidence_sanitized_and_accepted" in reasons)
    ):
        raise HumanMemoryProgramProtocolError("receipt_reason_codes_invalid")
    if (
        isinstance(receipt.admitted_at, bool)
        or not isinstance(receipt.admitted_at, (int, float))
        or not math.isfinite(float(receipt.admitted_at))
        or float(receipt.admitted_at) < 0
    ):
        raise HumanMemoryProgramProtocolError("receipt_admitted_at_invalid")
    if (
        envelope_json["disclosure_context"] != receipt_json["disclosure_context"]
        or envelope_json["evidence_refs"] != receipt_json["evidence_refs"]
    ):
        raise HumanMemoryProgramProtocolError("receipt_lineage_binding_mismatch")

    try:
        receipt.verify(envelope)
    except Exception as exc:  # noqa: BLE001
        raise HumanMemoryProgramProtocolError(
            "sanitization_receipt_verification_failed"
        ) from exc
    if not receipt.accepted:
        raise HumanMemoryProgramProtocolError("sanitization_receipt_not_accepted")
    if envelope_schema == 1:
        envelope_hash = _sha256_json(envelope_json)
        receipt_hash = _sha256_json(receipt_json)
    else:
        envelope_hash = _sha256_domain_json(
            "simple-harness/sanitized-evidence-envelope/v2", envelope_json
        )
        receipt_hash = _sha256_domain_json(
            "simple-harness/sanitized-evidence-receipt/v2", receipt_json
        )
    if envelope_hash != _digest(envelope.envelope_hash, "envelope_hash"):
        raise HumanMemoryProgramProtocolError("evidence_envelope_hash_mismatch")
    if receipt_hash != _digest(receipt.receipt_hash, "receipt_hash"):
        raise HumanMemoryProgramProtocolError("evidence_receipt_hash_mismatch")
    if receipt.envelope_hash != envelope_hash:
        raise HumanMemoryProgramProtocolError("receipt_envelope_hash_mismatch")
    if (
        envelope.evidence_id != receipt.evidence_id
        or envelope.run_id != receipt.run_id
        or envelope.subject != receipt.subject
        or envelope.source_hash != receipt.source_hash
        or envelope.sanitized_hash != receipt.sanitized_hash
    ):
        raise HumanMemoryProgramProtocolError("receipt_evidence_binding_mismatch")

    payload = envelope_json.get("sanitized_payload")
    if not isinstance(payload, Mapping):
        raise HumanMemoryProgramProtocolError("sanitized_payload_invalid")
    payload_dict = dict(payload)
    if _sha256_json(payload_dict) != _digest(
        envelope.sanitized_hash, "sanitized_hash"
    ):
        raise HumanMemoryProgramProtocolError("sanitized_payload_hash_mismatch")
    _reject_private_keys(payload_dict)
    kind = _source_kind(envelope.source_kind)
    if kind == "provider_record":
        _validate_provider_payload(payload_dict)
    return envelope_json, receipt_json, kind


class HumanMemoryProgramStore:
    """Host authority for fresh primary identity and append-only raw evidence."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    async def initialize_subject(self, subject: str) -> PrimaryConversationReceipt:
        subject = _bounded_identifier(subject, "subject", maximum=512)
        await initialize_human_memory_program_state_db(self._db_path)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                cursor = await db.execute(
                    "SELECT primary_conversation_id,created_at FROM "
                    "human_memory_primary_conversations WHERE subject=? AND writable=1",
                    (subject,),
                )
                primary = await cursor.fetchone()
                await cursor.close()
                if primary is None:
                    created_at = time.time()
                    primary_id = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"simple-harness:human-memory-v1:{subject}",
                        )
                    )
                    await db.execute(
                        "INSERT INTO human_memory_primary_conversations("
                        "primary_conversation_id,subject,writable,created_at) "
                        "VALUES (?,?,1,?)",
                        (primary_id, subject, created_at),
                    )
                    marker = await self._marker_tx(db)
                    marker_sha256 = _sha256_json(marker)
                    receipt_payload = {
                        "subject": subject,
                        "primary_conversation_id": primary_id,
                        "format_epoch": "human-memory-v1",
                        "marker_sha256": marker_sha256,
                        "created_at": created_at,
                    }
                    await db.execute(
                        "INSERT INTO human_memory_init_receipts("
                        "receipt_id,subject,primary_conversation_id,format_epoch,"
                        "marker_sha256,receipt_sha256,created_at) VALUES (?,?,?,?,?,?,?)",
                        (
                            str(
                                uuid.uuid5(
                                    uuid.NAMESPACE_URL,
                                    f"simple-harness:human-memory-init:{subject}",
                                )
                            ),
                            subject,
                            primary_id,
                            "human-memory-v1",
                            marker_sha256,
                            _sha256_json(receipt_payload),
                            created_at,
                        ),
                    )
                cursor = await db.execute(
                    "SELECT * FROM human_memory_init_receipts WHERE subject=?",
                    (subject,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        assert row is not None
        return PrimaryConversationReceipt(
            receipt_id=str(row["receipt_id"]),
            subject=str(row["subject"]),
            primary_conversation_id=str(row["primary_conversation_id"]),
            format_epoch=str(row["format_epoch"]),
            marker_sha256=str(row["marker_sha256"]),
            receipt_sha256=str(row["receipt_sha256"]),
            created_at=float(row["created_at"]),
        )

    async def open_primary_conversation(
        self,
        subject: str,
        *,
        requested_conversation_id: str | None = None,
    ) -> PrimaryConversationReceipt:
        receipt = await self.initialize_subject(subject)
        if (
            requested_conversation_id is not None
            and requested_conversation_id != receipt.primary_conversation_id
        ):
            raise PrimaryConversationMismatch(PrimaryConversationMismatch.code)
        return receipt

    async def append_evidence(
        self,
        envelope: SanitizedEvidenceEnvelopeLike,
        receipt: SanitizedEvidenceReceiptLike,
    ) -> CommittedHostEvidence:
        envelope_json, receipt_json, kind = _validate_protocol_pair(
            envelope, receipt
        )
        primary = await self.initialize_subject(envelope.subject)
        payload_json = _canonical_json(envelope_json["sanitized_payload"])
        committed_at = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                cursor = await db.execute(
                    "SELECT evidence.*, receipt.receipt_sha256 AS "
                    "linked_receipt_sha256 FROM human_memory_evidence AS evidence "
                    "JOIN human_memory_sanitization_receipts AS receipt "
                    "ON receipt.receipt_id=evidence.receipt_id "
                    "WHERE evidence.evidence_id=?",
                    (envelope.evidence_id,),
                )
                existing = await cursor.fetchone()
                await cursor.close()
                if existing is not None:
                    if (
                        str(existing["envelope_sha256"]) != envelope.envelope_hash
                        or str(existing["receipt_id"]) != receipt.receipt_id
                        or str(existing["linked_receipt_sha256"])
                        != receipt.receipt_hash
                    ):
                        raise HumanMemoryProgramConflict(
                            HumanMemoryProgramConflict.code
                        )
                    await db.rollback()
                    return self._evidence_from_row(existing)

                await db.execute(
                    "INSERT INTO human_memory_sanitization_receipts("
                    "receipt_id,evidence_id,subject,run_id,envelope_sha256,"
                    "source_sha256,sanitized_sha256,filter_policy_version,"
                    "receipt_sha256,receipt_json,admitted_at,committed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        receipt.receipt_id,
                        receipt.evidence_id,
                        receipt.subject,
                        receipt.run_id,
                        receipt.envelope_hash,
                        receipt.source_hash,
                        receipt.sanitized_hash,
                        receipt.filter_policy_version,
                        receipt.receipt_hash,
                        _canonical_json(receipt_json),
                        float(receipt.admitted_at),
                        committed_at,
                    ),
                )
                await db.execute(
                    "INSERT INTO human_memory_evidence("
                    "evidence_id,primary_conversation_id,subject,run_id,source_kind,"
                    "source_ref,source_sha256,sanitized_sha256,envelope_sha256,"
                    "receipt_id,payload_json,envelope_json,occurred_at,committed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        envelope.evidence_id,
                        primary.primary_conversation_id,
                        envelope.subject,
                        envelope.run_id,
                        kind,
                        envelope.source_ref,
                        envelope.source_hash,
                        envelope.sanitized_hash,
                        envelope.envelope_hash,
                        receipt.receipt_id,
                        payload_json,
                        _canonical_json(envelope_json),
                        float(receipt.admitted_at),
                        committed_at,
                    ),
                )
                cursor = await db.execute(
                    "SELECT * FROM human_memory_evidence WHERE evidence_id=?",
                    (envelope.evidence_id,),
                )
                row = await cursor.fetchone()
                await cursor.close()
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                raise HumanMemoryProgramConflict(
                    HumanMemoryProgramConflict.code
                ) from exc
            except Exception:
                await db.rollback()
                raise
        assert row is not None
        return self._evidence_from_row(row)

    async def list_evidence(
        self, subject: str, *, limit: int = 1000
    ) -> list[CommittedHostEvidence]:
        primary = await self.initialize_subject(subject)
        bounded = max(1, min(int(limit), 10_000))
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM human_memory_evidence "
                "WHERE primary_conversation_id=? "
                "ORDER BY committed_at,evidence_id LIMIT ?",
                (primary.primary_conversation_id, bounded),
            )
            rows = await cursor.fetchall()
            await cursor.close()
        return [self._evidence_from_row(row) for row in rows]

    @staticmethod
    async def _marker_tx(db: aiosqlite.Connection) -> dict[str, object]:
        cursor = await db.execute(
            "SELECT format_epoch,schema_version,migration_id,migration_sha256 "
            "FROM human_memory_program_marker WHERE singleton=1"
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise RuntimeError("human_memory_program_marker_missing")
        return {
            "format_epoch": row[0],
            "schema_version": row[1],
            "migration_id": row[2],
            "migration_sha256": row[3],
        }

    @staticmethod
    def _evidence_from_row(row: aiosqlite.Row) -> CommittedHostEvidence:
        return CommittedHostEvidence(
            evidence_id=str(row["evidence_id"]),
            primary_conversation_id=str(row["primary_conversation_id"]),
            subject=str(row["subject"]),
            run_id=str(row["run_id"]),
            source_kind=str(row["source_kind"]),
            source_ref=str(row["source_ref"]),
            envelope_sha256=str(row["envelope_sha256"]),
            receipt_id=str(row["receipt_id"]),
            committed_at=float(row["committed_at"]),
        )


__all__ = [
    "CommittedHostEvidence",
    "HumanMemoryProgramConflict",
    "HumanMemoryProgramProtocolError",
    "HumanMemoryProgramStore",
    "PrimaryConversationMismatch",
    "PrimaryConversationReceipt",
    "SanitizedEvidenceEnvelopeLike",
    "SanitizedEvidenceReceiptLike",
]
