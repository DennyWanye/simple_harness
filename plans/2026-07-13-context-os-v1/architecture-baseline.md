# DeskPet Context OS V1：当前架构基线

> 校准对象：工作树 `F:\projects\deskpet`，HEAD `e4a3bdc7528066e7d6a290004eeeb2eee5e5edf6`。
>
> 校准日期：2026-07-13。工作树存在大量用户修改，因此本文件只读记录当前事实，
> 不改写脏状态中的 `ARCHITECTURE/ARCHITECTURE.md`。

## 1. 代表性生产调用链

文字聊天的上下文链路是：

```text
backend/main.py::_run_chat
  0. capability_gate（可能独立调用 LLM 并直接结束请求）
  1. 未短路时持久化当前 user row
  2. ContextAssembler.assemble(... current_message_id=...)
       -> classifier 也可能是辅助 LLM call
  3. ContextBundle.build_messages(
       frozen system,
       skill prelude,
       dynamic memory,
       L2 history,
       late current-request nudge,
       current user)
  4. post-assembly injectors
       -> supervisor hint / summary-quality L1 reinject / code reliability
       -> intent memory / plan system message
  5. ContextManager.prepare_chat_messages(...)
       -> history_compactor（当前仍可能执行 preflight 摘要）
  6. ProblemHandlingPipeline / plan injectors
  7. build_agent(... compressor=ContextCompressor ...)
  8. AgentLoop.run()
       -> goal/todo/subagent/self-check/evidence/completion control events
       -> 每轮预算检查
       -> 旧 tool result microcompact
       -> pre-compaction L1 append
       -> ContextCompressor 结构化摘要
       -> Skill body remount
       -> registry 重新生成并按 tool name 过滤最终 schemas
       -> provider attempt: chat_with_tools(messages, actual_tool_schemas_or_none)
       -> tool call/result 继续追加到 working_messages
```

代码证据：

- `backend/main.py:7365-7406`：assemble 和 `build_messages`。
- `backend/main.py:7189-7273`：capability gate 在 Assembler 前可能调用 LLM 并短路。
- `backend/main.py:7443-7524, 7920-7946, 8053-8103, 8180-8307`：Assembler 后仍有多类 system/control 注入。
- `backend/main.py:7930-8085`：构造 `ContextManager` 并运行 preflight chat prep。
- `backend/main.py:8490-8515`：把 reactive compressor 注入 AgentLoop。
- `backend/agent/agent_loop.py:976-989`：从 registry 获取本轮 tool schemas。
- `backend/agent/agent_loop.py:1052-1125, 1429-1476, 1842-2013`：loop 内 goal/todo/self-check/evidence/completion 等动态注入。
- `backend/agent/agent_loop.py:1275-1380`：每轮 reactive compaction。
- `backend/agent/agent_loop.py:2724-2978`：tool call/result 进入工作历史。

## 2. 当前 assembly 数据模型

`ComponentRegistry.fanout()` 并行调用每个组件，每个组件只能返回一个 `Slice`。
`ContextAssembler._stitch()` 依据 `Slice.bucket` 合并为：

```text
frozen_system
skill_prelude
memory_block(dynamic)
history(L2 meta side-channel)
late_system_nudge
tool_schemas
```

当前关键组件：

| 组件 | 当前 bucket / priority | 当前行为 |
|---|---:|---|
| persona | frozen / 90 | 注入人格、model、base URL；code mode 还注入 project root。 |
| memory | dynamic 或 frozen / 100 | 同一个 Slice 同时拼 L1 与 L3；只要 L3 非空，L1 也整体成为 dynamic。L2 通过 `meta.l2_history` 旁路提升为真实 messages。 |
| skill | skill / 85 | 紧凑描述列表常驻；强语义匹配时正文自动内联；compact 后 AgentLoop 会 remount 已调用正文。 |
| tool | schemas / 60 | 主要产出 schema；文本 bucket 基本为空。 |
| preference | dynamic / 85 | 每轮读取偏好画像。 |
| workspace/workspace_memory | dynamic / 40/45 | code/workspace 场景注入摘要。 |
| time | dynamic / 10 | 当前时间。 |

