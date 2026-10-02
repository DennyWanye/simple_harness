"""Real Plan Commit must schedule the independent review required by scoped acceptance.

The commits run on the product's deployment: the main loop proposed a method, had it
independently reviewed and opened the adoption round (``h1i_seed.reviewed``); the
planner's adoption reply goes through the production collector.
"""
import asyncio

import pytest
from h1i_seed import plan_reply, reviewed

from agent_orchestrator.contracts.htn import TaskForm
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore


def test_real_new_protocol_commit_schedules_independent_review_for_each_leafs_own_criteria(tmp_path):
    async def case():
        async with reviewed(tmp_path, key="scoped-critic") as ((loop, mission, _world, _root, dispatch, _product), intent, provider):
            asked = len(provider.asked)
            await loop._collect_plan_decision(intent, object(), mission,
                plan_reply(intent.config["planning_package"]), dispatch)
            network = dispatch.network(mission.id)
            assert network.plan_revision == 1
            from agent_orchestrator.orchestrator.scoped_content_review import (
                task_content_prompt_scope,
            )
            leaves = 0
            for occurrence in network.occurrences:
                if occurrence.form != TaskForm.PRIMITIVE:
                    continue
                leaves += 1
                task = loop.store.get_task(str(occurrence.task_id))
                assert task is not None and "critic_review" in task.verification_policy
                # The independent review judges this leaf's own criteria, as the content
                # scope the plan froze for it names them.
                scope = task_content_prompt_scope(loop.store, mission.id, task.id)
                assert scope["purpose"] == "TASK_CONTENT"
                assert [c["criterion_id"] for c in scope["criteria"]] == list(
                    network.binding_for_task(occurrence.task_id).requirement_refs)
            assert leaves == 1
            assert len(provider.asked) == asked  # the commit asked no model
    asyncio.run(case())


def _goals_without_applicable_method(world):
    """片 A：``goals_needing_method`` 已删；同一事实改从候选过滤结果读。"""
    return tuple(
        occurrence
        for occurrence, result in world.dispatch.method_candidates(world.mission.id).items()
        if not result.applicable
    )


def test_suspended_method_is_not_offered_as_a_candidate(tmp_path):
    from test_htn_end_to_end import build_world

    from agent_orchestrator.contracts.htn import MissionRef
    from agent_orchestrator.storage.htn_store import HtnStore
    world = build_world(tmp_path, key="registry-dispatch-scope")
    reference = world.contract.method_ref()
    assert world.dispatch.method_applicability(world.mission.id)
    assert _goals_without_applicable_method(world) == ()
    registration = world.env.registry.suspend(reference, reason="confirmed counterexample")
    HtnStore(world.store).set_method_registration(registration)
    assert world.dispatch.method_applicability(world.mission.id) == ()
    assert _goals_without_applicable_method(world)
    # Immutable definitions stay available to historical instances.
    assert world.env.registry.definition(reference) == world.contract
    from agent_orchestrator.planning.htn.planner_package import method_rows
    assert method_rows(world.env.registry, ["plan.goal"], mission_id=world.mission.id) == ((), 0)
    registration = world.env.registry.reinstate(reference,
        mission_id=MissionRef("a-different-trial-mission"), reason="scoped re-evaluation")
    HtnStore(world.store).set_method_registration(registration)
    assert world.dispatch.method_applicability(world.mission.id) == ()
    assert _goals_without_applicable_method(world)
    world.env.registry.allow_evaluation_trials(reference, [world.mission.id])
    assert world.dispatch.method_applicability(world.mission.id)
    assert _goals_without_applicable_method(world) == ()


@pytest.mark.parametrize("with_grant", [True, False])
def test_unconditional_method_can_commit_only_with_actual_planning_grant(tmp_path, with_grant):
    """The reviewed method has no applicability conditions and names no approval; its
    applicability report never carries planning authority.  The adoption commits only
    while the round's grant stands (the person revokes it in the other case)."""
    from agent_orchestrator.storage.htn_store import HtnStore

    async def case():
        async with reviewed(tmp_path, key=f"unconditional-method-{with_grant}") as ((loop, mission, _world, _root, dispatch, product), intent, provider):
            selection = intent.config["planning_package"]["method_selection"][0]
            assert selection["applicable"]
            report, = dispatch.method_applicability(mission.id)
            assert report.report.applicable and not report.report.authorization.allowed
            if not with_grant:
                binding = PlanningAdmissionStore(loop.store).get_request_binding(intent.intent_id)
                product.control.planning_authorization({
                    "operation": "revoke", "grant_id": binding["grant_id"],
                    "expected_revision": int(binding["grant_revision"]),
                    "command_id": "person-revoke-unconditional", "reason": "withdrawn"})
            asked = len(provider.asked)
            await loop._collect_plan_decision(intent, object(), mission,
                plan_reply(intent.config["planning_package"]), dispatch)
            plan = HtnStore(loop.store).active_plan_revision(mission.id)
            assert (plan is not None and plan.revision == 1) if with_grant else plan is None
            assert len(provider.asked) == asked
    asyncio.run(case())
