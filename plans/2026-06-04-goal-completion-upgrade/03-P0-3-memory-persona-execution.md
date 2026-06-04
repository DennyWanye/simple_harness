# P0-3 记忆 + 人格（含 Pin）— 可执行实现 Blueprint

> 作者：架构师（深入真实代码产出）  日期：2026-06-04
> 范围：plan §4 P0-3（WI-3.1 ~ 3.4）。只读不改代码，本文是实现说明书。
> 配套自查：`research/deskpet-self-audit/p0-3-memory-persona.md`
> 对标：openhuman PROFILE.md / 半衰期 / 写入分级；best-practices §4.3 多 scope。

---

## 0. 现状速览（哪些已做好 — 别碰，哪些是缺口）

### ✅ 已做好，禁止重写

| 能力 | 真实代码事实（行号已核） | 不碰理由 |
|---|---|---|
| **7 路检索 / RRF 融合** | `retriever.py`(vec+fts+recency+salience 4 路 RRF) + `enhanced_retriever.py`(facts/entity/chunk 3 附加路) | DeskPet 检索领先对标。MemEval Recall 基线靠它，**任何改动不得回归**。 |
| **facts 召回 category-agnostic** | `enhanced_retriever._collect_fact_hits`(L236) 走 `vector_search`(L257)→`search`(L274) LIKE 兜底；`find_by_entities`(L488 facts.py) 只查 value 列；`_fact_row_to_hit`(L457) 渲染 `[fact] key: value` —— **全程不按 category 过滤** | **新增 category 自动进召回**，无需改检索任何一行。这是 3.1 最大利好。 |
| **半衰期核心** | `facts.py _CATEGORY_DECAY`(L57)：profile=0.0 永不衰减 / preference=0.005≈200d；`FactsStore.daily_decay()`(L772) 公式 `confidence *= exp(-decay_rate * days_since_touch)` | 半衰期机制本体完整，3.1/3.3 只往字典加项 + 接调度。 |
| **Forget + 5s undo** | `mark_forgotten`(L616)/`restore_from_undo`(L641)/`is_forgotten_recently`(L673)；`memory_forget` 工具已接 UI（`p4_ipc.py` `_handle_memory_forget_ws` L422） | Forget 闭环完整，3.3 不重做 Forget。 |
| **facts 抽取管线** | `FactExtractor.process_message`(L859)：extract→merge→cross-key→upsert，失败隔离、`_persist_lock` 串行化、`is_forgotten_recently` 防回插 | 3.1 只改 prompt + category 白名单，**不动管线结构**。 |
| **facts list IPC** | `p4_ipc.py _handle_memory_facts_list`(L376) 已返回 active facts（含 category/subject 过滤） | 3.3 Pin UI 复用此通道，只加 pin 字段透传 + 新 IPC verb。 |
| **VectorWorker 失败隔离 + 背压** | `vector_worker.py` 8条/2s 批、maxsize=1024 drop 最老、drain | 3.4 只加 light 快路**绕过** enqueue，不动主 batch 路。 |
| **assembler 并行 fanout** | `registry.py fanout`(L59) per-component 软超时 + 异常兜底；`build_default_assembler`(`__init__.py` L95) 注册 7 component | 3.2 新增 1 个 component，照现有模式 register 即可。 |

### 🟡 / ❌ 缺口（本 P0-3 要补）

| # | 缺口 | 真实证据 |
|---|---|---|
| 3.1 | facts **无 goal/decision/constraint** category；`_EXTRACT_PROMPT`(L77) 明确排除"一次性/时效"目标 → 用户目标被过滤；无 scope 标注 | `_CATEGORY_DECAY`(L57) 只 6 类；`goal_store.py` 纯内存（L62 TODO 持久化留 v2） |
| 3.2 | 无 Component 把活跃偏好**主动置顶**注入；`PersonaComponent`(persona.py) 只渲染静态文本，不抓 facts | `MemoryComponent` 注入的是语义召回（碰巧命中），非画像 |
| 3.3 | 无 **Pin**（profile 靠 decay=0.0 间接免衰减，但用户不能动态钉任意条）；`PreferenceMemory`(JSON) **无衰减**（只有 max_entries=500 FIFO） | `preference_memory.py` 无 half-life 字段 |
| 3.3-bug | **`FactsStore.daily_decay()` 生产从不被调用** —— 只 `retriever.daily_decay`(salience) 在跑 | `Grep daily_decay` 仅测试引用 facts 版；main.py 无调用点 |
| 3.4 | VectorWorker **无 skip_embed 快路**；语音 tick / 截屏判定全量进 embed 队列 | `append_message`(L250) 无 light 参数；hook 无条件 enqueue |

