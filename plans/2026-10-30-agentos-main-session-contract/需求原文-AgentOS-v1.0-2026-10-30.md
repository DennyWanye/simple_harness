# 需求原文：AgentOS 需求合同 v1.0（2026-10-30 收到，原样保留）

来源：AgentOS 侧 2026-10-30 发来的需求合同。SDK 侧的评估见同目录《评估与方案.md》。

---

# SDK 需求：持久主会话统筹多个工作会话

版本：需求合同 v1.0 · 2026-10-30 · 状态：可交接实施，尚未开发/验收。

需求方：AgentOS。实现方：Simple Harness SDK。用户已选定"通用编排能力补入 SDK，AgentOS 做产品和业务适配"。本文取代前一研究报告中"宿主持久 BaseAgent 主控＋宿主自建跨会话协调"的实施推荐；前期研究仍作为能力基线。

本文 MUST/必须为验收要求，SHOULD/建议为可替换实现建议。接口名称与类型均为**拟新增或拟扩展合同**，不是 opt.222 已存在的可调用代码。SDK 团队可依现有命名约定调整名称，但必须提供本文合同到实际公开接口的逐项映射，不得改变行为语义。不得让 AgentOS 依赖 SDK 私有方法、修改内部数据库或复制调度逻辑。

## 1. 目标、主要矛盾与范围

用户在一个主会话中提出目标，主会话能创建至少两个独立工作会话，后台并行推进；工作会话可以持续接收追加输入，使用 SDK 的 Mission、规划、TaskGraph、执行、审查和资源管理。子结果可靠回到主会话；宿主允许时，主会话自动核对、汇总并在原授权范围内继续。关页面或重启服务后，身份、归属、审批和已发生的业务效果不丢失、不伪造。

**主要矛盾：责任与授权必须跨多轮/多进程连续，而当前接入把执行绑定到一次请求，SDK 高层 Session 尚无完整的跨会话统筹合同。** 解决顺序为稳定身份与责任→异步编排→结果驱动→恢复验证。长期不是一个永不结束的模型请求；无待办事件时不得持续轮询模型。

本期必须支持：同一 Host 内一层主→工作 Session、多个工作 Session 并存、至少两个可同时推进的独立 Mission、同一工作 Session 的多轮追加输入、主会话在子任务运行时可接收用户输入、结果驱动和持久恢复。一个 Mission 内部的并行子步骤不能替代多个工作 Session。

本期不要求：任意深度代理树、跨 Host 联邦、多机高可用、通用聊天网络、新的业务身份认证系统、无限自主行动。单进程部署可以作为首版支持模式，但多个进程误打开同一 store 时必须明确拒绝或正确 fencing，不能产生双 owner。

## 2. 固定基线：复用什么、缺什么

研究基线 SDK：`0.13.0.dev20260925+opt.222`，源码 ref `47fd34f0da65667817d30f1994e73523b5aaf8ba`；AgentOS：`2e3e1ad40d9196595987b94cbe4edd586d66b7dc`。不把本文日期推断为将来最新版本。

| 对象/能力 | opt.222 已核事实 | 本期要求 |
|---|---|---|
| BaseAgent | create/open/持久 input/turn；公开工具端口 | 内部可复用；对接方不必自己组装跨会话循环 |
| 高层 Session | open_session/send/history/tasks/events/inbox/ack/respond/cancel_task | 扩展为可管理子 Session 的公开主控入口 |
| 委派 | delegate_task→Mission 异步；每 Session 仅一个在途 Mission | 一个主 Session 可管理多个工作 Session；每工作 Session 一个在途 Mission 的默认限制可保留 |
| 结果 | result_ready 与 inbox 持久化 | 加 SDK 内的可靠父结果投递、消费与受控自动唤醒 |
| 控制命令 | session_chat.py 固定 delegate_task/task_status/task_cancel | 新增原生跨会话管理命令/受限公开控制扩展；不得伪装成只读 DomainTool |
| 编排 | Mission/HTN/TaskGraph/Assurance、容量调度 | 工作会话任务沿用这些能力，不另造执行引擎 |
| 上下文 | 历史、窗口、部分摘要/召回机制 | 目标、关联、授权、待结果与回执是持久结构化事实，不只放提示词 |

SDK 源码定位、测试入口与文档漂移见附录 A。尤其不能把当前委派键按"目标文字 hash"实现：固定版本实际采用 turn+slot；新合同需显式版本化，不暗改旧回放语义。

