# 架构基线：全局 Skill、默认普通会话工作区与自动权限模式

<!-- calibrated-head: 00cf185e -->

## 校准范围与工作树边界

- `ARCHITECTURE/ARCHITECTURE.md` 的正式锚点仍为 `362e5149…`，当前 HEAD 为 `00cf185e`。
- 正式架构文件已含用户未提交改动；Phase 0 不覆盖这些内容。本文件先冻结本需求的代码事实，功能通过测试后再把最终生产事实精确回写到 `ARCHITECTURE/`。
- 本次只调查 Session 创建/绑定、权限 policy、Capability Skill install、SDK Tool/Skill catalog 与 Settings/Chat adapters；实时语音等当前脏工作树内容不在范围内。

## 主要矛盾

普通会话目前是 `projectless`，因此生产 SDK 会主动裁剪所有需要 workspace 的 Tool；Project Skill installer 又把 intent、授权 receipt、binding 和 runtime verification 全部绑定到 Project identity。用户目标则要求普通会话始终有一个确定 workspace，Skill 属于全局用户 authority，并让每个 Session 看到同一完整产品 catalog。必须同时改变 Session 默认根与 Capability scope owner，不能只改 UI 文案或复制 Skill 文件。

## 解剖麻雀 1：普通 Session 创建与 workspace authority

1. `SessionList` 的“普通会话”调用 `session_create`，payload 的 `project_id=null`；输入框空态发送也通过 `new_session=true` 进入后端创建（`tauri-app/src/components/SessionList.tsx:166-174`；`backend/main.py:14765-14790`）。
2. `SessionCreationService.create_conversation_session` 接受 `project_id=None`，只在显式 Project 时校验 execution root；创建核心 Session 后，仅当 `project is not None` 才写 `session_project_bindings`（`backend/deskpet/session/project_binding.py:846-899,930-1025`）。
3. 因此当前普通 Session resolve 为 `ProjectlessWorkspace`，没有 execution root；回归测试明确断言这一事实（`backend/tests/session/test_project_scoped_sessions.py:146-164`）。
4. 目标架构不能把 `register_project()` 与 `create_conversation_session()` 直接嵌套：两者当前各自拥有 SQLite 锁/事务，`mkdir` 又是外部副作用。应新增一个由 `SessionCreationService` 调用的 durable allocation saga/repository API，以一个 allocation ID 协调目录、automatic-workspace Project、Session binding 与 creation receipt；UI 或 LLM 不得猜绝对路径。
5. 用户显式选目录仍复用 `project_preview → project_register → session_create(project_id=...)`；取消 picker 才回落默认创建，非法目录维持现有 fail-closed registration（`backend/deskpet/session/project_protocol.py:34-74`；`backend/deskpet/session/project_binding.py:145-211`）。

## 解剖麻雀 2：权限自动模式

1. 权限 authority 是 `CapabilityStore.authorization_policy_state` 单例，当前 fresh schema 写死 `manual` generation 0（`backend/deskpet/capabilities/store.py:383-392`）。
2. 启动时只把 legacy JSON 一次性导入 SQLite，并把结果镜像给旧 `PermissionGate`；此后 `_authorization_auto_mode()` 从 SQLite 读取（`backend/main.py:2660-2695,2945-2962`）。
3. Settings 的 `AutoModeToggle` 会先向后端拉真实值，但 React/localStorage 初值当前默认为 false；注释仍称 default OFF（`tauri-app/src/components/SettingsPanel.tsx:456-505`；`backend/main.py:13144-13163`）。
4. 用户要求的是普通会话默认自动模式。当前 policy 是进程/用户级全局单例，不是 per-Session 字段。不能只改 fresh DDL：旧库的 `manual,generation=0` 没有 provenance，无法区分历史默认与用户主动选择。目标 schema 必须增加 policy provenance（例如 `factory_default` / `legacy_import` / `user_explicit`）和 schema generation：fresh 为 `auto/factory_default`；可证明为旧 factory default 的记录一次迁移到 auto；legacy import 或用户显式 CAS 一律保留。无法证明的旧 manual fail-safe 保留并在 UI 要求一次明确选择，而不是擅自覆盖。
5. `PreparedAuthorizationRuntime` 仍以 policy/grant 决定调用，auto 不等于无条件执行；deny、强制外部确认和不满足 selector 的路径必须继续 fail closed（`backend/deskpet/permissions/runtime.py:450-520`）。

## 解剖麻雀 3：Skill 安装 authority