代码证据：

- `backend/deskpet/agent/assembler/bundle.py:251-325`。
- `backend/deskpet/agent/assembler/assembler.py:330-394`。
- `backend/deskpet/agent/assembler/components/memory.py:205-284`。
- `backend/deskpet/agent/assembler/components/skill.py:48-265`。

## 3. 已经具备、不得重做的能力

1. L2 newest-tail、当前 row 按 message id 精确去重；SessionDB 已持久化工具字段并尝试扩展完整组边界，但渲染层尚未完整 round-trip。
2. L3 独立超时，失败不再连带丢弃 L2。
3. 当前请求 late system anchor，避免历史覆盖本轮请求。
4. ContextCompressor 的七段结构化增量摘要、反射检测、目标锚定。
5. 旧 tool result microcompact、ref-store、重复文件读取 supersede。
6. tool call/result sanitize 与切分边界保护。
7. Skill 描述/正文渐进披露与 compact 后 remount。
8. `context_usage` provider usage ring 和基础 ContextTrace。
9. compaction、adaptive threshold、summary quality、size-aware microcompact 默认点亮。

相关回归入口包括：

- `backend/tests/test_p4s21_context_bundle_history.py`
- `backend/tests/test_l2_page_in.py`
- `backend/tests/test_deskpet_context_compressor.py`
- `backend/tests/test_compaction_bestpractice_upgrade.py`
- `backend/tests/test_deskpet_skill_remount_after_compaction.py`
- `backend/tests/test_p5s2_tool_result_truncator.py`
- `backend/tests/test_prompt_cache.py`

## 4. 当前主要缺口

### 4.1 一个 Slice 无法表达两个生命周期

`MemoryComponent` 把 L1 与 L3 合并到同一个 `combined` 文本，再根据是否存在 L3
决定整个 Slice 是 dynamic 还是 frozen。这使 L1 的“frozen”只是注释意图，不是请求层面的
真实缓存边界。

### 4.2 仍有两套有损历史压缩

`ContextManager.prepare_chat_messages()` 在进入 AgentLoop 前仍可能调用
`history_compactor.compact_messages()`；AgentLoop 内又由 `ContextCompressor` 生成另一种
结构化摘要。旧计划曾提出收敛，但当前生产代码尚未完成该收敛。

此外，`ContextCompressor._partition()` 当前会把所有 `role=system` 消息抽出并整体前置，再拼接
head/summary/tail。这会把原本位于历史尾部或 loop 中段的 current-request anchor、plan、
completion/evidence/self-check 等 control event 提升到 prefix，compact 后因果位置会漂移。

### 4.3 pre-compaction flush 信息不足且写错层

当前 flush 只提取 goal text 和最后一条 user 文本，拼成一条 salience=0.6 的
`[task-state]` 字符串追加到全局 L1 `MEMORY.md`。它没有 decisions、completed、pending、
artifacts、blockers、receipt outcome，也没有 revision/idempotency；而同一 run 只靠内存 latch
限频。它既不足以恢复任务，又可能长期污染用户级记忆。

### 4.4 Goal、GoalTask、Workflow 和普通长任务没有统一只读投影

- `SessionGoal` 有 text/progress/criteria/subgoals，但 `subgoals` 不持久化，当前 producer 也不明确。
- `goal_tasks` 已有 status/dependency/claim 数据，是更可靠的 pending 来源。
- Durable Workflow `ProposalStateV1` 已有 original request、active plan/step/todos、evidence、
  pending/committed tool results，但普通 ReAct context 不消费统一投影。
- 普通长任务没有 durable task snapshot。

因此不能再创造一套与 Goal/Workflow 竞争的“权威 ledger”；需要建立 projection，并只为
派生/普通任务补 session-scoped snapshot。

### 4.5 初始预算与真实请求不一致

- 生产 assembler 当前在 `backend/main.py:2144-2150` 固定用 `context_window=32_000`、
  `budget_ratio=0.6`，不随本轮有效 provider/model 变化；类级 200K 默认不是生产接线值。
