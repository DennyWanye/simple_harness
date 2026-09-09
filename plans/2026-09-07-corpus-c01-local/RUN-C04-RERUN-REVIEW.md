# 语料 C04 重跑复核记录（Prospective 切片 20 例，deepseek-v4-flash）

复核时间：2026-09-09 上午。性质：主代理逐例读库 + 读 trace 的终态复核，**不是人工标注**，未起子代理。
原始证据（gitignored）：`.local-test-evidence/2026-09-09/corpus-c04-flash/run-01/`，
批次驱动日志 `.local-test-evidence/2026-09-09/corpus-c04-flash/run-01.driver.log`（每例一行 JSON）。
方法沿用 `RUN-RERUN-FLASH-01-REVIEW.md`：gold 取 `原文 04-time.md`（每例 `scoring/original-documents.json`）
与 `oracle.json` 的 `oracle_source_text` / `labels`，逐项核对终答，再回到 DB 与日志核实登记形态。

## 一、范围与组合

- **20 例**，即 `backend/deskpet/quality/corpus_c04.py:SPECS` 的 C04-01…C04-20 全集。
- provider：**`deepseek-v4-flash`**，20/20 走 primary，无回退、无 5xx（`preflight.json` 各例 200）。
- 组合：Host 批次起跑时 HEAD 为 `96785a60`（已含 s5c 修复 `48617f73`，04:08 合入）、
  Memory SDK **0.6.34**（`dbf967fc` 05:13 钉；批次后 `60ab03a1` 08:09 才升到 0.6.37，**本轮测的不是 0.6.37**）。
- 批次经 `scripts/run_corpus_batch.py` → 每例 `scripts/run_resource_bounded.py`（6 GiB / 1200 s），串行；
  20 例 **10.3 min**，中位 28.8 s，最长 73.7 s（C04-06），峰值 RSS 1.78 GiB；
  **20/20 `driver_returncode=0`、`stop_reason` 全 None、`SETUP_BLOCKED` 0**（上一轮同一切片是 19/20 阻塞）。

**批中 HEAD 变动的诚实说明**：批次跨 07:58:57–08:09:27（+08:00）。其间 HEAD 动过三次，其中
`d2409b0f`（08:05:25，事件 W 线上输入预算门）改到了运行期路径
`backend/deskpet/sdk_adapters/provider.py` 与新增 `wire_input_budget.py`，
理论上影响 **C04-13…C04-20 共 8 例**（C04-13 进程 08:05:28 启动）。
实测该门**一次都没触发**：20 例 `backend.log` 里 `wire_input_budget` 零命中，故不影响本轮结论。
另两次（`ebcdfd07` / `a0a869f4`）只改 `scripts/native/*.sh`，与语料跑道无关。

## 二、总结果

| 项 | 数值 |
|---|---|
| 执行 | 20 / 20（rc 0） |
| **复核裁定** | **PASS 20 / FAIL 0 / INCONCLUSIVE 0** |
| 调度登记 | **20/20 成功**，`missing=[]`、`tick_errors=[]` 全空 |
| required 类型命中 | **40 / 40 = 100%** |
| 多提类型 | **12 / 52 = 23.1%**（12 例各多提一个 `semantic`） |
| 隐私违规 | 0（20 例 `privacy_allowed=true`，无越权披露） |
| provider handoff | 19 例 2 次（一次工具 + 一次作答），C04-06 3 次；零空召回循环 |
| 路由 | 20/20 首选 `context_route(route=memory_standalone)`，`origin=model_proposal`，`selection_policy_departures` 全空 |

20 例 `oracle_verdict` 均为 `PENDING_POST_TERMINAL_REVIEW`——这是**自动 oracle 正常完成**的取值
（`backend/deskpet/quality/corpus_scoring.py:299`：`completed` 为真即取此值），
不是失败，与上一轮的 `SETUP_BLOCKED` 是两回事。语义部分即由本记录补齐。

## 三、注册 / 调度形态核实（本轮的主要目的）

逐例读了 `runtime/userdata/data/human_memory_v7.db` 与 `state.db`（只读、拷贝到 scratchpad 后打开；
注意 `?mode=ro&immutable=1` 会**跳过 WAL** 导致漏读，本轮改用拷贝全套 `-wal/-shm` 后读取）。

