# DeskPet 记忆工程 + 人格半衰期 现状自查报告
> 生成时间: 2026-06-04  
> 自查范围: `backend/deskpet/memory/` + `backend/deskpet/agent/preference_memory.py` + `backend/deskpet/agent/assembler/components/persona.py`  
> 方法: 只读代码，以代码为准。

---

## 结论速览表

| # | 检查项 | 判定 | 关键证据文件 |
|---|--------|------|-------------|
| 1 | 检索多路融合 | ✅ 已有（4路 RRF + 3附加路） | `retriever.py` + `enhanced_retriever.py` |
| 2 | 人格/偏好画像 | 🟡 部分（有 facts 偏好抽取 + PreferenceMemory，但无统一画像渲染到 prompt） | `preference_memory.py` + `facts.py` |
| 3 | 偏好半衰期/衰减/Pin-Forget | 🟡 部分（衰减已有，Pin 机制缺失，Forget 有但仅针对 facts 不覆盖 PreferenceMemory） | `facts.py:57-68` + `retriever.py:837-910` |
| 4 | 写入分级 | 🟡 部分（BGE-M3 异步写入 + mock 降级，但无明确"跳过 embedding 快路"分级） | `vector_worker.py` + `embedder_worker.py` |
| 5 | 抽取「目标/决策/约束」 | ❌ 缺口（facts_extract 抽 5 类泛事实，无 goal/constraint/decision 专用 category；goal_store 是运行时内存无持久化） | `facts.py:57-69` + `agent/goal_store.py` |

---

## 逐条详述

### 条目 1：检索多路融合

**判定: ✅ 已有**

`retriever.py` 的基础 `Retriever` 已实现 4 路 RRF 融合（`retriever.py:265-294`）：
- **vec**: sqlite-vec KNN，BGE-M3 向量
- **fts**: FTS5 全文，trigram tokenizer
- **recency**: 按 `created_at DESC` 最新消息
- **salience**: 按 `salience` 分（recall 命中自动 boost +0.05，每日指数衰减）

权重默认：`vec=0.5 / fts=0.3 / recency=0.15 / salience=0.05`（`RetrievalPolicy` 类，`retriever.py:154-177`）。

`EnhancedRetriever`（`enhanced_retriever.py`）在此基础上叠加 3 路附加信号：
- **facts**: 结构化 facts 向量召回 + LIKE 兜底，RRF 折叠（`facts_weight` 参数）
- **chunk**: 长消息切块后的 chunk 向量召回，命中补进父 message
- **entity**: query 端 NER 抽实体 → LIKE facts.value，`entity_weight=0.10`

**session-affinity**: 跨 session 项目类记忆按 `cross_session_decay`（默认 0.15）降权（`retriever.py:588-631`）。

**缺口**: 没有真正意义上的 graph 路（知识图谱）；freshness 信号由 recency 路近似代替，并非独立显式的"新近度"信号。但综合来看，混合打分加权完整度高，达到 5 路实质融合（vec+fts+recency+salience+facts/entity）。

---

### 条目 2：人格 / 偏好画像

**判定: 🟡 部分**

**已有的两块：**

**A. FactsStore 偏好抽取**（`facts.py`）  
`FactExtractor` 从消息中抽 5 类结构化事实，其中 `category=preference` 和 `category=profile` 直接对应"偏好"和"个人资料"。每条 fact 有 `key/value/confidence/evidence`，持久化进 SQLite `facts` 表。LLM prompt 明确排除一次性/临时信息，只保留"数周后仍有意义的稳定事实"（`facts.py:77-105` extract prompt）。

**B. PreferenceMemory**（`preference_memory.py`）  
独立的 BGE-M3 语义偏好记忆，存 `kind="plan"|"intent"` 两类，JSON 落盘到 `<userdata>/preference_memory.json`。用于"第一次确认过的任务类型，下次自动确认"的行为偏好。

**PersonaComponent**（`assembler/components/persona.py`）  
仅渲染静态 persona 文本（config 设定的角色描述 + 当前模型名），提示用户"熟悉用户偏好（见 USER.md / MEMORY.md）"，但实际 USER.md/MEMORY.md 内容由其他 component 注入，persona 本身不动态抓 facts。

**真实缺口**：  
没有一个"用户偏好画像摘要"Component 会在每轮把当前活跃的 `category=preference/profile` facts 渲染成结构化的画像块注入 system prompt。MemoryComponent（`assembler/components/memory.py`）注入的是语义召回结果，不是专门的偏好画像。目前偏好只能靠召回"碰到"，而非被主动 pin 到上下文最前面。

---

### 条目 3：偏好半衰期 / 衰减 / Pin-Forget

**判定: 🟡 部分（衰减已有，Pin 缺失，Forget 部分有）**

**已有的衰减机制：**

**A. Facts 层的 per-category 衰减**（`facts.py:57-68`）：
```python
_CATEGORY_DECAY: dict[str, float] = {
    "profile": 0.0,        # 永不衰减 — 用户姓名/生日
    "preference": 0.005,   # 约 200天 half-life
    "project": 0.01,
    "event": 0.05,         # 快 — 昨天的事
    "reflection": 0.02,
    "episodic_summary": 0.01,
}
```
`FactsStore.daily_decay()`（`facts.py:772-803`）：每日对活跃 facts 做 `confidence *= exp(-decay_rate * days_since_last_recalled)`。`profile` 衰减率=0.0（永不衰减），`preference` 衰减率=0.005（约 200 天 half-life）。这正是"偏好半衰期"的实质实现。

