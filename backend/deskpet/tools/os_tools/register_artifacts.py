# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Register existing workspace files as first-class Harness artifacts.

The tool is deliberately read-only: it never copies, moves, rewrites, or
creates files.  It validates every requested path against the trusted Run
workspace and returns the standard ``artifacts`` envelope consumed by the
registry receipt path and the frontend ArtifactCard projection.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..capabilities import ToolExecutionContext
from ..context_adapter import legacy_execution_context

_MAX_ARTIFACTS = 50


def _error(code: str, hint: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": False, "error": code, "hint": hint}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_root(context: ToolExecutionContext) -> Path | None:
    raw = context.workspace or context.write_scope_root
    if not raw:
        return None
    try:
        root = Path(raw).expanduser().resolve(strict=True)
    except OSError:
        return None
    return root if root.is_dir() else None


def register_artifacts(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    """Return a standard artifact envelope for existing workspace files."""

    paths = args.get("paths")
    if not isinstance(paths, list) or not paths:
        return _error(
            "paths required",
            "register_artifacts requires a non-empty paths array of existing files.",
        )
    if len(paths) > _MAX_ARTIFACTS:
        return _error(
            "too many artifacts",
            f"At most {_MAX_ARTIFACTS} files can be registered per call.",
            count=len(paths),
            limit=_MAX_ARTIFACTS,
        )
    if any(not isinstance(item, str) or not item.strip() for item in paths):
        return _error(
            "invalid path",
            "Every paths item must be a non-empty string.",
        )

    context = legacy_execution_context(args, task_id, execution_context)
    root = _workspace_root(context)
    if root is None:
        return _error(
            "workspace required",
            "Select a project directory before registering existing artifacts.",
        )

    artifacts: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for raw in paths:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            return _error(
                "artifact outside workspace",
                "Every artifact must be an existing file inside the selected project directory.",
                path=raw,
                workspace=str(root),
            )
        if not resolved.is_file():
            return _error(
                "artifact is not a file",
                "Directories cannot be registered as file artifacts.",
                path=str(resolved),
            )
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            size = resolved.stat().st_size
            sha256 = _sha256(resolved)
        except OSError as exc:
            return _error(
                f"OSError: {exc}",
                "The artifact changed or became unreadable while it was being registered.",
                path=str(resolved),
            )
        artifacts.append(
            {
                "kind": "file",
                "path": str(resolved),
                "title": resolved.name,
                "size_bytes": size,
                "sha256": sha256,
            }
        )

    return json.dumps(
        {
            "ok": True,
            "result": f"Registered {len(artifacts)} existing workspace artifact(s).",
            "artifacts": artifacts,
            "workspace": str(root),
            "content_modified": False,
        },
        ensure_ascii=False,
    )


__all__ = ["register_artifacts"]
