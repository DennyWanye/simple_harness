# 00-PLAN — DeepResearch 子代理 Fan-out（每子问题一个子代理深查 → 主线程统一分析）

> **版本**: v0.1 DRAFT（待用户 review）
> **日期**: 2026-06-21
> **状态**: 📋 规划中（先写 plan，review 后再执行）
> **本目录**: `plans/2026-06-21-deepresearch-subagent-fanout/`
> **关联**:
> - 子代理基建：[`plans/2026-06-21-subagent-concurrency-driver/`](../2026-06-21-subagent-concurrency-driver/00-PRD.md)（scheduler/task_kinds/registry 已落地）
> - deepresearch 现状：[`research_tools.py`](../../backend/deskpet/tools/research_tools.py)（§6.0 改造后全管线）

---

## 0. 一句话

deepresearch 的 **Plan 拆题之后**，不再把所有子问题丢进一个扁平池统一搜，而是**每个子问题派一个子代理独立跑完整 deepresearch 单问题调查**（复用现有全管线：搜索+直连权威源+分层打分+精排），由 `SubagentScheduler` 有界并发调度（research lane=2，全局 cap=4，超额排队背压）；N 份子调查报告回到主线程 → 主线程做**统一 synthesize 分析** → 得出最终结论。**全程 flag-gated，出厂默认 OFF，OFF 时字节级 BC**。

---

## 1. 背景 — 现状与要改的点（已读码核实）

### 1.1 deepresearch 当前管线（`research_tools.py`）

入口 `deepresearch()`（[:980](../../backend/deskpet/tools/research_tools.py:980)）当前是**单编排器扁平池**：

| 阶段 | 行 | 现状 |
|---|---|---|
| 1. plan 拆题 | [:1058](../../backend/deskpet/tools/research_tools.py:1058) | LLM 拆出 `sub_questions`（3–6 条） |
| 1.5 query 扩展 | [:1076](../../backend/deskpet/tools/research_tools.py:1076) | multi-query + HyDE |
| 2. search | [:1083](../../backend/deskpet/tools/research_tools.py:1083) | **所有子问题的搜索串拍平成一个 `search_specs` 列表统一搜** |
| 3. fetch+extract | [:1132](../../backend/deskpet/tools/research_tools.py:1132) | 候选 URL 统一抓 |
| 4. score+filter | [:1143](../../backend/deskpet/tools/research_tools.py:1143) | 统一打分（authority/recency/relevance/depth） |
| 4.4 直连权威源 | [:1213](../../backend/deskpet/tools/research_tools.py:1213) | cninfo/百科/wiki/arxiv 多源并发 |
| 4.5 reflection | [:1300](../../backend/deskpet/tools/research_tools.py:1300) | deep 档补轮 |
| 4.6/4.7 语义+精排 | [:1334](../../backend/deskpet/tools/research_tools.py:1334)/[:1364](../../backend/deskpet/tools/research_tools.py:1364) | BGE-M3 + LLM rerank |
| 5. synthesize | [:1408](../../backend/deskpet/tools/research_tools.py:1408) | **一次** `_SYNTH_PROMPT` 把所有 passage 喂 LLM 出整篇 |
| 6. cite_check | [:1427](../../backend/deskpet/tools/research_tools.py:1427) | 引用自检 + 废引用清理 |

> **要改的点**：在 plan 拆题（`sub_questions` 算出，[:1074](../../backend/deskpet/tools/research_tools.py:1074)）**之后**插入分叉——满足 fan-out 条件时，走「每子问题一个子代理子调查 → 跨子报告 synthesize」的新路径；否则原扁平路径**一字节不动**。

### 1.2 注入机制（已核实，照抄现有范式）

- **工具是自注册**：`_register_deepresearch_tool()`（[:1856](../../backend/deskpet/tools/research_tools.py:1856)）在 import 时 `registry.register("deepresearch", ...)`，handler = `_handle_deepresearch`（[:1711](../../backend/deskpet/tools/research_tools.py:1711)）。
- **LLM 走进程级全局桥**：main.py 启动时设一个 module-global，`_resolve_default_llm_call()`（[:1797](../../backend/deskpet/tools/research_tools.py:1797) 附近）读它。
- **➡️ scheduler 注入照抄此模式**：新增 module-global `_SUBAGENT_SCHEDULER` + `set_subagent_scheduler(s)`，main.py lifespan 在 flag ON 时设置；`_handle_deepresearch` 读取后传给 `deepresearch(..., scheduler=...)`。**无须把自注册改成工厂**，最小扰动。

