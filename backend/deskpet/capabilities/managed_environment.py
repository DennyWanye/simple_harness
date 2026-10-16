"""托管运行环境准备：按能力包声明检查并准备解释器、依赖与版本（从 platform 下沉）。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from deskpet.capabilities.contracts import JsonValue, fingerprint_json
from deskpet.capabilities.local_runtime import (
    LocalRuntimeRequest,
    LocalRuntimeResult,
    LocalToolRuntime,
)
from deskpet.capabilities.manager import CapabilityManagerError, CapabilityOperationRecord
from deskpet.capabilities.manifest import (
    CommandDependency,
    PackValidationResult,
    ToolEntry,
    windows_extended_path,
)
from deskpet.capabilities.runtime_prepare import PreparedRuntimeInstanceSpec

CommandFinder = Callable[[str], str | None]


def _default_command_finder(name: str) -> str | None:
    environment_name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper() + "_EXECUTABLE"
    configured = os.environ.get(environment_name)
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve(strict=True))
    candidates = [name]
    if name.casefold() == "godot":
        candidates.append("godot4")
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return str(Path(found).resolve(strict=True))
    return None


def _extract_version(value: str) -> tuple[int, ...] | None:
    match = re.search(r"(?<!\d)(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())


def _version_satisfies(actual: tuple[int, ...] | None, constraint: str) -> bool | None:
    if actual is None:
        return None
    match = re.fullmatch(r"\s*(>=|>|<=|<|==)?\s*(\d+(?:\.\d+){0,2})\s*", constraint)
    if match is None:
        return None
    expected = tuple(int(part) for part in match.group(2).split("."))
    width = max(len(actual), len(expected))
    left = actual + (0,) * (width - len(actual))
    right = expected + (0,) * (width - len(expected))
    operator = match.group(1) or "=="
    return {
        ">=": left >= right,
        ">": left > right,
        "<=": left <= right,
        "<": left < right,
        "==": left == right,
    }[operator]


class ManagedEnvironmentPreparer:
    """Probe dependencies and run declared healthchecks out of process."""

    def __init__(
        self,
        *,
        runtime: LocalToolRuntime,
        python_executable: str | Path | None = None,
        command_finder: CommandFinder | None = None,
        healthcheck_timeout_seconds: float = 15.0,
        command_probe_timeout_seconds: float = 5.0,
    ) -> None:
        if healthcheck_timeout_seconds <= 0 or command_probe_timeout_seconds <= 0:
            raise ValueError("probe timeouts must be positive")
        self.runtime = runtime
        self.python_executable = str(python_executable or sys.executable)
        self.command_finder = command_finder or _default_command_finder
        self.healthcheck_timeout_seconds = float(healthcheck_timeout_seconds)
        self.command_probe_timeout_seconds = float(command_probe_timeout_seconds)

    def materialize_static(
        self,
        validation: PackValidationResult,
        *,
        environment_root: Path,
        operation_id: str,
    ) -> Mapping[str, JsonValue]:
        """Materialize host-only state without starting or probing a runtime.

        Command lookup is captured here so the later prepared runtime set owns
        an immutable executable path.  Version probes and tool healthchecks are
        deliberately planned separately and must pass through the managed
        start-ACK protocol.
        """

        windows_extended_path(environment_root).mkdir(parents=True, exist_ok=True)
        command_dependencies: list[dict[str, JsonValue]] = []
        for dependency in validation.manifest.dependencies.commands:
            detector = self._has_detector(validation, dependency.name)
            resolved = self.command_finder(dependency.name)
            command_dependencies.append(
                {
                    "name": dependency.name,
                    "required_version": dependency.version,
                    "blocking": not detector,
                    "resolved_path": resolved,
                    "status": (
                        "planned"
                        if resolved is not None
                        else ("missing_nonblocking_detector" if detector else "missing")
                    ),
                }
            )
            if resolved is None and not detector:
                raise CapabilityManagerError(
                    "command_dependency_unavailable",
                    f"{dependency.name} {dependency.version}: missing",
                )

        return {
            "kind": "managed-local-static-v1",
            "operation_id": operation_id,
            "path": str(environment_root.resolve(strict=False)),
            "command_dependencies": command_dependencies,
            "python_dependencies": [
                {
                    "requirement": dependency.requirement,
                    "status": "declared_host_environment",
                }
                for dependency in validation.manifest.dependencies.python
            ],
        }

    def plan_executable_checks(
        self,
        validation: PackValidationResult,
        *,
        materialization: Mapping[str, JsonValue],
        operation_id: str,
        adapter_fingerprint: str,
    ) -> tuple[PreparedRuntimeInstanceSpec, ...]:
        """Freeze dependency probes and healthchecks without executing them."""

        raw_dependencies = materialization.get("command_dependencies")
        if not isinstance(raw_dependencies, list):
            raise CapabilityManagerError(
                "invalid_static_materialization",
                "command_dependencies must be a list",
            )
        dependencies_by_name: dict[str, Mapping[str, JsonValue]] = {}
        for item in raw_dependencies:
            if not isinstance(item, Mapping):
                raise CapabilityManagerError(
                    "invalid_static_materialization",
                    "command dependency evidence must be an object",
                )
            name = str(item.get("name") or "")
            if not name or name in dependencies_by_name:
                raise CapabilityManagerError(
                    "invalid_static_materialization",
                    "command dependency names must be non-empty and unique",
                )
            dependencies_by_name[name] = item

        instances: list[PreparedRuntimeInstanceSpec] = []
        for dependency in validation.manifest.dependencies.commands:
            evidence = dependencies_by_name.get(dependency.name)
            if evidence is None:
                raise CapabilityManagerError(
                    "invalid_static_materialization",
                    f"missing command dependency evidence: {dependency.name}",
                )
            resolved_path = str(evidence.get("resolved_path") or "")
            if not resolved_path:
                # Missing non-blocking detector dependencies remain visible in
                # static evidence but cannot authorize a process launch.
                continue
            ordinal = len(instances)
            argv = [resolved_path, "--version"]
            instances.append(
                PreparedRuntimeInstanceSpec(
                    entry_id=f"dependency-probe:{dependency.name}",
                    runtime_kind="dependency_probe",
                    adapter_id="managed-process-job-v1",
                    adapter_fingerprint=adapter_fingerprint,
                    start_envelope={
                        "schema_version": 1,
                        "argv": argv,
                        "argv_hash": fingerprint_json(argv),
                        "cwd": str(validation.root.resolve(strict=True)),
                        "operation_id": operation_id,
                    },
                    health_envelope={
                        "kind": "command-version-v1",
                        "required_version": dependency.version,
                        "timeout_seconds": self.command_probe_timeout_seconds,
                        "nonblocking_detector": self._has_detector(validation, dependency.name),
                    },
                    ordinal=ordinal,
                )
            )

        checked: set[tuple[str, str]] = set()
        for tool in validation.manifest.tools:
            key = (tool.entry, tool.healthcheck)
            if key in checked:
                continue
            checked.add(key)
            entry_path = str((validation.root / tool.entry).resolve(strict=True))
            ordinal = len(instances)
            argv = [self.python_executable, entry_path]
            instances.append(
                PreparedRuntimeInstanceSpec(
                    entry_id=(f"tool-health:{fingerprint_json([tool.entry, tool.healthcheck])}"),
                    runtime_kind="tool_health",
                    adapter_id="managed-process-job-v1",
                    adapter_fingerprint=adapter_fingerprint,
                    start_envelope={
                        "schema_version": 1,
                        "argv": argv,
                        "argv_hash": fingerprint_json(argv),
                        "cwd": str(validation.root.resolve(strict=True)),
                        "operation_id": operation_id,
                    },
                    health_envelope={
                        "kind": "deskpet-json-tool-health-v1",
                        "protocol": "deskpet-json-tool-v1",
                        "tool": tool.healthcheck,
                        "timeout_seconds": self.healthcheck_timeout_seconds,
                    },
                    ordinal=ordinal,
                )
            )
        return tuple(instances)

    @staticmethod
    def _has_detector(validation: PackValidationResult, dependency: str) -> bool:
        normalized = dependency.casefold()
        return any(
            tool.id.casefold() == "detect"
            and (
                normalized in tool.provider_name.casefold()
                or normalized in validation.manifest.id.casefold()
            )
            for tool in validation.manifest.tools
        )

    @staticmethod
    async def _terminate_probe(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.communicate()
            except OSError:
                process.kill()
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            if process.returncode is None:
                if os.name == "nt":
                    process.kill()
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
            await process.wait()

    async def _probe_command(
        self,
        dependency: CommandDependency,
        *,
        nonblocking_detector: bool,
    ) -> dict[str, JsonValue]:
        resolved = self.command_finder(dependency.name)
        evidence: dict[str, JsonValue] = {
            "name": dependency.name,
            "required_version": dependency.version,
            "blocking": not nonblocking_detector,
            "resolved_path": resolved,
        }
        if resolved is None:
            evidence["status"] = (
                "missing_nonblocking_detector" if nonblocking_detector else "missing"
            )
            return evidence
        process_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_kwargs["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(
                resolved,
                "--version",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **process_kwargs,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.command_probe_timeout_seconds
            )
        except asyncio.CancelledError:
            if "process" in locals() and process.returncode is None:
                await asyncio.shield(self._terminate_probe(process))
            raise
        except (OSError, TimeoutError) as exc:
            if "process" in locals() and process.returncode is None:
                await self._terminate_probe(process)
            evidence.update(
                {
                    "status": (
                        "probe_failed_nonblocking_detector"
                        if nonblocking_detector
                        else "probe_failed"
                    ),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return evidence
        output = (
            stdout.decode("utf-8", errors="replace").strip()
            or stderr.decode("utf-8", errors="replace").strip()
        )[:1000]
        actual = _extract_version(output)
        satisfies = _version_satisfies(actual, dependency.version)
        status = "available"
        if process.returncode != 0 or satisfies is False:
            status = "incompatible_nonblocking_detector" if nonblocking_detector else "incompatible"
        evidence.update(
            {
                "status": status,
                "exit_code": int(process.returncode or 0),
                "version_output": output,
                "version_satisfies": satisfies,
            }
        )
        return evidence

    async def _run_healthcheck(
        self,
        validation: PackValidationResult,
        tool: ToolEntry,
        *,
        operation_id: str,
    ) -> tuple[LocalRuntimeResult, dict[str, JsonValue]]:
        if tool.runtime != "deskpet-json-tool-v1":
            raise CapabilityManagerError(
                "unsupported_healthcheck_runtime",
                f"cannot healthcheck runtime {tool.runtime}",
            )
        request = LocalRuntimeRequest(
            tool=tool.healthcheck,
            args={},
            root_run_id=f"capability-healthcheck:{operation_id}",
            run_id=f"capability-healthcheck:{operation_id}",
            effect_id=f"healthcheck:{tool.id}",
        )
        result = await self.runtime.execute(
            (
                self.python_executable,
                str((validation.root / tool.entry).resolve(strict=True)),
            ),
            request,
            timeout_seconds=self.healthcheck_timeout_seconds,
            cwd=str(validation.root),
        )
        evidence: dict[str, JsonValue] = {
            "entry": tool.entry,
            "healthcheck": tool.healthcheck,
            "tool_ids": [tool.id],
            "status": result.status,
            "request_id": result.request_id,
            "exit_code": result.exit_code,
            "lease_id": result.lease_id,
            "cleanup_remaining_pids": (
                [] if result.cleanup is None else list(result.cleanup.remaining_pids)
            ),
        }
        if result.response is not None and result.status == "success":
            evidence["value"] = result.response.get("value")
        if result.error_code:
            evidence["error_code"] = result.error_code
        if result.error_message:
            evidence["error_message"] = result.error_message
        return result, evidence

    async def prepare(
        self,
        validation: PackValidationResult,
        *,
        environment_root: Path,
        operation_id: str,
    ) -> Mapping[str, JsonValue]:
        windows_extended_path(environment_root).mkdir(parents=True, exist_ok=True)
        command_evidence: list[dict[str, JsonValue]] = []
        for dependency in validation.manifest.dependencies.commands:
            detector = self._has_detector(validation, dependency.name)
            evidence = await self._probe_command(dependency, nonblocking_detector=detector)
            command_evidence.append(evidence)
            if evidence["status"] not in {
                "available",
                "missing_nonblocking_detector",
                "probe_failed_nonblocking_detector",
                "incompatible_nonblocking_detector",
            }:
                raise CapabilityManagerError(
                    "command_dependency_unavailable",
                    f"{dependency.name} {dependency.version}: {evidence['status']}",
                )

        healthchecks: list[dict[str, JsonValue]] = []
        checked: dict[tuple[str, str], int] = {}
        for tool in validation.manifest.tools:
            key = (tool.entry, tool.healthcheck)
            existing = checked.get(key)
            if existing is not None:
                tool_ids = healthchecks[existing]["tool_ids"]
                if isinstance(tool_ids, list):
                    tool_ids.append(tool.id)
                continue
            result, evidence = await self._run_healthcheck(
                validation, tool, operation_id=operation_id
            )
            checked[key] = len(healthchecks)
            healthchecks.append(evidence)
            if not result.ok:
                raise CapabilityManagerError(
                    "capability_healthcheck_failed",
                    (
                        f"{tool.entry}:{tool.healthcheck} returned {result.status}: "
                        f"{result.error_code or result.error_message or 'unknown error'}"
                    ),
                )

        return {
            "kind": "managed-local-v1",
            "path": str(environment_root.resolve(strict=False)),
            "command_dependencies": command_evidence,
            "python_dependencies": [
                {
                    "requirement": dependency.requirement,
                    "status": "declared_host_environment",
                }
                for dependency in validation.manifest.dependencies.python
            ],
            "healthchecks": healthchecks,
        }

    async def probe(
        self,
        record: CapabilityOperationRecord,
        *,
        environment_root: Path | None,
    ) -> Mapping[str, JsonValue]:
        return {
            "kind": "managed-local-v1",
            "operation_id": record.operation_id,
            "exists": bool(environment_root and environment_root.is_dir()),
            "path": None if environment_root is None else str(environment_root),
        }
