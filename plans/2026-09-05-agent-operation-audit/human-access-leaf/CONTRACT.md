# HUMAN purpose-bound Memory operation audit

2026-09-05. plan-status: finalized. Base54156f1e. Explicit user implementation
instruction and Dirac independent contract challenge recorded in coordinating task.
Original Memory HM-AC7 / S6 Task4 and all-operation PLAN remain authority; this is
only the explicit human metadata access leaf, not full audit/native/program PASS.

## Boundary

A separate “查看我的记忆操作记录（仅元数据）” action grants current local HUMAN
SUBJECT metadata scope only. A node why-memory click, natural language, static
AuthenticatedHostSnapshot, ordinary Agent, background task, and absent request scope
cannot grant this authority. Existing signed binding is reused; no new signature
system. Actual verified serving lease is mandatory at admission, SDK authorization,
each page admission and final disclosure. Closing/rebinding/expiry invalidates it.

primary.audit.open {primary_ref,open_action_id}; page
{primary_ref,audit_ref,page_action_id,cursor_ref}; close {primary_ref,audit_ref}.
No wire authority/principal/subject/purpose/receipt or SDK cursor. Server fixes
operation_metadata display, page limit100, max_reads32, expiry5 minutes.
SDK public AuditAccessAuthorityPort + authorize_audit_access + read_operation_audit.
Exact installed Memory0.6.12 exposes only SealedAuditPurpose.EVIDENCE_AUDIT.
The SDK sealed decision uses that actual purpose; Host narrows this product grant
to operation_metadata by keeping the receipt/authority server-only and exposing
only read_operation_audit. No OPERATION_METADATA SDK enum or SDK-enforced narrower
purpose is claimed. A future content-audit endpoint cannot reuse this Host grant.
Action admission reuses Host S1 with an isolated audit domain, first committed_at;
raw nonce/grant remains server-only. No Agent/context or analysis outbox forwarding.

Host persists requested page BEFORE SDK access, freezing session/grant/lease/primary/
page/inputcursor. Saved ACKloss replay returns exactly the same logical delivery,
after live checks; it does not consume or claim another SDK read. Same action key
with changed page rejects. New actions cannot read an old cached page for free.
Each new page requests at most one SDK page, preserves snapshot/access hashes and
opaque cursor. SDK success before Host save leaves unknown, never guessed exact page.
No SDK public charge-only or cross-grant cursor port is invented. After restart,
rebind, close or expiry old grants are unusable; archives remain and explicit new
open selects a new snapshot, not a joined continuation. Budget exhaustion/incomplete
and legacy gaps stay visible; enumeration_complete is not all_operations_recorded.

## Oracle before implementation

| ID | Required decisive result |
| --- | --- |
| HA1 | Real signed HUMAN binds; explicit open persists isolated S1 then public SDK authorizes; absent scope/static auth/unbound/cross-primary cannot grant. |
| HA2 | Actual public SDK nonempty audit metadata page rendered; page and whole response bounded; no plaintext/nonce/token/Agent input. |
| HA3 | Same key changed page rejects; durable requested precedes SDK call; after-save ACKloss retry exact body with one SDK read; different action cannot bypass budget using cache. |
| HA4 | SDK success Host-save failure records unknown/incomplete; cancellation/crash does not issue another read automatically; reopened serving service rejects old grant. |
| HA5 | Real signed rebind during slow open/page rejects unauthorized write/output; close/expiry rejects cached replay; concurrent page/close or same action does not double-charge. |
| HA6 |32 accesses then33rd new page rejected; no automatic new grant or snapshot mixing. |
| HA7 | UI opens only on explicit click, no background auto grant, clears in-flight/details on hide/close/rebind/expiry; unknown keeps original action for explicit retry. |
| HA8 | Production composition + dispatcher + frontend parent wiring; focused adjacent regression; independent correctness review, ARCH/handoff. Native remains coordinator-owned. |

Execution: single owner in isolated tree; focused actual installed SDK/Host signed
fixtures first, frontend tests/typecheck/build, independent review. No paid Provider,
SDK modifications, shared node_modules changes, port18120 or native App.

## UI lifecycle and final delivery clarification (2026-09-06)

The existing boundPrimaryPort replays ControlChannel's actual cached bound object
on subscription. Matching initial replay must preserve usability; a new bound wire
frame, owner change, disconnect or rechallenge revokes the old capability. This is
an in-memory cache distinction, not a new signature or persistent authority.
Global identity-ready broadcasts cannot establish readiness. Actual backend
request scope and final serving lease remain authoritative.

UI transport IDs differ from logical open/page action IDs. A known grant is
best-effort closed on hiding and content is immediately removed. Its close result
remains unknown without a matched ACK. A pending open survives same-owner panel
hiding in a port-owned WeakMap. Explicit abandonment of this read/open clears only
local waiting: Host's unknown archive remains, possible grants expire normally,
and a later explicit open is a different grant. This does not permit abandonment
or replay of business effects. Cross-owner replay is prohibited.

The WS sender rechecks the actual grant under the existing shared revocation lease
and holds it through send (maximum five seconds). A transport failure propagates;
it is not a second rejected-read ACK. The current tests cover revocation before
entering this final sender, not every network delivery interleaving after send has
begun. No browser/native claim is made from React fixtures.
