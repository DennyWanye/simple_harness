# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3c part 2b: the three wirings part 2 left open, and the scenarios they unlock.

Part 2's §13 named them: there was no deployment-side ``PlanningWorld``, no path
from a verified leaf to an ``Acceptance``, and no assembly that pointed the
observers at anything.  Each section below is the witness that one of them is now
real, and the last two sections are the scenarios that could not be written before:

§1  the deployment's ``PlanningWorld`` — derived capabilities, store-backed
    evidence, a seed library that went through the admission protocol.
§2  the observer assembly — one reader per predicate, chosen by the deployment.
§3  the leaf acceptance chain — verdict → anchors → ``Acceptance`` →
    ``acceptance_outputs`` → a DATA consumer that can finally resolve.
§4  the evidence round — an UNKNOWN precondition becomes a read-only occurrence
    that the observer index executes, and an observer that is down is
    ``OBSERVER_UNAVAILABLE`` with nothing written.
§5  OR methods: a precondition the observer denies refutes that branch and leaves
    the other one as the only method that can be grounded.
§6  a shared read-only subgoal is executed once.
§7  the context the Planner writes a method from — typed, no authority vocabulary.
§8  mutation self-check.

HTN 补齐阶段 A′（2026-10-03）：只借任务号与存储的用例改用产品组装建出的任务
（:func:`_open_product_mission`）；观察行经观察管线（读者是外界的替身）写入，不手插。
写做法上下文的"描述目标与操作者""每个准则带原文"两条删除，由
``assurance_exec/test_planner_chooses_and_proposes.py::test_the_context_for_writing_a_method_carries_the_requirement_text_and_a_fresh_identity``
覆盖；其余四条改为读产品自己的 ``method_proposal_context``。第 6 节见那里的说明。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))

from agent_orchestrator.contracts.evidence_state import TruthValue  # noqa: E402
from agent_orchestrator.contracts.models import ContractError  # noqa: E402
from agent_orchestrator.graph.eligibility import ReadinessReason  # noqa: E402
from agent_orchestrator.planning.htn.observation_pipeline import (  # noqa: E402
    build_index,
    observe_predicate,
    record_observation,
)
from agent_orchestrator.planning.htn.observers import Observation, unavailable  # noqa: E402
from agent_orchestrator.planning.htn.observers.code import code_observers  # noqa: E402
from agent_orchestrator.planning.htn.seed_methods import seed_content_hash  # noqa: E402
from agent_orchestrator.planning.htn.world import (  # noqa: E402
    CAPABILITY_LAYERS,
    DeploymentPlanningWorld,
    assign_readers,
    build_planning_world,
    capability_records,
    declared_capability_ids,
)
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402


def ref(identifier: str, version: int = 1):
    from agent_orchestrator.contracts.semantic_base import VersionedRef

    return VersionedRef(
        id=identifier, version=version, content_hash=seed_content_hash(identifier, version)
    )


@pytest.fixture
def worktree(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "seed"],
        cwd=root,
        check=True,
    )
    return root


def _open_product_mission(tmp_path, *, key: str, criteria: tuple[str, ...] = ("file:NOTES.md",),
                          wrap: Any = None, **world: Any):
    """A user Mission created through the product's deployment assembly (HTN 补齐阶段 A′):
    root initialised, TaskGraph bound, Assurance on, the completion mapping confirmed the
    product's way and planning begun — then held open for synchronous test code.  Yields
    an ``h1i_seed.Seed`` (or ``wrap(seed)``); ``world`` goes to ``product_world``."""

    import asyncio

    from h1i_seed import CONFIG, GOAL, Seed, root_task

    from agent_orchestrator.testing.product_world import product_world
    from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

    loop = asyncio.new_event_loop()
    opened = product_world(Path(tmp_path) / "root", LayeredScriptedProvider(), auto=False, **{**CONFIG, **world})
    product = loop.run_until_complete(opened.__aenter__())
    try:
        created = product.create({"goal": GOAL, "idempotency_key": key, "success_criteria": list(criteria)})
        mission_id = created["mission_id"]
        product.deployment.duties.auto_confirm_content_completion(auto=True)
        product.loop.commit.begin_planning(mission_id)
        dispatch = product.loop._dispatch_for(mission_id)
        seed = Seed(product.loop, product.store.get_mission(mission_id), dispatch.planning,
                    HtnStore(product.store).latest_task_semantics(root_task(mission_id)), dispatch, product)
        yield seed if wrap is None else wrap(seed)
    finally:
        loop.run_until_complete(opened.__aexit__(None, None, None))
        loop.close()


@pytest.fixture
def product_mission(tmp_path) -> Any:
    """Only a Mission row and its store: the product-assembled Mission the D-class cases
    borrow (they need a Mission id to write observations against, nothing more)."""

    yield from _open_product_mission(tmp_path, key="wiring")


