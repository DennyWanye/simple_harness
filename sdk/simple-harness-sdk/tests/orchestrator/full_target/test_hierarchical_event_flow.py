# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3b red tests: the hierarchical mode, wired from the Planner's reply to the plan.

Six properties, each of which would be silently wrong if the wiring were merely
"plausible":

1. **One reply, one revision.**  A scripted ``<plan_revision_proposal>`` becomes a
   committed ``PlanRevision`` through ``parse → compile → commit_plan_revision``,
   and the ``PlanRevisionCommitted`` event lands in the store.
2. **A refusal is recompiled once, not forever.**  ``READ_SET_STALE`` on the first
   delivery is answered by compiling again *against the freshly read snapshot*;
   still refused after the bound, the round records ``PlanCommitRefused`` and
   stops.  Nothing is rebased (ADR-13 / C19).
3. **A compound never enters the Worker path.**  The interception is ``form`` from
   the semantic binding, so writing ``TaskStatus.READY`` onto the compound Task
   changes nothing, and ``NEEDS_REFINEMENT`` is recorded.
4. **Reading goes through the projection.**  ``list`` / ready / running / terminal /
   root review are computed from ``TaskNetworkSnapshot`` + ``evaluate_readiness``;
   the suite mutates ``Task.status`` to ``READY`` and asserts every one of those
   answers is unchanged (§18.5 rule 4, TG §7).
5. **A missing semantic binding is corruption.**  ``GraphIntegrityError``, never a
   legacy fallback (§18.5).
6. (2026-10-02) The flat mode was removed; its "legacy is untouched" golden went
   with it.

The last section is the mutation self-check: each mutant is a plausible wrong
implementation, and an assertion the real tests make must catch it.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))
_ORCH_TESTS = Path(__file__).resolve().parents[1]
if str(_ORCH_TESTS) not in sys.path:
    sys.path.insert(0, str(_ORCH_TESTS))

from htn_world import Env, method, out, param, step, task_binding  # noqa: E402

from agent_orchestrator.artifacts.versioning import UpstreamInput  # noqa: E402
from agent_orchestrator.contracts import (  # noqa: E402
    Budget,
    MissionStatus,
    Task,
    TaskStatus,
)
from agent_orchestrator.contracts.htn import (  # noqa: E402
    OccurrenceId,
    OccurrenceSpec,
    Requiredness,
    TaskForm,
    TaskRef,
)
from agent_orchestrator.contracts.models import ContractError  # noqa: E402
from agent_orchestrator.contracts.obligations import Obligation  # noqa: E402
from agent_orchestrator.graph.eligibility import (  # noqa: E402
    OccurrenceOutcome,
    ReadinessReason,
)
from agent_orchestrator.graph.projection_validation import GraphIntegrityError  # noqa: E402
from agent_orchestrator.orchestrator import hierarchical_dispatch as module  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import (  # noqa: E402
    CommitService,
    MissionSpec,
)
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.orchestrator.hierarchical_dispatch import (  # noqa: E402
    COMPOUND_DISPLAY_STATUS,
    COMPOUND_PHASE_CHANGED,
    DISPATCH_INTERCEPTED,
    PLAN_INTEGRITY_FAILED,
    CompoundPhase,
    HierarchicalDispatch,
    PlanIntegrityError,
    next_compound_phase,
)
from agent_orchestrator.orchestrator.plan_commits import (  # noqa: E402
    HIERARCHICAL_SEMANTICS,
    PLAN_REVISION_COMMITTED,
    PlanCommitRejected,
    PlanPrincipal,
)
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.obligation_store import ObligationStore  # noqa: E402
from agent_orchestrator.storage.store import Store  # noqa: E402
from agent_orchestrator.testing.fixtures import RoleScriptedProvider  # noqa: E402
from scripted_plans import (  # noqa: E402
    apply_scripted_plan,
    approve_content_only_completion,
    plan_revision_proposal_step,
)
from simple_harness.agents import AgentTurnState  # noqa: E402

TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")
ROOT_TASK = "task-root"
ROOT_DUTY = "obl-root"
FUEL = 8
HEX_A = "a" * 64
HEX_OTHER = "f" * 64
HIERARCHICAL_GOAL = "交付一个可验收的层次计划"


# ------------------------------------------------------------------ the world builders
def _env(mission: str) -> Env:
    env = Env(mission=mission)
    env.register_type(
        "plan.goal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-root",),
        domain="plan",
    )
    env.register_type(
        "plan.leaf",
        parameters=(("subject", "string"),),
        outputs=(("result", "plan.result"),),
        capabilities=("plan.read",),
        domain="plan",
    )
    env.register_type(
        "plan.review",
        parameters=(("subject", "string"),),
        inputs=(("result", "plan.result", True),),
        outputs=(("verdict", "plan.verdict"),),
        capabilities=("plan.read",),
        domain="plan",
    )
    return env


def _outer(method_id: str = "plan.outer"):
    return method(
        method_id,
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step(
                "leaf",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
            step(
                "review",
                "plan.review",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "result": out("leaf", "result")},
                capabilities=("plan.read",),
            ),
        ),
        links=(("c-root", "review", "c-reviewed"),),
        finalizer="review",
    )


def _spec(key: str, *, mode: str) -> MissionSpec:
    return MissionSpec(
        goal=HIERARCHICAL_GOAL,
        success_criteria=("file:a.md",),
        tenant_id="tenant-p23b",
        idempotency_key=key,
        allowed_tools=TOOLS,
        budget=Budget(max_tokens=200_000, max_attempts=12),
        orchestration_semantics_version=mode,
    )


@dataclass
class World:
    service: CommitService
    mission: Any
    env: Env
    contract: Any
    binding: Any
    dispatch: HierarchicalDispatch
    principal: PlanPrincipal

    @property
    def store(self) -> Store:
        return self.service.store

    @property
    def semantics(self) -> HtnStore:
        return HtnStore(self.service.store)

    def reply(self, **changes: Any) -> str:
        return _proposal_text(self.contract, **changes)

    def plan(self, text: str | None = None, *, command_id: str = "cmd-a"):
        return apply_scripted_plan(self.dispatch,
            self.mission.id,
            text if text is not None else self.reply(),
            principal=self.principal,
            command_id=command_id,
        )

    def events(self, kind: str) -> list[Any]:
        return [e for e in self.store.list_events(self.mission.id) if e.type == kind]


