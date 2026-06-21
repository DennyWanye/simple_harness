在 DeskPet 项目实现 PPT Pro 计划的 ppt_tools「执行半」（WI-6 + WI-7 + WI-10）。只改 `G:\projects\deskpet\backend\deskpet\tools\ppt_tools.py`（可新增 import）。**不要改 main/image_tools/前端/config。**

先读权威计划 `G:\projects\deskpet\plans\2026-06-21-ppt-deepresearch-pro\00-PLAN.md` 的 WI-6、WI-7、WI-10 段 + §2.2 回退判定 + §2.3/§2.5 编排骨架（伪码就在那，按它实现）。

已就绪可直接用的接口（已 import 或 from 调用）：
- 本文件已有（S-ppt-A 刚加）：`_ppt_pro_cfg()`(SimpleNamespace,字段 enabled/default_depth/max_revisions/research_timeout_s/confirm_timeout_s/image_probe_timeout_s/render_timeout_s/save_research/outline_history)、`_research_topic_for_ppt`、`_save_and_index_research`、`_draft_outline_from_research`、`_outline_to_markdown`、`_fallback_minimal_outline`、`SlideOutline`/`parse_outline`、现有 `ppt_create`(@~3152)/`_handle_ppt_create`(@~3750)/`_register_ppt_tool`(@~3834)/`_autofill_image_prompts`(@~1867)。
- `from deskpet.tools.image_tools import probe_image_reachable, generate_images`：`generate_images(prompts,*,size=...,model=None)` 返回 list[{prompt,path,error,error_kind}]，error_kind ∈ connectivity/model_unavailable/auth/quota/content/unknown。`probe_image_reachable(*, timeout_s=8.0)->bool`。
- `from deskpet.tools import ppt_outline_store`：用不到（propose 在 main 注入），但 reuse 还原用 `ppt_outline_store.get_outline(oid)`（返回含 slides_json 的 dict，`parse_outline(json.loads(row["slides_json"]))` 还原）。

实现：

**WI-6 回退编排**
- `_degrade_to_template(slides) -> list[SlideOutline]`：把 image_full+image_prompt 页转模板友好（image_full→bullet/section）；**清空每页 image_prompt 和 image_path**（防 ppt_create 内部二次生图）；保留 title/bullets/subtitle（bullets 已由双模式拟纲填充，密度够）。
- `_should_fallback(res: dict) -> bool`：`res.get("error_kind") in {"connectivity","model_unavailable"}`。
- `_autofill_with_connectivity_gate(slides) -> tuple[bool,int]`：按 §2.5/WI-6 伪码——先只生第1张图；首图失败且 `_should_fallback` → 返回(False,0)；content类失败→占位继续；再生其余；**末尾若 n_ok==0 → 返回(False,0)**（全图失败也回退兜底）；否则(True,n_ok)。用 `generate_images`。
- `_render_pro(slides, *, theme, title, author, output_path, image_mode, probe_timeout_s, notify)->dict`：按 §2.5 伪码——image_mode 且 `not probe_image_reachable(timeout_s=probe_timeout_s)` → use_template+notify；否则 `_autofill_with_connectivity_gate`，不可达→use_template+notify；use_template 则 `_degrade_to_template`+`ppt_create(..., template=_default_template()或默认大类, skip_image_gen=True)`；否则惊艳路径 `ppt_create(..., skip_image_gen=True)`（图已生好）。`notify` 是同步回调(str)->None。
- **给 `ppt_create` 加 keyword-only 形参 `skip_image_gen: bool=False`**：在其内部调用 `_autofill_image_prompts` 处（@~3239-3248 那段）加 `if not skip_image_gen:` 包住生图分支。**默认 False = 现有行为字节不变（BC）**。