# ======================================================================================
# 1. the deployment's PlanningWorld
# ======================================================================================


def test_the_assembled_world_satisfies_the_structural_type() -> None:
    """``install_hierarchical`` asks for four attributes and two calls (§18.5)."""

    world = build_planning_world("m-1")
    for name in ("catalog", "schemas", "registry", "predicates"):
        assert getattr(world, name) is not None
        assert not callable(getattr(world, name))
    assert world.capabilities().records
    assert world.snapshot().scope_id == "mission"


def test_the_seed_methods_went_through_the_admission_protocol() -> None:
    """A registered method is one the registry admitted, not one someone inserted."""

    world = build_planning_world("m-1", domains=("code",))
    refs = world.registry.method_refs()
    assert {item.method_id for item in refs} == {
        "code.fix-by-patch",
        "code.fix-by-revert",
        # G2: the third branch of the OR, whose sub-goal needs the same read-only
        # reading of the repository its parent already has.
        "code.fix-by-assessed-revert",
        "code.assess-by-reading",
        "code.review-changes-directly",
        "code.review-changes-recursively",
    }
    for reference in refs:
        assert world.registry.definition(reference) is not None


def test_a_capability_is_registered_only_when_an_operator_implements_it() -> None:
    """§14.2 'registered': there is a primitive task type with a real operator."""

    world = build_planning_world("m-1", domains=("code",), deployed_layers=("code_test",))
    table = {item.capability_id: item for item in world.records}
    assert set(table) == set(declared_capability_ids(world.catalog))
    assert all(item.registered for item in table.values())


def test_a_capability_whose_layer_is_not_deployed_is_unavailable_with_a_reason() -> None:
    """The table is derived from this host, not declared by the domain data."""

    world = build_planning_world("m-1", domains=("code",), deployed_layers=())
    table = {item.capability_id: item for item in world.records}
    assert CAPABILITY_LAYERS["tests.run"] == "code_test"
    assert not table["tests.run"].available
    assert table["tests.run"].unavailable_reasons() == ("configured",)
    assert table["repo.read"].available


def test_an_unhealthy_tool_and_a_missing_layer_are_different_axes() -> None:
    world = build_planning_world(
        "m-1", domains=("code",), deployed_layers=("code_test",), unhealthy=("repo.write",)
    )
    table = {item.capability_id: item for item in world.records}
    assert table["repo.write"].unavailable_reasons() == ("healthy",)
    assert table["tests.run"].available


def test_an_undeclared_capability_is_not_configured() -> None:
    """Review P1-6: the ``configured`` axis used to be fail-open.

    ``needed is None or needed in layers`` read a *missing* table entry as "needs no
    layer", so a capability this deployment had never heard of came back
    ``configured=True`` and a method could be admitted against a tool nobody
    installed.  The table is the deployment's declaration now: listed with a layer,
    listed with ``None`` (this host runs it as-is), or not listed — and not listed
    is refused with the §14.2 axis that says why.
    """

    world = build_planning_world("m-1", domains=("code",), deployed_layers=("code_test",))
    # A deployment that declares nothing: every capability the domain names is one
    # this host has not promised.
    blank = {
        item.capability_id: item
        for item in capability_records(
            world.catalog, deployed_layers=("code_test",), capability_layers={}
        )
    }
    assert set(blank) == set(declared_capability_ids(world.catalog))
    assert all(item.registered for item in blank.values())
    assert all(not item.configured for item in blank.values())
    assert all(item.unavailable_reasons() == ("configured",) for item in blank.values())
    # A deployment that declares one of them, with ``None`` meaning "no extra layer".
    partial = {
        item.capability_id: item
        for item in capability_records(
            world.catalog, deployed_layers=("code_test",), capability_layers={"repo.read": None}
        )
    }
    assert partial["repo.read"].available
    assert not partial["tests.run"].configured


def test_every_capability_the_shipped_domains_name_is_declared_by_the_deployment() -> None:
    """The fail-closed rule is only usable if the table covers what actually ships."""

    for domain in ("code", "appworld"):
        world = build_planning_world("m-1", domains=(domain,))
        missing = [
            item for item in declared_capability_ids(world.catalog) if item not in CAPABILITY_LAYERS
        ]
        assert missing == [], f"{domain} names capabilities this deployment never declared"


def test_a_capability_named_only_by_a_compound_type_is_not_registered() -> None:
    """Mutation: reporting every mentioned capability as registered would admit a
    method against a tool nothing can dispatch to."""

    from agent_orchestrator.planning.htn.registry import TaskTypeCatalog

    world = build_planning_world("m-1", domains=("code",))
    catalog = TaskTypeCatalog()
    for spec in world.catalog.task_types():
        if spec.form.name == "COMPOUND":
            catalog.register(spec)
        elif spec.operator_ref is None:
            catalog.register(spec)
    orphan = capability_records(catalog)
    assert all(not item.registered for item in orphan)


