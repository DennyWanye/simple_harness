# Successor Memory public source-only admission — approved and implemented in0.6.8

2026-09-05. Required integration repair authorized by main; candidate version remains main-assigned.
Harness0.7.2/Memory0.6.7 frozen bytes stay untouched. This document does not authorize default use of the current indexing leaf.

## Existing public API answer: none
On exact Memory0.6.7 source fa6badd086d089e3e1752df45993ce0ccba98377:
- MemoryManager.ingest_committed_evidence(envelope,receipt,analysis_lineage=None) has no enqueue switch. On new admission,
  SQLite inserts analyze_evidence job AND memory.mutation.requested outbox in the admission transaction (sqlite_v5.py:725/747).
  Passing None doesn't disable analysis; the worker later falls back to its config. Replaying a known source avoids new jobs,
  but cannot admit a genuinely new source without first creating them.
- register_conversation_evidence(ref) requires the exact evidence_envelopes + ingestion_receipts pair (sqlite_v5.py:1789).
  It cannot itself admit a new source. rebuild_short_horizon_projection only derives existing registrations.
- check_history_visibility is a current read observation and deliberately accepts verified cold Host S1; it doesn't persist
  evidence or make it available to registration. Sealed export/read/legacy remember are not equivalent admission paths.
- EvidenceIngestionReceipt requires nonempty mutation_job_id/outbox_id and hashes those fields. Returning it with fake IDs,
  deleting jobs afterward, disabling the global worker, or calling private SQLite methods would misstate the contract.

Host consequence: each new assistant used by current short_indexing.py goes through full ingest, while existing
HostMemoryAnalysisExecutor._resolve_outbox requires actual USER memory_ingestion_evidence_links. The assistant has no such
link. This is an integration defect (retry/dead-letter), not merely a missing feature that can be left running in the background.

## Minimal proposed public seam

```python
async def MemoryManager.admit_evidence_source(
    self, *, principal: MemoryPrincipal,
    envelope: SanitizedEvidenceEnvelope,
    receipt: SanitizedEvidenceReceipt,
) -> EvidenceSourceAdmissionReceipt: ...
```

Source-only is a distinct operation; no new overload/flag can silently change the existing ingest return contract or defaults.
Same exact S1 validation as existing ingest: canonical sanitized payload, envelope/receipt/source/ref bindings, accepted flag,
filter-policy allowlist, private-content limits, run/subject/disclosure checks and evidence refs. Resolve the supplied principal
through existing registered owner authority and require principal.actor_id == envelope.subject; do not introduce a new authority.
This port proves durable source admission only. Conversation metadata/public-text/classification remains independently checked
by register_conversation_evidence with the existing constructor-bound Host conversation authority. No execution authorization,
no new Run, no model classification, and no analysis_lineage parameter.

New frozen receipt fields: schema_version=1, receipt_id, evidence_id, subject, source_ref, source_hash, sanitized_hash,
envelope_hash, admission_receipt_id, admission_receipt_hash, accepted_at, receipt_hash. No mutation-job or outbox identifiers.
IDs/hash use a NEW versioned source-admission domain and independently frozen canonical vectors before implementation;
existing EvidenceIngestionReceipt, S1, registration, typed/short result and history hash algorithms/bytes remain unchanged.
The first accepted_at is durable SDK trusted clock time; exact replay returns the identical receipt including that time.

## Persistence / idempotency boundary
Reuse canonical evidence_envelopes/items/links. A minimal separate immutable source_admission_receipts table distinguishes
source-only receipts from existing ingestion receipts (which imply analysis). No second copy of evidence or classification.
Shared admission/read validation can be factored internally without exposing a SQLite port or changing old receipt identity.

- One transaction persists envelope/items/links/source receipt; zero analyze_evidence job, zero mutation.requested outbox,
  zero analysis batch/apply-head/cognitive revision change, zero executor/provider invocation.
- Enforce shared identity uniqueness across both admission modes: subject+source_ref, evidence_id, Host admission receipt ID.
  Exact source-only replay returns exact first source receipt. Any changed envelope/admission/ref/subject is rejected, no writes.
