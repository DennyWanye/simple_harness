# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""Linux 隔离区（bubblewrap）的行为核对，对应 AIPhone 交接 SDK-02 的 L-02～L-08。

判据都是行为：隔离区里的代码真的读不到 HOME 和别处的文件、写不出去、连不上宿主的端口与
Unix 套接字（含抽象套接字）、碰不到宿主进程、开不了新的用户命名空间、没有能力位；脱离进程树的
后台进程（setsid、两次 fork、关掉描述符、把自己设成不可转储）在运行结束后真的不在了——每个
隔离区进程的命令行都带一个唯一标记，宿主 ``/proc`` 里找不到这个标记才算收干净；宿主被
SIGKILL 后隔离区随之结束，冷恢复按"开机号 + 第一个进程启动时刻 + 进程命名空间"认领，
对不上的进程一个不碰。

只在装了 bubblewrap、允许非特权用户命名空间的 Linux 上跑；别处整文件跳过。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest
from agent_orchestrator.artifacts.workspace import WorkspaceManager
from agent_orchestrator.runtime import sandbox
from agent_orchestrator.runtime.tool_gateway import run_pytest

if not sandbox.LINUX:
    pytest.skip("bubblewrap is the Linux sandbox", allow_module_level=True)
try:
    sandbox.BwrapExecutor.for_interpreter()
except sandbox.SandboxUnavailable as _error:
    pytest.skip(f"bubblewrap unavailable here: {_error}", allow_module_level=True)

SRC = Path(sandbox.__file__).resolve().parents[2]


def _run(coro, limit=120.0):
    return asyncio.run(asyncio.wait_for(coro, timeout=limit))


def _executor(**options) -> sandbox.BwrapExecutor:
    return sandbox.BwrapExecutor.for_interpreter(**options)


def _spec(**overrides) -> sandbox.SandboxSpec:
    values = dict(cpu_seconds=20, wall_seconds=20, max_processes=32, max_output_bytes=64_000)
    values.update(overrides)
    return sandbox.SandboxSpec(**values)


def _token_pids(token: str) -> list[int]:
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            command = Path(f"/proc/{name}/cmdline").read_bytes()
            state = Path(f"/proc/{name}/stat").read_bytes().rpartition(b")")[2].split()[0]
        except OSError:
            continue
        if token.encode() in command and state not in (b"Z", b"X"):
            found.append(int(name))
    return found


def _wait_gone(token: str, seconds: float = 3.0) -> list[int]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _token_pids(token):
            return []
        time.sleep(0.05)
    return _token_pids(token)


def _script(workspace: Path, name: str, body: str) -> Path:
    path = workspace / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return root


# ------------------------------------------------------------------ L-08 启动检查
def test_probe_passes_every_item_and_admits_the_executor():
    executor = _executor()
    assert executor.kind == "bwrap" and executor.isolated
    report = _run(sandbox.probe_sandbox(executor))
    assert report.ok, report.to_json()
    assert {item["name"] for item in report.items} == {
        "read_home",
        "read_other_temp",
        "write_outside",
        "network",
        "signal_host",
        "daemon_reaped",
        "output_truncated",
        "cpu_killed",
    }
    network = next(item for item in report.items if item["name"] == "network")
    assert {"network_tcp", "network_unix", "network_abstract"} <= set(network["observed"])
    if sandbox._lan_address():  # 有局域网地址时还要证明连不上它（不是"连接被拒"）
        assert network["observed"]["network_lan"] not in ("ConnectionRefusedError", "ALLOWED")
    assert executor.usable and sandbox.probe_passed(executor)


def test_a_whitelist_that_exposes_home_fails_the_probe():
    executor = _executor(extra_read_paths=(str(Path.home()),))
    report = _run(sandbox.probe_sandbox(executor))
    assert not report.ok and not sandbox.probe_passed(executor)
    assert "read_home" in {item["name"] for item in report.items if not item["ok"]}


def test_a_missing_bwrap_is_named():
    with pytest.raises(sandbox.SandboxUnavailable, match="not installed"):
        _executor(bwrap="/nonexistent/bwrap")