def test_an_unknown_domain_is_refused_rather_than_producing_an_empty_library() -> None:
    with pytest.raises(ContractError, match="no seed domain"):
        build_planning_world("m-1", domains=("code", "not-a-domain"))


def test_the_evidence_snapshot_is_read_from_the_store_every_time(product_mission) -> None:
    """A cached snapshot is the stale read ADR-13 refuses; a new observation shows."""

    store, mission = product_mission.loop.store, product_mission.mission
    semantics = HtnStore(store)
    world = build_planning_world(mission.id, domains=("code",), semantics=semantics)
    before = world.snapshot()
    assert before.entries == () and before.support_revision == 0
    _observe(world, semantics, mission.id, polarity=True)
    after = world.snapshot()
    assert after.support_revision == 1
    assert after.entries[0].truth() is TruthValue.TRUE
    assert after.snapshot_id != before.snapshot_id


def test_the_latest_reading_of_one_observer_is_what_the_snapshot_says(product_mission) -> None:
    """阶段 D（偏差裁决《观察重读》）：同一个观察器先说成立、后说不成立，快照按它最新的一次读
    ——不成立；两条记录都留在库里。不同观察器一正一反仍是冲突（见
    ``product_world/test_desktop_preconditions.py`` 的函数级用例）。"""
    store, mission = product_mission.loop.store, product_mission.mission
    semantics = HtnStore(store)
    world = build_planning_world(mission.id, domains=("code",), semantics=semantics)
    _observe(world, semantics, mission.id, polarity=True)
    _observe(world, semantics, mission.id, polarity=False, at=2)
    entry = world.snapshot().entries[0]
    assert entry.truth() is TruthValue.FALSE
    assert len(semantics.list_observations(mission.id)) == 2


def test_the_fixture_env_is_filled_by_the_real_assembly() -> None:
    """``seed_env`` delegates; it no longer keeps a second answer to 'what is a world'."""

    from htn_world import seed_env

    env = seed_env("mission-x")
    assert isinstance(env.world, DeploymentPlanningWorld)
    assert env.catalog is env.world.catalog
    assert env.registry is env.world.registry
    assert env.predicates is env.world.predicates
    assert env.schemas is env.world.schemas


def _observe(world, semantics, mission_id: str, *, polarity: bool, at: int = 1) -> None:
    """One reading of ``code.repo-checked-out`` recorded the way a deployment records it:
    a reader (the test double for the outside world) answers, the observation pipeline
    writes.  ``polarity=False`` is an authoritative denial."""

    from agent_orchestrator.planning.htn.observers import denial, observed

    class Reader:
        observer_id = "code.repo-observer"

        def predicate_ids(self) -> tuple[str, ...]:
            return ("code.repo-checked-out",)

        def observe(self, signature, arguments, *, now_ms: int) -> Observation:
            if polarity:
                return observed(signature, arguments, polarity=True, observer_id=self.observer_id, now_ms=now_ms)
            return denial(signature, arguments, observer_id=self.observer_id, now_ms=now_ms,
                          coverage_scope="worktree:/tmp")

    index = build_index(world.predicates, (Reader(),))
    outcome = record_observation(
        semantics, mission_id,
        observe_predicate(index, ref("code.repo-checked-out"), {"repository": "r1"}, now_ms=at))
    assert outcome.recorded, outcome


# ======================================================================================
# 2. the observer assembly
# ======================================================================================


def test_the_shipped_code_observers_cannot_be_indexed_without_an_assignment(worktree) -> None:
    """Mutation: the pack ships two identities for one CLOSED predicate on purpose."""

    world = build_planning_world("m-1", domains=("code",))
    with pytest.raises(ContractError, match="offered by two observers"):
        build_index(world.predicates, code_observers(worktree))


def test_the_deployment_assigns_one_reader_per_predicate(worktree) -> None:
    world = build_planning_world("m-1", domains=("code",), worktree=worktree)
    mapping = world.observer_index.to_json()["predicates"]
    assert mapping["code.working-tree-clean"] == "code.repo-observer"
    assert set(mapping) == {
        "code.changeset-reviewable",
        "code.changeset-too-large",
        "code.regression-commit-known",
        "code.repo-checked-out",
        "code.test-is-failing",
        "code.working-tree-clean",
        # P2.3c part 3a: the two business-state readers L2 acceptance needs.  They
        # are in this set for the same reason the others are — the deployment index
        # assigns exactly one reader per declared predicate, and a predicate the seed
        # library declares with no reader installed would be permanently unknowable.
        "code.diff-touches-only",
        "code.declared-dependency-present",
    }


