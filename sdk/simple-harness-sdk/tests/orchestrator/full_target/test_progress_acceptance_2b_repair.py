# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""NEXT-TG-1.0 第二批 B 验收补测（§7.3 第 3、6 条）。

* 第 3 条：明确的结构缺口（叶子没过检查 → 原修复触发）只产生一次原 Planner 请求；重复
  收集触发、反复 tick、冷重开都不重复产生请求或规划轮。世界是产品同形部署
  （``taskgraph_exec.production_fixture``）：执行者真交了没写结论的结果，修复请求与规划轮由
  主循环真开出来；规划器的那次调用被扣住（一次很慢的模型调用），轮次停在"已开出"。
* 第 6 条：动作结果未知只核对不重发；把一笔未知费用按上限结清，不改变该动作 / 效果
  状态，也不触发重发。世界复用 ``assurance_exec/_operation_world.OperationWorld``
  （分诊表：随对外操作族并入代表用例 3 的变体，那一批改写）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE / "assurance_exec", _HERE / "operation_completion", _HERE / "taskgraph_exec"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from _operation_world import OperationWorld  # noqa: E402
from production_fixture import OUTPUT, enabled_world, product_loop, result_envelope, scripted_worker  # noqa: E402
from test_completion_contract import _effect_state  # noqa: E402

from agent_orchestrator.governance.budgets import UsageFact  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import mission_account  # noqa: E402
from agent_orchestrator.orchestrator.completion_status import (  # noqa: E402
    read_occurrence_completion,
)
from agent_orchestrator.orchestrator.planning_repair_requests import (  # noqa: E402
    REQUESTED,
    collect_triggers,
    pending_requests,
)
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector  # noqa: E402
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


# ======================================================================================
# §7.3 第 6 条：结果未知只核对；费用按上限结清不改变效果状态
# ======================================================================================


def _unknown_publish(tmp_path) -> tuple[OperationWorld, dict]:
    """服务端已发布但回执丢失：动作 UNKNOWN，效果待核对。"""

    w = OperationWorld(tmp_path)
    w.produce_and_accept()
    w.submit("deliver-report")
    w.materialize("deliver-report")
    real_execute = FilePublishConnector.execute

    def applied_but_lost(self, *args, **kwargs):
        real_execute(self, *args, **kwargs)
        raise RuntimeError("reply lost after the service applied it")

    with patch.object(FilePublishConnector, "execute", applied_but_lost):
        assert w.dispatch()
    action = w.store.get_action(w.actions["deliver-report"]["action_key"])
    assert action["state"] == "UNKNOWN" and not action.get("receipt")
    assert _effect_state(w) == "RECONCILIATION_REQUIRED"
    return w, action


def test_settling_an_unknown_charge_at_its_upper_bound_leaves_the_unknown_action_alone(tmp_path) -> None:
    """§7.3-6：未知费用按上限结清后，UNKNOWN 动作 / 待核对效果不变、不重发，之后仍只靠核对收敛。"""

    w, action = _unknown_publish(tmp_path)
    ledger = w.commit.ledger
    subject = "charge:" + str(action["action_key"])
    with w.store.transaction():
        ledger.reserve(account_id=mission_account(w.mission_id), subject_id=subject,
                       tokens=4_000, cost_micros=0, counts_attempt=False)
        ledger.import_usage(subject_id=subject, mission_id=w.mission_id, facts=[
            UsageFact("call-known", 1_000, 200, 0), UsageFact("call-lost", 0, 0, 0, unknown=True)])
    before = dict(w.store.get_action(action["action_key"]))
    with w.store.transaction():
        settled = ledger.settle_at_upper_bound(subject_id=subject)
    assert settled["state"] == "SETTLED" and settled["settled_tokens"] == 4_000
    assert ledger.usage_flags(w.mission_id)["usage_fully_known"] is False  # 费用事实仍是未知
    after = dict(w.store.get_action(action["action_key"]))
    assert after["state"] == "UNKNOWN" and not after.get("receipt")
    assert {k: after[k] for k in ("state", "receipt", "idempotency_key")} == {
        k: before[k] for k in ("state", "receipt", "idempotency_key")}
    assert _effect_state(w) == "RECONCILIATION_REQUIRED"
    assert not read_occurrence_completion(w.store, w.mission_id, w.occurrence).complete
    # 结清不是"已完成"也不是"未发生"：不重发，服务端只有那一次发布。
    assert not w.dispatch() and len(w.published_files()) == 1
    account = ledger.account(mission_account(w.mission_id))
    booked = (account.settled_tokens, account.reserved_tokens)
    # 之后唯一的收敛路径仍是核对：读到服务端事实后记为成功，且依旧没有第二次发送。
    assert asyncio.run(w.executor.reconcile(w.mission_id))
    assert w.store.get_action(action["action_key"])["state"] == "SUCCEEDED"
    assert len(w.published_files()) == 1
    # 核对成功也不回头改写已结清的费用（不退、不重复计）。
    account = ledger.account(mission_account(w.mission_id))
    assert (account.settled_tokens, account.reserved_tokens) == booked
    assert ledger.usage_flags(w.mission_id)["budget_conserved"] is True
