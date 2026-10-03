# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""The Critic wait window inherits the SDK turn deadline; invalid explicit windows are refused.

HTN 补齐阶段 A′：编排服务只接受部署给的原生执行池，这里用产品同形的原生执行池选项构造（不开起来）。
"""

import pytest
from p33_world import native_options

from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider


def test_critic_wait_inherits_sdk_deadline_and_rejects_invalid_explicit_windows(tmp_path):
    config = OrchestratorConfig(evidence_root=tmp_path, turn_deadline_seconds=321, price_table=None)
    provider = LayeredScriptedProvider()

    def build(**kwargs):
        return Orchestrator(config, provider, **native_options(config, provider), **kwargs)

    assert build()._critic_wait == 321
    assert build(critic_wait_seconds=12)._critic_wait == 12
    assert build(critic_wait_seconds=321)._critic_wait == 321
    for invalid in (0, -1, 322, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            build(critic_wait_seconds=invalid)
