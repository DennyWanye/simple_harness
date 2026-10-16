"""Evaluation experiments, authorizations, risk assessments and execution permits."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import (
    CompanionConflictError,
    CompanionStateError,
    EvaluationExecutionPermit,
    OwnerRef,
)
from .core import CompanionStoreCore
from .support import _bytes_hash, canonical_hash, canonical_json

SAFE_STATIC_RUNNER_POLICY_HASH = canonical_hash(
    {
        "schema": "background-pairwise-runner-v1",
        "zero_tools": True,
        "blind_labels": True,
    }
)


class EvaluationAdmissionMixin(CompanionStoreCore):
    """Evaluation experiments, authorizations, risk assessments and execution permits."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Evaluation permits, physical launch claims, results, and reports

    def admit_evaluation_experiment(
        self,
        owner: OwnerRef,
        *,
        evaluation: Mapping[str, Any],
        case_inputs: Sequence[Mapping[str, Any]],
        cases: Sequence[Mapping[str, Any]],
        reason_code: str = "evaluation_experiment_admitted",
    ) -> Mapping[str, Any]:
        """Freeze one evaluation run, its inputs, and all variants atomically.

        This is the only public production admission API for the evaluation
        tables.  It verifies the immutable candidate package fence, derives
        input/fixture hashes from bytes, requires old/candidate variants to
        share the same input, and moves the candidate to ``evaluating`` in the
        same transaction.
        """

        required_run = {
            "evaluation_id",
            "candidate_id",
            "candidate_mode",
            "candidate_package_hash",
            "candidate_manifest_hash",
            "candidate_archive_hash",
            "suite_hash",
            "attempt_key",
            "baseline_kind",
            "old_snapshot_hash",
            "candidate_snapshot_hash",
            "runner_id",
            "runner_policy_hash",
            "provider_id",
            "model_id",
        }
        missing_run = required_run - set(evaluation)
        if missing_run:
            raise ValueError("evaluation_admission_fields_missing:" + ",".join(sorted(missing_run)))
        candidate_mode = str(evaluation["candidate_mode"])
        baseline_kind = str(evaluation["baseline_kind"])
        if candidate_mode not in {"genesis", "update", "builtin_override"}:
            raise ValueError("evaluation_candidate_mode_invalid")
        if baseline_kind not in {"source_pack", "capability_absent_v1"}:
            raise ValueError("evaluation_baseline_kind_invalid")
        if candidate_mode == "genesis" and (
            baseline_kind != "capability_absent_v1"
            or not evaluation.get("absent_baseline_ref")
            or not evaluation.get("absent_baseline_hash")
        ):
            raise ValueError("evaluation_absent_baseline_required")
        if candidate_mode != "genesis" and (
            baseline_kind != "source_pack"
            or not evaluation.get("source_owner_key")
            or not evaluation.get("source_scope")
            or not evaluation.get("source_scope_key")
            or not evaluation.get("source_pack_id")
            or not evaluation.get("source_version")
            or not evaluation.get("source_manifest_hash")
            or int(evaluation.get("source_binding_generation") or 0) < 1
        ):
            raise ValueError("evaluation_source_baseline_required")
        if not case_inputs or not cases:
            raise ValueError("evaluation_cases_required")

        def frozen_blob(value: Any, name: str) -> bytes:
            if isinstance(value, bytes):
                return value
            if isinstance(value, (Mapping, list, tuple)):
                return canonical_json(value).encode("utf-8")
            raise TypeError(f"{name}_must_be_bytes_or_json")

        normalized_inputs: list[dict[str, Any]] = []
        seen_input_ids: set[str] = set()
        seen_case_ids: set[str] = set()
        for raw in case_inputs:
            required_input = {
                "input_id",
                "case_id",
                "source_kind",
                "input_envelope",
                "adapter_id",
                "adapter_version",
                "adapter_build_fingerprint",
                "assertion_ref",
                "assertion_hash",
                "read_tool_fixture",
                "evaluation_tool_adapter_map",
            }
            missing = required_input - set(raw)
            if missing:
                raise ValueError("evaluation_input_fields_missing:" + ",".join(sorted(missing)))
            input_id = str(raw["input_id"]).strip()
            case_id = str(raw["case_id"]).strip()
            source_kind = str(raw["source_kind"])
            if (
                not input_id
                or not case_id
                or input_id in seen_input_ids
                or case_id in seen_case_ids
            ):
                raise ValueError("evaluation_input_identity_invalid")
            if source_kind not in {"packaged_suite", "historical_replay"}:
                raise ValueError("evaluation_input_source_kind_invalid")
            seen_input_ids.add(input_id)
            seen_case_ids.add(case_id)
            envelope_blob = frozen_blob(raw["input_envelope"], "evaluation_input_envelope")
            fixture_blob = frozen_blob(
                raw["read_tool_fixture"],
                "evaluation_read_tool_fixture",
            )
            adapter_map_json = canonical_json(dict(raw["evaluation_tool_adapter_map"]))
            source_event_refs = tuple(
                sorted({str(item).strip() for item in raw.get("source_event_refs", ())})
            )
            if any(not item for item in source_event_refs):
                raise ValueError("evaluation_source_event_ref_invalid")
            frozen_facts = {
                "schema": "evaluation-case-input-v1",
                "input_id": input_id,
                "case_id": case_id,
                "source_kind": source_kind,
                "resource_ref": raw.get("resource_ref"),
                "resource_hash": raw.get("resource_hash"),
                "source_event_refs": list(source_event_refs),
                "input_envelope_hash": _bytes_hash(envelope_blob),
                "adapter_id": str(raw["adapter_id"]),
                "adapter_version": str(raw["adapter_version"]),
                "adapter_build_fingerprint": str(raw["adapter_build_fingerprint"]),
                "assertion_ref": str(raw["assertion_ref"]),
                "assertion_hash": str(raw["assertion_hash"]),
                "read_tool_fixture_root_hash": _bytes_hash(fixture_blob),
                "evaluation_tool_adapter_map_hash": canonical_hash(json.loads(adapter_map_json)),
            }
            if any(
                not str(frozen_facts[name]).strip()
                for name in (
                    "adapter_id",
                    "adapter_version",
                    "adapter_build_fingerprint",
                    "assertion_ref",
                    "assertion_hash",
                )
            ):
                raise ValueError("evaluation_input_fence_required")
            normalized_inputs.append(
                {
                    **frozen_facts,
                    "source_event_refs_json": (
                        None if not source_event_refs else canonical_json(source_event_refs)
                    ),
                    "input_envelope_blob": envelope_blob,
                    "input_hash": canonical_hash(frozen_facts),
                    "read_tool_fixture_blob": fixture_blob,
                    "evaluation_tool_adapter_map_json": adapter_map_json,
                }
            )

        input_by_case = {str(item["case_id"]): item for item in normalized_inputs}
        normalized_cases: list[dict[str, str]] = []
        variants_by_case: dict[str, set[str]] = {}
        for raw in cases:
            required_case = {
                "case_id",
                "variant",
                "input_id",
                "manifest_case_version",
                "blind_label",
                "expected_kind",
            }
            missing = required_case - set(raw)
            if missing:
                raise ValueError("evaluation_case_fields_missing:" + ",".join(sorted(missing)))
            case_id = str(raw["case_id"]).strip()
            variant = str(raw["variant"])
            input_id = str(raw["input_id"]).strip()
            input_row = input_by_case.get(case_id)
            if (
                input_row is None
                or input_id != input_row["input_id"]
                or variant not in {"old", "candidate"}
            ):
                raise ValueError("evaluation_case_input_fence_invalid")
            seen_variants = variants_by_case.setdefault(case_id, set())
            if variant in seen_variants:
                raise ValueError("evaluation_case_variant_duplicate")
            seen_variants.add(variant)
            normalized_cases.append(
                {
                    "case_id": case_id,
                    "variant": variant,
                    "input_id": input_id,
                    "input_hash": str(input_row["input_hash"]),
                    "manifest_case_version": str(raw["manifest_case_version"]),
                    "blind_label": str(raw["blind_label"]),
                    "expected_kind": str(raw["expected_kind"]),
                }
            )
        if (
            any(variants != {"old", "candidate"} for variants in variants_by_case.values())
            or set(variants_by_case) != seen_case_ids
        ):
            raise ValueError("evaluation_case_variants_incomplete")

        evaluation_id = str(evaluation["evaluation_id"]).strip()
        candidate_id = str(evaluation["candidate_id"]).strip()
        if not evaluation_id or not candidate_id:
            raise ValueError("evaluation_identity_required")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            candidate = db.execute(
                """SELECT a.*,p.candidate_package_hash,
                          p.candidate_manifest_hash,p.archive_hash
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
                    candidate_id,
                ),
            ).fetchone()
            if candidate is None:
                raise CompanionStateError("evaluation_candidate_missing")
            if (
                str(candidate["candidate_mode"]) != candidate_mode
                or str(candidate["candidate_package_hash"])
                != str(evaluation["candidate_package_hash"])
                or str(candidate["candidate_manifest_hash"])
                != str(evaluation["candidate_manifest_hash"])
                or str(candidate["archive_hash"]) != str(evaluation["candidate_archive_hash"])
            ):
                raise CompanionConflictError("evaluation_candidate_package_fence_mismatch")
            existing = db.execute(
                """SELECT * FROM evaluation_runs
                   WHERE profile_id=? AND profile_generation=?
                     AND (evaluation_id=? OR
                          (candidate_id=? AND suite_hash=? AND attempt_key=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    candidate_id,
                    str(evaluation["suite_hash"]),
                    str(evaluation["attempt_key"]),
                ),
            ).fetchone()
            expected_run = {
                "evaluation_id": evaluation_id,
                "candidate_id": candidate_id,
                "candidate_mode": candidate_mode,
                "suite_hash": str(evaluation["suite_hash"]),
                "attempt_key": str(evaluation["attempt_key"]),
                "baseline_kind": baseline_kind,
                "old_snapshot_hash": str(evaluation["old_snapshot_hash"]),
                "candidate_snapshot_hash": str(evaluation["candidate_snapshot_hash"]),
                "source_owner_key": evaluation.get("source_owner_key"),
                "source_scope": evaluation.get("source_scope"),
                "source_scope_key": evaluation.get("source_scope_key"),
                "source_pack_id": evaluation.get("source_pack_id"),
                "source_version": evaluation.get("source_version"),
                "source_manifest_hash": evaluation.get("source_manifest_hash"),
                "source_binding_generation": evaluation.get("source_binding_generation"),
                "absent_baseline_ref": evaluation.get("absent_baseline_ref"),
                "absent_baseline_hash": evaluation.get("absent_baseline_hash"),
                "runner_id": str(evaluation["runner_id"]),
                "runner_policy_hash": str(evaluation["runner_policy_hash"]),
                "provider_id": str(evaluation["provider_id"]),
                "model_id": str(evaluation["model_id"]),
            }
            if existing is not None:
                if not self._same(existing, expected_run):
                    raise CompanionConflictError("evaluation_experiment_conflict")
                stored_inputs = db.execute(
                    """SELECT * FROM evaluation_case_inputs
                       WHERE profile_id=? AND profile_generation=?
                         AND evaluation_id=? ORDER BY input_id""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                    ),
                ).fetchall()
                stored_cases = db.execute(
                    """SELECT * FROM evaluation_cases
                       WHERE profile_id=? AND profile_generation=?
                         AND evaluation_id=? ORDER BY case_id,variant""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                    ),
                ).fetchall()
                expected_inputs = {
                    str(item["input_id"]): {
                        "case_id": item["case_id"],
                        "source_kind": item["source_kind"],
                        "resource_ref": item["resource_ref"],
                        "resource_hash": item["resource_hash"],
                        "source_event_refs_json": item["source_event_refs_json"],
                        "input_envelope_blob": item["input_envelope_blob"],
                        "input_hash": item["input_hash"],
                        "adapter_id": item["adapter_id"],
                        "adapter_version": item["adapter_version"],
                        "adapter_build_fingerprint": item["adapter_build_fingerprint"],
                        "assertion_ref": item["assertion_ref"],
                        "assertion_hash": item["assertion_hash"],
                        "read_tool_fixture_blob": item["read_tool_fixture_blob"],
                        "read_tool_fixture_root_hash": item["read_tool_fixture_root_hash"],
                        "evaluation_tool_adapter_map_json": item[
                            "evaluation_tool_adapter_map_json"
                        ],
                    }
                    for item in normalized_inputs
                }
                expected_cases = {
                    (str(item["case_id"]), str(item["variant"])): {
                        "input_id": item["input_id"],
                        "input_hash": item["input_hash"],
                        "manifest_case_version": item["manifest_case_version"],
                        "blind_label": item["blind_label"],
                        "expected_kind": item["expected_kind"],
                    }
                    for item in normalized_cases
                }
                if (
                    len(stored_inputs) != len(expected_inputs)
                    or any(
                        str(row["input_id"]) not in expected_inputs
                        or not self._same(row, expected_inputs[str(row["input_id"])])
                        for row in stored_inputs
                    )
                    or len(stored_cases) != len(expected_cases)
                    or any(
                        (
                            str(row["case_id"]),
                            str(row["variant"]),
                        )
                        not in expected_cases
                        or not self._same(
                            row,
                            expected_cases[
                                (
                                    str(row["case_id"]),
                                    str(row["variant"]),
                                )
                            ],
                        )
                        for row in stored_cases
                    )
                ):
                    raise CompanionConflictError("evaluation_experiment_case_conflict")
                return {
                    "evaluation": dict(existing),
                    "input_count": len(stored_inputs),
                    "case_count": len(stored_cases),
                }
            if str(candidate["status"]) != "proposed":
                raise CompanionStateError("evaluation_candidate_not_proposed")
            db.execute(
                """INSERT INTO evaluation_runs(
                     profile_id,profile_generation,evaluation_id,candidate_id,
                     candidate_mode,suite_hash,attempt_key,baseline_kind,
                     old_snapshot_hash,candidate_snapshot_hash,
                     source_owner_key,source_scope,source_scope_key,
                     source_pack_id,source_version,source_manifest_hash,
                     source_binding_generation,absent_baseline_ref,
                     absent_baseline_hash,runner_id,runner_policy_hash,
                     provider_id,model_id,status,claim_epoch,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                             'queued',0,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    *expected_run.values(),
                    reason_code,
                    now,
                    now,
                ),
            )
            for item in normalized_inputs:
                db.execute(
                    """INSERT INTO evaluation_case_inputs(
                         profile_id,profile_generation,evaluation_id,input_id,
                         case_id,source_kind,resource_ref,resource_hash,
                         source_event_refs_json,input_envelope_blob,input_hash,
                         adapter_id,adapter_version,adapter_build_fingerprint,
                         assertion_ref,assertion_hash,read_tool_fixture_blob,
                         read_tool_fixture_root_hash,
                         evaluation_tool_adapter_map_json,content_state,
                         reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                 'live','evaluation_input_frozen',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        item["input_id"],
                        item["case_id"],
                        item["source_kind"],
                        item["resource_ref"],
                        item["resource_hash"],
                        item["source_event_refs_json"],
                        item["input_envelope_blob"],
                        item["input_hash"],
                        item["adapter_id"],
                        item["adapter_version"],
                        item["adapter_build_fingerprint"],
                        item["assertion_ref"],
                        item["assertion_hash"],
                        item["read_tool_fixture_blob"],
                        item["read_tool_fixture_root_hash"],
                        item["evaluation_tool_adapter_map_json"],
                        now,
                        now,
                    ),
                )
            for item in normalized_cases:
                db.execute(
                    """INSERT INTO evaluation_cases(
                         profile_id,profile_generation,evaluation_id,case_id,
                         variant,input_id,input_hash,manifest_case_version,
                         blind_label,expected_kind,status,claim_epoch,attempt,
                         recovery_only,reason_code,schema_version,
                         created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,'queued',0,0,0,
                                 'evaluation_case_queued',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        item["case_id"],
                        item["variant"],
                        item["input_id"],
                        item["input_hash"],
                        item["manifest_case_version"],
                        item["blind_label"],
                        item["expected_kind"],
                        now,
                        now,
                    ),
                )
            updated = db.execute(
                """UPDATE candidate_artifacts
                   SET status='evaluating',
                       reason_code='evaluation_experiment_admitted',
                       updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=? AND status='proposed'""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionConflictError("evaluation_candidate_admission_lost")
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "evaluation_experiment",
                        evaluation_id,
                        candidate_id,
                        str(evaluation["suite_hash"]),
                        sorted(item["input_hash"] for item in normalized_inputs),
                    ]
                ),
                now=now,
            )
            row = db.execute(
                """SELECT * FROM evaluation_runs
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchone()
            assert row is not None
            return {
                "evaluation": dict(row),
                "input_count": len(normalized_inputs),
                "case_count": len(normalized_cases),
            }

    def create_evaluation_authorization(
        self,
        owner: OwnerRef,
        *,
        authorization_id: str,
        nonce: str,
        evaluation_id: str,
        candidate_id: str,
        package_hash: str,
        suite_hash: str,
        runner_policy_hash: str,
        expires_at: str,
        actor: str,
        risk_ack: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Freeze a one-shot user authorization for evaluation only."""

        if actor != "user" or risk_ack != "no_os_sandbox":
            raise CompanionStateError("evaluation_authorization_acknowledgement_invalid")
        now = self._now()
        expected = {
            "nonce": nonce,
            "candidate_id": candidate_id,
            "package_hash": package_hash,
            "suite_hash": suite_hash,
            "runner_policy_hash": runner_policy_hash,
            "expires_at": expires_at,
            "actor": actor,
            "risk_ack": risk_ack,
            "reason_code": reason_code,
            "schema_version": 1,
        }
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_authorizations
                   WHERE profile_id=? AND profile_generation=?
                     AND (authorization_id=? OR nonce=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    authorization_id,
                    nonce,
                ),
            ).fetchone()
            if existing is not None:
                if existing["authorization_id"] != authorization_id or not self._same(
                    existing, expected
                ):
                    raise CompanionConflictError("evaluation_authorization_conflict")
                return dict(existing)
            evaluation = db.execute(
                """SELECT e.candidate_id,e.suite_hash,e.runner_policy_hash,
                          p.candidate_package_hash,a.status
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id
                    AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   WHERE e.profile_id=? AND e.profile_generation=?
                     AND e.evaluation_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchone()
            if (
                evaluation is None
                or evaluation["candidate_id"] != candidate_id
                or evaluation["candidate_package_hash"] != package_hash
                or evaluation["suite_hash"] != suite_hash
                or evaluation["runner_policy_hash"] != runner_policy_hash
                or evaluation["status"] in {"invalidated", "expired", "stale"}
                or expires_at <= now
            ):
                raise CompanionStateError("evaluation_authorization_fence_invalid")
            db.execute(
                """INSERT INTO evaluation_authorizations(
                     profile_id,profile_generation,authorization_id,nonce,
                     candidate_id,package_hash,suite_hash,runner_policy_hash,
                     expires_at,consumed_at,actor,risk_ack,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    authorization_id,
                    nonce,
                    candidate_id,
                    package_hash,
                    suite_hash,
                    runner_policy_hash,
                    expires_at,
                    actor,
                    risk_ack,
                    reason_code,
                    1,
                    now,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_authorizations
                       WHERE profile_id=? AND profile_generation=?
                         AND authorization_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        authorization_id,
                    ),
                ).fetchone()
            )

    def record_risk_assessment(
        self,
        owner: OwnerRef,
        *,
        risk_id: str,
        candidate_id: str,
        candidate_package_hash: str,
        static_preflight: Mapping[str, Any],
        effect_topology_diff: Mapping[str, Any],
        risk: str,
        risk_hash: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Freeze deterministic preflight/risk facts before permit issuance."""

        if risk not in {"low", "medium", "high", "unknown"}:
            raise ValueError("risk assessment level invalid")
        now = self._now()
        preflight_json = canonical_json(dict(static_preflight))
        diff_json = canonical_json(dict(effect_topology_diff))
        expected = {
            "candidate_id": candidate_id,
            "candidate_package_hash": candidate_package_hash,
            "static_preflight_json": preflight_json,
            "effect_topology_diff_json": diff_json,
            "risk": risk,
            "risk_hash": risk_hash,
            "reason_code": reason_code,
            "schema_version": 1,
        }
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM risk_assessments
                   WHERE profile_id=? AND profile_generation=?
                     AND (risk_id=? OR
                          (candidate_id=? AND candidate_package_hash=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    risk_id,
                    candidate_id,
                    candidate_package_hash,
                ),
            ).fetchone()
            if existing is not None:
                if existing["risk_id"] != risk_id or not self._same(existing, expected):
                    raise CompanionConflictError("risk_assessment_conflict")
                return dict(existing)
            candidate = db.execute(
                """SELECT a.status,p.candidate_package_hash,p.content_state
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
                    candidate_id,
                ),
            ).fetchone()
            if (
                candidate is None
                or candidate["candidate_package_hash"] != candidate_package_hash
                or candidate["content_state"] != "live"
                or candidate["status"] in {"invalidated", "expired", "stale"}
            ):
                raise CompanionStateError("risk_candidate_fence_invalid")
            db.execute(
                """INSERT INTO risk_assessments(
                     profile_id,profile_generation,risk_id,candidate_id,
                     candidate_package_hash,static_preflight_json,
                     effect_topology_diff_json,risk,risk_hash,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    risk_id,
                    candidate_id,
                    candidate_package_hash,
                    preflight_json,
                    diff_json,
                    risk,
                    risk_hash,
                    reason_code,
                    now,
                ),
            )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(["risk_assessment", risk_id, risk_hash]),
                now=now,
            )
            return dict(
                db.execute(
                    """SELECT * FROM risk_assessments
                       WHERE profile_id=? AND profile_generation=? AND risk_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        risk_id,
                    ),
                ).fetchone()
            )

    def issue_evaluation_execution_permit(
        self,
        owner: OwnerRef,
        permit: EvaluationExecutionPermit,
    ) -> Mapping[str, Any]:
        if permit.mode not in {
            "safe_auto",
            "safe_static",
            "user_authorized",
        }:
            raise ValueError("evaluation_permit_mode_invalid")
        if (permit.mode in {"safe_auto", "safe_static"}) != (permit.authorization_id is None):
            raise CompanionStateError("evaluation_permit_authorization_mode_mismatch")
        permit_facts = {
            "schema_version": permit.schema_version,
            "evaluation_id": permit.evaluation_id,
            "mode": permit.mode,
            "candidate_id": permit.candidate_id,
            "package_hash": permit.package_hash,
            "manifest_hash": permit.manifest_hash,
            "archive_hash": permit.archive_hash,
            "suite_hash": permit.suite_hash,
            "runner_policy_hash": permit.runner_policy_hash,
            "issued_revocation_epoch": permit.issued_revocation_epoch,
            "preflight_ref": permit.preflight_ref,
            "preflight_hash": permit.preflight_hash,
            "risk_ref": permit.risk_ref,
            "risk_hash": permit.risk_hash,
            "authorization_id": permit.authorization_id,
        }
        if canonical_hash(permit_facts) != permit.permit_hash:
            raise CompanionConflictError("evaluation_permit_hash_mismatch")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            evaluation = db.execute(
                """SELECT e.*,p.candidate_package_hash,p.candidate_manifest_hash,p.archive_hash
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   WHERE e.profile_id=? AND e.profile_generation=? AND e.evaluation_id=?""",
                (owner.profile_id, owner.profile_generation, permit.evaluation_id),
            ).fetchone()
            if evaluation is None:
                raise CompanionStateError("evaluation_missing")
            if evaluation["status"] not in {"queued", "running"}:
                raise CompanionStateError("evaluation_terminal")
            expected = {
                "evaluation_id": permit.evaluation_id,
                "mode": permit.mode,
                "candidate_id": permit.candidate_id,
                "package_hash": permit.package_hash,
                "manifest_hash": permit.manifest_hash,
                "archive_hash": permit.archive_hash,
                "suite_hash": permit.suite_hash,
                "runner_policy_hash": permit.runner_policy_hash,
                "issued_revocation_epoch": permit.issued_revocation_epoch,
                "preflight_ref": permit.preflight_ref,
                "preflight_hash": permit.preflight_hash,
                "risk_ref": permit.risk_ref,
                "risk_hash": permit.risk_hash,
                "authorization_id": permit.authorization_id,
                "permit_hash": permit.permit_hash,
                "schema_version": permit.schema_version,
            }
            existing = db.execute(
                """SELECT * FROM evaluation_execution_permits
                   WHERE profile_id=? AND profile_generation=?
                     AND (permit_id=? OR evaluation_id=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    permit.permit_id,
                    permit.evaluation_id,
                ),
            ).fetchone()
            if existing is not None:
                if existing["permit_id"] != permit.permit_id or not self._same(existing, expected):
                    raise CompanionConflictError("evaluation_permit_conflict")
                return dict(existing)
            if (
                evaluation["candidate_id"] != permit.candidate_id
                or evaluation["candidate_package_hash"] != permit.package_hash
                or evaluation["candidate_manifest_hash"] != permit.manifest_hash
                or evaluation["archive_hash"] != permit.archive_hash
                or evaluation["suite_hash"] != permit.suite_hash
                or evaluation["runner_policy_hash"] != permit.runner_policy_hash
            ):
                raise CompanionConflictError("evaluation_permit_fence_mismatch")
            risk = db.execute(
                """SELECT risk_id,risk_hash,risk,static_preflight_json
                   FROM risk_assessments
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                     AND risk_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    permit.candidate_id,
                    permit.risk_ref,
                ),
            ).fetchone()
            safe_static_valid = False
            if risk is not None and permit.mode == "safe_static":
                try:
                    preflight = json.loads(str(risk["static_preflight_json"]))
                except json.JSONDecodeError as exc:
                    raise CompanionStateError("evaluation_permit_risk_invalid") from exc
                safe_static_valid = (
                    isinstance(preflight, dict)
                    and preflight.get("candidate_kind") == "instruction"
                    and risk["risk"] != "low"
                    and permit.runner_policy_hash == SAFE_STATIC_RUNNER_POLICY_HASH
                )
            if (
                risk is None
                or risk["risk_hash"] != permit.risk_hash
                or (permit.mode == "safe_auto" and risk["risk"] != "low")
                or (permit.mode == "safe_static" and not safe_static_valid)
            ):
                raise CompanionStateError("evaluation_permit_risk_invalid")
            if permit.mode == "user_authorized":
                authorization = db.execute(
                    """SELECT * FROM evaluation_authorizations
                       WHERE profile_id=? AND profile_generation=? AND authorization_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        permit.authorization_id,
                    ),
                ).fetchone()
                if (
                    authorization is None
                    or authorization["candidate_id"] != permit.candidate_id
                    or authorization["package_hash"] != permit.package_hash
                    or authorization["suite_hash"] != permit.suite_hash
                    or authorization["runner_policy_hash"] != permit.runner_policy_hash
                    or authorization["consumed_at"] is not None
                    or authorization["expires_at"] <= now
                ):
                    raise CompanionStateError("evaluation_authorization_invalid")
                db.execute(
                    """UPDATE evaluation_authorizations SET consumed_at=?
                       WHERE profile_id=? AND profile_generation=? AND authorization_id=?
                         AND consumed_at IS NULL""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        permit.authorization_id,
                    ),
                )
            db.execute(
                """INSERT INTO evaluation_execution_permits(
                     profile_id,profile_generation,permit_id,evaluation_id,mode,candidate_id,
                     package_hash,manifest_hash,archive_hash,suite_hash,runner_policy_hash,
                     issued_revocation_epoch,preflight_ref,preflight_hash,risk_ref,risk_hash,
                     authorization_id,status,permit_hash,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'issued',?,?,?,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    permit.permit_id,
                    permit.evaluation_id,
                    permit.mode,
                    permit.candidate_id,
                    permit.package_hash,
                    permit.manifest_hash,
                    permit.archive_hash,
                    permit.suite_hash,
                    permit.runner_policy_hash,
                    permit.issued_revocation_epoch,
                    permit.preflight_ref,
                    permit.preflight_hash,
                    permit.risk_ref,
                    permit.risk_hash,
                    permit.authorization_id,
                    permit.permit_hash,
                    permit.reason_code,
                    permit.schema_version,
                    now,
                    now,
                ),
            )
            db.execute(
                """UPDATE evaluation_runs
                   SET execution_permit_ref=?,execution_permit_hash=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?""",
                (
                    permit.permit_id,
                    permit.permit_hash,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    permit.evaluation_id,
                ),
            )
            db.execute(
                """UPDATE candidate_artifacts
                   SET status='evaluating',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                     AND status IN ('proposed','awaiting_eval_authorization')""",
                (
                    (
                        "safe_auto_evaluation_permit_issued"
                        if permit.mode == "safe_auto"
                        else "safe_static_evaluation_permit_issued"
                        if permit.mode == "safe_static"
                        else "user_authorized_evaluation_permit_issued"
                    ),
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    permit.candidate_id,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_execution_permits
                       WHERE profile_id=? AND profile_generation=? AND permit_id=?""",
                    (owner.profile_id, owner.profile_generation, permit.permit_id),
                ).fetchone()
            )
