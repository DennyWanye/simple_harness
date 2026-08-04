# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Application discovery and launch built on structured process leases."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

import psutil

from ..capabilities import ToolExecutionContext
from ..context_adapter import legacy_execution_context
from .process_tools import get_process_tool_service


def _error(code: str, message: str) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message}},
        ensure_ascii=False,
    )


def _discover_windows_app_paths(query: str) -> list[str]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    names = [query] if query.casefold().endswith(".exe") else [query, f"{query}.exe"]
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    results: list[str] = []
    for root in roots:
        for name in names:
            key_path = rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{name}"
            for access in (
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0),
            ):
                try:
                    with winreg.OpenKey(root, key_path, 0, access) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                    if isinstance(value, str):
                        results.append(value)
                except OSError:
                    continue
    return results


def discover_app(query: str) -> list[dict[str, Any]]:
    if not query.strip():
        raise ValueError("query must be non-empty")
    candidates: list[tuple[str, str]] = []
    direct = Path(query).expanduser()
    if direct.is_absolute() or direct.parent != Path("."):
        candidates.append(("explicit_path", str(direct.resolve(strict=False))))
    located = shutil.which(query)
    if located:
        candidates.append(("path", located))
    for value in _discover_windows_app_paths(query):
        candidates.append(("windows_app_paths", value))

    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for source, raw_path in candidates:
        path = Path(raw_path).expanduser().resolve(strict=False)
        identity = os.path.normcase(str(path)) if os.name == "nt" else str(path)
        if identity in seen:
            continue
        seen.add(identity)
        launchable = path.is_file()
        results.append(
            {
                "source": source,
                "executable": str(path),
                "discovered": True,
                "launchable_probe": launchable,
            }
        )
    return results


async def app_discover(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    del task_id, execution_context
    query = args.get("query")
    if not isinstance(query, str):
        return _error("invalid_arguments", "query must be a string")
    try:
        candidates = discover_app(query)
    except (OSError, ValueError) as exc:
        return _error("app_discovery_failed", str(exc))
    return json.dumps(
        {
            "ok": True,
            "query": query,
            "discovered": bool(candidates),
            "candidates": candidates,
            "note": "discovery is not proof that the application launched",
        },
        ensure_ascii=False,
    )


async def app_launch(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    query = args.get("app") or args.get("executable")
    argv = args.get("argv", [])
    if not isinstance(query, str) or not query:
        return _error("invalid_arguments", "app or executable is required")
    if not isinstance(argv, list) or not all(isinstance(part, str) for part in argv):
        return _error("invalid_arguments", "argv must be an array of strings")
    try:
        candidates = discover_app(query)
        launchable = next(
            (item for item in candidates if item["launchable_probe"]), None
        )
        if launchable is None:
            return _error("app_not_launchable", "no launchable application candidate found")
        context = legacy_execution_context(args, task_id, execution_context)
        result = await get_process_tool_service().start(
            executable=launchable["executable"],
            argv=tuple(argv),
            cwd=str(args["cwd"]) if args.get("cwd") is not None else None,
            environment=args.get("environment"),
            root_run_id=context.root_run_id or context.run_id or task_id or "legacy",
        )
        await asyncio.sleep(0.1)
        launch_verified = False
        try:
            process = psutil.Process(int(result["pid"]))
            launch_verified = (
                abs(float(process.create_time()) - float(result["creation_time"])) <= 0.01
                and process.is_running()
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            launch_verified = False
        return json.dumps(
            {
                "ok": True,
                "candidate": launchable,
                "process": result,
                "launch_verified": launch_verified,
                "verification": "pid_identity_probe",
            },
            ensure_ascii=False,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _error("app_launch_failed", str(exc))


def resolve_app_launch_resources(args: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    query = args.get("app") or args.get("executable")
    if not isinstance(query, str) or not query:
        raise ValueError("app or executable is required")
    candidate = next(
        (item for item in discover_app(query) if item["launchable_probe"]), None
    )
    if candidate is None:
        raise ValueError("no launchable application candidate found")
    return (
        {
            "kind": "application",
            "application": query,
            "executable": candidate["executable"],
        },
    )


__all__ = [
    "app_discover",
    "app_launch",
    "discover_app",
    "resolve_app_launch_resources",
]