> **协同关键**：3.1 与 P0-1 WI-1.1（Durable Goal Store）必须共享 schema 约定 —— 见文末 §协同契约。

---

## WI-3.1 — goal / decision / constraint 记忆抽取

### 1. 目标文件
- `backend/deskpet/memory/facts.py`（`_CATEGORY_DECAY` + `_EXTRACT_PROMPT` + `ExtractedFact` scope）
- `backend/deskpet/agent/goal_store.py`（双写 hook，与 P0-1 协同）
- `backend/config.py`（新 flag `memory.v2.goal_facts`）
- `backend/main.py`（FactExtractor 构造处 L869 透传 scope；FactsStore.daily_decay 调度，见 3.3）
- **不改**：`enhanced_retriever.py` / `retriever.py`（召回 category-agnostic，自动吃新类）

### 2. facts category 怎么加（不过滤时效性目标）

**a) `_CATEGORY_DECAY` 加三类**（facts.py L57，半衰期对齐自查 §P3 建议）：
```python
"goal":       0.005,   # ≈200d，与 preference 同级（活跃目标长期留）
"decision":   0.002,   # ≈1年（一次决策长期有效）
"constraint": 0.001,   # 最慢（约束几乎不过期）
```
`VALID_CATEGORIES`(L69) 由字典派生，自动纳入白名单。`is_valid()`(L197) 自动放行。

**b) `_EXTRACT_PROMPT` 正向引导**（facts.py L77）—— 这是核心难点。现 prompt 第 92-95 行**显式排除**"meeting tomorrow / today's todo"，会误杀"我下周要做 X"。改法：
- category 行加：`goal | decision | constraint`（除现有 4 类）。
- 在"Do NOT extract one-off…"段后**追加正向白名单豁免**，原文意：
  > **例外**：用户陈述的**持续性目标**（"我想在月底前完成 X"）、**已做的决策**（"这个项目决定用 TypeScript"）、**长期约束**（"只能用开源库 / 预算 2000 以内"）→ MUST 抽，category 分别 goal/decision/constraint。区分关键：**一次性事件**（明天 3 点开会）仍排除；**带方向性/长期有效的意图与约束**抽出。
- 给 goal value 约定带状态前缀利于召回，如 `value="[active] 月底前完成 PPT 生成模块"`（可选，便于人格层与 verify 读"原目标"）。

**风险闸**：prompt 一改可能让 extractor 抽出更多噪声。**flag 隔离**：新 category 抽取由 `config.memory.v2.goal_facts`（默认 False）控制 —— flag 关时 prompt 用旧版（4 类），flag 开时用新版（7 类）。`_EXTRACT_PROMPT` 拆成两个常量或参数化 categories 行。

### 3. 召回路径
**零改动**。新 category 写进 `facts` 表后：
- 向量路 `_collect_fact_hits`→`vector_search`(L536) 只 `WHERE is_active=1 AND embedding IS NOT NULL`，不看 category。
- LIKE 兜底 `search`(L427) / entity `find_by_entities`(L488) 同样 category-agnostic。
- `_fact_row_to_hit`(L457) 渲染 `[fact] key: value` 进 L3 块 → LLM 跨会话看到上次目标。
- **唯一可选增强**：`_fact_row_to_hit` 文本前缀按 category 区分（`[goal]`/`[decision]`/`[constraint]` 替代统一 `[fact]`），让 LLM/人格层更易识别。这是 1 行改动，低风险，建议做。

### 4. 与 P0-1 durable goal store 的协同（谁存当前活跃 / 谁存历史沉淀）

| 维度 | P0-1 `SessionGoalStore`（升级为 DB 持久） | P0-3 facts `category=goal` |
|---|---|---|
| 职责 | **当前活跃目标**（执行态：text/子目标/进度/状态/iterations） | **历史沉淀**（"上次说过的目标/决策/约束"跨会话召回） |
| 生命周期 | 一个 session 一条活跃 goal，done 后归档 | 永久（按 decay 缓慢淡出，可 Pin 锁死） |
| 读者 | AgentLoop / VerifyGate(2.3) / re-anchor(1.3) | EnhancedRetriever → L3 块 → 下次主动懂你 |
| 存储 | SessionDB 新表（P0-1 定义） | `facts` 表（已存在） |

