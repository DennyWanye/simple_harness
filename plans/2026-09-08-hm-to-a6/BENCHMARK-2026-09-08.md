# HM-AC-8 质量 / 延迟 / Token 基准（2026-09-08）

> 义务：HM-AC-8「跨仓初始化、故障与质量门」中的可量化部分：本地记忆检索 p95 ≤ 500 ms 且 hard deadline ≤ 2 s；
> required-memory-type recall ≥ 90%、no-recall 判断正确率 ≥ 90%、额外类型率 ≤ 15%、隐私禁止项/硬触发 100%；
> 最终 Context 严格服从 token 预算；provider timeout 240 s（S5）。
> 工具：`scripts/benchmark/hm_benchmark.py`（只读证据 → JSON + 中文 Markdown；`--selftest` 合成夹具；单测
> `backend/tests/quality/test_hm_benchmark.py`）。
> 生成文件：`benchmark-2026-09-08-native.{json,md}`（4 个原生真机目录）、`benchmark-2026-09-08-corpus.{json,md}`
> （10 个语料批次目录 + 6 份复审判定）、`benchmark-2026-09-08-all.{json,md}`（14 目录汇总）。本文只摘要与解读，
> 逐目录明细以生成文件为准。

---

## 0. 一句话结论

- **原生真机（DeepSeek，4 次运行 55 turn / 374 次 provider 调用）**：provider 超时门 PASS（max 136 s < 240 s）、
  分析通道预算门全部 PASS、context 三元组一致 PASS；**typed recall 门 FAIL**（foreground_recall p95 4 795 ms、
  max 5 304 ms、SDK deadline_exceeded 11 次）——但 11 次超时**全部来自尝试 3**（Host 侧修复前），尝试 4 已无 SDK 超时，
  只是 Host 墙钟 p95 仍 1 384 ms > 500 ms；**provider 实报 input_tokens 峰值 55 563 > effective_input_budget 26 752 FAIL**，
  而 Host 自估峰值 26 221 ≤ 26 752 PASS——说明预算是按 Host 估算执行的，而估算系统性偏低（messages 口径中位数只有
  provider 实报的 0.58）。
- **语料批次（luna，294 个用例运行时根 / 311 turn）**：typed recall 门 PASS（p95 441 ms、max 606 ms、0 超时）；复审质量
  required recall 94.5%（146 例）PASS、no-match 100%（19 例）PASS、隐私 0 违规 PASS、硬触发 55/55 PASS、
  **额外类型率 28.6% FAIL（阈值 15%）**。provider / 分析通道延迟在语料批次里**不可测**（SDK 冻结时钟），标 N-A。
- 跨全部证据的 Run 失败率 10.7%（39/366），原生真机 23.6%（13/55），原因码集中在
  `react_max_turns_exceeded`（6）、`react_repeated_tool_exceeded`（3）、`driver_failed`（30，语料侧几乎全是中转
  5xx/限流）。

---

## 1. 证据来源与已知缺陷状态

