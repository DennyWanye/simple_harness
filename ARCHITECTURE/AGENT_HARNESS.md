# DeskPet Agent Harness Architecture

> Last updated: 2026-07-20. Scope: request lifecycle, harness state, service wiring, subagent sidecars, and harness simplification evidence.

## Summary

DeskPet uses a product-specific agent harness rather than a generic agent framework. The main runtime is a single parent ReAct loop with strong local-product affordances: Tauri/WebSocket event bridging, permission popups, artifact cards, receipt-backed verification, session memory, and optional subagent/team sidecars.

The importable contract lives in `backend/deskpet/agent/harness_manifest.py`. Tests should use that module when they need stable names for lifecycle stages, service wiring, or known harness weaknesses.

## Harness simplification program status

R2 of the approved simplification plan is complete, but **execution ownership is unchanged**: text still enters through `main.py::_run_chat`, Voice keeps its legacy adapter, AgentLoop remains the short-task driver, and durable workflows keep their current owners.

- The production parity census freezes 141 legacy event/sink/control callsites with `unmapped_count=0`, plus direct behavior fixtures for canonical messages, capability-filtered toolsets, read-only/Code/DeepResearch/PPT routing, Voice tags/TTS-facing text, SessionDB delivery, AutoResume, permission restoration and codify.
- Locked Git-object LOC manifests reproduce phase-0 `21,563` and rollback `33,228` lines. Rename/copy, dirty/untracked, Unicode paths, excluded-directory moves and rewritten moves outside `backend/` are fail-closed.
- The canonical schema-2 benchmark completed 10,000 real `RunKernel.start → UoW.finalize → RunKernel.close` lifecycles with 10,000 terminal rows/events, zero completed-run strong references and about 1.40 MiB RSS growth.
- Integrated verification: `176 passed, 9 xfailed`; parity census `141/141` mapped; current LOC `33,228` with zero unknown classifications.

The approved target is documented in [`plans/2026-07-20-agent-harness-simplification/target-architecture.md`](../plans/2026-07-20-agent-harness-simplification/target-architecture.md). R1.2 keeps production at `legacy/0` while adding the production-shaped activation control plane to the existing `SqliteExecutionUnitOfWork`. It durably scans/registers the legacy drain manifest and advances only through CAS transitions `legacy → draining → activated(generation=1) → open`. Starts are fail-closed during `draining/activated`; rows created before activation remain immutable `legacy/0`, while rows created after `open` are immutably fenced as `kernel/current-generation`. The production bootstrap still does not call activation before the R6 cutover.

The same UoW now owns product-neutral continuation JSON through strict `save/load/delete` continuation-version CAS. `promote_and_persist_batch_boundary` atomically promotes a bounded ephemeral ReAct run, persists the complete continuation, optional `DecisionOpen`, waiting event, and its deliveries; an existing durable run is not promoted twice. A new durable row advances the ephemeral version once, and a newly appended waiting event advances it once more; the returned record is the authoritative post-commit version. Activation verification covers concurrent/repeated CAS, illegal owner writes, non-empty drain refusal, four crash/restart windows, and restart recovery. Continuation verification covers complete JSON recovery, stale save/delete rejection, wrong-run decision rejection, idempotent replay, and four whole-transaction promotion crash windows. Focused harness/workflow schema regression: `231 passed, 9 xfailed`.

R1.2 removes the reverse dependency from `ToolRegistry` to the test-only
`harness.tool_executor`: the registry no longer exposes
`prepare_execution_call`/`execute_call` and depends directly on the existing
`PreparedToolCall`/`NormalizedToolOutcome` workflow primitives. The canonical
`prepare_call`/`execute_prepared` path now accepts a separate trusted
`ToolExecutionContext`, rejects model-owned host fields, avoids the legacy
session-context merge, revalidates call/effect/grant bindings, and invokes
`context_handler` without putting host identity into model JSON. A temporary
harness-side bridge preserves dormant pre-cutover tests; it is not a new
production owner and is deleted by the Driver/UoW owner-collapse slice.
Verification: full harness `199 passed, 9 xfailed`; adjacent workflow effect
and production wiring `33 passed`; canonical registry-focused tests `45 passed`.