**双写契约（推荐方案 — 单向钩，不共享 schema）**：
- `goal_store.py` 在 `set()`（P0-1 改成 DB persist 版）成功后，触发一个**可选 fanout callable** `on_goal_set(session_id, text)` → 调 `FactExtractor` 直接 `upsert` 一条 `category=goal, key=f"goal_{session_id}", value=text, scope=session`。这复用 facts 既有 upsert，不需新表。
- **不共享 schema**：P0-1 的 goal 表是执行态（含 iterations/done），facts 是沉淀态（含 decay/confidence）。强耦同一 schema 会让两个子系统互相掣肘。用**单向事件钩**解耦：goal_store 是 source of truth（活跃态），facts 是它的"记忆投影"。
- **去重**：同 session 重设 goal → facts `find_active(subject, key="goal_<sid>")` 命中 → 走 merge/replace（已有逻辑），不会堆积。
- **避免双写漂移**：facts 侧 goal 只读不回写 goal_store；verify(2.3) 读"原目标"统一从 **P0-1 store** 取（活跃态权威），facts goal 仅供跨会话召回。这条写进契约防"两个 goal 源 disagree"（feedback_cross_layer_contract）。

### 5. 多 scope 打标（user / session）
现 facts 只有 `subject`（默认 "user"），无 scope 维度。
- **方案**：facts 表加 `scope TEXT DEFAULT 'user'` 列（走 `schema_v2_migrator._COLUMN_ADDS` 加 `("scope", "TEXT DEFAULT 'user'")`，与现有 superseded_by/forgotten_at 同机制，老库 ALTER）。
- `ExtractedFact` 加 `scope` 字段（默认 "user"）；`upsert` 加 `scope` 参数。
- 打标规则：user 级长期偏好/决策/约束 → `scope=user`；某 session 专属目标 → `scope=session`（key 带 session_id）。
- 召回端 session-affinity 已有（`retriever.py` cross_session_decay L588）：`scope=session` 且非当前 session 的 goal 自然降权，符合直觉。
- **flag 守护**：scope 列 ALTER 失败时（`alter_failures().get("scope")`）回退到 subject-only，不阻断启动。

### 6. 边界 case + flag 默认值
- LLM 抽不出 goal（表述模糊）→ 维持现状（return []），不强抽。
- goal 文本超 100 字 → `value < 100 chars` 约定 + normalize 截断（已有 L194 evidence 截 200）。
- **flag**：`memory.v2.goal_facts` **默认 False**（延续 v2 全 flag 默认关惯例，L182）。dev 先开验证抽取质量，prod 观测后再点亮。

### 7. 测试计划
**单测**（`backend/tests/test_goal_decision_facts.py` 新建）：
- TG-1 prompt 新版抽出 goal/decision/constraint（mock LLM 返三类 JSON，断言 upsert 三条）。
- TG-2 `_CATEGORY_DECAY` 含三类 + decay 率正确（goal 200d 半衰期数值断言）。
- TG-3 scope 列写入 + list_active 按 scope 过滤（需先确认 schema_v2_migrator ALTER）。
- TG-4 goal_store.set → on_goal_set 钩 → facts 出现 category=goal（双写契约）。
- TG-5 同 session 重设 goal → facts 走 replace 不堆积。
- TG-6 flag=False 时 prompt 用旧版（4 类）—— 字节级回归保护。

**真机 E2E**（windows-mcp，跨会话召回）：
- 会话 A：对话"我这个项目决定用 TypeScript，预算 2000 以内"→ 截图 → 抓 backend log 确认 `category=decision/constraint` upsert。
- 重启 backend（或新 session）→ 会话 B：问"我之前定的技术栈是什么"→ 截图 → LLM 回答含 TypeScript → log 确认 L3 块出现 `[decision]`。
- declare：`坐标=(输入框) | 动作=Clipboard粘贴+Enter | 期望=B 会话答出 A 会话的决策`。

**MemEval 不回归**：跑现有 `test_deskpet_retriever.py` + enhanced_retriever 召回测试，断言 Recall 不降（新 category 只增不改既有路）。

