# 01 · Q1 详解 — DeskPet 当前问题处理全链路 + 结构性短板

> 事实基础来自对 backend 真实代码的考古（非文档自述）。所有 file:line 以 2026-06-24
> master 为准；少数行号为"邻近锚点"，实现时以函数名 + 上下文为准（见 04 文档逐处复核）。

---

## 1. 完整线性链路（带 file:line）

### A. 预处理链（PRE-LOOP）— `backend/main.py`

| 步 | 位置 | 做什么 |
|---|---|---|
| 入口 | `main.py:_run_chat`（WS handler `/ws/control`，chat/chat_v2 消息，约 :3976 触发 → :6012 主体） | 触发 `_run_chat_with_timeout` → `_run_chat` 任务 |
| [1] 持久化 | `main.py:~6033` | 用户消息 → SessionDB + VectorWorker enqueue |
| [2] Sentinel | `main.py:~6032` / 替换 :6284-6301 | `<<auto_resume>>` / `<<supervisor_followup>>` 检测 → 替换成明确续跑指令 |
| [3] 能力门 | `main.py:~6065` → `agent/capability_gate.py:classify_request()` | rule-first 正则匹配"生成图片/视频/3D"等 + haiku LLM fallback。REFUSE → 直接 `chat_v2_final` 不进 loop |
| [4] 组装 | `main.py:~6151` → `deskpet/agent/assembler/assembler.py:assemble()`（:124） | 见下方 B |
| [5] Supervisor hint | `main.py:~6303` | `nudge_queue.pop_all()` → 插 system 消息 |
| [6] 摘要质量回路 | `main.py:~6350`（WI-1B-4，flag off=BC） | 用户用"刚才/之前"困惑措辞 + 有历史压缩 → 重注任务态 snapshot |
| [7] ContextManager | `main.py:~6473` → `agent/context_manager.py` | per-model context window 解析构造 |
| [8] provider 链 | `main.py:~6500` → `llm/resolution.py:resolve_provider_for_session` | per-session binding 解析 |
| [9] **规划** | `main.py:~6692` → `agent/plan.py:maybe_extract_plan()`（:96） | **仅 code mode + msg>40 字符**，单次 Structured-Output LLM 出 1-8 步，发 `chat_v2_plan` + 注入 system；`plan_confirm_gate=True`(默认 off) → 挂起等确认 |
| [10] 装配 | `main.py:~6979` → `build_agent()`（:924）→ `_agent.run()` | 启动 AgentLoop async generator |

### B. 组装期 — `deskpet/agent/assembler/`

`ContextAssembler.assemble()`（`assembler.py:124`）：
1. **TaskClassifier.classify()**（`classifier.py:242`）三级分类 8 类 task_type：
   - tier-1 rule：正则匹配（code/recall/web_search/plan/emotion…）
   - tier-2 embed：BGE-M3 余弦 vs `exemplars.jsonl`（阈值 0.75）
   - tier-3 llm：`claude-haiku-4-5` 判 8 类（**LLM 调用**）
   - → `ClassifierResult{task_type, path, confidence}`
2. `policy = _policies[task_type]`（决定哪些 component 跑）
3. **ComponentRegistry.fanout()** 并发 6 个 Component（1500ms 超时）：
   - `PersonaComponent`（人格 + code-mode 工作流提示，`components/persona.py:48`）
   - `MemoryComponent`（L1/L2 记忆召回 + 历史）
   - `ToolComponent`（enabled_toolsets 过滤后工具 schema）
   - `SkillComponent`（技能列表 + 自动披露，`components/skill.py:74`，embedding sim ≥ 0.55 inline body）
   - `TimeComponent` / `WorkspaceComponent`
4. `BudgetAllocator.allocate()` 按 priority 裁剪
5. → `ContextBundle{frozen_system, memory_block, skill_prelude, tool_schemas, history}`（`bundle.py:39`）→ `build_messages()`

