# ruff: noqa: E402 -- shared step07 fixtures require the test path.
"""Executable A06 regression draft (intentionally PARTIAL).

Both halves use production Stores and APIs.  The planning half runs on the product's
deployment: the main loop proposed a method, had it reviewed and opened the adoption
round (``h1i_seed.reviewed``); the planner's reply goes through the real collector.
The action half still uses the action-ledger fixture: an unapproved external write is
joined to the product world together with representative case 3.  These tests prove
both enforcement points independently and must not be mapped as full A06 coverage
until a real origin producer joins them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import sys
from pathlib import Path

_SDK_TESTS = Path(__file__).resolve().parent.parent
_FULL_TARGET = _SDK_TESTS / "full_target"
_STEP07 = _SDK_TESTS / "step07"
for _test_dir in (_FULL_TARGET, _STEP07):
    if str(_test_dir) not in sys.path:
        sys.path.insert(0, str(_test_dir))

from helpers_step07 import ENABLED, candidate, ledger_service  # noqa: E402
from h1i_seed import events as _events  # noqa: E402
from h1i_seed import plan_reply, reviewed  # noqa: E402
from test_htn_store import envelope  # noqa: E402

from agent_orchestrator.contracts.planning_decisions import PlanningDecisionStatus
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
from simple_harness.contracts import canonical_json


def _revision_count(loop, mission_id: str) -> int:
    return len(HtnStore(loop.store).list_plan_revisions(mission_id))


def test_a06_zero_declared_approvals_and_applicable_method_do_not_replace_grant(tmp_path):
    """Method applicability is descriptive; withdrawn authority still fails closed.

    The person revokes the round's planning grant before the planner's reply arrives;
    the reply names no approval and adopts an applicable, independently reviewed method,
    and is still refused without any plan or action write."""

    async def case() -> None:
        async with reviewed(tmp_path, key="h1h-a06-no-grant") as ((loop, mission, _world, _root, dispatch, product), opener, _provider):
            package = opener.config["planning_package"]
            assert package["method_selection"][0]["applicable"]
            binding = PlanningAdmissionStore(loop.store).get_request_binding(opener.intent_id)
            assert binding is not None
            product.control.planning_authorization({
                "operation": "revoke", "grant_id": binding["grant_id"],
                "expected_revision": int(binding["grant_revision"]),
                "command_id": "person-revoke-h1h-a06", "reason": "the person withdrew it",
            })
            # No approval is named by the planner decision.  This must not be
            # confused with the separate durable planning-lane grant.
            reply = plan_reply(package)
            before_revisions = _revision_count(loop, mission.id)
            before_actions = len(loop.store.list_actions(mission.id))

            await loop._collect_plan_decision(opener, object(), mission, reply, dispatch)

            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert stored is not None
            assert stored["status"] == str(PlanningDecisionStatus.REJECTED)
            # A request is never dispatched before it is authorised
            # (``planning_selection.awaits_authority``), so a reply that meets no
            # authority is one whose grant was withdrawn: its binding is stale.  The
            # never-bound code (AUTHORIZATION_REQUIRED) is pinned on a damaged binding by
            # ``test_h1h_authority_matrix.py::test_a02_real_collector_*[missing_binding]``.
            assert stored["rejection_codes"] == ["REQUEST_BINDING_STALE"]
            assert _revision_count(loop, mission.id) == before_revisions
            assert len(loop.store.list_actions(mission.id)) == before_actions
            assert not _events(loop, mission.id, "PlanRevisionCommitted")

    asyncio.run(case())


def _put_complete_operation_link(service, mission, action) -> None:
    task = service.store.get_task(action["task_id"])
    assert task is not None
    frozen = dataclasses.replace(envelope(), mission_id=mission.id, scope_id="mission")
    HtnStore(service.store).bind_operation(frozen, principal_id="origin-principal")
    admission = PlanningAdmissionStore(service.store)
    binding = admission.get_operation_binding(str(frozen.operation_occurrence_id))
    assert binding is not None
    link = {
        "operation_id": binding["operation_id"],
        "request_hash": binding["request_hash"],
        "operation_occurrence_id": binding["operation_occurrence_id"],
        "mission_id": binding["mission_id"],
        "envelope_hash": binding["envelope_hash"],
        "principal_id": binding["principal_id"],
        "scope_id": binding["scope_id"],
        "obligation_id": binding["obligation_id"],
        "producer_task_id": task.id,
        "producer_htn_occurrence_id": "occ-h1h-a06",
        "producer_contract_revision": 1,
        "producer_plan_revision": 1,
        "action_key": action["action_key"],
        "action_id": action["action_id"],
        "action_version": action["version"],
        "params_hash": action["params_hash"],
        "idempotency_key": action["idempotency_key"],
        "provenance_receipt_id": "receipt-h1h-a06",
        "link_hash": hashlib.sha256(b"h1h-a06-link").hexdigest(),
        "link_json": "{}",
    }
    link["link_json"] = canonical_json(link)
    admission.put_operation_action_link(link)


def test_a06_unapproved_external_write_with_complete_origin_link_cannot_handoff(tmp_path):
    """Reach the approval gate, with no missing-link shortcut or connector call."""

    service, mission, tasks, config, connectors, _deployment = ledger_service(tmp_path)
    action = service.propose_action(
        candidate(operation="set"),
        mission_id=mission.id,
        task_id=tasks["A"].id,
        result_id="result-h1h-a06",
        attempt_id=f"{tasks['A'].id}:attempt-h1h-a06",
        artifact_id="artifact-h1h-a06",
        artifact_hash="a" * 64,
        connectors=connectors,
        deployment=ENABLED,
    )
    assert action["state"] == "AWAITING_APPROVAL"
    assert action["required_approvals"] == 1
    _put_complete_operation_link(service, mission, action)
    before_handed_off = len(
        [
            event
            for event in service.store.list_events(mission.id)
            if event.type == "ActionHandedOff"
        ]
    )

    handed, reason = service.begin_handoff(
        action["action_key"],
        owner="h1h-a06-owner",
        lease_seconds=30.0,
        connectors=connectors,
        deployment=ENABLED,
    )

    assert handed is None
    assert reason == "not_ready:AWAITING_APPROVAL"
    stored = service.store.get_action(action["action_key"])
    assert stored is not None and stored["state"] == "AWAITING_APPROVAL"
    assert stored["handoffs"] == 0
    assert service.ledger.reservation(f"action:{action['action_key']}") is None
    assert config.calls == []
    assert (
        len(
            [
                event
                for event in service.store.list_events(mission.id)
                if event.type == "ActionHandedOff"
            ]
        )
        == before_handed_off
    )


def test_a06_valid_grant_allows_real_refine_commit(tmp_path):
    """Positive planning control; kept separate until a real action origin exists."""

    async def case() -> None:
        async with reviewed(tmp_path, key="h1h-a06-valid-grant") as ((loop, mission, _world, _root, dispatch, _product), opener, _provider):
            before = _revision_count(loop, mission.id)
            await loop._collect_plan_decision(
                opener,
                object(),
                mission,
                plan_reply(opener.config["planning_package"]),
                dispatch,
            )
            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert stored is not None
            assert stored["status"] == str(PlanningDecisionStatus.COMMITTED)
            assert _revision_count(loop, mission.id) == before + 1

    asyncio.run(case())