def _proposal_text(contract: Any, **changes: Any) -> str:
    reference = contract.method_ref()
    base: dict[str, Any] = {
        "proposal_id": "prop-1",
        "expected_plan_revision": 0,
        "read_set": [
            {
                "kind": "method",
                "id": reference.method_id,
                "semantic_revision": reference.version,
                "content_hash": reference.content_hash,
            }
        ],
        "operations": [
            {
                "op": "refine",
                "goal_id": ROOT_TASK,
                "obligation_id": ROOT_DUTY,
                "method_ref": {
                    "id": reference.method_id,
                    "version": reference.version,
                    "content_hash": reference.content_hash,
                },
                "bindings": {},
            }
        ],
    }
    base.update(changes)
    return plan_revision_proposal_step(**base)


def _world(tmp_path, *, mode: str = HIERARCHICAL_SEMANTICS, key: str = "p23b", **kwargs) -> World:
    service = CommitService(Store.open(tmp_path / "orchestrator.db"))
    mission, _ = service.create_mission(_spec(key, mode=mode))
    env = _env(mission.id)
    contract = _outer()
    receipt = env.admit(contract)
    assert receipt.admitted, receipt.problems
    binding = task_binding(
        env,
        "plan.goal",
        task_id=ROOT_TASK,
        obligation=ROOT_DUTY,
        parameters={"subject": "alpha"},
    )
    if mode == HIERARCHICAL_SEMANTICS:
        ObligationStore(service.store).register(
            Obligation(
                obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
                mission_id=mission.id,
                requirement_refs=("req-1",),
                goal_signature_id="plan.goal",
            ),
            recursion_fuel=FUEL,
        )
        HtnStore(service.store).put_task_semantics(mission.id, binding)
        # 带协议绑定的任务：根要求先确认（纯内容），计划提交才能冻结完成范围。
        approve_content_only_completion(service, mission, binding, command_id=f"approve-{key}")
        HtnStore(service.store).register_method(
            contract, env.registry.registration(contract.method_ref())
        )
    return World(
        service=service,
        mission=mission,
        env=env,
        contract=contract,
        binding=binding,
        dispatch=HierarchicalDispatch(service.store, service, planning=env, **kwargs),
        principal=PlanPrincipal("manager-1", "mission", 0),
    )


# ====================================================================== one plan round
def test_a_scripted_planner_reply_commits_one_plan_revision(tmp_path):
    world = _world(tmp_path)
    outcome = world.plan()
    assert outcome.committed, outcome.refusals
    assert outcome.receipt.new_plan_revision == 1


def test_the_committed_revision_becomes_the_active_one(tmp_path):
    world = _world(tmp_path)
    world.plan()
    active = world.semantics.active_plan_revision(world.mission.id)
    assert active is not None and active.state == "ACTIVE"


def test_the_round_appends_the_plan_revision_committed_event(tmp_path):
    world = _world(tmp_path)
    world.plan()
    events = world.events(PLAN_REVISION_COMMITTED)
    assert len(events) == 1
    assert events[0].payload["proposal_id"] == "prop-1"


def test_the_round_records_the_proposal_id_in_the_commit_source(tmp_path):
    world = _world(tmp_path)
    outcome = world.plan()
    assert outcome.receipt.detail["source"]["proposal_id"] == "prop-1"


def test_the_first_round_compiles_against_the_seed_root_network(tmp_path):
    world = _world(tmp_path)
    seed = world.dispatch.network(world.mission.id)
    assert [str(spec.occurrence_id) for spec in seed.occurrences] == [ROOT_TASK]
    assert int(seed.plan_revision) == 0


def test_the_committed_network_is_read_back_from_the_store(tmp_path):
    world = _world(tmp_path)
    world.plan()
    network = world.dispatch.network(world.mission.id)
    assert int(network.plan_revision) == 1
    assert len(network.occurrences) == 3  # the root plus the method's two slots
    assert len(network.adopted_instance_ids) == 1


def test_the_read_back_network_keeps_the_root_as_the_only_root(tmp_path):
    world = _world(tmp_path)
    world.plan()
    network = world.dispatch.network(world.mission.id)
    assert [str(item) for item in network.root_occurrence_ids] == [ROOT_TASK]


def test_a_reply_claiming_an_authority_field_is_refused_at_the_boundary(tmp_path):
    world = _world(tmp_path)
    with pytest.raises(ContractError) as caught:
        world.plan(world.reply(extras={"manager_epoch": 3}))
    assert "manager_epoch" in str(caught.value)


def test_a_deployment_without_a_planning_world_refuses_visibly(tmp_path):
    world = _world(tmp_path)
    world.dispatch.planning = None
    with pytest.raises(ContractError) as caught:
        world.plan()
    assert "PlanningWorld" in str(caught.value)


def test_a_plan_commit_does_not_advance_the_integer_graph_version(tmp_path):
    """P2.3a: the serialisation point of this mode is the plan revision.

    The assembly passes ``base_graph_version`` because ADR-13 keeps the integer as
    the coarse gate, and must not treat it as a concurrency token: a Mission whose
    graph version never moves has to keep accepting plan revisions.
    """

    world = _world(tmp_path)
    before = int((world.mission.final_report or {}).get("graph_version") or 1)
    assert world.plan().committed
    after = world.store.get_mission(world.mission.id)
    assert int((after.final_report or {}).get("graph_version") or 1) == before