def test_the_order_is_the_deployments_precedence_declaration(worktree) -> None:
    """Listing the workspace identity first gives it the shared predicate."""

    readers = tuple(reversed(code_observers(worktree)))
    world = build_planning_world("m-1", domains=("code",), observers=readers)
    mapping = world.observer_index.to_json()["predicates"]
    assert mapping["code.working-tree-clean"] == "code.workspace-observer"
    assert mapping["code.repo-checked-out"] == "code.repo-observer"


def test_an_observer_left_with_nothing_to_read_is_dropped(worktree) -> None:
    readers = code_observers(worktree)
    assigned = assign_readers(readers)
    assert "code.workspace-observer" not in {item.observer_id for item in assigned}


def test_a_deployment_without_a_worktree_installs_no_code_observer() -> None:
    """Not being able to look is not the proposition being false."""

    world = build_planning_world("m-1", domains=("code",))
    assert world.observed_predicate_ids() == ()
    outcome = observe_predicate(
        world.observer_index, ref("code.repo-checked-out"), {"repository": "r"}, now_ms=1
    )
    assert outcome.reason is ReadinessReason.OBSERVER_UNAVAILABLE
    assert outcome.record is None


def test_a_real_observer_reads_the_real_worktree(worktree) -> None:
    world = build_planning_world("m-1", domains=("code",), worktree=worktree)
    outcome = observe_predicate(
        world.observer_index, ref("code.repo-checked-out"), {"repository": str(worktree)}, now_ms=1
    )
    assert outcome.available
    assert outcome.record is not None and outcome.record.polarity is True


def test_a_dirty_worktree_is_an_authoritative_negative(worktree) -> None:
    (worktree / "a.py").write_text("x = 2\n", encoding="utf-8")
    world = build_planning_world("m-1", domains=("code",), worktree=worktree)
    outcome = observe_predicate(
        world.observer_index,
        ref("code.working-tree-clean"),
        {"repository": str(worktree)},
        now_ms=1,
    )
    assert outcome.available
    assert outcome.record is not None
    assert outcome.record.polarity is False
    assert outcome.record.is_authoritative_negative


def test_an_observer_that_is_down_writes_nothing(product_mission, worktree) -> None:
    """The asymmetry ``record_observation`` exists for."""

    store, mission = product_mission.loop.store, product_mission.mission
    semantics = HtnStore(store)
    world = build_planning_world(mission.id, domains=("code",), worktree=worktree)

    class Down:
        observer_id = "code.repo-observer"

        def predicate_ids(self) -> tuple[str, ...]:
            return ("code.repo-checked-out",)

        def observe(self, signature, arguments, *, now_ms: int) -> Observation:
            return unavailable(self.observer_id, "code.repo-checked-out", "service is down")

    index = build_index(world.predicates, (Down(),))
    outcome = record_observation(
        semantics,
        mission.id,
        observe_predicate(index, ref("code.repo-checked-out"), {"repository": "r"}, now_ms=1),
    )
    assert outcome.reason is ReadinessReason.OBSERVER_UNAVAILABLE
    assert outcome.recorded is False
    assert semantics.list_observations(mission.id) == ()


# ======================================================================================
# 3. an UNKNOWN precondition becomes a look, and the look decides the OR
# ======================================================================================
#
# Part 2's §9 could not write these: ``refinement`` produced evidence *requests* and
# nothing executed them, so an UNKNOWN precondition stayed UNKNOWN and the OR choice
# could only be moved by a test asserting the evidence by fiat (``Env.say``).  Here
# the evidence arrives the way a deployment's does: an observer reads, the pipeline
# records, and the next applicability check reads the store.  Which alternative to
# take stays the planner's judgement; what the store decides is which alternative
# *can* be grounded.


from htn_world import (  # noqa: E402
    Env,
    atom,
    ground_draft,
    method,
    out,
    param,
    root_network,
    step,
    task_binding,
)

from agent_orchestrator.contracts.htn import (  # noqa: E402
    ReusePolicy,
    SideEffectKind,
    TaskForm,
)
from agent_orchestrator.planning.htn import evidence_round  # noqa: E402
from agent_orchestrator.planning.htn.applicability import (  # noqa: E402
    ApplicabilityStatus,
    assess_method,
)
from agent_orchestrator.planning.htn.observers import denial, observed  # noqa: E402
from agent_orchestrator.planning.htn.refinement import (  # noqa: E402
    evidence_requests,
    unknown_predicates,
)

PRIMARY = "demo.primary-ready"
FALLBACK = "demo.fallback-ready"


class _ScriptedObserver:
    """A reader with a script.  Answers only what the deployment routed to it."""

    observer_id = "demo.observer"

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def predicate_ids(self) -> tuple[str, ...]:
        return (PRIMARY, FALLBACK)

    def observe(self, signature, arguments, *, now_ms: int):
        name = signature.predicate_ref.id
        self.calls.append(name)
        answer = self.answers.get(name)
        if answer is None:
            return unavailable(self.observer_id, name, "this reader has no script for it")
        if answer is False:
            return denial(
                signature,
                arguments,
                observer_id=self.observer_id,
                now_ms=now_ms,
                coverage_scope="demo:alpha",
            )
        return observed(
            signature,
            arguments,
            polarity=True,
            observer_id=self.observer_id,
            now_ms=now_ms,
        )


