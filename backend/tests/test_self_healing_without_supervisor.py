# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""工具熔断与会话活动记录不挂在监工开关下：监工 Agent 关着它们也照常装配。"""

from __future__ import annotations

import ast

from tests.host_source import host_source_text, host_source_tree


def _supervisor_branch() -> ast.If:
    tree = host_source_tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and '_sup_cfg.get("enabled"' in ast.unparse(node.test).replace(
            "'", '"'
        ):
            return node
    raise AssertionError("supervisor branch not found")


def test_breaker_is_not_under_the_supervisor_switch():
    branch = ast.unparse(_supervisor_branch())
    assert "ToolCircuitBreaker" not in branch
    assert "ToolCircuitBreaker" in host_source_text()


def test_session_activity_is_registered_regardless_of_the_supervisor_switch():
    branch = ast.unparse(_supervisor_branch())
    assert 'service_context.register("session_activity"' not in branch
    assert 'service_context.register("session_activity"' in host_source_text()
