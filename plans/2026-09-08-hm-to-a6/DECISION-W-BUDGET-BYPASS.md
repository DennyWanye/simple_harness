# 决策备忘：超窗请求静默发出 + 回执/调用不变量（事件 W）

- 日期：2026-09-09（含同日一次只读评审的处置，见 §评审）
- 分支：`worktree-budget-bypass`（基线 `34dbc82a`，已合入 main `1e7043e9`
  ——事件 U / F-E3 / T / V；冲突只有 `a6_verify.py` 的 A6-12 段与
  `ARCHITECTURE/AGENT_HARNESS.md` 的追加位置，两侧逻辑都保留）
- 证据：`.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo/`
  （`native.log`、`userdata/data/simple-harness-sdk/execution-v6.sqlite3` 的
  `provider_invocations`、`userdata/data/state.db` 的
  `run_context_snapshot_receipts`、`a6-progress.jsonl`）。
  拟合用合池 **239 组** `(request_json, usage_json)` 真机配对：
  run9 134 + run8 87 + run6 18，全部 `deepseek-v4-flash`、窗口经全局
  `model_overrides.toml` 钉在 32000 → `budget_tier=8192`、
  `effective_input_budget=26752`。
- 相关前案：`DECISION-TOKEN-ESTIMATOR.md`（Incident N，pro 的校准三元组）、
  `DECISION-SAME-RUN-CONTEXT-BOUND.md`（Incident O，有序降级 + flash 三元组）、
  `DECISION-HISTORY-TOOL-CALL-ARGS.md` / `DECISION-TOOL-CALL-ARGUMENTS-REPLAY.md`
  （事件 K，wire 期补回 assistant `tool_calls.arguments`）、
  `DECISION-RECALL-AUTHORITY-STALE.md`（`recall_context_use_authority_stale`）。

---

## 0. 一句话

第 9 次里 **134 次 provider 调用有 37 次真实计费超过 `effective_input_budget`、
其中 24 次超过整个 32000 窗口**（峰值 **76 708** = 窗口的 2.40 倍），
而装配期的 `sdk_context_budget_exceeded` **一次都没有为它们触发**——因为闸门比的
是 Host 自己估的数，而**真正发出去的 prompt 里有两块 Host 结构上估不到的质量**。
验证器把 76 708 记进了 numbers，却因为判据排在 `timeout → INCONCLUSIVE` 之后
而从未判过它。本轮的修法是**用实测代替拟合**：在物理发出之前，用同一条 Run
上一次真实 `usage` 推出的下界拦截；并把 A6-3 的计费侧判据提到降级分支之前。

---

## 1. A6-3：静默超窗的两块隐藏质量

### 1.1 事故 Run 的逐轮账（run `product-sdk-4996b84d…`，即第 17 轮）

`wire` = Host 按 `request.messages` 文本估出的 token；`schema` = tools 数组；
`planned` = 回执里的 `planned_input_tokens`（已乘校准倍率）；
`inp` = `usage.input_tokens`，即 provider 计费的 prompt。

| ordinal | wire | schema | planned | provider `inp` | `inp`/(wire+schema) | 本轮 reasoning | 累计 reasoning | 本轮 tool_call args | 累计 args | 复算预测 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 9521 | 3574 | 20954 | 13005 | 0.99 | 5713 | 0 | 119 | 0 | 13095 |
| 2 | 9969 | 3574 | 22350 | 19646 | 1.45 | 932 | 5713 | 46 | 119 | 19375 |
| 4 | 12593 | 3574 | 26681 | 33200 | 2.05 | 2115 | 11555 | 5593 | 5788 | 33510 |
| 6 | 12434 | 3574 | 26420 | 45565 | 2.85 | 4760 | 18539 | 222 | 11694 | 46241 |
| 10 | 12355 | 3574 | 26293 | 65454 | 4.11 | 2064 | 31882 | 59 | 17743 | 65554 |
| 14 | 11654 | 3574 | 25139 | **76708** | **5.04** | 1071 | 38215 | 44 | 23758 | 77201 |

复算模型：

```
provider_input[n] ≈ wire[n] + tool_schema[n]
                    + Σ_{k<n} reasoning_tokens[k]
                    + Σ_{k<n} tool_call_arguments_tokens[k]
```

14 轮逐条误差 **≤1.6%**。也就是说 76 708 里 **61 973（80.8%）** 是这两块：

1. **中转站回灌的 `reasoning_content`（38 215）**。Incident O 判定
   「flash 几乎不产 reasoning」的依据是 run6 的 16 组（单轮最高 1813、累计 1922）；
   第 9 次同型号同中转站上，单轮最高 5713、**累计 38 215**，与 pro 同一量级。
   那条前提被证伪了。
2. **Host 自己在 wire 期补回的 assistant `tool_calls.arguments`（23 758）**。
   `sdk_adapters/provider.py::_ProductOpenAICompatibleProvider._wire_messages`
   （事件 K 的 memo 修复）在**装配 payload 时**才把 arguments 拼回去，
   而 fingerprint 与预算检查都发生在那之前——落库的 `request_json` 里
   assistant 消息只有 `{"role":"assistant","content":""}`，一个 `tool_calls`
   字段都没有。第 17 轮循环调 `task_scope_update` 的那段 CJK arguments 约
   5.6 K token，被后续**每一条**请求重新带上，到 ordinal 14 累计 23 758。

注意 `wire` 全程在 11 654~12 593 之间**几乎不动**——Host 的裁史/分页确实在起作用，
但它裁的是自己看得见的那部分；隐藏质量是中转站与 wire 期加上去的，裁不掉。

### 1.2 计费侧的全局量（run9 / 134 次调用）

