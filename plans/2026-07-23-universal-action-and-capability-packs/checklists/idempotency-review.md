# 通用行动与能力包平台 — 副作用与幂等性审查

> 审查日期：2026-07-24  
> 自动化状态：PASS  
> 真人状态：full-audit 执行中；真人矩阵不替代本表的持久化/崩溃测试

本表逐项回答“稳定身份、重复结果、冲突结果、崩溃恢复、覆盖测试”五问。

| 边界 | 稳定身份 / fence | 相同重放 | 冲突 / 未知结果 | 崩溃恢复与主要测试 | 自动化 |
|---|---|---|---|---|---:|
| 顶层 Run 创建 | trusted session + `client_request_id/client_turn_id` + root CAS | 返回已有 Run/投影，不重复追加用户消息 | 身份或 payload 漂移关闭失败 | `test_run_kernel.py` 的 durable create/terminal/recovery 组 | PASS |
| running-root 续聊 | `run_id + task_scope_id + message_ref`，conversation reservation 与 FIFO version 分离 | 返回原 queued/bound receipt | terminal/enqueue 由 Run version CAS 重仲裁；取消后拒绝/失败未绑定项 | `test_running_root_queues_continuation_and_supersedes_stale_terminal`、`test_terminal_enqueue_race_retries_fifo_after_run_version_fence`、`test_running_user_continuations_reserve_fifo_and_survive_restart` | PASS |
| 唯一 Driver owner | 每 Run 的 `LiveRun.task + start_lock + driver_lock` | 已有 running/done owner 不安装第二 owner | retiring/cancel/recover 竞态必须等待精确 owner；`CANCEL_REQUESTED` 不得 provider relaunch | `test_cancel_interrupts_active_driver_owner_before_driver_cancel`、`test_recover_cannot_install_owner_while_cancel_is_converging`、`test_recover_joins_retiring_owner_before_relaunch` | PASS |
| Provider action batch / Attempt | root TaskGoal + provider turn fence + Attempt ID + batch fingerprint | 已接受 batch 重放返回原 Attempt/outcomes | 不同 payload 拒绝；transport 只有真实稳定 key 才声明 exactly-once，否则 at-least-once + host fence | `test_provider_batch_crash_rolls_back_and_restart_replays_once`、Attempt/failure focused suite | PASS |
| FailureSet 与重规划 | `attempt_id + failure_set_id + ordered report identity` | 同一 report/failure set 不重复扩张 | 同源失败可在后续 Attempt 记录；相同策略/相同错误无变化重试被 loop guard 拒绝 | `test_failure_set_persists_all_ordered_reports_across_restart`、`test_same_failure_source_can_be_recorded_in_later_attempt`、`test_attempt_loop_guard.py` | PASS |
| Profile launch ticket / child | ticket ref + parent/root/task/Attempt/provider turn/call/profile/driver/generation/snapshot/grant/fingerprint + request fingerprint | consumed ticket 返回原 child | 不同 fingerprint/payload、stale generation 或取消 goal 拒绝 | `test_profile_ticket_unique_one_shot_and_crash_recovery`、`test_profile_ticket_recovery_cancels_only_cancelled_goal`、catalog contract tests | PASS |
| 普通 Effect claim/settle | stable call/effect ID + request fingerprint + UoW phase/version | settled 结果复用，不再执行物理副作用 | late/unknown 不盲重跑；signal/settle 失败不 ack registry | `test_runtime_keeps_mixed_batch_pending_when_one_physical_call_is_late`、`test_signal_or_atomic_settle_failure_never_acknowledges_registry`、EffectBatchExecutor suite | PASS |
| TaskGrant / Auto 授权 | task/resource/action category + policy generation + decision/grant fingerprint | 同范围复用 exact grant | policy generation 或资源范围漂移拒绝；Auto 只改变 actor/等待，不改变核验 | `test_auto_task_grant_decision_and_exact_grant_commit_atomically`、`test_policy_generation_change_rejects_stale_authorization_commit`、rollback fault test | PASS |
| external wait / UAC | task/root/Attempt + original provider call/effect + wait version/nonce | 重复“已处理”只复查同一 external wait | 等待期间无 terminal tool result、FailureSet 或新 Attempt；取消只终止该 scope | `test_external_wait_resumes_same_attempt_without_failure_fact`、`test_run_cancel_terminalizes_open_external_wait_scope` | PASS |
| 能力版本与 active binding | immutable `(capability_id, version)` + binding CAS + operation phase intent | 同版本安装返回原 version/binding | 哈希/manifest/权限失败零 version、零 binding；publish unknown 留 reconcile intent | `test_versions_are_immutable_and_binding_is_cas`、`test_operation_phase_intents_are_strict_and_idempotent`、`test_integrity_failure_never_creates_version_or_binding` | PASS |
| 能力安装/更新/回滚/卸载 | operation ID + source revision/hash + expected active version | 相同 install/update 幂等 | update 创建派生版本；切换失败保留旧 pointer；卸载只 unbind，版本可恢复 | `test_install_publishes_immutable_version_and_is_idempotent`、update/rollback、catalog-swap crash/reconcile、uninstall recovery tests | PASS |
| CapabilityBuilder 发布与立即使用 | search receipt/catalog stamp + builder child/ticket + staging nonce + generated revision + refresh lease | 已发布 revision/refresh receipt 复用 | 无 search evidence、已有足够匹配、unsafe/native worker、测试不全均拒绝 | `test_builder_admission_uses_current_search_and_managed_staging`、`test_builder_completion_is_closed_and_manager_owned`、`test_generated_pack_installs_and_rehydrates_after_restart` | PASS |
| 同 root catalog refresh | run + old/new catalog/schema/grant fingerprint + continuation lease + nonce | 已刷新 snapshot 返回原引用 | forged ref、parent/receipt drift 或 crash 全事务回滚；旧 in-flight brokered ref 可完成 | `test_same_run_refresh_swaps_snapshots_in_one_transaction`、`test_refresh_crash_rolls_back_continuation_lease_and_nonce`、staging/source fault tests | PASS |
| 能力 runtime/call lease | physical runtime session + run snapshot lease + call lease + owner epoch | 同 call 复用已 claim runtime | stop/drain 等待 call lease；事务失败回滚 runtime+call claim | `test_runtime_call_lease_pins_physical_session_and_blocks_stop`、`test_last_run_snapshot_release_drains_unbound_shared_runtime`、`test_runtime_and_call_claim_roll_back_with_caller_transaction` | PASS |
| Terminal / delivery / Artifact | Run terminal CAS + stable delivery/event/artifact identity | 已终态不重复 final、delivery 或 Artifact | 过期 terminal 在 queued continuation 前失败；delivery 失败可恢复，不反向改变 Run 事实 | `test_run_kernel.py` terminal race/close/recovery 组、workflow delivery suites | PASS |

## 审查结论

- 未发现“重复请求会重复安装、重复 child、重复 effect、重复 terminal 或重复 Artifact”的
  未封口自动化缺口。
- Provider 网络语义不会被夸大：没有稳定 provider idempotency key 时，对外只声明
  at-least-once；host-side turn/effect fences 仍阻止第二批副作用派发。
- 仍需真人确认的是 UI 投影和真实应用行为：Manual/Auto、取消按钮、三 root 窗口隔离、
  UAC external wait、Godot/Blender/Web、损坏包和生成能力的真实结果。任一 required
  真人行仍为 PENDING 时，总 DoD 仍为 FAIL。
