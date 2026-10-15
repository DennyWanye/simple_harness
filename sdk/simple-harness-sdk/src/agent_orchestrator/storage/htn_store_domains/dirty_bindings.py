# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Epochs, dirty marks, operation bindings and read sets."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from simple_harness.contracts import canonical_json

from ...contracts import models
from ...contracts.htn import MethodInstanceDraft, OccurrenceSpec, SemanticReadSet
from ...contracts.resolution import OperationEnvelope
from ...contracts.semantic_base import content_hash_of, identifier, index
from ..store import StoreConflict
from ..taskgraph_source_events import record_source_change
from .core import HtnStoreCore
from .records import DIRTY_STATES, DirtyEntry, StoredPlanRevision


class HtnDirtyBindingMixin(HtnStoreCore):
    """Epochs, dirty marks, operation bindings and read sets."""

    __slots__ = ()

    if TYPE_CHECKING:

        def active_plan_revision(self, mission_id: str) -> StoredPlanRevision | None: ...
        def list_method_instances(
            self, mission_id: str, *, plan_revision: int | None = None, state: str | None = None
        ) -> tuple[MethodInstanceDraft, ...]: ...
        def list_plan_memberships(
            self, mission_id: str, revision: int
        ) -> tuple[OccurrenceSpec, ...]: ...

    def bump_epoch(self, mission_id: str, scope_id: str, *, bumped_by: str) -> int:
        """Raise a scope's validity epoch.  Readers behind it must fail closed."""

        mission = identifier(mission_id, "mission_id")
        scope = identifier(scope_id, "scope_id")
        actor = identifier(bumped_by, "bumped_by")
        with self._store.transaction() as connection:
            row = connection.execute(
                "SELECT epoch FROM validity_epochs WHERE mission_id = ? AND scope_id = ?",
                (mission, scope),
            ).fetchone()
            # TaskGraph readers treat a missing row as epoch zero, so the first
            # invalidation moves to one; inserting zero would leave the original
            # witnesses current.
            epoch = 1 if row is None else int(row[0]) + 1
            connection.execute(
                "INSERT INTO validity_epochs(mission_id,scope_id,epoch,bumped_by,updated_at)"
                " VALUES (?,?,?,?,?) ON CONFLICT(mission_id,scope_id) DO UPDATE SET"
                " epoch = excluded.epoch, bumped_by = excluded.bumped_by,"
                " updated_at = excluded.updated_at",
                (mission, scope, epoch, actor, self._store.now),
            )

            record_source_change(
                self._store,
                mission,
                kind="validity_epoch",
                source_id=scope,
                revision=epoch,
                content_hash=models.sha256_hex(
                    {"mission_id": mission, "scope_id": scope, "epoch": epoch, "bumped_by": actor}
                ),
            )
        return epoch

    def epoch(self, mission_id: str, scope_id: str) -> int:
        row = self._store.connection.execute(
            "SELECT epoch FROM validity_epochs WHERE mission_id = ? AND scope_id = ?",
            (identifier(mission_id, "mission_id"), identifier(scope_id, "scope_id")),
        ).fetchone()
        return 0 if row is None else int(row[0])

    def mark_dirty(
        self,
        mission_id: str,
        *,
        subject_kind: str,
        subject_id: str,
        scope_id: str,
        epoch: int,
        reason: str,
        state: str = "PENDING",
    ) -> DirtyEntry:
        mission = identifier(mission_id, "mission_id")
        entry = DirtyEntry(
            mission_id=mission,
            subject_kind=identifier(subject_kind, "subject_kind"),
            subject_id=identifier(subject_id, "subject_id"),
            epoch=index(epoch, "epoch"),
            scope_id=identifier(scope_id, "scope_id"),
            reason=str(reason),
            state=self._state(state, DIRTY_STATES, "dirty state"),
        )
        now = self._store.now
        with self._store.transaction() as connection:
            connection.execute(
                "INSERT INTO validity_dirty(mission_id,subject_kind,subject_id,epoch,scope_id,"
                "reason,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(mission_id,subject_kind,subject_id,epoch) DO UPDATE SET"
                " state = excluded.state, reason = excluded.reason,"
                " updated_at = excluded.updated_at",
                (
                    entry.mission_id,
                    entry.subject_kind,
                    entry.subject_id,
                    entry.epoch,
                    entry.scope_id,
                    entry.reason,
                    entry.state,
                    now,
                    now,
                ),
            )
        return entry

    def clear_revoked_generation(self, mission_id: str, goal_task_id: str) -> int:
        """A result of the goal's current generation was committed: its revocations are done.

        A plan commit that re-versions a Task marks each of its occurrences
        ``dispatch_generation_revoked``; nothing else ever clears that mark, and every
        completion reader refuses a dirty subject.  The revision moved with the mark, and
        a goal resolution is admitted only at the current contract revision, so once one
        is committed the revoked generation can no longer be mistaken for the live one.
        Only this goal's revocations are cleared; marks for any other reason stay.
        """

        mission = identifier(mission_id, "mission_id")
        task_id = identifier(goal_task_id, "goal_task_id")
        subjects = {task_id}
        active = self.active_plan_revision(mission)
        if active is not None:
            subjects |= {
                str(spec.occurrence_id)
                for spec in self.list_plan_memberships(mission, active.revision)
                if str(spec.task_id) == task_id
            }
        subjects |= {
            str(item.effective_goal_occurrence_id)
            for item in self.list_method_instances(mission, state="ADOPTED")
            if str(item.goal_id) == task_id and item.effective_goal_occurrence_id
        }
        marks = ",".join("?" * len(subjects))
        with self._store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE validity_dirty SET state='CLEARED', updated_at=? WHERE mission_id=?"
                " AND subject_kind='occurrence' AND reason='dispatch_generation_revoked'"
                f" AND state IN ('PENDING','RECHECKING') AND subject_id IN ({marks})",  # noqa: S608
                (self._store.now, mission, *sorted(subjects)),
            )
        return int(cursor.rowcount)

    def list_dirty(self, mission_id: str, *, state: str = "PENDING") -> tuple[DirtyEntry, ...]:
        rows = self._store.connection.execute(
            "SELECT mission_id,subject_kind,subject_id,epoch,scope_id,reason,state"
            " FROM validity_dirty WHERE mission_id = ? AND state = ?"
            " ORDER BY created_at, subject_id",
            (
                identifier(mission_id, "mission_id"),
                self._state(state, DIRTY_STATES, "dirty state"),
            ),
        ).fetchall()
        return tuple(
            DirtyEntry(
                mission_id=str(row[0]),
                subject_kind=str(row[1]),
                subject_id=str(row[2]),
                epoch=int(row[3]),
                scope_id=str(row[4]),
                reason=str(row[5]),
                state=str(row[6]),
            )
            for row in rows
        )

    # ================================================================== operations
    def bind_operation(
        self, envelope: OperationEnvelope, *, principal_id: str, scope_id: str | None = None
    ) -> OperationEnvelope:
        """Freeze one operation's semantics against a principal and scope (AER §12.2).

        ``OperationId`` carries exactly one ``request_hash``: a second envelope with
        the same id and a different request is ``OPERATION_PAYLOAD_CONFLICT`` and is
        refused by the foreign key, not merely by a check this method could forget.
        """

        if not isinstance(envelope, OperationEnvelope):
            raise StoreConflict("bind_operation expects an OperationEnvelope")
        principal = identifier(principal_id, "principal_id")
        scope = identifier(scope_id, "scope_id") if scope_id is not None else envelope.scope_id
        document = envelope.to_json()
        digest = content_hash_of(document)
        now = self._store.now
        with self._store.transaction() as connection:
            stored = connection.execute(
                "SELECT request_hash, envelope_hash FROM operation_identities WHERE operation_id = ?",
                (str(envelope.operation_id),),
            ).fetchone()
            if stored is None:
                self._execute(
                    connection,
                    "INSERT INTO operation_identities(operation_id,mission_id,request_hash,"
                    "envelope_hash,envelope_json,created_at) VALUES (?,?,?,?,?,?)",
                    (
                        str(envelope.operation_id),
                        envelope.mission_id,
                        envelope.request_hash,
                        digest,
                        canonical_json(document),
                        now,
                    ),
                    f"operation {envelope.operation_id!s} identity could not be stored",
                )
            elif stored["request_hash"] != envelope.request_hash:
                raise StoreConflict(
                    f"OPERATION_PAYLOAD_CONFLICT: operation {envelope.operation_id!s} is bound to "
                    f"request {stored['request_hash']}, not {envelope.request_hash}"
                )
            self._execute(
                connection,
                "INSERT INTO operation_bindings(principal_id,scope_id,operation_occurrence_id,"
                "operation_id,request_hash,mission_id,obligation_id,connector_id,connector_version,"
                "operation_kind,envelope_hash,envelope_json,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    principal,
                    scope,
                    str(envelope.operation_occurrence_id),
                    str(envelope.operation_id),
                    envelope.request_hash,
                    envelope.mission_id,
                    envelope.obligation_id,
                    envelope.connector_id,
                    envelope.connector_version,
                    str(envelope.operation_kind),
                    digest,
                    canonical_json(document),
                    now,
                ),
                f"operation occurrence {envelope.operation_occurrence_id!s} is already bound",
            )
        return envelope

    def get_operation_binding(
        self, principal_id: str, scope_id: str, operation_occurrence_id: str
    ) -> OperationEnvelope:
        row = self._one(
            "SELECT envelope_json FROM operation_bindings WHERE principal_id = ?"
            " AND scope_id = ? AND operation_occurrence_id = ?",
            (
                identifier(principal_id, "principal_id"),
                identifier(scope_id, "scope_id"),
                identifier(operation_occurrence_id, "operation_occurrence_id"),
            ),
            f"no operation binding for {operation_occurrence_id}",
        )
        return OperationEnvelope.from_json(json.loads(row[0]))

    def list_operation_bindings(
        self, mission_id: str, *, operation_id: str | None = None
    ) -> tuple[OperationEnvelope, ...]:
        clauses = ["mission_id = ?"]
        values: list[Any] = [identifier(mission_id, "mission_id")]
        if operation_id is not None:
            clauses.append("operation_id = ?")
            values.append(identifier(operation_id, "operation_id"))
        rows = self._store.connection.execute(
            f"SELECT envelope_json FROM operation_bindings WHERE {' AND '.join(clauses)}"
            " ORDER BY created_at, operation_occurrence_id",
            tuple(values),
        ).fetchall()
        return tuple(OperationEnvelope.from_json(json.loads(row[0])) for row in rows)

    # ================================================================== read sets
    def record_read_set(self, mission_id: str, proposal_id: str, read_set: SemanticReadSet) -> str:
        """Index one proposal's semantic read-set, item by item (ADR-13).

        The whole read-set is kept in a ``read_set`` row so it round-trips exactly;
        the other rows are the per-subject index a revalidation reads.
        """

        if not isinstance(read_set, SemanticReadSet):
            raise StoreConflict("record_read_set expects a SemanticReadSet")
        mission = identifier(mission_id, "mission_id")
        proposal = identifier(proposal_id, "proposal_id")
        document = read_set.to_json()
        digest = content_hash_of(document)
        rows: list[tuple[str, str, int, str | None, str]] = [
            (
                "read_set",
                proposal,
                read_set.requirements_revision,
                digest,
                canonical_json(document),
            ),
            (
                "requirements",
                mission,
                read_set.requirements_revision,
                None,
                canonical_json({"requirements_revision": read_set.requirements_revision}),
            ),
        ]
        for group in (
            read_set.goal_revisions,
            read_set.method_revisions,
            read_set.observation_revisions,
            read_set.acceptance_revisions,
            read_set.obligation_revisions,
        ):
            for item in group:
                rows.append(
                    (
                        str(item.kind),
                        item.id,
                        item.semantic_revision,
                        item.content_hash,
                        canonical_json(item.to_json()),
                    )
                )
        for scope in read_set.scope_epochs:
            rows.append(
                (
                    "scope_epoch",
                    scope.scope_id,
                    scope.validity_epoch,
                    None,
                    canonical_json(scope.to_json()),
                )
            )
        for absence in read_set.absences:
            payload = absence.to_json()
            rows.append(
                (
                    "absence",
                    content_hash_of(payload),
                    absence.range_revision,
                    None,
                    canonical_json(payload),
                )
            )
        now = self._store.now
        with self._store.transaction() as connection:
            for subject_type, subject_id, revision, content_hash, item_json in rows:
                self._execute(
                    connection,
                    "INSERT INTO plan_read_sets(mission_id,proposal_id,subject_type,subject_id,"
                    "semantic_revision,content_hash,item_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        mission,
                        proposal,
                        subject_type,
                        subject_id,
                        revision,
                        content_hash,
                        item_json,
                        now,
                    ),
                    f"read-set item {subject_type}/{subject_id} appears twice in {proposal}",
                )
        return digest

    def get_read_set(self, mission_id: str, proposal_id: str) -> SemanticReadSet:
        row = self._one(
            "SELECT item_json FROM plan_read_sets WHERE mission_id = ? AND proposal_id = ?"
            " AND subject_type = 'read_set'",
            (identifier(mission_id, "mission_id"), identifier(proposal_id, "proposal_id")),
            f"no read-set recorded for proposal {proposal_id}",
        )
        return SemanticReadSet.from_json(json.loads(row[0]))

    def list_read_set_items(self, mission_id: str, proposal_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._store.connection.execute(
            "SELECT subject_type,subject_id,semantic_revision,content_hash,item_json"
            " FROM plan_read_sets WHERE mission_id = ? AND proposal_id = ?"
            " AND subject_type <> 'read_set' ORDER BY subject_type, subject_id",
            (identifier(mission_id, "mission_id"), identifier(proposal_id, "proposal_id")),
        ).fetchall()
        return tuple(
            {
                "subject_type": row[0],
                "subject_id": row[1],
                "semantic_revision": row[2],
                "content_hash": row[3],
                "item": json.loads(row[4]),
            }
            for row in rows
        )

    def read_set_consumers(
        self, mission_id: str, subject_type: str, subject_id: str
    ) -> tuple[str, ...]:
        """Which proposals read this subject — the reverse of :meth:`record_read_set`."""

        rows = self._store.connection.execute(
            "SELECT proposal_id FROM plan_read_sets WHERE mission_id = ? AND subject_type = ?"
            " AND subject_id = ? ORDER BY proposal_id",
            (
                identifier(mission_id, "mission_id"),
                identifier(subject_type, "subject_type"),
                identifier(subject_id, "subject_id"),
            ),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)
