<!-- plan-status: finalized (plan-bs) -->

# Plan：以本地项目为根目录的不可变 Session

## 状态

- `plan-bs` challenge 已 `CONVERGED`（3 轮）；Ponytail minimality 建议已应用；用户已于 2026-08-25 确认行为契约与本计划。
- `BEHAVIOR_POLICY = preserve-approved`。
- 流程档位：FULL（新增持久化 schema/迁移，修改 Session 创建、共享 workspace authority 和桌面 UI）。
- 2026-08-26：TC-PS-04 已在 macOS 当前 debug `.app` 完成五个真实 `deepseek-v4-flash` root Run：终端
  cwd/读取/写入统一落在 Session Binding 根，前端、legacy、latest-Run 和模型文本冲突路径均不能改根，
  Project Rules 只从绑定根加载；目录缺失时 Provider/Tool 零副作用 fail closed，完整重启后仍回到同一根。
  TC-PS-06 又以 `project_root != execution_root` fixture 完成三个真实 root Run：左侧归组和 Inspector 保留
  Project root，终端 cwd、读取与两次写入只使用 explicit execution root，Project-only 文件相对读取失败，
  完整重启后边界不变；TC-PS-07 也已完成 missing-root、无关目录拒绝、活跃 Run 恢复和同身份 relocation
  两条 lane。其余 required 场景仍未完成，所以计划整体保持 `BLOCKED`，不得宣布 release 完成。

## 主要矛盾

决定成败的核心问题不是“侧栏显示一个路径”，而是让一个项目 Session 从创建、恢复到每个 fresh Run
都只从同一个 durable Session Project Binding 获得工作目录，同时保留无项目普通聊天，且不让 legacy
`code_sessions`、latest-Run workspace、全局 fallback 或前端字段重新成为并列 authority。

目标依赖方向必须是单向的：

```text
Project + immutable SessionProjectBinding
                    │
                    ▼
       effective execution root per fresh Run
                    │
                    ▼
  HostContext → TaskWorkContext → SDK Tool authority
                    │
                    ├── Terminal / file tools / artifact scope
                    └── Project snapshot / project rules
```

## 关联验收标准

- 覆盖 AC-1～AC-8；唯一验收事实源为 [acceptance.md](acceptance.md)。
- 用户可见语义以 [behavior-contract.md](behavior-contract.md) 为准；定稿前需用户逐行确认。
- Assurance profile 为 `standard`，见 [assurance-contract.json](assurance-contract.json)。

## 架构与外部实践调研结论

### 当前代码事实（解剖麻雀：项目 Session 的首条消息）

1. `SessionList.startNewTopic()` 只发送 `chat_v2 + new_session`，没有 Project identity；
   `backend/main.py` 经 `TaskSessionManager.resolve()` 生成 UUID，只继承 Provider/Model binding，再发布
   `session_switched/task_session_started`。
2. `SessionDB.sessions` 只有 `id/created_at/metadata`；`list_sessions_with_preview()` 从 `messages` 聚合，
   所以零消息 Session 不在列表，也没有项目字段。
3. fresh Run 启动前，`backend/main.py::_run_product_harness_chat()` 调
   `execution_uow.get_latest_session_project_context()`，从最近 `user_path` root Run 反推 workspace；没有值时
   `_issue_product_harness_host()` 和 `tool_catalog/providers.py::_execution_context()` 仍可回退全局 workspace。
4. Run 级 `TaskWorkContext` 已提供正确的 immutable freeze/CAS 基础，但其上游不是 Session binding。
5. `code_sessions` 是 retired Code Mode 的可覆盖映射，`upsert_code_session()` 会替换同一 base Session 的
   `project_root`，只能作迁移输入。
6. 前端 `SessionList` 是扁平数组；zustand 虽有历史 `project_root/project_name` 字段，但生产 list/hydration
   不填充它们；`ChatView` 也没有固定项目 Inspector。

完整当前事实已回写并通过 architecture challenger：
`ARCHITECTURE/ARCHITECTURE.md` §3.3、`ARCHITECTURE/AGENT_HARNESS.md`“当前 Session 到 workspace”与
`ARCHITECTURE/UI.md` §0.9。

### Codex 参考与本项目适配

- Codex 在 Thread 创建时把 `config.cwd` 持久化到 `ThreadPersistenceMetadata.cwd`，Thread API 也以 cwd
  过滤和恢复历史。这支持“创建时绑定工作目录”，参考：
  <https://github.com/openai/codex/blob/main/codex-rs/core/src/session/session.rs>、
  <https://github.com/openai/codex/blob/main/codex-rs/thread-store/src/types.rs>。
- Codex app-server 允许 `thread/start` 带 cwd，也允许 turn override cwd；simple_harness 不照搬后者，因为
  已批准行为要求项目 Session 不可原地换根目录。这里采用更严格的 Session binding，Run 只读取冻结值。
  参考：<https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md>。
