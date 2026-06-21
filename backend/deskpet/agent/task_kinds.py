# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事务分型（task-kind）注册表 — 子代理并发驱动的「多种事务」路由地基。

plan: plans/2026-06-21-subagent-concurrency-driver/ WI-0.1

每个 ``KindProfile`` 把一种「事务」（research / code / doc / web / fileops /
general）映射到：工具子集 + 迭代上限 + system framing + 可选模型覆盖 + 并发
lane cap。``agent_parallel`` / ``spawn_subagents`` 的每个子任务带一个 ``kind``
字段，由本模块解析成 profile，决定该子代理怎么跑。

设计要点
--------
* **递归守门**：任何 kind 的工具子集都会被剔除 spawn 类工具（``agent`` /
  ``agent_parallel`` / ``spawn_team`` / ``spawn_subagents`` / ``await_subagents``），
  保证子代理不能再 spawn 子代理 → 扁平 fan-out、depth=1。
* **未知 kind 安全回退**：解析不到 → ``general``（只读集），永不抛。
* **配置覆盖**：``load_kind_overrides`` 从 ``config.raw['agent']['subagent_kinds']``
  合并覆盖；单条坏覆盖逐条 try/except 跳过，不污染整表（防 b05823b 式
  config 静默失效）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

# 递归守门：任何 kind 的工具子集都不得含这些
# （与 agent_parallel._FORBIDDEN_NESTED_TOOLS / teammate_tools.FORBIDDEN_TEAMMATE_TOOLS 对齐）
_FORBIDDEN_IN_KIND = frozenset(
    {
        "agent",
        "agent_parallel",
        "spawn_team",
        "spawn_subagents",
        "await_subagents",
        "deepresearch",
    }
)


@dataclass(frozen=True)
class KindProfile:
    """一种事务类型的子代理画像。"""

    kind: str
    tools: tuple[str, ...]
    max_iterations: int
    framing: str = ""
    model: str | None = None
    lane_concurrency: int = 2


# 工具名全部对真实注册表核实（registration.py / os_tools / ppt_tools /
# research_tools / image_tools / excel_tools / doc_tools）：
#   read_file write_file edit_file glob grep list_directory run_shell
#   web_search web_fetch ppt_create doc_create excel_create deepresearch
#   generate_image skill_invoke
_BUILTIN_KINDS: dict[str, KindProfile] = {
    "general": KindProfile(
        "general",
        ("read_file", "list_directory", "glob", "grep", "web_search"),
        15,
        framing="你是通用只读子代理，专注调查并返回简洁结论。",
        lane_concurrency=2,
    ),
    "research": KindProfile(
        "research",
        ("web_search", "web_fetch", "read_file"),
        12,
        framing="你是调研子代理：检索权威来源、交叉验证、给带依据的结论。",
        lane_concurrency=2,
    ),
    "code": KindProfile(
        "code",
        (
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "run_shell",
            "list_directory",
        ),
        20,
        framing="你是编码子代理：读现状→改代码→自检（读回/跑测试）。",
        lane_concurrency=2,
    ),
    "fileops": KindProfile(
        "fileops",
        ("read_file", "write_file", "glob", "grep", "list_directory"),
        12,
        framing="你是文件操作子代理：按契约读写文件，不越界。",
        lane_concurrency=3,
    ),
    "doc": KindProfile(
        "doc",
        (
            "ppt_create",
            "doc_create",
            "excel_create",
            "read_file",
            "web_search",
            "generate_image",
        ),
        15,
        framing="你是文档生成子代理：产出 PPT/Word/Excel，必报完整产物路径。",
        lane_concurrency=1,
    ),
    "web": KindProfile(
        "web",
        ("web_search", "web_fetch"),
        8,
        framing="你是联网快查子代理：快速找事实/网址，不深挖。",
        lane_concurrency=3,
    ),
}

_DEFAULT_KIND = "general"


def _strip_forbidden(tools: tuple[str, ...]) -> tuple[str, ...]:
    """剔除递归守门工具（保证子代理不能再 spawn）。"""
    return tuple(t for t in tools if t not in _FORBIDDEN_IN_KIND)


def resolve_kind(
    name: str | None, *, overrides: dict[str, KindProfile] | None = None
) -> KindProfile:
    """名字 → KindProfile。

    * 未知 kind → ``general``（只读安全回退，R5）。
    * 返回的 profile 工具子集已 ``_strip_forbidden``（递归守门 → depth=1）。
    """
    table = overrides or _BUILTIN_KINDS
    key = (name or "").strip().lower()
    prof = (
        table.get(key)
        or table.get(_DEFAULT_KIND)
        or _BUILTIN_KINDS[_DEFAULT_KIND]
    )
    return replace(prof, tools=_strip_forbidden(prof.tools))


def known_kinds(overrides: dict[str, KindProfile] | None = None) -> list[str]:
    """已知 kind 名列表（含 overrides 新增）。"""
    return sorted((overrides or _BUILTIN_KINDS).keys())


def load_kind_overrides(
    raw_agent_cfg: dict[str, Any] | None,
) -> dict[str, KindProfile]:
    """从 ``config.raw['agent']['subagent_kinds']`` 合并覆盖到内置默认。

    缺省 / 格式错 → 返回内置默认（**不抛**，防 config 单例陷阱 R2）。
    单条坏覆盖逐条 try/except 跳过，整表仍可用。
    """
    merged: dict[str, KindProfile] = dict(_BUILTIN_KINDS)
    section: Any = {}
    if isinstance(raw_agent_cfg, dict):
        section = raw_agent_cfg.get("subagent_kinds") or {}
    if not isinstance(section, dict):
        return merged
    for name, spec in section.items():
        if not isinstance(spec, dict):
            continue
        try:
            base = merged.get(name) or _BUILTIN_KINDS[_DEFAULT_KIND]
            tools = spec.get("tools", base.tools)
            if not isinstance(tools, (list, tuple)):
                raise TypeError("tools must be a list")
            merged[name] = replace(
                base,
                kind=name,
                tools=tuple(str(t) for t in tools),
                max_iterations=int(spec.get("max_iterations", base.max_iterations)),
                framing=str(spec.get("framing", base.framing)),
                model=spec.get("model", base.model),
                lane_concurrency=int(
                    spec.get("lane_concurrency", base.lane_concurrency)
                ),
            )
        except Exception:  # noqa: BLE001 — 单条坏覆盖不污染整表
            continue
    return merged


__all__ = [
    "KindProfile",
    "resolve_kind",
    "known_kinds",
    "load_kind_overrides",
    "_FORBIDDEN_IN_KIND",
    "_BUILTIN_KINDS",
    "_DEFAULT_KIND",
]
