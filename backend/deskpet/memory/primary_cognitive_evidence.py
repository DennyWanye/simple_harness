"""Durable, exact Host admission for an explicit cognitive forget action.

The caller verifies the current public memory target before the first admission.
This module never reads or mutates a Memory SDK database.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from deskpet.memory.evidence_authority import (
    HostEvidenceAuthority,
    HostEvidenceUnavailable,
)
from deskpet.memory.history_source_authority import history_namespace_tx
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    build_host_typed_evidence,
)
from deskpet.memory.writer_fence import (
    assert_human_memory_ingress_open_tx,
    human_memory_connection,
    human_memory_request_boundary,
)
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
        self._path = Path(path)
        self._program = HumanMemoryProgramStore(path)
        self._authority = HostEvidenceAuthority(path)

    def _pair(self, payload: Mapping[str, object], idempotency_key: str, *, history_cut=None):
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
        action_payload = {"schema": ACTION_SCHEMA, **dict(payload)}
        if history_cut is not None:
            action_payload.update(schema="primary-memory-forget/v2", history_cut=history_cut)
        return build_host_typed_evidence(
            subject=self._auth.subject,
            authority_ref=self._auth.authority_ref,
            payload=action_payload,
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
            actual_payload = item.envelope.to_json()["sanitized_payload"]
            if actual_payload.get("schema") == "primary-memory-forget/v2":
                cut = actual_payload.get("history_cut")
                if not isinstance(cut, dict) or set(cut) != {"namespace", "through_sequence"}:
                    raise CognitiveActionEvidenceError("primary_memory_action_evidence_invalid")
                envelope, receipt = self._pair(payload, idempotency_key, history_cut=cut)
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
        # Validate the request before touching storage. The cut belongs to the
        # first durable action, never the later SDK delivery or ACK retry.
        self._pair(payload, idempotency_key)
        primary = await self._program.initialize_subject(self._auth.subject)
        async with human_memory_connection(self._path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                existing = await self.find_action(payload=payload, idempotency_key=idempotency_key)
                if existing is not None:
                    await db.commit()
                    return existing
                namespace, primary_ref = await history_namespace_tx(db, self._auth.subject)
                if primary_ref != primary.primary_conversation_id:
                    raise CognitiveActionEvidenceError("primary_memory_action_evidence_invalid")
                row = await (await db.execute(
                    "SELECT COALESCE(MAX(enqueue_sequence),0) AS through_sequence "
                    "FROM foreground_turns WHERE subject=? AND primary_conversation_id=?",
                    (self._auth.subject, primary_ref),
                )).fetchone()
                envelope, receipt = self._pair(payload, idempotency_key, history_cut={
                    "namespace": namespace, "through_sequence": row["through_sequence"],
                })
                committed = await self._program.append_evidence_tx(
                    db, envelope, receipt, primary_conversation_id=primary_ref,
                    committed_at=time.time(),
                )
                if committed.envelope_sha256 != envelope.envelope_hash or committed.subject != self._auth.subject:
                    raise CognitiveActionEvidenceError("primary_memory_action_evidence_invalid")
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
        return self._receipt(envelope, committed.committed_at)
