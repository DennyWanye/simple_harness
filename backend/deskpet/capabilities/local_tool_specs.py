"""本地能力包工具规格：把已安装能力包的工具声明转成工具注册表规格（从 platform 下沉）。"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from deskpet.capabilities.contracts import JsonValue, fingerprint_json
from deskpet.capabilities.input_views import InputViewRequest
from deskpet.capabilities.manager import CapabilityManagerError
from deskpet.capabilities.manifest import PackValidationResult, ToolEntry, windows_extended_path
from deskpet.capabilities.publisher import capability_registry_source, capability_spec_version
from deskpet.capabilities.tool_proxy import (
    BrokeredPlanEnvelope,
    LocalToolDefinition,
    LocalToolProxy,
)
from deskpet.tools.build_identity import ExecutionBuildIdentity
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry, ToolSpec, tool_spec_fingerprint
from deskpet.types.task_grants import ResourceSelector
from deskpet.workflows.contracts import EffectKind, EffectPolicy
from deskpet.workflows.effects import NormalizedToolOutcome

BrokeredAuthorityResolver = Callable[
    [ToolExecutionContext],
    "BrokeredInvocationAuthority | Awaitable[BrokeredInvocationAuthority]",
]


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
        raise CapabilityManagerError("tool_schema_invalid", f"{path} must contain one JSON object")
    unknown = sorted(set(value) - _STRICT_SCHEMA_KEYS)
    if unknown:
        raise CapabilityManagerError(
            "tool_schema_invalid",
            f"{path} has unsupported top-level fields: {', '.join(unknown)}",
        )
    if value.get("type") != "object":
        raise CapabilityManagerError("tool_schema_invalid", f"{path} must declare type=object")
    properties = value.get("properties", {})
    if not isinstance(properties, dict) or not all(
        isinstance(name, str) and isinstance(schema, dict) for name, schema in properties.items()
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
    return NormalizedToolOutcome.malformed("local capability handler returned an unsupported value")


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


def _input_requests(tool: ToolEntry, args: Mapping[str, Any]) -> tuple[InputViewRequest, ...]:
    requests: list[InputViewRequest] = []
    for name in tool.input_views:
        if name not in args:
            raise ValueError(f"declared input view is missing: {name}")
        raw = args[name]
        if isinstance(raw, Mapping):
            extra = set(raw) - {"resource", "kind", "view_kind", "opaque_ref"}
            if extra or "resource" not in raw:
                raise ValueError(f"input view {name!r} must contain resource/kind/view_kind only")
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
            raise ValueError(f"input view {name!r} has unsupported view_kind {view_kind!r}")
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
            if (
                kind in {"file", "directory", "artifact"}
                and isinstance(resource, str)
                and resource.strip()
            ):
                selectors.append(ResourceSelector.filesystem(resource, *filesystem_access))

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
                suffix = "" if parsed.port in {None, default_port} else f":{parsed.port}"
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
            elif lowered in {"path", "source", "destination"} or lowered.endswith("_path"):
                selectors.append(ResourceSelector.filesystem(value, *filesystem_access))

        if tool.execution_profile == "brokered-effect-v1":
            managed_root = context.write_scope_root or context.workspace
            if not managed_root:
                raise ValueError(f"{tool.provider_name} requires a trusted workspace root")
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

    async def _authority(self, context: ToolExecutionContext) -> BrokeredInvocationAuthority | None:
        resolver = self.brokered_authority_resolver
        if resolver is None:
            return None
        value = resolver(context)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, BrokeredInvocationAuthority):
            raise TypeError("brokered authority resolver must return BrokeredInvocationAuthority")
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
        source = capability_registry_source(manifest.id, manifest.version, manifest.manifest_hash)
        spec_version = capability_spec_version(manifest.version, manifest.manifest_hash)
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
                            "deskpet-adapter:backend/deskpet/capabilities/platform.py",
                            hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
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
                            {"path": path, "sha256": digest} for path, digest in build_artifacts
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
                    return NormalizedToolOutcome.failure("input_view_invalid", str(exc))
                authority: BrokeredInvocationAuthority | None = None
                if declared_tool.execution_profile == "brokered-effect-v1":
                    try:
                        authority = await self._authority(context)
                    except (TypeError, ValueError) as exc:
                        return NormalizedToolOutcome.failure("brokered_authority_invalid", str(exc))
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
                    task_grant_id=("" if authority is None else authority.task_grant_id),
                    catalog_stamp=({} if authority is None else authority.catalog_stamp),
                )

            policy = EffectPolicy(
                policy_id=(f"capability:{manifest.id}:{tool.id}:{effect_kind.value}"),
                version="v1",
                kind=effect_kind,
                max_attempts=1,
                reusable_across_branches=(effect_kind is EffectKind.DETERMINISTIC_REUSABLE),
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
                    f"capability:{manifest.id}:{tool.id}:{manifest.manifest_hash}"
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
