# Phase3 子代理模型运行记录

采样时间：2026-09-12T22:15:22.229749+00:00。主会话固定 GPT-6 Astra/high；最多3个子代理并行，按任务选 Luna/Terra/Sol/Astra。

下表为本机按 response_id 去重的 token 使用记录，不是订阅扣费。缓存输入已包含在输入总数中；输出包含推理 token，不重复相加。同一子代理复用多个任务时为累计数据，运行跨度含等待，不能相加当作整批耗时。

| 子代理 / 实际模型与推理 | 运行跨度（分） | 非缓存输入 | 缓存输入 | 输出 | 结果与返工 |
|---|---:|---:|---:|---:|---|
| Noether / gpt-5.6-terra medium | 22.08 | 167,521 | 4,790,528 | 28,909 | FAILED_ESCALATED_ASTRA; 主复核 3 轮 |
| Dalton / gpt-5.6-terra medium | 4.85 | 99,122 | 1,925,888 | 13,916 | HELPER_ACCEPTED_7PASS_0.15s_INTEGRATED_MAIN56PASS; 主复核 0 轮 |
| Aquinas / gpt-5.6-luna medium | 8.99 | 134,985 | 3,398,912 | 20,494 | ACCEPTED_CLASSIFICATION_WITH_OPEN_PRODUCTION_GAP; 主复核 1 轮 |
| Nietzsche / gpt-5.6-sol high | 60.21 | 439,464 | 23,959,552 | 77,616 | Host judgment native accepted; FIRST production2P1 fixed and independent ACCEPT;56PASS8.79s after main repaired true verified-knowledge fixture; 主复核 4 轮 |
| Kierkegaard / gpt-5.6-luna medium | 5.77 | 136,093 | 1,217,280 | 12,023 | ACCEPTED; 主复核 1 轮 |
| Beauvoir / gpt-6-astra high | 47.84 | 209,495 | 5,696,000 | 43,581 | OSkill2PASS9.59s; originalSDKresultsamehash/coldzeroWorker; unknownholds unchanged; 主复核 0 轮 |
| Galileo / gpt-6-astra high | 40.72 | 341,082 | 2,792,832 | 12,286 | FIRST2P1 accepted/fixed/rereviewACCEPT; P34 production ingress gap and bounded implementation design delivered; 主复核 0 轮 |

观测结论：Terra 的独立预算 helper 7项通过；同模型的跨进程恢复任务经历3轮返工后升级 Astra，Astra 修正后主测2项通过（9.59秒）。Luna 完成证据分类与 AST 兼容定位，主代理修正了精确证据ID和仅忽略空字段的边界。Sol 完成 Host 判定显示，预算集成经 Astra 指出2项P1后修复；主代理进一步补齐真实知识消费夹具，当前56项通过（8.79秒）。新P32补丁与P34入口仍在验证/实施。

这些不是同等任务的对照实验，不能据此给模型排总榜或声称节省百分比。后续仍按语义复杂度、质量要求和返工风险选择模型；普通有界切片可用轻量模型，跨进程恢复和预算契约适合更强模型。主协调时间与本人的实现、原生UI交织，未独立计时；保留复核轮次和测试时间，不虚构协调分钟数。

原始索引：`.local-test-evidence/2026-09-13/p33-g/agent-efficiency-0500/usage-0619.json`；SHA-256 `61aa6802eee4ac8d0238d4c1e57084c94b0d025c76aa27652fbc8f11614e858a`。原始记录仅本机 ignored 保存。

## 2026-09-13 07:10 CST checkpoint

Captured at 2026-09-12T23:10:41.190347+00:00. Lifecycle spans include multiple assignments and idle time; they are not isolated implementation durations. Cached input is a subset of input; output includes reasoning. Concurrent spans must not be summed.

