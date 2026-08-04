from __future__ import annotations

import pytest

from deskpet.execution.contracts import RunEventCandidate
from deskpet.harness.bootstrap import build_harness_runtime
from deskpet.harness.contracts import HostContext, RegisteredDriver
from deskpet.harness.ports import DriverTerminalCandidate
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class RecordingDriver:
    def __init__(self) -> None:
        self.starts = []

    async def start(self, request):
        self.starts.append(request)
        yield DriverTerminalCandidate(request.run_id, "completed", "ok")

    async def signal(self, signal):
        if False:
            yield signal

    async def cancel(self, run_id, reason):
        if False:
            yield RunEventCandidate("unused", "unused")

    async def recover(self, run_id, recovery_lease):
        if False:
            yield run_id

    async def close(self):
        return None


def _profiles() -> ProfileRegistry:
    return ProfileRegistry(
        (
            ProfileSpec(
                "agent.general",
                None,
                "react",
                display_name="General Agent",
                description="The only top-level execution profile.",
                input_schema_ref="profile-input://agent-general/v1",
                launch_policy="reserved_control",
            ),
        )
    )


def _host(session_id: str) -> HostContext:
    return HostContext(
        session_id=session_id,
        principal_id=f"local:{session_id}",
        auth_epoch=1,
        capability_hash="c" * 64,
        available_capabilities=frozenset(),
        provider_plan=("fixture",),
        trace_id=f"trace:{session_id}",
    )


@pytest.mark.asyncio
async def test_every_new_phrase_and_venue_starts_agent_general(tmp_path) -> None:
    driver = RecordingDriver()
    runtime = await build_harness_runtime(
        uow=SqliteExecutionUnitOfWork(tmp_path / "execution.db"),
        profiles=_profiles(),
        root_profile_key="agent.general",
        drivers=(RegisteredDriver("react", driver),),
    )
    try:
        cases = (
            ("做一个 Godot 塔防", "text", "companion"),
            ("Create a Blender scene", "code", "code"),
            ("打开浏览器整理任务", "voice", "auto"),
        )
        handles = []
        for index, (text, venue, mode) in enumerate(cases):
            handles.append(
                await runtime.run_client.start(
                    {
                        "text": text,
                        "request_id": f"request-{index}",
                        "turn_id": f"turn-{index}",
                        "venue": venue,
                        "mode": mode,
                        "payload": {
                            "profile_key": "workflow.deep_research",
                            "driver_kind": "workflow",
                        },
                    },
                    _host("session-1"),
                )
            )
        for handle in handles:
            async for _ in handle.events:
                pass
        assert [item.profile_key for item in driver.starts] == [
            "agent.general",
            "agent.general",
            "agent.general",
        ]
    finally:
        await runtime.close()
