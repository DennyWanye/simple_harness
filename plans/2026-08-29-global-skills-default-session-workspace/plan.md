<!-- plan-status: finalized -->

## 2026-08-29 增量修复：Auto Skill 安装与 Run/UI 收敛（已批准）

- 现场根因：`SdkPreparedAuthorizationPolicy.decide()` 只在 manual 分支执行 `stage_authorization_preflight()`；Auto 直接 ALLOW，`skill_install_handler()` 随后无法取得 intent/decision receipt，连续返回 `skill_install_intent_not_found`。错误被 registry 压成非重试友好的通用失败，Provider 最终进入 `provider_outcome_unknown`，前端健康检查只重置本地 store，未收敛 durable SDK Run。
- Task C4（AC-9）：把 Skill source stage/validate 提到 Auto/Manual 共用路径；新增 `auto-approved-skill-install-v1` Host receipt。唯一 trusted constructor 是 `ProductAuthorizationAdapter + AuthorizationSagaRepository`：用 CAS 从 `PREPARED` 绑定 auto decision nonce/version/provenance，再激活 exact grant；随后只复用真实 SDK effect/handoff callback。receipt hash 覆盖 approval kind/version、intent/content/member stamp、principal、policy generation、grant fingerprint、run/root/call/effect、expiry。resolver 只接受完整 handoff-committed 且 provenance exact-match 的 auto-v1；manual-v1 与历史 hash 字节兼容。禁止伪造 SDK decision receipt或模拟点击 manual UI。
- Task C5（AC-10）：将 `ProjectSkillInstallError` 的 stable code/public message/retryable/allowed actions/failure receipt 映射到 SDK Tool outcome。stage failure key 只由 validated owner + normalized URL/ref + stable source input 派生，排除 run/call/effect；同 generation 的 non-retryable replay 直接返回同一 durable rejection，只有显式、幂等 retry command可按 policy 增 generation。新增 `session_run_status_query`：后端解析 root→SDK Run并调用 `RunClient.query()`，返回 run_version/state/waiting_reason/allowed_actions/terminal_ref；UI 在匹配且不旧于当前版本的 ack 前保留 active_run_id，genuine waiting 显示等待/恢复/停止。`chat_v2_interrupt` 携 cancel command/expected version，只有查询到 durable terminal 后 ack；完成/取消竞态由最高 durable Run version决定。5 秒无 ack 时显示连接恢复态但不得假装 idle。
- 验证：新增 TC-GS-10；扩展 TC-GS-03/09；自动化覆盖 Auto/Manual receipt、无 intent 回归、错误映射、duplicate non-retryable、durable cancel/ack 和 UI 状态。真实干净 profile 使用 `deepseek-v4-flash` 从 Chat 安装 `plan-test-skill`，验证无确认成功；再用无 Skill 仓库验证错误和 UI 收敛；新 Session 复验全局可见。
- 用户批准 hash：`6b84b0709b71298582d32cc57259dea8a3bcb7c9a2ed9841d23776f4ccacf226`。

# Program Plan：全局 Skill、普通会话默认工作区与自动权限

## 主要矛盾

决定成败的核心问题是把三类 authority 正确分离后再组合：Session workspace 必须逐 Session 冻结，Skill visibility 必须逐 validated owner 全局共享，Tool execution 必须逐 effect 继续受 authorization/health/platform/workspace gate 约束。当前实现把普通会话做成 projectless、把 managed Skill 安装绑定 Project、又用同一个 executable catalog 表达 discoverability，无法靠开关或目录复制满足需求。

## 关联验收与 release slicing

- Program 覆盖 AC-1～AC-10，行为下限见 `acceptance.md`、`assurance-contract.json` 和 `behavior-contract.md`。
- MUST AC 共 9 条，超过单 release unit 的 8 条阈值，拆为三个垂直 slice；每个 slice 独立 gate/init/finalize，Program 只有三个 slice 均 SHIPPABLE 才完成。
- Slice A（AC-1/2/6/7/8）：automatic workspace 与普通 Session authority。
- Slice B（AC-3/4/6/7/8）：user-global Skill lifecycle 与 runtime snapshot。
- Slice C（AC-4/5/7/8/9/10）：全局 descriptor catalog、eligibility、权限默认/迁移与 Run/UI 收敛。
- `BEHAVIOR_POLICY=preserve-approved`：不减少任何已批准外部行为；Project-scoped Skill 的未交付语义按 acceptance 明确退休。

## 最佳实践与本项目适配

