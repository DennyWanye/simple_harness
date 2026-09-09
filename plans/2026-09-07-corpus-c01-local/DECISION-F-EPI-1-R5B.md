# 裁定：F-EPI-1（episode 本地化时间字段）、F-ETR-8（R5b）与 F-OBS-2（死列）

> 来源：`RUN-C04-RERUN-2-REVIEW.md` §八缺陷 1／2／3／4、§十 followup 表；
> 前情裁定 `DECISION-F-C04-1-F-ETR-7.md`、`DECISION-EXTRA-TYPE-RATE.md`。
> 分支：`worktree-corpus-epi-time`（基线 `53523794`，工作树 `.claude/worktrees/corpus-epi-time`，**未并入 main**）。
> 语料记录（`CORPUS-CUMULATIVE-2026-09-08.md`、`RUN-C04-RERUN-2-REVIEW.md`）本轮不改写，由重跑批次回写。

---

## 0. 结论先行

1. **F-EPI-1（同时消掉 F-C04-2）**：召回片段新增与 `trigger_local` 对称的
   **`occurred_local`**，由 Host 渲染、挂在 SDK payload **旁边**（不进 payload、不改 `payload_hash`）。
   精度不是新造的字段，而是 **SDK 自己的有效时间区间 `[occurred_start, occurred_end]`**
   ——那是 episode 唯一被内容哈希覆盖的时间通道（§1.2）。
   `undated` 渲染为**不渲染**（字段整个不出现），`month` → 「2026年8月」，
   `week` → 「2026年8月24–30日那周」，`day`/`night` → 「2026年9月4日 周五」，
   `minute` → 与 `trigger_local` 同格式的 `2026-09-30T16:00+08:00 周三`。
2. **「不再把分钟级锚点暴露给模型」这一半做不到，并且不应该硬做**：
   `sdk_adapters/typed_context_use.py:149` 的 carrier 逐字节比对
   `displayed["payload"] != thaw_json(item.public_payload)`，Host 一旦删改投影 payload，
   typed-use 绑定立刻 `typed_use_projection_differs`。原始 `occurred_start` 因此**仍在 payload 里**；
   本轮换用的机制是「渲染面沉默 + 一条按需下发的 Host 提示」（§1.4）。
3. **F-ETR-8（R5b）**：策略正文按复核给定的**逐字**替换落地；
   但复核只核了 `test_policy_keeps_the_route_schema_inside_its_measured_token_cost` 的 643 上限，
   **真正卡住的闸不是它**——`test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn`
   的实测余量本轮开工时只剩 **2 个 token**（§3.1）。R5b 的 +5 单独就会打红它。
   现以同一段描述里一处语义等价的压缩（`is valid only with` → `requires`，−3）供给，
   `context_route` schema **623 → 625（≤643）**，8192 档 planned **5325 = effective 5325**，绿。
4. **PERSONA 一个字都没加**：那是每请求的受保护质量，余量为 0。
   `occurred_local` 的读法改由 `context_route` 结果上一条**条件下发**的 `temporal_hint` 承载
   （与既有 `procedure_hint` 同性质、同载体），只在这次召回真的返回了「没有 `occurred_local`
   的 episode 片段」时才出现，其余轮次逐字节不变、零 token 成本（§1.4）。
5. **F-OBS-2**：全仓核过，**没有任何语料复核/计分代码读
   `prospective_records.scheduler_registration_ref`**，无可改；在唯一一处容易被误认的读取点
   （`corpus_c04_prepare.py`，读的是登记 authority intent 的同名字段）加了辨析注释（§4）。
6. **必须重跑 C04 全 20 例**：20 例 episode 的 payload 内容哈希全变（新增 `occurred_end`），
   旧证据与新夹具不同源（§5）。

---

## 1. F-EPI-1

### 1.1 缺陷复述（复核口径）

prospective 片段由 Host 渲染 `trigger_local`，模型 18/18 逐字转述、零错误；
episode 片段只有裸 epoch `occurred_start`，于是：

| 用例 | 原文精度 | 合成锚点 | 终答 | 性质 |
|---|---|---|---|---|
| C04-10 | `undated`（「上次」） | `2026-09-05T10:00+08:00` | 「2026-09-03 左右」 | 自己换算，**差 2 天** |
| C04-14 | `undated`（「未指定日期」） | 同上 | 「第一阶段（上次，2026-09-05）」 | 把锚点当原文事实 |
| C04-17 | `month`（「8月」） | `2026-08-15T12:00+08:00` | 「今年 8月中旬」 | 把 month 收窄成旬 |

