<!-- plan-status: finalized -->

# Plan：聊天与设置共享的 Project-scoped Skill 安装

## 主要矛盾

- 决定成败的核心问题：把 raw GitHub Skill repo 转成 Manager-owned immutable
  Capability Pack，并将“下载、审查、确认、原子发布、Project binding、新 Run 可用性”
  收敛为一个权威事务，不再让聊天模型用 shell 猜目录。
- 这是权威与身份合约问题，不是提示词问题：`capability_install` 已存在但没进 SDK
  product catalog；现有 Project capability key 又只用 workspace path，未绑定冻结
  `project_id/revision/identity`。

## 关联验收标准

- 全量覆盖 `AC-SI-1` ～ `AC-SI-6`，以
  [`acceptance.md`](acceptance.md) 和 [`assurance-contract.json`](assurance-contract.json) 为冻结边界。
- `BEHAVIOR_POLICY=preserve-approved`：保留受支持的 GitHub URL 安装能力，退役 legacy
  直接目录 publish 和 multi-repo 无确认 partial finalize。

## 最佳实践与项目适配

- 先把可变 ref 解析为 exact commit，再确认和发布。GitHub 官方 commit API
  可以用 ref 取得 exact SHA，zipball API 可按该 SHA 下载 archive。本项目对 metadata
  响应和 archive stream 分别封顶，不用无硬字节上限的 Git clone 作不可信运输。
  参考：https://docs.github.com/en/rest/commits/commits 与
  https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip
- 文件集要有确定顺序和完整性边界。GitHub tree API 以 tree SHA 枚举 blob，且明确
  recursive 结果可能 truncated；本项目不用 recursive tree API 作完整性边界，而是对受限
  archive 的本地 staged tree 做排序相对路径 + file SHA-256。
  参考：https://docs.github.com/en/rest/git/trees
- 确认必须绑定实际将发布的对象，不在确认后重新解析 branch HEAD。项目已有
  Manager idempotency/publish-intent/CAS，因此不新建第二套事务日志；application service
  只持久化 staged intent 及确认 receipt，最终 publish 交给 Manager。

## 典型入口链（通用模式）

```text
chat capability_install_skill / Settings Skill Store
  -> ProjectSkillInstallService.stage(url, trusted_project)
  -> BoundedGitHubSkillSource exact-commit archive stream
  -> RawSkillPackCanonicalizer discover/validate/sort/hash
  -> durable staged intent (project identity + expiry + list digest)
  -> one HITL confirmation (skill_install)
  -> ProjectSkillInstallService.confirm(intent_id, digest)
  -> CapabilityPackManager.install(... scope=project, identity-aware scope_key)
  -> Manager receipt + Hub snapshot refresh
  -> next Run resolves exact manifest/content hash and pages Skill body
```

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `backend/deskpet/tools/capabilities.py` | trusted Tool context | 加入冻结 Project id/revision/identity，保持不从模型参数取值 |
| `backend/deskpet/harness/context.py` | Host context factory | 从 Run workspace snapshot 投影完整 Project identity |
| `backend/deskpet/sdk_adapters/tool_authority.py` / `tool_catalog/providers.py` | SDK 调用上下文 | 传递与复验 Project identity |
| `backend/deskpet/capabilities/contracts.py` | Capability scope contract | 新增 versioned identity-aware project key，保留旧 path key 为非权威兼容 |
| `backend/deskpet/capabilities/source.py` | staging-only source | 保留 canonical pack source；新增独立 bounded GitHub archive transport，不改宽 `GitPackSource` 合约 |
| `backend/deskpet/capabilities/skill_install.py`（新） | 唯一 application service | raw Skill canonicalization 与单一 install intent lifecycle |
| `backend/deskpet/capabilities/manager.py` / `store.py` | 原子 lifecycle/store | 复用 install/publish/bind；增加批次原子性所需的 staged pack/intent 证据 |
| `backend/deskpet/capabilities/tools.py` | model-visible adapter | 新增一个薄 `skill_install`，confirm 只是 Host continuation，严禁 shell bypass |
| `backend/deskpet/sdk_adapters/tools.py` | SDK product catalog | 投影新 typed tools，保持 `skill_install` 权限/HITL |
| `backend/main.py` | composition + WebSocket adapters | 注入唯一 service；legacy URL/confirm 改为薄 adapter，multi 不再自动 finalize |
| `tauri-app/src/components/SkillStorePanel.tsx` | Settings UI | 展示 batch manifest/digest/project scope，一次确认后接收结构化 receipt |
| `backend/tests/capabilities/*` | lifecycle tests | canonicalization/scope/atomicity/recovery/idempotency/adversarial tests |
| `backend/tests/test_sdk_*` / `tauri-app/src/**/*.test.tsx` | catalog/UI regression | tool discovery/HITL/schema/UI 一致性 |
| `ARCHITECTURE/*` / `PROJECT_STATUS.md` | 生产事实 | 验收绿后回写新链路、证据与剩余 gate |