| 量 | 值 |
|---|---|
| `usage.input_tokens` 峰值 | **76 708**（窗口 32000 的 2.40 倍） |
| 超过 `effective_input_budget`(26752) 的调用 | **37 / 134** |
| 超过整个窗口(32000) 的调用 | **24 / 134** |
| `planned_input_tokens` < 真实 `input_tokens` 的条数 | **59 / 134** |
| 最差 planned / 真实 | **0.3277**（25 139 对 76 708） |
| 期间触发的 `sdk_context_budget_exceeded` | 4 次（**没有一次**属于上面 37 条） |

### 1.3 现行校准三元组在合池 239 组上的表现

`deepseek-v4-flash` = `min(1.25 + 0.35·ordinal, 1.65)`，schema 跟随同一倍率。

| 证据 | n | 估算/真实 p50 | p05 | min | 低估条数 | 真超预算 | 真超窗 |
|---|---|---|---|---|---|---|---|
| run6（当初的拟合集） | 18 | 1.293 | 1.090 | 1.090 | **0** | 0 | 0 |
| run8 | 87 | 1.122 | 0.814 | 0.736 | 24 | 5 | 0 |
| run9 | 134 | 1.047 | 0.478 | 0.328 | 59 | 37 | 24 |
| **合池** | **239** | 1.116 | 0.671 | **0.328** | **83** | **42** | **24** |

**42 条真实超预算的请求，旧口径一条都没判出来（42/42 漏判）。**

---

## 2. 为什么这次**不**重拟三元组（关键决策）

按 pro/Incident P 的方法（schema 单独按 1.30，messages 按 ordinal 走）在合池上重拟，
可以做到低估 0：`min(2.00 + 0.35·ordinal, 6.90)` + schema 1.30，
42 条真超预算全部判出。**但代价不可接受**，而且不是「参数没调好」，是**模型形状不成立**。

### 2.1 同一个 ordinal 上的两个要求没有交集

对每个 ordinal，令
「要把该 ordinal 上真实超预算的请求都判出来」所需的最低倍率为 `lo`，
「不要把该 ordinal 上真实装得下的请求判成超预算」所允许的最高倍率为 `hi`：

| ordinal | `lo`（必须 ≥） | `hi`（必须 ≤） | |
|---|---|---|---|
| 2 | 1.831 | 1.685 | **无解** |
| 3 | 1.720 | 1.664 | **无解** |
| 4 | 1.716 | 1.659 | **无解** |
| 6 | 1.739 | 1.672 | **无解** |
| 7 | 2.004 | 1.681 | **无解** |
| 8 | 1.979 | 1.720 | **无解** |
| 11 | 2.055 | 2.054 | **无解** |
| 5 / 9 / 10 / 12..17 | — | — | 有解 |

同型号、同中转站、同 ordinal、同窗口，**16 个可判 ordinal 里 7 个严格无解**
（上表逐行，评审前这里写的是「8 个」，与表本身不符，已按表改正）。
根因是隐藏质量**可加、逐 Run**（这条 Run 想了多少、循环调了几次工具），
而不是**成比例**；用一个按 ordinal 走的标量倍率去拟它，只能在
「漏判超窗」与「打死装得下的 Run」之间挑一头。

### 2.2 具体代价

把三元组调到 `2.00/0.35/6.90`+schema 1.30：

- 低估 **83 → 0**，42 条真超预算 **0/42 → 42/42** 判出；
- 但 197 条**真实装得下**的请求里 **102 条**会被判为超预算；
- 而且会**重新打死 Incident O 修好的那条 Run**：run6 ordinal 6 真实只需要 ~1.4，
  新倍率给 4.10，把真实 19 491 的 prompt 估成 47 067
  （`tests/sdk_adapters/test_token_estimator_calibration.py::
  test_run6_turn5_request_set_no_longer_fails_closed` 在这组参数下变红）。

Incident O 本身就是「倍率太大打死装得下的 Run」，把它再做一遍不是修复。
**所以 `llm/model_info.py` 里的三个数保持不变，只把上面这套证据与结论写进注释。**

### 2.3 记一个口径错误（不改行为）

run6 那次拟合把 `request_id` 的 provider-turn 序号当成 **0 基**，生产实际是
**1 基**（`receipt.provider_turn_ordinal` 从 1 起）。后果是生产比拟合**多留一档余量**
（ord1 用的是拟合给 ord0 的下一档 1.60 而不是 1.25），方向安全。
新 fixture `hm_to_a6_flash_pool_samples.json` 一律按真实 1 基记。

---

## 3. 修法：物理发出之前的**实测**下界

新增 `backend/deskpet/sdk_adapters/wire_input_budget.py`。

同一条 Run 上一次真实的 `usage.input_tokens` 已经在 Host 手里，它减去当时
Host 能量到的 wire，就是那一轮隐藏质量的**实测值**；再加上那一轮**回灌的
reasoning**（下一轮 prompt 里唯一量不到的新增质量）：

```
carry(n)  = max(0, input_tokens(n-1) - wire_tokens(n-1)) + reasoning_tokens(n-1)
carry_eff = carry(n) × min(1, wire_tokens(n) ÷ wire_tokens(n-1))
floor(n)  = wire_tokens(n) + carry_eff        # wire_tokens 量的是**装配好的 payload**
floor(n) > effective_input_budget  →  fail closed
```

两处口径都是评审改出来的，各自都有真机代价（§评审 MUST-FIX 1/2）：

