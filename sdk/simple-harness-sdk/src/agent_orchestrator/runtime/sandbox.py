# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The sandbox executor port and its adapters (P3.2 plan v3 D1 / D2; plan review
round 1 P0-2 / P1-2 / P1-3 / P1-4, round 2 P1-2 / P2-1 / P2-9).

Every piece of model-written code this build runs — ``run_tests``, the ``code_test`` layer,
Mission ``pytest:`` criteria, the evaluator's oracle — goes through
:meth:`SandboxExecutorPort.execute`, which returns an :class:`ExecutionReceipt` saying what
really happened: exit code, bounded output (the tail is kept, where pytest puts its
summary), whether the wall clock or a limit stopped it, and whether every process of the
run is gone — measured after the reaping, never assumed.

``SeatbeltExecutor`` (macOS ``sandbox-exec``) is the sandbox: no network, no file content
outside a read whitelist computed from the interpreter it runs, no writes outside the run's
own copy and scratch directory, no signals to processes outside the sandbox, no mach
lookups, no information about other processes.  Its processes are found by *sandbox
identity*: each run's profile allows reading one canary path and denies a decoy, and
``sandbox_check()`` asked about every process of this user picks out exactly the processes
of this run — however they left the process tree (``setsid``, double fork, ``chdir("/")``,
closed descriptors).  ``ProcessOnlyExecutor`` is not a sandbox and says so
(``isolated=False``): it follows the parent chain and the run's directory, and a full
daemonize can leave it — for trusted code only.

``BwrapExecutor``（Linux，bubblewrap）给同样的保证：每次运行一套新的用户/进程/网络/IPC
命名空间，根目录是空的、只读的，只读挂解释器算出来的白名单，可写的只有本次的副本和草稿目录。
进程归属按 *进程命名空间*：命名空间里的进程逃不出去（``setsid``、两次 fork 都还在里面），
杀掉命名空间的第一个进程，内核就把里面的进程全部结束；宿主死了，``--die-with-parent``
让整个隔离区跟着结束。

Honest limits (plan §2): CPU time and file size are hard limits (``setrlimit``, inherited
by every descendant); memory and the process count are *soft* limits — sampled while the
run lasts and enforced by reaping, because this platform has no cgroups, ``RLIMIT_AS`` /
``RLIMIT_DATA`` cannot be set and ``RLIMIT_NPROC`` counts every process of the user.
Linux 上同样按软限制做：手机上的 cgroup 一般不能委派给普通用户。
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import json
import logging
import os
import pwd
import resource
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


SANDBOX_VERSION = "sandbox-v1"
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
PS = "/bin/ps"
LSOF = "/usr/sbin/lsof"
DEFAULT_PATH = "/usr/bin:/bin"
# read-only system locations every interpreter needs (plan v3 D1; "/" itself is listed by
# the interpreter at start-up — without it every program aborts, review round 2 P2-1)
SYSTEM_READ_PATHS = ("/System", "/usr", "/bin", "/private/etc", "/private/var/db/timezone")
DEVICE_READ_LITERALS = ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")
PROBE_SECRET = "P32-PROBE-SECRET"

LINUX = sys.platform.startswith("linux")
PROC = Path("/proc")
# Linux 隔离区里只读挂的系统位置：动态链接器与库、时区、用户名解析；存在的才挂，是链接的照原样
# 建链接。别的 /etc 内容（凭据、主机配置）不挂。
LINUX_SYSTEM_READ_PATHS = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/etc/ld.so.cache",
    "/etc/ld.so.conf",
    "/etc/ld.so.conf.d",
    "/etc/localtime",
    "/etc/alternatives",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/group",
)


class SandboxUnavailable(RuntimeError):
    """The deployment asked for a sandbox this machine cannot provide (plan D2)."""


# ------------------------------------------------------------------ contracts
@dataclass(frozen=True, slots=True)
class SandboxSpec:
    cpu_seconds: int = 120
    wall_seconds: float = 120.0
    max_processes: int = 64
    max_rss_bytes: int = 2 * 1024 * 1024 * 1024
    max_output_bytes: int = 1024 * 1024
    max_file_bytes: int = 64 * 1024 * 1024
    env: Mapping[str, str] = field(default_factory=dict)
    network: str = "none"

    def __post_init__(self) -> None:
        if self.network != "none":
            raise ValueError("this build only runs model-written code without a network")
        if self.cpu_seconds < 1 or self.wall_seconds <= 0 or self.max_output_bytes < 1:
            raise ValueError("sandbox limits must be positive")

    def effective_limits(self, *, isolated: bool = True) -> dict[str, dict[str, Any]]:
        """What this run is really held to.  ``cpu_seconds`` is a *per-process* rlimit, not
        a total for the run; the network is only denied when an adapter isolates the run —
        an unsandboxed one says so rather than claiming a limit it does not apply (code
        review round 1 P1-3 / P2-5)."""

        return {
            "cpu_seconds": {"value": self.cpu_seconds, "enforcement": "hard", "scope": "process"},
            "wall_seconds": {"value": self.wall_seconds, "enforcement": "hard"},
            "max_file_bytes": {"value": self.max_file_bytes, "enforcement": "hard"},
            "max_output_bytes": {"value": self.max_output_bytes, "enforcement": "hard"},
            "max_rss_bytes": {"value": self.max_rss_bytes, "enforcement": "soft"},
            "max_processes": {"value": self.max_processes, "enforcement": "soft"},
            "network": (
                {"value": self.network, "enforcement": "hard"}
                if isolated
                else {"value": "unrestricted", "enforcement": "none"}
            ),
        }


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    execution_id: str
    kind: str  # seatbelt | bwrap | process_only | 应用自带执行器的种类
    isolated: bool
    environment_digest: str
    effective_limits: Mapping[str, Any]
    exit_code: int | None
    output: str
    truncated: bool
    timed_out: bool
    limit_exceeded: str | None  # cpu | rss | processes
    tree_killed: bool
    residual_pids: tuple[int, ...]
    status: str  # ok | error (a process of the run could not be removed)

    def to_json(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "kind": self.kind,
            "isolated": self.isolated,
            "environment_digest": self.environment_digest,
            "effective_limits": dict(self.effective_limits),
            "exit_code": self.exit_code,
            "output": self.output,
            "truncated": self.truncated,
            "timed_out": self.timed_out,
            "limit_exceeded": self.limit_exceeded,
            "tree_killed": self.tree_killed,
            "residual_pids": list(self.residual_pids),
            "status": self.status,
        }


class SandboxExecutorPort(Protocol):
    kind: str
    isolated: bool
    interpreter: str

    @property
    def environment_digest(self) -> str: ...

    async def execute(
        self, command: Sequence[str], *, cwd: str, spec: SandboxSpec
    ) -> ExecutionReceipt: ...


# ------------------------------------------------------------------ process table
@dataclass(frozen=True, slots=True)
class _Proc:
    pid: int
    ppid: int
    uid: int
    rss_kb: int
    zombie: bool


def _process_table(*, strict: bool = False, timeout: float = 10) -> list[_Proc]:
    if LINUX:
        return _linux_process_table(strict=strict)
    try:
        completed = subprocess.run(
            [PS, "-A", "-o", "pid=,ppid=,uid=,rss=,stat="],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if strict and completed.returncode != 0:
            raise SandboxUnavailable("process inventory failed")
        out = completed.stdout
    except (OSError, subprocess.SubprocessError) as error:
        if strict:
            raise SandboxUnavailable("process inventory unavailable") from error
        return []
    table = []
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        if len(parts) < 5:
            if strict:
                raise SandboxUnavailable("process inventory contains an incomplete row")
            continue
        try:
            pid, ppid, uid, rss = (int(value) for value in parts[:4])
            table.append(_Proc(pid, ppid, uid, rss, parts[4][:1] == "Z"))
        except ValueError as error:
            if strict:
                raise SandboxUnavailable("process inventory contains an invalid row") from error
            continue
    if strict and not any(proc.pid == os.getpid() for proc in table):
        raise SandboxUnavailable("process inventory incomplete")
    return table


def _boot_id() -> str:
    try:
        return (PROC / "sys" / "kernel" / "random" / "boot_id").read_text().strip()
    except OSError:
        return ""


def _proc_stat(pid: int) -> list[str] | None:
    """``/proc/<pid>/stat`` 里命令名之后的字段（第 0 个是状态，第 1 个父进程号，第 2 个进程组，
    第 3 个会话，第 19 个启动时刻，第 21 个常驻页数）；进程不在了返回 ``None``。命令名可能带空格
    和括号，所以按最后一个右括号切。"""

    try:
        raw = (PROC / str(pid) / "stat").read_bytes().decode("utf-8", "replace")
    except (FileNotFoundError, ProcessLookupError):
        return None
    return raw.rpartition(")")[2].split()


def _linux_process_table(*, strict: bool) -> list[_Proc]:
    """Linux 不靠 ``ps``（手机上可能是 busybox，不认 ``-o``）：直接读 ``/proc``。"""

    page_kb = max(1, os.sysconf("SC_PAGE_SIZE") // 1024)
    try:
        names = os.listdir(PROC)
    except OSError as error:
        if strict:
            raise SandboxUnavailable("process inventory unavailable") from error
        return []
    table = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            fields = _proc_stat(int(name))
            uid = os.stat(PROC / name).st_uid
        except OSError:
            continue  # 刚结束，或被 hidepid 藏起来的别人的进程
        if fields is None:
            continue
        try:
            table.append(
                _Proc(int(name), int(fields[1]), uid, int(fields[21]) * page_kb, fields[0] in "ZX")
            )
        except (IndexError, ValueError) as error:
            if strict:
                raise SandboxUnavailable("process inventory contains an invalid row") from error
    if strict and not any(proc.pid == os.getpid() for proc in table):
        raise SandboxUnavailable("process inventory incomplete")
    return table


def _pid_namespace(pid: int) -> str | None:
    try:
        return os.readlink(PROC / str(pid) / "ns" / "pid")
    except OSError:
        return None


def _namespace_members(namespace: str) -> set[int]:
    """此刻在进程命名空间 ``namespace``（``pid:[编号]``）里、还活着的进程（宿主视角的进程号）。
    不按用户过滤：隔离区里的进程可以把自己设成不可转储，``/proc`` 属主就变成 root；读命名空间
    链接仍然允许，因为宿主是那个用户命名空间的主人。"""

    try:
        names = os.listdir(PROC)
    except OSError as error:
        raise SandboxUnavailable("process inventory unavailable") from error
    members = set()
    for name in names:
        if not name.isdigit() or _pid_namespace(int(name)) != namespace:
            continue
        try:
            fields = _proc_stat(int(name))
        except OSError:
            fields = ["?"]  # 在命名空间里却读不到状态：按活着算，宁可多收
        if fields is not None and fields[0] not in "ZX":
            members.add(int(name))
    return members


def _descendants(table: Sequence[_Proc], root: int) -> set[int]:
    found = {root}
    changed = True
    while changed:
        changed = False
        for proc in table:
            if proc.ppid in found and proc.pid not in found:
                found.add(proc.pid)
                changed = True
    return found


def _sigkill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


#: ``prctl(PR_SET_NO_NEW_PRIVS, 1)``：此后 exec 不再因 setuid 位或文件能力提权，子孙都继承。
PR_SET_NO_NEW_PRIVS = 38


def _set_no_new_privs(prctl: Any) -> None:
    if prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS) failed")


def _preexec(
    cpu_seconds: int, max_file_bytes: int, *, no_new_privs: bool = False
) -> Callable[[], None]:
    # 在父进程里先载入 C 库并取好 prctl：子进程在 fork 与 exec 之间只做系统调用
    prctl = ctypes.CDLL(None, use_errno=True).prctl if no_new_privs else None

    def apply() -> None:  # runs in the child before exec: inherited by every descendant
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))
        if prctl is not None:
            _set_no_new_privs(prctl)

    return apply


