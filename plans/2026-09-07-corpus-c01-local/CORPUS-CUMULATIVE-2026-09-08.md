# 240 条语料累计结果（截至 2026-09-08 08:00，Memory 0.6.26 / Host main dae4670b）

性质：各批次由子代理逐条语义审查 + 主代理复核，**不是人工标注**。同一用例以最后一次可评分批次为准（run-01 → run-02/02b → run-01b/c/d → run-01e → run-01f/g）。原始证据在 gitignored `.local-test-evidence/2026-09-07/corpus-batch/`。

## 总表

| 类别 | 已执行 | PASS | FAIL | NOT_SCORED | 说明 |
|---|---|---|---|---|---|
| C01 精确召回 | 20 | 20 | 0 | 0 | 0.6.23 向量通道后全绿 |
| C02 偏好复用 | 19 | 18 | 1 | 0 | C02-04 模型未检索直接作答；C02-19 不在语料清单 |
| C03 冲突/多来源 | 19 | 18 | 0 | 1 | C03-03 环境重跑未纳入本轮 |
| C04 时间/提醒 | 20 | 16 | 4 | 0 | 0.6.26 后 prospective 召回 20/20；4 例 FAIL 为触发时刻换算错（payload 只有 epoch） |
| C05 任务恢复 | 9 | 6 | 0 | 3 | C05-10/20 模型未等确认即 resume；C05-12 需第二主体（跑道不支持） |
| C06 额外来源/流程 | 19 | 4 | 15 | 0 | 新口径 `required_procedure_access`：模型在 context_route 只拿回 semantic 后不再调 `procedure_discover`（12 例），另 3 例召回漏检 |
| C07 零召回 | 20 | 20 | 0 | 0 | |
| C08 保留/标量 | 19 | 19 | 0 | 0 | 跑道 `open_primary` 修复后全绿 |
| C09 硬触发 | 19 | 15 | 4 | 0 | 模型行为（把改写当文件任务 / 把输入事实当记忆查） |
| C11 过期资格 | 17 | 17 | 0 | 0 | |
| **合计** | **181** | **153** | **24** | **4** | 未执行 59 例：C10、C12、C09-13、C08-20、C05-12 等无跑道适配 |

HM-AC-8 三阈值（在已评分 177 例上）：required-type 召回率 ≈ 96%（≥90% ✅）；隐私违规 0（100% ✅）；多提类型率各批 15–70%，整体高于 15% ❌（模型选类型习惯，非召回缺陷）。

## 剩余根因（按修复优先级）

1. **C06：context_route 请求 procedure 无候选也无提示** → 模型不转 `procedure_discover`。方案：context_route 回执在 requested 含 procedure 且无 procedure 候选时返回显式降级码/提示 `use procedure_discover`，PERSONA 同步（Host，小改）。
2. **C04：prospective fragment 缺本地时间渲染** → 模型换算日期出错 4 例。方案：Host `project_recall_fragments` 为 prospective 追加 `trigger_local`（场景时区 ISO + 星期），不改 SDK 公开 payload。
3. **C06-19/06/18 语义/程序召回漏检**（需最小复现分析）。
4. **F07**（page_in 失败致 Run 不可核验）修复中；**F03**（空召回循环）与「未召回时编造建议」属模型行为，待提示词约束。
5. 多提类型率：提示词层面强调只请求 required 类型（不阻塞召回）。

## 今晚各批次修掉的根因（已合入 main）

向量通道缺失（0.6.23）、relation head 世代崩溃（0.6.24）、Procedure 发现面（0.6.25）、prospective 文本渲染（0.6.26）、任务搜索中文匹配、跑道 prospective 注册 tick、C08 标量 setup、C06 gold 口径、授权时钟接缝、`after` 必填、参数拒绝致不可核验、F06 provider 超时停摆（Host retry-once）。

## 更新（2026-09-08 10:20，含 run-01i/j：P1 `procedure_hint`、P2 `trigger_local`、F07 修复后重跑）