### 8. build order（WI 内）
1. config flag `goal_facts` → 2. `_CATEGORY_DECAY` 加三类 + scope 列（schema_v2_migrator）→ 3. `_EXTRACT_PROMPT` 双版本 + categories 参数化 → 4. `ExtractedFact.scope` + upsert scope 参数 → 5. goal_store 双写钩（依赖 P0-1 DB 版 set）→ 6. `_fact_row_to_hit` category 前缀（可选）→ 7. 测试。

---

## WI-3.2 — 人格画像主动注入 Component

### 1. 目标文件
- `backend/deskpet/agent/assembler/components/preference_profile.py`（**新建**）
- `backend/deskpet/agent/assembler/__init__.py`（`build_default_assembler` L95 注册）
- `backend/deskpet/agent/assembler/components/base.py`（`ComponentContext` 加 `facts_store` 依赖，或复用 `memory_manager`）
- `backend/config.py`（flag `memory.v2.persona_inject`）
- `backend/main.py`（注入 facts_store 到 assembler）

### 2. 新 Component 放哪 + 怎么选"当前活跃偏好"
新建 `PreferenceProfileComponent`（`name="preference_profile"`）：
- 每轮从 `facts_store.list_active(category="preference", limit=N)` + `list_active(category="profile", limit=M)` 拉活跃事实（已有方法，L399）。
- 可选并入 `category=constraint`（约束也属"该懂你"画像，但**不并 goal** —— goal 走执行态，画像层只放稳定偏好/约束）。
- **"活跃"判据**：`list_active` 已按 `is_active=1` + `updated_at DESC`；再按 `confidence` 排序取 top-N（衰减低的自然沉底）。Pin 项（见 3.3）强制置顶。
- 预算：top-N 默认 8~12 条，控 token；`_approx_tokens` 估算（persona.py L119 同款）。

### 3. 注入 prompt 的位置 / 格式（类 PROFILE.md 块）
- **bucket = `"frozen"`**？不行 —— 偏好改了下轮要反映，**用 `"dynamic"`**（每轮重建，对齐 plan 验收"改偏好下轮即反映"）。代价：牺牲这一小块 prompt cache，但画像块小（<400 token），可接受。
- **priority = 85**（自查 §P1 建议）：低于 persona(90)、高于 skill(70)/tool(60)。`Slice.priority` 越高越不被 budget 裁（bundle.py L78 注释）。
- 渲染格式（类 openhuman PROFILE.md）：
```
## 用户画像 (PROFILE, 活跃偏好)
- [preference] 喜欢的饮料: 乌龙茶
- [profile] 称呼: 老王
- [constraint] 工作时段: 只在晚上工作
- 📌 [preference] 编辑器: neovim   ← Pin 项带钉标
```
- bucket="dynamic" → 进 `memory_block`（bundle.py `build_messages` L295），位于 history 前、skill 后。

### 4. 与现有 SkillComponent / assemble 管线怎么挂
- 照 `SkillComponent`/`PersonaComponent` 同款 duck-typed Protocol（base.py L60），实现 `async def provide(ctx) -> Slice`。
- `build_default_assembler`(`__init__.py` L95-102) 加一行 `registry.register(PreferenceProfileComponent(facts_store=...))`。
- **依赖注入**：Component 需访问 facts_store。两条路：
  - (a) `ComponentContext` 加字段 `facts_store: Any = None`（base.py L41 区，与 memory_manager 并列）—— 干净，推荐。
  - (b) 构造时传入（`PreferenceProfileComponent(store=...)`，同 `WorkspaceMemoryComponent(store=...)` L102 先例）—— 也可。
  - **推荐 (b)**：与 WorkspaceMemoryComponent 一致，main.py 在 build_assembler 时把 facts_store 传进工厂。
- **policy 挂载**：`AssemblyPolicy.prefer`(bundle.py L140) 默认 `["persona","time","workspace"]` → 加 `"preference_profile"`。或走 `load_policies()`(default.yaml) 把它加进各 task_type 的 prefer。flag 关时 component 返回空 Slice（no_store），不挂 prefer 也行。
- **fanout 安全**：registry per-component 软超时(L129) + 异常兜底已覆盖，新 component 慢/挂不拖累别人。

