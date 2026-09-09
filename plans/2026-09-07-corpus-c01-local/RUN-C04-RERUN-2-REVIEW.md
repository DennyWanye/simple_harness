# 语料 C04 第二次重跑复核记录（Prospective 切片 20 例，deepseek-v4-flash，Memory 0.6.37）

复核时间：2026-09-09 中午。性质：主代理逐例读库 + 读 trace 的终态复核，**不是人工标注**，未起子代理。
原始证据（gitignored）：`.local-test-evidence/2026-09-09/corpus-c04-flash-r2/run-01/`，
批次驱动日志 `.local-test-evidence/2026-09-09/corpus-c04-flash-r2/run-01.driver.log`（每例一行 JSON）。
方法与逐例表格沿用 `RUN-C04-RERUN-REVIEW.md`（下称"上一轮"）；本轮**新增一列"精度裁定"**——
这是 `DECISION-F-C04-1-F-ETR-7.md` §4「④ 日期精度是否仍全对——**这一项才是 C04 第一次被真正计量**」的兑现。

## 一、范围与组合

- **20 例**，`backend/deskpet/quality/corpus_c04.py:SPECS` 的 C04-01…C04-20 全集。
- provider：**`deepseek-v4-flash`**，20/20 走 primary，`preflight.json` 各例 200，无回退、无 5xx。
- 组合：两项修复 `f132b437`（08:41 合入 main：F-C04-1 注记移出正文 + F-ETR-7 规则 R5）、
  Memory SDK **0.6.37**（`backend/pyproject.toml:170` 钉死）。上一轮测的是 0.6.34，**F-RERUN-2 由本轮兑现**。
- 批次 09:12:21–09:21:43（+08:00），串行，每例 6 GiB / 1200 s；20 例 **9 min 22 s**，
  中位 25.8 s，最长 49.8 s（C04-06），峰值 RSS **1.75 GiB**。
- **20/20 `driver_returncode=0`、`stop_reason` 全 None、`SETUP_BLOCKED` 0**。

**批中 HEAD 变动的诚实说明**：批次跨 09:12–09:21，其间 HEAD 动过三次
（`a9009db4` / `18652694` / `4462b414`，均在 09:14–09:15）。三者改动文件为
`scripts/native/a6_verify.py`、`backend/tests/native/test_a6_verify_a6_4.py`、
`ARCHITECTURE/AGENT_HARNESS.md` 与 `plans/**` ——**`backend/deskpet/**` 一个字未动**，
与语料运行期路径完全无交集。上一轮那条"运行期路径在批中被改"的保留意见，本轮不适用。

## 二、总结果

| 项 | 数值 | 与上一轮对比 |
|---|---|---|
| 执行 | 20 / 20（rc 0） | 持平 |
| **复核裁定（gold 口径）** | **PASS 20 / FAIL 0 / INCONCLUSIVE 0** | 持平 |
| **复核裁定（日期精度口径，本轮首次可计量）** | **PASS 17 / FAIL 3**（C04-10 / C04-14 / C04-17） | 上一轮不可计量 |
| 调度登记 | **20/20 成功**，`missing=[]`、`tick_errors=[]` 全空 | 持平 |
| required 类型命中 | **40 / 40 = 100%** | 持平 |
| 多提类型 | **6 / 46 = 13.0%** | 12 / 52 = 23.1% ↓ |
| 多提的例数 | 6 / 20 = 30% | 12 / 20 = 60% ↓ |
| 隐私违规 | 0（20 例 `privacy_allowed=true`、`no_recall=false`，片段 `privacy_class` 全 `personal`） | 持平 |
| provider handoff | 19 例 2 次（一次工具 + 一次作答），C04-06 3 次 | 持平 |
| 路由 | 20/20 首选 `context_route(route=memory_standalone)`、`origin=model_proposal` | 持平 |
| `route_effects` 工具调用 | **20/20 恰好 1 条 `context_route`** | 上一轮有 2 例路由噪声（`tool_search`/`task_scope_search`），本轮**归零** |

20 例 `oracle_verdict` 均为 `PENDING_POST_TERMINAL_REVIEW`——这是自动 oracle 正常完成的取值
（`backend/deskpet/quality/corpus_scoring.py:299`），不是失败；语义部分由本记录补齐。

