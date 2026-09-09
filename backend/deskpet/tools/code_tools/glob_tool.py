# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""glob — find files by glob pattern under the project root.

Pattern syntax follows ``pathlib.PurePath.match`` semantics, plus
``**`` for recursive descent (via ``Path.rglob``):

    "**/*.py"        → all .py files anywhere in the tree
    "src/*.ts"       → only direct children of src/
    "test_*.py"      → top-level test files (no recursion)

``path`` may name **either** a directory (walked) **or** a single file:
naming a file matches the pattern against that one file's name, so
``glob`` answers "does this file exist / match" instead of failing.
（事件 AH：文件路径此前只有 ``path is not a directory`` 一条守卫，且拒因
被 ``_result`` 压成不可行动的 ``tool_failed``。）

Output order is descending mtime — newest matches first — so the LLM
can find recently-touched files without paginating through every match.

Cap: 200 results. The LLM should narrow the pattern if it hits the cap
(returned in the metadata so it can tell). The body itself travels
through the same large-result paging every settled tool result uses.

F-Z1 遗留 3：``rglob`` 会跟随根内指向根外的目录符号链接，每条结果因此在
这里按 ``within_root`` 复判一次，越根结果丢弃并计入 ``escaped_results``。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..capabilities import ToolExecutionContext
from ..context_adapter import legacy_execution_context
from ._search_scope import (
    PATH_UNREADABLE,
    PATTERN_REQUIRED,
    SEARCH_ROOT_MISSING,
    glob_name_matches,
    resolve_search_target,
    search_error,
    within_root,
)

log = logging.getLogger(__name__)

_MAX_RESULTS = 200


def glob_tool(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    """Tool handler. ``args["pattern"]`` is required; ``args["path"]``
    optional — defaults to the injected project root.

    The chat handler injects ``args["_project_root"] = str(project_root)``
    before dispatch so this tool can run without the LLM having to
    re-state the root every call. Under the F-Z1 read gate the projected
    root is the *verified* bound root that contains the requested path.
    """
    pattern = args.get("pattern")
    if not pattern or not isinstance(pattern, str):
        return search_error("glob", PATTERN_REQUIRED)

    context = legacy_execution_context(args, task_id, execution_context)
    raw = args.get("path") or context.workspace
    if not raw:
        return search_error("glob", SEARCH_ROOT_MISSING)
    target, kind, failure = resolve_search_target(str(raw))
    if failure is not None:
        code, error_type = failure
        return search_error("glob", code, error_type=error_type)
    assert target is not None  # resolve_search_target 恰有一侧被置上

    escaped = 0
    if kind == "file":
        # 单文件目标：pattern 退化成对这一个文件名的匹配。
        matches: list[Path] = (
            [target] if glob_name_matches(target.name, pattern) else []
        )
    else:
        try:
            # ``rglob("**/X")`` and ``glob("**/X", recursive=True)`` differ —
            # rglob with a non-** pattern descends; rglob("**") includes the
            # root itself. We pass the user pattern straight through.
            found = list(target.rglob(pattern))
        except OSError as e:
            log.warning("glob failed: %s", type(e).__name__)
            return search_error("glob", PATH_UNREADABLE, error_type=type(e).__name__)
        matches = []
        for item in found:
            if within_root(item, target):
                matches.append(item)
            else:
                escaped += 1

    # Stable mtime sort (descending). Files that vanished between rglob
    # and stat fall back to mtime=0 so they sort last instead of crashing.
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    matches.sort(key=_mtime, reverse=True)

    truncated = len(matches) > _MAX_RESULTS
    keep = matches[:_MAX_RESULTS]

    out: dict[str, Any] = {
        "pattern": pattern,
        "root": str(target),
        "target_kind": kind,
        "count": len(keep),
        "total_match": len(matches),
        "truncated": truncated,
        "escaped_results": escaped,
        "files": [str(p) for p in keep],
    }
    if truncated:
        out["next_action"] = (
            "Result truncated. Narrow the pattern or pass a path deeper in "
            "the tree, then call glob again."
        )
    return json.dumps(out, ensure_ascii=False)