> **关键观察**：分类结果 `task_type` **只用于选 policy（展示哪些组件）**，**不反馈给 LLM**，也不驱动任何"针对该问题类型的分析"。

### C. ReAct 主循环 — `agent/agent_loop.py:AgentLoop.run()`（:635）

```
[11] 循环前一次性：WI-4a goal anchor 注入（:717-739，需 /goal）
FOR iteration in 1..max_iter（Companion=16, Code=50）:
  [12] drain 子代理 completion_queue（:770-805，WI-3.3）
  [13] TerminationGate.allows_call()（:820 → termination.py）轮数/墙钟/cost 硬上限
  [14] ContextManager.check_budget()（:856）BLOCK>95% / WARN>80%
  [15] WI-4.0 compaction（:906-933，compressor 非 None）token>window*0.75 → 中段 LLM 摘要
  [16] 第 10/20/30 轮注入 self-check 三级升压（:1188，_SELFCHECK_TIER1/2/3）
  [17] LLM 调用（:1294）流式 → AssistantDelta/Message；record_turn(cost)
  IF stop_reason == "tool_use":
    [18] 签名重复检测（:2031）同名同参≥3 次 → 抑制+nudge
    [19] 工具分发（:2098-2115）asyncio.gather 并发 → _dispatch_one → execute_tool
         → registry.py:execute_tool（:618）查工具→disabled→熔断→权限 gate（WS Future）
         → handler → 超时 → envelope → record_tool_result（截断+ref-store）→ ToolResultEvent
  ELSE（模型想收尾）:
    [20] 【守门1】completion_probe（:1401，规则，默认 on）查 SessionDB code todos 未完 → nudge+continue（上限 2）
    [21] 【守门2】VerifyGate.check()（:1481，默认 off）regex 抽 claim → 对账 receipt ledger → 不过则 rebound；第3次失败 → ephemeral 子代理救援 → verify_exhausted 强退
    [22] 【守门3】goal_checker（:1756，需 /goal）LLM-judge done/hint → 未达成 nudge+continue（上限 goal.max_iterations 默认 10）
    [23] 【守门4】external_evaluator（:1869，默认 off）跨人格 QA 评分 0-10，<6 → ErrorEvent("evaluator_revise")
    [24] 全过 → record_final_answer() → FinalEvent
```

### D. 收尾 — `backend/main.py:~7100+`

- 事件 → WS 转发（chat_v2_final/delta/tool_call/tool_result）
- 后置（:7453）：意图记忆写入（WI-A1）/ artifact → ArtifactCard / Auto-Resume / Supervisor followup

---

## 2. 关键文件清单

| 层 | 主文件 | 关键 function/class | 行号 |
|---|---|---|---|
| WS 入口 + 装配 | `backend/main.py` | `_run_chat` / `build_agent` / 事件转发 | :6012 / :924 |
| ReAct 主循环 | `backend/agent/agent_loop.py` | `AgentLoop.run()` | :635 |
| 任务分类 | `backend/deskpet/agent/assembler/classifier.py` | `TaskClassifier.classify()` | :242 |
| 组装期 | `backend/deskpet/agent/assembler/assembler.py` | `ContextAssembler.assemble()` | :124 |
| bundle 格式 | `backend/deskpet/agent/assembler/bundle.py` | `ContextBundle` / `TASK_TYPES` | :39 |
| 技能披露 | `backend/deskpet/agent/assembler/components/skill.py` | `SkillComponent.provide()` | :74 |
| 计划生成 | `backend/agent/plan.py` | `maybe_extract_plan()` | :96 |
| 终止裁决 | `backend/agent/termination.py` | `TerminationGate` | — |
| 上下文管理 | `backend/agent/context_manager.py` | `check_budget()` / `record_tool_result()` | — |
| 历史压缩 | `backend/agent/history_compactor.py` | `maybe_compact()` | — |
| 能力门 | `backend/agent/capability_gate.py` | `classify_request()` | — |
| 工具注册/分发 | `backend/deskpet/tools/registry.py` | `execute_tool()` / `schemas()` | :618 |
| 守门1 completion | `backend/main.py`（closure） | `_completion_probe` | :6873 |
| 守门2 verify | `backend/deskpet/agent/verify_gate.py` | `VerifyGate.check()` | — |
| 守门3 goal | `backend/deskpet/agent/goal_checker.py` | `GoalChecker.check()` | — |
| 守门4 evaluator | `backend/deskpet/agent/external_evaluator.py` | `ExternalEvaluator.evaluate()` | — |
| 结构化反思 | `backend/deskpet/agent/reflection.py` | `StructuredReflection` / `_REFLECTION_INSTRUCTION` | :27 |
| 任务图 | `backend/deskpet/agent/task_graph.py` | `TaskGraph` | — |
| 配置 | `backend/config.py` | feature flags 默认值 | — |

