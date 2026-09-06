# Harness Context-use reservation successor — design only

2026-09-06. No SDK/Host business code, schema, wheel, pin or userdata changes;
no new environment or test execution. Package version is **unassigned**: inspect
the candidate inventory before allocating a successor, not an assumed 0.7.x.

## Findings and scope

Original Memory plan S3 Task5 §5.4 requires Memory authorization first, then a
Harness reservation bound to that exact receipt; one exact attempt may complete
once, while new attempts/continuations need new authorization. H073 source
`0282fa982995b24bc893fdf6bed69d2caacd6587` has the DTO but no reservation association.
Read source in `simple-harness-sdk-operation-audit`; its current HEAD8bdc738 has
no `src/` difference from that frozen source. Host observations below are from
`simple_harness-primary-candidate` HEADc39b2569 plus its current readonly working
files; these are design evidence, not a test of that changing combination.

- `execution/dispatch.py:291–486`: prepare/claim binds request fingerprint, target
  and estimator; handoff CAS precedes Provider.invoke. SUCCEEDED returns the durable
  response without transport. HANDED_OFF cannot blindly replay; recovery goes UNKNOWN.
- `execution/sqlite/uow.py:6313–6440`: budget reservation and claim insert share a
  transaction, followed by lease-fenced handoff CAS. Existing-row fast paths do not
  currently compare recall authority. `reauthorize_provider_not_started` permits one
  rehandoff after trusted CONFIRMED_NOT_STARTED: this must not reuse a consumed grant.
- `runtime/drivers/react_loop.py:302–433`: actual provider request ID is derived
  from Run plus global reserved turn ordinal. Context/request bytes are checkpointed
  before invoke. `react.py:133–214` receives genuine continuation claims but does not
  carry their identity into the per-attempt Context-use boundary.
- `ConsumerRuntimePorts` wires neither a RunContextAuthorityPort nor a recall-use
  authority; high-level public consumers need both, using the same production coordinator.
- Host `sdk_adapters/context_authority.py:1094–1178` freezes **post-budget** actual
  messages/request fingerprint. `human_memory_v7.project_recall_fragments` retains
  result/item history bindings, but not the complete Context-use carrier.
- Host `ProductProviderAdapter.invoke:593` invokes `pre_invoke_guard` immediately
  before its physical delegate. `execution/primary_dependencies.py:267` rebuilds
  verified sources and calls current PrimaryHistoryPolicy. It currently has no
  grant lookup or invocation-bound exception for an already authorized typed fragment.

Keep the current coordinator and Memory authority. Add a typed authority sidecar
to the existing invocation; do not add a Host consumption counter or infer memory
provenance by searching arbitrary text. Memory's existing request/receipt hash
algorithms and public authorize method need no change for this design.

## Proposed public contracts (new, not existing H073 exports)

1. `ProviderContextUseAttemptV1`: subject, run_id, turn_id, continuation_id (nullable
   only for the true root), provider_request_id, provider_turn_ordinal,
   handoff_ordinal, context_snapshot_id/revision, context_payload_hash,
   request_fingerprint, requested_at. The driver gets turn/continuation from durable
   start/accepted continuation facts, not request metadata. Preserve it in the
   checkpoint across suspension/reopen, including after continuation ACK.
2. `RecallContextUseIntentV1`: decision_id/hash, result_id/hash, ordered item_bindings,
   ordered ContextFragmentV2 values and their manifest; final-message ownership
   commitments described below. One intent per actual retained result, bounded by
   the existing Context/Recall budgets. Empty means the trusted snapshot producer
   explicitly attests no typed-recall material in that partition; it is not a silent
   fallback when authority or provenance is missing.
3. `RecallContextUseAuthorityPort.authorize_recall_context_use(request:
   RecallContextUseAuthorizationRequestV1) -> Awaitable[RecallContextUseReceiptV1]`.
   Host binds its real principal and existing MemoryManager to this configured
   trusted port; SDK has no Memory package dependency. It must return the actual
   Memory receipt, never a locally signed equivalent. SDK validates exact class,
   request hash/bindings, epoch/policy shape and time interval before reserving.
4. RunContextAuthorityRequest/RunContextSnapshot get a versioned successor carrying
   the trusted attempt identity and intents **outside ProviderRequest.metadata**.
   The existing request payload hash remains the actual Provider wire fingerprint;
   the new snapshot receipt additionally binds the intent hash. Old schema1 bytes
   keep their old decoder/hash; an old snapshot cannot imply recall-use coverage.