## 三、注册 / 调度形态核实

逐例把 `runtime/userdata/data/human_memory_v7.db` 与 `state.db` **连同 `-wal`/`-shm` 一起拷到 scratchpad 后只读打开**
（`?mode=ro&immutable=1` 会跳过 WAL 导致漏读登记状态，这是上一轮踩过的坑）。

| 检查项 | 结果 |
|---|---|
| `state.db` `PRAGMA user_version` | 20/20 = **55** |
| 游标表落点 | 20/20 写 **`prospective_outbox_cursor_v52`**（C04-12/17 各 3 行）；封存表 `prospective_outbox_cursor` **0 行** |
| `s5c_cursor_successor_required` / `IntegrityError` / `ProspectiveSetupNotReady` / `LEDGER_TAMPERED` | **全批零命中**（`grep -r` 覆盖 20 例全部证据文件） |
| `state.db.prospective_scheduler_registrations` | 24 条命令、48 行，`prepared`→`applied` 成对落地，`outbox_id` 一一对应 |
| `human_memory_v7.db.prospective_scheduler_registrations` | **24** 条登记事件（18 例各 1 + C04-12 三条 + C04-17 三条） |
| `prospective_records` 行数 | **23** 行 = 20 条种子 P + C04-12 rev2 + C04-17 P_OLD rev1/rev2，**无模型新建** |
| `prospective_trigger_events`（HM）/ `prospective_timer_events`、`prospective_occurrences`、`prospective_invalidation_terminals`（state） | **全部 0** ——无触发、无结算、无新增调度 |
| `prospective_signal_rejections` | 0；`signal_decisions` / `signal_results` 各 24，与登记事件一一对应 |
| `analysis_batches` / `accepted_analysis_plans` | 每例各 1，种子写入，非模型写侧 |
| `observation-route_effects.json` | 每例 **1 条** = 唯一的 `context_route` 调用；无任何变更类效果 |

两条生命周期用例逐条对上 `corpus_c04_lifecycle.py:successor()` 的期望，且与上一轮**逐 hash 一致**（确定性）：

- **C04-12（rescheduled）**：`registration(mem…1bfc29ed, prospective_revision=1, registration_revision=1,
  ref=host:prospective-registration:7be9d7…)` → `invalidation(同 ref)` →
  `registration(prospective_revision=2, registration_revision=2, ref=…dd48b6…)`；
  HM 侧状态 `accepted / invalidated / accepted`，第三条 `lifecycle_state=rescheduled`。
  召回只返回新的 9/9 09:30，旧 9/7 提醒**未进片段**。
- **C04-17（cancelled）**：`registration(押金 mem…afa11cff, ref=…e6112a…)` +
  `registration(旧出发提醒 mem…66d1cc18, ref=…0a036c…)` → `invalidation(66d1cc18)`；
  **被取消的 rev2 没有再拿到任何登记**（这是对的）。召回只返回押金那条。

`prospective_invalidation_terminals` / `prospective_invalidation_terminal_receipts` 两例均 0 ——
`prospective_registration_source.py:82-112` 只让 "invalidation not required" 的结算分支写这两张表，
本轮两次作废都走了正常的 `registration_invalidated` 路径，**0 行是预期值，不是漏写**（沿用上一轮结论）。

### `trigger_local` 独立复算

把 18 条 time trigger 按 `trigger_at` + `timezone` 用 `zoneinfo` 独立复算（含星期），
与 Host 渲染的 `trigger_local` **18/18 逐字一致**，零 mismatch；两条 event trigger（C04-10/11）
`trigger_local=None`，符合"事件触发无本地时刻"的语义。最难的两条同样对齐：

| 用例 | Host 渲染 | 独立复算 |
|---|---|---|
| C04-06（Europe/London，BST） | `2026-09-08T09:00+01:00 周二` | 一致 ✅ |
| C04-03（跨年） | `2027-01-02T10:00+08:00 周六` | 一致 ✅ |

**上一轮已证伪的 `trigger_local` 历史 FAIL，本轮在 0.6.37 上二次确认未复发。**

