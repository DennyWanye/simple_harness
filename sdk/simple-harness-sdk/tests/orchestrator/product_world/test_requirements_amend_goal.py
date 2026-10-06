# SPDX-License-Identifier: Apache-2.0
"""改要求时允许改目标（第 2 批 H19；原计划 §9.x）。

目标变更是一次要求修订：``{op: "goal", statement}`` 写要求书第 n+1 版（条目照旧）、根合同新版本、
根目标语义绑定的目标陈述换成新目标、任务自己的目标文本换新、作用域纪元动一次；回执与事件里
记下新旧目标。同一命令只改一次目标；改成一样的、空的、字段不对的都按名拒绝、一样都不写。
现有计划要不要重做由规划器判，这里不动计划。

**改坏检验**：改目标时不更新任务的目标文本 → 第一条变红。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, planner_reply


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


amend_script = _load("_amend_script_goal", "test_requirements_amend.py")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _root(store: Any, mission_id: str) -> Any:
    root_task = next(row[0] for row in store.connection.execute(
        "SELECT task_id FROM task_semantics WHERE mission_id=? AND task_id LIKE 'user-root-%'", (mission_id,)))
    return HtnStore(store).latest_task_semantics(root_task)


def test_a_goal_change_is_one_requirements_amendment(tmp_path):
    async def case():
        provider = LayeredScriptedProvider(planner=planner_reply)
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写三份文件", "idempotency_key": "amend-goal",
                                       "success_criteria": ["file:a.md", "file:b.md"]})["mission_id"]
            await amend_script.until_first_plan(world, mission_id)
            before = amend_script.written(world.store, mission_id)
            version = world.store.get_mission(mission_id).version
            assert _root(world.store, mission_id).goal_signature.statement == "写三份文件"

            # 按名拒绝、一样都不写
            for changes, code in (
                ([{"op": "goal", "statement": "写三份文件"}], "AMEND_DUPLICATE"),
                ([{"op": "goal", "statement": "  "}], "AMEND_EMPTY"),
                ([{"op": "goal"}], "AMEND_EMPTY"),
                ([{"op": "goal", "statement": "甲"}, {"op": "goal", "statement": "乙"}], "AMEND_EMPTY"),
                ([{"op": "goal", "goal": "新目标"}], "AMEND_EMPTY"),
            ):
                with pytest.raises(FacadeError) as refused:
                    amend_script.amend(world, mission_id, changes)
                assert refused.value.code == code, changes
            assert amend_script.written(world.store, mission_id) == before
            assert world.store.get_mission(mission_id).goal == "写三份文件"

            # 只改目标：要求条目不变也写第 2 版；根合同、根陈述、任务目标、纪元都换
            receipt = amend_script.amend(world, mission_id, [{"op": "goal", "statement": "写三份中文文件"}])
            after = amend_script.written(world.store, mission_id)
            assert after == {**before, "revisions": [1, 2], "root_contract": 2, "epoch": before["epoch"] + 1,
                             "events": 1, "receipts": 1}
            assert receipt["goal"] == {"previous": "写三份文件", "current": "写三份中文文件"}
            assert receipt["changes"] == {"added": [], "rewritten": [], "removed": []}
            mission = world.store.get_mission(mission_id)
            assert mission.goal == "写三份中文文件" and mission.version == version + 1
            root = _root(world.store, mission_id)
            assert root.goal_signature.statement == "写三份中文文件"
            assert root.typed_parameters["goal"] == "写三份中文文件"
            htn = HtnStore(world.store)
            latest = htn.latest_requirements_revision(mission_id)
            assert latest.amendment_credential_ref == "amend-1"
            assert {str(c.criterion_id): (int(c.revision), str(c.statement)) for c in latest.criteria} == {
                "c-user-1": (1, "file:a.md"), "c-user-2": (1, "file:b.md")}
            [event] = [e for e in world.store.list_events(mission_id) if e.type == "RequirementsAmended"]
            assert event.payload["goal"] == receipt["goal"]
            # 同一命令重放：同一回执，不再写
            assert amend_script.amend(world, mission_id, [{"op": "goal", "statement": "写三份中文文件"}],
                                      expected=receipt["previous_requirements_ref"]) == receipt
            assert amend_script.written(world.store, mission_id) == after

            # 目标与条目一起改
            mixed = amend_script.amend(world, mission_id, [
                {"op": "goal", "statement": "写两份中文文件"},
                {"op": "remove", "criterion_id": "c-user-2"}], command_id="amend-2")
            assert mixed["goal"]["current"] == "写两份中文文件" and mixed["changes"]["removed"] == ["c-user-2"]
            assert world.store.get_mission(mission_id).goal == "写两份中文文件"
            assert _root(world.store, mission_id).requirement_refs == ("c-user-1",)
            assert amend_script.written(world.store, mission_id)["revisions"] == [1, 2, 3]

    asyncio.run(case())
