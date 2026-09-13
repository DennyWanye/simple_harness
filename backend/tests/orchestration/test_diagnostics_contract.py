# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""P36 PD1/PD2: real facade ownership, fixed export scope and read-only completed data."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from agent_orchestrator.api.facade import MissionControlV1
from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


@pytest.mark.asyncio
async def test_diagnostics_authenticates_before_reading_and_rejects_scope_overrides(
    orchestration_root, principal, monkeypatch
):
    service = OrchestrationService(orchestration_root, OrchestrationSettings(),
        provider=notes_provider(), principal=principal, drive=False)
    await service.start()
    try:
        own = service.create_mission(notes_request("p36-owned"))["mission_id"]
        foreign = MissionControlV1(service._orchestrator, tenant_id="another-user", principal=principal)
        other = foreign.create(notes_request("p36-foreign"))["mission_id"]
        assert service.status()["diagnostics_available"] is True
        def forbidden(*args, **kwargs):
            pytest.fail("diagnostics read ran before ownership/request validation")
        monkeypatch.setattr("deskpet.orchestration.diagnostics.build_diagnostics", forbidden)
        for action in ("mission_diagnostics", "mission_support_export"):
            errors = []
            for mission_id in (other, "missing-mission"):
                result = await handle(service, action, {"mission_id": mission_id}, request_id="auth")
                errors.append(result["payload"])
            assert errors[0] == errors[1]
            assert errors[0]["error_code"] == "not_found"
            for extra in ({"directory": "/tmp/unauthorized"}, {"tenant_id": "another-user"},
                          {"include_history": True}, {"path": "../outside"}):
                result = await handle(service, action, {"mission_id": own, **extra})
                assert result["payload"]["error_code"] == "invalid_request"
        assert not (orchestration_root / "support").exists()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_completed_diagnostics_and_exports_preserve_state_calls_and_artifacts(
    orchestration_root, principal, monkeypatch
):
    provider = notes_provider()
    service = OrchestrationService(orchestration_root, OrchestrationSettings(),
        provider=provider, principal=principal, drive=False)
    await service.start()
    try:
        mission_id = service.create_mission(notes_request("p36-completed"))["mission_id"]
        await service.drain()
        before = service._call("snapshot", mission_id)
        calls = provider.calls
        assert service.status()["diagnostics_available"] is True
        response = await handle(service, "mission_diagnostics", {"mission_id": mission_id})
        assert response["payload"]["ok"] is True, response
        report = response["payload"]["data"]
        assert report["attribution"]["mission_status"] == "COMPLETED"
        assert report["replay"]["comparison"]["consistent"] is True
        assert report["verification"]["results"]
        assert report["verification"]["artifacts"]
        assert report["attribution"]["attempts"]
        receipts = []
        for _ in range(2):
            response = await handle(service, "mission_support_export", {"mission_id": mission_id})
            assert response["payload"]["ok"] is True, response
            receipts.append(response["payload"]["data"])
        assert receipts[0] == receipts[1]
        path = Path(receipts[0]["path"])
        assert path.parent == orchestration_root / "support"
        assert json.loads(path.read_text()) == report
        assert service._call("snapshot", mission_id) == before
        assert provider.calls == calls
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_unadvertised_diagnostics_is_unavailable(orchestration_root, principal, monkeypatch):
    monkeypatch.setattr(OrchestrationService, "_detect_diagnostics", lambda self: False)
    service = OrchestrationService(orchestration_root, OrchestrationSettings(),
        provider=notes_provider(), principal=principal, drive=False)
    await service.start()
    try:
        assert service.status()["diagnostics_available"] is False
        result = await handle(service, "mission_diagnostics", {"mission_id": "m"})
        assert result["payload"]["error_code"] == "diagnostics_unavailable"
    finally:
        await service.close()
