# PPT 能力优化 — DeepResearch 调研 → 大纲确认 → 惊艳生图(gpt-image-2) / 模板兜底

> **状态**: **v1.1 — 用户 review 拍板 4 项决策已并入（§7）。其中决策 2「改 FP-5 风格独立大纲卡 + 存历史」是 F3 实质架构变更，已重写 WI-3/WI-4 + 新增 WI-3 历史持久化，并对该 delta 跑 R4 对抗复核（见状态尾）。**
> **对抗轨迹**：R1 codex 4B+3M / architect 2B+4M → R2 codex 1B+3M → R3 codex **EXECUTABLE-AS-IS**（v1.0 LOCKED）→ v1.1 并入用户决策 → R4 对 F3 大纲卡 delta 复核。挑战记录见 `.challenge-r{1,2,3,4}.out`。
> **建档**: 2026-06-21
> **作者**: Claude (Lead) · 对抗：codex gpt-5.5（只读，2 轮）+ architect 子代理
> **前置阅读**: [STATUS/PPT.md](../../STATUS/PPT.md) · [STATUS/DeepResearch.md](../../STATUS/DeepResearch.md) · [STATUS/AgentLoop.md](../../STATUS/AgentLoop.md)
>
> **v0.1→v0.2（R1）关键修正**：① 确认/调研环**拆独立 asyncio.Task**、handler 秒回（修 FP-5 同款 preempt-cancel BLOCKING）；② 配置文件是 `backend/config.py` 非 `deskpet/config.py`，走 `standalone_config_section` 避开 `config.config` 坏读法（BLOCKING）；③ 注入经 `set_ppt_pro_services`，非 `ppt_tools.set_session_context`（BLOCKING）；④ F4 回退增加 **4xx model_unavailable** 触发（BLOCKING）；⑤ `_degrade_to_template` 清空 image_prompt + `skip_image_gen` 双保险防二次烧图；⑥ 大纲**双模式产出**防回退塌方；⑦ 修订环传 prev_slides；⑧ 前端「让我改改」进编辑态；⑨ 新增 WI-10。
> **v0.2→v0.3（R2）关键修正**：⑩ 4xx 判定改**分层**（status_code→error.code→多语言文案兜底+诚实声明）+「全图失败也回退」二次兜底 + 真实样例测试（BLOCKING）；⑪ `_PPT_PRO_RUNNING[sid]` **同会话去重**防 AgentLoop 重复调起第二个 task（MAJOR）；⑫ 后台编排加**总超时 1200s + `/stop`/same-sid 取消入口 + 清理**（MAJOR）；⑬ WI-10 **闭合通道**：注入 `artifact_pusher`+`receipt_reporter` 后台主动推成品卡/对账（MAJOR）；⑭ 删 §4 残留 `deskpet/config.py` 行 + WI-8 加「LLM 见 status 不重复调」。

---

## 0. 需求（用户原话）

> 优化 PPT 的能力：用户生成 PPT 给了主题以后，**用 deepresearch 的能力进行充分的调研**，然后**生成大纲**，**和用户确认大纲是否 ok**，ok 的话就**优先用 gpt-image-2 生成惊艳的 PPT**；如果**连接不上 gpt-image-2，则用模板的方式生成 PPT**。

拆成 4 个**不可裁剪**的功能点：

| # | 功能点 | 验收口径 |
|---|---|---|
| **F1** | 主题 → 用 deepresearch 充分调研 | 生成前真调 `deepresearch()` 管线，大纲内容有调研来源支撑（非凭空编） |
| **F2** | 调研结果 → 生成结构化大纲 | LLM 基于调研报告产出 `list[SlideOutline]`，落到可渲染的 outline JSON |
| **F3** | 和用户确认大纲（可改） | 暂停 agent，前端弹「大纲确认卡」展示多行大纲，用户可「确认生成 / 提修改意见」；改了重拟再确认 |
| **F4** | 确认后优先 gpt-image-2 惊艳生图；连不上回退模板 | 默认走 `image_full` AI 整页生图；**探测+首图实测**判定 gpt-image-2 不可达 → **整副回退模板路径**，不留占位图残页 |

---

## 1. 现状摘要（来自代码测绘，含真实行号）

> 详见 §1 各文件实测。下列行号为 2026-06-21 实测，实现时以源码 grep 为准（见 §9 实现纪律）。

### 1.1 PPT 工具层（`backend/deskpet/tools/ppt_tools.py`，~3860 行）
- 入口 `ppt_create(outline, *, theme, title, author, output_path, template, dry_run)` @**3152**；handler `_handle_ppt_create` @**3750**；注册 `_register_ppt_tool()` @**3834-3860**（`timeout_seconds=1200.0`, `permission_category="write_file"`, `concurrency_safe=False`）。
- 分派：`wants_fullbleed` @**3232**（`any(layout=="image_full" and image_prompt)`）→ `chosen_template` @**3235**（`template or (None if wants_fullbleed else _default_template())`）。
- 生图触发 @**3239-3248**：`has_prompts and (chosen_template or has_img_layout)` → `_autofill_image_prompts(slides)` @**1867**；否则 `_assign_image_layouts` @**1834**。
- 三渲染路径：`_render_with_design_pages` @**2950**（模板设计页≥3）/ `_render_with_template` @**3042** / `_render_fromscratch` @**3302**。
- **生图失败现状（关键）**：`_autofill_image_prompts` @**1899-1905** 单页失败只 `log.debug`、`image_path` 留 `None`、**不抛异常不回退**；`_render_image_full_v2` @**1539** 见 `image_path=None` → 渲染成「占位图」（深色底+IMG 徽章+"image placeholder"）。**当前没有任何「整副回退模板」机制**——这是 F4 的核心缺口。
- 异步：`_handle_ppt_create` @**3771-3818**，条件 `n_imgs>=1 and not dry_run and worker.alive() and _ppt_async_enabled()` → `_bg_job` 后台跑 + `worker.notifier(sid, "✨...")` + 立即返回 `{"ok":True,"status":"generating"}`；失败降级同步。
- outline 结构 `SlideOutline` dataclass @**160-191**（字段：`layout/title/subtitle/bullets/left/right/left_title/right_title/image_path/image_prompt/image_variant/font_scale/caption/quote/cite/chart/notes`）；`parse_outline` @**308** 宽容解析；`normalize` @**193**。

### 1.2 配图（`backend/deskpet/tools/image_tools.py`）
- `_generate_png(prompt, size, model) -> (bytes|None, err|None)` @**206**，**从不抛异常**；`generate_images(prompts, *, size, model) -> list[{prompt,path,error}]` @**337**。
- 超时 `_TIMEOUT=httpx.Timeout(connect=10, read=300, write=30, pool=30)` @**52**；`_MAX_ATTEMPTS=2`；失败分类 @**250-324**：连接类（ConnectTimeout/RemoteProtocolError/ConnectError/ReadError/WriteError/ProtocolError/SSLError/502/503）记 `last_transient` **可重试**；读超时/504/4xx/其它 **立即返回 (None, err)**。
- **可据「error 文本/异常类」区分「连接类失败（=连不上 gpt-image-2）」vs「内容/4xx 失败」**——F4 回退判定要用到这点（见 §3 WI-2）。

### 1.3 DeepResearch（`backend/deskpet/tools/research_tools.py`，~2282 行）
- 核心 `async def deepresearch(topic, *, llm_call, search=None, extract=None, max_sub_questions=5, max_urls_per_query=4, max_total_passages=12, min_passage_chars=250, max_rounds=1, mode="standard", user_request=None, scheduler=None, parent_sid="default", _depth=0, skip_plan=False) -> ResearchReport` @**1338**。
- **library-only，可直接 `await deepresearch(...)`**（模块注释明示「no FastAPI/IPC glue」）。唯一必填非默认参数是 `llm_call`。
- 依赖注入（main.py lifespan 一次性，全局透明）：`set_live_llm_call` @main.py**674** / `set_rerank_llm_call` @**692** / `set_semantic_scorer` @**1146** / `set_subagent_scheduler` @**2018**。内部调用方拿 LLM：`_resolve_default_llm_call()` @research_tools**2215**（优先返回已注入的 `_LIVE_LLM_CALL`，否则按 config+keychain 重建）。
- 返回 `ResearchReport` dataclass @**677**：`topic/summary/report_md/citations:list[Citation]/sub_questions/coverage:dict/errors`。**注意**：落盘 `path`/`artifacts` 不在 `ResearchReport` 里，只在 handler `_handle_deepresearch` @**2111** 落盘后追加；落盘函数 `_save_report` @**2174** + `_update_deepresearch_index` @**2007**，目录 `paths.deepresearch_dir()` @paths**142**。
- 档位 `_DEPTH_PRESETS` @**2104**：light `(3,2,8,1)` / standard `(5,4,12,1)` / deep `(6,5,16,2)`（子问题/每问URL/段落上限/反思轮）。全局工具超时 `_DEEPRESEARCH_TOOL_TIMEOUT=300.0` @**491**；**直调无内置 timeout**，需调用方自加 `asyncio.wait_for`。