## 3. 职责与权威数据

| SDK 负责并持久化 | AgentOS 负责 |
|---|---|
| 主子 Session 关系、会话/轮次/委派/Mission ID、事件和结果引用 | chat→SDK Session 的产品映射、共享登录与成员 persona 校验 |
| 命令幂等、父结果投递、消费状态、唤醒输入、状态版本 | 判断具体用户目标允许哪些操作；提供授权/审批策略 |
| 调度、并发准入、取消传播、owner 互斥、恢复 | NAS/记忆/视频等业务工具、业务事务和权威回执 |
| 公开状态/事件/控制/恢复接口、版本兼容声明 | 页面、SSE或轮询投影、用户通知、部署与回滚 |

SDK 不决定家庭权限；AgentOS 不再维护第二套通用父子调度器、轮询子完成再手动 send 的续办循环。AgentOS 可持有产品状态缓存，但 SDK 是编排状态权威，业务系统是外部效果权威。两者不能由模型文字覆盖。

## 4. 术语、身份和不变量

- **Session**：持久、多轮逻辑会话；主/工作是角色。主 Session 长期存在并不表示某个目标永不完成。
- **Goal**：宿主授权的有界目标，含版本、完成/停止条件、预算、grant_ref；同一主 Session 可先后持有多个 Goal；首版每主 Session 最多一个未收口 Goal，Goal 内可并行多个子委派。
- **Turn/Input**：一次用户输入或经授权的结果驱动输入；同一 Session 的状态提交串行。
- **Delegation**：主目标向一个工作 Session 的委派关联，有独立 ID 和终态；不是把 SDK 内部 task_id 当聊天会话号。
- **Mission**：SDK 有界执行目标；一个工作 Session 可先后承载多个 Mission，不修改已有 Mission 的冻结输入冒充续办。
- **Effect**：真实业务操作，使用独立 operation_key、参数摘要和业务回执；不是模型 turn 或事件的别名。

必须保持以下不变量：

I1 同一 owner/principal 与稳定 session_id 打开同一逻辑身份；配置不兼容明确报错，不能静默创建替代身份。
I2 子 Session 同时只有一个父关系，关系创建原子化；首版最大深度1，不能成环、跨 principal 或用模型传入的 ID 绕过归属。
I3 已受理命令、事件和结果至少持久保存到可恢复介质；进程内 Future/set 不构成受理承诺。
I4 每 Session 的权威状态提交串行，旧 owner/旧版本不能覆盖新状态；主与子可同时处理各自轮次。
I5 相同命令身份不重复产生关联、Mission、消息或批准；不同参数不能套用旧成功。
I6 结果是资料，不是授权。跨会话转发、摘要、重启不得扩大工具权限或自动批准业务写入。
I7 取消禁止取消屏障后的新派发，但保留屏障前已提交/已交接/未知效果；不把"取消请求收到"伪装成"所有外部效果撤销"。
I8 确定性校验失败、结果未知、审查拒绝必须如实保留；不得以主模型总结成"成功"覆盖。

## 5. 公开接口合同（拟议语义）

以下是目标调用面，非现成SDK示例。允许用现有 Session 方法及公开 request/result 类型表达，不要求逐字实现此伪签名。

### 5.1 类型和通用规则

所有写命令携带 `command_id`（调用方生成）、目标身份、可信 principal、`expected_version`（针对已有可变对象）、`protocol_version`。SDK 绑定规范化业务字段的 `request_hash`。身份域至少包含 principal、目标Session和操作类型。

先检查可信身份和归属，再查幂等记录；同ID同hash返回原 receipt，即使当前版本已前进；同ID异hash返回 `idempotency_conflict`。之后检查版本/授权/容量。并发相同ID只允许一个逻辑命令生效。重试不能延长授权有效期。

`CommandReceipt` 最少包含：`command_id, request_hash, status(accepted|completed|rejected), session_id, delegation_id?, input_id?, version, event_seq, error?`。accepted 必须已经持久化；不表示模型已答复或任务已完成。可用 read-only receipt 查询得到后续状态。预校验拒绝无需造业务对象。持久化失败不得返回accepted；连接中断导致调用方无法确认时，必须允许按原command_id查询或重试。

状态/事件结果必须有公开可序列化 schema，禁止把内存对象句柄当唯一恢复入口。文本/结果采用大小有界的内容或不可变 artifact 引用+hash；SDK提供分页/分段读取，截断必须可见，不得静默丢失完成依据。

