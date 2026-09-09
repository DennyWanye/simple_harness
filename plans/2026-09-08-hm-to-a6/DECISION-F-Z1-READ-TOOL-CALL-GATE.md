# 缺陷 F-Z1 决定备忘：读类文件工具的**调用时**工作区闸门

- 日期：2026-09-09
- 分支：`worktree-read-tools-projection`（自 `main@b76a7bd9`）
- 前置：`DECISION-Z-UNSCOPED-GUIDANCE-WRAP-UP.md` §三（F-Z1 的登记处）、
  `DECISION-STANDALONE-ROUTE-TOOL-AUTHORITY.md`（路由权威披露面）、
  S4 `S4-host-primary-taskscope.md`（TaskScope Archive / append-only binding set /
  canonical path + filesystem identity）、S5 `S5-host-context-integration.md`（route 环路）
- 证据（复述，不是转述）：
  - `.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo/`——**每一个** Run 的
    `capability_snapshot` 都没有 `read_file`；
  - `.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-j5yjctfj/`——turn 6
    `tool_activate builtin:read_file` → `workspace_unscoped`，随后 16 次 `tool_search`
    直到装配预算把整个 Run 打死。

---

## 一、契约先确认：为什么读类是 `requires_project`，写类却敢在未绑定 Run 里暴露

读了 S4/S5 切片、`tool_authority.py` 引用的设计冻结注释、事故 A/B 备忘之后，两件事是分开的：

**（1）读类 `requires_project` 的理由是「读必须收在绑定的工作区根内」，不是「读比写更私密」。**
S4 Task 2/3 把工作区根做成 append-only binding set：每个根是
`CanonicalWorkspaceRoot`（canonical path + POSIX inode 的 `filesystem_identity`），
由 Manual/Auto 授权链落库，`verify_effect_authority` 每次现场重验（`workspace_root_unavailable`
/ `workspace_root_identity_drift` / `workspace_root_not_canonical` / `workspace_root_too_broad`）。
「这个 Run 能读哪些字节」只有这一个权威来源。没有绑定根的 Run 里，一次 `read_file`
在 handler 层能读到什么？——`os_tools/read_file.py` 的越界校验写着
`if _scope_root:`，`scope_root=None` 时是 Strangler-Fig 回退**不拦**。也就是说，
在没有根的情况下暴露读类，等于把整台机器的文件系统交给模型。所以
`ProductToolRegistration.projectless_admission` 默认 `requires_project`（fail-closed），
`filter_sdk_catalog_for_workspace` 的 projectless 分支把它们裁掉。

**（2）写类（PROJECT_EFFECT）敢在未绑定 Run 里暴露，唯一的理由是「调用时还有闸门」。**
`PROJECT_EFFECT_TOOL_NAMES` 在 `SDK_TOOL_EXECUTION_POLICY_OVERRIDES` 里是
`("project_effect", "required", "required")`，于是每一次调用要连过三道：

1. **SDK react barrier**——`react_loop._preflight_tool_batch`：`route_requirement REQUIRED`
   且 `route_state is UNROUTED` → 该序号 `ROUTE_BARRIER_NOT_OBSERVED`，是**工具级**拒绝；
2. **Host `TaskExecutionEnvelope` 权威**——`task_execution.py`：PROJECT_EFFECT 而回执没有
   `task_scope_id` / binding 权威 → `TaskExecutionAuthorityError`（事故 A 的整 Run 故障就在这，
   所以事故 A/B 的修法是把路由事实发布到披露面让这个状态**不可达**，不是伪造回执）；
3. **`EffectGate`**——envelope 身份回声 → 冻结 Run 权威一致性 → v45 durable route receipt →
   S4 `verify_task_execution_envelope` → 严格 head 版本 → TaskScope 生命周期，
   二元裁决、只返回 `ToolResult.rejected`、永不抛异常。

**读类没有任何一道等价的调用时闸门**（NON_PROJECT_EFFECT、route/TaskScope 都 OPTIONAL，
`EffectGate._is_project_effect` 直接放行）。这就是事件 Z 备忘 §三第 3 条那句
「读类没有等价的调用时闸门，暴露即等于在没有工作区根的情况下读任意路径」的完整推导。

**结论：F-Z1 的正确修法只有一条**——不是放宽读类的投影豁免，而是**先补上读类缺的那道调用时闸门**，
补上之后投影豁免才是安全的。本轮就是这么做的。

---

## 二、做了什么

### 2.1 投影：`primary_route_capable` 的 projectless Run 现在也带读类四件套

`backend/deskpet/sdk_adapters/tool_authority.py` 新增与 `PROJECT_EFFECT_TOOL_NAMES` 并列的