- L2 history 通过 meta side-channel 提升，未进入 slice budget。
- AgentLoop `check_budget()` 主要数 messages，不数单独传给 provider 的 tool schemas。
- provider 的上轮 `prompt_tokens` floor 只能从第二个 LLM call 起兜底，首个请求仍可能低估。
- `ContextManager` 在 provider chain 解析前固化 model info；fallback attempt 可能拥有不同窗口。
- 当前 budget BLOCK 早于 reactive compressor，直接终止时 compressor 没有自救机会。
- Context breakdown 里的工具成本仍是“工具数 × 60”的粗估。

### 4.6 诊断不是“实际发送请求”的账本

ContextAssembler decisions 是内存 ring；`context_usage` 有 provider 总 token，但 breakdown
重新从 facts、registry 和 SessionDB 推测，并不等于刚才实际发送的 fragments、schemas 与裁剪结果。
provider chain usage 还会选择第一个拥有任意 `last_usage` 的 provider，未绑定 request/turn，可能读到
上一轮；前端 ContextTrace 又使用 assembler decision ring 和手填 context window。因此三种数据源
不是同一次请求账本。

### 4.7 缺少路径生命周期

现有 workspace summary 和 Skill 匹配是任务/语义粒度，没有 Claude Code 式“读取某路径时加载、
compact 后再次命中再加载”的规则生命周期。该能力只应进入 code/workspace 场景，不能污染普通桌宠聊天。

### 4.8 跨轮 L2 工具组尚未真正 round-trip

SessionDB 能存 `tool_calls/tool_call_id`，newest-tail 读取也尝试保住工具组边界；但
`MemoryComponent` 当前只接纳 `user/assistant/system`，丢弃 `tool` role，没有复制 assistant 的
`tool_calls`，而空 content 的 tool-call assistant 还会被跳过。因此 AC-CTX-3 是新增能力，不能写成
“保持现状”。

### 4.9 最终 context producer 分散在 Assembler 之后

supervisor、summary reinject、intent、ProblemHandlingPipeline、plan，以及 AgentLoop 的
goal/todo/subagent/self-check/evidence/completion nudges 都会直接改变最终 provider 输入。
它们必须进入 producer inventory，并区分三种位置合同：

1. request prefix fragments：允许按 platform/stable/task/retrieved 排序与缓存。
2. conversation/tool groups：保持原始 transcript 与不可拆工具组顺序。
3. in-loop control events：靠近触发它们的 turn，不得被 compressor 按 system role 提升到前缀。

### 4.10 Assembler schemas 不是实际出站 schemas

main 只从 `ContextBundle.tool_schemas` 提取允许的工具名；AgentLoop 再从 registry 重新生成 schema 并
按名称过滤。因此实际请求报告只能在即时 provider attempt 边界冻结，不能把 bundle 中的 schema
对象当作最终请求事实。

### 4.11 多入口尚缺当前事实矩阵

AC-CTX-12 涉及 text/code/web/research/voice，但当前只校准了 text 与部分 voice 高层链路。
执行前必须逐入口记录：是否经过 Assembler、ContextManager、AgentLoop；谁生成实际 schemas；
谁持久化 history；谁做 compact。共享合同应基于这张矩阵迁移，不能先假设入口已经一致。

当前已校准的第一版矩阵如下；Task 0.1 仍需用 contract test 锁死并补齐旁路：

| scope/入口 | Assembler | ContextManager | AgentLoop / schemas | history owner | compact owner |
|---|---|---|---|---|---|
| text chat | `main._run_chat` 调用 | preflight + loop 注入 | `build_agent`；AgentLoop 从 registry 重建/过滤 | SessionDB，当前 user 先写 | 当前 preflight + reactive 均可能 |
| code mode | 与 text 相同，仅增加 code-mode config/tool/injectors | 与 text 相同 | 与 text 相同 | 与 text 相同 | 与 text 相同 |
| web/research | 不是独立 venue；是 text/code AgentLoop 内的 `web_search`/`deepresearch` 工具路径 | 继承父 loop | 继承父 loop；结果回到 transcript | 父 session SessionDB + working messages | 继承父 loop |
| voice | `backend/pipeline/voice_pipeline.py::_run_with_tools` 调 assembler/build_messages | 构造后直接注入 loop，不走 text preflight | 优先 `build_agent`，失败会回退裸 AgentLoop | voice 先写 SessionDB，assistant 持久化需在 Task 0.1 校准 | build_agent 路径可 reactive；裸 loop 无 compressor |

