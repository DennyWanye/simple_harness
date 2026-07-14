from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "e2e"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base: str, path: str, body: dict | None = None, *, method: str | None = None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base + path,
        data,
        {"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def _wait(base: str, path: str) -> None:
    for _ in range(100):
        try:
            _request(base, path)
            return
        except Exception:
            time.sleep(0.03)
    raise AssertionError(f"fixture unavailable: {base}{path}")


@pytest.fixture
def provider_seam(tmp_path: Path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log = tmp_path / "provider.jsonl"
    process = subprocess.Popen(
        [sys.executable, str(SCRIPTS / "context_os_provider_seam.py"), "--port", str(port), "--log", str(log)],
        env={**os.environ, "DESKPET_DEV_MODE": "1"},
    )
    _wait(base, "/v1/models")
    yield base, log
    process.terminate()
    process.wait(timeout=5)


@pytest.fixture
def fixture_daemon(tmp_path: Path):
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log = tmp_path / "fixture.jsonl"
    process = subprocess.Popen(
        [sys.executable, str(SCRIPTS / "context_os_fixture_daemon.py"), "--port", str(port), "--log", str(log)],
        env={**os.environ, "DESKPET_DEV_MODE": "1"},
    )
    _wait(base, "/health")
    yield base, log
    try:
        _request(base, "/stop", {})
    except Exception:
        process.terminate()
    process.wait(timeout=5)


def _chat(base: str, body: dict, *, request_id: str, attempt_id: str, purpose: str = "agent_response"):
    request = urllib.request.Request(
        base + "/v1/chat/completions",
        json.dumps(body).encode(),
        {
            "Content-Type": "application/json",
            "Authorization": "Bearer ctx-e2e-local-key",
            "X-DeskPet-Purpose": purpose,
            "X-DeskPet-Request-Id": request_id,
            "X-DeskPet-Attempt-Id": attempt_id,
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def test_catalog_then_model_fault_triggers_once_and_is_clearable(provider_seam):
    base, log = provider_seam
    _request(base, "/__control/catalog", {"model": "ctx-compact-fixture", "window": 8192})
    armed = _request(base, "/__control/model-fault", {"model": "ctx-compact-fixture", "error": "dedicated_model_unavailable"})
    assert armed["fault"]["trigger_count"] == 0

    body = {"model": "ctx-compact-fixture", "messages": [{"role": "user", "content": "CTX-COMPACT-FAULT"}]}
    with pytest.raises(urllib.error.HTTPError) as first:
        _chat(base, body, request_id="req-fault", attempt_id="fault-1", purpose="compressor")
    assert first.value.code == 503

    second = _chat(base, body, request_id="req-fault-2", attempt_id="fault-2", purpose="compressor")
    assert second["choices"][0]["finish_reason"] == "stop"
    state = _request(base, "/__control/state")
    fault = state["model_faults"]["ctx-compact-fixture"]
    assert fault["active"] is False
    assert fault["trigger_count"] == 1
    assert fault["triggered_request_id"] == "req-fault"
    assert _request(base, "/__control/model-fault/ctx-compact-fixture", method="DELETE")["cleared"] is True
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events].count("fault_triggered") == 1


def test_fallback_requires_same_request_new_attempt_and_clears_on_terminal(provider_seam):
    base, _ = provider_seam
    _request(base, "/__control/fallback", {"marker": "CTX-FALLBACK-1", "from": "ctx-primary", "to": "ctx-fallback"})
    primary = {"model": "ctx-primary", "messages": [{"role": "user", "content": "CTX-FALLBACK-1"}]}
    with pytest.raises(urllib.error.HTTPError) as failed:
        _chat(base, primary, request_id="req-chain", attempt_id="attempt-primary")
    assert failed.value.code == 503

    fallback = {**primary, "model": "ctx-fallback"}
    result = _chat(base, fallback, request_id="req-chain", attempt_id="attempt-fallback")
    assert result["choices"][0]["message"]["content"] == "ACK:CTX-FALLBACK-1"
    assert _request(base, "/__control/state")["fallback"] is None


def test_force_finish_rounds_are_causal_and_terminal_clears_arm(provider_seam):
    base, log = provider_seam
    _request(base, "/__control/force-finish", {"marker": "CTX-FORCE-1", "tool_rounds": 3})
    tools = [{"type": "function", "function": {"name": "mcp_ctx_fixture_tool_001", "parameters": {"type": "object"}}}]
    messages = [{"role": "user", "content": "CTX-FORCE-1"}]
    for index in range(1, 4):
        response = _chat(
            base,
            {"model": "ctx-primary", "messages": messages, "tools": tools},
            request_id="req-force",
            attempt_id=f"force-{index}",
        )
        call = response["choices"][0]["message"]["tool_calls"][0]
        assert json.loads(call["function"]["arguments"])["sequence"] == 900 + index
        messages.extend(
            [
                {"role": "assistant", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"invocation_id": f"inv-{index}"})},
            ]
        )
    terminal = _chat(
        base,
        {"model": "ctx-primary", "messages": messages},
        request_id="req-force",
        attempt_id="force-terminal",
        purpose="force_finish",
    )
    assert terminal["choices"][0]["message"]["content"] == "FORCE-FINISH-ACK"
    assert _request(base, "/__control/state")["force_finish"] is None
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events].count("force_finish_round") == 3
    assert [event["event"] for event in events].count("force_finish_terminal") == 1


