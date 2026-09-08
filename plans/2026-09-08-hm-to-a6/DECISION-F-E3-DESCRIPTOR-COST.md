# 决策备忘：分页摘要自身的成本有界化（F-E3，Incident E / F-E2 的 followup）

> 日期：2026-09-09
> 义务：`HM-TO-A6`（AC = HM-AC-6「动态上下文组装必须在模型预算内完成，大 tool result 必须分页」）
> 前置：[DECISION-SAME-RUN-CONTEXT-BOUND](DECISION-SAME-RUN-CONTEXT-BOUND.md)（Incident E）、
> [DECISION-F-E2-CONTROL-RESULT-BOUND](DECISION-F-E2-CONTROL-RESULT-BOUND.md)（F-E2）
> 分支：`worktree-f-e3`（自 `dbf967fc` 起）

---

## 1. 事实与证据（不是推断）

证据来源：`.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo/`
（HM-TO-A6 第 9 次原生真机跑，`deepseek-v4-flash`，`model_overrides.toml` 把 window 钉在
**32000** → `effective_input_budget = 26752`，`ratio = 1.65`）。
Host 为 `dbf967fc`，**已经包含** Incident E 的同 Run 上界与 F-E2 的 control 结果省略。

### 1.1 四个 Run 在「已经分页」的状态下仍然超预算

`native.log` 中四条 `sdk_context_budget_exceeded`（`full_trim=True`，即有序降级已经全部跑完）：

| Run | planned | effective | 超出 | protected | tool_schemas | open_group |
|---|---:|---:|---:|---:|---:|---:|
| `fd4b0849e7` | 27 982 | 26 752 | **1 230** | 8 624 | 6 077 | 19 358 |
| `539ca5f03b` | 27 932 | 26 752 | **1 180** | 8 607 | 6 077 | 19 325 |
| `5c893a4459` | 27 007 | 26 752 | **255** | 8 592 | 5 898 | 18 415 |
| `4996b84db6` | 26 862 | 26 752 | **110** | 7 771 | 5 898 | 19 091 |

> 更正一处口径：任务书把 `884744e320`（T11）列为四个失败 Run 之一。按 `native.log` 逐行核对，
> 真正抛 `sdk_context_budget_exceeded` 的第四个 Run 是 **`4996b84db6`**；`884744e320` 是
> `closure_run_not_completed`，不是预算超限。下文两者都测。

### 1.2 主要成本是分页摘要自己，不是被分页的结果体

对每个 Run 的**最后一次成功 provider 调用**（`provider_invocations.request_json`），按
`metadata.source` 分组统计（`.local-test-evidence` 全程只读 `?mode=ro`）：

| Run | `primary_settled_effect_v1` 条数 | 摘要合计 B | 原体合计 B | 摘要 token | 原体 token |
|---|---:|---:|---:|---:|---:|
| `fd4b0849e7` | 13 | 26 060 | 72 035 | 6 550 | 18 014 |
| `539ca5f03b` | 14 | 28 076 | 79 175 | 7 073 | 19 798 |
| `5c893a4459` | 16 | 32 098 | 98 817 | 8 036 | 24 709 |
| `4996b84db6` | 6 | 12 014 | 30 737 | 3 065 | 7 685 |
| `884744e320` | 4 | 8 069 | 28 874 | 2 019 | 7 220 |

**16 条摘要单独就是 8 036 token / 26 752 预算的 30%。** 单条摘要 **1 960–2 030 B**，
与原体大小无关（原体从 2 338 B 到 10 851 B 都是同一个数）。逐字段实测（`5c893a4459` 的一条，
共 1 993 B）：

| 字段 | 字节 | 说明 |
|---|---:|---|
| `excerpt` | 1 086 | `_excerpt(content)` 固定 1 024 B，JSON 转义后 1 086 |
| `source.run_id` | 88 | 每条重复同一个 Run id |
| `source.provider_invocation_id` | 92 | 无消费者 |
| `source.provider_response_hash` | 92 | 无消费者 |
| `source.effect_id` | 86 | `verify_request` 的查找键，**必须留** |
| `source.content_hash` | 82 | 与顶层 `source_hash` **完全重复** |
| `source.result_hash` | 81 | 无消费者 |
| `source_hash` | 81 | 工具 schema 要求模型逐字复制，**必须留** |
| `reference_id` | 107 | 准入令牌，**必须留** |
| `kind` / `page_tool` / `content_bytes` / `effect_version` / `raw_call_id` / `tool_name` | 35/30/21/19/49/26 | |

