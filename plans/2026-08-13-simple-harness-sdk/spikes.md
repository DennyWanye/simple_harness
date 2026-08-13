# 关键假设 spike 记录

> 日期：2026-08-13  
> 规则：spike 源码位于 `/tmp/simple-harness-sdk-spike.3dAvxv`，不进入产品或计划提交；以下只保存命令、脱敏输出和结论。任何本地 Provider key、完整响应、原始日志均未写入本文件。

## H1 / H5 — package mechanism、import-audit harness、macOS ARM64 Python 3.11

可丢弃包使用 `src/simple_harness/`、Hatchling、唯一 runtime dependency `httpx`。第一次运行发现
bundled Python 没安装 `build`，因此构建命令统一改为会创建隔离 build env 的 `uv build`。

第一次双构建还发现 sdist 默认收进仓库内 `.venv-check`，绝对 Python symlink 导致：

```text
Invalid tar file ... virtual environments must be excluded from source distributions
```

修正为显式 `[tool.hatch.build.targets.sdist] include/exclude` 后：

```text
$ SOURCE_DATE_EPOCH=0 uv build --out-dir build-c
Successfully built simple_harness_sdk_spike-0.0.0.tar.gz
Successfully built simple_harness_sdk_spike-0.0.0-py3-none-any.whl

$ SOURCE_DATE_EPOCH=0 uv build --out-dir build-d
Successfully built simple_harness_sdk_spike-0.0.0.tar.gz
Successfully built simple_harness_sdk_spike-0.0.0-py3-none-any.whl

wheel A/B sha256 = 36751e41fffad7d3eeee31cf6c748468b3bd2caf5366b94e5f859da6d8f66963
sdist A/B sha256 = 413316dc0d3af2bd0bc3eaf16645e648ce8c7ef3c6b918abf9e9d2bda0c54a4a
```

同一 wheel 安装到 clean macOS ARM64 Python 3.11：

```text
$ uv venv .venv-py311 --python .../cpython-3.11-macos-aarch64-none/bin/python3.11
$ uv pip install --python .venv-py311/bin/python build-c/*.whl
$ .venv-py311/bin/python audit_import.py
IMPORT_PURITY_PASS
PLATFORM Darwin arm64 3.11.15
```

Audit 断言 import 前后环境、cwd files、threads 不变，且未加载 FastAPI/Torch/Playwright/DeskPet。

**结论**：H1 与 H5 成立；选定 build/reproducibility/import-audit 机制以及 macOS ARM64/Python
3.11 exact-wheel install gate可行。本 spike 不声称真实 Core 已解耦；真实 Core 见 H6。Linux ARM64
和 Windows x64 是 Slice 7 必须真跑并 PASS 的原生 acceptance gate，不能由本机或交叉构建替代。

## H2 — 当前 durable 原子/unknown oracle

运行：

```text
$ backend/.venv/bin/python -m pytest -q \
  test_run_kernel.py::test_kernel_decision_boundary_rolls_back_and_retries_after_restart \
  test_run_kernel.py::test_atomic_driver_commits_before_kernel_returns_handle \
  test_workflow_outbox_tx.py \
  test_provider_dispatch.py::{post_handoff_cancel,missing_ack,post_handoff_error} \
  test_wi2_tool_executor.py::test_prepared_receipt_and_artifact_refs_are_exposed_for_atomic_settlement
.............. [100%]
14 passed in 1.61s
```

**结论**：H2 成立。现有代码提供可迁移的 atomic decision/start、delivery CAS/idempotency、Provider
post-handoff unknown 和 Tool settlement oracle；schema v1 必须复制这些行为，不复制混合 schema。

## H3 — 同一主模型选择 bounded Personal candidate

从当前产品 registry 读取一个 enabled Provider 和 keychain secret（只检查存在，不打印），使用其
当前模型，temperature=0。给两个 trusted descriptor：

- `weekly_work_planner`：项目优先级与周复盘；
- `fitness_training_coach`：运动与恢复。

用户请求“安排下周项目优先级和周五复盘”，强制唯一 `workflow_spawn` control schema，连续三次：

```text
LIVE_DESCRIPTOR_PASS runs=3 choices=weekly_work_planner,weekly_work_planner,weekly_work_planner
LIVE_TOOL_USAGE usage_present=true
RESPONSE_HASHES e8cbb71eb84a7a7a,79a81dcd0593232f,d3cf23dc13d60a7d
```

