# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Immutable contracts shared by the capability catalog domain.

The catalog is a projection, never an execution authority.  Executability is
grounded by a :class:`RegistryCatalogSnapshot`, while versions and bindings are
grounded by ``CapabilityStore``.  Keeping the wire-shaped contracts here lets
the production composition inject those authorities without coupling the
domain to ``main.py`` or the process-wide registry singleton.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence, TypeAlias

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]

CapabilityKind = Literal["instruction", "function_tool", "mcp_tool", "pack"]
CapabilityHealth = Literal["unknown", "healthy", "degraded", "failed"]
CapabilityScopeKind = Literal["builtin", "run", "project", "user"]
ManagementPolicy = Literal["host_managed", "user_managed", "legacy_import"]
CatalogEntryKind = Literal["pack", "host_tool", "host_instruction"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LOGICAL_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$")
_PROVIDER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SCOPE_PRECEDENCE: tuple[CapabilityScopeKind, ...] = (
    "run",
    "project",
    "user",
    "builtin",
)
LEGACY_LOCAL_OWNER_KEY = "companion:legacy_local_profile:1"


class CapabilityContractError(ValueError):
    """Raised when an immutable capability contract is malformed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CapabilityCollisionError(CapabilityContractError):
    """Raised when a deterministic catalog choice cannot be made safely."""


class CatalogUnstableError(RuntimeError):
    """Raised after every bounded snapshot attempt observes revision drift."""


class PendingPublishError(RuntimeError):
    """Raised when an unreconciled publish intent makes projection unsafe."""


def canonical_json(value: object) -> str:
    """Return the canonical JSON representation used by all fingerprints."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


EMPTY_OWNER_BINDING_SET_STAMP = fingerprint_json(
    {"domain": "owner-binding-set-v1", "entries": []}
)
EMPTY_RECEIPT_SET_HASH = fingerprint_json(
    {"domain": "manager-receipt-set-v1", "receipts": []}
)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CapabilityContractError(
            "invalid_text", f"{field_name} must be a non-empty string"
        )
    return unicodedata.normalize("NFC", value.strip())


def _logical_id(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _LOGICAL_ID_RE.fullmatch(text):
        raise CapabilityContractError(
            "invalid_logical_id",
            f"{field_name} must use only ASCII letters, digits, '_' or '-'",
        )
    return text


def _sha256(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _SHA256_RE.fullmatch(text):
        raise CapabilityContractError(
            "invalid_fingerprint",
            f"{field_name} must be a lowercase SHA-256 digest",
        )
    return text


def _unique_text_tuple(values: Sequence[object], field_name: str) -> tuple[str, ...]:
    result = tuple(_required_text(value, field_name) for value in values)
    if len(result) != len(set(result)):
        raise CapabilityContractError(
            "duplicate_value", f"{field_name} cannot contain duplicates"
        )
    return result


def provider_tool_name(pack_id: str, logical_tool_id: str) -> str:
    """Return the only valid provider-facing name for a pack tool.

    Names are deliberately rejected instead of truncated: truncation makes two
    distinct logical IDs collide and would turn registration order into an
    execution decision.
    """

    pack = _logical_id(pack_id, "pack_id")
    tool = _logical_id(logical_tool_id, "logical_tool_id")
    name = f"{pack}__{tool}"
    if len(name) > 64 or not _PROVIDER_NAME_RE.fullmatch(name):
        raise CapabilityContractError(
            "invalid_provider_name",
            "provider tool name must be <=64 ASCII letters, digits, '_' or '-'",
        )
    return name


def canonical_project_scope_key(project_root: str | Path) -> str:
    """Return the legacy path-only key for diagnostics and recovery.

    New Runs must use :func:`canonical_project_identity_scope_key`.  A path is
    mutable and can be reused by another Project, so this key is deliberately
    never produced by ``CapabilityScope.for_run`` as an authoritative binding.
    """

    resolved = Path(project_root).expanduser().resolve(strict=False)
    canonical = unicodedata.normalize("NFC", str(resolved))
    # Windows roots are case-insensitive.  ``Path`` may be a WindowsPath only
    # on Windows, so use drive presence as the platform-independent signal.
    if resolved.drive:
        canonical = canonical.casefold()
    return f"project:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def canonical_project_identity_scope_key(
    project_id: str,
    project_revision: int,
    project_identity: str,
) -> str:
    """Return the opaque v2 binding key for one frozen Project identity."""

    normalized_id = _required_text(project_id, "project_id")
    normalized_identity = _required_text(project_identity, "project_identity")
    if (
        not isinstance(project_revision, int)
        or isinstance(project_revision, bool)
        or project_revision < 1
    ):
        raise CapabilityContractError(
            "invalid_project_revision",
            "project_revision must be a positive integer",
        )
    digest = fingerprint_json(
        {
            "domain": "project-capability-scope-v2",
            "project_id": normalized_id,
            "project_identity": normalized_identity,
            "project_revision": project_revision,
        }
    )
    return f"project:v2:{digest}"


def canonical_global_owner_key(identity_namespace_hash: str) -> str:
    """Derive the opaque user-global Capability owner from local identity."""

    seed = _sha256(identity_namespace_hash, "identity_namespace_hash")
    digest = fingerprint_json(
        {
            "domain": "simple-harness-global-capability-owner-v2",
            "identity_namespace_hash": seed,
        }
    )
    return f"user:v2:{digest}"


@dataclass(frozen=True, slots=True)
class CatalogStamp:
    catalog_generation: int
    registry_revision: int
    binding_generation: int
    skill_revision: int
    mcp_revision: int
    fingerprint: str = ""

    def __post_init__(self) -> None:
        values = {
            "catalog_generation": self.catalog_generation,
            "registry_revision": self.registry_revision,
            "binding_generation": self.binding_generation,
            "skill_revision": self.skill_revision,
            "mcp_revision": self.mcp_revision,
        }
        if any(not isinstance(value, int) or value < 0 for value in values.values()):
            raise CapabilityContractError(
                "invalid_revision", "catalog revisions must be non-negative integers"
            )
        expected = fingerprint_json(values)
        if self.fingerprint:
            actual = _sha256(self.fingerprint, "fingerprint")
            if actual != expected:
                raise CapabilityContractError(
                    "stamp_fingerprint_mismatch",
                    "CatalogStamp fingerprint does not match its revisions",
                )
        object.__setattr__(self, "fingerprint", expected)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "catalog_generation": self.catalog_generation,
            "registry_revision": self.registry_revision,
            "binding_generation": self.binding_generation,
            "skill_revision": self.skill_revision,
            "mcp_revision": self.mcp_revision,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class CapabilityVersionDescriptor:
    capability_id: str
    display_name: str
    version: str
    kind: CapabilityKind
    source: str
    description: str
    aliases: tuple[str, ...]
    logical_tool_ids: tuple[str, ...]
    provider_tool_names: tuple[str, ...]
    permission_categories: tuple[str, ...]
    effect_kinds: tuple[str, ...]
    schema_hash: str
    manifest_hash: str
    health: CapabilityHealth = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capability_id", _logical_id(self.capability_id, "capability_id")
        )
        for field_name in ("display_name", "version", "source", "description"):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.kind not in {"instruction", "function_tool", "mcp_tool", "pack"}:
            raise CapabilityContractError("invalid_kind", f"unknown kind: {self.kind}")
        if self.health not in {"unknown", "healthy", "degraded", "failed"}:
            raise CapabilityContractError(
                "invalid_health", f"unknown health: {self.health}"
            )
        for field_name in (
            "aliases",
            "logical_tool_ids",
            "provider_tool_names",
            "permission_categories",
            "effect_kinds",
        ):
            object.__setattr__(
                self,
                field_name,
                _unique_text_tuple(getattr(self, field_name), field_name),
            )
        for logical_id in self.logical_tool_ids:
            _logical_id(logical_id, "logical_tool_ids")
        for provider_name in self.provider_tool_names:
            if not _PROVIDER_NAME_RE.fullmatch(provider_name):
                raise CapabilityContractError(
                    "invalid_provider_name",
                    "provider tool names must be <=64 ASCII letters, digits, '_' or '-'",
                )
        if len(self.logical_tool_ids) != len(self.provider_tool_names):
            raise CapabilityContractError(
                "tool_mapping_mismatch",
                "logical_tool_ids and provider_tool_names must have equal length",
            )
        object.__setattr__(self, "schema_hash", _sha256(self.schema_hash, "schema_hash"))
        object.__setattr__(
            self, "manifest_hash", _sha256(self.manifest_hash, "manifest_hash")
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "capability_id": self.capability_id,
            "display_name": self.display_name,
            "version": self.version,
            "kind": self.kind,
            "source": self.source,
            "description": self.description,
            "aliases": list(self.aliases),
            "logical_tool_ids": list(self.logical_tool_ids),
            "provider_tool_names": list(self.provider_tool_names),
            "permission_categories": list(self.permission_categories),
            "effect_kinds": list(self.effect_kinds),
            "schema_hash": self.schema_hash,
            "manifest_hash": self.manifest_hash,
            "health": self.health,
        }


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    binding_id: str
    capability_id: str
    version: str
    manifest_hash: str
    scope: CapabilityScopeKind
    scope_key: str
    active: bool
    generation: int
    owner_key: str = ""
    management_policy: ManagementPolicy = "legacy_import"
    management_generation: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _required_text(self.binding_id, "binding_id"))
        object.__setattr__(
            self, "capability_id", _logical_id(self.capability_id, "capability_id")
        )
        object.__setattr__(self, "version", _required_text(self.version, "version"))
        object.__setattr__(
            self, "manifest_hash", _sha256(self.manifest_hash, "manifest_hash")
        )
        if self.scope not in _SCOPE_PRECEDENCE:
            raise CapabilityContractError("invalid_scope", f"unknown scope: {self.scope}")
        object.__setattr__(self, "scope_key", _required_text(self.scope_key, "scope_key"))
        if not isinstance(self.active, bool):
            raise CapabilityContractError("invalid_binding", "active must be a boolean")
        if not isinstance(self.generation, int) or self.generation < 0:
            raise CapabilityContractError(
                "invalid_generation", "binding generation must be non-negative"
            )
        owner_key = self.owner_key or (
            "builtin" if self.scope == "builtin" else LEGACY_LOCAL_OWNER_KEY
        )
        object.__setattr__(self, "owner_key", _required_text(owner_key, "owner_key"))
        if self.management_policy not in {
            "host_managed",
            "user_managed",
            "legacy_import",
        }:
            raise CapabilityContractError(
                "invalid_management_policy",
                "unknown binding management policy",
            )
        if (
            not isinstance(self.management_generation, int)
            or self.management_generation < 0
        ):
            raise CapabilityContractError(
                "invalid_management_generation",
                "management generation must be non-negative",
            )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "binding_id": self.binding_id,
            "capability_id": self.capability_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "scope": self.scope,
            "scope_key": self.scope_key,
            "active": self.active,
            "generation": self.generation,
            "owner_key": self.owner_key,
            "management_policy": self.management_policy,
            "management_generation": self.management_generation,
        }