5. Public coordinator `prepare_claim`/`invoke` accept the frozen typed sidecar;
   `ConsumerRuntimePorts`/production composition wire both authority ports. Add a
   public `read_provider_context_use(run_id, request_id)` view of the exact current
   invocation, handoff ordinal/state/version, manifest and grant bindings. The view
   is backed by the real ledger and validates stored hashes; it contains no private
   source payload and grants no dispatch capability to its reader. Host guard must
   compare it to the actual incoming request fingerprint, never accept a caller dict.

The Memory `provider_attempt_id` is derived under a new versioned Harness domain
from the **full durable attempt identity** (including turn, continuation, actual
request ID and handoff ordinal). Store those fields separately as well. This is a
new production mapping, not a claim that relabeling attempt strings already proves
continuation enforcement. A real accepted continuation must produce a new attempt;
same local attempt label across continuations cannot reuse a grant. A test must
execute a real public continuation command, not just modify a DTO string.

## Hash order, durable link and replay

Freeze a one-way sequence:

`actual final messages → request fingerprint + receipt-free fragments/manifest →
durable attempt + Memory request → actual Memory receipt → invocation grant bundle`.

- ContextFragmentV2 recall_binding use_receipt fields remain null in the pre-use
  manifest. Never fill them afterward and thereby change the authorized fragment
  hash. Receipt ID/hash lives in the separate grant bundle, not provider content.
- Existing E=H(C({domain,payload})) remains unchanged. New attempt/intent/grant-bundle
  hashes use distinct versioned Harness domains. No hash includes itself or a
  downstream receipt. Do not change provider_request_fingerprint or old checkpoints'
  canonical bytes to insert a receipt.
- The trusted Host producer ties fragments to **whole exact final messages** by
  ordinal and canonical message hash, matching actual public SDK effect results.
  Budget-pruned messages contribute no grant; every retained typed payload is covered.
  A partial/ambiguous free-text rendering fails closed; do not use substring search,
  all-route/lane unions or caller-declared fragments as proof. SDK binds the verified
  Host manifest to the exact wire fingerprint; Host owns that source decomposition.

Minimal durable additions belong to Harness execution authority, not OA1 observation:

- `provider_context_use_attempts`: key `(invocation_id,handoff_ordinal)`, frozen intent
  JSON/hash, persisted requested_at, eventual grant bundle JSON/hash. The intent is
  immutable; grant fields permit only one null→bound CAS. Prepare the intent with the
  checkpoint under the real Run lease **before** the external Memory await. An intent
  alone is neither a budget claim nor permission to send.
- `provider_context_use_receipt_bindings`: one ordinal row per receipt, FK to the
  invocation and attempt, unique receipt identity within trusted authority scope and
  subject. Exact replay must match receipt hash and its complete original association;
  another invocation, continuation or handoff cannot borrow it. Bind the stable Host
  authority scope to existing deployment/store identity in Run admission, not model input.
- Bind all receipts and insert the Provider claim/budget reservation in one Harness
  transaction; existing-claim paths must compare the sidecar too. No partial bundle is
  sendable. External authority awaits never hold a Harness SQLite transaction/lock.
- The existing `claimed→handed_off` CAS is the consumption point. Require the complete
  bound bundle and valid interval in that same transaction. No independent increment
  in an adapter. Consumption is observable from the durable handoff ordinal/state.
  Root/continuation identity and lease are rechecked after the external await.

Crash cases: before Memory commit leaves only an intent; after Memory commit but
before Harness claim repeats the **persisted same request/time** through Memory exact
replay. After claim but before handoff resumes only the same association, at most once,
while unexpired. After handoff/uncertain transport, UNKNOWN blocks replay. SUCCEEDED
reopen returns the exact durable response without another send. A grant expiring before
handoff blocks dispatch; expiry does not revoke a response already sent or make replay
of stored response a fresh disclosure permission.

Trusted CONFIRMED_NOT_STARTED must allocate a **new handoff ordinal and new Memory
authorization**, then atomically link the new grant and rearm the existing invocation
with the unchanged request/target/budget identity. The original consumed receipt never
becomes unconsumed. Suppression meanwhile rejects that fresh authorization. Preserve
the original retry cap and reconciliation evidence; absent/unknown evidence never retries.

## Host wiring and privacy boundary

At new context_route production, preserve complete public result/decision/item/page
bindings while the real execution is available; no private Memory SQL or reconstructed
old gold. At ProductRunContextAuthority's final post-budget assembly, verify whole
actual effect/message ownership and freeze the retained intents. SDK supplies the real
attempt identity, calls the configured Host→Memory authority, and owns reservation and
consumption. Host does not pre-generate a receipt before the final snapshot is known.