因此“多入口一致性”实质是：text/code/web/research 共享主 loop 但存在 scope-specific producer；voice 是
真正的第二入口，并且当前还存在裸 AgentLoop fallback。V1 不应虚构四套独立 pipeline。

### 4.12 稳定 Persona 中混入动态运行态

`PersonaComponent` 当前把 model、base URL，以及 code mode 下的 project root 放入 frozen 文本。
model/provider endpoint 属于 attempt/runtime 诊断信息，不应进入 persona prompt；project root 属于 task/path
scope。若不拆开，即使 L1 与 L3 已分离，AC-CTX-2 的稳定 fingerprint 仍会随运行态漂移。

### 4.13 provider call 不只来自 user-response AgentLoop

capability gate 可能在 Assembler 前调用 LLM 并直接返回；classifier、plan、summarizer/compressor 等也可能
调用 provider。实际请求账本必须带 `purpose`，至少区分 `capability_gate`、`classifier`、`planner`、
`compressor`、`agent_response`、`force_finish`。所有 provider calls 都有基础 attempt report；只有
`agent_response/force_finish` 要求完整 fragment/trim/cache 因果链，辅助调用记录实际 messages/tools/window/
usage 及其 purpose，避免把辅助成本和用户响应上下文混在一起。

### 4.14 毛选七步流水线的 Companion scope 存在契约漂移

配置和模块文档都声明 `ProblemHandlingPipeline` “仅 Companion 主线”，但当前
`backend/main.py:8177-8183` 的 pre-loop 条件只检查 pipeline enabled、非 sentinel，没有显式
`not _in_code_mode`。Code Mode UI 当前关闭使该分支通常不可达，但从运行时合同看仍是潜在漂移。
Context OS producer inventory 必须把七步流水线标为 `companion.control`，并用 contract test 钉住
code scope 不注入 intent/contradiction/evidence/self-check/convergence context。

### 4.15 压缩模型“名义 Haiku、实际主模型”且不可配置

`ContextCompressor` 构造参数默认 `model="claude-haiku-4-5"`，但生产传入的
`OpenAICompatibleAgentLLM` shim 明确忽略 `model` 参数，实际使用启动时绑定的
`local_llm or cloud_llm` provider。设置页只能改 `compact_at_pct`，不能选择压缩模型；provider chain/
per-session 模型与启动主模型不一致时，还可能出现“按 Session 窗口触发、由另一模型摘要”的错配。

### 4.16 SessionDB 保存全部，但每轮只装载固定 2～10 条

当前 policy 按 task type 固定 `l2_top_k`：chat=5、task/web=8、recall=10、code/plan=3、command=2。
即使有效窗口为 1M、整个 Session 只有 200K，也不会全量装载。`ContextCompressor` 只压缩已经进入
working messages 的中段，不能覆盖从未装入本轮的 SessionDB 旧消息。因此“数据库有完整历史”不等于
“Agent 本轮了解整个 Session”。

### 4.17 工具 schema 有两个 owner，存在 TOCTOU 漂移

`ToolComponent.provide()` 已经从 v2 registry 读取并按 task policy 产出精确 `tool_schemas`，但
`backend/main.py:8560-8590` 只提取名称传给 AgentLoop；`AgentLoop._run_impl()` 又在
`backend/agent/agent_loop.py:976-990` 调一次 `registry.schemas()` 并按名字过滤。两次读取之间的
`visible_when`、MCP 注册状态、环境变量和 tools config 都可能变化，因此 bundle、预算器、provider 与
ContextTrace 看到的集合没有单一真相。这也是当前 tool schema 无法稳定进入 context snapshot 的直接原因。

