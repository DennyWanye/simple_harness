# 裁决 F-Z1c：读闸门的绑定提案不该死在 invocation origin 上

- 日期：2026-09-09
- 事件：HM-TO-A6 短段 12a · 第 6 轮（15:50）
- 证据：`.local-test-evidence/2026-09-09/native-a6-run12a/primary-ui-c2x5xy9i/native.log`
  中 5 条 `workspace_read_denied run=… tool=read_file
  reason=workspace_binding_invocation_origin_stale binding_proposal=-`（Run
  `product-sdk-5721913010…` 3 条、`product-sdk-51c212ee4c…` 2 条）；同目录
  `userdata/data/state.db` 与 `execution-v6.sqlite3`
- 代码：`backend/deskpet/task_scope/runtime_binding_authority.py`、
  `backend/deskpet/sdk_adapters/read_gate.py`
- 测试：`backend/tests/task_scope/test_runtime_binding_authority.py`（+2 例，共 5）、
  `backend/tests/sdk_adapters/test_read_binding_proposal_f_z1b.py`（+2 例，共 13）
- 前置：[DECISION-F-Z1-READ-TOOL-CALL-GATE](DECISION-F-Z1-READ-TOOL-CALL-GATE.md)（合并 `3f30a17b`）、
  [DECISION-F-Z1B-READ-BINDING-PROPOSAL](DECISION-F-Z1B-READ-BINDING-PROPOSAL.md)（合并 `e6696fd6`）

---

## 一、现象：F-Z1b 的出口在生产上一次都没走通

F-Z1b 给读侧接上了 S4 绑定提案通道，预期路径是：路径越界 → Host 算候选根 → 提案 →
auto 自动授权 → `read_workspace_binding_revised` → 模型再路由一次 → 读通过。

12a 第 6 轮实际发生的是：三次 `read_file
~/SimpleHarnessWorkSpace/a6-fixture/qiufen-checklist-a.md` **全部**以
`workspace_binding_invocation_origin_stale` 被拒，而且 `binding_proposal=-`——提案根本没
产生。这个码不是读闸门的码，是 S4 权威 `runtime_binding_authority.py:494` 抛出来的，被
`_propose_binding` 的 `except WorkspaceBindingError` 原样透传成了模型可见的原因码。

证据逐行：

| 事实 | 取证 |
|---|---|
| 5 条读拒绝，全是同一个码，全无提案引用 | `host_pre_admission_audit` 里 5 行 `workspace_read.read_file.workspace_binding_invocation_origin_stale`（尾部没有 `@<ref>`） |
| 任务已经被路由绑住 | `context_route_decisions`：scope `239f50cd-11f2-524b-aea1-1697a546ed2c` 上 1 条 `create_new` + 5 条 `continue_active`，`binding_set_revision=1`、回执 `61bc0a98-b4be-5c90-9205-8bf8545c1379` |
| 绑定一版没动 | `task_workspace_binding_heads.current_revision=1`；`task_workspace_binding_roots` 只有托管家目录一条（`first_binding_set_revision=1`），`a6-fixture` 从未入库 |
| **前台 Run 行的 `task_scope_id` 恒为 NULL** | `foreground_runs` 8 行，`task_scope_id IS NULL` 8 行（`binding_set_revision` 也全是 0） |
| 该 Run 没有产生任何读效应 | `execution-v6.sqlite3` 里这个 Run 只有一个 `context_route`（`continue_active`）效应 |

## 二、根因：两个「合法状态」在同一条路径上打架

### 2.1 `context_route` 从不回填 Run 行的 `task_scope_id`

`foreground_runs.task_scope_id` 只有一个来源：入队 turn 自带的 `scope_ref`
（`foreground_queue.py` 的 `claim_next` 从 candidate 里抄过来）。A6 旅程的每一轮都是普通对话
入队（无 `scope_ref`），任务是模型在 Run **里面**用 `context_route` 绑的，而路由裁决只写
`context_route_decisions` / `task_scope_run_watermarks`，不回写 Run 行。所以
`create_new` / `continue_active` / `resume_existing` 之后，Run 行照旧是 NULL——上表第四行的
8/8 就是这个。

`_append_auto` 用的正是这个字段分叉：

```python
if current.task_scope_id is None:
    target = _PrimaryBindingTarget(..., origin, ...)
    self._verify_primary_target(current, target)
```

