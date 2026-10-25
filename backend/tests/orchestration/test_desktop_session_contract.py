"""Desktop Session integration prerequisites and retired protocol boundaries."""

import pytest
from agent_orchestrator.host import HostUnavailable, OrchestrationHost, Session

from deskpet.orchestration.handlers import MESSAGE_TYPES
from deskpet.orchestration.projection import CONTRACT_VERBS


def test_pinned_session_can_open_on_the_existing_task_page_host():
    assert callable(getattr(OrchestrationHost, "open_session", None)), (
        "The installed SDK must provide the published Host session factory"
    )


@pytest.mark.asyncio
async def test_public_session_open_rejects_the_task_page_host_directory_lock(tmp_path, principal):
    host = OrchestrationHost(tmp_path, principal=principal)
    assert host.lock.acquire()
    try:
        with pytest.raises(HostUnavailable, match="另一个实例正在使用编排目录"):
            await Session.open(tmp_path, principal=principal, session_id="desktop-main")
        assert host.lock.held
    finally:
        await host.close()
    assert not host.lock.held


def test_desktop_session_replaces_the_notice_protocol():
    assert "mission_notices" not in MESSAGE_TYPES
    assert "mission_notice_ack" not in MESSAGE_TYPES
    assert "mission_notices" not in CONTRACT_VERBS


def test_task_page_host_commands_remain_available():
    assert {"mission_create", "mission_create_with_sources", "mission_get", "mission_list"} <= set(
        MESSAGE_TYPES
    )
