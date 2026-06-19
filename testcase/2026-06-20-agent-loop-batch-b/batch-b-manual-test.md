# Agent Loop Batch B — 手工测试文档（WI-4 / WI-5 / WI-6）

> **被测功能**（commit `8ddda29` + `41583ee`，plan `plans/2026-06-20-agent-loop-optimization/00-PLAN.md`）：
> - **WI-4 Focus Chain**：code 任务每 `_TODO_SYNC_EVERY=8` 轮（且 `iteration < _SELFCHECK_TIER3_AT=30`）回灌一条 `[当前任务进度]` todo 进度 system 消息（带 ✓/🔄/⏳）。日志锚点 `wi4_todo_sync sid=… iter=… n=…`。
> - **WI-5 触发式知识注入**：`[skills] knowledge_enabled=true`（默认 false）时，用户消息含知识片段 triggers 关键词 → 该片段正文自动注入上下文（heading「知识片段（自动注入）」，slice 标 triggered/protected 防压）。内置片段：ppt-tips / windows-path-debug / source-check。
> - **WI-6 SEARCH/REPLACE 降级**：`edit_file` 精确未命中且 `fuzzy=true`（默认）时降级 whitespace→anchor 匹配，仍失败返回 `matched_by="none"` + `did_you_mean`；命中返回 `matched_by="whitespace"|"anchor"`。`fuzzy=false`→纯精确（BC）。
>
> **执行方式（HARD）**：真人 / windows-mcp 真实桌宠 App 上模拟鼠标点击 + 键盘输入。禁止 WebSocket / pytest / import 替代 UI 证据（见根 CLAUDE.md 手测纪律）。
> **证据**：`plans/manual-results-2026-06-20-batch-b/`。**最后更新**：2026-06-20

---

## 0. 改动事实基线

| 事项 | 事实 | 出处 |
|---|---|---|
| WI-4 周期常数 | 每 8 轮、iteration<30 | `agent_loop.py:98,908-909` |
| WI-4 日志锚点 | `wi4_todo_sync sid=.. iter=.. n=..` | `agent_loop.py:929` |
| WI-4 注入消息 | `[当前任务进度]\n…` 含 ✓/🔄/⏳ | `agent_loop.py:99,926` |
| WI-5 flag | `[skills] knowledge_enabled` 默认 false | `config.py:385,1105` |
| WI-5 注入 heading | "知识片段（自动注入）" + knowledge_loaded_count | `skill.py:225,232` |
| WI-5 片段 | ppt-tips / windows-path-debug / source-check | `backend/deskpet/skills/builtin/*/SKILL.md` |
| WI-6 fuzzy 参数 | schema + handler `args.get("fuzzy",True)` | `registration.py` edit_file schema、`edit_file.py:128` |
| WI-6 降级返回 | `matched_by` ∈ whitespace/anchor/none + did_you_mean | `edit_file.py:184,187,116,207` |
| HARD GATE | `[backend_launch] Dev python=…` | backend_launch.rs |

---

## 1. 环境启动（前置）
同 Batch A §1（taskkill → 注入 `DESKPET_BACKEND_DIR=G:\projects\deskpet\backend` + `DESKPET_PYTHON` → `npx tauri dev` → 验 `[backend_launch] Dev python=…`）。
**WI-5 用例额外前置**：`<user_data>\config.toml` 的 `[skills]` 段加 `knowledge_enabled = true`，重启。

---

## TC-0 — 环境 HARD GATE
启动日志含 `[backend_launch] Dev python=...backend_dir=G:\projects\deskpet\backend`。**PASS**：是 Dev python 非 Bundled exe。**需 windows-mcp：是（启动）**

---

## TC-1 — ★ WI-4 Focus Chain：多步 code 任务 ≥8 轮回灌 todo 进度
**类型**：UI 真测｜**需 windows-mcp：是**

**目的**：验证 code 任务跑过 8 轮时，自动回灌 `[当前任务进度]` 进度快照。

**前置**：进 code 模式（toolbar terminal 图标「进入 Code 模式」）。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | 进 code 模式，Screenshot | — | code 仪表盘 |
| 2 | Click code 输入框 → Clipboard → Ctrl+V → Enter | `请依次（一个一个、不要批量）用工具完成：在当前项目建 a1.txt~a6.txt 共6个文件各写自己的文件名，每建一个就用 read_file 确认一次，最后逐个删除并确认` | code agent 多轮执行（≥8 次工具迭代） |
| 3 | WaitFor 收尾 | — | 任务完成 |
| 4 | grep tauri-dev 日志 `wi4_todo_sync` | — | **出现 ≥1 条 `wi4_todo_sync sid=.. iter=8.. n=..`**；working_messages 注入过 `[当前任务进度]` |

**PASS**：日志见 `wi4_todo_sync`（iter 为 8 的倍数且 <30）+ 任务正常收尾。**FAIL**：跑过 8 轮却无 wi4_todo_sync。

---

## TC-2 — WI-4 BC：短任务（<8 轮）不回灌
**类型**：UI 真测｜**需 windows-mcp：是**

