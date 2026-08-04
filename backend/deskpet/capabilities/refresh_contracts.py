# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable contracts for same-run capability catalog refresh."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .contracts import CatalogStamp, JsonValue, canonical_json, fingerprint_json

CapabilityAction = Literal[
    "activate",
    "install",
    "update",
    "build",
    "repair",
    "rollback",
    "uninstall",
]
RefreshSourceKind = Literal["tool_effect", "child_terminal"]
RefreshStatus = Literal["pending", "committed", "failed"]

_ACTIONS = {
    "activate",
    "install",
    "update",
    "build",
    "repair",
    "rollback",
    "uninstall",
}
_SOURCE_KINDS = {"tool_effect", "child_terminal"}
_STATUSES = {"pending", "committed", "failed"}


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is required")
    return value


def _sha256(value: object, field_name: str) -> str:
    text = _required_text(value, field_name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return text


def _text_tuple(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    result = tuple(_required_text(item, field_name) for item in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} cannot contain duplicates")
    return result


def _stamp_from_dict(value: Mapping[str, Any]) -> CatalogStamp:
    return CatalogStamp(
        catalog_generation=int(value["catalog_generation"]),
        registry_revision=int(value["registry_revision"]),
        binding_generation=int(value["binding_generation"]),
        skill_revision=int(value["skill_revision"]),
        mcp_revision=int(value["mcp_revision"]),
        fingerprint=str(value["fingerprint"]),
    )


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class CapabilityOperationReceipt:
    operation_id: str
    root_run_id: str
    parent_command_id: str
    parent_effect_id: str
    action: CapabilityAction
    refresh_nonce: str
    old_stamp: CatalogStamp
    published_binding_generation: int
    affected_capability_ids: tuple[str, ...]
    affected_version_refs: tuple[str, ...]
    affected_tool_spec_fingerprints: tuple[str, ...]
    manifest_hashes: tuple[str, ...]
    status: Literal["published"] = "published"
    operation_receipt_hash: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "operation_id",
            "root_run_id",
            "parent_command_id",
            "parent_effect_id",
            "refresh_nonce",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.action not in _ACTIONS:
            raise ValueError(f"unknown capability action: {self.action}")
        if self.status != "published":
            raise ValueError("operation receipt status must be published")
        if (
            not isinstance(self.published_binding_generation, int)
            or self.published_binding_generation <= 0
        ):
            raise ValueError("published_binding_generation must be positive")
        for field_name in (
            "affected_capability_ids",
            "affected_version_refs",
        ):
            object.__setattr__(
                self,
                field_name,
                _text_tuple(getattr(self, field_name), field_name),
            )
        for field_name in (
            "affected_tool_spec_fingerprints",
            "manifest_hashes",
        ):
            values = tuple(
                _sha256(item, field_name) for item in getattr(self, field_name)
            )
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} cannot contain duplicates")
            object.__setattr__(self, field_name, values)
        expected = fingerprint_json(self._payload())
        if self.operation_receipt_hash and (
            _sha256(
                self.operation_receipt_hash, "operation_receipt_hash"
            )
            != expected
        ):
            raise ValueError("operation receipt hash does not match its payload")
        object.__setattr__(self, "operation_receipt_hash", expected)

    def _payload(self) -> dict[str, JsonValue]:
        return {
            "operation_id": self.operation_id,
            "root_run_id": self.root_run_id,
            "parent_command_id": self.parent_command_id,
            "parent_effect_id": self.parent_effect_id,
            "action": self.action,
            "status": self.status,
            "refresh_nonce": self.refresh_nonce,
            "old_stamp": self.old_stamp.to_dict(),
            "published_binding_generation": self.published_binding_generation,
            "affected_capability_ids": list(self.affected_capability_ids),
            "affected_version_refs": list(self.affected_version_refs),
            "affected_tool_spec_fingerprints": list(
                self.affected_tool_spec_fingerprints
            ),
            "manifest_hashes": list(self.manifest_hashes),
        }

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            **self._payload(),
            "operation_receipt_hash": self.operation_receipt_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CapabilityOperationReceipt":
        return cls(
            operation_id=str(value["operation_id"]),
            root_run_id=str(value["root_run_id"]),
            parent_command_id=str(value["parent_command_id"]),
            parent_effect_id=str(value["parent_effect_id"]),
            action=str(value["action"]),  # type: ignore[arg-type]
            status=str(value["status"]),  # type: ignore[arg-type]
            refresh_nonce=str(value["refresh_nonce"]),
            operation_receipt_hash=str(value["operation_receipt_hash"]),
            old_stamp=_stamp_from_dict(value["old_stamp"]),
            published_binding_generation=int(
                value["published_binding_generation"]
            ),
            affected_capability_ids=tuple(
                str(item) for item in value["affected_capability_ids"]
            ),
            affected_version_refs=tuple(
                str(item) for item in value["affected_version_refs"]
            ),
            affected_tool_spec_fingerprints=tuple(
                str(item) for item in value["affected_tool_spec_fingerprints"]
            ),
            manifest_hashes=tuple(str(item) for item in value["manifest_hashes"]),
        )


@dataclass(frozen=True, slots=True)
class CapabilityRefreshIntent:
    intent_id: str
    root_run_id: str
    run_id: str
    operation_id: str
    source_kind: RefreshSourceKind
    source_command_id: str
    source_effect_id: str
    refresh_nonce: str
    expected_continuation_version: int
    old_stamp: CatalogStamp
    old_catalog_snapshot_ref: str
    old_tool_set_snapshot_ref: str
    exposure_intent_ref: str
    status: RefreshStatus = "pending"
    commit_hash: str | None = None
    commit: "CapabilityRefreshCommit | None" = None

    def __post_init__(self) -> None:
        for field_name in (
            "intent_id",
            "root_run_id",
            "run_id",
            "operation_id",
            "source_command_id",
            "source_effect_id",
            "refresh_nonce",
            "exposure_intent_ref",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if self.source_kind not in _SOURCE_KINDS:
            raise ValueError(f"unknown refresh source: {self.source_kind}")
        if self.status not in _STATUSES:
            raise ValueError(f"unknown refresh status: {self.status}")
        if (
            not isinstance(self.expected_continuation_version, int)
            or self.expected_continuation_version < 0
        ):
            raise ValueError(
                "expected_continuation_version must be non-negative"
            )
        object.__setattr__(
            self,
            "old_catalog_snapshot_ref",
            _sha256(self.old_catalog_snapshot_ref, "old_catalog_snapshot_ref"),
        )
        object.__setattr__(
            self,
            "old_tool_set_snapshot_ref",
            _sha256(self.old_tool_set_snapshot_ref, "old_tool_set_snapshot_ref"),
        )
        if self.commit_hash is not None:
            object.__setattr__(
                self, "commit_hash", _sha256(self.commit_hash, "commit_hash")
            )
        if self.status == "committed":
            if self.commit is None or self.commit_hash != self.commit.fingerprint:
                raise ValueError("committed refresh intent requires its exact commit")
        elif self.commit is not None or self.commit_hash is not None:
            raise ValueError("only a committed refresh intent may contain a commit")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "intent_id": self.intent_id,
            "root_run_id": self.root_run_id,
            "run_id": self.run_id,
            "operation_id": self.operation_id,
            "source_kind": self.source_kind,
            "source_command_id": self.source_command_id,
            "source_effect_id": self.source_effect_id,
            "refresh_nonce": self.refresh_nonce,
            "expected_continuation_version": self.expected_continuation_version,
            "old_stamp": self.old_stamp.to_dict(),
            "old_catalog_snapshot_ref": self.old_catalog_snapshot_ref,
            "old_tool_set_snapshot_ref": self.old_tool_set_snapshot_ref,
            "exposure_intent_ref": self.exposure_intent_ref,
            "status": self.status,
            "commit_hash": self.commit_hash,
            "commit": None if self.commit is None else self.commit.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CapabilityRefreshCommit:
    intent_id: str
    root_run_id: str
    run_id: str
    operation_id: str
    refresh_nonce: str
    expected_continuation_version: int
    old_stamp: CatalogStamp
    new_stamp: CatalogStamp
    old_catalog_snapshot_ref: str
    new_catalog_snapshot_ref: str
    old_tool_set_snapshot_ref: str
    new_tool_set_snapshot_ref: str
    new_context_os: Mapping[str, JsonValue]
    affected_tool_spec_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "intent_id",
            "root_run_id",
            "run_id",
            "operation_id",
            "refresh_nonce",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        if (
            not isinstance(self.expected_continuation_version, int)
            or self.expected_continuation_version < 0
        ):
            raise ValueError(
                "expected_continuation_version must be non-negative"
            )
        if self.new_stamp.fingerprint == self.old_stamp.fingerprint:
            raise ValueError("refresh commit must advance the catalog stamp")
        for field_name in (
            "old_catalog_snapshot_ref",
            "new_catalog_snapshot_ref",
            "old_tool_set_snapshot_ref",
            "new_tool_set_snapshot_ref",
        ):
            object.__setattr__(
                self, field_name, _sha256(getattr(self, field_name), field_name)
            )
        fingerprints = tuple(
            _sha256(item, "affected_tool_spec_fingerprints")
            for item in self.affected_tool_spec_fingerprints
        )
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("affected_tool_spec_fingerprints cannot contain duplicates")
        object.__setattr__(
            self, "affected_tool_spec_fingerprints", fingerprints
        )
        # Canonical round-trip validates JSON safety and detaches caller state.
        context = json.loads(canonical_json(_thaw_json(self.new_context_os)))
        object.__setattr__(self, "new_context_os", _freeze_json(context))

    @property
    def fingerprint(self) -> str:
        return fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "intent_id": self.intent_id,
            "root_run_id": self.root_run_id,
            "run_id": self.run_id,
            "operation_id": self.operation_id,
            "refresh_nonce": self.refresh_nonce,
            "expected_continuation_version": self.expected_continuation_version,
            "old_stamp": self.old_stamp.to_dict(),
            "new_stamp": self.new_stamp.to_dict(),
            "old_catalog_snapshot_ref": self.old_catalog_snapshot_ref,
            "new_catalog_snapshot_ref": self.new_catalog_snapshot_ref,
            "old_tool_set_snapshot_ref": self.old_tool_set_snapshot_ref,
            "new_tool_set_snapshot_ref": self.new_tool_set_snapshot_ref,
            "new_context_os": _thaw_json(self.new_context_os),
            "affected_tool_spec_fingerprints": list(
                self.affected_tool_spec_fingerprints
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CapabilityRefreshCommit":
        return cls(
            intent_id=str(value["intent_id"]),
            root_run_id=str(value["root_run_id"]),
            run_id=str(value["run_id"]),
            operation_id=str(value["operation_id"]),
            refresh_nonce=str(value["refresh_nonce"]),
            expected_continuation_version=int(
                value["expected_continuation_version"]
            ),
            old_stamp=_stamp_from_dict(value["old_stamp"]),
            new_stamp=_stamp_from_dict(value["new_stamp"]),
            old_catalog_snapshot_ref=str(value["old_catalog_snapshot_ref"]),
            new_catalog_snapshot_ref=str(value["new_catalog_snapshot_ref"]),
            old_tool_set_snapshot_ref=str(value["old_tool_set_snapshot_ref"]),
            new_tool_set_snapshot_ref=str(value["new_tool_set_snapshot_ref"]),
            new_context_os=dict(value["new_context_os"]),
            affected_tool_spec_fingerprints=tuple(
                str(item) for item in value["affected_tool_spec_fingerprints"]
            ),
        )


def refresh_intent_from_dict(
    value: Mapping[str, Any],
) -> CapabilityRefreshIntent:
    commit_raw = value.get("commit")
    return CapabilityRefreshIntent(
        intent_id=str(value["intent_id"]),
        root_run_id=str(value["root_run_id"]),
        run_id=str(value["run_id"]),
        operation_id=str(value["operation_id"]),
        source_kind=str(value["source_kind"]),  # type: ignore[arg-type]
        source_command_id=str(value["source_command_id"]),
        source_effect_id=str(value["source_effect_id"]),
        refresh_nonce=str(value["refresh_nonce"]),
        expected_continuation_version=int(
            value["expected_continuation_version"]
        ),
        old_stamp=_stamp_from_dict(value["old_stamp"]),
        old_catalog_snapshot_ref=str(value["old_catalog_snapshot_ref"]),
        old_tool_set_snapshot_ref=str(value["old_tool_set_snapshot_ref"]),
        exposure_intent_ref=str(value["exposure_intent_ref"]),
        status=str(value["status"]),  # type: ignore[arg-type]
        commit_hash=(
            None if value.get("commit_hash") is None else str(value["commit_hash"])
        ),
        commit=(
            CapabilityRefreshCommit.from_dict(commit_raw)
            if isinstance(commit_raw, Mapping)
            else None
        ),
    )


__all__ = [
    "CapabilityAction",
    "CapabilityOperationReceipt",
    "CapabilityRefreshCommit",
    "CapabilityRefreshIntent",
    "RefreshSourceKind",
    "RefreshStatus",
    "refresh_intent_from_dict",
]
