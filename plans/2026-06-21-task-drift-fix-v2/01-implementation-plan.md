# 任务漂移 v2 — 实现 plan（含代码实现细节，经 codex R1 硬化）

> **日期**: 2026-06-21　**状态**: 🔨 实现细节定稿中（codex 多轮挑战 → 收敛至 EXECUTABLE-AS-IS）
> **配套**: 调研与方向见 [00-research-and-best-fix.md](./00-research-and-best-fix.md)
> **行号声明**: 下列 `file:line` 来自 codex R1 只读核读（2026-06-21），实现时以 `grep` 复核为准（代码会漂）。

---

## §0 链路澄清（漂移精确发生在哪两层 — 决定改点优先级）

**层 1（上游·主 agent loop 选 topic 时已漂）**：
`main.py:5465` 先把用户消息 append 进 `_sid` → `main.py:5667` 用同 `_sid` assemble，`MemoryComponent` 把 L2 旧历史提升为真实 history → `bundle.py:313` 把 history 插在当前 user **前** → `main.py:6322` 调 `AgentLoop.run(..., loop_user_request=_text)`。Fix B 在 `agent_loop.py:64` 覆盖注入 `user_request`，**但 `topic` 已由带旧 L2 的主 loop 生成**（漂了）。

**层 2（下游·deepresearch 内部继续信漂 topic）**：
`research_tools.py:1369` `_ur=(user_request or topic).strip()`，**但后续大量逻辑仍直接用 `topic`**：plan prompt `REFINED TOPIC`[542]、兜底 subquestion[1423/1437]、query expansion[1464]、synth 标题[564/573]、fanout synth 标题[633]、fallback 标题[1153/1157/1964/1976]、保存 slug[2158/2193]。→ 漂移被**固化进报告/sub_questions/文件名**。

**★ codex 修正的关键假设**：**fanout 子代理不继承主会话历史**。`_run_subagent_fanout`[1162] 的 `_run_one()` 直接递归 `deepresearch(q, ..., user_request=q, scheduler=None, skip_plan=True)`[1192-1204]；`subagent_scheduler.py:67` 只管并发/进度，**不构造 messages、不读 parent 历史**。所以"6 份错主题报告"的根因是**上游 sub_questions 已由漂 topic 生成**，不是子代理继承了脏上下文。→ 原 00-plan 的 T0-2"改子代理隔离"**作废**，改为"验证已隔离 + 让 sub_questions 从原话来（T0-1 覆盖）"。

**结论**：T0-1（让 deepresearch 全程以原话为准）覆盖层 2 + fanout；T1-1（会话切分）才治层 1（主 loop 不被旧 L2 污染）。**两者都要做**（层 1 不治，主 loop 选的 topic 仍漂，只是 T0-1 让报告标题/slug 被原话拉回——是纵深，非根治）。

---

## §1 T0-1：deepresearch 原话夺权（★核心，diff 级全变量替换表）

**目标**：deepresearch 内部一切"主题"派生自 `user_request`（原话），LLM 给的 `topic` 降为 untrusted candidate（冲突即弃）。

**改 `research_tools.py`**：
1. `:1369` 之后新增锚定变量：
   ```py
   request_topic = _ur            # canonical subject (authoritative)
   llm_topic = topic              # untrusted candidate; may be drifted
   ```
2. **全替换表**（把"当主题用"的 `topic` → `request_topic`）：

   | 位置 | 现状 | 改为 |
   |---|---|---|
   | sub_questions 兜底 `:1423/:1437` | `sub_questions=[topic]` | `[request_topic]` |
   | query expansion `:1464` | `_expand_queries(llm_call, topic, ...)` | `request_topic` |
   | `ResearchReport(topic=...)` | `topic=topic` | `topic=request_topic` |
   | `_SYNTH_PROMPT.format` `:1787` | `topic=topic` | `topic=request_topic` |
   | `_FANOUT_SYNTH_PROMPT` `:633` 标题 | `# {topic}` | `# {request_topic}` |
   | `_no_results_template`/`_passages_only_fallback` `:1153/1157/1964/1976` | `topic` | `request_topic` |
   | handler 保存 `:2158/2160/2193` | `_save_report(topic,...)`/index/`title_slug(topic)` | `save_topic = report.topic or (user_request or topic)` 后全用 `save_topic` |

3. **`_PLAN_PROMPT`（:536）改写**（让 sub_questions 不被漂 topic 带偏）：
   ```
   ORIGINAL USER REQUEST (authoritative; derive ALL sub-questions from THIS):
   {user_request}

   CANDIDATE TOPIC FROM TOOL ARGS (untrusted; may be drifted — IGNORE if it conflicts):
   {topic}
   ```
   `_SYNTH_PROMPT`(:564/573) 报告骨架 `# {topic}` → `# {request_topic}`。
