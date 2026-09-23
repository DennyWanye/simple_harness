# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""C07 (Host half): the three Assurance read verbs on the authenticated control channel.

Real ``OrchestrationService`` (native root, fixed Principal, SDK deployment assembly),
real Mission creation through the original door, no model turn (``drive=False``).
The UI half is the MissionsView/MissionAssurance vitest suite.
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


def _snapshot(mission_id, **overrides):
    body = {"schema_version": 1, "mission_id": mission_id, "view": "CURRENT",
            "at_event_seq": None, "cursor": None, "limit": 100}
    body.update(overrides)
    return body


async def _service(root, principal, **settings):
    service = OrchestrationService(root, OrchestrationSettings(**settings),
                                   provider=notes_provider(missions=2), principal=principal, drive=False)
    await service.start()
    return service


@pytest.mark.asyncio
async def test_assured_lane_reads_through_fixed_caller_and_survives_rebuild(orchestration_root, principal):
    from agent_orchestrator.assurance.contracts import validate
    from agent_orchestrator.storage.assurance_store import AssuranceStore
    from agent_orchestrator.storage.htn_store import HtnStore

    service = await _service(orchestration_root, principal, assurance_profile="on")
    try:
        status = service.status()
        assert status["assurance_available"] is True and status["assurance_profile"] == "on"
        created = await handle(service, "mission_create", {"request_id": "c1", **notes_request("assured-ws")})
        assert created["payload"]["ok"] is True, created
        mission_id = created["payload"]["data"]["mission_id"]
        store = service._orchestrator.store
        assert AssuranceStore(store).lane(mission_id) == "ASSURANCE_1_1"
        # The factory and the Host root initialization agree on one revision-1 body.
        revisions = HtnStore(store).list_requirements_revisions(mission_id)
        assert [r.revision for r in revisions] == [1]
        assert [c.statement for c in revisions[0].criteria] == ["file:NOTES.md"]
        assert revisions[0].authority_subject == principal.principal_id

        # snapshot: contract body in, contract body out; caller never in the body.
        response = await handle(service, "mission_assurance_snapshot", {"request_id": "s1", **_snapshot(mission_id)})
        assert response["type"] == "mission_assurance_snapshot_response"
        assert response["payload"]["ok"] is True, response
        page = response["payload"]["data"]
        validate("host-response-v1", page)
        assert page["request_id"] == "s1" and page["mission_id"] == mission_id
        kinds = {(item["kind"], item["id"]) for item in page["items"]}
        assert ("CRITERION", "c-user-1") in kinds and ("CLOSEOUT", mission_id) in kinds
        assert len(page["host_fingerprint"]) == 64 and len(page["sdk_fingerprint"]) == 64

        # Errors carry the SDK's host-error-v1 body next to the transport code.
        missing = await handle(service, "mission_assurance_snapshot", {"request_id": "s2", **_snapshot("mission-none")})
        assert missing["payload"]["ok"] is False and missing["payload"]["error_code"] == "not_found"
        assert "assurance_error" not in missing["payload"]  # refused by the original ownership rule
        bad = await handle(service, "mission_assurance_snapshot", {"request_id": "s3", **_snapshot(mission_id, limit=0)})
        assert bad["payload"]["error_code"] == "CONTRACT_INVALID"
        validate("host-error-v1", bad["payload"]["assurance_error"])
        assert bad["payload"]["assurance_error"]["request_id"] == "s3"
        review = await handle(service, "mission_assurance_review", {
            "request_id": "r1", "schema_version": 1, "mission_id": mission_id,
            "review_key": "no-such-review", "cursor": None, "limit": 10})
        assert review["payload"]["error_code"] == "NOT_FOUND"
        validate("host-error-v1", review["payload"]["assurance_error"])
        use = await handle(service, "mission_assurance_use_check", {
            "request_id": "u1", "schema_version": 1, "mission_id": mission_id,
            "subject_ref": {"kind": "result", "pin": {"id": "none", "revision": 0, "content_hash": "0" * 64}},
            "view": "CURRENT", "at_event_seq": None})
        assert use["payload"]["ok"] is True
        validate("host-use-response-v1", use["payload"]["data"])
        assert use["payload"]["data"]["decision"] == "UNAVAILABLE"
        assert use["payload"]["data"]["diagnostic_only"] is True
        # A body cannot name another tenant or principal: unknown fields are refused.
        spoof = await handle(service, "mission_assurance_snapshot",
                             {"request_id": "s4", **_snapshot(mission_id), "tenant_id": "other"})
        assert spoof["payload"]["error_code"] == "CONTRACT_INVALID"

        # Rebuild: the native root replays idempotently and the verbs keep working.
        await service._rebuild()
        assert service.status()["assurance_available"] is True
        again = await handle(service, "mission_assurance_snapshot", {"request_id": "s5", **_snapshot(mission_id)})
        assert again["payload"]["ok"] is True, again
        assert again["payload"]["data"]["root_incarnation_id"] == page["root_incarnation_id"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_profile_off_keeps_the_original_lane_and_refuses_assured_reads(orchestration_root, principal):
    from agent_orchestrator.storage.assurance_store import AssuranceStore

    # Explicit opt-out (the default is "on" since the verified 2026-09-23 delivery).
    service = await _service(orchestration_root, principal, assurance_profile="off")
    try:
        assert service.status()["assurance_profile"] == "off"
        assert service.status()["assurance_available"] is True
        created = await handle(service, "mission_create", {"request_id": "c1", **notes_request("plain-ws")})
        mission_id = created["payload"]["data"]["mission_id"]
        assert AssuranceStore(service._orchestrator.store).lane(mission_id) == "COMPLETION_V1"
        response = await handle(service, "mission_assurance_snapshot", {"request_id": "s1", **_snapshot(mission_id)})
        assert response["payload"]["ok"] is False
        assert response["payload"]["error_code"] == "PROFILE_UNBOUND", response
        assert response["payload"]["assurance_error"]["code"] == "PROFILE_UNBOUND"
    finally:
        await service.close()


def test_settings_parse_assurance_profile():
    from deskpet.orchestration.settings import load_settings

    # Default ON since the verified 2026-09-23 delivery; only an explicit "off" opts out.
    assert load_settings({}).assurance_profile == "on"
    assert load_settings({"assurance_profile": "ON"}).assurance_profile == "on"
    assert load_settings({"assurance_profile": "off"}).assurance_profile == "off"
    assert load_settings({"assurance_profile": "shadow"}).assurance_profile == "on"
    assert load_settings({"assurance_profile": 1}).assurance_profile == "on"


@pytest.mark.asyncio
async def test_check_policy_projection_is_replay_safe_and_needs_a_frozen_scope(orchestration_root, principal):
    """Real model run 2 (2026-09-23): content reviews need the per-Scope check
    policy the Host projects from the confirmed requirements. Before the plan
    freezes a Scope there is nothing to project; the projection never raises."""
    from deskpet.orchestration.assurance import project_check_policies

    service = await _service(orchestration_root, principal, assurance_profile="on")
    try:
        created = await handle(service, "mission_create", {"request_id": "c1", **notes_request("policy-ws")})
        assert created["payload"]["ok"] is True, created
        mission_id = created["payload"]["data"]["mission_id"]
        assert project_check_policies(service) == 0
        assert project_check_policies(service, mission_id) == 0
        assert service._assurance_policy_scopes == set()
        # The loop hook is the same function and never fails the loop.
        assert service._project_assurance_policies() == 0
    finally:
        await service.close()