## Complexity inventory

| 复杂度表面 | 是否新增 | 理由 / AC 或 risk 绑定 |
|---|:---:|---|
| 新依赖 | 否 | 复用 httpx/archive validator/Manager/SQLite |
| 新公共 API | 是 | typed stage/confirm，AC-SI-1/3/4 |
| 新持久化状态 | 是 | staged intent/confirmation receipt，为冷重启、过期、幂等与崩溃恢复，AC-SI-3/4 |
| 新配置项 | 否 | TTL 使用内部常量，不增加用户可见调节面 |
| 新抽象层 | 是 | `ProjectSkillInstallService` 是 chat/Settings 共享 owner，AC-SI-1/2/6 |
| 新后台任务 | 否 | stage/confirm 是有界请求，无 watcher |
| 可复用已有实现 | 是 | archive materializer/limits、`CapabilityPackageValidator`、`CapabilityPackManager`、`CapabilityHub` |
| 标准库/平台能力 | 是 | SHA-256、SQLite transaction、OS keychain 既有边界 |

## Assurance / 信任与失败边界

- Profile：`standard`；contract 为 `assurance-contract.json` 中 `AS-SI-*`。
- 信任边界：URL/ref/repo 内容全部不可信；Project identity、Run/effect identity、permission decision、
  Manager receipt 只从 Host trusted context 取值。
- 持久化：intent 记录 normalized URL、resolved commit、sorted file-set digest、skill list digest、
  Project identity/revision、expiry、status；不记录 token/credential。deny/expiry 清理 exact staging tree。
- 失败/对手：非 HTTPS GitHub、redirect 越界、path traversal、symlink/submodule、超限、
  manifest/schema/tool 不兼容、ref 漂移、digest 篡改、重放、过期、跨 Project 确认、
  publish 中崩溃、部分批次失败，均结构化 fail closed。
- 停止追踪点：Git transport 完成并得到 exact commit；OS 文件系统原子操作；
  Manager publish receipt；Provider 真实返回后的 Skill 使用证明。

## 任务清单（按依赖排序）

### Task 0 — Product state v1→v2 原子 migration [AC-SI-4, AC-SI-6]

- `ProductStateDatabase` 是唯一 schema/migration owner。保留 immutable v1 DDL/hash 常量；initialize
  识别严格有效的 v1 后，以单个 `BEGIN IMMEDIATE` 创建 v2 Skill-install aggregate/child/index，完成
  FK/row-count/manifest 校验，最后更新 schema meta 与 `PRAGMA user_version=2` 再 commit。任一 fault
  rollback 后必须仍是完整可 reopen 的 v1；成功后重复 initialize 幂等。v1 binary 看见 v2 明确拒绝，
  不自动降级；应用回退只允许恢复 migration 前备份。
