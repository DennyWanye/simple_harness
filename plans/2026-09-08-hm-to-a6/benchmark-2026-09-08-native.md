# HM 基准报告（hm-benchmark-v1）

- 生成时间：2026-09-08T18:39:23+08:00
- 估算器：deskpet.sdk_adapters.context_partitions.text_tokens
- 分位数：nearest-rank；汇总列为各目录原始样本合并后重新计算。

## 1. 证据目录

| 名称 | 路径 | 运行时根数 | 警告数 |
|---|---|---|---|
| A6尝试3 | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-a6-b3682fe1` | 1 | 0 |
| A6尝试4 | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-a6-run4/primary-ui-7f_uv2a1` | 1 | 0 |
| r14 | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-7cec5249` | 1 | 0 |
| r15 | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-r15-188394b1` | 1 | 0 |

## 2. 每 turn 墙钟（发送 → 终态）

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | 汇总 |
|---|---|---|---|---|---|
| turn 数（库内） | 23 | 22 | 5 | 5 | 55 |
| 终态分布 | COMPLETED=16; FAILED=7 | COMPLETED=18; FAILED=4 | COMPLETED=4; FAILED=1 | COMPLETED=4; FAILED=1 | COMPLETED=42; FAILED=13 |
| 库内墙钟 p50 (ms) | 45,092.5 | 25,162 | 24,507 | 40,918.1 | 30,951.4 |
| 库内墙钟 p95 (ms) | 316,595 | 179,620 | 460,669.3 | 264,844 | 316,595 |
| 库内墙钟 max (ms) | 317,277.2 | 206,046.2 | 460,669.3 | 264,844 | 460,669.3 |
| 仅 COMPLETED p95 (ms) | 316,595 | 169,906.5 | 30,951.4 | 64,369.2 | 254,593.6 |
| UI 驱动 turn 数 | 23 | 24 | 0 | 5 | 52 |
| UI 驱动 p50 (s) | 32 | 25 | — | 82 | 26 |
| UI 驱动 p95 (s) | 264 | 183 | — | 278 | 264 |
| UI 驱动 max (s) | 326 | 222 | — | 278 | 326 |

## 3. Provider 调用

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | 汇总 |
|---|---|---|---|---|---|
| 调用数（结算审计） | 183 | 124 | 32 | 35 | 374 |
| 结算状态 | succeeded=182; failed=1 | succeeded=124 | succeeded=32 | succeeded=35 | succeeded=373; failed=1 |
| 延迟 p50 (ms, 库) | 3,464.3 | 4,820.3 | 7,375.6 | 5,100 | 4,449.7 |
| 延迟 p95 (ms, 库) | 25,660.5 | 30,521.3 | 55,060.3 | 42,514.6 | 32,618.8 |
| 延迟 max (ms, 库) | 116,955.3 | 136,022.6 | 97,574 | 44,657.4 | 136,022.6 |
| 延迟未测量（冻结时钟） | 0 | 0 | 0 | 0 | 0 |
| 延迟 p95 (ms, native.log) | 29,886 | 32,358 | 54,971 | 42,222 | 37,313 |
| native.log started/succeeded/failed | 206/205/1 | 146/146/0 | 39/36/2 | 40/39/0 | 431/426/3 |
| timeout_seconds | 240 | 240 | 240 | 240 | 240 |
| 每 turn 调用数 p50 | 2 | 2 | 2 | 3 | 2 |
| 每 turn 调用数 p95 | 25 | 25 | 25 | 25 | 25 |
| 每 turn 调用数 max | 25 | 25 | 25 | 25 | 25 |

