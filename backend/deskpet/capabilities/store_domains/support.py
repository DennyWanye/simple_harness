# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from deskpet.permissions.policy import AuthorizationMode

from ..contracts import (
    LEGACY_LOCAL_OWNER_KEY,
    CapabilityBinding,
    CapabilityVersionDescriptor,
    JsonValue,
    OwnerScopeKey,
    PlatformDetailToken,
    canonical_json,
    fingerprint_json,
)

LegacyAuthorizationImportOutcome = Literal[
    "imported",
    "missing",
    "invalid",
]


class CapabilityStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CapabilityStoreConflict(CapabilityStoreError):
    pass


class CapabilitySchemaMissing(CapabilityStoreError):
    pass


@dataclass(frozen=True, slots=True)
class CapabilitySnapshotLeaseIntent:
    lease_intent_id: str
    lease_intent_hash: str
    snapshot_ref: str
    snapshot_ref_schema: str
    run_id: str
    root_run_id: str
    request_id: str
    turn_id: str
    lease_generation: int
    owner_operation_id: str
    lease_owner_kind: str
    owner_record_ref: str | None
    owner_record_hash: str | None
    start_fingerprint: str | None
    run_catalog_content_stamp: str
    entry_set_hash: str
    expected_entry_count: int
    status: str
    prepared_at: float
    bound_at: float | None
    released_at: float | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeProjectionReceipt:
    projection_receipt_id: str
    projection_receipt_hash: str
    purpose: str
    lease_intent_id: str | None
    run_catalog_content_stamp: str | None
    process_instance_id: str
    process_catalog_stamp: str | None
    prepared_tool_set_hash: str | None
    pin_token_hash: str | None
    status: str
    created_at: float
    ready_at: float | None
    retired_at: float | None


@dataclass(frozen=True, slots=True)
class PreparedRunCatalogProjection:
    intent: CapabilitySnapshotLeaseIntent
    projection_receipt: CapabilityRuntimeProjectionReceipt
    process_catalog_stamp: str
    status: str = "prepared"


@dataclass(frozen=True, slots=True)
class SnapshotLeaseAdoptionReceipt:
    intent_id: str
    intent_hash: str
    status: str
    owner_record_ref: str
    owner_record_hash: str
    start_fingerprint: str
    projection_receipt_id: str
    projection_receipt_hash: str


@dataclass(frozen=True, slots=True)
class CapabilitySnapshotLeaseReleaseReceipt:
    release_receipt_id: str
    release_receipt_hash: str
    lease_intent_id: str
    run_id: str
    snapshot_ref: str
    release_reason: str
    owner_terminal_or_transition_ref: str
    owner_terminal_or_transition_hash: str
    last_active_member_for_snapshot: bool
    cleanup_ticket_ref: str
    cleanup_ticket_hash: str
    cleanup_status: str
    status: str
    created_at: float
    cleaned_at: float | None
    last_error: str | None


