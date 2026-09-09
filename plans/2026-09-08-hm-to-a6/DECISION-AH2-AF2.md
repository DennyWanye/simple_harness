# 裁决 AH-2 / AF-2：引用解析失败必须给出结构化、可执行的拒因

- 日期：2026-09-09
- 分支：`worktree-effect-page-ref`（基线 `4301c72a`）
- 事件：HM-TO-A6 第 12 次整跑 · T17（Run `product-sdk-71b4ca76…`）
- 证据：`.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/`
  —— `userdata/data/simple-harness-sdk/execution-v6.sqlite3` 的 `execution_effects`；
  `native.log` / `logs/backend.log` 的
  `product_tool.failed tool=context_page_in code=primary_effect_page_reference`（×2）
- 代码：`backend/deskpet/execution/current_tool_pages.py`、
  `backend/deskpet/execution/primary_context_pages.py`、
  `backend/deskpet/tools/context_page_in_tools.py`
- 测试：`backend/tests/execution/test_page_reference_guidance_af2.py`（新增 17 例）、
  `backend/tests/execution/test_page_offset_guidance.py`（更新 2 例）
- 前置：[DECISION-AF-PAGE-OFFSET-GUIDANCE](DECISION-AF-PAGE-OFFSET-GUIDANCE.md)、
  [DECISION-AG-PAGE-SIZE-WRAP-UP](DECISION-AG-PAGE-SIZE-WRAP-UP.md)、
  [DECISION-AH-GREP-FILE-PATH](DECISION-AH-GREP-FILE-PATH.md)、
  [DECISION-F-E3-DESCRIPTOR-COST](DECISION-F-E3-DESCRIPTOR-COST.md)、
  [DECISION-TOKEN-ESTIMATOR](DECISION-TOKEN-ESTIMATOR.md)

---

## 一、现象与真因

T17 那一轮里模型连挂四次，`RUN-12-ATTEMPT.md` 记为 AH-2 与 AF-2：

| 工具 | `error_code` | `public_message` |
|---|---|---|
| `context_route`（带 `goal` 参数） | `invalid_tool_arguments` | 「arguments do not match its schema」（已可归因） |
| `write_file` ×2 | `tool_failed` | `Tool execution failed.` |
| `context_page_in` ×2 | `primary_effect_page_reference` | `Requested primary page is unavailable.` |

### 1.1 AF-2 的真因：**丢掉了 offset 尾巴，而拒绝不肯说**

两次 `context_page_in` 的实参逐字是：

```
{"reference_id":"primary-effect-page:v1:59f03e516e650241a6c151db6722aa8f4bb2251c…",
 "source_hash":"c9773807553dc8174a16b0a663d25f7f48c4a7b052e7661e88a6850f3e2b5430"}
{"reference_id":"primary-effect-page:v1:4631c6d0ece7184adc08a0f71bf5e2e547bdcf90…",
 "source_hash":"c6644209fa1258b4d5e8e2abf4caa0c2bc1994572d82d898e8b785f87b1815b2"}
```

引用只有「前缀 + 64 位摘要」，**没有 `":<offset>"` 尾巴**。
`admitted_current_page` 里 `ref.removeprefix(PREFIX).split(":")` 只解出一段 →
`ValueError` → `PrimaryContextPageUnavailable("primary_effect_page_reference")`，
**不带 `detail`**，因此 `_primary_page_message(None)` 逐字回到那句
「Requested primary page is unavailable.」。

两件事合起来才构成这次事故：

1. **摘要里印的 `reference_id` 本来就带 `:0`**（`_wire_summary` 走
   `reference(descriptor)`，即 `PREFIX + digest + ":0"`）。模型是**重构**了引用而
   不是照抄——这一点事件 AF 已经在工具说明里写过「never invent one」，但它禁的是
   「发明 offset」，没有禁「**丢掉** offset」。
2. **拒绝里没有任何可据以自纠的信息**：不说少了什么、不说这条摘要还在不在、不说
   现在能用哪条引用。模型于是转去 `tool_search` 空转，T17 的预算就此打光。