## 四、逐例裁定

`extra` = 该例多提的类型数；`精度` = 该例对**被引用记忆的时间表述**是否不超出 `setup_receipt.precision_oracle` 记的原文精度。

| 用例 | gold 裁定 | 精度裁定 | memory_types | extra | 注册 | 依据（对照 gold / precision_oracle） |
|---|---|---|---|---|---|---|
| C04-01 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月4日验样封面偏暗」+「周一 2026-09-07 09:00 索取修正版」；明说"没有执行任何动作" |
| C04-02 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「8月31日盘点缺两本」+「9/8 10:00 补登记」；未取其他月份盘点 |
| C04-03 | PASS | PASS | episode, prospective | 0 | 1 accepted | **年份分离正确**：2025-12-31 缺照片 / 2027-01-02 10:00 补拍。仅措辞瑕疵："跨年**夜**"（原文只到 day，见 §八观察 2） |
| C04-04 | PASS | PASS | +semantic | 1 | 1 accepted | 「9月5日试用导出成功」+「9/10 15:00 清点导出副本」；明说不执行 |
| C04-05 | PASS | PASS | episode, prospective | 0 | 1 accepted | **取下午 15:00「校色通过」而非上午 09:00「打印偏色」**，且上/下午判断与两条 minute 锚点一致；周一 11:00 领样 |
| C04-06 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月4日远程访谈音频缺段」+ 换算「伦敦 09:00 BST = 北京 16:00」并**显式给出该日偏移**；未通知任何人 |
| C04-07 | PASS | PASS | episode, prospective | 0 | 1 accepted | 按**事件发生时间** 8/20 定位「页码错序」；复核 9/9 14:00 |
| C04-08 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月5日交接少一份表」+「9/7 08:30 补送」；"昨天/明天"以固定时钟 9/6 为基准换算正确 |
| C04-09 | PASS（边界） | PASS | +semantic | 1 | 1 accepted | 取 E2「8月31–9月6周重复封面」+「9/8 12:00 复核封面」；E1「8月24–30周缺页」**明确归为前一周**；两条 week 记忆均按"周"表述，未落到某一天 |
| C04-10 | PASS | **FAIL** | episode, prospective | 0 | 1 accepted | gold 满足（漏借阅卡 + 事件触发归档回执，未触发/未结算）。**精度失败**：原文精度 `undated`（"上次"），终答却写「发生在 **2026-09-03 左右**」——既凭空给了日期，**且算错**（锚点是 `2026-09-05T10:00+08:00`） |
| C04-11 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月5日试印失败、颜色不符」+ 成功后寄样；明写"验收尚未成功，提醒还没有被触发" |
| C04-12 | PASS | PASS | +semantic | 1 | 3（含 1 作废） | 「9月4日因场地检修推迟讨论」+「9/9 09:30 确认新场地」；**未把旧 9/7 提醒当待办**。措辞瑕疵："上上周五"（9/4 相对固定时钟 9/6 应为上周五，见 §八观察 2） |
| C04-13 | PASS | PASS | +semantic | 1 | 1 accepted | 「9月3日申请材料缺签名」+「9/9 10:00 补签」，**把 9/10 截止日与提醒时间明确分开** |
| C04-14 | PASS | **FAIL** | episode, prospective | 0 | 1 accepted | gold 满足（去重完成 / 第二阶段未开始 +「9/8 14:00 开始格式检查」）。**精度失败**：原文精度 `undated`（`authored_time_text='未指定日期'`），终答却写「第一阶段（上次，**2026-09-05**）」——把合成锚点当成原文事实 |
| C04-15 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「三季度清点缺备份索引」+「2026-10-02（周五）10:00 补索引」；跨季度未跨错年份 |
| C04-16 | PASS（边界） | PASS | episode, prospective | 0 | 1 accepted | 取 E2「9月4日最近试课计时超长」+「9/7 12:00 缩短练习」；E1 只写「**7月**首次试课麦克风失效」，**未落到 7/15**（month 精度守住） |
| C04-17 | PASS | **FAIL** | episode, prospective | 0 | 3（含 1 作废） | gold 满足（天气取消 + 9/10 16:00 核对押金；旧出发提醒既未召回也未复活）。**精度失败**：原文精度 `month`（"8月"），终答收窄成「今年 **8月中旬**」——来源只能是合成锚点 `2026-08-15T12:00` |
| C04-18 | PASS | PASS | +semantic | 1 | 1 accepted | 「2026年8月12日修订封面缺作者名」+「2026-09-12（周六）09:00 交修正版」；年份按用户给定的 2026 |
| C04-19 | PASS | PASS | episode, prospective | 0 | 1 accepted | **未编造成功**：明写"结果尚未确认、没有存下最终结果"；+「9/8 10:00 追问测试结果」，两来源都用上了 |
| C04-20 | PASS | PASS | +semantic | 1 | 1 accepted | 「9月4日**夜间**传稿漏附件」（night 精度守住，未写成 21:00）+ 双时区「上海 2026-09-08 00:30 / UTC 2026-09-07 16:30」，日期翻转按**一条**提醒处理 |

