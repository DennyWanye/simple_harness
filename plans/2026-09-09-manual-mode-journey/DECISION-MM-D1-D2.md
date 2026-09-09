# DECISION MM-D1 / MM-D2：Manual 模式授权策略权威 与 目录授权卡片可见性

- 日期：2026-09-09
- 分支：`worktree-manual-auth`（不合并）
- 证据：`.local-test-evidence/2026-09-09/native-manual-run3/primary-ui-sil305gb/`
  （`native.log`、`userdata/data/state.db`、`userdata/data/sdk-product-state.db`、
  `userdata/data/workflow.db`、`userdata/data/simple-harness-sdk/execution-v6.sqlite3`、
  `manual-driver.log`、`manual-progress.jsonl`）
- 上游：`plans/2026-09-09-manual-mode-journey/00-PLAN.md`、`RUN-03-PARTIAL.md`

---

## MM-D1 结论：workflow.db 是唯一权威；sdk-product-state.db 的同名表是 DDL 残留；**驱动读错了库**

### 事实

| 库 | `authorization_policy_state` 行 | 谁写 |
|---|---|---|
| `workflow.db` | `mode=manual, generation=1, updated_at=1788909580.91, provenance=user_explicit, schema_generation=2, user_set_receipt_ref=policy-user-set:1` | 设置页勾选框 → `backend/main.py:_set_authorization_auto_mode` → `CapabilityStore.compare_and_set_policy_mode` |
| `sdk-product-state.db` | `mode=auto, generation=0, updated_at=1788909300.0, provenance=factory_default, receipt=NULL` | **无人写**。建库时 `INSERT OR IGNORE` 的种子行 |

- `CapabilityStore` 建在 `workflow_service.execution_uow` 之上（`backend/main.py:2713`），
  即 `workflow.db`。策略表 DDL 与读写全部在 `backend/deskpet/capabilities/store.py:385-400, 9224-9300`。
- `sdk-product-state.db` 的同名表来自
  `backend/deskpet/product_state/schema.py:19-35`——它把 `capabilities/store.py` 的
  `CAPABILITY_SCHEMA_SQL` **整段复用**来建 `task_grants` 等表，于是把
  `authorization_policy_state` / `authorization_policy_legacy_imports` 一并带了出来，
  连 `INSERT OR IGNORE(1,'auto',0,…,'factory_default',2)` 种子行也带出来了。
  `product_state/schema.py` 自己的 `SCHEMA_V*_PARTS` 里并没有这张表。
- 因此它**不是镜像**（没有任何写路径会更新它），也**不是合法遗留态**，
  而是一段 DDL 残留；`workflow.db` 那张表的 `authorization_policy_legacy_imports`
  里还留着 `permissions_auto_mode.json:v1 | missing` 的一次性导入回执，
  product-state 那张则是空的——再次印证只有前者在被使用。

### 影响

1. **驱动读错库（本次的直接症状）**：`scripts/native/manual_driver.sh` 的
   `POLICY_MODE/GEN/PROV/RECEIPT` 全部从 `$PRODUCT` 读，于是 run3 从 T3 到 T8
   一直记 `policy_mode=auto policy_provenance=factory_default policy_generation=0`，
   而同一时刻 Manual 行为已经生效（T4 起出现授权卡片）。**记录失真，不是产品缺陷。**
2. **一处潜在误用**：`backend/deskpet/product_state/task_grants.py:225-236`
   的 `_assert_current_policy` 在**没有注入** `policy_generation_provider` 时会回落读那张残留表。
   生产组装（`backend/main.py:8703-8711`）永远注入
   `authorization_policy.current_policy_generation`，所以生产不受影响；但
   `backend/deskpet/sdk_adapters/conformance.py:290` 与十余个既有用例走的是回落分支
   （它们都用 `policy_generation=0`，正好和种子行一致才没暴露）。一旦有人在
   `generation>0` 的真实库上走回落分支，**所有 grant 都会被判 `TaskGrantConflict`**。

### 裁定与修复

- **权威**：`workflow.db.authorization_policy_state`（`CapabilityStore`）。
  任何读者要判断 auto/manual，必须走 `CapabilityStore.get_policy_state()`
  或注入的 `policy_generation_provider`，**不得**直接读 `sdk-product-state.db`。
- **修驱动**：`scripts/native/manual_driver.sh` 新增 `WF="$DATA/workflow.db"`，
  四个 `POLICY_*` 计数器改从 `$WF` 读，并就地写下权威说明。
