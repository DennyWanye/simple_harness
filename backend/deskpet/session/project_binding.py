# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Durable Project identity and immutable Session workspace resolution.

This module is deliberately app-private.  It is the only path from user chosen
folders to a trusted workspace value; UI paths and legacy Run metadata are not
accepted as workspace authorities.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping, TypeAlias

import aiosqlite


CATALOG_SCHEMA_VERSION = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_HANDOFF_MESSAGES = 6
MAX_HANDOFF_CHARS = 6000


class ProjectSessionError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True, slots=True)
class RegistrationPreview:
    selected_path: str
    detected_git_root: str | None
    canonical_root: str
    root_kind: Literal["git", "folder"]
    display_name: str
    filesystem_identity: str

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("filesystem_identity")
        return value


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    display_name: str
    canonical_root: str
    root_kind: Literal["git", "folder"]
    filesystem_identity: str
    project_revision: int
    created_at: float
    updated_at: float
    last_opened_at: float


@dataclass(frozen=True, slots=True)
class SessionProjectBinding:
    session_id: str
    project_id: str
    execution_kind: Literal["project_root", "explicit"]
    execution_root: str | None
    execution_identity: str | None
    source_session_id: str | None
    handoff: dict[str, Any] | None
    binding_version: int
    created_at: float


@dataclass(frozen=True, slots=True)
class ProjectBoundWorkspace:
    kind: Literal["project"]
    session_id: str
    project_id: str
    project_name: str
    project_root: str
    execution_kind: Literal["project_root", "explicit"]
    effective_root: str
    project_identity: str
    execution_identity: str
    project_revision: int
    binding_version: int
    handoff: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class ProjectlessWorkspace:
    kind: Literal["projectless"]
    session_id: str


@dataclass(frozen=True, slots=True)
class ProjectMissingWorkspace:
    kind: Literal["missing"]
    session_id: str
    project_id: str
    project_name: str
    project_root: str
    execution_kind: Literal["project_root", "explicit"]
    effective_root: str
    project_revision: int
    binding_version: int
    error_code: Literal["workspace_unavailable", "workspace_binding_stale"]


ProjectWorkspaceResolution: TypeAlias = (
    ProjectBoundWorkspace | ProjectlessWorkspace | ProjectMissingWorkspace
)
WorkspaceResolution: TypeAlias = ProjectWorkspaceResolution


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _filesystem_identity(path: Path) -> str:
    try:
        stat = path.stat()
    except FileNotFoundError as exc:
        raise ProjectSessionError("path_not_found") from exc
    except PermissionError as exc:
        raise ProjectSessionError("path_unreadable") from exc
    raw = f"v1\0{platform.system().lower()}\0{int(stat.st_dev)}\0{int(stat.st_ino)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strict_directory(raw_path: str) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        raise ProjectSessionError("path_empty")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise ProjectSessionError("path_not_absolute")
    try:
        resolved = expanded.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ProjectSessionError("path_not_found") from exc
    except PermissionError as exc:
        raise ProjectSessionError("path_unreadable") from exc
    if not resolved.is_dir():
        raise ProjectSessionError("path_not_directory")
    return resolved


