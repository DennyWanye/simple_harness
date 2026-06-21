# PPT 能力优化 — DeepResearch 调研 → 大纲确认 → 惊艳生图(gpt-image-2) / 模板兜底

> **状态**: v0.1 DRAFT（待 codex/子代理多轮对抗挑战收敛到 EXECUTABLE-AS-IS）
> **建档**: 2026-06-21
> **作者**: Claude (Lead)
> **前置阅读**: [STATUS/PPT.md](../../STATUS/PPT.md) · [STATUS/DeepResearch.md](../../STATUS/DeepResearch.md) · [STATUS/AgentLoop.md](../../STATUS/AgentLoop.md)

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
- **结论（来自测绘）**：`ask_clarification` 形态最省力——后端已能在 tool handler 内 `await _clarify_ask(...)` 挂起 agent，前端已有弹窗。**只需扩展 payload 携带多行大纲 Markdown（`content_md`）+ 前端富文本渲染**即可承载 F3「展示大纲让用户确认/改」。

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

### 2.2 F4 回退判定（双层，确定性）
1. **预探测**（便宜、免费）：`image_tools.probe_image_reachable(timeout=...)` —— `trust_env=False` 直连 relay base `/v1/models`（已知 200，见 STATUS 06-09），connect 5s/read 8s。返回 `False` 即 relay 整体不可达 → 直接走模板，**不烧任何生图钱**。
2. **首图实测**（精确，捕捉「relay 在但 gpt-image-2 模型挂」）：预探测过了之后，先只生**第 1 张**图；若返回**连接类错误**（§1.2 的 transient 分类 + 读超时/504，即「连不上」语义）→ 判定不可达 → 放弃剩余生图、整副 `_degrade_to_template` 回退。若首图成功 → 继续生其余图（惊艳路径）。
   - 「连不上」= 网络/服务层失败（connect/read-timeout/5xx/SSL/protocol）。「内容失败」（4xx 拒绝/safety）**不触发整副回退**（那不是「连不上」，按现状占位降级，避免误伤）。

### 2.3 同步/异步边界
- Stage A/B/C（调研+拟纲+确认）**同步**在 handler 内 `await`（agent turn 挂起，期间推进度消息）。最坏 ≈ research(≤300s) + 确认等待(≤300s)。`ppt_pro` 注册 `timeout_seconds=1800.0`（30min）兜住。
- Stage D 渲染（生图 1-3min/张）**复用现有 `_bg_job` 异步路径**：确认后 `submit_background` 跑渲染，handler 立即返回 `{"ok":True,"status":"generating"}`，notifier 推回成品。
- 若 worker 不可用 → 同步渲染降级（同 `_handle_ppt_create` 现有降级）。

### 2.4 Flag / BC
- 新增 `[ppt].pro_enabled`（默认 **True**——这是用户主诉求，要开箱可用；但保留开关可关）。
- `ppt_pro` 是**新增工具**，不改 `ppt_create` 字节行为 → 对现有调用零 BC 风险。
- `_clarify_ask` 加 `timeout` 形参（默认 120 保 BC），`clarification_request` payload 加**可选** `content_md`（旧前端忽略未知字段，BC）。

---

## 3. 工作项（WI）— 逐文件逐函数

> 每个 WI 标注：文件、函数、改动类型（新增/改）、伪代码/diff、验收。

### WI-0 · 配置与开关
**文件** `backend/deskpet/config.py`（PPT 配置段，grep `class .*PPT|\[ppt\]|ppt_` 定位）
- 加字段：`pro_enabled: bool = True`、`pro_default_depth: str = "standard"`（light/standard/deep）、`pro_max_revisions: int = 2`、`pro_research_timeout_s: float = 300.0`、`pro_confirm_timeout_s: float = 300.0`、`pro_image_probe_timeout_s: float = 8.0`。
- 解析对齐现有 `[ppt]` 段读法（参考 `visual_review`/`async_enabled`/`preview_render` 的解析）。
**验收**：`test_config.py` 加 1 例验证默认值 + toml 覆盖；flag-off 时 `ppt_pro` 不注册。

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
    except (asyncio.TimeoutError, Exception) as e:
        log.warning("ppt_pro research failed/timeout: %s → degrade to no-research outline", e)
        return None