- migration 开始前，owner 先对严格有效 v1 使用 SQLite `Connection.backup` 生成同 userdata filesystem
  的一致快照（无需把 WAL checkpoint 当先决条件），以 exact v1 validator + quick_check 校验、SHA-256、
  `0600`、file/parent fsync 后原子 rename；任何一步失败都在 DDL 前终止。成功或 migration rollback 后
  至少保留最新 verified pre-v2 backup。恢复只能由 app/DB 全停后的显式 offline tooling 执行：核 hash/
  权限/v1 manifest，先保存现 v2 recovery artifact，再同盘原子替换并只清匹配 stale WAL/SHM；禁止自动降级。
- 新增唯一 `capability_skill_install_intents` aggregate，冻结 stable preflight identity、effect/call/run/
  channel、Project scope、principal、source/exact commit/archive/raw/member/permission hashes、nonce/version/
  expiry/state_version，带完整 lifecycle CHECK、settlement/cleanup/verification refs 与 CAS API；按
  effect+call 唯一，按 status+expiry 查询。它不是第二套 Manager/decision journal，而是本功能唯一 intent owner。
- child rows 保存 ordered immutable install members 与 operation members；normalized name 在 intent 内唯一，
  operation committed member 行 FK 到 exact capability version，committed-set stamp 覆盖有序全集。现有
  publish intent 保持 aggregate header，并新增逐 member old/new binding child；append-only operation evidence
  记录 materialization、catalog swap、cleanup 与 runtime verification，使用稳定 idempotency key。
- intent 与 Manager operation 通过 handoff row 一对一（intent 可在确认前为 0 operation，确认后恰好 1）；
  staged member 只 FK intent，handoff transaction CAS 消费确认并原子创建/reuse operation + exact ordered
  operation-member rows。publish-intent member child FK operation member；terminal transaction 才写 committed
  binding/version refs。每一跳重算 canonical staged/operation/committed set stamp，要求 count、ordinal、
  normalized name、pack/version/manifest/content/source digest 全等，缺失/额外/重复均在外部 mutation 前拒绝。
- transactionally rebuild 现有 `capability_operations` 与 `capability_publish_intents` 为 v2 superset：保留
  所有 legacy kind/phase/row，新增 `skill_install_batch` 及 `batch_staged -> batch_prepared ->
  batch_publish_intent -> batch_files_materialized -> batch_catalog_swapped -> batch_committed`。publish intent
  同步 batch phase，binding envelope 带 schema discriminator；legacy 与 batch recovery 分支互斥。operation
  仍是唯一 Manager phase owner，phase evidence 只存 immutable proof，不决定当前 phase。
- 复用现有 versions、bindings、owner stamp、operation receipt 与 transaction owner；不修改旧 receipt，
  verification attestation 关联它，只有 attestation CAS 完整后 intent 才 succeeded。旧 userdata 的
  operations/bindings/receipts 原样保留，不迁移 legacy userdata Skill。
- migration 测试：fresh v2、真实 v1 fixture reopen、每个 DDL/copy/manifest/user_version fault rollback、
  v2 reopen 幂等、partial/corrupt/user_version mismatch fail closed、旧数据逐 hash 保持、downgrade 拒绝。
- schema integrity 不再只信 expected DDL hash/table names：维护 immutable structured v1/v2 manifest，按对象名
  排序采集 `table_xinfo`、`index_list/index_xinfo`、`foreign_key_list` 与允许的 table/index/view/trigger 清单，
  canonical JSON fingerprint；CHECK 用 SAVEPOINT 内的 valid/invalid named probes 验证。迁移 commit 前与每次
  reopen 都核 semantic live fingerprint、CHECK probes、foreign_key_check/quick_check；missing/altered/
  unexpected object、伪造旧 manifest、partial v2 均 fail closed。

### Task 1 — Project identity-aware Capability scope [AC-SI-2, AC-SI-5]

