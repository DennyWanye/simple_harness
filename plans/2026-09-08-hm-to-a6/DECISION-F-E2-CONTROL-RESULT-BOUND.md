# 决策备忘：同一 Run 内 CONTROL 工具结果的有界化（followup F-E2）

> 日期：2026-09-09
> 义务：`HM-TO-A6`（AC = HM-AC-6）
> 前置：`DECISION-SAME-RUN-CONTEXT-BOUND.md`（Incident E，本备忘是它 §8 的 followup **F-E2**）
> 相关：`DECISION-SCOPE-SOURCE-MISMATCH.md`（Incident R）、`DECISION-CONTESTED-DISCLOSURE.md`（事件 O）、
> `DECISION-TOKEN-ESTIMATOR.md`（Incident N）
> Host 工作树 `worktree-f-e2`（基线 `main` = `3b4b343a`）
> 本备忘由执行代理自行裁决并记录，未向用户提问。

---

## 1. 事故与原始数字（全部来自证据，不是推断）

证据（只读，本轮只取副本，含 `-wal/-shm`）：
`.local-test-evidence/2026-09-09/native-a6-run8/primary-ui-a_tg7ppv/`
（`native.log`、`userdata/data/state.db`、`userdata/data/simple-harness-sdk/execution-v6.sqlite3`）。

模型 `deepseek-v4-flash`，`model_overrides.toml` 把窗口钉在 **32000** →
`budget_tier = 8192`，`effective_input_budget = 32000 − 2048 − 3200 = **26752**`。

失败 Run：`product-sdk-28e8fd37c5bfe57aaa084a1a88b4608a6feba1e0d8c956f8649ec5b3fc74eeab`
（对话第 11 轮，`runs.state = failed`，19:49:20.977Z 起、19:50:01.084Z 终）。

### 1.1 唯一一条预算日志

`native.log` 全文 1498 行里 `deskpet.sdk_adapters.context_authority` 只打了 **1 行**：

```
sdk_context_budget_exceeded planned=27015 effective=26752 protected=7771
  tool_schemas=5898 groups=1 ratio=1.65 protected_messages=1873
  open_group=19244 full_trim=True
```

算式自洽：`protected 7771 = protected_messages 1873 + tool_schemas 5898`，
`planned 27015 = 7771 + open_group 19244`，**超出 263 token**。
`groups=1` + `full_trim=True` ⇒ 事件 O 的有序降级已经走完全程：
可分页的体全部强制分页、历史裁到零、只剩那个永不裁剪的 open 组。

### 1.2 逐轮回执（`state.db.run_context_snapshot_receipts`）

失败 Run 只有 **9 行**回执（第 10 轮从未产出请求，`context.prepare` 先失败）：

| turn | planned | headroom | causal_groups | groups_trimmed_for_budget | pages_forced | current_tool_pages | current_tool_tokens |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 19303 | 7449 | 3 | 0 | 0 | 0 | 0 |
| 2 | 25960 | 792 | 2 | 1 | 0 | 0 | 0 |
| 3 | 26180 | 572 | 2 | 1 | 0 | 0 | 0 |
| 4 | 17968 | 8784 | 1 | 2 | 0 | 0 | 0 |
| 5 | 19694 | 7058 | 1 | 2 | 2 | 2 | 1006 |
| 6 | 20524 | 6228 | 1 | 2 | 1 | 3 | 1509 |
| 7 | 22448 | 4304 | 1 | 2 | 1 | 3 | 1509 |
| 8 | 24344 | 2408 | 1 | 2 | 1 | 3 | 1509 |
| 9 | 25224 | 1528 | 1 | 2 | 1 | 4 | 2016 |
| 10 | 27015 | **−263** | 1 | full_trim | — | — | — |

**从第 4 轮起 `causal_groups` 恒为 1**：两个可裁组早已用光，历史这条杠杆此后一直是空的。
headroom 此后单调衰减 8784 → 7058 → 6228 → 4304 → 2408 → 1528 → 负。

### 1.3 最后一次真实请求（provider turn 9）的成分

`request_json` 共 **66 198 B**：messages 51 724 B（22 条）+ tools 14 394 B（12 个 schema，
估算计 **5898 token**）。12 条 `role=tool` 共 **41 078 B**，占 messages 的 79.4%：

| 工具 | 条数 | content 字节 | 是否 CONTROL |
|---|---:|---:|---|
| `task_scope_search` | 1 | **12 389** | 是 |
| `context_route` | 3 | **11 573**（6977 / 4265 / 331） | 是 |
| `context_page_in` | 4 | **9 060**（2287 / 2269 / 2256 / 2248） | 是 |
| `tool_search` | 4 | 8 056（2027 / 1993 / 2009 / 2027） | **否** |
| 合计 | 12 | 41 078 | CONTROL 占 **33 022 B / 80.4%** |

### 1.4 对委派方原始判断的三处订正（必须记录）

1. **`tool_search` 不是 CONTROL 工具，也没有"从不分页"。**
   `execution/current_tool_pages.CONTROL_TOOLS` 只含
   `{context_route, context_page_in, task_scope_search, task_scope_update}`；
   `tool_catalog/providers._CONTROL_TOOLS` 是**另一套东西**（派发 kind），
   两者无交集语义，`tool_search` 都不在其中。
   证据侧更直接：turn 9 的 4 条 `tool_search` 消息**全部带
   `metadata.source = "primary_settled_effect_v1"`**——它们已经是 Incident E 的分页摘要
   （回执 `current_tool_pages = 4`、`current_tool_tokens = 2016`）。
   也就是说 **非 CONTROL 这条杠杆在事故当时已经拉到底**：
   摘要固定成本约 1.9 KB/条（F-E3），而 `tool_search` 原体本就约 2 KB，
   `replace()` 的「摘要不比原体小就不换」规则让它们再也压不动。
