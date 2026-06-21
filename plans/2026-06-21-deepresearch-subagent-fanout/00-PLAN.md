# 00-PLAN — DeepResearch 子代理 Fan-out（每子问题一个子代理深查 → 主线程统一分析）

> **版本**: v0.2（吸收第 1 轮 codex gpt-5.5 双子代理对抗挑战：7 BLOCKING + 4 MAJOR 全修，见 §13 附录）
> **日期**: 2026-06-21
> **状态**: 📋 规划中（先写 plan，review 后再执行）
> **本目录**: `plans/2026-06-21-deepresearch-subagent-fanout/`
> **关联**:
> - 子代理基建：[`plans/2026-06-21-subagent-concurrency-driver/`](../2026-06-21-subagent-concurrency-driver/00-PRD.md)（scheduler/task_kinds/registry 已落地）
> - deepresearch 现状：[`research_tools.py`](../../backend/deskpet/tools/research_tools.py)（§6.0 改造后全管线）

---

## 0. 一句话

deepresearch 的 **Plan 拆题之后**，不再把所有子问题丢进一个扁平池统一搜，而是**每个子问题派一个子代理独立跑完整 deepresearch 单问题调查**（复用现有全管线：搜索+直连权威源+分层打分+精排），由 `SubagentScheduler` 有界并发调度（research lane=2，全局 cap=4，超额排队背压）；N 份子调查报告回到主线程 → 主线程做**统一 synthesize 分析** → 得出最终结论。**全程 flag-gated，出厂默认 OFF，OFF 时字节级 BC**。

> **附带需求（WI-8，与 fan-out flag 无关，所有 deepresearch 都生效）**：所有报告落 **deskpet 安装目录下的 `DeepResearch/`**（**不进 C 盘 `%AppData%`**）+ 维护 `DeepResearch/index.md` 总索引（倒序、可点开）+ repo README 记录该索引作用；**打包应用同逻辑**（安装根解析）。

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
- `task_kinds.py`：内置 `research` KindProfile（[:60](../../backend/deskpet/agent/task_kinds.py:60)）。**⚠️ 递归炸弹（R1 挑战确认严重）**：其工具集含 `deepresearch` 自身（[:62](../../backend/deskpet/agent/task_kinds.py:62)）。本计划的内部 fan-out 虽是编程式调度，但 WI-5 会给 `_handle_deepresearch` 注入全局 scheduler——于是**LLM 派的 research 子代理调 `deepresearch` 工具就会再次 fan-out**（挑战者 A BLOCKING-2）。**因此递归守门必须做成强制、airtight**（见 §5 重写版），不能只当"建议"。

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

## 4. 关键决策（D1–D10）

- **D1 — 子调查 = 复用 deepresearch 单问题全跑，不写新调查器**：每子问题 `deepresearch(q, max_sub_questions=1, _depth=1, scheduler=None)`。理由：用户拍板「复用全管线」；保住 §6.0 全部质量能力；零重复代码。被否：真 AgentLoop research 子代理（丢失直连源/打分/精排，单问题质量反降，用户这轮死磕的质量会倒退）。
- **D2 — 内层用 `max_sub_questions=1` + 可选 `skip_plan`**：内层把子问题当唯一问题，避免「子问题再被 LLM 拆成更多子问题」的浪费与漂移。`skip_plan=True` 直接用 `[q]` 跳过内层 plan LLM 调用（省一次 round-trip + 防漂移）。理由：子问题已是叶子粒度，无须再拆。
- **D3 — 经 scheduler 调度，不裸 gather**：每子调查包进 `scheduler.run(kind="research", coro_factory=...)`，由全局 cap=4 + research lane=2 背压。理由：复用已测调度地基；防 N×全管线 并发打爆 relay（风险 R1）。scheduler 缺失（flag 未全开）→ 回退本地 `asyncio.Semaphore(2)` 兜底有界，不裸 gather。
- **D4 — 统一分析 = 新 `_FANOUT_SYNTH_PROMPT` 跨子报告综合**：不复用 `_SYNTH_PROMPT`（那是 passage→报告）。新 prompt 输入 = N 个「子问题 + 其调查小结 + 其来源（全局 [^n]）」，要求 LLM 跨子问题交叉对比、消解冲突、给统一结论。理由：用户要「统一的分析得出结论」，不是 N 份报告拼接。
- **D5 — 引用全局重编号**：合并所有子报告 citations，按归一 URL 去重，重排全局 `[^n]`；synthesize 让 LLM 引用全局编号；cite_check 对合并引用。理由：N 份子报告各自 [^1][^2]… 会撞号；全局重编号是唯一正确做法。
- **D6 — flag = `[research].subagent_fanout`（默认 false），依赖 `features.subagent_driver`**：fan-out 需要 scheduler，scheduler 由 `features.subagent_driver=ON` 构造（子代理 driver 计划）。两者都 ON 才生效。理由：复用 driver 的 scheduler 生命周期，不重复构造；项目铁律新功能默认 OFF。读取走 `_research_raw()` 安全兜底（`b05823b` 教训，**不复刻 `config.config` 单例 bug**）。
- **D7 — 落盘只在最外层一次**：内层子调用直接走 `deepresearch()`（不经 `_handle_deepresearch` 的 `_save_report`），天然不落 N 份中间报告；主线程合成后由 `_handle_deepresearch` 落盘一次。理由：避免 OutPut/Research 堆 N 份碎报告。
- **D8 —【R1:A-BLOCKING-3 重写】预算由构造保证 ≤ tool 超时**：原"固定 120s 单跑超时"数学不成立（挑战者 A：deep=6 子问题、research lane=2 → 3 波 ×120s=360s 已破 300s）。改为**动态预算**，由构造保证最坏 wall-clock ≤ tool 超时：
  - 模块常量 `_DEEPRESEARCH_TOOL_TIMEOUT=300.0`（注册处 [:1870](../../backend/deskpet/tools/research_tools.py:1870) 与预算计算**共用同一常量**）、`_FANOUT_OUTER_RESERVE=60.0`（外层 plan+merge+synth 预留）。
  - `n = min(len(sub_questions), fanout_max_subquestions)`（默认 cap=6；超出的子问题丢弃并 `log` 记 dropped，不静默）。
  - `conc = min(global_concurrency, research_lane_cap)`（从 `[agent.concurrency]` 读，默认 `min(4,2)=2`；缺省安全兜底）。
  - `waves = ceil(n / conc)`；`per_subrun_timeout = clamp((TOOL_TIMEOUT - RESERVE) / waves, 45.0, 150.0)`。
  - **可证**：`waves * per_subrun + RESERVE ≤ TOOL_TIMEOUT`（默认 n=6→waves=3→per_subrun=80s→3×80+60=300）；TG-2 加断言。`fanout_max_subquestions` 上限保证 `(TOOL-RESERVE)/waves ≥ 45`（waves≤5）。
  - 子跑档默认走 `_fanout_subrun_mode`（auto=deep→standard / standard→light / light→light）；**注意直连权威源（baike/wiki/arxiv/cninfo）在任何档位都跑**，所以降档不丢一手源质量，只减普通搜索的 URL/passage 量。
