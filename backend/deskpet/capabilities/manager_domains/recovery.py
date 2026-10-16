# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Publish recovery, pending global activations and binding rehydration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Any

from ..contracts import CapabilityBinding, JsonValue, fingerprint_json
from ..manifest import load_and_validate_pack
from ..store import CapabilityOperationRecord, CapabilityStoreConflict, CapabilityVersionRecord
from .core import CapabilityPackManagerCore
from .support import CapabilityManagerError, CapabilityPublication, CapabilityRehydrateFailure


class RecoveryMixin(CapabilityPackManagerCore):
    """Publish recovery, pending global activations and binding rehydration."""

    __slots__ = ()

    async def _recover_publish_operation(
        self, operation: CapabilityOperationRecord
    ) -> CapabilityOperationRecord:
        intent = await self.store.get_publish_intent_for_operation(operation.operation_id)
        if intent is None:
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={
                    "code": "publish_intent_missing",
                    "phase": operation.phase,
                },
            )

        if operation.phase == "bound" and intent.status == "committed":
            return await self._recover_uninstall_operation(intent, operation)

        try:
            reconciliation = await self.publisher.reconcile(intent)
        except Exception as exc:
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={
                    "code": "publisher_reconciliation_failed",
                    "message": str(exc),
                    "phase": operation.phase,
                },
            )
        return await self._recover_after_reconciliation(intent, operation, reconciliation)

    async def _recover_after_reconciliation(
        self, intent, operation: CapabilityOperationRecord, reconciliation
    ) -> CapabilityOperationRecord:
        if reconciliation.status == "rolled_back":
            await self.store.advance_publish_intent(
                intent.intent_id,
                phase=intent.phase,
                status="rolled_back",
            )
            return await self.store.fail_operation(
                operation.operation_id,
                status="failed",
                error={
                    "code": "publish_rolled_back",
                    "phase": operation.phase,
                },
            )
        if reconciliation.status not in {"published", "completed"}:
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={
                    "code": "publish_reconciliation_required",
                    "phase": operation.phase,
                    "publisher_status": reconciliation.status,
                },
            )

        new_binding = intent.new_binding
        required = {
            "scope",
            "scope_key",
            "capability_id",
            "version",
            "manifest_hash",
        }
        if not required.issubset(new_binding) or not all(
            isinstance(new_binding[key], str) and bool(str(new_binding[key]).strip())
            for key in required
        ):
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={
                    "code": "publish_binding_projection_invalid",
                    "phase": operation.phase,
                },
            )

        if operation.phase == "publish_intent":
            async with self.store.write_transaction() as db:
                tx = self.store.bind(db)
                await tx.advance_publish_intent(
                    intent.intent_id,
                    phase="catalog_swapped",
                    status="pending",
                )
                await tx.commit_phase(
                    operation.operation_id,
                    "catalog_swapped",
                    evidence={
                        "registry_revision": (
                            reconciliation.registry_revision
                            if reconciliation.registry_revision is not None
                            else intent.expected_registry_revision
                        ),
                        "recovered": True,
                    },
                )
            operation = await self.store.get_operation(operation.operation_id) or operation

        if operation.phase == "catalog_swapped":
            scope = str(new_binding["scope"])
            scope_key = str(new_binding["scope_key"])
            capability_id = str(new_binding["capability_id"])
            current = await self.store.get_binding(
                scope,
                scope_key,
                capability_id,
                owner_key=(
                    None if new_binding.get("owner_key") is None else str(new_binding["owner_key"])
                ),
            )
            try:
                old_binding = self._binding_from_intent_payload(intent.old_binding)
            except Exception as exc:
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="unknown",
                    error={
                        "code": str(
                            getattr(
                                exc,
                                "code",
                                "publish_binding_projection_invalid",
                            )
                        ),
                        "message": str(exc),
                        "phase": operation.phase,
                    },
                )
            if (old_binding is None and current is not None) or (
                old_binding is not None
                and (current is None or current.fingerprint != old_binding.fingerprint)
            ):
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="unknown",
                    error={
                        "code": "binding_reconciliation_conflict",
                        "phase": operation.phase,
                    },
                )
            expected_generation = 0 if old_binding is None else old_binding.generation
            enabled = bool(new_binding.get("active", True))
            try:
                await self._recover_bind_publication(
                    capability_id,
                    enabled=enabled,
                    expected_generation=expected_generation,
                    intent=intent,
                    new_binding=new_binding,
                    operation=operation,
                    scope=scope,
                    scope_key=scope_key,
                )
            except CapabilityStoreConflict as exc:
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="unknown",
                    error={
                        "code": "binding_reconciliation_conflict",
                        "message": str(exc),
                        "phase": operation.phase,
                    },
                )
            operation = await self.store.get_operation(operation.operation_id) or operation

        return await self._recover_unbound_operation(intent, new_binding, operation)

    async def _recover_uninstall_operation(
        self, intent, operation: CapabilityOperationRecord
    ) -> CapabilityOperationRecord:
        if operation.kind == "uninstall":
            try:
                old_binding = self._binding_from_intent_payload(intent.old_binding)
                if old_binding is None:
                    raise CapabilityManagerError(
                        "publish_binding_projection_invalid",
                        "uninstall recovery is missing the old binding",
                    )
                await self.publisher.stop_binding(old_binding)
            except Exception as exc:
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="unknown",
                    error={
                        "code": str(
                            getattr(
                                exc,
                                "code",
                                "runtime_stop_reconciliation_failed",
                            )
                        ),
                        "message": str(exc),
                        "phase": operation.phase,
                    },
                )
        new_payload = intent.new_binding
        new_binding = await self.store.get_binding(
            str(new_payload.get("scope") or ""),
            str(new_payload.get("scope_key") or ""),
            str(new_payload.get("capability_id") or ""),
            owner_key=(
                None if new_payload.get("owner_key") is None else str(new_payload["owner_key"])
            ),
        )
        if (
            new_binding is None
            or new_binding.version != new_payload.get("version")
            or new_binding.manifest_hash != new_payload.get("manifest_hash")
            or new_binding.active != bool(new_payload.get("active", True))
        ):
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={
                    "code": "publish_binding_projection_invalid",
                    "phase": operation.phase,
                },
            )
        version = await self.store.get_version(
            new_binding.capability_id,
            new_binding.version,
            new_binding.manifest_hash,
        )
        if version is None:
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={
                    "code": "completed_version_missing",
                    "phase": operation.phase,
                },
            )
        catalog_evidence = dict(
            await self.store.get_phase_evidence(
                operation.operation_id,
                "catalog_swapped",
            )
            or {}
        )
        receipt_evidence = await self._manager_receipt_evidence(
            operation_id=operation.operation_id,
            action=operation.kind,
            binding=new_binding,
            registry_revision=int(
                catalog_evidence.get("registry_revision") or intent.expected_registry_revision
            ),
            tool_spec_fingerprints=(version.expected_tool_fingerprints),
        )
        return await self.store.commit_phase(
            operation.operation_id,
            "published",
            evidence={
                "recovered": True,
                "intent_id": intent.intent_id,
                **receipt_evidence,
            },
        )

    async def _recover_bind_publication(
        self,
        capability_id,
        *,
        enabled,
        expected_generation,
        intent,
        new_binding,
        operation: CapabilityOperationRecord,
        scope,
        scope_key,
    ) -> None:
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            binding = await tx.set_binding(
                scope=scope,
                scope_key=scope_key,
                pack_id=capability_id,
                version=str(new_binding["version"]),
                manifest_hash=str(new_binding["manifest_hash"]),
                expected_generation=expected_generation,
                enabled=enabled,
                owner_key=(
                    None if new_binding.get("owner_key") is None else str(new_binding["owner_key"])
                ),
                management_policy=(
                    None
                    if new_binding.get("management_policy") is None
                    else str(new_binding["management_policy"])
                ),
            )
            await tx.advance_publish_intent(intent.intent_id, phase="bound", status="committed")
            await tx.commit_phase(
                operation.operation_id,
                "bound",
                evidence={
                    "binding_id": binding.binding_id,
                    "recovered": True,
                },
            )

    async def _recover_unbound_operation(
        self, intent, new_binding, operation: CapabilityOperationRecord
    ) -> CapabilityOperationRecord:
        if operation.phase != "bound":
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={
                    "code": "publish_reconciliation_phase_invalid",
                    "phase": operation.phase,
                },
            )
        if operation.kind == "uninstall":
            try:
                old_binding = self._binding_from_intent_payload(intent.old_binding)
                if old_binding is None:
                    raise CapabilityManagerError(
                        "publish_binding_projection_invalid",
                        "uninstall recovery is missing the old binding",
                    )
                await self.publisher.stop_binding(old_binding)
            except Exception as exc:
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="unknown",
                    error={
                        "code": str(
                            getattr(
                                exc,
                                "code",
                                "runtime_stop_reconciliation_failed",
                            )
                        ),
                        "message": str(exc),
                        "phase": operation.phase,
                    },
                )
        published_evidence: dict[str, JsonValue] = {
            "recovered": True,
            "intent_id": intent.intent_id,
        }
        if operation.kind != "uninstall":
            current_binding = await self.store.get_binding(
                str(new_binding["scope"]),
                str(new_binding["scope_key"]),
                str(new_binding["capability_id"]),
                owner_key=(
                    None if new_binding.get("owner_key") is None else str(new_binding["owner_key"])
                ),
            )
            if current_binding is None:
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="unknown",
                    error={
                        "code": "completed_binding_missing",
                        "phase": operation.phase,
                    },
                )
            version = await self.store.get_version(
                current_binding.capability_id,
                current_binding.version,
                current_binding.manifest_hash,
            )
            if version is None:
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="unknown",
                    error={
                        "code": "completed_version_missing",
                        "phase": operation.phase,
                    },
                )
            catalog_evidence = dict(
                await self.store.get_phase_evidence(
                    operation.operation_id,
                    "catalog_swapped",
                )
                or {}
            )
            published_evidence.update(
                await self._manager_receipt_evidence(
                    operation_id=operation.operation_id,
                    action=operation.kind,
                    binding=current_binding,
                    registry_revision=int(
                        catalog_evidence.get("registry_revision")
                        or intent.expected_registry_revision
                    ),
                    tool_spec_fingerprints=(version.expected_tool_fingerprints),
                )
            )
        return await self.store.commit_phase(
            operation.operation_id,
            "published",
            evidence=published_evidence,
        )

    async def recover(self) -> tuple[CapabilityOperationRecord, ...]:
        """Probe or reconcile every nonterminal operation without blind replay."""

        await self.initialize()
        await self._recover_pending_global_activations()
        recovered: list[CapabilityOperationRecord] = []
        for operation in await self.store.list_recoverable_operations():
            if operation.kind == "skill_install_batch":
                async with self.publish_lock:
                    recovered.append(await self._recover_skill_install_batch(operation))
                continue
            staging = self.layout.staging_path(operation.operation_id)
            if operation.phase in {"planned", "staged", "verified"}:
                if staging.exists():
                    await self._remove_exact_tree(staging)
                recovered.append(
                    await self.store.fail_operation(
                        operation.operation_id,
                        status="cancelled",
                        error={
                            "code": "recovered_pre_publish",
                            "phase": operation.phase,
                        },
                    )
                )
                continue
            if operation.phase in {"environment_ready", "candidate_ready"}:
                probe = await self.environment_preparer.probe(operation, environment_root=None)
                recovered.append(
                    await self.store.fail_operation(
                        operation.operation_id,
                        status="unknown",
                        error={
                            "code": "candidate_recovery_requires_restart",
                            "phase": operation.phase,
                            "probe": dict(probe),
                        },
                    )
                )
                continue
            async with self.publish_lock:
                recovered.append(await self._recover_publish_operation(operation))
        return tuple(recovered)

    async def _recover_pending_global_activations(self) -> None:
        """Settle the durable registry-published/binding-not-yet-written window."""
        async with self.store.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT operation_id,evidence_json
                       FROM capability_operation_phase_evidence
                       WHERE phase='global_activation' AND status='intent'
                       ORDER BY created_at"""
                )
            ).fetchall()
        for row in rows:
            operation_id = str(row["operation_id"])
            evidence = json.loads(str(row["evidence_json"]))
            publish_intent = await self.store.get_publish_intent_for_operation(operation_id)
            if publish_intent is None:
                continue
            reconciliation = await self.publisher.reconcile(publish_intent)
            if reconciliation.status == "rolled_back":
                # The registry still represents the old snapshot.  Preserve
                # every old binding and durably close this activation attempt.
                async with self.store.write_transaction() as db:
                    await db.execute(
                        """UPDATE capability_operation_phase_evidence
                           SET status='failed',updated_at=?
                           WHERE operation_id=? AND phase='global_activation'
                             AND status='intent'""",
                        (self.store.now(), operation_id),
                    )
                continue
            if reconciliation.status != "published":
                continue
            owner_key = str(evidence.get("owner_key") or "")
            members = evidence.get("members")
            if not owner_key.startswith("user:v2:") or not isinstance(members, list):
                continue
            bindings: list[CapabilityBinding] = []
            async with self.store.write_transaction() as db:
                tx = self.store.bind(db)
                for raw in members:
                    if not isinstance(raw, Mapping):
                        raise CapabilityManagerError(
                            "global_activation_intent_invalid", operation_id
                        )
                    binding = await tx.set_binding(
                        scope="user",
                        scope_key=owner_key,
                        pack_id=str(raw["pack_id"]),
                        version=str(raw["version"]),
                        manifest_hash=str(raw["manifest_hash"]),
                        expected_generation=int(raw.get("expected_binding_generation") or 0),
                        enabled=True,
                        owner_key=owner_key,
                        management_policy="user_managed",
                    )
                    bindings.append(binding)
                settled = {
                    **evidence,
                    "registry_revision": int(reconciliation.registry_revision or 0),
                    "binding_ids": [binding.binding_id for binding in bindings],
                    "recovered": True,
                }
                await db.execute(
                    """UPDATE capability_operation_phase_evidence
                       SET status='committed',evidence_json=?,updated_at=?
                       WHERE operation_id=? AND phase='global_activation'
                         AND status='intent'""",
                    (
                        json.dumps(settled, sort_keys=True),
                        self.store.now(),
                        operation_id,
                    ),
                )

    async def recover_pending_global_activations(self) -> None:
        async with self.publish_lock:
            await self._recover_pending_global_activations()

    async def _recover_skill_install_batch(
        self, operation: CapabilityOperationRecord
    ) -> CapabilityOperationRecord:
        staging = self.layout.staging_path(operation.operation_id)
        stamp = str(operation.request.get("member_set_stamp") or "")
        batch_root = (self.layout.packs / "batches" / stamp).resolve(strict=False)
        if operation.phase in {"batch_staged", "batch_prepared"}:
            if staging.exists():
                await self._remove_exact_tree(staging)
            return await self.store.fail_operation(
                operation.operation_id,
                status="cancelled",
                error={"code": "recovered_pre_publish", "phase": operation.phase},
            )
        intent = await self.store.get_publish_intent_for_operation(operation.operation_id)
        if intent is None:
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={"code": "batch_publish_intent_missing", "phase": operation.phase},
            )
        try:
            reconciliation = await self.publisher.reconcile(intent)
        except Exception as exc:
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={
                    "code": str(getattr(exc, "code", "batch_reconcile_failed")),
                    "phase": operation.phase,
                },
            )
        if reconciliation.status == "rolled_back":
            if staging.exists():
                await self._remove_exact_tree(staging)
            if batch_root.exists():
                await self._remove_batch_root(batch_root)
            await self.store.advance_publish_intent(
                intent.intent_id, phase=intent.phase, status="rolled_back"
            )
            return await self.store.fail_operation(
                operation.operation_id,
                status="failed",
                error={"code": "batch_publish_rolled_back", "phase": operation.phase},
            )
        if reconciliation.status != "published":
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={"code": "batch_publish_outcome_unknown", "phase": operation.phase},
            )
        # A full-new registry with an absent or partial content root cannot be
        # made authoritative; fence it rather than guessing member state.
        members = await self.store.operation_members(operation.operation_id)
        roots = tuple(batch_root / f"{m.ordinal:04d}-{m.normalized_name}" for m in members)
        if not batch_root.is_dir() or not all(root.is_dir() for root in roots):
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={"code": "batch_materialization_mixed", "phase": operation.phase},
            )
        publish_members = await self.store.publish_intent_members(intent.intent_id)
        if len(publish_members) != len(members):
            return await self.store.fail_operation(
                operation.operation_id,
                status="unknown",
                error={"code": "batch_publish_members_mixed", "phase": operation.phase},
            )
        validations = [load_and_validate_pack(root, environment=self.environment) for root in roots]
        candidates = [
            await self.publisher.prepare_candidate(
                v, install_root=root, operation_id=operation.operation_id
            )
            for v, root in zip(validations, roots, strict=True)
        ]
        for validation in validations:
            existing_version = await self.store.get_version(
                validation.manifest.id,
                validation.manifest.version,
            )
            if existing_version is not None and (
                existing_version.descriptor.manifest_hash != validation.manifest.manifest_hash
                or existing_version.descriptor.fingerprint != validation.descriptor.fingerprint
            ):
                if batch_root.exists():
                    await self._remove_batch_root(batch_root)
                await self.store.advance_publish_intent(
                    intent.intent_id,
                    phase=intent.phase,
                    status="rolled_back",
                )
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="failed",
                    error={
                        "code": "capability_version_conflict",
                        "phase": operation.phase,
                        "recovered": True,
                    },
                )
        bindings: list[CapabilityBinding] = []
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            for member, published, validation, candidate, root in zip(
                members, publish_members, validations, candidates, roots, strict=True
            ):
                expected = int(published.new_binding.get("expected_generation") or 0)
                await tx.record_version(
                    CapabilityVersionRecord(
                        validation.descriptor,
                        root,
                        "healthy",
                        candidate.tool_spec_fingerprints,
                        None,
                        None,
                        None,
                        self.store.now(),
                    )
                )
                binding = await tx.set_binding(
                    scope="project",
                    scope_key=operation.requested_scope_key or "",
                    pack_id=member.pack_id,
                    version=member.version,
                    manifest_hash=member.manifest_hash,
                    expected_generation=expected,
                    owner_key=str(published.new_binding.get("owner_key") or ""),
                    management_policy="user_managed",
                )
                bindings.append(binding)
                await db.execute(
                    """UPDATE capability_operation_members SET committed_version=?,
                       committed_manifest_hash=?,committed_set_stamp=?
                       WHERE operation_id=? AND ordinal=?""",
                    (
                        member.version,
                        member.manifest_hash,
                        stamp,
                        operation.operation_id,
                        member.ordinal,
                    ),
                )
            registry_revision = int(
                reconciliation.registry_revision or intent.expected_registry_revision
            )
            receipt_payload = {
                "schema": "capability-batch-manager-receipt-v1",
                "operation_id": operation.operation_id,
                "project_scope_key": operation.requested_scope_key or "",
                "owner_key": str(publish_members[0].new_binding.get("owner_key") or ""),
                "committed_set_stamp": stamp,
                "registry_revision": registry_revision,
                "members": [
                    {
                        "pack_id": m.pack_id,
                        "version": m.version,
                        "manifest_hash": m.manifest_hash,
                        "content_hash": m.content_hash,
                        "binding_generation": b.generation,
                    }
                    for m, b in zip(members, bindings, strict=True)
                ],
            }
            receipt_hash = fingerprint_json(receipt_payload)
            if intent.phase == "batch_files_materialized":
                await tx.advance_publish_intent(intent.intent_id, phase="batch_catalog_swapped")
                await tx.commit_phase(
                    operation.operation_id,
                    "batch_catalog_swapped",
                    evidence={"registry_revision": registry_revision, "recovered": True},
                )
            await tx.advance_publish_intent(
                intent.intent_id, phase="batch_committed", status="committed"
            )
            return await tx.commit_phase(
                operation.operation_id,
                "batch_committed",
                evidence={
                    **receipt_payload,
                    "manager_receipt_hash": receipt_hash,
                    "install_root": str(batch_root),
                    "recovered": True,
                },
            )

    async def rehydrate_active_bindings(
        self,
    ) -> tuple[CapabilityPublication, ...]:
        """Rebuild the process-local registry from durable active bindings.

        One installed pack that cannot be rebuilt (its directory is gone, its files or
        tool fingerprints no longer match) is skipped and kept in ``rehydrate_failures``
        by name; the other packs and the rest of the app still start (试用前 2026-10-08
        用户定：跳过坏包、在技能中心标"不可用"). The skipped pack is never published,
        so nothing of it can run."""

        await self.initialize()
        publications: list[CapabilityPublication] = []
        failures: list[CapabilityRehydrateFailure] = []
        async with self.publish_lock:
            for record in await self.store.list_active_versions():
                try:
                    publication = await self._rehydrate_one(record)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - one pack's fault is that pack's
                    code = str(getattr(error, "code", "") or type(error).__name__)
                    failures.append(
                        CapabilityRehydrateFailure(
                            record=record, code=code, message=str(error)[:300]
                        )
                    )
                    # 日志记录器名字钉在拆分前的模块名
                    logging.getLogger("deskpet.capabilities.manager").error(
                        "capability_rehydrate_skipped pack=%s version=%s code=%s error=%s",
                        record.descriptor.capability_id,
                        record.descriptor.version,
                        code,
                        str(error)[:300],
                    )
                    continue
                publications.append(publication)
        self.rehydrate_failures = tuple(failures)
        return tuple(publications)

    async def _rehydrate_one(self, record: Any) -> CapabilityPublication:
        operation_id = hashlib.sha256(
            (
                "capability-rehydrate|"
                f"{record.descriptor.capability_id}|"
                f"{record.descriptor.version}|"
                f"{record.descriptor.manifest_hash}"
            ).encode()
        ).hexdigest()
        prepare_rehydrate = getattr(
            self.publisher,
            "prepare_installed_for_rehydrate",
            None,
        )
        if callable(prepare_rehydrate):
            candidate, legacy_schema = await prepare_rehydrate(
                record,
                operation_id=operation_id,
            )
            if legacy_schema is not None:
                await self.store.migrate_version_tool_fingerprint_schema(
                    pack_id=record.descriptor.capability_id,
                    version=record.descriptor.version,
                    manifest_hash=record.descriptor.manifest_hash,
                    expected_fingerprints=(record.expected_tool_fingerprints),
                    replacement_fingerprints=(candidate.tool_spec_fingerprints),
                )
        else:
            candidate = await self.publisher.prepare_installed(
                record,
                operation_id=operation_id,
            )
        return await self.publisher.publish(
            candidate,
            operation_id=operation_id,
        )
