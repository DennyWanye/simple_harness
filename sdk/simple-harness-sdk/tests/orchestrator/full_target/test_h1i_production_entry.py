# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""H1-I production-entry regressions.

These tests deliberately enter through :class:`Orchestrator` and its real
``_create_planner_intent`` / ``_collect_plan_decision`` path, on the product's own
deployment (``h1i_seed``).  Decisions that need an independent review (a proposed
method) go through a real scripted turn of the main loop.  The only injected provider
is the repository's deterministic scripted provider; it keeps this regression suite
offline and leaves the real model run to the H1-I gate.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi  # noqa: E402
from agent_orchestrator.contracts.models import ContractError  # noqa: E402
from agent_orchestrator.contracts.planning_decisions import (  # noqa: E402
    PlanningDecisionStatus,
)
from agent_orchestrator.orchestrator.commit_service import CommitService  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.orchestrator.hierarchical_dispatch import (  # noqa: E402
    HierarchicalDispatch,
)
from agent_orchestrator.orchestrator.plan_commits import PlanCommitRejected  # noqa: E402
from agent_orchestrator.runtime.role_templates import (  # noqa: E402
    PLANNER_HIERARCHICAL,
    PLANNING_DECISION_PACKAGE_VERSION,
)

_FULL_TARGET = Path(__file__).resolve().parent
if str(_FULL_TARGET) not in sys.path:
    sys.path.insert(0, str(_FULL_TARGET))

from h1i_seed import CONFIG, CRITERIA, GOAL, plan_reply, seeded  # noqa: E402

from agent_orchestrator.testing.fixtures import package_of  # noqa: E402
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider  # noqa: E402


def test_the_package_states_its_version_as_the_integer_the_binding_stores() -> None:
    assert Orchestrator._planning_decision_package_version(
        {"package_version": PLANNING_DECISION_PACKAGE_VERSION}
    ) == PLANNING_DECISION_PACKAGE_VERSION
    for malformed in ("planner-package-hierarchical-v8", True, 0, None):
        with pytest.raises(ContractError):
            Orchestrator._planning_decision_package_version({"package_version": malformed})


async def _open_planner_round(
    loop: Orchestrator,
    mission: Any,
    dispatch: HierarchicalDispatch,
    *,
    ordinal: int,
):
    intent = await loop._create_planner_intent(mission.id, ordinal=ordinal)
    assert intent.kind == "plan"
    assert intent.mission_id == mission.id
    from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore
    frozen = PlanningDecisionStore(loop.store).get_mission_protocol(mission.id)
    assert frozen["package_version"] == PLANNING_DECISION_PACKAGE_VERSION
    assert intent.config["prompt_version"] == PLANNER_HIERARCHICAL.prompt_version
    assert intent.config["planning_package"]["planning_protocol"]["protocol"] == (
        "planning-decision-v1"
    )
    return intent


def _events(loop: Orchestrator, mission_id: str, event_type: str) -> list[Any]:
    return [event for event in loop.store.list_events(mission_id) if event.type == event_type]


def test_the_planning_request_is_the_package_and_nothing_appended(tmp_path: Path) -> None:
    async def case() -> None:
        async with seeded(tmp_path, key="h1i-v10") as (loop, mission, _world, _root, dispatch, _product):
            intent = await _open_planner_round(loop, mission, dispatch, ordinal=1)
            assert intent.config["planning_package"]["package_version"] == PLANNING_DECISION_PACKAGE_VERSION
            # One prompt: the wire format is described in the role template only.  A second
            # block of instructions appended to the request used to live here.
            message = intent.config["message"]["content"]
            assert "REMINDER" not in message and "plan_revision_proposal" not in message
            assert message.startswith("## context_builder_version")
            assert intent.config["prompt_version"] == PLANNER_HIERARCHICAL.prompt_version

    asyncio.run(case())


def test_format_retry_keeps_opener_package_and_request_identity(tmp_path: Path) -> None:
    async def case() -> None:
        async with seeded(tmp_path, key="h1i-retry") as (loop, mission, _world, _root, dispatch, _product):
            opener = await _open_planner_round(loop, mission, dispatch, ordinal=1)

            # This is the real collector's codec refusal.  It records UNREADABLE and
            # opens the one permitted same-request format retry.
            await loop._collect_plan_decision(
                opener,
                object(),
                mission,
                "<planning_decision>{not-json}</planning_decision>",
                dispatch,
            )
            opener_request = loop.store.connection.execute(
                "SELECT request_id, intent_id, package_hash, base_plan_revision "
                "FROM planning_requests WHERE request_id = ?",
                (opener.intent_id,),
            ).fetchone()
            assert opener_request is not None
            frozen_request_facts = tuple(opener_request)[2:]
            retry = await _open_planner_round(loop, mission, dispatch, ordinal=2)

            assert retry.config["planning_package"] == opener.config["planning_package"]
            # 2026-09-30：包冻结；消息 = 原消息 + 上一次的字段路径反馈。
            original = opener.config["message"]["content"]
            assert retry.config["message"]["content"].startswith(original)
            assert "previous_feedback" in retry.config["message"]["content"][len(original):]
            request = loop.store.connection.execute(
                "SELECT request_id, intent_id, package_hash, base_plan_revision "
                "FROM planning_requests WHERE request_id = ?",
                (opener.intent_id,),
            ).fetchone()
            assert request is not None
            assert tuple(request)[:2] == (opener.intent_id, retry.intent_id)
            assert tuple(request)[2:] == frozen_request_facts
            assert _events(loop, mission.id, "PlanningRejected")[-1].payload["reason"] == (
                "proposal_unreadable"
            )

    asyncio.run(case())


