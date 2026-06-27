# FactExtractor 重复抽取去重 — 修复 PLAN

> **状态**：**v0.6 — WI-1（L1 内容哈希幂等）EXECUTABLE-AS-IS（codex gpt-5.5 第 4 轮 13 项代码核实全过、0 BLOCKING/MAJOR 确认收敛）；WI-1.5/WI-2（L2a/L2b 近重复）降级「Phase 2 待设计 spike」**。4 轮对抗（2 general-purpose + 竞品对标 + codex gpt-5.5）。本 plan 仅规划，不含已执行代码——**待用户批准后执行 WI-1**。
> - 第 1 轮修：B1 并发 TOCTOU 双抽 / B2 登记撤销 safe-fail / B3 签名敲定 / replace 路径论证 / forget R5 / 双证验收。
> - 第 2 轮修：坏 JSON 撤销占位 / flag boot-log 实证 / summarizer 不短路 / 占位时间戳刷新 / T6 复现细节。
> - **第 3 轮（关键）**：子代理**实算**揪出 v0.4 的 L2a 用 **Jaccard+0.6 抓不到真测残留**（"用户的工号是A7788"vs"A7788" Jaccard=0.368《0.6；我把 Claude 的"token overlap">"Jaccard" 记错——overlap=交集/min 才对，但 overlap 对短事实有"子集误合并"硬伤）。**关键认知修正：真测 +3 本就是"重发完全相同内容"→ L1 直接跳过重抽即 +0，单 L1 已覆盖该 +3**；L2a/L2b 解的是用户未报的"换措辞"场景且有未决设计问题 → **降级为 Phase 2 spike，不进本次可执行范围**。
> - **v0.4 竞品对标（§9）**：Claude(内容寻址 ID)+Hermes(精确重复拒绝)**双重验证 L1**；mem0(向量+LLM ADD/UPDATE/NOOP)/Zep(双时态)为 L2 阶段参考。
> **创建**：2026-06-27 ｜ **作者**：Claude（Opus 4.8）
> **触发**：2026-06-27 windows-mcp 真测 IDEM-B 发现——重发**完全相同**的用户陈述，facts 表 count 仍 +3。已先修 `memory_write` 工具的时间戳键 bug（[RESULTS-TESTCASE.md](../manual-results-2026-06-27-enable-flags/RESULTS-TESTCASE.md) IDEM-B），本 plan 处理**残留的 FactExtractor 路径**。

---

## 1. 问题陈述（精确根因，含 file:line 证据）

### 1.1 现象
桌宠对话里用户**重发完全相同的一句话**（如「请记住：我的工号是 A7788，部门是平台架构组，直属领导叫张伟。」），后台 `FactExtractor` 每次都把它当新消息抽取 → facts 表累积**语义重复**的记录（真测 count +3）。

### 1.2 抽取/去重现有流程（已核实）
触发：`backend/main.py:2736-2755` `_on_message_fanout` → `asyncio.create_task(_extract_facts_bg)` → `_fact_extractor.process_message(message_id, content, role)`（仅 `config.memory.v2.facts_extract=True` 时）。

`process_message`（`facts.py:1006`，**唯一顶层入口**，bg fanout + summarizer 都走它；**无独立 `extract` 方法**——抽取逻辑内联在 process_message）→ role 白名单(`:1028`) + `min_chars`(`:1033`) 过滤 → LLM 抽取(`:1035-1038`) → `_persist_extracted`（`facts.py:1056`），对**每条抽取出的 fact**：
1. `normalize()` + `is_valid()` + `is_forgotten_recently()` 过滤（`facts.py:1071-1092`）。
2. `existing = find_active(subject, key)`（`facts.py:1093`，**按 (subject,key) 精确匹配**）。
3. `existing is None`（新 key）→ `cross_key_merge` 开则走 `_handle_cross_key`（语义召回 20+10 候选喂 LLM 判冲突，`facts.py:1168`），否则 `upsert` 直插（`facts.py:1112`）。
4. `existing is not None` → `_decide_merge`（`facts.py:1299`）：`no_op` / `merge`(原地更新) / `replace`(deactivate+insert)。其中 `_decide_merge:1304` 已有**廉价 exact-value no_op 门**（但**仅在 (subject,key) 命中时**才跑）。

### 1.3 根因（为何重发同内容仍增行）
`extract` 是 **LLM 驱动、非确定**的：同一句话两次抽取，LLM 产出的 `(category, key, value)` **略有差异**。真测实证：「工号 A7788」被抽成
- `fact / employee_id / "用户的工号是 A7788"`（第一次）
- `profile / employee_id / "A7788"`（第二次，category 与 value 都变了）