class _Tail:
    """Keeps the last ``cap`` bytes of a stream (pytest's summary is at the end)."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        self.data = bytearray()
        self.total = 0

    async def drain(self, stream: asyncio.StreamReader) -> None:
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                return
            self.total += len(chunk)
            self.data.extend(chunk)
            if len(self.data) > self.cap:
                del self.data[: len(self.data) - self.cap]

    def text(self) -> tuple[str, bool]:
        truncated = self.total > self.cap
        # a character cut at the front is dropped rather than replaced: the bound holds
        return self.data.decode("utf-8", "ignore" if truncated else "replace"), truncated


class SandboxExecutionLedger(Protocol):
    """子进程身份与回收材料的账本（§14.4，车道 J H04）。

    ``record_start`` 在子进程起来、身份读到之后立刻调用；写不进去的运行不许继续（先把刚起的进程
    组收掉再报错）——没有回收材料的子进程不能存在。``record_finish`` 在收尾后写结果。"""

    def record_start(self, record: Mapping[str, Any]) -> None: ...

    def record_finish(self, execution_id: str, outcome: str) -> None: ...


def process_identity(pid: int) -> dict[str, Any] | None:
    """这一刻进程号 ``pid`` 的身份：进程组、会话、启动时间、命令行。进程不在了返回 ``None``；
    查不了抛 :class:`SandboxUnavailable`。Linux 读 ``/proc``，启动时间是"开机号:开机后节拍数"，
    重启后不会和旧记录相等。"""

    if LINUX:
        return _linux_process_identity(pid)
    try:
        completed = subprocess.run(
            [PS, "-p", str(int(pid)), "-o", "lstart=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SandboxUnavailable("process identity query unavailable") from error
    line = completed.stdout.strip()
    if completed.returncode != 0 or not line:
        return None
    parts = line.split()
    if len(parts) < 5:
        raise SandboxUnavailable("process identity output unreadable")
    # macOS 的 ``ps -o sess`` 只打印 0：进程组与会话用系统调用读，进程刚没了就按"不在"算
    try:
        group, session = os.getpgid(int(pid)), os.getsid(int(pid))
    except ProcessLookupError:
        return None
    except PermissionError:
        group, session = None, None
    return {
        "process_group": group,
        "session_id": session,
        "process_started_at": " ".join(parts[:5]),
        "command": " ".join(parts[5:]),
    }


def _linux_process_identity(pid: int) -> dict[str, Any] | None:
    try:
        fields = _proc_stat(int(pid))
        if fields is None or fields[0] in "ZX":
            return None  # 僵尸进程已经结束，只差父进程收尸
        try:
            command = (PROC / str(int(pid)) / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            command = b""
        return {
            "process_group": int(fields[2]),
            "session_id": int(fields[3]),
            "process_started_at": f"{_boot_id()}:{fields[19]}",
            "command": command.replace(b"\0", b" ").decode("utf-8", "replace").strip(),
        }
    except (OSError, IndexError, ValueError) as error:
        raise SandboxUnavailable("process identity query unavailable") from error


def identity_matches(recorded: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """落库的身份与此刻同一个进程号上的进程是不是同一个：启动时间必须一样，进程组/会话有记就要
    一样。命令行只作记录（沙箱里的程序可以改自己的 argv），不参与判定。"""

    if str(recorded.get("process_started_at") or "") != str(
        current.get("process_started_at") or ""
    ):
        return False
    for key in ("process_group", "session_id"):
        if recorded.get(key) is not None and int(recorded[key]) != int(current[key]):
            return False
    return True


def reclaim_recorded_executions(
    rows: Sequence[Mapping[str, Any]],
    *,
    kill: Callable[[int], None] | None = None,
    sweeps: int = 3,
) -> list[dict[str, Any]]:
    """重启后的孤儿回收（恢复协议第 7 步）：对每条没记结束的子进程，先按落库身份核对此刻的进程，
    对上了才终止它的进程组；对不上（进程号被别人复用）一个不碰；进程不在了记 ``gone``；
    ``ps`` 不可用记 ``unverifiable``。返回每条的结果（``execution_id``、``outcome``、``pids``）。"""

    reports: list[dict[str, Any]] = []
    signal_one = kill or _sigkill
    for row in rows:
        execution_id = str(row["execution_id"])
        pid = int(row["root_pid"])
        try:
            current = process_identity(pid)
        except SandboxUnavailable as error:
            reports.append(
                {
                    "execution_id": execution_id,
                    "outcome": "unverifiable",
                    "root_pid": pid,
                    "reason": str(error),
                    "pids": [],
                }
            )
            continue
        if current is None:
            reports.append(
                {"execution_id": execution_id, "outcome": "gone", "root_pid": pid, "pids": []}
            )
            continue
        if not identity_matches(row, current):
            reports.append(
                {
                    "execution_id": execution_id,
                    "outcome": "identity_mismatch",
                    "root_pid": pid,
                    "recorded": {
                        k: row.get(k) for k in ("process_group", "session_id", "process_started_at")
                    },
                    "current": current,
                    "pids": [],
                }
            )
            continue
        group = current["process_group"]
        signalled: set[int] = set()
        for _ in range(max(1, sweeps)):
            if group is not None:
                try:
                    os.killpg(int(group), signal.SIGKILL)
                    signalled.add(int(group))
                except (ProcessLookupError, PermissionError):
                    pass
            signal_one(pid)
            signalled.add(pid)
            time.sleep(0.05)
            again = process_identity(pid)
            if again is None or not identity_matches(row, again):
                break
        after = process_identity(pid)
        alive = after is not None and identity_matches(row, after)
        reports.append(
            {
                "execution_id": execution_id,
                "outcome": "residual" if alive else "reclaimed",
                "root_pid": pid,
                "pids": sorted(signalled),
            }
        )
    return reports


# ------------------------------------------------------------------ the shared engine
class _Executor:
    kind = ""
    isolated = False
    #: 起子进程前设"不再提权"（见 :data:`PR_SET_NO_NEW_PRIVS`）。
    no_new_privs = False

    def __init__(
        self,
        interpreter: str | None = None,
        *,
        poll_interval: float = 0.1,
        kill: Callable[[int], None] | None = None,
        max_sweeps: int = 6,
        exec_root: str | Path | None = None,
    ) -> None:
        self.interpreter = str(interpreter or sys.executable)
        self._poll = poll_interval
        self._kill = kill or _sigkill  # seam: what removes a descendant
        self._max_sweeps = max(1, max_sweeps)
        self._exec_root = (
            Path(exec_root)
            if exec_root is not None
            else Path(tempfile.gettempdir()).resolve() / f"orch-exec-{os.getuid()}"
        )
        #: 子进程身份与回收材料的账本；编排启动时绑上（``StoreSandboxLedger``）。
        self.ledger: SandboxExecutionLedger | None = None

    @property
    def environment_digest(self) -> str:
        return _digest({"kind": self.kind, "version": SANDBOX_VERSION, **self._identity()})

    def _identity(self) -> dict[str, Any]:
        return {"interpreter": os.path.realpath(self.interpreter)}

    # subclass hooks -------------------------------------------------------
    def _argv(self, command: Sequence[str], run: _Run) -> list[str]:
        return list(command)

    def _members(self, run: _Run) -> set[int]:
        raise NotImplementedError

    def _survivors(self, run: _Run) -> set[int]:
        raise NotImplementedError

    def _spawn_options(self, run: _Run) -> dict[str, Any]:
        """额外的启动参数（例如交给子进程的描述符）；在 ``_argv`` 之前调用。"""

        return {}

    async def _started(self, run: _Run) -> None:
        """子进程起来之后、记账之前：读隔离区的归属信息。抛错 = 这次启动不能确认。"""

    def _kill_member(self, run: _Run, pid: int) -> None:
        self._kill(pid)

    def _cpu_limited(self, returncode: int | None) -> bool:
        return returncode == -signal.SIGXCPU

    # the run ----------------------------------------------------------------
    async def execute(
        self, command: Sequence[str], *, cwd: str, spec: SandboxSpec
    ) -> ExecutionReceipt:
        run = _Run.start(self._exec_root, Path(cwd).resolve())
        safe_to_delete = False
        if self.isolated:
            run.register(self.kind, self.environment_digest)
        env = {
            "PATH": DEFAULT_PATH,
            "HOME": str(run.scratch),
            "TMPDIR": str(run.scratch / "tmp"),
            "USER": run.user,
            "LOGNAME": run.user,
            "LANG": os.environ.get("LANG") or "en_US.UTF-8",
            **dict(spec.env),
        }
        tail = _Tail(spec.max_output_bytes)
        timed_out = False
        limit: str | None = None
        residual: set[int] = set()
        recorded = False
        try:
            process = await self._spawn(command, run, env, spec)
            recorded = self._record_start(run, command, process.pid)
            assert process.stdout is not None
            reader = asyncio.ensure_future(tail.drain(process.stdout))
            waiter = asyncio.ensure_future(process.wait())
            loop = asyncio.get_running_loop()
            deadline = loop.time() + spec.wall_seconds
            try:
                while not waiter.done():
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        timed_out = True
                        break
                    await asyncio.wait({waiter}, timeout=min(self._poll, remaining))
                    if waiter.done():
                        break
                    members, rss = await asyncio.to_thread(self._sample, run)
                    if len(members) > spec.max_processes:
                        limit = "processes"
                        break
                    if rss > spec.max_rss_bytes:
                        limit = "rss"
                        break
            except asyncio.CancelledError:
                residual = await asyncio.shield(asyncio.to_thread(self._reap, run, root_alive=True))
                safe_to_delete = not residual
                raise
            # code review round 1 P1-5: once ``process.wait()`` has returned, the child is
            # reaped and its pid may already belong to someone else — never signal it then
            residual = await asyncio.to_thread(self._reap, run, root_alive=not waiter.done())
            safe_to_delete = not residual
            returncode = await waiter
            try:
                await asyncio.wait_for(reader, timeout=2.0)
            except TimeoutError:  # a survivor still holds the pipe: keep what was read
                reader.cancel()
        finally:
            run.close_descriptors()
            run.close(clean=safe_to_delete)
            if recorded:
                self._record_finish(run.execution_id, "residual" if residual else "exited")
        if limit is None and self._cpu_limited(returncode):
            limit = "cpu"
        output, truncated = tail.text()
        return ExecutionReceipt(
            execution_id=run.execution_id,
            kind=self.kind,
            isolated=self.isolated,
            environment_digest=self.environment_digest,
            effective_limits=spec.effective_limits(isolated=self.isolated),
            exit_code=None if timed_out else returncode,
            output=output,
            truncated=truncated,
            timed_out=timed_out,
            limit_exceeded=limit,
            tree_killed=not residual,
            residual_pids=tuple(sorted(residual)),
            status="ok" if not residual else "error",
        )

    async def _spawn(
        self, command: Sequence[str], run: _Run, env: Mapping[str, str], spec: SandboxSpec
    ) -> asyncio.subprocess.Process:
        """起子进程并确认隔离区归属；确认不了就先收掉刚起的进程再报不可用。"""

        spawn_options = self._spawn_options(run)
        process = await asyncio.create_subprocess_exec(
            *self._argv(command, run),
            cwd=str(run.cwd),
            env=dict(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=_preexec(
                spec.cpu_seconds, spec.max_file_bytes, no_new_privs=self.no_new_privs
            ),
            **spawn_options,
        )
        run.root_pid = process.pid
        try:
            await self._started(run)
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(self._reap, run, root_alive=True))
            raise
        except Exception as error:  # noqa: BLE001 - 归属不明的运行不许继续
            await asyncio.to_thread(self._reap, run, root_alive=True)
            raise SandboxUnavailable("the sandbox start could not be confirmed") from error
        return process

    def _record_start(self, run: _Run, command: Sequence[str], pid: int) -> bool:
        """子进程一起来就把身份与回收材料落账（§14.4）。账本写不进去：先把刚起的进程组收掉，
        再报不可用——不留没有回收材料的子进程。没有账本（单测直接用执行器）就不记。"""

        if self.ledger is None:
            return False
        try:
            identity = process_identity(pid) or {}
            self.ledger.record_start(
                {
                    "execution_id": run.execution_id,
                    "kind": self.kind,
                    "root_pid": pid,
                    "process_group": identity.get("process_group"),
                    "session_id": identity.get("session_id"),
                    "process_started_at": identity.get("process_started_at"),
                    "command": list(command),
                    "cwd": str(run.cwd),
                    "scratch": str(run.scratch),
                }
            )
        except Exception as error:  # noqa: BLE001 - the ledger is what makes the run recoverable
            self._reap(run, root_alive=True)
            raise SandboxUnavailable("sandbox execution ledger unavailable") from error
        return True

    def _record_finish(self, execution_id: str, outcome: str) -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.record_finish(execution_id, outcome)
        except Exception:  # noqa: BLE001 - 运行已结束，未完成的行在重启时回收
            logger.debug("sandbox_record_finish_failed", exc_info=True)

    def _sample(self, run: _Run) -> tuple[set[int], int]:
        members = self._members(run)
        run.seen |= members
        rss = sum(p.rss_kb for p in run.table if p.pid in members) * 1024
        return members, rss

    def _reap(self, run: _Run, *, root_alive: bool = True) -> set[int]:
        """Kill every process of the run, sweep again until none is left or the sweeps
        run out; what is still alive afterwards is returned (``tree_killed`` = empty).

        ``root_alive`` says whether the run's own process is still running.  After a normal
        exit it is not, and its pid may have been handed to an unrelated process — signalling
        the group then could kill a stranger (code review round 1 P1-5)."""

        for _ in range(self._max_sweeps):
            if root_alive and run.root_pid is not None:
                try:
                    os.killpg(run.root_pid, signal.SIGKILL)  # the run's own group
                except (ProcessLookupError, PermissionError):
                    pass
                _sigkill(run.root_pid)
            root_alive = False  # after one sweep the run's own process is gone either way
            survivors = self._survivors(run)
            if not survivors:
                return set()
            for pid in survivors:
                self._kill_member(run, pid)
            time.sleep(0.05)
        return self._survivors(run)


@dataclass
class _Run:
    execution_id: str
    cwd: Path
    scratch: Path
    marks: Path  # canary / decoy: outside every write path of the run
    user: str
    root_pid: int | None = None
    seen: set[int] = field(default_factory=set)
    table: list[_Proc] = field(default_factory=list)
    index: Path | None = None
    owner_fd: int | None = None
    identity: dict[str, Any] | None = None
    #: 交给子进程的描述符（Linux：bwrap 的信息管道），运行结束必关。
    descriptors: list[int] = field(default_factory=list)
    #: Linux：隔离区的进程命名空间（``pid:[编号]``）与它的第一个进程。
    pid_namespace: str | None = None
    init_pid: int | None = None
    #: Linux：bwrap 写隔离区信息的管道（读端、写端）。
    info_pipe: tuple[int, int] | None = None

    @property
    def canary(self) -> Path:
        return self.marks / "canary"

    @property
    def decoy(self) -> Path:
        return self.marks / "decoy"

    @classmethod
    def start(cls, exec_root: Path, cwd: Path) -> _Run:
        exec_root = exec_root.resolve()
        exec_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        execution_id = uuid.uuid4().hex
        scratch = exec_root / execution_id
        marks = exec_root / f"{execution_id}.marks"
        (scratch / "tmp").mkdir(parents=True, mode=0o700)
        marks.mkdir(mode=0o700)
        (marks / "canary").write_text("canary", encoding="utf-8")
        (marks / "decoy").write_text("decoy", encoding="utf-8")
        return cls(execution_id, cwd, scratch, marks, pwd.getpwuid(os.getuid()).pw_name)

    def register(self, kind: str, environment_digest: str) -> None:
        # Outside cwd/scratch: sandbox children cannot erase their recovery identity.
        registry = self.cwd.parent / SANDBOX_RUNS
        registry.mkdir(mode=0o700, exist_ok=True)
        self.index = registry / f"{self.execution_id}.run.json"
        self.owner_fd = os.open(self.marks / "owner.lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        body = {
            "schema_version": 1,
            "execution_id": self.execution_id,
            "kind": kind,
            "uid": os.getuid(),
            "cwd": str(self.cwd),
            "scratch": str(self.scratch),
            "marks": str(self.marks),
            "environment_digest": environment_digest,
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        self.identity = {**body, "identity_sha256": _digest(body)}
        try:
            _durable_json(self.marks / "identity.json", self.identity)
            _durable_json(self.index, self.identity)
        except BaseException:
            os.close(self.owner_fd)
            self.owner_fd = None
            raise

    def close_descriptors(self) -> None:
        while self.descriptors:
            with contextlib.suppress(OSError):
                os.close(self.descriptors.pop())

    def close(self, *, clean: bool = True) -> None:
        try:
            if clean:
                if self.index is not None and self.identity is not None:
                    _finish_sandbox_cleanup(self.index, self.identity, mode="live")
                else:
                    shutil.rmtree(self.scratch, ignore_errors=True)
                    shutil.rmtree(self.marks, ignore_errors=True)
            # An uncertain/error run deliberately retains marks, identity and cwd.
        finally:
            if self.owner_fd is not None:
                os.close(self.owner_fd)
                self.owner_fd = None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


# Durable discovery lives beside execution copies, never in a child's writable tree.
SANDBOX_RUNS = ".sandbox-runs"


def _durable_json(path: Path, body: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(body, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cleanup_receipt(index: Path) -> Path:
    return index.with_name(index.name.replace(".run.json", ".cleanup.json"))


def _record_cleanup(index: Path, receipt: dict[str, Any]) -> None:
    # Keep previous failed/uncertain attempts when a later cold retry succeeds.
    history = index.with_name(index.stem + "." + uuid.uuid4().hex + ".cleanup.json")
    _durable_json(history, receipt)
    _durable_json(_cleanup_receipt(index), receipt)


def _finish_sandbox_cleanup(index: Path, identity: dict[str, Any], *, mode: str) -> dict[str, Any]:
    receipt = {"status": "reaped", "mode": mode, "identity": identity, "residual_pids": []}
    # Commit proof first: a crash during directory deletion can resume this exact cleanup.
    _record_cleanup(index, receipt)
    for key in ("scratch", "marks"):
        target = Path(identity[key])
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            pass  # another cleanup may have completed the same durable proof
    index.unlink(missing_ok=True)
    return receipt


def _sandbox_members(run: _Run, *, uid_only: bool = True, timeout: float = 2) -> set[int]:
    check = _sandbox_check()
    run.table = _process_table(strict=True, timeout=timeout)
    members = set()
    for proc in run.table:
        if proc.zombie or proc.pid == os.getpid() or (uid_only and proc.uid != os.getuid()):
            continue
        allowed, denied = check(proc.pid, str(run.canary)), check(proc.pid, str(run.decoy))
        if allowed == 0 and denied == 1:
            members.add(proc.pid)
        elif allowed not in (0, 1) or denied not in (0, 1):
            try:
                os.kill(proc.pid, 0)
            except ProcessLookupError:
                continue  # exited between ps and sandbox_check
            raise SandboxUnavailable("sandbox identity query unavailable")
    return members


def pending_workspace_executions(root: Path, cwd: Path) -> bool:
    """Deletion must not erase a still-live or unclassified execution's only copy."""
    for index in (root / SANDBOX_RUNS).glob("*.run.json"):
        try:
            identity = json.loads(index.read_text())
            if not isinstance(identity, dict):
                return True
            body = {key: value for key, value in identity.items() if key != "identity_sha256"}
            if _digest(body) != identity.get("identity_sha256"):
                return True
            if identity["cwd"] == str(cwd.resolve()):
                return True
        except (OSError, ValueError, KeyError, TypeError):
            return True  # cannot assign the damaged identity safely; retain copies
    return False


