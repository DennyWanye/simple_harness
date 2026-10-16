"""Evaluation results, reports and post-processing recovery."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import (
    CompanionConflictError,
    CompanionLeaseError,
    CompanionStateError,
    GrowthEvent,
    OwnerRef,
)
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


def _clone_companion_json_object(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompanionStateError(code)
    cloned = json.loads(canonical_json(dict(value)))
    if not isinstance(cloned, dict):
        raise CompanionStateError(code)
    return cloned


class EvaluationResultMixin(CompanionStoreCore):
    """Evaluation results, reports and post-processing recovery."""

    __slots__ = ()

    def record_evaluation_result(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        assertions: Mapping[str, Any],
        judge_result: Mapping[str, Any],
        usage: Mapping[str, Any],
        result_hash: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        payload = {
            "assertions": dict(assertions),
            "judge_result": dict(judge_result),
            "usage": dict(usage),
        }
        if canonical_hash(payload) != result_hash:
            raise CompanionConflictError("evaluation_result_hash_mismatch")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_results
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
            if existing is not None:
                if existing["result_hash"] != result_hash:
                    raise CompanionConflictError("evaluation_result_conflict")
                return dict(existing)
            case = db.execute(
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
            launch = db.execute(
                """SELECT * FROM evaluation_case_launches
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                     AND case_id=? AND variant=? AND case_lease_epoch=?
                     AND status='completed' ORDER BY attempt_ordinal DESC LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    claim_epoch,
                ),
            ).fetchone()
            if (
                case is None
                or case["status"] != "leased"
                or case["claim_owner"] != claim_owner
                or int(case["claim_epoch"]) != claim_epoch
                or launch is None
            ):
                raise CompanionLeaseError("evaluation_result_lease_or_launch_invalid")
            db.execute(
                """INSERT INTO evaluation_results(
                     profile_id,profile_generation,evaluation_id,case_id,variant,
                     assertions_json,judge_result_json,usage_json,result_hash,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    canonical_json(dict(assertions)),
                    canonical_json(dict(judge_result)),
                    canonical_json(dict(usage)),
                    result_hash,
                    reason_code,
                    now,
                ),
            )
            db.execute(
                """UPDATE evaluation_cases SET status='committed',lease_expires_at=NULL,
                     reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                     AND case_id=? AND variant=? AND claim_owner=? AND claim_epoch=?""",
                (
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
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_results
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

    def create_evaluation_report(
        self,
        owner: OwnerRef,
        *,
        report_id: str,
        evaluation_id: str,
        candidate_package_hash: str,
        dataset_hash: str,
        suite_hash: str,
        required_cases: Sequence[tuple[str, str]],
        results_root_hash: str,
        verdict: str,
        reason_code: str,
        failed_replan: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if verdict not in {"passed", "failed", "inconclusive"}:
            raise ValueError("evaluation_report_verdict_invalid")
        required = sorted(set(required_cases))
        if not required:
            raise CompanionStateError("evaluation_report_requires_cases")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_reports
                   WHERE profile_id=? AND profile_generation=?
                     AND (report_id=? OR evaluation_id=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    report_id,
                    evaluation_id,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    existing["report_id"] != report_id
                    or existing["candidate_package_hash"] != candidate_package_hash
                    or existing["suite_hash"] != suite_hash
                    or existing["results_root_hash"] != results_root_hash
                    or existing["verdict"] != verdict
                ):
                    raise CompanionConflictError("evaluation_report_conflict")
                return dict(existing)
            candidate_facts, result_items = self._load_evaluation_result_facts(
                candidate_package_hash,
                db=db,
                evaluation_id=evaluation_id,
                owner=owner,
                required=required,
                suite_hash=suite_hash,
            )
            if canonical_hash(result_items) != results_root_hash:
                raise CompanionConflictError("evaluation_results_root_mismatch")
            db.execute(
                """INSERT INTO evaluation_reports(
                     profile_id,profile_generation,report_id,evaluation_id,
                     candidate_package_hash,dataset_hash,suite_hash,results_root_hash,
                     verdict,reason_code,required_case_count,committed_result_count,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    report_id,
                    evaluation_id,
                    candidate_package_hash,
                    dataset_hash,
                    suite_hash,
                    results_root_hash,
                    verdict,
                    reason_code,
                    len(required),
                    len(result_items),
                    now,
                ),
            )
            self._write_evaluation_report_rows(
                candidate_facts,
                db=db,
                evaluation_id=evaluation_id,
                failed_replan=failed_replan,
                now=now,
                owner=owner,
                reason_code=reason_code,
                verdict=verdict,
            )
            static_preflight = json.loads(str(candidate_facts["static_preflight_json"]))
            topology_diff = json.loads(str(candidate_facts["effect_topology_diff_json"]))
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["evaluation_reported", report_id]),
                event_kind="evaluation_reported",
                event_id=report_id,
                sink_kind="companion_notification",
                payload={
                    "schema_version": 2,
                    "report_id": report_id,
                    "evaluation_id": evaluation_id,
                    "evaluation_report_hash": results_root_hash,
                    "candidate_id": str(candidate_facts["candidate_id"]),
                    "candidate_mode": str(candidate_facts["candidate_mode"]),
                    "pack_id": str(candidate_facts["pack_id"]),
                    "candidate_version": str(candidate_facts["version"]),
                    "candidate_content_hash": str(candidate_facts["candidate_content_hash"]),
                    "candidate_package_hash": candidate_package_hash,
                    "candidate_manifest_hash": str(candidate_facts["candidate_manifest_hash"]),
                    "candidate_archive_hash": str(candidate_facts["archive_hash"]),
                    "candidate_code_digest": None,
                    "risk_id": str(candidate_facts["risk_id"]),
                    "risk_assessment_hash": str(candidate_facts["risk_hash"]),
                    "risk_level": str(candidate_facts["risk"]),
                    "risk_preflight": static_preflight,
                    "risk_topology_diff": topology_diff,
                    "owner_key": str(candidate_facts["target_owner_key"]),
                    "scope": str(candidate_facts["target_scope"]),
                    "scope_key": str(candidate_facts["target_scope_key"]),
                    "target_owner_key": str(candidate_facts["target_owner_key"]),
                    "target_scope": str(candidate_facts["target_scope"]),
                    "target_scope_key": str(candidate_facts["target_scope_key"]),
                    "target_expected_absent": bool(candidate_facts["target_expected_absent"]),
                    "expected_binding_generation": int(
                        candidate_facts["target_expected_binding_generation"]
                    ),
                    "source_owner_key": candidate_facts["source_owner_key"],
                    "source_scope": candidate_facts["source_scope"],
                    "source_scope_key": candidate_facts["source_scope_key"],
                    "source_version": candidate_facts["source_version"],
                    "source_manifest_hash": candidate_facts["source_manifest_hash"],
                    "source_binding_generation": candidate_facts["source_binding_generation"],
                    "dataset_hash": dataset_hash,
                    "suite_hash": suite_hash,
                    "results_root_hash": results_root_hash,
                    "verdict": verdict,
                    "reason_code": reason_code,
                },
                now=now,
                reason_code=reason_code,
            )
            return self._bump_evaluation_report_detail(
                db, now=now, owner=owner, report_id=report_id, results_root_hash=results_root_hash
            )

    def _bump_evaluation_report_detail(
        self, db, *, now, owner: OwnerRef, report_id: str, results_root_hash: str
    ) -> Mapping[str, Any]:
        self._bump_detail(
            db,
            owner,
            mutation_hash=canonical_hash(["evaluation_report", report_id, results_root_hash]),
            now=now,
        )
        return dict(
            db.execute(
                """SELECT * FROM evaluation_reports
                       WHERE profile_id=? AND profile_generation=? AND report_id=?""",
                (owner.profile_id, owner.profile_generation, report_id),
            ).fetchone()
        )

    def _load_evaluation_result_facts(
        self,
        candidate_package_hash: str,
        *,
        db,
        evaluation_id: str,
        owner: OwnerRef,
        required,
        suite_hash: str,
    ):
        evaluation = db.execute(
            """SELECT e.*,p.candidate_package_hash
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   WHERE e.profile_id=? AND e.profile_generation=? AND e.evaluation_id=?""",
            (owner.profile_id, owner.profile_generation, evaluation_id),
        ).fetchone()
        if (
            evaluation is None
            or evaluation["candidate_package_hash"] != candidate_package_hash
            or evaluation["suite_hash"] != suite_hash
        ):
            raise CompanionConflictError("evaluation_report_fence_mismatch")
        candidate_facts = db.execute(
            """SELECT
                     e.candidate_id,e.candidate_mode,
                     a.status AS candidate_status,
                     a.reason_code AS candidate_reason_code,
                     a.target_id,
                     a.source_owner_key,a.source_scope,a.source_scope_key,
                     a.source_version,a.source_manifest_hash,
                     a.source_binding_generation,
                     a.target_owner_key,a.target_scope,a.target_scope_key,
                     a.target_expected_absent,
                     a.target_expected_binding_generation,
                     p.pack_id,p.version,p.candidate_content_hash,
                     p.candidate_manifest_hash,p.candidate_package_hash,
                     p.archive_hash,
                     r.risk_id,r.risk_hash,r.risk,
                     r.static_preflight_json,r.effect_topology_diff_json
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id
                    AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN risk_assessments r
                     ON r.profile_id=e.profile_id
                    AND r.profile_generation=e.profile_generation
                    AND r.candidate_id=e.candidate_id
                    AND r.candidate_package_hash=p.candidate_package_hash
                   WHERE e.profile_id=? AND e.profile_generation=?
                     AND e.evaluation_id=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                evaluation_id,
            ),
        ).fetchone()
        if candidate_facts is None:
            raise CompanionStateError("evaluation_report_risk_assessment_missing")
        result_items: list[dict[str, str]] = []
        for case_id, variant in required:
            row = db.execute(
                """SELECT c.status,r.result_hash
                       FROM evaluation_cases c
                       LEFT JOIN evaluation_results r
                         ON r.profile_id=c.profile_id AND r.profile_generation=c.profile_generation
                        AND r.evaluation_id=c.evaluation_id AND r.case_id=c.case_id
                        AND r.variant=c.variant
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
            if row is None or row["status"] != "committed" or row["result_hash"] is None:
                raise CompanionStateError("evaluation_report_incomplete")
            result_items.append(
                {
                    "case_id": case_id,
                    "variant": variant,
                    "result_hash": str(row["result_hash"]),
                }
            )
        return candidate_facts, result_items

    def _write_evaluation_report_rows(
        self,
        candidate_facts,
        *,
        db,
        evaluation_id: str,
        failed_replan: Mapping[str, Any] | None,
        now,
        owner: OwnerRef,
        reason_code: str,
        verdict: str,
    ) -> None:
        db.execute(
            """UPDATE evaluation_runs SET status=?,reason_code=?,lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?""",
            (
                verdict,
                reason_code,
                now,
                owner.profile_id,
                owner.profile_generation,
                evaluation_id,
            ),
        )
        if verdict == "passed":
            next_candidate_status = (
                "eligible"
                if candidate_facts["risk"] == "low"
                else "awaiting_activation_confirmation"
            )
            db.execute(
                """UPDATE candidate_artifacts
                       SET status=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND candidate_id=? AND status='evaluating'""",
                (
                    next_candidate_status,
                    (
                        "evaluation_passed_low_risk"
                        if next_candidate_status == "eligible"
                        else "evaluation_passed_activation_confirmation_required"
                    ),
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_facts["candidate_id"],
                ),
            )
        elif verdict == "failed" and failed_replan is not None:
            replan = dict(failed_replan)
            if str(replan.get("candidate_id") or "") != str(candidate_facts["candidate_id"]):
                raise CompanionConflictError("evaluation_replan_candidate_mismatch")
            exhausted = bool(replan.get("exhausted", False))
            invalidation_reason = (
                "evaluation_failed_replan_exhausted" if exhausted else "evaluation_failed_replan"
            )
            candidate_status = str(candidate_facts["candidate_status"])
            candidate_reason = str(candidate_facts["candidate_reason_code"] or "")
            exact_replay = (
                candidate_status == "invalidated" and candidate_reason == invalidation_reason
            )
            applied = False
            if candidate_status == "evaluating":
                cursor = db.execute(
                    """UPDATE candidate_artifacts
                           SET status='invalidated',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=?
                             AND candidate_id=? AND status='evaluating'""",
                    (
                        invalidation_reason,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        candidate_facts["candidate_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise CompanionConflictError("candidate_evaluation_replan_transition_lost")
                applied = True
                if str(candidate_facts["candidate_mode"]) == "genesis":
                    db.execute(
                        """UPDATE growth_target_reservations
                               SET candidate_id=NULL,status='released',
                                   reservation_version=reservation_version+1,
                                   reason_code=?,updated_at=?
                               WHERE profile_id=? AND profile_generation=?
                                 AND target_id=? AND candidate_id=?
                                 AND status='held'""",
                        (
                            invalidation_reason,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            candidate_facts["target_id"],
                            candidate_facts["candidate_id"],
                        ),
                    )
            if (
                not exhausted
                and (applied or exact_replay)
                and replan.get("event") is not None
                and replan.get("job") is not None
            ):
                event_payload = _clone_companion_json_object(
                    replan["event"],
                    "evaluation_replan_event_invalid",
                )
                growth_event = GrowthEvent(
                    owner=owner,
                    event_id=str(event_payload["event_id"]),
                    source_kind=str(event_payload["source_kind"]),
                    source_ref=str(event_payload["source_ref"]),
                    context_key=str(event_payload["context_key"]),
                    root_run_id=str(event_payload["root_run_id"]),
                    reason_code=str(event_payload["reason_code"]),
                    payload=_clone_companion_json_object(
                        event_payload["payload"],
                        "evaluation_replan_event_payload_invalid",
                    ),
                )
                self._record_growth_event_db(
                    db,
                    growth_event,
                    now=now,
                )
                job = _clone_companion_json_object(
                    replan["job"],
                    "evaluation_replan_job_invalid",
                )
                self._enqueue_job_db(
                    db,
                    owner,
                    job_id=str(job["job_id"]),
                    kind=str(job["kind"]),
                    dedupe_key=str(job["dedupe_key"]),
                    payload=_clone_companion_json_object(
                        job["payload"],
                        "evaluation_replan_job_payload_invalid",
                    ),
                    reason_code=str(job["reason_code"]),
                    budget_reserved_tokens=int(job["budget_reserved_tokens"]),
                    budget_reserved_ms=int(job["budget_reserved_ms"]),
                    now=now,
                )

    def get_evaluation_postprocess_recovery(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
    ) -> Mapping[str, Any]:
        """Return frozen report/results before accepting another model output."""

        with self.read() as db:
            self._require_owner(db, owner)
            report = db.execute(
                """SELECT * FROM evaluation_reports
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchone()
            rows = db.execute(
                """SELECT case_id,variant,assertions_json,judge_result_json,
                          usage_json,result_hash,reason_code
                   FROM evaluation_results
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=?
                   ORDER BY case_id,variant""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchall()
        return {
            "report": None if report is None else dict(report),
            "results": tuple(
                {
                    "case_id": str(row["case_id"]),
                    "variant": str(row["variant"]),
                    "assertions": json.loads(str(row["assertions_json"])),
                    "judge_result": json.loads(str(row["judge_result_json"])),
                    "usage": json.loads(str(row["usage_json"])),
                    "result_hash": str(row["result_hash"]),
                    "reason_code": str(row["reason_code"]),
                }
                for row in rows
            ),
        }