## 4. Token

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | 汇总 |
|---|---|---|---|---|---|
| 模型 | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro |
| 输入 token/turn p50 | 18,087 | 19,027 | 19,012 | 21,002 | 19,027 |
| 输入 token/turn p95 | 549,265 | 411,214 | 828,016 | 695,222 | 695,222 |
| 输入 token/turn max | 721,647 | 432,112 | 828,016 | 695,222 | 828,016 |
| 输出 token/turn p50 | 1,707 | 1,260 | 1,472 | 2,137 | 1,546 |
| 输出 token/turn p95 | 15,559 | 9,279 | 30,600 | 13,491 | 16,412 |
| 单次输入峰值 | 44378 | 31154 | 55563 | 37681 | 55563 |
| 单次输入 p50 | 15,262 | 13,200 | 27,710 | 26,829 | 15,262 |
| 输入 token 合计 | 3139158 | 1817444 | 885762 | 777945 | 6620309 |
| 输出 token 合计 | 83114 | 76607 | 34351 | 19702 | 213774 |
| cache token 合计 | 2241152 | 1268736 | 707072 | 570240 | 4787200 |
| 窗口 / 档位 / effective_input_budget | 32000 / 8192 / 26752 | 32000 / 8192 / 26752 | 1000000 / 32768 / 895904 | 32000 / 8192 / 26752 | 32000 / 8192 / 26752 |
| Host 估算/实报（messages）p50 | 0.613 | 0.521 | 0.645 | 0.548 | 0.582 |
| Host 估算/实报（messages）p95 | 0.695 | 0.708 | 0.749 | 0.681 | 0.705 |
| Host 估算/实报（含 tools）p50 | 0.824 | 0.782 | 0.826 | 0.743 | 0.818 |
| Host 估算峰值（messages） | 24504 | 18838 | 26221 | 21113 | 26221 |

## 5. typed recall

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | 汇总 |
|---|---|---|---|---|---|
| foreground_recall 次数 | 14 | 10 | 0 | 0 | 24 |
| foreground_recall p50 (ms) | 3,505.4 | 692.6 | — | — | 960.3 |
| foreground_recall p95 (ms) | 5,304 | 1,383.8 | — | — | 4,795.1 |
| foreground_recall max (ms) | 5,304 | 1,383.8 | — | — | 5,304 |
| foreground_recall 状态 | returned=3; raised=11 | returned=10 | — | — | returned=13; raised=11 |
| analysis_candidates p95 (ms) | 38.5 | 132 | 4.1 | 12.4 | 132 |
| 全部 caller p95 (ms) | 4,795.1 | 854.2 | 9.9 | 13.7 | 3,777 |
| SDK 终态 | completed=26; deadline_exceeded=11 | completed=32 | completed=5 | completed=5 | completed=68; deadline_exceeded=11 |
| SDK 超时率 | 0.297 | 0 | 0 | 0 | 0.139 |
| SDK 降级码 | cognitive_vector_stale=1; STALE_ACTIVE_GENERATION=1 | cognitive_vector_stale=3; STALE_ACTIVE_GENERATION=2 | — | — | cognitive_vector_stale=4; STALE_ACTIVE_GENERATION=3 |
| 请求 deadline_ms | 1000 | 1000, 2000 | 1000 | 1000 | 1000, 2000 |

## 6. 分析通道

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | 汇总 |
|---|---|---|---|---|---|
| invocation 数 | 23 | 22 | 4 | 4 | 53 |
| invocation 状态 | public/analysis_validator_accepted=21; rejected_unsafe/analysis_delivery_authority_rejected=2 | public/analysis_validator_accepted=22 | public/analysis_validator_accepted=4 | public/analysis_validator_accepted=3; rejected_unsafe/analysis_delivery_authority_rejected=1 | public/analysis_validator_accepted=50; rejected_unsafe/analysis_delivery_authority_rejected=3 |
| invocation 失败率 | 0.087 | 0 | 0 | 0.25 | 0.057 |
| 延迟 p50 (ms) | 10,883 | 8,983 | 30,442 | 29,601 | 12,477 |
| 延迟 p95 (ms) | 44,346 | 44,381 | 45,127 | 31,387 | 45,127 |
| 延迟 max (ms) | 51,042 | 64,142 | 45,127 | 31,387 | 64,142 |
| 延迟未测量（冻结时钟） | 0 | 0 | 0 | 0 | 0 |
| 输入 token/批 p50 | 3,992 | 4,188 | 3,909 | 3,910 | 4,065 |
| 输入 token/批 max | 4,228 | 9,292 | 3,926 | 3,924 | 9,292 |
| 输出 token/批 p50 | 733 | 612 | 2,440 | 1,984 | 774 |
| 输出 token/批 max | 3,376 | 4,384 | 2,730 | 2,161 | 4,384 |
| 批次数 / 状态 | 23 / applied=21; failed=2 | 22 / applied=22 | 9 / applied=4; failed=5 | 5 / applied=3; failed=1; handed_off=1 | 59 / applied=50; failed=8; handed_off=1 |
| 批次失败率 | 0.087 | 0 | 0.556 | 0.4 | 0.152 |
| 批次失败原因 | analysis_delivery_authority_rejected=2 | — | analysis_executor_failed=5 | analysis_delivery_authority_rejected=1 | analysis_delivery_authority_rejected=3; analysis_executor_failed=5 |
| 批次墙钟 p95 (ms) | 44,538.3 | 44,618.3 | 210,474 | 31,669.5 | 51,256.6 |
| 请求预算 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 |

