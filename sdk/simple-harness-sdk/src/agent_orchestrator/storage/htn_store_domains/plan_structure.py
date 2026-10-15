# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Plan revisions, memberships, order constraints, data requirements, input manifests and requirements revisions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts.htn import DataRequirement, OccurrenceSpec, OrderConstraint, SemanticReadSet
from ...contracts.resolution import RequirementsRevision
from ...contracts.semantic_base import content_hash_of, identifier, index
from ..store import StoreConflict
from .core import HtnStoreCore
from .records import PLAN_REVISION_STATES, StoredPlanRevision


class HtnPlanStructureMixin(HtnStoreCore):
    """Plan revisions, memberships, order constraints, data requirements, input manifests and requirements revisions."""

    __slots__ = ()

    # ================================================================== plan revisions
    def insert_plan_revision(
        self,
        mission_id: str,
        revision: int,
        *,
        snapshot_hash: str,
        read_set: SemanticReadSet,
        state: str = "PREPARED",
        base_revision: int | None = None,
        delta_id: str | None = None,
    ) -> StoredPlanRevision:
        if not isinstance(read_set, SemanticReadSet):
            raise StoreConflict("insert_plan_revision expects a SemanticReadSet")
        mission = identifier(mission_id, "mission_id")
        number = index(revision, "revision")
        lifecycle = self._state(state, PLAN_REVISION_STATES, "plan revision state")
        base = None if base_revision is None else index(base_revision, "base_revision")
        now = self._store.now
        self._insert(
            "INSERT INTO plan_revisions(mission_id,revision,state,base_revision,snapshot_hash,"
            "delta_id,read_set_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                mission,
                number,
                lifecycle,
                base,
                snapshot_hash,
                delta_id,
                canonical_json(read_set.to_json()),
                now,
                now,
            ),
            f"plan revision {mission}@{number} already stored",
        )
        return StoredPlanRevision(
            mission_id=mission,
            revision=number,
            state=lifecycle,
            base_revision=base,
            snapshot_hash=snapshot_hash,
            delta_id=delta_id,
            read_set=read_set,
        )

    def get_plan_revision(self, mission_id: str, revision: int) -> StoredPlanRevision:
        row = self._one(
            "SELECT * FROM plan_revisions WHERE mission_id = ? AND revision = ?",
            (identifier(mission_id, "mission_id"), index(revision, "revision")),
            f"no plan revision {revision} in {mission_id}",
        )
        return self._plan_revision(row)

    def list_plan_revisions(self, mission_id: str) -> tuple[StoredPlanRevision, ...]:
        rows = self._store.connection.execute(
            "SELECT * FROM plan_revisions WHERE mission_id = ? ORDER BY revision",
            (identifier(mission_id, "mission_id"),),
        ).fetchall()
        return tuple(self._plan_revision(row) for row in rows)

    def active_plan_revision(self, mission_id: str) -> StoredPlanRevision | None:
        row = self._store.connection.execute(
            "SELECT * FROM plan_revisions WHERE mission_id = ? AND state = 'ACTIVE'",
            (identifier(mission_id, "mission_id"),),
        ).fetchone()
        return None if row is None else self._plan_revision(row)

    def activate_plan_revision(self, mission_id: str, revision: int) -> StoredPlanRevision:
        """Adopt one revision.  The previous ACTIVE is retired in the same transaction.

        A Mission has exactly one plan under execution control (§20 rule 5); the
        partial unique index enforces it, and this method is the only way to move
        between revisions without tripping it.
        """

        mission = identifier(mission_id, "mission_id")
        number = index(revision, "revision")
        current = self.get_plan_revision(mission, number)
        if current.state == "RETIRED":
            raise StoreConflict(f"plan revision {mission}@{number} is retired")
        now = self._store.now
        with self._store.transaction() as connection:
            connection.execute(
                "UPDATE plan_revisions SET state = 'RETIRED', updated_at = ?"
                " WHERE mission_id = ? AND state = 'ACTIVE' AND revision <> ?",
                (now, mission, number),
            )
            self._execute(
                connection,
                "UPDATE plan_revisions SET state = 'ACTIVE', updated_at = ?"
                " WHERE mission_id = ? AND revision = ?",
                (now, mission, number),
                f"plan revision {mission}@{number} could not be activated",
            )
        return self.get_plan_revision(mission, number)

    def insert_plan_membership(
        self,
        mission_id: str,
        revision: int,
        occurrence: OccurrenceSpec,
        *,
        instance_id: str | None = None,
        adopted: bool = True,
    ) -> OccurrenceSpec:
        if not isinstance(occurrence, OccurrenceSpec):
            raise StoreConflict("insert_plan_membership expects an OccurrenceSpec")
        mission = identifier(mission_id, "mission_id")
        number = index(revision, "revision")
        self._insert(
            "INSERT INTO plan_memberships(mission_id,revision,occurrence_id,task_id,obligation_id,"
            "instance_id,form,requiredness,adopted,member_json,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                mission,
                number,
                str(occurrence.occurrence_id),
                str(occurrence.task_id),
                str(occurrence.obligation_id),
                None if instance_id is None else identifier(instance_id, "instance_id"),
                str(occurrence.form),
                str(occurrence.requiredness),
                1 if adopted else 0,
                canonical_json(occurrence.to_json()),
                self._store.now,
            ),
            f"occurrence {occurrence.occurrence_id!s} already a member of {mission}@{number}",
        )
        return occurrence

    def list_plan_memberships(self, mission_id: str, revision: int) -> tuple[OccurrenceSpec, ...]:
        rows = self._store.connection.execute(
            "SELECT member_json FROM plan_memberships WHERE mission_id = ? AND revision = ?"
            " ORDER BY occurrence_id",
            (identifier(mission_id, "mission_id"), index(revision, "revision")),
        ).fetchall()
        return tuple(OccurrenceSpec.from_json(json.loads(row[0])) for row in rows)

    # ================================================================== order and data
    def insert_order_constraint(
        self, mission_id: str, plan_revision: int, constraint: OrderConstraint
    ) -> OrderConstraint:
        if not isinstance(constraint, OrderConstraint):
            raise StoreConflict("insert_order_constraint expects an OrderConstraint")
        mission = identifier(mission_id, "mission_id")
        number = index(plan_revision, "plan_revision")
        self._insert(
            "INSERT INTO order_constraints(mission_id,plan_revision,before_occurrence,"
            "after_occurrence,release_condition,constraint_json,created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                mission,
                number,
                str(constraint.before),
                str(constraint.after),
                str(constraint.release_condition),
                canonical_json(constraint.to_json()),
                self._store.now,
            ),
            f"order {constraint.before!s} → {constraint.after!s} already stored"
            f" in {mission}@{number}",
        )
        return constraint

    def list_order_constraints(
        self,
        mission_id: str,
        plan_revision: int,
        *,
        before: str | None = None,
        after: str | None = None,
    ) -> tuple[OrderConstraint, ...]:
        clauses = ["mission_id = ?", "plan_revision = ?"]
        values: list[Any] = [
            identifier(mission_id, "mission_id"),
            index(plan_revision, "plan_revision"),
        ]
        if before is not None:
            clauses.append("before_occurrence = ?")
            values.append(identifier(before, "before"))
        if after is not None:
            clauses.append("after_occurrence = ?")
            values.append(identifier(after, "after"))
        rows = self._store.connection.execute(
            f"SELECT constraint_json FROM order_constraints WHERE {' AND '.join(clauses)}"
            " ORDER BY before_occurrence, after_occurrence",
            tuple(values),
        ).fetchall()
        return tuple(OrderConstraint.from_json(json.loads(row[0])) for row in rows)

    def insert_data_requirement(
        self, mission_id: str, plan_revision: int, requirement: DataRequirement
    ) -> DataRequirement:
        if not isinstance(requirement, DataRequirement):
            raise StoreConflict("insert_data_requirement expects a DataRequirement")
        mission = identifier(mission_id, "mission_id")
        number = index(plan_revision, "plan_revision")
        self._insert(
            "INSERT INTO data_requirements(mission_id,plan_revision,requirement_id,"
            "producer_occurrence,output_port,consumer_occurrence,input_port,"
            "requirement_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                mission,
                number,
                requirement.requirement_id,
                str(requirement.producer_occurrence),
                requirement.output_port,
                str(requirement.consumer_occurrence),
                requirement.input_port,
                canonical_json(requirement.to_json()),
                self._store.now,
            ),
            f"data requirement {requirement.requirement_id} already stored in {mission}@{number}",
        )
        return requirement

    def list_data_requirements(
        self,
        mission_id: str,
        plan_revision: int,
        *,
        consumer_occurrence: str | None = None,
        producer_occurrence: str | None = None,
    ) -> tuple[DataRequirement, ...]:
        clauses = ["mission_id = ?", "plan_revision = ?"]
        values: list[Any] = [
            identifier(mission_id, "mission_id"),
            index(plan_revision, "plan_revision"),
        ]
        if consumer_occurrence is not None:
            clauses.append("consumer_occurrence = ?")
            values.append(identifier(consumer_occurrence, "consumer_occurrence"))
        if producer_occurrence is not None:
            clauses.append("producer_occurrence = ?")
            values.append(identifier(producer_occurrence, "producer_occurrence"))
        rows = self._store.connection.execute(
            f"SELECT requirement_json FROM data_requirements WHERE {' AND '.join(clauses)}"
            " ORDER BY requirement_id",
            tuple(values),
        ).fetchall()
        return tuple(DataRequirement.from_json(json.loads(row[0])) for row in rows)

    def insert_input_manifest(
        self,
        mission_id: str,
        task_id: str,
        manifest: Mapping[str, Any],
        *,
        attempt_id: str | None = None,
        request_id: str | None = None,
        input_binding_revision: int = 0,
    ) -> str:
        """Freeze one input set and bind this task to it.  Identity is the content hash.

        The content row is immutable and Mission-free; *using* it is a binding row.
        Two tasks — in one Mission or in two — that resolve to byte-identical inputs
        therefore share one immutable manifest and get one binding each, instead of
        one of them being refused for reusing the other's bytes.

        The document is kept as opaque canonical JSON: the resolved-manifest type
        lives in ``artifacts/input_bindings`` (P2.2b) and this table must not become
        a second authority on how an input set is built.
        """

        mission = identifier(mission_id, "mission_id")
        task = identifier(task_id, "task_id")
        if not isinstance(manifest, Mapping):
            raise StoreConflict("insert_input_manifest expects a mapping")
        document = dict(manifest)
        digest = content_hash_of(document)
        revision = index(input_binding_revision, "input_binding_revision")
        now = self._store.now
        with self._store.transaction() as connection:
            frozen = canonical_json(document)
            known = connection.execute(
                "SELECT manifest_json FROM input_manifests WHERE manifest_hash=?", (digest,)
            ).fetchone()
            if known is not None:
                if known[0] != frozen:
                    raise StoreConflict(f"input manifest {digest} has a different frozen body")
            else:
                self._execute(
                    connection,
                    "INSERT INTO input_manifests(manifest_hash,origin_mission_id,manifest_json,"
                    "created_at) VALUES (?,?,?,?)",
                    (digest, mission, frozen, now),
                    f"input manifest {digest} could not be stored",
                )
            self._execute(
                connection,
                "INSERT INTO input_manifest_bindings(mission_id,task_id,manifest_hash,attempt_id,"
                "request_id,input_binding_revision,created_at) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(mission_id,task_id,manifest_hash,input_binding_revision) DO NOTHING",
                (mission, task, digest, attempt_id, request_id, revision, now),
                f"task {task} could not be bound to input manifest {digest}",
            )
        return digest

    def get_input_manifest(self, manifest_hash: str) -> dict[str, Any]:
        row = self._one(
            "SELECT manifest_json FROM input_manifests WHERE manifest_hash = ?",
            (identifier(manifest_hash, "manifest_hash"),),
            f"no input manifest {manifest_hash}",
        )
        loaded: dict[str, Any] = json.loads(row[0])
        return loaded

    def list_manifest_bindings(self, manifest_hash: str) -> tuple[dict[str, Any], ...]:
        """Every (Mission, task) that froze this exact input set."""

        rows = self._store.connection.execute(
            "SELECT mission_id, task_id, attempt_id, request_id, input_binding_revision"
            " FROM input_manifest_bindings WHERE manifest_hash = ? ORDER BY mission_id, task_id",
            (identifier(manifest_hash, "manifest_hash"),),
        ).fetchall()
        return tuple(
            {
                "mission_id": row[0],
                "task_id": row[1],
                "attempt_id": row[2],
                "request_id": row[3],
                "input_binding_revision": row[4],
            }
            for row in rows
        )

    # ================================================================== requirements
    def insert_requirements_revision(self, revision: RequirementsRevision) -> str:
        if not isinstance(revision, RequirementsRevision):
            raise StoreConflict("insert_requirements_revision expects a RequirementsRevision")
        document = revision.to_json()
        digest = content_hash_of(document)
        self._insert(
            "INSERT INTO requirements_revisions(mission_id,revision,revision_id,content_hash,"
            "authority_subject,revision_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                revision.mission_id,
                revision.revision,
                str(revision.revision_id),
                digest,
                revision.authority_subject,
                canonical_json(document),
                self._store.now,
            ),
            f"requirements revision {revision.mission_id}@{revision.revision} already stored",
        )
        return digest

    def get_requirements_revision(self, mission_id: str, revision: int) -> RequirementsRevision:
        row = self._one(
            "SELECT revision_json FROM requirements_revisions WHERE mission_id = ? AND revision = ?",
            (identifier(mission_id, "mission_id"), index(revision, "revision")),
            f"no requirements revision {revision} in {mission_id}",
        )
        return RequirementsRevision.from_json(json.loads(row[0]))

    def latest_requirements_revision(self, mission_id: str) -> RequirementsRevision | None:
        row = self._store.connection.execute(
            "SELECT revision_json FROM requirements_revisions WHERE mission_id = ?"
            " ORDER BY revision DESC LIMIT 1",
            (identifier(mission_id, "mission_id"),),
        ).fetchone()
        return None if row is None else RequirementsRevision.from_json(json.loads(row[0]))

    def list_requirements_revisions(self, mission_id: str) -> tuple[RequirementsRevision, ...]:
        rows = self._store.connection.execute(
            "SELECT revision_json FROM requirements_revisions WHERE mission_id=? ORDER BY revision",
            (identifier(mission_id, "mission_id"),),
        ).fetchall()
        return tuple(RequirementsRevision.from_json(json.loads(row[0])) for row in rows)

    @staticmethod
    def _plan_revision(row: sqlite3.Row) -> StoredPlanRevision:
        return StoredPlanRevision(
            mission_id=str(row["mission_id"]),
            revision=int(row["revision"]),
            state=str(row["state"]),
            base_revision=None if row["base_revision"] is None else int(row["base_revision"]),
            snapshot_hash=str(row["snapshot_hash"]),
            delta_id=row["delta_id"],
            read_set=SemanticReadSet.from_json(json.loads(row["read_set_json"])),
        )