R1 owner-collapse now has one durable state owner: `SqliteExecutionUnitOfWork`.
The former `ExecutionLedger`, decision store, effect store, and continuation
store modules have been deleted instead of retained as facades. Short ReAct
runs stay only in Kernel's bounded live index and create zero execution rows;
the first tool, decision, or delegate boundary atomically promotes the run and
persists its complete continuation through the UoW. After promotion Kernel
drops its ephemeral record and reads the UoW-owned version. The execution port
surface is one `ExecutionUnitOfWork` protocol, and the duplicate execution
`ToolOutcome` contract and unused codecs are gone. Production remains fenced at
`legacy/0`; this slice does not activate the Kernel owner.

R1 now exports eight stable atomic-operation contracts and 33 enumerated crash
windows. The existing UoW bodies were split into same-connection primitives;
there is no second ledger or nested connection. Decision resolution advances
its continuation/event with any one-shot grant; grant consumption claims a
fenced execution effect/attempt; settlement commits attempt, effect link,
continuation, event and delivery; child apply advances the parent and acks the
inbox; child finalization commits the terminal event and parent signal. The
durable-command-first child saga intentionally commits the immutable command
intent before scheduling atomically creates child+link and marks it scheduled.
TeamStore now atomically claims a task with a stable cross-DB child-command
outbox, which a reconciler can replay and acknowledge. Goal projection remains
a separate domain store and is therefore represented as a durable finalization
delivery, not a fake cross-database SQL link.

The machine-readable matrix fixes each window's durable before/after state,
restart actor, idempotency key and run/boundary/decision/effect/attempt/child/
link/inbox/event/delivery/external-write counts. Every hook is killed before
commit, reopened through a fresh store, replayed, and compared under
`required == tested == exported`; focused matrix verification is `33 passed`,
adjacent UoW/Decision/Child/Ledger/Team regression is `148 passed`, and the
integrated harness-simplification suite is `217 passed, 9 xfailed`. Production
ownership remains `legacy/0` until the R6 activation commit.

This atomicity slice grows `execution_uow.py` from 3,854 to 4,467 physical
lines (`+613` net after extracting the old decision/grant/child-signal bodies)
and TeamStore by `+176` net lines. It is therefore correct for crash safety but
is **not** the final simplification shape: R2～R4 must split product-neutral
records/SQL primitives from orchestration and delete compatibility surfaces so
the R5 combined core/UoW gate reaches `≤2,800` lines. On the integrated
owner-collapse head, the R1 adjusted-total gate is `32,766 <= 33,228`, with
zero unknown classifications.

R2 turns the old text ingress into a smaller composition shell without a
Kernel/Driver/Workflow/Voice cutover. `ProductTurnPreparer` now owns the typed
`prepare_context -> route_intent -> plan_decision` stages. It returns
venue-neutral `ProductDomainCommand` values instead of writing WebSocket,
SessionDB, activity, or plan-waiter state. `RunPresenter` consumes the existing
legacy `AgentEvent` stream through explicit live/durable/domain handler
registries and owns terminal feedback/billing. Product commands are projected
through the narrow `ProductDomainSink`; only `LegacyProductDomainSink` knows
the current WebSocket, peer broadcast, SessionDB, plan waiter, and read-only
compatibility APIs. A separate `RunEventPresentationAdapter` protocol reserves
the future typed RunEvent boundary; R2 does not claim that cutover.