- Codex 的 `agents_md.rs` 从 cwd 向上按 root marker（默认 `.git`）确定项目规则边界。本项目已有
  `ProjectRulesComponent`，但它只信任 Host verified workspace；因此只需更换上游 authority，不另造规则加载器。
  参考：<https://github.com/openai/codex/blob/main/codex-rs/core/src/agents_md.rs>。
- Codex Desktop 的 project/sidebar 完整 UI 不在上述开源 core 中；本计划不声称复制其闭源 UI。项目分组是
  基于用户确认的 simple_harness 产品设计。

### 选定方案与放弃方案

| 方案 | 结论 | 原因 |
|------|------|------|
| 把路径写进提示词或前端 store | 放弃 | 不能约束终端/工具，重启和并发 Run 会漂移 |
| 继续使用 latest-Run project context | 放弃 | 同 Session 后续 Run 可选别的目录，不满足不可变 Session 归属 |
| 直接复用 `code_sessions` | 放弃 | legacy key/语义错误，upsert 可改根目录，且和普通 Session/Run 生命周期脱节 |
| 给 `sessions` JSON metadata 塞路径 | 放弃 | 无 FK/去重/查询/迁移边界，`ensure_session` 对旧行不更新，仍难表达 Project→Sessions |
| `projects` + immutable `session_project_bindings` | 采用 | Project identity、路径 relocation、Session 归属和 effective execution root 各有单一职责 |

## 目标数据模型

### `projects`

```text
project_id             UUID primary key
display_name           user-editable label
canonical_root         current resolved absolute directory
root_kind              git | folder
filesystem_identity    opaque sha256(platform + st_dev + st_ino)
project_revision       monotonic integer, starts at 1
created_at/updated_at/last_opened_at
```

- `UNIQUE(filesystem_identity)` 防止 symlink/路径拼写把同一目录重复注册。
- Git 子目录默认先用有界 `git -C <selected> rev-parse --show-toplevel` 预览仓库根；用户选择
  `selected_folder` 时跳过提升。Git 命令失败只按普通目录处理，不阻断非 Git 项目。
- `filesystem_identity` 只存 opaque hash，不进入 UI/Provider/日志。它支持同一卷内 rename/move 后重新定位；
  跨卷复制/网络盘 identity 变化属于已批准 exploratory 边界，不在本 release 自动认领。

### `session_project_bindings`

```text
session_id             primary key, FK sessions(id)
project_id             FK projects(project_id)
execution_kind         project_root | explicit
execution_root         nullable; only explicit uses it
execution_identity     nullable; required and immutable for explicit
source_session_id      nullable; ordinary→project handoff provenance
handoff_json           nullable bounded structured handoff
binding_version        fixed initial version
created_at
```

- 普通项目 Session 用 `execution_kind=project_root`，effective execution root 在读取时取 Project 当前
  `canonical_root`；Project 合法 relocation 无需逐 Session 改写，因此 Session binding 仍不可变。
- `explicit` 只用于 AC-6 fixture/未来 worktree，保存独立 execution root；本次不自动发现、创建或删除 worktree。
- SQLite trigger 拒绝 binding row 的 `UPDATE/DELETE`；Session 删除沿 delivery/owner tombstone 隐藏，但保留
  binding、Provider、Memory 与创建 receipt 作为 provenance，任何产品入口都不能复活相同 Session id。Project 和
  用户文件不删除。Project relocation 只更新 `projects.canonical_root/project_revision`，要求新目录 identity 等于
  已存 identity，并以 expected revision CAS。
- 无项目会话没有 binding row；这比 nullable project row 更直接，读取结果用 typed
  `ProjectlessSession`，不得回退 latest Run 或全局 workspace。

### 创建、列表与升级辅助状态

- `session_creation_receipts(request_id PK, intent_hash, session_id UNIQUE/FK RESTRICT, result_json,
  lifecycle active|deleted, created_at, deleted_at)`：请求级幂等和 lost-ACK replay 的唯一 authority；保留到
  state.db 被用户显式整体 reset，不做 TTL 清理。
- `project_session_catalog_state(singleton, catalog_revision)`：每次会改变 catalog membership 或排序键的
  Project/Session 创建、删除、relocate、message append/clear、Project activation 的 `last_opened_at` 更新与
  backfill completion，都在拥有该变更的同一事务 bump，作为 keyset cursor 的失效 fence。
- `project_session_backfill_state(singleton, phase pending|scanning|applying|verifying|completed|failed,
  source_high_water, last_base_session_id, counters, outcome_digest, backup_path, backup_sha256, timestamps,
  error_code)`：v32 semantic backfill 的 durable startup gate；未 completed 时不开放 WebSocket/Session create。