### 负控（"不声称触发 / 不新增调度 / 不结算"）

C04 全类 gold 的 `required_types` 一律 `[episode, prospective]`，没有"含糊愿望不建提醒"形态的负控例；
本类的负向要求是上面这条，逐例核对 **20/20 满足**，并有硬证据：
`prospective_records` 行数恰等于 fixture 种子数（23），trigger/timer/occurrence 三表全 0，
`route_effects` 每例仅 1 条读取型调用。

C04-06 结尾说"我会把它提交给后台记忆流程登记，**目前不声称已完成持久化修改**"——
措辞上略有承诺意味，但库里零新增、零效果，不构成违规，仅记。

## 五、F-C04-1 验证（精度注记外泄）

### 5.1 机械口径：模型可见面零命中

对 20 例逐一取出**全部模型可见文本**——`observation-trace.json` 里每次 provider 调用的
`request_json.messages[*].content`（系统提示、用户轮、工具结果回填）与 `request_json.tools`（12 个工具的完整 schema）、
`response_json`（含 tool_calls 与作答），以及 `observation-transcript.json` 的 assistant 轮——
对 `精度 / 夹具 / 锚点 / 评分 / 原时间`，另加 `原文 / gold / oracle / fixture / synthetic / precision / scoring` 共 11 个关键词计数：

**20/20 例、全部关键词、模型输入与输出三个面，命中数一律 0。**

再对整棵证据树（285 MB）做原始 `grep -r` 定位残留出处，并用路径遍历确认每一处的字段路径：

| 关键词 | 出现处 | 是否模型可见 |
|---|---|---|
| `精度` / `锚点` / `评分` | `execution.json` → `.setup_receipt.precision_oracle.<label>.note`（每例 2–3 条） | **否**：worker 退出后由 `corpus_scoring_session` 写入的评分端回执 |
| 同上 | `review-packet.json` → `.execution.setup_receipt.precision_oracle.<label>.note` | **否**：上者在复核包里的副本 |
| `评分` | `runtime/config.toml:99` 注释「D8 高后果异体评分」 | **否**：运行期配置注释，不进 prompt |
| `评分` | `scoring/original-documents.json`（gold 语料原文表） | **否**：评分端 gold，不进 prompt |
| `夹具` / `原时间` | **全树零命中** | — |

源码侧一并确认：`corpus_c04.py` 里 `精度` 只剩 3 处——模块 docstring 的历史说明（第 8 行）、
`SCORING_METADATA_MARKERS` 白名单（第 95 行）、`precision_oracle()` 的 `note`（第 135 行），
`public_memory_text()` 只把**原文自己的时间措辞**前置到动作句（`9月4日` + `验样封面偏暗` → `9月4日验样封面偏暗`）。

**裁定：F-C04-1 的"注记外泄"部分，验收通过，关闭。** 上一轮 11/20 例把注记复述给用户
（含 C04-04 原样写出"评分依据"四字）的形态，本轮 **0/20**。

### 5.2 日期精度是否"真的被测出来了"——部分达成

把 20 例终答逐条对 `setup_receipt.precision_oracle` 打分（机械扫描 anchor 的 `HH:MM` 与日期串，
再人工读全文确认；两处 `12:00` 机械命中经核实来自 **prospective 的 `trigger_local`**，不是 episode 的锚点，属误报）：