同一条路径上还有两个同样不透明的分支（本次一并结清）：

- `digest` 解析出来了但不在本次请求里 → 事件 AF 给过一个独立稳定码
  `primary_page_reference_unavailable`，但只有一句劝告，**没有替代品**；
- 引用既不是主页面引用、也不在临时引用表里 →
  `context_page_in_tools` 只回 `{"ok":false,"error":"reference_stale","retriable":true}`，
  过期与「压根不是引用」（例如把 `recall-item:<id>:1` 当页引用）挤在同一个词里。

### 1.2 AH-2 的真因：**已由事件 AH 就地覆盖，本次不再改 `write_file`**

T17 的 `write_file` 实参是
`{"path":"/ws/Task-488d567e8533/goal_目标说明_0000-0106.txt","content":"目标条款 0000：…"}`。
用真 handler 离线复现，它返回的是

```json
{"ok": false, "error": "OSError: [Errno 30] Read-only file system: '/ws'", "hint": "…", "examples": […]}
```

—— 一个**没有 `error_code`** 的信封。事件 AH 之前 `sdk_adapters/tools.py::_result`
的默认分支把它压成 `tool_failed` + "Tool execution failed."，而 `ToolResult.failed`
不带 `value`，拒因两边都到不了。事件 AH 已经把默认分支改成「异常类名 + 净化后的
自然语言」，同一个信封现在产出

```
OSError: [Errno 30] Read-only file system: '<path>'
```

并同时进 `public_message` 与 `product_tool.failed` 的 `reason=` 字段。
**AH-2 因此就地结清**，本次只补一条钉住它的用例
（`test_the_same_turns_write_file_failure_is_already_attributable`），不再动
`write_file` 本身——它的守卫是对的（`/ws` 真的只读），坏的只是信息蒸发。

## 二、裁决

### 2.1 三个引用解析失败分支各有结构化原因（稳定码一个字符未改）

`PrimaryContextPageUnavailable.detail` 的既有纪律照旧：`str(exc)` 永远只是稳定码，
详情只在**当次**拒绝里渲染进 `public_message`，不参与重放比对、不参与任何指纹、
**不回显模型写的文本**。新增的原因词汇：

| `reason` | 触发 | 携带的下一步 |
|---|---|---|
| `page_reference_missing_offset` | 摘要认得这条来源，缺的只是 `":<offset>"`（事故的确切形态；`:abc` / `:0:0` / `:+0` / `:` 同理） | `retry_reference_id`＝该来源第 0 页的完整引用 |
| `page_reference_unknown` | 引用解析不了且摘要也认不出这个 digest | `available_reference_ids`（本请求真正可用的**最新** 3 条）＋ `references_available` |
| `page_reference_stale` | 引用形态合法，但这条页在本次请求里已经不在 | 同上（原 `reason="reference_not_in_this_request"` 并入此名，**稳定码 `primary_page_reference_unavailable` 未变**） |

三份清单全部来自**权威面**：`read_primary_effect_page_facts` 取这次调用的父请求，
`verify_request` 把请求里的每一条摘要按公共审计/效果事实逐字节重建，再投影成
`reference(descriptor)`。不是从模型写的请求文本里抄回去的，因此「列出来的每一条都
真的读得回字节」是可证的性质（用例逐条读了一遍）。

`_request_references` 只走拒绝路径，且任何一步读不出权威事实就返回空清单——与
`_admitted_next_offset` 同一条纪律：**提示永远不得改变这次调用的结果**。
`digest not in found` 那一支直接复用已经重建好的 `found`，不重跑 `verify_request`。

### 2.2 offset 拒绝公布**有效范围**

`_reject_bad_offset` 的详情新增 `valid_offset_range = [0, content_bytes - 1]`。
既有的 `offset_negative` / `offset_past_end` / `offset_not_on_character_boundary`
三个原因**保留不动**——它们比一个合并的 `page_offset_out_of_range` 更精确，而任务书
真正要的「有效范围」由新字段给出。两者的关系写进了注释与用例：
`valid_offsets` 是范围里那个**可执行子集**（页链上的起点），范围本身是受理口径的
完整边界（任何落在 UTF-8 码点边界上的 offset 都被受理，这一条自事件 AG 起一字未改）。

