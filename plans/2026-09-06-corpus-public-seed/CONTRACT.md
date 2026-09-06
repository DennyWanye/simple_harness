# Corpus public seed C01 leaf

2026-09-06. Base be7cd15a, same worktree, feat/corpus-public-seed.

## Actual entry and ownership

`deskpet.quality.corpus_seed` is a fixture adapter, not a tool/API or LLM extractor.
It accepts isolated setup records and an actual Host-committed envelope/receipt.
It never receives the compiler Case, labels, gold, initial conversation or followup.
The reviewed compiler/trusted-bindings modules in Memory main remain unchanged.

Real public chain: `MemoryManager.register_principal_owner` →
`ingest_committed_evidence` (configured HostEvidenceAuthority) →
`apply_memory_mutation_plan` → `get_memory_mutation_receipt_view`.
Source-only `admit_evidence_source` is NOT a mutation prerequisite: it cannot
satisfy ingestion span checks and the two admission modes are mutually exclusive.
These boundaries were discovered through real installed failures, not bypassed.

## Mapping and readback

C01-10 setup A is explicitly mapped to active, source-bound, explicit-user
Semantic claim, personal classification, no expiration/conflict. Exact source text
is admitted separately, then UTF8 spans derive only from that actual text.
A is a fixture label; SDK allocates the actual memory ID. Public receipt verifies
plan/hash, operation, ID/revision1/type/epistemic/evidence bindings and survives reopen.
The committed receipt does not prove current suppression/visibility/lifecycle.

Independent HM-AC2/6 fixture: an explicit Chinese source defines preference,
procedure steps and applicability. ONE strict-atomic plan creates semantic claim,
low-risk Procedure and semantic relation APPLIES_TO, with CreatedByOperationTarget
and exact operation dependencies. Each operation cites its own actual source span.
Public receipt reports 3 records; display-only public graph reports 2 nodes/1 edge,
claim→Procedure; the relation is not a node. No human-to-human collaboration edge.
Graph stays setup/reviewer data and never becomes Agent Context.

## Remaining boundaries

- C01-10 is one implemented setup, not 20 C01 or 240 quality passes. Every other
  sample remains NOT_RUN; none removed or relabeled ready.
- Graph is an independent explicit fixture, not corpus seed secretly enriched
  from gold, nor native/LLM relation extraction.
- C01-01 episode B lacks precise occurrence time although the public payload
  requires it; no timestamp is inferred from gold or falsely claimed observed.
- Reviewed source-file extraction to setup dispatch, current suppression/readback,
  correction/revision/expired mapping, full corpus clock, real recent-history
  isolation, Host runtime + two real model rounds remain to implement/verify.
- Tests use controlled Host service auth and real Host persistence/evidence
  authority, actual public MemoryManager/SQLite. They do not prove verified native
  authentication or the full runtime. Test clock200 is not a corpus scenario run.
- All240 must use one independently configured common policy. No per-case
  permission or tool/type selection is introduced. NonSELF belongs to Hegel;
  Procedure corpus compilation belongs to Singer. SDK trees are untouched.
- No Provider, model, native, new env, raw SQL business writes or artifact build.
