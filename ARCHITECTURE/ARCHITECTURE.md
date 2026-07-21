<!-- last-calibrated: 2e71e9a1 -->
# DeskPet Long-Running Agent Architecture Baseline

> Last verified: 2026-07-21. DeepResearch new-run default is immutable v7. It uses a six-node manager graph that decomposes the topic, runs one bounded research child per direction, diagnoses/retries weak child results, joins all terminal children, and synthesizes one cited report. Historical v1-v6 runs remain registered for recovery and compatibility reads. The UI now projects stable per-direction business progress, suppresses internal attempt rows, restores historical file artifacts, and exposes the standard open/save-as/reveal/copy-path actions. Reports continue to use the legacy `DeepResearch` directory.

> Current observability fact: workflow-node `started_at`/`ended_at`/status/attempt are durable in `workflow_node_attempts`, node `duration_ms` is durable in `trace_spans`, and `deepresearch_stage_timing` is a diagnostic mirror. V7 additionally logs privacy-safe child id/attempt/status/reason/source count/duration; page content and full prompts are excluded.

> Calibration map: §3 is explicitly retained as the pre-durable persistence baseline; §4.2/§4.3 and §5-§10 retain historical problem/design context. The header, §4.1, §11 and §13-§15 are current-calibrated production facts. DeepResearch detail lives in [`SEARCH_GATEWAY_DEEPRESEARCH.md`](SEARCH_GATEWAY_DEEPRESEARCH.md).

## 1. System Shape

DeskPet is a local desktop product, not a generic agent framework. The application has four runtime layers:

```text
Tauri shell + React UI
        | WebSocket / HTTP / Tauri commands
backend/main.py orchestration and adapters
        | service_context + build_agent
DeskPet Harness
        | AgentLoop / ContextAssembler / ToolRegistry / completion gates
Product capabilities and local persistence
        | Office / research / files / permissions / artifacts / state.db
```

The product Harness is valuable and remains the main agent execution boundary. The durable workflow upgrade must compose with it rather than replace it. The canonical text-chat stages and their current persistence/recovery boundaries are defined in [`AGENT_HARNESS.md`](AGENT_HARNESS.md); the isolated new runtime exports its executable manifest from `backend/deskpet/harness/bootstrap.py::HarnessManifest`.

## 2. Current Request Lifecycle

The production text-chat path is:

```text
ws_ingress
  -> context_assembly
  -> pre_loop_problem_pipeline
  -> AgentLoop ReAct turns
       <-> LLM chooses tool calls
       <-> ToolRegistry permission/timeout/receipt/artifact dispatch
       <-> completion/verification gates
       <-> optional sidecar subagents
       -> ws_egress for each emitted event
```

`backend/main.py` owns the WebSocket adapter, provider resolution, service construction, plan/permission/clarification round trips, persistence calls, and event mapping. `AgentLoop.run()` is the dynamic reasoning loop. It emits typed events but does not own WebSocket or SessionDB writes. `ContextAssembler` selects persona, memory, skills and tool schemas before the problem pipeline and loop. `ToolRegistry.execute_tool()` is the top-level execution gate for tools selected by AgentLoop, not a universal boundary for every product action.

The flow above is documented as ordered lifecycle stages, but it is not a general durable graph runtime. DeskPet already has a persisted goal `TaskGraphStore` and SQLite-backed TeamStore task data; neither drives the parent AgentLoop or the three long-running product workflows with compiled edges, shared typed workflow state, leases and exact node checkpoints.

The default chat policy currently exposes the wildcard tool set. “Short chat stays on ReAct” therefore means routing and behavior compatibility, not a physically tool-free runtime. Code planning, todos and goal tasks are also three separate state models with no shared executable step id.

Other ingress venues are not identical:

- `/ws/audio` uses `VoicePipeline`: SessionDB/context assembly -> AgentLoop -> TTS. It does not share every text-chat problem-pipeline, plan-confirm and provider-resolution step.
- Slash/admin/control commands are handled directly in `main.py` and can bypass AgentLoop and ToolRegistry.
- Tauri commands and background workers can perform product actions outside the text-chat lifecycle.

The runtime also spans two Python package trees: `backend/agent/*` contains the parent loop and legacy/P6 services, while `backend/deskpet/agent/*` contains newer assembler, gates and product-agent services. New workflow core code needs one explicit owner to avoid extending this split.

## 3. Historical Persistence Baseline And Current Timing Owners

This section's original inventory predates the native workflow runtime and is retained to explain the migration. The current workflow fact source is `<user_data>/data/workflow.db`, as calibrated in §13; `state.db` remains the conversation/memory/product projection store rather than the workflow execution owner.

### 3.1 Current DeepResearch timing ownership