- **第二项只加 reasoning，不加整个 `output_tokens`**。上一轮的 assistant 正文与
  `tool_calls.arguments` 这一轮**已经在 `wire_tokens(n)` 里**——闸门量的就是补回
  arguments 之后的 payload 本身；再加一遍就是把同一块质量计价两次。合池 239 组
  里因此有 **28 组** 的 "floor" 高过真实计费（最多 +1769），那就不是下界。
  `usage` 不报 `reasoning_tokens` 时退回 `output_tokens`（保守方向）。
- **carry 按本轮 payload 相对上一轮的收缩比打折**。隐藏质量挂在产生它的那几轮
  assistant 消息上；装配期一裁史/强制分页，那几轮连同它们的 reasoning 一起离开
  payload，而账本里的 carry 还停在裁史之前。`wire(n) < wire(n-1)` 是这道闸门唯一
  看得见的裁史信号——裁史事实（`groups_trimmed_for_budget`、`pages_forced`）落在
  **回执**里，不在 `ProviderRequest` 上：239 组真机配对的 `request.metadata`
  **全是 `{}`**。不打折就会拿一份过期的 carry 去**终局性**地打死一个真装得下的请求
  （真机样本 `run9/22ea642e` ordinal 3→4：wire 14 996 被裁到 11 579，
  真实只计费 15 543，不打折的 floor 是 16 032 —— 反超真实计费）。

- 位置：`_ProductOpenAICompatibleProvider._request_payload`，即
  **payload 成型之后、任何字节离开 Host 之前**。这样 `wire_tokens(n)`
  里**包含**刚补回的 `tool_calls.arguments`——装配期估算看不到的那一块，
  在这里是可量的。
- 错误码：`WireInputBudgetExceeded.error_code =
  "sdk_provider_wire_input_budget_exceeded"`。它继承 SDK 的
  `ProviderRequestRejectedError`（在 `_DEFINITE_PROVIDER_FAILURES` 里，所以协调器
  结算为**确定失败**而不是未知交接，Run 不会卡在 waiting），但**覆盖了基类的
  `provider_request_rejected`**：`ProviderError.__init__` 把类属性 `error_code`
  写进实例 slot `code`，而 `dispatch` 按 `str(exc.code)` 结算
  `provider_invocations.error_code` —— 于是这次拦截在库里与 `ProspectiveRequestGuard`
  等其它 rejected 分得开，a6_verify 直接按它取证（§6.1）。
- 日志一行，只有数字没有 payload，与 `sdk_context_budget_exceeded` 同体例：
  `sdk_provider_wire_input_budget_exceeded floor=… effective=… wire=… carry=…
  carry_before_trim=… observed_input=… observed_output=… observed_hidden=…
  observed_wire=… window=… ordinal=…`（`carry` 与 `carry_before_trim` 不等就是
  「上一轮之后裁过史」）。
- 窗口优先取**请求自己带的** `metadata.budget.context_window` / `context_window`，
  取不到才用适配器构造时 `llm.model_info.resolve` 出来的型号窗口（**含用户全局
  `model_overrides.toml`**——本次事故的 32000 正是它钉出来的）。两个都取不到就整条
  闸门不判。今天 SDK 不往 `ProviderRequest.metadata` 写窗口，这条优先级是为写入方
  出现的那天准备的：判的必须是这条请求**被装配时**用的那个窗口。
- 账本是进程内的有界 FIFO（256 条），键是 **`(run_key, target.model)`**：同一条 Run
  中途换绑型号时不会拿旧型号的隐藏质量判新型号的请求。`run_key` 对不带
  `:provider-turn:` 的 request_id（探针的 `hash-only`、conformance 的 `req-1`）返回
  空串 = **没有观测**，不再退化成「整串当 Run」——那会把不同 Run 的观测混成一条。
  冷启动/首轮没有观测 → `carry=0`，退化成「只看 wire」，与本模块出现之前逐 token
  相同。响应没有 `usage`、或没有配对的 `record_wire`，就**不记**观测——宁可没有
  观测，也不要错误的观测。

### 3.1 效果（同一批 239 组，逐条重放 `request_json` 复算）

复算脚本与夹具同源：按 `claimed_at` 取每条 Run 的 `(request_json, usage_json)`，
`wire` 用 `wire_message_tokens` + `tool_schema_tokens` 同口径量，逐轮把上一轮的
真实观测灌进账本。**注意口径**：落库的 `request_json` 里没有 `_wire_messages` 补回
的 `tool_calls.arguments`，所以离线算出来的 `wire(n)` 比生产实际的 wire **偏小**，
下面这组数是保守的一侧。

| | 旧口径（只有倍率） | 评审前（carry 加 output，不打折） | **本轮（reasoning + 收缩比）** |
|---|---|---|---|
| 触发次数 | — | 40 | **36** |
| 触发中真实超预算 | — | 40 / 40 | **36 / 36** |
| 误伤（真实装得下却被拦） | — | 0 / 197 | **0 / 197** |
| 真超 `effective` 被拦下 | 0 / 42 | 40 / 42 | **36 / 42** |
| 真超**整个窗口**被拦下 | 0 / 24 | 24 / 24 | **24 / 24** |
| "floor" 反超真实计费（即**不是**下界） | — | 28 / 239（最多 +1769） | **6 / 239**（最多 +1661） |

**代价与收益**：修法各让出 4 条真超预算的判出（40→36），换来 22 组「floor 不再高过
真实计费」。让出的那 4 条（`run8/400aa3a2` ord 10/11、`run9/fb734a05` ord 19、
`run9/b0dc8632` ord 7）与原本就漏的 2 条（`run9/765be600` ord 2、
`run8/f9b609ff` ord 10）一样，**全部仍在 32000 物理窗口内**（最大 28 466 = 超
effective 6.4%），真超窗口的 24 条一条都没让。方向是对的：这道闸门是**终局**的
（一响这次尝试就结束），宁可把边缘那几条交给装配期的有序降级，也不能因为一份过期
或重复计价的 carry 打死真装得下的请求。

