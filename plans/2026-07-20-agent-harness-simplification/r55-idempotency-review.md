# R5.5 Idempotency Review

> Reviewed: 2026-07-21
> A-source commit: `69c6980ae7297997fdffd3c49770adbf0c7e5c80`

| Boundary | Stable identity / fence | Duplicate result | Conflicting result | Recovery result |
|---|---|---|---|---|
| Admission resolution | run, decision, nonce, version, complete `DecisionSignal` fingerprint | Exact replay returns the persisted resolution | Changed response or allow/cancel conflict fails closed | Authoritative continuation is reread before acting |
| Launch claim | run, admission boundary version, recovery owner/epoch, stable launch operation id | Existing claim is reused | Stale lease/version is rejected | Idempotent provider reuses operation id; non-idempotent ambiguity becomes `launch_unknown` |
| Driver start | per-run `start_lock`, authoritative RunRecord, single LiveRun task | Already-running or terminal run is a no-op | Unknown phase fails closed | `launched` nonterminal re-enters the matching Driver only |
| Terminal delivery | terminal transition plus durable delivery intents in one UoW commit | Existing terminal is not projected twice | Nonterminal delivery on reject/cancel/expiry is invalid | Goal/projection delivery survives restart |
| Workflow admission | typed admission claim on the precreated execution | Replayed consume does not reschedule | Missing/stale claim fails closed | Only claimed workflows may resume |
| Product preparation | trusted request identity recovered before preparation | Same trusted request avoids repeated preparation | Wrong-session identity is rejected | Kernel remains the final start-time TOCTOU authority |

Review evidence is exercised by `test_r55_admission_kernel.py`,
`test_execution_continuations_uow.py`, `test_run_kernel.py`, and
`test_product_venue_chain.py`. The final independent code audit found no
blocking idempotency or quick-terminal duplicate-approval defect.
