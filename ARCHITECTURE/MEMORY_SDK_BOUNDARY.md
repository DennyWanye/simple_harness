# Memory SDK 边界与 Host 接口契约


## 2026-09-06 source10完整oracle后继（独审待回）

固定65990a68，exact installed H073/M0613f2 source层正式10PASS/0FAIL/0BLOCKED。
完整90表schema/PK/nonfinal根、request/attempt/terminal关系、原distinct admitted source与member/group hash、
确切reopen outer/cause/trace及零recall/零写均独立判定；没有改SDK错误码、fixture、10AC/阈值。
1个集成test含10正控+30篡改检查通过；schema2/extra-key、swap/reuse重hash命中目标reason。
首轮three-member错误cause导致1红，已保留并定向修正为实际FKcause；不改原证据。
本次未选391public，不与626/fbeb旧public计为新401全量，不称program/quality/native完成。
全部默认OS锁2GiB/180s，最大157920KiB，无残留且slot已释放。
[命令、红绿与原始hash](../plans/2026-09-06-typed-recall-source-oracle/RESULTS.md)。


## 2026-09-06 固定626后续正式分批（整体仍BLOCKED）

H073/M0613、runner626ff8d8的11个fresh bounded调用互斥覆盖原391public+10source；
public182PASS/0FAIL/209BLOCKED，source0PASS/0FAIL/10BLOCKED。
本次分批并集182/0/219，非一个full401 Run、非质量/机器gate；不拼旧2格observe或旧178历史。
355public+10source实际OBSERVED，36executor未实现；BLOCKED原因为122fixture/setup、61oracle、36executor。
source使用exact clean M0613f2；真实fault/corruption仅source证据，完整oracle仍缺。
全部默认OS共享锁、2GiB/180s/批，最大147904KiB，所有进程组无残留且槽释放。
[逐批Run、命令与逐格分类索引](../plans/2026-09-06-typed-recall-0613/FORMAL-BATCHES.md)。


## 2026-09-06 H073/M0613 runner successor（独立测试工具叶子）

独立 `feat/typed-recall-0613`，base60f280dc；候选pins显式后继并保留旧lineage，
原401/391+10/14攻击/阈值不变。observe在public/source层及cell统一不授PASS，FAIL保留。
必要工具测试12passed；两原格真实installed public OBSERVED且业务断言通过，正式0PASS/0FAIL/2BLOCKED。
source10与其余399未执行，旧178/0/223历史不覆写、不拼接。H164/M72包文件逐字节核对；无模型/native。
资源入口145baed3默认共享锁，两组无残留且槽已释放。原runner applicability WIP未动，原三格仍BLOCKED。
该工具叶子不表示S3/program或401全量完成，未合主树。
[命令、资源与证据hash](../plans/2026-09-06-typed-recall-0613/RESULTS.md)。

> 最后更新：2026-09-05
> 验收基线：simple_harness `4e797ccd`；Harness `fbb156f` / 0.3.0 / wheel `cf629cee…`；
> Memory `3d4247b` / 0.4.0 / wheel `bfcd2506…`
> 发布标记：Harness `v0.3.0` → `fbb156f`；Memory `v0.4.0` → `3d4247b`；主分支与 tags 已推送；
> 本地冻结 wheel/sdist 已正式发布到对应 GitHub Release，并通过公开稳定 URL 下载回验

本文档是 simple_harness 的 Memory 生产边界事实源。2026-08-22 的官方一等集成已完成代码、自动化门禁
与真实 macOS Computer Use UI 验收；SH-M1～SH-M6、SH-SURFACE 均已在真实 DeepSeek provider 下通过。

## 2026-09-05 Typed recall 执行桥验证工具（仅独立分支）

