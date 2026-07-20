# R3 Recovery Fence Hardening Results

> Date: 2026-07-21
> Status: PASS; production owner remains `legacy/0`.

## Production facts

- Recovery ownership is an explicit four-field execution token: `run_id`,
  `owner`, `epoch`, and `expires_at`.
- Runtime renews before constructing the Driver recovery iterator, before its
  first `anext`, and by heartbeat during long operations.
- ReAct continuation, effect claim/settlement, child scheduling/signal apply,
  event, and terminal transactions validate the token inside their own
  `BEGIN IMMEDIATE` transaction.
- Child boundary save, child event, and inbox acknowledgement commit together.
- Workflow recovery validates the execution token and claims the native
  `RunFence` in one SQLite transaction. Runner consumes that preclaimed fence
  and does not claim again.
- A recoverable tool effect is claimed before external execution. If takeover
  occurs while the call is running, the old settlement raises
  `StaleRecoveryLease` and cannot advance the boundary.
- ReAct resume reconstructs one canonical `DriverStart`, preserving its frozen
  capability snapshot so a resumed legacy collaborator keeps the same tool
  allow-list.
- Child accepted/terminal responses enter canonical model input as host system
  messages in the same child-boundary/event/inbox-ack transaction. Their
  durable `signal_id` makes crash recovery model-visible exactly once. A
  root-terminal child retains its terminal intent until Kernel materializes
  the parent terminal, closing the post-ack crash window.
- New ReAct turns default to scoped `UNKNOWN` completion evidence. Successful
  tool outcomes resolve exact run/turn/call/effect/artifact identity through
  the UoW; both legacy VerifyGate and pipeline SelfCheck fail closed instead
  of reusing same-name receipts from an older session. Calls without this
  scoped host input retain legacy compatibility.
- Durable boundaries retain the trusted run context/spec needed for a resumed
  model to prepare a second tool. Existing continuation version and the active
  recovery lease fence that new batch; takeover makes the stale resume fail.
  Multi-tool batches currently resolve as scoped `UNKNOWN` as a whole rather
  than trusting one partial outcome.

## Fault-window evidence

Seven new negative/concurrency tests cover same-owner claim idempotency,
first-yield renewal order, stale continuation/terminal, stale effect claim,
workflow handoff rollback/idempotency, takeover during a tool call, and stale
child boundary/event/inbox acknowledgement. Existing stale event coverage is
also retained.

## Gates

- Focused recovery/Kernel/ReAct/child/workflow/tool/bootstrap: `71 passed`.
- Focused ReAct/scoped/Kernel regression: `58 passed`.
- Full harness simplification: `275 passed, 9 xfailed`.
- Expanded AgentLoop verification adjacency: `59 passed`.
- Owner audit: exit 0; `missing=[]`.
- Kernel: 631 physical lines (`<=650`).
- Adjusted orchestration LOC: `33,223 <= 33,228`; unknown classifications: 0.

No production activation or owner switch is included in this slice.
