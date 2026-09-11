# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Fixtures for the Host orchestration service tests (plan 2026-09-11 H2/H3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.governance.permissions import Principal


@pytest.fixture
def principal() -> Principal:
    return Principal("local-user:test", "本机用户")


@pytest.fixture
def orchestration_root(tmp_path: Path) -> Path:
    return tmp_path / "userdata" / "data" / "agent-orchestrator"
