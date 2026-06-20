# 任务漂移修复 · 最佳方案 plan（基于 HANDOFF 深度调研后定稿）

> **日期**: 2026-06-20　**状态**: 📐 方案定稿待实现
> **输入**: `plans/2026-06-20-task-drift-fix-HANDOFF.md`（调研交接）
> **一句话结论**: 漂移真凶是 **L2 原始历史零相关性门控**（按时近直灌成对话轮次）。最佳修复是**在组装层做"话题跳变感知的历史门控 + 当前请求优先锚定"（Fix A）**为根治，**deepresearch 把用户原话显式传进内部 prompt（Fix B）**为纵深防御，**暂缓 D1 硬会话切分**（高风险、改造面大、Fix A 已软性达成隔离效果）。

---

## 1. 诊断闭环（已逐条核实，非推测）

| 事实 | 代码位置（已验证） |
|---|---|
| 桌宠聊天单一 `session_id="default"`，前端不传 session_id | `main.py:3771`、`ControlChannel.ts` 无 session_id 参数 |
| L2 = **纯时近**，`get_messages` `ORDER BY created_at ASC` 取最近 N | `session_db.py:337-360`、`manager.py:233-263` |
| L2 条数：chat=5 / task=5 / web_search=2（policy 决定） | `policies/default.yaml` |
| **L2 被提升为真·对话轮次**（role=user/assistant），插在 system 之后、当前 user 之前 | `memory.py:95-116` → `assembler._stitch:365-371` → `bundle.build_messages:300` |
| **L2 全程零相关性门控** —— 换话题时旧 N 轮就是"当前对话线" | `memory.py:71-116`（无任何 relevance 过滤） |
| L3 召回已被 WI-2 时近降权，但 L2 没有对应门 | `retriever.py`（WI-2）vs `memory.py`（无门） |
| compaction 要 1M×80% 才触发，短会话永不触发 → WI-1 落空 | `tauri-dev.log.err:71` |
| WI-4a 目标锚定只在 `/goal` + 只锚外层 | `agent_loop.py:622-644` |
| deepresearch 内部 plan/synth 的 LLM 调用**完全隔离**，只拿 `topic`（单条 message），看不到用户原话 | `research_tools.py:975`(签名) / `:463`(_PLAN_PROMPT 仅 `{topic}`) / `:1764`(`_call` 单 message) |
| **embedder 组装期可复用**：L3 召回本就为当前 query 算 embedding；`messages` 表存 `embedding` BLOB | `retriever.py:536`(`_safe_embed_query`)、`embedder.py`、`001_p4_initial_v9.sql:60-73` |

**真凶链（短会话场景）**：
```
用户狂聊 CATL（872 条）→ get_messages 取最近 5 条全是 CATL
→ memory.py 无门，原样提升成对话轮次
→ messages = [system…][CATL轮×5][user: "调研 Rust Tokio"]
→ LLM 把 CATL 5 轮当"正在进行的对话线"，新请求被压
→ <think> 承认 conflict 但仍漂；tool call topic="宁德…"
→ deepresearch 内部只拿到漂移后的 topic，放大漂移
```

---

## 2. 方案选型（对 HANDOFF §4 候选的工程裁决）

| 方向 | 裁决 | 理由 |
|---|---|---|
| **D2/D3 → Fix A** 话题跳变感知的 L2/L3 门控 + 当前请求优先锚定 | ✅ **采纳为根治** | 直击已实锤路径；**全部封闭在组装层**，不动会话模型/fan-out/前端；可配置可回滚；高相似度时保留历史 → **不破坏追问连续性**；embedder 现成、成本极低 |
| **D5 → Fix B** deepresearch 传入 user_request | ✅ **采纳为纵深** | 3~5 行、隔离改动、零外溢；堵住"工具内部选题漂移"（即使外层 topic 略漂也能被原话拉回） |
| **D4** 隐式 goal 化 | ⏸ 可选/暂不 | Fix A 的"当前请求优先锚定"已在组装层覆盖同等效果，避免与 WI-4a 冗余 |
| **D1** 硬按任务切分 session | ⏳ **暂缓** | 教科书根因但**高风险**：①"新任务"误检会断连续对话 ②多窗口 fan-out 硬编码 `=="default"`（`main.py:3173`）需参数化 ③前端 ControlChannel 不传 session_id，需全链路打通。单机桌宠下 **Fix A 已用"组装期排除不相关旧轮"软性达成隔离**，无需这套管线。留作未来"新建对话"UX 真需要时再做 |

