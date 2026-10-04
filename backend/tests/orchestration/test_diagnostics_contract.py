# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""P36 PD1/PD2: real facade ownership, fixed export scope and read-only completed data."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from deskpet.orchestration.handlers import handle
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._layered_lane import (
    LayeredScriptedProvider,
    layered_service,
    notes_mission,
    quick_runtime,
    run_until_settled,
)
from ._support import notes_provider, notes_request


def _foreign_copy(store, mission_id: str, *, tenant_id: str) -> str:
    row = store.connection.execute(
        "SELECT * FROM missions WHERE mission_id=?", (mission_id,)
    ).fetchone()
    copied = dict(row)
    other = mission_id + "-foreign"
    document = json.loads(copied["json"])
    document.update({"id": other, "tenant_id": tenant_id, "idempotency_key": "p36-foreign"})
    copied.update(mission_id=other, tenant_id=tenant_id, idempotency_key="p36-foreign",
                  json=json.dumps(document, ensure_ascii=False, sort_keys=True))
    with store.transaction():
        store.connection.execute(
            f"INSERT INTO missions({','.join(copied)}) VALUES ({','.join('?' * len(copied))})",
            tuple(copied.values()),
        )
    return other


@pytest.mark.asyncio
async def test_diagnostics_authenticates_before_reading_and_rejects_scope_overrides(
    orchestration_root, principal, monkeypatch
):
    service = OrchestrationService(orchestration_root, OrchestrationSettings(),
        provider=notes_provider(), principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        own = service.create_mission(notes_request("p36-owned"))["mission_id"]
        # 另一个用户的任务。这个部署的保证通道只为本机用户建任务（建别的用户的任务会被
        # 拒），所以直接在测试库里复制一行、换掉归属，只为验证"别人的任务读不到"。
        other = _foreign_copy(service._orchestrator.store, own, tenant_id="another-user")
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
    quick_runtime(monkeypatch)
    provider = LayeredScriptedProvider()
    service = layered_service(orchestration_root, principal, provider)
    await service.start()
    try:
        mission_id = service.create_mission(notes_mission("p36-completed"))["mission_id"]
        assert (await run_until_settled(service, mission_id)).status.value == "COMPLETED"
        before = service._call("snapshot", mission_id)
        calls = len(provider.asked)
        assert service.status()["diagnostics_available"] is True
        response = await handle(service, "mission_diagnostics", {"mission_id": mission_id})
        assert response["payload"]["ok"] is True, response
        report = response["payload"]["data"]
        assert report["attribution"]["mission_status"] == "COMPLETED"
        assert report["replay"]["status"] == "CONSISTENT" and "library" not in report["replay"]
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
        exported = json.loads(path.read_text())
        # 导出比诊断多一节"核对执行图历史"（HTN 补齐阶段 B 第 2 条），其余逐字相同
        assert exported.pop("taskgraph_history")["replay"]["status"] == "GRAPH_PROJECTION_VERIFIED"
        assert exported == report
        assert service._call("snapshot", mission_id) == before
        assert len(provider.asked) == calls  # diagnostics and exports never call the model
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_unadvertised_diagnostics_is_unavailable(orchestration_root, principal, monkeypatch):
    monkeypatch.setattr(OrchestrationService, "_detect_diagnostics", lambda self: False)
    service = OrchestrationService(orchestration_root, OrchestrationSettings(),
        provider=notes_provider(), principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        assert service.status()["diagnostics_available"] is False
        result = await handle(service, "mission_diagnostics", {"mission_id": "m"})
        assert result["payload"]["error_code"] == "diagnostics_unavailable"
    finally:
        await service.close()
