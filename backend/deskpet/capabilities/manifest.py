# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Strict ``deskpet-pack.json`` v1 parsing and integrity validation."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .contracts import (
    CapabilityContractError,
    CapabilityVersionDescriptor,
    JsonValue,
    canonical_json,
    fingerprint_json,
    provider_tool_name,
)

PACK_MANIFEST_NAME = "deskpet-pack.json"
PACK_SCHEMA_VERSION = 2
SUPPORTED_PACK_SCHEMA_VERSIONS = frozenset({1, 2})

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_TYPES = frozenset(
    {
        "builtin",
        "local",
        "configured",
        "git",
        "local_archive",
        "companion_growth",
    }
)
_RUNTIMES = frozenset({"deskpet-json-tool-v1", "mcp"})
_EXECUTION_PROFILES = frozenset({"brokered-effect-v1", "native-adapter"})
_KNOWN_PERMISSIONS = frozenset(
    {
        "filesystem_read",
        "filesystem_write",
        "process_execute",
        "network_access",
        "package_install",
        "application_control",
        "desktop_control",
        "capability_manage",
        # Legacy categories remain accepted by adapters during migration.
        "read_file",
        "read_file_sensitive",
        "write_file",
        "desktop_write",
        "shell",
        "network",
        "mcp_call",
        "skill_install",
    }
)
_KNOWN_EFFECTS = frozenset(
    {
        "read_only",
        "idempotent_read",
        "deterministic_reusable",
        "staged_file",
        "opaque_manual",
    }
)
_WRITE_PERMISSIONS = frozenset(
    {"filesystem_write", "write_file", "desktop_write"}
)
_OPAQUE_PERMISSIONS = frozenset(
    {
        "process_execute",
        "package_install",
        "application_control",
        "desktop_control",
        "capability_manage",
        "shell",
        "skill_install",
    }
)
CORE_RESERVED_TOOL_NAMES = frozenset(
    {
        "workflow_spawn",
        "capability_build",
        "capability_repair",
        "capability_search",
        "capability_describe",
        "capability_activate",
        "workspace_prepare",
    }
)


class PackManifestError(CapabilityContractError):
    """A stable, user-presentable package rejection."""


@dataclass(frozen=True, slots=True)
class PackSource:
    type: str
    uri: str
    revision: str


@dataclass(frozen=True, slots=True)
class PackCompatibility:
    deskpet: str
    os: tuple[str, ...]
    architectures: tuple[str, ...]
    python: str


@dataclass(frozen=True, slots=True)
class SkillEntry:
    id: str
    path: str
    allowed_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowEntry:
    id: str
    path: str
    interpreter_id: str
    interpreter_version: str


@dataclass(frozen=True, slots=True)
class ToolEntry:
    id: str
    provider_name: str
    runtime: str
    execution_profile: str
    input_views: tuple[str, ...]
    entry: str
    schema: str
    healthcheck: str


@dataclass(frozen=True, slots=True)
class McpServerEntry:
    id: str
    config_ref: str


@dataclass(frozen=True, slots=True)
class PythonDependency:
    requirement: str


@dataclass(frozen=True, slots=True)
class CommandDependency:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class PackDependencies:
    python: tuple[PythonDependency, ...]
    commands: tuple[CommandDependency, ...]


@dataclass(frozen=True, slots=True)
class PackFile:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class UninstallPolicy:
    stop_servers: bool
    remove_environment_when_unreferenced: bool


