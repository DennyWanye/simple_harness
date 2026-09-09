# 决策备忘：分页 offset 必须自解释（HM-TO-A6 事件 AF）

- 日期：2026-09-09
- 分支：`worktree-page-offset`（基线 `57ace264`）
- 证据：`.local-test-evidence/2026-09-09/native-a6-run12b/primary-ui-9iyw1map/`
  （`userdata/data/simple-harness-sdk/execution-v6.sqlite3` 末个 Run
  `product-sdk-1a7c0e11…`，24 次 provider 调用；`state.db` 的
  `primary_effect_identities`；`native.log`）。短旅程 ① run12b 第 13 轮，
  模型 `deepseek-v4-flash`，窗口钉 32000。Run 由 `react_termination_limits`
  终止（driver 360 s 超时）。
- 相关前案：`DECISION-F-E3-DESCRIPTOR-COST.md`（描述符的有界形态）、
  `DECISION-SAME-RUN-CONTEXT-BOUND.md`（同 Run 分页）、事件 B（拒绝必须自带
  可执行的下一步）、事件 X-2（`ContextPageInStore` 的字节上限与 TTL）。

---

## 1. 现象

第 13 轮的任务是"把 47 KB 参照件 B 里 `ANCHOR-BETA` 之后那一行原文抄回来"。
模型走通了 F-E3 的读路径：`read_file` 结果超过 16 KiB，按
`primary_settled_effect_v1` 描述符形态进入请求，模型据此调
`context_page_in` 分页读回。

**16 次 `context_page_in`，9 成 7 败，7 次全部是
`error_code=primary_page_offset_invalid` /
`public_message="Requested primary page is unavailable."`。**
同轮还有 `grep` × 3、`tool_search` / `tool_describe` / `tool_activate` 各 3 次，
最终撞上 react 上限。

## 2. 逐次 offset 复盘（本备忘要求的表）

全部 16 次指向同一条引用
`primary-effect-page:v1:bef60a6ea8d99880a02116ed329bd820a72e27cebabf92bbd6c7d24677a660e4:<offset>`，
`source_hash=c2e43c25…`，正文 **48 272 B**，页大小 `PAGE_BYTES = 1024`（**字节**）。

| 轮/序 | 请求 offset | 在 `[0, 48272)` 内 | 落在 UTF-8 码点边界 | 结果 | 该页返回的 `next_reference_id` offset |
|---|---|---|---|---|---|
| 21/0 | 0 | 是 | 是 | 成功 | 1024 |
| 22/0 | 1024 | 是 | 是 | 成功 | 2046 |
| 22/1 | 8192 | 是 | 是 | 成功 | 9216 |
| 22/2 | 16384 | 是 | 是 | 成功 | 17407 |
| 22/3 | 24576 | 是 | **否** | **失败** | — |
| 22/4 | 32768 | 是 | **否** | **失败** | — |
| 22/5 | 40960 | 是 | 是 | 成功 | 41984 |
| 23/0 | 2048 | 是 | **否** | **失败** | — |
| 23/1 | 3072 | 是 | **否** | **失败** | — |
| 23/2 | 5120 | 是 | 是 | 成功 | 6144 |
| 23/3 | 6144 | 是 | 是 | 成功 | 7168 |
| 23/4 | 12288 | 是 | 是 | 成功 | 13310 |
| 23/5 | 20480 | 是 | **否** | **失败** | — |
| 23/6 | 24576 | 是 | **否** | **失败** | — |
| 23/7 | 28672 | 是 | 是 | 成功 | 29695 |
| 23/8 | 32768 | 是 | **否** | **失败** | — |

判定口径与结论：

- **合法 offset 的定义**（`primary_context_pages._excerpt`）：`0 <= offset < content_bytes`
  且 `raw[offset:]` 能作为 UTF-8 解码，即 **落在码点边界上**。页大小单位是
  **字节**，不是页序号、不是行号。
