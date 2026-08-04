# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Transactional capability-pack lifecycle orchestrator."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .contracts import (
    CapabilityBinding,
    CapabilityScopeKind,
    JsonValue,
    OwnerScopeKey,
    fingerprint_json,
)
from .manifest import (
    PackEnvironment,
    PackValidationResult,
    load_and_validate_pack,
    windows_extended_path,
)
from .package_limits import (
    CapabilityPackageValidationError,
    CapabilityPackageValidator,
    ValidatedCapabilityPackageRefV1,
)
from .source import CapabilitySourceResolver, PackSourceRequest, StagedPack
from .store import (
    CAPABILITY_OPERATION_PHASES,
    CapabilityOperationRecord,
    CapabilityPublishIntent,
    CapabilityStore,
    CapabilityStoreConflict,
    CapabilityVersionRecord,
)


class CapabilityManagerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_VALIDATED_REF_FIELDS = (
    "source_kind",
    "policy_version",
    "baseline_hash",
    "policy_hash",
    "archive_hash",
    "manifest_hash",
    "file_set_hash",
    "entry_count",
    "total_uncompressed_bytes",
    "validation_receipt_hash",
)


def _manifest_source_matches(
    *, requested_source_type: str, manifest_source_type: str
) -> bool:
    """Match logical archive transports to their legacy manifest identity."""

    if requested_source_type in {"local_archive", "companion_growth"}:
        return manifest_source_type in {requested_source_type, "local"}
    return manifest_source_type == requested_source_type


