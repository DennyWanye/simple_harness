# HM 基准报告（hm-benchmark-v1）

- 生成时间：2026-09-08T18:39:33+08:00
- 估算器：deskpet.sdk_adapters.context_partitions.text_tokens
- 分位数：nearest-rank；汇总列为各目录原始样本合并后重新计算。

## 1. 证据目录

| 名称 | 路径 | 运行时根数 | 警告数 |
|---|---|---|---|
| run-01 | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01` | 81 | 2 |
| run-01b | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01b` | 4 | 0 |
| run-01c | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01c` | 12 | 0 |
| run-01d | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01d` | 7 | 0 |
| run-01e | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e` | 92 | 16 |
| run-01f | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01f` | 21 | 0 |
| run-01g | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01g` | 49 | 0 |
| run-01h | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01h` | 1 | 0 |
| run-01i | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01i` | 3 | 0 |
| run-01j | `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01j` | 24 | 0 |

## 2. 每 turn 墙钟（发送 → 终态）

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j | 汇总 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| turn 数（库内） | 101 | 4 | 12 | 7 | 96 | 21 | 52 | 1 | 3 | 24 | 321 |
| 终态分布 | COMPLETED=87; FAILED=13 | COMPLETED=3 | COMPLETED=10; FAILED=1 | COMPLETED=7 | COMPLETED=83; FAILED=7 | COMPLETED=19; FAILED=2 | COMPLETED=51; FAILED=1 | FAILED=1 | COMPLETED=3 | COMPLETED=22; FAILED=1 | COMPLETED=285; FAILED=26 |
| 库内墙钟 p50 (ms) | 31,347.9 | 57,800.9 | 72,117.3 | 59,107.9 | 76,519.3 | 67,054.5 | 62,179.3 | 105,881.6 | 76,876.8 | 69,507.9 | 53,119.2 |
| 库内墙钟 p95 (ms) | 196,369.1 | 210,975.9 | 220,777.2 | 140,207.6 | 448,096.7 | 298,500.3 | 225,098.8 | 105,881.6 | 181,564.6 | 674,233.2 | 268,063.2 |
| 库内墙钟 max (ms) | 422,033.1 | 210,975.9 | 220,777.2 | 140,207.6 | 792,812.8 | 524,791.3 | 274,522.1 | 105,881.6 | 181,564.6 | 800,163.2 | 800,163.2 |
| 仅 COMPLETED p95 (ms) | 200,991 | 210,975.9 | 220,777.2 | 140,207.6 | 448,096.7 | 524,791.3 | 225,098.8 | — | 181,564.6 | 674,233.2 | 274,522.1 |
| UI 驱动 turn 数 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| UI 驱动 p50 (s) | — | — | — | — | — | — | — | — | — | — | — |
| UI 驱动 p95 (s) | — | — | — | — | — | — | — | — | — | — | — |
| UI 驱动 max (s) | — | — | — | — | — | — | — | — | — | — | — |

## 3. Provider 调用

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j | 汇总 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 调用数（结算审计） | 220 | 5 | 14 | 11 | 168 | 53 | 90 | 1 | 7 | 78 | 647 |
| 结算状态 | succeeded=218; failed=2 | succeeded=5 | succeeded=14 | succeeded=11 | succeeded=168 | succeeded=53 | succeeded=90 | succeeded=1 | succeeded=7 | succeeded=78 | succeeded=645; failed=2 |
| 延迟 p50 (ms, 库) | — | — | — | — | — | — | — | — | — | — | — |
| 延迟 p95 (ms, 库) | — | — | — | — | — | — | — | — | — | — | — |
| 延迟 max (ms, 库) | — | — | — | — | — | — | — | — | — | — | — |
| 延迟未测量（冻结时钟） | 226 | 6 | 22 | 14 | 209 | 65 | 115 | 2 | 9 | 86 | 754 |
| 延迟 p95 (ms, native.log) | 61,051 | 165,424 | 148,719 | 81,756 | 167,911 | 115,688 | 128,679 | 99,085 | 95,237 | 156,939 | 137,990 |
| native.log started/succeeded/failed | 238/226/12 | 7/6/1 | 24/22/2 | 14/14/0 | 220/209/10 | 66/65/1 | 116/115/1 | 2/2/0 | 9/9/0 | 88/86/2 | 784/754/29 |
| timeout_seconds | 240 | 240 | 240 | 240 | 240 | 240 | 240 | 240 | 240 | 240 | 240 |
| 每 turn 调用数 p50 | 1 | 1 | 1 | 2 | 2 | 2 | 2 | 1 | 1 | 3 | 2 |
| 每 turn 调用数 p95 | 9 | 2 | 2 | 2 | 6 | 5 | 4 | 1 | 5 | 10 | 7 |
| 每 turn 调用数 max | 15 | 2 | 2 | 2 | 16 | 8 | 9 | 1 | 5 | 12 | 16 |

## 4. Token

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j | 汇总 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 模型 | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna | gpt-5.6-luna |
| 输入 token/turn p50 | 7,205 | 7,222 | 7,216 | 15,100 | 7,771 | 15,600 | 15,191 | 7,318 | 8,086 | 24,173 | 7,866 |
| 输入 token/turn p95 | 93,163 | 15,303 | 15,927 | 15,110 | 44,914 | 48,637 | 34,260 | 7,318 | 46,088 | 106,835 | 51,570 |
| 输入 token/turn max | 200,177 | 15,303 | 15,927 | 15,110 | 226,540 | 78,689 | 90,715 | 7,318 | 46,088 | 120,620 | 226,540 |
| 输出 token/turn p50 | 45 | 161 | 144 | 497 | 170 | 367 | 238 | 135 | 149 | 497 | 167 |
| 输出 token/turn p95 | 872 | 470 | 408 | 1,995 | 731 | 1,010 | 944 | 135 | 920 | 2,580 | 1,075 |
| 单次输入峰值 | 19662 | 8086 | 8249 | 7843 | 22910 | 11706 | 14007 | 7318 | 10323 | 12592 | 22910 |
| 单次输入 p50 | 7,206 | 7,222 | 7,216 | 7,275 | 7,770 | 8,516 | 7,870 | 7,318 | 8,685 | 8,168 | 7,825 |
| 输入 token 合计 | 1313187 | 37408 | 104843 | 82238 | 1288691 | 463064 | 719982 | 7318 | 61595 | 679497 | 4757823 |
| 输出 token 合计 | 13745 | 1002 | 2204 | 4469 | 22653 | 8304 | 16097 | 135 | 1188 | 17072 | 86869 |
| cache token 合计 | 867840 | 19968 | 66048 | 26624 | 883712 | 306688 | 560128 | 6656 | 34304 | 432640 | 3204608 |
| 窗口 / 档位 / effective_input_budget | — / — / — | — / — / — | — / — / — | — / — / — | — / — / — | — / — / — | — / — / — | — / — / — | — / — / — | — / — / — | — / — / — |
| Host 估算/实报（messages）p50 | 0.198 | 0.096 | 0.095 | 0.102 | 0.15 | 0.182 | 0.151 | 0.109 | 0.195 | 0.195 | 0.161 |
| Host 估算/实报（messages）p95 | 0.635 | 0.171 | 0.147 | 0.197 | 0.461 | 0.38 | 0.344 | 0.158 | 0.26 | 0.434 | 0.42 |
| Host 估算/实报（含 tools）p50 | 0.595 | 0.552 | 0.551 | 0.558 | 0.558 | 0.563 | 0.563 | 0.563 | 0.556 | 0.601 | 0.573 |
| Host 估算峰值（messages） | 13280 | 1379 | 1353 | 1670 | 17699 | 4672 | 5267 | 1351 | 2682 | 5778 | 17699 |

## 5. typed recall

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j | 汇总 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| foreground_recall 次数 | 40 | 3 | 12 | 7 | 91 | 32 | 44 | 1 | 6 | 24 | 260 |
| foreground_recall p50 (ms) | 6.3 | 362.3 | 280.8 | 336.4 | 285.2 | 291 | 281 | 318 | 409.3 | 371.3 | 283.6 |
| foreground_recall p95 (ms) | 10.4 | 362.4 | 408.8 | 347 | 396.4 | 500.4 | 491.1 | 318 | 472 | 465.6 | 440.8 |
| foreground_recall max (ms) | 11.5 | 362.4 | 408.8 | 347 | 606.4 | 503.6 | 496.2 | 318 | 472 | 468.4 | 606.4 |
| foreground_recall 状态 | returned=40 | returned=3 | returned=12 | returned=7 | returned=91 | returned=32 | returned=44 | returned=1 | returned=6 | returned=24 | returned=260 |
| analysis_candidates p95 (ms) | — | — | — | — | — | — | — | — | — | — | — |
| 全部 caller p95 (ms) | 10.4 | 362.4 | 408.8 | 347 | 390 | 316 | 291.2 | 318 | 472 | 463.2 | 399.7 |
| SDK 终态 | completed=40 | completed=3 | completed=12 | completed=7 | completed=91 | completed=32 | completed=44 | completed=1 | completed=6 | completed=24 | completed=260 |
| SDK 超时率 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SDK 降级码 | cognitive_vector_unavailable=39; NO_ACTIVE_GENERATION=39 | NO_ACTIVE_GENERATION=3 | NO_ACTIVE_GENERATION=11 | NO_ACTIVE_GENERATION=7 | NO_ACTIVE_GENERATION=91 | NO_ACTIVE_GENERATION=32 | NO_ACTIVE_GENERATION=44 | NO_ACTIVE_GENERATION=1 | NO_ACTIVE_GENERATION=5 | NO_ACTIVE_GENERATION=24 | cognitive_vector_unavailable=39; NO_ACTIVE_GENERATION=257 |
| 请求 deadline_ms | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 | 1000 |

## 6. 分析通道

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j | 汇总 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| invocation 数 | 75 | 4 | 12 | 7 | 81 | 21 | 48 | 1 | 3 | 24 | 276 |
| invocation 状态 | public/analysis_validator_accepted=75 | public/analysis_validator_accepted=4 | public/analysis_validator_accepted=12 | public/analysis_validator_accepted=7 | public/analysis_validator_accepted=81 | public/analysis_validator_accepted=21 | public/analysis_validator_accepted=48 | public/analysis_validator_accepted=1 | public/analysis_validator_accepted=3 | public/analysis_validator_accepted=24 | public/analysis_validator_accepted=276 |
| invocation 失败率 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 延迟 p50 (ms) | — | — | — | — | — | 1 | — | — | — | — | 1 |
| 延迟 p95 (ms) | — | — | — | — | — | 1 | — | — | — | — | 1 |
| 延迟 max (ms) | — | — | — | — | — | 1 | — | — | — | — | 1 |
| 延迟未测量（冻结时钟） | 75 | 4 | 12 | 7 | 81 | 20 | 48 | 1 | 3 | 24 | 275 |
| 输入 token/批 p50 | — | — | — | — | — | — | — | — | — | — | — |
| 输入 token/批 max | — | — | — | — | — | — | — | — | — | — | — |
| 输出 token/批 p50 | — | — | — | — | — | — | — | — | — | — | — |
| 输出 token/批 max | — | — | — | — | — | — | — | — | — | — | — |
| 批次数 / 状态 | 75 / applied=75 | 4 / applied=4 | 12 / applied=12 | 7 / applied=7 | 81 / applied=81 | 21 / applied=21 | 48 / applied=48 | 1 / applied=1 | 3 / applied=3 | 24 / applied=24 | 276 / applied=276 |
| 批次失败率 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 批次失败原因 | — | — | — | — | — | — | — | — | — | — | — |
| 批次墙钟 p95 (ms) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 请求预算 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 | deadline_ms=180000; max_input_tokens=16384; max_output_tokens=6144 |

## 7. 上下文组装

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j | 汇总 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 快照回执数 | 239 | 7 | 24 | 14 | 221 | 66 | 116 | 3 | 9 | 87 | 786 |
| budget_tier | 32768=239 | 32768=7 | 32768=24 | 32768=14 | 32768=221 | 32768=66 | 32768=116 | 32768=3 | 32768=9 | 32768=87 | 32768=786 |
| causal_groups p50 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| causal_groups max | 2 | 1 | 1 | 1 | 2 | 1 | 2 | 1 | 1 | 1 | 2 |
| trimmed_groups 合计 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 含裁剪的回执数 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| current_tool_pages 合计 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| current_tool_tokens max | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| context_page_in 调用 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 2 |
| native.log 三元组 | context.preparing=101; context.staged=101; context.consumed=101 | context.preparing=4; context.staged=4; context.consumed=4 | context.preparing=12; context.staged=12; context.consumed=12 | context.preparing=7; context.staged=7; context.consumed=7 | context.preparing=96; context.staged=96; context.consumed=96 | context.preparing=21; context.staged=21; context.consumed=21 | context.preparing=52; context.staged=52; context.consumed=52 | context.preparing=1; context.staged=1; context.consumed=1 | context.preparing=3; context.staged=3; context.consumed=3 | context.preparing=24; context.staged=24; context.consumed=24 | context.preparing=321; context.staged=321; context.consumed=321 |

## 8. Run 失败

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j | 汇总 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 有终态的 turn | 100 | 3 | 11 | 7 | 90 | 21 | 52 | 1 | 3 | 23 | 311 |
| 失败 Run 数 | 13 | 0 | 1 | 0 | 7 | 2 | 1 | 1 | 0 | 1 | 26 |
| 失败率 | 0.13 | 0 | 0.091 | 0 | 0.078 | 0.095 | 0.019 | 1 | 0 | 0.043 | 0.084 |
| 按 reason code | driver_failed=13 | — | driver_failed=1 | — | driver_failed=7 | driver_failed=2 | driver_failed=1 | driver_failed=1 | — | driver_failed=1 | driver_failed=26 |
| 驱动层 runtime.failed | — | — | — | — | — | — | — | — | — | primary_message_scope_source_mismatch=1 | primary_message_scope_source_mismatch=1 |

## 9. 语料批次（用例级）

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j | 汇总 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 用例数 | 91 | 4 | 12 | 7 | 92 | 21 | 49 | 1 | 3 | 25 | 305 |
| 用例耗时 p50 (s) | 49.2 | 72.8 | 87.9 | 73.6 | 103.6 | 79.4 | 75 | 117.1 | 92.8 | 85.2 | 73.6 |
| 用例耗时 p95 (s) | 211.9 | 344.4 | 419.2 | 155.3 | 465.3 | 312 | 247.9 | 117.1 | 196.5 | 813 | 345.6 |
| oracle 判定 | PENDING_POST_TERMINAL_REVIEW=63; EXECUTION_FAILED=17; SETUP_BLOCKED=5; NO_PACKET=6 | PENDING_POST_TERMINAL_REVIEW=3; EXECUTION_FAILED=1 | PENDING_POST_TERMINAL_REVIEW=10; EXECUTION_FAILED=2 | PENDING_POST_TERMINAL_REVIEW=7 | PENDING_POST_TERMINAL_REVIEW=69; EXECUTION_FAILED=13; SETUP_BLOCKED=9; NO_PACKET=1 | PENDING_POST_TERMINAL_REVIEW=19; EXECUTION_FAILED=2 | PENDING_POST_TERMINAL_REVIEW=48; EXECUTION_FAILED=1 | EXECUTION_FAILED=1 | PENDING_POST_TERMINAL_REVIEW=3 | PENDING_POST_TERMINAL_REVIEW=22; NO_PACKET=1; EXECUTION_FAILED=2 | PENDING_POST_TERMINAL_REVIEW=244; EXECUTION_FAILED=39; SETUP_BLOCKED=14; NO_PACKET=8 |
| EXECUTION_FAILED 率 | 0.187 | 0.25 | 0.167 | 0 | 0.141 | 0.095 | 0.02 | 1 | 0 | 0.08 | 0.128 |
| 峰值 RSS max (MiB) | 1,762 | 1,531 | 1,658 | 1,641 | 1,704 | 1,862 | 1,872 | 1,434 | 1,690 | 1,786 | 1,872 |

## 10. 语料复审判定（--review-verdicts）

| 指标 | 值 |
|---|---|
| cases | 182 |
| scored | 171 |
| categories | exact=20; task=4; no-match=20; C05=2; C08=4; C09=19; semantic=18; C02 语义/偏好(semantic)=1; entity=1; C03 冲突/多来源(entity)=18; C04 时间/提醒(time)=20; C05 任务(task)=2; C05 任务范围(task)=1; C06 跨范围/流程(cross-scope)=20; C08 保留/标量(suppressed)=7; C08 抑制标量(suppressed)=8; C11 过期资格(expired)=16; C11 过期(expired)=1 |
| verdicts | PASS=155; FAIL=16; NOT_SCORED=11 |
| pass_rate | 0.906 |
| required_recall_rate | 0.945 |
| required_recall_n | 146 |
| extra_type_rate | 0.286 |
| extra_type_n | 171 |
| no_recall_accuracy | 1 |
| no_recall_n | 19 |
| privacy_accuracy | 1 |
| privacy_violations | 0 |
| hard_trigger_accuracy | 1 |
| hard_trigger_n | 55 |
| not_scored_reasons | approval_blocked=1; other=3; setup=1; followup_unmet=2; setup_runway=1; provider_502_503_timeout=1; host_runtime_error(primary_message_scope_source_mismatch)=1; unsupported(NO_PACKET)=1 |

## 11. 契约阈值判定（汇总）

| 指标 | 阈值 | 实测 | 判定 | 依据 |
|---|---|---|---|---|
| typed recall（foreground_recall，Host 墙钟）p95 | ≤ 500 ms | 440.8 | **PASS** | acceptance.md HM-AC-8 |
| typed recall（foreground_recall）最大值 / SDK deadline_exceeded | max ≤ 2000 ms 且 SDK 超时率 = 0 | max_ms=606.4; sdk_timeout_rate=0 | **PASS** | acceptance.md HM-AC-8 / S3 hard deadline |
| typed recall（全部 caller）p95 | ≤ 500 ms | 399.7 | **PASS** | acceptance.md HM-AC-8（信息性：含 analysis_candidates 等后台 caller） |
| Provider 调用最大延迟 / 超时错误 | max < 240 s 且无 timeout error_code | max_ms=—; timeout_error_codes=0; log_failed_or_degraded=29 | **N-A** | S5 provider timeout（native.log timeout_seconds） |
| Provider 实报单次 input_tokens 峰值 | effective_input_budget 未知 | 22910 | **N-A** | S5 context_partitions.effective_input_budget（窗口取 native.log model_context_resolved） |
| Host 估算（messages）单次峰值 | effective_input_budget 未知 | 17699 | **N-A** | S5 effective_input_budget |
| 分析通道 LLM 延迟最大值 | ≤ 请求预算 deadline_ms=180000 | 1 | **PASS** | analysis_batches.request_json.budget |
| 分析通道输入 token 最大值 | ≤ 请求预算 max_input_tokens=16384 | — | **N-A** | analysis_batches.request_json.budget |
| 分析通道输出 token 最大值 | ≤ 请求预算 max_output_tokens=6144 | — | **N-A** | analysis_batches.request_json.budget |
| native.log context.preparing/staged/consumed 三元组计数一致 | 三者相等 | context.preparing=321; context.staged=321; context.consumed=321 | **PASS** | plans/2026-09-08-hm-to-a6/00-PLAN.md §0 |
| 每 turn 墙钟（enqueued→终态）p95 | 契约未给出阈值 | 268,063.2 | **N-A** | — |
| 每 turn provider 调用数 p95 | 契约未给出阈值 | 7 | **N-A** | — |
| Host 估算 / Provider 实报（messages）p50 | 契约未给出阈值 | 0.161 | **N-A** | — |
| Run 失败率 | 契约未给出阈值 | 0.084 | **N-A** | — |
| 分析通道 invocation 失败率 | 契约未给出阈值 | 0 | **N-A** | — |
| 语料复审 required-type recall | ≥ 90% | rate=0.945; n=146 | **PASS** | acceptance.md HM-AC-8（review-verdicts.json，已计分用例） |
| 语料复审额外类型率 | ≤ 15% | rate=0.286; n=171 | **FAIL** | acceptance.md HM-AC-8 |
| 语料复审 no-recall（no-match 类）判断正确率 | ≥ 90% | rate=1; n=19 | **PASS** | acceptance.md HM-AC-8 |
| 语料复审隐私禁止项正确率（已计分用例无 privacy_violation） | = 100% | rate=1; violations=0 | **PASS** | acceptance.md HM-AC-8 |
| 语料复审硬触发正确率 | = 100% | rate=1; n=55 | **PASS** | acceptance.md HM-AC-8（冻结路由集硬触发召回率 100%） |

### 11.1 逐目录判定

| 指标 | run-01 | run-01b | run-01c | run-01d | run-01e | run-01f | run-01g | run-01h | run-01i | run-01j |
|---|---|---|---|---|---|---|---|---|---|---|
| typed recall（foreground_recall，Host 墙钟）p95 | PASS（10.4） | PASS（362.4） | PASS（408.8） | PASS（347） | PASS（396.4） | FAIL（500.4） | PASS（491.1） | PASS（318） | PASS（472） | PASS（465.6） |
| typed recall（foreground_recall）最大值 / SDK deadline_exceeded | PASS（max_ms=11.5; sdk_timeout_rate=0） | PASS（max_ms=362.4; sdk_timeout_rate=0） | PASS（max_ms=408.8; sdk_timeout_rate=0） | PASS（max_ms=347; sdk_timeout_rate=0） | PASS（max_ms=606.4; sdk_timeout_rate=0） | PASS（max_ms=503.6; sdk_timeout_rate=0） | PASS（max_ms=496.2; sdk_timeout_rate=0） | PASS（max_ms=318; sdk_timeout_rate=0） | PASS（max_ms=472; sdk_timeout_rate=0） | PASS（max_ms=468.4; sdk_timeout_rate=0） |
| typed recall（全部 caller）p95 | PASS（10.4） | PASS（362.4） | PASS（408.8） | PASS（347） | PASS（390） | PASS（316） | PASS（291.2） | PASS（318） | PASS（472） | PASS（463.2） |
| Provider 调用最大延迟 / 超时错误 | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=12） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=1） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=2） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=0） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=10） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=1） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=1） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=0） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=0） | N-A（max_ms=—; timeout_error_codes=0; log_failed_or_degraded=2） |
| Provider 实报单次 input_tokens 峰值 | N-A（19662） | N-A（8086） | N-A（8249） | N-A（7843） | N-A（22910） | N-A（11706） | N-A（14007） | N-A（7318） | N-A（10323） | N-A（12592） |
| Host 估算（messages）单次峰值 | N-A（13280） | N-A（1379） | N-A（1353） | N-A（1670） | N-A（17699） | N-A（4672） | N-A（5267） | N-A（1351） | N-A（2682） | N-A（5778） |
| 分析通道 LLM 延迟最大值 | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | PASS（1） | N-A（—） | N-A（—） | N-A（—） | N-A（—） |
| 分析通道输入 token 最大值 | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） |
| 分析通道输出 token 最大值 | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） | N-A（—） |
| native.log context.preparing/staged/consumed 三元组计数一致 | PASS（context.preparing=101; context.staged=101; context.consumed=101） | PASS（context.preparing=4; context.staged=4; context.consumed=4） | PASS（context.preparing=12; context.staged=12; context.consumed=12） | PASS（context.preparing=7; context.staged=7; context.consumed=7） | PASS（context.preparing=96; context.staged=96; context.consumed=96） | PASS（context.preparing=21; context.staged=21; context.consumed=21） | PASS（context.preparing=52; context.staged=52; context.consumed=52） | PASS（context.preparing=1; context.staged=1; context.consumed=1） | PASS（context.preparing=3; context.staged=3; context.consumed=3） | PASS（context.preparing=24; context.staged=24; context.consumed=24） |
| 每 turn 墙钟（enqueued→终态）p95 | N-A（196,369.1） | N-A（210,975.9） | N-A（220,777.2） | N-A（140,207.6） | N-A（448,096.7） | N-A（298,500.3） | N-A（225,098.8） | N-A（105,881.6） | N-A（181,564.6） | N-A（674,233.2） |
| 每 turn provider 调用数 p95 | N-A（9） | N-A（2） | N-A（2） | N-A（2） | N-A（6） | N-A（5） | N-A（4） | N-A（1） | N-A（5） | N-A（10） |
| Host 估算 / Provider 实报（messages）p50 | N-A（0.198） | N-A（0.096） | N-A（0.095） | N-A（0.102） | N-A（0.15） | N-A（0.182） | N-A（0.151） | N-A（0.109） | N-A（0.195） | N-A（0.195） |
| Run 失败率 | N-A（0.13） | N-A（0） | N-A（0.091） | N-A（0） | N-A（0.078） | N-A（0.095） | N-A（0.019） | N-A（1） | N-A（0） | N-A（0.043） |
| 分析通道 invocation 失败率 | N-A（0） | N-A（0） | N-A（0） | N-A（0） | N-A（0） | N-A（0） | N-A（0） | N-A（0） | N-A（0） | N-A（0） |
| 语料复审 required-type recall | — | — | — | — | — | — | — | — | — | — |
| 语料复审额外类型率 | — | — | — | — | — | — | — | — | — | — |
| 语料复审 no-recall（no-match 类）判断正确率 | — | — | — | — | — | — | — | — | — | — |
| 语料复审隐私禁止项正确率（已计分用例无 privacy_violation） | — | — | — | — | — | — | — | — | — | — |
| 语料复审硬触发正确率 | — | — | — | — | — | — | — | — | — | — |

## 12. 提取警告

- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01/C07-03/scoring/C07-03/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01/C07-03/scoring/C07-03/runtime/userdata/data: 缺少 operation-audit.db
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-03/scoring/C08-03/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-03/scoring/C08-03/runtime/userdata/data: 缺少 operation-audit.db
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-05/scoring/C08-05/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-05/scoring/C08-05/runtime/userdata/data: 缺少 operation-audit.db
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-07/scoring/C08-07/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-07/scoring/C08-07/runtime/userdata/data: 缺少 operation-audit.db
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-08/scoring/C08-08/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-08/scoring/C08-08/runtime/userdata/data: 缺少 operation-audit.db
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-10/scoring/C08-10/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-10/scoring/C08-10/runtime/userdata/data: 缺少 operation-audit.db
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-12/scoring/C08-12/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-12/scoring/C08-12/runtime/userdata/data: 缺少 operation-audit.db
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-14/scoring/C08-14/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-14/scoring/C08-14/runtime/userdata/data: 缺少 operation-audit.db
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-17/scoring/C08-17/runtime/userdata/data: 缺少 execution-v6.sqlite3
- /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-07/corpus-batch/run-01e/C08-17/scoring/C08-17/runtime/userdata/data: 缺少 operation-audit.db