def _demo_env() -> Env:
    env = Env()
    for name in (PRIMARY, FALLBACK):
        env.register_predicate(
            name, (("subject", "string"),), closed=True, observers=("demo.observer",)
        )
    env.register_type(
        "demo.goal",
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-done",),
        domain="demo",
    )
    env.register_type(
        "demo.shared-read",
        parameters=(("subject", "string"),),
        outputs=(("facts", "demo.facts"),),
        capabilities=("demo.read",),
        effect=SideEffectKind.EXTERNAL_READ,
        reuse=ReusePolicy.REUSE_ACCEPTED,
        domain="demo",
    )
    for name, port in (("demo.a", "ra"), ("demo.b", "rb")):
        env.register_type(
            name,
            parameters=(("subject", "string"),),
            inputs=(("facts", "demo.facts", True),),
            outputs=((port, f"demo.{port}"),),
            capabilities=("demo.read",),
            domain="demo",
        )
    env.register_type(
        "demo.observer-type",
        parameters=(("proposition_key", "string"),),
        capabilities=("demo.read",),
        effect=SideEffectKind.EXTERNAL_READ,
        observes=(PRIMARY, FALLBACK),
        domain="demo",
    )
    return env


def _alternative(method_id: str, predicate: str, tail: str):
    return method(
        method_id,
        "demo.goal",
        parameter_schema="demo.goal.params",
        applicable=(atom(predicate, {"subject": param("subject")}),),
        steps=(
            step(
                "shared",
                "demo.shared-read",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("demo.read",),
            ),
            step(
                tail,
                f"demo.{tail}",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "facts": out("shared", "facts")},
                capabilities=("demo.read",),
            ),
        ),
        links=(("c-done", tail, f"c-{tail}"),),
        finalizer=tail,
    )


@dataclass
class DemoWorld:
    env: Env
    world: DeploymentPlanningWorld
    observer: _ScriptedObserver
    binding: Any
    store: Any

    def methods(self):
        return {
            contract.method_id: contract
            for contract in (
                self.env.registry.definition(reference)
                for reference in self.env.registry.method_refs()
            )
            if contract is not None
        }

    def evidence(self):
        """What the evidence round would ask about the root goal.

        The same walk ``HierarchicalDispatch.run_evidence_round`` makes: every
        method offered for the open compound goal, its UNKNOWN applicability atoms,
        and the read-only occurrences that could answer them.
        """

        network = root_network(self.env, self.binding)
        spec = network.occurrence(network.root_occurrence_ids[0])
        found: dict[str, Any] = {}
        for contract in self.methods().values():
            unknowns = unknown_predicates(
                contract.applicable_when,
                parameters=self.binding.typed_parameters,
                predicates=self.env.predicates,
                snapshot=self.world.snapshot(),
            )
            for request in evidence_requests(
                unknowns,
                for_occurrence=spec.occurrence_id,
                obligation_id=spec.obligation_id,
                catalog=self.env.catalog,
                semantic_scope=self.binding.semantic_scope,
                contract_revision=int(self.binding.contract_revision),
            ):
                found.setdefault(request.proposition_key, request)
        return tuple(found[key] for key in sorted(found))

    def status(self, method_id: str) -> ApplicabilityStatus:
        return assess_method(
            self.binding,
            self.methods()[method_id],
            self.world.snapshot(),
            self.env.capabilities(),
            registry=self.env.predicates,
        ).status

    def draft(self, method_id: str):
        return ground_draft(
            self.env,
            self.binding,
            self.methods()[method_id],
            root_network(self.env, self.binding),
            snapshot=self.world.snapshot(),
        )

    def asks(self, requests) -> tuple[Any, ...]:
        """The grounded atoms behind these evidence requests."""

        pending = evidence_round.pending_asks(
            _conditions(),
            parameters={"subject": "alpha"},
            registry=self.env.predicates,
            snapshot=self.world.snapshot(),
        )
        return evidence_round.asks_for_requests(requests, pending)

    def look(self, asks, *, now_ms: int = 1_000):
        return evidence_round.run_round(
            self.world.observer_index,
            HtnStore(self.store),
            self.env.mission,
            asks,
            now_ms=now_ms,
        )