**B. Messages 层的 salience 衰减**（`retriever.py:837-910`）：
`daily_decay()` 函数对所有消息做 `salience *= exp(-lambda * days_since_touch)`，default `lambda=0.02`。

**已有的 Forget 机制：**  
`FactsStore.mark_forgotten()`（`facts.py:617-639`）+ `restore_from_undo()`（5s undo 窗口）。`memory_forget` 工具（`memory_tools.py`）已接通 UI（WebSocket `p4_ipc.py:422`）。用户可以通过 MemoryPanel 明确遗忘某条 fact。

**真实缺口：**

1. **Pin 机制（钉住）**: 没有 `pinned=True` 字段或类似机制让用户把某条偏好"永久置顶"不受衰减和降权影响。`profile` category 靠 `decay_rate=0.0` 达到"永不衰减"效果，但没有显式 Pin 操作让用户动态指定哪条偏好免衰减。

2. **PreferenceMemory 无衰减**: `preference_memory.py` 的条目永不衰减（只有 `max_entries=500` 的 FIFO 截断），没有 half-life 机制。

3. **Forget 不统一**: `memory_forget` 工具只处理 `facts` 表，无法 forget `preference_memory.json` 里的条目（后者只有 `clear()` 全清或按 kind 清）。

---

### 条目 4：写入分级

**判定: 🟡 部分（异步 BGE-M3 写入有，但无明确的"跳过 embedding"轻量级路径）**

**已有的写入架构：**

`VectorWorker`（`vector_worker.py`）：batch 异步写，"8条或2s"触发，失败隔离（不阻塞主 chat 写入），`maxsize=1024` 队列背压，drop 最老条目（`vector_worker.py:60-65`）。

`EmbedderWorker`（`embedder_worker.py`）：进一步的分批异步化，避免 BGE-M3 CPU 推理阻塞主线程。

`FactsStore.upsert()`：每条 fact 写入时同步调 `await self._embed_fact(key, value)`，embedder=None/mock 时 embedding 列留 NULL，召回端 LIKE 兜底（`facts.py:240-270`）。

**语音/截屏高频流处理**：`image_worker.py` 处理图像生成（是工具输出，不是截屏流），没有看到专门处理"语音 tick/实时截屏"的轻量写入路径。这类高频流似乎依赖上层聚合后才进 session_db。

**真实缺口**：  
没有明确的 `light=True / skip_embed=True` 写入参数让调用方"这条消息很临时/高频，不做 embedding"。所有写入 session_db 的消息都会被 VectorWorker 接入 embed 队列（虽然 queue 可 drop，但无主动跳过机制）。

---

### 条目 5：记忆抽取「目标/决策/约束」

**判定: ❌ 缺口**

**已有的 facts category**（`facts.py:57-69`）：
```
preference / profile / project / event / reflection / episodic_summary
```
无 `goal`、`decision`、`constraint` 这三类专用 category。

**goal_store.py**（`agent/goal_store.py`）：有 `SessionGoal` 结构存 `/goal <text>` 命令设定的目标，但：
- 纯**运行时内存**（Python dict），进程重启后丢失
- 无持久化到 facts 或 session_db
- 只有当前 session 可见，无跨 session 召回
- 不被 facts_extract 管，不进 retriever

**extract prompt**（`facts.py:77-105`）明确排除"一次性、时间性、临时事项"（"一个明天 3 点的会议"、"临时文件路径"等），但没有正向列出要抽 goal/decision/constraint 这类结构。

**多 scope 打标**：facts 表有 `subject` 字段（默认"user"），可区分 subject，但没有 `scope=user/session/agent` 这种跨层标注维度。

**真实缺口**：用户说"我下周要做 X，你记一下"，如果 X 的表述不像"稳定偏好"，extract prompt 会过滤掉；即使抽进来也只进 `event` category（最快衰减），无法作为 goal 长期保留。用户的决策（"我决定用 TypeScript 做这个项目"）和约束（"这个项目只能用开源库"）均无专用存储路径。

---

## 给 Plan 的建议

### P1 — 偏好画像 Component（高优先级）
在 `assembler/components/` 新增 `preference_profile.py`，每轮从 `facts_store.list_active(category="preference")` + `list_active(category="profile")` 拉 top-N 事实，渲染成结构化 persona 块注入 system prompt 头部（`priority=85`，仅次于 PersonaComponent）。

### P2 — 统一 Pin / Forget（中优先级）
- `facts` 表加 `pinned=0/1` 列；`pinned=1` → `decay_rate=0.0`（免衰减）+ `confidence` 不降
- `memory_forget` 工具扩展支持 `PreferenceMemory`（kind="plan"/"intent"）的按条遗忘
- UI：MemoryPanel 每条事实旁加 Pin/Forget 按钮

### P3 — 目标/决策/约束 Category（中优先级）
- `_CATEGORY_DECAY` 加 `"goal": 0.005, "decision": 0.002, "constraint": 0.001`（约束最慢衰减）
- `_EXTRACT_PROMPT` 加正向引导，明确让 LLM 抽 goal/decision/constraint 类陈述
- `goal_store.py` 在目标设定时自动写一条 `category=goal` fact 做持久化（双写）

### P4 — 写入分级（低优先级，视高频流需求）
如果引入语音实时 tick / 截屏流，需在 `session_db.append_message()` 或 VectorWorker 入口加 `skip_embed: bool` 参数，高频流消息只进 L2 不进 L3 向量索引。

---

*报告仅描述代码现状，所有"已有"判定以实际代码逻辑为准，不依赖文档或注释。*
