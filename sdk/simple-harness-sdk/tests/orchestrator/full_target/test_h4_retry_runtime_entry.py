"""Exercise H4 retry and runtime wakes through persisted production entry points.

Every case runs on the product's deployment and its main loop (``h1i_seed``): the first
plan revision is committed for real and its leaf is dispatched to a scripted executor.
A step fails the way the product sees steps fail -- the executor's call makes no progress
for ``stall_seconds`` (timed out), its result is sent back by the independent reviewer,
or the executor reports that it is blocked -- and the repair goes through the system's own
retry decision or the planner's repair round, both through the production collector.
No Attempt is created and no outcome is recorded by hand; a direct ``create_attempt`` is
only ever a refused probe at the real entry (adjudication ①a).
"""
import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from h1i_seed import committed, events, resume, root_task, run_until
from h1i_seed import planner as seed_planner

from agent_orchestrator.orchestrator.commit_service import CommitRejected, Reservation
from agent_orchestrator.orchestrator.planning_repair_requests import (
    collect_triggers,
    pending_requests,
)
from agent_orchestrator.orchestrator.planning_retry import pending_retry_permit
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    review_input,
    review_reply,
    worker_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _leaf(loop: Any, mission: Any) -> str:
    [task] = [task for task in loop.store.list_tasks(mission.id) if task.id != root_task(mission.id)]
    return task.id


def _probe_retry_attempt(loop: Any, task_id: str, retry_of: str) -> CommitRejected:
    """A retry Attempt delivered straight to the commit entry, without a current
    committed RETRY_SAME_METHOD: refused, nothing written."""

    before = loop.store.connection.total_changes
    with pytest.raises(CommitRejected) as refused:
        loop.commit.create_attempt(task_id, role="worker", model="fixture-worker",
            prompt_version="fixture-worker-v1", context_version="h4-runtime-v1",
            reservation=Reservation(tokens=1000),
            intent_config={"message": "an unauthorised retry"},
            input_hash="e" * 64, inputs=(), retry_of=retry_of)
    assert loop.store.connection.total_changes == before
    return refused.value


@asynccontextmanager
async def _timed_out_and_retried(tmp_path: Any, key: str) -> AsyncIterator[tuple[Any, str]]:
    """The leaf's executor makes no progress for ``stall_seconds``: the Attempt times out,
    a repair request opens, the system's own RETRY_SAME_METHOD is committed and the next
    Attempt is created under it (its executor's call held again)."""

    async with committed(tmp_path, key=key, stall_seconds=0.5) as seed:
        task_id = _leaf(seed.loop, seed.mission)
        await run_until(seed.product, lambda: len(seed.loop.store.list_attempts(task_id)) >= 2, timeout=30)
        yield seed, task_id


def test_timeout_opens_repair_and_committed_retry_is_single_use(tmp_path):
    async def case():
        async with _timed_out_and_retried(tmp_path, "h4-retry-live") as (seed, task_id):
            loop, mission = seed.loop, seed.mission
            first, second = loop.store.list_attempts(task_id)[:2]
            assert str(first.status) in {"TIMED_OUT", "AttemptStatus.TIMED_OUT"}, first.status
            assert events(loop, mission.id, "AttemptTimedOut")
            requested = [e for e in events(loop, mission.id, "PlanningRepairRequested")
                         if first.id in json.dumps(e.payload)]
            assert len(requested) == 1
            [repair] = [e.payload for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                        if e.payload.get("decision_type") == "REPAIR"]
            assert repair["status"] == "COMMITTED"
            assert not pending_requests(loop.store, mission.id)
            second_intent = loop.store.get_intent_for_subject(second.id)
            assert second.retry_of == first.id
            assert second_intent.config["planning_retry_decision_id"] == repair["decision_id"]
            assert pending_retry_permit(loop.store, mission.id, task_id) is None
            # The committed retry was used once; another retry of the same failure is refused.
            _probe_retry_attempt(loop, task_id, first.id)
            assert len(loop.store.list_attempts(task_id)) == 2

    asyncio.run(case())


