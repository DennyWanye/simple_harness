# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Register built-in OS tools into a ToolRegistry.

Called once at backend startup from main.py — kept out of __init__.py
so importing the package doesn't trigger registration as a side effect
(prevents duplicate-register warnings during tests).
"""
from __future__ import annotations

from typing import Any

from ..context_adapter import bind_context_handler
from .app_tools import app_discover, app_launch
from .desktop_create_file import desktop_create_file
from .download_tools import download_file
from .edit_file import edit_file
from .list_directory import list_directory
from .move_file import move_file
from .process_tools import process_list, process_start, process_stop, process_wait
from .read_file import read_file
from .run_shell import run_shell
from .web_fetch import web_fetch
from .write_file import write_file
from ..resource_scopes import (
    app_launch_scope,
    desktop_create_file_scope,
    download_file_scope,
    edit_file_scope,
    list_directory_scope,
    move_file_scope,
    process_start_scope,
    process_stop_scope,
    read_file_scope,
    run_shell_scope,
    web_fetch_scope,
    write_file_scope,
)


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def register_os_tools(registry) -> None:  # type: ignore[no-untyped-def]
    """Register structured OS tools onto ``registry``.

    Idempotent — re-registration replaces (with the existing warning
    log). Safe to call from main.py at startup.
    """
    registry.register(
        name="read_file",
        toolset="os",
        schema=_schema(
            "read_file",
            "Read a text file. Returns content, line count, truncation flag. Pass offset+limit for large files.",
            {
                "path": {"type": "string", "description": "Absolute file path"},
                "offset": {"type": "integer", "default": 0},
                "limit": {"type": "integer", "default": 2000},
            },
            ["path"],
        ),
        handler=read_file,
        context_handler=bind_context_handler(read_file),
        permission_category="read_file",
        resource_scope_resolver=read_file_scope,
        resource_scope_resolver_id="builtin:os:read_file",
    )

    registry.register(
        name="write_file",
        toolset="os",
        schema=_schema(
            "write_file",
            "Create a new file. Refuses to overwrite unless overwrite=true. Creates parent dirs. "
            "IMPORTANT: content is HARD-CAPPED at 4096 characters per call (G3 reliability fix). "
            "For longer files, split into multiple calls: first call with content=<part 1>, "
            "then subsequent calls with mode='append' for each chunk. Calls over 4KB are "
            "rejected before execution to avoid streaming JSON-escape failures.",
            {
                "path": {"type": "string", "description": "Absolute file path"},
                "content": {
                    "type": "string",
                    "description": "File content. MAX 4096 chars per call — split longer files into append-mode chunks.",
                    "maxLength": 4096,
                },
                "overwrite": {"type": "boolean", "default": False},
            },
            ["path", "content"],
        ),
        handler=write_file,
        context_handler=bind_context_handler(write_file),
        permission_category="write_file",
        resource_scope_resolver=write_file_scope,
        resource_scope_resolver_id="builtin:os:write_file",
        concurrency_safe=False,  # G3: filesystem write — must serialize
    )

    registry.register(
        name="edit_file",
        toolset="os",
        schema=_schema(
            "edit_file",
            "Replace exact text in a file. old_string must be unique unless replace_all=true. "
            "If exact match fails and fuzzy=true (default), falls back to whitespace/anchor "
            "matching and otherwise returns did_you_mean suggestions; set fuzzy=false to require "
            "an exact match only.",
            {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean", "default": False},
                "fuzzy": {"type": "boolean", "default": True},
            },
            ["path", "old_string", "new_string"],
        ),
        handler=edit_file,
        context_handler=bind_context_handler(edit_file),
        permission_category="write_file",
        resource_scope_resolver=edit_file_scope,
        resource_scope_resolver_id="builtin:os:edit_file",
        concurrency_safe=False,  # G3: in-place file mutation — must serialize
    )

    registry.register(
        name="list_directory",
        toolset="os",
        schema=_schema(
            "list_directory",
            "List files + subdirectories of a path with name/type/size.",
            {
                "path": {"type": "string"},
                "max_entries": {"type": "integer", "default": 100},
            },
            ["path"],
        ),
        handler=list_directory,
        permission_category="read_file",
        resource_scope_resolver=list_directory_scope,
        resource_scope_resolver_id="builtin:os:list_directory",
    )

    registry.register(
        name="run_shell",
        toolset="os",
        schema=_schema(
            "run_shell",
            (
                "Execute a shell command. On Windows the runtime auto-picks the "
                "best shell available: Git Bash (preferred) → bundled busybox sh "
                "→ PowerShell → cmd. Use Linux-style commands (ls / grep / sed / "
                "awk / find / cat / curl all work via Git Bash or busybox); "
                "forward-slash paths are accepted. Captures stdout/stderr/"
                "exit_code. Default timeout 30s. For a long-lived GUI app or "
                "server, prefer process_start (or app_launch) so this call can "
                "return immediately; a trailing POSIX '&' is detached from "
                "run_shell's stdio as a compatibility fallback. Asks user "
                "permission first."
            ),
            {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
            },
            ["command"],
        ),
        handler=run_shell,
        context_handler=bind_context_handler(run_shell),
        permission_category="shell",
        resource_scope_resolver=run_shell_scope,
        resource_scope_resolver_id="builtin:os:run_shell",
        dangerous=True,
        # P5-S1: shell commands can legitimately take a while (pip install,
        # cargo build). 5 minutes is the operative wall — the supervisor
        # watchdog still sits one level above this and will catch true
        # hangs at the 15-minute session-inactivity threshold.
        timeout_seconds=300.0,
        concurrency_safe=False,  # G3: arbitrary shell side effects — serialize
    )

    # NOTE: ``web_fetch`` is intentionally NOT registered here — the
    # full-featured implementation lives in ``deskpet/tools/web_tools.py``
    # (with robots.txt, rate-limiting, and 429 block-cache). We patch
    # *that* spec to set the network permission_category instead of
    # overwriting it with our minimal version.
    existing = registry.get("web_fetch")
    if existing is not None and existing.permission_category != "network":
        # Re-register through the public API so the warning appears
        # once per startup (matches the existing convention).
        # WI-T4.1 v3 (MR-T-11-6): 这是一次「显式覆盖」（patch 既有 spec
        # 的 permission_category），双方必须有一方 opt-in replace_allowed
        # 否则 register 会抛 ToolNameConflictError → 整个 v2 init 失败
        # → plugin/permission gate 一并降级（main.py:508-516 except 分支）。
        registry.register(
            name="web_fetch",
            toolset=existing.toolset,
            schema=existing.schema,
            handler=existing.handler,
            context_handler=existing.context_handler,
            check_fn=existing.check_fn,
            requires_env=list(existing.requires_env),
            permission_category="network",
            source=existing.source,
            dangerous=existing.dangerous,
            resource_scope_resolver=web_fetch_scope,
            resource_scope_resolver_id="builtin:os:web_fetch",
            replace_allowed=True,
        )

    registry.register(
        name="desktop_create_file",
        toolset="os",
        schema=_schema(
            "desktop_create_file",
            "Create a file on the user's Desktop with given name and content. Cross-platform.",
            {
                "name": {"type": "string", "description": "File name (no path separators)"},
                "content": {"type": "string"},
            },
            ["name", "content"],
        ),
        handler=desktop_create_file,
        permission_category="desktop_write",
        resource_scope_resolver=desktop_create_file_scope,
        resource_scope_resolver_id="builtin:os:desktop_create_file",
        concurrency_safe=False,  # G3: filesystem write — must serialize
    )

    registry.register(
        name="move_file",
        toolset="os",
        schema=_schema(
            "move_file",
            "Atomically move one file after verifying its SHA-256. Refuses overwrite by default.",
            {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "expected_source_hash": {
                    "type": "string",
                    "pattern": "^[0-9a-fA-F]{64}$",
                },
                "overwrite": {"type": "boolean", "default": False},
            },
            ["source", "destination", "expected_source_hash"],
        ),
        handler=move_file,
        context_handler=bind_context_handler(move_file),
        permission_category="write_file",
        resource_scope_resolver=move_file_scope,
        resource_scope_resolver_id="builtin:os:move_file",
        outcome_parser_id="json_error_envelope_v1",
        concurrency_safe=False,
    )

    registry.register(
        name="process_list",
        toolset="os",
        schema=_schema(
            "process_list",
            "List structured process identities. This does not stop processes.",
            {
                "query": {"type": "string", "default": ""},
                "max_entries": {"type": "integer", "default": 100},
            },
            [],
        ),
        handler=process_list,
        context_handler=bind_context_handler(process_list),
        permission_category="read_file",
    )

    registry.register(
        name="process_start",
        toolset="os",
        schema=_schema(
            "process_start",
            (
                "Start a long-lived GUI app, game, server, or script and return "
                "immediately with a same-Run process lease. Pass the executable "
                "and each argument separately; shell command strings are "
                "rejected. To launch a PowerShell script, use executable="
                "'powershell.exe' and argv=['-NoProfile','-File','C:/.../x.ps1']. "
                "For desktop control after launch, call window_list and pass "
                "its pid + creation_time + hwnd to window_focus, "
                "window_capture, or window_key."
            ),
            {
                "executable": {"type": "string"},
                "argv": {"type": "array", "items": {"type": "string"}, "default": []},
                "cwd": {"type": "string"},
                "environment": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            ["executable", "argv"],
        ),
        handler=process_start,
        context_handler=bind_context_handler(process_start),
        permission_category="shell",
        resource_scope_resolver=process_start_scope,
        resource_scope_resolver_id="builtin:os:process_start",
        dangerous=True,
        concurrency_safe=False,
    )

    registry.register(
        name="process_wait",
        toolset="os",
        schema=_schema(
            "process_wait",
            "Wait for a same-run process lease without killing it on timeout.",
            {
                "lease_id": {"type": "string"},
                "timeout_seconds": {"type": "number", "default": 0},
            },
            ["lease_id"],
        ),
        handler=process_wait,
        context_handler=bind_context_handler(process_wait),
        permission_category="read_file",
        timeout_seconds=305.0,
    )

    registry.register(
        name="process_stop",
        toolset="os",
        schema=_schema(
            "process_stop",
            "Stop a same-run lease or an exact PID identity. Process-name kills are forbidden.",
            {
                "lease_id": {"type": "string"},
                "pid": {"type": "integer"},
                "creation_time": {"type": "number"},
                "command_line": {"type": "array", "items": {"type": "string"}},
            },
            [],
        ),
        handler=process_stop,
        context_handler=bind_context_handler(process_stop),
        permission_category="shell",
        resource_scope_resolver=process_stop_scope,
        resource_scope_resolver_id="builtin:os:process_stop",
        dangerous=True,
        concurrency_safe=False,
    )

    registry.register(
        name="app_discover",
        toolset="os",
        schema=_schema(
            "app_discover",
            "Discover launch candidates and report a separate launchability probe.",
            {"query": {"type": "string"}},
            ["query"],
        ),
        handler=app_discover,
        context_handler=bind_context_handler(app_discover),
        permission_category="read_file",
    )

    registry.register(
        name="app_launch",
        toolset="os",
        schema=_schema(
            "app_launch",
            "Launch a discovered application with argv and verify the PID identity afterward.",
            {
                "app": {"type": "string"},
                "argv": {"type": "array", "items": {"type": "string"}, "default": []},
                "cwd": {"type": "string"},
                "environment": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            ["app", "argv"],
        ),
        handler=app_launch,
        context_handler=bind_context_handler(app_launch),
        permission_category="shell",
        resource_scope_resolver=app_launch_scope,
        resource_scope_resolver_id="builtin:os:app_launch",
        dangerous=True,
        concurrency_safe=False,
    )

    registry.register(
        name="download_file",
        toolset="os",
        schema=_schema(
            "download_file",
            "Download one http/https URL with a hard byte cap, optional SHA-256, and atomic publish.",
            {
                "url": {"type": "string"},
                "destination": {"type": "string"},
                "expected_sha256": {
                    "type": "string",
                    "pattern": "^[0-9a-fA-F]{64}$",
                },
                "max_bytes": {"type": "integer", "minimum": 1},
                "require_hash": {"type": "boolean", "default": False},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["url", "destination", "max_bytes"],
        ),
        handler=download_file,
        context_handler=bind_context_handler(download_file),
        permission_category="network",
        resource_scope_resolver=download_file_scope,
        resource_scope_resolver_id="builtin:os:download_file",
        concurrency_safe=False,
    )