- **17/20 例守住了原文精度**：`day` 只说日、`week` 只说"8月31日–9月6日这周"、
  `month` 只说"7月首次"、`night` 只说"夜间"，没有一例把 episode 的合成 `12:00` 说给用户。
- **3/20 例越过了原文精度**，且**三例的越界都来自同一个通道**——
  episode 片段里那个精确到秒的 `occurred_start` 时间戳：

| 用例 | 原文精度 | 合成锚点 | 终答说法 | 性质 |
|---|---|---|---|---|
| C04-10 | `undated`（"上次"） | `2026-09-05T10:00+08:00` | 「发生在 **2026-09-03 左右**」 | 凭空给日期 **且换算错误（差 2 天）** |
| C04-14 | `undated`（"未指定日期"） | `2026-09-05T10:00+08:00` | 「第一阶段（上次，**2026-09-05**）」 | 把合成锚点当原文事实 |
| C04-17 | `month`（"8月"） | `2026-08-15T12:00+08:00` | 「时间为今年 **8月中旬**」 | 把 month 收窄成旬 |

**裁定**：F-C04-1 的第二个目的——"让日期精度成为**模型自己**的答案"——**部分达成**。
注记不再预先作答，这一轮确实第一次测到了这项能力，代价是立刻测出 3 个失败点。
但 fixture 仍通过 `occurred_start` 给出一个**确定到分钟的时间**，
所以严格说本轮测到的是"模型是否会把一个精确时间戳当成原文事实转述"，
而**不是**"模型能否在原文只有 `上次` 时保持不确定"——后者需要 episode 侧也具备精度通道。
详见 §七 F-C04-2 / F-EPI-1。

## 六、R5 残留（6 例仍多提 `semantic`）

### 6.1 模型看到的选择策略正文（20 例逐 hash 相同）

`context_route.parameters.properties.memory_types.description` 在 20 例的 provider 请求里
sha256 前 16 位**唯一**（`984f161026e419bc`），即 6 个残留例与 14 个干净例看到的是**同一段字**：

```
Required for memory_standalone. Request only types that can hold the answer; extras return nothing
and spend the budget. semantic: facts, preferences, standing agreements - use alone for "what is my
habit/format/unit/agreement", including when worded "as I said before"; never a safety net on an
occurrence-plus-reminder question that asks for no standing value. episode: only when the answer
needs the past occurrence itself - what happened, when, with whom, how it ended; a past reference
alone is not such a question. prospective: only for a future intention, reminder, deadline or
trigger; scheduling words in a preference question are not one. procedure: typed recall returns
only an already-bound Procedure, never a workflow saved but not yet used - call procedure_discover
instead when the request mentions steps or a checklist. An empty list is valid only with
include_short_horizon=true. Selection grants no permission to disclose or execute.
```

R5 的模型可见半句即 `never a safety net on an occurrence-plus-reminder question that asks for no
standing value`（`backend/deskpet/memory/recall_selection.py:13-15`）。

### 6.2 Host 咨询码：6/6 命中，14/14 不命中

| 用例 | `requested_memory_types` | `selection_policy_departures` |
|---|---|---|
| C04-04 | `[episode, prospective, semantic]` | `[semantic_fallback_on_reminder_lifecycle_request]` |
| C04-09 | `[episode, prospective, semantic]` | 同上 |
| C04-12 | `[episode, **semantic**, prospective]` | 同上 |
| C04-13 | `[**semantic**, episode, prospective]` | 同上 |
| C04-18 | `[**semantic**, episode, prospective]` | 同上 |
| C04-20 | `[episode, prospective, semantic]` | 同上 |
| 其余 14 例 | `[episode, prospective]` | `[]` |

6 例的 `semantic` 片段返回数：**0**（20 例 `fragments` 里没有任何一条 `semantic`）——多提依旧 100% 空手而归。

### 6.3 残留是"措辞缺口"，不是"R5 不覆盖的请求形态"

用 `backend/.venv` 直接跑判据函数 `indicates_reminder_lifecycle_request`：

