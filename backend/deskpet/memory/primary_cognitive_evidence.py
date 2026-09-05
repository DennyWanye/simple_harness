"""Durable, exact Host admission for an explicit cognitive forget action.

The caller verifies the current public memory target before the first admission.
This module never reads or mutates a Memory SDK database.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from deskpet.memory.evidence_authority import (
    HostEvidenceAuthority,
    HostEvidenceUnavailable,
)
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    build_host_typed_evidence,
)
from deskpet.memory.writer_fence import human_memory_request_boundary
from deskpet.task_scope.protocol import canonical_hash, identifier

ACTION_SCHEMA = "primary-memory-forget/v1"


class CognitiveActionEvidenceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CognitiveActionEvidence:
    evidence_id: str
    committed_at: float
    payload_hash: str
    suppression_request_id: str


class CognitiveActionEvidenceStore:
    def __init__(self, path: str | Path, *, auth: AuthenticatedHostSnapshot):
        self._auth = auth
        self._program = HumanMemoryProgramStore(path)
        self._authority = HostEvidenceAuthority(path)

    def _pair(self, payload: Mapping[str, object], idempotency_key: str):
        identifier(idempotency_key, "idempotency_key", 512)
        if set(payload) != {"memory_id", "expected_revision", "expected_content_hash"}:
            raise CognitiveActionEvidenceError("primary_memory_action_invalid")
        identifier(payload["memory_id"], "memory_id", 512)
        revision, digest = (
            payload["expected_revision"],
            payload["expected_content_hash"],
        )
        if type(revision) is not int or not 1 <= revision <= 2**53 - 1:
            raise CognitiveActionEvidenceError("primary_memory_action_invalid")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise CognitiveActionEvidenceError("primary_memory_action_invalid")
        key = canonical_hash(
            {"subject": self._auth.subject, "idempotency_key": idempotency_key}
        )
        return build_host_typed_evidence(
            subject=self._auth.subject,
            authority_ref=self._auth.authority_ref,
            payload={"schema": ACTION_SCHEMA, **dict(payload)},
            idempotency_key=f"cognitive-forget:{key}",
            source_ref=f"host-cognitive-action/forget/v1:{key}",
        )

    @staticmethod
    def _receipt(envelope, committed_at: float) -> CognitiveActionEvidence:
        if not math.isfinite(committed_at) or committed_at < 0:
            raise CognitiveActionEvidenceError("primary_memory_action_evidence_invalid")
        return CognitiveActionEvidence(
            str(envelope.evidence_id),
            committed_at,
            str(envelope.sanitized_hash),
            f"primary-forget:{envelope.evidence_id}",
        )

    async def find_action(self, *, payload: Mapping[str, object], idempotency_key: str):
        envelope, receipt = self._pair(payload, idempotency_key)
        async with human_memory_request_boundary():
            try:
                item = await self._authority.read_analysis_item(
                    str(envelope.evidence_id)
                )
            except HostEvidenceUnavailable as exc:
                if str(exc) == "host_evidence_not_admitted":
                    return None
                raise CognitiveActionEvidenceError(
                    "primary_memory_action_evidence_invalid"
                ) from exc
            if (
                item.envelope.envelope_hash != envelope.envelope_hash
                or item.receipt.receipt_hash != receipt.receipt_hash
                or item.envelope.source_ref != envelope.source_ref
                or item.envelope.subject != self._auth.subject
            ):
                raise CognitiveActionEvidenceError("primary_memory_action_conflict")
            return self._receipt(envelope, item.occurred_at)

    async def admit_action(
        self, *, payload: Mapping[str, object], idempotency_key: str
    ):
        envelope, receipt = self._pair(payload, idempotency_key)
        async with human_memory_request_boundary():
            existing = await self.find_action(
                payload=payload, idempotency_key=idempotency_key
            )
            if existing is not None:
                return existing
            committed = await self._program.append_evidence(envelope, receipt)
            if (
                committed.envelope_sha256 != envelope.envelope_hash
                or committed.subject != self._auth.subject
            ):
                raise CognitiveActionEvidenceError(
                    "primary_memory_action_evidence_invalid"
                )
            return self._receipt(envelope, committed.committed_at)
