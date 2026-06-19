# Agent Loop 优化 — 代码级实施计划 (v1)

> 依据 [`STATUS/AgentImprovements.md`](../../STATUS/AgentImprovements.md)（缺陷审计 + 业界对标）与 [`STATUS/AgentLoop.md`](../../STATUS/AgentLoop.md)（执行引擎骨架）落到**可照着写代码**的实施 plan。
>
> 创建：2026-06-20 ｜ 基线：master 读码核实 ｜ 目标读者：实现这批改动的 Claude/codex 子代理。
>
> **执行纪律**：本 plan 走项目标准 spec-first + TDD + 真机 E2E（见 §1）。**本 plan 内容只增不删**（挑战迭代产生的修订一律以「修订/补丁」形式追加，见 §12）。

---

## 0. 总览

### 0.1 工作项清单（按优先级 + 依赖）

| WI | 名称 | 维度 | 缺陷# | 优先级 | 主文件 | 工作量 | 依赖 |
|---|---|---|---|---|---|---|---|
| **WI-1** | `tool_choice` 协议级硬约束 | 控制 | #5 | 🥇 P1 | `providers/openai_compatible.py` + `agent_loop.py` | 0.5d | 无 |
| **WI-2** | 每轮结构化 trace（jsonl） | 可观测 | #7 | 🥇 P1 | `agent_loop.py` + 新 `agent/trace.py` | 1d | 无 |
| **WI-3** | 阶段化提示词 + 收尾自查 | 提示词 | #1/#5 | 🥈 P2 | `assembler/components/persona.py` + `agent_loop.py` | 0.5d | 无 |
| **WI-4** | Focus Chain：todo 进度周期回灌 | 记忆 | #6 | 🥈 P2 | `agent_loop.py` | 1d | 无 |
| **WI-5** | 触发式知识注入 | 记忆 | #6 | 🥈 P2 | `assembler/components/skill.py` + `skills/skill_matcher.py` | 2d | 无 |
| **WI-6** | SEARCH/REPLACE 编辑 + 降级匹配 + did-you-mean | 工具 | 编辑可靠性 | 🥈 P2 | `tools/os_tools/edit_file.py` | 2d | 无 |
| **WI-7** | `ask_clarification` 工具 | 工具 | 交互 | 🥉 P3 | 新 `tools/code_tools/clarify_tool.py` + `agent_loop.py` + 前端 | 2–3d | 前端 WS |

> 7 个 WI **彼此独立、无强依赖**，可并行。建议批次：**批 A**（WI-1/2/3，零架构风险、当天可完）→ **批 B**（WI-4/5/6，中等）→ **批 C**（WI-7，含前端）。

### 0.2 依赖与并行示意

```
批 A (零风险, 各自独立)        批 B (中等, 各自独立)         批 C (含前端)
┌─ WI-1 tool_choice            ┌─ WI-4 Focus Chain          └─ WI-7 ask_clarification
├─ WI-2 trace                  ├─ WI-5 触发式知识注入            (依赖前端新 WS 消息类型)
└─ WI-3 阶段化提示词            └─ WI-6 SEARCH/REPLACE 编辑
```

### 0.3 全局成功判据

1. 每个 WI 的新增单测全绿（pytest）。
2. 默认配置（flag off）下**字节级行为不变**（BC 回归：现有测试不破）。
3. 至少 WI-1/WI-3/WI-4/WI-6 各跑一次 windows-mcp 真机 E2E（见 §9），截图 + backend log 为证。
4. 完成后更新 [`STATUS/status.md`](../../STATUS/status.md) 与本 plan 的进度。

---

## 1. 全局约定（所有 WI 通用）

### 1.1 分支与环境
- 在 `master` 直接开发（项目策略，见 `feedback_deskpet_branch_strategy`），每个 WI 独立 commit。
- 跑测试用 worktree venv python：`backend/.venv/Scripts/python.exe`，从 repo 根用绝对正斜杠路径（见 `feedback_bash_cwd_venv`）。
- **新文件建好立即 `git add`+commit，且提交命令带 `dangerouslyDisableSandbox: true`**（沙箱会回滚未提交/沙箱内提交的改动，见 `feedback_commit_untracked_immediately`）。

### 1.2 测试命令
```bash
# 单 WI 单测（示例）
cd /g/projects/deskpet && backend/.venv/Scripts/python.exe -m pytest backend/tests/test_<wi>.py -v
# 全量 agent loop 回归（BC 守门）
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_deskpet_agent_loop.py \
  backend/tests/test_p6_agent_loop_gate.py backend/tests/test_p6_agent_loop_ctx.py \
  backend/tests/test_p5s2_*.py -v
```

### 1.3 TDD 纪律
- 每个 WI：先写失败测试（red）→ 实现（green）→ 重构。测试函数名 + 断言在各 WI 的「测试规格」节给出。
- flag 默认 off 的 WI，必须有一条「flag off → 行为与改动前字节一致」的 BC 测试。

### 1.4 验收纪律（HARD）
- 「改了代码 / 跑过单测」≠ 完成。涉及用户可见行为的 WI**必须** windows-mcp 真机 E2E（见根 `CLAUDE.md` 手工测试纪律），截图存 `plans/2026-06-20-agent-loop-optimization/evidence/`。
- 真机测 worktree 代码必须给 Tauri 进程注入 `DESKPET_BACKEND_DIR` + `DESKPET_PYTHON`，日志确认 `[backend_launch] Dev python=...`（见 `CLAUDE.md` 坑 #8）。

### 1.5 回滚
- 每个 WI 都用 config flag 或参数默认值守门，**关掉 flag = 回到旧行为**。回滚 = 单 commit revert。

---

## 2. WI-1 — `tool_choice` 协议级硬约束

### 2.1 现状（读码核实）
- `backend/providers/openai_compatible.py:237` `async def chat_with_tools(...)`；构造 request body 时 `tool_choice` **硬编码 `"auto"`**：
  - 非流式：`backend/providers/openai_compatible.py:345` `payload["tool_choice"] = "auto"`
  - 流式：`backend/providers/openai_compatible.py:691` `payload["tool_choice"] = "auto"`
- `backend/llm/openai_adapter.py:205` **已支持** `request["tool_choice"] = kwargs.get("tool_choice", "auto")`（fallback 路径已具备透传能力）。
- `backend/agent/agent_loop.py:898` chain mode 调 `prov.chat_with_tools(...)` 只传 `tools/max_tokens/temperature/response_format`，**未传 tool_choice**。
- 守门「必须收尾」的硬点：`agent_loop.py` 的 selfcheck tier3（`_SELFCHECK_TIER3`，`agent_loop.py:118-128`）与 `verify_exhausted`、completion/verify nudge —— 现在全是注入 system 文字，模型可无视。

### 2.2 目标
当循环判定「本轮必须收尾、禁止再调工具」时，对**下一轮 LLM 调用**传 `tool_choice="none"`，协议层强制模型只能产出文本、不能 tool_use。把"软提醒"升级为"硬约束"。

### 2.3 代码改动（逐处）

**(a) provider 透传** — `backend/providers/openai_compatible.py`
- `chat_with_tools(...)` 签名增 `tool_choice: str | None = None`（kw-only，放在现有 kw 后）。
- 345 行：`payload["tool_choice"] = "auto"` → `payload["tool_choice"] = tool_choice or "auto"`
- 691 行：同上。
- 注意：`"none"` 是 OpenAI 兼容协议合法值（中转站需支持；若中转站不认 `none`，见 §2.6 兜底）。

