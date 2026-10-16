# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Operations, phases, validation results, publish and refresh intents."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from ..contracts import JsonValue, canonical_json
from ..refresh_contracts import (
    CapabilityOperationReceipt,
    CapabilityRefreshCommit,
    CapabilityRefreshIntent,
    refresh_intent_from_dict,
)
from .core import CapabilityStoreCore
from .support import (
    CapabilityOperationRecord,
    CapabilityPublishIntent,
    CapabilityStoreConflict,
    CapabilityStoreError,
    _json_array,
    _json_object,
)

CAPABILITY_OPERATION_PHASES: tuple[str, ...] = (
    "planned",
    "staged",
    "verified",
    "environment_ready",
    "candidate_ready",
    "publish_intent",
    "catalog_swapped",
    "bound",
    "published",
)
BATCH_CAPABILITY_OPERATION_PHASES: tuple[str, ...] = (
    "batch_staged",
    "batch_prepared",
    "batch_publish_intent",
    "batch_files_materialized",
    "batch_catalog_swapped",
    "batch_committed",
)
ALL_CAPABILITY_OPERATION_PHASES = CAPABILITY_OPERATION_PHASES + BATCH_CAPABILITY_OPERATION_PHASES


def _operation_identity_request_json(request: Mapping[str, JsonValue]) -> str:
    """Canonical request JSON as used for operation-identity comparison.

    WBUI-DEF-S08-02: builtin (first-party) packs ship inside the app install
    directory, so their `source.uri` is an absolute path that changes whenever
    the app runs from a different location (dev worktree vs installed bundle,
    moved .app, user-data dir carried to another machine).  The idempotency
    key is a content hash (id+version+manifest hash) and deliberately ignores
    the path — but the stored request used to participate verbatim, so the
    same logical install replayed from a new install path raised
    operation_idempotency_conflict inside lifespan and the backend never
    bound its port.  Align the two: for builtin sources the uri is a runtime
    resolution detail, not identity.  Non-builtin sources (git, archives…)
    keep the uri — there it IS the identity of what was installed.
    """
    normalized = dict(request)
    source = normalized.get("source")
    if isinstance(source, Mapping) and source.get("type") == "builtin":
        normalized["source"] = {**source, "uri": None}
    return canonical_json(normalized)


def _operation_from_row(row: Mapping[str, Any]) -> CapabilityOperationRecord:
    request = _json_object(str(row["request_json"]))
    if request is None:  # pragma: no cover - NOT NULL in schema
        raise CapabilityStoreError("corrupt_operation", "request cannot be null")
    return CapabilityOperationRecord(
        operation_id=str(row["operation_id"]),
        idempotency_key=str(row["idempotency_key"]),
        root_run_id=None if row["root_run_id"] is None else str(row["root_run_id"]),
        kind=str(row["kind"]),
        pack_id=None if row["pack_id"] is None else str(row["pack_id"]),
        requested_scope=(None if row["requested_scope"] is None else str(row["requested_scope"])),
        requested_scope_key=(
            None if row["requested_scope_key"] is None else str(row["requested_scope_key"])
        ),
        phase=str(row["phase"]),
        status=str(row["status"]),
        request=request,
        error=_json_object(None if row["error_json"] is None else str(row["error_json"])),
        started_at=float(row["started_at"]),
        updated_at=float(row["updated_at"]),
        ended_at=None if row["ended_at"] is None else float(row["ended_at"]),
    )


