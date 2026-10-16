"""系统目录黑名单与路径规范化（阶段 6E 从 office_paths 下沉，受保护路径检查与办公工具共用）。"""

from __future__ import annotations

import os
from pathlib import Path


def _norm(p: str | os.PathLike[str]) -> Path:
    """Normalize to an absolute, resolved path (collapses ``..``)."""
    return Path(p).expanduser().resolve()


# ---------------------------------------------------------------------------
# System-directory blacklist (final backstop)
# ---------------------------------------------------------------------------
def _system_roots() -> list[Path]:
    roots: list[Path] = []
    for env in ("SystemRoot", "windir"):
        v = os.environ.get(env)
        if v:
            roots.append(_norm(v))
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        v = os.environ.get(env)
        if v:
            roots.append(_norm(v))
    # Hard-coded fallbacks in case the env vars are absent.
    for hard in (r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)"):
        roots.append(_norm(hard))
    return roots


def is_system_path(p: str | os.PathLike[str]) -> bool:
    """True when ``p`` is inside a protected Windows system directory."""
    target = _norm(p)
    for root in _system_roots():
        try:
            if target == root or target.is_relative_to(root):
                return True
        except (ValueError, AttributeError):
            pass
    return False