4. **禁止**用 LLM 二次"清洗/提炼"标题（会重新引入漂移面）。slug 已有 `title_slug(..., max_grapheme=40)`，长口语可接受；要更短只做**确定性** `collapse whitespace + truncate`，不做语义提炼。

**单测**（`backend/tests/`）：
- 改 `test_task_drift_fixb.py:14` 对 prompt 文案的断言（新增 CANDIDATE/authoritative 字样）。
- **新增回归**：`topic="CATL/宁德"` + `user_request="帮我深度调研 Rust Tokio…"` → 断言 plan prompt、`report.topic`、report markdown 标题、`_save_report` slug **全取 Rust**，topic 不出现。
- **BC**：`user_request=None`（老调用）→ `request_topic=topic`，行为等同旧逻辑（零回归）。

---

## §2 T0-2：fanout 验证隔离（修正为"验证 + 防污染"）

fanout 已隔离（§0），**无需改隔离**。只做：
- **测试断言**（`test_deepresearch_subagent_fanout.py:195` 附近）：`scheduler is None`、`skip_plan is True`、子 run 的 `user_request == 各 sub_question`——锁死"子研究不带主历史"这一不变量，防未来回归。
- 不强行把子 run `user_request` 改成"root request + sub_question"（会让搜索 query 变长变混、降召回）。sub_questions 的正确性由 T0-1（plan 从 `request_topic` 派生）保证。

---

## §3 T0-3：关 Tier2 相似度截断（yaml only，不删代码）

- `policies/default.yaml` 8 处 `topic_shift_gate: true → false`（`:44/61/77/105/120/135/151/167`）。
- **不删** `memory.py` 的 `_topic_similarity`/`_lexical_topic_shift`/gate 逻辑（`:109/122-153`）——opt-in 单测仍覆盖。
- Tier1（`relabel_l2`/`anchor_current` 产 label/nudge，`memory.py:174-230` + `bundle.py:317`）**保留**。
- 单测：改默认 policy 期望（确认默认 `topic_shift_gate=False`、不调 embedder）；保留显式 `MemoryPolicy(topic_shift_gate=True)` 的 opt-in 测试。
- ⚠️ **遗留漏洞（由 T1 兜底）**：关 Tier2 后，**非 deepresearch 的普通回复漂移**只剩 Tier1 软锚定，不能根治 → 必须靠 T1-1（会话切分）或 T1-2（L2 page-in）补。

---

## §4 T0-4：voice 路径补 Fix B（★功能补漏 — 只能多做）

codex 发现 **语音链路缺 Fix B**：`pipeline/voice_pipeline.py:616` `loop.run(messages, session_id=self.session_id)` **没传 `loop_user_request`** → 语音触发的 deepresearch 没有原话注入，会回到旧漂移风险。
- 改：`:616` 补 `loop_user_request=text`（text=本轮语音转写的用户原话）。
- `:493` 语音也在 append 前 resolve sid（配合 T1-1）。
- 单测：mock voice pipeline 断言 `loop_user_request` 透传。

---

## §5 T1-1：会话作用域切分（根治层 1，结构化信号优先）

**铁律：必须在"落库 append 前"切 sid**。现状 `main.py:5401` 得 `_msg_sid` → `:5465` 立即 append user → `:5667` assemble。**若 assemble 后再切，第一轮仍污染** → 切点必须在 `:5401` 之后、`:5465` 之前。

**新增** `backend/deskpet/session/task_scope.py`（仿 `code_mode/state.py` 模板）：
```py
@dataclass
class TaskScopeDecision:
    effective_sid: str
    created: bool
    reason: str           # "explicit_new" | "ui_button" | "continue" | "default"
    stripped_text: str    # 去掉 /new 前缀后的正文
class TaskSessionManager:
    def resolve(self, base_sid: str, text: str, explicit_new: bool) -> TaskScopeDecision: ...
```

**信号分级（先做显式，自动默认关）**：
- **T1-1a（默认开，先做）**：显式 `/new ...` 命令 + 前端 payload `{new_session:true}` + **UI"新话题"按钮** → strip `/new`，起新 `effective_sid`（如 `default-<ts>` 或 `task-<uuid>`）。
- **T1-1b（默认关 / 仅 shadow log，后做）**：自动"完整祈使式新任务"检测——**误判风险高**（连续追问同一研究会被误切），**先只 shadow log 观测命中率**，不真切；高置信场景（deepresearch 类）后续灰度。

**后端接入点清单**：
- `main.py:5401` 后切 `_msg_sid → effective_sid`（append/assemble 前）。
- `main.py:6767` `_chat_inflight` 用 effective sid。
- `main.py:6002` 工具 session context 注入用 effective sid。
- `pipeline/voice_pipeline.py:493` 语音同样 append 前 resolve。
- L2 隔离自动达成：`get_messages(effective_sid)` 新 scope 历史为空 → 不污染。

