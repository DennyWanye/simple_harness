# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3r: every Mission terminal path keeps honest books.

Grok H-L3-C1-r0 (fourth batch, N9): the wall clock failed the Mission while a Worker
verify attempt sat on an after-handoff UNKNOWN grant, and the books were left
inconsistent (the Host runner set ``budget_conserved=false``).  The honest accounting:
an unknown call is never written as a 0-token charge, its reservation is never lost,
and ``remaining + reserved + settled == pool``.  ``usage_fully_known`` ("every call's
tokens are known") is a different fact from budget conservation.

HTN 补齐阶段 A′（2026-10-03）换芯到产品同形世界。保证通道的记账口径（用量宁多算不少算，
2026-09-24）：没走完的任务把结果不明的调用的预留**留着**（可见、计数），不再释放；走完的
任务按上限结清。各终止路径的断言见 :func:`_unknown_outcome_world.assert_terminal_ledger`。
偏离（已记入迁移报告）：
* 墙钟停止那条删除：产品建任务入口不开放 ``max_runtime_seconds``，整任务墙钟在产品路径上
  走不到；它顺带钉的"诊断拆包装异常"（N11）并入
  ``test_after_handoff_unknown_bounded.py::test_consecutive_after_handoff_unknowns_stop_when_the_ladder_is_spent``。
* "交接后准入拒绝"那条改成产品真会走到的预算用尽：交接后由提供方抛出准入拒绝只在评测计量
  提供方里出现，产品没有这种外界事件。
* "运行环境不可用时两个标志各说各的"一条并入下面的参数化用例（``runtime_unavailable`` 档）。
"""

from __future__ import annotations

import asyncio
import inspect
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
    settle,
    status,
    transport_loss,
)

from agent_orchestrator.testing.product_world import product_world  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def test_every_mission_terminal_writer_goes_through_the_ledger_hook() -> None:
    """One choke point: fail_mission / fail_planning / stop_task / cancel_mission."""

    from agent_orchestrator.orchestrator import event_handler

    source = inspect.getsource(event_handler.Orchestrator)
    assert source.count("self.commit.fail_mission(") == 1, (
        "every fail_mission site must go through _commit_fail_mission"
    )
    assert source.count("self.commit.fail_planning(") == 1, (
        "every fail_planning site must go through _commit_fail_planning"
    )
    assert source.count("self.commit.stop_task(") == 1, (
        "every stop_task site must go through _commit_stop_task"
    )
    assert source.count("self.commit.cancel_mission(") == 1, (
        "every cancel_mission site must go through _commit_cancel_mission"
    )
    assert "def _prepare_terminal_ledger" in source


def _unreadable(request: Any) -> str:
    del request
    return "not a proposal"


# path → (status, stop reason, unknown usage on the books, scripted faults, planner, config, budget)
PATHS = {
    "planning_failed": ("FAILED", "planning_failed", False, {}, _unreadable, {"max_planning_attempts": 1}, {}),
    "budget_exhausted": ("FAILED", "budget_exhausted", False, {}, None, {}, {"max_tokens": 30_000}),
    "runtime_unavailable": ("FAILED", "runtime_unavailable", True, {"planner": [transport_loss]}, None,
                            {"max_planning_attempts": 1}, {}),
}


@pytest.mark.parametrize("path", list(PATHS))
def test_a_terminal_path_keeps_the_books(tmp_path, path) -> None:
    expected, stop_reason, unknown, faults, planner, config, budget = PATHS[path]

    async def case() -> dict[str, Any]:
        provider = FaultyProvider(faults, **({"planner": planner} if planner else {}))
        async with product_world(tmp_path / "root", provider, **{**CONFIG, **config}) as world:
            mission_id = create(world, "p23r-" + path, **budget)
            assert await settle(world, mission_id, seconds=15)
            final = world.store.get_mission(mission_id)
            return {
                "status": status(world, mission_id),
                "stop_reason": final.stop_reason,
                "report": dict(final.final_report or {}),
                "types": [item.type for item in events(world, mission_id)],
                "open": [item.subject_id for item in world.store.list_intents(*OPEN) if item.mission_id == mission_id],
                "ledger": assert_terminal_ledger(world, mission_id, unknown=unknown),
            }

    outcome = asyncio.run(case())
    assert outcome["status"] == expected, outcome["types"]
    assert outcome["stop_reason"] == stop_reason, outcome["report"]
    assert "MissionFailed" in outcome["types"]
    assert outcome["open"] == []
    if path == "budget_exhausted":
        assert outcome["report"]["detail"]["dimension"] == "tokens", outcome["report"]
        assert outcome["ledger"]["reserved"] == 0 and outcome["ledger"]["held_reservations"] == []


def test_cancel_mission_writes_usage_flags_on_a_hierarchical_report(tmp_path) -> None:
    """P2-1: cancel is a terminal path; the final report carries the two ledger flags."""

    async def case() -> dict[str, Any]:
        async with product_world(tmp_path / "root", FaultyProvider(), **CONFIG) as world:
            mission_id = create(world, "p23q-cancel-flags")
            receipt = world.control.cancel(mission_id)
            final = world.store.get_mission(mission_id)
            return {
                "receipt": receipt,
                "status": status(world, mission_id),
                "report": dict(final.final_report or {}),
                "costs": world.loop.commit.ledger.costs_report(mission_id),
                "types": [item.type for item in events(world, mission_id)],
            }

    outcome = asyncio.run(case())
    assert outcome["receipt"]["status"] == "CANCELLED" and outcome["status"] == "CANCELLED"
    assert "MissionCancelled" in outcome["types"]
    assert outcome["report"]["usage_fully_known"] is True
    assert outcome["report"]["budget_conserved"] is True
    assert outcome["costs"]["usage_fully_known"] is True
    assert outcome["costs"]["budget_conserved"] is True


def test_only_the_final_writer_writes_completed() -> None:
    """"完成"只有一个写方（Assurance 原计划 §7.1）：全包里把任务状态写成 COMPLETED 的只有收尾的
    唯一完成写方；判定只记"要求已满足"并请求收尾。

    **改坏检验**：判定里再加一条直接写完成的路（FIN-01）→ 变红。"""
    import re
    from pathlib import Path

    import agent_orchestrator

    root = Path(agent_orchestrator.__file__).parent
    writers = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        for _ in re.finditer(r"next_mission\(\s*[^()]*?MissionStatus\.COMPLETED", path.read_text(encoding="utf-8"))
    )
    assert writers == ["orchestrator/assurance_final_writer.py"]
