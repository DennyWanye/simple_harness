"""任务页执行图面板的 ``taskgraph.*`` 读接口与"改计划进度"面板上人的两个动作（2026-10-03）必须能从控制通道到达编排 handler。

2026-09-25 主流程优化条目 1：``main.py`` 的编排分发只放行 ``mission_``/``orchestration_``
前缀，``taskgraph.snapshot`` 等消息（handlers.py 已注册）到不了后台，面板只会读取超时。
本测试从 ``main.py`` 源码里取出那一个前缀元组，与 handlers.py 的注册表对照。
``agent_*`` 有意不放行（各档位技能目录统一之前不能开技能安装）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from deskpet.orchestration import handlers

MAIN = Path(__file__).parents[2] / "main.py"


def _orchestration_dispatch_prefixes() -> tuple[str, ...]:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    found: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "msg_type"
            and node.args
            and isinstance(node.args[0], ast.Tuple)
        ):
            values = tuple(elt.value for elt in node.args[0].elts if isinstance(elt, ast.Constant))
            if "mission_" in values:
                found.append(values)
    assert len(found) == 1, found
    return found[0]


def test_taskgraph_reads_are_routed_and_agent_verbs_are_not():
    prefixes = _orchestration_dispatch_prefixes()
    registered = {name for name in handlers._ACTIONS}
    taskgraph = sorted(name for name in registered if name.startswith("taskgraph."))
    assert taskgraph == [
        "taskgraph.abandon_convergence", "taskgraph.convergence", "taskgraph.diff", "taskgraph.execution_detail",
        "taskgraph.execution_snapshot", "taskgraph.retry_notification", "taskgraph.snapshot", "taskgraph.why_not_ready",
    ]
    for name in taskgraph:
        assert name.startswith(prefixes), (name, prefixes)
    for name in registered:
        if name.startswith("agent_"):
            assert not name.startswith(prefixes), name
