# Primary history visibility API contract — 2026-09-05

Base87c42b43; owner branch feat/human-memory-primary-history-api. Own helper,
read model, service, HUMAN WS command adapter and focused tests/docs only.
Runtime/context/terminal writer/composition/main.py/SDK/version/pin remain main-owned.

## Shared interface

```python
PrimaryHistoryPolicy(db_path, subject, checker)
await policy.check_evidence_ids(
    *, db, primary_ref: str, evidence_ids: tuple[str, ...],
    disclosure_context: DisclosureContext,
) -> dict[str, bool]

# In-flight Run: actual USER + retained inherited evidence + newly selected recall.
await policy.check_dependencies(
    *, db, primary_ref: str, dependencies: Mapping[str, object],
    disclosure_context: DisclosureContext,
) -> bool

# Trusted composition callback: selects the actual principal for this subject.
await checker(*, subject: str, disclosure_context: DisclosureContext,
              bindings: tuple[HistoryEvidenceBinding | HistoryRecallBinding, ...]
) -> HistoryVisibilitySnapshot
```

Callback uses only MemoryManager.check_history_visibility and public package-root DTOs.
It must reject a subject different from its trusted runtime principal. Helper validates
snapshot type/schema/subject and exact ordered binding commitments; request_hash is the
SDK's principal-inclusive observation, not an epoch/ALLOW cache key. No SDK private SQL.

Helper reads Host human_memory_evidence + sanitization receipt in the caller's Host
read transaction. Verify exact subject/primary/ID, envelope and receipt commitments,
source kind/ref/run/hash/payload metadata, accepted receipt binding. Follow canonical
S1 evidence_refs by exact hash. Recursion stays within the authenticated primary, while
allowing different actual Runs. Host rows are immutable; do not manufacture new S1 pairs.

The main-owned terminal writer embeds this hash-bound payload member:

```json
{"visibility_dependencies":{"schema_version":1,
 "evidence":[{"evidence_id":"actual-source","envelope_hash":"sha256"}],
 "recall":[{"result_id":"actual-result","result_hash":"sha256",
             "item_id":"actual-item","item_hash":"sha256"}]}}
```

`check_dependencies` validates this object directly before a terminal exists; at least
one actual evidence source is required. Runtime must ensure this includes the current
USER and all actually retained inputs, including newly selected recall. `item_hash`
is the public TypedRecallResultItemV1.result_item_hash, not public_payload_hash.
No invented observation or Run is needed. Runtime calls it after slow preparation and
at each physical Provider entry. Unsupported short-horizon carriers deny reuse/outbound;
hiding only the eventual terminal is insufficient. New standalone support requires its
own public exact binding and separate agreed Host integration.

This object is proof of completeness supplied by runtime; helper validates its exact
shape and dependencies, never infers missing recall from assistant prose. Terminal
observation must match real Host run/turn/binding/terminal receipt. Its input USER must
be among the evidenced dependency closure. Parent terminal dependencies recurse too,
so inherited cross-Run context cannot bypass source forgetting. Repeated identical
bindings deduplicate; conflicting identities/malformed proofs fail closed.

A source is visible only if its own S1 binding and every recursively proved evidence/
recall dependency is visible in one SDK batch. Legacy terminal missing this proof is
not repaired or rewritten: suppress assistant/tool/artifact group only; original USER
continues through its actual S1 binding, even before async SDK ingestion/analysis.

At most256 unique SDK bindings, depth64 and4096 edges; input/source IDs bounded.
Overflow is an error, never partial ALLOW or multiple independently authorized batches.
This does not claim bounded per-Run transcript cost or SQLite CPU/IO time.

## Read and WebSocket integration

Existing HUMAN WS command path already calls human_memory_api under the verified
connection request scope. Adapter validates/passes its real string request_id into
state/page/detail. Service builds USER_REVIEW, USER_SELF, authenticated Host/current
DisclosureContext with run_id=request_id and auth subject/authority_ref; that field
identifies this UI read, and no SDK execution is created. Caller-provided authority/
disclosure fields remain rejected. Recheck the existing connection fence after slow IO.

Keep existing subject-level suppression preflight (including empty history). Replace
per-source suppression checks with the shared helper. State checks active/queued source
IDs after binding reader IO. Page checks all selected dependencies after all transcript
reader IO; detail checks after its transcript read. Every output performs fresh checking,
regardless of unchanged Host revision, SDK epoch, request_hash or checked_at.

Host snapshot/SDK snapshot do not grant an atomic lease across future network writes.
Missing checker / invalid SDK response fails closed. No legacy source-only fallback.
Initial construction/import remains compatible with older installed SDK until composition
injects the new callback; reads requiring the new capability reject if unavailable.

## Focused acceptance

- Real cold/ingested-unanalysed USER admission readable, no fake execution or analysis gate.
- Actual memory-only suppression rejects supported USER and derived/inherited terminal;
  unrelated USER remains readable; no copied evidence suppression directive.
- Host envelope/receipt/hash/subject/primary and malformed/foreign dependency negatives.
- Old terminal missing proof: USER retained, generated group hidden; archives unchanged.
- Cross-Run evidence and recall bindings reach exactly one public SDK batch.
- Slow older reader followed by suppression: final page filters previously selected source;
  unchanged epoch never skips a check; state/detail enforce same output boundary.
- Actual signed HUMAN WS primary request forwards request_id and authenticated authority;
  unbound/stale connection or wire-injected disclosure is rejected.
- Existing exact controls, pagination/detail and durable ACK behavior retained.

Library/API tests use an isolated exact installed0.6.6/0.7.2 environment here; main owns
production composition, main pin/environment and native/provider acceptance. Independent review by main/Dirac before final integration.
