# Code-level implementation tasks

> 本文件是 [`plan.md`](plan.md) 的可执行 task ledger。每个 Task 只能在其 listed tests PASS 且证据写入 gate receipt 后完成。`$SDK_REPO` 固定为独立 checkout `simple-harness-sdk`；`$PRODUCT_REPO` 为本仓库。

## S0 — repository / provenance / oracle

### T0.1 — 建立独立 package 与 immutable build [AC-1, AC-8]

- 文件：`$SDK_REPO/pyproject.toml`, `uv.lock`, `src/simple_harness/__init__.py`,
  `.github/workflows/{ci,release}.yml`。
- 改法：Hatch `src` layout；`requires-python >=3.11`；显式 wheel packages 与 sdist
  include/exclude；`__init__.__all__` 只导出 version/public factories。release job 只接受 `v*` tag，
  build job生成一次 artifact，test/publish jobs下载同一 artifact，不再 build。
- tests：`tests/artifact/test_build_contents.py`, `test_import_purity.py`,
  `test_reproducible_build.py`；命令 `SOURCE_DATE_EPOCH=0 uv build` 两次 hash 相同。
- 依赖：无。

### T0.2 — 逐文件 Apache provenance gate [AC-1, AC-8]

- 文件：`$PRODUCT_REPO/.../source-manifest.tsv`, `$SDK_REPO/REUSE.toml`, `LICENSES/Apache-2.0.txt`,
  `NOTICE`, `scripts/check_source_provenance.py`。
- 改法：对 [`cutover-manifest.md`](cutover-manifest.md) 每个 source blob 写 commit/blob hash、git
  authors、copyright、third-party/generated、license verdict、target；script 拒绝空字段与
  `approved` 以外状态。rewrite 文件仍记录行为来源但不得复制未批准文本。
- tests：`tests/artifact/test_provenance_complete.py` + `reuse lint`。
- 依赖：T0.1。

### T0.3 — 冻结/迁移 black-box oracle [AC-5..8]

- 文件：现有 `testcase-lock.json`, `behavior-contract.md`；新增
  `$PRODUCT_REPO/scripts/acceptance/verify_sdk_oracles.py` 和 `behavior-changes/BC-SDK-IMPORTS.json`。
- 改法：BC 文件精确定义 old production imports/symbols -> SDK public imports/symbols；允许机械
  import/fixture migration与删除只验证 retired router/ticketless behavior 的用例，禁止改变其余
  expected terminal/event/effect/delivery assertions。脚本验证“旧 hash 或 approved transform +
  assertion AST hash”二选一；不再要求已删除模块的测试 bytes 永久不变。
- tests：对一个未批准 expected 改写做 self-test，必须报 `FROZEN_ORACLE_CHANGED`。
- 依赖：`BC-SDK-IMPORTS` 已由 SR-8 显式批准；T0.1。

### T0.4 — 生成并冻结完整 symbol disposition [AC-5, AC-8]

- 文件：现有 `cutover-symbols.json`, `cutover-manifest.md`；新增
  `$PRODUCT_REPO/scripts/acceptance/build_sdk_symbol_disposition.py`、`source-symbol-disposition.json`。
- 改法：先验证 30 个 source SHA 与 184-symbol inventory hash；AST枚举每个 public top-level symbol，
  强制人工赋唯一处置 `sdk_public/sdk_private/product_adapter/product_owned/retire` 和 target symbol；
  任何 source drift、新 symbol、重复/未分类项 fail。review 后冻结完整 disposition hash，T1–T6 每次
  source变化必须更新 mapping而不能只更新 hash。最终 gate还扫描 forbidden owner definitions。
- tests：generator golden/self tests覆盖 source drift、unclassified、duplicate target、forbidden survivor。
- 依赖：T0.2；必须在 T1.1 前 PASS，不能推迟到 cutover。

## S1 — contracts / Provider / Tool

### T1.1 — 冻结 JSON、identity、message/event/error public API [AC-1, AC-2]

