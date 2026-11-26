# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""One canonical form for workspace-relative paths (code review P1-3 / P1-4): the tool
gateway's untrusted-source check and the claim evidence grading must agree on what
``./docs/x``, ``docs//x`` or ``docs\\x`` denote, or a prefix rule can be bypassed."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from pathlib import PurePosixPath


def normalise_workspace_path(path: str) -> str:
    """``./docs/./x`` → ``docs/x``; leading slashes dropped; ``..`` kept verbatim so the
    workspace resolver (which refuses it) still sees it."""

    text = str(path).replace("\\", "/").strip()
    parts = [part for part in PurePosixPath(text).parts if part not in {".", "", "/"}]
    return "/".join(parts)


def under_prefix(path: str, prefixes: tuple[str, ...] | list[str]) -> bool:
    normalised = normalise_workspace_path(path)
    for prefix in prefixes:
        clean = normalise_workspace_path(prefix)
        if clean and (normalised == clean or normalised.startswith(clean + "/")):
            return True
    return False


def spelling_key(path: str) -> str:
    # 有些组合附标在大小写折叠后会变成字母，须先规范分解再折叠。
    return unicodedata.normalize("NFD", unicodedata.normalize("NFD", path).casefold())


def _prefixes(path: str) -> Iterable[str]:
    parts = path.split("/")
    for depth in range(1, len(parts) + 1):
        yield "/".join(parts[:depth])


def ambiguous_paths(paths: Iterable[str]) -> set[str]:
    """找出文件名或任一级目录名有多种拼写的路径。"""
    keys_by_path: dict[str, list[str]] = {}
    spelling: dict[str, str] = {}
    ambiguous: set[str] = set()
    for path in paths:
        keys = []
        for prefix in _prefixes(path):
            key = spelling_key(prefix)
            if spelling.setdefault(key, prefix) != prefix:
                ambiguous.add(key)
            keys.append(key)
        keys_by_path[path] = keys
    return {path for path, keys in keys_by_path.items() if not ambiguous.isdisjoint(keys)}


__all__ = ("ambiguous_paths", "normalise_workspace_path", "spelling_key", "under_prefix")