**(b) agent_loop 决策 → 透传** — `backend/agent/agent_loop.py`
- 在 `run()` 局部加状态：`_force_finish_next: bool = False`（初值 False，放在 `tools_used_count` 附近，`agent_loop.py:604` 区域）。
- chain mode 调用处（`agent_loop.py:898`）增参数：
  ```python
  raw = await prov.chat_with_tools(
      working_messages,
      tools=tool_schemas or None,
      max_tokens=int(llm_kwargs.get("max_tokens", 8192)),
      temperature=llm_kwargs.get("temperature"),
      response_format=llm_kwargs.get("response_format"),
      tool_choice=("none" if _force_finish_next else None),  # ← 新增
  )
  ```
- 非流式 fallback（`agent_loop.py:1050` `self.llm.chat_with_fallback(...)`）：把 `tool_choice` 塞进 `llm_kwargs`：
  ```python
  if _force_finish_next:
      llm_kwargs = {**llm_kwargs, "tool_choice": "none"}
  ```
  （registry→adapter 已支持 kwargs 透传，见 2.1。stream 路径同理塞 kwargs。）
- **置位时机**：在 selfcheck tier3 注入处（`iteration >= _SELFCHECK_TIER3_AT`，`agent_loop.py:858-877` 区块内）设 `_force_finish_next = True`；在 `verify_exhausted` 即将强退前若改为「再给一轮纯文本收尾」也可置位（本 WI 先只接 tier3，verify 分支留 §12 follow-up）。
- **复位**：每轮开头复位 `_force_finish_next = False`，仅在当轮判定需要时置 True（避免粘连）。

### 2.4 配置 flag
- `config.toml` 加 `[agent] force_finish_tool_choice = true`（默认 true；置 false → 永不传 `none`，回到旧行为）。读取处随 build_agent。
- AgentLoop 构造增 `force_finish_via_tool_choice: bool = True` 参数；为 False 时 §2.3(b) 的置位逻辑短路。

### 2.5 测试规格（`backend/tests/test_wi1_tool_choice.py`，新建）
- `test_provider_passes_tool_choice_none`：mock httpx，断言传 `tool_choice="none"` 时 request body 的 `tool_choice == "none"`。
- `test_provider_default_is_auto`：不传 → body `tool_choice == "auto"`（BC）。
- `test_tier3_sets_force_finish`：构造迭代到 `_SELFCHECK_TIER3_AT` 的 loop（mock LLM 一直要 tool），断言第 tier3 轮调用 provider 时 `tool_choice == "none"`。
- `test_flag_off_never_forces`：`force_finish_via_tool_choice=False` → 永远不传 none（BC）。

### 2.6 风险 / 兜底
- **中转站不认 `none`**：少数兼容实现只认 `auto`/`required`/对象形式。兜底：provider 内 try 一次，若 relay 返 400 且 message 含 `tool_choice`，降级为不传 + 在 messages 末尾追加强提示（记 metric `tool_choice_none_unsupported`）。该兜底列为 §12 必做补丁项。
- BC：默认行为不变（只有 tier3 等硬点才置 none），现有测试不应破。

### 2.7 DoD
单测全绿 + BC 回归绿 + 真机：构造一个会跑到 tier3 的长任务，log 确认某轮 `tool_choice=none`、该轮模型确实只出文本收尾（§9 E2E-1）。

---

## 3. WI-2 — 每轮结构化 trace（jsonl）

### 3.1 现状
- `agent_loop.py:44` `logger = logging.getLogger("deskpet.agent.loop")`；日志零散，**tool_call 的 args 完全不记**，异常 `str(exc)[:200]` 截断。无「每轮 LLM I/O 快照」。
- 无统一 trace 落盘。`metrics_sink` 是白名单事件、非 trace。

### 3.2 目标
每个 iteration 落一条结构化 jsonl trace，含：iter、输入 messages 摘要（角色+长度+末条预览）、LLM 输出（content 预览 + stop_reason）、tool_calls（name + **完整 args** + 结果 ok/err 摘要）、gate 决策、各守门结论、usage。dev 模式开，prod 默认关。

### 3.3 代码改动
**新建 `backend/agent/trace.py`**：
```python
class IterationTracer:
    def __init__(self, *, enabled: bool, trace_dir: Path, session_id: str, task_id: str): ...
    def record(self, event: dict) -> None:  # append 一行 json 到 <trace_dir>/<task_id>.jsonl
    # 字段约定: {"ts": <由调用方传, 不在 trace 内部取时间>, "iter", "kind", ...}
```
- ⚠️ Workflow/沙箱里 `Date.now()` 不可用 —— 但这是后端 Python 进程，**可用** `time.time()`；trace 时间戳由 trace.py 内部 `time.time()` 取（与 agent_loop 主逻辑无关，纯旁路）。
- `trace_dir`：`<user_data>/traces/`。user_data_dir 经 build_agent 注入（agent_loop 当前不直接持有 → 由构造参数传入，见下）。

**`agent_loop.py` 接线**：
- 构造增参数 `tracer: Optional[Any] = None`（BC：None → 零开销，所有 record 调用前 `if self._tracer:`）。
- 在 `run()` 关键点插 `self._tracer and self._tracer.record({...})`：
  1. 迭代开始：`{iter, kind:"iter_start", msg_count, est_tokens}`
  2. LLM 返回后（`agent_loop.py:1078` totals 累加附近）：`{iter, kind:"llm_out", stop_reason, content_preview, tool_calls:[{name,args}], usage}`
  3. 每个工具结果（`agent_loop.py:1521` dispatch_results 循环内）：`{iter, kind:"tool_result", name, args, ok, result_preview}`
  4. 守门结论（completion/verify/goal 各分支）：`{iter, kind:"gate", which, passed, reason}`
  5. 终止（FinalEvent / ErrorEvent 前）：`{iter, kind:"end", reason, gate_summary: self._gate.summary()}`

**build_agent 接线**（`main.py`）：
- 读 `config.toml [agent] trace_enabled`（默认 false）；为 true 时构造 `IterationTracer(enabled=True, trace_dir=user_data/traces, ...)` 注入。

### 3.4 测试规格（`backend/tests/test_wi2_trace.py`）
- `test_tracer_writes_jsonl`：enabled，跑一轮 mock loop，断言 jsonl 文件有 `iter_start`/`llm_out`/`end` 行，且 `tool_calls[0].args` 完整保留。
- `test_tracer_disabled_no_file`：tracer=None → 无文件、无异常（BC）。
- `test_tracer_records_full_args`：工具 args 含长字符串 → trace 里 args 不被截断（与 ContextManager 的 LLM 侧截断区分）。

### 3.5 风险
- trace 体积：长任务 jsonl 可能大 → 加单文件行数上限（如 2000）+ 轮转。列 §12。
- 隐私：args 可能含敏感内容 → trace 仅 dev/user_data 本地，不进 diagnostic bundle（确认 bundle 收集路径不含 traces/）。

### 3.6 DoD
单测绿 + 手动开 flag 跑一个多轮任务，`<user_data>/traces/<task>.jsonl` 能完整还原每轮 I/O（§9 E2E-2，可纯后端验证）。

---

## 4. WI-3 — 阶段化提示词 + 收尾自查

### 4.1 现状
- persona 在 `backend/deskpet/agent/assembler/components/persona.py`：companion persona（~L23-30）、code persona（~L36-63，已有「第 0 步分意图」「澄清→计划→执行→验证」骨架）。`_resolve_persona(config)`（~L83-117）选用。
- 收尾自查：`agent_loop.py` 有 selfcheck（迭代中提醒，非收尾），FinalEvent 前无强制自查。

> ⚠️ persona.py 行号来自子代理测绘，实现前 grep `_resolve_persona` / `code` 关键字确认。

### 4.2 目标
(a) code persona 补「第 4 步·收尾自查清单」（业界 Cline 四阶段 / OpenHands 五步的收尾对照打勾）；(b) 不照搬 Cline「一次一工具串行」——桌宠并发分发是特性，**保留**。

