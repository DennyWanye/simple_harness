# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Host-support 0.9.8 tests share one spy on every way the orchestrator reaches pytest."""

from __future__ import annotations

import pytest

from agent_orchestrator.orchestrator import event_handler as event_handler_module
from agent_orchestrator.runtime import tool_gateway
from agent_orchestrator.verification import deterministic_checks


@pytest.fixture(name="pytest_spy")  # a conftest function named pytest_* would be a hook
def spy_on_pytest(monkeypatch):
    """Every way the orchestrator reaches pytest, spied — the real runner still runs, so a
    call would also leave a marker file behind."""

    calls: list[dict] = []
    real = tool_gateway.run_pytest

    async def spy(root, *, path, timeout, executor=None):  # P3.2 D1: through the port now
        calls.append({"root": str(root), "path": path})
        return await real(root, path=path, timeout=timeout, executor=executor)

    for module in (tool_gateway, deterministic_checks, event_handler_module):
        monkeypatch.setattr(module, "run_pytest", spy)
    return calls