于是：
- **(a) key/category 漂移** → `find_active(subject, key)` 漏匹配（同 key 但 value 不同时仍能匹配，但 LLM 偶尔连 key 都换；且即便 key 同，`_decide_merge` 若判 `replace` 也会 deactivate+insert → count(*) +1）。
- **(b) cross_key 的 LLM 语义判冲突也非确定**（`_decide_cross_key_conflict:1260` 失败/不确定兜底 `should_insert=True`）→ 漏判 → 新插。

→ **既有去重全是 LLM 驱动、对"完全相同内容重发"没有确定性短路**。这是「遍历 + 写副作用」缺一个**确定性"已处理"判断**（对照 reviewer 五问）。

### 1.4 范围澄清
- **本 plan 只修 FactExtractor 路径**；`memory_write` 工具的时间戳键 bug 已在前序修复（内容哈希 key + find_active touch）。
- `cross_key_merge` flag 本就 ON（A 表），是 LLM 语义治理；本 plan 加的是**确定性前置去重**，与之互补（不替换）。

---

## 2. 设计目标 & 原则

- **G1（核心，本次范围）**：重发**完全相同内容** → facts 表 **count(*) 第二次 +0**（确定性，不依赖 LLM）。= 用户验收。
  - **★ 关键论证（第 3 轮确立）：单 L1（内容哈希幂等）已完整解 G1，且覆盖真测观测的 +3**。真测 +3 的来源正是"重发完全相同内容"被后台**重新抽取**（LLM 对同内容两次抽出 value/category 漂移 → find_active 漏或 `_decide_merge` 判 replace → +3）。L1 在 LLM 抽取**之前**按内容哈希短路 → **同内容根本不重抽** → 不产生漂移 → +0。故 G1 由 WI-1 单独达成，**不依赖 L2a/L2b**。
- **G2（增强，Phase 2 spike，本次不做）**：同一信息**换措辞**重述（非逐字、用户未报）→ 不累积近重复。L2a(token overlap)/L2b(embedding) 解此，但有未决设计问题（见 §10），降级待设计。
- **P1 BC**：新机制 flag 门控，OFF = 字节级现状（facts_extract 既有行为不变）。
- **P2 safe-fail**：去重判断失败/超时 → 退回现有 insert 路径，**绝不卡死、绝不丢事实**。
- **P3 不破坏既有去重**：cross_key_merge / _decide_merge / supersede 链全保留；新门只在它们**之前**做确定性短路。
- **P4 不误杀**：不同内容、真更新（用户改主意）必须照常抽取/更新。

---

## 3. 方案（分层，确定性优先）

### Layer 1 — 消息级内容哈希幂等（核心，确定性，解 G1）★

**思路**：抽取前算"归一化内容哈希"，若**最近**抽过完全相同的内容 → **整条跳过抽取**（facts 已在，不重抽）。直接消灭"重发同内容再抽一遍"的根，从源头 +0。

**实现点**：`FactExtractor`（`facts.py:955`）
- ctor 加：`self._recent_content: OrderedDict[str, float] = OrderedDict()`（LRU+TTL，bounded）+ **`self._dedup_lock = asyncio.Lock()`**（轻量锁，**与 `_persist_lock` 分开**——不串行化 LLM 抽取，只串行化 cache 临界区）。
- **`process_message`（`facts.py:1006`）内**：短路插在 **role 白名单(`:1028`) + `min_chars`(`:1033`) 之后、LLM 抽取(`:1038`) 之前**（覆盖 bg + summarizer 所有调用方）。

**★ 并发安全设计（修 B1/B2 TOCTOU）**：`_extract_facts_bg` 对每条消息 `asyncio.create_task` **并发**跑（`main.py:2755`），且 LLM 抽取在 `_persist_lock` **之外**。若不处理，快速重发两条相同内容会**都在对方登记前读到未命中 → 双抽**。故采用**「锁内判定 + 抢占登记占位」+「失败 try/finally 撤销占位」**：

