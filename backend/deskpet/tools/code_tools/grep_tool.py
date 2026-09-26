# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""grep — content search across project files via Python ``re``.

We deliberately don't shell out to ripgrep — keeps the frozen bundle
binary-free + cross-platform identical. For typical project sizes
(< 100k lines) Python regex over read_text is plenty fast (sub-second).

``path`` may name **either** a directory (walked) **or** a single file
(searched on its own). 事件 AH（HM-TO-A6 第 12 次尝试，turn 11）：模型对
``a6-fixture/qiufen-checklist-a.md`` 连打 4 次 ``grep``，每次都撞上这里
唯一的 ``if not root.is_dir()`` 守卫，拒因又被 ``_result`` 压成
``tool_failed`` / "Tool execution failed."，模型只好改用 1 KiB 分页硬读
40 KB 文件把预算打光。文件目标是模型最自然的用法，必须直接支持。

Three output modes (matches the Claude Code shape so prompts transfer):

  * ``files_with_matches`` — just paths (the default)
  * ``content`` — paths + line numbers + matching lines
  * ``count`` — paths + total match count per file

Filters:
  * ``glob`` — restrict to files matching this glob (e.g. ``"*.py"``);
    on a single-file target it filters that one file by name
  * ``case_insensitive`` — pass IGNORECASE to ``re.compile``
  * ``multiline`` — pass DOTALL + MULTILINE so ``.`` crosses newlines
  * ``context`` — N lines before/after each match (only for ``content``)

Caps: 100 files scanned, 250 result lines emitted. Truncation flag
returned in the JSON so the LLM can re-query with a tighter scope; the
body itself travels through the same large-result paging every settled
tool result uses (``primary_settled_effect_v1`` + ``context_page_in``
above ``DEFAULT_LARGE_RESULT_BYTES``), so the caps bound the *scan*, not
the model's ability to read what came back.

F-Z1 遗留 3：根内指向根外的目录符号链接会被 ``rglob`` 跟随，遍历结果因此
可能落在搜索根外。每条结果在这里按 ``within_root`` 复判一次，越根的结果
被丢弃并计入 ``escaped_results``。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..capabilities import ToolExecutionContext
from ..context_adapter import legacy_execution_context
from ._search_scope import (
    GLOB_INVALID,
    PATTERN_INVALID,
    PATTERN_REQUIRED,
    SEARCH_ROOT_MISSING,
    glob_name_matches,
    resolve_search_target,
    search_error,
    within_root,
)

log = logging.getLogger(__name__)

_MAX_FILES = 100
_MAX_RESULT_LINES = 250
# Files larger than this get skipped entirely — LLMs grepping
# a 50 MB compiled binary is never useful and would block the loop.
_MAX_FILE_BYTES = 5_000_000


def _iter_files(root: Path, file_glob: str | None) -> tuple[list[Path], int]:
    """``(candidate files under root, results dropped for leaving root)``."""
    if file_glob:
        candidates = list(root.rglob(file_glob))
    else:
        candidates = list(root.rglob("*"))
    files: list[Path] = []
    escaped = 0
    for p in candidates:
        if not p.is_file():
            continue
        # F-Z1 遗留 3：``rglob`` 跟随根内的目录符号链接，结果可能在根外。
        if not within_root(p, root):
            escaped += 1
            continue
        # 2026-09-26：读取已放开到任务根之外，遍历时跳过凭证类文件（未经授权）
        from deskpet.permissions.protected_paths import is_credential

        if is_credential(p):
            continue
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        files.append(p)
    # Sort by mtime descending so newer hits surface first.
    files.sort(
        key=lambda f: f.stat().st_mtime if f.exists() else 0,
        reverse=True,
    )
    return files, escaped