- 改动：扩展 `ToolExecutionContext`、所有 Host/SDK context factory 与 `CapabilityScope`，生成
  versioned project key；Hub/Store/Manager 只以 trusted identity key 做 project lookup/bind。
- 兼容：旧 path-only binding 可被诊断/列表，但不对新 Project Run 生效，不自动迁移。
  Project relocation 提升 revision 后，新 Run 只读新 key，旧 binding 立即 fail-closed 但保留为
  audit/recovery evidence；当前批准范围不自动 migrate/revoke，在新 revision 需重新安装。
- 验证：同路径不同 identity、同 identity 重启、relocation 后旧 binding 不可见且可诊断、
  stale revision replay、projectless 均有单元/集成测试。

### Task 2 — Raw Skill canonicalization 与 durable staged intent [AC-SI-3, AC-SI-4]

- 新增 `skill_install.py` 的 `BoundedGitHubSkillSource`：仅接受 normalized HTTPS
  GitHub repo URL；以有界 commit API 响应解析 exact SHA，再以该 SHA 请求 zipball，
  对 redirect host allowlist、响应头和实际 stream bytes 双重封顶。
- archive 解压前检查成员数、声明/实际字节、normalized path、casefold 冲突、
  symlink/special mode 和 zip bomb ratio；gitlink 目录不能生成 Skill candidate。递归发现
  `SKILL.md` 后生成每 Skill immutable instruction pack，每个再过 `CapabilityPackageValidator`。
- host-issued `ResolvedSkillSourceEvidence` 冻结 URL、requested ref、exact commit、archive hash、
  sorted raw file-set digest 和 selected subdirectory；后续层不重构 source identity。
- pack 粒度：每个 Skill 一个 instruction pack，但整批共用一个 install intent 和原子
  publish decision；任一项失败则不变更任何 binding。
- 名称冲突合约：同一将可见 Run catalog 内，Project candidate 若与 first-party 或已绑定
  Project Skill 有相同 normalized Skill name，stage 整批以 `skill_name_collision` 拒绝；仅
  exact pack/content digest 的幂等重放例外。不做隐式 precedence，不依赖查询排序选胜者。
- intent 持久化到 Capability SQLite；confirmation token 绑定 normalized URL、resolved commit、
  pack list/digest、Project identity/revision、expiry，消费一次后不可跨 Project 重放。
- intent 同时是 preflight/decision settlement 的唯一 durable aggregate，显式状态覆盖
  `staging -> awaiting_confirmation -> publishing -> published_pending_runtime_verification -> succeeded`，
  以及 `stage_failed|denied|expired` 各自的 `*_cleanup_pending` 中间态。只有 install service 可把
  opaque staging ref 解析为其固定 root 下的路径并删除；授权层永不接触文件路径。
- 验证：单/多 repo、排序稳定、重复名、空 repo、symlink/submodule/traversal/超限、
  ref 漂移、确认后篡改、过期/重放/拒绝。

### Task 3 — 原子 batch publish 与 Manager receipt [AC-SI-3, AC-SI-4, AC-SI-5]

- 扩展现有 Manager operation/publish-intent，使一个 operation 携带 immutable sorted member
  set、batch content root、expected CAS 和 committed-set stamp；batch operation id 由 exact
  Project scope + resolved commit + member/content digest 派生，member set 确认后不可变。阶段为
  `staged -> prepared -> publish_intent -> files_materialized -> catalog_swapped -> committed`，
  终态包含 `rolled_back/unknown`。
- 所有 member 在一个 batch staging root 预验证；持有共享 publish lock 时一次写入完整
  operation/expected CAS，一次 rename content-addressed batch root，一次交换组合 ToolSpec set，
  一个 SQLite transaction 写入全部 version/binding/owner stamp/receipt/committed。
- operation receipt 记录 exact pack id/version/manifest/content hash、Project scope key、binding generation、
  catalog revision 和 committed set stamp；幂等重放返回同一结果。
