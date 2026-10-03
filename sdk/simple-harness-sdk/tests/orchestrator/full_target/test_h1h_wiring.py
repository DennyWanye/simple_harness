# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""H1-H wiring: the durable request identity survives the bounded format retry.

The new protocol asks the Planner again **only** for a reply the codec could not
read, it asks **at most once**, and the retry is the *same* §34 request seen again
at attempt ordinal 1 — not a fresh request.  The retry therefore reuses the
existing "proposal unreadable -> re-ask with the repair hint" ladder rather than
opening a second loop, and the binding table keeps the id of the intent that
actually answered.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from h1i_seed import events, seeded
from test_h1i_production_entry import _open_planner_round

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.planning_decisions import PLANNING_DECISION_V1
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.role_templates import PLANNING_DECISION_PACKAGE_VERSION
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore

# Every round below is the real Orchestrator's on the product's deployment (``h1i_seed``):
# the Planner intent and its request binding come from ``_create_planner_intent``, the
# reply goes through ``_collect_plan_decision``.

_UNREADABLE = "<planning_decision>{not-json}</planning_decision>"
_FIXTURES = Path(__file__).parent / "fixtures" / "planning_decision_v1" / "valid"


def _grant(loop, mission, product, intent, command_id: str) -> None:
    PlanningAuthorizationApi(
        loop.store, tenant_id=mission.tenant_id, principal=product.deployment.principal
    ).issue(mission.id, command_id=command_id, request_id=intent.intent_id)


def _rejected_reply() -> str:
    """A reply the protocol can read but admission refuses (a subject the request did
    not ask about)."""

    body = json.loads((_FIXTURES / "wait.json").read_text(encoding="utf-8"))
    body["subject_key"] = "subject-not-in-this-request"
    return "<planning_decision>" + json.dumps(body) + "</planning_decision>"


def test_the_format_retry_answers_the_openers_request_once_and_reports_its_budget(tmp_path) -> None:
    """The unreadable reply's ladder, end to end on the real collector.

    * the opener (attempt ordinal 0) never inherits an identity and has one re-ask left —
      the admission context (assembled from one Store view) and the durable refusal both
      say so;
    * the re-ask is the opener's request again: it is bound to it, its admission context
      has no re-ask left, and its own unreadable reply is attempt ordinal 1 of that same
      request;
    * a re-ask may move only the answering intent: a package that drifted (another plan
      revision) cannot bind to the opener's request;
    * after the re-ask, the next round opens a request of its own.
    """

    async def case() -> None:
        async with seeded(tmp_path, key="h1h-wiring-ladder") as (loop, mission, _world, _root, dispatch, product):
            opener = await _open_planner_round(loop, mission, dispatch, ordinal=1)
            _grant(loop, mission, product, opener, "grant-h1h-wiring-opener")
            assert loop._planning_request_retry_id(intent=opener, mission=mission, new_mode=dispatch) is None
            # Authority and operation producers observe one deferred Store view; nested
            # reader views join that same connection rather than opening a second snapshot.
            original_read_view = loop.store.read_view
            read_connections: list[object] = []

            @contextmanager
            def counted_read_view():
                with original_read_view() as connection:
                    read_connections.append(connection)
                    yield connection

            loop.store.read_view = counted_read_view
            try:
                context = loop._hierarchical_admission_context(
                    intent=opener, mission=mission, new_mode=dispatch, raw_text="{}"
                )
            finally:
                loop.store.read_view = original_read_view
            assert len(read_connections) >= 2
            assert len({id(connection) for connection in read_connections}) == 1
            assert context.retry_budgets.same_request_format_retries_remaining == 1
            assert context.package_version == PLANNING_DECISION_PACKAGE_VERSION

            await loop._collect_plan_decision(opener, object(), mission, _UNREADABLE, dispatch)
            rejected = events(loop, mission.id, "PlanningRejected")
            assert len(rejected) == 1
            assert rejected[0].payload["reason"] == "proposal_unreadable"
            assert rejected[0].payload["detail"]["format_retry_remaining"] == 1
            evaluated = events(loop, mission.id, "PlanningDecisionEvaluated")
            assert [(e.payload["request_id"], e.payload["attempt_ordinal"]) for e in evaluated] == [
                (opener.intent_id, 0)
            ]

            retry = await _open_planner_round(loop, mission, dispatch, ordinal=2)
            assert loop._planning_request_retry_id(intent=retry, mission=mission, new_mode=dispatch) == (
                opener.intent_id
            )
            assert loop._planning_format_retry_remaining(intent=retry, mission=mission) == 0
            assert loop._format_retry_exhausted(intent=retry, reason="proposal_unreadable", mission=mission)
            retry_context = loop._hierarchical_admission_context(
                intent=retry, mission=mission, new_mode=dispatch, raw_text="{}"
            )
            assert retry_context.retry_budgets.same_request_format_retries_remaining == 0

            # The re-ask may not change anything but the answering intent.
            drifted = SimpleNamespace(package={
                **retry.config["planning_package"],
                "views": {**retry.config["planning_package"]["views"], "plans": [{"plan_revision": 9}]},
            })
            with pytest.raises(ContractError):
                loop._bind_hierarchical_planning_request(
                    intent=retry, mission=mission, new_mode=dispatch, package=drifted,
                    template=SimpleNamespace(prompt_version=retry.config["prompt_version"],
                                             instructions="prompt"),
                    request_id=opener.intent_id,
                )

            await loop._collect_plan_decision(retry, object(), mission, _UNREADABLE, dispatch)
            decisions = PlanningDecisionStore(loop.store)
            attempts = [decisions.get_planning_decision_by_attempt(opener.intent_id, n) for n in (0, 1)]
            assert [row["status"] for row in attempts] == ["UNREADABLE", "UNREADABLE"]
            evaluated = events(loop, mission.id, "PlanningDecisionEvaluated")
            assert [e.payload["attempt_ordinal"] for e in evaluated] == [0, 1]
            assert {e.payload["request_id"] for e in evaluated} == {opener.intent_id}

            # The collector opened the next round itself; it is a request of its own.
            third = loop.store.get_intent_for_subject(f"{mission.id}:planner:3")
            assert third is not None
            assert loop._planning_request_retry_id(intent=third, mission=mission, new_mode=dispatch) is None
            assert decisions.get_planning_request(third.intent_id) is not None

    asyncio.run(case())