- 文件：`$SDK_REPO/src/simple_harness/contracts/{json,identity,messages,events,errors}.py`,
  `__init__.py`, `docs/api/contracts.md`。
- 改法：实现 frozen/slots dataclasses；所有 ID 用 distinct wrapper；recursive JSON validator 拒绝
  NaN/bytes/arbitrary object；stable error含 `code/public_message/retryable`，private cause 不序列化。
- tests：`tests/unit/contracts/test_{json,identity,messages,events,errors}.py`、public API snapshot。
- 依赖：T0.4。

### T1.2 — Provider Port 与一次调用 HTTP Adapter [AC-2, AC-3]

- 文件：`providers/{base,openai_compatible,errors,redaction}.py`。
- 签名：`Provider.invoke(request: ProviderRequest, *, cancel: CancelToken) -> ProviderResponse`；
  `OpenAICompatibleProvider(client, base_url, model, secret, timeout)`。
- 改法：从当前 Adapter只复刻 protocol oracle；移除 Agent/context/metrics/retry/fallback；解析
  structured tool calls与 usage；401/402/429/5xx/timeout/cancel 分 typed error；异常 `repr` 先 redaction。
- tests：`tests/conformance/test_provider_contract.py`, `tests/integration/test_openai_mock_server.py`,
  `test_secret_canary.py`；live probe 复跑 H4。
- 依赖：T1.1。

### T1.3 — Tool Registry / Authorization / reconciliation contracts [AC-2, AC-4]

- 文件：`tools/{contracts,registry,authorization,reconciliation,errors}.py`。
- 签名：`Tool.invoke(arguments, context)->ToolResult`；`AuthorizationPort.authorize(prepared)`；
  `ToolReconciliationPort.observe(effect)->ReconciliationObservation`。
- 改法：schema在 handler 前校验 additionalProperties/type/enum/length/reserved fields；五态 outcome；
  observation 仅三态 `confirmed_not_started/completed/still_unknown` 并绑定 evidence ref。
- tests：`tests/conformance/test_tool_contract.py`, `test_authorization_contract.py`,
  `test_reconciliation_contract.py`；malformed/duplicate/late/cancel/secret cases。
- 依赖：T1.1。

## S2 — execution schema / UoW

### T2.1 — 定义 schema v1 与 migration owner [AC-2, AC-5, AC-7]

- 文件：`execution/sqlite/{schema.py,migrations/0001_initial.sql,database.py}`。
- 改法：只创建 cutover manifest 所列 execution tables；`Database.open(path)` 显式，启用 FK、
  configurable WAL，`close()` flush；migration table记录 SDK schema，不读取 DeskPet schema 1–29。
- tests：`tests/integration/execution/test_schema_v1.py`, `test_open_close.py`, `test_integrity.py`。
- 依赖：T1.1。

### T2.2 — root start/admission/decision/continuation 原子命令 [AC-5]

- 文件：`execution/uow.py`, `execution/sqlite/uow.py`。
- 函数：`create_with_start_snapshot`, `start_admission`, `resolve_admission`, `commit_decision`,
  `enqueue/claim/ack_continuation`；一个函数一个 `BEGIN IMMEDIATE` transaction。
- tests：`test_atomic_root_start.py`, `test_atomic_decision.py`, `test_continuation_fifo.py`；每个 SQL write
  point before/after fault + reopen。
- 依赖：T2.1。

### T2.3 — ticket/child/signal 原子命令 [AC-5, AC-6]

- 文件：同 T2.2 + `execution/contracts/children.py`。
- 函数：`claim_profile_launch_and_commit_child`, `finalize_child_and_enqueue_parent_signal`,
  `ack_child_signal`；移除 ticketless command public method。
- tests：`test_atomic_child_launch.py`, `test_atomic_child_terminal_signal.py`,
  `test_ticket_generation.py`；duplicate/stale/reused ticket。
- 依赖：T2.2。

### T2.4 — Provider invocation ledger 与 budget authority [AC-3, AC-5]

- 文件：`execution/{provider_invocations,dispatch,budget}.py`, SQLite UoW methods。
- 状态：`claimed -> handed_off -> succeeded|failed|unknown`；只有同 invocation CAS settle；usage
  absence -> estimator upper-bound or `unknown`，policy不得累计 0。
