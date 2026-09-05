# Primary short registration — bounded Host leaf

plan-status: finalized (original program authorization + explicit B choice, 2026-09-05)
Base: Host 7dcfce8bba0f41b8f673c8009fb4179c5bb03f6b. Frozen Harness 0.7.2 / Memory 0.6.7 stay unchanged.
Owner: new memory producer/registration/indexing modules + dedicated tests. Carver owns primary_history.py integration;
its two isolated wiring commits are included in this branch. No main/native/provider configuration edits or publication.

## Original obligations / oracle
S1 Task7: authenticated conversation metadata/classification. S3 Task4: complete groups beyond newest10, age<=5days.
S3 Task5 and S5 Task1–3 retain route authorization, actual source visibility and continuation obligations.
Original acceptance numbers and thresholds remain unchanged. This leaf does not complete S3/S6/program or prove native quality.

1. Actual foreground service + SDK ReAct/SQLite with deterministic provider completes 11 ordinary two-message groups.
   Actual USER ingestion outbox then registration/rebuild -> exact earliest USER+assistant hit; eligible universe contains
   only that group, newest10 excluded. Role/order/pointers/count/manifest are asserted independently of returned results.
2. New terminal/message S1 writes are atomic; crash after child write leaves neither observation nor child. Recovery rereads
   the actual SDK terminal and does not invoke provider again. Later foreground terminal receipt is a separate transaction.
3. Registration crash after ingest/first registration/before projection: reopen and retry exact IDs/bytes, no duplicate hits.
4. Original USER or new assistant source suppression rejects short hit and exact067 binding, including reopen.
5. Pending USER isn't a completed group. Legacy without new message source isn't restamped. Marked new source missing
   its child fails read-only; no repair. Unsupported actual tool transcript is blocked whole, never reduced to USER-only.
6. Generated child's EvidenceRefs preserve actual terminal/USER ancestry. Current Host policy recursively checks its actual
   prior evidence/typed/short dependencies. Conservative all-indexed roots can deny unrelated surviving hits; this limitation
   is tested and is NOT complete product short-memory availability.

## Source / publication boundary
Read immutable foreground turn/run/SDK binding/terminal receipt and Host human_memory_evidence/sanitization receipts.
Reuse read_primary_terminal_identity_tx, terminal_observation_tx and read_evidence_pair. Verify canonical commitments,
subject/primary/turn/real SDK event, original USER hash, exact transcript text/roles and complete dependency proof.
Publication requires the real COMPLETED foreground terminal receipt. A committed observation alone is insufficient.
No model metadata, text similarity, new Run, historical restamping, fake causal groups, or SDK SQL is authority.

## New producer B (main explicitly authorized after actual red)
`append_new_primary_message_evidence_tx(db, *, store, host_run_id, terminal_envelope, terminal_receipt)`
returns `PrimaryMessageProduction(evidence_ids: tuple[str,...], blocked_reason: str|None)`.
Carver calls only in record_terminal_observation's NEW append branch, after exact terminal S1 append and before COMMIT,
using the same BEGIN IMMEDIATE connection/Row factory, existing HumanMemoryProgramStore and ingress fence.
The helper does not independently prove newness: this is enforced by the caller's new-versus-prior branch.

USER reuses original S1. The one new assistant S1 is ASSISTANT_MESSAGE, original public terminal /messages/1 content,
actual SDK event ID/hash, real run/subject and original terminal receipt timestamp. Deterministic identity/receipt never
refresh time on replay. Its envelope AND receipt EvidenceRefs point to original USER + original terminal hashes; no reverse
link/hash cycle. Reuse the actual public-terminal sanitization policy; no private provider blocks or tool metadata promoted.

New terminal payload marks message_source_contract=primary-message-v1. Only marked prior rows call
`verify_new_primary_message_evidence_tx(db, *,host_run_id,terminal_envelope,terminal_receipt)` -> same result type.
It shares source/shape validation with append, but only reads: missing eligible child raises primary_message_source_missing;
changed child raises primary_message_source_corrupt; complex groups return original blocked reason. Unknown marker rejects.
Legacy no marker never calls producer or repairs a missing child.
Corruption/DB/fault exceptions propagate and roll back the observer transaction. Unsupported complex/artifact/non-string/absent
proof groups return blocked with no child writes; short unavailability doesn't invent foreground failure.