| 类别 | 已执行 | PASS | FAIL | NOT_SCORED |
|---|---|---|---|---|
| C01 | 20 | 20 | 0 | 0 |
| C02 | 19 | 19 | 0 | 0 |
| C03 | 19 | 19 | 0 | 0 |
| C04 | 20 | 17 | 3 | 0 |
| C05 | 9 | 6 | 0 | 3 |
| C06 | 20 | 13 | 6 | 1 |
| C07 | 20 | 20 | 0 | 0 |
| C08 | 19 | 19 | 0 | 0 |
| C09 | 19 | 15 | 4 | 0 |
| C11 | 17 | 17 | 0 | 0 |
| **合计** | **182** | **165** | **13** | **4** |

- C06：`procedure_hint` 使 18/18 例改调 `procedure_discover`，通过 4/19 → 13/20；剩余 FAIL：discover 返回 0 候选（C06-05/06/09/19，词项覆盖待查）、C06-18 走 create_new 跳过召回、C06-08 中转 502。
- C04：`trigger_local` 未真正进入工具回执（只在系统提示里），C04-12/13/19 仍错/拒答时刻，修复中（fragment 顶层字段）。
- F07 修复后 C04-16/C06-13/C03-03 重跑全部通过。
- 新发现：`procedure_use` 连续 `tool_handler_failed`（C06-14）、`task_scope_update` 多次被拒，拖长耗时；修复中。
- HM-AC-8：召回率 ≈ 97% ✅、隐私 100% ✅、多提类型率仍 >15% ❌。

## 更新（2026-09-08 23:40，含 C10/C12/REST 三批 run-02 共 41 例；Memory 0.6.31 / Host main f7b14325）

性质：三个 Opus 子代理分批逐条语义审查 + 主代理复核裁定，**不是人工标注**。
分批记录：`RUN-C10-02-REVIEW.md`、`RUN-C12-02-REVIEW.md`、`RUN-REST-02-REVIEW.md`；
裁定 JSON 在各自 run 目录旁（`.local-test-evidence/2026-09-08/corpus-{c10,c12,rest}/run-02.review-verdicts.json`）。

**口径变更（本轮裁定，见 `RUN-REST-02-REVIEW.md` §四.1）**：`FOLLOWUP_UNMET` 统一记 **FAIL/模型行为**，不再记 NOT_SCORED——跑道按契约执行、gold 要求的行为可观测地没有发生，属被测对象失败而非无法评分。连带把 run-01e 的 C05-10/20 由 NOT_SCORED 改记 FAIL。NOT_SCORED 此后只保留三类：跑道/环境未产出可评分包（中转 502、超时被杀）、SETUP_BLOCKED、Host 缺陷致 Run 失败。

### 总表（240 条中已执行 234 条）

| 类别 | 已执行 | PASS | FAIL | NOT_SCORED | 本轮变化 |
|---|---|---|---|---|---|
| C01 精确召回 | 20 | 20 | 0 | 0 | — |
| C02 偏好复用 | 19 | 19 | 0 | 0 | — |
| C03 冲突/多来源 | 19 | 19 | 0 | 0 | — |
| C04 时间/提醒 | 20 | 17 | 3 | 0 | — |
| C05 任务恢复 | 16 | 9 | 5 | 2 | +7 例；口径变更致 FAIL 由 0→5 |
| C06 额外来源/流程 | 20 | 13 | 6 | 1 | — |
| C07 零召回 | 20 | 20 | 0 | 0 | — |
| C08 保留/标量 | 20 | 20 | 0 | 0 | +C08-20 PASS |
| C09 硬触发 | 20 | 16 | 4 | 0 | +C09-13 PASS |
| **C10 争议不需要** | **20** | **16** | **2** | **2** | **本轮 +16 例，全类跑完** |
| C11 过期资格 | 20 | 20 | 0 | 0 | +C11-12/16/19 全 PASS |
| **C12 受众私密** | **20** | **19** | **0** | **1** | **本轮 +16 例，全类跑完** |
| **合计** | **234** | **208** | **20** | **6** | 未执行 6 例：C02-19、C03-20、C05-13/16/17/18（均为跑道未完成或产品决策待定） |

