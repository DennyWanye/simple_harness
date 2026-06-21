在 DeskPet 项目实现 PPT Pro 计划的 main.py 接线（WI-3 大纲卡后端 + WI-7 注入注册 + WI-10 通道）。主要改 `G:\projects\deskpet\backend\main.py`（必要时只读其它文件确认接口）。

先读权威计划 `G:\projects\deskpet\plans\2026-06-21-ppt-deepresearch-pro\00-PLAN.md` 的 WI-3（文件 B/WS 回灌/MAJOR-1 广播）、WI-7（注入）、WI-10（artifact_pusher/receipt_reporter 实体）+ §6 R-18/R-19。

已就绪接口（只读确认实际签名）：
- `backend/deskpet/tools/ppt_outline_store.py`：`PPTOutlineWaiters()`(add/resolve/pop)、`ensure_ppt_outline_table(conn)`、`save_outline(oid,sid,topic,slides,sources_count)`、`mark_status(oid,status)`、`list_history(sid,limit=20)`、`get_outline(oid)`、`expire_dangling_proposed()`、支持 conn_factory（看它默认怎么拿 state.db；与 main 的 SessionDB 路径对齐）。
- `backend/deskpet/tools/ppt_tools.py`：`set_ppt_pro_services(*, outline_propose, notifier, run_blocking, artifact_pusher, receipt_reporter)`、`_handle_ppt_pro`、`_register_ppt_pro_tool()`（确认它是否已在 ppt_tools 模块底部自注册——若是则 main 不用再注册，只需注入 services；若需 main 触发则在 lifespan 调）。
- main.py 现有：`_control_connections`(dict sid->ws，@~3990 注册)、`_clarify_ask`(@~534,本计划不再依赖它)、lifespan、`deskpet_tool_registry_v2`、SessionDB 实例、`emit_receipt`/ReceiptStore（grep `emit_receipt`/`receipt_store`）、现有 `tool_result` WS 广播路径（grep 发 `"type":"tool_result"` + artifacts 的地方，前端 ArtifactCard 从这里渲染）。

实现：

**WI-3 大纲卡后端**
- 模块级 `_PPT_OUTLINE_WAITERS = PPTOutlineWaiters()`（从 ppt_outline_store import）。
- `async def _broadcast_control(msg: dict)`：遍历 `_control_connections.values()` 逐个 `await ws.send_json(msg)`（best-effort，单个失败不影响其它）。参考现有「广播到所有 control 连接」的写法（grep 子代理面板广播 / `for ... in _control_connections`）。
- `async def _ppt_outline_propose(sid, *, topic, slides, sources_count, outline_md, no_research) -> dict`（按 WI-3 文件B伪码）：
  - `oid=uuid4().hex`；`cfg=_ppt_pro_cfg()`（从 ppt_tools import，或直接 standalone_config_section("ppt")）；若 `cfg.outline_history`：`save_outline(oid,sid,topic,slides,sources_count)`（注意 ensure 表）。
  - payload={outline_id,topic,outline_md,session_id:sid,sources_count,no_research,history:(list_history(sid,20) if cfg.outline_history else [])}。
  - `await _broadcast_control({"type":"ppt_outline_proposed","payload":payload})`。
  - `fut=loop.create_future(); _PPT_OUTLINE_WAITERS.add(oid,fut)`；`try: decision=await asyncio.wait_for(fut,cfg.confirm_timeout_s) except TimeoutError: decision={"action":"cancel"} except CancelledError: mark_status(oid,"cancelled"); raise finally: _PPT_OUTLINE_WAITERS.pop(oid)`。
  - `mark_status(oid, {accept:"accepted",modify:"proposed",cancel:"cancelled",reuse:"accepted"}.get(decision.get("action"),"rejected"))`；return decision。
- WS 回灌：在 control WS 消息循环（grep 处理 `skill_candidate_confirm`/`clarification_response` 的 elif 链）加 `elif msg_type=="ppt_outline_decision"`：取 payload{outline_id,action,feedback,reuse_id}；`if _PPT_OUTLINE_WAITERS.resolve(oid,{...})`(首次生效) → `await _broadcast_control({"type":"ppt_outline_resolved","payload":{"outline_id":oid}})`（清各面板 stale 卡）；重复 resolve 已 no-op。
- 启动清死卡（R-19）：lifespan 启动时调 `expire_dangling_proposed()`（把残留 proposed→expired，防跨重启死卡）。

**WI-7 注入 + 注册**
- 在 lifespan 里（`_ppt_outline_propose` 定义之后）调 `ppt_tools.set_ppt_pro_services(outline_propose=_ppt_outline_propose, notifier=<main-loop notifier>, run_blocking=lambda fn: asyncio.get_running_loop().run_in_executor(None,fn), artifact_pusher=_ppt_artifact_push, receipt_reporter=_ppt_receipt_report)`。
  - notifier：一个 `async def (sid, text)`，走 control WS 给该 sid 发一条聊天气泡消息（参考现有 notifier / worker.notifier 怎么把文本推到桌宠聊天；要在 main loop）。
  - **ppt_pro 工具已在 ppt_tools 模块底部自注册（line 4662 `_register_ppt_pro_tool()`，内部按 enabled 判定）——main 不要再注册，只需 `set_ppt_pro_services(...)` 注入。**（registry 对 handler 返回 dict 会 json.dumps，无需特殊处理。）
- 确认 `_session_id` 注入：ppt 工具 dispatch 时 registry 会把 session context 合并进 args（与现有 ppt_create 一致），无需额外改；若 ppt_create 现在就能拿到 `_session_id`，ppt_pro 同理。

**WI-10 通道实体**
- `async def _ppt_artifact_push(sid, artifacts: list, text: str="")`：构造合成 `{"type":"tool_result","payload":{...,"artifacts":artifacts,"session_id":sid,"text":text}}` 事件，走现有 tool_result WS 广播路径发给该 sid 的前端（前端 ArtifactCard 从 tool_result.artifacts 渲染「打开/在文件夹」卡），并像现有路径一样落 SessionDB（参考现有 tool_result 落库写法）。
- `def _ppt_receipt_report(sid, *, tool="ppt_pro", outcome, path=None, shas=None)`：调现有 `emit_receipt`/ReceiptStore 记一条 receipt（best-effort，失败 log）。

约束/验收：
- 不破坏现有 control WS 其它消息处理、不破坏现有 receipt/tool_result 路径。
- 启动无 Traceback。`cd /g/projects/deskpet/backend && .venv/Scripts/python.exe -c "import main"` 能 import（或现有 boot smoke）。
- 跑 `.venv/Scripts/python.exe -m pytest tests/ -k "ppt or clarify or receipt" -q` 不破现有。
- 若有现成 boot smoke 脚本就跑一下确认 ready。
- 自己补/扩单测覆盖 `_ppt_outline_propose`(mock ws/fut: accept/timeout/cancel/resolve首次生效广播resolved)、`_broadcast_control`、WS 回灌 ppt_outline_decision、启动 expire。放 `tests/test_ppt_outline_wiring.py`（若需 main 级则用现有 main 测试夹具风格）。

完成后简述：改了 main.py 哪些位置（行号）、notifier/artifact_push/receipt 复用了哪条现有路径、单测覆盖、boot/import 验证结果。