The frozen R0 census remains byte-for-byte unchanged at 141 callsites. A new
141-entry old-to-current mapping records both source hashes and callsites; the
current scan has the same capability/kind distribution, including 57 WS sends.
Both dual-send helpers are AST-validated, so deleting either origin send or
peer broadcast fails closed. Integrated verification: harness `232 passed,
9 xfailed`, census `141/141` with zero unmapped items, adjusted LOC
`32,490 <= 33,228` with zero unknown classifications; adjacent R2 branch
agent/context/problem/main suites are `335 passed`, and the three R2 production
modules total 611 physical lines (below the 1,000-line budget).

R3 recovery fencing foundation is now on schema v8. Each durable run has a
short-lived recovery owner/epoch/expiry separate from the deployment-level
`owner_generation`. `claim_recovery`, `renew_recovery`, and `release_recovery`
support exclusive claims and expired takeover; stale epochs fail closed. A
recovery-fenced `append_event` validates the lease and writes the event in the
same `BEGIN IMMEDIATE` transaction, closing the check-then-write race. Existing
v7 rows migrate with unchanged product fields and an unclaimed lease. Focused
verification is `26 passed`; the integrated harness is `235 passed, 9 xfailed`
and adjusted LOC is `32,809 <= 33,228`. Production ownership remains
`legacy/0`; Kernel recovery is not activated yet.

## Request Lifecycle

The outer text-chat order is `ws_ingress -> ProductTurnPreparer.prepare_context
-> ProductTurnPreparer.route_intent -> ProductTurnPreparer.plan_decision ->
agent_loop -> RunPresenter`. Tool dispatch, completion gates,
subagent activity, and WS egress are nested/re-entrant inside the AgentLoop
event stream; the table order is vocabulary order, not a once-only sequence.

| Stage | Owner | Role | Persistence | Recovery boundary |
|---|---|---|---|---|
| `ws_ingress` | `backend/main.py::_run_chat` | Receive chat, persist user message, resolve session/provider context. | Session | Conversation record survives; coroutine does not. |
| `context_assembly` | `deskpet.agent.turn_preparer.ProductTurnPreparer.prepare_context` | Compose ContextAssembler, attachments, sentinel, supervisor hints and summary reinjection. | Ephemeral | Falls back to a user-only message list. |
| `pre_loop_problem_pipeline` | `ProductTurnPreparer.route_intent` | Compose intent/contradiction analysis and return venue-neutral domain commands. | Ephemeral | Safe-fail returns an empty routed result. |
| `agent_loop` | `backend/agent/agent_loop.py::AgentLoop.run` | ReAct loop, provider fallback, tool calls, final/error events. | Ephemeral | Auto-resume can start a new run; no exact graph-node checkpoint. |
| `tool_dispatch` | `deskpet.tools.registry.ToolRegistry.execute_tool` | Permission gate, timeout, envelope, artifact, receipt, breaker accounting. | Session | Receipts/artifacts survive; in-flight handlers do not resume. |
| `completion_gates` | `AgentLoop` end-turn gates | completion_probe, VerifyGate, goal_checker, external evaluator, self-check. | Session | Receipt ledger and goal store survive; nudges are per run. |
| `subagent_sidecar` | `subagent_scheduler`, `subagent_registry`, `team/spawn_team` | Optional bounded sidecar subagent/team execution. | Session (mixed internals) | Subagent coroutines are process-local; TeamStore data survives, but workers are not automatically reclaimed. |
| `ws_egress` | `deskpet.agent.run_presenter.RunPresenter` + `LegacyProductDomainSink` | Translate legacy AgentEvents/product commands, persist assistant/tool state, and dual-send live frames. | Session | Persisted messages reload; streamed deltas are best-effort. |

## Current Weaknesses And Fix Direction

