#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Guarded offline restore of the pre-v32 state.db backup."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time


class RestoreRefused(RuntimeError):
    code = "state_db_restore_refused"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(raw: str, *, name: str | None = None) -> Path:
    supplied = Path(raw).expanduser()
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise RestoreRefused("paths must be absolute without parent traversal")
    for component in (supplied, *supplied.parents):
        if component.exists() and component.is_symlink():
            raise RestoreRefused("paths may not contain symlinks")
    resolved = supplied.resolve(strict=True)
    if not resolved.is_file() or (name and resolved.name != name):
        raise RestoreRefused("unexpected restore target")
    return resolved


def _assert_stopped(pid_file: str | None) -> None:
    if not pid_file:
        return
    path = _safe_file(pid_file)
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except ValueError as exc:
        raise RestoreRefused("invalid pid file") from exc
    except PermissionError as exc:
        raise RestoreRefused("cannot verify service process") from exc
    raise RestoreRefused("service is still running")


def restore_state_db(
    *, state_db: str, backup: str, expected_sha256: str,
    confirm: str, pid_file: str | None = None,
) -> Path:
    if confirm != "RESTORE-STATE-DB":
        raise RestoreRefused("exact confirmation token is required")
    target = _safe_file(state_db, name="state.db")
    source = _safe_file(backup)
    _assert_stopped(pid_file)
    expected = expected_sha256.strip().lower()
    if len(expected) != 64 or _sha256(source) != expected:
        raise RestoreRefused("backup sha256 mismatch")
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RestoreRefused("backup quick_check failed")
        if db.execute("PRAGMA user_version").fetchone()[0] != 31:
            raise RestoreRefused("backup is not schema v31")
        marker = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version='023_official_memory_integration_v31.sql'"
        ).fetchone()
        if marker is None:
            raise RestoreRefused("backup v31 marker missing")
    stamp = int(time.time() * 1000)
    quarantine = target.with_name(f"state.db.quarantine.{stamp}")
    staging = target.with_name(f"state.db.restore.{stamp}.tmp")
    shutil.copy2(source, staging)
    try:
        os.replace(target, quarantine)
        os.replace(staging, target)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{target}{suffix}")
            if sidecar.exists():
                if sidecar.is_symlink() or not sidecar.is_file():
                    raise RestoreRefused("unsafe SQLite sidecar")
                sidecar.unlink()
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RestoreRefused("restored database quick_check failed")
        return quarantine
    except Exception:
        if target.exists():
            target.unlink()
        if quarantine.exists():
            os.replace(quarantine, target)
        raise
    finally:
        if staging.exists():
            staging.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-db", required=True)
    parser.add_argument("--backup", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--pid-file")
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    restore_state_db(state_db=args.state_db, backup=args.backup,
                     expected_sha256=args.expected_sha256, confirm=args.confirm,
                     pid_file=args.pid_file)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RestoreRefused as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
