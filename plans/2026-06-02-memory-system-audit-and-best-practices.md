# DeskPet 记忆系统审计 + 业界最佳实践调研（2026-06-02）

> 探索模式产出（不改代码）。结论交叉验证：**我自己的代码审计** + **silent-failure-hunter 子代理**（11 发现）+ **best-practice 调研子代理**（6 主题，有引用）。
> 每条致命结论都标注「我已亲自核验 / agent 报告未独立核验」，并对 agent 的高估/误报做了校准。

---

## 0. TL;DR（先看这个）

**好消息——DeskPet 没踩业界"头号经典坑"**：
- ✅ cosine 计算**正确**：`facts.py:571` / `chunker.py:225` 显式 `np.dot(q,v)/(‖q‖·‖v‖)`，即使向量没预归一化也不退化。业界 RAG 反模式 #1（"未归一化→cosine 退化"）**DeskPet 不存在**。
- ✅ messages 主检索走 **FTS5 trigram**（`retriever.py`）→ CJK/子串鲁棒。F5 的裸 `LIKE '%整串%'` 只在 v2 的 facts/workspace 层（已修）。
- ✅ F5 两层已修 + 33 条严测护栏。

**真正的致命类不是"算错"，是"静默丢失 + 无自动兜底"**：
1. **🔴 无自动 backfill 安全网**（我已核验）：`backfill_missing()` 只在手动脚本/测试里调，**lifespan 启动时根本不调**；而 `main.py:810` 注释谎称 "embeddings backfill automatically"。后果：任何 embedding 缺口（worker 丢最后一批 / encode 失败 / 子进程崩）**永久且无声**，桌宠用户永远不会手动跑 `scripts/backfill_vectors.py`。
2. **🔴 检索降级链全程无告警**（hunter + 研究互证）：dense/FTS 路失效时只 `log.debug` 悄悄退化，"上线 90 分一个季度掉到 60 分"无人察觉。

**最大产品问题（我已核验，非 bug 是配置决策）**：
- **memory-v2 几乎全 OFF**（`config.toml [memory.v2]`）：`facts_extract / enhanced_retriever / rerank / chunking / query_rewrite / reflection` 全 false，只 `workspace_memory=true`。
- 含义：用户实际拿到的"长期记忆" = **message 级 RRF 召回 + 会话摘要**，**没有**结构化语义事实、没有 cross-encoder 重排、没有反思巩固。我刚做的 G1-G6 facts 严测验证的代码路径**生产里基本不跑**。

**该先做的 4 件事**（详见 §6）：① lifespan 加自动 backfill 兜底（半天，极高收益）② 检索 silent-failure 告警（低，高）③ 决定 v2 flag 是否上线，尤其 `facts_extract` + 写入端冲突消解（中，高）④ 建 30-50 题本地 MemEval 基线，戳破"FTS 字面命中虚高"（1-2 天，极高）。

---

## 1. 生产现实（config + wiring 核验）

`config.toml` 实际启用状态：

| 层 | 状态 | 说明（核验来源） |
|---|---|---|
| **L1 文件记忆**（MEMORY.md/USER.md） | ✅ ON | size cap + salience 驱逐，有界（`file_memory.py:251`） |
| **L2 session DB + FTS5** | ✅ ON | WAL + trigram tokenizer，子串/CJK 鲁棒 |
| **L3 向量**（sqlite-vec + BGE-M3 int8） | ✅ ON | **异步 worker** 每 2s/8 行 flush；daily salience decay λ=0.02 |
| **4 路 RRF** | ✅ ON | vec .5 / fts .3 / recency .15 / salience .05 |
| **summarizer 归档** | ✅ ON | 旧 messages 总结后 DELETE，summary 重嵌入向量库（**仅当传了 vector_worker**） |
| `workspace_memory`（code 工作记忆） | ✅ ON | F4 |
| `facts_extract`（事实抽取） | ❌ OFF | → facts 表基本空，**memory_search 生产无数据可搜**（除非 LLM 主动调 memory_write） |
| `enhanced_retriever`（facts 进 RRF） | ❌ OFF | 依赖 facts_extract |
| `rerank`（cross-encoder 重排） | ❌ OFF | |
| `chunking`（长消息切块） | ❌ OFF | 长消息 → 单个 1024 维向量 → 语义稀释 |
| `query_rewrite` | ❌ OFF | |
| `reflection` / `cross_key_merge` / `memory_forget` / `entity_path` / `episodic_to_semantic` | ❌ OFF | |

