# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""TG-0.2 — SubagentScheduler 有界并发 + 背压 + 进度。"""
from __future__ import annotations

import asyncio

import pytest

from deskpet.agent.subagent_scheduler import SubagentScheduler


def _run(coro):
    return asyncio.run(coro)


def test_global_cap_bounds_concurrency():  # 0.2.1
    peak = {"n": 0, "max": 0}

    async def body():
        sched = SubagentScheduler(global_concurrency=2)

        async def one():
            peak["n"] += 1
            peak["max"] = max(peak["max"], peak["n"])
            await asyncio.sleep(0.02)
            peak["n"] -= 1
            return "ok"

        results = await asyncio.gather(
            *[
                sched.run(kind="general", run_id=f"r{i}", task_id=f"t{i}",
                          parent_sid="p", coro_factory=one)
                for i in range(6)
            ]
        )
        return results

    res = _run(body())
    assert res == ["ok"] * 6
    assert peak["max"] <= 2  # 全局 cap 背压


def test_lane_cap_independent():  # 0.2.2
    seen = {"A": 0, "Amax": 0}

    async def body():
        sched = SubagentScheduler(global_concurrency=8, lane_caps={"A": 1, "B": 8})

        async def a():
            seen["A"] += 1
            seen["Amax"] = max(seen["Amax"], seen["A"])
            await asyncio.sleep(0.02)
            seen["A"] -= 1

        async def b():
            await asyncio.sleep(0.01)

        await asyncio.gather(
            *[sched.run(kind="A", run_id=f"a{i}", task_id=f"a{i}", parent_sid="p",
                        coro_factory=a) for i in range(3)],
            *[sched.run(kind="B", run_id=f"b{i}", task_id=f"b{i}", parent_sid="p",
                        coro_factory=b) for i in range(3)],
        )

    _run(body())
    assert seen["Amax"] == 1  # lane A cap=1 串行


def test_progress_sequence():  # 0.2.3
    events: list[tuple[str, str]] = []

    async def body():
        sched = SubagentScheduler(
            global_concurrency=2,
            progress_sink=lambda p: events.append((p["run_id"], p["status"])),
        )

        async def one():
            return 1

        await sched.run(kind="general", run_id="r1", task_id="t1",
                        parent_sid="p", coro_factory=one)

    _run(body())
    statuses = [s for (r, s) in events if r == "r1"]
    assert statuses == ["queued", "running", "completed"]


def test_failure_emits_failed_and_reraises():  # 0.2.4
    events: list[str] = []

    async def body():
        sched = SubagentScheduler(
            progress_sink=lambda p: events.append(p["status"]))

        async def boom():
            raise ValueError("nope")

        await sched.run(kind="general", run_id="r1", task_id="t1",
                        parent_sid="p", coro_factory=boom)

    with pytest.raises(ValueError):
        _run(body())
    assert "failed" in events


def test_progress_sink_exception_does_not_break():  # 0.2.5
    async def body():
        def bad_sink(_p):
            raise RuntimeError("sink down")

        sched = SubagentScheduler(progress_sink=bad_sink)

        async def one():
            return "still-ok"

        return await sched.run(kind="general", run_id="r1", task_id="t1",
                               parent_sid="p", coro_factory=one)

    assert _run(body()) == "still-ok"


def test_snapshot_returns_to_zero():  # 0.2.6
    async def body():
        sched = SubagentScheduler(global_concurrency=2)

        async def one():
            await asyncio.sleep(0.005)

        await asyncio.gather(
            *[sched.run(kind="general", run_id=f"r{i}", task_id=f"t{i}",
                        parent_sid="p", coro_factory=one) for i in range(4)]
        )
        return sched.snapshot()

    snap = _run(body())
    assert snap == {"running": 0, "queued": 0}