def _validated_digest(value: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class CapabilityVersionStorage:
    pack_id: str
    version: str
    manifest_hash: str
    storage_key: str
    pack_storage_schema: str
    pack_root_hash: str
    environment_storage_schema: str
    environment_root_hash: str
    archive_hash: str
    created_at: float

    def __post_init__(self) -> None:
        if not self.pack_id or not self.version:
            raise ValueError("version storage identity is required")
        object.__setattr__(
            self, "manifest_hash", _validated_digest(self.manifest_hash, "manifest_hash")
        )
        if len(self.storage_key) != 32 or any(
            character not in "0123456789abcdef" for character in self.storage_key
        ):
            raise ValueError("storage_key must be 32 lowercase hex characters")
        if self.pack_storage_schema not in {"legacy_v1", "content_key_v2_pack"}:
            raise ValueError("invalid pack storage schema")
        if self.environment_storage_schema not in {
            "legacy_v1",
            "content_key_v2_environment",
        }:
            raise ValueError("invalid environment storage schema")
        for name in ("pack_root_hash", "environment_root_hash", "archive_hash"):
            object.__setattr__(self, name, _validated_digest(getattr(self, name), name))
        if not math.isfinite(float(self.created_at)):
            raise ValueError("created_at must be finite")

    @staticmethod
    def derive_storage_key(pack_id: str, version: str, manifest_hash: str) -> str:
        _validated_digest(manifest_hash, "manifest_hash")
        return hashlib.sha256(
            canonical_json(["capability-storage-v2", pack_id, version, manifest_hash]).encode(
                "utf-8"
            )
        ).hexdigest()[:32]

    @property
    def pack_root(self) -> str:
        return f"cv2/{self.storage_key}/p"

    @property
    def environment_root(self) -> str:
        return f"cv2/{self.storage_key}/e"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "storage_key": self.storage_key,
            "pack_storage_schema": self.pack_storage_schema,
            "pack_root_hash": self.pack_root_hash,
            "environment_storage_schema": self.environment_storage_schema,
            "environment_root_hash": self.environment_root_hash,
            "archive_hash": self.archive_hash,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class CapabilityRuntimePrepareIntent:
    operation_id: str
    runtime_instance_id: str
    entry_id: str
    ordinal: int
    runtime_kind: str
    status: str
    runtime_set_hash: str
    authorization_hash: str | None
    launch_revocation_epoch: int
    adapter_fingerprint: str
    argv_hash: str
    env_scope_hash: str
    workdir: str
    job_identity: str | None
    pid: int | None
    process_create_time: float | None
    session_identity: str | None
    start_outcome: str | None
    start_ack_at: float | None
    health_outcome_hash: str | None
    started_at: float | None
    updated_at: float
    last_error: str | None


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeSetRecord:
    operation_id: str
    purpose: str
    lifecycle_action: str | None
    owner_activation_id: str | None
    lease_activation_id: str | None
    target_pack_id: str | None
    target_version: str | None
    owner_binding_set_stamp: str | None
    run_catalog_content_stamp: str | None
    target_package_hash: str | None
    target_manifest_hash: str | None
    runtime_set_hash: str
    expected_instance_count: int
    authorization_hash: str | None
    launch_revocation_epoch: int
    status: str
    created_at: float
    updated_at: float
    last_error: str | None
    instances: tuple[CapabilityRuntimePrepareIntent, ...]


@dataclass(frozen=True, slots=True)
class CapabilityVersionRecord:
    descriptor: CapabilityVersionDescriptor
    install_path: Path
    validation_status: str
    expected_tool_fingerprints: tuple[str, ...]
    parent_version: str | None
    parent_manifest_hash: str | None
    derived_from_receipt_ref: str | None
    created_at: float


@dataclass(frozen=True, slots=True)
class CapabilityOperationRecord:
    operation_id: str
    idempotency_key: str
    root_run_id: str | None
    kind: str
    pack_id: str | None
    requested_scope: str | None
    requested_scope_key: str | None
    phase: str
    status: str
    request: Mapping[str, JsonValue]
    error: Mapping[str, JsonValue] | None
    started_at: float
    updated_at: float
    ended_at: float | None


@dataclass(frozen=True, slots=True)
class CapabilityPublishIntent:
    intent_id: str
    operation_id: str
    expected_registry_revision: int
    old_specs: tuple[Mapping[str, JsonValue], ...]
    new_specs: tuple[Mapping[str, JsonValue], ...]
    old_binding: Mapping[str, JsonValue] | None
    new_binding: Mapping[str, JsonValue]
    phase: str
    status: str
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeLease:
    runtime_lease_id: str
    pack_id: str
    version: str
    manifest_hash: str
    server_id: str
    pid: int | None
    run_id: str | None
    session_generation: int
    state: str
    heartbeat_at: float
    created_at: float

    @property
    def provenance_ref(self) -> str:
        """Stable logical provenance; physical reconnects do not change it."""

        return fingerprint_json(
            {
                "runtime_lease_id": self.runtime_lease_id,
                "pack_id": self.pack_id,
                "version": self.version,
                "manifest_hash": self.manifest_hash,
                "server_id": self.server_id,
            }
        )


@dataclass(frozen=True, slots=True)
class CapabilityRuntimeCallLease:
    call_lease_id: str
    runtime_lease_id: str
    effect_id: str
    session_generation: int
    state: str
    started_at: float
    ended_at: float | None


@dataclass(frozen=True, slots=True)
class LegacyAuthorizationImportRecord:
    """Durable proof that one legacy authorization source was consumed."""

    source_key: str
    outcome: LegacyAuthorizationImportOutcome
    source_fingerprint: str | None
    imported_mode: AuthorizationMode | None
    error_code: str | None
    imported_at: float

    def __post_init__(self) -> None:
        if not self.source_key.strip():
            raise ValueError("legacy import source_key is required")
        if self.outcome not in {"imported", "missing", "invalid"}:
            raise ValueError(f"unknown legacy import outcome: {self.outcome}")
        if self.source_fingerprint is not None and (
            len(self.source_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in self.source_fingerprint)
        ):
            raise ValueError("legacy import source_fingerprint must be a lowercase SHA-256 digest")
        if self.imported_mode is not None and self.imported_mode not in {
            "manual",
            "auto",
        }:
            raise ValueError(f"unknown imported authorization mode: {self.imported_mode}")
        if not math.isfinite(float(self.imported_at)):
            raise ValueError("legacy import timestamp must be finite")
        if self.outcome == "imported":
            if (
                self.source_fingerprint is None
                or self.imported_mode is None
                or self.error_code is not None
            ):
                raise ValueError("imported legacy state requires a source and mode")
        elif self.outcome == "missing":
            if (
                self.source_fingerprint is not None
                or self.imported_mode is not None
                or self.error_code is not None
            ):
                raise ValueError("missing legacy state cannot carry source data")
        elif self.imported_mode is not None or not self.error_code:
            raise ValueError("invalid legacy state requires no mode and an error code")


def _json_object(raw: str | None) -> Mapping[str, JsonValue] | None:
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise CapabilityStoreError("corrupt_json", "stored value is not an object")
    return value


def _json_array(raw: str) -> tuple[Any, ...]:
    value = json.loads(raw)
    if not isinstance(value, list):
        raise CapabilityStoreError("corrupt_json", "stored value is not an array")
    return tuple(value)


def _descriptor_from_json(raw: str) -> CapabilityVersionDescriptor:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise CapabilityStoreError("corrupt_descriptor", "descriptor is not an object")
    try:
        return CapabilityVersionDescriptor(
            capability_id=str(value["capability_id"]),
            display_name=str(value["display_name"]),
            version=str(value["version"]),
            kind=str(value["kind"]),  # type: ignore[arg-type]
            source=str(value["source"]),
            description=str(value["description"]),
            aliases=tuple(str(item) for item in value["aliases"]),
            logical_tool_ids=tuple(str(item) for item in value["logical_tool_ids"]),
            provider_tool_names=tuple(str(item) for item in value["provider_tool_names"]),
            permission_categories=tuple(str(item) for item in value["permission_categories"]),
            effect_kinds=tuple(str(item) for item in value["effect_kinds"]),
            schema_hash=str(value["schema_hash"]),
            manifest_hash=str(value["manifest_hash"]),
            health=str(value["health"]),  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CapabilityStoreError(
            "corrupt_descriptor", f"cannot decode capability descriptor: {exc}"
        ) from exc


def _binding_from_row(row: Mapping[str, Any]) -> CapabilityBinding:
    keys = set(row.keys())
    scope = str(row["scope"])
    return CapabilityBinding(
        binding_id=str(row["binding_id"]),
        capability_id=str(row["pack_id"]),
        version=str(row["active_version"]),
        manifest_hash=str(row["active_manifest_hash"]),
        scope=scope,  # type: ignore[arg-type]
        scope_key=str(row["scope_key"]),
        active=bool(row["enabled"]),
        generation=int(row["generation"]),
        owner_key=(str(row["owner_key"]) if "owner_key" in keys else _default_owner_key(scope)),
        management_policy=(
            str(row["management_policy"])
            if "management_policy" in keys
            else ("host_managed" if scope == "builtin" else "legacy_import")
        ),  # type: ignore[arg-type]
        management_generation=(
            int(row["management_generation"]) if "management_generation" in keys else 0
        ),
    )


def _detail_token_from_row(row: Mapping[str, Any]) -> PlatformDetailToken:
    return PlatformDetailToken(
        key=OwnerScopeKey(
            str(row["owner_key"]),
            str(row["scope"]),  # type: ignore[arg-type]
            str(row["scope_key"]),
        ),
        exists=str(row["row_state"]) == "existing",
        version=int(row["version"]),
        owner_catalog_generation=int(row["owner_catalog_generation"]),
        committed_owner_binding_set_stamp=str(row["committed_owner_binding_set_stamp"]),
        manager_receipt_set_hash=str(row["manager_receipt_set_hash"]),
    )


def _version_from_row(row: Mapping[str, Any]) -> CapabilityVersionRecord:
    fingerprints = _json_array(str(row["expected_tool_fingerprints_json"]))
    return CapabilityVersionRecord(
        descriptor=_descriptor_from_json(str(row["descriptor_json"])),
        install_path=Path(str(row["install_path"])),
        validation_status=str(row["validation_status"]),
        expected_tool_fingerprints=tuple(str(item) for item in fingerprints),
        parent_version=(None if row["parent_version"] is None else str(row["parent_version"])),
        parent_manifest_hash=(
            None if row["parent_manifest_hash"] is None else str(row["parent_manifest_hash"])
        ),
        derived_from_receipt_ref=(
            None
            if row["derived_from_receipt_ref"] is None
            else str(row["derived_from_receipt_ref"])
        ),
        created_at=float(row["created_at"]),
    )


def _default_owner_key(scope: str) -> str:
    return "builtin" if scope == "builtin" else LEGACY_LOCAL_OWNER_KEY


async def _fetch_mappings(
    cursor: aiosqlite.Cursor,
) -> list[Mapping[str, Any]]:
    rows = await cursor.fetchall()
    if not rows or hasattr(rows[0], "keys"):
        return list(rows)
    columns = tuple(str(item[0]) for item in cursor.description or ())
    return [dict(zip(columns, row, strict=True)) for row in rows]