剩下的 6 组「floor 高过真实计费」里 **3 组是 ordinal 1**（`carry=0`，超的是文本
估算自身，与本模块无关，+90 / +810 / +913），carry 惹的只剩 3 组
（+177 / +351 / +1661）。

事故那条 Run 仍在 **ordinal 4** 被拦下（floor 28 196 对真实 33 200——低于真实计费
15%，是**下界**方向），不会走到 76 708。

### 3.2 已知边界

- 这道闸门只会 **fail closed**，不会降级。要让 Incident O 的有序降级
  （强制分页 → 裁史）也能看见这条 carry，必须把它送进
  `context_authority._plan_turn_messages`——那是 F-E3 车道正在改的文件，
  本轮**不动**，补丁与红测见第 5 节。
- 收缩比只是裁史的**代理信号**。装配期真正的裁史事实（`groups_trimmed_for_budget`、
  `pages_forced`）落在回执里，`ProviderRequest.metadata` 上没有——239 组真机配对
  全是 `{}`。等 F-W-2 把 carry 送进装配期，两边就能用同一份事实，代理信号可退休
  （F-W-5）。
- `wire_message_tokens` 给每个 `tool_calls` 条目只算 **8 个 token** 的信封，而
  `{"id":"call_0_<32 hex>","type":"function","function":{"name":"","arguments":""}}`
  实际约 20 个。这是**刻意偏低**的：这条线要下界，信封宁可少算；真正的量级在
  arguments 本身。

---

## 4. A6-12：回执/调用不变量

### 4.1 现象与根因

`provider_invocations` 134 条、`run_context_snapshot_receipts` 136 条，
两个 Run 各多一条（`ef57d663` 11≠10、`765be600` 5≠4）。逐条比对后：
**两条多出来的都是该 Run 的末条回执，指纹对不上任何调用。**

`native.log` 给出确切时序（以 `ef57d663` 为例）：

```
21:30:35.994  回执 rev11 落库（planned=20360, headroom=6392）
21:30:36.142  recall_context_use_authority_stale  (typed_context_use)
21:30:36.143  sdk_run_driver_failed / run.fail
21:30:37.204  foreground.runtime.closure_settled  reason=closure_run_not_completed, provider_calls=0
```

`ContextAuthority.prepare` 的顺序是
`record_snapshot_receipt(...)` → `self._typed_use_authority.snapshot_intents(...)`，
后者抛 `RecallContextUseAuthorityStale`。**回执已经提交，请求从未上线。**
`765be600` 完全同形（22:10:29.622 同一个理由码）。

### 4.2 正确的不变量

那条回执**不是假的**：Host 确实组装了那个请求。旧判据的问题是
①**按位次**配对（第 n 条调用配第 n 号 ordinal），②要求逐 Run 行数相等，
③不等就整项降级成 INCONCLUSIVE——于是把一种合法形态判成异常，
同时把真正的异常一起藏进 INCONCLUSIVE 里。重放证明要的是**方向性**的一一对应：

- **(a)** 每一条 `provider_invocations` 必须有且只有一条**同 Run、指纹相同**的
  回执（`expected_request_fingerprint == request_fingerprint`，且
  `payload_hash == expected`）。缺失或一对多 → **FAIL**。
- **(b)** 没有调用的回执，只允许是该 Run 的**末条**（`snapshot_revision` 最大），
  且每 Run **至多一条**——即「组装完但整条 Run 就此终止」，
  计为 `unsent_terminal_receipts`，不计入不匹配。
- **(c)** 出现在**中段**的孤儿回执、或同一 Run 出现多条 → **FAIL**：
  账本声称组装过一个既没发出、也没终止 Run 的请求。

第 9 次证据在新判据下：`invocations 134 / receipts 136 /
fingerprint_aligned 134 / invocations_without_receipt 0 /
orphan_receipts_without_invocation 0 / unsent_terminal_receipts 2
(['ef57d663:rev11','765be600:rev5'])` → **A6-12 由 INCONCLUSIVE 转 PASS**，
且不再需要「疑似跑动中的活库快照」这种猜测性说明。

### 4.3 写入侧（F-E3 车道，本轮不动）

更干净的做法是让回执成为 snapshot 构造的**最后一步**——把
`record_snapshot_receipt` 挪到 `snapshot_intents` 之后。它能消掉本次的两条孤儿，
但**消不掉这一类**：Run 仍可能在 snapshot 返回之后、调用被 claim 之前被取消。
所以 (b) 这条不变量无论写入侧怎么改都必须保留。补丁见第 5 节。

---

## 5. 交给 F-E3 车道的补丁（本轮**未**落地）

以下三个文件由 F-E3 车道持有，本轮一行未改。

### 5.1 `backend/deskpet/sdk_adapters/context_authority.py`——回执作为最后一步

```diff
         if occurrences is not None:
             await occurrences.recheck(presentation)
-        snapshot_id, snapshot_revision = await self._ledger.record_snapshot_receipt(
-            sdk_run_id=request.run_id.value,
-            ...
-        )
         typed_fields = {}
         if self._typed_use_authority is not None:
             intents = await self._typed_use_authority.snapshot_intents(request=request, messages=messages)
             typed_fields = dict(schema_version=2, recall_subject=self._typed_use_authority.subject,
                                 recall_intents=intents)
+        # 事件 W: 回执是「Host 组装了这个请求」的重放凭证, 必须是构造的**最后**
+        # 一步。放在 snapshot_intents 之前, 任何在它之后的失败(第 9 次的
+        # recall_context_use_authority_stale)都会留下一条永远等不到调用的回执。
+        snapshot_id, snapshot_revision = await self._ledger.record_snapshot_receipt(
+            sdk_run_id=request.run_id.value,
+            ...
+        )
```

