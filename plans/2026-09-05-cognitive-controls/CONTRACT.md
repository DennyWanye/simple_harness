# Cognitive controls leaf contract

Base15009a6e; main callback48968c8f is cherry-picked as0c10cd95 for real composition tests.
Only explicit USER browsing and memory-only forget: no NL correction, Agent graph input,
legacy facts, Provider, new ledger, SDK private storage, or shared entry changes.
Main owns human_memory_api/service/main/PrimaryChatView wiring, final writer/request boundary,
post-suppression notification, and native/outbound acceptance.

## Actual callback contract (48968c8f)

```python
PrimaryCognitiveControls(
    manager=await human_memory_v7.manager(),
    principal=actual_authenticated_memory_principal,
    authorize_primary=authorize_primary,
    find_action=action_store.find_action,
    admit_action=action_store.admit_action,
)
# all async, keyword-only
# authorize_primary(*, primary_ref: str) -> None
# find_action/admit_action(*, payload: Mapping[str, object], idempotency_key: str)
# -> CognitiveActionReceipt (structural Protocol); find may return None
```

Payload EXACT keys: memory_id, expected_revision, expected_content_hash.
idempotency_key=action_id. Structural receipt fields: evidence_id, committed_at,
payload_hash, suppression_request_id. No exact Python dataclass identity requirement.
Leaf recomputes Host canonical SHA256 over `{"schema":"primary-memory-forget/v1", **payload}`;
checks receipt.request_id derivation through suppression_request_id=`primary-forget:{evidence_id}`.
Source domain `host-cognitive-action/forget/v1` and id namespace are owned/verified by the
real main callback, including signed S1 pair, exact payload and authenticated subject. A generic
primary.append collision raises conflict; matching payload alone is not an authorized action.
The existing evidence table retains first committed_at (including valid0), even after reopen.
No new ledger. append does not itself validate current public memory target: the leaf does.

The request-scoped authorize_primary must verify current bound HUMAN authority and actual
subject/primary relation; it is called before/after slow public graph reads and before suppression.
The manager principal must match that authenticated subject. No fake execution Run is constructed.
Main must hold its real writer/request boundary through the combined action; this leaf callback
fence is not a claim of atomic auth+SDK mutation across awaits. Main callback's own four tests
and the leaf's controlled revocation tests do not replace final signed-WS integration.

## Public methods / wire operations for main

- `list(*, primary_ref, limit=20, cursor=None)` -> `primary.memory.list`.
  Response primary_ref/items/next_cursor. Items: memory_id, revision, label, status,
  can_forget, content_hash. Limit1..50; label512 Unicode codepoints/status64. ID/hash
  never truncated. Cursor is last memory_id with lexical ordering; pages replace, not
  snapshot pagination. SDK get_twin_graph_view scans all heads: ONLY output is bounded.
  The graph is USER display-only PROJECTION, never an Agent/context input or full READ API.
- `forget(*, primary_ref, action_id, memory_id, expected_revision, expected_content_hash)`
  -> `primary.memory.forget`. First call finds no admitted action, checks exact current
  public node/can_forget, then admits. A concurrently completed same action is found again
  if the intervening view has lost/changed the target; absence alone never means success.
  Replay requires exact authorized action/domain/payload and works after the node disappears.

SuppressionRequest uses receipt.suppression_request_id, requested_at=first committed_at,
actual subject, scope_kind=MEMORY, canonical memory_id, purpose=None, reason=user_request.
Forget targets the memory across revisions. expected_revision is an initial stale-view check,
NOT an SDK revision CAS. No physical archive deletion or automatic undo.

Return status=applied only after exact matching public SuppressionDecision; result also contains
primary_ref/action_id/memory_id/directive_ref/decision_hash/evidence_ref/view/refresh_required.
Immediately reread the public view. Post-commit display failure returns applied/view=null/
refresh_required=true after another auth fence; do not turn confirmed SDK success into unknown.
SDK exceptions or invalid receipt remain errors/unknown, never success by absence.

## Frontend integration surface

`PrimaryMemoryPanel({port, primaryRef, verifiedOwnerKey, ready, requests?, onClose?})`.
`port` is the actual bound PrimaryPort; verifiedOwnerKey comes from confirmed owner identity,
not global identity_status. Main should retain `new CognitiveRequests()` across panel close/open
for unresolved action IDs, and supply it via requests. Across same-owner rebind pending action
IDs/payloads survive; only verified new owner/primary resets them. Full process restart replay
requires the caller to retain the action ID: the backend S1 supports it, but this leaf does not
add a browser storage/action-recovery ledger or claim automatic UI recovery of lost action IDs.

Reads and writes have separate correlated request clients. Privacy/change events immediately
clear old display and reread; a read invalidation cannot cancel a valid mutation ACK. No polling,
no automatic mutation replay. Explicit retry uses exact prior action despite an empty graph.
Verified success clears pending action and fresh-reads; display errors preserve the applied notice.
Disabled/unbound state never falls back to legacy. Shared mounting/wire validation remain main-owned.

## Proof boundaries

Dedicated tests use actual Host action callback, real S1, installed public Memory0.6.7/Harness0.7.2
builder/graph/suppress/typed recall. They cover0 time, reopen, conflicting/generic-domain admission,
concurrent duplicates and find-none→other-commit race, lost SDK ACK, read/commit revocation fences,
post-commit view failure, and a new typed recall returning no old memory. Frontend tests cover
canonical IDs, explicit click only, ACK matching, stale privacy replies, unknown/rechallenge retry,
verified owner replacement, and no legacy/undo. No provider/native/full-loop2 claim.
