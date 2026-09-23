# H1-H stage matrix：Operation Completion 后的补充映射建议（2026-09-22）

本文件是静态审计建议，不是执行记录。权威判据仍为
`h1h_admission_amendment/AMENDMENT.zh-CN.md` §8；基线是 runner 最近一次
`20 PASS / 8 PARTIAL / 8 NOT_COVERED`。只有直接调用同一生产 producer、compiler、
commit guard，并断言该编号全部关键不变量的 nodeid 才能升为 `MATCH`。
Operation Completion 的 T0/T1/file-publish/T3 成功链证明正式 Operation 完成协议，
但不能替代 H1-H 对错误 receipt、负证明、集合竞争、规划授权或 preview 的反例。

## 可立即更新的三项

| ID | 建议 | 精确 nodeid | 直接证据 |
|---|---|---|---|
| I06 | `PARTIAL → MATCH` | `tests/orchestrator/full_target/test_h1h_commit_interleaving.py::test_i06_concurrent_authority_or_action_commit_makes_plan_commit_stale[authority]`；`...[handoff]` | 两个 SQLite writer 真实竞争；授权续期或真实 handoff 先提交后，计划提交分别以 `REQUEST_BINDING_STALE` / `OPERATION_SNAPSHOT_STALE` 拒绝；只留一份既有 plan commit，计划事务不添 action/outbox/event。 |
| O10 | `NOT_COVERED → MATCH` | `tests/orchestrator/full_target/test_h1h_retired_running_work.py::test_o10_cancelled_task_active_attempt_is_not_filtered_and_blocks_real_commit[live-foreign-lease]`；`...[expired-lease]` | cancelled Task 的外来 RUNNING attempt 在活/过期 lease 两态均进入生产 `RuntimeWorkSnapshot`，正式 commit 被阻断；Task/attempt/lease 不被释放、完成或抢占，零 revision/写入。 |
| P02/P03 | 两项均 `NOT_COVERED → PARTIAL` | `tests/orchestrator/full_target/test_h1h_preview_compiler_refusal.py::test_p02_p03_real_compiler_refusal_keeps_typed_reason[order-cycle-_cycle_network-ORDER_CYCLE]`；`...[unbound-port-_missing_required_port_network-DATA_UNBOUND]`（实际参数化 nodeid 以 pytest collect 输出为准） | 使用真实 production `PreviewInputs` 与 compiler，保留 `ORDER_CYCLE`、`DATA_UNBOUND` typed reason。P02 尚缺 refinement cycle 和既有 plan/账本/文件零变化；P03 尚缺多绑、覆盖缺失、资源冲突。runner 应映射函数级 nodeid，避免手写参数化 suffix 漂移。 |

`test_h1h_preview_compiler_refusal.py::test_real_collector_persists_the_compiler_bound_code`
可作为 P02/P03 的 supporting nodeid：它证明真实 collector 持久化 compiler code，且未知 code
fail closed；它本身没有制造 ORDER/refinement/DATA 四类输入，不能单独计 `MATCH`。

完成上述映射后，建议总数为 **22 MATCH / 9 PARTIAL / 5 NOT_COVERED**；这仍是
静态覆盖建议，不是测试结果。

## 仍为 PARTIAL 的九项

| ID | 可保留/新增 supporting nodeid | 仍缺的精确断言 |
|---|---|---|
| O02 | 现有 `test_h1h_operation_admission.py::test_missing_bridge_is_source_unavailable_instead_of_latest_join`；Operation intent source 测试只证明新协议精确 source，不能替代 mapper 双 action 情形 | 同 target 的两个真实 action/Task，具有不同 params hash/occurrence；显式证明 alias 不覆盖原 link。 |
| O04 | 现有 `test_h1h_operation_matrix.py::test_o04_store_identity_or_link_fault_is_source_unavailable` + `test_h1h_operation_tenant.py` | 必须从带 caller tenant 的生产 H1 mapper/commit 门读取错误 tenant 并拒绝且不泄漏。`operation_completion/test_operation_workspace_projection.py::test_foreign_tenant_cannot_reach_operation_workspace_through_the_facade` 是 workspace projection 边界，不能计本项。 |
| O08 | 现有 `test_h1h_commit_guard.py::test_o08_new_action_after_preview_is_detected_by_complete_set_reread` | preview 后新 handoff（同一 action 状态/集合版本变化）被 commit 完整集合复读抓住；不得仅靠“新增 action”。I06 handoff 是并发提交语义，不能代替此 preview 快照分支。 |
| P04 | 现有 unknown/unregistered premise；新增 supporting：`test_h1h_preview_compiler_refusal.py::test_p04_real_projection_bound_refusal_keeps_planning_bound_code`、`::test_real_collector_persists_the_compiler_bound_code` | registry 缠失、evidence 缺失、validator 缺失、`PARTIAL_CHECK` 四类分别拒绝；断言 projection/refinement report 未被空对象或自填 PASS 替代。planning bound 只是 typed-code 通路。 |
| P06 | 现有 `test_planning_decision_admission.py::test_running_work_on_the_retired_instance_is_refused` | 非法 repair 时真实兄弟 work/action/lease 完全不变；随后合法 replacement 才进入收敛，不能借 O10 的 cancelled Task fixture替代 repair 前后序列。 |
| P07 | 现有 adapter nodeid | 同一合法 replacement 在 `DEFERRED` 后关闭/reopen Store，经生产 resume 完成；raw decision/decision id 不变，LLM 调用数和 plan commit 数均不增加。 |
| I07 | 现有 legacy creation/package golden/new-table-empty nodeids | 创建旧 Mission 后关闭并冷 reopen，再跑实际旧入口；逐项比较 Prompt、package canonical bytes、event sequence 与预算口径。 |
| P02/P03 | 见上节 | 补齐后才能各自升 `MATCH`。 |