### 4.3 代码改动
- `persona.py` code persona 文本追加（纯文本、只增不改既有）：
  ```
  【第 4 步 · 收尾自查】说"我完成了"之前，逐项对照原始需求打勾：
    1. 列出用户最初要什么（需求清单）
    2. 逐项标 ✓/✗/部分；有 ✗ 必须回到执行或显式说明放弃理由
    3. 改了代码 → 必须已 verify（跑测试/真机/diff 看生效），不可"应该没问题"
    4. 最终回复给出：做了什么 + 验证证据 + 剩余项
  ```
- companion persona 可选追加一句轻量版自查（避免桌宠日常闲聊也变重，**仅在有工具调用发生时**才提示 → 该条件化逻辑放 §4.4，纯文本则不加）。

### 4.4 测试规格（`backend/tests/test_wi3_persona.py`）
- `test_code_persona_has_closing_checklist`：`_resolve_persona({code mode})` 文本含「收尾自查」关键字。
- `test_companion_persona_unchanged_when_flag_off`：companion 默认文本不变（BC）。

### 4.5 风险 / DoD
- 风险极低（纯提示词）。DoD：单测绿 + 真机 code 模式跑一个改文件任务，观察模型收尾是否产出对照清单（§9 E2E-3）。

---

## 5. WI-4 — Focus Chain：todo 进度周期回灌

### 5.1 现状
- todo 存取：`backend/deskpet/memory/session_db.py` `get_code_todos(session_id)` 返回 `[{content, activeForm, status, sort_order}, ...]`（status ∈ pending/in_progress/completed）。
- 写：`backend/deskpet/tools/code_tools/todo_write_tool.py` `build_todo_write_tool(...)` → `replace_code_todos`。
- 已有周期注入模板：always-on `[目标锚定]`（`agent_loop.py:615-632`）、selfcheck 周期注入（`agent_loop.py:858-877`）。

### 5.2 目标
每 `_TODO_SYNC_EVERY`（默认 8）轮，且本 session 有 code todos 时，注入一条带 ✓/🔄/⏳ 的 todo 进度 system 消息，作为"persistent north star"防 drift（对标 Cline Focus Chain）。

### 5.3 代码改动 — `agent_loop.py`
- 模块常量：`_TODO_SYNC_EVERY = 8`；模板 `_TODO_SYNC_MSG`。
- 构造增参数 `code_todo_getter: Optional[Callable[[str], Awaitable[list[dict]]]] = None`（BC：None → 整段跳过）。build_agent 用与 completion_probe 同源的 `_sdb.get_code_todos` 闭包注入。
- 在循环内 selfcheck 注入区块之后（`agent_loop.py:877` 后）加：
  ```python
  if self.code_todo_getter is not None and iteration % _TODO_SYNC_EVERY == 0:
      try:
          todos = await self.code_todo_getter(session_id)
      except Exception:
          todos = []
      if todos:
          lines = []
          for t in todos:
              mark = {"completed":"✓","in_progress":"🔄"}.get((t.get("status") or "").lower(), "⏳")
              lines.append(f"  {mark} {(t.get('content') or '')[:80]}")
          working_messages.append({"role":"system","content": _TODO_SYNC_MSG.format(body="\n".join(lines))})
          logger.info("wi4_todo_sync sid=%s iter=%d n=%d", session_id, iteration, len(todos))
  ```
- 与 `[目标锚定]` 互补：anchor=目标常驻；focus chain=带勾选状态的进度周期回灌。两者不重复（anchor 不含 todo 勾选状态）。

### 5.4 测试规格（`backend/tests/test_wi4_focus_chain.py`）
- `test_todo_sync_injected_every_n`：getter 返 3 条 todo，mock loop 跑到第 8 轮 → working_messages 出现含 ✓/🔄/⏳ 的 system 消息。
- `test_no_getter_no_injection`：getter=None → 无注入（BC）。
- `test_empty_todos_no_injection`：getter 返 [] → 不注入。

### 5.5 风险 / DoD
- 风险：长任务多次注入堆积 → 每次只注最新一条、且复用同一标记格式（可后续去重，记 §12）。
- DoD：单测绿 + 真机 code 模式多轮任务，log 见 `wi4_todo_sync`，模型收尾未漏 todo（§9 E2E-4）。

---

## 6. WI-5 — 触发式知识注入

### 6.1 现状（重要：部分已具备）
- `backend/deskpet/skills/skill_matcher.py:233` `match_async(query, skills)` **已有** trigger 关键词逻辑：`skill.triggers` 命中 query → sim 提到 `_TRIGGER_SIM=0.95`（`skill_matcher.py:35`）。
- `backend/deskpet/agent/assembler/components/skill.py` `SkillComponent.provide(ctx)`：`auto_enabled=True` 时按相似度排序；`auto_enabled=False` 时只回 desc 列表。**body 是否真正 inline 进 messages 需确认**（子代理称 L150+ 有 LRU+budget 草稿）。

> ⚠️ 实现前必做：读 `skill.py` 全文 + `skill_matcher.py` 确认「trigger 命中后 body 是否真的拼进 Slice」。若已拼 → 本 WI 缩为「补一组轻量知识片段 + 调阈值」；若没拼 → 需补 body inline。

### 6.2 目标
让「用户消息命中关键词 → 确定性注入对应知识/规则块」可用（区别于纯向量概率召回）。复用已有 trigger 机制，补足两点：(a) 确保命中后 body 真 inline；(b) 提供一组桌宠领域「轻量知识片段」（如「生成 PPT 的注意事项」「调试 windows 路径」），带 triggers。

### 6.3 代码改动
- **若 body 未 inline**：在 `SkillComponent.provide` 中，对 `match_async` 返回的 sim ≥ 强阈值（默认 0.55）项调 `loader.read_body(name)` 拼入 Slice text，带 token 预算上限（默认 25k，溢出按 sim 降序丢）。flag `[skills] inline_triggered_body=true`。
- **知识片段载体**：复用现有 skill 目录格式（`SKILL.md` + frontmatter `triggers:`），新增一个 `knowledge/` 类别（`user-invocable: false`，纯背景知识）。先放 2-3 个示例片段。
- BC：flag off → 行为不变。

### 6.4 测试规格（`backend/tests/test_wi5_trigger_inject.py`）
- `test_trigger_keyword_inlines_body`：注册一个带 `triggers:[ppt]` 的知识片段，query 含 "ppt" → provide 返回的 Slice 含该 body。
- `test_below_threshold_not_inlined`：无关 query → 不注入。
- `test_token_budget_drops_lowest`：多个命中超预算 → 保留高 sim、丢低 sim，且不超预算。
- `test_flag_off_bc`：flag off → 与改动前一致。

### 6.5 风险 / DoD
- 风险：body inline 撑爆 context → 严格 token 预算 + 与 ContextManager 压缩协同（知识片段标记为可压/不可压需定，记 §12）。
- DoD：单测绿 + 真机：说一句含触发词的话，log 确认对应知识块被注入（§9 E2E-5，可后端 assemble 验证）。

---

## 7. WI-6 — SEARCH/REPLACE 编辑 + 降级匹配 + did-you-mean

### 7.1 现状
- `backend/deskpet/tools/os_tools/edit_file.py` `edit_file(args, task_id)`：精确字符串匹配 `old_string`→`new_string`，`count==0` 报错、`count>1 && !replace_all` 报错（~L95-119）。成功返回 `{"replacements", "path"}`，失败返回 `{ok:false, hint, examples}`（~L59-67）。
- 现状是**精确匹配**，无降级、无 did-you-mean。曾踩输出截断 → permanent_tool_error（`agent_loop.py:901-910` 注释）。

### 7.2 目标
给 `edit_file` 增「降级匹配 + did-you-mean 反馈」（对标 Aider editblock 四层）；可选支持多块 SEARCH/REPLACE。**只改局部、不重写全文** → 配合已有局部编辑，降低输出截断风险。