@dataclass(frozen=True, slots=True)
class PackManifest:
    schema_version: int
    id: str
    name: str
    version: str
    source: PackSource
    compatibility: PackCompatibility
    skills: tuple[SkillEntry, ...]
    workflows: tuple[WorkflowEntry, ...]
    tools: tuple[ToolEntry, ...]
    mcp_servers: tuple[McpServerEntry, ...]
    permissions: tuple[str, ...]
    effects: tuple[str, ...]
    dependencies: PackDependencies
    files: tuple[PackFile, ...]
    uninstall: UninstallPolicy
    manifest_hash: str
    raw: Mapping[str, JsonValue]

    @property
    def source_identity(self) -> str:
        return f"{self.source.type}:{self.source.uri}@{self.source.revision}"

    @property
    def schema_hash(self) -> str:
        return fingerprint_json(
            [
                {
                    "id": tool.id,
                    "provider_name": tool.provider_name,
                    "schema": tool.schema,
                }
                for tool in self.tools
            ]
        )

    def descriptor(
        self,
        *,
        health: str = "unknown",
    ) -> CapabilityVersionDescriptor:
        if (self.skills or self.workflows) and (self.tools or self.mcp_servers):
            kind = "pack"
        elif self.tools:
            kind = "function_tool"
        elif self.mcp_servers:
            kind = "mcp_tool"
        else:
            kind = "instruction"
        return CapabilityVersionDescriptor(
            capability_id=self.id,
            display_name=self.name,
            version=self.version,
            kind=kind,
            source=self.source_identity,
            description=self.name,
            aliases=tuple(skill.id for skill in self.skills),
            logical_tool_ids=tuple(tool.id for tool in self.tools),
            provider_tool_names=tuple(tool.provider_name for tool in self.tools),
            permission_categories=self.permissions,
            effect_kinds=self.effects,
            schema_hash=self.schema_hash,
            manifest_hash=self.manifest_hash,
            health=health,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class PackEnvironment:
    deskpet_version: str | None
    os: str
    architecture: str
    python_version: str

    @classmethod
    def current(cls, *, deskpet_version: str | None = None) -> "PackEnvironment":
        os_name = {
            "win32": "windows",
            "linux": "linux",
            "darwin": "macos",
        }.get(sys.platform, sys.platform)
        architecture = platform.machine().strip().lower().replace("amd64", "x86_64")
        return cls(
            deskpet_version=deskpet_version,
            os=os_name,
            architecture=architecture,
            python_version=".".join(str(value) for value in sys.version_info[:3]),
        )


@dataclass(frozen=True, slots=True)
class PackValidationResult:
    manifest: PackManifest
    root: Path
    descriptor: CapabilityVersionDescriptor
    file_hashes: Mapping[str, str]


def _mapping(
    value: object,
    *,
    name: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PackManifestError("invalid_manifest", f"{name} must be an object")
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(value))
    extra = sorted(set(value) - allowed)
    if missing:
        raise PackManifestError(
            "missing_field", f"{name} is missing: {', '.join(missing)}"
        )
    if extra:
        raise PackManifestError(
            "unknown_field", f"{name} has unknown fields: {', '.join(extra)}"
        )
    return dict(value)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PackManifestError("invalid_text", f"{name} must be a non-empty string")
    return unicodedata.normalize("NFC", value.strip())


def _string_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PackManifestError("invalid_list", f"{name} must be an array")
    result = tuple(_text(item, name) for item in value)
    if len(result) != len(set(result)):
        raise PackManifestError("duplicate_value", f"{name} contains duplicates")
    return result


def _objects(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise PackManifestError("invalid_list", f"{name} must be an array")
    return list(value)


def _relative_path(value: object, name: str) -> str:
    text = _text(value, name)
    if "\\" in text:
        raise PackManifestError(
            "noncanonical_path", f"{name} must use '/' separators"
        )
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
    ):
        raise PackManifestError(
            "path_traversal", f"{name} must be a canonical relative path"
        )
    canonical = path.as_posix()
    if canonical != text:
        raise PackManifestError(
            "noncanonical_path", f"{name} is not in canonical form"
        )
    return canonical


def _semver(value: object, name: str) -> str:
    text = _text(value, name)
    if not _SEMVER_RE.fullmatch(text):
        raise PackManifestError("invalid_version", f"{name} must be semantic version")
    return text


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise PackManifestError("invalid_boolean", f"{name} must be a boolean")
    return value


def parse_pack_manifest(
    value: object,
    *,
    reserved_tool_names: Iterable[str] = CORE_RESERVED_TOOL_NAMES,
) -> PackManifest:
    """Dispatch v1/v2 parsing from the explicit integer schema version."""

    if not isinstance(value, dict):
        raise PackManifestError("invalid_manifest", "manifest must be an object")
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise PackManifestError(
            "invalid_schema_version", "schema_version must be an integer"
        )
    if schema_version not in SUPPORTED_PACK_SCHEMA_VERSIONS:
        raise PackManifestError(
            "unsupported_schema",
            f"unsupported schema_version: {schema_version}",
        )

    raw = _mapping(
        value,
        name="manifest",
        required={
            "schema_version",
            "id",
            "name",
            "version",
            "source",
            "compatibility",
            "entries",
            "permissions",
            "effects",
            "dependencies",
            "files",
            "uninstall",
        },
    )
    pack_id = _text(raw["id"], "id")
    # Reuse the deterministic provider contract to validate the pack ID.
    try:
        provider_tool_name(pack_id, "probe")
    except CapabilityContractError as exc:
        raise PackManifestError(exc.code, str(exc)) from exc
    version = _semver(raw["version"], "version")

    source_raw = _mapping(
        raw["source"],
        name="source",
        required={"type", "uri", "revision"},
    )
    source_type = _text(source_raw["type"], "source.type")
    if source_type not in _SOURCE_TYPES:
        raise PackManifestError(
            "invalid_source", f"unsupported source type: {source_type}"
        )
    source = PackSource(
        type=source_type,
        uri=_text(source_raw["uri"], "source.uri"),
        revision=_text(source_raw["revision"], "source.revision"),
    )

    compatibility_raw = _mapping(
        raw["compatibility"],
        name="compatibility",
        required={"deskpet", "os", "architectures", "python"},
    )
    compatibility = PackCompatibility(
        deskpet=_text(compatibility_raw["deskpet"], "compatibility.deskpet"),
        os=_string_list(compatibility_raw["os"], "compatibility.os"),
        architectures=_string_list(
            compatibility_raw["architectures"], "compatibility.architectures"
        ),
        python=_text(compatibility_raw["python"], "compatibility.python"),
    )

    entries_raw = _mapping(
        raw["entries"],
        name="entries",
        required=set(),
        optional=(
            {"skills", "tools", "mcp_servers", "workflows"}
            if schema_version == 2
            else {"skills", "tools", "mcp_servers"}
        ),
    )
    skills: list[SkillEntry] = []
    skill_ids: set[str] = set()
    for index, item in enumerate(_objects(entries_raw.get("skills", []), "entries.skills")):
        required = {"id", "path", "allowed_tools"} if schema_version == 2 else {"path"}
        row = _mapping(item, name=f"entries.skills[{index}]", required=required)
        path = _relative_path(row["path"], f"entries.skills[{index}].path")
        skill_id = (
            _text(row["id"], f"entries.skills[{index}].id")
            if schema_version == 2
            else PurePosixPath(path).parent.name
        )
        if schema_version == 2 and not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?",
            skill_id,
        ):
            raise PackManifestError(
                "invalid_skill_id",
                f"entries.skills[{index}].id is not canonical",
            )
        if skill_id in skill_ids:
            raise PackManifestError(
                "duplicate_skill_id", f"duplicate Skill id: {skill_id}"
            )
        skill_ids.add(skill_id)
        allowed_tools = (
            _string_list(
                row["allowed_tools"],
                f"entries.skills[{index}].allowed_tools",
            )
            if schema_version == 2
            else ()
        )
        if allowed_tools != tuple(sorted(allowed_tools)):
            raise PackManifestError(
                "noncanonical_allowed_tools",
                f"entries.skills[{index}].allowed_tools must be sorted",
            )
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", item) for item in allowed_tools):
            raise PackManifestError(
                "invalid_tool_name",
                f"entries.skills[{index}].allowed_tools contains an invalid tool name",
            )
        skills.append(
            SkillEntry(id=skill_id, path=path, allowed_tools=allowed_tools)
        )

    workflows: list[WorkflowEntry] = []
    workflow_ids: set[str] = set()
    for index, item in enumerate(
        _objects(entries_raw.get("workflows", []), "entries.workflows")
    ):
        row = _mapping(
            item,
            name=f"entries.workflows[{index}]",
            required={"id", "path", "interpreter_id", "interpreter_version"},
        )
        workflow_id = _text(row["id"], f"entries.workflows[{index}].id")
        if workflow_id in workflow_ids:
            raise PackManifestError(
                "duplicate_workflow_id", f"duplicate Workflow id: {workflow_id}"
            )
        workflow_ids.add(workflow_id)
        workflows.append(
            WorkflowEntry(
                id=workflow_id,
                path=_relative_path(
                    row["path"], f"entries.workflows[{index}].path"
                ),
                interpreter_id=_text(
                    row["interpreter_id"],
                    f"entries.workflows[{index}].interpreter_id",
                ),
                interpreter_version=_text(
                    row["interpreter_version"],
                    f"entries.workflows[{index}].interpreter_version",
                ),
            )
        )

    reserved = set(reserved_tool_names)
    tools: list[ToolEntry] = []
    provider_names: set[str] = set()
    logical_ids: set[str] = set()
    for index, item in enumerate(_objects(entries_raw.get("tools", []), "entries.tools")):
        row = _mapping(
            item,
            name=f"entries.tools[{index}]",
            required={
                "id",
                "provider_name",
                "runtime",
                "execution_profile",
                "input_views",
                "entry",
                "schema",
                "healthcheck",
            },
        )
        logical_id = _text(row["id"], f"entries.tools[{index}].id")
        try:
            expected_name = provider_tool_name(pack_id, logical_id)
        except CapabilityContractError as exc:
            raise PackManifestError(exc.code, str(exc)) from exc
        actual_name = _text(
            row["provider_name"], f"entries.tools[{index}].provider_name"
        )
        if actual_name != expected_name:
            raise PackManifestError(
                "provider_name_mismatch",
                f"{actual_name!r} must equal deterministic name {expected_name!r}",
            )
        if actual_name in reserved:
            raise PackManifestError(
                "reserved_tool_name", f"{actual_name!r} is a core reserved name"
            )
        if logical_id in logical_ids or actual_name in provider_names:
            raise PackManifestError(
                "tool_name_collision", "logical or provider tool name collides"
            )
        logical_ids.add(logical_id)
        provider_names.add(actual_name)
        runtime = _text(row["runtime"], f"entries.tools[{index}].runtime")
        profile = _text(
            row["execution_profile"], f"entries.tools[{index}].execution_profile"
        )
        if runtime not in _RUNTIMES:
            raise PackManifestError("invalid_runtime", f"unsupported runtime: {runtime}")
        if profile not in _EXECUTION_PROFILES:
            raise PackManifestError(
                "invalid_execution_profile", f"unsupported execution profile: {profile}"
            )
        tools.append(
            ToolEntry(
                id=logical_id,
                provider_name=actual_name,
                runtime=runtime,
                execution_profile=profile,
                input_views=_string_list(
                    row["input_views"], f"entries.tools[{index}].input_views"
                ),
                entry=_relative_path(row["entry"], f"entries.tools[{index}].entry"),
                schema=_relative_path(
                    row["schema"], f"entries.tools[{index}].schema"
                ),
                healthcheck=_text(
                    row["healthcheck"], f"entries.tools[{index}].healthcheck"
                ),
            )
        )

    mcp_servers: list[McpServerEntry] = []
    mcp_ids: set[str] = set()
    for index, item in enumerate(
        _objects(entries_raw.get("mcp_servers", []), "entries.mcp_servers")
    ):
        row = _mapping(
            item,
            name=f"entries.mcp_servers[{index}]",
            required={"id", "config_ref"},
        )
        server_id = _text(row["id"], f"entries.mcp_servers[{index}].id")
        if server_id in mcp_ids:
            raise PackManifestError(
                "mcp_server_collision", f"duplicate MCP server id: {server_id}"
            )
        mcp_ids.add(server_id)
        mcp_servers.append(
            McpServerEntry(
                id=server_id,
                config_ref=_relative_path(
                    row["config_ref"], f"entries.mcp_servers[{index}].config_ref"
                ),
            )
        )

    if not (skills or workflows or tools or mcp_servers):
        raise PackManifestError(
            "empty_pack", "entries must declare at least one skill, tool or MCP server"
        )

    permissions = _string_list(raw["permissions"], "permissions")
    effects = _string_list(raw["effects"], "effects")
    unknown_permissions = sorted(set(permissions) - _KNOWN_PERMISSIONS)
    unknown_effects = sorted(set(effects) - _KNOWN_EFFECTS)
    if unknown_permissions:
        raise PackManifestError(
            "unknown_permission",
            f"unknown permissions: {', '.join(unknown_permissions)}",
        )
    if unknown_effects:
        raise PackManifestError(
            "unknown_effect", f"unknown effects: {', '.join(unknown_effects)}"
        )
    if set(permissions) & _WRITE_PERMISSIONS and not (
        {"staged_file", "opaque_manual"} & set(effects)
    ):
        raise PackManifestError(
            "permission_effect_closure",
            "filesystem write permissions require staged_file or opaque_manual effect",
        )
    if set(permissions) & _OPAQUE_PERMISSIONS and "opaque_manual" not in effects:
        raise PackManifestError(
            "permission_effect_closure",
            "process, package, application and desktop permissions require opaque_manual",
        )

    dependencies_raw = _mapping(
        raw["dependencies"],
        name="dependencies",
        required={"python", "commands"},
    )
    python_dependencies: list[PythonDependency] = []
    for index, item in enumerate(
        _objects(dependencies_raw["python"], "dependencies.python")
    ):
        if isinstance(item, str):
            requirement = _text(item, f"dependencies.python[{index}]")
        else:
            row = _mapping(
                item,
                name=f"dependencies.python[{index}]",
                required={"requirement"},
            )
            requirement = _text(
                row["requirement"], f"dependencies.python[{index}].requirement"
            )
        python_dependencies.append(PythonDependency(requirement=requirement))

    command_dependencies: list[CommandDependency] = []
    command_names: set[str] = set()
    for index, item in enumerate(
        _objects(dependencies_raw["commands"], "dependencies.commands")
    ):
        row = _mapping(
            item,
            name=f"dependencies.commands[{index}]",
            required={"name", "version"},
        )
        name = _text(row["name"], f"dependencies.commands[{index}].name")
        if name in command_names:
            raise PackManifestError(
                "dependency_collision", f"duplicate command dependency: {name}"
            )
        command_names.add(name)
        command_dependencies.append(
            CommandDependency(
                name=name,
                version=_text(
                    row["version"], f"dependencies.commands[{index}].version"
                ),
            )
        )

    files: list[PackFile] = []
    file_paths: set[str] = set()
    for index, item in enumerate(_objects(raw["files"], "files")):
        row = _mapping(
            item, name=f"files[{index}]", required={"path", "sha256"}
        )
        path = _relative_path(row["path"], f"files[{index}].path")
        digest = _text(row["sha256"], f"files[{index}].sha256")
        if not _SHA256_RE.fullmatch(digest):
            raise PackManifestError(
                "invalid_hash", f"files[{index}].sha256 must be lowercase SHA-256"
            )
        if path == PACK_MANIFEST_NAME:
            raise PackManifestError(
                "manifest_hash_cycle",
                f"{PACK_MANIFEST_NAME} must not appear in files",
            )
        if path in file_paths:
            raise PackManifestError("file_collision", f"duplicate file path: {path}")
        file_paths.add(path)
        files.append(PackFile(path=path, sha256=digest))

    referenced_paths = {
        *(skill.path for skill in skills),
        *(workflow.path for workflow in workflows),
        *(tool.entry for tool in tools),
        *(tool.schema for tool in tools),
        *(server.config_ref for server in mcp_servers),
    }
    undeclared = sorted(referenced_paths - file_paths)
    if undeclared:
        raise PackManifestError(
            "undeclared_entry_file",
            f"entry files missing from files: {', '.join(undeclared)}",
        )

    uninstall_raw = _mapping(
        raw["uninstall"],
        name="uninstall",
        required={"stop_servers", "remove_environment_when_unreferenced"},
    )
    uninstall = UninstallPolicy(
        stop_servers=_bool(uninstall_raw["stop_servers"], "uninstall.stop_servers"),
        remove_environment_when_unreferenced=_bool(
            uninstall_raw["remove_environment_when_unreferenced"],
            "uninstall.remove_environment_when_unreferenced",
        ),
    )

    frozen_raw = json.loads(canonical_json(raw))
    return PackManifest(
        schema_version=schema_version,
        id=pack_id,
        name=_text(raw["name"], "name"),
        version=version,
        source=source,
        compatibility=compatibility,
        skills=tuple(skills),
        workflows=tuple(workflows),
        tools=tuple(tools),
        mcp_servers=tuple(mcp_servers),
        permissions=permissions,
        effects=effects,
        dependencies=PackDependencies(
            python=tuple(python_dependencies),
            commands=tuple(command_dependencies),
        ),
        files=tuple(files),
        uninstall=uninstall,
        manifest_hash=hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest(),
        raw=frozen_raw,
    )


