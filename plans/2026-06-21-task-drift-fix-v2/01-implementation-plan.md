# 任务漂移 v2 — 实现 plan（含代码实现细节，经 codex R1+R2 硬化）

> **日期**: 2026-06-21　**状态**: 🔨 实现细节定稿中（codex 多轮挑战 → 收敛至 EXECUTABLE-AS-IS）
> **配套**: 调研方向见 [00-research-and-best-fix.md](./00-research-and-best-fix.md)；挑战记录 §10。
> **行号声明**: `file:line` 来自 codex R1/R2 只读核读（2026-06-21），实现时以 `grep` 复核为准。

---

## §0 链路澄清（漂移精确发生在哪两层）

**层 1（上游·主 agent loop 选 topic 时已漂）**：`main.py:5465` append 用户消息进 `_sid` → `:5667` 用同 `_sid` assemble，`MemoryComponent` 把 L2 旧历史提升为真实 history → `bundle.py:313` 插在当前 user **前** → `:6322` `AgentLoop.run(..., loop_user_request=_text)`。Fix B（`agent_loop.py:64`）覆盖注入 `user_request`，**但 `topic` 已由带旧 L2 的主 loop 生成（漂了）**。

**层 2（下游·deepresearch 内部继续信漂 topic）**：`research_tools.py:1369` `_ur=(user_request or topic)`，但**后续大量逻辑仍用 `topic`**（详见 §1 全表）→ 漂移被固化进 plan/搜索/排序/报告/slug。

**★ R1 修正的关键假设**：**fanout 子代理不继承主会话历史**。`_run_subagent_fanout`[1162] 递归 `deepresearch(q, user_request=q, scheduler=None, skip_plan=True)`[1192]；`subagent_scheduler.py:67` 只管并发、不构造 messages/不读 parent 历史。→ "6 份错报告"根因是**上游 sub_questions 已由漂 topic 生成**，非子代理脏上下文。

**结论**：T0-1 治层 2 + fanout（让 deepresearch 全程以原话为准）；T1-1 治层 1（主 loop 不被旧 L2 污染）。**两者都要做**——层 1 不治，主 loop 选的 topic 仍漂，T0-1 只能把报告标题/slug 拉回（纵深，非根治）。

---

## §1 T0-1：deepresearch 原话夺权（★核心，diff 级全替换表）

`research_tools.py:1369` 后新增：
```py
request_topic = _ur            # canonical subject (authoritative) —— 一切"主题"用途都用它
llm_topic = topic              # untrusted candidate; 仅日志/可选展示
```

**全替换表**（把"当主题用"的 `topic` → `request_topic`；R1 基础 + R2 补全。行号实现时 grep 复核）：

| # | 位置 | 现状 | 改为 | 来源 |
|---|---|---|---|---|
| 1 | sub_questions 兜底 `:1423/1437` | `[topic]` | `[request_topic]` | R1 |
| 2 | query expansion `:1464` | `_expand_queries(llm_call, topic, ...)` | `request_topic` | R1 |
| 3 | **fanout root** `:1446` | `_run_subagent_fanout(topic=topic, ...)` | `topic=request_topic` | **R2** |
| 4 | **search owner** `:1479` | `search_specs.append((eq, topic))` | `(eq, request_topic)` | **R2** |
| 5 | **关键词评分** `:1530` | `_topic_keywords(topic)` | `_topic_keywords(request_topic)` | **R2** |
| 6 | **主题速度** `:1533` | `infer_topic_velocity(topic)` | `infer_topic_velocity(request_topic)` | **R2** |
| 7 | **二轮补搜 gap** `:1682` | `_gap_followup_queries(llm_call, topic, ...)` | `request_topic` | **R2** |
| 8 | **语义打分** `:1716` | `_SEMANTIC_SCORER(topic, ...)` | `request_topic` | **R2** |
| 9 | **LLM rerank** `:1750` | `_llm_rerank(topic, ...)` | `request_topic` | **R2** |
| 10 | synth `:1787` | `_SYNTH_PROMPT.format(topic=topic, ...)` | `topic=request_topic` | R1 |
| 11 | fanout synth 标题 `:633` | `# {topic}` | `# {request_topic}` | R1 |
| 12 | **no-results** `:1772/1773` | `ResearchReport(topic=topic)` + `_no_results_template(topic,...)` | `request_topic` | **R2** |
| 13 | fallback 标题 `:1153/1157/1964/1976` | `topic` | `request_topic` | R1 |
| 14 | **最终 report** `:1828` | `ResearchReport(topic=topic)` | `topic=request_topic` | **R2** |
| 15 | handler 保存 `:2158/2160/2193` | `_save_report(topic)`/index/`title_slug(topic)` | `save_topic = report.topic or (user_request or topic)` → 全用 `save_topic` | R1 |