- `WorkspaceResolution` 是 ProjectBindingService 返回的 frozen tagged in-memory value；其字段只在现有
  `SdkRunToolAuthorityV1.run_start_record()/restore_run()` 的 authority record v3 codec 中持久化一次：
  `kind/project_id/execution_kind/effective_root/project_identity/execution_identity/project_revision/binding_version`。
  它进入 authority fingerprint，但 filesystem identity 不进入 Provider/UI/普通日志；不得另建第二套 durable codec。

## 入口、持久化、信任与停止追踪点

- 用户入口：系统文件夹选择器 → 后端 path resolver/Project service → Session creation service →
  `sessions_list_response`/`session_created` → 左侧分组与右侧 Inspector。
- Trust boundary：用户选择的原始路径是不可信输入；后端 `resolve(strict=True)+is_dir+stat` 和 Git root 解析后
  才能持久化。前端传入的 `project_id/project_root` 不能直接成为工具 scope。
- Run 数据流：Session binding service 返回 typed `project/projectless/missing` → Host 冻结
  `WorkspaceResolutionV1` → TaskWorkContext/UoW → SDK Run Tool authority v3；所有 built-in/MCP wrapper 通过
  `ToolContext.run_id` 从 app-private authority getter 取执行上下文，不能把缺 metadata 解释为全局 fallback。
- Provider Context：`trusted_project_task_snapshot()` 增加 project id/name/root/execution root/binding version 和
  bounded handoff data；不把 filesystem identity、任意路径或旧 frontend state 放进 Provider messages。
- Relocation/物理效果 fence：root Run admission 以 expected Project revision CAS；存在
  `created/queued/running/waiting/cancel_requested` root Run 时拒绝 relocate；授权前和
  `ProductEffectExecutor` dispatch 前各 re-stat identity，漂移返回 `workspace_binding_stale`。本计划复用现有
  app-private authority seam，不改 Harness SDK 公共协议。
- 最大影响：一次创建/relocate 失败可见并可重试；不能发布半绑定 Session，不能让 Run 越过 effective root。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `backend/deskpet/memory/migrations/024_project_scoped_sessions_v32.sql` | state.db schema | 新建 Project/binding、creation receipt、catalog/backfill state、索引与 immutable trigger |
| `backend/deskpet/memory/migrator.py` | migration registry | 注册 v32 原子迁移 |
| `backend/deskpet/session/project_binding.py`（新） | typed domain/service | 路径规范化、Git root 预览、filesystem identity、binding resolve/relocate |
| `backend/deskpet/memory/session_db.py` | state.db repository | Project CRUD、原子 Session creation、分组列表、handoff、legacy backfill |
| `backend/llm/provider_registry.py` | Provider binding validation | 为 SessionCreationService 提供冻结 source model snapshot/复验入口 |
| `backend/deskpet/session/task_scope.py` | UUID scope decision | 保留旧 chat compatibility；不再拥有 Project 选择 |
| `backend/main.py` | WS ingress/composition | 新 project/session commands；new_session 路由；Run workspace authority cutover |
| `backend/deskpet/types/task_work_context.py` | root-local workspace | 接受 typed Session resolution，projectless 不创建默认 workspace |
| `backend/deskpet/sdk_adapters/context_preparation.py` | frozen Provider context | project snapshot 加 binding/handoff；保持 bounded/data-only |
| `backend/deskpet/tool_catalog/providers.py` | physical ToolContext | typed projectless 禁止 default workspace fallback |
| `backend/deskpet/sdk_adapters/tool_authority.py` | SDK Run tool authority | workspace resolution provenance/fingerprint；缺失目录 fail closed |
| `backend/deskpet/sdk_adapters/tools.py` | SDK ToolContext private seam | built-in/MCP 统一用 run_id 解析 app-private authority，校验 request/run identity |
| `backend/deskpet/agent/assembler/components/project_rules.py` | 规则发现 | 继续只读 verified effective execution root；补 authority provenance 测试 |
| `tauri-app/src/types/messages.ts` | WS types | Project、binding、创建/列表/inspect/relocate protocol |
| `tauri-app/src/stores/sessionsStore.ts` | frontend state | durable project/binding view models；移除历史假 project fields 的模糊语义 |
| `tauri-app/src/components/SessionList.tsx` | 左侧列表 | Project→Sessions 分组、项目内新建、无项目区、缺失状态 |
| `tauri-app/src/components/ProjectPickerDialog.tsx`（新） | 创建入口 | 目录选择、Git root preview、显式子目录模式、确认后创建 |
| `tauri-app/src/components/ProjectInspector.tsx`（新） | 右侧只读上下文 | 根目录、execution root、Git 状态、复制/打开/新建同项目/relocate |
| `tauri-app/src/views/ChatView.tsx` | 主聊天布局 | 固定 Inspector rail/窄屏只读条；projectless“在项目中继续” |
| `tauri-app/src-tauri/src/commands.rs`、`lib.rs` | native directory UI | 复用 folder picker；增加受控打开项目目录 command（若 opener 不能直接复用） |
| `verification/spikes/windows_path_identity_probe.py` | 后续 Windows probe | Project/explicit root 等价路径、rename、不同目录与可选 junction JSON oracle；不阻断本轮 macOS 验收 |
| `backend/scripts/restore_state_db_backup.py` | guarded support recovery | v32 semantic backfill 失败后的 hash/quick_check/停服恢复；不是 down-migration |
| `backend/tests/**`、`tauri-app/src/**/*.test.tsx`、`src-tauri` tests | 自动化证据 | AC/风险绑定测试 |
| `ARCHITECTURE/*.md` | 当前事实源 | 实现验收后同步生产链与状态（执行阶段完成，不在 plan-bs 提前写目标为事实） |