def test_an_interpreter_reached_through_symlinks_still_starts(tmp_path, workspace):
    """uv 的虚拟环境：解释器路径途中有落在白名单外的目录链接，隔离区里照原样建出来。"""

    real = Path(os.path.realpath(sys.executable))
    (tmp_path / "hop-b").symlink_to(real.parent)
    (tmp_path / "hop-a").symlink_to(tmp_path / "hop-b")
    interpreter = str(tmp_path / "hop-a" / real.name)
    executor = _executor(interpreter=interpreter)
    receipt = _run(
        executor.execute([interpreter, "-c", "print('started')"], cwd=str(workspace), spec=_spec())
    )
    assert receipt.exit_code == 0 and "started" in receipt.output, receipt.output


# ------------------------------------------------------------------ L-02 / L-03 越界
def test_model_written_code_cannot_reach_the_host(tmp_path, workspace):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("SECRET-BWRAP", encoding="utf-8")
    escape = outside / "escaped.txt"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    unix_path = str(outside / "u.sock")
    unix = socket.socket(socket.AF_UNIX)
    unix.bind(unix_path)
    unix.listen(1)
    abstract_name = f"bwrap-test-{uuid.uuid4().hex}"
    abstract = socket.socket(socket.AF_UNIX)
    abstract.bind("\0" + abstract_name)
    abstract.listen(1)
    _script(
        workspace,
        "test_escape.py",
        f"""
        import json, os, socket
        out = {{}}
        def attempt(name, fn):
            try:
                fn(); out[name] = "ALLOWED"
            except Exception as error:
                out[name] = type(error).__name__
        attempt("read_secret", lambda: open({str(secret)!r}).read())
        attempt("read_shadow", lambda: open("/etc/shadow").read())
        attempt("read_hostname", lambda: open("/etc/hostname").read())
        attempt("write_outside", lambda: open({str(escape)!r}, "w").write("x"))
        attempt("write_usr", lambda: open("/usr/escape.txt", "w").write("x"))
        attempt("tcp", lambda: socket.create_connection(("127.0.0.1", {listener.getsockname()[1]}), timeout=2))
        def unix_connect():
            s = socket.socket(socket.AF_UNIX); s.connect({unix_path!r})
        attempt("unix", unix_connect)
        def abstract_connect():
            s = socket.socket(socket.AF_UNIX); s.connect("\\0" + {abstract_name!r})
        attempt("abstract", abstract_connect)
        attempt("signal_host", lambda: os.kill({os.getpid()}, 0))
        for name, path in (("write_root", "/x"), ("write_tmp", "/tmp/x"), ("shm", "/dev/shm/x")):
            attempt(name, lambda path=path: open(path, "w").write("x"))
        attempt("new_user_namespace", lambda: os.unshare(os.CLONE_NEWUSER))
        status = open("/proc/self/status").read()
        out["cap_eff"] = [l.split()[1] for l in status.splitlines() if l.startswith("CapEff")][0]
        out["visible_processes"] = len([n for n in os.listdir("/proc") if n.isdigit()])
        out["home"] = sorted(os.listdir({str(Path.home())!r})) if os.path.isdir({str(Path.home())!r}) else None
        open("probe.json", "w").write(json.dumps(out))
        def test_still_runs():
            assert True
        """,
    )
    executor = _executor()
    try:
        run = _run(run_pytest(str(workspace), path=None, timeout=60, executor=executor))
    finally:
        listener.close()
        unix.close()
        abstract.close()
    assert run.passed, run.stdout
    observed = json.loads((workspace / "probe.json").read_text())
    for name in (
        "read_secret",
        "read_shadow",
        "read_hostname",
        "write_outside",
        "write_usr",
        "tcp",
        "unix",
        "abstract",
        "signal_host",
        "new_user_namespace",
        "write_root",
        "write_tmp",
    ):
        assert observed[name] != "ALLOWED", (name, observed)
    # /dev/shm 可写，但落在草稿目录的磁盘上（多进程要用），不占内存盘
    assert observed["shm"] == "ALLOWED"
    assert int(observed["cap_eff"], 16) == 0
    assert observed["visible_processes"] < 10  # 只看得到隔离区自己的进程
    home_names = observed["home"] or []
    assert all(not (Path.home() / name).is_file() for name in home_names)
    assert "SECRET-BWRAP" not in run.stdout and not escape.exists()
    assert run.receipt is not None and run.receipt.kind == "bwrap" and run.receipt.isolated


