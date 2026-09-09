# 语料 C04 第三次重跑复核记录（Prospective 切片 20 例，deepseek-v4-flash，Memory 0.6.37）

复核时间：2026-09-09 下午。性质：主代理逐例读库 + 读 trace 的终态复核，**不是人工标注**，未起子代理，单进程。
原始证据（gitignored）：`.local-test-evidence/2026-09-09/corpus-c04-flash-r3/run-01/`，
批次驱动日志 `.local-test-evidence/2026-09-09/corpus-c04-flash-r3/run-01.driver.log`（每例一行 JSON）。
方法与逐例表格沿用 `RUN-C04-RERUN-REVIEW.md`（第一轮）与 `RUN-C04-RERUN-2-REVIEW.md`（下称"上一轮"）。

本轮的**唯一新增复核项**是 **F-EPI-1 的机械验证**：`DECISION-F-EPI-1-R5B.md` §5 要求
「片段里 `occurred_local` 是否 23/23 与 `rendered_occurred_local` 逐字一致（这一项现在可以机械比对，不必人读）」。
本记录另**不满足于**该等式——因为 `precision_oracle.rendered_occurred_local` 是**直接 import Host 渲染函数**
算出来的（`DECISION-F-EPI-1-R5B.md` §1.5），两者相等只证明**接线正确**，不证明**渲染正确**。
故本轮额外用 `zoneinfo` 从 payload 的 `[occurred_start, occurred_end]` **独立复算**了全部 23 条（§五）。

## 一、范围与组合

- **20 例**，`backend/deskpet/quality/corpus_c04.py:SPECS` 的 C04-01…C04-20 全集。
- provider：**`deepseek-v4-flash`**，20/20 走 primary，`preflight.json` 各例 200，无回退、无 5xx。
- 组合：F-EPI-1 + R5b（`7d161678`，10:31 合入 main）、Memory SDK **0.6.37**（未动）。
- 批次 10:38:49–10:48:12（+08:00），串行，每例 6 GiB / 1200 s；20 例 **8 min 56 s**（worker 计时合计 549.4 s），
  中位 25.0 s，最长 53.0 s（C04-20），峰值 RSS **1.72 GiB**。
- **20/20 `driver_returncode=0`、`stop_reason` 全 None、`SETUP_BLOCKED` 0**；
  `s5c_cursor_successor_required` / `IntegrityError` / `ProspectiveSetupNotReady` / `LEDGER_TAMPERED` /
  `typed_use_projection_differs` 对整棵证据树 `grep -r` **全批零命中**。

### 批中 HEAD 变动（本轮有，必须诚实记）

上一轮那条"批中 HEAD 只动了 `plans/**` 与原生脚本"的免责，**本轮不成立**。批次跨 10:38–10:48，
其间 **10:43:59** 合入 `e6696fd6`（F-Z1b：读闸门越界走 S4 绑定提案），改动文件包含
`backend/deskpet/sdk_adapters/read_gate.py`（+502）与 **`backend/main.py`（+11，给 `WorkspaceReadGate`
注入两个惰性 getter）**——`main.py` 在语料运行期路径上（各例 `execution.initialization = main_product_factory`）。

逐例起止时刻与合入点对齐后：

| 分段 | 用例 | 所在树 |
|---|---|---|
| 10:38:49–10:44:02 | C04-01 … **C04-11** | `c15a0704`（含 F-EPI-1，**不含** F-Z1b） |
| 10:44:03–10:48:12 | **C04-12** … C04-20 | `e6696fd6`（`c15a0704` + F-Z1b） |

（C04-11 起于 10:43:39、终于 10:44:02，worker 在 10:43:39 已完成 import，合入发生在其存活期内但在其加载之后，
故归入前段。）

**"这条变动没有影响本轮结论"的实证，而不是推演**：

1. 两段的形态指标**逐项相同**：20/20 恰好 1 条 `context_route`、20/20 `NO_ACTIVE_GENERATION`、
   20/20 `origin=model_proposal`、20/20 rc 0、43 条片段 `privacy_class` 全 `personal`。
2. 最强的一条是**跨版本确定性**：两条生命周期例 C04-12 / C04-17 都在**后段**，
   而它们的 4 条 `scheduler_registration_ref`（`…7be9d7e7`、`…dd48b663`、`…e6112a66`、`…0a036c11`）
   与两条被作废记忆的完整 `memory_id`（`…66d1cc18`、`…afa11cff`）**与上一轮逐 hash 一致**（§三）。
3. 本轮的三例 `semantic` 残留**跨越该边界**（C04-06 在前段，C04-12/20 在后段），
   即残留与该变动无相关性。

**保留意见**：以上是"没看到影响"，不是"证明无影响"。C04 类不触碰工作区读闸门，
但若要一条零保留的计量，应在静止树上重跑。列为观察，不列缺陷。

### `state.db` 迁移版本变动

`PRAGMA user_version` **20/20 = 56**，上一轮是 55。第 56 号迁移是
`backend/deskpet/memory/context_use_recollect_schema.py:28 SCHEMA_VERSION = 56`，
该文件最近一次改动为今日 09:30 的 `043ecf92`（用途围栏车道），落在两批之间，**与本轮修复无关**。
C04 相关的每一张表（登记、记录、触发、结算、游标）计数与形态均与上一轮一致（§三），
迁移未带来任何 C04 侧行为变化。

## 二、总结果

