# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""DeskPet's first-party Godot 4 adapter.

The process implements one request per invocation of ``deskpet-json-tool-v1``.
It deliberately exposes only application-specific, read-mostly operations:

* ``detect`` finds a Godot executable and reports its real version/source.
* ``project_check`` asks Godot itself to load a project in headless editor mode.

Project authoring, downloads, process lifetime, and GUI interaction remain owned
by DeskPet's generic tools.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


PROTOCOL_VERSION = "deskpet-json-tool-v1"
PACK_VERSION = "1.0.2"
MAX_REQUEST_BYTES = 1_048_576
MAX_CAPTURE_CHARS = 32_768
MIN_GODOT_MAJOR = 4
DEFAULT_TIMEOUT_SECONDS = 90
MIN_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 300

ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]

_FAIL_ONCE_CASE_ID = "UA-GODOT-FAIL-ONCE"


class ToolError(RuntimeError):
    """A stable, user-actionable adapter failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})


@dataclass(frozen=True)
class Candidate:
    path: Path
    source: str


def _run_process(
    argv: Sequence[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    _raise_dev_fail_once_at_launch(argv)
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )


def _raise_dev_fail_once_at_launch(argv: Sequence[str]) -> None:
    """Arm one durable, dev-only failure at the real Godot launch boundary.

    The fixture is intentionally impossible to enable accidentally: both dev
    mode and the exact acceptance case id are required, and state must live in
    an explicitly isolated DeskPet user-data directory.  The marker survives a
    backend restart, so recovery cannot consume the same failure twice.
    """

    if os.environ.get("DESKPET_DEV_MODE") != "1":
        return
    if (
        os.environ.get("DESKPET_CAPABILITY_E2E_CASE_ID", "").strip()
        != _FAIL_ONCE_CASE_ID
    ):
        return
    if "--headless" not in argv:
        return
    user_data_raw = os.environ.get("DESKPET_USER_DATA_DIR", "").strip()
    if not user_data_raw:
        return
    state_dir = (
        Path(user_data_raw).expanduser().resolve(strict=False)
        / "acceptance-fixtures"
        / "ua-godot-fail-once"
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / "headless-launch-consumed.json"
    payload = json.dumps(
        {
            "case_id": _FAIL_ONCE_CASE_ID,
            "failure": "godot_executable_not_found",
            "launch_kind": "headless_project_check",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    try:
        descriptor = os.open(
            marker,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        return
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
        stream.write("\n")
    executable = str(argv[0]) if argv else "godot"
    raise FileNotFoundError(
        2,
        "dev acceptance fixture consumed the first Godot headless launch",
        executable,
    )


def _canonical_path(raw: str) -> Path:
    value = raw.strip()
    if not value:
        raise ToolError("invalid_path", "路径不能为空。")
    return Path(value).expanduser().resolve(strict=False)


def _append_candidate(
    candidates: list[Candidate],
    seen: set[str],
    raw_path: str | os.PathLike[str] | None,
    source: str,
) -> None:
    if raw_path is None:
        return
    try:
        path = Path(raw_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return
    key = os.path.normcase(str(path))
    if key in seen:
        return
    seen.add(key)
    candidates.append(Candidate(path=path, source=source))


def _standard_windows_candidates() -> list[Candidate]:
    if os.name != "nt":
        return []

    candidates: list[Candidate] = []
    seen: set[str] = set()
    roots: list[tuple[Path, str]] = []
    for env_name, source in (
        ("ProgramFiles", "system_install"),
        ("ProgramFiles(x86)", "system_install"),
        ("LOCALAPPDATA", "current_user_install"),
    ):
        value = os.environ.get(env_name)
        if value:
            roots.append((Path(value), source))

    for root, source in roots:
        for path in (
            root / "Godot" / "Godot.exe",
            root / "Godot Engine" / "Godot.exe",
        ):
            _append_candidate(candidates, seen, path, source)

        # Official portable builds commonly retain the version in the filename.
        # Keep discovery bounded to the application root; never scan the drive.
        godot_root = root / "Godot"
        if godot_root.is_dir():
            for path in sorted(godot_root.glob("Godot_v4*_win64*.exe")):
                _append_candidate(candidates, seen, path, "portable_install")

    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if program_files_x86:
        steam_root = (
            Path(program_files_x86)
            / "Steam"
            / "steamapps"
            / "common"
            / "Godot Engine"
        )
        for name in ("godot.windows.editor.x86_64.exe", "Godot.exe"):
            _append_candidate(candidates, seen, steam_root / name, "steam")
    return candidates


def _candidate_executables(preferred: str | None) -> list[Candidate]:
    candidates: list[Candidate] = []
    seen: set[str] = set()
    _append_candidate(candidates, seen, preferred, "request")
    _append_candidate(
        candidates,
        seen,
        os.environ.get("GODOT_EXECUTABLE"),
        "configured_environment",
    )
    for name in ("godot4", "godot"):
        _append_candidate(candidates, seen, shutil.which(name), "path")
    for candidate in _standard_windows_candidates():
        _append_candidate(candidates, seen, candidate.path, candidate.source)
    return candidates


_VERSION_RE = re.compile(
    r"(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?"
)


def _version_for(
    candidate: Candidate,
    *,
    runner: ProcessRunner,
) -> dict[str, Any]:
    try:
        completed = runner([str(candidate.path), "--version"], timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "godot_version_timeout",
            "Godot 版本探测超时。",
            details={"executable": str(candidate.path), "timeout_seconds": 15},
        ) from exc
    except OSError as exc:
        raise ToolError(
            "godot_executable_unavailable",
            "无法启动候选 Godot 可执行文件。",
            details={"executable": str(candidate.path), "reason": str(exc)},
        ) from exc

    raw = (completed.stdout or completed.stderr or "").strip()
    first_line = raw.splitlines()[0].strip() if raw else ""
    match = _VERSION_RE.search(first_line)
    if completed.returncode != 0 or match is None:
        raise ToolError(
            "godot_version_unrecognized",
            "候选程序没有返回可识别的 Godot 版本。",
            details={
                "executable": str(candidate.path),
                "exit_code": completed.returncode,
                "version_output": first_line[:500],
            },
        )

    major = int(match.group("major"))
    minor = int(match.group("minor"))
    patch = int(match.group("patch") or 0)
    return {
        "version": f"{major}.{minor}.{patch}",
        "version_raw": first_line,
        "major": major,
        "minor": minor,
        "patch": patch,
        "compatible": major >= MIN_GODOT_MAJOR,
    }


def detect_godot(
    arguments: Mapping[str, Any],
    *,
    runner: ProcessRunner = _run_process,
) -> dict[str, Any]:
    preferred_raw = arguments.get("preferred_executable")
    preferred = str(preferred_raw) if preferred_raw is not None else None
    candidates = _candidate_executables(preferred)
    inspected: list[dict[str, Any]] = []
    detected: list[dict[str, Any]] = []

    for candidate in candidates:
        if not candidate.path.is_file():
            inspected.append(
                {
                    "executable": str(candidate.path),
                    "source": candidate.source,
                    "status": "missing",
                }
            )
            continue
        try:
            version = _version_for(candidate, runner=runner)
        except ToolError as exc:
            inspected.append(
                {
                    "executable": str(candidate.path),
                    "source": candidate.source,
                    "status": "invalid",
                    "error_code": exc.code,
                }
            )
            continue
        detected.append(
            {
                "executable": str(candidate.path),
                "source": candidate.source,
                **version,
            }
        )

    chosen = next(
        (candidate for candidate in detected if candidate["compatible"]),
        detected[0] if detected else None,
    )
    if chosen is None:
        return {
            "found": False,
            "compatible": False,
            "minimum_version": f"{MIN_GODOT_MAJOR}.0",
            "reason": "godot_executable_not_found",
            "inspected": inspected,
        }
    return {
        "found": True,
        "minimum_version": f"{MIN_GODOT_MAJOR}.0",
        **chosen,
        "alternatives": [
            candidate
            for candidate in detected
            if candidate["executable"] != chosen["executable"]
        ],
        "inspected": inspected,
    }


_PROJECT_ERROR_MARKERS = (
    "SCRIPT ERROR:",
    "Parse Error:",
    "Parser Error:",
    "ERROR:",
    "Failed loading resource",
    "Cannot open file",
)


def _diagnostic_lines(stdout: str, stderr: str) -> list[str]:
    diagnostics: list[str] = []
    for line in (stdout + "\n" + stderr).splitlines():
        cleaned = line.strip()
        if cleaned and any(marker.casefold() in cleaned.casefold() for marker in _PROJECT_ERROR_MARKERS):
            diagnostics.append(cleaned[:2_000])
        if len(diagnostics) >= 50:
            break
    return diagnostics


def project_check(
    arguments: Mapping[str, Any],
    *,
    runner: ProcessRunner = _run_process,
) -> dict[str, Any]:
    project_raw = arguments.get("project_path")
    if not isinstance(project_raw, str):
        raise ToolError("project_path_required", "project_path 必须是字符串。")
    project_path = _canonical_path(project_raw)
    if not project_path.is_dir():
        raise ToolError(
            "godot_project_not_found",
            "Godot 项目目录不存在。",
            details={"project_path": str(project_path)},
        )
    project_file = project_path / "project.godot"
    if not project_file.is_file():
        raise ToolError(
            "godot_project_file_missing",
            "目录中缺少 project.godot。",
            details={"project_path": str(project_path)},
        )

    timeout_raw = arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, (int, float)):
        raise ToolError("invalid_timeout", "timeout_seconds 必须是数字。")
    timeout = int(timeout_raw)
    if not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ToolError(
            "invalid_timeout",
            f"timeout_seconds 必须在 {MIN_TIMEOUT_SECONDS} 到 {MAX_TIMEOUT_SECONDS} 之间。",
        )

    preferred = arguments.get("godot_executable")
    detection = detect_godot(
        {
            "preferred_executable": str(preferred)
            if preferred is not None
            else None
        },
        runner=runner,
    )
    if not detection["found"]:
        raise ToolError(
            "godot_executable_not_found",
            "未找到 Godot。请先通过可信官方来源准备 Godot 4。",
            details={"minimum_version": detection["minimum_version"]},
        )
    if not detection["compatible"]:
        raise ToolError(
            "godot_version_incompatible",
            "项目检查需要 Godot 4 或更高版本。",
            details={
                "executable": detection["executable"],
                "version": detection["version"],
                "minimum_version": detection["minimum_version"],
            },
        )

    argv = [
        str(detection["executable"]),
        "--headless",
        "--editor",
        "--path",
        str(project_path),
        "--quit",
    ]
    started = time.monotonic()
    try:
        completed = runner(argv, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "godot_project_check_timeout",
            "Godot headless 项目检查超时。",
            details={
                "project_path": str(project_path),
                "timeout_seconds": timeout,
            },
        ) from exc
    except OSError as exc:
        missing = isinstance(exc, FileNotFoundError)
        raise ToolError(
            (
                "godot_executable_not_found"
                if missing
                else "godot_project_check_launch_failed"
            ),
            (
                "Godot 可执行文件在启动时不可用。请重新探测可用安装后重试。"
                if missing
                else "无法启动 Godot headless 项目检查。"
            ),
            details={
                "reason": str(exc),
                "retry_strategy": (
                    "call godot__detect, then retry godot__project_check "
                    "with the detected executable"
                ),
            },
        ) from exc

    stdout = (completed.stdout or "")[:MAX_CAPTURE_CHARS]
    stderr = (completed.stderr or "")[:MAX_CAPTURE_CHARS]
    diagnostics = _diagnostic_lines(stdout, stderr)
    valid = completed.returncode == 0 and not diagnostics
    return {
        "valid": valid,
        "error_code": None if valid else "godot_project_invalid",
        "project_path": str(project_path),
        "project_file": str(project_file),
        "executable": detection["executable"],
        "version": detection["version"],
        "source": detection["source"],
        "exit_code": completed.returncode,
        "duration_ms": round((time.monotonic() - started) * 1_000),
        "diagnostics": diagnostics,
        "stdout": stdout,
        "stderr": stderr,
    }


def healthcheck() -> dict[str, Any]:
    return {
        "healthy": True,
        "protocol": PROTOCOL_VERSION,
        "pack_version": PACK_VERSION,
        "tools": ["detect", "project_check"],
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


def _arguments_from(request: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = request.get("arguments", request.get("args", {}))
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ToolError("invalid_arguments", "arguments 必须是 JSON object。")
    return raw


def handle_request(request: Mapping[str, Any]) -> dict[str, Any]:
    request_id = str(request.get("request_id") or request.get("id") or "")
    tool = str(request.get("tool") or request.get("tool_name") or "").strip()
    try:
        if request.get("protocol") != PROTOCOL_VERSION:
            raise ToolError(
                "unsupported_protocol",
                f"protocol 必须是 {PROTOCOL_VERSION}。",
            )
        arguments = _arguments_from(request)
        if tool in {"healthcheck", "__healthcheck__"}:
            result = healthcheck()
        elif tool in {"detect", "godot.detect", "godot__detect"}:
            result = detect_godot(arguments)
        elif tool in {
            "project_check",
            "godot.project_check",
            "godot__project_check",
        }:
            result = project_check(arguments)
        else:
            raise ToolError(
                "unknown_tool",
                "未知 Godot 工具。",
                details={"tool": tool},
            )
        return {
            "request_id": request_id,
            "ok": True,
            "value": result,
            "artifacts": [],
            "observations": [],
        }
    except ToolError as exc:
        return {
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        }
    except Exception as exc:  # pragma: no cover - last-resort process boundary
        return {
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": "godot_adapter_internal_error",
                "message": "Godot adapter 发生未预期错误。",
                "details": {"exception_type": type(exc).__name__},
            },
        }


def _read_request() -> Mapping[str, Any]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ToolError("request_too_large", "JSON tool request 超过 1 MiB。")
    if not raw.strip():
        raise ToolError("empty_request", "stdin 中没有 JSON tool request。")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError("invalid_json", "stdin 不是有效的 UTF-8 JSON。") from exc
    if not isinstance(value, Mapping):
        raise ToolError("invalid_request", "JSON tool request 必须是 object。")
    return value


def main() -> int:
    # Keep the protocol UTF-8 even when launched from a legacy Windows code
    # page outside DeskPet's host-provided PYTHONIOENCODING environment.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    if sys.argv[1:] == ["--healthcheck"]:
        response = {
            "request_id": "",
            "ok": True,
            "value": healthcheck(),
            "artifacts": [],
            "observations": [],
        }
    else:
        try:
            request = _read_request()
        except ToolError as exc:
            response = {
                "request_id": "",
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            }
        else:
            response = handle_request(request)
    sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()
    # A structured ``ok=false`` response is a protocol-level tool failure,
    # not a worker crash.  Transport success therefore always exits zero.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
