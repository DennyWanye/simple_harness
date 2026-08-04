# 任务：实现 deepresearch fan-out 递归守门（Phase 2 = WI-5c，单一共享常量封 4 路径）

你是编码 Expert。严格按已锁定 plan(v1.0) §5 + WI-5c 实现。本任务**只改**：`task_kinds.py` / `agent_tool.py` / `agent_parallel_tool.py` / `teammate_tools.py` + 3 个测试/脚本。**别碰** `research_tools.py` 和 `main.py`（另一并行 agent 做）。

## 背景（为什么）
deepresearch fan-out 后，`_handle_deepresearch` 始终注入 scheduler。必须保证**任何 LLM 派的子代理都拿不到 `deepresearch` 工具**，否则子代理调 deepresearch 会再 fan-out（递归爆炸）。子代理拿工具有 4 条路径，各有独立 forbidden 集，不同步——用**单一共享常量根治**。

## 先读
- plan §5「递归守门」+ §7 WI-5c：`G:/projects/deskpet/plans/2026-06-21-deepresearch-subagent-fanout/00-PLAN.md`
- `backend/deskpet/agent/task_kinds.py`：`_FORBIDDEN_IN_KIND`(:30, 现={agent,agent_parallel,spawn_team,spawn_subagents,await_subagents}) / research profile(:60-66, 现 tools 含 deepresearch) / `resolve_kind`+`_strip_forbidden`(:113-126)
- `backend/deskpet/tools/code_tools/agent_tool.py`:109-113（显式 tools 过滤，现只剥 "agent"）
- `backend/deskpet/tools/code_tools/agent_parallel_tool.py`:57（`_FORBIDDEN_NESTED_TOOLS`={agent,agent_parallel}）+ :251-260（`_filter_subagent_tools`）
- `backend/deskpet/agent/team/teammate_tools.py`:42（`FORBIDDEN_TEAMMATE_TOOLS`={agent,agent_parallel,spawn_team}）

## 实现（单一权威集 = task_kinds._FORBIDDEN_IN_KIND，含 deepresearch；其余 3 处引用它）
1. **task_kinds.py**：
   - `_FORBIDDEN_IN_KIND`(:30) 加 `"deepresearch"`（→6 元素）。
   - 内置 research profile(:62) tools `("web_search","web_fetch","deepresearch","read_file")` → `("web_search","web_fetch","read_file")`（去 deepresearch）。
   - 确认 `resolve_kind`/`_strip_forbidden` 对内置+overrides 都剥 `_FORBIDDEN_IN_KIND`（应已是；若 research profile 字面量含 deepresearch 而 resolve 时被剥，也 OK，但仍按上面删字面量）。
2. **agent_tool.py**:109-113：显式 tools 过滤 `t != "agent"` → `t not in _FORBIDDEN_IN_KIND`（顶部 `from deskpet.agent.task_kinds import _FORBIDDEN_IN_KIND`；注意 import 路径正确、无循环 import——task_kinds 只依赖 dataclasses/typing）。
3. **agent_parallel_tool.py**:57：`_FORBIDDEN_NESTED_TOOLS` 改为引用/并入 `_FORBIDDEN_IN_KIND`（如 `from deskpet.agent.task_kinds import _FORBIDDEN_IN_KIND as _FORBIDDEN_NESTED_TOOLS` 或 `_FORBIDDEN_NESTED_TOOLS = frozenset(_FORBIDDEN_IN_KIND)`）。保持 `_filter_subagent_tools`(:260) + `__all__` 导出名不变（spawn_subagents_tool 复用）。
4. **teammate_tools.py**:42：`FORBIDDEN_TEAMMATE_TOOLS` 并入 `_FORBIDDEN_IN_KIND`（union，保留原有 + deepresearch）：`FORBIDDEN_TEAMMATE_TOOLS = frozenset({"agent","agent_parallel","spawn_team"}) | _FORBIDDEN_IN_KIND`（import 共享集）。

## 同步更新会变红的现存断言（断言 research 含 deepresearch）
- `backend/tests/test_task_kinds.py`:18（`"deepresearch" in p.tools`）、:41（overrides merge `"deepresearch" in merged["research"].tools`）→ 改为断言**已被剥除**（`"deepresearch" not in ...`）。
- `backend/tests/test_agent_parallel_kinds.py`:62（`"deepresearch" in by["a"]["tools"]`）→ 改为 `"deepresearch" not in ...`。
- `backend/scripts/acceptance/subagent_driver_smoke.py`:43（`"deepresearch" in resolve_kind("research").tools`）→ 改为断言已剥除。
- **新增回归测试**（任选合适测试文件，如 test_task_kinds.py）：4 路径都不含 deepresearch——
  - `resolve_kind("research").tools` 不含 deepresearch；
  - `_filter_subagent_tools(["deepresearch","read_file"])` 不含 deepresearch（agent_parallel）；
  - agent_tool 显式 tools 过滤后不含 deepresearch（可直接断言 `"deepresearch" in _FORBIDDEN_IN_KIND`）；
  - `"deepresearch" in FORBIDDEN_TEAMMATE_TOOLS`。

## 约束 / 自检
- 不破坏 driver 现有测试：`cd G:/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/test_task_kinds.py tests/test_agent_parallel_kinds.py -q` 全绿；`.venv/Scripts/python.exe scripts/acceptance/subagent_driver_smoke.py` 仍 SHIP（若它跑得起来）。
- 无循环 import（改完 `python -c "import deskpet.tools.code_tools.agent_tool, deskpet.tools.code_tools.agent_parallel_tool, deskpet.agent.team.teammate_tools, deskpet.agent.task_kinds"` 不报错；cwd=backend）。
- 别碰 research_tools.py / main.py。
- 完成报告：4 文件改动 + 测试结果。
- python：`G:/projects/deskpet/backend/.venv/Scripts/python.exe`，cwd=`G:/projects/deskpet/backend`。