@pytest.fixture
def demo(product_mission) -> DemoWorld:
    store, mission = product_mission.loop.store, product_mission.mission
    env = _demo_env()
    env.mission = mission.id
    env.admit(_alternative("demo.primary", PRIMARY, "a"))
    env.admit(_alternative("demo.fallback", FALLBACK, "b"))
    observer = _ScriptedObserver({PRIMARY: False, FALLBACK: True})
    world = DeploymentPlanningWorld(
        mission_id=mission.id,
        schemas=env.schemas,
        predicates=env.predicates,
        catalog=env.catalog,
        registry=env.registry,
        records=env.capabilities().records,
        observers=build_index(env.predicates, (observer,)),
        semantics=HtnStore(store),
    )
    binding = task_binding(
        env,
        "demo.goal",
        task_id="task-root",
        obligation="obl-root",
        parameters={"subject": "alpha"},
    )
    return DemoWorld(env=env, world=world, observer=observer, binding=binding, store=store)


def test_an_unknown_precondition_asks_for_evidence_rather_than_dispatching(demo: DemoWorld) -> None:
    """ADR-07: UNKNOWN is a reason to look, never a reason to try the work."""

    requests = demo.evidence()
    assert {item.predicate_ref.id for item in requests} == {PRIMARY, FALLBACK}
    assert all(item.satisfiable for item in requests)
    assert all(item.occurrence is not None for item in requests)
    assert demo.status("demo.primary") is ApplicabilityStatus.NEEDS_EVIDENCE
    assert demo.status("demo.fallback") is ApplicabilityStatus.NEEDS_EVIDENCE


def test_the_evidence_occurrence_is_a_read_only_observer_type(demo: DemoWorld) -> None:
    for request in demo.evidence():
        assert request.observer is not None
        assert request.observer.read_only
        assert request.binding.side_effect_kind is SideEffectKind.EXTERNAL_READ
        assert request.binding.resource_writes == ()


def test_the_observer_index_executes_the_evidence_occurrence(demo: DemoWorld) -> None:
    result = demo.look(demo.asks(demo.evidence()))
    assert len(result.recorded) == 2
    assert demo.observer.calls == [PRIMARY, FALLBACK]
    stored = HtnStore(demo.store).list_observations(demo.env.mission)
    assert len(stored) == 2


def test_the_denied_alternative_is_refuted_and_the_other_one_applies(demo: DemoWorld) -> None:
    """The scenario §9 could not write: a look decides which branch can be grounded."""

    demo.look(demo.asks(demo.evidence()))
    assert demo.status("demo.primary") is ApplicabilityStatus.PRECONDITION_FALSE
    assert demo.status("demo.fallback") is ApplicabilityStatus.APPLICABLE
    assert demo.draft("demo.fallback").method_ref.method_id == "demo.fallback"


def test_the_refuted_alternative_contributes_no_occurrence(demo: DemoWorld) -> None:
    demo.look(demo.asks(demo.evidence()))
    draft = demo.draft("demo.fallback")
    assert {str(item.slot_key) for item in draft.child_bindings} == {"shared", "b"}


def test_an_observer_that_cannot_answer_leaves_the_choice_unmade(demo: DemoWorld) -> None:
    """OBSERVER_UNAVAILABLE is not FALSE: nothing is written and nothing is decided."""

    demo.observer.answers = {}
    result = demo.look(demo.asks(demo.evidence()))
    assert result.recorded == ()
    assert len(result.unavailable) == 2
    assert all(item.reason is ReadinessReason.OBSERVER_UNAVAILABLE for item in result.unavailable)
    assert HtnStore(demo.store).list_observations(demo.env.mission) == ()
    assert demo.status("demo.primary") is ApplicabilityStatus.NEEDS_EVIDENCE
    assert demo.status("demo.fallback") is ApplicabilityStatus.NEEDS_EVIDENCE


def test_an_observer_that_crashes_is_an_outage_and_not_a_polarity(demo: DemoWorld) -> None:
    def explode(signature, arguments, *, now_ms):
        raise RuntimeError("the reader died")

    demo.observer.observe = explode  # type: ignore[method-assign]
    result = demo.look(demo.asks(demo.evidence()))
    assert result.recorded == ()
    assert len(result.unavailable) == 2
    assert demo.status("demo.primary") is ApplicabilityStatus.NEEDS_EVIDENCE


def test_a_settled_proposition_is_not_looked_at_a_second_time(demo: DemoWorld) -> None:
    """A shared read-only question is asked once; the second round reads the record."""

    demo.look(demo.asks(demo.evidence()))
    assert len(demo.observer.calls) == 2
    assert demo.asks(demo.evidence()) == ()
    assert len(demo.observer.calls) == 2


