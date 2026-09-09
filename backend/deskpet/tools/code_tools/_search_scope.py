# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""``glob`` / ``grep`` 共用的搜索目标解析、越根过滤与稳定拒绝码（事件 AH）。

事件 AH（HM-TO-A6 第 12 次尝试，turn 11）：模型对一个**文件**路径调
``grep``，两个 handler 都只有 ``if not root.is_dir(): return {"error": ...}``
这一条守卫 —— 目录才是唯一合法目标。返回的信封没有 ``error_code`` /
``public_message``，于是 ``sdk_adapters/tools.py::_result`` 把它压成
``tool_failed`` + "Tool execution failed."：payload 里的 "path is not a
directory: ..." **完全丢失**（``ToolResult.failed`` 不带 ``value``），
日志里也只剩 ``code=tool_failed``。模型连挂 4 次后改用 1 KiB 分页硬读 40 KB
文件，预算打光。

本模块把三件事收成一处，两个 handler 共用：

1. :func:`resolve_search_target` —— 目标可以是**文件**（搜这一个文件）或
   **目录**（遍历），其余情况给出不含路径的稳定码；
2. :func:`within_root` —— F-Z1 遗留 3：根内符号链接指向根外时，遍历结果
   仍可能带出根外文件。逐条结果按 ``resolve()`` 复判包含性，语义与
   ``sdk_adapters/read_gate.path_within_root`` 一致（这里重写而不是导入，
   是为了不让 ``deskpet.tools`` 反向依赖 ``deskpet.sdk_adapters``）；
3. :func:`search_error` —— 与 ``os_tools/edit_file._err`` /
   ``tools/file_tools._err`` 同一套约定：``error_code`` 是**不含路径**的稳定
   分类（进日志），``public_message`` 是有界、不含路径的可执行拒绝语（进模型）。
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

# --- 稳定拒绝码（不含路径、不含 payload，可直接进日志字段）----------------
SEARCH_ROOT_MISSING = "search_root_missing"
PATH_NOT_FOUND = "path_not_found"
PATH_UNREADABLE = "path_unreadable"
NOT_A_SEARCHABLE_TARGET = "not_a_searchable_target"
PATTERN_REQUIRED = "pattern_required"
PATTERN_INVALID = "pattern_invalid"
GLOB_INVALID = "glob_invalid"

# ``public_message`` 的上界与 ``sdk_adapters/tools.py`` 的
# ``_MAX_HANDLER_PUBLIC_MESSAGE`` 同阶，但这里的文案本来就是常量级。
MAX_PUBLIC_MESSAGE = 512

_HINTS: dict[str, str] = {
    SEARCH_ROOT_MISSING: (
        "no path argument and no bound workspace root to search in. Route this "
        "Run with context_route first, or pass an absolute path inside the "
        "bound workspace root."
    ),
    PATH_NOT_FOUND: (
        "the path argument names nothing that exists. Confirm it with "
        "list_directory or glob, then call this tool again with a path that "
        "does exist."
    ),
    PATH_UNREADABLE: (
        "the path argument could not be opened (permission or filesystem "
        "error). Pick a different path inside the bound workspace root."
    ),
    NOT_A_SEARCHABLE_TARGET: (
        "the path argument is neither a regular file nor a directory (socket, "
        "device or broken link). Pass a file to search that one file, or a "
        "directory to walk it."
    ),
    PATTERN_REQUIRED: (
        "the pattern argument is required and must be a non-empty string. "
        "Call this tool again with pattern filled in."
    ),
    PATTERN_INVALID: (
        "the pattern argument is not a valid Python regular expression. "
        "Escape the special characters or simplify it, then call this tool "
        "again."
    ),
    GLOB_INVALID: (
        "the glob argument must be a string such as *.py. Drop it or pass a "
        "string, then call this tool again."
    ),
}


def search_error(
    tool: str,
    code: str,
    *,
    error_type: str | None = None,
    retriable: bool = True,
) -> str:
    """一条**不含路径**的稳定拒绝信封。

    ``error``（自然语言，可含路径）只回给模型的 payload —— 但 FAILED 的
    ``ToolResult`` 并不携带 payload，所以真正到达模型的是 ``public_message``：
    它必须自己说清「为什么 + 下一步」，且不含路径。``error_type`` 是异常类名
    （``OSError`` / ``re.error`` …），事件 AH 里正是它与拒因一起丢失了。
    """

    hint = _HINTS.get(code, "the call was rejected.")
    label = f"{code}: {error_type}" if error_type else code
    message = f"{tool} rejected ({label}): {hint}"[:MAX_PUBLIC_MESSAGE]
    body: dict[str, Any] = {
        "error": message,
        "error_code": code,
        "public_message": message,
        "retriable": retriable,
    }
    if error_type:
        body["error_type"] = error_type
    return json.dumps(body, ensure_ascii=False)


def resolve_search_target(raw: str) -> tuple[Path | None, str, tuple[str, str | None] | None]:
    """``(resolved, kind, failure)`` —— ``kind`` 取 ``"file"`` / ``"dir"``。

    恰有一侧被置上。``failure`` 是 ``(稳定码, 异常类名 | None)``。
    解析跟随符号链接（与读闸门 ``path_within_root`` 同一判据），所以指向根外
    的链接在这里就已经变成它真正的目标，随后由 :func:`within_root` 复判。
    """

    try:
        target = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        return None, "", (PATH_UNREADABLE, type(exc).__name__)
    try:
        if target.is_dir():
            return target, "dir", None
        if target.is_file():
            return target, "file", None
        if not target.exists():
            return None, "", (PATH_NOT_FOUND, None)
    except OSError as exc:
        return None, "", (PATH_UNREADABLE, type(exc).__name__)
    return None, "", (NOT_A_SEARCHABLE_TARGET, None)


def within_root(candidate: Path, root: Path) -> bool:
    """符号链接解析后的包含性判据；不可解析 → 不在内（fail closed）。

    与 ``sdk_adapters/read_gate.path_within_root`` 同语义，供逐条结果复判：
    ``Path.rglob`` 会跟随根内指向根外的目录链接，遍历结果因此可能落在根外
    （F-Z1 遗留 3）。
    """

    try:
        resolved = candidate.resolve()
        resolved_root = root.resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    if resolved == resolved_root:
        return True
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return False
    return True


def glob_name_matches(name: str, file_glob: str | None) -> bool:
    """目录遍历的 ``glob`` 过滤在单文件目标上等价于按文件名匹配。"""

    if not file_glob:
        return True
    return fnmatch(name, file_glob) or fnmatch(name, Path(file_glob).name)


__all__ = [
    "GLOB_INVALID",
    "MAX_PUBLIC_MESSAGE",
    "NOT_A_SEARCHABLE_TARGET",
    "PATH_NOT_FOUND",
    "PATH_UNREADABLE",
    "PATTERN_INVALID",
    "PATTERN_REQUIRED",
    "SEARCH_ROOT_MISSING",
    "glob_name_matches",
    "resolve_search_target",
    "search_error",
    "within_root",
]