## Registration / retry interface
`PrimaryConversationAuthority(db_path, *,subject,primary_ref)`:
- `registrations_for_run(host_run_id)` returns ConversationGroup or stable source-unavailable reason.
- `resolve_conversation_registration(reference)` re-derives exact existing sources and all four public ref bindings.

Registration reads the already PERSISTED child and compares it to deterministic source derivation. Derivation alone never
admits evidence. A marker with missing child is corruption; legacy missing child is conversation_message_source_missing.
Group ID=real HostRun; sequence=real enqueue_sequence; ordinal=actual transcript position; count=full two-message transcript.
Manifest commits ordered evidence/hash/pointer/role bindings + real Host/SDK terminal commitment. USER pointer=/text;
assistant pointer=/source/message/content. Actor USER/authenticated-user or ASSISTANT/model-output remains truthful.
Existing host:classification/v1 PERSONAL floor applies; TaskScope is actual run scope (None legitimate), entities empty
without authoritative entity facts. authorize_conversation_public_text binds exact text and classification authority.

`PrimaryShortIndexingService(authority, *,manager,principal,fault_hook=None).reconcile()` validates whole groups before writes,
then public ingest/register for all members and public rebuild_short_horizon_projection only after all writes succeed.
Immutable completed observations are the replayable outbox: no second terminal state machine, cursor or registration ledger.
Original USER requires real ingestion outbox delivered, and uses its actual persisted AnalysisLineage on replay, including a
fresh recreated Memory DB. This avoids NULL-lineage first-ingest races and preserves existing analysis source identity.
No synthetic analysis lineage is assigned to assistant sources. Producer/root/member binding failures never become partial groups.
Returns `ShortIndexingResult(groups, blocked, projection)`; `.visibility_dependencies` carries v2 exact USER+assistant roots
from the whole scanned owned group set, including previously registered rows, not only newly written sources.

## Explicit remaining integration limits
- Main/Carver still own builder conversation authority injection, startup/terminal reconciliation lifecycle and ordinary outbounds.
- A returned roots proof is NOT a grant, nor proof that an arbitrary selected SDK chunk belongs to that scan. Unknown indexed
  ownership/uncovered previous sources or concurrent new selections must fail closed. Caller checks actual067 triple plus
  complete current Host dependencies. The all-roots >256 bound/any denied root can block the whole lane; no truncation.
  Precise selected source refs need the next bounded public SDK seam. Do not copy private chunk algorithms or infer source by text.
- Frozen SDK has UNIQUE evidence_id registration and strict role/source_kind matching. B solves fresh ordinary two-message
  groups with real independent S1. Complex tool/multiple assistant/artifact groups remain explicitly unsupported whole groups;
  they are not counted as successfully registered and the original broader obligation is retained.
- SDK ingest also enqueues a mutation job for new assistant evidence. Existing Host analysis executor only resolves old USER
  outbox links; new assistant jobs aren't an implemented analysis path. No fake binding/lineage, private job edits or claim of
  analysis closure. Main must address queue/source-only admission before declaring full product composition complete.

## Preserved red evidence / oracle corrections
- value-r1: actual foreground11 + USER outbox11 delivered, direct terminal RUNTIME_EVENT assigned ASSISTANT registration
  was rejected: conversation role differs from admitted evidence source_kind. Main chose B; neither SDK was relaxed.
- value-r2: B yielded22 registrations, exact early hit. Test incorrectly required empty output for a latest-group query.
  SDK entity/time lane may return the early eligible group independently of query FTS. Correct oracle checks exact eligible
  universe/count=1 and no recent-group content; no threshold/expected text/ID changed to match product output.
- batch-r1 suppression controls initially used a positional argument instead of public keyword request=; fixed test call.
- batch-r2 guard initially omitted existing host_classification_policy from the new test builder; SDK correctly returned
  history_classification_unverifiable. Test now uses the actual production policy, not a relaxed SDK gate.

Evidence: ignored .local-test-evidence/2026-09-05/primary-short-ingestion; commands/results/current commit in JOURNAL.md.