def test_an_ask_with_no_observer_is_reported_once(demo: DemoWorld) -> None:
    """Review P2-19: one ask, one answer.

    ``run_round`` used to append an unreadable ask to ``unobservable`` and then call
    ``observe_predicate`` on it anyway, so the very same proposition came back twice
    in one result — once as "this deployment has no reader" and once as the
    ``NO_OBSERVER`` outcome saying the same thing.
    """

    ask = evidence_round.EvidenceAsk(
        predicate_ref=ref("demo.nobody-reads-this"),
        arguments={"subject": "alpha"},
        proposition_key="demo.nobody-reads-this#alpha",
    )
    before = len(demo.observer.calls)
    result = demo.look((ask,))
    assert [item.proposition_key for item in result.unobservable] == [ask.proposition_key]
    assert result.outcomes == ()
    assert result.recorded == ()
    assert len(demo.observer.calls) == before, "an ask nobody can read is not a reading"


def test_an_unregistered_observer_type_is_not_looked_at_through_the_index(demo: DemoWorld) -> None:
    """Mutation: running every pending ask would route around the catalogue."""

    asks = evidence_round.pending_asks(
        _conditions(),
        parameters={"subject": "alpha"},
        registry=demo.env.predicates,
        snapshot=demo.world.snapshot(),
    )
    assert len(asks) == 2
    assert evidence_round.asks_for_requests((), asks) == ()


def _conditions():
    from agent_orchestrator.contracts.htn import parse_conditions

    return parse_conditions(
        [
            atom(PRIMARY, {"subject": param("subject")}),
            atom(FALLBACK, {"subject": param("subject")}),
        ],
        "preconditions",
    )


# ======================================================================================
# 4. the context the Planner writes a method from
# ======================================================================================
#
# §7.3 source 4 / §18.5 C8: a typed context that carries no authority vocabulary at
# all.  The Planner proposes the method itself (HTN 精简 片 A); there is no separate
# synthesiser role, and the context carries nothing that addressed one (片 C).


from agent_orchestrator.orchestrator.hierarchical_dispatch import (  # noqa: E402
    HierarchicalDispatch,
)
from agent_orchestrator.planning.htn.method_proposals import (  # noqa: E402
    authority_claims,
)


@dataclass
class SynthWorld:
    """A user Mission the product assembly created (planning begun); the context is the
    one the product's own dispatch builds for its root goal."""

    seed: Any

    @property
    def mission(self) -> Any:
        return self.seed.mission

    @property
    def dispatch(self) -> HierarchicalDispatch:
        return self.seed.loop._new_mode(self.mission)

    def context(self) -> dict[str, Any]:
        from h1i_seed import root_task

        return self.dispatch.method_proposal_context(self.mission.id, root_task(self.mission.id))


@pytest.fixture
def synth(tmp_path) -> Any:
    yield from _open_product_mission(tmp_path, key="synth", criteria=("file:wordfreq.py",), wrap=SynthWorld)


def test_the_method_context_carries_no_authority_vocabulary(synth: SynthWorld) -> None:
    """§18.5: a model proposes the shape of the work, never its authority."""

    request = synth.context()
    assert authority_claims(request) == ()
    assert "mission_id" not in request
    assert set(request["forbidden_fields"]) >= {"mission_id", "registry_status", "budget_account"}


def test_file_and_operation_criteria_say_publishing_is_the_systems(synth: SynthWorld) -> None:
    """2026-09-29 第十三局：写做法的模型不知道发布由系统做（只在打回时才说），给写模块、写
    README 的步骤都写了"在发布目录中给出落点路径"，又自设了发布步骤；审阅员照这句判
    写模块那步不通过，连败两次后向人要发布目录。file:/action: 要求原文后面附上说明。
    产品同形世界里要求书由建任务时写出，用户写的 ``file:wordfreq.py`` 原样带上说明。

    **Mutation**: drop either note, or stop applying it in ``method_proposal_context`` → red."""
    from agent_orchestrator.orchestrator.hierarchical_dispatch import criterion_statement

    file_note = criterion_statement("file:wordfreq.py")
    assert file_note.startswith("file:wordfreq.py") and "发布由系统完成" in file_note
    action_note = criterion_statement("action:file_publish.publish:wordfreq.py")
    assert action_note.startswith("action:file_publish.publish:wordfreq.py")
    assert "由系统执行" in action_note and "不要为它单独设步骤" in action_note
    assert criterion_statement("README 说明用法") == "README 说明用法"

    [shown] = synth.context()["criterion_evidence"]
    assert shown["evidence_requirement"] == file_note


def test_a_proposed_method_is_admitted_against_the_method_width_bound(synth: SynthWorld) -> None:
    """The deployment world's own policy allows a human-authored method wider than the
    bound; a method the Planner proposes is decided against eight."""

    from agent_orchestrator.planning.htn.method_proposals import MAX_PROPOSED_METHOD_STEPS

    assert synth.seed.world.policy().max_steps > MAX_PROPOSED_METHOD_STEPS
    assert synth.dispatch._admission_policy(synth.mission.id).max_steps == MAX_PROPOSED_METHOD_STEPS