### 5. flag 默认值建议（plan §4 待定项）
**建议：出厂 flag（`memory.v2.persona_inject`，默认 False），dev 先开**。理由：
- 延续字节级契约 + flag 渐进点亮惯例（plan §1 护城河第 4 条）。
- 人格注入直接改每轮 prompt，影响面大，需真机观测是否过度影响语气/token。
- flag 关 → component 返回空 Slice，bundle 字节级等同当前 → 零回归风险。
- prod 观测窗后再点亮（与 verify_gate off→shadow 同节奏）。

### 6. 边界 case + 红线
- facts_store=None（mock/未注册）→ 空 Slice（`meta={"status":"no_store"}`），照 MemoryComponent L42 先例。
- 无活跃偏好 → 空 Slice，不插空块（省 token，照 `_render_l1` L138 先例）。
- **红线（plan §7）**：画像块**只描述偏好/称呼/约束**，**禁止**写"要讨好用户/最大化粘性/延长在线"类指令。Component 渲染纯事实，不加行为诱导。verify 完成判定**不读** preference_profile 块（与 P0-2 2.3 隔离：人格层不介入完成判定）。

### 7. 测试计划
**单测**（`test_preference_profile_component.py`）：
- TG-1 facts 有 preference/profile → Slice 含画像块、priority=85、bucket=dynamic。
- TG-2 facts_store=None → 空 Slice 不抛。
- TG-3 Pin 项置顶 + 带 📌 标记。
- TG-4 flag=False → component 不进 prefer / 返回空（字节级回归）。
- TG-5 渲染**不含**任何粘性/讨好措辞（断言关键词黑名单）。

**真机 E2E**（改偏好下轮即反映）：
- 对话"我喜欢喝乌龙茶"→ 等 facts 抽取（log 确认 preference upsert）→ 下一轮问"推荐个饮料"→ 截图 → LLM 提乌龙茶 → 抓 prompt log 确认画像块含该条。
- 再"其实我改喝咖啡了"→ 下轮画像块反映咖啡（cross-key replace 已有）。
- declare：`坐标=(输入框) | 动作=粘贴+Enter | 期望=画像块下轮含新偏好`。

**MemEval 不回归**：画像块是**新增** system 块，不改召回；跑 assembler 既有测试（`test_deskpet_context_assembler.py`）确认 bundle 结构不破。

### 8. build order
1. config flag → 2. 新 component 文件（list_active 拉数 + 渲染）→ 3. 工厂注册 + main.py 传 facts_store → 4. policy prefer 加名 → 5. Pin 置顶（依赖 3.3 pin 列）→ 6. 测试。
> **注**：3.2 读 3.3 的 Pin 字段做置顶 → **3.3 先于 3.2 的 Pin 子步**（基础画像可先做，Pin 标记后补）。

---

## WI-3.3 — Pin 钉住 + PreferenceMemory 衰减

### 1. 目标文件
- `backend/deskpet/memory/facts.py`（pin 字段 + daily_decay 跳过 pin + pin/unpin 方法）
- `backend/deskpet/memory/schema_v2_migrator.py`（`_COLUMN_ADDS` 加 `pinned` 列）
- `backend/deskpet/memory/memory_v2_schema.py`（`_DDL` facts 表加 `pinned` 列，新库一次到位）
- `backend/deskpet/agent/preference_memory.py`（补 half-life 衰减）
- `backend/p4_ipc.py`（pin/unpin IPC verb + facts_list 透传 pinned）
- `backend/main.py`（**接通 `FactsStore.daily_decay()` 调度** —— 现生产从不调用！）

### 2. Pin 数据怎么存（facts 加字段）
- facts 表加 `pinned INTEGER NOT NULL DEFAULT 0`：
  - `memory_v2_schema._DDL`(L79 facts CREATE) 加该列（新库）。
  - `schema_v2_migrator._COLUMN_ADDS["facts"]`(L31) 加 `("pinned", "INTEGER NOT NULL DEFAULT 0")`（老库 ALTER，与 superseded_by 同机制）。
- 新增方法（facts.py）：
  - `async def set_pinned(fact_id, pinned: bool)` —— UPDATE pinned 列。
  - `list_active` 已可用；Pin 项查询走 `WHERE pinned=1`（画像层置顶用）。