def test_a_failed_attempt_with_an_unknown_charge_can_still_be_retried(tmp_path):
    # 2026-09-25 UI 全量点击：工人一轮因工具参数 JSON 不合法失败，这次调用的用量
    # 记为未知；以前 RETRY_SAME_METHOD 一律被拒，文档任务停滞失败。按记账硬规则
    # （多算不少算、不冻结），未知费用留在原预留上，重试另开一次 Attempt。
    # 产品里一次超时的调用（被取消、没有回执）用量就是未知的。
    async def case():
        async with _timed_out_and_retried(tmp_path, "h4-retry-unknown-charge") as (seed, task_id):
            loop, mission = seed.loop, seed.mission
            first, second = loop.store.list_attempts(task_id)[:2]
            assert loop.commit.ledger.has_unknown_usage(first.id)
            [repair] = [e.payload for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                        if e.payload.get("decision_type") == "REPAIR"]
            assert repair["status"] == "COMMITTED"
            assert second.retry_of == first.id

    asyncio.run(case())


def test_runtime_wakes_fence_stale_planner_and_repeated_source_transitions(tmp_path):
    """The leaf's executor hangs on a Provider call whose outcome stays unknown; the loop
    gives the Attempt up (``_give_up_blocked_assured_attempt``, the product's own handler
    for that condition).  The planner had one earlier reply refused, so the system's own
    retry steps aside and the planner is asked: it declares the runtime blocked.  Provider
    outages and recoveries (the turn-health writer, ①c) wake the block, fence a stale
    resumed planner round, and a later RETRY_SAME_METHOD lifts the block."""

    from agent_orchestrator.orchestrator.planning_runtime_block import (
        WOKEN,
        blocks_intent,
        last_wake,
        pending_block,
        resume_source_current,
        wake_blocks,
    )

    state = {"asked": 0}

    def planner(request: Any) -> Any:
        state["asked"] += 1
        if state["asked"] == 1:
            return "bad JSON"  # one refused planner reply in the failure index
        package = package_of(request)
        runtime = [entry for entry in package.get("repair_requests") or ()
                   if entry["request"]["trigger_source"] == "RUNTIME_UNAVAILABLE"]
        if not runtime:
            return seed_planner(request)
        subject = next(s for s in package["planning_subjects"] if s["task_id"] == state["task_id"])
        body = {"schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
                "rationale": "Recover the recorded failed execution.", "reason_refs": [], "assumptions": [],
                "uncertainties": [], "alternatives": [], "replan_triggers": [],
                "payload": {"repair_kind": "DECLARE_RUNTIME_BLOCKED",
                            "repair_request_id": runtime[0]["request_id"],
                            "blockers": [{"code": "OTHER", "detail": "Recorded executor is unavailable."}],
                            "resumable_if": ["plan_revision_changed"]}}
        return "<planning_decision>" + json.dumps(body) + "</planning_decision>"

    async def case():
        provider = LayeredScriptedProvider(planner=planner)
        async with committed(tmp_path, key="h4-runtime-live", provider=provider,
                             max_planning_attempts=6) as seed:
            loop, mission, product, dispatch = seed.loop, seed.mission, seed.product, seed.dispatch
            task_id = state["task_id"] = _leaf(loop, mission)
            [first] = loop.store.list_attempts(task_id)
            first_intent = loop.store.get_intent_for_subject(first.id)
            profile = loop.profile_of(first_intent)
            await loop._give_up_blocked_assured_attempt(first_intent, detail={"waited_seconds": 600})
            assert loop.store.get_attempt(first.id).failure["reason"] == "provider_outcome_unknown"
            # The hung call finally returns; its turn was already given up, its answer is
            # history.  The executor is held again for whatever runs next.
            resume(product, hold=("worker",))
            await run_until(product, lambda: pending_block(loop.store, mission.id) is not None, timeout=30)
            [declared] = [e.payload for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                          if e.payload.get("decision_type") == "REPAIR"]
            assert declared["status"] == "NO_STATE_CHANGE", declared
            assert not wake_blocks(loop)
            assert "runtime block" in str(_probe_retry_attempt(loop, task_id, first.id))

            def unavailable():
                loop.commit.record_profile_failure(profile, error={"reason": "unavailable"},
                    threshold=1, cooldown_seconds=3600, mission_ids=(mission.id,))

            unavailable()
            assert wake_blocks(loop)
            loop.commit.record_profile_success(profile)
            assert wake_blocks(loop)
            resumed = await loop._create_planner_intent(mission.id, ordinal=loop._next_planning_ordinal(mission.id))
            assert resume_source_current(loop, resumed, mission)
            assert not blocks_intent(loop.store, resumed)
            unavailable()
            assert not resume_source_current(loop, resumed, mission)
            assert wake_blocks(loop)
            assert blocks_intent(loop.store, resumed)
            assert loop.store.get_intent(resumed.intent_id).state == "FAILED"
            loop.commit.record_profile_success(profile)
            assert wake_blocks(loop)
            assert not wake_blocks(loop)
            wakes = [e for e in loop.store.iter_events(mission.id) if e.type == WOKEN]
            assert len(wakes) == 4
            assert len({e.payload["decision_id"] for e in wakes}) == 4
            assert wakes[0].payload["source_hash"] == wakes[2].payload["source_hash"]
            assert wakes[1].payload["source_hash"] == wakes[3].payload["source_hash"]
            block = pending_block(loop.store, mission.id)
            assert last_wake(loop.store, mission.id, block).id == wakes[-1].id
            assert not resume_source_current(loop, resumed, mission)

            # The planner retries the recorded failure: the block is lifted and the next
            # Attempt is the loop's own, under the committed retry.
            retry = await loop._create_planner_intent(mission.id, ordinal=loop._next_planning_ordinal(mission.id))
            package = retry.config["planning_package"]
            network = dispatch.network(mission.id)
            occurrence = next(spec.occurrence_id for spec in network.occurrences if str(spec.task_id) == task_id)
            instance = next(item for item in network.method_instances
                            if any((c.goal_occurrence_id or c.occurrence_id) == occurrence for c in item.child_bindings))
            ref = next(r for r in package["visible_refs"]
                       if r["kind"] == "method_instance" and r["id"] == str(instance.instance_id))
            subject = next(s for s in package["planning_subjects"] if s["task_id"] == task_id)
            body = {"schema_version": 1, "decision_type": "REPAIR", "subject_key": subject["subject_key"],
                    "rationale": "Retry the recorded failure.", "reason_refs": [], "assumptions": [],
                    "uncertainties": [], "alternatives": [], "replan_triggers": [],
                    "payload": {"repair_kind": "RETRY_SAME_METHOD", "failed_attempt_id": first.id,
                                "method_instance_ref": ref}}
            from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi

            PlanningAuthorizationApi(loop.store, tenant_id=mission.tenant_id,
                                     principal=product.deployment.principal).issue(
                mission.id, command_id="grant-" + retry.intent_id, request_id=retry.intent_id)
            await loop._collect_plan_decision(retry, object(), mission,
                                              "<planning_decision>" + json.dumps(body) + "</planning_decision>",
                                              dispatch)
            retried = [e.payload for e in events(loop, mission.id, "PlanningDecisionEvaluated")
                       if e.payload.get("request_id") == retry.intent_id]
            assert retried[-1]["status"] == "COMMITTED", retried
            assert pending_block(loop.store, mission.id) is None
            provider.held.update({"worker", "planner"})
            await run_until(product, lambda: len(loop.store.list_attempts(task_id)) >= 2, timeout=30)
            second = loop.store.list_attempts(task_id)[1]
            assert second.retry_of == first.id

    asyncio.run(case())