- **没有一次越界**：16 个 offset 全部 `< 48272`。
- **没有一次是"引用失效"**：7 次失败的 `error_code` 全是
  `primary_page_offset_invalid`，不是 `primary_effect_page_not_admitted`。
  事件 X-2 给 `ContextPageInStore` 加的 8 MiB 字节上限与 300 s TTL
  **与本次无关**——`primary-effect-page:` 前缀走的是
  `store.primary_reader`（Host 的公共 audit/effect 事实），根本不经过那个
  请求内的临时 store。descriptor 也一直在请求里（第 20 次调用起可见）。
- **7 次失败的唯一原因是落在码点中间**：正文全是中文，48 272 B 里只有
  **21 227 个（44 %）** 字节位置是码点边界。模型按 1024 / 8192 的**等距步长**
  猜 offset，剩下 56 % 必然被拒。

## 3. 模型为什么这么猜

模型看到的描述符（第 20 次 provider 调用，**545 B**，逐字）：

```json
{"excerpt":"{\"error_code\":null,\"outcome\":\"succeeded\",\"public_message\":null,\"value\":{\"content\":\"# 秋分资料整理 · 参照件 B\\n\\nB-0000",
 "kind":"primary_settled_effect_v1","page_tool":"context_page_in","pages":48,
 "reference_id":"primary-effect-page:v1:bef60a6e…:0",
 "source":{"content_bytes":48272,"effect_id":"effect-7a10baf5…","tool_name":"read_file"},
 "source_hash":"c2e43c25…"}
```

模型看到的拒绝（逐字）：

```json
{"error_code":"primary_page_offset_invalid","outcome":"failed",
 "public_message":"Requested primary page is unavailable.","value":null}
```

于是：

1. 描述符只印 `pages: 48` 与 `content_bytes: 48272`。**没有 `page_size`**，
   offset 的单位无处可查；模型只能从 `48272 / 48 ≈ 1006` 或从第一次成功的
   `0 → 1024` 反推出"步长 1024"。这个反推**是对的**，但推不出"页起点不总是
   1024 的整数倍"。
2. 早先 T6/T8/T11 用 0 / 1024 / 2048 分页成功过——那几份正文的前几页恰好
   在整数倍处对齐，模型因此**误以为等距成立**，这是运气，不是契约。
3. 成功的 9 次其实**都返回了 `next_reference_id`**（真实边界 2046 / 9216 /
   17407 / 13310 / 29695 / 41984），但模型没有跟着这条链走，而是自己按
   1024 / 8192 的等距步长跳；工具说明里也没有一句话说"必须跟着走"。
4. 拒绝只有一句 "unavailable"：**没有页大小、没有页起点清单、没有页数、
   没有下一页**，也不区分"offset 不对"与"引用没了"。模型除了继续换 offset
   猜、再退回 `grep`，没有任何可执行的下一步——直到 react 上限吃掉整个 Run。

**根因一句话**：受理口径（"任意码点边界"）比模型能推断的口径（"page_size 的
整数倍"）宽，而两边的差集只在多字节正文上出现；Host 既没有在描述符里讲清
单位，也没有在拒绝里把可用 offset 讲出来。

## 4. 修复（事件 B 口径：拒绝携带可执行的下一步；披露只走回执）

### 4.1 受理口径一字未改（前提）

任何**落在码点边界且在正文内**的 offset 仍然被接受。这不是保守，是**必须**：
证据里成功的 8192 / 16384 / 40960 都**不在**规范页链上，收窄口径会让已记录的
成功页在 `check_runtime_dependencies` 重放时读不回来。`verify_request`、
`reference()` 的原像、`canonical_hash(descriptor)` 准入键、
`provider_request_fingerprint` 全部未动。

### 4.2 (a) `primary_page_offset_invalid` 带上可执行详情