```python
PROJECT_READ_TOOL_NAMES = ("read_file", "glob", "grep", "list_directory")
_PROJECTLESS_ROUTE_CAPABLE_TOOL_NAMES = frozenset((*PROJECT_EFFECT_TOOL_NAMES, *PROJECT_READ_TOOL_NAMES))
```

`filter_sdk_catalog_for_workspace` 的 projectless 豁免与 `prepare_run` 的
`projectless_catalog_contains_project_tool` 守卫**同时**改读这一个集合（两处必须按构造一致，
所以名字定义在 `tool_authority` 里，`read_gate` 反向 import）。

**为什么只有这四个**：它们的目标都走单一 `path` 参数，handler 都已经消费投影进来的
`write_scope_root` / `workspace`，闸门因此能同时绑定「根」和「要校验的每一个路径」。
`file_read` / `file_glob` / `file_grep` / `doc_read` 仍是 `requires_project`、仍被裁掉，
仍走 `workspace_unscoped` 指引（指引会点名上面这四个作为同类替代）。

**冻结身份语义一字未动**：读类保持 SDK 默认执行策略（NON_PROJECT_EFFECT / route OPTIONAL /
task_scope OPTIONAL），`_FrozenCapabilitySpec`、`capability_hash`、`scope_hash`、
`describe(nonce, capability_hash) -> activate` 三段哈希链全部不变。没有新增记录字段，
`SDK_TOOL_AUTHORITY_RECORD_VERSION` 不动。

### 2.2 闸门：`backend/deskpet/sdk_adapters/read_gate.py`（新）

`WorkspaceReadGate` 与 `EffectGate` 同构：二元裁决、只返回 `ToolResult.rejected`、永不抛异常、
拒绝时 `effect=None`（没有 `execution_effects` 行、没有 Host 事件）。

**适用面（关键，决定回归半径）**：只对
`isinstance(authority.task_work_context, PrimaryRunWorkContext)` 的 Run 的读类四件套生效。
这个类型正是 `ProductForegroundToolPort.freeze` 里 `candidate.task_scope_id is None`
（即 `primary_route_capable=True`）产生的那一个，`EffectGate.execution_scope` 判 primary
用的也是它。legacy Session Run、`project_bound` Run、起始就已绑定任务的 projectless Run
一律原路通过——**写侧闸门一个字节没动**。

**判据（依次，第一条不过即为原因码）**：

| 步 | 内容 | 原因码 |
| --- | --- | --- |
| 1 | `latest_route_decision_for_run(run_id, task_only=True)` 取本 Run 最近一次**绑定了任务**的 durable 路由决定（v45 `context_route_decisions`，与 `task_scope_update` 的 handler 闸门同源）。无行 / `task_scope_id` 为空 → 拒 | `read_requires_bound_workspace` |
| 2 | `read_route_receipt(run_id, receipt_id)` 取该决定的 `ContextRouteReceipt`，且 `task_scope_id` 必须与决定行一致 | `read_workspace_root_unavailable` |
| 3 | `verify_route_binding(receipt)` 取**该路由提交的那个精确 binding revision**（不读 live head：后来的 append 不得悄悄放宽一个已路由 Run 的可读范围） | `read_workspace_root_unavailable` |
| 4 | 对 receipt 的每个 `root_identity_hash` 走 `verify_effect_authority`（canonical path + 活体 inode 身份 + `workspace_root_too_broad`），要求**恰好一个**根（零/多根不隐式选，与 Run 起始冻结同口径） | `read_workspace_root_unavailable` |
| 5 | 参数里每个 `path` 在 resolve（含符号链接、`~`、相对路径按绑定根解析）之后必须在根内；`glob`/`grep` 的 `pattern` / `glob` 参数含 `..` 段即拒（实测 `Path('/tmp').rglob('../*')` 真的会走出根） | `path_outside_workspace_root` |

`direct_standalone` / `memory_standalone` 写下的决定行 `task_scope_id IS NULL`，
第 1 步就 fail closed → standalone 路由下读**照样拒**，与 PROJECT_EFFECT 同口径。

**模型可见文案**（事件 Z 的教训：拒绝语必须给这个 Run 真能执行的一步、不自指）：

- `read_requires_bound_workspace`：点名 `context_route` 的 `continue_active` /
  `resume_existing` / `create_new`，并明说「**在这同一个 Run 里**再调一次这个读工具就行——
  投影已经带着它了，缺的只是工作区绑定」，另加一句 standalone 下永远拒；
- `read_workspace_root_unavailable`：明说「对同一个任务再路由一次不会改变结果」，停手并告知用户；
- `path_outside_workspace_root`：说明父目录 / 越界绝对路径 / 指向外部的符号链接 / `..` 段一律拒，
  给出「用工作区内路径，或先 `list_directory` 工作区根」。