### 1.3 子代理基建（已落地，本计划只调用不重写）

- `SubagentScheduler.run(*, kind, run_id, task_id, parent_sid, coro_factory) -> Any`（[`subagent_scheduler.py:67`](../../backend/deskpet/agent/subagent_scheduler.py:67)）：双闸背压，返回 `coro_factory()` 的结果；自动 emit `subagent_progress`（queued→running→completed/failed）+ 日志锚点 `subagent_scheduled kind=...`。
- `task_kinds.py`：内置 `research` KindProfile（[:60](../../backend/deskpet/agent/task_kinds.py:60)）。**⚠️ 递归炸弹**：其工具集含 `deepresearch` 自身（[:62](../../backend/deskpet/agent/task_kinds.py:62)）。本计划的内部 fan-out **不经 LLM 工具调用**（是 deepresearch 内部编程式调度），所以**不碰这个 KindProfile**——但仍要硬保证内层子调查永不再 fan-out（见 §5 递归守门）。

---

## 2. 用户愿景 → 设计映射

> 用户原话：「Plan 拆题之后，每一个子问题都让一个子代理进行详细的调查，然后把调查报告给到主线程，让主线程进行统一的分析，然后得出结论。」
> 用户拍板：**复用 deepresearch 全管线**（每子问题跑完整单问题 deepresearch）+ 先写 plan review 后执行。

| 愿景 | 设计落点 |
|---|---|
| Plan 拆题之后 | plan 阶段不变，分叉点在 [:1074](../../backend/deskpet/tools/research_tools.py:1074) 之后 |
| 每子问题一个子代理详细调查 | 每子问题 = 一次 `deepresearch(子问题, max_sub_questions=1, _depth=1, scheduler=None)` 子调用（复用全管线：搜索+直连源+打分+精排+单问题 synth），经 `scheduler.run(kind="research", ...)` 有界并发跑 |
| 把调查报告给主线程 | 每个子调用返回一个 `ResearchReport`（含 report_md + citations + coverage）= 子调查报告 |
| 主线程统一分析得结论 | 主线程新 synthesize：把 N 份子报告 + 全局重编号引用喂 `_FANOUT_SYNTH_PROMPT` → 统一分析 + 结论；cite_check；落盘一次 |

---

## 3. 设计总览

```
deepresearch(topic, scheduler=S, _depth=0)
  │
  ├─ 1. plan 拆题 → sub_questions = [q1, q2, q3, ...]          (不变)
  │
  ├─ if fanout 条件满足 (flag ON & scheduler & _depth==0 & ≥2 子问题):
  │     │
  │     │   ┌─ S.run(kind=research, run=dr-1, coro=deepresearch(q1, max_sub_questions=1, _depth=1, scheduler=None))
  │     ├──►├─ S.run(kind=research, run=dr-2, coro=deepresearch(q2, ...))   ← 有界并发(cap4/lane2)
  │     │   └─ S.run(kind=research, run=dr-3, coro=deepresearch(q3, ...))      超额排队背压
  │     │            (内层 scheduler=None → 永不再 fan-out → depth-1 硬保证)
  │     │
  │     ├─ gather(return_exceptions=True) → N 份子 ResearchReport (失败隔离)
  │     │
  │     ├─ 全局引用重编号 (合并所有子报告 citations, 按 URL 去重, 重排 [^n])
  │     │
  │     ├─ 主线程统一 synthesize: _FANOUT_SYNTH_PROMPT(N 份子报告 + 合并引用) → 统一报告+结论
  │     │
  │     ├─ cite_check(合并引用) + 废引用清理                    (复用现有)
  │     │
  │     └─ return ResearchReport(coverage.subagent_fanout=观测)
  │
  └─ else: 现有扁平管线 (search→fetch→score→直连→reflection→语义→精排→synth→cite)  ← 字节级不变 BC
```

