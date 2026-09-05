# ResumePackage source closure — proposed implementation contract

> Current source-index contract (2026-09-05, runtime `e31c6efd`):
> [SOURCE-MIGRATION-CONTRACT](../2026-09-05-primary-effect-sources/SOURCE-MIGRATION-CONTRACT.md)
> supersedes the earlier reservation-only search/page-in enumeration and pre-fix
> completion statements below. Global v47 records exact SDK effect identities at
> real primary handler entry, including unscoped calls; public `read_effect`
> verifies results, and producer prefixes use the exact indexed sequence.
> `harness_evidence_reservations` remains scoped evidence/watermark state, not the
> complete dependency index. The 8e896472 independent late-forget P1 was reproduced
> and fixed; independent review of e31c6efd is required before integration.
> Earlier investigation and test results below are retained as historical stages.


2026-09-05. Investigation on runtime888efe0c + fixed shared API2645d8b2.
This is a concrete source/compatibility proposal for coordinator and Memory review,
not an implemented capability. Initial scoped is still P1; dynamic resume currently
rejects unproved content. No further generic guard expansion is proposed.

## Actual fields and origins

| Package component | Actual producer/content | What current proof establishes | Missing visibility dependency |
|---|---|---|---|
| schema/task_scope/source/revision/watermarks | search.py:open_exact; ProjectionSourceReceipt | owned Host exact source, state_hash/event_prefix_root/checkpoint_set_root/binding hash | hashes establish integrity, not S1 admission or memory lineage |
| README | projections.py:_render_tx: title, status, goal, scope/source IDs | exact materialized view receipt | initial title/goal have no S1; later goal derives from mutation |
| PLAN | canonical state.operations: operation ID/kind/value/reason/plan ID | immutable mutation event/decision and applied revision | actual generating Run's consumed dependencies; operation refs alone are not a completeness certificate |
| STATUS | status/goal, watermarks, pending closure IDs/reasons | state source plus current pending_receipts_tx | goal sources; dynamic pending closure snapshot is not in the canonical source roots |
| RESUME | status/goal/resume; latest checkpoint (including embedded state and metadata); complete binding row and root rows | checkpoint hash/set root, exact binding receipt | checkpoint duplicates historical state; arbitrary checkpoint metadata and binding proposal/grant provenance need classification, not whole-row automatic disclosure |
| EVIDENCE | event/group counts and archive/root block IDs | content-addressed Host archive tree | dereferencing blocks can reveal full events/state; references alone do not prove downstream text visibility |
| per-view content/hash/root/count/receipt | materialized view then search._bounded(content,4096), possibly1024 | content_sha256/receipt bind original materialized view | actual returned content may be truncated: add actual UTF8 slice hash separately, never compare it to full-view hash |
| search candidate title/goal/snippet | search index from same projection | permission-first scope and source | same textual source gaps as resume, cannot evade by search then route |

Concrete anchors: backend/deskpet/task_scope/search.py:open_exact;
projections.py:_load_model_tx/_render_tx; projection_sources.py:ProjectionSourceReceipt;
store.py:create_task_scope/_merged_refs/_reduce/create_checkpoint.

create_task_scope writes revision1 directly with title/goal, event_watermark0 and
no S1/event. Later applied mutation plans store original payload, merged envelope
refs and operations. _verify_refs_tx checks evidence ID/envelope hash existence;
it does not certify that refs enumerate all memory/history used by the generating
Run. Existing source_kind/source_event_id must be verified against real producer
receipts; merely matching text or a user turn is not a valid backfill.

## Minimum Host proof; no new Memory canonical-state authority

Add a Host-owned immutable projection disclosure manifest, produced by the same
verified renderer over one selected source. Proposed typed Host port:

`read_scope_disclosure(subject, scope_ref, source_ref, *, disclosure_context)`
returns a prepared projection containing original source identity, exact retained
view bytes, `package_hash`, `manifest_hash`, fragment list and explicit coverage.
Every fragment records view/field path, actual UTF8 hash, original full-view hash,
producer kind and exact producer receipt reference/hash, plus complete existing
S1/typed/short dependencies where it contains user/model text. The caller cannot
supply a boolean complete or classify arbitrary content as Host structural data.

