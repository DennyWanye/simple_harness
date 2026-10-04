# SPDX-License-Identifier: Apache-2.0
"""执行图各张表的直接负控：两个真跑完的任务，跨任务插入、改、删都被库拒绝（见 ``table_guards.py``）。

换计划时才有行的两张表（收敛作业与它的目标）在 ``test_repair_replace_method.py`` 的换做法用例末尾核。
"""
from __future__ import annotations

import asyncio

import pytest
from table_guards import check_table_guards

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_the_library_itself_refuses_cross_mission_and_rewritten_rows(tmp_path):
    async def case():
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            ids = [world.create({"goal": f"写一份笔记 {name}", "idempotency_key": "guards-" + name,
                                 "success_criteria": [f"file:notes/{name}.md"]})["mission_id"] for name in "ab"]
            for mission_id in ids:
                mission = await world.run_until_settled(mission_id, rounds=20)
                assert str(mission.status.value) == "COMPLETED"
            mine, other = ids
            before = world.store.connection.total_changes
            done = check_table_guards(world.store.connection, mine, other, (
                "taskgraph_policy_bindings", "taskgraph_revision_records", "taskgraph_member_pins",
                "taskgraph_method_pins", "taskgraph_demand_refs", "taskgraph_attempt_inputs",
                "taskgraph_followups"))
            assert len(done) == 7
            assert world.store.connection.total_changes == before  # 一行都没写进去

    asyncio.run(case())