固定部分 **907 B**，`excerpt` **1 086 B**。

### 1.3 `excerpt` 那 1 KiB 买到了什么：几乎什么都没有

对证据里全部 **47 条**真实摘要做前缀去重（同 Run 同工具内互相区分）：

| excerpt 截断 | 能互相区分的原体 |
|---:|---|
| 64 B | 5 / 45 |
| 96 B | 17 / 45 |
| **128 B** | **33 / 45（73%）** |
| 160–320 B | 33 / 45 |
| 384–1024 B | 34 / 45（76%） |

**128 B 之后到 1024 B，只多区分出 1 条。** 原因是 `canonical_json` 键序固定，前
**73 B** 恒为 `{"error_code":null,"outcome":"succeeded","public_message":null,"value":{"`，
剩下的判别力全在紧随其后的几十字节里。

### 1.4 「不该分页却分了」与「该分页却分不了」

- 分了但白分：`tool_describe` 原体 2 098 B → 摘要 2 011 B（省 4%）；
  `tool_search` 原体 2 338 B → 2 010 B（省 14%）。旧规则只要求
  `len(summary) < len(content)`，这两条都通过了，模型白白失去可读正文。
- 分不了：原体 ≈1 700 B 的 `tool_search`，摘要 ≈1 990 B **比原体还大**，`replace` 直接
  返回 0。这个体量在 flash 的实际行为里非常常见（一个 Run 内 13–16 次 `tool_search`），
  于是「同 Run 上界」这条杠杆对它**完全失效**。

Incident E 备忘 §6 早已预言了这一点（「超过 ~13 条后 break 事实上不可达」「把最旧一批压到
0–128 字节 excerpt，单条成本从 ~456 降到 ~110 token」），本轮证据只是把它变成了现场事故。

---

## 2. 方案

### 2.1 (a) 有界 descriptor：wire 只带有读者的字段

`summary()` 改为发送 descriptor 的**投影**，而不是 descriptor 本身：

```json
{"kind":"primary_settled_effect_v1",
 "excerpt":"<最多 128 B 的确定性前缀>",
 "pages":7,
 "page_tool":"context_page_in",
 "reference_id":"primary-effect-page:v1:<64 hex>:0",
 "source":{"effect_id":"…","tool_name":"tool_search","content_bytes":6598},
 "source_hash":"<64 hex>"}
```

**完整 descriptor 一个字段都没改**：它仍然是 `reference()` = `PREFIX +
canonical_hash(descriptor)` 的原像，仍然是 `admitted_current_page` 返回的 `source`。
`verify_request` 从公共 audit/effect 事实**重新推导整个 descriptor**，再和消息内容做逐字节
相等比较，而这个比较覆盖 `reference_id`——所以被拿掉的
`run_id` / `effect_version` / `result_hash` / `provider_invocation_id` /
`provider_response_hash` / `content_hash` / `raw_call_id` **仍然是 SHA-256 的原像分量**，
改动任何一个都会改掉 `reference_id`、当场 `summary_mismatch`。
**不是"不再检查"，是"不再打印"。**

保留哪些、为什么：

- `reference_id` / `source_hash` / `page_tool` —— `context_page_in` 的工具描述明文要求模型
  「从带 `page_tool="context_page_in"` 的截断标记里逐字复制 `reference_id` 与 `source_hash`」，
  三个都是模型可见契约。
- `source.effect_id` —— `verify_request` 唯一从 wire 读的字段（查找键）。
- `source.tool_name` / `source.content_bytes` / `pages` / `excerpt` —— 让占位符对模型可读：
  这是哪个工具的结果、多大、读完要几页、开头长什么样。