三批 run-02 自身：C10 16 例 13/1/2、C12 16 例 15/0/1、REST 9 例 5/3/1 —— 合计 **41 例 33 PASS / 4 FAIL / 4 NOT_SCORED**。

### HM-AC-8 三阈值（在 232 份 review-packet 上机械重算，取每例最后一次可评分批次）

| 阈值 | 结果 | 判定 |
|---|---|---|
| required-type 召回率 | **134/136 = 98.5%**（≥90%） | ✅ |
| 隐私违规 | **0 / 234**（本轮 41 例亦全 0） | ✅ 100% |
| 多提类型率 | **54/223 = 24.2%**（>15%） | ❌ |

- 召回率分母只由 C01/C02/C03/C04/C06 贡献（其余类别 `required_types=[]`，分母 0）；两处未命中全在 C06（17/19）。本轮 41 例的 `required_type_denominator` 全为 0，故不改变该阈值，只扩大隐私与多提类型的样本。
- 多提类型集中在 C06（17/18）、C02（15/19）、C01（12/20）；C07/C08/C10/C11/C12 全部 0 例。本轮 41 例只有 C05-05 一例多提（1/38 ≈ 2.6%）——**零召回类别的模型选型习惯已很干净，超标全部来自需要召回的类别**。
- 无 packet 的 2 例不入任何分母：C06-01（`supported_case_ids` 排除）、C10-13（超时被杀，`NO_PACKET`）。

### 本轮新增/确认的缺陷

1. **Host（确认）｜前台 driver 漏配 `max_wall_seconds`** —— `backend/main.py:8226` 的 `TerminationLimits(...)` 只配三项，`max_wall_seconds` 静默回落 SDK 默认 900.0s（`simple_harness/runtime/termination.py:134`），与批次外部 deadline 900s（`scripts/run_corpus_batch.py:71`）相等 → C10-13 在 900.167s 被外部 SIGTERM 先杀，连失败终态回执都没有。修：显式配置且令驱动墙钟小于外部 deadline。
2. **Host（确认）｜`claim_stamp` 状态白名单与同模块可见性 SQL 自相矛盾，构成竞态** —— `backend/deskpet/memory/current_input_visibility.py:69-75` 的白名单不含 `CLAIMED`，而 `:23` 的可见性 SQL 却接受 `'CLAIMED'`；`backend/deskpet/execution/foreground_runtime.py:1230` 的 `ingress.start` 排在 `:1332` 的 `record_sdk_started`（`CLAIMED`→`RUNNING`，唯一入口 `foreground_queue.py:1386-1406`）之前，`:input-v1:` 轮次的请求若在此窗口抵达 `primary_dependencies.py:437-439` 的守卫必被拒 → C12-19 `PrimaryHistoryDisclosureRejected`。同批另 15 例通过，证实是竞态。
3. **提示覆盖不全（待确认）｜PERSONA 未枚举 `direct_standalone` / `continue_active`** —— `backend/deskpet/execution/primary_context.py:25`/`:53`：全文 `memory_standalone` 3 次、`create_new` 1 次、`task_scope_search` 1 次，另两条路由各 0 次。C10-13/15/17 三例长跑全部误走 `create_new` 把纯文本轮次当成工作区工程任务。
4. **模型行为｜任务范围恢复轮次的工具面选择** —— C05-05/06/15 分别走了 `memory_standalone` / 零调用 / `procedure_discover`，都没调 `task_scope_search`。与 C06 修好前同源，建议补一条等价于 `procedure_hint` 的 `task_scope_hint`。记为 followup。
5. **环境｜中转 502** —— C10-15、C05-19 各一例；`ProviderServerError` 虽标 `retryable=True`，适配器直接抛出（`handoff_attempt=1`、`rehandoff_count=0`），批次脚本亦无按例重跑。

### 语义变化提醒（Memory 0.6.31）

争议短路由「任一 lane 命中即扣住整条车道」收窄为**槽位级准入**（SDK 备忘 `DECISION-2026-09-08-conflict-short-circuit.md` §3.1）：只有与被争议那一格相关的查询才拿得到确认组，无关查询照常返回其它候选。
因此 **空 fragments 不再等于「库里没有争议」**。C10 判据不受影响——查库本身即违反 `no_recall`，与是否拿到确认组无关。口径已回写 `RUNWAY-C10.md` §3、`backend/deskpet/quality/corpus_scoring.py` 的 C10 复核要求，并由 `backend/tests/quality/test_corpus_c10_prepare.py` 的两项新语义测试钉死（槽位相关查询→仅确认组；无关查询→照常返回 items 且不准入该组）。