### 1.4 暂停等用户确认（两套机制）
- **`ask_clarification`** 工具：`code_tools/clarify_tool.py`（schema @**15-33**、`build_clarification_ask` @**50-91**、`resolve_clarification_response` @**36-47**）。main.py 内联了等价闭包 `_clarify_ask` @**530-552**（`_clarify_pending: dict[str,Future]` @**500**，推 `clarification_request` 到 `_control_connections[session_id]`，`await asyncio.wait_for(fut, 120)`）；注册 @**2175-2184**；回灌 @**4181-4195**（`clarification_response` → `resolve_clarification_response` → `fut.set_result`）。
- 前端 `ClarificationDialog.tsx`（`tauri-app/src/components/`）+ `useClarificationRequests.ts`（hook，监听 `/ws/control` 的 `clarification_request`）；挂载 `App.tsx` @**723-724/1719-1722**（用 `permissionChannel`，pet 窗口 `/ws/control?session_id=default`）。类型 `skillPlatform.ts` @**57-73**。
- **结论（v1.1 用户决策）**：采用 **FP-5「技能候选卡」同款的独立卡片机制**（非 ask_clarification）——因为用户要「存历史大纲可复用」。范本完整可复制：`skill_codifier.SkillCandidateWaiters` + `_maybe_codify_skill` 的 Future-await 独立 task + `skill_candidate_proposed`/`skill_candidate_confirm` WS 双向 + `SkillCandidateCard` 持久化卡。详见 WI-3/WI-4。

### 1.5 Envelope 现状
```jsonc
// agent → 前端
{"type":"clarification_request","payload":{"request_id":"<uuid>","question":"...","options":["..."]}}
// 前端 → agent
{"type":"clarification_response","payload":{"request_id":"<uuid>","answer":"..."}}
```

---

## 2. 架构决策

### 2.1 核心决策：新建**单工具确定性编排** `ppt_pro`（而非让 LLM 多步编排）

**为什么不用 LLM 编排（让 SKILL.md 指挥 LLM 依次调 deepresearch→ask_clarification→ppt_create）**：
1. **窗口压力**：PPT.md §9.1 实测 gpt-5.5 桌宠窗口仅 8000，把整份调研报告塞进上下文再多步编排极易爆窗/丢步。
2. **抗漂移**：STATUS 记录了大量「跨层 / 多步」漂移 bug（task-drift、fanout-gating 7 处死链等）。多步 LLM 编排是漂移高发区。
3. **确定性**：F4「连不上回退模板」是确定性判定，必须在代码里做，不能靠 LLM「感觉」。

**决策**：新建工具 `ppt_pro(topic, *, depth, pages, theme, ...)`，agent **只调一次**，handler 内部按确定性管线跑 F1→F2→F3→F4。confirm 之中途暂停复用 `_clarify_ask`（已验证 tool handler 内可 `await` 挂起）。保留旧 `ppt_create` 不动（直接出图/直接给 outline 的简单场景 + 作为 `ppt_pro` 的渲染后端）。

```
LLM 一次调用:  ppt_pro(topic="新能源电池技术发布会", depth="standard", pages=8)
  │
  ├─ Stage A [F1] 调研   await deepresearch(topic, llm_call, mode=depth映射)  (含 asyncio.wait_for 超时)
  │        → 推「🔍 正在深度调研…」 / 完成推「📚 调研完成，N 源」
  ├─ Stage B [F2] 拟纲   await _draft_outline_from_research(topic, report, pages, llm_call)
  │        → list[SlideOutline]（默认 image_full + image_prompt = 惊艳模式）
  ├─ Stage C [F3] 确认   await _clarify_ask(question, options=["确认生成","让我改改"], content_md=大纲MD, sid, timeout=300)
  │        ├─ "确认生成" / 空答(超时视取消) → 进 D
  │        ├─ 自由文本(修改意见) → _redraft_outline(feedback) → 回 Stage C（≤ _PPT_PRO_MAX_REVISIONS 轮）
  │        └─ "取消" → 优雅返回「已取消」
  └─ Stage D [F4] 渲染   _render_pro(slides, ...)
           ├─ probe + 首图实测 gpt-image-2 可达 → 走 image_full 惊艳路径(复用 ppt_create 生图+视觉闭环)
           └─ 不可达 → _degrade_to_template(slides) + chosen_template=默认大类 → ppt_create 模板路径
                       + 推「⚠️ AI 配图暂时连不上，已用精美模板生成」
           最终落盘 + 预览图 artifact + notifier 推回 + 自动打开
```

### 2.2 F4 回退判定（双层，确定性 — v0.2 修 BLOCKING：4xx 模型不可用也要回退）
1. **预探测**（便宜、免费）：`image_tools.probe_image_reachable(timeout=...)` —— `trust_env=False` 直连 relay base `/v1/models`（已知 200，见 STATUS 06-09），connect 5s/read 8s。返回 `False` 即 relay 整体不可达 → 直接走模板，**不烧任何生图钱**。
   - **注意**：probe `/v1/models` 200 只证明 relay 活着，**不证明 gpt-image-2 模型可用**（codex BLOCKING）→ 必须靠第 2 层兜。
2. **首图实测**（精确）：预探测过了，先只生**第 1 张**图；按返回的 `error_kind`（WI-6b 新增）判定：
   - **触发整副回退模板**（= 广义「连不上 gpt-image-2」）：
     - `connectivity`：网络/服务层（connect/read-timeout/5xx/SSL/protocol）。
     - `model_unavailable`：4xx 中「model 不支持 / model_not_found / 该 relay 无 gpt-image-2」（codex 实测 `image_tools.py:267-277` 非 200 错误文案含「model 不支持」）——**这正是用户说的「连接不上 gpt-image-2」**，必须回退。
   - **不回退**（占位降级，避免误伤）：
     - `content`/`safety`：4xx 内容/safety 拒绝（个别 prompt 触发，换图或占位即可，非整体不可用）。
     - `auth`/`quota`：认证/额度——**不静默占位**，应明确通知用户「配图服务认证/额度有问题」（既非「连不上」也非「内容问题」，让用户知情）。
   - 首图成功 → 继续生其余图（惊艳路径）。

### 2.3 同步/异步边界（v0.2 重写 — 修 BLOCKING-1/2）

> **v0.1 错误**：原设计把 Stage A/B/C 同步 `await` 在 handler 内（agent turn 挂起 ~10min）。
> 这**精确复刻了 FP-5 当年修掉的 same-sid preempt bug**（`main.py:806-811` 注释）：确认期间用户
> 随便发一句话 → `main.py:6767-6770` 无条件 cancel 同 sid 上一个 chat task → `_clarify_ask` 的
> Future 被 cancel → 确认卡变僵尸、`ppt_pro` 静默死亡、零反馈。**必须拆独立 task。**

**v0.2 正确架构**（见 §2.5 详述）：
- `ppt_pro` = **async handler**，本体只校验参数 → `asyncio.create_task(_ppt_pro_orchestrate(...))` 在 **main loop**（uvicorn 单 loop，与 `_clarify_ask`/`_control_connections` 同 loop）起一个**独立编排 task** → **立即返回** `{"ok":True,"status":"researching","message":"正在调研主题…"}`。chat task 随即收尾，用户可继续对话；后续 preempt cancel **伤不到**独立编排 task（`create_task` 是 loop 级独立 task，非父子级联取消）。
- 独立编排 task 内：Stage A/B/C 串行 `await`（调研 → 拟纲 → 确认，**确认用 FP-5 风格独立大纲卡 `_ppt_outline_propose`**，同 loop 安全，WI-3）；Stage D 渲染（生图阻塞）走 `loop.run_in_executor(None, lambda: _render_pro(...))` 不阻塞 main loop。全程 notifier 推进度 + 推成品。
- **task 引用保活**：模块级 `_PPT_PRO_TASKS: set[asyncio.Task]` 持引用（asyncio 弱引用 task，不持会被 GC），`task.add_done_callback(_PPT_PRO_TASKS.discard)`。
- 这样 `ppt_pro` 注册的 `timeout_seconds` 只约束秒回的 handler，**不再需要 1800s 兜 10min**（编排 task 自管超时）。

### 2.5 独立编排 task 骨架（v0.2 新增 — FP-5 范本）

照搬 `main.py:803-835`（FP-5 `_await_candidate_decision` + `create_task`）经过实战验证的模式：

