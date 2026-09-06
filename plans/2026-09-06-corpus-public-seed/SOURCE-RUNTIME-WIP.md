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