- `feature/human-memory-typed-recall-runner`：已批准§3–4修订，§3既有JSON-domain、§4 state NUL；独立向量先于执行。401 IDs/391+10/14攻击/阈值不变；fixture rev7/layers rev5。
- 代码 `1e72f2ff`：**76个桥回归通过**；Harness0.7.2 + Memory0.6.5 clean两层消费者 **178 PASS / 0 FAIL / 223 BLOCKED**，public349+source10真实OBSERVED，42 public未实现，0 source正式PASS。14攻击见证、typed/UNKNOWN setup及14合法lifecycle历史已接通；6个short/mixed公开路径仍为完整oracle待闭合的OBSERVED。
- 独立复审接受拒绝基线P1/跨principal P2，以及lifecycle中间payload/action grant两项P2修复。旧 provisional PASS不倒填，当前来自加强控制后重跑；执行代码hash与提交Git blob逐字节一致。
- 本机索引 `.local-test-evidence/2026-09-05/typed-recall-clock065-r5/bridge-summary.json`，SHA256 `255e40bb644ecbd32b4987eb892d67318348bd7bc6331fd40f148e2f6aabc2c0`。命令、身份、setup与oracle剩余项见 [本批记录](../testcase/human-memory-program/runners/TYPED-RECALL-NORMAL-BATCH-2026-09-05.md)。
- 2026-09-05 增量 leaf 小批：12 public真实OBSERVED，**7 PASS / 0 FAIL / 5 BLOCKED**；其余389本轮未运行，不能与历史178相加。合法USER+TOOL双span已接通；leaf本机10测试通过。4个剩余适用性/信号setup、原128byte反例保留；无新产品缺陷结论。[小批命令与证据](../testcase/human-memory-program/runners/TYPED-RECALL-PUBLIC-LOOP-BATCH.md)。
- 2026-09-05 current-use增量：6 public真实OBSERVED，**0 PASS/0 FAIL/6 BLOCKED**；记住→纠正→忘记→reopen及新attempt拒旧result业务断言通过加强后的oracle，两个桥P2已修并独立复审ACCEPT。完整原epoch/continuation门仍未闭合；与leaf合计18个本批distinct cells为7/0/11，不替代完整401历史结果。新增[历史可见性只读设计](../testcase/human-memory-program/runners/TYPED-RECALL-HISTORY-VISIBILITY-GAP.md)，未实现SDK入口。
- **仅独立分支工具事实；未并入主树，合并暂缓，S3/program未完成。** 主共享venv仍Memory0.6.3；无provider/UI/MPS或SDK全量测试。128byte、原AUDIT/epistemic不可构造组合及short时间/hash差异保持BLOCKED；未将setup缺失报告成产品缺陷。

## 2026-09-01 Human Memory Program Host evidence、Canonical Archive、Task Home 与 Binding（S4 Task 1–4）

- Host 已新增 opt-in `human-memory-v1` 基础，并以不改写 v35–v37 checksum 的追加 migration 升至 state schema
  v38。该 epoch 只能从空数据库取得 durable
  bootstrap marker 后初始化；普通 state.db 启动仍停在 v34，不会因为新 migration 文件存在而升级。任何既有
  v34/更旧数据库从新的 primary 入口打开时，都在 backup、reset、migration 和业务写入之前稳定拒绝；不迁移、
  不删除，也不展示旧 Session。
- v35 对每个 authenticated subject 以 partial unique constraint 保证唯一 writable
  `primary_conversation_id`，并在同一事务写 immutable init receipt。并发冷初始化、v35 commit 前/后故障和重启
  均收敛到同一个 primary identity。
- 新 `HumanMemoryProgramStore` 只接受具备 S1 `SanitizedEvidenceEnvelope` / `SanitizedEvidenceReceipt`
  冻结结构、独立 canonical hash 校验和 receipt binding 的输入；receipt 先写，随后在同一事务 append
  user/assistant/tool/provider/run evidence。Provider payload 使用 public allowlist；认证字段、credential value
  canary、隐藏 reasoning 和私有扩展在持久化前 fail closed。raw evidence、sanitization receipt、primary identity、init/format marker 均有
  SQLite `BEFORE UPDATE/DELETE` 拒绝 trigger，纠正与遗忘必须由后续 append-only lineage 表达。