### 5.2 操作面

| 拟议操作 | 最少输入与输出 | 必须行为 |
|---|---|---|
| `open_session`（扩展） | session_id、principal、role、policy/config revision → SessionRef/snapshot | 幂等打开；重开不自动重复模型调用；暴露能力/版本；根Session创建不得伪造子归属 |
| `put_goal/finish_goal`（新增） | root_session_id、GoalSpec、command_id、expected_version → goal_ref+receipt | 可信宿主登记/更新授权目标；模型只能提案；终态收口须核对委派、结果与效果，不允许仅凭模型总结成功 |
| `create_child`（新增） | parent_session_id、goal_id/version、child_key、角色/能力请求、command_id → ChildRef+receipt | 原子建立父子关系；权限只能收窄；返回持久child_id；创建本身不隐式执行业务目标 |
| `submit`（扩展send的异步受理语义） | target_session_id、InputEnvelope、command_id、expected_version → receipt/input_id | 快速持久受理；主与不同子可分别执行；同Session串行；提供状态/结果查询 |
| `delegate`（扩展现有委派） | parent、child、goal_ref、task_spec、grant_ref、command_id → delegation_id+receipt | SDK建立关联并在child创建/关联Mission；原子或可恢复分阶段；worker同一时刻超限返回busy，不产生半个孤儿任务 |
| `capabilities/receipt`（新增或公开既有查询） | 协议版本/command_id与可信身份 → capability set或原命令receipt | 无模型调用；能力名覆盖父子关系、结果续办、恢复、取消、effect对账与支持的schema版本；receipt查询遵守同一归属边界 |
| `snapshot/list_children/task` | root/child/delegation ID、分页游标 → 权威状态/关系/版本 | 全部校验归属；终态和未知效果可查；查询不得触发新的模型调用 |
| `events`（扩展） | root_session_id、after、limit → events,next_cursor,has_more | 可返回此主树的持久事件；重连补读；游标过期返回明确错误和恢复快照方法 |
| `respond`（扩展） | request_id、decision、request_version、grant/principal、command_id → receipt | 复用人工响应；批准绑定目标/参数/版本；重复批准返回原结果；错误主体/过期/已取消拒绝 |
| `interrupt`（沿用并明确） | session_id、turn_id → receipt | 只停该轮模型回复，不隐式取消其他子Session/Mission |
| `cancel`（新增统一范围合同） | target_kind(goal\|delegation\|session_tree)、target_id、expected_version、reason、command_id → receipt | 明确传播范围及屏障；返回cancel_requested，最终效果核对后更新；调用者不得因跨树ID扩大取消范围 |
| `close/archive`（扩展） | session_id、expected_version、command_id → receipt | 首版有未终态委派/未消费结果则返回active_work；不得悄悄终止子任务；先显式取消/收口后才能关闭 |
| `start/recover/shutdown`（扩展Host生命周期） | store/config、恢复策略 → RecoveryReport | 打开原对象、重建驱动和待结果消费；不重发自然语言目标；shutdown可停止进程而保留业务责任 |

`submit` 的 InputEnvelope 最少含 `kind(user_message|directive|result_event), source_ref, goal_ref/version, payload_ref/hash, grant_ref`。`result_event` 只能由 SDK 内部投递或通过可信宿主入口验证生成；模型/浏览器不能自称内部事件。

追加指令规则：首版 `directive` 明确采用 `next_turn`，在当前工作Session轮次/Mission终态后进入下一轮，不静默改正在运行的冻结Mission。需立即改变执行则显式cancel该Mission，再以新输入/新身份建立后续委派。若SDK提供运行中steer，可作为增强能力另报其合同和测试；未支持时必须返回unsupported，不能假装已生效。

主会话创建子会话、委派、查询、发送指令、取消应有对应模型可调用的原生控制命令。命令目录必须是受限注册表，模型只提出参数；运行时注入可信parent/principal/grant。若提供扩展注册，schema、handler、effect类别、授权函数、幂等策略和版本必须完整声明；保留DomainTool只读约束，不开放任意反射/方法调用入口。

### 5.3 目标、任务和授权类型

以下字段是最小可序列化合同，SDK可复用既有公开类型，但必须发布实际schema：

