# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Canonical batch publish, install, update and repair."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import CapabilityBinding, CapabilityScopeKind, JsonValue, fingerprint_json
from ..manifest import PackValidationResult, load_and_validate_pack, windows_extended_path
from ..package_limits import ValidatedCapabilityPackageRefV1
from ..skill_source import CanonicalSkillBatch
from ..source import PackSourceRequest
from ..store import (
    CapabilityOperationRecord,
    CapabilityPublishIntentMember,
    CapabilityStoreConflict,
    CapabilityVersionRecord,
)
from .core import CapabilityPackManagerCore
from .support import (
    CapabilityBatchInstallResult,
    CapabilityCandidate,
    CapabilityInstallResult,
    CapabilityManagerError,
    CapabilityPublication,
    _intent_id,
    _operation_id,
)


def _manifest_source_matches(*, requested_source_type: str, manifest_source_type: str) -> bool:
    """Match logical archive transports to their legacy manifest identity."""

    if requested_source_type in {"local_archive", "companion_growth"}:
        return manifest_source_type in {requested_source_type, "local"}
    return manifest_source_type == requested_source_type


@dataclass
class _InstallState:
    """InstallMixin.install 的运行状态：原函数的局部变量与参数（切段用）。"""

    result: Any = None
    flow: Any = None
    actual_generation: Any = None
    binding: Any = None
    candidate: Any = None
    current_binding: Any = None
    derived_from_receipt_ref: Any = None
    expected_binding_generation: Any = None
    expected_generation: Any = None
    expected_pack_id: Any = None
    generated: Any = None
    idempotency_key: Any = None
    install_path: Any = None
    kind: Any = None
    lifecycle_request_fields: Any = None
    management_policy: Any = None
    manifest: Any = None
    operation: Any = None
    operation_id: Any = None
    owner_key: Any = None
    parent_manifest_hash: Any = None
    parent_version: Any = None
    publication: Any = None
    request: Any = None
    resolved_source: Any = None
    root_run_id: Any = None
    scope: Any = None
    scope_key: Any = None
    source: Any = None
    staging_path: Any = None
    stored_error: Any = None
    validated_ref: Any = None
    validation: Any = None


