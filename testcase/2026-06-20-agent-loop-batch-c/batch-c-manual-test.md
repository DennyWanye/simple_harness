# Agent Loop Batch C — 手工测试文档（WI-7 ask_clarification）

> **被测功能**（commit `4b2d25d`，plan `plans/2026-06-20-agent-loop-optimization/00-PLAN.md` §8/§13.7/§16.3/§16.5）：
> **WI-7 ask_clarification 工具**：agent 遇到意图不清时调 `ask_clarification(question, options?)` → 经**独立 control WS**（`clarification_request`）弹 `ClarificationDialog` → 用户点选项或输入答案 → `clarification_response` 回灌 → agent 据答继续。
> **关键防竞态（§13.7 H1）**：走独立 control 通道（非 chat 消息），答题**不会**触发"同 sid 新 chat 消息 cancel 旧 task"，agent task 不被杀。超时 120s 返回 `{ok:false}`。
>
> **执行方式（HARD）**：windows-mcp 真实桌宠 App 模拟鼠标点击 + 键盘输入。弹窗答题必须**真点击选项/真输入**。
> **证据**：`plans/manual-results-2026-06-20-batch-c/`。**最后更新**：2026-06-20

---

## 0. 改动事实基线

| 事项 | 事实 | 出处 |
|---|---|---|
| 工具 handler | `build_ask_clarification_tool(ask_fn)` 阻塞 await | `clarify_tool.py:94-103` |
| 工具注册 | registry.register ask_clarification, timeout 130s | `main.py:1945-1958` |
| ask 闭包 | `_clarify_ask`: control ws send + future + wait_for 120 | `main.py:530-552` |
| pending dict | `_clarify_pending` 模块级 | `main.py:500` |
| **防竞态 H1** | `clarification_response` 独立 control 分支(3951) 在 chat(5144) 之前 | `main.py:3951` |
| 前端 hook | `useClarificationRequests` 订阅 clarification_request | `useClarificationRequests.ts` |
| 前端弹窗 | ClarificationDialog: question纯文本+options按钮+**始终有输入框** | `ClarificationDialog.tsx:89,101,137` |
| HARD GATE | `[backend_launch] Dev python=…` | backend_launch.rs |

---

## 1. 环境启动（前置）
同 Batch A §1（taskkill → 注入 worktree 环境 → npx tauri dev → 验 `[backend_launch] Dev python=…`）。

---

## TC-0 — 环境 HARD GATE
启动日志 `[backend_launch] Dev python=...backend_dir=G:\projects\deskpet\backend`。**需 windows-mcp：是（启动）**

---

## TC-1 — ★ WI-7 完整问答闭环（真点弹窗答题）
**类型**：UI 真测（windows-mcp）｜**需 windows-mcp：是**

**目的**：验证 agent 遇歧义调 ask_clarification → 弹窗 → 真点击/输入答案 → agent 据答继续。

**前置**：进 code 模式（code 模式工具集含 ask_clarification）。

| 步 | 坐标/动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | 进 code 模式，Screenshot | — | code 仪表盘 |
| 2 | Click code 输入框 → Clipboard → Ctrl+V → Enter | `帮我把那个文件里的版本号改一下`（**故意不说哪个文件/改成什么** → 触发澄清） | agent 调 `ask_clarification` 问"哪个文件/改成什么版本" |
| 3 | WaitFor `ClarificationDialog` 弹出，Screenshot | — | 弹窗显示 question（+可能 options 按钮 + 输入框） |
| 4 | **真点击输入框 → Clipboard 设答案 → Ctrl+V → 点确认/Enter** | 答案如 `改 backend/version.txt 里的版本号为 9.9.9` | 弹窗关闭，答案回灌 |
| 5 | WaitFor agent 据答继续/收尾 | — | agent 用答案继续执行（不卡死、不被 cancel） |
| 6 | grep 日志 | — | `clarification_request` 发出 + `clarification_response` 收到 + agent task 未被 cancel |

**PASS**：弹窗出现 → 真答 → agent 据答继续完成。**FAIL**：不弹窗 / 答了不继续 / agent task 被杀。

---

## TC-2 — WI-7 选项按钮路径（若 agent 给了 options）
**类型**：UI 真测｜**需 windows-mcp：是**

| 步 | 动作 | 输入 | 期望 |
|---|---|---|---|
| 1 | 发一个有有限选项的歧义请求 | `帮我新建一个项目，但我还没决定用什么语言` | agent 可能 ask_clarification 带 options(python/js/...) |
| 2 | 若弹窗有 options 按钮 → **真点击**某选项 | 点 "python" | 该选项作为答案回灌 |
| 3 | agent 据选项继续 | — | 用所选语言继续 |

**PASS**：点选项→答案回灌→继续。（若 agent 未给 options，本例退化为 TC-1 输入框路径，标 N/A）

---

## TC-3 — ★ WI-7 防竞态（答题不被新 chat cancel，§13.7 H1）
**类型**：UI 真测 + 自动化｜**需 windows-mcp：是**

**目的**：验证 clarification 走独立 control 通道，答题/期间的交互不会杀掉等待中的 agent task。

| 步 | 动作 | 期望 |
|---|---|---|
| 1 | 触发 ask_clarification 弹窗（同 TC-1 步2-3） | 弹窗出现，agent task 挂起等待 |
| 2 | **真点击弹窗答案并确认** | answer 经 control 通道发出 |
| 3 | 观察 agent | agent **据答继续**（future 被 resolve，task 未被 cancel） |

**PASS**：答题后 agent 继续（非中断）。深层"chat 消息不 cancel clarification future"由 `test_wi7_clarify.py::test_clarify_not_cancelled_by_new_chat` 覆盖。

---

## TC-4 — WI-7 超时降级
**类型**：自动化覆盖｜**需 windows-mcp：否**

**说明**：120s 不答→`{ok:false, reason:...}`，GUI 等 120s 不稳定，由 `test_wi7_clarify.py::test_clarify_timeout` 覆盖。

---

## 9. 汇总
| Case | 范围 | 类型 | 需 windows-mcp | 判定 |
|---|---|---|---|---|
| TC-0 | HARD GATE | 启动日志 | 是 | _待执行_ |
| TC-1 | ★完整问答闭环(真答) | UI 真测 | 是 | _待执行_ |
| TC-2 | options 按钮路径 | UI 真测 | 是 | _待执行_ |
| TC-3 | ★防竞态(答不被cancel) | UI 真测+自动化 | 是 | _待执行_ |
| TC-4 | 超时降级 | 自动化 | 否 | 自动化 ✅ |

## 10. 自动化基线
`test_wi7_clarify.py`(3: 阻塞等待/超时/防cancel) 全绿；tsc --noEmit exit 0；后端 BC 回归 69 passed。