| 类型 | 必需字段与约束 |
|---|---|
| `GoalSpec` | `goal_id, root_session_id, goal_version, objective, completion_criteria[], stop_conditions[], budget, grant_ref, policy_revision`；创建用expected_version=0，修改按旧version比较后生成新version；完成条件不能只有"模型说完成" |
| `TaskSpec` | `objective, acceptance_criteria[], input_refs[], requested_capabilities[], effect_policy_ref, output_schema_or_artifact_spec`；与goal_ref一起冻结到委派；超出目标授权拒绝，不能靠任务文字自动扩权 |
| `AuthorizationRequest` | 由SDK注入可信`principal, root/session/goal/delegation_ref, action, target_ref, params_hash, grant_ref, policy_revision, operation_key?`；必须能定位具体拟发生操作；不接受模型自报可信身份 |
| `AuthorizationDecision` | 宿主返回`allow(constraints, revision, expires_at?)`、`needs_approval(request_ref)`或`deny(reason)`；异常/不可达等同暂不允许；约束必须可由执行路径检查，不能仅作为提示词 |
| `RecoveryReport` | `store_version, owner_generation, resumed_refs[], waiting_approval_refs[], held_refs[], unknown_effect_refs[], rejected_refs[]`和各项原因；恢复API返回不代表所有任务已完成 |

`grant_ref`是宿主可校验的不透明授权引用，不是SDK自创家庭权限。SDK在命令受理、实际派发及业务effect前调用宿主授权接口；允许缓存仅限仍有效且撤销可生效的决策，查询/策略超时不得推导allow。宿主审批请求必须绑定授权请求摘要，恢复时重新核验版本、有效性和撤销情况。

Goal状态至少为`active|waiting|cancel_requested|succeeded|failed|cancelled`；有unknown效果时保持waiting并暴露原因。`finish_goal(succeeded)`要求必要验收依据已登记、全部委派终态、结果已处理或被可信宿主按明确原因排除、无待审批/未知效果。终态Goal不可原地改写成新目标；新目标须新的goal_id。旧Goal更新前已启动的Mission保留冻结输入；其后续效果仍须按现行授权核验，旧结果归属不能迁移伪装为新版产物。

子关系归属主Session，创建时的goal_ref仅记录来源；子Session可在前一委派收口后被同父后续Goal复用。其新任务不能沿用旧Goal授权。对运行中child提交`next_turn`指令时返回独立input_id；终态后生成新委派时必须通过该input_id建立唯一关联，重复处理不得生成两次Mission。

### 5.4 错误合同

至少稳定暴露：`not_found_or_forbidden`、`invalid_argument`、`idempotency_conflict`、`stale_version`、`session_busy`、`capacity_exceeded`、`budget_exhausted`、`authorization_required`、`authorization_revoked`、`request_expired`、`cancelled`、`active_work`、`unsupported_capability`、`incompatible_store_version`、`owner_conflict`、`cursor_expired`、`effect_unknown`、`provider_unavailable`。

错误含 `code, retryable, safe_message, current_version?, receipt_ref?`；禁止输出密钥、跨成员内容或原始供应商敏感响应。retryable不代表可换command_id重新创建效果；同身份重试/查询原回执优先。busy/capacity必须有可查询的等待原因，不能无限空转模型。

## 6. 事件、结果消费与自动续办

事件最少字段：`event_id, root_session_id, session_id, parent_session_id?, goal_id, goal_version, delegation_id?, mission_id?, turn_id?, kind, seq, object_version, causation_id, payload_ref/hash, occurred_at, schema_version`。`seq`在一棵主树的持久流内单调，允许缺号；时间戳不用作冲突顺序。不得要求全系统全局序列。

事件至少涵盖：child_created、input_accepted、delegation_started、progress、needs_response、result_ready、continuation_queued、continuation_completed、cancel_requested、cancelled、failed、effect_unknown。消费者可忽略未知可选事件类型，但不能误判未知终态为成功。

SDK必须分别记录：

1. **result_received**：子结果/终态已持久化并关联父目标。
2. **continuation_pending/admitted**：满足宿主策略，形成幂等主输入并排队。
3. **result_applied**：该结果已由一个逻辑主输入处理，保存其input/turn/result引用。
4. **user_seen**：产品端用户是否看过；原 `ack()` 若语义为已读，继续表示已读，不能替代result_applied。

