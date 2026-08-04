"""Deterministic ingress routing between legacy ReAct and durable graphs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class WorkflowRoute(StrEnum):
    REACT = "react"
    DEEP_RESEARCH = "deep_research"
    PPT_PRO = "ppt_pro"
    CODE_COMPLEX = "code_complex"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: WorkflowRoute
    reason: str
    confidence: float


_RESEARCH = re.compile(
    r"(?:deep\s*research|调研|深度调研|深入调研|研究报告|行业研究|竞品调研|系统性调研|"
    r"(?:调研|研究).{0,48}(?:来源|选型|报告|路线|建议|比较|对比|分析)|"
    r"(?:来源|选型|报告|路线|建议|比较|对比|分析).{0,48}(?:调研|研究))",
    re.I,
)
_NEGATED_RESEARCH = re.compile(
    r"(?:(?:不要|不需要|无需|不用|别).{0,16}(?:调研|研究)|"
    r"\b(?:do\s+not|don't|without)\b.{0,24}\b(?:deep\s+research|research)\b)",
    re.I,
)
_PPT = re.compile(r"(?:ppt|powerpoint|演示文稿|幻灯片|路演稿|汇报材料|deck)", re.I)
_CODE_ACTION = re.compile(
    r"(?:实现|开发|创建|修改|重构|修复|新增|删除|迁移|升级|接入|构建|编译|测试|运行测试|执行命令|运行脚本|"
    r"\b(?:implement|build|write|edit|modify|refactor|fix|add|remove|delete|migrate|upgrade|integrate|test)\b|"
    r"\brun\s+(?:the\s+)?(?:tests?|build|command|script)\b|"
    r"\bexecute\s+(?:the\s+)?(?:tests?|build|command|script)\b)",
    re.I,
)
_PATHISH = re.compile(r"(?:[A-Za-z]:)?[^\s，。；;]+[\\/][^\s，。；;]+")
_WORKSPACE = re.compile(
    r"(?:代码|项目|仓库|文件|模块|接口|数据库|前端|后端|测试|code|repo|repository|file|module|api|database|frontend|backend)",
    re.I,
)
_READ_ONLY = re.compile(
    r"(?:(?:解释|说明|分析|总结|查看|检查|审查|搜索|查找|阅读|读取|告诉|定位|是什么|为什么|怎么|如何|哪里)|"
    r"\b(?:review|explain|describe|summarize|analy[sz]e|inspect|read|search|find|grep|locate|what|why|how|where)\b)",
    re.I,
)
_NEGATED_ACTION = re.compile(
    r"(?:(?:不要|不需要|无需|不用|禁止|别|只需|只要).{0,24}"
    r"(?:修改|改动|写入|创建|删除|执行|运行|修复|实现)|"
    r"\b(?:do\s+not|don't|without|only)\b.{0,24}"
    r"\b(?:write|edit|modify|create|delete|execute|run|fix|implement)\b)",
    re.I,
)


def route_task(
    text: str,
    *,
    mode: str = "auto",
    proposed_tools: Iterable[str] = (),
    workspace_context: bool = False,
) -> RouteDecision:
    """Choose a route before planning, without asking an LLM to classify it."""

    normalized = " ".join(text.strip().split())
    # Paths and repository identifiers are evidence for a code task, not its
    # domain.  For example, editing ``plans/ppt-session/foo.txt`` must not be
    # mistaken for a request to generate a presentation.
    intent_text = _PATHISH.sub(" ", normalized)
    if _RESEARCH.search(intent_text) and not _NEGATED_RESEARCH.search(intent_text):
        return RouteDecision(WorkflowRoute.DEEP_RESEARCH, "explicit_research_intent", 1.0)
    if _PPT.search(intent_text) and (_CODE_ACTION.search(intent_text) or len(intent_text) > 4):
        return RouteDecision(WorkflowRoute.PPT_PRO, "explicit_presentation_intent", 1.0)

    tool_names = {name.casefold() for name in proposed_tools}
    has_write_tool = any(
        token in name
        for name in tool_names
        for token in ("write", "edit", "patch", "shell", "test", "build", "create", "delete")
    )
    action = bool(_CODE_ACTION.search(normalized))
    workspace = workspace_context or bool(_WORKSPACE.search(normalized))
    ask = bool(_READ_ONLY.search(normalized)) or normalized.endswith(("?", "？"))
    # Negative instructions are authority, not weak lexical hints.  A phrase
    # such as “不要修改，只解释” contains the token “修改” but must not be
    # promoted to a writable Code workflow, even if an earlier planner
    # proposed a write-capable tool.
    negated_action = bool(_NEGATED_ACTION.search(normalized))
    read_only = ask and (negated_action or (not action and not has_write_tool))

    if mode.casefold() == "chat" or read_only:
        return RouteDecision(WorkflowRoute.REACT, "read_only_or_chat", 0.98)
    if has_write_tool:
        return RouteDecision(WorkflowRoute.CODE_COMPLEX, "write_tool_preflight", 1.0)
    if action and workspace:
        return RouteDecision(WorkflowRoute.CODE_COMPLEX, "workspace_action", 0.98)
    if workspace_context and not read_only:
        return RouteDecision(WorkflowRoute.CODE_COMPLEX, "unknown_workspace_task", 0.75)
    return RouteDecision(WorkflowRoute.REACT, "ordinary_conversation", 0.9)


__all__ = ["RouteDecision", "WorkflowRoute", "route_task"]