| 名称 | 目录 | 模型 / 版本 | 已知缺陷（影响读数的） |
|---|---|---|---|
| A6尝试3 | `.local-test-evidence/2026-09-08/native-a6-b3682fe1`（`primary-ui-xmqudtzt` + 修复后同 userdata 续跑 `primary-ui-hv9k7ncq`，`merged-attempt3/native.log` 为二者拼接，已去重） | deepseek-v4-pro，窗口 override 32000；Host `b3682fe1` → 中途升 `933df61e` | T7 `primary_history_transcript_mismatch` ×8 驱动停摆并重启（turn 墙钟含停摆间隙）；事件 A–D 未修（route authority、task_scope_search 循环、`nothing_to_close`、DeepSeek 畸形参数）；typed recall Host 侧修复（`DECISION-RECALL-TIMEOUT-HOST-SIDE.md`）未合入 → 11 次 `deadline_exceeded` |
| A6尝试4 | `.local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1` | deepseek-v4-pro，窗口 override 32000 | 事件 A–D 修复后；recall deadline 已抬到 2000 ms（`request_deadline_ms_seen = 1000, 2000`）；仍有 4 个 Run FAILED（3 × max turns，1 × driver_failed） |
| r14 | `.local-test-evidence/2026-09-08/native-7cec5249`（`primary-ui-ah1zxqx1`；`qcewdx7j` 无 userdata） | deepseek-v4-pro，**无窗口 override（1 000 000 → 32768 档）** | 分析通道 5/9 批次 `analysis_executor_failed`（DeepSeek 响应解析失败，后续修复）；Procedure 观测缺口 |
| r15 | `.local-test-evidence/2026-09-08/native-r15-188394b1/primary-ui-eth0v9u9` | deepseek-v4-pro，窗口 override 32000 | 1 批次 `analysis_delivery_authority_rejected`（recall authority 过期，见 `DECISION-RECALL-AUTHORITY-STALE.md`），1 批次停在 `handed_off` |
| 语料 run-01 … run-01j | `.local-test-evidence/2026-09-07/corpus-batch/run-01*`（每个用例一个 `scoring/<case>/runtime/userdata/data` 运行时根） | gpt-5.6-luna（svtun 中转）；SDK 0.6.20（run-01 的 C01 全部与 C07-01..03）→ 0.6.22；Host `d94e93dc..f74fede4` | **SDK 侧冻结时钟**（`provider_invocations.claimed_at == settled_at == 1788660000.0`，`llm_invocations.latency_ms = 0`）；确定性 `host_run_id` 跨用例重复；run-01 的 C01 用例在 0.6.20 上 recall p95 仅 10 ms（无向量道、词法直取），不代表当前构建 |

复审判定文件（按参数顺序后者覆盖前者，共 182 个唯一 case_id，171 个已计分）：
`run-01.review-verdicts.json`、`run-01.review-verdicts-part2.json`、`run-01bcd.review-verdicts.json`、
`run-01e.review-verdicts.json`、`run-01g.review-verdicts.json`、`run-01ij.review-verdicts.json`。
（`run-02*` 是另一批，不在本次范围。）

---

## 2. 口径（脚本实现，均已在自检夹具与单测中固定）

| 指标 | 数据源 | 口径 |
|---|---|---|
| 每 turn 墙钟（库内） | `state.db.foreground_turns.enqueued_at` → 该 turn **最后一个** Run 的 `foreground_terminal_receipts.recorded_at` | 含重启/停摆间隙；只统计有终态的 turn |
| 每 turn 墙钟（UI 驱动） | `*-progress.jsonl.elapsed_s`（System Events 发送 → 驱动观察到终态） | 仅原生真机有 |
| provider 调用延迟 | `execution-v6.provider_invocations`：`settled_at − handed_off_at`（无则 `claimed_at`），只取 `state=succeeded` | 0 ms 视为「未测量」（冻结时钟）并单列计数；`native.log product_provider_attempt_succeeded elapsed_ms` 作为交叉核对 |
| 每 turn provider 调用数 / token | `state.db.sdk_provider_attempt_audit` 按 **(运行时根, root_run_id)** 分组求和；峰值取单次 `input_tokens` | 语料的 host_run_id 跨用例重复，故必须带运行时根 |
| Host 估算 / provider 实报 | 对每条 `provider_invocations.request_json` 用 `deskpet.sdk_adapters.context_partitions.text_tokens(canonical_json(messages))`（与 Host 预算检查同一估算器）÷ `usage_json.usage.input_tokens` | 另给「含 tools schema」口径 |
| typed recall 延迟 | `operation-audit.db.memory_call_attempts`：`settled_at − started_at`，按 `caller` 分组；契约门看 `foreground_recall` | Host 侧墙钟（含跨进程调用），比 SDK 内部计时更严 |
| typed recall 超时率 | `human_memory_v7.db.typed_recall_terminals.terminal_kind = deadline_exceeded` / 全部终态 | SDK 侧事实 |
| 分析通道 | `llm_invocations`（`output_storage_status=public` 为接受）+ `analysis_batches.state` + `job_attempts.reason_code` | 预算阈值直接取 `analysis_batches.request_json.budget` |
| 上下文组装 | `run_context_snapshot_receipts.source_revisions_json`（budget_tier / causal_groups / trimmed_groups / current_tool_pages / current_tool_tokens）+ `execution_effects.tool_name='context_page_in'` + 日志三元组 | — |
| Run 失败原因 | `foreground_terminal_receipts.terminal_state != COMPLETED`，原因取 `run_events(kind='run.failed').payload.code`，无则 `raw_failures[0].error_code` | 驱动层 `foreground.runtime.failed` 单列（不是 Run 失败） |
| 预算 | 窗口取 `native.log model_context_resolved` 最后一次；档位 / `effective_input_budget` 用 Host `context_partitions` 函数 | 语料无 native.log → N-A |
| 分位数 | nearest-rank（第 ⌈p/100·n⌉ 个有序样本） | 汇总列为原始样本合并后重算，不是平均的平均 |
| 日志去重 | `native.log` 按行内容去重，重复次数取单文件内最大值（merged 拼接日志不重复计数）；无 native.log 时退回 `backend.log` | 二者行格式不同，不能同时解析 |
| 只读 | 每个 sqlite（含 -wal/-shm）先复制到临时目录再打开，用完即删 | 绝不写证据 |

