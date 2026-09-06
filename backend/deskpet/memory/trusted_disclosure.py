"""Durable Host configuration and current turn/run disclosure resolution.

No model authority, SDK permission issuance, or heuristic text interpretation.
The only executable configuration in this leaf is the existing local SELF lane.
"""
from __future__ import annotations

import json
import time
import uuid

import aiosqlite
from simple_harness import (
    DeliveryRecipient, IntendedAudience, DisclosurePurpose, DisclosureContext,
    DisclosureSource, DisclosureTrust, DisclosureGeneration, DisclosureReasonCode,
)
from deskpet.task_scope.protocol import canonical_hash, canonical_json, identifier, digest
from deskpet.memory.writer_fence import (
    human_memory_connection, assert_human_memory_ingress_open_tx,
    require_authenticated_host_snapshot,
)


POLICY = {
    "policy_id": "host-local-disclosure/v1",
    "minimum_necessary": True,
    "chat_grants_authority": False,
    "configuration_grants_input_permit": False,
}
POLICY_HASH = canonical_hash(POLICY)


class TrustedDisclosureError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _reject(code):
    raise TrustedDisclosureError("host_disclosure_" + code)


async def _one(db, sql, args=()):
    cursor = await db.execute(sql, args)
    try:
        return await cursor.fetchone()
    finally:
        await cursor.close()


def _decode(row, subject):
    if row is None:
        _reject("binding_missing")
    raw = json.loads(row["binding_json"])
    if (type(raw["policy_generation"]) is not int
            or row["subject"] != subject or raw["subject"] != subject
            or raw["binding_ref"] != row["binding_ref"]
            or raw["source_ref"] != row["binding_ref"]
            or raw["policy_generation"] != row["policy_generation"]
            or canonical_hash(raw) != row["binding_hash"]
            or raw["policy_hash"] != POLICY_HASH or raw["policy_id"] != POLICY["policy_id"]
            or raw["source_origin"] not in {"host_default", "authenticated_control"}):
        _reject("binding_mismatch")
    return raw


async def current_record_tx(db, subject):
    row = await _one(db, "SELECT c.* FROM human_memory_disclosure_heads h "
        "JOIN human_memory_disclosure_configs c ON c.binding_ref=h.binding_ref WHERE h.subject=?", (subject,))
    return None if row is None else _decode(row, subject)


def binding_token(raw):
    return {"binding_ref": raw["binding_ref"], "binding_hash": canonical_hash(raw),
            "policy_generation": raw["policy_generation"]}


async def bound_record_tx(db, *, subject, token):
    """Exact immutable binding fact, independent of the current policy head.

    History origins use this reader; only current execution resolves the head.
    In particular, bool/float must not compare equal to an integer generation.
    """
    if (type(token) is not dict
            or set(token) != {"binding_ref", "binding_hash", "policy_generation"}
            or type(token["policy_generation"]) is not int or token["policy_generation"] < 1):
        _reject("binding_token_invalid")
    try:
        identifier(token["binding_ref"], "binding_ref", 512)
        digest(token["binding_hash"], "binding_hash")
    except (TypeError, ValueError):
        _reject("binding_token_invalid")
    row = await _one(db, "SELECT * FROM human_memory_disclosure_configs WHERE binding_ref=?", (token["binding_ref"],))
    raw = _decode(row, subject)
    if token != binding_token(raw):
        _reject("binding_mismatch")
    return raw


def assert_executable(raw, *, input_use=None):
    # Enabling more recipients requires the real SDK gate, source permit and
    # outbound wiring. A stored UI preference cannot enable those capabilities.
    if (raw["recipient"] != "user_self" or raw["recipient_id"] != raw["subject"]
            or raw["intended_audience"] != "user_self" or raw["purpose"] != "task_execution"):
        if (input_use is None or input_use["disclosure_binding"] != binding_token(raw)
                or input_use["selection"] != {key: raw[key] for key in (
                    "recipient", "recipient_id", "intended_audience", "purpose")}
                or raw["purpose"] != "task_execution"):
            _reject("runtime_semantics_unavailable")


async def _insert_tx(db, *, subject, principal_id, authority_ref, origin,
                     lease_ref, request_key, request_hash, selection, previous):
    ref = "host:disclosure:" + str(uuid.uuid4())
    raw = {
        "schema_version": 1, "binding_ref": ref, "subject": subject,
        "principal_id": principal_id, "authority_ref": authority_ref,
        "source_ref": ref, "source_origin": origin, "control_lease_ref": lease_ref,
        "policy_generation": 1 if previous is None else previous["policy_generation"] + 1,
        "previous_ref": None if previous is None else previous["binding_ref"],
        "policy_id": POLICY["policy_id"], "policy_hash": POLICY_HASH,
        "issued_at": time.time(), **selection,
    }
    await db.execute("INSERT INTO human_memory_disclosure_configs VALUES (?,?,?,?,?,?,?)",
        (ref, subject, raw["policy_generation"], request_key, request_hash, canonical_hash(raw), canonical_json(raw)))
    await db.execute("INSERT INTO human_memory_disclosure_heads VALUES (?,?) "
        "ON CONFLICT(subject) DO UPDATE SET binding_ref=excluded.binding_ref", (subject, ref))
    return _decode(await _one(db, "SELECT * FROM human_memory_disclosure_configs WHERE binding_ref=?", (ref,)), subject)


