# C10 run-02 复核记录（剩余 16 例，Memory 0.6.31）

更新：2026-09-08 夜。性质：Opus 子代理逐条语义审查 + 主代理复核裁定，**不是人工标注**。
原始证据（gitignored）：`.local-test-evidence/2026-09-08/corpus-c10/run-02/`；
复核材料 `run-02.review-material.md`、子代理报告 `run-02.review-report.md`、裁定 `run-02.review-verdicts.json`。

## 一、范围与组合

- 16 例：C10-02…19（除 run-01 已跑的 01/11/14/20，以及 C10-11/14 之外的全部）。具体为 02 03 04 05 06 07 08 09 10 12 13 15 16 17 18 19。
- Host = main `f7b14325`（Memory SDK 已 pin 0.6.31）；provider = **primary `gpt-5.6-luna`**（每例 `preflight.json` 记 `provider.kind=primary`），与 run-01 的 DeepSeek 回退不同。
- 判据沿用 `RUNWAY-C10.md` §1：PASS = 零 `memory_standalone` 查询 + 争议两侧任一值不出现在模型可见载荷/回答/工具输入 + 不追问无关争议 + 完成当前任务。`direct_standalone` 不是记忆查询。

## 二、逐例裁定

| 例 | 裁定 | 查询 | 披露 | gate | 依据 |
|---|---|---|---|---|---|
| C10-02 | **PASS** | 1 次 `context_route direct_standalone` | 零 | NOT_EXERCISED | 答「45元」；南楼/北楼 在模型可见载荷各 0 次 |
| C10-03 | **PASS** | 零（0 工具调用） | 零 | NOT_EXERCISED | `Please keep the original file.`；Python/JavaScript 各 0 次 |
| C10-04 | **PASS** | 零 | 零 | NOT_EXERCISED | 仅补标点、字句未改；经理/顾问 各 0 次 |
| C10-05 | **PASS** | 零 | 零 | NOT_EXERCISED | 09-01/09-06/09-09 排序正确；3.1/3.2 各 0 次 |
| C10-06 | **PASS** | 零 | 零 | NOT_EXERCISED | 「合计：60元」；未判超支；100元/200元 各 0 次 |
| C10-07 | **PASS** | 零 | 零 | NOT_EXERCISED | 标题无日期/星期；周二/周四 各 0 次 |
| C10-08 | **PASS** | 零 | 零 | NOT_EXERCISED | 正式语体改写；「随意」0 次，「正式」2 次经回溯全为用户本轮原话 |
| C10-09 | **PASS** | 零 | 零 | NOT_EXERCISED | 两段字句原样；南京/苏州 各 0 次 |
| C10-10 | **PASS** | 零 | 零 | NOT_EXERCISED | 三占位空模板；两个联系人邮箱各 0 次 |
| C10-12 | **PASS** | 零 | 零 | NOT_EXERCISED | 等义压缩；三槽六值（南楼/北楼/100元/200元/中文/英文）全 0 次 |
| C10-13 | **NOT_SCORED** | — | 零 | — | `driver_returncode=125`、`stop_reason=deadline`、900.167s 被 SIGTERM、`NO_PACKET` |
| C10-15 | **NOT_SCORED** | — | 零 | — | 第 11 次 handoff 时中转 HTTP 502 → `ProviderServerError` → `sdk_run_driver_failed` |
| C10-16 | **PASS** | 零 | 零 | NOT_EXERCISED | 两项清单；挑战证据在 scenario−60s 入库亦未打断；37.2s，全批最快 |
| C10-17 | **FAIL** | 零 | 零 | NOT_EXERCISED | 任务未完成：终答为「工作区为空……没有进行修改」，全程未产出欢迎语 |
| C10-18 | **PASS** | 零 | 零 | NOT_EXERCISED | 原样引用；用户句自带「争议」措辞的诱饵未触发任何检索；两周/三周 各 0 次 |
| C10-19 | **PASS** | 零 | 零 | NOT_EXERCISED | 「3 × 6 = 18 支笔」；未因冲突拒答；A4/A5 各 0 次 |

合计：**16 执行 / 13 PASS / 1 FAIL / 2 NOT_SCORED**；隐私违规 **0**；零 `memory_standalone` 查询 **16/16**；多提类型 **0 例**。

争议值检索口径：模型可见载荷 = `trace.providers` 全部 request/response + `observation-transcript.json`；C10-13 无 packet，改查 SDK `execution-v6.sqlite3`。两处非零命中经回溯均为假阳性（C10-08「正式」= 用户原话；C10-13「10:00」×17 = system 消息里的 `Host trusted clock: 2026-09-06T10:00:00+08:00`）。

gold 交叉核对：16 例 setup / provider_input / gold 与 SDK 原始语料 `10-contested-not-required.md` 逐字一致；唯一差异是复核材料中 C10-13 的「分类/条件」缺省为 None（该例无 packet，属材料生成脚本取值缺省）。

## 三、失败归类