---

## 3. 汇总指标

### 3.1 原生真机（DeepSeek，4 目录汇总；逐目录见 `benchmark-2026-09-08-native.md`）

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | **汇总** |
|---|---|---|---|---|---|
| turn 数 / 终态 | 23（16 完成 / 7 失败） | 22（18 / 4） | 5（4 / 1） | 5（4 / 1） | **55（42 / 13）** |
| turn 墙钟 p50 / p95 / max（库内，s） | 45.1 / 316.6 / 317.3 | 25.2 / 179.6 / 206.0 | 24.5 / 460.7 / 460.7 | 40.9 / 264.8 / 264.8 | **31.0 / 316.6 / 460.7** |
| turn 墙钟 p50 / p95 / max（UI 驱动，s） | 32 / 264 / 326 | 25 / 183 / 222 | — | 82 / 278 / 278 | **26 / 264 / 326** |
| provider 调用数 | 183 | 124 | 32 | 35 | **374**（373 成功，1 失败 `provider_protocol_error`） |
| provider 延迟 p50 / p95 / max（ms） | 3 464 / 25 661 / 116 955 | 4 820 / 30 521 / 136 023 | 7 376 / 55 060 / 97 574 | 5 100 / 42 515 / 44 657 | **4 450 / 32 619 / 136 023** |
| 每 turn 调用数 p50 / p95 / max | 2 / 25 / 25 | 2 / 25 / 25 | 2 / 25 / 25 | 3 / 25 / 25 | **2 / 25 / 25** |
| 输入 token/turn p50 / p95 | 18 087 / 549 265 | 19 027 / 411 214 | 19 012 / 828 016 | 21 002 / 695 222 | **19 027 / 695 222** |
| 输出 token/turn p50 / p95 | 1 707 / 15 559 | 1 260 / 9 279 | 1 472 / 30 600 | 2 137 / 13 491 | **1 546 / 16 412** |
| 单次输入峰值 / 合计输入 / 其中 cache | 44 378 / 3.14 M / 2.24 M | 31 154 / 1.82 M / 1.27 M | 55 563 / 0.89 M / 0.71 M | 37 681 / 0.78 M / 0.57 M | **55 563 / 6.62 M / 4.79 M（72%）** |
| Host 估算 ÷ 实报（messages）p50 / p95 | 0.613 / 0.695 | 0.521 / 0.708 | 0.645 / 0.749 | 0.548 / 0.681 | **0.582 / 0.705** |
| Host 估算 ÷ 实报（含 tools）p50 | 0.824 | 0.782 | 0.826 | 0.743 | **0.818** |
| 窗口 / 档位 / effective_input_budget | 32000 / 8192 / 26752 | 同左 | 1 000 000 / 32768 / 895 904 | 32000 / 8192 / 26752 | 32000 / 8192 / 26752 |
| foreground_recall 次数 / p50 / p95 / max（ms） | 14 / 3 505 / 5 304 / 5 304 | 10 / 693 / 1 384 / 1 384 | 0 | 0 | **24 / 960 / 4 795 / 5 304** |
| foreground_recall 状态 | returned 3 / raised 11 | returned 10 | — | — | returned 13 / raised 11 |
| SDK 终态 / 超时率 | completed 26 / deadline_exceeded 11 → 29.7% | 32 / 0 | 5 / 0 | 5 / 0 | **68 / 11 → 13.9%** |
| 分析通道 invocation / 失败 | 23 / 2（authority_rejected） | 22 / 0 | 4 / 0 | 4 / 1 | **53 / 3（5.7%）** |
| 分析通道延迟 p50 / p95 / max（ms） | 10 883 / 44 346 / 51 042 | 8 983 / 44 381 / 64 142 | 30 442 / 45 127 / 45 127 | 29 601 / 31 387 / 31 387 | **12 477 / 45 127 / 64 142** |
| 分析通道输入 / 输出 token 每批 p50（max） | 3 992（4 228）/ 733（3 376） | 4 188（9 292）/ 612（4 384） | 3 909 / 2 440 | 3 910 / 1 984 | **4 065（9 292）/ 774（4 384）** |
| 分析批次 / 失败率 / 原因 | 23 / 8.7% / authority_rejected 2 | 22 / 0 | 9 / 55.6% / executor_failed 5 | 5 / 40% / authority_rejected 1 + handed_off 1 | **59 / 15.2%** |
| 快照回执 / causal_groups p50（max）/ trimmed | 184 / 4（6）/ 0 | 125 / 3（8）/ 0 | 32 / 5（5）/ 0 | 35 / 4（4）/ 0 | **376 / 4（8）/ 0** |
| current_tool_pages / tokens max / context_page_in | 0 / 0 / 0 | 427 / 8 243 / 4 | 0 / 0 / 0 | 0 / 0 / 0 | **427 / 8 243 / 4** |
| context 三元组（preparing=staged=consumed） | 23 | 22 | 5 | 5 | **55，一致** |
| Run 失败率 / 原因 | 30.4%：max_turns 1、repeated_tool 3、driver_failed 3 | 18.2%：max_turns 3、driver_failed 1 | 20%：max_turns 1 | 20%：max_turns 1 | **23.6%（13/55）** |
| 驱动层 runtime.failed | transcript_mismatch 8 | — | — | — | 8 |