**明确"不该改"（保留原样，R2）**：schema 里 `"topic"` 字段 `:2062/2098`；handler 入参 `args["topic"]` `:2117`；helper **形参名**（`_expand_queries(topic)`/`_llm_rerank(topic)`/`_save_report(topic)` 形参不改，只改**调用点传值**）；`topic_velocity` 作为 coverage key 不改；`seen` 去重集 `:327`（helper 内部，传对即可）；`depth`/`mode`/`max_rounds`/`brief` 链路**不得**被波及。

**`_PLAN_PROMPT`（:536）改写**：
```
ORIGINAL USER REQUEST (authoritative; derive ALL sub-questions from THIS):
{user_request}
CANDIDATE TOPIC FROM TOOL ARGS (untrusted; may be drifted — IGNORE if it conflicts):
{topic}
```
`_SYNTH_PROMPT`(:564/573) 骨架 `# {topic}` → `# {request_topic}`。**禁止** LLM 二次"清洗标题"（重引漂移面）；slug 用现有 `title_slug(max_grapheme=40)`，要短只做**确定性** collapse-whitespace+truncate。

**单测**：改 `test_task_drift_fixb.py:14` prompt 断言；**新增回归**：`topic="宁德/CATL"` + `user_request="深度调研 Rust Tokio"` → 断言 plan prompt / `report.topic` / markdown 标题 / `_save_report` slug / `search_specs` owner / `_SEMANTIC_SCORER`·`_llm_rerank`·`_gap_followup_queries` 入参 **全取 Rust**；**BC**：`user_request=None` → `request_topic=topic` 零回归。

---

## §2 T0-2：fanout 验证隔离（修正为"验证 + 防污染"）

fanout 已隔离（§0），不改隔离。仅：`test_deepresearch_subagent_fanout.py:195` 加断言 `scheduler is None`、`skip_plan is True`、子 run `user_request==各 sub_question`——锁死"子研究不带主历史"不变量。sub_questions 正确性由 T0-1（plan 从 `request_topic` 派生）+ §1#3（fanout root `topic=request_topic`）保证。不强行改子 run user_request（降召回）。

---

## §3 T0-3：关 Tier2 相似度截断（yaml only，不删代码；★顺序见 §8）

- `policies/default.yaml` 8 处 `topic_shift_gate: true → false`（`:44/61/77/105/120/135/151/167`）。
- **不删** `memory.py` 的 `_topic_similarity`/`_lexical_topic_shift`/gate（`:109/122-153`）；Tier1（`relabel_l2`/`anchor_current`，`memory.py:174-230`+`bundle.py:317`）**保留**。
- 单测：改默认 policy 期望（默认不调 embedder）；保留显式 `topic_shift_gate=True` opt-in 测试。
- ⚠️ **空窗（R2-E1）**：关 Tier2 后普通回复漂移只剩 Tier1 软锚定 → **必须 T1-1/T1-2 到位并真机验证后再默认关**（见 §8 顺序），否则过渡期漂移回潮。

---

## §4 T0-4：voice 路径补 Fix B + effective sid 全链路（★功能补漏）

`pipeline/voice_pipeline.py`：
- `:616` `loop.run(messages, session_id=self.session_id)` **补 `loop_user_request=text`**（`text` 在 `_run_with_tools(self, text, audio_ws)` 作用域=本轮语音原话，R2-C 已验证生效）。
- 配合 T1-1：voice 也要 resolve effective sid，且**全链路统一**——`:493/494` append、`:512` assembler、`:616` run、`:706` assistant 落库**都用同一 effective sid**（否则"用户写新 scope、组装/回复写旧 scope"）。
- 单测：mock voice pipeline 断言 `loop_user_request` 透传 + 四处 sid 一致。