| 输入 | 命中 R5 形态 |
|---|---|
| 20 例**模型自己写的 `query`** | **20 / 20** |
| 20 例**用户原话 `current_user_message`** | **20 / 20** |

也就是说，**6 个残留例的请求形态 100% 落在 R5 判据之内**，Host 也确实每一例都记了咨询码。
**残留不是形态未覆盖，而是模型看到了这条规则仍然违反它。** 三条支撑：

1. **R5 用"动机"设限，而模型不认为自己在兜底。** R5 的模型可见句是唯一一条以否定动机表述的规则
   （"never a **safety net**"），而同段 episode / prospective 用的都是可判定的正向门（"only when…"）。
   6 例里有 **3 例（C04-12 / 13 / 18）把 `semantic` 排在列表首位或次位**，不是追加在末尾——
   模型是把它当作一次主动判断在提，自查"我是不是在兜底"对它无从下手。
2. **跨两轮的成员churn 说明还有采样噪声，但也有稳定倾向。**
   上一轮残留 12 例 = {02,04,05,10,12,13,14,15,17,18,19,20}；本轮 6 例 = {04,09,12,13,18,20}。
   **7 例消失、1 例新增（C04-09）**，交集 5 例（04/12/13/18/20）**两轮都多提**。
   → R5 的收窄是真实的（12→6，且同一模型同一策略只差这一句），但残留里既有稳定倾向也有 ≥1 例的抖动。
3. **代价面无变化**：required 类型召回仍 100%（C04 全类 gold 无一条含 `semantic`），
   R5 一次也没有伤到应召回的类型。

### 6.4 建议的精确细化：R5b（把否定动机改成可判定的正向门）

> **R5b**：`semantic` 只在**请求本身用自己的词点名了一个长期值**（习惯／偏好／格式／单位／约定，
> 即 R1 的那组判别词）时才加；点不出这样的词就**不加**——
> "过去发生了什么 + 我已经定了什么提醒"就点不出。

**模型可见正文**（只替换 `semantic` 子句末尾那半句，其余一字不动）：

```
- 旧：; never a safety net on an occurrence-plus-reminder question that asks for no standing value.
+ 新：; if the request names no such standing value, omit it - what happened plus which reminder you set names none.
```

**Token 成本（实测，非估算）**：用 `tool_schema_tokens` 对替换后的 `CONTEXT_ROUTE_SCHEMA` 计量，
`context_route` 整个 schema **623 → 628 wire token**，
`test_policy_keeps_the_route_schema_inside_its_measured_token_cost` 的上限 **643 未动**，
`tests/execution/test_current_tool_megabyte.py[4096]` 的既有红不会被加重。
（另测了两版更长的候选：634 / 629，均在上限内，但 628 这版最短且判据最直白，取它。）

**Host 侧不需要改**：判据已覆盖 20/20，咨询码已 6/6 命中；
建议保持"只观测、不设门"，但把该码正式写进复核清单（本轮已这么用）。

**诚实的边界**：R5b 的收益是**预测，不是实测**。R5 本身把 12/52 → 6/46 是实测；
但 C04-09 的新增说明单次采样的噪声量级 ≥1 例，因此 6 → 更低的差异必须靠**再跑一轮**（或多轮）才能分辨，
不能靠推演宣布。列为 **F-ETR-8**。

## 七、隐私

**C04 无 `privacy_allowed=false`、无 `no_recall=true` 用例——本轮机械确认，与此前一致。**

- 20 例 `oracle.labels`：`privacy_allowed` 全 `true`、`no_recall` 全 `false`、
  `hard_trigger` 全 `无`、`required_procedure_access` 全 `null`、`requires_task_scope_search` 全 `false`。
- 20 例返回的全部片段 `privacy_class` **全为 `personal`**（无 `recipient_private` 一类）。
- 无第三方披露：唯一涉及"通知别人"的 C04-06 明确说明"暂不通知任何人，也没有任何通知已发出"，
  且 `route_effects` 仅 1 条读取型调用。
- **隐私违规 0**。

## 八、缺陷与观察

### 缺陷 1（跑道 + 观测，新，中）：episode 的合成锚点仍以精确时间戳进入模型可见片段