```python
# —— 内容哈希幂等短路（content_dedup on + 仅 user_message，修 MAJOR-2 summarizer）——
h = None
_dedup_active = self._content_dedup and source == "user_message"   # ★ summarizer/其他 source 不进短路
if _dedup_active:
    h = _content_hash(source, content)        # 归一化(strip+折叠空白+小写)+sha1，含 source
    async with self._dedup_lock:              # ★ 命中判定 + 抢占登记 在同一临界区，原子（修 B1）
        now = time.time()
        while self._recent_content:           # 清过期（从最旧端）
            k0, t0 = next(iter(self._recent_content.items()))
            if now - t0 >= self._content_ttl_s: self._recent_content.popitem(last=False)
            else: break
        hit_t = self._recent_content.get(h)
        if hit_t is not None:                 # 命中（已抽过 或 另一并发 task 已抢占占位）
            log.info("facts_extract_skip_dup seen_ago=%.0fs", now - hit_t)   # ★ info 级，验收可抓
            return []                         # 幂等短路
        self._recent_content[h] = now         # 未命中 → 抢占登记占位（LLM 前，防并发双抽）
        self._recent_content.move_to_end(h)
        while len(self._recent_content) > self._content_cache_max:
            self._recent_content.popitem(last=False)

# —— 原 LLM extract（锁外，不串行化不同内容）——
try:
    raw = await self._extract_llm(_prompt_tmpl.format(content=content[:2000]))
except Exception as exc:
    log.warning("FactExtractor.extract LLM failed: %s", exc)
    await self._revoke_placeholder(h)         # LLM 异常 → 撤销占位，可重试（修 B2，P2 不丢事实）
    return []
facts, parse_ok = _parse_extracted_v2(raw)    # ★ 修 BLOCKING-1：区分"LLM 说空"vs"坏 JSON"
if not parse_ok:                              # 畸形/JSONDecodeError（如 relay 偶发坏响应）
    await self._revoke_placeholder(h)         # → 撤销占位，下次可重抽（不把坏响应误当"已处理"）
    return []
# parse_ok 到此：facts 可能为 [] (LLM 确说无可抽) 或 N 条 → 都算"内容已成功处理"，保留占位。
extracted = facts
... (Stage2 category override + _persist_extracted) ...
# ★ 修 MAJOR-1：成功处理完，把占位时间戳刷新为"完成时刻"(TTL 基准对齐"已处理完成") + LRU 提鲜
if h is not None:
    async with self._dedup_lock:
        if h in self._recent_content:
            self._recent_content[h] = time.time(); self._recent_content.move_to_end(h)
return persisted
```

辅助：`async def _revoke_placeholder(self, h): `（`if h is not None: async with self._dedup_lock: self._recent_content.pop(h, None)`）。

**`_parse_extracted_v2`（修 BLOCKING-1）**：现 `_parse_extracted`（`facts.py:1355`）对"LLM 说空数组"和"raw 畸形 JSONDecodeError"**都返回 `[]`**——把坏响应误当"已处理"会让一次 relay 502 把首条身份陈述吞 1h（违 P2）。改造：抽出内部 `_try_parse_array(text) -> Optional[list]`（畸形/无 `[...]`/JSONDecodeError 返 `None`），`_parse_extracted_v2` 返 `(facts_list, parse_ok: bool)`：`None→([],False)`；合法数组→`(facts, True)`。**仅 parse_ok 才保留占位；parse 失败撤销**（同 LLM 异常档）。保留旧 `_parse_extracted` 不动（其它调用方兼容），新逻辑用 v2。

- **登记/撤销/刷新三态语义**：未命中**抢占登记**（B1）｜ LLM 异常 / parse 失败 → **撤销**（B2+BLOCKING-1，可重试不丢事实）｜ parse_ok（含 0 条）→ **保留 + 完成时刻刷新**（MAJOR-1）。
- **验证锁**：T6 并发（`_extract_llm` 调用==1）+ T7 LLM 异常撤销 + **T7b 坏 JSON 撤销**（见 §5.1）。
- **正确性**：只对**完全相同归一化内容**短路；不同内容 hash 不同 → 照常抽。
- **TTL 语义**：默认 3600s（1h）。窗口内重发同句 → 跳过；窗口外重发 → 重抽一次（再被 L2 或既有 merge 兜）。
- **重启**：in-process cache 重启清空 → 重启后首次重发会重抽一次（可接受；L2/既有 merge 兜）。
  - **可选硬化（设计备选，不一定做）**：DB 背书——抽取时把 `content_hash` 落 facts 行（新增列）或独立 `fact_extraction_log(content_hash, extracted_at)` 表，重启后仍认得。**默认先做 in-process**（零 schema 变更）；若验收要求跨重启幂等再升 DB 背书。

**解决真测 +3**：重发「工号 A7788」→ 内容哈希命中 → **不再抽取** → 0 新 fact → count +0。**G1 达成**。

### Layer 2（L2a Jaccard/overlap + L2b embedding）— ⛔ **Phase 2 待设计 spike，本次不做**（见 §10）

> 第 3 轮实算证伪 L2a 的 Jaccard 设计（短事实抓不到），且 overlap coefficient 有"子集误合并"硬伤、L2b embedding 受 LLM 抽取非确定性掣肘。**L2a/L2b 解的是用户未报的"换措辞"场景（G2），且 G1 已由 L1 单独达成** → 整体降级为 Phase 2 设计 spike，§10 记录已知设计问题供将来重启。以下原始设计草图**保留作 spike 起点，非本次可执行内容**。

#### （存档）Layer 2 — 值级 embedding 近重复门

