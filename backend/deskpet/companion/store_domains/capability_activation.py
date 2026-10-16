"""Capability mutation receipts, activation proofs and guard incidents."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from ..contracts import (
    CapabilityGuardIncident,
    CapabilityMutationReceipt,
    CompanionConflictError,
    CompanionStateError,
    OwnerRef,
)
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class CapabilityActivationMixin(CompanionStoreCore):
    """Capability mutation receipts, activation proofs and guard incidents."""

    __slots__ = ()

    def settle_capability_mutation_receipt(
        self,
        owner: OwnerRef,
        receipt: CapabilityMutationReceipt,
    ) -> Mapping[str, Any]:
        """Settle a trusted Manager receipt and all Companion projections once."""

        now = self._now()
        action = receipt.action.value
        candidate_mode = receipt.candidate_mode.value if receipt.candidate_mode else None
        with self._write() as db:
            self._require_owner(db, owner)
            request = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=? AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    receipt.activation_request_id,
                ),
            ).fetchone()
            if request is None:
                raise CompanionStateError("capability_mutation_request_missing")
            existing = db.execute(
                """SELECT * FROM capability_activation_receipts
                   WHERE profile_id=? AND profile_generation=?
                     AND (activation_request_id=? OR manager_operation_id=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    receipt.activation_request_id,
                    receipt.manager_operation_id,
                ),
            ).fetchone()
            expected_receipt = {
                "activation_request_id": receipt.activation_request_id,
                "manager_operation_id": receipt.manager_operation_id,
                "action": action,
                "candidate_mode": candidate_mode,
                "pack_id": receipt.pack_id,
                "version": receipt.version,
                "manifest_hash": receipt.manifest_hash,
                "source_fence_hash": receipt.source_fence_hash,
                "runtime_set_ref": receipt.runtime_set_ref,
                "runtime_set_hash": receipt.runtime_set_hash,
                "target_owner_key": receipt.target_owner_key,
                "target_scope": receipt.target_scope,
                "binding_generation": receipt.binding_generation,
                "owner_binding_set_stamp": receipt.owner_binding_set_stamp,
                "process_projection_fingerprint": receipt.process_projection_fingerprint,
                "target_user_owner_binding_set_stamp": receipt.target_user_owner_binding_set_stamp,
                "fallback_owner_key": receipt.fallback_owner_key,
                "fallback_scope": receipt.fallback_scope,
                "fallback_scope_key": receipt.fallback_scope_key,
                "fallback_binding_id": receipt.fallback_binding_id,
                "fallback_binding_generation": receipt.fallback_binding_generation,
                "fallback_pack_id": receipt.fallback_pack_id,
                "fallback_version": receipt.fallback_version,
                "fallback_manifest_hash": receipt.fallback_manifest_hash,
                "fallback_owner_binding_set_stamp": receipt.fallback_owner_binding_set_stamp,
                "fallback_process_projection_fingerprint": receipt.fallback_process_projection_fingerprint,
                "fallback_runtime_set_ref": receipt.fallback_runtime_set_ref,
                "fallback_runtime_set_hash": receipt.fallback_runtime_set_hash,
                "absence_proof_hash": receipt.absence_proof_hash,
                "result_hash": receipt.result_hash,
                "schema_version": receipt.schema_version,
            }
            if existing is not None:
                if not self._same(existing, expected_receipt):
                    raise CompanionConflictError("capability_mutation_receipt_conflict")
                return dict(existing)
            if request["status"] not in {
                "claimed",
                "staging",
                "staged",
                "publishing",
                "unknown",
            }:
                raise CompanionStateError("capability_mutation_not_settleable")
            request_source_hash = (
                canonical_hash(json.loads(str(request["source_fence_json"])))
                if request["source_fence_json"] is not None
                else None
            )
            if (
                request["action"] != action
                or request["candidate_mode"] != candidate_mode
                or request["pack_id"] != receipt.pack_id
                or request["manager_operation_id"] != receipt.manager_operation_id
                or request["runtime_set_ref"] != receipt.runtime_set_ref
                or request["runtime_set_hash"] != receipt.runtime_set_hash
                or request_source_hash != receipt.source_fence_hash
            ):
                raise CompanionConflictError("capability_mutation_receipt_fence_mismatch")
            if action in {"install", "update"}:
                if (
                    request["target_version"] != receipt.version
                    or request["target_manifest_hash"] != receipt.manifest_hash
                    or request["target_owner_key"] != receipt.target_owner_key
                    or request["target_scope"] != receipt.target_scope
                    or receipt.binding_generation is None
                    or receipt.binding_generation
                    <= int(request["target_expected_binding_generation"])
                    or not receipt.owner_binding_set_stamp
                    or not receipt.process_projection_fingerprint
                    or receipt.absence_proof_hash is not None
                ):
                    raise CompanionConflictError("capability_mutation_binding_receipt_invalid")
            elif action == "rollback":
                if request["rollback_kind"] == "remove_override":
                    required = (
                        receipt.target_user_owner_binding_set_stamp,
                        receipt.fallback_owner_key,
                        receipt.fallback_scope,
                        receipt.fallback_scope_key,
                        receipt.fallback_binding_id,
                        receipt.fallback_binding_generation,
                        receipt.fallback_pack_id,
                        receipt.fallback_version,
                        receipt.fallback_manifest_hash,
                        receipt.fallback_owner_binding_set_stamp,
                        receipt.fallback_process_projection_fingerprint,
                        receipt.fallback_runtime_set_ref,
                        receipt.fallback_runtime_set_hash,
                    )
                    if any(value is None for value in required):
                        raise CompanionConflictError("remove_override_receipt_incomplete")
                elif (
                    request["rollback_kind"] != "same_owner_version"
                    or receipt.binding_generation is None
                    or not receipt.owner_binding_set_stamp
                ):
                    raise CompanionConflictError("rollback_receipt_invalid")
            else:
                if receipt.absence_proof_hash is None or any(
                    value is not None
                    for value in (
                        receipt.fallback_owner_key,
                        receipt.fallback_runtime_set_hash,
                        receipt.binding_generation,
                    )
                ):
                    raise CompanionConflictError("capability_absence_receipt_invalid")

            db.execute(
                """INSERT INTO capability_activation_receipts(
                     profile_id,profile_generation,activation_request_id,manager_operation_id,
                     action,candidate_mode,pack_id,version,manifest_hash,source_fence_hash,
                     runtime_set_ref,runtime_set_hash,target_owner_key,target_scope,
                     binding_generation,owner_binding_set_stamp,process_projection_fingerprint,
                     target_user_owner_binding_set_stamp,fallback_owner_key,fallback_scope,
                     fallback_scope_key,fallback_binding_id,fallback_binding_generation,
                     fallback_pack_id,fallback_version,fallback_manifest_hash,
                     fallback_owner_binding_set_stamp,fallback_process_projection_fingerprint,
                     fallback_runtime_set_ref,fallback_runtime_set_hash,absence_proof_hash,
                     result_hash,settled_at,reason_code,schema_version
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    receipt.activation_request_id,
                    receipt.manager_operation_id,
                    action,
                    candidate_mode,
                    receipt.pack_id,
                    receipt.version,
                    receipt.manifest_hash,
                    receipt.source_fence_hash,
                    receipt.runtime_set_ref,
                    receipt.runtime_set_hash,
                    receipt.target_owner_key,
                    receipt.target_scope,
                    receipt.binding_generation,
                    receipt.owner_binding_set_stamp,
                    receipt.process_projection_fingerprint,
                    receipt.target_user_owner_binding_set_stamp,
                    receipt.fallback_owner_key,
                    receipt.fallback_scope,
                    receipt.fallback_scope_key,
                    receipt.fallback_binding_id,
                    receipt.fallback_binding_generation,
                    receipt.fallback_pack_id,
                    receipt.fallback_version,
                    receipt.fallback_manifest_hash,
                    receipt.fallback_owner_binding_set_stamp,
                    receipt.fallback_process_projection_fingerprint,
                    receipt.fallback_runtime_set_ref,
                    receipt.fallback_runtime_set_hash,
                    receipt.absence_proof_hash,
                    receipt.result_hash,
                    now,
                    receipt.reason_code,
                    receipt.schema_version,
                ),
            )
            db.execute(
                """UPDATE capability_activation_requests
                   SET status='succeeded',reason_code=?,lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND activation_request_id=?""",
                (
                    receipt.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    receipt.activation_request_id,
                ),
            )
            settled_candidate_id = request["candidate_id"]
            if settled_candidate_id is None and action in {"rollback", "disable"}:
                lineage = db.execute(
                    """SELECT from_id FROM lineage_edges
                       WHERE profile_id=? AND profile_generation=?
                         AND from_kind='candidate'
                         AND to_kind='capability_mutation_request'
                         AND to_id=? AND relation=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.activation_request_id,
                        action,
                    ),
                ).fetchall()
                if len(lineage) > 1:
                    raise CompanionConflictError("capability_mutation_candidate_lineage_conflict")
                if lineage:
                    settled_candidate_id = lineage[0]["from_id"]
            if action in {"install", "update"}:
                db.execute(
                    """UPDATE candidate_artifacts SET status='active',reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                    (
                        receipt.reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        request["candidate_id"],
                    ),
                )
                if candidate_mode == "genesis":
                    db.execute(
                        """UPDATE growth_target_reservations
                           SET status='consumed',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                             AND status='held'""",
                        (
                            receipt.reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            request["candidate_id"],
                        ),
                    )
            elif action == "rollback":
                if settled_candidate_id is not None:
                    db.execute(
                        """UPDATE candidate_artifacts
                           SET status='rolled_back',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                             AND status IN ('active','rollback_pending')""",
                        (
                            receipt.reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            settled_candidate_id,
                        ),
                    )
                db.execute(
                    """UPDATE capability_quarantines
                       SET status='released',release_receipt_hash=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND pack_id=?
                         AND status IN ('active','rollback_pending','release_pending')""",
                    (
                        receipt.result_hash,
                        receipt.reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.pack_id,
                    ),
                )
            else:
                if settled_candidate_id is not None:
                    db.execute(
                        """UPDATE candidate_artifacts
                           SET status='disabled',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                             AND status IN ('active','rollback_pending')""",
                        (
                            receipt.reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            settled_candidate_id,
                        ),
                    )
                db.execute(
                    """UPDATE capability_quarantines
                       SET status='disabled',release_receipt_hash=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND pack_id=?
                         AND status<>'released'""",
                    (
                        receipt.result_hash,
                        receipt.reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.pack_id,
                    ),
                )

            if action in {"rollback", "disable"}:
                db.execute(
                    """UPDATE capability_activation_guards
                       SET status='rolled_back',rollback_receipt_hash=?,
                           reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND rollback_request_id=?
                         AND status='rollback_pending'""",
                    (
                        receipt.result_hash,
                        receipt.reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.activation_request_id,
                    ),
                )

            # Any successful binding change supersedes another old open guard.
            db.execute(
                """UPDATE capability_activation_guards
                   SET status='superseded',reason_code='binding_superseded',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND target_owner_key=?
                     AND target_scope=? AND target_scope_key=? AND pack_id=?
                     AND status='open'""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request["target_owner_key"],
                    request["target_scope"],
                    request["target_scope_key"],
                    request["pack_id"],
                ),
            )
            if receipt.open_guard:
                if (
                    action not in {"install", "update"}
                    or receipt.guard_id is None
                    or receipt.guard_policy_hash is None
                    or receipt.rollback_plan is None
                    or receipt.binding_generation is None
                    or receipt.owner_binding_set_stamp is None
                    or request["candidate_id"] is None
                ):
                    raise CompanionStateError("activation_guard_contract_incomplete")
                risk = db.execute(
                    """SELECT risk FROM risk_assessments
                       WHERE profile_id=? AND profile_generation=? AND risk_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request["risk_id"],
                    ),
                ).fetchone()
                if risk is None or risk["risk"] != "low":
                    raise CompanionStateError("activation_guard_requires_low_risk")
                rollback_plan_json = canonical_json(dict(receipt.rollback_plan))
                rollback_plan_hash = canonical_hash(dict(receipt.rollback_plan))
                expires = (
                    (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(hours=24))
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
                db.execute(
                    """INSERT INTO capability_activation_guards(
                         profile_id,profile_generation,guard_id,activation_request_id,candidate_id,
                         pack_id,version,manifest_hash,receipt_hash,target_owner_key,target_scope,
                         target_scope_key,binding_generation,owner_stamp,opened_at,expires_at,
                         policy_id,policy_hash,window_seconds,threshold,rollback_plan_json,
                         rollback_plan_hash,status,reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'companion_guard_v1',?,
                                 86400,1,?,?,'open',?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.guard_id,
                        receipt.activation_request_id,
                        request["candidate_id"],
                        receipt.pack_id,
                        receipt.version,
                        receipt.manifest_hash,
                        receipt.result_hash,
                        request["target_owner_key"],
                        request["target_scope"],
                        request["target_scope_key"],
                        receipt.binding_generation,
                        receipt.owner_binding_set_stamp,
                        now,
                        expires,
                        receipt.guard_policy_hash,
                        rollback_plan_json,
                        rollback_plan_hash,
                        receipt.reason_code,
                        now,
                        now,
                    ),
                )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(
                    ["capability_mutation_settled", receipt.activation_request_id]
                ),
                event_kind="capability_mutation_settled",
                event_id=receipt.activation_request_id,
                sink_kind="companion_notification",
                payload={
                    "schema_version": 1,
                    "activation_request_id": receipt.activation_request_id,
                    "action": action,
                    "pack_id": receipt.pack_id,
                    "version": receipt.version,
                    "manifest_hash": receipt.manifest_hash,
                    "candidate_mode": candidate_mode,
                    "report_id": request["report_id"],
                    "reason_code": receipt.reason_code,
                    "result_hash": receipt.result_hash,
                },
                now=now,
                reason_code=receipt.reason_code,
            )
            self._bump_detail(db, owner, mutation_hash=receipt.result_hash, now=now)
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_receipts
                       WHERE profile_id=? AND profile_generation=? AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.activation_request_id,
                    ),
                ).fetchone()
            )

    def get_capability_activation_receipt(
        self,
        owner: OwnerRef,
        *,
        activation_request_id: str,
    ) -> Mapping[str, Any] | None:
        """Read one trusted Manager receipt within the exact owner fence."""

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM capability_activation_receipts
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(activation_request_id),
                ),
            ).fetchone()
            return None if row is None else dict(row)

    def get_candidate_activation_proof(
        self,
        owner: OwnerRef,
        *,
        candidate_id: str,
    ) -> Mapping[str, Any] | None:
        """Return the latest exact decision/request/receipt proof for a candidate.

        A pending request is returned with ``receipt=None``; callers therefore
        cannot mistake an admitted activation intent for a committed Manager
        outcome.
        """

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT q.*,d.decision_hash,d.actor AS decision_actor,
                          d.decision AS activation_decision,
                          r.risk,r.risk_hash,
                          e.verdict,e.results_root_hash,
                          a.manager_operation_id AS receipt_manager_operation_id,
                          a.result_hash AS receipt_result_hash,
                          a.binding_generation AS receipt_binding_generation,
                          a.owner_binding_set_stamp AS
                            receipt_owner_binding_set_stamp,
                          a.process_projection_fingerprint AS
                            receipt_process_projection_fingerprint,
                          a.settled_at AS receipt_settled_at,
                          a.reason_code AS receipt_reason_code
                   FROM capability_activation_requests q
                   JOIN growth_decisions d
                     ON d.profile_id=q.profile_id
                    AND d.profile_generation=q.profile_generation
                    AND d.decision_id=q.decision_id
                   JOIN risk_assessments r
                     ON r.profile_id=q.profile_id
                    AND r.profile_generation=q.profile_generation
                    AND r.risk_id=q.risk_id
                   JOIN evaluation_reports e
                     ON e.profile_id=q.profile_id
                    AND e.profile_generation=q.profile_generation
                    AND e.report_id=q.report_id
                   LEFT JOIN capability_activation_receipts a
                     ON a.profile_id=q.profile_id
                    AND a.profile_generation=q.profile_generation
                    AND a.activation_request_id=q.activation_request_id
                   WHERE q.profile_id=? AND q.profile_generation=?
                     AND q.candidate_id=?
                   ORDER BY q.created_at DESC,q.activation_request_id DESC
                   LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(candidate_id),
                ),
            ).fetchone()
            if row is None:
                return None
            raw = dict(row)
            receipt = None
            if raw["receipt_result_hash"] is not None:
                receipt = {
                    "activation_request_id": raw["activation_request_id"],
                    "manager_operation_id": raw["receipt_manager_operation_id"],
                    "result_hash": raw["receipt_result_hash"],
                    "binding_generation": raw["receipt_binding_generation"],
                    "owner_binding_set_stamp": raw["receipt_owner_binding_set_stamp"],
                    "process_projection_fingerprint": raw["receipt_process_projection_fingerprint"],
                    "settled_at": raw["receipt_settled_at"],
                    "reason_code": raw["receipt_reason_code"],
                }
            return {
                "candidate_id": raw["candidate_id"],
                "activation_request_id": raw["activation_request_id"],
                "request_fingerprint": raw["request_fingerprint"],
                "request_status": raw["status"],
                "action": raw["action"],
                "candidate_mode": raw["candidate_mode"],
                "target_owner_key": raw["target_owner_key"],
                "target_scope": raw["target_scope"],
                "target_scope_key": raw["target_scope_key"],
                "candidate_package_hash": raw["candidate_package_hash"],
                "target_manifest_hash": raw["target_manifest_hash"],
                "archive_hash": raw["archive_hash"],
                "report_id": raw["report_id"],
                "evaluation_verdict": raw["verdict"],
                "evaluation_results_root_hash": raw["results_root_hash"],
                "risk_id": raw["risk_id"],
                "risk": raw["risk"],
                "risk_hash": raw["risk_hash"],
                "decision_id": raw["decision_id"],
                "decision": raw["activation_decision"],
                "decision_actor": raw["decision_actor"],
                "decision_hash": raw["decision_hash"],
                "receipt": receipt,
            }

    def record_capability_guard_incident(
        self,
        owner: OwnerRef,
        incident: CapabilityGuardIncident,
    ) -> Mapping[str, Any]:
        allowed_failures = {
            "package_integrity",
            "runtime_contract",
            "schema_fingerprint",
            "effect_policy_fingerprint",
        }
        if incident.severity != "critical" or incident.failure_class not in allowed_failures:
            raise CompanionStateError("capability_guard_incident_not_eligible")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM capability_guard_incidents
                   WHERE profile_id=? AND profile_generation=?
                     AND (guard_id=? AND incident_id=?
                          OR source_authority=? AND source_event_id=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.guard_id,
                    incident.incident_id,
                    incident.source_authority,
                    incident.source_event_id,
                ),
            ).fetchone()
            expected = {
                "guard_id": incident.guard_id,
                "incident_id": incident.incident_id,
                "source_authority": incident.source_authority,
                "source_event_id": incident.source_event_id,
                "binding_generation": incident.binding_generation,
                "pack_id": incident.pack_id,
                "version": incident.version,
                "manifest_hash": incident.manifest_hash,
                "runtime_generation": incident.runtime_generation,
                "failure_class": incident.failure_class,
                "severity": incident.severity,
                "failure_fingerprint": incident.failure_fingerprint,
                "source_receipt_ref": incident.source_receipt_ref,
                "source_receipt_hash": incident.source_receipt_hash,
                "observed_at": incident.observed_at,
                "dedupe_hash": incident.dedupe_hash,
                "schema_version": incident.schema_version,
            }
            if existing is not None:
                if not self._same(existing, expected):
                    raise CompanionConflictError("capability_guard_incident_conflict")
                return dict(existing)
            guard = db.execute(
                """SELECT * FROM capability_activation_guards
                   WHERE profile_id=? AND profile_generation=? AND guard_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.guard_id,
                ),
            ).fetchone()
            if (
                guard is None
                or guard["status"] != "open"
                or guard["expires_at"] <= now
                or incident.observed_at < guard["opened_at"]
                or incident.observed_at > guard["expires_at"]
                or incident.observed_at > now
                or guard["binding_generation"] != incident.binding_generation
                or guard["pack_id"] != incident.pack_id
                or guard["version"] != incident.version
                or guard["manifest_hash"] != incident.manifest_hash
            ):
                raise CompanionStateError("capability_guard_fence_stale")
            candidate = db.execute(
                """SELECT candidate_mode,status FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    guard["candidate_id"],
                ),
            ).fetchone()
            if candidate is None or candidate["status"] != "active":
                raise CompanionStateError("capability_guard_candidate_not_active")
            plan = json.loads(str(guard["rollback_plan_json"]))
            action = str(plan.get("action", "rollback"))
            rollback_kind = plan.get("rollback_kind")
            if action not in {"rollback", "disable"}:
                raise CompanionStateError("capability_guard_rollback_plan_invalid")
            cause_ref = f"guard:{incident.guard_id}:incident:{incident.incident_id}"
            manager_key = canonical_hash(
                [
                    "companion_growth",
                    owner.profile_id,
                    owner.profile_generation,
                    incident.request_fingerprint,
                ]
            )
            db.execute(
                """INSERT INTO capability_activation_requests(
                     profile_id,profile_generation,activation_request_id,request_fingerprint,
                     action,candidate_id,candidate_mode,rollback_kind,activation_mode,
                     target_owner_key,target_scope,target_scope_key,pack_id,target_version,
                     target_manifest_hash,candidate_package_hash,archive_hash,source_fence_json,
                     target_expected_absent,target_expected_binding_generation,cause_ref,
                     manager_idempotency_key,status,claim_epoch,attempt,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,NULL,NULL,?,'normal',?,?,?,?,?,?,NULL,NULL,?,
                             0,?,?,?,'pending',0,0,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.rollback_request_id,
                    incident.request_fingerprint,
                    action,
                    rollback_kind,
                    guard["target_owner_key"],
                    guard["target_scope"],
                    guard["target_scope_key"],
                    guard["pack_id"],
                    plan.get("target_version"),
                    plan.get("target_manifest_hash"),
                    canonical_json(dict(plan.get("source_fence", {})))
                    if plan.get("source_fence") is not None
                    else None,
                    guard["binding_generation"],
                    cause_ref,
                    manager_key,
                    incident.reason_code,
                    now,
                    now,
                ),
            )
            db.execute(
                """INSERT INTO lineage_edges(
                     profile_id,profile_generation,from_kind,from_id,
                     to_kind,to_id,relation,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    "candidate",
                    guard["candidate_id"],
                    "capability_mutation_request",
                    incident.rollback_request_id,
                    action,
                    incident.reason_code,
                    now,
                ),
            )
            db.execute(
                """INSERT INTO capability_guard_incidents(
                     profile_id,profile_generation,guard_id,incident_id,source_authority,
                     source_event_id,binding_generation,pack_id,version,manifest_hash,
                     runtime_generation,failure_class,severity,failure_fingerprint,
                     source_receipt_ref,source_receipt_hash,observed_at,dedupe_hash,status,
                     reason_code,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'triggered',?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.guard_id,
                    incident.incident_id,
                    incident.source_authority,
                    incident.source_event_id,
                    incident.binding_generation,
                    incident.pack_id,
                    incident.version,
                    incident.manifest_hash,
                    incident.runtime_generation,
                    incident.failure_class,
                    incident.severity,
                    incident.failure_fingerprint,
                    incident.source_receipt_ref,
                    incident.source_receipt_hash,
                    incident.observed_at,
                    incident.dedupe_hash,
                    incident.reason_code,
                    now,
                ),
            )
            db.execute(
                """UPDATE capability_activation_guards
                   SET status='rollback_pending',trigger_incident_id=?,rollback_request_id=?,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND guard_id=? AND status='open'""",
                (
                    incident.incident_id,
                    incident.rollback_request_id,
                    incident.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    incident.guard_id,
                ),
            )
            db.execute(
                """UPDATE candidate_artifacts
                   SET status='rollback_pending',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                     AND status='active'""",
                (
                    incident.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    guard["candidate_id"],
                ),
            )
            quarantine = db.execute(
                """SELECT fence_generation FROM capability_quarantines
                   WHERE profile_id=? AND profile_generation=? AND pack_id=?
                     AND version=? AND manifest_hash=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.pack_id,
                    incident.version,
                    incident.manifest_hash,
                ),
            ).fetchone()
            if quarantine is None:
                db.execute(
                    """INSERT INTO capability_quarantines(
                         profile_id,profile_generation,pack_id,version,manifest_hash,reason_code,
                         evidence_ref,fence_generation,support_set_hash,status,schema_version,
                         created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,'rollback_pending',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        incident.pack_id,
                        incident.version,
                        incident.manifest_hash,
                        incident.reason_code,
                        cause_ref,
                        1,
                        canonical_hash([]),
                        now,
                        now,
                    ),
                )
            else:
                db.execute(
                    """UPDATE capability_quarantines
                       SET status='rollback_pending',fence_generation=fence_generation+1,
                           reason_code=?,evidence_ref=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND pack_id=?
                         AND version=? AND manifest_hash=?""",
                    (
                        incident.reason_code,
                        cause_ref,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        incident.pack_id,
                        incident.version,
                        incident.manifest_hash,
                    ),
                )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(
                    ["capability_guard_rollback", incident.guard_id, incident.incident_id]
                ),
                event_kind="capability_mutation",
                event_id=incident.rollback_request_id,
                sink_kind="capability_platform",
                payload={
                    "schema_version": 1,
                    "activation_request_id": incident.rollback_request_id,
                    "request_fingerprint": incident.request_fingerprint,
                    "action": action,
                },
                now=now,
                reason_code=incident.reason_code,
            )
            self._bump_detail(db, owner, mutation_hash=incident.dedupe_hash, now=now)
            return dict(
                db.execute(
                    """SELECT * FROM capability_guard_incidents
                       WHERE profile_id=? AND profile_generation=? AND guard_id=? AND incident_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        incident.guard_id,
                        incident.incident_id,
                    ),
                ).fetchone()
            )
