# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Runtime sets, runtime instances and visible entries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiosqlite

from ..contracts import (
    LEGACY_LOCAL_OWNER_KEY,
    CapabilityBinding,
    CapabilityCatalogEntry,
    CapabilityScope,
    CapabilityVersionDescriptor,
    JsonValue,
    fingerprint_json,
)
from .core import CapabilityStoreCore
from .support import (
    CapabilityRuntimePrepareIntent,
    CapabilityRuntimeSetRecord,
    CapabilityStoreConflict,
    CapabilityStoreError,
    CapabilityVersionRecord,
    _binding_from_row,
    _descriptor_from_json,
    _fetch_mappings,
    _json_array,
    _validated_digest,
    _version_from_row,
)


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeInstanceSpec:
    runtime_instance_id: str
    entry_id: str
    ordinal: int
    runtime_kind: str
    adapter_fingerprint: str
    argv_hash: str
    env_scope_hash: str
    workdir: str

    def __post_init__(self) -> None:
        if not self.runtime_instance_id or not self.entry_id or not self.workdir:
            raise ValueError("runtime instance identity and workdir are required")
        if not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("runtime instance ordinal must be non-negative")
        if self.runtime_kind not in {
            "dependency_probe",
            "tool_health",
            "mcp",
            "local_runtime",
        }:
            raise ValueError("invalid runtime kind")
        for name in ("adapter_fingerprint", "argv_hash", "env_scope_hash"):
            object.__setattr__(self, name, _validated_digest(getattr(self, name), name))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "runtime_instance_id": self.runtime_instance_id,
            "entry_id": self.entry_id,
            "ordinal": self.ordinal,
            "runtime_kind": self.runtime_kind,
            "adapter_fingerprint": self.adapter_fingerprint,
            "argv_hash": self.argv_hash,
            "env_scope_hash": self.env_scope_hash,
            "workdir": self.workdir,
        }


def _runtime_prepare_intent_from_row(
    row: Mapping[str, Any],
) -> CapabilityRuntimePrepareIntent:
    return CapabilityRuntimePrepareIntent(
        operation_id=str(row["operation_id"]),
        runtime_instance_id=str(row["runtime_instance_id"]),
        entry_id=str(row["entry_id"]),
        ordinal=int(row["ordinal"]),
        runtime_kind=str(row["runtime_kind"]),
        status=str(row["status"]),
        runtime_set_hash=str(row["runtime_set_hash"]),
        authorization_hash=(
            None if row["authorization_hash"] is None else str(row["authorization_hash"])
        ),
        launch_revocation_epoch=int(row["launch_revocation_epoch"]),
        adapter_fingerprint=str(row["adapter_fingerprint"]),
        argv_hash=str(row["argv_hash"]),
        env_scope_hash=str(row["env_scope_hash"]),
        workdir=str(row["workdir"]),
        job_identity=(None if row["job_identity"] is None else str(row["job_identity"])),
        pid=None if row["pid"] is None else int(row["pid"]),
        process_create_time=(
            None if row["process_create_time"] is None else float(row["process_create_time"])
        ),
        session_identity=(
            None if row["session_identity"] is None else str(row["session_identity"])
        ),
        start_outcome=(None if row["start_outcome"] is None else str(row["start_outcome"])),
        start_ack_at=(None if row["start_ack_at"] is None else float(row["start_ack_at"])),
        health_outcome_hash=(
            None if row["health_outcome_hash"] is None else str(row["health_outcome_hash"])
        ),
        started_at=(None if row["started_at"] is None else float(row["started_at"])),
        updated_at=float(row["updated_at"]),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
    )