### 4.18 当前 `tool_search` 不是同轮渐进加载

`backend/deskpet/tools/tool_search.py` 搜索 `registry.all_specs()` 并把完整 OpenAI schema 放进普通 tool result；
它既会包含 env-hidden inventory，也没有 session/task capability scope。AgentLoop 在 run 开始时一次性冻结
`tool_schemas`，收到搜索结果后不会 hydrate 新 schema，因此搜索到的工具不能可靠地在同一 run 的下一轮
成为 provider-declared function。现有测试只验证字符串匹配和 schema 返回形状，没有覆盖
`search → describe → activate → direct call` 的真实链路。

### 4.19 task tool policy 目前不是执行授权边界

Assembler 的 `AssemblyPolicy.tools` 只过滤模型可见 schema；`ToolRegistry.execute_tool()` 会检查全局
disabled toolsets、plan read-only、breaker 和 PermissionGate，却不会确认被调用工具属于本 request 的
direct/activated 集合。正常 function-calling provider 通常不会生成未声明工具，但这不是 host-side authorization，
也无法安全支持动态 tool discovery。V1 必须增加 request-scoped capability scope，并在 handler 前 fail closed
复核；durable workflow 的 `execute_prepared()` 保持独立 effect authorization，不与普通 Agent scope 混用。

### 4.20 Registry/MCP 变化缺少可重放的 catalog 版本

`ToolSpec` 已有 `schema_hash/spec_version/permission_policy_version`，但 Registry 没有单调 catalog revision，
register/unregister/MCP reconnect 后也没有使某个 request 的 deferred reference 显式 stale。要支持持久化
PreparedToolSet 摘要与同轮激活，必须为 catalog snapshot、per-tool hash 和 policy fingerprint 建立版本合同；
不能仅靠工具名重查当前 registry。

### 4.21 `visible_when` 是无参全局谓词，goal tools 可跨 Session 泄漏

`ToolSpec.is_visible()` 当前只调用无参 `visible_when()`；`backend/main.py` 注册 `goal_task_*` 时又调用
`SessionGoalStore.get_active_goal_context()` 的无 session 回退，即取全进程最近活跃目标。现有方法即使传入
`session_id`，当该 session 无 active goal 时仍会继续回退到全局最近目标，因此也不是 strict 查询。
Session A 有 active goal、Session B 没有时，B 仍可能看到 goal tools。V1 必须新增不回退的
`get_active_goal_context_for_session(session_id)`，冻结 `ToolEligibilityContext`，并让 session-scoped visibility
在 resolve、describe/activate、attempt validation 和 execute 全程使用同一 session；旧回退方法仅供 OFF。

### 4.22 tools config 读取失败目前 warn-and-continue

`ToolRegistry.schemas()` 与 `execute_tool()` 读取 `_tools_config_provider` 抛错时只记录 warning，随后按空 disabled
集合继续。这对 legacy 回退可接受，但对 capability authorization 属于 fail-open：配置服务不可用时，本应禁用
的工具可能被暴露或执行。Context OS ON 必须使用 strict、不可变 `ToolPolicySnapshot`；初始 resolve、激活与
执行任一读取失败都返回 `tool_policy_unavailable`，policy fingerprint 变化使旧 scope stale。OFF 才保留旧行为。

### 4.23 provider 限制不能参与逻辑工具集合

不同 provider 可能用不同 wire schema 表达同一 canonical tool，但 fallback 期间 request 的授权集合不能随
provider 改变。若 Resolver、describe 或 activate 预先应用 provider restriction，就会让同一 request 的 scope、
snapshot 与 receipt 随 attempt 漂移。V1 的 Resolver 只处理 host/session eligibility；provider adapter 仅负责
表达完全相同的 logical set，无法表达时淘汰该 attempt 并沿既有 chain fallback，不能静默筛工具。

## 5. 可复用的权威状态

