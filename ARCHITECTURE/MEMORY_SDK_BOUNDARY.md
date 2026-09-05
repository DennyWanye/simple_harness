# Memory SDK 边界与 Host 接口契约

> 最后更新：2026-09-05

## 2026-09-05 主对话隔离组合验证

运行层、API 与新前端已组合；真实 SDK + SQLite + deterministic Provider 经公共历史 API
验证新普通对话/旧 scoped 终态、重启标识一致与 raw event 错绑拒绝，聚焦 **51 passed**。
新原生 App 构建成功，真实 UI/provider 尚待执行；完整来源遗忘与新建项目授权仍在修复，
因此未切换 main、未标 S6/program 完成。命令与本地证据哈希见
[INTEGRATION](../plans/2026-09-05-s6-primary-preparation/INTEGRATION.md)。
> 验收基线：simple_harness `4e797ccd`；Harness `fbb156f` / 0.3.0 / wheel `cf629cee…`；
> Memory `3d4247b` / 0.4.0 / wheel `bfcd2506…`
> 发布标记：Harness `v0.3.0` → `fbb156f`；Memory `v0.4.0` → `3d4247b`；主分支与 tags 已推送；
> 本地冻结 wheel/sdist 已正式发布到对应 GitHub Release，并通过公开稳定 URL 下载回验

本文档是 simple_harness 的 Memory 生产边界事实源。2026-08-22 的官方一等集成已完成代码、自动化门禁
与真实 macOS Computer Use UI 验收；SH-M1～SH-M6、SH-SURFACE 均已在真实 DeepSeek provider 下通过。

## 2026-09-05 Primary API P1 后继修复

`fbc026a0` 的40项局部绿色未覆盖实际 Host authority/raw SDK hash 差异，独立组合 probe
已复现真实完成历史拒读。后继 API 改用 Carver 唯一 terminal_identity helper 验来源链，
再精确核对真实 SDK event；还修正 active source 抑制、slow reader 后整页 source 复查、
SQLite 已 commit 后 wake 失败仍返回 durable ACK（0.5秒通知预算）。没有新增权限或 ledger。
公开 suppression 没有 batch/snapshot/epoch，最终逐来源复查不等于原子隐私快照，
memory/entity lineage 扩展仍后续。该提交必须与 Carver helper 组合；真实 runtime API
集成测试由主维护并待其运行，不能把局部测试算产品闭环。
聚焦 source overlay 验证45 passed（29 API+16既有），尚待主组合测试与独立复核。
详见 [PRIMARY-API](../plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md)。

## 2026-09-05 Primary API 隔离切片

`feat/human-memory-primary-api`（base `29902ea4`）实现 `primary.state`、
`primary.messages.page/detail` 与 exact targeted `queue.control`。读取真实 Host
primary/turn/source/binding/terminal receipts，并通过注入的 SDK 公开 transcript/binding reader
验 run/event/hash/user anchor；queued user 可读，公开长文本可分块完整取回。
revision 读取既有 append-only 表尾及当前 head，page 最多扫描10 turns，queued policy
最多100项，超限计数明确为下界。无新 schema/计数 ledger/Session。

过滤结论仅覆盖当前 subject、Host 输入及终态观察 evidence；Memory resolver 不展开
memory/entity lineage，不能声称所有 memory-derived 历史的隐私已闭合。缺 policy/reader
拒绝对应读取。Carver 已在其隔离树注入三个 factory kwargs，组合树运行与 UI 验证尚待主协调；
本分支不改 main/runtime/fence/SDK/pin，不代表 S6/program 完成。

聚焦验证：新增24 + 既有16 = **40 passed**（公共 API/真实 Host SQLite 与已安装 Memory
suppression backend；SDK transcript/binding 为注入 fixtures），未跑 provider/UI/全量。
契约、命令与 ignored 证据索引见 [PRIMARY-API](../plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md)。

## 2026-09-05 S6隔离分支：control复用与无scope admission