1. OS known-folder 而非字符串拼路径。Apple Foundation 的 `FileManager.url(for:in:appropriateFor:create:)` 用于定位标准目录，`.documentDirectory + .userDomainMask` 才能适应沙箱、Documents 重定向与用户域。simple_harness 后端是 Python，因此由 Tauri/Rust host resolver 调 Foundation 并通过 composition port 提供 canonical URL；测试注入 fake resolver，不在 Python 写 `~/Documents`。参考：https://developer.apple.com/documentation/Foundation/FileManager/url%28for%3Ain%3AappropriateFor%3Acreate%3A%29 与 https://developer.apple.com/documentation/foundation/url/documentsdirectory
2. SQLite 只保证数据库事务原子，不覆盖外部 `mkdir`。SQLite 官方 atomic-commit 依赖 journal/WAL 与 crash recovery；因此目录和 DB 必须用 durable saga/receipt 协调，而不是声称跨资源单事务。项目已有 operation/receipt/CAS 模式，复用同一思想，不引入分布式事务框架。参考：https://sqlite.org/atomiccommit.html
3. Descriptor/executor 分层。项目 Provider 只能接收真实 callable `ToolSpec`，但产品 search/describe 要表达 unavailable 原因；因此新增轻量 descriptor projection，执行 registry 保持 fail-closed。它是现有 `ProductToolInventoryEntry` 的演进，不引入第二套执行 authority。
4. Immutable generation + fresh-Run snapshot。复用 Capability Manager/refresh CAS；发布产生新 global generation，fresh Run 冻结，运行中 Run 不突变。避免 watcher/后台任务，安装事务同步等待 snapshot publish 与 production verifier。

## 放弃的备选

- 不把 Skill 复制到每个 Project/Session：会产生重复状态、漂移和无法原子升级。
- 不把 automatic workspace 当普通用户 Project 展示：会改变侧栏语义并淹没 Project catalog。
- 不让 `mcp:filesystem` 继续指向 userdata root 冒充 Session workspace：违反 immutable root authority；本轮只让它可发现并结构化不可用。
- 不直接把所有历史 `manual` 改成 `auto`：无法区分用户选择，违反 AC-9。
- 不在安装后重建整个 SDK stack：生命周期重、会影响运行中 Run；改为 snapshot provider CAS。

## 文件影响清单

| 文件/目录 | 当前职责 | 计划改动 |
|---|---|---|
| `tauri-app/src-tauri/src/process_manager.rs`, `paths.rs` | native host bootstrap/path | spawn 前解析 Documents known-folder，将 canonical root/identity 写入 versioned child stdin bootstrap |
| `backend/deskpet/session/project_binding.py` | Project/Session binding与 creation receipt | automatic workspace schema、allocation saga、普通分类、旧 projectless migration |
| `backend/deskpet/session/project_protocol.py` | UI command adapter | session_create 的 selected/default intent 与结构化错误 |
| `tauri-app/src/components/SessionList.tsx`, `ProjectPickerDialog.tsx` | Session/Project UI | “新建普通会话”选择/跳过目录，展示 effective root 与错误 |
| `backend/deskpet/capabilities/contracts.py`, `store.py` | scope/schema/operations | stable global owner key、v2 global install contract、binding retirement、policy provenance |
| `backend/deskpet/capabilities/skill_install.py` | Project-scoped installer | 演进为 v2 global owner service，Settings/chat 两种 receipt 证据链 |
| `backend/deskpet/capabilities/manager.py`, `refresh*.py` | publish/bind/catalog CAS | user binding publish、project binding retirement、global snapshot generation |
| `backend/deskpet/sdk_adapters/tools.py`, `capability_catalog.py`, `tool_authority.py` | SDK Tool/resource catalog | descriptor/eligibility 分层、fresh-Run snapshot consumer |
| `backend/main.py` | production composition/adapters | principal-only Settings adapter、Global service、runtime snapshot provider、移除 per-workspace descriptor filtering |
| `tauri-app/src/components/SkillStorePanel.tsx`, `SettingsPanel.tsx` | Settings UI | 全局作用域文案/receipt；auto 初值与 provenance 状态 |
| `backend/tests/**`, `tauri-app/src/**/*.test.tsx`, `tauri-app/src-tauri/src/**` | 验证 | migration、fault、concurrency、wiring、UI、runtime tests |
| `ARCHITECTURE/*` | 生产事实 | 各 slice 通过后回写最终事实与证据 |

## Complexity inventory