```python
_PPT_PRO_TASKS: set[asyncio.Task] = set()   # 模块级，保活引用

async def _ppt_pro_orchestrate(*, topic, pages, depth, theme, image_mode,
                               title, author, output_path,
                               outline_propose, notifier, run_blocking, session_id):
    """独立 task：调研→拟纲→(大纲卡)确认→渲染。在 main loop 上跑，不受 chat preempt 影响。"""
    try:
        await notifier(session_id, "🔍 正在围绕主题做深度调研…")
        report = await _research_topic_for_ppt(topic, depth=depth, timeout_s=research_to)  # WI-1（deep档~5min）
        if cfg.pro_save_research and report: _save_and_index_research(topic, report)       # WI-1 默认落盘
        n_src = len(report.citations) if report else 0
        await notifier(session_id, f"📚 调研完成（{n_src} 个来源），正在拟大纲…" if report
                       else "📝 调研未取得来源，按通用知识拟大纲…")
        llm = await _resolve_default_llm_call()                                            # WI-2 锁定 LLM 源
        slides = await _draft_outline_from_research(topic, report, pages=pages, theme=theme,
                                                    image_mode=image_mode, llm_call=llm)
        # 确认环（FP-5 风格大纲卡，≤ max_revisions，传 prev_slides 防整盘重拟 — WI-3）
        confirmed = False
        for _ in range(max_revisions + 1):
            d = await outline_propose(session_id, topic=topic, slides=slides, sources_count=n_src,
                                      outline_md=_outline_to_markdown(slides), no_research=(report is None))
            act = d.get("action")
            if act == "accept": confirmed = True; break
            if act == "reuse":            # 复用历史大纲
                slides = parse_outline(_outline_store.get_outline(d["reuse_id"])["slides_json"])
                confirmed = True; break
            if act == "cancel":
                await notifier(session_id, "好的，已取消，没有生成 PPT。"); return
            # act == "modify"
            slides = await _draft_outline_from_research(topic, report, pages=pages, theme=theme,
                                                        image_mode=image_mode, llm_call=llm,
                                                        feedback=d.get("feedback",""), prev_slides=slides)
        if not confirmed:
            await notifier(session_id, "大纲改了好几轮还没定，先暂停啦，需要再叫我~"); return
        # 渲染（阻塞 → executor，不卡 main loop；render 阶段独立超时，绕过 registry 1200s 故自包）
        await notifier(session_id, "✅ 大纲已确认，开始生成…")
        result = await asyncio.wait_for(
            run_blocking(lambda: _render_pro(slides, theme=theme, title=title,
                    author=author, output_path=output_path, image_mode=image_mode,
                    probe_timeout_s=probe_to, notify=_sync_notify(notifier, session_id))),
            timeout=render_to)   # _ppt_pro_cfg().render_timeout_s = max(600, pages*120)
        await _ppt_pro_report_done(result, notifier, session_id)        # WI-10 receipt/artifact + 推成品 + 自动打开
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.exception("ppt_pro orchestrate failed")
        await notifier(session_id, f"😿 PPT 没做成：{e}")
```
- `run_blocking` = 注入的 `lambda coro_fn: loop.run_in_executor(None, coro_fn)`（main loop 的 executor）。
- `notifier` = 注入的 main-loop 推送（control WS 或 `worker.notifier`，确认 notifier 也在 main loop）。
- `outline_propose` = 注入的 `_ppt_outline_propose`（WI-3，FP-5 风格大纲卡 propose+await+history）。
- handler（WI-7）：`t=asyncio.create_task(_ppt_pro_orchestrate(...)); _PPT_PRO_TASKS.add(t); t.add_done_callback(_PPT_PRO_TASKS.discard); return {"ok":True,"status":"researching","message":"正在调研主题，稍等我把大纲拟出来给你确认~"}`。

### 2.6 Flag / BC
- 新增 `[ppt].pro_enabled`（默认 **True**——这是用户主诉求，要开箱可用；但保留开关可关）。
- `ppt_pro` 是**新增工具**，不改 `ppt_create` 字节行为 → 对现有调用零 BC 风险。
- `_clarify_ask` 加 `timeout` 形参（默认 120 保 BC），`clarification_request` payload 加**可选** `content_md`（旧前端忽略未知字段，BC）。

---

## 3. 工作项（WI）— 逐文件逐函数

> 每个 WI 标注：文件、函数、改动类型（新增/改）、伪代码/diff、验收。

### WI-0 · 配置与开关（v0.2 修 BLOCKING — 文件路径错了）
> **codex 实测**：`backend/deskpet/config.py` **不存在**；配置实现在 **`backend/config.py`**（`AppConfig.raw` @456，`standalone_config_section()` @870）。且 `config.py:874` 注释明确「工具拿不到 config 单例」——`ppt_tools.py` 现有 `_ppt_async_enabled`/`_ppt_preview_render_enabled` 用的 `_cfg.config.raw` 是**已知坏模式**（STATUS 多次记录 `config.config` 单例不存在致配置静默失效）。

**文件** `backend/config.py`
- 在 PPT 配置读取处加 6 个 pro_* 项。**读法对齐健壮方式**：用 `standalone_config_section("ppt")`（@870）或 `raw.get("ppt", {})`，**不要**用 `_cfg.config.raw`。
- 字段（**v1.1 按用户决策**）：`pro_enabled: bool = True`、`pro_default_depth: str = "deep"`（用户要「5 分钟」充分调研 → deep 档 6 子问题/2 轮反思）、`pro_max_revisions: int = 2`、`pro_research_timeout_s: float = 360.0`（~6min 上界容 5min 调研）、`pro_confirm_timeout_s: float = 1800.0`（大纲卡等用户更久，FP-5 卡用 300s；这里给 30min 宽松，超时按取消）、`pro_image_probe_timeout_s: float = 8.0`、`pro_render_timeout_s: float = 0.0`（0=用 `max(600, pages*120)` 动态默认）、`pro_save_research: bool = True`（用户要默认落盘）、`pro_outline_history: bool = True`（存历史大纲，见 WI-3）。
- **`ppt_tools.py` 侧**：新增 `_ppt_pro_cfg()` 读取（仿现有 `_ppt_async_enabled` 的 toml 读取风格，但走 `standalone_config_section`，**顺手把现有 `_cfg.config.raw` 坏读法一并修正**，对齐 STATUS 06-15/06-20 已修的 `[research]` 同款 bug）。
**验收**：`test_config.py`/`test_ppt_*` 加 1 例验证默认值 + toml 覆盖真生效（不被 `config.config` 坏读法吞）；flag-off 时 `ppt_pro` 不注册。

---

### WI-1 · DeepResearch 调研封装（F1）
**文件** `backend/deskpet/tools/ppt_tools.py`（新增函数，靠近文件头工具区）
新增：
```python
async def _research_topic_for_ppt(topic: str, *, depth: str, timeout_s: float) -> "ResearchReport | None":
    """为 PPT 调研主题，返回 ResearchReport（report_md/citations/coverage）。失败返回 None（不阻断，降级为无调研直接拟纲）。"""
    from deskpet.tools.research_tools import deepresearch, _resolve_default_llm_call, _DEPTH_PRESETS
    try:
        llm = await _resolve_default_llm_call()
    except Exception as e:
        log.warning("ppt_pro research: no llm_call (%s) → skip research", e)
        return None
    sub_q, urls, passages, rounds = _DEPTH_PRESETS.get(depth, _DEPTH_PRESETS["standard"])
    try:
        return await asyncio.wait_for(
            deepresearch(
                topic, llm_call=llm,
                max_sub_questions=sub_q, max_urls_per_query=urls,
                max_total_passages=passages, max_rounds=rounds, mode=depth,
                user_request=f"为制作 PPT 调研主题：{topic}",
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        log.warning("ppt_pro research timeout → degrade to no-research outline")
        return None
    except Exception as e:
        # MINOR-3：不要 except Exception 一把吞。区分「网络/搜索瞬时失败」(降级 None)
        # 与「配置/认证错误」(应让用户知道,否则静默退化成凭空编,违背 F1)。
        if _is_config_or_auth_error(e):
            raise                       # 上抛 → 编排 task 推「调研服务没配好…」给用户
        log.warning("ppt_pro research failed: %s → degrade to no-research outline", e)
        return None
```
**设计要点**：
- `_is_config_or_auth_error(e)`：识别 401/403/key 缺失/provider 未配置等硬错误（与 `image_tools` 401 落警告同源思路）；其余（搜索被封/抓取失败/网络瞬时）才降级。
- **复用 `_DEPTH_PRESETS`**（codex 实测：已是 research_tools 顶层常量 @**2104**，**直接 import 即可**，无需重构）。
- **降级而非失败（F1，吸收 codex MAJOR）**：调研「网络/搜索」类失败 → 返回 None，但**不静默当成 F1 完成**：Stage C 确认卡的问题文案改为显式告知「⚠️ 未取得调研来源，下面大纲基于通用知识，是否仍要生成？」让用户**知情后再确认**（不偷偷出「正式 PPT」）。「配置/认证」类失败 → 上抛（WI-1 已区分）。
- **默认落盘（v1.1 用户决策）**：`pro_save_research=True` → 调研完成后调 `_save_report(topic, report)` + `_update_deepresearch_index(...)`（与 deepresearch handler 同路，落 `paths.deepresearch_dir()` + 倒序索引），让 PPT 的调研报告也进 DeepResearch/ 可复用。`pro_save_research=False` 可关。

