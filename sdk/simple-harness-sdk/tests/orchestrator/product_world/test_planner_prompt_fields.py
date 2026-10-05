# SPDX-License-Identifier: Apache-2.0
"""规划器提示词讲每种决定时，要把那种决定的必填字段说全（联测真机：提示词讲"等待"时没提 reason，
规划器照着写、两次被判格式错误、规划次数用尽，任务失败）。

**改坏检验**（PRM-01）：提示词里"等待"那一条去掉 reason → 变红。"""
from __future__ import annotations

import inspect

from agent_orchestrator.contracts.planning_decisions import WaitDecision
from agent_orchestrator.runtime import role_templates


def test_the_wait_paragraph_names_every_required_field():
    source = inspect.getsource(role_templates)
    start = source.index("  - WAIT：")
    paragraph = source[start:source.index("  - NO_CHANGE：", start)]
    required = set(inspect.signature(WaitDecision).parameters)
    assert required == {"wait_for", "reason"}
    for name in required:
        assert name in paragraph, f"the WAIT paragraph of the planner prompt does not mention {name!r}"