> 设计哲学：**不改"存什么"（会话模型不动），只改"喂什么给 LLM"（组装期按相关性裁剪）**。这是最小爆炸半径、最高杠杆的切口。

---

## 3. Fix A — 话题跳变感知的历史门控 + 当前请求优先锚定（根治）

### 3.1 机制
组装 L2 时计算"当前请求 ↔ 最近历史"的话题相似度，据此分档处理：

- **延续（高相似）**：照旧保留 L2（追问 "它的竞品呢" / "继续" 不受影响）。
- **跳变（低相似）**：丢弃/大幅截断 L2 原始历史（保留 0~1 轮做最低限度衔接），并对 L3 同样门控（与 WI-2 叠加）。
- **始终注入**一条轻量"当前请求优先"指令（system，紧邻当前 user）：
  > 「当前请求是本轮唯一任务；先前对话仅为背景，若与当前请求冲突，**以当前请求为准**。」
  作为不确定档位下的廉价保险（即便门控判断模糊也兜底）。

### 3.2 相似度信号（低成本）
- 复用 `Embedder.encode([user_message])`（L3 已为同一 query 算过，可共享，避免二次编码）。
- 历史侧优先读 `messages.embedding` BLOB（已存）；缺失则一次性 batch-encode 最近 N 条（N≤5，开销可忽略）。
- 取 `max cosine(current, hist_i)`（任一最近轮与当前请求相关即视为延续）作为门控量；阈值进 `default.yaml` 可调。
- **降级安全**：embedder 未 ready / 编码失败 → 不门控（保留现状行为），绝不因此报错。

### 3.3 改动点
1. `components/memory.py::provide`：取到 `l2_rows` 后插入门控逻辑（相似度计算 + 分档截断），并在 meta 里产出"当前请求优先锚定"文本（或交给 bundle 注入）。
2. `assembler/components/base.py` / `ComponentContext`：确保 `embedder`（经 memory_manager/retriever）在组装期可达；若已可达则零改。
3. `policies/default.yaml`：新增 `topic_shift_threshold`、`l2_keep_on_shift`（跳变时保留轮数，默认 0~1）、锚定开关。
4. `bundle.py`：若锚定走独立 system message，确认插入位置紧邻当前 user（不破坏 prompt cache 前缀）。

### 3.4 风险与缓解
- **阈值误判断连续对话** → 阈值偏保守（宁可多保留历史）；"当前请求优先锚定"始终在，即使没截断也能软性纠偏；短追问（"继续/那它呢"）即使低相似也可由"短+代词"启发式豁免（可选二期）。
- **多窗口共享 default** → Fix A 不改 session_id，对 fan-out **零影响**。

---

## 4. Fix B — deepresearch 用户原话直传内部 prompt（纵深防御）

`research_tools.py`：
1. `deepresearch()` 签名加 `user_request: Optional[str] = None`（`:975`）。
2. `_PLAN_PROMPT`（`:463`）加 `ORIGINAL USER REQUEST: {user_request}` + `REFINED TOPIC: {topic}` 双锚；`_SYNTH_PROMPT`（`:485`）同理。
3. 调用点 `:1040`/`:1341` 透传 `user_request=user_request or topic`。
4. `_handle_deepresearch`（`:1632`）从 args 取 `user_request`；tool schema 增可选字段，并由 agent 调用侧把用户原话注入 args（若拿不到则 fallback 到 topic，零回归）。

独立于 Fix A，可单独上、单独验。

---

## 5. 验收（按项目手工测试纪律 HARD CONSTRAINT）

### 5.1 单测（先行，TDD）
- `memory.py` 门控：构造"高相似→保留 / 低相似→截断"两组 fixture，断言 `l2_history` 长度与锚定文本注入。
- embedder 未 ready → 不门控、不抛错（降级路径）。
- Fix B：`_PLAN_PROMPT.format` 含 user_request；`user_request=None` 回退 topic。