**验收**：mock `deepresearch` 的单测——(a) 正常返回 report；(b) 网络失败/超时返回 None；(c) 配置/认证错误**上抛**（不吞）；(d) 无 llm_call 上抛配置错误。

---

### WI-2 · 调研 → 大纲拟制 + 修订（F2）
**文件** `backend/deskpet/tools/ppt_tools.py`（新增）
```python
async def _draft_outline_from_research(
    topic: str, report: "ResearchReport | None", *, pages: int, theme: str,
    image_mode: bool, llm_call, feedback: str = "", prev_slides=None,
) -> list[SlideOutline]:
    """基于调研报告（可空）拟 PPT 大纲。
    **双模式产出（MAJOR-2）**：无论 image_mode，每页同时产出
      ① image_prompt（惊艳路径用） ② 充实的 bullets/subtitle（模板回退路径用）。
    渲染时按可达性二选字段，**不做有损 image_full→bullet 转换**，回退也不塌方。
    feedback 非空 = 用户修改意见；prev_slides 非空 = 在其基础上增量改（MAJOR-3）。"""
    research_md = (report.report_md if report else "")[: _PPT_PRO_RESEARCH_CTX_CHARS]  # 截断防爆窗，默认 6000
    prompt = _build_outline_prompt(topic, research_md, pages=pages, image_mode=image_mode,
                                   feedback=feedback,
                                   prev_md=(_outline_to_markdown(prev_slides) if prev_slides else ""))
    raw = await llm_call(prompt)
    slides = parse_outline(raw)              # 复用现有宽容解析
    if not slides:                           # 解析失败兜底：最小可用大纲
        slides = _fallback_minimal_outline(topic, pages, image_mode)
    return slides
```
- **`llm_call` 源锁定（MINOR-4）**：调用方（§2.5 编排 task）传入 `await _resolve_default_llm_call()`（research/主 LLM，窗口/`max_tokens` 较大），**不得**用桌宠 8000 窗口聊天模型，否则 6000 调研 + 系统 prompt 爆窗。
- 新增 `_build_outline_prompt(...)`：要求输出 §1.1 `SlideOutline` 兼容 JSON 数组，**每页同时含**：
  - `title` + `bullets`（3-5 条充实要点，模板路径直接用，密度匹配设计页）
  - `image_prompt`（惊艳路径用：电影感、负空间、禁字后缀，复用 `_assign_image_layouts` prompt 习惯）
  - `layout`：惊艳模式标 `image_full`（渲染时若回退会被 `_degrade_to_template` 按 bullets 改版式，但 bullets 已充实不塌）。
  - 强约束「内容必须基于以下调研报告，引用其中数据/事实，不得编造」+ 附 `research_md`。
  - `prev_md` 非空时 prompt 改为「这是当前大纲 + 用户要改 X，**只改动相关部分，保留其余页**」（增量修订，不整盘重拟）。
- 新增 `_outline_to_markdown(slides) -> str`：渲染给用户看的多行大纲 MD（页码 + 标题 + bullets 缩进），用于确认卡 `content_md` 与 `prev_md`。
- 新增 `_fallback_minimal_outline(topic, pages, image_mode)`：纯本地兜底（封面+pages-1 内容页占位），保证「LLM 全挂也能出大纲」。
- 常量 `_PPT_PRO_RESEARCH_CTX_CHARS = 6000`。

**验收**：单测——(a) 有 report 拟纲**同时含 image_prompt 与充实 bullets**；(b) report=None 仍出大纲；(c) feedback+prev_slides 注入重拟（mock llm 校验 prompt 含 feedback 与 prev_md，且指令为「增量改」）；(d) llm 返回垃圾 → `_fallback_minimal_outline`；(e) `_outline_to_markdown` 多页正确成文。

---

### WI-3 · 独立「大纲确认卡」后端（F3 后端 — v1.1 改 FP-5 风格 + 历史持久化）

> **用户决策（v1.1）**：不复用 `ask_clarification`，改用 **FP-5「技能候选卡」同款的独立卡片机制**——支持持久化（reload 不丢卡）+ **存历史大纲可复用**。范本：`skill_codifier.py`（`SkillCandidateWaiters`/`propose`/`confirm`）+ `main.py:709-835`（`_maybe_codify_skill` 的 Future-await 独立 task + WS 推送 + 回灌）。
> **天然契合**：`ppt_pro` 编排已是独立 task（R-4 修复），在其中 propose 卡 + await Future 不受 chat preempt 影响——比 ask_clarification 更顺。

**文件 A 新增** `backend/deskpet/tools/ppt_outline_store.py`（仿 `skill_codifier` 的 waiters + 仿 `receipt_store`/SessionDB 的持久化）：
- `class PPTOutlineWaiters`：`dict[str, asyncio.Future]`，`add(oid, fut)/resolve(oid, decision)/pop(oid)`（照搬 `SkillCandidateWaiters` @skill_codifier:285-317）。
- **历史持久化**（用户要「存历史大纲」）：`ppt_outline_history` 表（SessionDB，仿 `ensure_session_goals_table` 的 flag-gated 建表，`pro_outline_history` 开才建，保 BC 字节基线）。字段：`outline_id TEXT PK, session_id, topic, created_at, slides_json TEXT, sources_count INT, status TEXT(proposed/accepted/rejected/superseded)`。
  - `save_outline(oid, sid, topic, slides, sources)` / `mark_status(oid, status)` / `list_history(sid, limit=20)` / `get_outline(oid)`（供「从历史复用」）。

**文件 B 新增** `backend/main.py`（仿 `_maybe_codify_skill` @709-835，但由 ppt 编排 task 主动调，不走 turn-end hook）：
- 模块级 `_PPT_OUTLINE_WAITERS = PPTOutlineWaiters()`。
- `async def _ppt_outline_propose(sid, *, topic, slides, sources_count, outline_md, no_research) -> str(decision)`：
  ```python
  oid = uuid4().hex
  if cfg.pro_outline_history: outline_store.save_outline(oid, sid, topic, slides, sources_count)
  ws = _control_connections.get(sid) or _control_connections.get("default")
  await ws.send_json({"type": "ppt_outline_proposed", "payload": {
      "outline_id": oid, "topic": topic, "outline_md": outline_md,
      "sources_count": sources_count, "no_research": no_research,
      "history": outline_store.list_history(sid, 20) if cfg.pro_outline_history else []}})
  fut = loop.create_future(); _PPT_OUTLINE_WAITERS.add(oid, fut)
  try:    decision = await asyncio.wait_for(fut, cfg.pro_confirm_timeout_s)   # {action, feedback?, reuse_id?}
  except asyncio.TimeoutError: decision = {"action": "cancel"}
  finally: _PPT_OUTLINE_WAITERS.pop(oid)
  outline_store.mark_status(oid, _status_of(decision))
  return decision
  ```
- WS 回灌（仿 `skill_candidate_confirm` @main.py:4867-4882）：control WS 收 `ppt_outline_decision` → `_PPT_OUTLINE_WAITERS.resolve(oid, {action, feedback, reuse_id})`。`action ∈ {accept, modify, cancel, reuse}`。
- **注入**：经 `set_ppt_pro_services(...)` 把 `_ppt_outline_propose`（绑定 sid 由编排传）注入给 ppt 编排 task（替代原 `clarify`）。注入时机/loop 同 WI-7（main loop）。

**文件 C** §2.5 编排确认环改用卡：
```python
for _ in range(max_revisions + 1):
    d = await outline_propose(sid, topic=topic, slides=slides,
            sources_count=n_src, outline_md=_outline_to_markdown(slides), no_research=(report is None))
    act = d.get("action")
    if act == "accept": confirmed = True; break
    if act == "cancel": await notifier(sid, "好的，已取消~"); return
    if act == "reuse":                                   # 从历史大纲复用
        slides = parse_outline(outline_store.get_outline(d["reuse_id"])["slides_json"]); confirmed = True; break
    if act == "modify":
        slides = await _draft_outline_from_research(topic, report, pages=pages, theme=theme,
                    image_mode=image_mode, llm_call=llm, feedback=d.get("feedback",""), prev_slides=slides)
```
**验收**：单测——(a) propose 推 `ppt_outline_proposed`+注册 Future+落 history；(b) `ppt_outline_decision(accept/modify/cancel/reuse)` 正确 resolve；(c) 超时→cancel；(d) history flag off 不建表（字节 BC）；(e) reuse 从历史取回 slides；(f) modify 触发 redraft(prev_slides)。

