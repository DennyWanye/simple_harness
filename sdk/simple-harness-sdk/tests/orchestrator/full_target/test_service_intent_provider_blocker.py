# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3f: a service turn waiting on an unknown Provider outcome does not wait for ever.

Found by the P2.3e probe episode (H-L3-C1 on the Grok lane): a Planner round was handed
off, the transport failed 0.2 s later, the invocation was — correctly — settled
``UNKNOWN`` (the request may have reached the model), and then nothing happened until
the runner's 1800 s deadline.

HTN 补齐阶段 A′（2026-10-03）换芯到产品同形世界（产品部署组装、执行图建任务时绑定、
保证通道、原生执行池、提供方用量守卫；部署职责在两轮之间代签授权），只有模型回复是脚本。
保证通道上的答案与此前非保证通道不同（偏离已记入迁移报告）：

* **规划回合**：不再"换一个执行者再问一次"（``ServiceIntentRehandedOff`` 在产品上没有写入
  方）——重发会对同一个问题第二次计费。等满 ``min(stall_seconds, 30)`` 后这一轮按
  ``provider_outcome_unknown`` 被拒，交给规划次数决定：还有次数就开下一轮，没有就以
  ``runtime_unavailable`` 具名停止（从没听到规划器，不算"规划失败"）。
* **审阅**：保留原执行者，不重发；记一张"原调用待对账"的回执。内容审阅那一轮由验收的
  "没答上来"出口收掉（验收失败 → 重做），任务照常走完。
* 被放弃的调用在运行时账本里是 UNKNOWN，从不按 0 记账；它的预留留着、按上限计数。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _unknown_outcome_world import (  # noqa: E402
    CONFIG,
    LIMIT,
    OPEN,
    FaultyProvider,
    assert_terminal_ledger,
    create,
    events,
    grants,
    http_loss,
    invocations,
    plan_intents,
    settle,
    status,
    transport_loss,
)

from agent_orchestrator.orchestrator.commit_service import SERVICE_INTENT_REHANDED_OFF  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import (  # noqa: E402
    MAX_SERVICE_BLOCKER_SECONDS,
    Orchestrator,
)
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import REVIEWER  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ======================================================================================
# 1. the Planner: the round ends after the bound, the ladder asks again
# ======================================================================================


def test_a_planner_round_on_an_unknown_outcome_ends_after_the_bound_and_the_next_round_answers(tmp_path) -> None:
    """The red test for the wait.  Before P2.3f the round stayed SUBMITTED for ever.

    Also the P2.3l / N5 property (an UNKNOWN grant must not lock the next hand-off): the
    next round's executor is admitted by the Provider budget guard and answers, and the
    Mission completes (P2.3p: one such UNKNOWN still takes the ordinary road)."""

    async def case() -> dict[str, Any]:
        provider = FaultyProvider({"planner": [http_loss(418)]})
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            mission_id = create(world, "p23f-planner-unknown")
            assert await settle(world, mission_id, seconds=20)
            rounds = {item.subject_id.rpartition(":")[2]: item for item in plan_intents(world, mission_id)}
            return {
                "status": status(world, mission_id),
                "types": [item.type for item in events(world, mission_id)],
                "rejected": [dict(item.payload) for item in events(world, mission_id, "PlanningRejected")],
                "first": rounds["1"].state,
                "first_calls": invocations(world, rounds["1"]),
                "second_calls": invocations(world, rounds["2"]),
                "grants": {row["subject_id"].rpartition(":")[2]: row["state"] for row in grants(world)
                           if ":planner:" in row["subject_id"]},
                "planner_calls": provider.role_calls["planner"],
                "ledger": assert_terminal_ledger(world, mission_id, unknown=True),
            }

    outcome = asyncio.run(case())
    assert outcome["status"] == "COMPLETED", outcome["types"][-15:]
    # no second executor is ever asked the same question
    assert SERVICE_INTENT_REHANDED_OFF not in outcome["types"]
    [record] = outcome["rejected"]
    assert record["reason"] == "provider_outcome_unknown" and record["ordinal"] == 1
    detail = record["detail"]
    assert detail["rehandoffs"] == 0 and detail["assurance_lane"] is True
    assert detail["limit_seconds"] == LIMIT and detail["waited_seconds"] >= LIMIT
    assert detail["blocker"]["kind"] == "provider"
    assert outcome["first"] == "FAILED"
    # honest accounting: the abandoned call is UNKNOWN in the runtime ledger …
    assert [(item["state"], item["error_code"]) for item in outcome["first_calls"]] == [
        ("unknown", "provider_error_after_handoff")]
    assert [item["state"] for item in outcome["second_calls"]] == ["succeeded"]
    # … and its grant stays UNKNOWN; the next round's grant was admitted and settled
    assert outcome["grants"]["1"] == "UNKNOWN" and outcome["grants"]["2"] == "SETTLED"
    # the failed call, the proposal, and the adoption after the method's review
    assert outcome["planner_calls"] == 3
    assert "PlanRevisionCommitted" in outcome["types"] and "MissionFailed" not in outcome["types"]


