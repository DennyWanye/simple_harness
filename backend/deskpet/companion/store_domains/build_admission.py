"""Explicit-build and reflection-decision admission."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from ..build_admission import ExplicitGrowthBuildAdmissionV1
from ..contracts import (
    CompanionConflictError,
    CompanionOwnerError,
    CompanionStateError,
    GrowthEvent,
    OwnerRef,
)
from ..growth import GrowthProposalDecision, StructuredGrowthProposalV1, evidence_set_hash
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class BuildAdmissionMixin(CompanionStoreCore):
    """Explicit-build and reflection-decision admission."""

    __slots__ = ()

    def admit_explicit_build(self, admission: Any) -> Mapping[str, Any]:
        """Atomically persist trusted explicit evidence, proposal and build."""

        if not isinstance(admission, ExplicitGrowthBuildAdmissionV1):
            raise TypeError("explicit_growth_build_admission_required")
        owner = admission.owner
        expected_owner_key = f"companion:{owner.profile_id}:{owner.profile_generation}"
        if admission.owner_key != expected_owner_key:
            raise CompanionOwnerError("explicit_build_owner_key_mismatch")
        proposal = admission.proposal
        assert proposal.target is not None and proposal.target_fence is not None
        now = self._now()
        proposal_json = canonical_json(proposal.to_dict())
        source_fence_json = (
            None
            if proposal.source_fence is None
            else canonical_json(proposal.source_fence.to_dict())
        )
        target_fence_json = canonical_json(proposal.target_fence.to_dict())
        source_fence_hash = canonical_hash(
            None if proposal.source_fence is None else proposal.source_fence.to_dict()
        )
        target_fence_hash = canonical_hash(proposal.target_fence.to_dict())
        evidence_ids = tuple(sorted(set(proposal.evidence_event_ids)))
        if not evidence_ids:
            raise CompanionStateError("explicit_build_evidence_required")
        builder_launch_id = "builder-launch:" + canonical_hash(
            ["candidate-builder-launch-v1", admission.build_id]
        )
        child_run_id = "candidate-child:" + canonical_hash(
            ["candidate-builder-child-v1", admission.build_id]
        )
        expected_child_start_hash = canonical_hash(
            {
                "schema": "candidate-builder-start-v1",
                "build_id": admission.build_id,
                "proposal_ref": admission.proposal_ref,
                "proposal_hash": admission.proposal_hash,
                "evidence_set_hash": admission.evidence_set_hash,
                "builder_launch_id": builder_launch_id,
                "child_run_id": child_run_id,
            }
        )
        with self._write() as db:
            self._require_owner(db, owner)
            for event_id in evidence_ids:
                self._record_growth_event_db(
                    db,
                    GrowthEvent(
                        owner=owner,
                        event_id=event_id,
                        source_kind="explicit_user_build",
                        source_ref=f"{admission.source_ref}:{event_id}",
                        context_key=admission.user_message_ref,
                        root_run_id=admission.root_run_id,
                        reason_code="explicit_user_build_evidence",
                        payload={
                            "request_id": admission.request_id,
                            "user_message_ref": admission.user_message_ref,
                            "user_message_hash": admission.user_message_hash,
                            "source_hash": admission.source_hash,
                            "proposal_ref": admission.proposal_ref,
                        },
                    ),
                    now=now,
                )
            target = db.execute(
                """SELECT * FROM growth_targets
                   WHERE profile_id=? AND profile_generation=? AND target_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    proposal.target.target_id,
                ),
            ).fetchone()
            target_expected = {
                "kind": proposal.target.kind.value,
                "stable_name": proposal.target.stable_name,
                "pack_id": proposal.target.pack_id,
                "governance_domain": "companion_growth",
            }
            if target is None:
                db.execute(
                    """INSERT INTO growth_targets(
                    profile_id,profile_generation,target_id,kind,stable_name,
                    pack_id,governance_domain,reason_code,schema_version,
                    created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,'explicit_user_build',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        proposal.target.target_id,
                        proposal.target.kind.value,
                        proposal.target.stable_name,
                        proposal.target.pack_id,
                        "companion_growth",
                        now,
                        now,
                    ),
                )
            elif not self._same(target, target_expected):
                raise CompanionConflictError(f"growth_target_conflict:{proposal.target.target_id}")
            if proposal.candidate_mode.value == "genesis":
                reservation = db.execute(
                    """SELECT * FROM growth_target_reservations
                       WHERE profile_id=? AND profile_generation=?
                         AND kind=? AND stable_name=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        proposal.target.kind.value,
                        proposal.target.stable_name,
                    ),
                ).fetchone()
                if reservation is None:
                    db.execute(
                        """INSERT INTO growth_target_reservations(
                        profile_id,profile_generation,kind,stable_name,target_id,
                        pack_id,candidate_id,reservation_version,status,
                        reason_code,schema_version,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,NULL,1,'held',
                                 'explicit_user_build_reserved',1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            proposal.target.kind.value,
                            proposal.target.stable_name,
                            proposal.target.target_id,
                            proposal.target.pack_id,
                            now,
                            now,
                        ),
                    )
                else:
                    replay_build = db.execute(
                        """SELECT 1 FROM candidate_builds
                           WHERE profile_id=? AND profile_generation=?
                             AND (build_id=? OR
                               (source_kind='explicit_user_build' AND source_ref=?))
                           LIMIT 1""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            admission.build_id,
                            admission.source_ref,
                        ),
                    ).fetchone()
                    if (
                        str(reservation["target_id"]) != proposal.target.target_id
                        or str(reservation["pack_id"]) != proposal.target.pack_id
                        or (
                            replay_build is None
                            and (
                                str(reservation["status"]) != "held"
                                or reservation["candidate_id"] is not None
                            )
                        )
                    ):
                        raise CompanionConflictError(
                            f"growth_target_reserved:{proposal.target.target_id}"
                        )
            existing = db.execute(
                """SELECT * FROM candidate_builds
                   WHERE profile_id=? AND profile_generation=?
                     AND (build_id=? OR (source_kind='explicit_user_build'
                       AND source_ref=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    admission.build_id,
                    admission.source_ref,
                ),
            ).fetchone()
            immutable = {
                "build_id": admission.build_id,
                "source_kind": "explicit_user_build",
                "source_ref": admission.source_ref,
                "reflection_job_id": None,
                "proposal_ref": admission.proposal_ref,
                "proposal_json": proposal_json,
                "proposal_hash": admission.proposal_hash,
                "evidence_set_json": canonical_json(evidence_ids),
                "evidence_set_hash": admission.evidence_set_hash,
                "candidate_mode": proposal.candidate_mode.value,
                "source_fence_json": source_fence_json,
                "source_fence_hash": source_fence_hash,
                "target_fence_json": target_fence_json,
                "target_fence_hash": target_fence_hash,
                "builder_launch_id": builder_launch_id,
                "builder_child_run_id": child_run_id,
                "expected_child_start_hash": expected_child_start_hash,
            }
            if existing is not None:
                if not self._same(existing, immutable):
                    raise CompanionConflictError(f"candidate_build_conflict:{admission.build_id}")
                return dict(existing)
            db.execute(
                """INSERT INTO candidate_builds(
                profile_id,profile_generation,build_id,source_kind,source_ref,
                reflection_job_id,proposal_ref,proposal_json,proposal_hash,
                evidence_set_json,
                evidence_set_hash,candidate_mode,source_fence_json,
                source_fence_hash,target_fence_json,target_fence_hash,
                builder_launch_id,builder_child_run_id,
                expected_child_start_hash,status,reason_code,schema_version,
                created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                         'proposed','explicit_user_build_admitted',1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    *immutable.values(),
                    now,
                    now,
                ),
            )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "explicit_candidate_build",
                        admission.build_id,
                        admission.proposal_hash,
                    ]
                ),
                now=now,
            )
            row = db.execute(
                """SELECT * FROM candidate_builds
                   WHERE profile_id=? AND profile_generation=? AND build_id=?""",
                (owner.profile_id, owner.profile_generation, admission.build_id),
            ).fetchone()
            assert row is not None
            result = dict(row)
            result["proposal_json"] = proposal_json
            return result

    def admit_reflection_decision(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        decision_id: str,
        decision: str,
        reason_code: str,
        evidence_event_ids: Sequence[str],
        source_ref: str,
        proposal_ref: str | None = None,
        proposal: Any | None = None,
        build_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Persist one reflection outcome and its candidate admission atomically.

        The caller may supply model-produced proposal fields, but this method
        rehydrates the closed proposal type and derives all host identities,
        hashes, owner fences, reservation rows, and child launch identities.
        Non-candidate outcomes never create a build.
        """

        allowed = {"candidate", "abstain", "insufficient", "stale", "noop"}
        if decision not in allowed:
            raise ValueError("reflection_decision_invalid")
        job_id = str(job_id).strip()
        decision_id = str(decision_id).strip()
        source_ref = str(source_ref).strip()
        if not job_id or not decision_id or not source_ref or not reason_code:
            raise ValueError("reflection_decision_identity_required")
        evidence_ids = tuple(sorted({str(item).strip() for item in evidence_event_ids}))
        if any(not item for item in evidence_ids):
            raise ValueError("reflection_evidence_id_invalid")
        evidence_hash = evidence_set_hash(evidence_ids)

        typed_proposal = self._typed_reflection_proposal(
            decision,
            evidence_hash=evidence_hash,
            evidence_ids=evidence_ids,
            proposal=proposal,
            proposal_ref=proposal_ref,
        )

        proposal_json = None if typed_proposal is None else canonical_json(typed_proposal.to_dict())
        proposal_hash = None if typed_proposal is None else typed_proposal.proposal_hash
        normalized_build_id, normalized_proposal_ref = self._normalize_reflection_refs(
            build_id,
            decision=decision,
            evidence_hash=evidence_hash,
            job_id=job_id,
            owner=owner,
            proposal_hash=proposal_hash,
            proposal_ref=proposal_ref,
            typed_proposal=typed_proposal,
        )

        now = self._now()
        with self._write() as db:
            self._require_reflection_job(db, evidence_ids, job_id, owner)

            existing = db.execute(
                """SELECT * FROM reflection_decisions
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                   ORDER BY decision_id""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchall()
            expected_decision = {
                "job_id": job_id,
                "decision_id": decision_id,
                "decision": decision,
                "reason_code": reason_code,
                "evidence_set_hash": evidence_hash,
                "source_kind": "reflection",
                "source_ref": source_ref,
                "proposal_ref": normalized_proposal_ref,
                "proposal_json": proposal_json,
                "proposal_hash": proposal_hash,
                "schema_version": 1,
            }
            if existing:
                if len(existing) != 1 or not self._same(existing[0], expected_decision):
                    raise CompanionConflictError("reflection_decision_conflict")
                decision_row = dict(existing[0])
                build_row = None
                if normalized_build_id is not None:
                    build_row = self._find_candidate_build(db, normalized_build_id, owner=owner)
                return {
                    "decision": decision_row,
                    "candidate_build": (None if build_row is None else dict(build_row)),
                }

            db.execute(
                """INSERT INTO reflection_decisions(
                     profile_id,profile_generation,job_id,decision_id,
                     decision,reason_code,evidence_set_hash,source_kind,
                     source_ref,proposal_ref,proposal_json,proposal_hash,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    decision_id,
                    decision,
                    reason_code,
                    evidence_hash,
                    "reflection",
                    source_ref,
                    normalized_proposal_ref,
                    proposal_json,
                    proposal_hash,
                    now,
                ),
            )
            build_row: sqlite3.Row | None = None
            if decision == "candidate":
                self._admit_candidate_build(
                    db,
                    evidence_hash=evidence_hash,
                    evidence_ids=evidence_ids,
                    job_id=job_id,
                    normalized_build_id=normalized_build_id,
                    normalized_proposal_ref=normalized_proposal_ref,
                    now=now,
                    owner=owner,
                    proposal_hash=proposal_hash,
                    proposal_json=proposal_json,
                    source_ref=source_ref,
                    typed_proposal=typed_proposal,
                )
                build_row = self._find_candidate_build(db, normalized_build_id, owner=owner)
            return self._bump_reflection_detail(
                build_row,
                db=db,
                decision=decision,
                decision_id=decision_id,
                job_id=job_id,
                now=now,
                owner=owner,
                proposal_hash=proposal_hash,
            )

    def _admit_candidate_build(
        self,
        db,
        *,
        evidence_hash,
        evidence_ids,
        job_id: str,
        normalized_build_id,
        normalized_proposal_ref,
        now,
        owner: OwnerRef,
        proposal_hash,
        proposal_json,
        source_ref: str,
        typed_proposal,
    ) -> None:
        assert typed_proposal is not None
        assert typed_proposal.target is not None
        assert typed_proposal.target_fence is not None
        assert normalized_build_id is not None
        assert normalized_proposal_ref is not None
        self._require_proposal_target_state(db, now, owner, typed_proposal)

        source_fence_json = (
            None
            if typed_proposal.source_fence is None
            else canonical_json(typed_proposal.source_fence.to_dict())
        )
        target_fence_json = canonical_json(typed_proposal.target_fence.to_dict())
        builder_launch_id = "builder-launch:" + canonical_hash(
            ["candidate-builder-launch-v1", normalized_build_id]
        )
        child_run_id = "candidate-child:" + canonical_hash(
            ["candidate-builder-child-v1", normalized_build_id]
        )
        expected_child_start_hash = canonical_hash(
            {
                "schema": "candidate-builder-start-v1",
                "build_id": normalized_build_id,
                "proposal_ref": normalized_proposal_ref,
                "proposal_hash": proposal_hash,
                "evidence_set_hash": evidence_hash,
                "builder_launch_id": builder_launch_id,
                "child_run_id": child_run_id,
            }
        )
        db.execute(
            """INSERT INTO candidate_builds(
                         profile_id,profile_generation,build_id,source_kind,
                         source_ref,reflection_job_id,proposal_ref,
                         proposal_json,proposal_hash,evidence_set_json,
                         evidence_set_hash,candidate_mode,source_fence_json,
                         source_fence_hash,target_fence_json,target_fence_hash,
                         builder_launch_id,builder_child_run_id,
                         expected_child_start_hash,status,reason_code,
                         schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                 'proposed','reflection_candidate_admitted',
                                 1,?,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                normalized_build_id,
                "reflection",
                source_ref,
                job_id,
                normalized_proposal_ref,
                proposal_json,
                proposal_hash,
                canonical_json(evidence_ids),
                evidence_hash,
                typed_proposal.candidate_mode.value,
                source_fence_json,
                canonical_hash(
                    None
                    if typed_proposal.source_fence is None
                    else typed_proposal.source_fence.to_dict()
                ),
                target_fence_json,
                canonical_hash(typed_proposal.target_fence.to_dict()),
                builder_launch_id,
                child_run_id,
                expected_child_start_hash,
                now,
                now,
            ),
        )

    def _typed_reflection_proposal(
        self,
        decision: str,
        *,
        evidence_hash,
        evidence_ids,
        proposal: Any | None,
        proposal_ref: str | None,
    ) -> StructuredGrowthProposalV1 | None:
        typed_proposal: StructuredGrowthProposalV1 | None
        if proposal is None:
            typed_proposal = None
        elif isinstance(proposal, StructuredGrowthProposalV1):
            typed_proposal = proposal
        elif isinstance(proposal, Mapping):
            typed_proposal = StructuredGrowthProposalV1.from_mapping(proposal)
        else:
            raise TypeError("structured_growth_proposal_required")
        if decision == "candidate":
            if (
                typed_proposal is None
                or typed_proposal.decision is not GrowthProposalDecision.CANDIDATE
            ):
                raise CompanionStateError("reflection_candidate_proposal_required")
            if not proposal_ref:
                raise ValueError("reflection_proposal_ref_required")
        elif (
            typed_proposal is not None
            and typed_proposal.decision is not GrowthProposalDecision.ABSTAIN
        ):
            raise CompanionStateError("reflection_non_candidate_proposal_invalid")
        if typed_proposal is not None and (
            typed_proposal.evidence_event_ids != evidence_ids
            or typed_proposal.evidence_set_hash != evidence_hash
        ):
            raise CompanionConflictError("reflection_proposal_evidence_mismatch")
        return typed_proposal

    def _normalize_reflection_refs(
        self,
        build_id: str | None,
        *,
        decision: str,
        evidence_hash,
        job_id: str,
        owner: OwnerRef,
        proposal_hash,
        proposal_ref: str | None,
        typed_proposal: StructuredGrowthProposalV1 | None,
    ):
        normalized_proposal_ref = None if proposal_ref is None else str(proposal_ref).strip()
        if proposal_ref is not None and not normalized_proposal_ref:
            raise ValueError("reflection_proposal_ref_invalid")
        if decision == "candidate":
            assert typed_proposal is not None
            assert normalized_proposal_ref is not None
            expected_build_id = "candidate-build:" + canonical_hash(
                {
                    "schema": "reflection-candidate-build-v1",
                    "owner": {
                        "profile_id": owner.profile_id,
                        "profile_generation": owner.profile_generation,
                    },
                    "job_id": job_id,
                    "proposal_ref": normalized_proposal_ref,
                    "proposal_hash": proposal_hash,
                    "evidence_set_hash": evidence_hash,
                }
            )
            if build_id is not None and str(build_id) != expected_build_id:
                raise CompanionConflictError("reflection_build_id_not_host_derived")
            normalized_build_id = expected_build_id
        else:
            if build_id is not None:
                raise CompanionStateError("reflection_non_candidate_cannot_build")
            normalized_build_id = None
        return normalized_build_id, normalized_proposal_ref

    def _require_reflection_job(self, db, evidence_ids, job_id: str, owner: OwnerRef) -> None:
        self._require_owner(db, owner)
        job = db.execute(
            """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
            (owner.profile_id, owner.profile_generation, job_id),
        ).fetchone()
        if (
            job is None
            or str(job["kind"]) != "reflection"
            or str(job["status"]) not in {"leased", "succeeded"}
        ):
            raise CompanionStateError("reflection_job_not_admissible")
        if evidence_ids:
            placeholders = ",".join("?" for _ in evidence_ids)
            live = db.execute(
                f"""SELECT event_id FROM growth_events
                        WHERE profile_id=? AND profile_generation=?
                          AND content_state='live'
                          AND event_id IN ({placeholders})""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    *evidence_ids,
                ),
            ).fetchall()
            if {str(row["event_id"]) for row in live} != set(evidence_ids):
                raise CompanionStateError("reflection_evidence_missing_or_tombstoned")

    def _require_proposal_target_state(
        self, db, now, owner: OwnerRef, typed_proposal: StructuredGrowthProposalV1 | None
    ) -> None:
        target = typed_proposal.target
        target_expected = {
            "kind": target.kind.value,
            "stable_name": target.stable_name,
            "pack_id": target.pack_id,
            "governance_domain": "companion_growth",
        }
        target_row = db.execute(
            """SELECT * FROM growth_targets
                       WHERE profile_id=? AND profile_generation=?
                         AND target_id=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                target.target_id,
            ),
        ).fetchone()
        if target_row is None:
            db.execute(
                """INSERT INTO growth_targets(
                             profile_id,profile_generation,target_id,kind,
                             stable_name,pack_id,governance_domain,reason_code,
                             schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,?,?,'reflection_candidate',1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    target.target_id,
                    target.kind.value,
                    target.stable_name,
                    target.pack_id,
                    "companion_growth",
                    now,
                    now,
                ),
            )
        elif not self._same(target_row, target_expected):
            raise CompanionConflictError(f"growth_target_conflict:{target.target_id}")
        if typed_proposal.candidate_mode.value == "genesis":
            reservation = db.execute(
                """SELECT * FROM growth_target_reservations
                           WHERE profile_id=? AND profile_generation=?
                             AND kind=? AND stable_name=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    target.kind.value,
                    target.stable_name,
                ),
            ).fetchone()
            if reservation is None:
                db.execute(
                    """INSERT INTO growth_target_reservations(
                                 profile_id,profile_generation,kind,stable_name,
                                 target_id,pack_id,candidate_id,
                                 reservation_version,status,reason_code,
                                 schema_version,created_at,updated_at
                               ) VALUES (?,?,?,?,?,?,NULL,1,'held',
                                         'reflection_candidate_reserved',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        target.kind.value,
                        target.stable_name,
                        target.target_id,
                        target.pack_id,
                        now,
                        now,
                    ),
                )
            elif (
                str(reservation["target_id"]) == target.target_id
                and str(reservation["pack_id"]) == target.pack_id
                and str(reservation["status"]) == "released"
                and reservation["candidate_id"] is None
            ):
                cursor = db.execute(
                    """UPDATE growth_target_reservations
                               SET status='held',
                                   reason_code='reflection_candidate_replanned',
                                   updated_at=?
                               WHERE profile_id=? AND profile_generation=?
                                 AND kind=? AND stable_name=?
                                 AND target_id=? AND pack_id=?
                                 AND status='released'
                                 AND candidate_id IS NULL
                                 AND reservation_version=?""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        target.kind.value,
                        target.stable_name,
                        target.target_id,
                        target.pack_id,
                        int(reservation["reservation_version"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise CompanionConflictError(
                        f"growth_target_reservation_replan_conflict:{target.target_id}"
                    )
            elif (
                str(reservation["target_id"]) != target.target_id
                or str(reservation["pack_id"]) != target.pack_id
                or str(reservation["status"]) != "held"
                or reservation["candidate_id"] is not None
            ):
                raise CompanionConflictError(f"growth_target_reserved:{target.target_id}")

    def _bump_reflection_detail(
        self,
        build_row: sqlite3.Row | None,
        *,
        db,
        decision: str,
        decision_id: str,
        job_id: str,
        now,
        owner: OwnerRef,
        proposal_hash,
    ) -> Mapping[str, Any]:
        self._bump_detail(
            db,
            owner,
            mutation_hash=canonical_hash(
                [
                    "reflection_decision",
                    job_id,
                    decision_id,
                    decision,
                    proposal_hash,
                ]
            ),
            now=now,
        )
        decision_row = db.execute(
            """SELECT * FROM reflection_decisions
                   WHERE profile_id=? AND profile_generation=?
                     AND job_id=? AND decision_id=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                job_id,
                decision_id,
            ),
        ).fetchone()
        assert decision_row is not None
        return {
            "decision": dict(decision_row),
            "candidate_build": (None if build_row is None else dict(build_row)),
        }
