# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Structured process primitives with exact lease ownership.

``process_start`` accepts an executable and argv only. It intentionally does
not accept a command string or ``shell=True``. ``process_stop`` operates on a
same-root-run lease or an explicit PID identity; it never kills by image name.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import psutil

from ..capabilities import ToolExecutionContext
from ..context_adapter import legacy_execution_context


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    creation_time: float
    command_line: tuple[str, ...]
    executable: str
    parent_pid: int | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "creation_time": self.creation_time,
            "command_line": list(self.command_line),
            "executable": self.executable,
            "parent_pid": self.parent_pid,
        }


@dataclass(slots=True)
class ManagedProcessLease:
    lease_id: str
    root_run_id: str
    process: asyncio.subprocess.Process
    identity: ProcessIdentity
    stdout_log: Path
    stderr_log: Path
    stdout_handle: Any
    stderr_handle: Any
    started_at: float
    observed: dict[int, ProcessIdentity] = field(default_factory=dict)
    monitor_stop: asyncio.Event = field(default_factory=asyncio.Event)
    monitor_task: asyncio.Task[None] | None = None

    def close_logs(self) -> None:
        for handle in (self.stdout_handle, self.stderr_handle):
            with contextlib.suppress(Exception):
                handle.close()


def _error(code: str, message: str, **details: Any) -> str:
    return json.dumps(
        {"ok": False, "error": {"code": code, "message": message, **details}},
        ensure_ascii=False,
    )