### 5.2 真机 E2E（windows-mcp，**不可脚本回放替代**，feedback_real_e2e_not_script_replay）
1. 用现成 `%APPDATA%/deskpet/data/state.db` 的 872 条 CATL 历史（金矿）。
2. 桌宠输入框真模拟点击+粘贴：`帮我深度调研 Rust 异步运行时 Tokio 的架构与竞品对比`。
3. 抓 tauri dev log 的 `p5s2_tool_call_args_dump` → 确认 `topic` 含 Rust/Tokio、不含宁德/CATL；截图最终报告主题。
4. **判据**：连续 ≥3 次，tool topic = Rust、回复主题 = Rust，不漂。
5. 每个动作前 declare `坐标=(x,y) | 动作 | 期望`；截图存 `plans/manual-results-2026-06-20-task-drift/screenshots/`。

> 注意坑（HANDOFF §7）：跑 worktree 代码须 `DESKPET_BACKEND_DIR` + `DESKPET_PYTHON`，log 里确认 `[backend_launch] Dev python=…`；别手动起 backend / 双 vite。

---

## 6. 落地顺序
1. Fix B（最小、隔离）先上 + 单测。
2. Fix A 单测先行 → 实现 → 单测绿。
3. 合并后真机 E2E（5.2）→ 连续 3 次不漂 → 更新 `STATUS/status.md`。
4. （可选二期）短追问启发式豁免；若产品要"新建对话"UX 再评估 D1。

---

## 7. 第二轮深度调研发现（夯实/修正方案 · 2026-06-20）

### 7.1 Fix A 可行性：✅ 成立，但两条原假设被**修正**

**① embedder 组装期可达 ✅**：链路 `ComponentContext.memory_manager`（[base.py:42](backend/deskpet/agent/assembler/components/base.py:42)）→ `MemoryManager._retriever`（[manager.py:81](backend/deskpet/memory/manager.py:81)）→ `Retriever._embedder`（[retriever.py:215](backend/deskpet/memory/retriever.py:215)）可达（私有属性，需 `getattr` 防守）。`Embedder.encode(list[str])` 是 **async**、返回 `(N,1024)` BGE-M3、**L2 normalized**（→ cosine 退化为点积，极廉价）；`is_ready()`/`is_mock()` 可判降级。

**② ⚠️ 修正：历史 embedding 不在 `get_messages` 里**。原 plan 设想"直接读 `messages.embedding` BLOB"——**错**。`get_messages` 只 SELECT 12 列、不含 embedding（[session_db.py:348-354](backend/deskpet/memory/session_db.py:348)）；向量在**独立 `messages_vec` 虚表**、由 VectorWorker **异步回填**（[001_p4_initial_v9.sql:66](backend/deskpet/memory/migrations/001_p4_initial_v9.sql:66)）。
  → **后果**：最近几条消息向量**可能还没回填**，读 vec 表不可靠。
  → **改用更稳的做法**：组装期一次 `encode([current_request, recent_L2_concat])`（2 条短文本，一次 batch，~数十 ms），自给自足、不依赖异步回填。比读存量向量**更鲁棒**。

**③ ⚠️ 修正：当前 query 每轮其实被编码了 1~2 次（白扔）**。L3 召回 `_safe_embed_query`（[retriever.py:543](backend/deskpet/memory/retriever.py:543)）+ classifier embed tier（[classifier.py:257](backend/deskpet/agent/assembler/classifier.py:257)）都 encode 过当前 query，**算完即弃、不缓存/不返回**。Fix A 再 encode 一次 = 重复。**二期优化**：让 `Retriever.recall()` 回传 `query_vector` 供上游共享；一期直接重编码（成本可接受）。

**④ 锚定注入位置 ✅ 不破 prompt cache**：放进 `memory_block`（dynamic bucket，[memory.py:80-88](backend/deskpet/agent/assembler/components/memory.py:80)）或当前 user 前的独立 system message，均不动 frozen 前缀（[bundle.py:287-304](backend/deskpet/agent/assembler/bundle.py:287)）。

### 7.2 Fix A 正确性：**追问误伤是头号风险**，门控判据须**收紧为合取**

调研确认现有系统**完全没有"追问/延续/指代消解"检测**（classifier 三层级联 rule/embed/LLM 都不判延续，[classifier.py](backend/deskpet/agent/assembler/classifier.py)），且**没有任何测试保护 L2 多轮追问连续性**（L2 原始历史门控是全新地盘，无安全网）。危险样本：`继续` / `它的竞品呢` / `第二个方案呢`——**省略主语、与历史 embedding 相似度反而偏低**，朴素相似度门控会**误删它们需要的上下文**。