| 步 | 动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | code 输入框发简单单步任务 | `在当前项目建 short_b.txt 写一行 ok` | 1-3 轮完成 |
| 2 | grep 本轮 `wi4_todo_sync` | — | **无**（轮数没到 8） |

**PASS**：短任务无 wi4_todo_sync。

---

## TC-3 — ★ WI-5 触发知识注入（knowledge_enabled=on）
**类型**：UI 真测｜**需 windows-mcp：是**

**前置（真机发现：三重激活条件）**：WI-5 复用 FP-5 auto_disclosure body-inline 路径，需**同时**：① `[skills] knowledge_enabled = true`（WI-5 新增）② `[skills.auto_disclosure] enabled = true`（FP-5 已有）③ 请求归到 prefer 含 `skill` 的 task_type（`policy.py` 仅 `task` 类含；chat/code/web_search/plan 不含）。设好①②后重启。
> 注：`帮我做ppt` 真机被分类为 `chat`（无 skill）→ SkillComponent 不运行 → 知识不注入。这是已有 FP-5 task-门控，非 WI-5 范围。WI-5 逻辑由 6 单测覆盖。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | 桌宠 companion 输入框 → Clipboard → Ctrl+V → Enter | `帮我做一个 ppt 介绍新能源汽车` | 触发 ppt-tips 知识片段 |
| 2 | grep 日志 `knowledge_loaded_count` / "知识片段" | — | `knowledge_loaded_count>=1`（assemble 注入 ppt-tips 正文） |
| 3 | （可选）问含「路径」「windows」 | `windows 下反斜杠路径老出问题怎么调试` | 触发 windows-path-debug |

**PASS**：含触发词的请求使对应知识片段被注入（日志 knowledge_loaded_count≥1）。**FAIL**：触发词命中却无注入。

---

## TC-4 — WI-5 flag off（默认）不注入（BC）
**类型**：UI 真测｜**需 windows-mcp：是**

**前置**：config 删/注释 `knowledge_enabled`（恢复默认 false），重启。

| 步 | 动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | companion 发含触发词请求 | `帮我做一个 ppt` | 正常回复 |
| 2 | grep `knowledge_loaded_count` | — | =0 或无注入（知识片段不加载） |

**PASS**：flag off 时知识片段不注入、对话照常。

---

## TC-5 — WI-6 edit_file 降级匹配（code 真编辑）
**类型**：UI 真测 + 自动化旁证｜**需 windows-mcp：是**

**目的**：验证 code 模式真编辑文件时 fuzzy 降级可用（缩进/空白略偏仍能改），失败给 did_you_mean。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | code 模式发 | `在当前项目建 edit_demo.py 写：def f():\n    x = 1\n    return x。然后把其中 x = 1 改成 x = 2（注意我给的缩进可能和文件里不完全一致）` | agent 调 edit_file，若 old_string 空白不完全匹配则 fuzzy 降级命中 |
| 2 | WaitFor 收尾 | — | 文件被正确修改为 x = 2 |
| 3 | 看 edit_file tool 结果（开 trace 或 code 面板） | — | 命中（精确或 `matched_by=whitespace/anchor`），最终文件正确 |

**PASS**：编辑成功（含 fuzzy 降级路径可用），或失败时返回可用 did_you_mean。深层三层降级由 `test_wi6_edit_fallback.py`（5 测试）覆盖。

---

## TC-6 — WI-6 did_you_mean（找不到时给建议）
**类型**：自动化覆盖 + GUI 旁证｜**需 windows-mcp：否**

**说明**：构造"old_string 完全不存在"在 GUI 上不稳定，由 `test_wi6_edit_fallback.py::test_no_match_returns_did_you_mean` 覆盖（返回 `matched_by=none` + did_you_mean 含行号）。GUI 中若 agent 编辑误用了不存在的串，会收到 did_you_mean 并自行纠正。

**PASS**：自动化绿。

---

## 9. 汇总

| Case | 范围 | 类型 | 需 windows-mcp | 判定 |
|---|---|---|---|---|
| TC-0 | HARD GATE | 启动日志 | 是 | _待执行_ |
| TC-1 | ★WI-4 多步任务回灌 todo | UI 真测 | 是 | _待执行_ |
| TC-2 | WI-4 短任务不回灌(BC) | UI 真测 | 是 | _待执行_ |
| TC-3 | ★WI-5 触发知识注入 | UI 真测 | 是 | _待执行_ |
| TC-4 | WI-5 flag off 不注入(BC) | UI 真测 | 是 | _待执行_ |
| TC-5 | WI-6 fuzzy 降级真编辑 | UI 真测+旁证 | 是 | _待执行_ |
| TC-6 | WI-6 did_you_mean | 自动化+旁证 | 否 | 自动化 5/5 ✅ |

## 10. 自动化基线
`test_wi4_focus_chain.py`(4) / `test_wi5_trigger_inject.py`(6) / `test_wi6_edit_fallback.py`(5) 全绿；BC 回归 241 passed。