| 需求 | 首选权威来源 | 备注 |
|---|---|---|
| 当前显式目标 | `SessionGoalStore` / `session_goals` | 目标文本和状态权威；subgoals 当前不可靠。 |
| Goal pending/completed | `goal_tasks` | 使用 status/dependencies，不读内存 `subgoals` 作为最终权威。 |
| Durable Workflow 当前步骤 | workflow run/checkpoint + `ProposalStateV1` | 不复制 workflow state。 |
| 工具结果是否完成 | delivered success Receipt + Artifact refs | accepted/pending 只能表示已受理。 |
| 最近对话 | SessionDB newest-tail | 当前 user row 按 id 去重。 |
| 完整 Session transcript | SessionDB 全部未删除 messages | 原文权威；coverage summary/reference 只做代理视图，不能覆盖或删除原文。 |
| 跨 session 用户事实 | Memory V2 facts/L1 | 不存普通任务过程日志。 |
| 普通长任务派生状态 | 新的 session-scoped context snapshot | 是可重建 projection/cache，不可反向覆盖上述权威 store。 |

## 6. 外部最佳实践及 DeskPet 适配

### Hermes

- 实践：stable → context → volatile 的明确顺序、API-call-time overlay 与缓存 prompt 分离、
  prompt fingerprint 稳定；双层 compressor 仅用于不同运行边界。
- DeskPet 采用：明确 lifetime 和稳定 fingerprint；保留 gateway/loop 不同安全边界的思想。
- 不照搬：不采用固定 50%/85% 阈值，也不把两个摘要器串在同一请求链；DeskPet 已有按模型窗口和
  agentic 负载自适应阈值。
- 来源：https://hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly
- 来源：https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching/
- 工具层适配：吸收 policy-filtered Tool Search 和 compact descriptor → exact schema 的渐进披露；不照搬
  `execute_code` 程序化批量调用进入 V1，因为它会新增代码执行/RPC 安全边界，应在 Tool Capability Plane
  稳定后单独立项。
- 来源：https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-search

### OpenClaw

- 实践：context 与 memory 分离；详细 memory 按需取；压缩前 memory flush；`/context detail`
  把 system/tool schemas/transcript 都计入；compaction 与 tool-result pruning 分开。
- DeskPet 采用：结构化 pre-compact writeback、按需展开 L3、实际请求账本。
- 不照搬：不把任务过程追加到全局 Markdown；DeskPet 已有结构化 facts、Goal、Workflow、Receipt。
- 来源：https://docs.openclaw.ai/concepts/context
- 来源：https://docs.openclaw.ai/concepts/memory
- 工具层适配：吸收“先合成 effective catalog，再 search/describe/call，真实执行仍回到正常 policy/approval/
  logging 内核”的原则；V1 实现内部 typed capability seam，不在本计划扩成第三方插件 hook SDK。
- 来源：https://docs.openclaw.ai/tools/tool-search
- 来源：https://docs.openclaw.ai/plugins/hooks

### Codex

- 实践：`AGENTS.md` 持久项目规则、Skill progressive disclosure、tool output 限额、compact、
  subagent 独立 context。
- DeskPet 采用：项目规则与 Skill 分层、按作用域加载、长任务噪声隔离。
- 不照搬：普通桌宠聊天不扫描 repo，也不强制依赖项目文件。
- 来源：https://developers.openai.com/codex/codex-manual.md
- 工具层适配：吸收 Skill/工具目录的渐进披露、sandbox 与 approval 分层、子代理独立能力集；DeskPet 保留
  Registry 的 PermissionGate/Receipt/Artifact/VerifyGate，不用 context policy 替代执行权限。

### Claude Code

- 实践：根规则/auto memory compact 后重注入；path-scoped rules 与嵌套规则按文件命中加载；
  先清旧 tool output 再摘要；`/context` 显示真实分类；Skill 正文有独立重挂预算。
- DeskPet 采用：lifetime-aware remount、path rules、先 pruning 后 compaction。
- 不照搬：不依赖 CLAUDE.md 作为用户长期记忆；DeskPet 保持 Memory V2 的结构化召回。
- 来源：https://code.claude.com/docs/en/context-window
- 来源：https://code.claude.com/docs/en/how-claude-code-works
- 工具层适配：吸收 subagent `tools/disallowedTools` 的 capability-scope 表达和 deny-first 思路；完整
  Pre/PostToolUse 插件化 hook 生命周期不纳入 Context OS V1，只冻结可后续扩展的内部执行事件类型。