每次均只有一个 structured Tool call，参数 key 精确为 `profile_key/candidate_id/catalog_generation`，
candidate 与 generation 合法；hash 只覆盖脱敏 tool-call/model/stop-reason shape。

**结论**：H3 在明确区分的 bounded catalog 上成立，无需恢复 Personal 前置 matcher。实现仍需对
相近候选、无匹配候选、伪造字段、stale generation 做 conformance 与必要 clarification loop。

## H4 — OpenAI-compatible structured call、usage、at-most-once

同一 live probe 同时证明当前 relay 支持 required structured tool calling 且三次 usage 非空。
受控 transport/dispatch oracle：

```text
$ backend/.venv/bin/python -m pytest -q \
  test_openai_compatible.py::{catalog_stale,at_most_once_http_error,usage} \
  test_provider_dispatch.py::{claim_before_transport,transport_once,coordinated_cancel}
...... [100%]
6 passed in 0.12s
```

**结论**：H4 成立。SDK HTTP Adapter 可以保持一次调用职责；durable claim/handoff/outcome/unknown
继续属于 execution coordinator。当前 Adapter 的 DeskPet logging/metrics/context 不能原样复制。

## H6 — 真实 Core import RED 与 fake-host 行为 GREEN

不是用空壳代替真实 Core：对当前八个真实模块（Kernel、Tool executor、WorkflowRunner、ReAct/
Workflow Driver、OpenAI Adapter、AgentLoop、product profiles）安装 write/network/thread guard 后导入：

```text
REAL_CORE_IMPORT_PASS modules=8
IMPORT_SIDE_EFFECTS writes=0 network=0 threads=0
TRACKED_RUNTIME_IMPORTS aiosqlite,httpx,pydantic,structlog,yaml
```

但 import 触发了 `deskpet.tools` auto-discovery，并记录两个 circular-import skip：

```text
orchestration_controls -> harness.profiles -> partially initialized harness.ports
skill_tools -> companion.skills -> capabilities.run_catalog -> partially initialized harness.contracts
```

这不是目标 PASS，而是可复现的 **coupling RED**：当前真实 Core 不能原样成为纯净 SDK，具体根因是
package `__init__` auto-discovery 和 product dependency cycle。目标任务 T1–T5 必须在每个切片的 clean
wheel import gate中消除它，而不是把 auto-discovery log隐藏掉。

同一当前代码的 fixed-root、Workflow Driver、product Profiles、model workflow_spawn 与 Personal
fake-host行为：

```text
$ backend/.venv/bin/python -m pytest -q \
  test_general_agent_root.py test_workflow_driver.py test_product_workflow_profiles.py \
  test_model_workflow_spawn.py test_personal_workflow.py
............................................................... [100%]
63 passed in 2.67s
```

**结论**：H6 成立。真实当前行为有 63 项可冻结 GREEN oracle，同时真实 import coupling 有明确 RED
信号；依赖倒置后用同一 audit + oracle判断是否真正提取成功。由于本 skill 不实现业务代码，目标
Core 的最终 GREEN 必须作为每个实现 Slice 的出口，不能用最小 package spike冒充。

## H7 — 真实 full-runtime seam matrix

针对“单组件 GREEN 不能证明 Ports 能接起来”的风险，运行当前真实 integration seams：

```text
$ backend/.venv/bin/python -m pytest -q \
  test_wi5_react_driver.py::test_capability_snapshot_filters_agent_loop_schema_and_prepare \
  test_run_kernel.py::test_kernel_executes_tool_command_and_resumes_same_driver \
  test_run_kernel.py::test_composed_agent_loop_child_fault_binds_identity_and_parent_recovers \
  test_run_kernel.py::test_terminal_child_commit_wakes_parent_signal_reconciliation \
  test_workflow_delivery_pipeline.py
............ [100%]
12 passed in 1.04s
```

覆盖的真实 seams 是：Provider产生 Tool call并经 capability filter/prepare；Kernel消费 Tool command、
Effect executor落账并 signal同一 ReAct Driver；真实 AgentLoop/Provider coordinator进入 attached child
并把 failure/identity交回 parent；child terminal原子唤醒 parent signal；root/workflow delivery pipeline
幂等投递。

