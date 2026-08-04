# Durable Graph Workflows And Trace - Manual E2E

Status: skeleton created in Wave A; results and evidence are filled during Task 22.

## Evidence Rules

For every case record the physical click coordinates before acting, a before/after screenshot, the visible result, backend log lines, and the `run_id`, `trace_id`, `request_id`, and `turn_id` when available. Tests must use real UI input and clicks; direct WebSocket injection is not accepted.

## Cases

| ID | Scenario | Expected result | Evidence |
|---|---|---|---|
| DR-01 | Start a DeepResearch task | One graph run starts; plan/search/fetch/score/gap/synthesize/citation nodes are visible and the final report is delivered once. | Pending |
| DR-RECOVER-01 | Stop the app after search and restart during fetch | The same run resumes from its safe checkpoint and retains completed search results. | Pending |
| PPT-01 | Start PPT Pro and approve the outline | The run pauses durably on outline review, resumes after one click, renders and delivers one valid deck. | Pending |
| PPT-RECOVER-01 | Restart while outline approval is open | The open decision is restored; duplicate/expired responses cannot execute twice. | Pending |
| CODE-01 | Submit a complex code change | The task uses the code graph, persists plan/todos/test results, performs audit/fix as needed, and completes once. | Pending |
| CODE-CHAT-01 | Ask a read-only code question | The existing ReAct path answers without creating a graph run. | Pending |
| CODE-RECOVER-01 | Restart after LLM proposal and before tool execution | The proposal checkpoint is reused without calling the LLM again; committed effects are not duplicated. | Pending |
| TRACE-01 | Open Runs/Trace for each workflow type | Run list, graph/timeline, spans, errors, checkpoints and evaluations render with sensitive fields redacted. | Pending |
| FORK-01 | Fork a safe root checkpoint | A new run/branch is created; the source run is unchanged and each branch advances from its own head. | Pending |
| FORK-DANGER-01 | Fork across a side-effecting node | The UI requires explicit confirmation before re-execution. | Pending |
| EVAL-01 | Submit a human score and compare experiments | The score persists with evaluator/version metadata and comparison results are visible after restart. | Pending |
| UI-START-01 | Repeat the same accepted start request | Exactly one run is created and one accepted handoff is shown. | Pending |
| UI-HANDOFF-01 | Trigger a single async workflow call, then a mixed async/tool batch | The single call ends the chat turn while its workflow card keeps running; the mixed batch is rejected before any call executes. | Pending |
| UI-CONC-01 | Run two workflows in separate sessions concurrently | Events, finals, artifacts and working state remain associated with the correct session/run. | Pending |
| UI-DELETE-01 | Delete a normal session while delivery is pending | The deleted session stays deleted and late workflow messages do not recreate it. | Pending |
| UI-CODE-DELETE-01 | Delete a running code project | Todos/finals from the deleted project do not reappear; the run reaches a diagnosable terminal/cancel state. | Pending |
| UI-DYNAMIC-01 | Let a complex code run encounter an unapproved plugin/MCP write tool | A durable allow-once/continue/cancel decision appears; no write runs before approval. | Pending |

## Result Summary

Not run yet.
