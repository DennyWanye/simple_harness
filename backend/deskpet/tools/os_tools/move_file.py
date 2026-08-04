# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Hash-guarded same-volume file move primitive."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from ..capabilities import ToolExecutionContext
from ..context_adapter import legacy_execution_context

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(path: Path) -> str:
    raw = str(path.resolve(strict=False))
    return os.path.normcase(raw) if os.name == "nt" else raw


def _within(path: Path, root: Path) -> bool:
    candidate = _identity(path)
    root_value = _identity(root).rstrip("\\/")
    if candidate == root_value:
        return True
    separator = "\\" if os.name == "nt" else "/"
    return candidate.startswith(root_value + separator)


def _rename_noreplace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(source, destination)
        return
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            result = renameat2(
                -100, os.fsencode(source), -100, os.fsencode(destination), 1
            )
            if result == 0:
                return
            error_number = ctypes.get_errno()
            if error_number != errno.ENOSYS:
                raise OSError(error_number, os.strerror(error_number), str(destination))
    # Hard-link publish + source unlink preserves no-overwrite atomically on a
    # single filesystem. Directories and symlinks are rejected by the caller.
    os.link(source, destination)
    os.unlink(source)


def move_file(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    source_raw = args.get("source")
    destination_raw = args.get("destination")
    expected_hash = args.get("expected_source_hash")
    overwrite = args.get("overwrite", False)
    if not isinstance(source_raw, str) or not isinstance(destination_raw, str):
        return _error("invalid_arguments", "source and destination must be strings")
    if not isinstance(expected_hash, str) or _SHA256.fullmatch(expected_hash) is None:
        return _error(
            "invalid_arguments", "expected_source_hash must be 64 hex characters"
        )
    if not isinstance(overwrite, bool):
        return _error("invalid_arguments", "overwrite must be a boolean")

    source_input = Path(source_raw).expanduser()
    if source_input.is_symlink():
        return _error("source_symlink_rejected", "source symlinks are not supported")
    try:
        source = source_input.resolve(strict=True)
    except OSError as exc:
        return _error("source_missing", str(exc))
    destination_input = Path(destination_raw).expanduser()
    if destination_input.is_symlink():
        return _error(
            "destination_symlink_rejected", "destination symlinks are not supported"
        )
    destination = destination_input.resolve(strict=False)
    if not source.is_file():
        return _error("source_not_file", "source must be a regular file")
    if not destination.parent.is_dir():
        return _error("destination_parent_missing", "destination parent does not exist")

    context = legacy_execution_context(args, task_id, execution_context)
    scope = context.write_scope_root or context.workspace
    if scope:
        try:
            scope_root = Path(scope).expanduser().resolve(strict=True)
        except OSError as exc:
            return _error("invalid_write_scope", str(exc))
        if not _within(source, scope_root) or not _within(destination, scope_root):
            return _error(
                "path_outside_write_scope",
                "both source and destination must be inside the active write scope",
            )
    if _identity(source) == _identity(destination):
        return _error("same_path", "source and destination resolve to the same file")
    if destination.exists() and not overwrite:
        return _error("destination_exists", "destination already exists")

    actual_hash = _file_sha256(source)
    if actual_hash != expected_hash.casefold():
        return _error(
            "source_hash_mismatch",
            "source changed after the action was prepared",
            expected_source_hash=expected_hash.casefold(),
            actual_source_hash=actual_hash,
        )
    try:
        if overwrite:
            os.replace(source, destination)
        else:
            _rename_noreplace(source, destination)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            return _error(
                "cross_device_move_rejected",
                "move_file requires an atomic same-volume move",
            )
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            return _error("destination_exists", "destination already exists")
        return _error("move_failed", str(exc))

    return json.dumps(
        {
            "ok": True,
            "source": str(source),
            "destination": str(destination),
            "sha256": actual_hash,
            "bytes": destination.stat().st_size,
            "effect_id": context.effect_id or None,
            "moved_atomically": True,
        },
        ensure_ascii=False,
    )


def _error(code: str, message: str, **details: Any) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message, **details}},
        ensure_ascii=False,
    )


def resolve_move_file_resources(args: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    source = args.get("source")
    destination = args.get("destination")
    if not isinstance(source, str) or not isinstance(destination, str):
        raise ValueError("source and destination must be strings")
    return (
        {
            "kind": "filesystem",
            "access": "move_source",
            "path": str(Path(source).expanduser().resolve(strict=True)),
        },
        {
            "kind": "filesystem",
            "access": "move_destination",
            "path": str(Path(destination).expanduser().resolve(strict=False)),
        },
    )


__all__ = ["move_file", "resolve_move_file_resources"]
