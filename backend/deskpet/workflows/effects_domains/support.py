# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Shared helpers for the split class."""

from __future__ import annotations

import copy
import hashlib
import ntpath
import os
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from deskpet.types.task_grants import ResourceSelector

from ..contracts import JsonValue, canonical_json, validate_json_value


class EffectJournalError(RuntimeError):
    """Base class for durable effect contract failures."""


class PreparedToolArgumentsProjectionError(ValueError):
    """A prepared call could not be projected back to canonical JSON."""


class EffectStateConflict(EffectJournalError):
    """An effect or target cannot make the requested state transition."""


class BudgetReservationExceeded(EffectJournalError):
    """A durable reservation would exceed the run's immutable budget capability."""


class StagingPreconditionFailed(EffectJournalError):
    """The final target changed after call preparation."""


class TargetMode(StrEnum):
    CREATE = "create"
    REPLACE = "replace"
    APPEND = "append"
    EDIT = "edit"


class ToolOutcomeState(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    MALFORMED = "malformed"


class EffectAction(StrEnum):
    EXECUTE = "execute"
    REUSE = "reuse"
    IN_FLIGHT = "in_flight"
    RECONCILE = "reconcile"
    FAILED = "failed"


class EffectStatus(StrEnum):
    RUNNING = "running"
    COMMITTED = "committed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    LATE_ORPHAN = "late_orphan"


def _sha256_json(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return copy.deepcopy(value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_target_path(
    path: str | os.PathLike[str],
    *,
    platform: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> str:
    """Return the canonical path spelling used for reservation identity.

    Windows identity is separator-, drive-, UNC-, Unicode-, and case-insensitive.
    POSIX identity deliberately preserves case.
    """

    selected = (platform or ("windows" if os.name == "nt" else "posix")).lower()
    raw = unicodedata.normalize("NFC", os.fspath(path))
    if selected in {"windows", "win32", "nt"}:
        raw = raw.replace("/", "\\")
        if not ntpath.isabs(raw):
            base = unicodedata.normalize("NFC", os.fspath(cwd or os.getcwd())).replace("/", "\\")
            raw = ntpath.join(base, raw)
        normalized = ntpath.normpath(raw)
        drive, tail = ntpath.splitdrive(normalized)
        if drive and not tail.startswith("\\"):
            tail = "\\" + tail
        return unicodedata.normalize("NFC", drive + tail).casefold()
    if selected not in {"posix", "linux", "darwin"}:
        raise ValueError(f"unsupported target platform: {platform}")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd or os.getcwd()) / candidate
    return unicodedata.normalize("NFC", str(candidate.resolve(strict=False)))


def target_reservation_key(
    path: str | os.PathLike[str],
    *,
    platform: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> str:
    return hashlib.sha256(
        normalize_target_path(path, platform=platform, cwd=cwd).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedTarget:
    final_path: str
    staging_path: str
    reservation_key: str
    mode: TargetMode | str
    pre_exists: bool
    pre_hash: str | None
    pre_size: int | None
    pre_mtime: float | None
    format: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", TargetMode(self.mode))
        if not self.final_path or not self.staging_path or not self.reservation_key:
            raise ValueError("prepared target paths and reservation key are required")
        if Path(self.final_path).parent != Path(self.staging_path).parent:
            raise ValueError("staging path must be in the final target directory")
        if self.pre_exists and (
            self.pre_hash is None or self.pre_size is None or self.pre_mtime is None
        ):
            raise ValueError("existing target preparation requires hash, size, and mtime")
        if not self.pre_exists and any(
            value is not None for value in (self.pre_hash, self.pre_size, self.pre_mtime)
        ):
            raise ValueError("missing target preparation cannot have precondition evidence")
        if self.mode is TargetMode.CREATE and self.pre_exists:
            raise ValueError("create target already exists")
        if self.mode is not TargetMode.CREATE and not self.pre_exists:
            raise ValueError(f"{self.mode.value} target does not exist")

    @classmethod
    def prepare(
        cls,
        final_path: str | os.PathLike[str],
        *,
        run_id: str,
        stable_call_id: str,
        mode: TargetMode | str,
        format: str | None = None,
        platform: str | None = None,
    ) -> PreparedTarget:
        final = Path(final_path).expanduser().resolve(strict=False)
        if final.exists() and not final.is_file():
            raise ValueError(f"effect target is not a file: {final}")
        pre_exists = final.is_file()
        stat = final.stat() if pre_exists else None
        reservation_key = target_reservation_key(final, platform=platform)
        suffix = hashlib.sha256(
            f"{run_id}|{stable_call_id}|{reservation_key}".encode()
        ).hexdigest()[:20]
        staging = final.with_name(f".{final.name}.{suffix}.deskpet-stage")
        return cls(
            final_path=str(final),
            staging_path=str(staging),
            reservation_key=reservation_key,
            mode=mode,
            pre_exists=pre_exists,
            pre_hash=_file_sha256(final) if pre_exists else None,
            pre_size=int(stat.st_size) if stat else None,
            pre_mtime=float(stat.st_mtime) if stat else None,
            format=format,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "final_path": self.final_path,
            "staging_path": self.staging_path,
            "reservation_key": self.reservation_key,
            "mode": self.mode.value,
            "pre_exists": self.pre_exists,
            "pre_hash": self.pre_hash,
            "pre_size": self.pre_size,
            "pre_mtime": self.pre_mtime,
            "format": self.format,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreparedTarget:
        return cls(**dict(value))


def prepared_args_hash(
    tool_name: str,
    final_params: Mapping[str, JsonValue],
    input_blob_hashes: Sequence[str] = (),
) -> str:
    payload: dict[str, JsonValue] = {
        "tool_name": tool_name,
        "final_params": _thaw_json(final_params),
        "input_blob_hashes": sorted(str(item) for item in input_blob_hashes),
    }
    return _sha256_json(payload)


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    tool_name: str
    stable_call_id: str
    final_params: Mapping[str, JsonValue]
    args_hash: str
    prepared_targets: tuple[PreparedTarget, ...]
    tool_spec_version: str
    schema_hash: str
    permission_policy_version: str
    effect_type: str
    effect_policy_version: str = ""
    input_blob_hashes: tuple[str, ...] = ()
    tool_spec_fingerprint: str = ""
    runtime_provenance_ref: str = ""
    catalog_snapshot_ref: str = ""
    retry_of_effect_id: str = ""
    resource_selectors: tuple[ResourceSelector, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            (
                self.tool_name,
                self.stable_call_id,
                self.tool_spec_version,
                self.schema_hash,
                self.permission_policy_version,
                self.effect_type,
            )
        ):
            raise ValueError("prepared call identity and versions are required")
        params = _thaw_json(self.final_params)
        if not isinstance(params, dict):
            raise TypeError("prepared call final_params must be an object")
        validate_json_value(params)
        blobs = tuple(sorted(str(item) for item in self.input_blob_hashes))
        expected = prepared_args_hash(self.tool_name, params, blobs)
        if self.args_hash != expected:
            raise ValueError("prepared call args_hash does not match final parameters")
        for field_name in (
            "tool_spec_fingerprint",
            "runtime_provenance_ref",
            "catalog_snapshot_ref",
        ):
            value = getattr(self, field_name)
            if value and (
                len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        if self.retry_of_effect_id and (
            len(self.retry_of_effect_id) != 64
            or any(character not in "0123456789abcdef" for character in self.retry_of_effect_id)
        ):
            raise ValueError("retry_of_effect_id must be a lowercase SHA-256 digest")
        targets = tuple(self.prepared_targets)
        if len({item.reservation_key for item in targets}) != len(targets):
            raise ValueError("prepared call contains duplicate targets")

        selectors = tuple(self.resource_selectors)
        if not all(isinstance(item, ResourceSelector) for item in selectors):
            raise TypeError("prepared call resource_selectors must contain ResourceSelector")
        selector_keys = {(item.kind, item.canonical_value, item.access) for item in selectors}
        if len(selector_keys) != len(selectors):
            raise ValueError("prepared call contains duplicate resource selectors")
        object.__setattr__(self, "final_params", _freeze_json(params))
        object.__setattr__(self, "prepared_targets", targets)
        object.__setattr__(self, "input_blob_hashes", blobs)
        object.__setattr__(self, "resource_selectors", selectors)

    @classmethod
    def prepare(
        cls,
        *,
        tool_name: str,
        stable_call_id: str,
        final_params: Mapping[str, JsonValue],
        prepared_targets: Sequence[PreparedTarget] = (),
        tool_spec_version: str,
        schema_hash: str,
        permission_policy_version: str,
        effect_type: str,
        effect_policy_version: str = "",
        input_blob_hashes: Sequence[str] = (),
        tool_spec_fingerprint: str = "",
        runtime_provenance_ref: str = "",
        catalog_snapshot_ref: str = "",
        retry_of_effect_id: str = "",
        resource_selectors: Sequence[ResourceSelector] = (),
    ) -> PreparedToolCall:
        return cls(
            tool_name=tool_name,
            stable_call_id=stable_call_id,
            final_params=final_params,
            args_hash=prepared_args_hash(tool_name, final_params, input_blob_hashes),
            prepared_targets=tuple(prepared_targets),
            tool_spec_version=tool_spec_version,
            schema_hash=schema_hash,
            permission_policy_version=permission_policy_version,
            effect_type=effect_type,
            effect_policy_version=effect_policy_version,
            input_blob_hashes=tuple(input_blob_hashes),
            tool_spec_fingerprint=tool_spec_fingerprint,
            runtime_provenance_ref=runtime_provenance_ref,
            catalog_snapshot_ref=catalog_snapshot_ref,
            retry_of_effect_id=retry_of_effect_id,
            resource_selectors=tuple(resource_selectors),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        data: dict[str, JsonValue] = {
            "tool_name": self.tool_name,
            "stable_call_id": self.stable_call_id,
            "final_params": self.arguments_json(),
            "args_hash": self.args_hash,
            "prepared_targets": [target.to_dict() for target in self.prepared_targets],
            "tool_spec_version": self.tool_spec_version,
            "schema_hash": self.schema_hash,
            "permission_policy_version": self.permission_policy_version,
            "effect_type": self.effect_type,
            "input_blob_hashes": list(self.input_blob_hashes),
        }
        # Preserve byte identity for v1-v6 calls, which predate effect policy
        # versioning.  New calls still persist a non-empty version durably.
        if self.effect_policy_version:
            data["effect_policy_version"] = self.effect_policy_version
        # Preserve byte identity for legacy prepared calls. New registry
        # preparations persist all available immutable execution references.
        if self.tool_spec_fingerprint:
            data["tool_spec_fingerprint"] = self.tool_spec_fingerprint
        if self.runtime_provenance_ref:
            data["runtime_provenance_ref"] = self.runtime_provenance_ref
        if self.catalog_snapshot_ref:
            data["catalog_snapshot_ref"] = self.catalog_snapshot_ref
        if self.retry_of_effect_id:
            data["retry_of_effect_id"] = self.retry_of_effect_id
        if self.resource_selectors:
            data["resource_selectors"] = [
                selector.to_dict() for selector in self.resource_selectors
            ]
        return data

    def arguments_json(self) -> dict[str, JsonValue]:
        """Return a detached, recursively thawed JSON object for Host boundaries."""

        try:
            params = _thaw_json(self.final_params)
            if not isinstance(params, dict):
                raise TypeError("prepared call final_params must project to an object")
            validate_json_value(params)
        except (TypeError, ValueError) as exc:
            raise PreparedToolArgumentsProjectionError(
                "prepared tool arguments could not be projected to canonical JSON"
            ) from exc
        return params

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreparedToolCall:
        data = dict(value)
        data["prepared_targets"] = tuple(
            PreparedTarget.from_dict(item) for item in data.get("prepared_targets", ())
        )
        data.setdefault("effect_policy_version", "")
        data["input_blob_hashes"] = tuple(data.get("input_blob_hashes", ()))
        data.setdefault("tool_spec_fingerprint", "")
        data.setdefault("runtime_provenance_ref", "")
        data.setdefault("catalog_snapshot_ref", "")
        data.setdefault("retry_of_effect_id", "")

        data["resource_selectors"] = tuple(
            ResourceSelector.from_dict(item) for item in data.get("resource_selectors", ())
        )
        return cls(**data)


def effect_fingerprint(
    *,
    workflow_name: str,
    workflow_version: str,
    node_id: str,
    logical_effect_key: str,
    effect_type: str,
    args_hash: str,
    policy_version: str,
) -> str:
    return _sha256_json(
        {
            "workflow_name": workflow_name,
            "workflow_version": workflow_version,
            "node_id": node_id,
            "logical_effect_key": logical_effect_key,
            "effect_type": effect_type,
            "args_hash": args_hash,
            "policy_version": policy_version,
        }
    )


@dataclass(frozen=True, slots=True)
class NormalizedToolOutcome:
    state: ToolOutcomeState | str
    value: JsonValue = None
    error: Mapping[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        state = ToolOutcomeState(self.state)
        value = copy.deepcopy(self.value)
        error = copy.deepcopy(dict(self.error)) if self.error is not None else None
        validate_json_value(value)
        if error is not None:
            validate_json_value(error)
        if state is ToolOutcomeState.SUCCESS and error is not None:
            raise ValueError("successful outcomes cannot carry an error")
        if state is not ToolOutcomeState.SUCCESS and error is None:
            raise ValueError("failure and malformed outcomes require an error envelope")
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "value", _freeze_json(value))
        object.__setattr__(self, "error", _freeze_json(error) if error is not None else None)

    @classmethod
    def success(cls, value: JsonValue = None) -> NormalizedToolOutcome:
        return cls(ToolOutcomeState.SUCCESS, value=value)

    @classmethod
    def failure(cls, code: str, message: str, *, value: JsonValue = None) -> NormalizedToolOutcome:
        return cls(ToolOutcomeState.FAILURE, value=value, error={"code": code, "message": message})

    @classmethod
    def malformed(cls, message: str) -> NormalizedToolOutcome:
        return cls(
            ToolOutcomeState.MALFORMED,
            error={"code": "malformed_tool_outcome", "message": message},
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "state": self.state.value,
            "value": _thaw_json(self.value),
            "error": _thaw_json(self.error) if self.error is not None else None,
        }

    @property
    def status(self) -> ToolOutcomeState:
        return self.state

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NormalizedToolOutcome:
        return cls(state=value["state"], value=value.get("value"), error=value.get("error"))


@dataclass(frozen=True, slots=True)
class CheckpointEffectLink:
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str


@dataclass(frozen=True, slots=True)
class EffectRecord:
    effect_id: str
    run_id: str
    node_execution_id: str
    effect_fingerprint: str
    effect_type: str
    args_hash: str
    status: EffectStatus
    prepared: PreparedToolCall
    outcome: NormalizedToolOutcome | None
    receipt_ref: str | None
    artifact_refs: tuple[str, ...]
    lease_epoch: int


@dataclass(frozen=True, slots=True)
class BudgetReservationRecord:
    effect_id: str
    run_id: str
    ledger_kind: str
    input_reserved: int
    output_reserved: int
    cost_reserved_micros: int
    input_actual: int | None
    output_actual: int | None
    cost_actual_micros: int | None
    status: str
    dispatch_state: str
    upstream_started_at: float | None
    created_at: float
    updated_at: float