def _version_parts(value: str) -> tuple[int, int, int, tuple[tuple[int, object], ...]]:
    """A small deterministic SemVer/PEP440-compatible comparator.

    It intentionally supports the comparison operators used by pack v1.  A
    malformed value is rejected instead of silently treated as compatible.
    """

    normalized = value.strip().lower().replace("rc", "-rc.")
    match = re.match(
        r"^(\d+)\.(\d+)(?:\.(\d+))?(?:[-.]([0-9a-z.-]+))?(?:\+.*)?$",
        normalized,
    )
    if match is None:
        raise PackManifestError("invalid_version", f"invalid version: {value!r}")
    prerelease_text = match.group(4)
    prerelease: list[tuple[int, object]] = []
    if prerelease_text is not None:
        for token in prerelease_text.split("."):
            prerelease.append((0, int(token)) if token.isdigit() else (1, token))
    # A release sorts after its prereleases.
    marker: tuple[tuple[int, object], ...] = (
        tuple(prerelease) if prerelease else ((2, ""),)
    )
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
        marker,
    )


def _version_satisfies(version: str, expression: str) -> bool:
    current = _version_parts(version)
    for raw_clause in expression.split(","):
        clause = raw_clause.strip()
        match = re.fullmatch(r"(>=|<=|==|=|>|<)\s*(.+)", clause)
        if match is None:
            raise PackManifestError(
                "invalid_version_constraint",
                f"unsupported version constraint: {expression!r}",
            )
        operator, expected_text = match.groups()
        expected = _version_parts(expected_text)
        if operator in {"=", "=="} and current != expected:
            return False
        if operator == ">=" and current < expected:
            return False
        if operator == "<=" and current > expected:
            return False
        if operator == ">" and current <= expected:
            return False
        if operator == "<" and current >= expected:
            return False
    return True