- tests：`test_provider_dispatch_atomic.py`, `test_provider_unknown.py`, `test_budget_recovery.py`；移植
  current `test_provider_dispatch.py` oracle。
- 依赖：T2.2、T1.2。

### T2.5 — Effect ledger / fence / reconciliation [AC-4, AC-5]

- 文件：`execution/{effects,fences}.py`, `tools/executor.py`, SQLite UoW methods。
- 状态：prepared/handoff/outcome/unknown；stable call/effect id；reopen 调 T1.3 reconciliation Port；
  `still_unknown` 禁止 dispatch。
- tests：`test_effect_atomic.py`, `test_effect_reconcile.py`, `test_fence_epoch.py`；移植
  `test_wi2_tool_executor.py` oracle。
- 依赖：T2.2、T1.3。

### T2.6 — generic terminal delivery outbox [AC-5]

- 文件：`execution/delivery.py`, SQLite UoW methods。
- 函数：`commit_root_terminal_with_deliveries`, `claim/complete/release_delivery`；root terminal、outbox、
  terminal fence receipt 同 transaction；dispatcher sink失败只重试 delivery，不重开 Run。
- tests：`test_terminal_delivery_atomic.py`, `test_delivery_dispatcher.py`；移植
  `test_workflow_outbox_tx.py` 全部行为断言。
- 依赖：T2.2。

## S3 — Kernel / ReAct

### T3.0 — 冻结真实 Provider/ReAct/Tool/child/delivery seam [AC-5, AC-8]

- 文件：把 `spikes.md` H7 的 current integration cases复制为 SDK RED conformance skeleton：
  `tests/conformance/test_full_runtime_seam.py`；只替换 imports/factories，不改 assertion semantics。
- 改法：一个 fixture要求五个显式 Ports：Provider、Tool、Authorization、Context、Delivery；执行
  `Provider -> AgentLoopCollaborator -> ReActDriver -> Kernel -> EffectBatchExecutor -> same Driver`，
  另一个执行 attached child terminal -> parent signal -> root terminal -> delivery。开始 T3.1 时测试因
  SDK runtime未实现而 RED；T3.3 完成前必须 GREEN，不能用单组件 tests代替。
- tests：current source 12-case seam matrix必须先保持 GREEN；SDK `test_full_runtime_seam.py` 的 RED
  reason只能是 missing planned public symbol，不能是 fixture/import错误。
- 依赖：T2.6、T1.2–T1.3；是 T3.1/T3.3 的前置 gate。

### T3.1 — Kernel lifecycle closure [AC-5, AC-6]

- 文件：`runtime/{kernel,terminal,admission,context,start_snapshot,live_index}.py`。
- API：`build_runtime(uow, profiles, drivers, ports, root_profile_key="agent.general")` 不接受 classifier；
  `Runtime.start/close`、`RunClient.start/query/signal/cancel`。
- 改法：移植 atomic start/activation、single owner/recovery lease、terminal lifecycle；root key const
  校验；`tool_catalog_stale` 走一次 permanent terminal。
- tests：`test_kernel_start.py`, `test_fixed_root.py`, `test_start_recovery.py`,
  `test_catalog_stale_terminal.py`。
- 依赖：T3.0、T2.2、T2.6。

### T3.2 — child/continuation/reconciler closure [AC-5]

- 文件：`runtime/{child_runs,child_signal_runtime,user_continuations,reconciler,runtime}.py`。
- 改法：child 入口只接 `ProfileLaunchTicketRef`；恢复 lease/epoch；parent signal/continuation FIFO；
  startup reconciliation顺序 provider -> effects -> child signals -> deliveries -> recoverable Run。
- tests：`test_root_two_children.py`, `test_child_restart.py`, `test_continuation_restart.py`,
  `test_startup_reconcile_order.py`。
- 依赖：T2.3–T2.6、T3.1。

### T3.3 — ReAct Driver / termination budgets [AC-3..6]

