# Cutover manifest — source/symbol disposition

> 状态：plan-frozen target manifest；[`cutover-symbols.json`](cutover-symbols.json) 已冻结 30 个 authority source hash、184 个 public symbol inventory hash 和禁止残留的 owner definitions；实施时 `source-manifest.tsv` 为每个文件补 copyright、license verdict、逐 symbol disposition 和 target blob。  
> 原则：`rewrite` 表示以行为 oracle 为准重写到 Apache SDK，不等于复制 BUSL 文件；`adapter-retain` 只能依赖 SDK public API，不能代理旧 authority。

## SDK authority：rewrite 后删除产品实现

| Current source/symbol | SDK target | Disposition / final forbidden symbol |
|---|---|---|
| `backend/deskpet/harness/kernel.py::RunKernel`, `root_run_identity`, `decision_response_allows` | `src/simple_harness/runtime/kernel.py` | rewrite；产品最终禁止定义 `class RunKernel` |
| `harness/kernel_terminal.py::KernelTerminalLifecycle` | `runtime/terminal.py` | rewrite；terminal+delivery UoW 保持 SDK authority |
| `harness/admission_launch.py::AdmissionLauncher` | `runtime/admission.py` | rewrite |
| `harness/context.py::HostContextFactory` | `runtime/context.py` | generic contract rewrite；产品 assembler 留 Adapter |
| `harness/{child_runs,child_signal_runtime,live_index,runtime,start_snapshot,user_continuations,reconciler}.py` | 同名 `runtime/_internal/*.py` | rewrite；产品最终禁止这些 class 的第二定义 |
| `harness/profiles.py::{ProfileSpec,ProfileRegistry}`、`harness/ports.py` generic 部分 | `runtime/{profiles,ports}.py` | rewrite；public Profile contract only |
| `harness/drivers/react.py::ReActDriver`, `react_loop.py::AgentLoopCollaborator`、`agent/agent_loop.py::AgentLoop` generic state machine | `runtime/drivers/{react,react_loop}.py` | split/rewrite；DeskPet Context/metrics 留 Adapter；产品禁止 `AgentLoop` execution owner |
| `harness/drivers/workflow.py::WorkflowDriver` | `runtime/drivers/workflow.py` | rewrite |
| `harness/tool_executor.py::EffectBatchExecutor` | `tools/executor.py` | rewrite；uses public Tool/Authorization/Reconciliation Ports |
| `execution/{contracts,uow_ports,fences,dispatch,provider_invocations}.py` generic authority | `execution/{contracts,uow,fences,dispatch,provider_invocations}.py` | split/rewrite；产品禁止 Provider/Effect/Run ledger writer |
| `workflows/store/{schema,execution_uow}.py` generic command implementations | `execution/sqlite/{schema_v1,uow}.py` | rewrite clean v1；产品旧 schema 1–29 implementation removed after reset |
| `harness/projector.py::{ExecutionDeliveryDispatcher,SinkRegistration}` + generic delivery contracts | `execution/delivery.py` | rewrite；product sinks remain Adapter |
| `workflows/{contracts,errors,control,definition,native,runner,recovery,replay,execution_ports}.py` + checkpoint/lease/trace primitives | `workflow/` | rewrite；产品禁止 `WorkflowRunner/NativeWorkflowRunner` second owner |
| `workflows/definitions/v1/durable_task.py`, reusable `definitions/code_*`, `adapters/code_runtime.py` | `workflows/durable_task/` | rewrite generic graph/ports；DeskPet tools/workspace implementation excluded |
| `workflows/definitions/personal_workflow.py`, `adapters/personal_runtime.py` generic compiler/interpreter | `workflows/personal_v1/` | rewrite safe-replay boundary only |
| `capabilities/builder.py` + generic builder contracts/validators | `workflows/capability_build/` | rewrite as optional governed durable-task specialization |
| `providers/openai_compatible.py` protocol behavior | `providers/openai_compatible.py` | clean rewrite；do not copy DeskPet metrics/context/report hooks |

## Product-owned adapter-retain

| Current owner | Final product target | Must call |
|---|---|---|
| `harness/adapters/product_composition.py` | `backend/deskpet/sdk_adapters/composition.py` | `simple_harness.runtime.build_runtime` |
| `harness/adapters/product_turn_open.py` / venue adapters | `sdk_adapters/ingress.py` | SDK `RunClient.start/signal/cancel/query` only |
| `agent/turn_preparer.py` product Context OS | `sdk_adapters/context.py` | SDK `PreparedRunContext/ProfileDescriptorCatalog` |
| `tools/registry.py` product handlers/resources/permission glue | `sdk_adapters/tools.py`, existing handler modules | SDK Tool/Authorization/Reconciliation protocols |
| `permissions/*` | `sdk_adapters/authorization.py` + existing UI/policy | SDK `AuthorizationPort`; no SDK policy id/category |
| `companion/turn_authority.py` candidate store/UI | `sdk_adapters/personal_catalog.py` | SDK `PersonalWorkflowCatalogPort`; matcher deleted |
| capability hub/platform/source/package process | `sdk_adapters/capability_host.py` | SDK capability-build Ports |
| `agent/run_presenter.py`, SessionDB, ArtifactCard/WebSocket/TTS | `sdk_adapters/delivery.py` + current product modules | SDK generic delivery sink only |
| config/keychain/provider registry | `sdk_adapters/provider.py` | SDK Provider factory; secret never enters SDK state |

## Delete/retire

- `backend/deskpet/workflows/routing.py` production selector and `ModelPersonalWorkflowMatcher` call path.
- `RunKernel` public `classifier/router` construction and ticketless child `route_hint/driver_kind` authority.
- Product definitions of SDK-owned classes listed above after exact-wheel parity; no feature-flag fallback.
- schema 1–29 execution migration/compatibility code used only for old development Run reads. Historical docs/fixtures may remain outside production imports.

## Explicitly excluded from SDK

`main.py`, FastAPI/WebSocket/Tauri/UI, SessionDB/messages, TTS/voice/local model, browser/desktop/Office,
DeepResearch, PPT, MCP marketplace, product team orchestration, default shell Tool, Mobile Host/Droidian.

## Final machine gate

`scripts/acceptance/assert_sdk_cutover.py` reads a generated `cutover-symbols.json` and fails when:

1. any production module outside `backend/deskpet/sdk_adapters/` defines an SDK-owned class/symbol;
2. product code imports `simple_harness._internal` or old retired owner paths;
3. an Adapter imports an old authority instead of an SDK public symbol;
4. the built PyInstaller module inventory lacks `simple_harness` or contains deleted authority source;
5. installed SDK `version/commit/wheel_sha256` differs from release lock.
