# 裁定：F-C04-1（夹具精度注记外泄）与 F-ETR-7（P4 兜底 `semantic` → 规则 R5）

> 来源：`RUN-C04-RERUN-REVIEW.md` §六缺陷 2、缺陷 3；证据 `.local-test-evidence/2026-09-09/corpus-c04-flash/run-01/`
> 分支：`worktree-corpus-c04-fix`（基线 `08881887`，工作树 `.claude/worktrees/corpus-c04-fix`，**未合回 main**）
> 语料记录（`CORPUS-CUMULATIVE-2026-09-08.md`、`RUN-C04-RERUN-REVIEW.md`）本轮不改写，由重跑批次回写。

---

## 0. 结论先行

1. **F-C04-1**：精度注记已整体移出模型可见的记忆正文，只留在 `precision_oracle()`（评分端），
   并经 `corpus_c04_prepare` → `setup_receipt.precision_oracle` 写进**worker 退出后**的证据。
   模型可见正文改为保留**原文自己的时间措辞**（`9月4日` / `上次` / `8月24–30周`），
   因为那是记忆的事实，而 `精度=day` 是本类的答案。20 例 45 条 payload 全部零标记（§1）。
2. **F-ETR-7**：`DECISION-EXTRA-TYPE-RATE.md` §3.1 补 **R5（semantic 收窄）**，
   用策略已有的词汇写死判据；工具 schema 正文 +23 wire token（600 → 623，仍 ≤ 钉死上限 643）。
   Host 侧新增确定性咨询码 `semantic_fallback_on_reminder_lifecycle_request`（只观测、不设门）。
3. **量级**：R5 在全语料 **240/240** 条 provider_input 上恰好命中 **20 条 C04**、其它类别 **0 条**；
   gold 需要 `semantic` 的用例（C01/C02/C03/C06）**一条都不命中**。
4. **重算**：本轮 C04 多提的 12 次若被 R5 消掉，累计多提率
   **38/280 = 13.6% → 26/268 = 9.7%**；required 类型召回仍 100%（C04 gold 无一条含 `semantic`）。
5. 另记 **F-OBS-2**（`prospective_records.scheduler_registration_ref` 死列）**未修**，属 Memory SDK 侧（§5）。

---

## 1. F-C04-1：注记移出模型可见正文

### 1.1 缺陷复述（证据口径）

`backend/deskpet/quality/corpus_c04.py:temporal_payload` 对 `precision != 'minute'` 的 spec 拼接：

```
【原时间=…；精度=…；具体日时仅synthetic fixture锚点，非原文事实或评分答案】
```

后果两条：**11/20** 例把它复述给用户（C04-04 原样写出「评分」二字）；更重的是
**C04 考的就是日期精度**，注记把 `day`/`week`/`month`/`night`/`undated`/`event` 的答案
直接写进被召回的文本里，18/20 例至少一条种子记忆带此注记。

### 1.2 处置

| 项 | 旧 | 新 |
|---|---|---|
| 精度标签 `精度=day` | 记忆正文 | 只在 `precision_oracle(batch)` |
| 「synthetic fixture 锚点／非原文事实或评分答案」 | 记忆正文 | 只在 `precision_oracle(batch).note` |
| 原文时间措辞（`9月4日` / `上次` / `8月24–30周`） | 记忆正文（在注记里） | **仍在记忆正文**，前置到动作句 |
| 合成锚点 `occurred_start` / `trigger_at` | 确定性 | **一字未改**（仍由 `timestamp(local, zone)` 生成） |

**为什么不把原文措辞也删掉**：episode 的时间只有 `occurred_start` 一个通道，
删掉措辞后 `上次借书归还时漏带借阅卡` 会带着合成的 `2026-09-05T10:00` 出现，
等于夹具**替原文断言了一个它从未说过的精度**——那比留下措辞更失真。
措辞是记忆的事实（原文 `04-time.md` 的 setup 逐字如此，`SETUPS` 也是这么写的），
精度标签才是答案。二者分开，正是本裁定的全部内容。

实现细节：
- `public_memory_text(spec)`：episode 且 `precision != 'minute'` 时，用**最长重叠拼接**
  把原文措辞前置（`9月4日夜间` + `夜间传稿漏附件` → `9月4日夜间传稿漏附件`，不会出现 `夜间夜间`）；
  prospective **一律不加**——它的时间由 trigger 这个结构化字段承载（`event` 由 authored 条件承载）。