- batch lifecycle 只写 Task 0 的唯一 install-intent aggregate 与受约束 child/evidence；不再新建第二套
  Manager/decision journal，继续复用 Manager lock、operation receipt、recovery 和 idempotency owner。
- 崩溃点覆盖每个 batch phase 与每个 member prepare；恢复检查 operation + publisher
  fingerprint：full-new 幂等补交 DB，full-old 删除未提交 batch root 并 rollback，
  mixed/unverifiable 保持 unknown 且 catalog read fenced。

### Task 4 — 共享 application service 与 typed chat tools [AC-SI-1, AC-SI-3, AC-SI-4]

- `ProjectSkillInstallService.stage/confirm/cancel/status` 是唯一 owner；它调用 Task 2/3，
  并把 confirm 后状态先结算为 `published_pending_runtime_verification`，不对 UI/模型声称成功。
- 唯一 durable install intent 同时冻结 URL/exact commit/member digest/Project identity/revision/
  permission summary/expiry 及 confirmation nonce/version/principal/settlement status/consumed-at。chat 复用
  confirm-only decision grant；Settings 用 Host adapter 解答同一 intent。service 只接收 host-issued
  receipt，不接收模型或 UI boolean，不新建第二张 confirmation state table。
- receipt 消费用 CAS 一次性核对 nonce/version/principal/project/digest/expiry；approve 绑定
  batch idempotency，deny/expiry 持久 settle 并精确清理 staging，重复/跨渠道响应返回同一结算结果。
- 只注册一个 model-visible `skill_install` 并加入 SDK product catalog。产品
  `SdkPreparedAuthorizationPolicy` 注入 optional `ProjectSkillInstallPreflightPort`：仅该 Tool 在 SDK
  async authorization prepare 阶段先 stage，按 `(run_id,effect_id,args_hash,project_scope_key)` 幂等冻结
  intent，并把 opaque artifact ref、exact digest 与有界成员摘要写入 durable
  `AuthorizationRequest.metadata`；其他 Tool 的 port 默认为 `None`，零额外 I/O/行为变化。
- preflight port 返回 tagged `SkillInstallPreflightOutcome`：`Ready` 携带 intent/artifact/digest、
  有界清单/权限摘要和 expiry；`Rejected` 携带 durable failure receipt ref、stable code、公开消息、
  retryable 与 opaque correlation。Rejected 先 CAS 到 `stage_failed_cleanup_pending`，精确清理后落
  `stage_failed`，再映射为 `AuthorizationResult(DENY)`；不得创建确认卡或让异常逃逸成泛化成功文本。
- SDK 用户决定后继续复用现有 decision bind + effect handoff。唯一 Tool handler 不接收模型 receipt
  参数，而以 trusted run/call/effect IDs 通过 `AuthorizedPreflightReceiptResolver` 联查 SDK durable
  decision、Product authorization saga 的 `HANDOFF_COMMITTED` 状态与 install intent，签发 typed
  `AuthorizedSkillInstallReceipt` 后调用 `confirm_authorized`。receipt 覆盖 nonce/version、principal、
  Project/digest/expiry 与 decision/handoff 的 SDK+Host hashes；不暴露 model-visible confirm，不授予 shell。
- receipt 使用公共 immutable envelope + channel discriminator。`chat` variant 必须包含真实
  run/call/effect 与 SDK/Product decision/handoff hashes 并证明 saga=`HANDOFF_COMMITTED`；`settings`
  variant 必须缺省这些 SDK 字段，改含 Host-authenticated UI decision event/window receipt。两者都以
  stage 已冻结的 principal、Project scope、nonce/version、source revision、member digest 和 expiry
  校验并 CAS 同一 intent；exact replay 返回原 settlement，冲突 decision/channel/digest fail closed。
