"""2026-09-25 主流程优化条目 7：任务与会话数据只统计、只提醒，不删除。"""

from __future__ import annotations

import asyncio

import pytest

from deskpet.orchestration.storage_usage import CACHE_TTL_MS, FORCED_REFRESH_MIN_MS, StorageUsage, measure


def _fill(root):
    (root / "execution-native-256k.db.arp-root" / "sessions" / "session-1").mkdir(parents=True)
    (root / "execution-native-256k.db.arp-root" / "sessions" / "session-1" / "index.sqlite3").write_bytes(b"a" * 300)
    (root / "execution-native-256k.db").write_bytes(b"b" * 200)
    (root / "orchestrator.db").write_bytes(b"c" * 100)
    (root / "workspaces" / "m1" / ".assurance").mkdir(parents=True)
    (root / "workspaces" / "m1" / ".assurance" / "review.json").write_bytes(b"d" * 50)
    (root / "workspaces" / "m1" / "out.txt").write_bytes(b"e" * 25)


def test_measure_classifies_and_sums(tmp_path):
    _fill(tmp_path)
    totals = measure(tmp_path)
    assert totals == {"agent_sessions": 500, "assurance": 50, "orchestration": 125}
    assert measure(tmp_path / "missing") == {"agent_sessions": 0, "assurance": 0, "orchestration": 0}


@pytest.mark.asyncio
async def test_get_caches_throttles_and_warns(tmp_path):
    _fill(tmp_path)
    now = [1_000_000]
    usage = StorageUsage(tmp_path, warn_bytes=600, clock_ms=lambda: now[0])
    assert usage.over_warn is None  # nothing measured yet
    first = await usage.get()
    assert first["bytes"] == 675 and first["over_warn"] is True and first["measured"] is True
    assert sum(row["bytes"] for row in first["breakdown"]) == first["bytes"]
    (tmp_path / "orchestrator.db").write_bytes(b"c" * 1000)
    assert (await usage.get())["bytes"] == 675  # cached inside the TTL
    now[0] += CACHE_TTL_MS
    assert (await usage.get())["bytes"] == 1575  # stale: re-measured
    (tmp_path / "orchestrator.db").write_bytes(b"c" * 100)
    assert (await usage.get(refresh=True))["bytes"] == 675  # forced
    (tmp_path / "orchestrator.db").write_bytes(b"c" * 1000)
    assert (await usage.get(refresh=True))["bytes"] == 675  # a second force within 10 s is ignored
    now[0] += FORCED_REFRESH_MIN_MS
    assert (await usage.get(refresh=True))["bytes"] == 1575
    assert usage.schedule_if_stale() is False  # just measured
    # nothing was deleted by any of this
    assert (tmp_path / "execution-native-256k.db.arp-root" / "sessions" / "session-1" / "index.sqlite3").exists()
    await asyncio.sleep(0)