### 7.3 代码改动 — `edit_file.py`
- 精确匹配失败时，按序降级（新增、不改既有精确路径）：
  1. **忽略前导/尾随空白**：对 `old_string` 与文件各行 strip 后比对，命中则按原缩进替换。
  2. **行级锚点**：取 `old_string` 首尾各 1-2 行作锚，定位区间。
  3. 仍失败 → 不改文件，返回 `{ok:false, hint, did_you_mean:[最相似的 N 行/段]}`（用 `difflib.get_close_matches` / `SequenceMatcher` 找最接近段落，附行号）。
- 参数增 `fuzzy: bool = True`（默认开降级；置 false → 纯精确，BC）。flag 也可 `[tools] edit_fuzzy_match`。
- 返回结构扩展：降级命中时加 `{"matched_by": "whitespace"|"anchor", "confidence": float}`。
- （可选，§12）多块 `<<<<<<< SEARCH / ======= / >>>>>>> REPLACE` 解析 + order-invariant 应用，单独子项。

### 7.4 测试规格（`backend/tests/test_wi6_edit_fallback.py`）
- `test_exact_match_unchanged`：精确匹配路径行为与现在一致（BC）。
- `test_whitespace_fallback`：`old_string` 缩进与文件差一个 tab/空格 → 降级命中并正确替换。
- `test_anchor_fallback`：中间几行有微小差异、首尾行一致 → 锚点命中。
- `test_no_match_returns_did_you_mean`：完全找不到 → `ok:false` 且 `did_you_mean` 非空、含行号。
- `test_fuzzy_off_is_exact_only`：`fuzzy=False` → 降级不触发（BC）。

### 7.5 风险 / DoD
- 风险：降级误伤（改错位置）→ 降级命中要求 confidence 阈值（如 ≥0.9）+ 多候选时拒绝改并回 did-you-mean。
- DoD：单测绿 + 真机 code 模式让模型改一个缩进/上下文略有出入的编辑，确认降级成功或给出可用 did-you-mean（§9 E2E-6）。

---

## 8. WI-7 — `ask_clarification` 工具（含前端）

### 8.1 现状
- code persona 有「先问一句确认意图」**纯文本指导**，无工具支持。
- 权限弹窗已有「`asyncio.Future` + WS 阻塞问答」机制：`main.py` `_permission_responder`（~L499）建 future 注册 `_permission_pending[request_id]`，发 `permission_request`，`await fut`；前端回 `permission_response` → `set_result` 唤醒（见 [`AgentLoop.md`](../../STATUS/AgentLoop.md) §3.3）。**ask_clarification 直接复用此模式。**

### 8.2 目标
新增阻塞式 `ask_clarification(question, options?)` 工具：模型遇歧义主动反问，经 WS 弹给用户，等回复再继续。桌宠语音/Live2D 场景天然契合。

### 8.3 代码改动
**后端**：
- 新建 `backend/deskpet/tools/code_tools/clarify_tool.py`：`build_ask_clarification_tool(responder)` 工厂，handler 仿 `_permission_responder`——建 future、发 WS `clarification_request{request_id, question, options}`、`await fut`（超时 120s 默认 cancel）、返回 `{"ok":true, "answer": <user text>}`。
- `main.py`：注册工具（走 build_agent 注入 responder）+ 处理 `clarification_response` 消息（查 `_clarify_pending[request_id]` → set_result），与 permission 同构。
- schema：`{question: str (required), options: str[] (optional)}`，permission_category 走轻量（无副作用，可 default-allow）。

**前端（Tauri）**：
- 新 WS 消息类型 `clarification_request` / `clarification_response`：复用现有 permission 弹窗组件改一个「问答/选项」变体（`tauri-app/src/hooks/usePermissionRequests.ts` 同款模式）。
- 语音模式下可 TTS 播报 question（可选，§12）。

### 8.4 测试规格
- 后端 `backend/tests/test_wi7_clarify.py`：
  - `test_clarify_blocks_until_response`：mock responder，handler 发请求后挂起，set_result 后返回 answer。
  - `test_clarify_timeout`：无响应 → 超时返回 `{ok:false, reason:"timeout"}`。
- 前端：手动 E2E（无单测要求，§9 E2E-7）。

### 8.5 风险 / DoD
- 风险：阻塞期间用户发新消息 / 关窗 → 复用 permission 的 fire-and-forget + future 清理逻辑。前端是真实依赖（无前端则工具发出去没人应答 → 超时）。
- DoD：后端单测绿 + 前端弹窗渲染 + 真机：模型调 ask_clarification → 弹窗 → 用户答 → 模型据答继续（§9 E2E-7，必须 windows-mcp 真点击）。

---

## 9. 跨 WI 集成 + 真机 E2E 清单

> 真机用 windows-mcp，每条：截图 + backend log grep + PASS/FAIL。证据存 `evidence/`。

| 编号 | WI | 场景 | 关键证据 |
|---|---|---|---|
| E2E-1 | WI-1 | 触发 tier3 的长任务 | log 某轮 `tool_choice=none` + 该轮纯文本收尾 |
| E2E-2 | WI-2 | 开 trace flag 跑多轮 | `traces/<task>.jsonl` 完整还原（纯后端） |
| E2E-3 | WI-3 | code 模式改文件 | 收尾产出对照清单 |
| E2E-4 | WI-4 | code 模式 ≥8 轮任务 | log `wi4_todo_sync` + 未漏 todo |
| E2E-5 | WI-5 | 说含触发词的话 | log 知识块注入（assemble 层） |
| E2E-6 | WI-6 | 缩进/上下文略偏的编辑 | 降级命中 or 可用 did-you-mean |
| E2E-7 | WI-7 | 模型反问 → 用户答 | 弹窗截图 + 模型据答继续 |

**BC 全量回归**（每个 WI 合入前必跑）：§1.2 的 agent loop 全套 + `test_compaction_bestpractice_upgrade.py` + 工具相关套件，全绿才算。

---

## 10. STATUS / 归档纪律

- 每个 WI 跑通验收（单测 + 真机）→ **同次交付内**更新 [`STATUS/status.md`](../../STATUS/status.md) §3 模块完成度，并在本 plan §0.1 表对应行标 ✅。
- 里程碑级（如整批 A 完成）→ status.md §4 追加一行。
- 本 plan 已登记进 [`plans/index.md`](../index.md)；完成后状态以 status.md 为准。

---

## 11. 排期建议

| 批次 | WI | 串/并 | 预计 |
|---|---|---|---|
| A | WI-1, WI-2, WI-3 | 并行（3 子代理） | 1–1.5d |
| B | WI-4, WI-5, WI-6 | 并行（3 子代理） | 2–3d |
| C | WI-7 | 后端先行、前端跟进 | 2–3d |

> 子代理派单走 Lead-Expert + codex（见全局 `CLAUDE.md`）。每个 WI 一个自包含 HANDOFF（文件路径 + 本节内容 + 验收点）。

---

## 12. 挑战迭代记录 / 修订与补丁（只增不删）

> 本节记录子代理挑战发现的问题与对应补丁。**原文不删，修订在此追加**，确保 plan 可 100% 执行。

### 待办补丁清单（v1 自列，待挑战补充）
- [ ] WI-1：中转站不认 `tool_choice="none"` 的降级兜底（§2.6）。
- [ ] WI-1：把 `verify_exhausted` 也接「再给一轮纯文本收尾」而非直接强退。
- [ ] WI-2：trace 单文件行数上限 + 轮转；确认 diagnostic bundle 不收集 `traces/`。
- [ ] WI-4：todo 进度注入去重（多次注入只保最新）。
- [ ] WI-5：先 grep 确认 SkillComponent 是否已 inline body；知识片段可压/不可压标记。
- [ ] WI-6：多块 SEARCH/REPLACE + order-invariant（可选子项）。
- [ ] WI-7：前端弹窗组件具体改法 + 语音 TTS 播报。