def test_a_second_identical_reply_does_not_produce_a_second_revision(tmp_path):
    """Re-refining an already refined goal is refused by the compiler, not committed.

    The idempotency of one *command* is P2.3a's property.  What this slice has to
    hold is the round above it: the same reply delivered twice compiles against the
    *new* snapshot, where the goal it names is no longer open, and that is a refusal
    — never a second plan revision that duplicates the work.
    """

    world = _world(tmp_path)
    assert world.plan().committed
    with pytest.raises(ContractError, match="exactly one open occurrence"):
        world.plan(command_id="cmd-b")
    assert len(world.events(PLAN_REVISION_COMMITTED)) == 1
    assert world.semantics.active_plan_revision(world.mission.id).revision == 1


# ======================================================== the compound dispatch gate
def _committed(tmp_path, *, demand: bool = False, **kwargs) -> World:
    world = _world(tmp_path, **kwargs)
    assert world.plan().committed
    if demand:
        _admit_demand(world)
    return world


def _admit_demand(world: World) -> None:
    """TG decision 9: an occurrence is selected only while a demand for its duty lives."""

    duties = ObligationStore(world.store)
    seen: set[str] = set()
    for spec in world.dispatch.network(world.mission.id).occurrences:
        duty = str(spec.obligation_id)
        if duty in seen or not duties.exists(world.mission.id, spec.obligation_id):
            continue
        seen.add(duty)
        account = duties.account(world.mission.id, spec.obligation_id)
        if not account.has_admitted_demand:
            world.service.admit_obligation_demand(
                world.mission.id,
                spec.obligation_id,
                principal="mission-submitter",
                requester={"kind": "mission_root"},
                evidence={"occurrence": str(spec.occurrence_id)},
            )


def _make_task(world: World, task_id: str, *, status: TaskStatus) -> None:
    """Give a semantic task a legacy Task row in a chosen display state.

    The point of every test that calls this is that the row is *display*: the new
    mode's answers have to be identical whatever it says.
    """

    from agent_orchestrator.contracts import Budget as TaskBudget
    from agent_orchestrator.contracts import Task

    existing = world.store.get_task(task_id)
    ordinal = 1 + len(world.store.list_tasks(world.mission.id))
    task = Task(
        id=task_id,
        mission_id=world.mission.id,
        parent_task_ids=(),
        dependency_ids=(),
        goal=f"goal of {task_id}",
        rationale="display row",
        success_criteria=("file:a.md",),
        verification_policy=("format_check",),
        allowed_tools=TOOLS,
        budget=TaskBudget(max_tokens=1_000, max_attempts=2),
        priority=1.0,
        status=status,
        version=1 if existing is None else existing.version + 1,
    )
    if existing is None:
        world.store.insert_task(task, ordinal=ordinal)
    else:
        world.store.update_task(task, expected_version=existing.version)


def test_a_compound_is_intercepted_before_any_worker_dispatch(tmp_path):
    world = _committed(tmp_path)
    intercepted = world.dispatch.intercept_worker_dispatch(world.mission.id, ROOT_TASK)
    assert intercepted is not None
    assert intercepted.reason == str(ReadinessReason.NEEDS_REFINEMENT)


def test_the_interception_is_recorded_as_an_event(tmp_path):
    world = _committed(tmp_path)
    world.dispatch.intercept_worker_dispatch(world.mission.id, ROOT_TASK)
    events = world.events(DISPATCH_INTERCEPTED)
    assert len(events) == 1
    assert events[0].payload["reason"] == "NEEDS_REFINEMENT"
    assert events[0].payload["form"] == "compound"


def test_the_interception_does_not_depend_on_the_legacy_status(tmp_path):
    world = _committed(tmp_path)
    _make_task(world, ROOT_TASK, status=TaskStatus.READY)
    intercepted = world.dispatch.intercept_worker_dispatch(world.mission.id, ROOT_TASK)
    assert intercepted is not None and intercepted.reason == "NEEDS_REFINEMENT"


def test_a_primitive_is_not_intercepted_by_this_gate(tmp_path):
    world = _committed(tmp_path)
    network = world.dispatch.network(world.mission.id)
    leaf = next(spec for spec in network.occurrences if spec.form is TaskForm.PRIMITIVE)
    assert world.dispatch.intercept_worker_dispatch(world.mission.id, str(leaf.task_id)) is None


def test_the_interception_creates_no_attempt_for_the_compound(tmp_path):
    world = _committed(tmp_path)
    _make_task(world, ROOT_TASK, status=TaskStatus.READY)
    world.dispatch.intercept_worker_dispatch(world.mission.id, ROOT_TASK)
    assert world.store.list_attempts(ROOT_TASK) == []


def test_a_task_without_a_semantic_binding_is_corruption_at_the_gate(tmp_path):
    world = _committed(tmp_path)
    with pytest.raises(GraphIntegrityError):
        world.dispatch.intercept_worker_dispatch(world.mission.id, "task-nowhere")


# ========================================================== the projection is the read
def test_the_ready_set_comes_from_the_execution_frontier(tmp_path):
    world = _committed(tmp_path)
    ready = world.dispatch.ready_occurrences(world.mission.id)
    assert ROOT_TASK not in {str(item) for item in ready}  # a compound is never ready


def test_writing_ready_onto_the_compound_task_changes_no_answer(tmp_path):
    world = _committed(tmp_path)
    before = (
        world.dispatch.ready_occurrences(world.mission.id),
        world.dispatch.running_occurrences(world.mission.id),
        world.dispatch.root_review_ready(world.mission.id),
        world.dispatch.terminal(world.mission.id),
    )
    _make_task(world, ROOT_TASK, status=TaskStatus.READY)
    after = (
        world.dispatch.ready_occurrences(world.mission.id),
        world.dispatch.running_occurrences(world.mission.id),
        world.dispatch.root_review_ready(world.mission.id),
        world.dispatch.terminal(world.mission.id),
    )
    assert before == after


def test_writing_completed_onto_every_task_does_not_make_the_mission_terminal(tmp_path):
    world = _committed(tmp_path)
    for spec in world.dispatch.network(world.mission.id).occurrences:
        _make_task(world, str(spec.task_id), status=TaskStatus.COMPLETED)
    assert world.dispatch.terminal(world.mission.id) is False