---

## 3. 现有"规划与反思"能力盘点

| 机制 | 文件 | 作用 | 默认 |
|---|---|---|---|
| TaskClassifier 三级分类 | `classifier.py:242` | 8 类 task_type 驱动 context policy | **ON** |
| maybe_extract_plan | `plan.py:96` | code mode 进 loop 前单次 LLM 出 ≤8 步 | ON（code+msg>40） |
| Goal anchor（WI-4a） | `agent_loop.py:717` | /goal 激活后每轮注入目标锚定 | 需 /goal |
| Self-check 三级升压 | `agent_loop.py:171` | 第 10/20/30 轮注入反思/警告/强停 | ON（轮数到） |
| StructuredReflection | `reflection.py:27` | verify rebound 时强制 5 段 JSON 反思 | **OFF** |
| GoalChecker | `goal_checker.py` | end_turn LLM-judge done/hint | 需 /goal wired |
| TaskGraph | `task_graph.py` | DAG 多子任务图 | 需 spawn 工具激活 |
| plan 持久化 | `main.py:6750`+SessionDB | 挂起确认时持久化，F5 恢复 | OFF（plan_confirm_gate） |
| DeepResearch 内部规划 | `research_tools.py` | deepresearch 工具内 plan→搜→打分→synth | 工具触发时 |
| Preference memory | `preference_memory.py` | 相似 plan 历史批准 → auto-confirm | OFF |
| Auto-Resume | `auto_resume.py` | max_iter/budget → 自动续跑 | ON |
| VerifyGate | `verify_gate.py` | end_turn regex 抽 claim 对账 receipt | **OFF**（dev strict） |

---

## 4. 六条结构性短板（优化靶心）

1. **Companion 无显式"主要矛盾分析"**——分类只选 policy，不分析问题核心难点，LLM 每轮裸决策下一步。
2. **理解全靠单次 LLM 前向**——无"重述意图确认 / 主动澄清"结构化前置；`ask_clarification` 靠 persona 软提示 + code mode + LLM 随机调。
3. **规划只在 code mode 且 ≤8 步**——无递归分解；计划注入后无强制遵循机制；无完整 PDCA 环。
4. **三道真反思守门默认全关**——StructuredReflection/VerifyGate/external_evaluator 默认 off，生产跑裸 ReAct + completion_probe。
5. **分解是事后工具不是前置分析**——TaskGraph/spawn 由 LLM 随机调，非流程强制"先分解再执行"；子代理扁平 fan-out depth=1。
6. **缺"调查→假设→验证"环**——当前"理解(弱)→执行(强)→验证(多数关)"，"先想清楚再动手"只靠 persona 软建议，无结构约束 → 易短路径偏置（凭记忆直接答）。

> 这 6 条正是七步流水线（03 文档）要逐一补齐的目标：短板 1→Step 3；2→Step 1；3→Step 4；4→Step 6；5→Step 3/4；6→Step 2。