- v36 新增 Canonical TaskScope Archive：`task_scopes` identity、Host-native turn/file/test event、LLM mutation
  attempt/decision、step fact、evidence link、canonical revision、immutable checkpoint 及 projection/search outbox
  均在 Host state.db 留下永久、可重放记录。LLM `TaskScopeMutationPlan` 先做冻结结构、对象/JSON、canonical hash、
  disclosure、evidence lineage 和 credential/private-field 校验，再以 `base_revision` CAS 在单事务写 mutation
  decision/event/canonical revision/projection/search outbox；CAS 冲突只追加 attempt 审计，不产生新 revision。
- S1 `ExecutionEvidence` ingress 以 `source_event_id + evidence_hash` 幂等，持久保存 receipt、run cursor 与连续
  durable watermark；乱序 terminal 不可跨过缺失 sequence，只有 durable watermark 到达 terminal source
  sequence 才能生成 immutable terminal gate receipt。这里仅是 Host ingress/gate seam，尚未接入正式 foreground
  composition 或真实 Provider 链。
- TaskScope raw event/evidence link/decision/attempt/canonical revision/checkpoint/outbox 禁止 physical UPDATE/DELETE；
  head、watermark 与 projection cache 是可重算协调状态。projection cache 可删除，并已验证能从 canonical
  revision 逐字节等价重建；这不是 README/STATUS 等用户阅读视图，也未实现 search consumer。
- v37 新增 `reserved → filesystem_ready → committed | failed_retryable` 的 TaskScope task-home provisioning。
  managed 模式只在 configured managed workspace root 的真实子目录创建 `<safe-title>--<scope-digest>`；macOS/Linux
  未配置时使用 `~/SimpleHarnessWorkSpace`。explicit 模式只接受 trusted user selection/project picker 且目标必须
  是已存在、非 symlink 的 exact directory；项目允许管理元数据时 task home 位于
  `.simple-harness/task-scopes/<id>`，否则落 private app-data。稳定 staging+marker+filesystem identity 让每个故障
  边界重启都收敛同一路径，不以第二目录掩盖失败。
- POSIX materialization 持久冻结独立 `materialization_root` identity，并以 `O_DIRECTORY|O_NOFOLLOW` directory fd
  逐级创建/打开父目录；staging marker 与 no-replace publish 全部锚定该 fd。Darwin 使用
  `renameatx_np(RENAME_EXCL)`，Linux 使用 `renameat2(RENAME_NOREPLACE)`；缺少等价原语的平台稳定 fail-closed。
  broken symlink、resolve 后 root rename/replace、receipt commit/reopen 前 root 或 task-home identity/containment 漂移
  均不得生成 committed receipt。
- Provision receipt 只有 final committed 才存在；reserved/filesystem_ready/failed 都不形成 workspace authority。
  `proposed_workspace_root` 仍只是候选。v38 已新增 append-only binding proposal/challenge/decision/grant/set
  revisions：Manual 必须重载 durable authenticated user evidence/interaction，Auto 必须重载 Host-issued current-Run
  snapshot 并在 append/commit 前复核 active Run、context/config revision、时窗与 configured-root filesystem
  identity。task home 与 binding receipts 均不可物理改写。
- 当前 Host Human Memory candidate 精确固定 Harness SDK 0.7.0；Task 1–4 以 strict public DTO、真实 source DTO
  interoperability 与 durable authority restart/fault probes 验证，**不**把 fake DTO 当成生产集成。六阅读视图、
  candidate search/exact open、foreground FIFO 与 S4 Host integration/recovery 尚未实现；S5 才接 main-model
  route/recall/context/tool composition，S6 才切 UI。因此当前能力不作为产品成功声明或默认新入口。
- 决定性回归：提交态新增/迁移/既有 Session 组合 `67 passed`，SDK adapters `253 passed`。受影响后端
  m–r 分片先跑 `1497 passed`，提交态复跑为 `1496 passed, 26 skipped, 1 deselected, 1 failed`；唯一失败是
  已登记的环境型 `test_process_list_with_query` process-name filter 基线红，与本 slice 无调用/文件依赖。真实 S1
  DTO source interop probe PASS；没有 UI 或真实 Provider evidence 声明。原始本地 probe 数据仅保存在 ignored
  `.local-test-evidence/`。