- `ProductAuthorizationAdapter` 在 durable saga CAS 后向 install service 发送仅含 opaque identity 的
  deny/decision-expiry terminal evidence；service 独占 `*_cleanup_pending` CAS 与删除。没有 SDK terminal
  callback 的 OPEN decision（含 Run 被取消）不制造授权事实，由有界 intent expiry reconciler 在
  startup 及 stage/status/confirm 前结算并清理；过期后 handler 永远无法发布。
- 恢复不依赖 `_facts`：adapter 先以 unique effect+call 查询现有 saga；cache miss 时仅从恢复的 frozen
  Run authority、saga request JSON 与 durable grant 复建 exact prepared facts，逐项核对 args/schema/
  capability/catalog/scope/policy generation/grant fingerprint/artifact hash。已持久化 artifact 禁止重跑
  current policy 或重解析 branch HEAD；缺失/歧义/错配标 unknown/quarantined，禁止 handler 和成功文案。
- saga 创建前，unique durable install artifact 是唯一 recovery authority：以 stable operation/effect/call
  选择恰好一个 artifact，核对 frozen source/commit/member/Project/Run/catalog/capability/schema/scope/
  prepared-call/grant bindings 后，幂等创建 saga 并 prepare/reuse exact grant。artifact-only、active-grant/
  saga-absent、saga-persisted/SDK-decision-absent 三个边界均不得重新解析 HEAD、创建第二 intent、publish
  或 dispatch；后者必须重绑原 nonce/version/receipt CAS。artifact 缺失/歧义/损坏/错配在任何 mutation
  前结构化失败；orphan grant 只有 stored/JSON fingerprint、version、generation、artifact binding 全等才复用。
- completion gate：只有 service 的 succeeded receipt 才能声称安装成功；denied/failed/unknown 禁止
  用文本绕过。
- 验证：tool search/describe/activate，schema violation/out-of-order/duplicate/long payload/refusal/bypass。

### Task 5 — Settings/Capability Center 薄 adapter 归并 [AC-SI-3, AC-SI-6]

- `backend/main.py` 的 `skill_install_from_url` 调用同一 service 的 stage；删除 multi 自动
  `finalize_batch`。Settings approve/deny 先进入 Host-owned Settings decision adapter，由它把真实 UI
  action 与 authenticated principal、Project、intent digest、nonce/version 绑定为同一个
  `AuthorizedSkillInstallReceipt` 类型，再调用 `confirm_authorized/cancel_authorized`；service 永不接收
  `approved: bool`。Capability Center 不再返回 `model_driven_action_required`。
- Settings decision adapter 只能使用 Host 已认证的本地主窗口/账户身份，并以 stage 返回的 opaque
  intent + one-time nonce 在后端重新取得冻结 Project/principal；不得信任 UI/query 提供的 Session、
  Project 或 scope。若现有生产通道不能证明这个绑定，confirm/cancel 稳定返回
  `settings_install_authorization_unavailable`，实现线必须回 A2 增加最小 Host bridge，不能降级。
- `SkillStorePanel` 显示冻结 Project 名称/身份、resolved revision、排序 Skill 列表、权限类别、
  digest 摘要与过期；一次 approve/deny 适用整批。
- 旧 `<userdata>/skills` inventory 继续可诊断，但不作为新 Run authority，不隐式迁移。

### Task 6 — Runtime usability 证明与可观测性 [AC-SI-2, AC-SI-5]

- 扩展现有 `ProductCapabilityCatalogSourceAdapter`/Run catalog preparation seam：在 trusted
  workspace resolution 后直接查询 exact owner + Project scope key 的 active instruction-pack bindings，
  输出带 manifest/content/scope hash 的 immutable Skill records；不新建 projection cache/owner。
- SDK Run authority 在每个 Run prepare 时合并 first-party records 与该 Project projection，
  不再只用进程启动时 tuple；projectless/其他 Project 不得到该 record。
- 保留 `FirstPartyFrozenSkillResolver` 历史类名与调用点，只修正注释/入场条件，使它继续从冻结
  run catalog pack entry 解析 first-party 或 managed Project exact pack；`skill_invoke` 只接受本 Run selection key。冷启动
  先 Manager rehydrate，然后从 Store binding/version 重建 projection。
