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

2026-09-05 main-line unblock: narrow the initial design to one result-bound read/check;
reuse existing public DTOs. Extra caller copies of source fields, decision bindings,
check ids and authoritative lineage lists are not required SDK inputs.

```python
async def check_history_source_visibility(
    *,
    principal: MemoryPrincipal,
    result_id: str,
    result_hash: str,
    item_bindings: tuple[RecallItemBindingV1, ...],
    disclosure_context: DisclosureContext,  # current new Run
) -> HistorySourceVisibilityCheckV1: ...
```

Inputs are strict/bounded/nonempty and item bindings unique. Current disclosure subject and
recipient/purpose must be authorized for principal; its run_id identifies the new Run.
Host keeps the message dependency manifest and provenance itself and checks every dependency;
SDK checks one referenced durable result per call. A message spanning results needs every check
to allow. The Host manifest must be bound to the actual generated message/context, not a model's
claimed citations. If old history lacks verifiable result/item bindings, there is no source_ref-only
fallback: that history remains unknown/denied until actual trusted provenance can be recovered.

Trusted validation boundary:

1. Resolve the original durable result under principal ownership, verify result_hash and each
   item_id/result_item_hash, validate the associated decision binding. SDK derives source_kind,
   source_ref/revision/content_hash and original effective classification from that durable item.
   Caller supplies no evidence/entity authority. Malformed input rejects before source access;
   unavailable, tampered or unowned bindings fail closed without cross-owner payload disclosure.
2. Historical result expiry does not erase authenticity of the stored source binding, but grants
   no use permission. Independently load the canonical source and check current head/lifecycle/
   conflict/validity/type authority. A revised historical payload cannot silently become a new
   value. Check short existence, invalidation, source expiry and completeness of canonical lineage.
3. In one consistency transaction, expand authoritative evidence/entity lineage and check current
   subject/memory/evidence/entity suppression for the intended ordinary purpose, plus current
   privacy/attributes/disclosure. Unknown/corrupt/incomplete lineage denies. Minimal first scope is
   ordinary selected cognitive/short items; unsupported confirmation carriers deny explicitly.
   Any future confirmation support must enforce complete atomic groups, never individual members.
4. No query, candidate collection, ranking, model, twin, source mutation, new payload, result or
   Provider authorization. No change to existing typed recall/current-use validation or its old
   expiry/Run/turn constraints. The check only filters stored assistant/tool history; it cannot page
   an expired result, mint a recalled fragment authority, or relabel an old use receipt.

Minimal output: request_hash binding all inputs; ordered per-item ALLOW/DENY plus bounded reason;
SDK checked_at; current authority_epoch/policy_hash; earliest known source expiry (nullable);
check_hash binding that complete output. A new versioned domain must be specified with independent
vectors if implementation proceeds; no existing recall/hash domain is changed by this note.
Output is a fresh visibility check, not a durable use grant: every call checks current state, with
no replay cache returning an old ALLOW. Host admits a message only if every dependency allows;
all messages transitively derived from memory retain that classification, even in a later Run
that makes no additional recall call. Known ordinary non-derived history remains readable.

The result proves visibility at checked_at, not after arbitrary future mutations. Host must check
immediately before outbound use and serialize every relevant writer with history projection and
outbound enqueue for the claimed order. If another process/writer escapes that ordering, a stronger
final-use fence is required. Sampling a getter or adding a finite TTL cannot prove race freedom.
No claim of full cross-process/provider-use atomicity is made for this minimal read/check.

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
- Repeating identical inputs must recheck, never turn an earlier ALLOW into current proof after suppression. Two orderings:
  suppression commits before check ->deny; check completes before suppression ->receipt remains
  historical and Host outbound ordering must prevent treating it as a check after suppression.
- Host taint propagation: recalled fact -> assistant paraphrase -> later no-new-recall assistant/tool
  remains derived. Omitting an unverified message cannot accidentally drop known ordinary history.

These are proposed implementation oracles, not tests executed in this read-only investigation.
