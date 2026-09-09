# 事件 AG：翻页页大小 1024 → 4096，以及「收尾」为什么在最该触发的那一轮没触发

- 日期：2026-09-09
- 证据：`.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/`
  （HM-TO-A6 第 12 次尝试，第 6 轮，失败 Run `product-sdk-015ad2fe32f16f34743725fba3d57929db484ee0e81910c84306b0c8eb72defd`）
- 相关既有决策：事件 AF（offset 自解释）、F-E2（control 结果省略）、F-E3（描述符自身的成本）、
  事件 O（有序降级）、事件 Z（预算收尾 wrap-up）

## 1. 事故

第 6 轮用户只问一句：一份 40 003 B 参照件的**「标题和总行数」**。

Run 的效果序列是 `context_route`、`tool_describe`、`tool_activate`、`context_route`、
`read_file`（成功，40 003 B），然后是 **13 次全部成功的 `context_page_in`**，offset 依次为
0、1024、2046、3069、…、12278——模型老老实实顺着事件 AF 给的 `next_offset` 链一页页翻，
1 KiB 一页，中文约 300 字。翻到 12278 时进度还不到三分之一，第 19 次装配就死了：

```
sdk_context_budget_exceeded planned=27202 effective=26752 protected=8403
tool_schemas=6465 groups=1 ratio=1.65 protected_messages=1938
open_group=18799 full_trim=True
→ react_termination_limits，Run FAILED，用户一个字也没拿到
```

`run_context_snapshot_receipts` 里第 14–18 序数的 `budget_headroom` 是
**1078 / 95 / 2316 / 1105 / 373**，而这五轮的 `wrap_up_injected` 全是 **0**。

两个独立缺陷叠在一起造成这次全损：翻页太慢（每页太小、描述符答不了那一问），以及**收尾没触发**。

## 2. 缺陷一：1 KiB 的页在这个问题上必然翻爆预算

40 003 B ÷ 1024 ≈ **40 次** `context_page_in`。每一次都在 open group 里留下一条约 1.8 KB 的
control 结果；F-E2 只能把**较旧**的那些省略成约 160 token 的 stub，最新的一条永远保留。40 次
翻页本身就超过 32 K 窗口，而问题只是「标题和总行数」。

### 2.1 页大小 1024 → 4096

`primary_context_pages.PAGE_BYTES = 4096`。同一份正文从约 40 页降到 **10 页**（−75 %），
单页约 1365 个中文字。

为什么不是 8192：单页 8 KiB 的中文正文按 `text_tokens` 是约 2730 token，而**最新一次**翻页的
control 结果按 F-E2 是不可省略的，等于让一条消息常驻吃掉 26 752 有效预算的 10 % 以上；4096 是
「页数降到四分之一」与「最新一页仍然付得起」之间的平衡点。

### 2.2 准入身份与重放：一个字节都没变

- **引用不含页大小**。引用是 `PREFIX + canonical_hash(descriptor) + ":" + <字节 offset>`，
  descriptor 里既没有 `page_size` 也没有 `page_count`，所以升级前持久化的每一个引用照样解析、
  照样命中同一个准入键。
- **受理口径一字未改**。`_excerpt` / `_offset_reason` 仍然只要求 offset 非负、在正文内、落在
  UTF-8 码点边界上。事故里已记录的成功页（8192 / 16384 / 40960 之类，都不在新页链上）仍然读得回来。
  `page_bytes` 只决定**返回多少字节**，且在准入、顺序与 hash 校验全部完成之后才被用到。