def _runtime_set_from_rows(
    row: Mapping[str, Any],
    instances: Sequence[Mapping[str, Any]],
) -> CapabilityRuntimeSetRecord:
    return CapabilityRuntimeSetRecord(
        operation_id=str(row["operation_id"]),
        purpose=str(row["purpose"]),
        lifecycle_action=(
            None if row["lifecycle_action"] is None else str(row["lifecycle_action"])
        ),
        owner_activation_id=(
            None if row["owner_activation_id"] is None else str(row["owner_activation_id"])
        ),
        lease_activation_id=(
            None if row["lease_activation_id"] is None else str(row["lease_activation_id"])
        ),
        target_pack_id=(None if row["target_pack_id"] is None else str(row["target_pack_id"])),
        target_version=(None if row["target_version"] is None else str(row["target_version"])),
        owner_binding_set_stamp=(
            None if row["owner_binding_set_stamp"] is None else str(row["owner_binding_set_stamp"])
        ),
        run_catalog_content_stamp=(
            None
            if row["run_catalog_content_stamp"] is None
            else str(row["run_catalog_content_stamp"])
        ),
        target_package_hash=(
            None if row["target_package_hash"] is None else str(row["target_package_hash"])
        ),
        target_manifest_hash=(
            None if row["target_manifest_hash"] is None else str(row["target_manifest_hash"])
        ),
        runtime_set_hash=str(row["runtime_set_hash"]),
        expected_instance_count=int(row["expected_instance_count"]),
        authorization_hash=(
            None if row["authorization_hash"] is None else str(row["authorization_hash"])
        ),
        launch_revocation_epoch=int(row["launch_revocation_epoch"]),
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
        instances=tuple(_runtime_prepare_intent_from_row(item) for item in instances),
    )