| 项 | 本轮 | 上一轮 | 第一轮 |
|---|---|---|---|
| 执行 | 20 / 20（rc 0） | 20/20 | 20/20 |
| **复核裁定（gold 口径）** | **PASS 20 / FAIL 0 / INCONCLUSIVE 0** | PASS 20 | PASS 20 |
| **复核裁定（日期精度口径）** | **PASS 20 / FAIL 0** | PASS 17 / FAIL 3 | 不可计量 |
| 调度登记 | **20/20 成功**，`missing=[]`、`tick_errors=[]` 全空 | 持平 | 持平 |
| required 类型命中 | **40 / 40 = 100%** | 40/40 | 40/40 |
| 多提类型 | **3 / 43 = 7.0%** | 6 / 46 = 13.0% | 12 / 52 = 23.1% |
| 多提的例数 | **3 / 20 = 15%** | 6 / 20 = 30% | 12 / 20 = 60% |
| **`occurred_local` 与计分端逐字一致** | **23 / 23** | 字段不存在 | 字段不存在 |
| **`occurred_local` 独立复算一致** | **23 / 23** | — | — |
| `trigger_local` 独立复算一致 | **18 / 18**（+2 条 event trigger 无该字段） | 18/18 | — |
| 隐私违规 | **0** | 0 | 0 |
| provider handoff | 19 例 2 次，C04-20 4 次 | 19 例 2 次，C04-06 3 次 | — |
| `route_effects` 工具调用 | **20/20 恰好 1 条 `context_route`** | 20/20 | 有 2 例路由噪声 |

20 例 `oracle_verdict` 均为 `PENDING_POST_TERMINAL_REVIEW`——自动 oracle 正常完成的取值
（`backend/deskpet/quality/corpus_scoring.py:299`），不是失败；语义部分由本记录补齐。

## 三、注册 / 调度形态核实

逐例把 `runtime/userdata/data/human_memory_v7.db` 与 `state.db` **连同 `-wal`/`-shm` 一起拷到 scratchpad 后只读打开**
（`?mode=ro&immutable=1` 会跳过 WAL 导致漏读登记状态，第一轮踩过的坑）。

| 检查项 | 结果 | 与上一轮 |
|---|---|---|
| `state.db` `PRAGMA user_version` | 20/20 = **56** | 55 →（无关车道迁移，见 §一） |
| 游标表落点 | 20/20 写 **`prospective_outbox_cursor_v52`**，合计 **24** 行（C04-12/17 各 3 行）；封存表 `prospective_outbox_cursor` **0 行** | 持平 |
| `state.db.prospective_scheduler_registrations` | **48** 行 = 24 条命令的 `prepared`→`applied` 成对落地 | 持平 |
| `human_memory_v7.db.prospective_scheduler_registrations` | **24** 条登记事件（18 例各 1 + C04-12 三条 + C04-17 三条） | 持平 |
| `prospective_records` 行数 | **23** 行 = 20 条种子 P + C04-12 rev2 + C04-17 P_OLD rev1/rev2，**无模型新建** | 持平 |
| `prospective_trigger_events`（HM）/ `prospective_timer_events`、`prospective_occurrences`、`prospective_invalidation_terminals`（state） | **全部 0**——无触发、无结算、无新增调度 | 持平 |
| `prospective_signal_rejections` | 0；`prospective_signal_decisions` / `signal_results` / `signal_authority_consumptions` 各 **24**，与登记事件一一对应 | 持平 |
| `analysis_batches` / `accepted_analysis_plans` | 每例各 1（合计 20/20），种子写入，非模型写侧 | 持平 |
| `observation-route_effects.json` | 每例 **1 条**，`tool_name` 全为 `context_route`；无任何变更类效果 | 持平 |

两条生命周期用例与上一轮**逐 hash 一致**（跨 Host 版本、跨夹具改动的确定性）：

- **C04-12（rescheduled）**：`registration(mem…1bfc29ed, prospective_revision=1, registration_revision=1,
  ref=host:prospective-registration:7be9d7e7…)` → `invalidation(同 ref)` →
  `registration(prospective_revision=2, registration_revision=2, ref=…dd48b663…)`；
  状态 `accepted / invalidated / accepted`。召回只返回新的 9/9 09:30，旧 9/7 提醒**未进片段**。
- **C04-17（cancelled）**：`registration(押金 mem…afa11cff, ref=…e6112a66…)` +
  `registration(旧出发提醒 mem…66d1cc18, ref=…0a036c11…)` → `invalidation(…66d1cc18)`；
  **被取消的 rev2 没有再拿到任何登记**（这是对的）。召回只返回押金那条。

`prospective_invalidation_terminals` / `prospective_invalidation_terminal_receipts` 两例仍为 0 ——
`prospective_registration_source.py:82-112` 只让 "invalidation not required" 的结算分支写这两张表，
本轮两次作废都走了正常的 `registration_invalidated` 路径，**0 行是预期值，不是漏写**（沿用前两轮结论）。

### `trigger_local` 独立复算

18 条 time trigger 按 payload 的 `trigger_at` + `timezone` 用 `zoneinfo` 独立复算（含星期），
与 Host 渲染的 `trigger_local` **18/18 逐字一致**，零 mismatch；两条 event trigger（C04-10/11）
**片段里根本没有 `trigger_local` 这个键**，符合"事件触发无本地时刻"的语义。最难的两条同样对齐：

| 用例 | Host 渲染 | 独立复算 |
|---|---|---|
| C04-06（Europe/London，BST） | `2026-09-08T09:00+01:00 周二` | 一致 ✅ |
| C04-03（跨年） | `2027-01-02T10:00+08:00 周六` | 一致 ✅ |

## 四、逐例裁定