**核心不变量**：
1. **复用**：子调查 = 完整 deepresearch 单问题跑，§6.0 的全部质量能力（直连权威源/分层打分/精排/anti-blocking）原样生效。
2. **有界并发**：scheduler 双闸背压；N 个子问题不会一次性打爆 relay/中转站。
3. **depth-1 硬保证**：内层子调用 `scheduler=None` + `_depth=1`，fanout 条件含 `_depth==0`，内层永不再分叉。
4. **失败隔离**：单个子代理失败 → 记 error，其余子报告照常合成（部分胜过全失败）。
5. **BC**：flag OFF / scheduler 未注入 / 子问题 <2 → 原扁平路径一字节不动。

---

## 4. 关键决策（D1–D8）

- **D1 — 子调查 = 复用 deepresearch 单问题全跑，不写新调查器**：每子问题 `deepresearch(q, max_sub_questions=1, _depth=1, scheduler=None)`。理由：用户拍板「复用全管线」；保住 §6.0 全部质量能力；零重复代码。被否：真 AgentLoop research 子代理（丢失直连源/打分/精排，单问题质量反降，用户这轮死磕的质量会倒退）。
- **D2 — 内层用 `max_sub_questions=1` + 可选 `skip_plan`**：内层把子问题当唯一问题，避免「子问题再被 LLM 拆成更多子问题」的浪费与漂移。`skip_plan=True` 直接用 `[q]` 跳过内层 plan LLM 调用（省一次 round-trip + 防漂移）。理由：子问题已是叶子粒度，无须再拆。
- **D3 — 经 scheduler 调度，不裸 gather**：每子调查包进 `scheduler.run(kind="research", coro_factory=...)`，由全局 cap=4 + research lane=2 背压。理由：复用已测调度地基；防 N×全管线 并发打爆 relay（风险 R1）。scheduler 缺失（flag 未全开）→ 回退本地 `asyncio.Semaphore(2)` 兜底有界，不裸 gather。
- **D4 — 统一分析 = 新 `_FANOUT_SYNTH_PROMPT` 跨子报告综合**：不复用 `_SYNTH_PROMPT`（那是 passage→报告）。新 prompt 输入 = N 个「子问题 + 其调查小结 + 其来源（全局 [^n]）」，要求 LLM 跨子问题交叉对比、消解冲突、给统一结论。理由：用户要「统一的分析得出结论」，不是 N 份报告拼接。
- **D5 — 引用全局重编号**：合并所有子报告 citations，按归一 URL 去重，重排全局 `[^n]`；synthesize 让 LLM 引用全局编号；cite_check 对合并引用。理由：N 份子报告各自 [^1][^2]… 会撞号；全局重编号是唯一正确做法。
- **D6 — flag = `[research].subagent_fanout`（默认 false），依赖 `features.subagent_driver`**：fan-out 需要 scheduler，scheduler 由 `features.subagent_driver=ON` 构造（子代理 driver 计划）。两者都 ON 才生效。理由：复用 driver 的 scheduler 生命周期，不重复构造；项目铁律新功能默认 OFF。读取走 `_research_raw()` 安全兜底（`b05823b` 教训，**不复刻 `config.config` 单例 bug**）。
- **D7 — 落盘只在最外层一次**：内层子调用直接走 `deepresearch()`（不经 `_handle_deepresearch` 的 `_save_report`），天然不落 N 份中间报告；主线程合成后由 `_handle_deepresearch` 落盘一次。理由：避免 OutPut/Research 堆 N 份碎报告。
- **D8 — 子调查档位降一级 + 单跑超时**：外层 deep → 内层每子跑 standard（外层 standard → 内层 light），并 `asyncio.wait_for(子跑, timeout=_subrun_timeout)`（默认 120s）。理由：N 份全管线叠加易破 300s tool 超时；降档 + 单跑超时 + 并发重叠三管齐下（风险 R2）。

---

## 5. 递归守门（depth-1 硬保证）

内置 `research` KindProfile 工具集含 `deepresearch`（[task_kinds.py:62](../../backend/deskpet/agent/task_kinds.py:62)）——若将来有人用 LLM 工具 `agent_parallel(kind=research)` 派子代理，子代理能调 `deepresearch` → 又 fan-out。**本计划的内部 fan-out 必须自带守门**，三重保险：

