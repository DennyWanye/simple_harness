# SPDX-License-Identifier: Apache-2.0
"""审阅调用一直不回来，任务不许挂着（2026-10-03 阶段 B 裁决第 6 类）。

做法审阅、根终审交出去后，模型调用迟迟不回来（卡住，或重启后回合已不在本进程）：此前意图一直
"已提交"，任务挂着，主循环每轮还自称有进展。现在审阅调用像执行尝试一样计时（运行中没有进展
``stall_seconds``、结果不明 ``_service_blocker_limit``、回合不在本进程立即到期；起点是意图提交
时刻，重启不清零），到期按"被打断"收口：意图关为失败、记分类"回合失败 / 调用作废"与打断回执，
之后完全交给现有路径——第二次调用在新会话里重开；第二次也没回来，做法审阅出"没有结论"
（原因如实写"调用没拿到回复"），根终审重切新包。

**改坏检验**：主循环收集审阅意图时不再计时（改回当作规划轮）→ 第一条超时变红。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, review_input

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


class _ReviewHeld(LayeredScriptedProvider):
    """扣住某一种审阅的前 ``times`` 次调用：调用停在半路，永不返回（直到 ``release``）。"""

    def __init__(self, purpose: str, times: int) -> None:
        super().__init__()
        self.purpose, self.times = purpose, times
        self.held_calls = 0
        self.purpose_calls = 0
        self.let_go = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        data = review_input(request)
        if data is not None and str((data.get("package") or {}).get("purpose")) == self.purpose:
            self.purpose_calls += 1
            if self.held_calls < self.times:
                self.held_calls += 1
                await self.let_go.wait()
        return await super().invoke(request, cancel=cancel)


def _notes(key: str) -> dict:
    return {"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"], "idempotency_key": key}


def _abandoned(store, mission_id: str) -> list[dict[str, Any]]:
    return [event.payload for event in store.iter_events(mission_id)
            if event.type == "AssuranceReviewClassified" and event.payload.get("error_code") == "REVIEW_CALL_ABANDONED"]


async def _drive_until(world, predicate, *, timeout: float) -> None:
    async def loop() -> None:
        while not predicate():
            await world.drain(timeout=3)
            await asyncio.sleep(0.05)
    await asyncio.wait_for(loop(), timeout)


def test_a_root_review_that_never_answers_is_reopened(tmp_path):
    async def case():
        provider = _ReviewHeld("MISSION_FINAL", times=1)
        try:
            async with product_world(tmp_path / "root", provider, stall_seconds=2.0) as world:
                store = world.store
                mission_id = world.create(_notes("root-held"))["mission_id"]
                await _drive_until(world, lambda: str(store.get_mission(mission_id).status.value) in TERMINAL,
                                   timeout=120)
                assert str(store.get_mission(mission_id).status.value) == "COMPLETED"
                [abandoned] = _abandoned(store, mission_id)
                assert abandoned["invocation_ordinal"] == 1 and abandoned["abandoned"]["shape"] == "running"
                assert provider.purpose_calls == 2  # the second call, in a fresh session, answered
                intent = store.get_intent(abandoned["intent_id"])
                assert intent.state == "FAILED"
                assert store.get_receipt("assurance-review-interrupted:" + abandoned["intent_id"]) is not None
        finally:
            provider.let_go.set()

    asyncio.run(case())


def test_a_review_that_never_answers_twice_is_recut_not_left_hanging(tmp_path):
    """两次调用都没回来：现有路径接手（根终审重切新包、新包的调用回来了），任务完成。

    两次都收口之后才放行那两次被扣的调用（脚本模型的并发槽位被它们占着；真实调用会自己超时）：
    迟到的回复不被当成结论——新包的结论来自新包自己的调用。"""

    async def case():
        provider = _ReviewHeld("MISSION_FINAL", times=2)
        try:
            async with product_world(tmp_path / "root", provider, stall_seconds=2.0) as world:
                store = world.store
                mission_id = world.create(_notes("root-held-twice"))["mission_id"]
                await _drive_until(world, lambda: len(_abandoned(store, mission_id)) == 2, timeout=120)
                provider.let_go.set()
                await _drive_until(world, lambda: str(store.get_mission(mission_id).status.value) in TERMINAL,
                                   timeout=120)
                assert str(store.get_mission(mission_id).status.value) == "COMPLETED"
                assert [item["invocation_ordinal"] for item in _abandoned(store, mission_id)] == [1, 2]
                assert provider.purpose_calls == 3
        finally:
            provider.let_go.set()

    asyncio.run(case())


def test_a_root_review_interrupted_by_a_restart_does_not_keep_the_loop_busy(tmp_path):
    """第一段在根终审调用中途退出；第二段重开同一个库：旧意图在第二段一开始就收口（回合不在本进程，
    或恢复后成了"结果不明"——计时都从第一段的提交时刻起），``run()`` 能自己跑到空闲，任务完成。"""

    root = tmp_path / "root"

    async def case() -> None:
        first = _ReviewHeld("MISSION_FINAL", times=1)
        try:
            async with product_world(root, first, stall_seconds=600.0) as world:
                mission_id = world.create(_notes("root-restart"))["mission_id"]
                stop = asyncio.Event()

                async def drive() -> None:
                    while not stop.is_set():
                        await world.loop.run()
                        await world.deployment.between_cycles(auto=True)
                        await asyncio.sleep(0.05)

                runner = asyncio.create_task(drive())
                for _ in range(1200):
                    if first.held_calls:
                        break
                    await asyncio.sleep(0.05)
                assert first.held_calls == 1
                stop.set()
                runner.cancel()
        finally:
            first.let_go.set()

        second = _ReviewHeld("MISSION_FINAL", times=0)
        async with product_world(root, second, stall_seconds=600.0) as world:
            store = world.store
            for _ in range(20):  # each drain is a run() that has to return on its own
                assert await world.drain(timeout=60), "run() did not reach idle"
                if str(store.get_mission(mission_id).status.value) in TERMINAL:
                    break
            assert str(store.get_mission(mission_id).status.value) == "COMPLETED"
            [abandoned] = _abandoned(store, mission_id)
            # After a restart the old call is either gone or held as an unknown outcome; both are
            # timed from the intent's submission in the first run, so it closes at once.
            assert abandoned["abandoned"]["shape"] in {"missing", "blocked"}
            assert abandoned["abandoned"]["waited_seconds"] >= abandoned["abandoned"]["limit_seconds"]
            assert second.purpose_calls == 1

    asyncio.run(case())