→ **门控判据收紧为"低相似 ∧ 像独立新任务"合取**（任一不满足就保留历史）：
- 相似度信号：`max cos(current, 最近3条L2) < 阈值`（建议 0.35，进 yaml）。
- 新任务形状：消息**较长**（>~50 字）**且无指代起手**（可复用 [entity_extractor.py:39-55](backend/deskpet/memory/entity_extractor.py:39) 的代词/疑问词停用词集做 anaphora 检测）。
- 短消息（<10 字）/ 代词疑问词起手 → **直接判延续、保留历史**（高优先豁免）。
- **"当前请求优先"锚定始终在**——即便门控放过（保留历史）也能软性纠偏，是不确定档的兜底。
- **kill-switch**：config 开关，**默认关**，验证通过再开（对标 WI-2 的 legacy 退回）。

→ **L2 门控 与 L3 WI-2 降权分工，勿双罚**：L3 已对"同 session 旧记忆按年龄指数衰减"（[retriever.py:812](backend/deskpet/memory/retriever.py:812) `_intra_session_recency_weight`）；L2 门控只管"当前请求 ↔ 最近原始对话轮次的语义相关性"，**两者一个管年龄、一个管话题相关性，不重叠**。注意 L3 的 `affinity=1.0 同 session` / `0.8 跨 session 人物类` 是**受测保护的不变量**（`test_retriever_session_affinity.py`、`test_regression_2026_05_16_vpn_hijack.py`），那是 L3 召回评分，**与 L2 原始历史门控是两套机制**，别混改、别波及。

### 7.3 Fix B 可达性：⚠️ 修正——**鲁棒版需一小处管线打通，非"纯 3 行"**

工具执行时 `_handle_deepresearch(args, task_id)` **拿不到用户原话**——`_text` 在 `_run_chat`（[main.py:5222](backend/main.py:5222)）有，但进 AgentLoop 后丢了。两条路：
- **B1（schema 加字段，LLM 自己填 user_request）**：trivial，但**模型正在漂时填进来的也是漂的**，→ 不可靠、违背初衷。
- **B2（dispatch 时由系统注入原话）**：在 agent loop 工具分发处把"本轮原始 user 消息"（`_msgs[-1].content`，进 loop 前可得）注入到声明了 `user_request` 的工具 args。**鲁棒**，但需在工具分发链补一小段把原话传到 handler（非纯 3 行，但仍封闭）。
→ **采 B2**。改点：schema [research_tools.py:1590](backend/deskpet/tools/research_tools.py:1590) + handler [:1632](backend/deskpet/tools/research_tools.py:1632) + orchestrator 签名 [:975](backend/deskpet/tools/research_tools.py:975) + `_PLAN_PROMPT`[:463](backend/deskpet/tools/research_tools.py:463)/`_SYNTH_PROMPT`[:485] 注入 + **agent loop 分发处注入原话**（需定位 tool dispatch 点）。

### 7.4 D1 改造面已量化：**后端 12 处 + 前端 6 处 + DB 迁移**，暂缓正确

- 可复用模板：`code_mode/state.py` 的 `_code_session_id`/`enter`/`exit`/`delete`/`load_persisted`（[state.py:30-191](backend/deskpet/code_mode/state.py:30)）→ 可仿做 `TaskSessionManager`。
- 🔴 **最高复杂度 = 多窗口 fan-out 重组**：`_broadcast_default_chat_peers` 硬编码 `=="default"`（[main.py:3190](backend/main.py:3190)），13 处调用；session_id 不再恒 default 后要定义"同组会话"语义。
- 后端 session_id 仅 2 入口（[main.py:3771](backend/main.py:3771) control、[:6881](backend/main.py:6881) audio），默认 "default"；前端 `ControlChannel.ts` 完全不传，需补 5~8 处。
- **结论**：改造面中大、复杂度高，**暂缓正确**；Fix A 用组装期裁剪已软性达成隔离。

### 7.5 评审待你拍板的两个决策点
1. **Fix B 走 B2（系统注入原话，鲁棒）**——确认接受"需在 agent loop 分发处补一小段管线"（非纯 3 行）。
2. **Fix A 门控默认关（kill-switch off）先灰度**——确认先默认关、真机验证连续不漂后再默认开。