2. **`context_route` 是 3 条 11 573 B，不是 2 条 6559+3987。**
   `task_scope_search` 是 12 389 B（不是 11 915）。
3. **委派方说的「后续完成轮 provider 报 ~29171 prompt token」在证据里不存在。**
   该 Run 全部 9 次调用的 `usage.input_tokens` 为
   13076 / 20010 / 20670 / 16348 / 18022 / 18881 / 21256 / 23290 / **24142**，最大 24142。

### 1.5 一个必须诚实说出来的副产物：这次 raise 很可能是误报

turn 8、turn 9 的「Host 估算 / provider 实计」稳定在 **+4.5%**
（24344/23290、25224/24142）。按同一比例外推 turn 10 的 `planned = 27015`，
真实 prompt 约 **25 857**，比 `effective 26752` 还**低约 900**。
即：那次 fail-closed 很可能是估算器保守导致的**假阳性**，而不是真的溢出。

**本轮不修估算器**——那是 Incident N / F-TOK-6 的地界，`ratio` 与 `schema_ratio` 的重拟合
另有 gate。但这件事改变了 F-E2 的定位：它不是"救命"，而是
**把同 Run CONTROL 累积从"无界"变成"有界"，顺带给估算器的保守留出余量**。

---

## 2. 根因

Incident E 的结论是「历史有界 ≠ 一个 Run 的 Provider Context 有界」，并给非 CONTROL
结果加了累积上界。F-E2 是它明确留下的缺口：

- 当前 Run 自己的 tool 结果全部落在**唯一那个 open 因果组**里，`trim_causal_groups`
  永不裁 open 组；
- `CurrentToolProjector` 对 CONTROL 结果有**三道**独立防护
  （`source_content` 的 `source_not_pageable`、`settled` 过滤、projector 的逐条 `continue`），
  刻意让它们保留完整字节；
- 于是 CONTROL 结果在单 Run 内**线性累积且完全没有上界**：
  一条 12 389 B 的 `task_scope_search` 从 turn 2 一直原样驻留到 turn 9（8 个轮次、
  占 messages 30%），`context_route` 累到 11 573 B，`context_page_in` 每翻一页再加 ~2.3 KB。
- `context_page_in` 这一支还有个反常回路：`tool_search` 原体被摘要成 ~2 KB 页引用后，
  模型用 `context_page_in` 把它换回来，换回来的结果**又是一条 ~2.3 KB 的 CONTROL 结果**
  ——同一份 payload 在 Context 里占了**两次**（turn 6→8 `context_page_in` 从 0 → 4556 → 9060 B）。

---

## 3. 契约边界（哪些不能动）

沿用 Incident E §2，并新增一条本轮亲自核实的：

1. **SDK 冻结**、`RunContextSnapshot` / `provider_request_fingerprint` 必须可复算。
2. **`source_revisions` 不进指纹**，加字段安全。
3. **因果链不可撕开**：`role=tool` 必须保留 `name`/`call_id`，不得产生孤立 tool 消息（A6-4）。
4. `primary_history.py` 不改；持久主对话记录读的是 **SDK Context**
   （`composition.py:861` `SqliteContextPort(...).load(...).messages`），
   **不是**投影后的请求 —— 所以投影只影响出站请求，不污染归档。
5. **【本轮新增，最关键的一条】`context_route` 的结果体会被从出站请求里"再读回来"。**
   `sdk_adapters/typed_context_use.py::TypedContextUseAuthority._occurrences` 扫描
   `role == "tool" and name == "context_route"` 的消息，`json.loads(message.content)`，
   取 `value["context_route_receipt"]`，并要求解析出的体
   **逐字段等于该 effect 的 public result**（`typed_use_tool_result_differs`），
   才会产出 typed recall intent / occurrence 绑定。
   它被 `snapshot_intents`（`prepare_snapshot` 内）与 `consumed_occurrences`
   （交付前 `check_runtime_dependencies`）两处调用。
   把一条 `context_route` 结果换成摘要/存根，**不会 fail closed，而是被静默 `continue` 跳过**
   —— 那一轮的 typed context-use 凭证就凭空消失了。这是不可接受的。
   ⇒ **`context_route` 永不参与本方案。**

（已对整个 backend grep 过 `role.value == "tool"` / 按工具名读 body 的消费者：
生产代码只有 `current_tool_pages.py`、`composition.py::project_primary_transcript`
（喂的是**原始** messages）、以及上面这一处 `typed_context_use.py`。
`primary_message_v3` 的 scope-source 判据、`read_primary_tool_causal_sources`、
终局归档全部走 DB/账本，不读请求消息体。）

---

## 4. 候选方案与取舍

| 方案 | 内容 | 评估 |
|---|---|---|
| (a) 家族取代压缩 | 同家族出现更新的一次调用后（第二次 `context_route`、`task_scope_search` 之后跟了 `context_route`），旧结果体换成存根 | 「更新的一次取代旧的一次」是一句**关于权威的语义主张**，而 Host 无权做：两次 `task_scope_search` 可能是不同 query，两次 `context_route` 可能路由到不同 scope（Incident R 里第二次路由的 verdict 就是 `rejected`）。而且它对 `context_page_in`（4 条同族且都"当前"）几乎没有约束力。**否决** |
| (b) 只压 `context_page_in` | page-in 结果按 page id 可重取，旧的换成"重新翻页"存根 | 方向对（§2 的反常回路正是它），但只覆盖 9 060 B / 33 022 B，压不住最大的那条 12 389 B `task_scope_search`。**不足以单独成方案**，其可取部分被 (c) 完全覆盖（重取提示按家族给） |
| (c) 每 Run CONTROL 字节份额 + 最旧优先存根 | 与 Incident E 的非 CONTROL 上界**同构**：超过份额后，从最旧开始把可存根的 CONTROL 体换成固定大小的公开"省略回执" | 零新语义主张、零家族特判、与已过评审的 E 方案同一形状同一算术；确定性、可复算；**采用** |