**结论**：H7 成立。未来五类 Port 本身尚不存在，不能在 plan-bs 中假装目标实现已经 GREEN；正确
门禁是 T3.0 先把这 12 个 source assertions 变成 SDK `test_full_runtime_seam.py` 的预期 RED，再由
T3.1–T3.3 转 GREEN。Adapter不得为了过测试调用旧 authority。

## H8 — Provider target 与价格表的 pre-dispatch 绑定

T2.4 执行中发现 T1.2 Provider Port 没有声明实际 provider/model identity；若由 Host 自报 estimator
model，只能在响应后发现 mismatch，已经无法满足调用前 hard-cap。

- 可丢弃代码：`/tmp/sdk-provider-target-spike.uWGsdP/spike.py`，SHA-256
  `19a03956366542384136ae0fe0a35b641e378ec75ee58f81dc0eda9ee07ae5a8`。
- 命令：`backend/.venv/bin/python /tmp/sdk-provider-target-spike.uWGsdP/spike.py`。
- 实际输出：`PROVIDER_TARGET_SPIKE_PASS calls_before_mismatch=0 calls_after_match=1`。
- 结论：采用 immutable `ProviderTarget(provider_id, model, pricing_key)`；Adapter 的物理出站 model
  与公开 target 同源，冻结 estimator 精确绑定 target。Coordinator 在 claim/handoff 前比较，
  mismatch 时稳定拒绝且 transport 调用数为 0；禁止 Host/request metadata 覆盖。

第一轮 challenger 进一步指出，内存比较没有覆盖跨重启 identity，且 immutable 不能防恶意 Host
同时伪造自定义 Provider 与 estimator。补充 spike：

- 可丢弃代码：`/tmp/sdk-provider-restart-spike.UaaYDL/spike.py`，SHA-256
  `34dd1085bf2fe66ad31f22a61334a3a8e69243ed6d1ac68e237dcc7df98fe410`。
- 命令：`backend/.venv/bin/python /tmp/sdk-provider-restart-spike.UaaYDL/spike.py`。
- 实际输出：`PROVIDER_RESTART_BINDING_SPIKE_PASS calls=0 old=ee1342d4 new=10b54ac7`。
- 修订结论：完整 target 加 `endpoint_identity/adapter_key`，并与 estimator snapshot 一起进入 durable
  invocation identity/CAS；reopen 后换 target/价格表必须零调用拒绝。官方 Adapter 从真实构造字段
  生成 target；自定义 Provider 的诚实性属于可信 Host composition 边界，不宣称防恶意宿主。

第二轮 challenger 发现 configuration digest 不能进入 invocation ID，否则换配置会形成第二个调用。
补充并发 SQLite spike：

- 可丢弃代码：`/tmp/sdk-provider-logical-call-spike.XrOpjN/spike.py`，SHA-256
  `2932ad0223ab2f80ed17c6012e2198436cd7a1bb335d3ec5261d289c059f6636`。
- 实际输出：`LOGICAL_CALL_UNIQUENESS_SPIKE_PASS rows=1 results=created,mismatch second_transport_calls=0`。
- 最终模型：`run_id + request_id` 是数据库唯一 logical call；request/target/estimator fingerprints
  是该行不可变 CAS 属性。配置变化只能 mismatch，不能产生新的 invocation identity。

## 关键假设收口

| 假设 | 状态 | 剩余门 |
|---|---|---|
| H1 | PASS（build/audit机制） | 真 SDK dependency graph 形成后重跑 import/forbidden-dependency gate |
| H2 | PASS | schema v1 对八组原子命令逐写点 fault injection |
| H3 | PASS（明确候选） | 相近/无匹配/恶意候选矩阵 |
| H4 | PASS | SDK 重写 Adapter 后同一 live + controlled conformance |
| H5 | PASS | Linux ARM64、Windows x64 仍必须由原生 release runners 执行 acceptance |
| H6 | PASS（当前行为 GREEN / coupling RED 均已复现） | 每个 extraction Slice 重跑 clean-wheel import 与 frozen behavior oracle |
| H7 | PASS（12-case integrated source oracle） | T3.0 建 SDK RED，T3.3 必须用新五 Port 转 GREEN |
| H8 | PASS（首次、重启及并发异配置均在 transport 前拒绝） | T1.2 official target 同源 + T2.4 unique logical call 与 durable config CAS 回归 |