async def enqueue_binding_tx(db, *, subject, requested_ref, legacy=False, input_use=None):
    """Within the same fenced enqueue transaction; no evidence payload mutation."""
    current = await current_record_tx(db, subject)
    if legacy and requested_ref is None:
        if current is not None and current["source_origin"] != "host_default":
            _reject("legacy_policy_changed")
        return None
    if requested_ref is not None:
        row = await _one(db, "SELECT * FROM human_memory_disclosure_configs WHERE binding_ref=?", (requested_ref,))
        raw = _decode(row, subject)
        if current is None or raw["binding_ref"] != current["binding_ref"]:
            _reject("binding_stale")
    else:
        if current is None:
            current = await _insert_tx(db, subject=subject, principal_id=subject,
                authority_ref="host:local-self-default:v1", origin="host_default", lease_ref=None,
                request_key="host-default:v1", request_hash=canonical_hash({"subject": subject}),
                selection={"recipient": "user_self", "recipient_id": subject,
                           "intended_audience": "user_self", "purpose": "task_execution"}, previous=None)
        raw = current
    assert_executable(raw, input_use=input_use)
    return binding_token(raw)


class TrustedDisclosureStore:
    def __init__(self, path):
        self.path = path

    async def configure(self, *, auth, request_id, expected_ref, selection):
        # Require the actual connection scope, not just a constructible auth DTO.
        lease_ref = require_authenticated_host_snapshot(auth)
        if set(selection) != {"recipient", "recipient_id", "intended_audience", "purpose"}:
            _reject("configuration_fields_invalid")
        recipient = DeliveryRecipient(selection["recipient"])
        audience = IntendedAudience(selection["intended_audience"])
        purpose = DisclosurePurpose(selection["purpose"])
        if "unknown" in {recipient.value, audience.value, purpose.value}:
            _reject("configuration_unknown")
        recipient_id = selection["recipient_id"]
        if recipient == DeliveryRecipient.USER_SELF:
            if recipient_id not in (None, auth.subject):
                _reject("self_subject_mismatch")
            recipient_id = auth.subject
        elif recipient != DeliveryRecipient.PUBLIC:
            identifier(recipient_id, "recipient_id", 512)
        elif recipient_id is not None:
            identifier(recipient_id, "recipient_id", 512)
        chosen = {"recipient": recipient.value, "recipient_id": recipient_id,
                  "intended_audience": audience.value, "purpose": purpose.value}
        request_key = "control:" + identifier(request_id, "request_id", 512)
        if expected_ref is not None:
            identifier(expected_ref, "expected_ref", 512)
        request_hash = canonical_hash({"selection": chosen, "expected_ref": expected_ref})
        async with human_memory_connection(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            try:
                await assert_human_memory_ingress_open_tx(db)
                require_authenticated_host_snapshot(auth)
                existing = await _one(db, "SELECT * FROM human_memory_disclosure_configs WHERE subject=? AND request_key=?",
                                      (auth.subject, request_key))
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        _reject("configuration_idempotency_conflict")
                    result = _decode(existing, auth.subject)
                else:
                    current = await current_record_tx(db, auth.subject)
                    if expected_ref != (None if current is None else current["binding_ref"]):
                        _reject("configuration_changed")
                    result = await _insert_tx(db, subject=auth.subject, principal_id=auth.principal_id,
                        authority_ref=auth.authority_ref, origin="authenticated_control", lease_ref=lease_ref,
                        request_key=request_key, request_hash=request_hash, selection=chosen, previous=current)
                await db.commit()
                return result
            except BaseException:
                await db.rollback()
                raise

    async def current(self, *, auth):
        require_authenticated_host_snapshot(auth)
        async with human_memory_connection(self.path) as db:
            db.row_factory = aiosqlite.Row
            current = await current_record_tx(db, auth.subject)
            return {"configuration": current}


async def resolve_current_disclosure(*, db_path, subject, run_id, request_id, turn_id=None):
    """Resolve Host-owned turn identity before admission, SDK run identity after it."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN")
        if turn_id is None:
            row = await _one(db, "SELECT t.* FROM foreground_runs r "
                "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
                "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject WHERE b.sdk_run_id=?", (run_id,))
        else:
            row = await _one(db, "SELECT * FROM foreground_turns WHERE turn_id=?", (turn_id,))
        if row is None or row["subject"] != subject:
            _reject("turn_subject_mismatch")
        turn = json.loads(row["turn_json"])
        if canonical_hash(turn) != row["turn_hash"] or turn["subject"] != subject:
            _reject("turn_hash_mismatch")
        token = turn.get("disclosure_binding")
        current = await current_record_tx(db, subject)
        if "disclosure_binding" not in turn:
            if current is not None and current["source_origin"] != "host_default":
                _reject("legacy_policy_changed")
            # Existing persisted pre-v48 turns retain the original SELF lane.
            from deskpet.execution.primary_dependencies import current_disclosure
            return current_disclosure(run_id=run_id, subject=subject, request_id=request_id)
        await bound_record_tx(db, subject=subject, token=token)
        if current is None or token != binding_token(current):
            _reject("binding_stale")
        input_use = None
        if "input_use" in turn:
            from deskpet.memory.current_input_source import read_input_use_tx
            _, _, input_use = await read_input_use_tx(db, turn=row, config=current)
        assert_executable(current, input_use=input_use)
        return DisclosureContext(run_id, subject, DeliveryRecipient(current["recipient"]),
            current["recipient_id"], IntendedAudience(current["intended_audience"]),
            DisclosurePurpose(current["purpose"]), DisclosureSource.AUTHENTICATED_HOST,
            DisclosureTrust.TRUSTED_AUTHORITY, DisclosureGeneration.CURRENT,
            current["source_ref"] + ":" + canonical_hash({"binding": token, "request_id": request_id}),
            (DisclosureReasonCode.MINIMUM_NECESSARY,))