## Complexity inventory

| 复杂度表面 | 本次是否新增 | 理由 / AC 或 risk 绑定 |
|-----------|:---:|-----------------------|
| 新依赖 | 否 | Python stdlib `pathlib/os/subprocess/sqlite3` + 现有 Tauri dialog/opener 足够 |
| 新公共 SDK API | 否 | Host 内部 WS/domain 变化，不改 Harness SDK |
| 新持久化状态 | 是 | `projects` + immutable bindings，AC-1/2/6/7/8 |
| 新配置项/feature flag | 否 | 测试阶段验收后默认开启，不留灰度开关 |
| 新抽象层 | 是 | `ProjectBindingService` 是消除四套目录 authority 的必要边界，FAIL-AUTHORITY |
| 新后台任务 | 否 | availability/Git status 按需有界读取，不建 watcher |
| 新 UI store | 否 | 扩展现有 sessions store；不另建平行 Project zustand authority |
| 新 Context component | 否 | handoff 合并进现有 frozen project/task snapshot |
| 可复用已有实现 | `open_directory_dialog`、`TaskWorkContext`、`ProjectRulesComponent` | 目录选择与 root freeze 已存在 |
| 标准库/平台能力 | `Path.resolve`、`os.stat`、bounded `git rev-parse` | 路径 identity 与 Git root preview |

## 关键假设清单（challenge 中必须闭环）

| ID | 假设 | 静态证据 | 所需真跑证据 |
|----|------|----------|--------------|
| H-1 | `st_dev + st_ino` 在支持边界内识别 symlink/同卷 rename，并拒绝另一目录 | macOS spike 已证明 symlink/rename identity 相同 | 本轮以 macOS 真跑为 gate；Windows checked-in probe 保留到恢复 Windows 支持时执行 |
| H-2 | bounded Project/Session pages 在 500×200 fixture 下 p95 ≤200ms | 临时 SQLite 100k 完整 join+mapping p95 72.85ms；专项 keyset 51-row page p95 0.196ms | 实现后的 SQL/serialized payload/React Profiler 复测 |
| H-3 | typed projectless 能经 private run authority 到 physical ToolContext，无需改公开 SDK | contextvar + dynamic MCP wiring spike `3 passed in 0.35s` | 实现后 exhaustive 77-tool admission 与 no-global-fallback wiring tests |
| H-4 | 现有 folder picker 可为新 ProjectPicker 复用 | Rust command 与 ProjectDirectoryCard 调用链已定位 | React cancel/error test；真人 UI 留给 plan-task |

原始命令与量测见 [verification/spike-results.md](verification/spike-results.md)。原计划把 Windows probe 设为
强制 release stop gate；用户于 2026-08-25 调整范围为“Windows 暂不考虑”，因此本轮只以 macOS 证据判定，
probe 代码继续保留，未来恢复 Windows 支持时重新升为 gate。

## 任务清单（按依赖排序）

### Task 1 — 建立 Project identity、WorkspaceResolution 与 v32 schema  [覆盖 AC-1、AC-6、AC-7]

- 改动文件：新增 `backend/deskpet/session/project_binding.py`、migration 024；修改 `migrator.py`。
- 在 domain module 定义 `ProjectRecord`、`SessionProjectBinding`、
  `ProjectWorkspaceResolution(ProjectBound|Projectless|ProjectMissing)` 和稳定错误码。
- `resolve_registration(selected_path, mode)` 必须：trim → absolute/expanduser → `resolve(strict=True)` →
  directory check；`mode=git_root` 时用 argv 数组、有界 timeout、无 shell 的 Git probe，验证返回路径包含所选目录；
  `selected_folder` 跳过提升。
- 用 `os.stat` 生成不外泄的 filesystem identity hash；DB 唯一约束使重复注册返回 existing Project，而不是
  第二行。禁止按 display path 字符串去重。