1. **`_depth` 计数**：`deepresearch(..., _depth=0)` 默认；fanout 分叉只在 `_depth==0` 触发；内层子调用传 `_depth=1` → 内层 fanout 条件不满足 → 必走扁平。
2. **内层 `scheduler=None`**：内层子调用不传 scheduler → 即便 `_depth` 漏判，无 scheduler 也进不了 fanout 分支。
3. **（防御）LLM 路径**：若后续真用 `agent_parallel(kind=research)` 派子代理，建议在 `task_kinds` research 工具集**剥掉 `deepresearch`**（改 `("web_search","web_fetch","read_file")`）——属子代理 driver 计划的收口项，本计划在 §9 风险登记标注、不强改（避免跨计划耦合），但内部 fan-out 不依赖它。

---

## 6. Flag 表（出厂默认 OFF，BC 字节级）

| flag | 位置 | 出厂 | 作用 | OFF 行为 |
|---|---|---|---|---|
| `[research].subagent_fanout` | `[research]` 段 | `false` | 开启 deepresearch 子代理 fan-out | 走现有扁平管线（字节级 BC） |
| 依赖：`features.subagent_driver` | `[features]` | `false` | 构造 scheduler（子代理 driver 计划） | 无 scheduler → fan-out 自动退回扁平 |
| `[research].fanout_subrun_mode` | `[research]` | `"auto"` | 子跑档位策略（auto=外层降一级 / light / standard / inherit） | auto |
| `[research].fanout_subrun_timeout` | `[research]` | `120.0` | 每子调查单跑超时（秒） | 120 |
| `[research].fanout_min_subquestions` | `[research]` | `2` | 少于此数不 fan-out（1 个子问题没必要） | 2 |

> 读取一律走 `_research_raw()`（research_tools 内已有 `b05823b` 后的安全兜底实现）+ getattr 兜底，**不读 `config.config` 单例**。

---

## 7. 码级实施（WI，TDD 先红后绿）

### WI-1 — `deepresearch()` 加 fan-out 分叉 + 递归守门参数

**改** [`research_tools.py:980`](../../backend/deskpet/tools/research_tools.py:980) 签名：
```python
async def deepresearch(
    topic: str, *, llm_call, search=None, extract=None,
    max_sub_questions=5, max_urls_per_query=4, max_total_passages=12,
    min_passage_chars=250, max_rounds=1, mode="standard", user_request=None,
    scheduler=None,            # ★新：SubagentScheduler|None（仅最外层注入）
    parent_sid="default",      # ★新：进度事件标识
    _depth=0,                  # ★新：递归深度，>0 禁 fanout（depth-1 守门）
    skip_plan=False,           # ★新：子跑跳过内层 plan，直接用 [topic]
) -> ResearchReport:
```
plan 阶段（[:1058](../../backend/deskpet/tools/research_tools.py:1058)）：`skip_plan=True` 时跳过 LLM plan，`sub_questions=[topic]`。
plan 之后（[:1074](../../backend/deskpet/tools/research_tools.py:1074) 后）插入分叉：
```python
fanout_on = (
    scheduler is not None and _depth == 0
    and len(sub_questions) >= _fanout_min_subquestions()   # 默认 2
    and _fanout_enabled()                                  # [research].subagent_fanout
)
if fanout_on:
    return await _run_subagent_fanout(
        topic=topic, sub_questions=sub_questions, llm_call=llm_call,
        search=search_fn, extract=extract_fn, scheduler=scheduler,
        parent_sid=parent_sid, mode=mode, user_request=_ur,
        errors=errors, route=route,
    )
# else: 现有扁平管线不变（BC）
```
**测试** TG-1：fanout_on=False 各分支（scheduler=None / _depth=1 / <2 子问题 / flag OFF）→ 走扁平、与现状字节级一致（mock 对比）。

### WI-2 — `_run_subagent_fanout(...)` 新协程（fan-out 编排核心）

