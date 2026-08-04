"""Disposable spike: dynamically add, call, remove, and crash a real MCP server."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
SERVER = Path(__file__).with_name("spike_d_mcp_server.py").resolve()
sys.path.insert(0, str(BACKEND_ROOT))

from deskpet.mcp.manager import MCPManager, _ServerRuntime  # noqa: E402
from deskpet.tools.registry import ToolRegistry  # noqa: E402


def config(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "enabled": True,
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(SERVER)],
        "env": {},
    }


async def add_server(manager: MCPManager, entry: dict[str, Any]) -> _ServerRuntime:
    """Spike-only adapter using the manager's existing connect primitive."""
    name = str(entry["name"])
    async with manager._lock:
        if name in manager._servers:
            raise ValueError("duplicate server")
        runtime = _ServerRuntime(name, entry)
        manager._servers[name] = runtime
    try:
        await manager._connect_once(runtime)
    except BaseException:
        async with manager._lock:
            manager._servers.pop(name, None)
        raise
    return runtime


async def safe_teardown(manager: MCPManager, runtime: _ServerRuntime) -> str | None:
    """Close the AnyIO-owned stack in the same task that opened it.

    The production manager currently wraps ``exit_stack.aclose()`` in
    ``asyncio.wait_for()``, which creates a child task.  AnyIO's stdio
    transport owns cancel scopes that must be exited by their entering task,
    so this spike deliberately exercises the proposed lifecycle-owner seam.
    """
    manager._drop_tools(runtime)
    stack = runtime.exit_stack
    runtime.exit_stack = None
    runtime.session = None
    if stack is None:
        return None
    try:
        await stack.aclose()
    except BaseException as exc:  # noqa: BLE001 - cleanup evidence for spike
        return repr(exc)
    return None


async def remove_server(manager: MCPManager, name: str) -> str | None:
    """Spike-only dynamic removal with same-task transport teardown."""
    async with manager._lock:
        runtime = manager._servers.get(name)
    if runtime is None:
        return None
    if runtime.reconnect_task is not None:
        runtime.reconnect_task.cancel()
        await asyncio.gather(runtime.reconnect_task, return_exceptions=True)
        runtime.reconnect_task = None
    teardown_error = await safe_teardown(manager, runtime)
    runtime.state = "stopped"
    async with manager._lock:
        manager._servers.pop(name, None)
    return teardown_error


async def main() -> None:
    registry = ToolRegistry()
    manager = MCPManager({"enabled": True, "servers": []}, registry)
    manager._loop = asyncio.get_running_loop()
    revisions = [registry.catalog_snapshot().revision]
    result: dict[str, Any] = {}
    try:
        live = await add_server(manager, config("spike_live"))
        revisions.append(registry.catalog_snapshot().revision)
        echo_name = "mcp_spike_live_echo"
        result["live_add"] = {
            "state": live.state,
            "tool_registered": registry.get(echo_name) is not None,
        }
        echo = await manager.mcp_call("spike_live", "echo", {"value": "hello"})
        result["echo_call"] = {
            "has_error": "error" in echo,
            "serialized_result": echo,
        }
        live_teardown_error = await remove_server(manager, "spike_live")
        revisions.append(registry.catalog_snapshot().revision)
        result["live_remove"] = {
            "server_removed": "spike_live" not in manager.server_state(),
            "tool_unregistered": registry.get(echo_name) is None,
            "same_task_teardown_error": live_teardown_error,
        }

        crashed = await add_server(manager, config("spike_crash"))
        revisions.append(registry.catalog_snapshot().revision)
        crash_name = "mcp_spike_crash_crash"
        result["crash_add"] = {
            "state": crashed.state,
            "tool_registered": registry.get(crash_name) is not None,
        }
        async with asyncio.timeout(5):
            crash_result = await manager.mcp_call("spike_crash", "crash", {})
        await asyncio.sleep(0)
        revisions.append(registry.catalog_snapshot().revision)
        result["server_crash"] = {
            "structured_error": crash_result.get("error"),
            "manager_state": manager.server_state().get("spike_crash"),
            "tools_dropped": registry.get(crash_name) is None,
            "backend_spike_continued": True,
        }
    finally:
        cleanup_errors: dict[str, str] = {}
        for name in list(manager._servers):
            cleanup_error = await remove_server(manager, name)
            if cleanup_error is not None:
                cleanup_errors[name] = cleanup_error
        result["cleanup_errors"] = cleanup_errors

    result["catalog_revisions"] = revisions
    result["revision_monotonic"] = all(
        later > earlier for earlier, later in zip(revisions, revisions[1:])
    )
    result["all_tools_gone_after_stop"] = all(
        registry.get(name) is None
        for name in (
            "mcp_spike_live_echo",
            "mcp_spike_live_crash",
            "mcp_spike_crash_echo",
            "mcp_spike_crash_crash",
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
