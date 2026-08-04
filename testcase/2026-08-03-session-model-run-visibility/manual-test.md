# Session 模型一致性与 Agent 运行可见性 — 真机测试

> 状态：FROZEN / EXECUTED PASS（2026-08-03；执行结果以 verification gate 账本为准）
> 对应验收：AC-SRV-1～AC-SRV-8、S-SRV-1～S-SRV-5、V-SRV-1～V-SRV-8
> 对应计划：`plans/2026-08-03-session-model-run-visibility/plan.md`

## 测试目的

确认同一 Session 的模型选择、附属 LLM、Context Usage 和运行视图共用已冻结事实；同时确认
长任务只显示少量语义阶段，工具详情紧凑且脱敏，子任务失败后的接管、停止和晚到结果不会把
根任务或其他 Session 的状态显示错。

## 统一前置与证据纪律

1. 只启动一个 Tauri 实例；由 Tauri 自行启动唯一 backend 与 Vite。日志必须显示
   `Dev python=... backend_dir=F:\projects\deskpet\backend`，不能出现 bundled backend。
2. 使用当前已提交 HEAD；记录 HEAD、启动日志、`/health`、Vite HTTP、窗口和 resident worker。
3. 每个 UI 动作前记录 `坐标 / 动作 / 期望`，动作后立即截图；中文通过剪贴板粘贴输入。
4. 日志和 SQLite 只作辅助对账，不得用 WebSocket 直注或脚本回放代替 UI 提交。
5. 负向场景仅在 `DESKPET_DEV_MODE=1` 加载 `ProviderFaultScriptV1`；每次使用单独规则文件和
   user-data 目录，结束后确认没有 active injection，再精确清理规则文件和对应进程树。
6. 任一场景修复后，除本场景外至少复测一个未受影响的场景。

## S-SRV-1 — Kimi 冷路径与 Context Usage

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 精确停止旧实例；使用空的隔离 `DESKPET_USER_DATA_DIR` 启动当前代码并完成首次登录。 | 登录成功；后台、Vite、窗口与 resident worker 健康；隔离目录内没有旧 Session/usage sample。 |
| 2 | 新建 Session；打开模型选择器，在过滤输入框键入 `kimi`，从过滤结果选择可用的 Kimi 模型，并记录 Session id、provider incarnation/revision、binding epoch。 | 只显示匹配项；选择 ack 与 UI authoritative state 一致，不跳回 GLM。 |
| 3 | 发送任何消息之前展开 Context Usage。 | 显示 `binding-only/尚无 measured sample`，provider/model 为刚选择的 Kimi；不得显示 GLM 或伪造 token 数。 |
| 4 | UI 输入“用一句话告诉我你现在在用哪个模型。”并发送，等待新 Root terminal。 | Root 使用 Kimi 并 completed；回答非空；消息区出现自然语言 Agent output；usage 切换为 measured sample。 |
| 5 | 记录 `execution_provider_invocations` 与 workload audit 的 invocation id、workload class、callsite、provider/model、session/root/correlation、binding epoch 和 config revision。 | main 与 session-auxiliary 都带本 Session 身份和 Kimi binding，system-maintenance 若存在则必须是 detached/null identity。 |
| 6 | 关闭并重启同一隔离实例，重新打开 Session。 | Kimi binding、binding epoch 和 measured usage sample/lineage 均恢复，不跳回 GLM。 |

判定：AC-SRV-1/3/4 全部满足才 PASS。首次登录、只重启旧暖环境不能替代本场景。

## S-SRV-2 — ≥10 轮历史、确定触发压缩的多步骤项目任务

