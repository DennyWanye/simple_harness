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