- `UNQUOTED_TIME_ANCHORS = {'未指定日期'}`：authored 字段描述的是「原文没给时间」而非引用时间
  （仅 C04-14 E），不前置任何东西。
- `SPECS['C04-12'].P_OLD` 正文 `旧讨论提醒（原文未指定正文）` → `旧讨论提醒`：
  「原文未指定正文」同样是夹具记账语言，不该进模型可见文本。
- `precision_oracle(batch)` → `{label: {memory_type, authored_time_text, precision, anchor_local, anchor_zone, note}}`，
  由 `open_c04_fixture` yield，`corpus_scoring_session` 写进 `setup_receipt`（worker 退出后的证据，不是模型输入）。

45 条 payload 的实际正文已逐条核对（20 例全集），例如：
`9月4日验样封面偏暗`、`8月24–30周修复缺页`、`7月首次试课麦克风失效`、
`9月4日最近试课计时超长`、`上次借书归还时漏带借阅卡`、`9月4日夜间传稿漏附件`、`旧讨论提醒`。

### 1.3 未解决的残留（诚实记录）

- C04-09 的 prospective 精度是 `day`（原文只说「9月8日」），而 trigger 仍是
  `2026-09-08T12:00`——**结构化字段的假精度是既有形态，本轮未动**（改它要动 SDK 触发语义）。
  差别是：现在模型至少不会被告知「精度=day」。
- 同病的 C03（`corpus_c03_dates.py:36-38`、`corpus_c03_inference.py:72` 的
  `【原日期精度=…；synthetic_day=true；…】`）**本轮未动**，不在本代理范围；建议按同一处置另立一条。

---

## 2. F-ETR-7：规则 R5（semantic 收窄）

### 2.1 为什么必须是一条新规则

`DECISION-EXTRA-TYPE-RATE.md` §3.1 里 P1/P2/P3 各挂 R1/R2/R3 一条可由问句判定的规则，
**P4 只挂在总纲下**（「只请求答案真的可能在里面的最小类型集合」），没有判别词。
后果是它的触发与问句特征不相关：C04-08「昨天交接**缺什么**，明天一早我留了哪项提醒」不多提，
C04-13「申请上次查出**缺什么**，截止前我设了什么准备提醒」多提——同一句式跨在两侧。
**因此 R5 不能建立在「12 例与 8 例之间的差别」上**（那个差别是抖动），
只能建立在 **20 例共同的请求形态**上：问「发生了什么」+「我已经定了什么提醒」，且不问任何长期值。

### 2.2 R5 正式表述

> **R5（semantic 收窄）**：当请求是一个**生命周期回顾轮**——同时问「过去发生了什么」与
> 「我（已经）定下的提醒/待办/截止是什么」，且**没有问任何长期值**（习惯／偏好／格式／单位／约定，
> 即 R1 的那组判别词）——**不要把 `semantic` 当兜底加进来**。
> `semantic` 存的就是长期值；请求里没有长期值，它只能空手而归，却照样占用共享召回预算。

模型可见正文（`MEMORY_TYPE_SELECTION_POLICY` 的 semantic 句尾，英文，与既有措辞同风格）：

```
never a safety net on an occurrence-plus-reminder question that asks for no standing value
```

Token 成本（实测，非估算）：`context_route` 整个 schema **600 → 623 wire token**，
`test_policy_keeps_the_route_schema_inside_its_measured_token_cost` 的上限 **643 未动**
（该上限来自 `tests/execution/test_current_tool_megabyte.py[8192]` 的实测断点，见 §3.2 原记录）。

### 2.3 Host 侧咨询码（只观测，不设门）

`selection_policy_departures(memory_types, request=None)` 新增可选的 `request`，
命中时追加 `semantic_fallback_on_reminder_lifecycle_request`。判据函数
`indicates_reminder_lifecycle_request(text)`：三组纯文本标记（发生类 / 提醒类 / 长期值类），
**长期值类命中即一票否决**。与 `indicates_workflow_request` 同性质：
不拒绝、不过滤、不改写任何召回，不含 gold、不含 per-case 期望答案、不调模型。
`context_route.py` 的两处记录点把 `proposal["query"]` 传进去；
**query 本身仍不落审计**（投影仍只有枚举/布尔）。旧调用方（只传类型）行为逐 token 不变。

