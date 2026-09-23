# 原生接线合同：producer → 来源 → 事务 → consumer

所有 NEW 表示本次明确开发目标，不是假称已存在。源符号在hand-off里定位的使用 REPORTED；本机集成人记录实际后继签名/hash。不为命名不同另造第二套服务。

## 1. 对外与内部生产入口

| 目标签名 | 唯一caller / writer | 来源与输出 | 失败/恢复 |
|---|---|---|---|
| `NativeCreationService.create(config, profile_ref, creation_key, caller) -> BaseAgentReceipt` | 原AgentRuntime.create/create_many/delegate | 原creation intent→kernel defer-drive→binding_locked+protocol+session+adoption | 同key异体冲突；C0后崩溃以同Run完成，不激活半成品 |
| `ExecutionUow.create_agent_binding_locked(conn, binding, receipt) -> None` | 原create facade及NativeCreationService | 已登记原Run；caller明确conn，禁止内部commit | facade无外层时负责开txn；层级检查conn.in_transaction |
| `RuntimePlaneService.submit_input(agent, input_id, value, caller)` | 原BaseAgent.submit/signal | 原input hash+Journal append+Turn+原receipt同txn | 同input_id重送回原结果；禁止accept新输入到closing |
| `ProtocolGroupAdapter.capture(agent, turn, highwater) -> ProtocolGroupSnapshot` | composer和INDEX enqueue | 原Journal+原tool/effect receipts+checkpoint→闭组及锚点 | 不完整receipt保持OPEN；同closed group source变更错误 |
| `ContextSourceReader.capture(owner_binding, request_scope) -> ContextSourceSnapshot` | prepare（锁外一致读取） | 精确TaskGraph/InputManifest/Assurance证书/原config/目录 | standalone的N/A凭创建mode；有Mission来源缺失拒绝 |
| `NativeMeterAdapter.measure(prepared_wire, original_estimator, model_limits) -> TokenReceipt` | 原AgentProviderWire最终renderer之后 | 现有DeepSeek/HF计量、P/O与实际serializer | 任何计量覆盖缺口拒绝，不走UpperBound猜测 |
| `ProviderInvocationCoordinator.prepare_with_context(source, candidate, ordinal)` | 原Provider准备主链 | 原request/reserve/import＋ContextManifest/blob root | Orch预算事实与exec按原桥，不跨库伪txn |
| `ProviderInvocationCoordinator.cancel_unsent_for_replacement(request, reason, caller)` | 原admission发现来源失效 | 原request的row/fence＋no-send final＋hold settlement | 与handoff竞争失败则核对原请求；不能换payload |
| `RuntimeExposureImporter.import_input(original_receipt, context_ref)` | 原collector/BIND_IMPORT | actual wire/input hash+Context map→原Assurance exposure | 原call receipt先持久；同key同body幂等；不是prepare就曝光 |
| `SessionIndexCoordinator.enqueue_closed_group_locked(conn, group, session, index_generation)` | 原Journal追加/闭组writer | 同exec UOW写INDEX typed payload+原event | 只排已闭组；同key异source冲突 |
| `SessionIndexWorker.process(job, resource_pool)` | 原runtime tick | 原embedding invocation→partition commit→central ACK | 具体崩溃窗口见JOBS；不可免费重做UNKNOWN |
| `SessionRetriever.search(access: SessionAccess, request: SearchRequest) -> SearchPage` | model session_history.search /管理adapter | 固定session当前访问＋全范围冻结index snapshot | SCANNING进度不是空命中；按cursor复核访问 |
| `SessionRetriever.read(access, request: HistoryReadRequest) -> HistoryReadPage` | 原history.read及管理读口 | 原Journal精确seq/UTF8范围，源原文hash | index不决定原文存在；无权记录以范围证据区分 |
| `ContextPolicyCommands.submit(policy: Policy, caller, command_id)` | Host agent_context_policy_submit | 当前认证批准→原CAS policy→arp_policy_objects+原receipt | 不收caller字段；同command异body冲突 |
| `RuntimePlaneService.update_settings(command, caller, command_id, agent_id) -> CommandReceiptView` | Host设置入口 | effective policy CAS＋adoption+event+receipt同exec | expected_ref/rev必须一致；已冻结请求不变 |
| `CapabilityResolver.resolve(owner, requirement, input_ref, scope) -> CapabilityBinding` | 原AgentBridge/Operator执行准备 | realm owner目录＋实际部署＋当前policy | 精确硬门后批准priority；无cap拒绝，不切换Runtime |
| `ToolExposureService.prepare(session, requested_tools) -> ToolSnapshot` | runtime_catalog adapter | 原定义+scope+model-name映射 | exposure只有后续真实input receipt；撤权再查 |
| `ToolDispatchAdapter.dispatch(context_ref, call_id, raw_args)` | 原ToolGateway | 当前精确snapshot/Schema/TaskScope/Skill交集 | 外部Effect进入OPS，不由工具直写现实 |
| `SkillRegistryCommands.install(bundle_ref, scope, caller, command_id)` | 原Host skill_install | 实际安全解包+原catalog writer+lock | unresolved保存在QUARANTINED，不自动下依赖 |
| `SkillRegistryCommands.begin_trial(command: SkillTrialCommand, caller, command_id)` | 新Host verb委托原owner | 原隔离scope批准+真实eval dispatch/reserve | 不给生产凭据；必须full dependency lock |
| `SkillRegistryCommands.transition(command: SkillLifecycleCommand, caller, command_id)` | admit/suspend/resume/retire管理verbs | 原evaluation Acceptance+current policy+CAS | SUSPENDED恢复须原eval仍适用；RETIRED不可复活 |
| `SkillUseService.load(session, turn, call, skill, paths)` | 原skill.load tool | exact dependency/bundle+权限→原call receipt+E片段 | 同来源片段E只一份；不执行script |
| `SkillUseService.execute(session, turn, call, skill, arguments)` | 原skill.execute | 原ToolCall/reserve+SkillUse绑定→approved executor | 输出无效即失败，不自签Assurance |
| `RuntimePlaneService.destroy(session, expected_generation, caller, command_id)` | 原close完成后或显式destroy | same FileGuard→原cancel/fence→DRAINING→proof→PURGING | 不能仅关UI/丢对象就删；unknown照核对 |
| `SessionDisposalReader.capture(session, guard) -> CompleteSessionDisposal` | destroy/原GC协调 | 7种真实集合与hash；所有reader complete | 无来源不可填count0；原Formal pin不依赖temp不阻止删 |
| `RuntimePlaneService.rebuild(command: IndexRebuildCommand, caller, command_id)` | Host same-root重建 | 新index gen/实际资源/原Journal全量，publish receipt | 新root先Assurance隔离，不借此恢复执行 |
| `RuntimePlaneService.resume_pending(runtime_scope, now)` | 原startup/tick | CREATING intent、INDEX/PURGE/BIND_IMPORT、同prepare的ContextRecall协调、cursor过期清理 | claim≤8/每session≤2；没有SUMMARY创建路径 |