@dataclass(frozen=True, slots=True)
class CapabilityScope:
    """Canonical binding keys visible to one root run."""

    run_key: str | None = None
    project_key: str | None = None
    user_key: str = "default"
    builtin_key: str = "builtin"

    def __post_init__(self) -> None:
        for field_name in ("run_key", "project_key"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required_text(value, field_name))
        object.__setattr__(self, "user_key", _required_text(self.user_key, "user_key"))
        object.__setattr__(
            self, "builtin_key", _required_text(self.builtin_key, "builtin_key")
        )

    @classmethod
    def for_run(
        cls,
        root_run_id: str,
        *,
        project_root: str | Path | None = None,
        project_id: str | None = None,
        project_revision: int | None = None,
        project_identity: str | None = None,
        user_key: str = "default",
    ) -> "CapabilityScope":
        identity_values = (project_id, project_revision, project_identity)
        if any(value is not None for value in identity_values) and not all(
            value is not None for value in identity_values
        ):
            raise CapabilityContractError(
                "incomplete_project_identity",
                "project_id, project_revision and project_identity must be supplied together",
            )
        return cls(
            run_key=_required_text(root_run_id, "root_run_id"),
            project_key=(
                canonical_project_identity_scope_key(
                    str(project_id), int(project_revision), str(project_identity)
                )
                if project_id is not None
                else None
            ),
            user_key=user_key,
        )

    @property
    def canonical(self) -> str:
        return canonical_json(self.to_dict())

    def binding_keys(self) -> tuple[tuple[CapabilityScopeKind, str], ...]:
        keys: list[tuple[CapabilityScopeKind, str]] = []
        if self.run_key is not None:
            keys.append(("run", self.run_key))
        if self.project_key is not None:
            keys.append(("project", self.project_key))
        keys.extend((("user", self.user_key), ("builtin", self.builtin_key)))
        return tuple(keys)

    def key_for(self, scope: CapabilityScopeKind) -> str | None:
        return dict(self.binding_keys()).get(scope)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "run_key": self.run_key,
            "project_key": self.project_key,
            "user_key": self.user_key,
            "builtin_key": self.builtin_key,
        }