固定使用 `F:\projects\deskpet-e2e-scratch\session-model-run-visibility`。启动前仅在该目录创建：
`calculator.py`（`add(a,b)` 错误地返回 `a-b`）、`test_calculator.py`（断言 `add(2,1)==3`）和
`README.md`（说明运行 `pytest -q`）。隔离 user-data 的 `models.toml` 把 `kimi-k3` 的
`context_window` 固定为 `32000`，UI 将压缩阈值设为 `0.50`；这只是确定性测试配置，不修改真实项目。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 新建 Kimi Session；连续完成 10 个完整 `user → assistant` 对。第 N 条用户消息粘贴固定的 `history-N:` 加 4,000 个“历史校验N”字符，并要求“只回复收到N”；逐条等待 assistant 完成后再发下一条。 | 记录 Session id、20 条消息 id、Kimi binding；第 10 对结束前必须出现真实 `context_compacted`，并记录 compaction id、被替换范围、before/after token 和 sample lineage；未触发即 FAIL，不得写“条件未满足”。 |
| 2 | 输入验收冻结原句“检查这个项目，分步骤修复明显问题，运行测试后告诉我每一步做了什么。” | 创建新 Root，模型保持 Kimi；必须出现项目目录选择卡片，选择固定 scratch 目录后才允许继续，卡片不能跨 Session 常驻。 |
| 3 | Root 仍在 running 时把全局默认模型改为 GLM，但不改此 Session binding；继续观察运行图与消息流至少三次并对账后续 provider invocation。 | Root 的冻结 provider snapshot 和修改后的后续 invocation 仍为 Kimi；只出现实际阶段；当前阶段单向推进，不按每条 provider/tool 生成重卡片。 |
| 4 | 展开当前阶段，再展开一个工具输入和工具结果。 | 工具位于所属阶段；折叠行紧凑；输入脱敏；结果默认折叠且展开后有界，无 raw prepared/outcome。 |
| 5 | 若 Root 进入 waiting，只在该等待边界回答一次继续；不得另发普通“继续”制造新 Root。继续等待同一 Root completed；最终仍 waiting/failed/cancelled 即 FAIL。 | 默认视图严格为 6–8 个可理解阶段；同 stable ID 不重复；等待边界 continuation 保持同一 root；最终 `calculator.py` 已修正、`pytest -q` 通过，结果说明实际步骤；Context Usage 与步骤 1 的 compaction/sample lineage 可对账。 |

判定：文件夹卡片、真实 compaction、6–8 阶段、工具展开、修复后测试通过缺一即 FAIL；同时保存执行中、工具展开和终态截图，并对账完整 manifest/cursor/total。

## S-SRV-3 — 子任务失败后主 Agent 接管

固定使用只读 scratch 项目 `F:\projects\deskpet-e2e-scratch\session-model-run-visibility-s3`。先在不加载
故障脚本的实例中完成登录并新建 Session，记录 `<SESSION_ID>`，随后精确停止该实例；再创建只含以下
规则的 JSON，并通过 `DESKPET_PROVIDER_FAULT_SCRIPT` 重启同一隔离 user-data：

```json
{"schema_version":1,"rules":[{"rule_id":"s3-child-main","injection_ref":"s3-child-main-ref","session_id":"<SESSION_ID>","workload_class":"main","callsite_id":"agent.root_turn","purpose":"agent_response","occurrence":1,"action":"child_provider_failure","run_kind":"child"}]}
```

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | DEV 实例加载上述 consume-once 规则并重新打开已记录 Session。 | 规则加载成功但尚未消费；root main 不命中，只有真实 `parent_run_id != null` 的 child main 可消费。 |
| 2 | UI 输入验收冻结原句“用多步骤任务检查项目；如果子任务失败，你继续接管并完成，不要把整个任务直接判失败。”并通过文件夹卡片选择 S3 scratch。 | 新 Root 创建；120 秒内必须出现 `child accepted`，超时即 FAIL。规则首次命中后绑定 `bound_correlation_id=<child_run_id>`，`bound_root_id=NULL`，且物理 provider transport 未执行。 |
| 3 | 等待子任务失败并观察主 Agent 后续动作。 | child 保留 failed；故障 audit 的 session/root/parent/profile/purpose 与冻结 child 身份一致；出现 FailureReport/failure set/replacement Attempt，主 Agent 继续而非提前终止。 |
| 4 | 展开“验证与修复”阶段和失败 child。 | 能看到失败 child、后续接管步骤与工具；失败事实不被隐藏，也不重复。 |
| 5 | 等待根终态。 | 顶部显示“子任务失败，主 Agent 已接管并完成”；aggregate 为 completed_with_recovery。 |

判定：启动顺序、120 秒 child accepted、child correlation、五段因果证据缺一不可；仅有 `child failed + root completed` 不能 PASS。

## S-SRV-4 — 后台 401/402 与主任务隔离

401 retry run 使用：

```json
{"schema_version":1,"rules":[{"rule_id":"s4-maintenance-401","injection_ref":"s4-maintenance-401-ref","session_id":null,"workload_class":"system-maintenance","callsite_id":"provider.maintenance_probe","purpose":"auxiliary_unknown","occurrence":1,"action":"http_401","autorun":true}]}
```

