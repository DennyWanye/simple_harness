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

---
（v1 完，等待挑战迭代）