## H9 — child signal durable claim authority（执行期静态闭包审计）

- 触发：T3.2 开工时核对 clean schema v1，发现 `child_signals` 虽有
  `pending/claimed/acked`，但没有 `claimed_by/claimed_at/claim_expires_at`，T2.3 也没有
  `claim_child_signal` CAS；仅凭状态或内存 owner 无法覆盖 claim 后崩溃/reopen。
- 结论：在未发布 schema v1 内补完整 durable signal claim lease；持久化 owner/timestamps/
  独立 `claim_epoch`，每次 first claim/reclaim 原子递增 epoch，ack 同时 CAS owner+epoch
  并保存可验证 receipt 身份。每个 parent 只能 claim oldest non-acked head；head 未过期时
  不得跳到后续 signal。T3.2 只消费该 UoW authority。
- 性质：这是 plan/schema closure 缺口，不是历史 Run migration；SDK 尚未发布，因此修正 initial
  schema v1，不新增兼容 migration。
- 狭挑战迭代 1：`FAIL`，2 个 critical（未冻结 durable claim epoch/ack receipt；未冻结
  FIFO head eligibility）。上述契约与回归矩阵已补入 T2.3/T3.2，待第二轮独立挑战。
- 狭挑战迭代 2：`FAIL`，1 个 critical（ack 与 parent progress 未冻结为单一原子
  command）。已修订为 `ack_child_signal_and_commit_parent_progress`，在同一
  `BEGIN IMMEDIATE` 内完成 signal CAS + continuation/event/payload + unique receipt，并补逐
  write-point crash/reopen matrix。
- 狭挑战迭代 3：`PASS`，`NEW_CRITICAL_FINDINGS=0`。
- 实现复审迭代 4：发现草案只对 effect row 的历史 `fence_epoch` 做 CAS，未在
  handoff transaction 查当前 `run_fences`；且 Executor 构造时 owner 可与 per-call runtime
  owner 不一致。已冻结为同 transaction 同时校验当前 Runtime lease + 当前 active
  RunFence row，两 lease 必须同 run/同 owner，Executor 用 per-call owner acquire。
- 实现复审迭代 5：发现 `claim_runtime_activation` 与无条件 `RunFencePort.acquire`
  分两个 transaction，旧 owner 可在中间过期/新 owner 接管后恢复，覆盖新 RunFence
  造成活性故障。已冻结 `RunFencePort.acquire(run_id, execution_lease)` 在同 transaction
  先校验 active Runtime lease 再处理 fence，Kernel/Tool 均传 per-call lease。同 active owner/lease
  epoch acquire 幂等返回同一 run-level fence，不得每 Tool call 递增导致 Kernel terminal
  fence 失效；只有新 runtime owner/epoch 接管才递增。执行性复审又补充 exact
  `now` 参数与 `run_fences.runtime_lease_epoch`，避免真实时钟/虚拟时钟分裂，
  并区分同 owner ID 的 runtime epoch+1 接管。待第五轮挑战。
- 实现复审迭代 6：发现即使 Tool acquire 幂等返回 Kernel fence，现有 Executor
  `finally release()` 仍会使 terminal fence 失效。修订为 Kernel 唯一 acquire/release run-level
  fence，`DriverInvocation.run_fence` 与 execution lease 逐层传到 EffectExecutor；Tool 只验证/
  消费，不 acquire/release。第三方窄挑战又要求 handoff 精确匹配
  `run_fences.runtime_lease_epoch == execution_lease.epoch == run_fence.runtime_lease_epoch`，
  阻止同 owner epoch+1 接管但尚未重取 fence 时拼接旧 fence。待第六轮挑战。
- 实现复审迭代 7：第三方挑战发现同一“新 runtime lease 已 claim、新 fence
  尚未 acquire”窗口还可被旧 owner 用旧 current fence 提交 terminal。已明确
  `commit_root_terminal_with_deliveries(..., run_fence, execution_lease, now)` 在同一
  transaction 校验 active runtime lease + current RunFence + runtime epoch 三方等式后才写
  terminal/event/outbox，并补异 owner/同 owner epoch+1 两类窗口的零写回归。
- 最终窄挑战：`PASS`，`NEW_CRITICAL_FINDINGS=0`。