### 1.2 精度从哪里来（本轮唯一的设计抉择）

`EpisodeMemoryPayload` 的字段是
`title / participants / goals / actions / results / impacts / occurred_start / occurred_end / thread_ref`
——**既没有时区，也没有精度**。而 payload 是 SDK 冻结的 `_exact_keys` 结构，
内容哈希 = `canonical_hash(payload.to_json())`，加字段等于改 SDK，越界。

因此精度只能从 **payload 已有的 `[occurred_start, occurred_end]` 区间**读出。这不是权宜：
一条「8月发生」的记忆，它诚实的 SDK 表述本来就是「区间覆盖整个 8 月」，
而不是「8 月 15 日 12:00 这一瞬间」。语义约定：

* `occurred_end is None` —— 记忆**不界定**任何发生时间。渲染为空，字段不出现。
* `occurred_end == occurred_start` —— 点发生，按 `trigger_local` 同格式渲染完整本地时刻。
  这正是 `analysis_proposal.compile_operation` 给真实 Host 观测 episode 写的形态，
  所以真实（非语料）记忆自动获得完整时刻，无需任何夹具配合。
* 其余为区间，`occurred_end` 是**发生之后的第一瞬**。区间**只按它覆盖的日历天渲染，
  绝不按钟点渲染**——区间的含义就是「在这些天里的某处」。
  依次判：一天 / 整年 / 整月 / 恰好七天 / 其他显式范围。

畸形输入（非数、布尔、倒挂区间、越界 epoch）一律返回 `None` 而不猜，
与 `_prospective_trigger_local` 完全同款。

**时区**：episode 没有自己的时区，所以渲染区默认 `EPISODE_LOCAL_ZONE = "Asia/Shanghai"`，
即 `primary_context.PrimaryForegroundContextPort(clock_timezone=...)` 的默认值——
模型每个请求都读到 Host 那行固定时钟「timezone=Asia/Shanghai; today=…」，
用别的时区渲染发生时间会**和 Host 自己的陈述打架**。参数可注入，
控制里用 Europe/London 覆盖了 BST 偏移、23 小时的夏令时日、25 小时的夏令时周，
以及「伦敦的整月从上海看不是整月」这条跨时区判别（§2）。

### 1.3 夹具侧：只有 `month` 动了锚点

`corpus_c04.py::episode_interval(local, zone, precision)`：

| precision | occurred_start | occurred_end | 渲染 |
|---|---|---|---|
| `minute` | 锚点不变 | = start | `2026-09-30T16:00+08:00 周三` |
| `day` | 锚点不变（仍 12:00） | 次日 00:00 | `2026年9月4日 周五` |
| `night` | 锚点不变（仍 21:00） | 次日 00:00 | `2026年9月4日 周五`（「夜间」由记忆正文自己带） |
| `week` | 锚点不变（仍 12:00） | +7 天 00:00 | `2026年8月24–30日那周` |
| `month` | **改为当月 1 日 00:00** | 次月 1 日 00:00 | `2026年8月` |
| `undated` | 锚点不变 | **None** | 无字段 |

`SPECS` 表**一行未改**（`DECISION-F-C04-1-F-ETR-7.md` §6 的冻结继续有效），
区间全部在 `temporal_payload` 里派生；只有两条 month 记忆（C04-16 E1、C04-17 E）
的 `occurred_start` 由 12:00 前移到当月 1 日 00:00 —— 那本来就是「8月」这句话的诚实起点。

20 例 23 条 episode 的实际渲染逐条核过（控制 `test_every_c04_episode_renders_within_its_authored_precision`）：
**任何非 `minute` 精度的渲染串里都不含 `T` 与 `:`**，即粗精度记忆再也交不出钟点给模型。

### 1.4 「不暴露裸锚点」为什么改成一条提示

复核建议的后半句是「模型可见片段里不再暴露分钟级锚点」。做不到：
`typed_context_use.build_carrier` 用
`displayed["payload"] != thaw_json(item.public_payload)` 逐字节比对，
Host 删字段就直接 `typed_use_projection_differs`——那是真的完整性不变量，不该为本条削弱。

