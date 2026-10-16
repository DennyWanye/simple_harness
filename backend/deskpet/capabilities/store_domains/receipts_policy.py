# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Failure receipts, repair attempts, policy state, authorization imports and task grants."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.permissions.policy import (
    AuthorizationMode,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyState,
)
from deskpet.types.task_grants import TaskGrant

from ..contracts import JsonValue, canonical_json
from ..failure_receipts import (
    CapabilityFailureReceipt,
    CapabilityFailureReceiptIssuer,
    CapabilityRepairAttempt,
)
from .core import CapabilityStoreCore
from .support import (
    CapabilitySchemaMissing,
    CapabilityStoreConflict,
    CapabilityStoreError,
    LegacyAuthorizationImportRecord,
    _binding_from_row,
    _descriptor_from_json,
    _json_array,
    _json_object,
)


def _failure_receipt_from_row(
    row: Mapping[str, Any],
) -> CapabilityFailureReceipt:
    canonical_args = _json_object(str(row["canonical_args_json"]))
    if canonical_args is None:  # pragma: no cover - NOT NULL in schema
        raise CapabilityStoreError("corrupt_failure_receipt", "canonical args cannot be null")
    return CapabilityFailureReceipt(
        receipt_ref=str(row["receipt_ref"]),
        root_run_id=str(row["root_run_id"]),
        run_id=str(row["run_id"]),
        attempt_id=str(row["attempt_id"]),
        failure_report_ref=str(row["failure_report_ref"]),
        provider_call_id=str(row["provider_call_id"]),
        effect_id=str(row["effect_id"]),
        capability_id=str(row["capability_id"]),
        pack_version=str(row["pack_version"]),
        manifest_hash=str(row["manifest_hash"]),
        tool_name=str(row["tool_name"]),
        tool_spec_fingerprint=str(row["tool_spec_fingerprint"]),
        binding_scope=str(row["binding_scope"]),
        scope_key=str(row["scope_key"]),
        canonical_args=canonical_args,
        args_hash=str(row["args_hash"]),
        error_code=str(row["error_code"]),
        error_fingerprint=str(row["error_fingerprint"]),
        evidence_refs=tuple(str(item) for item in _json_array(str(row["evidence_refs_json"]))),
    )


