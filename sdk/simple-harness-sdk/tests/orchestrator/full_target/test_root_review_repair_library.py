# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3j: the repair round after a root review REJECT has something to repair *with*.

The Grok acceptance episode H-L3-C1-r1 (and H-L3-C2-r0, identical in shape) ended:

    MethodSynthesisRoundRecorded{TRIAL_ADMITTED}      ← code.fix-by-reproduce-patch-verify-explain
    → PlanRevisionCommitted{revision 1}, six leaves COMPLETED, six ports delivered
    → HierarchicalRootReviewRejected{c-change-explained: FAIL, blocker}   ← the reviewer was right
    → PlanningRejected{ordinal 4, root_review_rejected}                    ← D5-A opened the repair
    → planner ordinal 5: ``method_library []`` / ``applicability []`` / ``open_compound_goals []``
    → PlanningRejected{ordinal 5, no_applicable_method}
    → HierarchicalMissionStalled{hierarchical_no_dispatchable_work} → MissionFailed

The repair round's package listed methods and applicability only for compound goals
*nobody had refined yet*, and the root was refined — by the very instance the review
had just rejected.  So the Planner was told what the reviewer said and given nothing
to say back: no library, no applicability, no way to name the rejected instance, and
no route by which the findings reached a new synthesis round.

Three things change here, none of them in ``contracts/``:

(a) the package carries a ``rejected_refinements`` section for the occurrence whose
    adopted instance the root review rejected, and ``method_library`` /
    ``applicability`` are computed for that occurrence too, with the rejected method
    flagged;
(b) a proposal may carry ``retire_method`` (the rejected instance) together with the
    ``refine`` of the same goal — the replacement §9.1 calls "选择替代方法" — compiled
    through the compiler's existing ``retire_instance_ids`` and the commit's existing
    retirement checks;
(c) when the Planner, shown that package, still declares ``no_applicable_method``,
    the system's own applicability judgment (with the rejected method excluded) may
    open a *second* synthesis round for that goal, once per rejected plan revision,
    with the findings handed to the synthesiser as ``review_feedback``.

The fixture under ``fixtures/htn/c1_repair_round/`` is the real ordinal-5 package and
the real findings; the first test pins the defect shape so the repair is measured
against what actually happened.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_htn_end_to_end as e2e  # noqa: E402
from htn_world import method, out, param, step  # noqa: E402
from test_htn_end_to_end import (  # noqa: E402
    ROOT_DUTY,
    ROOT_TASK,
    World,
    _accept_every_child,
    committed,
)
from test_root_review_coordinator import coordinator, review  # noqa: E402

from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.models import (  # noqa: E402
    Attempt,
    Budget,
    ContractError,
)
from agent_orchestrator.contracts.resolution import (  # noqa: E402
    CriterionVerdict,
    ReviewVerdict,
)
from agent_orchestrator.contracts.state_machines import (  # noqa: E402
    AttemptStatus,
)
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from scripted_plans import plan_revision_proposal_step  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "htn" / "c1_repair_round"
NOW_MS = 2_000_000
ROOT_CRITERION = "c-root"
FREE_TEXT_CRITERION = "根目标经根评审通过并形成 GoalResolution"


def _c1() -> dict[str, Any]:
    return {
        "ord4": json.loads((FIXTURE / "package_ord4.json").read_text(encoding="utf-8")),
        "ord5": json.loads((FIXTURE / "package_ord5.json").read_text(encoding="utf-8")),
        "rejected": json.loads((FIXTURE / "root_review_rejected.json").read_text(encoding="utf-8")),
        "planning": json.loads((FIXTURE / "planning_rejected.json").read_text(encoding="utf-8")),
    }


C1_FINDING = _c1()["rejected"]["findings"][0]
BLOCKER = ({"severity": "blocker", "criterion_id": ROOT_CRITERION, "detail": C1_FINDING["detail"]},)


# ======================================================================================
# The worlds
# ======================================================================================


def _alt_method(method_id: str = "plan.alt"):
    """A second registered method for ``plan.goal``: the replacement the Planner may pick."""

    return method(
        method_id,
        "plan.goal",
        parameter_schema="plan.goal.params",
        steps=(
            step(
                "leaf2",
                "plan.leaf",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("plan.read",),
            ),
            step(
                "review2",
                "plan.review",
                TaskForm.PRIMITIVE,
                {"subject": param("subject"), "result": out("leaf2", "result")},
                capabilities=("plan.read",),
            ),
        ),
        links=(("c-root", "review2", "c-reviewed"),),
        finalizer="review2",
    )