def test_only_a_codec_refusal_reuses_the_request_and_an_unbound_round_fails_closed(tmp_path) -> None:
    """A reply the protocol could read and admission refused is a different answer from
    one it could not read: the next round opens a request of its own.  A round whose
    request binding is gone (damaged store) is never evaluated."""

    async def case() -> None:
        async with seeded(tmp_path, key="h1h-wiring-non-format") as (loop, mission, _world, _root, dispatch, product):
            first = await _open_planner_round(loop, mission, dispatch, ordinal=1)
            _grant(loop, mission, product, first, "grant-h1h-wiring-first")
            await loop._collect_plan_decision(
                first, object(), mission, _rejected_reply(), dispatch
            )
            row = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(first.intent_id, 0)
            assert row is not None and row["status"] == "REJECTED", row

            # The collector opened the next round itself.
            second = loop.store.get_intent_for_subject(f"{mission.id}:planner:2")
            assert second is not None
            assert loop._planning_request_retry_id(intent=second, mission=mission, new_mode=dispatch) is None
            request = PlanningDecisionStore(loop.store).get_planning_request(second.intent_id)
            assert request is not None and request.request_id == second.intent_id

            loop.store.connection.execute("PRAGMA foreign_keys = OFF")
            try:
                loop.store.connection.execute(
                    "DELETE FROM planning_requests WHERE request_id = ?", (second.intent_id,)
                )
            finally:
                loop.store.connection.execute("PRAGMA foreign_keys = ON")
            with pytest.raises(ContractError):
                await loop._collect_plan_decision(second, object(), mission, _UNREADABLE, dispatch)

    asyncio.run(case())


def test_the_http_field_reaches_the_mission_spec_and_its_binding(tmp_path) -> None:
    """§8.1: the charter's protocol field is real behaviour, not a source string.

    Driven through the same ``spec_from_request`` the HTTP door uses, then created through
    the product deployment's authenticated facade (HTN 补齐阶段 A′：不再裸建 ``CommitService``),
    so the assertion is about the stored row rather than about the text of a module.
    """

    from agent_orchestrator.api.missions import MissionRequestError, spec_from_request
    from agent_orchestrator.orchestrator.planning_protocol_binding import (
        planning_protocol_for_mission,
    )
    from agent_orchestrator.testing.product_world import product_world
    from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

    request = {"goal": "goal", "success_criteria": ["done"], "idempotency_key": "http-new",
               "planning_protocol_version": PLANNING_DECISION_V1}
    assert spec_from_request("tenant", request).planning_protocol_version == PLANNING_DECISION_V1

    async def case() -> None:
        async with product_world(tmp_path / "root", LayeredScriptedProvider()) as world:
            store = world.store
            created = world.control.create(request)
            assert created["created"]
            assert planning_protocol_for_mission(store, created["mission_id"])["protocol_version"] == (
                PLANNING_DECISION_V1
            )
            # An omitted key is the same default.
            default = world.control.create(
                {"goal": "goal", "success_criteria": ["done"], "idempotency_key": "http-default"})
            assert planning_protocol_for_mission(store, default["mission_id"])["protocol_version"] == (
                PLANNING_DECISION_V1
            )

    asyncio.run(case())

    # A present-but-unknown value is refused at the door, as a real error.
    with pytest.raises(MissionRequestError):
        spec_from_request(
            "tenant",
            {
                "goal": "goal",
                "idempotency_key": "http-bad",
                "planning_protocol_version": "planning-decision-v99",
            },
        )
    with pytest.raises(MissionRequestError):
        spec_from_request(
            "tenant",
            {
                "goal": "goal",
                "idempotency_key": "http-bad-type",
                "planning_protocol_version": 4,
            },
        )


@pytest.mark.parametrize(
    "raw",
    ["planner-package-unknown", True, None],
    ids=["unknown-string", "bool", "none"],
)
def test_an_unpairable_package_version_is_refused(raw) -> None:
    """§34/§9: only the exact v5 label and a real integer may become a binding.

    The label is translated, so an unknown string must not reach the column as text; and
    ``True``/``False`` must not.  ``bool`` is an ``int`` subclass and type-checks clean,
    so it must be refused explicitly rather than by ``isinstance(raw, int)`` alone.  The
    helper is called directly: this is the unit that decides the column's value.
    """

    with pytest.raises(ContractError):
        Orchestrator._planning_decision_package_version({"package_version": raw})