替代机制（`sdk_adapters/context_route.py::_temporal_hint`）：
这次召回只要返回了**至少一条没有 `occurred_local` 的 episode 片段**，
结果 extras 里加一条 `temporal_hint`：
`occurred_local` 是 Host 渲染的、按记忆实际陈述的精度给出的发生时间，逐字照报、
**永不从 `occurred_start` 反算日期**；没有 `occurred_local` 的 episode 片段**根本没有记录发生时间**，
只能用记忆自己的措辞说「什么时候」，不得给出任何日期、月份或星期。

选它而不选 PERSONA 的理由是可计量的：PERSONA 是每请求受保护质量、余量为 0（§3.1），
而 `temporal_hint` 与 `procedure_hint` 同载体（进同一 extras、同一 receipt 哈希、
`ContextRouteReceipt` 与 carrier 一字未改），是 fragments 的纯函数（不读 query、不读库、
不含 gold、不含任何 per-case 期望），**没有这种片段的轮次逐字节不变**。

### 1.5 计分端读的就是 Host 渲染的那个字段

`precision_oracle()` 的每条 episode 记录新增
`occurred_start` / `occurred_end` / **`rendered_occurred_local`**，
后者**直接 import `human_memory_v7.render_episode_occurred_local` 算出来**，
不在夹具里复述一遍规则——夹具自己算期望值等于自己给自己打分。
它随 `setup_receipt.precision_oracle` 落在 worker 退出后的证据里，
仍然**不进任何 payload、prompt 或模型可见面**（控制 `test_the_oracle_is_scoring_side_and_never_a_model_visible_payload`）。
复核下一轮可以机械地把终答的日期表述对这一列比对，而不再靠人读。

### 1.6 哈希与指纹影响

| 面 | 影响 |
|---|---|
| `fragment["payload"]` | **不变**（渲染串挂在 payload 外） |
| `fragment["payload_hash"]` | **不变**（仍是 SDK 的 `public_payload_hash`） |
| `fragment["bytes"]` / `["tokens"]` | **不变口径**：`_fragment_size` 仍只量 payload；渲染串不计入（与 `trigger_local` 既有性质相同，本轮不改口径） |
| typed-use carrier / history binding | **不变**：三元组 `(source_ref, source_revision, public_payload_hash)` 与逐字节 payload 比对全部照旧 |
| 片段键集合 | **变**：episode 片段多一个 `occurred_local`（控制里逐键钉死，见 `test_the_episode_fragment_shape_is_pinned`） |
| C04 夹具记忆内容哈希 | **变**：23 条 episode 的 `occurred_end` 从 `null` 变成数值（month 另加 `occurred_start` 前移）→ 必须重跑（§5） |
| 其他语料（C01/C03/C06/C07/C08/C10/C12） | **零影响**：它们的 episode 一律 `occurred_end=None`，字段不出现，投影逐字节不变 |

---

## 2. 控制（本轮新增／改动）

| 文件 | 例数 | 内容 |
|---|---|---|
| `backend/tests/memory/test_episode_occurred_local.py`（新增） | **52** | 逐精度表驱动（minute/day/night/week/跨月周/跨年周/month/跨年月/year/杂散区间）；`undated` 不渲染且裸锚点仍在 payload 里；点发生等于 `trigger_local` 格式（含秒）；渲染串挂 payload 旁、`payload_hash` 不动；**片段键集合逐键钉死**；`bytes` 仍等于 payload 自身字节数；跨时区判别（伦敦整月从上海看不是整月）；伦敦 BST 偏移；23 小时夏令时日仍是一天、25 小时夏令时周仍是一周；默认时区等于 `PrimaryForegroundContextPort.clock_timezone` 的默认值；倒挂区间／畸形 start／畸形 end／未知时区；类型取自片段而非 payload；prospective 片段不受影响；`temporal_hint` 只在缺字段时下发且不含任何 per-case 词；C04 三个失败例与其对照的逐例渲染期望；全 20 例「渲染不超出原文精度」；oracle 不进模型可见面；「只有 month 动锚点」；未知精度是夹具错误不是猜测；跨编译确定性；周边界差一秒退化为显式范围 |
| `backend/tests/quality/test_corpus_c04_prepare.py`（改） | 20 | C04-07 补 `occurred_end` 与渲染期望；新增 C04-10（`undated` → `occurred_end is None` 且渲染为 `None`）、C04-17（`month` 重锚 + 渲染 `2026年8月`）、C04-15（`minute` → end==start + 完整时刻）三段断言 |
| `backend/tests/memory/test_recall_selection_policy.py`（改） | 84 | R5b 正文钉死（新门 + 「`safety net` 已不在正文里」+ R1/R2 未动），其余 83 例一字未改、全绿 |

