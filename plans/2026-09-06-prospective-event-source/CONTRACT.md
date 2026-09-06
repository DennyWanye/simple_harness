# Event trigger: real publication seam

2026-09-06; read-only product inventory after A7 c342de9c. NOT_IMPLEMENTED / NOT_RUN.
A8 keeps only the first one-shot occurrence; recurrence remains backlog.

## Actual facts

- `backend/deskpet/memory/analysis_proposal.py:112` permits only action,
  trigger_at_iso, timezone. `_operation:394` constructs only ProspectiveTimeTrigger.
  A natural-language publication reminder cannot currently reach event registration.
- Public Harness ProspectiveEventTrigger already binds event_authority_ref,
  condition and its actual JSON-string hash. EVENT_OCCURRED needs a pending target,
  exact registration revision and a real signal receipt. No SDK protocol addition
  is needed merely to represent this first pending event.
- `backend/deskpet/capabilities/publisher.py:404` publishes the LOCAL ToolRegistry.
  `capabilities/manager.py` persists manager receipt / binding evidence; this is
  capability installation/publication, not deployment or project release success.
  Do not relabel it as a project publication receipt.
- `backend/deskpet/sdk_adapters/product_workflows/ppt_nodes.py:648` stores a
  PresentationNotifierPort.publish return dict in workflow state. A notifier result
  or generated PPT file is not a verified external publication acknowledgement.
- Search of production tool registration/execution/workflow code found no concrete
  project-release publisher with a destination-owned confirmation bound to an exact
  immutable artifact. Generic shell exit0, download atomic rename, Run.completed,
  Provider text and UI broadcasts do not supply that missing semantic fact.
- `ProspectiveSignalStore._prepared` currently validates time_due/time and the
  actual first Host time observation. Do not pass an event as a timer observation,
  outbox registration ACK, or a changed old v51 authority body.

## Missing production fact source and existing consumer seam

1. Main must identify the actual configured publication target/protocol and its
   durable fact owner. This is not authorization to add an external publisher to
   satisfy the reminder example. An existing operation receipt or a read-only
   confirmation reader may supply the fact; reuse normal project-effect authority
   and existing SDK effect lifecycle/results where applicable. The verified
   structured result must bind:
   - actual principal, accepted TaskScope and binding epoch;
   - SDK Run/effect/rawcall, stable operation id;
   - publisher kind and configured destination identity;
   - immutable artifact/commit digest;
   - destination confirmation id, confirmed revision/URL where applicable,
     verified response digest, recorded confirmation time.
   Success must come from the destination protocol's confirmation/readback of the
   exact requested version. Unknown timeout is not success and must reconcile the
   SAME operation, never automatically publish another release. This receipt belongs
   in the existing effect result/evidence flow, not a separate generic event ledger.
   A concrete publication target/credential adapter is absent from the current
   project source. Choosing local capability publication instead changes the event
   domain and does not satisfy the original generic project-release example.

2. Host binds an event target from that actual configured project publisher, before
   analysis compilation. The model may propose the action / quote / event candidate
   but may not issue an authority_ref or invent a destination/effect receipt. Compile
   the existing SDK event trigger only after exact Host selection. Preserve the
   original USER quote and Host evidence ids; schema/prompt gets an explicit event
   variant alongside unchanged first-time scheduling. Unsupported or ambiguous
   'publication' stays an explicit unbound intent, never a fabricated future time.

3. A public Host publication-source reader reads the existing exact effect fact and
   Host envelope binding, requires a confirmed typed publication result and matches
   target/condition/domain. EVENT_OCCURRED grant binds its actual receipt id/hash
   and exact ACK registration. The same ProspectiveScheduler drains both kinds;
   no independent event polling daemon or second occurrence ledger. First durable
   grant/handed_off recovery reuses the same ref across expiry and lost ACK, as time
   does. Source absence must not create a grant. Event journal encoding/dispatch
   must be reviewed explicitly; old timer body/hash/DDL stays readable unchanged.

## Necessary new oracles (not executed)

- Actual concrete publisher effect confirmation -> public event registration ->
  signal -> exactly one SDK occurrence -> next Run A7 presentation/ACK.
- Wrong destination/project/artifact/owner, pending/rejected/unknown effect,
  terminal-only claim and invented receipt: no event grant / no apply.
- Registration invalidated before handoff rejects; SDK-committed lost ACK, expiry,
  reopen replays same ref without republishing or a second occurrence.
- Publication source withdrawal and inherited reminder history retain original
  S1 dependency visibility. A7 already-green routes/ACK tests are not rerun.

The first missing production piece is the publisher confirmation producer, not
an SDK EVENT_OCCURRED DTO or a `ready=False` scheduler wrapper. No host.test event,
LLM inference, user-data mutation, external publish or models were run here.

## Independent source challenge (Dirac, 2026-09-06)

The targeted inventory found no overlooked concrete project-release confirmation
producer. This is a source-gap conclusion, not implementation acceptance.

- Event registration must bind stable project identity, publisher/destination
  identity and the exact configuration revision. Rename/rebind/destination changes
  cannot silently move an existing reminder to a new publication domain.
- First-event ordering must be explicit. Proposed minimal semantics: the first
  confirmed publication causally after the accepted registration. Bind an actual
  registration cut and publication confirmation ordering; do not compare arbitrary
  caller timestamps or reuse historical success for a newly registered reminder.
  Replay of an already consumed event for that SAME registration remains allowed.
  A publisher lacking a provable registration/confirmation ordering cannot claim
  this condition matched.
- Event observations use their own versioned commitment domain; old time
  observations and hashes are unchanged. Unknown publication reconciles the same
  operation and does not trigger another publish.

The concrete publication target/protocol must be selected by the main product
workstream within the original plan. This leaf does not assume authorization to
build a general publishing platform or treat local catalog publication as an
external project release. No additional SDK version is needed for the existing
EVENT_OCCURRED DTO itself.

## Implementation boundary after Dirac challenge

Protocol/storage work may proceed independently once the concrete receipt identity
and registration causal cut are specified: separate versioned event observation,
strict typed dispatch, original durable grant replay and invalidation. Existing
PreparedTimer/time body/hash/DDL are not widened to accept arbitrary event data.
A missing source produces no grant and no apply. Test-only receipt producers may
exercise codec/recovery, but are never installed as production authority and do
not prove publication. No extra ledger/schema is assumed.

Still unresolved before a usable production source port can be frozen:
- the actual durable publication receipt owner and its public exact lookup key;
- a provable accepted-registration-to-confirmation causal cut, including restart;
- the configured destination identity/revision against which a confirmation is read.

These are concrete production inputs, not model-provided claims or caller clocks.
Until their owner is identified, adding a nominal confirmation DTO would invent an
authority protocol. The source remains NOT_IMPLEMENTED / NOT_RUN; A7 results are
unchanged. This clarification follows Dirac's fixed-source boundary review.