def test_the_compound_occurrence_is_never_counted_as_running_work(tmp_path):
    world = _committed(tmp_path)
    _make_task(world, ROOT_TASK, status=TaskStatus.ACTIVE)
    running = {str(item) for item in world.dispatch.running_occurrences(world.mission.id)}
    assert ROOT_TASK not in running


def test_the_typed_view_carries_the_legacy_status_as_diagnostics_only(tmp_path):
    world = _committed(tmp_path)
    _make_task(world, ROOT_TASK, status=TaskStatus.READY)
    views = {str(view.task_id): view for view in world.dispatch.occurrences(world.mission.id)}
    assert views[ROOT_TASK].legacy_status == "READY"
    report = world.dispatch.read(world.mission.id).reports[OccurrenceId(ROOT_TASK)]
    assert report.reason is ReadinessReason.NEEDS_REFINEMENT


def test_the_planning_frontier_holds_the_compound_that_still_needs_a_method(tmp_path):
    world = _world(tmp_path)
    view = world.dispatch.read(world.mission.id)
    assert view.planning_frontier.by_reason[ReadinessReason.NEEDS_REFINEMENT] == (
        OccurrenceId(ROOT_TASK),
    )


def test_the_occurrence_listing_is_the_projection_not_the_task_table(tmp_path):
    world = _committed(tmp_path)

    def listing() -> set[str]:
        return {str(view.occurrence_id) for view in world.dispatch.occurrences(world.mission.id)}

    listed = listing()
    _make_task(world, "task-extra", status=TaskStatus.READY)
    assert listing() == listed


def test_a_membership_naming_an_unbound_task_is_a_graph_integrity_error(tmp_path):
    world = _committed(tmp_path)
    world.semantics.insert_plan_membership(
        world.mission.id,
        1,
        OccurrenceSpec(
            occurrence_id=OccurrenceId("occ-orphan"),
            task_id=TaskRef("task-orphan"),
            obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
            form=TaskForm.PRIMITIVE,
            requiredness=Requiredness.OPTIONAL_AUTHORIZED,
        ),
        instance_id=None,
        adopted=True,
    )
    with pytest.raises(GraphIntegrityError):
        world.dispatch.network(world.mission.id)


def test_a_mission_with_no_semantic_binding_at_all_is_corruption(tmp_path):
    service = CommitService(Store.open(tmp_path / "orchestrator.db"))
    mission, _ = service.create_mission(_spec("bare", mode=HIERARCHICAL_SEMANTICS))
    dispatch = HierarchicalDispatch(service.store, service)
    with pytest.raises(GraphIntegrityError):
        dispatch.network(mission.id)


def test_the_root_review_waits_for_every_gating_child(tmp_path):
    world = _committed(tmp_path)
    assert world.dispatch.root_review_ready(world.mission.id) is False


def test_the_root_review_is_not_unlocked_by_completing_the_task_rows(tmp_path):
    world = _committed(tmp_path)
    for spec in world.dispatch.network(world.mission.id).occurrences:
        _make_task(world, str(spec.task_id), status=TaskStatus.COMPLETED)
    assert world.dispatch.root_review_ready(world.mission.id) is False


def test_the_root_review_is_unlocked_when_the_children_are_accepted(tmp_path):
    world = _committed(tmp_path)
    _accept_children(world)
    assert world.dispatch.root_review_ready(world.mission.id) is True


def test_accepting_only_one_child_does_not_unlock_the_root_review(tmp_path):
    world = _committed(tmp_path)
    _accept_children(world, limit=1)
    assert world.dispatch.root_review_ready(world.mission.id) is False


def _accept_children(world: World, *, limit: int | None = None) -> list[str]:
    """Accept the child occurrences through the real chain, producers first.

    A Mission on the completion protocol counts an Acceptance only when it came out
    of a stored, verified result judged against the frozen completion scope — a
    hand-written acceptance row is a state the system cannot reach, so each child is
    delivered and accepted the way the loop does it.
    """

    from dataclasses import dataclass as _dataclass

    from scripted_plans import seed_verified_result

    from agent_orchestrator.orchestrator.leaf_acceptance import (
        LayerOutcome,
        LeafAcceptanceAssembly,
    )
    from agent_orchestrator.runtime.output_blocks import PortClaim

    @_dataclass(frozen=True)
    class _Delivered:
        id: str
        path: str
        version: int = 1

    layers = (
        LayerOutcome("schema_check", "PASS"),
        LayerOutcome("rule_check", "PASS"),
        LayerOutcome("critic_review", "PASS"),
    )
    network = world.dispatch.network(world.mission.id)
    consumers = {edge.consumer_occurrence for edge in network.data_requirements}
    children = sorted(
        (
            spec
            for spec in network.occurrences
            if spec.occurrence_id not in set(network.root_occurrence_ids)
        ),
        key=lambda item: (item.occurrence_id in consumers, str(item.occurrence_id)),
    )
    assembly = LeafAcceptanceAssembly(world.store, world.service, dispatch=world.dispatch)
    accepted: list[str] = []
    for index, spec in enumerate(children):
        if limit is not None and index >= limit:
            break
        task_id = str(spec.task_id)
        now_ms = 1_000_000 + index * 100_000
        declared = world.dispatch.declared_output_ports_for(world.mission.id, task_id)
        items = tuple(
            _Delivered(f"artifact-{task_id}-{item['port']}", f"out/{item['port']}.json")
            for item in declared
        )
        claims = tuple(
            PortClaim(port_key=item["port"], path=items[position].path)
            for position, item in enumerate(declared)
        )
        stored = seed_verified_result(
            world.service, world.dispatch, world.mission.id, task_id,
            result_id=f"result-{task_id}", layers=layers, items=items, claims=claims,
            now_ms=now_ms,
        )
        assembly.accept(
            world.mission.id,
            task_id,
            result_id=f"result-{task_id}",
            layers=layers,
            artifacts=stored,
            producer_agent_ids=("agent-worker",),
            reviewer_agent_id="agent-critic",
            now_ms=now_ms,
            port_claims=claims,
        )
        accepted.append(str(spec.occurrence_id))
    return accepted