1. 生产已有 `ProjectSkillInstallService`，其 staged intent、confirmation receipt、source evidence 和 operation 全部包含 `project_id/revision/identity` 与 `canonical_project_identity_scope_key`（`backend/deskpet/capabilities/skill_install.py:32-72,250-430`）。
2. Settings 与 chat 已能进入这套 managed service，而非单纯 legacy directory copy；但成功语义仍是 Project-scoped，不能满足全 Session 可见（`backend/main.py:2828-2845,12390-12445,13235-13285`）。
3. Capability domain 已有 `CapabilityScopeKind='user'`，scope precedence 为 `run > project > user > builtin`；因此全局安装应落在 owner-scoped `user` binding，而不是新增裸 `global` 字符串或复活 `<userdata>/skills` loader（`backend/deskpet/capabilities/contracts.py:24-42,393-449`）。现有 `user_key='default'` 不足以隔离 profile/OS user，必须升级为由 validated local owner identity 派生且跨 profile generation 稳定的 opaque key；binding epoch 不能让升级后已安装 Skill 消失。
4. 需要把 install authority 泛化为 versioned user-global service contract：v2 intent/receipt/handoff/idempotency hash 绑定 trusted principal 与 global owner key，Manager 原子 publish 到 user scope；Project 只作为调用时 workspace context，不参与可见性 key。旧 v1 Project intent/receipt 只读解析，非终态全部退休；已终态 Project binding 也不能继续以更高 precedence 参与 Skill 选择，需由显式 migration operation 生成 user binding 后将旧 binding 标记 retired。
5. 安装后的 runtime proof 必须从 production `SdkRuntimeIngress → ProductSdkRuntimeStack` 的 fresh Run catalog 解析 exact manifest/content hash；不能用 dormant RunKernel 或 legacy loader reload 证明成功（`backend/main.py:7410-8045,9368-9515`）。

## 解剖麻雀 4：所有 Session 的 Skill/Tool catalog

1. `PRODUCT_TOOL_NAMES` 是产品静态 Tool 全集，`_freeze_sdk_catalog` 冻结进全局 SDK catalog（`backend/deskpet/sdk_adapters/tools.py:27-45`；`backend/main.py:7187-7242`）。
2. 但 Run admission 会调用 `filter_sdk_catalog_for_workspace`。当前 projectless 只留下 `PROJECTLESS_SAFE_TOOL_NAMES`；project-bound 还会排除无法绑定 Session root 的 process-wide `mcp:filesystem`（`backend/deskpet/sdk_adapters/tools.py:47-78,577-650`；`backend/main.py:9391-9410`）。
3. AC-1 完成后，新普通 Session 不再是 projectless，但 project-bound filter 仍会删除 process-wide `mcp:filesystem`。为满足“每个 Session 可访问所有 Skill 和 Tool”，需要新增稳定的 descriptor catalog authority：它记录产品 Tool、configured MCP/Skill 及 `available | unavailable(reason)`，供 search/describe 使用；现有 executable `ToolSpec` registry 仍只承载可调用 handler。调用一个 descriptor 必须再经过 workspace/policy/health/platform eligibility，不能把 unavailable descriptor 注入 Provider 的 callable schema。
4. `mcp:filesystem` 当前不能绑定 Session root，本次不把它伪装为可执行；它全局可发现并返回结构化 `workspace_unscoped`。若未来要求它实际执行，需另立 per-Run scoped MCP 实例范围，不复用 process-wide userdata root。
5. Skill metadata 当前在 `ProductSdkRuntimeStack` composition 时冻结为 `skill_metas/resource_records/tool_authorities`（`backend/main.py:7495-7525`）。必须新增 `GlobalCatalogPublisher/RuntimeCatalogSnapshotProvider` owner：Manager publish 成功后构建并 CAS 发布完整 generation；每个 fresh Run 从 provider 冻结当前 snapshot；运行中的 attempt 保留旧 generation。Settings 成功必须等生产 ingress 对新 generation 的 exact manifest/content 解析证明，不要求 backend 重启。
6. Skill metadata 与 Skill eligibility 也要分层：所有 Session 可发现 user-global Skill descriptor；若其 `allowed_tools` 在当前 Session 因 workspace/platform/health 不可用，describe/invoke 返回结构化 eligibility，而不是隐藏 Skill 或宣称可成功执行。

## 目标 owner 与依赖方向