**回归（同一 venv，逐进程串行）**

| 套件 | 结果 |
|---|---|
| `tests/memory/test_episode_occurred_local.py` | **52 passed** |
| `tests/memory/test_recall_selection_policy.py` | 84 passed |
| `tests/memory/test_prospective_trigger_local.py` | 20 passed |
| `tests/sdk_adapters/test_context_route_tool.py` + `test_context_route_prospective_runtime.py` | 35 passed（合并上面四文件一次 **191 passed**） |
| `tests/quality/test_corpus_c04_prepare.py` | 20 passed（真实 SDK 落库 + 图谱内容哈希回读） |
| `tests/quality/test_corpus_c04_payload_text.py` | 85 passed |
| `tests/quality/test_corpus_prospective_settlement.py` + `test_corpus_scoring_trace.py` + `test_corpus_supported_case_ids.py` | 9 passed |
| `tests/sdk_adapters/test_token_estimator_calibration.py` | 54 passed（含 8192 档余量闸） |
| `tests/execution/test_current_tool_megabyte.py` | **3 passed**（`[4096]` 本轮实测已绿，不再是既有红） |
| `tests/quality`（全目录） | 38 failed / 355 passed；**与 main 基线逐条 diff 只多两条工作树环境项**：`test_corpus_scoring_process_exit`（子进程 `Memory SDK candidate installed origin mismatch`——装机 wheel 的 `direct_url` 指向主检出，任何工作树都红）、`test_corpus_c06_procedure_access`（`Memory SDK corpus source not present`，跳过）。其余 37 条与 main 逐条相同 |
| `tests/memory`（全目录） | 63 failed / **877** passed；main 基线 63 failed / 825 passed，**失败集合逐条 diff 完全相同**（`short_horizon_embedder_required` 等既有环境红），差额 +52 全是本轮新增控制 |

---

## 3. F-ETR-8（R5b）

### 3.1 复核漏掉的那道闸

R5b 的模型可见正文按复核 §6.4 **逐字**落地：

```
- 旧：; never a safety net on an occurrence-plus-reminder question that asks for no standing value.
+ 新：; if the request names no such standing value, omit it - what happened plus which reminder you set names none.
```

复核给的成本是「623 → 628，上限 643 未动」。上限确实没动，
但**这条正文的实际约束不是那个上限**：

```
test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn
fixed = text_tokens(PERSONA) + tool_schema_tokens(四个产品 schema)
planned = fixed + (MEGABYTE_PEAK_PLANNED_TOKENS - MEGABYTE_PEAK_FIXED_TOKENS)
assert planned <= effective_input_budget(8192)
```

本轮开工时实测：PERSONA 1122 + schemas 1163 = **fixed 2285**，
planned **5323**，effective **5325** —— **余量 2 个 token**。
（该闸的两个常量是 2026-09-09 那次和解在真实 Run 峰值上测的，`fixed` 当时是 2223；
此后已被别的车道吃掉 62。）R5b 的 +5 单独就会把 8192 档打红，
即「schema 623 < 643 所以安全」的推论**不成立**。

### 3.2 供给：同一段描述里的等价压缩

```
- " An empty list is valid only with include_short_horizon=true. "
+ " An empty list requires include_short_horizon=true. "
```

「valid only with」与「requires」都是同一个必要条件，语义零变化，省 3 token。
实测：`context_route` schema **623 → 625**（≤643），
fixed **2287**，planned **5325**，effective **5325**，`<=` 成立，绿。

**诚实记录**：8192 档余量现在是 **0**。下一个往 PERSONA 或 `context_route` 描述里加字的车道，
必须先压缩、或按该测试自己的话「re-derive the scenario in DECISION-TOKEN-ESTIMATOR.md」
（重推需要一次真实 Run，本轮跑不了）。这也是本轮**不动 PERSONA**、
改用条件下发 `temporal_hint` 的直接原因（§1.4）。

### 3.3 Host 侧不变

判据函数 `indicates_reminder_lifecycle_request`、三组文本标记、咨询码
`semantic_fallback_on_reminder_lifecycle_request` 与 `selection_policy_departures`
**一字未改**：复核实测 20/20 形态覆盖、6/6 命中，缺口在措辞不在判据。
84 例控制里那 20+12+8+5 条形态与负控断言原样全绿。