- **D9 —【R1:A-MAJOR-1 / B-BLOCKING-2】`parent_sid` 来源 = 注入的 `_session_id`，不是 task_id 反查**：`_resolve_sid(task_id)` 不存在。registry 执行前把 session context 合并进 params（[registry.py:690](../../backend/deskpet/tools/registry.py:690)），main 注入 `_session_id`（[main.py:5992](../../backend/main.py:5992)）。故 `_handle_deepresearch` 用 `parent_sid = str(args.get("_session_id") or "default")`。
- **D10 —【R1:A-BLOCKING-2】递归守门强制化（airtight）**：`_handle_deepresearch` **只在顶层会话**注入 scheduler——若 `_session_id` 含子代理标记（`.sub` / `.par-` / `.team-` / `.dr-`）则 `scheduler=None`，使任何 LLM 派的子代理调 `deepresearch` 都进不了 fan-out 分支。详见 §5 重写。

---

## 5. 递归守门（depth-1 硬保证，airtight）

挑战者 A 指出原"三重保险"有漏：WI-5 给 `_handle_deepresearch` 注入全局 scheduler 后，**LLM 派的 research 子代理调 `deepresearch` 工具仍会再 fan-out**（KindProfile 含 deepresearch）。修正为**四重 + 强制**：

1. **顶层才注入 scheduler（关键，D10）**：`_handle_deepresearch` 解析 `parent_sid = args.get("_session_id")`；若含子代理标记（`.sub`/`.par-`/`.team-`/`.dr-`）→ `scheduler=None`。这从**入口**杜绝任何子代理触发 fan-out：
   ```python
   sid = str(args.get("_session_id") or "default")
   _IN_SUBAGENT = any(m in sid for m in (".sub", ".par-", ".team-", ".dr-"))
   sched = None if _IN_SUBAGENT else _get_subagent_scheduler()
   report = await deepresearch(..., scheduler=sched, parent_sid=sid)
   ```
2. **`_depth` 计数**：fanout 分叉只在 `_depth==0` 触发；内层子调用传 `_depth=1` → 必走扁平。
3. **内层 `scheduler=None`**：`_run_subagent_fanout` 的每个内层 `deepresearch()` 显式不传 scheduler → 即便 `_depth` 漏判也进不了 fanout。
4. **【强制，非建议】剥 research KindProfile 的 deepresearch**：本计划**直接改** [task_kinds.py:62](../../backend/deskpet/agent/task_kinds.py:62) research 工具集 `("web_search","web_fetch","deepresearch","read_file")` → `("web_search","web_fetch","read_file")`（移除 `deepresearch`）。理由：消除 LLM 子代理调 deepresearch 的可能性（双保险，配合第 1 条）。这是一行安全收口，纳入本计划 WI-5（不再"留给 driver 计划"）。

> 第 1 条（顶层门）是主防线，第 4 条（剥工具）是不依赖 sid 命名的次防线；两者独立成立 → airtight。

---

## 6. Flag 表（出厂默认 OFF，BC 字节级）

| flag | 位置 | 出厂 | 作用 | OFF 行为 |
|---|---|---|---|---|
| `[research].subagent_fanout` | `[research]` 段 | `false` | 开启 deepresearch 子代理 fan-out | 走现有扁平管线（字节级 BC） |
| 依赖：`features.subagent_driver` | `[features]` | `false` | 构造 scheduler（子代理 driver 计划） | 无 scheduler → fan-out 自动退回扁平 |
| `[research].fanout_subrun_mode` | `[research]` | `"auto"` | 子跑档位策略（auto=外层降一级 / light / standard / inherit）；直连权威源任何档都跑 | auto |
| `[research].fanout_min_subquestions` | `[research]` | `2` | 少于此数不 fan-out（1 个子问题没必要） | 2 |
| `[research].fanout_max_subquestions` | `[research]` | `6` | fan-out 最多并发子问题数（超出丢弃并 log；保证预算 ≤ tool 超时，D8） | 6 |

