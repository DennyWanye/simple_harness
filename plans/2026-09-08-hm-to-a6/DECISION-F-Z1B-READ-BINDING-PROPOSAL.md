# 裁决 F-Z1b：读工具的越界路径改走 S4 绑定提案通道

- 日期：2026-09-09
- 事件：HM-TO-A6 第 11 次整跑 · 第 6 轮（14:05）
- 证据：`.local-test-evidence/2026-09-09/native-a6-run11/primary-ui-*/native.log` 中三条
  `workspace_read_denied run=… tool=read_file reason=path_outside_workspace_root`；
  `userdata/data/state.db` 的 `task_workspace_binding_roots` / `context_route_decisions` /
  `host_pre_admission_audit`
- 代码：`backend/deskpet/sdk_adapters/read_gate.py`、`backend/main.py`
- 测试：`backend/tests/sdk_adapters/test_read_binding_proposal_f_z1b.py`（11 例）
- 前置：[DECISION-F-Z1-READ-TOOL-CALL-GATE](DECISION-F-Z1-READ-TOOL-CALL-GATE.md)（合并 `3f30a17b`）

---

## 一、问题：F-Z1 的判据是对的，但它是一条死路

F-Z1 给读类四件套（`read_file` / `glob` / `grep` / `list_directory`）补上了调用时闸门：
只有本 Run **已落地的任务路由回执**所指的那一条已验证 S4 根之内才准读，越界一律
`path_outside_workspace_root`。判据本身没有错，fail-closed 也没有错。

错在**没有出口**。A6 旅程的样例文件在 `~/SimpleHarnessWorkSpace/a6-fixture/`，而该 Run 的任务
唯一的绑定根是它的托管家目录 `~/SimpleHarnessWorkSpace/task-<id>/`。模型被用户要求读那些
文件，拿到的是一个「拒绝 + 无可执行下一步」的组合：重试没用、换路径没用、重新路由也没用。
同样的越界如果发生在**写/效应侧**，走的是完全不同的剧本——
`task_scope/runtime_binding_authority.py:139` 的 `append_binding` 会按当前授权模式分叉：

- `manual` → `propose_manual_binding` 发出 durable 挑战 → 底部『项目目录授权 / 允许本次绑定』卡片；
- `auto` → `_append_auto` 直接落授权与绑定（2026-09-07 产品裁决：auto 模式永不打断用户）。

读侧从来没有接进这条通道。F-Z1b 就是把它接上。

---

## 二、裁决

### 2.1 候选根：模型只说文件，Host 自己算目录

模型永远不会（也不该）说出一个「根」。所以候选根由 Host 确定性地算出来，规则写死在
`read_binding_candidate_root()`：

1. 把请求路径按 `path_within_root` 同一口径解析（`~` 展开、相对路径按任务**主根**解析、
   `resolve()` 跟随符号链接）；
2. 解析后必须是**既定 workspace 根的严格后代**。等于既定根本身 → `workspace_root_too_broad`；
   不在既定根之下（含符号链接指向 workspace 之外）→ `path_outside_workspace_root`；
3. 候选根 = 解析后路径的**最近一个已存在目录祖先**（路径本身就是目录时就是它自己）。
   如果这个上溯一路走到既定 workspace 根，说明唯一可绑的祖先就是 workspace 自己
   → `workspace_root_too_broad`；
4. 候选根不得**等于或是**任务已持有的任何一条根的父目录（公共父目录会把已有根吞掉）
   → `workspace_root_too_broad`。

其余一律交给 S4 store 自己在真实文件系统上重算并复用它自己的拒绝码
（`workspace_root_symlink_or_not_directory` / `workspace_root_not_configured_descendant` /
`workspace_root_identity_drift` …），这里不新造一套判据。

> **关于「symlink 越界用哪个码」**：任务书同时写了「symlink-out 用 S4 码」与「配置 workspace
> 之外的读仍然是 `path_outside_workspace_root`」。二者在实现上冲突——因为候选根是**先解析再
> 判断**的，一条指向 workspace 之外的符号链接在进入 S4 store 之前就已经是「workspace 之外的
> 绝对路径」，S4 的 symlink 码根本没有机会触发。本轮取后者（更具体的那条）：
> **符号链接越界 = `path_outside_workspace_root`**；S4 码保留给它真正能判的两种情形
> （workspace 根本身 / 公共父目录 → `workspace_root_too_broad`）。反过来，指回 workspace
> **之内**的符号链接会解析成真实目录并正常成为候选根，这正是 MM-8 想要的效果：
> `manual-root-link` 的真实路径若已经是一条根，读直接放行、根数不变。

### 2.2 提案：复用 create_new 那条一模一样的权威

拿到候选根之后，读闸门调用的就是 `context_route` create_new 用的同一个入口——
`HumanMemoryHostService.append_binding(AppendBindingRequest(...))`。它内部按模式分叉，
所以 manual / auto 的行为与写侧**逐字节同源**，没有第二套实现：

