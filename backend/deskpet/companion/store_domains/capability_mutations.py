"""Activation decisions and capability mutation requests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from ..contracts import (
    CompanionConflictError,
    CompanionLeaseError,
    CompanionStateError,
    MutationRequest,
    OwnerRef,
)
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class CapabilityMutationMixin(CompanionStoreCore):
    """Activation decisions and capability mutation requests."""

    __slots__ = ()

    def record_activation_decision(
        self,
        owner: OwnerRef,
        *,
        decision_id: str,
        nonce: str,
        candidate_id: str,
        report_id: str,
        risk_id: str,
        decision: str,
        actor: str,
        activation_package_hash: str | None,
        activation_code_digest: str | None,
        activation_risk_ack: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Record an exact activation decision without granting action effects."""

        if decision not in {"activate", "reject"}:
            raise ValueError("growth activation decision invalid")
        if actor not in {"user", "system"}:
            raise ValueError("growth activation actor invalid")
        if activation_risk_ack not in {
            "none",
            "persistent_local_code_no_os_sandbox",
        }:
            raise ValueError("growth activation risk acknowledgement invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM growth_decisions
                   WHERE profile_id=? AND profile_generation=?
                     AND (decision_id=? OR nonce=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    decision_id,
                    nonce,
                ),
            ).fetchone()
            facts = db.execute(
                """SELECT a.*,p.candidate_package_hash,p.pack_id,p.version,
                          p.candidate_manifest_hash,r.risk,
                          r.static_preflight_json,er.verdict
                   FROM candidate_artifacts a
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN risk_assessments r
                     ON r.profile_id=a.profile_id
                    AND r.profile_generation=a.profile_generation
                    AND r.candidate_id=a.candidate_id AND r.risk_id=?
                   JOIN evaluation_runs e
                     ON e.profile_id=a.profile_id
                    AND e.profile_generation=a.profile_generation
                    AND e.candidate_id=a.candidate_id
                   JOIN evaluation_reports er
                     ON er.profile_id=e.profile_id
                    AND er.profile_generation=e.profile_generation
                    AND er.evaluation_id=e.evaluation_id AND er.report_id=?
                   WHERE a.profile_id=? AND a.profile_generation=?
                     AND a.candidate_id=?""",
                (
                    risk_id,
                    report_id,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            ).fetchone()
            if facts is None or facts["verdict"] != "passed":
                raise CompanionStateError("activation_decision_evaluation_not_passed")
            if facts["status"] not in {
                "eligible",
                "awaiting_activation_confirmation",
                "activation_pending",
            }:
                raise CompanionStateError("activation_decision_candidate_not_decidable")
            preflight = json.loads(str(facts["static_preflight_json"]))
            candidate_kind = str(preflight.get("candidate_kind") or "unknown")
            executable = candidate_kind in {"code", "hook", "local_runtime"}
            package_hash = str(facts["candidate_package_hash"])
            risk = str(facts["risk"])
            if decision == "activate":
                if risk != "low" and actor != "user":
                    raise CompanionStateError("activation_confirmation_user_required")
                if activation_package_hash != package_hash:
                    raise CompanionConflictError("activation_decision_package_hash_mismatch")
                if executable and (
                    actor != "user"
                    or not activation_code_digest
                    or activation_risk_ack != "persistent_local_code_no_os_sandbox"
                ):
                    raise CompanionStateError("executable_activation_confirmation_incomplete")
                if not executable and activation_risk_ack != "none":
                    raise CompanionStateError("activation_risk_ack_not_applicable")
            decision_payload = {
                "schema_version": 1,
                "decision_id": decision_id,
                "nonce": nonce,
                "candidate_id": candidate_id,
                "report_id": report_id,
                "risk_id": risk_id,
                "candidate_mode": facts["candidate_mode"],
                "source_fence": {
                    "owner_key": facts["source_owner_key"],
                    "scope": facts["source_scope"],
                    "scope_key": facts["source_scope_key"],
                    "version": facts["source_version"],
                    "manifest_hash": facts["source_manifest_hash"],
                    "binding_generation": facts["source_binding_generation"],
                }
                if facts["source_owner_key"] is not None
                else None,
                "target_owner_key": facts["target_owner_key"],
                "target_scope": facts["target_scope"],
                "target_scope_key": facts["target_scope_key"],
                "target_expected_absent": bool(facts["target_expected_absent"]),
                "target_expected_binding_generation": int(
                    facts["target_expected_binding_generation"]
                ),
                "decision": decision,
                "actor": actor,
                "reason_code": reason_code,
                "activation_package_hash": activation_package_hash,
                "activation_code_digest": activation_code_digest,
                "activation_risk_ack": activation_risk_ack,
            }
            decision_hash = canonical_hash(decision_payload)
            expected = {
                "nonce": nonce,
                "candidate_id": candidate_id,
                "report_id": report_id,
                "risk_id": risk_id,
                "candidate_mode": facts["candidate_mode"],
                "source_fence_json": (
                    None
                    if decision_payload["source_fence"] is None
                    else canonical_json(decision_payload["source_fence"])
                ),
                "target_owner_key": facts["target_owner_key"],
                "target_scope": facts["target_scope"],
                "target_scope_key": facts["target_scope_key"],
                "target_expected_absent": int(facts["target_expected_absent"]),
                "target_expected_binding_generation": int(
                    facts["target_expected_binding_generation"]
                ),
                "decision": decision,
                "actor": actor,
                "reason_code": reason_code,
                "activation_package_hash": activation_package_hash,
                "activation_code_digest": activation_code_digest,
                "activation_risk_ack": activation_risk_ack,
                "decision_hash": decision_hash,
                "schema_version": 1,
            }
            if existing is not None:
                if existing["decision_id"] != decision_id or not self._same(existing, expected):
                    raise CompanionConflictError("activation_decision_conflict")
                return dict(existing)
            db.execute(
                """INSERT INTO growth_decisions(
                     profile_id,profile_generation,decision_id,nonce,
                     candidate_id,report_id,risk_id,candidate_mode,
                     source_fence_json,target_owner_key,target_scope,
                     target_scope_key,target_expected_absent,
                     target_expected_binding_generation,decision,actor,
                     reason_code,activation_package_hash,
                     activation_code_digest,activation_risk_ack,
                     decision_hash,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    decision_id,
                    nonce,
                    candidate_id,
                    report_id,
                    risk_id,
                    facts["candidate_mode"],
                    expected["source_fence_json"],
                    facts["target_owner_key"],
                    facts["target_scope"],
                    facts["target_scope_key"],
                    facts["target_expected_absent"],
                    facts["target_expected_binding_generation"],
                    decision,
                    actor,
                    reason_code,
                    activation_package_hash,
                    activation_code_digest,
                    activation_risk_ack,
                    decision_hash,
                    now,
                ),
            )
            next_status = "activation_pending" if decision == "activate" else "rejected"
            db.execute(
                """UPDATE candidate_artifacts
                   SET status=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=? AND status IN (
                       'eligible','awaiting_activation_confirmation'
                     )""",
                (
                    next_status,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            )
            self._bump_detail(db, owner, mutation_hash=decision_hash, now=now)
            return dict(
                db.execute(
                    """SELECT * FROM growth_decisions
                       WHERE profile_id=? AND profile_generation=?
                         AND decision_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        decision_id,
                    ),
                ).fetchone()
            )

    # ------------------------------------------------------------------
    # Capability mutation intent.  CapabilityStore remains the only active
    # binding authority; these rows are an outbox/saga intent only.

    def create_capability_mutation_request(
        self, owner: OwnerRef, request: MutationRequest
    ) -> Mapping[str, Any]:
        now = self._now()
        action = request.action.value
        candidate_mode = request.candidate_mode.value if request.candidate_mode else None
        persisted_candidate_id = request.candidate_id if action in {"install", "update"} else None
        persisted_candidate_mode = candidate_mode if action in {"install", "update"} else None
        source_fence_json = (
            canonical_json(dict(request.source_fence)) if request.source_fence is not None else None
        )
        manager_key = canonical_hash(
            [
                "companion_growth",
                owner.profile_id,
                owner.profile_generation,
                request.request_fingerprint,
            ]
        )
        with self._write() as db:
            self._require_owner(db, owner)
            replay = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=? AND request_fingerprint=?""",
                (owner.profile_id, owner.profile_generation, request.request_fingerprint),
            ).fetchone()
            if replay is not None:
                replay_expected = {
                    "activation_request_id": request.request_id,
                    "action": action,
                    "candidate_id": persisted_candidate_id,
                    "candidate_mode": persisted_candidate_mode,
                    "rollback_kind": request.rollback_kind,
                    "activation_mode": request.activation_mode,
                    "target_owner_key": request.target_owner_key,
                    "target_scope": request.target_scope,
                    "target_scope_key": request.target_scope_key,
                    "pack_id": request.pack_id,
                    "target_version": request.target_version,
                    "target_manifest_hash": request.target_manifest_hash,
                    "candidate_package_hash": request.target_package_hash,
                    "archive_hash": request.target_archive_hash,
                    "source_fence_json": source_fence_json,
                    "target_expected_absent": int(request.target_expected_absent),
                    "target_expected_binding_generation": request.target_expected_binding_generation,
                    "cause_ref": request.cause_ref,
                    "reason_code": request.reason_code,
                }
                # Older trusted message-panel rollback rows did not retain the
                # source candidate identity.  Adopt the same durable intent
                # only when every other immutable field is identical, then
                # backfill the missing lineage so an already-settled rollback
                # can project the candidate terminal state as well.
                replay_identity_expected = {
                    key: value for key, value in replay_expected.items() if key != "reason_code"
                }
                legacy_rollback_backfill = (
                    action in {"rollback", "disable"}
                    and request.candidate_id is not None
                    and candidate_mode is not None
                    and self._same(replay, replay_identity_expected)
                    and (
                        replay["reason_code"] == request.reason_code
                        or replay["status"] in {"succeeded", "failed"}
                    )
                )
                if legacy_rollback_backfill:
                    candidate = db.execute(
                        """SELECT status,candidate_mode FROM candidate_artifacts
                           WHERE profile_id=? AND profile_generation=?
                             AND candidate_id=?""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            request.candidate_id,
                        ),
                    ).fetchone()
                    if (
                        candidate is None
                        or candidate["candidate_mode"] != candidate_mode
                        or candidate["status"] not in {"active", "rollback_pending", "rolled_back"}
                    ):
                        raise CompanionConflictError(
                            "capability_mutation_legacy_candidate_conflict"
                        )
                    db.execute(
                        """INSERT OR IGNORE INTO lineage_edges(
                             profile_id,profile_generation,from_kind,from_id,
                             to_kind,to_id,relation,reason_code,
                             schema_version,created_at
                           ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            "candidate",
                            request.candidate_id,
                            "capability_mutation_request",
                            replay["activation_request_id"],
                            action,
                            request.reason_code,
                            now,
                        ),
                    )
                    if replay["status"] == "succeeded":
                        db.execute(
                            """UPDATE candidate_artifacts
                               SET status='rolled_back',reason_code=?,updated_at=?
                               WHERE profile_id=? AND profile_generation=?
                                 AND candidate_id=?
                                 AND status IN ('active','rollback_pending')""",
                            (
                                replay["reason_code"],
                                now,
                                owner.profile_id,
                                owner.profile_generation,
                                request.candidate_id,
                            ),
                        )
                    elif replay["status"] in {
                        "pending",
                        "claimed",
                        "staging",
                        "staged",
                        "publishing",
                        "unknown",
                        "cleanup_required",
                    }:
                        db.execute(
                            """UPDATE candidate_artifacts
                               SET status='rollback_pending',reason_code=?,updated_at=?
                               WHERE profile_id=? AND profile_generation=?
                                 AND candidate_id=? AND status='active'""",
                            (
                                replay["reason_code"],
                                now,
                                owner.profile_id,
                                owner.profile_generation,
                                request.candidate_id,
                            ),
                        )
                    return dict(
                        db.execute(
                            """SELECT * FROM capability_activation_requests
                               WHERE profile_id=? AND profile_generation=?
                                 AND activation_request_id=?""",
                            (
                                owner.profile_id,
                                owner.profile_generation,
                                replay["activation_request_id"],
                            ),
                        ).fetchone()
                    )
                if not self._same(replay, replay_expected):
                    raise CompanionConflictError("capability_mutation_request_conflict")
                return dict(replay)

            report_id = risk_id = decision_id = None
            if action in {"install", "update"}:
                if request.candidate_id is None:
                    raise CompanionStateError("mutation_candidate_required")
                candidate = db.execute(
                    """SELECT a.*,p.pack_id AS frozen_pack_id,
                              p.version AS frozen_version,
                              p.candidate_package_hash,p.candidate_manifest_hash,
                              p.archive_hash
                       FROM candidate_artifacts a JOIN candidate_packages p
                         ON p.profile_id=a.profile_id AND p.profile_generation=a.profile_generation
                        AND p.package_id=a.package_id
                       WHERE a.profile_id=? AND a.profile_generation=? AND a.candidate_id=?""",
                    (owner.profile_id, owner.profile_generation, request.candidate_id),
                ).fetchone()
                if candidate is None or candidate["status"] not in {
                    "eligible",
                    "activation_pending",
                }:
                    raise CompanionStateError("mutation_candidate_not_eligible")
                frozen_mode = str(candidate["candidate_mode"])
                expected_action = "update" if frozen_mode == "update" else "install"
                if candidate_mode != frozen_mode or action != expected_action:
                    raise CompanionConflictError("mutation_candidate_mode_or_action_mismatch")
                if (
                    request.pack_id != candidate["frozen_pack_id"]
                    or request.target_version != candidate["frozen_version"]
                    or request.target_owner_key != candidate["target_owner_key"]
                    or request.target_scope != candidate["target_scope"]
                    or request.target_scope_key != candidate["target_scope_key"]
                    or int(request.target_expected_absent)
                    != int(candidate["target_expected_absent"])
                    or request.target_expected_binding_generation
                    != int(candidate["target_expected_binding_generation"])
                ):
                    raise CompanionConflictError("mutation_candidate_fence_mismatch")
                if request.source_fence is not None:
                    source_fence = dict(request.source_fence)
                    frozen_source = {
                        "owner_key": candidate["source_owner_key"],
                        "scope": candidate["source_scope"],
                        "scope_key": candidate["source_scope_key"],
                        "version": candidate["source_version"],
                        "manifest_hash": candidate["source_manifest_hash"],
                        "binding_generation": candidate["source_binding_generation"],
                    }
                    if any(source_fence.get(key) != value for key, value in frozen_source.items()):
                        raise CompanionConflictError("mutation_candidate_source_fence_mismatch")
                report = db.execute(
                    """SELECT report_id FROM evaluation_reports
                       WHERE profile_id=? AND profile_generation=? AND verdict='passed'
                         AND evaluation_id IN (
                           SELECT evaluation_id FROM evaluation_runs
                           WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                         ) ORDER BY created_at DESC LIMIT 1""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        owner.profile_id,
                        owner.profile_generation,
                        request.candidate_id,
                    ),
                ).fetchone()
                risk = db.execute(
                    """SELECT risk_id FROM risk_assessments
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                         AND risk<>'unknown' ORDER BY created_at DESC LIMIT 1""",
                    (owner.profile_id, owner.profile_generation, request.candidate_id),
                ).fetchone()
                decision = db.execute(
                    """SELECT decision_id FROM growth_decisions
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                         AND decision='activate' ORDER BY created_at DESC LIMIT 1""",
                    (owner.profile_id, owner.profile_generation, request.candidate_id),
                ).fetchone()
                if report is None or risk is None or decision is None:
                    raise CompanionStateError("mutation_evaluation_or_decision_missing")
                report_id, risk_id, decision_id = (
                    report["report_id"],
                    risk["risk_id"],
                    decision["decision_id"],
                )
                if (
                    request.target_package_hash != candidate["candidate_package_hash"]
                    or request.target_manifest_hash != candidate["candidate_manifest_hash"]
                    or request.target_archive_hash != candidate["archive_hash"]
                ):
                    raise CompanionConflictError("mutation_candidate_hash_mismatch")
                if candidate_mode == "genesis":
                    reservation = db.execute(
                        """SELECT candidate_id,status FROM growth_target_reservations
                           WHERE profile_id=? AND profile_generation=? AND target_id=?
                             AND reservation_version=?""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            candidate["target_id"],
                            candidate["reservation_version"],
                        ),
                    ).fetchone()
                    if (
                        reservation is None
                        or reservation["status"] != "held"
                        or reservation["candidate_id"] != request.candidate_id
                    ):
                        raise CompanionStateError("mutation_genesis_reservation_stale")
            elif request.candidate_id is not None:
                candidate = db.execute(
                    """SELECT a.status,a.candidate_mode,a.target_owner_key,
                              a.target_scope,a.target_scope_key,
                              p.pack_id AS frozen_pack_id
                       FROM candidate_artifacts a
                       JOIN candidate_packages p
                         ON p.profile_id=a.profile_id
                        AND p.profile_generation=a.profile_generation
                        AND p.package_id=a.package_id
                       WHERE a.profile_id=? AND a.profile_generation=?
                         AND a.candidate_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request.candidate_id,
                    ),
                ).fetchone()
                if candidate is None or candidate["status"] != "active":
                    raise CompanionStateError("mutation_candidate_not_active")
                if (
                    candidate_mode != candidate["candidate_mode"]
                    or request.target_owner_key != candidate["target_owner_key"]
                    or request.target_scope != candidate["target_scope"]
                    or request.target_scope_key != candidate["target_scope_key"]
                    or request.pack_id != candidate["frozen_pack_id"]
                ):
                    raise CompanionConflictError("mutation_candidate_rollback_fence_mismatch")
            try:
                db.execute(
                    """INSERT INTO capability_activation_requests(
                         profile_id,profile_generation,activation_request_id,request_fingerprint,
                         action,candidate_id,candidate_mode,rollback_kind,activation_mode,
                         target_owner_key,target_scope,target_scope_key,pack_id,target_version,
                         target_manifest_hash,candidate_package_hash,archive_hash,source_fence_json,
                         target_expected_absent,target_expected_binding_generation,report_id,risk_id,
                         decision_id,cause_ref,manager_idempotency_key,status,claim_epoch,attempt,
                         reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                 'pending',0,0,?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request.request_id,
                        request.request_fingerprint,
                        action,
                        persisted_candidate_id,
                        persisted_candidate_mode,
                        request.rollback_kind,
                        request.activation_mode,
                        request.target_owner_key,
                        request.target_scope,
                        request.target_scope_key,
                        request.pack_id,
                        request.target_version,
                        request.target_manifest_hash,
                        request.target_package_hash,
                        request.target_archive_hash,
                        source_fence_json,
                        int(request.target_expected_absent),
                        request.target_expected_binding_generation,
                        report_id,
                        risk_id,
                        decision_id,
                        request.cause_ref,
                        manager_key,
                        request.reason_code,
                        now,
                        now,
                    ),
                )
                if action in {"rollback", "disable"} and request.candidate_id is not None:
                    updated = db.execute(
                        """UPDATE candidate_artifacts
                           SET status='rollback_pending',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=?
                             AND candidate_id=? AND status='active'""",
                        (
                            request.reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            request.candidate_id,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise CompanionConflictError("mutation_candidate_rollback_claim_lost")
            except sqlite3.IntegrityError as exc:
                in_progress = db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=? AND target_owner_key=?
                         AND target_scope=? AND target_scope_key=? AND pack_id=?
                         AND status IN (
                           'pending','claimed','staging','staged','publishing','unknown','cleanup_required'
                         )""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request.target_owner_key,
                        request.target_scope,
                        request.target_scope_key,
                        request.pack_id,
                    ),
                ).fetchone()
                if in_progress is not None:
                    # A trusted UI rollback may be retried after the detail
                    # projection advances. Older builds included that read
                    # fence in the request id, so safely adopt the existing
                    # pending saga only when its full rollback target and
                    # cause are identical. Other competing mutations remain
                    # a hard conflict.
                    same_rollback_intent = (
                        action == "rollback"
                        and in_progress["action"] == "rollback"
                        and in_progress["rollback_kind"] == request.rollback_kind
                        and in_progress["target_version"] == request.target_version
                        and in_progress["target_manifest_hash"] == request.target_manifest_hash
                        and in_progress["target_expected_binding_generation"]
                        == request.target_expected_binding_generation
                        and in_progress["cause_ref"] == request.cause_ref
                        and in_progress["reason_code"] == request.reason_code
                    )
                    if same_rollback_intent:
                        if request.candidate_id is not None:
                            db.execute(
                                """INSERT OR IGNORE INTO lineage_edges(
                                     profile_id,profile_generation,from_kind,
                                     from_id,to_kind,to_id,relation,reason_code,
                                     schema_version,created_at
                                   ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                                (
                                    owner.profile_id,
                                    owner.profile_generation,
                                    "candidate",
                                    request.candidate_id,
                                    "capability_mutation_request",
                                    in_progress["activation_request_id"],
                                    action,
                                    request.reason_code,
                                    now,
                                ),
                            )
                            updated = db.execute(
                                """UPDATE candidate_artifacts
                                   SET status='rollback_pending',
                                       reason_code=?,updated_at=?
                                   WHERE profile_id=? AND profile_generation=?
                                     AND candidate_id=? AND status='active'""",
                                (
                                    request.reason_code,
                                    now,
                                    owner.profile_id,
                                    owner.profile_generation,
                                    request.candidate_id,
                                ),
                            )
                            if updated.rowcount != 1:
                                current_candidate = db.execute(
                                    """SELECT status FROM candidate_artifacts
                                       WHERE profile_id=? AND profile_generation=?
                                         AND candidate_id=?""",
                                    (
                                        owner.profile_id,
                                        owner.profile_generation,
                                        request.candidate_id,
                                    ),
                                ).fetchone()
                                if (
                                    current_candidate is None
                                    or current_candidate["status"] != "rollback_pending"
                                ):
                                    raise CompanionConflictError(
                                        "capability_mutation_adopted_candidate_state_conflict"
                                    ) from exc
                        return dict(
                            db.execute(
                                """SELECT * FROM capability_activation_requests
                                   WHERE profile_id=? AND profile_generation=?
                                     AND activation_request_id=?""",
                                (
                                    owner.profile_id,
                                    owner.profile_generation,
                                    in_progress["activation_request_id"],
                                ),
                            ).fetchone()
                        )
                    raise CompanionStateError(
                        "capability_mutation_in_progress:"
                        + str(in_progress["activation_request_id"])
                    ) from exc
                raise
            audit_id = canonical_hash(["mutation_request", request.request_id])
            audit_hash = canonical_hash(
                {
                    "request_id": request.request_id,
                    "fingerprint": request.request_fingerprint,
                    "action": action,
                }
            )
            db.execute(
                """INSERT INTO audit_events(
                     profile_id,profile_generation,audit_id,actor,action,reason_code,
                     after_hash,lineage_ref,audit_hash,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    audit_id,
                    "companion_host",
                    f"capability_{action}_requested",
                    request.reason_code,
                    request.request_fingerprint,
                    request.request_id,
                    audit_hash,
                    now,
                ),
            )
            if request.candidate_id:
                db.execute(
                    """INSERT INTO lineage_edges(
                         profile_id,profile_generation,from_kind,from_id,to_kind,to_id,relation,
                         reason_code,schema_version,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        "candidate",
                        request.candidate_id,
                        "capability_mutation_request",
                        request.request_id,
                        action,
                        request.reason_code,
                        now,
                    ),
                )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["capability_mutation", request.request_id]),
                event_kind="capability_mutation",
                event_id=request.request_id,
                sink_kind="capability_platform",
                payload={
                    "schema_version": 1,
                    "activation_request_id": request.request_id,
                    "request_fingerprint": request.request_fingerprint,
                    "action": action,
                },
                now=now,
                reason_code=request.reason_code,
            )
            self._bump_detail(db, owner, mutation_hash=request.request_fingerprint, now=now)
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=? AND activation_request_id=?""",
                    (owner.profile_id, owner.profile_generation, request.request_id),
                ).fetchone()
            )

    def get_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
    ) -> Mapping[str, Any]:
        """Read one durable mutation intent without exposing another owner."""

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("capability_mutation_request_missing")
            return dict(row)

    def claim_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        lease_seconds: float,
    ) -> Mapping[str, Any]:
        """Claim an activation saga or recover an expired claim.

        The request row is the only scheduler authority.  A lease steal never
        authorizes a fresh Manager operation: callers must first reconcile the
        stable ``manager_idempotency_key`` and any persisted operation/set
        references.
        """

        if not claim_owner:
            raise ValueError("claim_owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_dt = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        now = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("capability_mutation_request_missing")
            expired = (
                row["status"] in {"claimed", "staging", "staged", "publishing"}
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"] <= now
            )
            if row["status"] != "pending" and not expired:
                raise CompanionLeaseError("capability_mutation_not_claimable")
            next_epoch = int(row["claim_epoch"]) + 1
            next_attempt = int(row["attempt"]) + 1
            db.execute(
                """UPDATE capability_activation_requests
                   SET status='claimed',claim_owner=?,claim_epoch=?,
                       lease_expires_at=?,attempt=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=? AND claim_epoch=?""",
                (
                    claim_owner,
                    next_epoch,
                    expiry,
                    next_attempt,
                    (
                        "capability_mutation_recovery_claimed"
                        if expired
                        else "capability_mutation_claimed"
                    ),
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                    row["claim_epoch"],
                ),
            )
            claimed = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                ),
            ).fetchone()
            if (
                claimed is None
                or claimed["claim_owner"] != claim_owner
                or int(claimed["claim_epoch"]) != next_epoch
            ):
                raise CompanionLeaseError("capability_mutation_claim_lost")
            return dict(claimed)

    def record_capability_mutation_prepared(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        manager_operation_id: str,
        runtime_set_ref: str | None,
        runtime_set_hash: str | None,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Persist trusted staging identities before any runtime start."""

        if not manager_operation_id:
            raise ValueError("manager_operation_id is required")
        if (runtime_set_ref is None) != (runtime_set_hash is None):
            raise ValueError("runtime set ref/hash must be both present or absent")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("capability_mutation_request_missing")
            if row["claim_owner"] != claim_owner or int(row["claim_epoch"]) != claim_epoch:
                raise CompanionLeaseError("capability_mutation_claim_stale")
            if row["status"] == "staged":
                if (
                    row["manager_operation_id"] != manager_operation_id
                    or row["runtime_set_ref"] != runtime_set_ref
                    or row["runtime_set_hash"] != runtime_set_hash
                ):
                    raise CompanionConflictError("capability_mutation_prepared_conflict")
                return dict(row)
            if row["status"] not in {"claimed", "staging"}:
                raise CompanionStateError("capability_mutation_not_stageable")
            db.execute(
                """UPDATE capability_activation_requests
                   SET status='staged',manager_operation_id=?,runtime_set_ref=?,
                       runtime_set_hash=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=? AND claim_owner=?
                     AND claim_epoch=? AND status IN ('claimed','staging')""",
                (
                    manager_operation_id,
                    runtime_set_ref,
                    runtime_set_hash,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=?
                         AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request_id,
                    ),
                ).fetchone()
            )

    def mark_capability_mutation_publishing(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Close the final publish boundary for one exact prepared request."""

        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            updated = db.execute(
                """UPDATE capability_activation_requests
                   SET status='publishing',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=? AND claim_owner=?
                     AND claim_epoch=? AND status='staged'
                     AND manager_operation_id IS NOT NULL""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if updated.rowcount != 1:
                current = db.execute(
                    """SELECT status,claim_owner,claim_epoch
                       FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=?
                         AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request_id,
                    ),
                ).fetchone()
                if (
                    current is not None
                    and current["status"] == "publishing"
                    and current["claim_owner"] == claim_owner
                    and int(current["claim_epoch"]) == claim_epoch
                ):
                    return dict(current)
                raise CompanionLeaseError("capability_mutation_publish_claim_stale")
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=?
                         AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request_id,
                    ),
                ).fetchone()
            )

    def fail_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        status: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Settle an execution failure without erasing durable recovery refs."""

        if status not in {"failed", "stale", "unknown", "cleanup_required"}:
            raise ValueError("capability mutation failure status is invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            updated = db.execute(
                """UPDATE capability_activation_requests
                   SET status=?,lease_expires_at=NULL,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=? AND claim_owner=?
                     AND claim_epoch=? AND status IN (
                       'claimed','staging','staged','publishing','unknown',
                       'cleanup_required'
                     )""",
                (
                    status,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionLeaseError("capability_mutation_failure_claim_stale")
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=?
                         AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request_id,
                    ),
                ).fetchone()
            )