**决定：采用 (c)**，并把 (b) 的重取提示作为存根内的一个**冻结的、按家族查表的**字段带上。

### 4.1 关键取舍：这是「省略」，不是「摘要」，更不是「分页」

Incident E 的载体是 `primary_settled_effect_v1` 页引用。**CONTROL 结果绝不能用它**：
一个页引用同时是一张**准入票**——`admitted_current_page` 会凭它让 `context_page_in`
把源体**逐字节读回来**。把可执行凭证变成可再服务的对象，是安全面的扩大。

所以本方案的载体是新的 `primary_control_result_elided_v1`：

```json
{"kind":"primary_control_result_elided_v1",
 "reason":"same_run_control_result_bound",
 "refetch":"elided; call task_scope_search again with the same arguments",
 "source":{"content_bytes":12389,"content_hash":"<sha256(公开体)>",
           "effect_id":"…","effect_version":3,"raw_call_id":"…",
           "result_hash":"<该 Run 审计 head 的 result_hash>",
           "run_id":"…","tool_name":"task_scope_search"}}
```

- **没有 excerpt、没有任何从体派生的内容**，只有两个内容地址与身份 —— 因此它
  既不能冒充凭证本身，也不会把凭证泄回去；
- 大小与被替换的体**无关**，只随标识符长度小幅浮动：实测
  `task_scope_search` **626 B / 157 tok**、`task_scope_update` 630 / 158、
  `context_page_in` 659 / 165（flash ratio 1.65 下约 259–273 estimator-token）。
  即 12 389 B 的体与 2 248 B 的体换来的是同一个量级的常数；
- 全部字段来自 public audit/effect 事实，拿着 `(run_id, effect_id)` 可**逐字节复算**；
- `verify_control_stubs()` 在出站前逐条复算，**刻意不返回准入映射**，
  也刻意与 `verify_request()` 分开——省略一条凭证绝不能让它的体变得可 `context_page_in`。

### 4.2 明确不做的事

- **不丢弃**任何 tool 消息（会产生孤立 tool 消息，违反 A6-4）；只做「体 → 省略回执」。
- **不碰 `context_route`**（§3.5）。
- **不替换最新一个 provider turn 的 CONTROL 结果 —— 即使在 `force_all` 下也不替换。**
  非 CONTROL 的体在 `force_all` 时会连最新一批一起分页（模型还能 page 回来）；
  CONTROL 体一旦省略就**没有**再取回的通道，只能重新调用。刚拿到的路由/搜索/翻页结果
  正是模型此刻据以行动的东西，省略它会让下一轮直接失序，而不只是变穷。
- **不替换非 succeeded 的已结算 effect**（没有可代表的 public body）。
- **存根不比原体小就不替换**（`return 0`），避免把小结果换大。
- **不改估算器**（§1.5，另立 gate）。

---

## 5. 实现

改动 3 个生产文件 + 1 个新测试文件。

### 5.1 `backend/deskpet/execution/current_tool_pages.py`

- `CONTROL_MARKER = "primary_control_result_elided_v1"`；
- `STUBBABLE_CONTROL_TOOLS = CONTROL_TOOLS - {"context_route"}`（附 §3.5 的完整理由与
  已 grep 过全后端的说明）；
- `CONTROL_REFETCH_HINTS`：三个家族各一句冻结的、与被省略体无关的重取指引；
- `CONTROL_RESULT_BUDGET_DIVISOR = 4`（注释里带 §1.3 的证据数字）；
- `control_stub_content(facts, *, min_bytes=0)`：只接受
  `terminal ∧ state==succeeded ∧ outcome==succeeded ∧ tool_name ∈ STUBBABLE_CONTROL_TOOLS`，
  否则抛 `primary_effect_page_control_not_stubbable`；公开体的投影与 `source_content`
  **完全一致**（同一套 `canonical_json` + `redact_credential_shapes`），
  所以下面的 transcript 逐字节比对对两条路径是同一个口径；
- `control_stub(descriptor)`：省略回执的确切字节；
- `verify_control_stubs(stack, run_id, messages)`：逐条复算，返回条数，不返回准入映射；
- `_control_tool_tokens(messages)`：统计**全部** CONTROL 体（含永不可省略的
  `context_route`）—— 被约束的量就是整个 control 质量，诚实的上界必须能显示出
  「不可省略的部分已经把份额占满」这种情况（此时循环自然耗尽候选）；
- `control_result_allowance(metadata, *, provider_turn_ordinal=0)`：与
  `current_tool_allowance` **同一套 ratio 缩放整数算术**（Incident N：统计端用未标定的
  `text_tokens`，判定端用标定后的 estimator，divisor 必须同比缩放，否则标定模型会
  「过了这道界却仍然超预算」，即在还有分页余量时就 fail closed）；
- `CurrentToolProjector.__call__`：新增 `controls` 列表参与早退判据、
  计算 `control_allowance` / `control_over_bound` 并透传；
  **早退语义不变**——本来什么都不用做的轮次仍然返回 `None`，请求字节零漂移；
- `_project`：主循环把 CONTROL 消息分流到 `control` 列表（`context_route` 直接跳过），
  在既有非 CONTROL 上界之后追加 CONTROL 省略循环：
  剔除**最新 `provider_turn_ordinal` 整批**、剔除非 succeeded，
  按**消息索引升序**从最旧开始，直到 `carried_control <= control_allowance`
  或候选耗尽；单条包 `except PrimaryContextPageUnavailable: continue`（纵深防御）。