| Layer | Current owner | Persisted fields / retention | Current gap |
|---|---|---|---|
| Workflow node attempt | `workflow.db.workflow_node_attempts` | `started_at`, `ended_at`, `status`, `retry_attempt`; terminal workflow data defaults to 30-day retention | `duration_ms` is derived rather than a separate attempt column |
| Workflow node span | `workflow.db.trace_spans` | durable `started_at`, `ended_at`, `duration_ms`, node/status/attributes | no fetch-transport child spans yet |
| Search provider attempt | `search_gateway_attempt` in rotating `metrics.jsonl`; response/state may retain request-local outcome | `run_id`, request/provider/status/duration/count under the metrics privacy whitelist | not a durable trace-level attempt ledger for long-term latency analysis |
| Fetch transport/extractor | `deepresearch_fetch_attempt_timing` in rotating `metrics.jsonl` | `run_id`, stage/status/duration/fetcher/extractor/error/count | best-effort only; throttle/URL-lock/total fetch are not fully separated; millisecond rounding can produce `0` for sub-ms work |
| Diagnostic export | `<user_data>/metrics.jsonl` | max 2 MB; rotation keeps roughly the latest 2000 rows; included in support diagnostics | support bundle does not export rich workflow trace rows, so metrics is not 30-day exact history |

Current v7 must preserve the metrics privacy wall while adding durable, correlated child timing where AC-OBS requires later latency analysis. Telemetry failure must remain non-fatal to workflow execution. The earlier v6 requirement is retained only as historical implementation context in its module plan.

### 3.2 Pre-durable inventory (historical context)

The primary local database is `state.db`, managed by `deskpet.memory.session_db.SessionDB` and `deskpet.memory.schema.initialize_state_db`.

| Data | Current store | Durability | Limitation for workflows |
|---|---|---|---|
| Sessions and messages | `sessions`, `messages`, FTS/optional vec tables | Durable | Records conversation, not executable node state. |
| Code todos | `code_todos` | Durable | Full-list replacement; no node attempt/result/checkpoint model. |
| Code project binding | `code_sessions` | Durable | Identifies project/session only. |
| Awaiting code plan | `session_plans` | Durable record | The actual waiter is an in-memory `Future`; restart reloads the card but cannot resume the suspended coroutine. |
| Goals and goal tasks | `session_goals`, goal task tables | Durable | Completion tracking, not general workflow execution. |
| Team tasks/messages/permissions | Per-team SQLite files | Durable data | Worker coroutines and reclaim logic are process-local. |
| PPT outline history | `ppt_outline_history` in `state.db` | Durable record | Proposal status survives, but the running task and waiter are process-local. |
| Tool receipts | Receipt store | Durable evidence | Can prove side effects, but is not yet keyed to a workflow node attempt. |
| Artifacts | User data/output directories plus message envelopes | Durable files | No generic graph state references or replay policy. |
| Agent iteration trace | `<user_data>/traces/<task>.jsonl` | Append-only file | Flat per-run records, optional, no span tree/checkpoint link. |
| Anonymous metrics | `<user_data>/metrics.jsonl` | Rotating append-only file | Strict whitelist, intentionally too small for rich trace/replay. |
| Context decisions | In-memory `ContextAssembler` ring | Process-local | Context Trace UI loses history after restart. |

Additional stores include facts/workspace/skill memory and feedback tables in `state.db`, `billing.db`, supervisor hints, pending skill candidates, provider bindings, preference/runtime JSON files and permission-auto-mode state. Some are owned by formal migrations, while PPT outline and skill-candidate tables are still created lazily by their business modules. The workflow plan must inventory schema ownership and retention without attempting to merge unrelated product data into graph state.

`SessionDB` already provides WAL, busy timeout, an application write lock, retry with backoff, and idempotent migrations. It is the natural physical store for workflow metadata, but the graph core must depend on a storage protocol rather than directly on `SessionDB`.

The persistence layer currently has a version-contract defect that must be resolved before adding workflow tables: the configured target schema version is ahead of the highest effective migration/test baseline, while several memory-v2 tables are still created lazily by business modules. A single migration owner is required before the durable store becomes authoritative.

## 4. Long-Running Workflows Today

### 4.1 DeepResearch

New research runs are accepted as durable `deep_research/v7`; `backend/deskpet/tools/research_tools.py` provides the focused child/manager collection core while the v7 graph owns production orchestration. The current production chain is:

```text
deterministic ingress -> durable v7 launch
  -> normalize -> plan (2-6 subdirections)
  -> manager schedules one focused research child per direction
  -> classify -> diagnose/continue (max two attempts) -> join
  -> synthesize valid reports + explicit limitations
  -> persist -> generic terminal outbox
  -> SessionDB durable projection + best-effort WebSocket
```

Current UI/delivery boundary:

```text
plan/search -> durable v7 children snapshots keyed by stable child_id
  -> DeepResearch progress card (direction/status/attempt/source count)
  -> internal dr-i.aN scheduler rows filtered from the generic panel

persist -> canonical artifacts[] file envelope
  -> ProductDeliveryAdapter -> one FileArtifactCard
  -> open / save-as / reveal in folder / copy path
history -> old nested file metadata normalized to the same file card
```