- **不动 schema**：删掉 product-state 的残留表要动
  `product_schema_manifest` / `semantic_fingerprint` 的迁移链，收益不抵风险；
  改为在 `product_state/task_grants.py` 就地标注"这张表不是权威、回落分支只服务夹具"。
- **不把 provider 改成必填**：会红掉 `tests/sdk_adapters/test_authorization_saga.py`
  等十余处既有用例与 `conformance.py`，与本次范围不成比例。改用回归用例把不变量钉死。

---

## MM-D2 结论：T4 的 Run 不是"应答后没恢复"，而是**绑定授权卡片从来没有被应答过**；同时确实缺一条"应答 → 重驱 Run"的通道

### T4 时间线（epoch 秒 / 本地 UTC+8；来源见每行）

| 时间 | 本地 | 事件 | 来源 |
|---|---|---|---|
| 1788909580.91 | 07:19:40 | 设置页取消勾选 → `workflow.db` 策略 `manual/gen=1/user_explicit` | `workflow.db.authorization_policy_state` |
| 1788909629.77 | 07:20:29 | T4 Run `e77c991a` 启动（`product-sdk-e49c0352…`） | `native.log foreground.runtime.bound` |
| 1788909634.35 | 07:20:34 | `context_route` 调用 #1 → REQUIRE_USER；saga `sdk-auth:0f80d9b1` + `task-grant:5cf4941b` **prepared**，弹出**工具授权卡 A**（`允许一次`，TTL=prepare+300 s） | `authorization_sagas`、`task_grants.prepared_at`、`tool_authority.py:2013` |
| 1788909680.47 | 07:21:20 | 用户点 `允许一次` → grant #1 **activated** | `task_grants.activated_at` |
| 1788909680.57 | 07:21:20 | saga #1 → `handoff_committed`，效应派发 | `authorization_sagas.updated_at` |
| 1788909680.99 | 07:21:20 | 绑定提案 `17e8c618` 落库（scope `64a5c4c9`，根 `…/task-64a5c4c9…`，inode 22848579） | `task_workspace_binding_proposals` |
| 1788909681.04 | 07:21:21 | **手动挑战 `b65f7a34` 签发**，`expires_at_millis=1788909980936`（TTL 300 s） | `task_workspace_manual_challenges` |
| 1788909681.07 | 07:21:21 | `product_tool.failed tool=context_route code=context_route_binding_authorization_required`；效应 settled（**失败终态**） | `native.log`、`context_route_tool_invocations`（`verdict=rejected`） |
| 1788909684.73 | 07:21:24 | 模型自行重试 `context_route` → saga `sdk-auth:3b217688` + `task-grant:88848457` **prepared**，弹出**第二张工具授权卡 C**（`允许一次`，TTL 至 1788909984.7）。**此卡从未 activated** | `task_grants`（`activated_at` 为 NULL）、`authorization_sagas` |
| 1788909980.94 | 07:26:20 | 挑战 `b65f7a34` **过期**，`task_workspace_manual_decisions` 仍为 **0 行** | 库中无决定行 |
| 1788909984.73 | 07:26:24 | 卡 C 的用户确认请求过期 | `expires_at=prepare+300` |
| 1788910006.41 | 07:26:46 | saga `3b217688` → `aborted`，grant #2 → `revoked` | `authorization_sagas`、`task_grants.revoked_at` |
| 1788910006.61 | 07:26:46 | Run `e77c991a` → **FAILED** | `foreground_run_heads` |

### 逐一排除任务书里列的假设

- **不是"授权种类不对"**：绑定挑战确实被签发，且卡片投影**可用**。把
  `PrimaryWorkspaceBindings._snapshot`（`backend/deskpet/memory/primary_workspace_bindings.py:66-83`）
  的整条 JOIN 原样对证据库重放（`now=1788909700000`），**命中 1 行**：
  invocation `route-invocation:…effect-646401447d…` × challenge `b65f7a34` × run `e77c991a` × generation 1。
  `human_memory_evidence` 里 `4a4ac625…` 的 `source_ref`/`primary_conversation_id`/`subject` 全部对上，
  `_item` 的每条 `require` 都能过。
- **不是"saga 绑到了别的 challenge id"**：绑定挑战与工具授权 saga 是**两条互不相交**的通道，
  前者落 `state.db`，后者落 `sdk-product-state.db`，两者本来就没有共同 id。
- **不是"TTL 到期得太快"**：挑战 TTL 就是 300 s（`runtime_binding_authority.py:218`），
  从 07:21:21 到 07:26:20，用户有整整 5 分钟。
