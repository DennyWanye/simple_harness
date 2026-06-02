# 手工测试用例 — superpowers code 工作流全套（纯人工操作）

> **状态**: **v2**（opus-4.8 评估迭代 ×1 完成：修 TC-M8 测错组件、TC-M11 mode=strict、
>   TC-M1 p5s2_ 前缀、/prefs 渲染文案、加坐标/DPI/中文输入铁律；并据评估补了 prefs 进
>   `/api/commands/help` 下拉候选——真实修复一个可发现性 gap）。可供 windows-mcp 真机照测。
> **测试方式铁律**：**只允许人工的鼠标点击 + 键盘输入 + 肉眼/日志判定**。
>   ❌ 不允许脚本回放、WebSocket/CDP 注入、pytest、import 查内部状态当证据
>   （遵守 CLAUDE.md HARD CONSTRAINT + `feedback_real_e2e_not_script_replay`）。
>   windows-mcp 真机阶段：每动作 declare `坐标=(x,y) | 动作 | 期望`，截图存盘，肉眼/后端日志判 PASS/FAIL。
> **被测**: 11 FEAT 的用户可见行为。dev 已开全部 flag（plan_confirm_gate / preference_memory /
>   verify strict / slash_commands / goal_mode / agent_parallel）。
> **前置环境**：
>   1. dev app 已启动（`scripts/dev-worktree.ps1` 或既定 env：DESKPET_BACKEND_DIR=backend +
>      CDP 9222 仅用于"截图取证"，**不用于注入操作**）。
>   2. 已登录（chinzy relay，keychain 有 key）→ 真 LLM 可用。
>   3. 进入 Code 模式多项目仪表盘，至少一个项目 tile（如 test-research-helper）可见、idle。
>   4. backend 日志可 tail（抓 verify_gate / plan_confirm / preference_memory / intent_memory 事件）。

---

## 判定通用格式（每个 case 报告须含）
```
case:   TC-M-XX
坐标:   (x, y) [物理像素]
动作:   click / type "..." / 按键
截图:   screenshots/TC-M-XX-*.png（before/after）
日志:   <grep 后端关键事件 + timestamp>
判定:   PASS / FAIL / RETRY-N / SKIP(理由)
```

## 真机操作铁律（windows-mcp 阶段，evaluator R1 补）
1. **每个 click 前先 Screenshot/Snapshot 抓画面 → 肉眼/工具定位目标元素物理坐标 → 再
   SetCursorPos+SendInput 点击**。坐标随窗口位置变，**不可硬编码跨 case 复用**；可用 data-testid
   交叉确认元素（截图后比对）。
2. **DPI 150%（本机）**：windows-mcp 截图若给逻辑像素需 ×1.5 换算成物理像素；用
   `SetProcessDpiAwareness(2)` + **SendInput**（WebView2 不响应老式 `mouse_event`，见 CLAUDE.md 圣杯片段）。
3. **中文输入走剪贴板**：所有中文（如"你用的是什么模型？"）用 STA Runspace +
   `[System.Windows.Forms.Clipboard]::SetText("中文")` + Ctrl+V，**不要**用 SendKeys 直接打中文（IME 不支持）。
4. **CDP 9222 仅作可选 fallback 截图**；**操作一律 SendInput 真点击/真键入**，绝不用 CDP/WS 注入
   （否则违反 HARD CONSTRAINT，证据无效）。优先用 windows-mcp 原生 Screenshot 取证。
5. data-testid 速查：plan 确认栏 `plan-confirm-bar`、执行 `plan-confirm-go`、取消 `plan-confirm-cancel`；
   slash 下拉 `slash-dropdown`、候选项 `slash-item-{name}`。

---

## TC-M1 — 意图门：问元信息不该乱动手（FEAT Layer 1A）
**前置**：进入 test-research-helper tile，输入框可见。
**步骤（人工）**：
1. 鼠标点击该 tile 的输入框（聚焦）。
2. 键盘输入：`你用的是什么模型？`
3. 点击「发送」按钮（或 Enter）。
4. 肉眼观察 AI 回复 + tail 后端日志。
**期望**：
- AI **一句话直接回答模型名**（如"我用的是 deepseek-v4-pro"），**不**出现 todo/工具卡片/文件改动。
- 后端日志该轮 `tool_calls=0`、无 `p5s2_tool_call_args_dump`（真实事件名带 `p5s2_` 前缀）。
**判定依据**：回复是纯文字答案 + 日志零工具调用 = PASS。

## TC-M2 — 模糊派活先澄清（FEAT Layer 1A）
**步骤**：
1. 点击输入框，输入：`帮我优化一下代码`
2. 点「发送」。
**期望**：
- AI **反问澄清**（目标/范围/成功标准/哪个文件），**不**直接改任何文件。
- 后端日志 `tool_calls=0`；项目目录无新增/改动文件。
**判定**：出现澄清问题 + 零文件改动 = PASS。

