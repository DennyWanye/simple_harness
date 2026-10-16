# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Publish-and-bind, publish-and-disable and skill install batch activation."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from ..contracts import (
    CapabilityBinding,
    CapabilityScopeKind,
    JsonValue,
    OwnerScopeKey,
    fingerprint_json,
)
from ..package_limits import ValidatedCapabilityPackageRefV1
from ..skill_source import CanonicalSkillBatch, CanonicalSkillPack, ResolvedSkillSourceEvidence
from ..store import (
    CapabilitySkillInstallHandoff,
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStoreConflict,
)
from .core import CapabilityPackManagerCore
from .support import CapabilityCandidate, CapabilityManagerError, CapabilityPublication, _intent_id


class PublishMixin(CapabilityPackManagerCore):
    """Publish-and-bind, publish-and-disable and skill install batch activation."""

    __slots__ = ()

    async def _publish_and_bind(
        self,
        *,
        operation_id: str,
        candidate: CapabilityCandidate,
        pack_id: str,
        version: str,
        manifest_hash: str,
        scope: CapabilityScopeKind,
        scope_key: str,
        expected_binding_generation: int,
        owner_key: str | None = None,
        management_policy: str | None = None,
    ) -> tuple[CapabilityBinding, CapabilityPublication]:
        async with self.publish_lock:
            return await self._publish_and_bind_locked(
                operation_id=operation_id,
                candidate=candidate,
                pack_id=pack_id,
                version=version,
                manifest_hash=manifest_hash,
                scope=scope,
                scope_key=scope_key,
                expected_binding_generation=expected_binding_generation,
                owner_key=owner_key,
                management_policy=management_policy,
            )

    async def _publish_and_bind_locked(
        self,
        *,
        operation_id: str,
        candidate: CapabilityCandidate,
        pack_id: str,
        version: str,
        manifest_hash: str,
        scope: CapabilityScopeKind,
        scope_key: str,
        expected_binding_generation: int,
        owner_key: str | None = None,
        management_policy: str | None = None,
    ) -> tuple[CapabilityBinding, CapabilityPublication]:
        old_binding = await self.store.get_binding(
            scope,
            scope_key,
            pack_id,
            owner_key=owner_key,
        )
        actual_generation = 0 if old_binding is None else old_binding.generation
        if actual_generation != expected_binding_generation:
            raise CapabilityStoreConflict(
                "binding_generation_conflict",
                "binding changed before the publish lock was acquired",
            )
        new_binding_payload: Mapping[str, JsonValue] = {
            "scope": scope,
            "scope_key": scope_key,
            "capability_id": pack_id,
            "version": version,
            "manifest_hash": manifest_hash,
            "active": True,
            "expected_generation": expected_binding_generation,
            "owner_key": owner_key,
            "management_policy": management_policy,
        }
        old_binding_payload = None if old_binding is None else old_binding.to_dict()
        intent_id = _intent_id(operation_id)
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.create_publish_intent(
                intent_id=intent_id,
                operation_id=operation_id,
                expected_registry_revision=candidate.expected_registry_revision,
                old_specs=candidate.old_specs,
                new_specs=candidate.new_specs,
                old_binding=old_binding_payload,
                new_binding=new_binding_payload,
            )
            await tx.commit_phase(
                operation_id,
                "publish_intent",
                evidence={"intent_id": intent_id},
            )
        await self._fault("after:publish_intent")

        publication = await self.publisher.publish(candidate, operation_id=operation_id)
        if tuple(publication.tool_spec_fingerprints) != tuple(candidate.tool_spec_fingerprints):
            raise CapabilityManagerError(
                "publisher_fingerprint_mismatch",
                "published ToolSpecs differ from the verified candidate",
            )
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.advance_publish_intent(intent_id, phase="catalog_swapped", status="pending")
            await tx.commit_phase(
                operation_id,
                "catalog_swapped",
                evidence={"registry_revision": publication.registry_revision},
            )
        await self._fault("after:catalog_swapped")

        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            binding = await tx.set_binding(
                scope=scope,
                scope_key=scope_key,
                pack_id=pack_id,
                version=version,
                manifest_hash=manifest_hash,
                expected_generation=expected_binding_generation,
                enabled=True,
                owner_key=owner_key,
                management_policy=management_policy,
            )
            await tx.advance_publish_intent(intent_id, phase="bound", status="committed")
            await tx.commit_phase(
                operation_id,
                "bound",
                evidence={"binding_id": binding.binding_id},
            )
        await self._fault("after:bound")
        return binding, publication

    async def _publish_and_disable(
        self,
        *,
        operation_id: str,
        candidate: CapabilityCandidate,
        binding: CapabilityBinding,
    ) -> tuple[CapabilityBinding, CapabilityPublication]:
        async with self.publish_lock:
            return await self._publish_and_disable_locked(
                operation_id=operation_id,
                candidate=candidate,
                binding=binding,
            )

    async def _publish_and_disable_locked(
        self,
        *,
        operation_id: str,
        candidate: CapabilityCandidate,
        binding: CapabilityBinding,
    ) -> tuple[CapabilityBinding, CapabilityPublication]:
        current = await self.store.get_binding(
            binding.scope,
            binding.scope_key,
            binding.capability_id,
            owner_key=binding.owner_key,
        )
        if current is None or current.fingerprint != binding.fingerprint:
            raise CapabilityStoreConflict(
                "binding_generation_conflict",
                "binding changed before the uninstall publish lock was acquired",
            )
        new_binding_payload: Mapping[str, JsonValue] = {
            **binding.to_dict(),
            "active": False,
            "expected_generation": binding.generation,
        }
        intent_id = _intent_id(operation_id)
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.create_publish_intent(
                intent_id=intent_id,
                operation_id=operation_id,
                expected_registry_revision=candidate.expected_registry_revision,
                old_specs=candidate.old_specs,
                new_specs=candidate.new_specs,
                old_binding=binding.to_dict(),
                new_binding=new_binding_payload,
            )
            await tx.commit_phase(
                operation_id,
                "publish_intent",
                evidence={"intent_id": intent_id, "uninstall": True},
            )
        await self._fault("after:publish_intent")

        publication = await self.publisher.publish(candidate, operation_id=operation_id)
        if tuple(publication.tool_spec_fingerprints) != tuple(candidate.tool_spec_fingerprints):
            raise CapabilityManagerError(
                "publisher_fingerprint_mismatch",
                "unpublished ToolSpecs differ from the verified candidate",
            )
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.advance_publish_intent(intent_id, phase="catalog_swapped", status="pending")
            await tx.commit_phase(
                operation_id,
                "catalog_swapped",
                evidence={"registry_revision": publication.registry_revision},
            )
        await self._fault("after:catalog_swapped")

        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            disabled = await tx.set_binding(
                scope=binding.scope,
                scope_key=binding.scope_key,
                pack_id=binding.capability_id,
                version=binding.version,
                manifest_hash=binding.manifest_hash,
                expected_generation=binding.generation,
                enabled=False,
                owner_key=binding.owner_key,
                management_policy=binding.management_policy,
            )
            await tx.advance_publish_intent(intent_id, phase="bound", status="committed")
            await tx.commit_phase(
                operation_id,
                "bound",
                evidence={"binding_id": disabled.binding_id, "enabled": False},
            )
        await self._fault("after:bound")
        await self.publisher.stop_binding(binding)
        return disabled, publication

    async def _manager_receipt_evidence(
        self,
        *,
        operation_id: str,
        action: str,
        binding: CapabilityBinding,
        registry_revision: int,
        tool_spec_fingerprints: Sequence[str],
    ) -> Mapping[str, JsonValue]:
        owner_token = (
            await self.store.read_detail_token_vector(
                (
                    OwnerScopeKey(
                        binding.owner_key,
                        binding.scope,
                        binding.scope_key,
                    ),
                )
            )
        ).items[0]
        process_projection_fingerprint = fingerprint_json(
            {
                "schema_version": 1,
                "operation_id": operation_id,
                "owner_key": binding.owner_key,
                "scope": binding.scope,
                "scope_key": binding.scope_key,
                "pack_id": binding.capability_id,
                "version": binding.version,
                "manifest_hash": binding.manifest_hash,
                "registry_revision": registry_revision,
                "tool_spec_fingerprints": list(tool_spec_fingerprints),
            }
        )
        manager_receipt_hash = fingerprint_json(
            {
                "schema_version": 1,
                "operation_id": operation_id,
                "action": action,
                "binding_id": binding.binding_id,
                "binding_generation": binding.generation,
                "management_policy": binding.management_policy,
                "management_generation": binding.management_generation,
                "committed_owner_binding_set_stamp": (
                    owner_token.committed_owner_binding_set_stamp
                ),
                "process_projection_fingerprint": (process_projection_fingerprint),
            }
        )
        return {
            "manager_receipt_hash": manager_receipt_hash,
            "committed_owner_binding_set_stamp": (owner_token.committed_owner_binding_set_stamp),
            "process_projection_fingerprint": (process_projection_fingerprint),
        }

    async def publish_skill_install_batch(
        self,
        *,
        intent: CapabilitySkillInstallIntent,
        handoff: CapabilitySkillInstallHandoff,
        staging_root: Path,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> Mapping[str, JsonValue]:
        if (
            handoff.intent_id != intent.intent_id
            or handoff.member_set_stamp != intent.member_set_stamp
        ):
            raise CapabilityManagerError(
                "batch_handoff_mismatch", "handoff differs from install intent"
            )
        if tuple(member.ordinal for member in members) != tuple(range(len(members))):
            raise CapabilityManagerError(
                "batch_member_order_invalid", "members are not densely ordered"
            )
        root = staging_root.resolve(strict=True)
        packs = []
        selected = []
        for member in members:
            raw_ref = member.member.get("archive_ref")
            raw_validated = member.member.get("validated_ref")
            if not isinstance(raw_ref, str) or not isinstance(raw_validated, Mapping):
                raise CapabilityManagerError(
                    "batch_archive_metadata_missing", member.normalized_name
                )
            archive_path = (root / raw_ref).resolve(strict=True)
            try:
                archive_path.relative_to(root)
            except ValueError as exc:
                raise CapabilityManagerError("batch_archive_ref_unsafe", raw_ref) from exc
            archive_bytes = await asyncio.to_thread(archive_path.read_bytes)
            validated_ref = ValidatedCapabilityPackageRefV1.issue(
                source_kind=str(raw_validated["source_kind"]),
                limits=self.package_validator.limits,
                archive_hash=str(raw_validated["archive_hash"]),
                manifest_hash=str(raw_validated["manifest_hash"]),
                file_set_hash=str(raw_validated["file_set_hash"]),
                entry_count=int(raw_validated["entry_count"]),
                total_uncompressed_bytes=int(raw_validated["total_uncompressed_bytes"]),
            )
            expected_archive_hash = str(member.member.get("archive_hash") or member.source_digest)
            if hashlib.sha256(archive_bytes).hexdigest() != expected_archive_hash:
                raise CapabilityManagerError("batch_archive_hash_mismatch", member.normalized_name)
            actual_ref = await asyncio.to_thread(
                self.package_validator.validate_zip_archive,
                archive_bytes,
                source_kind=validated_ref.source_kind,
            )
            if actual_ref != validated_ref:
                raise CapabilityManagerError("batch_validated_ref_mismatch", member.normalized_name)
            with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as package:
                manifest_payload = json.loads(package.read("deskpet-pack.json"))
            provided_tools = tuple(
                sorted(
                    str(tool["provider_name"])
                    for tool in manifest_payload.get("entries", {}).get("tools", [])
                )
            )
            actual_allowed_tools = tuple(
                sorted(
                    str(tool_name)
                    for skill in manifest_payload.get("entries", {}).get("skills", [])
                    for tool_name in skill.get("allowed_tools", ())
                )
            )
            raw_allowed = member.member.get("allowed_tools")
            if (
                raw_allowed is not None
                and tuple(sorted(str(item) for item in raw_allowed)) != actual_allowed_tools
            ):
                raise CapabilityManagerError("batch_allowed_tools_mismatch", member.normalized_name)
            if len(provided_tools) != len(set(provided_tools)):
                raise CapabilityManagerError("batch_provided_tools_invalid", member.normalized_name)
            selected_ref = str(member.member.get("selected_subdirectory") or member.normalized_name)
            selected.append(selected_ref)

            packs.append(
                CanonicalSkillPack(
                    member.normalized_name,
                    selected_ref,
                    archive_bytes,
                    expected_archive_hash,
                    member.manifest_hash,
                    member.content_hash,
                    validated_ref,
                )
            )

        source = dict(intent.source)
        evidence = ResolvedSkillSourceEvidence.issue(
            normalized_url=str(
                source.get("normalized_url")
                or source.get("url")
                or "https://github.com/unknown/unknown"
            ),
            requested_ref=str(source.get("requested_ref") or "main"),
            exact_commit=intent.exact_commit,
            archive_hash=intent.archive_hash,
            raw_file_set_digest=intent.raw_tree_hash,
            selected_subdirectories=selected,
        )
        result = await self._publish_canonical_skill_install_batch(
            CanonicalSkillBatch(evidence, tuple(packs), intent.member_set_stamp),
            operation_id=handoff.operation_id,
            project_scope_key=intent.install_scope_key,
            # The frozen Run catalog and durable binding must share the same
            # opaque user owner.  principal_id authenticates the confirmation;
            # it is never a catalog/binding namespace.
            owner_key=(
                intent.install_scope_key if intent.install_scope == "user" else intent.principal_id
            ),
            committed_set_stamp=intent.member_set_stamp,
        )
        committed = await self.store.get_phase_evidence(handoff.operation_id, "batch_committed")
        if committed is None:
            raise CapabilityManagerError("batch_manager_receipt_missing", handoff.operation_id)
        return {
            "operation_id": result.operation.operation_id,
            "manager_receipt_hash": result.manager_receipt_hash,
            "committed_set_stamp": result.committed_set_stamp,
            "registry_revision": result.registry_revision,
            "binding_ids": [binding.binding_id for binding in result.bindings],
            "owner_key": str(committed.get("owner_key") or ""),
            "members": list(committed.get("members") or ()),
            "publication_state": (
                "pending_invisible" if intent.install_scope == "user" else "active"
            ),
        }

    async def activate_skill_install_batch(
        self,
        *,
        intent: CapabilitySkillInstallIntent,
        manager_receipt: Mapping[str, JsonValue],
        members: Sequence[CapabilitySkillInstallMember],
    ) -> Mapping[str, JsonValue]:
        """Atomically expose a verified user-global batch.

        Publication records exact version bytes first, without changing the
        visible binding.  Only this post-verification CAS advances the frozen
        owner catalog to the new versions.
        """
        if intent.install_scope != "user":
            raise CapabilityManagerError("batch_activation_scope_invalid", intent.install_scope)
        if str(manager_receipt.get("publication_state") or "") != "pending_invisible":
            raise CapabilityManagerError(
                "batch_activation_receipt_invalid", "publication is not pending"
            )
        receipt_members = {
            str(item.get("pack_id") or ""): item
            for item in manager_receipt.get("members", ())
            if isinstance(item, Mapping)
        }
        bindings: list[CapabilityBinding] = []
        operation_id = str(manager_receipt.get("operation_id") or "")
        async with self.publish_lock:
            existing = [
                await self.store.get_binding(
                    "user",
                    intent.install_scope_key,
                    member.pack_id,
                    owner_key=intent.install_scope_key,
                )
                for member in members
            ]
            if all(
                binding is not None
                and binding.active
                and binding.version == member.version
                and binding.manifest_hash == member.manifest_hash
                for binding, member in zip(existing, members, strict=True)
            ):
                bindings = [binding for binding in existing if binding is not None]
            else:
                for binding, member in zip(existing, members, strict=True):
                    receipt_member = receipt_members.get(member.pack_id)
                    if receipt_member is None:
                        raise CapabilityManagerError(
                            "batch_activation_receipt_incomplete", member.pack_id
                        )
                    expected_generation = int(
                        receipt_member.get("expected_binding_generation") or 0
                    )
                    actual_generation = 0 if binding is None else binding.generation
                    if actual_generation != expected_generation:
                        raise CapabilityManagerError(
                            "batch_activation_binding_conflict",
                            "global binding changed before activation",
                        )
                candidates: list[CapabilityCandidate] = []
                for member in members:
                    record = await self.store.get_version(
                        member.pack_id, member.version, member.manifest_hash
                    )
                    if record is None:
                        raise CapabilityManagerError(
                            "batch_activation_version_missing", member.pack_id
                        )
                    candidates.append(
                        await self.publisher.prepare_installed(
                            record,
                            operation_id=str(manager_receipt.get("operation_id") or ""),
                        )
                    )
                expected_revisions = {
                    candidate.expected_registry_revision for candidate in candidates
                }
                if len(expected_revisions) != 1:
                    raise CapabilityManagerError(
                        "batch_activation_registry_revision_mismatch",
                        "global candidates were prepared against different registries",
                    )
                runtime_payload: object | None = None
                if all(isinstance(candidate.runtime_payload, tuple) for candidate in candidates):
                    runtime_payload = tuple(
                        item
                        for candidate in candidates
                        for item in candidate.runtime_payload  # type: ignore[union-attr]
                    )
                combined = CapabilityCandidate(
                    expected_registry_revision=next(iter(expected_revisions), 0),
                    tool_spec_fingerprints=tuple(
                        fingerprint
                        for candidate in candidates
                        for fingerprint in candidate.tool_spec_fingerprints
                    ),
                    old_specs=tuple(
                        spec for candidate in candidates for spec in candidate.old_specs
                    ),
                    new_specs=tuple(
                        spec for candidate in candidates for spec in candidate.new_specs
                    ),
                    publisher_state={
                        "schema": "global-skill-activation-candidate-v2",
                        "member_count": len(candidates),
                    },
                    runtime_payload=runtime_payload,
                )
                activation_evidence: dict[str, JsonValue] = {
                    "schema": "global-skill-activation-intent-v2",
                    "owner_key": intent.install_scope_key,
                    "members": [
                        {
                            "pack_id": member.pack_id,
                            "version": member.version,
                            "manifest_hash": member.manifest_hash,
                            "expected_binding_generation": int(
                                receipt_members[member.pack_id].get("expected_binding_generation")
                                or 0
                            ),
                        }
                        for member in members
                    ],
                }
                async with self.store.write_transaction() as db:
                    await db.execute(
                        """INSERT INTO capability_operation_phase_evidence(
                               operation_id,phase,idempotency_key,status,evidence_json,
                               created_at,updated_at)
                           VALUES(?, 'global_activation', ?, 'intent', ?, ?, ?)
                           ON CONFLICT(operation_id,phase) DO UPDATE SET
                               status='intent',evidence_json=excluded.evidence_json,
                               updated_at=excluded.updated_at
                           WHERE capability_operation_phase_evidence.status='failed'""",
                        (
                            operation_id,
                            f"{operation_id}:global_activation",
                            json.dumps(activation_evidence, sort_keys=True),
                            self.store.now(),
                            self.store.now(),
                        ),
                    )
                publication = await self.publisher.publish(
                    combined,
                    operation_id=operation_id,
                )
                if tuple(publication.tool_spec_fingerprints) != combined.tool_spec_fingerprints:
                    raise CapabilityManagerError(
                        "publisher_fingerprint_mismatch", "batch ToolSpecs differ"
                    )
                await self._fault("after:global_activation_registry_publish")
                async with self.store.write_transaction() as db:
                    tx = self.store.bind(db)
                    for member in members:
                        receipt_member = receipt_members.get(member.pack_id)
                        if receipt_member is None:
                            raise CapabilityManagerError(
                                "batch_activation_receipt_incomplete", member.pack_id
                            )
                        expected_generation = int(
                            receipt_member.get("expected_binding_generation") or 0
                        )
                        binding = await tx.set_binding(
                            scope="user",
                            scope_key=intent.install_scope_key,
                            pack_id=member.pack_id,
                            version=member.version,
                            manifest_hash=member.manifest_hash,
                            expected_generation=expected_generation,
                            enabled=True,
                            owner_key=intent.install_scope_key,
                            management_policy="user_managed",
                        )
                        bindings.append(binding)
                    committed_evidence = {
                        **activation_evidence,
                        "registry_revision": publication.registry_revision,
                        "binding_ids": [binding.binding_id for binding in bindings],
                    }
                    await db.execute(
                        """UPDATE capability_operation_phase_evidence
                           SET status='committed',evidence_json=?,updated_at=?
                           WHERE operation_id=? AND phase='global_activation'
                             AND status='intent'""",
                        (
                            json.dumps(committed_evidence, sort_keys=True),
                            self.store.now(),
                            operation_id,
                        ),
                    )
        payload: dict[str, JsonValue] = {
            "schema": "global-skill-binding-activation-v2",
            "publication_state": "active",
            "owner_key": intent.install_scope_key,
            "operation_id": str(manager_receipt.get("operation_id") or ""),
            "binding_ids": [binding.binding_id for binding in bindings],
            "binding_generations": [binding.generation for binding in bindings],
            "registry_revision": (
                publication.registry_revision
                if "publication" in locals()
                else int(manager_receipt.get("registry_revision") or 0)
            ),
        }
        return {**payload, "activation_hash": fingerprint_json(payload)}
