# 拟议验收标准：DeskPet Context OS V1

> 状态：**AC-CTX-1～16 与七项决策已由用户 review；AC-CTX-17～19 工具能力补充已完成挑战，待本轮用户 review；实施仍需另行明确授权**。
>
> 本文件只约束本次 Context OS 计划，不替代仓库根目录现有的
> `acceptance.md`（该文件目前属于 Durable Workflow 项目）。

## 范围

- 包含：统一文字聊天和 AgentLoop 的上下文分层、拼接、预算、压缩、任务态快照、按需召回、缓存边界和诊断。
- 包含：复用现有 `ContextAssembler`、`ContextManager`、`ContextCompressor`、SessionDB、Goal/GoalTask、Durable Workflow state、Receipt/Artifact、SkillLoader 和 ContextTrace。
- 包含：为语音入口保留同一 assembly contract；语音专属 TTS 内容仍由语音管线负责。
- 包含：压缩模型可在设置中选择；单 Session 采用“能放下则全量原文，超窗则无盲区 coverage tree + 原文尾部 + 可恢复引用”。
- 包含：统一工具能力平面。ContextAssembler 产出单一 `ToolExposureIntent`，ContextRequestPlanner 汇总
  protected direct requirements 后由 ToolCapabilityResolver 一次冻结初始 `PreparedToolSet`；
  AgentLoop/Provider/预算器/诊断使用同一份精确 schema，大目录通过 policy-filtered 的渐进式工具发现与
  同轮激活按需展开。
- 明确不包含：替换 LLM provider、替换 AgentLoop、重写 Memory V2、创建第二套通用 Workflow Engine、把所有普通聊天强制转成 Goal/Workflow、引入外部 SaaS 记忆服务。
- 明确不包含：本轮直接实现代码。本轮只交付经挑战迭代的第一份 plan，用户 review 通过后才进入执行阶段。

## 功能验收条款

