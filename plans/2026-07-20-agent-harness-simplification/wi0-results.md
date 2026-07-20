# WI-0 — Counterexample and measurement baseline

Date: 2026-07-20

Branch: `codex/harness-wi0`

Starting commit: `961c7d340334927acfa07cfaffe071510f56cff3`

## Green baseline before WI-0

```powershell
$harnessTests = @(rg --files backend/tests | Where-Object { $_ -match 'test_agent_harness_.*\.py$' })
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest $harnessTests -q
```

Result: `30 passed in 4.70s`.

## Expected-red contracts

The counterexamples use strict `xfail`. Normal regression stays green, while
`--runxfail` demonstrates that the old production behavior violates each
contract.

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest backend/tests/harness_simplification -q
F:\projects\deskpet\backend\.venv\Scripts\python.exe -m pytest backend/tests/harness_simplification -q --runxfail
```

Results:

- Normal run: `9 xfailed in 1.27s`, exit `0`.
- Proof run: `9 failed in 1.27s`, exit `1` as expected.
- Frozen counterexamples: mixed safe/unsafe source barriers, two unsafe calls
  overlapping, reserved host context override, Text and Voice `ok:false`
  projected as success, cross-session subagent injection, Code-venue Research
  and PPT route sinks, and `/stop` omitting durable workflow cancellation.

## Mutable-owner audit

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts/acceptance/harness_owner_audit.py --json
```

Stable JSON result: `baseline=15`, `survivor_count=15`,
`new_equivalent_count=0`. The baseline JSON freezes 55 broad AST candidates so
later synonymous dict/set/queue/task owners are reported without redefining the
phase-0 snapshot.

## Measurement baseline

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts/bench/harness_baseline.py --iterations 40 --output plans/2026-07-20-agent-harness-simplification/baseline.json
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts/bench/harness_baseline.py --iterations 40 --compare plans/2026-07-20-agent-harness-simplification/baseline.json
```

The second run returned `comparison.passed=true` for every reproducibility
check. Frozen structural values are:

- orchestration LOC: `21,563`;
- workflow one-node checkpoint lifecycle: `3` transactions and `505` logical
  serialized bytes;
- completed-run retention probe: `10,000` strong references remain after the
  completion queue is drained.

Machine-dependent timing and RSS values are stored in `baseline.json` and are
compared with explicit noise allowances.

## Measurement boundary

There is no RunKernel on the phase-0 branch, so the local-start baseline is the
legacy AgentLoop pre-provider cost and is explicitly labelled as such. The
checked-in TTFT measurement uses a controlled 5 ms provider to make repeated
automated runs comparable. A real-provider end-to-end TTFT baseline requires a
live application trace and credentials; the JSON marks that evidence as
`requires external live trace capture` rather than presenting the controlled
probe as real E2E evidence.

ARCHITECTURE updates are intentionally deferred to the integration worktree as
directed for this isolated WI-0 slice.
