# DeskPet Agent Harness Architecture

> Last updated: 2026-07-20. Scope: request lifecycle, harness state, service wiring, subagent sidecars, and harness simplification evidence.

## Summary

DeskPet uses a product-specific agent harness rather than a generic agent framework. The main runtime is a single parent ReAct loop with strong local-product affordances: Tauri/WebSocket event bridging, permission popups, artifact cards, receipt-backed verification, session memory, and optional subagent/team sidecars.

The importable contract lives in `backend/deskpet/agent/harness_manifest.py`. Tests should use that module when they need stable names for lifecycle stages, service wiring, or known harness weaknesses.

## Harness simplification program status

R0 of the approved simplification plan is complete, but **production ownership is unchanged**: text and Voice still use the legacy paths described below, while the retained WI-1～WI-11 execution/harness code remains test-only foundation.

- The production parity census freezes 141 legacy event/sink/control callsites with `unmapped_count=0`, plus direct behavior fixtures for canonical messages, capability-filtered toolsets, read-only/Code/DeepResearch/PPT routing, Voice tags/TTS-facing text, SessionDB delivery, AutoResume, permission restoration and codify.
- Locked Git-object LOC manifests reproduce phase-0 `21,563` and rollback `33,228` lines. Rename/copy, dirty/untracked, Unicode paths, excluded-directory moves and rewritten moves outside `backend/` are fail-closed.
- The canonical schema-2 benchmark completed 10,000 real `RunKernel.start → UoW.finalize → RunKernel.close` lifecycles with 10,000 terminal rows/events, zero completed-run strong references and about 1.40 MiB RSS growth.
- Integrated verification: `176 passed, 9 xfailed`; parity census `141/141` mapped; current LOC `33,228` with zero unknown classifications.

The approved target is documented in [`plans/2026-07-20-agent-harness-simplification/target-architecture.md`](../plans/2026-07-20-agent-harness-simplification/target-architecture.md). R1 begins with an additive v7 schema while production remains `legacy/0`; no production owner changes before the R6 activation gate.

## Request Lifecycle

The outer text-chat order is `ws_ingress -> context_assembly ->
pre_loop_problem_pipeline -> agent_loop`. Tool dispatch, completion gates,
subagent activity, and WS egress are nested/re-entrant inside the AgentLoop
event stream; the table order is vocabulary order, not a once-only sequence.

| Stage | Owner | Role | Persistence | Recovery boundary |
|---|---|---|---|---|
| `ws_ingress` | `backend/main.py::_run_chat` | Receive chat, persist user message, resolve session/provider context. | Session | Conversation record survives; coroutine does not. |
| `context_assembly` | `deskpet.agent.assembler.ContextAssembler` | Task classification, memory/skill/tool/persona fan-out, scoped tool schemas. | Ephemeral | Falls back to chat/default policies. |
| `pre_loop_problem_pipeline` | `deskpet.agent.problem_pipeline.ProblemHandlingPipeline` | Intent/contradiction analysis, chitchat short-circuit, clarification after context assembly. | Ephemeral | Safe-fail returns empty pre-loop result. |
| `agent_loop` | `backend/agent/agent_loop.py::AgentLoop.run` | ReAct loop, provider fallback, tool calls, final/error events. | Ephemeral | Auto-resume can start a new run; no exact graph-node checkpoint. |
| `tool_dispatch` | `deskpet.tools.registry.ToolRegistry.execute_tool` | Permission gate, timeout, envelope, artifact, receipt, breaker accounting. | Session | Receipts/artifacts survive; in-flight handlers do not resume. |
| `completion_gates` | `AgentLoop` end-turn gates | completion_probe, VerifyGate, goal_checker, external evaluator, self-check. | Session | Receipt ledger and goal store survive; nudges are per run. |
| `subagent_sidecar` | `subagent_scheduler`, `subagent_registry`, `team/spawn_team` | Optional bounded sidecar subagent/team execution. | Session (mixed internals) | Subagent coroutines are process-local; TeamStore data survives, but workers are not automatically reclaimed. |
| `ws_egress` | `backend/main.py` event bridge | Translate AgentEvents to UI events and persist assistant/tool messages. | Session | Persisted messages reload; streamed deltas are best-effort. |

## Current Weaknesses And Fix Direction

| Weakness | Current evidence | Hardening direction |
|---|---|---|
| Control flow is dispersed | Request handling crosses `main.py`, `ProblemHandlingPipeline`, `ContextAssembler`, `AgentLoop`, and `ToolRegistry`. | Use `REQUEST_LIFECYCLE` as a tested stage vocabulary and keep owner paths current. |
| Parent ReAct is not a durable state machine | Short/general chat still cannot restart from a precise node after process death. DeepResearch, PPT Pro, and Complex Code now use explicit checkpointed graphs. | Keep short chat in ReAct; move only additional long, bounded workflows behind the durable graph contracts. |
| `main.py` glue is too thick | It wires provider resolution, services, flags, pipeline injection, event bridging, and UI round trips. | Move by ownership in small slices; guard the service contract first with manifest tests. |
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