- Task 2 新增 archive/ingress 专项 `8 passed`；与 Task 1 memory/session/SessionDB 组合 `75 passed`，SDK
  adapters `253 passed`，真实 S1 source `ExecutionEvidence` + `TaskScopeMutationPlan` DTO interoperability probe
  PASS。上述均为自动化/源码协议证据，没有 UI、真实 Provider 或 production composition 声明。
- Task 3 provisioning 专项 `18 passed`，Task 1–3 相关组合 `93 passed`，SDK adapters `253 passed`；覆盖全部七个
  operational fault 边界、v37 migration commit 前/后、nonexistent/symlink/untrusted explicit path、permission
  failure/retry、duplicate title、idempotency conflict、project/app-data metadata、broken-link escape、managed/explicit
  root replacement TOCTOU 和 no-binding/no-partial-authority。
- Task 4 binding 专项 `15 passed`；Task 1–4/marker/candidate 与 SDK adapter 组合 `322 passed`。当前明确未实现：
  `task_scope/projections.py`（六 bounded views/checkpoint verifier）、`task_scope/search.py`（permission-first
  candidate search/exact open）、`execution/foreground_queue.py`（单 foreground Run/FIFO）、
  `execution/recovery_fence.py` 及 `backend/main.py` 的 fresh Host service/旧入口 fence/emergency export 接线。

## 1. 当前生产链路

```text
Tauri/React chat/chat_v2
  -> validated local HumanIdentity.identity_namespace_hash
  -> immutable deployment/household/actor/session binding
  -> root 或 continuation 独立 immutable Context source snapshot
  -> Harness ConversationTurnInput / ConversationContinuationInput
  -> SDK durable context claim
  -> SDK 调 read-only product Context provider + MemoryManager recall
  -> frozen Context stage（Memory 始终按 untrusted data）
  -> Provider / Tool / recovery 复用同一 stage
  -> completed terminal committed-turn outbox
  -> MemoryManager.record_committed_turn
```

产品不再调用 `prepare_consumer_conversation_context`，也不再构造 `ConversationMemoryAdapter`、manual recall
query 或 query/sink 双口。Harness 的正式 `AgentMemoryPort` 与 `ConversationContextProviderPort` 是前台唯一
自动 Context/Memory 组合。

## 2. 资源与身份 ownership

| 事实/资源 | owner | 当前边界 |
|---|---|---|
| Session/UI message、delivery、Provider usage、非 Harness outbox | `state.db` / SessionDB | 产品投影事实 |
| Run、Context stage、Provider invocation、committed-turn outbox | Harness execution v4 DB | SDK 执行事实 |
| Messages/Facts/Twin/recall snapshot/write fence | Memory SDK v4 DB | 长期 Memory 事实 |
| Persona/历史/Skill/附件/project/task source | simple_harness content-addressed repository | provider 只读；同 ref 同 bytes |
| MemoryManager 生命周期 | simple_harness process | production builder 构造一次；Runtime `BORROWED`；shutdown 先关 runtime borrowers，再由 SessionDB 有界 drain/关闭 manager 一次 |

身份只来自 `LocalAuthSnapshotProvider.current_snapshot()` 经
`validate_auth_snapshot(..., user_data_dir=...)` 得到的 `HumanIdentity.identity_namespace_hash`。
`deployment_id` 来自 `state_db_identity.instance_id`；首次 actor 获得随机稳定 household；同 session 不可换绑。
模型、payload、Provider 配置、API key 与 legacy `profile_id` 均不能提供或覆盖 actor；身份损坏在 LLM 前
fail closed。

## 3. Context source durability

- root 与每个 continuation 各自生成 content-addressed immutable source ref；continuation 不继承 root ref。
- ingress 原子创建带 lease 的 `PENDING` binding；SDK durable accept 后标 `CLAIMED`；stage/terminal 后进入
  `STAGED` / `CONSUMED`。
