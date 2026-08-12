# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Structured OS-level tools.

Handlers use the existing ToolRegistry JSON-string contract. Process,
application, and download handlers are async; filesystem primitives remain
sync where no waiting is needed.

Permission gating happens *outside* the handler (in
``ToolRegistry.execute_tool``); the handlers themselves assume they are
allowed to run. They still validate inputs and return error envelopes
for filesystem / network / process failures.

Spec: openspec/changes/deskpet-skill-platform/specs/os-tools/spec.md
"""

from .app_tools import app_discover, app_launch, resolve_app_launch_resources
from .desktop_create_file import desktop_create_file
from .download_tools import download_file, resolve_download_resources
from .edit_file import edit_file
from .list_directory import list_directory
from .move_file import move_file, resolve_move_file_resources
from .register_artifacts import register_artifacts
from .process_tools import (
    process_list,
    process_start,
    process_stop,
    process_wait,
    resolve_process_start_resources,
    resolve_process_stop_resources,
)
from .read_file import read_file
from .registration import register_os_tools
from .run_shell import run_shell
from .web_fetch import web_fetch
from .write_file import write_file

__all__ = [
    "app_discover",
    "app_launch",
    "desktop_create_file",
    "download_file",
    "edit_file",
    "list_directory",
    "move_file",
    "register_artifacts",
    "process_list",
    "process_start",
    "process_stop",
    "process_wait",
    "resolve_app_launch_resources",
    "resolve_download_resources",
    "resolve_move_file_resources",
    "resolve_process_start_resources",
    "resolve_process_stop_resources",
    "read_file",
    "register_os_tools",
    "run_shell",
    "web_fetch",
    "write_file",
]