`extra` = 该例多提的类型数；`精度` = 该例对**被引用记忆的时间表述**是否不超出该记忆的 Host 渲染精度。

| 用例 | gold | 精度 | memory_types | extra | 注册 | 依据 |
|---|---|---|---|---|---|---|
| C04-01 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月4日验样封面偏暗」+「周一 2026-09-07 09:00 索取修正版」；明说"只列，不执行" |
| C04-02 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「8月31日盘点缺两本」+「9/8 10:00 补登记」 |
| C04-03 | PASS | PASS | episode, prospective | 0 | 1 accepted | **年份分离正确**：2025-12-31 缺照片 / 2027-01-02 10:00 补拍。**上一轮的"跨年夜"措辞瑕疵本轮消失**，只说「2025年12月31日（周三）」 |
| C04-04 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月5日试用导出成功」+「9/10 15:00 清点导出副本」；明说不执行。**上一轮的多提本轮消失** |
| C04-05 | PASS | PASS | episode, prospective | 0 | 1 accepted | 上午 09:00「打印偏色」/ 下午 15:00「校色通过」两条 minute 记忆分别对上；周一 11:00 领样 |
| C04-06 | PASS | PASS | **+semantic** | 1 | 1 accepted | 「9月4日远程访谈音频缺段」+ 换算「伦敦 09:00 BST（+01:00）= 北京 16:00」；未通知任何人 |
| C04-07 | PASS | PASS | episode, prospective | 0 | 1 accepted | 按**事件发生时间** 2026-08-20 定位「页码错序」；复核 9/9 14:00 |
| C04-08 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月5日交接少一份表」+「9/7 08:30 补送」；"昨天/明天"以固定时钟 9/6 换算正确 |
| C04-09 | PASS（边界） | PASS | episode, prospective | 0 | 1 accepted | 取 E2「8月31日–9月6日那周」，E1「8月24–30那一周」**明确归为前一周**；两条 week 记忆均按"周"表述。**上一轮的多提本轮消失** |
| **C04-10** | PASS | **PASS（上一轮 FAIL）** | episode, prospective | 0 | 1 accepted | gold 满足（漏借阅卡 + 事件触发归档回执，未触发）。**终答里日期/时刻 token 数为 0**，并主动写「这条记忆里没有记录具体发生日期，所以我无法告诉你是哪一天」——上一轮那句「发生在 2026-09-03 左右」（差 2 天）**彻底消失** |
| C04-11 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月5日试印失败、颜色不符」+ 条件触发寄样；明写"条件尚未满足、未触发" |
| C04-12 | PASS | PASS | **+semantic** | 1 | 3（含 1 作废） | 「9月4日因场地检修推迟」+「9/9 09:30 确认新场地」；未把旧 9/7 提醒当待办。**上一轮的"上上周五"措辞瑕疵本轮消失**（写「9月4日（周五）」） |
| C04-13 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「9月3日申请材料缺签名」+「9/9 10:00 补签」，**把 9/10 截止日与提醒时间明确分开**。**上一轮的多提本轮消失** |
| **C04-14** | PASS | **PASS（上一轮 FAIL）** | episode, prospective | 0 | 1 accepted | gold 满足（去重完成 / 第二阶段未开始 +「9/8 14:00 开始格式检查」）。**终答里唯一的日期是提醒的 2026-09-08**，第一阶段一处日期都没给——上一轮的「第一阶段（上次，2026-09-05）」**消失** |
| C04-15 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「三季度清点缺备份索引（2026-09-30 周三）」+「2026-10-02（周五）10:00 补索引」；minute 精度记忆只说到日，属**收窄内的收敛**，未越界 |
| C04-16 | PASS（边界） | PASS | episode, prospective | 0 | 1 accepted | 取 E2「2026年9月4日试课计时超长」+「9/7 12:00 缩短练习」；E1 只写「更早的一次（**7月**）」，month 精度守住 |
| **C04-17** | PASS | **PASS（上一轮 FAIL）** | episode, prospective | 0 | 3（含 1 作废） | gold 满足（天气取消 + 9/10 16:00 核对押金；旧出发提醒既未召回也未复活）。**终答写「2026年8月的团体出游」，全文无"上/中/下旬"**——上一轮的「8月中旬」消失 |
| C04-18 | PASS | PASS | episode, prospective | 0 | 1 accepted | 「2026年8月12日（周三）修订封面缺作者名」+「2026-09-12（周六）09:00 交修正版」。**上一轮的多提本轮消失** |
| C04-19 | PASS | PASS | episode, prospective | 0 | 1 accepted | **未编造成功**：明写"结果尚未确认、没有存下最终结果"；+「9/8 10:00 追问测试结果」 |
| C04-20 | PASS | PASS | **+semantic** | 1 | 1 accepted | 「9月4日**夜间**传稿漏附件」（night 精度只交出日历日，"夜间"来自记忆正文）+ 双时区「上海 2026-09-08 00:30 周二 / UTC 2026-09-07 16:30」，换算正确，日期翻转按**一条**提醒处理 |

### 负控（"不声称触发 / 不新增调度 / 不结算"）

C04 全类 gold 的 `required_types` 一律 `[episode, prospective]`，没有"含糊愿望不建提醒"形态的负控例；
本类的负向要求是上面这条，逐例核对 **20/20 满足**，硬证据：
`prospective_records` 行数恰等于 fixture 种子数（23），trigger / timer / occurrence 三表全 0，
`route_effects` 每例仅 1 条读取型调用。