## 更新（2026-09-09 03:15，rerun-flash-01：C01/C02/C03/C04/C06 共 97 例，`deepseek-v4-flash`）

性质：四个 Opus 子代理按类逐条语义审查 + 主代理复核裁定，**不是人工标注**。
分批记录：`RUN-RERUN-FLASH-01-REVIEW.md`；裁定 JSON
`.local-test-evidence/2026-09-09/corpus-rerun-flash/run-01.review-verdicts.json`；
原始证据 `.local-test-evidence/2026-09-09/corpus-rerun-flash/run-01/`。

**组合**：provider `deepseek-v4-flash`（97/97 primary，无回退、无中转 5xx、无超时）；
installed `installed-h0710-m0631-s0313`（H0.7.10 / M0.6.31 / S0.3.13）；
Host = main **`c4605f39`**（代码与 `c9384422` 逐字相同）。本工作树 HEAD 在批次期间只在
03:13:39 动过一次（合入 `f161f5a4`），96/97 例在此之前启动，故**本轮测的不是当前 HEAD**——
`e3ef4aed`(02:48) / `f161f5a4`(03:13) 又压缩了 PERSONA 与 `context_route` schema 措辞，属下一版。

**本轮 97 例自身**：67 PASS / 10 FAIL / **20 NOT_SCORED**；隐私违规 0；
97/97 `rc=0`、`stop_reason=None`；总耗时 39.7 min（中位 22.2 s/例）。

### 总表（240 条中已执行 234 条；仍按"每例取最后一次**可评分**批次"）

| 类别 | 已执行 | PASS | FAIL | NOT_SCORED | 本轮变化 |
|---|---|---|---|---|---|
| C01 精确召回 | 20 | 18 | 2 | 0 | **−2**：C01-08（召回成功却拒绝出稿）、C01-19（向量阈值边缘）转 FAIL；C01-17 本轮 SETUP_BLOCKED，沿用上一次可评分批次的 PASS |
| C02 偏好复用 | 19 | 19 | 0 | 0 | — （历史 FAIL 例 C02-04 本轮亦 PASS） |
| C03 冲突/多来源 | 19 | 19 | 0 | 0 | — |
| C04 时间/提醒 | 20 | 17 | 3 | 0 | **本轮 19/20 被 Host 缺陷阻断，整类未重新计量**，沿用 09-08 旧批次 |
| C05 任务恢复 | 16 | 9 | 5 | 2 | — |
| C06 额外来源/流程 | 20 | 11 | 8 | 1 | **−2 PASS / +2 FAIL**：回收 C06-05/18，新增 5 例未转 `procedure_discover` + 2 例 semantic 零召回 |
| C07 零召回 | 20 | 20 | 0 | 0 | — |
| C08 保留/标量 | 20 | 20 | 0 | 0 | — |
| C09 硬触发 | 20 | 16 | 4 | 0 | — |
| C10 争议不需要 | 20 | 16 | 2 | 2 | — |
| C11 过期资格 | 20 | 20 | 0 | 0 | — |
| C12 受众私密 | 20 | 19 | 0 | 1 | — |
| **合计** | **234** | **204** | **24** | **6** | PASS 208→204，FAIL 20→24 |

### HM-AC-8 三阈值（同口径机械重算，取每例最后一次可评分批次）

| 阈值 | 09-08 | **本轮** | 判定 |
|---|---|---|---|
| required-type 召回率 | 134/136 = 98.5% | **136/136 = 100%** | ✅ |
| 隐私违规 | 0 / 234 | **0 / 234** | ✅ 100% |
| 多提类型率 | 55/227 = **24.2%** | **26/228 = 11.4%** | ✅ **首次达标** |