## 2. purpose 的授权与身份来源

`RuntimeAuthorizationAdapter.evaluate(TrustedRuntimeRequest) -> AuthorizationReceipt` 是新适配器，**唯一调用既有真实权限评价点**。请求类包含 `caller_ref、service_owner_ref、agent_ref、owner_contract_ref、purpose、target_refs、input_hash、expected_policy_refs`；这些来自上游认证/运行绑定，不从LLM payload取。

| purpose | 当前权威输入 | issuer /读取门 | 拒绝 |
|---|---|---|---|
| CREATE / SETTINGS / CLOSE / DESTROY | Host认证principal/tenant＋原owner配置与管理policy | 原Host固定caller命令评价点 | 非owner、policy过期/缺失拒绝 |
| STANDALONE_CONTEXT / HISTORY | 显式standalone profile、原agent owner、session read policy | 原runtime authorizer+Assurance root gate（启用时） | 缺mode marker不当standalone |
| MISSION_CONTEXT / RETRIEVAL | 原Mission grant/TaskScope、InputManifest、Assurance read_use | 原编排policy reader→scope readset→runtime receipt | readset不完整拒绝；不借planning grant执行工具 |
| PROVIDER_CALL / EMBEDDING | 原service identity委派、Agent lifetime/Task预算、部署读取权限 | 原ProviderBudgetGuard/InvocationCoordinator | 费用未知保留hold，不发免费调用 |
| READ_TOOL / SANDBOX_TOOL / SKILL_LOAD | 原ToolGateway授权、workspace边界、Skill要求交集 | 原Tool授权writer/receipt | 指令不能扩权限 |
| EXTERNAL_EFFECT | OPS intent/official review/original approval/operation gate | 既有Operation handoff | 无效的ARP receipt不能替代审批 |
| CATALOG_INSTALL / TRIAL / ADMIT / SUSPEND | 原Host项目/用户管理主体＋catalog owner委派 | 同一managed registry command writer | 不接模型principal；元数据不等许可 |

receipt记录实际evaluate的policy版本/hash、issuer、输入hash、scope、observed/expiry、原decision receipt。原API只给bool时在原评价点追加真实recorder；不能在下游补一张ALLOW。新工厂ARP配置遇AllowAll实例拒绝，legacy/demos原行为不改。

## 3. 必须具名的内部快照

