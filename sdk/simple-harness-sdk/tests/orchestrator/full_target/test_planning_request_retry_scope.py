"""Later planning requests own their syntax retry, including after cold reopen."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_h1i_production_entry import _config, _open_planner_round, _seed_new_protocol
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from agent_orchestrator.governance.policies import deployed_layers
from agent_orchestrator.planning.htn.observers.code import code_observers
from agent_orchestrator.planning.htn.world import build_planning_world
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.orchestrator.planning_repair_requests import collect_triggers
from agent_orchestrator.orchestrator.planning_retry import pending_retry_permit
from test_h4_retry_runtime_entry import attempt, grant, refined, retry_payload


def _assert_retry_message_carries_feedback(retry, opener, code):
    before = opener.config["message"]["content"]
    after = retry.config["message"]["content"]
    assert after.startswith(before) and after != before
    tail = after[len(before):]
    assert "previous_feedback" in tail and code in tail
    feedback = json.loads(tail[tail.index("{"):tail.rindex("}") + 1])
    assert feedback["status"] == "UNREADABLE" and feedback["rejection_codes"] == [code]
    assert feedback["budgets"]["same_request_format_retries_remaining"] == 0


@pytest.mark.parametrize("ordinal", [2, 5])
def test_later_request_retries_its_own_frozen_package_once(tmp_path: Path, ordinal: int):
    async def case():
        config = replace(_config(tmp_path), max_planning_attempts=10)
        async with Orchestrator(config, RoleScriptedProvider({"planner": []})) as loop:
            mission, _, _, dispatch = _seed_new_protocol(loop, tmp_path, key="later-format")
            opener = await _open_planner_round(loop, mission, dispatch, ordinal=ordinal)
            await loop._collect_plan_decision(opener, None, mission, "bad JSON", dispatch)
            store = PlanningDecisionStore(loop.store)
            binding = store.get_planning_request(opener.intent_id)
            assert binding is not None and binding.intent_id != opener.intent_id
            retry = loop.store.get_intent(binding.intent_id)
            assert retry.config["ordinal"] == ordinal + 1
            assert retry.config["planning_package"] == opener.config["planning_package"]
            assert opener.config["planning_package"]["previous_feedback"] is None
            # 2026-09-30 格式三件：同一请求的包不变，但消息末尾附上"上一次错在哪"（字段路径反馈）。
            _assert_retry_message_carries_feedback(retry, opener, "DECISION_BLOCK_MISSING")
            assert store.get_planning_decision_by_attempt(opener.intent_id, 0)["status"] == "UNREADABLE"
            assert loop._planning_format_retry_remaining(intent=retry, mission=mission) == 0
            await loop._collect_plan_decision(retry, None, mission, "bad again", dispatch)
            assert store.get_planning_decision_by_attempt(opener.intent_id, 1)["status"] == "UNREADABLE"
            # 2026-09-30：同一请求的格式重试只有一次（上面），用完后规划总次数还有剩就开一个
            # **新请求**（新的冻结包、自己的一次格式重试），任务不因两次格式错失败。
            fresh = loop.store.get_intent_for_subject(f"{mission.id}:planner:{ordinal + 2}")
            assert fresh is not None and fresh.intent_id != retry.intent_id
            assert store.get_planning_request(fresh.intent_id) is None or \
                store.get_planning_request(fresh.intent_id).intent_id == fresh.intent_id
            assert loop._planning_format_retry_remaining(intent=fresh, mission=mission) == 1
            # 新请求的包里带 previous_feedback：上一次（同一请求的重试）为什么被拒。
            feedback = fresh.config["planning_package"]["previous_feedback"]
            assert feedback["status"] == "UNREADABLE"
            assert feedback["rejection_codes"] == ["DECISION_BLOCK_MISSING"]
            assert feedback["budgets"]["same_request_format_retries_remaining"] == 1
            assert str(loop.store.get_mission(mission.id).status) != "FAILED"
            before = loop.store.connection.total_changes
            await loop._collect_plan_decision(retry, None, mission, "bad again", dispatch)
            assert loop.store.connection.total_changes == before
    asyncio.run(case())


@pytest.mark.parametrize("corrected", [True, False])
def test_active_repair_format_retry_survives_cold_reopen(tmp_path, corrected):
    async def case():
        config = replace(_config(tmp_path), max_planning_attempts=6)
        async with Orchestrator(config, RoleScriptedProvider({"planner": []})) as loop:
            mission, dispatch, task_id = await refined(loop, tmp_path, "cold-repair-format")
            failed, _ = attempt(loop, task_id)
            loop.commit.mark_attempt_lost(failed.id, reason="runtime_unavailable")
            assert collect_triggers(loop, mission)
            opener = await loop._create_planner_intent(mission.id, ordinal=2)
            grant(loop, mission, opener)
            await loop._collect_plan_decision(opener, None, mission, "invalid", dispatch)
            request = PlanningDecisionStore(loop.store).get_planning_request(opener.intent_id)
            retry_id = request.intent_id
            assert retry_id != opener.intent_id
        async with Orchestrator(config, RoleScriptedProvider({"planner": []})) as loop:
            mission = loop.store.get_mission(mission.id)
            world = build_planning_world(mission.id, domains=("code",), semantics=HtnStore(loop.store),
                deployed_layers=deployed_layers(config.deployment_policy),
                observers=code_observers(tmp_path / "repo", allow_test_execution=True))
            dispatch = loop.install_hierarchical(planning=world)
            retry = await loop._create_planner_intent(mission.id, ordinal=3)
            assert retry.intent_id == retry_id
            _assert_retry_message_carries_feedback(retry, opener, "DECISION_BLOCK_MISSING")
            subject = next(s for s in retry.config["planning_package"]["planning_subjects"] if s["task_id"] == task_id)
            body = {"schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
                "rationale": "Retry the recorded failure.", "reason_refs": [], "assumptions": [],
                "uncertainties": [], "alternatives": [], "replan_triggers": [],
                "payload": retry_payload(dispatch, mission, task_id, failed.id, retry.config["planning_package"])}
            reply = "<planning_decision>" + json.dumps(body) + "</planning_decision>" if corrected else "bad again"
            await loop._collect_plan_decision(retry, None, mission, reply, dispatch)
            row = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(opener.intent_id, 1)
            assert row["status"] == ("COMMITTED" if corrected else "UNREADABLE"), row["detail_json"]
            # 2026-09-30：没改对也不判失败——同一请求的格式重试用完，规划总次数还有剩，开新请求。
            assert str(loop.store.get_mission(mission.id).status) == "ACTIVE"
            if not corrected:
                assert loop.store.get_intent_for_subject(f"{mission.id}:planner:4") is not None
            assert (pending_retry_permit(loop.store, mission.id, task_id) is not None) is corrected
            assert len(loop.store.list_attempts(task_id)) == 1
            changes = loop.store.connection.total_changes
            await loop._collect_plan_decision(retry, None, mission, reply, dispatch)
            assert loop.store.connection.total_changes == changes
    asyncio.run(case())


def test_historical_intent_keeps_global_attempt_identity():
    old = SimpleNamespace(config={"ordinal": 5})
    assert Orchestrator._planning_decision_attempt_ordinal(old) == 4