## 仍为 NOT_COVERED 的五项

| ID | 不应误算的现有测试 | 需要新增的精确 nodeid/断言 |
|---|---|---|
| A06 | T0/T3 completion approval、UP-Aries commit/revoke、Action approval 各自只覆盖相邻授权域 | 新建 `test_h1h_authority_action_separation.py::test_a06_planning_grant_and_external_action_approval_are_independent`：`required_approvals=0` 与 method gate=true 仍不能替代 planning grant；合法 planning grant 可 preview/commit，但未审批 external action 不能 handoff；批准后仅 action 门改变。 |
| O03 | O10 测的是 cancelled Task 的 active attempt，不是退役 method 的 UNKNOWN action | 新建 `test_h1h_operation_matrix.py::test_o03_retired_method_unknown_action_remains_in_complete_snapshot_and_blocks_commit`：action 所属 instance 退出 active plan 后仍在全量 mapper；正式 commit 零 revision/outbox。 |
| O07 | `operation_completion/test_operation_t0_t3_runtime.py::test_operation_t0_t3_runs_real_file_publish_and_commits_formal_completion` 只证明真实成功与 T3 replay，未断言 H1 mapper 四态 | 新建参数化 `test_h1h_operation_matrix.py::test_o07_receipt_and_negative_proof_map_to_exact_effect_state`：匹配 success=`APPLIED`；错 key/hash/operation receipt=`SOURCE_UNAVAILABLE`；完整 authoritative negative proof=`NOT_APPLIED`；缺 no-late-apply/覆盖不全=`UNRESOLVED`，并断言不以字符串状态代替证据。 |
| O09 | materialization、intent replay、handoff guard 分属不同局部测试；尚无 producer+link 同事务故障窗 | 新建 `test_h1h_operation_atomic_link.py::test_o09_propose_action_and_exact_link_rollback_and_replay_atomically`：在 action insert 与 link insert 间注入异常，两者皆无；相同 command 重送恰一 action/link；并发 handoff 在事务提交前读不到 action。 |
| I08 | 当前 no-NanoJev 范围已移出 Shadow；任何 legacy 或 Operation Completion 测试都不是 clean-env 三 producer 独立性 | 按 V1.4 去 NanoJev 口径新建 clean-env 集成 nodeid：同一 H1 fixture 真实调用 authorization、operation、shape 三 producer 和原 Commit；模块/注册表中无 NanoJev/Shadow 依赖；最终 receipt 来自本地 commit guard。若权威补遗仍要求“Shadow 有/无结果”，应先修订 §8 I08 文本，不能靠测试改写需求。 |

## Operation Completion 新证据的正确归属

- `operation_completion/test_operation_t0_t3_runtime.py::test_operation_t0_t3_runs_real_file_publish_and_commits_formal_completion`：可作为 O07 的成功态 fixture 来源，但当前断言止于真实 publish、正式 outcome acceptance、Task 完成和幂等 replay，尚未读取 H1 `OperationSnapshot` 并断言 `APPLIED`。
- `operation_completion/test_completion_spec_approval.py`：证明 requirements CAS、issuer/tenant、不可替换 spec 与 migration integrity；对应 Operation Completion 补遗，不自动覆盖 A06/O02/O04/O09。
- `operation_completion/test_operation_workspace_projection.py`：证明 Host/SDK workspace tenant 与只读 projection；不经过 H1 operation mapper，因此不能补 O04。
- D3 successor/supersedes 与 materialized 禁止替换测试约束 intent 生命周期；除非测试继续贯通 action+exact link 的同事务失败窗，否则不能补 O09。

## 缺口分类

**已确认代码未实现：** authoritative negative proof 的消费字段已经存在，但生产源码只读取
`reconciliation_proof.authoritative_not_applied/all_handoffs_covered/no_late_apply_proven`，没有生产
writer 写入该完整证明。因此 O07 的完整负证明态当前无法由 registered verifier 合法产生；测试不得
手改 action JSON 冒充该证明。

**尚缺验收证据，不能据此推断代码不存在：** A06 同一 Mission 的 planning grant/action approval
双向独立链、O03、O09、P02 refinement-cycle、P03 多绑/覆盖缺失/资源冲突、P04 四类不完整检查、
P06 合法前后收敛、P07 durable resume、O08 preview 后 handoff、I07 冷恢复，以及去 NanoJev 后
重新裁定的 I08。应先写直接 nodeid；只有红灯明确落在缺失生产接口时，才能升级为功能缺口。
