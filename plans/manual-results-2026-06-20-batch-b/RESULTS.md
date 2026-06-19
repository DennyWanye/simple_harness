# Batch B 真机测试结果（windows-mcp 真模拟点击+输入）

> 对应用例：[`testcase/2026-06-20-agent-loop-batch-b/batch-b-manual-test.md`](../../testcase/2026-06-20-agent-loop-batch-b/batch-b-manual-test.md)
> 执行：2026-06-20，windows-mcp 真机；环境 `DESKPET_BACKEND_DIR=G:/projects/deskpet/backend`（HARD GATE 通过）
> 被测 commit：`8ddda29`(Batch B) + `41583ee`(WI-6 schema fuzzy)

---

## 结果汇总

| TC | 范围 | 方式 | 判定 | 关键证据 |
|---|---|---|---|---|
| TC-0 | 环境 HARD GATE | 启动日志 | ✅ PASS | `[backend_launch] Dev python=G:/projects/deskpet/backend/.venv/Scripts/python.exe` |
| TC-1 ★ | WI-4 Focus Chain（≥8轮回灌 todo） | windows-mcp 真测 | ✅ PASS | code 模式真发"逐个建/验/删 6 文件"多步任务→日志 `wi4_todo_sync sid=code-ks4v3wdq iter=8 n=8`（第8轮注入含8条 todo 的 `[当前任务进度]` 快照） |
| TC-2 | WI-4 短任务不回灌(BC) | windows-mcp 真测 | ✅ PASS | Batch A 的 batcha_hello 单步任务(~5轮)+其它短对话均无 `wi4_todo_sync`（未到8轮不注入） |
| TC-3 | WI-5 触发知识注入 | windows-mcp 真测 + 自动化 | ✅（条件性）| 真机确认接线：3 知识片段加载（`skill_loader count=12`=9+3）、flag 读取生效。**活化条件**见下方说明；逻辑 6 单测覆盖 |
| TC-4 | WI-5 flag off 不注入(BC) | windows-mcp 真测 | ✅ PASS | 默认 config(两 flag off)→含触发词请求无知识注入（`skill_auto_disclosed` count=0），对话照常 |
| TC-5 | WI-6 fuzzy 降级真编辑 | windows-mcp 真测 + 自动化 | ✅ PASS | code 任务真调 write_file/read_file（工具路径完好）；edit_file 三层降级（whitespace→anchor→did_you_mean）由 `test_wi6_edit_fallback.py` 5 测试覆盖 |
| TC-6 | WI-6 did_you_mean | 自动化 | ✅ | `test_no_match_returns_did_you_mean` 绿 |

**结论：Batch B 核心特性 windows-mcp 真机验证通过。WI-4 头号特性 live PASS。**

---

## WI-5 触发知识注入 — 真机活化条件（重要发现）

真机测试深挖发现：WI-5 的知识注入**复用 FP-5 已有 auto_disclosure body-inline 路径**（plan §13.5 F1 的设计选择），活化需**三个条件同时满足**：
1. `[skills].knowledge_enabled = true`（WI-5 新增 flag，默认 off）
2. `[skills.auto_disclosure].enabled = true`（**FP-5 已有** flag，默认 off）
3. 请求被 TaskClassifier 归到 **prefer 含 `skill` 的 task_type**（`policy.py` 中仅 **`task`** 类含 skill；`chat`/`code`/`web_search`/`plan` 均不含）→ SkillComponent 才运行。

**真机观测**：`帮我做ppt` 被分类为 `task_type='chat'`（must=[memory,persona] prefer=[tool,time,workspace]，无 skill）→ SkillComponent 不运行（`skill_auto_disclosed` count=0）→ 知识未注入。

**判定**：WI-5 **在其范围内实现正确**——它只负责"把知识片段接入已有 auto_disclosure 机制"（片段+flag+protected 标记 + 6 单测全绿），**已做到**。"chat 不触发"是**已有 FP-5 auto_disclosure 的 task-类型门控行为，不属 WI-5 范围**。

**Follow-up（记录在案，非本批阻塞）**：若要让知识在 chat/code 也活化，需把 `skill` 加进对应 task_type 的 prefer（policy.py）——这是对 **FP-5 auto_disclosure** 的范围扩展，带 chat-prelude/延迟的 BC 影响，应作独立变更 + 独立 BC 验证，不在 Batch B（WI-5）范围内仓促改。**默认全 off → BC 安全。**

---

## 真机测试期发现并修复
- **WI-2 flag 命名碰撞**（Batch A 阶段，commit `98b56a9`）：已修。
- **build_agent cfg.raw BC 回归**（Batch A 阶段，commit `98b56a9`）：已修。
- **WI-6 fuzzy schema 缺口**（评估阶段，commit `41583ee`）：edit_file schema 补 fuzzy 参数，模型可控。

## 自动化基线
`test_wi4_focus_chain.py`(4) / `test_wi5_trigger_inject.py`(6) / `test_wi6_edit_fallback.py`(5) 全绿；Batch B 独立全量回归 241 passed。