| 复杂度表面 | 是否新增 | 理由 / 绑定 |
|---|:---:|---|
| 新依赖 | 否 | Foundation/SQLite/现有 SDK 与 Manager 足够 |
| 新公共 API | 是 | Host Documents resolver 与 descriptor eligibility；AC-1/5 |
| 新持久化状态 | 是 | allocation saga、v2 global install、policy provenance；AC-1/3/6/9 |
| 新配置项 | 否 | 根目录固定由 OS Documents resolver；无用户可调隐藏开关 |
| 新抽象层 | 否 | allocation 是 SessionCreationService 内部 repository operation；catalog generation 扩展现有 Manager/refresh |
| 新后台任务 | 否 | 创建/安装同步推进并由 startup reconciliation 恢复 |
| 复用已有实现 | 是 | ProjectBinding、Capability Manager/store/refresh、Git source validator、prepared authorization |
| 平台能力 | 是 | Foundation Documents URL、canonical filesystem identity、SQLite CAS |

## Assurance / 信任与失败边界

- Profile：standard；绑定 `ASSET-1..4`、`TRUST-1..2`、`FAIL-1..8`、`ADV-1`。
- Host native picker/known-folder、validated local owner、Run/effect identity、Manager receipt 是 trusted；Git URL/content、LLM payload、UI自由文本不可信。
- 目录 saga 只补偿自身创建、identity 未变且为空的目录；用户内容出现即保留并报告 orphan。
- Skill v2 receipt 不记录 token；Settings 窗口只解析 principal/global owner，不再解析当前 Project。Chat receipt 继续绑定 SDK/Host decision fences。
- 停止追踪点：Foundation 返回 canonical Documents URL；SQLite allocation/operation terminal receipt；Manager global binding receipt；Runtime snapshot generation CAS；fresh Run exact manifest/content verification；真实 Provider/Effect terminal。

## Slice A — Automatic workspace（3 Tasks）

### Task A1 — Host Documents resolver 与 allocation schema [AC-1, AC-2, AC-6]

- 改动：Rust parent 在每次 backend spawn 前调用 Foundation user-domain Documents API，把 canonical root/identity 写入 `host-bootstrap-v2` 并沿现有 child stdin 一次性发送；Python composition 前严格解析为 immutable `HostDocumentsRoot`。不新增 backend→Tauri RPC/renderer round-trip。扩展 Project/Session schema，新增 `project_origin`（`user_selected|automatic_session_workspace`）、`workspace_allocations` aggregate 与状态/identity/receipt/index。
- allocation identity：request ID 冻结预分配 session UUID 与 allocation ID；目录名为 sanitized session display seed + UUID 短后缀，冲突时不改 stable identity而结构化失败/恢复。
- policy：目录不存在可创建 `SimpleHarnessProjects` 与 leaf；只读、redirect/canonicalization/symlink mismatch fail closed。
- migration：旧 Project 记 `user_selected`；旧 projectless Session 建 pending allocation，不在 schema transaction 内 mkdir。
- 验证：Rust resolver tests + Python schema vN→vN+1 semantic manifest/fault rollback；Documents 缺失、只读、redirect、Unicode/case/symlink fixtures。

### Task A2 — 可恢复创建/迁移 saga [AC-1, AC-2, AC-6, AC-7]

- 改动：在 `SessionCreationService` 内新增 repository-level orchestration，禁止调用公开 `register_project()` 嵌套事务。严格按 T0 `reserved` SQLite commit → F1 exclusive mkdir/owned marker → T1 `directory_created` identity CAS → T2 Project/Session/binding/catalog/creation receipt 单一 SQLite transaction并置 `completed` → F2 marker cleanup；另有 `compensation_required|failed`。禁止 `project_committed/session_committed` 中间态。
- replay：同 request 并发只一个 owner；lost ACK 返回原 result。崩溃补偿只删除 exact empty leaf；非空/identity drift 写 `compensation_required` 并保留。
- 旧 projectless：首次 fresh Run admission 前强制完成一对一 migration；失败不得偷偷走 projectless Tool 集。
- 验证：每个 crash edge、并发、lost ACK、用户写入、目录替换、迁移重启决定性测试。

### Task A3 — 普通会话 UI 与生产接线 [AC-1, AC-2, AC-7, AC-8]