### 3. Pin 项跳过 daily_decay 的实现
`FactsStore.daily_decay()`(L772) 现 SQL `SELECT ... WHERE is_active=1`。改：
```sql
SELECT id, confidence, ... FROM facts WHERE is_active=1 AND pinned=0
```
即 **pinned=1 直接不参与衰减循环**，confidence 永不降。比"在 Python 里跳过"更省（少读行）。
- 老库 pinned 列 ALTER 失败 → SQL `WHERE pinned=0` 报错 → 用 `alter_failures().get("pinned")` 守护：失败时回退到无 pinned 条件的旧 SQL（不跳 pin，但不崩）。

### 3b. ★ 接通 daily_decay 调度（修隐藏 bug）
**关键发现**：`FactsStore.daily_decay()` 写好了但**生产从未调用**（`Grep` 仅测试引用）。retriever 的 salience daily_decay 也需确认调度点。
- main.py lifespan 启动时（facts_store 构造后 L848 附近）加一次性调用：`await _facts_store.daily_decay()`，对齐 retriever "启动时跑一次"约定（retriever.py L20 注释）。
- 或挂进既有的每日维护 task（若有；需 Grep 确认 retriever salience decay 的调度点，复用同一调度器）。
- **flag 守护**：daily_decay 调用包 try/except，失败只 log，不阻断启动。

### 4. 给 preference_memory.py 补半衰期（对齐 _CATEGORY_DECAY）
`preference_memory.py` 条目现只有 `ts`，无衰减（自查 §3 缺口 2）。补法（轻量，不破坏现有 match）：
- 每条 entry 已有 `ts`(L124)。`match()`(L133) 计算 cosine 时，**乘一个 recency 衰减因子**：
  ```python
  age_days = (now - e["ts"]) / 86400
  decay = math.exp(-_PREF_DECAY_RATE * age_days)   # _PREF_DECAY_RATE=0.005 对齐 preference
  effective = cosine * decay
  ```
  以 `effective` 排序选 best，仍与 threshold 比的是原 cosine（或 effective，二选一需定）。**建议**：阈值比 cosine（保命中精度），排序用 effective（老偏好沉底）。
- Pin 对齐：PreferenceMemory entry 加可选 `pinned: bool`，pinned 项 `decay=1.0`（不衰减）。`record` 可带 pin 参数；`clear`/list 已有。
- **不引入 DB**：preference_memory 是单用户本地 JSON（设计如此，L15），保持 JSON，只在内存计算时加 decay 因子，存储不变（向后兼容旧 JSON：缺 pinned 当 False）。

### 5. Pin/Unpin UI 接通
- `p4_ipc.py` 加 verb `memory_pin` / `memory_unpin`（payload `{fact_id, pinned}`），调 `facts_store.set_pinned`。注册进 `_handle` 分发（p4_ipc L85 同款）+ `_VALID` 消息白名单（L50）。
- `_handle_memory_facts_list`(L376) 已返回整行 → pinned 列自动透传给前端（无需改，dict 带 pinned）。
- 前端 MemoryPanel 每条加 📌 按钮（前端改，不在本 backend 蓝图范围，标注交接）。

### 6. 边界 case + flag
- pin 不存在的 fact_id → `set_pinned` WHERE 命中 0 行，静默（照 `mark_forgotten` L616 先例）。
- pinned fact 被 forget → forget 优先（is_active=0），pin 不阻止 forget（用户意图明确）。
- **flag**：Pin 是用户主动操作，**建议默认开**（无 prompt 影响，纯存储/衰减行为，不像人格注入改 prompt）。但 schema 列 ALTER 受 `alter_failures` 守护。PreferenceMemory decay 可挂 `memory.v2.pref_decay` flag（默认 False，dev 先验证不误伤命中率）。

### 7. 测试计划
**单测**（`test_pin_and_pref_decay.py`）：
- TG-1 `set_pinned(id, True)` → list 行 pinned=1。
- TG-2 daily_decay 跳过 pinned 项（pin 项 confidence 不变，非 pin 衰减）。
- TG-3 pinned 列 ALTER 失败 → daily_decay 回退旧 SQL 不崩。
- TG-4 ★ daily_decay **真被调度**（lifespan 调用点存在 / 启动后 facts confidence 按时衰减）。
- TG-5 PreferenceMemory match 加 recency decay：老条目 effective 分降、pin 条目不降。
- TG-6 旧 JSON（无 pinned）加载不报错（向后兼容）。