def _repair_attempt_from_row(
    row: Mapping[str, Any],
) -> CapabilityRepairAttempt:
    return CapabilityRepairAttempt(
        root_run_id=str(row["root_run_id"]),
        error_fingerprint=str(row["error_fingerprint"]),
        attempt_no=int(row["attempt_no"]),
        failure_receipt_ref=str(row["failure_receipt_ref"]),
        control_call_id=str(row["control_call_id"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        child_run_id=(None if row["child_run_id"] is None else str(row["child_run_id"])),
        operation_id=(None if row["operation_id"] is None else str(row["operation_id"])),
    )


def _legacy_authorization_import_from_row(
    row: Mapping[str, Any],
) -> LegacyAuthorizationImportRecord:
    return LegacyAuthorizationImportRecord(
        source_key=str(row["source_key"]),
        outcome=str(row["outcome"]),  # type: ignore[arg-type]
        source_fingerprint=(
            None if row["source_fingerprint"] is None else str(row["source_fingerprint"])
        ),
        imported_mode=(None if row["imported_mode"] is None else str(row["imported_mode"])),  # type: ignore[arg-type]
        error_code=None if row["error_code"] is None else str(row["error_code"]),
        imported_at=float(row["imported_at"]),
    )


class ReceiptPolicyMixin(CapabilityStoreCore):
    """Failure receipts, repair attempts, policy state, authorization imports and task grants."""

    __slots__ = ()

    async def _put_failure_receipt_tx(
        self,
        db: aiosqlite.Connection,
        receipt: CapabilityFailureReceipt,
    ) -> CapabilityFailureReceipt:
        payload = (
            receipt.receipt_ref,
            receipt.root_run_id,
            receipt.run_id,
            receipt.attempt_id,
            receipt.failure_report_ref,
            receipt.provider_call_id,
            receipt.effect_id,
            receipt.capability_id,
            receipt.pack_version,
            receipt.manifest_hash,
            receipt.tool_name,
            receipt.tool_spec_fingerprint,
            receipt.binding_scope,
            receipt.scope_key,
            canonical_json(dict(receipt.canonical_args)),
            receipt.args_hash,
            receipt.error_code,
            receipt.error_fingerprint,
            canonical_json(list(receipt.evidence_refs)),
        )
        existing = await (
            await db.execute(
                """SELECT * FROM capability_failure_receipts
                   WHERE receipt_ref=? OR effect_id=? OR (
                       root_run_id=? AND failure_report_ref=?
                   )""",
                (
                    receipt.receipt_ref,
                    receipt.effect_id,
                    receipt.root_run_id,
                    receipt.failure_report_ref,
                ),
            )
        ).fetchone()
        if existing is not None:
            current = _failure_receipt_from_row(existing)
            if current != receipt:
                raise CapabilityStoreConflict(
                    "failure_receipt_conflict",
                    "capability failure receipt replay differs",
                )
            return current
        await db.execute(
            """INSERT INTO capability_failure_receipts(
                receipt_ref,root_run_id,run_id,attempt_id,failure_report_ref,
                provider_call_id,effect_id,capability_id,pack_version,
                manifest_hash,tool_name,tool_spec_fingerprint,binding_scope,
                scope_key,canonical_args_json,args_hash,error_code,
                error_fingerprint,evidence_refs_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*payload, self._clock()),
        )
        return receipt

    async def put_failure_receipt(
        self, receipt: CapabilityFailureReceipt
    ) -> CapabilityFailureReceipt:
        async with self.write_transaction() as db:
            return await self._put_failure_receipt_tx(db, receipt)

    async def issue_failure_receipt(
        self,
        *,
        root_run_id: str,
        run_id: str,
        attempt_id: str,
        failure_report_ref: str,
        provider_call_id: str,
        effect_id: str,
        capability_id: str,
        pack_version: str,
        manifest_hash: str,
        tool_name: str,
        tool_spec_fingerprint: str,
        canonical_args: Mapping[str, JsonValue],
        error_code: str,
        error_fingerprint: str,
        evidence_refs: Sequence[str] = (),
    ) -> CapabilityFailureReceipt:
        """Validate immutable pack provenance and persist a host receipt."""

        async with self.write_transaction() as db:
            version_row = await (
                await db.execute(
                    """SELECT expected_tool_fingerprints_json,descriptor_json
                       FROM capability_versions
                       WHERE pack_id=? AND version=? AND manifest_hash=?""",
                    (capability_id, pack_version, manifest_hash),
                )
            ).fetchone()
            if version_row is None:
                raise CapabilityStoreError(
                    "failure_capability_version_missing",
                    "failed prepared call does not resolve to an installed version",
                )
            expected = {
                str(item)
                for item in _json_array(str(version_row["expected_tool_fingerprints_json"]))
            }
            descriptor = _descriptor_from_json(str(version_row["descriptor_json"]))
            if (
                tool_spec_fingerprint not in expected
                or tool_name not in descriptor.provider_tool_names
            ):
                raise CapabilityStoreError(
                    "failure_capability_provenance_mismatch",
                    "failed prepared call does not belong to the immutable pack",
                )
            binding_row = await (
                await db.execute(
                    """SELECT * FROM capability_bindings
                       WHERE pack_id=? AND active_version=?
                         AND active_manifest_hash=? AND enabled=1
                       ORDER BY CASE
                           WHEN scope='run' AND scope_key=? THEN 0
                           WHEN scope='project' THEN 1
                           WHEN scope='user' THEN 2
                           ELSE 3
                       END, generation DESC
                       LIMIT 1""",
                    (
                        capability_id,
                        pack_version,
                        manifest_hash,
                        root_run_id,
                    ),
                )
            ).fetchone()
            if binding_row is None:
                raise CapabilityStoreError(
                    "failure_capability_binding_missing",
                    "failed pack version has no active trusted binding",
                )
            binding = _binding_from_row(binding_row)
            receipt = CapabilityFailureReceiptIssuer.issue(
                root_run_id=root_run_id,
                run_id=run_id,
                attempt_id=attempt_id,
                failure_report_ref=failure_report_ref,
                provider_call_id=provider_call_id,
                effect_id=effect_id,
                capability_id=capability_id,
                pack_version=pack_version,
                manifest_hash=manifest_hash,
                tool_name=tool_name,
                tool_spec_fingerprint=tool_spec_fingerprint,
                binding_scope=binding.scope,
                scope_key=binding.scope_key,
                canonical_args=canonical_args,
                error_code=error_code,
                error_fingerprint=error_fingerprint,
                evidence_refs=evidence_refs,
            )
            return await self._put_failure_receipt_tx(db, receipt)

    async def get_failure_receipt(self, receipt_ref: str) -> CapabilityFailureReceipt | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_failure_receipts
                       WHERE receipt_ref=?""",
                    (receipt_ref,),
                )
            ).fetchone()
            return None if row is None else _failure_receipt_from_row(row)

    async def _claim_repair_attempt_tx(
        self,
        db: aiosqlite.Connection,
        *,
        root_run_id: str,
        failure_receipt_ref: str,
        control_call_id: str,
    ) -> CapabilityRepairAttempt:
        receipt_row = await (
            await db.execute(
                """SELECT * FROM capability_failure_receipts
                   WHERE receipt_ref=?""",
                (failure_receipt_ref,),
            )
        ).fetchone()
        if receipt_row is None:
            raise CapabilityStoreError(
                "failure_receipt_missing",
                "capability repair requires a durable host-signed receipt",
            )
        receipt = _failure_receipt_from_row(receipt_row)
        if receipt.root_run_id != root_run_id:
            raise CapabilityStoreError(
                "failure_receipt_root_mismatch",
                "capability failure receipt belongs to another root run",
            )
        replay = await (
            await db.execute(
                """SELECT * FROM capability_repair_attempts
                   WHERE root_run_id=? AND control_call_id=?""",
                (root_run_id, control_call_id),
            )
        ).fetchone()
        if replay is not None:
            attempt = _repair_attempt_from_row(replay)
            if attempt.failure_receipt_ref != failure_receipt_ref:
                raise CapabilityStoreConflict(
                    "repair_attempt_conflict",
                    "control call already belongs to another failure receipt",
                )
            return attempt
        row = await (
            await db.execute(
                """SELECT COALESCE(MAX(attempt_no),0) AS max_attempt
                   FROM capability_repair_attempts
                   WHERE root_run_id=? AND error_fingerprint=?""",
                (root_run_id, receipt.error_fingerprint),
            )
        ).fetchone()
        attempt_no = int(row["max_attempt"] or 0) + 1
        if attempt_no > 3:
            raise CapabilityStoreConflict(
                "capability_repair_budget_exhausted",
                "the same capability failure was already repaired three times",
            )
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_repair_attempts(
                root_run_id,error_fingerprint,attempt_no,failure_receipt_ref,
                control_call_id,status,child_run_id,operation_id,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,'admitted',NULL,NULL,?,?)""",
            (
                root_run_id,
                receipt.error_fingerprint,
                attempt_no,
                failure_receipt_ref,
                control_call_id,
                now,
                now,
            ),
        )
        return CapabilityRepairAttempt(
            root_run_id=root_run_id,
            error_fingerprint=receipt.error_fingerprint,
            attempt_no=attempt_no,
            failure_receipt_ref=failure_receipt_ref,
            control_call_id=control_call_id,
            status="admitted",
        )

    async def claim_repair_attempt(
        self,
        *,
        root_run_id: str,
        failure_receipt_ref: str,
        control_call_id: str,
    ) -> CapabilityRepairAttempt:
        async with self.write_transaction() as db:
            return await self._claim_repair_attempt_tx(
                db,
                root_run_id=root_run_id,
                failure_receipt_ref=failure_receipt_ref,
                control_call_id=control_call_id,
            )

    async def _get_policy_state_tx(self, db: aiosqlite.Connection) -> AuthorizationPolicyState:
        row = await (
            await db.execute(
                """SELECT mode,generation,updated_at,provenance,
                          schema_generation,user_set_receipt_ref
                   FROM authorization_policy_state WHERE singleton_id=1"""
            )
        ).fetchone()
        if row is None:
            raise CapabilitySchemaMissing(
                "authorization_policy_missing", "policy singleton is absent"
            )
        return AuthorizationPolicyState(
            mode=str(row["mode"]),  # type: ignore[arg-type]
            generation=int(row["generation"]),
            updated_at=float(row["updated_at"]),
            provenance=str(row["provenance"]),  # type: ignore[arg-type]
            schema_generation=int(row["schema_generation"]),
            user_set_receipt_ref=(
                None if row["user_set_receipt_ref"] is None else str(row["user_set_receipt_ref"])
            ),
        )

    async def get_policy_state(self) -> AuthorizationPolicyState:
        async with self.read_connection() as db:
            return await self._get_policy_state_tx(db)

    async def _compare_and_set_policy_mode_tx(
        self,
        db: aiosqlite.Connection,
        mode: AuthorizationMode,
        *,
        expected_generation: int,
        provenance: AuthorizationPolicyProvenance = "user_explicit",
        user_set_receipt_ref: str | None = None,
    ) -> AuthorizationPolicyState:
        current = await self._get_policy_state_tx(db)
        successor = current.transition(
            mode,
            expected_generation=expected_generation,
            now=self._clock(),
            provenance=provenance,
            user_set_receipt_ref=user_set_receipt_ref,
        )
        cursor = await db.execute(
            """UPDATE authorization_policy_state
               SET mode=?,generation=?,updated_at=?,provenance=?,
                   schema_generation=?,user_set_receipt_ref=?
               WHERE singleton_id=1 AND generation=?""",
            (
                successor.mode,
                successor.generation,
                successor.updated_at,
                successor.provenance,
                successor.schema_generation,
                successor.user_set_receipt_ref,
                expected_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "policy_generation_conflict", "authorization policy changed"
            )
        return successor

    async def compare_and_set_policy_mode(
        self,
        mode: AuthorizationMode,
        *,
        expected_generation: int,
        provenance: AuthorizationPolicyProvenance = "user_explicit",
        user_set_receipt_ref: str | None = None,
    ) -> AuthorizationPolicyState:
        async with self.write_transaction() as db:
            return await self._compare_and_set_policy_mode_tx(
                db,
                mode,
                expected_generation=expected_generation,
                provenance=provenance,
                user_set_receipt_ref=user_set_receipt_ref,
            )

    async def _get_legacy_authorization_import_tx(
        self,
        db: aiosqlite.Connection,
        source_key: str,
    ) -> LegacyAuthorizationImportRecord | None:
        row = await (
            await db.execute(
                """SELECT source_key,outcome,source_fingerprint,imported_mode,
                          error_code,imported_at
                   FROM authorization_policy_legacy_imports
                   WHERE source_key=?""",
                (source_key,),
            )
        ).fetchone()
        if row is None:
            return None
        return _legacy_authorization_import_from_row(row)

    async def get_legacy_authorization_import(
        self, source_key: str
    ) -> LegacyAuthorizationImportRecord | None:
        async with self.read_connection() as db:
            return await self._get_legacy_authorization_import_tx(db, source_key)

    async def _put_legacy_authorization_import_tx(
        self,
        db: aiosqlite.Connection,
        record: LegacyAuthorizationImportRecord,
    ) -> LegacyAuthorizationImportRecord:
        existing = await self._get_legacy_authorization_import_tx(db, record.source_key)
        if existing is not None:
            if existing != record:
                raise CapabilityStoreConflict(
                    "legacy_authorization_import_conflict",
                    "legacy authorization source was already consumed",
                )
            return existing
        await db.execute(
            """INSERT INTO authorization_policy_legacy_imports(
                   source_key,outcome,source_fingerprint,imported_mode,
                   error_code,imported_at
               ) VALUES(?,?,?,?,?,?)""",
            (
                record.source_key,
                record.outcome,
                record.source_fingerprint,
                record.imported_mode,
                record.error_code,
                record.imported_at,
            ),
        )
        return record

    async def put_legacy_authorization_import(
        self, record: LegacyAuthorizationImportRecord
    ) -> LegacyAuthorizationImportRecord:
        async with self.write_transaction() as db:
            return await self._put_legacy_authorization_import_tx(db, record)

    async def _put_task_grant_tx(self, db: aiosqlite.Connection, grant: TaskGrant) -> TaskGrant:
        grant_json = canonical_json(grant.to_dict())
        existing = await (
            await db.execute(
                "SELECT * FROM task_grants WHERE task_grant_id=?",
                (grant.task_grant_id,),
            )
        ).fetchone()
        if existing is not None:
            if (
                str(existing["grant_fingerprint"]) != grant.fingerprint
                or str(existing["grant_json"]) != grant_json
            ):
                raise CapabilityStoreConflict(
                    "task_grant_conflict",
                    "TaskGrant identity already belongs to another grant",
                )
            return grant
        await db.execute(
            """INSERT INTO task_grants(
                task_grant_id,root_run_id,source,policy_generation,version,
                grant_fingerprint,grant_json,status,created_at,revoked_at
            ) VALUES(?,?,?,?,?,?,?,'active',?,NULL)""",
            (
                grant.task_grant_id,
                grant.root_run_id,
                grant.source,
                grant.policy_generation,
                grant.version,
                grant.fingerprint,
                grant_json,
                self._clock(),
            ),
        )
        return grant

    async def put_task_grant(self, grant: TaskGrant) -> TaskGrant:
        async with self.write_transaction() as db:
            return await self._put_task_grant_tx(db, grant)

    async def get_task_grant(self, task_grant_id: str) -> TaskGrant | None:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT grant_json FROM task_grants
                       WHERE task_grant_id=? AND status='active'""",
                    (task_grant_id,),
                )
            ).fetchone()
            if row is None:
                return None
            return TaskGrant.from_dict(json.loads(str(row["grant_json"])))

    async def revoke_task_grant(self, task_grant_id: str) -> bool:
        async with self.write_transaction() as db:
            cursor = await db.execute(
                """UPDATE task_grants SET status='revoked',revoked_at=?
                   WHERE task_grant_id=? AND status='active'""",
                (self._clock(), task_grant_id),
            )
            return cursor.rowcount == 1
