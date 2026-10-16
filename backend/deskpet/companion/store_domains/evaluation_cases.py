"""Evaluation case claims, launches, completion and recovery."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from ..contracts import (
    CompanionConflictError,
    CompanionLeaseError,
    CompanionStateError,
    EvaluationCaseLaunch,
    OwnerRef,
)
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class EvaluationCaseMixin(CompanionStoreCore):
    """Evaluation case claims, launches, completion and recovery."""

    __slots__ = ()

    def claim_evaluation_case(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        lease_seconds: float,
    ) -> Mapping[str, Any]:
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
                """SELECT c.*,i.content_state,e.status AS evaluation_status,
                          a.status AS candidate_status,p.status AS permit_status,
                          p.permit_id,p.permit_hash
                   FROM evaluation_cases c
                   JOIN evaluation_case_inputs i
                     ON i.profile_id=c.profile_id AND i.profile_generation=c.profile_generation
                    AND i.evaluation_id=c.evaluation_id AND i.input_id=c.input_id
                   JOIN evaluation_runs e
                     ON e.profile_id=c.profile_id AND e.profile_generation=c.profile_generation
                    AND e.evaluation_id=c.evaluation_id
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN evaluation_execution_permits p
                     ON p.profile_id=e.profile_id AND p.profile_generation=e.profile_generation
                    AND p.evaluation_id=e.evaluation_id
                   WHERE c.profile_id=? AND c.profile_generation=? AND c.evaluation_id=?
                     AND c.case_id=? AND c.variant=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_case_missing")
            if (
                row["content_state"] != "live"
                or row["evaluation_status"] not in {"queued", "running"}
                or row["candidate_status"] in {"invalidated", "expired", "stale"}
                or row["permit_status"] not in {"issued", "claimed"}
            ):
                raise CompanionStateError("evaluation_case_fence_closed")
            expired_lease = (
                row["status"] == "leased"
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"] <= now
            )
            if row["status"] != "queued" and not expired_lease:
                raise CompanionLeaseError("evaluation_case_not_claimable")
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            recovery_only = int(expired_lease)
            db.execute(
                """UPDATE evaluation_cases
                   SET status='leased',claim_owner=?,claim_epoch=?,lease_expires_at=?,
                       attempt=?,recovery_only=?,reason_code='case_claimed',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                     AND case_id=? AND variant=? AND claim_epoch=?""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    recovery_only,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    row["claim_epoch"],
                ),
            )
            db.execute(
                """UPDATE evaluation_execution_permits SET status='claimed',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND permit_id=? AND status='issued'""",
                (now, owner.profile_id, owner.profile_generation, row["permit_id"]),
            )
            db.execute(
                """UPDATE evaluation_runs SET status='running',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=? AND status='queued'""",
                (now, owner.profile_id, owner.profile_generation, evaluation_id),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_cases
                       WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                         AND case_id=? AND variant=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        case_id,
                        variant,
                    ),
                ).fetchone()
            )

    def get_evaluation_execution_fence(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
    ) -> Mapping[str, Any]:
        """Return one joined, owner-scoped snapshot for a short start fence."""

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT
                     e.evaluation_id,e.status AS evaluation_status,e.candidate_id,
                     e.suite_hash,e.runner_policy_hash,
                     a.status AS candidate_status,
                     p.candidate_package_hash AS package_hash,
                     p.candidate_manifest_hash AS manifest_hash,
                     p.archive_hash,
                     x.permit_id,x.mode AS permit_mode,x.permit_hash,
                     x.status AS permit_status,x.issued_revocation_epoch,
                     x.preflight_ref,x.preflight_hash,x.risk_ref,x.risk_hash,
                     x.authorization_id,
                     r.risk AS risk_level,
                     CASE
                       WHEN x.risk_hash=r.risk_hash
                        AND r.candidate_package_hash=p.candidate_package_hash
                        AND (
                          (x.mode='safe_auto' AND r.risk='low'
                           AND x.authorization_id IS NULL)
                          OR
                          (x.mode='safe_static' AND r.risk<>'low'
                           AND x.authorization_id IS NULL
                           AND e.runner_policy_hash=
                             '557ea1903de386bd8e029119bc6adc0e144e5b44b5fdffad4374766d1fe7319c'
                           AND json_extract(
                             r.static_preflight_json,'$.candidate_kind'
                           )='instruction')
                          OR
                          (x.mode='user_authorized' AND x.authorization_id IS NOT NULL
                           AND z.consumed_at IS NOT NULL
                           AND z.candidate_id=e.candidate_id
                           AND z.package_hash=p.candidate_package_hash
                           AND z.suite_hash=e.suite_hash
                           AND z.runner_policy_hash=e.runner_policy_hash)
                        )
                       THEN 1 ELSE 0
                     END AS policy_verified,
                     CASE WHEN z.consumed_at IS NOT NULL THEN 1 ELSE 0 END
                       AS authorization_consumed,
                     c.case_id,c.variant,c.status AS case_status,c.claim_owner,
                     c.claim_epoch,c.attempt AS attempt_ordinal,c.recovery_only,
                     c.input_hash,i.content_state AS input_content_state,
                     i.adapter_id,i.adapter_version,
                     i.adapter_build_fingerprint AS adapter_fingerprint
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id
                    AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN evaluation_execution_permits x
                     ON x.profile_id=e.profile_id
                    AND x.profile_generation=e.profile_generation
                    AND x.evaluation_id=e.evaluation_id
                   JOIN risk_assessments r
                     ON r.profile_id=e.profile_id
                    AND r.profile_generation=e.profile_generation
                    AND r.risk_id=x.risk_ref
                    AND r.candidate_id=e.candidate_id
                   LEFT JOIN evaluation_authorizations z
                     ON z.profile_id=x.profile_id
                    AND z.profile_generation=x.profile_generation
                    AND z.authorization_id=x.authorization_id
                   JOIN evaluation_cases c
                     ON c.profile_id=e.profile_id
                    AND c.profile_generation=e.profile_generation
                    AND c.evaluation_id=e.evaluation_id
                   JOIN evaluation_case_inputs i
                     ON i.profile_id=c.profile_id
                    AND i.profile_generation=c.profile_generation
                    AND i.evaluation_id=c.evaluation_id
                    AND i.input_id=c.input_id
                   WHERE e.profile_id=? AND e.profile_generation=?
                     AND e.evaluation_id=? AND c.case_id=? AND c.variant=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_execution_fence_missing")
            return dict(row)

    def create_evaluation_case_launch(
        self,
        owner: OwnerRef,
        launch: EvaluationCaseLaunch,
        *,
        claim_owner: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        launch_facts = {
            "schema_version": launch.schema_version,
            "launch_id": launch.launch_id,
            "evaluation_id": launch.evaluation_id,
            "case_id": launch.case_id,
            "variant": launch.variant,
            "attempt_ordinal": launch.attempt_ordinal,
            "candidate_package_hash": launch.candidate_package_hash,
            "candidate_manifest_hash": launch.candidate_manifest_hash,
            "candidate_archive_hash": launch.candidate_archive_hash,
            "suite_hash": launch.suite_hash,
            "permit_mode": launch.permit_mode,
            "permit_id": launch.permit_id,
            "permit_hash": launch.permit_hash,
            "case_lease_epoch": launch.case_lease_epoch,
            "revocation_epoch": launch.revocation_epoch,
            "adapter_id": launch.adapter_id,
            "adapter_version": launch.adapter_version,
            "adapter_fingerprint": launch.adapter_fingerprint,
        }
        if canonical_hash(launch_facts) != launch.launch_fingerprint:
            raise CompanionConflictError("evaluation_launch_fingerprint_mismatch")
        expected = {
            "evaluation_id": launch.evaluation_id,
            "case_id": launch.case_id,
            "variant": launch.variant,
            "attempt_ordinal": launch.attempt_ordinal,
            "candidate_package_hash": launch.candidate_package_hash,
            "candidate_manifest_hash": launch.candidate_manifest_hash,
            "candidate_archive_hash": launch.candidate_archive_hash,
            "suite_hash": launch.suite_hash,
            "permit_mode": launch.permit_mode,
            "permit_id": launch.permit_id,
            "permit_hash": launch.permit_hash,
            "case_lease_epoch": launch.case_lease_epoch,
            "revocation_epoch": launch.revocation_epoch,
            "adapter_id": launch.adapter_id,
            "adapter_version": launch.adapter_version,
            "adapter_fingerprint": launch.adapter_fingerprint,
            "launch_fingerprint": launch.launch_fingerprint,
            "schema_version": launch.schema_version,
        }
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_case_launches
                   WHERE profile_id=? AND profile_generation=?
                     AND (launch_id=? OR
                          (evaluation_id=? AND case_id=? AND variant=? AND attempt_ordinal=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    launch.launch_id,
                    launch.evaluation_id,
                    launch.case_id,
                    launch.variant,
                    launch.attempt_ordinal,
                ),
            ).fetchone()
            if existing is not None:
                if existing["launch_id"] != launch.launch_id or not self._same(existing, expected):
                    raise CompanionConflictError("evaluation_launch_conflict")
                return dict(existing)
            row = db.execute(
                """SELECT c.*,e.suite_hash,a.status AS candidate_status,
                          p.candidate_package_hash,p.candidate_manifest_hash,p.archive_hash,
                          x.mode AS permit_mode,x.permit_hash,x.issued_revocation_epoch,
                          x.status AS permit_status
                   FROM evaluation_cases c
                   JOIN evaluation_runs e
                     ON e.profile_id=c.profile_id AND e.profile_generation=c.profile_generation
                    AND e.evaluation_id=c.evaluation_id
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN evaluation_execution_permits x
                     ON x.profile_id=e.profile_id AND x.profile_generation=e.profile_generation
                    AND x.evaluation_id=e.evaluation_id
                   WHERE c.profile_id=? AND c.profile_generation=? AND c.evaluation_id=?
                     AND c.case_id=? AND c.variant=? AND x.permit_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    launch.evaluation_id,
                    launch.case_id,
                    launch.variant,
                    launch.permit_id,
                ),
            ).fetchone()
            if (
                row is None
                or row["status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != launch.case_lease_epoch
            ):
                raise CompanionLeaseError("evaluation_case_lease_stale")
            if int(row["recovery_only"]):
                raise CompanionStateError("evaluation_case_recovery_only")
            if launch.attempt_ordinal != int(row["attempt"]):
                raise CompanionConflictError("evaluation_launch_attempt_mismatch")
            if (
                row["candidate_status"] in {"invalidated", "expired", "stale"}
                or row["permit_status"] not in {"issued", "claimed"}
                or row["candidate_package_hash"] != launch.candidate_package_hash
                or row["candidate_manifest_hash"] != launch.candidate_manifest_hash
                or row["archive_hash"] != launch.candidate_archive_hash
                or row["suite_hash"] != launch.suite_hash
                or row["permit_mode"] != launch.permit_mode
                or row["permit_hash"] != launch.permit_hash
                or int(row["issued_revocation_epoch"]) != launch.revocation_epoch
            ):
                raise CompanionStateError("evaluation_launch_fence_mismatch")
            try:
                db.execute(
                    """INSERT INTO evaluation_case_launches(
                         profile_id,profile_generation,evaluation_id,case_id,variant,attempt_ordinal,
                         launch_id,candidate_package_hash,candidate_manifest_hash,
                         candidate_archive_hash,suite_hash,permit_mode,permit_id,permit_hash,
                         case_lease_epoch,revocation_epoch,adapter_id,adapter_version,
                         adapter_fingerprint,launch_fingerprint,status,reason_code,schema_version,
                         created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'claimed',?,?,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        launch.evaluation_id,
                        launch.case_id,
                        launch.variant,
                        launch.attempt_ordinal,
                        launch.launch_id,
                        launch.candidate_package_hash,
                        launch.candidate_manifest_hash,
                        launch.candidate_archive_hash,
                        launch.suite_hash,
                        launch.permit_mode,
                        launch.permit_id,
                        launch.permit_hash,
                        launch.case_lease_epoch,
                        launch.revocation_epoch,
                        launch.adapter_id,
                        launch.adapter_version,
                        launch.adapter_fingerprint,
                        launch.launch_fingerprint,
                        launch.reason_code,
                        launch.schema_version,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise CompanionStateError(str(exc)) from exc
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_case_launches
                       WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                    (owner.profile_id, owner.profile_generation, launch.launch_id),
                ).fetchone()
            )

    def record_evaluation_case_start_ack(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        ack: Any,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Persist exact Job/process start identity before releasing the fence."""

        now = self._now()
        ack_facts = {
            "schema_version": 1,
            "launch_id": ack.launch_id,
            "runtime_instance_id": ack.runtime_instance_id,
            "adapter_identity": ack.adapter_identity,
            "job_identity": ack.job_identity,
            "pid": ack.pid,
            "process_create_time": ack.process_create_time,
            "command_line_hash": ack.command_line_hash,
        }
        if canonical_hash(ack_facts) != ack.start_receipt_hash:
            raise CompanionConflictError("evaluation_start_ack_hash_mismatch")
        runtime_identity = canonical_json(
            {
                "runtime_instance_id": ack.runtime_instance_id,
                "adapter_identity": ack.adapter_identity,
                "pid": ack.pid,
                "process_create_time": ack.process_create_time,
                "command_line_hash": ack.command_line_hash,
            }
        )
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT l.*,c.status AS case_status,c.claim_owner,c.claim_epoch
                   FROM evaluation_case_launches l
                   JOIN evaluation_cases c
                     ON c.profile_id=l.profile_id
                    AND c.profile_generation=l.profile_generation
                    AND c.evaluation_id=l.evaluation_id
                    AND c.case_id=l.case_id AND c.variant=l.variant
                   WHERE l.profile_id=? AND l.profile_generation=?
                     AND l.evaluation_id=? AND l.case_id=? AND l.variant=?
                     AND l.launch_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    ack.launch_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_launch_missing")
            if row["status"] == "started":
                if (
                    row["job_identity"] != ack.job_identity
                    or row["runtime_identity"] != runtime_identity
                    or row["outcome_hash"] != ack.start_receipt_hash
                ):
                    raise CompanionConflictError("evaluation_start_ack_conflict")
                return dict(row)
            if (
                row["status"] != "claimed"
                or row["case_status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
                or int(row["case_lease_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError("evaluation_start_ack_fence_stale")
            db.execute(
                """UPDATE evaluation_case_launches
                   SET status='started',start_outcome='started',
                       started_ack_at=?,job_identity=?,runtime_identity=?,
                       outcome_hash=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND launch_id=?
                     AND status='claimed'""",
                (
                    now,
                    ack.job_identity,
                    runtime_identity,
                    ack.start_receipt_hash,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    ack.launch_id,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_case_launches
                       WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        ack.launch_id,
                    ),
                ).fetchone()
            )

    def commit_evaluation_case_completion(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        launch_id: str,
        completion: Any,
    ) -> Mapping[str, Any]:
        """Atomically settle launch, result, and case terminal state."""

        if completion.cleanup_receipt_hash is None or completion.survivor_count != 0:
            raise CompanionStateError("evaluation_completion_cleanup_not_proven")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_results
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND case_id=? AND variant=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            if existing is not None:
                if existing["result_hash"] != completion.result_hash:
                    raise CompanionConflictError("evaluation_result_conflict")
                return dict(existing)
            row = db.execute(
                """SELECT l.status AS launch_status,c.status AS case_status,
                          c.claim_owner,c.claim_epoch
                   FROM evaluation_case_launches l
                   JOIN evaluation_cases c
                     ON c.profile_id=l.profile_id
                    AND c.profile_generation=l.profile_generation
                    AND c.evaluation_id=l.evaluation_id
                    AND c.case_id=l.case_id AND c.variant=l.variant
                   WHERE l.profile_id=? AND l.profile_generation=?
                     AND l.evaluation_id=? AND l.case_id=? AND l.variant=?
                     AND l.launch_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    launch_id,
                ),
            ).fetchone()
            if (
                row is None
                or row["launch_status"] != "started"
                or row["case_status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError("evaluation_completion_fence_stale")
            db.execute(
                """INSERT INTO evaluation_results(
                     profile_id,profile_generation,evaluation_id,case_id,variant,
                     assertions_json,judge_result_json,usage_json,result_hash,
                     reason_code,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    canonical_json(dict(completion.assertions)),
                    canonical_json(dict(completion.judge_result)),
                    canonical_json(dict(completion.usage)),
                    completion.result_hash,
                    completion.reason_code,
                    now,
                ),
            )
            db.execute(
                """UPDATE evaluation_case_launches
                   SET status='completed',outcome_ref=?,outcome_hash=?,
                       cleanup_receipt_hash=?,survivor_count=0,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND launch_id=?
                     AND status='started'""",
                (
                    completion.outcome_ref,
                    completion.outcome_hash,
                    completion.cleanup_receipt_hash,
                    completion.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    launch_id,
                ),
            )
            db.execute(
                """UPDATE evaluation_cases
                   SET status='committed',lease_expires_at=NULL,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND case_id=? AND variant=?
                     AND claim_owner=? AND claim_epoch=? AND status='leased'""",
                (
                    completion.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_results
                       WHERE profile_id=? AND profile_generation=?
                         AND evaluation_id=? AND case_id=? AND variant=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        case_id,
                        variant,
                    ),
                ).fetchone()
            )

    def abort_evaluation_case_execution(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        launch_id: str,
        expected_launch_status: str,
        cleanup_receipt_hash: str | None,
        survivor_count: int | None,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Monotonically make an unknown/started case non-passable."""

        terminal = (
            "aborted"
            if survivor_count == 0 and cleanup_receipt_hash is not None
            else "cleanup_required"
        )
        case_terminal = "inconclusive" if terminal == "aborted" else "cleanup_required"
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT l.*,c.status AS case_status,c.claim_owner,c.claim_epoch
                   FROM evaluation_case_launches l
                   JOIN evaluation_cases c
                     ON c.profile_id=l.profile_id
                    AND c.profile_generation=l.profile_generation
                    AND c.evaluation_id=l.evaluation_id
                    AND c.case_id=l.case_id AND c.variant=l.variant
                   WHERE l.profile_id=? AND l.profile_generation=?
                     AND l.evaluation_id=? AND l.case_id=? AND l.variant=?
                     AND l.launch_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    launch_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_launch_missing")
            if row["status"] in {"aborted", "cleanup_required"}:
                return dict(row)
            if (
                row["status"] != expected_launch_status
                or row["case_status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError("evaluation_abort_fence_stale")
            db.execute(
                """UPDATE evaluation_case_launches
                   SET status=?,cleanup_receipt_hash=?,survivor_count=?,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND launch_id=?
                     AND status=?""",
                (
                    terminal,
                    cleanup_receipt_hash,
                    survivor_count,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    launch_id,
                    expected_launch_status,
                ),
            )
            db.execute(
                """UPDATE evaluation_cases
                   SET status=?,lease_expires_at=NULL,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND case_id=? AND variant=?
                     AND claim_owner=? AND claim_epoch=? AND status='leased'""",
                (
                    case_terminal,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    claim_owner,
                    claim_epoch,
                ),
            )
            db.execute(
                """UPDATE evaluation_runs
                   SET status='inconclusive',lease_expires_at=NULL,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND status IN ('queued','running')""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_case_launches
                       WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        launch_id,
                    ),
                ).fetchone()
            )

    def get_evaluation_case_recovery_state(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_epoch: int,
    ) -> Mapping[str, Any] | None:
        """Expose the previous physical identity; recovery never relaunches it."""

        del claim_epoch
        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT launch_id,status AS launch_status,start_outcome,
                          job_identity,runtime_identity,session_identity,
                          outcome_ref,outcome_hash,cleanup_receipt_hash,
                          survivor_count,reason_code,attempt_ordinal
                   FROM evaluation_case_launches
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND case_id=? AND variant=?
                   ORDER BY attempt_ordinal DESC LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            return None if row is None else dict(row)

    def settle_evaluation_case_launch(
        self,
        owner: OwnerRef,
        *,
        launch_id: str,
        expected_status: str,
        status: str,
        outcome_ref: str | None = None,
        outcome_hash: str | None = None,
        cleanup_receipt_hash: str | None = None,
        survivor_count: int | None = None,
        reason_code: str,
    ) -> Mapping[str, Any]:
        allowed = {
            "claimed": {"started", "not_started", "unknown"},
            "started": {"completed", "failed", "unknown", "aborting"},
            "unknown": {"aborting", "cleanup_required"},
            "aborting": {"aborted", "cleanup_required"},
            "cleanup_required": {"aborting"},
        }
        if status not in allowed.get(expected_status, set()):
            raise CompanionStateError("evaluation_launch_transition_invalid")
        if status == "not_started" and (cleanup_receipt_hash is None or survivor_count != 0):
            raise CompanionStateError("evaluation_not_started_requires_zero_survivors")
        if status in {"completed", "failed"} and (outcome_ref is None or outcome_hash is None):
            raise CompanionStateError("evaluation_launch_outcome_required")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM evaluation_case_launches
                   WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                (owner.profile_id, owner.profile_generation, launch_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_launch_missing")
            if row["status"] == status:
                if (
                    row["outcome_ref"] != outcome_ref
                    or row["outcome_hash"] != outcome_hash
                    or row["cleanup_receipt_hash"] != cleanup_receipt_hash
                    or row["survivor_count"] != survivor_count
                ):
                    raise CompanionConflictError("evaluation_launch_settle_conflict")
                return dict(row)
            if row["status"] != expected_status:
                raise CompanionConflictError("evaluation_launch_state_conflict")
            db.execute(
                """UPDATE evaluation_case_launches
                   SET status=?,start_outcome=COALESCE(start_outcome,?),
                       started_ack_at=CASE WHEN ?='started' THEN COALESCE(started_ack_at,?) ELSE started_ack_at END,
                       outcome_ref=?,outcome_hash=?,cleanup_receipt_hash=?,survivor_count=?,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND launch_id=? AND status=?""",
                (
                    status,
                    status,
                    status,
                    now,
                    outcome_ref,
                    outcome_hash,
                    cleanup_receipt_hash,
                    survivor_count,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    launch_id,
                    expected_status,
                ),
            )
            if status == "not_started":
                db.execute(
                    """UPDATE evaluation_cases
                       SET status='queued',claim_owner=NULL,lease_expires_at=NULL,
                           recovery_only=0,next_retry_at=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                         AND case_id=? AND variant=? AND claim_epoch=?""",
                    (
                        now,
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        row["evaluation_id"],
                        row["case_id"],
                        row["variant"],
                        row["case_lease_epoch"],
                    ),
                )
            elif status in {"unknown", "cleanup_required"}:
                db.execute(
                    """UPDATE evaluation_cases SET status='cleanup_required',
                         reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                         AND case_id=? AND variant=? AND claim_epoch=?""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        row["evaluation_id"],
                        row["case_id"],
                        row["variant"],
                        row["case_lease_epoch"],
                    ),
                )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_case_launches
                       WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                    (owner.profile_id, owner.profile_generation, launch_id),
                ).fetchone()
            )
