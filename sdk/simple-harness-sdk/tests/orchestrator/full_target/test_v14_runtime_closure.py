"""Focused V1.4 runtime-closure regressions.

Every case runs on the product deployment (h1i_seed / taskgraph_exec.production_fixture):
the planning decisions come from real Planner rounds, questions are answered through the
authenticated facade.  Only the model replies are scripted.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_FULL_TARGET = Path(__file__).resolve().parent
for _extra in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from h1i_seed import seeded  # noqa: E402
from production_fixture import (  # noqa: E402
    CHAIN_CRITERIA,
    chain_planner,
    enabled_world,
    result_envelope,
    scripted_worker,
)

from agent_orchestrator.api.facade import FacadeError  # noqa: E402
from agent_orchestrator.contracts import TaskStatus  # noqa: E402
from agent_orchestrator.contracts.planning_decisions import PlanningDecisionStatus  # noqa: E402
from agent_orchestrator.orchestrator.planning_repair_requests import pending_requests  # noqa: E402
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402
from agent_orchestrator.storage.planning_human_store import PlanningHumanStore  # noqa: E402
from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.scripted_replies import decision  # noqa: E402


def _events(loop, mission_id: str, event_type: str) -> list:
    return [event for event in loop.store.list_events(mission_id) if event.type == event_type]


def test_v14_malformed_planning_reply_records_unreadable_without_secondary_failure(tmp_path) -> None:
    async def case() -> None:
        async with seeded(tmp_path, key="v14-malformed-reply") as (loop, mission, _world, _root, dispatch, _product):
            opener = await loop._create_planner_intent(mission.id, ordinal=1)
            await loop._collect_plan_decision(
                opener, object(), mission,
                "<planning_decision>{not-json}</planning_decision>", dispatch,
            )
            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            evaluated = _events(loop, mission.id, "PlanningDecisionEvaluated")[-1]
            assert stored is not None
            assert stored["status"] == str(PlanningDecisionStatus.UNREADABLE)
            assert stored["canonical_hash"] is None
            assert evaluated.payload["status"] == str(PlanningDecisionStatus.UNREADABLE)
            assert evaluated.payload["canonical_hash"] is None
            # the rejection ledger records it as well (one decode refusal, both records)
            assert _events(loop, mission.id, "PlanningRejected")[-1].payload["reason"] == "proposal_unreadable"
    asyncio.run(case())


# ---- H4 questions to the person and repair requests (real Planner rounds) -----------------


def _no_change(request):  # type: ignore[no-untyped-def]
    """The scripted Planner of these cases: the two-step chain, then "nothing to change"."""
    reply = chain_planner(request)
    if reply is not None:
        return reply
    subject = package_of(request)["planning_subjects"][0]["subject_key"]
    return decision(subject, "NO_CHANGE", {"reason": "计划不变。"}, "计划不变。")


def _question(subject_key: str, *, blocking: bool) -> dict:
    return {"schema_version": 1, "decision_type": "REQUEST_HUMAN", "subject_key": subject_key,
            "rationale": "需要用户确认。", "reason_refs": [], "assumptions": [], "uncertainties": [],
            "alternatives": [], "replan_triggers": [],
            "payload": {"question": "第二份文件要不要加上日期？", "options": [], "blocking": blocking}}


def _first_and_next(world):  # type: ignore[no-untyped-def]
    network = world.dispatch.network(world.mission.id)
    first = next(b for b in network.task_bindings if str(b.form) == "primitive" and not b.input_ports)
    following = next(b for b in network.task_bindings if str(b.form) == "primitive" and b.input_ports)
    return str(first.task_id), str(following.task_id)


@pytest.mark.parametrize("blocking", (False, True), ids=("nonblocking", "blocking"))
def test_h4_a_question_is_durable_and_only_a_blocking_one_pauses_dispatch(tmp_path, blocking) -> None:
    """A question is a durable row; a non-blocking one never stops dispatch, a blocking one
    holds every new plan and Worker until the person answers.  The answer is a CAS write:
    the same answer replays its receipt, a different one is refused."""

    async def case() -> None:
        async with enabled_world(tmp_path, key=f"v14-human-{blocking}", planner=_no_change,
                                 criteria=CHAIN_CRITERIA, hold_worker=True) as world:
            store, mission = world.store, world.mission
            await world.commit_seed()
            # The first step is handed off: its Worker's model call is out (held).
            await world.until(lambda: world.provider.entered.is_set())
            first, following = _first_and_next(world)
            intent = await world.open_planner_round()
            package = intent.config["planning_package"]
            await world.answer(intent, _question(package["planning_subjects"][0]["subject_key"], blocking=blocking))
            humans = PlanningHumanStore(store)
            [question] = humans.list(mission.id)
            assert question["state"] == "PENDING"
            assert humans.pending(mission.id) is blocking

            # The work already handed off is collected either way (a blocking question holds
            # only what has not started).
            world.provider.release.set()
            await world.until(lambda: store.get_task(first).status is TaskStatus.COMPLETED)
            if not blocking:
                await world.until(lambda: store.list_attempts(following))
                assert humans.get(question["decision_id"])["state"] == "PENDING"  # still open, still there
                return
            for _ in range(10):
                await world.step()
            assert not store.list_attempts(following)  # no new Worker while the question blocks

            control = world.product.control
            command = {"decision_id": question["decision_id"], "answer": "加上", "expected_version": question["version"],
                       "nonce": "nonce-1"}
            receipt = control.answer_planning_question(command)
            assert humans.pending(mission.id) is False
            assert control.answer_planning_question(command) == receipt
            with pytest.raises(FacadeError, match="different answer"):
                control.answer_planning_question({**command, "answer": "不加", "nonce": "nonce-2"})
            await world.until(lambda: store.list_attempts(following))

    asyncio.run(case())


def test_h4_a_superseded_question_cannot_be_answered_and_a_repair_request_is_consumed_by_its_committed_decision(
        tmp_path) -> None:
    """The first step fails its check; the repair round asks the person (non-blocking) — a
    question is no plan change, so the repair request stays.  The next round redoes the step
    with a successor: that committed decision on the request's own subject consumes it, and
    the plan moved on, so the open question is superseded and its answer is refused."""
    seen: dict = {}

    def no_claims(request):  # type: ignore[no-untyped-def]
        body = json.loads(result_envelope(request)[len("<result_envelope>"):-len("</result_envelope>")])
        body["claims"] = []
        return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"

    def planner(request):  # type: ignore[no-untyped-def]
        package = package_of(request)
        repairs = [entry for entry in package.get("repair_requests") or ()
                   if ((entry.get("request") or {}).get("context") or {}).get("event_type") != "GoalUnrefined"]
        if not repairs:
            return _no_change(request)
        subject = next(row for row in package["planning_subjects"] if row["task_id"] == seen["first"])
        seen.setdefault("repair_rounds", 0)
        seen["repair_rounds"] += 1
        if seen["repair_rounds"] == 1:
            return json_decision(_question(subject["subject_key"], blocking=False))
        world = seen["world"]
        old = world.dispatch.network(world.mission.id).binding_for_task(seen["first"])
        task_type = next(s for s in world.dispatch.require_planning_world().catalog.task_types()
                         if s.goal_signature == old.goal_signature)

        def visible(kind, identity):  # type: ignore[no-untyped-def]
            return next(row for row in package["visible_refs"] if row["kind"] == kind and row["id"] == identity)

        return decision(subject["subject_key"], "REPAIR", {
            "repair_kind": "PROPOSE_SUCCESSOR", "old_task_ref": visible("task", seen["first"]),
            "obligation_ref": visible("obligation", str(old.obligation_id)),
            "goal_type_ref": task_type.task_type_ref.to_json(),
            "bindings": {"goal": "重写第一份文件，写明要点。"}}, "原步骤的结果没有说明要点，换一个后继步骤重做。")

    worker = scripted_worker(("workspace_write_file", {"path": "facts.md", "content": "facts"}), no_claims)

    async def case() -> None:
        async with enabled_world(tmp_path, key="v14-human-stale", planner=planner, worker=worker,
                                 criteria=CHAIN_CRITERIA) as world:
            store, mission = world.store, world.mission
            seen["world"] = world
            await world.commit_seed()
            seen["first"], _following = _first_and_next(world)
            humans = PlanningHumanStore(store)
            await world.until(lambda: humans.list(mission.id))
            [question] = humans.list(mission.id)
            [request] = pending_requests(store, mission.id)  # the question did not consume it
            assert seen["first"] in request["request"]["trigger_refs"][0]
            assert question["request"]["binding"]["plan_revision"] == 1

            await world.until(lambda: world.dispatch.network(mission.id).plan_revision == 2)
            assert request["request_id"] not in {row["request_id"] for row in pending_requests(store, mission.id)}
            addressed = _events(world.loop, mission.id, "PlanningRepairAddressed")
            assert [row.payload["repair_request_ids"] for row in addressed] == [[request["request_id"]]]
            await world.until(lambda: humans.get(question["decision_id"])["state"] == "STALE")
            with pytest.raises(FacadeError, match="version changed"):
                world.product.control.answer_planning_question({
                    "decision_id": question["decision_id"], "answer": "加上",
                    "expected_version": question["version"], "nonce": "nonce-stale"})

    asyncio.run(case())


def json_decision(body: dict) -> str:
    return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"
