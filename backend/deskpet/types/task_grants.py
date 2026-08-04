# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Task-level resource grants and exact-call grant derivation.

The existing registry still validates an exact authorization immediately
before execution.  A ``TaskGrant`` only answers whether the runtime may mint
that exact authorization without asking the user again.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence
from urllib.parse import urlsplit

ResourceKind = Literal[
    "filesystem",
    "network_origin",
    "process_executable",
    "package_source",
    "application",
    "desktop_target",
    "capability_managed_root",
    "system_change",
]
GrantSource = Literal["user", "policy:auto"]
AuthorizationMode = Literal["manual", "auto"]

_RESOURCE_KINDS = frozenset(
    {
        "filesystem",
        "network_origin",
        "process_executable",
        "package_source",
        "application",
        "desktop_target",
        "capability_managed_root",
        "system_change",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TaskGrantError(ValueError):
    """Stable authorization-domain failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskGrantScopeError(TaskGrantError):
    """The requested exact call exceeds a task grant."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskGrantError("invalid_text", f"{name} must be a non-empty string")
    return unicodedata.normalize("NFC", value.strip())


def _hash(value: object, name: str) -> str:
    text = _text(value, name)
    if not _SHA256_RE.fullmatch(text):
        raise TaskGrantError(
            "invalid_fingerprint", f"{name} must be a lowercase SHA-256 digest"
        )
    return text


def canonical_filesystem_path(value: str | Path) -> str:
    resolved = Path(value).expanduser().resolve(strict=False)
    canonical = unicodedata.normalize("NFC", str(resolved))
    return os.path.normcase(canonical)


def canonical_network_origin(value: str) -> str:
    parsed = urlsplit(_text(value, "network origin"))
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise TaskGrantError(
            "invalid_network_origin",
            "network origin requires http(s) or ws(s) scheme and a host",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TaskGrantError(
            "invalid_network_origin",
            "network origin cannot contain credentials, query or fragment",
        )
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise TaskGrantError("invalid_network_origin", "network port is invalid") from exc
    default_port = {"http": 80, "https": 443, "ws": 80, "wss": 443}[scheme]
    suffix = "" if port in {None, default_port} else f":{port}"
    return f"{scheme}://{host}{suffix}"


def canonical_resource_value(kind: ResourceKind, value: str | Path) -> str:
    if kind not in _RESOURCE_KINDS:
        raise TaskGrantError("invalid_resource_kind", f"unknown resource kind: {kind}")
    if kind in {"filesystem", "capability_managed_root"}:
        return canonical_filesystem_path(value)
    text = _text(str(value), "canonical_value")
    if text == "*":
        return "*"
    if kind == "network_origin":
        return canonical_network_origin(text)
    if kind in {"process_executable", "package_source"}:
        if "://" in text and kind == "package_source":
            return canonical_network_origin(text)
        if any(separator in text for separator in ("/", "\\")) or Path(text).is_absolute():
            return canonical_filesystem_path(text)
        return text.casefold()
    return unicodedata.normalize("NFKC", text).casefold()


@dataclass(frozen=True, slots=True)
class ResourceSelector:
    kind: ResourceKind
    canonical_value: str
    access: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind not in _RESOURCE_KINDS:
            raise TaskGrantError(
                "invalid_resource_kind", f"unknown resource kind: {self.kind}"
            )
        canonical = canonical_resource_value(self.kind, self.canonical_value)
        object.__setattr__(self, "canonical_value", canonical)
        access = tuple(_text(item, "access").casefold() for item in self.access)
        if not access:
            raise TaskGrantError("missing_access", "resource selector access is required")
        if len(access) != len(set(access)):
            raise TaskGrantError("duplicate_access", "selector access cannot repeat")
        object.__setattr__(self, "access", tuple(sorted(access)))

    @classmethod
    def filesystem(
        cls, root: str | Path, *access: str
    ) -> "ResourceSelector":
        return cls("filesystem", canonical_filesystem_path(root), tuple(access))

    @classmethod
    def network(cls, origin: str, *access: str) -> "ResourceSelector":
        return cls("network_origin", canonical_network_origin(origin), tuple(access))

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "canonical_value": self.canonical_value,
            "access": list(self.access),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ResourceSelector":
        if not isinstance(value, dict) or set(value) != {
            "kind",
            "canonical_value",
            "access",
        }:
            raise TaskGrantError(
                "invalid_selector", "resource selector shape is invalid"
            )
        access = value["access"]
        if not isinstance(access, list):
            raise TaskGrantError("invalid_selector", "selector access must be an array")
        return cls(
            kind=str(value["kind"]),  # type: ignore[arg-type]
            canonical_value=str(value["canonical_value"]),
            access=tuple(str(item) for item in access),
        )


def selector_covers(granted: ResourceSelector, requested: ResourceSelector) -> bool:
    if granted.kind != requested.kind:
        return False
    if not set(requested.access).issubset(granted.access):
        return False
    if granted.canonical_value == "*":
        return granted.kind not in {"filesystem", "capability_managed_root"}
    if granted.kind in {"filesystem", "capability_managed_root"}:
        root = Path(granted.canonical_value)
        candidate = Path(requested.canonical_value)
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return candidate == root
    return granted.canonical_value == requested.canonical_value


@dataclass(frozen=True, slots=True)
class TaskGrant:
    task_grant_id: str
    root_run_id: str
    principal_id: str
    resource_selectors: tuple[ResourceSelector, ...]
    permission_categories: tuple[str, ...]
    effect_kinds: tuple[str, ...]
    source: GrantSource
    policy_generation: int
    expires_at: float | None
    version: int

    def __post_init__(self) -> None:
        for field_name in ("task_grant_id", "root_run_id", "principal_id"):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        selectors = tuple(self.resource_selectors)
        selector_keys = {
            (selector.kind, selector.canonical_value, selector.access)
            for selector in selectors
        }
        if len(selectors) != len(selector_keys):
            raise TaskGrantError(
                "duplicate_selector", "resource selectors cannot repeat"
            )
        object.__setattr__(self, "resource_selectors", selectors)
        for field_name in ("permission_categories", "effect_kinds"):
            values = tuple(
                _text(item, field_name) for item in getattr(self, field_name)
            )
            if len(values) != len(set(values)):
                raise TaskGrantError(
                    "duplicate_category", f"{field_name} cannot repeat"
                )
            object.__setattr__(self, field_name, tuple(sorted(values)))
        if self.source not in {"user", "policy:auto"}:
            raise TaskGrantError("invalid_source", f"unknown grant source: {self.source}")
        if not isinstance(self.policy_generation, int) or self.policy_generation < 0:
            raise TaskGrantError(
                "invalid_generation", "policy_generation must be non-negative"
            )
        if self.expires_at is not None and not math.isfinite(float(self.expires_at)):
            raise TaskGrantError("invalid_expiry", "expires_at must be finite")
        if not isinstance(self.version, int) or self.version < 1:
            raise TaskGrantError("invalid_version", "TaskGrant version must be positive")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "task_grant_id": self.task_grant_id,
            "root_run_id": self.root_run_id,
            "principal_id": self.principal_id,
            "resource_selectors": [
                selector.to_dict() for selector in self.resource_selectors
            ],
            "permission_categories": list(self.permission_categories),
            "effect_kinds": list(self.effect_kinds),
            "source": self.source,
            "policy_generation": self.policy_generation,
            "expires_at": self.expires_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, value: object) -> "TaskGrant":
        required = {
            "task_grant_id",
            "root_run_id",
            "principal_id",
            "resource_selectors",
            "permission_categories",
            "effect_kinds",
            "source",
            "policy_generation",
            "expires_at",
            "version",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise TaskGrantError("invalid_task_grant", "TaskGrant shape is invalid")
        selectors = value["resource_selectors"]
        permissions = value["permission_categories"]
        effects = value["effect_kinds"]
        if (
            not isinstance(selectors, list)
            or not isinstance(permissions, list)
            or not isinstance(effects, list)
        ):
            raise TaskGrantError(
                "invalid_task_grant", "TaskGrant collections must be arrays"
            )
        return cls(
            task_grant_id=str(value["task_grant_id"]),
            root_run_id=str(value["root_run_id"]),
            principal_id=str(value["principal_id"]),
            resource_selectors=tuple(
                ResourceSelector.from_dict(item) for item in selectors
            ),
            permission_categories=tuple(str(item) for item in permissions),
            effect_kinds=tuple(str(item) for item in effects),
            source=str(value["source"]),  # type: ignore[arg-type]
            policy_generation=int(value["policy_generation"]),
            expires_at=(
                None if value["expires_at"] is None else float(value["expires_at"])
            ),
            version=int(value["version"]),
        )

    def is_current(
        self,
        *,
        now: float,
        policy_mode: Literal["manual", "auto"],
        policy_generation: int,
    ) -> bool:
        if self.expires_at is not None and now >= self.expires_at:
            return False
        if self.source == "policy:auto":
            return (
                policy_mode == "auto"
                and self.policy_generation == policy_generation
            )
        return True

    def covers(
        self,
        *,
        root_run_id: str,
        resources: Sequence[ResourceSelector],
        permission_categories: Sequence[str],
        effect_kinds: Sequence[str],
    ) -> bool:
        if self.root_run_id != root_run_id:
            return False
        if not set(permission_categories).issubset(self.permission_categories):
            return False
        if not set(effect_kinds).issubset(self.effect_kinds):
            return False
        return all(
            any(selector_covers(granted, requested) for granted in self.resource_selectors)
            for requested in resources
        )

    def expanded(
        self,
        *,
        task_grant_id: str,
        resources: Sequence[ResourceSelector] = (),
        permission_categories: Sequence[str] = (),
        effect_kinds: Sequence[str] = (),
        policy_generation: int | None = None,
        expires_at: float | None = None,
    ) -> "TaskGrant":
        selectors = {
            (item.kind, item.canonical_value, item.access): item
            for item in (*self.resource_selectors, *resources)
        }
        return TaskGrant(
            task_grant_id=task_grant_id,
            root_run_id=self.root_run_id,
            principal_id=self.principal_id,
            resource_selectors=tuple(
                selectors[key] for key in sorted(selectors, key=str)
            ),
            permission_categories=tuple(
                sorted(set(self.permission_categories) | set(permission_categories))
            ),
            effect_kinds=tuple(sorted(set(self.effect_kinds) | set(effect_kinds))),
            source=self.source,
            policy_generation=(
                self.policy_generation
                if policy_generation is None
                else policy_generation
            ),
            expires_at=self.expires_at if expires_at is None else expires_at,
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class ExactGrantRequest:
    root_run_id: str
    run_id: str
    call_id: str
    effect_id: str
    tool_name: str
    args_hash: str
    capability_hash: str
    schema_hash: str
    scope_hash: str
    resource_selectors: tuple[ResourceSelector, ...]
    permission_categories: tuple[str, ...]
    effect_kinds: tuple[str, ...]
    expires_at: float

    def __post_init__(self) -> None:
        for field_name in (
            "root_run_id",
            "run_id",
            "call_id",
            "effect_id",
            "tool_name",
        ):
            object.__setattr__(
                self, field_name, _text(getattr(self, field_name), field_name)
            )
        for field_name in (
            "args_hash",
            "capability_hash",
            "schema_hash",
            "scope_hash",
        ):
            object.__setattr__(
                self, field_name, _hash(getattr(self, field_name), field_name)
            )
        object.__setattr__(self, "resource_selectors", tuple(self.resource_selectors))
        object.__setattr__(
            self,
            "permission_categories",
            tuple(sorted(set(self.permission_categories))),
        )
        object.__setattr__(
            self, "effect_kinds", tuple(sorted(set(self.effect_kinds)))
        )
        if not math.isfinite(float(self.expires_at)):
            raise TaskGrantError("invalid_expiry", "exact grant expiry must be finite")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "root_run_id": self.root_run_id,
                "run_id": self.run_id,
                "call_id": self.call_id,
                "effect_id": self.effect_id,
                "tool_name": self.tool_name,
                "args_hash": self.args_hash,
                "capability_hash": self.capability_hash,
                "schema_hash": self.schema_hash,
                "scope_hash": self.scope_hash,
                "resource_selectors": [
                    item.to_dict() for item in self.resource_selectors
                ],
                "permission_categories": list(self.permission_categories),
                "effect_kinds": list(self.effect_kinds),
                "expires_at": self.expires_at,
            }
        )


@dataclass(frozen=True, slots=True)
class DerivedAuthorizationGrant:
    grant_id: str
    task_grant_id: str
    task_grant_version: int
    source: GrantSource
    policy_generation: int
    root_run_id: str
    run_id: str
    call_id: str
    effect_id: str
    tool_name: str
    args_hash: str
    capability_hash: str
    schema_hash: str
    scope_hash: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class PreparedAuthorizationCommit:
    """Fenced input for the execution UoW's permission-decision transaction."""

    task_grant: TaskGrant
    exact_request: ExactGrantRequest
    expected_policy_mode: AuthorizationMode
    expected_policy_generation: int
    proposed_task_grant: bool
    authorization_origin: Literal["policy", "explicit_decision"] = "policy"
    decision_id: str | None = None
    decision_nonce: str | None = None
    confirm_only_snapshot_ref: str | None = None
    confirm_only_snapshot_hash: str | None = None

    def __post_init__(self) -> None:
        if self.expected_policy_mode not in {"manual", "auto"}:
            raise TaskGrantError(
                "invalid_policy_mode", "expected policy mode is invalid"
            )
        if (
            not isinstance(self.expected_policy_generation, int)
            or self.expected_policy_generation < 0
        ):
            raise TaskGrantError(
                "invalid_generation",
                "expected policy generation must be non-negative",
            )
        if not isinstance(self.proposed_task_grant, bool):
            raise TaskGrantError(
                "invalid_commit",
                "proposed_task_grant must be a boolean",
            )
        if self.authorization_origin not in {"policy", "explicit_decision"}:
            raise TaskGrantError(
                "invalid_authorization_origin",
                "authorization origin must be policy or explicit_decision",
            )
        explicit_fields = (
            self.decision_id,
            self.decision_nonce,
            self.confirm_only_snapshot_ref,
            self.confirm_only_snapshot_hash,
        )
        if self.authorization_origin == "explicit_decision":
            if any(not isinstance(value, str) or not value.strip() for value in explicit_fields):
                raise TaskGrantError(
                    "invalid_explicit_authorization",
                    "explicit authorization requires decision and frozen snapshot fences",
                )
            if self.task_grant.source != "user":
                raise TaskGrantError(
                    "invalid_explicit_authorization",
                    "explicit authorization must use a user TaskGrant",
                )
        elif any(value is not None for value in explicit_fields):
            raise TaskGrantError(
                "invalid_policy_authorization",
                "policy authorization cannot carry explicit-decision fences",
            )
        if self.task_grant.root_run_id != self.exact_request.root_run_id:
            raise TaskGrantError(
                "run_root_mismatch",
                "TaskGrant and exact request refer to different roots",
            )

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "task_grant": self.task_grant.to_dict(),
                "exact_request_fingerprint": self.exact_request.fingerprint,
                "expected_policy_mode": self.expected_policy_mode,
                "expected_policy_generation": self.expected_policy_generation,
                "proposed_task_grant": self.proposed_task_grant,
                "authorization_origin": self.authorization_origin,
                "decision_id": self.decision_id,
                "decision_nonce": self.decision_nonce,
                "confirm_only_snapshot_ref": self.confirm_only_snapshot_ref,
                "confirm_only_snapshot_hash": self.confirm_only_snapshot_hash,
            }
        )