- **线上形态的只读兼容**。升级瞬间还在飞的 Run，其已持久化的父请求里存的是按 1024 渲染的摘要，
  其已记录的 `context_page_in` 结果是 1024 B 的页；`primary_dependencies.check_runtime_dependencies`
  每一轮都会重算并逐字节比对。因此：
  - 新增 `current_tool_pages.legacy_af_summary`（页大小 1024、无 `text_stats`），
    `verify_request` 现在接受 `summary` / `legacy_af_summary` / `legacy_bounded_summary` /
    `legacy_summary` 四种形态。理由与 review M1 完全一致——一次纯容量升级不得把 Run 判成
    `summary_mismatch`；四种形态都是同一对重新推导出的 `(descriptor, content)` 的确定函数，
    准入键 `canonical_hash(descriptor)` 完全相同，不放宽任何权限。
  - `admitted_current_page` / `admitted_page` 增加 `page_bytes` 形参；`primary_dependencies`
    在新页大小对不上时**才**按 `LEGACY_PAGE_SIZE` 重算一次，稳态零成本。
  - `legacy_bounded_summary` 的 `pages`、`legacy_summary` 的 `_excerpt` 都钉死在 1024。
  - **实测验证**：把 `main` 的 `current_tool_pages.py` 以 1024 常量载入，在 7 种正文形状上比对，
    `legacy_af_summary` / `legacy_bounded_summary` / `legacy_summary` 三种形态与 main 的输出
    **逐字节相同**。
- **start 快照里的两处摘录与页大小解耦**。`primary_tool_result_summary_v1`（历史结果摘要）与
  `primary_tool_arguments_summary_v1`（超大实参摘要）必须与**不可变的** start 快照逐字节相等
  （`verify_history_projections`），所以它们改用独立的冻结常量 `EXCERPT_BYTES = 1024`；页大小
  再变，这两处也一个字节都不许动。历史页描述符（`primary_tool_result_summary_v1`）因此**没有**
  加统计字段——它一旦改形，升级前做的每一份 start 快照都会永久 fail closed。

### 2.3 描述符补上「标题和总行数」

事故那一问之所以只能靠翻页回答，是因为描述符里唯一的尺寸字段 `content_bytes` 量的是 **JSON 封套
的字节数**——既不是行数也不是字符数。

`summary()` 现在对**文本结果**多印两件事，都是同一份公共正文上 O(len) 的确定函数（不碰任何私有
字段、不引入任何新权威）：

- `text_stats = {"line_count", "char_count"}`。`line_count` 按 `\n` 数、末尾换行不多算一行
  （口径同 `wc -l`）。
- `excerpt` 换成正文的**首行**（有界）。

「文本载荷」的判据窄而确定：公共正文解成 JSON 后，`value` 本身是字符串，或 `value` 里
`content`/`text`/`body`/`output`/`stdout` 中第一个是字符串的字段。

**与任务书原口径的偏离（实测裁决）**：原计划是「正文以 Markdown 标题开头时才把首行当 excerpt」。
实测否掉了这个限制——不以标题开头的文本结果会**同时**付掉 128 B 的 JSON 前缀和 `text_stats`，
描述符涨到 665 B / 166 token，越过 F-E3 的 150 token 上限。改成「文本载荷一律印首行」后同一形态
是 573 B / 143 token，而且首行比那段固定封套信息量更高（128 B 前缀里前 73 B 是
`{"error_code":null,"outcome":"succeeded","public_message":null,"value":{"`，只剩约 55 B 真载荷）。

**两项配套的字节控制**（都由实测驱动）：

- `TITLE_EXCERPT_BYTES = 96`（小于 `SUMMARY_EXCERPT_BYTES = 128`）。`text_tokens` 给中日韩每字
  算一个 token，96 B 中文 = 32 字 = 32 token，与它取代的 128 B ASCII 前缀（128/4）等价——**标题
  永远不会比它取代的 excerpt 更贵**，描述符的 token 上限因此不依赖正文用哪种文字。
- `DESCRIPTOR_OFFSET_LIMIT` 8 → **4**。页大小提到 4096 之后「≤8 页」会覆盖到 32 KB 的正文，
  offset 也从 4 位数变 5 位数，叠上 `text_stats` 就顶破上限；收到 4 之后它仍然覆盖到 16 KB 正文
  （旧口径是 8 KB，清单反而更宽），字节却回到原量级。只读兼容形态用冻结的
  `LEGACY_DESCRIPTOR_OFFSET_LIMIT = 8`。