- `explicit` binding 在创建时独立计算并冻结 `execution_identity`；定义 frozen tagged
  `WorkspaceResolution` 全字段，并只扩展现有 authority record v3 的序列化/恢复/指纹契约。Project 初始化
  `project_revision=1`。
- migration runner 注册 schema v32；DDL 在 runner transaction 内完成，包含 Project/binding、creation
  receipt、catalog/backfill state、索引、FK `ON DELETE RESTRICT` 与 binding `UPDATE/DELETE` trigger。
- checked-in Windows probe 用 stdlib 生成 JSON，覆盖 case/dot-dot/separator 等价、同卷 rename、不同目录和
  可选 junction；oracle 为等价/rename 相等、不同目录不等，unsupported junction 明示 SKIP。
- 验证：macOS H-1 证据；registration unit；v31→v32 DDL
  rollback 与 physical object/marker/user_version 对账。

### Task 2 — 原子 Project/Session creation 与结构化 handoff  [覆盖 AC-2、AC-3、AC-8]

- 改动文件：`session_db.py`、`provider_registry.py`、新 service；相关 backend tests。
- 增加 `SessionCreationService.create_conversation_session()` 单入口；所有入口必须带全局唯一 `request_id`，
  service 计算 canonical `intent_hash`。路径/Git 解析在锁外完成，随后严格按
  `provider_registry._mutation_lock → SessionDB._write_lock → BEGIN IMMEDIATE` 取锁。
- 事务先查 `session_creation_receipts`：同 request/hash 返回原 terminal result；同 request/异 hash 返回永久
  `request_id_conflict`。不存在时在事务内生成 UUID、复验仍持锁的 Provider incarnation/revision，并通过
  connection-taking tx helpers 一次写 `sessions`、Memory binding、delivery active epoch、immutable owner/scope、
  Provider/model/context usage、Project binding 或显式 projectless、handoff、route/outbox、receipt 及
  `catalog_revision`。不得嵌套调用已有自带 transaction 的 repository methods。
- handoff 为 deterministic bounded JSON：source sid、最近用户目标、最多 6 条 public conversation 摘要片段、
  总字符/Token 上限和 source high-water mark；不复制消息 rows、不复制 Memory、不调用 LLM、不带 tool/raw
  reasoning/凭据。Provider Context 只在目标 Session 首次 Run 冻结读取一次。
- commit 是唯一 visibility point；commit 后才发布 ACK/switch/list。commit 前异常留下零行；commit 后 ACK 丢失
  以同 request/hash 重放同一 session/result，不重复 route/outbox。Provider 更新/删除等待上述锁，不采用
  post-commit revalidate+tombstone 补偿。
- 删除在 workflow session lock 下用一个 `BEGIN IMMEDIATE`：先 bump delivery epoch、tombstone owner/route、
  写 outbox 和 receipt `lifecycle=deleted`，再进行可重启 reconcile 的 Run cancel。保留 Session、Memory、Provider、
  binding、handoff、receipt；列表只认 active delivery+owner。删除和删除 ACK 重放幂等；相同 create request 在
  删除后返回 `session_deleted`，不同 request 也不得复用旧 session id；product creation 禁止调用会清 tombstone
  的 generic `ensure_session()`。
- 旧 `chat_v2 + new_session` 兼容入口调用同一 service，默认 `project_id=None`；不再走独立 ensure+inherit 旁路。
- 验证：每个 write/commit/ACK 前后故障注入，并发 duplicate、同 request 异 intent、Provider lock race、删除
  前后 crash/ACK loss、删除后 create replay、每个 terminal state 重启；逐表证明 pre-commit 零行或 post-commit
  单一相同结果。

### Task 3 — Project/Session WS read model 与命令协议  [覆盖 AC-1、AC-2、AC-5、AC-7]

- 改动文件：`backend/main.py`、`session_db.py`、`tauri-app/src/types/messages.ts`。
- 新增命令：`project_preview_register`、`project_register`、`session_create`、`project_inspect`、
  `project_relocate`；所有 request/response 带 request id，错误使用稳定 code。
- 将旧 unbounded nested response 改为两个 revisioned keyset API：Project catalog page 默认 50/max 100；一个
  Project 或 projectless scope 的 Session page 默认 50/max 100。排序固定为 Project
  `(last_opened_at DESC, project_id ASC)`、Session
  `(activity_at=COALESCE(last_message_at,created_at) DESC, session_id ASC)`；cursor 冻结
  `schema_version/catalog_revision/scope_kind/project_id/activity_at/session_id`，revision 变化返回 `stale_cursor`。