Host structural allowlist is limited to verified IDs, revision/watermarks, bounded
state enums and required binding identity. Free-form title/goal/reason, filesystem
path, full binding/checkpoint rows are not structural merely because Host stored
them. Existing effect authority may consume an exact root internally without
serializing that whole record as ordinary Provider history.

For text-producing operations, reuse original immutable event and applied-plan
receipts, then require actual generating Run/terminal source dependencies. Whole
producer-group dependency union is acceptable and safer than guessing per-word
lineage. If complete generating provenance is absent, refs are candidates only.
For new creation, persist authentic interaction/model-tool production evidence
with original current USER and all actual Run dependencies in the same Host
operation transaction. Bind canonical create receipt to this real producer.
Do not issue a fresh cold S1 that restamps unidentified old title/goal as new input.

Provider scope authority and visibility remain separate checks. Initial scoped
binds the manifest to Host admission+HOST_INITIAL+SDK public start snapshot;
dynamic resume binds it to real context_tool effect/result/route receipt and
actual package bytes. At every Provider entry verify actual initial and newly
consumed manifests, then batch all S1/typed/short through current shared policy.
Terminal must preserve actual consumed manifest identity/dependency union so a
later assistant cannot lose its scope-text provenance. No private SDK SQL.

## Compatibility and usable restoration

Keep canonical archives, old projection receipts, package v1 and original S1
unchanged. Existing valid text may be reconstructed only through original
producer receipts and complete sources; a scope revision/hash alone is insufficient.
Render an explicitly filtered projection with a NEW disclosure manifest rather
than claiming the old unfiltered package bytes remain identical.

For mixed old scopes, retain verified structural fields and independently proven
text groups, report missing field names/reasons without their text. Use current
USER request and real scope binding to continue work; this is a reduced honest
projection, not empty history and not a fabricated complete package. If essential
instructions are absent, accurately explain that missing context. Coordinator has approved continuation of entirely provenance-less legacy scopes
with verified structural fields, real binding and current USER. This case remains
separate from complete-source positives. The decisive restored positive
must carry actual source-bound title/goal or a later real mutation, plus effect
and terminal. Preserve a separate legacy-gap case.

The current closed-key visibility_dependencies v1 cannot encode a Host manifest
or standalone short. Coordinate one versioned Host proof extension with shared
helper (read v1 unchanged, emit new version only for new capability; never rewrite
historical hashes). Host manifest validation stays Host-owned; only validated
existing evidence/recall/short bindings reach the Memory public batch. SDK needs
no fabricated canonical TaskScope binding type.

## Files and acceptance

- task_scope store/projections/search: authentic producer references and bounded
  public disclosure-manifest reader; coordinator ownership coordination required.
- runtime primary_context/dependencies/composition/terminal: same manifest at
  initial preparation, dynamic actual effect, every outbound check and inheritance.
- shared primary_visibility + API: new Host proof parsing and validated dependency
  batch, coordinated with owner; no edit of owner's current tests.
- short067: preserve actual `(audit_id,chunk_ref,content_hash)` and verify actual
  returned text hash. shared helper accepts HistoryShortHorizonBinding in the SAME
  batch. 066 must not be called short-capable; capability requires fixed067 source
  plus helper support, not a runtime-only tuple field.

Required adjacent matrix before a fixed functional candidate:
1. real source-bound initial scoped and None→dynamic resume: exact package/source,
   production project effect, terminal and reopen/inheritance all complete;
2. title/goal producer absent: honest partial projection/explicit gap; original
   evidence unchanged, no automatic restoration using a current unrelated USER;
3. wrong scope/subject/source/manifest/package-byte hash and substituted actual
   effect/receipt deny; mutation during slow preparation forces fresh revalidation;
4. suppress MEMORY supporting a real earlier operation: API and next Provider
   hide the dependency group; unrelated structural/legitimate text remains usable;
5. actual truncated view bytes, checkpoint embedded-state dependencies, and search
   candidate/page-in coverage cannot bypass manifest checks;
6. ordinary None completion/inheritance/reopen, no_recall exact marker,
   create_new effect, typed recall exact/late deny/unknown remain green;
7. real067 standalone short (including mixed batch/reopen/expiry/forget) passes
   valid case and denies stale/substituted/incomplete provenance.

No code change, fixture relaxation, new SDK interface or production approval is
claimed by this document. Sent concrete missing-source findings to Hegel; runtime
restoration remains owned here after source contract coordination.

