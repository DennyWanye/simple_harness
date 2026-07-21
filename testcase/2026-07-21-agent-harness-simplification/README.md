# Agent Harness R5.5 Test Cases

> Scope: dormant durable admission and R6 cutover readiness.
> Production owner during these cases: `legacy/0`.

## Automated cases

| ID | Scenario | Expected result |
|---|---|---|
| TC-1 | Create a pending admission, then approve it twice concurrently or sequentially | Exactly one launch is claimed and started; the identical replay is idempotent |
| TC-2 | Reject, cancel, or expire before launch claim | No Driver starts; the matching terminal event, Goal projection, and delivery are committed once |
| TC-3 | Recover from `launch_claimed` or `launched` | Idempotent providers reuse the same launch operation; non-idempotent ambiguity fails closed as `launch_unknown`; terminal+launched is a no-op |
| TC-4 | Consume a precreated workflow admission | Workflow starts only after the durable claim; replay does not schedule a second workflow |
| TC-5 | Replay a decision with changed fields or a conflicting allow/cancel | Exact duplicates are accepted; changed/conflicting signals are rejected by the persisted resolution fingerprint |
| TC-6 | Open the dormant ProductVenue composition for the same trusted request twice | Trusted identity is recovered before product preparation; Kernel remains the authoritative start-time TOCTOU check |
| TC-7 | Run authority, parity, cutover-readiness, and LOC gates | DML1, starters23, fault39, public ops6, authority counts 1/1/1, parity 141/141, no unknown LOC, and all R5.5 caps pass |

## Evidence commands

Run from the repository root using the shared backend virtual environment:

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest backend\tests\harness_simplification
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts\acceptance\harness_authority_audit.py --check --json
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts\acceptance\harness_parity_census.py --check-mapping
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts\acceptance\legacy_cutover_audit.py --readiness --manifest plans\2026-07-20-agent-harness-simplification\legacy_cutover_spans.json
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts\bench\harness_baseline.py --loc-only --r55-construction-gate
```

After every command, enumerate only `python/pythonw/node/cargo/rustc/deskpet`
processes whose command line contains this exact worktree path, terminate those
left by the command, and require `cleanup_remaining=0`.

## Manual test routing

No Windows UI case is claimed for R5.5 because the new composition remains
dormant and the production owner is still `legacy/0`. Treating the legacy UI as
proof of the dormant path would be indirect evidence. R6 must run real simulated
click tests after activation, including Text and Voice starts, approval,
cancellation, restart recovery, duplicate input, and final delivery.