```
**设计要点**：
- **复用 `_DEPTH_PRESETS`**（不要硬编码档位数字，保持与 deepresearch 单一真相源一致）。从 research_tools import 该常量；若它是 `_handle_deepresearch` 内的局部 map，则在 research_tools 顶层提一个 `DEPTH_PRESETS` 公共常量（小重构，见 §3 WI-1b）。
- **降级而非失败**：调研失败/超时 → 返回 None，Stage B 用「无调研」prompt 直接拟纲（功能不缺，只是质量降级），并在确认卡注明「⚠️ 本次未取得调研来源」。
- **不落盘**：PPT 内部调研默认**不**调 `_save_report`（避免污染 DeepResearch/ 索引）；可加 config `[ppt].pro_save_research`（默认 False）允许落盘。

**WI-1b（小重构）** `research_tools.py`：把 `_DEPTH_PRESETS` 提为顶层公共常量 `DEPTH_PRESETS`（`_handle_deepresearch` 改引用它），供 ppt_tools 复用。**验收**：research 全套 BC 不破。

**验收**：mock `deepresearch` 的单测——(a) 正常返回 report；(b) 超时返回 None；(c) 无 llm_call 返回 None。

---

### WI-2 · 调研 → 大纲拟制 + 修订（F2）
**文件** `backend/deskpet/tools/ppt_tools.py`（新增）
```python
async def _draft_outline_from_research(
    topic: str, report: "ResearchReport | None", *, pages: int, theme: str,
    image_mode: bool, llm_call, feedback: str = "",
) -> list[SlideOutline]:
    """基于调研报告（可空）拟 PPT 大纲。image_mode=True 则每页带 image_full+image_prompt（惊艳模式）。
    feedback 非空时 = 用户修改意见，按其重拟。"""
    research_md = (report.report_md if report else "")[: _PPT_PRO_RESEARCH_CTX_CHARS]  # 截断防爆窗，默认 6000
    prompt = _build_outline_prompt(topic, research_md, pages=pages, image_mode=image_mode, feedback=feedback)
    raw = await llm_call(prompt)
    slides = parse_outline(raw)              # 复用现有宽容解析
    if not slides:                           # 解析失败兜底：最小可用大纲
        slides = _fallback_minimal_outline(topic, pages, image_mode)
    return slides
```
- 新增 `_build_outline_prompt(...)`：系统化提示词，要求输出 §1.1 `SlideOutline` 兼容的 JSON 数组。**惊艳模式**：封面 `layout="image_full"` + `image_prompt`（电影感、负空间、禁字后缀，复用 `_assign_image_layouts` 的 prompt 习惯），内容页 `image_full`+精炼 bullets。**模板模式**：`layout` 用 bullet/two_column/section 等，不带 image_prompt。提示词强约束「内容必须基于以下调研报告，引用其中的数据/事实，不得编造」+ 附 `research_md`。
- 新增 `_outline_to_markdown(slides) -> str`：把 slides 渲染成给用户看的多行大纲 MD（页码 + 标题 + bullets 缩进），用于确认卡 `content_md`。
- 新增 `_fallback_minimal_outline(topic, pages, image_mode)`：纯本地兜底（封面+pages-1 内容页占位），保证「LLM 全挂也能出大纲」。
- 常量 `_PPT_PRO_RESEARCH_CTX_CHARS = 6000`（research 注入上限，防爆 LLM 窗口）。

**验收**：单测——(a) 有 report 拟纲含 image_prompt（image_mode）；(b) report=None 仍出大纲；(c) feedback 注入重拟（mock llm 校验 prompt 含 feedback）；(d) llm 返回垃圾 → `_fallback_minimal_outline` 生效；(e) `_outline_to_markdown` 多页正确成文。

---

### WI-3 · 确认卡：后端挂起 + payload 扩展（F3 后端）
**文件 A** `backend/main.py`：把内联 `_clarify_ask` @**530-552** 加 `timeout` + `content_md` 形参（**保 BC**：默认 `timeout=120, content_md=None`）：
```python
async def _clarify_ask(question, options, session_id, *, timeout: float = 120.0, content_md: str | None = None):
    ...
    payload = {"request_id": request_id, "question": question, "options": list(options or [])}
    if content_md:
        payload["content_md"] = content_md     # 新增：富文本大纲
    await ws.send_json({"type": "clarification_request", "payload": payload})
    return await asyncio.wait_for(fut, timeout)   # 用形参 timeout