---

## 8. 第三轮审查后定稿（2026-06-20 · 独立核验 + 风险分层）

> 本节在 §3/§7 基础上做一处**降风险重构**并拍死 §7.5 两个决策点。核验：§1 全部承重断言已亲自读码复核通过（memory.py 零门控、bundle.py 顺序、embedder 路径、_PLAN_PROMPT 仅 topic、l2_top_k=5）——诊断坐实，方向不变。

### 8.1 核心重构：Fix A 按风险拆成两层（锚定/重定性 ≠ 语义截断）

**问题**：原 Fix A 把"当前请求锚定"（零风险）与"语义截断门控"（高风险，§7.2 自认头号风险=追问误伤）**捆在同一 kill-switch 后面、一起默认关**。后果：永远测不出"其实光锚定就够了"。且反推那次漂移——**模型当场承认 conflict 仍漂，但那是在"无任何锚定指令"下**，所以单条强锚定能不能治好是**未知数**，不该和高风险截断绑死。

**拆层**（按风险分级、分别上、分别验）：

| 层 | 内容 | 风险 | 默认 | 机理 |
|---|---|---|---|---|
| **Tier 1** | ① **当前请求优先锚定**：当前 user **正前方**插一条 system nudge（「当前请求是本轮唯一任务；先前对话仅背景，冲突时以当前请求为准」）。位置须**紧邻 user**（recency 才压得住 5 轮 CATL），非 memory_block 顶部。② **L2 历史重定性**：提升 L2 轮次前加一行 system 标签（「以下为较早对话记录，可能涉及其他话题，仅供背景参考」）。 | **零**（永不删上下文 → 不可能误伤追问） | **开** | 直接打"模型把 5 轮 CATL 当活跃对话线"的机理，不删一字、零相似度计算 |
| **Tier 2** | §7.2 那套**语义截断门控**（合取 `低相似 ∧ 长消息 ∧ 无指代起手` + anaphora 豁免） | **高**（新地盘无安全网，误删追问上下文） | **关**（kill-switch） | 删/截断不相关旧轮 |

> ⚠️ Tier 1 ② 重定性是**加一行标签、L2 仍是真 turn**，**不回退 P4-S21 #16**（当初故意把 L2 从 system block 挪进 history 防被当 noise 忽略，见 [memory.py:74-80](../../backend/deskpet/agent/assembler/components/memory.py)）——兼容。
>
> **策略**：先上 Tier 1（默认开）→ 真机连续验 → **够了就省掉 Tier 2**（连同头号风险一起省）；**不够才**开 Tier 2 灰度。即「先试改历史定性+锚定，删历史留兜底」。

### 8.2 §7.5 决策拍板

1. **Fix B = B2，且做成通用机制**：B1（LLM 自填 user_request）必然失败——模型正漂时填进 args 的也是漂的 topic。B2 由 agent loop 分发处**强制注入本轮原始 user 消息**。**改进**：不写成 deepresearch 专属，在 tool dispatch 处做成「**任何声明了 `user_request` 字段的工具，自动注入 `_msgs[-1].content`**」，一处管线多工具复用（ppt_create 等未来白嫖防漂）。
2. **Tier 1 默认开 / Tier 2 默认关**：取代原 §7.5#2「整个 Fix A 默认关」——Tier 1 零风险不开等于白做；Tier 2（截断）才灰度。

### 8.3 落地顺序（修订）
1. **Fix B（B2 通用注入）** — 最隔离，先上 + 单测。
2. **Fix A Tier 1**（锚定 + L2 重定性）— 默认开；单测验"标签+锚定注入"（**不验删除**）。
3. 合并 → 真机 E2E（872 CATL + 发 Rust，连续 ≥3 次不漂）。
4. **若 Tier 1 仍偶漂** → 才开 Tier 2，再验追问连续性回归。
5. 绿 → 更新 STATUS。

### 8.4 待两个子代理夯实的代码执行细节（本轮派活）
- **Agent A（Fix A 组装层）**：锚定"紧邻 user"需不需要改 `bundle.build_messages`（现签名 [bundle.py:282](../../backend/deskpet/agent/assembler/bundle.py) 是 system→history→user，无"history 后 system"槽位）；embedder async 在 `provide` 里 await 的并发/超时影响；Tier 1 两条标签的确切注入载体（memory_block meta 还是 bundle 新参数）。
- **Agent B（Fix B 分发层）**：本轮原始 user 消息（`_msgs[-1].content`）从 `_run_chat`（main.py:5222）到 AgentLoop tool dispatch 的确切可达点；通用注入"声明 user_request 的工具自动填原话"该挂在哪一层（schema/registry/dispatch）；有无阻碍。

