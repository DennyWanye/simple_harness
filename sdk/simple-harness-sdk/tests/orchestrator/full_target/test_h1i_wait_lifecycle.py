"""The planner's WAIT, end to end on the product's deployment (HTN 补齐阶段 A′).

The plan is two steps in order (``first`` then ``second``), committed and dispatched by
the main loop (``h1i_seed.committed``) while the executor's call for ``first`` is held:
``first`` is an ACTIVE Task under its exact semantic contract, the waited producer.  The
planner's WAIT round is opened and collected through the real ``_create_planner_intent``
/ ``_collect_plan_decision`` entry; ``first`` reaches its terminal state the product's
way (the executor answers, its independent review accepts), ``second`` then starts and
its executor's call is held, so the Mission stays open with no root review in flight,
and the loop's own cycle wakes the WAIT.  No Task row is written by hand.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from h1i_seed import (
    CONFIG,
    committed,
    events,
    resume,
    root_task,
    run_until,
    seeded,
)
from h1i_seed import planner as seed_planner

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.contracts import TaskStatus
from agent_orchestrator.contracts.planning_decisions import (
    PlanningDecisionEnvelopeV1,
    PlanningDecisionStatus,
)
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.hierarchical_dispatch import append_hierarchical_event
from agent_orchestrator.planning.decision_codec import serialize_planning_decision
from agent_orchestrator.planning.htn.planner_package import package_hash
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.store import InjectedCrash
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    broken_result,
    decision,
    worker_reply,
)

_WAIT_FIXTURE = Path(__file__).parent / "fixtures" / "planning_decision_v1" / "valid" / "wait.json"


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


TWO = ("file:NOTES.md", "file:SUMMARY.md")


def _two_step_method(context: dict[str, Any]) -> dict[str, Any]:
    request = context["request"]
    step = next(item for item in request["operators"]
                if str(item["task_type_ref"]["id"]).endswith("prepare-delivery"))
    first, second = [item["id"] for item in request["criterion_evidence"]]
    identity = request["new_method_identity"]

    def primitive(local_id: str) -> dict[str, Any]:
        return {"local_id": local_id, "task_type_ref": step["task_type_ref"], "form": "primitive",
                "arguments": {}, "required_capabilities": list(step["required_capabilities"]),
                "obligation_relation": "refines_parent"}

    return {
        "schema_version": 1, "method_id": identity["method_id"], "method_version": identity["method_version"],
        "goal_type_ref": request["goal_type_ref"],
        "parameter_schema_ref": request["goal_signature"]["parameter_schema_ref"],
        "output_schema_ref": request["goal_signature"]["output_schema_ref"],
        "applicable_when": [], "exploration_assumptions": [],
        "steps": [primitive("first"), primitive("second")],
        "ordering": [{"before": "first", "after": "second"}],
        "required_capabilities": [], "expected_effects": [],
        "composition": {
            "criterion_links": [
                {"parent_criterion_id": first, "child_step": "first", "child_criterion_id": first,
                 "evidence_requirement": "first 这一步完成第一条要求"},
                {"parent_criterion_id": second, "child_step": "second", "child_criterion_id": second,
                 "evidence_requirement": "second 这一步完成第二条要求"},
            ],
            "outputs": {}, "finalizer_step": "second", "independent_review_required": True,
        },
        "basis_refs": [],
    }


def _two_steps(request: Any) -> Any:
    """Propose the two-step method; once it is reviewed, adopt it (``h1i_seed.planner``)."""

    package = package_of(request)
    selection = (package.get("method_selection") or [{}])[0]
    contexts = package.get("method_proposal_contexts") or []
    if contexts and not selection.get("applicable"):
        return decision(contexts[0]["subject_key"], "PROPOSE_METHOD",
                        {"method_proposal": {"method": _two_step_method(contexts[0]),
                                             "rationale": "先写第一份，再写第二份。"}},
                        "两步依次完成两条要求。")
    return seed_planner(request)


def _provider(*, planner: Any = _two_steps, worker: Any = None) -> LayeredScriptedProvider:
    """The executor answers ``first`` (once released) and is held again for ``second``."""

    holder: dict[str, Any] = {}

    def first_only(request: Any) -> Any:
        reply = worker_reply(request)
        if not isinstance(reply, tuple):  # the result, not a file write: hold what follows
            holder["provider"].held.add("worker")
        return reply

    provider = LayeredScriptedProvider(planner=planner, worker=worker or first_only)
    holder["provider"] = provider
    return provider


def _wait_body(package: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    body = json.loads(_WAIT_FIXTURE.read_text(encoding="utf-8"))
    body["subject_key"] = package["planning_subjects"][0]["subject_key"]
    body["payload"]["wait_for"] = [target]
    return body


def _wait_reply(package: dict[str, Any], task_id: str) -> tuple[str, dict[str, Any]]:
    """WAIT for the visible ref of ``task_id`` (the first of its refs the package lists)."""

    visible = next(
        ref for ref in package["visible_refs"] if ref["kind"] == "task" and ref["id"] == task_id
    )
    body = _wait_body(package, visible)
    return serialize_planning_decision(PlanningDecisionEnvelopeV1.from_json(body)), visible


def _steps(loop: Orchestrator, mission: Any) -> list[Any]:
    """The two step Tasks, ``first`` (writes NOTES.md) then ``second`` (SUMMARY.md)."""

    steps = [task for task in loop.store.list_tasks(mission.id) if task.id != root_task(mission.id)]
    assert len(steps) == 2, steps
    first, second = sorted(steps, key=lambda task: task.outputs)
    assert (first.outputs, second.outputs) == (("NOTES.md",), ("SUMMARY.md",)), steps
    return [first, second]


def _first(loop: Orchestrator, mission: Any) -> Any:
    return _steps(loop, mission)[0]


def _grant(loop: Orchestrator, mission: Any, product: Any, intent: Any, command_id: str) -> None:
    PlanningAuthorizationApi(
        loop.store, tenant_id=mission.tenant_id, principal=product.deployment.principal
    ).issue(mission.id, command_id=command_id, request_id=intent.intent_id)


async def _open_round(loop: Orchestrator, mission: Any, product: Any, command_id: str) -> Any:
    intent = await loop._create_planner_intent(
        mission.id, ordinal=loop._next_planning_ordinal(mission.id)
    )
    _grant(loop, mission, product, intent, command_id)
    return intent


async def _register_wait(loop: Orchestrator, mission: Any, dispatch: Any, product: Any):
    """Open a planning round while ``first`` runs and answer it with WAIT for it."""

    first = _first(loop, mission)
    assert first.status is TaskStatus.ACTIVE
    opener = await _open_round(loop, mission, product, "grant-wait")
    reply, visible = _wait_reply(opener.config["planning_package"], first.id)
    await loop._collect_plan_decision(opener, object(), mission, reply, dispatch)
    return opener, reply, visible


def _planner_intents(loop: Orchestrator, mission_id: str) -> list[Any]:
    return [
        i
        for i in loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
        if i.mission_id == mission_id
        and i.kind == "plan"
    ]


def _plan(tmp_path: Path, key: str, **kwargs: Any) -> Any:
    return committed(tmp_path, key=key, provider=kwargs.pop("provider", None) or _provider(),
                     criteria=TWO, **kwargs)


async def _first_finishes_and_wakes(product: Any) -> None:
    """``first`` is answered, reviewed and accepted; the planner the loop wakes is held.
    Run until it was asked."""

    asked = resume(product, hold=("planner",))
    provider = product.provider
    await run_until(product, lambda: "planner" in provider.asked[asked:])


async def _first_completed(product: Any, loop: Orchestrator, mission: Any) -> Any:
    """Run until ``first`` is COMPLETED and ``second``'s executor call is held."""

    resume(product, hold=("planner",))
    await run_until(product, product.provider.entered.is_set)
    first, second = _steps(loop, mission)
    assert first.status is TaskStatus.COMPLETED, first.status
    assert second.status is TaskStatus.ACTIVE, second.status
    return first