### 3.2 语料批次（luna，10 目录汇总；逐目录见 `benchmark-2026-09-08-corpus.md`）

| 指标 | 汇总（294 运行时根） |
|---|---|
| turn 数 / 终态 | 321（311 有终态：285 完成 / 26 失败；10 未结算） |
| turn 墙钟 p50 / p95 / max（Host 时钟，s） | 53.1 / 268.1 / 800.2 |
| 用例级耗时 p50 / p95 / max（`batch-summary.jsonl`，s） | 73.6 / 345.6 / 900.4（305 例；峰值 RSS 1 872 MiB） |
| 用例级 oracle 判定 | PENDING_POST_TERMINAL_REVIEW 244、EXECUTION_FAILED 39（12.8%）、SETUP_BLOCKED 14、NO_PACKET 8 |
| provider 调用 | 786 条（754 成功 / 24 失败 / 7 unknown / 1 handed_off）；错误码：`provider_server_error` 13、`provider_rate_limited` 8、`provider_error_after_handoff` 7、`primary_history_disclosure_rejected` 3 |
| provider 延迟 | **不可测**（754 条 0 ms，冻结时钟）→ N-A |
| 每 turn 调用数 p50 / p95 / max | 2 / 7 / 16 |
| 输入 token/turn p50 / p95 / max；单次峰值 | 7 866 / 51 570 / 226 540；22 910（合计 4.76 M，cache 3.20 M = 67%） |
| Host 估算 ÷ 实报（messages / 含 tools）p50 | 0.161 / 0.573（短对话下 tools schema 与 cache 占比大） |
| foreground_recall 次数 / p50 / p95 / max（ms） | 260 / 284 / **441** / 606（>500 ms 的 5 次；run-01f 单目录 p95 500.4 ms 恰好越线） |
| SDK 终态 / 超时率 / 降级码 | completed 260 / 0 / `NO_ACTIVE_GENERATION` 257、`cognitive_vector_unavailable` 39 |
| 分析通道 | 276 invocation 全部接受、276 批次全部 applied；延迟 / token **不可测**（冻结时钟，275 条 0 ms） |
| 上下文组装 | 786 回执全部 32768 档；causal_groups p50 1（max 2）；trimmed 0；context_page_in 2 |
| context 三元组 | 321 = 321 = 321，一致 |
| Run 失败率 / 原因 | 8.4%（26/311）全部 `driver_failed`（中转 5xx / 限流 / handoff 后错误为主） |
| 复审质量（171 已计分） | pass 90.6%；required recall **94.5%**（146）；额外类型率 **28.6%**（171）；no-match 正确率 **100%**（19）；隐私违规 **0**；硬触发 **55/55** |
| NOT_SCORED 11 例原因 | other 3、followup_unmet 2、approval_blocked 1、setup 1、setup_runway 1、provider_502_503_timeout 1、host_runtime_error 1、unsupported(NO_PACKET) 1 |