@dataclass(frozen=True, slots=True, order=True)
class OwnerScopeKey:
    owner_key: str
    scope: CapabilityScopeKind
    scope_key: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "owner_key", _required_text(self.owner_key, "owner_key")
        )
        if self.scope not in _SCOPE_PRECEDENCE:
            raise CapabilityContractError("invalid_scope", f"unknown scope: {self.scope}")
        object.__setattr__(
            self, "scope_key", _required_text(self.scope_key, "scope_key")
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "owner_key": self.owner_key,
            "scope": self.scope,
            "scope_key": self.scope_key,
        }


@dataclass(frozen=True, slots=True)
class OwnerBindingSetStamp:
    key: OwnerScopeKey
    bindings: tuple[CapabilityBinding, ...]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        bindings = tuple(
            sorted(
                self.bindings,
                key=lambda item: (
                    item.capability_id,
                    item.binding_id,
                    item.generation,
                ),
            )
        )
        if any(
            item.owner_key != self.key.owner_key
            or item.scope != self.key.scope
            or item.scope_key != self.key.scope_key
            for item in bindings
        ):
            raise CapabilityContractError(
                "owner_binding_scope_mismatch",
                "owner binding set contains a row from another owner scope",
            )
        if len({item.capability_id for item in bindings}) != len(bindings):
            raise CapabilityContractError(
                "duplicate_binding", "owner binding set contains duplicate packs"
            )
        object.__setattr__(self, "bindings", bindings)
        payload = {
            "domain": "owner-binding-set-v1",
            "key": self.key.to_dict(),
            "entries": [item.to_dict() for item in bindings],
        }
        expected = fingerprint_json(payload)
        if self.fingerprint and _sha256(self.fingerprint, "fingerprint") != expected:
            raise CapabilityContractError(
                "owner_binding_stamp_mismatch",
                "owner binding set fingerprint does not match its rows",
            )
        object.__setattr__(self, "fingerprint", expected)


