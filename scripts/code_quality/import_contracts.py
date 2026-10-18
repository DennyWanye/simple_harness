#!/usr/bin/env python3
"""代码质量整治阶段 3：测试钉住的"导入隔离"约定（导入某入口时不得加载某些模块）。

这些约定来自测试里在新进程导入入口后检查 sys.modules 的断言。上提函数内导入前，
lift_imports 用它判断：上提会不会让某个入口在模块加载时就拉进被禁的模块。
新增这类测试时，把约定补到这里。

用法：python3 scripts/code_quality/import_contracts.py   # 检查现有顶层依赖图是否已违反约定
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inner_imports import BACKEND  # noqa: E402

_WORKFLOW_LEGACY = ("deskpet.workflows", "deskpet.tools.research_core")
_HANDLER_BLOCKED = (
    "config",
    "deskpet.tools.registry",
    "deskpet.tools._catalog_compat",
    "deskpet.harness",
    "deskpet.workflows",
)
_HANDLER_DYNAMIC = {
    "agent",
    "agent_parallel",
    "await_subagents",
    "context_page_in",
    "spawn_subagents",
    "spawn_team",
    "todo_write",
    "tool_activate",
    "tool_describe",
    "tool_search",
    "web_search",
}
_SDK_HEAVY = (
    "fastapi",
    "torch",
    "whisper",
    "playwright",
    "deskpet",
    "tauri",
    "simple_harness_memory",
)


@dataclass(frozen=True)
class Contract:
    entries: tuple[str, ...]
    forbidden: tuple[str, ...]
    test: str


def _handler_modules() -> tuple[str, ...]:
    """真实工具清单里 60 个静态处理函数所在模块（test_each_of_60_static_handler_resolutions_is_import_pure）。"""
    raw = json.loads(
        (BACKEND / "deskpet/tool_catalog/real_tool_manifest.json").read_text(encoding="utf-8")
    )
    mods: set[str] = set()
    for item in raw.get("tools") or ():
        if str(item["name"]) in _HANDLER_DYNAMIC:
            continue
        identity = str(item.get("context_handler_id") or item["handler_id"])
        if "<locals>" in identity:
            identity = str(item["handler_id"])
        mods.add(identity.split(":", 1)[0])
    return tuple(sorted(mods))


def contracts(tree: str) -> list[Contract]:
    if tree == "backend":
        return [
            Contract(
                ("deskpet.sdk_adapters.tools",),
                ("deskpet.tools.research_tools", "deskpet.workflows"),
                "sdk_adapters/test_product_host_ports.py",
            ),
            Contract(
                ("deskpet.tool_catalog", "deskpet.tools.context_page_in_tools"),
                ("deskpet.harness", "deskpet.workflows", "deskpet.tools.registry", "config"),
                "sdk_adapters/test_tool_catalog.py（71 个处理函数）",
            ),
            Contract(
                _handler_modules(),
                _HANDLER_BLOCKED,
                "sdk_adapters/test_tool_catalog.py（60 个静态处理函数）",
            ),
            Contract(
                ("deskpet.product_state",),
                ("deskpet.workflows", "deskpet.harness"),
                "sdk_adapters/test_authorization_saga.py",
            ),
            Contract(
                (
                    "deskpet.sdk_adapters.product_workflows.deep_research_definition",
                    "deskpet.sdk_adapters.product_workflows.ppt_pro_definition",
                ),
                _WORKFLOW_LEGACY,
                "sdk_adapters/test_product_workflows.py",
            ),
        ]
    return [
        Contract(
            ("simple_harness.observability",),
            (
                "simple_harness.runtime",
                "simple_harness.execution",
                "simple_harness.providers",
                "simple_harness.tools",
                "sqlite3",
            ),
            "unit/observability/test_observability_v1.py",
        ),
        Contract(
            ("simple_harness.runtime.agent_turn",),
            ("simple_harness.agents",),
            "agents/test_config_contracts.py",
        ),
        Contract(
            ("simple_harness",),
            ("simple_harness.agents", "simple_harness.execution.sqlite", *_SDK_HEAVY),
            "agents/test_build_agent_runtime.py、artifact/test_import_purity.py",
        ),
    ]


def matches(name: str, prefixes: tuple[str, ...]) -> str | None:
    for p in prefixes:
        if name == p or name.startswith(p + "."):
            return p
    return None


def first_forbidden(graph: dict[str, set[str]], starts, prefixes: tuple[str, ...]) -> str | None:
    """从 starts（连同其上层包）出发沿顶层依赖能走到的第一个被禁模块。"""
    from import_graph import with_ancestors

    starts = with_ancestors(starts)
    seen = set(starts)
    todo = list(starts)
    while todo:
        v = todo.pop()
        if matches(v, prefixes):
            return v
        for w in graph.get(v, ()):
            if w not in seen:
                seen.add(w)
                todo.append(w)
    return None


def main() -> int:
    from import_graph import build_graph

    graph = build_graph()
    bad = 0
    for tree in ("backend", "sdk"):
        for c in contracts(tree):
            hit = first_forbidden(graph, c.entries, c.forbidden)
            if hit:
                bad += 1
                print(f"已违反：{c.test} 入口 {c.entries[:3]} 能走到 {hit}")
    print("导入隔离约定：全部满足" if not bad else f"导入隔离约定：{bad} 条已违反")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