### 挑战轮次记录
- R0（v1 作者自评）：行号多来自子代理测绘，`persona.py`/`skill.py` 行号实现前需 grep 复核；provider 文件已确认为 `backend/providers/openai_compatible.py`（非 `backend/llm/`）。
- R1（3 子代理：正确性 / 完整性 / 运行时）：见 §13 权威修订。

---

## 13. R1 挑战修订（权威 — 与前文冲突处以本节为准）

> 三个独立子代理（正确性核实 / 实施完整性 / 运行时陷阱）对 v1 的挑战结论。**本节为只增内容，不删前文；凡与 §2-§11 冲突，以本节为准。** 已逐条 grep 复核。

### 13.0 全局架构决策（影响所有 WI）

- **A1 · 新依赖一律经 `build_agent` 工厂注入，不污染调用方**。`AgentLoop.__init__` 已有 40+ 可选参数；本批新增的 `tracer` / `code_todo_getter` / `force_finish_via_tool_choice` 仍加在 `__init__`（带安全默认值），但**只有 `build_agent`（`main.py:820+`）负责构造并传入**。其余 8 个构造点（`tool_use_shim.py`、`spawn_team.py:356`、`agent_tool.py:139`、`agent_parallel_tool.py`、`voice_pipeline.py:594`、`scripts/e2e_*.py`、测试 mock）**不传 = 吃默认值 = BC**。实现时逐一确认这 9 处不被新参数破坏。
- **A2 · config flag 走 `raw_config` dict，不改 `config.py` dataclass**。参考 `main.py:872` 的 `verifier_cfg` 读取模式：`cfg.raw_config.get("agent", {}).get("force_finish_tool_choice", True)`。涉及的 flag：`[agent] force_finish_tool_choice`(默认 true)、`[agent] trace_enabled`(默认 false)、`[skills] knowledge_enabled`(默认 false)、WI-6 `fuzzy` 走参数默认不读 config（更 BC）。
- **A3 · BC 测试范式**：mock provider 参考 `backend/tests/test_p5s2_agent_loop_provider_chain.py` 的 `_FakeProvider`；要断言 request body 的 WI-1，把 `_FakeProvider.chat_with_tools` 扩展成记录入参（`self.calls.append(kwargs)`），或 `mock.patch("httpx.AsyncClient.post")` 拦截。每个 WI 配一条 `test_wiN_bc`（flag off → 行为不变）。

### 13.1 WI-1 修订（4 处运行时 bug，必修）

- **B1 · provider 三方法都加 `tool_choice` 参数**：`chat_with_tools`(237)、`chat_stream_with_tools`、`_legacy_chat_with_tools_nonstream` 全部加 `tool_choice: str|None = None`，并把 345/691 的 `"auto"` 改 `tool_choice or "auto"`。（核实：345/691 行确为硬编码 `"auto"` ✅）
- **B2 · 🔴【时序 fix，最高优先】置位必须在该轮 LLM 调用之前**：
  - `_force_finish_next` 在 **for 循环顶部（`agent_loop.py:657`）复位为 False**。
  - 在 selfcheck 判定区块（`858`）里，`if iteration >= _SELFCHECK_TIER3_AT:` 成立时**立即 `_force_finish_next = True`**（在 append tier3 消息的同时、且必须早于 `884` 行的 LLM 调用 try）。
  - 若置位写在 LLM 调用之后 → 当轮仍用旧值传 `"auto"`、tier3 压力延迟一轮甚至失效（v1 §2.3 措辞含糊，以此为准）。
- **B3 · 🔴 流式分支也要注入（v1 漏）**：tool_choice 必须覆盖**三条**调用路径——chain(`898`)、**stream_capable(`~979`)**、nonstream fallback(`1050`)。stream 分支在 `async for ev in self.llm.chat_with_fallback_stream(...)` 调用前加 `if _force_finish_next: llm_kwargs = {**llm_kwargs, "tool_choice": "none"}`。v1 只写了 chain+nonstream，**stream 必须补**。
- **B4 · 🔴 tier3+ 禁止其他守门 nudge（指令打架 fix）**：tier3「禁止 tool_call、立即收尾」与 completion/verify/goal nudge「继续做」自相矛盾。在这三道 nudge 的注入分支前各加前置守卫 `if iteration < _SELFCHECK_TIER3_AT:`（tier3 及以后不再回灌"继续"类 nudge，统一交给强制收尾）。
- **B5 · verify_exhausted 给最后一轮纯文本收尾**（接 §12 待办）：把 `1297-1308` 的直接 `ErrorEvent + return` 改为：置 `_force_finish_next=True` + 注入"verify 失败，本轮必须 end_turn 给用户总结" + `continue`；配一个 `_verify_final_done` latch 防再次进入死循环（最多再来一轮）。
- **B6 · 兜底**：中转站不认 `tool_choice="none"` → provider 内 catch 400（message 含 tool_choice）降级为不传 + messages 末尾追加强提示，记 metric `tool_choice_none_unsupported`。（升入 DoD，见 §2.7 补：「且具备 relay 不支持 none 的兜底」）

### 13.2 WI-2 修订
- **C1 · tracer 经 build_agent 构造**：`AgentLoop.__init__` 加 `tracer=None`；`build_agent` 读 `[agent] trace_enabled`（默认 false），true 时构造 `IterationTracer(trace_dir=paths.user_data_dir()/"traces", ...)`（目录 mkdir(parents,exist_ok)）注入。其余构造点默认 None=零开销。
- **C2 · 时间戳用 `time.time()`**：这是后端 Python 进程，`time.time()` 可用（与"Workflow 脚本里 Date.now 不可用"无关，那是 JS 沙箱约束）。
- **C3 · 轮转先做简单版**：一个 `run(session_id)` 写 `<trace_dir>/<session_id>.jsonl`；行数上限/轮转留 §14 follow-up。确认 `traces/` 不在 diagnostic bundle 收集 glob 内（grep `diagnostic`/`bundle` 确认）。

### 13.3 WI-3 修订（必修，否则测试挂）
- **D1 · persona 第 4 步现状只有"验证"、无自查清单**（核实 `persona.py:36-62`，第 4 步在 L55-58 讲验证逻辑）。需在 `_CODE_MODE_PERSONA_TEMPLATE` **末尾（L62 之后，不改既有 L36-62）追加**「收尾自查清单」段（文本见 §4.3）。否则 `test_code_persona_has_closing_checklist` 找不到关键字必失败。
- **D2 · companion 条件化自查推迟**：companion 的"仅有工具调用时才提示"涉及运行时条件（应在 agent_loop selfcheck 块判 `tools_used_count` 注入，非 persona 文本）→ 拆为 follow-up（§14），WI-3 本体只改 code persona。

### 13.4 WI-4 修订
- **E1 · `code_todo_getter` 经 build_agent 注入**，复用 `_sdb.get_code_todos`（与 completion_probe **同源**）。`AgentLoop.__init__` 加 `code_todo_getter=None`。
- **E2 · 与 `_GOAL_ANCHOR_EVERY=5` 错峰**：todo sync 放 selfcheck 之后（`877` 之后），周期 8。测试需覆盖：iter 5/10（注 anchor）、iter 8/16（注 todo）、iter 40（若同周期不重复堆叠）。anchor=目标常驻、todo=带勾选进度，二者不重。

