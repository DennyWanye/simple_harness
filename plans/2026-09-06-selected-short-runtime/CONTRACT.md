# Actual selected short dependencies

2026-09-06 · base `49249dbd57853afaad2a5646670fedcd857d3b5e` · bounded Host leaf.

Original S3 Task4 source/group completeness and Task5 actual recall/final-use are
the oracle. No SDK0612 change, worker activation, new model request protocol,
new evidence stamp, numeric threshold change, or full S3 completion claim.

1. Production factory owns one PrimaryConversationAuthority, shared by public SDK
   builder and selected reader. Bind its primary lazily from the existing Host
   initialization receipt/marker, recomputed in each group/registration read's
   own read-only transaction. Pin primary+store epoch once, never replace it
   across awaits; changed epoch/primary, missing init or bad receipt rejects.
   Explicit primary also must match. Do not
   invent a primary or require database initialization at factory construction.
2. Explicit memory_types retain their existing long-term-only branch. Only the
   existing default short call enters this leaf, with its actual principal and
   disclosure context. Preserve actual audit_id/chunk_ref/content_hash and bytes.
3. Resolve only actual returned triples through the public SDK port and existing
   SelectedShortSourceReader; accept a hit only with its complete verified group.
   Bind each fragment by its actual triple to that hit's full group; do not attach
   the lane-wide union. Only merge dependencies of actual retained fragments at
   the existing recursive history/final outbound fence. Never use
   completed_run_ids/all-indexed roots to determine selected dependencies.
4. Missing authority/port/refs, malformed or forged binding, partial group, wrong
   subject, current suppression or proof overflow cannot emit the affected short
   content. No broad catch may turn a failure into visible content. Preserve
   long-term results; cancellation propagates. Unrelated group suppression must
   not reject an independently visible selected hit.
5. Existing two-message USER+ASSISTANT admission remains exact. A completed tool
   or multi-message transcript, unfinished group or old unproduced terminal is
   not a partial two-message registration. Index worker/incremental scheduling
   and broader source producer contracts remain separate work.

Decisive tests (write before implementation; run only in coordinator test slot):

- Real 11 completed Host groups + delivered outbox + explicit public indexing
  setup; actual production factory recall emits early hit with exactly its two
  sources, latest ten excluded; no all-groups enumeration on recall.
- Reopen preserves exact source references; unrelated forget keeps early hit;
  selected source/terminal ancestor forget denies it, including final recheck.
- With two actual hits A/B, projecting/cropping out B removes B's group dependencies;
  forgetting B cannot poison A. A 12-group fixture is necessary for two eligible
  groups under the unchanged recent-ten exclusion; the basic positive stays11.
- Public snapshot lacking a complete ordinal/source proof and forged triple
  reject; missing authority cannot emit unproven content; downstream dependency
  removal remains a rejection. Explicit long-term selection stays unchanged.
- Retain existing true multi-message/tool/unfinished admission negative controls.

Evidence is deterministic Host/public SDK integration, not native, model quality,
automatic production indexing, or independent installed identity. Borrowed main
venv is reported as borrowed. Baseline/test execution pending serialized slot.