402 retry run 使用独立 user-data 和规则文件，只把 `401` 改为 `402`，并增加
`"model_quota":false`。规则由 `ProviderFaultScenarioRunner` 启动真实 detached workload；不能通过
直接调用 matcher 或手写 audit row 代替。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 先不加载故障脚本启动隔离实例，完成登录、provider 配置和 Kimi Session 创建后精确停止；再用同一 user-data 加载单次 401 autorun 规则重启。 | 规则严格匹配 `session_id=null/root_run_id=null` 的 maintenance workload，不命中任何 Session main；配置/登录问题不能误记为故障注入结果。 |
| 2 | UI 输入“继续完成当前任务。” | 主 Root 正常创建并由 Kimi 推进。 |
| 3 | 观察后台错误、breaker audit 和根终态。 | 后台稳定降级；同一失效域无重试风暴；主 Root 不因后台 401 失败。 |
| 4 | 对 402 使用另一个独立 user-data：同样先无脚本完成登录/provider/Kimi Session，停止后再加载 402 autorun 规则重启，重复步骤 2～3。 | 402 按 provider 账户域隔离；`model_quota=false`；主 Root 仍只由自身结果结算。 |
| 5 | 检查规则消费与 audit。 | 每条规则只消费一次；audit 的 session/root 均为 NULL，`injection_correlation_hash` 对应 injection correlation；测试后没有 active injection。 |

判定：401 和 402 是两个 retry run，不增加 distinct scenario 数；两者都需负向日志证据。

## S-SRV-5 — 两 Session 并发、停止与晚到事件

先在不加载闩锁的隔离实例中完成登录，创建 Kimi Session A/B 并记录 ID，然后精确停止实例。创建
`F:\projects\deskpet-e2e-scratch\session-model-run-visibility-s5\probe.txt`，内容固定为 `late-read-v1`，
并创建 `README.md`，内容固定为五行 `step-1` 到 `step-5`。
再创建下面的闩锁规则（`<SESSION_A_ID>` 替换为实际 ID），通过
`DESKPET_TOOL_COMPLETION_LATCH_SCRIPT` 重启同一 user-data。`armed_file/release_file` 必须是规则文件
同目录的相对文件名，启动前两者都不存在：

```json
{"schema_version":1,"rules":[{"rule_id":"s5-late-read","injection_ref":"s5-late-read-ref","session_id":"<SESSION_A_ID>","tool_name":"file_read","occurrence":1,"armed_file":"s5-late-read.armed.json","release_file":"s5-late-read.release","timeout_seconds":300}]}
```

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | Session A 输入“只读取 probe.txt 并逐字告诉我内容，不要调用其他工具”，通过文件夹卡片选择 S5 scratch；Session B 输入“分 5 步只读检查 README.md，每步汇报进度”，也通过 B 自己的文件夹卡片选择同一 scratch。记录两个 root id。 | 两个 root 不同且分别绑定各自 Session；A 的首个 `file_read` 精确命中闩锁，B 持续推进；各自运行图和模型状态不串线。 |
| 2 | 最多等待 120 秒让 `s5-late-read.armed.json` 出现；超时即 FAIL。读取 marker 的 session/run/effect/tool/outcome state，并与 `execution_effects`、最新 attempt 的 run/effect/handoff ack 对账；随后只在 Session A 点击停止。 | marker 与 durable effect 完全一致，真实 handler outcome 已产生但尚未交回 Agent；停止前 durable 列为 `status=running,handoff_state=started`，停止后为 `status=unknown,handoff_state=started_may_complete,completion_disposition=inflight_effect_may_complete`；A 终止，B 继续。 |
| 3 | 确认 A 已 terminal 后创建 `s5-late-read.release`，等待 Reconciler。 | 同一 effect 的 durable 列只迁移一次到 `status=late_reconciled,handoff_state=reconciled,completion_disposition=reconciled_completed_suppressed`；late outcome hash 可审计但不得回送 Driver。 |
| 4 | 切换 A/B 并展开各自阶段。 | A 无 running 阶段；B 的阶段、工具和终态不包含 A 的 stable ID。 |
| 5 | 重启应用后重新打开 A/B，并对账 registry 已忘记该 effect。 | A 仍 cancelled、无新 assistant/tool-success；B 按自身终态恢复；同一 late effect 不重复对账；完整 terminal 后停止轮询。 |

