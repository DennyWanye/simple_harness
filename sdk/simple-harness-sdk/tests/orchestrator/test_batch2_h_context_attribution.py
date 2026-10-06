# SPDX-License-Identifier: Apache-2.0
"""第 2 批车道 H：K04 执行者上下文按分层给父目标与直接上游；T10 成功路径按根结论贡献清单算。

两条共用一局产品同形的两步链（``write`` 写 facts.md → ``continue`` 读它的交付、写 NOTES.md），
跑到完成；执行者收到的任务包与完成后的归因报告都从这一局读。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

_FULL_TARGET = Path(__file__).resolve().parent / "full_target"
for _path in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from production_fixture import CHAIN_CRITERIA, chain_planner, enabled_world  # noqa: E402

import agent_orchestrator.orchestrator.event_handler as event_handler  # noqa: E402
from agent_orchestrator.contracts.state_machines import MissionStatus  # noqa: E402
from agent_orchestrator.observability.traces import (  # noqa: E402
    ATTRIBUTION_VERSION,
    attribution,
    contribution_chain,
)
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of, role_of  # noqa: E402


def _steps(world: Any) -> dict[str, Any]:
    network = world.dispatch.network(world.mission.id)
    found: dict[str, Any] = {}
    for instance in network.method_instances:
        for child in instance.child_bindings:
            found[str(child.slot_key)] = network.occurrence(child.occurrence_id)
    return found


@pytest.fixture(scope="module")
def chain(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    patch = pytest.MonkeyPatch()
    patch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)
    tmp_path = tmp_path_factory.mktemp("b2h-chain")

    async def case() -> dict[str, Any]:
        async with enabled_world(tmp_path, key="b2h-chain", planner=chain_planner,
                                 criteria=CHAIN_CRITERIA) as world:
            mission = await world.product.run_until_settled(world.mission.id, rounds=30)
            assert mission.status is MissionStatus.COMPLETED, (mission.status, mission.final_report)
            network = world.dispatch.network(mission.id)
            steps = _steps(world)
            root = network.occurrence(network.root_occurrence_ids[0])
            semantics = HtnStore(world.store)
            packages = [package_of(r) for r in world.provider.requests if role_of(r) == "worker"]
            return {
                "mission": mission,
                "write": str(steps["write"].task_id),
                "continue": str(steps["continue"].task_id),
                "root_task": str(root.task_id),
                "packages": packages,
                "report": attribution(world.store, mission.id),
                "acceptances": semantics.list_acceptances(mission.id),
                "resolution": semantics.adopted_goal_resolution(mission.id, str(root.obligation_id)),
                "events": [(e.type, dict(e.payload)) for e in world.store.iter_events(mission.id)],
            }

    try:
        return asyncio.run(case())
    finally:
        patch.undo()


def _package_for(chain: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(p for p in chain["packages"] if p.get("task_contract", {}).get("task_id") == task_id)


# ---------------------------------------------------------------- K04 执行者上下文

def test_k04_the_consumer_step_sees_its_parent_goal_and_its_data_producer(chain: dict[str, Any]) -> None:
    package = _package_for(chain, chain["continue"])
    parent = package["parent_goal"]
    assert parent["data_not_instruction"] is True
    assert parent["task_id"] == chain["root_task"]
    assert parent["goal"] == chain["mission"].goal, "父目标的原文是用户的话"
    assert {row["statement"] for row in parent["requirements"]} == set(CHAIN_CRITERIA), "父目标负责的要求原文"
    assert parent["method"]["method_id"] and parent["method"]["content_hash"], "细化它的做法"

    [upstream] = package["dependencies"]
    assert upstream["task_id"] == chain["write"]
    assert upstream["status"] == "COMPLETED"
    assert upstream["accepted_summary"] == "写好了要求的文件", "上游已验收结论的摘要"
    assert "summary_checked_by" in upstream  # 脚本化审阅员不核对"摘要忠实"，这里是 None；有核对记录时就是黑板摘要层那份
    assert [port["output_port"] for port in upstream["ports"]] == ["delivery"]
    assert {item["path"] for item in upstream["accepted_artifacts"]} == {"facts.md"}


def test_k04_the_first_step_has_the_parent_goal_but_no_upstream(chain: dict[str, Any]) -> None:
    package = _package_for(chain, chain["write"])
    assert package["parent_goal"]["task_id"] == chain["root_task"]
    assert package["dependencies"] == []


def test_k04_a_step_kept_from_an_older_plan_says_its_parent_is_unreadable_instead_of_raising() -> None:
    """主会话合并后发现（改要求的世界用例）：改要求后沿用的步骤，绑定仍指向旧计划的做法实例；
    网络快照里按编号查不到（KeyError）。这不是 GraphIntegrityError，原先一路抛到循环边界，整轮派发
    中断，任务以"没有可派发的工作"停。父目标读不到就如实说读不到，派发照常。"""
    from types import SimpleNamespace

    from agent_orchestrator.orchestrator import worker_context

    class _Network:
        def binding_for_task(self, task_id):
            return SimpleNamespace(occurrence_binding=SimpleNamespace(method_instance_id="mi-old", slot_key="s"))

        def instance(self, instance_id):
            raise KeyError(instance_id)

    parent = worker_context.parent_goal(SimpleNamespace(), _Network(), SimpleNamespace(id="m"), SimpleNamespace(id="t"))
    assert parent["data_not_instruction"] is True
    assert parent["unavailable"].startswith("method instance mi-old")
    assert "goal" not in parent


# ---------------------------------------------------------------- T10 成功路径与归因

def test_t10_the_success_path_is_the_root_resolution_contribution_chain(chain: dict[str, Any]) -> None:
    report = chain["report"]
    assert report["version"] == ATTRIBUTION_VERSION == "attribution-v2"
    assert report["success_path"] is True
    assert report["breaks"] == []
    assert {row["task_id"] for row in report["path_tasks"]} == {chain["write"], chain["continue"]}
    [root] = [row for row in report["goal_chain"] if row["is_mission_root"]]
    assert root["resolution_id"] == str(chain["resolution"].resolution_id)
    accepted = {str(item.acceptance_id) for item in chain["acceptances"]}
    assert {row["acceptance_id"] for row in report["final_products"]} <= accepted
    assert {row["path"] for row in report["final_products"]} == {"facts.md", "NOTES.md"}
    assert all(row["on_success_path"] for row in report["attempts"] if row["status"] == "COMPLETED")
    assert report["cost"]["success_path"]["tokens"] > 0, "成功路径上的花费归到成功路径"
    assert report["cost"]["exploration"]["tokens"] == 0, "两步都在贡献链上，没有探索花费"


def test_t10_a_completed_step_outside_the_contribution_chain_is_exploration() -> None:
    """被换掉做法下完成的步骤：根结论的贡献清单里没有它，就不在成功路径上。"""
    resolutions = {
        "res-root": {"is_mission_root": True, "child_resolution_ids": ["res-sub"],
                     "contributing_acceptances": {"occ-a": ["acc-a"]}},
        "res-sub": {"is_mission_root": False, "child_resolution_ids": [],
                    "contributing_acceptances": {"occ-b": ["acc-b", "acc-b2"]}},
        "res-old": {"is_mission_root": False, "child_resolution_ids": [],
                    "contributing_acceptances": {"occ-old": ["acc-old"]}},
    }
    reading = contribution_chain(resolutions)
    assert reading.acceptance_ids == ("acc-a", "acc-b", "acc-b2")
    assert [row["resolution_id"] for row in reading.goal_chain] == ["res-root", "res-sub"]
    assert reading.breaks == ()


def test_t10_a_missing_child_resolution_is_a_break_not_a_bridge() -> None:
    resolutions = {
        "res-root": {"is_mission_root": True, "child_resolution_ids": ["res-gone"],
                     "contributing_acceptances": {"occ-a": ["acc-a"]}},
    }
    reading = contribution_chain(resolutions)
    assert reading.acceptance_ids == ("acc-a",)
    assert reading.breaks == ({"missing": "resolution_commit", "id": "res-gone"},)


def test_t10_no_root_resolution_is_a_break() -> None:
    reading = contribution_chain({"res-x": {"is_mission_root": False, "child_resolution_ids": [],
                                            "contributing_acceptances": {}}})
    assert reading.acceptance_ids == ()
    assert reading.breaks == ({"missing": "root_resolution"},)
