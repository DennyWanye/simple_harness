# SPDX-License-Identifier: Apache-2.0
"""推后必补第 3 批车道 R2，A22：历史切面按当时那一版要求显示准则。

原计划 Assurance 附录 C.6（:530）：快照每一项的 ``history_state`` 取固定事件序号时的原状态。此前历史
视图只认激活事件里的第 1 版要求：用户中途改了要求以后，翻改后的历史切面，看到的仍是旧准则。
现在取 ``at_event_seq`` 以内最后一次改要求（``RequirementsAmended``）钉住的版本与哈希；没改过就是激活
时那一版。

产品同形世界：建任务（三条准则）→ 第一个计划提交 → 用户经门面加一条准则。

**改坏检验**：历史切面退回只按激活哈希找要求 → 改后的切面缺新准则 → 本条失败。
"""
from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, planner_reply

from test_requirements_amend import amend, until_first_plan  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _history_criteria(world, mission_id: str, at_seq: int) -> set[str]:
    page = world.control.assurance_snapshot({
        "schema_version": 1, "request_id": "r-hist", "mission_id": mission_id,
        "view": "HISTORY", "at_event_seq": at_seq, "cursor": None, "limit": 100})
    assert page["snapshot_seq"] == at_seq
    return {item["id"] for item in page["items"] if item["kind"] == "CRITERION"}


def test_a_history_slice_shows_the_criteria_of_the_requirements_in_force_at_that_seq(tmp_path):
    async def case() -> None:
        provider = LayeredScriptedProvider(planner=planner_reply)
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写三份文件", "idempotency_key": "r2-a22",
                                       "success_criteria": ["file:a.md", "file:b.md", "file:c.md"]})["mission_id"]
            await until_first_plan(world, mission_id)
            htn = HtnStore(world.store)
            first = {c.criterion_id for c in htn.get_requirements_revision(mission_id, 1).criteria}
            before = world.store.last_event_seq(mission_id)
            assert _history_criteria(world, mission_id, before) == first

            amend(world, mission_id, [{"op": "add", "statement": "file:d.md"}], command_id="r2-a22-amend")
            second = {c.criterion_id for c in htn.get_requirements_revision(mission_id, 2).criteria}
            assert second > first
            after = world.store.last_event_seq(mission_id)
            assert _history_criteria(world, mission_id, after) == second
            # 改要求之前的切面不受后来的事实影响
            assert _history_criteria(world, mission_id, before) == first

    asyncio.run(case())