def test_an_unknown_outcome_with_no_rung_left_stops_as_runtime_unavailable(tmp_path) -> None:
    """``max_planning_attempts=1``: no next rung, so the Mission ends — named, decided.

    P2.3l: ``planning_failed`` would be a lie (the Planner was never heard);
    ``runtime_unavailable`` is the stop for a model service that did not answer.  The
    call is asked exactly once; the books keep it as unknown, its reservation held."""

    async def case() -> dict[str, Any]:
        provider = FaultyProvider({"planner": [transport_loss]})
        async with product_world(tmp_path / "root", provider, **{**CONFIG, "max_planning_attempts": 1}) as world:
            mission_id = create(world, "p23f-planner-no-rung")
            assert await settle(world, mission_id, seconds=10)
            final = world.store.get_mission(mission_id)
            subject = f"{mission_id}:planner:1"
            return {
                "status": status(world, mission_id),
                "stop_reason": final.stop_reason,
                "report": dict(final.final_report or {}),
                "types": [item.type for item in events(world, mission_id)],
                "planner_calls": provider.role_calls.get("planner", 0),
                "open": [item.subject_id for item in world.store.list_intents(*OPEN)
                         if item.mission_id == mission_id],
                "known": world.loop.commit.ledger.known_usage_for(subject)[0],
                "unknown": world.loop.commit.ledger.has_unknown_usage(subject),
                "ledger": assert_terminal_ledger(world, mission_id, unknown=True),
            }

    outcome = asyncio.run(case())
    assert outcome["status"] == "FAILED", outcome["types"]
    assert outcome["stop_reason"] == "runtime_unavailable", outcome["report"]
    assert outcome["report"]["planning_failure"]["reason"] == "provider_outcome_unknown"
    assert "MissionFailed" in outcome["types"]
    assert outcome["planner_calls"] == 1, "never re-sent"
    assert outcome["open"] == []
    # P1-1: giving up is not permission to write the call as 0 tokens
    assert outcome["known"] == 0 and outcome["unknown"] is True
    [held] = outcome["ledger"]["held_reservations"]
    assert held["subject_id"].endswith(":planner:1") and int(held["reserved_tokens"]) > 0


# ======================================================================================
# 2. the reviewer (the assured lane's review calls; scripted under the "unknown" role)
# ======================================================================================


def test_a_content_review_on_an_unknown_outcome_ends_through_the_verification_door(tmp_path) -> None:
    """The second review call (the step's content review) is lost after hand-off.  The
    call is not re-sent; the round is handed back to the acceptance's own "did not
    answer" door (the verification fails, the step is done again), and the Mission
    completes with the lost call kept as unknown and settled at its upper bound."""

    async def case() -> dict[str, Any]:
        provider = FaultyProvider({REVIEWER: [None, transport_loss]})
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            mission_id = create(world, "p23f-content-review")
            assert await settle(world, mission_id, seconds=25)
            reviews = [item for item in world.store.list_intents("SUBMITTED", "SETTLED", "FAILED")
                       if item.mission_id == mission_id and item.kind == "critic"]
            return {
                "status": status(world, mission_id),
                "types": [item.type for item in events(world, mission_id)],
                "reviews": [(item.state, [call["state"] for call in invocations(world, item)]) for item in reviews],
                "ledger": assert_terminal_ledger(world, mission_id, unknown=True),
            }

    outcome = asyncio.run(case())
    assert outcome["status"] == "COMPLETED", outcome["types"][-15:]
    assert SERVICE_INTENT_REHANDED_OFF not in outcome["types"]
    # the lost review kept its one call and was closed as interrupted once past the service
    # bound (卡住裁决第 6 类); a new round reviewed the redo
    assert outcome["reviews"][0] == ("FAILED", ["unknown"]), outcome["reviews"]
    assert ("SETTLED", ["succeeded"]) in outcome["reviews"][1:], outcome["reviews"]
    assert "VerificationFailed" in outcome["types"] and outcome["types"].count("AttemptCreated") == 2