### 3.3 全部 14 目录汇总（`benchmark-2026-09-08-all.md`）

turn 墙钟 p50 51.6 s / p95 274.5 s；provider 延迟（仅可测的 373 条）p50 4 450 ms / p95 32 619 ms / max 136 023 ms；
每 turn 调用 p50 2 / p95 12 / max 25；输入 token/turn p50 12 867 / p95 163 877；foreground_recall（284 次）p50 286 ms /
p95 854 ms / max 5 304 ms，SDK 超时率 3.2%（11/339）；分析通道可测延迟 p50 12 477 ms / p95 45 127 ms，invocation
失败率 0.9%，批次失败率 2.7%；Run 失败率 10.7%（39/366）。

---

## 4. 契约阈值判定

| 门 | 阈值 / 依据 | 原生真机 | 语料批次 | 全部汇总 |
|---|---|---|---|---|
| typed recall p95（foreground_recall，Host 墙钟） | ≤ 500 ms（HM-AC-8） | **FAIL** 4 795 ms（尝试3 5 304 / 尝试4 1 384；r14、r15 无样本） | **PASS** 441 ms | **FAIL** 854 ms |
| typed recall hard deadline | max ≤ 2 000 ms 且 SDK 超时率 0（HM-AC-8 / S3） | **FAIL** max 5 304、超时 11（尝试4 单看 PASS：max 1 384、超时 0） | **PASS** max 606、超时 0 | **FAIL** |
| typed recall p95（全部 caller，信息性） | ≤ 500 ms | FAIL 3 777 ms | PASS 400 ms | FAIL 518 ms |
| provider 超时 | max < 240 s 且无 timeout 错误码（S5） | **PASS** max 136.0 s | N-A（冻结时钟） | PASS |
| provider 实报单次 input_tokens 峰值 | ≤ effective_input_budget 26 752（S5） | **FAIL** 55 563（r14 无 override 单看 PASS；尝试3 44 378、尝试4 31 154、r15 37 681 均 FAIL） | N-A（无窗口证据） | FAIL |
| Host 估算单次峰值（messages） | ≤ 26 752 | **PASS** 26 221 | N-A | PASS |
| 分析通道延迟 / 输入 / 输出 | ≤ 180 000 ms / 16 384 / 6 144（请求预算） | **PASS** 64 142 / 9 292 / 4 384 | 延迟 PASS（唯一可测 1 ms）；token N-A | PASS |
| context 三元组一致 | preparing = staged = consumed | **PASS** 55 | **PASS** 321 | PASS 376 |
| required-type recall | ≥ 90% | — | **PASS** 94.5%（146） | PASS |
| 额外类型率 | ≤ 15% | — | **FAIL** 28.6%（171） | FAIL |
| no-recall（no-match 类）正确率 | ≥ 90% | — | **PASS** 100%（19） | PASS |
| 隐私禁止项 | 100% 无违规 | — | **PASS** 0 违规 | PASS |
| 硬触发正确率 | 100% | — | **PASS** 55/55 | PASS |
| turn 墙钟、每 turn 调用数、估算比、Run 失败率、分析失败率 | 契约未给阈值 | N-A | N-A | N-A |

---

## 5. 诚实解读