| Weakness | Current evidence | Hardening direction |
|---|---|---|
| Control flow is dispersed | Request handling crosses `main.py`, `ProblemHandlingPipeline`, `ContextAssembler`, `AgentLoop`, and `ToolRegistry`. | Use `REQUEST_LIFECYCLE` as a tested stage vocabulary and keep owner paths current. |
| Parent ReAct is not a durable state machine | Short/general chat still cannot restart from a precise node after process death. DeepResearch, PPT Pro, and Complex Code now use explicit checkpointed graphs. | Keep short chat in ReAct; move only additional long, bounded workflows behind the durable graph contracts. |
| `main.py` still owns the production shell | Provider resolution, workflow routing and AgentLoop construction remain in `_run_chat`, although preparation and presentation policy moved in R2. | Keep the R2 typed seams stable; cut execution ownership only in the later Driver/Kernel milestone. |
| Flag/service wiring is complex | Feature flags and `service_context` placeholders have repeatedly drifted. | Test every declared harness service against `ServiceContext.register/get`. |
| Multi-agent is sidecar, not core | `spawn_team` and nonblocking subagents run around the parent loop. | Keep sidecar semantics explicit; do not present it as a CrewAI-style primary runtime. |
| ReAct observability remains partly grep-heavy | Durable workflows have structured Trace/Evaluation records and Session progress, while legacy ReAct still relies on mixed logs/events. | Continue converging legacy events on the trace vocabulary without coupling product UI to raw logs. |
| Testing is expensive | True UI E2E is required for UI/desktop behavior but too costly for pure wiring drift. | Route manifest/logic changes to pytest; reserve windows-mcp for UI or real desktop behavior changes. |
| Low framework reuse | Harness is tightly coupled to DeskPet, Tauri, permissions, artifacts, receipts, and local tools. | Preserve product runtime; borrow LangGraph/CrewAI patterns selectively. |

## Framework Comparison

LangGraph is a low-level orchestration runtime for long-running stateful agents. Its best-fit lesson for DeskPet is explicit graph state, durable execution, human-in-the-loop checkpoints, and state transition traces. DeskPet should borrow this for future long-running workflows such as PPT Pro, DeepResearch orchestration, or multi-stage code tasks.

CrewAI provides crews and flows: role/task/process-centered multi-agent collaboration plus event-driven flow control and shared state. Its best-fit lesson for DeskPet is a clearer DSL for teammate roles, task ownership, and flow-level state, but DeskPet should keep its local ToolRegistry, permission gate, artifact, and VerifyGate stack.

The current recommendation is not a wholesale migration. DeskPet's product-specific local execution layer is valuable. The hardening path is to formalize the existing lifecycle, then incrementally extract long-running or team-oriented workflows behind explicit state/flow interfaces.

## Durable Long-Task Layer

As of 2026-07-11, DeepResearch, PPT Pro, and Complex Code are explicit v1 graphs with shared state, checkpoint recovery, durable decisions, structured Trace/Evaluation records, and Outbox-backed progress projection. The UI consumes a small public stage vocabulary rather than raw trace/log payloads. Stable workflow event ids make Session history and live WebSocket delivery converge without duplicate progress messages.

PPT Pro additionally treats a complete raster page as the rendering unit: the image provider creates a text-free background, DeskPet deterministically flattens approved copy into the final 16:9 page image, and the PPTX assembler inserts exactly one picture per slide. Legacy templates and direct `ppt_create` remain compatible paths.

## Follow-up

保留 DeskPet 现有 Harness，把复杂长任务逐渐改造成 LangGraph 式流程，再补上 LangSmith 式观测能力。

## Test Routing

- Pure manifest/docs/service wiring: `cd backend && python -m pytest tests/test_agent_harness_manifest.py tests/test_agent_harness_docs.py tests/test_agent_harness_main_callsite_contract.py -q`
- Adjacent harness logic: `cd backend && python -m pytest tests/test_agent_harness_manifest.py tests/test_problem_pipeline.py tests/test_deskpet_context_assembler.py tests/test_subagent_nonblocking.py -q`
- UI-visible event or permission changes: use the project windows-mcp discipline and store results under `plans/manual-results-*`.