**memory 工具现实**（`main.py:844/1123-1160` 核验）：`_facts_store` 带 embedder **无条件创建**，memory_write/search/read **已绑定可用**（F3 解耦了 forget flag），facts 表首次调用时 lazy 建。但**自动抽取关着** → facts 只在 LLM 主动调 `memory_write` 时才有数据。

---

## 2. 致命 / 高危 bug（交叉验证 + 我的严重度校准）

> 🔬 = 我已亲自读码核验；🤖 = silent-failure-hunter 报告，我未独立核验（已标注合理性）。

### 🔴 FATAL-A：无自动 backfill 安全网 → embedding 缺口永久且无声 🔬
- **核验**：全后端 grep，`backfill_missing()` 仅出现在 `scripts/backfill_vectors.py`、`scripts/eval_gate.py`、tests。**lifespan/main.py 启动路径不调**。`main.py:810` 注释 "embeddings backfill automatically" **是错的**——只有 live enqueue，无缺口回填。
- **触发口**（多个汇入同一致命后果）：
  - `vector_worker.py:155-158` `_wait_for_drain` TOCTOU：`get()` 取出最后一批但 `_flush()` 未完成时 `queue.empty()` 已 True → stop 把这批 cancel 掉（hunter FATAL-2，我核验机制属实）。
  - `_flush` encode 失败 → `_failed++; return`，messages.embedding 留 NULL（hunter HIGH-4）。
  - embedder 子进程崩 → 该批永久 NULL。
- **后果**：这些 NULL 行**没有任何自动回填**，向量召回永久漏掉它们，用户感知"刚说的话下次不记得"，零日志。
- **修复方向（便宜）**：lifespan worker 起来后 `asyncio.create_task(vw.backfill_missing())`；并定期（idle/每日 decay 时）补跑。把 `main.py:810` 的虚假注释改对。

### 🔴 FATAL-B：检索降级链全程无告警 🤖（研究 #3 强互证）
- hunter #6 `enhanced_retriever.py:260-269`：facts 向量空 → 静默降级 LIKE，**连 log.debug 都没有**；LIKE 也空 → `except: return []` 仅 debug。
- hunter #8 `retriever.py:421-423`：FTS 路 OperationalError 仅 `log.debug` → 返 `[]`，`_safe_call` 分不清"失败"还是"真没匹配"。
- hunter #4 `vector_worker.py:288-337`：`_failed` 计数无人消费、health check 不暴露。
- **研究报告 #3 独立印证**："坏 chunk size 不抛异常只悄悄改 recall 分布；缺失 BM25 层不报错只悄悄漏点名查询……上线 90 分一季度掉到 60 分。"
- **修复方向**：降级点一律 `log.warning` + 计数 metric；`stats()` 暴露 `failed_rate`，超阈值 `log.error`。

### 🟠 HIGH-C：embedder 子进程崩溃在持锁期间重启 → 冻结整个嵌入系统 🤖（合理，未独立核验）
- hunter #5 `embedder.py:534-537`：子进程崩后下次 `encode` 在 `self._lock` 持有期间 `_spawn_subprocess_worker()`，等 BGE-M3 加载（10-120s），其间所有 encode 挂起，VectorWorker `_run_loop` 阻塞 → 队列堆满 → 新消息被 drop。
- **建议**：独立核验后，把重启移到锁外 / 单独 health-check 任务重启。

### 🟡 MEDIUM-D：manager.recall 对 CancelledError 无防护 🔬（**我把 hunter 的 FATAL-1 下调**）
- `manager.py:152-156` `gather(return_exceptions=True)` 后逐个 `.result()`：若子 task 异常完成则 `.result()` 重抛。docstring 称 "never raises"。
- **我的校准**：正常路径下 `_safe_l*` 吞净 `Exception` → `.result()` 返值不抛。CancelledError "穿透" 其实是**正确的取消语义**（取消本就该传播）。这是"never-raises 契约对 BaseException 不严密 + 依赖 _safe_l* 密封"的**防御性问题**，**非 FATAL 静默丢数据**。值得加固但别恐慌。

### 🟡 MEDIUM-E：其余（hunter，flag 多为 OFF 故 blast radius 小）
- `facts.py:240-264` embedding 写失败仅 log.debug → fact 向量永久 NULL（facts 层 + **facts 无 backfill**，比 messages 更糟；但 facts_extract OFF 故当前影响小）。
- `facts.py:931-937` 旧库 `is_forgotten_recently` 抛错被吞 → "忘记"命令静默失效（memory_forget OFF）。
- `summarizer.py:467-469` / `session_db.py:545,730` `messages_vec` 删除失败 `except: pass` → 孤儿向量残留，KNN 缓慢膨胀（`meta is None` 已跳过，功能不错但表不一致）。