## 7. 上下文组装

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | 汇总 |
|---|---|---|---|---|---|
| 快照回执数 | 184 | 125 | 32 | 35 | 376 |
| budget_tier | 8192=184 | 8192=125 | 32768=32 | 8192=35 | 8192=344; 32768=32 |
| causal_groups p50 | 4 | 3 | 5 | 4 | 4 |
| causal_groups max | 6 | 8 | 5 | 4 | 8 |
| trimmed_groups 合计 | 0 | 0 | 0 | 0 | 0 |
| 含裁剪的回执数 | 0 | 0 | 0 | 0 | 0 |
| current_tool_pages 合计 | 0 | 427 | 0 | 0 | 427 |
| current_tool_tokens max | 0 | 8243 | 0 | 0 | 8243 |
| context_page_in 调用 | 0 | 4 | 0 | 0 | 4 |
| native.log 三元组 | context.preparing=23; context.staged=23; context.consumed=23 | context.preparing=22; context.staged=22; context.consumed=22 | context.preparing=5; context.staged=5; context.consumed=5 | context.preparing=5; context.staged=5; context.consumed=5 | context.preparing=55; context.staged=55; context.consumed=55 |

## 8. Run 失败

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | 汇总 |
|---|---|---|---|---|---|
| 有终态的 turn | 23 | 22 | 5 | 5 | 55 |
| 失败 Run 数 | 7 | 4 | 1 | 1 | 13 |
| 失败率 | 0.304 | 0.182 | 0.2 | 0.2 | 0.236 |
| 按 reason code | react_max_turns_exceeded=1; driver_failed=3; react_repeated_tool_exceeded=3 | react_max_turns_exceeded=3; driver_failed=1 | react_max_turns_exceeded=1 | react_max_turns_exceeded=1 | react_max_turns_exceeded=6; driver_failed=4; react_repeated_tool_exceeded=3 |
| 驱动层 runtime.failed | primary_history_transcript_mismatch=8 | — | — | — | primary_history_transcript_mismatch=8 |

## 9. 语料批次（用例级）

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 | 汇总 |
|---|---|---|---|---|---|
| 用例数 | 0 | 0 | 0 | 0 | 0 |
| 用例耗时 p50 (s) | — | — | — | — | — |
| 用例耗时 p95 (s) | — | — | — | — | — |
| oracle 判定 | — | — | — | — | — |
| EXECUTION_FAILED 率 | — | — | — | — | — |
| 峰值 RSS max (MiB) | — | — | — | — | — |

## 11. 契约阈值判定（汇总）

