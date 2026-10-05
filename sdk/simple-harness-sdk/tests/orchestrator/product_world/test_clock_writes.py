# SPDX-License-Identifier: Apache-2.0
"""保证通道的时钟最高水位什么时候落库（联测裁决 2026-10-05）。

真机上每一轮都写一条时钟回执（两小时 8.6 万条、库 162 MB）。现在：进程内的水位是精确的；库里的
那一行与它的回执只在三种情况下写——发现回拨、恢复、正常前进累计满 10 秒。

**改坏检验**（CLK-01）：正常前进不设门槛（每次都落库）→ 第一段变红；
（CLK-02）不用进程内水位（只和库里的比）→ 10 秒内的回拨发现不了 → 第二段变红。
"""
from __future__ import annotations

import asyncio

from agent_orchestrator.assurance.clock import CLOCK_PERSIST_STEP_MS
from agent_orchestrator.orchestrator.assurance_clock import observe_assurance_clock
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def test_the_clock_mark_is_written_on_rollback_recovery_or_every_ten_seconds(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            commit, connection = world.loop.commit, world.store.connection

            def receipts() -> int:
                return int(connection.execute(
                    "SELECT COUNT(*) FROM commit_receipts WHERE kind='AssuranceClockObserved'").fetchone()[0])

            def row() -> tuple[int, int, str]:
                return tuple(connection.execute(
                    "SELECT clock_generation, wall_high_ms, clock_state FROM assurance_environment_state"
                ).fetchone())

            base = int(world.store.now * 1000) + 60_000
            observe_assurance_clock(commit, now_ms=base)  # 比库里的多走了一分钟：落库
            start, generation = receipts(), row()[0]
            assert row()[1] == base
            # 10 秒内一步步往前走：进程内记着，不落库、不写回执
            for step in range(1, 50):
                state = observe_assurance_clock(commit, now_ms=base + step * 100)
                assert state.wall_high_ms == base + step * 100 and state.state == "STABLE"
            assert receipts() == start and row()[1] == base
            # 10 秒内的回拨：比进程内见过的最高水位小，立刻发现、升代次、落库
            high = base + 4_900
            back = observe_assurance_clock(commit, now_ms=base + 2_000)
            assert (back.state, back.generation, back.wall_high_ms) == ("ROLLBACK", generation + 1, high)
            assert receipts() == start + 1 and row() == (generation + 1, high, "ROLLBACK")
            # 还没回到最高水位：仍是回拨，不重复写
            observe_assurance_clock(commit, now_ms=base + 3_000)
            assert receipts() == start + 1
            # 回到最高水位之后：恢复，落库
            again = observe_assurance_clock(commit, now_ms=high + 1)
            assert again.state == "STABLE" and receipts() == start + 2 and row()[2] == "STABLE"
            # 正常前进累计满 10 秒：恰好再写一条
            observe_assurance_clock(commit, now_ms=high + CLOCK_PERSIST_STEP_MS)
            assert receipts() == start + 2
            observe_assurance_clock(commit, now_ms=high + 1 + CLOCK_PERSIST_STEP_MS)
            assert receipts() == start + 3 and row()[1] == high + 1 + CLOCK_PERSIST_STEP_MS

    asyncio.run(case())