```
> 同步更新 `code_tools/clarify_tool.py::build_clarification_ask` 的等价闭包签名（保持两处一致，避免再次漂移；二者本是同一逻辑两份拷贝——可顺手抽到 clarify_tool 单一实现，main.py import 之，见 §6 风险 R-3）。

**文件 B** `backend/deskpet/tools/ppt_tools.py`：让 `ppt_pro` handler 能拿到 `_clarify_ask` + `session_id`。
- 现状：tool 经 `registry.set_session_context` 注入 `worker`（含 `notifier`/`submit_background`）。新增注入 `clarify`（= `_clarify_ask`）与 `session_id`。
- **main.py** lifespan 注册 ppt 工具上下文处（grep `set_session_context`）追加 `clarify=_clarify_ask`。
- handler 内：
```python
async def _ppt_pro_confirm(slides, *, clarify, session_id, timeout_s, note: str = "") -> str:
    md = _outline_to_markdown(slides)
    q = "我按主题调研后拟了下面的 PPT 大纲，确认就开始生成；想改的话直接告诉我改哪里 👇"
    if note: q = note + "\n" + q
    return (await clarify(q, ["确认生成", "让我改改", "取消"], session_id,
                          timeout=timeout_s, content_md=md)) or ""
```
- 主流程（确认环 ≤ `pro_max_revisions`）：
```python
slides = await _draft_outline_from_research(topic, report, pages=pages, theme=theme, image_mode=image_mode, llm_call=llm)
for _round in range(cfg_max_revisions + 1):
    ans = await _ppt_pro_confirm(slides, clarify=clarify, session_id=sid, timeout_s=confirm_to,
                                 note=("" if report else "⚠️ 本次未取得调研来源，大纲基于通用知识。"))
    norm = ans.strip()
    if norm in ("确认生成", "确认", "ok", "OK", "") :       # 空=超时→按取消处理（见下）
        confirmed = (norm != ""); break
    if norm in ("取消", "cancel"):
        return {"ok": True, "status": "cancelled", "message": "已取消，没有生成 PPT。"}
    # 其余 = 修改意见 → 重拟
    slides = await _draft_outline_from_research(topic, report, pages=pages, theme=theme,
                                                image_mode=image_mode, llm_call=llm, feedback=norm)
else:
    confirmed = False  # 改太多轮，停
if not confirmed:
    return {"ok": True, "status": "cancelled", "message": "大纲未确认（超时或修改次数过多），已暂停。需要的话再叫我~"}
