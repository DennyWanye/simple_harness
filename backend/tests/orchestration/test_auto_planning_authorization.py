"""2026-09-25 UI 全量点击：自动模式下 Host 代签"授权本轮规划"，并如实记成自动。"""
import pytest

from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings


class _Control:
    def __init__(self, pending):
        self.pending = pending
        self.issued: list[dict] = []

    def pending_planning_authorizations(self):
        return list(self.pending)

    def planning_authorization(self, command):
        self.issued.append(dict(command))
        return {"grant_id": "g"}


def _service(tmp_path, mode, control):
    async def read():
        if isinstance(mode, Exception):
            raise mode
        return mode

    service = OrchestrationService(tmp_path, OrchestrationSettings(), principal=object(),
                                   drive=False, permission_mode_reader=read)
    service._state, service._control = "available", control
    return service


PENDING = [{"mission_id": "m1", "request_id": "r1", "intent_id": "i1", "state": "AUTHORIZATION_REQUIRED"}]


@pytest.mark.asyncio
async def test_auto_mode_issues_once_as_host_auto_permission(tmp_path):
    control = _Control(PENDING)
    service = _service(tmp_path, "auto", control)
    assert await service._auto_authorize_planning() == 1
    assert control.issued == [{"operation": "issue", "mission_id": "m1", "request_id": "r1",
                               "command_id": "host-auto-planning:r1",
                               "approval_source": "HOST_AUTO_PERMISSION"}]
    assert await service._auto_authorize_planning() == 0  # same request is not re-issued
    assert len(control.issued) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["manual", RuntimeError("store down")])
async def test_manual_or_unreadable_mode_leaves_the_button(tmp_path, mode):
    control = _Control(PENDING)
    service = _service(tmp_path, mode, control)
    assert await service._auto_authorize_planning() == 0
    assert control.issued == []