### 2.4 判别力实测（全语料 240 条 provider_input）

| 集合 | 命中 R5 形态 | 说明 |
|---|---|---|
| C04 20 例 | **20 / 20** | 12 例多提的 + 8 例没多提的，形态一致（这正是「抖动不是判断」的证据） |
| 其余 220 例（C01/C02/C03/C05–C12） | **0** | 无一命中 |
| gold `required_types` 含 `semantic` 的用例（C01/C02/C03/C06） | **0** | 每一条都点名了长期值，被一票否决 |

负控（逐条断言，见 §3）：C01-09「我之前**要求**日常距离显示用什么**单位**？」、
C01-01「照我之前定下的**习惯**」、C02-12「按我家里一贯的环境限制」、
C03-01「按我们定下的反馈**方式**」、C06-03「按我自己的金额**格式**和检查**习惯**」——
`indicates_reminder_lifecycle_request` 全部 `False`，`semantic` 请求零咨询码。

### 2.5 指标重算

| 口径 | 现状（09-09 合并） | R5 消掉 12 次后 |
|---|---|---|
| C04 多提 / 预测类型数 | 12 / 52 = 23.1% | **0 / 40 = 0%** |
| 累计多提 / 预测类型数 | 38 / 280 = **13.6%** | **26 / 268 = 9.7%** |
| 累计 required 召回 | 176 / 176 = 100% | **100%（不变）** |

分母同步减 12：那 12 例原本每例提 3 类，R5 后提 2 类（52 = 8×2 + 12×3 → 40 = 20×2）。
若只按分子变、分母不变的粗口径算是 26/280 = 9.3%，**以 26/268 = 9.7% 为准**。
这是**预测值，不是实测**——真正的重测必须走批次重跑（§4），理由与 §4「限制 2」同：
跨模型/单次采样的绝对数不可外推。

---

## 3. 测试

| 文件 | 例数 | 内容 |
|---|---|---|
| `backend/tests/quality/test_corpus_c04_payload_text.py`（新增） | **85** | 纯文本、无库无时钟无模型：20 例逐例断言**任何模型可见 payload 字符串**不含 `精度/评分/夹具/锚点/原文/gold/oracle/fixture/synthetic/precision/scoring`；正文不含任何精度词汇；旧注记两段字面量全语料消失；`precision_oracle` 仍逐条给出 precision/authored/anchor；带日期的 episode 保留原文措辞；`未指定日期` 不被凭空加日期；重叠拼接不重复；prospective 正文不带时间；payload 跨编译确定性 |
| `backend/tests/quality/test_corpus_c04_prepare.py`（改） | 20 | 原来断言注记**存在**的那三行反转为「无标记 + `precision_oracle` 精度可用」；其余 20 例设置控制一字未改 |
| `backend/tests/memory/test_recall_selection_policy.py`（改，+11 个用例函数 / 共 84 例） | **84** | R5 正文进 schema；**20 条 C04 真实 turn 逐条**断言形态可判定；**12 条**多提选择拿到 R5 码；**8 条**最小选择码集仍为空；一条汇总断言「12 → 0、8 不变」；5 条 C01/C02/C03/C06 负控（`semantic` 必需处零码）；「长期值词一票否决」「两半缺一不成立」「不传 query 时 R5 不可判定（旧调用方不变）」「R5 只咨询不选择」「与既有两码的合成顺序」 |

回归（同一 venv，逐个进程串行跑）：

| 套件 | 结果 |
|---|---|
| `tests/quality/test_corpus_c04_payload_text.py` | 85 passed |
| `tests/quality/test_corpus_c04_prepare.py` | 20 passed |
| `tests/memory/test_recall_selection_policy.py` | 84 passed |
| `tests/sdk_adapters/test_model_recall_selection.py` + `test_recall_selection_failure_audit.py` + `tests/memory/test_model_short_recall.py` | 38 passed |
| `tests/sdk_adapters/test_context_route_tool.py` | 34 passed |
| `tests/quality/test_corpus_prospective_settlement.py` + `tests/sdk_adapters/test_context_route_prospective_runtime.py` | 8 passed |
| `tests/quality/test_corpus_scoring_trace.py` + `test_corpus_supported_case_ids.py` | 3 passed |
| `tests/execution/test_current_tool_megabyte.py` | `[8192]`/`[32768]` 绿，**`[4096]` 仍为既有红**（`wire_count 1 != 3`，与 F-ETR-4 记录逐字一致，本轮一个 token 未加到该红上：schema 623 < 643） |