```
- **超时语义**：`asyncio.wait_for` 超时 → `_clarify_ask` 返回 `""` → 按「未确认/取消」优雅返回（不报错、不强行生成）。

**验收**：单测（mock clarify）——(a) 首轮「确认生成」→ confirmed=True；(b) 「让我改改…」→ 触发 redraft 再确认；(c) 超过 max_revisions → cancelled；(d) 空答（超时）→ cancelled；(e) `content_md` 真带进 payload。

---

### WI-4 · 确认卡：前端富文本渲染（F3 前端）
**文件 A** `tauri-app/src/types/skillPlatform.ts` @**57-73**：`ClarificationRequest["payload"]` 加可选 `content_md?: string`。
**文件 B** `tauri-app/src/components/ClarificationDialog.tsx`：问题文本下方，若 `current.content_md` 存在，渲染一块滚动区展示大纲（markdown 或等宽 `<pre>`；项目已有 markdown 渲染器则复用，否则 `<pre style={{whiteSpace:"pre-wrap",maxHeight:"40vh",overflow:"auto"}}>`）。选项按钮「确认生成 / 让我改改 / 取消」+ 自由文本输入框（已有）：点「让我改改」聚焦输入框提示用户写修改意见；输入框内容作为 `answer` 回传（即修改意见）。
**文件 C** `useClarificationRequests.ts`：无需改逻辑（payload 透传），仅确保 `content_md` 随 payload 进入 `current`。
**验收**：`vitest` 组件测——(a) 有 content_md 渲染大纲块；(b) 无 content_md 不渲染（BC）；(c) 点选项/输入文本各自 `onResolve` 正确值。

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
        if _is_connectivity_error(res0.get("error")):   # 连不上 → 不可达
            return (False, 0)
        # 内容类失败：占位降级，继续后续（非「连不上」）
    else:
        slides[i0].image_path = res0["path"]
    # 生其余
    rest = generate_images([s.image_prompt for _, s in prompts[1:]], ...)
    n_ok = sum(1 for r in rest if r.get("path")) + (1 if slides[i0].image_path else 0)
    for (idx, s), r in zip(prompts[1:], rest):
        if r.get("path"): slides[idx].image_path = r["path"]
    return (True, n_ok)
```
- 新增 `_is_connectivity_error(err: str|None) -> bool`：按 error 文本/标记匹配连接类（connect/timeout/读超时/5xx/SSL/protocol/relay 不可达）。**建议**：在 `image_tools` 的失败分类里给 transient 类 error 文本加统一前缀（如 `[transient]`）或返回结构里加 `kind:"connectivity"|"content"`，让判定不靠脆弱的中文文本匹配（见 WI-6b）。
- **WI-6b（强化判定，推荐）** `image_tools.py`：`generate_images`/`_generate_png` 的失败返回从 `error:str` 升级为同时带 `error_kind: "connectivity"|"content"|"unknown"`（不破坏 `error` 字段，加键即可，BC）。`_is_connectivity_error` 直接读 `error_kind`。
- 新增渲染编排 `_render_pro(slides, *, theme, title, author, output_path, image_mode, probe_timeout_s, notifier, sid) -> dict`：
```python
def _render_pro(...):
    use_template = not image_mode
    if image_mode and not probe_image_reachable(timeout_s=probe_timeout_s):
        use_template = True; _notify(notifier, sid, "⚠️ AI 配图暂时连不上，已切换精美模板生成。")
    if image_mode and not use_template:
        reachable, n_ok = _autofill_with_connectivity_gate(slides)
        if not reachable:
            use_template = True; _notify(notifier, sid, "⚠️ AI 配图暂时连不上，已切换精美模板生成。")
    if use_template:
        slides = _degrade_to_template(slides)
        return ppt_create(slides, theme=theme, title=title, author=author,
                          output_path=output_path, template=_default_category_template())
    # 惊艳路径：图已生好(image_path 填好)，直接渲染 image_full + 视觉闭环
    return ppt_create(slides, theme=theme, title=title, author=author, output_path=output_path)
```
> **关键**：`ppt_create` 现状会在内部再调一次 `_autofill_image_prompts`（@3239-3248）。`_render_pro` 已先生好图（`image_path` 已填），需避免二次生图。两种实现选一（§6 R-1）：(i) 给 `ppt_create` 加 `skip_image_gen: bool=False` 形参，`_render_pro` 传 True；(ii) `_render_pro` 走更底层的 `_render_fromscratch`/`_render_with_*` 直接渲染绕过 `ppt_create` 顶层。**推荐 (i)**（改动小、复用顶层视觉闭环）。
**验收**：单测——(a) image_mode + probe False → 走模板路径（`_degrade_to_template` 调用 + ppt_create 带 template）；(b) probe True 但首图 connectivity error → 回退模板；(c) 首图 content error → 不回退（占位继续）；(d) 全程成功 → 惊艳路径不二次生图；(e) `_degrade_to_template` 内容不丢。

---

### WI-7 · `ppt_pro` 工具 handler + schema + 注册（编排总装）
**文件** `backend/deskpet/tools/ppt_tools.py`
- 新增 `async def _handle_ppt_pro(**kwargs)`：编排 Stage A→D（调 WI-1/2/3/6），同步做 A/B/C，确认后渲染走异步 `_bg_job`（复用 `worker.submit_background` + `notifier`），返回 `{"ok":True,"status":"generating"}`；worker 不可用则同步 `_render_pro`。
- 入参 schema `_PPT_PRO_SCHEMA`：
  - `topic: string`（必填）
  - `pages: integer`（默认 8，范围 3-20）
  - `depth: enum(light,standard,deep)`（默认取 config `pro_default_depth`）
  - `theme: enum(minimal,dark,playful)`（默认 minimal）
  - `image_mode: boolean`（默认 True = 惊艳优先；False = 直接模板）
  - `title/author/output_path`（同 ppt_create）
- 注册 `_register_ppt_pro_tool()`（仿 `_register_ppt_tool` @3834）：`toolset="ppt"`, `permission_category="write_file"`, `timeout_seconds=1800.0`, `concurrency_safe=False`；**仅当 `config.ppt.pro_enabled` 为 True 时注册**。
- **进度可观测**：Stage A 前 `_notify("🔍 正在围绕主题做深度调研…")`；A 后 `_notify(f"📚 调研完成（{n_sources} 个来源），正在拟大纲…")`；用 `worker.notifier`（异步）或 control WS。
**验收**：handler 级单测（mock research/llm/clarify/render）跑通 happy path（确认→生成）+ 取消路径 + 调研降级路径。