## 7. 架构约束

1. `ContextAssembler` 继续拥有 pre-loop component selection，不新建第二个 assembler。
2. `ContextManager` 继续拥有 request budget、tool-result ref/dedup 等运行时治理，但退出默认 preflight 有损摘要。
3. `ContextCompressor` 是默认生产链唯一有损摘要 owner。
4. Task context 是对现有权威 store 的 projection；snapshot 只保存派生状态和普通任务状态。
5. 每一次实际 provider attempt 必须在调用边界生成一份可诊断但脱敏的 `PreparedContextReport`，
   并用 request/attempt id 回填该次 usage；Assembler decisions 只是其上游输入之一。
6. provider chain 在公共裁剪时采用最小 effective input budget；每个实际 attempt 再按该
   provider/model 的 window、实际 schemas/`tools=None` 与 reserve 重算。
7. budget gate 顺序固定为 estimate → reversible pruning → 必要时 preflush + single compact →
   re-estimate → final BLOCK；flush 失败且 pruning 后仍超限时显式返回可恢复 budget error。
8. text/voice/code/research 共享 fragment contract，允许 policy 不同，不允许各自再造拼接器。
9. 所有 provider call 都以 `purpose + request_id + attempt_id` 进入统一基础报告；user-response attempt
   额外关联 fragment/trim/compact/cache 决策，辅助调用不得冒充用户响应 usage。
10. 压缩模型默认 `follow_session`，显式专用模型必须可解析并记录真实 provider/model；失败时不静默换模。
11. 单 Session 原始 transcript 能放下时全量装载；不能放下时用分层连续区间 coverage tree 覆盖全部
    未删除 message id，并保留最近 raw tail 与精确 page-in reference，任何 gap/overlap 都阻止有损提交。
12. `PreparedToolSet` 是每个 request 的唯一工具集合真相；Assembler 只产 `ToolExposureIntent`，
    RequestPlanner 汇总 protected direct requirements 后调用 Resolver 一次冻结，AgentLoop 只消费和生成不可变
    activation revision，不得从 Registry 按名字重建第二份 schema。
13. tool policy 必须分成 direct、discoverable、denied 三类；search/describe/activate 与真实 execute 都受同一
    request-scoped capability scope 约束，PermissionGate 仍是其后的副作用审批层。
14. Registry catalog revision 与 per-tool schema/policy version 必须可复核；MCP 热注册、卸载或 spec 变化使旧
    deferred reference fail closed，而不是静默解析成新工具。
15. 工具 eligibility 必须显式绑定 session/request；session-scoped `visible_when` 不得使用无参全局回退，
    Session A 的 goal/tool state 不得影响 Session B。
16. Context OS ON 的 tools config 必须读取为 strict immutable `ToolPolicySnapshot`；读取失败 fail closed，
    fingerprint 变化使旧 capability scope stale。OFF 才保留 legacy warn-and-continue。
17. `PreparedToolSet` 是 provider-neutral 授权集合；provider adapter 只能等价转码并报告 wire hash/tokens，
    不支持某 schema 时失败当前 attempt，禁止过滤、改名或重选逻辑工具。
18. legacy `AssemblyPolicy.tools` 必须原样保留给 `context_os_v1=false`；ON 的 `tool_exposure` 是并列新字段，
    禁止用 direct/discoverable 的有损逆变换实现回滚。
19. capability scope revision 与 `session_context_snapshots.revision` 是独立并发域；所有 DB CAS 必须用显式
    `expected_row_revision` 和返回的新 handle 传递，禁止用 scope revision 代替。
20. snapshot CAS 的 cancellation 必须经过 commit-ack settlement；不得让调用方处于“不知道 DB 是否提交”的
    状态。已提交但尚未激活/发送的 row 只作 diagnostic，不能恢复 capability scope。