class InstallMixin(CapabilityPackManagerCore):
    """Canonical batch publish, install, update and repair."""

    __slots__ = ()

    async def _publish_canonical_skill_install_batch(
        self,
        batch: CanonicalSkillBatch,
        *,
        operation_id: str,
        project_scope_key: str,
        owner_key: str,
        committed_set_stamp: str,
        expected_binding_generations: Mapping[str, int] | None = None,
    ) -> CapabilityBatchInstallResult:
        """Validate and atomically publish one frozen canonical Skill batch.

        The source boundary supplies only canonical archives and their issued
        package refs.  Candidate construction, registry mutation, binding CAS,
        and the Manager receipt remain Manager-owned.
        """

        await self.initialize()
        operation = await self.store.get_operation(operation_id)
        if operation is None or operation.kind != "skill_install_batch":
            raise CapabilityManagerError("batch_operation_missing", operation_id)
        expected_scope = "user" if project_scope_key.startswith("user:v2:") else "project"
        if (
            operation.requested_scope != expected_scope
            or operation.requested_scope_key != project_scope_key
        ):
            raise CapabilityManagerError(
                "batch_owner_scope_mismatch", "batch operation belongs to another owner scope"
            )
        if str(operation.request.get("member_set_stamp") or "") != committed_set_stamp:
            raise CapabilityManagerError("batch_set_stamp_mismatch", "confirmed member set changed")
        receipt_evidence = await self.store.get_phase_evidence(operation_id, "batch_committed")
        if operation.status == "succeeded" and receipt_evidence is not None:
            bindings: list[CapabilityBinding] = []
            for member in await self.store.operation_members(operation_id):
                binding = await self.store.get_binding(
                    expected_scope, project_scope_key, member.pack_id, owner_key=owner_key
                )
                if binding is None and expected_scope != "user":
                    raise CapabilityManagerError("batch_committed_binding_missing", member.pack_id)
                if binding is not None:
                    bindings.append(binding)
            return CapabilityBatchInstallResult(
                operation,
                tuple(bindings),
                Path(str(receipt_evidence["install_root"])),
                int(receipt_evidence["registry_revision"]),
                str(receipt_evidence["manager_receipt_hash"]),
                committed_set_stamp,
            )
        if operation.status != "running" or operation.phase != "batch_staged":
            raise CapabilityManagerError(
                "batch_reconciliation_required", f"batch is {operation.status}/{operation.phase}"
            )

        durable_members = await self.store.operation_members(operation_id)
        if len(durable_members) != len(batch.packs):
            raise CapabilityManagerError(
                "batch_member_count_mismatch", "confirmed member count changed"
            )
        staging_root = self.layout.staging_path(operation_id)
        if staging_root.exists():
            await self._remove_exact_tree(staging_root)
        staging_root.mkdir(parents=True)
        validations: list[PackValidationResult] = []
        candidates: list[CapabilityCandidate] = []
        try:
            for ordinal, (frozen, durable) in enumerate(
                zip(batch.packs, durable_members, strict=True)
            ):
                member_root, validation = await self._batch_member_root(
                    durable, frozen, ordinal, staging_root
                )
                candidate = await self.publisher.prepare_candidate(
                    validation, install_root=member_root, operation_id=operation_id
                )
                if len(candidate.tool_spec_fingerprints) != len(validation.manifest.tools):
                    raise CapabilityManagerError("candidate_tool_mismatch", durable.normalized_name)
                validations.append(validation)
                candidates.append(candidate)
                await self._fault(f"after:batch_member_prepared:{ordinal}")
            await self._require_batch_versions_absent(validations)
            operation = await self._commit_phase(
                operation_id,
                "batch_prepared",
                {"member_count": len(durable_members), "committed_set_stamp": committed_set_stamp},
            )
            await self._fault("after:batch_prepared")
        except Exception as exc:
            await self._record_operation_failure(operation_id, exc)
            raise

        batch_root, combined = self._batch_publish_root(candidates, committed_set_stamp)
        generations = dict(expected_binding_generations or {})
        binding_scope = "user" if project_scope_key.startswith("user:v2:") else "project"
        async with self.publish_lock:
            old_bindings: list[CapabilityBinding | None] = []
            for member in durable_members:
                binding = await self.store.get_binding(
                    binding_scope, project_scope_key, member.pack_id, owner_key=owner_key
                )
                actual = 0 if binding is None else binding.generation
                if generations.get(member.pack_id, actual) != actual:
                    raise CapabilityStoreConflict("binding_generation_conflict", member.pack_id)
                generations[member.pack_id] = actual
                old_bindings.append(binding)
            intent_id = _intent_id(operation_id)
            db, publication, tx = await self._batch_publish_envelope(
                batch_root,
                binding_scope=binding_scope,
                combined=combined,
                committed_set_stamp=committed_set_stamp,
                durable_members=durable_members,
                generations=generations,
                intent_id=intent_id,
                old_bindings=old_bindings,
                operation_id=operation_id,
                owner_key=owner_key,
                project_scope_key=project_scope_key,
                staging_root=staging_root,
            )
            bindings, operation, receipt_hash = await self._batch_publish_commit(
                batch_root,
                binding_scope=binding_scope,
                candidates=candidates,
                committed_set_stamp=committed_set_stamp,
                durable_members=durable_members,
                generations=generations,
                intent_id=intent_id,
                operation_id=operation_id,
                owner_key=owner_key,
                project_scope_key=project_scope_key,
                publication=publication,
                validations=validations,
            )
        return CapabilityBatchInstallResult(
            operation,
            tuple(bindings),
            batch_root,
            publication.registry_revision,
            receipt_hash,
            committed_set_stamp,
        )

    async def _require_batch_versions_absent(self, validations: list[PackValidationResult]) -> None:
        for validation in validations:
            existing_version = await self.store.get_version(
                validation.manifest.id,
                validation.manifest.version,
            )
            if existing_version is not None and (
                existing_version.descriptor.manifest_hash != validation.manifest.manifest_hash
                or existing_version.descriptor.fingerprint != validation.descriptor.fingerprint
            ):
                raise CapabilityStoreConflict(
                    "capability_version_conflict",
                    "same capability version already exists with different immutable data",
                )

    async def _batch_member_root(self, durable, frozen, ordinal, staging_root):
        if (
            frozen.skill_name != durable.normalized_name
            or frozen.manifest_hash != durable.manifest_hash
            or frozen.content_digest != durable.content_hash
        ):
            raise CapabilityManagerError(
                "batch_member_identity_mismatch",
                f"member {ordinal} differs from confirmation",
            )
        member_root = staging_root / f"{ordinal:04d}-{durable.normalized_name}"
        issued = await asyncio.to_thread(
            self.package_validator.materialize_zip,
            frozen.archive_bytes,
            member_root,
            source_kind=frozen.validated_ref.source_kind,
        )
        if issued != frozen.validated_ref:
            raise CapabilityManagerError("batch_validated_ref_mismatch", durable.normalized_name)
        validation = load_and_validate_pack(member_root, environment=self.environment)
        if (
            validation.manifest.id != durable.pack_id
            or validation.manifest.version != durable.version
            or validation.manifest.manifest_hash != durable.manifest_hash
        ):
            raise CapabilityManagerError(
                "batch_manifest_identity_mismatch", durable.normalized_name
            )
        return member_root, validation

    def _batch_publish_root(self, candidates: list[CapabilityCandidate], committed_set_stamp: str):
        expected_revisions = {candidate.expected_registry_revision for candidate in candidates}
        if len(expected_revisions) != 1:
            raise CapabilityManagerError(
                "batch_registry_revision_mismatch",
                "members were prepared against different registry revisions",
            )
        runtime_payload: object | None = None
        if all(isinstance(candidate.runtime_payload, tuple) for candidate in candidates):
            runtime_payload = tuple(
                item for candidate in candidates for item in candidate.runtime_payload
            )  # type: ignore[union-attr]
        combined = CapabilityCandidate(
            expected_registry_revision=next(iter(expected_revisions), 0),
            tool_spec_fingerprints=tuple(
                fp for candidate in candidates for fp in candidate.tool_spec_fingerprints
            ),
            old_specs=tuple(spec for candidate in candidates for spec in candidate.old_specs),
            new_specs=tuple(spec for candidate in candidates for spec in candidate.new_specs),
            publisher_state={
                "schema": "capability-batch-publisher-state-v1",
                "member_count": len(candidates),
            },
            runtime_payload=runtime_payload,
        )
        batch_root = (self.layout.packs / "batches" / committed_set_stamp).resolve(strict=False)
        return batch_root, combined

    async def _batch_publish_envelope(
        self,
        batch_root,
        *,
        binding_scope,
        combined,
        committed_set_stamp: str,
        durable_members,
        generations,
        intent_id,
        old_bindings: list[CapabilityBinding | None],
        operation_id: str,
        owner_key: str,
        project_scope_key: str,
        staging_root,
    ):
        envelope: Mapping[str, JsonValue] = {
            "schema": (
                "capability-batch-binding-v2"
                if binding_scope == "user"
                else "capability-batch-binding-v1"
            ),
            "project_scope_key": project_scope_key,
            "scope": binding_scope,
            "scope_key": project_scope_key,
            "owner_key": owner_key,
            "member_set_digest": committed_set_stamp,
            "expected_binding_generations": generations,
        }
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.create_publish_intent(
                intent_id=intent_id,
                operation_id=operation_id,
                expected_registry_revision=combined.expected_registry_revision,
                old_specs=combined.old_specs,
                new_specs=combined.new_specs,
                old_binding=None,
                new_binding=envelope,
            )
            await tx.put_publish_intent_members_in_transaction(
                intent_id,
                tuple(
                    CapabilityPublishIntentMember(
                        intent_id,
                        operation_id,
                        member.ordinal,
                        None if old is None else old.to_dict(),
                        {
                            "scope": binding_scope,
                            "scope_key": project_scope_key,
                            "owner_key": owner_key,
                            "capability_id": member.pack_id,
                            "version": member.version,
                            "manifest_hash": member.manifest_hash,
                            "active": True,
                            "expected_generation": generations[member.pack_id],
                        },
                    )
                    for member, old in zip(durable_members, old_bindings, strict=True)
                ),
            )
            await tx.commit_phase(
                operation_id, "batch_publish_intent", evidence={"intent_id": intent_id}
            )
        await self._fault("after:batch_publish_intent")
        batch_root.parent.mkdir(parents=True, exist_ok=True)
        if batch_root.exists():
            await self._remove_batch_root(batch_root)
        await asyncio.to_thread(os.replace, staging_root, batch_root)
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.advance_publish_intent(intent_id, phase="batch_files_materialized")
            await tx.commit_phase(
                operation_id,
                "batch_files_materialized",
                evidence={"batch_root": str(batch_root)},
            )
        await self._fault("after:batch_files_materialized")
        if binding_scope == "user":
            # User-global candidates remain private until the fresh-Run
            # verifier accepts the exact installed versions.  In
            # particular, do not mutate the process-wide executable Tool
            # registry merely because the binding is still invisible.
            publication = CapabilityPublication(
                registry_revision=combined.expected_registry_revision,
                tool_spec_fingerprints=combined.tool_spec_fingerprints,
                evidence={"publication_state": "deferred_pending_verification"},
            )
        else:
            publication = await self.publisher.publish(combined, operation_id=operation_id)
            if tuple(publication.tool_spec_fingerprints) != combined.tool_spec_fingerprints:
                raise CapabilityManagerError(
                    "publisher_fingerprint_mismatch", "batch ToolSpecs differ"
                )
        return db, publication, tx

    async def _batch_publish_commit(
        self,
        batch_root,
        *,
        binding_scope,
        candidates: list[CapabilityCandidate],
        committed_set_stamp: str,
        durable_members,
        generations,
        intent_id,
        operation_id: str,
        owner_key: str,
        project_scope_key: str,
        publication,
        validations: list[PackValidationResult],
    ):
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.advance_publish_intent(intent_id, phase="batch_catalog_swapped")
            await tx.commit_phase(
                operation_id,
                "batch_catalog_swapped",
                evidence={
                    "registry_revision": publication.registry_revision,
                    "publication_state": (
                        "deferred_pending_verification" if binding_scope == "user" else "active"
                    ),
                },
            )
        await self._fault("after:batch_catalog_swapped")
        bindings: list[CapabilityBinding] = []
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            for durable, validation, candidate in zip(
                durable_members, validations, candidates, strict=True
            ):
                install_path = batch_root / f"{durable.ordinal:04d}-{durable.normalized_name}"
                await tx.record_version(
                    CapabilityVersionRecord(
                        validation.descriptor,
                        install_path,
                        "healthy",
                        candidate.tool_spec_fingerprints,
                        None,
                        None,
                        None,
                        self.store.now(),
                    )
                )
                if binding_scope == "project":
                    binding = await tx.set_binding(
                        scope=binding_scope,
                        scope_key=project_scope_key,
                        pack_id=durable.pack_id,
                        version=durable.version,
                        manifest_hash=durable.manifest_hash,
                        expected_generation=generations[durable.pack_id],
                        enabled=True,
                        owner_key=owner_key,
                        management_policy="user_managed",
                    )
                    bindings.append(binding)
                await db.execute(
                    """UPDATE capability_operation_members SET committed_version=?,
                           committed_manifest_hash=?,committed_set_stamp=?
                           WHERE operation_id=? AND ordinal=?""",
                    (
                        durable.version,
                        durable.manifest_hash,
                        committed_set_stamp,
                        operation_id,
                        durable.ordinal,
                    ),
                )
            receipt_payload = {
                "schema": (
                    "capability-batch-manager-receipt-v2"
                    if binding_scope == "user"
                    else "capability-batch-manager-receipt-v1"
                ),
                "operation_id": operation_id,
                "project_scope_key": project_scope_key,
                "scope": binding_scope,
                "scope_key": project_scope_key,
                "owner_key": owner_key,
                "committed_set_stamp": committed_set_stamp,
                "registry_revision": publication.registry_revision,
                "publication_state": ("pending_invisible" if binding_scope == "user" else "active"),
                "members": [
                    {
                        "pack_id": m.pack_id,
                        "version": m.version,
                        "manifest_hash": m.manifest_hash,
                        "content_hash": m.content_hash,
                        "expected_binding_generation": generations[m.pack_id],
                        **(
                            {"binding_generation": b.generation}
                            if binding_scope == "project"
                            else {}
                        ),
                    }
                    for m, b in zip(
                        durable_members,
                        bindings if binding_scope == "project" else [None] * len(durable_members),
                        strict=True,
                    )
                ],
            }
            receipt_hash = fingerprint_json(receipt_payload)
            await tx.advance_publish_intent(intent_id, phase="batch_committed", status="committed")
            operation = await tx.commit_phase(
                operation_id,
                "batch_committed",
                evidence={
                    **receipt_payload,
                    "manager_receipt_hash": receipt_hash,
                    "install_root": str(batch_root),
                },
            )
        await self._fault("after:batch_committed")
        return bindings, operation, receipt_hash

    async def _remove_batch_root(self, target: Path) -> None:
        target = target.resolve(strict=False)
        root = (self.layout.packs / "batches").resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise CapabilityManagerError("unsafe_cleanup_target", str(target)) from exc
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target)

    async def install(
        self,
        source: PackSourceRequest,
        *,
        scope: CapabilityScopeKind,
        scope_key: str,
        idempotency_key: str,
        root_run_id: str | None = None,
        generated: bool = False,
        expected_pack_id: str | None = None,
        parent_version: str | None = None,
        parent_manifest_hash: str | None = None,
        derived_from_receipt_ref: str | None = None,
        kind: str = "install",
        owner_key: str | None = None,
        expected_binding_generation: int | None = None,
        management_policy: str | None = None,
    ) -> CapabilityInstallResult:
        state = _InstallState(
            source=source,
            scope=scope,
            scope_key=scope_key,
            idempotency_key=idempotency_key,
            root_run_id=root_run_id,
            generated=generated,
            expected_pack_id=expected_pack_id,
            parent_version=parent_version,
            parent_manifest_hash=parent_manifest_hash,
            derived_from_receipt_ref=derived_from_receipt_ref,
            kind=kind,
            owner_key=owner_key,
            expected_binding_generation=expected_binding_generation,
            management_policy=management_policy,
        )
        await self._require_install_kind(
            state.derived_from_receipt_ref,
            state.kind,
            state.parent_manifest_hash,
            state.parent_version,
        )
        state.resolved_source = self.source_resolver.resolve_declared_source(state.source)
        state.operation_id = _operation_id(state.idempotency_key)
        state.request: Mapping[str, JsonValue] = {
            "source": {
                "type": state.source.source_type,
                "uri": state.source.uri,
                "revision": state.source.revision,
                "subdirectory": state.source.subdirectory,
            },
            "scope": state.scope,
            "scope_key": state.scope_key,
            "generated": state.generated,
            "expected_pack_id": state.expected_pack_id,
            "parent_version": state.parent_version,
            "parent_manifest_hash": state.parent_manifest_hash,
            "derived_from_receipt_ref": state.derived_from_receipt_ref,
        }
        # Keep the historical request envelope byte-for-byte stable for
        # ordinary installs.  These lifecycle fields were added later for
        # Companion-owned mutations; serializing them as explicit nulls would
        # turn a valid replay of an older first-party install into an
        # idempotency conflict at startup.
        state.lifecycle_request_fields = self._lifecycle_request_fields(
            state.expected_binding_generation, state.owner_key
        )
        if state.management_policy is not None:
            state.lifecycle_request_fields["management_policy"] = state.management_policy
        state.request = {**state.request, **state.lifecycle_request_fields}
        state.operation = await self.store.create_operation(
            operation_id=state.operation_id,
            idempotency_key=state.idempotency_key,
            kind=state.kind,
            request=state.request,
            root_run_id=state.root_run_id,
            pack_id=state.expected_pack_id,
            requested_scope=state.scope,
            requested_scope_key=state.scope_key,
        )
        if state.operation.status == "succeeded":
            return await self._result_for_completed_install(state.operation)
        if state.operation.status != "running":
            state.stored_error = dict(state.operation.error or {})
            raise CapabilityManagerError(
                str(state.stored_error.get("code") or "operation_not_resumable"),
                str(
                    state.stored_error.get("message")
                    or f"operation is {state.operation.status} at {state.operation.phase}"
                ),
            )
        state.staging_path = self.layout.staging_path(state.operation_id)
        state.validated_ref: ValidatedCapabilityPackageRefV1 | None = None
        state.flow = None
        await self._run_install_operation(state)
        if state.flow == "stop":
            return state.result

    async def _run_install_operation(self, state: _InstallState) -> None:
        """执行安装操作的各个阶段；失败时按错误类型收尾。"""

        try:
            (
                state.operation,
                state.validated_ref,
                state.validation,
            ) = await self._install_validate_source(
                state.operation,
                operation_id=state.operation_id,
                resolved_source=state.resolved_source,
                source=state.source,
                staging_path=state.staging_path,
                validated_ref=state.validated_ref,
            )
            state.manifest = state.validation.manifest
            if state.expected_pack_id is not None and state.manifest.id != state.expected_pack_id:
                raise CapabilityManagerError(
                    "unexpected_pack_id",
                    f"expected {state.expected_pack_id}, staged pack is {state.manifest.id}",
                )
            (
                state.install_path,
                state.operation,
                state.validation,
            ) = await self._install_stage_payload(
                state.generated,
                manifest=state.manifest,
                operation_id=state.operation_id,
                resolved_source=state.resolved_source,
                staging_path=state.staging_path,
                validated_ref=state.validated_ref,
                validation=state.validation,
            )

            state.candidate = await self.publisher.prepare_candidate(
                state.validation,
                install_root=state.install_path,
                operation_id=state.operation_id,
            )
            state.operation = await self._install_check_candidate(
                state.candidate,
                derived_from_receipt_ref=state.derived_from_receipt_ref,
                install_path=state.install_path,
                manifest=state.manifest,
                operation=state.operation,
                operation_id=state.operation_id,
                parent_manifest_hash=state.parent_manifest_hash,
                parent_version=state.parent_version,
                validation=state.validation,
            )

            state.current_binding = await self.store.get_binding(
                state.scope,
                state.scope_key,
                state.manifest.id,
                owner_key=state.owner_key,
            )
            state.actual_generation = (
                0 if state.current_binding is None else state.current_binding.generation
            )
            state.expected_generation = (
                state.actual_generation
                if state.expected_binding_generation is None
                else state.expected_binding_generation
            )
            state.binding, state.publication = await self._publish_and_bind(
                operation_id=state.operation_id,
                candidate=state.candidate,
                pack_id=state.manifest.id,
                version=state.manifest.version,
                manifest_hash=state.manifest.manifest_hash,
                scope=state.scope,
                scope_key=state.scope_key,
                expected_binding_generation=state.expected_generation,
                owner_key=state.owner_key,
                management_policy=state.management_policy,
            )
            state.result = await self._install_finish(
                state.binding,
                install_path=state.install_path,
                kind=state.kind,
                operation_id=state.operation_id,
                publication=state.publication,
                validation=state.validation,
            )
            state.flow = "stop"
            return
        except asyncio.CancelledError:
            await self._cancel_operation(state.operation_id)
            raise
        except Exception as exc:
            await self._record_operation_failure(state.operation_id, exc)
            raise

    async def _require_install_kind(
        self,
        derived_from_receipt_ref: str | None,
        kind: str,
        parent_manifest_hash: str | None,
        parent_version: str | None,
    ) -> None:
        if kind not in {"install", "update", "repair"}:
            raise CapabilityManagerError("invalid_install_kind", kind)
        if kind == "repair" and not (
            parent_version and parent_manifest_hash and derived_from_receipt_ref
        ):
            raise CapabilityManagerError(
                "repair_lineage_required",
                "repair requires parent version/hash and trusted failure receipt",
            )
        await self.initialize()

    def _lifecycle_request_fields(
        self, expected_binding_generation: int | None, owner_key: str | None
    ) -> dict[str, JsonValue]:
        lifecycle_request_fields: dict[str, JsonValue] = {}
        if owner_key is not None:
            lifecycle_request_fields["owner_key"] = owner_key
        if expected_binding_generation is not None:
            lifecycle_request_fields["expected_binding_generation"] = expected_binding_generation
        return lifecycle_request_fields

    async def _install_validate_source(
        self,
        operation,
        *,
        operation_id,
        resolved_source,
        source: PackSourceRequest,
        staging_path,
        validated_ref: ValidatedCapabilityPackageRefV1 | None,
    ):
        if operation.phase == "planned":
            await self._commit_phase(operation_id, "planned", {"source": source.uri})
            # A crash while copying has no durable staged evidence.  The
            # exact operation staging tree is disposable and may be
            # rebuilt safely.
            if staging_path.exists():
                await self._remove_exact_tree(staging_path)
            staged = await self.source_resolver.stage(source, staging_path)
            validated_ref = self._require_staged_validated_ref(
                staged,
                resolved_source=resolved_source,
            )
            operation = await self._commit_phase(
                operation_id,
                "staged",
                {
                    "path": str(staged.root),
                    "validated_ref": dict(self._validated_ref_evidence(validated_ref)),
                },
            )
        if operation.phase == "staged":
            if not staging_path.exists():
                raise CapabilityManagerError(
                    "staging_missing",
                    "operation says staged but its exact staging directory is absent",
                )
            if validated_ref is None:
                validated_ref = self._validated_ref_from_evidence(
                    await self.store.get_phase_evidence(operation_id, "staged")
                )
            if validated_ref.source_kind != resolved_source.source_type:
                raise CapabilityManagerError(
                    "validated_package_source_mismatch",
                    "durable validated package source differs from the resolved source",
                )
            validation_root = staging_path
        else:
            verified_evidence = await self.store.get_phase_evidence(operation_id, "verified")
            install_path_value = (
                None if verified_evidence is None else verified_evidence.get("install_path")
            )
            if not isinstance(install_path_value, str):
                raise CapabilityManagerError(
                    "verified_projection_missing",
                    "verified operation lacks its immutable install path",
                )
            validation_root = Path(install_path_value)

        validation = load_and_validate_pack(validation_root, environment=self.environment)
        return operation, validated_ref, validation

    async def _install_stage_payload(
        self,
        generated: bool,
        *,
        manifest,
        operation_id,
        resolved_source,
        staging_path,
        validated_ref: ValidatedCapabilityPackageRefV1 | None,
        validation,
    ):
        if not _manifest_source_matches(
            requested_source_type=resolved_source.source_type,
            manifest_source_type=manifest.source.type,
        ):
            raise CapabilityManagerError(
                "source_identity_mismatch",
                "manifest source type differs from the staged source",
            )
        if manifest.source.revision != resolved_source.revision:
            raise CapabilityManagerError(
                "source_revision_mismatch",
                "manifest source revision differs from the requested revision",
            )
        if generated and any(
            tool.execution_profile != "brokered-effect-v1" for tool in manifest.tools
        ):
            raise CapabilityManagerError(
                "generated_native_adapter_forbidden",
                "generated packs may only use brokered-effect-v1",
            )
        operation = await self.store.bind_operation_pack_id(operation_id, manifest.id)
        await self.store.record_validation_result(
            operation_id,
            "manifest_and_integrity",
            status="passed",
            detail={"manifest_hash": manifest.manifest_hash},
        )

        install_path = self.layout.pack_path(
            manifest.id, manifest.version, manifest.manifest_hash
        ).resolve(strict=False)
        install_io_path = windows_extended_path(install_path)
        if operation.phase == "staged":
            if validated_ref is None:
                raise CapabilityManagerError(
                    "validated_package_ref_missing",
                    "staged operation lacks its validated package ref",
                )
            if install_io_path.exists():
                await self._validate_materialized_tree(
                    install_io_path,
                    expected_ref=validated_ref,
                )
                installed_validation = load_and_validate_pack(
                    install_io_path, environment=self.environment
                )
                if installed_validation.manifest.manifest_hash != manifest.manifest_hash:
                    raise CapabilityManagerError(
                        "immutable_version_conflict",
                        "immutable version path contains another manifest",
                    )
                await self._remove_exact_tree(staging_path)
                validation = installed_validation
            else:
                install_path.parent.mkdir(parents=True, exist_ok=True)
                await self._fault("before:install_replace")
                await self._validate_materialized_tree(
                    staging_path,
                    expected_ref=validated_ref,
                )
                await asyncio.to_thread(
                    os.replace,
                    windows_extended_path(staging_path),
                    install_io_path,
                )
                validation = load_and_validate_pack(install_io_path, environment=self.environment)
            operation = await self._commit_phase(
                operation_id,
                "verified",
                {
                    "manifest_hash": manifest.manifest_hash,
                    "install_path": str(install_path),
                },
            )

        environment_path = self.layout.environment_path(
            manifest.id, manifest.version, manifest.manifest_hash
        )
        if operation.phase == "verified":
            environment_evidence = await self.environment_preparer.prepare(
                validation,
                environment_root=environment_path,
                operation_id=operation_id,
            )
            operation = await self._commit_phase(
                operation_id,
                "environment_ready",
                dict(environment_evidence),
            )
        return install_path, operation, validation

    async def _install_check_candidate(
        self,
        candidate,
        *,
        derived_from_receipt_ref: str | None,
        install_path,
        manifest,
        operation,
        operation_id,
        parent_manifest_hash: str | None,
        parent_version: str | None,
        validation,
    ):
        if len(candidate.tool_spec_fingerprints) != len(manifest.tools):
            raise CapabilityManagerError(
                "candidate_tool_mismatch",
                "candidate must provide one ToolSpec fingerprint per declared tool",
            )
        if operation.phase == "environment_ready":
            version_record = CapabilityVersionRecord(
                descriptor=validation.descriptor,
                install_path=install_path,
                validation_status="healthy",
                expected_tool_fingerprints=candidate.tool_spec_fingerprints,
                parent_version=parent_version,
                parent_manifest_hash=parent_manifest_hash,
                derived_from_receipt_ref=derived_from_receipt_ref,
                created_at=self.store.now(),
            )
            await self.store.record_version(version_record)
            operation = await self._commit_phase(
                operation_id,
                "candidate_ready",
                {"tool_spec_fingerprints": list(candidate.tool_spec_fingerprints)},
            )

        if operation.phase in {"publish_intent", "catalog_swapped", "bound"}:
            raise CapabilityManagerError(
                "publish_reconciliation_required",
                f"operation at {operation.phase} must be reconciled before resume",
            )
        return operation

    async def _install_finish(
        self, binding, *, install_path, kind: str, operation_id, publication, validation
    ) -> CapabilityInstallResult:
        receipt_evidence = await self._manager_receipt_evidence(
            operation_id=operation_id,
            action=kind,
            binding=binding,
            registry_revision=publication.registry_revision,
            tool_spec_fingerprints=publication.tool_spec_fingerprints,
        )
        operation = await self._commit_phase(
            operation_id,
            "published",
            {
                "binding_id": binding.binding_id,
                "registry_revision": publication.registry_revision,
                **receipt_evidence,
            },
        )
        return CapabilityInstallResult(
            operation=operation,
            validation=validation,
            binding=binding,
            install_path=install_path,
            registry_revision=publication.registry_revision,
            tool_spec_fingerprints=publication.tool_spec_fingerprints,
            manager_receipt_hash=str(receipt_evidence["manager_receipt_hash"]),
            committed_owner_binding_set_stamp=str(
                receipt_evidence["committed_owner_binding_set_stamp"]
            ),
            process_projection_fingerprint=str(receipt_evidence["process_projection_fingerprint"]),
        )

    async def update(self, source: PackSourceRequest, **kwargs: Any) -> CapabilityInstallResult:
        return await self.install(source, kind="update", **kwargs)

    async def repair(self, source: PackSourceRequest, **kwargs: Any) -> CapabilityInstallResult:
        return await self.install(source, kind="repair", **kwargs)

    async def _result_for_completed_install(
        self, operation: CapabilityOperationRecord
    ) -> CapabilityInstallResult:
        pack_id = operation.pack_id
        scope = operation.requested_scope
        scope_key = operation.requested_scope_key
        if not pack_id or not scope or not scope_key:
            intent = await self.store.get_publish_intent_for_operation(operation.operation_id)
            if intent is None:
                raise CapabilityManagerError(
                    "completed_operation_projection_missing",
                    "completed install lacks a direct result projection",
                )
            pack_id_value = intent.new_binding.get("capability_id")
            scope_value = intent.new_binding.get("scope")
            scope_key_value = intent.new_binding.get("scope_key")
            if not all(
                isinstance(value, str) for value in (pack_id_value, scope_value, scope_key_value)
            ):
                raise CapabilityManagerError(
                    "completed_operation_projection_invalid",
                    "completed install binding projection is malformed",
                )
            pack_id = str(pack_id_value)
            scope = str(scope_value)
            scope_key = str(scope_key_value)
        owner_key = operation.request.get("owner_key")
        binding = await self.store.get_binding(
            str(scope),
            str(scope_key),
            str(pack_id),
            owner_key=(None if not isinstance(owner_key, str) else owner_key),
        )
        if binding is None:
            raise CapabilityManagerError(
                "completed_binding_missing", "completed operation has no active binding"
            )
        record = await self.store.get_version(
            binding.capability_id, binding.version, binding.manifest_hash
        )
        if record is None:
            raise CapabilityManagerError(
                "completed_version_missing", "completed operation version is absent"
            )
        validation = load_and_validate_pack(record.install_path, environment=self.environment)
        evidence = await self.store.get_phase_evidence(
            operation.operation_id,
            "published",
        )
        evidence = dict(evidence or {})
        return CapabilityInstallResult(
            operation=operation,
            validation=validation,
            binding=binding,
            install_path=record.install_path,
            registry_revision=0,
            tool_spec_fingerprints=record.expected_tool_fingerprints,
            manager_receipt_hash=str(evidence.get("manager_receipt_hash") or ""),
            committed_owner_binding_set_stamp=str(
                evidence.get("committed_owner_binding_set_stamp") or ""
            ),
            process_projection_fingerprint=str(
                evidence.get("process_projection_fingerprint") or ""
            ),
        )