def resolve_registration(
    selected_path: str,
    mode: Literal["git_root", "selected_folder"] = "git_root",
    *,
    git_timeout_seconds: float = 2.0,
) -> RegistrationPreview:
    if mode not in ("git_root", "selected_folder"):
        raise ProjectSessionError("invalid_request", "unsupported registration mode")
    selected = _strict_directory(selected_path)
    detected_git_root: Path | None = None
    if mode == "git_root":
        try:
            completed = subprocess.run(
                ["git", "-C", os.fspath(selected), "rev-parse", "--show-toplevel"],
                check=False,
                capture_output=True,
                text=True,
                timeout=git_timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProjectSessionError("git_probe_timeout") from exc
        except OSError:
            completed = None
        if completed is not None and completed.returncode == 0:
            candidate = _strict_directory(completed.stdout.strip())
            try:
                selected.relative_to(candidate)
            except ValueError as exc:
                raise ProjectSessionError("git_root_invalid") from exc
            detected_git_root = candidate
    canonical = detected_git_root or selected
    return RegistrationPreview(
        selected_path=os.fspath(selected),
        detected_git_root=os.fspath(detected_git_root) if detected_git_root else None,
        canonical_root=os.fspath(canonical),
        root_kind="git" if detected_git_root else "folder",
        display_name=canonical.name or os.fspath(canonical),
        filesystem_identity=_filesystem_identity(canonical),
    )


def _encode_cursor(payload: dict[str, Any]) -> str:
    data = _canonical_json(payload).encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> dict[str, Any]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ProjectSessionError("cursor_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ProjectSessionError("cursor_invalid")
    return payload


class ProjectBindingService:
    """Repository/service boundary consumed by main.py and Run authority wiring."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._write_lock = write_lock or asyncio.Lock()

    async def preview_registration(self, selected_path: str, mode: str) -> RegistrationPreview:
        return await asyncio.to_thread(resolve_registration, selected_path, mode)  # type: ignore[arg-type]

    async def _require_upgrade_complete(self, db: aiosqlite.Connection) -> None:
        row = await (await db.execute(
            "SELECT phase FROM legacy_session_reset_state "
            "WHERE singleton=1 AND policy_version=1"
        )).fetchone()
        if row is None or str(row[0]) != "completed":
            raise ProjectSessionError("upgrade_incomplete")

    @staticmethod
    def _project_from_row(row: Any) -> ProjectRecord:
        return ProjectRecord(
            project_id=str(row[0]), display_name=str(row[1]), canonical_root=str(row[2]),
            root_kind=str(row[3]), filesystem_identity=str(row[4]),
            project_revision=int(row[5]), created_at=float(row[6]),
            updated_at=float(row[7]), last_opened_at=float(row[8]),
        )

    async def register_project(
        self, selected_path: str, mode: str = "git_root", display_name: str | None = None
    ) -> tuple[ProjectRecord, bool]:
        preview = await self.preview_registration(selected_path, mode)
        clean_name = str(display_name or preview.display_name).strip()[:120]
        if not clean_name:
            raise ProjectSessionError("invalid_request", "display name is empty")
        now = time.time()
        async with self._write_lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("PRAGMA foreign_keys=ON")
                await db.execute("BEGIN IMMEDIATE")
                await self._require_upgrade_complete(db)
                row = await (await db.execute(
                    "SELECT project_id,display_name,canonical_root,root_kind,filesystem_identity,"
                    "project_revision,created_at,updated_at,last_opened_at FROM projects "
                    "WHERE filesystem_identity=?", (preview.filesystem_identity,)
                )).fetchone()
                created = row is None
                if row is None:
                    project_id = str(uuid.uuid4())
                    await db.execute(
                        "INSERT INTO projects(project_id,display_name,canonical_root,root_kind,"
                        "filesystem_identity,project_revision,created_at,updated_at,last_opened_at) "
                        "VALUES(?,?,?,?,?,1,?,?,?)",
                        (project_id, clean_name, preview.canonical_root, preview.root_kind,
                         preview.filesystem_identity, now, now, now),
                    )
                    row = (project_id, clean_name, preview.canonical_root, preview.root_kind,
                           preview.filesystem_identity, 1, now, now, now)
                await db.commit()
                return self._project_from_row(row), created

    async def get_project(self, project_id: str) -> ProjectRecord:
        async with aiosqlite.connect(self._db_path) as db:
            await self._require_upgrade_complete(db)
            row = await (await db.execute(
                "SELECT project_id,display_name,canonical_root,root_kind,filesystem_identity,"
                "project_revision,created_at,updated_at,last_opened_at FROM projects WHERE project_id=?",
                (str(project_id),),
            )).fetchone()
        if row is None:
            raise ProjectSessionError("project_not_found")
        return self._project_from_row(row)

    async def resolve_session(self, session_id: str) -> WorkspaceResolution:
        sid = str(session_id or "").strip()
        if not sid:
            raise ProjectSessionError("session_not_found")
        async with aiosqlite.connect(self._db_path) as db:
            await self._require_upgrade_complete(db)
            exists = await (await db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,))).fetchone()
            if exists is None:
                raise ProjectSessionError("session_not_found")
            row = await (await db.execute(
                "SELECT b.project_id,b.execution_kind,b.execution_root,b.execution_identity,"
                "b.handoff_json,b.binding_version,p.display_name,p.canonical_root,"
                "p.filesystem_identity,p.project_revision FROM session_project_bindings b "
                "JOIN projects p ON p.project_id=b.project_id WHERE b.session_id=?", (sid,)
            )).fetchone()
        if row is None:
            return ProjectlessWorkspace(kind="projectless", session_id=sid)
        project_id, execution_kind = str(row[0]), str(row[1])
        project_root = str(row[7])
        effective_root = project_root if execution_kind == "project_root" else str(row[2])
        expected_execution_identity = str(row[8]) if execution_kind == "project_root" else str(row[3])
        try:
            actual = _filesystem_identity(_strict_directory(effective_root))
        except ProjectSessionError:
            return ProjectMissingWorkspace(
                kind="missing", session_id=sid, project_id=project_id,
                project_name=str(row[6]), project_root=project_root,
                execution_kind=execution_kind, effective_root=effective_root,
                project_revision=int(row[9]), binding_version=int(row[5]),
                error_code="workspace_unavailable",
            )
        if actual != expected_execution_identity:
            return ProjectMissingWorkspace(
                kind="missing", session_id=sid, project_id=project_id,
                project_name=str(row[6]), project_root=project_root,
                execution_kind=execution_kind, effective_root=effective_root,
                project_revision=int(row[9]), binding_version=int(row[5]),
                error_code="workspace_binding_stale",
            )
        return ProjectBoundWorkspace(
            kind="project", session_id=sid, project_id=project_id,
            project_name=str(row[6]), project_root=project_root,
            execution_kind=execution_kind, effective_root=effective_root,
            project_identity=str(row[8]), execution_identity=expected_execution_identity,
            project_revision=int(row[9]), binding_version=int(row[5]),
            handoff=json.loads(row[4]) if row[4] else None,
        )

    async def claim_handoff(
        self, session_id: str, first_run_id: str
    ) -> dict[str, Any] | None:
        """Return a bounded handoff only to the target Session's first Run."""

        sid = str(session_id or "").strip()
        run_id = str(first_run_id or "").strip()
        if not sid or not run_id:
            raise ProjectSessionError("invalid_request")
        async with self._write_lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                row = await (await db.execute(
                    "SELECT handoff_json FROM session_project_bindings WHERE session_id=?",
                    (sid,),
                )).fetchone()
                if row is None or not row[0]:
                    await db.rollback()
                    return None
                existing = await (await db.execute(
                    "SELECT first_run_id FROM session_handoff_consumptions WHERE session_id=?",
                    (sid,),
                )).fetchone()
                if existing is None:
                    await db.execute(
                        "INSERT INTO session_handoff_consumptions(session_id,first_run_id,consumed_at) "
                        "VALUES(?,?,?)",
                        (sid, run_id, time.time()),
                    )
                elif str(existing[0]) != run_id:
                    await db.rollback()
                    return None
                await db.commit()
                return json.loads(str(row[0]))

    async def admit_run(
        self, resolution: Mapping[str, Any], run_id: str
    ) -> None:
        """CAS one verified Project revision into the active-Run fence."""

        if str(resolution.get("kind")) != "project_bound":
            return
        self.validate_workspace_identity(resolution)
        async with self._write_lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                row = await (await db.execute(
                    "SELECT b.project_id,p.project_revision,p.canonical_root,p.filesystem_identity,"
                    "b.execution_kind,b.execution_root,b.execution_identity "
                    "FROM session_project_bindings b "
                    "JOIN projects p ON p.project_id=b.project_id WHERE b.session_id=?",
                    (str(resolution["session_id"]),),
                )).fetchone()
                if row is None:
                    raise ProjectSessionError("workspace_binding_stale")
                if (
                    str(row[0]) != str(resolution["project_id"])
                    or int(row[1]) != int(resolution["project_revision"])
                    or str(row[2]) != str(resolution["project_root"])
                    or str(row[3]) != str(resolution["project_identity"])
                    or str(row[4]) != str(resolution["execution_kind"])
                    or (
                        str(row[2]) if str(row[4]) == "project_root" else str(row[5])
                    ) != str(resolution["effective_root"])
                    or (
                        str(row[3]) if str(row[4]) == "project_root" else str(row[6])
                    ) != str(resolution["execution_identity"])
                ):
                    raise ProjectSessionError("workspace_binding_stale")
                self.validate_workspace_identity(resolution)
                existing = await (await db.execute(
                    "SELECT session_id,project_id,project_revision,state "
                    "FROM project_run_admissions WHERE run_id=?",
                    (str(run_id),),
                )).fetchone()
                expected = (
                    str(resolution["session_id"]),
                    str(resolution["project_id"]),
                    int(resolution["project_revision"]),
                )
                if existing is not None:
                    if tuple(existing[:3]) != expected:
                        raise ProjectSessionError("workspace_binding_stale")
                    await db.execute(
                        "UPDATE project_run_admissions SET state='active',released_at=NULL "
                        "WHERE run_id=?",
                        (str(run_id),),
                    )
                else:
                    await db.execute(
                        "INSERT INTO project_run_admissions(run_id,session_id,project_id,"
                        "project_revision,state,admitted_at) VALUES(?,?,?,?,'active',?)",
                        (str(run_id), *expected, time.time()),
                    )
                await db.commit()

    async def release_run(self, run_id: str) -> None:
        async with self._write_lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE project_run_admissions SET state='released',released_at=? "
                    "WHERE run_id=? AND state='active'",
                    (time.time(), str(run_id)),
                )
                await db.commit()

    async def reconcile_orphan_run_claims(
        self, has_durable_run_start: Callable[[str], Awaitable[bool]]
    ) -> dict[str, int]:
        """Drop only pre-RunStart crash residue before runtime ingress opens.

        A handoff reservation remains permanent once its deterministic first
        Run has a durable RunStart.  Active relocation fences without such a
        record are safe to remove because no recoverable Run can own them.
        """

        async with self._write_lock:
            async with aiosqlite.connect(self._db_path) as db:
                claim_tables = {
                    str(row[0])
                    for row in await (await db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                        "('project_run_admissions','session_handoff_consumptions')"
                    )).fetchall()
                }
                if claim_tables != {
                    "project_run_admissions", "session_handoff_consumptions"
                }:
                    return {"admissions_removed": 0, "handoffs_removed": 0}
                admissions = [
                    str(row[0])
                    for row in await (await db.execute(
                        "SELECT run_id FROM project_run_admissions WHERE state='active'"
                    )).fetchall()
                ]
                handoffs = [
                    (str(row[0]), str(row[1]))
                    for row in await (await db.execute(
                        "SELECT session_id,first_run_id FROM session_handoff_consumptions"
                    )).fetchall()
                ]
            orphan_admissions = [
                run_id for run_id in admissions
                if not await has_durable_run_start(run_id)
            ]
            orphan_handoffs = [
                (session_id, run_id) for session_id, run_id in handoffs
                if not await has_durable_run_start(run_id)
            ]
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                if orphan_admissions:
                    await db.executemany(
                        "DELETE FROM project_run_admissions WHERE run_id=? AND state='active'",
                        ((run_id,) for run_id in orphan_admissions),
                    )
                if orphan_handoffs:
                    await db.executemany(
                        "DELETE FROM session_handoff_consumptions "
                        "WHERE session_id=? AND first_run_id=?",
                        orphan_handoffs,
                    )
                await db.commit()
        return {
            "admissions_removed": len(orphan_admissions),
            "handoffs_removed": len(orphan_handoffs),
        }

    async def list_deleted_session_ids(self) -> tuple[str, ...]:
        """Return durable deletion fences for startup Run-cancel replay."""

        async with aiosqlite.connect(self._db_path) as db:
            rows = await (await db.execute(
                "SELECT session_id FROM session_delivery_state "
                "WHERE deleted_at IS NOT NULL ORDER BY session_id"
            )).fetchall()
        return tuple(str(row[0]) for row in rows)

    async def reconcile_deleted_session_runs(
        self,
        cancel_runs: Callable[[str], Awaitable[object]],
    ) -> dict[str, int]:
        """Idempotently replay the state.db -> workflow.db delete boundary."""

        attempted = completed = failed = 0
        for session_id in await self.list_deleted_session_ids():
            attempted += 1
            try:
                await cancel_runs(session_id)
            except Exception:  # noqa: BLE001 - retry remains durable for next boot
                failed += 1
            else:
                completed += 1
        return {"attempted": attempted, "completed": completed, "failed": failed}

    @staticmethod
    def validate_workspace_identity(resolution: Mapping[str, Any]) -> None:
        """Re-stat both frozen roots without consulting mutable Session state."""

        kind = str(resolution.get("kind") or "")
        if kind in {"legacy", "projectless"}:
            return
        if kind != "project_bound":
            raise RuntimeError("workspace_unavailable")
        try:
            project_identity = _filesystem_identity(
                _strict_directory(str(resolution["project_root"]))
            )
            execution_identity = _filesystem_identity(
                _strict_directory(str(resolution["effective_root"]))
            )
        except (KeyError, ProjectSessionError) as exc:
            raise RuntimeError("workspace_unavailable") from exc
        if (
            project_identity != str(resolution.get("project_identity") or "")
            or execution_identity
            != str(resolution.get("execution_identity") or "")
        ):
            raise RuntimeError("workspace_binding_stale")

    async def relocate_project(
        self, project_id: str, new_path: str, expected_project_revision: int
    ) -> ProjectRecord:
        candidate = _strict_directory(new_path)
        candidate_identity = _filesystem_identity(candidate)
        now = time.time()
        async with self._write_lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                await self._require_upgrade_complete(db)
                admitted = await (await db.execute(
                    "SELECT 1 FROM project_run_admissions "
                    "WHERE project_id=? AND state='active' LIMIT 1",
                    (project_id,),
                )).fetchone()
                if admitted is not None:
                    raise ProjectSessionError("project_runs_active")
                for run_table in ("execution_runs", "workflow_runs"):
                    exists = await (await db.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (run_table,),
                    )).fetchone()
                    if exists is None:
                        continue
                    active = await (await db.execute(
                        f"SELECT 1 FROM session_project_bindings b "
                        f"JOIN {run_table} r ON r.session_id=b.session_id "
                        "WHERE b.project_id=? AND r.status IN "
                        "('created','queued','running','waiting','cancel_requested') LIMIT 1",
                        (project_id,),
                    )).fetchone()
                    if active is not None:
                        raise ProjectSessionError("project_runs_active")
                row = await (await db.execute(
                    "SELECT filesystem_identity FROM projects WHERE project_id=?", (project_id,)
                )).fetchone()
                if row is None:
                    raise ProjectSessionError("project_not_found")
                if str(row[0]) != candidate_identity:
                    raise ProjectSessionError("project_identity_mismatch")
                cursor = await db.execute(
                    "UPDATE projects SET canonical_root=?,project_revision=project_revision+1,"
                    "updated_at=?,last_opened_at=? WHERE project_id=? AND project_revision=?",
                    (os.fspath(candidate), now, now, project_id, int(expected_project_revision)),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    raise ProjectSessionError("project_revision_conflict")
                await db.commit()
        return await self.get_project(project_id)

    async def inspect_project(self, project_id: str) -> dict[str, Any]:
        record = await self.get_project(project_id)
        descriptor = self.project_descriptor(record)

        def _git_status() -> dict[str, Any]:
            if descriptor["availability"] != "available":
                return {"available": False, "branch": None, "dirty": None,
                        "error_code": "workspace_unavailable"}
            try:
                completed = subprocess.run(
                    ["git", "-C", record.canonical_root, "status", "--porcelain=v1", "--branch"],
                    check=False, capture_output=True, text=True, timeout=2.0, shell=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return {"available": False, "branch": None, "dirty": None,
                        "error_code": "git_status_unavailable"}
            if completed.returncode != 0:
                return {"available": False, "branch": None, "dirty": None,
                        "error_code": "not_git_repository"}
            lines = completed.stdout.splitlines()
            branch = lines[0][3:].split("...")[0] if lines and lines[0].startswith("## ") else None
            return {"available": True, "branch": branch, "dirty": len(lines) > 1}

        return {"project": descriptor, "git": await asyncio.to_thread(_git_status)}

    async def catalog_revision(self, db: aiosqlite.Connection | None = None) -> int:
        if db is not None:
            row = await (await db.execute(
                "SELECT catalog_revision FROM project_session_catalog_state WHERE singleton=1"
            )).fetchone()
            return int(row[0])
        async with aiosqlite.connect(self._db_path) as connection:
            return await self.catalog_revision(connection)

    @staticmethod
    def _page_limit(value: int | None) -> int:
        return min(MAX_PAGE_SIZE, max(1, int(value or DEFAULT_PAGE_SIZE)))

    async def list_project_page(
        self, *, cursor: str | None = None, limit: int | None = None,
        pinned_project_id: str | None = None,
        pinned_session_id: str | None = None,
    ) -> dict[str, Any]:
        size = self._page_limit(limit)
        decoded = _decode_cursor(cursor) if cursor else None
        async with aiosqlite.connect(self._db_path) as db:
            await self._require_upgrade_complete(db)
            revision = await self.catalog_revision(db)
            if decoded and int(decoded.get("catalog_revision", -1)) != revision:
                raise ProjectSessionError("stale_cursor")
            params: list[Any] = []
            where = ""
            if decoded:
                where = "WHERE (last_opened_at < ? OR (last_opened_at = ? AND project_id > ?))"
                params.extend([decoded["last_opened_at"], decoded["last_opened_at"], decoded["project_id"]])
            rows = await (await db.execute(
                "SELECT project_id,display_name,canonical_root,root_kind,filesystem_identity,"
                "project_revision,created_at,updated_at,last_opened_at FROM projects " + where +
                " ORDER BY last_opened_at DESC,project_id ASC LIMIT ?", (*params, size + 1)
            )).fetchall()
            pinned_row = None
            if not pinned_project_id and pinned_session_id:
                binding_row = await (await db.execute(
                    "SELECT project_id FROM session_project_bindings WHERE session_id=?",
                    (pinned_session_id,),
                )).fetchone()
                pinned_project_id = str(binding_row[0]) if binding_row else None
            if pinned_project_id:
                pinned_row = await (await db.execute(
                    "SELECT project_id,display_name,canonical_root,root_kind,filesystem_identity,"
                    "project_revision,created_at,updated_at,last_opened_at FROM projects WHERE project_id=?",
                    (pinned_project_id,),
                )).fetchone()
        page_rows, has_more = rows[:size], len(rows) > size
        records = [self._project_from_row(row) for row in page_rows]
        next_cursor = None
        if has_more and records:
            tail = records[-1]
            next_cursor = _encode_cursor({"schema_version": CATALOG_SCHEMA_VERSION,
                "catalog_revision": revision, "last_opened_at": tail.last_opened_at,
                "project_id": tail.project_id})
        return {"schema_version": CATALOG_SCHEMA_VERSION, "catalog_revision": revision,
                "items": [self.project_descriptor(r) for r in records], "next_cursor": next_cursor,
                "pinned": self.project_descriptor(self._project_from_row(pinned_row)) if pinned_row else None}

    def project_descriptor(self, record: ProjectRecord) -> dict[str, Any]:
        available = False
        try:
            available = _filesystem_identity(_strict_directory(record.canonical_root)) == record.filesystem_identity
        except ProjectSessionError:
            pass
        return {"project_id": record.project_id, "display_name": record.display_name,
                "project_root": record.canonical_root, "root_kind": record.root_kind,
                "project_revision": record.project_revision,
                "availability": "available" if available else "missing",
                "created_at": record.created_at, "updated_at": record.updated_at,
                "last_opened_at": record.last_opened_at}

    async def list_session_page(
        self, *, scope_kind: Literal["project", "projectless"], project_id: str | None = None,
        cursor: str | None = None, limit: int | None = None,
        pinned_session_id: str | None = None,
    ) -> dict[str, Any]:
        if scope_kind not in ("project", "projectless") or (scope_kind == "project" and not project_id):
            raise ProjectSessionError("invalid_request")
        size = self._page_limit(limit)
        decoded = _decode_cursor(cursor) if cursor else None
        if decoded and (decoded.get("scope_kind") != scope_kind or decoded.get("project_id") != project_id):
            raise ProjectSessionError("cursor_invalid")
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = sqlite3.Row
            await self._require_upgrade_complete(db)
            revision = await self.catalog_revision(db)
            if decoded and int(decoded.get("catalog_revision", -1)) != revision:
                raise ProjectSessionError("stale_cursor")
            scope_sql = ("b.project_id=?" if scope_kind == "project" else "b.session_id IS NULL")
            params: list[Any] = [project_id] if scope_kind == "project" else []
            cursor_sql = ""
            if decoded:
                cursor_sql = " AND (COALESCE(ma.last_message_at,s.created_at) < ? OR " \
                    "(COALESCE(ma.last_message_at,s.created_at)=? AND s.id>?))"
                params.extend([decoded["activity_at"], decoded["activity_at"], decoded["session_id"]])
            base_projection = """
              SELECT s.id session_id,s.created_at,b.project_id,b.execution_kind,b.execution_root,
                     b.source_session_id,p.canonical_root,p.filesystem_identity,
                     b.execution_identity,t.title,ma.turn_count,ma.last_message_at,ma.preview
                FROM sessions s
                JOIN session_catalog_entries ce ON ce.session_id=s.id AND ce.product_kind='conversation'
                LEFT JOIN session_project_bindings b ON b.session_id=s.id
                LEFT JOIN projects p ON p.project_id=b.project_id
                LEFT JOIN session_titles t ON t.session_id=s.id
                LEFT JOIN (SELECT session_id,COUNT(*) turn_count,MAX(created_at) last_message_at,
                     MIN(CASE WHEN role='user' AND content<>'' THEN content END) preview
                     FROM messages GROUP BY session_id) ma ON ma.session_id=s.id
                LEFT JOIN session_creation_receipts r ON r.session_id=s.id
                LEFT JOIN session_delivery_state d ON d.session_id=s.id
                LEFT JOIN companion_session_owners o ON o.session_id=s.id
               WHERE """ + scope_sql + " AND d.deleted_at IS NULL AND (o.status IS NULL OR o.status='active') " \
                "AND (r.lifecycle='active' OR o.status='active' OR (r.request_id IS NULL AND ma.turn_count>0))"
            rows = await (await db.execute(base_projection + cursor_sql +
                " ORDER BY COALESCE(ma.last_message_at,s.created_at) DESC,s.id ASC LIMIT ?", (*params, size + 1)
            )).fetchall()
            pinned = None
            if pinned_session_id:
                pinned = await (await db.execute(
                    base_projection + " AND s.id=? LIMIT 1",
                    (*([project_id] if scope_kind == "project" else []), pinned_session_id),
                )).fetchone()
        items = [self._session_descriptor(row) for row in rows[:size]]
        next_cursor = None
        if len(rows) > size and items:
            tail = items[-1]
            next_cursor = _encode_cursor({"schema_version": CATALOG_SCHEMA_VERSION,
                "catalog_revision": revision, "scope_kind": scope_kind, "project_id": project_id,
                "activity_at": tail["activity_at"], "session_id": tail["session_id"]})
        return {"schema_version": CATALOG_SCHEMA_VERSION, "catalog_revision": revision,
                "scope": {"kind": scope_kind, "project_id": project_id}, "items": items,
                "next_cursor": next_cursor, "pinned": self._session_descriptor(pinned) if pinned else None}

    @staticmethod
    def _session_descriptor(row: sqlite3.Row) -> dict[str, Any]:
        execution_kind = row["execution_kind"]
        execution_root = (row["canonical_root"] if execution_kind == "project_root" else row["execution_root"])
        availability = "projectless"
        if row["project_id"]:
            try:
                expected = row["filesystem_identity"] if execution_kind == "project_root" else row["execution_identity"]
                availability = "available" if _filesystem_identity(_strict_directory(execution_root)) == expected else "missing"
            except ProjectSessionError:
                availability = "missing"
        preview = str(row["preview"] or "")[:60]
        return {"session_id": str(row["session_id"]), "project_id": row["project_id"],
                "session_kind": "project" if row["project_id"] else "projectless",
                "execution_kind": execution_kind, "execution_root": execution_root,
                "source_session_id": row["source_session_id"], "title": str(row["title"] or ""),
                "preview": preview, "turn_count": int(row["turn_count"] or 0),
                "activity_at": float(row["last_message_at"] or row["created_at"]),
                "created_at": float(row["created_at"]), "availability": availability}


class SessionCreationService:
    """Atomic, replay-safe product Session creation.

    Optional ``provider_mutation_lock`` preserves the registry→state.db lock
    order. ``fault_inject`` is a test-only crash-boundary probe.
    """

    def __init__(
        self, binding_service: ProjectBindingService, *,
        provider_mutation_lock: asyncio.Lock | None = None,
        session_db: Any = None,
        owner_identity_resolver: Callable[[], Any] | None = None,
        provider_binding_validator: Callable[[str, str, int], None] | None = None,
        fault_inject: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> None:
        self._bindings = binding_service
        self._provider_lock = provider_mutation_lock
        self._session_db = session_db
        self._owner_identity_resolver = owner_identity_resolver
        self._provider_binding_validator = provider_binding_validator
        self._fault_inject = fault_inject

    async def _fault(self, stage: str) -> None:
        if self._fault_inject is None:
            return
        result = self._fault_inject(stage)
        if result is not None:
            await result

    async def _build_handoff(self, db: aiosqlite.Connection, source_sid: str) -> dict[str, Any]:
        exists = await (await db.execute("SELECT 1 FROM sessions WHERE id=?", (source_sid,))).fetchone()
        if exists is None:
            raise ProjectSessionError("session_not_found")
        rows = await (await db.execute(
            "SELECT id,role,content FROM messages WHERE session_id=? AND role IN ('user','assistant') "
            "AND content<>'' ORDER BY id DESC LIMIT ?", (source_sid, MAX_HANDOFF_MESSAGES)
        )).fetchall()
        rows = list(reversed(rows))
        remaining = MAX_HANDOFF_CHARS
        excerpts: list[dict[str, Any]] = []
        for message_id, role, content in rows:
            text = str(content)[:remaining]
            if not text:
                break
            excerpts.append({"message_id": int(message_id), "role": str(role), "excerpt": text})
            remaining -= len(text)
        last_user = next((item["excerpt"] for item in reversed(excerpts) if item["role"] == "user"), "")
        high_water = int(rows[-1][0]) if rows else 0
        return {"schema_version": 1, "source_session_id": source_sid,
                "source_high_water": high_water, "last_user_goal": last_user[:1000],
                "conversation_excerpts": excerpts, "truncated": remaining == 0}

    async def create_conversation_session(
        self, *, request_id: str, project_id: str | None = None,
        source_session_id: str | None = None,
        execution_kind: Literal["project_root", "explicit"] = "project_root",
        execution_root: str | None = None,
    ) -> dict[str, Any]:
        request = str(request_id or "").strip()
        if not request:
            raise ProjectSessionError("invalid_request", "request_id is required")
        if execution_kind not in ("project_root", "explicit"):
            raise ProjectSessionError("invalid_request")
        if project_id is None and execution_kind == "explicit":
            raise ProjectSessionError("invalid_request")
        requested_execution_root = (
            os.path.abspath(os.path.expanduser(str(execution_root)))
            if execution_kind == "explicit" and execution_root
            else None
        )
        intent = {
            "schema_version": 1,
            "project_id": project_id,
            "source_session_id": source_session_id,
            "execution_kind": execution_kind,
            "execution_root": requested_execution_root,
        }
        intent_hash = hashlib.sha256(
            _canonical_json(intent).encode("utf-8")
        ).hexdigest()
        # Lost-ACK replay must not touch a path that may have moved since the
        # original commit, nor depend on a later owner binding epoch.
        async with aiosqlite.connect(self._bindings._db_path) as replay_db:
            receipt = await (await replay_db.execute(
                "SELECT intent_hash,result_json,lifecycle FROM session_creation_receipts "
                "WHERE request_id=?",
                (request,),
            )).fetchone()
        if receipt is not None:
            if str(receipt[0]) != intent_hash:
                raise ProjectSessionError("request_id_conflict")
            if str(receipt[2]) == "deleted":
                raise ProjectSessionError("session_deleted")
            result = json.loads(receipt[1])
            result["replayed"] = True
            return result
        explicit_path: str | None = None
        explicit_identity: str | None = None
        if execution_kind == "explicit":
            if not execution_root:
                raise ProjectSessionError("execution_root_required")
            try:
                path = _strict_directory(execution_root)
            except ProjectSessionError as exc:
                raise ProjectSessionError("execution_root_not_directory") from exc
            explicit_path, explicit_identity = os.fspath(path), _filesystem_identity(path)
        frozen_owner = (
            self._owner_identity_resolver()
            if self._owner_identity_resolver is not None
            else None
        )
        owner_snapshot = None
        if frozen_owner is not None:
            owner = frozen_owner.owner
            owner_snapshot = {
                "owner_kind": "companion_profile",
                "profile_id": str(owner.profile_id),
                "profile_generation": int(owner.profile_generation),
                "binding_epoch": int(frozen_owner.binding_epoch),
            }
        async with AsyncExitStack() as stack:
            if self._provider_lock is not None:
                await stack.enter_async_context(self._provider_lock)
            await stack.enter_async_context(self._bindings._write_lock)
            db = await stack.enter_async_context(aiosqlite.connect(self._bindings._db_path))
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                await self._bindings._require_upgrade_complete(db)
                receipt = await (await db.execute(
                    "SELECT intent_hash,result_json,lifecycle FROM session_creation_receipts WHERE request_id=?",
                    (request,),
                )).fetchone()
                if receipt is not None:
                    if str(receipt[0]) != intent_hash:
                        raise ProjectSessionError("request_id_conflict")
                    if str(receipt[2]) == "deleted":
                        raise ProjectSessionError("session_deleted")
                    result = json.loads(receipt[1]); result["replayed"] = True
                    await db.rollback()
                    return result
                project = None
                if project_id:
                    row = await (await db.execute(
                        "SELECT project_id,display_name,canonical_root,root_kind,filesystem_identity,"
                        "project_revision,created_at,updated_at,last_opened_at FROM projects WHERE project_id=?",
                        (project_id,),
                    )).fetchone()
                    if row is None:
                        raise ProjectSessionError("project_not_found")
                    project = self._bindings._project_from_row(row)
                handoff = await self._build_handoff(db, source_session_id) if source_session_id else None
                now, sid = time.time(), str(uuid.uuid4())
                await db.execute("INSERT INTO sessions(id,created_at,metadata) VALUES(?,?,?)",
                                 (sid, now, _canonical_json({"origin": "project_session_v1"})))
                await db.execute("INSERT INTO session_catalog_entries(session_id,product_kind,created_at) VALUES(?, 'conversation', ?)", (sid, now))
                await db.execute(
                    "INSERT INTO session_delivery_state(session_id,epoch,deleted_at,reason) VALUES(?,0,NULL,NULL)",
                    (sid,),
                )
                if self._session_db is not None:
                    await self._session_db._ensure_memory_binding_in_transaction(
                        db, session_id=sid, user_id="deskpet-local-owner-v1"
                    )
                await self._fault("after_session_core")
                if owner_snapshot is not None:
                    await db.execute(
                        "INSERT INTO companion_session_owners(session_id,owner_kind,profile_id,"
                        "profile_generation,binding_epoch,status,scope_version,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,'active',1,?,?)",
                        (
                            sid,
                            owner_snapshot["owner_kind"],
                            owner_snapshot["profile_id"],
                            owner_snapshot["profile_generation"],
                            owner_snapshot["binding_epoch"],
                            now,
                            now,
                        ),
                    )
                    await db.execute(
                        "INSERT INTO companion_owner_scope_versions(profile_id,profile_generation,scope_version,updated_at) "
                        "VALUES(?,?,1,?) ON CONFLICT(profile_id,profile_generation) DO UPDATE SET "
                        "scope_version=scope_version+1,updated_at=excluded.updated_at",
                        (
                            owner_snapshot["profile_id"],
                            owner_snapshot["profile_generation"],
                            now,
                        ),
                    )
                    route_row = await (await db.execute(
                        "SELECT route_version FROM companion_projection_routes "
                        "WHERE profile_id=? AND profile_generation=?",
                        (
                            owner_snapshot["profile_id"],
                            owner_snapshot["profile_generation"],
                        ),
                    )).fetchone()
                    route_version = int(route_row[0]) + 1 if route_row else 1
                    await db.execute(
                        "INSERT INTO companion_projection_routes(profile_id,profile_generation,binding_epoch,"
                        "target_session_id,target_epoch,route_version,status,created_at,updated_at) "
                        "VALUES(?,?,?,?,0,?,'active',?,?) ON CONFLICT(profile_id,profile_generation) DO UPDATE SET "
                        "binding_epoch=excluded.binding_epoch,target_session_id=excluded.target_session_id,"
                        "target_epoch=excluded.target_epoch,route_version=excluded.route_version,status='active',"
                        "updated_at=excluded.updated_at",
                        (
                            owner_snapshot["profile_id"],
                            owner_snapshot["profile_generation"],
                            owner_snapshot["binding_epoch"],
                            sid,
                            route_version,
                            now,
                            now,
                        ),
                    )
                    if self._session_db is not None:
                        await self._session_db._insert_companion_route_outbox(
                            db,
                            profile_id=owner_snapshot["profile_id"],
                            profile_generation=owner_snapshot["profile_generation"],
                            route_version=route_version,
                            event_kind="route_changed",
                            target_session_id=sid,
                            target_epoch=0,
                            now=now,
                        )
                await self._fault("after_owner_route")
                if project is not None:
                    await db.execute(
                        "INSERT INTO session_project_bindings(session_id,project_id,execution_kind,"
                        "execution_root,execution_identity,source_session_id,handoff_json,binding_version,created_at) "
                        "VALUES(?,?,?,?,?,?,?,1,?)",
                        (sid, project.project_id, execution_kind, explicit_path, explicit_identity,
                         source_session_id, _canonical_json(handoff) if handoff else None, now),
                    )
                descriptor = {"session_id": sid, "project_id": project_id,
                    "session_kind": "project" if project_id else "projectless",
                    "execution_kind": execution_kind if project_id else None,
                    "execution_root": (project.canonical_root if project and execution_kind == "project_root" else explicit_path),
                    "source_session_id": source_session_id, "title": "", "preview": "",
                    "turn_count": 0, "activity_at": now, "created_at": now,
                    "availability": "available" if project_id else "projectless"}
                result = {"session": descriptor, "replayed": False}
                provider_row = None
                if source_session_id:
                    provider_row = await (await db.execute(
                        "SELECT provider_id,preferred_model,model_params,provider_incarnation_id,"
                        "provider_config_revision FROM code_session_provider WHERE base_session_id=?",
                        (source_session_id,),
                    )).fetchone()
                frozen_provider = tuple(provider_row) if provider_row is not None else (None,) * 5
                if (
                    provider_row is not None
                    and provider_row[0] is not None
                    and self._provider_binding_validator is not None
                ):
                    try:
                        self._provider_binding_validator(
                            str(provider_row[0]),
                            str(provider_row[3]),
                            int(provider_row[4]),
                        )
                    except Exception as exc:
                        raise ProjectSessionError("provider_binding_stale") from exc
                await db.execute(
                    "INSERT INTO code_session_provider(base_session_id,provider_id,preferred_model,model_params,"
                    "provider_incarnation_id,provider_config_revision,binding_epoch,updated_at) "
                    "VALUES(?,?,?,?,?,?,1,?)",
                    (sid, *frozen_provider, now),
                )
                if self._session_db is not None:
                    await self._session_db.advance_context_usage_binding_state_tx(
                        db, session_id=sid, binding_epoch=1,
                        provider_id=frozen_provider[0], model_id=frozen_provider[1],
                    )
                await self._fault("after_binding_provider")
                if source_session_id:
                    owner_row = await (await db.execute(
                        "SELECT owner_kind,profile_id,profile_generation,binding_epoch "
                        "FROM companion_session_owners WHERE session_id=? AND status='active'",
                        (source_session_id,),
                    )).fetchone()
                    if owner_snapshot is None and owner_row is not None:
                        await db.execute(
                            "INSERT INTO companion_session_owners(session_id,owner_kind,profile_id,profile_generation,"
                            "binding_epoch,status,scope_version,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,'active',1,?,?)", (sid, *tuple(owner_row), now, now),
                        )
                        await db.execute(
                            "INSERT INTO companion_owner_scope_versions(profile_id,profile_generation,scope_version,updated_at) "
                            "VALUES(?,?,1,?) ON CONFLICT(profile_id,profile_generation) DO UPDATE SET "
                            "scope_version=scope_version+1,updated_at=excluded.updated_at",
                            (owner_row[1], owner_row[2], now),
                        )
                await self._fault("before_receipt")
                await db.execute(
                    "INSERT INTO session_creation_receipts(request_id,intent_hash,session_id,result_json,lifecycle,created_at) "
                    "VALUES(?,?,?,?, 'active', ?)", (request, intent_hash, sid, _canonical_json(result), now)
                )
                await self._fault("after_receipt")
                await self._fault("before_commit")
                await db.commit()
                await self._fault("after_commit")
                return result
            except Exception:
                await db.rollback()
                raise

    async def mark_deleted(self, session_id: str) -> None:
        sid, now = str(session_id or "").strip(), time.time()
        if not sid:
            raise ProjectSessionError("session_not_found")
        async with self._bindings._write_lock:
            async with aiosqlite.connect(self._bindings._db_path) as db:
                await db.execute("BEGIN IMMEDIATE")
                exists = await (await db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,))).fetchone()
                if exists is None:
                    raise ProjectSessionError("session_not_found")
                deleted = await (await db.execute(
                    "SELECT deleted_at FROM session_delivery_state WHERE session_id=?",
                    (sid,),
                )).fetchone()
                if deleted is not None and deleted[0] is not None:
                    await db.rollback()
                    return
                await self._fault("before_delete_writes")
                await db.execute(
                    "UPDATE project_session_catalog_state SET catalog_revision="
                    "catalog_revision+1 WHERE singleton=1"
                )
                await db.execute("DELETE FROM messages WHERE session_id=?", (sid,))
                await db.execute("DELETE FROM session_titles WHERE session_id=?", (sid,))
                await db.execute(
                    "INSERT INTO session_delivery_state(session_id,epoch,deleted_at,reason) VALUES(?,1,?,'deleted') "
                    "ON CONFLICT(session_id) DO UPDATE SET epoch=epoch+1,deleted_at=excluded.deleted_at,reason='deleted'",
                    (sid, now),
                )
                if self._session_db is not None:
                    epoch_row = await (await db.execute(
                        "SELECT epoch FROM session_delivery_state WHERE session_id=?", (sid,)
                    )).fetchone()
                    await self._session_db._tombstone_companion_route(
                        db, session_id=sid, new_epoch=int(epoch_row[0]),
                        reason="deleted", now=now,
                    )
                else:
                    await db.execute("UPDATE companion_session_owners SET status='tombstoned',updated_at=? WHERE session_id=? AND status='active'", (now, sid))
                await db.execute("UPDATE session_creation_receipts SET lifecycle='deleted',deleted_at=? WHERE session_id=? AND lifecycle='active'", (now, sid))
                await self._fault("after_delete_tombstones")
                await self._fault("before_delete_commit")
                await db.commit()
                await self._fault("after_delete_commit")


__all__ = [
    "ProjectBindingService", "SessionCreationService", "ProjectSessionError",
    "ProjectRecord", "SessionProjectBinding", "RegistrationPreview",
    "ProjectBoundWorkspace", "ProjectlessWorkspace", "ProjectMissingWorkspace",
    "ProjectWorkspaceResolution", "WorkspaceResolution", "resolve_registration",
]
