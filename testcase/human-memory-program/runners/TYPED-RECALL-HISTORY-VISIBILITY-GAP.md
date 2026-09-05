# Historical Memory source visibility — read-only design, 2026-09-05

Scope: S6 new-Run primary-history replay question arising from S3 public consumer work.
No SDK/Host production change, new API implementation, candidate upgrade, or authorization
change is part of this note. Memory0.6.3 installed and exact0.6.5 source30743bb have identical
ASTs for resolve_suppression, its resolver, authorize_recall_context_use and its source checker.

## Existing public surface and limits

`SuppressionCandidate(subject, evidence_id=None, memory_id=None, entity_ids=())` with
`resolve_suppression(candidate, purpose)` checks subject and supplied targets. It does not
expand memory/chunk lineage, resolve current heads/status/expiry, or prove completeness of
Host targets. The operation is on the public cognitive backend port; MemoryManager has no
same-name convenience wrapper. This is a negative veto, not sufficient ordinary visibility.

`MemoryManager.authorize_recall_context_use(*, principal, request, now=None)` validates
`RecallContextUseAuthorizationRequestV1` against the original durable decision Run and
original context turn; another Run/turn rejects invocation binding. A new attempt checks
old result epoch/policy/expiry and current source heads/type authority/disclosure/evidence
suppression. An identical prior attempt returns the old identical receipt before those current
checks. It is not historical-source reauthorization for a new Run. Current source checks do not
supply entity_ids; global suppression epoch invalidation is not a complete entity-lineage query.

Public snapshots:

- `TypedRecallResultV1`: authority_epoch, policy_hash, evaluated_at, authority_expires_at.
- `RecallContextUseReceiptV1`: authority_epoch, policy_hash, authorized_at, expires_at.
- `MemoryManager.execute_typed_recall(...)` produces a result snapshot, but exact idempotent
  replay returns an old result. A fresh recall is not an arbitrary source visibility query.
- There is no ordinary public current-authority-head getter. Audit canonical manifests require
  audit authority and must not become an Agent context data source. Digital Twin remains display-only.

A changed epoch/policy is sufficient to conservatively invalidate a memory-derived message.
An unchanged epoch is not proof of current visibility: valid_to/short expiry can pass solely by
clock movement; current disclosure may differ; incomplete original provenance remains incomplete.
Polling a fresh recall solely to fetch an epoch adds a recall transaction and still does not solve
these gaps. No claim of complete public verification is made for this workaround.

## Host-only conservative behavior on frozen candidates

At actual context assembly, persist verified result/item bindings and source_kind/source_ref/
source_revision/source_content_hash/public_payload_hash. Keep the Host dependency manifest bound
to the actual rendered fragment, message and outbound attempt. Also retain any verified source
provenance available at that point. A result-local item_id is not a memory_id; an evidence manifest
hash cannot be inverted into evidence IDs. Caller/model statements about sources are not authority.

Mark an assistant/tool message memory-derived when Memory content was in its generating context,
including transitively replayed derived history. Do not depend on whether the model explicitly
cited it. Unknown legacy provenance cannot default to clean. Keep known ordinary, non-derived
conversation readable; keep immutable original history for its authorized storage/audit purpose.
For Agent history projection, omit an unverified derived message rather than scrub only apparent
quotes: its prose may paraphrase the suppressed fact. No automatic inference from missing targets.

Resolve any complete known target set for current suppression as an extra deny gate. Until a
complete ordinary source check is available, omit unverifiable memory-derived history from a new
Run. This conservative denial can be justified; allowing it merely because known targets were not
suppressed or a sampled epoch was equal cannot. This note does not change old recall authorization.

## Minimal proposed SDK operation (not an existing API)

Proposed descriptive signature:

```python
async def check_history_source_visibility(
    *, principal: MemoryPrincipal, request: HistorySourceVisibilityRequestV1,
) -> HistorySourceVisibilityReceiptV1: ...
```