### 13.5 WI-5 修订（工作量缩小 + 时机澄清）
- **F1 · body inline 已实现**（核实 `skill.py:188-206` 已调 `loader.read_body` 拼入）→ WI-5 **缩为**：补 2-3 个知识片段（`SKILL.md`+frontmatter `triggers:`，`user-invocable:false`）+ flag `[skills] knowledge_enabled` + 测试。工作量 2d→**0.5-1d**。
- **F2 · 触发时机澄清（纠正 challenger 的部分误解）**：知识注入发生在 **chat turn 级的 `assemble()`（`_run_chat` 内一次性 preflight）**，不是 agent loop 每轮迭代。每个**用户消息=一个新 turn=一次 assemble**，所以"用户这条消息里的触发词"能命中——这是符合预期的。challenger 5.1 混淆了"loop iteration"与"conversation turn"。**但**：同一 turn 内若跑很多轮且触发 compaction，注入的知识可能被压掉 → 见 F3。
- **F3 · compaction 保护**：触发命中注入的知识 Slice 标 `triggered/protected` meta；`context_compressor` 见到该标记**不压或优先保**（对齐 §6.5 的"可压/不可压"待办，升为必做）。
- **F4 · 阈值统一**：沿用 `skill_matcher` 既有 trigger 路径（命中即 `_TRIGGER_SIM=0.95`）触发注入，**不引入第二套 0.55 阈值**，避免冲突与长 turn 误注。

### 13.6 WI-6 修订
- **G1 · 降级分层明确**：精确失败（count==0）→ ① strip 前后空白后比对（命中按原缩进替换）→ ② 首尾各 1-2 行锚点定位 → ③ 仍失败返回 `{ok:false, did_you_mean: difflib.get_close_matches(old, file_lines, n=3, cutoff=0.6), matched_by:"none"}`（带行号）。
- **G2 · fuzzy 走参数默认 True、不读 config**（更 BC，参考现有 `replace_all`）；`fuzzy=False` → 纯精确（BC）。降级命中要求相似度阈值（whitespace 命中需唯一；anchor 命中需 confidence≥0.9），多候选则拒改回 did-you-mean。

### 13.7 WI-7 修订（致命竞态，必修）
- **H1 · 🔴 必须走独立 control 通道，禁止混 chat 消息**：`clarification_request`/`clarification_response` 是**独立 WS 消息类型**（非 `chat_v2_*`），处理位置在 recv 主循环 `elif msg_type == "clarification_response"`（在 chat 消息处理之前），与 permission 同构。**原因**：chat 消息会触发"同 sid 新消息 cancel 旧 task"（`main.py:6419` 附近），若澄清答案当 chat 发，会 cancel 掉正在 `await fut` 的 agent task → 答案永远到不了、future 永挂。permission 没此问题正因为它走 control 通道——这是必须照抄的关键点，不是可选项。
- **H2 · future 清理**：handler `try: return await asyncio.wait_for(fut, 120) finally: _clarify_pending.pop(request_id, None)`；超时返 `{ok:false, reason:"user_did_not_respond_in_time"}`，schema description 说明超时风险。
- **H3 · 前端契约（补全，给前端工程师照做）**：
  - 新 WS in：`clarification_request {request_id, question, options?}`；新 WS out：`clarification_response {request_id, answer}`。
  - 前端：复用 `tauri-app/src/hooks/usePermissionRequests.ts` 同款 future/pending 模式新增 `ClarificationDialog`（问题文本 + options 按钮 + 自由输入框）；回复经 **control WS** 发 `clarification_response`。
  - 单测须含 `test_clarify_not_cancelled_by_new_chat`：挂起期间来一条 chat 消息，断言澄清 future 不被 cancel。
- **H4 · TTS 播报推迟**为 follow-up（§14），WI-7 本体只做文字弹窗。

### 13.8 工作量重估（覆盖 §11）
| 批次 | WI | 重估 |
|---|---|---|
| A | WI-1, WI-2, WI-3 | 2-3d（WI-1 含 4 处时序/分支修 + 兜底） |
| B | WI-4, WI-5, WI-6 | 3-4d（WI-5 缩小、WI-6 降级匹配占大头） |
| C | WI-7 | 2-3d（后端 + 前端 control 通道 + 竞态测试） |

---

## 14. Follow-up（本批不做、记录在案，只增不删）
- WI-1：`verify_exhausted` latch 细化；relay 不支持 `none` 的更优雅协商。
- WI-2：trace 行数上限 + 轮转 + archive。
- WI-3：companion 条件化自查（运行时 tools_used 判定注入）。
- WI-4：todo 进度注入去重（多次只保最新）。
- WI-5：知识片段更多领域覆盖；可压/不可压精细分级。
- WI-6：多块 `<<<<<<< SEARCH/=======/>>>>>>> REPLACE` + order-invariant。
- WI-7：语音 TTS 播报 question；options 的语音可达交互。

---

## 15. R2 挑战修订 + 代码实施参考（附录，权威，只增不删）

> R2 两子代理（修订核实 / 终审可执行性）结论：§13 修订方向 100% 正确、关键行号已核实（657/858/898/979/1050/3860/5058/6418 均真实存在）。剩余缺口＝「置位写哪行 / 竞态防护代码 / 参数签名」需要**代码骨架**才能 100% 无歧义。本节补齐。**行号为锚点，实现前 grep 复核（main.py 体量大、易漂移）。**

### 15.1 修正（覆盖 §13.0 A2 笔误）
- config flag 读取正确写法是 **`cfg.raw.get("agent", {}).get("force_finish_tool_choice", True)`**（属性名是 `.raw` 不是 `.raw_config`）。参考真实用例 `main.py:5171` `config.raw.get("companion")`、`main.py:249` `(config.raw.get("llm") or {}).get(...)`。§13.0 A2 引用的 `main.py:872 verifier_cfg` 是 dataclass 属性读法、不对标，以本条为准。

### 15.2 附录 A — 代码骨架

**A-1 · WI-1 `_force_finish_next` 三路径（agent_loop.py）**
```python
# for 循环顶部（657）—— 每轮复位，防粘连
for iteration in range(1, self.max_iterations + 1):
    _force_finish_next = False                      # ← 新增（657 区）

    # ... gate.allows_call / check_budget / compressor ...

    # selfcheck 注入块（858 区）—— 置位必须在此，早于任何 LLM 调用
    if iteration > 0 and iteration % _SELFCHECK_EVERY == 0:
        ...                                          # 既有注入逻辑不动
    if self.force_finish_via_tool_choice and iteration >= _SELFCHECK_TIER3_AT:
        _force_finish_next = True                    # ← 新增置位（在 884 行 LLM try 之前）

    # chain 路径（898）：
    raw = await prov.chat_with_tools(
        working_messages, tools=tool_schemas or None,
        max_tokens=..., temperature=..., response_format=...,
        tool_choice=("none" if _force_finish_next else None),   # ← 新增
    )

    # stream 路径（~979，在 async for 之前）：
    if _force_finish_next:
        llm_kwargs = {**llm_kwargs, "tool_choice": "none"}       # ← 新增
    async for ev in self.llm.chat_with_fallback_stream(working_messages, ..., **llm_kwargs):
        ...

    # nonstream fallback（1050）：
    if _force_finish_next:
        llm_kwargs = {**llm_kwargs, "tool_choice": "none"}       # ← 新增
    response = await self.llm.chat_with_fallback(working_messages, ..., **llm_kwargs)
```
> 关键不变式：置位（858 区）在三处 LLM 调用（898/979/1050）**之前**，行序成立（R2 已核实 858<898<979<1050）。复位在 657，保证不跨轮粘连。

**A-2 · WI-1 tier3+ 禁 nudge（B4）**
```python
# completion nudge（1120 区）/ verify nudge（1179 区）/ goal nudge（1393 区）三处，
# 各自最外层 if 追加前置守卫：
if iteration < _SELFCHECK_TIER3_AT and self.completion_probe is not None and ...:
    ...   # 短任务到不了 30，守卫恒真无副作用；tier3+ 不再回灌"继续"类 nudge
```

