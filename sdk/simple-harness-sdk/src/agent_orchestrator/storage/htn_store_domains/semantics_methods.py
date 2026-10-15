# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Task semantics, method registrations, method instances and child occurrences."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts.htn import (
    ChildBinding,
    MethodContract,
    MethodInstanceDraft,
    MethodRegistration,
    MethodRegistryStatus,
    TaskSemanticBindingV1,
)
from ...contracts.semantic_base import enum_of, identifier, index
from ..assurance_changes import original_source_mutation
from ..store import StoreConflict
from .core import HtnStoreCore
from .records import METHOD_INSTANCE_STATES, StoredMethod


class HtnSemanticsMethodMixin(HtnStoreCore):
    """Task semantics, method registrations, method instances and child occurrences."""

    __slots__ = ()

    # ================================================================== task semantics
    def put_task_semantics(self, mission_id: str, binding: TaskSemanticBindingV1) -> str:
        """Store one semantic binding.  ``(task_id, contract_revision)`` is its identity."""

        if not isinstance(binding, TaskSemanticBindingV1):
            raise StoreConflict("put_task_semantics expects a TaskSemanticBindingV1")
        mission = identifier(mission_id, "mission_id")
        digest = binding.content_hash()
        self._insert(
            "INSERT INTO task_semantics(task_id,binding_revision,mission_id,obligation_id,form,"
            "semantic_scope,goal_signature_id,operator_ref,adopted_method_instance_id,"
            "input_binding_revision,dispatch_generation,content_hash,binding_json,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(binding.task_id),
                int(binding.contract_revision),
                mission,
                str(binding.obligation_id),
                str(binding.form),
                binding.semantic_scope,
                binding.goal_signature.signature_id,
                None
                if binding.operator_ref is None
                else canonical_json(binding.operator_ref.to_json()),
                (
                    None
                    if binding.adopted_method_instance_id is None
                    else str(binding.adopted_method_instance_id)
                ),
                int(binding.input_binding_revision),
                int(binding.dispatch_generation),
                digest,
                canonical_json(binding.to_json()),
                self._store.now,
            ),
            f"task semantics {binding.task_id!s}@{binding.contract_revision} already stored",
        )
        return digest

    def get_task_semantics(self, task_id: str, binding_revision: int) -> TaskSemanticBindingV1:
        row = self._one(
            "SELECT binding_json FROM task_semantics WHERE task_id = ? AND binding_revision = ?",
            (identifier(task_id, "task_id"), index(binding_revision, "binding_revision")),
            f"no semantic binding for task {task_id}@{binding_revision}",
        )
        return TaskSemanticBindingV1.from_json(json.loads(row[0]))

    def latest_task_semantics(self, task_id: str) -> TaskSemanticBindingV1 | None:
        row = self._store.connection.execute(
            "SELECT binding_json FROM task_semantics WHERE task_id = ?"
            " ORDER BY binding_revision DESC LIMIT 1",
            (identifier(task_id, "task_id"),),
        ).fetchone()
        return None if row is None else TaskSemanticBindingV1.from_json(json.loads(row[0]))

    def task_semantics_of(self, mission_id: str, task_id: str) -> TaskSemanticBindingV1 | None:
        """The newest binding of one task *within one Mission*.

        This is the accessor a commit path wants: it keeps the Mission in the
        predicate, so nothing outside ``storage`` has to spell the table name in
        SQL of its own to read a task's meaning.
        """

        row = self._store.connection.execute(
            "SELECT binding_json FROM task_semantics WHERE mission_id = ? AND task_id = ?"
            " ORDER BY binding_revision DESC LIMIT 1",
            (identifier(mission_id, "mission_id"), identifier(task_id, "task_id")),
        ).fetchone()
        return None if row is None else TaskSemanticBindingV1.from_json(json.loads(row[0]))

    def list_task_semantics(
        self, mission_id: str, *, form: str | None = None
    ) -> tuple[TaskSemanticBindingV1, ...]:
        clauses = ["mission_id = ?"]
        values: list[Any] = [identifier(mission_id, "mission_id")]
        if form is not None:
            clauses.append("form = ?")
            values.append(str(form))
        rows = self._store.connection.execute(
            f"SELECT binding_json FROM task_semantics WHERE {' AND '.join(clauses)}"
            " ORDER BY task_id, binding_revision",
            tuple(values),
        ).fetchall()
        return tuple(TaskSemanticBindingV1.from_json(json.loads(row[0])) for row in rows)

    # ================================================================== method registry
    def register_method(
        self, contract: MethodContract, registration: MethodRegistration
    ) -> StoredMethod:
        """Store an immutable method definition with its registry record (§6.3, §7.3).

        Re-registering the same ``(method_id, version)`` with the same bytes returns
        what is stored; different bytes are a conflict, because a method definition
        is immutable and a second meaning would silently rewrite past decompositions.
        """

        if not isinstance(contract, MethodContract):
            raise StoreConflict("register_method expects a MethodContract")
        if not isinstance(registration, MethodRegistration):
            raise StoreConflict("register_method expects a MethodRegistration")
        reference = contract.method_ref()
        if (
            registration.method_ref.method_id != reference.method_id
            or registration.method_ref.version != reference.version
            or registration.method_ref.content_hash != reference.content_hash
        ):
            raise StoreConflict(
                "the registration does not describe this method definition "
                f"({registration.method_ref.to_json()} vs {reference.to_json()})"
            )
        stored = self._method_row(reference.method_id, reference.version)
        if stored is not None:
            if stored["content_hash"] != reference.content_hash:
                raise StoreConflict(
                    f"method {reference.method_id}@{reference.version} is already stored "
                    "with different content; a changed definition needs a new version"
                )
            return self._stored_method(stored)
        now = self._store.now
        with original_source_mutation(self._store, writer="HtnStore.register_method"):
            self._insert(
                "INSERT INTO method_contracts(method_id,method_version,content_hash,registry_status,"
                "author,trial_scope_mission,registration_json,contract_json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    reference.method_id,
                    reference.version,
                    reference.content_hash,
                    str(registration.status),
                    str(registration.author),
                    registration.trial_scope_mission,
                    canonical_json(registration.to_json()),
                    canonical_json(contract.to_json()),
                    now,
                    now,
                ),
                f"method {reference.method_id}@{reference.version} already stored",
            )
        return StoredMethod(contract=contract, registration=registration)

    def set_method_registration(self, registration: MethodRegistration) -> StoredMethod:
        """Promote / suspend a stored method.  The definition itself never changes."""

        if not isinstance(registration, MethodRegistration):
            raise StoreConflict("set_method_registration expects a MethodRegistration")
        reference = registration.method_ref
        stored = self._method_row(reference.method_id, reference.version)
        if stored is None:
            raise StoreConflict(f"method {reference.method_id}@{reference.version} is not stored")
        if stored["content_hash"] != reference.content_hash:
            raise StoreConflict(
                f"method {reference.method_id}@{reference.version} has a different content hash"
            )
        with (
            original_source_mutation(self._store, writer="HtnStore.set_method_registration"),
            self._store.transaction() as connection,
        ):
            try:
                connection.execute(
                    "UPDATE method_contracts SET registry_status = ?, author = ?,"
                    " trial_scope_mission = ?, registration_json = ?, updated_at = ?"
                    " WHERE method_id = ? AND method_version = ?",
                    (
                        str(registration.status),
                        str(registration.author),
                        registration.trial_scope_mission,
                        canonical_json(registration.to_json()),
                        self._store.now,
                        reference.method_id,
                        reference.version,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StoreConflict(f"registry status rejected: {error}") from error
        return self.get_method(reference.method_id, reference.version)

    def get_method(self, method_id: str, method_version: int) -> StoredMethod:
        row = self._method_row(
            identifier(method_id, "method_id"), index(method_version, "method_version", minimum=1)
        )
        if row is None:
            raise StoreConflict(f"method {method_id}@{method_version} is not stored")
        return self._stored_method(row)

    def list_methods(
        self, *, status: MethodRegistryStatus | None = None
    ) -> tuple[StoredMethod, ...]:
        clauses: list[str] = []
        values: list[Any] = []
        if status is not None:
            clauses.append("registry_status = ?")
            values.append(str(enum_of(MethodRegistryStatus, status, "status")))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._store.connection.execute(
            f"SELECT * FROM method_contracts{where} ORDER BY method_id, method_version",
            tuple(values),
        ).fetchall()
        return tuple(self._stored_method(row) for row in rows)

    # ================================================================== method instances
    def insert_method_instance(
        self, mission_id: str, draft: MethodInstanceDraft, *, state: str = "DRAFT"
    ) -> MethodInstanceDraft:
        """Store one grounding and, in the same transaction, its child occurrences."""

        if not isinstance(draft, MethodInstanceDraft):
            raise StoreConflict("insert_method_instance expects a MethodInstanceDraft")
        mission = identifier(mission_id, "mission_id")
        lifecycle = self._state(state, METHOD_INSTANCE_STATES, "method instance state")
        now = self._store.now
        with self._store.transaction() as connection:
            self._execute(
                connection,
                "INSERT INTO method_instances(mission_id,instance_id,goal_task_id,"
                "goal_occurrence_id,obligation_id,method_id,method_version,method_content_hash,"
                "plan_revision,state,parameters_digest,draft_json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mission,
                    str(draft.instance_id),
                    str(draft.goal_id),
                    str(draft.effective_goal_occurrence_id),
                    str(draft.obligation_id),
                    draft.method_ref.method_id,
                    draft.method_ref.version,
                    draft.method_ref.content_hash,
                    int(draft.plan_revision),
                    lifecycle,
                    draft.parameters_digest(),
                    canonical_json(draft.to_json()),
                    now,
                    now,
                ),
                f"method instance {draft.instance_id!s} already stored in {mission}",
            )
            for binding in draft.child_bindings:
                self._insert_child_occurrence(connection, mission, binding, now)
        return draft

    def get_method_instance(self, mission_id: str, instance_id: str) -> MethodInstanceDraft:
        row = self._one(
            "SELECT draft_json FROM method_instances WHERE mission_id = ? AND instance_id = ?",
            (identifier(mission_id, "mission_id"), identifier(instance_id, "instance_id")),
            f"no method instance {instance_id} in {mission_id}",
        )
        return MethodInstanceDraft.from_json(json.loads(row[0]))

    def method_instance_state(self, mission_id: str, instance_id: str) -> str:
        row = self._one(
            "SELECT state FROM method_instances WHERE mission_id = ? AND instance_id = ?",
            (identifier(mission_id, "mission_id"), identifier(instance_id, "instance_id")),
            f"no method instance {instance_id} in {mission_id}",
        )
        return str(row[0])

    def set_method_instance_state(self, mission_id: str, instance_id: str, state: str) -> str:
        lifecycle = self._state(state, METHOD_INSTANCE_STATES, "method instance state")
        mission = identifier(mission_id, "mission_id")
        instance = identifier(instance_id, "instance_id")
        self.method_instance_state(mission, instance)
        with self._store.transaction() as connection:
            connection.execute(
                "UPDATE method_instances SET state = ?, updated_at = ?"
                " WHERE mission_id = ? AND instance_id = ?",
                (lifecycle, self._store.now, mission, instance),
            )
        return lifecycle

    def list_method_instances(
        self, mission_id: str, *, plan_revision: int | None = None, state: str | None = None
    ) -> tuple[MethodInstanceDraft, ...]:
        clauses = ["mission_id = ?"]
        values: list[Any] = [identifier(mission_id, "mission_id")]
        if plan_revision is not None:
            clauses.append("plan_revision = ?")
            values.append(index(plan_revision, "plan_revision"))
        if state is not None:
            clauses.append("state = ?")
            values.append(self._state(state, METHOD_INSTANCE_STATES, "method instance state"))
        rows = self._store.connection.execute(
            f"SELECT draft_json FROM method_instances WHERE {' AND '.join(clauses)}"
            " ORDER BY created_at, instance_id",
            tuple(values),
        ).fetchall()
        return tuple(MethodInstanceDraft.from_json(json.loads(row[0])) for row in rows)

    def list_child_occurrences(self, mission_id: str, instance_id: str) -> tuple[ChildBinding, ...]:
        rows = self._store.connection.execute(
            "SELECT binding_json FROM method_child_occurrences WHERE mission_id = ?"
            " AND instance_id = ? ORDER BY slot_key",
            (identifier(mission_id, "mission_id"), identifier(instance_id, "instance_id")),
        ).fetchall()
        return tuple(ChildBinding.from_json(json.loads(row[0])) for row in rows)

    def occurrence_consumers(
        self, mission_id: str, goal_occurrence_id: str
    ) -> tuple[ChildBinding, ...]:
        """Every slot that adopted this goal occurrence — the shared-goal reverse index."""

        rows = self._store.connection.execute(
            "SELECT binding_json FROM method_child_occurrences WHERE mission_id = ?"
            " AND goal_occurrence_id = ? ORDER BY instance_id, slot_key",
            (
                identifier(mission_id, "mission_id"),
                identifier(goal_occurrence_id, "goal_occurrence_id"),
            ),
        ).fetchall()
        return tuple(ChildBinding.from_json(json.loads(row[0])) for row in rows)

    # ================================================================== internals
    def _insert_child_occurrence(
        self, connection: sqlite3.Connection, mission_id: str, binding: ChildBinding, now: float
    ) -> None:
        goal_occurrence = (
            binding.occurrence_id
            if binding.goal_occurrence_id is None
            else binding.goal_occurrence_id
        )
        self._execute(
            connection,
            "INSERT INTO method_child_occurrences(instance_id,slot_key,mission_id,occurrence_id,"
            "obligation_id,goal_occurrence_id,requiredness,reuse_policy,binding_json,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(binding.instance_id),
                binding.slot_key,
                mission_id,
                str(binding.occurrence_id),
                str(binding.obligation_id),
                str(goal_occurrence),
                str(binding.requiredness),
                str(binding.reuse_policy),
                canonical_json(binding.to_json()),
                now,
            ),
            f"slot {binding.slot_key} of instance {binding.instance_id!s} is already bound",
        )

    def _method_row(self, method_id: str, method_version: int) -> sqlite3.Row | None:
        return self._store.connection.execute(
            "SELECT * FROM method_contracts WHERE method_id = ? AND method_version = ?",
            (method_id, method_version),
        ).fetchone()

    @staticmethod
    def _stored_method(row: sqlite3.Row) -> StoredMethod:
        """Both halves come back through their contract codec.

        Re-deriving the registration from the indexed columns would skip the
        admission rules (§7.3: a model may not submit past DRAFT, TRIAL_ADMITTED is
        mission-scoped), which is exactly what a hand-edited row would exploit.
        """

        return StoredMethod(
            contract=MethodContract.from_json(json.loads(row["contract_json"])),
            registration=MethodRegistration.from_json(json.loads(row["registration_json"])),
        )