- 改动：`SessionList/ProjectPickerDialog` 的新普通会话流程提供“选择目录/使用默认目录”；selected path 仍先 preview/register，默认 path 只发 intent。Session descriptor 分离 `display_scope=ordinary` 与 `workspace_kind=project_bound`，侧栏不把 automatic Project 展示为用户 Project；Inspector 显示实际工作目录。
- 空态 `new_session=true` 走同一 service，不再建立 legacy projectless。
- 接线断言：UI protocol、chat path、fresh/continuation、recovery 都只使用 immutable binding。
- 验证：Vitest + backend protocol integration + current-build macOS UI S-GS-01/02/05。

## Slice B — User-global Skill lifecycle（3 Tasks）

### Task B1 — Global owner key 与 v2 install migration [AC-3, AC-6, AC-7]

- 改动：在现有 local identity loader/Capability scope value object 中，以 validated `identity_namespace_hash` 派生跨重启稳定、跨 OS user 隔离的 `global_owner_key`，替换 `user_key='default'`；不新建独立 identity service。新增 v2 global intent/member/handoff/receipt hashes；v1 Project records 保持只读解析。
- migration：v1 nonterminal → `superseded`; v1 succeeded 只有 exact Manager version/receipt 可验证时创建 migration operation，原子产生 user binding并退休所有同 capability 的 Project binding。project precedence 不得再覆盖 global Skill。
- downgrade：旧 binary 遇 v2 global schema 明确拒绝；backup/restore沿 ProductState versioned edge。
- 验证：fresh、v1 fixtures、每个 migration fault、跨 profile generation、跨 userdata隔离、collision/precedence。

### Task B2 — Global install service 与双入口 [AC-3, AC-6, AC-7, AC-8]

- 改动：把 `ProjectSkillInstallService` 演进为 `GlobalSkillInstallService` v2；Git staging/validator/Manager batch publish继续复用，但 visibility key只取 global owner。Settings `_settings_skill_install_adapter` 删除 `resolve_project_binding()`，窗口认证仅解析 trusted principal/global owner；Chat preflight仍绑定 run/call/effect与 explicit-only receipt，但不把 Project写入 install hash。
- 幂等：同 owner + exact commit + member set 一个 operation；并发两 Session 安装收敛到同 terminal receipt。
- UI：Skill Store 改为“全局安装/所有会话可用”，success 只来自 v2 terminal verification。
- 验证：Settings无 Project、Chat有/无显式 Project、malicious Git、batch partial fault、并发/重放、UI contract。

### Task B3 — Runtime global snapshot publish/verify [AC-4, AC-6, AC-7]

- 改动：扩展现有 Capability Manager/refresh snapshot API，把 managed Skill metas/resource records/tool authorities 做成 immutable generation；Manager global publish 后以 CAS 构建/交换 current snapshot。fresh Run 在 `_execute_sdk_run` 冻结 generation，continuation复用 run snapshot，运行中 attempt不变化；不增加第二个 catalog owner/provider。
- verifier：Settings/chat成功前启动 production ingress verifier，证明 exact Skill manifest/content hash在新 generation可 resolve/page-in；失败保留上一 snapshot并把 operation终态为失败/可重试。
- 不重启 SDK stack、不加 watcher；startup reconciliation重建未完成 snapshot publish。
- 验证：旧/默认/选择目录 Session、冷重启、publish CAS race、失败回滚、fresh vs in-flight generation、真实 Skill invoke。

## Slice C — Descriptor catalog 与自动权限（3 Tasks）

### Task C1 — 全局 descriptor 与 execution eligibility [AC-4, AC-5, AC-7]

- 改动：扩展单一现有 `ProductToolInventoryEntry`/Capability resource record authority，加入稳定 descriptor 字段（identity/schema/source/availability reason）；同一 frozen snapshot 纯投影为 search/describe descriptor 与 Provider eligible ToolSpec，不新增 descriptor store、持久化 catalog或第二 generation。
- `mcp:filesystem` 在不能绑定 Session root时 descriptor为 `unavailable:workspace_unscoped`；unhealthy/platform不支持也有稳定原因。Skill descriptor全局出现，allowed tools缺失时 invoke返回结构化 eligibility。
- wiring/exhaustiveness：`PRODUCT_TOOL_NAMES`、静态 manifest、descriptor、handler registry有全集断言；动态 MCP按 configured/health snapshot进入 descriptor。
- 验证：三类 Session descriptor等价、callable subset正确、unavailable不能执行、search/describe可见、授权不绕过。

### Task C2 — Authorization provenance 与默认 auto migration [AC-9, AC-6]

