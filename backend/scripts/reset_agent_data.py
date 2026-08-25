#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicit development-only reset for DeskPet's complete agent storage set."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys


_EXPECTED_EXECUTION_NAMES = frozenset({"execution-v1.sqlite3", "execution-v6.sqlite3"})


class AgentDataResetRefused(RuntimeError):
    code = "agent_data_reset_refused"


def _validated_paths(raw: tuple[str, str, str]) -> tuple[Path, Path, Path]:
    paths = tuple(Path(value).expanduser() for value in raw)
    if any(not value.is_absolute() for value in paths):
        raise AgentDataResetRefused("database paths must be absolute")
    if (
        paths[0].name != "state.db"
        or paths[1].name not in _EXPECTED_EXECUTION_NAMES
        or paths[2].name != "memory.db"
    ):
        raise AgentDataResetRefused("database filenames do not match the storage contract")
    resolved: list[Path] = []
    forbidden = {Path("/"), Path.home().resolve(), Path(__file__).resolve().parents[2]}
    for supplied in paths:
        if ".." in supplied.parts:
            raise AgentDataResetRefused("database paths may not traverse parents")
        for component in (supplied, *supplied.parents):
            if component.exists() and component.is_symlink():
                raise AgentDataResetRefused("database paths may not contain symlinks")
        value = supplied.resolve()
        if value in forbidden or value.parent in forbidden:
            raise AgentDataResetRefused("database path is too broad")
        resolved.append(value)
    if len(set(resolved)) != 3:
        raise AgentDataResetRefused("database paths must be distinct")
    return tuple(resolved)  # type: ignore[return-value]


def _assert_service_stopped(pid_file: Path | None) -> None:
    if pid_file is None or not pid_file.exists():
        return
    raw = pid_file.read_text(encoding="utf-8").strip()
    try:
        pid = int(raw)
    except ValueError as exc:
        raise AgentDataResetRefused("service pid file is invalid") from exc
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise AgentDataResetRefused("service process cannot be verified") from exc
    raise AgentDataResetRefused("DeskPet service is still running")


async def reset_storage_set(
    *,
    state_db: str,
    execution_db: str,
    memory_db: str,
    pid_file: str | None = None,
) -> tuple[Path, Path, Path]:
    if os.environ.get("DESKPET_DEV_MODE", "").lower() not in {"1", "true", "yes"}:
        raise AgentDataResetRefused("DESKPET_DEV_MODE is required")
    state, execution, memory = _validated_paths((state_db, execution_db, memory_db))
    _assert_service_stopped(None if pid_file is None else Path(pid_file).resolve())
    for database in (state, execution, memory):
        database.parent.mkdir(parents=True, exist_ok=True)
        for target in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
            if target.exists():
                if target.is_symlink() or not target.is_file():
                    raise AgentDataResetRefused("storage target is not a regular file")
                target.unlink()

    from deskpet.memory.session_db import SessionDB
    from simple_harness.execution.sqlite import Database
    from simple_harness_memory.backends.sqlite import SQLiteMemoryBackend

    session = SessionDB(state)
    await session.initialize()
    await session.close()
    execution_handle = Database.open(execution, wal=True)
    execution_handle.close()
    memory_handle = SQLiteMemoryBackend(str(memory))
    await memory_handle.initialize()
    await memory_handle.close()
    return state, execution, memory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-db", required=True)
    parser.add_argument("--execution-db", required=True)
    parser.add_argument("--memory-db", required=True)
    parser.add_argument("--pid-file")
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    if args.confirm != "RESET-AGENT-DATA":
        raise AgentDataResetRefused("exact confirmation token is required")
    asyncio.run(
        reset_storage_set(
            state_db=args.state_db,
            execution_db=args.execution_db,
            memory_db=args.memory_db,
            pid_file=args.pid_file,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AgentDataResetRefused as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