| 检查项 | 结果 |
|---|---|
| `state.db` `PRAGMA user_version` | 20/20 = **55**（正是上一轮触发缺陷的版本） |
| 游标表落点 | 20/20 写 **`prospective_outbox_cursor_v52`**；封存表 `prospective_outbox_cursor` **0 行** |
| `s5c_cursor_successor_required` / `IntegrityError` / `ProspectiveSetupNotReady` | **全批零命中**（`grep -r` 覆盖 20 例 `backend.log` + `worker.log` + 全部 JSON 证据） |
| `state.db.prospective_scheduler_registrations` | 每条命令 `prepared`→`applied` 成对落地，`authority_json.intent.scheduler_registration_ref` 与 HM 侧一致 |
| `human_memory_v7.db.prospective_scheduler_registrations` | 23 条记录对应 23 个登记事件，状态见下 |
| `prospective_records` 行数 | 23 行 = 20 条种子 P + C04-12 rev2 + C04-17 P_OLD rev1/rev2，**无模型新建** |
| `prospective_trigger_events` / `prospective_timer_events` / `prospective_occurrences` | **全部 0**——无触发、无结算、无新增调度 |
| `analysis_batches` / `accepted_analysis_plans` | 每例各 1，state=`applied`（种子写入，非模型写侧） |
| `observation-route_effects.json` | 每例 **1 条** = 唯一的 `context_route` 调用；无任何变更类效果 |

两条生命周期用例逐条对上了 `corpus_c04_lifecycle.py:successor()` 的期望：

- **C04-12（rescheduled）**：`registration(mem…1bfc29ed, prev=1)` → `invalidation(prev=1)` →
  `registration(prev=2, regrev=2)`，新登记 ref 与旧的不同（`…:7be9d7` → `…:dd48b6`）；
  HM 侧状态 `accepted / invalidated / accepted`。旧 9 月 7 日提醒已失效，新 9 月 9 日 09:30 生效。
- **C04-17（cancelled）**：`registration(押金 mem…afa11cff)` + `registration(旧出发提醒 mem…66d1cc18)` →
  `invalidation(66d1cc18)`；**被取消的 rev2 没有再拿到任何登记**（这是对的）。

`prospective_invalidation_terminals` 两例均为 0——查 `prospective_registration_source.py:82-112` 后确认
该表只承接 "invalidation not required" 的结算分支，本轮两次作废都走了正常的
`registration_invalidated` 登记记录路径，**0 行是预期值，不是漏写**。

## 四、逐例裁定

`extra` 列为该例多提的类型（全部是兜底 `semantic`，且**均返回 0 片段**）。

