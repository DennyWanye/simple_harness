# CODEX 作业指令 — 任务漂移修复 Fix B（deepresearch 用户原话直传，B2 通用注入）

你是 DeskPet 后端 Expert。在**当前 worktree**（`G:/projects/deskpet/.claude/worktrees/task-drift-fixb`，分支 `task-drift-fixb`）实现 **Fix B**。Lead（Claude）会审查 + 集成 + 真机验收。

## 权威规格（必读，自包含）
先读本 worktree 的 `plans/2026-06-20-task-drift-fix/00-fix-plan.md`，**重点 §9.2（Fix B 确切改点表 B1-B4）+ §9.2 的 🔴 阻碍 + §8.2 决策**。再读 `plans/2026-06-20-task-drift-fix-HANDOFF.md` 了解漂移根因。§9 的 file:line 是评估时核实的，可能因改动漂移，**按语义定位**。

## 背景一句话
桌宠对"深度调研 Rust Tokio"漂去研究上下文里旧的 CATL 主题。deepresearch 工具内部 `_PLAN_PROMPT`/`_SYNTH_PROMPT` 只吃 `{topic}`、看不到用户原话 → 放大漂移。Fix B = 由系统把**本轮原始 user 请求**强制注入工具，硬化主题贴合。

## 你的实现范围（严格只改这些，别碰 Fix A 的 bundle.py/memory.py/policy.py/assembler.py）

### B1 — loop-start 原话可靠取法
- `backend/main.py`：调 `_agent.run(...)` 的地方（约 :6092，`async for ev in _agent.run(`），新增 kwarg `loop_user_request=(None if _is_sentinel else _text)`。
- `_text` 是 `_run_chat` 的函数参数 = 100% 本轮用户原话。**不要**用 `_msgs[-1]`（进 loop 后是工具结果）。`_is_sentinel` 是已有判断（如 `<<auto_resume>>`），sentinel 时传 None。

### B2 — agent_loop dispatch 处通用注入
- `backend/deskpet/agent/agent_loop.py`：`run()` 签名加 `loop_user_request: Optional[str] = None`。
- 在 dispatch 工具的循环体（`for tc in response.tool_calls:`，`_dispatch_tool` 调用**之前**）插入通用注入：
  ```python
  if loop_user_request and isinstance(tc.arguments, dict):
      if _tool_declares_user_request(tc.name, tool_schemas):
          tc.arguments["user_request"] = loop_user_request   # 无条件覆盖
  ```
- `_tool_declares_user_request(name, schemas)`：遍历本 run 已构建的 `tool_schemas`，找到同名工具、判断其 `parameters.properties` 含 `"user_request"`。
- 🔴 **关键**：**必须无条件覆盖**（系统值永远赢）。**不要**复用 registry 的 `set_session_context` —— 它的合并顺序（`registry.py` 约 :690-692，`merged_params.update(session_context)` 先、`update(params)` 后）会让 LLM 自己填的漂值覆盖系统注入值，违背初衷。

### B3 — deepresearch schema 加可选字段
- `backend/deskpet/tools/research_tools.py`：deepresearch 的 tool schema（约 :1589 properties）加：
  ```python
  "user_request": {"type": "string", "description": "（系统注入，勿填）发起本轮的用户原始请求原文，用于对齐主题，防止 topic 漂移。"}
  ```
  **不要**加进 `required`。description 写"系统注入勿填"降低 LLM 主动填漂值概率。

### B4 — prompt 注入 + 透传链
- `_PLAN_PROMPT`（约 :463）顶部加：
  ```
  ORIGINAL USER REQUEST (authoritative — the plan MUST serve this):
  {user_request}

  REFINED TOPIC: {topic}
  ```
- `_SYNTH_PROMPT`（约 :485）的 `You are writing a research briefing on: {topic}` 上方加 `ORIGINAL USER REQUEST: {user_request}`。
- orchestrator `deepresearch()` 签名（约 :975）加 `user_request: Optional[str] = None`；函数内统一算 `_ur = (user_request or topic).strip()`。
- 两处 `.format`（约 :1040 plan、:1341 synth）传 `user_request=_ur`。
- handler `_handle_deepresearch`（约 :1632/:1638）从 `args.get("user_request")` 取、透传给 `deepresearch(..., user_request=...)`（约 :1658）。
- **零回归铁律**：`user_request=None`（老调用/单测/未注入）→ `_ur=topic` → prompt 退化为单锚，`.format` 不缺键不报错。

## 单元测试（必写，放 backend/tests/）
新建 `backend/tests/test_task_drift_fixb.py`，至少覆盖：
1. `_PLAN_PROMPT.format(topic=..., user_request=...)` 含 ORIGINAL USER REQUEST 文本。
2. `deepresearch(..., user_request=None)` 时 `_ur` 回退到 topic，prompt 不缺键、不抛 KeyError。
3. `_tool_declares_user_request("deepresearch", schemas)` 对含该字段的 schema 返 True、对不含的返 False。
4. dispatch 注入：构造一个 `tc.arguments={"topic":"漂移值","user_request":"LLM乱填"}`，注入后 `tc.arguments["user_request"] == loop_user_request`（验证无条件覆盖）。可对 agent_loop 的注入逻辑抽成可单测的小函数或直接测 `_tool_declares_user_request` + 覆盖语义。

## 跑测试（用主 checkout 的 venv 解释器，从本 worktree 的 backend 跑）
```bash
cd /g/projects/deskpet/.claude/worktrees/task-drift-fixb/backend
/g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/test_task_drift_fixb.py -v
# 回归：确认没碰坏 deepresearch 现有测试
/g/projects/deskpet/backend/.venv/Scripts/python.exe -m pytest tests/ -k "research or deepresearch or agent_loop" -q
```
`python -m pytest` 从本 worktree backend/ 跑 → sys.path[0]=CWD → import 的是本 worktree 代码（已确认隔离正确）。

## 交付要求
- 只改上述文件 + 新增测试文件。**不要** `git commit`（Lead 会审查后集成）。**不要**起 backend/Tauri/前端。
- 改完跑通单测，最后输出：①改了哪些文件每个文件改了什么 ②单测结果（贴 pytest 末尾 PASSED 行）③遇到的偏差/阻碍。