```text
Session create UI / empty-chat new_session
  -> SessionCreationService
  -> HostDocumentsResolver + DefaultWorkspaceAllocationSaga(allocation_id)
  -> recoverable mkdir + automatic-workspace Project + immutable SessionProjectBinding
  -> creation/allocation receipt (no nested public transactions)

Settings / chat skill_install
  -> GlobalSkillInstallService v2 (trusted global owner/principal, no project visibility key)
  -> BoundedGitHubSkillSource + validator
  -> CapabilityPackManager atomic publish/bind(scope=user)
  -> global catalog generation
  -> next fresh Run in any Session resolves exact Skill version

GlobalCatalogPublisher -> RuntimeCatalogSnapshotProvider(current generation)
SDK fresh Run
  -> full descriptor catalog (including unavailable reasons)
  -> callable ToolSpec registry for eligible handlers only
  -> per-call authorization/health/platform/workspace admission
```

## 数据与迁移边界

- `HostDocumentsResolver` 是唯一 Documents 路径 owner，使用 OS-known-folder API/平台 adapter，不在 domain 内拼 `~/Documents`；目录缺失、只读、iCloud/重定向或 canonicalization 失败均在创建 Session 前结构化失败。
- allocation ID 由 durable request identity 派生并预分配 Session UUID；子目录使用安全短标题 + stable suffix，canonical identity 冻结 device/inode。saga 状态至少覆盖 `reserved → directory_created → project_committed → session_committed → completed/compensation_required`，并处理并发同 request、lost ACK 和 crash replay。
- 补偿只能删除由该 receipt 创建、identity 未变且保持空目录的目标；用户写入、symlink 替换或 identity 漂移时保留目录并记录 orphan recovery，绝不递归删除。
- automatic workspace Project 增加 `origin=automatic_session_workspace`/展示类型。Runtime resolution 仍是 project-bound，但 Session catalog/UI 继续归为“普通会话”；用户显式 Project 仍单独分组。
- 旧 projectless Session 在 schema migration 后保留普通会话分类，并在首次 fresh Run 前通过同一 allocation saga 一对一获得默认 workspace；迁移未完成不得启动需要 workspace 的 Run。
- Skill install schema 新增 v2 user-global intent/receipt/handoff；旧 v1 终态通过显式 migration operation 重新发布 user binding并退休 Project binding，非终态标记 superseded，downgrade 明确拒绝新 schema。
- `authorization_policy_state` 增加 provenance。fresh 为 auto；`legacy_import`/`user_explicit` 保持；只有可证明的 `factory_default` 才自动迁移。Settings CAS 写入 `user_explicit`。

## 体检与已知技术债

- `ProjectSkillInstallService` 名称和 v1 哈希合同把调用 workspace 与安装可见 scope 耦合，是本需求的结构性阻塞；应新增 v2 owner/contract，不堆 project_id 空字符串特例。
- scope precedence 会让遗留 Project binding 覆盖 user binding；Skill project binding 必须显式退休，不能只新增 user binding。
- `filter_sdk_catalog_for_workspace` 把“不可发现”和“不可执行”混为一层；要新增 descriptor catalog 与 eligibility，但 Provider callable schema 仍只包含可执行 ToolSpec。
- `SessionCreationService` 目前事务只覆盖 DB，默认目录是新的外部副作用；必须有 crash/replay 协议，不能简单 `mkdir` 后插表。
- Settings 自动模式 UI 的 localStorage 只是启动占位，不是 authority；默认值应与 SQLite fresh state 一致并继续以后端响应覆盖。

## 自动模式统一 policy 矩阵

| 类别 | auto 模式行为 | authority |
|---|---|---|
| auto-eligible 且 exact resource selector/task grant 可生成 | 自动生成有界 grant 后执行 | `AuthorizationPolicy` + `PreparedAuthorizationRuntime` |
| `confirm_only_names` / Skill install publish / 不可逆外部动作 | 必须等待 exact 用户 decision，auto 不放行 | frozen PreparedToolSet + explicit-only decision fences |
| deny rule / scope expansion 不可安全派生 | 拒绝或等待用户扩大 scope | policy rule + TaskGrant derivation |
| unhealthy / platform unavailable / workspace unscoped | 不执行，返回结构化不可用原因 | descriptor eligibility + physical executor admission |
| legacy direct handler | 只能镜像 SQLite policy，不得比 prepared runtime 更宽松 | legacy `PermissionGate` compatibility mirror |

## README 与索引

- 根 README 已通过 `ARCHITECTURE/index.md` 提供架构入口，无需为 Phase 0 重复增加链接。
- 最终通过测试后更新 `ARCHITECTURE/ARCHITECTURE.md`、`ARCHITECTURE/AGENT_HARNESS.md`、`ARCHITECTURE/UI.md`、`ARCHITECTURE/PROJECT_STATUS.md` 与 `ARCHITECTURE/index.md` 顶部当前事实。
