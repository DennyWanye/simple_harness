# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Production composition for local capability packs.

This module owns wiring only.  Pack lifecycle remains in
``CapabilityPackManager``, publication remains in
``ToolRegistryCapabilityPublisher``, and worker processes remain owned by
``LocalToolRuntime``.  In particular, a brokered worker can return a validated
effect plan but can never execute that plan through this adapter.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import hashlib
import inspect
import json
import os
import re
import signal
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from deskpet.companion.authority import (
    RevocationBarrier,
    get_process_revocation_barrier,
)
from deskpet.companion.identity_gate import (
    CompanionIdentityNotReady,
    IdentityReadyGate,
)
from deskpet.execution.ports import CurrentExecutionScopeLease
from deskpet.types.task_grants import ResourceSelector
from deskpet.tools.capabilities import PreparedToolSet, ToolExecutionContext
from deskpet.tools.build_identity import ExecutionBuildIdentity
from deskpet.tools.registry import ToolRegistry, ToolSpec, tool_spec_fingerprint
from deskpet.workflows.contracts import EffectKind, EffectPolicy
from deskpet.workflows.effects import NormalizedToolOutcome

from .brokered_planner import BrokeredEffectPlanner, ResourceAuthorizer
from .catalog_gate import CapabilityCatalogGate, CatalogGateKey
from .contracts import (
    CapabilityBinding,
    CapabilityCatalogEntry,
    CapabilityScope,
    CapabilityVersionDescriptor,
    EMPTY_OWNER_BINDING_SET_STAMP,
    JsonValue,
    OwnerBindingSetStamp,
    OwnerScopeKey,
    PlatformDetailSnapshot,
    PlatformDetailTokenVector,
    ProcessCatalogStamp,
    RevisionedCatalogEntries,
    RunCatalogContentStamp,
    RunCatalogEntryIdentity,
    fingerprint_json,
)
from .hub import (
    CapabilityHub,
    SkillLoaderCatalogSource,
    StaticCapabilityEntrySource,
    StaticCatalogRevisionSource,
    ToolRegistryCatalogSource,
)
from .input_views import InputViewRequest, InputViewResolver
from .local_runtime import (
    LocalRuntimeRequest,
    LocalRuntimeResult,
    LocalToolRuntime,
    ProcessCleanupReport,
)
from .manager import (
    CapabilityInstallResult,
    CapabilityManagerError,
    CapabilityOperationRecord,
    CapabilityPackManager,
)
from .manifest import (
    CommandDependency,
    PackEnvironment,
    PackCompatibilityError,
    PackManifestError,
    PackValidationResult,
    ToolEntry,
    load_and_validate_pack,
)
from .publisher import (
    ToolRegistryCapabilityPublisher,
    capability_registry_source,
    capability_spec_version,
)
from .search import CapabilitySearch, SemanticEmbedder
from .source import CapabilitySourceResolver, PackSourceRequest
from .configured_catalog import (
    CompositeCapabilityEntrySource,
    ConfiguredCapabilityCatalogSource,
)
from .manifest import windows_extended_path
from .runtime_prepare import (
    CapabilityRuntimeSetCoordinator,
    PreparedRuntimeAdapter,
    PreparedRuntimeInstanceSpec,
    PreparedRuntimeSet,
    RuntimeLaunchAuthorization,
    RuntimeSetLedgerPort,
)
from .run_catalog import (
    PendingProcessPinToken,
    PreparedRunCatalogLease,
    SnapshotLeaseReadyGate,
)
from .store import CapabilityStore
from .tool_proxy import BrokeredPlanEnvelope, LocalToolDefinition, LocalToolProxy
from .tools import CapabilityToolService, register_capability_tools

BrokeredAuthorityResolver = Callable[
    [ToolExecutionContext],
    "BrokeredInvocationAuthority | Awaitable[BrokeredInvocationAuthority]",
]
CommandFinder = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class CurrentExecutionScopeFacts:
    owner_key: str
    profile_generation: int
    binding_epoch: int
    capability_hash: str
    scope_hash: str
    owner_binding_set_stamp: str
    scope: str
    scope_key: str
    pack_ids: tuple[str, ...] = ()


class ExecutionScopeAuthorityPort(Protocol):
    async def resolve_current_execution_scope(
        self,
        *,
        run_id: str,
        owner_key: str,
        profile_generation: int,
        binding_epoch: int,
    ) -> CurrentExecutionScopeFacts: ...