- confirm publish 后通过 canonical `RunClient -> RunKernel` 创建一个新的
  `skill.install.verify` 零 Provider/零 Effect 验证 Run：它走正常 Project admission、Hub snapshot、
  `SqliteRunCatalogLeasePreparer` 冻结和 authority-neutral resolver，对每个 member 执行 exact
  selection + content page-in/hash 复验，然后以 canonical terminal event 关闭。
- verification Run 的 canonical terminal event 是不可变证据；service 在同一 install
  operation/receipt 上 CAS 写入 verification run id、Project scope key、run catalog stamp 与所有
  manifest/content/scope hash，不新建第三个 receipt type/table。只有这些证明持久化后，service 才从
  `published_pending_runtime_verification` CAS 到 `succeeded`，Settings 才显示“安装成功”，
  chat completion gate 才允许返回 success。验证 Run 失败/崩溃则保持 pending/failed，可幂等重试，
  不回滚已提交 pack 也不误报可用。
- 添加无正文日志：intent/operation/run/project 的 opaque correlation、phase、reason code、latency；
  诊断导出不含 repo body/token。
- 把 publish + catalog visibility 延迟纳入 2s 预算测试。

### Task 7 — 回归、真 UI 和事实源 [AC-SI-1 ～ AC-SI-6]

- 机器门：Capability/SDK adapter/backend WebSocket/Vitest/TypeScript，加上 malicious fixture 和
  crash recovery。保留已有 Provider/Session/Skill/Capability 回归。
- macOS 真 UI：使用 fresh isolated `DESKPET_USER_DATA_DIR`，通过真实坐标点击和聊天输入
  执行 SI-M1～SI-M5；原始证据放 `.local-test-evidence/2026-08-27/<run>/`，仅在结论文档
  保留 scenario ID、相对索引和 SHA-256。
- 验收绿后同一交付更新 `ARCHITECTURE/ARCHITECTURE.md`、`AGENT_HARNESS.md`、`UI.md`、
  `PROJECT_STATUS.md` 与本 plan results。启动一个新鲜当前构建供用户测试，不修改原 userdata。

## A2-001 回炉与关键假设 spike（2026-08-28）

- 执行中证实原 plan 漏掉授权 owner seam：ToolRegistry 先校验 grant，之后才调用 handler；因此
  handler 内 stage 会让用户确认未知的 exact batch，而 UI boolean/handler 自签 receipt 又违反
  Host authority。该缺陷已按 `owner-missing` 写入 plan-test 账本并停止 Task 2～5 受影响线。
- 可丢弃真代码 spike 使用现有 `SdkPreparedAuthorizationPolicy`、`ProductAuthorizationAdapter`、
  authorization saga、durable grant 与 SDK ToolRegistry，实际观测顺序为
  `async_stage -> confirmation_prepared -> decision_bound -> handoff_bound -> host_receipt_resolved -> handler`，
  测试 `1 passed in 0.17s`。
- 结论：采用 Task 4/5 所述 Host-owned optional preflight + receipt resolver 是范围可控的结构修正；
  无需第二个 Tool、无需修改 vendor SDK、无需新建 decision/receipt journal。完整本地证据：
  `.local-test-evidence/2026-08-27/chat-skill-install-plan-loop/a2-001-preflight-spike.md`，源报告
  SHA-256 `21b13c137a3cc833950cabf7242907d5d0ce2a7cfa21f4d48b3a1189b556e17b`。