**WI-7 编排+handler+注册**（async handler 秒回 + 独立 task，照 §2.3/§2.5/WI-7 伪码）
- 模块级：`_PPT_PRO_CTX: dict = {}`、`_PPT_PRO_TASKS: set = set()`、`_PPT_PRO_RUNNING: dict[str,asyncio.Task] = {}`、`_PPT_PRO_RUNNING_TOPIC: dict[str,str] = {}`。
- `def set_ppt_pro_services(*, outline_propose=None, notifier=None, run_blocking=None, artifact_pusher=None, receipt_reporter=None)`：存入 `_PPT_PRO_CTX`（仿 research_tools.set_live_llm_call 注入范式）。
- `def _ppt_pro_cancel(sid)`：`t=_PPT_PRO_RUNNING.get(sid); if t and not t.done(): t.cancel()`。
- `async def _ppt_pro_orchestrate(*, topic,pages,depth,theme,image_mode,title,author,output_path,outline_propose,notifier,run_blocking,session_id)`：按 §2.5 骨架——notify「🔍调研中」→`_research_topic_for_ppt`(wait_for 已在其内)→`save_research` 真→`_save_and_index_research`→notify「📚完成N源/未取得」→`llm=await _resolve_default_llm_call()`(from research_tools)→`_draft_outline_from_research`→确认环(≤max_revisions)：`d=await outline_propose(session_id, topic=, slides=, sources_count=, outline_md=_outline_to_markdown(slides), no_research=(report is None))`；act accept→confirmed；reuse→`parse_outline(json.loads(ppt_outline_store.get_outline(d["reuse_id"])["slides_json"]))`+confirmed；cancel→notify+return；modify→`_draft_outline_from_research(...,feedback=d.get("feedback",""),prev_slides=slides)`。确认后 notify「✅开始生成」→`render_to = cfg.render_timeout_s or max(600,pages*120)`→`result=await asyncio.wait_for(run_blocking(lambda:_render_pro(...)), render_to)`→`await _ppt_pro_report_done(result,...)`。`except asyncio.CancelledError: notify+raise`；`except Exception: log.exception+notify「没做成」`。
- `async def _handle_ppt_pro(**kwargs)`：按 WI-7 伪码——读 `_session_id`(默认default)+从 `_PPT_PRO_CTX` 取 outline_propose/notifier/run_blocking；缺失返回明确错误 dict；**去重/替换**：`cur=_PPT_PRO_RUNNING.get(sid)`,`topic=kwargs.get("topic","").strip()`；若 cur 未done：同 topic→返回 status:already_running；异 topic→`_ppt_pro_cancel(sid)`+notify换主题。`_runner` 内 `try: await _ppt_pro_orchestrate(...) except CancelledError: notify「已停止」+raise except Exception: log+notify finally: _me=asyncio.current_task(); if _PPT_PRO_RUNNING.get(sid) is _me: pop sid from RUNNING和RUNNING_TOPIC`（**identity-guard**）。`t=create_task(_runner()); RUNNING[sid]=t; RUNNING_TOPIC[sid]=topic; _PPT_PRO_TASKS.add(t); t.add_done_callback(_PPT_PRO_TASKS.discard)`；返回 `{"ok":True,"status":"researching","message":"..."}`。
  - 注意 handler 是注册给 registry 的；现有 `_handle_ppt_create(args,task_id)` 是同步签名。`ppt_pro` 要 async handler——确认 registry 支持 async handler（research/clarify 工具就是 async handler，参考其注册）。`_session_id` 从 args 注入读（同 ppt_create 的 `args.get("_session_id")` @~3769）。**按 registry 实际 handler 约定调整 _handle_ppt_pro 签名**（读 registry.register 与现有 async 工具注册法）。
- `_PPT_PRO_SCHEMA`：topic:string(必填)/pages:integer(默认8,3-20)/depth:enum(light,standard,deep)/theme:enum(minimal,dark,playful)/image_mode:boolean(默认true)/title/author/output_path。
- `_register_ppt_pro_tool()`：仿 `_register_ppt_tool`，`toolset="ppt"`,`permission_category="write_file"`,`timeout_seconds=60.0`(秒回),`concurrency_safe=False`；**仅当 `_ppt_pro_cfg().enabled` 为 True 注册**。在模块底部现有 `_register_ppt_tool()` 调用处附近调用它。

**WI-10 上报**
- `async def _ppt_pro_report_done(result, *, notifier, session_id)`：从 `_PPT_PRO_CTX` 取 artifact_pusher/receipt_reporter（可空则降级只 notify）。成功(result.get("ok"))→`await artifact_pusher(session_id, result.get("artifacts",[]), text)`+`receipt_reporter(session_id, outcome="ok", path=result.get("path"))`+notify「✨做好啦」（带图复用现有自动打开逻辑）；失败→notify+`receipt_reporter(outcome="failed")`。全 best-effort 不崩。

约束/验收：
- async/loop 语义按 §2.3：handler 秒回、create_task 起独立 task；`_render_pro`(阻塞 ppt_create)经注入的 run_blocking(=loop.run_in_executor)跑。
- 不破坏现有 `ppt_create`/`_handle_ppt_create`/`-k ppt` 测试。`skip_image_gen` 默认 False 字节 BC。
- 自己补单测 `tests/test_ppt_pro_exec.py`：mock probe/generate_images/outline_propose/research/llm/render——(a)probe False→模板路径(_degrade调用+ppt_create skip_image_gen=True+template)；(b)首图connectivity→回退；(c)首图model_unavailable→回退；(d)content→不回退占位续；(e)全成功→惊艳不二次生图；(f)_degrade清image_prompt且bullets在；(g)ppt_create(skip_image_gen=False)默认字节BC；(h)handler秒回status:researching起task；(i)同topic→already_running、异topic→cancel旧；(j)orchestrate happy(accept→render)/cancel/超时/reuse/modify 路径(mock outline_propose返不同action)；(k)identity-guard:旧task finally不清新task索引。
- 跑 `cd /g/projects/deskpet/backend && .venv/Scripts/python.exe -m pytest tests/ -k ppt -q` 到绿。

完成后简述新增符号 + 单测覆盖 + 你如何处理 async handler 注册（registry 约定）。