红测（新建 `backend/tests/sdk_adapters/test_snapshot_receipt_is_last.py`）：
构造一个 `snapshot_intents` 抛 `RecallContextUseAuthorityStale` 的
`_typed_use_authority`，调用 `prepare`，断言
`select count(*) from run_context_snapshot_receipts where sdk_run_id=?` 为 **0**。
当前实现下该断言得到 1（红）。

### 5.2 `context_partitions.py` / `context_authority.py`——把 carry 送进有序降级

```diff
 def _plan_turn_messages(
     messages, window_tokens, *, extra_protected=None, exact_tool_sources=False,
-    tools=(), provider_turn_ordinal=0, model_id=None,
+    tools=(), provider_turn_ordinal=0, model_id=None, observed_carry_tokens=0,
     allow_full_group_trim=False, raise_on_overflow=True,
 ):
@@
-    protected_tokens = schema_tokens + sum(estimator(_message_text(m)) for m in protected)
+    # 事件 W: 中转站回灌的 reasoning 与 wire 期补回的 tool_calls.arguments 都是
+    # **不可裁剪**的质量, 所以它和 tool schema 一样属于 protected。它来自本 Run
+    # 上一次真实 usage(wire_input_budget.ObservedInputCarryLedger), 是实测而不是
+    # 拟合 —— 有它时倍率再怎么偏都越不过这条线。
+    protected_tokens = (schema_tokens + max(0, int(observed_carry_tokens))
+                        + sum(estimator(_message_text(m)) for m in protected))
@@
     facts = {
         ...
+        "observed_carry_tokens": max(0, int(observed_carry_tokens)),
         "planned_input_tokens": total,
         "budget_headroom": effective - total,
     }
```

`prepare` 侧从账本取值：

```diff
+        from deskpet.sdk_adapters.wire_input_budget import default_observed_input_carry_ledger
+        carry = default_observed_input_carry_ledger().carry_tokens(
+            f"{request.run_id.value}:provider-turn:{request.provider_turn_ordinal}")
         def _plan(selected, exact, *, full_trim: bool, probe_only: bool = False):
             return _plan_turn_messages(
                 selected, window_tokens,
                 ...
+                observed_carry_tokens=carry,
```

红测（新建 `backend/tests/sdk_adapters/test_observed_carry_reaches_the_planner.py`）：
账本里写入 `input_tokens=39682 / wire=12000 / output_tokens=4869`
（carry=32551），再用一组本来装得下的 messages 调 `_plan_turn_messages`，
断言 `facts["observed_carry_tokens"] == 32551` 且触发有序降级
（`groups_trimmed_for_budget > 0` 或 `ContextBudgetExceeded`）。
当前实现下该参数不存在（红：`TypeError`）。

落地后回执里会多出 `observed_carry_tokens` 字段，A6-3 可以直接按它取证
「Host 那一轮到底知不知道自己背着多少隐藏质量」。

---

## 6. 验证器改动（`scripts/native/a6_verify.py`）

### 6.1 A6-3

- 新增计费侧取证 `Evidence.provider_billed_stats(effective, window)`：
  `billed_attempts` / `max_billed_input_tokens` /
  `attempts_over_effective_budget` / `attempts_over_window` /
  `planned_billed_pairs` / `planned_under_counts_billed` /
  `worst_planned_over_billed`(+样本)。
  planned 由 `Evidence.receipt_planned_by_fingerprint()` 按
  `(sdk_run_id, expected_request_fingerprint)` 与调用逐条配对。
  该 map 的键**不含 ordinal**，同 Run 同指纹的两条回执后写覆盖先写
  （`out[key] = planned`）——只影响「同 Run 两次逐字节相同的装配」这一种形态下
  planned 取哪一条，配对本身仍由 A6-12 的消耗式配对守住。
- **只统前台泳道**（评审 SHOULD-FIX）：`Evidence.foreground_sdk_run_ids()`
  （`foreground_run_heads.sdk_run_id`）之外的 Run（工作流/后台/探针）另有窗口与
  预算配置，混进来既能拿别人的 prompt 判前台 FAIL、也能把前台的问题稀释掉。
  其它泳道单独记 `other_lane_billed_attempts` / `other_lane_runs` /
  `other_lane_max_billed_input_tokens` /
  `other_lane_attempts_over_effective_budget`，并在说明里固定追加一句
  （`lane_note`），**不参与判定**。`sdk_provider_attempt_audit` 的
  `max_input_tokens` 与估算侧 `requests_over_budget` 同样按泳道过滤
  （审计表不带 run，用 `invocation_id → provider_invocations.run_id` 归属；
  归属不到的行按前台算）。认不出前台泳道（旧证据没有 `foreground_run_heads`）
  时一律按前台算，方向是 fail closed，并在 `billed_lane` 里写明
  `all_runs_unattributed`。
- **判据顺序**：「真实计费超预算」与装配期溢出**同级**，一起排在所有
  INCONCLUSIVE 分支**之前**。这是本次最关键的一处：旧顺序把它排在
  `timeout 轮 → INCONCLUSIVE` 之后，而第 17 轮恰好记了 timeout，
  于是 `max_input_tokens=76708` 只作为 numbers 存在、从未参与判定——
  **这条排序本身就是漏判通道**。