判定：预创建 Session、120 秒 armed 门禁、marker/durable 双向绑定、两个独立 root、真实 UI 停止、晚到隔离与重启截图缺一即 FAIL。

## 自动化回归入口

```powershell
$env:PYTHONPATH='backend'
.\backend\.venv\Scripts\python.exe backend\scripts\smoke_session_model_run_visibility.py
.\backend\.venv\Scripts\python.exe backend\scripts\verify_real_harness_public_fixture.py
.\backend\.venv\Scripts\python.exe -m pytest -q `
  backend\tests\test_session_provider_authority_v23.py::test_root_snapshot_route_is_immutable_after_session_change `
  backend\tests\test_context_usage_state_v2.py::test_provider_binding_and_public_context_state_advance_atomically `
  backend\tests\test_context_usage_state_v2.py::test_provider_compaction_provider_reducer_and_restart `
  backend\tests\test_provider_workloads.py::test_cold_failure_is_single_flight_across_twenty_callers `
  backend\tests\test_provider_workloads.py::test_quota_cooldown_half_open_and_user_reset_use_test_clock `
  backend\tests\test_provider_workloads.py::test_failure_policy_scopes_match_failure_authority `
  backend\tests\test_provider_fault_script.py::test_maintenance_scenario_runner_is_owned_and_audits_null_identity `
  backend\tests\companion\test_provider_dispatch.py::test_child_main_fault_settles_claim_before_transport `
  backend\tests\harness_simplification\test_run_kernel.py::test_child_provider_fault_persists_failure_and_parent_recovers `
  backend\tests\harness_simplification\test_run_kernel.py::test_composed_agent_loop_child_fault_binds_identity_and_parent_recovers `
  backend\tests\harness_simplification\test_run_kernel.py::test_runtime_reuses_settled_effect_without_live_registry_policy `
  backend\tests\harness_simplification\test_wi2_tool_executor.py::test_cancel_after_started_dispatch_tracks_shielded_completion `
  backend\tests\harness_simplification\test_wi2_tool_executor.py::test_cancel_marks_started_effect_inflight_may_complete `
  backend\tests\harness_simplification\test_wi2_tool_executor.py::test_dev_tool_completion_latch_holds_only_completed_read_outcome `
  backend\tests\harness_simplification\test_run_kernel.py::test_cancelled_terminal_run_quarantines_late_effect_without_driver_resume `
  backend\tests\harness_simplification\test_run_kernel.py::test_real_latch_cancel_release_reconciles_without_driver_resume `
  backend\tests\test_generate_harness_public_fixture.py `
  backend\tests\harness_simplification\test_semantic_run_projection.py `
  backend\tests\test_harness_public_read_service.py
