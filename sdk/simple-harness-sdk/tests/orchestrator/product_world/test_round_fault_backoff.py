# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""库读写故障按退避间隔重试（试用前第 5 步，2026-10-07）。

原来同一任务同一处出错后每个主循环（约 65 毫秒）就再试一次，两分钟里几千次。现在第一次
故障后 0.25 秒再试，每再错一次翻倍，最长 10 秒；停的规矩不变（连续 6 轮且满 120 秒）。
等间隔的任务不算卡住，``run()`` 也不当它空闲返回。

**改坏检验**：``_mission_round`` 不问 ``_round_due`` → 第二条在 1.2 秒里重试几十次，变红。
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from agent_orchestrator.orchestrator.failure_classes import round_fault_delay
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def test_the_retry_interval_starts_at_a_quarter_second_doubles_and_stops_growing_at_ten():
    assert [round_fault_delay(n) for n in range(1, 9)] == [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


def test_a_failing_place_is_retried_on_the_backoff_not_every_cycle(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            loop = world.loop
            a = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"],
                              "idempotency_key": "backoff-a"})["mission_id"]
            real = loop.commit.heal_mission
            calls: list[float] = []

            def heal(mission_id: str):  # type: ignore[no-untyped-def]
                if mission_id == a:
                    calls.append(world.store.now)
                    raise sqlite3.OperationalError("disk I/O error")
                return real(mission_id)

            loop.commit.heal_mission = heal  # type: ignore[method-assign]
            # 等间隔时 ``run()`` 不当空闲返回：1.2 秒内一直在跑，到时被超时打断
            assert await world.drain(timeout=1.2) is False
            # 0、0.25、0.75 秒各一次（第 4 次在 1.75 秒，到不了）
            assert 2 <= len(calls) <= 4, calls
            gaps = [later - earlier for earlier, later in zip(calls, calls[1:])]
            assert all(gap >= 0.2 for gap in gaps), gaps
            assert str(world.store.get_mission(a).status.value) not in {"FAILED", "CANCELLED", "COMPLETED"}

    asyncio.run(case())