- `SessionAccess(session_ref, agent_ref, turn_ref|null, root_incarnation, purpose, caller_ref, authority_refs, authority_readset_hash, control_generation, expires_at_ms)`：只有原request或认证API构造；不序列化给模型作为授权token。
- `ContextSourceSnapshot(owner_mode, source_highwater, sections, required_group_pins, original_input_ref, task_contract_ref|null, requirement_ref|null, completion_scope_ref|null, plan_ref|null, use_certificate_refs, source_readset_ref, catalogue_witness_refs)`：同源caller绑定；nullable仅显式standalone适用分支。
- `ContextCandidate(messages, tool_schema, response_schema, media_refs, group_snapshot_ref, exact_source_views, allocation, retrieval_receipt_ref, effective_policy_ref, adoption_revision)`：纯内存候选，不能持久为actual exposure。
- `OriginalCallView`：原InvocationCoordinator的既有状态/type，adapter提供 `call_ref, request_hash, handoff_refs, reserve_refs, usage_refs, row_version, transport_state, terminal_reason`；transport_state只是原事实映射，不写第二份枚举到ARP库。
- `CollectionWitness/CompleteSessionDisposal/TokenReceipt/IndexSnapshot`：机器字段直接见同名Schema，构造后必须跑semantic validator，不能仅非空字符串。

## 4. Store连接级约定与幂等

新 `_locked`方法都显式接原connection并assert in_transaction：`put_creation_intent_locked/finalize_creation_locked/put_policy_object_locked/append_policy_adoption_locked/put_context_locked/put_job_locked/complete_job_locked/publish_index_generation_locked/put_catalogue_revision_locked/transition_activation_locked/put_skill_use_locked/append_runtime_event_locked/put_retention_permit_locked`。

原facade如自行开transaction，重构为公开wrapper→上述连接级函数。不得把wrapper套入新transaction后假设不会commit。原provider预算协调用原protocol，不调用Orch API时持有SDK事务。旧调用关系回归必须保留。

所有同identity insert先读原receipt/body，同hash返回原值，异hash冲突；SQL不使用OR REPLACE。不能把所有异常转换成空列表/None。只读get精确键，禁止latest改写原pin。

## 5. 实际计量器与embedding适配入口

`NativeMeterAdapter` 的生产owner为runtime/assembly中注入的现有 estimator；`measure_prepared(request, prior_context)`: 
1) 获取实际 `AgentProviderWire` final typed request；
2) 使用当前DeepSeek/HF原方法测wire；若原方法返回含P的aggregate，从其正式prior接口取P并做一次明确分解；不能按返回数字猜；
3) 构造TokenReceipt并检验 `prior_basis_ref`、P nonnegative、limits、input scope；
4) 原provider request_preparer持久；handoff核hash。
如果当前专用计量器没有可提取的分解入口，允许在同文件提取纯helper `measure_wire_and_prior(...)`，保留旧方法为原返回封装。这是已授权普通实现工作；不再问用户选估算算法。

`MeteredEmbeddingAdapter.embed_with_receipt(owner, call_key, texts, resource)`: 原call/reserve持久→有界offload原同步EmbeddingPort→原真实result/usage→typed receipt。原execution没有embedding purpose时新增该原purpose的payload/result codec和adapter；**不创建独立usage表**。本地NO_PROVIDER_CHARGE无需虚构外部billing；输入tokens只在真实tokenizer计量后填。HashEmbedder只用于故障测试。

## 6. R1–R5补正后的内部入口（新增语义，不改历史wire）

| 方法 | caller / lock / 结果 |
|---|---|
| `ContextRecallCoordinator.start(access, ContextRecallRequest)` | 原prepare adapter；C0原exec UOW、C1按原锁序；Progress或Result，绝不裸items |
| `ContextRecallCoordinator.resume(recall_key, access, *, now_ms, clock_receipt_ref)` | 原prepare/tick/startup，同原身份/预算/截止；无新的模型回复 |
| `SessionSearchService.start_frozen(access, request, snapshot, query_call_receipt, query_id)` | 仅internal coordinator；highwater/exclusions从request，不取latest；CONTEXT_RECALL purpose |
| `SessionSearchService.resume_frozen(access, recall_key, cursor)` | 同冻结查询与真实读权；cache page重送不再推进 |
| `ExecutionUow.put_context_recall_locked(conn, request, query_id)` | 同prepare identity原子建立协调；不是Provider call writer |
| `ExecutionUow.cas_context_recall_locked(conn, key, expected, progress_or_result)` | 同row CAS，immutable request/result；只存原call引用 |
| `SessionLifecycleService.record_purge_progress_locked(conn, session, destroy_id, expected_version, progress, observation_receipt)` | 同state可写blocking/phase，同destroy/gen；实际source recorder和事件同事务 |
| `RuntimeRetentionService.resume_purge(session, destroy_id, caller)` | 同目录marker/原receipt重新检查，只继续原phase；不允许借普通rebuild复活 |

Policy硬上限见CONTEXT-SEARCH C7；非法配置在提交/采用时拒绝。Search与ManagementSearch的Receipt/Page/ContextSummary统一`status_for`与nested semantic decoder。计量与原Provider handoff不受本次修改。

R1生产恢复caller：Host `agent_session_destroy_resume(SessionDestroyResume)`→原认证dispatcher→RuntimeRetentionService.resume_purge；自动FILE_BUSY重试由原tick用同destroy身份执行。原`agent_session_rebuild`必须在权限检查后再拒绝destroy_command_id非null的Session，不能用普通重建API清除删除阻断。