- schema：`authorization_policy_state` 增加 `provenance` 与 schema generation；Settings CAS写 `user_explicit`。
- 旧状态确定表：fresh新库→`auto/factory_default`；有有效 legacy import→保留导入值/`legacy_import`；generation>0→保留/`user_explicit`；manual generation=0且legacy outcome=`missing`且无用户set receipt→迁移`auto/factory_default_migrated`；证据冲突/不足→保留manual/`needs_user_choice`并在UI提示选择。
- policy：confirm-only names、Skill publish、deny、scope expansion、health/platform/workspace unavailable保持不自动执行；legacy gate仅镜像 SQLite结果。
- 验证：五类migration、CAS并发、冷重启、explicit-only/deny/unavailable、用户override。

### Task C3 — UI、production object graph 与 program regression [AC-4, AC-5, AC-8, AC-9]

- Settings 初始占位改为 true，但后端 response仍唯一 authority；显示“自动（推荐）”及 `needs_user_choice`。删除“auto-allows every category”误导注释。
- Capability/Skill UI 展示全局 scope、catalog generation、availability reason；Session Inspector展示默认/选择目录。
- production wiring tests从 Settings/chat→Manager→snapshot→`SdkRuntimeIngress/ProductSdkRuntimeStack`，并证明 dormant RunKernel/legacy loader无调用。
- 验证：critical+affected+因共享 runtime触发 full-surface smoke；macOS current build S-GS-03/04/05/07/09，真实 Provider/Skill/Tool路径。

### Task C4 — Auto Skill approval saga 与 preflight [AC-9]

- common preflight：workspace/current-authority、policy、health、platform admission 后，Auto/Manual 都先执行同一个 Skill source stage；任何 source rejection 都在 authorization decision 前 DENY、零 active grant、零 publish。
- auto transition：repository CAS 绑定 `auto-approved-skill-install-v1` decision nonce/version/provenance，重复同 identity 返回原 receipt，digest/policy/principal/version 任一不同则 conflict。只有 auto decision durable 后激活 exact TaskGrant；取消、expiry、effect failure或 provenance mismatch 撤销 exact grant并清理 staged intent。
- handoff/resolver：Auto 不调用 manual `bind_decision`；让既有 genuine `bind_effect_handoff` 接受 auto-decision-bound 前态并提交 handoff。resolver 按 `approval_kind` 分派：manual-v1继续要求 SDK/Host decision和handoff hashes；auto-v1要求 Host auto-decision hash、genuine SDK/Host effect/handoff hashes和 exact intent/content/member/policy identity。
- 验证：合法 Auto 无点击成功；非法 URL/缺 Skill/哈希失败无 auto transition；未 handoff 不可 resolve；重启/并发/lost ACK稳定重放；伪造 decision/handoff hash拒绝；manual fixtures/hash/拒绝/expiry全绿。

### Task C5 — Install failure、Run status 与 authoritative cancel [AC-10]

- failure contract：将 install exception 转成结构化 Tool failure，字段固定 `code/public_message/retryable/failure_receipt_ref/allowed_actions`，不得泄漏内部异常。source-input-derived failure key + attempt generation抑制相同 non-retryable replay；变更 URL/ref/input产生新 key。
- status contract：前端只发 `session_run_status_query(query_id,session_id,root_run_id,sdk_run_id,observed_run_version,observed_ui_status,observed_inflight,stuck_duration_ms)`；后端返回 authoritative `run_version/state/waiting_reason/allowed_actions/retryability/terminal_event_ref`。recognized waiting 保持 WAITING，unowned waiting显示 Cancel；旧版本/late ack忽略。
- cancel contract：`chat_v2_interrupt(cancel_command_id,session_id,root_run_id,sdk_run_id,expected_run_version,reason)` 复用正式 SDK cancel seam；already-cancelled幂等，completed/failed返回既有 terminal，不伪造 cancelled。UI 只在 matching terminal ack 后一次性清 inflight/active_run_id；5 秒无 ack停止假进度并显示恢复态。
- 验证：同 non-retryable source只 resolve一次且跨重启重放；running query不本地 reset；recognized/unowned waiting投影；query timeout保留 active_run_id；cancel replay与 cancel/completion/reconciliation race恰好一个 terminal；拒绝/terminal ack后 UI ≤5 秒收敛。

## Program 依赖、并行与停止条件

