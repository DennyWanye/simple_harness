"""Durable Host side record of the assistant's own tool calls (HM-TO-A6 F-K1).

The archived terminal observation (``primary_run_terminal``) records assistant
items as ``{"role","content"}`` only, and its envelope hash covers that exact
shape. The arguments the assistant actually sent live in public SDK provider
records and are read back, verified, at terminal observation time
(``primary_tool_causality.read_tool_causality``). This module archives them in
Host state (v55 ``primary_assistant_tool_calls``) as a content-addressed row per
``(sdk_run_id, message_ordinal)``, bound to the one observation it belongs to
by ``(evidence_id, envelope_hash)``, and joins them back when a history group
is projected. Nothing here is a grant, and no archived envelope changes: an
observation without rows renders exactly as before.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from deskpet.task_scope.protocol import (
    TaskScopeProtocolError, canonical_hash, canonical_json, redact_credential_shapes,
    reject_private_payload,
)

KIND = "primary_assistant_tool_calls/v1"
GROUP_KEY = "assistant_tool_calls"
# Explicit successor composition, not acceptance of any future integer (same
# rule as the v3 contract gate in ``primary_history``).
SIDE_RECORD_USER_VERSIONS = (55,)
_CALL_KEYS = {"call_id", "name", "arguments"}

log = logging.getLogger(__name__)


def record_id(sdk_run_id: str, message_ordinal: int) -> str:
    return f"primary-assistant-tool-calls:{sdk_run_id}:{message_ordinal}"


def side_record_body(record, *, host_run_id):
    """One durable body from one verified reader projection.

    ``arguments`` becomes a single credential-redacted canonical JSON string:
    the same object the SDK stored in ``provider_invocations.response_json`` and
    ``execution_effects.arguments_json``, rendered the way the transcript
    projection renders every other model-written text.
    """
    calls = []
    for call in record["tool_calls"]:
        text, _ = redact_credential_shapes(canonical_json(call["arguments"]))
        calls.append(dict(call_id=call["call_id"], name=call["name"], arguments=text))
    return dict(schema_version=1, kind=KIND, sdk_run_id=record["sdk_run_id"], host_run_id=host_run_id,
                message_ordinal=record["message_ordinal"],
                provider_invocation_id=record["provider_invocation_id"],
                provider_turn_ordinal=record["provider_turn_ordinal"],
                provider_response_hash=record["provider_response_hash"], tool_calls=calls)


def consistent_with_transcript(body, messages) -> bool:
    """The record names an assistant item whose tool results follow it in order.

    ``read_tool_causality`` proved that every call of one provider turn is
    followed by exactly one tool item (result or archived denial) in call
    order, so the join re-checks that shape against the archived transcript
    instead of trusting the row.
    """
    ordinal = body.get("message_ordinal")
    calls = body.get("tool_calls")
    if (type(ordinal) is not int or not 1 < ordinal <= len(messages)
            or messages[ordinal - 1].get("role") != "assistant"
            or not isinstance(calls, list) or not calls
            or not all(isinstance(call, dict) and set(call) == _CALL_KEYS
                       and all(isinstance(call[key], str) for key in _CALL_KEYS) for call in calls)):
        return False
    following = messages[ordinal:ordinal + len(calls)]
    return len(following) == len(calls) and all(
        isinstance(item, dict) and item.get("role") == "tool"
        and item.get("call_id") == call["call_id"] and item.get("name") == call["name"]
        for item, call in zip(following, calls))


async def _side_record_installed(db) -> bool:
    cursor = await db.execute("PRAGMA user_version")
    version = (await cursor.fetchone())[0]
    await cursor.close()
    return version in SIDE_RECORD_USER_VERSIONS


async def write_assistant_tool_calls_tx(db, *, host_run_id, sdk_run_id, evidence_id, envelope_hash,
                                        messages, records, recorded_at) -> int:
    """Archive the side rows inside the terminal observation's own transaction.

    A row that cannot be made durable (private-shape rejection, transcript
    disagreement) is skipped with an identifier-only warning; it never fails
    the observation commit and never becomes a partial or invented row.
    """
    if not records or not await _side_record_installed(db):
        return 0
    written = 0
    for record in records:
        body = side_record_body(record, host_run_id=host_run_id)
        ordinal = body["message_ordinal"]
        reason = None
        if body["sdk_run_id"] != sdk_run_id or not consistent_with_transcript(body, messages):
            reason = "transcript_mismatch"
        else:
            try:
                reject_private_payload(body)
            except TaskScopeProtocolError as exc:
                reason = str(exc)
        if reason is not None:
            log.warning("primary_assistant_tool_calls_skipped sdk_run_id=%s message_ordinal=%s reason=%s",
                        sdk_run_id, ordinal, reason)
            continue
        try:
            await db.execute(
                "INSERT INTO primary_assistant_tool_calls(record_id,sdk_run_id,host_run_id,message_ordinal,"
                "observation_evidence_id,observation_envelope_hash,body_json,body_hash,recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (record_id(sdk_run_id, ordinal), sdk_run_id, host_run_id, ordinal, evidence_id, envelope_hash,
                 canonical_json(body), canonical_hash(body), float(recorded_at)),
            )
        except sqlite3.IntegrityError:
            # A row for this (sdk_run_id, ordinal) already exists — the append-only
            # table refuses the second write. SQLite rolls back the failed statement
            # only, so the terminal observation's own transaction stays intact: an
            # enrichment must never be able to fail an archival commit.
            log.warning("primary_assistant_tool_calls_skipped sdk_run_id=%s message_ordinal=%s reason=%s",
                        sdk_run_id, ordinal, "already_recorded")
            continue
        written += 1
    return written


async def read_assistant_tool_calls_tx(db, *, sdk_run_id, evidence_id, envelope_hash, messages) -> dict:
    """``{message_ordinal: [{call_id, name, arguments}, ...]}`` for one archived observation.

    Rows are verified against their own hash, their identity columns and the
    archived transcript. Any disagreement degrades the whole group to the
    pre-F-K1 rendering (no ``tool_calls``) with an identifier-only warning
    rather than failing the read: a side record is an enrichment of an
    already verified archive, never its authority.
    """
    if not await _side_record_installed(db):
        return {}
    cursor = await db.execute(
        "SELECT record_id,host_run_id,message_ordinal,body_json,body_hash FROM primary_assistant_tool_calls "
        "WHERE sdk_run_id=? AND observation_evidence_id=? AND observation_envelope_hash=? ORDER BY message_ordinal",
        (sdk_run_id, evidence_id, envelope_hash),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    result = {}
    for row in rows:
        ordinal = row["message_ordinal"]
        try:
            body = json.loads(row["body_json"])
            ok = (isinstance(body, dict) and body.get("kind") == KIND and body.get("schema_version") == 1
                  and body.get("sdk_run_id") == sdk_run_id and body.get("host_run_id") == row["host_run_id"]
                  and body.get("message_ordinal") == ordinal
                  and row["record_id"] == record_id(sdk_run_id, ordinal)
                  and canonical_hash(body) == row["body_hash"]
                  and consistent_with_transcript(body, messages))
        except (ValueError, TypeError):
            ok = False
        if not ok:
            log.warning("primary_assistant_tool_calls_ignored sdk_run_id=%s message_ordinal=%s",
                        sdk_run_id, ordinal)
            return {}
        result[int(ordinal)] = [dict(call) for call in body["tool_calls"]]
    return result
