# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Durable, fenced side-effect facts for graph workflows."""

from __future__ import annotations

import copy
import hashlib
import json
import ntpath
import os
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import aiosqlite

from .contracts import EffectKind, EffectPolicy, JsonValue, canonical_json, validate_json_value
from .deadlines import DeadlineLeaseV1, DurableDeadlineV1, resume_deadline
from .store import RunFence, StaleRunFence, initialize_workflow_db


class EffectJournalError(RuntimeError):
    """Base class for durable effect contract failures."""


class PreparedToolArgumentsProjectionError(ValueError):
    """A prepared call could not be projected back to canonical JSON."""


class TargetReservationConflict(EffectJournalError):
    """A final target is already reserved by a different stable call."""


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
        if self.pre_exists and (self.pre_hash is None or self.pre_size is None or self.pre_mtime is None):
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
    ) -> "PreparedTarget":
        final = Path(final_path).expanduser().resolve(strict=False)
        if final.exists() and not final.is_file():
            raise ValueError(f"effect target is not a file: {final}")
        pre_exists = final.is_file()
        stat = final.stat() if pre_exists else None
        reservation_key = target_reservation_key(final, platform=platform)
        suffix = hashlib.sha256(
            f"{run_id}|{stable_call_id}|{reservation_key}".encode("utf-8")
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
    def from_dict(cls, value: Mapping[str, Any]) -> "PreparedTarget":
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
    resource_selectors: tuple["ResourceSelector", ...] = ()

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
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
        if self.retry_of_effect_id and (
            len(self.retry_of_effect_id) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.retry_of_effect_id
            )
        ):
            raise ValueError(
                "retry_of_effect_id must be a lowercase SHA-256 digest"
            )
        targets = tuple(self.prepared_targets)
        if len({item.reservation_key for item in targets}) != len(targets):
            raise ValueError("prepared call contains duplicate targets")
        from deskpet.types.task_grants import ResourceSelector

        selectors = tuple(self.resource_selectors)
        if not all(isinstance(item, ResourceSelector) for item in selectors):
            raise TypeError(
                "prepared call resource_selectors must contain ResourceSelector"
            )
        selector_keys = {
            (item.kind, item.canonical_value, item.access) for item in selectors
        }
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
        resource_selectors: Sequence["ResourceSelector"] = (),
    ) -> "PreparedToolCall":
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
    def from_dict(cls, value: Mapping[str, Any]) -> "PreparedToolCall":
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
        from deskpet.types.task_grants import ResourceSelector

        data["resource_selectors"] = tuple(
            ResourceSelector.from_dict(item)
            for item in data.get("resource_selectors", ())
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
    def success(cls, value: JsonValue = None) -> "NormalizedToolOutcome":
        return cls(ToolOutcomeState.SUCCESS, value=value)

    @classmethod
    def failure(
        cls, code: str, message: str, *, value: JsonValue = None
    ) -> "NormalizedToolOutcome":
        return cls(ToolOutcomeState.FAILURE, value=value, error={"code": code, "message": message})

    @classmethod
    def malformed(cls, message: str) -> "NormalizedToolOutcome":
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
    def from_dict(cls, value: Mapping[str, Any]) -> "NormalizedToolOutcome":
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
class BeginEffectResult:
    action: EffectAction
    effect: EffectRecord


@dataclass(frozen=True, slots=True)
class IdempotentReadAttempt:
    action: EffectAction
    effect_id: str
    logical_effect_id: str
    attempt_no: int
    status: EffectStatus
    canonical_result_ref: str | None = None
    dependency_refs: tuple[str, ...] = ()
    result_kind: str | None = None
    deadline_state: DurableDeadlineV1 | None = None
    deadline_lease: DeadlineLeaseV1 | None = None


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


@dataclass(frozen=True, slots=True)
class BudgetLedgerSnapshot:
    ledger_kind: str
    max_input_tokens: int
    max_output_tokens: int
    max_cost_micros: int
    charged_input_tokens: int
    charged_output_tokens: int
    charged_cost_micros: int
    reservations: tuple[BudgetReservationRecord, ...]


@dataclass(frozen=True, slots=True)
class EffectExecutionContext:
    """Fenced runtime identity required by production effect adapters."""

    journal: "EffectJournal"
    fence: RunFence
    node_execution_id: str
    workflow_name: str
    workflow_version: str
    node_id: str
    reuse_checkpoint: CheckpointEffectLink | None = None

    def __post_init__(self) -> None:
        if not all(
            (
                self.node_execution_id,
                self.workflow_name,
                self.workflow_version,
                self.node_id,
            )
        ):
            raise ValueError("effect execution context identity is incomplete")
        if self.fence.run_id == "":
            raise ValueError("effect execution context requires a run fence")


@dataclass(frozen=True, slots=True)
class StagedFileEvidence:
    sha256: str
    size: int
    format: str | None
    created_dirs: tuple[str, ...]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "format": self.format,
            "created_dirs": list(self.created_dirs),
        }