C04-06 与 C04-20 结尾都出现"会照此办理 / 会按两个时区一并标注"这类承诺意味的措辞，
但**两例都同时写明**"实际修改由后台流程完成，在收到确认结果前不声称已完成持久化修改"，
且库里零新增、零效果。不构成违规，仅记（较上一轮的同类措辞更收敛）。

## 五、F-EPI-1 验证（本轮核心）

### 5.1 片段形态：字段按设计出现与缺席

43 条片段的键集合逐条钉死，只有四种形态，与 `DECISION-F-EPI-1-R5B.md` §1.6「片段键集合变」一致：

| 形态 | 条数 | 说明 |
|---|---|---|
| episode **带** `occurred_local` | **21** | 有效时间区间存在 |
| episode **不带** `occurred_local` | **2** | C04-10 / C04-14，`occurred_end is None`（`undated`）→ 字段整个不出现 ✅ |
| prospective **带** `trigger_local` | **18** | time trigger |
| prospective **不带** `trigger_local` | **2** | C04-10 / C04-11，event trigger |

episode 片段的 `payload` **仍原样带着裸 `occurred_start`**（C04-10 的 `1788573600.0` 一字未删）——
这正是 `DECISION-F-EPI-1-R5B.md` §1.4 记的、为守住 typed-use carrier 逐字节比对不变量而**明确不做**的那一半，
本轮实证：`typed_use_projection_differs` 全批零命中，投影完整性没有为本条被削弱。

### 5.2 `occurred_local` == `rendered_occurred_local`：23 / 23 逐字一致

按 `(occurred_start, occurred_end)` 把片段与 `setup_receipt.precision_oracle` 的 episode 记录一一配对
（无一例歧义），逐字比较：

| 用例 | label | 原文精度 | 原文措辞 | `rendered_occurred_local`（计分端） | 片段 `occurred_local`（模型可见） |
|---|---|---|---|---|---|
| C04-01 | E | day | 9月4日 | `2026年9月4日 周五` | 同 ✅ |
| C04-02 | E | day | 8月31日 | `2026年8月31日 周一` | 同 ✅ |
| C04-03 | E | day | 2025年12月31日 | `2025年12月31日 周三` | 同 ✅ |
| C04-04 | E | day | 9月5日 | `2026年9月5日 周六` | 同 ✅ |
| C04-05 | E1 | minute | 9月5日09:00 | `2026-09-05T09:00+08:00 周六` | 同 ✅ |
| C04-05 | E2 | minute | 当天15:00 | `2026-09-05T15:00+08:00 周六` | 同 ✅ |
| C04-06 | E | day | 9月4日 | `2026年9月4日 周五` | 同 ✅ |
| C04-07 | E | day | 8月20日 | `2026年8月20日 周四` | 同 ✅ |
| C04-08 | E | day | 9月5日 | `2026年9月5日 周六` | 同 ✅ |
| C04-09 | E1 | week | 8月24–30周 | `2026年8月24–30日那周` | 同 ✅ |
| C04-09 | E2 | week | 8月31–9月6周 | `2026年8月31日–9月6日那周` | 同 ✅ |
| **C04-10** | **E** | **undated** | **上次** | **`null`** | **键不存在 ✅** |
| C04-11 | E | day | 9月5日 | `2026年9月5日 周六` | 同 ✅ |
| C04-12 | E | day | 9月4日 | `2026年9月4日 周五` | 同 ✅ |
| C04-13 | E | day | 9月3日 | `2026年9月3日 周四` | 同 ✅ |
| **C04-14** | **E** | **undated** | **未指定日期** | **`null`** | **键不存在 ✅** |
| C04-15 | E | minute | 2026年9月30日16:00 | `2026-09-30T16:00+08:00 周三` | 同 ✅ |
| C04-16 | E1 | month | 7月首次 | `2026年7月` | 同 ✅ |
| C04-16 | E2 | day | 9月4日最近 | `2026年9月4日 周五` | 同 ✅ |
| **C04-17** | **E** | **month** | **8月** | **`2026年8月`** | 同 ✅ |
| C04-18 | E | day | 2026年8月12日 | `2026年8月12日 周三` | 同 ✅ |
| C04-19 | E | day | 9月4日 | `2026年9月4日 周五` | 同 ✅ |
| C04-20 | E | night | 9月4日夜间 | `2026年9月4日 周五` | 同 ✅ |

**任何非 `minute` 精度的渲染串里都不含 `T` 与 `:`** —— 20 条粗精度记忆再也交不出钟点给模型，
与 `DECISION-F-EPI-1-R5B.md` §1.3 的控制断言一致。

### 5.3 独立复算：不信 oracle，只信区间

如开头所述，5.2 的等式只证明接线。本轮另用 `zoneinfo` 从 payload 的
`[occurred_start, occurred_end]` 按 `DECISION-F-EPI-1-R5B.md` §1.2 的语义约定**独立重写了一遍渲染器**
（`end is None` → 无字段；`end == start` → 完整时刻；否则只按覆盖的日历天渲染：
一天 / 整月 / 恰好七天 / 其他显式范围），不 import 任何 Host 代码：

**23 / 23 与片段里的 `occurred_local` 逐字一致，零 mismatch。**

其中三条最能分辨实现的：`2026年7月`（整月：`08-01 00:00` 起、`09-01 00:00` 止，
`occurred_start` 已按 §1.3 前移到当月 1 日）、`2026年8月24–30日那周`（同月七天）、
`2026年8月31日–9月6日那周`（**跨月**七天，格式随之切换）。

### 5.4 终答：三个失败点全部转 PASS，17 个负控无一越界