**新增** `research_tools.py`：
```python
async def _run_subagent_fanout(*, topic, sub_questions, llm_call, search, extract,
                               scheduler, parent_sid, mode, user_request,
                               errors, route) -> ResearchReport:
    submode = _fanout_subrun_mode(mode)        # D8: auto=降一级
    timeout = _fanout_subrun_timeout()         # 默认 120s
    d_subq, d_urls, d_pass, d_rounds = _DEPTH_PRESETS[submode]

    async def _one(i, q):
        async def _coro():
            return await asyncio.wait_for(deepresearch(
                q, llm_call=llm_call, search=search, extract=extract,
                max_sub_questions=1, max_urls_per_query=d_urls,
                max_total_passages=d_pass, max_rounds=d_rounds,
                mode=submode, user_request=q,
                scheduler=None, _depth=1, skip_plan=True,   # ★ depth-1 守门
            ), timeout=timeout)
        return await scheduler.run(
            kind="research", run_id=f"{parent_sid}.dr-{i}",
            task_id=f"dr-{i}", parent_sid=parent_sid, coro_factory=_coro,
        )

    results = await asyncio.gather(
        *[_one(i, q) for i, q in enumerate(sub_questions)],
        return_exceptions=True,
    )
    # 失败隔离 + 收集子报告
    sub_reports = []
    fanout_obs = {"enabled": True, "n_subagents": len(sub_questions),
                  "n_completed": 0, "n_failed": 0, "per_subquestion": []}
    for q, r in zip(sub_questions, results):
        if isinstance(r, BaseException):
            errors.append(f"fanout subagent {q!r}: {r}")
            fanout_obs["n_failed"] += 1
            fanout_obs["per_subquestion"].append({"q": q, "ok": False})
            continue
        sub_reports.append((q, r))
        fanout_obs["n_completed"] += 1
        fanout_obs["per_subquestion"].append({
            "q": q, "ok": True,
            "n_sources": (r.coverage or {}).get("n_sources", 0),
        })

    if not sub_reports:        # 全失败兜底
        return _no_results_report(topic, sub_questions, errors)

    # 全局引用重编号（WI-3）→ 统一 synthesize → cite_check（复用 :1427 逻辑）
    merged_citations, report_md = await _fanout_synthesize(
        topic, user_request, sub_reports, llm_call, errors)
    ... cite_check + 废引用清理（抽成可复用 helper，见 WI-4）...
    coverage = {... "subagent_fanout": fanout_obs, "mode": "fanout", ...}
    return ResearchReport(topic=topic, report_md=report_md,
                          citations=merged_citations, sub_questions=sub_questions,
                          coverage=coverage, errors=errors)
```
**scheduler 缺失兜底**：`_run_subagent_fanout` 若 `scheduler is None` 不会被调用（WI-1 条件已挡）；但为稳妥，scheduler 路径内部不假设其它服务。
**测试** TG-2：mock scheduler（直接 await coro_factory）+ mock 内层 deepresearch（返桩 ResearchReport）→ 验证 N 份合并、失败隔离（1 失败其余存活）、全失败兜底、coverage.subagent_fanout 正确、depth-1（断言内层收到 scheduler=None/_depth=1）。

### WI-3 — 全局引用重编号 helper

**新增**：
```python
def _merge_subreport_citations(sub_reports) -> tuple[list[Citation], dict]:
    """合并所有子报告 citations，按归一 URL 去重，重排全局 [^n]。
    返回 (merged_citations, {(report_idx, local_n): global_n})。"""
```
用现有 `_norm_url`（§4.4 契约-7 已有）做 URL 归一去重。
**测试** TG-3：撞号子报告（都含 [^1][^2]）→ 合并后全局唯一、URL 去重正确、映射表准。

### WI-4 — `_fanout_synthesize` + cite_check 复用

**新增** `_FANOUT_SYNTH_PROMPT`（module 级常量）：输入 N 个「### 子问题 q\n<子报告正文摘要>\n来源: [^g1][^g2]…」块 + 合并引用列表；要求跨子问题交叉对比、消解冲突、统一结论、正文引用全局 [^n]。
**新增** `_fanout_synthesize(topic, user_request, sub_reports, llm_call, errors)`：调 `_merge_subreport_citations` → `_format_subreports_for_llm`（剥子报告本地脚注、贴全局编号）→ `llm_call(_FANOUT_SYNTH_PROMPT...)` → 失败降级到「子报告拼接 + 全局引用」。
**重构（不改行为）**：把 [:1427–1452](../../backend/deskpet/tools/research_tools.py:1427) 的 cite_check + 废引用清理 + 引用附录抽成 `_finalize_report_md(report_md, citations, errors) -> (report_md, citations)`，扁平路径与 fanout 路径共用（扁平路径回归测试 diff=0）。
**测试** TG-4：synth LLM 失败 → 降级拼接含全局引用；cite_check 复用正确。