def test_an_accepted_child_reads_as_accepted_in_the_outcome_projection(tmp_path):
    world = _committed(tmp_path)
    accepted = set(_accept_children(world))
    outcomes = world.dispatch.read(world.mission.id).outcomes
    for occurrence, outcome in outcomes.items():
        if str(occurrence) in accepted:
            assert outcome is OccurrenceOutcome.ACCEPTED


def test_a_completed_task_without_an_acceptance_is_not_accepted(tmp_path):
    world = _committed(tmp_path)
    network = world.dispatch.network(world.mission.id)
    leaf = next(spec for spec in network.occurrences if spec.form is TaskForm.PRIMITIVE)
    _make_task(world, str(leaf.task_id), status=TaskStatus.COMPLETED)
    outcomes = world.dispatch.read(world.mission.id).outcomes
    assert outcomes[leaf.occurrence_id] is OccurrenceOutcome.SETTLED_OTHER


# ================================================================ the compound phases
def test_an_unrefined_compound_is_planning_ready(tmp_path):
    world = _world(tmp_path)
    phases = world.dispatch.advance_compound_phases(world.mission.id)
    assert phases[OccurrenceId(ROOT_TASK)] is CompoundPhase.PLANNING_READY


def test_a_refined_compound_waits_for_its_children(tmp_path):
    world = _committed(tmp_path)
    phases = world.dispatch.advance_compound_phases(world.mission.id)
    assert phases[OccurrenceId(ROOT_TASK)] is CompoundPhase.WAITING_CHILDREN


def test_a_compound_whose_children_are_accepted_enters_composition_review(tmp_path):
    world = _committed(tmp_path)
    _accept_children(world)
    phases = world.dispatch.advance_compound_phases(world.mission.id)
    assert phases[OccurrenceId(ROOT_TASK)] is CompoundPhase.COMPOSITION_REVIEW


def test_the_phase_pass_records_an_event_and_creates_no_attempt(tmp_path):
    world = _committed(tmp_path)
    world.dispatch.advance_compound_phases(world.mission.id)
    events = world.events(COMPOUND_PHASE_CHANGED)
    assert len(events) == 1
    assert events[0].payload["phase"] == "waiting_children"
    assert world.store.list_attempts(ROOT_TASK) == []


def test_the_phase_reducer_ignores_the_legacy_status_entirely(tmp_path):
    world = _committed(tmp_path)
    before = world.dispatch.advance_compound_phases(world.mission.id)
    for status in (TaskStatus.READY, TaskStatus.COMPLETED, TaskStatus.BLOCKED):
        _make_task(world, ROOT_TASK, status=status)
        assert world.dispatch.advance_compound_phases(world.mission.id) == before


def test_the_phase_reducer_refuses_a_primitive_occurrence(tmp_path):
    world = _committed(tmp_path)
    view = world.dispatch.read(world.mission.id)
    leaf = next(spec for spec in view.network.occurrences if spec.form is TaskForm.PRIMITIVE)
    with pytest.raises(ContractError):
        next_compound_phase(
            leaf,
            view.network,
            view.reports[leaf.occurrence_id],
            child_outcomes={},
            resolved=False,
        )


def test_every_phase_has_a_display_status(tmp_path):
    del tmp_path
    assert set(COMPOUND_DISPLAY_STATUS) == set(CompoundPhase)


def test_a_resolved_compound_is_resolution_committed(tmp_path):
    world = _committed(tmp_path)
    view = world.dispatch.read(world.mission.id)
    root = view.network.occurrence(OccurrenceId(ROOT_TASK))
    assert (
        next_compound_phase(
            root,
            view.network,
            view.reports[root.occurrence_id],
            child_outcomes={},
            resolved=True,
        )
        is CompoundPhase.RESOLUTION_COMMITTED
    )


# 2026-10-02 删旧平面模式第三刀：原"旧任务零回归"一节（平面任务事件字节黄金、旧路径不进分层
# 装配、平面任务不产新事件、模式开关默认 legacy、源码里 ``_new_mode`` 询问次数与
# ``merge_accepted`` 调用次数两枚结构钉）随平面模式删除。


# ===================================================================== inputs come from
# ===================================================================== the manifest
def test_a_task_with_no_data_requirement_starts_from_nothing(tmp_path):
    world = _committed(tmp_path)
    network = world.dispatch.network(world.mission.id)
    leaf = next(
        spec
        for spec in network.occurrences
        if spec.form is TaskForm.PRIMITIVE
        and not [
            item
            for item in network.data_requirements
            if item.consumer_occurrence == spec.occurrence_id
        ]
    )
    assert world.dispatch.attempt_inputs(world.mission.id, str(leaf.task_id)) == []


def test_a_consumer_whose_producer_has_not_finished_starts_from_nothing(tmp_path):
    world = _committed(tmp_path)
    network = world.dispatch.network(world.mission.id)
    consumers = {item.consumer_occurrence for item in network.data_requirements}
    assert consumers, "the method declares a DATA edge"
    for occurrence in consumers:
        spec = network.occurrence(occurrence)
        assert world.dispatch.attempt_inputs(world.mission.id, str(spec.task_id)) == []


def test_the_attempt_inputs_of_an_unknown_task_is_corruption(tmp_path):
    world = _committed(tmp_path)
    with pytest.raises(GraphIntegrityError):
        world.dispatch.attempt_inputs(world.mission.id, "task-nowhere")


def test_the_consumer_is_waiting_on_data_rather_than_ready(tmp_path):
    world = _committed(tmp_path, demand=True)
    view = world.dispatch.read(world.mission.id)
    consumers = {item.consumer_occurrence for item in view.network.data_requirements}
    for occurrence in consumers:
        assert view.reports[occurrence].reason is ReadinessReason.WAITING_DATA


def test_attempt_inputs_returns_the_recorded_upstream_input_shape(tmp_path):
    world = _committed(tmp_path)
    network = world.dispatch.network(world.mission.id)
    leaf = next(spec for spec in network.occurrences if spec.form is TaskForm.PRIMITIVE)
    inputs = world.dispatch.attempt_inputs(world.mission.id, str(leaf.task_id))
    assert all(isinstance(item, UpstreamInput) for item in inputs)