对 20 例终答做机械日期扫描（正则抓 `YYYY年M月D日` / `M月D日` / `YYYY年M月` / `HH:MM` / `M月` /
`上/中/下旬` / `周X`），再逐条归属：

| 复核清单项（`DECISION-F-EPI-1-R5B.md` §5） | 结果 |
|---|---|
| ① C04-10 / C04-14 是否**不再给出任何日期** | **PASS**。C04-10 终答的日期/时刻 token 数 **= 0**（全文一个都没有），并主动声明"没有记录具体发生日期"；C04-14 全文仅 `2026-09-08` / `14:00` / `周二` 三个 token，**全部属于 prospective 的 `trigger_local`**，第一阶段零日期 |
| ② C04-17 是否只说「8月」、不再出现「中旬」 | **PASS**。终答写「2026年8月的团体出游」，另一处 `9月10日 16:00` 属提醒；全文无「上旬/中旬/下旬」 |
| ③ 17 个负控例是否仍在渲染精度之内 | **PASS 17/17**。逐例把终答里每个日期 token 归属到该例的 `occurred_local` 或 `trigger_local`，无一条超出：`week` 只说到周（C04-09）、`month` 只说到月（C04-16 E1「7月」、C04-17「2026年8月」）、`night` 只说到日 + 记忆自带的"夜间"（C04-20）、`day` 只说到日 |
| ④ 6 例多提是否已不含 `semantic` | 部分：**6 例中 4 例（C04-04/09/13/18）转干净**，C04-12/20 未转，另**新增 C04-06**。详见 §六 |
| ⑤ 23/23 `occurred_local` 与 `rendered_occurred_local` 逐字一致 | **PASS**（§5.2），并加做独立复算（§5.3） |

**唯一一处收窄性措辞**：C04-05 把 minute 精度的 `09:00` 说成「上午 9:00 **左右**」。
这是把精确值**放宽**，不是超出精度，不构成越界；仅记。

**"模型自己拿 epoch 反算日期"的通道本轮零使用**：上一轮唯一一处自算（C04-10，差 2 天）在本轮不存在，
且没有任何终答出现裸 epoch 或由裸 epoch 换算出的日期。

### 5.5 `temporal_hint`：只在该出现时出现，且不含任何 per-case 内容

- **命中 2 / 20**，恰为 C04-10 与 C04-14 —— 也恰为全批仅有的两例"返回了没有 `occurred_local` 的 episode 片段"。
  其余 18 例结果 extras 里**根本没有这个键**，与 §1.4「没有这种片段的轮次逐字节不变、零 token 成本」一致。
- 两例的 `temporal_hint` 对象 sha256 **同一个**（`c46e861b9ea574a9`），即它是 fragments 的纯函数，
  不含 query、不含 gold、不含任何 per-case 期望。正文：

  > `occurred_local` is the Host-rendered occurrence time at the precision the memory actually states;
  > report it as written and never recompute a date from `occurred_start`. An episode fragment carrying
  > no `occurred_local` records no occurrence time at all: describe when it happened only in that
  > memory's own words, and state no date, month or weekday for it.
  > （`reason=episode_fragment_states_no_occurrence_time`，`next=answer_without_inventing_a_date`）

- **两例的终答与这条提示逐条对上**：C04-10 用了「上次」这个记忆自己的措辞并明说没有日期；
  C04-14 用了「上次 / 已完成」而不给日期。这是本轮**唯一**一条能把"修复 → 行为"直接连起来的因果链。

### 5.6 F-C04-1 无回归

对 20 例**全部模型可见文本**（`request_json.messages` + 工具 schema、`response_json`）
扫 `精度 / 夹具 / 锚点 / 评分 / 原时间 / 原文 / gold / oracle / fixture / synthetic / precision / scoring` 共 11 词：

**除 `precision` 2 次外全部 0 命中；那 2 次就是 C04-10 / C04-14 的 `temporal_hint` 英文正文里的
"at the precision the memory actually states"**——设计上就是模型可见的、与用例无关的通用句，不是注记外泄。
`鉴定：F-C04-1 保持关闭`。计分端字段（`precision_oracle.*.note` 与新增的
`occurred_start / occurred_end / rendered_occurred_local`）仍只在 `execution.json` /
`review-packet.json` 里，**未进任何 prompt**。

### 5.7 F-EPI-1 / F-C04-2 裁定

- **F-EPI-1：验收通过，关闭。** 对称字段落地、23/23 渲染正确（含独立复算）、缺席语义正确、
  提示条件下发且无 per-case 污染、三个目标例全部转 PASS、17 个负控无一回归。
- **F-C04-2：关闭，但改述后关闭。** 原文写的是"episode 合成锚点仍以精确 `occurred_start`
  进入模型可见片段"——**这一条事实依然成立**（§5.1，且按 §1.4 是有意保留）。
  它之所以可以关闭，不是因为锚点消失了，而是因为它**不再是模型回答日期时的信息来源**：
  本轮 20/20 例的日期表述全部可归属到 Host 渲染串，零例反算。
  上一轮那句"在 F-EPI-1 落地前不得声称『原文只有上次时模型能保持不确定』已验证"，
  **本轮已获验证**：C04-10 / C04-14 在没有任何本地化时间字段时，保持了不确定并明说了出来。

## 六、R5b 残留（3 例仍多提 `semantic`）

### 6.1 模型看到的选择策略正文（20 例逐 hash 相同）