### 2.3 历史页引用（另一个前缀）用同一套词汇

`primary_context_pages.admitted_page` 的两条分支同样带上详情：
`primary_page_reference_invalid` → `page_reference_unknown`（静态形状说明，并直接
点名「不要传 `recall-item:<id>:1`」），`primary_page_not_admitted` →
`page_reference_stale`。这里**不列**可用引用：该路径上没有一份已重建好的请求投影，
为了做提示重跑 `verify_history_projections`（异步、要读 db）不值得（见第四节）。

### 2.4 临时引用表：过期 / 召回片段 / 从来就不是引用

`context_page_in_tools` 的 `ref is None` 分支不再只回一个词。稳定码 `error`
（`reference_stale`）与 `retriable` 逐字不变——审计行与既有比对都靠它——新增的只是
同一个信封里的 `detail`：

- `recall-item:` 开头 → `page_reference_unknown` +「这是记忆条目 id，正文已经在
  `context_route` 结果里，不要 page it in」；
- 32 位十六进制（`ContextPageInStore` 的 uuid4 形态）→ `page_reference_stale` +
  「TTL 到期或被淘汰（事件 X-2 的字节/条数上限），重跑产出它的工具」；
- 其余 → `page_reference_unknown` +「引用必须从本请求里逐字抄」。

`available_reference_ids` 由新增的 `ContextPageInStore.live_reference_ids` 给出，
**严格按 session / request / scope 过滤**——这三者正是 `is_active` 的准入判据，所以
它绝不会把别的请求或别的 scope 的引用透给模型。其余分支
（`reference_scope_denied` / `reference_hash_mismatch` / `context_scope_missing`）
的信封一个字节没动，用例逐字钉住。

### 2.5 8192 档预算：新增的模型可见文本是**换来**的，不是加上去的

`test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn` 实测余量
**恰好为 0**（PERSONA 1122 + schema 1165 = 2287，加上固定的 variable 3038 = 5325，
等于 `effective_input_budget(8192)` = 5325）。因此：

- **参数说明一个字未加**（它进 `tool_schema_tokens`，直接计入这道门）；
- 工具**说明**里把「never invent one」换成「never invent **or drop** it」、并把
  `The ":<offset>" tail …` 改写成 `A reference_id always ends in ":<offset>" …`
  （+2 token），代价由删掉上一句冗余的「, and fails」（本就与前面的「nothing else
  is accepted」重复）抵掉：**875 B / 219 token → 872 B / 218 token，净 −1**；
- 所有新增详情都只在**失败路径的 `public_message`** 里出现，不进 PERSONA、不进
  schema，因而完全不占这道门的预算。上界仍是
  `sdk_adapters.tools._MAX_HANDLER_PUBLIC_MESSAGE`（2048），最坏形态
  （13+ 页正文 + 17 个页起点 + 有效范围 + 重试引用）实测仍在里面，有用例钉住。

新增 `DESCRIPTION_TOKENS_BEFORE_AF2 = 219` 作为回归闸：说明可以改措辞，不许变贵。

## 三、验证

只跑定向测试，**未整目录跑 `backend/tests/sdk_adapters`**（会挂起）。

| 范围 | 结果 |
|---|---|
| 收尾整合跑：`test_page_reference_guidance_af2.py`（新增 17 例）+ `test_page_offset_guidance.py` + `tests/test_context_page_in_tools.py` + `tests/sdk_adapters/test_token_estimator_calibration.py`（含 8192 档预算门）+ `test_current_tool_megabyte.py`（含 8192 档真装配整跑）+ `test_current_tool_pages.py` + `test_primary_context_pages.py` + `test_primary_page_in_sources.py` + `test_descriptor_cost_bound.py` + `test_context_budget_wrap_up.py` + `test_control_result_bound.py` + `tests/quality/test_audit_coverage.py` + `tests/sdk_adapters/test_tool_catalog.py` | **174 passed**（85 s） |
| `tests/execution/test_primary_dependency_v2.py` + `test_primary_history_outbound.py` + `test_primary_history_tool_calls.py` + `test_revoked_scope_terminal.py` + `test_scope_disclosure_runtime.py` + `tests/native/test_a6_verify_control_stubs.py` + `test_a6_verify_a6_4.py` | 63 passed / **4 failed** |
| `tests/test_provider_runtime_refresh.py` | 9 passed / **2 failed** |
| `tests/test_execution_build_manifest.py` | 8 passed / **1 failed** |