def validate_compatibility(
    manifest: PackManifest,
    environment: PackEnvironment,
) -> None:
    if (
        environment.deskpet_version is not None
        and not _version_satisfies(
            environment.deskpet_version, manifest.compatibility.deskpet
        )
    ):
        raise PackManifestError(
            "incompatible_deskpet",
            f"pack requires DeskPet {manifest.compatibility.deskpet}",
        )
    if environment.os not in manifest.compatibility.os:
        raise PackManifestError(
            "incompatible_os",
            f"pack supports {manifest.compatibility.os}, not {environment.os}",
        )
    architecture = environment.architecture.lower().replace("amd64", "x86_64")
    supported = {
        item.lower().replace("amd64", "x86_64")
        for item in manifest.compatibility.architectures
    }
    if architecture not in supported:
        raise PackManifestError(
            "incompatible_architecture",
            f"pack supports {sorted(supported)}, not {architecture}",
        )
    if not _version_satisfies(
        environment.python_version, manifest.compatibility.python
    ):
        raise PackManifestError(
            "incompatible_python",
            f"pack requires Python {manifest.compatibility.python}",
        )


def _resolve_pack_path(root: Path, relative: str) -> Path:
    candidate = (root / PurePosixPath(relative)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PackManifestError(
            "path_traversal", f"pack path escapes root: {relative}"
        ) from exc
    return candidate


def windows_extended_path(path: Path) -> Path:
    """Return a long-path-safe Windows view without changing stored identity."""

    if os.name != "nt":
        return path
    raw = str(path)
    if raw.startswith("\\\\?\\"):
        return path
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + raw[2:])
    return Path("\\\\?\\" + raw)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_mcp_config(path: Path, declared_ids: set[str]) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackManifestError(
            "invalid_mcp_config", f"cannot parse MCP config {path.name}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise PackManifestError("invalid_mcp_config", "MCP config must be an object")
    referenced: set[str] = set()
    if isinstance(value.get("mcpServers"), dict):
        referenced.update(str(key) for key in value["mcpServers"])
    servers = value.get("servers")
    if isinstance(servers, list):
        for item in servers:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                referenced.add(item["id"])
            elif isinstance(item, dict) and isinstance(item.get("name"), str):
                referenced.add(item["name"])
    unknown = sorted(referenced - declared_ids)
    if unknown:
        raise PackManifestError(
            "undeclared_mcp_server",
            f"MCP config references undeclared servers: {', '.join(unknown)}",
        )


def _validate_v2_skill_frontmatter(root: Path, entry: SkillEntry) -> None:
    path = _resolve_pack_path(root, entry.path)
    try:
        lines = path.read_text(encoding="utf-8").lstrip("\ufeff").splitlines()
    except (OSError, UnicodeError) as exc:
        raise PackManifestError(
            "invalid_skill", f"cannot read skill {entry.path}: {exc}"
        ) from exc
    if not lines or lines[0].strip() != "---":
        raise PackManifestError(
            "invalid_skill_frontmatter", f"{entry.path} has no frontmatter"
        )
    try:
        end = next(
            index for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
        frontmatter = yaml.safe_load("\n".join(lines[1:end])) or {}
    except (StopIteration, yaml.YAMLError) as exc:
        raise PackManifestError(
            "invalid_skill_frontmatter", f"{entry.path} frontmatter is invalid"
        ) from exc
    if not isinstance(frontmatter, dict) or frontmatter.get("name") != entry.id:
        raise PackManifestError(
            "skill_id_mismatch",
            f"{entry.path} name must equal manifest skill id {entry.id!r}",
        )
    allowed = frontmatter.get("allowed-tools")
    if not isinstance(allowed, list) or any(
        not isinstance(item, str) for item in allowed
    ):
        raise PackManifestError(
            "skill_allowed_tools_missing",
            f"{entry.path} must declare allowed-tools as a list",
        )
    if tuple(allowed) != entry.allowed_tools:
        raise PackManifestError(
            "skill_allowed_tools_mismatch",
            f"{entry.path} allowed-tools differ from the manifest",
        )


def load_and_validate_pack(
    pack_root: str | Path,
    *,
    environment: PackEnvironment | None = None,
    reserved_tool_names: Iterable[str] = CORE_RESERVED_TOOL_NAMES,
) -> PackValidationResult:
    """Run the fixed v1 validation sequence against a staged pack."""

    root = Path(pack_root).expanduser().resolve(strict=False)
    io_root = windows_extended_path(root).resolve(strict=True)
    if not io_root.is_dir():
        raise PackManifestError("invalid_pack_root", "pack root must be a directory")
    manifest_path = io_root / PACK_MANIFEST_NAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackManifestError(
            "manifest_missing", f"{PACK_MANIFEST_NAME} is missing"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackManifestError(
            "manifest_invalid", f"cannot parse {PACK_MANIFEST_NAME}: {exc}"
        ) from exc

    manifest = parse_pack_manifest(raw, reserved_tool_names=reserved_tool_names)
    validate_compatibility(manifest, environment or PackEnvironment.current())

    declared = {item.path: item.sha256 for item in manifest.files}
    actual_files: set[str] = set()
    for candidate in sorted(io_root.rglob("*")):
        if candidate.is_symlink():
            raise PackManifestError(
                "symlink_rejected",
                f"pack may not contain symlinks: {candidate.relative_to(io_root).as_posix()}",
            )
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(io_root).as_posix()
        if relative == PACK_MANIFEST_NAME:
            continue
        relative_parts = PurePosixPath(relative).parts
        if "__pycache__" in relative_parts or candidate.suffix in {".pyc", ".pyo"}:
            # Import/test caches are checkout-local transport noise. Source
            # adapters drop them before installation; they are never pack
            # content and cannot satisfy a declared manifest file.
            continue
        actual_files.add(relative)

    missing = sorted(set(declared) - actual_files)
    extra = sorted(actual_files - set(declared))
    if missing:
        raise PackManifestError(
            "pack_file_missing", f"declared files are missing: {', '.join(missing)}"
        )
    if extra:
        raise PackManifestError(
            "pack_file_unlisted", f"unlisted files are present: {', '.join(extra)}"
        )

    actual_hashes: dict[str, str] = {}
    for relative, expected_hash in sorted(declared.items()):
        path = _resolve_pack_path(io_root, relative)
        if not path.is_file():
            raise PackManifestError("pack_file_missing", f"missing file: {relative}")
        actual_hash = _hash_file(path)
        if actual_hash != expected_hash:
            raise PackManifestError(
                "hash_mismatch",
                f"SHA-256 mismatch for {relative}: expected {expected_hash}, got {actual_hash}",
            )
        actual_hashes[relative] = actual_hash

    for tool in manifest.tools:
        schema_path = _resolve_pack_path(io_root, tool.schema)
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PackManifestError(
                "invalid_tool_schema",
                f"cannot parse tool schema {tool.schema}: {exc}",
            ) from exc
        if not isinstance(schema, dict):
            raise PackManifestError(
                "invalid_tool_schema", f"{tool.schema} must contain a JSON object"
            )

    declared_mcp_ids = {server.id for server in manifest.mcp_servers}
    for config_ref in sorted({server.config_ref for server in manifest.mcp_servers}):
        _validate_mcp_config(_resolve_pack_path(io_root, config_ref), declared_mcp_ids)
    if manifest.schema_version == 2:
        for skill in manifest.skills:
            _validate_v2_skill_frontmatter(io_root, skill)

    return PackValidationResult(
        manifest=manifest,
        root=root,
        descriptor=manifest.descriptor(health="healthy"),
        file_hashes=actual_hashes,
    )


__all__ = [
    "CORE_RESERVED_TOOL_NAMES",
    "CommandDependency",
    "McpServerEntry",
    "PACK_MANIFEST_NAME",
    "PACK_SCHEMA_VERSION",
    "PackCompatibility",
    "PackDependencies",
    "PackEnvironment",
    "PackFile",
    "PackManifest",
    "PackManifestError",
    "PackSource",
    "PackValidationResult",
    "PythonDependency",
    "SkillEntry",
    "SUPPORTED_PACK_SCHEMA_VERSIONS",
    "ToolEntry",
    "WorkflowEntry",
    "UninstallPolicy",
    "load_and_validate_pack",
    "parse_pack_manifest",
    "validate_compatibility",
    "windows_extended_path",
]