- active/product predicate 必须在 LIMIT 前可查询：delivery 缺失或未 deleted；owner 缺失或 active；零消息行仅
  active product owner 可见，ownerless legacy 仍须有 messages；现有 task/UUID product-history classifier 迁成
  queryable bit，不能 page 后 Python 过滤。当前可见 selected Project/Session 作为不占 page limit 的 pinned
  descriptor 返回，前端按 id 去重；projection consistency 只跑 bounded page+pinned selection。
- 所有会改变 `activity_at` 或 `last_opened_at` 的写路径必须在同一 DB transaction bump catalog revision；若
  现有 message append/Project activation 不能共享该 transaction，先收敛为 connection-taking tx helper，不允许
  先写排序键再异步 bump。分页期间任何交错消息、clear 或 Project selection 使旧 cursor 返回 `stale_cursor`，
  客户端从首屏刷新而不是继续拼接。
- 桌面前后端按同一构建原子切换到 bounded page protocol，不再生成重复的旧 flat `sessions[]` 响应；仅保留
  旧入站 `new_session` 到 SessionCreationService 的兼容路由。
- Project availability 在后端按需校验；Git branch/dirty 只由 `project_inspect` 对当前 Project 有界查询，不在
  500-project list 上逐个 spawn Git。
- relocate 仅在旧 Project 缺失/用户显式触发时执行；新目录 filesystem identity 必须等于持久 identity，更新
  Project root 后 bump Project revision。不同 identity 返回 `project_identity_mismatch`。
- 验证：active/tombstoned/ownerless/zero-message/internal fixtures；cursor tie/stale/lost-ACK、pinned-outside-page
  与 dedupe；在 page 1/2 之间交错 message append、clear、Project activation，逐项断言 revision 同事务 bump、
  旧 cursor stale 且刷新后无重复/漏项；每页/Project catalog ≤128KiB、一次响应 ≤256KiB；missing root 与
  mismatch relocate。

### Task 4 — 将 fresh Run workspace authority 切到 Session Binding  [覆盖 AC-4、AC-6、AC-7]

- 改动文件：`backend/main.py`、`task_work_context.py`、`tool_catalog/providers.py`、
  `sdk_adapters/tool_authority.py`、project rules tests。
- `_run_product_harness_chat()` 在 reserve/Provider/tool catalog 前调用 binding service：
  - `ProjectBound`：校验 Project/explicit root identity，root Run admission 以 expected Project revision CAS，
    将完整 workspace resolution 字段持久化进唯一 authority record v3 codec；
  - `Projectless`：显式冻结 `workspace_root=None, source=none`；
  - `ProjectMissing`：提交稳定 `WORKSPACE_UNAVAILABLE` preflight，不启动 Provider/tool effect。
- `_issue_product_harness_host()` 接 typed resolution，删除前台新 Session 的 latest-Run 查询和全局 companion
  workspace fallback。legacy/system maintenance 如确需 fallback，必须用不同 typed source 明示，不能靠 `None` 猜。
- 所有 built-in/MCP wrapper 只用 injected private authority getter 按 `ToolContext.run_id` 解析执行上下文，并
  校验 run_id/request_id 一致；新 product path 删除 `_execution_context()` 的 global fallback。legacy-unknown 与
  projectless 是不同 tag，重启恢复也不能互转。
- 77 项 tool manifest 每项强制 `projectless_admission=safe|requires_project`，缺项使 catalog construction 失败；
  dynamic MCP 默认 requires_project。Provider snapshot/authority preparation 前冻结每 Run filtered catalog；生成式
  inventory 测试证明 projectless 不暴露 file/shell/process/artifact/project-directory/child-delegation/workspace
  discovery 工具，physical handler 再做 no-root fail-closed。
- `TaskWorkContext.workspace_root`、Host workspace/write scope、terminal cwd、file tools、artifact resolution 和
  ProjectRules verified root 必须相同并进入 authority fingerprint。
- authorization 前和 ProductEffectExecutor dispatch 前分别 re-stat Project/effective execution identity；漂移返回
  `workspace_binding_stale`。存在任一 bound root Run 处于 created/queued/running/waiting/cancel_requested 时，
  `project_relocate` 返回 `project_runs_active`；否则 identity match + expected revision CAS，只有 fresh Run 用新路径。
- `get_latest_session_project_context()` 保留只读 legacy migration/diagnostics，生产 fresh Run 不再调用。
- 验证：TO-A4/TO-R2；active Run+relocate 并发、外部 move/replace、两个 re-stat race、restart authority v3；
  构造前端/legacy/Run 冲突只接受 Session binding；exhaustive catalog 证明 projectless 不读取全局 root。

### Task 5 — 冻结 Provider 可见 Project/handoff snapshot  [覆盖 AC-3、AC-4、AC-6]

- 改动文件：`sdk_adapters/context_preparation.py`、`backend/main.py` 和对应 snapshot tests。
- 扩展 `trusted_project_task_snapshot()`：加入 `session_kind`、project id/name/root、effective execution root、
  binding/project revision、availability、可选 bounded handoff；Projectless 明确输出 `session_kind=projectless`
  且无任意默认路径。
