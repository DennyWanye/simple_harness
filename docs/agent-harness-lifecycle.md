# DeskPet Agent Harness Lifecycle

This document is the developer-facing companion to
`ARCHITECTURE/AGENT_HARNESS.md` and
`backend/deskpet/agent/harness_manifest.py`.

## Lifecycle

The canonical text-chat lifecycle has a linear outer path and a re-entrant
inner loop:

```text
ws_ingress
  -> context_assembly
  -> pre_loop_problem_pipeline
  -> agent_loop
       <-> tool_dispatch (zero or more times)
       <-> completion_gates (at end-turn candidates)
       <-> subagent_sidecar (scheduled/drained at safe boundaries)
       -> ws_egress (for each emitted event)
```

The tuple order in `REQUEST_LIFECYCLE` is canonical vocabulary order, not a
claim that nested entries run once or strictly after `agent_loop`.

Use the ids from `REQUEST_LIFECYCLE` when adding trace, metrics, docs, or
tests. Do not invent a parallel vocabulary in logs.

## Service Registration

`ServiceContext.register()` and `ServiceContext.get()` are the only supported
service container APIs. Harness-sensitive service names are declared in
`HARNESS_SERVICES`. Tests verify that every declared service is whitelisted by
`context._VALID_SERVICES`, so adding a new harness service means updating the
manifest and the whitelist together.

## Agent Factory

`build_agent` in `backend/main.py` is the production factory for `AgentLoop`.
New AgentLoop constructor dependencies should be grouped by responsibility and
covered by a focused wiring test. Avoid adding one more implicit
`service_context.get()` call without a manifest entry or test.

## Context Assembly

`ContextAssembler` runs before the problem pipeline and parent loop on the
production text-chat path. Its flow is:

```text
classify user request -> choose AssemblyPolicy -> fan out components
  -> allocate budget -> build ContextBundle -> write decisions trace
```

Policy drift is a common harness failure mode. If a policy exposes tools, the
`tool` component must run; if a component is listed in a policy, the default
assembler must register that component.

## AgentLoop

`AgentLoop.run()` owns the ReAct loop and yields typed events. It should remain
wire-agnostic: WebSocket payloads, SessionDB writes, and UI-specific mapping
belong to the adapter layer in `main.py`.

Major gates:

- `TerminationGate` for hard stop reasons.
- `ContextManager` for budget and tool-result truncation.
- `completion_probe`, `VerifyGate`, `goal_checker`, external evaluator, and
  `SelfCheckGate` for completion quality.

## Subagent Sidecar

Subagents are sidecars around the parent `AgentLoop`, not the primary runtime.
`SubagentScheduler` owns bounded concurrency. `SubagentRegistry` owns process
local run records, `completion_queue`, and cancellation cascade. TeamStore task,
message, and permission data is SQLite-backed, but teammate execution is still
process-local and is not automatically reclaimed after restart. The parent loop
drains `completion_queue` at safe turn boundaries and emits
`SubagentCompletionEvent` once per completion.

## Trace And Recovery

Iteration trace output, when enabled, is written under the user data trace area
as JSONL keyed by task/run id. Trace events should use lifecycle stage names
from the manifest. Today DeskPet has reactive recovery (`auto_resume`) rather
than LangGraph-style exact checkpoint resume. Long-running workflows should be
the first candidates for explicit checkpoint/state graph extraction.

## Test Routing

- Manifest, ServiceContext, policy, and subagent queue contracts: pytest.
- Event schema mapping, artifact cards, permission popups, or desktop UI
  interaction: windows-mcp true E2E with screenshots and backend logs.
- Do not use protocol-level WebSocket injection as a substitute for UI testing
  when the user-facing behavior changes.