class SqliteExecutionScopeAuthority:
    """Resolve one Run's immutable owner scope from durable start facts."""

    def __init__(self, store: CapabilityStore) -> None:
        self._store = store

    async def resolve_current_execution_scope(
        self,
        *,
        run_id: str,
        owner_key: str,
        profile_generation: int,
        binding_epoch: int,
    ) -> CurrentExecutionScopeFacts:
        if not run_id:
            raise RuntimeError("execution_scope_run_id_missing")
        async with self._store.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT s.run_context_json,
                              i.run_catalog_content_stamp,
                              c.request_scope_canonical_json
                       FROM execution_run_start_snapshots AS s
                       JOIN capability_snapshot_lease_intents AS i
                         ON i.run_id=s.run_id AND i.status='bound'
                       JOIN capability_run_catalog_snapshots AS c
                         ON c.run_catalog_content_stamp=
                            i.run_catalog_content_stamp
                       WHERE s.run_id=?
                       LIMIT 1""",
                    (run_id,),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("execution_scope_durable_facts_missing")
            entries = await (
                await db.execute(
                    """SELECT pack_id
                       FROM capability_run_catalog_snapshot_entries
                       WHERE run_catalog_content_stamp=?
                         AND entry_kind='pack'
                       ORDER BY ordinal""",
                    (str(row["run_catalog_content_stamp"]),),
                )
            ).fetchall()
        context = json.loads(str(row["run_context_json"]))
        workspace = dict(context.get("workspace") or {})
        scope_payload = json.loads(
            str(row["request_scope_canonical_json"])
        )
        if (
            str(context.get("owner_key") or "") != owner_key
            or int(context.get("profile_generation") or 0)
            != profile_generation
            or int(context.get("binding_epoch") or 0) != binding_epoch
        ):
            raise RuntimeError("execution_scope_start_identity_mismatch")
        capability_hash = str(context.get("capability_hash") or "")
        scope_hash = str(workspace.get("scope_hash") or "")
        if not capability_hash or not scope_hash:
            raise RuntimeError("execution_scope_start_fingerprint_missing")
        return CurrentExecutionScopeFacts(
            owner_key=owner_key,
            profile_generation=profile_generation,
            binding_epoch=binding_epoch,
            capability_hash=capability_hash,
            scope_hash=scope_hash,
            owner_binding_set_stamp=str(
                row["run_catalog_content_stamp"]
            ),
            scope="user",
            scope_key=str(scope_payload.get("user_key") or ""),
            pack_ids=tuple(
                str(item["pack_id"])
                for item in entries
                if item["pack_id"] is not None
            ),
        )


class OwnerRuntimeProjectionCommitPort(Protocol):
    async def activate_owner_runtime_projection(
        self, prepared_set: PreparedRuntimeSet
    ) -> Mapping[str, Any]: ...


class PreparedRunCatalogLeaseV1(Protocol):
    snapshot_ref: str
    run_catalog_content_stamp: str
    process_catalog_stamp: str
    lease_intent_id: str
    lease_intent_hash: str
    prepared_tool_set_fingerprint: str
    prepared_tool_set_capture_hash: str
    prepared_tool_set_envelope: Mapping[str, Any]
    lease_entries: tuple[Mapping[str, Any], ...]
    projection_receipt_hash: str


class RunCatalogLeasePreparationPort(Protocol):
    """Store transaction + process-pin seam required by the Task 7 Store slice."""

    async def prepare_captured_run_catalog(
        self,
        *,
        snapshot: Any,
        selected_entries: Sequence[Any],
        owner_key: str,
        prepared_tool_set: PreparedToolSet,
        prepared_tool_set_fingerprint: str,
        ready_gate: SnapshotLeaseReadyGate,
        run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        owner_operation_id: str,
    ) -> PreparedRunCatalogLeaseV1: ...


class _PlatformExecutionScopeLease:
    def __init__(
        self,
        facts: CurrentExecutionScopeFacts,
        *,
        gate_context: Any,
        publish_lock: asyncio.Lock,
        barrier_context: Any,
    ) -> None:
        self.owner_key = facts.owner_key
        self.profile_generation = facts.profile_generation
        self.binding_epoch = facts.binding_epoch
        self.capability_hash = facts.capability_hash
        self.scope_hash = facts.scope_hash
        self.owner_binding_set_stamp = facts.owner_binding_set_stamp
        self._gate_context = gate_context
        self._publish_lock = publish_lock
        self._barrier_context = barrier_context
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._gate_context.__aexit__(None, None, None)
        self._publish_lock.release()
        await self._barrier_context.__aexit__(None, None, None)

_CONTROL_TOOL_NAMES = frozenset(
    {
        "capability_list",
        "capability_search",
        "capability_catalog_refresh",
        "capability_install",
        "capability_update",
        "capability_uninstall",
        "capability_rollback",
    }
)
_STRICT_SCHEMA_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "dependentRequired",
        "dependentSchemas",
        "propertyNames",
        "minProperties",
        "maxProperties",
        "patternProperties",
    }
)


@dataclass(frozen=True, slots=True)
class BrokeredInvocationAuthority:
    """Trusted host facts required before accepting a worker effect plan."""

    task_grant_id: str
    catalog_stamp: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not self.task_grant_id.strip():
            raise ValueError("task_grant_id is required")
        object.__setattr__(self, "catalog_stamp", dict(self.catalog_stamp))


def _strict_parameters(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CapabilityManagerError(
            "tool_schema_unreadable", f"cannot read strict tool schema {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise CapabilityManagerError(
            "tool_schema_invalid", f"{path} must contain one JSON object"
        )
    unknown = sorted(set(value) - _STRICT_SCHEMA_KEYS)
    if unknown:
        raise CapabilityManagerError(
            "tool_schema_invalid",
            f"{path} has unsupported top-level fields: {', '.join(unknown)}",
        )
    if value.get("type") != "object":
        raise CapabilityManagerError(
            "tool_schema_invalid", f"{path} must declare type=object"
        )
    properties = value.get("properties", {})
    if not isinstance(properties, dict) or not all(
        isinstance(name, str) and isinstance(schema, dict)
        for name, schema in properties.items()
    ):
        raise CapabilityManagerError(
            "tool_schema_invalid", f"{path}.properties must map names to schemas"
        )
    required = value.get("required", [])
    if (
        not isinstance(required, list)
        or not all(isinstance(name, str) for name in required)
        or len(required) != len(set(required))
        or not set(required).issubset(properties)
    ):
        raise CapabilityManagerError(
            "tool_schema_invalid",
            f"{path}.required must contain unique declared property names",
        )
    if value.get("additionalProperties") is not False:
        raise CapabilityManagerError(
            "tool_schema_not_closed",
            f"{path} must set additionalProperties=false",
        )
    return value


def _permission_category(permissions: Sequence[str]) -> str:
    values = set(permissions)
    if values & {"package_install", "capability_manage", "skill_install"}:
        return "skill_install"
    if values & {"application_control", "desktop_control", "desktop_write"}:
        return "desktop_write"
    if values & {"process_execute", "shell"}:
        return "shell"
    if values & {"filesystem_write", "write_file"}:
        return "write_file"
    if values & {"network_access", "network"}:
        return "network"
    if "read_file_sensitive" in values:
        return "read_file_sensitive"
    if "mcp_call" in values:
        return "mcp_call"
    return "read_file"


def _effect_kind(effects: Sequence[str]) -> EffectKind:
    values = set(effects)
    if "opaque_manual" in values:
        return EffectKind.OPAQUE_MANUAL
    if "staged_file" in values:
        return EffectKind.STAGED_FILE
    if "deterministic_reusable" in values:
        return EffectKind.DETERMINISTIC_REUSABLE
    return EffectKind.IDEMPOTENT_READ


def _local_outcome_parser(raw: Any) -> NormalizedToolOutcome:
    if isinstance(raw, NormalizedToolOutcome):
        return raw
    if isinstance(raw, BrokeredPlanEnvelope):
        # Success here means only that the compute worker and strict host
        # validator produced a durable plan. ReAct consumes this private
        # marker before provider backfill and aggregates the real action
        # receipts into the parent's one visible outcome.
        return NormalizedToolOutcome.success(raw.to_durable_envelope())
    return NormalizedToolOutcome.malformed(
        "local capability handler returned an unsupported value"
    )


def _legacy_unavailable(_args: dict[str, Any], _task_id: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": {
                "code": "trusted_context_required",
                "message": "local capabilities require prepared Harness execution",
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _input_requests(
    tool: ToolEntry, args: Mapping[str, Any]
) -> tuple[InputViewRequest, ...]:
    requests: list[InputViewRequest] = []
    for name in tool.input_views:
        if name not in args:
            raise ValueError(f"declared input view is missing: {name}")
        raw = args[name]
        if isinstance(raw, Mapping):
            extra = set(raw) - {"resource", "kind", "view_kind", "opaque_ref"}
            if extra or "resource" not in raw:
                raise ValueError(
                    f"input view {name!r} must contain resource/kind/view_kind only"
                )
            resource = raw["resource"]
            kind = str(raw.get("kind") or "file")
            view_kind = str(raw.get("view_kind") or "metadata")
            opaque_ref = str(raw.get("opaque_ref") or name)
        else:
            resource = raw
            kind = "file"
            view_kind = "metadata"
            opaque_ref = name
        if kind not in {"file", "directory", "artifact", "value"}:
            raise ValueError(f"input view {name!r} has unsupported kind {kind!r}")
        if view_kind not in {"identity", "metadata", "text", "bytes"}:
            raise ValueError(
                f"input view {name!r} has unsupported view_kind {view_kind!r}"
            )
        requests.append(
            InputViewRequest(
                opaque_ref=opaque_ref,
                resource=resource,
                kind=kind,  # type: ignore[arg-type]
                view_kind=view_kind,  # type: ignore[arg-type]
            )
        )
    return tuple(requests)


def _resource_scope_resolver(
    *,
    pack_id: str,
    tool: ToolEntry,
    parameters: Mapping[str, Any],
    permissions: Sequence[str],
) -> Callable[
    [Mapping[str, Any], ToolExecutionContext],
    tuple[ResourceSelector, ...],
]:
    """Compile a pure resolver from immutable manifest/schema facts."""

    properties = frozenset(
        str(name)
        for name in (
            parameters.get("properties", {})
            if isinstance(parameters.get("properties"), Mapping)
            else {}
        )
    )
    permission_set = frozenset(permissions)
    filesystem_access = ["read"]
    if permission_set & {"filesystem_write", "write_file", "desktop_write"}:
        filesystem_access.append("write")
    process_access = ("execute",)

    def resolve(
        args: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> tuple[ResourceSelector, ...]:
        selectors: list[ResourceSelector] = []

        for name in tool.input_views:
            raw = args.get(name)
            kind = "file"
            resource = raw
            if isinstance(raw, Mapping):
                kind = str(raw.get("kind") or "file")
                resource = raw.get("resource")
            if kind in {"file", "directory", "artifact"} and isinstance(
                resource, str
            ) and resource.strip():
                selectors.append(
                    ResourceSelector.filesystem(resource, *filesystem_access)
                )

        for name in sorted(properties):
            value = args.get(name)
            if not isinstance(value, str) or not value.strip():
                continue
            lowered = name.casefold()
            if lowered == "url" or lowered.endswith("_url"):
                parsed = urlsplit(value)
                if (
                    parsed.scheme.casefold() not in {"http", "https", "ws", "wss"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    raise ValueError(f"{name} must contain a safe network origin")
                host = parsed.hostname
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                default_port = {
                    "http": 80,
                    "https": 443,
                    "ws": 80,
                    "wss": 443,
                }[parsed.scheme.casefold()]
                suffix = (
                    ""
                    if parsed.port in {None, default_port}
                    else f":{parsed.port}"
                )
                selectors.append(
                    ResourceSelector.network(
                        f"{parsed.scheme.casefold()}://{host}{suffix}",
                        "connect",
                        "read",
                    )
                )
            elif lowered == "executable" or lowered.endswith("_executable"):
                selectors.append(
                    ResourceSelector(
                        "process_executable",
                        str(Path(value).expanduser().resolve(strict=False)),
                        process_access,
                    )
                )
            elif (
                lowered in {"path", "source", "destination"}
                or lowered.endswith("_path")
            ):
                selectors.append(
                    ResourceSelector.filesystem(value, *filesystem_access)
                )

        if tool.execution_profile == "brokered-effect-v1":
            managed_root = context.write_scope_root or context.workspace
            if not managed_root:
                raise ValueError(
                    f"{tool.provider_name} requires a trusted workspace root"
                )
            selectors.append(
                ResourceSelector(
                    "capability_managed_root",
                    managed_root,
                    ("read", "write"),
                )
            )

        if permission_set & {"process_execute", "shell"} and not any(
            selector.kind == "process_executable" for selector in selectors
        ):
            selectors.append(
                ResourceSelector(
                    "system_change",
                    f"capability:{pack_id}:{tool.id}:process",
                    process_access,
                )
            )
        if not selectors:
            selectors.append(
                ResourceSelector(
                    "system_change",
                    f"capability:{pack_id}:{tool.id}",
                    ("read",),
                )
            )

        unique: dict[tuple[str, str, tuple[str, ...]], ResourceSelector] = {}
        for selector in selectors:
            unique[
                (
                    selector.kind,
                    selector.canonical_value,
                    selector.access,
                )
            ] = selector
        return tuple(unique.values())

    return resolve


class LocalCapabilityToolSpecFactory:
    """Build immutable offline ``ToolSpec`` objects for local JSON workers."""

    def __init__(
        self,
        *,
        proxy: LocalToolProxy,
        python_executable: str | Path | None = None,
        timeout_seconds: float = 30.0,
        brokered_authority_resolver: BrokeredAuthorityResolver | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.proxy = proxy
        self.python_executable = str(python_executable or sys.executable)
        self.timeout_seconds = float(timeout_seconds)
        self.brokered_authority_resolver = brokered_authority_resolver

    async def _authority(
        self, context: ToolExecutionContext
    ) -> BrokeredInvocationAuthority | None:
        resolver = self.brokered_authority_resolver
        if resolver is None:
            return None
        value = resolver(context)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, BrokeredInvocationAuthority):
            raise TypeError(
                "brokered authority resolver must return BrokeredInvocationAuthority"
            )
        return value

    def build_specs(
        self,
        validation: PackValidationResult,
        *,
        install_root: Path,
        operation_id: str,
    ) -> Sequence[ToolSpec]:
        del operation_id
        manifest = validation.manifest
        root = windows_extended_path(install_root).resolve(strict=True)
        source = capability_registry_source(
            manifest.id, manifest.version, manifest.manifest_hash
        )
        spec_version = capability_spec_version(
            manifest.version, manifest.manifest_hash
        )
        permission_category = _permission_category(manifest.permissions)
        effect_kind = _effect_kind(manifest.effects)
        dangerous = effect_kind in {
            EffectKind.STAGED_FILE,
            EffectKind.OPAQUE_MANUAL,
        } or permission_category in {
            "write_file",
            "desktop_write",
            "shell",
            "skill_install",
        }
        candidate = ToolRegistry()

        for tool in manifest.tools:
            if tool.runtime != "deskpet-json-tool-v1":
                raise CapabilityManagerError(
                    "unsupported_local_runtime",
                    f"{tool.provider_name} uses unsupported runtime {tool.runtime}",
                )
            entry_path = (root / tool.entry).resolve(strict=True)
            schema_path = (root / tool.schema).resolve(strict=True)
            try:
                entry_path.relative_to(root)
                schema_path.relative_to(root)
            except ValueError as exc:  # defense after strict manifest validation
                raise CapabilityManagerError(
                    "pack_path_escape", f"{tool.provider_name} escapes its pack root"
                ) from exc
            parameters = _strict_parameters(schema_path)
            description = str(
                parameters.get("description")
                or parameters.get("title")
                or f"{manifest.name} {tool.id}"
            )
            runtime_provenance_ref = fingerprint_json(
                {
                    "protocol": tool.runtime,
                    "execution_profile": tool.execution_profile,
                    "pack_id": manifest.id,
                    "pack_version": manifest.version,
                    "manifest_hash": manifest.manifest_hash,
                    "entry": tool.entry,
                    "entry_sha256": validation.file_hashes[tool.entry],
                    "python_runtime": {
                        "implementation": sys.implementation.name,
                        "major": sys.version_info.major,
                        "minor": sys.version_info.minor,
                    },
                }
            )
            handler_id = f"capability.{manifest.id}.{tool.id}.v1"
            build_artifacts = tuple(
                sorted(
                    (
                        (
                            "deskpet-adapter:"
                            "backend/deskpet/capabilities/platform.py",
                            hashlib.sha256(
                                Path(__file__).read_bytes()
                            ).hexdigest(),
                        ),
                        (
                            f"pack:{tool.entry}",
                            validation.file_hashes[tool.entry],
                        ),
                        (
                            f"pack:{tool.schema}",
                            validation.file_hashes[tool.schema],
                        ),
                    )
                )
            )
            sources_manifest_hash = fingerprint_json(
                {
                    "pack_id": manifest.id,
                    "pack_version": manifest.version,
                    "manifest_hash": manifest.manifest_hash,
                    "tool_id": tool.id,
                    "provider_name": tool.provider_name,
                    "runtime_provenance_ref": runtime_provenance_ref,
                }
            )
            execution_build_identity = ExecutionBuildIdentity(
                provider="deskpet-capability-pack",
                handler_id=handler_id,
                build_digest=fingerprint_json(
                    {
                        "provider": "deskpet-capability-pack",
                        "sources_manifest_hash": sources_manifest_hash,
                        "artifacts": [
                            {"path": path, "sha256": digest}
                            for path, digest in build_artifacts
                        ],
                    }
                ),
                sources_manifest_hash=sources_manifest_hash,
                artifacts=build_artifacts,
            )
            definition_box: dict[str, LocalToolDefinition] = {}

            async def context_handler(
                args: dict[str, Any],
                context: ToolExecutionContext | None,
                *,
                declared_tool: ToolEntry = tool,
                box: dict[str, LocalToolDefinition] = definition_box,
            ) -> NormalizedToolOutcome | BrokeredPlanEnvelope:
                if context is None:
                    return NormalizedToolOutcome.failure(
                        "trusted_context_required",
                        "local capabilities require trusted host context",
                    )
                try:
                    requests = _input_requests(declared_tool, args)
                except (TypeError, ValueError) as exc:
                    return NormalizedToolOutcome.failure(
                        "input_view_invalid", str(exc)
                    )
                authority: BrokeredInvocationAuthority | None = None
                if declared_tool.execution_profile == "brokered-effect-v1":
                    try:
                        authority = await self._authority(context)
                    except (TypeError, ValueError) as exc:
                        return NormalizedToolOutcome.failure(
                            "brokered_authority_invalid", str(exc)
                        )
                    if authority is None:
                        return NormalizedToolOutcome.failure(
                            "brokered_authority_unavailable",
                            "brokered tools require a TaskGrant and stamped catalog",
                        )
                workspace_roots = tuple(
                    dict.fromkeys(
                        value
                        for value in (
                            context.workspace,
                            context.write_scope_root,
                        )
                        if value
                    )
                )
                return await self.proxy.invoke(
                    box["definition"],
                    args,
                    input_requests=requests,
                    workspace_roots=workspace_roots,
                    temp_dir=str(context.write_scope_root or context.workspace or ""),
                    root_run_id=context.root_run_id,
                    run_id=context.run_id,
                    effect_id=context.effect_id,
                    parent_call_id=context.call_id,
                    provider_call_id=context.call_id,
                    task_grant_id=(
                        "" if authority is None else authority.task_grant_id
                    ),
                    catalog_stamp=(
                        {} if authority is None else authority.catalog_stamp
                    ),
                )

            policy = EffectPolicy(
                policy_id=(
                    f"capability:{manifest.id}:{tool.id}:{effect_kind.value}"
                ),
                version="v1",
                kind=effect_kind,
                max_attempts=1,
                reusable_across_branches=(
                    effect_kind is EffectKind.DETERMINISTIC_REUSABLE
                ),
            )
            resource_scope_resolver = _resource_scope_resolver(
                pack_id=manifest.id,
                tool=tool,
                parameters=parameters,
                permissions=manifest.permissions,
            )
            candidate.register(
                name=tool.provider_name,
                toolset=f"capability:{manifest.id}",
                schema={
                    "name": tool.provider_name,
                    "description": description,
                    "parameters": parameters,
                },
                handler=_legacy_unavailable,
                context_handler=context_handler,
                permission_category=permission_category,
                source=source,
                dangerous=dangerous,
                resource_scope_resolver=resource_scope_resolver,
                resource_scope_resolver_id=(
                    f"capability:{manifest.id}:{tool.id}:"
                    f"{manifest.manifest_hash}"
                ),
                resource_scope_resolver_version="deskpet-resource-scope-v1",
                timeout_seconds=self.timeout_seconds,
                concurrency_safe=not dangerous,
                spec_version=spec_version,
                permission_policy_version="capability-pack-v1",
                effect_policy=policy,
                outcome_parser=_local_outcome_parser,
                outcome_parser_id="local_capability_v1",
                outcome_parser_version="v1",
                dispatch_kind=(
                    "brokered_effect"
                    if tool.execution_profile == "brokered-effect-v1"
                    else "handler"
                ),
                runtime_provenance_ref=runtime_provenance_ref,
                stable_handler_id=handler_id,
                execution_build_identity=execution_build_identity,
            )
            spec = candidate.catalog_snapshot().specs[-1]
            definition_box["definition"] = LocalToolDefinition(
                name=tool.id,
                argv=(self.python_executable, str(entry_path)),
                execution_profile=tool.execution_profile,  # type: ignore[arg-type]
                tool_spec_fingerprint=tool_spec_fingerprint(spec),
                timeout_seconds=self.timeout_seconds,
                generated=tool.execution_profile == "brokered-effect-v1",
            )

        return candidate.catalog_snapshot().specs


def _default_command_finder(name: str) -> str | None:
    environment_name = (
        re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
        + "_EXECUTABLE"
    )
    configured = os.environ.get(environment_name)
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve(strict=True))
    candidates = [name]
    if name.casefold() == "godot":
        candidates.append("godot4")
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return str(Path(found).resolve(strict=True))
    return None


def _extract_version(value: str) -> tuple[int, ...] | None:
    match = re.search(r"(?<!\d)(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())


def _version_satisfies(actual: tuple[int, ...] | None, constraint: str) -> bool | None:
    if actual is None:
        return None
    match = re.fullmatch(r"\s*(>=|>|<=|<|==)?\s*(\d+(?:\.\d+){0,2})\s*", constraint)
    if match is None:
        return None
    expected = tuple(int(part) for part in match.group(2).split("."))
    width = max(len(actual), len(expected))
    left = actual + (0,) * (width - len(actual))
    right = expected + (0,) * (width - len(expected))
    operator = match.group(1) or "=="
    return {
        ">=": left >= right,
        ">": left > right,
        "<=": left <= right,
        "<": left < right,
        "==": left == right,
    }[operator]


class ManagedEnvironmentPreparer:
    """Probe dependencies and run declared healthchecks out of process."""

    def __init__(
        self,
        *,
        runtime: LocalToolRuntime,
        python_executable: str | Path | None = None,
        command_finder: CommandFinder | None = None,
        healthcheck_timeout_seconds: float = 15.0,
        command_probe_timeout_seconds: float = 5.0,
    ) -> None:
        if healthcheck_timeout_seconds <= 0 or command_probe_timeout_seconds <= 0:
            raise ValueError("probe timeouts must be positive")
        self.runtime = runtime
        self.python_executable = str(python_executable or sys.executable)
        self.command_finder = command_finder or _default_command_finder
        self.healthcheck_timeout_seconds = float(healthcheck_timeout_seconds)
        self.command_probe_timeout_seconds = float(command_probe_timeout_seconds)

    def materialize_static(
        self,
        validation: PackValidationResult,
        *,
        environment_root: Path,
        operation_id: str,
    ) -> Mapping[str, JsonValue]:
        """Materialize host-only state without starting or probing a runtime.

        Command lookup is captured here so the later prepared runtime set owns
        an immutable executable path.  Version probes and tool healthchecks are
        deliberately planned separately and must pass through the managed
        start-ACK protocol.
        """

        windows_extended_path(environment_root).mkdir(
            parents=True, exist_ok=True
        )
        command_dependencies: list[dict[str, JsonValue]] = []
        for dependency in validation.manifest.dependencies.commands:
            detector = self._has_detector(validation, dependency.name)
            resolved = self.command_finder(dependency.name)
            command_dependencies.append(
                {
                    "name": dependency.name,
                    "required_version": dependency.version,
                    "blocking": not detector,
                    "resolved_path": resolved,
                    "status": (
                        "planned"
                        if resolved is not None
                        else (
                            "missing_nonblocking_detector"
                            if detector
                            else "missing"
                        )
                    ),
                }
            )
            if resolved is None and not detector:
                raise CapabilityManagerError(
                    "command_dependency_unavailable",
                    f"{dependency.name} {dependency.version}: missing",
                )

        return {
            "kind": "managed-local-static-v1",
            "operation_id": operation_id,
            "path": str(environment_root.resolve(strict=False)),
            "command_dependencies": command_dependencies,
            "python_dependencies": [
                {
                    "requirement": dependency.requirement,
                    "status": "declared_host_environment",
                }
                for dependency in validation.manifest.dependencies.python
            ],
        }

    def plan_executable_checks(
        self,
        validation: PackValidationResult,
        *,
        materialization: Mapping[str, JsonValue],
        operation_id: str,
        adapter_fingerprint: str,
    ) -> tuple[PreparedRuntimeInstanceSpec, ...]:
        """Freeze dependency probes and healthchecks without executing them."""

        raw_dependencies = materialization.get("command_dependencies")
        if not isinstance(raw_dependencies, list):
            raise CapabilityManagerError(
                "invalid_static_materialization",
                "command_dependencies must be a list",
            )
        dependencies_by_name: dict[str, Mapping[str, JsonValue]] = {}
        for item in raw_dependencies:
            if not isinstance(item, Mapping):
                raise CapabilityManagerError(
                    "invalid_static_materialization",
                    "command dependency evidence must be an object",
                )
            name = str(item.get("name") or "")
            if not name or name in dependencies_by_name:
                raise CapabilityManagerError(
                    "invalid_static_materialization",
                    "command dependency names must be non-empty and unique",
                )
            dependencies_by_name[name] = item

        instances: list[PreparedRuntimeInstanceSpec] = []
        for dependency in validation.manifest.dependencies.commands:
            evidence = dependencies_by_name.get(dependency.name)
            if evidence is None:
                raise CapabilityManagerError(
                    "invalid_static_materialization",
                    f"missing command dependency evidence: {dependency.name}",
                )
            resolved_path = str(evidence.get("resolved_path") or "")
            if not resolved_path:
                # Missing non-blocking detector dependencies remain visible in
                # static evidence but cannot authorize a process launch.
                continue
            ordinal = len(instances)
            argv = [resolved_path, "--version"]
            instances.append(
                PreparedRuntimeInstanceSpec(
                    entry_id=f"dependency-probe:{dependency.name}",
                    runtime_kind="dependency_probe",
                    adapter_id="managed-process-job-v1",
                    adapter_fingerprint=adapter_fingerprint,
                    start_envelope={
                        "schema_version": 1,
                        "argv": argv,
                        "argv_hash": fingerprint_json(argv),
                        "cwd": str(validation.root.resolve(strict=True)),
                        "operation_id": operation_id,
                    },
                    health_envelope={
                        "kind": "command-version-v1",
                        "required_version": dependency.version,
                        "timeout_seconds": self.command_probe_timeout_seconds,
                        "nonblocking_detector": self._has_detector(
                            validation, dependency.name
                        ),
                    },
                    ordinal=ordinal,
                )
            )

        checked: set[tuple[str, str]] = set()
        for tool in validation.manifest.tools:
            key = (tool.entry, tool.healthcheck)
            if key in checked:
                continue
            checked.add(key)
            entry_path = str((validation.root / tool.entry).resolve(strict=True))
            ordinal = len(instances)
            argv = [self.python_executable, entry_path]
            instances.append(
                PreparedRuntimeInstanceSpec(
                    entry_id=(
                        f"tool-health:{fingerprint_json([tool.entry, tool.healthcheck])}"
                    ),
                    runtime_kind="tool_health",
                    adapter_id="managed-process-job-v1",
                    adapter_fingerprint=adapter_fingerprint,
                    start_envelope={
                        "schema_version": 1,
                        "argv": argv,
                        "argv_hash": fingerprint_json(argv),
                        "cwd": str(validation.root.resolve(strict=True)),
                        "operation_id": operation_id,
                    },
                    health_envelope={
                        "kind": "deskpet-json-tool-health-v1",
                        "protocol": "deskpet-json-tool-v1",
                        "tool": tool.healthcheck,
                        "timeout_seconds": self.healthcheck_timeout_seconds,
                    },
                    ordinal=ordinal,
                )
            )
        return tuple(instances)

    @staticmethod
    def _has_detector(validation: PackValidationResult, dependency: str) -> bool:
        normalized = dependency.casefold()
        return any(
            tool.id.casefold() == "detect"
            and (
                normalized in tool.provider_name.casefold()
                or normalized in validation.manifest.id.casefold()
            )
            for tool in validation.manifest.tools
        )

    @staticmethod
    async def _terminate_probe(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.communicate()
            except OSError:
                process.kill()
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            if process.returncode is None:
                if os.name == "nt":
                    process.kill()
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    async def _probe_command(
        self,
        dependency: CommandDependency,
        *,
        nonblocking_detector: bool,
    ) -> dict[str, JsonValue]:
        resolved = self.command_finder(dependency.name)
        evidence: dict[str, JsonValue] = {
            "name": dependency.name,
            "required_version": dependency.version,
            "blocking": not nonblocking_detector,
            "resolved_path": resolved,
        }
        if resolved is None:
            evidence["status"] = (
                "missing_nonblocking_detector"
                if nonblocking_detector
                else "missing"
            )
            return evidence
        process_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(
                resolved,
                "--version",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **process_kwargs,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.command_probe_timeout_seconds
            )
        except asyncio.CancelledError:
            if "process" in locals() and process.returncode is None:
                await asyncio.shield(self._terminate_probe(process))
            raise
        except (OSError, TimeoutError) as exc:
            if "process" in locals() and process.returncode is None:
                await self._terminate_probe(process)
            evidence.update(
                {
                    "status": (
                        "probe_failed_nonblocking_detector"
                        if nonblocking_detector
                        else "probe_failed"
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return evidence
        output = (
            stdout.decode("utf-8", errors="replace").strip()
            or stderr.decode("utf-8", errors="replace").strip()
        )[:1000]
        actual = _extract_version(output)
        satisfies = _version_satisfies(actual, dependency.version)
        status = "available"
        if process.returncode != 0 or satisfies is False:
            status = (
                "incompatible_nonblocking_detector"
                if nonblocking_detector
                else "incompatible"
            )
        evidence.update(
            {
                "status": status,
                "exit_code": int(process.returncode or 0),
                "version_output": output,
                "version_satisfies": satisfies,
            }
        )
        return evidence

    async def _run_healthcheck(
        self,
        validation: PackValidationResult,
        tool: ToolEntry,
        *,
        operation_id: str,
    ) -> tuple[LocalRuntimeResult, dict[str, JsonValue]]:
        if tool.runtime != "deskpet-json-tool-v1":
            raise CapabilityManagerError(
                "unsupported_healthcheck_runtime",
                f"cannot healthcheck runtime {tool.runtime}",
            )
        request = LocalRuntimeRequest(
            tool=tool.healthcheck,
            args={},
            root_run_id=f"capability-healthcheck:{operation_id}",
            run_id=f"capability-healthcheck:{operation_id}",
            effect_id=f"healthcheck:{tool.id}",
        )
        result = await self.runtime.execute(
            (
                self.python_executable,
                str((validation.root / tool.entry).resolve(strict=True)),
            ),
            request,
            timeout_seconds=self.healthcheck_timeout_seconds,
            cwd=str(validation.root),
        )
        evidence: dict[str, JsonValue] = {
            "entry": tool.entry,
            "healthcheck": tool.healthcheck,
            "tool_ids": [tool.id],
            "status": result.status,
            "request_id": result.request_id,
            "exit_code": result.exit_code,
            "lease_id": result.lease_id,
            "cleanup_remaining_pids": (
                []
                if result.cleanup is None
                else list(result.cleanup.remaining_pids)
            ),
        }
        if result.response is not None and result.status == "success":
            evidence["value"] = result.response.get("value")
        if result.error_code:
            evidence["error_code"] = result.error_code
        if result.error_message:
            evidence["error_message"] = result.error_message
        return result, evidence

    async def prepare(
        self,
        validation: PackValidationResult,
        *,
        environment_root: Path,
        operation_id: str,
    ) -> Mapping[str, JsonValue]:
        windows_extended_path(environment_root).mkdir(
            parents=True, exist_ok=True
        )
        command_evidence: list[dict[str, JsonValue]] = []
        for dependency in validation.manifest.dependencies.commands:
            detector = self._has_detector(validation, dependency.name)
            evidence = await self._probe_command(
                dependency, nonblocking_detector=detector
            )
            command_evidence.append(evidence)
            if evidence["status"] not in {
                "available",
                "missing_nonblocking_detector",
                "probe_failed_nonblocking_detector",
                "incompatible_nonblocking_detector",
            }:
                raise CapabilityManagerError(
                    "command_dependency_unavailable",
                    f"{dependency.name} {dependency.version}: {evidence['status']}",
                )

        healthchecks: list[dict[str, JsonValue]] = []
        checked: dict[tuple[str, str], int] = {}
        for tool in validation.manifest.tools:
            key = (tool.entry, tool.healthcheck)
            existing = checked.get(key)
            if existing is not None:
                tool_ids = healthchecks[existing]["tool_ids"]
                if isinstance(tool_ids, list):
                    tool_ids.append(tool.id)
                continue
            result, evidence = await self._run_healthcheck(
                validation, tool, operation_id=operation_id
            )
            checked[key] = len(healthchecks)
            healthchecks.append(evidence)
            if not result.ok:
                raise CapabilityManagerError(
                    "capability_healthcheck_failed",
                    (
                        f"{tool.entry}:{tool.healthcheck} returned {result.status}: "
                        f"{result.error_code or result.error_message or 'unknown error'}"
                    ),
                )

        return {
            "kind": "managed-local-v1",
            "path": str(environment_root.resolve(strict=False)),
            "command_dependencies": command_evidence,
            "python_dependencies": [
                {
                    "requirement": dependency.requirement,
                    "status": "declared_host_environment",
                }
                for dependency in validation.manifest.dependencies.python
            ],
            "healthchecks": healthchecks,
        }

    async def probe(
        self,
        record: CapabilityOperationRecord,
        *,
        environment_root: Path | None,
    ) -> Mapping[str, JsonValue]:
        return {
            "kind": "managed-local-v1",
            "operation_id": record.operation_id,
            "exists": bool(environment_root and environment_root.is_dir()),
            "path": None if environment_root is None else str(environment_root),
        }


class MCPManagerRevisionSource:
    """Revision adapter over the public, read-only MCP state snapshot."""

    def __init__(self, manager: Any) -> None:
        self._manager = manager
        self._lock = asyncio.Lock()
        self._fingerprint = ""
        self._revision = 0

    async def revision(self) -> int:
        state = self._manager.server_state()
        if not isinstance(state, Mapping):
            raise TypeError("MCPManager.server_state() must return a mapping")
        payload = {
            str(name): str(value)
            for name, value in sorted(state.items(), key=lambda item: str(item[0]))
        }
        fingerprint = fingerprint_json(payload)
        async with self._lock:
            if fingerprint != self._fingerprint:
                self._fingerprint = fingerprint
                self._revision += 1
            return self._revision


class LegacyPluginCatalogSource:
    """Prompt-only projection of the public legacy plugin inventory."""

    def __init__(self, manager: Any, *, user_scope_key: str = "default") -> None:
        self._manager = manager
        self._user_scope_key = user_scope_key
        self._lock = asyncio.Lock()
        self._fingerprint = ""
        self._revision = 0

    @staticmethod
    def _capability_id(name: str) -> str:
        candidate = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
        candidate = f"legacy_plugin_{candidate}" if candidate else "legacy_plugin"
        if len(candidate) <= 64 and re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?", candidate
        ):
            return candidate
        return "legacy_plugin_" + fingerprint_json(name)[:24]

    async def snapshot(self) -> RevisionedCatalogEntries:
        raw_plugins = self._manager.list_plugins()
        if not isinstance(raw_plugins, list) or not all(
            isinstance(item, Mapping) for item in raw_plugins
        ):
            raise TypeError("PluginManager.list_plugins() must return a list of mappings")
        payload = [
            {str(key): value for key, value in sorted(item.items())}
            for item in sorted(raw_plugins, key=lambda row: str(row.get("name", "")))
        ]
        fingerprint = fingerprint_json(payload)
        async with self._lock:
            if fingerprint != self._fingerprint:
                self._fingerprint = fingerprint
                self._revision += 1
            revision = self._revision

        entries: list[CapabilityCatalogEntry] = []
        for item in payload:
            if item.get("enabled") is not True:
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            manifest_hash = fingerprint_json(item)
            version = str(item.get("version") or f"content-{manifest_hash[:12]}")
            capability_id = self._capability_id(name)
            descriptor = CapabilityVersionDescriptor(
                capability_id=capability_id,
                display_name=name,
                version=version,
                kind="instruction",
                source=f"plugin:{name}",
                description=str(item.get("description") or name),
                aliases=(name,),
                logical_tool_ids=(),
                provider_tool_names=(),
                permission_categories=(),
                effect_kinds=(),
                schema_hash=fingerprint_json({}),
                manifest_hash=manifest_hash,
                health="healthy",
            )
            binding = CapabilityBinding(
                binding_id=fingerprint_json(
                    {
                        "scope": "user",
                        "scope_key": self._user_scope_key,
                        "capability_id": capability_id,
                    }
                ),
                capability_id=capability_id,
                version=version,
                manifest_hash=manifest_hash,
                scope="user",
                scope_key=self._user_scope_key,
                active=True,
                generation=max(1, revision),
            )
            entries.append(CapabilityCatalogEntry(descriptor, (binding,), ()))
        return RevisionedCatalogEntries(revision, tuple(entries))


@dataclass(frozen=True, slots=True)
class CapabilityPlatformInitialization:
    recovered_operations: tuple[CapabilityOperationRecord, ...]
    rehydrated_publications: tuple[Any, ...]
    first_party_installs: tuple[CapabilityInstallResult, ...]
    control_tools_registered: bool


@dataclass(frozen=True, slots=True)
class CapabilityLifecycleStaticRequest:
    """Host-normalized input for a mutation that must not publish while staging."""

    authorization_hash: str
    action: str
    owner_key: str
    scope: str
    scope_key: str
    pack_id: str
    manager_idempotency_key: str
    expected_binding_generation: int
    launch_revocation_epoch: int
    candidate_id: str | None = None
    candidate_mode: str | None = None
    target_version: str | None = None
    target_manifest_hash: str | None = None
    target_package_hash: str | None = None
    target_archive_hash: str | None = None
    source_fence_hash: str | None = None
    rollback_kind: str | None = None
    cause_ref: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedCapabilityLifecycleMutation:
    """Static Manager result plus the exact product-neutral runtime set."""

    manager_operation_id: str
    request: CapabilityLifecycleStaticRequest
    runtime_set_ref: str | None
    runtime_set: PreparedRuntimeSet | None

    def __post_init__(self) -> None:
        if not self.manager_operation_id:
            raise ValueError("manager operation id is required")
        if (self.runtime_set_ref is None) != (self.runtime_set is None):
            raise ValueError(
                "runtime set ref and prepared runtime set must be both present or absent"
            )
        if self.runtime_set is not None:
            if self.runtime_set.operation_id != self.manager_operation_id:
                raise ValueError("runtime set operation identity mismatch")
            if (
                self.runtime_set.launch_revocation_epoch
                != self.request.launch_revocation_epoch
            ):
                raise ValueError("runtime set revocation epoch mismatch")


class CapabilityLifecycleStaticPort(Protocol):
    """Future Manager seam: every prepare method here is static-only."""

    async def prepare_static(
        self,
        request: CapabilityLifecycleStaticRequest,
    ) -> PreparedCapabilityLifecycleMutation: ...

    async def activate_empty(
        self,
        prepared: PreparedCapabilityLifecycleMutation,
    ) -> Mapping[str, Any]: ...

    async def abort_static(
        self,
        prepared: PreparedCapabilityLifecycleMutation,
        *,
        reason_code: str,
    ) -> bool: ...


class CapabilityPlatform:
    """One lifecycle owner for the production capability subsystem."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        store: CapabilityStore,
        user_data_root: str | Path,
        environment: PackEnvironment | None = None,
        source_resolver: CapabilitySourceResolver | None = None,
        configured_sources: Mapping[str, PackSourceRequest] | None = None,
        runtime: LocalToolRuntime | None = None,
        input_resolver: InputViewResolver | None = None,
        resource_authorizer: ResourceAuthorizer | None = None,
        brokered_authority_resolver: BrokeredAuthorityResolver | None = None,
        skill_loader: Any | None = None,
        mcp_manager: Any | None = None,
        plugin_manager: Any | None = None,
        semantic_embedder: SemanticEmbedder | None = None,
        first_party_pack_roots: Sequence[str | Path] | None = None,
        register_control_surface: bool = True,
        python_executable: str | Path | None = None,
        command_finder: CommandFinder | None = None,
        catalog_gate: CapabilityCatalogGate | None = None,
        identity_gate: IdentityReadyGate | None = None,
        revocation_barrier: RevocationBarrier | None = None,
        execution_scope_authority: ExecutionScopeAuthorityPort | None = None,
        runtime_set_ledger: RuntimeSetLedgerPort | None = None,
        runtime_adapters: Mapping[str, PreparedRuntimeAdapter] | None = None,
        runtime_projection_commit: OwnerRuntimeProjectionCommitPort | None = None,
        run_catalog_lease_preparer: RunCatalogLeasePreparationPort | None = None,
        lifecycle_static_port: CapabilityLifecycleStaticPort | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.environment = environment or PackEnvironment.current()
        self.publish_lock = asyncio.Lock()
        self.catalog_gate = catalog_gate or CapabilityCatalogGate()
        self.identity_gate = identity_gate
        self.revocation_barrier = (
            revocation_barrier or get_process_revocation_barrier()
        )
        self.execution_scope_authority = execution_scope_authority
        self.runtime_projection_commit = runtime_projection_commit
        self.run_catalog_lease_preparer = run_catalog_lease_preparer
        self.lifecycle_static_port = lifecycle_static_port
        self.snapshot_lease_ready_gate = SnapshotLeaseReadyGate()
        self._prepared_child_catalog_leases: dict[
            str, PreparedRunCatalogLease
        ] = {}
        self.runtime_set_ledger = runtime_set_ledger
        self.runtime = runtime or LocalToolRuntime()
        self.input_resolver = input_resolver or InputViewResolver()
        self.brokered_planner = BrokeredEffectPlanner(
            resource_authorizer=resource_authorizer
        )
        self.proxy = LocalToolProxy(
            runtime=self.runtime,
            input_resolver=self.input_resolver,
            brokered_planner=self.brokered_planner,
        )
        self.spec_factory = LocalCapabilityToolSpecFactory(
            proxy=self.proxy,
            python_executable=python_executable,
            brokered_authority_resolver=brokered_authority_resolver,
        )
        self.environment_preparer = ManagedEnvironmentPreparer(
            runtime=self.runtime,
            python_executable=python_executable,
            command_finder=command_finder,
        )
        self.publisher = ToolRegistryCapabilityPublisher(
            registry=registry,
            spec_factory=self.spec_factory,
            environment=self.environment,
        )
        configured_sources = dict(configured_sources or {})
        effective_source_resolver = source_resolver or CapabilitySourceResolver(
            configured_sources=configured_sources
        )
        self.manager = CapabilityPackManager(
            store=store,
            user_data_root=user_data_root,
            source_resolver=effective_source_resolver,
            publisher=self.publisher,
            environment_preparer=self.environment_preparer,
            environment=self.environment,
            publish_lock=self.publish_lock,
        )
        self.registry_source = ToolRegistryCatalogSource(registry)
        skill_source = (
            SkillLoaderCatalogSource(skill_loader)
            if skill_loader is not None
            else StaticCapabilityEntrySource()
        )
        mcp_source = (
            MCPManagerRevisionSource(mcp_manager)
            if mcp_manager is not None
            else StaticCatalogRevisionSource()
        )
        legacy_source = (
            LegacyPluginCatalogSource(plugin_manager)
            if plugin_manager is not None
            else StaticCapabilityEntrySource()
        )
        configured_source = ConfiguredCapabilityCatalogSource(
            configured_sources
        )
        self.hub = CapabilityHub(
            store=store,
            registry_source=self.registry_source,
            skill_source=skill_source,
            mcp_revision_source=mcp_source,
            legacy_source=CompositeCapabilityEntrySource(
                legacy_source,
                configured_source,
            ),
            publish_lock=self.publish_lock,
            catalog_gate=self.catalog_gate,
        )
        self.runtime_sets = (
            CapabilityRuntimeSetCoordinator(
                ledger=runtime_set_ledger,
                adapters=dict(runtime_adapters or {}),
                projection=self,
            )
            if runtime_set_ledger is not None
            else None
        )

        self.search = CapabilitySearch(store=store, embedder=semantic_embedder)
        self.tool_service = CapabilityToolService(
            hub=self.hub,
            search=self.search,
            manager=self.manager,
        )
        self._register_control_surface = register_control_surface
        if first_party_pack_roots is None:
            repository_root = Path(__file__).resolve().parents[3]
            pack_root = repository_root / "capability-packs"
            first_party_pack_roots = (
                tuple(
                    path
                    for path in sorted(pack_root.iterdir())
                    if path.is_dir() and (path / "deskpet-pack.json").is_file()
                )
                if pack_root.is_dir()
                else ()
            )
        self.first_party_pack_roots = tuple(
            Path(path).resolve(strict=False) for path in first_party_pack_roots
        )
        self._initialize_lock = asyncio.Lock()
        self._initialization: CapabilityPlatformInitialization | None = None
        self._closed = False

    def bind_execution_identity_gate(self, gate: IdentityReadyGate) -> None:
        """Complete the one-time startup wiring for execution-scope leases.

        Production intentionally initializes the capability foundation before
        Companion identity.  The later authority-composition phase may fill
        the initially empty port, but it must never replace an already-bound
        identity gate because in-flight Runs rely on that object's epochs.
        """

        current = self.identity_gate
        if current is not None and current is not gate:
            raise RuntimeError("execution_scope_identity_gate_already_bound")
        self.identity_gate = gate

    @staticmethod
    def _detail_gate_keys(
        keys: Sequence[OwnerScopeKey],
    ) -> tuple[CatalogGateKey, ...]:
        return tuple(
            CatalogGateKey(key.owner_key, key.scope, key.scope_key, "*")
            for key in sorted(set(keys))
        )

    @staticmethod
    def _validate_detail_snapshot(snapshot: PlatformDetailSnapshot) -> None:
        bindings_by_key: dict[OwnerScopeKey, list[CapabilityBinding]] = {}
        for binding in snapshot.bindings:
            key = OwnerScopeKey(
                binding.owner_key,
                binding.scope,
                binding.scope_key,
            )
            bindings_by_key.setdefault(key, []).append(binding)
        for token in snapshot.tokens.items:
            bindings = tuple(bindings_by_key.get(token.key, ()))
            expected_stamp = (
                OwnerBindingSetStamp(token.key, bindings).fingerprint
                if bindings
                else EMPTY_OWNER_BINDING_SET_STAMP
            )
            if token.committed_owner_binding_set_stamp != expected_stamp:
                raise RuntimeError("catalog_reconciling")
            if not token.exists and bindings:
                raise RuntimeError("catalog_reconciling")

    async def read_detail_snapshot(
        self,
        keys: Sequence[OwnerScopeKey],
    ) -> PlatformDetailSnapshot:
        """Read a detail snapshot under the production publish/gate lock order."""

        ordered = tuple(sorted(set(keys)))
        async with self.publish_lock:
            async with self.catalog_gate.read(self._detail_gate_keys(ordered)):
                snapshot = await self.store.read_detail_snapshot(ordered)
                self._validate_detail_snapshot(snapshot)
                return snapshot

    async def read_detail_token_vector(
        self,
        keys: Sequence[OwnerScopeKey],
    ) -> PlatformDetailTokenVector:
        """Re-read the exact token set without dropping absent owner keys."""

        ordered = tuple(sorted(set(keys)))
        async with self.publish_lock:
            async with self.catalog_gate.read(self._detail_gate_keys(ordered)):
                vector = await self.store.read_detail_token_vector(ordered)
                if tuple(item.key for item in vector.items) != ordered:
                    raise RuntimeError("catalog_reconciling")
                return vector

    async def acquire_current_execution_scope(
        self,
        *,
        run_id: str = "",
        owner_key: str,
        profile_generation: int,
        binding_epoch: int,
        capability_hash: str,
        scope_hash: str,
    ) -> CurrentExecutionScopeLease:
        """Production ``CurrentExecutionScopeLeasePort`` implementation."""

        authority = self.execution_scope_authority
        identity_gate = self.identity_gate
        if authority is None or identity_gate is None:
            raise RuntimeError("execution_scope_authority_unavailable")
        try:
            current = identity_gate.freeze()
        except CompanionIdentityNotReady as exc:
            raise RuntimeError("execution_scope_identity_not_ready") from exc
        if (
            current.owner_key != owner_key
            or current.owner.profile_generation != profile_generation
            or current.binding_epoch != binding_epoch
        ):
            raise RuntimeError("execution_scope_identity_stale")

        barrier_context = self.revocation_barrier.shared(timeout=5.0)
        await barrier_context.__aenter__()
        gate_context = None
        publish_acquired = False
        try:
            await asyncio.wait_for(self.publish_lock.acquire(), timeout=5.0)
            publish_acquired = True
            facts = await authority.resolve_current_execution_scope(
                run_id=run_id,
                owner_key=owner_key,
                profile_generation=profile_generation,
                binding_epoch=binding_epoch,
            )
            keys = tuple(
                CatalogGateKey(
                    facts.owner_key,
                    facts.scope,
                    facts.scope_key,
                    pack_id,
                )
                for pack_id in (facts.pack_ids or ("*",))
            )
            gate_context = self.catalog_gate.read(keys, timeout=5.0)
            await gate_context.__aenter__()
            if (
                facts.owner_key != owner_key
                or facts.profile_generation != profile_generation
                or facts.binding_epoch != binding_epoch
                or facts.capability_hash != capability_hash
                or facts.scope_hash != scope_hash
                or not facts.owner_binding_set_stamp
            ):
                raise RuntimeError("execution_scope_fingerprint_stale")
            return _PlatformExecutionScopeLease(
                facts,
                gate_context=gate_context,
                publish_lock=self.publish_lock,
                barrier_context=barrier_context,
            )
        except BaseException:
            if gate_context is not None:
                await gate_context.__aexit__(None, None, None)
            if publish_acquired:
                self.publish_lock.release()
            await barrier_context.__aexit__(None, None, None)
            raise

    async def prepare_run_catalog_lease(
        self,
        *,
        scope: CapabilityScope,
        owner_key: str,
        prepared_tool_set: PreparedToolSet,
        prepared_tool_set_fingerprint: str,
        run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        owner_operation_id: str,
    ) -> PreparedRunCatalogLeaseV1:
        """Single-capture entrypoint; durable preparation is delegated to Store."""

        preparer = self.run_catalog_lease_preparer
        if preparer is None:
            raise RuntimeError("run_catalog_lease_preparer_unavailable")
        async with self.publish_lock:
            async with self.catalog_gate.read(
                self.hub._gate_keys(scope, owner_key)
            ):
                snapshot, entries = await self.hub._snapshot_locked(scope)
                return await preparer.prepare_captured_run_catalog(
                    snapshot=snapshot,
                    selected_entries=entries,
                    owner_key=owner_key,
                    prepared_tool_set=prepared_tool_set,
                    prepared_tool_set_fingerprint=prepared_tool_set_fingerprint,
                    ready_gate=self.snapshot_lease_ready_gate,
                    run_id=run_id,
                    root_run_id=root_run_id,
                    request_id=request_id,
                    turn_id=turn_id,
                    owner_operation_id=owner_operation_id,
                )

    async def _load_child_catalog_source(
        self,
        *,
        snapshot_ref: str = "",
        source_run_id: str = "",
        source_lease_intent_id: str = "",
        source_lease_intent_hash: str = "",
        lease_intent_id: str = "",
    ) -> tuple[
        RunCatalogContentStamp,
        ProcessCatalogStamp,
        str,
        Mapping[str, JsonValue],
        str,
        Mapping[str, int],
        tuple[str, ...],
    ]:
        """Load one exact parent projection without consulting live catalogs."""

        async with self.store.read_connection() as db:
            if lease_intent_id:
                where_clause = (
                    "intent.lease_intent_id=? AND intent.status='bound' "
                    "AND projection.status IN ('prepared','ready')"
                )
                parameters = (lease_intent_id,)
            else:
                where_clause = (
                    "intent.snapshot_ref=? AND intent.run_id=? "
                    "AND intent.lease_intent_id=? "
                    "AND intent.lease_intent_hash=? "
                    "AND intent.status='bound' "
                    "AND projection.status='ready'"
                )
                parameters = (
                    snapshot_ref,
                    source_run_id,
                    source_lease_intent_id,
                    source_lease_intent_hash,
                )
            source_rows = await (
                await db.execute(
                    f"""SELECT intent.snapshot_ref,
                              intent.run_catalog_content_stamp,
                              projection.process_instance_id,
                              projection.process_catalog_stamp,
                              projection.prepared_tool_set_envelope_json,
                              projection.prepared_tool_set_hash,
                              snapshot.request_owner_key,
                              snapshot.request_scope_canonical_json,
                              snapshot.catalog_generation_vector_json
                       FROM capability_snapshot_lease_intents AS intent
                       JOIN capability_runtime_projection_receipts AS projection
                         ON projection.lease_intent_id=intent.lease_intent_id
                        AND projection.purpose='snapshot_pin'
                       JOIN capability_run_catalog_snapshots AS snapshot
                         ON snapshot.run_catalog_content_stamp=
                            intent.run_catalog_content_stamp
                       WHERE {where_clause}""",
                    parameters,
                )
            ).fetchall()
            if len(source_rows) != 1:
                raise RuntimeError(
                    "child_catalog_source_not_trusted"
                    if not source_rows
                    else "child_catalog_source_ambiguous"
                )
            source = source_rows[0]
            entry_rows = await (
                await db.execute(
                    """SELECT entry_kind,descriptor_fingerprint,
                              descriptor_envelope_json,
                              tool_spec_fingerprints_json
                       FROM capability_run_catalog_snapshot_entries
                       WHERE run_catalog_content_stamp=?
                       ORDER BY ordinal""",
                    (str(source["run_catalog_content_stamp"]),),
                )
            ).fetchall()

        scope_payload = json.loads(
            str(source["request_scope_canonical_json"])
        )
        vector = json.loads(str(source["catalog_generation_vector_json"]))
        envelope = json.loads(
            str(source["prepared_tool_set_envelope_json"])
        )
        if not all(
            isinstance(value, Mapping)
            for value in (scope_payload, vector, envelope)
        ):
            raise RuntimeError("child_catalog_source_corrupt")
        entries = tuple(
            RunCatalogEntryIdentity(
                entry_kind=str(row["entry_kind"]),
                descriptor_fingerprint=str(
                    row["descriptor_fingerprint"]
                ),
                canonical_envelope=json.loads(
                    str(row["descriptor_envelope_json"])
                ),
            )
            for row in entry_rows
        )
        content = RunCatalogContentStamp(
            CapabilityScope(**dict(scope_payload)),
            entries,
            fingerprint=str(source["run_catalog_content_stamp"]),
        )
        process = ProcessCatalogStamp(
            process_instance_id=str(source["process_instance_id"]),
            run_catalog_content_stamp=content.fingerprint,
            catalog_generation=int(vector.get("catalog", 0)),
            registry_revision=int(vector.get("registry", 0)),
            skill_revision=int(vector.get("skill", 0)),
            mcp_revision=int(vector.get("mcp", 0)),
            fingerprint=str(source["process_catalog_stamp"]),
        )
        durable_snapshot_ref = str(source["snapshot_ref"])
        if content.snapshot_ref != durable_snapshot_ref or (
            snapshot_ref and durable_snapshot_ref != snapshot_ref
        ):
            raise RuntimeError("child_catalog_snapshot_ref_stale")
        tool_fingerprints = {
            str(value)
            for row in entry_rows
            for value in json.loads(
                str(row["tool_spec_fingerprints_json"])
            )
        }
        exact_tools = envelope.get("exact_tools", ())
        if isinstance(exact_tools, (list, tuple)):
            tool_fingerprints.update(
                str(item["tool_spec_fingerprint"])
                for item in exact_tools
                if isinstance(item, Mapping)
                and item.get("tool_spec_fingerprint")
            )
        return (
            content,
            process,
            str(source["request_owner_key"]),
            dict(envelope),
            str(source["prepared_tool_set_hash"]),
            {str(key): int(value) for key, value in vector.items()},
            tuple(sorted(tool_fingerprints)),
        )

    async def prepare_child_lease_projection(
        self,
        *,
        source_snapshot_ref: str,
        source_run_id: str,
        source_lease_intent_id: str,
        source_lease_intent_hash: str,
        child_run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        owner_operation_id: str,
    ) -> PreparedRunCatalogLease:
        """Prepare an independent child pin from its parent's durable catalog."""

        async with self.publish_lock:
            (
                content,
                process,
                owner_key,
                prepared_envelope,
                prepared_hash,
                generation_vector,
                tool_fingerprints,
            ) = await self._load_child_catalog_source(
                snapshot_ref=source_snapshot_ref,
                source_run_id=source_run_id,
                source_lease_intent_id=source_lease_intent_id,
                source_lease_intent_hash=source_lease_intent_hash,
            )
            pin: PendingProcessPinToken | None = None
            intent_id = ""
            try:
                async with self.store.write_transaction() as db:
                    projection = (
                        await self.store.prepare_run_catalog_projection_in_tx(
                            db,
                            content=content,
                            process_stamp=process,
                            snapshot_ref=source_snapshot_ref,
                            request_owner_key=owner_key,
                            catalog_generation_vector=generation_vector,
                            run_id=child_run_id,
                            root_run_id=root_run_id,
                            request_id=request_id,
                            turn_id=turn_id,
                            owner_operation_id=owner_operation_id,
                            lease_owner_kind="run_start",
                            prepared_tool_set_envelope=prepared_envelope,
                            prepared_tool_set_hash=prepared_hash,
                        )
                    )
                    intent = projection.intent
                    intent_id = intent.lease_intent_id
                    pin = PendingProcessPinToken(
                        hub=self.hub,
                        snapshot_ref=source_snapshot_ref,
                        run_id=child_run_id,
                        lease_intent_id=intent_id,
                        tool_spec_fingerprints=tool_fingerprints,
                    )
                    await pin.install()
                    await self.snapshot_lease_ready_gate.register_pending(
                        intent_id=intent_id,
                        run_id=child_run_id,
                        pin=pin,
                    )
                pin.commit()
            except BaseException:
                if pin is not None:
                    await pin.rollback()
                if intent_id:
                    await self.snapshot_lease_ready_gate.unregister_pending(
                        intent_id,
                        child_run_id,
                    )
                raise

        lease = PreparedRunCatalogLease(
            snapshot_ref=source_snapshot_ref,
            run_catalog_content_stamp=content.fingerprint,
            process_catalog_stamp=process.fingerprint,
            lease_intent_id=projection.intent.lease_intent_id,
            lease_intent_hash=projection.intent.lease_intent_hash,
            prepared_tool_set_fingerprint=str(
                prepared_envelope.get("external_ref") or prepared_hash
            ),
            run_id=child_run_id,
            root_run_id=root_run_id,
            entry_set_hash=content.entry_set_hash,
            expected_entry_count=len(content.entries),
            prepared_tool_set_capture_hash=prepared_hash,
            prepared_tool_set_envelope=prepared_envelope,
            lease_entries=tuple(item.to_dict() for item in content.entries),
            projection_receipt_id=(
                projection.projection_receipt.projection_receipt_id
            ),
            projection_receipt_hash=(
                projection.projection_receipt.projection_receipt_hash
            ),
            process_instance_id=process.process_instance_id,
            _store=self.store,
            _hub=self.hub,
            _ready_gate=self.snapshot_lease_ready_gate,
            _pin=pin,
        )
        self._prepared_child_catalog_leases[child_run_id] = lease
        return lease

    async def activate_child_snapshot_after_commit(
        self, start_snapshot: Any
    ) -> PreparedRunCatalogLease | None:
        """Rehydrate a child pin, validate its bound start, then open ReadyGate."""

        intent_id = str(
            getattr(start_snapshot, "capability_lease_intent_ref", "") or ""
        )
        intent_hash = str(
            getattr(start_snapshot, "capability_lease_intent_hash", "") or ""
        )
        capability_snapshot = json.loads(
            str(start_snapshot.capability_snapshot_json)
        )
        snapshot_ref = str(
            capability_snapshot.get("catalog_snapshot_ref") or ""
        )
        if not intent_id:
            if snapshot_ref:
                raise RuntimeError("child_catalog_lease_intent_missing")
            return None
        if not snapshot_ref or not intent_hash:
            raise RuntimeError("child_catalog_lease_identity_incomplete")

        state = await self.store.read_snapshot_projection_state(intent_id)
        if (
            state is None
            or state.lease_intent_hash != intent_hash
            or state.intent_status != "bound"
            or state.start_fingerprint != start_snapshot.start_fingerprint
            or state.run_catalog_content_stamp
            != str(capability_snapshot.get("run_catalog_content_stamp") or "")
        ):
            raise RuntimeError("child_catalog_projection_not_bound")
        lease = self._prepared_child_catalog_leases.get(
            start_snapshot.run_id
        )
        pin = None if lease is None else lease._pin
        if pin is None or not pin.installed:
            (
                content,
                process,
                _owner_key,
                prepared_envelope,
                prepared_hash,
                _generation_vector,
                tool_fingerprints,
            ) = await self._load_child_catalog_source(
                snapshot_ref=snapshot_ref,
                lease_intent_id=intent_id,
            )
            pin = PendingProcessPinToken(
                hub=self.hub,
                snapshot_ref=snapshot_ref,
                run_id=start_snapshot.run_id,
                lease_intent_id=intent_id,
                tool_spec_fingerprints=tool_fingerprints,
            )
            await pin.install()
            await self.snapshot_lease_ready_gate.register_pending(
                intent_id=intent_id,
                run_id=start_snapshot.run_id,
                pin=pin,
            )
            pin.commit()
            lease = PreparedRunCatalogLease(
                snapshot_ref=snapshot_ref,
                run_catalog_content_stamp=content.fingerprint,
                process_catalog_stamp=state.process_catalog_stamp,
                lease_intent_id=intent_id,
                lease_intent_hash=intent_hash,
                prepared_tool_set_fingerprint=str(
                    prepared_envelope.get("external_ref") or prepared_hash
                ),
                run_id=start_snapshot.run_id,
                root_run_id=json.loads(
                    start_snapshot.run_context_json
                )["root_run_id"],
                entry_set_hash=content.entry_set_hash,
                expected_entry_count=len(content.entries),
                prepared_tool_set_capture_hash=prepared_hash,
                prepared_tool_set_envelope=prepared_envelope,
                lease_entries=tuple(
                    item.to_dict() for item in content.entries
                ),
                projection_receipt_id=state.projection_receipt_id,
                projection_receipt_hash=state.projection_receipt_hash,
                process_instance_id=state.process_instance_id,
                _store=self.store,
                _hub=self.hub,
                _ready_gate=self.snapshot_lease_ready_gate,
                _pin=pin,
            )
            self._prepared_child_catalog_leases[start_snapshot.run_id] = lease
        activated = await self.store.activate_snapshot_projection_ready(
            intent_id,
            intent_hash=intent_hash,
            start_fingerprint=start_snapshot.start_fingerprint,
            process_instance_id=state.process_instance_id,
            process_catalog_stamp=state.process_catalog_stamp,
            pin_token_hash=pin.token_hash,
        )
        await self.snapshot_lease_ready_gate.activate(
            intent_id=intent_id,
            intent_hash=intent_hash,
            process_catalog_stamp=state.process_catalog_stamp,
            projection_receipt_hash=activated.projection_receipt_hash,
        )
        await self.snapshot_lease_ready_gate.require_ready(
            intent_id=intent_id,
            intent_hash=intent_hash,
            process_catalog_stamp=state.process_catalog_stamp,
        )
        return lease

    async def retire_run_catalog_ready(self, run_id: str) -> None:
        lease = self._prepared_child_catalog_leases.pop(run_id, None)
        if lease is None:
            return
        await lease._pin.rollback()
        await self.snapshot_lease_ready_gate.retire(
            intent_id=lease.lease_intent_id,
            run_id=run_id,
        )

    async def activate_prepared_projection(
        self, prepared_set: PreparedRuntimeSet
    ) -> Mapping[str, Any]:
        commit = self.runtime_projection_commit
        if commit is None:
            raise RuntimeError("runtime_projection_commit_unavailable")
        key = CatalogGateKey(
            prepared_set.owner_key,
            prepared_set.scope,
            prepared_set.scope_key,
            "*",
        )
        async with self.publish_lock:
            async with self.catalog_gate.writer(
                (key,), reason="owner_runtime_activation"
            ) as token:
                receipt = dict(
                    await commit.activate_owner_runtime_projection(prepared_set)
                )
                committed_stamp = str(
                    receipt.get("committed_owner_binding_set_stamp") or ""
                )
                receipt_hash = str(
                    receipt.get("manager_receipt_set_hash") or ""
                )
                if committed_stamp != prepared_set.owner_binding_set_stamp:
                    raise RuntimeError("owner_runtime_binding_stamp_mismatch")
                if not receipt_hash:
                    raise RuntimeError("owner_runtime_receipt_hash_missing")
                ledger = self.runtime_set_ledger
                if ledger is None:
                    raise RuntimeError("runtime_set_ledger_unavailable")
                await ledger.mark_set_activated(
                    prepared_set.operation_id,
                    prepared_set.runtime_set_hash,
                )
            await self.catalog_gate.open_after_reconcile(
                token,
                committed_stamp=committed_stamp,
                manager_receipt_hash=receipt_hash,
            )
        return receipt

    async def require_run_catalog_ready(self, run_id: str) -> None:
        await self.snapshot_lease_ready_gate.require_run_ready(run_id)

    def _runtime_coordinator(self) -> CapabilityRuntimeSetCoordinator:
        if self.runtime_sets is None:
            raise RuntimeError("runtime_set_ledger_unavailable")
        return self.runtime_sets

    async def prepare_owner_runtime_activation(self, **kwargs: Any):
        return await self._runtime_coordinator().prepare_owner_runtime_activation(
            **kwargs
        )

    async def start_prepared_runtime_instance(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
        authorization: RuntimeLaunchAuthorization,
        **kwargs: Any,
    ):
        return await self._runtime_coordinator().start_prepared_runtime_instance(
            prepared_set, instance, authorization, **kwargs
        )

    async def await_prepared_runtime_instance_health(
        self,
        prepared_set: PreparedRuntimeSet,
        instance: PreparedRuntimeInstanceSpec,
    ):
        return await self._runtime_coordinator().await_prepared_runtime_instance_health(
            prepared_set, instance
        )

    async def activate_prepared_set(self, prepared_set: PreparedRuntimeSet):
        return await self._runtime_coordinator().activate_prepared_set(prepared_set)

    async def abort_prepared_set(
        self, prepared_set: PreparedRuntimeSet, *, reason_code: str
    ) -> None:
        await self._runtime_coordinator().abort_prepared_set(
            prepared_set, reason_code=reason_code
        )

    async def prepare_lifecycle_mutation_static(
        self,
        request: CapabilityLifecycleStaticRequest,
    ) -> PreparedCapabilityLifecycleMutation:
        """Stage only; never fall back to the monolithic v1 Manager methods.

        The existing Manager install/update/rollback/uninstall entrypoints can
        publish a binding before returning.  Calling one of them here would
        make the Companion dispatcher believe it still owns the start/health
        fence, so the absence of the dedicated seam is deliberately terminal.
        """

        port = self.lifecycle_static_port
        if port is None:
            raise CapabilityManagerError(
                "static_lifecycle_prepare_unavailable",
                "CapabilityPackManager has no static lifecycle prepare API",
            )
        prepared = await port.prepare_static(request)
        if prepared.request != request:
            raise CapabilityManagerError(
                "static_lifecycle_request_mismatch",
                "static lifecycle preparation changed its trusted request",
            )
        return prepared

    async def activate_empty_lifecycle_mutation(
        self,
        prepared: PreparedCapabilityLifecycleMutation,
    ) -> Mapping[str, Any]:
        if prepared.runtime_set is not None:
            raise CapabilityManagerError(
                "runtime_set_activation_required",
                "non-empty lifecycle mutations use activate_prepared_set",
            )
        port = self.lifecycle_static_port
        if port is None:
            raise CapabilityManagerError(
                "static_lifecycle_activate_unavailable",
                "CapabilityPackManager has no static lifecycle activate API",
            )
        return await port.activate_empty(prepared)

    async def abort_lifecycle_mutation_static(
        self,
        prepared: PreparedCapabilityLifecycleMutation,
        *,
        reason_code: str,
    ) -> bool:
        port = self.lifecycle_static_port
        if port is None:
            return False
        return bool(
            await port.abort_static(prepared, reason_code=reason_code)
        )

    def _register_control_tools(self) -> bool:
        snapshot = self.registry.catalog_snapshot()
        by_name = {spec.name: spec for spec in snapshot.specs}
        present = _CONTROL_TOOL_NAMES & set(by_name)
        if present == _CONTROL_TOOL_NAMES:
            if any(by_name[name].source != "builtin" for name in present):
                raise CapabilityManagerError(
                    "control_tool_collision",
                    "capability control tool names are owned by another source",
                )
            return False
        if present:
            raise CapabilityManagerError(
                "partial_control_surface",
                "capability control tools are only partially registered",
            )
        register_capability_tools(self.registry, self.tool_service)
        return True

    async def recover(self) -> tuple[CapabilityOperationRecord, ...]:
        async with self._initialize_lock:
            if self._closed:
                raise RuntimeError("capability platform is closed")
            await self.manager.initialize()
            return await self.manager.recover()

    async def initialize(self) -> CapabilityPlatformInitialization:
        if self._initialization is not None:
            return self._initialization
        async with self._initialize_lock:
            if self._initialization is not None:
                return self._initialization
            if self._closed:
                raise RuntimeError("capability platform is closed")
            await self.manager.initialize()
            recovered = await self.manager.recover()
            registered = (
                self._register_control_tools()
                if self._register_control_surface
                else False
            )
            rehydrated = await self.manager.rehydrate_active_bindings()
            installed: list[CapabilityInstallResult] = []
            for pack_root in self.first_party_pack_roots:
                try:
                    validation = load_and_validate_pack(
                        pack_root, environment=self.environment
                    )
                except PackCompatibilityError as exc:
                    # 平台不兼容的 first-party pack（如 windows-only 的
                    # godot）在别的平台上属正常情况：跳过并留日志，绝不
                    # 让整个 capability runtime / 后端启动失败。
                    logging.getLogger(__name__).info(
                        "first_party_pack_skipped_incompatible pack=%s reason=%s",
                        pack_root,
                        exc,
                    )
                    continue
                manifest = validation.manifest
                if manifest.source.type != "builtin":
                    raise PackManifestError(
                        "first_party_source_invalid",
                        f"{manifest.id} must declare source.type=builtin",
                    )
                installed.append(
                    await self.manager.install(
                        PackSourceRequest(
                            source_type="builtin",
                            uri=str(pack_root),
                            revision=manifest.source.revision,
                        ),
                        scope="builtin",
                        scope_key="builtin",
                        idempotency_key=(
                            "first-party:"
                            f"{manifest.id}:{manifest.version}:{manifest.manifest_hash}"
                        ),
                        expected_pack_id=manifest.id,
                    )
                )
            result = CapabilityPlatformInitialization(
                recovered_operations=tuple(recovered),
                rehydrated_publications=tuple(rehydrated),
                first_party_installs=tuple(installed),
                control_tools_registered=registered,
            )
            self._initialization = result
            return result

    async def shutdown(self) -> tuple[ProcessCleanupReport, ...]:
        async with self._initialize_lock:
            if self._closed:
                return ()
            self._closed = True
        return await self.runtime.close()


__all__ = [
    "BrokeredAuthorityResolver",
    "BrokeredInvocationAuthority",
    "CapabilityPlatform",
    "CapabilityPlatformInitialization",
    "CapabilityLifecycleStaticPort",
    "CapabilityLifecycleStaticRequest",
    "PreparedCapabilityLifecycleMutation",
    "CurrentExecutionScopeFacts",
    "ExecutionScopeAuthorityPort",
    "LegacyPluginCatalogSource",
    "LocalCapabilityToolSpecFactory",
    "MCPManagerRevisionSource",
    "ManagedEnvironmentPreparer",
    "OwnerRuntimeProjectionCommitPort",
    "PreparedRunCatalogLeaseV1",
    "RunCatalogLeasePreparationPort",
    "SqliteExecutionScopeAuthority",
]
