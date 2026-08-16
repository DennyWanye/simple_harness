# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S5: file tools (toolset=file).

Sandboxed file CRUD + glob/grep, restricted to
``%APPDATA%/deskpet/workspace/``. Every path the LLM supplies is
resolved via :func:`_resolve_within_workspace`, which:

  1. interprets relative paths against the workspace root;
  2. refuses absolute paths outside the workspace;
  3. refuses ``..`` traversal that escapes after normalization;
  4. refuses UNC / drive-letter paths on Windows.

Rationale: the agent can hallucinate ``C:/Windows/system.ini`` or
``../../etc/passwd`` — either the model's own invention or a prompt
injection from web content — and the tool layer is the last line of
defence before real disk access.

Workspace path resolution tries, in order:

  1. ``DESKPET_WORKSPACE_DIR`` env (tests, CI).
  2. ``user_data_dir() / "workspace"`` — production: ``%APPDATA%\\deskpet\\workspace\\``.

The directory is lazily created on first access so tests using tmp
paths don't need to pre-mkdir.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import platformdirs

from .capabilities import ToolExecutionContext
from .context_adapter import bind_context_handler, legacy_execution_context

logger = logging.getLogger(__name__)

_APP_NAME = "deskpet"

_DEFAULT_GLOB_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}

_DEFAULT_GLOB_SKIP_REL_PARTS = {
    "backend/assets",
    "backend/dist-msi",
    "backend/dist-portable",
    "backend/models",
    "tauri-app/coverage",
    "tauri-app/node_modules",
}


# ---------------------------------------------------------------------
# 记忆系统升级 WI-M1.6 — 工作记忆注入点
# ---------------------------------------------------------------------
# file_write / file_read handler 改成 async 后，可直接 await
# WorkspaceMemoryStore.record_action（registry dispatch 用
# iscoroutinefunction 分流，async handler 直接 await，不进 worker thread
# → 不必 run_coroutine_threadsafe，无死锁）。
#
# 模块级函数没有依赖注入途径 —— main.py 在 workspace_memory flag 开时
# 调 set_workspace_store() 把实例塞进来。flag 关 → 保持 None → handler
# 跳过记录（Strangler-Fig：flag 关时 workspace_state 表不会被建）。
_workspace_store: Any | None = None
# Stage 2 round 2 fix：sync tool handler (os_tools/*) 跑在 executor，
# 没 running loop → 无法直接 schedule async record_action。设置时记
# 录 main loop reference，os_tools 用 run_coroutine_threadsafe 派回 main。
_workspace_loop: Any | None = None


def set_workspace_store(store: Any | None) -> None:
    """main.py 在 workspace_memory flag 开时注入 WorkspaceMemoryStore。

    同时记录当前 event loop，供 sync tool handler (os_tools.read_file /
    write_file) 通过 run_coroutine_threadsafe 派回主 loop 跑 record_action。

    Stage 2 round 2 fix：main.py 在 module top-level 调本函数（非 async
    上下文），get_running_loop 会 raise RuntimeError → _workspace_loop=None。
    lifespan startup 应再调一次 :func:`rebind_loop` 把当前 loop 绑上。
    """
    global _workspace_store, _workspace_loop
    _workspace_store = store
    try:
        import asyncio as _asyncio
        _workspace_loop = _asyncio.get_running_loop()
    except RuntimeError:
        _workspace_loop = None


def rebind_loop() -> bool:
    """Stage 2 round 2 fix：在 lifespan startup 调一次绑当前 async loop。

    sync tool handler (os_tools/read_file 等) 依赖 _workspace_loop 来
    通过 run_coroutine_threadsafe 派 record_action 回主 loop；
    set_workspace_store 在 module top-level 调时拿不到 loop，必须在
    lifespan async context 里补绑。Returns True if loop bound, else False.
    """
    global _workspace_loop
    try:
        import asyncio as _asyncio
        _workspace_loop = _asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