**思路**：对"同信息换措辞"的近重复（L1 的内容哈希抓不到，因文本不同），在 `_persist_extracted` 插入前加一道 embedding 近重复检测。

**实现点**：`_persist_extracted`（`facts.py:1093` 的 `existing is None` 分支内、`upsert` 之前）
- 复用现成 `self._embedder` + `_store.vector_search_in_subject`（`facts.py:1191` 已用于 cross_key）：
  ```python
  if existing is None and self._near_dup_enabled and self._embedder is not None:
      try:
          emb = await self._embedder.encode([f"{fact.key}: {fact.value}"])
          hits = await self._store.vector_search_in_subject(query_embedding=emb[0], subject=fact.subject, limit=3)
          top = hits[0] if hits else None
          # vector_search_in_subject 返回每行带 `_score`=cosine（facts.py:880），按 _score 降序
          if top and float(top.get("_score", 0.0)) >= self._near_dup_threshold:   # 如 0.93
              # 近重复 → 视为已存在：no_op（或 update_value touch），不新插
              log.debug("FactExtractor: near-dup skip (score=%.3f vs id=%s)", float(top["_score"]), top["id"])
              persisted.append({"id": top["id"], "action": "near_dup_noop"})
              continue
      except Exception as exc:
          log.debug("near-dup check failed, fall through to insert: %s", exc)
  ```
- **阈值**：`near_dup_threshold` 默认 0.93（保守高阈，宁可漏去重也不误杀不同事实；可配）。
- **safe-fail**：embedder/检索失败 → 不短路、走原 insert（P2）。
- **依赖**：`vector_search_in_subject` 返回结构需含 `score`（待核实其真实返回字段；若无 score 需取 distance 换算）。

> **L2 是否本轮做**：G1（L1）已满足用户验收（重发相同 +0）。L2 解的是"换措辞重述"——属增强，可作 **WI-2 独立后置**，先 ship L1。

### Layer 3 — （决策：不做）约束 `_decide_merge` 的 replace 增行
`_decide_merge` 返回 `replace`（`facts.py:1128`）会 deactivate+insert（count(*) +1，active 数不变）。这是 mem0/Zep 软失效语义（保历史），**非 bug**。

**为何 L1 已足以覆盖 replace 路径（B2 论证，必读）**：replace 路径只在"第二次抽取产出了 value 漂移的 fact"时才走到。但 **L1 的短路发生在 LLM 抽取（`:1038`）之前**——重发的是**完全相同的输入字节**，内容哈希命中即 `return []`，**根本不进 LLM 抽取** → 不存在"第二次的 value 漂移" → 永远走不到 `_decide_merge` 的 replace 分支。即：L1 从"输入侧"杜绝，replace 是"输出侧"现象，输入被短路则输出不发生。故 **L3 不做**，不破坏既有历史链语义。（这条因果是 L1 单独足以解 G1 的关键，不补则会被质疑 replace 残留。）

---

## 4. 工作项（WI）

### WI-1（核心，本轮做）— Layer 1 消息级内容哈希幂等

**敲定签名（B3，执行直接照抄）**：

1. **`config.py`**：
   - `MemoryV2Config`（`:178`）加 `extract_content_dedup: bool = True`（**测试阶段出厂点亮**；与 facts_extract 等同档）。
   - `MemoryV2FactsConfig`（`:155`）加 `content_dedup_ttl_s: int = 3600` + `content_dedup_cache_max: int = 256`（TTL/容量是调参，归 facts 子表）。
   - `_MIGRATABLE_SECTIONS` 的 `("memory","v2")` + `("memory","v2","facts")` **已在 allow-list**（无需加）。
2. **`facts.py`**：
   - 新增模块级 `import hashlib` + `def _content_hash(source: str, text: str) -> str`：`norm = " ".join(text.strip().split()).lower(); return hashlib.sha1((source+"\x00"+norm).encode()).hexdigest()`。
   - `FactExtractor.__init__`（`:967`，现签名末尾 `goal_facts` 后）加 **3 个 kw 参数，默认值保 BC**：
     `content_dedup: bool = False, content_ttl_s: int = 3600, content_cache_max: int = 256`，
     ctor 体加 `self._content_dedup = bool(content_dedup); self._content_ttl_s = int(content_ttl_s); self._content_cache_max = int(content_cache_max); self._recent_content: "OrderedDict[str, float]" = OrderedDict(); self._dedup_lock = asyncio.Lock()`（`from collections import OrderedDict` 顶部加）。
   - `process_message`（`:1006`）按 §3 Layer1 代码草图插短路 + 抢占登记 + 失败撤销。
