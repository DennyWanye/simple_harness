# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Run-frozen Skill instruction resolution for SDK and legacy Runs."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from simple_harness import RunId, thaw_json
from simple_harness.tools import SkillResourceRecord

from deskpet.companion.skills import PreparedSkillInvocationScopeV1
from deskpet.tools.capabilities import canonical_hash


class SdkThenLegacyFrozenSkillResolver:
    """Prefer the SDK Run catalog; fall back only when no SDK Run exists."""

    def __init__(self, *, authorities: Any, snapshot_resolver: Any, legacy_resolver: Any) -> None:
        if authorities is None or snapshot_resolver is None or legacy_resolver is None:
            raise RuntimeError("frozen_skill_resolver_dependency_missing")
        self._authorities = authorities
        self._snapshot_resolver = snapshot_resolver
        self._legacy_resolver = legacy_resolver

    async def resolve_frozen_instruction(
        self,
        *,
        run_id: str,
        skill_name: str,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        try:
            authority = self._authorities.resolve(run_id)
        except KeyError:
            return await self._legacy_resolver.resolve_frozen_instruction(
                run_id=run_id,
                skill_name=skill_name,
                arguments=arguments,
            )

        exposure = self._authorities.resolve_exposure(RunId(run_id))
        matches = tuple(
            record
            for record in exposure.catalog.snapshot.records
            if isinstance(record, SkillResourceRecord)
            and record.skill_locator == skill_name
        )
        if len(matches) != 1:
            raise RuntimeError("sdk_frozen_skill_not_in_run_catalog")
        record = matches[0]
        metadata = thaw_json(record.metadata)  # type: ignore[arg-type]
        if not isinstance(metadata, dict):
            raise RuntimeError("sdk_frozen_skill_metadata_invalid")
        scope = PreparedSkillInvocationScopeV1.from_dict(
            {
                "schema": "prepared_skill_invocation_scope/v1",
                "owner_key": metadata.get("owner_key"),
                "pack_id": metadata.get("pack_id"),
                "skill_id": skill_name,
                "version": metadata.get("version"),
                "manifest_hash": metadata.get("manifest_hash"),
                "content_hash": record.content_hash,
                "allowed_tools": metadata.get("allowed_tools"),
                "scope_hash": metadata.get("scope_hash"),
            }
        )
        resolved = await self._snapshot_resolver.resolve_instruction(
            scope,
            arguments,
        )

        visible = {
            item.name
            for item in exposure.provider_specs(RunId(run_id))
        }
        effective = sorted(set(scope.allowed_tools) & visible)
        facts = [self._exact_sdk_tool_fact(authority.specs[name]) for name in effective]
        ref_hashes = sorted(canonical_hash(item) for item in facts)
        refs_hash = canonical_hash(
            {
                "schema": "sdk_skill_tool_intersection/v1",
                "run_id": run_id,
                "catalog_fingerprint": authority.catalog_fingerprint,
                "scope_hash": scope.scope_hash,
                "effective_tool_ref_hashes": ref_hashes,
            }
        )
        return {
            **resolved.to_dict(),
            "capability_snapshot_ref": authority.catalog_fingerprint,
            "run_catalog_content_stamp": exposure.catalog.snapshot.fingerprint,
            "allowed_tool_refs": facts,
            "effective_tool_ref_hashes": ref_hashes,
            "effective_tool_refs_hash": refs_hash,
        }

    async def resolve_frozen_resource(
        self,
        *,
        run_id: str,
        skill_name: str,
        relative_path: str,
    ) -> Mapping[str, Any]:
        try:
            authority = self._authorities.resolve(run_id)
        except KeyError:
            return await self._legacy_resolver.resolve_frozen_resource(
                run_id=run_id,
                skill_name=skill_name,
                relative_path=relative_path,
            )

        exposure = self._authorities.resolve_exposure(RunId(run_id))
        matches = tuple(
            record
            for record in exposure.catalog.snapshot.records
            if isinstance(record, SkillResourceRecord)
            and record.skill_locator == skill_name
        )
        if len(matches) != 1:
            raise RuntimeError("sdk_frozen_skill_not_in_run_catalog")
        record = matches[0]
        metadata = thaw_json(record.metadata)  # type: ignore[arg-type]
        if not isinstance(metadata, dict):
            raise RuntimeError("sdk_frozen_skill_metadata_invalid")
        scope = PreparedSkillInvocationScopeV1.from_dict(
            {
                "schema": "prepared_skill_invocation_scope/v1",
                "owner_key": metadata.get("owner_key"),
                "pack_id": metadata.get("pack_id"),
                "skill_id": skill_name,
                "version": metadata.get("version"),
                "manifest_hash": metadata.get("manifest_hash"),
                "content_hash": record.content_hash,
                "allowed_tools": metadata.get("allowed_tools"),
                "scope_hash": metadata.get("scope_hash"),
            }
        )
        resolved_path, raw = await self._snapshot_resolver.resolve_skill_resource(
            scope, relative_path
        )
        if len(raw) > 256 * 1024:
            raise RuntimeError("frozen_skill_resource_too_large")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("frozen_skill_resource_not_text") from exc
        return {
            "skill_id": skill_name,
            "resource_path": relative_path,
            "resolved_path": resolved_path,
            "content": content,
            "content_hash": hashlib.sha256(raw).hexdigest(),
            "capability_snapshot_ref": authority.catalog_fingerprint,
        }

    @staticmethod
    def _exact_sdk_tool_fact(spec: Any) -> dict[str, Any]:
        schema_hash = str(spec.schema_hash)
        execution_identity = str(spec.execution_identity)
        dispatch_id = "simple_harness.sdk.product_tool"
        dispatch_version = "v1"
        return {
            "name": str(spec.name),
            "stable_handler_id": (
                f"sdk:{spec.source}:{spec.name}:{spec.spec_version}"
            ),
            "tool_spec_fingerprint": canonical_hash(
                {
                    "name": spec.name,
                    "schema_hash": schema_hash,
                    "description": spec.schema["description"],
                }
            ),
            "schema_hash": schema_hash,
            "execution_build_identity": {
                "sdk_execution_identity": execution_identity,
            },
            "dispatch_adapter_id": dispatch_id,
            "dispatch_adapter_version": dispatch_version,
            "dispatch_adapter_fingerprint": canonical_hash(
                {
                    "id": dispatch_id,
                    "version": dispatch_version,
                    "execution_identity": execution_identity,
                }
            ),
            "effect_policy": {
                "permission_category": spec.permission_category,
                "dangerous": bool(spec.dangerous),
            },
            "idempotency": "sdk_authority_managed",
        }


__all__ = ["SdkThenLegacyFrozenSkillResolver"]
