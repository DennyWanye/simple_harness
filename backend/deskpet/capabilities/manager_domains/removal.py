# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Rollback, uninstall and operation cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ..contracts import CapabilityBinding, CapabilityScopeKind, JsonValue
from ..store import CapabilityOperationRecord, CapabilityStoreConflict
from .core import CapabilityPackManagerCore
from .support import (
    CapabilityManagerError,
    CapabilityRollbackResult,
    CapabilityUninstallResult,
    _operation_id,
)


class RemovalMixin(CapabilityPackManagerCore):
    """Rollback, uninstall and operation cancellation."""

    __slots__ = ()

    async def rollback(
        self,
        *,
        pack_id: str,
        target_version: str,
        target_manifest_hash: str,
        scope: CapabilityScopeKind,
        scope_key: str,
        idempotency_key: str,
        root_run_id: str | None = None,
    ) -> CapabilityRollbackResult:
        operation_id = _operation_id(idempotency_key)
        try:
            return await self._rollback_impl(
                pack_id=pack_id,
                target_version=target_version,
                target_manifest_hash=target_manifest_hash,
                scope=scope,
                scope_key=scope_key,
                idempotency_key=idempotency_key,
                root_run_id=root_run_id,
            )
        except asyncio.CancelledError:
            await self._cancel_operation(operation_id)
            raise
        except Exception as exc:
            await self._record_operation_failure(operation_id, exc)
            raise

    async def _rollback_impl(
        self,
        *,
        pack_id: str,
        target_version: str,
        target_manifest_hash: str,
        scope: CapabilityScopeKind,
        scope_key: str,
        idempotency_key: str,
        root_run_id: str | None = None,
    ) -> CapabilityRollbackResult:
        await self.initialize()
        record = await self.store.get_version(pack_id, target_version, target_manifest_hash)
        if record is None:
            raise CapabilityManagerError(
                "rollback_version_not_found",
                f"unknown rollback target {pack_id}@{target_version}",
            )
        current = await self.store.get_binding(scope, scope_key, pack_id)
        if current is None:
            raise CapabilityManagerError(
                "binding_not_found", "rollback requires an existing binding"
            )
        operation_id = _operation_id(idempotency_key)
        operation = await self.store.create_operation(
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            kind="rollback",
            request={
                "pack_id": pack_id,
                "target_version": target_version,
                "target_manifest_hash": target_manifest_hash,
                "scope": scope,
                "scope_key": scope_key,
            },
            root_run_id=root_run_id,
            pack_id=pack_id,
            requested_scope=scope,
            requested_scope_key=scope_key,
        )
        if operation.status == "succeeded":
            binding = await self.store.get_binding(scope, scope_key, pack_id)
            if binding is None:
                raise CapabilityManagerError(
                    "completed_binding_missing", "rollback binding is absent"
                )
            return CapabilityRollbackResult(operation, binding, 0)
        await self._commit_phase(operation_id, "planned", {"target": target_version})
        # The non-file lifecycle phases are explicit no-ops for an already
        # verified immutable version.
        for phase in ("staged", "verified", "environment_ready"):
            await self._commit_phase(operation_id, phase, {"reused_version": target_version})
        candidate = await self.publisher.prepare_installed(record, operation_id=operation_id)
        await self._commit_phase(
            operation_id,
            "candidate_ready",
            {"tool_spec_fingerprints": list(candidate.tool_spec_fingerprints)},
        )
        binding, publication = await self._publish_and_bind(
            operation_id=operation_id,
            candidate=candidate,
            pack_id=pack_id,
            version=target_version,
            manifest_hash=target_manifest_hash,
            scope=scope,
            scope_key=scope_key,
            expected_binding_generation=current.generation,
        )
        operation = await self._commit_phase(
            operation_id,
            "published",
            {"binding_id": binding.binding_id},
        )
        return CapabilityRollbackResult(
            operation=operation,
            binding=binding,
            registry_revision=publication.registry_revision,
        )

    async def uninstall(
        self,
        *,
        pack_id: str,
        scope: CapabilityScopeKind,
        scope_key: str,
        idempotency_key: str,
        root_run_id: str | None = None,
        owner_key: str | None = None,
        expected_binding_generation: int | None = None,
    ) -> CapabilityUninstallResult:
        operation_id = _operation_id(idempotency_key)
        try:
            return await self._uninstall_impl(
                pack_id=pack_id,
                scope=scope,
                scope_key=scope_key,
                idempotency_key=idempotency_key,
                root_run_id=root_run_id,
                owner_key=owner_key,
                expected_binding_generation=expected_binding_generation,
            )
        except asyncio.CancelledError:
            await self._cancel_operation(operation_id)
            raise
        except Exception as exc:
            await self._record_operation_failure(operation_id, exc)
            raise

    async def _uninstall_impl(
        self,
        *,
        pack_id: str,
        scope: CapabilityScopeKind,
        scope_key: str,
        idempotency_key: str,
        root_run_id: str | None = None,
        owner_key: str | None = None,
        expected_binding_generation: int | None = None,
    ) -> CapabilityUninstallResult:
        await self.initialize()
        binding = await self.store.get_binding(
            scope,
            scope_key,
            pack_id,
            owner_key=owner_key,
        )
        if binding is None:
            raise CapabilityManagerError("binding_not_found", "binding is absent")
        if (
            expected_binding_generation is not None
            and binding.generation != expected_binding_generation
        ):
            raise CapabilityStoreConflict(
                "binding_generation_conflict",
                "binding changed before the uninstall was prepared",
            )
        operation_id = _operation_id(idempotency_key)
        operation_request: dict[str, JsonValue] = {
            "pack_id": pack_id,
            "scope": scope,
            "scope_key": scope_key,
        }
        if owner_key is not None:
            operation_request["owner_key"] = owner_key
        if expected_binding_generation is not None:
            operation_request["expected_binding_generation"] = expected_binding_generation
        operation = await self.store.create_operation(
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            kind="uninstall",
            request=operation_request,
            root_run_id=root_run_id,
            pack_id=pack_id,
            requested_scope=scope,
            requested_scope_key=scope_key,
        )
        if operation.status == "succeeded":
            current = await self.store.get_binding(
                scope,
                scope_key,
                pack_id,
                owner_key=owner_key,
            )
            if current is None:
                raise CapabilityManagerError(
                    "completed_binding_missing", "uninstall binding projection is absent"
                )
            evidence = dict(
                await self.store.get_phase_evidence(
                    operation_id,
                    "published",
                )
                or {}
            )
            return CapabilityUninstallResult(
                operation,
                current,
                True,
                registry_revision=int(evidence.get("registry_revision") or 0),
                manager_receipt_hash=str(evidence.get("manager_receipt_hash") or ""),
                committed_owner_binding_set_stamp=str(
                    evidence.get("committed_owner_binding_set_stamp") or ""
                ),
                process_projection_fingerprint=str(
                    evidence.get("process_projection_fingerprint") or ""
                ),
            )
        await self._commit_phase(operation_id, "planned", {"pack_id": pack_id})
        for phase in ("staged", "verified", "environment_ready"):
            await self._commit_phase(operation_id, phase, {"uninstall": True})
        record = await self.store.get_version(
            binding.capability_id, binding.version, binding.manifest_hash
        )
        if record is None:
            raise CapabilityManagerError(
                "bound_version_missing", "bound immutable version is absent"
            )
        candidate = await self.publisher.prepare_uninstall(
            record, binding, operation_id=operation_id
        )
        await self._commit_phase(
            operation_id,
            "candidate_ready",
            {"uninstall": True, "removed_specs": len(candidate.old_specs)},
        )
        disabled, _publication = await self._publish_and_disable(
            operation_id=operation_id,
            candidate=candidate,
            binding=binding,
        )
        receipt_evidence = await self._manager_receipt_evidence(
            operation_id=operation_id,
            action="uninstall",
            binding=disabled,
            registry_revision=_publication.registry_revision,
            tool_spec_fingerprints=_publication.tool_spec_fingerprints,
        )
        has_lease = await self.store.version_has_active_lease(
            pack_id, binding.version, binding.manifest_hash
        )
        operation = await self._commit_phase(
            operation_id,
            "published",
            {
                "files_retained": True,
                "active_snapshot_lease": has_lease,
                "registry_revision": _publication.registry_revision,
                **receipt_evidence,
            },
        )
        # V1 retains immutable versions; a later GC owns recoverable deletion.
        return CapabilityUninstallResult(
            operation,
            disabled,
            True,
            registry_revision=_publication.registry_revision,
            manager_receipt_hash=str(receipt_evidence["manager_receipt_hash"]),
            committed_owner_binding_set_stamp=str(
                receipt_evidence["committed_owner_binding_set_stamp"]
            ),
            process_projection_fingerprint=str(receipt_evidence["process_projection_fingerprint"]),
        )

    async def _cancel_operation(self, operation_id: str) -> None:
        operation = await self.store.get_operation(operation_id)
        if operation is None or operation.status != "running":
            return
        if operation.phase in {"planned", "staged", "verified"}:
            staging = self.layout.staging_path(operation_id)
            if staging.exists():
                await self._remove_exact_tree(staging)
            await self.store.fail_operation(
                operation_id,
                status="cancelled",
                error={"code": "cancelled", "phase": operation.phase},
            )
            return
        if operation.phase in {"environment_ready", "candidate_ready"}:
            await self.store.fail_operation(
                operation_id,
                status="cancelled",
                error={
                    "code": "cancelled",
                    "phase": operation.phase,
                    "requires_environment_probe": operation.phase == "environment_ready",
                },
            )
            return
        await self.store.fail_operation(
            operation_id,
            status="unknown",
            error={
                "code": "cancelled_during_publish",
                "phase": operation.phase,
                "reconciliation_required": True,
            },
        )

    async def cancel_operation(self, operation_id: str) -> CapabilityOperationRecord:
        """Settle one caller-owned in-flight lifecycle task after cancellation.

        This does not discover or cancel arbitrary process tasks.  The caller
        must first cancel the exact asyncio task it owns, then use this method
        to durably reconcile the operation phase.
        """

        await self._cancel_operation(operation_id)
        operation = await self.store.get_operation(operation_id)
        if operation is None:
            raise CapabilityManagerError(
                "operation_not_found", f"unknown operation: {operation_id}"
            )
        return operation

    @staticmethod
    def _binding_from_intent_payload(
        payload: Mapping[str, JsonValue] | None,
    ) -> CapabilityBinding | None:
        if payload is None:
            return None
        required = {
            "binding_id",
            "capability_id",
            "version",
            "manifest_hash",
            "scope",
            "scope_key",
            "active",
            "generation",
        }
        if not required.issubset(payload):
            raise CapabilityManagerError(
                "publish_binding_projection_invalid",
                "persisted binding projection is incomplete",
            )
        return CapabilityBinding(
            binding_id=str(payload["binding_id"]),
            capability_id=str(payload["capability_id"]),
            version=str(payload["version"]),
            manifest_hash=str(payload["manifest_hash"]),
            scope=str(payload["scope"]),  # type: ignore[arg-type]
            scope_key=str(payload["scope_key"]),
            active=bool(payload["active"]),
            generation=int(payload["generation"]),
            owner_key=str(payload.get("owner_key") or ""),
            management_policy=str(payload.get("management_policy") or "legacy_import"),  # type: ignore[arg-type]
            management_generation=int(payload.get("management_generation") or 0),
        )
