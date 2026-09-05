# Host Memory operation attempts and preparation rejection

2026-09-05. plan-status: finalized for this leaf under the user's approved all-operation
PLAN and explicit implementation instruction. Base7cf2a39c. The original parent requirements
remain in force; this leaf is not terminal-only/all-operations completion.

## Actual inventory and gaps

| Host entry | Actual Memory boundary / authority | Existing durable source | This leaf |
| --- | --- | --- | --- |
| HumanMemoryV7Runtime.typed_recall | execute_typed_recall; real runtime principal, context/plan | SDK admitted request/attempt/terminal; pre-admission carrier otherwise ephemeral | Host started/settled + exact optional carrier |
| SemanticCorrectionAuthority.prepare | execute_typed_recall for issued candidate list; actual analysis request | Host analysis attempt/candidate manifest; SDK recall records | Same journal wrapper, distinct caller family |
| typed_recall short lane | recall_short_horizon; same disclosure | public OA1 short_recall family | Inventory only, not instrumented in this leaf |
| PrimaryCognitiveControls | get_twin_graph_view/suppress; verified HUMAN action + first committed_at | Host signed action + SDK suppression receipt | Inventory only; no fake raw-query recording |
| PrimaryHistoryPolicy / selected_short_sources | check_history_visibility/resolve_short_horizon_sources | public snapshot + actual S1/source bindings | Inventory only; no SDK SQL |
| Short indexing / conversation producer | ingest/register/rebuild projection | Host registration/S1 + SDK index receipts | Inventory only |
| MemoryIngestionOutboxWorker | ingest_committed_evidence with analysis lineage | Host outbox + public SDK receipt | Inventory only |
| MemoryAnalysisLane / executor | public DurableMemoryJobRunner -> analysis/REVISE/apply | Host attempts + SDK job_transition/mutation families | OA1 reader retains actual distinctions; APPLIED never implies written |
| Memory runtime startup/occurrences | build/register owner/read_occurrence_inbox | SDK schema/ownership/inbox receipts | Inventory only |
| Foreground current-USER rejection before SDK start | actual PreparationRejection -> Host transition CLAIMED to FAILED, turn SETTLED | existing foreground_run_transitions + verified S1/candidate/visibility snapshot | Discover/verify and persist derived audit observation without SDKRun |
| Terminal Run | public Harness immutable snapshot/pages | existing terminal consumer | Unchanged; installed072 capability-unavailable preserved |
| Service/Realtime/provider/tool/runtime | corresponding SDK boundary producers | successor Harness/Service work | Still required, not covered by this leaf |

OA1 af49f2a, combined Memory0611 d520765 / wheeld290cbfc, is independently reviewed.
Carrier only covers execute_typed_recall protocol/ownership/narrowing/exact idempotency
rejection. It is not a witness of candidate queries on timeout/collector failures.
Public read_operation_audit requires a real sealed access receipt. Current Host has no
production AuditAccessAuthorityPort/grant issuer: ordinary history disclosure or an invented
Run cannot replace it. This leaf implements a receipt-supplied public reader with fixed
snapshot/cursor persistence and explicit authority_unavailable when no grant is supplied.
It does not mint a blanket sealed-read authority or expose it as an unauthenticated endpoint.
Production automatic sealed-read grant wiring remains an explicit next delivery boundary.

## Minimal executable contracts

- MemoryAttemptJournal in the existing separate operation-audit.db (additional Host audit
  tables, no business DB DDL). `execute_typed_recall(manager, *, principal, context, plan,
  now, caller)` surrounds only the actual public call. Logical request identity binds real
  principal/context/plan hashes plus effective now and exact protocol type/value; each invocation gets a new Host attempt UUID. No SDKRun
  existence is inferred from context.run_id. Store its hash as a claimed correlation, and
  a verified Host admission/binding lookup is still required before a future consumer may claim an actual Run association. This leaf does not infer that association.
- Commit Host started before passing optional public MemoryOperationObservationContext.
  On original error preserve class/message/trace/rejection_receipt. Persist only an exact
  public observation DTO verified by re-construction/hash and exact request/attempt hashes;
  original SDK persistence_status remains host_persistence_unverified. Host persistence
  is represented in its own settled row, not by mutating the SDK DTO.