def test_user_name_time_zone_scratch_and_multiprocessing_still_work(workspace):
    script = _script(
        workspace,
        "env_ok.py",
        """
        import datetime, getpass, os, tempfile, time
        print(getpass.getuser())
        print(time.localtime().tm_year > 2000, datetime.datetime.now().astimezone().tzinfo is not None)
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(b"x")
        print("tmp-ok", os.environ["TMPDIR"])
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        if __name__ == "__main__":
            with multiprocessing.Lock():
                pass
            with ProcessPoolExecutor(2) as pool:
                print("pool", sum(pool.map(abs, [-1, -2, -3])))
        """,
    )
    executor = _executor()
    receipt = _run(
        executor.execute([executor.interpreter, str(script)], cwd=str(workspace), spec=_spec())
    )
    assert receipt.exit_code == 0, receipt.output
    assert "True True" in receipt.output and "tmp-ok" in receipt.output
    assert "pool 6" in receipt.output  # 多进程要 /dev/shm（macOS 上也能用）


# ------------------------------------------------------------------ L-05 收敛
def _sleeper(token: str) -> str:
    return f'os.execv(sys.executable, [sys.executable, "-c", "import time; time.sleep(600)", {token!r}])'


BODIES = {
    "same-group": """
        import os, subprocess, sys, time
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", TOKEN])
        open("ready", "w").write("1")
        time.sleep(600)
    """,
    "setsid": """
        import os, subprocess, sys, time
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", TOKEN], start_new_session=True)
        open("ready", "w").write("1")
        time.sleep(600)
    """,
    "double-fork": """
        import os, sys, time
        if os.fork() == 0:
            os.setsid()
            if os.fork() == 0:
                open("ready", "w").write("1")
                SLEEP
            os._exit(0)
        time.sleep(0.5)
        sys.exit(0)
    """,
    "full-daemonize": """
        import os, sys, time
        if os.fork() == 0:
            os.setsid()
            if os.fork() == 0:
                open("ready", "w").write("1")
                os.chdir("/")
                os.closerange(0, 256)
                SLEEP
            os._exit(0)
        time.sleep(0.5)
        sys.exit(0)
    """,
    "not-dumpable": """
        import ctypes, os, sys, time
        if os.fork() == 0:
            os.setsid()
            if os.fork() == 0:
                ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE 0：/proc 属主变 root
                open("ready", "w").write("1")
                while True:
                    time.sleep(600)
            os._exit(0)
        time.sleep(0.5)
        sys.exit(0)
    """,
}


@pytest.mark.parametrize("case", sorted(BODIES))
def test_no_process_of_the_run_survives(case, workspace):
    token = f"tok-{uuid.uuid4().hex}"
    body = textwrap.dedent(BODIES[case]).replace("SLEEP", _sleeper(token).strip())
    script = _script(workspace, "spawn.py", f"TOKEN = {token!r}\n{body}")
    executor = _executor()
    wall = 3 if case in ("same-group", "setsid") else 30
    receipt = _run(
        executor.execute(
            [executor.interpreter, str(script), token],
            cwd=str(workspace),
            spec=_spec(wall_seconds=wall),
        )
    )
    assert (workspace / "ready").exists(), receipt.output
    assert receipt.tree_killed is True and receipt.residual_pids == () and receipt.status == "ok"
    assert _wait_gone(token) == [], f"{case}: a process of the run survived"


def test_cancelling_a_run_leaves_nothing_behind(workspace):
    token = f"tok-{uuid.uuid4().hex}"
    script = _script(
        workspace, "spawn.py", f"TOKEN = {token!r}\n" + textwrap.dedent(BODIES["setsid"])
    )
    executor = _executor()

    async def case():
        task = asyncio.create_task(
            executor.execute(
                [executor.interpreter, str(script), token],
                cwd=str(workspace),
                spec=_spec(wall_seconds=60),
            )
        )
        for _ in range(200):
            if (workspace / "ready").exists():
                break
            await asyncio.sleep(0.05)
        assert _token_pids(token)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(case())
    assert _wait_gone(token) == []