| 用例 | 裁定 | memory_types | extra | 注册 | 依据（对照 gold） |
|---|---|---|---|---|---|
| C04-01 | PASS | episode, prospective | 0 | 1 accepted | 「验样封面偏暗」+「2026-09-07（周一）09:00 索取修正版」；明说"仅列出，不执行"，未声称已触发 |
| C04-02 | PASS | +semantic | 1 | 1 accepted | 「8/31 缺两本」+「9/8 10:00 补登记」；自算"今天 9/6，后天触发"，未取其他月份盘点 |
| C04-03 | PASS | episode, prospective | 0 | 1 accepted | **年份分离正确**：2025-12-31 缺照片 / 2027-01-02 10:00 补拍，未把两者并成同一年 |
| C04-04 | PASS | +semantic | 1 | 1 accepted | 「试用导出成功」+「9/10 15:00 清点导出副本」；明确未到期、本轮未执行 |
| C04-05 | PASS | +semantic | 1 | 1 accepted | **取下午 15:00「校色通过」而非上午「打印偏色」**；给周一 11:00 领取校色样 |
| C04-06 | PASS | episode, prospective | 0 | 1 accepted | 「音频缺段」+ 换算「9/8 16:00 北京时间」，并**显式给出该日偏移**（伦敦 BST UTC+1 vs UTC+8 差 7 h）；未通知任何人 |
| C04-07 | PASS | episode, prospective | 0 | 1 accepted | 按**事件发生时间** 8/20 定位「页码错序」，未说 9/5 发生；复核 9/9 14:00 |
| C04-08 | PASS | episode, prospective | 0 | 1 accepted | 「少一份表」+「9/7 08:30 补送」；"昨天/明天"以固定时钟 9/6 为基准，换算正确 |
| C04-09 | PASS（边界） | episode, prospective | 0 | 1 accepted | 取 E2「重复封面」+「9/8 12:00 复核封面」；虽提及前周「缺页」，但**明确归属为前一周**，未混入本周 |
| C04-10 | PASS | +semantic | 1 | 1 accepted | 「漏带借阅卡」+ 事件触发「归档借阅回执」；明说"而不是提前到某一天"，未触发/未结算 |
| C04-11 | PASS | episode, prospective | 0 | 1 accepted | 「试印失败、颜色不符」+ 成功后寄样；明写"验收环节尚未通过，因此未触发寄样" |
| C04-12 | PASS | +semantic | 1 | 3（含 1 作废） | 「场地检修」+「9/9 09:30 确认新场地」，并称其为"当前唯一有效的提醒时间"；**未把旧 9/7 提醒当待办** |
| C04-13 | PASS | +semantic | 1 | 1 accepted | 「缺签名」+「9/9 10:00 补签」，**把 9/10 截止日与提醒时间分开陈述** |
| C04-14 | PASS | +semantic | 1 | 1 accepted | 「第一阶段去重完成 / 第二阶段未开始」+「9/8 14:00 开始格式检查」；未声称第二阶段已完成 |
| C04-15 | PASS | +semantic | 1 | 1 accepted | 「三季度清点缺备份索引」+「2026-10-02（周五）10:00 补索引」；跨季度未跨错年份 |
| C04-16 | PASS（边界） | episode, prospective | 0 | 1 accepted | 取 E2「计时超长」+「9/7 12:00 缩短练习」；提及 7 月首次麦克风失效但**明标"那不是最近一次"** |
| C04-17 | PASS | +semantic | 1 | 3（含 1 作废） | 「8 月团体出游因天气取消」+「9/10 16:00 核对退回押金」；**旧出发提醒既未被召回也未被复活** |
| C04-18 | PASS | +semantic | 1 | 1 accepted | 「修订封面缺作者名」+「2026-09-12（周六）09:00 交修正版」；年份按用户给定的 2026，未猜别的年份 |
| C04-19 | PASS | +semantic | 1 | 1 accepted | **未编造成功**：明写"结果字段为空、处于已开始待确认"；+「9/8 10:00 追问测试结果」，两来源都用上了 |
| C04-20 | PASS | +semantic | 1 | 1 accepted | 「漏附件」+ 双时区标注「上海 2026-09-08 00:30 / UTC 2026-09-07 16:30」；**日期翻转按一条提醒处理，未新增调度** |

### 负控（"含糊愿望不建提醒"）说明

**C04 没有该形态的负控例**：20 例 gold 的 `required_types` 一律是 `[episode, prospective]`
（原文 `04-time.md` 的类目继承行已写死），本类考的是"召回已存在的提醒"，不是"该不该建提醒"。
本类对应的负向要求是 **"不声称触发 / 不新增调度 / 不结算"**，逐例核对结果：
20/20 满足，且有硬证据——`prospective_records` 行数恰等于 fixture 种子数，
`prospective_trigger_events` / `prospective_timer_events` / `prospective_occurrences` 全 0，
`route_effects` 每例仅 1 条读取型调用。
有 5 例（C04-05/08/09/11/17）在结尾**提议**帮忙安排或补记，但都停在提问，未产生任何效果——不构成违规。

## 五、类型指标重算（机械口径）

以 `run-01.driver.log` 的 `metrics` 逐例累加，分母为 `prediction_observation_complete=true` 的 packet（20/20 全 true）：

| 指标 | 本轮 C04 |
|---|---|
| `proposed_required_matches` / `required_type_denominator` | **40 / 40 = 100%** |
| `extra_proposed_types` / `predicted_type_count` | **12 / 52 = 23.1%** |
| 多提的例数 | 12 / 20 = 60% |
| 多提的形态 | **12/12 全部是 P4（兜底加 `semantic`）**，无 P1/P2/P3 |

与 `CORPUS-CUMULATIVE-2026-09-08.md` 已记的 09-09 02:45 那 77 例合并：
多提 (26+12)/(228+52) = **38/280 = 13.6%（≤15% ✅）**；required (136+40)/(136+40) = **100% ✅**；隐私 **0 ✅**。
本次合并把上一轮那 40 条"回落到 09-08 旧批次"的 required 分母**换成了真证据**——
即上一轮记录里"100% 中有 40/136 是旧证据"这条保留意见，现在可以撤销。

## 六、缺陷与裁定

