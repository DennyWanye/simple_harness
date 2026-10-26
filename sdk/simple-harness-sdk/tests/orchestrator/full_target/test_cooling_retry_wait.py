"""Failed attempts waiting for a shared pool must survive idle confirmation."""

import asyncio
from dataclasses import replace

import pytest
from test_stall_asks_planner_first import stalled

from agent_orchestrator.contracts import MissionStatus
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, worker_reply
from simple_harness.providers.errors import ProviderServerError


@pytest.mark.parametrize("expire", [False, True])
def test_failed_attempt_waits_for_cooldown_then_retries_or_times_out(tmp_path, monkeypatch, expire):
    failed = False

    def worker(request):
        nonlocal failed
        if not failed:
            failed = True
            raise ProviderServerError(status_code=524)
        return worker_reply(request)

    async def case():
        provider = LayeredScriptedProvider(worker=worker)
        async with product_world(
            tmp_path / "root", provider, profile_failure_threshold=1, profile_cooldown_seconds=60.0
        ) as world:
            loop = world.loop
            mission_id = world.create(
                {
                    "goal": "Write NOTES.md with three points.",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "cooling-retry",
                }
            )["mission_id"]
            await loop.recover()
            async with asyncio.timeout(40):
                while not {"RuntimeProfileUnavailable", "PlanningRepairRequested"} <= {
                    e.type for e in world.store.list_events(mission_id)
                }:
                    await world.deployment.between_cycles(auto=True)
                    await loop._cycle()
                    await asyncio.sleep(0.01)
            events = world.store.list_events(mission_id)
            assert any(e.type == "PlanningRepairRequested" for e in events)
            assert not loop._attempts.deferred
            for _ in range(2):
                await world.deployment.between_cycles(auto=True)
                await loop._cycle()
                await loop._record_hierarchical_stall()
                await loop._confirm_and_stop_stalled()
                assert world.store.get_mission(mission_id).status is MissionStatus.ACTIVE
            if expire:
                loop._config = replace(loop._config, profile_wait_seconds=0.0)
                await loop._cycle()
                await loop._record_hierarchical_stall()
                await loop._confirm_and_stop_stalled()
                mission = world.store.get_mission(mission_id)
                assert mission.status is MissionStatus.FAILED
                assert mission.final_report["stop_reason"] == "runtime_unavailable"
            else:
                # Expire only the cooldown, preserving the production orchestration path.
                original_clock = world.store._clock
                monkeypatch.setattr(world.store, "_clock", lambda: original_clock() + 61.0)
                mission = await world.run_until_settled(mission_id, timeout=40)
                assert mission.status is MissionStatus.COMPLETED, mission.final_report
                events = world.store.list_events(mission_id)
                assert any(e.type == "PlanningRetryAuthorized" for e in events)
                assert len([e for e in events if e.type == "AttemptCreated"]) >= 2

    asyncio.run(case())


@pytest.mark.parametrize("inherited", [False, True])
def test_success_between_cooldowns_starts_a_new_wait(tmp_path, monkeypatch, inherited):
    async def case():
        async with stalled(tmp_path, key="recovered-cooldown") as world:
            loop = world.loop
            now = loop.store.now
            monkeypatch.setattr(loop.store, "_clock", lambda: now)
            profile = loop._router_for(world.mission_id).rules.default
            assert loop._config.profile_wait_seconds == 300.0
            for index in range(9):
                loop.commit.record_profile_failure(
                    profile,
                    error=None,
                    threshold=1,
                    cooldown_seconds=60,
                    mission_ids=() if inherited else (world.mission_id,),
                )
                if index < 8:
                    now += 20.0
                    loop.commit.record_profile_success(profile)
                    assert profile not in loop.commit.unavailable_until()
                    now += 20.0
            mission = world.mission()
            planned = {
                str(spec.task_id)
                for spec in loop._new_mode(mission).network(mission.id).occurrences
            }
            waits = loop._cooling_profile_waits(mission, loop.store.list_tasks(mission.id), planned)
            assert waits
            await loop._confirm_and_stop_stalled()
            assert world.mission().status is MissionStatus.ACTIVE, world.mission().final_report
            assert waits[0][2] == now

    asyncio.run(case())