**A-3 · WI-1 provider 透传（providers/openai_compatible.py）**
```python
async def chat_with_tools(self, messages, *, tools=None, max_tokens=..., temperature=None,
                          response_format=None, tool_choice: str | None = None):   # ← 加参数
    ...
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"     # 345 行：改这里
# chat_stream_with_tools / _legacy_chat_with_tools_nonstream 同样加参数 + 改 691 行。
# 兜底（B6）：except 捕获 relay 400 且 msg 含 "tool_choice" → 删 payload["tool_choice"] 重试一次
#            + metric record("tool_choice_none_unsupported", {provider, model})
```

**A-4 · WI-7 main.py recv 主循环（H1，必须在 chat 分支之前）**
```python
# 与 permission_response（3860 区）同构，独立 control 分支，位于 chat（5058）之前：
elif msg_type == "clarification_response":
    payload = raw.get("payload", {}) or {}
    rid = payload.get("request_id", "")
    fut = _clarify_pending.get(rid)
    if fut is not None and not fut.done():
        fut.set_result(payload.get("answer", ""))
    # 不创建新 chat task → 不触发 6418 的 cancel → 挂起的 agent task 安全
```

**A-5 · WI-7 clarify_tool handler（H2，future 清理）**
```python
def build_ask_clarification_tool(responder):           # responder 仿 _permission_responder
    async def _handler(args, task_id="", session_id="default"):
        rid = _new_id()
        fut = asyncio.get_running_loop().create_future()
        _clarify_pending[rid] = fut
        try:
            await responder.send({"type": "clarification_request",
                                  "payload": {"request_id": rid,
                                              "question": args["question"],
                                              "options": args.get("options", [])}})
            answer = await asyncio.wait_for(fut, timeout=120)
            return json.dumps({"ok": True, "answer": answer})
        except asyncio.TimeoutError:
            return json.dumps({"ok": False, "reason": "user_did_not_respond_in_time"})
        finally:
            _clarify_pending.pop(rid, None)            # ← 必有，防泄漏
    return _handler, _SCHEMA
```

**A-6 · WI-4 todo sync 块（agent_loop.py，selfcheck 之后 877 后）**
```python
if self.code_todo_getter is not None and iteration % _TODO_SYNC_EVERY == 0:
    try: todos = await self.code_todo_getter(session_id)   # get_code_todos 已是 async ✅
    except Exception: todos = []
    if todos:
        lines = [f"  {{'completed':'✓','in_progress':'🔄'}}.get(s,'⏳')} {c[:80]}"
                 for t in todos for c,s in [(t.get('content') or '', (t.get('status') or '').lower())]]
        working_messages.append({"role":"system","content": _TODO_SYNC_MSG.format(body="\n".join(lines))})
```

**A-7 · WI-6 edit_file 降级三层（edit_file.py，count==0 时）**
```python
# 精确失败后按序：
# ① whitespace：strip 每行后比对；命中行唯一 → 按原缩进替换；多命中 → 拒，转 ③
# ② anchor：取 old 首尾各 1-2 行定位区间；SequenceMatcher.ratio()≥0.85 且唯一 → 替换；否则 ③
# ③ did_you_mean：
import difflib
cand = difflib.get_close_matches(old_string, file_lines, n=3, cutoff=0.6)
return _err("no exact match", hint="见 did_you_mean", extra={
    "matched_by": "none",
    "did_you_mean": [{"line": file_lines.index(c)+1, "text": c} for c in cand]})
# fuzzy=False → 跳过 ①②③，仅精确（BC）
```

### 15.3 附录 B — 新增参数 / flag 签名表

| WI | AgentLoop.__init__ 新参数（带默认） | build_agent 注入来源 | config flag（`cfg.raw`） |
|---|---|---|---|
| WI-1 | `force_finish_via_tool_choice: bool = True` | 读 flag | `[agent].force_finish_tool_choice`=true |
| WI-2 | `tracer: Optional[Any] = None` | flag 为 true 时构造 `IterationTracer(trace_dir=paths.user_data_dir()/"traces")` | `[agent].trace_enabled`=false |
| WI-4 | `code_todo_getter: Optional[Callable[[str], Awaitable[list[dict]]]] = None` | `_sdb.get_code_todos`（与 completion_probe 同源） | 无（有 getter 即开）或 `[agent].todo_sync_enabled` |
| WI-5 | 无（复用 skill_loader/matcher） | — | `[skills].knowledge_enabled`=false |
| WI-6 | 无（工具参数 `fuzzy=True`，不读 config） | — | 无 |
| WI-7 | 无（工具经 build_agent 注册 + responder） | clarify responder（仿 permission） | 无（responder 为 None 则不注册） |
| WI-3 | 无（纯 persona 文本） | — | 无 |

> AgentLoop 仅 +3 参数（WI-1/2/4），全部 Optional/带默认 → 其余构造点不传即 BC。

### 15.4 AgentLoop 构造点核对清单（R2 实测，非测试 7 处）

| # | 文件:行 | 是否传新参数 |
|---|---|---|
| 1 | `main.py:975`（`build_agent` 内 `_AgentLoop(...)`） | ✅ 唯一注入点，传 WI-1/2/4 三参数 |
| 2 | `tool_use_shim.py:23` | ❌ 默认值（BC） |
| 3 | `spawn_team.py:356` | ❌ 默认值（BC） |
| 4 | `agent_tool.py:139` | ❌ 默认值（BC） |
| 5 | `voice_pipeline.py:594` | ❌ 默认值（BC） |
| 6 | `scripts/e2e_stage_a.py:135` | ❌ 默认值（BC） |
| 7 | `scripts/e2e_stage_a_full.py:93` | ❌ 默认值（BC） |
| + | `backend/tests/` 多处 mock | ❌ 默认值（BC） |

> `agent_parallel_tool.py` 经核实**不直接构造 AgentLoop**（它走 agent_tool 派子代理）。实现前 grep `AgentLoop(` / `_AgentLoop(` 复核全量。

### 15.5 补充测试规格（时序 / 竞态 / BC，所有 WI）

- **时序**：`test_force_finish_resets_per_iteration`（每轮复位）；`test_tier3_forces_tool_choice_none_all_paths`（chain/stream/nonstream 三路径都传 none）；`test_tier3_suppresses_other_nudges`（tier3+ 不注 completion/verify/goal nudge）。
- **竞态**：`test_clarify_not_cancelled_by_new_chat`（澄清挂起期来一条 chat 消息，断言 future 未被 cancel）；`test_clarify_timeout_cleans_pending`（超时后 `_clarify_pending` 不残留）。
- **BC（每 WI 一条）**：WI-1 `force_finish_via_tool_choice=False`→永不传 none；WI-2 tracer=None→无文件无开销；WI-3 companion 文本字节一致；WI-4 getter=None→无注入；WI-5 flag off→只返 desc；WI-6 fuzzy=False→纯精确；WI-7 responder=None→工具不注册。
- **E2E-1 构造法**（§9 补具体）：单测级用 `max_iterations=35` + mock LLM 每轮返 `stop_reason="tool_use"` 永不收尾 → 必然跨过 `_SELFCHECK_TIER3_AT=30` → 断言第 ≥30 轮 provider 收到 `tool_choice="none"`。真机级则给一个明显超长的任务诱导多轮。

### 15.6 R2 收敛判定
- R2 终审：补附录 A/B 后从「60-70%」升至「~90% 可执行」，剩余 ~10% 为「实现前 grep 校准行号」——这是**任何 plan 对活代码库都无法消除**的固有成本，plan 已逐处显式标注 grep 点（§13.0/§15 开头/各构造点）。
- **裁定：达到「有经验工程师可照本 plan 100% 无需再做设计决策执行」的标准**（剩余只是机械的行号核对，非设计空白）。R3 不再新增设计，仅供实现期回填真实行号。

---

## 16. R3 挑战修订（核验 §15 骨架，权威，只增不删）