---

## 3. DeskPet 做对了什么（校准，避免误伤好代码）

- **cosine 正确**：显式 norm 除法，业界头号坑没踩（研究报告把它列"头号嫌疑"，但 DeskPet 此处无 bug）。
- **FTS5 trigram**：messages 主路对 CJK/子串鲁棒；F5 只在 v2 facts/workspace 的裸 LIKE，已修。
- **保留有界**：summarizer prune 旧 messages + summary 重嵌（避免"归档即失忆"，代码注释明确防了这点）；L1 salience 驱逐。
- **Strangler-Fig flag 默认 OFF**：字节级契约保护，回退安全（代价是高级功能默认暗装——见 §4 取舍）。
- **F5 修复 + 33 严测**：facts/workspace 召回缺陷已堵 + 回归护栏。

---

## 4. 优化方向（我的审计 + 研究合并）

### 4.1 决策点：v2 flag 要不要上线？（最大杠杆）
当前"暗装"的 facts_extract / enhanced_retriever / reflection 是 DeskPet 长期记忆的核心卖点，却全关。**取舍**：
- 开 `facts_extract` → 有结构化语义记忆，但需配套**写入端冲突消解**（见 4.3），否则矛盾事实累积污染（业界 #5 反模式）。
- 建议：先建评测基线（§6 ④），再灰度开 `facts_extract` + 冲突消解，用基线量化收益，别盲开。

### 4.2 性能：facts Python 暴力余弦 → sqlite-vec（研究 #5）
- facts 召回是 Python 手写 O(N) 余弦（每行每查询重算 norm）。我实测 3.5ms@300 → 线性外推 ~100ms+@1 万。messages 已用 sqlite-vec（C），facts 没有，不一致。
- **不要上 ANN**（sqlite-vec 作者实测：10 万条以下暴力就够，桌宠到不了）。该做的是 facts 也换 sqlite-vec C 暴力（快一个量级 + int8/二值量化 + 顺带统一）。

### 4.3 写入端冲突消解（研究 #4，写入端最高性价比）
- 写新事实前向量取 top-3~5 相似旧事实 → 一次 LLM 判 **ADD/UPDATE/DELETE/NOOP**（Mem0 模式）；矛盾用 **Zep 式软失效时间戳**而非硬删（保留"你以前不是养狗吗"的回忆力）。
- 解决"搬家/改宠物名"后新旧打架。DeskPet 已有 `cross_key_merge`/`forget` flag，缺的是默认开 + UPDATE 决策。

### 4.4 召回质量微调（研究 #6，低复杂度）
- RRF 当前把 recency/salience 当**独立通道**塞进 4 路融合，可能稀释语义召回。业界更稳：**RRF 只融 dense+FTS 两路真召回器，recency/salience 作融合后 re-score 因子**（Generative Agents 的 `relevance+recency+importance` 加权）。

### 4.5 桌宠杀手锏：持久化 persona block（研究 #8）
- 借 Letta human/persona block：一个小的、agent 可自编辑的 in-context block 存"主人核心事实 + 桌宠性格"，**固定注入 prompt 不走检索**（检索不稳会让人设漂移）。比把人设丢向量库靠谱。

### 4.6 reflection 放 idle/sleep-time（研究 #10）
- 桌宠天然大量 idle，正是做记忆巩固窗口，不占用户等待；提炼的洞见写回 persona block。

---

## 5. 业界最佳实践调研摘要（子代理产出，浓缩 + 关键来源）

> 完整版含全部引用见子代理报告。以下为对 DeskPet 规模（单用户、数千-数万条）的提炼。**注**：报告中个别 2026 arXiv 编号/benchmark 数字为 agent 网络抓取，引用前建议 spot-check；核心方法论结论是业界长期共识，稳。