def test_component_fault_read_is_one_shot_and_reset_cleans_state(fixture_daemon):
    base, log = fixture_daemon
    armed = _request(base, "/fault", {"name": "snapshot_cas_timeout", "value": "timeout"})
    assert armed["fault"]["trigger_count"] == 0
    first = _request(base, "/fault/snapshot_cas_timeout?request_id=req-cas")
    second = _request(base, "/fault/snapshot_cas_timeout?request_id=req-other")
    assert first["value"] == "timeout"
    assert first["fault"]["trigger_count"] == 1
    assert first["fault"]["triggered_request_id"] == "req-cas"
    assert second["value"] is None
    assert second["fault"]["trigger_count"] == 1
    _request(base, "/reset", {})
    assert _request(base, "/fault/snapshot_cas_timeout")["fault"] is None
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events].count("fault_triggered") == 1


def test_control_cli_routes_catalog_and_model_fault_to_provider(provider_seam):
    base, _ = provider_seam
    env = {
        **os.environ,
        "DESKPET_CONTEXT_OS_E2E_PROVIDER_URL": base + "/v1",
    }

    def cli(*args: str) -> dict:
        output = subprocess.check_output(
            [sys.executable, str(SCRIPTS / "context_os_fixture_ctl.py"), *args],
            env=env,
            text=True,
        )
        return json.loads(output)

    assert cli("set-model", "--model", "ctx-cli", "--window", "4096")["model"] == "ctx-cli"
    assert cli("model-fault", "--model", "ctx-cli", "--error", "cli_fault")["fault"]["active"] is True
    assert cli("clear-model-fault", "--model", "ctx-cli")["cleared"] is True


def test_provider_arms_reject_duplicates_and_support_explicit_cleanup(provider_seam):
    base, _ = provider_seam
    _request(base, "/__control/fallback", {"marker": "CTX-FALLBACK-CLEAR", "from": "ctx-primary", "to": "ctx-fallback"})
    with pytest.raises(urllib.error.HTTPError) as duplicate_fallback:
        _request(base, "/__control/fallback", {"marker": "CTX-FALLBACK-OTHER", "from": "ctx-primary", "to": "ctx-fallback"})
    assert duplicate_fallback.value.code == 409
    assert _request(base, "/__control/fallback", method="DELETE")["cleared"] is True

    _request(base, "/__control/force-finish", {"marker": "CTX-FORCE-CLEAR", "tool_rounds": 2})
    with pytest.raises(urllib.error.HTTPError) as duplicate_force:
        _request(base, "/__control/force-finish", {"marker": "CTX-FORCE-OTHER", "tool_rounds": 2})
    assert duplicate_force.value.code == 409
    assert _request(base, "/__control/force-finish", method="DELETE")["cleared"] is True
