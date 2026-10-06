# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""第 2 批车道 I1，A02 / A40（Host 半边）：保证通道根隔离时，编排服务起来但不当"可用"，只开非披露
诊断（``mission_assurance_root_diagnostic``）与隔离只读分支（三个保证读动词答 ROOT_QUARANTINED）；
不派发任务、不披露任务标题、不开驱动循环。

隔离本身由 SDK 判（状态文件缺失 / 标记不符 → 管理模式），SDK 侧用例在
``tests/orchestrator/full_target/assurance_exec/test_batch2_root_quarantine.py``。第一条用例把 SDK 的
非披露诊断答案钉成 QUARANTINED，只测 Host 怎么接；最后一条（夜间 N3-07）不替身：用装好的 SDK 轮子
（opt.166 起有隔离判定与 VALIDATED 部署清单）真装根、真删状态文件、重启 Host。
"""

from __future__ import annotations

import pytest

from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import mission_count, notes_provider, notes_request

QUARANTINED = {"state": "QUARANTINED", "execution_allowed": False,
               "current_authentication_required": True, "blocking_code": "ROOT_QUARANTINED"}


def _quarantine(monkeypatch):
    from agent_orchestrator.api.facade import MissionControlV1

    monkeypatch.setattr(MissionControlV1, "assurance_root_diagnostic", lambda self: dict(QUARANTINED))


def _snapshot(mission_id):
    return {"schema_version": 1, "mission_id": mission_id, "view": "CURRENT",
            "at_event_seq": None, "cursor": None, "limit": 100}


@pytest.mark.asyncio
async def test_a_quarantined_root_starts_the_service_in_quarantine_with_only_the_diagnostic_open(
    orchestration_root, principal, monkeypatch
):
    from agent_orchestrator.assurance.contracts import validate

    _quarantine(monkeypatch)
    service = OrchestrationService(orchestration_root, OrchestrationSettings(), provider=notes_provider(),
                                   principal=principal, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        status = service.status()
        assert status["available"] is False and status["state"] == "quarantined"
        assert "隔离" in (status["reason"] or "")
        assert status["assurance_available"] is False
        assert status["assurance_root"] == QUARANTINED
        assert service._driver is None  # 不驱动循环：没有一项任务会被派发

        # 非披露诊断开着：只有状态、能否执行、是否要当前认证、匿名阻塞码、Host 自己的状态
        diagnostic = await handle(service, "mission_assurance_root_diagnostic", {"request_id": "d1"})
        assert diagnostic["payload"]["ok"] is True, diagnostic
        assert diagnostic["payload"]["data"] == {**QUARANTINED, "host_state": "quarantined"}
        junk = await handle(service, "mission_assurance_root_diagnostic", {"request_id": "d2", "mission_id": "m"})
        assert junk["payload"]["ok"] is False and junk["payload"]["error_code"] == "invalid_request"

        # 隔离只读分支：三个保证读动词答 SDK 的 ROOT_QUARANTINED（host-error-v1），不看对象存不存在
        snapshot = await handle(service, "mission_assurance_snapshot", {"request_id": "s1", **_snapshot("mission-x")})
        assert snapshot["payload"]["ok"] is False and snapshot["payload"]["error_code"] == "ROOT_QUARANTINED"
        validate("host-error-v1", snapshot["payload"]["assurance_error"])
        assert snapshot["payload"]["assurance_error"]["request_id"] == "s1"
        use = await handle(service, "mission_assurance_use_check", {
            "request_id": "u1", "schema_version": 1, "mission_id": "mission-x",
            "subject_ref": {"kind": "result", "pin": {"id": "none", "revision": 0, "content_hash": "0" * 64}},
            "view": "CURRENT", "at_event_seq": None})
        assert use["payload"]["error_code"] == "ROOT_QUARANTINED"

        # 其余一律不开：不建任务、不列任务（任务标题不披露）、不读通知
        for msg_type, body in (("mission_create", notes_request("q-1")), ("mission_list", {}),
                               ("mission_notices", {}), ("orchestration_policy_status", {})):
            answer = await handle(service, msg_type, {"request_id": "r", **body})
            assert answer["payload"]["ok"] is False, (msg_type, answer)
            assert answer["payload"]["error_code"] == "orchestration_unavailable", (msg_type, answer)
            assert "隔离" in answer["payload"]["error"]
        assert mission_count(orchestration_root) == 0
    finally:
        await service.close()
    assert service.status()["state"] == "closed"


@pytest.mark.asyncio
async def test_a_native_root_reports_itself_and_the_diagnostic_verb_is_read_only(orchestration_root, principal):
    service = OrchestrationService(orchestration_root, OrchestrationSettings(), provider=notes_provider(),
                                   principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        status = service.status()
        assert status["state"] == "available" and status["assurance_available"] is True
        assert status["assurance_root"] == {"state": "NATIVE", "execution_allowed": True,
                                            "current_authentication_required": False}
        diagnostic = await handle(service, "mission_assurance_root_diagnostic", {"request_id": "d1"})
        assert diagnostic["payload"]["ok"] is True
        assert diagnostic["payload"]["data"] == {"state": "NATIVE", "execution_allowed": True,
                                                 "current_authentication_required": False,
                                                 "host_state": "available"}
        assert mission_count(orchestration_root) == 0  # 诊断不写
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_really_deleted_root_state_file_restarts_the_host_in_quarantine(orchestration_root, principal):
    """夜间 N3-07（A40 真实场景）：不替身诊断。真 SDK 装好保证根 → 停服务 → 真删
    ``assurance-root-state.json`` → 重启 Host。SDK 自己判隔离，Host 只开非披露诊断，状态文件不被写回。"""
    from agent_orchestrator.assurance.root_gate import STATE_FILE

    def make() -> OrchestrationService:
        return OrchestrationService(orchestration_root, OrchestrationSettings(), provider=notes_provider(),
                                    principal=principal, drive=False, native_test_counter=FixtureWordCounter())

    first = make()
    await first.start()
    try:
        assert first.status()["state"] == "available"
        assert first.status()["assurance_root"]["state"] == "NATIVE"
    finally:
        await first.close()
    state_file = orchestration_root / STATE_FILE
    assert state_file.is_file()  # 第一次启动真装了根
    state_file.unlink()

    second = make()
    await second.start()
    try:
        status = second.status()
        assert status["available"] is False and status["state"] == "quarantined", status
        assert "隔离" in (status["reason"] or "")
        assert status["assurance_available"] is False
        root = status["assurance_root"]
        assert root["state"] == "QUARANTINED" and root["execution_allowed"] is False
        assert root["current_authentication_required"] is True and root["blocking_code"] == "ROOT_QUARANTINED"
        assert second._driver is None

        diagnostic = await handle(second, "mission_assurance_root_diagnostic", {"request_id": "d1"})
        assert diagnostic["payload"]["ok"] is True, diagnostic
        data = diagnostic["payload"]["data"]
        assert data["state"] == "QUARANTINED" and data["execution_allowed"] is False
        assert data["blocking_code"] == root["blocking_code"] and data["host_state"] == "quarantined"
        # 非披露：只有这几项，没有路径、任务、对象 id
        assert set(data) <= {"state", "execution_allowed", "current_authentication_required",
                             "blocking_code", "host_state"}, data

        for msg_type, body in (("mission_create", notes_request("q-real")), ("mission_list", {}),
                               ("mission_notices", {}), ("orchestration_policy_status", {})):
            answer = await handle(second, msg_type, {"request_id": "r", **body})
            assert answer["payload"]["ok"] is False, (msg_type, answer)
            assert answer["payload"]["error_code"] == "orchestration_unavailable", (msg_type, answer)
            assert "隔离" in answer["payload"]["error"]
        assert mission_count(orchestration_root) == 0
        assert not state_file.exists() and not state_file.is_symlink()  # 隔离不自动写回状态文件
    finally:
        await second.close()
    assert not state_file.exists()