**真机 E2E**（Pin 不衰减）：
- 设一条偏好 → Pin → 模拟时间推进（或注入 daily_decay 多次）→ 截图 facts panel → pin 项 confidence 满、未 pin 项降。
- declare：`坐标=(MemoryPanel pin 按钮) | 动作=click | 期望=pin 后该条免衰减`。

**MemEval 不回归**：pin/decay 只改 confidence/排序，不改召回结构；跑 facts + retriever 测试确认 Recall 不降。

### 8. build order
1. schema 加 pinned 列（_DDL + migrator）→ 2. `set_pinned` + daily_decay 加 `pinned=0` 过滤 → 3. ★ main.py 接通 daily_decay 调度 → 4. PreferenceMemory recency decay + pin → 5. p4_ipc pin/unpin verb → 6. 测试。

---

## WI-3.4 — 写入分级 light 快路

### 1. 目标文件
- `backend/deskpet/memory/vector_worker.py`（`enqueue` 加 skip 语义 或 新 `put_doc_light`）
- `backend/deskpet/memory/session_db.py`（`append_message` 加 `skip_embed: bool` 参数）
- 调用方（语音 tick / 截屏判定的写入点 —— 需定位高频流入口）
- `backend/config.py`（可选 flag `memory.v2.light_write`）

### 2. VectorWorker 加 skip_embed / put_doc_light 路径
现状：`append_message`(L250) 写完无条件触发 `_on_message_written` hook(L305) → VectorWorker.enqueue。light 路要**绕过 enqueue**。
- **方案 A（推荐，最小侵入）**：`append_message` 加 `skip_embed: bool = False` 参数。skip_embed=True 时**不触发 `_on_message_written`**（或触发但传 skip 标记）。消息照常入 L2（messages 表 + FTS5 trigger 自动同步），只不进 L3 向量。
  ```python
  if self._on_message_written is not None and not skip_embed:
      await self._on_message_written(msg_id, content)
  ```
- **方案 B**：VectorWorker 加 `put_doc_light(message_id, text)` 显式快路 —— 但 light 数据本就**不需要** embedding，方案 A 直接不 enqueue 更省。**选 A**。
- 召回端无影响：messages.embedding 留 NULL，向量召回自动跳过（已有 `WHERE embedding IS NOT NULL` 容错）；FTS/recency 路仍能召回（light 消息仍可被关键词命中），符合"轻量但不消失"。

### 3. 什么数据走 light（语音 tick / 截屏判定）
- **语音 tick**：VAD/ASR 的高频中间态、心跳 tick（非完整用户话语）→ light。完整一句用户话语**仍走正常路**（进向量）。
- **截屏判定流**：周期性截屏的"无变化/低信息"判定结果 → light。截屏触发的**有意义工具产物**走正常路。
- **判据落点**：在高频流的写入调用处传 `skip_embed=True`。需 Grep 定位语音/截屏写 session_db 的具体调用点（自查 §4 提到 image_worker 是工具输出非截屏流，语音 tick 入口待定位）。
- **保守原则**：默认所有对话照常进向量；**只**在明确高频/低信息流显式标 light，绝不误伤正常对话（plan 验收"正常对话照常入向量"）。

### 4. 不破坏正常对话入向量
- `skip_embed` 默认 False → 所有现有 `append_message` 调用点行为**字节级不变**（9+ 处 hook 调用点零改动，self-audit 提到的兼容点）。
- 只有**显式传 skip_embed=True 的新调用点**走快路。
- VectorWorker 本身不改（enqueue/batch/drain 全保留）——light 路根本不进 worker。

### 5. flag
- **建议**：light 快路可**默认开**（无 prompt 影响，纯写入优化，且默认 skip_embed=False 等于无行为变化），但**实际生效**取决于是否有调用方传 True。
- 若担心语音 tick 误判，可挂 `memory.v2.light_write`（默认 False），flag 关时所有 skip_embed 强制当 False（高频流也全量 embed，回退当前行为）。**建议挂 flag**，dev 验证哪些流真该 light 再点亮。

### 6. 边界 case
- skip_embed=True 但消息其实重要 → 最坏只是不进 L3 向量召回，FTS/recency 仍可召回（降级不丢）。
- light 消息后续想补 embedding → `VectorWorker.backfill_missing`(L198) 扫 `embedding IS NULL` 已能回填（若需要）。
- 空内容 → enqueue 本就 skip（L170），light 路无额外处理。