def test_an_unusable_capability_is_reported_as_unavailable(tmp_path) -> None:
    """A deployment whose tool set does not cover the workspace step: its operator is
    offered as unavailable and the capability is named (the deployment world computes
    the capability table from the Mission's tools, no test edits it)."""

    from agent_orchestrator.governance.policies import DeploymentPolicy

    tools = ("workspace_read_file",)
    for synth in _open_product_mission(tmp_path, key="unusable", wrap=SynthWorld, allowed_tools=tools,
                                       deployment_policy=DeploymentPolicy(allowed_tools=tools)):
        assert tuple(synth.mission.allowed_tools) == tools
        request = synth.context()
        assert "workspace.prepare" in request["unavailable_capabilities"]
        assert request["operators"] and [item for item in request["operators"] if item["available"]] == []


# ======================================================================================
# 5. mutation self-check
# ======================================================================================
#
# Each mutant below is a plausible wrong implementation of something part 2b added,
# and each is paired with the assertion above that already catches it.  A mutant that
# nothing catches is a test suite that is describing the code rather than checking it.


def test_mutant_a_world_that_trusts_the_declared_capabilities(worktree) -> None:
    """Mutant: ``capabilities()`` returns every id the domain data mentions, healthy.

    Caught by ``test_a_capability_whose_layer_is_not_deployed_is_unavailable_with_a_reason``:
    a host with no ``code_test`` layer would report ``tests.run`` available and admit
    a method against a test runner it cannot start.
    """

    from agent_orchestrator.planning.htn.applicability import CapabilityRecord, CapabilitySnapshot

    world = build_planning_world("m-1", domains=("code",), worktree=worktree, deployed_layers=())
    mutant = CapabilitySnapshot(
        records=tuple(
            CapabilityRecord(capability_id=item) for item in declared_capability_ids(world.catalog)
        )
    )
    assert all(item.available for item in mutant.records)  # the mutant's claim
    assert not all(item.available for item in world.capabilities().records)  # the real answer


def test_mutant_an_index_that_resolves_two_observers_by_order(worktree) -> None:
    """Mutant: ``build_index`` keeps the last observer it sees instead of refusing.

    Caught by ``test_the_shipped_code_observers_cannot_be_indexed_without_an_assignment``
    plus ``test_the_order_is_the_deployments_precedence_declaration``: the shipped
    pack has two authorised readers for one CLOSED predicate, and silently keeping
    one of them means the deployment never states which.
    """

    readers = code_observers(worktree)
    last_wins = {
        name: observer.observer_id for observer in readers for name in observer.predicate_ids()
    }
    assert last_wins["code.working-tree-clean"] == "code.workspace-observer"
    world = build_planning_world("m-1", domains=("code",), worktree=worktree)
    assert (
        world.observer_index.to_json()["predicates"]["code.working-tree-clean"]
        == "code.repo-observer"
    )


def test_mutant_an_evidence_round_that_records_whatever_came_back(demo: DemoWorld) -> None:
    """Mutant: ``run_round`` stores the observation regardless of the outcome.

    Caught by ``test_an_observer_that_cannot_answer_leaves_the_choice_unmade``: an
    outage would be written as a polarity and the OR choice would be decided by a
    service being down.
    """

    demo.observer.answers = {}
    asks = demo.asks(demo.evidence())
    outcomes = [
        observe_predicate(demo.world.observer_index, item.predicate_ref, item.arguments, now_ms=1)
        for item in asks
    ]
    assert all(item.record is None for item in outcomes)  # nothing a mutant could store
    assert HtnStore(demo.store).list_observations(demo.env.mission) == ()


def test_mutant_a_snapshot_cached_at_assembly_time(demo: DemoWorld) -> None:
    """Mutant: ``DeploymentPlanningWorld`` caches ``snapshot()`` on first call.

    Caught by ``test_the_evidence_snapshot_is_read_from_the_store_every_time`` and by
    ``test_the_denied_alternative_is_refuted_and_the_other_one_applies``: a cached
    snapshot means the round *after* an observation still cannot see it, and no
    branch could ever be refuted however much the deployment looks.
    """

    cached = demo.world.snapshot()
    demo.look(demo.asks(demo.evidence()))
    live = demo.world.snapshot()
    assert cached.support_revision == 0
    assert live.support_revision == 2
    assert live.snapshot_id != cached.snapshot_id


# 第 6 节（HTN 补齐阶段 A′）：原来的两条用例（"两条开工通道同时许可""许可写在当时的范围周期"）
# 钉的是前提 / 观测通道，产品部署 ``observers=()`` 走不到；分诊裁决⑥要把它们并进"带观察器的测试
# world_factory"上的参数化主循环用例（阶段 D 接桌面观察器时就是产品路径），本轮没有，先删（偏离：
# 等阶段 D 的 world_factory）。给别处暂留的旧构造器（裸 ``CommitService`` 建任务）已随导入方迁走删掉。
