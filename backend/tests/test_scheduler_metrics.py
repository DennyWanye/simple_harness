# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""WI-OC-2 — SubagentScheduler 累计观测层（背压/lane 指标）。

只覆盖**新增累计字段**（peak_concurrent / total_queued / total_rejected +
lane_wait 分位 + metrics_sink 接线）；既有调度/进度回归在
test_subagent_scheduler.py。BC：累计字段是纯增，旧瞬时字段不变。
"""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_lane_wait_event_in_metrics_whitelist():
    """subagent_lane_wait 真能写进真 sink（白名单接线正确，非 fallback 证据）。"""
    import tempfile
    from pathlib import Path

    from observability.metrics_sink import MetricsSink

    with tempfile.TemporaryDirectory() as td:
        sink = MetricsSink(Path(td) / "m.jsonl")
        ok = sink.record("subagent_lane_wait", {"kind": "code", "duration_ms": 12})
        assert ok is True
        rows = sink.read_all()
        assert any(r["event"] == "subagent_lane_wait" for r in rows)
        row = next(r for r in rows if r["event"] == "subagent_lane_wait")
        # detail 键已在白名单（kind + duration_ms 都存活）。
        assert row["detail"].get("kind") == "code"
        assert row["detail"].get("duration_ms") == 12
