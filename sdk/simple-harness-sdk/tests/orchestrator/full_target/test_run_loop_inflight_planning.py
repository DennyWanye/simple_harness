# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3e: ``run()`` must not leave while a planning-class intent is still in flight.

The Grok acceptance rerun (H-L3-C1) found it: a planning round was submitted, the loop
reported an idempotent "intent already exists" as **progress** on every cycle, and a
progressing cycle neither sleeps nor yields — ``run()`` spun through its ``max_cycles``
budget in milliseconds with the runtime's turn tasks starved the whole time, then left
with the turn submitted and nobody to collect it.  The Mission stayed at PLANNING with
no settlement and no stop.

Asserted here on the current planning protocol: ``run()`` stays until a submitted
planning intent is collected — however long the model takes (a held gate stands in for
a sixty-second model call) — and the budget bounds *work*, not waiting.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

import test_htn_end_to_end as e2e  # noqa: E402
from decision_loop import auto_grant, refine_step  # noqa: E402

from agent_orchestrator.contracts.models import MissionStatus  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import RoleScriptedProvider  # noqa: E402

OPEN_INTENT_STATES = ("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")


def _open_plan_intents(loop: Orchestrator, mission_id: str) -> list[str]:
    return sorted(
        item.subject_id
        for item in loop.store.list_intents(*OPEN_INTENT_STATES)
        if item.kind == "plan" and item.mission_id == mission_id
    )


def test_run_waits_for_an_inflight_planning_intent_however_slow_the_model_is(
    tmp_path,
) -> None:
    """The provider holds the Planner's call at a gate, so the round is "a slow real
    model turn" for as long as the test says.  ``max_cycles`` is small on purpose: on a
    loop that counts waiting as progress the budget is spent in milliseconds and
    ``run()`` returns with the intent still SUBMITTED.
    """

    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    world = e2e.build_world(evidence, key="p23e-run-exit-held")
    world.store.close()
    config = OrchestratorConfig(
        evidence_root=evidence,
        max_concurrency=3,
        test_timeout_seconds=60,
        max_planning_attempts=2,
    )
    gate = asyncio.Event()
    provider = RoleScriptedProvider({"planner": [refine_step()]}, gate=gate)

    async def case() -> dict[str, Any]:
        async with Orchestrator(config, provider, poll_interval=0.02) as loop:
            world.env.semantics = HtnStore(loop.store)
            loop.install_hierarchical(planning=world.env)
            auto_grant(loop)
            mission_id = world.mission.id
            # The fixture has already moved the Mission to PLANNING, so the round the
            # loop would open on a CREATED Mission is opened here by the same call.
            await loop._try_planner_intent(mission_id, ordinal=1)
            running = asyncio.create_task(loop.run(max_cycles=120))
            # Long enough for a busy loop to burn 120 cycles many times over, short
            # enough that a waiting loop has only polled.
            await asyncio.sleep(1.0)
            held = {
                "returned": running.done(),
                "open": _open_plan_intents(loop, mission_id),
                "calls_started": dict(provider.by_role),
            }
            gate.set()
            # the plan commits and the loop moves on to the leaves; the planning round
            # itself is what this test is about
            for _ in range(400):
                types = [item.type for item in loop.store.list_events(mission_id)]
                if "PlanRevisionCommitted" in types or running.done():
                    break
                await asyncio.sleep(0.02)
            running.cancel()
            try:
                await running
            except (asyncio.CancelledError, Exception):
                pass
            mission = loop.store.get_mission(mission_id)
            return {
                "held": held,
                "types": [item.type for item in loop.store.list_events(mission_id)],
                "open": _open_plan_intents(loop, mission_id),
                "status": None if mission is None else mission.status,
            }

    outcome = asyncio.run(case())
    held = outcome["held"]
    assert held["open"] == [f"{world.mission.id}:planner:1"], held
    assert held["returned"] is False, (
        f"run() returned while the planning intent was still in flight: {held}"
    )
    # A waiting loop yields; the runtime's turn task reaches the provider and holds at
    # the gate — a starved loop never lets it start.
    assert held["calls_started"].get("planner") == 1, held
    assert outcome["open"] == [], f"an intent was left in flight: {outcome}"
    assert "PlanRevisionCommitted" in outcome["types"], outcome["types"]
    assert outcome["status"] is not MissionStatus.PLANNING, outcome["status"]