投递允许至少一次；逻辑消费和状态转换必须幂等。将result事件与主input建立原子关联，或用可恢复事务日志达成等价保证：任何崩溃点都不得丢结果、创建两个逻辑续办输入或跳过未处理结果。可合并多个结果为一轮，但必须持久记录冻结的event_id集合，不丢关联。

**不承诺模型请求物理恰好一次。** 若供应商已计算而应答丢失，恢复可能再次计费；SDK必须报告这种不确定性。能够保证的是逻辑输入去重、业务授权与效果回执不被重放扩大。

宿主提供拟议 `ContinuationPolicy`：对可信事件元数据和目标快照返回 `enqueue(grant_ref,policy_revision)`、`hold(reason)` 或 `deny(reason)`；异常/不可达不得默许执行。派发时和每个业务效果前重新核验授权，避免入队时允许、执行时已撤销。待处理结果无论是否自动唤醒都可查询。

自动续办必须有可配置的每目标模型调用/轮次/工具调用/时间和并发上限、显式停止条件；包括所有子会话及审查/续办调用。扣额原子化，不因并发或重启重置；已有消费不能被续办新建goal绕开。保持现有预算配置语义，不能擅自改AgentOS已选不限累计调用设置；部署方必须明确选择自动推进的目标边界。预算用尽进入可见等待/失败原因，不以无限重试恢复。

## 7. 状态、并发、取消与恢复

会话生命周期与任务结果分离：Session `open → closing → closed`；运行状态可为 `idle|queued|running|waiting_input|waiting_result|waiting_approval|paused`，关闭不等于某一轮完成。Delegation `accepted → queued → running → waiting_* → succeeded|failed|cancelled|unknown`；cancel_requested为中间状态。unknown表示外部结果未定，不能当作可盲重试的普通failed。相同对象版本不能同时具有冲突终态。

**并发：** 同一Session的输入与结果串行提交，不持有覆盖全部子任务生命周期的主会话锁。Host仍承担总执行/模型容量；子Session不能通过新建绕过配额。至少验证配置容量2时两个独立子Mission可以同时进入运行，并且主消息可被持久受理、获得可解释排队状态；不可让子任务永久占满资源导致主控制命令饥饿。调度需有有限等待/公平性策略及确定性测试，不要求特定队列产品。

**取消屏障：** cancel命令与新派发提交之间定义明确序列。屏障前已交接给外部系统的调用可能继续发生，必须标已交接/完成/unknown；屏障后禁止新调用。子收到取消意图及父等待其确认的状态持久化。失败/超时不能直接冒称cancelled。完成后迟到取消不改写成功回执；取消后迟到结果可审计，不重新触发被撤销goal的自动续办。取消goal不必关闭主Session；之后新goal可继续使用同一主身份。

**目标修改：** 新用户指令可提升goal_version；旧结果保留并标归属旧版本，不能直接覆盖新版主目标或授权。是否作为参考资料由策略决定；旧审批不能授权新版参数。

**恢复：** 从原session/input/delegation/Mission/effect身份恢复。已受理但未派发命令、创建中子关系、待投递结果、待审批与取消意图均可恢复。owner互斥跨进程有效，旧owner不得继续提交；若不支持多worker，第二个实例应在公开入口明确owner_conflict。临时内存锁不足以承担此合同。

业务Effect流程必须保留 `operation_key + params_hash + authoritative_receipt_ref`。SDK向工具适配器传入稳定operation_key/params_hash；适配器须提供可恢复的`execute`与`lookup_receipt`语义，回执至少包含`committed|not_committed|pending|unknown`、业务对象引用和参数摘要。只有权威确认not_committed且旧尝试已不能继续提交时才可再次执行；暂时查不到记录不是未提交证明。SDK持久化该关联及每次对账结论，业务事务仍由适配器所属业务系统实现。执行前claim，提交后查询原回执；同key同参数重试最多一个真实效果；不同参数拒绝；缺回执且结果未知保留unknown。SDK不得把所有外部系统宣称为exactly-once，适配方不能提供对账时必须停止自动重放。

上下文摘要/压缩不得删除关系、待处理事件、授权版本、批准记录和业务回执。主模型可按公开引用回读完整证据；模型只见摘要时不能把未读依据标为verified。

## 8. SDK 验收标准（实现方必须交付证据）

先完成主体功能并跑通，再执行完整验收。测试必须调用对接方使用的公开接口；可用内部屏障制造故障，但不可直接改表把断言变绿。SDK提供确定性模拟模型/工具及至少一条真实模型示例（运行需调用方提供凭据；缺条件标BLOCKED）；模拟通过不能声称真实业务内容质量通过。

