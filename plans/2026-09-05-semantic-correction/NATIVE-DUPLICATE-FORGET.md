# Native remember/correct success, duplicate-source forget failure

Last updated: 2026-09-05. Current decision: **FAIL**, not a completed memory loop.
Actual backend source c2836c12caf6e96b650bfb591d0947438ae02ef3;
Harness0.7.2 / Memory0.6.9 / Service0.3.12 in the dedicated candidate environment.
Native binary SHA256 cf2584a6eb30c2c2f4948e2db32e7a83b8358462687e5d61d20f74c82695bbd9.
The existing binary loads the exact candidate Python backend; no main cutover.

## Observed behavior and actual ledgers

The earlier v2 attempt claimed success but its CREATE was rejected for an invented
candidate key. Its original USER/assistant records and no_mutation result remain
unchanged. A new real USER action after the v3 source correction successfully
created the preference. A subsequent real correction revised the same memory ID
from revision1 to revision2; the native panel visibly changed to the new value.
The follow-up answer used the new value, but there was no foreground recall tool
call: this proves a correct answer, not independent typed-recall selection.

The actual panel forget action removed the row and produced a real suppression
directive. A new question then returned the OLD preference. Read-only inspection
of that actual Provider request confirms the older repeated USER content remained
present and the revised value was absent. This is a disclosure failure, even
though the target memory row disappeared and the directive was committed.

| Scenario | Host Run | Physical foreground Provider calls | Analysis outcome |
| --- | --- | ---: | --- |
| Earlier rejected CREATE | b58b21d3-74b8-55d9-b2cd-301d4c8fa9cb | 2 | no_mutation |
| New CREATE | d6e591e2-5f75-52b5-9df6-20be1c6977de | 2 | mutate/create |
| Correction | d5e5870a-8e4f-5cef-a8b3-0fa5aa8104c3 | 2 | mutate/revise |
| Current-value question | 07d8a193-31c2-5804-afe1-f0cd2896cab7 | 1 | no_mutation |
| Question after forget | 1627e22b-ce9b-566a-b2cb-a53382b0f2e9 | 1 | no_mutation |

Each row has one separately recorded analysis attempt; those calls are not included
in the foreground count. Token usage and latency are retained in the local ledger.
Analysis cost attribution is unknown; legacy cost_microunits=0 does not prove free
usage. Foreground budget attribution is not a paid invoice. An applied analysis
job does not by itself prove a cognitive write or a correct user-visible outcome.

The actual target is
cognitive-memory-1c74ee03ee7d01a7ce6f1df91cfee66fb23483d7622625cf9dfd649fccc4b4d0.
The actual directive is
suppression-directive-288f40e01ef67e8a14d2df2e596978593760bee311572aee9742e1804e906c65,
decision hash02094169105a970cb4cc83f5853d0c2140af1e5f4d7c288583dcc483c186e8c8.
Its old format has no durable source cutoff; a later fix must not invent one from
the current time or current row count. No old receipts or failed Runs were erased.
The isolated native app was closed normally after capturing the failure.

## Deterministic counterexample, before product repair

`backend/tests/memory/test_primary_runtime_api_integration.py` now exercises an
earlier duplicate USER through the real Host runtime and public S1 ingestion,
then materializes only the later source, calls the actual HUMAN memory API, reads
history/detail, reopens stores and checks physical MockTransport requests.
The minimized duplicate fixture leaves analysis pending, unlike the native
applied/no_mutation outcome; it isolates the missing equivalence linkage without
claiming a replay of the native model analysis.

Initial control/duplicate: **1 passed, 1 failed**, 2 deselected, 2.89s.
Expanded no-duplicate/before-forget-ingest/after-forget-ingest:
**1 passed, 2 failed**, 2 deselected, 3.85s. The no-duplicate control also proves a
new same-text USER remains visible, old refs remain primary_message_unavailable,
and replaying the same forget action does not expand its scope. Duplicate cases
fail at post-forget history before reaching their later assertions; those later
checks are not claimed green. These tests do not replace native evidence.

Command: dedicated candidate Python `-m pytest
backend/tests/memory/test_primary_runtime_api_integration.py -q -k memory_only_forget`.
Required repair: verified exact complete USER-text equivalence, all-revision source
seeds, original admission order and first-action cutoff, real descendant lineage,
late delivery handling, and independent post-cut reassertion. No fuzzy text erasure,
permanent content ban, fabricated legacy cutoff, or bypass of the SDK public API.

## Local evidence index

Raw artifacts remain ignored in main Host
`.local-test-evidence/2026-09-05/human-memory-resume/primary-ui-avqul005/`:

- native-memory-ledger.json: 657060e28a75df9da37da843ac41040d6712813c3d986050dad34ad6dfa40693
- 03-correction-visible.png: 7c9080fcbe3d34edd1c30337440289fe28e82efda31dcdeced076488d437f35f
- 04-before-forget.png: 9b77420450784be028d86b5dbe9b2b7f8dd5677d07a5657523e0d9817a4f2f1f
- 05-forget-result.png: 93295da89ab314875db2a9df4a9aaf0297be2f0afde95d5de3cc8e6b5f862d6f
- 06-after-forget-old-value.png: 2b754a1673b04caed7c956fc854c13fdab561977da88d7b2a14b2c19cb936207

Candidate `.local-test-evidence/2026-09-05/primary-candidate/`:

- duplicate-forget-native-regression.log: bbe292c99f1d7e09e98af8a6782c3441be29abddef48a2abc9a94627d8ee890d
- duplicate-forget-expanded-red.log: 075f6b32a47bfe92683b2fe38dfb4bb4e314290ea8fd55943721aec640c1250f

These failures also become operation-audit oracles: recorded completion, actual
mutation, ordinary disclosure and user-visible claims are distinct facts. Audit
coverage must not infer memory success from applied jobs or absence from the panel.
