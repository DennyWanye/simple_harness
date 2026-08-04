# CODEX 实现作业 — 阶段 B 前端：响应会话切换 + 新话题按钮

你是 DeskPet（G:/projects/deskpet，master @ 0e6211e8）前端 Expert（React + TypeScript + Vite，`tauri-app/src`）。实现 v2 plan 阶段 B 的**前端配合部分**。Lead 审查+真机验收。

## 背景与契约（后端已完成）
后端 T1-1 会话切分已上线：用户发 `/new ...` 或 control payload `{new_session:true}` 时，后端起新 `effective_sid`（形如 `task-default-<seq>`），并向**该窗口的 control WS** 发两个事件（也按 group 广播给同组窗口）：
```json
{ "type": "session_switched",      "payload": { "old_sid": "default", "new_sid": "task-default-1", "reason": "explicit_new" } }
{ "type": "task_session_started",  "payload": { "old_sid": "default", "new_sid": "task-default-1", "reason": "explicit_new" } }
```
此后该窗口的对话（user echo / assistant final / 工具事件）的 `payload.session_id` 都是新 `effective_sid`。

## 问题（必须修，否则可见性回归）
- `tauri-app/src/App.tsx:552` 宠物窗**硬编码** `sessionId="default"`；`tauri-app/src/message-panel/MessagePanelRoot.tsx:50` `const SID = "default"`、`:436` `session_id={SID}`。
- 后端切到 `task-*` 后，新 scope 的消息 `payload.session_id=task-*` 进了 `sessions store[task-*]`，但前端仍订阅/渲染 `store["default"]` → **用户看不到新对话**（codex R2 点名的可见性回归）。

## 你的实现范围（只改前端 `tauri-app/src`）
1. **维护"当前活跃 sid"状态**（替代硬编码 `default`）：
   - 宠物窗（App.tsx）与消息面板（MessagePanelRoot.tsx）各维护一个 `activeSid`（初值 `"default"`）。
   - 渲染消息流 / Context usage / send / echo / stop 全部用 `activeSid`（取代写死的 `"default"`/`SID`）。
2. **响应后端事件**：在已有的 control WS 消息处理处（grep `session_switched` 当前应无、grep 现有事件 dispatch 如 `chat_v2_*`/`control` onmessage），新增对 `session_switched` / `task_session_started` 的处理 → `setActiveSid(payload.new_sid)`，并确保 `sessions store` 有该 sid 的条目（空消息列表即可，后续 echo/final 填充）。
3. **"新话题"按钮**（UI）：在输入栏（InputBar 或宠物窗工具条）加一个"➕ 新话题"按钮 → 发送 control payload `{ new_session: true, text: "<当前输入框文本或空>" }`（与现有发送消息走同一 control 通道；后端据 `new_session` 切 scope）。点击后输入框可保留文本。
4. **"回到上个话题"退路**（轻量）：可加一个小入口（如点当前 scope 标签）发 `{ session_id: "default" }` 或显示历史 scope 列表切回；**最小实现**：保留切回 `default` 的能力即可。
5. **BC**：无切换事件时 `activeSid` 恒 `"default"`，行为/渲染与现状完全一致（多窗口仍共享 default）。

## 约束
- 只改 `tauri-app/src/**`（App.tsx / MessagePanelRoot.tsx / InputBar 等 + 相关 store/类型）。**不碰后端**。
- **类型检查必须过**：`cd tauri-app && npx tsc --noEmit`（或项目既有 `npm run build` 的 tsc 步骤）零错误。
- 不引入新依赖。沿用现有 sessions store / WS 封装的写法（grep 现有 `useSessionsStore`/control onmessage 模式照抄）。
- **不 commit**（Lead 审查集成）。**不起 dev server**（只 tsc 验证）。

## 交付
输出：①改了哪些文件 + 每个改了什么（activeSid 状态 / 事件响应 / 新话题按钮 / 退路）②`tsc --noEmit` 结果（贴零错误证据）③BC 说明（无切换=default 不变）④任何偏差/阻碍（如某处 sid 难解耦）。