The parent-run projection is keyed by stable `child_id`, with retries updating the same direction row. A separate
children sequence lets late child terminal snapshots converge without rolling the parent card back from synth/final.
Canonical file envelopes use the existing `FileArtifactCard`; old nested envelopes remain readable during history
hydration without generating new delivery side effects.

V7 is immutable after release: v1-v6 remain registered for historical reads and recovery and are never coerced into a newer checkpoint schema. V7 owns manager-style decomposition, child monitoring/continuation, three-state business delivery and a single final report; v6 remains the compatibility path for its existing exact-evidence runs. Search and fetch share the process-wide Search Gateway and FetchExtractService; workflow/node state and traces are durable in `workflow.db`, while low-cardinality diagnostic mirrors remain in `metrics.jsonl`.

Provider health is shared per provider through a closed/open/half-open circuit. V7 can converge honestly to `completed`, `partial`, or `insufficient_evidence`; engine completion is recorded separately from business status. When public SERPs are unavailable, a child may nominate bounded direct URLs, but fetched text must pass the same evidence gates. Current implementation and spike evidence are documented in [`DeepResearch.md`](DeepResearch.md), [`SEARCH_GATEWAY_DEEPRESEARCH.md`](SEARCH_GATEWAY_DEEPRESEARCH.md) and [`../plans/2026-07-19-deepresearch-simplification/spike/result.md`](../plans/2026-07-19-deepresearch-simplification/spike/result.md).

### 4.2 PPT Pro

`backend/deskpet/tools/ppt_tools.py::_ppt_pro_orchestrate()` is an explicit but process-local workflow:

```text
DeepResearch
  -> draft outline
  -> user accept/modify/reuse/cancel loop
  -> render PPT
  -> preview/visual review
  -> artifact and receipt reporting
```

`ppt_outline_history` persists proposals. `_PPT_PRO_RUNNING`, `_PPT_PRO_TASKS` and the outline waiter registry hold active tasks/Futures in memory. Restarting the backend cannot continue the original coroutine after an outline decision. Rendering is a side-effecting boundary and must not be repeated blindly after recovery.

The top-level ToolRegistry handler returns `researching` immediately, so its first success receipt describes background-task acceptance rather than final deck delivery. The background path directly calls DeepResearch, LLM outline generation, image generation, rendering and visual review, then manually emits terminal artifacts/receipts. A graph migration must make those two meanings explicit instead of treating one tool receipt as the whole workflow.

The older `ppt_create` image path also starts a background worker and returns `generating` before the file exists. It shares the same acceptance-versus-delivery receipt and cancellation problem even though AC-7 focuses on PPT Pro.

### 4.3 Complex Code Tasks

Code mode uses the same `AgentLoop`, augmented by:

- a code persona that asks the LLM to clarify, plan, execute and verify;
- an optional persisted plan-confirm card;
- durable `code_todos` and code-session/project binding;
- `ask_clarification`, permission Futures and UI responses;
- completion probes, VerifyGate, GoalChecker, external evaluator and self-check;
- optional sidecar subagents/teams.

These features discipline ReAct behavior but do not make plan steps executable nodes. Todos are advisory state read back into prompts. Auto-resume starts a fresh agent run with a supervisor hint; it does not resume the exact LLM/tool step.

Subagent execution has three separate shapes: blocking `agent_parallel`, process-local nonblocking subagent registry/queue, and a team task SQLite store whose workers are still process-local. None currently provides a durable lease and crash reclaim contract for workflow nodes.

## 5. Human-In-The-Loop Today

DeskPet has several real user gates:

| Gate | Durable record | Active waiter | Restart behavior |
|---|---|---|---|
| Tool permission | Audit/receipt around tool execution | In-memory `Future` in permission gate | Pending execution cannot resume exactly. |
| Code plan confirmation | `session_plans.awaiting` | `_PLAN_CONFIRM_WAITERS` Future map in `main.py` | UI card can rehydrate; original coroutine is gone. |
| `ask_clarification` | Conversation/UI event | In-memory pending Future | No durable suspended node. |
| PPT outline confirmation | `ppt_outline_history.status` | In-memory outline Future registry | Proposal survives; workflow continuation does not. |
| Skill candidate confirmation | Pending candidate row | In-memory timed Future | Candidate survives, but production does not rebuild the waiter on restart. |
| Team permission/decision | Per-team SQLite data | Process-local teammate coordination | Data survives; active worker continuation does not. |

The UI interactions are genuine Human-in-the-loop, but the suspension mechanism is not durable. A future graph runtime should turn each gate into a persisted `waiting` node plus an idempotent response command.

## 6. Recovery Today

Recovery is reactive:

- Session messages, goals, todos, plans, artifacts and receipts survive restart.
- `AutoResumeOrchestrator` classifies selected `ErrorEvent` reasons, asks the supervisor for a nudge, then dispatches a new chat run.
- Supervisor follow-up can enqueue another synthetic turn.
- Tool circuit breakers, termination gates and convergence controls stop local failure loops.
- A new message for the same session cancels the previous chat task, but cancellation does not guarantee that threadpool-backed side effects have stopped.