## TC-M3 — plan-confirm 硬门：GO 路径（FEAT plan 硬门）
**步骤**：
1. 输入框输入明确任务：`请在项目根目录创建 MANUAL_T3.md 文件，写入一句项目简介，创建后读回确认`
2. 点「发送」。
3. 等待 → 肉眼确认 tile 内出现 **"📋 计划 (N 步) — 确认后执行"卡片**（testid `plan-confirm-bar`）
   **+ [▶ 执行]（testid `plan-confirm-go`）和 [取消]（testid `plan-confirm-cancel`）两个按钮**，
   且此时**任务尚未执行**（无 write_file 日志）。
4. 鼠标点击 **[▶ 执行]** 按钮。
**期望**：
- 点击前：计划卡 + 两按钮可见；后端日志有 `plan_confirm_gate_awaiting`、**无** tool dispatch。
- 点击后：按钮消失；后端 `plan_confirm_received decision=go` → `plan_confirm_gate_go` → 开始
  todo/list/write_file；`MANUAL_T3.md` 文件被创建。
**判定**：暂停→点执行→才执行→文件创建 = PASS。**测后删 MANUAL_T3.md**。

## TC-M4 — plan-confirm 硬门：CANCEL 路径（FEAT plan 硬门）
**步骤**：
1. 输入：`请在项目根目录创建 MANUAL_T4.md 文件并写入大段说明，然后读回验证`
2. 点「发送」→ 等计划卡 + 按钮出现。
3. 鼠标点击 **[取消]** 按钮。
**期望**：
- 前端：点击后 [执行]/[取消] 按钮**消失**（`chat_v2_plan_cancelled` → 清按钮），tile 状态徽章回 **idle**。
- 后端 `plan_confirm_received decision=cancel` → `plan_confirm_gate_cancelled`；**无任何 tool dispatch**；
  `MANUAL_T4.md` **未被创建**。
**判定**：点取消→按钮消失+回 idle→零执行→文件不存在 = PASS。

## TC-M5 — Layer 1B 偏好记忆：相似任务自动确认（FEAT Layer 1B）
**前置**：先确保偏好记忆里有一条"已批准"的计划（可先跑一遍 TC-M3 点[执行]，即记下一条）。
**步骤**：
1. 输入一个与 TC-M3 **逐字相同、仅文件名 T3→T5** 的任务（确保 cosine ≥0.86 稳过阈值）：
   `请在项目根目录创建 MANUAL_T5.md 文件，写入一句项目简介，创建后读回确认`
2. 点「发送」，肉眼观察。
**期望**：
- **完全不弹** [执行]/[取消] 按钮（auto 时 `awaiting_confirm=false` → 前端根本不渲染 `plan-confirm-bar`，
  不是"一闪而过"）；tile 直接进入执行（thinking/工具卡）。**硬判据：截图中无 `plan-confirm-bar` 元素**。
- 后端日志 `plan_confirm_auto_approved sid=... score=0.xxx`（≥0.86）、**无** `plan_confirm_gate_awaiting`。
- `MANUAL_T5.md` 被创建。
**判定**：相似任务无按钮直接跑 + 截图无 plan-confirm-bar + 日志 auto_approved = PASS。**测后删 MANUAL_T5.md**。

## TC-M6 — `/prefs` 查看偏好记忆（FEAT A2）
**步骤**：
1. 输入框输入：`/prefs`
2. 点「发送」。
**期望**（真实渲染文案，ws.ts format_slash_result prefs_list 分支）：
- tile 内出现一条结果消息，标题 **`偏好记忆（共 N 条）:`**，每条形如 **`  plan/approved: 请在项目根目录创建...`**
  / **`  intent/ask: 你用的是什么模型？`**（`{kind}/{label}: {text}`，缩进两空格）。
**判定**：看到 `偏好记忆（共 N 条）:` 列表（含 TC-M3/M5 记下的 plan 条目 + TC-M1 的 intent 条目）= PASS。

## TC-M7 — `/prefs clear` 清除（FEAT A2）
**步骤**：
1. 输入：`/prefs clear`，点「发送」。
2. 再输入：`/prefs`，点「发送」。
**期望**（真实渲染文案）：
- 第一次：出现 **`已清除 N 条偏好记忆`** 提示。
- 第二次：**`偏好记忆为空（暂无意图/计划记录）`**（不会出现"共 0 条"）。
**判定**：`已清除 N 条偏好记忆` + 再查显示 `偏好记忆为空（暂无意图/计划记录）` = PASS。

