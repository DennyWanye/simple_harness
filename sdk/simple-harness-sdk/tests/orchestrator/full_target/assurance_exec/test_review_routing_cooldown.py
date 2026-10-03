# SPDX-License-Identifier: Apache-2.0
"""审阅调用遇到服务商 5xx、模型进入冷却期时，打开审阅只是"现在开不了"，不是主循环的错误
（2026-10-03，迁移子代理发现）。

此前终审（MISSION_FINAL）的打开路径只接住保证通道自己的错误，冷却期里路由抛出的
``RoutingUnavailable`` 冲出 ``_ask_root_reviewer``，``run()`` 整轮停掉，同进程里别的任务一起停。
现在：冷却期里不开审阅、不算进展，冷却过后照常打开，任务完成。服务端报错属于基础设施失败，
不扣任何次数（用户 2026-09-28 决定）。做法审阅那条路同样把冷却当"服务故障"，只计服务故障宽限。

**改坏检验**：去掉打开审阅时对冷却期的处理 → 主循环抛出 → 变红。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

from simple_harness.providers.errors import ProviderServerError  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def test_a_final_review_that_cannot_open_during_a_cooldown_opens_after_it(tmp_path):
    # One 503 trips the cooldown (threshold 1); the failed turn's retry re-opens the review
    # while the reviewing model is still cooling down.
    provider = ReviewScript(failures={"MISSION_FINAL": [ProviderServerError(status_code=503)]})

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider, profile_cooldown_seconds=5.0,
                                    profile_failure_threshold=1) as case:
            mission = await case.settle(timeout=90)
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            assert provider.review_calls["MISSION_FINAL"] >= 2

    asyncio.run(run())
