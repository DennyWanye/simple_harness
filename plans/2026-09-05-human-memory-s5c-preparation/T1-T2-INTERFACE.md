# T1/T2 本切片接口定稿与实现边界

2026-09-05。基于用户本轮明确授权实现独立 Host schema/store/authority，不修改原 AC，不增加权限，不发行泛化 grant。执行方式：本会话串行（同一事务/表簇），其余 S5c 任务不实现。原 S5 Task7、A7/A8/A11 来源保持 [SOURCES.md](SOURCES.md) 的原文/hash。

先合并 main `c183fe70`（含 Q1 `4eb1eb7c` 与 exact legacy downgrade 用例），merge commit `c9c634d412a63c98288ee9cd1e4ac6b9130cd1b1`。不追随合并后 main 的其他 WIP/pin。当前代码仅在 `feat/human-memory-s5c-preparation`，未合入 main。

## 最小布局与必要性

4 张领域表，不建立通用 event-store，也不建立第二 timer/lease authority：

| 表 | 本切已使用的事实 | 后续保留的阶段（本切无写入口） |
|---|---|---|
| prospective_scheduler_registrations | 每个 owner/outbox 的 prepared signal、原 outbox immutable payload/hash、exact authority JSON/hash；回签身份固定 | applied；T3 决定性 Memory result receipt/重放接线尚未交付 |
| prospective_outbox_cursor | 与 prepared registration 同事务，append-only sequence/前驱hash/位置；CAS expected_cursor | 不表示 processed，不代替 occurrence claim/ack |
| prospective_occurrences | Memory inbox matched/live 的幂等 claimed 记录；canonical occurrence key；不写 v45 occurrence_presented | presented/acknowledged/settled/overdue；T4 才连接 snapshot/ack/terminal与严格状态转换 |
| memory_action_events | exact typed MemoryActionIntent 的 requested 记录和已入 Host evidence 绑定；记录请求不是授权 | authorized/suppressed/analysis_queued/applied；无生产授权发行/抑制/调度入口 |

新增 action journal 的代码依据：v46 `harness_evidence_reservations` 仅允许 provider/tool/context/route/run_terminal 且带固定 TaskScope/sequence lineage；`post_turn_invocation_attempts.purpose` 只有 closure/analysis，承载投递五态而非用户 action 请求/plan authority；`host_pre_admission_audit` 只存拒绝且 kind 仅 context_route/task_scope_update/analysis_result。将 action 混入其中会改既有 CHECK/身份语义；本切只加专用表，v45/v46 SQL 字节不变。单表专用 phases 不等于通用动态事件存储。尚未实现的 phase 只是 schema 占位，不能据此声称 lifecycle 可用。

## 显式 schema seam

`deskpet.memory.s5c_schema.initialize_s5c_state_db(path, *, fault_inject=None)`：

1. 只对合法 fresh Human epoch/v46 前向初始化；v47 reopen 验证旧 marker/chain、v47 SQL checksum/实际 DDL、recovery registry/fence；未来版本拒绝。
2. 使用原 migrator 的 transactional-script 与 recovery registry helpers。DDL、4表 recovery 注册、migration-chain、schema_migrations、user_version=47 一次事务提交；active foreground/WAITING 或 recovery 非 OPEN 时拒绝。
3. SQL 固定路径 `memory/migrations/s5c/039_prospective_memory_actions_v47.sql`，migration ID 含 `s5c/`。放在子目录是为了让既有非递归 migration glob 保持默认 v46，非引入第二 schema 框架。
4. **默认生产 HUMAN_TARGET=46 不变**，原 initializer 打开 v47 稳定拒绝；没有 main.py/production builder 调用新 initializer。完整 cutover 尚需独立接线，不能只修改 target 数字让旧 composition 接受半成品。既有 recovery coordinator 的默认 bind 同样只接受生产 v46；测试在 v46合法 bind 后，验证其 fence仍保护v47新表。
5. 本切所有4表 UPDATE/DELETE拒绝，并注册既有 recovery fence；后续维护不得绕过这些不可变记录。

## 已实现内部接口