# ================================================================= mutation self-check
# Each entry is (a plausible wrong implementation, the assertion above that must catch
# it).  The witness is written as a small callable so the mapping between "mutant" and
# "the property it breaks" is explicit rather than implied by a large try/except pile.


def _witness_compound_is_intercepted(world: World, monkeypatch) -> None:
    del monkeypatch
    assert world.plan().committed
    _make_task(world, ROOT_TASK, status=TaskStatus.READY)
    intercepted = world.dispatch.intercept_worker_dispatch(world.mission.id, ROOT_TASK)
    assert intercepted is not None and intercepted.reason == "NEEDS_REFINEMENT"


def _witness_completed_rows_do_not_unlock_the_root(world: World, monkeypatch) -> None:
    del monkeypatch
    assert world.plan().committed
    for spec in world.dispatch.network(world.mission.id).occurrences:
        _make_task(world, str(spec.task_id), status=TaskStatus.COMPLETED)
    assert world.dispatch.root_review_ready(world.mission.id) is False


def _witness_phase_waits_for_children(world: World, monkeypatch) -> None:
    del monkeypatch
    assert world.plan().committed
    phases = world.dispatch.advance_compound_phases(world.mission.id)
    assert phases[OccurrenceId(ROOT_TASK)] is CompoundPhase.WAITING_CHILDREN


def _witness_missing_binding_is_corruption(world: World, monkeypatch) -> None:
    del monkeypatch
    assert world.plan().committed
    world.semantics.insert_plan_membership(
        world.mission.id,
        1,
        OccurrenceSpec(
            occurrence_id=OccurrenceId("occ-orphan"),
            task_id=TaskRef("task-orphan"),
            obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
            form=TaskForm.PRIMITIVE,
            requiredness=Requiredness.OPTIONAL_AUTHORIZED,
        ),
        instance_id=None,
        adopted=True,
    )
    with pytest.raises(GraphIntegrityError):
        world.dispatch.network(world.mission.id)


def _mutant_form_gate_off(monkeypatch) -> None:
    from agent_orchestrator.graph.eligibility import LegacyReadyVerdict

    monkeypatch.setattr(
        module,
        "legacy_ready_is_not_eligibility",
        lambda status, *, form=None: LegacyReadyVerdict(False, None, "mutant"),
    )


def _mutant_status_decides_the_root_review(monkeypatch) -> None:
    monkeypatch.setattr(
        module.HierarchicalDispatch,
        "root_review_ready",
        lambda self, mission_id: all(
            str(task.status) == "COMPLETED" for task in self.store.list_tasks(mission_id)
        ),
    )


def _mutant_review_ignores_children(monkeypatch) -> None:
    monkeypatch.setattr(
        module, "next_compound_phase", lambda *a, **k: CompoundPhase.COMPOSITION_REVIEW
    )


def _mutant_unbound_membership_is_dropped(monkeypatch) -> None:
    """The tempting wrong fix: quietly skip the occurrence whose meaning is missing."""

    original = module.HtnStore.list_plan_memberships

    def lenient(self, mission_id, revision):
        return tuple(
            spec
            for spec in original(self, mission_id, revision)
            if self.task_semantics_of(mission_id, str(spec.task_id)) is not None
        )

    monkeypatch.setattr(module.HtnStore, "list_plan_memberships", lenient)


MUTANTS = [
    (
        "the compound form gate is switched off",
        _mutant_form_gate_off,
        _witness_compound_is_intercepted,
    ),
    (
        "COMPLETED task rows decide the root review",
        _mutant_status_decides_the_root_review,
        _witness_completed_rows_do_not_unlock_the_root,
    ),
    (
        "the composition review ignores the children",
        _mutant_review_ignores_children,
        _witness_phase_waits_for_children,
    ),
    (
        "an unbound membership is quietly dropped",
        _mutant_unbound_membership_is_dropped,
        _witness_missing_binding_is_corruption,
    ),
]


@pytest.mark.parametrize(("name", "mutate", "witness"), MUTANTS, ids=[item[0] for item in MUTANTS])
def test_the_witness_assertion_passes_on_the_real_implementation(
    tmp_path, monkeypatch, name, mutate, witness
):
    del name, mutate
    witness(_world(tmp_path, key="real"), monkeypatch)


@pytest.mark.parametrize(("name", "mutate", "witness"), MUTANTS, ids=[item[0] for item in MUTANTS])
def test_each_mutant_breaks_its_witness_assertion(tmp_path, monkeypatch, name, mutate, witness):
    mutate(monkeypatch)
    with pytest.raises((AssertionError, KeyError, GraphIntegrityError, pytest.fail.Exception)):
        witness(_world(tmp_path, key="mutated"), monkeypatch)


def test_a_plan_commit_rejected_is_not_swallowed_into_a_committed_outcome(tmp_path, monkeypatch):
    """The loop must not report a commit it did not get."""

    world = _world(tmp_path)

    def _always_refuse(self, command, principal, **kwargs):
        raise PlanCommitRejected("READ_SET_STALE", "mutant")

    monkeypatch.setattr(CommitService, "commit_plan_revision", _always_refuse)
    outcome = world.plan()
    assert not outcome.committed and outcome.receipt is None


# ============================================ the event handler's own branch, exercised
# The wiring sites are assembly, but assembly is still behaviour: these drive the real
# ``Orchestrator`` so the settle paths, the ``PlanPrincipal`` construction, the
# COMMITTED gate and the four integrity guards are executed rather than asserted about.


def _orchestrator(tmp_path, planner_steps: Sequence[Any]) -> Orchestrator:
    config = OrchestratorConfig(
        evidence_root=Path(tmp_path) / "evidence", max_concurrency=1, test_timeout_seconds=60
    )
    return Orchestrator(config, RoleScriptedProvider({"planner": list(planner_steps)}))


