# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-6: human approvals (original §22) through the Host service.

Draft written before the implementation (plan 2026-09-11 H2/H3).  The action path uses the
SDK's approval-action fixtures and its dedicated *test* configuration service; passing here
grants nothing for production.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.testing.fixtures import APPROVAL_SEED, APPROVAL_SPEC
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
    OrchestrationSettings,
)

from ._support import (
    SCRIPTED_LANE,
    judgment_provider,
    judgment_request,
    notes_request,
    review_provider,
)


def _approval_request(key: str) -> dict:
    return {
        "goal": APPROVAL_SPEC["goal"],
        "success_criteria": list(APPROVAL_SPEC["success_criteria"]),
        "idempotency_key": key,
        "budget": {"max_tokens": 200_000, "max_attempts": 8},
    }


async def _approval_service(root, principal) -> OrchestrationService:
    service = OrchestrationService(
        root,
        OrchestrationSettings(),
        principal=principal,
        test_scenario="approval-action",  # fixtures provider + TestConfigService (plan §3.7)
        drive=False,
    )
    await service.start()
    return service


@pytest.mark.asyncio
async def test_action_approval_then_single_handoff_completes(orchestration_root, principal):
    service = await _approval_service(orchestration_root, principal)
    try:
        created = service.create_mission(_approval_request("k-approve"))
        await service.drain()
        pending = service.approvals(created["mission_id"])
        assert [p["kind"] for p in pending] == ["action"]
        assert pending[0]["action"]["reason"]["source"] == "model"  # never the system's words

        decided = service.decide(pending[0]["request_id"], "approve")
        assert decided["request_state"] == "GRANTED"
        await service.drain()

        detail = service.mission_detail(created["mission_id"])
        assert detail["mission"]["status"] == "COMPLETED"
        actions = detail["actions"]
        assert len(actions) == 1 and actions[0]["state"] == "SUCCEEDED"
        decisions = [a for a in detail["approvals"] if a["request_id"] == pending[0]["request_id"]]
        assert decisions[0]["granted_by"] == [principal.principal_id]  # identity from the caller
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_reject_needs_a_reason_and_never_runs_the_action(orchestration_root, principal):
    service = await _approval_service(orchestration_root, principal)
    try:
        created = service.create_mission(_approval_request("k-reject"))
        await service.drain()
        request_id = service.approvals(created["mission_id"])[0]["request_id"]

        with pytest.raises(OrchestrationRequestError) as refused:
            service.decide(request_id, "reject", reason="  ")
        assert refused.value.code == "invalid_request"
        assert service.approvals(created["mission_id"])[0]["state"] == "PENDING"

        service.decide(request_id, "reject", reason="这次先不改测试配置")
        await service.drain()
        detail = service.mission_detail(created["mission_id"])
        assert [a["state"] for a in detail["actions"]] == ["REJECTED"]  # never handed off
        # the SDK step-7 semantics, fixed in the protocol (plan review P1-3)
        assert detail["mission"]["status"] == "FAILED"
        assert detail["mission"]["stop_reason"] == "approval_rejected"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_auto_authorization_mode_never_decides_an_orchestration_approval(
    orchestration_root, principal
):
    """D4: the Host auto / manual mode governs chat tool effects only."""

    service = await _approval_service(orchestration_root, principal)
    try:
        service.note_authorization_mode("auto")  # what the Host tells the service, if anything
        created = service.create_mission(_approval_request("k-auto"))
        await service.drain()
        await service.drain()
        assert [p["state"] for p in service.approvals(created["mission_id"])] == ["PENDING"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_review_request_pass_accepts_the_result(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=review_provider(),
        principal=principal,
        drive=False,
        test_scenario=SCRIPTED_LANE,  # 脚本化旧协议 Provider 只在夹具通道可用（见 _support）
    )
    await service.start()
    try:
        created = service.create_mission(notes_request("k-review"))
        await service.drain()
        pending = service.approvals(created["mission_id"])
        assert [p["kind"] for p in pending] == ["review"]
        service.decide(pending[0]["request_id"], "review_pass", note="看过了，要点齐全")
        await service.drain()
        assert service.mission_detail(created["mission_id"])["mission"]["status"] == "COMPLETED"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_secret_in_reason_or_comment_is_refused_and_not_written(
    orchestration_root, principal
):
    service = await _approval_service(orchestration_root, principal)
    try:
        created = service.create_mission(_approval_request("k-secret"))
        await service.drain()
        request_id = service.approvals(created["mission_id"])[0]["request_id"]
        secret = "sk-" + "c" * 32
        with pytest.raises(OrchestrationRequestError) as refused:
            service.decide(request_id, "reject", reason=f"密钥 {secret}")
        assert refused.value.code == "secret_rejected"
        with pytest.raises(OrchestrationRequestError):
            service.comment(created["mission_id"], f"记一下 {secret}")
        assert service.approvals(created["mission_id"])[0]["state"] == "PENDING"
        assert secret not in str(service.mission_detail(created["mission_id"]))
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_judgment_disagreement_is_arbitrated_by_a_person(orchestration_root, principal):
    """HA-19: the Task Critics say met, the independent judge says unmet → arbitration."""

    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=judgment_provider(),
        principal=principal,
        drive=False,
        test_scenario=SCRIPTED_LANE,  # 脚本化旧协议 Provider 只在夹具通道可用（见 _support）
    )
    await service.start()
    try:
        created = service.create_mission(judgment_request("k-judge"))
        await service.drain()
        pending = service.approvals(created["mission_id"])
        assert [p["kind"] for p in pending] == ["arbitration"]
        request_id = pending[0]["request_id"]
        with pytest.raises(OrchestrationRequestError) as refused:
            service.decide(request_id, "arbitrate", ruling="met", basis="  ")
        assert refused.value.code == "invalid_request"  # the basis is required
        service.decide(request_id, "arbitrate", ruling="met", basis="我读过 A.md 与 B.md，要点齐全")
        await service.drain()
        assert service.mission_detail(created["mission_id"])["mission"]["status"] == "COMPLETED"
    finally:
        await service.close()


def test_approval_seed_is_available_to_the_test_scenario():
    """The test scenario seeds the action format document the fixture Worker reads."""

    assert "docs/ACTION_FORMAT.md" in APPROVAL_SEED
