# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3e: ``run()`` must not leave while a planning-class intent is still in flight.

The Grok acceptance rerun (H-L3-C1) found it: a planning round was submitted, the loop
reported an idempotent "intent already exists" as **progress** on every cycle, and a
progressing cycle neither sleeps nor yields — ``run()`` spun through its ``max_cycles``
budget in milliseconds with the runtime's turn tasks starved the whole time, then left
with the turn submitted and nobody to collect it.  The Mission stayed at PLANNING with
no settlement and no stop.

Asserted here on the product's deployment (HTN 补齐阶段 A′：产品同形世界，建任务即绑定执行图、
保证通道、部署职责在两轮之间代签授权；只有模型回复是脚本）: ``run()`` stays until a
submitted planning intent is collected — however long the model takes (the Planner's
call is held, standing in for a sixty-second model call) — and the budget bounds *work*,
not waiting.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

OPEN_INTENT_STATES = ("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _open_plan_intents(loop: Any, mission_id: str) -> list[str]:
    return sorted(item.subject_id for item in loop.store.list_intents(*OPEN_INTENT_STATES)
                  if item.kind == "plan" and item.mission_id == mission_id)


def test_run_waits_for_an_inflight_planning_intent_however_slow_the_model_is(tmp_path) -> None:
    """``max_cycles`` is small on purpose: on a loop that counts waiting as progress the
    budget is spent in milliseconds and ``run()`` returns with the intent still SUBMITTED."""

    provider = LayeredScriptedProvider()
    provider.held.add("planner")

    async def case() -> dict[str, Any]:
        async with product_world(tmp_path / "root", provider, max_concurrency=3, test_timeout_seconds=60,
                                 max_planning_attempts=2) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md，列出三条要点", "idempotency_key": "p23e-run-exit-held",
                                       "success_criteria": ["file:NOTES.md"]})["mission_id"]
            loop = world.loop
            # the loop itself opens the first round, the deployment grants it between cycles
            running = asyncio.create_task(loop.run(max_cycles=120))
            await asyncio.wait_for(provider.entered.wait(), 10)
            # Long enough for a busy loop to burn 120 cycles many times over, short
            # enough that a waiting loop has only polled.
            await asyncio.sleep(1.0)
            held = {
                "returned": running.done(),
                "open": _open_plan_intents(loop, mission_id),
                "calls_started": provider.asked.count("planner"),
            }
            provider.held.clear()
            provider.release.set()
            # the plan commits (proposal, its review, adoption) and the loop moves on to the
            # leaves; the planning rounds themselves are what this test is about
            for _ in range(500):
                committed = any(item.type == "PlanRevisionCommitted" for item in loop.store.list_events(mission_id))
                if (committed and not _open_plan_intents(loop, mission_id)) or running.done():
                    break
                await asyncio.sleep(0.02)
            running.cancel()
            try:
                await running
            except (asyncio.CancelledError, Exception):
                pass
            return {
                "mission_id": mission_id,
                "held": held,
                "types": [item.type for item in loop.store.list_events(mission_id)],
                "open": _open_plan_intents(loop, mission_id),
                "status": str(loop.store.get_mission(mission_id).status.value),
            }

    outcome = asyncio.run(case())
    held = outcome["held"]
    assert held["open"] == [f"{outcome['mission_id']}:planner:1"], held
    assert held["returned"] is False, f"run() returned while the planning intent was still in flight: {held}"
    # A waiting loop yields; the runtime's turn task reaches the provider and holds there —
    # a starved loop never lets it start.
    assert held["calls_started"] == 1, held
    assert outcome["open"] == [], f"an intent was left in flight: {outcome}"
    assert "PlanRevisionCommitted" in outcome["types"], outcome["types"]
    assert outcome["status"] != "PLANNING", outcome["status"]