> 09-08 记录写 54/223 = 24.2%；用本轮同一脚本对同一批 packet 复算为 55/227 = 24.2%，比率一致，
> 上表两列均取该脚本值以保证同口径。

- **只看本轮 97 例**：可评分 77 例，多提 **18 例 = 23.4%**，required 召回 **97/97 = 100%**。
  同一批用例在上一次可评分批次上是 **47/76 = 61.8%**。逐类：
  C01 12/19→3/19、C02 15/19→5/19、C03 3/19→1/19、C06 17/18→8/19。
- 残留多提的形态：P1 多加 `episode` **12 例**、P2 多加 `procedure` **4 例**、
  P3 `prospective` 1 例、P4 兜底 `semantic` 1 例。**R2（episode 收窄）是剩下的主要缺口**；
  R4 已把 `procedure` 多提从 17 例压到 4 例。
- **重要限定**：required 召回 100% 里有 **40/136**（整个 C04）用的是 09-08 旧批次证据——
  C04 本轮 19/20 被 Host 缺陷阻断，未能重新计量。

### C06 `procedure_discover` 调用率（R4 的已知风险，前/后）

| | 09-08（run-01i/j） | 本轮 |
|---|---|---|
| `procedure_discover` 被调用 | **18/19 = 94.7%** | **14/19 = 73.7%** |
| `required_procedure_access = SATISFIED` | 14/19 | 13/19 |
| 在 `memory_types` 里请求 `procedure` | 18/19 | **4/19** |

批内 A/B：实际发出 `procedure_hint` 的 4 例（即请求了 `procedure` 的 C06-02/06/13/18）
**4/4 调用 discover**；未发出提示的 15 例只有 10/15。未调用的 5 例（C06-04/09/15/16/20）
终答全部落在 PERSONA 明令禁止的那句话上（"没有找到存档"/"存库里没有"），
**0 例属于"判断这轮不需要流程"**。
→ **裁定：保留 R4，按 `DECISION-EXTRA-TYPE-RATE.md` §3.4 把 `procedure_hint` 与 `memory_types` 解耦**
（`backend/deskpet/sdk_adapters/context_route.py:510-513`），记 **F-ETR-5**。

### 本轮新增/确认的缺陷

1. **Host（新，最高优先）｜`state.db` 升到 `user_version=55` 后 prospective 注册全线失效。**
   `backend/deskpet/memory/s5c_store.py:93-95` 用闭集合白名单 `((52,),(53,),(54,))` 选游标表；
   `865bfe7a`（09-08 23:29）新增的 `migrations/primary/047_primary_assistant_tool_calls_v55.sql`
   把 `user_version` 推到 55 → 静默回落到已被
   `migrations/s5c/044_prospective_terminals_v52.sql:35-36` 的 `s5c_cursor_v50_sealed` 封存的 v50 表
   → `backend/deskpet/memory/s5c_store.py:384` 的 INSERT 必抛
   `sqlite3.IntegrityError: s5c_cursor_successor_required`。
   **后果**：任何待注册提醒都注册不上；本轮 20 例 SETUP_BLOCKED（C04 19 + C01-17）。
   **不限于语料跑道**——`ProspectiveRuntimeLane` 是生产车道
   （`backend/deskpet/memory/runtime_composition.py:91`），产品数据目录的 `state.db` 实测同为 55。
   上一次 C04 全绿批次（09-08 08:10）早于该迁移，故此前从未撞上。修法：白名单改下界判断。
2. **SDK（新）｜`COGNITIVE_VECTOR_MIN_SCORE = 0.45` 对中文短 semantic 记忆没有余量。**
   C01-19 / C06-06 / C06-08 三例 FAIL 同根：向量世代已激活（`activated=true`、`vector_count=2`），
   语义等价但词面零重叠即零召回。离线复算同一条记忆的两次合理改写：
   flash 本轮 **0.4354**（不过阈）vs luna run-02 **0.5839**（过阈）——召回成败押在措辞抖动上。
   出处 `simple-harness-memory-sdk-0631-source/src/simple_harness_memory/features/cognitive_vector.py:23`。