def _publish_intent_from_row(row: Mapping[str, Any]) -> CapabilityPublishIntent:
    old_specs = _json_array(str(row["old_specs_json"]))
    new_specs = _json_array(str(row["new_specs_json"]))
    old_binding = _json_object(
        None if row["old_binding_json"] is None else str(row["old_binding_json"])
    )
    new_binding = _json_object(str(row["new_binding_json"]))
    if new_binding is None:  # pragma: no cover - NOT NULL
        raise CapabilityStoreError("corrupt_publish_intent", "new binding is missing")
    return CapabilityPublishIntent(
        intent_id=str(row["intent_id"]),
        operation_id=str(row["operation_id"]),
        expected_registry_revision=int(row["expected_registry_revision"]),
        old_specs=tuple(dict(item) for item in old_specs),
        new_specs=tuple(dict(item) for item in new_specs),
        old_binding=old_binding,
        new_binding=new_binding,
        phase=str(row["phase"]),
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _operation_receipt_from_row(
    row: Mapping[str, Any],
) -> CapabilityOperationReceipt:
    value = json.loads(str(row["receipt_json"]))
    if not isinstance(value, dict):
        raise CapabilityStoreError(
            "corrupt_operation_receipt", "operation receipt is not an object"
        )
    return CapabilityOperationReceipt.from_dict(value)


def _refresh_intent_from_row(
    row: Mapping[str, Any],
) -> CapabilityRefreshIntent:
    value = json.loads(str(row["intent_json"]))
    if not isinstance(value, dict):
        raise CapabilityStoreError("corrupt_refresh_intent", "refresh intent is not an object")
    value["status"] = str(row["status"])
    value["commit_hash"] = None if row["commit_hash"] is None else str(row["commit_hash"])
    if row["commit_json"] is None:
        value["commit"] = None
    else:
        commit = json.loads(str(row["commit_json"]))
        if not isinstance(commit, dict):
            raise CapabilityStoreError("corrupt_refresh_intent", "refresh commit is not an object")
        value["commit"] = commit
    return refresh_intent_from_dict(value)


class OperationMixin(CapabilityStoreCore):
    """Operations, phases, validation results, publish and refresh intents."""

    __slots__ = ()

    async def _create_operation_tx(
        self,
        db: aiosqlite.Connection,
        *,
        operation_id: str,
        idempotency_key: str,
        kind: str,
        request: Mapping[str, JsonValue],
        root_run_id: str | None = None,
        pack_id: str | None = None,
        requested_scope: str | None = None,
        requested_scope_key: str | None = None,
    ) -> CapabilityOperationRecord:
        if kind not in {
            "activate",
            "install",
            "update",
            "build",
            "repair",
            "rollback",
            "uninstall",
            "skill_install_batch",
        }:
            raise CapabilityStoreError("invalid_operation_kind", f"unknown operation kind: {kind}")
        request_json = canonical_json(dict(request))
        existing = await (
            await db.execute(
                """SELECT * FROM capability_operations
                   WHERE operation_id=? OR idempotency_key=?""",
                (operation_id, idempotency_key),
            )
        ).fetchone()
        if existing is not None:
            record = _operation_from_row(existing)
            # Install requests may discover their pack_id only after the
            # staged manifest validates.  Binding that discovered identity to
            # the durable operation must not make the original request cease
            # to be idempotent when it intentionally supplied pack_id=None.
            pack_identity_matches = record.pack_id == pack_id or (
                pack_id is None and record.request.get("expected_pack_id") is None
            )
            if (
                record.operation_id != operation_id
                or record.idempotency_key != idempotency_key
                or record.kind != kind
                or _operation_identity_request_json(record.request)
                != _operation_identity_request_json(request)
                or record.root_run_id != root_run_id
                or not pack_identity_matches
                or record.requested_scope != requested_scope
                or record.requested_scope_key != requested_scope_key
            ):
                raise CapabilityStoreConflict(
                    "operation_idempotency_conflict",
                    "operation identity already belongs to another request",
                )
            return record
        now = self._clock()
        initial_phase = "batch_staged" if kind == "skill_install_batch" else "planned"
        await db.execute(
            """INSERT INTO capability_operations(
                operation_id,idempotency_key,root_run_id,kind,pack_id,
                requested_scope,requested_scope_key,phase,status,request_json,
                error_json,started_at,updated_at,ended_at
            ) VALUES(?,?,?,?,?,?,?,?, 'running',?,NULL,?,?,NULL)""",
            (
                operation_id,
                idempotency_key,
                root_run_id,
                kind,
                pack_id,
                requested_scope,
                requested_scope_key,
                initial_phase,
                request_json,
                now,
                now,
            ),
        )
        row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        return _operation_from_row(row)

    async def create_operation(self, **kwargs: Any) -> CapabilityOperationRecord:
        async with self.write_transaction() as db:
            return await self._create_operation_tx(db, **kwargs)

    async def get_operation(self, operation_id: str) -> CapabilityOperationRecord | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM capability_operations WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            return None if row is None else _operation_from_row(row)

    async def _bind_operation_pack_id_tx(
        self, db: aiosqlite.Connection, operation_id: str, pack_id: str
    ) -> CapabilityOperationRecord:
        row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("operation_not_found", operation_id)
        operation = _operation_from_row(row)
        if operation.pack_id is not None and operation.pack_id != pack_id:
            raise CapabilityStoreConflict(
                "operation_pack_conflict",
                "operation is already bound to another capability",
            )
        if operation.pack_id is None:
            await db.execute(
                """UPDATE capability_operations SET pack_id=?,updated_at=?
                   WHERE operation_id=? AND pack_id IS NULL""",
                (pack_id, self._clock(), operation_id),
            )
        current = await self.get_operation_from_db(db, operation_id)
        if current is None:  # pragma: no cover
            raise CapabilityStoreError("operation_not_found", operation_id)
        return current

    async def bind_operation_pack_id(
        self, operation_id: str, pack_id: str
    ) -> CapabilityOperationRecord:
        async with self.write_transaction() as db:
            return await self._bind_operation_pack_id_tx(db, operation_id, pack_id)

    async def get_phase_evidence(
        self, operation_id: str, phase: str
    ) -> Mapping[str, JsonValue] | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT status,evidence_json
                       FROM capability_operation_phase_evidence
                       WHERE operation_id=? AND phase=?""",
                    (operation_id, phase),
                )
            ).fetchone()
            if row is None or str(row["status"]) != "committed":
                return None
            value = _json_object(str(row["evidence_json"]))
            return value

    async def _record_phase_intent_tx(
        self, db: aiosqlite.Connection, operation_id: str, phase: str
    ) -> CapabilityOperationRecord:
        if phase not in ALL_CAPABILITY_OPERATION_PHASES:
            raise CapabilityStoreError(
                "invalid_operation_phase", f"unknown operation phase: {phase}"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("operation_not_found", operation_id)
        operation = _operation_from_row(row)
        phase_order = (
            BATCH_CAPABILITY_OPERATION_PHASES
            if operation.kind == "skill_install_batch"
            else CAPABILITY_OPERATION_PHASES
        )
        if operation.phase not in phase_order or phase not in phase_order:
            raise CapabilityStoreConflict(
                "invalid_phase_transition", "operation phase graph differs"
            )
        current_index = phase_order.index(operation.phase)
        target_index = phase_order.index(phase)
        if target_index not in {current_index, current_index + 1}:
            raise CapabilityStoreConflict(
                "invalid_phase_transition",
                f"cannot advance {operation.phase} to {phase}",
            )
        now = self._clock()
        stable_key = f"{operation_id}:{phase}"
        evidence_row = await (
            await db.execute(
                """SELECT * FROM capability_operation_phase_evidence
                   WHERE operation_id=? AND phase=?""",
                (operation_id, phase),
            )
        ).fetchone()
        if evidence_row is None:
            await db.execute(
                """INSERT INTO capability_operation_phase_evidence(
                    operation_id,phase,idempotency_key,status,evidence_json,
                    created_at,updated_at
                ) VALUES(?,?,?,'intent','{}',?,?)""",
                (operation_id, phase, stable_key, now, now),
            )
        elif str(evidence_row["idempotency_key"]) != stable_key:
            raise CapabilityStoreConflict(
                "phase_idempotency_conflict", "phase intent identity changed"
            )
        return operation

    async def record_phase_intent(self, operation_id: str, phase: str) -> CapabilityOperationRecord:
        async with self.write_transaction() as db:
            return await self._record_phase_intent_tx(db, operation_id, phase)

    async def _commit_phase_tx(
        self,
        db: aiosqlite.Connection,
        operation_id: str,
        phase: str,
        *,
        evidence: Mapping[str, JsonValue] | None,
    ) -> CapabilityOperationRecord:
        operation = await self._record_phase_intent_tx(db, operation_id, phase)
        now = self._clock()
        evidence_json = canonical_json(dict(evidence or {}))
        evidence_row = await (
            await db.execute(
                """SELECT * FROM capability_operation_phase_evidence
                   WHERE operation_id=? AND phase=?""",
                (operation_id, phase),
            )
        ).fetchone()
        if str(evidence_row["status"]) == "committed":
            if str(evidence_row["evidence_json"]) != evidence_json:
                raise CapabilityStoreConflict(
                    "phase_evidence_conflict",
                    "committed phase evidence cannot change",
                )
            current = await self.get_operation_from_db(db, operation_id)
            if current is None:  # pragma: no cover
                raise CapabilityStoreError("operation_not_found", operation_id)
            return current
        await db.execute(
            """UPDATE capability_operation_phase_evidence
               SET status='committed',evidence_json=?,updated_at=?
               WHERE operation_id=? AND phase=?""",
            (evidence_json, now, operation_id, phase),
        )
        terminal = phase in {"published", "batch_committed"}
        cursor = await db.execute(
            """UPDATE capability_operations
                SET phase=?,status=?,error_json=NULL,updated_at=?,ended_at=?
                WHERE operation_id=? AND phase=?
                  AND status IN ('running','unknown')""",
            (
                phase,
                "succeeded" if terminal else "running",
                now,
                now if terminal else None,
                operation_id,
                operation.phase,
            ),
        )
        if cursor.rowcount != 1 and operation.phase != phase:
            raise CapabilityStoreConflict("operation_phase_conflict", "operation phase changed")
        current = await self.get_operation_from_db(db, operation_id)
        if current is None:  # pragma: no cover
            raise CapabilityStoreError("operation_not_found", operation_id)
        return current

    async def commit_phase(
        self,
        operation_id: str,
        phase: str,
        *,
        evidence: Mapping[str, JsonValue] | None = None,
    ) -> CapabilityOperationRecord:
        async with self.write_transaction() as db:
            return await self._commit_phase_tx(db, operation_id, phase, evidence=evidence)

    async def get_operation_from_db(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> CapabilityOperationRecord | None:
        row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (operation_id,),
            )
        ).fetchone()
        return None if row is None else _operation_from_row(row)

    async def fail_operation(
        self,
        operation_id: str,
        *,
        status: str = "failed",
        error: Mapping[str, JsonValue],
    ) -> CapabilityOperationRecord:
        if status not in {"failed", "cancelled", "unknown"}:
            raise CapabilityStoreError(
                "invalid_operation_status", f"invalid failure status: {status}"
            )
        async with self.write_transaction() as db:
            now = self._clock()
            cursor = await db.execute(
                """UPDATE capability_operations SET
                    status=?,error_json=?,updated_at=?,ended_at=?
                   WHERE operation_id=? AND status IN ('running','unknown')""",
                (
                    status,
                    canonical_json(dict(error)),
                    now,
                    None if status == "unknown" else now,
                    operation_id,
                ),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM capability_operations WHERE operation_id=?",
                    (operation_id,),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError("operation_not_found", operation_id)
            record = _operation_from_row(row)
            if cursor.rowcount != 1 and (
                record.status != status
                or canonical_json(dict(record.error or {})) != canonical_json(dict(error))
            ):
                raise CapabilityStoreConflict(
                    "operation_terminal_conflict",
                    "operation already ended differently",
                )
            return record

    async def list_recoverable_operations(
        self,
    ) -> tuple[CapabilityOperationRecord, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM capability_operations
                       WHERE status IN ('running','unknown')
                       ORDER BY started_at,operation_id"""
                )
            ).fetchall()
            return tuple(_operation_from_row(row) for row in rows)

    async def list_operations(self, *, limit: int = 100) -> tuple[CapabilityOperationRecord, ...]:
        bounded = max(1, min(int(limit), 500))
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM capability_operations
                       ORDER BY updated_at DESC,operation_id DESC LIMIT ?""",
                    (bounded,),
                )
            ).fetchall()
            return tuple(_operation_from_row(row) for row in rows)

    async def list_validation_results(
        self, operation_id: str
    ) -> tuple[Mapping[str, JsonValue], ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT check_name,status,evidence_ref,created_at
                       FROM capability_validation_results
                       WHERE operation_id=?
                       ORDER BY created_at,check_name""",
                    (operation_id,),
                )
            ).fetchall()
            return tuple(
                {
                    "check_name": str(row["check_name"]),
                    "status": str(row["status"]),
                    "evidence_ref": (
                        None if row["evidence_ref"] is None else str(row["evidence_ref"])
                    ),
                    "created_at": float(row["created_at"]),
                }
                for row in rows
            )

    async def record_validation_result(
        self,
        operation_id: str,
        check_name: str,
        *,
        status: str,
        detail: Mapping[str, JsonValue] | None = None,
        evidence_ref: str | None = None,
    ) -> None:
        if status not in {"passed", "failed", "skipped", "unknown"}:
            raise CapabilityStoreError(
                "invalid_validation_result", f"unknown result status: {status}"
            )
        detail_json = canonical_json(dict(detail or {}))
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_validation_results
                       WHERE operation_id=? AND check_name=?""",
                    (operation_id, check_name),
                )
            ).fetchone()
            if row is not None:
                if (
                    str(row["status"]) != status
                    or str(row["detail_json"]) != detail_json
                    or (None if row["evidence_ref"] is None else str(row["evidence_ref"]))
                    != evidence_ref
                ):
                    raise CapabilityStoreConflict(
                        "validation_result_conflict",
                        "validation result cannot change",
                    )
                return
            await db.execute(
                """INSERT INTO capability_validation_results(
                    operation_id,check_name,status,evidence_ref,detail_json,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    operation_id,
                    check_name,
                    status,
                    evidence_ref,
                    detail_json,
                    self._clock(),
                ),
            )

    async def _create_publish_intent_tx(
        self,
        db: aiosqlite.Connection,
        *,
        intent_id: str,
        operation_id: str,
        expected_registry_revision: int,
        old_specs: Sequence[Mapping[str, JsonValue]],
        new_specs: Sequence[Mapping[str, JsonValue]],
        old_binding: Mapping[str, JsonValue] | None,
        new_binding: Mapping[str, JsonValue],
    ) -> CapabilityPublishIntent:
        old_specs_json = canonical_json([dict(item) for item in old_specs])
        new_specs_json = canonical_json([dict(item) for item in new_specs])
        old_binding_json = None if old_binding is None else canonical_json(dict(old_binding))
        new_binding_json = canonical_json(dict(new_binding))
        existing = await (
            await db.execute(
                """SELECT * FROM capability_publish_intents
                   WHERE intent_id=? OR operation_id=?""",
                (intent_id, operation_id),
            )
        ).fetchone()
        if existing is not None:
            current = _publish_intent_from_row(existing)
            if (
                current.intent_id != intent_id
                or current.operation_id != operation_id
                or current.expected_registry_revision != expected_registry_revision
                or canonical_json(list(current.old_specs)) != old_specs_json
                or canonical_json(list(current.new_specs)) != new_specs_json
                or (
                    None
                    if current.old_binding is None
                    else canonical_json(dict(current.old_binding))
                )
                != old_binding_json
                or canonical_json(dict(current.new_binding)) != new_binding_json
            ):
                raise CapabilityStoreConflict(
                    "publish_intent_conflict",
                    "publish intent identity already belongs to another swap",
                )
            return current
        now = self._clock()
        operation_row = await (
            await db.execute(
                "SELECT kind FROM capability_operations WHERE operation_id=?", (operation_id,)
            )
        ).fetchone()
        publish_phase = (
            "batch_publish_intent"
            if operation_row is not None and str(operation_row["kind"]) == "skill_install_batch"
            else "publish_intent"
        )
        await db.execute(
            """INSERT INTO capability_publish_intents(
                intent_id,operation_id,expected_registry_revision,old_specs_json,
                new_specs_json,old_binding_json,new_binding_json,phase,status,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?, 'pending',?,?)""",
            (
                intent_id,
                operation_id,
                expected_registry_revision,
                old_specs_json,
                new_specs_json,
                old_binding_json,
                new_binding_json,
                publish_phase,
                now,
                now,
            ),
        )
        await self._bump_generation_tx(db, catalog=True, binding=False)
        row = await (
            await db.execute(
                "SELECT * FROM capability_publish_intents WHERE intent_id=?",
                (intent_id,),
            )
        ).fetchone()
        return _publish_intent_from_row(row)

    async def create_publish_intent(self, **kwargs: Any) -> CapabilityPublishIntent:
        async with self.write_transaction() as db:
            return await self._create_publish_intent_tx(db, **kwargs)

    async def _advance_publish_intent_tx(
        self,
        db: aiosqlite.Connection,
        intent_id: str,
        *,
        phase: str,
        status: str,
    ) -> CapabilityPublishIntent:
        if phase not in {
            "publish_intent",
            "catalog_swapped",
            "bound",
            "batch_publish_intent",
            "batch_files_materialized",
            "batch_catalog_swapped",
            "batch_committed",
        }:
            raise CapabilityStoreError("invalid_publish_phase", f"unknown publish phase: {phase}")
        if status not in {"pending", "committed", "rolled_back", "unknown"}:
            raise CapabilityStoreError(
                "invalid_publish_status", f"unknown publish status: {status}"
            )
        row = await (
            await db.execute(
                "SELECT * FROM capability_publish_intents WHERE intent_id=?",
                (intent_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("publish_intent_not_found", intent_id)
        current = _publish_intent_from_row(row)
        order = (
            {
                "batch_publish_intent": 0,
                "batch_files_materialized": 1,
                "batch_catalog_swapped": 2,
                "batch_committed": 3,
            }
            if current.phase.startswith("batch_")
            else {"publish_intent": 0, "catalog_swapped": 1, "bound": 2}
        )
        if phase not in order:
            raise CapabilityStoreConflict("publish_phase_conflict", "publish phase graph differs")
        if order[phase] < order[current.phase] or order[phase] > order[current.phase] + 1:
            raise CapabilityStoreConflict(
                "publish_phase_conflict",
                f"cannot advance publish intent {current.phase} to {phase}",
            )
        if current.status in {"committed", "rolled_back"}:
            if current.status != status or current.phase != phase:
                raise CapabilityStoreConflict(
                    "publish_terminal_conflict", "publish intent is already terminal"
                )
            return current
        now = self._clock()
        await db.execute(
            """UPDATE capability_publish_intents
               SET phase=?,status=?,updated_at=? WHERE intent_id=?""",
            (phase, status, now, intent_id),
        )
        await self._bump_generation_tx(db, catalog=True, binding=False)
        row = await (
            await db.execute(
                "SELECT * FROM capability_publish_intents WHERE intent_id=?",
                (intent_id,),
            )
        ).fetchone()
        return _publish_intent_from_row(row)

    async def advance_publish_intent(
        self, intent_id: str, *, phase: str, status: str = "pending"
    ) -> CapabilityPublishIntent:
        async with self.write_transaction() as db:
            return await self._advance_publish_intent_tx(db, intent_id, phase=phase, status=status)

    async def get_publish_intent_for_operation(
        self, operation_id: str
    ) -> CapabilityPublishIntent | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_publish_intents
                       WHERE operation_id=?""",
                    (operation_id,),
                )
            ).fetchone()
            return None if row is None else _publish_intent_from_row(row)

    async def _put_operation_receipt_tx(
        self,
        db: aiosqlite.Connection,
        receipt: CapabilityOperationReceipt,
    ) -> CapabilityOperationReceipt:
        if not isinstance(receipt, CapabilityOperationReceipt):
            raise TypeError("receipt must be CapabilityOperationReceipt")
        operation_row = await (
            await db.execute(
                "SELECT * FROM capability_operations WHERE operation_id=?",
                (receipt.operation_id,),
            )
        ).fetchone()
        if operation_row is None:
            raise CapabilityStoreError("operation_not_found", receipt.operation_id)
        operation = _operation_from_row(operation_row)
        if operation.status != "succeeded" or operation.phase != "published":
            raise CapabilityStoreConflict(
                "operation_not_published",
                "only a completed published operation can issue a receipt",
            )
        if operation.kind != receipt.action:
            raise CapabilityStoreConflict(
                "operation_receipt_action_mismatch",
                "operation receipt action differs from the operation",
            )
        if operation.root_run_id != receipt.root_run_id:
            raise CapabilityStoreConflict(
                "operation_receipt_root_mismatch",
                "operation receipt root run differs from the operation",
            )
        existing = await (
            await db.execute(
                """SELECT * FROM capability_operation_receipts
                   WHERE operation_id=? OR receipt_hash=?""",
                (receipt.operation_id, receipt.operation_receipt_hash),
            )
        ).fetchall()
        if existing:
            if len(existing) != 1:
                raise CapabilityStoreConflict(
                    "operation_receipt_conflict",
                    "operation and receipt hash refer to different receipts",
                )
            current = _operation_receipt_from_row(existing[0])
            if current != receipt:
                raise CapabilityStoreConflict(
                    "operation_receipt_conflict",
                    "operation already has a different immutable receipt",
                )
            return current
        await db.execute(
            """INSERT INTO capability_operation_receipts(
                operation_id,receipt_hash,receipt_json,created_at
            ) VALUES(?,?,?,?)""",
            (
                receipt.operation_id,
                receipt.operation_receipt_hash,
                canonical_json(receipt.to_dict()),
                self._clock(),
            ),
        )
        return receipt

    async def put_operation_receipt(
        self, receipt: CapabilityOperationReceipt
    ) -> CapabilityOperationReceipt:
        async with self.write_transaction() as db:
            return await self._put_operation_receipt_tx(db, receipt)

    async def get_operation_receipt(self, operation_id: str) -> CapabilityOperationReceipt | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_operation_receipts
                       WHERE operation_id=?""",
                    (operation_id,),
                )
            ).fetchone()
            return None if row is None else _operation_receipt_from_row(row)

    async def _stage_refresh_intent_tx(
        self,
        db: aiosqlite.Connection,
        intent: CapabilityRefreshIntent,
    ) -> CapabilityRefreshIntent:
        if not isinstance(intent, CapabilityRefreshIntent):
            raise TypeError("intent must be CapabilityRefreshIntent")
        if intent.status != "pending":
            raise CapabilityStoreError(
                "invalid_refresh_status", "new refresh intent must be pending"
            )
        receipt_row = await (
            await db.execute(
                """SELECT * FROM capability_operation_receipts
                   WHERE operation_id=?""",
                (intent.operation_id,),
            )
        ).fetchone()
        if receipt_row is None:
            raise CapabilityStoreError("operation_receipt_not_found", intent.operation_id)
        receipt = _operation_receipt_from_row(receipt_row)
        if (
            receipt.root_run_id != intent.root_run_id
            or receipt.refresh_nonce != intent.refresh_nonce
            or receipt.old_stamp.fingerprint != intent.old_stamp.fingerprint
            or receipt.parent_command_id != intent.source_command_id
            or receipt.parent_effect_id != intent.source_effect_id
        ):
            raise CapabilityStoreConflict(
                "refresh_receipt_mismatch",
                "refresh intent is not bound to the published operation receipt",
            )
        existing_rows = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=? OR operation_id=? OR refresh_nonce=?""",
                (intent.intent_id, intent.operation_id, intent.refresh_nonce),
            )
        ).fetchall()
        if existing_rows:
            if len(existing_rows) != 1:
                raise CapabilityStoreConflict(
                    "refresh_intent_conflict",
                    "refresh identities refer to different intents",
                )
            current = _refresh_intent_from_row(existing_rows[0])
            if current != intent:
                raise CapabilityStoreConflict(
                    "refresh_intent_conflict",
                    "refresh intent identity already belongs to another payload",
                )
            return current
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_refresh_intents(
                intent_id,operation_id,refresh_nonce,root_run_id,run_id,
                source_kind,status,intent_json,commit_hash,commit_json,
                error_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,'pending',?,NULL,NULL,NULL,?,?)""",
            (
                intent.intent_id,
                intent.operation_id,
                intent.refresh_nonce,
                intent.root_run_id,
                intent.run_id,
                intent.source_kind,
                canonical_json(intent.to_dict()),
                now,
                now,
            ),
        )
        return intent

    async def get_refresh_intent(self, intent_id: str) -> CapabilityRefreshIntent | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_refresh_intents
                       WHERE intent_id=?""",
                    (intent_id,),
                )
            ).fetchone()
            return None if row is None else _refresh_intent_from_row(row)

    async def _commit_refresh_intent_tx(
        self,
        db: aiosqlite.Connection,
        commit: CapabilityRefreshCommit,
    ) -> CapabilityRefreshIntent:
        if not isinstance(commit, CapabilityRefreshCommit):
            raise TypeError("commit must be CapabilityRefreshCommit")
        row = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=?""",
                (commit.intent_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("refresh_intent_not_found", commit.intent_id)
        current = _refresh_intent_from_row(row)
        if current.status == "committed":
            if current.commit_hash != commit.fingerprint:
                raise CapabilityStoreConflict(
                    "refresh_commit_conflict",
                    "refresh nonce was committed with another payload",
                )
            return current
        if current.status != "pending":
            raise CapabilityStoreConflict(
                "refresh_intent_terminal", "refresh intent is already terminal"
            )
        identity = (
            current.intent_id,
            current.root_run_id,
            current.run_id,
            current.operation_id,
            current.refresh_nonce,
            current.expected_continuation_version,
            current.old_stamp.fingerprint,
            current.old_catalog_snapshot_ref,
            current.old_tool_set_snapshot_ref,
        )
        committed_identity = (
            commit.intent_id,
            commit.root_run_id,
            commit.run_id,
            commit.operation_id,
            commit.refresh_nonce,
            commit.expected_continuation_version,
            commit.old_stamp.fingerprint,
            commit.old_catalog_snapshot_ref,
            commit.old_tool_set_snapshot_ref,
        )
        if identity != committed_identity:
            raise CapabilityStoreConflict(
                "refresh_commit_conflict",
                "refresh commit does not match its pending intent",
            )
        receipt_row = await (
            await db.execute(
                """SELECT * FROM capability_operation_receipts
                   WHERE operation_id=?""",
                (commit.operation_id,),
            )
        ).fetchone()
        if receipt_row is None:
            raise CapabilityStoreError("operation_receipt_not_found", commit.operation_id)
        receipt = _operation_receipt_from_row(receipt_row)
        if receipt.affected_tool_spec_fingerprints != commit.affected_tool_spec_fingerprints:
            raise CapabilityStoreConflict(
                "refresh_commit_fingerprint_mismatch",
                "refresh commit differs from published ToolSpec evidence",
            )
        cursor = await db.execute(
            """UPDATE capability_refresh_intents
               SET status='committed',commit_hash=?,commit_json=?,updated_at=?
               WHERE intent_id=? AND status='pending'""",
            (
                commit.fingerprint,
                canonical_json(commit.to_dict()),
                self._clock(),
                commit.intent_id,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "refresh_commit_conflict", "refresh intent changed concurrently"
            )
        row = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=?""",
                (commit.intent_id,),
            )
        ).fetchone()
        return _refresh_intent_from_row(row)

    async def _fail_refresh_intent_tx(
        self,
        db: aiosqlite.Connection,
        intent_id: str,
        *,
        error: Mapping[str, JsonValue],
    ) -> CapabilityRefreshIntent:
        error_json = canonical_json(dict(error))
        row = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=?""",
                (intent_id,),
            )
        ).fetchone()
        if row is None:
            raise CapabilityStoreError("refresh_intent_not_found", intent_id)
        current = _refresh_intent_from_row(row)
        if current.status == "committed":
            raise CapabilityStoreConflict(
                "refresh_intent_terminal",
                "committed refresh intent cannot be failed",
            )
        if current.status == "failed":
            if str(row["error_json"]) != error_json:
                raise CapabilityStoreConflict(
                    "refresh_failure_conflict",
                    "refresh intent already failed differently",
                )
            return current
        await db.execute(
            """UPDATE capability_refresh_intents
               SET status='failed',error_json=?,updated_at=?
               WHERE intent_id=? AND status='pending'""",
            (error_json, self._clock(), intent_id),
        )
        row = await (
            await db.execute(
                """SELECT * FROM capability_refresh_intents
                   WHERE intent_id=?""",
                (intent_id,),
            )
        ).fetchone()
        return _refresh_intent_from_row(row)