def _rework_first_result() -> Any:
    seen = {"rework": False}

    def reviewer(request: Any) -> str:
        package = review_input(request)
        assert package is not None
        if (package.get("package") or {}).get("purpose") == "TASK_CONTENT" and not seen["rework"]:
            seen["rework"] = True
            return review_reply(package, verdict="REWORK", grade="FAIL", reason="要点太笼统，请写具体。")
        return review_reply(package)

    return reviewer


def _refused_repair(request: Any) -> str:
    """A repair-round reply the protocol can read and admission refuses (a subject the
    request did not ask about)."""

    package = package_of(request)
    if not package.get("repair_requests"):
        return seed_planner(request)
    body = {"schema_version": 1, "decision_type": "NO_CHANGE", "subject_key": "subject-not-in-this-request",
            "rationale": "nothing to change", "reason_refs": [], "assumptions": [], "uncertainties": [],
            "alternatives": [], "replan_triggers": [], "payload": {"reason": "nothing to change"}}
    return "<planning_decision>" + json.dumps(body) + "</planning_decision>"


def _planner_rounds(loop: Any, mission_id: str) -> set[str]:
    return {item.intent_id for item in loop.store.list_intents(
        "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED", "SETTLED", "FAILED")
        if item.mission_id == mission_id and ":planner:" in item.intent_id}