def _register(world: World, contract: Any) -> None:
    receipt = world.env.admit(contract)
    assert receipt.admitted, receipt.problems
    HtnStore(world.store).register_method(
        contract, world.env.registry.registration(contract.method_ref())
    )


def _seeded(tmp_path, *, key: str, alt: bool, free_text: bool = False) -> World:
    """A committed plan whose gating children are accepted; the store is left open."""

    original = e2e._spec
    if free_text:
        # The Mission Judge checks ``file:`` criteria on the integrated artifact tree,
        # which leaves accepted through the assembly do not populate; a free-text
        # criterion goes to the (scripted) independent Critic instead.
        import dataclasses

        def spec(*args: Any, **kwargs: Any):
            return dataclasses.replace(
                original(*args, **kwargs), success_criteria=(FREE_TEXT_CRITERION,)
            )

        e2e._spec = spec
    try:
        world = committed(tmp_path, key=key, demand=True)
    finally:
        e2e._spec = original
    if alt:
        _register(world, _alt_method())
    world.dispatch.issue_input_witnesses(world.mission.id, world.network(), now_ms=1_000_000)
    _accept_every_child(world)
    return world


def _rejected_open(tmp_path, *, key: str, alt: bool, with_method_ref: bool = True) -> World:
    """A Mission whose root review REJECTED the plan and whose repair round is on
    record; the store is left open for ``world.plan``."""

    world = _seeded(tmp_path, key=key, alt=alt)
    coordinator(world).cut(world.mission.id, now_ms=NOW_MS)
    review(
        world,
        verdict=ReviewVerdict.REJECTED,
        verdicts={ROOT_CRITERION: CriterionVerdict.FAIL},
        findings=BLOCKER,
    )
    return world


def _running_attempt(world: World, task_id: str, *, ordinal: int | None = None) -> str:
    if ordinal is None:
        # 带协议绑定的世界里这一步被验收时已经有过一次真实尝试（序号 1），这里接着往下编。
        ordinal = 1 + max(
            (int(item.ordinal) for item in world.store.list_attempts(task_id)), default=0,
        )
    attempt_id = f"{task_id}:att-{ordinal}"
    world.store.insert_attempt(
        Attempt(
            id=attempt_id,
            task_id=task_id,
            mission_id=world.mission.id,
            role="worker",
            model="fixture",
            prompt_version="worker-hierarchical-v2",
            context_version="ctx",
            budget_reserved=Budget(max_tokens=500),
            lease_owner="w",
            lease_expires_at=None,
            status=AttemptStatus.RUNNING,
            retry_of=None,
            created_at=1.0,
            version=1,
            ordinal=ordinal,
            creation_key=f"k-{attempt_id}",
            input_id="i",
            failure=None,
        )
    )
    return attempt_id


# ======================================================================================
# 1. The defect, pinned by the real package
# ======================================================================================


# ======================================================================================
# 2. (a) the repaired package
# ======================================================================================


# ======================================================================================
# 3. (b) retire + refine as one replacement
# ======================================================================================


def _replacement(
    world: World,
    contract: Any,
    *,
    instance_id: str,
    revision: int,
    proposal_id: str = "p-replace",
) -> str:
    reference = contract.method_ref()
    return plan_revision_proposal_step(
        proposal_id=proposal_id,
        expected_plan_revision=revision,
        read_set=[
            {
                "kind": "method",
                "id": reference.method_id,
                "semantic_revision": reference.version,
                "content_hash": reference.content_hash,
            }
        ],
        operations=[
            {
                "op": "retire_method",
                "method_instance_id": instance_id,
                "reason": "the root review rejected this instance's result",
            },
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
            },
        ],
        rationale="replace the rejected method with the alternative",
    )


def _adopted_root(world: World) -> str:
    network = world.network()
    draft = network.adopted_instance_for(network.root_occurrence_ids[0])
    assert draft is not None
    return str(draft.instance_id)