---

## §5 T1-1：会话作用域切分（根治层 1，结构化信号优先）

**插入点（R2 精确）**：`main.py:5401`（得 base sid）**之后、`:5419`（code-mode suggest）之前**，先 resolve 再覆盖 `_msg_sid` 和 `text`：
```py
_msg_sid = _payload.get("session_id") or session_id            # :5401
decision = task_session_manager.resolve(_msg_sid, text, explicit_new=<payload.new_session>)
_msg_sid = decision.effective_sid
text = decision.stripped_text                                   # 去掉 /new 前缀
```

**新增** `backend/deskpet/session/task_scope.py`（仿 `code_mode/state.py`）：`TaskScopeDecision(effective_sid, created, reason, stripped_text)` + `TaskSessionManager.resolve(base_sid, text, explicit_new) -> TaskScopeDecision`。

**信号分级**：
- **T1-1a（默认开，先做）**：显式 `/new ...` + payload `{new_session:true}` + UI"新话题"按钮 → strip `/new`，起新 `effective_sid`（如 `task-<ts>`）。
- **T1-1b（默认关 / shadow log，后做）**：自动祈使式检测——误判风险高，**先只 shadow log 命中率**，不真切。

**后端接入点全清单（R2 补全 — 任一漏掉就"切了 scope 但某处仍读/写 default"）**：
| 位置 | 现状 | 必须改 |
|---|---|---|
| `main.py:5401→5419 间` | 得 `_msg_sid` | 插 resolve，覆盖 `_msg_sid`+`text` |
| `main.py:5419` | `cmm.is_enabled(_msg_sid)` | 用 effective |
| `main.py:5426` | `code_mode_suggest` payload | 发 effective |
| `main.py:5442` | `reset_auto_resume_attempts(_msg_sid)` | 用 effective |
| `main.py:5465` | append user | effective |
| `main.py:5667` | assemble | effective |
| `main.py:6002` | 工具 session context 注入 | effective |
| `main.py:6767/6770/6771/6786/6837` | `_chat_inflight` / `_auto_resume_redispatchers` / followup callback | 全 effective |
| `main.py:3405` `_broadcast_default_chat_peers` | 硬编码只广播 `payload.session_id=="default"` | **改 group 语义**：定义"同组会话"（主桌宠窗+消息面板共享同一 effective_sid 组），按组广播；否则切 `task-*` 后多窗口同步失效 |
| voice `:493/494/512/616/706` | `self.session_id` | 全 effective（§4）|

**前端接入点**：后端发 `session_switched`/`task_session_started` 事件；`App.tsx:552` 宠物窗硬编码 `default`、`MessagePanelRoot.tsx:436` InputBar 固定 `SID` → 响应切换、显示当前 scope、提供"新话题"按钮 + "回上个话题"退路。**无 DB 迁移**（messages 已按 session_id 存）。

---

## §6 T1-2：L2 降级 external memory（page-in，兜底普通回复漂移）

- `MemoryPolicy`（`bundle.py`）加 `l2_page_in: Literal["always","followup","off"]="always"`；`policy.py::_to_policy` 同步解析（隐藏必改点）。
- `MemoryComponent.provide`（`memory.py`）在 `manager.retrieve()` 前：
  ```py
  if policy_memory.l2_page_in == "off": call_policy["l2_top_k"] = 0
  elif policy_memory.l2_page_in == "followup" and not _starts_with_anaphora(ctx.user_message): call_policy["l2_top_k"] = 0
  ```
- **默认 profile**：`task`/`web_search`/`command` 设 `followup`；`recall`/`chat`/`emotion` 保 `always`。
- **`/continue` 具体改点（R2-D 补）**：① `main.py` resolve 阶段解析 `/continue` 前缀 → 本轮 `TaskScopeDecision.reason="continue"`；② 透传到组装层强制本轮 `l2_page_in="always"`（经 ctx 或 policy override）；③ 前端"同会话/继续"按钮发 payload `{force_l2:true}`；④ strip `/continue` 正文。
- ⚠️ R2-D：`l2_top_k=0` 不炸（`manager.py:147` 支持），但跳过 `memory.py:179-201` 的 reasoning_content 回填 → **连续 thinking 场景必测**。
- 单测：3 档 + 默认 profile + anaphora 豁免 + reasoning_content 场景。