| ID | 功能点 | 可验证验收条件 | 优先级 |
|---|---|---|---|
| AC-CTX-1 | 单一上下文生命周期模型 | 每个 request-prefix 注入项均带稳定的 `source/lifetime/priority/trim_policy/protected/reason` 元数据，prefix 顺序固定为平台约束 → 稳定身份/规则 → 当前任务态 → 按需知识；conversation/tool groups 与 in-loop control events 不被提升到 prefix，按原始因果顺序保留在 transcript 中；同一内容不得跨层重复注入。 | 必须 |
| AC-CTX-2 | 真正稳定的缓存前缀 | 连续两轮未修改 persona、匹配的项目规则和 L1 核心记忆时，稳定前缀的序列化字节与 fingerprint 完全一致；时间、session、本轮 L3、当前模型运行态等动态内容不得进入此前缀。支持显式 cache control 的 provider 在该稳定边界落 breakpoint；不支持者至少保持稳定序列化/fingerprint 且不破坏请求。 | 必须 |
| AC-CTX-3 | L1/L2/L3 生命周期分离 | L1 核心档案与 L3 本轮召回不再共享一个 dynamic slice；跨轮 L2 新增完整 OpenAI 工具组 round-trip，保留 `assistant.tool_calls` 及其全部 `tool_call_id` result，并按 newest-tail 的不可拆组边界读取；L3 超时、空结果或降级不得抹掉 L2。 | 必须 |
| AC-CTX-4 | 当前任务态投影 | 对显式 `/goal`、GoalTask、Durable Workflow 和普通长任务，系统可生成统一的只读 `TaskContextSnapshot`；权威字段优先来自现有 Goal/Workflow/Receipt/Artifact store，派生摘要不能覆盖权威状态。无活动任务的闲聊不注入空任务块。 | 必须 |
| AC-CTX-5 | 压缩前结构化写回 | context 即将有损压缩时，先把 objective、decisions、completed、pending、artifacts、blockers 和 source revisions 写入 session-scoped context snapshot；成功后才允许有损压缩。写回失败时先做可逆 pruning 并保留 current request、protected task state 与最近完整 tool group；若仍超 effective input budget，返回明确的可恢复 budget error，不允许静默有损压缩或超限出站；不得继续把普通任务快照无界追加到全局 `MEMORY.md`。 | 必须 |
| AC-CTX-6 | 单一有损压缩 owner | 默认生产链路只能由 `ContextCompressor` 生成有损摘要；`history_compactor` 不得在同一链路预先生成第二种摘要。一个 compaction cycle 可为 coverage tree 生成多个互不重叠的 bounded segment summaries，但每个 source range 最多提交一次、所有 job 共享同一 owner/model resolution/cycle id。回退开关关闭 reactive compressor 时，旧 preflight 行为仍可用。 | 必须 |
| AC-CTX-7 | 工具组与大结果治理 | assistant tool call 与对应 tool result 始终作为不可拆分组处理；先裁陈旧大 tool result 正文并保留结构化 outcome/ref/receipt/artifact，再考虑摘要历史。accepted/pending receipt 不得被提升为 completed。 | 必须 |
| AC-CTX-8 | 全请求预算 | 预算计算覆盖 system/fragments、L2、当前用户消息、实际出站 tool schemas 或 `tools=None`、附件、tool calls/results、压缩摘要和输出/推理 reserve；首轮在没有 provider usage floor 时也不得漏算 schemas。provider chain 在每个实际 attempt 按该 provider/model 的窗口重算；调用前的公共可逆裁剪以 chain 最小 effective input budget 为安全线。顺序必须是预算预估 → 可逆 pruning/必要 compact → 重新预算 → 最终 BLOCK。 | 必须 |
| AC-CTX-9 | 渐进披露 | Skill 启动只暴露紧凑索引、正文按语义或显式调用加载；L3 先注入 bounded 摘要/引用，细节可通过 memory 工具展开；项目/路径规则只在 workspace/code 场景且匹配路径时加载。现有普通聊天自动 L2 连续性不能退化。 | 必须 |
| AC-CTX-10 | 压缩后恢复 | compact 后重新挂载 protected task snapshot、稳定规则、仍活跃的 Skill 正文和最近完整对话尾部；路径规则在再次命中路径时重新加载。压缩前后当前任务、pending、关键决策和 artifact identity 一致。 | 必须 |
| AC-CTX-11 | 实际请求可观测性 | 每一次 provider attempt 都以 `purpose + request_id + attempt_id` 记录 provider/model、最终序列化 messages、实际 tool schemas 或 `tools=None`、window/reserve，并用同一 id 回填 authoritative usage；`agent_response/force_finish` 还须关联各层 tokens、cache fingerprint 和加载/裁剪/压缩原因。capability gate、classifier、planner、compressor 等辅助调用与用户响应分栏，chain fallback、force-finish、compact 后重试不得复用旧 usage。不得用“工具数 × 60”等粗略值冒充实际 breakdown，敏感正文只显示脱敏 preview/hash。 | 必须 |
| AC-CTX-12 | 多入口一致性与降级 | text chat、code mode、web/research 和 voice 都有明确入口矩阵，至少共享相同 fragment/lifetime、预算与 provider-attempt report 合同；任一可选组件、L3、snapshot、规则读取或 compactor 失败均 safe-fail。未超预算时当前用户原文和最近连续尾部仍可发送；超预算则按 AC-CTX-5/8 明确失败，不静默破坏状态。 | 必须 |
| AC-CTX-13 | 默认启用与回退 | 完成并验收的 Context OS V1 路径在测试阶段默认启用；保留一个明确的兼容回退开关，但不得长期双写/双压缩。OFF 必须读取原样保留的 legacy `tools` 配置，八类 task 的工具 names/order 与校准 HEAD 完全一致，不得从 ON 的 direct/discoverable 结构反向推导。 | 必须 |
| AC-CTX-14 | 长会话真实验收 | 在调小 context window 的真实 DeskPet 运行栈中完成：多轮普通承接、20+ 工具调用长任务、一次 compact、任务切换、应用重启恢复；用户追问时能正确给出当前目标/下一步/产物，不调用无关工具，不回漂旧任务。 | 必须 |
| AC-CTX-15 | 压缩模型可配置 | 设置页提供 `跟随当前会话模型`（默认）与专用压缩模型选择；每次 compact 记录实际 provider/model。专用模型无法解析或调用失败时，不得静默换模型并提交有损摘要：保留原文/既有有效 coverage，发出诊断；若因此仍超预算则返回可恢复 budget error。`context_os_v1=false` 时不改变旧压缩模型接线。 | 必须 |
| AC-CTX-16 | 单 Session 无盲区覆盖 | 对当前 Session，在完整原始 transcript（eligible 的 user/assistant/tool rows，含工具组、排除旧派生 summary rows）能放入剩余 effective input budget 时必须全量无损装载，不得继续受固定 `l2_top_k` 限制；超窗后，每条 eligible 未删除消息必须且只能由 raw message 或带连续 message-id 范围/source hash 的有效 summary node 覆盖，每个 summary node 同时携带可 page-in reference，但 reference 本身不能冒充内容覆盖。ContextTrace 必须证明无 gap/overlap；用户追问旧细节时可从当前 SessionDB 精确 page-in。摘要失败、stale hash 或 coverage 不完整时不得静默丢弃历史。 | 必须 |
| AC-CTX-17 | 单一工具集合真相 | `ContextAssembler` 只生成一个 `ToolExposureIntent`；`ContextRequestPlanner` 汇总 history/page-in 等 protected direct requirements 后，每个 request 只调用一次 `ToolCapabilityResolver` 生成 `ResolvedToolDraft`，再用该 draft 无二次 Registry 读取地 finalize 唯一初始、不可变、版本化 `PreparedToolSet`；同 run 激活只生成后继 immutable revision。其中包含 exact direct schemas、deferred capability refs、registry revision、strict policy/schema fingerprint 和选择原因。`main.py` 不得再把 schema 降级成名字后让 `AgentLoop` 调 `registry.schemas()` 重建。逻辑集合只由 host/session eligibility 决定且 provider-neutral；每个 provider attempt 的 adapter 可以产生不同 wire 格式，但不得静默筛减/改名/加工具，必须返回 adapter id/version、wire payload hash/tokens，并让预算器、snapshot、report 与真实 request builder 消费同一 `PreparedToolPayload`。adapter 不支持某 schema 只淘汰该 provider attempt，不改变逻辑集合。 | 必须 |
| AC-CTX-18 | 授权边界内的渐进式工具披露 | 当 policy 配置 deferred capabilities 时，模型仅常驻 direct schemas 与 `tool_search/tool_describe/tool_activate` 桥接工具；搜索只能返回当前 request 的 session-aware capability scope 已授权且可见的 compact descriptors，不能遍历全进程 registry、其他 session、env-hidden、mode-denied 或 disabled 工具。`describe` 返回精确 schema/hash，`activate` 经同一 session eligibility、strict policy/version 和预算复核后在同一 AgentLoop run 的下一次 provider attempt 生效；未描述、过期、越权、超预算、policy 不可用/变化或跨 session 激活必须 fail closed。Session A 的 active goal 不得让 Session B 看见 goal tools。 | 必须 |
| AC-CTX-19 | 工具执行授权、持久化与既有闭环不回归 | Context OS ON 时，每个 AgentLoop tool call 必须携带 request-scoped capability scope，Registry 在 PermissionGate/handler 前用同一 session eligibility 和 strict current `ToolPolicySnapshot` 再次确认该工具属于 direct 或已激活集合；policy provider 读取失败返回 `tool_policy_unavailable`，不得 warn 后继续。缺少 capability-aware `execute_tool` 的 Registry/legacy `dispatch()` 必须 fail closed。`execute_prepared` durable workflow 仍按既有 effect authorization 合同执行，不被普通 Agent scope 旁路。每个 request 的精确工具集合进入 ContextAttemptStore/report；存在 active task 时，初始 prepare 必须以 DB row revision=0 create-or-CAS 完整 projection+工具摘要，后续 activation/attempt 再 CAS 持久化最新 direct/activated names、per-tool schema hash、policy fingerprint、registry revision、选择原因、schema tokens 和 selected provider adapter id。capability scope revision 与 DB row revision 必须用独立类型/命名和 write receipt 传递，禁止互换；异步 CAS 在取消时必须先结算 commit outcome，已提交但未激活/未发送的记录标为非权威 diagnostic/prepared 并传播取消。无 active task 的闲聊不得为此创建空 snapshot。不持久化 handler/凭据/完整敏感参数；MCP register/unregister 或 spec/policy/session-eligibility 变化必须使旧引用 stale。Artifact、Receipt、VerifyGate、timeout、breaker 和 `force_finish tools=None` 语义不得回归。 | 必须 |