### 5.2 `backend/deskpet/sdk_adapters/context_authority.py`

- `_current_tool_page_facts` 增加两个整数字段（**从最终 messages 本身统计**，
  记录的是"真实发出的请求"的事实，不是 projector 的自报）：
  - `control_results_stubbed`：带 `CONTROL_MARKER` 的 `role=tool` 条数；
  - `control_result_tokens`：CONTROL 体仍占的 token（`control_result_allowance` 约束的那个量）；
- `prepare_snapshot` 的降级第 1 步（事件 O 的 `force_all`）：
  「绝不倒退」判据从单轴 `current_tool_pages` 改为**双轴**
  （`current_tool_pages` 与 `control_results_stubbed` 都不减），
  并把 `control_stubs_forced` 写进 `source_revisions`。

### 5.3 `backend/deskpet/execution/primary_dependencies.py`

`check_runtime_dependencies` 在既有 `verify_request(...)` 之后追加一行
`verify_control_stubs(stack, sdk_run_id, request.messages)` ——
出站前对每一条省略回执按 public audit/effect 事实**再复算一次**，
且**独立于**页准入映射（注释写明了为什么必须独立）。

### 5.4 回执字段契约（A6-3 / A6-4 归因，供 `scripts/native/a6_verify.py` 取证）

`state.db.run_context_snapshot_receipts.source_revisions_json` 内层 `source_revisions`
在 Incident E 的基础上再加三个 int：

```json
{"…": "…", "current_tool_pages":N, "current_tool_tokens":N,
 "control_results_stubbed":N, "control_result_tokens":N, "control_stubs_forced":N}
```

- `control_results_stubbed > 0` ⇒ 本轮 CONTROL 上界确实生效；
- `control_result_tokens` ⇒ 可直接对它做单调/上限断言；
- `control_stubs_forced > 0` ⇒ 这一轮是靠降级第 1 步才活下来的；
- 三者都是 int，落在既有 `receipt_trim_stats()` 的读取形状里，**无需 schema 迁移**；
- **不进指纹**（§3.2），A6-12 的重放断言不受影响。

> 与 Incident E §4.3 同样的处置：`scripts/native/a6_verify.py` 在本工作树中仍是
> **未被 git 追踪**的文件（属另一路在飞的工作），本轮**不新建也不修改**它，
> 只在此固定字段契约。接入记为 followup（见 §9，与 F-E1 合并跟进）。

---

## 6. 测试

新增 `backend/tests/execution/test_control_result_bound.py`，**6 个用例**
（其中第 6 个是评审 MUST-FIX 的回归用例，见 §7.1）。

### 6.1 单元：省略回执本身

- `test_control_stub_is_deterministic_small_and_refuses_route_carriers`
  —— 两次构造逐字节相同；`len < 700 B`、`text_tokens < 200`；
  回执里**不含**被省略体的任何片段；
  `context_route` 抛 `control_not_stubbable`（并断言它在 `CONTROL_TOOLS` 里、
  不在 `STUBBABLE_CONTROL_TOOLS` 里）；非 succeeded 的已结算 effect 同样被拒。
- `test_verify_control_stubs_refuses_a_forged_or_foreign_notice`
  —— 正常回执通过；篡改 `content_bytes` 被拒；伪造 `run_id` 被拒。

### 6.2 端到端形状：事故本身

`test_incident_shape_fits_once_control_results_are_bounded`
用 §1.3 的**实测字节形状**重建 turn 10 的请求
（system + user + 1×`task_scope_search` 12389 + 3×`context_route` 11573
+ 5×`context_page_in` 11340 + 5×`tool_search` 10076，CONTROL 合计 **35 302 B**），
跑**真实**的 `_plan_turn_messages`、**真实**估算器、**真实**冻结预算，
`window = 32000`、`effective = 26752`、ratio 钉在 **1.65**；
system 前缀按真实估算器反算，使未加约束时正好**超出 263 token**（与事故同值）。

> 与委派方给的形状（1 + 2 + 5 + 5）相比，本用例用的是**证据里的真实条数**
> （1 + 3 + 4 + 4，再加 turn 10 自己的一批 `context_page_in` + `tool_search`），
> 见 §1.4 订正。

断言（实际取到的数字写在括号里）：

1. 未加约束：`planned = 27016 > 26752`，`budget_headroom < 0`，`causal_groups == 1`；
2. 加约束后：`planned = 19545`，`headroom = +7207`，**请求装下了**；
3. 回执记录了它：`control_results_stubbed = 5`，
   `control_result_tokens` 由 **8829 → 4301**（省下 4528 text-token = **7471 estimator-token**，
   是缺口 263 的 **28 倍**）；被省略的名字集合 ⊆ `STUBBABLE_CONTROL_TOOLS`；
4. 3 条 `context_route` 的 content **逐字节未变**；
5. 因果链完整：消息条数不变，`(role, name, call_id)` 序列逐项相等；
6. **省略回执不是页引用**：投影后的请求经 `verify_request` 得到的**页准入映射里
   不含任何被省略的 effect、也不含任何 control 描述符** —— 即"省略一条可执行凭证
   绝不会让它的体变成 `context_page_in` 可读"（评审认为最值得单独测的那条，见 §7.2）；
7. **确定性**：同一 Run 状态两次投影产出相同字节。

> 值得单独记一笔：`planned` 从 27016 降到 19545，共省 7471 estimator-token，
> 与 CONTROL 省略省下的 4528 text-token × 1.65 **完全相等**
> —— 即这一轮**没有任何非 CONTROL 体被分页**。这从行为上再次证实 §1.4①：
> 非 CONTROL 杠杆在这个形状下确实已经拉到底。

### 6.3 两条规则各自的用例

