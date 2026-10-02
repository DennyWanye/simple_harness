"""2026-09-25 UI 全量点击：自动模式下 Host 代签"授权本轮规划"，并如实记成自动。"""
import pytest

from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from ._word_counter import FixtureWordCounter


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
                                   drive=False, permission_mode_reader=read, native_test_counter=FixtureWordCounter())
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


@pytest.mark.asyncio
async def test_host_duties_are_bound_to_run_between_loop_cycles(tmp_path):
    """NEXT-TG-1.0 2A.1g: one Mission's long turn kept ``run()`` from returning and
    held a new Mission's planning grant for eleven minutes; the Host duties now run
    between the loop's cycles too."""

    control = _Control(PENDING)
    service = _service(tmp_path, "auto", control)
    bound = {}

    class _Loop:
        def set_between_cycles(self, duty, *, every_seconds):
            bound["duty"], bound["every"] = duty, every_seconds

    service._bind_host_duties(_Loop())
    assert bound["every"] == service.settings.tick_active_seconds
    await bound["duty"]()
    assert [c["request_id"] for c in control.issued] == ["r1"]
    service._bind_host_duties(object())  # an SDK without the hook is left alone


def test_a_round_that_changed_nothing_waits_the_long_tick(tmp_path):
    """Real run 2026-09-28: two stuck Missions kept the 2 s tick and a core at 100%."""

    service = _service(tmp_path, "auto", _Control([]))
    marks = iter([(1,), (1,), (2,)])

    class _Store:
        def list_missions(self):
            from types import SimpleNamespace
            return [SimpleNamespace(id="m1", status="ACTIVE")]

        def waiting_on(self, mission_id):
            return []

    class _Loop:
        store = _Store()

        def durable_watermark(self):
            return next(marks)

    service._orchestrator = _Loop()
    service._note_quiet_round()
    assert service._tick() == service.settings.tick_active_seconds  # first round: busy
    service._note_quiet_round()
    assert service._tick() == service.settings.tick_waiting_seconds  # nothing changed
    service._note_quiet_round()
    assert service._tick() == service.settings.tick_active_seconds  # a write: busy again
