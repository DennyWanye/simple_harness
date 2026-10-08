# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Checked-in execution identity and effect metadata for durable host tools.

The JSON files beside this module are the authority.  This module deliberately
does not inspect a Python callable, permission category, or tool description to
invent durable metadata.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping


class ManifestValidationError(ValueError):
    """A checked-in execution authority manifest is malformed or inconsistent."""


class EffectClass(str, Enum):
    READ_ONLY = "read_only"
    DRAFT_ONLY = "draft_only"
    REVERSIBLE_LOCAL = "reversible_local"
    EXTERNAL_SEND = "external_send"
    DESTRUCTIVE = "destructive"
    PAYMENT = "payment"
    CREDENTIAL = "credential"
    PRIVACY = "privacy"
    UNKNOWN = "unknown"


class IdempotencyClass(str, Enum):
    IDEMPOTENT = "idempotent"
    EFFECT_ID = "effect_id"
    NON_IDEMPOTENT = "non_idempotent"
    UNKNOWN = "unknown"


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256(value: str, field_name: str) -> str:
    normalized = str(value)
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ManifestValidationError(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return normalized


@dataclass(frozen=True)
class ToolEffectMetadata:
    effect_class: EffectClass
    idempotency: IdempotencyClass
    target_normalizer_version: str

    def __post_init__(self) -> None:
        if not str(self.target_normalizer_version).strip():
            raise ManifestValidationError("target_normalizer_version is required")

    @classmethod
    def unknown(cls) -> "ToolEffectMetadata":
        return cls(EffectClass.UNKNOWN, IdempotencyClass.UNKNOWN, "none")

    def fingerprint_payload(self) -> dict[str, str]:
        return {
            "effect_class": self.effect_class.value,
            "idempotency": self.idempotency.value,
            "target_normalizer_version": self.target_normalizer_version,
        }


@dataclass(frozen=True)
class ExecutionBuildIdentity:
    provider: str
    handler_id: str
    build_digest: str
    sources_manifest_hash: str
    artifacts: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.provider or not self.handler_id:
            raise ManifestValidationError("build identity provider and handler_id required")
        _sha256(self.build_digest, "build_digest")
        _sha256(self.sources_manifest_hash, "sources_manifest_hash")
        seen: set[str] = set()
        for path, digest in self.artifacts:
            if not path or path in seen:
                raise ManifestValidationError("build identity artifact paths must be unique")
            seen.add(path)
            _sha256(digest, f"artifact digest for {path}")

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.fingerprint_payload())

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "handler_id": self.handler_id,
            "build_digest": self.build_digest,
            "sources_manifest_hash": self.sources_manifest_hash,
            "artifacts": [
                {"path": path, "sha256": digest}
                for path, digest in self.artifacts
            ],
        }


@dataclass(frozen=True)
class CoreHandlerAuthority:
    handler_id: str
    tool_name: str
    authority_phase: str
    lifecycle: str
    effect: ToolEffectMetadata
    build: ExecutionBuildIdentity

    @property
    def planned(self) -> bool:
        return self.lifecycle == "planned"


_TOOLS_DIR = Path(__file__).resolve().parent
_SOURCES_PATH = _TOOLS_DIR / "execution_build_sources.json"
_BUILD_PATH = _TOOLS_DIR / "execution_build_manifest.json"
_EFFECT_PATH = _TOOLS_DIR / "tool_effect_policy_manifest.json"


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestValidationError(f"cannot load authority manifest {path.name}") from exc
    if not isinstance(value, Mapping):
        raise ManifestValidationError(f"{path.name} must contain a JSON object")
    return value