- `test_newest_provider_turn_control_results_stay_verbatim`
  —— 即使 `force_all=True`，最新一个 provider turn 的 CONTROL 结果仍逐字节保留；
  同时断言更旧的确实让位了（避免空断言）。
- `test_a_run_below_the_control_allowance_keeps_every_byte`
  —— 未越界的轮次 projector 直接不动手（`None` 或原样返回），
  且**一次 `read_primary_effect_page_facts` 都没调用**（零字节漂移 + 零审计扫描成本）。
- `test_the_protected_turn_comes_from_all_sources_not_the_elidable_ones`
  —— 评审 MUST-FIX M1 的回归用例，见 §7.1。

### 6.4 回归有效性（把修复摘掉，用例必须红）

把 `_project` 里 `for index, source in control_candidates:` 改成 `for … in []:`
（即只摘掉 CONTROL 省略循环）后：

```
FAILED test_incident_shape_fits_once_control_results_are_bounded
FAILED test_newest_provider_turn_control_results_stay_verbatim
2 failed, 3 passed        （当时 5 个用例）
```

把 §7.1 的 `newest_turn` 判据改回错误口径（`for _, source in control`）后：

```
FAILED test_the_protected_turn_comes_from_all_sources_not_the_elidable_ones
1 failed, 5 passed
```

两次装回后均全绿。**用例确实抓的是这两段代码。**

### 6.5 回归口径与结果

口径同 Incident E §9：不拿绝对失败数当结论，而是在**同一台机、同一 venv、
同一批参数**（`-p no:logging -p no:randomly`）下，对**改动后工作树**与
**`main` 的一次性 detached 工作树**（`.claude/worktrees/f-e2-base`，`3b4b343a`）
逐套件跑两遍比对。**全程未使用 `git stash`。**

每次只跑一个 pytest 进程、只点名文件，**没有**跑
`tests/sdk_adapters/test_composition.py`（两棵树上都会挂死，Incident E §9 已记录）。

| 套件 | 改动后（含评审 MUST-FIX） | `main` 同机基线 | 差异 |
|---|---|---|---|
| `tests/execution/test_control_result_bound.py`（新增） | **6 passed** | 不存在 | +6 |
| `tests/execution/test_current_tool_pages.py` | 3 passed | 3 passed | **完全一致** |
| `tests/memory/test_procedure_scope_sources.py` | 5 passed | 5 passed | **完全一致** |
| `tests/sdk_adapters` 相关子集（8 个文件：`test_context_authority_primitives` / `test_context_partitions` / `test_context_preparation` / `test_context_route_authority` / `test_token_estimator_calibration` / `test_typed_context_use_primary` / `test_s5a_acceptance_matrix` / `test_sdk_context_cutover_gates`） | 12 failed / 99 passed | 12 failed / 99 passed | **失败用例 ID 集合逐行相同**（`diff` 空） |
| `tests/execution`（全量，确定性顺序） | **40 failed / 248 passed / 15 errors** | 41 failed / 241 passed / 15 errors | **改动后的失败集合是基线失败集合的真子集** —— 没有任何一条只在改动侧失败 |

`tests/execution` 全量的 +7 passed = 6 条新用例 + 1 条**顺序相关**的既有抖动
（`test_primary_foreground_runtime.py::test_primary_none_routes_to_exact_task_and_writes_real_file`
在基线全量跑里 FAIL，但在**未改动的基线上单独跑时 2 passed**，与本改动无关）。

两条抖动都单独复验过，结论都不指向本改动：

- `test_unscoped_search_late_forget.py::test_unscoped_search_rechecks_forgotten_source_before_second_delegate[create]`
  —— 在**改动前后两棵树上单独跑都 FAIL**（各 1 failed / 2 passed，~20.8 s），
  是既有的红；它在 MUST-FIX 之前那一版的全量跑里出现在改动侧、在基线侧没出现，
  纯属全量顺序造成的位置漂移。Incident E §9 里它也被记为「仅基线侧失败」，方向正好相反
  —— 同一条抖动。
- `test_primary_foreground_runtime.py::test_primary_none_routes_to_exact_task_and_writes_real_file`
  —— 在**未改动的基线上单独跑 2 passed**，同样是顺序相关，不是本改动修好的。

sdk_adapters 侧那 12 条既有红的成因（两侧同源同码）：
`ProductRunContextAuthority() got multiple values for keyword argument 'typed_use_authority'` ×9、
`assert 'routed_standalone' == 'routed_task'` ×2、`human_memory_program_marker_invalid` ×1。

---

## 7. 独立评审（另起 agent，opus，只读，不跑 pytest）

评审覆盖五个方面：授权与凭证安全、确定性/可复算、预算算术、降级顺序、其它正确性。

### 7.1 MUST-FIX（已修，并补了能抓住它的用例）

**M1：「保护最新一批」的判据取错了统计口径，导致上界在事故那种形状下退化为空操作。**

原实现：

```python
newest_control = max((source["provider_turn_ordinal"] for _, source in control), default=0)
```

`control` 只装**可省略**的家族。于是这条规则的实际语义不是「保护模型刚拿到的那一批」，
而是「保护**最近一次恰好产出了可省略 control 结果**的轮次，无论它多陈旧」。

评审给出的失败场景直接来自事故时间线：12 389 B 的 `task_scope_search` 在 provider turn 1
产出；在 turn 6 第一条 `context_page_in` 出现之前，它是**唯一**的可省略 control 来源，
因此 `newest_control == 1`、`control_candidates` 恒为空 ——
turn 2–5 一条都省略不掉，而 `control_over_bound` 早已为真
（route 2895 + search 3098 = 5993 > allowance 4053），
`_project` 每轮照样把完整的因果/审计扫描跑一遍却毫无收益。
更糟：一个**从不调用 `context_page_in`** 的 Run（route + 一条大 `task_scope_search`，
正是 HM 最常见的形状）**永远**省略不了任何东西，`force_all` 也救不了。