于是**生产上的每一次绑定追加**（包括 F-Z1b 读闸门的候选根提案）都走 `_PrimaryBindingTarget`
这条本来为 create_new 首绑准备的分支。

### 2.2 `_verify_primary_target` 把「没捕获 origin」当成「origin 过期」

`_foreground_invocation_origin` 只在 `ProductEffectExecutor.execute` 里、
`_foreground_admission.authorize()` 之后、真正派发物理效应那一段被 `set`。而 F-Z1b 的读闸门
`self._read_gate.verify(...)` 跑在**这一段之前**（`tools.py` 里读闸门在 `origin = await
self._foreground_admission.authorize(...)` 上面），所以闸门里
`active_product_foreground_origin()` 必然是 `None`。

`_append_auto` 顶层一直承认这个状态：

```python
if origin is not None:
    self._verify_invocation_origin(current, origin)
```

但紧接着的 `_verify_primary_target` **无条件**再复核一次，而
`_verify_invocation_origin` 的第一个条件就是 `origin is None → raise`。两处口径不一致，
`None` 被判成「过期」。

合起来：**任务未挂在 Run 行上（2.1）× 读闸门跑在 origin 捕获之前（2.2）＝ F-Z1b 的提案在
任何一行提案落库之前就死了**，模型只拿到一个它无从理解的 S4 码；`read_gate_public_message`
对未知码回落 `READ_ROUTE_MESSAGE`（「先调 context_route 再重试」），模型照做、再读、再死，
于是日志里连着三条一模一样的拒绝。

同一根因也卡住 Host API：只要有一个未挂 scope 的前台 Run 在跑，UI 侧的
`binding.append` 同样会被判 origin 过期。

## 三、裁决

### 3.1 origin 检查区分「缺席」与「不符」

新增 `_verify_captured_invocation_origin(current, origin)`：**捕获到的 origin 一律逐字段复核
（`host_run_id` / `sdk_run_id` / `owner_id` / `generation` 四项全等，且 `current` 不得为
None）；没捕获到就直接返回**。`_append_auto` 顶层与 `_verify_primary_target` 都改调它，两处
口径从此是同一条。`_verify_invocation_origin` 本身一字未松（`origin is None` 仍然抛），它现在
只被「确实带着一个 origin」的调用者触达。

这道检查保护的是什么，钉清楚：它是**把一次已捕获的 dispatch 身份钉死在当前前台租约上**的
检查，防的是两件事——① 租约被 reclaim 之后，老 worker 拿着旧 `generation` 继续绑
（`test_create_new_cannot_borrow_reclaimed_generation`，两个时点各一例）；② 一次**已完成**的
effect 的 contextvars 上下文被继承后，回落到「没有 Run」的 pre-admission bootstrap
（`test_completed_invocation_cannot_fall_back_to_bootstrap`）。这两例都带着非空 origin，
修复后一字不动、照旧红→拒。

「没捕获 origin」不是这两件事里的任何一件：它意味着调用者根本不在派发段内（Host API 的
`binding.append`、F-Z1b 的读闸门）。这类调用者仍要过 `_verify_primary_target` 余下的全部证明，
一条不放宽：Run 归属（`host_run_id` / `subject`）、Run 未挂 scope、有 sdk 绑定、状态在
`CLAIMED/RUNNING`、租约未过期、以及 `human_memory_evidence` 里那条 `host-binding-append:<key>`
的 durable 提案证据必须与 `task_scopes` / `foreground_runs` / `foreground_run_sdk_bindings`
三表 join 得上且 payload 逐字段相等。落库时 store 的 `_verify_current_run_authority` 还会在写
锁内再验一遍。

### 3.2 不改的东西

- **不动 `tools.py` 的门序**（把 `authorize()` 提到读闸门之前会让「闸门已拒的读」也去消费一次
  前台准入，且租约过期时的表面错误从「读被拒」变成 `ForegroundQueueError` 抛出，属另一个
  改动的责任面）。
- **不给 Run 行回填 `task_scope_id`**：那是 S4 Task 2 的 append-only 语义（路由只在下一轮
  刷新），本轮不碰。
- **不把 `_PrimaryBindingTarget` 分支换成「读路由回执」判据**：那会让 S4 权威去读 v45 路由
  账本，是新的耦合面；F-Z1c 的最小正确修复就在 origin 检查本身。

### 3.3 顺手修掉的确定性缺陷：主根不能按哈希排

