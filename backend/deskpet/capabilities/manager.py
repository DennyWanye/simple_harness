# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Transactional capability-pack lifecycle orchestrator."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import io
import inspect
import json
import os
import shutil
import zipfile
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
from .skill_source import CanonicalSkillBatch
from .store import (
    CAPABILITY_OPERATION_PHASES,
    CapabilityOperationRecord,
    CapabilityPublishIntent,
    CapabilityPublishIntentMember,
    CapabilitySkillInstallHandoff,
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStore,
    CapabilityStoreConflict,
    CapabilityVersionRecord,
)


@dataclass(frozen=True, slots=True)
class CapabilityRehydrateFailure:
    """An installed pack that could not be rebuilt at startup and was skipped."""

    record: Any
    code: str
    message: str


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


@dataclass(frozen=True, slots=True)
class CapabilityBatchInstallResult:
    operation: CapabilityOperationRecord
    bindings: tuple[CapabilityBinding, ...]
    install_root: Path
    registry_revision: int
    manager_receipt_hash: str
    committed_set_stamp: str


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

    #: installed packs skipped at the last rehydrate, by name (shown as unavailable)
    rehydrate_failures: tuple[CapabilityRehydrateFailure, ...] = ()

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
            if latest.phase in {
                "publish_intent", "catalog_swapped", "bound",
                "batch_publish_intent", "batch_files_materialized",
                "batch_catalog_swapped",
            }
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

    async def publish_skill_install_batch(
        self, *, intent: CapabilitySkillInstallIntent,
        handoff: CapabilitySkillInstallHandoff, staging_root: Path,
        members: Sequence[CapabilitySkillInstallMember],
    ) -> Mapping[str, JsonValue]:
        if handoff.intent_id != intent.intent_id or handoff.member_set_stamp != intent.member_set_stamp:
            raise CapabilityManagerError("batch_handoff_mismatch", "handoff differs from install intent")
        if tuple(member.ordinal for member in members) != tuple(range(len(members))):
            raise CapabilityManagerError("batch_member_order_invalid", "members are not densely ordered")
        root = staging_root.resolve(strict=True)
        packs = []
        selected = []
        for member in members:
            raw_ref = member.member.get("archive_ref")
            raw_validated = member.member.get("validated_ref")
            if not isinstance(raw_ref, str) or not isinstance(raw_validated, Mapping):
                raise CapabilityManagerError("batch_archive_metadata_missing", member.normalized_name)
            archive_path = (root / raw_ref).resolve(strict=True)
            try:
                archive_path.relative_to(root)
            except ValueError as exc:
                raise CapabilityManagerError("batch_archive_ref_unsafe", raw_ref) from exc
            archive_bytes = await asyncio.to_thread(archive_path.read_bytes)
            validated_ref = ValidatedCapabilityPackageRefV1.issue(
                source_kind=str(raw_validated["source_kind"]), limits=self.package_validator.limits,
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
                self.package_validator.validate_zip_archive, archive_bytes,
                source_kind=validated_ref.source_kind,
            )
            if actual_ref != validated_ref:
                raise CapabilityManagerError("batch_validated_ref_mismatch", member.normalized_name)
            with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as package:
                manifest_payload = json.loads(package.read("deskpet-pack.json"))
            provided_tools = tuple(sorted(
                str(tool["provider_name"])
                for tool in manifest_payload.get("entries", {}).get("tools", [])
            ))
            actual_allowed_tools = tuple(sorted(
                str(tool_name)
                for skill in manifest_payload.get("entries", {}).get("skills", [])
                for tool_name in skill.get("allowed_tools", ())
            ))
            raw_allowed = member.member.get("allowed_tools")
            if raw_allowed is not None and tuple(sorted(str(item) for item in raw_allowed)) != actual_allowed_tools:
                raise CapabilityManagerError("batch_allowed_tools_mismatch", member.normalized_name)
            if len(provided_tools) != len(set(provided_tools)):
                raise CapabilityManagerError("batch_provided_tools_invalid", member.normalized_name)
            selected_ref = str(member.member.get("selected_subdirectory") or member.normalized_name)
            selected.append(selected_ref)
            from .skill_source import CanonicalSkillPack
            packs.append(CanonicalSkillPack(
                member.normalized_name, selected_ref, archive_bytes, expected_archive_hash,
                member.manifest_hash, member.content_hash, validated_ref,
            ))
        from .skill_source import ResolvedSkillSourceEvidence
        source = dict(intent.source)
        evidence = ResolvedSkillSourceEvidence.issue(
            normalized_url=str(source.get("normalized_url") or source.get("url") or "https://github.com/unknown/unknown"),
            requested_ref=str(source.get("requested_ref") or "main"), exact_commit=intent.exact_commit,
            archive_hash=intent.archive_hash, raw_file_set_digest=intent.raw_tree_hash,
            selected_subdirectories=selected,
        )
        result = await self._publish_canonical_skill_install_batch(
            CanonicalSkillBatch(evidence, tuple(packs), intent.member_set_stamp),
            operation_id=handoff.operation_id, project_scope_key=intent.install_scope_key,
            # The frozen Run catalog and durable binding must share the same
            # opaque user owner.  principal_id authenticates the confirmation;
            # it is never a catalog/binding namespace.
            owner_key=(
                intent.install_scope_key
                if intent.install_scope == "user"
                else intent.principal_id
            ),
            committed_set_stamp=intent.member_set_stamp,
        )
        committed = await self.store.get_phase_evidence(
            handoff.operation_id, "batch_committed"
        )
        if committed is None:
            raise CapabilityManagerError(
                "batch_manager_receipt_missing", handoff.operation_id
            )
        return {
            "operation_id": result.operation.operation_id,
            "manager_receipt_hash": result.manager_receipt_hash,
            "committed_set_stamp": result.committed_set_stamp,
            "registry_revision": result.registry_revision,
            "binding_ids": [binding.binding_id for binding in result.bindings],
            "owner_key": str(committed.get("owner_key") or ""),
            "members": list(committed.get("members") or ()),
            "publication_state": (
                "pending_invisible"
                if intent.install_scope == "user"
                else "active"
            ),
        }

    async def activate_skill_install_batch(
        self, *, intent: CapabilitySkillInstallIntent,
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
            raise CapabilityManagerError("batch_activation_receipt_invalid", "publication is not pending")
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
                    "user", intent.install_scope_key, member.pack_id,
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
                                receipt_members[member.pack_id].get(
                                    "expected_binding_generation"
                                ) or 0
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
                            self.store.now(), self.store.now(),
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
                            raise CapabilityManagerError("batch_activation_receipt_incomplete", member.pack_id)
                        expected_generation = int(receipt_member.get("expected_binding_generation") or 0)
                        binding = await tx.set_binding(
                            scope="user", scope_key=intent.install_scope_key,
                            pack_id=member.pack_id, version=member.version,
                            manifest_hash=member.manifest_hash,
                            expected_generation=expected_generation, enabled=True,
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
                            self.store.now(), operation_id,
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
        if operation.requested_scope != expected_scope or operation.requested_scope_key != project_scope_key:
            raise CapabilityManagerError("batch_owner_scope_mismatch", "batch operation belongs to another owner scope")
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
                operation, tuple(bindings), Path(str(receipt_evidence["install_root"])),
                int(receipt_evidence["registry_revision"]),
                str(receipt_evidence["manager_receipt_hash"]), committed_set_stamp,
            )
        if operation.status != "running" or operation.phase != "batch_staged":
            raise CapabilityManagerError("batch_reconciliation_required", f"batch is {operation.status}/{operation.phase}")

        durable_members = await self.store.operation_members(operation_id)
        if len(durable_members) != len(batch.packs):
            raise CapabilityManagerError("batch_member_count_mismatch", "confirmed member count changed")
        staging_root = self.layout.staging_path(operation_id)
        if staging_root.exists():
            await self._remove_exact_tree(staging_root)
        staging_root.mkdir(parents=True)
        validations: list[PackValidationResult] = []
        candidates: list[CapabilityCandidate] = []
        try:
            for ordinal, (frozen, durable) in enumerate(zip(batch.packs, durable_members, strict=True)):
                if frozen.skill_name != durable.normalized_name or frozen.manifest_hash != durable.manifest_hash or frozen.content_digest != durable.content_hash:
                    raise CapabilityManagerError("batch_member_identity_mismatch", f"member {ordinal} differs from confirmation")
                member_root = staging_root / f"{ordinal:04d}-{durable.normalized_name}"
                issued = await asyncio.to_thread(
                    self.package_validator.materialize_zip, frozen.archive_bytes, member_root,
                    source_kind=frozen.validated_ref.source_kind,
                )
                if issued != frozen.validated_ref:
                    raise CapabilityManagerError("batch_validated_ref_mismatch", durable.normalized_name)
                validation = load_and_validate_pack(member_root, environment=self.environment)
                if validation.manifest.id != durable.pack_id or validation.manifest.version != durable.version or validation.manifest.manifest_hash != durable.manifest_hash:
                    raise CapabilityManagerError("batch_manifest_identity_mismatch", durable.normalized_name)
                candidate = await self.publisher.prepare_candidate(
                    validation, install_root=member_root, operation_id=operation_id
                )
                if len(candidate.tool_spec_fingerprints) != len(validation.manifest.tools):
                    raise CapabilityManagerError("candidate_tool_mismatch", durable.normalized_name)
                validations.append(validation)
                candidates.append(candidate)
                await self._fault(f"after:batch_member_prepared:{ordinal}")
            for validation in validations:
                existing_version = await self.store.get_version(
                    validation.manifest.id,
                    validation.manifest.version,
                )
                if (
                    existing_version is not None
                    and (
                        existing_version.descriptor.manifest_hash
                        != validation.manifest.manifest_hash
                        or existing_version.descriptor.fingerprint
                        != validation.descriptor.fingerprint
                    )
                ):
                    raise CapabilityStoreConflict(
                        "capability_version_conflict",
                        "same capability version already exists with different immutable data",
                    )
            operation = await self._commit_phase(
                operation_id, "batch_prepared",
                {"member_count": len(durable_members), "committed_set_stamp": committed_set_stamp},
            )
            await self._fault("after:batch_prepared")
        except Exception as exc:
            await self._record_operation_failure(operation_id, exc)
            raise

        expected_revisions = {candidate.expected_registry_revision for candidate in candidates}
        if len(expected_revisions) != 1:
            raise CapabilityManagerError("batch_registry_revision_mismatch", "members were prepared against different registry revisions")
        runtime_payload: object | None = None
        if all(isinstance(candidate.runtime_payload, tuple) for candidate in candidates):
            runtime_payload = tuple(item for candidate in candidates for item in candidate.runtime_payload)  # type: ignore[union-attr]
        combined = CapabilityCandidate(
            expected_registry_revision=next(iter(expected_revisions), 0),
            tool_spec_fingerprints=tuple(fp for candidate in candidates for fp in candidate.tool_spec_fingerprints),
            old_specs=tuple(spec for candidate in candidates for spec in candidate.old_specs),
            new_specs=tuple(spec for candidate in candidates for spec in candidate.new_specs),
            publisher_state={"schema": "capability-batch-publisher-state-v1", "member_count": len(candidates)},
            runtime_payload=runtime_payload,
        )
        batch_root = (self.layout.packs / "batches" / committed_set_stamp).resolve(strict=False)
        generations = dict(expected_binding_generations or {})
        binding_scope = "user" if project_scope_key.startswith("user:v2:") else "project"
        async with self.publish_lock:
            old_bindings: list[CapabilityBinding | None] = []
            for member in durable_members:
                binding = await self.store.get_binding(binding_scope, project_scope_key, member.pack_id, owner_key=owner_key)
                actual = 0 if binding is None else binding.generation
                if generations.get(member.pack_id, actual) != actual:
                    raise CapabilityStoreConflict("binding_generation_conflict", member.pack_id)
                generations[member.pack_id] = actual
                old_bindings.append(binding)
            intent_id = _intent_id(operation_id)
            envelope: Mapping[str, JsonValue] = {
                "schema": (
                    "capability-batch-binding-v2"
                    if binding_scope == "user" else "capability-batch-binding-v1"
                ),
                "project_scope_key": project_scope_key,
                "scope": binding_scope, "scope_key": project_scope_key,
                "owner_key": owner_key, "member_set_digest": committed_set_stamp,
                "expected_binding_generations": generations,
            }
            async with self.store.write_transaction() as db:
                tx = self.store.bind(db)
                await tx.create_publish_intent(
                    intent_id=intent_id, operation_id=operation_id,
                    expected_registry_revision=combined.expected_registry_revision,
                    old_specs=combined.old_specs, new_specs=combined.new_specs,
                    old_binding=None, new_binding=envelope,
                )
                await tx.put_publish_intent_members_in_transaction(intent_id, tuple(
                    CapabilityPublishIntentMember(
                        intent_id, operation_id, member.ordinal,
                        None if old is None else old.to_dict(),
                        {"scope": binding_scope, "scope_key": project_scope_key, "owner_key": owner_key,
                         "capability_id": member.pack_id, "version": member.version,
                         "manifest_hash": member.manifest_hash, "active": True,
                         "expected_generation": generations[member.pack_id]},
                    ) for member, old in zip(durable_members, old_bindings, strict=True)
                ))
                await tx.commit_phase(operation_id, "batch_publish_intent", evidence={"intent_id": intent_id})
            await self._fault("after:batch_publish_intent")
            batch_root.parent.mkdir(parents=True, exist_ok=True)
            if batch_root.exists():
                await self._remove_batch_root(batch_root)
            await asyncio.to_thread(os.replace, staging_root, batch_root)
            async with self.store.write_transaction() as db:
                tx = self.store.bind(db)
                await tx.advance_publish_intent(intent_id, phase="batch_files_materialized")
                await tx.commit_phase(operation_id, "batch_files_materialized", evidence={"batch_root": str(batch_root)})
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
                    raise CapabilityManagerError("publisher_fingerprint_mismatch", "batch ToolSpecs differ")
            async with self.store.write_transaction() as db:
                tx = self.store.bind(db)
                await tx.advance_publish_intent(intent_id, phase="batch_catalog_swapped")
                await tx.commit_phase(operation_id, "batch_catalog_swapped", evidence={
                    "registry_revision": publication.registry_revision,
                    "publication_state": (
                        "deferred_pending_verification"
                        if binding_scope == "user" else "active"
                    ),
                })
            await self._fault("after:batch_catalog_swapped")
            bindings: list[CapabilityBinding] = []
            async with self.store.write_transaction() as db:
                tx = self.store.bind(db)
                for durable, validation, candidate in zip(durable_members, validations, candidates, strict=True):
                    install_path = batch_root / f"{durable.ordinal:04d}-{durable.normalized_name}"
                    await tx.record_version(CapabilityVersionRecord(
                        validation.descriptor, install_path, "healthy", candidate.tool_spec_fingerprints,
                        None, None, None, self.store.now(),
                    ))
                    if binding_scope == "project":
                        binding = await tx.set_binding(
                            scope=binding_scope, scope_key=project_scope_key, pack_id=durable.pack_id,
                            version=durable.version, manifest_hash=durable.manifest_hash,
                            expected_generation=generations[durable.pack_id], enabled=True,
                            owner_key=owner_key, management_policy="user_managed",
                        )
                        bindings.append(binding)
                    await db.execute(
                        """UPDATE capability_operation_members SET committed_version=?,
                           committed_manifest_hash=?,committed_set_stamp=?
                           WHERE operation_id=? AND ordinal=?""",
                        (durable.version,durable.manifest_hash,committed_set_stamp,operation_id,durable.ordinal),
                    )
                receipt_payload = {
                    "schema": (
                        "capability-batch-manager-receipt-v2"
                        if binding_scope == "user"
                        else "capability-batch-manager-receipt-v1"
                    ),
                    "operation_id": operation_id,
                    "project_scope_key": project_scope_key,
                    "scope": binding_scope, "scope_key": project_scope_key,
                    "owner_key": owner_key,
                    "committed_set_stamp": committed_set_stamp, "registry_revision": publication.registry_revision,
                    "publication_state": (
                        "pending_invisible" if binding_scope == "user" else "active"
                    ),
                    "members": [
                        {"pack_id": m.pack_id, "version": m.version,
                         "manifest_hash": m.manifest_hash, "content_hash": m.content_hash,
                         "expected_binding_generation": generations[m.pack_id],
                         **(
                             {"binding_generation": b.generation}
                             if binding_scope == "project" else {}
                         )}
                        for m, b in zip(
                            durable_members,
                            bindings if binding_scope == "project" else [None] * len(durable_members),
                            strict=True,
                        )
                    ],
                }
                receipt_hash = fingerprint_json(receipt_payload)
                await tx.advance_publish_intent(intent_id, phase="batch_committed", status="committed")
                operation = await tx.commit_phase(operation_id, "batch_committed", evidence={
                    **receipt_payload, "manager_receipt_hash": receipt_hash, "install_root": str(batch_root)
                })
            await self._fault("after:batch_committed")
        return CapabilityBatchInstallResult(operation, tuple(bindings), batch_root,
                                            publication.registry_revision, receipt_hash, committed_set_stamp)

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
            publish_intent = await self.store.get_publish_intent_for_operation(
                operation_id
            )
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
                        scope="user", scope_key=owner_key,
                        pack_id=str(raw["pack_id"]), version=str(raw["version"]),
                        manifest_hash=str(raw["manifest_hash"]),
                        expected_generation=int(
                            raw.get("expected_binding_generation") or 0
                        ),
                        enabled=True, owner_key=owner_key,
                        management_policy="user_managed",
                    )
                    bindings.append(binding)
                settled = {
                    **evidence,
                    "registry_revision": int(
                        reconciliation.registry_revision or 0
                    ),
                    "binding_ids": [binding.binding_id for binding in bindings],
                    "recovered": True,
                }
                await db.execute(
                    """UPDATE capability_operation_phase_evidence
                       SET status='committed',evidence_json=?,updated_at=?
                       WHERE operation_id=? AND phase='global_activation'
                         AND status='intent'""",
                    (
                        json.dumps(settled, sort_keys=True), self.store.now(),
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
                operation.operation_id, status="cancelled",
                error={"code": "recovered_pre_publish", "phase": operation.phase},
            )
        intent = await self.store.get_publish_intent_for_operation(operation.operation_id)
        if intent is None:
            return await self.store.fail_operation(
                operation.operation_id, status="unknown",
                error={"code": "batch_publish_intent_missing", "phase": operation.phase},
            )
        try:
            reconciliation = await self.publisher.reconcile(intent)
        except Exception as exc:
            return await self.store.fail_operation(
                operation.operation_id, status="unknown",
                error={"code": str(getattr(exc, "code", "batch_reconcile_failed")), "phase": operation.phase},
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
                operation.operation_id, status="failed",
                error={"code": "batch_publish_rolled_back", "phase": operation.phase},
            )
        if reconciliation.status != "published":
            return await self.store.fail_operation(
                operation.operation_id, status="unknown",
                error={"code": "batch_publish_outcome_unknown", "phase": operation.phase},
            )
        # A full-new registry with an absent or partial content root cannot be
        # made authoritative; fence it rather than guessing member state.
        members = await self.store.operation_members(operation.operation_id)
        roots = tuple(batch_root / f"{m.ordinal:04d}-{m.normalized_name}" for m in members)
        if not batch_root.is_dir() or not all(root.is_dir() for root in roots):
            return await self.store.fail_operation(
                operation.operation_id, status="unknown",
                error={"code": "batch_materialization_mixed", "phase": operation.phase},
            )
        publish_members = await self.store.publish_intent_members(intent.intent_id)
        if len(publish_members) != len(members):
            return await self.store.fail_operation(
                operation.operation_id, status="unknown",
                error={"code": "batch_publish_members_mixed", "phase": operation.phase},
            )
        validations = [load_and_validate_pack(root, environment=self.environment) for root in roots]
        candidates = [await self.publisher.prepare_candidate(v, install_root=root, operation_id=operation.operation_id)
                      for v, root in zip(validations, roots, strict=True)]
        for validation in validations:
            existing_version = await self.store.get_version(
                validation.manifest.id,
                validation.manifest.version,
            )
            if (
                existing_version is not None
                and (
                    existing_version.descriptor.manifest_hash
                    != validation.manifest.manifest_hash
                    or existing_version.descriptor.fingerprint
                    != validation.descriptor.fingerprint
                )
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
                await tx.record_version(CapabilityVersionRecord(
                    validation.descriptor, root, "healthy", candidate.tool_spec_fingerprints,
                    None, None, None, self.store.now(),
                ))
                binding = await tx.set_binding(
                    scope="project", scope_key=operation.requested_scope_key or "",
                    pack_id=member.pack_id, version=member.version,
                    manifest_hash=member.manifest_hash, expected_generation=expected,
                    owner_key=str(published.new_binding.get("owner_key") or ""),
                    management_policy="user_managed",
                )
                bindings.append(binding)
                await db.execute(
                    """UPDATE capability_operation_members SET committed_version=?,
                       committed_manifest_hash=?,committed_set_stamp=?
                       WHERE operation_id=? AND ordinal=?""",
                    (member.version,member.manifest_hash,stamp,operation.operation_id,member.ordinal),
                )
            registry_revision = int(reconciliation.registry_revision or intent.expected_registry_revision)
            receipt_payload = {
                "schema": "capability-batch-manager-receipt-v1", "operation_id": operation.operation_id,
                "project_scope_key": operation.requested_scope_key or "",
                "owner_key": str(publish_members[0].new_binding.get("owner_key") or ""),
                "committed_set_stamp": stamp,
                "registry_revision": registry_revision,
                "members": [{"pack_id": m.pack_id,"version": m.version,"manifest_hash": m.manifest_hash,
                             "content_hash": m.content_hash,"binding_generation": b.generation}
                            for m,b in zip(members,bindings,strict=True)],
            }
            receipt_hash = fingerprint_json(receipt_payload)
            if intent.phase == "batch_files_materialized":
                await tx.advance_publish_intent(intent.intent_id, phase="batch_catalog_swapped")
                await tx.commit_phase(operation.operation_id, "batch_catalog_swapped",
                                      evidence={"registry_revision": registry_revision, "recovered": True})
            await tx.advance_publish_intent(intent.intent_id, phase="batch_committed", status="committed")
            return await tx.commit_phase(operation.operation_id, "batch_committed", evidence={
                **receipt_payload, "manager_receipt_hash": receipt_hash,
                "install_root": str(batch_root), "recovered": True,
            })

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
                    failures.append(CapabilityRehydrateFailure(record=record, code=code, message=str(error)[:300]))
                    logging.getLogger(__name__).error(
                        "capability_rehydrate_skipped pack=%s version=%s code=%s error=%s",
                        record.descriptor.capability_id, record.descriptor.version, code, str(error)[:300],
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
        return await self.publisher.publish(
            candidate,
            operation_id=operation_id,
        )


__all__ = [
    "CapabilityRehydrateFailure",
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