**修复**（`_project`）：保护轮次从**全部** `sources` 取，不从可省略子集取。

```python
newest_turn = max((source["provider_turn_ordinal"] for source in sources), default=0)
```

原用例抓不到这个缺陷：它的 fixture 里两个口径恰好取到同一个值。
**新增** `test_the_protected_turn_comes_from_all_sources_not_the_elidable_ones`
复刻上述常见形状（turn 1 一条大 `task_scope_search` + 一条 `context_route`，
turn 2 一条 `tool_search`），断言那条唯一的、最旧的 `task_scope_search` 确实被省略，
同轮的 `context_route` 逐字节未动。**回归有效性已验证**：把判据改回
`for _, source in control` 后该用例立刻 FAIL（`len([]) == 0`），改回来 6 passed。

### 7.2 已按评审修改（不改行为）

- **R2 文档自相矛盾**：`control_result_allowance` 的 docstring 原写「两者合起来永远不会
  超过 effective 的一半」，但两个循环都不是硬 cap，而是候选耗尽即停的尽力排空
  —— 事故里最终停在 4301 > 4053 且此后**永久**如此。已改写为
  「divisor 决定排空何时**开始**，不决定它停在哪；唯一真正的天花板是
  `_plan_turn_messages` 的 fail-closed」，与同文件 `_control_tool_tokens` 的注释对齐。
- **R5 重取提示可能是假话**：`context_page_in` 的提示原写「用**本请求里仍在的**
  `primary_settled_effect_v1` 摘要的 reference_id / source_hash」——
  那条摘要是否在，由另一条上界在另一轮决定，Host 给不了这个承诺。已改为不作此断言。
- **R9 存根不是严格定长**：实测 `task_scope_search` 626 B / 157 tok、
  `task_scope_update` 630 / 158、`context_page_in` **659 / 165**，且随
  `run_id`/`effect_id`/`raw_call_id` 长度浮动。已在本备忘中改写为**区间**
  （§4.1 与下文）。行为上无害：`elide()` 本来就拒绝应用不比原体小的存根。
- **R8 用例硬伤**：删掉恒真断言 `assert … not in body or True`（改为真正断言
  `content_hash` 在、体的内容不在）与一行死代码；
  **补上评审认为最值得测的那条**：断言投影后的请求经 `verify_request` 得到的**页准入映射
  里不含任何 control 描述符**（即"省略凭证绝不会变成可 page 的对象"这条核心安全主张）。
- **小项**：`CONTROL_REFETCH_HINTS` 由普通 `dict` 改为 `MappingProxyType`，与"冻结"的说法一致。

### 7.3 评审判定 OK 的项（附它复核到的事实）

- **`context_route` 的豁免既必要又充分。**
  *必要*：`typed_context_use.py:167` 按 `name == "context_route"` 选，
  `:170-175` 取 `wrapped.get("value")`——存根上是 `None`，于是 `continue`，
  **静默丢弃**该 occurrence；`snapshot_intents` 与 `consumed_occurrences` 由于读同一份
  messages 而彼此自洽，所以**不会 fail closed**，凭证就这么凭空消失。
  *充分*：评审把整个 backend 扫了一遍「从请求里读 tool 结果**体**」的消费者，完整集合是
  `_occurrences`（已豁免）、`verify_request`（按 `metadata.source == MARKER` 选，
  `CONTROL_MARKER` 永远进不了准入映射）、`admitted_current_page`（走 `verify_request`）、
  `provider.py::_wire_messages`（只用 `call_id`/`name`）、
  三个 request-guard（整请求自比对哈希）、`_plan_turn_messages` 的 protected 前缀扫描。
  其余全部读账本：`read_run_dependencies` 经 `primary_effect_identities` +
  `read_primary_dependency_facts` 解析 `task_scope_search` / `context_page_in`；
  `read_scope_sources_tx` 吃 `tool_causal_sources`；
  `_closure_arguments` / `procedure_runtime` 读 provider **response** 里的 tool call。
- **模型无法伪造存根**：`react_loop.py:830-837` 构造每条 tool 结果 `Message` 时
  metadata 恒为空，而 `verify_control_stubs` 与 `_current_tool_page_facts` 都按
  `metadata["source"]` 选。模型能产出正文、能产出 tool call 入参，**产不出 metadata dict**；
  一条"长得像存根"的模型正文既不被计数也不被复算。
- **v3 / A6-4 完整**：持久 transcript 来自 `SqliteContextPort.load()`，永远是完整体，
  终局归档看不到存根；`elide` 保留 `role`/`name`/`call_id`，
  assistant `tool_calls` ↔ tool 结果配对未断。
- **occurrence 绑定不受影响**：`(ordinal, sha256(message_json))` 的绑定既不改序号
  （原地替换，不增不删不重排）也不改任何 route 消息的字节。
- **可复算**：`sources` 顺序由 `sorted(providers.items())` + `enumerate(tool_calls)` 且拒绝空洞；
  `control_candidates` 按消息索引重排；`canonical_json` 排键；descriptor 每个字段都来自账本。
  `record_snapshot_receipt` 的幂等键 `(sdk_run_id, provider_turn_ordinal, payload_hash)` 成立，
  SDK 侧只校验 `str → 非负 int`。
- **预算算术正确且确实触发**：`effective_input_budget(32000) = 26752`；
  `scale = round(4 × 1.65 × 1000) = 6600`；`allowance = 4053`；
  事故 control 质量 8829 text-token > 4053；5 次省略省下 4528 text-token ≈ 7470 标定 token，
  远大于 263 的缺口。ratio 缩放方向正确。