def test_a_finished_runs_namespace_number_is_not_reused_while_it_is_reaped(tmp_path):
    """前一次运行收尾慢的时候，新开的隔离区拿不到它的命名空间编号，收尾也就不会杀到新的。"""

    first_ws, second_ws = tmp_path / "a", tmp_path / "b"
    first_ws.mkdir()
    second_ws.mkdir()
    a, b = _executor(), _executor()
    seen: dict[str, str | None] = {}
    original_reap, original_started = a._reap, b._started

    def slow_reap(run, *, root_alive=True):
        seen["a"] = run.pid_namespace
        held = set()
        for descriptor in run.descriptors:
            try:
                held.add(os.readlink(f"/proc/self/fd/{descriptor}"))
            except OSError:
                continue
        seen["pinned"] = str(run.pid_namespace in held)  # 编号复用看时机，钉住与否是确定的
        time.sleep(1.5)
        return original_reap(run, root_alive=root_alive)

    async def started(run):
        await original_started(run)
        seen["b"] = run.pid_namespace

    a._reap, b._started = slow_reap, started

    async def case():
        first = asyncio.create_task(
            a.execute([a.interpreter, "-c", "pass"], cwd=str(first_ws), spec=_spec())
        )
        while "a" not in seen:
            await asyncio.sleep(0.01)
        second = await b.execute(
            [b.interpreter, "-c", "import time; time.sleep(1); print('done')"],
            cwd=str(second_ws),
            spec=_spec(),
        )
        await first
        return second

    receipt = _run(case())
    assert receipt.exit_code == 0 and "done" in receipt.output, receipt.output
    assert seen["pinned"] == "True"
    assert seen["a"] and seen["b"] and seen["a"] != seen["b"]


# ------------------------------------------------------------------ L-04 限制
def test_cpu_limit_stops_a_busy_loop(workspace):
    executor = _executor()
    receipt = _run(
        executor.execute(
            [executor.interpreter, "-c", "while True:\n    pass"],
            cwd=str(workspace),
            spec=_spec(cpu_seconds=1),
        )
    )
    assert receipt.limit_exceeded == "cpu" and not receipt.timed_out


def test_output_is_bounded_and_wall_timeout_is_reported(workspace):
    executor = _executor()
    receipt = _run(
        executor.execute(
            [executor.interpreter, "-c", "import sys; sys.stdout.write('x' * 200000)"],
            cwd=str(workspace),
            spec=_spec(max_output_bytes=4096),
        )
    )
    assert receipt.truncated and len(receipt.output.encode()) <= 4096
    receipt = _run(
        executor.execute(
            [executor.interpreter, "-c", "import time; time.sleep(30)"],
            cwd=str(workspace),
            spec=_spec(wall_seconds=1),
        )
    )
    assert receipt.timed_out and receipt.exit_code is None and receipt.tree_killed


def test_soft_limits_reap_the_run(workspace):
    executor = _executor()
    many = _script(
        workspace,
        "many.py",
        """
        import subprocess, sys, time
        children = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(12)]
        time.sleep(30)
        """,
    )
    receipt = _run(
        executor.execute(
            [executor.interpreter, str(many)],
            cwd=str(workspace),
            spec=_spec(wall_seconds=30, max_processes=5),
        )
    )
    assert receipt.limit_exceeded == "processes" and receipt.tree_killed
    big = _script(
        workspace,
        "big.py",
        """
        import time
        block = bytearray(400 * 1024 * 1024)
        for i in range(0, len(block), 4096):
            block[i] = 1
        time.sleep(30)
        """,
    )
    receipt = _run(
        executor.execute(
            [executor.interpreter, str(big)],
            cwd=str(workspace),
            spec=_spec(wall_seconds=30, max_rss_bytes=100 * 1024 * 1024),
        )
    )
    assert receipt.limit_exceeded == "rss" and receipt.tree_killed
    assert receipt.effective_limits["max_rss_bytes"]["enforcement"] == "soft"


# ------------------------------------------------------------------ L-06 宿主被杀与冷恢复
CHILD_HOST = r"""
import asyncio, sys
from pathlib import Path
from agent_orchestrator.artifacts.workspace import WorkspaceManager
from agent_orchestrator.runtime import sandbox

JOB = '''
import os, sys, time
token = sys.argv[1]
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        open("ready", "w").write("1")
        os.execv(sys.executable, [sys.executable, "-c", "import time; time.sleep(600)", token])
    os._exit(0)
time.sleep(600)
'''

async def main(root, token):
    manager = WorkspaceManager(root / "workspaces")
    manager.create("attempt", seed={"job.py": JOB})
    copy = manager.exec_copy("attempt")
    executor = sandbox.BwrapExecutor.for_interpreter(exec_root=root / "scratch")
    task = asyncio.create_task(executor.execute(
        [sys.executable, str(copy.root / "job.py"), token],
        cwd=str(copy.root), spec=sandbox.SandboxSpec(wall_seconds=60, cpu_seconds=30)))
    while not (copy.root / "ready").exists():
        assert not task.done(), task.result()
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)
    print("READY", flush=True)
    await task

asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
"""


