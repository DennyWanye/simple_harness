# S5c 具体接口设计（拟议，供 review）

2026-09-05。**这是初次准备稿；本切已定稿并实现的内部接口及G6版本化限制见 [T1-T2-INTERFACE.md](T1-T2-INTERFACE.md)，优先于下文旧的kwargs草案。** 下文 `Host*`/`Immediate*` 是拟议新接口；标为“现有”的方法已从源码核对。没有实现/注册/调用它们。范围及任务见 [README](README.md)，原文/hash 见 [SOURCES](SOURCES.md)。

## 1. Authority 与 ownership

现有 Harness 0.7.2 接口保持原签名：

```python
async def resolve_prospective_signal_authority(
    reference: ProspectiveSignalAuthorityRef,
) -> ProspectiveSignalAuthority: ...

async def resolve_memory_action_authority(
    reference: MemoryActionAuthorityRef,
) -> MemoryActionAuthority: ...
```

Host 新 `HostProspectiveSignalAuthority` / `HostMemoryActionAuthority` 只解析 state.db 的 durable exact authority；核验完整 principal/deployment/household/scope、source receipt/hash、target memory/revision、intent hash、Run/operation 身份。不接受模型提交的自签 authority，不从当前 DB head 重新编造旧 ref。Memory 自己消费 replay identity；Host resolver 不冒充 Memory consumption fence。

`HumanMemoryV7Runtime.__init__/build_kwargs` 新增这两 required ports，并由 `_build_product_sdk_runtime_stack` 及其单一 composition owner 注入同一对象；缺任一 production startup stable fail。保留 evidence authority、analysis executor/delivery 同对象断言、owner 幂等注册与 shutdown ownership。

`MemoryActionIntent` 现有字段绑定 subject/action/target memory+revision、evidence refs+span hashes、Run/Turn、plan_id+plan_intent_hash、operation_id+canonical index+operation_intent_hash。因此先冻结无 authority 的 exact plan intent，再由 Host 从用户 evidence/授权事实发行 authority、填 ref；不能先造通用“允许修改记忆” grant。REVISE/SUPERSEDE/SUPPRESS 之外不加动作权限。

## 2. Registration → signal → occurrence

现有 Memory 公共 facade：

```python
await manager.read_outbox(principal=p, states=("pending",), after=cursor, limit=100)
await manager.apply_prospective_signal(principal=p, scope=s, reference=ref)
await manager.read_occurrence_inbox(principal=p, after=cursor, limit=200)
```

`read_outbox` 是只读，不 claim/settle；顺序为 `(created_at,outbox_id)`，payload/hash 可用。Host 拟议接口：

```python
await scheduler.project_outbox_page(principal, page)  # 本地一个事务
await scheduler.deliver_prepared_signals()           # 跨库可重放，不跨库伪事务
await scheduler.observe_event(event_receipt_ref)     # 只接受 Host durable event
await scheduler.tick(now)                           # 唯一 clock/lease owner
await occurrence_store.reconcile(principal, disclosure_context)
```

1. 首次 cursor 从空开始，纳入 S5b 积存 pending。逐行校验 topic/payload_hash，接受 registration/invalidation 并存原 outbox ref；无关 topic 只做该 consumer 的已检查位置，不消费其业务；malformed 不越过而静默丢失。
2. **同 state.db 事务**提交 registration/invalidation、固定 signal intent/authority、消费 cursor。cursor 仅指已 durable 接收，Memory 回签尚未完成时留可重试 pending signal，不意味着 occurrence processed。不能 cursor 已提交却无本地可恢复 signal。
3. signal_id 从 domain+principal+outbox_id+signal_kind 派生；registration ack 必须带原 outbox_id/payload_hash，transition_from=transition_to。保存完整 typed intent/ref/issued_at/expires_at/nonce，重放不重新取当前时刻；已应用返回 Memory 同 result。过期且尚未应用必须显式续接同业务 signal 的 authority 协议，不能靠改 observed_at 或新 event identity 偷过；T1 conformance 必须验证这一分支。
4. 调 public `apply_prospective_signal`；核对 result 的 signal/target/base/committed revision/outcome，再记录回签。`REGISTRATION_ACCEPTED` 完成后才 live；invalidation 先本地阻止新 claim，再持久回签 `REGISTRATION_INVALIDATED`。各自 receipt 可独立重放。
5. due signal 固定首次 observation（不能每 tick 用新 now），event signal 绑定 Host exact event receipt+condition hash。authority DTO 的 run_id/operation_id 使用可解析 Host source lineage，不凭空制造 foreground Run。time 是 pending→triggered；事件同理。Host 不从终答“发布成功”几个字推断外部效果成功。
6. **从 Memory inbox**接收 matched occurrence；使用协议产生的 occurrence_key，不自建另一个 hash 算法。Host signal 唯一约束阻止同 trigger revision/event 换 signal_id 产生第二 key。重新读取当前生命周期、suppression、current DisclosureContext 后才 claim/present。