- handoff 标记为 data-only、有 source sid/high-water；只在目标 Session 首次 Run 使用，后续 Run 靠目标自身
  conversation history。snapshot/hash/预算统计覆盖新增字段，Inspector 只展示公开元数据，不显示旧对话正文。
- ProjectRules 继续从 verified execution root 发现，不从 snapshot JSON 的显示字符串自行打开路径。
- 验证：snapshot determinism/bounds/redaction；Projectless 无路径；project/execution root 不同 fixture。

### Task 6 — 左侧 Project→Sessions 与创建体验  [覆盖 AC-1、AC-2、AC-3、AC-5]

- 改动文件：`SessionList.tsx`、新 `ProjectPickerDialog.tsx`、sessions store、App/Workbench props、React tests。
- store 只保存后端 bounded pages/cursors/pinned descriptors，不自行以路径字符串 regroup；active sid owner 仍在
  App，避免第二指针。Project groups 按需加载 Session page；复用已安装 `react-virtuoso 4.18.6` 与
  `src/code-panel/MessageStream.tsx` 的 stable-key/visible-window/test-mode 模式，只做分组数据适配，不新增依赖或
  自定义 windowing engine，mounted Session rows ≤150。
- 左侧结构：全局“新建普通会话”；每个 Project 可折叠、显示 missing badge 和“新建 Session”；底部独立
  “无项目会话”。零消息项目 Session 创建 ACK 后立即出现并切换。
- 全局“添加项目/新建项目 Session”使用现有 `open_directory_dialog`：先发 preview，展示“所选目录/检测到的
  Git 根/最终绑定路径”，默认 git root，显式 checkbox/secondary action 选择独立子目录，再 register+create。
- 普通会话“在项目中继续”走同一 picker/create，带 source sid；当前 Session 永不改绑。
- 删除/重命名保持现有语义；本 release 不增加未被 acceptance 要求的 Project hide 状态、命令或 store 分支。
- 验证：component tests 覆盖 grouped/empty/missing/duplicate ACK/stale cursor/error/reconnect/pinned dedupe；React
  Profiler 在 500×200 fixture 上 list update p95 ≤50ms 且 mounted rows ≤150；不使用路径做客户端 authority。

### Task 7 — 右侧只读 Project Inspector 与 relocate  [覆盖 AC-5、AC-6、AC-7]

- 改动文件：新 `ProjectInspector.tsx`、`ChatView.tsx`、Rust commands/lib、对应 tests。
- 宽屏在 ChatView 右侧常驻 inspector rail；窄屏改为始终可见的只读 compact bar，可展开详情，不能完全隐藏
  当前项目身份。Projectless 显示“普通会话”与“在项目中继续”。
- 显示 Project name/root/effective execution root；仅两者不同时同时列出。Git branch/dirty 按 active Project
  请求，失败显示 unavailable 而不阻断聊天。
- 动作：复制路径、在 Finder/Explorer 打开、新建同项目 Session；没有修改根目录。只有 missing 状态出现
  “重新定位同一项目”，选择后经 backend identity validation；成功刷新整个 Project group。
- native open/reveal command 只接受存在目录，错误返回 UI；不把 Project path 写日志。
- 验证：React layout/interaction、Rust command unit；missing→valid relocate 与 mismatch error 状态。

### Task 8 — crash-safe v32 semantic upgrade 与旧 authority 退休  [覆盖 AC-8]

- 改动文件：startup composition、`session_db.py`、`code_mode/state.py`、migration tests/reset script。
- 先增加 executable v31 prerequisite：`user_version=31`、durable marker=023、
  `TARGET_SCHEMA_VERSION=max(MIGRATION_STEPS)=31`、v17 canonical tables 存在且 Project tables 不存在；再注册
  024/v32，并验证 physical objects/marker/user_version 精确对账。
- 在现有 `backend/deskpet/memory/schema.py::initialize_state_db()` 启动阻塞链内增加单一
  `run_project_session_upgrade()` 函数，复用 `migrator.py::backup_db` 与迁移注册；不新增单实现 coordinator
  class/interface/factory。该函数停写、checkpoint WAL，生成 pre-v32 backup+manifest，`quick_check` 与 SHA-256
  通过后才执行 DDL；backup 保留到 semantic completion。
- DDL 创建 backfill state；冻结 code_sessions source high-water，按 `base_session_id` 稳定有界分块。每个 chunk
  在同一事务写 Project/binding/低敏 outcome/counters/cursor/catalog revision；已有 binding 永远胜出，无效/缺失/
  冲突保持 projectless，不猜 latest Run。未 completed 时 API fail closed。