- `auto` → `source='auto'` 的授权 + binding revision +1（不弹任何卡片）；
- `manual` → durable 提案 + 挑战，读闸门另外在 `context_route_tool_invocations` 里补一行
  与路由工具**同形**的拒绝行（`verdict='rejected'`、`detail.code =
  context_route_binding_authorization_required`、`detail.binding_challenge.challenge_ref`）。

那一行是必须的：`PrimaryWorkspaceBindings._snapshot` 的卡片查询就是按这三样东西 join 的，
并且 `_item` 还把提案的幂等键钉死成 `context-route:{sdk_run_id}:{effect_id}`。读闸门原样沿用
这两个形状，于是**卡片不需要新查询、不需要新 schema、不需要改前端**就会出现。
读闸门的拒绝码也直接沿用 `context_route_binding_authorization_required`——旅程驱动的
`print_binding_challenges` 正是按这个字符串捞 challenge_ref 的。

### 2.3 同调用还是重试：**永远不是同一次调用**（本轮钉死的规则）

任务书允许两种实现，要求先去看写侧怎么做。看过之后结论是唯一的：

- 读权威（`bound_context`）走的是路由回执**所指的那一版** binding revision，不是 live head
  ——F-Z1 的原话是「a later append cannot silently widen what an already-routed Run may read」。
  所以刚追加的新根，在旧回执下**根本看不见**。
- 写侧更严：`EffectGate` 第 5 步是 `workspace_binding_receipt_superseded`——**head 必须等于
  回执 revision**。这意味着只要绑定动了一版，本 Run 后续的一切工程效应都会被拒，直到签发
  新的 `context_route` 回执。

如果为了「同一次调用就读到」而让读绕过回执直接看 head，读是通了，**写会在模型毫无察觉的情况下
全线断掉**——A6 的剧本正是「读样例 → 写答案」，那等于把缺陷从读侧搬到写侧。

所以规则统一为：**绑定成功 ⇒ 拒绝本次读，并要求模型再调一次 `context_route`
（`continue_active`，无需其它参数）刷新回执，然后重发同一次读。** 这一次额外调用同时把写侧
的 supersede 修好了，是净收益而不是净损耗。`_continue_active` 本来就取全局 active task 的
binding **head** 签发新回执，SDK 的 react barrier 对 CONTEXT_CONTROL 工具没有 FORBIDDEN 限制，
ReAct driver 收到第二个路由回执只是替换 `state.route_receipt`——这条路径是现成的。

两条 F-Z1b 拒绝码的文案都把这一步写死：

| 码 | 触发 | 文案要点 |
|---|---|---|
| `read_workspace_binding_revised` | auto 下绑定已落地 | 目录已绑给本任务；本次读未执行；调一次 `context_route route=continue_active`，再重发这次读 |
| `context_route_binding_authorization_required` | manual 下挑战已发出 | 已请『项目目录授权』卡片；**不要循环重试**，告诉用户并等待；批准后同样是「先 `context_route`，再重发读」 |
| `workspace_root_too_broad` | workspace 根本身 / 公共父目录 | 任务只在具体项目目录里工作，永远不绑整个 workspace；请指名一个具体目录 |
| `path_outside_workspace_root` | 解析后离开既定 workspace | 与 F-Z1 一致，不变 |

### 2.4 多根：F-Z1 的「恰好一条根」必须放宽

`bound_root` 原本要求 `len(roots) == 1`，否则 `read_workspace_root_unavailable`。这条规则在
F-Z1b 之下会**把闸门自己的成功提案变成故障**：第二条根一落地，整个任务的读就全死了。
所以新增 `bound_context()` 返回 `BoundReadContext(task_scope_id, binding_set_revision, roots)`：

- 零根仍然是 `read_workspace_root_unavailable`（没有任何读权威）；
- 一根以上一律接受——读是**逐路径**的，多根完全承载得起读权威，包含性判断只需要额外回答
  「是哪一条根放行了它」，`execution_scope` 就把那一条投影进 `ToolExecutionContext.workspace`；
- 不带 `path` 的 `glob`/`grep` 落在**主根**＝回执里的第一条根（追加顺序，即 revision 1 的
  托管家目录），与 F-Z1 的语义一致。

写侧的 `effect_gate_projectless_project_effect`（零/多根冻结不承载写权威）**一字未改**——
本轮只放宽读。

### 2.5 回执：`binding_proposal_ref` 进原因码尾巴

`host_pre_admission_audit` 只有 `payload_kind`（CHECK 限死三值）、`reason_code`、
64 位 `payload_hash` 三列可用，加列要重建表迁移，本轮不取。提案引用因此挂在原因码尾部：

```
workspace_read.<tool>.<reason>[@<binding_proposal_ref>]
```

`<binding_proposal_ref>` 是挑战 id（manual）或绑定回执 id（auto），**都不是路径**，
读取用 `parse_read_audit_reason()`。F-Z1 的既有回执行为（放行也记、只记原因码与参数哈希、
写失败只记 warning 不改裁决）一字未改。

---

## 三、装配