def _seed_hierarchical(orchestrator: Orchestrator, *, key: str, roots: int = 1):
    """A hierarchical Mission in one Orchestrator's store, with its declarations.

    ``roots=2`` writes a second semantic binding and no plan revision, which is the
    ``root_not_identified`` corruption: a Mission whose recorded meaning does not say
    which goal it was opened for.
    """

    mission, _ = orchestrator.commit.create_mission(_spec(key, mode=HIERARCHICAL_SEMANTICS))
    env = _env(mission.id)
    contract = _outer()
    assert env.admit(contract).admitted
    semantics = HtnStore(orchestrator.store)
    ObligationStore(orchestrator.store).register(
        Obligation(
            obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
            mission_id=mission.id,
            requirement_refs=("req-1",),
            goal_signature_id="plan.goal",
        ),
        recursion_fuel=FUEL,
    )
    for index in range(roots):
        semantics.put_task_semantics(
            mission.id,
            task_binding(
                env,
                "plan.goal",
                task_id=ROOT_TASK if index == 0 else f"{ROOT_TASK}-{index}",
                obligation=ROOT_DUTY,
                parameters={"subject": "alpha"},
            ),
        )
    semantics.register_method(contract, env.registry.registration(contract.method_ref()))
    orchestrator.install_hierarchical(planning=env)
    return mission, env, contract


def _commit_scripted_plan(orchestrator: Orchestrator, mission: Any, contract: Any) -> None:
    """Put the scripted root refinement on a loop Mission's board without a model round."""

    binding = HtnStore(orchestrator.store).task_semantics_of(mission.id, ROOT_TASK)
    orchestrator.commit.begin_planning(mission.id)
    approve_content_only_completion(
        orchestrator.commit, mission, binding, command_id=f"approve-{mission.id}"
    )
    dispatch = orchestrator._new_mode(orchestrator.store.get_mission(mission.id))
    outcome = apply_scripted_plan(
        dispatch,
        mission.id,
        _proposal_text(contract),
        principal=PlanPrincipal("manager-1", "mission", 0),
        command_id="cmd-loop",
    )
    assert outcome.committed, outcome.last_reason


def _drive(tmp_path, planner_steps: Sequence[Any], probe, *, key: str = "orch", roots: int = 1):
    """Run one Orchestrator to idle, then hand the probe the store it wrote."""

    async def case() -> None:
        async with _orchestrator(tmp_path, planner_steps) as orchestrator:
            mission, env, contract = _seed_hierarchical(orchestrator, key=key, roots=roots)
            await orchestrator.run()
            probe(orchestrator, mission, env, contract)

    asyncio.run(case())


def _events(orchestrator: Orchestrator, mission_id: str, kind: str) -> list[Any]:
    return [item for item in orchestrator.store.list_events(mission_id) if item.type == kind]


def test_a_damaged_plan_stops_that_mission_through_the_planner_branch(tmp_path):
    """Two root bindings and no plan revision: there is no root to seed a network from."""

    def probe(orchestrator, mission, env, contract) -> None:
        del env, contract
        integrity = _events(orchestrator, mission.id, PLAN_INTEGRITY_FAILED)
        assert integrity, [item.type for item in orchestrator.store.list_events(mission.id)]
        assert integrity[0].payload["code"] == "root_not_identified"
        assert "cycle" not in integrity[0].payload["diagnose"]
        assert orchestrator.store.get_mission(mission.id).status is MissionStatus.FAILED
        # Corruption is not a bad proposal: the Planner is not asked again.
        assert _events(orchestrator, mission.id, "PlanningRejected") == []

    _drive(tmp_path, [_proposal_text(_outer())], probe, key="orch-damaged", roots=2)


class _UncommittedTurn:
    """A planner turn that did not commit; the shape ``_collect_plan`` is handed."""

    state = AgentTurnState.FAILED
    turn_id = "turn-uncommitted"
    error = {"kind": "transport", "retryable": True}
    public_output = None
    usage_refs: tuple[Any, ...] = ()


def _planner_intent(orchestrator: Orchestrator, mission_id: str):
    return next(
        item
        for item in orchestrator.store.list_intents(
            "PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED"
        )
        if item.mission_id == mission_id
    )


def test_a_turn_that_did_not_commit_is_a_planning_rejection(tmp_path):
    """The COMMITTED gate of the new branch, executed rather than read."""

    async def case() -> None:
        async with _orchestrator(tmp_path, []) as orchestrator:
            mission, _env, _contract = _seed_hierarchical(orchestrator, key="orch-nocommit")
            orchestrator.commit.begin_planning(mission.id)
            await orchestrator._try_planner_intent(mission.id, ordinal=1)
            current = orchestrator.store.get_mission(mission.id)
            new_mode = orchestrator._new_mode(current)
            assert new_mode is not None
            await orchestrator._collect_plan_hierarchical(
                _planner_intent(orchestrator, mission.id),
                _UncommittedTurn(),
                current,
                "",
                new_mode,
            )
            rejections = _events(orchestrator, mission.id, "PlanningRejected")
            assert rejections and rejections[0].payload["reason"] == "proposal_unreadable"
            assert "planner turn failed" in rejections[0].payload["detail"]["error"]

    asyncio.run(case())


def _force_active(orchestrator: Orchestrator, mission_id: str):
    """Take a hierarchical Mission from PLANNING to ACTIVE without a legacy graph.

    P2.3b commits plan revisions but writes no legacy Task rows, so nothing moves the
    Mission to ACTIVE yet (that bridge is P2.3c's).  The two guards below are about
    what happens *while* ACTIVE, so the transition is made explicitly here rather
    than asserted into existence.
    """

    from agent_orchestrator.orchestrator.state_machine import next_mission

    mission = orchestrator.store.get_mission(mission_id)
    if mission.status is MissionStatus.FAILED:
        # Part 2d: an idle hierarchical cycle now *ends* (``NO_DISPATCHABLE_WORK``),
        # so a guard about what happens while ACTIVE has to put it back there.  §25.1
        # has no FAILED→ACTIVE edge — correctly — so the row is rewritten directly
        # rather than transitioned; this is a test putting a world back, not the
        # product reviving a Mission.
        assert mission.final_report["stop_reason"] == "no_dispatchable_work"
        revived = dataclasses.replace(
            mission,
            status=MissionStatus.ACTIVE,
            stop_reason=None,
            version=mission.version + 1,
        )
        orchestrator.store.update_mission(revived, expected_version=mission.version)
        return revived
    if mission.status is MissionStatus.PLANNING:
        updated = next_mission(mission, MissionStatus.ACTIVE)
        orchestrator.store.update_mission(updated, expected_version=mission.version)
        return updated
    return mission


