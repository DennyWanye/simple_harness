# HM-TO-A6 ContextSnapshot 审计结果

证据目录: `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-09/native-a6-run13/primary-ui-hwbyjnym`
窗口/预算: window=32000 (来源: native.log:model_context_resolved(deepseek-v4-flash)); tier=8192; generation_reserve=2048; safety_margin=3200; effective_input_budget=26752
native.log 三元组: preparing=21 / staged=21 / consumed=21; 前台 Run=21; 均衡=True
轮次: 记录到 T24/24; timeout 轮=无

| 项 | 判定 | 依据数字 | 说明 |
|---|---|---|---|
| A6-1 20+ turn 动态组装 | **INCONCLUSIVE** | foreground_run_heads=21; non_terminal_runs=0; receipt_runs=21; runs_with_ordinal_gap=0 | 终态 Run 仅 21 个, 少于 22 个非 UI 轮, 无法证明 20+ turn 组装。 |
| A6-2 大 tool result 分页 | **BLOCKED** | distinct_page_reference_ids=9; page_reference_samples=["primary-effect-page:v1:1ac06fa7ff44287fa3c45bf2fca4e9e3...; history_summaries_in_requests=14; messages_tagged_primary_tool_history_v1=14 | 全程未出现 >16 KiB 的 tool 结果, 也无 context_page_in 调用 —— T5/T6/T8 未成功读文件, 按 plan 第 4 节记 BLOCKED(环境/路由)而非 FAIL。 |
| A6-3 预算内有界 | **PASS** | attempt_rows=83; max_input_tokens=19898; first8_max_input_tokens=19546; last8_max_input_tokens=16553 | max_input_tokens=19898 < effective_input_budget=26752; provider 计费峰值 19898 亦在预算内(83 次调用, planned 低估 0 条); 后8/前8 轮调整后峰值比 0.847 ≤ 1.6(未调整的逐轮比 1.015, 旧的逐次尝试比 1.388); 观测到 32 次整组丢弃。 判据只统前台泳道; 本次证据里没有其它泳道的 provider 调用。 实测闸门 sdk_provider_wire_input_budget_exceeded 本次触发 1 次(耐久结算 1 次, 其中前台 1 次) —— 这些请求被拦在发出之前, 不计入上面的计费统计。 同 Run 有界化: pages_forced 合计 0(单 Run 峰值 0); control_results_stubbed 合计 0; control_stubs_forced 合计 0; control_result_tokens 峰值 2606; budget_headroom 最小 148; 请求内省略通知 0 次。 |
| A6-4 裁剪不破坏因果链 | **PASS** | requests_parsed=84; max_history_groups_in_one_request=6; groups_max_cap=10; requests_over_groups_max=0 | 84 次请求无孤立 tool 消息, 组数峰值 6 ≤ 10, 9 次整组消失(其中 2 次该请求回执确实裁过组)全部满足从头部丢组的后缀单调。 |
| A6-5 README/STATUS 超限拆分 | **INCONCLUSIVE** | view_revisions=12; readme_bounded_revisions=0; readme_bounded_max_bytes=0; readme_cap_bytes=16384 | README/STATUS 均未进入 bounded 形态 —— T17/T18 的超长 goal.set 未逐字落库, 按 plan 第 4 节记 INCONCLUSIVE(可用多条 decision.record 顶 PLAN 作备用杠杆重试)。 |
| A6-6 同一 plan 建节点 + relation memory | **PASS** | cognitive_relations_rows=2; applies_to_rows=1; relations_new_workflow_target_plus_relation_in_same_plan=1; relation_kinds=["applies_to", "contests"] | 1/2 条关系是 applies_to 知识边, 且其 **target 流程节点**(heads.memory_type=procedure)与 relation memory 由同一个 plan 新建(本 plan 新建的端点角色 [['target']]), 两端 revision 均可解析, relation_memory_id 在 heads 中存在; relation_kind=['applies_to', 'contests']。 |
| A6-7 edge 更新/纠正(supersede) | **PASS** | heads=9; memories_with_multiple_revisions=1; lifecycle_only_revisions_exempted=0; lifecycle_states_observed=["active"] | 1 条记忆出现新 revision, head 全部指向最新 revision 且其 lifecycle 合法(取值 ['active']), 每次**内容**前进都有 evolution 血缘边(kind=['contests']), 另有 0 个纯生命周期推进按 S3/S5c 契约豁免[]; 旧 revision 按 append-only 契约保持不可变快照, 不参与召回。 |
| A6-8 争议 contested | **INCONCLUSIVE** | cognitive_conflict_groups_rows=1; revisions_contested=1; t22_invocations=0; t22_asks_confirmation=False | 冲突组 1 个、contested revision 1 条已成立, 但 T22 未在 a6-progress.jsonl 中定位到 provider 调用, 后半段无法判定。 |
| A6-9 ordinary projection policy 过滤 | **PASS** | expected_nodes=8; expected_edges=1; heads=9; suppressed_direct=["522ddb859e4b"] | T24 人工观测 nodes=8 edges=1, 与 DB 复算一致; 被抑制 1 条(含同源副本 1 条)、redacted 0 条、expired 0 条、relation memory 1 条都不在图上; 可见边 kind=['contests'](争议期 contests 边按契约应当出现), 被过滤边 kind=['applies_to'](旧 revision 血缘边/端点不可见)。 |
| A6-10 relation/endpoint 遗忘 + close/reopen | **PASS** | cognitive_relations_final=2; cognitive_relations_peak_in_progress=2; suppression_directives=1; suppression_targets=1 | 关系行 2 未减少(峰值 2, append-only 成立); 遗忘使 1 个节点离图(直接目标 1 条 + 同源副本 1 条), T23 观测 nodes=8 edges=1 与复算一致; T24 关表重开观测逐字相同, 未复活。含 knowledge 关系行 1 条。 |
| A6-11 图谱不进入 Provider Context | **PASS** | invocations_scanned=84; needle_relation_ids=2; needle_memory_ids=9; hits_relation_id=0 | 84 次请求中 relation_id/relation_hash/twin_graph/graph_edge 命中数 = 0; T16/T24 的 provider_invocations 增量均为 0。 |
| A6-12 snapshot 重放指纹 | **PASS** | invocations=84; replay_match=84; replay_mismatch=0; replay_error=0 | 84 条 request_json 重放指纹 == request_fingerprint == receipt.expected_request_fingerprint, 且 payload_hash 与之恒等(84 条); 每条调用恰好一条同 Run 同指纹 receipt, 无中段孤儿; 另有 0 条「组装完但 Run 就此终止」的末条 receipt[](合法形态, 不计入不匹配)。 |
| NC-1 T3 简单改写不查长期库(no_recall) | **PASS** | route_decisions_in_t3=1; origins=["no_recall"] | T3 的 context_route_decisions 1 行全部为 no_recall(或无召回记录)。 |
| NC-2 T12 闲聊不建/切 TaskScope | **PASS** | route_decisions_in_t12=1; routes=["direct_standalone"] | T12 未出现 create_new(该轮路由 ['direct_standalone'])。 |
| NC-3 T14 模糊愿望不产生 Prospective | **PASS** | prospective_records=0; prospective_scheduler_registrations=0 | prospective_records 与 scheduler_registrations 均为 0, 未产生 pending Prospective。 |
| NC-4 T22 争议期不直接用旧值 3.12 | **INCONCLUSIVE** | t22_invocations=0; asks_confirmation=False; mentions_3_12=False; response_chars=0 | 未定位到 T22 的 provider 调用(未跑到该轮或计数快照缺失)。 |
| NC-5 T16/T24 图谱 UI 不新增 provider_invocations | **PASS** | provider_invocation_delta={"T16": 0, "T24": 0} | T16/T24 的 provider_invocations 增量均为 0({'T16': 0, 'T24': 0})。 |
| NC-6 request_json 无凭据形状 | **PASS** | invocations_scanned=84 | 84 次请求全文扫描, sk-/Bearer/ghp_/AKIA/PRIVATE KEY 命中数均为 0。 |

## 判定统计
PASS=13  FAIL=0  BLOCKED=1  INCONCLUSIVE=4

## 证据文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| human_memory_v7.db | `33c0bea7306976499c5ac8adf55dafff2026f40f6b8cd445df5d1e76a023a0e7` |
| state.db | `789db5ae2b0d5e2ad988ac0eeb1ae320b082ac408a03d6021bbedff5f752886c` |
| operation-audit.db | `e050468c3a5c86cf82e1ae0e93ddd7084855a93c62e33b6f0651a385ba2e4c69` |
| execution-v6.sqlite3 | `8f7d627cf362e61f98c2d7b34114c8c8d3723795f9f4bf306cf6f8c708160d0b` |
| native.log | `afe9ec795dda47b7b96c3f535d45704a709a72b3f281130f078f0f61212abadb` |
| a6-progress.jsonl | `55602745fe35dd1d59eed68c8deedc525dd1692731475a2dee915be8f7d08a6a` |

## Schema / 假设备注
- context window=32000 读自 native.log:model_context_resolved(deepseek-v4-flash); receipts 记录的 budget_tier=[8192]。
- sdk_context_public_snapshots 按 plan 第 0 节未被用作任何判定依据(该表在当前构建为空)。
- A6-11 默认区分「图谱结构性标识」与「memory_id 在 tool 回执中的回声」; 加 --strict-a6-11 可回到 plan 字面的一刀切命中判定。

JSON 已写入: plans/2026-09-08-hm-to-a6/RUN-13-a6-verify.json