`WorkspaceBindingSetReceipt.root_identity_hashes` 是 `tuple(sorted(...))`——一个**按哈希排序的
集合摘要**，不是历史顺序。`bound_context` 直接按它的顺序收集根，于是
`BoundReadContext.primary_root`（`roots[0]`）取决于哪个 SHA 恰好排前面：F-Z1b 追加第二条根之
后，「主根」有约一半概率翻成刚提案的那个目录，而主根正是相对路径解析与
`read_binding_candidate_root(primary_root=...)` 的基准。既有用例
`test_multi_root_reads_run_in_the_root_that_contains_the_path` 因此是**掷硬币**式的
（同一份代码连跑四次：通过、通过、失败、失败——tmp 目录名进了路径、进了哈希）。

改为按 `task_workspace_binding_roots.first_binding_set_revision` 的**落库追加顺序**排
（`_ordered_root_hashes`），revision 1 的托管家目录永远是主根。排序只是呈现，不是权威：每条根
仍逐条走 `verify_effect_authority` 对着回执所指的那一版核验；查询失败就回落回执自身的顺序。

## 四、测试

`backend/tests/task_scope/test_runtime_binding_authority.py`（5 例，+2）：

1. `test_auto_binding_from_unscoped_run_without_captured_origin`——**红→绿的复现**。按证据形状
   造：入队**不带 `scope_ref`** 的普通轮 → prepare → claim → preparation/intent/observation/
   `bind_sdk_run` 走完真实 Run 生命周期 → 断言 `current_snapshot().task_scope_id is None` →
   无 origin 的 `binding.append`。`main` 上得到
   `{'code': 'workspace_binding_invocation_origin_stale'}`，修复后 `status=bound`、
   revision 1、`_primary_target` 归位。
2. `test_auto_binding_still_refuses_a_foreign_invocation_origin`——负例。把
   `_foreground_invocation_origin` 设成 `generation+1` 的伪 origin，仍是
   `workspace_binding_invocation_origin_stale`，`task_workspace_binding_revisions` 零行。

`backend/tests/sdk_adapters/test_read_binding_proposal_f_z1b.py`（13 例，+2）：

3. `test_live_unscoped_run_reads_through_the_proposal_path`——**事故形状的复现**。在 F-Z1b 原有
   基座上补上生产里唯一缺的那个要件：一条真实的、`task_scope_id` 为 NULL 的在跑前台 Run。
   `main` 上闸门返回 `workspace_binding_invocation_origin_stale`（日志逐字复现事故那一行），
   修复后是 `read_workspace_binding_revised`，head 1→2、新根＝`a6-fixture`、回执带提案引用；
   再路由一次后同一条路径放行。
4. `test_live_run_with_a_foreign_origin_is_still_refused`——读闸门带伪 origin 提案，仍拒，
   根集合不变。

回归（单进程、点名文件）：
`test_read_binding_proposal_f_z1b.py` + `test_read_tool_call_gate_f_z1.py` +
`tests/task_scope/test_runtime_binding_authority.py` 共 **33 passed**（F-Z1b 连跑三次稳定，
3.3 之前是随机红）；
`tests/execution/test_primary_create_new_runtime.py` + `tests/task_scope/test_workspace_bindings.py`
+ `tests/task_scope/test_manual_binding_display_seam.py` 共 **31 passed**；
`tests/sdk_adapters/test_context_route_authority.py` + `test_context_route_tool.py` +
`tests/task_scope/test_projections_search.py` 共 **57 passed**。

## 五、遗留

- **未知 S4 码对模型的文案**：`_propose_binding` 把 S4 权威的码原样透传，
  `read_gate_public_message` 对不认识的码回落成「先 `context_route` 再重试」，这正是事故里
  三次同样重试的直接原因。本轮不改（改了会掩盖下一个同类根因：一个陌生码应当**显眼**）。
  若后续再出现「模型对着一个不可能自愈的码重试」，再考虑给未识别码一条「停下来并说明」的
  文案（与 `read_workspace_root_unavailable` 同调）。
- **读闸门仍然没有 origin**：本轮承认它没有，而不是给它一个。若将来要求读也在捕获的
  dispatch 身份下提案，正确做法是在 `ProductEffectExecutor.execute` 里把
  `authorize()`/`origin` 的捕获提到读闸门之前（并接受它带来的门序变化），那是 `tools.py`
  的改动面。
- `foreground_runs.task_scope_id` 与路由裁决长期不一致这件事本身没有解决——本轮只是让绑定
  权威不再依赖它来判断「这是不是首绑」。