async def _record_workspace_action(
    *, session_id: str, path: str, action: str, content: str | None,
) -> None:
    """best-effort 记一次文件动作。store 未注入 / 出错 → 静默跳过，
    绝不影响 file 工具主路径。"""
    store = _workspace_store
    if store is None:
        return
    try:
        await store.record_action(
            session_id=session_id or "default",
            path=path,
            action=action,
            content=content,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("workspace record_action failed: %s", exc)


def _workspace_root(override: str | Path | None = None) -> Path:
    override = override or os.environ.get("DESKPET_WORKSPACE_DIR")
    if override:
        root = Path(override).resolve()
    else:
        # Match backend/paths.user_data_dir() without importing the
        # backend flat layout (tools module must be import-safe outside
        # the full backend).
        base = Path(
            platformdirs.user_data_dir(_APP_NAME, appauthor=False, roaming=True)
        )
        root = (base / "workspace").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _context_workspace_root(context: ToolExecutionContext) -> Path:
    override = context.workspace or context.write_scope_root
    return _workspace_root(str(override) if override else None)


def _resolve_within_workspace(
    path_str: str, workspace: str | Path | None = None
) -> Path | None:
    """Resolve ``path_str`` against the workspace. Return a Path inside
    the workspace on success, or None if the path escapes / is malformed.

    Keep the resolution conservative: we use ``Path.resolve()`` followed
    by ``relative_to(root)`` — if it raises ``ValueError``, the resolved
    absolute path is NOT a descendant of root, so we reject. This
    handles ``..``, symlinks, and drive-letter escapes uniformly.
    """
    if not isinstance(path_str, str) or not path_str:
        return None

    # Refuse explicit absolute paths up front — even if they happen to
    # point inside the workspace, the agent should never be typing raw
    # absolute disk paths. Catches ``C:\\Windows\\...``, ``/etc/passwd``,
    # UNC ``\\\\server\\share`` etc.
    root = _workspace_root(workspace)
    # ``workspace_prepare`` intentionally returns an absolute trusted root.
    # Accept a model joining that root with a filename, but keep the resolved
    # descendant check below as the authority boundary.  Absolute paths,
    # traversal and symlinks that escape the trusted root remain rejected.
    p = Path(path_str)
    if path_str.startswith(("\\\\", "//")):
        return None
    candidate = p.resolve() if p.is_absolute() else (root / p).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _err(msg: str, retriable: bool = False) -> str:
    return json.dumps({"error": msg, "retriable": retriable}, ensure_ascii=False)


def _workspace_rel(path: Path, workspace: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return None


def _should_skip_glob_dir(path: Path, workspace: Path) -> bool:
    rel = _workspace_rel(path, workspace)
    if rel is None:
        return True
    rel_norm = rel.strip("/")
    if path.name in _DEFAULT_GLOB_SKIP_DIR_NAMES:
        return True
    return any(
        rel_norm == skip or rel_norm.startswith(f"{skip}/")
        for skip in _DEFAULT_GLOB_SKIP_REL_PARTS
    )


def _is_under_skipped_glob_dir(path: Path, workspace: Path) -> bool:
    rel = _workspace_rel(path, workspace)
    if rel is None:
        return True
    parts = Path(rel).parts
    if any(part in _DEFAULT_GLOB_SKIP_DIR_NAMES for part in parts):
        return True
    rel_norm = rel.strip("/")
    return any(
        rel_norm == skip or rel_norm.startswith(f"{skip}/")
        for skip in _DEFAULT_GLOB_SKIP_REL_PARTS
    )


def _iter_glob_matches(
    root: Path, pattern: str, workspace: Path,
) -> tuple[list[Path], list[str]]:
    """Iterate glob matches while pruning heavyweight generated dirs."""
    skipped: set[str] = set()
    matches: list[Path] = []
    prune_skipped_dirs = not _should_skip_glob_dir(root, workspace)

    if "**" not in pattern:
        for path in root.glob(pattern):
            if prune_skipped_dirs and _is_under_skipped_glob_dir(path, workspace):
                parent = path if path.is_dir() else path.parent
                rel = _workspace_rel(parent, workspace)
                if rel:
                    skipped.add(rel)
                continue
            matches.append(path)
        return matches, sorted(skipped)

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        kept_dirnames: list[str] = []
        for dirname in dirnames:
            child = current / dirname
            if prune_skipped_dirs and _should_skip_glob_dir(child, workspace):
                rel = _workspace_rel(child, workspace)
                if rel:
                    skipped.add(rel)
                continue
            kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames

        candidates: Iterator[Path] = (
            current / name for name in [*kept_dirnames, *filenames]
        )
        for candidate in candidates:
            rel = _workspace_rel(candidate, root)
            if rel is not None and candidate.match(pattern):
                matches.append(candidate)
    return matches, sorted(skipped)


# ---------------------------------------------------------------------
# file_read
# ---------------------------------------------------------------------
_SCHEMA_READ: dict[str, Any] = {
    "name": "file_read",
    "description": (
        "Read a UTF-8 text file from the Simple Harness workspace. Supports "
        "line offset + limit for large files. Returns content + lines_read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative path (e.g. 'notes/todo.md').",
            },
            "offset": {
                "type": "integer",
                "description": "0-based starting line. Default 0.",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to return. Default 2000.",
                "default": 2000,
            },
        },
        "required": ["path"],
    },
}