`context_route.parameters.properties.memory_types.description` 在 20 例的 provider 请求里
sha256 前 16 位**唯一**（`276ddf4c11d162bc`），即 3 个残留例与 17 个干净例看到的是**同一段字**：

```
Required for memory_standalone. Request only types that can hold the answer; extras return nothing
and spend the budget. semantic: facts, preferences, standing agreements - use alone for "what is my
habit/format/unit/agreement", including when worded "as I said before"; if the request names no such
standing value, omit it - what happened plus which reminder you set names none. episode: only when
the answer needs the past occurrence itself - what happened, when, with whom, how it ended; a past
reference alone is not such a question. prospective: only for a future intention, reminder, deadline
or trigger; scheduling words in a preference question are not one. procedure: typed recall returns
only an already-bound Procedure, never a workflow saved but not yet used - call procedure_discover
instead when the request mentions steps or a checklist. An empty list requires
include_short_horizon=true. Selection grants no permission to disclose or execute.
```

R5b 的模型可见半句即
`if the request names no such standing value, omit it - what happened plus which reminder you set names none`
（`backend/deskpet/memory/recall_selection.py:13-16`），
以及 §3.2 的供给性压缩 `An empty list requires include_short_horizon=true`
（原 `is valid only with`）—— **两处逐字落地，与 `DECISION-F-EPI-1-R5B.md` §3 一致**。

### 6.2 Host 咨询码：3/3 命中，17/17 不命中

| 用例 | `requested_memory_types` | `selection_policy_departures` |
|---|---|---|
| C04-06 | `[episode, prospective, semantic]` | `[semantic_fallback_on_reminder_lifecycle_request]` |
| C04-12 | `[**semantic**, episode, prospective]` | 同上 |
| C04-20 | `[**semantic**, episode, prospective]` | 同上 |
| 其余 17 例 | `[episode, prospective]` | `[]` |

3 例的 `semantic` 片段返回数：**0**（43 条片段全为 episode / prospective）——多提依旧 100% 空手而归。
20/20 例 `origin=model_proposal`、`include_short_horizon=true`。

### 6.3 判据覆盖仍 100%，但**残留已不再是同一种东西**

用 `backend/.venv` 直接跑 `indicates_reminder_lifecycle_request`：
**20 条模型 `query` 20/20 命中，20 条用户原话 20/20 命中**——形态覆盖没有退化。
但把三例的用户原话摊开看，它们**不是同一类**：

| 用例 | 用户原话 | 请求里有没有"长期值"？ | 性质 |
|---|---|---|---|
| **C04-12** | 「讨论为什么延期？现在有效的场地确认提醒是哪天？」 | **没有**，一个词都点不出 | **R5b 的纯违反**——正向门写得再清楚，模型仍加了 `semantic` |
| **C04-06** | 「访谈出了什么问题？补录提醒**换成北京时间**告诉我，先别通知任何人。」 | **有争议**：请求确实点名了一个**时区/时间格式** | R5b 的门**被这类请求满足了**，模型加 `semantic` 不违反它读到的那句话 |
| **C04-20** | 「夜间传稿漏了什么？把补交提醒**同时按上海和 UTC 标注日期时间**。」 | 同上 | 同上 |

关键在于 R5b 的门是 **"请求有没有点名一个长期值"**，而 R1 给"长期值"举的例子正是
`habit / format / unit / agreement`。「换成北京时间」「按上海和 UTC 标注」在自然语言里
**就是在点名一个 format/unit**。所以对 C04-06 / C04-20 而言，
**模型的选择与它读到的规则是自洽的**——它在问"我平时的时间标注口径是什么"，
而这正是 `semantic` 该装的东西（只不过本语料的 fixture 里没种这条记忆，于是返回 0 条）。

Host 侧的判据函数**看不到这一层**：`STANDING_VALUE_MARKERS` 里有 `格式 / 单位 / 方式 / 标准`，
但没有「时区 / 北京时间 / UTC / 标注」，所以 `indicates_reminder_lifecycle_request`
对这两例返回 True、记下咨询码。**咨询码与 R5b 的门在这两例上口径不一致**，
这是本轮新暴露的一处**观测口径问题**，不是模型问题。

**跨三轮的成员变化**（同模型、同语料、只差策略正文一句）：

| 轮次 | 多提例 | 集合 |
|---|---|---|
| 第一轮（无 R5） | 12 | {02,04,05,10,12,13,14,15,17,18,19,20} |
| 上一轮（R5） | 6 | {04,09,12,13,18,20} |
| **本轮（R5b）** | **3** | **{06,12,20}** |

R5 → R5b 净减 3 例（4 例消失：04/09/13/18；1 例新增：06）。
三轮单调下降 23.1% → 13.0% → **7.0%**，required 三轮均 100%，
**R5b 一次也没有伤到应召回的类型**。但按上一轮已记的采样噪声量级（≥1 例），
6 → 3 的差异**方向可信、幅度不可单轮定论**。

### 6.4 要不要 R5c：不建议，且**目前也付不起**

按任务口径，只有"残留是措辞缺口"才提 R5c。结论是**只有 1/3 是措辞缺口**：

- **C04-12** 是真缺口，但它是**单例**。上一轮已实测单轮采样噪声 ≥1 例（C04-09 的新增），
  为一个单例改动每请求受保护正文，**收益无法与噪声分辨**。
- **C04-06 / C04-20** 不是措辞缺口，而是**规则边界之外的真实歧义**：请求确实点名了一种时间标注口径。
  想让它们不加 `semantic`，需要的不是"把规则说得更清楚"，而是"把规则改窄"
  ——即声明"时区/时刻标注不算长期值"。这是一次**语义变更**，会影响
  C01/C02/C06 里那些 gold **确实需要** `semantic` 的格式/单位类请求，风险不对称，
  **不应为 2 个零命中的多提去承担**。

