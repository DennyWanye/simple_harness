"""Future terminal-only tool Scope provenance; no legacy source backfill.

Scope belongs to an individual settled tool fact, never to the latest route or
an inferred whole Run. The original SDK causal fact stays unchanged. These Host
references add no effect permission and do not change old v2 source encoding.

2026-09-09 HM-TO-A6 incident R: every one of the ~20 equality clauses below used
to raise the same opaque ``primary_message_scope_source_mismatch``, so a real
foreground stall named no clause and no fact.  Each clause now carries a stable,
payload-free ``reason_code`` (field names and the failing fact's item ordinal
only — never envelope bytes, tool arguments or results).  The stable top-level
``code`` strings are unchanged, so existing callers and audit greps still work.
"""
from __future__ import annotations

import json
from deskpet.memory import primary_message_v2 as v2
from deskpet.task_scope.protocol import canonical_hash, validate_execution_evidence
from deskpet.execution.evidence_ingress import _HostExecutionEvidence

CONTRACT = "primary-message-v3"

MISMATCH = "primary_message_scope_source_mismatch"
SOURCE_MISSING = "primary_message_scope_source_missing"
CONTROL_SOURCE_MISSING = "primary_message_scope_control_source_missing"
CONTRACT_MISMATCH = "primary_message_v3_contract_mismatch"

# The Host control producer (``ContextRouteLedgerStore``) imports its route tool
# fact through ``ingest_ledger_fact_tx``, which reserves without a tool name.
# The *same* effect may already hold a reservation made by the ordinary Tool
# dispatch path (``ToolAdapter._reserve_evidence``, ``tool_name='context_route'``)
# whenever the Run already had an admission scope when the model issued the call.
# Both spellings are legitimate for this one control Tool; any other tool name on
# the reservation means a physical invocation is being relabelled as control.
CONTROL_TOOL_NAME = "context_route"
CONTROL_PUBLIC_KEYS = frozenset(
    {"tool_name", "effect_id", "raw_call_id", "verdict", "decision_id", "proposal_hash"}
)
CONTROL_VERDICTS = frozenset({"accepted", "rejected", "clarification"})


class PrimaryScopeSourceError(RuntimeError):
    """Stable code plus the single clause that refused this source.

    ``str(exc)`` stays the historical stable code so nothing downstream has to
    change; ``reason_code`` names the clause and ``item_ordinal`` the transcript
    position of the fact it refused.  Both are Host-internal field names, never
    conversation content.
    """

    def __init__(self, code: str, reason_code: str, *, item_ordinal: object = None):
        self.code = code
        self.reason_code = reason_code
        self.item_ordinal = item_ordinal if type(item_ordinal) is int else None
        super().__init__(code)


def _check(condition, reason_code, *, ordinal=None, code=MISMATCH):
    if not condition:
        raise PrimaryScopeSourceError(code, reason_code, item_ordinal=ordinal)


async def _verify_route_control_tx(db, *, sdk_run_id, fact, public, reservation, ordinal=None):
    """Exact existing Host control producer, not a physical invocation proof."""
    from deskpet.sdk_adapters.context_authority import canonical_sha256
    _check(set(public) == set(CONTROL_PUBLIC_KEYS), "route_control_public_keys", ordinal=ordinal)
    _check(fact["tool_name"] == CONTROL_TOOL_NAME, "route_control_fact_tool_name", ordinal=ordinal)
    _check(public["tool_name"] == CONTROL_TOOL_NAME, "route_control_public_tool_name", ordinal=ordinal)
    _check(public["raw_call_id"] == fact.get("raw_call_id"), "route_control_raw_call_id", ordinal=ordinal)
    # 2026-09-09 HM-TO-A6 incident R: this used to demand ``tool_name IS NULL``.
    # That only held for a Run's *first* route call, which binds the admission
    # scope and therefore has no dispatch-time reservation at all.  A second,
    # mid-Run ``context_route`` — now routinely guided by the zero-hit
    # ``task_scope_search`` follow-up (c70f568f) — reserves through the ordinary
    # Tool path first and legitimately carries its own name.
    _check(reservation["tool_name"] in (None, CONTROL_TOOL_NAME),
           "route_control_reservation_tool_name", ordinal=ordinal)
    cursor = await db.execute('SELECT * FROM context_route_tool_invocations WHERE sdk_run_id=? AND effect_id=?',
                              (sdk_run_id, fact['effect_id']))
    row = await cursor.fetchone()
    _check(row is not None, "route_control_invocation_row", ordinal=ordinal, code=CONTROL_SOURCE_MISSING)
    body = dict(decision_id=row['decision_id'], detail=json.loads(row['detail_json']),
        effect_id=row['effect_id'], proposal_hash=row['proposal_hash'], raw_call_id=row['raw_call_id'],
        sdk_run_id=row['sdk_run_id'], verdict=row['verdict'])
    expected = dict(tool_name=CONTROL_TOOL_NAME, **{key: row[key] for key in
        ('effect_id', 'raw_call_id', 'verdict', 'decision_id', 'proposal_hash')})
    _check(row['invocation_id'] == f"route-invocation:{sdk_run_id}:{fact['effect_id']}",
           "route_control_invocation_id", ordinal=ordinal)
    _check(row['verdict'] in CONTROL_VERDICTS, "route_control_verdict", ordinal=ordinal)
    _check(canonical_sha256(body) == row['invocation_hash'], "route_control_invocation_hash", ordinal=ordinal)
    _check(public == expected, "route_control_public_payload", ordinal=ordinal)