- **不是 `can_decide=false`**：`context["mode"]` 来自 `CapabilityStore`（`main.py:3071` 的
  `policy = service_context.get("capability_store")`）= `manual` ✔；
  `canonical_workspace_root` 复核的目录 `/Users/taiwan/SimpleHarnessWorkSpace/task-64a5c4c9-…`
  至今存在且 inode 与提案里的 `object_id=22848579` 一致 ✔；`base_binding_set_revision=0`
  与 head 一致 ✔ → `state=pending, can_decide=true`。

### 根因

**RC-1（本次 Run 失败的直接原因）——同一时刻两张外观相近、语义不同、TTL 各自独立的授权面并存，用户只答了其中一条通道。**

`context_route` 效应一失败，模型立刻（+3.7 s）重发同一个工具调用，于是界面上同时存在：

- **绑定卡**：主对话底部 `<section aria-label="项目目录授权">`（`tauri-app/src/primary/PrimaryWorkspaceBindings.tsx:117-131`），
  按钮 `允许本次绑定` / `拒绝` → 写 `task_workspace_manual_decisions` → grant(`source=manual`) → `binding_revisions`；
- **工具授权卡**：SDK 的 REQUIRE_USER 弹窗，按钮 `允许一次` / `拒绝` → 写 `task_grants(source=user)`。

run3 全程 `task_grants(source=user)=94`、`authorization_sagas=112`，而
`task_workspace_manual_decisions=0`、`binding_grants(source='manual')=0`、`binding_revisions=1`（那 1 条是 T1 的 auto 绑定）。
T7 的操作记录写得更直白：`tool_allow=32 bind_allow=0`。
**整趟真人旅程里 `允许本次绑定` 一次都没有被成功点到**，用户全程只在答工具卡。
两条通道彼此不知道对方存在，也没有任何界面提示"这一步需要答两张卡、且顺序有讲究"。

**RC-2（结构性缺陷，本次未能根治）——应答绑定卡不会驱动 Run。**

`WorkspaceBindingRuntimeAuthority.decide_manual_binding`（`backend/deskpet/task_scope/runtime_binding_authority.py:230-277`）
写完 decision → grant → binding revision 就返回。全仓找不到任何路径会因此
重试那个已经失败的 `context_route` 效应、或唤醒 Run。Run 能否继续**完全取决于模型是否自愿再调一次
`context_route`**，而每次重调在 Manual 模式下又要用户在**另一个** 300 s 窗口里答一张新的工具卡。
T4 里模型确实重试了（+3.7 s），但那张卡也没被答，于是 Run 在双 TTL 都耗尽后 FAILED。

**RC-3（可见性放大器，本次已修）——挑战落库后没有任何刷新信号。**

`PrimaryWorkspaceBindings` 没有轮询，只在 `tool_result` 消息、窗口 focus、连接变化、
`human_memory_changed` 推送时重读。而全后端只有
`memory_ingestion_outbox`（job APPLIED 后）和 `primary_cognitive_controls` 会广播
`human_memory_changed`——**挑战签发与决定提交都不广播**。
首次显示只能靠"效应 settled 的 `tool_result` 恰好在挑战写库之后到达"这一顺序巧合
（本次确实成立，但没有任何用例把它钉住）；而决定提交后，除了发起决定的那一个客户端，
其他视图/其他 Run 完全不会刷新。300 s TTL 下这是个真实的失效放大器。

### 本次修复

1. **`backend/deskpet/task_scope/runtime_binding_authority.py`**：新增可选
   `display_invalidation` 构造参数与 `_notify_display()`。在**挑战签发后**、
   **allow 决定的绑定落库后**、**deny 决定落库后**各广播一次 content-free 的
   `human_memory_changed`。通知失败被吞掉（`_notify_display` 内 `except`），
   **绝不回滚已提交的挑战/决定/绑定**，也**不参与任何授权判定**。
2. **`backend/main.py`**：把 `MemoryDisplayInvalidation` 注册进 `service_context`
   （`memory_display_invalidation`），并在 `_activate_human_memory_host_ports`
   构造 `WorkspaceBindingRuntimeAuthority` 时注入。创建（lifespan:3365）早于
   激活（lifespan:5710），取值时序安全。
3. **`scripts/native/manual_driver.sh`**：MM-D1 的 `POLICY_*` 改读 `workflow.db`。
4. **`backend/deskpet/product_state/task_grants.py`**：就地标注策略权威与回落分支的适用范围。

**fail-closed 语义完全没有放宽**：未应答的挑战照旧到期，效应照旧失败，绝不执行。

### 用例

`backend/tests/task_scope/test_manual_binding_display_seam.py`（**5 例，全绿**）：