def test_a_refused_repair_round_opens_the_next_round_instead_of_idling(tmp_path):
    """2026-09-25 desktop run: the repair round's ordinal (4, counting the committed
    rounds) was over the ladder although it was the first refusal, so the refusal was
    only noted and the Mission sat ACTIVE with no work and no human request."""

    async def case():
        holder: dict[str, Any] = {}

        def planner(request: Any) -> str:
            reply = _refused_repair(request)
            if package_of(request).get("repair_requests"):
                holder["provider"].held.add("planner")  # 交出这份被拒回复后就不再答
            return reply

        provider = LayeredScriptedProvider(planner=planner, reviewer=_rework_first_result(), worker=worker_reply)
        holder["provider"] = provider
        async with committed(tmp_path, key="h4-refused-repair", provider=provider) as seed:
            loop, mission, product = seed.loop, seed.mission, seed.product
            provider.held.discard("worker")
            released = provider.release
            provider.release = asyncio.Event()
            released.set()  # the executor answers; its result is sent back by the reviewer

            def refusal() -> dict[str, Any] | None:
                return next((e.payload for e in events(loop, mission.id, "PlanningRejected")
                             if e.payload.get("ordinal")), None)

            # 规划器交出被拒回复后就停住，只看系统有没有开下一轮
            await run_until(product, lambda: refusal() is not None, timeout=30)
            refused = refusal()
            assert refused["reason"] == "proposal_not_grounded", refused  # 读得懂、准入拒绝（不是格式错）
            following = f"{mission.id}:planner:{int(refused['ordinal']) + 1}"
            await run_until(product, lambda: any(r.endswith(following) for r in _planner_rounds(loop, mission.id)),
                            timeout=30)
            assert loop.store.get_mission(mission.id).status.value == "ACTIVE"  # the next round, not silence

    asyncio.run(case())


def test_a_spent_repair_ladder_ends_the_mission_by_name(tmp_path):
    async def case():
        provider = LayeredScriptedProvider(planner=_refused_repair, reviewer=_rework_first_result(),
                                           worker=worker_reply)
        async with committed(tmp_path, key="h4-spent-repair", provider=provider) as seed:
            loop, mission, product = seed.loop, seed.mission, seed.product
            provider.held.discard("worker")
            released = provider.release
            provider.release = asyncio.Event()
            released.set()
            await run_until(product, lambda: loop.store.get_mission(mission.id).status.value
                            in {"FAILED", "COMPLETED", "CANCELLED"}, timeout=60)
            final = loop.store.get_mission(mission.id)
            assert final.status.value == "FAILED", final.status
            assert final.final_report["stop_reason"] == "planning_failed", final.final_report
            refusals = [e for e in events(loop, mission.id, "PlanningRejected")]
            assert len(refusals) >= loop._config.max_planning_attempts

    asyncio.run(case())


def test_a_step_that_reports_blocked_goes_back_to_the_planner(tmp_path):
    """2026-09-29 真机第十、十一局：测试步如实报告"卡住"（工作区缺 wordfreq.py），尝试进了
    "等重试"，但这个结果不在"转规划修补"的来源里——没人问规划器，几秒后判"没有可派发
    的工作"，整局失败。卡住 / 失败 / 没进展的如实报告都应交给规划器，带上步骤的说明。

    **Mutation**: drop ``OutcomeRecorded`` from the sources → no request, red."""

    summary = "工作区里没有 wordfreq.py，pytest 收集阶段 ModuleNotFoundError，本步骤无法完成"

    def blocked(request: Any) -> Any:
        package = package_of(request)
        contract = package.get("task_contract", {})
        envelope = {
            "task_id": contract.get("task_id", ""),
            "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
            "outcome": "blocked", "summary": summary, "claims": [], "evidence": [], "artifacts": [],
            "outputs": {}, "proposed_tasks": [], "used_knowledge": [], "risks": [], "cost": {},
        }
        return "<result_envelope>" + json.dumps(envelope, ensure_ascii=False) + "</result_envelope>"

    async def case():
        provider = LayeredScriptedProvider(planner=seed_planner, worker=blocked)
        async with committed(tmp_path, key="h4-blocked-live", provider=provider) as seed:
            loop, mission, product = seed.loop, seed.mission, seed.product
            provider.held = {"planner"}  # the repair round stays with the planner
            released = provider.release
            provider.release = asyncio.Event()
            released.set()  # the executor answers: blocked

            def reported() -> list[Any]:
                return [entry for entry in pending_requests(loop.store, mission.id)
                        if entry["request"]["trigger_source"] == "WORKER_REJECT"]

            await run_until(product, lambda: bool(reported()), timeout=30)
            requests = reported()
            assert len(requests) == 1
            request = requests[0]["request"]
            assert summary in json.dumps(request, ensure_ascii=False)
            assert not collect_triggers(loop, loop.store.get_mission(mission.id)), \
                "the same report opens one request only"

    asyncio.run(case())