- 去掉 `source.content_hash`：它与顶层 `source_hash` 逐字节相同，工具 schema 用的是后者。
- 去掉 `source.run_id`：`verify_request` 是拿**调用方可信的** `run_id` 去查
  `read_primary_effect_page_facts` 的，而 `read_effect_facts` 本来就要求
  `effect.run_id.value == run_id`（`effect_missing`）。新代码把 `foreign_source` 断言挪到
  **重新推导出来的** descriptor 上，比原来对着模型可见文本比更强。

**`SUMMARY_EXCERPT_BYTES = 128`**，取自 §1.3 实测：128 B 与 1 024 B 的判别力只差 1/45，
而 128 B 恰好覆盖固定 73 B 包装后的第一个 `capability_id`。

**实测单条：566–568 B / 142 token**（旧：1 960–2 030 B / 490–507 token），**降为 1/3.5**。
落在任务书要求的 100–150 token 区间内。

> 为什么**没有**采用 Incident E 备忘里「把 excerpt 长度纳入 descriptor、按新旧压到 0–128 B」
> 的变长方案：那会把一个**策略值**放进准入原像（`canonical_hash(descriptor)`），并让 wire 上
> 的一个字段参与决定 descriptor 身份；同一份 body 会因为投影时的预算压力得到不同的
> `reference_id`。安全性仍然成立，但准入面变宽，换来的只有约 30 token/条。定长硬上限
> 更简单、更可复算，且已经达标。

### 2.2 (b) 不划算就不分页

新增 `worth_paging(body_bytes, content_bytes, margin=True)`：

- 常态：`len(summary) × PAGE_SAVING_DIVISOR(=2) ≤ len(content)`，即摘要必须**不超过原体一半**，
  否则原体保留原样。按 §1.4，2 098 B / 2 338 B 那两条从此不会再被白分。
- `force_all`（Incident O 的有序降级第 2 步，Host 已经判定「这一轮装不下」）：降回旧规则
  「只要更小就分」。这就是任务书说的「除非同 Run 上界确实需要，此时取最小形态」。

另外（独立评审 N1）：`worth_paging` 是**字节**规则，而它服务的上界是**token** 规则，
`text_tokens` 对 CJK 按 1 token/字、其余按 1 token/4 字符计价。一个由 4 字节码位（emoji、
扩展 B 区）组成的 1 615 B 原体只值 119 token，而它 556 B 的摘要值 131 token——字节说「该分」，
token 说「分了更贵」，`carried` 会不降反增。因此 `replace` 现在**同时**要求
`text_tokens(content) - text_tokens(body) > 0`，累积循环由构造保证单调。

### 2.3 (c) 每 Run「合并组通知」——**否决**

考虑过把 N 条同工具摘要合并成一条列出全部 `reference_id` 的组通知。否决，三条理由：

1. **会破坏 tool_call / tool_result 配对**。每条 `role=tool` 消息绑定一个具体的 `call_id`，
   Provider 协议要求每个 assistant tool_call 有且只有一条对应结果消息。合并意味着删掉
   N-1 条 tool 消息，而 `_project` 的既有不变量正是「不增、不删、不重排，`name`/`call_id`
   完整保留」（F-E2 的用例也断言这一点）。
2. **准入面要重做**。`verify_request` 现在是消息↔descriptor 一一对应；组通知需要一条新的
   准入路径，而每个 `reference_id` 仍必须各自带上 `effect_id` 才能被重新推导。
3. **省不了多少**。合并只能省掉重复的 `kind`（35 B）与 `page_tool`（30 B），单条 65 B；
   16 条合计约 1 KB / 260 token。为 260 token 新开一条准入面不划算。

### 2.4 回执

`_current_tool_page_facts` 新增两个计数（和既有计数一样，**从真正发出去的消息里数**，
不是投影器的自述）：

- `descriptor_bytes_saved` = Σ(`source.content_bytes` − 摘要自身字节)。
  attempt 9 的形状下：旧 ~46–67 KB 换 ~26–32 KB，新 ~46–67 KB 换 ~7–9 KB。
- `results_kept_verbatim_small` = 原样出发且小于 `PAGE_WORTH_MIN_BYTES` 的非 control tool 体条数。
  **纯描述、不作因果断言**（见 §4 评审 S1）。