- terminal 释放 root 与全部 continuation refcount；共享 hash 不会被单个 binding 误删。
- orphan cleanup 只处理超过 horizon/lease 且 execution claim-inspector 证明无引用的记录；inspector 故障
  保留重试。
- provider 只读 source snapshot，校验 canonical hash、item/byte bounds，不写产品数据库。

## 4. 写入 authority 与工具面

- Harness foreground message 使用 execution committed-turn outbox；`FAILED` / `CANCELLED` 不生成长期 Turn。
- Companion/background/非 Harness message 保留 `product_memory_outbox`，经同一个 MemoryManager 的 explicit
  projection 写入。`memory_authority=harness|product|none` 保证同一消息不进两套 authority。
- ordinary foreground catalog 不再暴露可触发第二次 live recall 的 `memory_recall` / `memory_search`。
- simple_harness 现有显式 remember/read/forget 工具从 resolver 的完整 deployment/household/actor/session
  构造可信 `MemoryPrincipal`；write 调正式 `remember_fact` 并保留 salience/pinned/tier，返回准确 fact ID；
  read 调 `read_fact`，不再按 legacy user 扫描 facts。独立 event key 的同 payload 重试保持同 ID，元数据变化
  conflict，跨 principal 不可读/不可重放。forget 显式传 `source_event_id`，由 SDK canonicalize payload hash；
  首次 action 与重放返回同一结果，后续独立 action 对已删除 fact 稳定返回 false，receipt 跨重启保持且不复活；
  自然语言遗忘仍按安全例外关闭。
- `MemoryManager.share_fact(principal, fact_id)` 是 Memory SDK 正式授权分享接口；本轮不为 simple_harness
  新增 `memory_share` Tool/UI，供后续 K6/AgentOS、NovelTagSystem、AI Phone 消费。

## 5. 恢复、迁移与 DEV fault

- 产品 v4 coordinator 只调用两 SDK 的公开 migrator；先备份两库并写 owner-only journal，任一步失败恢复
  all-old pair，完整 hash 验证后才保留 all-new pair。
- recall timeout 按 SDK policy 降级为空 frozen stage；record transient 不回滚成功响应，由 durable outbox
  重试收敛。
- fault wrapper 仅在 `DESKPET_DEV_MODE=1` 且 user-data 位于仓库 `.local-test-evidence` 时允许装配；其他路径
  fail closed。

## 6. 当前验证状态

- exact wheel SHA/direct-url installed-origin 与 candidate conformance：PASS。
- Harness full：`1379 passed, 2 skipped`；Memory 默认 full：`200 passed, 7 skipped`，正式 candidate gate：
  `205 passed, 2 skipped`。
- 产品最终聚焦：backend `83 passed`；MemoryPanel `18 passed`；TypeScript typecheck PASS。
- 产品 full baseline：15 shards PASS、2 个实施前 known-red（root live fixture、ESLint 171 fingerprint），0 unexpected。
- SH-I01：同 user-data 重启稳定、Provider/API key/model/payload spoof 不影响、跨 user-data 隔离、损坏身份/
  错误 snapshot 在 LLM 前拒绝、legacy profile 排除：PASS。
- 真实 UI：SH-M1～SH-M6、SH-SURFACE 全 PASS。SH-M5 按冻结 exact oracle 跨进程新 Session 召回
  `Max`；`Aurora-R4` 只属于早期隔离 canary。SH-M6 以进程环境 attestation 直接证明 recall timeout
  fixture 已启用且主 Turn 不受阻断；record transient 在未写入时退出后由 startup recovery 唯一收敛，
  新 Session 回答“晚饭后”。SH-SURFACE 已在当前构建真实打开 macOS 附件选择器并以 Esc 安全取消。
- r7 独立审计因附件截图错配、recall fault 缺直接 attestation、S6-A8 状态文字与导入 custody 不完整而
  判 FAIL；这些证据/文档缺口已在继任 Gate 输入前修复，r7 不作为发布 receipt。原始截图、日志、进程
  attestation 和 Gate ledger 仅在 ignored `.local-test-evidence/2026-08-22/`，Git 只保存结论与 hash 索引。
