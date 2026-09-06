# Runtime/source leaf — limited verified result

2026-09-06. This latest section supersedes historical NOT_RUN status below.
Three distinct new controls passed in separate targeted batches, not a combined
rerun and not 240 quality execution:

- runtime-first-r1: actual C01 CREATE setup job materialized through public SDK
  runner and exact constructor-bound fixture authority; no foreground source.
- runtime-first-r3: actual transient delivery failure → retry_scheduled → reopen
  first IDLE rejected as unconfirmed; graph unchanged. Dirac limited P1 closure.
- runtime-source-r6: 1 PASS/1.71s. Actual local-owner Host/SDK source Run,
  actual MemoryIngestionOutboxWorker delivery before original full group,
  same-DB rejection, interrupted original-pair import and exact replay/readback,
  scoring source has no history origin or completed source Run; real scoring
  request excludes source USER/assistant, own outbox delivered before exact
  queued-turn COMPLETED group. Two deterministic transport invocations, no
  external Provider/model. This is one ordinary request isolation proof, not
  actual typed recall/short registration cross-DB privacy or all240 completion.

r4/r5 source reds preserve DTO field mistakes: EvidenceRef uses content_hash
(the admitted parent envelope hash in this context), not envelope_hash or
 evidence_hash. No SDK change or relaxed comparison. r6 used original own
corpus_c01 registry; main C02 green was not rerun. C03 independent WIP, C04/main
registry are excluded from this source delivery.

PG19566 exit0 remaining[], peak179408KiB, resource2.354s, slot released and main/
Singer notified. Exact installed H078/M618; no source overlay/new env/native.
Only actual APPLIED confirms setup-job execution; idle never certifies prior
settlement. Setup executor scope is C01 CREATE, not C01-06 revision/C02 inference.
Source import is strict fresh ordinary-v1 and rejects A7/derived dependencies.
Authored recent-history replay/full corpus runtime still remain.

Source is fixed for independent Dirac review; source-green alone is not full
production/native or corpus-quality acceptance. All raw remains local ignored.

- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-source-r4/command.log` SHA256 `6f92048d7817cc819c9f96d8062771d9c8efe904fc1cf60f19220beed6ed9e14`
- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-source-r4/resource.json` SHA256 `343c1cf5a745341c7f2573e8a42eb59b5b8ac9e0b9ae2cb9d3f4ff3a147cbc2e`
- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-source-r5/command.log` SHA256 `eae21c569d96aa2950e2e27af9b917eb950d29bc530d85c7802080723b6f1547`
- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-source-r5/resource.json` SHA256 `2e6e4a17de551053b83366533356246949b8d89b5dc95218a5118e333adc4955`
- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-source-r6/command.log` SHA256 `d0deb4e52e1bd4074ae74959d4a2ebfad828b825df07c466351f28c4ce29b9f8`
- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-source-r6/resource.json` SHA256 `620a4e48c2748d28ecc443e50939fcf00e80ce3a3aaec01658ee9b2144a7065d`

## Historical preparation and failures

# Source/scoring separation — WIP, NOT_RUN

2026-09-06. This is preparation for runtime evaluation, not a quality run.
Main owns C02/corpus_inference. Native r15 owns the resource slot; no new batch.

`corpus_source.import_setup_conversation_sources` reads the original completed
source Run through PrimaryConversationAuthority, validates its group before any
copy, and writes only exact original S1/receipt bytes through append_evidence.
Source/scoring DB paths must differ, including filesystem aliases. An interrupted
copy is not success; repeat imports exact same pairs. No queue, Run, terminal
projection, conversation registration or history origin is copied/reissued.

Scope is only original ordinary USER+ASSISTANT primary-message-v1, no A7 source
field (even [] is currently rejected), no recalled/short dependencies, and no
payload evidence dependency beyond the exact original USER pair. Invalid or
missing dependency proofs reject. This does not support every full-app ordinary
Run and is not a general cross-store migration.

The source DB remains available for original complete-group registration checks.
Scoring uses its own runtime/history/current-input authority; its USER replica
has no foreground_turns row, so resolve_history_source must return None. Do not
replace all scoring authority with a source-path resolver or create a scoring
origin to force admission. Memory mutations may reference the exact copied S1;
this alone is not current-use/alias-cut or source namespace continuity proof.