`PrimaryContextPageUnavailable` 新增可选的 `detail`；`str(exc)` **仍然只是那个
稳定码**（`primary_dependencies` 用它逐字重放比对）。
`current_tool_pages._reject_bad_offset` 在 `_excerpt` 之前判定并抛出：

| 字段 | 含义 |
|---|---|
| `reason` | `offset_negative` / `offset_past_end` / `offset_not_on_character_boundary` |
| `requested_offset` | 这次请求的 offset |
| `page_size` | `PAGE_SIZE = PAGE_BYTES = 1024`（**字节**） |
| `page_count` | 精确页数（走 `page_starts()`，不再是 `ceil` 估算） |
| `content_bytes` | 正文字节数 |
| `valid_offsets` | 有界页起点清单：**前 16 个 + 最后一个** |
| `offsets_listed` | 清单条数（说明它是截断过的） |
| `next_offset` | **本 Run 已准入过的最高一页之后**的下一个 offset；一页都没读过时是 0；已读到结尾时是 `null` |
| `retry_reference_id` | 可直接复制重试的完整 `reference_id` |
| `next_step` | "offset is a BYTE offset … retry with retry_reference_id" |

`next_offset` 的来源是**权威回执**，不是模型可见的请求文本：Host 的
`primary_effect_identities`（不可变、append-only）给出本次调用之前本 Run 的
`context_page_in` 效果顺序，公共 audit/effect 事实给出每个效果的实参与结果；
最多回溯 `MAX_SCANNED_PAGE_EFFECTS = 8` 个，任何一个读不出权威事实就跳过，
读不出来就退回 0（"从头翻"）。**提示永远不改变这次调用的结果。**

详情通过 `public_message` 抵达模型——失败的工具结果只有 `error_code` 与
`public_message` 两个字段能过 `sdk_adapters.tools._result`（`value` 被丢弃）。
形态是 **固定前缀 + 空格 + canonical JSON**：

```
Requested primary page is unavailable. {"content_bytes":48272,"next_offset":0,
"next_step":"offset is a BYTE offset into the source body and must be one of valid_offsets; retry context_page_in with retry_reference_id",
"offsets_listed":17,"page_count":48,"page_size":1024,"reason":"offset_not_on_character_boundary",
"requested_offset":24576,"retry_reference_id":"primary-effect-page:v1:bef60a6e…:0",
"valid_offsets":[0,1024,2046,3070,4093,5117,6141,7165,8189,9213,10237,11261,12285,13307,14331,15354,48107]}
```

超过 `_MAX_HANDLER_PUBLIC_MESSAGE = 2048` 时**整段详情不带**，宁可回退到升级前
那一句，也不给模型半条被截断的 JSON。

### 4.3 (b) 描述符讲清单位与页数

`summary()` 的 wire 形态：`pages`（`ceil` 估算）→ **`page_count`（精确）+
`page_size`（1024，字节）**；页数 ≤ `DESCRIPTOR_OFFSET_LIMIT = 8`
**且**页起点不等于 `range(0, content_bytes, page_size)` 时，附 `valid_offsets`
全量清单。后半个条件是必需的：单字节正文的页起点就是 page_size 的整数倍，
已被前两个字段完全决定，再印一遍纯属白花字节，而 F-E3 的每条描述符 token
上限没有余量。真正需要清单的恰好是本事件的形态——多字节正文。

**字节预算实测**（事故那份 48 272 B 正文，同一 descriptor）：
**564 B / 143 token → 586 B / 149 token**（+22 B / +6 token，+3.9 %）。
`test_descriptor_cost_bound.py` 的两条上界都仍然成立：
`DESCRIPTOR_TOKEN_CEILING = 150`（实测 142，六种体量恒定）、
`< MAIN_DESCRIPTOR_BYTES // 3 = 653`（实测 567）。