- production rehydration spike 进一步在清空 adapter/policy/registry/`_facts` 后，以 persisted saga、
  verified durable grant 与 restored frozen Run authority 完成 exact resume，且证明 recovery 不调用
  current policy；missing saga、scope mismatch、corrupt indexed grant fingerprint 均 fail closed，
  `2 passed in 0.21s`。实现须新增 public verified grant lookup、narrow `restore_facts`、durable-first
  adapter replay 与完整 crash matrix。证据：`.local-test-evidence/2026-08-27/chat-skill-install-plan-loop/
  spike-auth-preflight-rehydration.md`；测试 SHA-256
  `42605ef563eb70f34d31f71dd19beb104e0f3a670c32049e0325f87421abd183`；最终 `6 passed in 0.18s`，
  额外覆盖 artifact-only、orphan grant、pre-SDK-decision 与四类 mutation-before-stop 反例。

## A2-002 schema 回炉与 migration spike（2026-08-28）

- 执行证实现有 Product schema v1 无法约束 finalized intent/batch CAS，且运行时加表会被 strict manifest
  reopen 拒绝；已按 `owner-missing` 停止 Task 2～4 并回到 Phase 2。
- disposable ProductStateDatabase migration spike 在真实 populated v1 上覆盖 14 个 fault boundary，全部
  rollback 后由真实 v1 validator reopen；成功/重复 v2 reopen、legacy hash、FK、downgrade/corruption
  fail-closed 均通过，`19 passed in 0.16s`。证据：`.local-test-evidence/2026-08-27/
  chat-skill-install-plan-loop/a2-002-migration-spike.md`，报告 SHA-256
  `5261be2f360eb2bdfaabd54a73935e202212d5db4148f64baa32c6a7ff4cb3e3`。
- 结论：采用 Task 0 的唯一 intent aggregate + existing Manager journals v2 superset rebuild；这不是第二套
  publish/decision journal。schema migration、live semantic manifest、verified backup/offline restore 和 exact
  relational handoff 必须同一 release unit 落地并完成旧 userdata reopen/crash proof。

## 停止条件

- 任一方案要求把 Project identity 改为模型参数、直接写 `.claude/skills` /
  `.codex/skills`、或在 Manager receipt 前报成功：立即停止并回到架构评审。
- 原子 batch 无法在现有 Manager/Store 上保证 all-old/all-new：先做可丢弃 spike，
  不用 best-effort partial publish 降级验收。
- 真 Provider/网络/系统权限不可用时，精确标记对应 UI/real-provider gate BLOCKED，
  不用自动化或协议层证据代替。

## 关键 spike 证据

### SPIKE-SI-RAW-TRANSPORT

- 真跑：对目标仓库执行 `git clone --filter=blob:none --no-checkout --depth 1`，然后
  `git rev-parse --verify 'HEAD^{commit}'`、`git ls-tree -rz <commit>` 和 `git clone -h`。
- 输出：commit=`fc0f94deed20aa00ca65874edaf345d50a2f4b1b`，tree entries=`200`，
  partial clone `.git`=`416 KiB`。CLI 只提供 depth/filter/timeout 类控制，没有
  max bytes/object-store 硬上限。exact-SHA zipball 真请求经
  `github.com -> codeload.github.com` 得到 200，但未提供 Content-Length。
- 结论：放弃 Git CLI 作 raw untrusted transport；选用 bounded GitHub commit API +
  exact-SHA archive stream，对实际读取字节计数并超限中止。

### SPIKE-SI-PROJECTION

- 真跑：在 backend venv 实例化 `ProductCapabilityCatalogSourceAdapter`，分别传入
  `owner_key=project:A`、`project:B` 和空 skills，生成 SDK resource records。
- 输出：A/B 均生成 `skill:plan-test`，`source_revision` 分别为 `project:A/B`；
  projectless 记录数为 0，`isolated=True`。源码还显示现有
  `SqliteRunCatalogLeasePreparer` 会把 Hub selected store pack 冻结为 `entry_kind=pack`，
  resolver 已从该 Run snapshot 解析正文。
- 结论：SDK record adapter 和 frozen resolver 可复用；只需补每 Run Project metadata
  source 与 resource-record capture seam，不新造第二套 resolver。
