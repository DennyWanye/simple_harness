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