### 7. 测试计划
**单测**（`test_light_write_path.py`）：
- TG-1 `append_message(skip_embed=True)` → hook **不触发** → VectorWorker.enqueue 未被调（mock hook 断言 call_count=0）。
- TG-2 `append_message(skip_embed=False)`（默认）→ hook 照常触发（字节级回归）。
- TG-3 light 消息进 messages 表 + FTS（L2 可召回）、不进 messages_vec。
- TG-4 flag=False → skip_embed 强制 False（高频流也 embed）。

**真机 E2E**（高频流不触发 embedding 队列）：
- 触发一段语音 tick / 截屏流 → 抓 VectorWorker `stats()` 的 `queued_total` 不随 tick 增长 → 截图日志。
- 同时发一句正常对话 → `queued_total` +1（正常入向量）。
- declare：`坐标=(语音触发/对话框) | 动作=说话/输入 | 期望=tick 不进队列、对话进队列`。

**MemEval 不回归**：light 只影响**新写入的高频流**，不改历史召回；跑 retriever 测试确认 Recall 不降。

### 8. build order
1. config flag `light_write`（可选）→ 2. `append_message` 加 `skip_embed` 参数 + hook 条件 → 3. 定位语音 tick / 截屏写入点传 skip_embed=True → 4. 测试。

---

## 整体 build order（跨 WI）

```
阶段一（schema 地基，先行）：
  3.1-a  facts 加 goal/decision/constraint + scope 列(schema_v2_migrator)
  3.3-a  facts 加 pinned 列(_DDL + migrator)
  → 一次性把 facts 表三个新列(scope/pinned + 三 category 走 _CATEGORY_DECAY 无需列)加齐，
    避免多次 ALTER。schema 改完先跑 schema 测试确认老库 ALTER + 新库 DDL 都对。

阶段二（各 WI 主体，3.1/3.3/3.4 可并行）：
  3.1-b  _EXTRACT_PROMPT 双版本 + ExtractedFact.scope + upsert + flag goal_facts
  3.3-b  set_pinned + daily_decay 加 pinned 过滤 + ★接通 daily_decay 调度 + PreferenceMemory decay
  3.4    append_message skip_embed + 定位高频流入口
  → 三者文件独立(3.1 改 prompt/extractor，3.3 改 decay/pin，3.4 改 session_db)，可并行。

阶段三（人格注入，依赖 3.1+3.3）：
  3.1-c  goal_store 双写钩(依赖 P0-1 DB 版 set)
  3.2    PreferenceProfileComponent(读 facts preference/profile/constraint + Pin 置顶)
  → 3.2 的 Pin 置顶依赖 3.3 的 pinned 列；goal 双写依赖 P0-1。

阶段四：全套测试 + MemEval 回归 + 真机 E2E + 更新 STATUS/status.md
```

---

## 与 P0-1 的记忆协同契约（写进 spec，防漂移）

| 条款 | 约定 |
|---|---|
| **活跃态权威** | 当前活跃目标的 single source of truth = **P0-1 `SessionGoalStore`（DB 持久版）**。verify(2.3)/re-anchor(1.3) 读"原目标"**只从此取**。 |
| **沉淀态投影** | facts `category=goal` 是活跃 goal 的**只读记忆投影**，供跨会话召回（"上次说过的目标"）。**不回写** goal_store。 |
| **单向事件钩** | `goal_store.set()` 成功 → 触发 `on_goal_set(session_id, text)` → facts upsert（key=`goal_<sid>`, scope=session）。解耦，不共享 schema。 |
| **去重** | facts 侧同 key 走既有 merge/replace，不堆积。 |
| **scope 语义** | session 级 goal → `scope=session`（key 带 sid，跨会话自然降权）；user 级 decision/constraint → `scope=user`（长期画像）。 |
| **completion 隔离（红线）** | 人格层（3.2 画像块）与 facts goal **不参与完成判定**。verify 完成判定 = receipt 客观证据 + P0-1 活跃 goal_text 对照（plan §7 红线，与 P0-2 对齐）。 |
| **契约校验** | 加 `scripts/e2e_goal_memory.py` live smoke：set goal → 查 P0-1 store + facts 两处一致，防双源 disagree（feedback_cross_layer_contract）。 |