async def _handle_file_read(
    args: dict[str, Any],
    task_id: str,
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    # WI-M1.6: handler 改 async —— 成功读后直接 await record_action 记
    # 工作记忆（registry dispatch 对 async handler 直接 await）。
    context = legacy_execution_context(args, task_id, execution_context)
    workspace = _context_workspace_root(context)
    target = _resolve_within_workspace(str(args.get("path", "")), workspace)
    if target is None:
        return _err("path outside workspace", retriable=False)
    offset = int(args.get("offset", 0) or 0)
    limit = int(args.get("limit", 2000) or 2000)
    if offset < 0 or limit < 0:
        return _err("offset and limit must be non-negative", retriable=False)
    if not target.exists():
        return _err(f"file not found: {args.get('path')}", retriable=False)
    if not target.is_file():
        return _err(f"not a regular file: {args.get('path')}", retriable=False)
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            lines: list[str] = []
            for i, line in enumerate(f):
                if i < offset:
                    continue
                if len(lines) >= limit:
                    break
                lines.append(line)
    except OSError as exc:
        return _err(f"read failed: {exc}", retriable=True)
    rel_path = str(target.relative_to(workspace)).replace("\\", "/")
    content = "".join(lines)
    await _record_workspace_action(
        session_id=context.session_id, path=rel_path, action="read", content=content,
    )
    return json.dumps(
        {
            "content": content,
            "lines_read": len(lines),
            "path": rel_path,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------
# file_write
# ---------------------------------------------------------------------
_SCHEMA_WRITE: dict[str, Any] = {
    "name": "file_write",
    "description": (
        "Write UTF-8 text to a workspace file. 'overwrite' replaces the "
        "file; 'append' adds to the end. Parent directories are created "
        "automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Workspace-relative path.",
            },
            "content": {
                "type": "string",
                "description": "UTF-8 content to write.",
            },
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "default": "overwrite",
            },
        },
        "required": ["path", "content"],
    },
}


