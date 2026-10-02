"""SCRIPT 技能在 Host 沙箱里执行（RP-E3 遗留第 1 项）。

适配器把 SDK 交来的 ScriptRun 放进独立工作区，用模型代码同一个沙箱执行器跑，
只允许 Python；输出文件按上限读回；确认不了结束的记 UNKNOWN。
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from agent_orchestrator.runtime.sandbox import ProcessOnlyExecutor, SandboxUnavailable, SeatbeltExecutor
from deskpet.orchestration.skill_script_runner import SandboxScriptRunner
from simple_harness.agents.arp.pins import Pin
from simple_harness.agents.arp.ports import ScriptRun

SCRIPT = b"""import json, sys
data = json.load(open(sys.argv[1]))
if data.get("mode") == "fail":
    sys.exit(3)
if data.get("mode") == "sleep":
    import time; time.sleep(30)
if data.get("mode") == "link":
    import os; os.symlink(data["target"], sys.argv[2]); sys.exit(0)
if data.get("mode") == "hardlink":
    import os
    try:
        os.link(data["target"], sys.argv[2])
    except OSError:
        sys.exit(0)
    sys.exit(0)
if data.get("mode") == "fifo":
    import os; os.mkfifo(sys.argv[2]); sys.exit(0)
if data.get("mode") == "big":
    data["pad"] = "x" * 5000
json.dump({"n": data["n"] + 1, **({"pad": data["pad"]} if "pad" in data else {})}, open(sys.argv[2], "w"))
"""


def _run(payload: bytes, *, argv=("python3", "scripts/run.py", "{input_json}", "{output_json}"), timeout_ms=20_000, limit=4096) -> ScriptRun:  # type: ignore[no-untyped-def]
    pin = Pin("skill", "s", 1, "0" * 64)
    return ScriptRun(skill_ref=pin, runner_ref=Pin("provider", "p", 1, "0" * 64), script_path="scripts/run.py", script_bytes=SCRIPT,
                     argv=tuple(argv), input_json=payload, workspace_key="skill/sess/use-1", timeout_ms=timeout_ms, output_limit_bytes=limit)


def _executors(tmp_path):  # type: ignore[no-untyped-def]
    found = [ProcessOnlyExecutor(sys.executable, exec_root=tmp_path / "exec-p")]
    if sys.platform == "darwin":
        try:
            found.append(SeatbeltExecutor.for_interpreter(sys.executable, exec_root=tmp_path / "exec-s"))
        except (SandboxUnavailable, TypeError):
            pass
    return found


def test_runs_python_scripts_and_maps_every_end(tmp_path) -> None:
    async def case() -> None:
        for executor in _executors(tmp_path):
            runner = SandboxScriptRunner(executor, tmp_path / f"runs-{executor.kind}")
            ok = await runner.run_async(_run(b'{"n": 1}'))
            assert (ok.terminal, ok.exit_code, ok.output, ok.truncated) == ("EXITED", 0, b'{"n": 2}', False), executor.kind
            assert ok.receipt_ref.id.startswith("sandbox:")
            failed = await runner.run_async(_run(b'{"n": 1, "mode": "fail"}'))
            assert (failed.terminal, failed.exit_code, failed.output) == ("EXITED", 3, None)
            big = await runner.run_async(_run(b'{"n": 1, "mode": "big"}'))
            assert big.output is None and big.truncated is True
            slow = await runner.run_async(_run(b'{"n": 1, "mode": "sleep"}', timeout_ms=500))
            assert slow.terminal == "TIMEOUT" and slow.output is None
            other = await runner.run_async(_run(b'{"n": 1}', argv=("bash", "scripts/run.py", "{output_json}")))
            assert (other.terminal, other.exit_code) == ("EXITED", 127)
            # Workspaces are removed; the executor's own crash-recovery records stay.
            left = {p.name for p in (tmp_path / f"runs-{executor.kind}").iterdir()}
            assert left <= {".sandbox-runs"}, left

    asyncio.run(case())


def test_script_path_cannot_leave_the_workspace(tmp_path) -> None:
    runner = SandboxScriptRunner(ProcessOnlyExecutor(sys.executable, exec_root=tmp_path / "exec"), tmp_path / "runs")
    bad = ScriptRun(**{**{f: getattr(_run(b"{}"), f) for f in ScriptRun.__slots__}, "script_path": "../escape.py"})
    with pytest.raises(ValueError):
        asyncio.run(runner.run_async(bad))
    assert not (tmp_path / "escape.py").exists()


def test_sync_run_outside_a_loop(tmp_path) -> None:
    runner = SandboxScriptRunner(ProcessOnlyExecutor(sys.executable, exec_root=tmp_path / "exec"), tmp_path / "runs")
    assert runner.run(_run(b'{"n": 41}')).output == b'{"n": 42}'


@pytest.mark.parametrize("with_sandbox", [True, False])
def test_native_plane_binds_the_runner_only_with_a_proven_sandbox(tmp_path, with_sandbox) -> None:
    from deskpet.orchestration.native_plane import build_native_pools

    from agent_orchestrator.testing.word_counter import FixtureWordCounter

    counter = FixtureWordCounter()
    executor = ProcessOnlyExecutor(sys.executable, exec_root=tmp_path / "exec") if with_sandbox else None
    plane = build_native_pools(tenant_id="t", principal_id="p", allowed_tools=(), models_dir=None,
                            meter_factory=counter.meter_factory, script_executor=executor)
    assembly = plane.assembly("deepseek-native-256k-v1", tokens=256_000, counter=counter)
    ports = assembly.arp_ports(tmp_path / "execution.sqlite3")
    status = plane.status()["profiles"][0]["script_runner"]
    if with_sandbox:
        assert isinstance(ports.script_runner, SandboxScriptRunner) and status == "sandbox:process_only"
        assert ports.script_runner.workspace_root == tmp_path / "execution.sqlite3.skill-runs"
    else:
        assert ports.script_runner is None and status == "unavailable"


def test_output_file_is_never_followed_out_of_the_workspace(tmp_path) -> None:
    """A script cannot hand back a host file (symlink) or hang the host (FIFO) as its output."""

    secret = tmp_path / "host-secret.json"
    secret.write_text('{"api_key": "not-for-the-model"}')

    async def case() -> None:
        for executor in _executors(tmp_path):
            runner = SandboxScriptRunner(executor, tmp_path / f"runs-{executor.kind}")
            linked = await runner.run_async(_run(('{"n": 1, "mode": "link", "target": "%s"}' % secret).encode()))
            assert linked.terminal == "EXITED" and linked.exit_code == 0 and linked.output is None, executor.kind
            hard = await runner.run_async(_run(('{"n": 1, "mode": "hardlink", "target": "%s"}' % secret).encode()))
            assert hard.output is None, executor.kind
            fifo = await asyncio.wait_for(runner.run_async(_run(b'{"n": 1, "mode": "fifo"}')), timeout=20)
            assert fifo.output is None

    asyncio.run(case())
