"""技能安装校验的存储读写：校验尝试的分配、比较并交换、作废与证明（从 capabilities/store 挪出）。"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from deskpet.capabilities.contracts import JsonValue, canonical_json, fingerprint_json
from deskpet.capabilities.store_domains.core import _skill_install_intent_from_row
from deskpet.capabilities.store_domains.support import (
    CapabilityStoreConflict,
    CapabilityStoreError,
    _json_object,
)
from deskpet.execution.contracts import root_idempotency_key


@dataclass(frozen=True, slots=True)
class CapabilitySkillInstallVerificationAttempt:
    attempt_id: str
    intent_id: str
    attempt_generation: int
    state_version: int
    status: str
    verifier_session_id: str
    request_id: str
    turn_id: str
    expected_run_id: str
    actual_run_id: str | None
    manager_operation_id: str
    manager_receipt_hash: str
    committed_set_stamp: str
    project_scope_key: str
    expected_member_set_stamp: str
    lease_intent_id: str | None
    lease_intent_hash: str | None
    capability_snapshot_ref: str | None
    run_catalog_content_stamp: str | None
    process_catalog_stamp: str | None
    projection_receipt_id: str | None
    projection_receipt_hash: str | None
    terminal_event_id: str | None
    terminal_event_hash: str | None
    evidence_hash: str | None
    release_receipt_id: str | None
    release_receipt_hash: str | None
    release_owner_event_hash: str | None
    migration_classification: str | None
    migration_classification_hash: str | None
    superseded_by_attempt_id: str | None
    error: Mapping[str, JsonValue] | None
    created_at: float
    updated_at: float
    terminal_at: float | None


@dataclass(frozen=True, slots=True)
class CapabilitySkillInstallVerificationAttestation:
    attestation_id: str
    intent_id: str
    attempt_id: str | None
    provenance: str
    runtime_proof_valid: bool
    verification_ref: str
    evidence_hash: str | None
    attestation: Mapping[str, JsonValue]
    created_at: float


def _verification_attempt_from_row(
    row: Mapping[str, Any],
) -> CapabilitySkillInstallVerificationAttempt:
    optional = lambda name: None if name not in row.keys() or row[name] is None else str(row[name])
    return CapabilitySkillInstallVerificationAttempt(
        attempt_id=str(row["attempt_id"]),
        intent_id=str(row["intent_id"]),
        attempt_generation=int(row["attempt_generation"]),
        state_version=int(row["state_version"]),
        status=str(row["status"]),
        verifier_session_id=str(row["verifier_session_id"]),
        request_id=str(row["request_id"]),
        turn_id=str(row["turn_id"]),
        expected_run_id=str(row["expected_run_id"]),
        actual_run_id=None if row["actual_run_id"] is None else str(row["actual_run_id"]),
        manager_operation_id=str(row["manager_operation_id"]),
        manager_receipt_hash=str(row["manager_receipt_hash"]),
        committed_set_stamp=str(row["committed_set_stamp"]),
        project_scope_key=str(row["project_scope_key"]),
        expected_member_set_stamp=str(row["expected_member_set_stamp"]),
        lease_intent_id=optional("lease_intent_id"),
        lease_intent_hash=optional("lease_intent_hash"),
        capability_snapshot_ref=optional("capability_snapshot_ref"),
        run_catalog_content_stamp=optional("run_catalog_content_stamp"),
        process_catalog_stamp=optional("process_catalog_stamp"),
        projection_receipt_id=optional("projection_receipt_id"),
        projection_receipt_hash=optional("projection_receipt_hash"),
        terminal_event_id=None
        if row["terminal_event_id"] is None
        else str(row["terminal_event_id"]),
        terminal_event_hash=None
        if row["terminal_event_hash"] is None
        else str(row["terminal_event_hash"]),
        evidence_hash=optional("evidence_hash"),
        release_receipt_id=optional("release_receipt_id"),
        release_receipt_hash=optional("release_receipt_hash"),
        release_owner_event_hash=optional("release_owner_event_hash"),
        migration_classification=optional("migration_classification"),
        migration_classification_hash=optional("migration_classification_hash"),
        superseded_by_attempt_id=None
        if row["superseded_by_attempt_id"] is None
        else str(row["superseded_by_attempt_id"]),
        error=_json_object(None if row["error_json"] is None else str(row["error_json"])),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        terminal_at=None if row["terminal_at"] is None else float(row["terminal_at"]),
    )


def _verification_attestation_from_row(
    row: Mapping[str, Any],
) -> CapabilitySkillInstallVerificationAttestation:
    payload = _json_object(str(row["attestation_json"]))
    if payload is None:
        raise CapabilityStoreError("corrupt_json", "attestation is absent")
    return CapabilitySkillInstallVerificationAttestation(
        attestation_id=str(row["attestation_id"]),
        intent_id=str(row["intent_id"]),
        attempt_id=None if row["attempt_id"] is None else str(row["attempt_id"]),
        provenance=str(row["provenance"]),
        runtime_proof_valid=bool(row["runtime_proof_valid"]),
        verification_ref=str(row["verification_ref"]),
        evidence_hash=None if row["evidence_hash"] is None else str(row["evidence_hash"]),
        attestation=payload,
        created_at=float(row["created_at"]),
    )


async def _get_skill_install_verification_attempt(self, attempt_id: str):
    async with self.read_connection() as db:
        row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
                (attempt_id,),
            )
        ).fetchone()
        return None if row is None else _verification_attempt_from_row(row)


async def _get_current_skill_install_verification_attempt(self, intent_id: str):
    async with self.read_connection() as db:
        row = await (
            await db.execute(
                "SELECT a.* FROM capability_skill_install_intents i JOIN "
                "capability_skill_install_verification_attempts a "
                "ON a.attempt_id=i.current_verification_attempt_id "
                "WHERE i.intent_id=?",
                (intent_id,),
            )
        ).fetchone()
        return None if row is None else _verification_attempt_from_row(row)


async def _list_pending_skill_install_verification_attempts(
    self,
    *,
    limit: int = 100,
):
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    async with self.read_connection() as db:
        rows = await (
            await db.execute(
                "SELECT a.* FROM capability_skill_install_intents i JOIN "
                "capability_skill_install_verification_attempts a "
                "ON a.attempt_id=i.current_verification_attempt_id "
                "WHERE a.status IN ('allocated','start_submitted','run_durable',"
                "'catalog_ready','page_in_proven','terminal_observed','lease_released','unknown') "
                "ORDER BY a.updated_at,a.intent_id,a.attempt_generation LIMIT ?",
                (limit,),
            )
        ).fetchall()
        return tuple(_verification_attempt_from_row(row) for row in rows)


async def _list_succeeded_global_skill_install_intents(
    self,
    *,
    limit: int = 100,
):
    """Return attested global installs that may still need binding activation."""

    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    async with self.read_connection() as db:
        rows = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_intents "
                "WHERE status='succeeded' "
                "AND current_verification_attempt_id IS NOT NULL "
                "AND json_extract(source_json, '$.schema')="
                "'global-skill-install-source-v2' "
                "ORDER BY updated_at,intent_id LIMIT ?",
                (limit,),
            )
        ).fetchall()
        return tuple(_skill_install_intent_from_row(row) for row in rows)


async def _get_skill_install_verification_attestation(self, intent_id: str):
    async with self.read_connection() as db:
        rows = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_verification_attestations "
                "WHERE intent_id=? ORDER BY created_at DESC,attestation_id DESC",
                (intent_id,),
            )
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attestation_ambiguous",
                "intent has multiple verification attestations",
            )
        return _verification_attestation_from_row(rows[0])


async def _allocate_skill_install_verification_attempt(
    self,
    intent_id: str,
    *,
    expected_state_version: int,
    manager_operation_id: str,
    manager_receipt_hash: str,
    committed_set_stamp: str,
    project_scope_key: str,
    expected_member_set_stamp: str,
    verifier_session_id: str,
):
    async with self.write_transaction() as db:
        row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("skill_install_intent_not_found", intent_id)
        intent = _skill_install_intent_from_row(row)
        if intent.current_verification_attempt_id:
            current = await (
                await db.execute(
                    "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
                    (intent.current_verification_attempt_id,),
                )
            ).fetchone()
            if current is None:
                raise CapabilityStoreConflict(
                    "skill_install_verification_attempt_missing", "current attempt is missing"
                )
            existing = _verification_attempt_from_row(current)
            if (
                existing.manager_operation_id != manager_operation_id
                or existing.manager_receipt_hash != manager_receipt_hash
                or existing.committed_set_stamp != committed_set_stamp
                or existing.project_scope_key != project_scope_key
                or existing.expected_member_set_stamp != expected_member_set_stamp
                or existing.verifier_session_id != verifier_session_id
            ):
                raise CapabilityStoreConflict(
                    "skill_install_verification_attempt_conflict",
                    "current attempt differs from immutable allocation",
                )
            return existing
        if (
            intent.state_version != expected_state_version
            or intent.status != "published_pending_runtime_verification"
        ):
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict",
                "intent cannot allocate verification",
            )
        generation = intent.verification_attempt_generation + 1
        identity = {
            "domain": "skill-install-verification-attempt-v1",
            "intent_id": intent_id,
            "attempt_generation": generation,
            "manager_operation_id": manager_operation_id,
            "manager_receipt_hash": manager_receipt_hash,
            "committed_set_stamp": committed_set_stamp,
            "project_scope_key": project_scope_key,
            "expected_member_set_stamp": expected_member_set_stamp,
            "verifier_session_id": verifier_session_id,
        }
        attempt_id = fingerprint_json(identity)
        request_id = f"skill-install-verify-request:{attempt_id}"
        turn_id = f"skill-install-verify-turn:{attempt_id}"
        root_key = root_idempotency_key(verifier_session_id, request_id, turn_id)
        expected_run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{root_key}").hex
        now = self._clock()
        await db.execute(
            "INSERT INTO capability_skill_install_verification_attempts("
            "attempt_id,intent_id,attempt_generation,state_version,status,verifier_session_id,"
            "request_id,turn_id,expected_run_id,manager_operation_id,manager_receipt_hash,"
            "committed_set_stamp,project_scope_key,expected_member_set_stamp,created_at,updated_at) "
            "VALUES(?,?,?,1,'allocated',?,?,?,?,?,?,?,?,?,?,?)",
            (
                attempt_id,
                intent_id,
                generation,
                verifier_session_id,
                request_id,
                turn_id,
                expected_run_id,
                manager_operation_id,
                manager_receipt_hash,
                committed_set_stamp,
                project_scope_key,
                expected_member_set_stamp,
                now,
                now,
            ),
        )
        changed = await db.execute(
            "UPDATE capability_skill_install_intents SET verification_attempt_generation=?,"
            "current_verification_attempt_id=?,state_version=state_version+1,updated_at=? "
            "WHERE intent_id=? AND state_version=? AND current_verification_attempt_id IS NULL",
            (generation, attempt_id, now, intent_id, expected_state_version),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict", "attempt allocation lost CAS"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
                (attempt_id,),
            )
        ).fetchone()
        return _verification_attempt_from_row(row)


async def _cas_skill_install_verification_attempt(
    self,
    attempt_id: str,
    *,
    expected_state_version: int,
    status: str,
    **fields: Any,
):
    transitions = {
        "allocated": {"start_submitted", "unknown", "terminal_failed"},
        "start_submitted": {"run_durable", "unknown", "terminal_failed"},
        "run_durable": {"catalog_ready", "unknown", "terminal_failed"},
        "catalog_ready": {"page_in_proven", "unknown", "terminal_failed"},
        "page_in_proven": {"terminal_observed", "unknown", "terminal_failed"},
        "terminal_observed": {"lease_released", "unknown", "terminal_failed"},
        "unknown": {
            "start_submitted",
            "run_durable",
            "catalog_ready",
            "page_in_proven",
            "terminal_observed",
            "lease_released",
            "terminal_failed",
            "superseded",
        },
        "terminal_failed": {"superseded"},
    }
    async with self.write_transaction() as db:
        row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
                (attempt_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("skill_install_verification_attempt_not_found", attempt_id)
        current = _verification_attempt_from_row(row)
        if current.state_version != expected_state_version or status not in transitions.get(
            current.status, set()
        ):
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict", "attempt transition conflict"
            )
        allowed = (
            "actual_run_id",
            "lease_intent_id",
            "lease_intent_hash",
            "capability_snapshot_ref",
            "run_catalog_content_stamp",
            "process_catalog_stamp",
            "projection_receipt_id",
            "projection_receipt_hash",
            "terminal_event_id",
            "terminal_event_hash",
            "evidence_hash",
            "release_receipt_id",
            "release_receipt_hash",
            "release_owner_event_hash",
            "superseded_by_attempt_id",
        )
        values = [fields.get(name) for name in allowed]
        for name in allowed:
            supplied = fields.get(name)
            existing = getattr(current, name)
            if supplied is not None and existing is not None and str(supplied) != str(existing):
                raise CapabilityStoreConflict(
                    "skill_install_verification_attempt_fact_conflict",
                    f"immutable verification fact differs: {name}",
                )
        effective = {
            name: fields.get(name) if fields.get(name) is not None else getattr(current, name)
            for name in allowed
        }
        required = {
            "run_durable": ("actual_run_id",),
            "catalog_ready": (
                "actual_run_id",
                "lease_intent_id",
                "lease_intent_hash",
                "capability_snapshot_ref",
                "run_catalog_content_stamp",
                "process_catalog_stamp",
                "projection_receipt_id",
                "projection_receipt_hash",
            ),
            "page_in_proven": ("evidence_hash",),
            "terminal_observed": ("terminal_event_id", "terminal_event_hash"),
            "lease_released": (
                "release_receipt_id",
                "release_receipt_hash",
                "release_owner_event_hash",
            ),
        }.get(status, ())
        if any(not effective.get(name) for name in required):
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_fact_incomplete",
                f"verification phase lacks required facts: {status}",
            )
        now = self._clock()
        changed = await db.execute(
            "UPDATE capability_skill_install_verification_attempts SET status=?,state_version=state_version+1,"
            + ",".join(f"{name}=COALESCE(?,{name})" for name in allowed)
            + ",error_json=?,updated_at=?,terminal_at=? WHERE attempt_id=? AND state_version=?",
            (
                status,
                *values,
                None if fields.get("error") is None else canonical_json(dict(fields["error"])),
                now,
                now if status.startswith("terminal_") else None,
                attempt_id,
                expected_state_version,
            ),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict", "attempt CAS lost"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
                (attempt_id,),
            )
        ).fetchone()
        return _verification_attempt_from_row(row)


async def _attach_skill_install_verification_attestation(
    self,
    intent_id: str,
    *,
    expected_intent_state_version: int,
    attempt_id: str,
    expected_attempt_state_version: int,
    attestation: Mapping[str, JsonValue],
):
    evidence_hash = str(attestation.get("evidence_hash") or "")
    verification_ref = fingerprint_json(dict(attestation))
    attestation_id = fingerprint_json(
        {"intent_id": intent_id, "attempt_id": attempt_id, "verification_ref": verification_ref}
    )
    async with self.write_transaction() as db:
        attempt = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=? AND intent_id=?",
                (attempt_id, intent_id),
            )
        ).fetchone()
        intent = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
            )
        ).fetchone()
        if (
            attempt is None
            or intent is None
            or str(attempt["status"]) != "lease_released"
            or int(attempt["state_version"]) != expected_attempt_state_version
            or int(intent["state_version"]) != expected_intent_state_version
            or str(intent["current_verification_attempt_id"] or "") != attempt_id
            or not evidence_hash
        ):
            raise CapabilityStoreConflict(
                "skill_install_verification_attestation_cas_conflict",
                "attestation authority differs",
            )
        now = self._clock()
        await db.execute(
            "INSERT INTO capability_skill_install_verification_attestations(attestation_id,intent_id,attempt_id,provenance,runtime_proof_valid,verification_ref,evidence_hash,attestation_json,created_at) VALUES(?,?,?,'runtime_v3',1,?,?,?,?)",
            (
                attestation_id,
                intent_id,
                attempt_id,
                verification_ref,
                evidence_hash,
                canonical_json(dict(attestation)),
                now,
            ),
        )
        transitioned = await db.execute(
            "UPDATE capability_skill_install_verification_attempts SET status='attested',"
            "state_version=state_version+1,updated_at=? WHERE attempt_id=? AND state_version=? "
            "AND status='lease_released'",
            (now, attempt_id, expected_attempt_state_version),
        )
        if transitioned.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attestation_cas_conflict",
                "attempt attestation CAS lost",
            )
        changed = await db.execute(
            "UPDATE capability_skill_install_intents SET status='succeeded',verification_ref=?,state_version=state_version+1,updated_at=? WHERE intent_id=? AND state_version=? AND current_verification_attempt_id=? AND status='published_pending_runtime_verification'",
            (verification_ref, now, intent_id, expected_intent_state_version, attempt_id),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attestation_cas_conflict", "intent attestation CAS lost"
            )
        return CapabilitySkillInstallVerificationAttestation(
            attestation_id=attestation_id,
            intent_id=intent_id,
            attempt_id=attempt_id,
            provenance="runtime_v3",
            runtime_proof_valid=True,
            verification_ref=verification_ref,
            evidence_hash=evidence_hash,
            attestation=dict(attestation),
            created_at=now,
        )


async def _supersede_skill_install_verification_attempt(
    self,
    intent_id: str,
    *,
    expected_intent_state_version: int,
    attempt_id: str,
    expected_attempt_state_version: int,
    verifier_session_id: str,
):
    """Atomically replace one unresolved/failed attempt with its next generation."""
    async with self.write_transaction() as db:
        intent_row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_intents WHERE intent_id=?", (intent_id,)
            )
        ).fetchone()
        attempt_row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_verification_attempts "
                "WHERE attempt_id=? AND intent_id=?",
                (attempt_id, intent_id),
            )
        ).fetchone()
        if intent_row is None or attempt_row is None:
            raise CapabilityStoreError("skill_install_verification_attempt_not_found", attempt_id)
        old = _verification_attempt_from_row(attempt_row)
        if (
            int(intent_row["state_version"]) != expected_intent_state_version
            or str(intent_row["current_verification_attempt_id"] or "") != attempt_id
            or old.state_version != expected_attempt_state_version
            or old.status not in {"unknown", "terminal_failed"}
        ):
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict",
                "attempt supersession authority differs",
            )
        generation = old.attempt_generation + 1
        identity = {
            "domain": "skill-install-verification-attempt-v1",
            "intent_id": intent_id,
            "attempt_generation": generation,
            "manager_operation_id": old.manager_operation_id,
            "manager_receipt_hash": old.manager_receipt_hash,
            "committed_set_stamp": old.committed_set_stamp,
            "project_scope_key": old.project_scope_key,
            "expected_member_set_stamp": old.expected_member_set_stamp,
            "verifier_session_id": verifier_session_id,
        }
        successor_id = fingerprint_json(identity)
        request_id = f"skill-install-verify-request:{successor_id}"
        turn_id = f"skill-install-verify-turn:{successor_id}"
        root_key = root_idempotency_key(verifier_session_id, request_id, turn_id)
        expected_run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{root_key}").hex
        now = self._clock()
        changed = await db.execute(
            "UPDATE capability_skill_install_verification_attempts SET "
            "status='superseded',state_version=state_version+1,updated_at=? "
            "WHERE attempt_id=? AND state_version=? AND status IN ('unknown','terminal_failed')",
            (now, attempt_id, expected_attempt_state_version),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict", "attempt supersession lost CAS"
            )
        await db.execute(
            "INSERT INTO capability_skill_install_verification_attempts("
            "attempt_id,intent_id,attempt_generation,state_version,status,verifier_session_id,"
            "request_id,turn_id,expected_run_id,manager_operation_id,manager_receipt_hash,"
            "committed_set_stamp,project_scope_key,expected_member_set_stamp,created_at,updated_at) "
            "VALUES(?,?,?,1,'allocated',?,?,?,?,?,?,?,?,?,?,?)",
            (
                successor_id,
                intent_id,
                generation,
                verifier_session_id,
                request_id,
                turn_id,
                expected_run_id,
                old.manager_operation_id,
                old.manager_receipt_hash,
                old.committed_set_stamp,
                old.project_scope_key,
                old.expected_member_set_stamp,
                now,
                now,
            ),
        )
        await db.execute(
            "UPDATE capability_skill_install_verification_attempts "
            "SET superseded_by_attempt_id=? WHERE attempt_id=?",
            (successor_id, attempt_id),
        )
        changed = await db.execute(
            "UPDATE capability_skill_install_intents SET verification_attempt_generation=?,"
            "current_verification_attempt_id=?,state_version=state_version+1,updated_at=? "
            "WHERE intent_id=? AND state_version=? AND current_verification_attempt_id=?",
            (generation, successor_id, now, intent_id, expected_intent_state_version, attempt_id),
        )
        if changed.rowcount != 1:
            raise CapabilityStoreConflict(
                "skill_install_verification_attempt_cas_conflict", "intent supersession lost CAS"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_skill_install_verification_attempts WHERE attempt_id=?",
                (successor_id,),
            )
        ).fetchone()
        return _verification_attempt_from_row(row)