3. **`main.py`**（`_FactExtractor(...)` 构造，`:1427`；该处已有 `_v2_cfg = config.memory.v2`，`:1411`）：加 3 个传参
   `content_dedup=_v2_cfg.extract_content_dedup, content_ttl_s=_v2_cfg.facts.content_dedup_ttl_s, content_cache_max=_v2_cfg.facts.content_dedup_cache_max`。
   - **★ boot-log 实证（修 BLOCKING-2，防 knowledge_enabled 同款静默漏传）**：把 `content_dedup=_v2_cfg.extract_content_dedup` 追加进既有 `p4_fact_extractor_ready` 这条 `logger.info`（`main.py:1436-1441`，现含 min_chars/cross_key_merge/goal_facts）的 kwargs → boot 日志可证 flag 真传进去。三处（config True / ctor 默认 False / main.py 注入）任一没接对，单测因 ctor 直传 True 仍绿但真机白测，故必须 boot 实证。

- **BC**：ctor 默认 `content_dedup=False` → 不传时（含所有现存测试/调用方）行为字节级不变；仅 main.py 注入 `True` 时启用。**flag/BC 性质**：这是**有意行为变更**（重复内容跳过抽取），非字节 BC——按 WI-0.0 测试阶段口径标注（出厂 ON，可单条关回退）。
- **forget 交互裁决（修 MAJOR）**：见 §6 R5。
- **验收**：见 §5。

### WI-1.5 ⛔ **Phase 2 待设计 spike，本次不做** — Layer 2a token 重叠确定性去重
> 第 3 轮实算证伪：Jaccard+0.6 抓不到真测残留；改 overlap coefficient 又有子集误合并。降级 §10。以下草图作 spike 起点。
> 来源：Claude memory 的写时 Jaccard 去重（token 重叠 >60% → supersede）。确定性、无 LLM/embedding，比 L2b 廉价且不受 LLM 抖动影响；解真测残留（"用户的工号是A7788"vs"A7788"词面高度重叠）。
- **改 `facts.py` `_persist_extracted`**：对**每条** fact（采纳 mem0"无条件查重"——放在 `find_active(subject,key)` **之前**，命中即 no_op，省后续 LLM）：
  ```python
  def _jaccard(a: str, b: str) -> float:
      sa, sb = set(_norm_tokens(a)), set(_norm_tokens(b))   # 归一化分词（中文按字/词，英文按词）
      if not sa or not sb: return 0.0
      return len(sa & sb) / len(sa | sb)
  # 在 _persist_extracted 循环内、find_active 之前：
  if self._jaccard_dedup:
      recent = await self._store.list_active(subject=fact.subject, limit=50)
      for r in recent:
          if _jaccard(fact.value, str(r.get("value",""))) >= self._jaccard_threshold:   # 默认 0.6
              log.debug("facts_jaccard_dedup score>=%.2f vs id=%s", self._jaccard_threshold, r["id"])
              # 命中：no_op（已有等价事实）；可选 update_value touch 更新 confidence
              persisted.append({"id": r["id"], "action": "jaccard_noop"})
              break
      else:
          ... 进入原 find_active / cross_key / insert 流程 ...
  ```
- **中文分词注意**：`_norm_tokens` 中文宜按**字 + bigram**（纯单字 Jaccard 噪声大），英文按空格词；阈值 0.6 需对中文调（真测校准）。
- **flag（Phase 2 only，本次不建）**：`[memory.v2].extract_jaccard_dedup` + `[memory.v2.facts].jaccard_threshold`。
- **safe-fail**：`list_active`/分词失败 → 不拦截、走原流程。
- **验收（未来 spike 草案，本次不跑）**：T10 草案——但 §10 已证 Jaccard/overlap 对短事实不可靠，spike 重启时须先按 §10 重定度量再写验收。

### WI-2 ⛔ **Phase 2 待设计 spike，本次不做** — Layer 2b embedding 近重复门
- ✅ 已核实 `vector_search_in_subject` 返回每行带 `_score`=cosine（`facts.py:880`）。
- `_persist_extracted` 的 `existing is None` 分支（`facts.py:1096`）插近重复门，**放在 `_handle_cross_key` 块之前**（命中即 no_op `continue`，省一次 cross_key LLM 调用，MINOR）+ flag `extract_near_dup` + threshold 配置（默认 0.93）。
- safe-fail：embedder/检索失败走原 insert（不短路、不丢事实）。

### WI-3 — 测试 + 真机验收（见 §5）

---

## 5. 验收（HARD）