def _method_reviews(world: Any, mission_id: str) -> list[Any]:
    return [item for item in world.store.list_intents("SUBMITTED", "SETTLED", "FAILED")
            if item.mission_id == mission_id and ":assurance-method-plan:" in item.subject_id]


def test_a_method_review_on_an_unknown_outcome_is_reopened_in_a_new_session(tmp_path) -> None:
    """卡住裁决第 6 类：做法审阅的第一次调用交接后丢了（结果不明）。等过服务时限，原意图按
    "被打断"收口（不扣次数、不重发原调用），新会话重开第二次，拿到结论，任务继续到完成。

    **改坏检验**（收尾裁决）：审阅那一支改回"保留原调用一直等" → 任务停在规划中 → 变红。"""

    async def case() -> dict[str, Any]:
        provider = FaultyProvider({REVIEWER: [transport_loss]})
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            mission_id = create(world, "p23f-method-review")
            assert await settle(world, mission_id, seconds=40)
            return {
                "status": status(world, mission_id),
                "types": [item.type for item in events(world, mission_id)],
                "reviews": [(item.state, [call["state"] for call in invocations(world, item)])
                            for item in _method_reviews(world, mission_id)],
                "reviewed": [dict(item.payload) for item in events(world, mission_id, "PlanningMethodReviewed")],
            }

    outcome = asyncio.run(case())
    assert outcome["status"] == "COMPLETED", outcome["types"][-15:]
    assert SERVICE_INTENT_REHANDED_OFF not in outcome["types"], "the lost call itself is never re-sent"
    first, second = outcome["reviews"][:2]
    assert first == ("FAILED", ["unknown"]), outcome["reviews"]
    assert second == ("SETTLED", ["succeeded"]), outcome["reviews"]
    assert [item["outcome"] for item in outcome["reviewed"]] == ["PASSED"], outcome["reviewed"]


def test_a_method_review_that_never_answers_is_reported_as_no_verdict(tmp_path) -> None:
    """做法审阅的两次调用都没回来：记"没有结论"，原因如实写"审阅调用没拿到回复"，交给规划器
    （不说成审阅员判不了）。"""
    from agent_orchestrator.testing.scripted_replies import review_input

    class MethodReviewLost(FaultyProvider):
        async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
            data = review_input(request)
            if data is not None and str((data.get("package") or {}).get("purpose")) == "METHOD_PLAN":
                self.faults.setdefault(REVIEWER, []).insert(0, transport_loss)
            return await super().invoke(request, cancel=cancel)

    async def case() -> dict[str, Any]:
        async with product_world(tmp_path / "root", MethodReviewLost(), **CONFIG) as world:
            mission_id = create(world, "p23f-method-review-never")
            reviewed = lambda: events(world, mission_id, "PlanningMethodReviewed")  # noqa: E731
            assert await settle(world, mission_id, seconds=40, done=lambda: bool(reviewed()))
            return {"reviewed": [dict(item.payload) for item in reviewed()],
                    "reviews": [(item.state, [call["state"] for call in invocations(world, item)])
                                for item in _method_reviews(world, mission_id)]}

    outcome = asyncio.run(case())
    first = outcome["reviewed"][0]
    assert first["outcome"] == "NO_VERDICT" and first["record_id"] is None, first
    assert "got no reply 2 time(s)" in first["reason"], first
    assert outcome["reviews"][:2] == [("FAILED", ["unknown"]), ("FAILED", ["unknown"])], outcome["reviews"]


# ======================================================================================
# 3. the bound
# ======================================================================================


def test_the_bound_is_the_smaller_of_stall_seconds_and_the_ceiling(tmp_path) -> None:
    # 用户 2026-10-02：上限 300 秒改 30 秒（重启打断一次调用后不再原地等三分钟）。
    assert MAX_SERVICE_BLOCKER_SECONDS == 30.0

    def bound(stall: float) -> float:
        config = OrchestratorConfig(evidence_root=Path(tmp_path), stall_seconds=stall)
        return Orchestrator._service_blocker_limit.fget(SimpleNamespace(_config=config))

    assert bound(12.0) == 12.0
    assert bound(180.0) == 30.0  # 产品默认的 180 秒不再是这里的界