def _corrupt_plan(store: Store, mission_id: str) -> None:
    """Add a plan member whose task has no semantic binding (§18.5 corruption)."""

    semantics = HtnStore(store)
    revision = int(semantics.active_plan_revision(mission_id).revision)
    semantics.insert_plan_membership(
        mission_id,
        revision,
        OccurrenceSpec(
            occurrence_id=OccurrenceId("occ-orphan"),
            task_id=TaskRef("task-orphan"),
            obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
            form=TaskForm.PRIMITIVE,
            requiredness=Requiredness.OPTIONAL_AUTHORIZED,
        ),
        instance_id=None,
        adopted=True,
    )


def _insert_display_task(store: Store, mission_id: str, task_id: str) -> Task:
    task = Task(
        id=task_id,
        mission_id=mission_id,
        parent_task_ids=(),
        dependency_ids=(),
        goal=f"goal of {task_id}",
        rationale="display row",
        success_criteria=("file:a.md",),
        verification_policy=("format_check",),
        allowed_tools=TOOLS,
        budget=Budget(max_tokens=1_000, max_attempts=2),
        priority=1.0,
        status=TaskStatus.READY,
        version=1,
    )
    store.insert_task(task, ordinal=1 + len(store.list_tasks(mission_id)))
    return task


def test_the_decide_branch_stops_one_mission_on_a_damaged_plan(tmp_path):
    """Guard 3: the terminal judgement never raises out of the shared loop."""

    async def case() -> None:
        async with _orchestrator(tmp_path, []) as orchestrator:
            mission, _env, contract = _seed_hierarchical(orchestrator, key="orch-decide")
            _commit_scripted_plan(orchestrator, mission, contract)
            assert HtnStore(orchestrator.store).active_plan_revision(mission.id) is not None
            _corrupt_plan(orchestrator.store, mission.id)
            _insert_display_task(orchestrator.store, mission.id, "task-display")
            active = _force_active(orchestrator, mission.id)
            assert await orchestrator._decide(active) is True
            integrity = _events(orchestrator, mission.id, PLAN_INTEGRITY_FAILED)
            assert integrity and integrity[0].payload["code"] == "semantic_binding_missing"
            assert orchestrator.store.get_mission(mission.id).status is MissionStatus.FAILED

    asyncio.run(case())


def test_the_dispatch_branch_stops_one_mission_on_a_missing_binding(tmp_path):
    """Guard 4: a Task with no meaning stops the Mission, it does not end the run."""

    async def case() -> None:
        async with _orchestrator(tmp_path, []) as orchestrator:
            mission, _env, contract = _seed_hierarchical(orchestrator, key="orch-dispatch")
            _commit_scripted_plan(orchestrator, mission, contract)
            task = _insert_display_task(orchestrator.store, mission.id, "task-unbound")
            active = _force_active(orchestrator, mission.id)
            assert await orchestrator._next_attempt(active, task, []) is True
            integrity = _events(orchestrator, mission.id, PLAN_INTEGRITY_FAILED)
            assert integrity and integrity[0].payload["code"] == "semantic_binding_missing"
            assert orchestrator.store.get_mission(mission.id).status is MissionStatus.FAILED

    asyncio.run(case())


# ================================================== the carried-integrity reading path
def test_the_default_read_of_a_damaged_plan_raises(tmp_path):
    world = _committed(tmp_path)
    _corrupt_plan(world.store, world.mission.id)
    with pytest.raises(GraphIntegrityError):
        world.dispatch.read(world.mission.id)


def test_the_tolerant_read_carries_the_error_instead_of_raising(tmp_path):
    world = _committed(tmp_path)
    _corrupt_plan(world.store, world.mission.id)
    view = world.dispatch.read(world.mission.id, tolerate_integrity=True)
    assert isinstance(view.plan.integrity_error, PlanIntegrityError)
    assert view.plan.integrity_error.code == "semantic_binding_missing"


def test_every_occurrence_of_a_damaged_plan_reports_graph_integrity(tmp_path):
    world = _committed(tmp_path)
    _corrupt_plan(world.store, world.mission.id)
    view = world.dispatch.read(world.mission.id, tolerate_integrity=True)
    assert view.reports
    assert {report.reason for report in view.reports.values()} == {ReadinessReason.GRAPH_INTEGRITY}


def test_both_frontiers_of_a_damaged_plan_are_empty(tmp_path):
    world = _committed(tmp_path)
    _corrupt_plan(world.store, world.mission.id)
    view = world.dispatch.read(world.mission.id, tolerate_integrity=True)
    assert view.execution_frontier.occurrences == ()
    assert view.planning_frontier.occurrences == ()


def test_the_missing_binding_message_does_not_talk_about_a_cycle(tmp_path):
    world = _committed(tmp_path)
    _corrupt_plan(world.store, world.mission.id)
    with pytest.raises(GraphIntegrityError) as caught:
        world.dispatch.network(world.mission.id)
    assert "cycle" not in str(caught.value)
    assert "cycle" not in caught.value.diagnose()
    assert "task-orphan" in str(caught.value)


def test_the_plan_integrity_error_reports_no_topological_order(tmp_path):
    """TG §14.3: a damaged projection's healthy prefix is not handed out as a plan."""

    world = _committed(tmp_path)
    _corrupt_plan(world.store, world.mission.id)
    with pytest.raises(GraphIntegrityError) as caught:
        world.dispatch.network(world.mission.id)
    assert caught.value.cycle == ()
    assert not hasattr(caught.value, "order")