`main.py` 的 `WorkspaceReadGate` 多注入两个 getter（与 `ContextRouteToolService` 同源）：
`human_memory_host_service_factory` 与 `human_memory_binding_append_authority`。两者都是惰性
lambda，与 EffectGate 同处构造。**没有注入时闸门退回纯 F-Z1 行为**（越界即
`path_outside_workspace_root`），这条退路有专门用例守着。

---

## 四、A6 驱动 / 计划要不要预绑 fixture 目录

**不要**。整条 F-Z1b 的意义就在于「模型撞上越界读 → Host 发起提案 → auto 自动绑 / manual 出卡片」
这条路径本身是被跑过的。预绑 `a6-fixture` 等于把这条路径从旅程里删掉，MM-5/MM-6 会退化成
INCONCLUSIVE（"模型没试"不等于"拒绝生效"，见 `plans/2026-09-09-manual-mode-journey/00-PLAN.md`
的失败判读约定）。

需要在驱动侧补的只有一件事：**A6 auto 旅程里，模型第一次读 fixture 会拿到
`read_workspace_binding_revised` 并需要再路由一次**。驱动的轮次预算与「一轮内允许的工具调用
数」要容得下这多出来的 2 次调用（`context_route` + 重发读）。Manual 旅程 T7/T8 的卡片时序不变
（挑战 TTL 仍是 300 s，仍然是「两张卡都要答」）。

---

## 五、测试

`backend/tests/sdk_adapters/test_read_binding_proposal_f_z1b.py`（11 例，真 v45 state.db +
真 `HumanMemoryHostServiceFactory` + 真 `WorkspaceBindingRuntimeAuthority` 两条通道 +
真 route ledger + 真 `WorkspaceReadGate`）：

1. auto：读 `<workspace>/a6-fixture/x.md` → `read_workspace_binding_revised`；head revision
   1→2、新根就是 fixture 目录、授权来源 `auto`、无新增 manual 挑战；回执带 `@<receipt_ref>`
   且不含路径；再路由一次后重发同一次读 → 放行，投影根＝fixture；重发不再追加根。
2. manual：→ `context_route_binding_authorization_required`；挑战 durable、绑定未动；
   `context_route_tool_invocations` 的 code / challenge_ref / 幂等键三项与卡片查询完全对齐；
   用户 allow 后 revision→2，但**不重新路由仍然拒**（钉死 2.3 的规则），重新路由后放行。
3. workspace 根本身 / 只能绑到 workspace 根的散落文件 → `workspace_root_too_broad`，根数不变。
4. 符号链接越界 / workspace 外绝对路径 / 父目录 → `path_outside_workspace_root`，根数不变、无提案。
5. auto 下模型不重新路由就连发三次同一次读 → 每次都是 `read_workspace_binding_revised`，
   binding 只前进一版、根集合不重复追加（S4 的 `workspace_binding_root_already_present` /
   `workspace_binding_base_revision_conflict` 两个冲突被归一到「先重新路由」这一条下一步）。
6. `glob` pattern 里的 `..` → 永远不进提案通道（pattern 不指名目录）。
7. 未路由的 Run → 仍是 `read_requires_bound_workspace`，不发提案。
8. 未注入提案权威的闸门 → 退回 F-Z1 行为。
9. 多根：两条根各自放行、各自投影正确；不带 `path` 的 glob 落主根。
10. 零可验证根 → 仍是 `read_workspace_root_unavailable`。
11. 候选根规则纯函数表（最近已存在目录祖先 / 不存在的深路径 / 目录本身 / 相对路径 /
    workspace 根 / 散落文件 / 公共父目录 / 已有根本身 / workspace 外 / 符号链接内外）。

回归（单进程、点名文件）：
`test_read_tool_call_gate_f_z1.py` 15 passed（F-Z1 全绿）、
`test_unscoped_guidance_incident_z.py` + `tests/task_scope/*`（7 个文件）+ F-Z1 合计 81 passed、
`tests/test_main_service_registrations.py` + `tests/sdk_adapters/test_context_route_tool.py`
45 passed。全部一次通过，没有需要与 main 对比 FAILED 集合的红。

---

## 六、遗留

- 本轮没有给 `context_route` 的重路由做「模型忘了重路由」的兜底（例如在下一次读时自动带上
  同一句提示）。目前靠拒绝文案。若 A6 实测显示模型仍会卡住，再考虑把 supersede 后的第一条
  工程效应拒绝也改写成同一句话。
- **manual 下模型不重新路由就重发读，会再发一张卡片**（每次工具调用一张，与 MM 计划 T7 的
  「列目录的工具卡可能再弹一次」一致）。挑战本身是幂等键按 effect_id 派生的，所以不会覆盖旧
  挑战，但连发多次会攒下多张待答卡。拒绝文案已明写「不要循环重试」，本轮不加 Host 侧节流。
- `read_workspace_binding_revised` 目前不区分「auto 刚绑好」与「别人刚绑好」两种 supersede
  来源。前者由本闸门产生，后者今天还到不了读侧（读侧看的是回执 revision，不是 head）。
