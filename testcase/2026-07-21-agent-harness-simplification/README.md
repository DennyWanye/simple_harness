# Agent Harness R6/R7 Test Cases

> Scope: R5.5 durable admission, R6 production cutover and R7 release evidence.
> Current production owner: `kernel/1`, activation `open/generation=1`.

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

## R6 cutover gates

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts\acceptance\legacy_cutover_audit.py --cutover --manifest plans\2026-07-20-agent-harness-simplification\legacy_cutover_spans.json --dynamic-stacks plans\2026-07-20-agent-harness-simplification\r6-live-stacks.json
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts\bench\harness_baseline.py --r6-final-gate --r55-budget plans\2026-07-20-agent-harness-simplification\r55-admission-budget.json --loc-only
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts\acceptance\last_mile_smoke.py --no-vitest --no-cargo
```

## Manual R7 status

| Scenario | Current evidence |
|---|---|
| S-1 Text read-only ReAct | **PASS** — real Computer Use click/type/send; UI returned `HARNESS-R6-FINAL-OK`; relay HTTP 200; activation `open/1` |
| S-2 Code write/approval | Automated production/recovery coverage PASS; fresh R6 Windows case pending |
| S-3 DeepResearch | Automated route/workflow/recovery coverage PASS; fresh R6 Windows case pending |
| S-4 PPT approval/artifact | Automated route/workflow/decision coverage PASS; fresh R6 Windows case pending |
| S-5 Voice short/long handoff | Automated ASR/TTS/barge-in/route coverage PASS; fresh spoken Windows case pending |
| S-6 Multi-session cancel/isolation | Automated cross-session and cancel-tree coverage PASS; fresh Windows case pending |
| S-7 Permission/late effect | Automated failure/unknown/reconcile coverage PASS; fresh Windows case pending |

Never infer S-2 through S-7 from the S-1 run. Each fresh Windows case must keep
the repository's screenshot/action/log discipline and perform exact root PID
cleanup afterward.