**放行后的分发期投影**：`read_gate` 的 `execution_scope` 把**已校验的根**写进 contextvar，
`effect_gate.project_tool_execution_context` 在没有 PROJECT_EFFECT 投影时改走它，于是
`ToolExecutionContext.workspace` / `.write_scope_root` 在这一次分发内等于绑定根。
两个后果：相对路径与不带 `path` 的 `glob`/`grep` 落在绑定根而不是后端进程 cwd；
handler 自己那道 `agent.write_scope.write_scope_check` 也随之生效，成为纵深防御第二层。
`verify` 没放行过的调用进 `execution_scope` 直接 `workspace_read_root_unverified`——
**绝不凭空造根**。

### 2.3 装配

`ProductEffectExecutor` 新增可选 `read_gate` 口（`WorkspaceReadGatePort`），
位置在 EffectGate 之后、`assert_workspace_current` 之前，沿用同一条 step-0 纪律
（`_is_first_occurrence`：已有 durable SDK 账本记录的重放不再重验）。
`main.py` 与 EffectGate 同处构造 `WorkspaceReadGate`（同样的 binding store / route ledger /
scope store / authority resolver），缺件即启动失败，不退化成「没有闸门」。

### 2.4 回执 / 审计

每一次闸门裁决（放行与拒绝都记）写一行 `host_pre_admission_audit`：
`reason_code = "workspace_read.<tool>.<code>"`（放行是 `...admitted`）+ 参数的
canonical hash。**没有路径、没有 pattern、没有文件内容**。
`payload_kind` 复用既有的 `context_route`（该表的 `payload_kind` 有 CHECK 三值约束，
新增一个值要做 CHECK 重建迁移），靠 `workspace_read.` 前缀命名空间隔离——
与 analysis 车道用 `memory.analysis.blocked%` 前缀在 `analysis_result` 下自隔离是同一手法。
读取用新增的 `workspace_read_audit_rows(db_path)`。审计写失败只记 warning：
回执是证据不是权威，丢回执不能把一次拒绝变成放行，也不能把一次放行变成 Run 故障。

### 2.5 事件 Z 拒绝文案的改口（本轮必须做）

事件 Z 的「未绑定」分支原文是「文件工具**从本会话的下一个 Run 起**可激活」。F-Z1 之后这句话
对读写两类都是错的：在这类 Run 里，`context_route` 一提交，**本 Run 内**读写就都能用了。
`_UNSCOPED_UNROUTED_ACTION` 与 `_UNSCOPED_ALTERNATIVES_ACTION` 同步改口为
「先 `context_route`，然后用本 Run 已经暴露的那些工作区文件工具——它们按调用逐次对绑定工作区把关，
路由一提交就在这同一个 Run 里开始工作」。
该分支现在只对投影里**真的没有**的能力（`doc_read` / `file_read` / MCP filesystem 等）触发。

---

## 三、明确没做的

- **不给读类加 `route_requirement=required`**。加了之后未路由的读会先被 SDK barrier 打成
  `ROUTE_BARRIER_NOT_OBSERVED`，模型看到的是 SDK 固定文案，拿不到我们这条带可执行下一步的
  `read_requires_bound_workspace`；而且那会改动冻结记录契约。Host 侧闸门更精确也更可控。
- **不动写侧闸门**（react barrier / envelope / EffectGate / 粘性 memo 一行未改），
  测试里专门钉了「读闸门对 `write_file`/`edit_file`/`run_shell`/`workspace_prepare` 一律返回 None
  且不写任何审计行」。
- **不做 schema 迁移**。见 §2.4：新 `payload_kind` 需要 CHECK 重建（v50），会牵动 7 处硬编码
  `user_version == 49` 的断言，超出本轮半径。记为残留。

---

## 四、测试

新增 `backend/tests/sdk_adapters/test_read_tool_call_gate_f_z1.py`（15 例，真部件：
真 v45 `state.db`、真 `WorkspaceBindingAuthorityStore` Manual 授权链、真
`ContextRouteLedgerStore` 决定行、真 `WorkspaceReadGate`、真 `filter_sdk_catalog_for_workspace`）：

| 组 | 例 |
| --- | --- |
| 投影 | 能路由未绑定 Run 含读类四件套且写类一条不少、`file_read` 仍被裁；起始已绑定的 projectless Run 读写都不给 |
| 适用面 | 闸门只认 `PrimaryRunWorkContext` 的读类四件套；legacy/project_bound Run 不插手 |
| 未路由 | 拒 `read_requires_bound_workspace`，文案点名 `context_route` 三种 route 且说明本 Run 内即可重试；审计行不含路径、hash 长 64 |
| 已绑定 | `create_new` / `resume_existing` / `continue_active` 三种绑定路由**各一例**，根内放行，`bound_root` 返回校验后的 canonical 根 |
| standalone | `direct_standalone` / `memory_standalone` 各一例，仍拒 |
| 越界 | 父目录 / 越界绝对路径 / 相对 `..` / 根内符号链接指向根外 / `glob` pattern 含 `..`；同时钉住根内相对路径与不带 `path` 的 `glob` 照常放行 |
| 无根 | 路由到任务但 binding receipt 取不到 → `read_workspace_root_unavailable`，文案是「停手」不是「再路由」 |
| 写侧 | 读闸门对四个 PROJECT_EFFECT 工具一律 None、零审计行 |
| 分发期 | `execution_scope` 投影已校验根到 `workspace`/`write_scope_root`，出作用域即失效；未放行的调用 `workspace_read_root_unverified` |
| 包含性 | `path_within_root` 解析符号链接、根不可解析时 fail closed |