def _handler_rows(
    manifest: Mapping[str, Any], *, manifest_name: str
) -> dict[str, Mapping[str, Any]]:
    if int(manifest.get("schema_version", 0)) != 1:
        raise ManifestValidationError(f"{manifest_name} schema_version must be 1")
    raw = manifest.get("handlers")
    if not isinstance(raw, list):
        raise ManifestValidationError(f"{manifest_name}.handlers must be a list")
    rows: dict[str, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            raise ManifestValidationError(f"{manifest_name} handler row malformed")
        handler_id = str(item.get("handler_id") or "")
        if not handler_id or handler_id in rows:
            raise ManifestValidationError(
                f"{manifest_name} handler ids must be non-empty and unique"
            )
        rows[handler_id] = item
    return rows


@lru_cache(maxsize=1)
def load_core_handler_authorities() -> tuple[CoreHandlerAuthority, ...]:
    sources = _load_json(_SOURCES_PATH)
    build = _load_json(_BUILD_PATH)
    effects = _load_json(_EFFECT_PATH)
    source_rows = _handler_rows(sources, manifest_name=_SOURCES_PATH.name)
    build_rows = _handler_rows(build, manifest_name=_BUILD_PATH.name)
    effect_rows = _handler_rows(effects, manifest_name=_EFFECT_PATH.name)
    handler_sets = (set(source_rows), set(build_rows), set(effect_rows))
    if not (handler_sets[0] == handler_sets[1] == handler_sets[2]):
        raise ManifestValidationError(
            "execution source/effect/build handler sets must be identical"
        )
    expected_sources_hash = canonical_hash(sources)
    declared_sources_hash = _sha256(
        str(build.get("sources_manifest_hash") or ""), "sources_manifest_hash"
    )
    if declared_sources_hash != expected_sources_hash:
        raise ManifestValidationError("execution build manifest source hash mismatch")

    authorities: list[CoreHandlerAuthority] = []
    tool_names: set[str] = set()
    for handler_id in sorted(source_rows):
        source = source_rows[handler_id]
        built = build_rows[handler_id]
        effect = effect_rows[handler_id]
        tool_name = str(source.get("tool_name") or "")
        if not tool_name or tool_name in tool_names:
            raise ManifestValidationError("source manifest tool names must be unique")
        tool_names.add(tool_name)
        for row, name in (
            (built, "build"),
            (effect, "effect"),
        ):
            if str(row.get("tool_name") or "") != tool_name:
                raise ManifestValidationError(
                    f"{name} manifest tool mismatch for {handler_id}"
                )
        phase = str(source.get("authority_phase") or "")
        lifecycle = str(source.get("lifecycle") or "")
        if phase not in {"both", "legacy", "companion"}:
            raise ManifestValidationError(f"invalid authority phase for {handler_id}")
        if lifecycle not in {"active", "planned"}:
            raise ManifestValidationError(f"invalid lifecycle for {handler_id}")
        artifacts_raw = built.get("artifacts")
        if not isinstance(artifacts_raw, list) or not artifacts_raw:
            raise ManifestValidationError(f"build artifacts missing for {handler_id}")
        artifacts = tuple(
            (
                str(item["path"]),
                _sha256(str(item["sha256"]), f"artifact digest for {handler_id}"),
            )
            for item in artifacts_raw
            if isinstance(item, Mapping)
        )
        if len(artifacts) != len(artifacts_raw):
            raise ManifestValidationError(f"build artifacts malformed for {handler_id}")
        effect_metadata = ToolEffectMetadata(
            EffectClass(str(effect.get("effect_class") or "unknown")),
            IdempotencyClass(str(effect.get("idempotency") or "unknown")),
            str(effect.get("target_normalizer_version") or ""),
        )
        authorities.append(
            CoreHandlerAuthority(
                handler_id=handler_id,
                tool_name=tool_name,
                authority_phase=phase,
                lifecycle=lifecycle,
                effect=effect_metadata,
                build=ExecutionBuildIdentity(
                    provider="core_checked_manifest_v1",
                    handler_id=handler_id,
                    build_digest=_sha256(
                        str(built.get("build_digest") or ""),
                        f"build_digest for {handler_id}",
                    ),
                    sources_manifest_hash=declared_sources_hash,
                    artifacts=artifacts,
                ),
            )
        )
    return tuple(authorities)


@lru_cache(maxsize=1)
def _authority_by_tool_name() -> dict[str, CoreHandlerAuthority]:
    return {item.tool_name: item for item in load_core_handler_authorities()}


def core_authority_for_tool(tool_name: str) -> CoreHandlerAuthority | None:
    return _authority_by_tool_name().get(str(tool_name))


def authority_accepts_handler(
    authority: CoreHandlerAuthority, handler: object
) -> bool:
    """Verify that a callable originates in one of the hashed artifacts.

    The callable path is only an admission check.  It is never used as the
    durable identity itself.
    """

    if getattr(sys, "frozen", False):
        # 冻结包（PyInstaller）里没有仓库目录，处理器也多半只有字节码：按定义它的模块名对上
        # 清单里登记的 ``backend/<模块路径>.py``。字节身份由构建清单本身保证，这里只做准入
        # （2026-10-08 macOS 正式包：否则核心处理器全落空，成长功能切换失败）。
        module = inspect.getmodule(handler)
        name = str(getattr(module, "__name__", "") or "")
        if not name:
            return False
        stem = "backend/" + name.replace(".", "/")
        return bool({stem + ".py", stem + "/__init__.py"} & {path for path, _digest in authority.build.artifacts})
    try:
        source = Path(str(inspect.getsourcefile(handler) or "")).resolve(strict=True)
    except (OSError, TypeError, ValueError):
        return False
    repo_root = Path(__file__).resolve().parents[3]
    accepted = {
        (repo_root / Path(*PurePath(path).parts)).resolve(strict=True)
        for path, _digest in authority.build.artifacts
    }
    return source in accepted


def PurePath(value: str) -> Path:
    """Convert a manifest POSIX path without interpreting host separators."""

    from pathlib import PurePosixPath

    return Path(*PurePosixPath(value).parts)


def authority_handler_ids(
    *, phase: str = "legacy", include_planned: bool = True
) -> frozenset[str]:
    if phase not in {"legacy", "companion"}:
        raise ValueError("phase must be legacy or companion")
    return frozenset(
        item.handler_id
        for item in load_core_handler_authorities()
        if item.authority_phase in {"both", phase}
        and (include_planned or not item.planned)
    )


def validate_core_registry_handler_set(
    specs: Iterable[Any],
    *,
    phase: str = "legacy",
    include_planned: bool = False,
) -> None:
    expected = authority_handler_ids(phase=phase, include_planned=include_planned)
    authority_by_id = {
        item.handler_id: item
        for item in load_core_handler_authorities()
        if item.handler_id in expected
    }
    actual_by_id: dict[str, Any] = {}
    for spec in specs:
        if str(getattr(spec, "source", "")) != "builtin":
            continue
        handler_id = str(getattr(spec, "stable_handler_id", ""))
        if not handler_id:
            continue
        if handler_id in actual_by_id:
            raise ManifestValidationError(
                f"core registry handler id is duplicated: {handler_id}"
            )
        actual_by_id[handler_id] = spec
    actual = set(actual_by_id)
    if actual != expected:
        missing = sorted(expected - actual)
        unused = sorted(actual - expected)
        raise ManifestValidationError(
            f"core registry handler set mismatch: missing={missing!r} unused={unused!r}"
        )
    for handler_id, spec in actual_by_id.items():
        authority = authority_by_id[handler_id]
        build = getattr(spec, "execution_build_identity", None)
        if (
            str(getattr(spec, "name", "")) != authority.tool_name
            or getattr(spec, "effect_class", None) is not authority.effect.effect_class
            or getattr(spec, "idempotency", None) is not authority.effect.idempotency
            or str(getattr(spec, "target_normalizer_version", ""))
            != authority.effect.target_normalizer_version
            or build != authority.build
        ):
            raise ManifestValidationError(
                f"core registry authority metadata mismatch: {handler_id}"
            )


__all__ = [
    "CoreHandlerAuthority",
    "EffectClass",
    "ExecutionBuildIdentity",
    "IdempotencyClass",
    "ManifestValidationError",
    "ToolEffectMetadata",
    "authority_handler_ids",
    "authority_accepts_handler",
    "canonical_hash",
    "canonical_json_bytes",
    "core_authority_for_tool",
    "load_core_handler_authorities",
    "validate_core_registry_handler_set",
]
