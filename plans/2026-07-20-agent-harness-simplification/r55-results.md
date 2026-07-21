# R5.5 Durable Admission / Cutover Readiness Results

> Date: 2026-07-21
> A-source commit: `69c6980ae7297997fdffd3c49770adbf0c7e5c80`
> Production owner after this slice: `legacy/0`

## Outcome

R5.5 is complete. The dormant ProductVenue path now enters the existing Kernel
through one durable admission boundary instead of an in-memory approval gap.
This slice deliberately does not activate the production owner; the atomic
owner switch remains R6 work.

The durable phase chain is:

```text
pending -> accepted_start_pending -> launch_claimed -> launched
       \-> rejected | cancelled | expired | launch_unknown
```

The same UoW authority owns association, waiting, decision resolution, launch
claim, provider footprint, and terminal deliveries. `resolution_fingerprint`
locks exact replay semantics: an identical signal is idempotent, while a
changed or conflicting response fails closed. A second approval rereads the
authoritative run under the per-run start lock, so a fast first launch that has
already terminalized cannot be mistaken for an unknown phase.

Idempotent recovery reuses the stable `launch_operation_id`; non-idempotent
ambiguous recovery emits one `launch_outcome_unknown` final. `run.final` is the
canonical generic terminal event, historical terminal kinds remain compatible,
and terminal hydration keeps the single LiveRun/task reference valid until the
task actually finishes.

## Machine gates

| Gate | Result |
|---|---:|
| Raw orchestration LOC | `34,756 <= 34,800` |
| Migration-adjusted LOC | `34,247 <= 34,250` |
| Core LOC | `5,950 <= 5,950` |
| Kernel LOC | `924 <= 925` |
| Kernel public operations | `6` |
| Public transaction starters | `23` |
| Execution-table DML authorities | `1` |
| Fault hooks | `39` |
| Run map / Supervisor / Presenter authorities | `1 / 1 / 1` |
| Unknown LOC classifications | `0` |
| Product parity mapping | `141 / 141`, unmapped `0` |
| Legacy cutover readiness | 15 owners and 14 declared roots covered; dormant factories unreachable from production |

The authority, parity, cutover, and admission-budget artifacts are source-locked
to the A-source commit above. The cutover manifest embeds the complete supplied
dynamic-stack evidence for all declared legacy roots; it is readiness evidence,
not a claim that R6 activation has occurred.

## Verification

- Focused admission/UoW verification: `42 passed`.
- Product approval/admission regression after the final duplicate-signal fix:
  `12 passed`.
- Complete harness-simplification suite, split to keep resource use bounded:
  `218 passed, 8 xfailed` plus `253 passed`, total `471 passed, 8 xfailed`.
- Authority audit, parity mapping check, R5.5 cutover readiness, and R5.5 LOC
  construction gate: PASS.
- The boundary-by-boundary idempotency review is recorded in
  [`r55-idempotency-review.md`](./r55-idempotency-review.md); the final
  independent code audit found no blocking defect.

One earlier all-in-one pytest invocation exceeded its 300-second command bound.
Its two remaining pytest processes were immediately terminated by exact
worktree command-line match. The complete suite was then rerun in two bounded
halves. Every spike, test, benchmark, and gate finished with
`cleanup_remaining=0`.

## Test strategy

R5.5 changes only the dormant composition and durable backend boundary while
production remains `legacy/0`; there is no new reachable UI behavior to prove
with simulated clicks in this slice. The executable cases are recorded in the
R5.5 testcase document. R6 activation must add Windows real-E2E coverage for
Text, Voice, approval, cancellation, restart recovery, and terminal delivery.

## Remaining work

R6 must atomically drain `legacy/0`, switch production ownership to the current
Kernel generation, run the exact/similarity/reference/reachability/live-stack
cutover gate, and execute the real UI acceptance matrix. No R5.5 dormant factory
is allowed to become production-reachable before that cutover.