class RuntimeSetMixin(CapabilityStoreCore):
    """Runtime sets, runtime instances and visible entries."""

    __slots__ = ()

    @staticmethod
    def _runtime_set_fingerprint(
        *,
        operation_id: str,
        purpose: str,
        lifecycle_action: str | None,
        owner_activation_id: str | None,
        lease_activation_id: str | None,
        target_pack_id: str | None,
        target_version: str | None,
        owner_binding_set_stamp: str | None,
        run_catalog_content_stamp: str | None,
        target_package_hash: str | None,
        target_manifest_hash: str | None,
        authorization_hash: str | None,
        launch_revocation_epoch: int,
        instances: Sequence[CapabilityRuntimeInstanceSpec],
    ) -> str:
        return fingerprint_json(
            {
                "domain": "capability-runtime-set-v1",
                "operation_id": operation_id,
                "purpose": purpose,
                "lifecycle_action": lifecycle_action,
                "owner_activation_id": owner_activation_id,
                "lease_activation_id": lease_activation_id,
                "target_pack_id": target_pack_id,
                "target_version": target_version,
                "owner_binding_set_stamp": owner_binding_set_stamp,
                "run_catalog_content_stamp": run_catalog_content_stamp,
                "target_package_hash": target_package_hash,
                "target_manifest_hash": target_manifest_hash,
                "authorization_hash": authorization_hash,
                "launch_revocation_epoch": launch_revocation_epoch,
                "instances": [
                    item.to_dict()
                    for item in sorted(instances, key=lambda item: (item.ordinal, item.entry_id))
                ],
            }
        )

    def _validate_runtime_set_integrity(
        self, record: CapabilityRuntimeSetRecord
    ) -> tuple[CapabilityRuntimeInstanceSpec, ...]:
        specs = tuple(
            CapabilityRuntimeInstanceSpec(
                runtime_instance_id=item.runtime_instance_id,
                entry_id=item.entry_id,
                ordinal=item.ordinal,
                runtime_kind=item.runtime_kind,
                adapter_fingerprint=item.adapter_fingerprint,
                argv_hash=item.argv_hash,
                env_scope_hash=item.env_scope_hash,
                workdir=item.workdir,
            )
            for item in record.instances
        )
        recomputed = self._runtime_set_fingerprint(
            operation_id=record.operation_id,
            purpose=record.purpose,
            lifecycle_action=record.lifecycle_action,
            owner_activation_id=record.owner_activation_id,
            lease_activation_id=record.lease_activation_id,
            target_pack_id=record.target_pack_id,
            target_version=record.target_version,
            owner_binding_set_stamp=record.owner_binding_set_stamp,
            run_catalog_content_stamp=record.run_catalog_content_stamp,
            target_package_hash=record.target_package_hash,
            target_manifest_hash=record.target_manifest_hash,
            authorization_hash=record.authorization_hash,
            launch_revocation_epoch=record.launch_revocation_epoch,
            instances=specs,
        )
        ordered_ordinals = tuple(item.ordinal for item in specs)
        if (
            len(specs) != record.expected_instance_count
            or ordered_ordinals != tuple(range(len(specs)))
            or len({item.entry_id for item in specs}) != len(specs)
            or len({item.runtime_instance_id for item in specs}) != len(specs)
            or recomputed != record.runtime_set_hash
            or any(item.runtime_set_hash != record.runtime_set_hash for item in record.instances)
        ):
            raise CapabilityStoreConflict(
                "runtime_set_incomplete",
                "runtime set header and members fail count/hash validation",
            )
        return specs

    async def _get_runtime_set_tx(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> CapabilityRuntimeSetRecord | None:
        headers = await _fetch_mappings(
            await db.execute(
                "SELECT * FROM capability_runtime_sets WHERE operation_id=?",
                (operation_id,),
            )
        )
        if not headers:
            return None
        members = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_prepare_intents
                   WHERE operation_id=?
                   ORDER BY ordinal,entry_id,runtime_instance_id""",
                (operation_id,),
            )
        )
        return _runtime_set_from_rows(headers[0], members)

    async def _prepare_runtime_set_tx(
        self,
        db: aiosqlite.Connection,
        *,
        operation_id: str,
        purpose: str,
        lifecycle_action: str | None,
        owner_activation_id: str | None,
        lease_activation_id: str | None,
        target_pack_id: str | None,
        target_version: str | None,
        owner_binding_set_stamp: str | None,
        run_catalog_content_stamp: str | None,
        target_package_hash: str | None,
        target_manifest_hash: str | None,
        authorization_hash: str | None,
        launch_revocation_epoch: int,
        instances: Sequence[CapabilityRuntimeInstanceSpec],
        runtime_set_hash: str | None = None,
    ) -> CapabilityRuntimeSetRecord:
        if not operation_id:
            raise CapabilityStoreError("invalid_runtime_set", "operation id is required")
        if not isinstance(launch_revocation_epoch, int) or launch_revocation_epoch < 0:
            raise CapabilityStoreError("invalid_runtime_set", "launch revocation epoch is invalid")
        if purpose == "mutation":
            valid_shape = (
                lifecycle_action is not None
                and owner_activation_id is None
                and lease_activation_id is None
                and owner_binding_set_stamp is not None
                and run_catalog_content_stamp is None
            )
        elif purpose == "owner_rehydrate":
            valid_shape = (
                lifecycle_action is None
                and owner_activation_id is not None
                and lease_activation_id is None
                and owner_binding_set_stamp is not None
                and run_catalog_content_stamp is None
            )
        elif purpose == "lease_rehydrate":
            valid_shape = (
                lifecycle_action is None
                and owner_activation_id is None
                and lease_activation_id is not None
                and owner_binding_set_stamp is None
                and run_catalog_content_stamp is not None
            )
        else:
            valid_shape = False
        if not valid_shape:
            raise CapabilityStoreError(
                "invalid_runtime_set",
                "runtime set purpose and authority references disagree",
            )
        for name, value in (
            ("owner_binding_set_stamp", owner_binding_set_stamp),
            ("run_catalog_content_stamp", run_catalog_content_stamp),
            ("target_package_hash", target_package_hash),
            ("target_manifest_hash", target_manifest_hash),
            ("authorization_hash", authorization_hash),
        ):
            if value is not None:
                try:
                    _validated_digest(value, name)
                except ValueError as exc:
                    raise CapabilityStoreError("invalid_runtime_set", str(exc)) from exc
        ordered = tuple(sorted(tuple(instances), key=lambda item: (item.ordinal, item.entry_id)))
        if (
            len({item.runtime_instance_id for item in ordered}) != len(ordered)
            or len({item.entry_id for item in ordered}) != len(ordered)
            or len({item.ordinal for item in ordered}) != len(ordered)
            or tuple(item.ordinal for item in ordered) != tuple(range(len(ordered)))
        ):
            raise CapabilityStoreError(
                "invalid_runtime_set",
                "runtime instances require unique contiguous ordinals and identities",
            )
        expected_hash = self._runtime_set_fingerprint(
            operation_id=operation_id,
            purpose=purpose,
            lifecycle_action=lifecycle_action,
            owner_activation_id=owner_activation_id,
            lease_activation_id=lease_activation_id,
            target_pack_id=target_pack_id,
            target_version=target_version,
            owner_binding_set_stamp=owner_binding_set_stamp,
            run_catalog_content_stamp=run_catalog_content_stamp,
            target_package_hash=target_package_hash,
            target_manifest_hash=target_manifest_hash,
            authorization_hash=authorization_hash,
            launch_revocation_epoch=launch_revocation_epoch,
            instances=ordered,
        )
        if runtime_set_hash is not None and runtime_set_hash != expected_hash:
            raise CapabilityStoreConflict(
                "runtime_set_hash_mismatch",
                "runtime set hash does not match its immutable members",
            )
        current = await self._get_runtime_set_tx(db, operation_id)
        if current is not None:
            current_specs = tuple(
                CapabilityRuntimeInstanceSpec(
                    runtime_instance_id=item.runtime_instance_id,
                    entry_id=item.entry_id,
                    ordinal=item.ordinal,
                    runtime_kind=item.runtime_kind,
                    adapter_fingerprint=item.adapter_fingerprint,
                    argv_hash=item.argv_hash,
                    env_scope_hash=item.env_scope_hash,
                    workdir=item.workdir,
                )
                for item in current.instances
            )
            immutable = (
                current.purpose,
                current.lifecycle_action,
                current.owner_activation_id,
                current.lease_activation_id,
                current.target_pack_id,
                current.target_version,
                current.owner_binding_set_stamp,
                current.run_catalog_content_stamp,
                current.target_package_hash,
                current.target_manifest_hash,
                current.runtime_set_hash,
                current.expected_instance_count,
                current.authorization_hash,
                current.launch_revocation_epoch,
                current_specs,
            )
            expected = (
                purpose,
                lifecycle_action,
                owner_activation_id,
                lease_activation_id,
                target_pack_id,
                target_version,
                owner_binding_set_stamp,
                run_catalog_content_stamp,
                target_package_hash,
                target_manifest_hash,
                expected_hash,
                len(ordered),
                authorization_hash,
                launch_revocation_epoch,
                ordered,
            )
            if immutable != expected:
                raise CapabilityStoreConflict(
                    "runtime_set_conflict",
                    "stored runtime set differs from immutable request",
                )
            return current
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_runtime_sets(
                operation_id,purpose,lifecycle_action,owner_activation_id,
                lease_activation_id,target_pack_id,target_version,
                owner_binding_set_stamp,run_catalog_content_stamp,
                target_package_hash,target_manifest_hash,runtime_set_hash,
                expected_instance_count,authorization_hash,
                launch_revocation_epoch,status,created_at,updated_at,last_error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?,?,NULL)""",
            (
                operation_id,
                purpose,
                lifecycle_action,
                owner_activation_id,
                lease_activation_id,
                target_pack_id,
                target_version,
                owner_binding_set_stamp,
                run_catalog_content_stamp,
                target_package_hash,
                target_manifest_hash,
                expected_hash,
                len(ordered),
                authorization_hash,
                launch_revocation_epoch,
                now,
                now,
            ),
        )
        for item in ordered:
            await db.execute(
                """INSERT INTO capability_runtime_prepare_intents(
                    operation_id,runtime_instance_id,entry_id,ordinal,
                    runtime_kind,status,runtime_set_hash,authorization_hash,
                    launch_revocation_epoch,adapter_fingerprint,argv_hash,
                    env_scope_hash,workdir,job_identity,pid,
                    process_create_time,session_identity,start_outcome,
                    start_ack_at,health_outcome_hash,started_at,updated_at,
                    last_error
                ) VALUES(?,?,?,?,?,'prepared',?,?,?,?,?,?,?,NULL,NULL,NULL,
                         NULL,NULL,NULL,NULL,NULL,?,NULL)""",
                (
                    operation_id,
                    item.runtime_instance_id,
                    item.entry_id,
                    item.ordinal,
                    item.runtime_kind,
                    expected_hash,
                    authorization_hash,
                    launch_revocation_epoch,
                    item.adapter_fingerprint,
                    item.argv_hash,
                    item.env_scope_hash,
                    item.workdir,
                    now,
                ),
            )
        prepared = await self._get_runtime_set_tx(db, operation_id)
        if prepared is None:  # pragma: no cover
            raise CapabilityStoreError("runtime_set_missing", "prepared runtime set disappeared")
        return prepared

    async def prepare_runtime_set(self, **kwargs: Any) -> CapabilityRuntimeSetRecord:
        async with self.write_transaction() as db:
            return await self._prepare_runtime_set_tx(db, **kwargs)

    async def get_runtime_set(self, operation_id: str) -> CapabilityRuntimeSetRecord | None:
        async with self.read_connection() as db:
            return await self._get_runtime_set_tx(db, operation_id)

    async def _mark_runtime_instance_ready_tx(
        self,
        db: aiosqlite.Connection,
        operation_id: str,
        runtime_instance_id: str,
        *,
        job_identity: str,
        pid: int,
        process_create_time: float,
        session_identity: str,
        start_outcome: str,
        start_ack_at: float,
        health_outcome_hash: str,
        started_at: float,
    ) -> CapabilityRuntimePrepareIntent:
        _validated_digest(health_outcome_hash, "health_outcome_hash")
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_prepare_intents
                   WHERE operation_id=? AND runtime_instance_id=?""",
                (operation_id, runtime_instance_id),
            )
        )
        if not rows:
            raise CapabilityStoreError("runtime_instance_missing", "runtime instance is absent")
        current = _runtime_prepare_intent_from_row(rows[0])
        expected_runtime = (
            job_identity,
            pid,
            float(process_create_time),
            session_identity,
            start_outcome,
            float(start_ack_at),
            health_outcome_hash,
            float(started_at),
        )
        actual_runtime = (
            current.job_identity,
            current.pid,
            current.process_create_time,
            current.session_identity,
            current.start_outcome,
            current.start_ack_at,
            current.health_outcome_hash,
            current.started_at,
        )
        if current.status in {"health_passed", "activated"}:
            if actual_runtime != expected_runtime:
                raise CapabilityStoreConflict(
                    "runtime_instance_conflict",
                    "ready runtime identity differs from immutable receipt",
                )
            return current
        if current.status not in {"prepared", "launch_claimed", "started"}:
            raise CapabilityStoreConflict(
                "runtime_instance_terminal",
                "runtime instance cannot become ready from its current state",
            )
        await db.execute(
            """UPDATE capability_runtime_prepare_intents
               SET status='health_passed',job_identity=?,pid=?,
                   process_create_time=?,session_identity=?,start_outcome=?,
                   start_ack_at=?,health_outcome_hash=?,started_at=?,
                   updated_at=?,last_error=NULL
               WHERE operation_id=? AND runtime_instance_id=?""",
            (
                *expected_runtime,
                self._clock(),
                operation_id,
                runtime_instance_id,
            ),
        )
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_prepare_intents
                   WHERE operation_id=? AND runtime_instance_id=?""",
                (operation_id, runtime_instance_id),
            )
        )
        return _runtime_prepare_intent_from_row(rows[0])

    async def mark_runtime_instance_ready(
        self, operation_id: str, runtime_instance_id: str, **kwargs: Any
    ) -> CapabilityRuntimePrepareIntent:
        async with self.write_transaction() as db:
            return await self._mark_runtime_instance_ready_tx(
                db, operation_id, runtime_instance_id, **kwargs
            )

    async def _mark_runtime_set_ready_tx(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> CapabilityRuntimeSetRecord:
        current = await self._get_runtime_set_tx(db, operation_id)
        if current is None:
            raise CapabilityStoreError("runtime_set_missing", "runtime set is absent")
        self._validate_runtime_set_integrity(current)
        if current.status == "activated":
            return current
        if current.status not in {"prepared", "launching", "started", "health_passed"}:
            raise CapabilityStoreConflict(
                "runtime_set_terminal",
                "runtime set cannot become ready from its current state",
            )
        if any(item.status != "health_passed" for item in current.instances):
            raise CapabilityStoreConflict(
                "runtime_set_not_ready",
                "all runtime instances must pass health before activation",
            )
        now = self._clock()
        await db.execute(
            """UPDATE capability_runtime_prepare_intents
               SET status='activated',updated_at=?
               WHERE operation_id=? AND status='health_passed'""",
            (now, operation_id),
        )
        await db.execute(
            """UPDATE capability_runtime_sets
               SET status='activated',updated_at=?,last_error=NULL
               WHERE operation_id=?""",
            (now, operation_id),
        )
        ready = await self._get_runtime_set_tx(db, operation_id)
        assert ready is not None
        return ready

    async def mark_runtime_set_ready(self, operation_id: str) -> CapabilityRuntimeSetRecord:
        async with self.write_transaction() as db:
            return await self._mark_runtime_set_ready_tx(db, operation_id)

    async def _retire_runtime_set_tx(
        self, db: aiosqlite.Connection, operation_id: str
    ) -> CapabilityRuntimeSetRecord:
        current = await self._get_runtime_set_tx(db, operation_id)
        if current is None:
            raise CapabilityStoreError("runtime_set_missing", "runtime set is absent")
        if current.status == "aborted":
            return current
        if current.status == "cleanup_required":
            raise CapabilityStoreConflict(
                "runtime_set_cleanup_required",
                "runtime set requires explicit cleanup reconciliation",
            )
        now = self._clock()
        await db.execute(
            """UPDATE capability_runtime_prepare_intents
               SET status='aborted',updated_at=?
               WHERE operation_id=? AND status!='cleanup_required'""",
            (now, operation_id),
        )
        await db.execute(
            """UPDATE capability_runtime_sets
               SET status='aborted',updated_at=?,last_error=NULL
               WHERE operation_id=?""",
            (now, operation_id),
        )
        retired = await self._get_runtime_set_tx(db, operation_id)
        assert retired is not None
        return retired

    async def retire_runtime_set(self, operation_id: str) -> CapabilityRuntimeSetRecord:
        async with self.write_transaction() as db:
            return await self._retire_runtime_set_tx(db, operation_id)

    async def list_active_versions(
        self, *, owner_key: str | None = None
    ) -> tuple[CapabilityVersionRecord, ...]:
        """Return each immutable version selected by any active binding once."""

        async with self.read_connection() as db:
            owner_clause = "" if owner_key is None else "AND b.owner_key=?"
            parameters: tuple[Any, ...] = () if owner_key is None else (owner_key,)
            rows = await (
                await db.execute(
                    f"""SELECT DISTINCT v.*
                       FROM capability_versions AS v
                       JOIN capability_bindings AS b
                         ON b.pack_id=v.pack_id
                        AND b.active_version=v.version
                        AND b.active_manifest_hash=v.manifest_hash
                       WHERE b.enabled=1 {owner_clause}
                       ORDER BY v.pack_id,v.version,v.manifest_hash""",
                    parameters,
                )
            ).fetchall()
        return tuple(_version_from_row(row) for row in rows)

    async def visible_entries(
        self, scope: CapabilityScope, *, owner_key: str | None = None
    ) -> tuple[CapabilityCatalogEntry, ...]:
        keys = scope.binding_keys()
        owner_keys = tuple(
            ("builtin" if scope_name == "builtin" else str(owner_key or LEGACY_LOCAL_OWNER_KEY))
            for scope_name, _scope_key in keys
        )
        clauses = " OR ".join("(b.owner_key=? AND b.scope=? AND b.scope_key=?)" for _ in keys)
        params = tuple(
            value for owner, item in zip(owner_keys, keys, strict=True) for value in (owner, *item)
        )
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    f"""SELECT b.*,v.descriptor_json,
                        v.expected_tool_fingerprints_json
                        FROM capability_bindings b
                        JOIN capability_versions v
                          ON v.pack_id=b.pack_id
                         AND v.version=b.active_version
                         AND v.manifest_hash=b.active_manifest_hash
                        WHERE b.enabled=1 AND ({clauses})
                        ORDER BY b.pack_id,b.scope,b.generation""",
                    params,
                )
            ).fetchall()
        grouped: dict[
            tuple[str, str, str],
            tuple[CapabilityVersionDescriptor, list[CapabilityBinding], tuple[str, ...]],
        ] = {}
        for row in rows:
            descriptor = _descriptor_from_json(str(row["descriptor_json"]))
            key = (
                descriptor.capability_id,
                descriptor.version,
                descriptor.manifest_hash,
            )
            fingerprints = tuple(
                str(item) for item in _json_array(str(row["expected_tool_fingerprints_json"]))
            )
            if key not in grouped:
                grouped[key] = (descriptor, [], fingerprints)
            grouped[key][1].append(_binding_from_row(row))
        return tuple(
            CapabilityCatalogEntry(
                version=descriptor,
                bindings=tuple(bindings),
                expected_tool_fingerprints=fingerprints,
            )
            for descriptor, bindings, fingerprints in grouped.values()
        )
