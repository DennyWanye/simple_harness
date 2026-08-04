# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Process-isolated JSON protocol for local capability tools.

Process isolation is a reliability boundary, not a permission boundary.  A
generated tool is still treated as untrusted application code and therefore
cannot perform host effects directly; see ``tool_proxy.py`` and
``brokered_planner.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import psutil

from .contracts import fingerprint_json
from .runtime_prepare import PreparedRuntimeInstanceSpec

_PROTOCOL = "deskpet-json-tool-v1"
_ALLOWED_RESPONSE_KEYS = frozenset(
    {"ok", "request_id", "value", "effect_plan", "artifacts", "observations", "error"}
)
_HOST_ONLY_RESPONSE_KEYS = frozenset(
    {
        "receipt",
        "receipt_ref",
        "operation_id",
        "effect_id",
        "task_grant_id",
        "capability_failure_receipt",
        "deferred_signal",
    }
)


@dataclass(frozen=True, slots=True)
class LocalRuntimeRequest:
    """One immutable worker request."""

    tool: str
    args: Mapping[str, Any]
    root_run_id: str
    run_id: str
    effect_id: str
    workspace_roots: tuple[str, ...] = ()
    temp_dir: str = ""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    protocol: str = _PROTOCOL

    def to_payload(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "request_id": self.request_id,
            "tool": self.tool,
            "args": dict(self.args),
            "context": {
                "workspace_roots": list(self.workspace_roots),
                "temp_dir": self.temp_dir,
                "root_run_id": self.root_run_id,
                "run_id": self.run_id,
                "effect_id": self.effect_id,
            },
        }


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    creation_time: float
    command_line: tuple[str, ...]
    parent_pid: int | None
    private_memory_bytes: int


@dataclass(frozen=True, slots=True)
class ProcessCleanupReport:
    lease_id: str
    matched_pids: tuple[int, ...]
    stopped_pids: tuple[int, ...]
    remaining_pids: tuple[int, ...]
    released_private_memory_bytes: int

    @classmethod
    def empty(cls, lease_id: str) -> "ProcessCleanupReport":
        return cls(lease_id, (), (), (), 0)