def recover_workspace_executions(
    root: Path,
    *,
    kill: Callable[[int], None] | None = None,
    max_sweeps: int = 6,
    timeout_seconds: float = 5,
) -> list[dict[str, Any]]:
    """Cold sandbox cleanup, with no PID/parent-tree authority from the dead host.

    Each active executor holds a non-inherited flock. Only an orphan can be claimed;
    every signal is based on the original sandbox identity (seatbelt: the canary/decoy
    pair; bwrap: boot id + the namespace's first process + the pid namespace). Failed
    scans or surviving members retain the identity and copy, with an explicit receipt.
    """
    reports: list[dict[str, Any]] = []
    root = root.resolve()
    for index in sorted((root / SANDBOX_RUNS).glob("*.run.json")):
        descriptor = None
        identity: dict[str, Any] = {}
        residual: set[int] = set()
        try:
            identity = json.loads(index.read_text())
            if not isinstance(identity, dict):
                raise ValueError("invalid sandbox recovery record")
            body = {key: value for key, value in identity.items() if key != "identity_sha256"}
            execution_id = identity["execution_id"]
            cwd, scratch, marks = (Path(identity[key]) for key in ("cwd", "scratch", "marks"))
            if (
                identity["schema_version"] != 1
                or identity["kind"] not in ("seatbelt", "bwrap")
                or identity["uid"] != os.getuid()
                or _digest(body) != identity["identity_sha256"]
                or index.name != f"{execution_id}.run.json"
                or cwd.parent != root
                or scratch.name != execution_id
                or marks != scratch.with_name(execution_id + ".marks")
                or any(path.is_symlink() for path in (index, cwd, scratch, marks))
            ):
                raise ValueError("invalid sandbox recovery identity")
            prior_paths = [
                _cleanup_receipt(index),
                *index.parent.glob(index.stem + ".*.cleanup.json"),
            ]
            reaped = False
            for prior_path in prior_paths:
                if prior_path.exists():
                    prior = json.loads(prior_path.read_text())
                    if prior.get("status") == "reaped" and prior.get("identity") == identity:
                        reaped = True
                        break
            if reaped:
                reports.append(_finish_sandbox_cleanup(index, identity, mode="resume_cleanup"))
                continue
            descriptor = os.open(marks / "owner.lock", os.O_RDWR | os.O_NOFOLLOW)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                reports.append({"status": "active", "identity": identity, "residual_pids": []})
                continue
            if json.loads((marks / "identity.json").read_text()) != identity:
                raise ValueError("sandbox recovery identity differs from original marks")
            if not all((marks / name).is_file() for name in ("canary", "decoy")):
                raise ValueError("original sandbox identity marks are missing")
            deadline = time.monotonic() + timeout_seconds
            if identity["kind"] == "bwrap":
                reports.append(_cold_bwrap_cleanup(index, identity, marks, deadline))
                continue
            run = _Run(execution_id, cwd, scratch, marks, pwd.getpwuid(os.getuid()).pw_name)
            for sweep in range(max_sweeps + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("sandbox cleanup deadline exceeded")
                residual = _sandbox_members(run, timeout=min(2, remaining))
                if not residual:
                    reports.append(_finish_sandbox_cleanup(index, identity, mode="cold"))
                    break
                if sweep == max_sweeps:
                    report = {
                        "status": "residual",
                        "identity": identity,
                        "residual_pids": sorted(residual),
                    }
                    _record_cleanup(index, report)
                    reports.append(report)
                    break
                check = _sandbox_check()
                for pid in residual:
                    # Recheck immediately before signalling: never trust a recycled PID.
                    if check(pid, str(run.canary)) == 0 and check(pid, str(run.decoy)) == 1:
                        (kill or _sigkill)(pid)
                time.sleep(0.05)
        except (OSError, ValueError, KeyError, TypeError, SandboxUnavailable) as error:
            report = {
                "status": "unknown",
                "identity": identity,
                "residual_pids": sorted(residual),
                "reason": type(error).__name__,
            }
            _record_cleanup(index, report)
            reports.append(report)
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return reports


# ------------------------------------------------------------------ process only
class ProcessOnlyExecutor(_Executor):
    """No sandbox: a child in its own session with the hard rlimits, reaped along the
    parent chain and by the processes holding its directory.  For trusted code only."""

    kind = "process_only"
    isolated = False

    def __init__(self, interpreter: str | None = None, **options: Any) -> None:
        options.setdefault("poll_interval", 0.1)
        super().__init__(interpreter, **options)

    @property
    def usable(self) -> bool:
        return True

    def _members(self, run: _Run) -> set[int]:
        run.table = _process_table()
        if run.root_pid is None:
            return set()
        alive = {p.pid for p in run.table if not p.zombie}
        return _descendants(run.table, run.root_pid) & alive

    def _survivors(self, run: _Run) -> set[int]:
        run.table = _process_table()
        alive = {p.pid for p in run.table if not p.zombie and p.uid == os.getuid()}
        found = set(run.seen)
        if run.root_pid is not None:
            found |= _descendants(run.table, run.root_pid)
        found |= _holding(run.cwd)
        found.discard(os.getpid())
        return found & alive


def _holding(directory: Path) -> set[int]:
    """Processes of this user whose cwd or an open file lies in ``directory``."""

    if LINUX:
        return _linux_holding(directory)
    if not Path(LSOF).exists():
        return set()
    try:
        # bounded on purpose: a reap runs this once per sweep, and on a loaded machine an
        # unbounded scan made a single reap take over a minute (wheel verification 0.10.0).
        # Missing a slow answer costs a sweep, never correctness — the pid set only grows.
        out = subprocess.run(
            [LSOF, "-t", "-u", str(os.getuid()), "-a", "+D", str(directory)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {int(line) for line in out.split() if line.strip().isdigit()}


def _linux_holding(directory: Path) -> set[int]:
    """Linux 没有 ``lsof`` 也行：读本用户进程的 ``cwd`` 与打开的描述符链接。"""

    root = str(directory.resolve())
    uid = os.getuid()
    found: set[int] = set()
    try:
        names = os.listdir(PROC)
    except OSError:
        return found
    for name in names:
        if not name.isdigit():
            continue
        base = PROC / name
        try:
            if os.stat(base).st_uid != uid:
                continue
            links = [os.readlink(base / "cwd")]
            links += [os.readlink(base / "fd" / fd) for fd in os.listdir(base / "fd")]
        except OSError:
            continue  # 刚结束，或读不到
        if any(link == root or link.startswith(root + "/") for link in links):
            found.add(int(name))
    return found


# ------------------------------------------------------------------ seatbelt
_CHECK: Callable[[int, str], int] | None = None


def _sandbox_check() -> Callable[[int, str], int]:
    """``sandbox_check(pid, "file-read-data", PATH | NO_REPORT, path)``: 0 allowed, 1 denied
    (an undocumented libsystem_sandbox export, used the way WebKit and Chromium do)."""

    global _CHECK
    if _CHECK is None:
        library = ctypes.CDLL("/usr/lib/system/libsystem_sandbox.dylib")
        no_report = ctypes.c_int.in_dll(library, "SANDBOX_CHECK_NO_REPORT").value
        function = library.sandbox_check
        function.restype = ctypes.c_int
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]  # fixed part only
        filter_path = 1  # SANDBOX_FILTER_PATH

        def check(pid: int, path: str) -> int:
            target = ctypes.c_char_p(os.fsencode(path))  # the variadic argument, typed
            return int(function(pid, b"file-read-data", filter_path | no_report, target))

        _CHECK = check
    return _CHECK


def _quote(path: str | Path) -> str:
    return json.dumps(str(path))  # an SBPL string literal


def _interpreter_facts(interpreter: str) -> dict[str, Any]:
    probe = (
        "import json, os, sys; print(json.dumps({'prefix': sys.prefix, 'base': sys.base_prefix,"
        " 'executable': sys.executable, 'path': sys.path, 'version': sys.version}))"
    )
    try:
        done = subprocess.run(
            [interpreter, "-I", "-c", probe],
            cwd="/",
            env={"PATH": DEFAULT_PATH},
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return json.loads(done.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        message = f"cannot inspect the interpreter {interpreter}: {error}"
        raise SandboxUnavailable(message) from error


def _read_whitelist(
    interpreter: str, system: Sequence[str], extra: Sequence[str]
) -> tuple[list[str], str]:
    """只读白名单从运行用的解释器算（review round 2 P2-9）：它的前缀、基础前缀、真实可执行文件
    所在目录、每个存在的 ``sys.path`` 条目（含 ``.pth`` 带进来的），再加系统位置。"""

    facts = _interpreter_facts(interpreter)
    paths: list[str] = list(system)
    for raw in (
        facts["prefix"],
        facts["base"],
        os.path.dirname(os.path.realpath(facts["executable"])),
        os.path.dirname(os.path.realpath(interpreter)),
        *facts["path"],
    ):
        if raw and raw != "/" and os.path.exists(raw):
            paths.append(os.path.realpath(raw))
    paths.extend(os.path.realpath(p) for p in extra)
    return paths, str(facts["version"])


class SeatbeltExecutor(_Executor):
    kind = "seatbelt"
    isolated = True

    def __init__(
        self,
        interpreter: str,
        *,
        read_paths: Sequence[str],
        python_version: str = "",
        **options: Any,
    ) -> None:
        if sys.platform != "darwin" or not Path(SANDBOX_EXEC).exists():
            raise SandboxUnavailable("the seatbelt sandbox needs macOS sandbox-exec")
        options.setdefault("poll_interval", 0.25)
        super().__init__(interpreter, **options)
        self._read_paths = tuple(dict.fromkeys(read_paths))
        self._python_version = python_version
        self.probe_report: ProbeReport | None = None
        try:
            _sandbox_check()
        except (OSError, AttributeError, ValueError) as error:
            raise SandboxUnavailable(f"sandbox_check is not available: {error}") from error

    @classmethod
    def for_interpreter(
        cls,
        interpreter: str | None = None,
        *,
        extra_read_paths: Sequence[str] = (),
        **options: Any,
    ) -> SeatbeltExecutor:
        """The read whitelist comes from the interpreter the runs use (review round 2
        P2-9): its prefix, base prefix, the directory of its real executable and every
        existing ``sys.path`` entry (``.pth`` paths included) — plus the system paths."""

        chosen = str(interpreter or sys.executable)
        paths, version = _read_whitelist(chosen, SYSTEM_READ_PATHS, extra_read_paths)
        return cls(chosen, read_paths=paths, python_version=version, **options)

    @property
    def base_read_paths(self) -> tuple[str, ...]:
        return self._read_paths

    @property
    def usable(self) -> bool:
        return (
            self.probe_report is not None
            and self.probe_report.ok
            and self.probe_report.environment_digest == self.environment_digest
        )

    def _identity(self) -> dict[str, Any]:
        return {
            "interpreter": os.path.realpath(self.interpreter),
            "python": self._python_version,
            "read_paths": list(self._read_paths),
            "profile": _profile("<cwd>", "<scratch>", "<canary>", self._read_paths),
        }

    def _argv(self, command: Sequence[str], run: _Run) -> list[str]:
        profile = _profile(run.cwd, run.scratch, run.canary, self._read_paths)
        return [SANDBOX_EXEC, "-p", profile, *command]

    def _identified(self, run: _Run, *, uid_only: bool = True) -> set[int]:
        return _sandbox_members(run, uid_only=uid_only)

    def _members(self, run: _Run) -> set[int]:
        return self._identified(run)

    def _survivors(self, run: _Run) -> set[int]:
        return self._identified(run)


def _profile(cwd: Any, scratch: Any, canary: Any, read_paths: Sequence[str]) -> str:
    reads = " ".join(f"(subpath {_quote(p)})" for p in read_paths)
    devices = " ".join(f"(literal {_quote(p)})" for p in DEVICE_READ_LITERALS)
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny network*)\n"
        '(deny file-read-data (subpath "/"))\n'
        f'(allow file-read-data (literal "/") {devices} {reads} '
        f"(subpath {_quote(cwd)}) (subpath {_quote(scratch)}) (literal {_quote(canary)}))\n"
        '(deny file-write* (subpath "/"))\n'
        f"(allow file-write* (subpath {_quote(cwd)}) (subpath {_quote(scratch)}) "
        '(literal "/dev/null"))\n'
        "(deny signal)\n"
        "(allow signal (target same-sandbox))\n"
        "(deny mach-lookup)\n"
        "(deny process-info* (target others))\n"
    )


# ------------------------------------------------------------------ bubblewrap (Linux)
def read_sysctl(name: str) -> str | None:
    try:
        return (PROC / "sys" / Path(*name.split("."))).read_text().strip()
    except OSError:
        return None


def linux_userns_hint() -> str:
    """bubblewrap 起不来时最可能的系统原因（只读系统设置，不改它们）。"""

    hints = []
    if read_sysctl("kernel.apparmor_restrict_unprivileged_userns") == "1":
        hints.append(
            "AppArmor restricts unprivileged user namespaces (Ubuntu 23.10+); install the"
            " bwrap profile: python -m agent_orchestrator.host.sandbox_doctor --apparmor-profile"
        )
    if read_sysctl("kernel.unprivileged_userns_clone") == "0":
        hints.append("kernel.unprivileged_userns_clone=0 forbids user namespaces")
    if read_sysctl("user.max_user_namespaces") == "0":
        hints.append("user.max_user_namespaces=0 forbids user namespaces")
    return "; ".join(hints)


#: 草稿目录下给隔离区当 ``/dev/shm`` 的子目录。
SHM_DIR = ".shm"


def _bwrap_features(bwrap: str) -> tuple[str, bool]:
    """版本与是否支持 ``--disable-userns``（0.8 起；隔离区里不能再开用户命名空间靠它）。"""

    try:
        version = subprocess.run(
            [bwrap, "--version"], capture_output=True, text=True, timeout=10, check=False
        ).stdout.strip()
        usage = subprocess.run(
            [bwrap, "--help"], capture_output=True, text=True, timeout=10, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise SandboxUnavailable(f"bubblewrap could not be inspected: {error}") from error
    if not version:
        raise SandboxUnavailable("bubblewrap did not report its version")
    return version, "--disable-userns" in usage


def _symlink_hops(path: str, *, limit: int = 40) -> list[tuple[str, str]]:
    """解析 ``path`` 途中经过的每一个符号链接（链接所在路径、链接内容），按出现顺序。

    例如 uv 建的虚拟环境：``venv/bin/python`` → ``…/uv/python/cpython-3.13-…/bin/python3.13``，
    中间的 ``cpython-3.13-…`` 本身又是指向带小版本号目录的链接。白名单只有解析后的真实目录，
    隔离区里要把这些链接照原样建出来，按原路径启动解释器才找得到。"""

    hops: list[tuple[str, str]] = []
    current = Path("/")
    queue = list(Path(os.path.abspath(path)).parts[1:])
    while queue and len(hops) < limit:
        candidate = current / queue.pop(0)
        if not candidate.is_symlink():
            current = candidate
            continue
        target = os.readlink(candidate)
        hops.append((str(candidate), target))
        resolved = Path(os.path.normpath(current / target))  # current 只由真实目录组成
        queue = list(resolved.parts[1:]) + queue
        current = Path("/")
    return hops


def _bwrap_mounts(read_paths: Sequence[str], links: Sequence[tuple[str, str]] = ()) -> list[str]:
    """只读挂载参数：系统位置（是链接的照原样建链接）+ 白名单；已被上级挂上的不重复挂；
    再补上解释器路径途中、落在挂载之外的链接。"""

    args: list[str] = []
    covered: list[str] = []

    def is_covered(path: str) -> bool:
        return any(path == root or path.startswith(root.rstrip("/") + "/") for root in covered)

    for path in (*LINUX_SYSTEM_READ_PATHS, *sorted(read_paths)):
        if not os.path.lexists(path) or is_covered(path):
            continue
        if os.path.islink(path):
            args += ["--symlink", os.readlink(path), path]
        else:
            args += ["--ro-bind", path, path]
        covered.append(path)
    for link, target in links:
        if not is_covered(link):
            args += ["--symlink", target, link]
            covered.append(link)
    return args


def _read_until_eof(descriptor: int, timeout: float) -> bytes:
    """读 bwrap 的信息管道直到它关掉；这个函数负责关读端（被取消的调用方不再碰它）。"""

    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("the sandbox did not report its namespaces in time")
            ready, _, _ = select.select([descriptor], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            if sum(len(c) for c in chunks) > 1024 * 1024:
                raise ValueError("the sandbox information is too large")
    finally:
        os.close(descriptor)


def _pin_namespace(pid: int, namespace: str | None) -> int | None:
    """打开 ``pid`` 的进程命名空间并核对它就是 ``namespace``；返回持有它的描述符。"""

    if namespace is None:
        return None
    try:
        descriptor = os.open(PROC / str(pid) / "ns" / "pid", os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if os.readlink(f"/proc/self/fd/{descriptor}") == namespace:
            return descriptor
    except OSError:
        pass
    os.close(descriptor)
    return None


def _open_pidfd(pid: int) -> int:
    """钉住进程的 pidfd；Python 或内核没有 pidfd（4.14 这类 5.3 以前的内核报 ENOSYS）抛 OSError。"""

    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise OSError(errno.ENOSYS, "pidfd is not available")
    return os.pidfd_open(pid)


def _signal_in_namespace(pid: int, namespace: str | None) -> None:
    """先用 pidfd 钉住进程、再核对它还在这个命名空间、再发 SIGKILL：进程号被复用也不误杀。
    内核没有 pidfd（5.3 以前）时退回"核对后直接发"。"""

    if namespace is None:
        return
    try:
        handle = _open_pidfd(pid)
    except ProcessLookupError:
        return
    except OSError:
        if _pid_namespace(pid) == namespace:
            _sigkill(pid)
        return
    try:
        if _pid_namespace(pid) == namespace:
            signal.pidfd_send_signal(handle, signal.SIGKILL)  # type: ignore[attr-defined,unused-ignore]
    except OSError:
        pass
    finally:
        os.close(handle)


class BwrapExecutor(_Executor):
    """Linux 的隔离区（bubblewrap，非特权用户命名空间）。

    每次运行：新的用户、进程、网络、IPC、UTS 命名空间，隔离区里不能再开用户命名空间，丢掉全部
    能力；根目录是空的、只读的，只读挂系统位置和解释器白名单，可写的只有本次的副本和草稿目录
    （宿主路径原样挂，回执里的路径和宿主看到的一致；``TMPDIR`` 指向草稿）；``/proc`` 只看得到
    隔离区自己的进程。没有网络：新的网络命名空间里只有回环，宿主的端口、抽象 Unix 套接字、
    D-Bus 都够不着。

    分两步建：外层 bwrap 只建用户命名空间（并禁止里面再建），内层 bwrap 在其中建进程、网络、
    IPC、UTS 命名空间和隔离区本身。有的内核（Droidian 4.14）在同一次克隆里同时建用户与进程
    命名空间后，挂 ``/proc`` 报 EBUSY；分两步在这些内核和普通发行版上都能建。两层都用 bwrap
    （Ubuntu 的 AppArmor 只放行 bwrap 建用户命名空间），宿主给它设"不再提权"，setuid 版的
    bwrap 也按非特权方式运行。隔离区里的身份是用户命名空间里的 0 号，没有能力位，exec 也拿不回
    （启动检查实测）。"""

    kind = "bwrap"
    isolated = True
    no_new_privs = True

    def __init__(
        self,
        interpreter: str,
        *,
        read_paths: Sequence[str],
        python_version: str = "",
        bwrap: str | None = None,
        **options: Any,
    ) -> None:
        if not LINUX:
            raise SandboxUnavailable("the bubblewrap sandbox needs Linux")
        found = shutil.which(bwrap or "bwrap")
        if found is None:
            raise SandboxUnavailable(
                "bubblewrap is not installed (no bwrap found); install the bubblewrap package"
            )
        options.setdefault("poll_interval", 0.25)
        super().__init__(interpreter, **options)
        self.bwrap = os.path.realpath(found)
        self.bwrap_version, disable_userns = _bwrap_features(self.bwrap)
        if not disable_userns:
            raise SandboxUnavailable(
                f"{self.bwrap_version} has no --disable-userns; bubblewrap 0.8 or newer is needed"
            )
        self._read_paths = tuple(dict.fromkeys(read_paths))
        self._python_version = python_version
        self._mounts = _bwrap_mounts(self._read_paths, _symlink_hops(self.interpreter))
        self.probe_report: ProbeReport | None = None
        self._preflight()

    @classmethod
    def for_interpreter(
        cls,
        interpreter: str | None = None,
        *,
        extra_read_paths: Sequence[str] = (),
        **options: Any,
    ) -> BwrapExecutor:
        chosen = str(interpreter or sys.executable)
        paths, version = _read_whitelist(chosen, LINUX_SYSTEM_READ_PATHS, extra_read_paths)
        return cls(chosen, read_paths=paths, python_version=version, **options)

    @property
    def base_read_paths(self) -> tuple[str, ...]:
        return self._read_paths

    @property
    def usable(self) -> bool:
        return (
            self.probe_report is not None
            and self.probe_report.ok
            and self.probe_report.environment_digest == self.environment_digest
        )

    def _identity(self) -> dict[str, Any]:
        return {
            "interpreter": os.path.realpath(self.interpreter),
            "python": self._python_version,
            "read_paths": list(self._read_paths),
            "bwrap_version": self.bwrap_version,
            "argv": self._bwrap_argv(
                ["<command>"], cwd="<cwd>", scratch="<scratch>", info_fd="<fd>"
            ),
        }

    def _bwrap_argv(
        self, command: Sequence[str], *, cwd: Any, scratch: Any, info_fd: Any
    ) -> list[str]:
        # 外层：只建用户命名空间，里面是 0 号、带全部能力（只在这个命名空间里），并禁止再建
        # 用户命名空间；根目录照原样挂过去，内层 bwrap 才找得到自己和宿主的 /proc
        argv = [self.bwrap, "--unshare-user", "--disable-userns", "--uid", "0", "--gid", "0"]
        argv += ["--cap-add", "ALL", "--dev-bind", "/", "/", "--die-with-parent", "--"]
        # 内层：不建用户命名空间，其余全部新建；丢掉全部能力
        argv += [self.bwrap, "--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts"]
        argv += ["--unshare-cgroup-try", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
        if info_fd is not None:
            argv += ["--info-fd", str(info_fd)]
        argv += [*self._mounts, "--proc", "/proc", "--dev", "/dev"]
        argv += ["--bind", str(cwd), str(cwd), "--bind", str(scratch), str(scratch)]
        # /dev/shm 换成草稿目录下的磁盘目录：多进程的信号量要在这里建文件（macOS 上也能用）
        argv += ["--bind", f"{scratch}/{SHM_DIR}", "/dev/shm"]
        # 根目录与 /dev 是内存盘：写进去的文件占内存、不算进常驻内存采样，所以都改成只读
        # （只读重挂不往下递归，上面的绑定照样可写）；临时文件走 TMPDIR = 草稿目录
        argv += ["--remount-ro", "/dev", "--remount-ro", "/"]
        return [*argv, "--chdir", str(cwd), "--", *command]

    def _preflight(self) -> None:
        """先起一个最小隔离区跑一下解释器：起不来就把 bwrap 的原话和最可能的系统原因带出去。"""

        with tempfile.TemporaryDirectory(prefix="orch-bwrap-check-") as raw:
            work = Path(raw).resolve()
            (work / SHM_DIR).mkdir()
            argv = self._bwrap_argv(
                [self.interpreter, "-I", "-c", "pass"], cwd=work, scratch=work, info_fd=None
            )
            try:
                done = subprocess.run(
                    argv,
                    env={"PATH": DEFAULT_PATH},
                    preexec_fn=_preexec(60, 1 << 30, no_new_privs=True),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise SandboxUnavailable(f"bubblewrap could not be started: {error}") from error
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip()[-400:]
            hint = linux_userns_hint()
            raise SandboxUnavailable(
                f"bubblewrap cannot create a sandbox here: {detail}"
                + (f" ({hint})" if hint else "")
            )

    def _spawn_options(self, run: _Run) -> dict[str, Any]:
        read_end, write_end = os.pipe()
        run.descriptors += [read_end, write_end]
        run.info_pipe = (read_end, write_end)
        return {"pass_fds": (write_end,)}

    def _argv(self, command: Sequence[str], run: _Run) -> list[str]:
        assert run.info_pipe is not None
        (run.scratch / SHM_DIR).mkdir(mode=0o700, exist_ok=True)
        return self._bwrap_argv(command, cwd=run.cwd, scratch=run.scratch, info_fd=run.info_pipe[1])

    async def _started(self, run: _Run) -> None:
        """读 bwrap 报的隔离区信息（第一个进程、进程命名空间），落盘供冷恢复按身份回收。"""

        assert run.info_pipe is not None
        read_end, write_end = run.info_pipe
        run.descriptors.remove(write_end)
        os.close(write_end)
        run.descriptors.remove(read_end)  # 从这里起由读线程关
        raw = await asyncio.to_thread(_read_until_eof, read_end, 30.0)
        if not raw.strip():
            return  # 隔离区没建起来就结束了：bwrap 的原话在输出里
        info = json.loads(raw)
        init_pid = int(info["child-pid"])
        namespace = (
            f"pid:[{int(info['pid-namespace'])}]"
            if "pid-namespace" in info
            else _pid_namespace(init_pid)
        )
        run.init_pid = init_pid
        # 钉住命名空间：持有它的描述符到运行收尾，命名空间结束后编号也不会被别的隔离区复用，
        # 收尾时按编号找成员就不会找到别人的进程。钉不住 = 第一个进程已经结束、整个隔离区随它
        # 结束了，不再按编号找成员。
        pinned = _pin_namespace(init_pid, namespace)
        if pinned is None:
            return
        run.descriptors.append(pinned)
        run.pid_namespace = namespace
        fields = _proc_stat(init_pid)
        if fields is None or _pid_namespace(init_pid) != namespace:
            return  # 第一个进程已经结束
        record = {
            "execution_id": run.execution_id,
            "identity_sha256": (run.identity or {}).get("identity_sha256"),
            "boot_id": _boot_id(),
            "init_pid": init_pid,
            "init_started": fields[19],
            "pid_namespace": namespace,
        }
        await asyncio.to_thread(
            _durable_json,
            run.marks / "namespace.json",
            {**record, "record_sha256": _digest(record)},
        )

    def _members(self, run: _Run) -> set[int]:
        if run.pid_namespace is None:
            run.table = []
            return set()
        members = _namespace_members(run.pid_namespace)
        page_kb = max(1, os.sysconf("SC_PAGE_SIZE") // 1024)
        table = []
        for pid in members:
            try:
                fields = _proc_stat(pid)
                if fields is not None:
                    table.append(_Proc(pid, int(fields[1]), -1, int(fields[21]) * page_kb, False))
            except (OSError, IndexError, ValueError):
                continue
        run.table = table
        return members

    def _survivors(self, run: _Run) -> set[int]:
        return self._members(run)

    def _kill_member(self, run: _Run, pid: int) -> None:
        if self._kill is not _sigkill:  # 测试接缝：照样先核对命名空间
            if _pid_namespace(pid) == run.pid_namespace:
                self._kill(pid)
            return
        _signal_in_namespace(pid, run.pid_namespace)

    def _cpu_limited(self, returncode: int | None) -> bool:
        # bwrap 把"被信号结束"转成 128 + 信号号的退出码
        return returncode in (-signal.SIGXCPU, 128 + signal.SIGXCPU)


def _cold_bwrap_cleanup(
    index: Path, identity: dict[str, Any], marks: Path, deadline: float
) -> dict[str, Any]:
    residual = _settle_bwrap_run(marks, identity, deadline)
    if not residual:
        return _finish_sandbox_cleanup(index, identity, mode="cold")
    report = {"status": "residual", "identity": identity, "residual_pids": sorted(residual)}
    _record_cleanup(index, report)
    return report


def _settle_bwrap_run(marks: Path, identity: Mapping[str, Any], deadline: float) -> set[int]:
    """冷恢复一次 bwrap 运行：按落盘的"开机号 + 第一个进程的启动时刻 + 进程命名空间"核对，
    对上了才结束它的第一个进程（内核随之结束整个命名空间），再等成员清空；对不上一个不碰。
    返回仍在的成员。"""

    path = marks / "namespace.json"
    if not path.exists():
        # 没记下命名空间：要么隔离区没建起来，要么宿主先死了——--die-with-parent 已让它结束
        return set()
    record = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in record.items() if key != "record_sha256"}
    if (
        _digest(body) != record.get("record_sha256")
        or record.get("execution_id") != identity["execution_id"]
        or record.get("identity_sha256") != identity["identity_sha256"]
    ):
        raise ValueError("invalid sandbox namespace record")
    if record["boot_id"] != _boot_id():
        return set()  # 机器重启过：这次运行的进程都不在了
    namespace, init_pid = str(record["pid_namespace"]), int(record["init_pid"])
    fields = _proc_stat(init_pid)
    if (
        fields is None
        or fields[19] != str(record["init_started"])
        or _pid_namespace(init_pid) != namespace
    ):
        return set()  # 第一个进程不在了，命名空间也就不在了
    _signal_in_namespace(init_pid, namespace)
    while True:
        members = _namespace_members(namespace)
        if not members or time.monotonic() >= deadline:
            return members
        time.sleep(0.05)


# ------------------------------------------------------------------ the capability probe
@dataclass(frozen=True, slots=True)
class ProbeReport:
    ok: bool
    items: tuple[dict[str, Any], ...]
    targets: tuple[str, ...]
    disjoint: bool
    environment_digest: str

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "items": [dict(item) for item in self.items],
            "targets": list(self.targets),
            "disjoint": self.disjoint,
            "environment_digest": self.environment_digest,
            "version": SANDBOX_VERSION,
        }


_ACCESS_PROBE = """
import json, os, socket, sys
cfg = json.loads(sys.argv[1])
out = {}
def attempt(name, fn):
    try:
        fn()
        out[name] = "ALLOWED"
    except Exception as error:
        out[name] = type(error).__name__
def list_home():
    out["home_entries"] = sorted(os.listdir(cfg["home"]))
attempt("read_home", list_home)
attempt("read_other_temp", lambda: open(cfg["secret"]).read())
attempt("write_outside", lambda: open(cfg["escape"], "w").write("x"))
attempt("network_tcp", lambda: socket.create_connection(("127.0.0.1", cfg["port"]), timeout=2))
def unix():
    s = socket.socket(socket.AF_UNIX)
    s.connect(cfg["unix"])
attempt("network_unix", unix)
def lan():
    socket.create_connection((cfg["lan"], cfg["port"]), timeout=2)
if cfg.get("lan"):
    attempt("network_lan", lan)
def abstract():
    s = socket.socket(socket.AF_UNIX)
    s.connect("\\0" + cfg["abstract"])
if cfg.get("abstract"):
    attempt("network_abstract", abstract)
attempt("signal_host", lambda: os.kill(cfg["host_pid"], 0))
if cfg.get("linux"):
    import ctypes, subprocess
    def new_user_namespace():
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.unshare(0x10000000) != 0:  # CLONE_NEWUSER；3.11 没有 os.unshare
            raise OSError(ctypes.get_errno(), "unshare")
    attempt("new_user_namespace", new_user_namespace)
    caps = "[l.split()[1] for l in open('/proc/self/status') if l.startswith('CapEff')][0]"
    out["cap_eff"] = eval(caps)
    # 隔离区里的身份可能是用户命名空间里的 0 号：再 exec 一次，看能力位是不是拿得回来
    child = subprocess.run([sys.executable, "-c", f"print({caps})"], capture_output=True, text=True)
    out["child_cap_eff"] = child.stdout.strip()
open("probe.json", "w").write(json.dumps(out))
"""
# 后台进程关掉全部描述符后再拿一把文件锁、写"就绪"：运行结束后宿主拿得到这把锁，才证明它真的
# 结束了。不用进程号——Linux 隔离区有自己的进程号空间，里面的号在宿主那边是别的进程。
_DAEMON_PROBE = """
import fcntl, os, sys, time
lock_path, ready = os.path.abspath("daemon.lock"), os.path.abspath("daemon.ready")
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        os.chdir("/")
        os.closerange(0, 256)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        open(ready, "w").write(str(os.getpid()))
        time.sleep(600)
    os._exit(0)
deadline = time.monotonic() + 10
while not os.path.exists(ready) and time.monotonic() < deadline:
    time.sleep(0.01)
"""


def _lock_released(path: Path, seconds: float = 2.0) -> bool:
    """拿得到 ``path`` 上的排他锁 = 持锁的进程都结束了。"""

    deadline = time.monotonic() + seconds
    descriptor = os.open(path, os.O_RDWR)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.05)
    finally:
        os.close(descriptor)


def _under(path: str, roots: Sequence[str]) -> bool:
    resolved = Path(path).resolve()
    return any(resolved == Path(r) or Path(r) in resolved.parents for r in roots)


#: 通过了启动检查的执行器（按对象身份）及它当时的环境摘要；准入只认这里，不认执行器自己说的。
_PROBE_PASSED: dict[int, tuple[Any, str]] = {}


def probe_passed(executor: Any) -> bool:
    """这个执行器在这台机器上通过了 :func:`probe_sandbox`，且之后环境没变。"""

    entry = _PROBE_PASSED.get(id(executor))
    return (
        entry is not None
        and entry[0] is executor
        and entry[1] == getattr(executor, "environment_digest", None)
    )


def _home_skeleton(home: str, read_paths: Sequence[str]) -> set[str]:
    """隔离区里列 HOME 能看到的最多是什么：白名单里落在 HOME 下的路径，bwrap 会为它们建出空的
    上级目录（只有名字，没有内容）。列出来的超出这些名字，就是 HOME 漏进来了。"""

    names = set()
    for path in read_paths:
        if _under(path, [home]) and Path(path).resolve() != Path(home):
            names.add(Path(path).resolve().relative_to(home).parts[0])
    return names


def _abstract_listener() -> tuple[str, socket.socket | None]:
    """Linux 的抽象 Unix 套接字不在文件系统里（D-Bus 常用），只有网络命名空间挡得住。"""

    if not LINUX:
        return "", None
    name = f"orch-probe-{uuid.uuid4().hex}"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind("\0" + name)
    listener.listen(1)
    return name, listener


def _lan_address() -> str | None:
    """本机一个非回环的 IPv4 地址（UDP 的 connect 只选路由、不发包）；没有网络就 ``None``。"""

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1，只用来选出口地址
        address = str(probe.getsockname()[0])
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def _access_items(
    seen: Mapping[str, Any],
    *,
    ran: bool,
    escaped: bool,
    home_skeleton: set[str],
    abstract: bool,
    lan: bool = False,
    linux: bool = False,
) -> list[dict[str, Any]]:
    def denied(name: str) -> bool:
        if name == "network_lan":
            # 连本机局域网地址上没人听的端口：网络通就是"连接被拒"或超时，只有够不着
            # （没有路由、被禁止）才算拒绝。宿主不在局域网地址上监听，免得触发防火墙询问。
            return seen.get(name) not in (
                None,
                "ALLOWED",
                "ConnectionRefusedError",
                "TimeoutError",
            )
        return seen.get(name) not in (None, "ALLOWED")

    home_hidden = denied("read_home") or set(seen.get("home_entries") or ()) <= home_skeleton
    network_names = (
        ("network_tcp", "network_unix")
        + (("network_abstract",) if abstract else ())
        + (("network_lan",) if lan else ())
    )
    checks: tuple[tuple[str, bool, tuple[str, ...]], ...] = (
        ("read_home", home_hidden, ("read_home", "home_entries")),
        ("read_other_temp", denied("read_other_temp"), ("read_other_temp",)),
        ("write_outside", denied("write_outside") and not escaped, ("write_outside",)),
        ("network", all(denied(n) for n in network_names), network_names),
        ("signal_host", denied("signal_host"), ("signal_host",)),
    )
    if linux:

        def no_caps(name: str) -> bool:
            try:
                return int(str(seen.get(name)), 16) == 0
            except ValueError:
                return False

        checks += (
            (
                "privileges",
                denied("new_user_namespace") and no_caps("cap_eff") and no_caps("child_cap_eff"),
                ("new_user_namespace", "cap_eff", "child_cap_eff"),
            ),
        )
    return [
        {
            "name": name,
            "ok": bool(ok) and ran,
            "observed": {key: seen.get(key) for key in keys if key in seen},
        }
        for name, ok, keys in checks
    ]


async def probe_sandbox(executor: Any) -> ProbeReport:
    """Eight behavioural checks (plan v3 D1), nine on Linux; any miss makes the adapter
    unusable.  Every target is computed here, apart from the read whitelist, and must lie
    outside it.  Linux 多一项"特权"：隔离区里开不了新的用户命名空间，自己和 exec 出来的子进程
    都没有能力位。

    "拒绝"按行为判：尝试抛了错、目标没被改动就算拒绝（macOS 报无权限，Linux 报不存在或连接
    被拒，都是够不着）。应用自带的执行器也走这一套，通过了才准入。"""

    home = str(Path.home().resolve())
    read_paths = tuple(getattr(executor, "base_read_paths", ()) or ())
    with (
        tempfile.TemporaryDirectory(prefix="orch-probe-other-") as raw_other,
        tempfile.TemporaryDirectory(prefix="orch-probe-ws-") as raw_ws,
    ):
        other = Path(raw_other).resolve()
        workdir = Path(raw_ws).resolve()
        secret = other / "secret.txt"
        secret.write_text(PROBE_SECRET, encoding="utf-8")
        escape = other / "escape.txt"
        unix_path = other / "u.sock"
        targets = (home, str(other), str(secret), str(escape), str(unix_path))
        disjoint = not any(_under(t, read_paths) for t in targets)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        unix = socket.socket(socket.AF_UNIX)
        unix.bind(str(unix_path))
        unix.listen(1)
        abstract_name, abstract = _abstract_listener()
        lan_address = _lan_address()
        items: list[dict[str, Any]] = []
        try:
            cfg = {
                "home": home,
                "secret": str(secret),
                "escape": str(escape),
                "port": listener.getsockname()[1],
                "unix": str(unix_path),
                "abstract": abstract_name,
                "lan": lan_address or "",
                "host_pid": os.getpid(),
                "linux": LINUX,
            }
            script = workdir / "access_probe.py"
            script.write_text(_ACCESS_PROBE, encoding="utf-8")
            receipt = await executor.execute(
                [executor.interpreter, str(script), json.dumps(cfg)],
                cwd=str(workdir),
                spec=SandboxSpec(cpu_seconds=30, wall_seconds=60),
            )
            try:
                seen = json.loads((workdir / "probe.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                seen = {}

            items += _access_items(
                seen,
                ran=receipt.exit_code == 0,
                escaped=escape.exists(),
                home_skeleton=_home_skeleton(home, read_paths),
                abstract=bool(abstract_name),
                lan=bool(lan_address),
                linux=LINUX,
            )
            script = workdir / "daemon_probe.py"
            script.write_text(_DAEMON_PROBE, encoding="utf-8")
            receipt = await executor.execute(
                [executor.interpreter, str(script)],
                cwd=str(workdir),
                spec=SandboxSpec(cpu_seconds=30, wall_seconds=30),
            )
            started = (workdir / "daemon.ready").exists()
            gone = started and _lock_released(workdir / "daemon.lock")
            items.append(
                {
                    "name": "daemon_reaped",
                    "ok": gone and receipt.tree_killed,
                    "observed": {
                        "started": started,
                        "gone": gone,
                        "tree_killed": receipt.tree_killed,
                    },
                }
            )
            receipt = await executor.execute(
                [executor.interpreter, "-c", "import sys; sys.stdout.write('x' * 100000)"],
                cwd=str(workdir),
                spec=SandboxSpec(cpu_seconds=30, wall_seconds=30, max_output_bytes=4096),
            )
            items.append(
                {
                    "name": "output_truncated",
                    "ok": receipt.truncated and len(receipt.output.encode()) <= 4096,
                    "observed": {"truncated": receipt.truncated},
                }
            )
            receipt = await executor.execute(
                [executor.interpreter, "-c", "while True:\n    pass"],
                cwd=str(workdir),
                spec=SandboxSpec(cpu_seconds=1, wall_seconds=30),
            )
            items.append(
                {
                    "name": "cpu_killed",
                    "ok": receipt.limit_exceeded == "cpu" and not receipt.timed_out,
                    "observed": {"limit_exceeded": receipt.limit_exceeded},
                }
            )
        finally:
            listener.close()
            unix.close()
            if abstract is not None:
                abstract.close()
    report = ProbeReport(
        ok=disjoint and all(item["ok"] for item in items),
        items=tuple(items),
        targets=targets,
        disjoint=disjoint,
        environment_digest=executor.environment_digest,
    )
    with contextlib.suppress(AttributeError):  # 应用自带的执行器可以不收这份报告
        executor.probe_report = report
    if report.ok:
        _PROBE_PASSED[id(executor)] = (executor, report.environment_digest)
    else:
        _PROBE_PASSED.pop(id(executor), None)
    return report


# ------------------------------------------------------------------ resolving a deployment
def platform_executor(
    interpreter: str | None = None,
    *,
    extra_read_paths: Sequence[str] = (),
    bwrap: str | None = None,
) -> SandboxExecutorPort:
    """这台机器的隔离执行器：macOS → Seatbelt，Linux → bubblewrap；别的系统没有。
    还没做启动检查——交给 :func:`probe_sandbox`，通过了才准入。"""

    if sys.platform == "darwin":
        return SeatbeltExecutor.for_interpreter(interpreter, extra_read_paths=extra_read_paths)
    if LINUX:
        return BwrapExecutor.for_interpreter(
            interpreter, extra_read_paths=extra_read_paths, bwrap=bwrap
        )
    raise SandboxUnavailable(f"no sandbox for this platform ({sys.platform})")


def resolve_executor(deployment: Any, executor: Any = None) -> SandboxExecutorPort | None:
    """The executor a deployment runs model-written code with (plan v3 D2): ``off`` →
    none; ``process_only`` → the given one or a process executor; ``sandboxed`` → only an
    isolating executor whose probe passed on this very environment (Seatbelt, bubblewrap,
    or an application's own — the probe decides, not the object's own claims)."""

    mode = getattr(deployment, "code_execution", "process_only")
    if mode == "off":
        return None
    if mode == "sandboxed":
        if executor is None or getattr(executor, "isolated", False) is not True:
            raise SandboxUnavailable(
                "code_execution='sandboxed' needs an isolating executor whose probe passed"
            )
        if not probe_passed(executor):
            raise SandboxUnavailable("the sandbox executor has not passed its probe here")
        return executor
    return executor if executor is not None else ProcessOnlyExecutor()


__all__ = (
    "BwrapExecutor",
    "ExecutionReceipt",
    "ProbeReport",
    "ProcessOnlyExecutor",
    "SANDBOX_VERSION",
    "SandboxExecutionLedger",
    "SandboxExecutorPort",
    "SandboxSpec",
    "SandboxUnavailable",
    "SeatbeltExecutor",
    "identity_matches",
    "linux_userns_hint",
    "platform_executor",
    "probe_passed",
    "probe_sandbox",
    "process_identity",
    "read_sysctl",
    "reclaim_recorded_executions",
    "resolve_executor",
)
