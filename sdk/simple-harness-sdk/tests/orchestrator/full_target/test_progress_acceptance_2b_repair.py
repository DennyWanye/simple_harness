# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""NEXT-TG-1.0 第二批 B 验收补测（§7.3 第 3、6 条）。

* 第 3 条：明确的结构缺口（叶子没过检查 → 原修复触发）只产生一次原 Planner 请求；重复
  收集触发、反复 tick、冷重开都不重复产生请求或规划轮。世界是产品同形部署
  （``taskgraph_exec.production_fixture``）：执行者真交了没写结论的结果，修复请求与规划轮由
  主循环真开出来；规划器的那次调用被扣住（一次很慢的模型调用），轮次停在"已开出"。
* 第 6 条（动作结果未知只核对不重发）：2026-10-03 并入代表用例 3 的变体
  ``operation_completion/test_publish_variants.py::test_a_lost_reply_is_reconciled_and_never_resent``
  （服务端已发布、回执丢了、核对又连不上：动作一直"结果未知"，只核对、不重发，服务恢复后核对成
  成功，始终一次发布，``budget_conserved`` 为真）。原用例里"手工给一个 ``charge:`` 记账科目预留、
  导入未知用量、按上限结清"那一段是手造产品自己才会写的账本行（裁决①b2），删；按上限结清本身的
  性质由供方记账族的产品同形用例守。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE / "taskgraph_exec"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from production_fixture import (  # noqa: E402
    OUTPUT,
    enabled_world,
    product_loop,
    result_envelope,
    scripted_worker,
)

from agent_orchestrator.orchestrator.planning_repair_requests import (  # noqa: E402
    REQUESTED,
    collect_triggers,
    pending_requests,
)
from agent_orchestrator.testing.fixtures import RoleScriptedProvider  # noqa: E402

OPEN = ("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")


# ======================================================================================
# §7.3 第 3 条：同一结构缺口只开一轮
# ======================================================================================


def _planner_intents(loop, mission_id: str) -> list:
    return [i for i in loop.store.list_intents(*OPEN, "SETTLED", "FAILED")
            if i.mission_id == mission_id and i.kind == "plan" and ":planner:" in i.subject_id]


def _ledger(loop, mission_id: str) -> dict[str, int]:
    events = list(loop.store.iter_events(mission_id))
    return {
        "requested": sum(1 for e in events if e.type == REQUESTED),
        "resumed": sum(1 for e in events if e.type == "PlanningServiceResumed"),
        "planner_intents": len(_planner_intents(loop, mission_id)),
    }


async def _slow_entrances(loop, mission) -> list[bool]:
    """§7.1 列出的每个"何时再规划"入口各敲一次（不跑执行器）。

    HTN 精简片 B：原专用入口"展开未细化目标"已删除，"计划里有目标还没有做法"现在
    由 ``collect_triggers`` 里的通用请求负责，所以这里少敲一个入口、不少一条路径。
    """

    current = loop.store.get_mission(mission.id)
    return [
        collect_triggers(loop, current),
        loop._resume_planning_services(current),
        await loop._retry_deferred_planning(),
    ]


def _no_claims(request):  # type: ignore[no-untyped-def]
    body = json.loads(result_envelope(request)[len("<result_envelope>"):-len("</result_envelope>")])
    body["claims"] = []
    return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"


async def _owed_repair(world) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """The leaf's result fails its check; the loop records the repair request and opens the
    Planner round for it.  The Planner's call is held: the round stays open."""
    await world.commit_seed()
    before = _ledger(world.loop, world.mission.id)
    world.provider.held.add("planner")
    await world.until(lambda: world.provider.entered.is_set())
    [request] = pending_requests(world.store, world.mission.id)
    assert request["request"]["context"]["event_type"] == "VerificationFailed"
    opened = _ledger(world.loop, world.mission.id)
    assert opened["requested"] == 1
    assert opened["planner_intents"] == before["planner_intents"] + 1
    assert opened["resumed"] == before["resumed"] + 1
    return opened


def _failing_worker():  # type: ignore[no-untyped-def]
    return scripted_worker(("workspace_write_file", {"path": OUTPUT, "content": "# 要点\n"}), _no_claims)


def test_one_structural_gap_opens_one_planner_round_across_repeated_ticks(tmp_path) -> None:
    """§7.3-3：同一修复触发反复收集、反复 tick，只有一条修复请求和一个规划轮。"""

    async def case():
        async with enabled_world(tmp_path, key="tg2b-one-gap", worker=_failing_worker()) as world:
            loop, mission = world.loop, world.mission
            opened = await _owed_repair(world)
            # 同一事实再收集三次：不再记录新请求；那一轮还开着，不再开第二轮。
            assert [collect_triggers(loop, loop.store.get_mission(mission.id)) for _ in range(3)] == [False] * 3
            for _ in range(3):
                assert not any(await _slow_entrances(loop, mission))
            for _ in range(3):
                await world.step()
            assert _ledger(loop, mission.id) == opened
            assert len(pending_requests(loop.store, mission.id)) == 1

    asyncio.run(case())


def test_a_cold_reopen_does_not_ask_the_planner_again_for_the_same_gap(tmp_path) -> None:
    """§7.3-3：进程重启（同一库冷重开、产品启动装配）后，同一缺口不再产生请求或规划轮。"""

    state: dict = {}

    async def first():
        async with enabled_world(tmp_path, key="tg2b-reopen", worker=_failing_worker()) as world:
            state["ledger"] = await _owed_repair(world)
            state["mission_id"] = world.mission.id

    async def second():
        async with product_loop(tmp_path, RoleScriptedProvider({})) as product:
            loop = product.loop
            mission = loop.store.get_mission(state["mission_id"])
            assert loop._new_mode(mission) is not None  # 真的装回了层次模式，入口不是空转
            assert len(pending_requests(loop.store, mission.id)) == 1
            for _ in range(3):
                assert not any(await _slow_entrances(loop, mission))
            assert _ledger(loop, mission.id) == state["ledger"]

    asyncio.run(first())
    asyncio.run(second())