接口不是模型/WS入口；DB path 与 principal 由 trusted Host composition 固定。owner key 用 deployment/household/actor 的 canonical JSON hash，session不构成新 owner，因此跨Run同owner可接续，跨deployment/household不能借同actor字符串解析。

- `S5cStore(path, principal, *, fault_inject=None)`：构建时验证显式 v47。
- `registration_signal_id(principal, outbox_id, kind)`：确定性领域身份。
- `commit_registration(entry: OutboxEntryV1, authority: ProspectiveSignalAuthority, *, expected_cursor) -> ProspectiveSignalAuthorityRef`：只接受 registration/invalidation 的 exact v1 outbox payload；校验 topic/trigger/revision/outbox/hash/scope/issuer/固定signal ID。一行准备与 cursor 同事务；同来源不同payload/authority拒绝。lost-ACK允许原旧cursor重放已提交同record，不创建新时间/ref。调用者T3必须从 principal-bound public read_outbox连续分页；本API不是完整consumer，不负责过滤/处理其他topic。
- `cursor() -> (created_at,outbox_id) | None`：最后已接收的相关 registration位置，不能解释成Memory outbox已全settled。
- `claim_occurrence(entry: OccurrenceInboxEntryV1) -> record_id`：可信 principal-bound inbox 调用方提供 matched/live entry；durable claim不表示披露授权，不生成snapshot/present/ack、也不写v45投影。来源、当前disclosure与suppression的完整再验证仍属T3/T4/T6。
- `record_action_request(action_id, intent: MemoryActionIntent) -> request_record_hash`：验证typed intent、subject及所有evidence refs对应Host已承认envelope hash+Run；幂等固定完整plan/operation/target revision。未知target/授权语义、span支持与正式grant仍由T5完整链验证，当前请求不能驱动mutation。
- `HostProspectiveSignalAuthority.resolve_prospective_signal_authority(ref)`：只读 exact durable source/authority与ref的完整绑定；不调用Memory、不重签、不延长expiry。Memory SDK继续负责expiry及atomic consumption/replay。
- `HostMemoryActionAuthority.resolve_memory_action_authority(ref)`：只读authorized行，并交叉检查原requested intent/record hash、subject/issuer/authority/ref。**本切没有authorized行writer**；真实T5 issuer尚不存在。正向测试用隔离DB的明确fixture-only authorized行验证解析，绝不冒充生产授权链证明。只有requested行时一定not_found。

A7的ack工具和状态转换、A8的实际Memory回签与唯一scheduler不是本切实现完成项；schema字段存在不构成全S5c完成。

## G6 更新：SDK priority 明确阻塞

用户补充的代码约束纳入本接口边界，不变更当前SDK或pin，也不混入主线程为S3冻结的Memory 0.6.5 source/candidate。当前运行测试安装的是Memory0.6.3；这不是建议降级主候选。

- Memory jobs v1 payload严格为 `schema_version/evidence_id/envelope_hash/source_hash`，ordinary历史字节必须保持；**禁止添加analysis_schedule进现有JSON**。当前Host代码没有priority实现或能工作的placeholder调用。
- 后续优先评审独立 `schedule_class`/schedule provenance列或独立版本化schema，而非只新增kwargs；升级/新状态必须让旧0.6.3稳定拒绝，open/integrity/recovery验证同步，不从版本字符串推断安全。
- 同evidence/schedule replay幂等；不同schedule应在现有ingest early-return之前验证并稳定拒绝冲突，不能replay吞掉调度变化。
- ordinary/immediate批次分离，eligible immediate免普通max-wait，但先reclaim既有batch，且不抢占同principal handed_off/result_committed fence；保留原fixed plan/base_revision/result、不得额外调用LLM掩盖幂等。
- 此设计仍是待主协调的SDK contract差额，不声称仅Host改动可满足AC-5。T5 priority与T6 suppression/旧snapshot披露屏障继续BLOCKED。

## 可审阅状态

本切代码/测试已落在隔离分支，实际验证见 [journal.md](journal.md)。production composition、真实provider/UI、T3–T8、S5c整体gate均未完成。当前会话无独立子代理/code-review工具，未用执行者自检冒称独立审查；按主协调交独立review后再决定合入。