- **无字节漂移**：`plan_recent_causal_groups` 的替换判据
  （`role == "tool" and size > 16384`）与 `__call__` 的 `large` 判据完全相同，
  所以任何 `exact` 会起作用的场合 `large` 必非空、改动前本就不会早退。

### 7.4 评审提出、本轮**不改**但如实记录的风险

- **R1（成本，最重要的遗留项）**：`control_over_bound` 依赖的量包含永不可省略的
  `context_route` 质量，所以事故之后该条件**永久为真**，昂贵路径变成常态：
  每轮进 `_project`（一次 `read_run_operation_audit(limit=4096)` + 最多 32×256 页回执）、
  每个候选一次 `read_primary_effect_page_facts`（又一次全量审计扫描）、
  `force_all` 再来一遍、出站前 `verify_control_stubs` 每条存根再来一次
  —— 约为 F-E5 已测得的 O(n²) 成本（14 轮 ≈38 s）的 **3 倍**。
  **F-E5 的 `(run_id, effect_id)` 请求内缓存从"nice-to-have"升级为前置条件**（见 §9）。
- **R3（降级顺序的单调判据近乎恒真）**：由于 forced pass 的应用集合按构造是 bounded pass 的
  超集，两个计数器天然单调，AND 规则只在 forced pass **完全没投影**时才起作用；
  此时若 `before == after == (0,0)` 会被接受并把 `exact_sources` 悄悄从 True 退回 False。
  评审逐条追过，**当前不会造成字节漂移**（理由见 §7.3 末），但这是个"偶然成立的不变量"。
  更稳的做法是直接比 `planned_input_tokens`，或让 `_projected` 自报是否真的投影过。
  本轮不改（会动到事件 O 的降级骨架），记为 followup。
- **R4**：`control_over_bound` 让一些原本早退的轮次进入 `_project`，
  而 `project_primary_transcript` 抛裸 `RuntimeError`、`read_primary_tool_causality` 可抛
  `SdkRuntimeNotReady`，`__call__` 只捕获两类 `Unavailable`。暴露面变宽但性质不新
  （事件 E 的 `over_bound` 已经有同样的敞口）。
- **R6（唯一的非 Run 状态输入）**：`control_result_allowance` → `calibration_for_model` →
  `llm.model_info.resolve` 会叠 `model_overrides.toml`。改那个文件会移动 ratio →
  移动 allowance → 改变**哪些**体被省略 → 改变 `provider_request_fingerprint`。
  这对 `current_tool_allowance` 是既有性质（Incident N），F-E2 把这个面**扩大了一倍**。
- **R7**：三个新回执字段落库了，但 `scripts/native/a6_verify.py:542-573` 与
  `scripts/benchmark/hm_benchmark.py:497-510` 只取 `current_tool_pages` / `trimmed_groups`，
  **没有任何 verifier 读得到**这份 A6-3/A6-4 归因 —— 就是 F-E1 那个缺口的重演（见 §9）。
- **R8 剩余的用例保真度**：`FakeStack` 返回常量 `result_hash`、不做审计绑定，
  所以「存根带的是该 effect 自己的公开内容地址」「`verify_control_stubs` 证明该 effect
  属于本 Run」这两条是对着一个结构上无法以那种方式失败的 fake 断言的；
  `foreign` 用例只走到便宜的 `control_stub_foreign_source` 前置检查，没进 `read_effect_facts`。
  另外缺三个用例：跨轮（投影结果不会被回灌）、>16 KiB 的 control 结果现在会被省略
  （这是**真实的行为变化**，此前这种体是裸奔的）、以及 M1/R1 那种"越界但零候选"的形状。
  记为 followup。
- **小项（已核对，不改）**：`control_stub_content` 的 `min_bytes=0` 默认让
  `control_not_large` 在两个调用点都是死检查（保留，与 `source_content` 形状一致）；
  它比 `source_content` 少一条 `effect.evidence_ref` 前置，但 `read_effect_facts:141-142`
  在结果存在时已校验 `audit_hash(evidence_ref) == head.evidence_ref_hash`，不丢东西；
  `verify_control_stubs` 里的 `json.loads` 可能抛 `JSONDecodeError` 而非
  `PrimaryContextPageUnavailable`，但 `check_runtime_dependencies` 外层
  `except Exception → PrimaryHistoryDisclosureRejected`，仍然 fail closed 且不泄漏。

### 7.5 评审对"方案本身"的结论

> 省略 control 结果是对的杠杆，豁免线也画在了对的位置 ——
> 再窄会静默打断 typed recall，再宽收益有限（route 只占本次 control 质量的 35%，
> 而它恰好是唯一被再读回来的家族）。把 `verify_control_stubs` 挡在准入映射之外是正确的判断。

---

## 8. 有界性的诚实边界

1. **这是"每条上界"，不是"总量收敛到 allowance"。** 与 Incident E §6 同一性质：
   越过份额后，每条可省略的 CONTROL 结果最多贡献 **157 text-token（≈259 estimator-token
   @1.65）**，但条数仍然线性增长。事故形状里省略完 5 条之后
   `control_result_tokens = 4301` **仍然大于 allowance 4053** ——
   因为 `context_route` 的 11 573 B（≈2894 text-token）**永远不能省略**，
   加上 5 条存根的 ~785 token 就已经压不下去了。循环耗尽候选后正常退出，
   这是设计内的形态，不是缺陷；`control_results_stubbed` 与
   `control_result_tokens` 不再同步变化正是这个状态的可观测特征。
2. **`context_route` 是硬下限。** 一个 Run 里路由调用越多，不可压缩的底座越高。
   事故 Run 三次路由已经吃掉 11.5 KB。要真正压住它，必须先改
   `typed_context_use._occurrences` 的取值来源（从请求消息体改为从账本/回执读），
   那是独立的一项授权面改动，本轮不做（见 §9 F-E2b）。
