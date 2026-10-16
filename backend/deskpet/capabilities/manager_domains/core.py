# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Construction, phases, validated refs and operation failures."""

from __future__ import annotations

import asyncio
import inspect
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from ..contracts import CapabilityBinding, CapabilityScopeKind, JsonValue
from ..manifest import PackEnvironment, PackValidationResult, windows_extended_path
from ..package_limits import (
    CapabilityPackageValidationError,
    CapabilityPackageValidator,
    ValidatedCapabilityPackageRefV1,
)
from ..skill_source import CanonicalSkillBatch
from ..source import CapabilitySourceResolver, PackSourceRequest, StagedPack
from ..store import (
    CapabilityOperationRecord,
    CapabilityPublishIntent,
    CapabilityStore,
    CapabilityVersionRecord,
)
from .support import (
    CapabilityBatchInstallResult,
    CapabilityCandidate,
    CapabilityManagerError,
    CapabilityPublication,
    CapabilityRehydrateFailure,
)

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

    def environment_path(self, pack_id: str, version: str, manifest_hash: str) -> Path:
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
    ) -> CapabilityCandidate: ...

    async def prepare_installed(
        self,
        record: CapabilityVersionRecord,
        *,
        operation_id: str,
    ) -> CapabilityCandidate: ...

    async def prepare_uninstall(
        self,
        record: CapabilityVersionRecord,
        binding: CapabilityBinding,
        *,
        operation_id: str,
    ) -> CapabilityCandidate: ...

    async def publish(
        self,
        candidate: CapabilityCandidate,
        *,
        operation_id: str,
    ) -> CapabilityPublication: ...

    async def reconcile(self, intent: CapabilityPublishIntent) -> PublishReconciliation: ...

    async def stop_binding(self, binding: CapabilityBinding) -> None: ...


class CapabilityEnvironmentPreparer(Protocol):
    async def prepare(
        self,
        validation: PackValidationResult,
        *,
        environment_root: Path,
        operation_id: str,
    ) -> Mapping[str, JsonValue]: ...

    async def probe(
        self,
        record: CapabilityOperationRecord,
        *,
        environment_root: Path | None,
    ) -> Mapping[str, JsonValue]: ...


class _AsyncLock(Protocol):
    async def __aenter__(self) -> Any: ...

    async def __aexit__(self, *args: Any) -> None: ...


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

    async def reconcile(self, intent: CapabilityPublishIntent) -> PublishReconciliation:
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


class CapabilityPackManagerCore:
    """Construction, phases, validated refs and operation failures."""

    if TYPE_CHECKING:

        @staticmethod
        def _binding_from_intent_payload(
            payload: Mapping[str, JsonValue] | None,
        ) -> CapabilityBinding | None: ...
        async def _cancel_operation(self, operation_id: str) -> None: ...
        async def _manager_receipt_evidence(
            self,
            *,
            operation_id: str,
            action: str,
            binding: CapabilityBinding,
            registry_revision: int,
            tool_spec_fingerprints: Sequence[str],
        ) -> Mapping[str, JsonValue]: ...
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
            owner_key: str | None = ...,
            management_policy: str | None = ...,
        ) -> tuple[CapabilityBinding, CapabilityPublication]: ...
        async def _publish_and_disable(
            self, *, operation_id: str, candidate: CapabilityCandidate, binding: CapabilityBinding
        ) -> tuple[CapabilityBinding, CapabilityPublication]: ...
        async def _publish_canonical_skill_install_batch(
            self,
            batch: CanonicalSkillBatch,
            *,
            operation_id: str,
            project_scope_key: str,
            owner_key: str,
            committed_set_stamp: str,
            expected_binding_generations: Mapping[str, int] | None = ...,
        ) -> CapabilityBatchInstallResult: ...
        async def _remove_batch_root(self, target: Path) -> None: ...

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
        self.layout = CapabilityLayout(Path(user_data_root) / "capabilities")
        self.package_validator = package_validator or CapabilityPackageValidator()
        self.source_resolver = source_resolver or CapabilitySourceResolver(
            package_validator=self.package_validator
        )
        self.publisher = publisher or FoundationPublisher()
        self.environment_preparer = environment_preparer or FoundationEnvironmentPreparer()
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
        operation = await self.store.commit_phase(operation_id, phase, evidence=evidence)
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
        return {name: getattr(validated_ref, name) for name in _VALIDATED_REF_FIELDS}

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
        if any(raw.get(name) != getattr(validated_ref, name) for name in _VALIDATED_REF_FIELDS):
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

    async def _record_operation_failure(self, operation_id: str, exc: Exception) -> None:
        """Settle an ordinary lifecycle error without masking hard crashes."""

        latest = await self.store.get_operation(operation_id)
        if latest is None or latest.status != "running":
            return
        status = (
            "unknown"
            if latest.phase
            in {
                "publish_intent",
                "catalog_swapped",
                "bound",
                "batch_publish_intent",
                "batch_files_materialized",
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