- 文件：`runtime/drivers/{react,react_loop}.py`, `runtime/{context,termination}.py`。
- 改法：把现 `AgentLoop` state machine改用 Context/Provider/Tool/Authorization/Trace Ports；Provider
  每 turn 只经 T2.4 coordinator；Tool只经 T2.5；max turns/calls/wall/cost/repeat hard gates写代码。
- tests：`test_react_no_tool.py`, `test_react_tool_roundtrip.py`, `test_react_budgets.py`,
  `test_react_cancel.py`, `test_react_crash.py`。
- 依赖：T1.2–T1.3、T2.4–T2.5、T3.1。

### T3.4 — SDK runtime conformance surface [AC-4, AC-5, AC-8]

- 文件：`testing/{models,fakes,provider,tool,runtime}.py`, `testing/__init__.py`。
- API：`ConformanceHost(provider_factory, tool_factory, runtime_factory, paths_factory)`；
  `run_conformance(host, suites)->ConformanceReport`，protocol=`simple-harness-conformance.v1`。
- tests：SDK自身 fake host 执行 no-tool/one-tool/multi-tool/cancel/restart/duplicate/unknown；JSON report
  schema snapshot。
- 依赖：T3.2–T3.3。

## S4 — Workflow runtime / selection

### T4.1 — Native compiler/runner/checkpoint ports [AC-5, AC-7]

- 文件：`workflow/{contracts,definition,compiler,runner,checkpoint,lease,recovery,replay,trace}.py`。
- 改法：`WorkflowRunner.__init__` 必填 registry/checkpoint/lease/recovery/trace/UoW；禁止构造 concrete
  SQLite；保留 retry/loop/HITL/quarantine/replay语义。
- tests：`test_compiler.py`, `test_runner_fault_matrix.py`, `test_checkpoint_reopen.py`,
  `test_lease_quarantine.py`, `test_replay.py`。
- 依赖：T2.1–T2.5。

### T4.2 — Profile catalog / orchestration control / launch ticket [AC-6]

- 文件：`runtime/profiles.py`, `runtime/orchestration.py`, `runtime/drivers/workflow.py`。
- API：`ProfileDescriptor(key, description, use_when, avoid_when, input_schema_ref, generation,
  fingerprint)`；`workflow_spawn(profile_key, objective, ..., candidate_id?, catalog_generation)`。
- 改法：control schema从 frozen model-spawnable catalog生成；Host只 validate + bind ticket；无 regex/
  classifier/route_hint。conditional registration检查 required Ports。
- tests：`test_agent_selected_profile.py`, `test_stale_catalog.py`, `test_forged_binding.py`,
  `test_optional_profiles.py`。
- 依赖：T2.3、T3.3、T4.1。

## S5 — official Profiles

### T5.1 — durable_task graph/ports [AC-5, AC-7]

- 文件：`workflows/durable_task/{definition,state,ports,nodes,output_contract}.py`。
- 改法：节点固定 plan -> HITL -> execute batches -> test/audit -> bounded repair -> output audit ->
  terminal；completion只依据 plan/effect/output receipts；所有产品能力经 Proposal/CapabilityCatalog/
  Workspace/Artifact/Authorization Ports。
- tests：`test_durable_task_graph.py`, `test_durable_task_human.py`, `test_durable_task_recovery.py`,
  `test_durable_task_output_negative.py`。
- 依赖：T4.1–T4.2。

### T5.2 — personal descriptor/catalog/interpreter [AC-6, AC-7]

- 文件：`workflows/personal_v1/{contracts,catalog,compiler,interpreter,definition}.py`。
- 改法：bounded descriptor只暴露 whitelist；candidate id + generation/fingerprint进入 control；Host从
  CatalogPort绑定 owner/version/graph；interpreter只允许 idempotent_read/deterministic_reusable。
- tests：`test_personal_catalog.py`, `test_personal_selection_live.py`, `test_personal_forgery.py`,
  `test_personal_safe_tools.py`, `test_personal_reopen.py`。
- 依赖：T4.2；H3。