Event bridge 最小目标是本 scope 已提交、确证成功的发布事件来源。`analysis_proposal.py` 需 time/event 严格 union，event_name/source/condition 与现有 `ProspectiveEventTrigger` 对齐；引用可解析的 Host event authority，禁止任意模型字符串成为可信事件。若 Host 尚无可解析的真实发布 receipt，T1 标明具体 source 缺口；不能以整 Run COMPLETED 等同发布成功或新增外部发布能力来凑验收。

## 3. v47 持久化布局

所有表在 Host state.db；`039_...v47.sql` 仅为拟议编号，实施前复核。v45/v46 文件字节不变、marker/checksum遵循现 migrator；不复用 Companion DB。

| 新表 | 最小事实与唯一约束 |
|---|---|
| `prospective_scheduler_registrations` | append-only registration/signal 记录：principal全身份、outbox_id+payload hash、memory_id+revision、typed trigger/hash、registration ref/revision、source event ref、固定 intent+authority+Memory result。unique event_id；source outbox/domain phase 与 signal identity 唯一。lease 可由追加 claim epoch/expiry记录派生，不能覆盖 authority。 |
| `prospective_outbox_cursor` | consumer+principal、页序号、prior cursor/hash、next `(created_at,outbox_id)`、page commitment；append-only CAS推进，与本页 registration 同事务。event消费水位用独立 consumer key，不与 occurrence ack 共用。 |
| `prospective_occurrences` | append-only claimed/presented/acknowledged/settled/overdue 事件：occurrence_key+memory/revision、principal、inbox event ref/hash、Run、snapshot receipt/hash、prior event hash、reason、receipt。partial unique presented `(principal,occurrence_key,sdk_run_id)`；ack同键；overdue `(principal,occurrence_key)`一次。canonical状态/count由链派生，可索引，不把计数当processed。 |

A7 的 overdue 用第三表事件，不新增 overdue 表。v45 `occurrence_presented` 仍只是 mandatory exit 投影，不当 canonical presented 日志。遗忘及 immediate 另需要 exact command/authority/result journal：拟议新增 **一张** `memory_action_events`（event_id、action_id、phase、principal/Run/Turn/effect、request+intent hash、source evidence refs、authority ref/body、job ref、suppression receipt、prior hash），phase 的幂等 unique 绑定 action_id+phase+payload hash。这是 AC-5 耐久性所需，不声称 A11 已提供此表；T1 review其是否可无损复用现有 Host ledger，若不能则随 v47显式登记。不能将这些事件塞入只允许 pause/stop/cancel 的 foreground signal 表。

所有 cursor/state变更验证 exact prior/hash；同 ID不同payload冲突，不通过修改 revision解决。Memory/Host跨库用先durable intent→public call→durable receipt的重放协议，不 SQL写另一库。

## 4. Snapshot / ack / terminal

新 Host ack tool schema：`{"type":"object","properties":{"occurrence_key":{"type":"string","pattern":"^[0-9a-f]{64}$"}},"required":["occurrence_key"],"additionalProperties":false}`。principal/Run/Scope由 trusted ToolContext解析，不收模型字段。五路可见、Host内部状态工具、不借文件权限或 workspace grant。

`prepare_snapshot` 必须把经过当前 disclosure+suppression重验的 inbox 集合固定下来；`record_snapshot_receipt` 的同一事务插入 presentation事件与snapshot receipt。第3次跨Run呈现时保护消息包含 overdue=true并同事务唯一追加overdue事件。同Run重放只复用原presentation；后续 provider turn仍能保留待ack上下文，不把再次注入计为第二次呈现。

拟议 `ack_presented(ctx, occurrence_key) -> {receipt_id,receipt_hash,occurrence_key,state:"acknowledged"}`：验证 key来自本principal、本Run已提交snapshot的presented事实且仍live；ack事件与v45 mandatory-exit投影同事务；先读幂等receipt，lost-ACK返回原件。非法/他人/未呈现key稳定拒绝且不变状态。没有ack的Run失败/拒绝不改变processed；已有durableack不因随后Run失败而抹除。

终态事务读取ack，将canonical settle(acknowledged)与原Host terminal/evidence/outbox结合；crash时由同Run重放或下一次观察到ack的terminal结算。Memory triggered→completed由该ack轮的analysis严格plan完成，不由scheduler直接更新Memory head。

suppressed/FORGOTTEN → settle(suppressed)，**永不写 v45 投影且内容不进入Context**；superseded/expired按A7退出且写离开投影。无N次自动settle。

重放有两种情况：源事实未变，snapshot/provider-send checkpoint恢复必须保持原hash；suppression/disclosure已经改变，旧snapshot不能再次发送，必须走现有公开失效/新snapshot契约（不能原地改checkpoint字节）。当前接缝尚未证明能同时满足两者，T1/T6必须测出合法路径；必要SDK缺口交主协调，禁止放宽隐私或固定snapshot契约。