---

### WI-8 · SKILL.md 更新（路由 + 修 stale）
**文件** `backend/deskpet/skills/builtin/ppt-generate/SKILL.md`
1. **修 stale（PPT.md §9.2 记录的已知 bug）**：第 27 行旧模板名 `商务深蓝-水墨/高级感-蓝/简约高级-灰` → 真实大类 `高级色/高级简约/通用商务`（与 `ppt_template_picker.py` 一致）。
2. **新增路由规则**：当用户「给个主题要做（正式/调研型/惊艳）PPT」→ **优先调 `ppt_pro(topic=...)`**（它会自动调研→拟纲→确认→生图/模板）。仅当用户**已给好完整 outline / 明确要求跳过调研和确认 / 只要朴素快出** → 直接用 `ppt_create`。
3. 说明 `ppt_pro` 会暂停等用户确认大纲，LLM 不要自己再问一遍。
**验收**：人工读校 + 不破坏 skill 加载（skill loader 测试绿）。

---

### WI-9 · 测试与真机验收
- **单测**（backend）：WI-0~7 各自 focus 测；新增 `test_ppt_pro.py` 覆盖编排全路径（research 降级 / 确认 / 修改环 / 取消 / 超时 / probe 回退 / 首图 gate）。复用 mock-embedder/mock-llm 风格。
- **BC 回归**：`-k ppt` 全套 + research 全套 + clarify 相关 + 前端 `vitest`（ClarificationDialog）+ `tsc 0 err`。
- **接线冒烟**：裸进程冒烟脚本 `scripts/acceptance/ppt_pro_smoke.py`（dry-ish：mock 生图 + mock confirm，验证编排 wiring 不死）。
- **windows-mcp 真机 E2E**（项目硬纪律，见 §8 手测计划，单独文档 `02-manual-test.md`）。

---

## 4. 文件改动清单（一页速查）

| 文件 | 改动 | WI |
|---|---|---|
| `backend/deskpet/config.py` | 加 `[ppt]` 6 个 pro_* 字段 | WI-0 |
| `backend/deskpet/tools/research_tools.py` | `_DEPTH_PRESETS`→顶层 `DEPTH_PRESETS` 公共常量 | WI-1b |
| `backend/deskpet/tools/ppt_tools.py` | 新增 `_research_topic_for_ppt`/`_draft_outline_from_research`/`_build_outline_prompt`/`_outline_to_markdown`/`_fallback_minimal_outline`/`_ppt_pro_confirm`/`_degrade_to_template`/`_autofill_with_connectivity_gate`/`_is_connectivity_error`/`_render_pro`/`_handle_ppt_pro`/`_PPT_PRO_SCHEMA`/`_register_ppt_pro_tool`；`ppt_create` 加 `skip_image_gen` 形参 | WI-1/2/3/6/7 |
| `backend/deskpet/tools/image_tools.py` | 新增 `probe_image_reachable`；失败返回加 `error_kind`；`_resolve_relay_base_and_key` helper | WI-5/6b |
| `backend/main.py` | `_clarify_ask` 加 `timeout`+`content_md`；ppt 工具 `set_session_context` 注入 `clarify`+`session_id`；`pro_enabled` 时注册 `ppt_pro` | WI-3/7 |
| `backend/deskpet/tools/code_tools/clarify_tool.py` | `build_clarification_ask` 闭包同步加 `timeout`+`content_md`（与 main.py 一致） | WI-3 |
| `tauri-app/src/types/skillPlatform.ts` | `ClarificationRequest.payload.content_md?: string` | WI-4 |
| `tauri-app/src/components/ClarificationDialog.tsx` | 渲染 `content_md` 大纲块 | WI-4 |
| `backend/deskpet/skills/builtin/ppt-generate/SKILL.md` | 路由到 `ppt_pro` + 修 stale 模板名 | WI-8 |
| `backend/tests/test_ppt_pro.py`（新）+ 既有 ppt/research/clarify 测 | 单测 + BC | WI-9 |
| `tauri-app/src/components/__tests__/ClarificationDialog.test.tsx` | vitest | WI-4/9 |
| `scripts/acceptance/ppt_pro_smoke.py`（新） | 接线冒烟 | WI-9 |
| `plans/2026-06-21-ppt-deepresearch-pro/02-manual-test.md`（新） | windows-mcp 真机用例 | WI-9 |