**成本侧（实测，非估算）**——即便想做，本轮也付不起：

| 闸 | 现值 | 余量 |
|---|---|---|
| `test_policy_keeps_the_route_schema_inside_its_measured_token_cost`（`context_route` schema ≤ 643） | **625** | 18 |
| `test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn` | PERSONA 1122 + schemas 1165 = fixed **2287**，variable 3038，planned **5325**，effective **5325** | **0** |

三个候选正文的实测 `context_route` schema 增量：

| 候选 | schema tokens | Δ | 643 余量 | 8192 档 |
|---|---|---|---|---|
| 现状（R5b） | 625 | — | 18 | planned 5325 = effective 5325，**恰好绿** |
| `…names none, nor does a timezone.` | 630 | **+5** | 13 | **5330 > 5325，红** |
| `…names none; a timezone to convert is not one.` | 634 | +9 | 9 | **红** |
| 在 episode 子句前插入完整一句 | 641 | +16 | 2 | **红** |

**最便宜的候选也是 +5，而 8192 档余量是 0 —— 任何一个 token 都会打红那道闸。**
`DECISION-F-EPI-1-R5B.md` §3.2 已经把该档的"零头"用掉了
（`is valid only with` → `requires`，−3，刚好抵掉 R5b 的 +5 里的 3）。
要再加字，必须先满足该测试自己写的两条出口之一：**压缩别处**，或
**"re-derive the scenario in DECISION-TOKEN-ESTIMATOR.md"（需要一次真实 Run）**。本轮两者都不做。

**裁定：F-ETR-8 关闭（R5b 已实测生效：13.0% → 7.0%，required 未受损）。不提 R5c。**
残留改为两条更便宜、更对症的后续：

- **F-ETR-9（低，Host 侧零 token）**：把「时区 / 北京时间 / UTC / 标注」一类词纳入
  `STANDING_VALUE_MARKERS` 的考量，使咨询码与 R5b 的门口径一致——
  否则 C04-06 / C04-20 会长期以"违规"身份出现在报表里，而它们并不违规。
  这只改 Host 观测面，**不进 prompt、零 token**，但必须先核它不会误伤 C01/C02/C06 的 gold 命中。
- **F-ETR-10（低）**：C04-12 这一单例是否稳定，需靠**多轮同配置重跑**分辨，不靠改正文。

## 七、隐私

**C04 无 `privacy_allowed=false`、无 `no_recall=true` 用例——本轮机械确认，与前两轮一致。**

- 20 例 `oracle.labels`：`privacy_allowed` 全 `true`、`no_recall` 全 `false`、
  `hard_trigger` 全 `无`、`required_procedure_access` 全 `null`、`requires_task_scope_search` 全 `false`。
- 返回的 **43 条片段 `privacy_class` 全为 `personal`**（无 `recipient_private` 一类）。
- 无第三方披露：唯一涉及"通知别人"的 C04-06 明确说明"先不通知任何人，目前不会有任何通知发出"，
  且 `route_effects` 仅 1 条读取型调用。
- **隐私违规 0**。

## 八、缺陷与观察

### 缺陷（新增）：无

本轮**没有发现需要修复的新缺陷**。上一轮列出的三条中缺陷全部处置完毕（§5.7、§6.4）。

### 观察 1（记录，中）：批中 HEAD 变动落在运行期路径上

见 §一。`main.py` 在 `main_product_factory` 路径上，本批 C04-12…C04-20 跑在改后的树上。
已用三条实证说明未见影响（形态指标逐项相同、跨版本 hash 确定性、残留与边界无相关），
但这是"没看到"，不是"证明无"。**建议**：下一次语料批次前先确认无并发车道要合入，
或把批次改成在 detached HEAD 上跑。这是流程建议，不是代码缺陷。

### 观察 2（沿用并加强）：`NO_ACTIVE_GENERATION` 的归因二次确认

上一轮定位该码来自**短时程车道**，证据是 18/20 例出现、未出现的 C04-16/18 恰是仅有的两例
`include_short_horizon=false`（18/18 对 2/2）。**本轮 20/20 例 `include_short_horizon=true`，
该码也 20/20 出现，无一例外**——同一假说在"全部为真"的这一侧再次成立。
同批 `cognitive_vector_generation` 20/20 `activated=true, cas_miss=false, replayed=false`。
`DECISION-PROSPECTIVE-PROCEDURE-RECALL.md` §3.3 那条"应归零"的期待，
按上一轮的改写口径（"短时程无活跃代时是否应该降级"）保持。

### 观察 3（沿用）：F-OBS-2 死列在 0.6.37 上三次确认

`prospective_records.scheduler_registration_ref` **23/23 行为 NULL**，
而同库 `prospective_scheduler_registrations.scheduler_registration_ref` 完整
（`host:prospective-registration:7be9d7e7…` 等）。根因不变
（`backends/sqlite_v5.py:11136-11145` 硬写 `None` + `backends/schema_v5.py:662-667` 的 immutable 触发器 = 死列）。
`DECISION-F-EPI-1-R5B.md` §4 已核实无语料代码读它、并在唯一易误认处加了辨析注释。
**F-OBS-2 保持开启（低），归属 Memory SDK 侧。**

### 观察 4（新，低）：C04-20 的 handoff 从 2 升到 4

