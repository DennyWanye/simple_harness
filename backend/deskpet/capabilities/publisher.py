# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ToolRegistry-backed atomic capability publisher."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from deskpet.tools.registry import (
    ToolCatalogMutationError,
    ToolCatalogRevisionConflict,
    ToolRegistry,
    ToolSpec,
    legacy_tool_spec_fingerprint_pre_authority_v2,
    legacy_tool_spec_fingerprint_v1,
    tool_spec_fingerprint,
)

from .contracts import CapabilityBinding, JsonValue
from .manager import (
    CapabilityCandidate,
    CapabilityManagerError,
    CapabilityPublication,
    PublishReconciliation,
)
from .manifest import (
    PackEnvironment,
    PackValidationResult,
    load_and_validate_pack,
)
from .store import CapabilityPublishIntent, CapabilityVersionRecord


class CapabilityToolSpecFactory(Protocol):
    def build_specs(
        self,
        validation: PackValidationResult,
        *,
        install_root: Path,
        operation_id: str,
    ) -> Sequence[ToolSpec] | Awaitable[Sequence[ToolSpec]]:
        ...


def capability_registry_source(
    pack_id: str,
    version: str,
    manifest_hash: str,
) -> str:
    return f"capability:{pack_id}:{version}:{manifest_hash}"


def capability_spec_version(version: str, manifest_hash: str) -> str:
    return f"capability-pack:{version}:{manifest_hash}"


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _serialized_spec(spec: ToolSpec) -> Mapping[str, JsonValue]:
    return {
        "name": spec.name,
        "fingerprint": tool_spec_fingerprint(spec),
        "source": spec.source,
        "schema_hash": spec.schema_hash,
        "spec_version": spec.spec_version,
        "runtime_provenance_ref": spec.runtime_provenance_ref,
    }


def _spec_map(
    values: Sequence[Mapping[str, JsonValue]],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name = value.get("name")
        fingerprint = value.get("fingerprint")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(fingerprint, str)
            or len(fingerprint) != 64
        ):
            raise CapabilityManagerError(
                "publish_spec_projection_invalid",
                "persisted ToolSpec projection is malformed",
            )
        if name in result:
            raise CapabilityManagerError(
                "publish_spec_projection_invalid",
                f"duplicate ToolSpec projection: {name}",
            )
        result[name] = fingerprint
    return result


