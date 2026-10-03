# ruff: noqa: E402, E501 -- shared step07 fixtures require the test path.
"""Executable A06 regression draft (intentionally PARTIAL).

Both halves use production Stores and APIs.  The planning half runs on the product's
deployment: the main loop proposed a method, had it reviewed and opened the adoption
round (``h1i_seed.reviewed``); the planner's reply goes through the real collector.
The action half is a variant of representative case 3 on the same product deployment: the
system materialises the publish action with its real operation link and waits for the
person's approval (``helpers_step07.operation_world``).  These tests prove
both enforcement points independently and must not be mapped as full A06 coverage
until a real origin producer joins them.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_SDK_TESTS = Path(__file__).resolve().parent.parent
_FULL_TARGET = _SDK_TESTS / "full_target"
_STEP07 = _SDK_TESTS / "step07"
for _test_dir in (_FULL_TARGET, _STEP07):
    if str(_test_dir) not in sys.path:
        sys.path.insert(0, str(_test_dir))

from h1i_seed import events as _events  # noqa: E402
from h1i_seed import plan_reply, reviewed  # noqa: E402
from helpers_step07 import operation_world, until_pending  # noqa: E402

from agent_orchestrator.contracts.planning_decisions import PlanningDecisionStatus
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.planning_admission_store import PlanningAdmissionStore
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


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


def test_a06_unapproved_external_write_with_complete_origin_link_cannot_handoff(tmp_path):
    """Reach the approval gate, with no missing-link shortcut or connector call.

    代表用例 3 的变体（产品同形世界）：系统按人确认的效果自己物化出发布动作，带完整的真实操作
    链接（T0），停在"等人批准"；此时直接走交接入口，只会因为没批准被拒，不预留、不调用发布服务。"""

    async def case() -> None:
        async with operation_world(tmp_path, key="h1h-a06-unapproved") as world:
            action = await until_pending(world)
            assert action["required_approvals"] == 1
            link = PlanningAdmissionStore(world.store).get_operation_action_link_for_action(action["action_key"])
            assert link is not None and link["action_key"] == action["action_key"]  # a complete, real link
            before_handed_off = len(world.events("ActionHandedOff"))

            handed, reason = world.service.begin_handoff(
                action["action_key"],
                owner="h1h-a06-owner",
                lease_seconds=30.0,
                connectors=world.connectors,
                deployment=world.deployment,
            )

            assert handed is None
            assert reason == "not_ready:AWAITING_APPROVAL"
            stored = world.store.get_action(action["action_key"])
            assert stored is not None and stored["state"] == "AWAITING_APPROVAL"
            assert stored["handoffs"] == 0
            assert world.service.ledger.reservation(f"action:{action['action_key']}") is None
            assert world.publish_ledger() == [] and world.published_files() == []
            assert len(world.events("ActionHandedOff")) == before_handed_off

    asyncio.run(case())


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