---

### WI-4 · 独立「大纲确认卡」前端 `PPTOutlineCard`（F3 前端 — v1.1 改 FP-5 风格）
> 范本：`SkillCandidateCard`（`MessageBubble.tsx:681-710`）+ `ws.ts:360-375`（`skill_candidate_proposed`→push_message）+ `sessionsStore.ts:364-388`（resolve）。**持久化卡**（reload 保留，仿 skill_candidate 的 `set_messages` 保 awaiting 卡）。

**文件 A** `tauri-app/src/code-panel/ws.ts`（仿 :360-375）：`case "ppt_outline_proposed"` → `store.push_message(sid, {role:"ppt_outline", ppt_outline_awaiting:true, outline_id, topic, outline_md, sources_count, no_research, history})`。
**文件 B** `tauri-app/src/stores/sessionsStore.ts`：加 `ppt_outline` message role + 字段（仿 skill_candidate 字段 @27/54-62）；`resolve_ppt_outline(sid, oid, decision)` 清 awaiting flag。
**文件 C 新增** `tauri-app/src/code-panel/PPTOutlineCard.tsx`（仿 `SkillCandidateCard`）：
- 渲染 `outline_md`（markdown 滚动区）+ `sources_count`（"📚 N 个调研来源"）+ `no_research` 时显"⚠️ 无来源"提示（**不显费用，用户决策 4**）。
- 按钮：「✅ 确认生成」→ send `{type:"ppt_outline_decision", payload:{outline_id, action:"accept"}}`；「✏️ 修改」→ 展开 textarea，提交 → `action:"modify", feedback`；「✖ 取消」→ `action:"cancel"`。
- **历史复用**（用户决策 2）：`history` 非空 → 折叠区「📜 历史大纲」列出过往 topic+时间，点某条 → `action:"reuse", reuse_id`。
- 发送后清 `ppt_outline_awaiting`（按钮消失），走 `codePanelWS.send` + pet 窗口 control WS 两通道（卡可能挂在主消息面板，对齐 STATUS 06-21 SubagentProgressPanel 双挂载教训）。
**文件 D** 挂载：`MessageBubble.tsx`（code panel）+ 主消息面板（`MessageStreamPanel`，对齐 STATUS 06-21 子代理面板双挂载）渲染 `role==="ppt_outline"` 的卡。
**验收**：`vitest`——(a) 卡渲染 outline_md+来源数+无费用字样；(b) accept/modify/cancel/reuse 各发对 envelope；(c) reload 保留 awaiting 卡（持久化）；(d) 历史区点击发 reuse_id。

---

### WI-5 · gpt-image-2 可达探测（F4 第 1 层）
**文件** `backend/deskpet/tools/image_tools.py`（新增）
```python
def probe_image_reachable(*, timeout_s: float = 8.0) -> bool:
    """便宜探测 relay images 服务是否可达：GET <base>/v1/models，trust_env=False 直连。
    仅判网络/服务层可达，不验 gpt-image-2 模型本身（那交给首图实测）。"""
    base, key = _resolve_relay_base_and_key()        # 复用 image_tools 现有 base/key 解析
    if not base:
        return False
    try:
        with httpx.Client(trust_env=_image_trust_env(), timeout=httpx.Timeout(connect=5.0, read=timeout_s, write=5.0, pool=5.0)) as c:
            r = c.get(base.rstrip("/") + "/v1/models", headers=({"Authorization": f"Bearer {key}"} if key else {}))
            return r.status_code < 500
    except Exception as e:
        log.info("image probe unreachable: %s", e)
        return False
```
- 复用现有 base/key/`trust_env` 解析（grep `trust_env`/`/images/generations`/base url 组装）。若现成函数没有独立的 base 解析，抽一个 `_resolve_relay_base_and_key()` 内部 helper（与 `_generate_png` 用同一来源，避免漂移）。
**验收**：单测 mock httpx——200/404→True（<500 视可达，404 也算「服务在」）、503/连接异常→False。

---

### WI-6 · 整副回退模板（F4 第 2 层 + 渲染编排）
**文件** `backend/deskpet/tools/ppt_tools.py`
- 新增 `_degrade_to_template(slides) -> list[SlideOutline]`：把 `image_full`+`image_prompt` 的页转成模板友好版式（`image_full`→`bullet`/`section`；清空 `image_prompt`/`image_path`；保留 title/bullets/subtitle）。**内容不丢，只换皮**。
- 新增首图实测封装（复用 `_autofill_image_prompts` 但带「首图 gate」）：
```python
def _autofill_with_connectivity_gate(slides) -> tuple[bool, int]:
    """先生第 1 张图；若连接类失败 → 返回 (False, 0) 表示判定不可达不再生图。
    成功 → 生其余图，返回 (True, n_ok)。返回 reachable 标志 + 成功页数。"""
    prompts = [(i, s) for i, s in enumerate(slides) if s.image_prompt]
    if not prompts: return (True, 0)
    i0, s0 = prompts[0]
    res0 = generate_images([s0.image_prompt], size=_size_for(s0))[0]
    if not res0.get("path"):
        if _should_fallback(res0):          # connectivity 或 model_unavailable → 不可达
            return (False, 0)
        # content/safety 失败：占位降级，继续后续（非「连不上」）
    else:
        slides[i0].image_path = res0["path"]
    # 生其余
    rest = generate_images([s.image_prompt for _, s in prompts[1:]], ...)
    n_ok = sum(1 for r in rest if r.get("path")) + (1 if slides[i0].image_path else 0)
    for (idx, s), r in zip(prompts[1:], rest):
        if r.get("path"): slides[idx].image_path = r["path"]
    if n_ok == 0:                    # v0.3 兜底：全图失败(含被误判 content)→ 整副回退,避免全占位烂 deck
        return (False, 0)
    return (True, n_ok)
```
- 新增 `_should_fallback(res: dict) -> bool`：`res.get("error_kind") in {"connectivity","model_unavailable"}`（直接读结构化 kind，**不靠脆弱中文文本匹配**）。
- **WI-6b（结构化失败分类，必做 — codex BLOCKING；v0.3 改判定优先级）** `image_tools.py`：`generate_images`/`_generate_png` 失败返回加键 `error_kind`（不破坏 `error` 字段，BC）。codex 实测 `image_tools.py:263-271` 现状：非 200 只把 `resp.json()` dump 成文本拼进 error string，**不保证有结构化 `error.code`**。所以判定**分层、诚实**（不能只承诺「不靠文案」）：
  - **第 1 优先：HTTP status_code**（最可靠）——connect/读超时/RemoteProtocolError/SSL/502/503/504 → `connectivity`；401/403 → `auth`；429 → `quota`。
  - **第 2 优先：解析返回体的结构化 `error.code`/`error.type`/`code`**（若 relay 提供）——`model_not_found`/`model_*unsupported*`/`invalid_model` → `model_unavailable`；`content_policy`/`safety` → `content`。
  - **第 3 兜底（承认局限）：多语言子串匹配**（relay 只给自然语言 message 时唯一手段）——message 含 `model`+(`not found`/`unsupport`/`不支持`/`不存在`/`无可用`) → `model_unavailable`。**plan 诚实声明：此层依赖 relay 文案，relay 改文案可能漏判 → 由「首图实测失败 + 整体失败率」二次兜底（见下），且测试必须覆盖真实 relay 4xx 样例。**
  - 其余 4xx → `content`；无法归类 → `unknown`。
  - **额外兜底（治 BLOCKING 残留）**：即便单图被误判成 `content` 没触发回退，`_autofill_with_connectivity_gate` 末尾若**所有页生图全失败（n_ok==0）**，也判定不可达 → 整副回退模板（避免「全是占位图」的烂 deck）。
  - **真实样例测试**：抓一次 relay 对「不存在的 model」的真实 4xx 响应体存进 testdata，单测断言被归为 `model_unavailable`。