改写 `backend/tests/sdk_adapters/test_unscoped_guidance_incident_z.py`（7 → 9 例，**契约变更**）：
原来钉「读类被裁」的那一例改钉「读类在投影内、`file_read` 仍被裁」；
原来用 `builtin:read_file` 走拒绝路径的三例改用 `builtin:file_read`（它才是现在还会被拒的读类），
断言从「下一个 Run 才生效」改为「同类替代点名 `builtin:read_file`/`builtin:list_directory` +
`context_route` + this same Run」；新增两例：`read_file` 在未路由 Run 里 describe→activate
真的成功、以及未绑定分支文案不再说 "next Run"。

结果（`backend/.venv` 的 python，一次一个 pytest 进程，`-p no:randomly`）：

| 集合 | 分支 | main@b76a7bd9 | 差 |
| --- | --- | --- | --- |
| `test_read_tool_call_gate_f_z1.py` | 15 passed | 文件不存在（红） | 新增 |
| `test_unscoped_guidance_incident_z.py` | 9 passed | 9 例里 4 例在分支语义下必须改写 | 契约变更 |
| 点名集合（tool_authority / tool_activate_unavailable_disclosure / global_descriptor_authorization / dynamic_mcp_projection） | 60 passed | 同 | — |
| `test_public_tool_projection` + `test_provider_runtime_refresh` + `sdk_adapters/test_conformance` + `test_product_host_ports` | 64 passed / 3 failed | **同 3 failed** | 先存红 |
| effect gate 四件套 + `test_primary_workspace_binding_ui` + `test_chat_session_project_effect_reachability` + `test_project_skill_run_projection` | 30 passed / 16 failed | **同 16 failed** | 先存红 |
| `tests/sdk_adapters`（排除 `test_composition.py`） | 58 failed / 735 passed | 58 failed / 718 passed | FAILED 集合**逐行相同**，通过数 +17 |
| `tests/execution` | 39 failed / 269 passed / 10 errors | 41 failed / 267 passed / 10 errors | 差集只有 2 条且方向是**由红转绿**：`test_primary_create_new_runtime.py::test_first_tool_waits_for_durable_host_started_without_provider_delay[False]`、`test_unscoped_search_late_forget.py::…[create]`。无新增红 |

基线用 `b76a7bd9` 的一次性 worktree 跑出，两侧同一个解释器、同一组参数。

---

## 五、遗留

1. **审计 `payload_kind` 复用**（§2.4）：读闸门回执现在挂在 `context_route` 下靠前缀隔离。
   要独立 `payload_kind='workspace_read'` 需要一次 CHECK 重建迁移（v50）+ 更新 7 处
   硬编码 `user_version == 49` 的断言。方向安全（不影响裁决），但读审计的人要知道这条口径。
2. **只覆盖四个读工具**：`file_read` / `file_glob` / `file_grep` / `doc_read` 仍被裁。
   若后续要纳入，逐个确认「目标参数是什么、handler 是否消费投影根」再加进
   `PROJECT_READ_TOOL_NAMES`——闸门本身不用改。
3. **根内符号链接指向根外的目录，`glob`/`grep` 的遍历结果仍可能带出根外文件**。
   这是 `project_bound` Run 早就存在的性质（handler 只校验入参路径，不逐条校验遍历结果），
   本轮没有扩大它，也没有收窄它。要收窄需要在 `glob_tool`/`grep_tool` 里对每条结果复用
   `path_within_root`，属独立改动。
4. **未跑原生旅程**（依约束不启动原生应用）。F-Z1 的真人复验就是 HM-TO-A6 第 6 轮：
   `read_file` 应当在 `context_route` 之后于**同一个 Run 内**读出
   `a6-fixture/qiufen-checklist-a.md` 全文，A6-2 的大结果分页因此才结构上可达。
5. `execution_scope` 的 `(run_id, call_id) -> root` 交接表是进程内 dict，按调用弹出；
   一个被 `verify` 放行却从未进入分发的调用（上游异常）会留下一条，直到进程结束。
   量级与 Run 内调用数同阶，不做清理；若将来要严格，可在 Run 终态时按 run_id 清一次。