**前端接入点（否则可见性回归）**：
- 后端发 `session_switched` / `task_session_started` 事件。
- `App.tsx:552` 宠物窗硬编码加载 `default`、`MessagePanelRoot.tsx:436` InputBar 固定 `SID` → 需响应切换/显示当前 scope，并提供"新话题"按钮 + "回到上个话题"退路。
- **无 DB 迁移**（messages 已按 `session_id` 存）。

**多窗口兼容**：`_broadcast_default_chat_peers`（`main.py:3173/3190`）按 effective_sid 广播；定义"同组会话"语义（主桌宠窗 + 消息面板共享同一 effective_sid）。

---

## §6 T1-2：L2 降级 external memory（page-in，兜底普通回复漂移）

- `MemoryPolicy`（`bundle.py`）加字段 `l2_page_in: Literal["always","followup","off"] = "always"`；`policy.py::_to_policy` 同步解析（隐藏必改点）。
- `MemoryComponent.provide`（`memory.py`）在 `manager.retrieve()` 前：
  ```py
  if policy_memory.l2_page_in == "off":
      call_policy["l2_top_k"] = 0
  elif policy_memory.l2_page_in == "followup" and not _starts_with_anaphora(ctx.user_message):
      call_policy["l2_top_k"] = 0
  ```
- **默认 profile**：`task`/`web_search`/`command` 设 `followup`（或 `off`）；`recall`/`chat`/`emotion` 保 `always`（连续性）。
- ⚠️ 风险：非代词的同任务追问（"再对比一下价格""给个表格"）可能丢 L2 → 用 `/continue`、UI"同会话"按钮、或显式 follow-up 词表补偿（列为 T1-2 子项）。
- 单测：3 档行为 + 默认 profile + anaphora 豁免。

---

## §7 测试矩阵（功能只能多做：单测 + 真机都要）

**单测**：
- T0-1 原话夺权回归（topic≠user_request → 全取 user_request）+ BC（user_request=None 等旧 topic）。
- T0-2 fanout 隔离不变量断言。
- T0-3 默认 policy 不调 embedder。
- T0-4 voice `loop_user_request` 透传。
- T1-1 `TaskSessionManager.resolve`（/new strip、新 sid、continue 退路）。
- T1-2 page-in 3 档 + 默认 profile。

**真机（windows-mcp，沿用复现口径）**：
1. **相邻领域**（★头号，之前失效点）：先灌钠离子历史 → 发"固态电池" → grep `p5s2 topic` + 落盘 slug **= 固态电池不漂钠离子**。
2. 跨域：区块链 / Rust，不漂 CATL。
3. **voice**：语音发无关新主题 → deepresearch 不漂。
4. `/new` 切场：发 A → `/new` → 发无关 B → B 的上下文不含 A。
5. 追问连续性不误伤：A 主题连续追问（含"再对比"非代词）不丢上下文。
6. fanout：漂修好后 6 子代理研究的是**你发主题**的 6 子问题。
判定靠 backend log（`p5s2_tool_call_args_dump` topic + 落盘文件名 slug），**不只看 Fix B 锚点**（它触发但治不了层 1）。

---

## §8 落地顺序
1. **T0-1 + T0-2 + T0-4**（deepresearch 原话夺权 + fanout 隔离断言 + voice 补 Fix B）— 小改、低风险、直灭"报告/6 份 fanout 漂主题 + 语音漂"。
2. **T0-3**（关 Tier2，止损负价值软门控）。
3. **T1-1a**（`/new` 显式 + UI 按钮 + 落库前切 sid + 前端事件）— 根治层 1 主 loop 污染。
4. **T1-2**（L2 page-in 兜底普通回复漂）+ **T1-1b**（自动检测 shadow log）。
5. 每阶段：单测先行 → 真机复验（**相邻领域必验**）。

---

## §9 codex R1 已揪出并已纳入的问题（防遗忘）
- ✅ T0-2 假设错（fanout 未继承主历史）→ 改为验证+断言。
- ✅ 漂移两层澄清（主 loop topic + deepresearch 内部信 topic）。
- ✅ T0-1 不能只改 _PLAN_PROMPT（slug/index/fallback/synth 标题全要改）→ 全替换表。
- ✅ voice 缺 Fix B（功能漏做）→ T0-4。
- ✅ 关 Tier2 留普通回复漂移漏洞 → T1 兜底。
- ✅ 自动切 session 误伤追问 → /new 显式优先、自动默认关。
- ✅ 前端硬编码 default，静默切 sid 可见性回归 → 前端事件 + UI 按钮。
- ✅ BC：user_request=None 等同旧 topic。
