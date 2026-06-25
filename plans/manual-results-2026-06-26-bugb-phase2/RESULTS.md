# BUG-B Phase 2 — windows-mcp 真机手测结果（复活组装期 classifier）

> **执行**：2026-06-26（本地 06:26–06:39）
> **方式**：真坐标鼠标点击 + 剪贴板中文输入(Ctrl+V) + 真点发送 + tauri-phase2.log 结构化事件 grep。
> **被测进程**：worktree 当前代码（含 Phase 2 改动；`[backend_launch] Dev python` 非 frozen）。
> **硬证据锚点**：`assembler_task_classified task_type=...`（assembler.py:251，组装 bundle task_type 唯一硬证据）。
> 日志：`../manual-results-2026-06-26-bugb-phase1/tauri-phase2.log`（与 Phase1 同启动器，仅换 LOGNAME）。

## 结果总览：★3 全 PASS（核心 BUGB-6 通过），0 FAIL

| TC | ★ | 输入 / 场景 | 关键证据（log）| 判定 |
|---|---|---|---|---|
| TC-E1 | ★ | boot 自检（classifier 复活接电）| `assembler_classifier_llm_injected provider=...` ×1 + **`assembler_classifier_llm_inject_failed` ×0** | **PASS** |
| TC-A1 | ★ | 帮我看这段 python 为什么会 IndexError | `assembler_task_classified task_type='code'`（非 chat）| **PASS** |
| TC-A2 | ★ | 修一下这个 bug 它老是崩 | `assembler_task_classified task_type='code'`（debug→code bundle，非 chat）| **PASS** |
| TC-A3 | | 钠离子电池的工作原理是什么（中性真问题）| `assembler_task_classified task_type='chat'`（**llm tier 判定**：纯事实问答归 chat；非"无脑 default chat"——词法地板会给 task，这里是上游 tier 真出结果，证 classifier 活着）| **PASS（观察）** |
| TC-D | | 你好（闲聊，两层判定器协同）| `assembler_task_classified task_type='chat'` **且** `intent_triage.allowlist_hit`（同轮各打各 log，不互斥）| **PASS** |
| TC-E2 | | 不回归：发上述各问后桌宠均正常回复（"你好呀，我在呢～今天想让我帮你做什么？"）| 无 boot 崩溃、无 `assembler_classifier_llm_inject_failed`、运行期 `classifier.llm_failed`/`classifier.llm_timeout` 均 0 | **PASS** |

## ★ 一票否决逐条
- **TC-E1 classifier 复活接电**：✅ boot 出 `assembler_classifier_llm_injected`，无 inject_failed。WI-5 注入真生效。
- **TC-A1 真 code → 组装非 chat**：✅ `task_type='code'`。修复前 `llm_registry=None`+embed 撞锁 → 恒 default `chat`；现 → `code`。BUGB-6 招牌通过。
- **TC-A2 真 debug → 组装非 chat**：✅ `task_type='code'`（"bug/崩"被识别为 code 能力侧）。

## 关键结论
- **classifier 复活**：4 次 `assembler_task_classified` = code / chat / code / chat。两个 **code** 对应真 code/debug 问题（TC-A1/A2），两个 **chat** 对应纯事实问答（LLM 判 conversational）与寒暄（TC-A3/D）。即：真 code/debug 拿到 code bundle、casual/chitchat 拿到 chat bundle —— 正是 Phase 2 目标（修复前真 code 也恒 chat）。
- **WI-5 R2 命门**：llm tier 真出结果（path=llm/embed/rule 均见非-default 判定），无 `classifier.llm_timeout`（timeout 8s 适配 gpt-5.5 thinking 生效）、无 `classifier.llm_failed`（未踩 BUG-A，relay 链路稳）。
- **两层判定器协同**：TC-D "你好" 同轮 `assembler_task_classified task_type=chat`（组装）+ `intent_triage.allowlist_hit`（短路，Phase1）并存不打架。
- **WI-6 词法地板 fail-closed**：单测 `test_classifier_lexical_floor_branches` 已证三分支（chat/code/task）；E2E 因 llm tier 现已接电，多数请求在上游 tier 即出结果，地板兜底仅在 rule+embed+llm 全失效时触发（难在真机强制，单测覆盖充分）。

## 证据
- 截图：`screenshots/P2-code-classified-replies.png`（含 code 问题回复）。
- 日志：`../manual-results-2026-06-26-bugb-phase1/tauri-phase2.log`（assembler_task_classified=4 / injected=1 / inject_failed=0 / llm_failed=0 / llm_timeout=0）。
- grep 工具：`../manual-results-2026-06-26-bugb-phase1/p2grep.py`。

**结论**：Phase 2 windows-mcp 真机手测 ★3 全 PASS、0 FAIL。组装期 classifier 在真实运行栈内复活：真 code/debug 问题拿到 `code` bundle（不再恒 `chat`），闲聊/寒暄拿到 `chat` bundle，两层判定器协同无冲突，无回归。