- 顺序：A1→A2→A3；B1→B2→B3；C1与C2可在B3后并行，最后C3。A与B的schema设计先统一版本边，再可并行实现。
- black-box验证准备在用户批准后与实现并行，只读 acceptance/behavior contract，不读实现代码。
- 若 Host Documents resolver需新增沙箱 entitlement、validated owner无法提供跨 generation稳定 identity、或 runtime snapshot无法在不重启 stack下CAS交换，先做可丢弃 spike；spike失败则 architecture reset并请求用户重新确认，不用路径字符串/全栈重启hack。
- 每个 slice task≤3、MUST≤5、高风险子系统≤3；各自独立 baseline、manifest、receipt。

## Primary challenge synthesis 修订（Round 1）

- Native bridge：Task A1 不新增 backend→Tauri RPC。Rust parent 每次 spawn 用 Foundation 解析 Documents，并把 `documents_root/path_identity` 加入 versioned host-bootstrap-v2，经现有 child stdin 一次性传入；Python 在 composition 前严格解析。v1/缺字段只允许测试或不需要 automatic workspace 的路径，automatic create 返回 `host_documents_unavailable`，禁止 HOME/env/renderer fallback。
- Global owner：Task B1 在现有 identity loader/scope value object 内，以 `HumanIdentity.identity_namespace_hash` 为稳定 seed，读取 existing identity 时强制 regular-file、非 symlink、当前 uid owner、0600、父目录非共享可写；domain-separated SHA-256 形成 opaque key。失败关闭 install/publish但保留 last committed catalog。
- Workspace saga：删掉 `project_committed/session_committed`。唯一状态为 `reserved,directory_created,completed,compensation_required,failed`；边界为 T0 reserve(SQLite)→F1 exclusive mkdir+owned marker→T1 identity fence(SQLite CAS)→T2 Project/Session/binding/catalog/receipt 单事务 publish→F2 非权威 marker cleanup。任何 product row 只在 T2 一起可见。
- Legacy convergence：冻结 owner+pack 的完整 Project binding-set stamp。所有可验证来源 exact `(pack,version,manifest,content,receipt)` 相同才迁移；不同版本/manifest 或既有不同 global binding进入 durable `legacy_global_conflict`，保持旧 global snapshot并要求显式全局重装/选择，不按时间/semver/row order猜 winner。user promotion 与全部 Project retirement 同一 CAS transaction。
- Release rollback：三 slice 共用 release generation R。ingress关闭后先对 `state.db/workflow.db/sdk-product-state.db` 创建 verified backup-set manifest；external marker依次 `backup_verified→state_migrated→product_state_migrated→workflow_migrated→semantic_backfill→runtime_published→completed`。新 semantic write 前可 whole-set restore；之后只允许 roll-forward或显式 whole-set offline restore，并 quarantine R 创建的 workspace，禁止单库恢复和旧 binary 写新 schema。
- Verification：实现前冻结 `TC-GS-01..09`，逐一对应 `S-GS-01..09`；fixture `GS-FX-IMMUTABLE-01` 固定 Git exact commit/archive/member SHA，单 Skill `simple-harness-global-proof`，真实 Provider invoke 返回 `GS_GLOBAL_SKILL_OK:<manifest_sha>:<workspace_basename>`。命令入口固定为目标 pytest、Vitest、Cargo resolver tests、black-box probe、baseline_runner 与当前 full-surface smoke；macOS 只经当前 Tauri build 真点击，证据进 ignored `.local-test-evidence/2026-08-29/`。
- Frozen assets：上述 oracle 已具体落在 `verification/testcases.md` 与 `verification/fixture.json`；spike 命令/原始输出落在 `verification/spike-results.md`。实现不得反向放宽这些文件，gate init 按逐文件 SHA 锁定。

## Required spike 结果

- `SPIKE-NATIVE-DOCUMENTS-BOOTSTRAP`：2026-08-29 当前 macOS 运行 Foundation Swift probe，输出 `true` 与 `/Users/denny/Documents`，证明 `.documentDirectory/.userDomainMask` 返回 file URL 且无需新增 entitlement。代码核对证明现有 Rust parent 已向 child stdin 写 bootstrap，故选择 host-bootstrap-v2；实现后仍需 packaged round-trip test。
- `SPIKE-GLOBAL-OWNER-AUTHORITY`：使用当前 `load_or_create_local_identity` 在临时 user-data 连续加载，输出 `stable True`、`owner_mode_symlink True`、domain-separated key length `64`、`symlink_detectable True`。证明稳定 seed 与 POSIX owner/mode/symlink guard 可实现；现有 loader 尚未执行这些 guard，Task B1 必须补齐并覆盖 corrupt/copied/shared-root 负例。
