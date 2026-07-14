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
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import aiosqlite

from .contracts import EffectKind, EffectPolicy, JsonValue, canonical_json, validate_json_value
from .store import RunFence, StaleRunFence, initialize_workflow_db


class EffectJournalError(RuntimeError):
    """Base class for durable effect contract failures."""


class TargetReservationConflict(EffectJournalError):
    """A final target is already reserved by a different stable call."""


class EffectStateConflict(EffectJournalError):
    """An effect or target cannot make the requested state transition."""


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
    input_blob_hashes: tuple[str, ...] = ()

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
        params = copy.deepcopy(dict(self.final_params))
        validate_json_value(params)
        blobs = tuple(sorted(str(item) for item in self.input_blob_hashes))
        expected = prepared_args_hash(self.tool_name, params, blobs)
        if self.args_hash != expected:
            raise ValueError("prepared call args_hash does not match final parameters")
        targets = tuple(self.prepared_targets)
        if len({item.reservation_key for item in targets}) != len(targets):
            raise ValueError("prepared call contains duplicate targets")
        object.__setattr__(self, "final_params", _freeze_json(params))
        object.__setattr__(self, "prepared_targets", targets)
        object.__setattr__(self, "input_blob_hashes", blobs)

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
        input_blob_hashes: Sequence[str] = (),
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
            input_blob_hashes=tuple(input_blob_hashes),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tool_name": self.tool_name,
            "stable_call_id": self.stable_call_id,
            "final_params": _thaw_json(self.final_params),
            "args_hash": self.args_hash,
            "prepared_targets": [target.to_dict() for target in self.prepared_targets],
            "tool_spec_version": self.tool_spec_version,
            "schema_hash": self.schema_hash,
            "permission_policy_version": self.permission_policy_version,
            "effect_type": self.effect_type,
            "input_blob_hashes": list(self.input_blob_hashes),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreparedToolCall":
        data = dict(value)
        data["prepared_targets"] = tuple(
            PreparedTarget.from_dict(item) for item in data.get("prepared_targets", ())
        )
        data["input_blob_hashes"] = tuple(data.get("input_blob_hashes", ()))
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
            await self._assert_fence(db, fence)
            await self._reserve_targets(db, fence, prepared, "prepared", ttl_seconds)
            await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

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
        fingerprint = effect_fingerprint(
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            node_id=node_id,
            logical_effect_key=logical_effect_key,
            effect_type=prepared.effect_type,
            args_hash=prepared.args_hash,
            policy_version=policy.version,
        )
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._assert_fence(db, fence)
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_effects WHERE run_id=? AND effect_fingerprint=?",
                    (fence.run_id, fingerprint),
                )
            ).fetchone()
            if existing is not None:
                if existing["args_hash"] != prepared.args_hash:
                    raise EffectStateConflict("effect fingerprint resolved to different arguments")
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
                await db.commit()
                record = self._record(existing)
                return BeginEffectResult(self._existing_action(record.status), record)

            reusable = None
            if (
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
                await db.commit()
                return BeginEffectResult(EffectAction.REUSE, self._record(reusable))

            await self._reserve_targets(
                db, fence, prepared, "claimed", reservation_ttl_seconds
            )
            effect_id = hashlib.sha256(f"{fence.run_id}|{fingerprint}".encode("utf-8")).hexdigest()
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
            row = await (
                await db.execute("SELECT * FROM workflow_effects WHERE effect_id=?", (effect_id,))
            ).fetchone()
            await db.commit()
            assert row is not None
            return BeginEffectResult(EffectAction.EXECUTE, self._record(row))
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

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
            await self._assert_fence(db, fence)
            row = await self._effect_for_update(db, fence.run_id, effect_id)
            if int(row["lease_epoch"]) != fence.lease_epoch:
                raise StaleRunFence(f"effect belongs to stale epoch: {effect_id}")
            if row["status"] == EffectStatus.COMMITTED:
                await db.commit()
                return self._record(row)
            if row["status"] not in {EffectStatus.RUNNING, EffectStatus.UNCERTAIN}:
                raise EffectStateConflict(f"cannot commit effect in state {row['status']}")
            target_status = EffectStatus.COMMITTED if outcome.state is ToolOutcomeState.SUCCESS else EffectStatus.FAILED
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
            reservation_status = "committed" if target_status is EffectStatus.COMMITTED else "released"
            await db.execute(
                """UPDATE workflow_target_reservations SET status=?,lease_expires_at=NULL,updated_at=?
                WHERE reservation_key IN (SELECT reservation_key FROM workflow_effect_targets WHERE effect_id=?)""",
                (reservation_status, now, effect_id),
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
        return await self._reconcile_write(fence, effect_id, status, normalized)

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
                await db.commit()
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
            await db.commit()
            return self._record(result)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

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
            await self._assert_fence(db, fence)
            row = await self._effect_for_update(db, fence.run_id, effect_id)
            if row["status"] in {EffectStatus.COMMITTED, EffectStatus.FAILED}:
                if row["status"] == status:
                    await db.commit()
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
            await db.commit()
            return self._record(result)
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

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
    "CheckpointEffectLink",
    "EffectAction",
    "EffectExecutionContext",
    "EffectJournal",
    "EffectJournalError",
    "EffectRecord",
    "EffectStateConflict",
    "EffectStatus",
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