---

## §7 测试矩阵（功能只能多做：单测 + 真机）

**单测**：
- T0-1：原话夺权回归（topic≠user_request → plan/report/slug/**search owner/`_SEMANTIC_SCORER`/`_llm_rerank`/`_gap_followup_queries`/fanout root** 全取 user_request）+ BC（user_request=None 等旧 topic）。
- T0-2：fanout 隔离不变量断言。
- T0-3：默认 policy 不调 embedder。
- T0-4：voice `loop_user_request` 透传 + 四处 sid 一致。
- T1-1：`resolve`（/new strip、新 sid、continue 退路）+ **`/new` 全链路 effective sid**（5419/5442/5465/5667/6002/6767 + 广播 group）。
- T1-2：page-in 3 档 + 默认 profile + reasoning_content + `/continue` 强制 always。

**真机（windows-mcp，沿用复现口径，判 log `p5s2 topic` + 落盘 slug，不只看 Fix B 锚点）**：
1. **相邻领域**（★头号）：灌钠离子 → 发"固态电池" → 不漂钠离子。
2. 跨域：区块链/Rust 不漂 CATL。
3. **voice**：语音发无关新主题 → deepresearch 不漂。
4. `/new` 切场：A → `/new` → 无关 B → B 上下文不含 A；多窗口（桌宠+面板）同步一致。
5. 追问连续性不误伤：A 连续追问（含"再对比"非代词 + `/continue`）不丢上下文。
6. fanout：漂修好后 6 子代理研究**你发主题**的 6 子问题。
7. **关 Tier2 前后**普通回复不漂（验 §8 顺序无空窗）。
8. **auto-resume sentinel** 不重新引入 deepresearch 漂移（R2-E2）。

---

## §8 落地顺序（R2-E1 修正：先 T1 兜底再关 Tier2）

1. **T0-1 + T0-2 + T0-4**（deepresearch 原话夺权 + fanout 断言 + voice 补 Fix B）— 小改低风险，直灭"报告/6 份 fanout/语音漂主题"。
2. **T1-1a**（`/new` 显式 + UI + 落库前切 sid + 全链路 effective + 广播 group + 前端事件）— 根治层 1。
3. **T1-2**（L2 page-in + `/continue` 兜底）。
4. **T0-3 关 Tier2**（★**T1-1/T1-2 真机验证通过后**才默认关，避免过渡期普通回复漂移回潮）。
5. **T1-1b**（自动检测 shadow log）。
- 每阶段：单测先行 → 真机复验（**相邻领域 + 多窗口 + sentinel 必验**）。
- **auto-resume sentinel 处理（R2-E2）**：`_run_chat(..., "<<auto_resume>>")` 时 `loop_user_request=None`——明确策略二选一：(a) **禁止 sentinel 轮触发 deepresearch**（gate 掉），或 (b) 把"被恢复任务的原始 user_request"透传进 sentinel 轮。本 plan 采 (a)（更简单、零漂移）：在工具 dispatch 处，若本轮 `loop_user_request is None` 且工具是 deepresearch → 拒绝并提示需显式请求。单测覆盖。

---

## §9 codex R1 已纳入（防遗忘）
T0-2 假设错→验证+断言；漂移两层；T0-1 全替换表；voice 缺 Fix B；关 Tier2 漏洞；自动切 session 误伤→/new 优先；前端硬编码→事件+按钮；BC user_request=None。

## §10 codex R2 已纳入（防遗忘）
①§1 漏改 9 处（fanout root/search owner/topic_keywords/velocity/gap/semantic/rerank/no-results/最终 report）+ 明确"不该改"清单；②§5 插入点精确到 5401→5419 间 + 早期 sid（5419/5426/5442）+ in-flight/redispatch/callback（6767/6770/6771/6786/6837）+ `_broadcast_default_chat_peers`(3405) 改 group 语义 + voice 全链路（493/494/512/616/706）；③§6 `/continue` 具体改点 + reasoning_content 必测；④§8 顺序改"T1 兜底后再关 Tier2" + auto-resume sentinel 禁触发 deepresearch。