1. **typed recall 门的 FAIL 由「修复前」的尝试 3 主导。** 11 次 `deadline_exceeded` 全部在尝试 3（Host `b3682fe1`，
   SDK 0.6.26 把 `embed_batch` 放在 `_write_lock` 内、Host 尚未做嵌入批处理/deadline 抬升），Host 侧墙钟 p50 3.5 s。
   尝试 4 已无 SDK 超时，但 Host 侧 p50 693 ms / p95 1 384 ms 仍是契约 500 ms 的 1.4–2.8 倍——原因是尝试 4 把请求
   deadline 抬到 2 000 ms 换取不超时，而不是让检索本身变快。同一 Host 口径下语料批次 p95 441 ms（260 次）能过门，
   说明 A6 场景（24 轮累积、40 KB 工具结果、向量道 `cognitive_vector_stale`）比语料单轮更重。**当前构建在 A6 负载下
   不满足 p95 ≤ 500 ms**，需在 SDK 0.6.27（embed 移出写锁）合入后用同一脚本复测。
2. **hard deadline 2 s 在尝试 4 满足（max 1 384 ms），语料批次满足（max 606 ms）**；只有尝试 3 越线。
3. **「provider 实报峰值超预算」不是 provider 报错，而是估算器偏差。** Host 用 `text_tokens`（CJK 每字 1、其余每 4
   字符 1）做预算裁剪，估算峰值 26 221 落在 26 752 之内；但 DeepSeek 实报 input_tokens（含 cache 命中与 tools schema）
   是估算的 1.2–1.9 倍（messages 口径中位 0.58，含 tools 0.82）。因此 override 到 32 000 的「窗口」在 provider 侧实际被
   突破（44 378 / 37 681 / 31 154），只是 DeepSeek 真窗口 1 M 掩盖了它。**如果换成真 32 K 窗口的模型，这些请求会被
   provider 拒绝**。整份证据里 `trimmed_groups` 恒为 0，A6-3「整组裁剪」从未触发，与 `a6_verify.py` 的 INCONCLUSIVE
   一致——预算门在 32 000 override 下只能证明「估算值不超」，不能证明「真实 token 不超」。
4. **turn 墙钟的长尾是缺陷不是模型。** 尝试 3 p95 316 s 对应 T7：`primary_history_transcript_mismatch` ×8 驱动停摆
   → 修复 `933df61e` → 重启续跑；r14 max 460 s 是 Procedure 轮里 25 次调用打满 `react_max_turns`；语料 max 800 s 与
   run-01j 674 s 是中转 5xx/限流后 `provider_error_after_handoff` 重试。去掉这些，原生 COMPLETED turn 中位 25–45 s，
   UI 驱动口径中位 26 s。
5. **每 turn 25 次调用 = `react_max_turns` 上限**，四次原生运行各至少一次打满（共 6 次 `react_max_turns_exceeded`
   + 3 次 `react_repeated_tool_exceeded`），全是模型在 `task_scope_search`（0 候选）/ 空参 `task_scope_update` /
   `decision.record` 被 `nothing_to_close` 拒绝后循环——即 RUN-01-ATTEMPTS 的事件 B/C。尝试 4 修复后仍有 3 次，
   说明发现面提示尚未根治。输入 token/turn p95 695 K（cache 占 72%）就是这些循环轮的账单。
6. **分析通道**：原生 53 次 invocation 3 次 `analysis_delivery_authority_rejected`（recall authority 过期，已有
   DECISION），r14 的 5 次 `analysis_executor_failed` 是 DeepSeek 解析失败修复前的样本。延迟 p95 45 s、单批输入 ≤ 9 292、
   输出 ≤ 4 384，都在请求预算内；但 r14 批次墙钟 p95 210 s 说明 executor 失败会让批次在 180 s deadline 附近徘徊。
7. **语料批次数字的可信边界**：SDK 侧时间戳被夹具冻结，provider 与分析通道延迟一律 N-A；Host 侧时钟真实，turn 墙钟与
   typed recall 延迟可信。run-01 的 C01 用例跑在 0.6.20（recall p95 10 ms，无向量道），把它并入汇总会把 p95 拉低，
   因此 3.2 表里的 441 ms 应视为**偏乐观下界**——去掉 run-01 后各批次 p95 在 318–500 ms 之间。
