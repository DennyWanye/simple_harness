# SPDX-License-Identifier: Apache-2.0
"""第 2 批车道 H：H06 任务停止条件要有读方，补"连续多轮无新知识"与"结果重复率过高"两种停止。

Harness 只做计数与上限：规划轮之间知识库 / 验收记录的新增计数、结果内容哈希的重复计数。
达到上限先把事实交给规划器一次（现有停滞路径），规划器仍停在原地（这一版计划没有改动）才停。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_FULL_TARGET = Path(__file__).resolve().parent / "full_target"
for _path in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from production_fixture import enabled_world  # noqa: E402

import agent_orchestrator.orchestrator.event_handler as event_handler  # noqa: E402
from agent_orchestrator.api.missions import MissionRequestError, spec_from_request  # noqa: E402
from agent_orchestrator.contracts.models import ContractError  # noqa: E402
from agent_orchestrator.contracts.state_machines import (  # noqa: E402
    MissionStatus,
    MissionStopReason,
)
from agent_orchestrator.governance.policies import DeploymentPolicy  # noqa: E402
from agent_orchestrator.orchestrator import stop_conditions as sc  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import MissionSpec  # noqa: E402
from agent_orchestrator.orchestrator.hierarchical_dispatch import (
    append_hierarchical_event,  # noqa: E402
)
from agent_orchestrator.orchestrator.planning_repair_requests import (  # noqa: E402
    ADDRESSED,
    REQUESTED,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _event(kind: str, **payload: Any) -> Any:
    return SimpleNamespace(type=kind, payload=payload)


# ---------------------------------------------------------------- 配置：名字、阈值、默认值

def test_the_two_stop_conditions_are_on_by_default_with_deployment_thresholds() -> None:
    policy = DeploymentPolicy()
    assert "no_new_knowledge" in MissionSpec(goal="g", success_criteria=("c",), tenant_id="t",
                                             idempotency_key="k").stop_conditions
    spec = spec_from_request("t", {"goal": "g", "success_criteria": ["c"], "idempotency_key": "k"})
    parsed = sc.parse_stop_conditions(spec.stop_conditions, policy)
    assert parsed == {"no_new_knowledge": policy.no_new_knowledge_rounds,
                      "result_duplication": policy.result_duplication_rate}
    assert policy.no_new_knowledge_rounds == 5 and policy.result_duplication_rate == 0.5
    assert policy.result_duplication_min_results == 4


def test_a_mission_may_set_its_own_thresholds_or_leave_a_condition_out() -> None:
    parsed = sc.parse_stop_conditions(("verification_passed", "no_new_knowledge=3", "result_duplication=0.75"),
                                      DeploymentPolicy())
    assert parsed == {"no_new_knowledge": 3, "result_duplication": 0.75}
    assert sc.parse_stop_conditions(("verification_passed", "budget_exhausted"), DeploymentPolicy()) == {}


@pytest.mark.parametrize("bad", ("no_new_knowledge=0", "no_new_knowledge=x", "result_duplication=1.5",
                                 "result_duplication=0"))
def test_a_bad_threshold_is_refused_when_the_mission_is_created(bad: str) -> None:
    with pytest.raises(ContractError):
        sc.parse_stop_conditions((bad,), DeploymentPolicy())
    with pytest.raises(MissionRequestError):
        spec_from_request("t", {"goal": "g", "success_criteria": ["c"], "idempotency_key": "k",
                                "stop_conditions": [bad]})


def test_the_deployment_defaults_do_not_change_the_policy_bytes() -> None:
    assert "no_new_knowledge_rounds" not in DeploymentPolicy().to_json()
    custom = DeploymentPolicy(no_new_knowledge_rounds=2, result_duplication_rate=0.9,
                              result_duplication_min_results=3).to_json()
    assert (custom["no_new_knowledge_rounds"], custom["result_duplication_rate"],
            custom["result_duplication_min_results"]) == (2, 0.9, 3)


# ---------------------------------------------------------------- 计数：只数，不判语义

def test_the_streak_counts_closed_planning_rounds_with_no_new_knowledge_or_acceptance() -> None:
    commit = _event("PlanningDecisionEvaluated", status="COMMITTED")
    rejected = _event("PlanningDecisionEvaluated", status="REJECTED")
    events = [commit, _event("KnowledgeCommitted"), commit, commit, rejected, _event("AcceptanceCommitted"),
              commit, commit, commit]
    # 第 1 轮有知识 → 0；第 2 轮空 → 1；第 3 轮有验收 → 0；第 4、5 轮空 → 2；最后一轮还没关
    assert sc.knowledge_streak(events) == 2
    assert sc.knowledge_streak([commit]) == 0
    assert sc.knowledge_streak([]) == 0
    assert sc.knowledge_streak([commit, _event("KnowledgeCommitted"), commit]) == 0


def test_duplicates_are_counted_by_content_hash() -> None:
    assert sc.duplicate_count(["a", "b", "a", "a"]) == (2, 4)
    assert sc.duplicate_count([]) == (0, 0)
    assert sc.duplicate_count(["a", "b"]) == (0, 2)
    hashed = sc.result_content_hash(["h2", "h1"], "x")
    assert hashed == sc.result_content_hash(["h1", "h2"], "another summary"), "有产物时只看产物内容"
    assert sc.result_content_hash([], "same") == sc.result_content_hash([], "same")
    assert sc.result_content_hash([], "same") != sc.result_content_hash([], "other")


def test_reached_reports_each_condition_at_or_over_its_limit() -> None:
    policy = DeploymentPolicy(result_duplication_min_results=4)
    mission = SimpleNamespace(id="m", stop_conditions=("no_new_knowledge=2", "result_duplication=0.5"))
    reached = sc.reached_stop_conditions(mission, policy, streak=2, hashes=["a", "a", "b", "a"])
    assert [row["condition"] for row in reached] == ["no_new_knowledge", "result_duplication"]
    assert reached[0] == {"condition": "no_new_knowledge", "rounds_without_new_knowledge": 2, "limit": 2}
    assert reached[1] == {"condition": "result_duplication", "duplicate_results": 2, "results": 4,
                          "rate": 0.5, "limit": 0.5}
    # 结果太少不算（最少结果数来自部署政策）
    assert sc.reached_stop_conditions(mission, policy, streak=1, hashes=["a", "a"]) == []
    # 任务没配这两条就永远不达
    bare = SimpleNamespace(id="m", stop_conditions=("verification_passed",))
    assert sc.reached_stop_conditions(bare, policy, streak=99, hashes=["a"] * 10) == []
    assert sc.reason_for("no_new_knowledge") is MissionStopReason.NO_NEW_KNOWLEDGE
    assert sc.reason_for("result_duplication") is MissionStopReason.RESULT_DUPLICATION


# ---------------------------------------------------------------- 接线：先问规划器一次，停在原地才停

def test_a_reached_condition_asks_the_planner_once_and_stops_only_when_the_plan_stayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reached = [{"condition": "no_new_knowledge", "rounds_without_new_knowledge": 5, "limit": 5}]
    # 主循环自己每轮也会读一次：种子计划提交前不达，之后由用例直接调用处理函数推进
    switch = {"on": False}
    monkeypatch.setattr(sc, "reached_stop_conditions", lambda *a, **k: list(reached) if switch["on"] else [])

    async def case() -> dict[str, Any]:
        async with enabled_world(tmp_path, key="b2h-stop", hold_worker=True) as world:
            await world.commit_seed()
            loop, store, mission_id = world.loop, world.store, world.mission.id
            new_mode = loop._new_mode(store.get_mission(mission_id))
            switch["on"] = True

            first = await loop._stop_conditions_reached(store.get_mission(mission_id), new_mode)
            asked = [e for e in store.iter_events(mission_id) if e.type == REQUESTED
                     and str(e.payload["source_key"]).startswith(sc.STOP_PREFIX)]
            # 规划器还没答：等，不停，也不重复问
            second = await loop._stop_conditions_reached(store.get_mission(mission_id), new_mode)
            still_active = store.get_mission(mission_id).status
            asked_again = [e for e in store.iter_events(mission_id) if e.type == REQUESTED
                           and str(e.payload["source_key"]).startswith(sc.STOP_PREFIX)]
            # 规划器了结了这条请求、计划版本没变 → 停
            request_id = str(asked[0].payload["request_id"])
            append_hierarchical_event(store, ADDRESSED, mission_id, key="test:" + request_id,
                                      payload={"decision_id": "d-1", "decision_type": "NO_CHANGE",
                                               "status": "NO_STATE_CHANGE", "subject_key": None,
                                               "repair_request_ids": [request_id]})
            third = await loop._stop_conditions_reached(store.get_mission(mission_id), new_mode)
            mission = store.get_mission(mission_id)
            return {"first": first, "second": second, "third": third, "asked": asked,
                    "asked_again": asked_again, "still_active": still_active, "mission": mission}

    seen = asyncio.run(case())
    assert seen["first"] is True and len(seen["asked"]) == 1
    request = seen["asked"][0].payload["request"]
    assert request["context"]["reason"] == "stop_condition_reached"
    assert request["context"]["conditions"] == reached
    assert seen["second"] is False and seen["still_active"] is MissionStatus.ACTIVE
    assert len(seen["asked_again"]) == 1, "同一版计划只问一次"
    assert seen["third"] is True
    assert seen["mission"].status is MissionStatus.FAILED
    assert seen["mission"].stop_reason == "no_new_knowledge"
    detail = seen["mission"].final_report["detail"]
    assert detail["conditions"] == reached and detail["planner_asked"]["request_id"] == str(
        seen["asked"][0].payload["request_id"])


def test_nothing_reached_means_nothing_asked_and_nothing_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "reached_stop_conditions", lambda *a, **k: [])

    async def case() -> Any:
        async with enabled_world(tmp_path, key="b2h-quiet", hold_worker=True) as world:
            await world.commit_seed()
            loop, store, mission_id = world.loop, world.store, world.mission.id
            new_mode = loop._new_mode(store.get_mission(mission_id))
            progressed = await loop._stop_conditions_reached(store.get_mission(mission_id), new_mode)
            asked = [e for e in store.iter_events(mission_id) if e.type == REQUESTED]
            return progressed, asked, store.get_mission(mission_id).status

    progressed, asked, status = asyncio.run(case())
    assert progressed is False and asked == [] and status is MissionStatus.ACTIVE