| 指标 | 阈值 | 实测 | 判定 | 依据 |
|---|---|---|---|---|
| typed recall（foreground_recall，Host 墙钟）p95 | ≤ 500 ms | 4,795.1 | **FAIL** | acceptance.md HM-AC-8 |
| typed recall（foreground_recall）最大值 / SDK deadline_exceeded | max ≤ 2000 ms 且 SDK 超时率 = 0 | max_ms=5,304; sdk_timeout_rate=0.139 | **FAIL** | acceptance.md HM-AC-8 / S3 hard deadline |
| typed recall（全部 caller）p95 | ≤ 500 ms | 3,777 | **FAIL** | acceptance.md HM-AC-8（信息性：含 analysis_candidates 等后台 caller） |
| Provider 调用最大延迟 / 超时错误 | max < 240 s 且无 timeout error_code | max_ms=136,022.6; timeout_error_codes=0; log_failed_or_degraded=3 | **PASS** | S5 provider timeout（native.log timeout_seconds） |
| Provider 实报单次 input_tokens 峰值 | ≤ effective_input_budget=26752 | 55563 | **FAIL** | S5 context_partitions.effective_input_budget（窗口取 native.log model_context_resolved） |
| Host 估算（messages）单次峰值 | ≤ effective_input_budget=26752 | 26221 | **PASS** | S5 effective_input_budget |
| 分析通道 LLM 延迟最大值 | ≤ 请求预算 deadline_ms=180000 | 64,142 | **PASS** | analysis_batches.request_json.budget |
| 分析通道输入 token 最大值 | ≤ 请求预算 max_input_tokens=16384 | 9,292 | **PASS** | analysis_batches.request_json.budget |
| 分析通道输出 token 最大值 | ≤ 请求预算 max_output_tokens=6144 | 4,384 | **PASS** | analysis_batches.request_json.budget |
| native.log context.preparing/staged/consumed 三元组计数一致 | 三者相等 | context.preparing=55; context.staged=55; context.consumed=55 | **PASS** | plans/2026-09-08-hm-to-a6/00-PLAN.md §0 |
| 每 turn 墙钟（enqueued→终态）p95 | 契约未给出阈值 | 316,595 | **N-A** | — |
| 每 turn provider 调用数 p95 | 契约未给出阈值 | 25 | **N-A** | — |
| Host 估算 / Provider 实报（messages）p50 | 契约未给出阈值 | 0.582 | **N-A** | — |
| Run 失败率 | 契约未给出阈值 | 0.236 | **N-A** | — |
| 分析通道 invocation 失败率 | 契约未给出阈值 | 0.057 | **N-A** | — |

### 11.1 逐目录判定

| 指标 | A6尝试3 | A6尝试4 | r14 | r15 |
|---|---|---|---|---|
| typed recall（foreground_recall，Host 墙钟）p95 | FAIL（5,304） | FAIL（1,383.8） | N-A（—） | N-A（—） |
| typed recall（foreground_recall）最大值 / SDK deadline_exceeded | FAIL（max_ms=5,304; sdk_timeout_rate=0.297） | PASS（max_ms=1,383.8; sdk_timeout_rate=0） | N-A（max_ms=—; sdk_timeout_rate=0） | N-A（max_ms=—; sdk_timeout_rate=0） |
| typed recall（全部 caller）p95 | FAIL（4,795.1） | FAIL（854.2） | PASS（9.9） | PASS（13.7） |
| Provider 调用最大延迟 / 超时错误 | PASS（max_ms=116,955.3; timeout_error_codes=0; log_failed_or_degraded=1） | PASS（max_ms=136,022.6; timeout_error_codes=0; log_failed_or_degraded=0） | PASS（max_ms=97,574; timeout_error_codes=0; log_failed_or_degraded=2） | PASS（max_ms=44,657.4; timeout_error_codes=0; log_failed_or_degraded=0） |
| Provider 实报单次 input_tokens 峰值 | FAIL（44378） | FAIL（31154） | PASS（55563） | FAIL（37681） |
| Host 估算（messages）单次峰值 | PASS（24504） | PASS（18838） | PASS（26221） | PASS（21113） |
| 分析通道 LLM 延迟最大值 | PASS（51,042） | PASS（64,142） | PASS（45,127） | PASS（31,387） |
| 分析通道输入 token 最大值 | PASS（4,228） | PASS（9,292） | PASS（3,926） | PASS（3,924） |
| 分析通道输出 token 最大值 | PASS（3,376） | PASS（4,384） | PASS（2,730） | PASS（2,161） |
| native.log context.preparing/staged/consumed 三元组计数一致 | PASS（context.preparing=23; context.staged=23; context.consumed=23） | PASS（context.preparing=22; context.staged=22; context.consumed=22） | PASS（context.preparing=5; context.staged=5; context.consumed=5） | PASS（context.preparing=5; context.staged=5; context.consumed=5） |
| 每 turn 墙钟（enqueued→终态）p95 | N-A（316,595） | N-A（179,620） | N-A（460,669.3） | N-A（264,844） |
| 每 turn provider 调用数 p95 | N-A（25） | N-A（25） | N-A（25） | N-A（25） |
| Host 估算 / Provider 实报（messages）p50 | N-A（0.613） | N-A（0.521） | N-A（0.645） | N-A（0.548） |
| Run 失败率 | N-A（0.304） | N-A（0.182） | N-A（0.2） | N-A（0.2） |
| 分析通道 invocation 失败率 | N-A（0.087） | N-A（0） | N-A（0） | N-A（0.25） |

