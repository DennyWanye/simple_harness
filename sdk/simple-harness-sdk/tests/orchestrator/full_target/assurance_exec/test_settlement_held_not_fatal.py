# SPDX-License-Identifier: Apache-2.0
"""Host real-model run 4 (2026-09-23, mission-7ad4aae9eafdba2d): an assured Attempt whose
provider turn failed before any usage fact made the settlement raise out of the loop, which
then re-raised on every round. The reservation is held, visible on the timeline, and the loop
goes on.（2026-10-03 迁到产品同形世界：执行者那次调用以服务端错误结束、没有任何用量事实。）"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _review_world import ReviewScript, quick_waits, reviewed_mission  # noqa: E402

from agent_orchestrator.testing.scripted_replies import planner_reply, retry_same_method  # noqa: E402
from simple_harness.providers.errors import ProviderServerError  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def test_assured_attempt_with_open_settlement_holds_its_reservation(tmp_path):
    provider = ReviewScript(worker_failures=[ProviderServerError(status_code=503)],
                            repair=lambda request: retry_same_method(request) or planner_reply(request))

    async def run() -> None:
        async with reviewed_mission(tmp_path, provider) as case:
            mission = await case.settle()
            # 循环没有被结清错误打断：同一做法重试一次后任务完成。
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            [held] = [json.loads(row[0]) for row in case.store.connection.execute(
                "SELECT payload_json FROM events WHERE mission_id=? AND type='ReservationHeld'",
                (case.mission_id,)).fetchall()]
            assert held["reason"] == "unknown_usage" and held["subject_id"].endswith(":attempt-1")
            [state] = case.store.connection.execute(
                "SELECT state FROM budget_reservations WHERE subject_id=?", (held["subject_id"],)).fetchone()
            assert state != "RELEASED"
            counted = [e.payload for e in case.events("ReservationCountedAtUpperBound")]
            assert any(held["subject_id"] in json.dumps(item) for item in counted), counted

    asyncio.run(run())