class ToolRegistryCapabilityPublisher:
    """Prepare candidates off-catalog, then swap every provider name once."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        spec_factory: CapabilityToolSpecFactory,
        environment: PackEnvironment | None = None,
        stop_binding: (
            Callable[[CapabilityBinding], Any] | None
        ) = None,
    ) -> None:
        self.registry = registry
        self.spec_factory = spec_factory
        self.environment = environment or PackEnvironment.current()
        self._stop_binding = stop_binding

    @staticmethod
    def _owned_specs(
        snapshot_specs: Sequence[ToolSpec],
        pack_id: str,
    ) -> tuple[ToolSpec, ...]:
        prefix = f"capability:{pack_id}:"
        return tuple(
            spec for spec in snapshot_specs if spec.source.startswith(prefix)
        )

    async def _prepare(
        self,
        validation: PackValidationResult,
        *,
        install_root: Path,
        operation_id: str,
    ) -> CapabilityCandidate:
        manifest = validation.manifest
        if manifest.mcp_servers:
            raise CapabilityManagerError(
                "mcp_runtime_publisher_required",
                "MCP pack publication requires the managed MCP runtime publisher",
            )
        built = tuple(
            await _maybe_await(
                self.spec_factory.build_specs(
                    validation,
                    install_root=install_root,
                    operation_id=operation_id,
                )
            )
        )
        expected_names = tuple(
            tool.provider_name for tool in manifest.tools
        )
        actual_names = tuple(spec.name for spec in built)
        if len(actual_names) != len(set(actual_names)) or set(actual_names) != set(
            expected_names
        ):
            raise CapabilityManagerError(
                "candidate_tool_mismatch",
                "spec factory must return exactly one spec per manifest tool",
            )
        source = capability_registry_source(
            manifest.id,
            manifest.version,
            manifest.manifest_hash,
        )
        spec_version = capability_spec_version(
            manifest.version,
            manifest.manifest_hash,
        )
        by_name = {spec.name: spec for spec in built}
        for name in expected_names:
            spec = by_name[name]
            if spec.source != source:
                raise CapabilityManagerError(
                    "candidate_provenance_mismatch",
                    f"{name} must use registry source {source}",
                )
            if spec.spec_version != spec_version:
                raise CapabilityManagerError(
                    "candidate_spec_version_mismatch",
                    f"{name} must use spec_version {spec_version}",
                )
            if len(spec.schema_hash) != 64 or any(
                character not in "0123456789abcdef"
                for character in spec.schema_hash
            ):
                raise CapabilityManagerError(
                    "candidate_schema_hash_missing",
                    f"{name} has no immutable schema hash",
                )
            if (
                len(spec.runtime_provenance_ref) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in spec.runtime_provenance_ref
                )
            ):
                raise CapabilityManagerError(
                    "candidate_runtime_provenance_missing",
                    f"{name} has no immutable runtime provenance",
                )

        snapshot = self.registry.catalog_snapshot()
        old_specs = self._owned_specs(snapshot.specs, manifest.id)
        old_names = {spec.name for spec in old_specs}
        for spec in built:
            current = next(
                (
                    item
                    for item in snapshot.specs
                    if item.name == spec.name
                ),
                None,
            )
            if current is not None and spec.name not in old_names:
                raise CapabilityManagerError(
                    "provider_name_collision",
                    f"provider name is owned by {current.source}: {spec.name}",
                )

        old_projection = tuple(
            _serialized_spec(spec)
            for spec in sorted(old_specs, key=lambda item: item.name)
        )
        new_projection = tuple(
            _serialized_spec(spec)
            for spec in sorted(built, key=lambda item: item.name)
        )
        fingerprints_by_name = {
            spec.name: tool_spec_fingerprint(spec) for spec in built
        }
        return CapabilityCandidate(
            expected_registry_revision=snapshot.revision,
            tool_spec_fingerprints=tuple(
                fingerprints_by_name[name] for name in expected_names
            ),
            old_specs=old_projection,
            new_specs=new_projection,
            publisher_state={
                "pack_id": manifest.id,
                "version": manifest.version,
                "manifest_hash": manifest.manifest_hash,
            },
            runtime_payload=built,
        )

    async def prepare_candidate(
        self,
        validation: PackValidationResult,
        *,
        install_root: Path,
        operation_id: str,
    ) -> CapabilityCandidate:
        return await self._prepare(
            validation,
            install_root=install_root,
            operation_id=operation_id,
        )

    async def _prepare_installed_candidate(
        self,
        record: CapabilityVersionRecord,
        *,
        operation_id: str,
    ) -> CapabilityCandidate:
        validation = load_and_validate_pack(
            record.install_path,
            environment=self.environment,
        )
        if (
            validation.descriptor.capability_id
            != record.descriptor.capability_id
            or validation.descriptor.version != record.descriptor.version
            or validation.descriptor.manifest_hash
            != record.descriptor.manifest_hash
        ):
            raise CapabilityManagerError(
                "installed_version_integrity_mismatch",
                "installed pack no longer matches its immutable store record",
            )
        candidate = await self._prepare(
            validation,
            install_root=record.install_path,
            operation_id=operation_id,
        )
        return candidate

    async def prepare_installed(
        self,
        record: CapabilityVersionRecord,
        *,
        operation_id: str,
    ) -> CapabilityCandidate:
        candidate = await self._prepare_installed_candidate(
            record,
            operation_id=operation_id,
        )
        if (
            candidate.tool_spec_fingerprints
            != record.expected_tool_fingerprints
        ):
            raise CapabilityManagerError(
                "installed_tool_fingerprint_mismatch",
                "rollback candidate differs from the recorded ToolSpecs",
            )
        return candidate

    async def prepare_installed_for_rehydrate(
        self,
        record: CapabilityVersionRecord,
        *,
        operation_id: str,
    ) -> tuple[CapabilityCandidate, str | None]:
        """Prepare startup replay and recognize exact legacy projections.

        The immutable pack is validated before this comparison.  A mismatch is
        accepted only when every recorded fingerprint is exactly reproducible;
        arbitrary or tampered values still fail closed.
        """

        candidate = await self._prepare_installed_candidate(
            record,
            operation_id=operation_id,
        )
        if (
            candidate.tool_spec_fingerprints
            == record.expected_tool_fingerprints
        ):
            return candidate, None
        specs = candidate.runtime_payload
        if not isinstance(specs, Sequence) or not all(
            isinstance(spec, ToolSpec) for spec in specs
        ):
            raise CapabilityManagerError(
                "installed_tool_fingerprint_mismatch",
                "rollback candidate differs from the recorded ToolSpecs",
            )
        legacy_fingerprints = tuple(
            legacy_tool_spec_fingerprint_v1(spec) for spec in specs
        )
        if legacy_fingerprints == record.expected_tool_fingerprints:
            return candidate, "legacy_v1"
        pre_authority_fingerprints = tuple(
            legacy_tool_spec_fingerprint_pre_authority_v2(spec)
            for spec in specs
        )
        if (
            pre_authority_fingerprints
            == record.expected_tool_fingerprints
        ):
            return candidate, "pre_authority_v2"
        raise CapabilityManagerError(
            "installed_tool_fingerprint_mismatch",
            "rollback candidate differs from the recorded ToolSpecs",
        )

    async def prepare_uninstall(
        self,
        record: CapabilityVersionRecord,
        binding: CapabilityBinding,
        *,
        operation_id: str,
    ) -> CapabilityCandidate:
        del operation_id
        if (
            binding.capability_id != record.descriptor.capability_id
            or binding.version != record.descriptor.version
            or binding.manifest_hash != record.descriptor.manifest_hash
        ):
            raise CapabilityManagerError(
                "uninstall_binding_mismatch",
                "uninstall binding does not select the supplied version",
            )
        snapshot = self.registry.catalog_snapshot()
        old_specs = self._owned_specs(
            snapshot.specs,
            binding.capability_id,
        )
        old_fingerprints = tuple(
            tool_spec_fingerprint(spec)
            for spec in sorted(old_specs, key=lambda item: item.name)
        )
        if set(old_fingerprints) != set(record.expected_tool_fingerprints):
            raise CapabilityManagerError(
                "uninstall_registry_state_mismatch",
                "live registry does not match the bound immutable version",
            )
        return CapabilityCandidate(
            expected_registry_revision=snapshot.revision,
            tool_spec_fingerprints=(),
            old_specs=tuple(
                _serialized_spec(spec)
                for spec in sorted(old_specs, key=lambda item: item.name)
            ),
            new_specs=(),
            publisher_state={
                "pack_id": binding.capability_id,
                "uninstall": True,
            },
            runtime_payload=(),
        )

    async def publish(
        self,
        candidate: CapabilityCandidate,
        *,
        operation_id: str,
    ) -> CapabilityPublication:
        del operation_id
        if not isinstance(candidate.runtime_payload, tuple) or not all(
            isinstance(spec, ToolSpec) for spec in candidate.runtime_payload
        ):
            raise CapabilityManagerError(
                "candidate_runtime_payload_missing",
                "prepared ToolSpecs are unavailable for atomic publication",
            )
        old_specs = _spec_map(candidate.old_specs)
        new_specs = _spec_map(candidate.new_specs)
        expected: dict[str, str | None] = {
            name: fingerprint for name, fingerprint in old_specs.items()
        }
        for name in new_specs:
            expected.setdefault(name, None)
        try:
            snapshot = self.registry.compare_and_swap_catalog(
                expected_revision=candidate.expected_registry_revision,
                expected_fingerprints=expected,
                replacements=candidate.runtime_payload,
            )
        except ToolCatalogRevisionConflict as exc:
            raise CapabilityManagerError(
                "registry_revision_conflict",
                str(exc),
            ) from exc
        except ToolCatalogMutationError as exc:
            raise CapabilityManagerError(
                "registry_catalog_conflict",
                str(exc),
            ) from exc

        live = {
            spec.name: tool_spec_fingerprint(spec)
            for spec in snapshot.specs
        }
        if any(live.get(name) != fingerprint for name, fingerprint in new_specs.items()):
            raise CapabilityManagerError(
                "registry_publish_verification_failed",
                "published registry does not contain the candidate ToolSpecs",
            )
        if any(name in live for name in set(old_specs) - set(new_specs)):
            raise CapabilityManagerError(
                "registry_publish_verification_failed",
                "retired provider names remain in the live registry",
            )
        return CapabilityPublication(
            registry_revision=snapshot.revision,
            tool_spec_fingerprints=candidate.tool_spec_fingerprints,
            evidence={
                "atomic": True,
                "published_names": sorted(new_specs),
                "retired_names": sorted(set(old_specs) - set(new_specs)),
            },
        )

    async def reconcile(
        self,
        intent: CapabilityPublishIntent,
    ) -> PublishReconciliation:
        try:
            old_specs = _spec_map(intent.old_specs)
            new_specs = _spec_map(intent.new_specs)
        except CapabilityManagerError as exc:
            return PublishReconciliation(
                status="unknown",
                evidence={"code": exc.code, "message": str(exc)},
            )
        snapshot = self.registry.catalog_snapshot()
        live = {
            spec.name: tool_spec_fingerprint(spec)
            for spec in snapshot.specs
        }
        names = set(old_specs) | set(new_specs)

        def matches(target: Mapping[str, str]) -> bool:
            return all(
                (
                    live.get(name) == target[name]
                    if name in target
                    else name not in live
                )
                for name in names
            )

        if matches(new_specs):
            return PublishReconciliation(
                status="published",
                registry_revision=snapshot.revision,
                tool_spec_fingerprints=tuple(new_specs.values()),
                evidence={"atomic": True, "state": "new"},
            )
        if matches(old_specs):
            return PublishReconciliation(
                status="rolled_back",
                registry_revision=snapshot.revision,
                tool_spec_fingerprints=tuple(old_specs.values()),
                evidence={"atomic": True, "state": "old"},
            )
        return PublishReconciliation(
            status="unknown",
            registry_revision=snapshot.revision,
            evidence={"atomic": True, "state": "mixed_or_foreign"},
        )

    async def stop_binding(self, binding: CapabilityBinding) -> None:
        if self._stop_binding is not None:
            await _maybe_await(self._stop_binding(binding))


__all__ = [
    "CapabilityToolSpecFactory",
    "ToolRegistryCapabilityPublisher",
    "capability_registry_source",
    "capability_spec_version",
]