### 5.1 单测（`backend/tests/`）
- **`test_factextractor_content_dedup.py`**（新建）：
  - T1：同一 content 调 `extract` 两次（mock LLM 每次返回**不同** ExtractedFact 模拟 LLM 抖动）→ 第二次因内容哈希命中 **return [] 不抽**（断言 `_extract_llm` mock 第二次**未被调用** + persisted 为空）。
  - T2：不同 content 两次 → 都正常抽取（不误短路）。
  - T3：TTL 过期后同 content → 重新抽取（mock 时间推进）。
  - T4：flag OFF（`content_dedup=False`）→ 无幂等短路、行为字节级等同现状。
  - T5：cache 超 max → LRU 淘汰最早。
  - **T6（★ 并发回归，修 B1 的锁，修 MAJOR-3 复现）**：mock `_extract_llm = async def fn(...): await asyncio.sleep(0.01); return <raw>`（制造交错让出点），`asyncio.gather(process_message(same)×2)` → 断言 `_extract_llm` mock **总调用 == 1**。**配负向 sanity**：临时把 `_dedup_lock` 换成 `contextlib.nullcontext()`（不加锁变体）跑同 harness → 断言会复现 **==2**，证明 ==1 是锁的功劳而非协程顺序巧合（或用 call-order spy 断言"登记在 LLM 调用前"更确定）。
  - **T7（修 B2 撤销）**：mock `_extract_llm` 首次抛异常 → 断言占位被撤销（第二次同内容**会重抽**，mock 第二次被调用）。
  - **T7b（★ 修 BLOCKING-1 坏 JSON 撤销）**：mock `_extract_llm` 返回**畸形串**（非 JSON 数组）→ 断言 `parse_ok=False` 路径撤销占位、第二次同内容**会重抽**（不把坏响应误当"已处理"吞 1h）。
  - **T8（forget 交互，R5）**：`clear_content_cache()` 调用后同内容会重抽（验证自愈钩可用）。
  - **T9（summarizer 不短路，修 MAJOR-2）**：`source="summarizer"` 同文本两次 → 都正常抽（断言 `_extract_llm` 调用 2 次，L1 不短路 summarizer）。
- **`test_memory_g4_flag_matrix.py` / `test_memory_v2_config.py`**：扩 flag 默认值断言（测试阶段 True）。
- **回归**：`pytest tests/test_facts*.py tests/test_memory*.py` 全绿（不破坏 cross_key/merge/supersede）。

### 5.2 真机 windows-mcp（按 `~/.claude/knowledge-base/windows-mcp-e2e.md` 纪律）
- 复用 `plans/manual-results-2026-06-27-enable-flags/launch-dev.ps1`（源码非 frozen）。
- **★ boot-log 前置（修 BLOCKING-2，不满足则后续作废）**：boot 日志必须出现 `p4_fact_extractor_ready ... content_dedup=True`（证明 flag 三处接对、真传进 FactExtractor）；若为 `content_dedup=False` 或缺该字段 → 接线未通，停下修 main.py 注入，不进 C0/C1/C2。
- baseline `select count(*) from facts` = C0。
- 发一条**全新**身份陈述 → 等抽取 → C1。
- **重发完全相同** → 等抽取 → C2。
- **★ 双证防假绿（修 MAJOR）**——三条同时成立才算 PASS：
  1. **C1 > C0**（证明第一次真抽过、链路活——否则 C2==C1 可能是 relay 挂了根本没抽的假绿）。
  2. **C2 == C1**（第二次不增）。
  3. 第二次回合 log **必须出现** `facts_extract_skip_dup`（**info 级**，§3 草图已定 info）**且不出现** 第二次的 LLM 抽取出站日志（证明是 hash 短路、而非链路死）。
- ⚠️ relay 健康前提（json_schema 502 恢复，主聊天 200）；若 C1==C0（第一次没抽出）→ 本验收 env-limited，待 relay 恢复重跑（不可在没抽过的情况下宣称 C2==C1 通过）。

### 5.3 ★ 一票否决
- 重发相同内容 **C1>C0 且 C2==C1 且第二次有 `facts_extract_skip_dup` info 日志**（G1 双证，§5.2）。
- **并发**：T6 并发两条同内容 `_extract_llm` 总调用 == 1（无 TOCTOU 双抽）。
- **safe-fail**：T7 LLM 失败撤销占位、可重试（不丢事实）。
- flag OFF（`content_dedup=False`）字节级 BC。
- 不同内容/真更新不被误短路。

---

## 6. flag / BC / 风险