---

## 9. 子代理代码执行评估定稿（2026-06-20 · 两个 general-purpose 子代理并行读码后）

> 两个子代理各亲自读码，**各挖出一个 🔴 级阻碍**（§8 未覆盖）。本节是**可直接照改的实现 spec** + 对 §8 的修正。所有 file:line 为本轮读码核实。

### 9.1 Fix A 确切改点（组装层）

**可行性**：Tier 1 锚定 ✅ / Tier 1 重定性 ✅ / Tier 2 截断 ⚠️（embedder 超时风险，见阻碍）。

| # | 文件:行 | 改什么 |
|---|---|---|
| A1 | [bundle.py:262](../../backend/deskpet/agent/assembler/bundle.py) `build_messages` | 加 keyword-only `late_system_nudge: Optional[str]=None`；在 `history` extend **之后**、`user_message` append **之前**插入 `{"role":"system","content":late_system_nudge}`。**对 prompt cache 零影响**（frozen 前缀不动，nudge 落动态尾部）。 |
| A2 | [bundle.py](../../backend/deskpet/agent/assembler/bundle.py) `Bundle` | 新增字段 `late_system_nudge: str=""`，由 MemoryComponent 经 meta 透出、`assembler._stitch`（[:365](../../backend/deskpet/agent/assembler/assembler.py)）提升（仿 `l2_history` 同一套机制）。**锚定逻辑收敛组装层，main.py 仅 1 行透传。** |
| A3 | [memory.py:95](../../backend/deskpet/agent/assembler/components/memory.py) | 构建 `l2_history` 时，若 `policy.relabel_l2`，在列表**头部**插一条 system：「以下为较早对话记录，可能涉及其他话题，仅供背景参考」。**就用 l2_history 首条，不需 bundle 新参数**；L2 仍是真 turn，**不回退 P4-S21 #16**。 |
| A4 | [bundle.py:106](../../backend/deskpet/agent/assembler/bundle.py) `MemoryPolicy` + [policy.py:201](../../backend/deskpet/agent/assembler/policy.py) `_to_policy` | **双改**（隐藏必改点）：dataclass 加 `relabel_l2=True`/`anchor_current=True`/`topic_shift_gate=False`/`topic_shift_threshold=0.35`/`l2_keep_on_shift=1`；`_to_policy` 同步解析，否则 YAML 新 key **被静默丢弃**。 |
| A5 | [memory.py](../../backend/deskpet/agent/assembler/components/memory.py) provide（Tier 2 才执行） | embedder 经 `getattr(mm,"_retriever",None)._embedder` 逐层防守取；`_topic_similarity` 内：`is_ready()/is_mock()` 预检（mock=md5 hash 无语义→不门控）→ `await asyncio.wait_for(emb.encode([cur,l2_concat]),0.3)` → `except→None→保留全部 L2`（fail-open）。归一化向量 cosine 退化点积。 |
| A6 | [entity_extractor.py:39](../../backend/deskpet/memory/entity_extractor.py) `_STOPWORDS` | 直接 import 复用做 anaphora 检测（句首代词/疑问词起手→判延续）。合取判据：`sim<阈值 ∧ len(cur)>50 ∧ not _starts_with_anaphora` 才截断，任一不满足全量保留。 |

**🔴 阻碍（A）**：embedder 的 `await encode` 落在**已有 1500ms 硬超时的组件 fan-out 内**（[registry.py:172](../../backend/deskpet/agent/assembler/registry.py) + assembler.py:91），`mm.recall` 已占一部分。**冷模型首调（warmup 加载 286MB BGE-M3）→ memory slice 超时 → L2+L3 全丢，比漂移更糟。** 缓解：①Tier 2 才碰 embedder（**Tier 1 零 embedder→零此风险，这是拆层最大价值**）②`is_ready()` 预检不触发 warmup ③内层 `wait_for(0.3)` ④fail-open。

### 9.2 Fix B 确切改点（分发层）

