# P34 v14 diagnostic follow-up

Last updated: 2026-09-14 CST. Historical strict result: FIRST FAIL / COMPARE PASS. This pair does not close P3.4-A04. Host production remains18ff5d24; no new native UI or packaging acceptance.

Frozen source-snapshot-v40: SDK e10123db2196aee434b55614c2d4f7f80298e2ac / Host0e1a36db. Same context256-8m-out32k-v7 contract, initial8192/output ceiling32768/context262144, Mission8M/24attempts; one fixed pair, no reroll. Contract SHA256643dc6c26c02918246d33a4b0b77d47ff4c7a2fde3ba8963ccc86766175a10e5.

| Arm | Strict gate | Mission | Calls | Total tokens | Cache input subset | Seconds |
|---|---|---|---:|---:|---:|---:|
| FIRST | FAIL | COMPLETED/verification_passed |60|650020|444671|425.324|
| COMPARE | PASS | COMPLETED/verification_passed |105|1214646|863872|671.226|

Total165calls1864666tokens. Runner1097.48seconds, pytest1FAIL1096.87seconds. Both final reserved_tokens=0 and rehandoff_count=0; process group59210 has no remaining members.

FIRST has two physical arguments_json failures. B/task2 turn4 terminated at length with8192output/7339reasoning; the same Agent recovered at16384 on its next request, while the failed physical call and13189tokens remained counted. C/task3 turn8 terminated normally at tool_calls with1113output/371reasoning/19709total; this is a separate malformed JSON response, not truncation. Original invalid arguments were not retained, so exact syntax cause is unavailable. Historical v13 does not gain a retroactive diagnosis from v14.

COMPARE passed the unchanged final C/S code/test/Critic checks, two same-task candidates and actual synthesis, Manager dependency repair, and failed-audit fragment reuse. A delivered FIRST Mission still fails the strict pair when a physical provider error is retained.

The subsequent SDK explicit strict provider mode preserves logical tool schema semantics, uses matching HTTP/token-count serialization and a distinct frozen profile. It is separately versioned as context256-8m-strict32k-v9; initial32768/output ceiling32768 with the same semantic goals/materials/budgets/criteria. It does not redirect existing Host legacy pools. New source-native strict UI is not claimed.

Raw evidence: .local-test-evidence/2026-09-13/p33-g/source-snapshot-v40/sdk/.local-test-evidence/2026-09-14/p34-real-search-value-741a127b08c246aba14b518712edf8c5/; comparison.json SHA256 d65f79078e2471485120f7b727140413993110815d8f72ea2aaf1c309c392d9f. Raw files remain ignored; process-cleanup.json records cleanup. Detailed SDK probes and boundaries: ../simple-harness-sdk/plans/2026-09-12-phase3/p34/v14-diagnostic-followup.md (sibling repository from the workspace root).
