# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""代码类工具包。

``register_code_tools`` 由 ``host/tools_bootstrap.py`` 在启动时调用，把 glob、grep、
web_search、fetch_tool_result 注册进工具登记表（``toolset="code"``）；todo_write 与
委派类工具只在传入对应处理函数时才注册。

本包导出：

  * glob  — 按模式找文件（pathlib.rglob），按修改时间排序
  * grep  — 用 Python re 搜内容（不依赖外部 rg）
  * todo_write — 当前会话的任务清单，存进 SessionDB 并推给界面
  * web_search — 走进程内共享的 SearchGateway（``deskpet.retrieval``）
  * ask_clarification — 向用户提澄清问题

委派类工具（agent、agent_parallel、spawn_team、spawn_subagents）在本包只留公开结构，
执行归产品 Harness 的 ``DelegateRun``。
"""

from __future__ import annotations

from .clarify_tool import build_ask_clarification_tool
from .glob_tool import glob_tool
from .grep_tool import grep_tool
from .registration import register_code_tools
from .todo_write_tool import build_todo_write_tool
from .web_search_tool import web_search

__all__ = [
    "glob_tool",
    "grep_tool",
    "build_todo_write_tool",
    "web_search",
    "build_ask_clarification_tool",
    "register_code_tools",
]