- **证据**：`temporal_payload` 的 episode 分支把 `timestamp(local, zone)` 写进 `occurred_start`，
  召回片段原样带出（如 C04-10 `occurred_start: 1788573600.0` = `2026-09-05T10:00+08:00`），
  而这条记忆的原文精度是 `undated`（"上次"）。3/20 例据此对用户断言了超出原文精度的时间（§5.2）。
- **性质**：这是 F-C04-1 处置里**已知未解决的残留**（`DECISION-F-C04-1-F-ETR-7.md` §1.3 只点了
  prospective 侧的 C04-09，本轮测出 episode 侧同病且实际发生）。注记不再预先作答，
  但**结构化字段仍然在作答**，只是从"带标签的答案"变成了"不带标签的数字"。
- **裁定**：不推翻 20 例 gold PASS；但"C04 的日期精度能力"目前只能声称
  **"模型是否会把精确锚点当原文事实转述"这一项被测到了（17/20）**，
  不能声称"原文只有 `上次` 时模型能保持不确定"已验证。列为 **F-C04-2（中）**。

### 缺陷 2（Host 渲染不对称，新，中）：prospective 有 `trigger_local`，episode 没有 `occurred_local`

- **证据**：召回片段里 prospective 由 Host 渲染出 `trigger_local`
  （如 `2026-09-08T09:00+01:00 周二`，18/18 复算逐字一致），
  **episode 只有裸 epoch `occurred_start`，没有任何本地化字段**。
- **后果，有直接实证**：本轮 20 例中，凡是模型**转述 Host 已渲染好的时刻**的地方（18 条 prospective）
  **零错误**；唯一一处模型**自己拿 epoch 做换算**的地方（C04-10）**算错了 2 天**
  （`2026-09-05T10:00` → 答成"2026-09-03 左右"）。
- **建议**：episode 片段补一个与 `trigger_local` 对称的本地化字段，
  并让它按 `precision_oracle` 同源的精度渲染（`day` → `2026-09-05 周六`、`month` → `2026-08`、
  `undated` → 省略）。这**同时**解决缺陷 1：模型不必也不能再从 epoch 反推出比原文更细的时间。
  列为 **F-EPI-1（中）**——这是本轮唯一一条"改了能同时消掉两个缺陷"的建议。

### 缺陷 3（Host 策略措辞，本轮部分兑现）：R5 生效但残留 6 例

见 §六。R5 实测把 C04 多提 **12/52 = 23.1% → 6/46 = 13.0%**，required 未受损；
残留 6 例全部落在 R5 判据内（形态覆盖 100%），属**措辞/自查性缺口**而非规则缺口。
细化方案 **R5b** 已给出确切正文与实测 token 成本（623→628 ≤ 643）。列为 **F-ETR-8（中）**。

### 缺陷 4（Memory SDK，低）：`prospective_records.scheduler_registration_ref` 仍恒为 NULL

**F-OBS-2 在 0.6.37 上未修复，本轮二次确认**：23/23 行该列为 NULL，
而同库 `prospective_scheduler_registrations.scheduler_registration_ref` 完整
（`host:prospective-registration:7be9d7…` 等）。根因不变
（`backends/sqlite_v5.py:11136-11145` 硬写 `None` + `backends/schema_v5.py:662-667` 的 immutable 触发器 = 死列）。
危害不变：只读证据审查易把"ref 为空"误读成"没登记上"。**F-OBS-2 保持开启。**

### 观察（非缺陷，记录备查）

1. **`NO_ACTIVE_GENERATION` 的归因被本轮定位了。** 该降级码出现在 **18/20** 例，
   而未出现的恰好是 **C04-16 与 C04-18** ——也恰好是 20 例中**仅有的两例
   `include_short_horizon=false`**。18/18 对 2/2，无例外。
   同批 `cognitive_vector_generation` 20/20 `activated=true, cas_miss=false, replayed=false`。
   → 该码来自**短时程车道**（没有活跃会话代可分页），**与类型化召回无关**。
   `DECISION-PROSPECTIVE-PROCEDURE-RECALL.md` §3.3 那条"应归零"的期待，
   在 `include_short_horizon=true` 的请求上本就不该归零；建议把该 followup **改写成
   "短时程车道无活跃代时是否应该降级"**，而不是继续当作未兑现的修复。