class StagedFileLifecycle:
    """Same-directory copy-on-write staging with precondition checks."""

    def stage(
        self,
        target: PreparedTarget,
        content: bytes,
        *,
        validator: Callable[[Path, str | None], None] | None = None,
    ) -> StagedFileEvidence:
        final = Path(target.final_path)
        staging = Path(target.staging_path)
        if final.parent != staging.parent:
            raise ValueError("staging and final files must share a directory")
        self._assert_precondition(target)
        created_dirs = self._create_parent_dirs(final.parent)
        payload = content
        if target.mode is TargetMode.APPEND:
            payload = final.read_bytes() + content
        try:
            with staging.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._validate_format(staging, target.format)
            if validator is not None:
                validator(staging, target.format)
            return StagedFileEvidence(
                sha256=_file_sha256(staging),
                size=staging.stat().st_size,
                format=target.format,
                created_dirs=created_dirs,
            )
        except BaseException:
            if staging.exists():
                staging.unlink()
            self._remove_empty_dirs(created_dirs)
            raise

    def commit(self, target: PreparedTarget, evidence: StagedFileEvidence) -> None:
        final = Path(target.final_path)
        staging = Path(target.staging_path)
        if not staging.is_file():
            raise StagingPreconditionFailed("staging file is missing")
        if staging.stat().st_size != evidence.size or _file_sha256(staging) != evidence.sha256:
            raise StagingPreconditionFailed("staging file changed after validation")
        self._assert_precondition(target)
        if target.mode is TargetMode.CREATE:
            try:
                os.link(staging, final)
            except FileExistsError as exc:
                raise StagingPreconditionFailed("create target appeared before commit") from exc
            staging.unlink()
        else:
            os.replace(staging, final)
        self._flush_file(final)
        self._flush_directory(final.parent)

    def rollback(self, target: PreparedTarget, evidence: StagedFileEvidence | None = None) -> None:
        staging = Path(target.staging_path)
        if staging.exists():
            staging.unlink()
        if evidence is not None:
            self._remove_empty_dirs(evidence.created_dirs)

    def reconcile(self, target: PreparedTarget, evidence: StagedFileEvidence) -> str:
        final = Path(target.final_path)
        staging = Path(target.staging_path)
        final_matches = final.is_file() and final.stat().st_size == evidence.size and _file_sha256(final) == evidence.sha256
        staging_matches = staging.is_file() and staging.stat().st_size == evidence.size and _file_sha256(staging) == evidence.sha256
        if final_matches:
            return "committed"
        if staging_matches:
            return "staged"
        return "uncertain"

    @staticmethod
    def _create_parent_dirs(parent: Path) -> tuple[str, ...]:
        missing: list[Path] = []
        cursor = parent
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for item in reversed(missing):
            item.mkdir()
        return tuple(str(item) for item in missing)

    @staticmethod
    def _remove_empty_dirs(created_dirs: Sequence[str]) -> None:
        for item in created_dirs:
            try:
                Path(item).rmdir()
            except (FileNotFoundError, OSError):
                pass

    @staticmethod
    def _assert_precondition(target: PreparedTarget) -> None:
        final = Path(target.final_path)
        exists = final.is_file()
        if target.pre_exists != exists:
            raise StagingPreconditionFailed("target existence changed after preparation")
        if not exists:
            return
        stat = final.stat()
        if (
            stat.st_size != target.pre_size
            or stat.st_mtime != target.pre_mtime
            or _file_sha256(final) != target.pre_hash
        ):
            raise StagingPreconditionFailed("target content changed after preparation")

    @staticmethod
    def _validate_format(path: Path, format_name: str | None) -> None:
        selected = (format_name or path.suffix.lstrip(".")).casefold()
        if selected in {"docx", "xlsx", "pptx"} and not zipfile.is_zipfile(path):
            raise ValueError(f"invalid {selected} staging file")
        if selected == "pdf" and not path.read_bytes().startswith(b"%PDF-"):
            raise ValueError("invalid PDF staging file")
        if selected == "png" and not path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG staging file")

    @staticmethod
    def _flush_file(path: Path) -> None:
        # Windows requires a writable CRT descriptor for fsync/FlushFileBuffers.
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _flush_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class EffectJournal:
    """SQLite effect journal whose mutations are fenced by run ownership."""

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    async def initialize(self) -> None:
        await initialize_workflow_db(self.path)

    async def _connect(self) -> aiosqlite.Connection:
        await initialize_workflow_db(self.path)
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("PRAGMA synchronous=FULL")
        return db

    async def resolve_active_context(
        self,
        *,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        session_id: str | None = None,
    ) -> EffectExecutionContext:
        """Resolve exactly one live production run or fail closed."""

        db = await self._connect()
        try:
            clauses = [
                "workflow_name=?",
                "workflow_version=?",
                "status='running'",
                "lease_owner IS NOT NULL",
                "lease_expires_at>?",
            ]
            params: list[object] = [
                workflow_name,
                workflow_version,
                self._clock(),
            ]
            if session_id is not None:
                clauses.append("session_id=?")
                params.append(session_id)
            rows = await (
                await db.execute(
                    f"""SELECT run_id,lease_owner,lease_epoch,run_version,
                    head_checkpoint_ns,head_checkpoint_id
                    FROM workflow_runs WHERE {' AND '.join(clauses)}
                    ORDER BY updated_at DESC LIMIT 2""",
                    params,
                )
            ).fetchall()
        finally:
            await db.close()
        if len(rows) != 1:
            qualifier = f" for session {session_id}" if session_id is not None else ""
            raise EffectStateConflict(
                f"expected one live {workflow_name}@{workflow_version} run{qualifier}; found {len(rows)}"
            )
        row = rows[0]
        node_digest = hashlib.sha256(
            canonical_json(
                {
                    "run_id": str(row["run_id"]),
                    "node_id": node_id,
                    "checkpoint_ns": str(row["head_checkpoint_ns"] or ""),
                    "checkpoint_id": str(row["head_checkpoint_id"] or "root"),
                }
            ).encode("utf-8")
        ).hexdigest()
        return EffectExecutionContext(
            journal=self,
            fence=RunFence(
                str(row["run_id"]),
                str(row["lease_owner"]),
                int(row["lease_epoch"]),
                int(row["run_version"]),
            ),
            node_execution_id=f"effect-node-{node_digest[:32]}",
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
        )

    @staticmethod
    async def _assert_fence(db: aiosqlite.Connection, fence: RunFence) -> None:
        row = await (
            await db.execute(
                """SELECT 1 FROM workflow_runs WHERE run_id=? AND lease_owner=?
                AND lease_epoch=? AND run_version=? AND status='running'""",
                (fence.run_id, fence.owner, fence.lease_epoch, fence.run_version),
            )
        ).fetchone()
        if row is None:
            raise StaleRunFence(f"stale effect writer: {fence.run_id}")

    async def reserve_targets(
        self,
        fence: RunFence,
        prepared: PreparedToolCall,
        *,
        ttl_seconds: float = 90.0,
    ) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self.reserve_targets_tx(
                db,
                fence,
                prepared,
                ttl_seconds=ttl_seconds,
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def reserve_targets_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        prepared: PreparedToolCall,
        *,
        ttl_seconds: float = 90.0,
    ) -> None:
        """Reserve prepared targets on the caller-owned transaction."""

        await self._assert_fence(db, fence)
        await self._reserve_targets(db, fence, prepared, "prepared", ttl_seconds)

    async def begin(
        self,
        fence: RunFence,
        *,
        node_execution_id: str,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        logical_effect_key: str,
        prepared: PreparedToolCall,
        policy: EffectPolicy,
        reuse_checkpoint: CheckpointEffectLink | None = None,
        reservation_ttl_seconds: float = 90.0,
    ) -> BeginEffectResult:
        """Begin a legacy effect without changing its established semantics."""

        return await self._begin(
            fence,
            node_execution_id=node_execution_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
            logical_effect_key=logical_effect_key,
            prepared=prepared,
            policy=policy,
            reuse_checkpoint=reuse_checkpoint,
            reservation_ttl_seconds=reservation_ttl_seconds,
            budget_reservation=None,
            resource_budget_kind=None,
        )

    async def begin_with_budget(
        self,
        fence: RunFence,
        *,
        node_execution_id: str,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        logical_effect_key: str,
        prepared: PreparedToolCall,
        policy: EffectPolicy,
        ledger_kind: str,
        input_reserved: int,
        output_reserved: int,
        cost_reserved_micros: int,
        capability_key: str = "research_llm_budget",
        reuse_checkpoint: CheckpointEffectLink | None = None,
        reservation_ttl_seconds: float = 90.0,
        record_denial: bool = False,
        denial_artifact_refs: Sequence[str] = (),
        resource_budget_kind: str | None = None,
    ) -> BeginEffectResult:
        """Atomically fence, cap-check and reserve an effect's upstream budget."""

        if not ledger_kind:
            raise ValueError("ledger_kind is required")
        reserved = (input_reserved, output_reserved, cost_reserved_micros)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in reserved):
            raise ValueError("budget reservations must be non-negative integers")
        if not any(reserved):
            raise ValueError("at least one budget reservation must be positive")
        if not capability_key:
            raise ValueError("capability_key is required")
        if resource_budget_kind not in {None, "llm"}:
            raise ValueError("unsupported v6 effect resource budget kind")
        return await self._begin(
            fence,
            node_execution_id=node_execution_id,
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
            logical_effect_key=logical_effect_key,
            prepared=prepared,
            policy=policy,
            reuse_checkpoint=reuse_checkpoint,
            reservation_ttl_seconds=reservation_ttl_seconds,
            budget_reservation=(
                ledger_kind,
                input_reserved,
                output_reserved,
                cost_reserved_micros,
                capability_key,
            ),
            record_budget_denial=record_denial,
            denial_artifact_refs=denial_artifact_refs,
            resource_budget_kind=resource_budget_kind,
        )

    @staticmethod
    def _deadline_epoch(value: object) -> float:
        text = str(value)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()

    @staticmethod
    def _v6_deadline_from_row(row: Mapping[str, object]) -> DurableDeadlineV1:
        def wall(value: object | None) -> str | None:
            if value is None:
                return None
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat()

        return DurableDeadlineV1.from_json(
            {
                "schema_version": int(row["schema_version"]),
                "deadline_id": str(row["deadline_id"]),
                "parent_deadline_id": row["parent_deadline_id"],
                "logical_scope": str(row["logical_scope"]),
                "policy_hash": str(row["policy_hash"]),
                "budget_ms": int(row["budget_ms"]),
                "remaining_ms": int(row["remaining_ms"]),
                "created_at": wall(row["created_at"]),
                "last_observed_at": wall(row["last_observed_at"]),
                "wall_not_after": wall(row["wall_not_after"]),
                "offline_policy": str(row["offline_policy"]),
                "rollback_tolerance_ms": int(row["rollback_tolerance_ms"]),
                "revision": int(row["revision"]),
                "status": str(row["status"]),
                "terminal_reason": row["terminal_reason"],
                "terminal_at": wall(row["terminal_at"]),
            }
        )

    async def _persist_v6_deadline_transition(
        self,
        db: aiosqlite.Connection,
        *,
        run_id: str,
        previous_revision: int,
        state: DurableDeadlineV1,
    ) -> None:
        cursor = await db.execute(
            """UPDATE workflow_research_deadlines SET
            remaining_ms=?,last_observed_at=?,revision=?,status=?,
            terminal_reason=?,terminal_at=?
            WHERE deadline_id=? AND run_id=? AND revision=? AND status='open'""",
            (
                state.remaining_ms,
                self._deadline_epoch(state.last_observed_at),
                state.revision,
                state.status,
                state.terminal_reason,
                (
                    self._deadline_epoch(state.terminal_at)
                    if state.terminal_at is not None
                    else None
                ),
                state.deadline_id,
                run_id,
                previous_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise EffectStateConflict("v6 read deadline observation CAS lost")

    async def persist_v6_deadline(
        self,
        fence: RunFence,
        state: Mapping[str, JsonValue],
        *,
        expected_revision: int | None = None,
    ) -> None:
        """Insert or CAS-update one contract-owned v6 deadline row."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_deadlines WHERE deadline_id=?",
                    (str(state["deadline_id"]),),
                )
            ).fetchone()
            values = (
                int(state["schema_version"]),
                fence.run_id,
                state.get("parent_deadline_id"),
                str(state["logical_scope"]),
                str(state["policy_hash"]),
                int(state["budget_ms"]),
                int(state["remaining_ms"]),
                self._deadline_epoch(state["created_at"]),
                self._deadline_epoch(state["last_observed_at"]),
                self._deadline_epoch(state["wall_not_after"]),
                str(state["offline_policy"]),
                int(state["rollback_tolerance_ms"]),
                int(state["revision"]),
                str(state["status"]),
                state.get("terminal_reason"),
                (
                    self._deadline_epoch(state["terminal_at"])
                    if state.get("terminal_at") is not None
                    else None
                ),
            )
            if existing is None:
                if expected_revision is not None:
                    raise EffectStateConflict("v6 deadline CAS target is missing")
                await db.execute(
                    """INSERT INTO workflow_research_deadlines(
                    deadline_id,schema_version,run_id,parent_deadline_id,logical_scope,
                    policy_hash,budget_ms,remaining_ms,created_at,last_observed_at,
                    wall_not_after,offline_policy,rollback_tolerance_ms,revision,status,
                    terminal_reason,terminal_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(state["deadline_id"]), *values),
                )
            else:
                if str(existing["run_id"]) != fence.run_id:
                    raise EffectStateConflict("v6 deadline belongs to another run")
                if expected_revision is None:
                    immutable = (
                        str(existing["logical_scope"]),
                        str(existing["policy_hash"]),
                        int(existing["budget_ms"]),
                        existing["parent_deadline_id"],
                    )
                    requested = (
                        str(state["logical_scope"]),
                        str(state["policy_hash"]),
                        int(state["budget_ms"]),
                        state.get("parent_deadline_id"),
                    )
                    if immutable != requested:
                        raise EffectStateConflict("v6 deadline immutable identity differs")
                else:
                    cursor = await db.execute(
                        """UPDATE workflow_research_deadlines SET
                        remaining_ms=?,last_observed_at=?,revision=?,status=?,
                        terminal_reason=?,terminal_at=?
                        WHERE deadline_id=? AND run_id=? AND revision=?""",
                        (
                            int(state["remaining_ms"]),
                            self._deadline_epoch(state["last_observed_at"]),
                            int(state["revision"]),
                            str(state["status"]),
                            state.get("terminal_reason"),
                            (
                                self._deadline_epoch(state["terminal_at"])
                                if state.get("terminal_at") is not None
                                else None
                            ),
                            str(state["deadline_id"]),
                            fence.run_id,
                            expected_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise EffectStateConflict("v6 deadline revision changed")
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def load_v6_deadline(
        self, run_id: str, deadline_id: str
    ) -> dict[str, JsonValue] | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_research_deadlines WHERE run_id=? AND deadline_id=?",
                    (run_id, deadline_id),
                )
            ).fetchone()
            if row is None:
                return None
            def wall(value: object | None) -> str | None:
                if value is None:
                    return None
                return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
            return {
                "schema_version": int(row["schema_version"]),
                "deadline_id": str(row["deadline_id"]),
                "parent_deadline_id": row["parent_deadline_id"],
                "logical_scope": str(row["logical_scope"]),
                "policy_hash": str(row["policy_hash"]),
                "budget_ms": int(row["budget_ms"]),
                "remaining_ms": int(row["remaining_ms"]),
                "created_at": wall(row["created_at"]),
                "last_observed_at": wall(row["last_observed_at"]),
                "wall_not_after": wall(row["wall_not_after"]),
                "offline_policy": str(row["offline_policy"]),
                "rollback_tolerance_ms": int(row["rollback_tolerance_ms"]),
                "revision": int(row["revision"]),
                "status": str(row["status"]),
                "terminal_reason": row["terminal_reason"],
                "terminal_at": wall(row["terminal_at"]),
            }
        finally:
            await db.close()

    async def ensure_v6_resource_budgets(
        self,
        fence: RunFence,
        *,
        policy_hash: str,
        budgets: Mapping[str, int],
    ) -> None:
        expected = {"query", "fetch", "browser", "llm", "lane"}
        if set(budgets) != expected:
            raise ValueError("v6 route budgets must cover query/fetch/browser/llm/lane")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            for kind in sorted(expected):
                limit = int(budgets[kind])
                if limit < 0:
                    raise ValueError("v6 route budget limits must be non-negative")
                budget_id = hashlib.sha256(
                    f"{fence.run_id}|{policy_hash}|{kind}".encode("utf-8")
                ).hexdigest()
                row = await (
                    await db.execute(
                        """SELECT * FROM workflow_research_resource_budgets
                        WHERE run_id=? AND resource_kind=?""",
                        (fence.run_id, kind),
                    )
                ).fetchone()
                if row is None:
                    await db.execute(
                        """INSERT INTO workflow_research_resource_budgets(
                        budget_id,run_id,policy_hash,resource_kind,hard_limit)
                        VALUES(?,?,?,?,?)""",
                        (budget_id, fence.run_id, policy_hash, kind, limit),
                    )
                elif str(row["policy_hash"]) != policy_hash or int(row["hard_limit"]) != limit:
                    raise EffectStateConflict("persisted v6 route budget differs")
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def begin_idempotent_read_attempt(
        self,
        fence: RunFence,
        *,
        node_execution_id: str,
        node_id: str,
        logical_effect_id: str,
        attempt_no: int,
        deadline_id: str,
        deadline_revision: int,
        deadline_now_wall: datetime | None = None,
        deadline_now_monotonic_ns: int | None = None,
        resource_kind: str,
        resource_hard_limit: int,
        resource_policy_hash: str,
        prepared: PreparedToolCall,
        fault_injector: Callable[[str], None] | None = None,
    ) -> IdempotentReadAttempt:
        """Atomically attach frozen v6 read identity, deadline and one resource unit."""

        if len(logical_effect_id) != 64 or any(
            ch not in "0123456789abcdef" for ch in logical_effect_id
        ):
            raise ValueError("logical_effect_id must be a lowercase SHA-256 digest")
        if prepared.stable_call_id != logical_effect_id:
            raise ValueError("v6 read stable_call_id must equal logical_effect_id")
        if attempt_no not in {1, 2}:
            raise ValueError("v6 read attempt_no must be 1 or 2")
        if resource_kind not in {"query", "fetch", "browser"}:
            raise ValueError("unsupported v6 read resource kind")
        effect_id = hashlib.sha256(
            f"{fence.run_id}|{logical_effect_id}|{attempt_no}".encode("utf-8")
        ).hexdigest()
        fingerprint = hashlib.sha256(
            f"{logical_effect_id}|{attempt_no}".encode("utf-8")
        ).hexdigest()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            existing = await (
                await db.execute(
                    """SELECT effect.*,head.canonical_effect_id FROM workflow_effects effect
                    LEFT JOIN workflow_effect_attempt_heads head
                    ON head.logical_effect_id=effect.logical_effect_id
                    WHERE effect.run_id=? AND effect.logical_effect_id=? AND effect.attempt_no=?""",
                    (fence.run_id, logical_effect_id, attempt_no),
                )
            ).fetchone()
            if existing is not None:
                raw = json.loads(existing["outcome_json"]) if existing["outcome_json"] else None
                action = (
                    EffectAction.REUSE
                    if existing["status"] == "committed"
                    and existing["canonical_effect_id"] == existing["effect_id"]
                    else EffectAction.IN_FLIGHT
                )
                await db.commit()
                return IdempotentReadAttempt(
                    action=action,
                    effect_id=str(existing["effect_id"]),
                    logical_effect_id=logical_effect_id,
                    attempt_no=attempt_no,
                    status=EffectStatus(str(existing["status"])),
                    canonical_result_ref=(
                        str(raw["canonical_result_ref"])
                        if action is EffectAction.REUSE and raw
                        else None
                    ),
                    dependency_refs=(
                        tuple(str(x) for x in raw["dependency_refs"])
                        if action is EffectAction.REUSE and raw
                        else ()
                    ),
                    result_kind=(
                        str(raw["result_kind"])
                        if action is EffectAction.REUSE and raw
                        else None
                    ),
                )
            deadline = await (
                await db.execute(
                    """SELECT * FROM workflow_research_deadlines
                    WHERE deadline_id=? AND run_id=?""",
                    (deadline_id, fence.run_id),
                )
            ).fetchone()
            if deadline is None or int(deadline["revision"]) != deadline_revision:
                raise EffectStateConflict("v6 read deadline revision differs")
            if str(deadline["status"]) != "open":
                raise EffectStateConflict("v6 read deadline is terminal")
            deadline_state = self._v6_deadline_from_row(deadline)
            deadline_lease: DeadlineLeaseV1 | None = None
            if (deadline_now_wall is None) != (deadline_now_monotonic_ns is None):
                raise ValueError("v6 read deadline observations must be supplied together")
            if deadline_now_wall is not None and deadline_now_monotonic_ns is not None:
                transition = resume_deadline(
                    deadline_state,
                    now_wall=deadline_now_wall,
                    now_monotonic_ns=deadline_now_monotonic_ns,
                )
                await self._persist_v6_deadline_transition(
                    db,
                    run_id=fence.run_id,
                    previous_revision=deadline_state.revision,
                    state=transition.state,
                )
                deadline_state = transition.state
                deadline_lease = transition.lease
                deadline_revision = deadline_state.revision
                if deadline_lease is None:
                    await db.commit()
                    raise EffectStateConflict("v6 read deadline expired before dispatch")
                if fault_injector is not None:
                    fault_injector("after_deadline_before_effect")
            head = await (
                await db.execute(
                    "SELECT * FROM workflow_effect_attempt_heads WHERE logical_effect_id=?",
                    (logical_effect_id,),
                )
            ).fetchone()
            if head is None:
                if attempt_no != 1:
                    raise EffectStateConflict("first v6 read attempt must be 1")
            elif (
                str(head["run_id"]) != fence.run_id
                or attempt_no != int(head["latest_attempt_no"]) + 1
                or head["canonical_effect_id"] is not None
            ):
                raise EffectStateConflict("v6 read attempt does not extend its head")
            budget_id = hashlib.sha256(
                f"{fence.run_id}|{resource_policy_hash}|{resource_kind}".encode("utf-8")
            ).hexdigest()
            budget = await (
                await db.execute(
                    "SELECT * FROM workflow_research_resource_budgets WHERE run_id=? AND resource_kind=?",
                    (fence.run_id, resource_kind),
                )
            ).fetchone()
            if budget is None:
                await db.execute(
                    """INSERT INTO workflow_research_resource_budgets(
                    budget_id,run_id,policy_hash,resource_kind,hard_limit)
                    VALUES(?,?,?,?,?)""",
                    (budget_id, fence.run_id, resource_policy_hash, resource_kind, resource_hard_limit),
                )
            elif (
                str(budget["policy_hash"]) != resource_policy_hash
                or int(budget["hard_limit"]) != resource_hard_limit
            ):
                raise EffectStateConflict("v6 read route budget identity differs")
            budget = await (
                await db.execute(
                    "SELECT * FROM workflow_research_resource_budgets WHERE budget_id=?",
                    (budget_id,),
                )
            ).fetchone()
            assert budget is not None
            if int(budget["reserved"]) + int(budget["consumed"]) >= int(budget["hard_limit"]):
                raise BudgetReservationExceeded(f"v6 {resource_kind} route budget exhausted")
            now = self._clock()
            policy_json = canonical_json(
                {
                    "policy_id": "deep-research-v6-page-read-v1",
                    "version": "v1",
                    "kind": EffectKind.IDEMPOTENT_READ.value,
                    "max_attempts": 2,
                    "reusable_across_branches": False,
                    "deadline_id": deadline_id,
                    "deadline_revision": deadline_revision,
                }
            )
            supersedes = None
            if head is not None:
                supersedes = hashlib.sha256(
                    f"{fence.run_id}|{logical_effect_id}|{attempt_no - 1}".encode("utf-8")
                ).hexdigest()
            await db.execute(
                """INSERT INTO workflow_effects(
                effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,
                policy_json,args_hash,status,prepared_json,artifact_refs_json,
                lease_epoch,started_at,updated_at,logical_effect_id,attempt_no,
                supersedes_effect_id) VALUES(?,?,?,?,?,?,?,'running',?,'[]',?,?,?,?,?,?)""",
                (
                    effect_id, fence.run_id, node_execution_id, fingerprint,
                    prepared.effect_type, policy_json, prepared.args_hash,
                    canonical_json(prepared.to_dict()), fence.lease_epoch, now, now,
                    logical_effect_id, attempt_no, supersedes,
                ),
            )
            await db.execute(
                "INSERT INTO workflow_node_effects(node_execution_id,effect_id) VALUES(?,?)",
                (node_execution_id, effect_id),
            )
            reservation_id = hashlib.sha256(
                f"{effect_id}|{budget_id}|{deadline_id}".encode("utf-8")
            ).hexdigest()
            await db.execute(
                """INSERT INTO workflow_research_resource_reservations(
                reservation_id,budget_id,deadline_id,effect_id,amount_reserved,
                status,created_at,updated_at) VALUES(?,?,?,?,1,'reserved',?,?)""",
                (reservation_id, budget_id, deadline_id, effect_id, now, now),
            )
            await db.execute(
                """UPDATE workflow_research_resource_budgets
                SET reserved=reserved+1,revision=revision+1 WHERE budget_id=?""",
                (budget_id,),
            )
            if head is None:
                await db.execute(
                    """INSERT INTO workflow_effect_attempt_heads(
                    logical_effect_id,run_id,latest_attempt_no,canonical_effect_id,
                    policy_id,updated_at) VALUES(?,?,1,NULL,?,?)""",
                    (logical_effect_id, fence.run_id, "deep-research-v6-page-read-v1", now),
                )
            else:
                await db.execute(
                    """UPDATE workflow_effect_attempt_heads SET latest_attempt_no=?,updated_at=?
                    WHERE logical_effect_id=?""",
                    (attempt_no, now, logical_effect_id),
                )
            if fault_injector is not None:
                fault_injector("before_commit")
            await db.commit()
            if fault_injector is not None:
                fault_injector("after_commit")
            return IdempotentReadAttempt(
                EffectAction.EXECUTE, effect_id, logical_effect_id, attempt_no,
                EffectStatus.RUNNING,
                deadline_state=deadline_state,
                deadline_lease=deadline_lease,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def commit_idempotent_read_attempt(
        self,
        fence: RunFence,
        *,
        effect_id: str,
        canonical_result_ref: str,
        dependency_refs: Sequence[str],
        result_kind: str,
        amount_actual: int = 1,
        deadline_now_wall: datetime | None = None,
        deadline_now_monotonic_ns: int | None = None,
    ) -> IdempotentReadAttempt:
        if result_kind not in {"official_search", "page_extraction"}:
            raise ValueError("unsupported v6 read result kind")
        wire_refs = tuple(sorted(set(str(ref) for ref in dependency_refs)))
        if canonical_result_ref not in wire_refs:
            raise ValueError("canonical v6 read result must belong to its closure")
        digests = tuple(ref.removeprefix("sha256:") for ref in wire_refs)
        if any(len(digest) != 64 for digest in digests):
            raise ValueError("v6 read dependency refs must be wire SHA-256 refs")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            row = await (
                await db.execute("SELECT * FROM workflow_effects WHERE effect_id=?", (effect_id,))
            ).fetchone()
            if row is None or row["logical_effect_id"] is None:
                raise EffectStateConflict("v6 read effect is missing")
            head = await (
                await db.execute(
                    "SELECT * FROM workflow_effect_attempt_heads WHERE logical_effect_id=?",
                    (row["logical_effect_id"],),
                )
            ).fetchone()
            if row["status"] == "committed" and head is not None and head["canonical_effect_id"] == effect_id:
                raw = json.loads(row["outcome_json"])
                await db.commit()
                return IdempotentReadAttempt(
                    EffectAction.REUSE, effect_id, str(row["logical_effect_id"]),
                    int(row["attempt_no"]), EffectStatus.COMMITTED,
                    str(raw["canonical_result_ref"]),
                    tuple(str(x) for x in raw["dependency_refs"]),
                    str(raw["result_kind"]),
                )
            if row["status"] != "running" or head is None or head["canonical_effect_id"] is not None:
                raise EffectStateConflict("v6 read effect cannot become canonical")
            if (deadline_now_wall is None) != (deadline_now_monotonic_ns is None):
                raise ValueError("v6 read deadline observations must be supplied together")
            deadline_state: DurableDeadlineV1 | None = None
            deadline_lease: DeadlineLeaseV1 | None = None
            if deadline_now_wall is not None and deadline_now_monotonic_ns is not None:
                deadline_row = await (
                    await db.execute(
                        """SELECT deadline.* FROM workflow_research_deadlines deadline
                        JOIN workflow_research_resource_reservations reservation
                          ON reservation.deadline_id=deadline.deadline_id
                        WHERE reservation.effect_id=? AND deadline.run_id=?""",
                        (effect_id, fence.run_id),
                    )
                ).fetchone()
                if deadline_row is None:
                    raise EffectStateConflict("v6 read deadline is missing at commit")
                deadline_state = self._v6_deadline_from_row(deadline_row)
                if deadline_state.status == "open":
                    transition = resume_deadline(
                        deadline_state,
                        now_wall=deadline_now_wall,
                        now_monotonic_ns=deadline_now_monotonic_ns,
                    )
                    await self._persist_v6_deadline_transition(
                        db,
                        run_id=fence.run_id,
                        previous_revision=deadline_state.revision,
                        state=transition.state,
                    )
                    deadline_state = transition.state
                    deadline_lease = transition.lease
            for digest in digests:
                blob = await (
                    await db.execute("SELECT 1 FROM workflow_blobs WHERE sha256=?", (digest,))
                ).fetchone()
                owner = await (
                    await db.execute(
                        """SELECT 1 FROM workflow_blob_refs
                        WHERE sha256=? AND owner_kind='run_staging' AND owner_id=?""",
                        (digest, fence.run_id),
                    )
                ).fetchone()
                if blob is None or owner is None:
                    raise StagingPreconditionFailed("v6 read closure lacks same-run staging owner")
            outcome = {
                "schema_version": 1,
                "canonical_result_ref": canonical_result_ref,
                "dependency_refs": list(wire_refs),
                "result_kind": result_kind,
            }
            now = self._clock()
            await db.execute(
                """UPDATE workflow_effects SET status='committed',outcome_json=?,
                artifact_refs_json=?,updated_at=?,ended_at=? WHERE effect_id=?""",
                (canonical_json(outcome), canonical_json(list(digests)), now, now, effect_id),
            )
            for digest in digests:
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at) VALUES(?,'effect',?,?)""",
                    (digest, effect_id, now),
                )
                await db.execute(
                    """DELETE FROM workflow_blob_refs
                    WHERE sha256=? AND owner_kind='run_staging' AND owner_id=?""",
                    (digest, fence.run_id),
                )
            await db.execute(
                """UPDATE workflow_effect_attempt_heads SET canonical_effect_id=?,updated_at=?
                WHERE logical_effect_id=? AND canonical_effect_id IS NULL""",
                (effect_id, now, row["logical_effect_id"]),
            )
            reservation = await (
                await db.execute(
                    """SELECT reservation.*,budget.reserved,budget.consumed
                    FROM workflow_research_resource_reservations reservation
                    JOIN workflow_research_resource_budgets budget USING(budget_id)
                    WHERE reservation.effect_id=?""",
                    (effect_id,),
                )
            ).fetchone()
            if reservation is None or reservation["status"] != "reserved":
                raise EffectStateConflict("v6 read resource reservation is missing")
            actual = max(0, min(int(amount_actual), int(reservation["amount_reserved"])))
            await db.execute(
                """UPDATE workflow_research_resource_reservations SET
                amount_actual=?,status='committed',updated_at=? WHERE effect_id=?""",
                (actual, now, effect_id),
            )
            await db.execute(
                """UPDATE workflow_research_resource_budgets SET
                reserved=reserved-?,consumed=consumed+?,revision=revision+1
                WHERE budget_id=?""",
                (int(reservation["amount_reserved"]), actual, reservation["budget_id"]),
            )
            await db.commit()
            return IdempotentReadAttempt(
                EffectAction.REUSE, effect_id, str(row["logical_effect_id"]),
                int(row["attempt_no"]), EffectStatus.COMMITTED,
                canonical_result_ref, wire_refs, result_kind,
                deadline_state=deadline_state,
                deadline_lease=deadline_lease,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def reconcile_idempotent_read_for_retry(
        self,
        fence: RunFence,
        *,
        logical_effect_id: str,
        attempt_no: int,
    ) -> IdempotentReadAttempt:
        """Conservatively settle one stale read attempt before a separate retry begin.

        This transaction never creates the successor attempt.  Keeping the head
        on the real failed row prevents a crash from manufacturing an attempt
        gap; the later ``begin_idempotent_read_attempt(attempt_no + 1)`` is the
        only operation allowed to advance it.
        """

        if len(logical_effect_id) != 64 or any(
            character not in "0123456789abcdef" for character in logical_effect_id
        ):
            raise ValueError("logical_effect_id must be a lowercase SHA-256 digest")
        if attempt_no not in {1, 2}:
            raise ValueError("v6 read attempt_no must be 1 or 2")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            head = await (
                await db.execute(
                    "SELECT * FROM workflow_effect_attempt_heads WHERE logical_effect_id=?",
                    (logical_effect_id,),
                )
            ).fetchone()
            row = await (
                await db.execute(
                    """SELECT * FROM workflow_effects
                    WHERE run_id=? AND logical_effect_id=? AND attempt_no=?""",
                    (fence.run_id, logical_effect_id, attempt_no),
                )
            ).fetchone()
            if row is None or head is None or str(head["run_id"]) != fence.run_id:
                raise EffectStateConflict("v6 read attempt/head is missing")
            if (
                head["canonical_effect_id"] is not None
                and str(head["canonical_effect_id"]) == str(row["effect_id"])
                and str(row["status"]) == "committed"
            ):
                outcome = json.loads(str(row["outcome_json"]))
                await db.commit()
                return IdempotentReadAttempt(
                    EffectAction.REUSE,
                    str(row["effect_id"]),
                    logical_effect_id,
                    attempt_no,
                    EffectStatus.COMMITTED,
                    str(outcome["canonical_result_ref"]),
                    tuple(str(item) for item in outcome["dependency_refs"]),
                    str(outcome["result_kind"]),
                )
            if str(row["status"]) == "failed":
                await db.commit()
                return IdempotentReadAttempt(
                    EffectAction.FAILED,
                    str(row["effect_id"]),
                    logical_effect_id,
                    attempt_no,
                    EffectStatus.FAILED,
                )
            if int(head["latest_attempt_no"]) != attempt_no or head["canonical_effect_id"] is not None:
                raise EffectStateConflict("v6 read head no longer names the stale attempt")
            if str(row["status"]) not in {"running", "uncertain"}:
                raise EffectStateConflict("v6 read attempt cannot be reconciled")
            if int(row["lease_epoch"]) >= fence.lease_epoch:
                raise EffectStateConflict("current-owner v6 read attempt is still in flight")
            reservation = await (
                await db.execute(
                    """SELECT reservation.*,budget.reserved,budget.consumed
                    FROM workflow_research_resource_reservations reservation
                    JOIN workflow_research_resource_budgets budget USING(budget_id)
                    WHERE reservation.effect_id=?""",
                    (row["effect_id"],),
                )
            ).fetchone()
            if reservation is None or str(reservation["status"]) != "reserved":
                raise EffectStateConflict("stale v6 read reservation is not reserved")
            amount = int(reservation["amount_reserved"])
            now = self._clock()
            outcome = {
                "schema_version": 1,
                "status": "failed",
                "reason_code": "stale_owner_epoch",
            }
            await db.execute(
                """UPDATE workflow_effects SET status='failed',outcome_json=?,
                updated_at=?,ended_at=? WHERE effect_id=? AND status IN ('running','uncertain')""",
                (canonical_json(outcome), now, now, row["effect_id"]),
            )
            await db.execute(
                """UPDATE workflow_research_resource_reservations SET
                amount_actual=amount_reserved,status='committed',updated_at=?
                WHERE effect_id=? AND status='reserved'""",
                (now, row["effect_id"]),
            )
            budget_cursor = await db.execute(
                """UPDATE workflow_research_resource_budgets SET
                reserved=reserved-?,consumed=consumed+?,revision=revision+1
                WHERE budget_id=? AND reserved>=?""",
                (amount, amount, reservation["budget_id"], amount),
            )
            if budget_cursor.rowcount != 1:
                raise EffectStateConflict("stale v6 read budget settlement CAS lost")
            await db.commit()
            return IdempotentReadAttempt(
                EffectAction.FAILED,
                str(row["effect_id"]),
                logical_effect_id,
                attempt_no,
                EffectStatus.FAILED,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def assert_canonical_v6_read_result_owner(
        self,
        *,
        run_id: str,
        canonical_result_ref: str,
        result_kind: str,
    ) -> None:
        """Require a typed result ref to be the same-run canonical effect head."""

        if result_kind not in {"official_search", "page_extraction"}:
            raise ValueError("unsupported v6 read result kind")
        digest = canonical_result_ref.removeprefix("sha256:")
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("canonical_result_ref must be a wire SHA-256 ref")
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT 1 FROM workflow_effects effect
                    JOIN workflow_effect_attempt_heads head
                      ON head.logical_effect_id=effect.logical_effect_id
                     AND head.canonical_effect_id=effect.effect_id
                    JOIN workflow_blob_refs owner
                      ON owner.sha256=? AND owner.owner_kind='effect'
                     AND owner.owner_id=effect.effect_id
                    WHERE effect.run_id=? AND effect.status='committed'
                      AND json_extract(effect.outcome_json,'$.canonical_result_ref')=?
                      AND json_extract(effect.outcome_json,'$.result_kind')=?
                    LIMIT 1""",
                    (digest, run_id, canonical_result_ref, result_kind),
                )
            ).fetchone()
            if row is None:
                raise StagingPreconditionFailed(
                    "v6 read result is not owned by the same-run canonical effect head"
                )
        finally:
            await db.close()

    async def _begin(
        self,
        fence: RunFence,
        *,
        node_execution_id: str,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        logical_effect_key: str,
        prepared: PreparedToolCall,
        policy: EffectPolicy,
        reuse_checkpoint: CheckpointEffectLink | None,
        reservation_ttl_seconds: float,
        budget_reservation: tuple[str, int, int, int, str] | None,
        record_budget_denial: bool = False,
        denial_artifact_refs: Sequence[str] = (),
        resource_budget_kind: str | None = None,
    ) -> BeginEffectResult:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self.begin_tx(
                db,
                fence,
                node_execution_id=node_execution_id,
                workflow_name=workflow_name,
                workflow_version=workflow_version,
                node_id=node_id,
                logical_effect_key=logical_effect_key,
                prepared=prepared,
                policy=policy,
                reuse_checkpoint=reuse_checkpoint,
                reservation_ttl_seconds=reservation_ttl_seconds,
                budget_reservation=budget_reservation,
                record_budget_denial=record_budget_denial,
                denial_artifact_refs=denial_artifact_refs,
                resource_budget_kind=resource_budget_kind,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def begin_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        *,
        node_execution_id: str,
        workflow_name: str,
        workflow_version: str,
        node_id: str,
        logical_effect_key: str,
        prepared: PreparedToolCall,
        policy: EffectPolicy,
        reuse_checkpoint: CheckpointEffectLink | None = None,
        reservation_ttl_seconds: float = 90.0,
        budget_reservation: tuple[str, int, int, int, str] | None = None,
        record_budget_denial: bool = False,
        denial_artifact_refs: Sequence[str] = (),
        resource_budget_kind: str | None = None,
    ) -> BeginEffectResult:
        """Claim or reuse an effect on the caller-owned transaction.

        The caller owns transaction boundaries and connection lifetime.  This
        primitive deliberately performs no BEGIN, commit, rollback, or close.
        """

        fingerprint = effect_fingerprint(
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
            logical_effect_key=logical_effect_key,
            effect_type=prepared.effect_type,
            args_hash=prepared.args_hash,
            policy_version=policy.version,
        )
        await self._assert_fence(db, fence)
        try:
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_effects WHERE run_id=? AND effect_fingerprint=?",
                    (fence.run_id, fingerprint),
                )
            ).fetchone()
            if existing is not None:
                if existing["args_hash"] != prepared.args_hash:
                    raise EffectStateConflict("effect fingerprint resolved to different arguments")
                if budget_reservation is not None:
                    reservation = await (
                        await db.execute(
                            "SELECT * FROM workflow_effect_budget_reservations WHERE effect_id=?",
                            (existing["effect_id"],),
                        )
                    ).fetchone()
                    ledger_kind, input_reserved, output_reserved, cost_reserved, _ = budget_reservation
                    if reservation is None or (
                        reservation["run_id"] != fence.run_id
                        or reservation["ledger_kind"] != ledger_kind
                        or int(reservation["input_reserved"]) != input_reserved
                        or int(reservation["output_reserved"]) != output_reserved
                        or int(reservation["cost_reserved_micros"]) != cost_reserved
                    ):
                        raise EffectStateConflict(
                            "existing effect has a different or missing budget reservation"
                        )
                if (
                    existing["status"] == EffectStatus.RUNNING
                    and int(existing["lease_epoch"]) != fence.lease_epoch
                ):
                    now = self._clock()
                    await db.execute(
                        """UPDATE workflow_effects SET status='uncertain',outcome_json=?,updated_at=?
                        WHERE effect_id=? AND status='running'""",
                        (
                            canonical_json(
                                NormalizedToolOutcome.malformed(
                                    "effect owner epoch changed before completion"
                                ).to_dict()
                            ),
                            now,
                            existing["effect_id"],
                        ),
                    )
                    target_rows = await (
                        await db.execute(
                            "SELECT reservation_key,target_json FROM workflow_effect_targets WHERE effect_id=?",
                            (existing["effect_id"],),
                        )
                    ).fetchall()
                    for target_row in target_rows:
                        target_payload = json.loads(target_row["target_json"])
                        if target_payload["status"] not in {"committed", "rolled_back"}:
                            target_payload["status"] = "uncertain"
                            await db.execute(
                                """UPDATE workflow_effect_targets SET target_json=?
                                WHERE effect_id=? AND reservation_key=?""",
                                (
                                    canonical_json(target_payload),
                                    existing["effect_id"],
                                    target_row["reservation_key"],
                                ),
                            )
                    existing = await (
                        await db.execute(
                            "SELECT * FROM workflow_effects WHERE effect_id=?",
                            (existing["effect_id"],),
                        )
                    ).fetchone()
                    assert existing is not None
                await db.execute(
                    "INSERT OR IGNORE INTO workflow_node_effects(node_execution_id,effect_id) VALUES(?,?)",
                    (node_execution_id, existing["effect_id"]),
                )
                record = self._record(existing)
                return BeginEffectResult(self._existing_action(record.status), record)

            reusable = None
            if (
                budget_reservation is None
                and
                reuse_checkpoint is not None
                and policy.kind is EffectKind.DETERMINISTIC_REUSABLE
                and policy.reusable_across_branches
            ):
                reusable = await self._find_checkpoint_effect(db, reuse_checkpoint, fingerprint)
            if reusable is not None:
                await db.execute(
                    "INSERT OR IGNORE INTO workflow_node_effects(node_execution_id,effect_id) VALUES(?,?)",
                    (node_execution_id, reusable["effect_id"]),
                )
                return BeginEffectResult(EffectAction.REUSE, self._record(reusable))

            effect_id = hashlib.sha256(
                f"{fence.run_id}|{fingerprint}".encode("utf-8")
            ).hexdigest()
            now = self._clock()
            policy_json = canonical_json(
                {
                    "policy_id": policy.policy_id,
                    "version": policy.version,
                    "kind": policy.kind.value,
                    "max_attempts": policy.max_attempts,
                    "reusable_across_branches": policy.reusable_across_branches,
                }
            )
            resource_budget = None
            resource_budget_denied = False
            if resource_budget_kind is not None:
                resource_budget = await (
                    await db.execute(
                        """SELECT * FROM workflow_research_resource_budgets
                        WHERE run_id=? AND resource_kind=?""",
                        (fence.run_id, resource_budget_kind),
                    )
                ).fetchone()
                if resource_budget is None:
                    raise EffectStateConflict(
                        f"v6 {resource_budget_kind} route budget is missing"
                    )
                resource_budget_denied = (
                    int(resource_budget["reserved"])
                    + int(resource_budget["consumed"])
                    >= int(resource_budget["hard_limit"])
                )
            if budget_reservation is not None:
                ledger_kind, input_reserved, output_reserved, cost_reserved, capability_key = (
                    budget_reservation
                )
                limits = await self._budget_limits_for_update(
                    db, fence.run_id, capability_key
                )
                charged = await self._charged_budget_for_update(db, fence.run_id, ledger_kind)
                requested = (input_reserved, output_reserved, cost_reserved)
                capability_budget_denied = any(
                    charged_value + requested_value > limit
                    for charged_value, requested_value, limit in zip(charged, requested, limits)
                )
                if resource_budget_denied or capability_budget_denied:
                    if record_budget_denial:
                        if prepared.prepared_targets:
                            raise EffectStateConflict(
                                "recorded budget denial cannot own prepared targets"
                            )
                        denied = NormalizedToolOutcome.failure(
                            "budget_denied",
                            (
                                "effect round exceeds frozen route resource budget"
                                if resource_budget_denied
                                else "effect reservation exceeds immutable run capability"
                            ),
                        )
                        await db.execute(
                            """INSERT INTO workflow_effects(
                            effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,
                            policy_json,args_hash,status,outcome_json,prepared_json,artifact_refs_json,
                            lease_epoch,started_at,updated_at,ended_at
                            ) VALUES(?,?,?,?,?,?,?,'failed',?,?,?, ?,?,?,?)""",
                            (
                                effect_id,
                                fence.run_id,
                                node_execution_id,
                                fingerprint,
                                prepared.effect_type,
                                policy_json,
                                prepared.args_hash,
                                canonical_json(denied.to_dict()),
                                canonical_json(prepared.to_dict()),
                                canonical_json(list(denial_artifact_refs)),
                                fence.lease_epoch,
                                now,
                                now,
                                now,
                            ),
                        )
                        await db.execute(
                            """INSERT INTO workflow_effect_budget_reservations(
                            effect_id,run_id,ledger_kind,input_reserved,output_reserved,
                            cost_reserved_micros,status,dispatch_state,created_at,updated_at
                            ) VALUES(?,?,?,?,?,?,'released','not_started',?,?)""",
                            (
                                effect_id,
                                fence.run_id,
                                ledger_kind,
                                input_reserved,
                                output_reserved,
                                cost_reserved,
                                now,
                                now,
                            ),
                        )
                        await db.execute(
                            """INSERT INTO workflow_node_effects(
                            node_execution_id,effect_id) VALUES(?,?)""",
                            (node_execution_id, effect_id),
                        )
                        for artifact_ref in sorted(set(denial_artifact_refs)):
                            digest = str(artifact_ref).removeprefix("sha256:")
                            blob = await (
                                await db.execute(
                                    "SELECT 1 FROM workflow_blobs WHERE sha256=?", (digest,)
                                )
                            ).fetchone()
                            if blob is None:
                                raise StagingPreconditionFailed(
                                    f"budget denial dependency is not registered: {digest}"
                                )
                            await db.execute(
                                """INSERT OR IGNORE INTO workflow_blob_refs(
                                sha256,owner_kind,owner_id,created_at
                                ) VALUES(?,'effect',?,?)""",
                                (digest, effect_id, now),
                            )
                        row = await (
                            await db.execute(
                                "SELECT * FROM workflow_effects WHERE effect_id=?",
                                (effect_id,),
                            )
                        ).fetchone()
                        assert row is not None
                        return BeginEffectResult(EffectAction.FAILED, self._record(row))
                    raise BudgetReservationExceeded(
                        f"{ledger_kind} reservation exceeds run capability snapshot"
                    )
            await self._reserve_targets(db, fence, prepared, "claimed", reservation_ttl_seconds)
            await db.execute(
                """INSERT INTO workflow_effects(
                effect_id,run_id,node_execution_id,effect_fingerprint,effect_type,policy_json,
                args_hash,status,prepared_json,lease_epoch,started_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'running',?,?,?,?)""",
                (
                    effect_id,
                    fence.run_id,
                    node_execution_id,
                    fingerprint,
                    prepared.effect_type,
                    policy_json,
                    prepared.args_hash,
                    canonical_json(prepared.to_dict()),
                    fence.lease_epoch,
                    now,
                    now,
                ),
            )
            for target in prepared.prepared_targets:
                await db.execute(
                    "INSERT INTO workflow_effect_targets(effect_id,reservation_key,target_json) VALUES(?,?,?)",
                    (
                        effect_id,
                        target.reservation_key,
                        canonical_json({"status": "prepared", "target": target.to_dict()}),
                    ),
                )
            await db.execute(
                "INSERT INTO workflow_node_effects(node_execution_id,effect_id) VALUES(?,?)",
                (node_execution_id, effect_id),
            )
            if budget_reservation is not None:
                ledger_kind, input_reserved, output_reserved, cost_reserved, _ = budget_reservation
                await db.execute(
                    """INSERT INTO workflow_effect_budget_reservations(
                    effect_id,run_id,ledger_kind,input_reserved,output_reserved,
                    cost_reserved_micros,status,dispatch_state,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,'reserved','not_started',?,?)""",
                    (
                        effect_id,
                        fence.run_id,
                        ledger_kind,
                        input_reserved,
                        output_reserved,
                        cost_reserved,
                        now,
                        now,
                    ),
                )
            if resource_budget is not None:
                cursor = await db.execute(
                    """UPDATE workflow_research_resource_budgets
                    SET consumed=consumed+1,revision=revision+1
                    WHERE budget_id=? AND reserved+consumed<hard_limit""",
                    (resource_budget["budget_id"],),
                )
                if cursor.rowcount != 1:
                    raise EffectStateConflict("v6 LLM route budget consume CAS lost")
            row = await (
                await db.execute("SELECT * FROM workflow_effects WHERE effect_id=?", (effect_id,))
            ).fetchone()
            assert row is not None
            return BeginEffectResult(EffectAction.EXECUTE, self._record(row))
        except BaseException:
            raise

    async def commit(
        self,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome,
        *,
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
    ) -> EffectRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self.commit_tx(
                db,
                fence,
                effect_id,
                outcome,
                receipt_ref=receipt_ref,
                artifact_refs=artifact_refs,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def commit_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome,
        *,
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
    ) -> EffectRecord:
        """Settle an effect on the caller-owned transaction."""

        await self._assert_fence(db, fence)
        row = await self._effect_for_update(db, fence.run_id, effect_id)
        if int(row["lease_epoch"]) != fence.lease_epoch:
            raise StaleRunFence(f"effect belongs to stale epoch: {effect_id}")
        if row["status"] == EffectStatus.COMMITTED:
            return self._record(row)
        if row["status"] not in {EffectStatus.RUNNING, EffectStatus.UNCERTAIN}:
            raise EffectStateConflict(f"cannot commit effect in state {row['status']}")
        target_status = (
            EffectStatus.COMMITTED
            if outcome.state is ToolOutcomeState.SUCCESS
            else EffectStatus.FAILED
        )
        if target_status is EffectStatus.COMMITTED:
            await self._assert_staged_targets_committed(db, row)
        now = self._clock()
        await db.execute(
            """UPDATE workflow_effects SET status=?,outcome_json=?,receipt_ref=?,
            artifact_refs_json=?,updated_at=?,ended_at=? WHERE effect_id=?""",
            (
                target_status.value,
                canonical_json(outcome.to_dict()),
                receipt_ref,
                canonical_json(list(artifact_refs)),
                now,
                now,
                effect_id,
            ),
        )
        reservation_status = (
            "committed" if target_status is EffectStatus.COMMITTED else "released"
        )
        await db.execute(
            """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
            WHERE reservation_key IN (SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?)""",
            (reservation_status, now, effect_id),
        )
        result = await self._effect_for_update(db, fence.run_id, effect_id)
        return self._record(result)

    async def mark_upstream_started(
        self, fence: RunFence, effect_id: str
    ) -> BudgetReservationRecord:
        """CAS a reserved call immediately before entering provider transport."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            effect = await self._effect_for_update(db, fence.run_id, effect_id)
            if int(effect["lease_epoch"]) != fence.lease_epoch:
                raise StaleRunFence(f"effect belongs to stale epoch: {effect_id}")
            reservation = await self._budget_reservation_for_update(db, fence.run_id, effect_id)
            if reservation["status"] != "reserved":
                raise EffectStateConflict(
                    f"cannot dispatch budget reservation in state {reservation['status']}"
                )
            if reservation["dispatch_state"] == "not_started":
                now = self._clock()
                cursor = await db.execute(
                    """UPDATE workflow_effect_budget_reservations
                    SET dispatch_state='started',upstream_started_at=?,updated_at=?
                    WHERE effect_id=? AND status='reserved' AND dispatch_state='not_started'""",
                    (now, now, effect_id),
                )
                if cursor.rowcount != 1:
                    raise EffectStateConflict("upstream dispatch CAS lost")
                reservation = await self._budget_reservation_for_update(
                    db, fence.run_id, effect_id
                )
            await db.commit()
            return self._budget_reservation_record(reservation)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def commit_or_hold(
        self,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome,
        *,
        input_actual: int | None = None,
        output_actual: int | None = None,
        cost_actual_micros: int | None = None,
        receipt_ref: str | None = None,
        artifact_refs: Sequence[str] = (),
    ) -> EffectRecord:
        """Settle known usage or conservatively hold a dispatched unknown call."""

        actuals = (input_actual, output_actual, cost_actual_micros)
        if any(value is not None for value in actuals) and not all(
            value is not None for value in actuals
        ):
            raise ValueError("actual input, output and cost must be supplied together")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in actuals
            if value is not None
        ):
            raise ValueError("actual usage must be non-negative integers")

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            effect = await self._effect_for_update(db, fence.run_id, effect_id)
            reservation = await self._budget_reservation_for_update(db, fence.run_id, effect_id)
            if reservation["status"] != "reserved":
                await db.commit()
                return self._record(effect)

            usage_known = input_actual is not None
            if usage_known and int(effect["lease_epoch"]) != fence.lease_epoch:
                raise StaleRunFence(f"late usage belongs to stale epoch: {effect_id}")
            now = self._clock()
            if usage_known:
                reservation_status = "committed"
                effect_status = (
                    EffectStatus.COMMITTED
                    if outcome.state is ToolOutcomeState.SUCCESS
                    else EffectStatus.FAILED
                )
            elif reservation["dispatch_state"] == "started":
                reservation_status = "held_uncertain"
                effect_status = EffectStatus.UNCERTAIN
            else:
                reservation_status = "released"
                effect_status = EffectStatus.FAILED

            if effect_status is EffectStatus.COMMITTED:
                await self._assert_staged_targets_committed(db, effect)
            await db.execute(
                """UPDATE workflow_effect_budget_reservations
                SET input_actual=?,output_actual=?,cost_actual_micros=?,status=?,updated_at=?
                WHERE effect_id=? AND status='reserved'""",
                (
                    input_actual,
                    output_actual,
                    cost_actual_micros,
                    reservation_status,
                    now,
                    effect_id,
                ),
            )
            await db.execute(
                """UPDATE workflow_effects SET status=?,outcome_json=?,receipt_ref=?,
                artifact_refs_json=?,updated_at=?,ended_at=? WHERE effect_id=?""",
                (
                    effect_status.value,
                    canonical_json(outcome.to_dict()),
                    receipt_ref,
                    canonical_json(list(artifact_refs)),
                    now,
                    now,
                    effect_id,
                ),
            )
            # Blob dependencies supplied by the effect adapter become durable
            # effect-owned roots in the same transaction as the canonical
            # effect outcome.  Keeping this promotion here (instead of in the
            # blob store) closes the raw-result/outcome crash window: replay can
            # only observe either the old reservation or the committed outcome
            # together with all of its registered blob dependencies.
            for artifact_ref in sorted(set(artifact_refs)):
                digest = str(artifact_ref).strip()
                if digest.startswith("sha256:"):
                    digest = digest[7:]
                if len(digest) != 64 or any(
                    character not in "0123456789abcdef" for character in digest
                ):
                    raise ValueError("artifact_refs must contain lowercase SHA-256 refs")
                blob = await (
                    await db.execute(
                        "SELECT 1 FROM workflow_blobs WHERE sha256=?", (digest,)
                    )
                ).fetchone()
                if blob is None:
                    raise StagingPreconditionFailed(
                        f"effect blob dependency is not registered: {digest}"
                    )
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at
                    ) VALUES(?,'effect',?,?)""",
                    (digest, effect_id, now),
                )
            target_status = "committed" if effect_status is EffectStatus.COMMITTED else "released"
            await db.execute(
                """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
                WHERE reservation_key IN (
                    SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?
                )""",
                (target_status, now, effect_id),
            )
            result = await self._effect_for_update(db, fence.run_id, effect_id)
            await db.commit()
            return self._record(result)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def rebuild_budget_ledger(
        self,
        fence: RunFence,
        *,
        ledger_kind: str,
        capability_key: str = "research_llm_budget",
    ) -> BudgetLedgerSnapshot:
        """Rebuild the authoritative ledger projection from durable reservations."""

        if not ledger_kind or not capability_key:
            raise ValueError("ledger_kind and capability_key are required")
        db = await self._connect()
        try:
            await db.execute("BEGIN")
            await self._assert_fence(db, fence)
            limits = await self._budget_limits_for_update(db, fence.run_id, capability_key)
            charged = await self._charged_budget_for_update(db, fence.run_id, ledger_kind)
            rows = await (
                await db.execute(
                    """SELECT * FROM workflow_effect_budget_reservations
                    WHERE run_id=? AND ledger_kind=? ORDER BY created_at,effect_id""",
                    (fence.run_id, ledger_kind),
                )
            ).fetchall()
            await db.commit()
            return BudgetLedgerSnapshot(
                ledger_kind=ledger_kind,
                max_input_tokens=limits[0],
                max_output_tokens=limits[1],
                max_cost_micros=limits[2],
                charged_input_tokens=charged[0],
                charged_output_tokens=charged[1],
                charged_cost_micros=charged[2],
                reservations=tuple(self._budget_reservation_record(row) for row in rows),
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def mark_uncertain(self, fence: RunFence, effect_id: str, reason: str) -> EffectRecord:
        return await self._reconcile_write(
            fence,
            effect_id,
            EffectStatus.UNCERTAIN,
            NormalizedToolOutcome.malformed(reason),
        )

    async def reconcile(
        self,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome | None,
        *,
        evidence_verified: bool = False,
    ) -> EffectRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self.reconcile_tx(
                db,
                fence,
                effect_id,
                outcome,
                evidence_verified=evidence_verified,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def reconcile_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        effect_id: str,
        outcome: NormalizedToolOutcome | None,
        *,
        evidence_verified: bool = False,
    ) -> EffectRecord:
        """Reconcile an uncertain effect on the caller-owned transaction."""

        if outcome is None or outcome.state is ToolOutcomeState.MALFORMED:
            status = EffectStatus.UNCERTAIN
            normalized = outcome or NormalizedToolOutcome.malformed("reconciliation produced no outcome")
        elif outcome.state is ToolOutcomeState.SUCCESS and evidence_verified:
            status = EffectStatus.COMMITTED
            normalized = outcome
        elif outcome.state is ToolOutcomeState.FAILURE:
            status = EffectStatus.FAILED
            normalized = outcome
        else:
            status = EffectStatus.UNCERTAIN
            normalized = NormalizedToolOutcome.malformed("success lacks verified reconciliation evidence")
        return await self._reconcile_write_tx(db, fence, effect_id, status, normalized)

    async def finalize_late(
        self,
        fence: RunFence,
        *,
        effect_id: str,
        args_hash: str,
        lease_epoch: int,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord:
        """Record a thread result without ever advancing graph state under a stale fence."""

        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self.finalize_late_tx(
                db,
                fence,
                effect_id=effect_id,
                args_hash=args_hash,
                lease_epoch=lease_epoch,
                outcome=outcome,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def finalize_late_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        *,
        effect_id: str,
        args_hash: str,
        lease_epoch: int,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord:
        """Finalize a late result on the caller-owned transaction."""

        row = await self._effect_for_update(db, fence.run_id, effect_id)
        if row["args_hash"] != args_hash or int(row["lease_epoch"]) != lease_epoch:
            raise EffectStateConflict("late result identity does not match the prepared effect")
        current = await (
            await db.execute(
                """SELECT 1 FROM workflow_runs WHERE run_id=? AND lease_owner=? AND lease_epoch=?
                AND run_version=? AND status='running'""",
                (fence.run_id, fence.owner, fence.lease_epoch, fence.run_version),
            )
        ).fetchone()
        now = self._clock()
        if row["status"] in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            return self._record(row)
        if current is None:
            status = EffectStatus.LATE_ORPHAN
        elif outcome.state is ToolOutcomeState.SUCCESS:
            status = EffectStatus.COMMITTED
            await self._assert_staged_targets_committed(db, row)
        else:
            status = EffectStatus.FAILED
        await db.execute(
            """UPDATE workflow_effects SET status=?,outcome_json=?,updated_at=?,ended_at=?
            WHERE effect_id=?""",
            (status.value, canonical_json(outcome.to_dict()), now, now, effect_id),
        )
        if status in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            reservation_status = "committed" if status is EffectStatus.COMMITTED else "released"
            await db.execute(
                """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
                WHERE reservation_key IN (SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?)""",
                (reservation_status, now, effect_id),
            )
        result = await self._effect_for_update(db, fence.run_id, effect_id)
        return self._record(result)

    async def record_target_state(
        self,
        fence: RunFence,
        effect_id: str,
        reservation_key: str,
        *,
        expected: Sequence[str],
        status: str,
        evidence: Mapping[str, JsonValue] | None = None,
    ) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            effect = await self._effect_for_update(db, fence.run_id, effect_id)
            if int(effect["lease_epoch"]) != fence.lease_epoch:
                raise StaleRunFence(f"target belongs to stale effect epoch: {effect_id}")
            row = await (
                await db.execute(
                    "SELECT target_json FROM workflow_effect_targets WHERE effect_id=? AND reservation_key=?",
                    (effect_id, reservation_key),
                )
            ).fetchone()
            if row is None:
                raise KeyError(reservation_key)
            payload = json.loads(row["target_json"])
            if payload["status"] not in set(expected):
                raise EffectStateConflict(
                    f"target {reservation_key} is {payload['status']}, expected {tuple(expected)}"
                )
            transitions = {
                "prepared": {"staged", "rolled_back", "uncertain"},
                "staged": {"committing", "rolled_back", "uncertain"},
                "committing": {"committed", "uncertain"},
                "uncertain": {"staged", "committing", "committed", "rolled_back"},
                "committed": set(),
                "rolled_back": set(),
            }
            if status not in transitions.get(payload["status"], set()):
                raise EffectStateConflict(
                    f"invalid target transition {payload['status']} -> {status}"
                )
            payload["status"] = status
            if evidence is not None:
                copied = copy.deepcopy(dict(evidence))
                validate_json_value(copied)
                payload["evidence"] = copied
            await db.execute(
                "UPDATE workflow_effect_targets SET target_json=? WHERE effect_id=? AND reservation_key=?",
                (canonical_json(payload), effect_id, reservation_key),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def link_checkpoint(
        self,
        fence: RunFence,
        link: CheckpointEffectLink,
        effect_id: str,
        node_execution_id: str,
    ) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            row = await (
                await db.execute(
                    """SELECT effect.* FROM workflow_effects effect
                    WHERE effect.effect_id=? AND (
                        effect.run_id=? OR EXISTS(
                            SELECT 1 FROM workflow_node_effects node_link
                            WHERE node_link.node_execution_id=? AND node_link.effect_id=effect.effect_id
                        )
                    )""",
                    (effect_id, fence.run_id, node_execution_id),
                )
            ).fetchone()
            if row is None:
                raise EffectStateConflict("effect is not linked to the fenced run node")
            if row["status"] != EffectStatus.COMMITTED:
                raise EffectStateConflict("only committed effects may be linked to a checkpoint")
            checkpoint = await (
                await db.execute(
                    """SELECT 1 FROM workflow_checkpoints WHERE thread_id=? AND checkpoint_ns=?
                    AND checkpoint_id=? AND run_id=?""",
                    (link.thread_id, link.checkpoint_ns, link.checkpoint_id, fence.run_id),
                )
            ).fetchone()
            if checkpoint is None:
                raise EffectStateConflict("checkpoint link does not belong to the fenced run")
            await db.execute(
                """INSERT OR IGNORE INTO workflow_checkpoint_effects(
                thread_id,checkpoint_ns,checkpoint_id,effect_id,node_execution_id
                ) VALUES(?,?,?,?,?)""",
                (link.thread_id, link.checkpoint_ns, link.checkpoint_id, effect_id, node_execution_id),
            )
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def get(self, effect_id: str) -> EffectRecord | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute("SELECT * FROM workflow_effects WHERE effect_id=?", (effect_id,))
            ).fetchone()
            return self._record(row) if row is not None else None
        finally:
            await db.close()

    async def _reconcile_write(
        self,
        fence: RunFence,
        effect_id: str,
        status: EffectStatus,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            result = await self._reconcile_write_tx(
                db,
                fence,
                effect_id,
                status,
                outcome,
            )
            await db.commit()
            return result
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def _reconcile_write_tx(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        effect_id: str,
        status: EffectStatus,
        outcome: NormalizedToolOutcome,
    ) -> EffectRecord:
        await self._assert_fence(db, fence)
        row = await self._effect_for_update(db, fence.run_id, effect_id)
        if row["status"] in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            if row["status"] == status:
                return self._record(row)
            raise EffectStateConflict(
                f"terminal effect {effect_id} cannot transition from {row['status']} to {status.value}"
            )
        if status is EffectStatus.COMMITTED:
            await self._assert_staged_targets_committed(db, row)
        now = self._clock()
        ended_at = now if status in {EffectStatus.COMMITTED, EffectStatus.FAILED} else None
        await db.execute(
            """UPDATE workflow_effects SET status=?,outcome_json=?,updated_at=?,ended_at=?
            WHERE effect_id=?""",
            (status.value, canonical_json(outcome.to_dict()), now, ended_at, effect_id),
        )
        if status in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
            reservation_status = "committed" if status is EffectStatus.COMMITTED else "released"
            await db.execute(
                """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
                WHERE reservation_key IN (SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?)""",
                (reservation_status, now, effect_id),
            )
        result = await self._effect_for_update(db, fence.run_id, effect_id)
        return self._record(result)

    async def _reserve_targets(
        self,
        db: aiosqlite.Connection,
        fence: RunFence,
        prepared: PreparedToolCall,
        status: str,
        ttl_seconds: float,
    ) -> None:
        now = self._clock()
        for target in prepared.prepared_targets:
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_target_reservations WHERE reservation_key=?",
                    (target.reservation_key,),
                )
            ).fetchone()
            if row is None:
                await db.execute(
                    """INSERT INTO workflow_target_reservations(
                    reservation_key,display_path,run_id,call_id,status,lease_expires_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        target.reservation_key,
                        target.final_path,
                        fence.run_id,
                        prepared.stable_call_id,
                        status,
                        now + ttl_seconds,
                        now,
                    ),
                )
                continue
            same_call = row["run_id"] == fence.run_id and row["call_id"] == prepared.stable_call_id
            reusable_status = row["status"] in {"prepared", "claimed"}
            terminal_status = row["status"] in {"released", "committed"}
            if not ((same_call and reusable_status) or terminal_status):
                raise TargetReservationConflict(
                    f"target already reserved by {row['run_id']}:{row['call_id']}"
                )
            await db.execute(
                """UPDATE workflow_target_reservations SET display_path=?,run_id=?,call_id=?,
                status=?,lease_expires_at=?,updated_at=?
                WHERE reservation_key=?""",
                (
                    target.final_path,
                    fence.run_id,
                    prepared.stable_call_id,
                    status,
                    now + ttl_seconds,
                    now,
                    target.reservation_key,
                ),
            )

    @staticmethod
    async def _budget_limits_for_update(
        db: aiosqlite.Connection, run_id: str, capability_key: str
    ) -> tuple[int, int, int]:
        row = await (
            await db.execute(
                """SELECT capability.snapshot_json FROM workflow_runs run
                JOIN workflow_capabilities capability
                  ON capability.capability_hash=run.capability_hash
                WHERE run.run_id=?""",
                (run_id,),
            )
        ).fetchone()
        if row is None:
            raise EffectStateConflict(f"run capability snapshot is missing: {run_id}")
        snapshot = json.loads(str(row["snapshot_json"]))
        raw_limits = snapshot.get(capability_key) if isinstance(snapshot, dict) else None
        if not isinstance(raw_limits, dict):
            raise EffectStateConflict(
                f"run capability snapshot is missing budget key {capability_key}"
            )
        names = ("max_input_tokens", "max_output_tokens", "max_cost_micros")
        limits: list[int] = []
        for name in names:
            value = raw_limits.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EffectStateConflict(
                    f"run capability snapshot has invalid {capability_key}.{name}"
                )
            limits.append(value)
        return limits[0], limits[1], limits[2]

    @staticmethod
    async def _charged_budget_for_update(
        db: aiosqlite.Connection, run_id: str, ledger_kind: str
    ) -> tuple[int, int, int]:
        row = await (
            await db.execute(
                """SELECT
                COALESCE(SUM(CASE
                    WHEN status IN ('reserved','held_uncertain') THEN input_reserved
                    WHEN status='committed' THEN COALESCE(input_actual,input_reserved)
                    ELSE 0 END),0),
                COALESCE(SUM(CASE
                    WHEN status IN ('reserved','held_uncertain') THEN output_reserved
                    WHEN status='committed' THEN COALESCE(output_actual,output_reserved)
                    ELSE 0 END),0),
                COALESCE(SUM(CASE
                    WHEN status IN ('reserved','held_uncertain') THEN cost_reserved_micros
                    WHEN status='committed' THEN COALESCE(cost_actual_micros,cost_reserved_micros)
                    ELSE 0 END),0)
                FROM workflow_effect_budget_reservations
                WHERE run_id=? AND ledger_kind=?""",
                (run_id, ledger_kind),
            )
        ).fetchone()
        assert row is not None
        return int(row[0]), int(row[1]), int(row[2])

    @staticmethod
    async def _budget_reservation_for_update(
        db: aiosqlite.Connection, run_id: str, effect_id: str
    ) -> aiosqlite.Row:
        row = await (
            await db.execute(
                """SELECT * FROM workflow_effect_budget_reservations
                WHERE effect_id=? AND run_id=?""",
                (effect_id, run_id),
            )
        ).fetchone()
        if row is None:
            raise EffectStateConflict(f"effect has no budget reservation: {effect_id}")
        return row

    @staticmethod
    def _budget_reservation_record(row: aiosqlite.Row) -> BudgetReservationRecord:
        return BudgetReservationRecord(
            effect_id=str(row["effect_id"]),
            run_id=str(row["run_id"]),
            ledger_kind=str(row["ledger_kind"]),
            input_reserved=int(row["input_reserved"]),
            output_reserved=int(row["output_reserved"]),
            cost_reserved_micros=int(row["cost_reserved_micros"]),
            input_actual=(
                None if row["input_actual"] is None else int(row["input_actual"])
            ),
            output_actual=(
                None if row["output_actual"] is None else int(row["output_actual"])
            ),
            cost_actual_micros=(
                None
                if row["cost_actual_micros"] is None
                else int(row["cost_actual_micros"])
            ),
            status=str(row["status"]),
            dispatch_state=str(row["dispatch_state"]),
            upstream_started_at=(
                None
                if row["upstream_started_at"] is None
                else float(row["upstream_started_at"])
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    async def _effect_for_update(
        db: aiosqlite.Connection, run_id: str, effect_id: str
    ) -> aiosqlite.Row:
        row = await (
            await db.execute(
                "SELECT * FROM workflow_effects WHERE effect_id=? AND run_id=?",
                (effect_id, run_id),
            )
        ).fetchone()
        if row is None:
            raise KeyError(effect_id)
        return row

    @staticmethod
    async def _assert_staged_targets_committed(
        db: aiosqlite.Connection, effect: aiosqlite.Row
    ) -> None:
        policy = json.loads(effect["policy_json"])
        if policy["kind"] != EffectKind.STAGED_FILE.value:
            return
        rows = await (
            await db.execute(
                "SELECT target_json FROM workflow_effect_targets WHERE effect_id=?",
                (effect["effect_id"],),
            )
        ).fetchall()
        if not rows or any(json.loads(row["target_json"])["status"] != "committed" for row in rows):
            raise EffectStateConflict("staged file effect cannot commit before every target commits")

    @staticmethod
    async def _find_checkpoint_effect(
        db: aiosqlite.Connection,
        checkpoint: CheckpointEffectLink,
        fingerprint: str,
    ) -> aiosqlite.Row | None:
        return await (
            await db.execute(
                """WITH RECURSIVE ancestors(thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id) AS (
                    SELECT thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id
                    FROM workflow_checkpoints
                    WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
                    UNION ALL
                    SELECT parent.thread_id,parent.checkpoint_ns,parent.checkpoint_id,parent.parent_checkpoint_id
                    FROM workflow_checkpoints parent
                    JOIN ancestors child ON parent.thread_id=child.thread_id
                      AND parent.checkpoint_ns=child.checkpoint_ns
                      AND parent.checkpoint_id=child.parent_checkpoint_id
                )
                SELECT effect.* FROM ancestors
                JOIN workflow_checkpoint_effects link USING(thread_id,checkpoint_ns,checkpoint_id)
                JOIN workflow_effects effect ON effect.effect_id=link.effect_id
                WHERE effect.effect_fingerprint=? AND effect.status='committed'
                ORDER BY effect.ended_at DESC LIMIT 1""",
                (
                    checkpoint.thread_id,
                    checkpoint.checkpoint_ns,
                    checkpoint.checkpoint_id,
                    fingerprint,
                ),
            )
        ).fetchone()

    @staticmethod
    def _existing_action(status: EffectStatus) -> EffectAction:
        if status is EffectStatus.COMMITTED:
            return EffectAction.REUSE
        if status is EffectStatus.RUNNING:
            return EffectAction.IN_FLIGHT
        if status in {EffectStatus.UNCERTAIN, EffectStatus.LATE_ORPHAN}:
            return EffectAction.RECONCILE
        return EffectAction.FAILED

    @staticmethod
    def _record(row: aiosqlite.Row) -> EffectRecord:
        return EffectRecord(
            effect_id=str(row["effect_id"]),
            run_id=str(row["run_id"]),
            node_execution_id=str(row["node_execution_id"]),
            effect_fingerprint=str(row["effect_fingerprint"]),
            effect_type=str(row["effect_type"]),
            args_hash=str(row["args_hash"]),
            status=EffectStatus(row["status"]),
            prepared=PreparedToolCall.from_dict(json.loads(row["prepared_json"])),
            outcome=(
                NormalizedToolOutcome.from_dict(json.loads(row["outcome_json"]))
                if row["outcome_json"] is not None
                else None
            ),
            receipt_ref=row["receipt_ref"],
            artifact_refs=tuple(json.loads(row["artifact_refs_json"])),
            lease_epoch=int(row["lease_epoch"]),
        )


__all__ = [
    "BeginEffectResult",
    "BudgetLedgerSnapshot",
    "BudgetReservationExceeded",
    "BudgetReservationRecord",
    "CheckpointEffectLink",
    "EffectAction",
    "EffectExecutionContext",
    "EffectJournal",
    "EffectJournalError",
    "EffectRecord",
    "EffectStateConflict",
    "EffectStatus",
    "IdempotentReadAttempt",
    "NormalizedToolOutcome",
    "PreparedTarget",
    "PreparedToolCall",
    "StagedFileEvidence",
    "StagedFileLifecycle",
    "StagingPreconditionFailed",
    "TargetMode",
    "TargetReservationConflict",
    "ToolOutcomeState",
    "effect_fingerprint",
    "normalize_target_path",
    "prepared_args_hash",
    "target_reservation_key",
]
