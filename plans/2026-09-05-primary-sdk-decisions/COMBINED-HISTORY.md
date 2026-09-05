# Primary decisions + history visibility combination

2026-09-05, main-owned candidate from08446e36. Isolated only, no main cutover/native claim.

Cherry-pick794beaa8 while preserving actual history API request_id/disclosure_context and
factory history_visibility_checker. The mechanical merge failed the real production decision
fixture before correction: read_model.state now requires disclosure_context. The combined
service derives it from authenticated HUMAN request_id for both list/respond and passes it
through PrimaryDecisions to the shared state reader. No fallback synthetic identity or removal
of history policy. Test factory now delegates its checker to actual installed Memory manager;
this is a fixture upgrade, not a production allow-all bypass.

20 exact production authorization cases passed15.16s. Added public EVIDENCE suppression cases:
after challenge/current USER forget, list has no params/result, respond cannot allow, SDK
exact decision remains open, Provider requests stay1 and Scope count0. Actual request ID/subject
observed at public history checker. Existing slow state/rebind/Stop/foreign-subject cases retain
last-admission behavior. This is public source suppression, not an additional MEMORY-only test.

Remaining history/API/foreground adjacent checks and independent fixed review will be recorded
before native use. Stage1 native oldMemory.6.3 results remain separate in its owner tree; this
candidate uses.6.7 and still has the known unscoped search provenance P1. UI layout repair is
owned separately. No program acceptance claim.

Local raw logs stay ignored in .local-test-evidence/2026-09-05/primary-candidate/.

Affected API/history/foreground adjacent suite:53 passed34.90s. Targeted ruff check passed; ruff only reformatted the two changed decision files afterward.

| Local log | SHA-256 |
| --- | --- |
| decisions-combined-red.log | 81cd6ebe31a8675e6fb634ae48cd90c5f9e75f58f45a2e268b379fe97c3a6a7a |
| decisions-combined-green.log | b808ce06991054c7e44fd8639269609b4bd62b65b7ad2a07b0120d7fabc65887 |
| decisions-history-adjacent.log | 3e492617228794b46905ae8d1fa1b0532c4e7930f71a861aa3be6efd5486495d |

BOUND_WAITING notification from61436ccc runtime/test only is now combined. Runtime suite16 passed3.28s; waiting-notify.log SHA-256 `0a75fdbddd1badbcf6fedfb60fcab212ebd6fe2d326d9132cfddea5ac0037946`. Notification remains post-commit/best-effort and is not an authorization ACK.