两者都不进 `provider_request_fingerprint`，A6-12 快照重放不受影响；
`RunContextSnapshot` 要求 `source_revisions` 的值非负，`max(0, …)` 保证。

### 2.5 与 F-E2 的组合

F-E2 的省略通知（`primary_control_result_elided_v1`、`control_stub`、`verify_control_stubs`）
**一个字节都没动**。两者互不干涉：control 消息不进 `descriptor_bytes_saved`，也不进
`results_kept_verbatim_small`（`name not in CONTROL_TOOLS` 分支）；`context_route` 仍然
永不省略、永不分页。唯一留下的是一处刻意的不对称——省略通知仍在 wire 上带 `run_id` 并
就地检查，而分页摘要不带、改查重新推导的那个。已在 `verify_control_stubs` 的 docstring
里写明「不要把它'统一'掉」。

---

## 3. 效果（离线复算四个失败 Run）

用**修复后的真实 `summary()`** 重算每个 Run 最后一次请求里的全部摘要：

| Run | planned | 超出 | 摘要 B 旧→新 | 摘要 token 旧→新 | 省(未校准) | 省(×1.65) | 能装下？ |
|---|---:|---:|---|---|---:|---:|---|
| `fd4b0849e7` | 27 982 | 1 230 | 26 060 → 7 360 | 6 550 → 1 846 | 4 704 | **7 761** | 是 |
| `539ca5f03b` | 27 932 | 1 180 | 28 076 → 7 924 | 7 073 → 1 988 | 5 085 | **8 390** | 是 |
| `5c893a4459` | 27 007 | 255 | 32 098 → 9 059 | 8 036 → 2 272 | 5 764 | **9 510** | 是 |
| `4996b84db6` | 26 862 | 110 | 12 014 → 3 396 | 3 065 → 852 | 2 213 | **3 651** | 是 |
| `884744e320` | （非预算失败） | — | 8 069 → 2 266 | 2 019 → 568 | 1 451 | 2 394 | n/a |

四条全部以 3–8 倍的余量装下。单条摘要 **566–568 B / 142 token**（n=53，min/med/max 一致）。

**诚实说明**：这四条是各 Run**最后一次成功发出**的请求；真正抛异常的那一轮没有落
`provider_invocations` 行，它比这里多一批结果。摘要节省随条数线性增长，所以上表相对
真正失败的那一轮是保守估计，但它不是那一轮的逐字节复算。

---

## 4. 独立评审（另起 opus agent，只读，不跑 pytest）

评审覆盖 6 个问题：准入安全、重放/持久性、确定性、预算算术、与 F-E2 的组合、其他缺陷。
结论：准入安全、确定性无 MUST-FIX；发现 1 条 MUST-FIX、1 条 SHOULD-FIX、5 条 NIT。已修：

**M1（MUST-FIX，已修）——跨版本 wire 格式不兼容，会关掉跨升级在飞的 Run。**
`MARKER` 没变而它命名的载荷不兼容地变了，且没有版本位、没有兼容读。破坏路径不是假设的：
`admitted_current_page` 校验的是**持久化的**父请求
（`read_effect_facts` 从 `provider_invocations.request_json` 重建），而
`primary_dependencies.check_runtime_dependencies` 对该 Run **每一条**历史
`context_page_in` effect、**每一轮**都重跑一次。升级前存下的旧形态摘要不再等于
`summary(descriptor, content)` → `summary_mismatch` → `PrimaryContextPageUnavailable` →
被 `check_runtime_dependencies` 的兜底 `except Exception` 变成
`PrimaryHistoryDisclosureRejected`，Run 被永久关闭。回滚方向同理。
另外，把 run_id 检查从 wire 挪到重新推导，会让「外来 effect」的**错误码**从
`primary_effect_page_foreign_source` 变成 `..._effect_missing`，而
`primary_dependencies.py` 会重放已记录的确定性拒绝并在错误码不一致时抛
`primary_page_rejection_mismatch`。