- 装配期溢出的 FAIL 说明里也追加计费侧数字，两条证据不再互相遮蔽。
- 新增 `Evidence.wire_input_budget_events()`：两路数第 3 节那道闸门——
  `sdk_provider_wire_input_budget_exceeded_total`（native.log 那一行）与
  `sdk_provider_wire_input_budget_settled_invocations` /
  `_settled_foreground`（`provider_invocations.error_code` 上的**耐久**结算，
  带 Run 归属）。被拦下的请求一个字节都没发出、也没有 usage，所以它在计费统计里
  一个数都不会动：**只有**这两个数能证明「这一轮闸门真的拦过」。非零时由
  `wire_gate_note` 固定写进 A6-3 的说明。

### 6.2 A6-12

按 §4.2 的三条不变量重写：指纹配对取代位次配对；`unsent_terminal_receipts`
/ `orphan_receipts_without_invocation` / `duplicate_receipts_for_one_invocation`
进 numbers；行数不等本身不再是判据。`per_run_count_mismatch` 保留但**重新填**成
真的逐 Run 行数差（`ef57d663:11!=10`），不再把 unsent 样本抄一遍——它现在是一个
读者要看的量，不是判据。FAIL 说明里那句也改了措辞：孤儿计数同时覆盖「中段孤儿」
与「同一条 Run 多条未发出的 receipt」两种形态，只说「中段孤儿 N 条」会误导。
兼容早于 S5a 的证据目录（无 `snapshot_revision` 列时退回
`provider_turn_ordinal`）。`--selftest` 的建表同步加列。

### 6.3 第 9 次证据的判定变化

| 项 | 旧 | 新 |
|---|---|---|
| A6-3 | FAIL（只因装配期 4 次溢出） | FAIL（说明里同时给出 37/134 超预算、24 次超窗、峰值 76708、planned 低估 59/134、最差 0.3277） |
| A6-12 | INCONCLUSIVE（"疑似跑动中的活库快照"） | **PASS**（134 条重放全等；2 条合法末条回执被正确归类） |
| 总计 | PASS 9 / FAIL 6 / INCONCLUSIVE 3 | **PASS 10 / FAIL 6 / INCONCLUSIVE 2** |

---

## 7. 测试

新增：

- `backend/tests/sdk_adapters/test_wire_input_budget.py`（**29 条**）——
  结构性缺口（用**真的** `_wire_messages` + 真的 `ToolCallArgumentsMemo` 演一遍：
  同一批 Message，文本估算 <20 token、线上多出 2000+）、账本语义（冷启动、
  无 usage、未配对、有界、按 `(Run, 型号)`、无 Run 前缀不记）、窗口未知不判、
  `metadata` 窗口优先、carry 只算 reasoning、裁史打折（真机 `22ea642e` ord 3→4）、
  打折在 facts 里可见、事故 Run 在 ordinal 4 被拦，以及**群体判据全部走真代码复算**：
  夹具的 `previous_turn` 灌进真的 `ObservedInputCarryLedger`、payload 由字符类计数
  还原后过真的 `check_wire_input_budget`（先用 round-trip 钉死还原出来的 wire 与
  夹具记的逐 token 相等），据此判「0 误伤」「超窗全拦」「漏判都在物理窗口内」
  「打折只降不升」「floor 反超真实计费的只有夹具点名的那几条」；
  **无解性证明**改为**从 samples 现算** `lo/hi`（再与夹具记的 239 组结论同号交叉验证）；
  旧口径 42/42 漏判改为逐条按 `llm.model_info` 现行三元组复算；三元组保持不变；
  两条接线用例：闸门抛出时 `httpx.MockTransport` **一个字节都没收到**（且
  `exc.code` 就是那个稳定码）、没有隐藏质量的 Run 三轮全发；被拦下的请求不留悬挂
  的 wire 记录；夹具只含原始观测、无 payload、无派生量。
- `backend/tests/native/test_a6_verify_budget_bypass.py`（**12 条**）——
  A6-3 在有 timeout 轮时仍然因计费超预算 FAIL、planned/计费逐条配对、
  装配期溢出说明里带计费侧、实测闸门在 native.log 可数、闸门的 `error_code` 在
  `provider_invocations` 上可数（且拦下的那次不进计费统计）、**只判前台泳道**
  （另一条泳道 40 000 的调用不再把前台判 FAIL，但在 numbers 与说明里可见）；
  A6-12 接受每 Run 一条末条回执（并报出真的行数差 `11!=10`）、
  中段孤儿 FAIL、调用无回执 FAIL、同 Run 两条未发 FAIL、干净 Run PASS、
  同指纹消耗式配对（共用一条 receipt FAIL / 两条各认领一条 PASS）。
- fixture `backend/tests/fixtures/hm_to_a6_flash_pool_samples.json`（评审后重建）——
  239 组的总体量 + **65 条**代表样本。样本只落**原始观测**：本轮 payload 的字符类
  计数、`wire_input_tokens`（round-trip 锚点）、provider 计费三元
  （input/output/reasoning）、以及同 Run 上一轮的 `(wire, input, output, reasoning)`。
  `measured_carry_tokens` / `measured_input_floor` 这两个**派生量已删除**——它们
  正是评审说的「断言退化成夹具自证」的来源。总体量分两块：`measured_floor`
  （本轮口径）与 `measured_floor_before_the_review`（评审前口径），修法的代价与
  收益一眼可比。**不含任何 payload**（与 `hm_to_a6_pro_pool_samples.json` 同体例）。

红/绿基线：新增的 41 条用例在 `34dbc82a` 上**全部红**
（`wire_input_budget` 模块不存在；a6_verify 的 11 条 `KeyError` / 判定不符），
在本分支上**全部绿**。

回归（基线一律 `34dbc82a`，同一 venv、单进程）：

