# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3a: the Planner's two typed blocks, and what the boundary refuses (§18.5 C8).

The hierarchical Planner does not draw a DAG.  It emits one
``<plan_revision_proposal>`` block — semantic operations plus the read-set it
decided on — or one ``<method_proposal>`` block, and both go through the *real*
codec before anything downstream sees them.  Four properties are pinned here:

1. **A well-formed block becomes a contract object, and nothing more.**  Holding a
   :class:`PlanProposal` proves the shape parsed; it proves nothing about coverage,
   cycles or budgets, which is the compiler's and the Commit Service's business.
2. **Authority is never model-authored.**  Every field the system binds is refused
   at the boundary — in the block, in a ``read_set`` entry and in an operation — and
   refused rather than silently overwritten, so an attempt leaves a trace instead of
   a free probe of the gate.  A *value* that happens to share a name with one of
   those fields (a method parameter called ``scope``) is not a claim.
3. **A malformed block is a BlockError, not a second request.**  Missing, doubled,
   unparseable and non-object bodies all raise through the existing bounded-repair
   path (§18.5 C8), never a new Attempt identity.
4. (2026-10-02) The flat ``<task_graph_proposal>`` parser and the frozen flat Planner
   prompt digests were removed with the flat mode.

The blocks are built with the shipped fixture provider's two new scripted steps, so
none of this depends on a live model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_HTN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "htn"
if str(_HTN_FIXTURES) not in sys.path:
    sys.path.insert(0, str(_HTN_FIXTURES))


from agent_orchestrator.contracts import ContractError  # noqa: E402
from agent_orchestrator.contracts.htn import (  # noqa: E402
    PlanProposal,
    ProposeSuccessorOperation,
    RefineOperation,
    RetireMethodOperation,
    RunningWorkPolicy,
)
from agent_orchestrator.planning.htn.method_proposals import SYSTEM_BOUND_FIELDS  # noqa: E402
from agent_orchestrator.runtime.role_templates import (  # noqa: E402
    PLANNER_HIERARCHICAL,
    PLANNER_HIERARCHICAL_VERSION,
    TEMPLATE_VERSIONS,
    registered_versions,
)
from scripted_plans import plan_revision_proposal_step, scripted_plan_proposal  # noqa: E402

MISSION = "mission-p23a"
HASH_A = "a" * 64
HASH_B = "b" * 64


# ------------------------------------------------------------------ the world builders
def _read_item(**overrides: Any) -> dict[str, Any]:
    item = {"kind": "task", "id": "task-root", "semantic_revision": 1, "content_hash": HASH_A}
    item.update(overrides)
    return item


def _refine(**overrides: Any) -> dict[str, Any]:
    operation = {
        "op": "refine",
        "goal_id": "occ-root",
        "obligation_id": "obl-root",
        "method_ref": {"id": "plan.split", "version": 1, "content_hash": HASH_B},
        "bindings": {"subject": "readme"},
    }
    operation.update(overrides)
    return operation


def _block(**overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "proposal_id": "prop-1",
        "expected_plan_revision": 0,
        "read_set": [_read_item()],
        "operations": [_refine()],
        "rationale": "根目标要先拆成两步",
    }
    kwargs.update(overrides)
    return plan_revision_proposal_step(**kwargs)


def _parse(**overrides: Any) -> PlanProposal:
    return scripted_plan_proposal(_block(**overrides), mission_id=MISSION)


def _refused(**overrides: Any) -> str:
    with pytest.raises(ContractError) as caught:
        _parse(**overrides)
    return str(caught.value)


# ====================================================== a well-formed plan proposal
def test_a_well_formed_block_parses_into_a_plan_proposal():
    proposal = _parse()
    assert isinstance(proposal, PlanProposal)
    assert proposal.proposal_id == "prop-1"
    assert int(proposal.expected_plan_revision) == 0
    assert proposal.rationale == "根目标要先拆成两步"
    assert proposal.running_work_policy is RunningWorkPolicy.RETAIN_IF_BINDINGS_UNCHANGED


def test_the_mission_is_supplied_by_the_caller_not_the_block():
    """Which Mission a proposal belongs to is decided by the request that made it."""

    assert str(_parse().mission_id) == MISSION
    assert "mission_id" not in json.loads(
        _block()
        .removeprefix("<plan_revision_proposal>")
        .removesuffix("</plan_revision_proposal>")
    )


def test_the_read_set_entries_keep_the_revision_and_hash_they_claim():
    proposal = _parse(read_set=[_read_item(), _read_item(kind="method", id="plan.split")])
    assert [str(item.kind) for item in proposal.read_set] == ["task", "method"]
    assert {item.content_hash for item in proposal.read_set} == {HASH_A}