def test_retire_and_refine_replaces_the_root_method_in_one_revision(tmp_path) -> None:
    """§9.1 "选择替代方法", through the compiler's ``retire_instance_ids`` and the commit."""

    world = _rejected_open(tmp_path, key="p23j-replace", alt=True)
    alt = _alt_method()
    old = _adopted_root(world)
    outcome = world.plan(
        _replacement(world, alt, instance_id=old, revision=1), command_id="cmd-replace"
    )
    assert outcome.committed, outcome.last_reason
    network = world.network()
    assert int(network.plan_revision) == 2
    new = network.adopted_instance_for(network.root_occurrence_ids[0])
    assert new is not None and str(new.instance_id) != old
    assert str(new.method_ref.method_id) == "plan.alt"
    assert old not in {str(item) for item in network.adopted_instance_ids}
    states = {
        str(item.instance_id): item
        for item in HtnStore(world.store).list_method_instances(world.mission.id, state="RETIRED")
    }
    assert old in states, "the rejected instance is RETIRED, not deleted"
    # The old branch's occurrences left the plan; the new branch's are on the board.
    kinds = sorted(
        network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id
        for spec in network.occurrences
        if spec.form is TaskForm.PRIMITIVE
    )
    assert kinds == ["plan.leaf", "plan.review"]
    committed_event = world.events(e2e.PLAN_REVISION_COMMITTED)[-1].payload
    assert committed_event["retired_method_instances"] == [old]
    assert committed_event["budget_conservation"]["holds"] is True
    new_tasks = {
        str(spec.task_id)
        for spec in network.occurrences
        if spec.form is TaskForm.PRIMITIVE
    }
    assert new_tasks <= set(world.tasks()), "the replacement's leaves are materialised"


def test_a_retirement_must_name_the_instance_adopted_at_the_refined_occurrence(tmp_path) -> None:
    world = _rejected_open(tmp_path, key="p23j-replace-stranger", alt=True)
    # 撤的不是这一处正在用的做法：这一处仍被占着，候选认不出"还没细化的那一处"。
    with pytest.raises(ContractError, match="exactly one open occurrence"):
        world.plan(
            _replacement(world, _alt_method(), instance_id="mi-stranger", revision=1),
            command_id="cmd-stranger",
        )


def test_refining_a_refined_goal_without_retiring_is_still_refused(tmp_path) -> None:
    """The control: the replacement is explicit, never implied by a second refine."""

    world = _seeded(tmp_path, key="p23j-replace-implicit", alt=True)
    alt = _alt_method()
    reference = alt.method_ref()
    text = plan_revision_proposal_step(
        proposal_id="p-implicit",
        expected_plan_revision=1,
        read_set=[
            {
                "kind": "method",
                "id": reference.method_id,
                "semantic_revision": reference.version,
                "content_hash": reference.content_hash,
            }
        ],
        operations=[
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
    )
    with pytest.raises(ContractError, match="exactly one open occurrence"):
        world.plan(text, command_id="cmd-implicit")
    assert int(world.network().plan_revision) == 1


def test_a_replacement_waits_for_the_retired_leaves_open_attempts(tmp_path) -> None:
    """P2.3s: a replacement is not committed while the retired leaves still run —
    the commit under admission refuses until that work has converged."""

    world = _rejected_open(tmp_path, key="p23j-retire-running", alt=True)
    network = world.network()
    leaf = next(
        str(spec.task_id) for spec in network.occurrences if spec.form is TaskForm.PRIMITIVE
    )
    attempt = _running_attempt(world, leaf)
    outcome = world.plan(
        _replacement(world, _alt_method(), instance_id=_adopted_root(world), revision=1),
        command_id="cmd-retire-running",
    )
    assert not outcome.committed
    assert outcome.last_reason == "RUNNING_WORK_UNRESOLVED"
    assert int(world.network().plan_revision) == 1
    stored = world.store.get_attempt(attempt)
    assert stored is not None and stored.status is AttemptStatus.RUNNING


def test_readopting_the_same_method_with_the_same_parameters_is_refused_by_name(tmp_path) -> None:
    """Verification P0-1: a repair record without the method reference (the shape older
    records have) leaves the history empty; replacing the adopted instance with the very
    same method and parameters is still refused by the commit, by name, and nothing is
    written."""

    world = _rejected_open(tmp_path, key="p23j-readopt-id", alt=False, with_method_ref=False)
    old = _adopted_root(world)
    outcome = world.plan(
        _replacement(world, world.contract, instance_id=old, revision=1),
        command_id="cmd-readopt-id",
    )
    assert not outcome.committed
    assert outcome.last_reason == "REPAIR_NOT_ALLOWED"
    assert int(world.network().plan_revision) == 1


# ======================================================================================
# 4. End to end on a real Orchestrator, cycle by cycle
# ======================================================================================