## 5. Immediate remember/correct 与公开 priority 缺口

新 `memory_write` 建议严格union：remember `{action:"remember",text}`；correct `{action:"correct",memory_id,target_revision,text}`，additionalProperties=false。解析目标来自当前principal普通typed read的exact ref，不使用旧integer fact_id、tier或pinned等legacy语义。兼容旧payload只可稳定解释/拒绝，不能静默承诺永久pin。

Host `prepare_immediate_write(ctx, request)` 同事务保存 exact command+用户evidence来源，返回 `{action_id,state:"pending_terminal"}`。这不是已物化receipt。终态在原ingestion outbox中绑定immediate intent；同 evidence只允许一份analysis job，不能普通+immediate各调一次。Run失败的explicit intent以durable事实裁定是否可执行，不从内存标记偷跑。

当前 `ingest_committed_evidence(...,analysis_lineage=...)` 无priority；`claim_analysis_batch(config,worker_id)`先reclaim再按oldest选pending，`MemoryJobWorkerConfig`无priority。建议最小 **待评审 SDK contract**：在ingest新增可选 `analysis_schedule: AnalysisSchedule`（class=`ordinary|immediate`、Host action ref/hash；default ordinary），与evidence/job同事务固定；重放不同schedule报冲突。claim优先选择eligible immediate，immediate免普通max_batch_wait，不混入普通batch；同Run多个显式操作保持原lineage和批次一致性。不能用任意metadata暗示priority或Host直写Memory jobs。

该扩展不需要新 LLM/extractor，也不允许抢占 handed_off/result_committed batch。保持0.6.3同principal fence：先收敛旧durable result固定plan/base_revision，再claim immediate；活跃任务未完成的等待不等于ordinary pending优先。已有持久result仅重放，不增provider calls；未知投递仍按not_sent/sent_unknown/sent_confirmed分类。

Host compiler增加REVISE/SUPERSEDE与Prospective lifecycle update，先冻结grounded result和无authority的plan intent，再绑定由exact用户指令或ackreceipt支持的action refs。若proposal需要confirmation但缺exactauthority，保留blocked/needs-confirmation真实结果，不调用另一LLM重生plan。原SDK功能冻结仍有效，本轮仅提交接口需求给主协调；不能声称Host-only可以完整解决G6。

## 6. 同步 forget 与全部普通读取

现有 `manager.suppress(SuppressionRequest, principal=p)` 不接 `MemoryActionAuthorityRef`。最小Host-only同步链：新 `authorize_forget(ctx, exact_target) -> HostSuppressionCommandRef`从已授权用户directive、exactprincipal/target和effect事实durable发行命令；新 `apply_forget(command_ref)`解析该命令，构造固定 `SuppressionRequest(request_id,subject,scope_kind,scope_ref,reason_code,requested_at,purpose=None)`，调用公开suppress；Host保存decision_id/hash后才返回工具成功。request_id/time固定，Memory已commit但Host未落receipt时同请求重放即可，不生成新directive。

这个Host suppression command是Host的memory_action authority服务职责，**不是**伪造SDK `MemoryActionAuthorityRef`消费。SDK注入resolver仍负责correct及严格plan mutation；如果review要求suppress自身必须消费SDK action ref，现有API确实不满足，需另列exact契约差额，不能把两种receipt混称。

forget公开tool建议 `{memory_id,target_revision}` strict；自然语言目标由主模型用当前普通typed recall取得exact候选，歧义保留clarification，不把query当全subject suppression。用户已授权的exact命令不重复问确认。nonce/token不进入普通日志/Context；正常领域receipt hash可作为证据。

成功receipt是读取屏障：其后新发起的 typed recall/search、TaskScope search/open、六views、ResumePackage、short-horizon、Context、graph、exact ID、旧checkpoint及普通trace必须重验suppression。不能把原TC-HM-07步骤3简称“六路”后实际只测6个函数。对Host缓存/Archive中同一内容用原evidence来源关联过滤，不能只假设Memory已过滤。raw evidence/lineage只追加且row ID/content hash守恒。重放泄露与普通trace内容也纳入负例。

## 7. review 必须回答的具体问题

1. G6的公开priority参数/可信来源验证/无双job事务能否以最小Memory版本增量满足？当前冻结候选不改。
2. event trigger的真实Host source receipt是什么？若没有，标缺口，不以假发布事件声称真实端到端通过。
3. Host同步suppression command与SDK mutation action resolver的分工是否满足AC-5？canonical action journal复用或新增一表，列明事务约束。
4. suppression后的旧snapshot如何通过公开契约失效，同时保持未变化事实的replay hash不变？不能简单改revision。
5. registration signal authority过期但Memory未apply的合法续接如何保留原业务signal/occurrence identity？先做公开DTO与同DB重开conformance，不伪造新event。

以上是可review的具体实现前置项，不是新增AC或要求用户再次批准已授权任务。本轮到准备文档为止。
