# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""2026-09-25 desktop run: the Mission list said "请求已接收" while the detail said "等你处理"
— the list's waiting test ignored planning authorization requests and blocking planning
questions, which the detail snapshot counts.  The list now counts them the same way."""

from __future__ import annotations

from types import SimpleNamespace

import agent_orchestrator.orchestrator.planning_selection as selection
import agent_orchestrator.storage.planning_decision_store as decisions
import agent_orchestrator.storage.planning_human_store as humans

from deskpet.orchestration.service import OrchestrationService


class _Store:
    def __init__(self) -> None:
        self.intents = [
            SimpleNamespace(intent_id="i-auth", mission_id="m-auth"),
            SimpleNamespace(intent_id="i-granted", mission_id="m-run"),
        ]

    def list_intents(self, *states: str) -> list:
        assert set(states) == {"PENDING", "CLAIMED", "AGENT_CREATED"}
        return self.intents

    def list_missions(self, **_kwargs) -> list:
        return [SimpleNamespace(id=m) for m in ("m-auth", "m-run", "m-question", "m-note")]


def _service(store: _Store) -> OrchestrationService:
    service = OrchestrationService.__new__(OrchestrationService)
    service._orchestrator = SimpleNamespace(store=store)
    return service


def test_list_counts_planning_authorization_and_blocking_questions(monkeypatch):
    monkeypatch.setattr(selection, "awaits_authority", lambda _store, intent: intent.intent_id == "i-auth")

    class _Decisions:
        def __init__(self, _store) -> None: ...
        def get_planning_request_for_intent(self, intent_id):
            return object() if intent_id in {"i-auth", "i-granted"} else None

    class _Humans:
        def __init__(self, _store) -> None: ...
        def list(self, mission_id):
            if mission_id == "m-question":
                return [{"state": "PENDING", "request": {"payload": {"blocking": True}}}]
            if mission_id == "m-note":
                return [{"state": "PENDING", "request": {"payload": {"blocking": False}}}]
            return []

    monkeypatch.setattr(decisions, "PlanningDecisionStore", _Decisions)
    monkeypatch.setattr(humans, "PlanningHumanStore", _Humans)
    assert _service(_Store())._planning_waits() == {"m-auth", "m-question"}


def test_a_failing_read_never_breaks_the_list(monkeypatch):
    def broken(_store, _intent):
        raise RuntimeError("store closed")

    monkeypatch.setattr(selection, "awaits_authority", broken)
    assert _service(_Store())._planning_waits() == set()
