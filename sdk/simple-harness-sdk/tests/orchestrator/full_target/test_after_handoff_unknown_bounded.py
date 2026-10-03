# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3p: consecutive after-handoff 0-token UNKNOWNs are bounded.

Grok H-L3-C2-r0/r1 (third batch): planner:1 and the synthesizer succeeded, then every
later planner invoke settled ``unknown`` / ``provider_error_after_handoff`` with 0 tokens,
and the loop ran until the wall clock — r0 stopped in PLANNING with ``stop_reason=null``,
r1's Worker leaf sat ``blocked=true`` until 1800 s.

HTN 补齐阶段 A′（2026-10-03）换芯到产品同形世界，只有模型回复是脚本。产品上界限的来源
与此前不同（偏离已记入迁移报告）：

* 规划回合：每一轮"结果不明"等满界限后按 ``provider_outcome_unknown`` 被拒，计入规划次数；
  次数用完（``max_planning_attempts``）以 ``runtime_unavailable`` 具名停止，不再开新一轮。
  原来那个"连续 N=2 次就停"的独立计数在产品路径上已没有计数方（只被清零），由规划次数兜住。
* 执行者尝试：从不再交接；等满界限按丢失处理，交给重做；同一步"非模型原因失败"到上限
  （2026-09-28 用户定，按每步设上限）以 ``runtime_unavailable`` 具名停止。
* 每一次结果不明的调用都留在账上：授权 UNKNOWN、预留按上限挂着计数，诊断里只有错误类名
  与 HTTP 状态（包装异常拆到底层），不带回复正文与密钥。

一条 UNKNOWN 之后照常走完（P2.3p 的 N-1 档）由
``test_service_intent_provider_blocker.py::test_a_planner_round_on_an_unknown_outcome_ends_after_the_bound_and_the_next_round_answers``
覆盖；原"常量核对"一条删除——``MAX_SERVICE_REHANDOFFS`` / ``MAX_CONSECUTIVE_AFTER_HANDOFF_UNKNOWNS``
在产品路径上已是孤儿。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _unknown_outcome_world import (  # noqa: E402
    CONFIG,
    OPEN,
    FaultyProvider,
    assert_terminal_ledger,
    create,
    events,
    http_loss,
    invocations,
    plan_intents,
    settle,
    status,
    transport_loss,
    unclassified,
)

from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from simple_harness.providers.errors import ProviderTransportError  # noqa: E402


class _ProviderAdapterError(RuntimeError):
    """A provider adapter's own wrapper around the transport error it caught."""