| ID | 场景/故障 | 必须满足的判据与证据 |
|---|---|---|
| SDK-01 | 同主session连续多轮、销毁对象/重启后重开 | 同稳定身份、历史/子关系不变；open不产生模型调用；配置冲突明确拒绝 |
| SDK-02 | 主创建A/B并委派独立Mission，容量2；运行中再发主消息 | 两任务存在真实运行重叠；主消息可受理并最终处理；归属隔离且总容量不超限；记录事件/屏障而非只看耗时 |
| SDK-03 | 同child后续追加输入；运行中next_turn指令 | 原任务输入不被偷偷修改；新输入排队且可见；终态后同child继续；unsupported steer明确拒绝 |
| SDK-04 | 重复create/delegate/submit/respond，模拟受理后应答丢失 | 同ID同参数返回同对象/receipt；不同参数冲突；任务、输入、批准各只生效一次 |
| SDK-05 | 子结果落库后父消费前崩溃；重复/乱序投递 | 相同event只形成一个逻辑主输入；迟到结果不倒退终态；重启后无丢失；已读ack不跳过业务消费 |
| SDK-06 | 两子结果与用户更新goal同时提交 | 版本可串行解释，无丢更新；旧goal结果不覆盖新目标，旧授权不产生新效果 |
| SDK-07 | 未授权结果事件、伪造parent/principal、子输出指令注入 | 不提升权限，不跨树读写；结果仅资料；宿主hold/deny或策略异常时不自动执行；保留原因 |
| SDK-08 | memory式事务提交成功、应答前断进程 | 同operation_key下业务记录和事务回执各1；恢复返回原ID；同key异参数拒绝；无法对账时unknown且不重发 |
| SDK-09 | 等待审批时重启，重复批准/拒绝/旧版批准/错误主体 | 等待可恢复；未合法批准零效果；合法批准最多一个效果；原批准范围不可扩大 |
| SDK-10 | 单child取消、goal取消、主回复interrupt、浏览器断线 | 四者行为不同且可查；取消屏障后无新派发；断线不取消业务；已交接/已完成效果保留；迟到结果不续办已取消goal |
| SDK-11 | 创建/执行/恢复中启动第二owner，旧owner再提交 | 正确fencing或第二owner拒绝；不得双派发/双消费；留owner代次及拒绝证据 |
| SDK-12 | 预算用尽、供应商失联、全部子槽占用、无新事件 | 总额不因子会话/重启绕过；明确等待原因；控制请求不永久饥饿；闲置期间模型调用数不增长 |
| SDK-13 | 压缩/上下文换代，父尚有未完成子任务 | 关系/授权/回执摘要前后hash或规范字段一致；待结果仍消费；没有重新创建已完成效果 |
| SDK-14 | active close、最终close、游标补读/过期 | active_work明确拒绝关闭；正常收口后可archive；分页不遗漏；过期返回快照恢复协议，不能静默跳到最新 |
| SDK-15 | opt.222兼容调用、新能力缺失、不兼容store | 旧行为按版本保留；新客户端能探测缺能力并拒绝启动；不得原地损坏旧store；迁移失败可回原版本读旧数据 |
| SDK-16 | 一条真实模型主控→两子→Mission执行→回流→主汇总 | 有公开接口调用记录、真实工具回执及SDK审查轨迹；主汇总关键字段机械对账；不得人工伪造review ACCEPT |

证据包最少含SDK commit/version/wheel SHA、公开schema、测试命令和通过/失败/跳过数、脱敏事件JSONL、稳定对象映射、故障注入点、业务效果计数/回执hash。每项用例给出可重复运行入口与预期断言；本机未具备条件必须标SKIP/BLOCKED，不算PASS。数值转换等有机械oracle的项目以oracle为准，模型审查不能覆盖机械失败。

## 9. SDK 交付与实施顺序

- **M1：身份与接口合同。** 公开类型/schema、能力发现、父子关系、命令幂等与串行提交；SDK-01/03/04/07。
- **M2：原生异步统筹。** 主控命令、多workerSession→Mission、共享容量、结果投递与续办策略；SDK-02/05/06/12。
- **M3：恢复与安全收口。** owner、取消屏障、审批、效果对账、上下文和版本升级；SDK-08/09/10/11/13/14/15。
- **M4：可交付包。** 合并主干或明确受支持版本；带hash的wheel、changelog、公开接口参考、迁移/回滚说明、完整SDK-01～16矩阵、一个无需私有接口的主→两子示例。

