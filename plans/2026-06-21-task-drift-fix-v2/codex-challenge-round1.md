# CODEX 挑战 Round 1 — 任务漂移 v2 plan：代码实现细节调研 + 对抗挑战

你是 DeskPet 项目（G:/projects/deskpet，master）的**资深后端架构师 + 严格对抗挑战者**。有一份"任务漂移 v2 修复方案"plan，目前还是**方向性**的，缺**代码实现细节**。你这一轮要**同时做两件事**：(A) 把方案落成确切的代码实现细节，(B) 对抗挑战其可执行性。**只读，不改任何文件。**

## 用户硬要求（评判基准）
1. **plan 必须含代码实现细节**：每个改点要有 `file:line` + 现状代码 + 要改成什么 + 函数签名/数据流 + 风险。空泛的"让 topic 派生自原话"不合格，要给到"改哪个函数的哪行、怎么改"。
2. **功能只能多做，不可以少做**：发现 plan 漏掉的必须做的功能/边界，要补出来。
3. 目标是让 plan **100% 可被另一个 agent 照着执行**，且**功能达到预期**（真机不再漂移）。

## 背景（根因，已定）
桌宠聊天单一永续 `session="default"` 从不切分，历史里高频旧主题（CATL/宁德/电池）成 attention-sink，新无关请求被带偏——**LLM 在生成 deepresearch 工具调用的 `topic` 参数时就漂了**。已证伪：相似度/词法门控对**相邻领域**（固态电池↔钠离子）失效（distractor interference）。2026-06-21 fanout 真机又复现 2 次。

## 待挑战的 plan
读 `plans/2026-06-21-task-drift-fix-v2/00-research-and-best-fix.md`，**重点 §4 最佳修复方案（T0-1/T0-2/T0-3 + T1-1/T1-2 + T2）+ §1 根因实证**。

## 你必须逐项落实的代码实现细节调研（边查边挑战）

### T0-1：deepresearch 的 topic/sub_questions/报告标题/slug 严格派生自用户原话
- 读 `backend/deskpet/tools/research_tools.py`：`deepresearch()` orchestrator、`_PLAN_PROMPT`(约:536)、`_SYNTH_PROMPT`(:561)、`_FANOUT_SYNTH_PROMPT`(:611)、sub_questions 解析、**报告标题/落盘文件名 slug 怎么生成**（grep slug/filename/save/OutPut/DeepResearch）、`_ur=(user_request or topic)` 现状。
- 落实：要让"报告主题/sub_questions/slug"以 `user_request` 为准，**具体改哪几行**？`# {topic}` 改 `# {user_request}`？slug 用哪个变量？plan 阶段 prompt 怎么改让 LLM 不被漂 topic 带偏？**给确切 diff 级方案**。
- 挑战：`user_request` 有时是长口语（"帮我深度调研 X 的 A、B、C…"），直接当标题/slug 合适吗？要不要保留一个"清洗/提炼"步骤但**以原话为锚**？topic 完全弃用会不会破坏现有 brief/depth 等逻辑？

### T0-2（核心，先查清假设再挑战）：fanout 研究子代理用干净上下文
- **关键事实核查**：现在 fanout 子代理 spawn 时**到底传了什么 context**？是否继承主会话永续历史？读 `backend/deskpet/tools/research_tools.py` 的 `_run_subagent_fanout`(:1162)、`subagent_scheduler.py`、grep `subagent_scheduled`、子代理怎么拿到 sub_question 和上下文、子 run 的 session_id/messages 怎么构造。
- 落实：若子代理已隔离 → plan 的 T0-2 假设错了，要修正；若继承主线 → 给"改成只注入 user_request + sub_question"的确切改点。
- 挑战：deepresearch 内部 LLM 调用（plan/synth）走的是 orchestrator 自己的 `llm_call` 闭包还是 agent loop？它看不看主会话历史？把这条链路彻底澄清——**漂移到底发生在哪一层**（agent loop 选 topic 调工具时 / orchestrator plan 时 / 子代理研究时）。

### T0-3：关掉 Tier2 相似度截断
- 读 `backend/deskpet/agent/assembler/components/memory.py`（Tier2 `_topic_similarity`/`_lexical_topic_shift`/`topic_shift_gate`）+ `policies/default.yaml`。
- 落实：是改 `default.yaml` 8 段 `topic_shift_gate:false`，还是删代码？Tier1（锚定/重定性）保留——确认关 Tier2 不影响 Tier1 + 不破坏现有单测（`test_task_drift_fixa.py`）。
- 挑战：关了 Tier2，主会话层就只剩 Tier1 锚定（软）——对"非 deepresearch 的普通漂移"（如桌宠闲聊回复漂）还有没有防线？plan 是否漏了"主会话回复也会漂"这个场景？

### T1-1：会话作用域切分（结构化信号，非 embedding）
- 读 `backend/main.py` session_id 入口(:3771 control / :6881 audio)、多窗口 fanout `_broadcast_default_chat_peers`(:3173/:3190)、`_run_chat`、`backend/deskpet/code_mode/state.py`（可复用 session 切分模板 `_code_session_id`/enter/exit/load_persisted）。
- 落实：plan 说"完整祈使式自包含请求=任务边界"——**这个信号具体怎么在代码里判？** 在哪一层切 session_id？L2 历史读取（get_messages(sid)）怎么因 scope 改变而隔离？多窗口共享 default 怎么兼容？前端要不要改？给改造点清单（后端 N 处 + 前端 M 处 + 是否要 DB 迁移）。
- 挑战：祈使式检测会不会误判（用户连续追问同一研究）？显式 `/new` 按钮 vs 自动信号，plan 该选哪个或都做？这是大改，T1 的范围和风险要量化。

### T1-2：永续历史降级 external memory（MemGPT 式）
- 读 L2 现状：`memory.py` provide 怎么把 L2 raw 历史提进 bundle.history、`get_messages` limit、L3 召回。落实"L2 不默认直灌、改按相关性 page-in"的最小改法 + 风险（会不会破坏追问连续性）。

## 输出格式
1. **链路澄清**：漂移精确发生在哪一层（用 file:line 证据）——这决定 T0 改点的优先级。
2. **逐项实现细节**：T0-1/T0-2/T0-3/T1-1/T1-2 每项给"现状 file:line + 确切改法（diff 级）+ 风险 + 单测影响"。
3. **可执行性挑战清单**：plan 哪里假设错/漏功能/有阻碍/会破坏现有（按严重度排序）。
4. **"功能只能多做"补漏**：plan 漏掉的必须做的（如"主会话回复漂移"场景、fanout 与会话切分的交互、BC 回归）。
5. **收敛判定**：要 100% 可执行，还必须补哪些实现细节（给清单）。

严格、挑刺、给到代码行级。只读（可 grep/read/pytest --collect-only，**不要改文件**）。