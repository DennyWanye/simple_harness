"""Candidate build claims and build lifecycle acknowledgements."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from ..build_admission import CandidateDraftReceiptExpectationV1, GrowthCandidateBuildPermitV1
from ..candidate_build_coordinator import CandidateBuildLaunchStateV1, CandidateBuildStatus
from ..contracts import (
    CompanionConflictError,
    CompanionLeaseError,
    CompanionOwnerError,
    CompanionStateError,
    OwnerRef,
)
from .core import CompanionStoreCore
from .support import canonical_json


class CandidateBuildMixin(CompanionStoreCore):
    """Candidate build claims and build lifecycle acknowledgements."""

    __slots__ = ()

    @staticmethod
    def _candidate_build_owner(row: sqlite3.Row) -> OwnerRef:
        return OwnerRef(str(row["profile_id"]), int(row["profile_generation"]))

    @staticmethod
    def _candidate_build_permit_matches(row: sqlite3.Row, permit: Any) -> bool:
        return (
            str(row["build_id"]) == permit.build_id
            and str(row["build_permit_ref"] or "") == permit.permit_id
            and str(row["build_permit_hash"] or "") == permit.permit_hash
            and int(row["claim_epoch"]) == permit.lease_epoch
            and str(row["proposal_ref"]) == permit.proposal_ref
            and str(row["proposal_hash"]) == permit.proposal_hash
            and str(row["evidence_set_hash"]) == permit.evidence_set_hash
            and str(row["source_fence_hash"]) == permit.source_fence_hash
            and str(row["target_fence_hash"]) == permit.target_fence_hash
        )

    @staticmethod
    def _candidate_build_state(row: sqlite3.Row) -> Any:

        expectation = None
        if row["draft_receipt_expectation_json"] is not None:
            raw = json.loads(str(row["draft_receipt_expectation_json"]))
            expectation = CandidateDraftReceiptExpectationV1(
                **{
                    name: str(raw[name])
                    for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
                }
            )
        return CandidateBuildLaunchStateV1(
            build_id=str(row["build_id"]),
            status=str(row["status"]),
            permit_id=str(row["build_permit_ref"]),
            permit_hash=str(row["build_permit_hash"]),
            lease_epoch=int(row["claim_epoch"]),
            builder_launch_id=str(row["builder_launch_id"]),
            child_run_id=str(row["builder_child_run_id"]),
            expected_child_start_hash=str(row["expected_child_start_hash"]),
            task_workspace=str(row["task_workspace"] or ""),
            draft_receipt_expectation=expectation,
            candidate_ref=(str(row["candidate_ref"]) if row["candidate_ref"] is not None else None),
            candidate_hash=(
                str(row["candidate_hash"]) if row["candidate_hash"] is not None else None
            ),
            last_error=(str(row["last_error"]) if row["last_error"] is not None else None),
        )

    def _find_candidate_build(
        self,
        db: sqlite3.Connection,
        build_id: str,
        *,
        owner: OwnerRef | None = None,
    ) -> sqlite3.Row:
        if owner is None:
            rows = db.execute(
                "SELECT * FROM candidate_builds WHERE build_id=?",
                (str(build_id).strip(),),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT * FROM candidate_builds
                   WHERE profile_id=? AND profile_generation=? AND build_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(build_id).strip(),
                ),
            ).fetchall()
        if len(rows) != 1:
            raise CompanionStateError(
                "candidate_build_missing" if not rows else "candidate_build_identity_ambiguous"
            )
        return rows[0]

    def get_candidate_build_recovery(
        self,
        owner: OwnerRef,
        *,
        build_id: str,
    ) -> Mapping[str, str | None]:
        """Return the minimal exact owner-fenced build recovery view.

        This read seam lets the production pipeline distinguish a partially
        committed retry (the candidate is already built, but downstream
        evaluation admission did not finish) from a fresh build.  It remains
        fail-closed on missing or ambiguous identities and deliberately does
        not manufacture a launch-state object for a merely proposed build.
        """

        with self.read() as db:
            self._require_owner(db, owner)
            row = self._find_candidate_build(
                db,
                build_id,
                owner=owner,
            )
            return {
                "build_id": str(row["build_id"]),
                "status": str(row["status"]),
                "candidate_ref": (
                    str(row["candidate_ref"]) if row["candidate_ref"] is not None else None
                ),
            }

    def issue_for_claim(
        self,
        *,
        build_id: str,
        claim_owner: str,
        claim_epoch: int,
        owner: OwnerRef | None = None,
        lease_seconds: float = 300.0,
    ) -> Any:
        """Claim/recover one build and issue its host-only exact permit."""

        claim_owner = str(claim_owner).strip()
        if not claim_owner or claim_epoch < 1 or lease_seconds <= 0 or lease_seconds > 900:
            raise ValueError("candidate_build_claim_invalid")
        now = self._now()
        parsed_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
        expiry = (
            (parsed_now + timedelta(seconds=lease_seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self._write() as db:
            row = self._find_candidate_build(db, build_id, owner=owner)
            build_owner = self._candidate_build_owner(row)
            if owner is not None and build_owner != owner:
                raise CompanionOwnerError("candidate_build_owner_mismatch")
            owner = build_owner
            self._require_owner(db, owner)
            status = CandidateBuildStatus(str(row["status"]))
            if status in {
                CandidateBuildStatus.BUILT,
                CandidateBuildStatus.FAILED,
                CandidateBuildStatus.INCONCLUSIVE,
                CandidateBuildStatus.CANCELLED,
                CandidateBuildStatus.STALE,
            }:
                raise CompanionStateError("candidate_build_terminal")
            target = json.loads(str(row["target_fence_json"]))
            owner_key = str(target.get("owner_key") or "")
            permit = GrowthCandidateBuildPermitV1.issue(
                build_id=str(row["build_id"]),
                owner_key=owner_key,
                proposal_ref=str(row["proposal_ref"]),
                proposal_hash=str(row["proposal_hash"]),
                evidence_set_hash=str(row["evidence_set_hash"]),
                source_fence_hash=str(row["source_fence_hash"]),
                target_fence_hash=str(row["target_fence_hash"]),
                lease_epoch=claim_epoch,
            )
            if (
                int(row["claim_epoch"]) == claim_epoch
                and str(row["claim_owner"] or "") == claim_owner
                and self._candidate_build_permit_matches(row, permit)
            ):
                return permit
            lease_expired = (
                row["lease_expires_at"] is not None and str(row["lease_expires_at"]) <= now
            )
            if claim_epoch != int(row["claim_epoch"]) + 1 or (
                row["claim_owner"] is not None and not lease_expired and int(row["claim_epoch"]) > 0
            ):
                raise CompanionLeaseError("candidate_build_claim_stale")
            evidence_ids = tuple(json.loads(str(row["evidence_set_json"])))
            placeholders = ",".join("?" for _ in evidence_ids)
            live_count = int(
                db.execute(
                    f"""SELECT COUNT(*) FROM growth_events
                        WHERE profile_id=? AND profile_generation=?
                          AND content_state='live'
                          AND event_id IN ({placeholders})""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        *evidence_ids,
                    ),
                ).fetchone()[0]
            )
            if live_count != len(evidence_ids):
                db.execute(
                    """UPDATE candidate_builds
                       SET status='stale',last_error='stale_evidence',
                           reason_code='stale_evidence',updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND build_id=?""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        build_id,
                    ),
                )
                raise CompanionStateError("candidate_build_evidence_stale")
            db.execute(
                """UPDATE candidate_builds
                   SET claim_owner=?,claim_epoch=?,lease_expires_at=?,
                       attempt=attempt+1,build_permit_ref=?,
                       build_permit_hash=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND build_id=?""",
                (
                    claim_owner,
                    claim_epoch,
                    expiry,
                    permit.permit_id,
                    permit.permit_hash,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    build_id,
                ),
            )
            return permit

    def get_next_candidate_build(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str | None = None,
    ) -> Mapping[str, Any] | None:
        """Return the next recoverable build for exactly one owner.

        This is an advisory read.  ``claim_next_candidate_build`` performs the
        authoritative selection and lease CAS in one writer transaction.
        """

        now = self._now()
        with self.read() as db:
            self._require_owner(db, owner)
            params: list[Any] = [
                owner.profile_id,
                owner.profile_generation,
                now,
            ]
            owner_clause = ""
            if claim_owner is not None:
                normalized = str(claim_owner).strip()
                if not normalized:
                    raise ValueError("candidate_build_claim_owner_required")
                owner_clause = " OR claim_owner=?"
                params.append(normalized)
            row = db.execute(
                f"""SELECT * FROM candidate_builds
                    WHERE profile_id=? AND profile_generation=?
                      AND status IN (
                        'proposed','launch_pending','child_precreated',
                        'running','handoff_pending'
                      )
                      AND (
                        claim_owner IS NULL OR lease_expires_at<=?
                        {owner_clause}
                      )
                    ORDER BY
                      CASE status
                        WHEN 'handoff_pending' THEN 0
                        WHEN 'running' THEN 1
                        WHEN 'child_precreated' THEN 2
                        WHEN 'launch_pending' THEN 3
                        ELSE 4
                      END,
                      created_at,build_id
                    LIMIT 1""",
                tuple(params),
            ).fetchone()
            return None if row is None else dict(row)

    def claim_next_candidate_build(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float = 300.0,
        build_id: str | None = None,
    ) -> Any | None:
        """Atomically select an owner-fenced build and issue its exact permit.

        ``build_id`` pins recovery to the build named by an already claimed
        scheduler job.  Omitting it preserves the generic queue-consumer
        behavior that selects the owner's highest-priority recoverable build.
        """

        normalized = str(claim_owner).strip()
        normalized_build_id = None if build_id is None else str(build_id).strip()
        if (
            not normalized
            or (build_id is not None and not normalized_build_id)
            or lease_seconds <= 0
            or lease_seconds > 900
        ):
            raise ValueError("candidate_build_claim_invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM candidate_builds
                   WHERE profile_id=? AND profile_generation=?
                     AND status IN (
                       'proposed','launch_pending','child_precreated',
                       'running','handoff_pending'
                     )
                     AND (? IS NULL OR build_id=?)
                     AND (
                       claim_owner IS NULL OR lease_expires_at<=?
                       OR claim_owner=?
                     )
                   ORDER BY
                     CASE status
                       WHEN 'handoff_pending' THEN 0
                       WHEN 'running' THEN 1
                       WHEN 'child_precreated' THEN 2
                       WHEN 'launch_pending' THEN 3
                       ELSE 4
                     END,
                     created_at,build_id
                   LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    normalized_build_id,
                    normalized_build_id,
                    now,
                    normalized,
                ),
            ).fetchone()
            if row is None:
                return None
            active_replay = (
                str(row["claim_owner"] or "") == normalized
                and row["lease_expires_at"] is not None
                and str(row["lease_expires_at"]) > now
                and int(row["claim_epoch"]) > 0
                and row["build_permit_ref"] is not None
                and row["build_permit_hash"] is not None
            )
            claim_epoch = int(row["claim_epoch"]) if active_replay else int(row["claim_epoch"]) + 1
            return self.issue_for_claim(
                build_id=str(row["build_id"]),
                claim_owner=normalized,
                claim_epoch=claim_epoch,
                owner=owner,
                lease_seconds=lease_seconds,
            )

    def _candidate_build_transition(
        self,
        permit: Any,
        *,
        allowed: Sequence[str],
        target: str,
        updates: Mapping[str, Any] | None = None,
        reason_code: str,
    ) -> Any:
        now = self._now()
        with self._write() as db:
            row = self._find_candidate_build(db, permit.build_id)
            if not self._candidate_build_permit_matches(row, permit):
                raise CompanionLeaseError("candidate_build_permit_stale")
            if str(row["status"]) == target:
                return self._candidate_build_state(row)
            if str(row["status"]) not in set(allowed):
                raise CompanionStateError(
                    f"candidate_build_transition_invalid:{row['status']}:{target}"
                )
            assignments = ["status=?", "reason_code=?", "updated_at=?"]
            params: list[Any] = [target, reason_code, now]
            for name, value in (updates or {}).items():
                assignments.append(f"{name}=?")
                params.append(value)
            params.extend(
                (
                    row["profile_id"],
                    row["profile_generation"],
                    row["build_id"],
                    row["status"],
                    row["claim_epoch"],
                )
            )
            cursor = db.execute(
                f"""UPDATE candidate_builds SET {",".join(assignments)}
                    WHERE profile_id=? AND profile_generation=? AND build_id=?
                      AND status=? AND claim_epoch=?""",
                tuple(params),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError("candidate_build_transition_lost")
            updated = self._find_candidate_build(db, permit.build_id)
            return self._candidate_build_state(updated)

    def prepare_launch_pending(self, permit: Any, *, task_workspace: str) -> Any:
        workspace = str(task_workspace).strip()
        if not workspace:
            raise ValueError("candidate_build_workspace_required")
        with self._write() as db:
            row = self._find_candidate_build(db, permit.build_id)
            if not self._candidate_build_permit_matches(row, permit):
                raise CompanionLeaseError("candidate_build_permit_stale")
            existing_workspace = str(row["task_workspace"] or "")
            if existing_workspace and existing_workspace != workspace:
                raise CompanionConflictError("candidate_build_workspace_conflict")
            if str(row["status"]) == "proposed":
                db.execute(
                    """UPDATE candidate_builds
                       SET status='launch_pending',task_workspace=?,
                           reason_code='builder_launch_pending',updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND build_id=?
                         AND status='proposed' AND claim_epoch=?""",
                    (
                        workspace,
                        self._now(),
                        row["profile_id"],
                        row["profile_generation"],
                        row["build_id"],
                        permit.lease_epoch,
                    ),
                )
                row = self._find_candidate_build(db, permit.build_id)
            return self._candidate_build_state(row)

    def ack_child_precreated(
        self,
        permit: Any,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
    ) -> Any:
        self._verify_candidate_child_identity(
            permit,
            builder_launch_id=builder_launch_id,
            child_run_id=child_run_id,
            child_start_hash=child_start_hash,
        )
        return self._candidate_build_transition(
            permit,
            allowed=("launch_pending",),
            target="child_precreated",
            reason_code="builder_child_precreated",
        )

    def ack_running(
        self,
        permit: Any,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
    ) -> Any:
        self._verify_candidate_child_identity(
            permit,
            builder_launch_id=builder_launch_id,
            child_run_id=child_run_id,
            child_start_hash=child_start_hash,
        )
        return self._candidate_build_transition(
            permit,
            allowed=("child_precreated",),
            target="running",
            reason_code="builder_child_running",
        )

    def _verify_candidate_child_identity(
        self,
        permit: Any,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
    ) -> None:
        with self.read() as db:
            row = self._find_candidate_build(db, permit.build_id)
            if (
                not self._candidate_build_permit_matches(row, permit)
                or str(row["builder_launch_id"]) != builder_launch_id
                or str(row["builder_child_run_id"]) != child_run_id
                or str(row["expected_child_start_hash"]) != child_start_hash
            ):
                raise CompanionConflictError("candidate_builder_child_identity_mismatch")

    def ack_handoff_pending(self, permit: Any, *, expectation: Any) -> Any:
        values = {
            name: str(getattr(expectation, name)) for name in expectation.__dataclass_fields__
        }
        return self._candidate_build_transition(
            permit,
            allowed=("running",),
            target="handoff_pending",
            updates={
                "draft_receipt_ref": expectation.receipt_id,
                "draft_receipt_hash": expectation.receipt_hash,
                "draft_receipt_expectation_json": canonical_json(values),
            },
            reason_code="candidate_handoff_pending",
        )

    def ack_built(
        self,
        permit: Any,
        *,
        expectation: Any,
        handoff: Mapping[str, Any],
    ) -> Any:
        candidate_ref = str(handoff.get("candidate_ref") or "")
        candidate_hash = str(handoff.get("candidate_hash") or "")
        if not candidate_ref or len(candidate_hash) != 64:
            raise CompanionConflictError("candidate_handoff_identity_invalid")
        return self._candidate_build_transition(
            permit,
            allowed=("handoff_pending",),
            target="built",
            updates={
                "candidate_ref": candidate_ref,
                "candidate_hash": candidate_hash,
                "lease_expires_at": None,
            },
            reason_code="candidate_built",
        )

    def mark_stale(self, permit: Any, *, reason_code: str) -> Any:
        return self._candidate_build_transition(
            permit,
            allowed=(
                "proposed",
                "launch_pending",
                "child_precreated",
                "running",
                "handoff_pending",
            ),
            target="stale",
            updates={
                "last_error": str(reason_code),
                "lease_expires_at": None,
            },
            reason_code=str(reason_code),
        )

    def mark_failed(self, permit: Any, *, reason_code: str) -> Any:
        return self._candidate_build_transition(
            permit,
            allowed=(
                "launch_pending",
                "child_precreated",
                "running",
                "handoff_pending",
            ),
            target="failed",
            updates={
                "last_error": str(reason_code),
                "lease_expires_at": None,
            },
            reason_code=str(reason_code),
        )
