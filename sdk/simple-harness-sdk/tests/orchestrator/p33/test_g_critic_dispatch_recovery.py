# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""The Critic wait window inherits the SDK turn deadline; invalid explicit windows are refused."""

import pytest

from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.fixtures import RoleScriptedProvider


def test_critic_wait_inherits_sdk_deadline_and_rejects_invalid_explicit_windows(tmp_path):
    config = OrchestratorConfig(evidence_root=tmp_path, turn_deadline_seconds=321)
    assert Orchestrator(config, RoleScriptedProvider({}))._critic_wait == 321
    assert Orchestrator(config, RoleScriptedProvider({}), critic_wait_seconds=12)._critic_wait == 12
    assert Orchestrator(config, RoleScriptedProvider({}), critic_wait_seconds=321)._critic_wait == 321
    for invalid in (0, -1, 322, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            Orchestrator(config, RoleScriptedProvider({}), critic_wait_seconds=invalid)
