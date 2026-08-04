# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


PACK_ROOT = Path(__file__).resolve().parents[1]
ENTRY = PACK_ROOT / "tools" / "godot" / "main.py"
MANIFEST = PACK_ROOT / "deskpet-pack.json"
FIXTURE = PACK_ROOT / "tests" / "fixtures" / "minimal_project"


def _load_adapter() -> Any:
    spec = importlib.util.spec_from_file_location("deskpet_godot_adapter", ENTRY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def adapter() -> Any:
    return _load_adapter()


def _fake_executable(tmp_path: Path) -> Path:
    executable = tmp_path / ("Godot_v4.3-stable_win64.exe" if sys.platform == "win32" else "godot4")
    executable.write_bytes(b"fixture")
    return executable


def _completed(
    argv: list[str],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_detect_reports_real_version_and_source(
    adapter: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = _fake_executable(tmp_path)
    seen: list[list[str]] = []
    monkeypatch.delenv("GODOT_EXECUTABLE", raising=False)
    monkeypatch.setattr(adapter.shutil, "which", lambda _name: None)
    monkeypatch.setattr(adapter, "_standard_windows_candidates", lambda: [])

    def runner(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        assert timeout == 15
        return _completed(argv, stdout="4.3.stable.official.77dcf97d8\n")

    result = adapter.detect_godot(
        {"preferred_executable": str(executable)},
        runner=runner,
    )

    assert result == {
        "found": True,
        "minimum_version": "4.0",
        "executable": str(executable.resolve()),
        "source": "request",
        "version": "4.3.0",
        "version_raw": "4.3.stable.official.77dcf97d8",
        "major": 4,
        "minor": 3,
        "patch": 0,
        "compatible": True,
        "alternatives": [],
        "inspected": [],
    }
    assert seen == [[str(executable.resolve()), "--version"]]


def test_detect_is_honest_when_godot_is_missing(
    adapter: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GODOT_EXECUTABLE", raising=False)
    monkeypatch.setattr(adapter.shutil, "which", lambda _name: None)
    monkeypatch.setattr(adapter, "_standard_windows_candidates", lambda: [])

    result = adapter.detect_godot(
        {"preferred_executable": str(tmp_path / "missing.exe")}
    )

    assert result["found"] is False
    assert result["compatible"] is False
    assert result["reason"] == "godot_executable_not_found"
    assert result["inspected"][0]["status"] == "missing"


def test_detect_reports_incompatible_godot_three(
    adapter: Any,
    tmp_path: Path,
) -> None:
    executable = _fake_executable(tmp_path)

    result = adapter.detect_godot(
        {"preferred_executable": str(executable)},
        runner=lambda argv, timeout: _completed(argv, stdout="3.5.3.stable\n"),
    )

    assert result["found"] is True
    assert result["version"] == "3.5.3"
    assert result["compatible"] is False


def test_project_check_uses_headless_editor_and_unicode_path(
    adapter: Any,
    tmp_path: Path,
) -> None:
    executable = _fake_executable(tmp_path)
    project = tmp_path / "中文 项目"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "--version":
            return _completed(argv, stdout="4.2.2.stable\n")
        assert timeout == 45
        return _completed(argv, stdout="Godot Engine v4.2.2.stable\n")

    result = adapter.project_check(
        {
            "project_path": str(project),
            "godot_executable": str(executable),
            "timeout_seconds": 45,
        },
        runner=runner,
    )

    assert result["valid"] is True
    assert result["diagnostics"] == []
    assert calls[1] == [
        str(executable.resolve()),
        "--headless",
        "--editor",
        "--path",
        str(project.resolve()),
        "--quit",
    ]


def test_project_check_returns_project_diagnostics_without_hiding_them(
    adapter: Any,
    tmp_path: Path,
) -> None:
    executable = _fake_executable(tmp_path)
    project = tmp_path / "broken"
    project.mkdir()
    (project / "project.godot").write_text("config_version=5\n", encoding="utf-8")

    def runner(argv: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        if argv[-1] == "--version":
            return _completed(argv, stdout="4.4.stable\n")
        return _completed(
            argv,
            stderr="SCRIPT ERROR: Parse Error: Expected expression at res://main.gd:4\n",
            returncode=1,
        )

    result = adapter.project_check(
        {
            "project_path": str(project),
            "godot_executable": str(executable),
        },
        runner=runner,
    )

    assert result["valid"] is False
    assert result["error_code"] == "godot_project_invalid"
    assert result["exit_code"] == 1
    assert result["diagnostics"] == [
        "SCRIPT ERROR: Parse Error: Expected expression at res://main.gd:4"
    ]


def test_dev_fail_once_is_durable_and_only_wraps_real_headless_launch(
    adapter: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _fake_executable(tmp_path)
    project = tmp_path / "retry project"
    project.mkdir()
    (project / "project.godot").write_text(
        "config_version=5\n", encoding="utf-8"
    )
    user_data = tmp_path / "isolated-user-data"
    monkeypatch.setenv("DESKPET_DEV_MODE", "1")
    monkeypatch.setenv(
        "DESKPET_CAPABILITY_E2E_CASE_ID", "UA-GODOT-FAIL-ONCE"
    )
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(user_data))
    launches: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        launches.append(argv)
        if argv[-1] == "--version":
            return _completed(argv, stdout="4.3.stable\n")
        return _completed(argv, stdout="Godot Engine v4.3.stable\n")

    monkeypatch.setattr(adapter.subprocess, "run", fake_run)
    with pytest.raises(adapter.ToolError) as first:
        adapter.project_check(
            {
                "project_path": str(project),
                "godot_executable": str(executable),
            }
        )
    assert first.value.code == "godot_executable_not_found"
    assert first.value.details["retry_strategy"].startswith(
        "call godot__detect"
    )
    # The first version probe was real, but the armed headless launch failed
    # immediately at the subprocess boundary.
    assert launches == [[str(executable.resolve()), "--version"]]

    second = adapter.project_check(
        {
            "project_path": str(project),
            "godot_executable": str(executable),
        }
    )
    assert second["valid"] is True
    assert launches[-1][1:3] == ["--headless", "--editor"]
    marker = (
        user_data
        / "acceptance-fixtures"
        / "ua-godot-fail-once"
        / "headless-launch-consumed.json"
    )
    assert json.loads(marker.read_text(encoding="utf-8"))["failure"] == (
        "godot_executable_not_found"
    )


def test_fail_once_fixture_is_inert_without_both_dev_guards(
    adapter: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path / "state"))
    monkeypatch.setenv(
        "DESKPET_CAPABILITY_E2E_CASE_ID", "UA-GODOT-FAIL-ONCE"
    )
    monkeypatch.delenv("DESKPET_DEV_MODE", raising=False)
    monkeypatch.setattr(
        adapter.subprocess,
        "run",
        lambda argv, **_kwargs: _completed(list(argv), stdout="ok\n"),
    )
    adapter._run_process(["godot", "--headless"], timeout=5)
    assert not (tmp_path / "state" / "acceptance-fixtures").exists()


def test_project_check_rejects_non_project_directory(
    adapter: Any,
    tmp_path: Path,
) -> None:
    with pytest.raises(adapter.ToolError) as raised:
        adapter.project_check({"project_path": str(tmp_path)})
    assert raised.value.code == "godot_project_file_missing"


def test_json_tool_healthcheck_process() -> None:
    completed = subprocess.run(
        [sys.executable, str(ENTRY), "--healthcheck"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0
    response = json.loads(completed.stdout)
    assert set(response) == {
        "ok",
        "request_id",
        "value",
        "artifacts",
        "observations",
    }
    assert response["ok"] is True
    assert response["value"]["protocol"] == "deskpet-json-tool-v1"
    assert response["value"]["healthy"] is True
    assert response["value"]["tools"] == ["detect", "project_check"]


def test_json_tool_declared_failure_is_not_a_process_crash() -> None:
    request = {
        "protocol": "deskpet-json-tool-v1",
        "request_id": "request-1",
        "tool": "unknown",
        "args": {},
        "context": {},
    }
    completed = subprocess.run(
        [sys.executable, str(ENTRY)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    )

    assert completed.returncode == 0
    response = json.loads(completed.stdout)
    assert set(response) == {"request_id", "ok", "error"}
    assert response["request_id"] == "request-1"
    assert response["ok"] is False
    assert response["error"]["code"] == "unknown_tool"


def test_json_tool_wire_envelope_matches_host_contract() -> None:
    request = {
        "protocol": "deskpet-json-tool-v1",
        "request_id": "wire-1",
        "tool": "healthcheck",
        "args": {},
        "context": {
            "workspace_roots": [],
            "temp_dir": "",
            "root_run_id": "root-1",
            "run_id": "run-1",
            "effect_id": "effect-1",
        },
    }
    completed = subprocess.run(
        [sys.executable, str(ENTRY)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0
    response = json.loads(completed.stdout)
    assert response["request_id"] == "wire-1"
    assert response["ok"] is True
    assert response["value"]["healthy"] is True
    assert response["artifacts"] == []
    assert response["observations"] == []
    assert set(response) <= {
        "ok",
        "request_id",
        "value",
        "effect_plan",
        "artifacts",
        "observations",
        "error",
    }


def test_json_tool_protocol_error_is_structured_and_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, str(ENTRY)],
        input=json.dumps(
            {
                "protocol": "wrong-protocol",
                "request_id": "wire-bad",
                "tool": "healthcheck",
                "args": {},
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0
    response = json.loads(completed.stdout)
    assert response == {
        "error": {
            "code": "unsupported_protocol",
            "details": {},
            "message": "protocol 必须是 deskpet-json-tool-v1。",
        },
        "ok": False,
        "request_id": "wire-bad",
    }


def test_manifest_is_strict_and_file_hashes_match() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["id"] == "godot"
    assert manifest["version"] == "1.0.4"
    assert manifest["source"]["type"] == "builtin"
    assert manifest["compatibility"]["os"] == ["windows"]
    tools = manifest["entries"]["tools"]
    assert [tool["id"] for tool in tools] == ["detect", "project_check"]
    assert [tool["provider_name"] for tool in tools] == [
        "godot__detect",
        "godot__project_check",
    ]
    assert all(tool["runtime"] == "deskpet-json-tool-v1" for tool in tools)
    assert all(tool["execution_profile"] == "native-adapter" for tool in tools)
    assert "mcp_servers" not in manifest["entries"]

    declared = {item["path"]: item["sha256"] for item in manifest["files"]}
    actual_files = {
        path.relative_to(PACK_ROOT).as_posix()
        for path in PACK_ROOT.rglob("*")
        if path.is_file()
        and path != MANIFEST
        and "__pycache__" not in path.parts
        and not path.name.endswith((".pyc", ".pyo"))
    }
    assert set(declared) == actual_files
    for relative, expected_hash in declared.items():
        digest = hashlib.sha256((PACK_ROOT / relative).read_bytes()).hexdigest()
        assert digest == expected_hash


@pytest.mark.skipif(
    not any(
        executable
        for executable in (shutil.which("godot4"), shutil.which("godot"))
    ),
    reason="Godot is not installed on this test machine",
)
def test_real_godot_headless_fixture_when_available(adapter: Any) -> None:
    result = adapter.project_check({"project_path": str(FIXTURE)})
    assert result["valid"] is True, result