| Agent / observed model and effort | Lifecycle minutes | Uncached input | Cached input | Output | Outcome |
|---|---:|---:|---:|---:|---|
| Noether / gpt-5.6-terra medium | 22.08 | 167,521 | 4,790,528 | 28,909 | FAILED_ESCALATED_ASTRA |
| Dalton / gpt-5.6-terra medium | 4.85 | 99,122 | 1,925,888 | 13,916 | HELPER_ACCEPTED_7PASS_0.15s_INTEGRATED_MAIN56PASS |
| Aquinas / gpt-5.6-luna medium | 8.99 | 134,985 | 3,398,912 | 20,494 | ACCEPTED_CLASSIFICATION_WITH_OPEN_PRODUCTION_GAP |
| Nietzsche / gpt-5.6-sol high | 115.53 | 846,364 | 58,966,784 | 175,776 | RUNNING |
| Kierkegaard / gpt-5.6-luna medium | 5.77 | 136,093 | 1,217,280 | 12,023 | ACCEPTED |
| Beauvoir / gpt-6-astra high | 103.15 | 829,516 | 17,738,752 | 96,645 | RUNNING |
| Galileo / gpt-6-astra high | 56.46 | 587,809 | 6,392,960 | 22,880 | ACCEPTED |
| Tesla / gpt-5.6-terra high | 5.05 | 130,221 | 1,179,136 | 14,268 | ACCEPTED_FAILURE_ORACLE_MAIN_FIXED |
| Ohm / gpt-5.6-sol high | 18.29 | 183,573 | 6,013,184 | 21,933 | ACCEPTED_SOFTWARE_NATIVE_PENDING |
| Zeno / gpt-6-astra high | 6.09 | 68,538 | 819,072 | 8,010 | RUNNING |

Sol document arbitration required source-attribution versus world-candidate correction, followed by a parent fix to the post-drain status assertion. Actual service route and affected controls:14 PASS/25.55s; native UI pending. Astra context cold control also needed repairs: missing Orch heartbeat, logical versus wire request identities, then a still-open cold retrieval assertion. Stronger models still require independent verification. New external effect receipt-loss, SIGKILL and multi-database backup control uses Astra/high because of its recovery and idempotency risk.

Parent rework is included: two queue-test field mistakes, one invalid test path, and lint rule selection briefly removed necessary noqa comments (restored, then checked under the full rule set). Queue strengthening and Manager controls:6 PASS/8.02s. Isolated parent coordination time is unavailable. These unmatched tasks cannot establish model rankings or a savings percentage.

Raw local index: `.local-test-evidence/2026-09-13/p33-g/agent-efficiency-0500/usage-0710.json`; SHA-256 `9c1888ba8f00ec13d24de4bb7236c58b1a4058ff09a6a032b8f87d03195e2ebf`. Raw records remain ignored and local.

## 2026-09-13 07:39 adaptive-model checkpoint

Main remains GPT-6 Astra/high. Three-slot limit retained. Small form review used Luna medium; cross-state review used Astra high; composite pressure fixture and P34 production repair use Sol high. Cached input is included in total input, not charged again as a separate token count. Local usage records do not expose official billed credit cost. This sample was actually collected at 2026-09-13 07:39:38 CST; its local filename label usage-0747 is not the collection time.

| Agent / actual runtime | Lifecycle minutes (includes idle and reassignments) | Uncached input | Cached input | Output | Accepted result / limitation |
|---|---:|---:|---:|---:|---|
| Zeno / gpt-6-astra high | 35.04 | 188003 | 6085248 | 29525 | A08 external applied-once/cold/backup1PASS8.35s after main fixture repairs; three P34 production P1s identified; fixes pending. |
| Mill / gpt-5.6-luna medium | 7.46 | 60863 | 293120 | 3354 | One real P1 reserve leak into next Mission; main fix/regression85PASS1.33s/typecheck; limited re-review ACCEPT. Closed. |
| Nash / gpt-5.6-sol high | 3.40 | 67006 | 926208 | 7992 | New bounded A03 priority/drain test-only assignment; not yet tested or accepted. |

These are unmatched tasks and overlapping lifecycle spans, not a controlled model ranking or savings percentage. Parent overhead is not isolated: main fixed Critic coverage/NULL test expectations, integrated reviews, ran tests, updated architecture and prepared source UI.