修法：新增 `legacy_summary()`——**只读不写**的旧形态。`verify_request` 接受
`message.content in (summary(...), legacy_summary(...))`；wire 上带 `run_id` 的（=旧形态）
保留 main 的 `foreign_source` 检查**及其位置**，错误码不变。这不放宽任何东西：两种形态都是
同一份重新推导的 `(descriptor, content)` 的确定性函数，比较仍是对权威的逐字节相等，
`reference_id` 里的准入摘要在两种形态下**完全相同**，`canonical_hash(descriptor)` 也相同。
用例 `test_the_pre_f_e3_wire_shape_is_still_verifiable` 覆盖：旧形态可验证、准入键一致、
篡改 `run_id` 仍以原错误码拒绝、篡改 `excerpt` 仍被拒。

**S1（SHOULD-FIX，已修）——`results_kept_verbatim_small` 在 `force_all` 下越权断言。**
原注释写「这些是因为 margin 规则才保持原样的」，但 `force_all` 恰恰放弃 margin，
且未成功 effect 与最新一批根本没进过 `worth_paging`。已把两处 docstring 与常量注释改成
**纯描述**：「原样出发、且小于分页地板的非 control tool 体条数」，不再声称因果。

**N1（已修）** 字节规则服务 token 上界，见 §2.2 末段，新增
`text_tokens` 正收益要求 + 用例 `test_paging_never_increases_the_token_count…`。
**N2（已修）** `SUMMARY_MIN_BYTES` 的下界论证原来靠一次测量（短 `effect_id` 会破），
改成结构论证（excerpt 是 `min(128, body)`，对小 body 永不为空），
用例 `test_no_body_below_the_paging_floor_can_ever_be_paged` 跨 id 长度 20/32/71 直接证性质。
**N3（已修）** `marker is None` → `marker != MARKER`。
**N4（已修）** `pages` 是 1 KiB 页数的**下界**（多字节 body 的页会在码位边界截短），
注释写明。
**N5（已修）** F-E2/F-E3 的 `run_id` 检查不对称，已在 `verify_control_stubs` docstring 里
写明并标注「不要统一」。

---

## 5. 用例与门禁

新增 `backend/tests/execution/test_descriptor_cost_bound.py`，**10 例**：

| 用例 | 断言 |
|---|---|
| `test_bounded_descriptor_costs_about_a_hundred_tokens_whatever_the_body_is` | 6 种 body（1.4 KB–98.8 KB）单条 ≤150 token、≤ main 的 1/3；excerpt ≤128 B 且是原体前缀；`reference_id`/`source_hash`/`page_tool`/`pages` 齐全 |
| `test_no_body_below_the_paging_floor_can_ever_be_paged` | 跨 `effect_id` 长度 20/32/71 × body 200–799 B，`worth_paging` 恒为假 |
| `test_the_pre_f_e3_wire_shape_is_still_verifiable` | 评审 M1：旧形态仍可验证、准入键一致、篡改仍被原错误码拒绝 |
| `test_paging_never_increases_the_token_count_it_is_supposed_to_reduce` | 评审 N1：emoji 体上 `_settled_tool_tokens` 单调不增，且一条都不分页 |
| `test_margin_rule_refuses_a_body_that_paging_barely_shrinks` | 2 098→2 011、2 338→2 010 被拒；边界 1 200/1 199；`force_all` 放弃 margin |
| `test_small_settled_bodies_stay_verbatim_and_the_receipt_says_so` | 12 条 700 B + 1 条 40 KB：只分 40 KB 那条；两个新计数正确；`force_all` 下小体确实让位 |
| `test_summary_is_a_deterministic_function_of_the_body` | 同输入同字节；CJK 边界不破坏确定性 |
| `test_reference_identity_still_binds_every_field_the_wire_no_longer_prints` | 5 个被拿掉的字段任一变化都会改 `reference_id` |
| `test_verify_request_accepts_the_bounded_form_and_rejects_a_tampered_one` | 往返通过；篡改 `content_bytes` / `effect_id` 被拒 |
| `test_incident_shape_of_sixteen_small_searches_fits_once_descriptors_are_bounded` | **事故形状**：16×1 700 B `tool_search` + 6 条已到 F-E2 地板的 `context_page_in` + 最新一批 1 条 + 1 条 4 800 B `context_route`，`effective=26752`、`ratio=1.65`、单 open 组、`INCIDENT_OVERSHOOT=1230`。修前 headroom **−1231**（分页 0 条），修后 **+3479**（分页 10 条） |