async def _handle_file_write(
    args: dict[str, Any],
    task_id: str,
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    # WI-M1.6: handler 改 async —— 成功写后 await record_action 记工作记忆。
    context = legacy_execution_context(args, task_id, execution_context)
    workspace = _context_workspace_root(context)
    target = _resolve_within_workspace(str(args.get("path", "")), workspace)
    if target is None:
        return _err("path outside workspace", retriable=False)
    content = args.get("content", "")
    if not isinstance(content, str):
        return _err("content must be a string", retriable=False)
    mode = str(args.get("mode", "overwrite") or "overwrite")
    if mode not in {"overwrite", "append"}:
        return _err(f"invalid mode: {mode}", retriable=False)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        flag = "a" if mode == "append" else "w"
        with target.open(flag, encoding="utf-8") as f:
            written = f.write(content)
    except OSError as exc:
        return _err(f"write failed: {exc}", retriable=True)
    rel_path = str(target.relative_to(workspace)).replace("\\", "/")
    await _record_workspace_action(
        session_id=context.session_id, path=rel_path, action="write", content=content,
    )
    return json.dumps(
        {
            "bytes_written": written,
            "path": rel_path,
            "mode": mode,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------
# file_glob
# ---------------------------------------------------------------------
_SCHEMA_GLOB: dict[str, Any] = {
    "name": "file_glob",
    "description": (
        "List workspace files matching a glob pattern. Uses pathlib glob "
        "semantics (e.g. '**/*.md' for recursive markdown), while recursive "
        "workspace scans skip heavyweight generated directories by default. "
        "Returns workspace-relative paths."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '*.txt' or 'notes/**/*.md'.",
            },
            "root": {
                "type": "string",
                "description": "Sub-root to glob under. Default '.'.",
                "default": ".",
            },
        },
        "required": ["pattern"],
    },
}


def _handle_file_glob(
    args: dict[str, Any],
    task_id: str,
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    pattern = str(args.get("pattern", "") or "")
    if not pattern:
        return _err("pattern is required", retriable=False)
    root_rel = str(args.get("root", ".") or ".")
    context = legacy_execution_context(args, task_id, execution_context)
    workspace = _context_workspace_root(context)
    root = _resolve_within_workspace(root_rel, workspace)
    if root is None:
        return _err("path outside workspace", retriable=False)
    if not root.exists():
        return json.dumps({"matches": [], "count": 0})
    matches: list[str] = []
    skipped_dirs: list[str] = []
    try:
        # Use glob for patterns with ** — rglob is "**/<pattern>" which
        # mangles user intent. Path.glob("**/*.md") is what we want.
        found, skipped_dirs = _iter_glob_matches(root, pattern, workspace)
        for p in found:
            try:
                rel = p.resolve().relative_to(workspace)
            except ValueError:
                # Defensive: glob shouldn't escape root, but skip if it does.
                continue
            matches.append(str(rel).replace("\\", "/"))
    except OSError as exc:
        return _err(f"glob failed: {exc}", retriable=True)
    matches.sort()
    return json.dumps(
        {
            "matches": matches,
            "count": len(matches),
            "skipped_dirs": skipped_dirs,
            "skipped_count": len(skipped_dirs),
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------
# file_grep
# ---------------------------------------------------------------------
_SCHEMA_GREP: dict[str, Any] = {
    "name": "file_grep",
    "description": (
        "Search a workspace file line-by-line for a regex pattern. Returns "
        "matching line numbers + text. Useful for quick lookups before "
        "reading larger files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Python regex pattern.",
            },
            "path": {
                "type": "string",
                "description": "Workspace-relative file path.",
            },
            "max_matches": {
                "type": "integer",
                "description": "Stop after N matches. Default 50.",
                "default": 50,
            },
        },
        "required": ["pattern", "path"],
    },
}


def _handle_file_grep(
    args: dict[str, Any],
    task_id: str,
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    pattern = str(args.get("pattern", "") or "")
    if not pattern:
        return _err("pattern is required", retriable=False)
    context = legacy_execution_context(args, task_id, execution_context)
    workspace = _context_workspace_root(context)
    target = _resolve_within_workspace(str(args.get("path", "")), workspace)
    if target is None:
        return _err("path outside workspace", retriable=False)
    max_matches = int(args.get("max_matches", 50) or 50)
    if max_matches <= 0:
        return _err("max_matches must be positive", retriable=False)
    if not target.exists() or not target.is_file():
        return _err(f"file not found: {args.get('path')}", retriable=False)
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return _err(f"invalid regex: {exc}", retriable=False)
    out: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if rx.search(line):
                    out.append({"line": i, "text": line.rstrip("\n")})
                    if len(out) >= max_matches:
                        break
    except OSError as exc:
        return _err(f"grep failed: {exc}", retriable=True)
    return json.dumps(
        {"matches": out, "count": len(out)}, ensure_ascii=False
    )


# ---------------------------------------------------------------------
# workspace_recall (记忆系统升级 WI-M1.6)
# ---------------------------------------------------------------------
_SCHEMA_WORKSPACE_RECALL: dict[str, Any] = {
    "name": "workspace_recall",
    "description": (
        "Recall files you've already read or written earlier in THIS "
        "task — search by path or content keyword. Use this before "
        "re-reading a file to check if you already know it. Returns "
        "path / last_action / content_summary for each match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Path fragment or content keyword to search.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results. Default 10.",
                "default": 10,
            },
        },
        "required": ["query"],
    },
}


async def _handle_workspace_recall(
    args: dict[str, Any],
    task_id: str,
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    """WI-M1.6: 查回本 task 改过/读过的文件。store 未注入（flag 关）→
    返回 reason=workspace_memory_disabled。"""
    store = _workspace_store
    if store is None:
        return json.dumps(
            {"matches": [], "count": 0, "reason": "workspace_memory_disabled"},
            ensure_ascii=False,
        )
    query = str(args.get("query", "") or "")
    if not query.strip():
        return _err("query is required", retriable=False)
    limit = int(args.get("limit", 10) or 10)
    context = legacy_execution_context(args, task_id, execution_context)
    try:
        rows = await store.recall(
            query, session_id=context.session_id, limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"workspace_recall failed: {exc}", retriable=True)
    matches = [
        {
            "path": r.get("path"),
            "last_action": r.get("last_action"),
            "content_summary": r.get("content_summary"),
            "byte_size": r.get("byte_size"),
        }
        for r in rows
    ]
    return json.dumps(
        {"matches": matches, "count": len(matches)}, ensure_ascii=False,
    )


# ---------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------
def register_static_tools(registry) -> None:
    registry.register(
        "file_read", "file", _SCHEMA_READ, _handle_file_read,
        context_handler=bind_context_handler(_handle_file_read),
    )
    registry.register(
        "file_write", "file", _SCHEMA_WRITE, _handle_file_write,
        context_handler=bind_context_handler(_handle_file_write),
        concurrency_safe=False,
    )
    registry.register(
        "file_glob", "file", _SCHEMA_GLOB, _handle_file_glob,
        context_handler=bind_context_handler(_handle_file_glob),
    )
    registry.register(
        "file_grep", "file", _SCHEMA_GREP, _handle_file_grep,
        context_handler=bind_context_handler(_handle_file_grep),
    )
    registry.register(
        "workspace_recall", "file", _SCHEMA_WORKSPACE_RECALL,
        _handle_workspace_recall,
        context_handler=bind_context_handler(_handle_workspace_recall),
    )
