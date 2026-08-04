# codex 作业 C：实现 WI-7（ask_clarification 工具 — 后端 + 前端）

DeskPet 项目（仓库根 `G:\projects\deskpet`，已在此 cwd）。**严格按权威 plan 实现。**

## 第 0 步：读 plan + 摸现状
1. 读 `plans/2026-06-20-agent-loop-optimization/00-PLAN.md` 的 **§8（WI-7）、§13.7（H1-H4）、§15.2 A-4/A-5、§16.3**。以 §16.3 为准（定死了 responder 契约：仿 `_permission_responder`，独立 control 通道）。
2. **关键约束（§13.7 H1，致命竞态）**：`clarification_request`/`clarification_response` 必须走**独立 control WS 消息类型**（不混 chat 消息）。否则用户回答会被"同 sid 新 chat 消息 cancel 旧 task"逻辑（`main.py:6419` 附近）杀掉 → future 永挂。permission 没此问题正因走 control 通道——必须照抄。
3. 摸现状（读这些理解模式）：
   - 后端：`backend/main.py` 的 `_permission_responder`（约 499）、`_permission_pending`（约 497 模块级 dict）、recv 主循环里 `elif msg_type == "permission_response":`（约 3860）、`_control_connections`（约 2797）。`import uuid` 已存在。
   - 前端：`tauri-app/src/hooks/usePermissionRequests.ts`（hook 模式）、`tauri-app/src/components/PermissionPopup.tsx`（弹窗）、`tauri-app/src/App.tsx`（约 715 接线 + 消息 switch）、`tauri-app/src/types/skillPlatform.ts`（PermissionRequest/Response 类型）、`tauri-app/src/ws/ControlChannel`。

## 第 1 步：后端实现
1. **新建 `backend/deskpet/tools/code_tools/clarify_tool.py`**（§16.3 A-5）：
   ```python
   import json
   def build_ask_clarification_tool(ask_fn):  # ask_fn: async (question, options, session_id) -> str
       _SCHEMA = {"name": "ask_clarification",
                  "description": "当用户意图不清时，向用户提问澄清并等待其回答后再继续。阻塞式：返回用户的回答。",
                  "parameters": {"type": "object",
                                 "properties": {"question": {"type": "string", "description": "向用户提的澄清问题(简短1-2句)"},
                                                "options": {"type": "array", "items": {"type": "string"}, "description": "可选项(可空)"}},
                                 "required": ["question"]}}
       async def _handler(args, task_id="", session_id="default"):
           answer = await ask_fn(args.get("question", ""), args.get("options", []), session_id)
           if answer:
               return json.dumps({"ok": True, "answer": answer}, ensure_ascii=False)
           return json.dumps({"ok": False, "reason": "user_did_not_respond_or_no_channel"}, ensure_ascii=False)
       return _handler, _SCHEMA
   ```
2. **`backend/main.py`**：
   - 模块级（与 `_permission_pending` 同处）加 `_clarify_pending: dict[str, asyncio.Future] = {}`。
   - 加 `_clarify_ask` 闭包（仿 `_permission_responder`，§16.3 A-? 的 `_clarify_ask`）：取 `_control_connections.get(session_id) or .get("default")`；无连接返回 ""；建 `rid = str(uuid.uuid4())` + future 注册 `_clarify_pending[rid]`；`await ws.send_json({"type":"clarification_request","payload":{"request_id":rid,"question":question,"options":options}})`；`try: return await asyncio.wait_for(fut, 120) except asyncio.TimeoutError: return "" finally: _clarify_pending.pop(rid, None)`。
   - recv 主循环加 `elif msg_type == "clarification_response":`（**在 chat 消息分支之前**，与 permission_response 同位置）：取 rid/answer，`fut=_clarify_pending.get(rid)`，`if fut and not fut.done(): fut.set_result(payload.get("answer",""))`。
   - **注册工具**：用 `build_ask_clarification_tool(_clarify_ask)` 拿到 (handler, schema)，注册进 tool registry（grep 现有工具注册方式，如 `registry.register(...)` 或 build_agent 注入；permission_category 走轻量无副作用，如 default-allow 类别）。确保 agent 能看到并调用 ask_clarification。
3. **后端测试** `backend/tests/test_wi7_clarify.py`：
   - `test_clarify_blocks_until_response`：mock ask_fn / 直接测 _clarify_ask 风格——建一个挂起 future，set_result 后 handler 返回 answer。
   - `test_clarify_timeout`：无响应→超时返回 `{ok:false, reason:...}`。
   - `test_clarify_not_cancelled_by_new_chat`（§16.6）：模拟 clarification 挂起期间收到一条 chat 消息，断言该 future **未被 cancel**（因走独立分支、不进 chat cancel 路径）。
   跑 `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_wi7_clarify.py -v` 到全绿；再跑 `backend/tests/test_websocket.py`（若有）确保 recv 分支不破。

## 第 2 步：前端实现（§16.5 默认交互）
1. **类型**：在 `tauri-app/src/types/skillPlatform.ts`（或 messages.ts，仿 PermissionRequest/Response）加 `ClarificationRequest`（type:"clarification_request", payload:{request_id, question, options?}）+ `ClarificationResponse`（type:"clarification_response", payload:{request_id, answer}）。
2. **hook** `tauri-app/src/hooks/useClarificationRequests.ts`：仿 `usePermissionRequests.ts`，订阅 `clarification_request`，queue FIFO，暴露 `current` + `resolve(answer: string)`（`channel.send({type:"clarification_response", payload:{request_id, answer}})`）。
3. **组件** `tauri-app/src/components/ClarificationDialog.tsx`：仿 `PermissionPopup.tsx`。渲染：question 纯文本（**不**支持 markdown）；options 非空→渲染为按钮列表，点击即 `resolve(option)`；**始终**同时显示一个自由输入框 + 确认按钮（回车/点确认→`resolve(输入值)`）。
4. **接线** `tauri-app/src/App.tsx`：在 permission 接线附近 mount `useClarificationRequests(channel)` + 渲染 `<ClarificationDialog current={...} onResolve={...}/>`。
5. 跑前端类型检查：`cd tauri-app && npx tsc --noEmit`（或项目 lint 命令）确保无类型错误。

## 约束
- **不要 `git commit` / `git add`**。
- 不改 `backend/agent/agent_loop.py`（别的批改过）。后端主要动 main.py + 新 clarify_tool.py；前端动 App.tsx + 新 hook/组件 + types。
- 前端无法单测，但务必 `npx tsc --noEmit` 通过。
- 完成输出：后端/前端改动清单 + 后端测试结果 + tsc 结果。