def test_wait_registration_is_durable_and_does_not_add_inflight_work(tmp_path: Path) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-register") as (loop, mission, _world, _root, dispatch, product):
            inflight_before = {i.intent_id for i in loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")}
            evaluated_before = len(events(loop, mission.id, "PlanningDecisionEvaluated"))
            opener, _reply, visible = await _register_wait(loop, mission, dispatch, product)
            evaluated = events(loop, mission.id, "PlanningDecisionEvaluated")
            registered = events(loop, mission.id, "PlanningWaitRegistered")
            assert len(evaluated) == evaluated_before + 1
            assert evaluated[-1].payload["status"] == str(PlanningDecisionStatus.NO_STATE_CHANGE)
            assert len(registered) == 1 and registered[0].payload["wait_for"] == [visible]
            assert loop._has_pending_planning_waits(mission.id)
            # The WAIT is durable external work: it puts nothing in flight of its own;
            # what is in flight is exactly the leaf's held turn from before the round.
            inflight_after = {i.intent_id for i in loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")}
            assert opener.intent_id not in inflight_after
            assert inflight_after == inflight_before

    asyncio.run(case())


def test_wait_ignores_fake_event_and_wakes_once_from_exact_terminal_task(tmp_path: Path) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-store") as (loop, mission, _world, _root, dispatch, product):
            _opener, _reply, visible = await _register_wait(loop, mission, dispatch, product)
            # An event that merely mentions the target is no completion evidence.
            append_hierarchical_event(
                loop.store,
                "TargetCompleted",
                mission.id,
                key="fake-target",
                payload={"planning_ref": visible},
            )
            assert not await loop._wake_planning_waits()
            await _first_finishes_and_wakes(product)
            assert _steps(loop, mission)[0].status is TaskStatus.COMPLETED
            assert (
                len(events(loop, mission.id, "PlanningWaitWoken"))
                == len(_planner_intents(loop, mission.id))
                == 1
            )
            assert not await loop._wake_planning_waits()

    asyncio.run(case())


def test_wait_target_terminal_before_registration_wakes(tmp_path: Path) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-first") as (loop, mission, _world, _root, dispatch, product):
            leaf = await _first_completed(product, loop, mission)
            opener = await _open_round(loop, mission, product, "grant-wait-first")
            reply, _visible = _wait_reply(opener.config["planning_package"], leaf.id)
            await loop._collect_plan_decision(opener, object(), mission, reply, dispatch)
            assert len(events(loop, mission.id, "PlanningWaitRegistered")) == 1
            assert await loop._wake_planning_waits()
            assert (
                len(events(loop, mission.id, "PlanningWaitWoken"))
                == len(_planner_intents(loop, mission.id))
                == 1
            )

    asyncio.run(case())


def test_wait_wake_fault_rolls_back_woken_and_planner_intent_together(tmp_path: Path) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-wake-fault") as (loop, mission, _world, _root, dispatch, product):
            await _register_wait(loop, mission, dispatch, product)
            # The crash hits the loop's own wake, in the cycle after the leaf completes.
            loop.store.arm("planning_wait_before_wake_commit")
            with pytest.raises(InjectedCrash, match="planning_wait_before_wake_commit"):
                await _first_finishes_and_wakes(product)
            assert _steps(loop, mission)[0].status is TaskStatus.COMPLETED
            assert not events(loop, mission.id, "PlanningWaitWoken") and not _planner_intents(
                loop, mission.id
            )
            assert loop._has_pending_planning_waits(mission.id)
            assert await loop._wake_planning_waits()
            assert (
                len(events(loop, mission.id, "PlanningWaitWoken"))
                == len(_planner_intents(loop, mission.id))
                == 1
            )

    asyncio.run(case())


def test_wait_registration_fault_rolls_back_terminal_decision_and_registration(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-register-fault") as (loop, mission, _world, _root, dispatch, product):
            opener = await _open_round(loop, mission, product, "grant-wait-fault")
            reply, _visible = _wait_reply(opener.config["planning_package"], _first(loop, mission).id)
            evaluated_before = len(events(loop, mission.id, "PlanningDecisionEvaluated"))
            loop.store.arm("planning_wait_before_registration_commit")
            with pytest.raises(InjectedCrash, match="planning_wait_before_registration_commit"):
                await loop._collect_plan_decision(opener, object(), mission, reply, dispatch)
            assert not events(loop, mission.id, "PlanningWaitRegistered")
            assert len(events(loop, mission.id, "PlanningDecisionEvaluated")) == evaluated_before
            assert loop.store.get_intent(opener.intent_id).state == "PENDING"
            await loop._collect_plan_decision(opener, object(), mission, reply, dispatch)
            assert len(events(loop, mission.id, "PlanningWaitRegistered")) == 1

    asyncio.run(case())


def test_wait_reopens_from_durable_store(tmp_path: Path) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-reopen") as (first, mission, _world, _root, dispatch, product):
            leaf = await _first_completed(product, first, mission)
            opener = await _open_round(first, mission, product, "grant-wait-reopen")
            reply, _visible = _wait_reply(opener.config["planning_package"], leaf.id)
            await first._collect_plan_decision(opener, object(), mission, reply, dispatch)
            assert len(events(first, mission.id, "PlanningWaitRegistered")) == 1
            mission_id = mission.id
        # A new process on the same library, with the product's deployment: the WAIT is
        # read back from the store, never re-seeded.
        async with product_world(
            tmp_path / "root", RoleScriptedProvider({}), auto=False, **CONFIG
        ) as world:
            reopened = world.loop
            assert reopened._has_pending_planning_waits(mission_id)
            assert await reopened._wake_planning_waits()
            assert (
                len(events(reopened, mission_id, "PlanningWaitWoken"))
                == len(_planner_intents(reopened, mission_id))
                == 1
            )
            assert not await reopened._wake_planning_waits()

    asyncio.run(case())


def test_wait_for_a_target_with_no_running_producer_is_rejected_without_registration(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        async with seeded(tmp_path, key="h1i-wait-unsupported") as (loop, mission, _world, _root, dispatch, product):
            opener = await _open_round(loop, mission, product, "grant-wait-unsupported")
            package = opener.config["planning_package"]
            # The root's obligation is a valid, visible protocol ref, but before any plan
            # nothing runs under it that a WAIT may suspend for; this is admission
            # coverage, not a codec refusal.
            obligation = next(ref for ref in package["visible_refs"] if ref["kind"] == "obligation")
            body = _wait_body(package, obligation)
            await loop._collect_plan_decision(
                opener,
                object(),
                mission,
                f"<planning_decision>{json.dumps(body)}</planning_decision>",
                dispatch,
            )
            assert not events(loop, mission.id, "PlanningWaitRegistered")
            evaluated = events(loop, mission.id, "PlanningDecisionEvaluated")[-1]
            assert evaluated.payload["status"] == str(PlanningDecisionStatus.REJECTED)
            assert evaluated.payload["rejection_codes"] == ["PARAMETER_INVALID"]

    asyncio.run(case())


def test_repeated_satisfied_wait_stops_instead_of_registering_a_busy_loop(tmp_path: Path) -> None:
    """The planner answers the round its WAIT woke with the same WAIT again (the
    target is already satisfied): the loop stops the Mission instead of waking forever."""

    waited: dict[str, str] = {}

    def planner(request: Any) -> Any:
        if "id" in waited:
            return _wait_reply(package_of(request), waited["id"])[0]
        return _two_steps(request)

    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-repeat", provider=_provider(planner=planner)) as (loop, mission, _world, _root, dispatch, product):
            await _register_wait(loop, mission, dispatch, product)
            waited["id"] = _first(loop, mission).id
            resume(product)
            await run_until(product, lambda: loop.store.get_mission(mission.id).status.name == "FAILED")
            assert len(events(loop, mission.id, "PlanningWaitRegistered")) == 2
            assert len(events(loop, mission.id, "PlanningWaitWoken")) == 1
            assert len(_planner_intents(loop, mission.id)) == 0
            report = loop.store.get_mission(mission.id).final_report
            assert report["stop_reason"] == "planning_failed"
            assert report["detail"]["reason"] == "repeated_wait_without_progress"

    asyncio.run(case())


def test_wait_woken_by_a_completed_task_names_its_settled_state(tmp_path: Path) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-completed") as (loop, mission, _world, _root, dispatch, product):
            _opener, _reply, visible = await _register_wait(loop, mission, dispatch, product)
            await _first_finishes_and_wakes(product)
            woken = events(loop, mission.id, "PlanningWaitWoken")[-1]
            assert woken.payload["settled_tasks"] == {visible["id"]: str(TaskStatus.COMPLETED)}

    asyncio.run(case())


def test_wait_wakeup_freezes_terminal_task_outcome_in_new_protocol_package(tmp_path: Path) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-package") as (loop, mission, _world, _root, dispatch, product):
            opener, _reply, visible = await _register_wait(loop, mission, dispatch, product)
            opener_hash = loop.store.connection.execute(
                "SELECT package_hash FROM planning_requests WHERE intent_id = ?",
                (opener.intent_id,),
            ).fetchone()[0]
            await _first_finishes_and_wakes(product)
            task = loop.store.get_task(visible["id"])
            assert task.status is TaskStatus.COMPLETED

            intent = _planner_intents(loop, mission.id)[0]
            package = intent.config["planning_package"]
            row = next(
                item
                for item in package["views"]["goals"]
                if item["task_id"] == visible["id"]
            )
            assert row["task_status"] == str(TaskStatus.COMPLETED)
            assert row["task_version"] == task.version
            assert row["occurrence_outcome"] == "ACCEPTED"
            assert str(TaskStatus.COMPLETED) in intent.config["message"]["content"]

            bound_hash = loop.store.connection.execute(
                "SELECT package_hash FROM planning_requests WHERE intent_id = ?",
                (intent.intent_id,),
            ).fetchone()[0]
            assert bound_hash == package_hash(package)
            assert bound_hash != opener_hash

    asyncio.run(case())


def test_registered_wait_tolerates_active_to_ready_retry_without_stopping(tmp_path: Path) -> None:
    """``first``'s result is malformed once: the system redoes it in place (the Task goes
    back to READY and runs again) by its own committed retry decision.  The registered
    WAIT neither wakes nor stops the Mission; the system's newer decision supersedes it
    (only the latest accepted decision suspends the planner)."""

    holder: dict[str, Any] = {}

    def broken_once(request: Any) -> Any:
        reply = broken_result(request)
        if not isinstance(reply, tuple):  # the result, not a file write: hold the redo
            holder["provider"].held.add("worker")
        return reply

    async def case() -> None:
        provider = _provider(worker=broken_once)
        holder["provider"] = provider
        async with _plan(tmp_path, "h1i-wait-retry-ready", provider=provider) as (loop, mission, _world, _root, dispatch, product):
            _opener, _reply, visible = await _register_wait(loop, mission, dispatch, product)
            asked = resume(product, hold=("planner",))
            await run_until(product, provider.entered.is_set)
            assert events(loop, mission.id, "ResultRejected")
            again = loop.store.get_task(visible["id"])
            assert len(loop.store.list_attempts(again.id)) == 2
            assert again.status in {TaskStatus.READY, TaskStatus.ACTIVE}
            assert "planner" not in provider.asked[asked:]  # the redo is the system's own
            latest = events(loop, mission.id, "PlanningDecisionEvaluated")[-1].payload
            assert (latest["status"], latest["decision_type"]) == ("COMMITTED", "REPAIR")
            assert not events(loop, mission.id, "PlanningWaitWoken")
            assert loop._pending_planning_wait(mission.id) is None
            assert not await loop._wake_planning_waits()
            assert loop.store.get_mission(mission.id).status.name not in {"FAILED", "CANCELLED"}

    asyncio.run(case())


def test_wait_rejects_blocked_target_that_has_no_inflight_work(tmp_path: Path) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-blocked") as (loop, mission, _world, _root, dispatch, product):
            # The root is a compound occurrence waiting for its children: BLOCKED, and no
            # turn of its own is running.
            root = loop.store.get_task(root_task(mission.id))
            assert root is not None and root.status is TaskStatus.BLOCKED
            opener = await _open_round(loop, mission, product, "grant-wait-blocked")
            reply, _visible = _wait_reply(opener.config["planning_package"], root.id)
            await loop._collect_plan_decision(opener, object(), mission, reply, dispatch)
            assert not events(loop, mission.id, "PlanningWaitRegistered")

    asyncio.run(case())


def test_wait_unknown_or_foreign_mission_ref_never_passes_from_task_existence(
    tmp_path: Path,
) -> None:
    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-negative") as (loop, mission, _world, _root, _dispatch, product):
            leaf = await _first_completed(product, loop, mission)
            semantic = HtnStore(loop.store).task_semantics_of(mission.id, leaf.id)
            assert semantic is not None

            def ref(kind: str) -> SimpleNamespace:
                return SimpleNamespace(
                    kind=kind,
                    id=leaf.id,
                    semantic_revision=semantic.contract_revision,
                    content_hash=semantic.content_hash(),
                )

            from agent_orchestrator.contracts.planning_decisions import PlanningRefV1

            exact = PlanningRefV1.from_json({"kind": "task", "id": leaf.id,
                                             "semantic_revision": int(semantic.contract_revision),
                                             "content_hash": semantic.content_hash()})
            assert loop._planning_wait_ref_satisfied(mission, exact)
            assert not loop._planning_wait_ref_satisfied(mission, ref("unknown"))
            assert not loop._planning_wait_ref_satisfied(
                replace(mission, id="foreign-mission"), ref("task")
            )

    asyncio.run(case())


def test_wait_accepts_a_task_ref_carrying_its_binding_contract_hash(tmp_path: Path) -> None:
    """2026-09-30 真机（方案 B 第 1 局）：规划器的引用清单里同一步有两个引用——分层语义的
    内容哈希（``_network_authorities``）与共享候选里的合同哈希（``graph_repair_sources``）。
    规划器选了后者回 WAIT，"能不能等"只认前者，两次被拒、白花两轮规划次数。两者指的是
    同一步、同一修订的同一份当前绑定：能等、等到终态时能被唤醒；错的哈希仍然拒绝。"""
    from agent_orchestrator.contracts.planning_decisions import PlanningRefV1

    async def case() -> None:
        async with _plan(tmp_path, "h1i-wait-contract-hash") as (loop, mission, _world, _root, _dispatch, product):
            leaf = _first(loop, mission)
            assert leaf.status is TaskStatus.ACTIVE
            semantics = HtnStore(loop.store).task_semantics_of(mission.id, leaf.id)
            assert semantics is not None and semantics.contract_hash != semantics.content_hash()
            visible = {"kind": "task", "id": leaf.id, "semantic_revision": int(semantics.contract_revision)}
            by_contract = PlanningRefV1.from_json({**visible, "content_hash": semantics.contract_hash})
            by_content = PlanningRefV1.from_json({**visible, "content_hash": semantics.content_hash()})
            wrong = PlanningRefV1.from_json({**visible, "content_hash": "0" * 64})
            assert loop._planning_wait_ref_waitable(mission, by_content)
            assert loop._planning_wait_ref_waitable(mission, by_contract)
            assert not loop._planning_wait_ref_waitable(mission, wrong)
            assert not loop._planning_wait_ref_satisfied(mission, by_contract)
            await _first_completed(product, loop, mission)
            assert loop._planning_wait_ref_satisfied(mission, by_contract)
            assert loop._planning_wait_ref_satisfied(mission, by_content)
            assert not loop._planning_wait_ref_satisfied(mission, wrong)

    asyncio.run(case())