def _safe_read(p: Path) -> str | None:
    """Read text best-effort; return None for binary / unreadable files."""
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def grep_tool(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    pattern = args.get("pattern")
    if not pattern or not isinstance(pattern, str):
        return search_error("grep", PATTERN_REQUIRED)

    context = legacy_execution_context(args, task_id, execution_context)
    path = args.get("path") or context.workspace
    if not path:
        return search_error("grep", SEARCH_ROOT_MISSING)
    target, kind, failure = resolve_search_target(str(path))
    if failure is not None:
        code, error_type = failure
        return search_error("grep", code, error_type=error_type)
    assert target is not None  # resolve_search_target 恰有一侧被置上

    file_glob = args.get("glob")
    if file_glob is not None and not isinstance(file_glob, str):
        return search_error("grep", GLOB_INVALID)

    output_mode = args.get("output_mode", "files_with_matches")
    if output_mode not in {"files_with_matches", "content", "count"}:
        output_mode = "files_with_matches"

    flags = 0
    if args.get("case_insensitive"):
        flags |= re.IGNORECASE
    if args.get("multiline"):
        flags |= re.DOTALL | re.MULTILINE

    try:
        regex = re.compile(pattern, flags)
    except re.error:
        # ``type(re.error()).__name__`` 在 3.12 是裸的 ``error``；模型看到
        # "pattern_invalid: error" 毫无信息量，钉成限定名。
        log.warning("grep pattern rejected: re.error")
        return search_error("grep", PATTERN_INVALID, error_type="re.error")

    context_lines = max(0, int(args.get("context") or 0))

    if kind == "file":
        # 单文件目标：这个文件**就是**搜索集，根即它自身，越根无从谈起。
        root = target
        from deskpet.permissions.protected_paths import is_credential

        files = [target] if glob_name_matches(target.name, file_glob) and not is_credential(target) else []
        escaped = 0
        truncated_files = False
    else:
        root = target
        files, escaped = _iter_files(root, file_glob)
        truncated_files = len(files) > _MAX_FILES
        files = files[:_MAX_FILES]

    matches_by_file: dict[str, list[tuple[int, str]]] = {}
    counts: dict[str, int] = {}

    total_lines = 0
    truncated_lines = False
    for f in files:
        text = _safe_read(f)
        if text is None:
            continue
        # Multiline mode: search across the whole text, then map matches
        # back to line numbers. Otherwise: per-line search (fast path).
        if args.get("multiline"):
            file_matches: list[tuple[int, str]] = []
            for m in regex.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                # Snippet: first line of the match
                snippet = m.group(0).split("\n", 1)[0]
                file_matches.append((line_no, snippet))
            cnt = len(file_matches)
        else:
            file_matches = []
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    file_matches.append((line_no, line))
            cnt = len(file_matches)
        if cnt == 0:
            continue
        counts[str(f)] = cnt
        if output_mode == "content":
            # With context: capture surrounding lines per match.
            if context_lines > 0:
                lines = text.splitlines()
                expanded: list[tuple[int, str]] = []
                for line_no, _line in file_matches:
                    start = max(0, line_no - 1 - context_lines)
                    end = min(len(lines), line_no + context_lines)
                    for off in range(start, end):
                        expanded.append((off + 1, lines[off]))
                # Dedupe consecutive duplicate lines (overlapping ctx)
                seen = set()
                dedup: list[tuple[int, str]] = []
                for ln, ltxt in expanded:
                    if ln in seen:
                        continue
                    seen.add(ln)
                    dedup.append((ln, ltxt))
                file_matches = dedup
            matches_by_file[str(f)] = file_matches
            total_lines += len(file_matches)
            if total_lines >= _MAX_RESULT_LINES:
                truncated_lines = True
                break

    out: dict[str, Any] = {
        "pattern": pattern,
        "root": str(root),
        "target_kind": kind,
        "files_scanned": len(files),
        "files_with_matches": len(counts),
        "truncated_files": truncated_files,
        "truncated_lines": truncated_lines,
        "escaped_results": escaped,
    }
    if truncated_files or truncated_lines:
        out["next_action"] = (
            "Result truncated. Narrow with a tighter pattern, a glob filter, "
            "or a path naming one file, then call grep again."
        )

    if output_mode == "files_with_matches":
        out["files"] = sorted(counts.keys())
    elif output_mode == "count":
        out["counts"] = counts
    else:  # content
        out["matches"] = {
            f: [{"line": ln, "text": txt} for (ln, txt) in arr]
            for f, arr in matches_by_file.items()
        }

    return json.dumps(out, ensure_ascii=False)