def _success(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _capture_identity(pid: int) -> ProcessIdentity | None:
    try:
        process = psutil.Process(pid)
        with process.oneshot():
            return ProcessIdentity(
                pid=pid,
                creation_time=float(process.create_time()),
                command_line=tuple(process.cmdline()),
                executable=process.exe(),
                parent_pid=process.ppid(),
            )
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def _matches_identity(process: psutil.Process, expected: ProcessIdentity) -> bool:
    try:
        return (
            abs(float(process.create_time()) - expected.creation_time) <= 0.01
            and (
                tuple(process.cmdline()) == expected.command_line
                # 模型交回的是进程列表给它的、密钥已隐藏的那份（同一规则，结果确定）
                or _redacted_argv(process.cmdline()) == _redacted_argv(expected.command_line)
            )
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _identity_is_still_running(identity: ProcessIdentity) -> bool:
    try:
        process = psutil.Process(identity.pid)
    except psutil.NoSuchProcess:
        return False
    return _matches_identity(process, identity) and process.is_running()


def _private_memory(process: psutil.Process) -> int:
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
        value = process.memory_full_info()
        return int(getattr(value, "private", getattr(value, "uss", 0)))
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        return int(process.memory_info().rss)
    return 0


def _resolve_executable(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("executable must be a non-empty string")
    if "\x00" in value:
        raise ValueError("executable contains NUL")
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        resolved = candidate.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("executable is not a file")
        return str(resolved)
    discovered = shutil.which(value)
    if discovered is None:
        raise ValueError(f"executable was not found: {value}")
    return str(Path(discovered).resolve(strict=True))


def _validate_argv(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("argv must be an array; shell command strings are not accepted")
    if len(value) > 256:
        raise ValueError("argv exceeds 256 items")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or "\x00" in item:
            raise ValueError("every argv item must be NUL-free text")
        result.append(item)
    return tuple(result)


class ProcessToolService:
    """Own process leases launched through DeskPet."""

    def __init__(self, *, log_root: str | os.PathLike[str] | None = None) -> None:
        default_root = Path(tempfile.gettempdir()) / "deskpet" / "process-logs"
        self.log_root = Path(log_root or default_root).expanduser().resolve(strict=False)
        self._leases: dict[str, ManagedProcessLease] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        executable: str,
        argv: Sequence[str],
        cwd: str | None,
        environment: Mapping[str, str] | None,
        root_run_id: str,
    ) -> dict[str, Any]:
        resolved = _resolve_executable(executable)
        if isinstance(argv, (str, bytes)) or not all(
            isinstance(part, str) and "\x00" not in part for part in argv
        ):
            raise ValueError("argv must be a sequence of NUL-free strings")
        if cwd is not None:
            working_directory = str(Path(cwd).expanduser().resolve(strict=True))
            if not Path(working_directory).is_dir():
                raise ValueError("cwd must be a directory")
        else:
            working_directory = None
        if environment is not None:
            if not isinstance(environment, Mapping):
                raise ValueError("environment must be an object")
            child_env = dict(os.environ)
            for key, value in environment.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise ValueError("environment keys and values must be strings")
                if "\x00" in key or "\x00" in value or "=" in key:
                    raise ValueError("environment contains an invalid entry")
                child_env[key] = value
        else:
            child_env = None

        lease_id = f"process:{root_run_id}:{uuid.uuid4()}"
        root_component = hashlib.sha256(root_run_id.encode("utf-8")).hexdigest()[:20]
        lease_dir = self.log_root / root_component
        lease_dir.mkdir(parents=True, exist_ok=True)
        suffix = lease_id.rsplit(":", 1)[-1]
        stdout_log = lease_dir / f"{suffix}.stdout.log"
        stderr_log = lease_dir / f"{suffix}.stderr.log"
        stdout_handle = stdout_log.open("xb")
        try:
            stderr_handle = stderr_log.open("xb")
        except Exception:
            stdout_handle.close()
            with contextlib.suppress(OSError):
                stdout_log.unlink()
            raise

        kwargs: dict[str, Any] = {
            "cwd": working_directory,
            "env": child_env,
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": stdout_handle,
            "stderr": stderr_handle,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(resolved, *argv, **kwargs)
            identity = _capture_identity(process.pid)
            if identity is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
                raise RuntimeError("could not capture started process identity")
            lease = ManagedProcessLease(
                lease_id=lease_id,
                root_run_id=root_run_id,
                process=process,
                identity=identity,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                stdout_handle=stdout_handle,
                stderr_handle=stderr_handle,
                started_at=time.time(),
                observed={identity.pid: identity},
            )
            async with self._lock:
                self._leases[lease_id] = lease
            lease.monitor_task = asyncio.create_task(self._monitor_lease(lease))
            return {
                **identity.to_mapping(),
                "lease_id": lease_id,
                "root_run_id": root_run_id,
                "stdout_log_ref": str(stdout_log),
                "stderr_log_ref": str(stderr_log),
            }
        except Exception:
            stdout_handle.close()
            stderr_handle.close()
            with contextlib.suppress(OSError):
                stdout_log.unlink()
            with contextlib.suppress(OSError):
                stderr_log.unlink()
            raise

    async def _monitor_lease(self, lease: ManagedProcessLease) -> None:
        while not lease.monitor_stop.is_set():
            self._observe_lease(lease)
            try:
                await asyncio.wait_for(lease.monitor_stop.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                continue

    @staticmethod
    def _observe_lease(lease: ManagedProcessLease) -> None:
        try:
            root = psutil.Process(lease.identity.pid)
            if not _matches_identity(root, lease.identity):
                return
            children = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # The root can disappear between Process(pid), identity validation,
            # and children(). Observation is best-effort; the exact identities
            # already captured on the lease remain authoritative for cleanup.
            return
        for process in children:
            identity = _capture_identity(process.pid)
            if identity is not None:
                lease.observed[identity.pid] = identity

    async def _finalize_lease(self, lease: ManagedProcessLease) -> None:
        lease.monitor_stop.set()
        if lease.monitor_task is not None:
            lease.monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, psutil.Error):
                await lease.monitor_task
        lease.close_logs()
        async with self._lock:
            self._leases.pop(lease.lease_id, None)

    async def wait(
        self, lease_id: str, *, root_run_id: str, timeout_seconds: float
    ) -> dict[str, Any]:
        lease = await self._owned_lease(lease_id, root_run_id)
        try:
            exit_code = await asyncio.wait_for(
                asyncio.shield(lease.process.wait()), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            return {
                "lease_id": lease_id,
                "pid": lease.identity.pid,
                "running": True,
                "exit_code": None,
            }
        self._observe_lease(lease)
        descendant_pids = [
            identity.pid
            for identity in lease.observed.values()
            if identity.pid != lease.identity.pid
            and _identity_is_still_running(identity)
        ]
        if not descendant_pids:
            await self._finalize_lease(lease)
        return {
            "lease_id": lease_id,
            "pid": lease.identity.pid,
            "running": bool(descendant_pids),
            "exit_code": exit_code,
            "descendant_pids": descendant_pids,
            "stdout_log_ref": str(lease.stdout_log),
            "stderr_log_ref": str(lease.stderr_log),
        }

    async def stop_owned(
        self, lease_id: str, *, root_run_id: str
    ) -> dict[str, Any]:
        lease = await self._owned_lease(lease_id, root_run_id)
        self._observe_lease(lease)
        result = await self._stop_identities(
            tuple(lease.observed.values()), root_pid=lease.identity.pid
        )
        with contextlib.suppress(Exception):
            await lease.process.wait()
        await self._finalize_lease(lease)
        return {"lease_id": lease_id, **result}

    async def stop_explicit(self, identity: ProcessIdentity) -> dict[str, Any]:
        return await self._stop_identity(identity)

    async def _owned_lease(
        self, lease_id: str, root_run_id: str
    ) -> ManagedProcessLease:
        async with self._lock:
            lease = self._leases.get(lease_id)
        if lease is None:
            raise KeyError("process lease was not found")
        if lease.root_run_id != root_run_id:
            raise PermissionError("process lease belongs to another root run")
        return lease

    async def _stop_identity(self, identity: ProcessIdentity) -> dict[str, Any]:
        try:
            root = psutil.Process(identity.pid)
        except psutil.NoSuchProcess:
            return {
                "pid": identity.pid,
                "already_exited": True,
                "matched_pids": [],
                "stopped_pids": [],
                "remaining_pids": [],
                "released_private_memory_bytes": 0,
            }
        if not _matches_identity(root, identity):
            raise PermissionError("PID identity mismatch; refusing to stop process")

        descendants: list[tuple[psutil.Process, ProcessIdentity]] = []
        for process in root.children(recursive=True):
            child_identity = _capture_identity(process.pid)
            if child_identity is not None:
                descendants.append((process, child_identity))
        return await self._stop_identities(
            tuple([item[1] for item in descendants] + [identity]),
            root_pid=identity.pid,
        )

    async def _stop_identities(
        self, identities: tuple[ProcessIdentity, ...], *, root_pid: int
    ) -> dict[str, Any]:
        expected_by_pid = {identity.pid: identity for identity in identities}
        observed: list[tuple[psutil.Process, ProcessIdentity]] = []
        for identity in identities:
            try:
                process = psutil.Process(identity.pid)
            except psutil.NoSuchProcess:
                continue
            if _matches_identity(process, identity):
                observed.append((process, identity))
        if not observed:
            return {
                "pid": root_pid,
                "already_exited": True,
                "matched_pids": [],
                "stopped_pids": [],
                "remaining_pids": [],
                "released_private_memory_bytes": 0,
            }

        def depth(identity: ProcessIdentity) -> int:
            value = 0
            current = identity
            seen: set[int] = set()
            while (
                current.parent_pid is not None
                and current.parent_pid in expected_by_pid
                and current.parent_pid not in seen
            ):
                seen.add(current.pid)
                current = expected_by_pid[current.parent_pid]
                value += 1
            return value

        observed.sort(key=lambda item: depth(item[1]), reverse=True)
        matched_pids = [process.pid for process, _ in observed]
        before_memory = sum(_private_memory(process) for process, _ in observed)
        for process, expected in observed:
            if _matches_identity(process, expected):
                with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                    process.terminate()
        _, alive = await asyncio.to_thread(
            psutil.wait_procs, [item[0] for item in observed], timeout=1.0
        )
        for process in alive:
            expected = expected_by_pid.get(process.pid)
            if expected is not None and _matches_identity(process, expected):
                with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                    process.kill()
        _, alive = await asyncio.to_thread(psutil.wait_procs, alive, timeout=1.0)
        remaining = [
            process.pid
            for process in alive
            if (
                (expected := expected_by_pid.get(process.pid)) is not None
                and _matches_identity(process, expected)
            )
        ]
        after_memory = sum(
            _private_memory(process) for process in alive if process.pid in remaining
        )
        return {
            "pid": root_pid,
            "already_exited": False,
            "matched_pids": matched_pids,
            "stopped_pids": [
                pid for pid in matched_pids if pid not in set(remaining)
            ],
            "remaining_pids": remaining,
            "released_private_memory_bytes": max(0, before_memory - after_memory),
        }

    async def close_root_run(self, root_run_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            lease_ids = [
                lease_id
                for lease_id, lease in self._leases.items()
                if lease.root_run_id == root_run_id
            ]
        results = []
        for lease_id in lease_ids:
            with contextlib.suppress(KeyError, PermissionError):
                results.append(await self.stop_owned(lease_id, root_run_id=root_run_id))
        return results

    async def reset_for_tests(self) -> None:
        async with self._lock:
            leases = tuple(self._leases.values())
            self._leases.clear()
        for lease in leases:
            self._observe_lease(lease)
            with contextlib.suppress(Exception):
                await self._stop_identities(
                    tuple(lease.observed.values()), root_pid=lease.identity.pid
                )
            await self._finalize_lease(lease)


_PROCESS_SERVICE = ProcessToolService()


def get_process_tool_service() -> ProcessToolService:
    return _PROCESS_SERVICE


def set_process_tool_service(service: ProcessToolService) -> ProcessToolService:
    """Replace the module service and return the previous one (tests/startup)."""

    global _PROCESS_SERVICE
    previous = _PROCESS_SERVICE
    _PROCESS_SERVICE = service
    return previous


# 进程列表交给模型前把命令行里的密钥藏起来（试用前 2026-10-08 用户定）：别的程序常把密钥写在
# 参数里（``--api-key=…``、``--token …``、``API_KEY=…``、``sk-…``、``https://u:p@…``、
# ``bash -c "… --token …"``）。停外部进程时模型交回的是这份隐藏后的命令行，所以身份比对两边都按
# 同一规则隐藏后再比（``_matches_identity``）；进程号与启动时间照比，身份不会认错。
_SECRET_WORDS = (
    "key|apikey|token|secret|password|passwd|pwd|passphrase|credential|credentials|cookie"
    "|auth|authorization|bearer"
)
# 名字按 - _ . 分段，整段是密钥词（``--max-tokens``、``--tokenizer``、``--author`` 不算）
_SECRET_NAME = re.compile(rf"(?i)(?:^|[-_.])(?:{_SECRET_WORDS})(?:$|[-_.])")
_SECRET_VALUE = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_-]{20,}|(?:sk|pk|rk)-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|(?:AKIA|ASIA)[A-Z0-9]{16})\b"
)
# 一个参数里夹着的写法：``--token v`` / ``--token=v`` / ``API_KEY=v`` / ``X-Api-Key: v`` /
# ``Authorization: token v`` / ``Bearer v`` / ``scheme://user:pass@host``
_INLINE = (
    re.compile(rf"(?i)(--?[A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*?(?:^|[-_.]|\b)(?:{_SECRET_WORDS})(?:[-_.][A-Za-z0-9]+)*[ =]+)"
               r"(?!\[REDACTED\])([^\s\"']+)"),
    re.compile(rf"(?i)(\b[A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*?(?:^|[-_.]|\b)(?:{_SECRET_WORDS})(?:[-_.][A-Za-z0-9]+)*\s*[:=]\s*"
               r"(?:(?:bearer|token|basic)\s+)?+)(?!\[REDACTED\])([^\s\"']+)"),  # ?+：方案词不回吐成值
    re.compile(r"(?i)(\bbearer\s+)(?!\[REDACTED\])([^\s\"']+)"),
    re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]+:)(?!\[REDACTED\])([^\s/@]+)(?=@)"),
)
_HIDDEN = "[REDACTED]"


def _redact_inline(text: str) -> str:
    for pattern in _INLINE:
        text = pattern.sub(lambda match: match.group(1) + _HIDDEN, text)
    return _SECRET_VALUE.sub(_HIDDEN, text)


def _secret_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name.lstrip("-")))