@dataclass(frozen=True, slots=True)
class PlatformDetailToken:
    key: OwnerScopeKey
    exists: bool
    version: int
    owner_catalog_generation: int
    committed_owner_binding_set_stamp: str
    manager_receipt_set_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.exists, bool):
            raise CapabilityContractError("invalid_detail_token", "exists must be bool")
        for name in ("version", "owner_catalog_generation"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise CapabilityContractError(
                    "invalid_detail_token", f"{name} must be non-negative"
                )
        object.__setattr__(
            self,
            "committed_owner_binding_set_stamp",
            _sha256(
                self.committed_owner_binding_set_stamp,
                "committed_owner_binding_set_stamp",
            ),
        )
        object.__setattr__(
            self,
            "manager_receipt_set_hash",
            _sha256(self.manager_receipt_set_hash, "manager_receipt_set_hash"),
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            **self.key.to_dict(),
            "exists": self.exists,
            "version": self.version,
            "owner_catalog_generation": self.owner_catalog_generation,
            "committed_owner_binding_set_stamp": (
                self.committed_owner_binding_set_stamp
            ),
            "manager_receipt_set_hash": self.manager_receipt_set_hash,
        }


@dataclass(frozen=True, slots=True)
class PlatformDetailTokenVector:
    items: tuple[PlatformDetailToken, ...]

    def __post_init__(self) -> None:
        items = tuple(sorted(self.items, key=lambda item: item.key))
        if len({item.key for item in items}) != len(items):
            raise CapabilityContractError(
                "duplicate_detail_key", "detail token keys cannot repeat"
            )
        object.__setattr__(self, "items", items)

    @property
    def fingerprint(self) -> str:
        return fingerprint_json([item.to_dict() for item in self.items])


@dataclass(frozen=True, slots=True)
class PlatformDetailSnapshot:
    tokens: PlatformDetailTokenVector
    bindings: tuple[CapabilityBinding, ...]

    def __post_init__(self) -> None:
        allowed = {item.key for item in self.tokens.items}
        bindings = tuple(
            sorted(
                self.bindings,
                key=lambda item: (
                    item.owner_key,
                    item.scope,
                    item.scope_key,
                    item.capability_id,
                ),
            )
        )
        if any(
            OwnerScopeKey(item.owner_key, item.scope, item.scope_key) not in allowed
            for item in bindings
        ):
            raise CapabilityContractError(
                "detail_binding_scope_mismatch",
                "detail snapshot contains an unrequested owner binding",
            )
        object.__setattr__(self, "bindings", bindings)


@dataclass(frozen=True, slots=True)
class RunCatalogEntryIdentity:
    entry_kind: CatalogEntryKind
    descriptor_fingerprint: str
    canonical_envelope: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if self.entry_kind not in {"pack", "host_tool", "host_instruction"}:
            raise CapabilityContractError(
                "invalid_catalog_entry_kind", "unknown catalog entry kind"
            )
        object.__setattr__(
            self,
            "descriptor_fingerprint",
            _sha256(self.descriptor_fingerprint, "descriptor_fingerprint"),
        )
        envelope = json.loads(canonical_json(dict(self.canonical_envelope)))
        if not isinstance(envelope, dict):
            raise CapabilityContractError(
                "invalid_catalog_envelope", "catalog envelope must be an object"
            )
        object.__setattr__(
            self, "canonical_envelope", MappingProxyType(envelope)
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "entry_kind": self.entry_kind,
            "descriptor_fingerprint": self.descriptor_fingerprint,
            "canonical_envelope": dict(self.canonical_envelope),
        }


@dataclass(frozen=True, slots=True)
class RunCatalogContentStamp:
    request_scope: CapabilityScope
    entries: tuple[RunCatalogEntryIdentity, ...]
    fingerprint: str = ""

    def __post_init__(self) -> None:
        entries = tuple(
            sorted(self.entries, key=lambda item: item.descriptor_fingerprint)
        )
        if len({item.descriptor_fingerprint for item in entries}) != len(entries):
            raise CapabilityContractError(
                "duplicate_catalog_entry", "catalog entries cannot repeat"
            )
        object.__setattr__(self, "entries", entries)
        expected = fingerprint_json(
            {
                "domain": "run-catalog-content-v1",
                "request_scope": self.request_scope.to_dict(),
                "entries": [item.to_dict() for item in entries],
            }
        )
        if self.fingerprint and _sha256(self.fingerprint, "fingerprint") != expected:
            raise CapabilityContractError(
                "run_catalog_stamp_mismatch",
                "run catalog content fingerprint does not match",
            )
        object.__setattr__(self, "fingerprint", expected)

    @property
    def request_scope_hash(self) -> str:
        return fingerprint_json(self.request_scope.to_dict())

    @property
    def entry_set_hash(self) -> str:
        return fingerprint_json([item.to_dict() for item in self.entries])

    @property
    def snapshot_ref(self) -> str:
        return fingerprint_json(
            {
                "domain": "run-catalog-snapshot-ref-v2",
                "request_scope_hash": self.request_scope_hash,
                "run_catalog_content_stamp": self.fingerprint,
                "descriptor_fingerprints": [
                    item.descriptor_fingerprint for item in self.entries
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class ProcessCatalogStamp:
    process_instance_id: str
    run_catalog_content_stamp: str
    catalog_generation: int
    registry_revision: int
    skill_revision: int
    mcp_revision: int
    fingerprint: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "process_instance_id",
            _required_text(self.process_instance_id, "process_instance_id"),
        )
        object.__setattr__(
            self,
            "run_catalog_content_stamp",
            _sha256(
                self.run_catalog_content_stamp, "run_catalog_content_stamp"
            ),
        )
        values = {
            "catalog_generation": self.catalog_generation,
            "registry_revision": self.registry_revision,
            "skill_revision": self.skill_revision,
            "mcp_revision": self.mcp_revision,
        }
        if any(not isinstance(value, int) or value < 0 for value in values.values()):
            raise CapabilityContractError(
                "invalid_revision", "process catalog revisions must be non-negative"
            )
        expected = fingerprint_json(
            {
                "process_instance_id": self.process_instance_id,
                "run_catalog_content_stamp": self.run_catalog_content_stamp,
                **values,
            }
        )
        if self.fingerprint and _sha256(self.fingerprint, "fingerprint") != expected:
            raise CapabilityContractError(
                "process_catalog_stamp_mismatch",
                "process catalog fingerprint does not match",
            )
        object.__setattr__(self, "fingerprint", expected)


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    version: CapabilityVersionDescriptor
    visible_bindings: tuple[CapabilityBinding, ...]
    executable: bool
    installed: bool
    tool_spec_fingerprints: tuple[str, ...]
    stamp: CatalogStamp

    def __post_init__(self) -> None:
        bindings = tuple(self.visible_bindings)
        for binding in bindings:
            if binding.capability_id != self.version.capability_id:
                raise CapabilityContractError(
                    "binding_capability_mismatch",
                    "visible binding belongs to another capability",
                )
        object.__setattr__(self, "visible_bindings", bindings)
        fingerprints = tuple(
            _sha256(value, "tool_spec_fingerprints")
            for value in self.tool_spec_fingerprints
        )
        if len(fingerprints) != len(set(fingerprints)):
            raise CapabilityContractError(
                "duplicate_fingerprint", "tool fingerprints cannot repeat"
            )
        object.__setattr__(self, "tool_spec_fingerprints", fingerprints)
        if not isinstance(self.executable, bool) or not isinstance(self.installed, bool):
            raise CapabilityContractError(
                "invalid_descriptor", "executable and installed must be booleans"
            )
        if self.executable and self.version.provider_tool_names and not fingerprints:
            raise CapabilityContractError(
                "unverified_executable",
                "executable tool capabilities require registry fingerprints",
            )
        if self.version.kind == "instruction" and self.executable:
            raise CapabilityContractError(
                "instruction_not_executable", "instruction capabilities cannot execute"
            )

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(
            {
                "version": self.version.to_dict(),
                "visible_bindings": [
                    binding.to_dict() for binding in self.visible_bindings
                ],
                "executable": self.executable,
                "installed": self.installed,
                "tool_spec_fingerprints": list(self.tool_spec_fingerprints),
                "stamp": self.stamp.to_dict(),
            }
        )


@dataclass(frozen=True, slots=True)
class CapabilityCatalogSnapshot:
    stamp: CatalogStamp
    scope: CapabilityScope
    descriptors: tuple[CapabilityDescriptor, ...]
    created_at: float
    descriptor_fingerprints: tuple[str, ...] = field(init=False)
    snapshot_ref: str = field(init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(float(self.created_at)):
            raise CapabilityContractError("invalid_timestamp", "created_at must be finite")
        descriptors = tuple(self.descriptors)
        if len({item.version.capability_id for item in descriptors}) != len(descriptors):
            raise CapabilityCollisionError(
                "duplicate_capability",
                "a catalog snapshot may contain only one selected version per capability",
            )
        if any(item.stamp != self.stamp for item in descriptors):
            raise CapabilityContractError(
                "mixed_catalog_stamp", "every descriptor must share the snapshot stamp"
            )
        descriptors = tuple(
            sorted(descriptors, key=lambda item: item.version.capability_id)
        )
        object.__setattr__(self, "descriptors", descriptors)
        fingerprints = tuple(item.fingerprint for item in descriptors)
        object.__setattr__(self, "descriptor_fingerprints", fingerprints)
        object.__setattr__(
            self,
            "snapshot_ref",
            fingerprint_json(
                {
                    "stamp": self.stamp.to_dict(),
                    "scope": self.scope.to_dict(),
                    "descriptor_fingerprints": list(fingerprints),
                }
            ),
        )

    def get(self, capability_id: str) -> CapabilityDescriptor | None:
        return next(
            (
                item
                for item in self.descriptors
                if item.version.capability_id == capability_id
            ),
            None,
        )


@dataclass(frozen=True, slots=True)
class RegistryToolDescriptor:
    provider_name: str
    source: str
    description: str
    schema_hash: str
    permission_category: str
    effect_kind: str
    spec_version: str
    permission_policy_version: str = "v1"
    dangerous: bool = False
    effect_policy_version: str = ""
    dispatch_kind: str = "legacy_handler"
    concurrency_safe: bool = True
    completion_semantics: str = "sync"
    outcome_parser_id: str = ""
    outcome_parser_version: str = ""
    outcome_parser_hash: str = ""
    resource_scope_resolver_id: str = ""
    resource_scope_resolver_version: str = ""
    runtime_provenance_ref: str = ""
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not _PROVIDER_NAME_RE.fullmatch(self.provider_name):
            raise CapabilityContractError(
                "invalid_provider_name", f"invalid provider name: {self.provider_name}"
            )
        for field_name in (
            "source",
            "description",
            "permission_category",
            "effect_kind",
            "spec_version",
            "permission_policy_version",
            "dispatch_kind",
            "completion_semantics",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "schema_hash", _sha256(self.schema_hash, "schema_hash"))
        if bool(self.resource_scope_resolver_id) != bool(
            self.resource_scope_resolver_version
        ):
            raise CapabilityContractError(
                "invalid_resource_scope_resolver",
                "resource scope resolver id and version must be provided together",
            )
        if self.resource_scope_resolver_id:
            object.__setattr__(
                self,
                "resource_scope_resolver_id",
                _required_text(
                    self.resource_scope_resolver_id,
                    "resource_scope_resolver_id",
                ),
            )
            object.__setattr__(
                self,
                "resource_scope_resolver_version",
                _required_text(
                    self.resource_scope_resolver_version,
                    "resource_scope_resolver_version",
                ),
            )
        if self.runtime_provenance_ref:
            object.__setattr__(
                self,
                "runtime_provenance_ref",
                _sha256(self.runtime_provenance_ref, "runtime_provenance_ref"),
            )
        payload = {
            "provider_name": self.provider_name,
            "source": self.source,
            "description": self.description,
            "schema_hash": self.schema_hash,
            "permission_category": self.permission_category,
            "effect_kind": self.effect_kind,
            "spec_version": self.spec_version,
            "permission_policy_version": self.permission_policy_version,
            "dangerous": bool(self.dangerous),
            "effect_policy_version": self.effect_policy_version,
            "dispatch_kind": self.dispatch_kind,
            "concurrency_safe": bool(self.concurrency_safe),
            "completion_semantics": self.completion_semantics,
            "outcome_parser_id": self.outcome_parser_id,
            "outcome_parser_version": self.outcome_parser_version,
            "outcome_parser_hash": self.outcome_parser_hash,
        }
        if self.resource_scope_resolver_id:
            payload["resource_scope_resolver_id"] = self.resource_scope_resolver_id
            payload[
                "resource_scope_resolver_version"
            ] = self.resource_scope_resolver_version
        if self.runtime_provenance_ref:
            payload["runtime_provenance_ref"] = self.runtime_provenance_ref
        expected = fingerprint_json(payload)
        if self.fingerprint and _sha256(self.fingerprint, "fingerprint") != expected:
            raise CapabilityContractError(
                "tool_fingerprint_mismatch",
                "registry tool fingerprint does not match its immutable fields",
            )
        object.__setattr__(self, "fingerprint", expected)


@dataclass(frozen=True, slots=True)
class RegistryCatalogSnapshot:
    revision: int
    tools: tuple[RegistryToolDescriptor, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise CapabilityContractError(
                "invalid_revision", "registry revision must be non-negative"
            )
        tools = tuple(self.tools)
        if len({tool.provider_name for tool in tools}) != len(tools):
            raise CapabilityCollisionError(
                "registry_name_collision", "registry snapshot contains duplicate tool names"
            )
        object.__setattr__(
            self, "tools", tuple(sorted(tools, key=lambda item: item.provider_name))
        )

    @property
    def by_name(self) -> Mapping[str, RegistryToolDescriptor]:
        return MappingProxyType({tool.provider_name: tool for tool in self.tools})


@dataclass(frozen=True, slots=True)
class CapabilityCatalogEntry:
    """Authority-neutral input row consumed by ``CapabilityHub``."""

    version: CapabilityVersionDescriptor
    bindings: tuple[CapabilityBinding, ...]
    expected_tool_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "bindings", tuple(self.bindings))
        object.__setattr__(
            self,
            "expected_tool_fingerprints",
            tuple(
                _sha256(value, "expected_tool_fingerprints")
                for value in self.expected_tool_fingerprints
            ),
        )


@dataclass(frozen=True, slots=True)
class RevisionedCatalogEntries:
    revision: int
    entries: tuple[CapabilityCatalogEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise CapabilityContractError(
                "invalid_revision", "source revision must be non-negative"
            )
        object.__setattr__(self, "entries", tuple(self.entries))


@dataclass(frozen=True, slots=True)
class CapabilitySearchHit:
    capability_id: str
    version: str
    score: float
    match_kind: Literal["exact", "alias", "token", "semantic"]
    executable: bool
    descriptor_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capability_id", _logical_id(self.capability_id, "capability_id")
        )
        object.__setattr__(self, "version", _required_text(self.version, "version"))
        if not math.isfinite(float(self.score)):
            raise CapabilityContractError("invalid_score", "search score must be finite")
        if self.match_kind not in {"exact", "alias", "token", "semantic"}:
            raise CapabilityContractError(
                "invalid_match_kind", f"unknown match kind: {self.match_kind}"
            )
        object.__setattr__(
            self,
            "descriptor_fingerprint",
            _sha256(self.descriptor_fingerprint, "descriptor_fingerprint"),
        )


@dataclass(frozen=True, slots=True)
class CapabilitySearchReceipt:
    receipt_id: str
    root_run_id: str
    catalog_stamp_fingerprint: str
    query_hash: str
    hits: tuple[CapabilitySearchHit, ...]
    created_at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _sha256(self.receipt_id, "receipt_id"))
        object.__setattr__(
            self, "root_run_id", _required_text(self.root_run_id, "root_run_id")
        )
        object.__setattr__(
            self,
            "catalog_stamp_fingerprint",
            _sha256(self.catalog_stamp_fingerprint, "catalog_stamp_fingerprint"),
        )
        object.__setattr__(self, "query_hash", _sha256(self.query_hash, "query_hash"))
        object.__setattr__(self, "hits", tuple(self.hits))
        if not math.isfinite(float(self.created_at)):
            raise CapabilityContractError("invalid_timestamp", "created_at must be finite")

    @classmethod
    def create(
        cls,
        *,
        root_run_id: str,
        stamp: CatalogStamp,
        query: str,
        hits: Sequence[CapabilitySearchHit],
        created_at: float,
    ) -> "CapabilitySearchReceipt":
        normalized_query = unicodedata.normalize("NFKC", query).strip().casefold()
        query_hash = fingerprint_json({"query": normalized_query})
        receipt_id = fingerprint_json(
            {
                "root_run_id": root_run_id,
                "catalog_stamp_fingerprint": stamp.fingerprint,
                "query_hash": query_hash,
            }
        )
        return cls(
            receipt_id=receipt_id,
            root_run_id=root_run_id,
            catalog_stamp_fingerprint=stamp.fingerprint,
            query_hash=query_hash,
            hits=tuple(hits),
            created_at=created_at,
        )


SCOPE_PRECEDENCE = _SCOPE_PRECEDENCE

__all__ = [
    "CapabilityBinding",
    "CapabilityCatalogEntry",
    "CapabilityCatalogSnapshot",
    "CapabilityCollisionError",
    "CapabilityContractError",
    "CapabilityDescriptor",
    "CapabilityHealth",
    "CapabilityKind",
    "CatalogEntryKind",
    "CapabilityScope",
    "CapabilityScopeKind",
    "canonical_global_owner_key",
    "CapabilitySearchHit",
    "CapabilitySearchReceipt",
    "CapabilityVersionDescriptor",
    "CatalogStamp",
    "CatalogUnstableError",
    "EMPTY_OWNER_BINDING_SET_STAMP",
    "EMPTY_RECEIPT_SET_HASH",
    "JsonValue",
    "LEGACY_LOCAL_OWNER_KEY",
    "ManagementPolicy",
    "OwnerBindingSetStamp",
    "OwnerScopeKey",
    "PendingPublishError",
    "PlatformDetailSnapshot",
    "PlatformDetailToken",
    "PlatformDetailTokenVector",
    "ProcessCatalogStamp",
    "RegistryCatalogSnapshot",
    "RegistryToolDescriptor",
    "RevisionedCatalogEntries",
    "RunCatalogContentStamp",
    "RunCatalogEntryIdentity",
    "SCOPE_PRECEDENCE",
    "canonical_json",
    "canonical_project_scope_key",
    "canonical_project_identity_scope_key",
    "fingerprint_json",
    "provider_tool_name",
]