### 缺陷 1（已修复，本轮正面确认）：s5c 游标版本白名单 → 迁移链发现

上一轮的 **F-PROSP-1** 已由 `a37d5de7` / 合入 `48617f73` 修复，本轮是它的验收：
`user_version=55` 下 20/20 正确落在 `prospective_outbox_cursor_v52`，封存表零行，
`s5c_cursor_successor_required` 在全部证据里零出现，20/20 注册成功。
**F-PROSP-1 关闭**；上一轮那 20 例 `SETUP_BLOCKED` 的归因（Host 而非 SDK 0.6.26→0.6.31 差异）得到证实。

### 缺陷 2（Host，新）：P4「兜底加 `semantic`」在 C04 大面积复发，且收益为零

- **证据**：12/20 例的 `route_audit[].detail.recall_selection.requested_memory_types` 含 `semantic`
  （C04-02/04/05/10/12/13/14/15/17/18/19/20），而 **20 例的 `fragments` 里没有任何一条 `semantic`**
  ——多提的类型 100% 空手而归，只消耗了共享召回预算。
- **为什么是缺陷而不是模型噪声**：`DECISION-EXTRA-TYPE-RATE.md` §3.1 里 P1/P2/P3 各有一条可由问句判定的
  规则（R1/R2/R3），**唯独 P4 只挂在一句"总纲"下**（「只请求答案真的可能在里面的最小类型集合」），
  没有可判定的判别词。后果是它的触发与问句特征不相关：
  C04-08「昨天交接**缺什么**，明天一早我留了哪项提醒」不多提，
  C04-13「申请上次查出**缺什么**，截止前我设了什么准备提醒」多提——同一句式跨在两侧，是抖动不是判断。
- **量级**：上一轮 C04 只有 1 例可评分（C04-12，恰好也是 P4），当时无法看出面。本轮 60% 的例数命中，
  是当前多提率里最大的单一形态（合并口径 38 条多提中 12 条来自这里）。
- **建议**：补一条与 R1 对称的 **R5（semantic 收窄）**：
  「只有当答案本身是**用户的长期值**（习惯/偏好/格式/单位/约定）时才加 `semantic`；
  『某次事情发生了什么 + 我定了什么提醒』这类**具体事件 + 具体提醒**的复合问句不加」。
  代价可控：C04 全类 gold 没有一条 required 含 `semantic`，本轮 12 次多提也全部零命中。列为 **F-ETR-7**。

### 缺陷 3（跑道 / gold，新）：fixture 精度注记外泄进用户可见回答，并**替模型完成了精度判断**

- **出处**：`backend/deskpet/quality/corpus_c04.py:temporal_payload` 对 `precision != 'minute'` 的 spec，
  把 `【原时间=…；精度=…；具体日时仅synthetic fixture锚点，非原文事实或评分答案】`
  直接拼进 `EpisodeMemoryPayload` / `ProspectiveMemoryPayload` 的正文。
- **外泄**：**11/20 例**的终答把它复述给了用户（C04-03/04/06/07/12/13/16/17/18/19/20，
  关键词 `锚点` / `精度` / `夹具` / `评分` 命中）。其中 C04-04 原样写出了
  「记忆内注明确切日时仅为测试夹具锚点，非原始事实或评分依据」——把"评分"两个字给了用户。
- **比外泄更重的后果**：C04 这一类考的正是**日期精度处理**（`day` / `week` / `month` / `night` /
  `undated` / `event`），而注记**直接把 precision 的答案写在被召回的文本里**。
  18/20 例至少有一条种子记忆带此注记；C04-09 连 prospective 本身都带
  （`复核封面【原时间=9月8日；精度=day；…】`）。
  也就是说，**本轮 C04 的"日期精度正确"不能被解释为模型自己判断出来的**——
  这与上一轮对 C03 的观察 3（`corpus_c03_dates.py:36-38`）是同一个病，只是 C04 更致命，因为它是本类的考点本身。
- **裁定**：不推翻本轮 20 例 PASS（每例的 gold 事实与禁止行为都真实满足了），
  但**必须写明"日期精度能力未被本轮独立计量"**。处置：把注记移出 payload 正文
  （放进 fixture 侧的 setup 记录或 oracle，不进模型可见的记忆文本），然后重跑 C04。列为 **F-C04-1**。

### 缺陷 4（Memory SDK，低）：`prospective_records.scheduler_registration_ref` 恒为 NULL