### 2.4 描述符成本实测（`text_tokens`，F-E3 上限 150 token）

| 正文形态 | 事件 AG 后 | 事件 AF（升级前） |
|---|---|---|
| 事故形态 40 KB 中文、首行是标题 | **512 B / 129 tok**，10 页 | 581 B / 146 tok，40 页 |
| 13 KB 中文、无标题 | 527 B / 132 tok | 580 B / 145 tok |
| 10 KB 中文、印页起点清单 | 560 B / 141 tok | 580 B / 145 tok |
| 首行 400 字中文（最坏形态） | 585 B / **148 tok** | 580 B / 146 tok |
| ASCII 8 KB | 503 B / 126 tok | 580 B / 145 tok |
| 非文本（`tool_search` 6.6 KB / 98 KB） | 578–580 B / 145 tok（**逐字节不变**） | 同左 |

最坏 148 token，仍在 150 之内；事故那一形态反而比升级前**便宜 17 token**，并且不用翻任何一页
就能回答「标题和总行数」。`context_page_in` 的工具说明同步点破了这一点。

## 3. 缺陷二：收尾（wrap-up）为什么在最该触发的那一轮没触发

事件 Z 的收尾机制本身是好的：`_plan_turn_messages(wrap_up_message=…)` 会把 open group 的因果链
交出去（只留前导 USER 消息），把一条约 120 token 的确定性 SYSTEM 指令作为受保护材料注入，收据里
记 `wrap_up_injected` 与 `open_group_items_dropped`。

**根因不在 `_plan_turn_messages`，而在调用方 `ProductRunContextAuthority` 的两处门。**
证据里这个 Run 只收尾过一次：

```
sdk_context_budget_wrap_up run=product-sdk-015ad2fe… turn=13 headroom=1003
threshold=1200 open_group_items_dropped=0
```

- **门 1：收尾判定整个嵌在「有界计划已经装不下」这个分支里。** 主循环是
  `if assembly_facts["budget_headroom"] < 0:` 才进入 force_all → 历史清零 → 收尾判定。第 14–18 轮
  最终余量 1078 / 95 / 2316 / 1105 / 373——四轮都在一个 react 步（1200）以下，**但都装得下**，
  于是这个分支根本不进，模型被照常邀请「再翻一页」。
- **门 2：「每个 Run 只收尾一次」的闩，被一次什么也没降级的劝告用光了。** 第 13 轮余量 1003 是
  **正数**：那一轮装得下，收尾只是一句劝告（`open_group_items_dropped=0`），却把这个 Run 唯一的
  收尾额度消费掉。模型没听、继续翻页；第 19 轮真的超了 450 token 时，`_should_wrap_up` 因为闩再次
  返回 False，走 else 分支原样 raise。**一次没有产生任何降级的劝告，换掉了后面那次本该救命的收尾。**

所以任务书里列的三种猜测（只在 `groups_trimmed_for_budget` 发生时探、只在 `full_trim` 且历史组
> 0 时探、溢出来自 open group 时跳过）都不是答案——真正的答案是**进入条件太晚 + 一次性闩被劝告
消耗**。

### 3.1 裁决

1. **收尾判定移出 `headroom < 0` 分支**，改成在**所有降级步骤跑完之后**对最终计划判一次：
   计划仍然装不下，**或**余量已不够下一个 react 步（< 1200），就收尾。
2. **去掉「每个 Run 一次」的闩**。收尾指令是确定性的、约 120 token 的 SYSTEM 消息，每轮重发既幂等
   又便宜；真正的界由 react 的 25 轮 / 600 s 上限提供，不需要一道进程本地的闩。何况那个闩是
   **进程本地**状态，等于让第 N 轮的请求取决于本进程此前处理过哪些轮——本身就是重放上的隐患；去掉
   之后同一轮的计划只由这一轮的输入决定。`_wrap_up_runs` 保留为纯观测集合。
3. **收尾自身的成本被算进降级序**。收尾指令把一个本来刚好装下的计划顶出去时，才补上「历史清零」
   这一步（而不是反过来先交历史）；先按当前的 `full_trim` 探一次，不够再升到 `full_trim=True` 探
   一次，仍然不够才 raise——raise 时带上收尾后的完整分解。
