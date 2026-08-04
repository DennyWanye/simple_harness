# SPDX-License-Identifier: BUSL-1.1
"""Typed RuntimeSetLedgerPort backed by CapabilityStore generic runtime DML."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .contracts import fingerprint_json
from .runtime_prepare import (
    OwnerRuntimeActivation,
    PreparedRuntimeInstanceSpec,
    PreparedRuntimeSet,
    RuntimeHealthOutcome,
    RuntimeLaunchAuthorization,
    RuntimeNotStarted,
    RuntimeStartedAck,
    RuntimeStartResult,
    RuntimeStartUnknown,
    runtime_instance_id_for,
)
from .store import CapabilityRuntimeInstanceSpec, CapabilityStore


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


class CapabilityStoreRuntimeSetLedger:
    """Translate the product-neutral coordinator protocol to Store records.

    The generic Store owns its internal set fingerprint.  The coordinator's
    public runtime-set fingerprint is revalidated from the caller's immutable
    ``PreparedRuntimeSet`` on every operation, while each launch authorization
    is durably bound to its deterministic runtime-instance row.
    """

    def __init__(self, store: CapabilityStore) -> None:
        self._store = store

    async def next_owner_runtime_generation(
        self, owner_key: str, scope: str, scope_key: str
    ) -> int:
        async with self._store.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT COALESCE(MAX(
                           owner_runtime_activation_generation
                       ),0)
                       FROM capability_owner_runtime_activations
                       WHERE owner_key=? AND scope=? AND scope_key=?""",
                    (owner_key, scope, scope_key),
                )
            ).fetchone()
        return int(row[0]) + 1

    async def create_owner_runtime_activation(
        self, activation: OwnerRuntimeActivation
    ) -> OwnerRuntimeActivation:
        async with self._store.write_transaction() as db:
            rows = await (
                await db.execute(
                    """SELECT * FROM capability_owner_runtime_activations
                       WHERE owner_activation_id=?
                          OR (
                              owner_key=? AND scope=? AND scope_key=?
                              AND owner_runtime_activation_generation=?
                          )""",
                    (
                        activation.owner_activation_id,
                        activation.owner_key,
                        activation.scope,
                        activation.scope_key,
                        activation.generation,
                    ),
                )
            ).fetchall()
            if rows:
                if len(rows) != 1:
                    raise RuntimeError("owner_runtime_activation_conflict")
                row = rows[0]
                actual = (
                    str(row["owner_activation_id"]),
                    str(row["owner_key"]),
                    str(row["scope"]),
                    str(row["scope_key"]),
                    int(row["owner_runtime_activation_generation"]),
                    str(row["owner_binding_set_stamp"]),
                    int(row["startup_or_profile_epoch"]),
                )
                expected = (
                    activation.owner_activation_id,
                    activation.owner_key,
                    activation.scope,
                    activation.scope_key,
                    activation.generation,
                    activation.owner_binding_set_stamp,
                    activation.startup_or_profile_epoch,
                )
                if actual != expected:
                    raise RuntimeError("owner_runtime_activation_conflict")
                return activation
            now = self._store.now()
            await db.execute(
                """INSERT INTO capability_owner_runtime_activations(
                    owner_key,scope,scope_key,
                    owner_runtime_activation_generation,owner_activation_id,
                    owner_binding_set_stamp,startup_or_profile_epoch,status,
                    created_at,updated_at,last_error
                ) VALUES(?,?,?,?,?,?,?,'prepared',?,?,NULL)""",
                (
                    activation.owner_key,
                    activation.scope,
                    activation.scope_key,
                    activation.generation,
                    activation.owner_activation_id,
                    activation.owner_binding_set_stamp,
                    activation.startup_or_profile_epoch,
                    now,
                    now,
                ),
            )
        return activation

    async def create_runtime_set(
        self, prepared_set: PreparedRuntimeSet
    ) -> PreparedRuntimeSet:
        owner_activation_id = await self._owner_activation_id(prepared_set)
        instances = tuple(
            self._store_instance(prepared_set, item)
            for item in prepared_set.instances
        )
        record = await self._store.prepare_runtime_set(
            operation_id=prepared_set.operation_id,
            purpose=prepared_set.purpose,
            lifecycle_action=None,
            owner_activation_id=owner_activation_id,
            lease_activation_id=None,
            target_pack_id=None,
            target_version=None,
            owner_binding_set_stamp=prepared_set.owner_binding_set_stamp,
            run_catalog_content_stamp=None,
            target_package_hash=None,
            target_manifest_hash=None,
            authorization_hash=prepared_set.runtime_set_hash,
            launch_revocation_epoch=prepared_set.launch_revocation_epoch,
            instances=instances,
        )
        self._validate_record(prepared_set, record)
        return prepared_set

    async def claim_runtime_instance(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        authorization: RuntimeLaunchAuthorization,
    ) -> str:
        authorization.validate(prepared_set, instance)
        runtime_instance_id = runtime_instance_id_for(
            prepared_set, instance
        )
        async with self._store.write_transaction() as db:
            record = await self._store._get_runtime_set_tx(
                db, prepared_set.operation_id
            )
            self._validate_record(prepared_set, record)
            row = await (
                await db.execute(
                    """SELECT status,authorization_hash
                       FROM capability_runtime_prepare_intents
                       WHERE operation_id=? AND runtime_instance_id=?
                         AND entry_id=?""",
                    (
                        prepared_set.operation_id,
                        runtime_instance_id,
                        instance.entry_id,
                    ),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("runtime_instance_missing")
            current_hash = (
                None
                if row["authorization_hash"] is None
                else str(row["authorization_hash"])
            )
            if str(row["status"]) == "launch_claimed":
                if current_hash != authorization.authorization_hash:
                    raise RuntimeError("runtime_launch_claim_conflict")
                return runtime_instance_id
            if str(row["status"]) != "prepared":
                raise RuntimeError("runtime_launch_claim_terminal")
            cursor = await db.execute(
                """UPDATE capability_runtime_prepare_intents
                   SET status='launch_claimed',authorization_hash=?,
                       updated_at=?,last_error=NULL
                   WHERE operation_id=? AND runtime_instance_id=?
                     AND status='prepared'""",
                (
                    authorization.authorization_hash,
                    self._store.now(),
                    prepared_set.operation_id,
                    runtime_instance_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("runtime_launch_claim_conflict")
            await db.execute(
                """UPDATE capability_runtime_sets
                   SET status='launching',updated_at=?
                   WHERE operation_id=? AND status='prepared'""",
                (self._store.now(), prepared_set.operation_id),
            )
        return runtime_instance_id

    async def record_start_result(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        result: RuntimeStartResult,
    ) -> None:
        self._validate_result_binding(prepared_set, instance, result)
        runtime_instance_id = runtime_instance_id_for(
            prepared_set, instance
        )
        if isinstance(result, RuntimeStartedAck):
            status = "started"
            reason = "started"
            payload = _canonical_json(
                {
                    "adapter_identity": result.adapter_identity,
                    "start_identity": dict(result.start_identity),
                }
            )
            acknowledged_at = float(result.acknowledged_at)
            start_identity = dict(result.start_identity)
        elif isinstance(result, RuntimeNotStarted):
            status = "not_started"
            reason = result.reason_code
            payload = None
            acknowledged_at = self._store.now()
            start_identity = {}
        else:
            status = "unknown"
            reason = result.reason_code
            payload = None
            acknowledged_at = self._store.now()
            start_identity = {}
        async with self._store.write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_runtime_prepare_intents
                       WHERE operation_id=? AND runtime_instance_id=?""",
                    (prepared_set.operation_id, runtime_instance_id),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("runtime_instance_missing")
            expected = (status, reason, payload, acknowledged_at)
            actual = (
                str(row["status"]),
                None
                if row["start_outcome"] is None
                else str(row["start_outcome"]),
                None
                if row["session_identity"] is None
                else str(row["session_identity"]),
                None
                if row["start_ack_at"] is None
                else float(row["start_ack_at"]),
            )
            if str(row["status"]) in {
                "started",
                "not_started",
                "unknown",
                "health_passed",
                "activated",
            }:
                normalized_actual = (
                    "started"
                    if str(row["status"]) in {
                        "health_passed",
                        "activated",
                    }
                    else str(row["status"]),
                    actual[1],
                    actual[2],
                    actual[3],
                )
                if normalized_actual != expected:
                    raise RuntimeError("runtime_start_result_conflict")
                return
            if str(row["status"]) != "launch_claimed":
                raise RuntimeError("runtime_start_result_without_claim")
            await db.execute(
                """UPDATE capability_runtime_prepare_intents
                   SET status=?,job_identity=?,pid=?,process_create_time=?,
                       session_identity=?,start_outcome=?,start_ack_at=?,
                       started_at=?,updated_at=?,last_error=NULL
                   WHERE operation_id=? AND runtime_instance_id=?
                     AND status='launch_claimed'""",
                (
                    status,
                    start_identity.get("job_identity"),
                    start_identity.get("pid"),
                    start_identity.get("process_create_time"),
                    payload,
                    reason,
                    acknowledged_at,
                    acknowledged_at if status == "started" else None,
                    self._store.now(),
                    prepared_set.operation_id,
                    runtime_instance_id,
                ),
            )
            await db.execute(
                """UPDATE capability_runtime_sets
                   SET status=?,updated_at=?,last_error=?
                   WHERE operation_id=?""",
                (
                    status,
                    self._store.now(),
                    None if status == "started" else reason,
                    prepared_set.operation_id,
                ),
            )

    async def get_runtime_instance_start_result(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
    ) -> RuntimeStartResult | None:
        record = await self._store.get_runtime_set(prepared_set.operation_id)
        self._validate_record(prepared_set, record)
        runtime_instance_id = runtime_instance_id_for(
            prepared_set, instance
        )
        row = next(
            (
                item
                for item in record.instances
                if item.runtime_instance_id == runtime_instance_id
            ),
            None,
        )
        if row is None:
            raise RuntimeError("runtime_instance_missing")
        if row.status in {"prepared", "launch_claimed"}:
            return None
        if row.status in {"started", "health_passed", "activated"}:
            if row.session_identity is None or row.start_ack_at is None:
                raise RuntimeError("runtime_started_receipt_incomplete")
            payload = json.loads(row.session_identity)
            return RuntimeStartedAck(
                operation_id=prepared_set.operation_id,
                runtime_set_hash=prepared_set.runtime_set_hash,
                entry_id=instance.entry_id,
                runtime_instance_id=runtime_instance_id,
                adapter_identity=str(payload["adapter_identity"]),
                start_identity=dict(payload["start_identity"]),
                acknowledged_at=float(row.start_ack_at),
            )
        if row.status == "not_started":
            return RuntimeNotStarted(
                prepared_set.operation_id,
                instance.entry_id,
                str(row.start_outcome or "not_started"),
                runtime_instance_id,
            )
        if row.status in {"unknown", "cleanup_required"}:
            return RuntimeStartUnknown(
                prepared_set.operation_id,
                instance.entry_id,
                str(row.start_outcome or row.last_error or "unknown"),
                runtime_instance_id,
            )
        return None

    async def record_health_outcome(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        outcome: RuntimeHealthOutcome,
    ) -> None:
        if (
            outcome.operation_id != prepared_set.operation_id
            or outcome.entry_id != instance.entry_id
        ):
            raise ValueError("runtime health outcome binding mismatch")
        runtime_instance_id = runtime_instance_id_for(
            prepared_set, instance
        )
        target_status = (
            "health_passed" if outcome.healthy else "cleanup_required"
        )
        last_error = None if outcome.healthy else "runtime_health_failed"
        async with self._store.write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT status,health_outcome_hash
                       FROM capability_runtime_prepare_intents
                       WHERE operation_id=? AND runtime_instance_id=?""",
                    (prepared_set.operation_id, runtime_instance_id),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("runtime_instance_missing")
            if str(row["status"]) in {
                "health_passed",
                "activated",
                "cleanup_required",
            }:
                if str(row["health_outcome_hash"]) != outcome.outcome_hash:
                    raise RuntimeError("runtime_health_outcome_conflict")
                if (
                    outcome.healthy
                    != (str(row["status"]) in {"health_passed", "activated"})
                ):
                    raise RuntimeError("runtime_health_outcome_conflict")
                return
            if str(row["status"]) != "started":
                raise RuntimeError("runtime_health_without_started_ack")
            await db.execute(
                """UPDATE capability_runtime_prepare_intents
                   SET status=?,health_outcome_hash=?,
                       updated_at=?,last_error=?
                   WHERE operation_id=? AND runtime_instance_id=?
                     AND status='started'""",
                (
                    target_status,
                    outcome.outcome_hash,
                    self._store.now(),
                    last_error,
                    prepared_set.operation_id,
                    runtime_instance_id,
                ),
            )
            await db.execute(
                """UPDATE capability_runtime_sets
                   SET status=?,updated_at=?,last_error=?
                   WHERE operation_id=?""",
                (
                    target_status,
                    self._store.now(),
                    last_error,
                    prepared_set.operation_id,
                ),
            )

    async def assert_set_health_passed(
        self, operation_id: str, runtime_set_hash: str
    ) -> None:
        record = await self._store.get_runtime_set(operation_id)
        if record is None:
            raise RuntimeError("runtime_set_missing")
        if record.authorization_hash != runtime_set_hash:
            raise RuntimeError("runtime_set_hash_mismatch")
        if any(item.status != "health_passed" for item in record.instances):
            raise RuntimeError("runtime_set_not_healthy")

    async def mark_set_activated(
        self, operation_id: str, runtime_set_hash: str
    ) -> None:
        if not runtime_set_hash:
            raise ValueError("runtime_set_hash is required")
        async with self._store.write_transaction() as db:
            current = await self._store._get_runtime_set_tx(db, operation_id)
            if (
                current is None
                or current.authorization_hash != runtime_set_hash
            ):
                raise RuntimeError("runtime_set_hash_mismatch")
            record = await self._store._mark_runtime_set_ready_tx(
                db, operation_id
            )
            if record.owner_activation_id is not None:
                await db.execute(
                    """UPDATE capability_owner_runtime_activations
                       SET status='published',updated_at=?,last_error=NULL
                       WHERE owner_activation_id=?
                         AND status IN (
                             'prepared','launching','health_passed'
                         )""",
                    (
                        self._store.now(),
                        record.owner_activation_id,
                    ),
                )

    async def mark_set_aborted(
        self,
        operation_id: str,
        runtime_set_hash: str,
        reason_code: str,
    ) -> None:
        if not runtime_set_hash or not reason_code:
            raise ValueError("runtime set hash and reason are required")
        async with self._store.write_transaction() as db:
            current = await self._store._get_runtime_set_tx(db, operation_id)
            if (
                current is None
                or current.authorization_hash != runtime_set_hash
            ):
                raise RuntimeError("runtime_set_hash_mismatch")
            if current.status == "cleanup_required":
                now = self._store.now()
                await db.execute(
                    """UPDATE capability_runtime_prepare_intents
                       SET status='aborted',updated_at=?,last_error=?
                       WHERE operation_id=?""",
                    (now, reason_code, operation_id),
                )
                await db.execute(
                    """UPDATE capability_runtime_sets
                       SET status='aborted',updated_at=?,last_error=?
                       WHERE operation_id=?""",
                    (now, reason_code, operation_id),
                )
                record = await self._store._get_runtime_set_tx(
                    db, operation_id
                )
                assert record is not None
            else:
                record = await self._store._retire_runtime_set_tx(
                    db, operation_id
                )
            await db.execute(
                """UPDATE capability_runtime_sets
                   SET last_error=? WHERE operation_id=?""",
                (reason_code, operation_id),
            )
            if record.owner_activation_id is not None:
                await db.execute(
                    """UPDATE capability_owner_runtime_activations
                       SET status='aborted',updated_at=?,last_error=?
                       WHERE owner_activation_id=?
                         AND status!='cleanup_required'""",
                    (
                        self._store.now(),
                        reason_code,
                        record.owner_activation_id,
                    ),
                )

    async def _owner_activation_id(
        self, prepared_set: PreparedRuntimeSet
    ) -> str:
        async with self._store.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT owner_activation_id
                       FROM capability_owner_runtime_activations
                       WHERE owner_key=? AND scope=? AND scope_key=?
                         AND owner_runtime_activation_generation=?
                         AND owner_binding_set_stamp=?""",
                    (
                        prepared_set.owner_key,
                        prepared_set.scope,
                        prepared_set.scope_key,
                        prepared_set.owner_runtime_activation_generation,
                        prepared_set.owner_binding_set_stamp,
                    ),
                )
            ).fetchone()
        if row is None:
            raise RuntimeError("owner_runtime_activation_missing")
        return str(row["owner_activation_id"])

    @staticmethod
    def _store_instance(
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
    ) -> CapabilityRuntimeInstanceSpec:
        start = dict(instance.start_envelope)
        return CapabilityRuntimeInstanceSpec(
            runtime_instance_id=runtime_instance_id_for(
                prepared_set, instance
            ),
            entry_id=instance.entry_id,
            ordinal=instance.ordinal,
            runtime_kind=instance.runtime_kind,
            adapter_fingerprint=instance.adapter_fingerprint,
            argv_hash=fingerprint_json(start.get("argv", [])),
            env_scope_hash=fingerprint_json(
                start.get("env_scope", start.get("env", {}))
            ),
            workdir=str(start.get("workdir") or start.get("cwd") or "."),
        )

    @classmethod
    def _validate_record(
        cls, prepared_set: PreparedRuntimeSet, record: Any
    ) -> None:
        if record is None:
            raise RuntimeError("runtime_set_missing")
        expected = (
            prepared_set.operation_id,
            prepared_set.purpose,
            prepared_set.owner_binding_set_stamp,
            prepared_set.runtime_set_hash,
            prepared_set.launch_revocation_epoch,
            tuple(
                cls._store_instance(prepared_set, item)
                for item in prepared_set.instances
            ),
        )
        actual_specs = tuple(
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
        actual = (
            record.operation_id,
            record.purpose,
            record.owner_binding_set_stamp,
            record.authorization_hash,
            record.launch_revocation_epoch,
            actual_specs,
        )
        if actual != expected:
            raise RuntimeError("runtime_set_conflict")

    @staticmethod
    def _validate_result_binding(
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        result: RuntimeStartResult,
    ) -> None:
        if (
            result.operation_id != prepared_set.operation_id
            or result.entry_id != instance.entry_id
            or result.runtime_instance_id
            != runtime_instance_id_for(prepared_set, instance)
        ):
            raise ValueError("runtime start result binding mismatch")
        if (
            isinstance(result, RuntimeStartedAck)
            and result.runtime_set_hash != prepared_set.runtime_set_hash
        ):
            raise ValueError("runtime started ACK set identity mismatch")


__all__ = ["CapabilityStoreRuntimeSetLedger"]