def derive_exact_grant(
    task_grant: TaskGrant,
    request: ExactGrantRequest,
    *,
    actual_root_run_id: str,
    policy_mode: Literal["manual", "auto"],
    policy_generation: int,
    now: float,
) -> DerivedAuthorizationGrant:
    """Derive an exact grant or fail closed with a stable error code."""

    if request.root_run_id != actual_root_run_id:
        raise TaskGrantScopeError(
            "run_root_mismatch", "current run does not belong to requested root"
        )
    if not task_grant.is_current(
        now=now,
        policy_mode=policy_mode,
        policy_generation=policy_generation,
    ):
        raise TaskGrantScopeError("stale_task_grant", "TaskGrant is expired or stale")
    if not task_grant.covers(
        root_run_id=actual_root_run_id,
        resources=request.resource_selectors,
        permission_categories=request.permission_categories,
        effect_kinds=request.effect_kinds,
    ):
        raise TaskGrantScopeError(
            "scope_expansion_required",
            "prepared call exceeds the current TaskGrant",
        )
    if request.expires_at <= now:
        raise TaskGrantScopeError("expired_exact_grant", "exact grant is already expired")
    grant_id = _fingerprint(
        {
            "task_grant_id": task_grant.task_grant_id,
            "task_grant_version": task_grant.version,
            "request_fingerprint": request.fingerprint,
            "policy_generation": policy_generation,
        }
    )
    return DerivedAuthorizationGrant(
        grant_id=grant_id,
        task_grant_id=task_grant.task_grant_id,
        task_grant_version=task_grant.version,
        source=task_grant.source,
        policy_generation=policy_generation,
        root_run_id=request.root_run_id,
        run_id=request.run_id,
        call_id=request.call_id,
        effect_id=request.effect_id,
        tool_name=request.tool_name,
        args_hash=request.args_hash,
        capability_hash=request.capability_hash,
        schema_hash=request.schema_hash,
        scope_hash=request.scope_hash,
        expires_at=request.expires_at,
    )


__all__ = [
    "AuthorizationMode",
    "DerivedAuthorizationGrant",
    "ExactGrantRequest",
    "GrantSource",
    "PreparedAuthorizationCommit",
    "ResourceKind",
    "ResourceSelector",
    "TaskGrant",
    "TaskGrantError",
    "TaskGrantScopeError",
    "canonical_filesystem_path",
    "canonical_network_origin",
    "canonical_resource_value",
    "derive_exact_grant",
    "selector_covers",
]