`feat/human-memory-s6-primary-preparation`（base `4eb1eb7c`）已实现P1：沿现
`/ws/control` 的signed profile_bind验证本连接owner/epoch/active lease；HUMAN读写
不再只凭全局ready或shared secret，保持原local subject、primary-ID fence及exact effect授权。
没有新增账户、socket、Rust/TS scope或每次读签名。

P2 runtime本树已通过真实SQLite/Harness + deterministic Provider测试：无scope实际完成、
重开/终态事务故障恢复、真实消息投影、unscoped队首后scoped FIFO，以及None→生产
context_route.resume_existing→工具发现/激活→生产write_file→semantic closure/terminal。
项目effect从真实route/binding验证exact root，保留scoped冻结检查；binding head更新负例不落盘。
统一terminal_identity先验Host receipt/binding/observation或ExecutionEvidence链，再比raw
SDK event ID/hash/state；旧式scoped fixture不删除原始证据。helper用于runtime/API共享解释。
轻量state_changed接现control broadcast空payload，独立合并限时500ms；control请求事务前
重验原verified connection/epoch/lease，保留原撤销barrier。
2026-09-05聚焦181 passed（含动态正负、SDK身份替换拒绝、crash/reopen及通知），无App/真实Provider。
main已惰性注入API三reader/resolver，**依赖Dirac API提交及terminal helper接入后继进行组合验证**。
**完整历史Memory/evidence/entity来源suppression留下一提交，尚未闭合**；当前候选不得据此
合main或宣称S6 Task1/2完成。create_new Manual路径也未有新UI验收，不走旧目录卡/external wait。

CREATE_NEW候选62f44631曾发现generation origin P1，后继冻结原Host/SDK Run、owner/gen，
首次选择与写锁内commit前重复核验；真实reclaim/终态后旧context回归及相关套件85 passed。
origin c04912f9已获Dirac独立限定ACCEPT（9 passed）。首tool启动竞态后继用本次启动Event
等待真实Host RUNNING持久化，再按原owner/gen/state授权；不放宽CLAIMED，5秒有界失败。
无Provider等待的真实首tool正例/期间reclaim拒绝及相关套件87 passed；待独立复核及新项目native验收。

CREATE_NEW后继已完成生产backend修复：配置CanonicalWorkspaceRoot取canonical_path后使用稳定task子目录；
service先持久化真实binding proposal；active None Run以实际Run/context+owned目标scope取得原AUTO authority，
不走无Run bootstrap，不改变admission scope。Manual返回可消费真challenge，旧scoped不可跨scope。
相关回归66 passed（含5条新增、真实AUTO落盘/terminal/重开、lease丢失及伪造evidence负例）。
Manual UI/失败结果投影仍未接通，完整Memory-forget history仍未闭合。详见
[CREATE_NEW交付](../plans/2026-09-05-s6-primary-preparation/CREATE-NEW-BINDING.md)。

本记录仅为隔离分支状态，未合main；不表示S6 Task1/2或program验收完成。

实现/命令/原始证据hash与交叉点见
[实施交接](../plans/2026-09-05-s6-primary-preparation/IMPLEMENTATION.md)。

## 2026-09-05 Harness 0.7.2 接入与当前验证

Host `8d57441517836aaaa30ac16a33576f4d68a9d1ad` 已安装 Harness 0.7.2，source
`2b8428465cbd41032ba024a0b7199183161f5ecd`，wheel SHA-256
`53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed`；Memory 保持
0.6.3 / `6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78`。
版本、manifest 和安装身份核验通过；SDK 独立复核 151 个包文件与源码/安装字节一致，
110 个已导入模块均来自 Host venv，安装版定向回归 17 passed。

A14 两个独立真实 gpt-5.5 / queue.enqueue root 均完成 README 1.1.3→1.2.0、TaskScope
语义收口、终态 outbox 与 Memory accepted plan，每个 root 物化 episode+semantic 两个 head。
root 为 `2fa7d7b1-3430-5052-bc10-5cfb77beb32a`、`870babc5-3842-56d7-bae5-05d1477119c0`；
前台调用分别 13/12 次，analysis 各 1 次，无重发。旧 0.7.1 失败证据保留。
独立 AI 质量复核确认原句/spans/实际效果/绑定一致；发现 episode 误用 analysis 时间的 P2 已改用首次 Host committed_at，26 条聚焦回归通过、主执行者复审接受，真实生产复验待执行；
另一个补读 before 来源 P2 已用衍生纠正包闭合并独立接受，原封口 834 文件未改。
不能把两条机械通过当成 S5b 完成，也不将物化计作 typed recall 命中（A15 仍 NOT_MEASURED）。