@dataclass(frozen=True, slots=True)
class LocalRuntimeResult:
    """Classified worker result.

    ``status`` distinguishes a worker-declared failure from a malformed
    protocol response and from a runtime failure.  The host never trusts a
    worker-created receipt or operation identifier.
    """

    status: Literal["success", "failure", "malformed", "timeout", "crash", "cancelled"]
    request_id: str
    response: Mapping[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    exit_code: int | None = None
    stderr: str = ""
    stderr_truncated: bool = False
    lease_id: str = ""
    cleanup: ProcessCleanupReport | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


class _RuntimeLease:
    """Exact process identities observed below one runtime root."""

    def __init__(self, lease_id: str, root_run_id: str, root: ProcessIdentity) -> None:
        self.lease_id = lease_id
        self.root_run_id = root_run_id
        self.root_pid = root.pid
        self.identities: dict[int, ProcessIdentity] = {root.pid: root}

    def observe_tree(self) -> None:
        try:
            root = psutil.Process(self.root_pid)
            descendants = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            descendants = []
        for process in descendants:
            identity = _capture_identity(process)
            if identity is not None:
                self.identities[identity.pid] = identity

    def matching_processes(self) -> list[tuple[psutil.Process, ProcessIdentity]]:
        matched: list[tuple[psutil.Process, ProcessIdentity]] = []
        for identity in self.identities.values():
            try:
                process = psutil.Process(identity.pid)
            except psutil.NoSuchProcess:
                continue
            if _identity_matches(process, identity):
                matched.append((process, identity))
        return matched


def _private_memory(process: psutil.Process) -> int:
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
        full = process.memory_full_info()
        return int(getattr(full, "private", getattr(full, "uss", 0)))
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        return int(process.memory_info().rss)
    return 0


def _capture_identity(process: psutil.Process) -> ProcessIdentity | None:
    try:
        with process.oneshot():
            return ProcessIdentity(
                pid=process.pid,
                creation_time=float(process.create_time()),
                command_line=tuple(process.cmdline()),
                parent_pid=process.ppid(),
                private_memory_bytes=_private_memory(process),
            )
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


def _identity_matches(process: psutil.Process, expected: ProcessIdentity) -> bool:
    """Defend against PID reuse before touching a process."""

    try:
        if abs(float(process.create_time()) - expected.creation_time) > 0.01:
            return False
        return tuple(process.cmdline()) == expected.command_line
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _decode_bounded(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    selected = data[:limit]
    return selected.decode("utf-8", errors="replace"), truncated


def _parse_single_json(raw: bytes, *, limit: int) -> Mapping[str, Any]:
    if len(raw) > limit:
        raise ValueError(f"stdout exceeded {limit} bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("stdout is not valid UTF-8") from exc
    decoder = json.JSONDecoder()
    stripped = text.lstrip()
    if not stripped:
        raise ValueError("stdout was empty")
    try:
        value, offset = decoder.raw_decode(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"stdout is not JSON: {exc.msg}") from exc
    if stripped[offset:].strip():
        raise ValueError("stdout contained more than one JSON value or trailing data")
    if not isinstance(value, dict):
        raise ValueError("stdout JSON must be an object")
    return value


class LocalToolRuntime:
    """Run one local JSON tool per process and own its exact process lease."""

    def __init__(
        self,
        *,
        stdout_limit_bytes: int = 1024 * 1024,
        stderr_limit_bytes: int = 64 * 1024,
        default_timeout_seconds: float = 30.0,
        monitor_interval_seconds: float = 0.025,
        cleanup_grace_seconds: float = 1.0,
    ) -> None:
        if stdout_limit_bytes <= 0 or stderr_limit_bytes <= 0:
            raise ValueError("output limits must be positive")
        if default_timeout_seconds <= 0:
            raise ValueError("default timeout must be positive")
        self.stdout_limit_bytes = stdout_limit_bytes
        self.stderr_limit_bytes = stderr_limit_bytes
        self.default_timeout_seconds = default_timeout_seconds
        self.monitor_interval_seconds = monitor_interval_seconds
        self.cleanup_grace_seconds = cleanup_grace_seconds
        self._leases: dict[str, _RuntimeLease] = {}
        self._lease_lock = asyncio.Lock()

    @staticmethod
    def plan_prepared_instance(
        *,
        entry_id: str,
        ordinal: int,
        command: Sequence[str],
        cwd: str,
        health_envelope: Mapping[str, Any],
        adapter_fingerprint: str,
        env_scope: Mapping[str, str] | None = None,
        adapter_id: str = "managed-process-job-v1",
    ) -> PreparedRuntimeInstanceSpec:
        """Freeze a local launch without spawning, importing, or executing it."""

        argv = tuple(str(item) for item in command)
        if not argv or any(not item for item in argv):
            raise ValueError("prepared local runtime command is required")
        workdir = str(Path(cwd).expanduser().resolve(strict=False))
        environment = dict(sorted((env_scope or {}).items()))
        return PreparedRuntimeInstanceSpec(
            entry_id=entry_id,
            runtime_kind="local_runtime",
            adapter_id=adapter_id,
            adapter_fingerprint=adapter_fingerprint,
            start_envelope={
                "schema_version": 1,
                "argv": list(argv),
                "argv_hash": fingerprint_json(list(argv)),
                "cwd": workdir,
                "env_scope": environment,
                "env_scope_hash": fingerprint_json(environment),
            },
            health_envelope=dict(health_envelope),
            ordinal=ordinal,
        )

    async def execute(
        self,
        argv: Sequence[str],
        request: LocalRuntimeRequest,
        *,
        timeout_seconds: float | None = None,
        environment: Mapping[str, str] | None = None,
        cwd: str | None = None,
    ) -> LocalRuntimeResult:
        if not argv or not all(isinstance(part, str) and part for part in argv):
            raise ValueError("argv must be a non-empty sequence of non-empty strings")
        if isinstance(argv, (str, bytes)):
            raise TypeError("argv must not be a shell command string")
        if request.protocol != _PROTOCOL:
            raise ValueError(f"unsupported protocol: {request.protocol}")

        try:
            encoded = json.dumps(
                request.to_payload(), ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            return LocalRuntimeResult(
                status="malformed",
                request_id=request.request_id,
                error_code="local_tool_request_malformed",
                error_message=f"request is not JSON-serializable: {exc}",
            )
        timeout = (
            self.default_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        child_environment = dict(os.environ)
        if environment is not None:
            child_environment.update(environment)
        # The wire protocol is UTF-8 on every host. These variables make the
        # bundled Python template honor that contract on Windows code pages;
        # non-Python workers simply ignore them.
        child_environment["PYTHONUTF8"] = "1"
        child_environment["PYTHONIOENCODING"] = "utf-8"
        stdout_capture = tempfile.TemporaryFile(mode="w+b")
        stderr_capture = tempfile.TemporaryFile(mode="w+b")
        process_kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": stdout_capture,
            "stderr": stderr_capture,
            "cwd": cwd,
            "env": child_environment,
        }
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True

        try:
            process = await asyncio.create_subprocess_exec(
                *tuple(argv), **process_kwargs
            )
        except OSError as exc:
            stdout_capture.close()
            stderr_capture.close()
            return LocalRuntimeResult(
                status="crash",
                request_id=request.request_id,
                error_code="local_tool_spawn_failed",
                error_message=str(exc),
            )
        root_identity = _capture_identity(psutil.Process(process.pid))
        if root_identity is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            stdout_capture.close()
            stderr_capture.close()
            return LocalRuntimeResult(
                status="crash",
                request_id=request.request_id,
                error_code="runtime_identity_unavailable",
                error_message="could not capture worker process identity",
                exit_code=process.returncode,
            )

        lease_id = f"local-runtime:{request.root_run_id}:{request.request_id}"
        lease = _RuntimeLease(lease_id, request.root_run_id, root_identity)
        async with self._lease_lock:
            self._leases[lease_id] = lease
        monitor_stop = asyncio.Event()
        monitor = asyncio.create_task(self._monitor_lease(lease, monitor_stop))
        cleanup: ProcessCleanupReport | None = None

        try:
            communication = asyncio.create_task(process.communicate(input=encoded))
            try:
                deadline = asyncio.get_running_loop().time() + timeout
                limit_error: tuple[str, str] | None = None
                timed_out = False
                while not communication.done():
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        timed_out = True
                        break
                    await asyncio.wait(
                        {communication},
                        timeout=min(self.monitor_interval_seconds, remaining),
                    )
                    if communication.done():
                        break
                    stdout_size = os.fstat(stdout_capture.fileno()).st_size
                    stderr_size = os.fstat(stderr_capture.fileno()).st_size
                    if stdout_size > self.stdout_limit_bytes:
                        limit_error = (
                            "local_tool_stdout_limit_exceeded",
                            f"stdout exceeded {self.stdout_limit_bytes} bytes",
                        )
                        break
                    if stderr_size > self.stderr_limit_bytes:
                        limit_error = (
                            "local_tool_stderr_limit_exceeded",
                            f"stderr exceeded {self.stderr_limit_bytes} bytes",
                        )
                        break
                if timed_out or limit_error is not None:
                    lease.observe_tree()
                    cleanup = await self._cleanup_lease(lease)
                    with contextlib.suppress(Exception):
                        await communication
                    stdout_capture.seek(0)
                    stderr_capture.seek(0)
                    stderr_text, stderr_truncated = _decode_bounded(
                        stderr_capture.read(self.stderr_limit_bytes + 1),
                        self.stderr_limit_bytes,
                    )
                    if timed_out:
                        return LocalRuntimeResult(
                            status="timeout",
                            request_id=request.request_id,
                            error_code="local_tool_timeout",
                            error_message=f"local tool exceeded {timeout:.3f} seconds",
                            exit_code=process.returncode,
                            stderr=stderr_text,
                            stderr_truncated=stderr_truncated,
                            lease_id=lease_id,
                            cleanup=cleanup,
                        )
                    return LocalRuntimeResult(
                        status="malformed",
                        request_id=request.request_id,
                        error_code=limit_error[0],
                        error_message=limit_error[1],
                        exit_code=process.returncode,
                        stderr=stderr_text,
                        stderr_truncated=stderr_truncated,
                        lease_id=lease_id,
                        cleanup=cleanup,
                    )
                await communication
            except asyncio.CancelledError:
                lease.observe_tree()
                await self._cleanup_lease(lease)
                communication.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await communication
                raise

            stdout_capture.seek(0)
            stderr_capture.seek(0)
            stdout = stdout_capture.read(self.stdout_limit_bytes + 1)
            stderr = stderr_capture.read(self.stderr_limit_bytes + 1)
            lease.observe_tree()
            stderr_text, stderr_truncated = _decode_bounded(
                stderr, self.stderr_limit_bytes
            )
            # A well-behaved one-shot worker leaves no descendants.  Clean up
            # any survivor using only identities observed on this lease.
            cleanup = await self._cleanup_lease(lease, include_root=False)

            if process.returncode != 0:
                response: Mapping[str, Any] | None = None
                with contextlib.suppress(ValueError):
                    parsed = _parse_single_json(
                        stdout, limit=self.stdout_limit_bytes
                    )
                    self._validate_response(parsed, request)
                    response = parsed
                return LocalRuntimeResult(
                    status="crash",
                    request_id=request.request_id,
                    response=response,
                    error_code="local_tool_process_failed",
                    error_message=f"local tool exited with code {process.returncode}",
                    exit_code=process.returncode,
                    stderr=stderr_text,
                    stderr_truncated=stderr_truncated,
                    lease_id=lease_id,
                    cleanup=cleanup,
                )

            try:
                response = _parse_single_json(stdout, limit=self.stdout_limit_bytes)
                self._validate_response(response, request)
            except ValueError as exc:
                return LocalRuntimeResult(
                    status="malformed",
                    request_id=request.request_id,
                    error_code="local_tool_protocol_malformed",
                    error_message=str(exc),
                    exit_code=process.returncode,
                    stderr=stderr_text,
                    stderr_truncated=stderr_truncated,
                    lease_id=lease_id,
                    cleanup=cleanup,
                )

            if response["ok"] is False:
                error = response.get("error")
                if not isinstance(error, Mapping):
                    return LocalRuntimeResult(
                        status="malformed",
                        request_id=request.request_id,
                        error_code="local_tool_protocol_malformed",
                        error_message="worker failure must include an error object",
                        exit_code=process.returncode,
                        stderr=stderr_text,
                        stderr_truncated=stderr_truncated,
                        lease_id=lease_id,
                        cleanup=cleanup,
                    )
                return LocalRuntimeResult(
                    status="failure",
                    request_id=request.request_id,
                    response=response,
                    error_code=str(error.get("code", "local_tool_failed")),
                    error_message=str(error.get("message", "local tool failed")),
                    exit_code=process.returncode,
                    stderr=stderr_text,
                    stderr_truncated=stderr_truncated,
                    lease_id=lease_id,
                    cleanup=cleanup,
                )
            return LocalRuntimeResult(
                status="success",
                request_id=request.request_id,
                response=response,
                exit_code=process.returncode,
                stderr=stderr_text,
                stderr_truncated=stderr_truncated,
                lease_id=lease_id,
                cleanup=cleanup,
            )
        finally:
            monitor_stop.set()
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
            async with self._lease_lock:
                self._leases.pop(lease_id, None)
            stdout_capture.close()
            stderr_capture.close()

    async def close(self) -> tuple[ProcessCleanupReport, ...]:
        """Release all active runtime leases, e.g. during backend shutdown."""

        async with self._lease_lock:
            leases = tuple(self._leases.values())
        reports = []
        for lease in leases:
            lease.observe_tree()
            reports.append(await self._cleanup_lease(lease))
        return tuple(reports)

    async def cancel_root_run(
        self, root_run_id: str
    ) -> tuple[ProcessCleanupReport, ...]:
        async with self._lease_lock:
            leases = tuple(
                lease for lease in self._leases.values() if lease.root_run_id == root_run_id
            )
        reports = []
        for lease in leases:
            lease.observe_tree()
            reports.append(await self._cleanup_lease(lease))
        return tuple(reports)

    async def _monitor_lease(
        self, lease: _RuntimeLease, stop: asyncio.Event
    ) -> None:
        while not stop.is_set():
            lease.observe_tree()
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.monitor_interval_seconds
                )
            except asyncio.TimeoutError:
                continue

    async def _cleanup_lease(
        self, lease: _RuntimeLease, *, include_root: bool = True
    ) -> ProcessCleanupReport:
        lease.observe_tree()
        matched = lease.matching_processes()
        if not include_root:
            matched = [entry for entry in matched if entry[0].pid != lease.root_pid]
        if not matched:
            return ProcessCleanupReport.empty(lease.lease_id)

        # Children first.  The observed parent links are immutable evidence;
        # no name-based search or broad image kill is ever used.
        depth: dict[int, int] = {}

        def identity_depth(identity: ProcessIdentity) -> int:
            if identity.pid in depth:
                return depth[identity.pid]
            current = identity
            seen: set[int] = set()
            value = 0
            while current.parent_pid in lease.identities and current.parent_pid not in seen:
                seen.add(current.pid)
                value += 1
                current = lease.identities[current.parent_pid]  # type: ignore[index]
            depth[identity.pid] = value
            return value

        matched.sort(key=lambda item: identity_depth(item[1]), reverse=True)
        matched_pids = tuple(process.pid for process, _ in matched)
        before_memory = sum(_private_memory(process) for process, _ in matched)
        for process, identity in matched:
            if not _identity_matches(process, identity):
                continue
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                process.terminate()
        _, alive = psutil.wait_procs(
            [process for process, _ in matched], timeout=self.cleanup_grace_seconds
        )
        for process in alive:
            identity = lease.identities.get(process.pid)
            if identity is None or not _identity_matches(process, identity):
                continue
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                process.kill()
        _, alive_after_kill = psutil.wait_procs(
            alive, timeout=self.cleanup_grace_seconds
        )
        remaining = tuple(
            process.pid
            for process in alive_after_kill
            if (
                (identity := lease.identities.get(process.pid)) is not None
                and _identity_matches(process, identity)
            )
        )
        stopped = tuple(pid for pid in matched_pids if pid not in remaining)
        after_memory = 0
        for process in alive_after_kill:
            identity = lease.identities.get(process.pid)
            if identity is not None and _identity_matches(process, identity):
                after_memory += _private_memory(process)
        return ProcessCleanupReport(
            lease_id=lease.lease_id,
            matched_pids=matched_pids,
            stopped_pids=stopped,
            remaining_pids=remaining,
            released_private_memory_bytes=max(0, before_memory - after_memory),
        )

    @staticmethod
    def _validate_response(
        response: Mapping[str, Any], request: LocalRuntimeRequest
    ) -> None:
        unknown = set(response) - _ALLOWED_RESPONSE_KEYS
        if unknown:
            raise ValueError(f"response contains unsupported keys: {sorted(unknown)}")
        forbidden = set(response) & _HOST_ONLY_RESPONSE_KEYS
        if forbidden:
            raise ValueError(f"response attempted host-only fields: {sorted(forbidden)}")
        if response.get("request_id") != request.request_id:
            raise ValueError("response request_id does not match request")
        if not isinstance(response.get("ok"), bool):
            raise ValueError("response ok must be a boolean")
        for list_key in ("artifacts", "observations"):
            if list_key in response and not isinstance(response[list_key], list):
                raise ValueError(f"response {list_key} must be an array")
