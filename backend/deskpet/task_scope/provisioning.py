# SPDX-License-Identifier: BUSL-1.1

"""Recoverable TaskScope task_home provisioning without workspace authority.

This state machine only materializes a task home and records a non-authoritative
workspace candidate.  Task 4 is the sole owner of workspace binding authority.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import platform
import stat
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import aiosqlite

from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_hash, canonical_json, identifier
from deskpet.task_scope.store import (
    CanonicalTaskScopeStore,
    TaskScopeConflict,
    TaskScopeNotFound,
    _uuid,
)

ProvisionMode = Literal["managed", "explicit"]
TrustedPathProvenance = Literal[
    "host_managed_policy", "trusted_user_selection", "trusted_project_picker"
]


class TaskScopeProvisionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TaskScopeProvisionRequest:
    task_scope_id: str
    title: str
    idempotency_key: str
    mode: ProvisionMode
    provenance: TrustedPathProvenance
    explicit_path: str | None = None
    allow_project_metadata: bool = True

    def __post_init__(self) -> None:
        identifier(self.task_scope_id, "task_scope_id", 512)
        identifier(self.title, "title", 4096)
        identifier(self.idempotency_key, "idempotency_key", 512)
        if self.mode == "managed":
            if (
                self.provenance != "host_managed_policy"
                or self.explicit_path is not None
            ):
                raise TaskScopeProvisionError("managed_provenance_rejected")
        elif self.mode == "explicit":
            if self.provenance not in {
                "trusted_user_selection",
                "trusted_project_picker",
            }:
                raise TaskScopeProvisionError("explicit_provenance_rejected")
            if (
                not isinstance(self.explicit_path, str)
                or not self.explicit_path.strip()
            ):
                raise TaskScopeProvisionError("explicit_path_required")
        else:
            raise TaskScopeProvisionError("provision_mode_rejected")
        if not isinstance(self.allow_project_metadata, bool):
            raise TaskScopeProvisionError("metadata_policy_invalid")


@dataclass(frozen=True, slots=True)
class TaskScopeProvisionReceipt:
    receipt_id: str
    provision_id: str
    task_scope_id: str
    task_home: str
    task_home_identity: str
    proposed_workspace_root: str | None
    proposed_workspace_identity: str | None
    materialization_root: str
    materialization_root_identity: str
    metadata_location: Literal["managed", "project", "app_data"]
    receipt_hash: str
    committed_at: float


@dataclass(frozen=True, slots=True)
class _ResolvedRequest:
    request: TaskScopeProvisionRequest
    provision_id: str
    request_hash: str
    managed_workspace_root: str | None
    proposed_workspace_root: str | None
    proposed_workspace_identity: str | None
    materialization_root: str
    materialization_root_identity: str
    task_home: str
    staging_path: str
    metadata_location: Literal["managed", "project", "app_data"]


class TaskScopeProvisioner:
    def __init__(
        self,
        db_path: str | Path,
        *,
        managed_workspace_root: str | Path | None = None,
        app_data_root: str | Path | None = None,
        home_directory: str | Path | None = None,
    ) -> None:
        self._store = CanonicalTaskScopeStore(db_path)
        self._managed_workspace_root = (
            None if managed_workspace_root is None else Path(managed_workspace_root)
        )
        self._app_data_root = (
            Path(app_data_root)
            if app_data_root is not None
            else Path(db_path).parent / "task-scope-homes"
        )
        self._home_directory = (
            Path(home_directory) if home_directory is not None else Path.home()
        )

    async def provision(
        self,
        request: TaskScopeProvisionRequest,
        *,
        fault_inject: Callable[[str], None] | None = None,
    ) -> TaskScopeProvisionReceipt:
        await self._store.initialize()
        resolved = await self._resolve(request)
        await self._reserve(resolved)
        self._fault(fault_inject, "after_reserved_commit")

        row = await self._load_provision(resolved.provision_id)
        if row["state"] == "committed":
            return await self._load_committed_receipt(
                resolved.provision_id, verify=True
            )
        if row["state"] == "failed_retryable":
            await self._transition(
                resolved.provision_id,
                expected="failed_retryable",
                target="reserved",
                reason_code="provision_retry_requested",
            )
            row = await self._load_provision(resolved.provision_id)

        if row["state"] == "reserved":
            self._fault(fault_inject, "before_filesystem_create")
            try:
                task_home_identity = self._materialize(resolved, fault_inject)
            except TaskScopeProvisionError as exc:
                await self._mark_failed(resolved.provision_id, exc.code)
                raise
            self._fault(fault_inject, "after_filesystem_create")
            await self._mark_filesystem_ready(
                resolved.provision_id,
                task_home_identity=task_home_identity,
                proposed_workspace_identity=(
                    task_home_identity
                    if request.mode == "managed"
                    else resolved.proposed_workspace_identity
                ),
            )
            self._fault(fault_inject, "after_filesystem_ready_commit")

        row = await self._load_provision(resolved.provision_id)
        if row["state"] == "filesystem_ready":
            self._verify_materialized(row)
            self._fault(fault_inject, "before_committed_receipt")
            await self._commit_receipt(resolved.provision_id)
            self._fault(fault_inject, "after_committed_receipt")
        return await self._load_committed_receipt(resolved.provision_id, verify=True)

    async def _resolve(self, request: TaskScopeProvisionRequest) -> _ResolvedRequest:
        async with self._store._connection() as db:
            scope = await self._store._fetchone(
                db,
                "SELECT title FROM task_scopes WHERE task_scope_id=?",
                (request.task_scope_id,),
            )
        if scope is None:
            raise TaskScopeNotFound(TaskScopeNotFound.code)
        if str(scope["title"]) != request.title:
            raise TaskScopeConflict("provision_title_scope_mismatch")
        _require_anchored_materialization_support()

        provision_id = _uuid(f"task-scope-provision:{request.idempotency_key}")
        suffix = hashlib.sha256(request.task_scope_id.encode("utf-8")).hexdigest()[:12]
        scope_component = _safe_scope_component(request.task_scope_id)
        directory_name = f"{_safe_title(request.title)}--{suffix}"
        managed_root: Path | None = None
        proposed_root: Path | None = None
        proposed_identity: str | None = None
        materialization_root: Path
        if request.mode == "managed":
            managed_root = self._configured_managed_root()
            managed_root = _ensure_managed_root(managed_root)
            materialization_root = managed_root
            task_home = managed_root / directory_name
            _require_real_child(managed_root, task_home)
            proposed_root = task_home
            location: Literal["managed", "project", "app_data"] = "managed"
        else:
            assert request.explicit_path is not None
            proposed_root = _strict_existing_directory(request.explicit_path)
            if proposed_root in {Path(proposed_root.anchor), Path.home().resolve()}:
                raise TaskScopeProvisionError("explicit_path_too_broad")
            proposed_identity = _filesystem_identity(proposed_root)
            if request.allow_project_metadata and os.access(
                proposed_root, os.W_OK | os.X_OK
            ):
                materialization_root = proposed_root
                task_home = (
                    proposed_root / ".simple-harness" / "task-scopes" / scope_component
                )
                _assert_no_existing_symlink(task_home)
                location = "project"
            else:
                app_root = _ensure_managed_root(self._app_data_root)
                materialization_root = app_root
                task_home = app_root / scope_component
                _require_real_child(app_root, task_home)
                location = "app_data"
        task_home = _canonical_nonexistent(task_home)
        staging = task_home.parent / f".{task_home.name}.provision-{provision_id[:12]}"
        payload = {
            "schema_version": 1,
            "task_scope_id": request.task_scope_id,
            "title": request.title,
            "idempotency_key": request.idempotency_key,
            "mode": request.mode,
            "provenance": request.provenance,
            "explicit_path": (
                str(proposed_root) if request.mode == "explicit" else None
            ),
            "allow_project_metadata": request.allow_project_metadata,
            "managed_workspace_root": None
            if managed_root is None
            else str(managed_root),
            "materialization_root": str(materialization_root),
            "task_home": str(task_home),
            "metadata_location": location,
        }
        return _ResolvedRequest(
            request=request,
            provision_id=provision_id,
            request_hash=canonical_hash(payload),
            managed_workspace_root=None if managed_root is None else str(managed_root),
            proposed_workspace_root=None
            if proposed_root is None
            else str(proposed_root),
            proposed_workspace_identity=proposed_identity,
            materialization_root=str(materialization_root),
            materialization_root_identity=_filesystem_identity(materialization_root),
            task_home=str(task_home),
            staging_path=str(staging),
            metadata_location=location,
        )

    def _configured_managed_root(self) -> Path:
        if self._managed_workspace_root is not None:
            return self._managed_workspace_root
        if platform.system() not in {"Darwin", "Linux"}:
            raise TaskScopeProvisionError("managed_workspace_root_required")
        return self._home_directory / "SimpleHarnessWorkSpace"

    async def _reserve(self, value: _ResolvedRequest) -> None:
        now = time.time()
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                by_key = await self._store._fetchone(
                    db,
                    "SELECT * FROM task_scope_provisions WHERE idempotency_key=?",
                    (value.request.idempotency_key,),
                )
                by_scope = await self._store._fetchone(
                    db,
                    "SELECT * FROM task_scope_provisions WHERE task_scope_id=?",
                    (value.request.task_scope_id,),
                )
                existing = by_key or by_scope
                if existing is not None:
                    if (
                        existing["request_hash"] != value.request_hash
                        or existing["task_scope_id"] != value.request.task_scope_id
                        or existing["idempotency_key"] != value.request.idempotency_key
                        or existing["task_home"] != value.task_home
                        or existing["materialization_root"]
                        != value.materialization_root
                    ):
                        raise TaskScopeConflict("provision_idempotency_conflict")
                    if existing[
                        "materialization_root_identity"
                    ] != value.materialization_root_identity or (
                        value.request.mode == "explicit"
                        and existing["proposed_workspace_identity"]
                        != value.proposed_workspace_identity
                    ):
                        raise TaskScopeProvisionError("provision_root_identity_drift")
                    await db.commit()
                    return
                await db.execute(
                    "INSERT INTO task_scope_provisions(provision_id,task_scope_id,idempotency_key,request_hash,provision_mode,trusted_provenance,managed_workspace_root,proposed_workspace_root,proposed_workspace_identity,materialization_root,materialization_root_identity,task_home,staging_path,metadata_location,state,task_home_identity,failure_code,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'reserved',NULL,NULL,?,?)",
                    (
                        value.provision_id,
                        value.request.task_scope_id,
                        value.request.idempotency_key,
                        value.request_hash,
                        value.request.mode,
                        value.request.provenance,
                        value.managed_workspace_root,
                        value.proposed_workspace_root,
                        value.proposed_workspace_identity,
                        value.materialization_root,
                        value.materialization_root_identity,
                        value.task_home,
                        value.staging_path,
                        value.metadata_location,
                        now,
                        now,
                    ),
                )
                await self._append_transition_tx(
                    db,
                    provision_id=value.provision_id,
                    from_state=None,
                    to_state="reserved",
                    reason_code="provision_reserved",
                    now=now,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    def _materialize(
        self,
        value: _ResolvedRequest,
        fault_inject: Callable[[str], None] | None,
    ) -> str:
        marker = {
            "schema_version": 1,
            "provision_id": value.provision_id,
            "task_scope_id": value.request.task_scope_id,
            "request_hash": value.request_hash,
            "task_home": value.task_home,
        }
        try:
            identity = _materialize_anchored(value, marker, fault_inject)
            _verify_resolved_roots(value)
            target = Path(value.task_home)
            _assert_no_existing_symlink(target)
            if _filesystem_identity(target) != identity:
                raise TaskScopeProvisionError("task_home_identity_drift")
            return identity
        except PermissionError as exc:
            raise TaskScopeProvisionError("filesystem_permission_denied") from exc
        except OSError as exc:
            if isinstance(exc, TaskScopeProvisionError):
                raise
            raise TaskScopeProvisionError("filesystem_create_failed") from exc

    @staticmethod
    def _verify_marker(directory: Path, expected: dict[str, object]) -> None:
        if directory.is_symlink() or not directory.is_dir():
            raise TaskScopeProvisionError("task_home_not_real_directory")
        marker_path = directory / ".simple-harness-provision.json"
        if marker_path.is_symlink() or not marker_path.is_file():
            raise TaskScopeProvisionError("task_home_marker_missing")
        try:
            actual = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TaskScopeProvisionError("task_home_marker_invalid") from exc
        if actual != expected:
            raise TaskScopeProvisionError("task_home_marker_conflict")

    async def _mark_filesystem_ready(
        self,
        provision_id: str,
        *,
        task_home_identity: str,
        proposed_workspace_identity: str | None,
    ) -> None:
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                row = await self._store._fetchone(
                    db,
                    "SELECT * FROM task_scope_provisions WHERE provision_id=?",
                    (provision_id,),
                )
                assert row is not None
                if row["state"] in {"filesystem_ready", "committed"}:
                    await db.commit()
                    return
                if row["state"] != "reserved":
                    raise TaskScopeConflict("provision_state_conflict")
                now = time.time()
                await db.execute(
                    "UPDATE task_scope_provisions SET state='filesystem_ready',task_home_identity=?,proposed_workspace_identity=COALESCE(proposed_workspace_identity,?),failure_code=NULL,updated_at=? WHERE provision_id=? AND state='reserved'",
                    (
                        task_home_identity,
                        proposed_workspace_identity,
                        now,
                        provision_id,
                    ),
                )
                await self._append_transition_tx(
                    db,
                    provision_id=provision_id,
                    from_state="reserved",
                    to_state="filesystem_ready",
                    reason_code="task_home_filesystem_verified",
                    now=now,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def _commit_receipt(self, provision_id: str) -> None:
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                row = await self._store._fetchone(
                    db,
                    "SELECT * FROM task_scope_provisions WHERE provision_id=?",
                    (provision_id,),
                )
                assert row is not None
                if row["state"] == "committed":
                    await db.commit()
                    return
                if row["state"] != "filesystem_ready":
                    raise TaskScopeConflict("provision_state_conflict")
                self._verify_materialized(row)
                committed_at = time.time()
                payload = {
                    "schema_version": 1,
                    "provision_id": provision_id,
                    "task_scope_id": row["task_scope_id"],
                    "request_hash": row["request_hash"],
                    "task_home": row["task_home"],
                    "task_home_identity": row["task_home_identity"],
                    "proposed_workspace_root": row["proposed_workspace_root"],
                    "proposed_workspace_identity": row["proposed_workspace_identity"],
                    "materialization_root": row["materialization_root"],
                    "materialization_root_identity": row[
                        "materialization_root_identity"
                    ],
                    "metadata_location": row["metadata_location"],
                    "committed_at": committed_at,
                }
                receipt_hash = canonical_hash(payload)
                await db.execute(
                    "INSERT INTO task_scope_provision_receipts(receipt_id,provision_id,task_scope_id,request_hash,task_home,task_home_identity,proposed_workspace_root,proposed_workspace_identity,materialization_root,materialization_root_identity,metadata_location,receipt_hash,receipt_json,committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _uuid(f"task-scope-provision-receipt:{provision_id}"),
                        provision_id,
                        row["task_scope_id"],
                        row["request_hash"],
                        row["task_home"],
                        row["task_home_identity"],
                        row["proposed_workspace_root"],
                        row["proposed_workspace_identity"],
                        row["materialization_root"],
                        row["materialization_root_identity"],
                        row["metadata_location"],
                        receipt_hash,
                        canonical_json(payload),
                        committed_at,
                    ),
                )
                await db.execute(
                    "UPDATE task_scope_provisions SET state='committed',failure_code=NULL,updated_at=? WHERE provision_id=? AND state='filesystem_ready'",
                    (committed_at, provision_id),
                )
                await self._append_transition_tx(
                    db,
                    provision_id=provision_id,
                    from_state="filesystem_ready",
                    to_state="committed",
                    reason_code="provision_receipt_committed",
                    now=committed_at,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def _mark_failed(self, provision_id: str, reason: str) -> None:
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                row = await self._store._fetchone(
                    db,
                    "SELECT state FROM task_scope_provisions WHERE provision_id=?",
                    (provision_id,),
                )
                assert row is not None
                if row["state"] == "committed":
                    raise TaskScopeConflict("committed_provision_cannot_fail")
                now = time.time()
                prior = str(row["state"])
                await db.execute(
                    "UPDATE task_scope_provisions SET state='failed_retryable',failure_code=?,updated_at=? WHERE provision_id=?",
                    (reason, now, provision_id),
                )
                await self._append_transition_tx(
                    db,
                    provision_id=provision_id,
                    from_state=prior,
                    to_state="failed_retryable",
                    reason_code=reason,
                    now=now,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def _transition(
        self, provision_id: str, *, expected: str, target: str, reason_code: str
    ) -> None:
        async with self._store._connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            await assert_human_memory_ingress_open_tx(db)
            try:
                updated = await db.execute(
                    "UPDATE task_scope_provisions SET state=?,failure_code=NULL,updated_at=? WHERE provision_id=? AND state=?",
                    (target, time.time(), provision_id, expected),
                )
                if updated.rowcount != 1:
                    raise TaskScopeConflict("provision_state_conflict")
                await self._append_transition_tx(
                    db,
                    provision_id=provision_id,
                    from_state=expected,
                    to_state=target,
                    reason_code=reason_code,
                    now=time.time(),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def _append_transition_tx(
        self,
        db: aiosqlite.Connection,
        *,
        provision_id: str,
        from_state: str | None,
        to_state: str,
        reason_code: str,
        now: float,
    ) -> None:
        row = await self._store._fetchone(
            db,
            "SELECT COALESCE(MAX(event_sequence),0)+1 AS sequence FROM task_scope_provision_events WHERE provision_id=?",
            (provision_id,),
        )
        sequence = int(row["sequence"])
        payload = {
            "schema_version": 1,
            "provision_id": provision_id,
            "event_sequence": sequence,
            "from_state": from_state,
            "to_state": to_state,
            "reason_code": reason_code,
        }
        await db.execute(
            "INSERT INTO task_scope_provision_events(event_id,provision_id,event_sequence,from_state,to_state,reason_code,event_hash,event_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                _uuid(f"task-scope-provision-event:{provision_id}:{sequence}"),
                provision_id,
                sequence,
                from_state,
                to_state,
                reason_code,
                canonical_hash(payload),
                canonical_json(payload),
                now,
            ),
        )

    async def _load_provision(self, provision_id: str) -> aiosqlite.Row:
        async with self._store._connection() as db:
            row = await self._store._fetchone(
                db,
                "SELECT * FROM task_scope_provisions WHERE provision_id=?",
                (provision_id,),
            )
        if row is None:
            raise TaskScopeNotFound("provision_not_found")
        return row

    async def _load_committed_receipt(
        self, provision_id: str, *, verify: bool
    ) -> TaskScopeProvisionReceipt:
        async with self._store._connection() as db:
            row = await self._store._fetchone(
                db,
                "SELECT * FROM task_scope_provision_receipts WHERE provision_id=?",
                (provision_id,),
            )
        if row is None:
            raise TaskScopeConflict("provision_not_committed")
        if verify:
            self._verify_receipt_row(row)
        return TaskScopeProvisionReceipt(
            receipt_id=str(row["receipt_id"]),
            provision_id=str(row["provision_id"]),
            task_scope_id=str(row["task_scope_id"]),
            task_home=str(row["task_home"]),
            task_home_identity=str(row["task_home_identity"]),
            proposed_workspace_root=(
                None
                if row["proposed_workspace_root"] is None
                else str(row["proposed_workspace_root"])
            ),
            proposed_workspace_identity=(
                None
                if row["proposed_workspace_identity"] is None
                else str(row["proposed_workspace_identity"])
            ),
            materialization_root=str(row["materialization_root"]),
            materialization_root_identity=str(row["materialization_root_identity"]),
            metadata_location=str(row["metadata_location"]),  # type: ignore[arg-type]
            receipt_hash=str(row["receipt_hash"]),
            committed_at=float(row["committed_at"]),
        )

    def _verify_materialized(self, row: aiosqlite.Row) -> None:
        target = Path(str(row["task_home"]))
        _verify_stored_roots(row)
        expected = {
            "schema_version": 1,
            "provision_id": str(row["provision_id"]),
            "task_scope_id": str(row["task_scope_id"]),
            "request_hash": str(row["request_hash"]),
            "task_home": str(row["task_home"]),
        }
        self._verify_marker(target, expected)
        if _filesystem_identity(target) != row["task_home_identity"]:
            raise TaskScopeProvisionError("task_home_identity_drift")

    def _verify_receipt_row(self, row: aiosqlite.Row) -> None:
        try:
            payload = json.loads(str(row["receipt_json"]))
        except ValueError as exc:
            raise TaskScopeProvisionError("provision_receipt_invalid") from exc
        if canonical_hash(payload) != row["receipt_hash"]:
            raise TaskScopeProvisionError("provision_receipt_hash_mismatch")
        _verify_stored_roots(row)
        if (
            _filesystem_identity(Path(str(row["task_home"])))
            != row["task_home_identity"]
        ):
            raise TaskScopeProvisionError("task_home_identity_drift")

    @staticmethod
    def _fault(callback: Callable[[str], None] | None, stage: str) -> None:
        if callback is not None:
            callback(stage)


def _safe_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    characters: list[str] = []
    previous_dash = False
    for character in normalized:
        safe = character.isalnum() or character in {"-", "_"}
        output = character if safe else "-"
        if output == "-" and previous_dash:
            continue
        characters.append(output)
        previous_dash = output == "-"
        if len("".join(characters).encode("utf-8")) >= 48:
            break
    slug = "".join(characters).strip("-._")
    return slug or "task"


def _safe_scope_component(value: str) -> str:
    if (
        value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or len(value.encode("utf-8")) > 128
    ):
        raise TaskScopeProvisionError("task_scope_id_path_unsafe")
    if not all(character.isalnum() or character in {"-", "_"} for character in value):
        raise TaskScopeProvisionError("task_scope_id_path_unsafe")
    return value


def _assert_no_existing_symlink(path: Path) -> None:
    expanded = path.expanduser()
    for component in (expanded, *expanded.parents):
        if component.is_symlink():
            raise TaskScopeProvisionError("path_symlink_rejected")


def _strict_existing_directory(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise TaskScopeProvisionError("explicit_path_not_absolute")
    _assert_no_existing_symlink(path)
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise TaskScopeProvisionError("explicit_path_not_found") from exc
    except PermissionError as exc:
        raise TaskScopeProvisionError("explicit_path_unreadable") from exc
    if resolved.is_symlink() or not resolved.is_dir():
        raise TaskScopeProvisionError("explicit_path_not_directory")
    if not os.access(resolved, os.R_OK | os.X_OK):
        raise TaskScopeProvisionError("explicit_path_unreadable")
    return resolved


def _canonical_nonexistent(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        raise TaskScopeProvisionError("task_home_not_absolute")
    _assert_no_existing_symlink(path)
    return path.resolve(strict=False)


def _ensure_managed_root(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        raise TaskScopeProvisionError("managed_root_not_absolute")
    _assert_no_existing_symlink(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except PermissionError as exc:
        raise TaskScopeProvisionError("managed_root_permission_denied") from exc
    resolved = _strict_existing_directory(path)
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise TaskScopeProvisionError("managed_root_too_broad")
    return resolved


def _require_real_child(root: Path, child: Path) -> None:
    canonical_root = root.resolve(strict=True)
    canonical_child = child.resolve(strict=False)
    try:
        relative = canonical_child.relative_to(canonical_root)
    except ValueError as exc:
        raise TaskScopeProvisionError("task_home_outside_managed_root") from exc
    if not relative.parts:
        raise TaskScopeProvisionError("task_home_equals_managed_root")


def _filesystem_identity(path: Path) -> str:
    try:
        info = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise TaskScopeProvisionError("task_home_missing") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise TaskScopeProvisionError("task_home_not_real_directory")
    return _filesystem_identity_from_stat(info)


def _filesystem_identity_from_stat(info: os.stat_result) -> str:
    raw = f"v1\0{platform.system().lower()}\0{int(info.st_dev)}\0{int(info.st_ino)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_marker_at(directory_fd: int, value: dict[str, object]) -> None:
    payload = (canonical_json(value) + "\n").encode("utf-8")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(
        ".simple-harness-provision.json", flags, 0o600, dir_fd=directory_fd
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_marker_at(directory_fd: int) -> dict[str, object] | None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(
            ".simple-harness-provision.json", flags, dir_fd=directory_fd
        )
    except FileNotFoundError:
        return None
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 4096)
            if not chunk:
                break
            total += len(chunk)
            if total > 65536:
                raise TaskScopeProvisionError("task_home_marker_invalid")
            chunks.append(chunk)
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise TaskScopeProvisionError("task_home_marker_invalid") from exc
    finally:
        os.close(descriptor)
    if not isinstance(value, dict):
        raise TaskScopeProvisionError("task_home_marker_invalid")
    return value


def _directory_open_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if os.name != "posix" or any(not hasattr(os, name) for name in required):
        raise TaskScopeProvisionError("anchored_materialization_unsupported")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_directory_at(parent_fd: int, name: str, *, conflict_code: str) -> int:
    try:
        descriptor = os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise TaskScopeProvisionError("path_symlink_rejected") from exc
        raise TaskScopeProvisionError(conflict_code) from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise TaskScopeProvisionError(conflict_code)
    return descriptor


def _ensure_directory_at(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except PermissionError as exc:
        raise TaskScopeProvisionError("filesystem_permission_denied") from exc
    return _open_directory_at(parent_fd, name, conflict_code="task_home_parent_invalid")


def _entry_stat(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _verify_marker_at(
    directory_fd: int, expected: dict[str, object], *, missing_code: str
) -> None:
    actual = _read_marker_at(directory_fd)
    if actual is None:
        raise TaskScopeProvisionError(missing_code)
    if actual != expected:
        raise TaskScopeProvisionError("task_home_marker_conflict")


def _rename_noreplace(parent_fd: int, source: str, target: str) -> None:
    function, flags = _anchored_rename_api()
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    result = function(parent_fd, source_bytes, parent_fd, target_bytes, flags)
    if result == 0:
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise TaskScopeProvisionError("task_home_collision")
    raise TaskScopeProvisionError("filesystem_create_failed") from OSError(
        error, os.strerror(error)
    )


def _anchored_rename_api() -> tuple[Callable[..., int], int]:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        flags = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        flags = 0x00000001  # RENAME_NOREPLACE
    else:
        function = None
        flags = 0
    if function is None:
        raise TaskScopeProvisionError("anchored_rename_unsupported")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    return function, flags


def _require_anchored_materialization_support() -> None:
    _directory_open_flags()
    _anchored_rename_api()


def _relative_components(root: Path, child: Path) -> tuple[str, ...]:
    try:
        relative = child.relative_to(root)
    except ValueError as exc:
        raise TaskScopeProvisionError("task_home_outside_materialization_root") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise TaskScopeProvisionError("task_home_outside_materialization_root")
    return relative.parts


def _materialize_anchored(
    value: _ResolvedRequest,
    marker: dict[str, object],
    fault_inject: Callable[[str], None] | None,
) -> str:
    root = Path(value.materialization_root)
    target = Path(value.task_home)
    staging = Path(value.staging_path)
    target_parts = _relative_components(root, target)
    staging_parts = _relative_components(root, staging)
    if target_parts[:-1] != staging_parts[:-1]:
        raise TaskScopeProvisionError("provision_staging_conflict")
    try:
        root_fd = os.open(root, _directory_open_flags())
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise TaskScopeProvisionError("path_symlink_rejected") from exc
        raise TaskScopeProvisionError("materialization_root_unavailable") from exc
    descriptors = [root_fd]
    try:
        if (
            _filesystem_identity_from_stat(os.fstat(root_fd))
            != value.materialization_root_identity
        ):
            raise TaskScopeProvisionError("provision_root_identity_drift")
        parent_fd = root_fd
        for component in target_parts[:-1]:
            parent_fd = _ensure_directory_at(parent_fd, component)
            descriptors.append(parent_fd)
        target_name = target_parts[-1]
        staging_name = staging_parts[-1]
        target_info = _entry_stat(parent_fd, target_name)
        if target_info is not None:
            if not stat.S_ISDIR(target_info.st_mode):
                raise TaskScopeProvisionError("task_home_not_real_directory")
            target_fd = _open_directory_at(
                parent_fd, target_name, conflict_code="task_home_not_real_directory"
            )
            try:
                _verify_marker_at(
                    target_fd, marker, missing_code="task_home_marker_missing"
                )
                return _filesystem_identity_from_stat(os.fstat(target_fd))
            finally:
                os.close(target_fd)

        staging_info = _entry_stat(parent_fd, staging_name)
        if staging_info is None:
            try:
                os.mkdir(staging_name, 0o700, dir_fd=parent_fd)
            except PermissionError as exc:
                raise TaskScopeProvisionError("filesystem_permission_denied") from exc
        elif not stat.S_ISDIR(staging_info.st_mode):
            raise TaskScopeProvisionError("provision_staging_conflict")
        staging_fd = _open_directory_at(
            parent_fd, staging_name, conflict_code="provision_staging_conflict"
        )
        try:
            actual = _read_marker_at(staging_fd)
            if actual is None:
                if os.listdir(staging_fd):
                    raise TaskScopeProvisionError("provision_staging_conflict")
                _write_marker_at(staging_fd, marker)
            elif actual != marker:
                raise TaskScopeProvisionError("task_home_marker_conflict")
            if fault_inject is not None:
                fault_inject("after_staging_ready")
        finally:
            os.close(staging_fd)
        _rename_noreplace(parent_fd, staging_name, target_name)
        os.fsync(parent_fd)
        target_fd = _open_directory_at(
            parent_fd, target_name, conflict_code="task_home_not_real_directory"
        )
        try:
            _verify_marker_at(
                target_fd, marker, missing_code="task_home_marker_missing"
            )
            return _filesystem_identity_from_stat(os.fstat(target_fd))
        finally:
            os.close(target_fd)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _verify_resolved_roots(value: _ResolvedRequest) -> None:
    _require_root_identity(
        Path(value.materialization_root), value.materialization_root_identity
    )
    if value.proposed_workspace_identity is not None:
        assert value.proposed_workspace_root is not None
        _require_root_identity(
            Path(value.proposed_workspace_root), value.proposed_workspace_identity
        )


def _verify_stored_roots(row: aiosqlite.Row) -> None:
    root = Path(str(row["materialization_root"]))
    target = Path(str(row["task_home"]))
    _assert_no_existing_symlink(root)
    _assert_no_existing_symlink(target)
    _relative_components(root, target)
    _require_root_identity(root, str(row["materialization_root_identity"]))
    if row["proposed_workspace_identity"] is not None:
        proposed = Path(str(row["proposed_workspace_root"]))
        _assert_no_existing_symlink(proposed)
        _require_root_identity(proposed, str(row["proposed_workspace_identity"]))


def _require_root_identity(path: Path, expected: str) -> None:
    try:
        actual = _filesystem_identity(path)
    except TaskScopeProvisionError as exc:
        raise TaskScopeProvisionError("provision_root_identity_drift") from exc
    if actual != expected:
        raise TaskScopeProvisionError("provision_root_identity_drift")