- Absent/unsupported/malformed/cross-attempt witness -> explicit closed coverage status,
  never candidate_count0. Cancellation/crash leaves a started/unknown operation, never an
  automatic business retry. Success stores safe actual result/receipt hashes only. No text,
  query, exception message, token, nonce, audio, body or secret-containing metadata.
- Audit storage failure is a degraded recording status and cannot replace the business
  result or trigger execution again. No totals for calls/usage/cost. Missing metrics remain
  unknown; repeated DTO projections are not physical Provider attempts.
- Preparation source scanner reads ONLY Host state. Reuses read_preparation_rejection_tx
  to verify actual transition/head/source and no SDK binding/start. Derived finding identity
  uses actual host_run/transition hash + owner + rule_version; retry/reopen deduplicates.
  No fabricated terminal/effect/Memory outbox, no statement of zero fees.
- Public Memory reader persists stable page JSON + page_hash + access_event_hash separately,
  retains coverage/exclusions/expectations/all_operations_recorded=False. Actual sealed grant
  supplied by trusted authority only, checked each page by SDK. Once first page saved, resume
  uses its exact opaque cursor; no live fallback on expired/missing/corrupt snapshot. New
  read generation is allowed only if no first page was selected, retaining old unknown.
- Default Host runtime wiring enables the implemented recorder and preparation discovery.
  No SDK pin changes or native/18120/paid Provider. Reader without an authority is explicitly
  unavailable rather than pretending full production enumeration.

## Decisive oracle (before code)

| ID | Required observation |
| --- | --- |
| M1 | Real installed0611 invalid typed request: durable started precedes SDK entry; original error identity preserved; exact immutable carrier persists under that attempt. |
| M2 | bool/version/ownership/narrowing/idempotency scopes differ correctly; raw credential-shaped error text never stored. Foreign/malformed carrier fails closed, no false query0. |
| M3 | Success/old Memory signature without OA1/cancel/store failure/settlement crash have explicit distinct status; no wrapper retry; reopen preserves unresolved started and exact findings once. |
| M4 | Actual production runtime typed_recall and correction-candidate caller consume the journal; baseline public result and product hash unchanged. |
| M5 | Real Host admission + public Memory suppression -> actual preparation-rejection settlement -> scanner; SDKRun NULL; repeat/reopen yields one derived finding. Tampered source fails closed. |
| M6 | Public SDK sealed reader actual multi-page snapshot: append does not enter selected snapshot; resume preserves cursor/hash/access-event distinctions. Missing grant cannot read; corrupt cursor/page cannot trigger live fallback. |
| M7 | Fixed source focused tests/typecheck where applicable, architecture/handoff and independent review; no full operation coverage claim. |

Remaining all-operation obligations include the inventory-only calls above, SDK-internal
per-call/early-failure producers, Harness/Service/Realtime combination and standalone coverage,
production sealed-audit grant issuance, authorized external readthrough, original privacy
and real-native acceptance. Enumeration complete never means all_operations_recorded.

## Review refinements before source freeze

Dirac contract and source challenge: preserve unknown audit reads, separate Memory
capability from Harness072, freeze all real call inputs, and do not mistake context
run correlation for SDK existence. Public carrier validation compares the actual
error's witness, current context/plan and Host attempt/request, including OA1 closed
reason canonicalization; arbitrary narrowing text is not stored. Started/settlement
I/O has a0.5s await bound; background SQLite may finish after timeout, so unresolved
rows remain unknown and are never an execution replay instruction. Cancellation
propagates immediately. The trusted journal page is a bounded live keyset view,
not an immutable snapshot or proof of all historical calls.

Reader query identity freezes requester/target/receipt hash/limit/expectations; the
caller read reference is stored hashed. Saved pages retain immutable SDK data hashes,
opaque cursor and separate access-event hashes. Every reopen and completion checks
the entire saved chain (work proportional to saved pages; not constant-time). Each
advance reads at most16 pages of at most100 items, rejects page JSON over2MiB, and
never claims the SDK's internal snapshot build/coverage scan is bounded by that cap.
An interrupted first read without a saved snapshot retains an unknown attempt and
a later explicit advance can supersede it with a new generation; this may consume
another grant read and does not mean the first read did not execute. With any saved
page, recovery uses only that original cursor. Missing/expired/corrupt snapshots stay
unavailable; they never reopen live. Two-live-reader CAS controls protect both a
late success and late failure against the new owner's stored snapshot.
