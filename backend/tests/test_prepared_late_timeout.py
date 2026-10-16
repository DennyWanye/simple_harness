"""FOLLOWUPS F-6：预备调用超时、登记为晚到挂起且设置了就绪回调时，超时分支不能抛名字未定义。"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.effects import ToolOutcomeState


def _schema(name: str) -> dict:
    return {
        "name": name,
        "description": name,
        "parameters": {"type": "object", "properties": {"value": {"type": "string"}}},
    }


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope",
        session_id="session",
        request_id="request",
        root_run_id="root-run",
        turn_id="turn",
        workspace=".",
        capability_hash="capability-hash",
        scope_hash="scope-hash",
        run_id="run-late",
        call_id="call-late",
        effect_id="effect-late",
        trace_id="trace",
    )


@pytest.mark.asyncio
async def test_sync_timeout_with_ready_callback_reports_timeout_and_fires_later() -> None:
    registry = ToolRegistry()

    def slow(args, task_id):
        time.sleep(0.3)
        return json.dumps({"ok": True})

    registry.register("slow_probe", "test", _schema("slow_probe"), slow, timeout_seconds=0.05)
    ready: list[str] = []
    registry._set_prepared_ready_callback(ready.append)
    prepared = registry.prepare_call("slow_probe", {"value": "x"}, "session", "call-late")

    outcome = await registry.execute_prepared(
        prepared, effect_id="effect-late", execution_context=_context()
    )

    assert outcome.state is not ToolOutcomeState.SUCCESS
    assert registry._prepared_execution_metadata["effect-late"] == {"late_pending": True}
    for _ in range(50):
        if ready:
            break
        await asyncio.sleep(0.02)
    assert ready == ["run-late"]
