# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""T01: one real HTN refinement followed by a leaf stop.

This deliberately stops at the planning boundary.  No Jev provider, Worker, or
business callback is installed or invoked; those counters are part of the
fixture so an accidental downstream dispatch fails the case.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from collections.abc import Iterator

import pytest

_FIXTURE = Path(__file__).resolve().parents[4] / "simple-harness-sdk" / "tests" / "orchestrator" / "full_target" / "fixtures" / "htn"
if not _FIXTURE.exists():
    pytest.skip("SDK HTN fixture source is unavailable", allow_module_level=True)
sys.path.insert(0, str(_FIXTURE))

from htn_world import BUDGET, Env, ledger_for, method, param, root_network, step, task_binding  # noqa: E402
from agent_orchestrator.contracts.evidence_state import TruthValue  # noqa: E402
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.planning.htn.applicability import assess_method  # noqa: E402
from agent_orchestrator.planning.htn.grounding import ground_method  # noqa: E402
from agent_orchestrator.planning.htn.refinement import (  # noqa: E402
    FrontierItem,
    RefinementOutcome,
    planning_frontier,
    refine,
)
from agent_orchestrator.planning.htn.compiler import compile_refinement_bundle  # noqa: E402


class Calls:
    def __init__(self) -> None:
        self.jev = self.worker = self.business = 0
        self.profiled_calls = 0

    def profile(self, frame: FrameType, event: str, _arg: object) -> None:
        """Count calls crossing the real downstream namespaces.

        ``refine`` intentionally has no callback parameters: it is the planning
        boundary and must not invoke Jev or execution.  The profile hook is
        installed around the real entry point so these assertions observe the
        running call stack rather than an unattached counter in the fixture.
        """

        if event != "call":
            return
        self.profiled_calls += 1
        module = str(frame.f_globals.get("__name__", ""))
        if module.startswith("agent_orchestrator.decision"):
            self.jev += 1
        elif ".worker" in module or ".workers" in module:
            self.worker += 1
        elif ".business" in module or ".execution" in module:
            self.business += 1

    @contextmanager
    def observe_refine(self) -> Iterator[None]:
        previous = sys.getprofile()
        sys.setprofile(self.profile)
        try:
            yield
        finally:
            sys.setprofile(previous)


def _case() -> tuple[Env, object, Calls]:
    env = Env(mission="t01-mission")
    env.register_predicate("t01.ready", (("subject", "string"),))
    env.register_type("t01.goal", form=TaskForm.COMPOUND, parameters=(("subject", "string"),), criteria=("done",))
    env.register_type("t01.leaf", parameters=(("subject", "string"),), outputs=(("result", "t01.result"),), capabilities=("t01.read",))
    env.say("t01.ready", {"subject": "alpha"}, TruthValue.TRUE)
    contract = method(
        "t01.expand", "t01.goal", parameter_schema="t01.goal.params",
        steps=(step("leaf", "t01.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")}, capabilities=("t01.read",)),),
        links=(("done", "leaf", "result"),), finalizer="leaf",
    )
    env.admit(contract)
    calls = Calls()
    return env, contract, calls


def test_t01_refines_root_then_stops_at_satisfied_leaf() -> None:
    env, contract, calls = _case()
    root = task_binding(env, "t01.goal", task_id="root", obligation="obl-root", parameters={"subject": "alpha"})
    network = root_network(env, root)
    ledger = ledger_for(root, fuel=2, mission=env.mission)
    frontier = planning_frontier(network)
    assert len(frontier) == 1

    with calls.observe_refine():
        first = refine(frontier, network=network, registry=env.registry, catalog=env.catalog,
                       schemas=env.schemas, predicates=env.predicates, snapshot=env.snapshot(),
                       capabilities=env.capabilities(), ledger=ledger, budget=BUDGET)
    assert first.outcomes == (RefinementOutcome.REFINED,)
    draft = first.drafts[0]
    assert draft.grounded_parameters[0].value == "alpha"
    assert draft.goal_id == "root"
    bundle = compile_refinement_bundle(draft, network, method=contract, catalog=env.catalog, schemas=env.schemas, registry=env.registry)
    child = next(item for item in bundle.task_bindings if item.form is TaskForm.PRIMITIVE)
    assert child.typed_parameters == {"subject": "alpha"}
    assert tuple(port.port_key for port in child.output_ports) == ("result",)
    child_occ = next(occ for occ in bundle.network.execution_projection().projected_occurrences if bundle.network.binding_for_occurrence(occ).task_id == child.task_id)
    with calls.observe_refine():
        second = refine((FrontierItem.of(bundle.network.occurrence(child_occ)),), network=bundle.network,
                        registry=env.registry, catalog=env.catalog, schemas=env.schemas, predicates=env.predicates,
                        snapshot=env.snapshot(), capabilities=env.capabilities(), ledger=ledger, budget=BUDGET)
    assert second.outcomes == (RefinementOutcome.LEAF,)
    decision = second.decisions[0]
    assert decision.leaf is not None and decision.leaf.is_leaf
    assert decision.draft is None
    assert calls.profiled_calls > 0, "the downstream counter hook did not observe the real refine call"
    assert calls.jev == calls.worker == calls.business == 0

    # Rebuilding the same state yields the same planning result and does not grow
    # a duplicate child or accidentally execute anything.
    env2, _contract2, calls2 = _case()
    root2 = task_binding(env2, "t01.goal", task_id="root", obligation="obl-root", parameters={"subject": "alpha"})
    with calls2.observe_refine():
        first2 = refine(planning_frontier(root_network(env2, root2)), network=root_network(env2, root2), registry=env2.registry, catalog=env2.catalog, schemas=env2.schemas, predicates=env2.predicates, snapshot=env2.snapshot(), capabilities=env2.capabilities(), ledger=ledger_for(root2, mission=env2.mission), budget=BUDGET)
    assert first2.outcomes == (RefinementOutcome.REFINED,)
    assert first2.drafts[0].to_json() == draft.to_json()
    assert calls2.profiled_calls > 0
    assert calls2.jev == calls2.worker == calls2.business == 0