- Cross-mode calls are explicit MemoryIdempotencyConflict('evidence_admission_mode_conflict'), with no writes/jobs/lineage update:
  full ingest after source-only does not silently schedule/promote; source-only after full ingest does not pretend old jobs absent.
  Automatic promotion/cleanup is OUT OF SCOPE. USER stays on the established full-ingest analysis outbox path.
- Existing full-ingest fresh/replay/fault/default semantics and all old receipt hashes stay identical. New implementation checks
  cross-mode conflicts before an INSERT failure; immutable first analysis lineage cannot be patched after source-only admission.
- register_conversation_evidence and current-history evidence reconstruction accept either exact admission kind, preserving
  source/subject/receipt/classification gates. A source-only receipt is never interpreted as an EvidenceIngestionReceipt.
- Registration, projection and current suppression/history need reopen coverage with source-only rows. Source-only admission
  doesn't itself authorize disclosure or traverse Host-generated dependency proofs.

Schema/version/root exports/public API snapshot updates belong only to the successor independently reviewed candidate.
Existing .067 test DBs with assistant full-ingest jobs are not silently converted or cleaned. For this isolated candidate use
fresh Memory data or an explicitly reviewed migration; do not re-stamp Host message S1 to evade mode conflict.

## Exact Host call-site change once candidate is frozen
In PrimaryShortIndexingService.reconcile, preserve original USER full ingest with its real outbox AnalysisLineage.
For a new assistant registration, replace ONLY its current full-ingest call with:

```python
await manager.admit_evidence_source(
    principal=principal, envelope=registration.envelope,
    receipt=registration.admission_receipt,
)
await manager.register_conversation_evidence(reference)
```

No fallback to full ingest when the method is absent. The admission capability/version check must occur BEFORE any group
writes. Missing API keeps reconciliation unavailable. Only after this change plus tests can main wire the ordinary default
startup/terminal indexing path. Atomic Host producer remains useful and doesn't by itself enqueue a Memory job.
The conservative all-indexed-roots limitation remains separately open; this seam does not claim selected-only provenance.

## Decisive evidence required before default integration
1. Independent oracle first: canonical source-receipt vectors, replay/conflict/mode matrix and exact no-job invariant.
2. SDK source tests, real SQLite: fresh/exact replay/reopen/source-only concurrent same-key all return one stable admission;
   inspect actual jobs/outbox/analysis rows and SQL trace to prove no insert/enqueue or analysis-head mutation. This is allowed
   only inside SDK source tests/independent review, never Host production SQL access.
3. Public installed-wheel consumer with a trapping executor: admission -> registration -> projection -> actual selected short;
   DurableMemoryJobRunner remains idle, analyze/delivery/provider traps stay zero before and after reopen. Combine this with
   source-level no-job row/trace evidence; 'runner idle' alone doesn't prove no hidden queue.
4. Reuse this leaf's real Host11-group producer/outbox test against the new wheel:11 USER analysis jobs retain their actual
   lineage;11 assistant admissions add ZERO jobs. Process existing USER work and prove no assistant retry/dead-letter appears.
5. Crash before/after envelope/source receipt commit: rollback or exact replay; no duplicate sources/receipts/jobs. Contrast
   legacy full-ingest produces its one real analysis job/outbox and preserves receipt bytes.
6. Source-only-first/full-first cross-mode conflicts and spoofed receipt/subject/source/hash rejected before side effects.
7. Original USER/source/terminal-ancestor and MEMORY-only suppression controls, exact067-compatible short triple plus Host
   current ancestry check, reopen deny. The public query/disclosure/expiry/hash/threshold contracts remain unchanged.

Current status2026-09-05: source-only implemented in independently reviewed0.6.8 source5e8397b,
frozenwheel98a9c788; Host55b9e402 independently ACCEPT with17 bounded tests and committed2 decisive
checks. New assistant admissions no longer enqueue analysis; USER full admission retains actual
lineage. Selected-source visibility remains a separate open obligation; main/native not modified.
See JOURNAL.md for evidence layers, exact identity and review boundaries.