```

运行 fixture 集成项前按
`plans/2026-08-03-session-model-run-visibility/verification/real-fixture-source.json` 显式设置
`DESKPET_FIXTURE_WORKFLOW_DB`、`DESKPET_FIXTURE_STATE_DB`、`DESKPET_FIXTURE_SESSION_ID`、
`DESKPET_FIXTURE_ROOT_RUN_ID`。专用 verifier 会只读快照并迁移临时副本；缺 env、源不存在或不一致均
非零退出，因此不能以 pytest SKIP 通过门禁。
预期：所有测试 PASS、零 SKIP；真实派生 fixture 为 366 facts / 6 phases / 29 unique logical tools /
23 shell / completed_with_recovery / projection_complete=true，且 secret scan 为零。

## 结果表（执行后回写）

| 场景 | driver | root run | engine 终态 | 业务终态 | 证据 | 状态 |
|---|---|---|---|---|---|---|
| S-SRV-1 | AI + Windows UI | `32f9e2fe01f5568d9ca56ef0b17b4ada` | completed | completed | `slice-a-final5.../s-srv-1-binding-only-before-message.png`、`s-srv-1-kimi-measured-terminal.png`、`s-srv-1-restart-recovered.png` + `s-srv-1-ledger.json` | PASS |
| S-SRV-2 | AI + Windows UI | `04999765753a5342aa9f7b4619b0fd38` | completed | completed | `slice-b-final5.../s-srv-2-long-session-graph.jpg` + `s-srv-2-ledger.json` + `s-srv-2-fixture-verification.json` | PASS |
| S-SRV-3 | AI + Windows UI | `573cf15ecffb561493b2590bcb785368` | completed | completed_with_recovery | `slice-b-final5.../s-srv-3-child-failure-parent-recovery.jpg` + `s-srv-3-formal-child-fault-ledger.json` | PASS |
| S-SRV-4-401 | AI + Windows UI | `e6acd3abbe85519ca1f6736329bb3aa3` | completed | completed | `slice-a-final5.../s-srv-4-401-independent-user-data.png` + `s-srv-4-fault-audit.json`（`s4-final-maintenance-401-ref`） | PASS |
| S-SRV-4-402 | AI + Windows UI | `11dac2d13ae954ddae8c3cde9b3658cf` | completed | completed | `slice-a-final5.../s-srv-4-402-independent-user-data.png` + `s-srv-4-fault-audit.json`（`s4-final-maintenance-402-ref`） | PASS |
| S-SRV-5-A | AI + Windows UI | `9789eee4324a57129c020b522cd9a33d` | cancelled | cancelled | `s-srv-5-formal-latch-ledger.json` + `s-srv-5-a-restart-cancelled.jpg`；effect `e9870e...585c5`，late outcome `3bfc5f...35967` | PASS |
| S-SRV-5-B | AI + Windows UI | `6300620adab9532fb14d5efe5e6834e1` | completed | completed | 同一 formal ledger + `s-srv-5-b-restart-completed.jpg` | PASS |

## 验证账本（执行后回写）

| 验证节点 | 精确证据节点 / 场景 | run / invocation / effect / audit 标识 | 计数与关键断言 | 状态 |
|---|---|---|---|---|
| V-SRV-1 | `test_duplicate_and_out_of_order_replay_is_byte_stable` + `test_late_early_fact_adds_evidence_without_rolling_back_current_phase` | `automated-regression-final.json` | 乱序保留事实、阶段不回退、byte stable | PASS |
| V-SRV-2 | 上述 duplicate replay + `test_runtime_reuses_settled_effect_without_live_registry_policy` + `harnessPublicSnapshotStore`: `does not notify ... twice` + `test_kernel_decision_rejects_wrong_stale_and_duplicate_without_advancing` | `automated-regression-final.json` + S-SRV-5 formal ledger | 重复 provider tool call 复用 durable effect、物理副作用不重执行；重复 event/decision 不重复展示或推进 | PASS |
| V-SRV-3 | `test_missing_provider_identity_fields_fail_closed_without_parent_guessing` + `test_child_main_fault_matches_only_actual_child_run` + `test_proposal_provider_call_forwards_frozen_child_identity` + `harnessPublicSnapshotStore`: `drops raw data while adapting an unknown schema` | 66 个 child-provider/production-wiring 定向回归 + S-SRV-3 formal ledger | model/purpose/parent/schema 分别缺失时 fail closed，不猜模型、不串父子、raw 不泄露 | PASS |
| V-SRV-4 | `test_tool_projector_redacts_and_bounds_declared_fields` + `HarnessInspectorPanel`: `never renders raw secrets from an old schema` | `automated-regression-final.json` | 超长/敏感输入结果有界脱敏 | PASS |
| V-SRV-5 | `test_toolless_text_completion_has_no_fabricated_tool_phase` + S-SRV-1 | `automated-regression-final.json` + S-SRV-1 UI/ledger | 模型不调用工具时，文本 Root completed，所有阶段 tool_refs 为空 | PASS |
| V-SRV-6 | `test_root_outcome_truth_table` + `test_recovery_requires_complete_causal_chain` + `test_child_failure_abandon_and_request_user_preserve_child_terminal` + S-SRV-3 | `automated-regression-final.json` + S-SRV-3 formal ledger | 接管/放弃/请求用户分别为 recovered/failed/waiting，child failed 终态不变 | PASS |
| V-SRV-7 | `test_manifest_rebuilds_1500_facts_and_106_archive_rows_without_leaks` + `test_manifest_details_cursor_has_no_duplicates_and_is_owner_fenced` | real fixture verifier：2 passed；366 facts / 6 phases / 29 tools / 23 shell | total/cursor/truncated 完整；补齐前不声称完整 | PASS |
| V-SRV-8 | `test_registry_cas_and_remove_readd_incarnation_fail_closed` + `test_binding_only_stale_and_old_epoch_late_sample` | `automated-regression-final.json` | stale provider/binding 明确 fail closed，未绑定链不变 | PASS |