This is useful resilience, but the recovery unit is a chat run. There is no persisted node lease, committed state version, compare-and-swap owner, retry policy per node, or deterministic state merge for parallel branches.

## 7. Observability And Evaluation Today

Current observability is split across several systems:

1. `IterationTracer` writes optional JSONL events keyed by task/session.
2. `MetricsSink` writes privacy-safe whitelisted counters to `metrics.jsonl`.
3. `ContextAssembler` keeps a 50-record in-memory decisions ring exposed by `ContextTracePanel`.
4. Prometheus exposes a small set of operational metrics such as LLM TTFT.
5. Individual workflows emit their own coverage, timings, logs, artifacts and receipts.
6. Structlog/log files, crash reports and supervisor audit records provide additional diagnostics.

The iteration tracer is currently disabled unless an unconfigured `[agent].iteration_trace_enabled` key is added; `[context.assembler].trace_enabled` controls a different feature. Its construction also uses a generated task id and empty session id, so correlation with the production AgentLoop run is unreliable. Context Trace is a global process-local ring rather than a persisted, session-filtered trace.

Current quality checks are runtime gates rather than an evaluation platform:

- VerifyGate matches completion claims against receipt evidence.
- GoalChecker uses an LLM judge for active goal completion.
- ExternalEvaluator uses a different model/persona and returns score/issues/pass-or-revise.
- PPT visual review evaluates rendered slides.
- Existing scripts/tests provide domain-specific regression checks.

Missing platform concepts are a common trace/span schema, parent-child trees, persisted run index, checkpoint links, read-only replay, forked replay, evaluator records, datasets and version comparison.

There are concrete contract gaps behind that summary: evaluators return incompatible tuple/dataclass/dict shapes; some evaluator event names and score fields are not accepted by the metrics whitelist; and the diagnostic bundle does not include trace/checkpoint/evaluation summaries. These must be unified without weakening the existing privacy boundary of anonymous metrics.

## 8. External And Platform Dependencies

| Area | Current dependency/boundary |
|---|---|
| Desktop | Tauri/Rust, WebView2, React/TypeScript, Pixi/Live2D. |
| Backend | Python asyncio/FastAPI/WebSocket adapters. |
| Persistence | SQLite/aiosqlite, WAL, optional sqlite-vec. |
| Models/audio | Torch/CUDA, BGE-M3, Silero, faster-whisper, CosyVoice/edge-TTS. |
| LLM/secrets | OpenAI-compatible provider registry, relay, OS keychain and per-session resolution. |
| Research/browser | Search providers, Scrapling/trafilatura, browser-use/Chromium or system Edge CDP, optional Jina/direct-source adapters. |
| PPT/Office | python-pptx, image generation provider, LibreOffice plus WPS/Office COM rendering on Windows. |
| Extensions | MCP subprocess/client integration and filesystem-based skills. |
| Local execution | AgentLoop-selected ToolRegistry path, plus slash handlers, background workers and Tauri commands. |

The graph core must remain Python-only and UI-agnostic. It must not require a cloud trace service. Tauri/WebSocket, provider, tool and storage concerns enter through adapters.

## 9. Ownership Boundaries For The Upgrade

The following boundaries are stable enough to build on:

```text
workflow core
  definitions / validation / runner / state patches / policies
        |
workflow ports
  checkpoint store / trace store / evaluator / human response / clock
        |
DeskPet adapters
  SessionDB / ToolRegistry / provider / WebSocket events / artifacts / receipts
        |
workflow definitions
  DeepResearch / PPT Pro / Complex Code
```

The current Harness remains responsible for product policy. Workflow adapters must reuse ToolRegistry where a registered tool exists. Internal workflow stages that are not public tools still need equivalent centralized permission, trace, idempotency, artifact and receipt ports; they must not silently preserve today's bypasses.

## 10. Technical Debt And Risks

