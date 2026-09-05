# Primary exact SDK tool decisions — implementation contract

2026-09-05. Isolated branch feat/human-memory-primary-decisions, base
5da24d6f67331aeb190f5cb23defd2863674d670. Native App18120 remains main-owned.
No provider invocation, approval, license default change, fake Run/session or manual
workspace-binding decision is part of this slice.

## Observed broken path

PrimaryRunPanel uses usePermissionRequests -> primaryRunChannel ->
permissions_pending_list. p4_ipc currently asks the legacy permission_gate.list_pending.
Actual foreground tool decisions are durable SDK tool_authorization decisions.
SdkRuntimeIngress.list_open_authorizations already reads/projects these, and
SdkRuntimeIngress.decide_authorization already calls the public SDK authorization API.
Legacy main._project_open_sdk_authorizations is driven by retained legacy execution
presentations, not the primary foreground WAITING boundary. foreground_runtime records
BOUND_WAITING and returns without its state_changed callback; Carver owns that fix.

Reported native root6db4545d-f074-5797-bbad-09dd3c468430 / SDK
product-sdk-812aa644b05ec98c7ca6fd52584895102c851f9ad8ec55dfab664cfa7f49d48a
reached CREATE_NEW tool authorization waiting. User subsequently stopped it: Host
STOPPED, SDK/decision cancelled, effects/scopes0. This historical stop evidence is not
an authorization-approve regression. Missing stopped history is a separate gap.

## Minimal control contract

Use authenticated HUMAN control request scope. Add two operations:

- primary.decisions.list: primary_ref, expected_run_ref, expected_generation.
- primary.decisions.respond: above fields plus decision_id, nonce, version,
  decision (allow or deny). No allow_session expansion or policy/default mutation.

Host derives SDKRun and execution session from the exact owned durable Host binding;
reject missing/foreign primary, stale generation, non-current terminal/stopping Run,
malformed versions including bool, and foreign decision. Validate public SDK projection
against that binding. UI cannot choose identity by sending an execution session ID.

List response: primary_ref, run_ref, generation, sdk_run_ref, sdk_state, pending,
truncated. Pending retains SDK-issued decision_id/request_id/nonce/version plus actual
execution_session_ref, tool name, bounded public summary/params and expiry. Exact-Run lookup, never an all-owner pending dump. The API returns at most 32
decisions and fails closed on overflow; see the underlying SDK query boundary below. No content-bearing broadcast.

Respond reuses existing SDK decide_authorization; current connection and Host target
must be rechecked after any slow preparation, then the SDK enforces exact nonce/version
and Run decision CAS. SDK signal runtime-generation is not Host run-generation: validate
its actual run/delivery identity, project Host target fields separately. No assumption
that an arbitrary success response is a matching ACK. After committed acceptance,
wake the existing foreground driver with the authenticated subject; notification/wake
failure cannot change a committed decision into a rejection or cause blind re-approve.
Cross-store generation race must be tested at the last admission boundary; do not hold
Host SQLite write locks over callbacks which could write/read the same Host store.

Old permission_response must not provide an unauthenticated alternate route for primary
foreground decisions. Retain legitimate legacy behavior; exact primary decisions go
through the bound primary authority. Existing SDK effect/foreground lease checks remain.

## UI and ownership

Reuse PermissionPopup/usePermissionRequests for display; adapt Primary channel to HUMAN
requests with correlation and exact ACK verification. Only render decisions returned by
an authenticated exact-Run read. Primary ref is never substituted for execution session.
Read once on mount after real mapping; re-read after bound reconnect, human_memory_changed,
focus/manual refresh. Coalesce notifications and cap read work; no unbounded polling.
Clear old owner/Run decisions on invalidation; do not reuse cached unknown-auth messages.
Show waiting for authorization from actual pending/SDK state, without falsifying Host's
persistent RUNNING state. Failed/unknown response retains a recoverable pending view.

Owner files: new backend/deskpet/memory/primary_decisions.py; HUMAN API/service and minimal
main composition/dispatch wiring; bounded existing SDK Host facade projection if needed;
Primary run channel/panel and caller props; focused tests plus architecture/status docs.
Carver exclusively owns foreground_runtime waiting notification and other runtime fixes.
SDK, policy defaults, manual binding, stopped-history, Provider/native processes unchanged.

