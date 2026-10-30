"""命令行仅在已验沙箱内运行，输出和终态必须如实返回。"""

from __future__ import annotations

import asyncio
import socket

import pytest
from agent_orchestrator.runtime.sandbox_run import sandbox_run_tool

from agent_orchestrator.artifacts.workspace import WorkspaceManager
from agent_orchestrator.runtime.domain_tools import ToolCallContext
from agent_orchestrator.runtime.sandbox import (
    ExecutionReceipt,
    ProcessOnlyExecutor,
    SandboxUnavailable,
    platform_executor,
    probe_sandbox,
)


@pytest.fixture(scope="module")
def executor():
    try:
        value = platform_executor()
        report = asyncio.run(probe_sandbox(value))
    except (SandboxUnavailable, OSError) as error:
        pytest.skip(f"本机没有可用隔离沙箱：{error}")
    if not report.ok:
        failed = [item["name"] for item in report.items if not item["ok"]]
        pytest.skip(f"本机隔离沙箱启动检查失败：{failed}")
    return value


@pytest.fixture
def workspace(tmp_path):
    manager = WorkspaceManager(tmp_path / "workspaces")
    manager.create("attempt-one", seed={"sub/input.txt": "original"})
    return manager


def _invoke(tool, **arguments):
    return asyncio.run(
        tool.invoke(arguments, ToolCallContext("mission-one", "attempt-one", "run:call"))
    )


def test_sandbox_run_refuses_unisolated_and_unprobed_executors(workspace):
    with pytest.raises(SandboxUnavailable):
        sandbox_run_tool(ProcessOnlyExecutor(), workspace.root)

    class Unprobed:
        isolated = True
        environment_digest = "unprobed"

    with pytest.raises(SandboxUnavailable):
        sandbox_run_tool(Unprobed(), workspace.root)


@pytest.mark.parametrize("cwd", ["..", "a/../sub", "/tmp", "C:\\temp", "sub\\..", ""])
def test_sandbox_run_rejects_cwd_outside_workspace(executor, workspace, cwd):
    tool = sandbox_run_tool(executor, workspace.root)
    with pytest.raises(ValueError, match="cwd"):
        _invoke(tool, argv=["echo", "never"], cwd=cwd)


@pytest.mark.parametrize(
    "arguments",
    [
        {"argv": []},
        {"argv": "echo invalid"},
        {"argv": [False]},
        {"argv": ["echo"], "stdin": "é" * 32769},
        {"argv": ["echo"], "timeout_seconds": 0},
        {"argv": ["echo"], "timeout_seconds": 121},
        {"argv": ["echo"], "timeout_seconds": True},
    ],
)
def test_sandbox_run_validates_limits_before_execute(executor, workspace, arguments):
    with pytest.raises(ValueError):
        _invoke(sandbox_run_tool(executor, workspace.root), **arguments)


def test_sandbox_run_separates_streams_stdin_and_uses_disposable_copy(executor, workspace):
    tool = sandbox_run_tool(executor, workspace.root)
    assert tool.name == "sandbox_run"
    result = _invoke(
        tool,
        argv=[
            executor.interpreter,
            "-c",
            "import pathlib,sys; print(sys.stdin.read()); "
            "print(pathlib.Path('input.txt').read_text(), file=sys.stderr); "
            "pathlib.Path('input.txt').write_text('changed'); sys.exit(7)",
        ],
        stdin="hello",
        cwd="sub",
    )
    assert result["exit_code"] == 7
    assert result["stdout"] == "hello\n"
    assert result["stderr"] == "original\n"
    assert result["truncated"] is False
    assert result["killed_by_timeout"] is False
    assert result["duration_ms"] >= 0
    assert workspace.get("attempt-one").read_text("sub/input.txt") == "original"


def test_sandbox_run_truncates_each_stream_at_64k(executor, workspace):
    result = _invoke(
        sandbox_run_tool(executor, workspace.root),
        argv=[
            executor.interpreter,
            "-c",
            "import sys; sys.stdout.write('x'*80000); sys.stderr.write('y'*90000)",
        ],
    )
    assert result["exit_code"] == 0
    assert result["truncated"] is True
    assert result["stdout"] == "x" * 65536
    assert result["stderr"] == "y" * 65536


def test_sandbox_run_timeout_kills_process_tree(executor, workspace):
    result = _invoke(
        sandbox_run_tool(executor, workspace.root),
        argv=[
            executor.interpreter,
            "-c",
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); "
            "print('started',flush=True); time.sleep(60)",
        ],
        timeout_seconds=1,
    )
    assert result["killed_by_timeout"] is True
    assert result["exit_code"] is None
    assert "started" in result["stdout"]
    assert result.get("status") != "unknown"


def test_sandbox_run_cannot_access_network(executor, workspace):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        code = f"import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',{port}))"
        result = _invoke(
            sandbox_run_tool(executor, workspace.root),
            argv=[executor.interpreter, "-c", code],
        )
    assert result["exit_code"] != 0
    assert "Error" in result["stderr"]


def test_sandbox_run_unconfirmed_end_is_unknown(executor, workspace, monkeypatch):
    async def unfinished(command, *, cwd, spec):
        return ExecutionReceipt(
            execution_id="unfinished",
            kind=executor.kind,
            isolated=True,
            environment_digest=executor.environment_digest,
            effective_limits={},
            exit_code=0,
            output="",
            truncated=False,
            timed_out=False,
            limit_exceeded=None,
            tree_killed=False,
            residual_pids=(12345,),
            status="error",
        )

    monkeypatch.setattr(executor, "execute", unfinished)
    result = _invoke(sandbox_run_tool(executor, workspace.root), argv=["echo", "anything"])
    assert result["status"] == "unknown"
    assert result["exit_code"] is None
