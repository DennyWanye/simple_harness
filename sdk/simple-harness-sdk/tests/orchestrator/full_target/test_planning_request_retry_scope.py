"""Later planning requests own their syntax retry, including after cold reopen.

On the product's deployment (``h1i_seed``): the later request follows real refused
rounds; the repair request follows a leaf whose result its independent reviewer sent
back, on the main loop; the reopened process is the product's deployment again.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from h1i_seed import CONFIG, committed, events, root_task, run_until, seeded
from h1i_seed import planner as seed_planner

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.planning_retry import pending_retry_permit
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    review_input,
    review_reply,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "planning_decision_v1" / "valid"


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _grant(loop: Any, mission: Any, product: Any, intent: Any) -> None:
    PlanningAuthorizationApi(loop.store, tenant_id=mission.tenant_id,
                             principal=product.deployment.principal).issue(
        mission.id, command_id="grant-" + intent.intent_id, request_id=intent.intent_id)


def _refused_reply() -> str:
    """A reply the protocol can read and admission refuses (a subject not asked about)."""

    body = json.loads((_FIXTURES / "wait.json").read_text(encoding="utf-8"))
    body["subject_key"] = "subject-not-in-this-request"
    return "<planning_decision>" + json.dumps(body) + "</planning_decision>"


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
        async with seeded(tmp_path, key="later-format", max_planning_attempts=10) as (loop, mission, _, _, dispatch, product):
            # Earlier requests were answered and refused (not a format problem): each refusal
            # opened the next request, so request ``ordinal`` is a later one.
            current = await loop._create_planner_intent(mission.id, ordinal=1)
            for n in range(1, ordinal):
                _grant(loop, mission, product, current)
                await loop._collect_plan_decision(current, None, mission, _refused_reply(), dispatch)
                current = loop.store.get_intent_for_subject(f"{mission.id}:planner:{n + 1}")
                assert current is not None
            opener = current
            prior = opener.config["planning_package"]["previous_feedback"]
            assert prior is not None and prior["status"] == "REJECTED"
            charged_before = loop._planning_attempts(mission.id)
            await loop._collect_plan_decision(opener, None, mission, "bad JSON", dispatch)
            # 2026-10-09 第 2 条：同一请求第一次读不懂，重问一次不算答错
            assert loop._planning_attempts(mission.id) == charged_before
            store = PlanningDecisionStore(loop.store)
            binding = store.get_planning_request(opener.intent_id)
            assert binding is not None and binding.intent_id != opener.intent_id
            retry = loop.store.get_intent(binding.intent_id)
            assert retry.config["ordinal"] == ordinal + 1
            assert retry.config["planning_package"] == opener.config["planning_package"]
            # 2026-09-30 格式三件：同一请求的包不变，但消息末尾附上"上一次错在哪"（字段路径反馈）。
            _assert_retry_message_carries_feedback(retry, opener, "DECISION_BLOCK_MISSING")
            assert store.get_planning_decision_by_attempt(opener.intent_id, 0)["status"] == "UNREADABLE"
            assert loop._planning_format_retry_remaining(intent=retry, mission=mission) == 0
            await loop._collect_plan_decision(retry, None, mission, "bad again", dispatch)
            assert store.get_planning_decision_by_attempt(opener.intent_id, 1)["status"] == "UNREADABLE"
            assert loop._planning_attempts(mission.id) == charged_before + 1  # 重问仍读不懂才算一次
            # 2026-09-30：同一请求的格式重试只有一次（上面），用完后规划总次数还有剩就开一个
            # **新请求**（新的冻结包、自己的一次格式重试），任务不因两次格式错失败。
            # 片 A（2026-10-01）：新请求取下一个空闲序号（``_next_planning_ordinal``）。本测试
            # 从第 ``ordinal`` 轮直接开题、前面的序号空着，所以按"开着的规划意图"找它。
            opened = [
                item for item in loop.store.list_intents(
                    "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
                if item.mission_id == mission.id and item.kind == "plan"
                and item.intent_id not in {opener.intent_id, retry.intent_id}
            ]
            assert len(opened) == 1, [item.subject_id for item in opened]
            fresh = opened[0]
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
    """The leaf's result is sent back by its reviewer; the planner's repair reply is
    unreadable, so the same request is re-asked; the process stops while that re-ask is
    with the planner.  The reopened process answers the very same retry request."""

    seen = {"rework": False}

    def reviewer(request: Any) -> str:
        package = review_input(request)
        assert package is not None
        if (package.get("package") or {}).get("purpose") == "TASK_CONTENT" and not seen["rework"]:
            seen["rework"] = True
            return review_reply(package, verdict="REWORK", grade="FAIL", reason="要点太笼统，请写具体。")
        return review_reply(package)

    def planner(request: Any) -> Any:
        if package_of(request).get("repair_requests"):
            return "invalid"
        return seed_planner(request)

    async def case():
        provider = LayeredScriptedProvider(planner=planner, reviewer=reviewer)
        async with committed(tmp_path, key="cold-repair-format", provider=provider,
                             max_planning_attempts=6) as seed:
            loop, mission, product = seed.loop, seed.mission, seed.product
            [task_id] = [t.id for t in loop.store.list_tasks(mission.id) if t.id != root_task(mission.id)]
            provider.held.discard("worker")
            released = provider.release
            provider.release = asyncio.Event()
            released.set()  # the executor answers; the reviewer sends the result back

            def reasked() -> bool:
                rejected = [e for e in events(loop, mission.id, "PlanningRejected")
                            if e.payload.get("reason") == "proposal_unreadable"]
                if rejected:
                    provider.held.add("planner")  # the re-ask stays with the planner
                return bool(rejected)

            await run_until(product, reasked, timeout=30)
            [unreadable] = [e.payload for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                            if e.payload.get("status") == "UNREADABLE"]
            opener = loop.store.get_intent(unreadable["request_id"])
            request = PlanningDecisionStore(loop.store).get_planning_request(opener.intent_id)
            retry_id = request.intent_id
            assert retry_id != opener.intent_id
            retry_ordinal = int(loop.store.get_intent(retry_id).config["ordinal"])
            failed_id = loop.store.list_attempts(task_id)[0].id
        async with product_world(tmp_path / "root", RoleScriptedProvider({}), auto=False,
                                 **{**CONFIG, "max_planning_attempts": 6}) as world:
            loop = world.loop
            mission = loop.store.get_mission(mission.id)
            dispatch = loop._dispatch_for(mission.id)
            retry = await loop._create_planner_intent(mission.id, ordinal=retry_ordinal)
            assert retry.intent_id == retry_id
            _assert_retry_message_carries_feedback(retry, opener, "DECISION_BLOCK_MISSING")
            package = retry.config["planning_package"]
            subject = next(s for s in package["planning_subjects"] if s["task_id"] == task_id)
            network = dispatch.network(mission.id)
            occurrence = next(spec.occurrence_id for spec in network.occurrences if str(spec.task_id) == task_id)
            instance = next(item for item in network.method_instances
                            if any((c.goal_occurrence_id or c.occurrence_id) == occurrence for c in item.child_bindings))
            ref = next(r for r in package["visible_refs"]
                       if r["kind"] == "method_instance" and r["id"] == str(instance.instance_id))
            body = {"schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
                "rationale": "Retry the recorded failure.", "reason_refs": [], "assumptions": [],
                "uncertainties": [], "alternatives": [], "replan_triggers": [],
                "payload": {"repair_kind": "RETRY_SAME_METHOD", "failed_attempt_id": failed_id,
                            "method_instance_ref": ref}}
            reply = "<planning_decision>" + json.dumps(body) + "</planning_decision>" if corrected else "bad again"
            await loop._collect_plan_decision(retry, None, mission, reply, dispatch)
            row = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(opener.intent_id, 1)
            assert row["status"] == ("COMMITTED" if corrected else "UNREADABLE"), row["detail"]
            # 2026-09-30：没改对也不判失败——同一请求的格式重试用完，规划总次数还有剩，开新请求。
            assert str(loop.store.get_mission(mission.id).status.value) == "ACTIVE"
            if not corrected:
                assert loop.store.get_intent_for_subject(f"{mission.id}:planner:{retry_ordinal + 1}") is not None
            assert (pending_retry_permit(loop.store, mission.id, task_id) is not None) is corrected
            assert len(loop.store.list_attempts(task_id)) == 1
            changes = loop.store.connection.total_changes
            await loop._collect_plan_decision(retry, None, mission, reply, dispatch)
            assert loop.store.connection.total_changes == changes
    asyncio.run(case())


def test_historical_intent_keeps_global_attempt_identity():
    old = SimpleNamespace(config={"ordinal": 5})
    assert Orchestrator._planning_decision_attempt_ordinal(old) == 4
