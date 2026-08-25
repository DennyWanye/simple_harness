# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Crash-resumable v32 semantic upgrade from retired ``code_sessions``."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable

import aiosqlite

from deskpet.session.project_binding import _filesystem_identity, _strict_directory


CHUNK_SIZE = 100


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_digest(base_session_id: str, project_root: str, project_name: str) -> str:
    raw = json.dumps([base_session_id, project_root, project_name], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _product_session_id(session_id: str) -> bool:
    if session_id.startswith("task-"):
        return True
    try:
        uuid.UUID(session_id)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


async def run_project_session_upgrade(
    db_path: str | Path,
    *,
    backup_path: str | Path | None = None,
    fault_inject: Callable[[str], None] | None = None,
) -> None:
    """Resume the v32 semantic backfill until its durable gate is completed.

    Every source row gets exactly one low-sensitivity outcome.  A completed
    state is a strict no-op.  Existing immutable bindings always win.
    """

    path = Path(db_path)
    backup = Path(backup_path) if backup_path else None
    backup_sha = _sha256_file(backup) if backup and backup.exists() else None
    if fault_inject:
        fault_inject("before_scan")
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN IMMEDIATE")
        state = await (await db.execute(
            "SELECT phase,source_high_water,last_base_session_id FROM project_session_backfill_state WHERE singleton=1"
        )).fetchone()
        if state is None:
            raise RuntimeError("project_session_backfill_state_missing")
        if str(state[0]) == "completed":
            await db.rollback()
            return
        now = time.time()
        high_water = state[1]
        if high_water is None:
            high_water_row = await (await db.execute("SELECT MAX(base_session_id),COUNT(*) FROM code_sessions")).fetchone()
            high_water, source_count = high_water_row[0], int(high_water_row[1])
            await db.execute(
                "UPDATE project_session_backfill_state SET phase='scanning',source_high_water=?,"
                "source_count=?,backup_path=?,backup_sha256=?,started_at=COALESCE(started_at,?),updated_at=?,error_code=NULL WHERE singleton=1",
                (high_water, source_count, os.fspath(backup) if backup else None, backup_sha, now, now),
            )
        # Turn the old Python post-filter into a durable, queryable classifier.
        session_rows = await (await db.execute("SELECT id,created_at FROM sessions")).fetchall()
        await db.executemany(
            "INSERT OR IGNORE INTO session_catalog_entries(session_id,product_kind,created_at) VALUES(?, 'conversation', ?)",
            [(str(row[0]), float(row[1])) for row in session_rows if _product_session_id(str(row[0]))],
        )
        await db.execute("UPDATE project_session_backfill_state SET phase='applying',updated_at=? WHERE singleton=1", (now,))
        await db.commit()
    if fault_inject:
        fault_inject("after_scan_commit")

    while True:
        async with aiosqlite.connect(path) as db:
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            state = await (await db.execute(
                "SELECT source_high_water,last_base_session_id FROM project_session_backfill_state WHERE singleton=1"
            )).fetchone()
            high_water, last = state[0], state[1]
            if high_water is None:
                rows = []
            else:
                rows = await (await db.execute(
                    "SELECT base_session_id,project_root,project_name FROM code_sessions "
                    "WHERE base_session_id<=? AND (? IS NULL OR base_session_id>?) "
                    "ORDER BY base_session_id LIMIT ?", (high_water, last, last, CHUNK_SIZE)
                )).fetchall()
            if not rows:
                await db.execute("UPDATE project_session_backfill_state SET phase='verifying',updated_at=? WHERE singleton=1", (time.time(),))
                await db.commit()
                break
            counters = {"migrated": 0, "invalid": 0, "missing": 0, "conflict": 0, "prebound": 0}
            for base_sid, raw_root, project_name in rows:
                sid, raw = str(base_sid), str(raw_root or "")
                digest = _source_digest(sid, raw, str(project_name or ""))
                prior = await (await db.execute(
                    "SELECT outcome FROM project_session_backfill_outcomes WHERE base_session_id=?", (sid,)
                )).fetchone()
                if prior is not None:
                    continue
                session_exists = await (await db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,))).fetchone()
                binding = await (await db.execute("SELECT project_id FROM session_project_bindings WHERE session_id=?", (sid,))).fetchone()
                project_id: str | None = None
                detail: str | None = None
                if binding is not None:
                    outcome, project_id = "prebound", str(binding[0])
                elif session_exists is None:
                    outcome, detail = "invalid", "session_missing"
                else:
                    try:
                        root = _strict_directory(raw)
                    except Exception as exc:  # noqa: BLE001
                        code = str(getattr(exc, "code", "path_invalid"))
                        outcome, detail = ("missing" if code == "path_not_found" else "invalid"), code
                    else:
                        identity = _filesystem_identity(root)
                        project_row = await (await db.execute(
                            "SELECT project_id FROM projects WHERE filesystem_identity=?", (identity,)
                        )).fetchone()
                        project_id = str(project_row[0]) if project_row else str(uuid.uuid4())
                        now = time.time()
                        if project_row is None:
                            await db.execute(
                                "INSERT INTO projects(project_id,display_name,canonical_root,root_kind,filesystem_identity,"
                                "project_revision,created_at,updated_at,last_opened_at) VALUES(?,?,?,?,?,1,?,?,?)",
                                (project_id, str(project_name or root.name)[:120], os.fspath(root),
                                 "folder", identity, now, now, now),
                            )
                        await db.execute(
                            "INSERT INTO session_project_bindings(session_id,project_id,execution_kind,"
                            "binding_version,created_at) VALUES(?,?,'project_root',1,?)",
                            (sid, project_id, now),
                        )
                        outcome = "migrated"
                await db.execute(
                    "INSERT INTO project_session_backfill_outcomes(base_session_id,source_digest,outcome,project_id,detail_code,recorded_at) "
                    "VALUES(?,?,?,?,?,?)", (sid, digest, outcome, project_id, detail, time.time()),
                )
                counters[outcome] += 1
            await db.execute(
                "UPDATE project_session_backfill_state SET last_base_session_id=?,"
                "migrated_count=migrated_count+?,skipped_count=skipped_count+?,"
                "conflict_count=conflict_count+?,updated_at=? WHERE singleton=1",
                (str(rows[-1][0]), counters["migrated"], counters["invalid"] + counters["missing"] + counters["prebound"],
                 counters["conflict"], time.time()),
            )
            await db.commit()
        if fault_inject:
            fault_inject("after_chunk_commit")

    async with aiosqlite.connect(path) as db:
        await db.execute("BEGIN IMMEDIATE")
        state = await (await db.execute(
            "SELECT source_count,source_high_water FROM project_session_backfill_state WHERE singleton=1"
        )).fetchone()
        source_count, high_water = int(state[0]), state[1]
        outcome_count = int((await (await db.execute(
            "SELECT COUNT(*) FROM project_session_backfill_outcomes WHERE (? IS NULL OR base_session_id<=?)",
            (high_water, high_water),
        )).fetchone())[0])
        if outcome_count != source_count:
            await db.execute(
                "UPDATE project_session_backfill_state SET phase='failed',error_code='source_count_mismatch',updated_at=? WHERE singleton=1",
                (time.time(),),
            )
            await db.commit()
            raise RuntimeError("project_session_backfill_source_count_mismatch")
        outcomes = await (await db.execute(
            "SELECT base_session_id,source_digest,outcome,COALESCE(project_id,''),COALESCE(detail_code,'') "
            "FROM project_session_backfill_outcomes ORDER BY base_session_id"
        )).fetchall()
        digest = hashlib.sha256(json.dumps([list(row) for row in outcomes], separators=(",", ":")).encode()).hexdigest()
        now = time.time()
        if fault_inject:
            fault_inject("before_completion_commit")
        await db.execute(
            "UPDATE project_session_backfill_state SET phase='completed',outcome_digest=?,completed_at=?,updated_at=?,error_code=NULL WHERE singleton=1",
            (digest, now, now),
        )
        await db.commit()
    if fault_inject:
        fault_inject("after_completion_commit")


__all__ = ["run_project_session_upgrade"]
