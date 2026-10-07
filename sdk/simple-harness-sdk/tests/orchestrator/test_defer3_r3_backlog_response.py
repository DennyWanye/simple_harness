# SPDX-License-Identifier: Apache-2.0
"""推后第 3 批车道 R3：H12 审阅积压时除限流外，补"加审阅并发""暂停新拆分"（原文 §18.5）。

触发只看一个计数（待审结果数）的高低水位；动作只有"多开几个审阅名额（有上限）""已有计划的任务
先不开新的规划轮（有时限）"。积压消退后两件事都恢复。不改并发上限默认值、不碰准入身份；
"合并重复候选"是语义判断，不做。
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

from agent_orchestrator.orchestrator.progress import IdleFacts, Route, idle_verdict  # noqa: E402
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.scheduling.backpressure import (  # noqa: E402
    BackpressureLimits,
    BackpressureState,
    Observation,
    backlog_response,
    evaluate,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _observe(state: BackpressureState, pending: int, at: float) -> BackpressureState:
    limits = BackpressureLimits(max_pending_verifications=4, low_watermark_ratio=0.5)
    new_state, _ = evaluate(state, Observation(running_attempts=0, pending_dispatch=0,
                                               pending_verifications=pending, observed_at=at), limits)
    return new_state


def _respond(state: BackpressureState, at: float) -> Any:
    return backlog_response(state, verifier_workers=2, verifier_ceiling=4, now=at, pause_seconds=600.0)


def test_a_backlog_raises_verifier_concurrency_to_its_ceiling_and_restores_it_after() -> None:
    """积压时动态加并发有上限、积压消退后恢复。"""
    state = BackpressureState()
    seen = []
    for at, pending in ((1.0, 1), (2.0, 5), (3.0, 9), (4.0, 3), (5.0, 2), (6.0, 0)):
        state = _observe(state, pending, at)
        response = _respond(state, at)
        seen.append((pending, response.verifier_workers, response.decomposition_paused, response.reason))
    assert seen == [
        (1, 2, False, "normal"),
        (5, 4, True, "raised"),     # 到高水位：审阅并发升到上限，暂停新拆分
        (9, 4, True, "raised"),     # 积压更大也不超过上限
        (3, 4, True, "raised"),     # 高低水位之间维持（滞回）
        (2, 2, False, "normal"),    # 回落到低水位：两件事都恢复
        (0, 2, False, "normal"),
    ]
    assert all(workers <= 4 for _, workers, _, _ in seen)


def test_the_pause_lapses_after_its_time_limit_while_the_extra_verifiers_stay() -> None:
    state = _observe(BackpressureState(), 5, 100.0)
    assert _respond(state, 100.0 + 599.0).decomposition_paused is True
    lapsed = _respond(state, 100.0 + 600.0)
    assert (lapsed.decomposition_paused, lapsed.reason, lapsed.verifier_workers) == (False, "pause_lapsed", 4)
    assert lapsed.to_json()["since"] == 100.0


def test_other_dimensions_do_not_trigger_the_backlog_responses() -> None:
    limits = BackpressureLimits(max_running_attempts=1)
    state, _ = evaluate(BackpressureState(), Observation(running_attempts=5, pending_dispatch=0,
                                                         pending_verifications=0, observed_at=1.0), limits)
    assert state.is_raised
    response = _respond(state, 1.0)
    assert (response.verifier_workers, response.decomposition_paused) == (2, False)


def test_the_defaults_and_the_admission_caps_are_unchanged(tmp_path: Path) -> None:
    config = OrchestratorConfig(evidence_root=tmp_path, model="m")
    assert (config.verifier_workers, config.max_concurrency, config.max_concurrent_model_calls) == (2, 2, 2)
    # 裁决 2026-10-07 第 6 件 B（偏离 #53）：默认上限受模型调用名额约束——
    # max(审阅数, min(2 × 审阅数, 模型名额))；桌面默认 2/2 时为 2，默认路径上审阅并发不变
    assert config.verifier_workers_ceiling == 2 and config.decomposition_pause_seconds == 600.0
    assert config.to_json()["backpressure"]["verifier_workers_ceiling"] == 2
    for slots, ceiling in ((1, 2), (3, 3), (4, 4), (8, 4)):
        assert OrchestratorConfig(evidence_root=tmp_path, model="m",
                                  max_concurrent_model_calls=slots).verifier_workers_ceiling == ceiling
    assert OrchestratorConfig(evidence_root=tmp_path, model="m",
                              verifier_workers_ceiling=5).verifier_workers_ceiling == 5  # 显式值照用
    with pytest.raises(ValueError):
        OrchestratorConfig(evidence_root=tmp_path, model="m", verifier_workers=3, verifier_workers_ceiling=2)
    from agent_orchestrator.governance.policies import SNAPSHOT_FIELDS

    assert SNAPSHOT_FIELDS["verifier_workers_ceiling"] == "include"
    assert SNAPSHOT_FIELDS["decomposition_pause_seconds"] == "include"


def test_the_pause_is_a_named_wait_never_a_stall() -> None:
    decision = idle_verdict(IdleFacts("m", backlog_paused=True, plan_has_work=True))
    assert (decision.route, decision.reason_code) == (Route.WAIT, "VERIFICATION_BACKLOG")


# ---------------------------------------------------------------- 产品同形：事件可审计，结局不变

def test_a_real_backlog_is_recorded_on_the_timeline_and_the_mission_still_completes(tmp_path: Path) -> None:
    from production_fixture import enabled_world

    async def case() -> tuple[Any, list[dict[str, Any]], dict[str, Any]]:
        # 待审上限设 1：一份结果待审就到高水位，审完回落到 0（低水位）
        # 显式给上限 4，看"升到上限、回落恢复"的整条时间线
        async with enabled_world(tmp_path, key="r3-backlog", max_pending_verifications=1,
                                 verifier_workers_ceiling=4) as world:
            await world.until(lambda: str(world.store.get_mission(world.mission.id).status) in
                              {"COMPLETED", "FAILED", "CANCELLED"}, timeout=60)
            changes = [dict(e.payload) for e in world.store.iter_events(world.mission.id)
                       if e.type == "BacklogResponseChanged"]
            return world.store.get_mission(world.mission.id), changes, \
                world.store.get_scheduler_state("backlog_response") or {}

    mission, changes, state = asyncio.run(case())
    assert str(mission.status) == "COMPLETED", mission.final_report
    assert [(c["verifier_workers"], c["decomposition_paused"], c["reason"]) for c in changes][:2] == [
        (4, True, "raised"), (2, False, "normal")]
    assert changes[0]["verifier_ceiling"] == 4 and changes[0]["base_verifier_workers"] == 2
    assert state["verifier_workers"] == 2 and state["decomposition_paused"] is False


def test_with_the_default_config_a_backlog_never_adds_verifiers_beyond_the_model_slots(tmp_path: Path) -> None:
    """默认配置（产品世界模型名额 1、审阅 2）下积压照样升起、暂停新拆分，但审阅并发不加：
    多开的审阅只会排队等名额、按回合墙钟超时，改变结局（裁决第 6 件 B）。"""
    from production_fixture import enabled_world

    async def case() -> tuple[Any, list[dict[str, Any]], int]:
        async with enabled_world(tmp_path, key="r3-backlog-default", max_pending_verifications=1) as world:
            await world.until(lambda: str(world.store.get_mission(world.mission.id).status) in
                              {"COMPLETED", "FAILED", "CANCELLED"}, timeout=60)
            changes = [dict(e.payload) for e in world.store.iter_events(world.mission.id)
                       if e.type == "BacklogResponseChanged"]
            return world.store.get_mission(world.mission.id), changes, world.loop._config.max_concurrent_model_calls

    mission, changes, slots = asyncio.run(case())
    assert str(mission.status) == "COMPLETED", mission.final_report
    assert changes and changes[0]["decomposition_paused"] is True
    assert all(c["verifier_workers"] <= max(c["base_verifier_workers"], slots) for c in changes), changes
    assert all(c["verifier_ceiling"] == c["base_verifier_workers"] for c in changes), changes


def _paused(store: Any) -> Any:
    from agent_orchestrator.scheduling.backpressure import BacklogResponse

    return BacklogResponse(base_verifier_workers=2, verifier_workers=4, verifier_ceiling=4,
                           decomposition_paused=True, reason="raised", since=store.now)


def _open(store: Any) -> Any:
    from agent_orchestrator.scheduling.backpressure import BacklogResponse

    return BacklogResponse(base_verifier_workers=2, verifier_workers=2, verifier_ceiling=4,
                           decomposition_paused=False, reason="normal", since=None)


def _ask_planner(loop: Any, mission: Any) -> None:
    from agent_orchestrator.orchestrator import planning_repair_requests as repair_requests

    new_mode = loop._new_mode(mission)
    assert repair_requests.request_planner_for_stall(
        new_mode, mission, plan_revision=int(new_mode.network(mission.id).plan_revision), detail={})


def test_while_paused_a_mission_with_its_own_queued_review_opens_no_new_planner_round(tmp_path: Path) -> None:
    """试用前第 5 步：只暂停自己有结果在排队等审的任务；人刚答了问题的那一轮照开。

    **改坏检验**：放行条件去掉"人答了问题" → 第三段 ``answered`` 为假，变红。"""
    from production_fixture import enabled_world

    async def case() -> dict[str, Any]:
        async with enabled_world(tmp_path, key="r3-pause") as world:
            never = asyncio.Event()

            async def verify_later(result_id: str) -> bool:  # 审阅一直轮不到：结果留在队里
                await never.wait()
                return False

            world.loop._verify = verify_later  # type: ignore[method-assign]
            await world.until(lambda: world.store.has_unverified_results(world.mission.id), timeout=60)
            loop, store = world.loop, world.store
            mission = store.get_mission(world.mission.id)
            _ask_planner(loop, mission)
            loop._backlog = _paused(store)
            held = loop._resume_planning_services(mission)
            in_flight_while_paused = loop._planner_intents_in_flight(mission.id)
            facts = loop._idle_facts(mission, read_plan=False)
            with store.transaction():  # 人答了一个问题（回答接口写的就是这条回执）
                loop.commit._emit("PlanningHumanAnswered", mission.id, key=f"{mission.id}:answered-test",
                                  payload={"decision_id": "decision-answered-test"})
            answered = loop._resume_planning_services(mission)
            return {"held": held, "in_flight": in_flight_while_paused, "facts": facts,
                    "answered": answered, "in_flight_after": loop._planner_intents_in_flight(mission.id)}

    seen = asyncio.run(case())
    assert seen["held"] is False and seen["in_flight"] is False
    assert seen["facts"] is not None and seen["facts"][0].backlog_paused is True
    assert seen["answered"] is True and seen["in_flight_after"] is True


def test_while_paused_a_mission_with_nothing_queued_still_plans(tmp_path: Path) -> None:
    """试用前第 5 步：别的任务的积压不拖住没往队里加东西的任务。

    **改坏检验**：``_decomposition_paused`` 只看全局开关 → ``resumed`` 为假，变红。"""
    from production_fixture import enabled_world

    async def case() -> dict[str, Any]:
        async with enabled_world(tmp_path, key="r3-pause-other", hold_worker=True) as world:
            await world.commit_seed()
            loop, store = world.loop, world.store
            mission = store.get_mission(world.mission.id)
            assert not store.has_unverified_results(mission.id)
            _ask_planner(loop, mission)
            loop._backlog = _paused(store)
            resumed = loop._resume_planning_services(mission)
            facts = loop._idle_facts(mission, read_plan=False)
            loop._backlog = _open(store)
            return {"resumed": resumed, "in_flight": loop._planner_intents_in_flight(mission.id), "facts": facts}

    seen = asyncio.run(case())
    assert seen["resumed"] is True and seen["in_flight"] is True
    assert seen["facts"] is None or seen["facts"][0].backlog_paused is False