C04-20 本轮 4 次 provider handoff、53.0 s（全批最长），而它只发了 1 次工具调用。
即模型在作答前多绕了两轮。终答正确（双时区换算无误），不影响裁定；
若后续出现同类膨胀再立项。

### 观察 5（记录）：`state.db` user_version 55 → 56

见 §一。来自无关车道的迁移（`context_use_recollect_schema`，今日 09:30 `043ecf92`），
C04 相关表计数与生命周期 hash 均未变。

## 九、指标重算（机械口径）

以 `run-01.driver.log` 的 `metrics` 逐例累加，分母为 `prediction_observation_complete=true` 的 packet（20/20 全 true）：

| 指标 | 本轮 C04 | 上一轮 | 第一轮 |
|---|---|---|---|
| `proposed_required_matches` / `required_type_denominator` | **40 / 40 = 100%** | 40/40 = 100% | 40/40 = 100% |
| `extra_proposed_types` / `predicted_type_count` | **3 / 43 = 7.0%** | 6 / 46 = 13.0% | 12 / 52 = 23.1% |
| 多提的例数 | **3 / 20 = 15%** | 6/20 = 30% | 12/20 = 60% |
| 多提形态 | **3/3 全部是 P4（加 `semantic`）**，无 P1/P2/P3 | 6/6 同 | 12/12 同 |

与 `CORPUS-CUMULATIVE-2026-09-08.md` 已记的 09-09 02:45 那 77 例（26 / 228）合并：

- 多提类型：(26+3) / (228+43) = **29 / 271 = 10.7%（≤15% ✅）**
- required 类型召回：(136+40) / (136+40) = **176 / 176 = 100% ✅**
- 隐私违规：**0 ✅**
- **多提残留数：3**（全部是 `semantic`，全部零命中；其中仅 1 例是规则的纯违反）

本次以本轮 20 例**取代**上一轮 C04 的 6/46——上一轮的夹具与本轮不同源
（23 条 episode 的 `occurred_end` 由 `null` 变为数值、两条 month 的 `occurred_start` 前移，
内容哈希全变，见 `DECISION-F-EPI-1-R5B.md` §5），两者不可相加。

## 十、后续（followup）

| 编号 | 事项 | 优先级 | 状态 |
|---|---|---|---|
| （关闭）**F-EPI-1** | episode 片段 `occurred_local`：23/23 与计分端逐字一致、23/23 独立复算一致、`undated` 2/2 正确缺席、`temporal_hint` 2/2 条件下发且无 per-case 污染（§五） | — | **关闭** |
| （关闭）**F-C04-2** | 三个越界例全部转 PASS、17 个负控无回归、20/20 日期表述可归属到 Host 渲染串、零例从 epoch 反算；裸锚点仍在 payload 里是**有意保留**（typed-use 不变量），已改述后关闭（§5.7） | — | **关闭** |
| （关闭）**F-ETR-8** | R5b 实测生效：13.0% → **7.0%**，required 100% 未受损；正文与 −3 供给逐字落地（§六） | — | **关闭** |
| **F-ETR-9** | 咨询码与 R5b 的门在"时区/标注"类请求上口径不一致（C04-06/20 被记为违规但并不违规）；建议只改 Host 观测面（`STANDING_VALUE_MARKERS`），**零 token**，须先核不误伤 C01/C02/C06 的 gold（§6.3、§6.4） | 低 | 新 |
| **F-ETR-10** | C04-12 的单例残留是否稳定，靠多轮同配置重跑分辨，不靠改正文；**不提 R5c**（最便宜候选 +5 token，8192 档余量为 0，付不起；且 3 例里只有 1 例是措辞缺口）（§6.4） | 低 | 新 |
| **F-BATCH-1** | 语料批次期间禁止其他车道向 main 合入运行期路径改动（本轮 F-Z1b 在 10:43:59 落在批中，C04-12…20 跑在改后的树上）；建议 detached HEAD 跑批（§一、§八观察 1） | 低 | 新（流程） |
| F-OBS-2 | `prospective_records.scheduler_registration_ref` 死列；0.6.37 上三次确认未修复 | 低 | 保持开启 |
| （保持改写）`NO_ACTIVE_GENERATION` | 短时程车道归因二次确认（本轮 20/20 `include_short_horizon=true` → 20/20 出现，与上一轮 18/18 对 2/2 互补） | 低 | 改写后保持 |
| （保持关闭）F-C04-1 | 模型可见面 11 词扫描仅 2 次 `precision`，来自 `temporal_hint` 的通用英文正文，非注记外泄（§5.6） | — | 关闭 |

## 十一、8192 档余量的公开警告

本记录把 `DECISION-F-EPI-1-R5B.md` §3.2 的诚实记录**实测复现并升格为对全仓的警告**：

```
PERSONA 1122 + 四个产品 schema 1165 = fixed 2287
variable 3038（MEGABYTE_PEAK_PLANNED - MEGABYTE_PEAK_FIXED，冻结）
planned 5325   effective_input_budget(8192) = 5325   余量 0
```

**任何往 `PERSONA` 或四个产品 schema 的 description 里加字的车道，现在会直接打红
`test_persona_and_route_schema_still_fit_the_8192_tier_megabyte_turn`。**
出口只有两条（该测试自己写的）：先在同一受保护面里压缩等量 token，
或按 `DECISION-TOKEN-ESTIMATOR.md` 用一次真实 Run 重推 `MEGABYTE_PEAK_*` 两个常量。
本轮不提 R5c 的**第一位理由是规则边界（§6.4），第二位理由才是这道闸**——
但两条各自独立成立。