@dataclass(frozen=True, slots=True)
class CapabilityLayout:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve(strict=False))

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def packs(self) -> Path:
        return self.root / "packs"

    @property
    def environments(self) -> Path:
        return self.root / "envs"

    @property
    def run_packs(self) -> Path:
        return self.root / "run"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def staging_path(self, operation_id: str) -> Path:
        return self.staging / operation_id

    def pack_path(self, pack_id: str, version: str, manifest_hash: str) -> Path:
        return self.packs / pack_id / version / manifest_hash

    def environment_path(
        self, pack_id: str, version: str, manifest_hash: str
    ) -> Path:
        return self.environments / pack_id / version / manifest_hash

    def ensure(self) -> None:
        for path in (
            self.staging,
            self.packs,
            self.environments,
            self.run_packs,
            self.logs,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class CapabilityCandidate:
    expected_registry_revision: int
    tool_spec_fingerprints: tuple[str, ...]
    old_specs: tuple[Mapping[str, JsonValue], ...]
    new_specs: tuple[Mapping[str, JsonValue], ...]
    publisher_state: Mapping[str, JsonValue]
    runtime_payload: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class CapabilityPublication:
    registry_revision: int
    tool_spec_fingerprints: tuple[str, ...]
    evidence: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class PublishReconciliation:
    status: str
    registry_revision: int | None = None
    tool_spec_fingerprints: tuple[str, ...] = ()
    evidence: Mapping[str, JsonValue] | None = None


class CapabilityPublisher(Protocol):
    async def prepare_candidate(
        self,
        validation: PackValidationResult,
        *,
        install_root: Path,
        operation_id: str,
    ) -> CapabilityCandidate:
        ...

    async def prepare_installed(
        self,
        record: CapabilityVersionRecord,
        *,
        operation_id: str,
    ) -> CapabilityCandidate:
        ...

    async def prepare_uninstall(
        self,
        record: CapabilityVersionRecord,
        binding: CapabilityBinding,
        *,
        operation_id: str,
    ) -> CapabilityCandidate:
        ...

    async def publish(
        self,
        candidate: CapabilityCandidate,
        *,
        operation_id: str,
    ) -> CapabilityPublication:
        ...

    async def reconcile(
        self, intent: CapabilityPublishIntent
    ) -> PublishReconciliation:
        ...

    async def stop_binding(self, binding: CapabilityBinding) -> None:
        ...


class CapabilityEnvironmentPreparer(Protocol):
    async def prepare(
        self,
        validation: PackValidationResult,
        *,
        environment_root: Path,
        operation_id: str,
    ) -> Mapping[str, JsonValue]:
        ...

    async def probe(
        self,
        record: CapabilityOperationRecord,
        *,
        environment_root: Path | None,
    ) -> Mapping[str, JsonValue]:
        ...


class _AsyncLock(Protocol):
    async def __aenter__(self) -> Any:
        ...

    async def __aexit__(self, *args: Any) -> None:
        ...


class FoundationPublisher:
    """Safe default for instruction-only packs.

    Executable packs fail explicitly until production injects the ToolRegistry
    / MCP publisher; the foundation never claims a tool is active without that
    authority.
    """

    async def prepare_candidate(
        self,
        validation: PackValidationResult,
        *,
        install_root: Path,
        operation_id: str,
    ) -> CapabilityCandidate:
        del install_root, operation_id
        if validation.manifest.tools or validation.manifest.mcp_servers:
            raise CapabilityManagerError(
                "publisher_required",
                "executable packs require the production registry/MCP publisher",
            )
        return CapabilityCandidate(
            expected_registry_revision=0,
            tool_spec_fingerprints=(),
            old_specs=(),
            new_specs=(),
            publisher_state={},
        )

    async def prepare_installed(
        self,
        record: CapabilityVersionRecord,
        *,
        operation_id: str,
    ) -> CapabilityCandidate:
        del operation_id
        if record.descriptor.provider_tool_names:
            raise CapabilityManagerError(
                "publisher_required",
                "executable rollback requires the production registry publisher",
            )
        return CapabilityCandidate(0, (), (), (), {})

    async def prepare_uninstall(
        self,
        record: CapabilityVersionRecord,
        binding: CapabilityBinding,
        *,
        operation_id: str,
    ) -> CapabilityCandidate:
        del binding, operation_id
        if record.descriptor.provider_tool_names:
            raise CapabilityManagerError(
                "publisher_required",
                "executable uninstall requires the production registry publisher",
            )
        return CapabilityCandidate(0, (), (), (), {})

    async def publish(
        self,
        candidate: CapabilityCandidate,
        *,
        operation_id: str,
    ) -> CapabilityPublication:
        del operation_id
        return CapabilityPublication(
            registry_revision=candidate.expected_registry_revision,
            tool_spec_fingerprints=candidate.tool_spec_fingerprints,
            evidence={"kind": "instruction_only"},
        )

    async def reconcile(
        self, intent: CapabilityPublishIntent
    ) -> PublishReconciliation:
        # No executable state was touched by the foundation publisher.
        if intent.old_specs or intent.new_specs:
            return PublishReconciliation(status="unknown")
        return PublishReconciliation(
            status="published",
            registry_revision=intent.expected_registry_revision,
            evidence={"kind": "instruction_only"},
        )

    async def stop_binding(self, binding: CapabilityBinding) -> None:
        del binding


class FoundationEnvironmentPreparer:
    """Create an empty immutable environment only when no deps are declared."""

    async def prepare(
        self,
        validation: PackValidationResult,
        *,
        environment_root: Path,
        operation_id: str,
    ) -> Mapping[str, JsonValue]:
        del operation_id
        dependencies = validation.manifest.dependencies
        if dependencies.python or dependencies.commands:
            raise CapabilityManagerError(
                "environment_preparer_required",
                "declared dependencies require the production environment preparer",
            )
        windows_extended_path(environment_root).mkdir(parents=True, exist_ok=True)
        return {"kind": "empty", "path": str(environment_root)}

    async def probe(
        self,
        record: CapabilityOperationRecord,
        *,
        environment_root: Path | None,
    ) -> Mapping[str, JsonValue]:
        del record
        return {
            "exists": bool(environment_root and environment_root.is_dir()),
            "kind": "empty",
        }


@dataclass(frozen=True, slots=True)
class CapabilityInstallResult:
    operation: CapabilityOperationRecord
    validation: PackValidationResult
    binding: CapabilityBinding
    install_path: Path
    registry_revision: int
    tool_spec_fingerprints: tuple[str, ...]
    manager_receipt_hash: str = ""
    committed_owner_binding_set_stamp: str = ""
    process_projection_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityRollbackResult:
    operation: CapabilityOperationRecord
    binding: CapabilityBinding
    registry_revision: int


@dataclass(frozen=True, slots=True)
class CapabilityUninstallResult:
    operation: CapabilityOperationRecord
    binding: CapabilityBinding
    files_retained: bool
    registry_revision: int = 0
    manager_receipt_hash: str = ""
    committed_owner_binding_set_stamp: str = ""
    process_projection_fingerprint: str = ""


def _operation_id(idempotency_key: str) -> str:
    if not idempotency_key.strip():
        raise CapabilityManagerError(
            "missing_idempotency_key", "idempotency key is required"
        )
    return hashlib.sha256(f"capability:{idempotency_key}".encode("utf-8")).hexdigest()


def capability_operation_id(idempotency_key: str) -> str:
    """Return the Manager-owned stable operation identity for one request."""

    return _operation_id(idempotency_key)


def _intent_id(operation_id: str) -> str:
    return hashlib.sha256(f"{operation_id}:publish".encode("utf-8")).hexdigest()


class CapabilityPackManager:
    """Run the fixed package lifecycle with durable phase intents."""

    def __init__(
        self,
        *,
        store: CapabilityStore,
        user_data_root: str | Path,
        source_resolver: CapabilitySourceResolver | None = None,
        publisher: CapabilityPublisher | None = None,
        environment_preparer: CapabilityEnvironmentPreparer | None = None,
        environment: PackEnvironment | None = None,
        publish_lock: _AsyncLock | None = None,
        fault_injector: Callable[[str], Any] | None = None,
        package_validator: CapabilityPackageValidator | None = None,
    ) -> None:
        self.store = store
        self.layout = CapabilityLayout(
            Path(user_data_root) / "capabilities"
        )
        self.package_validator = (
            package_validator or CapabilityPackageValidator()
        )
        self.source_resolver = source_resolver or CapabilitySourceResolver(
            package_validator=self.package_validator
        )
        self.publisher = publisher or FoundationPublisher()
        self.environment_preparer = (
            environment_preparer or FoundationEnvironmentPreparer()
        )
        self.environment = environment or PackEnvironment.current()
        self.publish_lock = publish_lock or asyncio.Lock()
        self._fault_injector = fault_injector

    async def initialize(self) -> None:
        await self.store.initialize()
        await asyncio.to_thread(self.layout.ensure)

    async def _fault(self, point: str) -> None:
        if self._fault_injector is None:
            return
        value = self._fault_injector(point)
        if inspect.isawaitable(value):
            await value

    async def _commit_phase(
        self,
        operation_id: str,
        phase: str,
        evidence: Mapping[str, JsonValue] | None = None,
    ) -> CapabilityOperationRecord:
        operation = await self.store.commit_phase(
            operation_id, phase, evidence=evidence
        )
        await self._fault(f"after:{phase}")
        return operation

    async def _remove_exact_tree(self, target: Path) -> None:
        target = target.resolve(strict=False)
        staging_root = self.layout.staging.resolve(strict=False)
        try:
            target.relative_to(staging_root)
        except ValueError as exc:
            raise CapabilityManagerError(
                "unsafe_cleanup_target",
                f"cleanup target is outside capability staging: {target}",
            ) from exc
        if target.exists():
            await asyncio.to_thread(shutil.rmtree, target)

    def _require_staged_validated_ref(
        self,
        staged: StagedPack,
        *,
        resolved_source: PackSourceRequest,
    ) -> ValidatedCapabilityPackageRefV1:
        validated_ref = staged.validated_ref
        if not isinstance(validated_ref, ValidatedCapabilityPackageRefV1):
            raise CapabilityManagerError(
                "validated_package_ref_required",
                "capability sources must provide a host-issued validated package ref",
            )
        if validated_ref.source_kind != resolved_source.source_type:
            raise CapabilityManagerError(
                "validated_package_source_mismatch",
                "validated package source differs from the resolved source",
            )
        return validated_ref

    @staticmethod
    def _validated_ref_evidence(
        validated_ref: ValidatedCapabilityPackageRefV1,
    ) -> Mapping[str, JsonValue]:
        return {
            name: getattr(validated_ref, name)
            for name in _VALIDATED_REF_FIELDS
        }

    def _validated_ref_from_evidence(
        self, evidence: Mapping[str, JsonValue] | None
    ) -> ValidatedCapabilityPackageRefV1:
        raw = None if evidence is None else evidence.get("validated_ref")
        if not isinstance(raw, Mapping):
            raise CapabilityManagerError(
                "validated_package_ref_missing",
                "staged operation lacks its durable validated package ref",
            )
        try:
            validated_ref = ValidatedCapabilityPackageRefV1.issue(
                source_kind=str(raw["source_kind"]),
                limits=self.package_validator.limits,
                archive_hash=str(raw["archive_hash"]),
                manifest_hash=str(raw["manifest_hash"]),
                file_set_hash=str(raw["file_set_hash"]),
                entry_count=int(raw["entry_count"]),
                total_uncompressed_bytes=int(raw["total_uncompressed_bytes"]),
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            CapabilityPackageValidationError,
        ) as exc:
            raise CapabilityManagerError(
                "validated_package_ref_invalid",
                "staged operation contains an invalid validated package ref",
            ) from exc
        if any(
            raw.get(name) != getattr(validated_ref, name)
            for name in _VALIDATED_REF_FIELDS
        ):
            raise CapabilityManagerError(
                "validated_package_ref_invalid",
                "staged validated package ref does not match the active policy",
            )
        return validated_ref

    async def _validate_materialized_tree(
        self,
        root: Path,
        *,
        expected_ref: ValidatedCapabilityPackageRefV1,
    ) -> ValidatedCapabilityPackageRefV1:
        try:
            return await asyncio.to_thread(
                self.package_validator.validate_materialized_tree,
                root,
                source_kind=expected_ref.source_kind,
                expected_ref=expected_ref,
            )
        except CapabilityPackageValidationError as exc:
            raise CapabilityManagerError(exc.code, str(exc)) from exc

    async def _record_operation_failure(
        self, operation_id: str, exc: Exception
    ) -> None:
        """Settle an ordinary lifecycle error without masking hard crashes."""

        latest = await self.store.get_operation(operation_id)
        if latest is None or latest.status != "running":
            return
        status = (
            "unknown"
            if latest.phase in {"publish_intent", "catalog_swapped", "bound"}
            else "failed"
        )
        await self.store.fail_operation(
            operation_id,
            status=status,
            error={
                "code": str(getattr(exc, "code", "capability_error")),
                "message": str(exc),
                "phase": latest.phase,
                "reconciliation_required": status == "unknown",
            },
        )
        staging_path = self.layout.staging_path(operation_id)
        if status == "failed" and staging_path.exists():
            await self._remove_exact_tree(staging_path)

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
        old_binding_payload = (
            None if old_binding is None else old_binding.to_dict()
        )
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

        publication = await self.publisher.publish(
            candidate, operation_id=operation_id
        )
        if (
            tuple(publication.tool_spec_fingerprints)
            != tuple(candidate.tool_spec_fingerprints)
        ):
            raise CapabilityManagerError(
                "publisher_fingerprint_mismatch",
                "published ToolSpecs differ from the verified candidate",
            )
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.advance_publish_intent(
                intent_id, phase="catalog_swapped", status="pending"
            )
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
            await tx.advance_publish_intent(
                intent_id, phase="bound", status="committed"
            )
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

        publication = await self.publisher.publish(
            candidate, operation_id=operation_id
        )
        if tuple(publication.tool_spec_fingerprints) != tuple(
            candidate.tool_spec_fingerprints
        ):
            raise CapabilityManagerError(
                "publisher_fingerprint_mismatch",
                "unpublished ToolSpecs differ from the verified candidate",
            )
        async with self.store.write_transaction() as db:
            tx = self.store.bind(db)
            await tx.advance_publish_intent(
                intent_id, phase="catalog_swapped", status="pending"
            )
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
            await tx.advance_publish_intent(
                intent_id, phase="bound", status="committed"
            )
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
                "process_projection_fingerprint": (
                    process_projection_fingerprint
                ),
            }
        )
        return {
            "manager_receipt_hash": manager_receipt_hash,
            "committed_owner_binding_set_stamp": (
                owner_token.committed_owner_binding_set_stamp
            ),
            "process_projection_fingerprint": (
                process_projection_fingerprint
            ),
        }

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
        resolved_source = self.source_resolver.resolve_declared_source(source)
        operation_id = _operation_id(idempotency_key)
        request: Mapping[str, JsonValue] = {
            "source": {
                "type": source.source_type,
                "uri": source.uri,
                "revision": source.revision,
                "subdirectory": source.subdirectory,
            },
            "scope": scope,
            "scope_key": scope_key,
            "generated": generated,
            "expected_pack_id": expected_pack_id,
            "parent_version": parent_version,
            "parent_manifest_hash": parent_manifest_hash,
            "derived_from_receipt_ref": derived_from_receipt_ref,
        }
        # Keep the historical request envelope byte-for-byte stable for
        # ordinary installs.  These lifecycle fields were added later for
        # Companion-owned mutations; serializing them as explicit nulls would
        # turn a valid replay of an older first-party install into an
        # idempotency conflict at startup.
        lifecycle_request_fields: dict[str, JsonValue] = {}
        if owner_key is not None:
            lifecycle_request_fields["owner_key"] = owner_key
        if expected_binding_generation is not None:
            lifecycle_request_fields["expected_binding_generation"] = (
                expected_binding_generation
            )
        if management_policy is not None:
            lifecycle_request_fields["management_policy"] = management_policy
        request = {**request, **lifecycle_request_fields}
        operation = await self.store.create_operation(
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            kind=kind,
            request=request,
            root_run_id=root_run_id,
            pack_id=expected_pack_id,
            requested_scope=scope,
            requested_scope_key=scope_key,
        )
        if operation.status == "succeeded":
            return await self._result_for_completed_install(operation)
        if operation.status != "running":
            stored_error = dict(operation.error or {})
            raise CapabilityManagerError(
                str(stored_error.get("code") or "operation_not_resumable"),
                str(
                    stored_error.get("message")
                    or f"operation is {operation.status} at {operation.phase}"
                ),
            )
        staging_path = self.layout.staging_path(operation_id)
        validated_ref: ValidatedCapabilityPackageRefV1 | None = None
        try:
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
                        "validated_ref": dict(
                            self._validated_ref_evidence(validated_ref)
                        ),
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
                        await self.store.get_phase_evidence(
                            operation_id, "staged"
                        )
                    )
                if validated_ref.source_kind != resolved_source.source_type:
                    raise CapabilityManagerError(
                        "validated_package_source_mismatch",
                        "durable validated package source differs from "
                        "the resolved source",
                    )
                validation_root = staging_path
            else:
                verified_evidence = await self.store.get_phase_evidence(
                    operation_id, "verified"
                )
                install_path_value = (
                    None
                    if verified_evidence is None
                    else verified_evidence.get("install_path")
                )
                if not isinstance(install_path_value, str):
                    raise CapabilityManagerError(
                        "verified_projection_missing",
                        "verified operation lacks its immutable install path",
                    )
                validation_root = Path(install_path_value)

            validation = load_and_validate_pack(
                validation_root, environment=self.environment
            )
            manifest = validation.manifest
            if expected_pack_id is not None and manifest.id != expected_pack_id:
                raise CapabilityManagerError(
                    "unexpected_pack_id",
                    f"expected {expected_pack_id}, staged pack is {manifest.id}",
                )
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
                tool.execution_profile != "brokered-effect-v1"
                for tool in manifest.tools
            ):
                raise CapabilityManagerError(
                    "generated_native_adapter_forbidden",
                    "generated packs may only use brokered-effect-v1",
                )
            operation = await self.store.bind_operation_pack_id(
                operation_id, manifest.id
            )
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
                    if (
                        installed_validation.manifest.manifest_hash
                        != manifest.manifest_hash
                    ):
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
                    validation = load_and_validate_pack(
                        install_io_path, environment=self.environment
                    )
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

            candidate = await self.publisher.prepare_candidate(
                validation,
                install_root=install_path,
                operation_id=operation_id,
            )
            if len(candidate.tool_spec_fingerprints) != len(
                manifest.tools
            ):
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
                    {
                        "tool_spec_fingerprints": list(
                            candidate.tool_spec_fingerprints
                        )
                    },
                )

            if operation.phase in {"publish_intent", "catalog_swapped", "bound"}:
                raise CapabilityManagerError(
                    "publish_reconciliation_required",
                    f"operation at {operation.phase} must be reconciled before resume",
                )

            current_binding = await self.store.get_binding(
                scope,
                scope_key,
                manifest.id,
                owner_key=owner_key,
            )
            actual_generation = (
                0 if current_binding is None else current_binding.generation
            )
            expected_generation = (
                actual_generation
                if expected_binding_generation is None
                else expected_binding_generation
            )
            binding, publication = await self._publish_and_bind(
                operation_id=operation_id,
                candidate=candidate,
                pack_id=manifest.id,
                version=manifest.version,
                manifest_hash=manifest.manifest_hash,
                scope=scope,
                scope_key=scope_key,
                expected_binding_generation=expected_generation,
                owner_key=owner_key,
                management_policy=management_policy,
            )
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
                manager_receipt_hash=str(
                    receipt_evidence["manager_receipt_hash"]
                ),
                committed_owner_binding_set_stamp=str(
                    receipt_evidence[
                        "committed_owner_binding_set_stamp"
                    ]
                ),
                process_projection_fingerprint=str(
                    receipt_evidence["process_projection_fingerprint"]
                ),
            )
        except asyncio.CancelledError:
            await self._cancel_operation(operation_id)
            raise
        except Exception as exc:
            await self._record_operation_failure(operation_id, exc)
            raise

    async def update(self, source: PackSourceRequest, **kwargs: Any) -> CapabilityInstallResult:
        return await self.install(source, kind="update", **kwargs)

    async def repair(
        self, source: PackSourceRequest, **kwargs: Any
    ) -> CapabilityInstallResult:
        return await self.install(source, kind="repair", **kwargs)

    async def _result_for_completed_install(
        self, operation: CapabilityOperationRecord
    ) -> CapabilityInstallResult:
        pack_id = operation.pack_id
        scope = operation.requested_scope
        scope_key = operation.requested_scope_key
        if not pack_id or not scope or not scope_key:
            intent = await self.store.get_publish_intent_for_operation(
                operation.operation_id
            )
            if intent is None:
                raise CapabilityManagerError(
                    "completed_operation_projection_missing",
                    "completed install lacks a direct result projection",
                )
            pack_id_value = intent.new_binding.get("capability_id")
            scope_value = intent.new_binding.get("scope")
            scope_key_value = intent.new_binding.get("scope_key")
            if not all(
                isinstance(value, str)
                for value in (pack_id_value, scope_value, scope_key_value)
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
            owner_key=(
                None if not isinstance(owner_key, str) else owner_key
            ),
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
        validation = load_and_validate_pack(
            record.install_path, environment=self.environment
        )
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
            manager_receipt_hash=str(
                evidence.get("manager_receipt_hash") or ""
            ),
            committed_owner_binding_set_stamp=str(
                evidence.get("committed_owner_binding_set_stamp") or ""
            ),
            process_projection_fingerprint=str(
                evidence.get("process_projection_fingerprint") or ""
            ),
        )

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
        record = await self.store.get_version(
            pack_id, target_version, target_manifest_hash
        )
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
            await self._commit_phase(
                operation_id, phase, {"reused_version": target_version}
            )
        candidate = await self.publisher.prepare_installed(
            record, operation_id=operation_id
        )
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
            operation_request["expected_binding_generation"] = (
                expected_binding_generation
            )
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
                manager_receipt_hash=str(
                    evidence.get("manager_receipt_hash") or ""
                ),
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
            manager_receipt_hash=str(
                receipt_evidence["manager_receipt_hash"]
            ),
            committed_owner_binding_set_stamp=str(
                receipt_evidence["committed_owner_binding_set_stamp"]
            ),
            process_projection_fingerprint=str(
                receipt_evidence["process_projection_fingerprint"]
            ),
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
                    "requires_environment_probe": operation.phase
                    == "environment_ready",
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

    async def cancel_operation(
        self, operation_id: str
    ) -> CapabilityOperationRecord:
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
            management_policy=str(
                payload.get("management_policy") or "legacy_import"
            ),  # type: ignore[arg-type]
            management_generation=int(
                payload.get("management_generation") or 0
            ),
        )

    async def _recover_publish_operation(
        self, operation: CapabilityOperationRecord
    ) -> CapabilityOperationRecord:
        intent = await self.store.get_publish_intent_for_operation(
            operation.operation_id
        )
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
            if operation.kind == "uninstall":
                try:
                    old_binding = self._binding_from_intent_payload(
                        intent.old_binding
                    )
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
                    None
                    if new_payload.get("owner_key") is None
                    else str(new_payload["owner_key"])
                ),
            )
            if (
                new_binding is None
                or new_binding.version != new_payload.get("version")
                or new_binding.manifest_hash
                != new_payload.get("manifest_hash")
                or new_binding.active
                != bool(new_payload.get("active", True))
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
                    catalog_evidence.get("registry_revision")
                    or intent.expected_registry_revision
                ),
                tool_spec_fingerprints=(
                    version.expected_tool_fingerprints
                ),
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
            isinstance(new_binding[key], str)
            and bool(str(new_binding[key]).strip())
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
            operation = (
                await self.store.get_operation(operation.operation_id)
                or operation
            )

        if operation.phase == "catalog_swapped":
            scope = str(new_binding["scope"])
            scope_key = str(new_binding["scope_key"])
            capability_id = str(new_binding["capability_id"])
            current = await self.store.get_binding(
                scope,
                scope_key,
                capability_id,
                owner_key=(
                    None
                    if new_binding.get("owner_key") is None
                    else str(new_binding["owner_key"])
                ),
            )
            try:
                old_binding = self._binding_from_intent_payload(
                    intent.old_binding
                )
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
            if (
                (old_binding is None and current is not None)
                or (
                    old_binding is not None
                    and (
                        current is None
                        or current.fingerprint != old_binding.fingerprint
                    )
                )
            ):
                return await self.store.fail_operation(
                    operation.operation_id,
                    status="unknown",
                    error={
                        "code": "binding_reconciliation_conflict",
                        "phase": operation.phase,
                    },
                )
            expected_generation = (
                0 if old_binding is None else old_binding.generation
            )
            enabled = bool(new_binding.get("active", True))
            try:
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
                            None
                            if new_binding.get("owner_key") is None
                            else str(new_binding["owner_key"])
                        ),
                        management_policy=(
                            None
                            if new_binding.get("management_policy") is None
                            else str(new_binding["management_policy"])
                        ),
                    )
                    await tx.advance_publish_intent(
                        intent.intent_id, phase="bound", status="committed"
                    )
                    await tx.commit_phase(
                        operation.operation_id,
                        "bound",
                        evidence={
                            "binding_id": binding.binding_id,
                            "recovered": True,
                        },
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
            operation = (
                await self.store.get_operation(operation.operation_id)
                or operation
            )

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
                old_binding = self._binding_from_intent_payload(
                    intent.old_binding
                )
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
                    None
                    if new_binding.get("owner_key") is None
                    else str(new_binding["owner_key"])
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
                    tool_spec_fingerprints=(
                        version.expected_tool_fingerprints
                    ),
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
        recovered: list[CapabilityOperationRecord] = []
        for operation in await self.store.list_recoverable_operations():
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
                probe = await self.environment_preparer.probe(
                    operation, environment_root=None
                )
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
                recovered.append(
                    await self._recover_publish_operation(operation)
                )
        return tuple(recovered)

    async def rehydrate_active_bindings(
        self,
    ) -> tuple[CapabilityPublication, ...]:
        """Rebuild the process-local registry from durable active bindings."""

        await self.initialize()
        publications: list[CapabilityPublication] = []
        async with self.publish_lock:
            for record in await self.store.list_active_versions():
                operation_id = hashlib.sha256(
                    (
                        "capability-rehydrate|"
                        f"{record.descriptor.capability_id}|"
                        f"{record.descriptor.version}|"
                        f"{record.descriptor.manifest_hash}"
                    ).encode("utf-8")
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
                            expected_fingerprints=(
                                record.expected_tool_fingerprints
                            ),
                            replacement_fingerprints=(
                                candidate.tool_spec_fingerprints
                            ),
                        )
                else:
                    candidate = await self.publisher.prepare_installed(
                        record,
                        operation_id=operation_id,
                    )
                publications.append(
                    await self.publisher.publish(
                        candidate,
                        operation_id=operation_id,
                    )
                )
        return tuple(publications)


__all__ = [
    "CapabilityCandidate",
    "CapabilityEnvironmentPreparer",
    "CapabilityInstallResult",
    "CapabilityLayout",
    "CapabilityManagerError",
    "CapabilityPackManager",
    "CapabilityPublication",
    "CapabilityPublisher",
    "CapabilityRollbackResult",
    "CapabilityUninstallResult",
    "FoundationEnvironmentPreparer",
    "FoundationPublisher",
    "PublishReconciliation",
    "capability_operation_id",
]