### WI-5 — scheduler 注入（main.py + 全局桥）+ flag 读取

**改** `research_tools.py`：仿 LLM 全局桥（[:1797](../../backend/deskpet/tools/research_tools.py:1797)）加：
```python
_SUBAGENT_SCHEDULER = None
def set_subagent_scheduler(s) -> None:
    global _SUBAGENT_SCHEDULER; _SUBAGENT_SCHEDULER = s
def _get_subagent_scheduler(): return _SUBAGENT_SCHEDULER
def _fanout_enabled() -> bool: return bool(_research_raw().get("subagent_fanout", False))
def _fanout_min_subquestions() -> int: return int(_research_raw().get("fanout_min_subquestions", 2))
def _fanout_subrun_timeout() -> float: return float(_research_raw().get("fanout_subrun_timeout", 120.0))
def _fanout_subrun_mode(outer) -> str: ...  # auto=降一级映射
```
**改** `_handle_deepresearch`（[:1738](../../backend/deskpet/tools/research_tools.py:1738)）：`deepresearch(..., scheduler=_get_subagent_scheduler(), parent_sid=_resolve_sid(task_id))`。
**改** `main.py` lifespan：在子代理 driver 块（scheduler 构造处，driver 计划 WI-1.4）之后：
```python
if _subagent_scheduler is not None and bool(_research_raw().get("subagent_fanout", False)):
    from deskpet.tools import research_tools as _rt
    _rt.set_subagent_scheduler(_subagent_scheduler)
    log.info("deepresearch subagent fanout ENABLED (scheduler wired)")
```
**测试** TG-5：`_fanout_enabled` 真读 `[research].subagent_fanout`（b05823b 回归：真 config 读到 True）；无 `.raw` stub 不抛。

### WI-6 — 观测 + WS 进度（复用，几乎零新代码）

- coverage 加 `subagent_fanout` 块（WI-2 已写）+ `mode="fanout"`。
- WS 进度：`scheduler.run` 已 emit `subagent_progress`（driver 计划 WI-1.5 的 `_subagent_progress_sink` 广播）→ 桌宠前端 `SubagentProgressPanel` 自动显示 N 条 research lane 并发「忙」。**本计划无须新增前端**（复用 driver 的面板）。
- 日志锚点：`subagent_scheduled kind=research run_id=<sid>.dr-N`（scheduler 自带，真机 grep 验 N 个并发）。

### WI-7 — 测试组（详见 §10）

---

## 8. 改动文件清单

| 文件 | 改动 | WI |
|---|---|---|
| `backend/deskpet/tools/research_tools.py` | 签名加 5 参 + fanout 分叉 + `_run_subagent_fanout` + 引用重编号 + `_fanout_synthesize` + `_FANOUT_SYNTH_PROMPT` + 全局桥 + flag helper + cite 复用重构 | WI-1~6 |
| `backend/main.py` | lifespan 接 `set_subagent_scheduler` | WI-5 |
| `backend/config.py`（可选） | `[research]` 段 raw-read，无需 dataclass 字段（已 raw 兜底） | WI-5 |
| `backend/tests/test_deepresearch_subagent_fanout.py` | 新建测试组 TG-1~5 | WI-7 |
| `testcase/<date>-deepresearch-subagent-fanout/manual-test.md` | windows-mcp 真机 E2E | §10 |

> 前端：**零改动**（复用子代理 driver 的 `SubagentProgressPanel`）。

---

## 9. 风险登记