**可行性**：B2 系统注入 ✅ / 通用化 ✅（但**必须避开** `set_session_context`，见 🔴 阻碍）。

| # | 文件:行 | 改什么 |
|---|---|---|
| B1 | [main.py:6092](../../backend/main.py) 调 `_agent.run(...)` 处 | 新增 kwarg `loop_user_request=(None if _is_sentinel else _text)`。**`_text` 是函数参数、100% 本轮原话**，比 `_msgs[-1]` 零歧义。 |
| B2 | [agent_loop.py:564](../../backend/deskpet/agent/agent_loop.py) `run()` 签名 + :1872 dispatch 循环体 | run() 收 `loop_user_request`；在 `for tc in response.tool_calls:` 体内、`_dispatch_tool` 之前：若 `_tool_declares_user_request(tc.name, tool_schemas)` 且 `isinstance(tc.arguments,dict)` → **无条件覆盖** `tc.arguments["user_request"]=loop_user_request`。 |
| B3 | [research_tools.py:1589](../../backend/deskpet/tools/research_tools.py) schema | properties 加 `user_request`（**不进 required**），description 写「系统注入，勿填」（降低 LLM 主动填漂值的概率）。 |
| B4 | research_tools.py：`_PLAN_PROMPT`[:463] / `_SYNTH_PROMPT`[:485] / orchestrator 签名[:975] / `.format`[:1040,:1341] / handler[:1638,:1658] | 加 `ORIGINAL USER REQUEST (authoritative)` 双锚；orchestrator 内 `_ur=(user_request or topic).strip()`；handler 从 args 取 `user_request` 透传。**`user_request=None`→`_ur=topic` 零回归**。 |

**🔴 阻碍（B）**：**不能复用 registry 的 `set_session_context`** —— [registry.py:690-692](../../backend/deskpet/agent/registry.py) 合并顺序是 `session_context` 先、`params`（LLM 填的）后 → **冲突时 LLM 值获胜**。若 LLM 正漂时也填了个漂的 `user_request`，会**覆盖系统注入的真原话**，正好违背 B2 初衷。缓解：注入走 **agent_loop dispatch 直写 `tc.arguments`** + **无条件覆盖**（系统值永远赢，一并解决"LLM 乱填"+"合并顺序"两个问题）。

### 9.3 对 §8 的修正（子代理发现，须并入）

1. **§8.1 Tier 1① 锚定位置钉死 = `build_messages` 新参数 `late_system_nudge`**（非 memory_block 顶部——那在 history 前 recency 压不住；非拼进 user 文本——会污染 Fix B 的原话源）。两个 Fix 在此**耦合**：锚定必须走独立 system message 保持 user turn 纯净。
2. **§8 漏了"配置双改"**：`MemoryPolicy` dataclass + `_to_policy` loader 是固定 schema，YAML 加 key 会被静默吞（A4）。
3. **§8.1 Tier 2 未量化超时风险**：必须补 `is_ready()` 预检 + 内层 `wait_for(0.3)` + fail-open，否则 Tier 2 开启时冷模型反向制造"丢 L2"新 bug（A 阻碍）。
4. **§8.2「自动注入 `_msgs[-1].content`」措辞有坑**：`_msgs[-1]` 仅在 `run()` 入口、循环开始前是用户原话；进 loop 后 `working_messages[-1]` 是工具结果。**改为：main.py:6092 显式传 `loop_user_request=_text`**（B1）。
5. **§8.2 通用注入挂点钉死 = agent_loop dispatch 直写 `tc.arguments`，显式排除 `set_session_context`**（合并顺序陷阱，B 阻碍）。
6. **注入用「无条件覆盖」而非「缺失才填」**：schema 字段对 LLM 可见可能被乱填，无条件覆盖一举解决（取代 §4#4「拿不到则 fallback」）。

### 9.4 测试空白（须补）
- 现有 [test_p4s21_context_bundle_history.py](../../backend/tests) 只覆盖 L2→history 提升，**无任何 L2 多轮追问连续性/门控测试**。Tier 1 单测验"标签+nudge 注入"（不验删除）；Tier 2 必须补四组 fixture：高相似保留 / 低相似截断 / mock 不门控 / encode 超时 fail-open。
- Fix B 单测：`_PLAN_PROMPT.format` 含 user_request；`user_request=None` 回退 topic；dispatch 无条件覆盖 LLM 乱填值。