- completion transaction 验证 source=count conservation、每个 frozen source 一个 outcome、无 overwrite/duplicate、
  outcome digest 后开放启动。重复 completed startup 是 no-op，不新增 Project/binding/outcome/backup/marker/counter。
- 增加 guarded restore command：要求服务停止、绝对窄路径、拒绝 symlink、expected SHA-256、`quick_check`、明确
  `--confirm RESTORE-STATE-DB`；先 quarantine 当前 DB，再原子替换并处理 WAL/SHM，复验 v31 marker/digest。
  二进制回滚必须先 restore backup，不提供 down-migration，`reset_agent_data.py` 不能当恢复手段。
- `code_sessions` 不删除，停止新 ordinary Session 写入；retired manager 仅保留旧读取/删除兼容到后续清理。
- `scripts/reset_agent_data.py` 的开发期显式 reset 清单加入新表，但不新增用户运行时 wipe。
- 验证：在 backup 前后、DDL commit 前后、scan/chunk commit 前后、completion/ACK 前后 crash inject；每次重启
  必须精确 rollback/resume/no-op。真实 v31 fixture 含 valid/missing/duplicate/pre-bound conflict/active/deleted/
  archived；两次运行逐项比对 sessions/messages/archive/title/Memory/Provider/delivery/owner/projection/ordering 与
  digest，restore 复现 byte-identical v31 snapshot。

### Task 9 — 验收、真实 UI、架构事实回写与提交态门  [覆盖 AC-1～AC-8]

- 自动化：backend migration/domain/Session creation/Run authority/context tests；frontend SessionList/Picker/
  Inspector/control WS tests；Rust directory commands；TypeScript/build；按 impact map 跑 Session/Provider/Memory/
  Harness critical+affected surface。路由/共享启动装配受影响，若 impact map 不完整则升级 full-surface smoke。
- 性能：用 500 Projects×200 Sessions 跑 bounded keyset SQL/EXPLAIN、serialized payload 与 browser React
  Profiler；backend p95 ≤200ms、catalog/page ≤128KiB、响应 ≤256KiB、list update p95 ≤50ms、mounted rows ≤150；
  任一不满足不进入真人 UI。
- 真实桌面 UI（原始证据只进 `.local-test-evidence/<date>/<run>/`）：
  1. macOS 当前源码 debug build：Git 子目录预览根、显式子目录、非 Git 注册；
  2. 项目内新建零消息 Session、发送文件读取/受控写入、重启恢复；
  3. 普通会话→在项目中继续，核对新旧 Session/handoff；
  4. project/execution root 不同 fixture 的归组与执行；
  5. 移走目录后的 missing/fail-closed、同卷 relocate 成功、无关目录拒绝；
  6. Windows current build、Project/explicit identity probe 与 windows-mcp UI 真测由用户于 2026-08-25
     明确移出本轮，保留为恢复 Windows 支持时的后续 gate。
- 每个 UI case 遵守项目手测纪律：动作前声明坐标/动作/期望，截图→真点击/输入→截图→日志判定；不得用 WS
  注入或脚本回放替代 UI。
- 功能全绿后默认开启；同次更新 `ARCHITECTURE/ARCHITECTURE.md`、`AGENT_HARNESS.md`、`UI.md`、
  `PROJECT_STATUS.md` 的当前生产事实/完成度/里程碑与最后更新日期。
- 最终 `git status --porcelain -- . ':(exclude)<run-dir>'` 为空，验证针对 HEAD；保留用户已有无关未跟踪 wheel，
  只提交本计划 scope。

## AC → Task 追溯

| AC | 实现 Task | 决定性验证 |
|----|-----------|------------|
| AC-1 | 1,3,6 | TO-A1、TO-R5 |
| AC-2 | 2,3,6 | TO-A2、TO-R1 |
| AC-3 | 2,5,6 | TO-A3 |
| AC-4 | 4,5 | TO-A4、TO-R2 |
| AC-5 | 3,6,7 | TO-A5、TO-R3 |
| AC-6 | 1,4,5,7 | TO-A6 |
| AC-7 | 1,3,4,7 | TO-A7 |
| AC-8 | 2,8 | TO-A8、TO-R4 |

## 实施停止条件

- 发现必须修改 Harness SDK 公共协议、引入跨设备 Project identity 或自动 worktree 管理才能完成 AC：停止并
  提交 scope-change proposal，不静默扩张。
- H-1 无法在目标平台提供稳定 same-volume identity：回到用户 review，选择“写项目 marker”或“取消 relocate”；
  不用目录内容猜测伪装强 identity。
- Projectless 仍能在任一新 Run/Tool seam 回退全局 workspace：AC-3/4 未完成，不得用 UI 隐藏代替修复。
- migration 不能证明消息/标题/Memory/Provider/delivery 状态不丢：停止在备份恢复状态，不进入 UI rollout。