async def read_scope_sources_tx(db, *, subject, sdk_run_id, facts):
    sources = []
    for fact in facts:
        ordinal = fact["item_ordinal"]
        source = dict(item_ordinal=ordinal, task_scope_id=None, ingest_receipt=None)
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
            _check(event is not None, "scope_event_row", ordinal=ordinal, code=SOURCE_MISSING)
            _check(reservation is not None, "scope_reservation_row", ordinal=ordinal, code=SOURCE_MISSING)
            _check(owner is not None, "scope_owner_row", ordinal=ordinal, code=SOURCE_MISSING)
            raw, digest = validate_execution_evidence(_HostExecutionEvidence(json.loads(event["payload_json"])))
            public = raw["public_payload"]
            _check(receipt["run_id"] == sdk_run_id, "receipt_run_id", ordinal=ordinal)
            _check(fact["sdk_run_id"] == sdk_run_id, "fact_sdk_run_id", ordinal=ordinal)
            _check(receipt["task_scope_id"] == event["task_scope_id"], "receipt_task_scope_id", ordinal=ordinal)
            _check(owner["subject"] == subject, "task_scope_owner_subject", ordinal=ordinal)
            _check(event["event_kind"] == "harness.tool_invocation", "event_kind", ordinal=ordinal)
            _check(event["source_kind"] == "harness", "event_source_kind", ordinal=ordinal)
            _check(event["source_event_id"] == "execution:" + event_key, "event_source_event_id", ordinal=ordinal)
            _check(raw["event_id"] == event_key, "evidence_event_id", ordinal=ordinal)
            _check(raw["run_id"] == sdk_run_id, "evidence_run_id", ordinal=ordinal)
            _check(raw["subject"] == subject, "evidence_subject", ordinal=ordinal)
            _check(raw["kind"] == "tool_invocation", "evidence_kind", ordinal=ordinal)
            _check(digest == receipt["evidence_hash"], "receipt_evidence_hash", ordinal=ordinal)
            _check(digest == event["payload_hash"], "event_payload_hash", ordinal=ordinal)
            _check(reservation["task_scope_id"] == receipt["task_scope_id"],
                   "reservation_task_scope_id", ordinal=ordinal)
            _check(reservation["run_id"] == sdk_run_id, "reservation_run_id", ordinal=ordinal)
            _check(reservation["kind"] == "tool_invocation", "reservation_kind", ordinal=ordinal)
            _check(reservation["status"] == "ingested", "reservation_status", ordinal=ordinal)
            _check(reservation["source_sequence"] == receipt["source_sequence"],
                   "reservation_source_sequence", ordinal=ordinal)
            _check(public.get("effect_id") == fact["effect_id"], "public_effect_id", ordinal=ordinal)
            if public.get('tool_name') == CONTROL_TOOL_NAME and 'verdict' in public:
                await _verify_route_control_tx(db, sdk_run_id=sdk_run_id, fact=fact,
                    public=public, reservation=reservation, ordinal=ordinal)
                sources.append(source)
                continue  # verified control lineage deliberately grants no Scope proof
            _check(public.get('call_id') == fact['internal_call_id'], "public_call_id", ordinal=ordinal)
            _check(public.get('tool_name') == fact['tool_name'], "public_tool_name", ordinal=ordinal)
            _check(public.get('effect_state') == fact['state'], "public_effect_state", ordinal=ordinal)
            source.update(task_scope_id=receipt["task_scope_id"], ingest_receipt={
                key: receipt[key] for key in ("receipt_id", "source_event_id", "run_id", "task_scope_id",
                    "source_sequence", "event_id", "evidence_hash", "evidence_kind", "committed_at")})
        sources.append(source)
    return sources


def _stored_source_reason(stored, expected):
    """Payload-free ``(reason_code, item_ordinal)`` for a persisted/derived diff."""
    if type(stored) is not list:
        return "terminal_scope_sources_type", None
    if len(stored) != len(expected):
        return "terminal_scope_sources_length", None
    for actual, wanted in zip(stored, expected):
        if actual == wanted:
            continue
        ordinal = wanted.get("item_ordinal")
        if type(actual) is not dict:
            return "terminal_scope_source_type", ordinal
        if set(actual) != set(wanted):
            return "terminal_scope_source_keys", ordinal
        key = next(name for name in sorted(wanted) if actual[name] != wanted[name])
        if key != "ingest_receipt" or type(actual[key]) is not dict or type(wanted[key]) is not dict:
            return f"terminal_scope_source_{key}", ordinal
        if set(actual[key]) != set(wanted[key]):
            return "terminal_scope_source_ingest_receipt_keys", ordinal
        field = next(name for name in sorted(wanted[key]) if actual[key][name] != wanted[key][name])
        return f"terminal_scope_source_ingest_receipt_{field}", ordinal
    return "terminal_scope_sources", None


async def verify_scope_sources_tx(db, terminal):
    payload = terminal.to_json()["sanitized_payload"]
    if payload.get("message_source_contract") != CONTRACT:
        raise PrimaryScopeSourceError(CONTRACT_MISMATCH, "message_source_contract")
    expected = await read_scope_sources_tx(db, subject=terminal.subject,
        sdk_run_id=terminal.run_id, facts=payload["tool_causal_sources"])
    stored = payload.get("tool_scope_sources")
    if stored != expected:
        reason, ordinal = _stored_source_reason(stored, expected)
        raise PrimaryScopeSourceError(MISMATCH, reason, item_ordinal=ordinal)


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