- 新增 `_degrade_to_template(slides) -> list[SlideOutline]`（修 R-1 fallback 二次烧图）：
  - `image_full`→`bullet`/`section`；**清空 `image_prompt` 和 `image_path`**（否则 `ppt_create` @3239 的 `has_prompts` 仍为真会二次烧图！codex MAJOR）；保留 title/**充实 bullets**（WI-2 双模式已产）/subtitle。**内容不丢、密度匹配模板设计页**。
- 新增渲染编排 `_render_pro(slides, *, theme, title, author, output_path, image_mode, probe_timeout_s, notify) -> dict`：
```python
def _render_pro(...):
    use_template = not image_mode
    if image_mode and not probe_image_reachable(timeout_s=probe_timeout_s):
        use_template = True; notify("⚠️ AI 配图暂时连不上，已切换精美模板生成。")
    if image_mode and not use_template:
        reachable, n_ok = _autofill_with_connectivity_gate(slides)
        if not reachable:
            use_template = True; notify("⚠️ gpt-image-2 暂时用不了，已切换精美模板生成。")
    if use_template:
        slides = _degrade_to_template(slides)   # 已清空 image_prompt → 下面 ppt_create 不会二次生图
        return ppt_create(slides, theme=theme, title=title, author=author,
                          output_path=output_path, template=_default_category_template(),
                          skip_image_gen=True)              # 双保险
    # 惊艳路径：图已生好(image_path 填好)，跳过 ppt_create 内部二次生图
    return ppt_create(slides, theme=theme, title=title, author=author,
                      output_path=output_path, skip_image_gen=True)
```
> **R-1（双保险）**：①`_degrade_to_template` 清空 image_prompt；②给 `ppt_create` 加 keyword-only `skip_image_gen: bool=False`（@3239 的 `_autofill_image_prompts` 前加 `if not skip_image_gen:`，**默认 False = 字节 BC**），`_render_pro` 两条路径都传 True。两条都做，确保模板回退路径 + 惊艳已生图路径都不二次烧图（codex MAJOR：fallback 路径也会烧图）。
**验收**：单测——(a) probe False → 模板路径（`_degrade_to_template` + ppt_create skip_image_gen=True + template）；(b) 首图 connectivity → 回退；(c) **首图 model_unavailable（4xx 模型不支持）→ 回退**；(d) 首图 content → 不回退（占位继续）；(e) 全程成功 → 惊艳路径不二次生图；(f) `_degrade_to_template` 清空 image_prompt 且 bullets 充实不丢；(g) `ppt_create(skip_image_gen=False)` 默认行为字节 BC。

---

### WI-7 · `ppt_pro` 工具 handler（async 秒回 + 独立编排 task）+ schema + 注册（总装）
> v0.2 修 BLOCKING-1/2/3：handler 是 **async**、**秒回**、起独立 task；注入经 **registry session context**（不是 `ppt_tools.set_session_context`，那不存在）；handler 从 **args 读 `_session_id`**（同现有 `ppt_create` @**3769** 的读法）。

**文件** `backend/deskpet/tools/ppt_tools.py`
- 新增 `async def _handle_ppt_pro(**kwargs)`：
```python
_PPT_PRO_RUNNING: dict[str, asyncio.Task] = {}    # v0.3 按 session 去重（codex MAJOR：防重复调用起第二个 task）

async def _handle_ppt_pro(**kwargs):
    sid = kwargs.get("_session_id", "default")          # registry 注入进 args（同 ppt_create @3769）
    outline_propose = _PPT_PRO_CTX.get("outline_propose")  # WI-3 FP-5 风格大纲卡 propose
    notifier = _PPT_PRO_CTX.get("notifier")
    run_blocking = _PPT_PRO_CTX.get("run_blocking")
    if outline_propose is None or notifier is None:     # 接线缺失 → 不静默死，明确报错
        return {"ok": False, "error": "ppt_pro 接线缺失（outline_propose/notifier 未注入）", "fallback": "请用 ppt_create"}
    # ① 同 session 去重：已有在跑的 ppt_pro → 不起第二个（codex MAJOR）
    cur = _PPT_PRO_RUNNING.get(sid)
    if cur is not None and not cur.done():
        return {"ok": True, "status": "already_running",
                "message": "这个会话已经有一份 PPT 在做啦（调研/等确认/生成中），先把那份弄完哈~"}
    async def _runner():
        try:
            await _ppt_pro_orchestrate(        # ② 不用单一总 wait_for（见下「分阶段限时」）
                topic=kwargs["topic"], pages=kwargs.get("pages", 8),
                depth=kwargs.get("depth") or _ppt_pro_cfg().default_depth,
                theme=kwargs.get("theme", "minimal"), image_mode=kwargs.get("image_mode", True),
                title=kwargs.get("title", ""), author=kwargs.get("author", "DeskPet"),
                output_path=kwargs.get("output_path"),
                outline_propose=outline_propose, notifier=notifier, run_blocking=run_blocking, session_id=sid)
        except asyncio.CancelledError:
            await notifier(sid, "🛑 已停止当前 PPT 任务。"); raise
        except Exception as e:
            log.exception("ppt_pro runner failed"); await notifier(sid, f"😿 PPT 没做成：{e}")
        finally:
            _PPT_PRO_RUNNING.pop(sid, None)
    t = asyncio.create_task(_runner())
    _PPT_PRO_RUNNING[sid] = t
    _PPT_PRO_TASKS.add(t); t.add_done_callback(_PPT_PRO_TASKS.discard)
    return {"ok": True, "status": "researching",
            "message": "收到~ 我先围绕这个主题做调研，拟好大纲会弹给你确认，确认后开始生成 PPT。"}
```
- **② 分阶段限时（v0.3 修正：不能用单一总超时——会把「用户慢慢看大纲」也算进去误杀）**。**不**对整个 `_ppt_pro_orchestrate` 套一个 `wait_for(total)`，而是**逐阶段**各自有界（确认等待**不计入**机器超时，由用户节奏决定）：
  - 调研：`asyncio.wait_for(deepresearch(...), research_timeout_s)`（WI-1 已有，默认 300s）。
  - 确认：`_clarify_ask(timeout=confirm_timeout_s)` 每轮（默认 300s），`max_revisions` 轮——这是**等用户**的合理上界，超时=用户没回，按取消优雅返回（非「机器卡死」）。
  - **渲染（关键泄漏点）**：`ppt_create` 经 `run_in_executor` 直跑、**绕过了 registry 的 1200s 超时** → 必须自己包 `asyncio.wait_for(run_blocking(...), render_timeout_s)`（新增 config `[ppt].pro_render_timeout_s`，默认 `max(600, pages*120)`，覆盖 N 张图最坏耗时）。超时 → notifier 告知 + 标 failed。
  - 这样每阶段都有界、task 不会永久挂；且**用户看大纲的时间不被机器超时杀**。
- **③ 取消入口（codex MAJOR）**：`/stop`（grep 现有 `/stop`/`subagent_cancel_all`/chat preempt 处）+ 可选 same-sid 新 `ppt_pro` 调用时，调 `_ppt_pro_cancel(sid)` → `_PPT_PRO_RUNNING[sid].cancel()`。`CancelledError` 能穿透正在 `await _ppt_outline_propose`（其内 `await asyncio.wait_for(fut, ...)`）的 task（cancel 会传播进内层 await），编排 `except CancelledError` 兜底通知后重抛。与现有 `/stop` 级联取消对齐（STATUS 06-21 subagent `/stop` 范本）。
- **注入（关键，BLOCKING-3）**：`outline_propose`(WI-3)/notifier/run_blocking/artifact_pusher/receipt_reporter 是 **main loop** 的资源。落地用 **`ppt_tools.set_ppt_pro_services(outline_propose=..., notifier=..., run_blocking=..., artifact_pusher=..., receipt_reporter=...)`**（新增模块级 setter + `_PPT_PRO_CTX` dict，仿 research_tools 的 `set_live_llm_call` 注入范式）；`session_id` 仍从 handler args 读（registry `:262/:691` 注入 `_session_id`）。
  - **注入时机**：必须在 `main.py` 的 `_ppt_outline_propose`/`_PPT_OUTLINE_WAITERS`（WI-3 文件 B）定义**之后**调 setter。
- `_ppt_pro_orchestrate`：见 §2.5 骨架（含 prev_slides 修订环、no-research 知情确认、取消/超时优雅返回、渲染走 run_blocking）。
- 入参 schema `_PPT_PRO_SCHEMA`：`topic:string`(必填) / `pages:integer`(默认8,范围3-20) / `depth:enum(light,standard,deep)`(默认 config) / `theme:enum(minimal,dark,playful)` / `image_mode:boolean`(默认True=惊艳) / `title/author/output_path`(同 ppt_create)。
- 注册 `_register_ppt_pro_tool()`（仿 `_register_ppt_tool` @**3834**）：`toolset="ppt"`, `permission_category="write_file"`, `timeout_seconds=60.0`（**秒回，不需 1800**）, `concurrency_safe=False`；**仅当 `_ppt_pro_cfg().enabled` 为 True 时注册**。
**验收**：handler 级单测（mock outline_propose/notifier/run_blocking/research/llm）——(a) 秒回 `status:researching` 且起了独立 task；(b) 接线缺失返回明确错误不崩；(c) 独立 task happy path（大纲卡 accept→渲染）；(d) 取消/超时/no-research 知情确认/reuse 历史路径；(e) chat task 被 cancel 不影响独立编排 task（模拟 parent cancel）；(f) same-sid 重复调返回 already_running。

---

### WI-8 · SKILL.md 更新（路由 + 修 stale）
**文件** `backend/deskpet/skills/builtin/ppt-generate/SKILL.md`
1. **修 stale（PPT.md §9.2 记录的已知 bug）**：第 27 行旧模板名 `商务深蓝-水墨/高级感-蓝/简约高级-灰` → 真实大类 `高级色/高级简约/通用商务`（与 `ppt_template_picker.py` 一致）。
2. **新增路由规则**：当用户「给个主题要做（正式/调研型/惊艳）PPT」→ **优先调 `ppt_pro(topic=...)`**（它会自动调研→拟纲→确认→生图/模板）。仅当用户**已给好完整 outline / 明确要求跳过调研和确认 / 只要朴素快出** → 直接用 `ppt_create`。
3. 说明 `ppt_pro` 会暂停等用户确认大纲，LLM 不要自己再问一遍。
4. **防重复调用（codex R2 MAJOR）**：`ppt_pro` 返回 `status:researching`/`already_running` 表示**已在后台进行**——LLM **不要再次调用** `ppt_pro`，也不要因为「没看到成品」而重试；安静等后台 notifier 推进度/成品即可（对齐现有 `ppt_create` 异步 `status:generating` 的处理）。
**验收**：人工读校 + 不破坏 skill 加载（skill loader 测试绿）。

---

### WI-10 · 后台 task 的 receipt / artifact 通道（architect MAJOR-4 + codex R2：通道未闭合）
> `ppt_pro` handler 秒回 `status:researching` 时，registry 只对这个**秒回值**发 receipt（`registry.py:780-860`），真正产物在独立 task 出 → registry 生命周期看不到。**必须显式注入产物上报通道**，否则 verify gate 永远只看到 `status:researching`。

**关键：先勘探现有「异步 ppt_create 怎么把成品 artifact 卡推回前端」**（`_bg_job` @**3779-3816** + `worker.notifier`）。`_bg_job` 现状只 notifier 文本（architect 指出它也没结构化 artifact）。所以本 WI 要**新建**一条产物上报通道，`ppt_pro` 与（顺带）`ppt_create` 异步路径共用：

**文件 A** `backend/main.py`：`set_ppt_pro_services(...)` 追加注入（R3 MINOR：明确通道实体，**没有独立 `artifact_push` 协议**，复用现有 `tool_result` envelope）：
- `artifact_pusher`：main-loop async `async def push(sid, artifacts: list[dict], text: str)` —— **构造一条合成 `{"type":"tool_result", "payload":{..., "artifacts":[...], "session_id":sid}}` 事件**，走现有 `tool_result` WS 广播路径（codex 实测 main.py:6490 广播 + 前端 `ArtifactCard.tsx:384`/`MessageBubble.tsx:480` 从 `tool_result.artifacts[]` 解析渲染「打开/在文件夹中显示」卡），**并像现有路径一样落 SessionDB**（持久化，reload 不丢卡）。artifacts 用 `kind=file`(成品 .pptx)+`kind=image`(预览 PNG)。
- `receipt_reporter`（可选）：复用 `emit_receipt(store, ...)`（codex 实测 `receipt_store.py:264`），`report(sid, tool="ppt_pro", outcome, path, shas)`，让 verify gate 对账后台真实产物。

**文件 B** `backend/deskpet/tools/ppt_tools.py`：`_ppt_pro_report_done(result, *, notifier, artifact_pusher, receipt_reporter, sid)`：
- 成功：`await artifact_pusher(sid, result["artifacts"])`（成品 .pptx + 预览图 PNG，复用 `ppt_create` 已构造的 artifacts @**3349** 附近）+ `receipt_reporter(sid, outcome="ok", path=result["path"], shas=...)` + notifier「✨…已自动打开」+ 自动打开（带图复用 PPT.md §2 逻辑）。
- 失败/取消：notifier 文本 + `receipt_reporter(outcome="failed"/"cancelled")`（不静默；verify gate 可见）。
**验收**：单测——(a) 成功 → artifact_pusher 收到含 path 的 artifacts + receipt outcome=ok；(b) 渲染失败 → outcome=failed + notifier；(c) 取消/超时 → outcome=cancelled；(d) 真机 TC-1 前端能看到成品「打开/在文件夹中显示」卡（非纯文本）。

---

### WI-9 · 测试与真机验收
- **单测**（backend）：WI-0~7/WI-10 各自 focus 测；新增 `test_ppt_pro.py` 覆盖编排全路径（research 降级/配置错上抛 / 确认 / 修改环(prev_slides) / 取消 / 超时 / probe 回退 / 首图 connectivity gate / 首图 model_unavailable gate / content 不回退 / 独立 task 不被 parent cancel）。复用 mock-embedder/mock-llm 风格。
- **BC 回归**：`-k ppt` 全套 + research 全套 + clarify 相关 + 前端 `vitest`（ClarificationDialog）+ `tsc 0 err`。
- **接线冒烟**：裸进程冒烟脚本 `scripts/acceptance/ppt_pro_smoke.py`（dry-ish：mock 生图 + mock confirm，验证编排 wiring 不死）。
- **windows-mcp 真机 E2E**（项目硬纪律，见 §8 手测计划，单独文档 `02-manual-test.md`）。

---

## 4. 文件改动清单（一页速查）

| 文件 | 改动 | WI |
|---|---|---|
| `backend/deskpet/tools/research_tools.py` | 无需改（`_DEPTH_PRESETS` 已是顶层常量 @2104，直接 import） | WI-1b |
| `backend/deskpet/tools/ppt_tools.py` | 新增 `_ppt_pro_cfg`/`_research_topic_for_ppt`/`_is_config_or_auth_error`/`_draft_outline_from_research`/`_build_outline_prompt`/`_outline_to_markdown`/`_fallback_minimal_outline`/`_degrade_to_template`/`_autofill_with_connectivity_gate`/`_should_fallback`/`_render_pro`/`_ppt_pro_orchestrate`/`_handle_ppt_pro`/`_ppt_pro_report_done`/`_PPT_PRO_SCHEMA`/`_PPT_PRO_CTX`/`_PPT_PRO_TASKS`/`set_ppt_pro_services`/`_register_ppt_pro_tool`；`ppt_create` 加 `skip_image_gen` 形参；**修现有 `_ppt_async_enabled` 等的 `_cfg.config.raw` 坏读法** | WI-0/1/2/6/7/10 |
| `backend/deskpet/tools/image_tools.py` | 新增 `probe_image_reachable`；失败返回加 `error_kind`(connectivity/model_unavailable/auth/quota/content/unknown)；`_resolve_relay_base_and_key` helper | WI-5/6b |
| `backend/config.py`（**不是** deskpet/config.py） | 加 `[ppt]` pro_* 项（含 `pro_default_depth=deep`/`pro_save_research=True`/`pro_outline_history=True`），走 `standalone_config_section("ppt")` | WI-0 |
| `backend/deskpet/tools/ppt_outline_store.py`（**新**，v1.1） | `PPTOutlineWaiters` + 大纲历史持久化（`ppt_outline_history` 表，flag-gated）+ save/list/get/mark | WI-3 |
| `backend/main.py` | 新增 `_PPT_OUTLINE_WAITERS`+`_ppt_outline_propose`（FP-5 风格大纲卡 propose+await）+ WS 回灌 `ppt_outline_decision`；定义后调 `ppt_tools.set_ppt_pro_services(outline_propose/notifier/run_blocking/artifact_pusher/receipt_reporter)`；`pro_enabled` 时注册 `ppt_pro` | WI-3/7/10 |
| `tauri-app/src/code-panel/ws.ts` + `stores/sessionsStore.ts` | `ppt_outline_proposed`→push_message；`ppt_outline` role+字段+`resolve_ppt_outline` | WI-4 |
| `tauri-app/src/code-panel/PPTOutlineCard.tsx`（**新**，v1.1） | FP-5 风格大纲卡：渲染大纲+来源数+无费用；按钮 确认/修改(textarea)/取消/历史复用 → `ppt_outline_decision`；双面板挂载 | WI-4 |
| `backend/deskpet/skills/builtin/ppt-generate/SKILL.md` | 路由到 `ppt_pro` + 修 stale 模板名 | WI-8 |
| `backend/deskpet/tools/ppt_tools.py`（`_ppt_pro_report_done`） | 后台 task 成功/失败补发 receipt + artifact | WI-10 |
| `backend/tests/test_ppt_pro.py`（新）+ 既有 ppt/research/clarify 测 | 单测 + BC | WI-9 |
| `tauri-app/src/code-panel/__tests__/PPTOutlineCard.test.tsx`（新） | vitest（卡渲染/4 动作/历史复用/持久化） | WI-4/9 |
| `scripts/acceptance/ppt_pro_smoke.py`（新） | 接线冒烟 | WI-9 |
| `plans/2026-06-21-ppt-deepresearch-pro/02-manual-test.md`（新） | windows-mcp 真机用例 | WI-9 |

---

## 5. 执行顺序（依赖图）

```
WI-0 (config: backend/config.py + 修坏读法) ─┬─ WI-1 (research 封装, import _DEPTH_PRESETS)
                                            ├─ WI-2 (拟纲/双模式/修订)
                                            ├─ WI-5 (probe) ─ WI-6b (error_kind 细分) ─ WI-6 (回退编排+_degrade 清 prompt)
                                            └─ WI-3 后端 clarify 扩展 ─┐
WI-4 (前端 clarify 渲染 + 让我改改编辑态) ──────────────────────────────┤
                                                                     └─ WI-7 (ppt_pro async 秒回+独立 task 总装) ─ WI-10 (receipt) ─ WI-8 (SKILL) ─ WI-9 (测试/真机)
```
- 可并行：{WI-1}、{WI-2}、{WI-5+WI-6b+WI-6}、{WI-3 后端}、{WI-4 前端} 五条独立线（codex 多 worktree 并行）。
- 汇合点：WI-7 总装（含 §2.5 独立编排 task）→ WI-10 上报 → WI-8 路由。

---

## 6. 风险与对策（v0.2：R1 对抗挑战后更新，✅=已在 plan 内消解）

| ID | 风险 | 状态/对策 |
|---|---|---|
| **R-1** | `_render_pro` 已生图 / 模板回退路径，`ppt_create` @3239 二次生图（重复烧钱） | ✅ **双保险**：`_degrade_to_template` 清空 image_prompt + `ppt_create` 加 `skip_image_gen`（默认 False=BC），两路径都传 True（WI-6） |
| **R-2** | `_clarify_ask` 走 `_control_connections[session_id]`，ppt 拿到的 sid 是否命中 | ✅ 低风险：codex 实测后端默认缺省 query→`"default"`（main.py:3986/3996），`_clarify_ask` 内 `or get("default")` 兜底；handler 从 args 读 `_session_id`（WI-7）。真机核验命中 |
| **R-3** | ~~`_clarify_ask` 两份拷贝~~ | ✅ **伪命题已删**：codex 实测 main.py:530 是唯一活动实现，clarify_tool 未被调用；只改一处 |
| **R-4** | 确认/调研内联 await 在 chat task → same-sid 新消息 preempt cancel 杀确认链路（=FP-5 旧 bug） | ✅ **BLOCKING 已修**：handler 秒回 + 独立 `asyncio.create_task` 编排 task（§2.3/§2.5），照搬 FP-5 范本，preempt 伤不到 |
| **R-5** | 调研 report_md 注入拟纲 prompt 爆窗 | ✅ `_PPT_PRO_RESEARCH_CTX_CHARS=6000` 截断 + 拟纲用 `_resolve_default_llm_call`（WI-2 锁定） |
| **R-6** | probe `/v1/models` 200 但 gpt-image-2 模型不可用（假阳） | ✅ 双层：probe + 首图实测 gate，且 4xx model_unavailable 也回退（§2.2/WI-6b） |
| **R-7** | `parse_outline` 对 LLM 乱输出鲁棒性 | ✅ `_fallback_minimal_outline` 兜底 + 复用 `normalize` |
| **R-8** | flag 默认 True 改变出厂行为 | ✅ 新工具 + SKILL.md 路由控制 + `image_mode`/快出 + `pro_enabled` 可关 |
| **R-9** | 模板回退内容密度塌方（image_full 稀疏文本填模板设计页留白） | ✅ WI-2 双模式产出（image_prompt + 充实 bullets），回退用 bullets 不做有损转换（architect MAJOR-2） |
| **R-10** | 修订环丢上一版大纲，整盘重拟推翻已认可页 | ✅ WI-2 传 `prev_slides`，prompt 改「增量改、保留其余」（architect MAJOR-3） |
| **R-11** | F1 调研静默失败 → 偷偷出「正式 PPT」凭空编 | ✅ 区分配置错(上抛)/网络错(降级)，且 no-research 时确认卡显式告知让用户知情确认（WI-1，codex/architect MAJOR） |
| **R-12** | 后台 task 产物/失败 verify gate 看不到 | ✅ WI-10 补 receipt+artifact（architect MAJOR-4） |
| **R-13** | 配置走 `_cfg.config.raw` 坏读法静默失效（STATUS 多次踩） | ✅ WI-0 走 `standalone_config_section`，顺手修现有坏读法（codex BLOCKING） |
| **R-14** | 4xx「不靠中文文案」承诺无法兑现（relay 可能只给自然语言 message 无 error.code） | ✅ WI-6b 分层判定（status_code→error.code→多语言文案兜底，**诚实声明文案层局限**）+ 「全图失败 n_ok==0 也回退」二次兜底 + 真实 relay 样例测试（codex R2 BLOCKING） |
| **R-15** | 秒回后 AgentLoop 回灌 tool_result 继续迭代 → 重复调 `ppt_pro` 起第二个后台 task（重复调研/烧图） | ✅ WI-7 `_PPT_PRO_RUNNING[sid]` 去重，重复调返回 `already_running` + WI-8 SKILL 告知 LLM 见 status 即等待（codex R2 MAJOR） |
| **R-16** | 后台编排无超时/取消/清理 → task 泄漏挂死 | ✅ WI-7 **分阶段限时**（research 300s / confirm 等用户 300s/轮 / **render `wait_for(max(600,pages*120))` 补 registry 绕过的洞**）——确认等待不计入机器超时（避免误杀用户看大纲）+ `/stop`/same-sid 取消 `_ppt_pro_cancel` + `finally` 清 `_PPT_PRO_RUNNING`（codex R2 MAJOR + R3 预防总超时误杀确认） |
| **R-17** | WI-10 receipt/artifact 通道未闭合（后台 task 不在 registry 生命周期内） | ✅ WI-10 显式注入 `artifact_pusher`+`receipt_reporter`，后台 task 主动推成品卡 + 对账（codex R2 MAJOR） |

---

## 7. 待决问题（v1.1 — 用户已全部拍板 ✅）

| # | 决策 | 落地 |
|---|---|---|
| 1 | 调研「5 分钟」 | `pro_default_depth=deep`（6 子问题/2 轮反思，~5min），`pro_research_timeout_s=360`（WI-0/WI-1） |
| 2 | **FP-5 风格独立大纲卡 + 存历史大纲** | 重写 WI-3/WI-4：`ppt_outline_proposed` 卡 + Future-await + `ppt_outline_history` 持久化 + 历史复用 |
| 3 | 调研默认落盘 | `pro_save_research=True` → 落 DeepResearch/（WI-1） |
| 4 | 不注明生图费用 | 大纲卡不显示费用（WI-4） |

---

## 8. 手测计划（windows-mcp，详见 `02-manual-test.md`）
- TC-1 惊艳全链路：发主题 → 真调 deepresearch（log 见 `deepresearch` 调用 + 来源数）→ 确认卡弹出含大纲 → 真点「确认生成」→ gpt-image-2 真出图 → 落盘 + 自动打开。
- TC-2 修改环：确认卡输「把第3页换成竞品对比」→ 大纲重拟 → 再确认 → 生成。
- TC-3 取消：点「取消」→ 不生成。
- TC-4 ★回退：断网/关 relay 出图（或 mock probe False）→ 探测不可达 → 走模板 → 仍出完整 deck（无占位残页）+ 通知「已用模板」。
- TC-5 调研降级：research 超时 → 仍拟纲（注明无来源）→ 确认 → 生成。
- TC-6 BC：旧 `ppt_create` 直接出图路径不受影响。

---

## 9. 实现纪律
- **行号以实现时 grep 为准**（本 plan 行号为 2026-06-21 测绘快照，代码会变）。
- 新文件**即建即 `git add`+commit**（防沙箱回滚，见全局 memory）。
- 中文文件用 Edit/Write（勿用 PowerShell Get-Content/Set-Content 链）。
- codex gpt-5.5 子代理实现（独立文件并行/同文件串行），Lead 集成 + 真机验收。
- 每 WI 完成跑对应单测；总装后跑 `-k ppt` + research + clarify + vitest + tsc 全绿才进真机。
- 完成后更新 `STATUS/PPT.md` + `STATUS/status.md`（项目硬纪律）。
