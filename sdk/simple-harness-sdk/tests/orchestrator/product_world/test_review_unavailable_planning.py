# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""规划器的做法已被接受、却开不了独立审阅（时钟回拨、审阅线路不可用……）：这不是规划器答错，
走服务故障宽限，不扣答错次数（用户 2026-09-28 决定；2026-10-20 在 Lima 虚拟机里时钟回拨让
任务失败后补齐）。

**改坏检验**：只把 ``REVIEW_ROUTE_UNAVAILABLE`` 当服务故障（改动前的样子）→ 时钟回拨连续
三次就把三次答错名额用完，任务以规划失败结束 → 第一条变红。
"""

from __future__ import annotations

import asyncio

import pytest
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.orchestrator.assurance_review_runtime import AssuranceReviewRuntime
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _flaky_review(monkeypatch, code: str, failures: int) -> dict[str, int]:
    original = AssuranceReviewRuntime.ensure_method_plan
    calls = {"refused": 0}

    def ensure(self, mission, **kwargs):  # type: ignore[no-untyped-def]
        if calls["refused"] < failures:
            calls["refused"] += 1
            raise AssuranceError(code)
        return original(self, mission, **kwargs)

    monkeypatch.setattr(AssuranceReviewRuntime, "ensure_method_plan", ensure)
    return calls


async def _run(root, attempts: int) -> dict:
    async with product_world(
        root, LayeredScriptedProvider(), max_planning_attempts=attempts
    ) as world:
        mission_id = world.create(
            {
                "goal": "写一份 NOTES.md",
                "idempotency_key": "review-unavailable-1",
                "success_criteria": ["file:NOTES.md"],
            }
        )["mission_id"]
        for _ in range(40):
            await world.drain(timeout=20)
            mission = world.store.get_mission(mission_id)
            if str(mission.status) in ("COMPLETED", "FAILED", "CANCELLED"):
                break
        mission = world.store.get_mission(mission_id)
        return {"status": str(mission.status), "stop": str(getattr(mission, "stop_reason", ""))}


def test_clock_rollbacks_during_method_review_do_not_spend_the_planners_answers(
    tmp_path, monkeypatch
):
    calls = _flaky_review(monkeypatch, "TIME_DISCONTINUITY", failures=3)
    result = asyncio.run(_run(tmp_path / "root", attempts=3))
    assert calls["refused"] == 3
    assert result["status"] == "COMPLETED", result


def test_a_review_that_never_opens_stops_as_runtime_unavailable_not_planning_failed(
    tmp_path, monkeypatch
):
    """宽限用完：按"运行环境不可用"停，不说成规划失败。"""

    _flaky_review(monkeypatch, "TIME_DISCONTINUITY", failures=10_000)
    result = asyncio.run(_run(tmp_path / "root", attempts=3))
    assert result["status"] == "FAILED", result
    assert "runtime_unavailable" in result["stop"].lower(), result
