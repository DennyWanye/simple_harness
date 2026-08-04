# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Deterministic resource selectors for prepared host tool calls.

The model supplies tool arguments; the host resolves those arguments into
canonical resources before an authorization grant can be issued.  Resolvers
must stay pure with respect to user intent: they may inspect the local host to
resolve an executable or desktop path, but they never broaden a selector from
the model-provided target to an unrelated root.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from deskpet.types.task_grants import ResourceSelector

from .capabilities import ToolExecutionContext


def _required_text(args: Mapping[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    if "\x00" in value:
        raise ValueError(f"{key} contains NUL")
    return value


def _filesystem(value: str, *access: str, strict: bool = False) -> ResourceSelector:
    return ResourceSelector.filesystem(
        Path(value).expanduser().resolve(strict=strict),
        *access,
    )


def _network_origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme.casefold() not in {"http", "https", "ws", "wss"}:
        raise ValueError("url must use http(s) or ws(s)")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("url must contain a host and no credentials")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default_port = {
        "http": 80,
        "https": 443,
        "ws": 80,
        "wss": 443,
    }[parsed.scheme.casefold()]
    suffix = "" if port in {None, default_port} else f":{port}"
    return f"{parsed.scheme.casefold()}://{host}{suffix}"


def read_file_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    del context
    return (_filesystem(_required_text(args, "path"), "read"),)


def write_file_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    del context
    return (_filesystem(_required_text(args, "path"), "write"),)


def edit_file_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    return write_file_scope(args, context)


def list_directory_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    del context
    return (_filesystem(_required_text(args, "path"), "list", "read"),)


def move_file_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    del context
    return (
        _filesystem(_required_text(args, "source"), "move_source", "read"),
        _filesystem(
            _required_text(args, "destination"),
            "move_destination",
            "write",
        ),
    )


def download_file_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    del context
    url = _required_text(args, "url")
    parsed = urlsplit(url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("url must be an absolute http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("url credentials are not accepted")
    # Accessing .port validates malformed numeric ports.
    parsed.port
    return (
        ResourceSelector.network(_network_origin(url), "connect", "read"),
        _filesystem(_required_text(args, "destination"), "write"),
    )


def run_shell_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    _required_text(args, "command")
    from .os_tools.run_shell import _pick_shell, resolve_run_shell_cwd

    shell, _leading_args = _pick_shell()
    cwd_value = resolve_run_shell_cwd(dict(args), context, required=True)
    assert cwd_value is not None
    return (
        ResourceSelector(
            "process_executable",
            str(Path(shell).expanduser().resolve(strict=False)),
            ("execute",),
        ),
        _filesystem(cwd_value, "working_directory"),
        ResourceSelector("system_change", "opaque_shell", ("execute",)),
    )


def process_start_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    from .os_tools.process_tools import _resolve_executable, _validate_argv

    executable = _resolve_executable(args.get("executable"))
    _validate_argv(args.get("argv", []))
    selectors: list[ResourceSelector] = [
        ResourceSelector("process_executable", executable, ("execute",))
    ]
    cwd_value = args.get("cwd") or context.workspace
    if cwd_value is not None:
        if not isinstance(cwd_value, str) or not cwd_value.strip():
            raise ValueError("cwd must be a non-empty string")
        selectors.append(_filesystem(cwd_value, "working_directory"))
    return tuple(selectors)


def _opaque_process_identity(args: Mapping[str, Any]) -> str:
    payload = {
        "pid": args.get("pid"),
        "creation_time": args.get("creation_time"),
        "command_line": args.get("command_line"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "process_identity:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def process_stop_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    lease_id = args.get("lease_id")
    if isinstance(lease_id, str) and lease_id:
        if not context.root_run_id:
            raise ValueError("process lease authorization requires root_run_id")
        return (
            ResourceSelector(
                "system_change",
                f"process_lease:{context.root_run_id}:{lease_id}",
                ("stop",),
            ),
        )
    command_line = args.get("command_line")
    if (
        not isinstance(command_line, list)
        or not command_line
        or not all(isinstance(item, str) and "\x00" not in item for item in command_line)
    ):
        raise ValueError("explicit process stop requires a non-empty command_line")
    return (
        ResourceSelector(
            "process_executable",
            command_line[0],
            ("stop",),
        ),
        ResourceSelector(
            "system_change",
            _opaque_process_identity(args),
            ("stop",),
        ),
    )


def app_launch_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    from .os_tools.app_tools import discover_app

    query = args.get("app") or args.get("executable")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("app or executable is required")
    candidate = next(
        (item for item in discover_app(query) if item["launchable_probe"]),
        None,
    )
    if candidate is None:
        raise ValueError("no launchable application candidate found")
    selectors: list[ResourceSelector] = [
        ResourceSelector("application", query, ("launch",)),
        ResourceSelector(
            "process_executable",
            str(candidate["executable"]),
            ("execute",),
        ),
    ]
    cwd_value = args.get("cwd") or context.workspace
    if cwd_value is not None:
        if not isinstance(cwd_value, str) or not cwd_value.strip():
            raise ValueError("cwd must be a non-empty string")
        selectors.append(_filesystem(cwd_value, "working_directory"))
    return tuple(selectors)


def desktop_create_file_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    del context
    from .os_tools.desktop_create_file import _resolve_desktop

    name = _required_text(args, "name")
    if any(separator in name for separator in ("/", "\\")) or ".." in name:
        raise ValueError("name must be one desktop file component")
    desktop, _platform = _resolve_desktop()
    return (
        ResourceSelector("desktop_target", "user_desktop", ("write",)),
        _filesystem(str(desktop / name), "write"),
    )


def web_fetch_scope(
    args: Mapping[str, Any], context: ToolExecutionContext
) -> tuple[ResourceSelector, ...]:
    del context
    url = _required_text(args, "url")
    return (ResourceSelector.network(_network_origin(url), "connect", "read"),)


__all__ = [
    "app_launch_scope",
    "desktop_create_file_scope",
    "download_file_scope",
    "edit_file_scope",
    "list_directory_scope",
    "move_file_scope",
    "process_start_scope",
    "process_stop_scope",
    "read_file_scope",
    "run_shell_scope",
    "web_fetch_scope",
    "write_file_scope",
]
