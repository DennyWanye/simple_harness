# SPDX-License-Identifier: Apache-2.0
"""要求的轻重交审阅员按原话判（HTN 一致性补改 H-3，用户 2026-10-04）：系统不给准则分"必须 / 偏好"
（每条都是默认的"必须达到"），终审拿到的准则原文就是用户原话；提示词写明分类字段只是系统默认值、
轻重以原话为准。审阅员是否真的按原话判轻重，留真实模型联测看。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, review_input, reviewer_reply


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_the_final_review_gets_the_users_own_words(tmp_path):
    criteria = ["file:a.md", "file:b.md"]
    seen: list[dict[str, Any]] = []

    def reviewer(request: Any):
        data = review_input(request)
        inner = (data or {}).get("package") or {}
        if inner.get("purpose") == "MISSION_FINAL":
            seen.append(inner)
        return reviewer_reply(request)

    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider(reviewer=reviewer)) as world:
            mission_id = world.create({"goal": "写笔记", "idempotency_key": "weight-words",
                                       "success_criteria": criteria})["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=30)
            assert str(mission.status.value) == "COMPLETED"

    asyncio.run(case())
    assert seen, "the final review never ran"
    rows = seen[-1]["criteria"]
    assert [row["statement"] for row in rows] == criteria
    # 系统不分轻重：每条都是同一个默认值，判轻重的依据只有原话
    assert {row["requirement_class"] for row in rows} == {"REQUIRED_OUTCOME"}