4. **装不下的那一支照旧动用 open group 的因果链**：只保留前导 USER 消息（这一轮用户真正问的话），
   assistant 回声、分页 descriptor、省略通知全部交出去，`wrap_up_injected=1` 与
   `open_group_items_dropped` 进收据。任务书允许再保留「最后一对 assistant/tool」，本次**没有**
   采用——多留一条 assistant 工具调用就必须连带留全它的所有 tool 结果，否则请求因果上非法；只留
   前导 USER 更保守，且已经足够让模型作答。

### 3.2 事故重放

第 19 轮：全降级跑完仍差 450 token → 收尾必须还能触发 → open group 的 31 条全部交出 → 装得下，
模型带着用户那一问和它已经读到的东西作答，而不是整轮丢失。

## 4. 测试

单进程、点名文件，`50 passed`（改动前 39 例：35 passed + 4 failed）。

| 文件 | 例数 | 本次新增/改动 |
|---|---|---|
| `tests/execution/test_page_offset_guidance.py` | 16 | +4：页大小升级不动受理口径 / 引用与准入摘要无页大小 / 升级前记录的页按旧页大小逐字节重放 / 40 KB 从 ~40 页降到 10 页；改 2：描述符清单分支按新页大小重新取样（60、150、520 行）、页起点清单的「前 16 + 末 1」有界性改用 800 行（79 KB / 20 页）才钉得住 |
| `tests/execution/test_descriptor_cost_bound.py` | 15 | +5：文本结果不翻页即可答「标题和总行数」/ 标题 excerpt 永不比它取代的前缀更贵 / 非文本正文逐字节不变 / 事故正文 40 页→10 页 / 升级前线上形态仍是正文的确定函数 |
| `tests/execution/test_context_budget_wrap_up.py` | 7 | +2：事件 AG 的 13 次翻页 open group 在无收尾时 raise（组成复现）、**一次正余量劝告之后第 19 轮仍然收尾并装下**；改 1：阈值用例从「每 Run 一次」改成「每一轮需要就触发」 |
| `tests/execution/test_current_tool_megabyte.py` | 3 | 改 1：4096 档现在收尾**两次**（第 1 轮 headroom=151 纯劝告、0 条被裁；第 2 轮 headroom=−124 真降级、2 条被裁），并记录 `open_group_items_dropped` |
| `tests/execution/test_current_tool_pages.py` | 3 | 无改动 |
| `tests/execution/test_control_result_bound.py` | 6 | 无改动 |

**main 上的红**（一次性 throwaway worktree，只拷 `test_context_budget_wrap_up.py`，跑完即删）：
`2 failed, 5 passed`——`test_event_ag_still_wraps_up_after_an_earlier_advisory`
（`_should_wrap_up('product-sdk-015ad2fe', -451)` 返回 False）与
`test_threshold_is_one_react_step_and_fires_on_every_turn_that_needs_it`。这正是事故的死因。

另外静态修正、但按本次「只跑点名文件」的约束**未运行**：
`tests/execution/test_primary_context_pages.py` 的 UTF-8 分页 oracle（`<= 1024` → `<= PAGE_BYTES`
并补上 import）。该 oracle 的性质已用同样的正文在 pytest 之外单独跑通（5 页、首尾相接不重不漏、
五种非法 offset 全部抛错）。

## 5. 不做

- **不给历史页描述符（`primary_tool_result_summary_v1`）加统计**：它必须与不可变的 start 快照
  逐字节相等，改形会让升级前做的每一份快照永久 fail closed。
- **不改 `_offset_reason` / `_excerpt` 的受理口径**：任何收紧都会让已记录的成功页读不回来。
- **不在收尾时保留「最后一对 assistant/tool」**：见 §3.1 第 4 条。
- **不动 `observability/memory_probe.py` 与 `scripts/native/twoflow_*`**（其它 agent 的区域）。