def _wrapped(status_code: int):
    def fault(request: Any) -> None:
        del request
        try:
            raise ProviderTransportError(public_message="scripted transport loss after handoff",
                                         status_code=status_code)
        except ProviderTransportError as error:
            raise _ProviderAdapterError("physical call failed; usage unknown") from error

    return fault


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_consecutive_after_handoff_unknowns_stop_when_the_ladder_is_spent(tmp_path) -> None:
    """Three planner rounds in a row end on an unknown outcome (three different kinds of
    after-handoff failure).  ``max_planning_attempts=3``: the Mission is FAILED /
    ``runtime_unavailable`` with ``MissionFailed``, no fourth round is opened, nothing is
    left in flight, and every lost call is on the books — never in PLANNING with
    ``stop_reason=null``.  N11: the diagnostics unwrap a wrapper to the transport class and
    HTTP status, and never carry the reply text."""

    async def case() -> dict[str, Any]:
        provider = FaultyProvider({"planner": [unclassified, http_loss(418), _wrapped(503), transport_loss]})
        async with product_world(tmp_path / "root", provider, **{**CONFIG, "max_planning_attempts": 3}) as world:
            mission_id = create(world, "p23p-ladder-stop")
            assert await settle(world, mission_id, seconds=15)
            final = world.store.get_mission(mission_id)
            rounds = sorted(plan_intents(world, mission_id), key=lambda item: item.created_at)
            return {
                "status": status(world, mission_id),
                "stop_reason": final.stop_reason,
                "report": dict(final.final_report or {}),
                "types": [item.type for item in events(world, mission_id)],
                "rejected": [item.payload["reason"] for item in events(world, mission_id, "PlanningRejected")],
                "rounds": [item.subject_id.rpartition(":")[2] for item in rounds],
                "diagnostics": [invocations(world, item) for item in rounds],
                "planner_calls": provider.role_calls["planner"],
                "open": [item.subject_id for item in world.store.list_intents(*OPEN) if item.mission_id == mission_id],
                "ledger": assert_terminal_ledger(world, mission_id, unknown=True),
            }

    outcome = asyncio.run(case())
    assert outcome["status"] == "FAILED", outcome["types"]
    assert outcome["stop_reason"] == "runtime_unavailable", outcome["report"]
    assert outcome["report"]["planning_failure"]["reason"] == "provider_outcome_unknown"
    assert "MissionFailed" in outcome["types"]
    assert outcome["rejected"] == ["provider_outcome_unknown"] * 3
    assert outcome["planner_calls"] == 3 and outcome["rounds"] == ["1", "2", "3"], outcome["rounds"]
    assert outcome["open"] == []
    assert len(outcome["ledger"]["held_reservations"]) == 3
    # the runtime_unavailable report: usage is not fully known, the budget is conserved
    assert outcome["report"]["usage_fully_known"] is False and outcome["report"]["budget_conserved"] is True

    seen = []
    for calls in outcome["diagnostics"]:
        [call] = calls
        assert call["state"] == "unknown" and call["error_code"] == "provider_error_after_handoff", call
        usage = call["usage"]
        seen.append((usage.get("error_class"), usage.get("http_status"), usage.get("wrapper_class")))
        dumped = str(usage)
        assert "scripted transport loss" not in dumped and "scripted unclassified" not in dumped
        assert "Authorization" not in dumped and "api_key" not in dumped
    assert seen == [("UnknownAfterHandoff", None, None), ("ProviderTransportError", 418, None),
                    ("ProviderTransportError", 503, "_ProviderAdapterError")], seen


def test_a_worker_step_whose_calls_keep_ending_unknown_stops_as_runtime_unavailable(tmp_path) -> None:
    """Worker attempts are never re-handed off.  They also must not sit until 1800 s: each
    attempt blocked on an unknown outcome ends LOST after the bound, the step is done
    again, and when the step's non-model failures reach their cap the Mission stops as
    ``runtime_unavailable`` — every lost attempt's reservation held and counted."""

    async def case() -> dict[str, Any]:
        provider = FaultyProvider({"worker": [transport_loss] * 12})
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            mission_id = create(world, "p23p-worker")
            assert await settle(world, mission_id, seconds=30)
            final = world.store.get_mission(mission_id)
            attempts = [item for item in world.store.list_intents("SETTLED", "FAILED", *OPEN)
                        if item.mission_id == mission_id and item.kind == "attempt"]
            return {
                "status": status(world, mission_id),
                "stop_reason": final.stop_reason,
                "report": dict(final.final_report or {}),
                "types": [item.type for item in events(world, mission_id)],
                "lost": [item.payload.get("reason") for item in events(world, mission_id, "AttemptLost")],
                "attempts": [(item.state, [call["state"] for call in invocations(world, item)]) for item in attempts],
                "worker_calls": provider.role_calls["worker"],
                "open": [item.subject_id for item in world.store.list_intents(*OPEN) if item.mission_id == mission_id],
                "ledger": assert_terminal_ledger(world, mission_id, unknown=True),
            }

    outcome = asyncio.run(case())
    assert outcome["status"] == "FAILED", outcome["types"][-15:]
    assert outcome["stop_reason"] == "runtime_unavailable", outcome["report"]
    cap = outcome["report"]["detail"]["cap"]
    assert outcome["report"]["detail"]["reason"] == "non_model_failures_exhausted"
    assert "MissionFailed" in outcome["types"]
    assert outcome["lost"] == ["provider_outcome_unknown"] * cap, outcome["lost"]
    assert outcome["worker_calls"] == cap and outcome["attempts"] == [("FAILED", ["unknown"])] * cap
    assert outcome["open"] == []
    assert len(outcome["ledger"]["held_reservations"]) == cap