## Coordination response / short ownership

Hegel confirmed public Memory has no Host canonical-revision carrier and cannot
recover never-recorded create title/goal provenance. Structural facts still need
actual Host authority; embedded free text/paths are not automatically structural.

Coordinator2299ef55 returned prior test ownership clean. Coordinator now owns
primary_visibility.py and API short tests. Runtime owner will not stage those.
Agreed short proof v2 exact top-level keys: schema_version/evidence/recall/
short_horizon; short entries audit_id/chunk_ref/content_hash. Preserve v1 parsing
and all old archives. ResumePackage manifest is deliberately separate from this
short extension pending Host source-contract review; it is not a Memory binding.

## Implemented candidate (2026-09-05)

One deterministic Host reader in task_scope/disclosure.py; no new ledger or archive
rewrite. Actual successful CREATE_NEW result stores producer_dependencies captured
before the operation. Retained title/goal must match original canonical fields,
real public SDK effect arguments, exact route receipt and reconstructed pre-effect
consumption prefix. It never points at a future terminal. A later goal mutation,
even to identical text, prevents attributing that goal to the create producer.
Old mutation operations/checkpoint metadata/binding paths remain explicit field
gaps; their source reconstruction is not claimed complete in this slice.

Initial scoped preparation keeps its original exact Host lineage/route authority
but sends only the filtered package; immutable start metadata freezes the package.
The guard crosschecks actual public SDK start bytes and Host initial receipt.
Dynamic resume and search return the same renderer's packages; actual tool results
are reverified at each Provider entry. Historical 8e896472 search enumeration used Host
harness_evidence_reservations and SDK public read_effect. This was incomplete
for unscoped search and is superseded by the v47 exact-effect index above.
Project page-in can only contribute exact initial projection bytes; an unknown
history carrier cannot become a complete producer proof. Skill configuration is
separate. No new ordinary raw archive/block references are emitted. Generic
page-in feature completeness/native UI is not asserted by this slice.

Manifest commits original source/full-view hashes, actual retained UTF8 hash,
fragment producer identities, dependency union and explicit missing fields. It is
reconstructed from immutable materialized views; fresh Memory suppression checks
still run on all retained dependencies at outbound, and terminal carries the
resulting source union for inheritance. Recursion is bounded32, producers256,
text fragments4096 UTF8 bytes; scope metadata is deeply frozen.

Tests use real Host SQLite + installed public Harness0.7.2 + fixed Memory0.6.6,
current tracked helper7dcfce8b (v1 scope proof; no short claim), deterministic
Provider. Real public MEMORY-only materialization/suppression uses the coordinator's
existing helper with HostEvidenceAuthority. Fourteen new cases: initial/dynamic/
search × production source create/legacy gap/EVIDENCE suppression/MEMORY-only
suppression, all file effect + terminal; plus actual start bytes/binding mismatch
reject before Provider. Dynamic malformed manifest/bytes/scope cases use real
route/effect persistence and reject second physical send/no replay.

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-history/venv066/bin/python -m pytest backend/tests/execution/test_primary_foreground_runtime.py backend/tests/execution/test_primary_dynamic_resume_visibility.py backend/tests/execution/test_primary_history_outbound.py backend/tests/execution/test_primary_create_new_runtime.py backend/tests/execution/test_scope_disclosure_runtime.py backend/tests/sdk_adapters/test_primary_provider_preflight.py backend/tests/sdk_adapters/test_provider_timeout_is_a_safety_net.py backend/tests/sdk_adapters/test_product_host_ports.py -q -p no:cacheprovider
```

96 passed in62.94s, exit0. Final stricter duplicate start-snapshot check: the two
start/binding negatives rerun2 passed in1.78s, exit0. This replaces the previous
initial-scoped functional red, not full program/native or all historical producer
coverage. Independent review required before main integration.

Ignored evidence in `.local-test-evidence/2026-09-05/primary-history/`:
- scope-combined.log SHA256 ec37fceb69102b88fc570778438b8107cd8ec1d4ca4b7e1348e64c25ae2cb060
- scope-start-bytes-final.log SHA256 4d30655d6add2f9c65a7578e48ebb39f379803dd59124a0ea4fe805dbd27ec20
Original scoped red log remains unchanged. No App/Provider/main/pin/SDK changes.