## H11 — Runtime owner lease heartbeat（执行期静态闭包审计）

- 触发：T3.1 首版只在 `_activate` 获取有界 `workflow_leases` row，长 Driver 运行期间
  无 renew。TTL 过期后第二 Runtime 可接管，而旧 Driver 仍可继续物理 Provider/Tool；
  只在最后 Context/terminal CAS 拒绝已经太晚。
- 结论：增加 owner+epoch CAS `renew_runtime_lease`，Runtime 在严格小于 TTL 的间隔
  heartbeat。renew 失败立即 cancel 旧 Driver/未 handoff effect；所有 side-effect/context/
  terminal 命令均消费当前 active lease fence，Provider/Tool handoff 在同 UoW transaction
  校验 fence。失租前已 handed_off 的原调用只允许完成/reconcile，新 owner 不重放。
  close 按 cancel Driver/未 handoff -> heartbeat 保持期间 join Driver -> 停并 join heartbeat ->
  释放 lease 顺序，防止慢取消期间形成无租双 owner。
  虚拟 clock 覆盖长运行续租、新 epoch 接管、旧 owner 零新出站/写入和 close 无泄漏。
- 性质：这是 durable single-owner Kernel 的 safety closure，不是历史 Run 兼容。
- 狭挑战迭代 1：`FAIL`，2 个 critical（Provider/Tool handoff 的 lease-fenced API/SQL 责任
  未落文件；non-cooperative call 下 bounded close/隔离语义未冻结）。已指定 T2.4/T2.5
  Port 签名、同 transaction 的 SQLite active-lease + ledger CAS、Tool 双 fence、post-handoff settle
  例外、竞态测试和 non-cooperative bounded-close 分支。
- 狭挑战迭代 2：`FAIL`，1 个 critical（只改底层 UoW 签名，未冻结从
  `DriverInvocation` 经 ReAct 到 Coordinator/Executor 的 per-call lease 传递）。已将
  `ExecutionLease` 列为上层 public invoke/execute 到两个 handoff UoW 的逐层必填参数，
  禁止共享可变 state/闭包/Host pre-check，并补漏传/错 run/旧 epoch 零物理调用回归，
  待第三轮挑战。
- 狭挑战迭代 3：`PASS`，`NEW_CRITICAL_FINDINGS=0`。

## H10 — ReAct termination durable checkpoint（执行期静态闭包审计）

- 触发：T3.3 的 standalone ReAct 实现将 `turns/tool_calls/repeat/started_at` 只保存在
  进程内；T2.4 cost 可恢复，其他四个 hard gate 在重启后归零，且 turn RequestId
  可重用。T3.1 原 `ContextPort.load/append` 也没有 revision/append receipt，普通 context
  message 不能代替 termination authority。
- 结论：复用 clean schema v1 的 `workflow_checkpoints` + `workflow_leases`，用
  `react.termination.v1` namespace 持久化计数、wall epoch、phase 和当前 side-effect identity。
  所有转移须校验 owner/lease_epoch/version；在物理出站前 reserve，恢复时只用稳定
  ID 读取/reconcile durable ledger。Context append 增加 durable revision + append receipt，保证
  provider/tool 结果重放写入幂等。跨重启 wall budget 用 Unix epoch；clock 倒退时 fail
  closed，不用跨进程不可比的 monotonic origin。详细 phase/CAS/fault matrix 已补入
  T3.1/T3.3。
- 狭挑战迭代 1 补充发现：`ContextPort.append` 若只有 revision CAS，旧 owner 会在
  lease epoch 接管后竞速写入。修订为 append 必填 current `ExecutionLease`，并在同一
  SQLite transaction 校验 active owner+epoch+expiry；回归覆盖 stale owner 携带尚新 revision 仍拒绝。
- 狭挑战迭代 2：`FAIL`，1 个 critical（未持久化 repeat key，且 batch 可能只消耗
  一次 Tool budget）。已冻结 `tool_calls_reserved_total + repeat_key + repeat_streak`，
  repeat key 为 tool name + canonical args SHA-256；按 batch call order 全量模拟后一次 CAS，
  任一超限则零 effect prepare。
- 狭挑战迭代 3：`PASS`，`NEW_CRITICAL_FINDINGS=0`。
- 性质：这是未发布 SDK schema v1 的 runtime closure，不是历史 Run 兼容处理。