| # | 风险 | 缓解 |
|---|---|---|
| R1 | N×全管线子调查并发打爆 relay（429/504） | scheduler 全局 cap=4 + research lane=2 背压；子跑降档（D8）；沿用现有 relay 重试链 |
| R2 | N 份全管线叠加破 300s tool 超时 | 子跑降档（D8）+ 单跑 `wait_for(120s)` + 并发重叠；必要时提 deepresearch tool timeout（[:1870](../../backend/deskpet/tools/research_tools.py:1870)） |
| R3 | 递归 fan-out（子代理再 fan-out）爆栈/烧钱 | §5 三重守门（`_depth==0` 条件 + 内层 `scheduler=None` + 防御性剥工具建议） |
| R4 | 引用撞号 / 全局重编号错 → 悬空引用 | WI-3 URL 归一去重 + 映射表；cite_check 复用兜底（[:1427](../../backend/deskpet/tools/research_tools.py:1427)） |
| R5 | 单子代理失败拖垮整 run | `gather(return_exceptions=True)` 失败隔离；全失败才 no_results 兜底 |
| R6 | flag 静默失效（config.config 单例陷阱重演） | 一律 `_research_raw()` 安全兜底；TG-5 加真 config 回归（仿 b05823b） |
| R7 | BC 破坏（扁平路径被动了） | 每 WI 末跑扁平回归 diff=0；cite 复用重构后扁平路径用例全绿 |
| R8 | task_kinds research 工具集含 deepresearch（LLM 路径递归隐患） | §5 标注；内部 fan-out 不依赖该 KindProfile；建议 driver 计划收口剥工具 |

---

## 10. 验收准入

### 10.1 单元（pytest，TDD 红→绿）

- TG-1：fanout 条件 4 分支 OFF → 走扁平、字节级 BC。
- TG-2：fanout ON（mock scheduler+内层）→ N 份合并、失败隔离、全失败兜底、coverage.subagent_fanout、depth-1 断言。
- TG-3：引用全局重编号（撞号去重映射）。
- TG-4：`_fanout_synthesize` + synth 失败降级 + cite 复用。
- TG-5：flag 真 config 读取（b05823b 回归）+ 无 .raw 不抛。
- 全量回归：现有 `test_deskpet_research_tools.py` 全绿（BC）。

### 10.2 真机 windows-mcp E2E（HARD — 不可用脚本/协议层替代）

环境：`features.subagent_driver=ON` + `[research].subagent_fanout=ON`，dev 账号登录（`LOCAL-DEV-CREDENTIALS.md`）。

| case | 动作 | 期望 | 证据 |
|---|---|---|---|
| TC-F1 | 桌宠输入框真输入「深度调研 2025 年钠离子电池产业现状」→ Enter | plan 拆 3–6 子问题 → backend log 出现 **N 条** `subagent_scheduled kind=research run_id=...dr-0/1/2...` 并发 | 截图 + log grep |
| TC-F2 | 观察前端 | `SubagentProgressPanel` 显示 N 条 research lane 并发「忙」（背压时部分 queued） | 截图 |
| TC-F3 | 等出报告 | 最终报告**跨子问题统一分析**（不是 N 份拼接）+ 引用全局连续 + 引用自检通过 | 截图报告 + 落盘 .md |
| TC-F4 | 背压验证（6 子问题场景） | 全局 cap=4 → 4 跑 2 排队 → 全部完成不丢 | log |
| TC-F5 | flag OFF 回归 | `subagent_fanout=OFF` → 同问题走扁平、无 `subagent_scheduled` log、报告正常 | log + 截图 |

> 失败必 retry ≥3 次不同 workaround 才可标「环境受限」；跳过须显式声明 + 等用户确认。

---

## 11. 范围外（明确不做）

- 子代理间 mesh 通信 / 共享池（用 `spawn_team`，非本计划）。
- 非阻塞后台 fan-out（deepresearch 是阻塞工具，返回即报告；非阻塞属 driver 计划 P3）。
- 递归深度 >1 的 orchestrator 树（§5 守门硬卡 depth-1）。
- 真 AgentLoop research 子代理（用户已否，选复用全管线）。
- 前端新面板（复用 driver 的 `SubagentProgressPanel`）。

---

## 12. 执行纪律（codingsys）

- 本计划即 spec（3+ 文件 → spec-first 满足）；每 WI 先红后绿；编辑后必跑 pytest。
- 实现可用 codex gpt-5.5 子代理并行（WI-1/2/3/4 同文件串行，测试可并行写）。
- flag OFF 字节基线守：每 WI 末跑扁平回归。
- 真机收口：windows-mcp E2E（§10.2），不接受单测/协议层替代（feedback_real_e2e）。
- STATUS 纪律：验收后同步 `STATUS/status.md` §3 + 里程碑。