在现有发布流程允许的前提下按上述逻辑顺序实现，单项合理定向检查可随开发进行，主体未跑通前不以大规模回归代替开发。所有接口名称差异、暂不支持项和schema变更必须列明，未完成必需项不能标完整交付。示例中至少包含hold与自动续办两种policy，不能默认授予业务写权限。

SDK 实施可以自行决定内部类分层、SQLite表/事务组织、队列与唤醒实现；不允许将SDK应负责的持久结果消费、父子取消或恢复委托给一个未定义的宿主"自行处理"。

## 10. AgentOS 后续接入清单

| ID | 接入工作/涉及位置 | 依赖SDK合同 | AgentOS验收 |
|---|---|---|---|
| AO-01 | chat_sessions及sdk_runtime：产品chat→稳定SDK主Session/version绑定；旧task历史保留 | open/能力发现/版本 | 同chat多轮同SDK主身份，跨成员不可读；归档旧任务不自动重放 |
| AO-02 | web/routers/agent、active_streams、app生命周期：服务持有执行，SSE只订阅 | submit/events/start/recover | 关页重开得到实际状态；HTTP断开不等于取消；同主turn串行而子后台执行 |
| AO-03 | delegation/runtime：接原生子Session/委派，移除新路径的同步await父阻塞 | create_child/delegate/snapshot | 两子运行期间主会话能继续响应；child可追加next_turn输入 |
| AO-04 | tools/core、permissions/hooks、business connectors：映射可信主体、能力、审批、effect回执 | 控制命令授权+respond+effect合同 | 原安全检查全部仍在；personal/family不串；memory业务提交后断线无重复写入 |
| AO-05 | 宿主ContinuationPolicy：目标版本、授权范围、停止条件与预算配置 | 结果驱动策略 | hold不跑模型；允许时仅原目标内续办；政策撤销在执行前生效 |
| AO-06 | ChatPage/SdkTaskPanel：主会话＋子状态/详情、补读游标；区分停止回复/取消子/取消目标 | snapshot/events/cancel/interrupt | 原Chrome登录态下点击、刷新、重新打开；字段逐项对照权威响应 |
| AO-07 | 模型选择/版本升级：固定执行中Mission配置，明确下一轮模型变更 | 配置版本/能力声明 | 不将不可用渠道静默换成别的渠道；不兼容升级建立后继绑定且可追溯 |
| AO-08 | 测试、部署、归档：固定新SDK wheel/hash，新旧数据隔离，回滚脚本与记录 | SDK完整交付包 | 主体完成后回归；K6真实主→两子闭环、重启、取消、唯一写入；回滚不覆盖新业务数据库 |

AgentOS不重新实现SDK的父子状态机、调度、事件消费和自动唤醒。HTTP层/页面可缓存SDK公开快照；消费游标用于用户展示，不接管SDK result_applied。已有安全hook的调用位置在Mission路径必须实测，不能因为名称相同就认为接好了。

AgentOS验收顺序：公开接口契约对照→主体编码跑通→定向断线/重启/回执检查→影响范围回归→真实Chrome与K6验收→部署/归档。SDK模拟测试与AgentOS真实模型测试分别报告。

## 11. 兼容、升级和回滚合同

SDK必须公开协议/状态库版本、能力集及迁移能力；不要要求AgentOS根据版本字符串猜接口可用。新特性仅在调用方显式选择该合同后启用，opt.222调用的单在途/非自动续办语义保持可辨认。AgentOS在新能力验证后启用其选定目标模式，不留"代码已做但从未接线"的状态。

运行中Mission配置冻结。首版不要求任意模型热切换；支持下一轮变更则经公开配置更新产生revision，若需新会话代次则返回后继映射，不得悄悄丢旧history/child。SDK必须明确是否支持原store前向迁移/回退；不支持回退时不能将新store交旧wheel打开。

升级/回滚需先查看在途工作、取消/完成或保留新版本runner负责收口；单实例回旧会中断新任务时必须明确标暂停/无法推进，保留记录与外部回执。禁止强制把新版本任务送旧版本重跑。保存旧wheel、旧镜像、原store及迁移日志。业务PostgreSQL不因SDK回滚恢复旧快照覆盖新记录。

## 12. 最小端到端调用顺序