New control prepared in test_corpus_source_runtime.py (NOT_RUN): actual source
Host/Harness deterministic Run, rejected same DB, append interruption and replay,
exact replica readback/no scoring history origin, then actual scoring Host/SDK
request contains its own query but no source USER/ASSISTANT history. Source and
scoring model transports are explicit deterministic fixtures; zero quality-model
execution. No gold input. Existing source-only publicappend control was green;
this new full-source-Run control has not run.

Generic corpus_setup_jobs remains a separate uncommitted draft. Its explicit bootstrap
constructor authority binding is now implemented in SetupFixtureDeliveryAuthority; do not use it as a production
analysis drain or declare runtime setup ready. Graph c11 helper is not needed by
main; its IDLE-as-success P1 correction/negative test remain WIP (BUSY75, no child),
not independently closed. Main used its own actual APPLIED graph bootstrap.


Prepared actual setup-job control: test_corpus_setup_runtime_jobs.py invokes
public builder with exact constructor-bound fixture authority, source-only S1,
then actual SDK runner materializes the plan (not direct seed followed by fake
no-op). Only APPLIED confirms execution; first IDLE is unconfirmed. This test is
NOT_RUN. Current generic executor supports only C01 CREATE, no inference/revise.

C03 source-only data/compile_c03_setup prepared: 20 original setup hashes,
17 ordinary Episode+Semantic mappings; C03-02 season, C03-17 two explicit months,
C03-20 unsupported model inference retained with precise special-mapping reasons.
The generic registry/public apply integration is still pending; no C03 setup
PASS. Raw entity names distinguish entities but do not manufacture SDK entity
registration/alias authority. Explicit alias facts remain original text/qualifiers.
Main owns C02 and corpus_c01 registry; no edit to those files.


The scoring adapter is now implemented in corpus_runtime.execute_scoring_turn:
strict text+delivery key only, actual service.enqueue_turn then public runtime
wake/drain, actual PrimaryConversationAuthority group matched to exact queued
turn_id. Driver idle alone is not completion. Unknown/failed/waiting terminal
raises an explicit unavailable error, never quality PASS. The new source-runtime
control invokes this adapter. No fake recent transcript construction is offered;
authored recent-history replay remains a separate required seam.

Static AST parsing of the new modules/tests succeeded. No resource batch/model
was started after main native priority; all new execution controls remain NOT_RUN.


## 2026-09-06 first execution

runtime-first-r1: 1 passed, 2 failed in 1.71s. Real C01 setup analysis job
materialization/control passed (not a corpus quality pass). Source runtime
failed before Provider: test AUTH actor-1 conflicted with real local principal;
fixture now uses actual local_owner_auth rather than changing production guard.
Graph negative injected generic RuntimeError, which correctly settled dead_letter
instead of target backoff; changed to public AnalysisDeliveryAuthorityTransientError.
Original reds preserved. runtime-first-r2 attempted only these two failed controls;
default lock BUSY75, no child. Corrections not yet revalidated.

PG18401 exit1, remaining[], peak183968KiB,2.360s resource elapsed. No remaining
owned process. Existing green setup job not scheduled for repeat.

- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-first-r1/command.log` SHA256 `a80f01b726834de1c2dbe7a44c36c85b5a3e36984c4706937c4e4e38c8ff8895`

- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-first-r1/resource.json` SHA256 `6f187af0ebc64c370e159238df824ab0d7a1a70798adad6cfde72aab050e5413`


runtime-first-r3 (only two previous failures): 1 passed, 1 failed in 1.61s.
Graph public transient delivery failure produced actual retry/backoff; reopen
IDLE is rejected as unconfirmed. Source Run now passes identity/runtime but full
group correctly rejects conversation_user_ingestion_pending. Corrective fixture
now registers actual runtime manager owner and delivers actual source outbox
before group; scoring execute_scoring_turn accepts the existing ingestion worker
and runs it before its own exact group check. Its return never replaces group
proof. This last correction is NOT_RUN; no authority/USER role changes.
PG19139 exit1 remaining[], peak183472KiB, default lock released; main/Singer
notified. Actual setup job's prior green was not rerun.

- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-first-r3/command.log` SHA256 `bcf0fa258bac9b4314cfaf0ed10070d0822956e0aa700ef60891201b363f640d`

- `.local-test-evidence/2026-09-06/corpus-public-seed/runtime-first-r3/resource.json` SHA256 `7dfcf7f0814d451d1c255b2c70c694efe80a9b8f0b0b820958971bc92f028667`