def _redacted_argv(argv: list[str] | tuple[str, ...]) -> list[str]:
    out: list[str] = []
    hide_next = False
    for raw in argv:
        arg = str(raw)
        if hide_next and not arg.startswith("-"):
            out.append(_HIDDEN)
            hide_next = False
            continue
        hide_next = False
        name, sep, _value = arg.partition("=")
        if sep and " " not in name and _secret_name(name):
            out.append(f"{name}={_HIDDEN}")
            continue
        if arg.startswith("-") and " " not in arg and _secret_name(arg):
            out.append(arg)
            hide_next = True  # ``--token abc``：值在下一个参数
            continue
        out.append(_redact_inline(arg))
    return out


async def process_list(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    del task_id, execution_context
    max_entries = args.get("max_entries", 100)
    if not isinstance(max_entries, int) or isinstance(max_entries, bool):
        return _error("invalid_arguments", "max_entries must be an integer")
    max_entries = max(1, min(max_entries, 500))
    query = str(args.get("query", "")).casefold()
    rows: list[dict[str, Any]] = []
    for process in psutil.process_iter(
        attrs=["pid", "name", "exe", "cmdline", "create_time"]
    ):
        try:
            info = process.info
            command_line = _redacted_argv(info.get("cmdline") or [])
            searchable = " ".join(
                [
                    str(info.get("name") or ""),
                    str(info.get("exe") or ""),
                    " ".join(command_line),
                ]
            ).casefold()
            if query and query not in searchable:
                continue
            rows.append(
                {
                    "pid": info["pid"],
                    "name": info.get("name"),
                    "executable": info.get("exe"),
                    "command_line": command_line,
                    "creation_time": info.get("create_time"),
                }
            )
            if len(rows) >= max_entries:
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return _success(processes=rows, truncated=len(rows) >= max_entries)


async def process_start(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    if "command" in args or "shell" in args:
        return _error(
            "shell_string_rejected",
            "process_start accepts executable + argv only; use run_shell for shell syntax",
        )
    try:
        argv = _validate_argv(args.get("argv", []))
        context = legacy_execution_context(args, task_id, execution_context)
        result = await _PROCESS_SERVICE.start(
            executable=_resolve_executable(args.get("executable")),
            argv=argv,
            cwd=str(args["cwd"]) if args.get("cwd") is not None else None,
            environment=args.get("environment"),
            root_run_id=context.root_run_id or context.run_id or task_id or "legacy",
        )
        return _success(process=result)
    except (OSError, ValueError, RuntimeError) as exc:
        return _error("process_start_failed", str(exc))


async def process_wait(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    lease_id = args.get("lease_id")
    timeout = args.get("timeout_seconds", 0.0)
    if not isinstance(lease_id, str) or not lease_id:
        return _error("invalid_arguments", "lease_id is required")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        return _error("invalid_arguments", "timeout_seconds must be a number")
    timeout = max(0.0, min(float(timeout), 300.0))
    context = legacy_execution_context(args, task_id, execution_context)
    try:
        result = await _PROCESS_SERVICE.wait(
            lease_id,
            root_run_id=context.root_run_id or context.run_id or task_id or "legacy",
            timeout_seconds=timeout,
        )
        return _success(process=result)
    except KeyError as exc:
        return _error("process_lease_not_found", str(exc))
    except PermissionError as exc:
        return _error("process_identity_forbidden", str(exc))


async def process_stop(
    args: dict[str, Any],
    task_id: str = "",
    *,
    execution_context: ToolExecutionContext | None = None,
) -> str:
    context = legacy_execution_context(args, task_id, execution_context)
    root_run_id = context.root_run_id or context.run_id or task_id or "legacy"
    try:
        if isinstance(args.get("lease_id"), str) and args["lease_id"]:
            result = await _PROCESS_SERVICE.stop_owned(
                args["lease_id"], root_run_id=root_run_id
            )
        else:
            pid = args.get("pid")
            creation_time = args.get("creation_time")
            command_line = args.get("command_line")
            if (
                not isinstance(pid, int)
                or isinstance(pid, bool)
                or not isinstance(creation_time, (int, float))
                or isinstance(creation_time, bool)
                or not isinstance(command_line, list)
                or not all(isinstance(part, str) for part in command_line)
            ):
                return _error(
                    "invalid_arguments",
                    "provide a same-run lease_id or pid + creation_time + command_line",
                )
            captured = _capture_identity(pid)
            executable = captured.executable if captured is not None else ""
            result = await _PROCESS_SERVICE.stop_explicit(
                ProcessIdentity(
                    pid=pid,
                    creation_time=float(creation_time),
                    command_line=tuple(command_line),
                    executable=executable,
                )
            )
        return _success(cleanup=result)
    except KeyError as exc:
        return _error("process_lease_not_found", str(exc))
    except PermissionError as exc:
        return _error("process_identity_mismatch", str(exc))


def resolve_process_start_resources(args: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Deterministic §4.8 selector input for host authorization."""

    executable = _resolve_executable(args.get("executable"))
    return (
        {
            "kind": "process_executable",
            "executable": executable,
            "argv": list(_validate_argv(args.get("argv", []))),
        },
    )


def resolve_process_stop_resources(args: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    lease_id = args.get("lease_id")
    if isinstance(lease_id, str) and lease_id:
        return ({"kind": "process_lease", "lease_id": lease_id},)
    pid = args.get("pid")
    creation_time = args.get("creation_time")
    command_line = args.get("command_line")
    if (
        not isinstance(pid, int)
        or isinstance(pid, bool)
        or not isinstance(creation_time, (int, float))
        or isinstance(creation_time, bool)
        or not isinstance(command_line, list)
        or not all(isinstance(part, str) for part in command_line)
    ):
        raise ValueError(
            "process stop requires lease_id or pid + creation_time + command_line"
        )
    return (
        {
            "kind": "process_identity",
            "pid": pid,
            "creation_time": float(creation_time),
            "command_line": list(command_line),
        },
    )


__all__ = [
    "ProcessIdentity",
    "ProcessToolService",
    "get_process_tool_service",
    "process_list",
    "process_start",
    "process_stop",
    "process_wait",
    "resolve_process_start_resources",
    "resolve_process_stop_resources",
    "set_process_tool_service",
]