**7 个 failed 全部是基线既有**：在基线 `4301c72a` 的一次性 detached worktree 上逐条
复跑，失败集合逐行相同（`test_late_history_denial_is_failed_while_sent_ambiguity_stays_unknown[sent_unknown]`、
`test_resume_source_or_explicit_legacy_gap_with_actual_file_terminal[memory_suppressed-*]` ×3、
`test_real_product_sdk_production_composition_starts`、
`test_human_epoch_composition_registers_three_authorities`、
`test_checked_manifest_is_canonical_and_current`）。
`deskpet/tools/execution_build_manifest.json` 不覆盖本次改动的三个文件（grep 0 命中），
且它在基线上就已经是 stale 的，故本次未重新生成。

新增用例走的是**真装配**：真 sqlite `primary_effect_identities`、真
`source_content` / `verify_request` 权威重建、真 `admitted_current_page` /
`admitted_page` / `ContextPageInStore` handler，复用
`test_page_offset_guidance._scenario` 的 CJK 正文场景（与事故里 47 KB 参照件同形状）。
覆盖：事故的确切形态（丢尾巴）、四种不可解析的尾巴、未知 digest、清单有界、
过期引用给替代品、offset 越界公布范围、详情不回显模型文本、权威读不出时不改变结果、
详情确定且不越 2048、历史页前缀、临时引用表三种形态与跨 session/request/scope 隔离、
以及 AH-2 的 `write_file` 归因。

## 四、残余风险

1. **未跑原生旅程**（依约束不启动原生应用，本轮有整跑在飞）。真人复验就是
   HM-TO-A6 下一次 T17：`context_page_in` 抄漏尾巴时应当**一次**拿到
   `retry_reference_id` 并读回正文，而不是两次不透明拒绝 + `tool_search` 空转。
2. **历史页路径不列可用引用**（§2.3）。它只给静态形状说明。要给出「现在可用的历史
   页引用」需要在拒绝路径上重跑 `verify_history_projections`（异步、读 db、逐条重建
   投影）——那是一次真实的成本，且事故形态发生在当前 Run 页前缀上。属独立改动。
3. **`available_reference_ids` 是「本请求现在可用」的快照，不是承诺**。下一轮装配是否
   还把同一条摘要放进请求，由另一条预算规则在另一轮决定（与 F-E2 的
   `CONTROL_REFETCH_HINTS` 刻意不承诺同源）。措辞已避免把它讲成保证。
4. **`reason` 词汇变了一个词**：`reference_not_in_this_request` → `page_reference_stale`。
   稳定**码**没变，`REJECTION_CODE_ALIASES` 与 `rejection_code_matches` 一字未动，
   `primary_dependencies` 的重放比对（只看 `error_code` 与 `public_message` 的固定
   前缀）因此原样成立；受影响的只有事件 AF 那条用例的一行断言，已同步更新。
5. **工具说明的 −1 token 是按 `text_tokens` 量的**，与真实分词器只在同一量级上一致
   （这正是 `DECISION-TOKEN-ESTIMATOR.md` 的口径）。8192 档余量为 0 的现实没有变，
   下一次任何模型可见文本新增仍然必须先找到等量的压缩空间。
6. **清单只取 3 条**（两侧同值 `MAX_LISTED_REFERENCES`），且是**最新的 3 条**
   （请求投影从尾部取、临时引用表倒序遍历，都有用例钉住）。可用引用超过 3 条时，
   更旧的那些只能靠 `references_available` 这个计数被知道，模型拿不到它们的 id。
   这是有意的字节取舍：模型要的是**一条能用的**引用，不是一份目录。