合计 **272 例绿 + 1 例既有红**。

---

## 4. 必须重跑哪些用例

**必跑：C04 全 20 例**（`C04-01…C04-20`）。理由分两条，缺一不可：

1. **F-C04-1 使本轮 C04 的「日期精度正确」全部作废**——18/20 例的种子记忆带过注记，
   在重跑前**不得声称 C04 的日期精度能力已验证**；20 例的 payload 内容哈希本轮已改，
   旧证据与新夹具不同源。
2. **F-ETR-7 的 12 例多提**（C04-02/04/05/10/12/13/14/15/17/18/19/20）要在真模型上验证 R5 是否生效；
   另 8 例（C04-01/03/06/07/08/09/11/16）是负控，必须仍不多提且 required 仍 100%。

命令（与 09-09 那轮同形，仅换证据根；串行、每例 6 GiB / 1200 s）：

```bash
scripts/run_corpus_batch.py \
  --host-root   <本工作树或已合入的 main> \
  --memory-sdk-root <memory-sdk 仓> \
  --installed-target <工作树内安装目标> \
  --evidence-root .local-test-evidence/2026-09-10/corpus-c04-flash \
  --case-file    .local-test-evidence/2026-09-09/corpus-c04-flash/cases-c04.txt \
  --primary-env-file .local-test-evidence/2026-09-07/credentials/deepseek.env \
  --primary-model deepseek-v4-flash \
  --rss-mib 6144 --seconds 1200
```

**建议同批重跑（验证 R5 没有伤到 required `semantic`）**：C01（20）、C02（19）、C06（20）各挑
5 例的抽样即可——R5 在这些类别上全语料零命中（§2.4），抽样只是真模型侧的旁证。
**可不跑**：C05/C07/C08/C09/C10/C11/C12（R5 零命中，且策略只可能收窄选择）。

重跑后必须核的四项：① 12 例的 `requested_memory_types` 是否已不含 `semantic`；
② 8 例是否仍为 `[episode, prospective]`；③ 20 例终答是否还出现 `精度/夹具/锚点/评分`（应为 0）；
④ 日期精度是否仍全对——**这一项才是 C04 第一次被真正计量**。

---

## 5. 一并记录、本轮不修：F-OBS-2

`prospective_records.scheduler_registration_ref` 恒为 NULL（23/23 行 `typeof=null`），
而同库 `prospective_scheduler_registrations.scheduler_registration_ref` 完整。
根因在 Memory SDK：`backends/sqlite_v5.py:11136-11145` 的 INSERT 把该列硬写成 `None`，
`backends/schema_v5.py:662-667` 又给该表挂了 `immutable_update`/`immutable_delete` 触发器，
**登记成功后永远无法回填**——死列。危害是只读证据审查会把「ref 为空」误读成「没登记上」。
**属 SDK 侧（删列或改视图 join 派生），不在本工作树范围，本轮只记录不动。**

---

## 6. 明确未改动

- 不改 `SPECS` 的任何**时间锚点**（`local`/`zone`）、不改 `SETUPS`/`SCENARIO_CLOCKS`（含其 sha256）、
  不改 `compile_c04_setup` 的任何校验；C04-12/C04-17 的生命周期路径与 `corpus_c04_lifecycle.py` 一字未改。
- 不改 `REQUESTABLE_MEMORY_TYPES` / `HOST_DEFAULT_MEMORY_TYPES` / 解析器 `parse_recall_selection`。
- 不改 R1–R4 一个字；不升分析协议；不改 PERSONA；不改 `procedure_hint` 的解耦逻辑（F-ETR-5）。
- 不改 oracle/gold、`corpus_scoring.py` 的任何指标口径；`corpus_scoring_session.py` 只在
  `setup_receipt` 名单里加了 `precision_oracle` 一个词。
- 不动 `sdk_adapters/provider.py`、`wire_input_budget.py`、`execution/*`、
  `memory/procedure_runtime.py`、`semantic_correction.py`。