3. **省略是不可逆的（对模型而言）。** 与页引用不同，省略回执没有 `context_page_in`
   通道；模型只能按 `refetch` 重新调用，拿到的是**新的**凭证，不是原来那一份。
   这是刻意的（§4.1），但代价真实：一次 `task_scope_search` 重搜是一次真实的工具调用。
4. **本轮不解决 §1.5 的估算器假阳性。** 事故那次 raise 很可能本来就不该发生。
   F-E2 只是把 CONTROL 累积从无界变有界，顺带把余量做厚。
5. **F-E5 的 O(n²) 审计扫描成本仍在**，且本方案让它更常触发：
   每条省略都要 `read_run_operation_audit(limit=4096)` + 最多 32×256 页 projection receipt，
   而请求里保存的是省略回执、下一轮又要从原体重算一遍。
   §6.3 的"未越界不调用"用例把没必要的那部分挡住了，但越界之后仍是每轮全量重算。

---

## 9. Followups

| 编号 | 内容 |
|---|---|
| F-E1（沿用，评审 R7 加码） | `scripts/native/a6_verify.py:542-573` 与 `scripts/benchmark/hm_benchmark.py:497-510` 目前只取 `current_tool_pages` / `trimmed_groups`。要把 `control_results_stubbed` / `control_result_tokens` / `control_stubs_forced` 一并接入，否则本轮落库的 A6-3/A6-4 归因**没有任何 verifier 读得到**（§5.4、§7.4） |
| **F-E2d（评审 R1，优先级最高）** | 事故之后 `control_over_bound` 因 `context_route` 的不可省略质量而**永久为真**，昂贵路径变成常态，约为 F-E5 已测 O(n²) 成本的 3 倍。**F-E5 的请求内缓存因此从 nice-to-have 升级为前置条件**；同时应给 `_project` 加一条「越界但零候选」的早退，避免白跑审计扫描 |
| F-E2e（评审 R3） | `prepare_snapshot` 的双轴单调判据近乎恒真，且在 forced pass 完全没投影时会把 `exact_sources` 悄悄 True→False（当前不致字节漂移，但属偶然成立的不变量）。改为直接比 `planned_input_tokens`，或让 `_projected` 自报是否真的投影过。会动到事件 O 的降级骨架，单独立项 |
| F-E2f（评审 R8） | 补三个用例：跨轮（投影结果不会被回灌 Context）、>16 KiB 的 control 结果现在会被省略（真实行为变化，此前裸奔）、以及「越界但零候选」的形状；并把 `FakeStack` 换成带真实审计绑定的 stack，让「存根带的是该 effect 自己的内容地址」这条断言有可能失败 |
| **F-E2b** | 把 `typed_context_use._occurrences` 的 `context_route` 取值来源从**出站请求消息体**改为**账本/回执**（`context_route_tool_invocations` 已经有 `detail_json` 与 `invocation_hash`）。改完之后 `context_route` 才可能进入 `STUBBABLE_CONTROL_TOOLS`，§8.2 的硬下限才消失。属授权面改动，需单独裁决与 gate |
| **F-E2c** | `context_page_in` 的反常回路（§2 末）：`tool_search` 原体 → 2 KB 页摘要 → 模型 page 回来 → 又一条 2.3 KB CONTROL 结果，同一 payload 占两次。应在 page-in 成功后就地把**被 page 的那条摘要**与 page 结果合并，而不是两份都留 |
| F-E3（沿用） | 把 `excerpt` 长度纳入 descriptor，让非 CONTROL 摘要的固定成本从 ~456 降到 ~110 token。本轮证据给了它新的紧迫性：`tool_search` 原体约 2 KB，而摘要约 1.9 KB，**分页对它几乎无效** |
| F-TOK/N（沿用） | §1.5 的 +4.5% 系统性高估：turn 10 的 raise 很可能是假阳性。属 Incident N / F-TOK-6 地界，单独 gate |
| F-E5（沿用） | 每轮 O(n²) 的审计扫描；按 `(run_id, effect_id)` 做请求内缓存 |

---

## 10. 备忘存放位置的一处偏离（如实记录）

委派任务书要求把本备忘写到
`simple-harness-memory-sdk/plans/2026-09-08-hm-to-a6/DECISION-F-E2-CONTROL-RESULT-BOUND.md`
并在那个仓库单独提交。**实际不是这样做的**，理由有三：

1. 该目录在 `/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk` 中**不存在**；
2. 任务书让先读的三份前置备忘（事件 E / R / O）以及 N、A/B、C/D 等**全部**在
   **Host 仓库**的 `plans/2026-09-08-hm-to-a6/` 下 —— 把 F-E2 单独放到另一个仓库会让它与兄弟备忘脱节；
3. 任务书同时要求「不要自行合并到 main」，而在另一个仓库的 main 上单独提交与该约束相悖。

因此本备忘与代码一起提交在 **Host 工作树分支 `worktree-f-e2`** 上，
路径 `plans/2026-09-08-hm-to-a6/DECISION-F-E2-CONTROL-RESULT-BOUND.md`，与兄弟备忘同目录。
若需要移到 memory-sdk 仓库，`git mv` 即可，不存在第二份副本。

---

## 11. 边界

无 DDL、无证据改写、无 SDK 改动、无新授权面（`verify_control_stubs` 只做只读复算，
且刻意不进入页准入映射）。未触碰 `primary_history.py`、`context_partitions.py`、
桌面应用与 18120 端口。未跑 `uv sync`、未构建应用。
证据目录全程只读（`?mode=ro` 打开、副本上做统计），未写入、未长时间持锁。