| 组 | 本分支 | 基线 | 差集 |
|---|---|---|---|
| 评审后一次跑（合入 main `1e7043e9` 之后）：`test_wire_input_budget` + `tests/native/` + `test_token_estimator_calibration` | **143 passed, 0 failed** | — | — |
| 评审前：`test_model_info` / `test_model_context_windows` / `test_model_context_ipc` / `test_token_budget_per_model` / `test_context_config_v2` / `test_token_estimator_calibration` / `test_context_authority_primitives` / `test_wire_input_budget` / `test_primary_provider_preflight` / `tests/native/` | **183 passed, 0 failed** | — | — |
| provider 相邻 11 个 sdk_adapters + 4 个 execution 文件 | 177 passed / 12 failed | 177 passed / **同样 12 failed** | **∅** |
| provider/memory/execution 更广的 14 个文件 | 75 passed / 11 failed | 74 passed / **12 failed**（多一条 `test_primary_none_routes_to_exact_task_and_writes_real_file[True]`，本分支上通过） | 新增 **0** |

即：本分支引入 **0 条新失败**，FAILED 集合是基线的子集。

---

## 评审（2026-09-09 一次只读评审，逐条处置）

评审对象是本 memo 第 3/6/7 节落地后的代码。所有数字都在合池 239 组上**重新复算**过
（脚本口径见 §3.1），复算独立于夹具生成器：两边算出的
`fires=40 / 0 误伤 / 2 漏判`（评审前口径）逐个相等，才用它来判本轮的改动。

| # | 结论 | 处置 |
|---|---|---|
| MUST-FIX 1 | carry 熬过了一次裁史 | **已改**：`ObservedProviderTurn.carry_for_wire` 按 `min(1, wire(n)/wire(n-1))` 打折 |
| MUST-FIX 2 | `measured_input_floor` 不是下界 | **已改**：carry 第二项由 `output_tokens` 改为 `reasoning_tokens`（缺失才退回 output） |
| MUST-FIX 3 | 群体用例是同义反复 | **已改**：夹具删派生量，用例把 `previous_turn` 灌进真账本、过真闸门复算 |
| SHOULD-FIX ×7 | 见下 | 6 条已改，1 条（`(run, fingerprint)` 后写覆盖）按「记录不改」处置 |
| NIT ×5 | 见下 | 4 条已改，1 条（`+8` 信封）按「记录为刻意」处置 |

### MUST-FIX 1 —— carry 熬过一次裁史，可能终局性打死装得下的请求

成立。装配期裁史/强制分页之后，产生隐藏质量的那几轮 assistant 消息已经离开 payload，
账本里的 carry 还停在裁史之前。**改法**：carry 按本轮 payload 相对上一轮的收缩比打折
（`carry_eff = carry × min(1, wire(n)/wire(n-1))`，整除）。

评审建议的另一条路——「`request.metadata` 里有 `groups_trimmed_for_budget` / 强制分页
就跳过闸门」——**查证后不采用**：那些事实落在**回执**里，`ProviderRequest.metadata`
上没有；239 组真机配对的 `metadata` 全是 `{}`（`select metadata from
provider_invocations` 逐条查过）。等 F-W-2 把 carry 送进装配期，两边共用同一份事实时
再退休收缩比这个代理信号（F-W-5）。

评审给的复现编号（`run9-08c04462-t13` → carry 15 580 + wire 12 000）与真机配对对不上
（该样本 wire=14 937、carry=13 878、真实计费 29 373，是**真超预算**的一条）。
逐条重放后，**记录在案的 239 组里一条误伤都没有**（评审前口径也是 0/197）——
真正成立的是它的机理而不是那组数字：不打折的 floor 在 28 组上高过真实计费，
其中 `run9/22ea642e` ord 3→4 最典型（wire 14 996→11 579、真实计费 15 543、
不打折 floor 16 032）。预算只要落在 15 543 与 16 032 之间，这条终局闸门就会打死一个
真装得下的请求。红测按这条真机样本写（`test_a_history_trim_discounts_the_carry_
instead_of_killing_the_request`）。

### MUST-FIX 2 —— `measured_input_floor` 不是「floor」

成立，且比评审说的更广：评审在 52 条样本里看到 10 条超调（最多 +865），
在**全池 239 组**上是 **28 条**（最多 +1769）。根因如评审所述——上一轮的正文与
`tool_calls.arguments` 这一轮已经在 `wire(n)` 里（闸门量的就是补回 arguments 之后的
payload），只有回灌的 reasoning 是新增且量不到的。

**改法**：`carry = hidden + reasoning_tokens`，`reasoning_tokens is None` 时退回
`output_tokens`（保守方向）。`ProviderUsage.reasoning_tokens` 早已在
`provider.py::_sdk_provider_usage` 解析（`completion_tokens_details.reasoning_tokens`），
本轮只是把它接进 `observe_usage`。真机 239 组**每一条**都带 `reasoning_tokens`，
退化分支只为不报这个字段的中转站留着。

**重新复算的 40/40 · 0/197**（§3.1 已整表替换）：这个组合本身没变号，但两处修法各让出
4 条判出——`fires 40 → 36`、`真超预算被拦 40/42 → 36/42`、
`"floor" 反超真实计费 28/239 → 6/239`。误伤仍是 **0/197**，超窗仍是 **24/24**。
让出的 4 条与原本漏的 2 条全部仍在 32000 物理窗口内（最大 28 466）。

### MUST-FIX 3 —— 群体用例断言的是夹具字面量