| 用例 | 钉住什么 |
|---|---|
| `test_manual_challenge_and_allow_each_publish_one_refresh_hint` | 签发 1 次、allow 决定 1 次；幂等重放不产生第二条 binding revision |
| `test_manual_deny_publishes_refresh_hint_and_appends_nothing` | deny 也广播；不追加任何根 |
| `test_refresh_hint_failure_never_undoes_the_binding_commit` | 广播抛异常时挑战/绑定照样提交（通知 fail-open，授权 fail-closed） |
| `test_unanswered_challenge_expires_fail_closed_like_run3_t4` | **T4 形状**：TTL 就是 300 s；无人应答 → 过期后 decide 失败，decisions/grants/revisions 全为 0 |
| `test_product_state_policy_row_is_a_ddl_residue_not_the_authority` | **MM-D1**：种子行恒为 `auto/0/factory_default`；注入 provider 时 `generation=1` 的 grant 被接受，回落到残留表则 `TaskGrantConflict` |

回归：`tests/task_scope/test_runtime_binding_authority.py`（5）、
`tests/permissions/test_task_grant_clock_seam.py`（7）、
`tests/execution/test_primary_workspace_binding_ui.py`（7）——**19 例全绿**。

### 明确的限制（未做）

- **RC-2 未根治**。让"应答绑定卡"确定性地重驱那个已失败的效应，需要在前台 Run 生命周期上
  新增一条 authorization wait-blocker（对照 F06 给 provider 做的
  `waiting_runs_blocked_on_provider` + `ProviderReconciliationPort`）。
  那一片代码（runtime lifecycle）本轮由 `worktree-mem-growth` 持有，
  且必须配真人旅程复验，**本轮不动**。建议的形状记在下面「后续」。
- **双卡并存未合并**。把绑定授权折叠进工具授权卡（一次点击既授工具又授绑定）会改变
  授权语义与回执血缘，属产品决策，未做。
- **`context_route` 每次重试都新建 proposal+challenge**。`PrimaryWorkspaceBindings._item`
  硬校验 `proposal.idempotency_key == f"context-route:{sdk_run_id}:{effect_id}"`，
  即"一效应一张卡"是投影的身份约束；改成跨效应复用挑战会连带改投影的整条校验链，未做。
- 本轮**没有**启动原生应用，全部结论来自 run3 落盘证据与代码静态追踪。

### 后续（建议，未实施）

- **MM-D2-R**：`decide_manual_binding` 的 allow 分支在绑定落库后，向前台 Run 发一条
  `binding_authorization_settled(scope_ref, run_id)`；`ForegroundRuntimeExecutionAuthority`
  据此把该 Run 上因 `context_route_binding_authorization_required` 失败的最后一个效应
  重新入队一次（**只重驱一次**，且仍走完整 EffectGate/授权链）。未应答/deny 时不入队。
- **MM-D2-U**：绑定挑战期间在工具授权卡上显式提示"本次还需要在下方『项目目录授权』里
  允许目录绑定"，消除两张卡的顺序歧义。

---

## 旅程驱动与计划必须改的地方

1. `scripts/native/manual_driver.sh`：`POLICY_*` 读 `workflow.db`（**本次已改**）。
2. **判据缺失，不是计数器缺失**：`manual_decisions_allow` / `binding_grants_manual`
   两个计数器 run3 全程都在记（一直是 0），但驱动与计划都没有把它们当作 T4/T7/T8 的
   **成败判据**，只看 `last_run_state` 与 `task_grants`，所以整趟旅程跑完才发现
   `允许本次绑定` 一次都没答成。这两个计数器必须升级为该轮的硬判据：
   T4/T7/T8 结束时 `manual_decisions_allow` 未增加即判该轮失败并立即停。
3. `00-PLAN.md` 的 T4/T7/T8 操作步骤要写明：**这一步会同时出现两张卡**——
   底部 `项目目录授权` 区里的 `允许本次绑定`，和 SDK 弹窗的 `允许一次`——
   **两张都要答**，且先答哪张都行但都在各自的 300 s 窗口内。
   `00-PLAN.md:192` 已经警告过两个 `拒绝` 同名，但没有警告两个"允许"分属不同通道。
4. `@ASK@` 轮的超时（360 s）**短于**两张卡串起来的实际窗口，建议提到 600 s，
   并在超时前先打一次 `task_workspace_manual_decisions` 的计数快照。
5. T4 判据补一条：`context_route_tool_invocations` 里出现
   `verdict=rejected & code=context_route_binding_authorization_required` 时，
   驱动应立刻把该行的 `challenge_ref` 打到 `manual-driver.log`，方便真人定位该点哪张卡。