2. **两处措辞瑕疵，均不违反 gold，仅记**：
   C04-03 把 `day` 精度的 `2025年12月31日` 说成"上次跨年**夜**"（多给了时段，但"跨年夜"在中文里常指该日）；
   C04-12 把 9/4 相对固定时钟 9/6 说成"**上上**周五"（应为上周五）。
3. **`embedding=NO_LOCAL_MODEL_LOADED`、`provider_clock_projection=CONFIGURED_MAIN_PRIMARY_SEMANTIC_CLOCK_NOT_OBSERVED`**
   在 20/20 例出现，属跑道既有状态，非本轮新增。
4. **路由噪声归零**：上一轮 C04-06 的两次 `tool_search`、C04-14 的一次 `task_scope_search` 本轮均未复现，
   20/20 例 `route_effects` 恰好 1 条 `context_route`。

## 九、指标重算（机械口径）

以 `run-01.driver.log` 的 `metrics` 逐例累加，分母为 `prediction_observation_complete=true` 的 packet（20/20 全 true）：

| 指标 | 本轮 C04 | 上一轮 C04 |
|---|---|---|
| `proposed_required_matches` / `required_type_denominator` | **40 / 40 = 100%** | 40 / 40 = 100% |
| `extra_proposed_types` / `predicted_type_count` | **6 / 46 = 13.0%** | 12 / 52 = 23.1% |
| 多提的例数 | 6 / 20 = 30% | 12 / 20 = 60% |
| 多提形态 | **6/6 全部是 P4（兜底加 `semantic`）**，无 P1/P2/P3 | 12/12 同 |

与 `CORPUS-CUMULATIVE-2026-09-08.md` 已记的 09-09 02:45 那 77 例（26 / 228）合并：

- 多提类型：(26+6) / (228+46) = **32 / 274 = 11.7%（≤15% ✅）**
- required 类型召回：(136+40) / (136+40) = **176 / 176 = 100% ✅**
- 隐私违规：**0 ✅**
- **多提残留数：6**（全部是 `semantic`，全部零命中）

本次以本轮 20 例**取代**上一轮 C04 的 12/52——上一轮的夹具与本轮不同源
（payload 正文已改，内容哈希不同），两者不可相加。

## 十、后续（followup）

| 编号 | 事项 | 优先级 | 状态 |
|---|---|---|---|
| **F-EPI-1** | episode 片段补与 `trigger_local` 对称的本地化字段，并按原文精度渲染（`undated` 省略）——同时消掉 F-C04-2 与 C04-10 的换算错误（§八缺陷 2） | **中，首选** | 新 |
| **F-C04-2** | episode 合成锚点仍以精确 `occurred_start` 进入模型可见片段；在 F-EPI-1 落地前**不得声称"原文无日期时模型能保持不确定"已验证**（§八缺陷 1） | 中 | 新 |
| **F-ETR-8** | R5b：把 R5 的否定动机句改成可判定正向门（正文与 623→628 实测 token 见 §6.4），再跑一轮 C04 计量 6 → ? | 中 | 新 |
| F-OBS-2 | `prospective_records.scheduler_registration_ref` 死列；0.6.37 上未修复，二次确认 | 低 | 保持开启 |
| （关闭）**F-C04-1** | 精度注记移出模型可见正文：20/20 例、11 个关键词、输入/输出/终答三面**零命中**（§五） | — | **关闭** |
| （关闭）**F-ETR-7** | R5 落地并实测生效：23.1% → 13.0%，required 100% 未受损（§六、§九） | — | **关闭**（残留转 F-ETR-8） |
| （关闭）**F-RERUN-2** | 本轮即 Memory **0.6.37** 的计量：注册 20/20、`trigger_local` 18/18、无新增回归 | — | **关闭** |
| （改写）`NO_ACTIVE_GENERATION` | 归因已定位为短时程车道（18/18 对 2/2，见 §八观察 1）；原"应归零"的期待改写为"短时程无活跃代时是否应降级" | 低 | 改写 |