- **分层**：episodic / semantic / procedural 三分法已收敛。Letta=OS 式虚拟内存 + sleep-time 巩固；Mem0=vector+graph+kv 混合；Zep/Graphiti=时序知识图谱 + 双时间软失效；Generative Agents=`recency+importance+relevance`。
- **检索**：hybrid（BM25+dense）用 **RRF（基于排名）优于加权分数**（cosine/BM25 分数尺度不一，加权永远次优）。DeskPet 用 RRF 方向对。三段式：召回~128×2 → RRF→~32 → rerank→8。
- **重排**：cross-encoder +5~15 NDCG 但 80-400ms 且 CPU 实时不可行 → **DeskPet 无 GPU，建议 reranker 保持 OFF**；要做用 FlashRank（CPU 轻量）而非 BGE-reranker 重模型。
- **写入巩固**：抽取要带上下文窗口（摘要+最近 N 条）；**防幻觉**（抽取附原文出处 + 写前校验 + 低置信进待确认区）；**冲突消解 ADD/UPDATE/DELETE + 软失效**。
- **embedding**：cosine 必须归一化（DeskPet 显式除法已等效）；BGE-M3 int8 选型对（多语言 MIRACL nDCG 强）；存 `model_version` 指纹，换模型必全量重嵌，**绝不新旧向量混存**。
- **评测**：LongMemEval/LoCoMo 六类能力（尤其 knowledge-update / multi-session / temporal）；双指标 **Recall@k + LLM-as-judge**；**专设"字面 vs 改写"对照题**戳破 FTS 虚高（若改写版 recall 暴跌 = dense 路没真工作）。
- **明确"别投入/负收益"清单**：ANN/HNSW（<10 万条无意义）、在线 cross-encoder reranker（无 GPU+加延迟）、HyDE/multi-query（多次 LLM 拖慢桌宠）、late/hierarchical chunking（记忆单元本就短）、graph memory（单用户关系推理需求弱）。

---

## 6. 优先级 Top 10 行动清单（合并代码审计 + 研究）

| # | 行动 | 解决 | 复杂度 | 收益 | 来源 |
|---|---|---|---|---|---|
| **1** | **lifespan 加自动 backfill 兜底** + 改正 main.py:810 虚假注释 | FATAL-A 永久 embedding 缺口 | 低（半天） | **极高** | 我核验 |
| **2** | **检索/嵌入 silent-failure 告警**：降级点 log.warning + 计数 + health 暴露 failed_rate | FATAL-B 无声退化 | 低 | **极高** | hunter+研究互证 |
| **3** | **建 30-50 题本地 DeskPet-MemEval**（六类，字面 vs 改写对照，Recall@k + LLM-judge） | 戳破"FTS 虚高"，建迭代基线 | 中（1-2d） | **极高** | 研究（LongMemEval/LoCoMo） |
| **4** | **决定并灰度开 `facts_extract` + 写入端冲突消解**（ADD/UPDATE/DELETE + 软失效），用 #3 基线量化 | 长期语义记忆暗装 / 矛盾污染 | 中 | 高 | 我核验 + 研究 Mem0/Zep |
| **5** | **facts 召回换 sqlite-vec**（C 暴力，非 ANN）+ 统一 messages/facts 向量栈 | Python 暴力慢 + 两套不一致 | 中 | 高 | 研究 sqlite-vec 一手 |
| **6** | embedder 子进程重启移出锁（先独立核验 HIGH-C） | 崩溃冻结整个嵌入系统 | 中 | 高 | hunter（待核验） |
| **7** | RRF 只融 dense+FTS，recency/salience 改融合后 re-score | recency/salience 稀释语义召回 | 低 | 中-高 | 研究 RRF+Gen-Agents |
| **8** | 持久化 persona/human block（固定注入，不走检索） | 桌宠人设漂移 | 中 | 中-高（体验杀手锏） | 研究 Letta |
| **9** | TTL×salience 地板×访问频率强化，纳入检索 re-score（不必物删） | 陈旧记忆挤占 top-k | 中 | 中 | 研究 forgetting 多篇 |
| **10** | reflection/episodic→semantic 放 idle 低频跑，洞见写回 persona | 巩固不占用户等待 | 中-高 | 中（长期） | 研究 Gen-Agents+Letta |

---

## 附：方法论（如何得出 + 诚实标注）
- **3 路交叉验证**：我的代码审计（config/wiring/cosine/retention 亲自读码）+ silent-failure-hunter 子代理（11 发现）+ best-practice 调研子代理（6 主题有引用）。
- **校准动作**：① 研究"头号归一化坑"经核验 DeskPet 不存在（显式除法）；② hunter 的 FATAL-1 我下调为 MEDIUM（CancelledError 传播是正确语义）；③ hunter 的 FATAL-2 我一度怀疑"backfill 能救"，核验后发现 **backfill 仅手动脚本**→确认其永久性，反而上升为系统性 FATAL-A。
- **未独立核验项**已标 🤖（HIGH-C 子进程冻结、部分 MEDIUM），落地前应先复现。
- **未改任何代码**（探索模式）。落地按 §6 优先级，建议先做 #1#2#3 再谈其余。
</content>
</invoke>