### T5.3 — capability_build durable specialization [AC-6, AC-7]

- 文件：`workflows/capability_build/{contracts,profile,payload,validation}.py`，pyproject extra。
- 改法：Profile绑定 durable_task v1；payload必须含 current-stamp search-miss receipt；required Ports
  六项全部 ready才注册；build/test/install/activate每步有 authorization/receipt，成功后默认可用。
- tests：`test_capability_profile.py`, `test_search_miss_gate.py`, `test_build_failure.py`,
  `test_install_activate.py`, `test_required_ports.py`。
- 依赖：T5.1、T4.2。

### T5.4 — conformance CLI/pytest consumer API [AC-8]

- 文件：`testing/cli.py`, `testing/pytest_plugin.py`, `docs/api/conformance.md`, pyproject entry point。
- API：`python -m simple_harness.testing --host module:factory --suite provider,tool,runtime,workflow
  --json report.json`；extra `[testing]` 安装 pytest；plugin fixture `simple_harness_conformance_host`。
- 兼容：report记录 SDK version、protocol version、host id/platform；major protocol不匹配 fail closed。
- tests：`tests/conformance/test_cli.py`, `test_pytest_plugin.py`, `test_protocol_version.py`；wheel install后
  执行 CLI，证明 tests/源码目录不存在也可运行。
- 依赖：T3.4、T5.1–T5.3。

## S6 — Simple Harness consumer/cutover

### T6.1 — Product SDK adapters [AC-6..8]

- 文件：`backend/deskpet/sdk_adapters/{composition,ingress,provider,authorization,context,tools,
  reconciliation,personal_catalog,capability_host,delivery,runtime_paths}.py`。
- 改法：逐项实现 SDK Protocol；`composition.build_product_runtime()` 只 import SDK public API；
  Presenter/SessionDB/Tool handlers/permissions/Companion/capability platform保持产品 owner。
- tests：每个 Adapter 一个 contract test；用 T5.4 CLI 对 `deskpet.sdk_adapters.conformance:build_host`
  运行 provider/tool/runtime/workflow suites。
- 依赖：T5.4。

### T6.2 — 生产 ingress/path dependency 首次切换 [AC-6, AC-8]

- 文件：`backend/main.py`, `backend/deskpet/harness/adapters/product_turn_open.py`（改成薄转发后删除）、
  `backend/pyproject.toml`, `backend/uv.lock`。
- 改法：开发期 `uv sources` 指向 `$SDK_REPO`；main composition/ingress只用 T6.1；禁止 fallback
  flag；运行 oracle迁移脚本，保持 approved assertions。
- tests：现有 Harness targeted suite + SDK-S1..S7 automation；`assert_sdk_cutover.py --phase path`。
- 依赖：T6.1、T0.3。

### T6.3 — 删除旧 authority / matcher / router [AC-6..8]

- 文件：按 [`cutover-manifest.md`](cutover-manifest.md) SDK-authority rows删除或裁出产品-owned部分；
  删除 `ModelPersonalWorkflowMatcher` production wiring、legacy selector/ticketless public branch。
- 改法：消费 T0.4 已冻结的完整 disposition并逐 symbol删除；Adapter不得 import deleted owner；历史测试按
  BC-SDK-IMPORTS迁到 SDK public API，assertion AST hash保持。
- tests：`scripts/acceptance/assert_sdk_cutover.py --phase deleted`；全 Harness/companion/capability tests；
  negative `rg`/AST/module inventory。
- 依赖：T6.2 parity PASS。

### T6.4 — 审计式 dev execution reset [AC-7]

- 文件：`scripts/dev/reset_sdk_execution_data.py`, `backend/tests/test_sdk_execution_reset.py`,
  `docs/sdk-reset.md`。
- 改法：resolve exact user-data；dry-run inventory DB/WAL/SHM+hash；生成 nonce；只有
  `--confirm-reset <nonce>` 且 allowlist exact match才删除；拒绝 root/home/repo/evidence/Product Session。
- tests：temp directory positive；symlink/path traversal/wrong nonce/broad target negative；产品真实 reset
  前单独取得执行确认，输出只记录安全 hash。
