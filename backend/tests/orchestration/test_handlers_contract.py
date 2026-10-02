# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Control-channel contract (plan 2026-09-11 §3.3): every request answers
``<type>_response {request_id, ok, data | error_code, error}``.

Draft written before the implementation (plan H3).  ``handle`` is the pure dispatcher the
``/ws/control`` loop calls; it never raises into the socket loop.
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.handlers import MESSAGE_TYPES, handle
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


def test_message_types_are_the_planned_set():
    assert set(MESSAGE_TYPES) == {
        "orchestration_status",
        "mission_create",
        "mission_create_with_sources",
        "mission_operation_completion_approve",
        "mission_operation_intent_submit",
        "mission_operation_intent_status",
        "mission_planning_authorization",
        "mission_planning_answer",
        "mission_source_register",
        "mission_source_supersede",
        "mission_source_revoke",
        "mission_list",
        "mission_get",
        "mission_events",
        "mission_cancel",
        "mission_approval_list",
        "mission_approval_decide",
        "mission_takeover",
        "mission_comment",
        "mission_artifact_read",
        "mission_diagnostics",
        "mission_support_export",
        "orchestration_policy_status",
        "orchestration_storage_get",
        "taskgraph.snapshot",
        "taskgraph.why_not_ready",
        "taskgraph.diff",
        "taskgraph.convergence",
        "taskgraph.execution_snapshot",
        "taskgraph.execution_detail",
        "mission_assurance_snapshot",
        "mission_assurance_review",
        "mission_assurance_use_check",
        "agent_runtime_request",
        "agent_skill_evaluation_mission",
        "agent_skill_evaluation_dispatch",
        "agent_skill_request",
        "orchestration_skill_catalogue",
        "orchestration_skill_install_file",
        "orchestration_skill_lifecycle",
        "orchestration_skill_evaluate",
        "orchestration_skill_admit",
    }


@pytest.mark.asyncio
async def test_create_then_get_round_trip(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        created = await handle(
            service, "mission_create", {"request_id": "r1", **notes_request("k-ws")}
        )
        assert created["type"] == "mission_create_response"
        assert created["payload"]["request_id"] == "r1" and created["payload"]["ok"] is True
        mission_id = created["payload"]["data"]["mission_id"]

        got = await handle(service, "mission_get", {"request_id": "r2", "mission_id": mission_id})
        assert got["payload"]["ok"] is True
        assert got["payload"]["data"]["mission"]["id"] == mission_id
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("msg_type", "payload", "code"),
    [
        ("mission_get", {"mission_id": "mission-does-not-exist"}, "not_found"),
        ("mission_create", {"goal": "", "success_criteria": []}, "invalid_request"),
        ("mission_events", {"mission_id": "x", "after_seq": -1}, "invalid_request"),
        ("mission_approval_decide", {"approval_id": "x", "decision": "maybe"}, "invalid_request"),
        ("mission_approval_decide", {"approval_id": "x", "decision": "reject"}, "invalid_request"),
        ("mission_takeover", {"task_id": "x", "action": "stop", "basis": " "}, "invalid_request"),
        ("mission_takeover", {"task_id": "x", "action": "revive", "basis": "b"}, "invalid_request"),
    ],
)
async def test_errors_are_coded_and_never_raise(
    orchestration_root, principal, msg_type, payload, code
):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        response = await handle(service, msg_type, {"request_id": "e1", **payload})
        assert response["payload"]["ok"] is False
        assert response["payload"]["error_code"] == code
        assert response["payload"]["request_id"] == "e1"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_unavailable_service_answers_every_type(principal):
    for msg_type in MESSAGE_TYPES:
        response = await handle(None, msg_type, {"request_id": "u1"})
        if msg_type == "orchestration_status":
            assert response["payload"]["ok"] is True
            assert response["payload"]["data"]["available"] is False
        else:
            assert response["payload"]["error_code"] == "orchestration_unavailable"
