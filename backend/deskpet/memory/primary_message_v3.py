"""Future terminal-only tool Scope provenance; no legacy source backfill.

Scope belongs to an individual settled tool fact, never to the latest route or
an inferred whole Run. The original SDK causal fact stays unchanged. These Host
references add no effect permission and do not change old v2 source encoding.
"""
from __future__ import annotations

import json
from deskpet.memory import primary_message_v2 as v2
from deskpet.task_scope.protocol import canonical_hash, validate_execution_evidence
from deskpet.execution.evidence_ingress import _HostExecutionEvidence

CONTRACT = "primary-message-v3"


async def read_scope_sources_tx(db, *, subject, sdk_run_id, facts):
    sources = []
    for fact in facts:
        source = dict(item_ordinal=fact["item_ordinal"], task_scope_id=None, ingest_receipt=None)
        event_key = "effect:" + fact["effect_id"]
        cursor = await db.execute(
            "SELECT * FROM task_scope_execution_ingest_receipts WHERE source_event_id=?", (event_key,))
        receipt = await cursor.fetchone()
        # A control-tool ledger fact or an unscoped call cannot be relabelled as
        # a scoped ToolInvocationFact. Such a source carries no Scope proof.
        if receipt is not None and receipt["evidence_kind"] == "tool_invocation":
            cursor = await db.execute("SELECT * FROM task_scope_events WHERE event_id=?", (receipt["event_id"],))
            event = await cursor.fetchone()
            cursor = await db.execute("SELECT * FROM harness_evidence_reservations WHERE source_event_id=?", (event_key,))
            reservation = await cursor.fetchone()
            cursor = await db.execute("SELECT subject FROM task_scopes WHERE task_scope_id=?", (receipt["task_scope_id"],))
            owner = await cursor.fetchone()
            if event is None or reservation is None or owner is None:
                raise RuntimeError("primary_message_scope_source_missing")
            raw, digest = validate_execution_evidence(_HostExecutionEvidence(json.loads(event["payload_json"])))
            public = raw["public_payload"]
            if (receipt["run_id"] != sdk_run_id or fact["sdk_run_id"] != sdk_run_id
                    or receipt["task_scope_id"] != event["task_scope_id"] or owner["subject"] != subject
                    or event["event_kind"] != "harness.tool_invocation" or event["source_kind"] != "harness"
                    or event["source_event_id"] != "execution:" + event_key
                    or raw["event_id"] != event_key or raw["run_id"] != sdk_run_id or raw["subject"] != subject
                    or raw["kind"] != "tool_invocation"
                    or digest != receipt["evidence_hash"] or digest != event["payload_hash"]
                    or reservation["task_scope_id"] != receipt["task_scope_id"]
                    or reservation["run_id"] != sdk_run_id or reservation["kind"] != "tool_invocation"
                    or reservation["status"] != "ingested" or reservation["source_sequence"] != receipt["source_sequence"]
                    or public.get("effect_id") != fact["effect_id"]
                    or public.get("call_id") != fact["internal_call_id"]
                    or public.get("tool_name") != fact["tool_name"] or public.get("effect_state") != fact["state"]):
                raise RuntimeError("primary_message_scope_source_mismatch")
            source.update(task_scope_id=receipt["task_scope_id"], ingest_receipt={
                key: receipt[key] for key in ("receipt_id", "source_event_id", "run_id", "task_scope_id",
                    "source_sequence", "event_id", "evidence_hash", "evidence_kind", "committed_at")})
        sources.append(source)
    return sources


async def verify_scope_sources_tx(db, terminal):
    payload = terminal.to_json()["sanitized_payload"]
    if payload.get("message_source_contract") != CONTRACT:
        raise RuntimeError("primary_message_v3_contract_mismatch")
    expected = await read_scope_sources_tx(db, subject=terminal.subject,
        sdk_run_id=terminal.run_id, facts=payload["tool_causal_sources"])
    if payload.get("tool_scope_sources") != expected:
        raise RuntimeError("primary_message_scope_source_mismatch")


def pairs(terminal, terminal_receipt, user, *, host_run_id):
    return v2._pairs(terminal, terminal_receipt, user, host_run_id=host_run_id,
        contract=CONTRACT, schema_version=3)


async def verify(db, *, primary_ref, host_run_id, terminal, terminal_receipt, user):
    from deskpet.memory.primary_visibility import read_evidence_pair
    await verify_scope_sources_tx(db, terminal)
    expected = pairs(terminal, terminal_receipt, user, host_run_id=host_run_id)
    for envelope, receipt in expected:
        actual = await read_evidence_pair(db=db, subject=user.subject,
            primary_ref=primary_ref, evidence_id=envelope.evidence_id)
        if actual != (envelope, receipt):
            raise RuntimeError("primary_message_v3_source_corrupt")
    return expected