- 依赖：T6.2；在 T6.3 final run 前执行。

### T6.5 — Simple Harness desktop真 E2E [AC-5..8]

- 文件：`scripts/acceptance/sdk_desktop_receipt.py`、本 plan result/index（原始证据 local only）。
- 改法：按 AGENTS.md 启动唯一 Tauri-managed backend/vite；SDK-S1..S5逐案真实点击/输入；receipt按
  run/child/effect/provider/delivery ID 审计，supervisor restart验证无重复。
- tests：SDK-S1..S7 terminal expectations；截图/日志/DB hashes在 `.local-test-evidence`。
- 依赖：T6.3–T6.4。

## S7 — exact artifact / release / handoff

### T7.1 — 私有 immutable release artifact [AC-1, AC-8]

- 文件：SDK `.github/workflows/release.yml`, `docs/release/v0.1.0.md`。
- 改法：默认 remote `git@github.com:DennyWanye/simple-harness-sdk.git`、visibility private；public 化需
  另行显式批准。GitHub Release保存 wheel/sdist/SBOM/NOTICE/attestation；tag build只生成一次，所有
  runner下载该 artifact；release note记录 commit/tag/hash。
- tests：release dry-run；artifact digest/attestation verify；remote/tag ancestry。
- 依赖：T0.2、T5.4。

### T7.2 — 三平台安装同一 wheel [AC-1, AC-8]

- 文件：SDK CI `artifact-consumers.yml`。
- matrix：`ubuntu-24.04-arm` (Linux ARM64), `macos-14` (ARM64), `windows-latest` (x64), Python 3.11；
  job下载 T7.1 exact wheel，校验 hash，clean venv装 `[testing]` 并跑 import/conformance CLI。
- tests：三个原生 runtime report必须同 wheel SHA/protocol/version；任一缺 runner/PASS则 release blocked。
- 依赖：T7.1 candidate artifact。

### T7.3 — Product vendor exact wheel 与 PyInstaller [AC-1, AC-8]

- 文件：`backend/vendor/simple_harness_sdk-0.1.0-py3-none-any.whl`, `backend/pyproject.toml`,
  `backend/uv.lock`, `scripts/verify_sdk_wheel.py`, `scripts/build_backend.ps1`, PyInstaller spec/hook。
- 改法：从 private Release下载一次，验证 T7.1 SHA后把**同一 bytes** vendored进 private产品 repo；
  backend dependency使用 local wheel direct reference并由 `uv.lock` 锁 hash；build前 verify；PyInstaller
  collect SDK modules/metadata。禁止 editable/path/PYTHONPATH。
- tests：clean checkout `uv sync --frozen`、wheel provenance、frozen backend import/conformance、
  `assert_sdk_cutover.py --phase wheel`，然后重跑 T6.5。
- 依赖：T7.2 PASS。

### T7.4 — AIPhone Handoff [AC-8]

- 文件：SDK `docs/consumers/aiphone-handoff.md`, release manifest JSON。
- 改法：写 private Release artifact获取方法、exact identifiers、Python/platform results、factory/Port/
  conformance CLI、三个 Profile、Mobile Host boundary、known limits；不含 secret/设备控制码。
- tests：在 clean Linux ARM64 job用 handoff命令安装 exact artifact并运行 fake Mobile Host Tool Adapter
  conformance；本 release不写/部署 AIPhone repo。
- 依赖：T7.3。

### T7.5 — 架构事实与 program closure [AC-1..8]

- 文件：SDK architecture/status/changelog；产品 `ARCHITECTURE/SDK_EXTRACTION.md`,
  `ARCHITECTURE/PROJECT_STATUS.md`；`ac-trace.json`, `FINAL_REPORT.md`。
- 改法：生成 `AC -> Task -> code symbols -> testcase -> result/receipt`；记录 old authority absence、
  exact wheel、consumer reports与所有非 PASS gate。只有 AC-1..8 全 PASS 才标完成。
- 依赖：T0.1–T7.4 全部 PASS。