8. **额外类型率 28.6% FAIL 是真实缺陷**：复审 notes 反复出现「多提了 episode 类型」（C01 全部 `predicted_types =
   [episode, semantic]`），是主模型在 `context_route` 里习惯性附带 episode，而非召回实现问题；这是 prompt/发现面的
   工作（另一路 agent 正在改 analysis prompt / 401 runner），本文只记录。
9. **未覆盖**：`--review-verdicts` 只覆盖 182/240 条语料（`run-01.unreviewed.txt` 与 run-02 批不在内）；原生真机
   没有复审判定；`no-recall` 类只有 19 例，统计力弱。

---

## 6. 裁决记录（用户授权由本 agent 裁决，不再询问）

| 事项 | 裁决 | 理由 |
|---|---|---|
| typed recall 契约门用哪个 caller | `foreground_recall`（Host 墙钟）判门；「全部 caller」只作信息行 | 契约「本地记忆检索」指用户关键路径；`analysis_candidates` 等是后台 caller |
| recall 延迟用 Host 还是 SDK 计时 | Host `memory_call_attempts`；SDK `typed_recall_requests/terminals` 的 created_at 相同无法计时 | SDK 只留终态类型，Host 墙钟更严且用户可感 |
| provider 0 ms 延迟 | 视为未测量，不进统计，单列计数 | 语料夹具冻结时钟；0 ms 计入会把 p50 压成 0 |
| 每 turn 分组键 | (运行时根, root_run_id) | 语料 host_run_id 由确定性种子派生、跨用例重复 |
| Host 估算口径 | messages 用 Host 同一 `text_tokens`；另列含 tools schema | 与 Host 预算裁剪一致；tools 是 provider 计费但 Host 不算的部分 |
| 预算窗口来源 | `native.log model_context_resolved` 最后一次；`sdk_provider_attempt_audit.context_window` 恒 0 不用 | 与 `a6_verify.py` 一致 |
| 日志重复 | native.log 按行去重、单文件内最大重复数；无 native.log 才用 backend.log | merged 日志与分段日志并存；两种日志行格式不同 |
| 复审判定字段 | 只依赖 `required_hit`/`extra_types`/`privacy_violation`/`hard_trigger_correct`；`category` 含 no-match 才算 no-recall；同 case_id 后文件覆盖 | category 是自由文本；后批次是复审 |
| 无契约阈值的指标 | 一律 N-A，不自设阈值 | 任务要求只在契约给出时判定 |

---

## 7. 复现

```bash
W=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness   # 或任一 worktree
PY=$W/backend/.venv/bin/python; E=$W/.local-test-evidence
PYTHONPATH=$W/backend $PY scripts/benchmark/hm_benchmark.py --selftest
PYTHONPATH=$W/backend $PY scripts/benchmark/hm_benchmark.py \
  --evidence $E/2026-09-08/native-a6-b3682fe1 --label "A6尝试3=$E/2026-09-08/native-a6-b3682fe1" \
  --evidence $E/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1 --label "A6尝试4=$E/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1" \
  --evidence $E/2026-09-08/native-7cec5249 --label "r14=$E/2026-09-08/native-7cec5249" \
  --evidence $E/2026-09-08/native-r15-188394b1 --label "r15=$E/2026-09-08/native-r15-188394b1" \
  --out plans/2026-09-08-hm-to-a6/benchmark-2026-09-08-native.json --md
# 语料：--evidence $E/2026-09-07/corpus-batch/run-01 … run-01j，外加 6 个 --review-verdicts；全部 14 目录合并见 -all
cd backend && PYTHONPATH=$PWD $PY -m pytest tests/quality/test_hm_benchmark.py -q
```

---

## 8. 文件

- `scripts/benchmark/hm_benchmark.py` —— 提取器 + `--selftest`
- `backend/tests/quality/test_hm_benchmark.py` —— 11 个单测（分位数、估算器、夹具指标、日志去重、backend.log 退回、冻结时钟、跨目录合并、自由文本复审字段、Markdown、CLI）
- `plans/2026-09-08-hm-to-a6/benchmark-2026-09-08-{native,corpus,all}.{json,md}` —— 本次结果