At the physical pre-delegate guard, read the SDK's bound-use view and verify exact
Run/request/continuation/handoff/fingerprint and fragment coverage. A consumed grant
may satisfy **only its exact typed recall occurrence in that one frozen snapshot**;
ordinary raw USER history, independent short carriers, other tool/assistant history,
TaskScope sources and unbound recall occurrences still require current visibility.
Never skip the entire PrimaryHistoryPolicy or de-duplicate away an independent raw
history dependency just because it points to the same memory/evidence.

Thus receipt-first can send its exact typed partition once after suppression if all
other policy checks still allow; an independently forgotten USER-history occurrence
can still reject the whole request. That is not a global history exemption. Future
attempts need new authorization. Legacy tool results without complete carriers and
legacy active typed snapshots cannot be stamped with synthetic grants: reject with
an explicit missing-authority boundary or construct a genuinely new public recall
under a new attempt. No archive/old receipt rewriting.

## Files and compatibility

Harness: new `execution/context_use.py` contracts and narrow SQLite authority sidecar;
`execution/{dispatch,provider_invocations}.py`, `execution/sqlite/{uow,database,schema}.py`,
`runtime/{termination,react_checkpoint,consumer_adapter,production}.py`,
`runtime/drivers/{react,react_loop}.py`, `execution/context_authority.py`, public runtime
read facade/exports/API snapshot/docs. New tests target the real coordinator/consumer,
recovery and authority schema. Review whether kernel/continuation claim serialization
needs additional typed facts; do not derive active continuation from a mutable latest-row scan.

Host: `memory/human_memory_v7.py` carrier projection, `sdk_adapters/context_authority.py`,
`execution/primary_dependencies.py`, provider guard/coordinator wrapper and composition,
plus a narrow Host Memory-authority adapter and actual production-factory tests.
Do not implement in the401 validation adapter and call that production coverage.

An authority table addition requires a **new execution schema descriptor** and official
backup-first additive migration for exact valid existing schema7 + audit catalog.
Keeping schema7's marker and only adding a table is unsafe: old H073 could reopen it
and ignore the new authority. Preserve old rows, request/receipt/checkpoint bytes,
jobs/outbox/cursors; no reset or fresh-only upgrade. WAL-aware backup, nonempty reopen,
unknown/future/partial schema readonly rejection, old binary downgrade rejection and
idempotent same-backup receipt reuse are required. A successor package version/pin is
allocated only after source review; frozen H073/M0613/wheels remain unchanged.

## Decisive oracle before implementation

1. Real two-item Memory public setup and actual Harness public consumer: receipt-first,
   suppression commit, one physical ProviderPort invocation, exact response; reopen and
   same command/attempt return the same receipt/response, physical count remains one.
2. Suppression-first: no grant and zero physical calls. New public continuation and new
   provider attempt cannot reuse a prior grant; fresh authorization after suppression
   rejects. A clean new continuation with valid current recall positively sends once.
3. Exact mismatch controls: snapshot, result/item subset/order, subject/Run/turn,
   continuation, request fingerprint, handoff ordinal and borrowed receipt all reject
   before dispatch. Empty-intent/legacy input cannot bypass a required authority.
4. Crash/reopen at intent, Memory commit, grant/claim commit, before/after handoff and
   response ACK; two competing owners. Durable headers/bundle/unique receipt rows and
   actual physical call counts must agree. No helper-local counter is the authority.
5. Real UNKNOWN + trusted not-started reconciliation: one new ordinal/grant on allowed
   retry, suppressed retry denied, unverified reconciliation never resends. Cancellation
   preserves owned cleanup and uncertainty, not a fabricated safe-before-send result.
6. Real Host factory/request: grant covers only retained typed A; trimmed B irrelevant;
   missing carrier refuses; raw USER/assistant history and standalone short remain under
   current visibility. Positive receipt-first uses an unrelated safe current USER so
   an independent history denial cannot conceal whether the typed grant works.
7. Existing nonempty execution database migration/reopen, WAL-only commit and crash
   controls; original bytes/hashes retained and previous SDK rejects the successor DB.

These are proposed acceptance obligations, **NOT_RUN**. Keep401 IDs, existing numbers
and negative expectations. Receipt-first/duplicate remain BLOCKED until the actual
successor production consumer and independent oracle prove the first five controls;
Host integration/privacy and installed artifact gates remain separately reported.