成立。夹具重建：删掉 `measured_carry_tokens` / `measured_input_floor`，改存
`previous_turn`（上一轮的 `wire/input/output/reasoning` 原始观测）与
`wire_input_tokens`（round-trip 锚点）。用例把 `previous_turn` 经
`record_wire`/`observe_usage` 灌进真的 `ObservedInputCarryLedger`，把字符类计数还原成
真的 payload + tool specs，再过真的 `check_wire_input_budget`——先用一条 round-trip
用例钉死「还原出来的 wire == 夹具记的 wire」，其余判据（0 误伤 / 超窗全拦 / 漏判都在
窗口内 / 打折只降不升 / 反超名单）全部由重放结果导出。倍率无解性也改为**从 samples
现算** `lo/hi`，再与夹具记的 239 组结论交叉验证同号。

### SHOULD-FIX

1. **`request.metadata` 的窗口优先** —— 已改（`window_tokens_from_metadata`，
   `budget.context_window` → `context_window`，与 `context_authority._resolve_window_tokens`
   同一批 key）。今天没有写入方，注释与用例都写明了这一点。
2. **`run_key` 对无 Run 前缀的 id 返回 `""`** —— 已改。`hash-only` 探针、
   conformance 的 `req-1`、裸 `provider-turn:N` 都不再自成「一条 Run」，
   否则会拿另一条 Run 的 carry 判这一条。
3. **`provider_billed_stats` 只统前台泳道** —— 已改，其它泳道单独报（§6.1），
   `sdk_provider_attempt_audit` 与估算侧 `requests_over_budget` 一并按泳道过滤。
   第 9 次证据里 134 次调用全部属于前台，所以数字不变；判据从此不会被工作流泳道污染。
4. **自己的 `error_code`** —— 已改。`error_code = "sdk_provider_wire_input_budget_exceeded"`
   覆盖基类的 `provider_request_rejected`，`dispatch` 按 `str(exc.code)` 结算到
   `provider_invocations.error_code`；A6-3 增两个耐久计数并在说明里点名（§6.1）。
   （原实现叫 `reason_code` 并在注释里说「不能叫 code」——那句话只对 `code` 这个
   实例 slot 成立，类属性 `error_code` 才是 SDK 留的那个钩子。）
5. **账本键 `(run_key, target.model)`** —— 已改。`record_wire` 记下型号，
   `observe_usage` 用同一次请求的型号入账，闸门从 `payload["model"]`（即
   `target.model`）取键。
6. **`per_run_count_mismatch` 重填** —— 已改（§6.2）。
7. **`(run, fingerprint)` 后写覆盖** —— **记录不改**：`receipt_planned_by_fingerprint`
   的键不含 ordinal，同 Run 同指纹的两条回执后写覆盖先写。只影响「同 Run 两次逐字节
   相同的装配」这种形态下 planned 取哪一条（两条的 planned 也必然相同，因为装配
   逐字节相同），配对本身由 A6-12 的消耗式配对守。已写进 §6.1。

### NIT

1. **docstring 里「重新拟合的 ratio 接住那 2 条」** —— 已删。那句是假的：
   §2 的结论正是重拟无解，两条漏判由装配期的有序降级承担，不由倍率承担。
2. **`+8` 的 `tool_calls` 信封偏低** —— **记录为刻意**：真实信封约 20 token，
   这条线要的是下界，信封宁可少算；注释里写明了量级与理由。
3. **`forget()` 没有接线** —— **已删**。有界 FIFO 就是这个账本的全部生命周期管理，
   一个没人调用的清理入口只会让读者以为存在显式回收。
4. **「中段孤儿 receipt 2 条」措辞** —— 已改（§6.2）。
5. **`(run, fingerprint)` last-write-wins 未记** —— 已记（见 SHOULD-FIX 7）。

### 评审之外、本轮一并改正的

- §2.1 正文原写「16 个可判 ordinal 里 **8** 个严格无解」，同页的表只有 **7** 行无解。
  按表改正为 7。
- 夹具的 `cumulative_reasoning_tokens_max` 由 39 286 改为 **38 215**（重建时按
  「本轮之前的累计」逐 Run 复算，与 §1.1 事故 Run 那一列一致）。

---

## 8. 待办

- **F-W-1**（F-E3 车道）：§5.1 回执作为最后一步 + 红测。
- **F-W-2**（F-E3 车道）：§5.2 把 `observed_carry_tokens` 送进
  `_plan_turn_messages`，让**有序降级**（而不只是 fail-close）也能看见隐藏质量；
  落地后回执多出 `observed_carry_tokens`，A6-3 增一条取证。
- **F-W-3**：账本是进程内的。冷启动续聊时第一轮没有观测——可以从
  `provider_invocations` 里读本 Run 上一次的 `usage_json` + `request_json`
  重建 carry，把这个盲区补掉。
- **F-W-5**：裁史的收缩比只是代理信号。F-W-2 落地后，装配期与闸门可以共用
  `groups_trimmed_for_budget` / `pages_forced` 这些**真事实**（走
  `ProviderRequest.metadata` 或回执），届时把收缩比换掉。
- **F-W-6**：A6-3 的泳道过滤依赖 `foreground_run_heads.sdk_run_id`。
  `sdk_provider_attempt_audit` 自己不带 run，本轮靠 `invocation_id` 回查
  `provider_invocations` 归属；归属不到的审计行按前台算（fail closed）。
  更干净的做法是让审计行自己带 `sdk_run_id`。
- **F-W-4**：把「同型号同 ordinal 上倍率无解」这个结论回写
  `DECISION-TOKEN-ESTIMATOR.md` §3 的 F-TOK-1 条目——pro 的
  `min(1.50+1.25·ordinal, 7.10)` 大概率有同样的问题，只是它的生产窗口是 1M，
  多估还够不着边界，所以还没炸。