> 单跑超时**不再是固定 flag**——由 D8 动态计算（`clamp((300−60)/waves, 45, 150)`，与注册 tool timeout 共用 `_DEEPRESEARCH_TOOL_TIMEOUT` 常量），按构造保证最坏 wall-clock ≤ tool 超时。
| `DESKPET_DEEPRESEARCH_DIR`（env，非 flag） | 环境变量 | （未设）| 覆盖 DeepResearch 落盘根目录（测试/power user） | 走 `deepresearch_dir()` 默认解析（安装根） |

> 报告落盘根目录（WI-8）默认 = **安装目录/DeepResearch/**（dev=repo 根），**不进 C 盘 `%AppData%`**；与 fan-out flag 无关，所有 deepresearch 都落这。
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
    # —— D8 动态预算：按构造保证最坏 wall-clock ≤ tool 超时 ——
    cap = _fanout_max_subquestions()                       # 默认 6
    eff_subq = sub_questions[:cap]
    if len(sub_questions) > cap:
        errors.append(f"fanout: dropped {len(sub_questions)-cap} subquestions over cap {cap}")
    submode = _fanout_subrun_mode(mode)                    # auto=降一级；直连源各档都跑
    _, d_urls, d_pass, d_rounds = _DEPTH_PRESETS[submode]
    conc = max(1, min(_global_concurrency(), _research_lane_cap()))   # 默认 min(4,2)=2
    waves = max(1, math.ceil(len(eff_subq) / conc))
    timeout = max(45.0, min(150.0,
        (_DEEPRESEARCH_TOOL_TIMEOUT - _FANOUT_OUTER_RESERVE) / waves))

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
        *[_one(i, q) for i, q in enumerate(eff_subq)],
        return_exceptions=True,
    )
    # 失败隔离 + 收集子报告
    sub_reports = []   # list[tuple[str, ResearchReport]]
    fanout_obs = {"enabled": True, "n_subagents": len(eff_subq),
                  "n_completed": 0, "n_failed": 0,
                  "waves": waves, "per_subrun_timeout_s": round(timeout, 1),
                  "per_subquestion": []}
    for q, r in zip(eff_subq, results):
        if isinstance(r, BaseException):
            errors.append(f"fanout subagent {q!r}: {r}")
            fanout_obs["n_failed"] += 1
            fanout_obs["per_subquestion"].append({"q": q, "ok": False})
            continue
        sub_reports.append((q, r))
        errors.extend(r.errors or [])                      # 透传子跑错误
        fanout_obs["n_completed"] += 1
        fanout_obs["per_subquestion"].append({
            "q": q, "ok": True,
            "n_sources": (r.coverage or {}).get("n_sources", 0),
        })

    base_cov = {"n_sub_questions": len(sub_questions),
                "mode": "fanout", "subagent_fanout": fanout_obs}

    if not sub_reports:        # 全失败兜底（★A-BLOCKING-4：用现成 no-passages 构造，含 summary）
        return ResearchReport(
            topic=topic, summary="",
            report_md=_no_results_template(topic, sub_questions),
            citations=[], sub_questions=sub_questions,
            coverage={"n_sources": 0, "n_domains": 0, **base_cov},
            errors=errors,
        )

    # 全局引用重编号（WI-3）→ 统一 synthesize（WI-4）→ cite_check 复用 _finalize_report_md
    report_md, merged_citations = await _fanout_synthesize(
        topic, user_request, sub_reports, llm_call, errors)   # 内部已 _finalize_report_md
    domains = {_host(c.url) for c in merged_citations if _host(c.url)}
    coverage = {"n_sources": len(merged_citations), "n_domains": len(domains), **base_cov}
    return ResearchReport(
        topic=topic,
        summary=_extract_summary(report_md),                  # ★A-BLOCKING-4：必填 summary
        report_md=report_md, citations=merged_citations,
        sub_questions=sub_questions, coverage=coverage, errors=errors,
    )
```
**新增模块依赖**：`import math`（文件顶部，若未 import）。常量 `_DEEPRESEARCH_TOOL_TIMEOUT=300.0` / `_FANOUT_OUTER_RESERVE=60.0`；helper `_global_concurrency()`/`_research_lane_cap()`/`_fanout_max_subquestions()` 见 WI-5（读 `[agent.concurrency]`/`[research]`，安全兜底）。
**ResearchReport 字段对齐**（已核 [:572-579](../../backend/deskpet/tools/research_tools.py:572)）：必填 `topic/summary/report_md/citations/sub_questions/coverage`，`errors` 有默认。
**测试** TG-2：mock scheduler（直接 await coro_factory）+ mock 内层 deepresearch（返桩 ResearchReport）→ 验证 N 份合并、失败隔离（1 失败其余存活+错误透传）、全失败兜底（summary 字段在、不 TypeError）、coverage.subagent_fanout/waves 正确、cap 截断、depth-1（断言内层收到 scheduler=None/_depth=1）、**预算断言** `waves*timeout + RESERVE ≤ _DEEPRESEARCH_TOOL_TIMEOUT`。

### WI-3 — 全局引用重编号 helper（★A-BLOCKING-1：先把 `_norm_url` 提到模块级）

**前置重构（必做）**：`_norm_url` 当前是 §4.4 direct-source 分支内的**局部函数**（[research_tools.py:1217](../../backend/deskpet/tools/research_tools.py:1217) 附近），WI-3 无法直接复用。先把它**提为模块级** `def _norm_url(u: str) -> str:`（剪切到模块作用域），原 direct-source 分支改为引用模块级版本（行为不变，扁平回归 diff=0）。
**新增**：
```python
def _merge_subreport_citations(sub_reports) -> tuple[list[Citation], dict]:
    """合并所有子报告 citations，按归一 URL(_norm_url) 去重，重排全局 [^n]（从 1 起）。
    返回 (merged_citations, refmap)，refmap = {(report_idx, local_n): global_n}。
    Citation 字段已核：.n / .url / .title / .as_footnote()（[:540 附近]）。"""
```
**测试** TG-3：撞号子报告（都含 [^1][^2]）→ 合并后全局唯一、同 URL 跨报告去重为同一 global_n、refmap 准；模块级 `_norm_url` 提取后 direct-source 去重行为不变（回归）。

### WI-4 — `_fanout_synthesize` + 子报告脚注重写 + cite_check 复用（★A-MAJOR-2）

**问题（挑战者 A）**：每份子报告都自带 `## 引用` 附录 + 正文 `[^local]`（落盘逻辑 [:1449-1452](../../backend/deskpet/tools/research_tools.py:1449)）。直接拼进统一 synth 会**脚注撞号 + 悬空**。`find_footnote_refs`（[:908](../../backend/deskpet/tools/research_tools.py:908)）只返回编号，不剥除/不重写。必须显式处理。

**新增两个纯函数**：
```python
def _strip_footnote_definitions(md: str) -> str:
    """删除子报告末尾的「## 引用」整段 + 所有 `[^n]: ...` 定义行，只留正文（含正文 [^n] 标记）。"""
def _rewrite_local_refs(md: str, local_to_global: dict[int, int]) -> str:
    """把正文里的 `[^local]` 按映射改写为 `[^global]`（用 _FOOTNOTE_REF_RE 替换）。"""
```
**新增** `_FANOUT_SYNTH_PROMPT`（module 级常量）：输入 = N 个「### 子问题 q\n<已剥附录+已重写为全局编号的子报告正文>」块 + 合并引用列表；**明确指令**：「输入中各子报告的引用编号**已统一为全局编号**，你只能引用这些全局 `[^n]`，不得新造编号；跨子问题交叉对比、消解冲突、给统一结论」。
**新增** `async def _fanout_synthesize(topic, user_request, sub_reports, llm_call, errors) -> tuple[str, list[Citation]]`：
1. `merged, refmap = _merge_subreport_citations(sub_reports)`（WI-3）
2. 每份子报告：`body = _strip_footnote_definitions(r.report_md)` → `body = _rewrite_local_refs(body, {ln: refmap[(idx, ln)] ...})`
3. `llm_call(_FANOUT_SYNTH_PROMPT.format(...))` → 失败降级到「按子问题拼接已重写正文 + 全局引用附录」
4. `report_md, merged = _finalize_report_md(report_md, merged, errors)`（下面复用 helper）→ return
**重构（不改行为）**：把扁平路径 [:1427–1452](../../backend/deskpet/tools/research_tools.py:1427) 的 cite_check + 废引用清理（`find_footnote_refs`/`used_citations`）+ 引用附录追加抽成 `def _finalize_report_md(report_md, citations, errors) -> tuple[str, list[Citation]]`，扁平路径改调它（**逐字搬运、回归 diff=0**），fanout 路径复用。
**测试** TG-4：`_strip_footnote_definitions` 去附录留正文；`_rewrite_local_refs` 撞号→全局正确；synth LLM 失败→降级仍含全局引用且 cite_check 通过；`_finalize_report_md` 抽取后扁平用例字节级 BC。

### WI-5 — scheduler 注入（main.py + 全局桥）+ flag/并发读取 + 递归守门接线

**改 a — `research_tools.py` 全局桥 + helper**（仿 LLM 全局桥 [:1797](../../backend/deskpet/tools/research_tools.py:1797)）：
```python
import math   # 若文件未 import（WI-2 用）
_DEEPRESEARCH_TOOL_TIMEOUT = 300.0          # 与注册处 timeout_seconds 共用（[:1870]）
_FANOUT_OUTER_RESERVE = 60.0
_SUBAGENT_SCHEDULER = None
def set_subagent_scheduler(s) -> None:
    global _SUBAGENT_SCHEDULER; _SUBAGENT_SCHEDULER = s
def get_subagent_scheduler(): return _SUBAGENT_SCHEDULER   # 公开（main.py 不直接 import 私有）
def _fanout_enabled() -> bool: return bool(_research_raw().get("subagent_fanout", False))
def _fanout_min_subquestions() -> int: return int(_research_raw().get("fanout_min_subquestions", 2) or 2)
def _fanout_max_subquestions() -> int: return int(_research_raw().get("fanout_max_subquestions", 6) or 6)
def _fanout_subrun_mode(outer: str) -> str:
    m = str(_research_raw().get("fanout_subrun_mode", "auto")).lower()
    if m in ("light", "standard", "deep"): return m
    if m == "inherit": return outer if outer in _DEPTH_PRESETS else "standard"
    return {"deep": "standard", "standard": "light", "light": "light"}.get(outer, "light")  # auto
def _agent_raw() -> dict:    # 读 [agent.concurrency]，安全兜底（不复刻 config.config 单例 bug）
    try:
        import config as _cfg
        obj = getattr(_cfg, "config", None)
        raw = (obj.raw if obj is not None and hasattr(obj, "raw")
               else _cfg.load_config(_cfg.resolve_config_path()).raw)
        a = (raw or {}).get("agent") or {}
        return a if isinstance(a, dict) else {}
    except Exception: return {}
def _global_concurrency() -> int:
    return int((_agent_raw().get("concurrency") or {}).get("global_concurrency", 4) or 4)
def _research_lane_cap() -> int:
    lc = (_agent_raw().get("concurrency") or {}).get("lane_caps") or {}
    return int(lc.get("research", 2) or 2)
```
> ⚠️ `_DEEPRESEARCH_TOOL_TIMEOUT` 定义后，注册处 [:1870](../../backend/deskpet/tools/research_tools.py:1870) 的 `timeout_seconds=300.0` 改成引用该常量（单一真相，D8 预算才成立）。

**改 b — `_handle_deepresearch`（[:1711](../../backend/deskpet/tools/research_tools.py:1711)）顶层守门注入（D9/D10）**：
```python
sid = str(args.get("_session_id") or "default")
_IN_SUBAGENT = any(m in sid for m in (".sub", ".par-", ".team-", ".dr-"))
sched = None if _IN_SUBAGENT else get_subagent_scheduler()
report = await deepresearch(..., scheduler=sched, parent_sid=sid)
```
（`_session_id` 由 registry 注入 params，[registry.py:690](../../backend/deskpet/tools/registry.py:690)/[main.py:5992](../../backend/main.py:5992)，已核实。**不用** `_resolve_sid`，它不存在。）

**改 c — `task_kinds.py:62` 强制剥工具（§5 第 4 条）**：research 工具集 `("web_search","web_fetch","deepresearch","read_file")` → `("web_search","web_fetch","read_file")`。

**改 d — `main.py` lifespan**（driver 的 scheduler 构造块之后；scheduler 局部名以 driver 实现为准，挑战核实为 `service_context.register("subagent_scheduler", ...)` [main.py:2007](../../backend/main.py:2007)，故用 `service_context.get`）：
```python
_sched = service_context.get("subagent_scheduler")   # driver 已注册；未开则 None
from deskpet.tools import research_tools as _rt        # ★ _research_raw 在 research_tools，不是 main 本地
if _sched is not None and _rt._fanout_enabled():
    _rt.set_subagent_scheduler(_sched)
    logger.info("deepresearch subagent fanout ENABLED (scheduler wired)")   # ★ main.py 是 logger 不是 log（[:86]）
```
**测试** TG-5：`_fanout_enabled`/`_fanout_max_subquestions`/`_global_concurrency`/`_research_lane_cap` 真读 config（b05823b 回归：真 `[research].subagent_fanout=true` + `[agent.concurrency].lane_caps.research` 读到）；无 `.raw` stub 不抛；`_handle_deepresearch` 在 sid 含 `.par-` 时 `scheduler=None`（守门）；顶层 sid 时注入。

### WI-6 — 观测 + WS 进度（复用，几乎零新代码）

- coverage 加 `subagent_fanout` 块（WI-2 已写）+ `mode="fanout"`。
- WS 进度：`scheduler.run` 已 emit `subagent_progress`（driver 计划 WI-1.5 的 `_subagent_progress_sink` 广播）→ 桌宠前端 `SubagentProgressPanel` 自动显示 N 条 research lane 并发「忙」。**本计划无须新增前端**（复用 driver 的面板）。
- 日志锚点：`subagent_scheduled kind=research run_id=<sid>.dr-N`（scheduler 自带，真机 grep 验 N 个并发）。

### WI-8 — 所有 deepresearch 报告落「安装目录/DeepResearch/」+ index.md + README（用户新增需求）

> 用户要求：**所有 deepresearch**（含扁平 + fan-out）报告落 **deskpet 安装目录下的 `DeepResearch/`**（**不进 C 盘 `%AppData%`**），并维护 `DeepResearch/index.md` 索引所有调研，repo README 记录该 index 作用；**打包后应用同逻辑**。
> 注意：这是对 deepresearch **落盘行为的通用改动**，与 fan-out flag 无关（无论 flag 开关都生效）。

**WI-8a — `paths.deepresearch_dir()` 新函数**（[`paths.py`](../../backend/paths.py)）：
```python
def deepresearch_dir() -> Path:
    """所有 deepresearch 报告 + index.md 的根目录。解析顺序：
      1. DESKPET_DEEPRESEARCH_DIR env 覆盖（测试/power user/自定义盘）。
      2. frozen：安装根 → <install_root>/DeepResearch/（须 probe 该目录本身可写）。
      3. dev：repo 根（Path(__file__).resolve().parents[1]）→ <repo>/DeepResearch/。
      4. 兜底（安装根不可写，如 per-machine Program Files 无 admin）：
         <用户主目录>/DeskPet/DeepResearch（可见、好找）+ log.warning。
      ★ 绝不回落 %AppData%\\Roaming（user_data_dir()）——那正是用户明确不要的隐藏 C 盘位置。
    目录按需 mkdir(parents=True, exist_ok=True)。"""
```
**★B-MAJOR-1 修正**：安装根可写性**不能借用** `_portable_userdata_dir` 的 `<root>/userdata` probe（那只证明 userdata 可写，不证明能在 root 下新建 `DeepResearch/`）。新增独立 helper：
```python
def _install_root_for_deepresearch() -> Path | None:
    base = _install_dir()                       # frozen=exe 父（[:50]）；dev=None
    if base is None: return None
    root = base.parent if base.name.lower() == "backend" else base   # Tauri layout
    target = root / "DeepResearch"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".deskpet-dr-write-probe"
        probe.write_bytes(b""); probe.unlink()  # 直接 probe DeepResearch 本身可写
        return target
    except OSError:
        return None
```
**★B-BLOCKING-3 修正**：兜底**不再是** `user_data_dir()/DeepResearch`（会落 %AppData% C 盘）。改为 `Path.home()/"DeskPet"/"DeepResearch"`（可见、好找）+ `log.warning("install root not writable; DeepResearch falls back to <home>; set DESKPET_DEEPRESEARCH_DIR to override")`。
> 说明：常见安装是 Tauri NSIS **per-user**（装到可写目录），安装根可写 → 报告就落安装目录旁，满足"安装目录/DeepResearch + 不进 Roaming"。只有 per-machine Program Files 才触发 home 兜底；env 覆盖永远优先。

**WI-8b — `_save_report` 改用 `deepresearch_dir()`**（[research_tools.py:1770](../../backend/deskpet/tools/research_tools.py:1770)）：
- 把 `from paths import output_dir; base = output_dir("Research")`（[:1774-1775](../../backend/deskpet/tools/research_tools.py:1774)）改为 `from paths import deepresearch_dir; base = deepresearch_dir()`。
- 文件名沿用 `<slug>-<ts>.md`，落 `<DeepResearch>/<slug>-<ts>.md`（顶层，不再嵌 `OutPut/Research`）。
- header 元数据不变。**保存成功后调 WI-8c 更新 index。**
- 该改动**对扁平 + fan-out 两条路径同时生效**（都经 `_handle_deepresearch` → `_save_report`）。

**WI-8c — `_update_deepresearch_index(report_path, topic, report)`**（research_tools.py 新增）：
- 维护 `<DeepResearch>/index.md`：每份报告一行，**倒序**（最新在上）。表格列：日期 / 主题 / 文件(相对链接) / 来源数 / 域名数 / 模式(flat/fanout) / 子问题数。
- 首次创建时写表头 + 说明段（"本目录索引所有 deepresearch 调研报告，供后续查阅复用"）。
- **幂等去重**：按文件名 dedup（同文件不重复加行）。原子写（写 tmp → `os.replace`）。失败 try/except 不影响报告返回（落盘已成功）。
- **★B-MAJOR-3 并发安全**：两个 deepresearch tool call 并发各自"读→插→replace"会丢更新。加**进程内 `asyncio.Lock`**（模块级 `_INDEX_LOCK`）串行化 index 更新（单机单后端进程足够；deskpet 是单实例桌宠，无多进程写同一 index）。**编码强制 `encoding="utf-8"`** 读写（避开 PowerShell/中文 mojibake 坑，`feedback_powershell_chinese_files`）。
- 主题列对 `|`/换行做转义（防破坏 Markdown 表格）。
```python
_INDEX_LOCK = asyncio.Lock()
async def _update_deepresearch_index(report_path: Path, topic: str, report: ResearchReport) -> None:
    async with _INDEX_LOCK:
        base = report_path.parent
        idx = base / "index.md"
        cov = report.coverage or {}
        safe_topic = str(topic).replace("|", "/").replace("\n", " ").strip()
        row = (f"| {time.strftime('%Y-%m-%d %H:%M')} | {safe_topic} | "
               f"[{report_path.name}]({report_path.name}) | {cov.get('n_sources',0)} | "
               f"{cov.get('n_domains',0)} | {cov.get('mode','flat')} | {cov.get('n_sub_questions',0)} |")
        existing = idx.read_text(encoding="utf-8") if idx.exists() else _INDEX_HEADER
        if report_path.name in existing:   # 幂等
            return
        merged = _insert_row_after_header(existing, row)   # 表头后插首行（倒序）
        tmp = idx.with_suffix(".md.tmp")
        tmp.write_text(merged, encoding="utf-8")
        os.replace(tmp, idx)
```
> `_save_report` 是同步函数（[:1770](../../backend/deskpet/tools/research_tools.py:1770)）；index 更新改在 `_handle_deepresearch`（async，[:1753-1765](../../backend/deskpet/tools/research_tools.py:1753) 落盘块）里 `await _update_deepresearch_index(...)`，避免在同步函数里建 event loop。`_save_report` 仅负责写报告 md + 返回 path。

**WI-8d — repo README 记录 index 作用**（[`README.md`](../../README.md)）：
- 加一节（仿现有 `plans/index.md` 在 README 的记法）：说明 `DeepResearch/`（安装目录下）汇集所有 deepresearch 报告，`DeepResearch/index.md` 是总索引（倒序、可点开），方便后续查阅复用；打包应用同样在安装目录下生成。

**WI-8e — 打包应用同逻辑（★B-MAJOR-2：覆盖 NSIS+MSI 两种安装位置）**：
- 项目实际出 **NSIS + MSI**（[tauri.conf.json:71](../../tauri-app/src-tauri/tauri.conf.json:71) `targets:["nsis"]`、[release.ps1:66](../../scripts/release.ps1:66) `--bundles nsis msi`），backend bundle 路径 `dist-portable/deskpet-backend`（[tauri.conf.json:88](../../tauri-app/src-tauri/tauri.conf.json:88)）。
- `deepresearch_dir()` 的 frozen 分支（WI-8a）对**任何安装位置**都先 probe `<install_root>/DeepResearch` 可写：
  - **per-user 安装**（NSIS 默认 currentUser / 用户自选非 C 盘目录）→ 安装根可写 → 落安装目录旁 ✓（满足用户要求）。
  - **per-machine Program Files**（无 admin 写）→ probe 失败 → 落 `<home>/DeskPet/DeepResearch`（WI-8a home 兜底）+ warning ✓（不崩、不进 Roaming）。
- 运行时产物，**不入安装清单**（与 userdata 同策略，卸载不删报告）。**无须改 installer**；但若要强制"永远在安装目录旁"，可在 installer 预建可写 `<install>/DeepResearch`（可选增强，非必需）。
- 验收（TC-F6 扩展）：至少覆盖 **per-user 非 C 盘安装** 一种真机/半真机场景验证落点；Program Files 场景验 home 兜底 + warning。

**测试** TG-6：`deepresearch_dir()` 三分支（env 覆盖 / frozen mock `sys.frozen`+`sys.executable` / dev repo 根 / 只读兜底）；`_update_deepresearch_index` 首建表头 + 倒序插入 + 幂等去重 + 原子写;`_save_report` 落点改到 DeepResearch（非 OutPut/Research）回归。

### WI-7 — 测试组（详见 §10）

---

## 8. 改动文件清单

| 文件 | 改动 | WI |
|---|---|---|
| `backend/deskpet/tools/research_tools.py` | 签名加 5 参 + fanout 分叉 + `_run_subagent_fanout` + 引用重编号 + `_fanout_synthesize` + `_FANOUT_SYNTH_PROMPT` + 全局桥 + flag helper + cite 复用重构 | WI-1~6 |
| `backend/main.py` | lifespan 接 `set_subagent_scheduler`（`service_context.get` + `logger`） | WI-5d |
| `backend/deskpet/agent/task_kinds.py` | research 工具集剥掉 `deepresearch`（递归守门第 4 条） | WI-5c/§5 |
| `backend/deskpet/tools/registry.py` | （只读核实）`_session_id` 注入 params，无改动 | WI-5b |
| `backend/config.py` | 无改动（`[research]`/`[agent.concurrency]` 纯 raw-read，挑战 B 确认够健壮） | WI-5 |
| `backend/paths.py` | 新增 `deepresearch_dir()` + `_install_root_for_deepresearch()`（独立 probe DeepResearch 本身） | WI-8a |
| `README.md`（repo 根） | 记录 `DeepResearch/index.md` 索引作用 | WI-8d |
| `backend/tests/test_deepresearch_subagent_fanout.py` | 新建测试组 TG-1~5 | WI-7 |
| `backend/tests/test_deepresearch_output_dir.py` | 新建 TG-6（落盘根 + index 维护） | WI-8 |
| `testcase/<date>-deepresearch-subagent-fanout/manual-test.md` | windows-mcp 真机 E2E | §10 |

> 前端：**零改动**（复用子代理 driver 的 `SubagentProgressPanel`）。

---

## 9. 风险登记

| # | 风险 | 缓解 |
|---|---|---|
| R1 | N×全管线子调查并发打爆 relay（429/504） | scheduler 全局 cap=4 + research lane=2 背压；子跑降档（D8）；沿用现有 relay 重试链 |
| R2 | N 份全管线叠加破 300s tool 超时 | **D8 动态预算由构造保证** `waves*per_subrun+reserve ≤ TOOL_TIMEOUT`（共用 `_DEEPRESEARCH_TOOL_TIMEOUT` 常量）+ `fanout_max_subquestions` 上限 + 子跑降档；TG-2 预算断言 |
| R3 | 递归 fan-out（子代理再 fan-out）爆栈/烧钱 | §5 **四重强制守门**（顶层 sid 门 `scheduler=None` + `_depth==0` 条件 + 内层 `scheduler=None` + **强制剥 research KindProfile 的 deepresearch**） |
| R4 | 引用撞号 / 全局重编号错 → 悬空引用 | WI-3 URL 归一去重 + 映射表；cite_check 复用兜底（[:1427](../../backend/deskpet/tools/research_tools.py:1427)） |
| R5 | 单子代理失败拖垮整 run | `gather(return_exceptions=True)` 失败隔离；全失败才 no_results 兜底 |
| R6 | flag 静默失效（config.config 单例陷阱重演） | 一律 `_research_raw()` 安全兜底；TG-5 加真 config 回归（仿 b05823b） |
| R7 | BC 破坏（扁平路径被动了） | 每 WI 末跑扁平回归 diff=0；cite 复用重构后扁平路径用例全绿 |
| R8 | task_kinds research 工具集含 deepresearch（LLM 路径递归隐患） | **已修**：WI-5c 强制剥掉 + 顶层 sid 门双保险（§5）；不再"留给 driver 计划" |
| R9 | index.md 并发丢更新 / 中文编码损坏 | WI-8c `asyncio.Lock` 串行 + `encoding="utf-8"` 读写 + 主题 `|`/换行转义 |
| R10 | 经典 Program Files 安装报告落 C 盘隐藏 Roaming | WI-8a 兜底改 `<home>/DeskPet/DeepResearch` 可见目录 + warning + env 覆盖，**绝不回落 Roaming** |

---

## 10. 验收准入

### 10.1 单元（pytest，TDD 红→绿）

- TG-1：fanout 条件 4 分支 OFF → 走扁平、字节级 BC。
- TG-2：fanout ON（mock scheduler+内层）→ N 份合并、失败隔离（1 失败其余存活+错误透传）、全失败兜底（含 summary 不 TypeError）、coverage.subagent_fanout/waves、cap 截断、depth-1 断言（内层 scheduler=None/_depth=1）、**预算断言** `waves*per_subrun+RESERVE ≤ _DEEPRESEARCH_TOOL_TIMEOUT`（默认 + 极端 n=20）。
- TG-3：模块级 `_norm_url` 提取后 direct-source 去重回归；引用全局重编号（撞号去重 refmap）。
- TG-4：`_strip_footnote_definitions`/`_rewrite_local_refs` 正确；`_fanout_synthesize` + synth 失败降级含全局引用；`_finalize_report_md` 抽取后扁平 cite 字节级 BC。
- TG-5：`_fanout_enabled`/`_fanout_max_subquestions`/`_global_concurrency`/`_research_lane_cap` 真读 config（b05823b 回归）+ 无 .raw 不抛；`_handle_deepresearch` sid 含 `.par-`→scheduler=None、顶层→注入（守门）。
- TG-6（WI-8）：`deepresearch_dir()` 四分支（env / frozen mock `sys.frozen`+`sys.executable` 可写安装根 / dev repo 根 / **frozen 但 root 不可写 → home 兜底 + 不落 user_data_dir**）；`_install_root_for_deepresearch` 直接 probe DeepResearch 本身；`_update_deepresearch_index` 首建表头 + 倒序 + 幂等去重 + 原子 `os.replace` + utf-8 + 主题转义 + `asyncio.Lock` 串行（并发两次只插两行不丢）；`_save_report` 落点为 DeepResearch（非 OutPut/Research）。
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
| TC-F6 | 落盘 + 索引（WI-8，扁平/fanout 均验） | 报告落 **安装目录/DeepResearch/**（dev=repo 根，**非 C 盘 AppData**）→ `DeepResearch/index.md` 新增倒序一行可点开 → repo README 有索引说明 | 文件路径截图 + index.md 内容 |

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

---

## 13. 附录：评审修订记录

### 第 1 轮对抗挑战（2026-06-21，codex gpt-5.5 双子代理并行，read-only，读真源码）

两名挑战者各出 VERDICT: NOT-EXECUTABLE。合并去重 **7 BLOCKING + 4 MAJOR**，全部已吸收（v0.1 → v0.2）：

| ID | 来源 | 严重 | 问题 | 修订落点 |
|---|---|---|---|---|
| 1 | A | BLOCKING | `_norm_url` 是 direct-source 分支内局部函数，不能直接复用 | WI-3 前置：提为模块级再复用 |
| 2 | A | BLOCKING | depth-1 守门有漏：LLM 派 research 子代理调 deepresearch 仍会再 fan-out | §5 重写四重守门 + WI-5b 顶层 sid 门 + WI-5c 强制剥工具 |
| 3 | A | BLOCKING | 300s 预算数学不成立（deep 6 子问题最坏 360s） | D8 重写动态预算 + `fanout_max_subquestions` + TG-2 断言 |
| 4 | A | BLOCKING | WI-2 返回缺 `summary` → TypeError；`_no_results_report` 不存在 | WI-2 用 `_extract_summary` + 现成 no-passages 构造（`_no_results_template`） |
| 5 | B | BLOCKING | WI-5 main.py 接线 `log`/`_research_raw` NameError | WI-5d 用 `logger` + `from deskpet.tools import research_tools as _rt` |
| 6 | A+B | BLOCKING(B)/MAJOR(A) | `parent_sid=_resolve_sid(task_id)` 函数不存在 | D9/WI-5b 用 `args.get("_session_id")` |
| 7 | B | BLOCKING | "经典安装不进 C 盘"承诺不成立（兜底落 %AppData%） | WI-8a 兜底改 `<home>/DeskPet/DeepResearch`，绝不回落 Roaming |
| 8 | A | MAJOR | 子报告本地脚注污染统一 synth/cite | WI-4 加 `_strip_footnote_definitions`+`_rewrite_local_refs`+prompt 规则 |
| 9 | B | MAJOR | `_install_root_writable` 不能借 userdata probe | WI-8a 独立 probe `<root>/DeepResearch` 本身 |
| 10 | B | MAJOR | 打包非纯便携（NSIS+MSI），未覆盖安装位置/ACL | WI-8e 覆盖 per-user/per-machine 两类 + 验收 |
| 11 | B | MAJOR | index.md 原子 replace 不防并发丢更新 + 未定编码 | WI-8c `asyncio.Lock` + `encoding="utf-8"` + 转义 |

**两轮共同确认正确（GAP，无需改）**：keyword-only 新参数不破调用（字节级 BC 靠 TG-1 diff 证）；`scheduler.run` 用法/返回值一致；LLM 全局桥 + scheduler 实例存在（driver 已落地：[config.py:424](../../backend/config.py:424)、[main.py:2007](../../backend/main.py:2007)、SubagentProgressPanel）；dev `parents[1]`=repo 根；`[research]`/`[agent]` raw 读够健壮（不必扩 config dataclass）；README 可自然插入。

**结论（v0.2）**：11 项全修，待第 2 轮挑战验证收敛。