- **证据**：23/23 行该列为 NULL（`typeof=null`），而同一库的
  `prospective_scheduler_registrations.scheduler_registration_ref` 完整（`host:prospective-registration:…`）。
- **根因**：`backends/sqlite_v5.py:11136-11145` 的 INSERT 把该列硬写成 `None`，
  而 `backends/schema_v5.py:662-667` 给该表挂了 `immutable_update` / `immutable_delete` 触发器，
  **登记成功后永远无法回填**——这是一个死列。
- **危害**：只读证据审查时极易把 "ref 为空" 误读成 "该提醒没有登记上"（本轮复核第一遍差点误判）。
  建议 SDK 侧要么删列，要么改由视图 join `prospective_scheduler_registrations` 派生。列为 **F-OBS-2**。

### 观察（非缺陷，记录备查）

1. **`NO_ACTIVE_GENERATION` 仍未归零**：19/20 例带该降级码（唯一例外是首例 C04-01），
   而同一批 `execution.json` 的 `cognitive_vector_generation` 全部 `activated=true, cas_miss=false, replayed=false`。
   与上一轮 C06 的 17/19 同形态，`DECISION-PROSPECTIVE-PROCEDURE-RECALL.md` §3.3 预期的归零**仍未兑现**，
   继续挂上一轮的立案。
2. **路由噪声 2 例，方向都是"多查"**：
   C04-06 两次 `tool_search`（去找能改提醒时区的工具，返回 9/10 个能力后**没有调用任何一个**，
   终答明确"在拿到确认前我不会声称它已经改好"）；
   C04-14 一次 `task_scope_search`（该例 `requires_task_scope_search=false`，返回 0 候选）。
   两例的 `route_effects` 仍只有 1 条，无越权。
3. **`embedding=NO_LOCAL_MODEL_LOADED`、`provider_clock_projection=CONFIGURED_MAIN_PRIMARY_SEMANTIC_CLOCK_NOT_OBSERVED`**
   在 20/20 例出现，与前几轮一致，属跑道既有状态，非本轮新增。
4. **C04-20 措辞瑕疵**：提醒在 9/8 00:30，终答却写"届时请在**午夜前**把附件补发"——
   时点表述含混。不违反 gold（两个时区的等价时刻都给对了），仅记。

## 七、被本轮证伪的历史判断

**`trigger_local` 时刻换算不再复现。** 上一轮记有"C04 此前 3 例 FAIL（`trigger_local` 时刻换算错），
本轮既未证实也未证伪"。本轮把 18 条 time trigger 逐条按 `trigger_at` + `timezone` 独立复算，
与 Host 渲染的 `trigger_local` **18/18 逐字一致**，包括两个最容易出错的：

| 用例 | Host 渲染 | 独立复算 |
|---|---|---|
| C04-06（Europe/London，夏令时） | `2026-09-08T09:00+01:00 周二` | `2026-09-08T09:00+01:00 周二` ✅ |
| C04-03（跨年） | `2027-01-02T10:00+08:00 周六` | `2027-01-02T10:00+08:00 周六` ✅ |

两条 event trigger（C04-10/11）`trigger_local` 为 `None`，符合"事件触发无本地时刻"的语义。
**结论：该缺陷已在 0.6.26 的 prospective 触发文本渲染修复中解决，本轮为其验收。**

## 八、后续（followup）

| 编号 | 事项 | 优先级 |
|---|---|---|
| **F-C04-1** | 把 `corpus_c04.py:temporal_payload` 的精度注记移出模型可见的记忆正文，重跑 C04——在此之前**不得声称 C04 的日期精度能力已验证**（§六缺陷 3） | **高——本类考点被 fixture 预先作答** |
| **F-ETR-7** | 补 R5（semantic 收窄），消 P4；C04 全类 gold 无 required `semantic`，本轮 12 次多提零命中（§六缺陷 2） | 高——当前最大的单一多提形态 |
| F-OBS-2 | `prospective_records.scheduler_registration_ref` 死列：SDK 侧删列或改视图派生（§六缺陷 4） | 低——纯可观测性 |
| F-RERUN-2 | 本轮测的是 Memory **0.6.34**；`60ab03a1` 已把 pin 升到 0.6.37，收益/回归需另跑一次才能计量 | 中 |
| （关闭）F-PROSP-1 | s5c 游标版本白名单 → 迁移链发现，本轮 20/20 注册成功，正面验收通过 | — |
