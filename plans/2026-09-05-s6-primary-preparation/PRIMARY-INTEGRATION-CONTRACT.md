# S6 primary runtime/API 对接契约

最后更新：2026-09-05。本提交是隔离树可组合测试候选，不可据此合main/宣称S6产品完成。
service/API使用Dirac `fbc026a0`及其terminal identity修复后继；UI由Popper独立交付。

## 实际运行与授权

None admission保留真实PrimaryRunWorkContext（scope/root=None、binding revision=0），
允许冻结受SDK PROJECT_EFFECT/route-required策略约束的工具。模型必须先通过生产
ContextRouteToolService，真实route receipt/S4 binding/root/lifecycle经EffectGate验证；
原scoped Run仍检查冻结scope/root。物理dispatch期间的临时ContextVar仅携带已验证的
exact effect envelope/root，供产品authorization和文件handler使用，finally清除。
不会给Run伪造TaskScope，不将某次effect的root升级成整Run默认root。

生产动态验收路径：None → resume_existing → tool_search/describe/activate → write_file
→ task_scope_update → terminal gate/receipt/outbox。write_file用生产handler向tmp root落盘；
Provider和authorization response为deterministic测试适配，真实SDK/ReAct/SQLite/checkpoint、
route service、binding验证、EffectGate、semantic closure和terminal事务均非mock。
已绑定TaskScope是fixture前置条件；这不证明create/manual UI闭环。

`create_new`调用WorkspaceBindingRuntimeAuthority，Manual返回真实binding challenge，
route handler目前只返回context_route_binding_authorization_required；**不会产生旧
project_directory_request/SDK external wait**。现有效响应是绑定human_memory_request的
binding.manual.propose(scope_ref,root) / binding.manual.decide(challenge_ref,decision)，
完成后resume_existing。旧project_directory_response不是这条产品链。

## 唯一terminal identity解释

`execution.terminal_identity.read_primary_terminal_identity_tx(db, *, subject, primary_ref,
host_run_id, sdk_run_id)`在调用者Host snapshot内读取，要求aiosqlite.Row。
无匹配committed terminal返回None；存在但链缺失/不匹配抛stable RuntimeError。

DTO `PrimaryTerminalIdentity`字段：sdk_run_id、raw_sdk_event_id、raw_sdk_event_hash、
terminal_state、host_receipt_ref/hash、legacy_scoped、observation_evidence_id。
`verify_sdk_terminal(actual)`比较public SDK terminal的run/event ID/hash/state，STOPPED映射cancelled。

Host receipt.sdk_event_id/hash仍是**Host terminal authority**，没有改ledger语义。
- 新standalone：receipt → primary observation主键/封套hash → payload内raw SDK id/hash。
- scoped/legacy：receipt → ingest receipt + gate + ExecutionEvidence哈希链；raw id=event_id，
  raw hash=public_payload.sdk_terminal_event_hash。检查subject/run/kind/state、正整数generation
  不大于最终Host generation、input evidence_refs与admitted turn对应。
- scoped同时有observation时，交叉验证两条链的raw SDK identity。
- 验foreground SDK binding_json/hash、Host receipt hash、subject/primary/turn关联。
- 旧observer fixture自然不提供primary reader，仍生成真实scoped watermark/closure；
  不DROP TRIGGER/DELETE原始evidence。新observation缺失不允许当legacy正向恢复。

## 已有真实来源与未闭合suppression

PrimaryHistoryStore实际组：`turn_id, terminal_state, source_ref, source_hash, messages`。
新组source_ref/hash是Host primary observation ID/envelope hash；legacy是Host terminal
receipt派生锚（包含actual SDK terminal hash和public messages）。候选查询绑定真实
primary/subject/turn/Host run/SDK run/terminal，不读取legacy SessionDB聊天历史。

可用Host来源：foreground_turns.evidence_id/evidence_hash、turn_id/enqueue_sequence、
primary_conversation_id/subject；SDK binding ID/hash；Host terminal receipt及上节normalized
raw SDK terminal identity。新observation记录实际message组、turn/run、generation及raw event。
公开SDK reader使用SqliteContextPort.load；实际当前user文本定位本turn后缀。

primary_context在prepare阶段构造primary_history_ref/hash与history_sources(ref/hash)，
PreparedSdkContextSnapshotV1的snapshot_id/fingerprint绑定这些源和消息；RunStart保存
context_authority_ref/hash/snapshot_id与实际provider_messages。没有完整memory/entity
source依赖列表可从这些字段逆推出，不把model声明的evidenceID当来源证明。

Host format_epoch（human-memory-v1）/schema migration marker是存储格式身份；provider
binding_epoch、foreground generation、control connection epoch是各自authority世代。
**均不是Memory suppression epoch**。Memory公开resolve_suppression的当前decision/checked_at
可用于给定candidate；当前公开API不能展开完整历史memory/evidence/entity lineage。
没有新增SDK接口、读取Memory私有SQL或捏造epoch。完整历史来源suppression按主授权留到下一提交，
本提交的runtime history仍未闭合此gate，不可直接作为默认产品完成状态。

## API factory与消息投影

main惰性注入三kwargs，不提前绑定None：
- settled_run_reader → stack.read_settled_primary_run(sdk_run_id, current_text=...)
- suppression_resolver(candidate,purpose) → current HumanMemoryV7Runtime.manager().backend.resolve_suppression
- run_binding_reader → stack.read_closure_run_facts(sdk_run_id).binding_record（不是整个facts）

public transcript保留文本、tool name/call_id；artifact content block只保留
artifact_ref/name/mime_type/uri。system/reasoning/opaque continuation/任意metadata不投影。
SDK0.7.2 Provider输出入口不接受artifact block，所以artifact测试仅是public Context投影，
不声称Provider生成artifact。tool组缺完整原始tool-call request时，历史整组作为带来源的数据引用，
不伪造调用或孤立native tool messages。

queued/live/terminal有界分页、当前READ suppression与exact control由API owner负责。
queued只用真实admitted evidence，live用exact run关联后的public SDK Context；终态以Host
commit可见性为准。API final shape见Dirac PRIMARY-API.md；sdk_run_ref/execution_session_ref
只能在真实绑定验证后供UI关联，不是authorization凭据。

## 连接fence与轻量刷新

main仍复用/ws/control verified connection，在handle_human_memory_command外安装
HumanMemoryControlBinding.request_scope。canonical DB事务前取得现shared revocation barrier，
重验当前connection/epoch/durable lease；commit/rollback后释放。reconnect/unbind使用现
exclusive barrier。新runtime background task不继承旧socket写权限，无每读签名/新账户体系。

ForegroundRuntimeExecutionAuthority可选`state_changed: Callable[[], Awaitable[None]]`；
main接现_broadcast_control({"type":"human_memory_changed","payload":{}})。
claim/bind/start/terminal/control/pause ACK和driver error触发空payload提示，由UI绑定API补读。
单独合并任务每次500ms限时，异常不阻塞业务/不回滚事实；close取消，无第二ledger/订阅框架。
重连补读，不依赖legacychat_v2_final。

## T4/T5消费边界

ForegroundQueueStore.record_sdk_terminal是单一事务：绑定/lease/真实终态/必要closure校验，
再terminal receipt + Run/turn settled + existing memory_ingestion_outbox一起commit。
T4 occurrence settle应在同一db事务接入；当前没有placeholder callback。
T5复用UUID5(memory-ingestion-outbox:{sdk_run_id}:{turn_id})及同一user evidence link；
不按runtime observation另建第二analysis job。source lineage补足另交主/Hegel设计。
