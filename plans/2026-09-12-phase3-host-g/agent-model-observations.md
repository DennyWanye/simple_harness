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

## Checkpoint 2026-09-13 08:40 CST

Actual local turn_context confirms models below. Durations are child lifecycle (work, waits and rework), not isolated active work. Counts deduplicate response_id; cache is separate. No savings percentage can be inferred.

| Child | Actual model/effort | Lifecycle at snapshot | Uncached input | Cached input | Output |
|---|---|---:|---:|---:|---:|
| Arendt | gpt-5.6-terra/medium | 588.16s | 58645 | 545024 | 9219 |
| Locke | gpt-5.6-sol/high | 1621.34s | 184227 | 7542784 | 33946 |
| Boyle | gpt-5.6-terra/high | 902.39s | 132466 | 2241792 | 24552 |
| Plato | gpt-6-astra/high | 782.45s | 96414 | 2245888 | 15886 |

Arendt accepted after one review cycle corrected false-positive CLI bound tests and bool/int resume check. Locke P34 accepted after adapting to actual Host defaults and synthesis public shape (33PASS8.04s), now bounded load fixture. Boyle UI accepted after main requested decisive independent invalid/default/idempotency tests (101PASS1.47s), and independently reviewed P34. Plato authored a not-yet-run real search oracle and independently accepted doc8 compatibility; main requested deterministic baseline-audit materials before any paid run.
Main overhead includes UI/file chooser work, fixture review, broad-test legacy drift diagnosis, and report analysis; not separately timed. Main fixed global-order old Critic fixture exhaustion and two obsolete assertions after interrupted full suite1487PASS2FAIL5opt-in-skips/499.90s. No reduction of correctness gates or hidden skipped acceptance.


2026-09-14 long-context children (local numeric usage records, cached input is a subset):

| Child/model/effort | Wall dispatch-to-last-return seconds, including waits | Uncached input | Cached input | Output | Accepted output/rework |
|---|---:|---:|---:|---:|---|
| Faraday Sol/high | 872.22 |199652|7513344|27753| Mission profile wiring; parent corrected global legacy floor and added per-pool guards; covered by SDK26PASS |
| Mendel Terra/medium |495.36|126501|1498624|11655| P36 support-report privacy test; collection import rework, then PASS |
| Franklin Sol/high |1905.02|275244|4643072|31691| Four review issues fixed; new LC6 controls2PASS6.16s; no paid calls |

Raw index .local-test-evidence/2026-09-13/p33-g/agent-efficiency-0500/usage-context-final.json. Wall intervals overlap, are not summed or claimed as active compute. Parent coordination active time was not separately instrumented; parent observer rework243.64+98.50runnerseconds explicitly recorded in long-context-results.md. Different task scopes prevent a matched savings claim. All three closed. New Plato Sol/high is scoped to three P34 experiment-test files, minimal initial prompt/no inherited full history; parent continues native/evidence and compatibility work. Main model settings untouched.

## 2026-09-14 P36 and v11 diagnosis measurements

| Child/model/effort | Dispatch-to-last-return seconds including waits | Uncached input | Cached input | Output | Outcome/rework |
|---|---:|---:|---:|---:|---|
| Carver Terra/high |379.58|166973|1805056|18615| Initial helper/tests; independent review rejected raw payload/path copying, required Sol repair |
| Avicenna Sol/high |604.17|285301|3682048|27007| Four privacy/runtime findings plus explicit projection repair accepted in source/wheel tests and native PD4 |
| Bernoulli Sol/high |393.51|113850|3100160|17323| Offline v11 strict FAIL classification accepted; no paid calls or edits |
| Sagan Sol/high |234.00|117135|1354752|11306| Offline synthesis prompt contradiction and paged full-read false-negative established; no edits/tests/calls |

All four closed. Raw usage-p36-final.json under the ignored agent-efficiency-0500 evidence directory; cached input is a subset, not additional input. Matched comparative tasks are unavailable, so no claimed savings. Parent overhead not separately timed; nativev31 setup163.682s andv32 partial249.911s are explicit rework, v33 lifecycle1016.772s includes reporting/user/analysis waiting and is not active UI time. Corrected no-op typecheck and broad-read waste recorded in retro. Next Heisenberg Sol/high owns bounded SDK P34 repair while parent finishes Host native/documentation; parent remains sole test/API runner and main settings untouched.

## 2026-09-14 scoped child measurements

Local deduplicated response_id usage, not subscription billed credits. Reasoning is included in output. Wall time spans dispatch to last return including waits; parallel durations must not be summed.

| Child | Actual model/effort | Seconds | Uncached input | Cached input | Output | Acceptance/rework |
|---|---|---:|---:|---:|---:|---|
| Arendt | Sol/high |416.62|140804|3966976|20048|Action candidate; parent removed arbitrary8cap, fixed role filter and five fixture failures; successor pending|
| Gibbs | Sol/medium |54.96|74964|304640|2419|Found second worker-only context filter; parent fixed|
| Bohr | Luna/medium |110.40|56275|294144|4200|Found missing mkdir and resume opt-in validation; parent fixed,39PASS0.17s|
| Mill | Sol/high |129.66|105383|960256|6203|LC2 cap/recovery/identity scoped ACCEPT; later routing repair excluded|
| Pascal | Terra/medium |109.64|48195|300288|5317|Protocol diagnostic9PASS0.27s; parent ruff import fix; cumulative pending|

Ptolemy Astra/high remains repairing3 LC2 failures; live counters are not final. Parent overhead includes centralized tests, v12 error-layer correction, native launcher review and fixture rework; not separately timed. Luna found two concrete defects in a small review, but unmatched tasks do not establish any savings percentage. Main session remains Astra/high; application DeepSeek API is separate.

LC2 child final local measurement: Ptolemy actual[['gpt-6-astra', 'high']], wall1958.81s including waits, uncached input415410, cached input6062208, output45622. Parent first3LC2failures led to actual default-routing repair and two exact oracle expectation repairs; five additional routing evidence controls. Corrected focused72PASS13.07s, adjacent56PASS13.64s, Host45PASS0.52s. Native and final cumulative still pending; no-dispatch historical default is unprovable and not claimed preserved. Parent also fixed formatting and protocol metadata typing. No matched-task savings claim.


2026-09-14 原生v36后记：主模型Astra/high保持，按任务自编排，无plan-test skill。下表取本地去重response_id记录，不是订阅官方计费。墙钟含等待，各项不可相加；父任务独立协调时间未精确分离。

| 子代理 | 实际模型/推理 | 墙钟秒 | 非缓存输入 | 缓存输入 | 输出 | 接受/返工 |
|---|---|---:|---:|---:|---:|---|
| Meitner | Terra/high | 275.96 | 138929 | 1445888 | 10930 | UI117测试与原生v36通过；父审发现发送失败挂起后修复 |
| Cicero | Luna/medium | 117.56 | 74275 | 649728 | 5216 | 审批数量与普通请求byte基线建议采用，真实行为由父测v36 |
| Aquinas | Terra/medium | 128.76 | 60398 | 411904 | 6431 | 48AC清单仅作线索，部分状态/条件过期，父审修订；不作为完成数量 |
| Erdos | Sol/high | 305.76 | 121520 | 2472448 | 15079 | 旧库脚本采用，父任务补第二来源登记并亲跑旧SDK/新原生；不由子代理宣称PASS |

不同任务不可据此计算节省百分比。原始用量位于ignored agent-efficiency-0500/usage-native-v36.json。
