"""A frozen backend must not be executed as a Python interpreter probe."""

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest
from agent_orchestrator.runtime import sandbox
from deskpet.orchestration.service import OrchestrationService


def service_at(root):
    service = OrchestrationService.__new__(OrchestrationService)
    service.root = root
    service._executor = object()
    return service


def test_frozen_backend_never_enters_interpreter_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    def forbidden(_interpreter):
        pytest.fail("frozen application was treated as a Python CLI")

    monkeypatch.setattr(sandbox.SeatbeltExecutor, "for_interpreter", forbidden)
    service = service_at(tmp_path)
    result = asyncio.run(service._probe_sandbox())
    assert result == {"ok": False, "reason": "frozen_backend_is_not_a_python_interpreter"}
    assert service._executor is None
    assert list(tmp_path.iterdir()) == []


def test_source_mode_retains_interpreter_unavailable_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    seen = []

    def unavailable(interpreter):
        seen.append(interpreter)
        raise sandbox.SandboxUnavailable("unit interpreter unavailable")

    monkeypatch.setattr(sandbox.SeatbeltExecutor, "for_interpreter", unavailable)
    service = service_at(tmp_path)
    assert asyncio.run(service._probe_sandbox()) == {
        "ok": False, "reason": "unit interpreter unavailable",
    }
    assert seen == [sys.executable] and service._executor is None


@pytest.mark.parametrize("passed", [False, True])
def test_source_mode_still_uses_probe_verdict_and_records_it(tmp_path, monkeypatch, passed):
    # Wiring control only: this does not assert that real isolation passed.
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    executor = object()
    seen = []

    def make(interpreter):
        seen.append(interpreter)
        return executor

    payload = {"ok": passed, "reason": "unit_probe_control"}

    async def probe(actual):
        assert actual is executor
        return SimpleNamespace(ok=passed, to_json=lambda: payload)

    monkeypatch.setattr(sandbox.SeatbeltExecutor, "for_interpreter", make)
    monkeypatch.setattr(sandbox, "probe_sandbox", probe)
    service = service_at(tmp_path)
    assert asyncio.run(service._probe_sandbox()) == payload
    assert seen == [sys.executable]
    assert service._executor is (executor if passed else None)
    assert json.loads((tmp_path / "sandbox-probe.json").read_text()) == payload
