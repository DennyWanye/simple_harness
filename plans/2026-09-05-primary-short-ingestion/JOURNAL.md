# Primary short ingestion — fixed leaf validation

2026-09-05. Scope: ordinary completed USER + one assistant group, new message producer and replayable short-index admission.
Source tested/reviewed: **2fb1d1905138a57ce7acfb93495112d95aca16fe**, clean when execution began.
Branch `feat/human-memory-primary-short-ingestion`, base `7dcfce8bba0f41b8f673c8009fb4179c5bb03f6b`.
This is a program sub-delivery with journal evidence, not a machine receipt or S3/S6 completion.

## Fixed sources / ownership
- `7fe6b178` new-terminal message producer + initial B contract.
- `3e87474a` cherry-pick Carver `3918b55d`, same transaction producer call.
- `64dea62c` read-only producer replay verifier.
- `3b082412` cherry-pick Carver `c6533e6e`, future producer marker + marked-prior read-only verification.
- `2fb1d190` new conversation authority, short indexing service,13 dedicated cases and final source/oracle contract.
Carver/main own subsequent runtime/main.py/human_memory_v7 composition. This branch is not merged into main.

## Reproducible commands / environment
From this worktree's `backend/`:

```sh
../.local-test-evidence/2026-09-05/primary-short-ingestion/venv/bin/python -m pytest \
  tests/memory/test_primary_short_ingestion.py \
  tests/execution/test_primary_foreground_runtime.py::test_primary_real_runtime_terminal_outbox_history_reopen \
  tests/execution/test_primary_foreground_runtime.py::test_primary_tool_history_uses_actual_group_not_fabricated_tool_calls -q
```

Actual recorded command additionally used a fresh ignored `--basetemp=../.local-test-evidence/2026-09-05/primary-short-ingestion/committed-r1-db`.
Use a NEW evidence directory on rerun; pytest replaces its chosen basetemp directory.
Minimal value rerun: same Python, `-m pytest tests/memory/test_primary_short_ingestion.py -k real_eleven -q`.
Targeted failure controls: `-k 'atomic_rollback or crash_reopen or marked_missing or old_terminal'`.

**15 passed,31.71s** =13 dedicated +2 existing adjacent; no skip/xfail. Deterministic provider responses exercise actual
Host foreground service/S1/outbox, installed SDK ReAct, SQLite and public Memory calls. No test invents group registration
metadata or expected SDK results. No external provider/model/native UI was started; no full SDK/program suite ran.

Independent isolated Python3.12.13 env. Memory0.6.7 wheel SHA
`7dd224c29923ab1346a78bb8529d9426559bb1e3a38b8c0964f57cc8687e9c3d`, Harness0.7.2 wheel SHA
`53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed`.
Current root imports resolve inside this env; no SDK PYTHONPATH/production source overlay. `uv pip check`:40 packages compatible.
Ruff on3 new modules + dedicated tests and Python compileall passed. No mypy/full backend dependency qualification claimed.
Frozen067 wheel was rehashed unchanged after validation. Native/main venv and frozen SDK sources were untouched.

## Decisive mapping
| Original obligations | Executed scope / outcome |
|---|---|
| S1 T7 / S3 T4 | Real11 completed groups,22 registrations, full roles/order/count/manifest/pointers; exact earliest content hit, newest10 excluded; PASS |
| S1 T7 / S5 T1 | New assistant S1 references original USER+terminal; write-child fault rolls back both observer and child; reopen no second provider invocation; PASS |
| S3 T4 durability | after-ingest/after-registration/before-projection faults; reopen/repeat exact refs, one old hit;3 PASS |
| AC7 / S3 T4 | original USER and assistant EVIDENCE suppression -> no short hit, exact067 binding denied, reopen deny;2 PASS |
| S1 T7 source absence | pending USER isn't group; legacy missing child remains unavailable; marked child removed in disposable Host DB -> helper/authority read-only reject, no repair; PASS |
| S3 T4 complete membership | real tool transcript remains whole unsupported group;11 following ordinary groups still yield only earliest ordinary hit; PASS |
| Original analysis lineage | fresh recreated Memory replays actual USER AnalysisLineage from delivered Host outbox; PASS |
| S5 T3 current sources | whole-roots policy allowed before forget; recent terminal EVIDENCE ancestor forget rejects the conservative lane though old hit survives; independent USER remains visible; PASS with limitation |
| Existing primary runtime | two adjacent real ordinary/tool history cases; PASS |

Do not overstate controls: last guard test is terminal EVIDENCE suppression, not MEMORY-only typed ancestor suppression.
Marked missing-child test calls helper/authority directly, not actual observer prior entry. Legacy test emulates old producer
shape at the Host test boundary and proves reconcile doesn't backfill. These do not claim stronger unexecuted scenarios.

## Independent review
Dirac read cumulative fixed source/oracle and final log: **Scoped ACCEPT**, no new concrete P0/P1 in the bounded B leaf.
Independent source/minimality review did not rerun tests or SDK121; fifteen tests are this executor's evidence.
Review source task: `01a06eda-e08c-73a3-a3bd-e278f761d826`; final message binds exact2fb1d190 and logSHA below.
The review explicitly retains selected-source coverage and unwanted assistant analysis jobs as integration limitations.

## Evidence index
All raw evidence is ignored, never added to Git:
`.local-test-evidence/2026-09-05/primary-short-ingestion/`.
- `committed-r1.log`: SHA256 `c544545133c3c7403b743a6f6c7f7e79d7e516d73006df6ba1a7ffdd67fb4f63`.
- `committed-r1-db/`: real Host/SDK test databases.
- `environment.json`: installed paths/versions/exact wheel hashes.
- `value-r1.log`, `value-r2.log`, `batch-r1.log`, `batch-r2.log`: original reds and identified corrections retained.
- `controls-r2.log`, `guard-r1.log`, `identity-r1.log`: small diagnostic/control batches; not added to final15 count.
The earliest role-conflict test used pytest's default temporary DB location, subsequently unavailable; only its captured
failure log is retained. Do not claim a preserved original red database. Later raw DBs use ignored basetemp paths.

## Deployment block / next step
**Do not default-enable the current indexing service on067.** Its assistant full-ingest call enqueues an analysis job with
no Host analysis outbox binding, causing retry/dead-letter. This is a real integration defect, not harmless background state.
No private job edits, fake lineage or global worker disabling. The new atomic Host producer can persist sources independently;
main must first adopt a successor public source-only admission before connecting ordinary index reconciliation.
See [SOURCE-ONLY-ADMISSION.md](SOURCE-ONLY-ADMISSION.md) for existing-public-API absence, minimal seam, mode/replay rules,
Host replacement call-site and required no-job/source/installed consumer evidence. No successor SDK implementation here.

All-indexed roots still isn't selected-source coverage proof; unknown owned set or >256/denied ancestor stays fail-closed.
Complex tool/artifact groups remain unindexed. Main/runtime combination, selected-source public seam and native product
acceptance are not complete; no S3/S6/program completion claim.

Retro: the first real11-run test exposed a true role/source carrier conflict before broad testing; group reclassification
would have hidden it. One overly strong FTS-empty assertion and two fixture/API mistakes cost small retries. Preserve source
contracts and exact real APIs earlier; do not repeat green SDK/source suites to compensate.

VERDICT: BLOCKED — default short indexing integration; bounded B producer/registration leaf15 PASS and Scoped ACCEPT — 2026-09-05 — 2fb1d1905138a57ce7acfb93495112d95aca16fe — successor public source-only admission required.