def _killed_host(tmp_path) -> tuple[Path, str]:
    """起一个子宿主跑一次隔离区，等后台进程起来后 SIGKILL 子宿主；返回数据目录与标记。"""

    root, token = tmp_path / "host", f"tok-{uuid.uuid4().hex}"
    root.mkdir()
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD_HOST, str(root), token],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        line = child.stdout.readline() if child.stdout else b""
        assert line.strip() == b"READY", child.stderr.read() if child.stderr else b""
        assert _token_pids(token), "the sandboxed daemon should be running"
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(10)
    finally:
        if child.poll() is None:
            child.kill()
    return root, token


def test_killing_the_host_ends_the_sandbox_and_cold_recovery_reclaims_the_run(tmp_path):
    root, token = _killed_host(tmp_path)
    assert _wait_gone(token) == [], "--die-with-parent should end the sandbox with its host"
    manager = WorkspaceManager(root / "workspaces")
    runs = list((manager.root / sandbox.SANDBOX_RUNS).glob("*.run.json"))
    assert len(runs) == 1
    identity = json.loads(runs[0].read_text())
    assert (Path(identity["marks"]) / "namespace.json").exists()
    manager.sweep_exec_copies(older_than=0)
    (report,) = manager.last_sandbox_cleanup
    assert report["status"] == "reaped" and report["mode"] == "cold"
    assert not list((manager.root / sandbox.SANDBOX_RUNS).glob("*.run.json"))
    assert not Path(identity["marks"]).exists() and not Path(identity["scratch"]).exists()


def test_cold_recovery_never_signals_a_process_that_reused_the_pid(tmp_path):
    root, token = _killed_host(tmp_path)
    assert _wait_gone(token) == []
    manager = WorkspaceManager(root / "workspaces")
    (index,) = (manager.root / sandbox.SANDBOX_RUNS).glob("*.run.json")
    marks = Path(json.loads(index.read_text())["marks"])
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        record = json.loads((marks / "namespace.json").read_text())
        record.pop("record_sha256")
        record["init_pid"] = stranger.pid  # 进程号"被复用"，启动时刻对不上
        sandbox._durable_json(
            marks / "namespace.json", {**record, "record_sha256": sandbox._digest(record)}
        )
        manager.sweep_exec_copies(older_than=0)
        (report,) = manager.last_sandbox_cleanup
        assert report["status"] == "reaped"
        assert stranger.poll() is None, "an unrelated process was signalled"
    finally:
        stranger.kill()
        stranger.wait()


def test_a_tampered_namespace_record_keeps_the_copy(tmp_path):
    root, token = _killed_host(tmp_path)
    assert _wait_gone(token) == []
    manager = WorkspaceManager(root / "workspaces")
    (index,) = (manager.root / sandbox.SANDBOX_RUNS).glob("*.run.json")
    identity = json.loads(index.read_text())
    path = Path(identity["marks"]) / "namespace.json"
    record = json.loads(path.read_text())
    record["init_pid"] = 1  # 改了内容、没改摘要
    path.write_text(json.dumps(record))
    reports = sandbox.recover_workspace_executions(manager.root)
    assert [r["status"] for r in reports] == ["unknown"]
    assert Path(identity["marks"]).exists() and index.exists()


# ------------------------------------------------------------------ 账本里的进程身份（Linux）
def test_process_identity_reads_proc_and_guards_pid_reuse():
    me = sandbox.process_identity(os.getpid())
    assert me is not None and me["process_started_at"].count(":") == 1
    assert sandbox.identity_matches(me, sandbox.process_identity(os.getpid()))
    stranger = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        current = sandbox.process_identity(stranger.pid)
        row = {
            "execution_id": "e",
            "root_pid": stranger.pid,
            **current,
            "process_started_at": "other-boot:1",
        }
        (report,) = sandbox.reclaim_recorded_executions([row])
        assert report["outcome"] == "identity_mismatch" and stranger.poll() is None
        row = {"execution_id": "e", "root_pid": stranger.pid, **current}
        (report,) = sandbox.reclaim_recorded_executions([row])
        assert report["outcome"] == "reclaimed"
        assert stranger.wait(5) == -signal.SIGKILL
    finally:
        if stranger.poll() is None:
            stranger.kill()
