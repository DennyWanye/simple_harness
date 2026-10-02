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
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


def _snapshot(mission_id, **overrides):
    body = {"schema_version": 1, "mission_id": mission_id, "view": "CURRENT",
            "at_event_seq": None, "cursor": None, "limit": 100}
    body.update(overrides)
    return body


async def _service(root, principal, **settings):
    service = OrchestrationService(root, OrchestrationSettings(**settings),
                                   provider=notes_provider(missions=2), principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    return service


@pytest.mark.asyncio
async def test_assured_lane_reads_through_fixed_caller_and_survives_rebuild(orchestration_root, principal):
    from agent_orchestrator.assurance.contracts import validate
    from agent_orchestrator.storage.assurance_store import AssuranceStore
    from agent_orchestrator.storage.htn_store import HtnStore

    service = await _service(orchestration_root, principal)
    try:
        status = service.status()
        assert status["assurance_available"] is True
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
async def test_there_is_no_opt_out_of_the_assured_lane(orchestration_root, principal):
    """HTN 精简 片 D 第 6 项（2026-10-02）：不走保证通道的旧审阅路径，在产品里只剩"把保证通道
    关掉"这一个入口（设置项 ``assurance_profile: off``）。保证通道是默认且唯一的通道，开关删除：
    设置里不再有这一项，写了也不起作用，新任务一律走保证通道。"""
    import dataclasses

    from agent_orchestrator.storage.assurance_store import AssuranceStore
    from deskpet.orchestration.settings import load_settings

    assert "assurance_profile" not in {f.name for f in dataclasses.fields(OrchestrationSettings)}
    settings = load_settings({"assurance_profile": "off"})
    service = OrchestrationService(orchestration_root, settings, provider=notes_provider(missions=2),
                                   principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        status = service.status()
        assert status["assurance_available"] is True and "assurance_profile" not in status
        created = await handle(service, "mission_create", {"request_id": "c1", **notes_request("plain-ws")})
        mission_id = created["payload"]["data"]["mission_id"]
        assert AssuranceStore(service._orchestrator.store).lane(mission_id) == "ASSURANCE_1_1"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_check_policy_projection_is_replay_safe_and_needs_a_frozen_scope(orchestration_root, principal):
    """Real model run 2 (2026-09-23): content reviews need the per-Scope check
    policy the Host projects from the confirmed requirements. Before the plan
    freezes a Scope there is nothing to project; the projection never raises."""
    service = await _service(orchestration_root, principal)
    project_check_policies = service._duties.project_check_policies
    try:
        created = await handle(service, "mission_create", {"request_id": "c1", **notes_request("policy-ws")})
        assert created["payload"]["ok"] is True, created
        mission_id = created["payload"]["data"]["mission_id"]
        # 2026-10-01 (HTN 精简 片 A): the one thing there is to project before any Scope
        # exists is the root goal's METHOD_PLAN policy — the Planner proposes its own
        # method for it, and that method's independent review needs the policy first.
        assert project_check_policies() == 1
        assert project_check_policies(mission_id) == 0
        assert service._duties.policy_scopes == {f"method-plan:desktop-root-{mission_id}:1:r1"}
        # 命令号是已有回执的身份（HTN 补齐阶段 A′ 搬进 SDK 时字节不变）
        from agent_orchestrator.assurance.codec import fingerprint
        [receipt_id] = [row[0] for row in service._orchestrator.store.connection.execute(
            "SELECT approval_receipt_id FROM assurance_criterion_policies WHERE mission_id=?", (mission_id,))]
        assert receipt_id == "assurance-check-policy-approval:" + fingerprint({
            "mission": mission_id, "tenant": service.tenant_id, "principal": principal.principal_id,
            "command": f"host-check-policy:method-plan:desktop-root-{mission_id}:1:r1"})
        row = service._orchestrator.store.connection.execute(
            "SELECT COUNT(*) FROM assurance_criterion_policies WHERE mission_id=?", (mission_id,)).fetchone()
        assert row[0] == 1
        # The loop hook is the same function and never fails the loop.
        assert service._project_assurance_policies() == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_check_policy_projector_is_installed_into_the_sdk_review_runtime(orchestration_root, principal):
    """Real model run 7 (2026-09-23): the plan froze the Scope and ran the review in
    one loop run, so the after-run projection was too late. The SDK review runtime
    now calls the Host projector right before preparing a review."""
    service = await _service(orchestration_root, principal)
    try:
        runtime = service._orchestrator._assurance_reviews
        projector = runtime._check_policy_projector
        assert projector is not None
        # Same function as the loop hook: nothing to project before a Scope is frozen.
        assert projector("mission-none") == 0
        assert service._duties.project_check_policies("mission-none") == 0
    finally:
        await service.close()