- **flag**：`[memory.v2].extract_content_dedup`（WI-1，本次，完成即默认 True，OFF=BC）。`extract_near_dup`/`extract_jaccard_dedup`（WI-2/1.5）= **Phase 2 only，本次不建**。
- **BC**：OFF 时 FactExtractor 行为字节级不变（cache 不构造、不短路）。
- **风险**：
  - R1 in-process cache 重启丢失 → 重启后首次重发会重抽一次。**缓解**：可接受（既有 merge/L2 兜）；或升 DB 背书（设计备选）。
  - R2 内容哈希过严（标点/全半角差异 → 不同 hash → 漏短路）。**缓解**：归一化要足够（strip+折叠空白+小写+全角转半角？需定）。
  - R3 误短路（真想更新但内容碰巧字节相同）→ 用户重发同句通常就是想确认"记住了"，跳过抽取无害（事实已在）。低风险。
  - R4 L2 阈值过低误杀不同事实。**缓解**：高阈 0.93 + 仅 no_op 不删原行。
  - **R5（forget 交互，修 MAJOR — 显式裁决）**：用户说 X→抽取+登记 hash→`memory_forget` 删掉 X→**TTL 窗口(1h)内**再说完全相同的 X 想重记→cache 命中→`return []`→**记不上**。
    - **裁决：声明可接受 + 自愈**。理由：① 该场景（forget 后 1h 内逐字重发同句重记）罕见；② TTL 过期(默认 1h)后自动可重记；③ 进程重启亦清 cache。
    - **配套**：`FactExtractor` 暴露 `clear_content_cache()` 公有方法（`async with self._dedup_lock: self._recent_content.clear()`）。**最小改动先不接线**（forget 工具在 `memory_tools.py` 只持有 `_facts_store` 不持 extractor）；若后续验收要求"forget 后立即可重记"，再在 `bind()` 注入 extractor 句柄 + forget 成功后调 `clear_content_cache()`。
    - **T8**：测 `clear_content_cache()` 调用后同内容会重抽（验证自愈钩可用）；并文档化"未接线时 forget→1h 内重发同句不重记"为已知可接受行为。
- **回退**：任一 flag 关单条回退。

---

## 7. 待核实/待定
- ✅ **已 close**：① 入口 = `process_message`(`facts.py:1006`)，**无独立 extract 方法**，短路放 role/min_chars 后、LLM 前（覆盖 bg+summarizer）。② `vector_search_in_subject` 返回每行带 **`_score`**=cosine（`facts.py:880`），WI-2 用 `_score` 比阈。⑤ 短路放在 role 过滤(`:1028`)之后，非 user/assistant/summarizer 早 `return []` 不进短路。
- ✅ **第 2 轮已 close**：⑦ parse 成功空 vs 坏 JSON 区分（`_parse_extracted_v2` + parse_ok，坏 JSON 撤销占位，修 BLOCKING-1）。⑧ flag boot-log 实证（`p4_fact_extractor_ready content_dedup=` + 验收前置，修 BLOCKING-2）。⑨ summarizer 不进短路（`source=="user_message"` 守卫，修 MAJOR-2）。⑩ 占位时间戳成功后刷新结束时刻（修 MAJOR-1）。⑪ T6 加交错 mock + 负向 sanity（修 MAJOR-3）。
- ✅ **已定**：flag 名 `extract_content_dedup`（v2 表）+ `content_dedup_ttl_s`/`content_dedup_cache_max`（facts 子表）；归一化 = strip+折叠空白+小写（不去前缀，全角→半角暂不做，真测发现再加）；cache key 含 source。
- ⏳ **执行时核实（非阻塞）**：`main.py:1411/1427` 的 `_v2_cfg` 变量名与 `p4_fact_extractor_ready` 行号（read 后照改）；`_parse_extracted` 现有内部结构（抽 `_try_parse_array` 时别破坏既有调用方）。

---

## 8. 执行序
**本次只做 WI-1**：WI-1（L1 内容哈希幂等）→ 单测（T1-T9）→ 真机双证验收 G1 → ship。
**Phase 2（不在本次范围，待 §10 设计 spike 解决后另启）**：WI-1.5（L2a token-overlap）/ WI-2（L2b embedding）。

---

## 9. 竞品对标 / prior art（2026-06-27 调研，回应"别闭门造车"）

> facts.py 自己写了"mem0-style merge"/"Zep 式软失效"，但本 plan 初稿（L1/L2）是顺着代码库现有语义 + 第一性原理设计的，**没查成熟系统**。补查 Claude / mem0 / Zep / Hermes / OpenClaw 后，结论是**本 plan 方向被验证、且有两处该吸收的改进**。

