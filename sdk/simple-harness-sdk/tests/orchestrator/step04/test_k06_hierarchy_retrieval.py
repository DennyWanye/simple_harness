# SPDX-License-Identifier: Apache-2.0
"""推后第 2 批 K06：给执行者挑知识时，"图距离"和"同一分支"按分层结构算（09-10 §10.1 第 676、682 行）。

分层计划下 ``Task.dependency_ids`` 恒空，旧算法除了自己全是常数 0.3。现在按计划结构算：
- 图距离：目标树的分解边加上数据边、顺序约束，无向最短边数 d，分量取 1/(1+d)；
- 同一分支：两者在目标树上最近公共祖先的深度，占这一步深度的比例。
只看结构，不看知识内容说了什么。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.context.retrieval import GoalTree, rank_knowledge
from agent_orchestrator.contracts import Budget, Task, TaskStatus
from agent_orchestrator.memory.verified_knowledge import KnowledgeRecord


def _task(task_id: str) -> Task:
    return Task(id=task_id, mission_id="m", parent_task_ids=(), dependency_ids=(),
                goal="整理 impl_a 的空输入行为", rationale="r", success_criteria=("pytest:tests/x.py",),
                verification_policy=("format_check",), allowed_tools=(), budget=Budget(max_tokens=10),
                priority=1.0, status=TaskStatus.READY, version=1)


def _record(kid: str, source: str) -> KnowledgeRecord:
    # 内容同样相关、新旧相同、没有复用：只剩结构分量能分出先后
    return KnowledgeRecord(id=kid, mission_id="m", claim_id=kid, content="impl_a 空输入 抛错",
                           type="statement", status="VERIFIED", version=1, key=f"key.{kid}",
                           stance="affirms", proposed_by="agent", source_task=source,
                           source_attempt=f"{source}:attempt-1", source_result="r",
                           evidence=("pytest:tests/x.py",), verifier={"layer": "code_test"},
                           created_at=1.0)


def _two_level_tree() -> GoalTree:
    # R → A、B；A → a1、a2、a3；B → b1、b2；a1 把产出交给 a2（数据边）
    return GoalTree.build(
        roots=("R",),
        children={"R": ("A", "B"), "A": ("a1", "a2", "a3"), "B": ("b1", "b2")},
        task_of={name: f"m:{name}" for name in ("R", "A", "B", "a1", "a2", "a3", "b1", "b2")},
        precedence=(("a1", "a2"),),
    )


def test_distance_and_branch_come_from_the_goal_tree_and_its_precedence_edges() -> None:
    tree = _two_level_tree()
    assert tree.distance("m:a2", "m:a2") == 0
    assert tree.distance("m:a2", "m:a1") == 1, "数据边上的直接上游"
    assert tree.distance("m:a2", "m:a3") == 2, "同一做法下的兄弟"
    assert tree.distance("m:a2", "m:b1") == 4, "另一个顶层分支"
    assert tree.distance("m:a2", "m:gone") is None, "已不在现行计划里"
    assert tree.branch_share("m:a2", "m:a2") == 1.0
    assert tree.branch_share("m:a2", "m:a3") == pytest.approx(0.5)
    assert tree.branch_share("m:a2", "m:b1") == 0.0
    assert tree.branch_share("m:a2", "m:gone") == 0.0


def test_over_the_limit_the_related_upstream_is_the_one_kept() -> None:
    tree = _two_level_tree()
    # 上游那条的编号故意排在最后：同分时按编号排，赢只能靠结构分
    records = [_record("K-b1", "m:b1"), _record("K-gone", "m:gone"), _record("K-b2", "m:b2"),
               _record("K-a3", "m:a3"), _record("K-up", "m:a1")]
    full = rank_knowledge(_task("m:a2"), records, goal_tree=tree, limit=10)
    assert [item.id for item in full.items] == ["K-up", "K-a3", "K-b1", "K-b2", "K-gone"]
    parts = {item.id: item.parts for item in full.items}
    assert parts["K-up"]["proximity"] == pytest.approx(0.5) and parts["K-up"]["branch"] == pytest.approx(0.5)
    assert parts["K-b1"]["proximity"] == pytest.approx(0.2) and parts["K-b1"]["branch"] == 0.0
    assert parts["K-gone"]["proximity"] == 0.0 and parts["K-gone"]["branch"] == 0.0

    limited = rank_knowledge(_task("m:a2"), records, goal_tree=tree, limit=1)
    assert [item.id for item in limited.items] == ["K-up"], "超上限时留下的是相关上游"
    assert set(limited.dropped["over_limit"]) == {"K-a3", "K-b1", "K-b2", "K-gone"}


def test_in_a_flat_two_level_plan_the_data_producer_outranks_a_plain_sibling() -> None:
    """根下直接挂一排步骤（真实计划常见形状）：只算树边时兄弟全是等距，又退回常数。"""
    tree = GoalTree.build(roots=("R",), children={"R": ("s1", "s2", "s3")},
                          task_of={name: f"m:{name}" for name in ("R", "s1", "s2", "s3")},
                          precedence=(("s1", "s3"),))
    records = [_record("K-s2", "m:s2"), _record("K-up", "m:s1")]
    limited = rank_knowledge(_task("m:s3"), records, goal_tree=tree, limit=1)
    assert [item.id for item in limited.items] == ["K-up"]
    assert limited.dropped["over_limit"] == ["K-s2"]


def test_a_step_shared_at_two_places_takes_its_closest_occurrence() -> None:
    tree = GoalTree.build(roots=("R",), children={"R": ("A", "B"), "A": ("x1", "p"), "B": ("x2", "q")},
                          task_of={"R": "m:R", "A": "m:A", "B": "m:B", "x1": "m:x", "x2": "m:x",
                                   "p": "m:p", "q": "m:q"},
                          precedence=())
    assert tree.distance("m:q", "m:x") == 2
    assert tree.branch_share("m:q", "m:x") == pytest.approx(0.5)


# ---------------------------------------------------------------- 产品同形：从真实网络建树

_FULL_TARGET = Path(__file__).resolve().parents[1] / "full_target"
for _path in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def test_the_tree_is_read_from_the_product_network(tmp_path: Path) -> None:
    from production_fixture import CHAIN_CRITERIA, chain_planner, enabled_world

    import agent_orchestrator.orchestrator.event_handler as event_handler
    from agent_orchestrator.contracts.state_machines import MissionStatus

    patch = pytest.MonkeyPatch()
    patch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)

    async def case() -> dict[str, Any]:
        async with enabled_world(tmp_path, key="k06-chain", planner=chain_planner,
                                 criteria=CHAIN_CRITERIA) as world:
            mission = await world.product.run_until_settled(world.mission.id, rounds=30)
            assert mission.status is MissionStatus.COMPLETED, (mission.status, mission.final_report)
            network = world.dispatch.network(mission.id)
            steps = {str(child.slot_key): str(network.occurrence(child.occurrence_id).task_id)
                     for instance in network.method_instances for child in instance.child_bindings}
            root = str(network.occurrence(network.root_occurrence_ids[0]).task_id)
            return {"tree": GoalTree.from_network(network), "steps": steps, "root": root}

    try:
        found = asyncio.run(case())
    finally:
        patch.undo()
    tree, steps = found["tree"], found["steps"]
    assert tree.distance(steps["continue"], steps["write"]) == 1, "数据边：write 把交付给 continue"
    assert tree.distance(steps["continue"], found["root"]) == 1, "父目标"
    assert tree.branch_share(steps["continue"], steps["write"]) == 0.0, "两步都直接挂在根下"