## TC-M8 — slash 命令自动补全下拉（FEAT B3 SlashDropdown）
**⚠️ 关键**：slash 下拉只在 **InputBar**（完整 chat 视图）里，**不在** grid tile 的裸 textarea。
**步骤**：
1. 点击 tile 的 **⤢ 展开按钮**进入完整 chat 视图（CodePanelRoot），或用消息面板（MessagePanelRoot）。
2. 鼠标点击**底部 InputBar 输入框**。
3. 键盘只输入一个字符：`/`
**期望**：
- 输入框**上方**弹出命令候选下拉（testid `slash-dropdown`，`position:absolute; bottom:100%`），
  每项 testid `slash-item-{name}`，候选含 **help / goal / prefs**（prefs 已补进 `/api/commands/help`）
  + 已装 skills；底部固定提示行 **`↑↓ 选择 · Tab/Enter 接受 · ESC 关闭`**。
**判定**：完整视图 InputBar 输入 `/` 弹出 `slash-dropdown` 且候选含 prefs = PASS。
（这是 B3 引入、你之前问的"像 Claude Code 那种"补全下拉。）

## TC-M9 — A4 plan 消息持久化（刷新不丢按钮）（FEAT A4）
**步骤**：
1. 输入明确任务（如 TC-M3 的文本，但**不要点执行/取消**），等计划卡 + [执行]/[取消] 出现。
2. **刷新面板**（Tauri 桌面 app 不响应浏览器 F5，按优先级）：
   (a) **关闭 Code 面板窗口再重新打开**（最稳，触发组件重挂 → `session_messages_load` 重发）；
   (b) 若 dev 开了 webview devtools：在 webview 内 `Ctrl+R`。
3. 刷新后肉眼观察该 tile。
**期望**：
- 刷新后 tile 内 **计划卡 + [执行]/[取消] 按钮仍在**（不因刷新消失）。
- 点击 [执行] 仍能继续执行该任务。
**判定**：刷新后按钮仍在且可点继续 = PASS。

## TC-M10 — 意图记忆学习（同类提问免澄清）（FEAT A1）
**步骤**（观察"学习"效果，可能 subtle）：
1. 第一次问一个会被当"提问"的元信息问题（如 TC-M1），观察直接回答。
2. **等 AI 回复结束后**（record 是 chat_v2_final 后 fire-and-forget），tail 后端日志确认出现
   `preference_memory recorded kind=intent label=ask text='...'`。
3. 再问一个语义相似的元信息问题（如 `你现在跑在什么模型上`）。
**期望**：
- 第二次同样**直接回答、不动手**；日志可能出现 `intent_memory_hint label=ask`（命中意图记忆注入 hint）。
**判定**：同类提问稳定直接回答 + 日志 intent record/hint = PASS。（注：persona 本身已能直答，
  intent 记忆是强化层，hint 命中是加分项；主判据仍是"不乱动手"。）

## TC-M11 — verify strict 不误杀真任务（FEAT verify strict，dev）
**步骤**：
1. 输入明确任务并走完（TC-M3 GO 路径，点[执行]让它真创建文件）。
2. 观察任务**正常完成**（AI 给出完成总结，文件真创建）。
3. 在 **backend 启动日志**里 grep `verify_gate_init mode=strict patterns=N`（**无引号**、structlog 风格，
   boot 时打一次，**不是每轮**），且任务轮**无 `verify_gate_nudge_injected` 阻断**。
**期望**：strict 模式下真任务（真调 write_file）不被误拦，正常完成。
**判定**：strict 激活 + 真任务完成 + 无误杀 nudge = PASS。

---

## windows-mcp 真机执行状态（过夜）
- **环境已备好**：dev app 已重启（跑 dev 源码 + CDP 9222 + 全 flag ON）、windows-mcp 工具已加载、
  Desktop Pet 窗口已定位（585×930，companion 聊天界面）。
- **未实跑 11 case**：Code 模式面板需从 pet 工具栏打开 → WebView2 + DPI 150% + 双屏 + 截图降采样，
  盲坐标点击 11 个 case 极易产出**错误/虚假证据**。按 CLAUDE.md HARD CONSTRAINT「绕过/糊弄得来的
  PASS 是负价值」+ 真机需有人值守，**故意不在无人值守 + 巨大 context 下硬点造假**。
- **建议**：用户在场时按本文档逐 case 真机跑（关键 case：TC-M3/M4 plan 门、TC-M6/M7 /prefs、
  TC-M8 slash 下拉、TC-M5 自动确认、TC-M9 刷新持久化）。所有功能已有单测 + 完成度审计 11/11 背书，
  真机是「用户视角最终确认」。

## 测试副产物清理
测试中创建的 `MANUAL_T*.md` 在对应 case 测完后删除；`/prefs clear` 已清偏好；不留垃圾。

## 不在本手测范围（说明）
- verify strict **拦截 fake**（需诱导 LLM 撒谎，人工难稳定复现）→ 由单测 9/9 覆盖，手测只验"不误杀真"。
- agent_parallel/team 多子代理（B3）→ 需特定多任务场景，本轮聚焦 superpowers 主链路，可后续补。