## Decisive executable tests

1. Real Host foreground + installed Harness, production ProductAuthorizationAdapter/
   policy produces REQUIRE_USER during actual CREATE_NEW. Durable SDK decision opens;
   exact bound HUMAN pending read returns it; explicit human decision response reaches
   SDK continuation; actual task effect and terminal prove continuation. Deterministic
   Provider only. No permissive stub authorization or fabricated decision fixture can
   stand in for this integration test.
2. deny branch yields no protected effect; original stop/cancel invalidates pending.
3. Signed control scope negative chain: unbound/stale/rebound owner; cross-primary/run/
   SDK binding; stale Host generation, nonce/version, expired/cancelled decision;
   integer bool rejected. Last-admission race produces zero unauthorized SDK decisions.
4. Actual delayed ACK/replayed exact decision does not duplicate physical effect or
   create a Run. Wake failure retains durable result and reconnect recovers actual state.
5. Component tests: decision exists before mapping/mount; same-Run remount; disconnect
   then verified reconnect; two sequential challenges; mismatched/out-of-order ACK.
   Only explicit click sends allow/deny. Unrelated Run/owner never displays a card.
6. Focused frontend vitest and tsc; affected Host/API/authorization suites, not whole
   backend. Raw logs remain ignored. Main performs native continuation on a fresh Run;
   already-cancelled native root is not replayed.

## Implemented candidate and verification

PrimaryRunPanel receives the same boundPrimaryPort used by PrimaryChatView. HUMAN
requests never use the secondary companion-action controlWS socket. That socket is
retained only for exact-Run tool/directory events; it cannot introduce a permission
card or acknowledge a Primary authorization. No Session-wide allow choice is shown.
Read lifecycle survives StrictMode cleanup/remount, connection loss clears cards,
and notifications are coalesced without polling. Argument previews are bounded and
credential-shaped fields/fragments omitted; SDK nonce/version stay out of the UI
preview, logs and documentation. The SDK retains its own existing durable decision.

The production regression constructs real CapabilityStore + PreparedAuthorizationRuntime
+ SdkPreparedAuthorizationPolicy + ProductAuthorizationAdapter/DurableTaskGrantAuthority,
installed Harness 0.7.2, actual dynamic Context tools, file effect and Host terminal.
It uses a deterministic Provider, signed profile binding/request_scope, the actual
HUMAN command dispatcher and scheduler_wake=runtime + drain. It opens context_route
before any TaskScope exists, approves sequential actual decisions, writes one fresh.txt,
and closes the Host Run. Exact replay returns duplicate without a new Provider call;
closed-record wrong version also rejects. Negative branches cover different subject/
primary/Run/generation/decision/nonce/version, bool fields, forbidden allow_session,
late same-owner rebind, late STOP, cancelled and expired SDK decisions. Committed deny
ACK survives wake failure. A direct production legacy signal entry probe cannot bypass
the Primary guard. No real Provider or native UI was started.

Affected backend suite: 66 passed; final focused production-decision suite: 18 passed.
Frontend Primary/controller/view suite: 35 passed; tsc -b --noEmit passed. These are
isolated automated fixtures, not native E2E or a S6/program gate. Exact commands and
ignored log digests are in RESULTS.md. Independent correctness review is pending at
this initial candidate commit; main owns native verification.

## Public-port and integration boundaries

Harness 0.7.2 ExecutionUnitOfWork.read_decision(decision_id) is public, as is
RunClient.decide_authorization. The new exact-read facade delegates to that UoW port.
There is NO public open-decision enumeration in this version. The existing Host
ProductSdkRuntimeStack.list_open_authorization_decisions uses SDK-schema SQL internally;
this slice reuses it with exact run/session and adds no SDK SQL or schema dependency.
The 32-item response limit does not claim a bound on that pre-existing SQL scan.

Carver owns the missing post-BOUND_WAITING human_memory_changed notification; it must
be combined for prompt late-task UI hydration. This slice does not change his runtime,
SDKs, pins or license/default policy. The actual SDK-state projection shows waiting
without changing Host RUNNING ledger semantics. Stop/cancel native evidence and missing
stopped-history text remain separate. Main's subsequent source-visibility/resume work
is not validated by this baseline's authorization fixture. No native acceptance claim.