def test_malformed_decision_retry_reuses_the_frozen_request_package(tmp_path: Path) -> None:
    """A schema rejection may open one retry without changing its request facts."""

    async def case() -> None:
        async with seeded(tmp_path, key="h1i-retry-frozen-package") as (loop, mission, _world, _root, dispatch, _product):
            opener = await _open_planner_round(loop, mission, dispatch, ordinal=1)
            request_before = loop.store.connection.execute(
                "SELECT request_id, package_hash, base_plan_revision, visible_refs_digest "
                "FROM planning_requests WHERE request_id = ?",
                (opener.intent_id,),
            ).fetchone()
            assert request_before is not None
            malformed = (
                '<planning_decision>{"schema_version":1,"decision_type":"REFINE",'
                '"subject_key":"subject-root","rationale":"bad",'
                '"reason_refs":[],"assumptions":["bare-string"],'
                '"payload":{"method_ref":{"kind":"method","id":"code.fix-by-patch",'
                '"semantic_revision":2,"content_hash":"' + "0" * 64 + '"},"bindings":{}},'
                '"uncertainties":[],"alternatives":[],"replan_triggers":[]}'
                "</planning_decision>"
            )
            await loop._collect_plan_decision(
                opener, object(), mission, malformed, dispatch
            )
            request_after = loop.store.connection.execute(
                "SELECT request_id, package_hash, base_plan_revision, visible_refs_digest, "
                "intent_id "
                "FROM planning_requests WHERE request_id = ?",
                (opener.intent_id,),
            ).fetchone()
            assert request_after is not None
            assert tuple(request_after[:4]) == tuple(request_before)
            assert request_after[4] != opener.intent_id
            retry = loop.store.get_intent(request_after[4])
            assert retry is not None and retry.config["planning_package"] == opener.config[
                "planning_package"
            ]

    asyncio.run(case())


def _planner(request: Any) -> Any:
    return plan_reply(package_of(request))


def test_commit_refusal_records_domain_code_and_not_commit_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise PlanCommitRejected("READ_SET_STALE", "deterministic H1-I refusal")

    # Keep the real turn -> collector -> admission -> review -> preview -> commit chain;
    # replace only the durable CommitService refusal boundary so the regression does
    # not depend on a timing race to produce a stale commit.
    monkeypatch.setattr(CommitService, "commit_planning_revision", refuse)

    async def case() -> None:
        provider = LayeredScriptedProvider(planner=_planner)
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            created = world.create({"goal": GOAL, "idempotency_key": "h1i-refusal",
                                    "success_criteria": list(CRITERIA)})
            await world.run_until_settled(created["mission_id"], rounds=6)
            rows = _events(world.loop, created["mission_id"], "PlanningDecisionEvaluated")
            refused = [row for row in rows if row.payload["status"] == str(PlanningDecisionStatus.COMMIT_REJECTED)]
            assert refused, [row.payload for row in rows]
            assert refused[0].payload["rejection_codes"] == ["INTERNAL_CONTRACT_ERROR"]
            assert not _events(world.loop, created["mission_id"], "PlanRevisionCommitted")

    asyncio.run(case())


def test_real_collector_preview_commit_commits_one_plan_revision(tmp_path: Path) -> None:
    async def case() -> None:
        provider = LayeredScriptedProvider(planner=_planner)
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            created = world.create({"goal": GOAL, "idempotency_key": "h1i-commit",
                                    "success_criteria": list(CRITERIA)})
            mission = await world.run_until_settled(created["mission_id"], rounds=12)
            evaluated = [row.payload["status"] for row in
                         _events(world.loop, mission.id, "PlanningDecisionEvaluated")]
            # The proposal registers the method (no plan change); after its independent
            # review the adopted method is committed as exactly one plan revision.
            assert evaluated == [str(PlanningDecisionStatus.NO_STATE_CHANGE),
                                 str(PlanningDecisionStatus.COMMITTED)], evaluated
            assert len(_events(world.loop, mission.id, "PlanRevisionCommitted")) == 1
            assert world.loop._dispatch_for(mission.id).network(mission.id).plan_revision == 1

    asyncio.run(case())


@pytest.mark.parametrize(
    "decision_type",
    ("WAIT", "NO_CHANGE"),
)
def test_state_free_decision_context_has_no_uninitialised_operation_snapshot(
    tmp_path: Path, decision_type: str
) -> None:
    """State-free decisions skip live operation reads and still assemble safely."""

    async def case() -> None:
        async with seeded(tmp_path, key=f"h1i-state-free-{decision_type.lower()}") as (loop, mission, _world, _root, dispatch, _product):
            opener = await _open_planner_round(loop, mission, dispatch, ordinal=1)
            PlanningAuthorizationApi(
                loop.store,
                tenant_id=mission.tenant_id,
                principal=_product.deployment.principal,
            ).issue(
                mission.id,
                command_id=f"grant-h1i-{decision_type.lower()}",
                request_id=opener.intent_id,
            )
            context = loop._hierarchical_admission_context(
                intent=opener,
                mission=mission,
                new_mode=dispatch,
                raw_text="<planning_decision>{}</planning_decision>",
                include_plan_sources=False,
            )
            assert context.operations.snapshot is None
            assert context.operations.unresolved_operations == ()

    asyncio.run(case())