事故形状用例在 main 上真的红：把它单独拷到 `dbf967fc` 的一次性 worktree 里跑，停在
`assert facts["budget_headroom"] >= 0` → `-1231`，`paged=0`——full_trim 之后整条有序降级
一个字节都没省下来，正是现场。

### 门禁对比（`backend/.venv`，一次一个 pytest 进程）

命名文件 32 个（`tests/execution/test_control_result_bound.py`、`test_current_tool_pages.py`、
`test_current_tool_megabyte.py`、`test_revoked_scope_terminal.py`、`test_descriptor_cost_bound.py`、
`tests/native/test_a6_verify_control_stubs.py`，加上 `tests/sdk_adapters/` 里引用
`context_authority` / `current_tool_pages` 的全部文件，**按仓库规则排除
`tests/sdk_adapters/test_composition.py`**）：

| | main（`dbf967fc`，一次性 worktree） | `worktree-f-e3` |
|---|---|---|
| 结果 | **41 failed, 335 passed, 4 deselected** | **41 failed, 345 passed, 4 deselected** |
| FAILED 集合 | — | **与 main 逐条相同（diff 为空）** |

41 条红是 main 上既有的（`test_no_recall_gate` 2、`test_s5a_acceptance_matrix` 3、
`test_s5a_milestone_route_loop` 1、`test_s5b_acceptance_matrix` 9、
`test_typed_context_use_primary` 9，以及 `test_context_route_*` / `test_effect_gate*` /
`test_tool_*` 等），与本次改动无关。345 − 335 = 10，正是新增用例。

### 一处夹具改动（必须说明）

`tests/execution/test_current_tool_pages.py::test_same_run_settled_results_get_a_per_result_ceiling_and_page_back_exact_bytes`
在改动后一度变红。原因**不是产品缺陷，是夹具假设过期**：它用
`next(s for s in summaries if len(body) <= 16384)` 取第一条摘要，然后在正文里找
`BOUND_TAIL`。有界 descriptor 之后，该 Run 建场阶段自己那条 1 226 B 的 `tool_describe`
结果也够格被分页了（566×2 = 1 132 ≤ 1 226），而且它排在最前，于是取到了一条**没有**
`BOUND_TAIL` 的正文 → `str.index` 抛错 → 该轮 provider 调用被重投，测试读到了错误的消息。
改法只是把选择条件收窄到「正文里含 `BOUND_TAIL`」，用例意图（一条 <16 KiB 的结算体必须能
逐字节分页读回）一字未改。

---

## 6. 边界与遗留

1. **不是渐近有界，只是把常数降了 3.5 倍。** 单条仍 ~142 token，请求仍随结算结果条数线性
   增长，只是斜率从 ~496 token/条降到 ~142 token/条（约 3.5 倍余量），真正的兜底仍然是
   `_plan_turn_messages` 的 fail-closed。
2. **73 B 的固定包装仍在 excerpt 预算里。** `{"error_code":null,"outcome":"succeeded",…}`
   占掉 128 B 里的 73 B。可以按 `"value":` 的位置取偏移把它跳过（仍是 body 的确定性函数），
   但那会引入对键序/键存在性的依赖。本轮不做，记为 **F-E3-a**。
3. **`legacy_summary` 是一份需要择期清理的兼容读。** 等到确认没有任何跨 F-E3 升级的在飞
   Run 之后可以删；删之前请确认 `provider_invocations` 里不再存在旧形态摘要。记为 **F-E3-b**。
4. **`results_kept_verbatim_small` 是描述性的**，不要当成「margin 规则拒绝了多少条」来读
   （见 §4 S1）。若要真正的因果计数，需要投影器把决策回传，那会打破「回执只数真正发出的
   消息」这条既有原则。记为 **F-E3-c**。
5. **没有做真机验证。** 本轮全部是离线复算 + 单测；一次原生旅程正在 main + `backend/.venv`
   上跑，按约束没有启动第二次原生跑，也没有 `uv sync` / build。