**跨升级兼容**：`legacy_bounded_summary()` 只读不写地接受事件 AF 之前的 F-E3
形态（`pages`），与 `legacy_summary()`（F-E3 之前）并列被 `verify_request` 接受。
理由与 review M1 完全一致：`admitted_current_page` 校验的是**持久化的**父请求，
没有它，升级瞬间在飞的 Run 会因为一次纯措辞变更永久 fail closed。

### 4.4 (c) 引用真的不在了 → 不同的稳定码

原先 `primary_effect_page_not_admitted` 同时是"引用不在本请求里"的唯一出口。
现在改为 **`primary_page_reference_unavailable`**，`detail.next_step` 给出
可执行的下一步："重跑产出该结果的工具，或从**本次请求里**的
`primary_settled_effect_v1` 摘要复制 `reference_id` / `source_hash`"。
换 offset 重试对这种情况永远不会成功，必须换一步走。

**重放兼容**：`REJECTION_CODE_ALIASES`（冻结、单向）把新码映射回它取代的旧码，
`rejection_code_matches()` 供 `primary_dependencies` 比对。升级前记录了
`primary_effect_page_not_admitted` 的在飞 Run 不会被判
`primary_page_rejection_mismatch`。方向单一，语义没有放宽。

### 4.5 (d) 工具说明写明 offset 单位

`CONTEXT_PAGE_IN_SCHEMA["description"]` 追加：

> The ":<offset>" tail of a reference_id is a BYTE offset into the body, not a
> page index and not always a multiple of page_size; never invent one — use the
> summary's valid_offsets, a page's next_reference_id, or a rejection's
> retry_reference_id.

前半段按等义压缩，把新增说明摊回原量级：134 → 175 token（+41）。
**参数说明一个字都没加**——`parameters` 进
`tool_schema_tokens`，而 8192 档的受保护预算没有余量
（`test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn`：
先前把 17 token 的参数说明加进去就直接把 5342 顶过 5325）。

## 5. 未动的东西（显式声明）

- `verify_request` 的校验语义、`reference()` / `canonical_hash(descriptor)` 准入键；
- `_excerpt` 的受理口径与页大小 `PAGE_BYTES = 1024`；
- `provider_request_fingerprint`、`result_hash`、审计头；
- `public_message` 的**前缀**逐字不变（`primary_dependencies` 由全等改为前缀判定，
  升级前只有前缀的记录照样通过）；
- F-E2 的 `primary_control_result_elided_v1` 与 `verify_control_stubs`；
- 事件 X-2 的 `ContextPageInStore` 上限（本事件与它无关，未触碰）。

## 6. 用例

新增 `backend/tests/execution/test_page_offset_guidance.py`（12 例）：事故形状
按构造复现（44 % 边界率、页起点非整数倍、页链无重无漏覆盖全文）、描述符字段
（含"page_size 已决定的清单不重复印"）、三种 wire 形态都仍可验证、三种拒绝形态
（码点中间 / 越界 / 引用不在本请求）、`next_offset` 取自权威回执且真的可读、
详情确定性、`public_message` 前缀与 2048 上限、工具说明。

改 `backend/tests/execution/test_descriptor_cost_bound.py` 1 处断言
（`pages` → `page_count` + `page_size` + 清单省略规则）。

## 7. 遗留（followup）

- **AF-F1**：受理口径仍宽于规范页链（任意码点边界都收）。收窄可以让 offset
  完全自解释，但会破坏已记录页的重放，需要一次带版本位的 wire 升级，本轮不做。
- **AF-F2**：`next_offset` 只回溯 8 个 `context_page_in` 效果。一个 Run 读了
  超过 8 页且乱序时，提示可能给出偏小的 offset（仍然合法可读，只是不是最优）。
- **AF-F3**：历史页路径（`primary-tool-page:v1:`，`primary_context_pages.
  admitted_page`）本轮未加详情，其拒绝仍只有稳定码。原生证据里尚未出现该形态
  的连撞，等出现再做。
