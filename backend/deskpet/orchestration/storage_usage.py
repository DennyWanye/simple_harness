# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""How much disk the task and Agent-session data takes (2026-09-25 主流程优化条目 7).

User decision 2026-09-25: session data (chat turns, retrieval indexes, logs, check
records) is kept forever for audit — nothing here deletes anything.  The Host only
measures ``<user_data>/data/agent-orchestrator/`` and reminds the user in Settings once
the total passes ``[orchestration] storage_warn_bytes`` (default 5 GiB).

The walk runs in a thread (never on the event loop), its result is cached, and a
refresh is throttled: at most one forced re-measure every 10 s, one automatic one
every 30 min.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_WARN_BYTES = 5 * 1024**3
CACHE_TTL_MS = 10 * 60 * 1000
AUTO_REFRESH_MS = 30 * 60 * 1000
FORCED_REFRESH_MIN_MS = 10 * 1000

#: Fixed breakdown keys the Settings page shows (label is the user-facing text).
BREAKDOWN = (
    ("agent_sessions", "Agent 会话与检索索引"),
    ("assurance", "质量检查记录"),
    ("orchestration", "任务编排记录"),
)


def _classify(relative: Path) -> str:
    parts = relative.parts
    first = parts[0] if parts else ""
    if first.endswith(".arp-root") or first.startswith("execution-") or first.endswith(".skill-runs"):
        return "agent_sessions"
    if any("assurance" in part for part in parts):
        return "assurance"
    return "orchestration"


def measure(root: Path) -> dict[str, int]:
    """Bytes per breakdown key under ``root`` (missing root = all zero).  Symlinks are
    not followed: the orchestration directory must not contain any (paths.py)."""

    totals = {key: 0 for key, _ in BREAKDOWN}
    if not root.is_dir():
        return totals
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.is_symlink():
                    continue
                size = path.stat().st_size
            except OSError:
                continue
            totals[_classify(path.relative_to(root))] += size
    return totals


class StorageUsage:
    def __init__(self, root: Path, *, warn_bytes: int = DEFAULT_WARN_BYTES, clock_ms=None) -> None:  # type: ignore[no-untyped-def]
        self.root = Path(root)
        self.warn_bytes = int(warn_bytes)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._totals: dict[str, int] | None = None
        self._measured_at_ms: int | None = None
        self._forced_at_ms: int | None = None
        self._task: asyncio.Task[Any] | None = None

    # ---- reads ---------------------------------------------------------------------
    @property
    def over_warn(self) -> bool | None:
        """``None`` until the first measurement lands."""

        return None if self._totals is None else sum(self._totals.values()) >= self.warn_bytes

    def snapshot(self) -> dict[str, Any]:
        totals = self._totals or {key: 0 for key, _ in BREAKDOWN}
        return {
            "bytes": sum(totals.values()),
            "measured_at_ms": self._measured_at_ms,
            "warn_bytes": self.warn_bytes,
            "over_warn": bool(self.over_warn),
            "measured": self._totals is not None,
            "breakdown": [{"key": key, "label": label, "bytes": totals[key]} for key, label in BREAKDOWN],
        }

    # ---- measurement ---------------------------------------------------------------
    def _stale(self, ttl_ms: int) -> bool:
        return self._measured_at_ms is None or self._clock_ms() - self._measured_at_ms >= ttl_ms

    async def _measure(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            totals = await loop.run_in_executor(None, measure, self.root)
        except Exception:  # noqa: BLE001 - a failed walk keeps the previous number
            logger.warning("storage usage walk failed for %s", self.root, exc_info=True)
            return
        self._totals, self._measured_at_ms = totals, self._clock_ms()

    async def get(self, *, refresh: bool = False) -> dict[str, Any]:
        """The cached snapshot, re-measured when stale (10 min) or when ``refresh`` asks
        for it (throttled to one forced walk per 10 s)."""

        now = self._clock_ms()
        forced = refresh and (self._forced_at_ms is None or now - self._forced_at_ms >= FORCED_REFRESH_MIN_MS)
        if forced:
            self._forced_at_ms = now
        if forced or self._stale(CACHE_TTL_MS):
            await self._measure()
        return self.snapshot()

    def schedule_if_stale(self) -> bool:
        """Background refresh (30 min) hooked into the service's wake loop; never blocks."""

        if not self._stale(AUTO_REFRESH_MS) or (self._task is not None and not self._task.done()):
            return False
        self._task = asyncio.get_running_loop().create_task(self._measure(), name="storage-usage-refresh")
        return True


__all__ = ("BREAKDOWN", "DEFAULT_WARN_BYTES", "StorageUsage", "measure")