## H12 — H7 阶段门与 child-to-parent public orchestration closure（执行期审计）

- 触发：T3.0 v4 把源项目 H7 的 12 个 assertions 恢复后，真实 RED 分成两种 owner：4 个
  Provider/ReAct/Tool/restart/child 闭环依赖 T3；8 个 strict/legacy terminal projection 依赖尚未创建的
  `simple_harness.workflow`。原计划只写“T3.3 前 12 GREEN”，但 T4 仅有 T4.1/T4.2，既没有
  `workflow/native.py` / `workflow/errors.py` owner，也没有办法把精确预期的 8 RED 当成绿色阶段门。
- 阶段门结论：不删、不 skip、不 xfail 任何 assertion。注册两个 strict pytest markers，增加
  fail-closed verifier：collect 必须精确 4+8=12；T3 结束时 4 runtime GREEN 且 8 workflow 逐 nodeid
  只能因缺 T4.3 frozen `workflow.native.NativeWorkflowExecutable` / `workflow.errors.InvalidStatePatch`
  authority RED（允许 package 尚不存在或已由 T4.1 创建，但不允许其他 error）；新增 T4.3 实现
  strict/legacy terminal 后 4、8、全 12
  都必须 GREEN。第一轮窄挑战：`FAIL`，2 个 critical（无真实 T4 owner；无可执行 expected-RED
  verifier）；已按上述方案补 owner/脚本/精确阶段语义，待复审。
- public seam 结论：v4 还暴露 T3.2 只有低层 UoW/receiver、没有 H7 所需 public orchestration。
  Runtime 必须提供 typed child launch + schedule、public reconcile、public delivery dispatch；
  `DriverInvocation` 必须携 FIFO claimed continuation。现 continuation schema 没有 expiry/claim epoch，
  会在 crash 后永久 claimed，且查询 pending 会越过旧 head；因此 clean schema v1 必须增加绑定
  Runtime lease epoch 的 durable claim lease/独立 claim epoch/HOL eligibility/progress receipt。
  continuation ack 与 parent WAITING/terminal
  progress（含 terminal outbox）必须同 transaction，否则 crash 会造成重复 parent progress 或永久
  丢 wakeup。新增 continuation-aware fault/reopen matrix；不得让 conformance 访问 `_ports` 或
  `_activate/_schedule` 伪造闭环。
- 第二轮窄挑战：`FAIL`，3 个 critical（未接 Kernel child terminal -> parent signal producer；ack
  第一条后第二条 continuation 缺自动 drain liveness；runtime 阶段错误原因硬编码整包缺失会被 T4.1
  合法并行进度打破）。已补 attached/supervised child 三种 terminal 的原子 stable signal、detached
  不 signal；WAITING ack 后立即重查/expiry wake、terminal 残余 quarantine；verifier 改为只接受
  T4.3 两个 pending public authorities，待第三轮复审。
- 第三轮窄挑战：`FAIL`，3 个 critical（child terminal producer 缺 Runtime/Run 双 fence与durable
  attachment policy；continuation claim 独立 TTL 会和持续 renew 的 Runtime lease冲突；exact receipt
  replay若先验已释放 lease就无法处理 after-commit 响应丢失）。已修订为 producer/detached terminal
  同事务读取 run_link 并校验双 fence；continuation claim 不设第二套 TTL、只绑定 active
  ExecutionLease；atomic progress 先只读 exact receipt，未命中才校验 active authorities并写入，待
  第四轮复审。
- 第四轮窄挑战：`FAIL`，2 个 critical（child terminal 没有同事务 fence receipt/release 与
  receipt-first after-commit replay；Driver 异常但 heartbeat 仍 active 时会留下 orphan continuation
  claim）。已补三种 child attachment terminal 的 receipt-first、terminal/signal/fence receipt/release
  单事务；claimed-continuation Driver 正常/exception/cancel/non-cooperative 所有出口必须 atomic ack
  或隔离 task 后 release Runtime/Run authority，使新 epoch 无需等待第二套 TTL 即可 reclaim，待第五轮。
- 第五轮窄挑战：`PASS`，`NEW_CRITICAL_FINDINGS=0`。
- 性质：这是未发布 SDK 的真实 public seam/任务 owner closure，不是历史 Run 兼容。
