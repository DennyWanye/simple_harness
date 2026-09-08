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