## 非功能与边界

- 性能：不触发 L3/路径规则/compact 的短聊天，assembly P95 不应比当前基线增加超过 20ms；不得新增每轮辅助 LLM 调用。
- 工具性能：500 个 deferred capability fixture 下，初始 provider payload 不携带 deferred full schemas；相较
  全量 schema 直传，工具 schema bytes 至少下降 80%。未使用 deferred 工具的请求不得新增 bridge 之外的
  provider round-trip；实际 search/describe/activate 只在模型明确需要 deferred 能力时发生。
- 缓存：稳定前缀 fingerprint 变化必须能解释到具体 fragment；动态 fragment 变化不得连带改变更早的稳定层。
- 并发：同一 session/task snapshot 使用 revision/CAS 或等价单调机制；迟到写回不能覆盖更新任务态。
- 幂等：同一个 compaction cycle 的 flush 最多提交一次；重试不得重复追加相同 decision/artifact。
- 安全：召回记忆、Skill、项目规则和工具结果都视为有来源的上下文数据，不得因此绕过现有 permission/verify/receipt gate。
- 工具安全：capability visibility 不是 PermissionGate 的替代品；policy deny、scope authorization、PermissionGate
  和 durable effect authorization 分层执行。任何 bridge/tool result 文本都不得伪造 host activation directive。