| 系统 | 重复事实去重机制 | 对本 plan 的启示 |
|---|---|---|
| **Claude memory**（[platform docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)） | ① **内容寻址 ID = SHA-256(session+role+content) → 重复摄入幂等**；② **写时 token overlap >60% → supersede 旧条**（⚠️ 是 overlap coefficient=交集/min，**非 Jaccard**——第 3 轮实算纠错）；③ 每 10 次抽取/>80 条 Haiku 批量合并 | **①= 我的 L1** 被工业方案**直接验证** ✓（核心保留）；② 第 3 轮实算发现：对 DeskPet 的**短结构化事实**（"A7788"），overlap=1.0 会"子集误合并"不同事实 → 不能直接照搬，**降级 Phase 2 spike**；③ 周期合并 = 未来兜底 |
| **mem0**（[arxiv 2504.19413](https://arxiv.org/html/2504.19413v1) / [docs](https://docs.mem0.ai/core-concepts/memory-operations/add)） | 抽取候选 fact → **对每条 fact 用向量相似度检索既有记忆** → LLM 判 **ADD / UPDATE / DELETE / NOOP**（已存在则 NOOP） | **关键教训：语义查重对每条 fact 无条件做**。DeskPet 现状只在 `find_active(subject,key)` **漏匹配时**才走 cross_key 语义；mem0 是**每条都查**。→ L2a/L2b 应**对每条抽取 fact 跑**（不只 existing is None 分支），从结构上堵 key 漂移 |
| **Zep / Graphiti**（[arxiv 2501.13956](https://arxiv.org/abs/2501.13956)） | 实体解析 + 边去重（限同实体对）+ **双时态 valid_at/invalid_at**（关旧边开新边，保历史） | DeskPet 已有 supersede 链（=双时态软失效），**无需改**；印证"保历史不硬删"方向对 |
| **Hermes Agent**（[NousResearch docs](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/memory.md)） | MEMORY.md/USER.md 双文件；**精确重复条目自动拒绝**（防重复提交注入）；高级去重委托 mem0 | **精确重复拒绝 = 我的 L1** 再次被验证 ✓；且点出 L1 还有**安全价值**（防重复提交污染/注入），非仅省存储 |
| **OpenClaw**（[docs](https://openclawlab.com/en/docs/agent/memory/)） | 文件级 MEMORY.md，agent 自管去重 | 文件级方案不直接适用 DeskPet 的 SQLite facts 表；参考价值低 |

### 据 prior art 对 plan 的结论（第 3 轮修正后）
- **L1（内容哈希幂等）= 本次执行核心**：Claude 内容寻址 ID + Hermes 精确重复拒绝**双重验证**，确定性、廉价，**单独解 G1 + 覆盖真测 +3**。保留为 WI-1。
- **L2a/L2b 降级 Phase 2 spike**：第 3 轮实算证伪 L2a 的 token-overlap 对短结构化事实不可靠（见 §10）；mem0 的 embedding+LLM（L2b）受 FactExtractor LLM 非确定性掣肘。两者解的是用户未报的"换措辞"（G2），**本次不做**。

---

## 10. Phase 2 spike — L2 近重复去重 待设计 open questions（第 3 轮归档，将来重启用）

> 本节记录 L2a/L2b 为何不能直接执行、重启时要先解决什么。**不是本次可执行内容。**

1. **token-overlap 的度量选择（实算结论）**：第 3 轮对真测残留实算（字+bigram 集合）：

   | 残留对 | Jaccard(∩/∪) | overlap(∩/min) |
   |---|---|---|
   | "用户的工号是 A7788" vs "A7788" | 0.368 | 1.000 |
   | "我的部门是平台架构组" vs "平台架构组" | 0.474 | 1.000 |
   | "直属领导叫张伟" vs "张伟" | 0.231 | 1.000 |

   - **Jaccard+0.6 全抓不到**（短串 vs 长句并集被撑大）→ Jaccard 方案废。
   - **overlap coefficient(交集/min) 全 =1.0** 能抓，但**子集误合并硬伤**："张伟"(短) ⊂ 任何含"张伟"的 value → overlap=1.0 → 会把"张伟"(用户名?) 与"张伟是我领导"误判等价。任何阈值都挡不住"短串是长串子集"。
2. **核心未决**：短结构化事实（"A7788"/"张伟"）的近重复判定，纯 token 集合度量**做不到既抓真重复又不误合并不同事实**。需要：① **方向性 + 语义**（新 value 是旧 value 的更详版 → UPDATE 取详版，对齐 mem0 UPDATE；反向 → NOOP）；② 或 **key 感知 + embedding**（同 key 内用 embedding 余弦 + LLM 仲裁，对齐 mem0 的 per-fact 向量检索 + ADD/UPDATE/DELETE/NOOP）。
3. **更深的根**：真测 +3 的结构性原因是 **FactExtractor 的 LLM 抽取本身非确定**（同内容两次抽出 value/category 漂移）。L1 从"输入侧"短路绕开了它；但若要解"换措辞"（G2），本质要么**降低抽取非确定性**（更严 prompt / 结构化 schema 固定 key），要么**引入 mem0 式 per-fact 语义仲裁**。这是一个独立的中等工程，需单独 spike + 真测校准中文阈值。
4. **重启 spike 时的 checklist**（届时补 main.py 注入 + boot-log + 中文分词 `_norm_tokens` 新建 + 每-fact 性能：`recent` 提到 fact 循环外按 subject 查一次复用 + 整块 try/except safe-fail + 误合并单测）。
