"""Durable, run-bound primary turn observations; no legacy Session selectors.

The existing append-only evidence store holds the public transcript. Visibility
requires the foreground terminal receipt: an observer crash cannot publish a
half-settled turn. No new schema or second ingestion queue is introduced.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import aiosqlite
from simple_harness import (
    DeliveryRecipient, DisclosureContext, DisclosureGeneration, DisclosurePurpose,
    DisclosureReasonCode, DisclosureSource, DisclosureTrust, EvidenceReasonCode,
    EvidenceSourceKind, IntendedAudience, SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt,
)
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_hash, identifier, reject_private_payload

POLICY = "host-primary-runtime-v1"


def observation_id(sdk_run_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"primary-runtime:{sdk_run_id}"))


def evidence_pair(subject: str, sdk_run_id: str, payload: dict, occurred_at: float):
    digest = canonical_hash(payload)
    disclosure = DisclosureContext(
        sdk_run_id, subject, DeliveryRecipient.USER_SELF, subject, IntendedAudience.USER_SELF,
        DisclosurePurpose.TASK_EXECUTION, DisclosureSource.AUTHENTICATED_HOST,
        DisclosureTrust.TRUSTED_AUTHORITY, DisclosureGeneration.CURRENT,
        "host:primary-runtime-observer:v1", (DisclosureReasonCode.MINIMUM_NECESSARY,),
    )
    evidence_id = observation_id(sdk_run_id)
    envelope = SanitizedEvidenceEnvelope(
        evidence_id=evidence_id, run_id=sdk_run_id, subject=subject,
        source_kind=EvidenceSourceKind.RUNTIME_EVENT, source_ref=f"primary-runtime:{sdk_run_id}",
        source_hash=digest, sanitized_payload=payload, sanitized_hash=digest,
        filter_policy_version=POLICY, removed_spans=(), disclosure_context=disclosure, evidence_refs=(),
    )
    receipt = SanitizedEvidenceReceipt(
        receipt_id=f"primary-runtime-receipt:{evidence_id}", run_id=sdk_run_id, subject=subject,
        evidence_id=evidence_id, envelope_hash=envelope.envelope_hash, source_hash=digest,
        sanitized_hash=digest, filter_policy_version=POLICY, accepted=True,
        reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,), disclosure_context=disclosure,
        evidence_refs=(), admitted_at=occurred_at,
    )
    return envelope, receipt


async def terminal_observation_tx(db, *, host_run_id, sdk_run_id, subject):
    cursor = await db.execute(
        "SELECT e.* FROM human_memory_evidence e "
        "JOIN foreground_runs r ON r.host_run_id=? "
        "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id AND b.sdk_run_id=e.run_id "
        "WHERE e.evidence_id=? AND e.run_id=? AND e.subject=? "
        "AND e.primary_conversation_id=r.primary_conversation_id AND r.subject=e.subject "
        "AND e.source_kind='runtime_event' AND e.source_ref=?",
        (host_run_id, observation_id(sdk_run_id), sdk_run_id, subject, f"primary-runtime:{sdk_run_id}"),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    envelope = json.loads(row["envelope_json"])
    expected, _ = evidence_pair(subject, sdk_run_id, payload, float(row["occurred_at"]))
    if (envelope != dict(expected.to_json())
            or canonical_hash(payload) != row["sanitized_sha256"]
            or expected.envelope_hash != row["envelope_sha256"]
            or payload.get("host_run_id") != host_run_id
            or payload.get("sdk_run_id") != sdk_run_id):
        raise RuntimeError("primary_runtime_observation_corrupt")
    return row, payload


async def record_terminal_observation(db_path, *, host_run_id, sdk_run_id, subject,
                                      owner_id, generation, terminal, sdk_evidence, messages, error_code=None):
    from deskpet.execution.foreground_queue import ForegroundQueueStore
    queue = ForegroundQueueStore(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("BEGIN IMMEDIATE")
        await assert_human_memory_ingress_open_tx(db)
        await queue._validate_lease_tx(db, host_run_id, owner_id, generation, time.time())
        await queue._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
        prior = await terminal_observation_tx(db, host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject)
        if prior is not None:
            row, payload = prior
            if (payload["sdk_event_id"] != sdk_evidence.event_id
                    or payload["sdk_event_hash"] != sdk_evidence.event_hash
                    or payload["terminal_state"] != terminal.value
                    or payload["messages"] != list(messages)):
                raise RuntimeError("primary_runtime_terminal_conflict")
            await db.commit()
            return row["evidence_id"], row["envelope_sha256"]
        run = await queue._run_tx(db, host_run_id)
        if run["subject"] != subject:
            raise RuntimeError("primary_runtime_subject_mismatch")
        payload = {
            "schema_version": 1, "kind": "primary_run_terminal", "subject": subject,
            "host_run_id": host_run_id, "sdk_run_id": sdk_run_id, "turn_id": run["turn_id"],
            "generation": generation, "terminal_state": terminal.value,
            "sdk_event_id": sdk_evidence.event_id, "sdk_event_hash": sdk_evidence.event_hash,
            "messages": list(messages), "error_code": error_code,
        }
        reject_private_payload(payload)
        envelope, receipt = evidence_pair(subject, sdk_run_id, payload, float(sdk_evidence.occurred_at))
        committed = await HumanMemoryProgramStore(db_path).append_evidence_tx(
            db, envelope, receipt, primary_conversation_id=run["primary_conversation_id"], committed_at=time.time(),
        )
        await db.commit()
        return committed.evidence_id, committed.envelope_sha256


class PrimaryHistoryStore:
    def __init__(self, db_path: str | Path, *, settled_run_reader=None):
        self._db_path = db_path
        self._settled_run_reader = settled_run_reader

    async def read(self, *, subject: str, primary_ref: str, before_sequence: int,
                   limit: int = 10, completed_only: bool = False) -> tuple[dict, ...]:
        identifier(subject, "subject")
        identifier(primary_ref, "primary_ref")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("primary_history_limit_invalid")
        if type(before_sequence) is not int or before_sequence < 1:
            raise ValueError("primary_history_sequence_invalid")
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT r.host_run_id,r.task_scope_id,b.sdk_run_id,t.enqueue_sequence,t.turn_id,t.turn_json,"
                "f.terminal_receipt_id,f.receipt_hash,f.receipt_json,f.terminal_state "
                "FROM foreground_runs r JOIN foreground_turns t ON t.turn_id=r.turn_id "
                "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
                "JOIN foreground_terminal_receipts f ON f.host_run_id=r.host_run_id AND f.sdk_run_id=b.sdk_run_id "
                "WHERE r.subject=? AND r.primary_conversation_id=? AND t.enqueue_sequence<? "
                "AND (?=0 OR f.terminal_state='COMPLETED') "
                "ORDER BY t.enqueue_sequence DESC LIMIT ?",
                (subject, primary_ref, before_sequence, int(completed_only), limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            result = []
            for run in reversed(rows):
                from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
                identity = await read_primary_terminal_identity_tx(db, subject=subject, primary_ref=primary_ref,
                    host_run_id=run["host_run_id"], sdk_run_id=run["sdk_run_id"])
                if identity is None:
                    raise RuntimeError("primary_history_terminal_receipt_corrupt")
                receipt = json.loads(run["receipt_json"])
                if canonical_hash(receipt) != run["receipt_hash"]:
                    raise RuntimeError("primary_history_terminal_receipt_corrupt")
                found = await terminal_observation_tx(db, host_run_id=run["host_run_id"],
                                                      sdk_run_id=run["sdk_run_id"], subject=subject)
                if found is None:
                    if (receipt.get("primary_observation_ref") is not None
                            or receipt.get("terminal_authority_kind") == "primary_runtime_observation"
                            or run["task_scope_id"] is None
                            or not receipt.get("terminal_gate_receipt_id")):
                        raise RuntimeError("primary_history_observation_corrupt")
                    # Pre-S6 scoped Runs have no primary observation. Re-read
                    # their actual SDK terminal + Context, anchored by the
                    # already committed Host binding and terminal receipt.
                    if self._settled_run_reader is None:
                        raise RuntimeError("primary_history_observation_missing")
                    if canonical_hash(json.loads(run["receipt_json"])) != run["receipt_hash"]:
                        raise RuntimeError("primary_history_terminal_receipt_corrupt")
                    terminal, messages = self._settled_run_reader(
                        run["sdk_run_id"], current_text=json.loads(run["turn_json"])["payload"]["text"],
                    )
                    identity.verify_sdk_terminal(terminal)
                    result.append({
                        "source_ref": f"primary-terminal:{run['terminal_receipt_id']}",
                        "source_hash": canonical_hash({"host_receipt_hash": run["receipt_hash"],
                                                       "sdk_event_hash": terminal.event_hash, "messages": messages}),
                        "turn_id": run["turn_id"], "terminal_state": run["terminal_state"],
                        "messages": list(messages),
                    })
                    continue
                row, payload = found
                if self._settled_run_reader is not None:
                    terminal, messages = self._settled_run_reader(
                        run["sdk_run_id"], current_text=json.loads(run["turn_json"])["payload"]["text"])
                    identity.verify_sdk_terminal(terminal)
                    if list(messages) != payload["messages"]:
                        raise RuntimeError("primary_history_transcript_mismatch")
                if (receipt.get("primary_observation_ref") != row["evidence_id"]
                        or receipt.get("primary_observation_hash") != row["envelope_sha256"]):
                    raise RuntimeError("primary_history_observation_corrupt")
                result.append({"source_ref": row["evidence_id"], "source_hash": row["envelope_sha256"],
                               "turn_id": payload["turn_id"], "terminal_state": payload["terminal_state"],
                               "messages": payload["messages"]})
            return tuple(result)