def test_the_operation_kinds_parse():
    proposal = _parse(
        operations=[
            _refine(),
            {"op": "retire_method", "method_instance_id": "mi-1", "reason": "绑定已变"},
            {
                "op": "propose_successor",
                "old_task_id": "task-failed",
                "obligation_id": "obl-root",
                "goal_type_ref": {"id": "plan.leaf", "version": 1, "content_hash": HASH_B},
                "bindings": {"subject": "readme"},
            },
        ]
    )
    assert [type(item) for item in proposal.operations] == [
        RefineOperation,
        RetireMethodOperation,
        ProposeSuccessorOperation,
    ]


def test_a_trigger_ref_the_model_may_legitimately_cite_parses():
    proposal = _parse(
        trigger_refs=[
            {
                "kind": "observation",
                "id": "obs-1",
                "revision": 1,
                "content_hash": HASH_A,
                "produced_by": "model",
            }
        ]
    )
    assert [ref.id for ref in proposal.trigger_refs] == ["obs-1"]


# ============================================================ authority is not authored
def test_a_method_parameter_that_shares_a_name_with_a_bound_field_is_a_value():
    """``bindings`` is domain vocabulary; refusing it there would make the contract
    depend on what a domain happens to call its parameters."""

    proposal = _parse(operations=[_refine(bindings={"scope": "src/", "principal": "reviewer"})])
    operation = proposal.operations[0]
    assert operation.bindings == {"scope": "src/", "principal": "reviewer"}


def test_a_claimed_tool_attribution_inside_a_trigger_ref_is_refused():
    """Not one of the block's own fields, so the deep provenance walk has to catch it."""

    detail = _refused(
        trigger_refs=[
            {
                "kind": "tool_receipt",
                "id": "receipt-1",
                "revision": 1,
                "content_hash": HASH_A,
                "produced_by": "tool",
            }
        ]
    )
    assert "produced_by" in detail


#: Every field the system binds, written out.  The parametrised test above iterates
#: ``SYSTEM_BOUND_FIELDS`` itself, so it can only ever say "each field I refuse is
#: refused" — deleting one from the set deletes its test with it.  This literal is
#: what makes a *shrinking* set fail: adding a field is a deliberate edit in two
#: places, and removing one is caught here.
GOLDEN_BOUND_FIELDS = frozenset(
    {
        "authored_by",
        "authorization_ref",
        "budget_account",
        "grant_ref",
        "mission_id",
        "opened_by",
        "principal",
        "principal_id",
        "provenance",
        "registry_status",
        "scope",
        "scope_id",
    }
)


def test_the_bound_field_set_is_exactly_the_golden_set():
    assert SYSTEM_BOUND_FIELDS == GOLDEN_BOUND_FIELDS
    assert len(GOLDEN_BOUND_FIELDS) == 12


def test_the_golden_set_names_every_authority_channel_the_gate_checks():
    assert {
        "mission_id",
        "scope",
        "principal",
        "budget_account",
        "registry_status",
        "authorization_ref",
        "grant_ref",
        "provenance",
    } <= GOLDEN_BOUND_FIELDS


def test_the_hierarchical_prompt_lists_every_bound_field():
    """A field refused by the parser and absent from the prompt is a trap: the model
    is never told, and its whole proposal is thrown away for writing it."""

    text = PLANNER_HIERARCHICAL.instructions
    assert sorted(field for field in GOLDEN_BOUND_FIELDS if field not in text) == []


# =========================================================== an unreadable block
# =========================================================== the contract's own floors
def test_an_empty_read_set_is_refused():
    assert "read_set" in _refused(read_set=[])


def test_no_operations_is_refused():
    assert "operations" in _refused(operations=[])


def test_a_missing_required_field_is_refused():
    assert "rationale" in _refused(drop=("rationale",))


def test_another_schema_version_is_refused():
    assert "schema_version" in _refused(schema_version=2)


def test_an_unknown_operation_is_refused_with_the_known_ones_named():
    detail = _refused(operations=[{"op": "delete_everything", "goal_id": "occ-root"}])
    assert "refine" in detail and "retire_method" in detail


def test_an_unknown_running_work_policy_is_refused():
    assert "running_work_policy" in _refused(running_work_policy="just_kill_it")


# ============================================================ the template registry
def test_the_hierarchical_planner_is_registered_as_its_own_version():
    assert PLANNER_HIERARCHICAL_VERSION in registered_versions()["planner"]
    assert TEMPLATE_VERSIONS["planner"][PLANNER_HIERARCHICAL_VERSION] is PLANNER_HIERARCHICAL
    assert PLANNER_HIERARCHICAL.name == "planner"


def test_the_hierarchical_planner_asks_for_no_tools():
    """It proposes; it does not act.  A tool on this template would be a way to run
    work without going through a plan revision at all."""

    assert PLANNER_HIERARCHICAL.tool_names == ()


# ============================================================ the scripted blocks
def test_the_fixture_step_never_writes_a_mission_id_of_its_own():
    assert "mission_id" not in plan_revision_proposal_step(
        read_set=[_read_item()], operations=[_refine()]
    )