> R3 两子代理（骨架逐段核验 / 实现者干跑）对 §15.2 附录 A 做最后核验，发现 **3 个骨架自身的真 bug**（非行号漂移）+ 1 处设计空白。本节修正，**与 §15 冲突处以本节为准**。R3 同时确认：A-1 三路径、A-3、A-4 骨架正确可照抄；方法行号 `_legacy_chat_with_tools_nonstream`(292)、`chat_stream_with_tools`(631) 核实无误；§15 全部关键行号(657/858/898/979/1050/1120/1179/1393/3860/5058)再次确认真实。

### 16.1 🔴 修正 A-6（WI-4 todo sync — 原 list comprehension 语法错）
原 §15.2 A-6 的一行式 `{{'completed':'✓',...}}.get(s,'⏳')` 在 f-string 里大括号转义错误、跑不通。**以下显式循环版为准**（与 §5.3 一致）：
```python
if self.code_todo_getter is not None and iteration % _TODO_SYNC_EVERY == 0:
    try:
        todos = await self.code_todo_getter(session_id)   # get_code_todos 已是 async ✅
    except Exception:
        todos = []
    if todos:
        lines = []
        for t in todos:
            content = (t.get("content") or "")[:80]
            status = (t.get("status") or "").lower()
            mark = {"completed": "✓", "in_progress": "🔄"}.get(status, "⏳")
            lines.append(f"  {mark} {content}")
        working_messages.append({"role": "system",
                                 "content": _TODO_SYNC_MSG.format(body="\n".join(lines))})
```

### 16.2 🔴 修正 A-7（WI-6 edit_file 降级 — 缺 file_lines 定义）
原 §15.2 A-7 直接用 `file_lines` 但未定义 → NameError。**降级分支开头必须先建 `file_lines`**，且 did_you_mean 对**行**做匹配：
```python
# count == 0（精确未命中）降级分支：
file_lines = text.split("\n")                  # ← 必须先定义（原骨架漏）
# ① whitespace fallback：strip 每行比对，唯一命中→按原缩进替换；多命中→转 ③
# ② anchor fallback：old 首尾 1-2 行定位，SequenceMatcher.ratio()≥0.85 且唯一→替换；否则→③
# ③ did_you_mean：
import difflib
cands = difflib.get_close_matches(old_string, file_lines, n=3, cutoff=0.6)
return _err("no exact match", hint="见 did_you_mean",
            matched_by="none",
            did_you_mean=[{"line": file_lines.index(c) + 1, "text": c} for c in cands])
```
> 注：真实 `_err(error, hint, **extra)`（`edit_file.py:30`）—— extra 走 kwargs，故上面把 `matched_by`/`did_you_mean` 作为 kwargs 直接传（不是包在 `extra={}` 里）。`fuzzy` 是 `edit_file` **新增**参数（现签名无，需加，默认 True）。

### 16.3 🔴 修正 A-4/A-5（WI-7 responder 契约 — 原 `.send()` 接口未定义，真 BLOCKING）
真实 `_permission_responder`（`main.py:499`）是 **`async def _permission_responder(req) -> PermissionResponse`**：内部 `ws = _control_connections.get(req.session_id)` → `ws.send_json({...})` → 建 future → `await fut`。**不是 `responder.send()` 对象**。WI-7 照同构定义一个**澄清 sender 闭包**，工具 handler 只 await 它：

```python
# main.py 模块级（与 _permission_pending 同处）：
_clarify_pending: dict[str, asyncio.Future] = {}     # ← 新增模块级 dict

# main.py 内构造 sender 闭包（与 _permission_responder 同构）：
async def _clarify_ask(question: str, options: list[str], session_id: str) -> str:
    ws = _control_connections.get(session_id) or _control_connections.get("default")
    if ws is None:
        return ""                                     # 无连接→空答（handler 判空降级）
    rid = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    _clarify_pending[rid] = fut
    try:
        await ws.send_json({"type": "clarification_request",
                            "payload": {"request_id": rid, "question": question, "options": options}})
        return await asyncio.wait_for(fut, timeout=120)
    except asyncio.TimeoutError:
        return ""                                     # 超时→空答
    finally:
        _clarify_pending.pop(rid, None)               # ← 必有，防泄漏

# 工具 handler（clarify_tool.py）只依赖一个 async callable，不依赖 .send 对象：
def build_ask_clarification_tool(ask_fn):             # ask_fn = _clarify_ask
    async def _handler(args, task_id="", session_id="default"):
        answer = await ask_fn(args["question"], args.get("options", []), session_id)
        if answer:
            return json.dumps({"ok": True, "answer": answer})
        return json.dumps({"ok": False, "reason": "user_did_not_respond_or_no_channel"})
    return _handler, _SCHEMA
```
- recv 主循环分支（§15 A-4）不变，仍 `elif msg_type == "clarification_response": fut=_clarify_pending.get(rid); fut.set_result(payload.get("answer",""))`。
- **关键**：`_clarify_ask` 内部 await future、`_clarify_pending` 模块级、回答走独立 control 分支 —— 三者合起来才避开 §13.7 H1 的 cancel 竞态。

### 16.4 修正 A-2（三处 nudge 守卫 — 逐处调括号，非套模板）
R3 确认 completion(1120)/verify(1179)/goal(1393) 三处最外层 `if` 条件各不相同（verify 还有 `getattr` 多条件、goal 内部 1398 还有嵌套 if）。加 `iteration < _SELFCHECK_TIER3_AT and` 时**逐处把它并进该处真实条件的最外层**（必要时整体加括号），不要套同一字符串模板。短任务到不了 30，守卫恒真无副作用（R3 确认）。

### 16.5 消解 WI-7 前端设计空白（用默认决策，避免 punt）
R3 指出 `ClarificationDialog` 的 options/输入框交互是设计空白。**本 plan 拍定默认行为**（实现者无需再问产品）：
- `question` 纯文本渲染（**不**支持 markdown，避免注入）。
- `options` 非空 → 渲染为按钮列表，点击即把该 option 文本作为 answer 发 `clarification_response`。
- **始终**同时显示一个自由输入框（即使有 options），用户可不选按钮、直接输入；回车/确认即发 answer。
- 复用 `tauri-app/src/hooks/usePermissionRequests.ts` 的 pending/resolve 模式，新增 `useClarificationRequests` + `ClarificationDialog`；回复经 **control WS** 发 `{type:"clarification_response", payload:{request_id, answer}}`。

### 16.6 测试基础设施补充（R3 实现者干跑发现）
- WI-1 测试要断言 provider 收到 `tool_choice`：现有 `_FakeProvider`（`test_p5s2_agent_loop_provider_chain.py`）的 `chat_with_tools` 无 tool_choice 形参 → 测试里**扩展一个 mock 子类**：`chat_with_tools(self, *a, tool_choice=None, **kw)` 内 `self.last_tool_choice = tool_choice`，断言之。这是测试基建改造，非生产代码。
- WI-7 `test_clarify_not_cancelled_by_new_chat`：构造挂起的 `_clarify_ask`（future 未 resolve），模拟收到一条 `chat` 消息，断言该 future 未被 cancel（因为走独立分支、不进 `_chat_inflight` cancel 路径）。

### 16.7 R3 收敛判定
- R3 把「可照抄落地」从 §15 的骨架级提升到**逐行无误**：3 个骨架 bug（A-6 语法 / A-7 漏定义 / A-5 responder 契约）已在 §16 修正并给出真实可跑代码；WI-7 唯一的设计空白（前端交互）已用 §16.5 默认决策消解。
- 剩余仅「实现期 grep 复核 main.py 行号」这一**活代码库固有机械成本**，plan 已逐处标注。
- **裁定：plan 已收敛到「有经验工程师可照本 plan + §13/§15/§16 修订，无需再做任何设计决策、直接编码」**。R3 是最后一轮设计层核验；R4 不再需要（无新设计面，只剩落地时的行号回填）。

---
（v1 + R1 + R2 + R3 修订完。plan 收敛至可执行，挑战迭代结束。）