### 3.4 收益仍是预测，不是实测

R5 把 C04 多提从 12/52 打到 6/46 是实测；R5b 的 6 → ? **必须靠重跑**分辨
（复核已记单次采样噪声量级 ≥1 例，C04-09 就是新增的那一例）。本记录不宣布任何收益数字。

---

## 4. F-OBS-2（死列）

复核要求：不修 SDK，让语料复核／计分**停止读**这一列并记录。核过全仓：

* `prospective_records.scheduler_registration_ref` —— **没有任何语料复核或计分代码读它**，无可改。
* `corpus_c04_prepare.py` 与 `tests/quality/test_corpus_c08_derived_main.py` 里出现的
  `scheduler_registration_ref`，读的是**登记 authority intent 自己的同名字段**，恒有值，
  与死列无关；这是最容易误认的一处，已就地加注辨析（根因行号、为什么不可回填、
  「ref 为空 ≠ 没登记上」、真来源是 `prospective_scheduler_registrations`）。
* `scripts/native/*_verify.py` 只数 `prospective_records` 行数，从不取该列；且原生验证脚本不在本轮范围。

**F-OBS-2 保持开启（低），归属 Memory SDK 侧（删列或改视图 join 派生）。**

---

## 5. 必须重跑哪些用例

**必跑：C04 全 20 例。** 理由：

1. 20 例的 23 条 episode payload 全部新增了 `occurred_end`（两条 month 另有 `occurred_start` 前移），
   **内容哈希全变**，旧证据与新夹具不同源；
2. F-EPI-1 的三个目标例（C04-10 / 14 / 17）是这次修复的全部理由，必须在真模型上验；
   另 17 例是负控，必须**仍然**守住原文精度，且 `required_types` 召回仍 100%；
3. F-ETR-8 的 6 例多提（C04-04/09/12/13/18/20）要看 R5b 是否把 6 压下去，
   另 14 例是负控（不得由 `[episode, prospective]` 变多）。

**建议同批抽样**：C01／C02／C06 各 5 例——验证 R5b 没有伤到 gold 需要 `semantic` 的形态
（R5 在这些类别上全语料零命中，R5b 判据未改，抽样只是真模型旁证）。
**可不跑**：C03/C05/C07/C08/C09/C10/C11/C12（episode 一律 `occurred_end=None`，
片段逐字节不变；R5b 判据零命中）。

重跑后必须核的五项：
① C04-10/14 终答是否**不再给出任何日期**（只用「上次」这类原文措辞）；
② C04-17 是否只说「8月」、不再出现「中旬」；
③ 17 个负控例的日期表述是否仍在 `precision_oracle.rendered_occurred_local` 的精度之内；
④ 6 例的 `requested_memory_types` 是否已不含 `semantic`，14 例是否不变；
⑤ 片段里 `occurred_local` 是否 23/23 与 `rendered_occurred_local` 逐字一致
（这一项现在可以机械比对，不必人读）。

命令与 `DECISION-F-C04-1-F-ETR-7.md` §4 同形，仅换证据根与 `--host-root`（本工作树）。

---

## 6. 明确未改动

- 不改 `SPECS` / `SETUPS` / `SCENARIO_CLOCKS` 任何一行（含其 sha256），
  不改 `compile_c04_setup` 的任何校验，不改 `corpus_c04_lifecycle.py`；
  区间全部在 `temporal_payload` 里派生。
- 不改 `public_memory_text`：原文时间措辞仍前置到动作句（F-C04-1 的处置继续有效）。
- 不改 PERSONA 一个字；不改 R1–R4；不改 `indicates_reminder_lifecycle_request` 与三组标记；
  不改 `REQUESTABLE_MEMORY_TYPES` / `HOST_DEFAULT_MEMORY_TYPES` / `parse_recall_selection`。
- 不改 `_prospective_trigger_local` 与 prospective 片段的任何一个字节。
- 不改 `_fragment_size` 的计量口径；不改 typed-use carrier 的比对不变量；
  不改 `ContextRouteReceipt`、`public_result_hash` 的构成规则。
- 不动 Memory SDK（0.6.37 钉死）、`sdk_adapters/provider.py`、`wire_input_budget.py`、
  `execution/*`、`tools/tool_catalog/*`、`tool_authority.py`。
- 不改 oracle/gold 与 `corpus_scoring.py` 的任何指标口径；
  `precision_oracle` 只在 episode 记录上多了三个计分端字段。