| Risk | Current evidence | Planning consequence |
|---|---|---|
| `main.py` owns too much orchestration | Provider, WebSocket, plans, permissions, service wiring and persistence meet there. | Add adapters and service registration; avoid placing graph algorithms in `main.py`. |
| Three workflows may fork their own runtimes | Research, PPT and Code already have separate state conventions. | Build and test one generic runtime before workflow migrations. |
| Side effects are not node-addressable | Receipts exist but lack workflow/node attempt identity. | Add idempotency keys and receipt linkage before replay. |
| Durable waiting is split-brain | DB records survive while Futures do not. | Persist waiting commands first; Futures become transport optimizations only. |
| State payloads can become huge | Research passages and rendered images are large. | Checkpoints store references/artifacts for large blobs, not unlimited inline JSON. |
| Parallel state merge can be nondeterministic | Research fan-out and subagents complete in arbitrary order. | Require reducers or conflict detection per state field. |
| Trace privacy is stricter than debug needs | Metrics intentionally rejects rich payloads. | Keep metrics separate; trace uses explicit redaction and local retention policy. |
| Replay can repeat destructive actions | File/Office/system tools have side effects. | Default replay reuses committed outputs; re-execution requires policy and confirmation. |
| Migration blast radius | `state.db` contains messages, memory, code and goals. | Add idempotent migrations, backup/rollback tests and repository-level compatibility tests. |
| Schema ownership is already split | Formal migrations and lazy runtime table creation disagree on the target version. | Repair the migration baseline before adding workflow tables; prohibit new business-owned DDL. |
| Cancellation cannot stop all effects | Cancelling an asyncio task does not terminate already-running threadpool work. | Model `cancel_requested` separately from terminal `cancelled`; reconcile late effect results. |
| Existing task claims have no lease | Goal/team/task state is not a general crash-reclaim coordinator. | Add owner, epoch, expiry and CAS transitions to workflow runs/nodes. |
| Trace correlation is unreliable | Iteration trace is default-off and generated ids do not match the live run. | Create trace/run ids at ingress and pass one context through every adapter. |
| Evaluation telemetry can disappear | Runtime evaluators emit shapes/events outside MetricsSink's closed schema. | Persist normalized evaluation records first; metrics becomes a low-cardinality projection. |
| Multiple ingress venues drift | Text, voice, slash/control and background workers do not share one call sequence. | Route workflows through a venue-independent coordinator and keep venue adapters thin. |
| Tool timeout can outlive its receipt | `wait_for` cannot kill a running threadpool handler; the caller may return before the effect finishes. | Persist effect intent before dispatch and reconcile late completion before retry/replay. |

## 11. Relevant Feature Defaults

| Capability | Current default/source | Baseline meaning |
|---|---|---|
| Plan confirm / agent team | Enabled in `[features]` | UI gates and team tools are active, but waiting/execution is not durable. |
| VerifyGate / ExternalEvaluator | Strict / enabled | Runtime completion gates are active; outcomes are not a unified eval record. |
| Context decisions trace | `[context.assembler].trace_enabled=true` | In-memory Context Trace view. |
| Agent iteration trace | No configured `[agent].iteration_trace_enabled` | Effectively off by default. |
| DeepResearch v7 durable workflow | `[workflows].deep_research_version="v7"` | New runs use the six-node manager/children graph; v1-v6 remain compatibility/recovery definitions. |
| Search Gateway | `[search_gateway].enabled=true` | Shared provider/cache/circuit resources with request-local budget and diagnostics. |
| PPT preview/outline history | Code defaults enabled | Preview files and outline rows exist; active background task/Future remains process-local. |

## 12. Test Boundaries

- Pure graph definitions, state reducers, checkpoint transitions, leases and trace serialization: deterministic pytest.
- Crash/restart, SQLite migration, duplicate response and side-effect replay: integration tests with failure injection.
- Existing Harness adapters and three production workflows: focused regression suites plus live stack tests.
- Trace viewer, durable pause/resume, replay confirmation and human scores: project-mandated windows-mcp true UI testing with screenshots and backend logs.

No protocol-level WebSocket injection may replace real UI evidence for user-visible behavior.

## 13. 2026-07-11 Durable Runtime Calibration

The durable workflow upgrade is now present under `backend/deskpet/workflows/`. `WorkflowLauncher` owns accepted-async background driving and terminal outbox publication; `WorkflowRunner` owns leases, resume and terminal convergence; `WorkflowExecutionObserver` records node executions and structured spans. `workflow.db` is authoritative for runs, node executions, checkpoints, events, deliveries and trace/evaluation data.

The current session projection uses one durable public path:

```text
Workflow node wrapper
  -> WorkflowExecutionObserver
  -> node execution + Trace span persisted

Workflow node wrapper
  -> WorkflowProgressReporter (public-node whitelist)
  -> stable workflow.progress Outbox event
  -> SessionDB workflow_event_id + WebSocket delivery

WorkflowLauncher
  -> workflow.accepted / terminal intents / workflow.final
  -> WorkflowOutbox
  -> SessionDB + WebSocket delivery handlers in backend/main.py
  -> ws.ts reduces structured events into the original session
```

DeepResearch, PPT Pro and Complex Code publish user-safe stage starts through this reporter. The progress `event_key` contains workflow/node/transition/attempt and is scoped by Outbox `run_id`, so a new retry attempt receives a higher durable event sequence while replay of the same attempt remains idempotent. `ws.ts` and hydrated history now feed the same reducer instead of appending each event as a normal assistant message.

AC-23 changes the product projection, not the durable event source: live and hydrated history envelopes feed one `run_id`-keyed reducer, monotonic `seq` rejects replay and out-of-order regression, and one `workflow_progress` component is updated in place. A late history response reduces into the current store rather than replacing a newer live component. `workflow.accepted` creates the component, `workflow.progress` updates its node-level display state, `workflow.final` alone locks completed/failed/cancelled run terminal state, and `workflow.final_assistant` remains a separate answer bubble. Node-level failed/cancelled progress may be superseded by a later higher-seq recovery event. Waiting remains visible until the next public stage starts or the run reaches final. Progress projection never carries raw prompts, tool arguments or file content; unrelated ordinary tool messages keep their existing UI semantics.

