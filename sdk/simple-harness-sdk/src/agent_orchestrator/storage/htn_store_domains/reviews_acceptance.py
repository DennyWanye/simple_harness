# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Review packages and records, acceptances, goal resolutions, validity witnesses and observations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from simple_harness.contracts import canonical_json

from ...assurance.codec import AssuranceError
from ...contracts import models
from ...contracts.evidence_state import ObservationRecord, TruthValue, ValidityWitness, truth_change
from ...contracts.resolution import (
    Acceptance,
    GoalResolution,
    ReviewPackage,
    ReviewPurpose,
    ReviewRecord,
    account_for_purpose,
)
from ...contracts.semantic_base import content_hash_of, enum_of, identifier
from ...knowledge.validity import witness_subject
from ..store import StoreConflict
from ..taskgraph_source_events import record_source_change
from .core import HtnStoreCore
from .records import StoredReviewRecord, _observation_question


class HtnReviewAcceptanceMixin(HtnStoreCore):
    """Review packages and records, acceptances, goal resolutions, validity witnesses and observations."""

    __slots__ = ()

    if TYPE_CHECKING:

        def bump_epoch(self, mission_id: str, scope_id: str, *, bumped_by: str) -> int: ...

    # ================================================================== review
    def insert_review_package(self, package: ReviewPackage) -> str:
        if not isinstance(package, ReviewPackage):
            raise StoreConflict("insert_review_package expects a ReviewPackage")
        document = package.to_json()
        digest = content_hash_of(document)
        binding = package.binding
        self._insert(
            "INSERT INTO review_packages(package_id,mission_id,purpose,review_account,"
            "obligation_id,subject_kind,subject_id,requirements_revision,input_manifest_hash,"
            "method_instance_id,package_hash,package_json,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(package.package_id),
                binding.mission_id,
                str(package.purpose),
                str(account_for_purpose(package.purpose)),
                binding.obligation_id,
                str(binding.subject_ref.kind),
                binding.subject_ref.id,
                binding.requirements_revision,
                binding.input_manifest_hash,
                (None if package.method_instance_id is None else str(package.method_instance_id)),
                digest,
                canonical_json(document),
                self._store.now,
            ),
            f"review package {package.package_id!s} already stored",
        )
        return digest

    def get_review_package(self, package_id: str) -> ReviewPackage:
        row = self._one(
            "SELECT package_json FROM review_packages WHERE package_id = ?",
            (identifier(package_id, "package_id"),),
            f"no review package {package_id}",
        )
        return ReviewPackage.from_json(json.loads(row[0]))

    def list_review_packages(
        self, mission_id: str, *, purpose: ReviewPurpose | None = None
    ) -> tuple[ReviewPackage, ...]:
        clauses = ["mission_id = ?"]
        values: list[Any] = [identifier(mission_id, "mission_id")]
        if purpose is not None:
            clauses.append("purpose = ?")
            values.append(str(enum_of(ReviewPurpose, purpose, "purpose")))
        rows = self._store.connection.execute(
            f"SELECT package_json FROM review_packages WHERE {' AND '.join(clauses)}"
            " ORDER BY created_at, package_id",
            tuple(values),
        ).fetchall()
        return tuple(ReviewPackage.from_json(json.loads(row[0])) for row in rows)

    def insert_review_record(
        self, record: ReviewRecord, *, official: bool = False, assurance_import: Any = None
    ) -> str:
        """Store one judgement and its per-criterion evaluations in one transaction.

        At most one record per package may be ``official`` — AER §18.2's "同 review
        command 只一份正式接纳".  A second one is a conflict, not an overwrite.
        """

        if not isinstance(record, ReviewRecord):
            raise StoreConflict("insert_review_record expects a ReviewRecord")
        stored_package = self.get_review_package(record.package_id)
        if stored_package.purpose is not record.purpose:
            raise StoreConflict(
                f"review record {record.record_id!s} does not carry its package's purpose"
            )
        document = record.to_json()
        digest = content_hash_of(document)
        mission = record.binding.mission_id
        now = self._store.now
        with self._store.transaction() as connection:
            if official:
                # 按需导入：循环依赖（htn_store ↔ assurance_review_import）
                from ...orchestrator.assurance_review_import import PreparedOfficialReview

                # 按需导入：循环依赖（htn_store ↔ assurance_store）
                from ..assurance_store import AssuranceStore

                AssuranceStore(self._store).require_assured(mission)
                if not isinstance(assurance_import, PreparedOfficialReview):
                    raise AssuranceError("REVIEW_RUNTIME_IMPORT_REQUIRED")
                assurance_import.require_locked(self._store, record)
            self._execute(
                connection,
                "INSERT INTO review_records(record_id,package_id,mission_id,purpose,"
                "reviewer_agent_id,reviewer_turn_id,verdict,evidence_manifest_hash,official,"
                "record_hash,record_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(record.record_id),
                    str(record.package_id),
                    mission,
                    str(record.purpose),
                    record.reviewer_agent_id,
                    record.reviewer_turn_id,
                    str(record.verdict),
                    record.evidence_manifest_hash,
                    1 if official else 0,
                    digest,
                    canonical_json(document),
                    now,
                ),
                f"review record {record.record_id!s} conflicts with what is stored"
                f" for package {record.package_id!s}",
            )
            for outcome in record.criteria:
                receipt = content_hash_of(
                    {
                        "package_id": str(record.package_id),
                        "binding": record.binding.to_json(),
                        "outcome": outcome.to_json(),
                    }
                )
                self._execute(
                    connection,
                    "INSERT INTO criterion_evaluations(review_id,criterion_id,mission_id,"
                    "package_id,verdict,check_execution,check_receipt_hash,outcome_json,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        str(record.record_id),
                        outcome.criterion_id,
                        mission,
                        str(record.package_id),
                        str(outcome.verdict),
                        str(outcome.check_execution),
                        receipt,
                        canonical_json(outcome.to_json()),
                        now,
                    ),
                    f"criterion {outcome.criterion_id} appears twice in review"
                    f" {record.record_id!s}",
                )
        return digest

    def get_review_record(self, record_id: str) -> StoredReviewRecord:
        row = self._one(
            "SELECT record_json, official FROM review_records WHERE record_id = ?",
            (identifier(record_id, "record_id"),),
            f"no review record {record_id}",
        )
        return StoredReviewRecord(
            record=ReviewRecord.from_json(json.loads(row[0])), official=bool(row[1])
        )

    def list_review_records(self, package_id: str) -> tuple[StoredReviewRecord, ...]:
        rows = self._store.connection.execute(
            "SELECT record_json, official FROM review_records WHERE package_id = ?"
            " ORDER BY created_at, record_id",
            (identifier(package_id, "package_id"),),
        ).fetchall()
        return tuple(
            StoredReviewRecord(
                record=ReviewRecord.from_json(json.loads(row[0])), official=bool(row[1])
            )
            for row in rows
        )

    def official_review_record(self, package_id: str) -> ReviewRecord | None:
        row = self._store.connection.execute(
            "SELECT record_json FROM review_records WHERE package_id = ? AND official = 1",
            (identifier(package_id, "package_id"),),
        ).fetchone()
        return None if row is None else ReviewRecord.from_json(json.loads(row[0]))

    def list_criterion_evaluations(self, review_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._store.connection.execute(
            "SELECT criterion_id, verdict, check_execution, check_receipt_hash, outcome_json"
            " FROM criterion_evaluations WHERE review_id = ? ORDER BY criterion_id",
            (identifier(review_id, "review_id"),),
        ).fetchall()
        return tuple(
            {
                "criterion_id": row[0],
                "verdict": row[1],
                "check_execution": row[2],
                "check_receipt_hash": row[3],
                "outcome": json.loads(row[4]),
            }
            for row in rows
        )

    # ================================================================== acceptance
    def insert_acceptance(self, acceptance: Acceptance) -> str:
        """Store one acceptance.  Re-submitting the same content returns its hash."""

        if not isinstance(acceptance, Acceptance):
            raise StoreConflict("insert_acceptance expects an Acceptance")
        document = acceptance.to_json()
        digest = content_hash_of(document)
        existing = self._store.connection.execute(
            "SELECT content_hash FROM acceptances WHERE acceptance_id = ?",
            (str(acceptance.acceptance_id),),
        ).fetchone()
        if existing is not None:
            if existing[0] != digest:
                raise StoreConflict(
                    f"acceptance {acceptance.acceptance_id!s} is already stored with"
                    " different content"
                )
            return digest
        self._insert(
            "INSERT INTO acceptances(acceptance_id,mission_id,task_id,obligation_id,"
            "review_record_id,requirements_revision,contract_revision,input_manifest_hash,"
            "validity,accepted_at_ms,content_hash,acceptance_json,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(acceptance.acceptance_id),
                acceptance.mission_id,
                str(acceptance.task_id),
                str(acceptance.obligation_id),
                str(acceptance.review_record_id),
                acceptance.requirements_revision,
                acceptance.contract_revision,
                acceptance.input_manifest_hash,
                str(acceptance.validity),
                acceptance.accepted_at_ms,
                digest,
                canonical_json(document),
                self._store.now,
            ),
            f"acceptance {acceptance.acceptance_id!s} could not be stored",
        )
        return digest

    def get_acceptance(self, acceptance_id: str) -> Acceptance:
        row = self._one(
            "SELECT acceptance_json FROM acceptances WHERE acceptance_id = ?",
            (identifier(acceptance_id, "acceptance_id"),),
            f"no acceptance {acceptance_id}",
        )
        return Acceptance.from_json(json.loads(row[0]))

    def list_acceptances(
        self, mission_id: str, *, obligation_id: str | None = None
    ) -> tuple[Acceptance, ...]:
        clauses = ["mission_id = ?"]
        values: list[Any] = [identifier(mission_id, "mission_id")]
        if obligation_id is not None:
            clauses.append("obligation_id = ?")
            values.append(identifier(obligation_id, "obligation_id"))
        rows = self._store.connection.execute(
            f"SELECT acceptance_json FROM acceptances WHERE {' AND '.join(clauses)}"
            " ORDER BY accepted_at_ms, acceptance_id",
            tuple(values),
        ).fetchall()
        return tuple(Acceptance.from_json(json.loads(row[0])) for row in rows)

    def insert_goal_resolution(self, resolution: GoalResolution, *, adopted: bool = False) -> str:
        if not isinstance(resolution, GoalResolution):
            raise StoreConflict("insert_goal_resolution expects a GoalResolution")
        document = resolution.to_json()
        digest = content_hash_of(document)
        now = self._store.now
        self._insert(
            "INSERT INTO goal_resolutions(resolution_id,mission_id,obligation_id,goal_task_id,"
            "requirements_version,contract_revision,method_instance_id,review_receipt_id,verdict,"
            "validity,adopted,content_hash,resolution_json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(resolution.resolution_id),
                resolution.mission_id,
                resolution.obligation_id,
                resolution.goal_task_id,
                resolution.requirements_version,
                resolution.contract_revision,
                resolution.method_instance_id,
                resolution.review_receipt_id,
                str(resolution.verdict),
                str(resolution.validity),
                1 if adopted else 0,
                digest,
                canonical_json(document),
                now,
                now,
            ),
            f"goal resolution {resolution.resolution_id!s} conflicts with what is stored"
            f" for obligation {resolution.obligation_id}",
        )
        return digest

    def get_goal_resolution(self, resolution_id: str) -> GoalResolution:
        row = self._one(
            "SELECT resolution_json FROM goal_resolutions WHERE resolution_id = ?",
            (identifier(resolution_id, "resolution_id"),),
            f"no goal resolution {resolution_id}",
        )
        return GoalResolution.from_json(json.loads(row[0]))

    def adopted_goal_resolution(self, mission_id: str, obligation_id: str) -> GoalResolution | None:
        row = self._store.connection.execute(
            "SELECT resolution_json FROM goal_resolutions WHERE mission_id = ?"
            " AND obligation_id = ? AND adopted = 1",
            (identifier(mission_id, "mission_id"), identifier(obligation_id, "obligation_id")),
        ).fetchone()
        return None if row is None else GoalResolution.from_json(json.loads(row[0]))

    def list_goal_resolutions(
        self, mission_id: str, *, obligation_id: str | None = None
    ) -> tuple[GoalResolution, ...]:
        clauses = ["mission_id = ?"]
        values: list[Any] = [identifier(mission_id, "mission_id")]
        if obligation_id is not None:
            clauses.append("obligation_id = ?")
            values.append(identifier(obligation_id, "obligation_id"))
        rows = self._store.connection.execute(
            f"SELECT resolution_json FROM goal_resolutions WHERE {' AND '.join(clauses)}"
            " ORDER BY created_at, resolution_id",
            tuple(values),
        ).fetchall()
        return tuple(GoalResolution.from_json(json.loads(row[0])) for row in rows)

    # ================================================================== evidence
    def insert_validity_witness(
        self, mission_id: str, witness: ValidityWitness, *, subject: str
    ) -> ValidityWitness:
        """Store one licence, filed under the subject its issuer names (migration 18).

        ``subject`` is declared by the caller and re-checked here against
        :func:`~..knowledge.validity.witness_subject`, which recomputes it from the
        witness alone.  The declaration is what AER §8.1 asks for — a licence says
        which support it was taken over — and the recomputation is what keeps the row
        auditable (§16.1): a subject the witness does not name would let two different
        licences be filed under one key again, so it is refused rather than stored.
        """

        if not isinstance(witness, ValidityWitness):
            raise StoreConflict("insert_validity_witness expects a ValidityWitness")
        expected = witness_subject(witness)
        if str(subject) != expected:
            raise StoreConflict(
                f"validity witness {witness.witness_id} was offered under subject {subject!r}"
                f" but names {expected!r}; a licence is filed under the subject it says it"
                " was taken over, never under one supplied beside it"
            )
        mission = identifier(mission_id, "mission_id")
        with self._store.transaction():
            self._insert(
                "INSERT INTO validity_witnesses(witness_id,mission_id,consumer_kind,consumer_id,"
                "purpose,subject_digest,scope_id,scope_epoch,support_revision,truth,freshness,"
                "availability,decision,as_of_ms,not_after_ms,witness_json,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    witness.witness_id,
                    mission,
                    str(witness.consumer_ref.kind),
                    witness.consumer_ref.id,
                    str(witness.purpose),
                    expected,
                    witness.scope_id,
                    witness.scope_epoch,
                    witness.support_revision,
                    str(witness.truth),
                    str(witness.freshness),
                    str(witness.availability),
                    str(witness.decision),
                    witness.as_of_ms,
                    witness.not_after_ms,
                    canonical_json(witness.to_json()),
                    self._store.now,
                ),
                f"validity witness {witness.witness_id} conflicts with one already stored"
                f" for {witness.consumer_ref.id}/{witness.purpose!s}",
            )

            record_source_change(
                self._store,
                mission,
                kind="validity_witness",
                source_id=witness.witness_id,
                revision=1,
                content_hash=models.sha256_hex(witness.to_json()),
            )
        return witness

    def get_validity_witness(self, witness_id: str) -> ValidityWitness:
        row = self._one(
            "SELECT witness_json FROM validity_witnesses WHERE witness_id = ?",
            (identifier(witness_id, "witness_id"),),
            f"no validity witness {witness_id}",
        )
        return ValidityWitness.from_json(json.loads(row[0]))

    def list_validity_witnesses(
        self, mission_id: str, *, scope_id: str | None = None, subject: str | None = None
    ) -> tuple[ValidityWitness, ...]:
        clauses = ["mission_id = ?"]
        values: list[Any] = [identifier(mission_id, "mission_id")]
        if scope_id is not None:
            clauses.append("scope_id = ?")
            values.append(identifier(scope_id, "scope_id"))
        if subject is not None:
            clauses.append("subject_digest = ?")
            values.append(str(subject))
        rows = self._store.connection.execute(
            f"SELECT witness_json FROM validity_witnesses WHERE {' AND '.join(clauses)}"
            " ORDER BY as_of_ms, witness_id",
            tuple(values),
        ).fetchall()
        return tuple(ValidityWitness.from_json(json.loads(row[0])) for row in rows)

    def insert_observation(
        self,
        mission_id: str,
        observation: ObservationRecord,
        *,
        question: Mapping[str, Any] | None,
        scope_id: str = "mission",
    ) -> ObservationRecord:
        """The one write entry for an observation.

        ``question`` is what was asked — ``{"predicate_ref", "arguments"}`` — stored on
        the row so the proposition can be read again; it must compute back to the
        record's proposition key (an order check, not a judgement).

        The scope's validity epoch is raised here, in the same transaction, when this
        observation changes a truth that was already known (TRUE/FALSE before, something
        else after): every witness taken under the old epoch stops being usable.  A
        first look (UNKNOWN before) and a look at a conflicted proposition raise nothing —
        no usable witness was ever issued on those."""
        if not isinstance(observation, ObservationRecord):
            raise StoreConflict("insert_observation expects an ObservationRecord")
        mission = identifier(mission_id, "mission_id")
        asked = _observation_question(question, observation.proposition_key)
        with self._store.transaction():
            before, after = truth_change(
                self.list_observations(mission, proposition_key=observation.proposition_key),
                observation,
            )
            self._insert(
                "INSERT INTO observations(observation_id,mission_id,proposition_key,polarity,scope_id,"
                "source_kind,source_id,coverage,observer_id,observed_at_ms,recorded_at_ms,"
                "query_watermark_ms,valid_until_ms,observation_json,created_at,question_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    observation.observation_id,
                    mission,
                    observation.proposition_key,
                    1 if observation.polarity else 0,
                    identifier(scope_id, "scope_id"),
                    str(observation.source_ref.kind),
                    observation.source_ref.id,
                    str(observation.coverage),
                    observation.observer_id,
                    observation.observed_at_ms,
                    observation.recorded_at_ms,
                    observation.query_watermark_ms,
                    observation.valid_until_ms,
                    canonical_json(observation.to_json()),
                    self._store.now,
                    asked,
                ),
                f"observation {observation.observation_id} already stored",
            )
            if before in (TruthValue.TRUE, TruthValue.FALSE) and after is not before:
                self.bump_epoch(
                    mission,
                    identifier(scope_id, "scope_id"),
                    bumped_by=f"observation:{observation.observation_id}",
                )

            record_source_change(
                self._store,
                mission,
                kind="observation",
                source_id=observation.observation_id,
                revision=1,
                content_hash=models.sha256_hex(observation.to_json()),
            )
        return observation

    def observation_questions(self, mission_id: str) -> tuple[dict[str, Any], ...]:
        """One row per recorded proposition: what was asked and in which scope (the latest
        observation's), for the re-read."""
        rows = self._store.connection.execute(
            "SELECT proposition_key, scope_id, question_json FROM observations WHERE mission_id=?"
            " ORDER BY observed_at_ms, observation_id",
            (identifier(mission_id, "mission_id"),),
        ).fetchall()
        found: dict[str, dict[str, Any]] = {}
        for key, scope, raw in rows:
            if not raw:
                raise StoreConflict(
                    f"observation of {key} records no question; it cannot be read again"
                )
            found[str(key)] = {
                "proposition_key": str(key),
                "scope_id": str(scope),
                **json.loads(raw),
            }
        return tuple(found[key] for key in sorted(found))

    def get_observation(self, observation_id: str) -> ObservationRecord:
        row = self._one(
            "SELECT observation_json FROM observations WHERE observation_id = ?",
            (identifier(observation_id, "observation_id"),),
            f"no observation {observation_id}",
        )
        return ObservationRecord.from_json(json.loads(row[0]))

    def list_observations(
        self, mission_id: str, *, proposition_key: str | None = None
    ) -> tuple[ObservationRecord, ...]:
        clauses = ["mission_id = ?"]
        values: list[Any] = [identifier(mission_id, "mission_id")]
        if proposition_key is not None:
            clauses.append("proposition_key = ?")
            values.append(str(proposition_key))
        rows = self._store.connection.execute(
            f"SELECT observation_json FROM observations WHERE {' AND '.join(clauses)}"
            " ORDER BY observed_at_ms, observation_id",
            tuple(values),
        ).fetchall()
        return tuple(ObservationRecord.from_json(json.loads(row[0])) for row in rows)