Input: schema_version; unique check_id; current run_id/turn_id; current DisclosureContext;
Host history_dependency_manifest_hash; a bounded, nonempty unique list of historical bindings.
Each binding contains original decision_id/hash, result_id/hash, item_id/result_item_hash and
source_kind/ref/revision/content_hash/public_payload_hash. A historical use-receipt binding may be
retained for Host causal provenance but grants no current authority. Check clock is SDK-owned.
No caller-supplied authoritative evidence/entity lists, candidate scores, new query or selector.

Trusted validation boundary:

1. Strict DTO/type, ownership and current disclosure checks. Resolve original durable result/item,
   verify exact hashes and source discriminant before inspecting a source. Tampered/missing/cross-
   principal bindings deny; caller-provided source_ref alone is not authority. Enforce atomic
   confirmation-group handling if supported; otherwise explicitly deny that carrier.
2. Historical expiry does not erase the authenticity of its stored binding. It also does not grant
   use permission: load the referenced canonical source and check its current state independently.
   A superseded historical revision cannot silently become the new payload/revision.
3. Inside one consistency transaction, resolve authoritative Memory/evidence/entity lineage,
   current head/lifecycle/conflict/validity, type-specific authority, short source current existence,
   invalidation/expiry and current privacy/attributes/disclosure. Check all relevant suppression
   scopes for the intended ordinary purpose. Unknown/corrupt/incomplete lineage denies.
4. No search, ranking, model call, source mutation, twin data, or new recall result. Existing typed-
   recall/current-use receipt validation and its expiry/Run restrictions remain unchanged.

Output: request_hash + dependency_manifest_hash + current Run/turn/disclosure binding;
ordered per-input ALLOW/DENY and bounded reason codes; current authority_epoch/policy_hash;
SDK checked_at and earliest source/time expiry bound; immutable receipt_hash. No new source
payload or unrelated entity/evidence data. Host projects a message only if every dependency allows.
Repeated check_id must not replay an old ALLOW as a fresh check: either reject reuse or explicitly
mark historical replay and require a unique id for each new validation.

The receipt proves visibility at checked_at, not after arbitrary future mutations. Host must check
immediately before outbound use and serialize local mutation and outbound enqueue, including all
writers, for the claimed ordering. If it cannot guarantee that ordering, an additional final-use
fence is required; a getter or finite receipt TTL alone cannot prove race freedom. This is an
explicit design limit, not a claim of provider authorization from a read-only receipt.

## Decisive test oracle before implementation

Use independent admitted input facts and trusted frozen clock; never derive gold from SDK output.

- Positive: verified stored cognitive and short bindings in new Run, unchanged current source and
  permitted disclosure, allow; ordinary non-derived history requires no SDK source admission.
- Same verified old binding after memory/evidence/entity/subject suppression denies, including
  entity suppression that predates the check and spans multiple evidence records. Incomplete or
  forged Host target lists cannot bypass SDK lineage expansion. Unknown provenance remains denied.
- Revise3.11 ->3.12: old3.11 binding denies; no silent substitution. Forgotten/revoked/contested/
  invalid current state and short invalidation/deletion deny. Confirmations remain atomic.
- Clock only: at exact valid_to/chunk expiry deny even if epoch unchanged. New recipient/purpose
  restriction denies even when source bytes and epoch are unchanged.
- Tamper each decision/result/item/source/hash/subject binding; duplicates/missing sources and
  cross-principal references reject. Output list completeness and request/receipt hashes are exact.
- Old result/use receipt expired: old authorize_recall_context_use still rejects as before; new
  history check independently allows only if canonical source is currently visible. A new Run
  cannot reuse an old authorization receipt by relabeling Run/turn/attempt.
- Repeat check id cannot turn an earlier ALLOW into current proof after suppression. Two orderings:
  suppression commits before check ->deny; check completes before suppression ->receipt remains
  historical and Host outbound ordering must prevent treating it as a check after suppression.
- Host taint propagation: recalled fact -> assistant paraphrase -> later no-new-recall assistant/tool
  remains derived. Omitting an unverified message cannot accidentally drop known ordinary history.

These are proposed implementation oracles, not tests executed in this read-only investigation.