Progress may be emitted when a public stage starts. A node wrapper's `succeeded_pending` callback is not a durable completion boundary, so it must not publish “completed” before the following checkpoint/superstep is committed. The next public stage start is the safe user-visible evidence that the previous stage advanced; waiting, failure, cancellation and final states remain explicit. Recovery, manual resume and retry must reconstruct the persisted `delivery` session reference rather than substituting `workflow_runs.session_id`, otherwise the epoch guard in `backend/main.py` correctly discards the event.

PPT outline decisions are a separate product state machine: `modify` validates non-empty feedback, resolves the current decision into `revise_outline`, creates a new revision and a new pending decision/card, and must say “正在修改大纲”; `accept` and `reuse` enter generation; `cancel` terminates without generation. Duplicate responses retain the existing durable decision CAS semantics.

PPT Pro v1 creates stable slide records, checkpoints each generated background, and uses a deterministic deck-level planner plus six Pillow compositors. The planner assigns `cover_band/text_left/text_right/visual_top/floating_card/quote_center`, enforces deck coverage and adjacency constraints, and runs the same text-fit policy used by the compositor before any image effect is committed.

The planner runs before effect identity is computed, aligns each text-free image prompt's negative-space direction with the corresponding Pillow safe region, validates a CJK-capable font, and fails explicitly on unfit copy. Variant, layout-spec, compositor and font-policy versions enter the effect hash. The final boundary remains a 1792x1008 raster page; `python-pptx` inserts exactly that one picture and adds no visible text or decoration shapes.

For AC-21 the image-mode boundary becomes:

```text
confirmed outline deck (AC-22 target)
  -> deterministic layout plan + variant-specific text-free background prompt
  -> exact visible copy + layout variant/spec version in the input hash
  -> one durable image effect per stable slide id + content revision/input hash
  -> 16:9 normalization + variant-aware deterministic text compositor
  -> blank PPT slide with exactly one full-bleed picture
  -> preview + visual evaluation
  -> regenerate only pages named by visual issues
  -> publish artifact
```

Template mode remains available for legacy/compatibility requests. Explicit `full_page_images` is strict: provider failure during initial generation or a visual revision terminates with a user-safe recoverable error and never reports a template deck as a successful full-page result. Full-page image mode intentionally trades away element-level editability; speaker notes and the outer `.pptx` artifact remain.

Visual issue invalidation is explicit: issue page -> stable slide id -> incremented page revision -> revised full-page prompt -> recomputed input hash -> only that record returns to `pending` -> image map generates a new effect. The PPT production adapter validates the generated file and the dedicated assembler owns 16:9 normalization and the `blank slide + exactly one full-bleed picture` invariant; the assembler never probes or calls the image provider.

## 14. Native Workflow Engine Replacement Baseline

The durable layer currently uses LangGraph as a narrow execution kernel, not as the product Harness. `WorkflowDefinition.bind()` converts DeskPet nodes, edges, reducers and retry policies into `StateGraph`; `WorkflowExecutable` forwards `ainvoke`, stream and `Command(resume=...)`; four pause points call `langgraph.types.interrupt` (one PPT outline gate and three Code clarification/plan/tool-approval gates). `FencedAsyncSqliteSaver` implements LangGraph's checkpoint protocol while adding DeskPet run fences, branch heads, pending writes, decisions, node projections and ownership rows.

The production coupling is concentrated but semantically deep:

| Coupling | Current owner | Native replacement boundary |
|---|---|---|
| Graph scheduling, join and retry | `workflows/definition.py::WorkflowDefinition.bind` | Execute `WorkflowDefinition` directly with a versioned static frontier; support sequential/conditional edges, multi-source join, exclusive barriers, bounded loops/retry and max-step cancellation checks. Dynamic map/Send is not part of the current contract. |
| Execution identity | LangGraph `runtime.execution_info` -> `NodeExecutionIdentity` | Deterministically generate checkpoint/task/attempt/first-attempt identities from run lineage, base checkpoint, invocation key and retry attempt before calling a handler. |
| Node lifecycle SPI | LangGraph node wrapper in `definition.py` | Native wrapper owns observer/progress callbacks, trace span activation, retry classification and `succeeded_pending`; storage remains external but these hooks are an engine contract. |
| Interrupt control flow | `definitions/v1/ppt_pro.py`, `definitions/code_nodes.py` | Raise a DeskPet `WorkflowInterrupt` carrying JSON-safe prompt/id. Native checkpoint commit atomically writes pending state, open decision and run `waiting`; Runner only owns lease/invocation/terminal convergence. |
| Checkpoint commit/projection | `store/checkpointer.py::aput_writes/aput` | Store canonical JSON native snapshots and preserve the two-phase invariant: handler success projects `succeeded_pending`; only the fenced checkpoint/head/ownership transaction promotes it to `succeeded`. |
| Resume/replay/fork | `definition.py::WorkflowExecutable`, `replay.py` | Resume from persisted native frontier; history/fork consume native snapshots. V1 fork remains root-namespace only, rejects pending writes/active fan-out, and requires the existing explicit confirmation gate for dangerous effects. |
| Evaluation adapter | `scripts/workflow_eval_adapter.py` | Use an in-memory/native checkpoint store and DeskPet interrupt metadata; preserve dataset/experiment behavior without framework types. |
| Legacy compatibility | `JsonPlusSerializer` typed checkpoint blobs | Detect by explicit persisted type. An isolated no-pickle reader may expose allowlisted metadata/state for read-only legacy history only; every legacy nonterminal run becomes safely blocked and is never converted or resumed. |
| Packaging and guard | `pyproject.toml`, `uv.lock`, `deskpet-backend.spec`, runtime hook, dependency smoke | Remove LangGraph/LangChain direct and transitive dependencies, hidden-import collection and strict-msgpack hook; replace smoke with a source/import/clean-interpreter guard and record lock/frozen deltas. |

The replacement must not duplicate a general Pregel engine. The three registered workflows require sequential nodes, conditional routes, bounded loops, multi-source joins, exclusive interrupt barriers and stable per-domain map effects. They do not require arbitrary dynamic topology, distributed scheduling or cross-machine consensus. ToolRegistry, permissions, effect ledger, Artifact/Receipt delivery, Trace/Evaluation storage, Session delivery, lease/CAS and retention remain authoritative product services outside the scheduler. Their execution lifecycle adapters do not remain untouched: observer/progress/waiting/failed classification and evaluation execution must be rewired as native engine SPI.

Checkpoint compatibility is a versioned boundary. New rows use `checkpoint_type="deskpet-native-json-v1"` and a canonical snapshot with `engine_kind="deskpet-native"`, `snapshot_version=1`, `state`, `frontier`, `step`, `node_writes`, `interrupt`, `parent_checkpoint_id` and deterministic identity metadata. Completed legacy runs keep their durable run/event/trace/artifact history; an isolated no-pickle reader may decode allowlisted legacy values only for that read-only view. Every legacy nonterminal run is blocked with a safe recovery action and is never converted, resumed, silently restarted or used to execute a side effect.

The engine switch changes implementation identity. Native registrations publish new implementation/engine metadata and do not pretend to match a legacy LangGraph implementation hash. Terminal legacy runs remain queryable. Legacy nonterminal runs are not converted or resumed in this migration: startup deterministically moves them to `blocked` with `legacy_checkpoint_incompatible`, preserves their existing run/session/decision/pending-write rows as read-only evidence, closes no decision and executes no effect. A user retry creates a new native run through normal ingress, so decision nonce/version and Session run identity never cross engine identities. This is deliberately separate from the existing fork saga, whose `prepare_fork()` copies source workflow/version/hashes and therefore cannot be used as an engine migration transaction. Existing native fork restrictions remain fail-closed, with dangerous effects allowed only after the existing explicit confirmation.

## 15. Context OS V1 Runtime

Context OS V1 is the default request-context path. Both the bundled `config.toml` and `FeaturesConfig.context_os_v1` default to `true`. The single compatibility rollback is `context_os_v1=false`; it restores the preserved legacy assembler/preflight/schema path. ON and OFF are mutually exclusive for one request: Context OS does not shadow-run a second assembler, tool resolver or lossy compressor.

### 15.1 Owners and request flow

The production flow is:

```text
text/code/voice ingress
  -> SessionDB append (authoritative transcript)
  -> ContextAssembler
       typed fragments + ToolExposureIntent + task projection inputs
  -> ContextRequestPlanner.prepare_initial
       one catalog/policy resolve
       PreparedToolSet + fixed prefix/current turn/attachments/reserve
       SessionHistoryPlanner coverage plan
       common-safe provider-chain budget
  -> optional CoverageCompactionJob[]
       AgentLoop -> ContextCompressor -> ContextSegmentStore commit
       -> planner replan with the same PreparedToolSet
  -> AgentLoop
       exact prepared messages and logical tool set
       per-provider/model attempt re-budget
       provider transport
  -> ContextAttemptStore / ContextTrace
       planned -> sent -> terminal, matching usage and coverage facts
```

Ownership is deliberately narrow:

| Concern | Authoritative owner |
|---|---|
| Raw conversation and message identity | `SessionDB`; assembler top-k history is never treated as complete Session coverage. |
| Fragment lifecycle and stable-prefix candidates | `ContextAssembler` and typed `ContextFragment` metadata. |
| Initial whole-request plan and common provider-chain safe budget | `ContextRequestPlanner`; protected prefix, current turn, actual tool payload, attachment estimates and generation reserve are charged before history. |
| Lossy summarization | `ContextCompressor` only. The planner may return jobs but does not call an LLM or commit a summary. |
| Reactive execution, compaction ordering and post-compact remount | `AgentLoop`; snapshot flush precedes lossy compaction, and protected/stable fragments, active skills/path rules and the latest complete causal tail are remounted. |
| Provider-specific budget and request lifecycle | The immediate provider-attempt seam plus the public transport marker. Each candidate is re-estimated for its actual provider/model/window and `tools` or `None`; a budget failure cannot send and may continue to the next fallback candidate. |

Provider chains first plan against the candidate with the smallest effective input budget. The order is estimate, reversible history planning, required coverage compaction, replan, then recoverable block. Every actual attempt is checked again, so a provider-specific window or adapter difference cannot reuse an earlier provider's budget result.

Attachments remain provider message content blocks. `attachment_budget` creates body-free `AttachmentRef` records containing only message/content indexes, media type, byte size and estimate metadata; attachment bodies are neither copied into snapshots nor exposed by ContextTrace. Text, code, voice and subagent preparation all pass the resulting refs and token total into the same planner.

### 15.2 Snapshot and coverage persistence

`state.db` migration V18 owns two Context OS tables:

- `session_context_snapshots` stores a derived `TaskContextSnapshot` and a bounded prepared-tool summary. Workflow/goal/receipt/artifact stores remain authoritative; the snapshot is a recoverable projection and cannot promote pending evidence to completed state.
- `session_context_segments` stores raw/summary coverage nodes, continuous message-id ranges, source hashes, child lineage, token estimates and page-in references. `ContextSegmentStore` validates source hashes and commits summaries through CAS-safe operations.

Snapshot writes use distinct DB row revisions and typed `ContextSnapshotHandle`/`SnapshotWriteReceipt` values. Initial projection, activation and provider-attempt adapter updates settle asynchronous CAS outcomes before scope activation or transport; cancellation after a DB commit cannot make an unknown write authoritative.

For one Session, every eligible, undeleted user/assistant/tool message is covered exactly once. If all raw rows fit, all raw rows are loaded. Otherwise `SessionHistoryPlanner` selects a continuous raw tail plus valid summary nodes and page-in references. A reference does not count as content coverage. Gaps, overlaps, stale source hashes, broken tool-call causal groups or residual compaction jobs fail closed before provider transport. `session_history_page_in` is bound to the current runtime Session and pages only at complete causal-group boundaries.

### 15.3 Tool capability plane

`ContextAssembler` produces provider-neutral `ToolExposureIntent`. `ToolCapabilityResolver` reads one strict, session-aware catalog/policy snapshot and freezes an immutable `PreparedToolSet` containing exact direct schemas, bounded deferred references, deny decisions, registry/policy revisions and fingerprints. AgentLoop and diagnostics consume that same set; ON mode does not reduce it to names and reconstruct schemas later.

Deferred capabilities are exposed only through scoped `tool_search`, `tool_describe` and `tool_activate` bridges. Search cannot enumerate another Session or policy-hidden tools. Activation is an exclusive turn: eligibility, current spec hash, policy, budget and snapshot serialization are validated before the candidate revision is committed. Every provider attempt and real tool execution revalidates direct/activated references; MCP unregister/replace or policy drift makes the old reference stale and fails closed. Existing PermissionGate, durable effect authorization, receipts, artifacts, VerifyGate, timeouts and breakers remain downstream authorities.

### 15.4 Attempt diagnostics and rollback

`ContextAttemptStore` is a body-free, bounded in-memory diagnostic ring. It records `purpose`, Session/request/attempt identity, provider/model/window, adapter identity, message and tool hashes, fragment decisions, tool-set revisions, attachment/reserve attribution, compression resolution, Session coverage facts and matching authoritative usage. Auxiliary capability-gate/classifier/planner/compressor/research/workflow calls receive explicit purposes; provider bottom seams create an attempt if no complete outer attempt scope exists. The public transport coroutine changes `planned` to `sent` only after it obtains send control; completion, failure and cancellation close the same attempt.

ContextTrace renders these frozen facts, including raw/summary/reference coverage, gaps/overlaps/stale counts and page-in references, without credentials, full tool arguments, attachment bodies or sensitive file content.

Rollback is intentionally one-dimensional:

- `context_os_v1=true` is the shipped default and activates the planner, snapshot/segment stores, capability scope and attempt diagnostics.
- `context_os_v1=false` restores the preserved legacy `tools` names/order, legacy preflight/reactive behavior and legacy context-usage payload. It does not read Context OS snapshots/segments or register an attempt store.
- A startup log states the active owner, resolved window and migration version without prompt content.

Automated verification and benchmark results are recorded in [`../plans/2026-07-13-context-os-v1/results.md`](../plans/2026-07-13-context-os-v1/results.md). Real Windows UI E2E remains a separate evidence boundary and is never inferred from unit tests or protocol-level calls.
