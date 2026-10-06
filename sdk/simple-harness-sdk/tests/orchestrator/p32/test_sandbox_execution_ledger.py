# SPDX-License-Identifier: Apache-2.0
"""子进程身份与回收材料落库（原计划 §14.4；第 2 批车道 J H04）。

* 执行器起子进程就把进程组/会话/启动时间/命令行/沙箱根记进账本，收尾记结果；账本写不进去 → 子进程
  先收掉再报不可用；
* 重启后的孤儿回收按落库身份核对：启动时间对得上才终止；进程号被别人复用（身份对不上）一个不碰；
  进程不在了记 gone；
* ``StoreSandboxLedger`` 落到编排库，归属从执行副本目录名读。

**改坏检验**：``reclaim_recorded_executions`` 里去掉 ``identity_matches`` 核对 → 第 3 条红（陌生进程被杀）。
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_orchestrator.artifacts.workspace import EXEC_COPY_MARK
from agent_orchestrator.runtime.sandbox import (
    ProcessOnlyExecutor,
    SandboxSpec,
    SandboxUnavailable,
    identity_matches,
    process_identity,
    reclaim_recorded_executions,
)
from agent_orchestrator.storage.recovery_store import RecoveryStore, StoreSandboxLedger
from agent_orchestrator.storage.store import Store

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="只做 macOS（用户决定 A#9）")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_dead(pid: int, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        time.sleep(0.05)
    return not _alive(pid)


class _Ledger:
    def __init__(self, fail: bool = False) -> None:
        self.started: list[dict] = []
        self.finished: list[tuple[str, str]] = []
        self.fail = fail

    def record_start(self, record) -> None:
        if self.fail:
            raise RuntimeError("ledger down")
        self.started.append(dict(record))

    def record_finish(self, execution_id: str, outcome: str) -> None:
        self.finished.append((execution_id, outcome))


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / f"attempt-1{EXEC_COPY_MARK}abc123"
    root.mkdir()
    return root


def test_execute_records_identity_at_start_and_outcome_at_finish(workspace, tmp_path):
    executor = ProcessOnlyExecutor(exec_root=tmp_path / "exec")
    ledger = _Ledger()
    executor.ledger = ledger
    receipt = asyncio.run(executor.execute(
        [sys.executable, "-c", "import os; print(os.getpid())"], cwd=str(workspace),
        spec=SandboxSpec(cpu_seconds=10, wall_seconds=20)))
    assert receipt.exit_code == 0
    [record] = ledger.started
    assert record["execution_id"] == receipt.execution_id and record["kind"] == "process_only"
    assert record["root_pid"] == int(receipt.output.strip())
    # start_new_session=True：子进程自成进程组与会话
    assert record["process_group"] == record["root_pid"] == record["session_id"]
    assert isinstance(record["process_started_at"], str) and len(record["process_started_at"].split()) == 5
    assert record["command"][0] == sys.executable and record["cwd"] == str(workspace.resolve())
    assert Path(record["scratch"]).name == receipt.execution_id
    assert ledger.finished == [(receipt.execution_id, "exited")]


def test_a_ledger_that_cannot_record_stops_the_run_and_leaves_no_child(workspace, tmp_path):
    executor = ProcessOnlyExecutor(exec_root=tmp_path / "exec")
    executor.ledger = _Ledger(fail=True)
    marker = workspace / "pid"
    with pytest.raises(SandboxUnavailable, match="ledger"):
        asyncio.run(executor.execute(
            [sys.executable, "-c", f"import os,time; open({str(marker)!r},'w').write(str(os.getpid())); time.sleep(30)"],
            cwd=str(workspace), spec=SandboxSpec(cpu_seconds=10, wall_seconds=20)))
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if marker.exists():  # 子进程来得及写下自己的进程号：它必须已经被收掉
        assert _wait_dead(int(marker.read_text()))


def _orphan(tmp_path, name: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(tmp_path),
                            start_new_session=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _row(execution_id: str, pid: int, identity: dict) -> dict:
    return {"execution_id": execution_id, "root_pid": pid, **identity}


def test_reclaim_kills_only_the_process_whose_recorded_identity_still_matches(tmp_path):
    matched = _orphan(tmp_path, "matched")
    stranger = _orphan(tmp_path, "stranger")
    try:
        real = process_identity(matched.pid)
        assert real is not None and identity_matches(real, real)
        # 一条身份对得上的、一条启动时间记成别的（进程号被复用的样子）、一条进程已经不在了
        gone = subprocess.run([sys.executable, "-c", "pass"], capture_output=True)
        dead_pid = None
        probe = subprocess.Popen([sys.executable, "-c", "pass"])
        probe.wait()
        dead_pid = probe.pid
        rows = [
            _row("e-matched", matched.pid, real),
            _row("e-stranger", stranger.pid, {**process_identity(stranger.pid), "process_started_at": "Thu Jan  1 00:00:00 1970"}),
            _row("e-gone", dead_pid, {"process_group": dead_pid, "session_id": dead_pid, "process_started_at": "x"}),
        ]
        reports = {r["execution_id"]: r for r in reclaim_recorded_executions(rows)}
        assert reports["e-matched"]["outcome"] == "reclaimed" and matched.pid in reports["e-matched"]["pids"]
        assert _wait_dead(matched.pid)
        assert reports["e-stranger"]["outcome"] == "identity_mismatch" and reports["e-stranger"]["pids"] == []
        assert _alive(stranger.pid)  # 一个不碰
        assert reports["e-gone"]["outcome"] == "gone"
        assert gone.returncode == 0
    finally:
        for proc in (matched, stranger):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def test_store_ledger_writes_identity_and_reads_owner_from_the_exec_copy_name(tmp_path):
    store = Store.open(tmp_path / "o.db")
    try:
        ledger = StoreSandboxLedger(store)
        cwd = tmp_path / f"attempt-9{EXEC_COPY_MARK}deadbeef"
        ledger.record_start({"execution_id": "e1", "kind": "process_only", "root_pid": 123, "process_group": 123,
                             "session_id": 123, "process_started_at": "Mon Oct  6 10:00:00 2026",
                             "command": ["python", "-c", "pass"], "cwd": str(cwd), "scratch": str(tmp_path / "s")})
        [row] = RecoveryStore(store).open_sandbox_executions()
        assert row["execution_id"] == "e1" and row["command"] == ["python", "-c", "pass"]
        assert row["process_started_at"] == "Mon Oct  6 10:00:00 2026" and row["cwd"] == str(cwd)
        # 库里没有这个尝试：归属为空，但回收材料一样不缺
        assert row["attempt_id"] is None and row["mission_id"] is None
        ledger.record_finish("e1", "exited")
        assert RecoveryStore(store).open_sandbox_executions() == []
        outcome = store.connection.execute("SELECT outcome FROM sandbox_executions WHERE execution_id='e1'").fetchone()[0]
        assert outcome == "exited"
    finally:
        store.close()
