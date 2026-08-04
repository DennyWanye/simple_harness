from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.os_tools.app_tools import app_discover, app_launch
from deskpet.tools.os_tools.process_tools import (
    ProcessToolService,
    process_stop,
    set_process_tool_service,
)


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        root_run_id="root-app",
        run_id="run-app",
        call_id="call",
        effect_id="effect",
    )


@pytest.mark.asyncio
async def test_app_discovery_is_separate_from_launch_probe() -> None:
    result = json.loads(await app_discover({"query": sys.executable}))
    assert result["discovered"] is True
    assert result["candidates"][0]["launchable_probe"] is True
    assert "not proof" in result["note"]


@pytest.mark.asyncio
async def test_app_launch_returns_process_lease_and_second_probe(tmp_path: Path) -> None:
    service = ProcessToolService(log_root=tmp_path / "logs")
    previous = set_process_tool_service(service)
    try:
        result = json.loads(
            await app_launch(
                {
                    "app": sys.executable,
                    "argv": ["-c", "import time; time.sleep(30)"],
                },
                execution_context=_context(),
            )
        )
        assert result["ok"] is True
        assert result["launch_verified"] is True
        assert result["verification"] == "pid_identity_probe"
        stopped = json.loads(
            await process_stop(
                {"lease_id": result["process"]["lease_id"]},
                execution_context=_context(),
            )
        )
        assert stopped["ok"] is True
    finally:
        await service.reset_for_tests()
        set_process_tool_service(previous)