当前 native UI 的隔离源 backend / gpt-5.5 root `a48a396c440054e299af4094b273f2db`
已收到真实非空回复；只覆盖 chat 冷启动，S6 唯一主对话/queue UI 仍未交付。
Host 全量在上述 HEAD 得到 6535 passed / 6 failed / 47 skipped / 6 deselected；
一个新增失败为 exact candidate 测试的旧 hash 字面量，更新当前 hash 并保留旧 hash 负例后整文件 20 passed。
其余五个失败属于历史七节点子集，其中 downgrade 原因变化仍在独立核验，不能直接豁免；
另外两历史节点已通过。6 deselected 全来自默认 marker，命令的显式 deselect 未匹配；没有测试挂起。
本次还完成 S2 聚焦 49 passed、S3/S4 聚焦 96 passed、S7 聚焦 16 passed；各日志明确测试层级。
完整回归退出码与原失败保留，尚未得到 machine finalize PASS。

本机证据根 `.local-test-evidence/2026-09-05/human-memory-resume/`：
`tools/a14-20260905T093324-p0e5cu3m/`、`independent-review/a14-quality-093324/`、
`tools/derived-correction-recollection-before-20260905T095723/`、
`independent-review/a14-q2-correction/`、`reg-full-host/REPORT.md`、
`host-final-candidate-pin-retest.log`、`tauri-app-harness072-resolved.log`。
原始文件及 SHA 索引均 ignored；未 push/tag/发布。S3 另在隔离分支补公共执行桥，S5c/S6 未交付。

## 2026-09-05 早期 S5b 恢复修复与候选记录

当前 Host 使用 Harness 0.7.1 / Memory 0.6.3 / Service 0.3.12；上方 0.4.0 发布与下方
早期 S4 状态是历史验收记录。Memory source `2f3d73814fe6a884e0458d87567b918c5863033e`，
wheel SHA-256 `6b20ae5bff6c3ecfe1108ccaff9bb41c4dc6a3b98bb754dac2c418673ab77c78`。
双次构建字节一致，版本/来源/hash 校验通过；未 push/tag/发布。

Memory 按 principal 等待已领取但未物化的 analysis batch；恢复沿用固定结果、plan、base_revision
与 evidence，不通过改写 revision 或追加 Provider 调用挽救旧结果。合法 no_mutation 的可选
closure_reason 被原样持久与恢复，仍零认知写入；不可用响应继续 rejected。两项修复独立复审接受。
公共 API 和 schema v7.1 保持原契约，旧 0.6.2 wheel 保留。

当前精确 wheel 的 Host 集成 51 passed、Memory 恢复/API 16 passed；原始证据在
`.local-test-evidence/2026-09-05/human-memory-resume/`。新 wheel 原生 UI/gpt-5.5 root
`c2af5326a8d05023868f7994f1a4e0be` 已取得非空回复；r5 保留早期失败，S8 FLAKY。
A14 真实 queue.enqueue root `142bdb3b-9026-5264-b244-69e94bf0e388` 写 README 后被 Harness 0.7.1
initial/current route 恢复 P1 阻断：terminal FAILED、closure pending、accepted/head=0。
用户已批准 A17 限定 SDK route 修复和必需 port/回归/新候选接入，其余 SDK 功能继续冻结；
本次只记录事实与证据，不改 Memory plan/base_revision/evidence，不改产品源码/pin。
原始失败与 metadata 位于上述根的 `verification/r5-local/artifacts/s1-route-failure/`；
S5b 机器门及 program 未完成，A15 typed recall 仍 NOT_MEASURED，交 S5c/S6。

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