---

## 5. 执行顺序（依赖图）

```
WI-0 (config) ─┬─ WI-1b (DEPTH_PRESETS 重构) ─ WI-1 (research 封装)
               ├─ WI-2 (拟纲/修订)
               ├─ WI-5 (probe) ─ WI-6b (error_kind) ─ WI-6 (回退编排)
               └─ WI-3 后端 clarify 扩展 ─┐
WI-4 (前端 clarify 渲染) ──────────────────┤
                                          └─ WI-7 (ppt_pro 总装) ─ WI-8 (SKILL) ─ WI-9 (测试/真机)
```
- 可并行：{WI-1+WI-1b}、{WI-2}、{WI-5+WI-6b+WI-6}、{WI-3 后端}、{WI-4 前端} 五条独立线（codex 多 worktree 并行）。
- 汇合点：WI-7 总装。

---

## 6. 风险与对策（待对抗挑战补充）

| ID | 风险 | 对策 |
|---|---|---|
| **R-1** | `_render_pro` 已生图，`ppt_create` 内部 @3239 二次生图（重复烧钱/覆盖） | 给 `ppt_create` 加 `skip_image_gen=False` 形参，`_render_pro` 惊艳路径传 True（WI-6 已列） |
| **R-2** | `_clarify_ask` 走 `_control_connections[session_id]`；ppt 工具拿到的 `session_id` 是否就是 control WS 注册的那个 key？pet 窗口是 `"default"` | WI-7 注入 `session_id` 必须用与 control WS 注册一致的 key；测绘见 App.tsx 用 `session_id=default`。实现时真机核验 `_control_connections` 命中 |
| **R-3** | `_clarify_ask` 在 main.py 与 clarify_tool.py 两份拷贝，改一处漏一处再漂移 | WI-3 顺手收敛为单一实现（clarify_tool 导出，main.py import），二者签名强制一致 |
| **R-4** | 同步 handler 内 await 调研+确认最坏 ~10min，agent turn 长时间挂起，期间用户发新消息会 preempt/cancel 当前 turn → 确认链路被杀 | 参照 FP-5「确认卡」教训（独立 asyncio.Task 防 preempt）。评估：是否把确认环也拆独立 task？或接受「确认期间不收新消息」。**留待挑战定夺**（见 §7 待决） |
| **R-5** | 调研 report_md 注入拟纲 prompt 爆 LLM 窗口（gpt-5.5 8000） | `_PPT_PRO_RESEARCH_CTX_CHARS=6000` 截断；拟纲用 `_resolve_default_llm_call`（research LLM，max_tokens 较大）而非 8000 窗口的聊天模型 |
| **R-6** | probe `/v1/models` 200 但 gpt-image-2 模型本身不可用（probe 假阳） | 双层判定：probe 过了仍有「首图实测 gate」兜底（WI-6） |
| **R-7** | `parse_outline` 对 LLM 乱输出的鲁棒性 | `_fallback_minimal_outline` 兜底；复用现有 `normalize` |
| **R-8** | flag 默认 True 改变出厂行为（用户原本「做 PPT」可能不期望被暂停确认） | `ppt_pro` 是新工具，SKILL.md 路由控制何时用；保留 `image_mode`/快出路径；`pro_enabled` 可关回旧行为 |

---

## 7. 待决问题（需挑战阶段 + 用户拍板）

1. **R-4 确认期 preempt**：确认环是否拆独立 task（防新消息 cancel）？还是接受确认期间阻塞？（影响 UX 与实现复杂度）
2. **`ppt_pro` vs 扩展 `ppt_create`**：是否真要新工具，还是给 `ppt_create` 加 `research=True/confirm=True` 开关？（本 plan 选新工具，理由 §2.1）
3. **确认卡用 `ask_clarification` 复用 vs 专用「大纲卡」**（FP-5 风格独立卡片）？本 plan 选前者（省力）；若要「保存为模板/历史大纲」等富交互，后者更好。
4. **调研档位默认**：PPT 默认 standard 是否太慢（standard ≈ 5 子问题，可能 2-3min）？是否默认 light？
5. **调研是否落盘**到 DeepResearch/（默认否）。

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