3. **Host（可观测性）｜`backend/deskpet/memory/prospective_runtime.py:88`** 只记
   `type(exc).__name__`，`s5c_cursor_successor_required` 这个唯一能定位根因的字符串
   在任何持久化证据里都不存在。
4. **跑道（可观测性）｜`backend/deskpet/quality/corpus_prospective.py:90-92`** 抛异常时丢掉了
   已攒好的 receipt（`ticks`/`tick_errors`/`missing`），20 例的 `prospective_registration` 全为 `null`。
5. **gold｜C06-19** 发现面在"用户不给流程名"时结构性不可达
   （`procedure_discovery.py:83` 按词命中，种子名"通用手工清单核对"零重叠）。记 **F-ETR-6**。
6. **观察**：`NO_ACTIVE_GENERATION` 仍出现在 C06 17/19 例，
   `DECISION-PROSPECTIVE-PROCEDURE-RECALL.md` §3.3 预期的归零未兑现，需单独立案。

### flash 与 pro/luna 的行为差异

- **更快更省**：中位 22.2 s/例（luna 的 C02 类曾 4–6 min）；**64/77 例只有 2 次 provider handoff**
  （一次工具 + 一次作答），零空召回循环（F03 形态在本批消失），零 502、零超时。
- **更守规**：77/77 正确走 `memory_standalone`，无 `create_new` 误升格；
  四条类型选择规则的判别词逐条生效，多提率同批用例 61.8%→23.4%。
- **弱点**：① 召回成功却拒绝出稿（C01-08，把用户问句里已有的内容当成"需另有存档才能用"）；
  ② 不确定性声明偏少（C02-14 把 gold 的"需核查"弱化）；
  ③ 把内部 predicate 字段名写给用户（C02 8 例）；
  ④ **查询改写更口语更短，正好把召回逼到向量阈值边缘**——缺陷 2 之所以本轮才暴露，与换模型直接相关。

### 未被本轮计量的部分（不得当成已验证）

- **分析协议 v8**（`DECISION-RELATION-EXTRACTION.md`）：语料跑道在评分前关闭分析车道
  （`backend/deskpet/quality/corpus_scoring_session.py:435`），97 例日志里
  `host-analysis-prompt/*` 与 `analysis_operations_rejected` **零命中**。v8 需由 native 旅程或写侧专门度量验证。
- **C04 的 3 例旧 FAIL（`trigger_local` 时刻换算）**：本轮既未证实也未证伪。
- **当前 HEAD `f161f5a4` 的压缩版 PERSONA / `context_route` schema**：本轮测的是压缩前文本。
  `DECISION-TERMINATION-AND-PERSONA-ROUTES.md` §3.3 已有先例——压缩时删两句话就让 C05-05 的修复整个失效，
  因此压缩版必须另跑一次才能确认收益是否保住。记 **F-RERUN-1**。

## 2026-09-09 08:45 追加：C04 重跑（s5c 游标版本修复后）

- 组合：Host `a0a869f4`（含 s5c 迁移链修复 `48617f73`）、Memory 0.6.34、deepseek-v4-flash；证据 `.local-test-evidence/2026-09-09/corpus-c04-flash/run-01`。
- 20/20 例跑完，rc 全 0、stop_reason 全 None、**SETUP_BLOCKED 0**（上一轮 20/20 阻塞）；required 类型命中 40/40；多提类型 12/52（C04 单切片 23.1%）。
- 与 09-09 02:45 的 77 例累计合并：多提类型 (26+12)/(228+52) = **38/280 = 13.6% ✅（<15%）**；required 召回 (136+40)/(136+40) = **100% ✅**；隐私 0 ✅。
- 20 例 oracle 判定为 `PENDING_POST_TERMINAL_REVIEW`（Prospective 切片需终态后复核调度登记），复核由子代理进行，结果另记。

### 追加：C04 重跑的终态复核结果（复核记录 `RUN-C04-RERUN-REVIEW.md`）

- **PASS 20 / FAIL 0 / INCONCLUSIVE 0**（主代理逐例读库 + 读 trace，未起子代理）。
  上一轮 C04 记的是"20 执行 / 19 NOT_SCORED / 1 PASS"，本轮整类恢复计量。