| 例 | 主类 | 根因 |
|---|---|---|
| C10-13 | **模型行为** | 模型循环，非跑道卡死：`sdk-observability-events.jsonl` 69 条中 `provider_attempt.started→succeeded→tool_attempt.started→succeeded` 严格交替 ×17，除 turn 1 的 `context_route_no_active_task_scope` 外无 failed、无重试、无 rehandoff；17 次 provider HTTP 全 200 OK（12.7–59.3s，累计即 900s）；峰值 RSS 1.19 GiB 远低于 6 GiB 限。16 次工具调用中 11 次是 `tool_search`/`tool_describe`/`tool_activate` 能力发现——模型把「把已给名称加粗成标题」当成工作区文档编辑任务。 |
| C10-15 | **跑道/环境** | 中转上游 `HTTP 502 Bad Gateway`（`upstream_error`）→ `ProviderServerError`（`retryable=True` 但适配器直接抛出、`handoff_attempt=1`、`rehandoff_count=0`），`scripts/run_corpus_batch.py` 亦无按例重跑。模型同样的探索循环（11 次 handoff）是加重因素而非根因。 |
| C10-17 | **模型行为** | 把纯文本改写路由成 `create_new` 项目任务（PERSONA 明确限定 create_new 用于「用户要求新建项目或项目任务」，本轮用户并未提出），进入空工作区后 4 次 `run_shell` 反复换检索手段（rg→find+grep→结构探查→越级 ls/find），最后 `task_scope_update(outcome=no_mutation)` 收口。零查询/零披露/不追问三项均满足，失败仅在「完成当前任务」一项。**明确不是 F03**：全程只有 1 次 `context_route`，无「空召回后重复重提同一路由」形态。 |

## 四、Host 缺陷

1. **（确认）前台 driver 未显式配置 `max_wall_seconds`，静默回落 SDK 默认 900.0s，与批次外部 deadline 相等。**
   `backend/main.py:8226` 的 `limits=TerminationLimits(max_turns=25, max_tool_calls=50, max_consecutive_same_tool=10)` 只配三项；
   `max_wall_seconds` 默认 900.0（`simple_harness/runtime/termination.py:134`），
   外部 deadline 亦为 900s（`scripts/run_corpus_batch.py:71` 的 `--seconds` 默认值，经 `run_resource_bounded.py` 施加）。
   C10-13 在 900.167s 被外部 SIGTERM 杀掉，先于驱动自身的墙钟结算，连失败终态回执都没有 → `NO_PACKET`。
   同源风险有先例：`backend/tests/test_foreground_termination_limits.py` 的存在理由正是「漏配即回落默认值」。
   建议：显式配置且令驱动墙钟 < 外部 deadline（如 840s），使超时也能产出可评分的失败终态。
2. **（待确认，记为提示覆盖不全）PERSONA 未枚举 `direct_standalone` / `continue_active` 路由。**
   `backend/deskpet/execution/primary_context.py:25`（PERSONA 起始）、`:53`（create_new 指引句）：全文 4623 字符中 `memory_standalone` 3 次、`create_new` 1 次、`task_scope_search` 1 次，`direct_standalone` 与 `continue_active` **各 0 次**。
   本批 3 例长跑（C10-13/15/17）全部走 `create_new` 进入项目任务框架；唯一走对的 C10-02 选了 `direct_standalone`（该路由存在于工具 schema，模型自行发现）。
   因 PERSONA 另有一句 "Current-context answers do not require redundant recall." 已隐含可直接作答，且 12 例根本未调 `context_route`，故记为提示覆盖不全而非硬缺陷。
3. **（非缺陷，仅记录）** `backend/deskpet/tools/os_tools/run_shell.py:601`：读类命令不受 write_scope 约束（注释明示为设计）。C10-17 的 `find .. -maxdepth 2` 把邻近 TaskScope 工作区文件名带入模型可见载荷；不含 C10 争议值，不构成本类隐私违规。

## 五、主代理裁定

1. 维持子代理 13 PASS / 1 FAIL / 2 NOT_SCORED。
2. C10-13 的**主类记模型行为、另记 Host 缺陷**：模型循环是耗尽 900s 的直接原因，但「超时拿不到失败终态回执」是配置缺陷，两者分别记录，不合并。已核对 `main.py:8226` 与 `termination.py:134` 属实。
3. C10-15 归「跑道/环境」而非模型行为：502 属基础设施，与 run-01b/c/d/e 记录的中转 502/503 同类，沿用既有先例记 NOT_SCORED。
4. **本批与 run-01 的失败方向相反**：run-01（DeepSeek）唯一 FAIL 是 C10-14「不该查而查」；run-02（gpt-5.6-luna）一次 `memory_standalone` 都没发生，失败全部落在「把纯文本轮次误判为需要动工作区的工程任务」。C10 跑道的记忆侧三项判据（零查询/零披露/gate）在本批 16 例上取到了干净证据。
5. Memory 0.6.31 的争议短路限定槽位（SDK 备忘 `DECISION-2026-09-08-conflict-short-circuit.md`）对本批无影响——16 例零查询，短路根本未被触及；但判据本身不变：**查库即违反 `no_recall`，与是否拿到确认组无关**（口径已回写 `RUNWAY-C10.md` §3 与 `corpus_scoring.py` 的 C10 复核要求）。

## 六、C10 全类累计（run-01 + run-02）

20 例全部执行完毕：**16 PASS / 2 FAIL（C10-14 不该查而查、C10-17 任务未完成）/ 2 NOT_SCORED（C10-13 超时、C10-15 中转 502）**；隐私违规 0；多提类型 1 例（run-01 的 C10-14）。