以下步骤为语义示例，不是opt.222可复制运行的代码。SDK交付时须提供等价可运行示例，AgentOS无需访问私有对象：

1. 宿主按稳定`chat_id`映射打开主Session M；登记Goal G/v1及授权引用，保存受理回执。
2. 主控经SDK命令创建工作Session A/B；分别委派只读任务D1/D2，输出有明确字段和校验条件。共享容量设为2，记录实际运行重叠。
3. D1/D2执行期间向M提交新用户输入，得到持久input_id；向A提交next_turn追加指令，查询可见排队状态。当前D1输入不改变。
4. D1结果落库后、父续办之前强制结束进程。以原store重启，打开原M/A/B；SDK恢复D1结果投递、D2责任和待执行追加指令。效果不确定的步骤先查回执。
5. 宿主策略hold时仅保留待结果；切换为有效授权允许时，SDK为待结果创建唯一主输入。重复事件/重复恢复仍只有一个逻辑消费记录。
6. 汇总依据逐项机械核对；必要结果全部处理、追加任务收口、无审批/unknown效果后完成G。M保持open，可接受下一Goal。另跑取消变体验证取消G不会关闭M或撤销已经发生的效果。

## 13. 可直接验收的交接结论

SDK交付完成的最低标准：公开入口即可让同一持久主会话创建两个工作会话，原生Mission在共享容量下并行；主会话继续收输入；结果可靠回流并受宿主策略控制；重启/重复投递/取消/审批不会丢责任或重复产生可对账业务效果；提供SDK-01～16证据及兼容说明。

AgentOS完成的最低标准：AO-01～08在真实接口和页面接通、权限与回执实测不退化、浏览器断线不杀后台责任、重启与回滚可追溯；SDK通过并不等于AgentOS通过。

这份文档不要求再让用户决定锁、队列或类名。工作会话可多轮与并行作为目标能力；自动续办提供hold/授权内推进两种策略，由AgentOS明确配置。若实施发现不能满足某项必须合同，SDK方应指出冲突的具体条款、证据和替代行为，而不是静默降级成串行任务列表。

## 附录 A：原始证据与复核入口

固定SDK仓库：https://github.com/DennyWanye/simple_harness ，ref见§2。以下路径相对于 `sdk/simple-harness-sdk/`：

- `src/simple_harness/agents/runtime.py:429` 与 `agents/base.py:112`：持久身份与输入；后者完整路径为 `src/simple_harness/agents/base.py`。
- `src/agent_orchestrator/host/sessions.py:15`、`host/session.py:279`：公开Session与状态/事件接口；后者完整路径为 `src/agent_orchestrator/host/session.py`。
- `src/agent_orchestrator/host/delegation.py:106`：单Session在途限制；`host/inbox.py:33`：结果投影，完整路径为 `src/agent_orchestrator/host/inbox.py`。
- `src/agent_orchestrator/host/session_chat.py:207`：固定控制命令；`src/agent_orchestrator/host/host.py:109`：DomainTool只读限制。
- `src/agent_orchestrator/deployment/assembly.py:83`、`src/agent_orchestrator/scheduling/allocator.py:392`：Mission/TaskGraph和调度。
- SDK Session结果与恢复测试 `tests/orchestrator/host/test_session.py#L474-L535`、幂等重放测试 `tests/orchestrator/host/test_session_replay.py#L65-L209`、两会话隔离测试 `tests/orchestrator/host/test_host_sessions.py#L58-L83`（均在 ref `47fd34f0`）。研究只阅读测试，不将其计作本环境通过。

AgentOS固定提交见§2：`host/agentos_host/sdk_runtime/runtime.py:1078`（按task创建）、`host/agentos_host/sdk_runtime/core.py:487`（creation_key）、`host/agentos_host/sdk_runtime/delegation.py:65`（同步等待）、`host/agentos_host/web/routers/agent.py:252`（断线）、`host/agentos_host/sdk_runtime/lifecycle.py:38`（启动收敛）。实际核对见 AgentOS 仓库 `sdk-main-session-research-20261030/notes/task-b.md`。

辅助材料（AgentOS 仓库内）：`sdk-main-session-research-20261030/notes/task-a.md`（SDK原始能力核实）、`notes/task-c.md`（外部验收原则）、`REPORT.zh-CN.md`（研究报告）。外部持久工作流文档只解释验证原则，不能用作本SDK已实现能力的证据。