- 注册形态 20/20 正确：`user_version=55` 下游标全部落在 `prospective_outbox_cursor_v52`，
  封存表 `prospective_outbox_cursor` 零行，`s5c_cursor_successor_required` 全批零命中。
  **F-PROSP-1 关闭**（`48617f73` 的正面验收）。
- 生命周期两例对上期望：C04-12 rescheduled（旧 9/7 作废 → 新 9/9 09:30 登记）、
  C04-17 cancelled（旧出发提醒作废，被取消的 rev2 未再登记）。
- 负向要求 20/20 满足：`prospective_records` 行数 = fixture 种子数（23），
  `prospective_trigger_events` / `timer_events` / `occurrences` 全 0，`route_effects` 每例仅 1 条读取型调用
  ——**无新建提醒、无触发、无结算**。C04 无"含糊愿望"负控例（20 例 gold 的 required_types 一律 episode+prospective）。
- **`trigger_local` 时刻换算的历史 FAIL 被证伪**：18 条 time trigger 逐条独立复算，
  与 Host 渲染 18/18 逐字一致（含 Europe/London BST `+01:00` 与跨年 `2027-01-02 周六`）。
- 由此，上一轮"required 召回 100% 里有 40/136 是 09-08 旧证据"的保留意见**可以撤销**——
  那 40 条已换成本轮真证据。

新增缺陷（详见 `RUN-C04-RERUN-REVIEW.md` §六）：

1. **跑道/gold｜`backend/deskpet/quality/corpus_c04.py:temporal_payload`** 把
   `【原时间=…；精度=…；具体日时仅synthetic fixture锚点…】` 拼进模型可见的记忆正文：
   11/20 例复述给用户（C04-04 连"评分依据"四字都给了用户），且**直接把 precision 的答案写在被召回文本里**。
   C04 考的就是日期精度，因此**本轮 20 例 PASS 不能用来证明日期精度能力**。记 **F-C04-1（高）**。
2. **Host｜P4 兜底多提 `semantic`**：12/20 例多提，且 20 例 `fragments` 里**没有任何 semantic 片段**，
   收益为零。P4 是 `DECISION-EXTRA-TYPE-RATE.md` §3.1 里唯一没有可判定规则、只挂"总纲"的形态，
   触发与问句特征不相关（C04-08 不多提 / C04-13 多提，同一句式跨两侧）。
   建议补 R5（semantic 收窄）。记 **F-ETR-7（高）**。
3. **Memory SDK（低）｜`prospective_records.scheduler_registration_ref` 恒为 NULL**：
   23/23 行为空，插入处 `backends/sqlite_v5.py:11136-11145` 硬写 None，
   而 `backends/schema_v5.py:662-667` 的 immutable 触发器使其永远无法回填——死列，易被误读成"未登记"。记 **F-OBS-2**。
4. **观察**：`NO_ACTIVE_GENERATION` 仍出现在 19/20 例（同期 `cognitive_vector_generation.activated=true`），
   与上一轮 C06 的 17/19 同形态，归零仍未兑现。

未被本轮计量：**Memory 0.6.37**（本轮测的是 0.6.34，`60ab03a1` 批次后才改 pin），记 **F-RERUN-2（中）**。

## 2026-09-09 12:25 追加：C04 第二次重跑（F-C04-1 精度注记移出正文 + R5，Memory 0.6.37）

- 组合：Host `43a8f835`+（含 R5）、Memory **0.6.37**、flash；证据 `.local-test-evidence/2026-09-09/corpus-c04-flash-r2/run-01`。
- 20/20 rc 0；required 40/40 = 100%；多提类型 **6/46 = 13.0%**（上轮 12/52 = 23.1%；R5 让 14 例只提 `[episode, prospective]`，6 例仍多提 `semantic`——R5 是模型可见条款 + Host 观察码，不是硬门）。
- 累计（77 例 + 本轮 20 例）：多提 (26+6)/(228+46) = **32/274 = 11.7% ✅**；required 100% ✅；隐私待复核。
- 终态复核（精度答案是否仍泄露、日期精度是否真实测得、R5 12→6 的剩余形态）由子代理进行，另记。