- 隐私：ContextTrace 不展示凭据、完整敏感工具参数或未脱敏文件正文。
- 兼容：保留现有 ToolRegistry handler、消息 role/tool_call_id 契约、thinking `reasoning_content` round-trip 和 Session UI 事件语义。
- 兼容：`context_os_v1=false` 时保留现有静态 `tool_search` 与 AgentLoop schema 重建路径；ON 时现有工具 handler、
  Artifact/Receipt/VerifyGate、MCP qualified name 和 durable `execute_prepared` 合同不变。
- 迁移：新增持久化结构必须走当前 schema/migration owner；不得新增懒建表债务。
- 可扩展性：Session coverage 使用分层连续区间树；超长 Session 不要求每轮重读/重算全部原文，增量 append/merge 后仍保持无 gap/overlap。

## 完成定义（后续执行阶段使用）

- AC-CTX-1～19 均有 `AC → task → code → automated testcase → real result` 可追溯证据。
- 聚焦 pytest、全量非 live 回归、前端测试/tsc 全绿；已有红线单独记录，不得伪装成本次回归。
- Windows Computer Use 真点击完成 AC-CTX-14～19；工具类 case 额外对账 scope revision、provider schema hash
  与真实 handler receipt，截图与 Tauri/backend 日志齐全。
- `STATUS/status.md`、架构文档、计划结果和 testcase index 同步更新。
