# S6 Cytoscape display leaf — approved selection and implementation contract

Selection authority: Memory main5c5407b, program EXECUTION-REVISION-2026-09-05.md.
This Host leaf contract references that approved choice; it does not amend Memory main.

2026-09-05. plan-status: finalized (user explicitly selected Cytoscape.js and authorized
this isolated implementation). Authority: Memory plans/2026-08-29-human-memory-digital-twin/acceptance.md
HM-AC-6, HM-S12, HM-TO-A6, slices/S6-ui-and-program-verification.md Task3,
EXECUTION-REVISION-2026-09-05.md loop5 and5c5407b approved selection. Full original AC remains required.
Implementation base Hostbf8f9f7d9bcf9f1cbe0aabfe5e31ace7b7aefd4c; earlier5bb WIP preserved separately.

## Approved technology delta

The user's latest explicit selection replaces only S6 Task3's pure React+SVG / zero-new-
dependencies implementation choice with Cytoscape.js (https://js.cytoscape.org/), hosted
locally in the existing React/Vite bundle. Pin cytoscape3.34.2 (bundled TypeScript types)
and lock npm integrity. No CDN, separate graph database, extensions, telemetry, new
framework or memory reasoning engine. Layout/zoom/pan/filter are user display operations;
no graph algorithm output is sent to recall, Context, the model, tool choice or execution.

## Actual existing capability and gaps

Memory manager.public get_twin_graph_view(principal=...) returns a server-filtered view
of canonical four-type memory nodes and real active relation edges. Nodes include status,
lifecycle/epistemic/conflict/verification/confidence, immutable source hashes and action
capabilities; edges include exact endpoints/relation kind/hash. Hidden endpoints/relations
are already excluded by the Memory ordinary projection. Host primary.memory.list currently
forwards only a limited node subset, losing edges and metadata; primary.memory.forget is
an existing verified HUMAN exact action with durable unknown/retry handling.

Add a thin graph read through the same Human API/service, without SDK source/private SQL,
new persistence or mutation authority. No fake Task/Project/Person/etc. nodes where the
public DTO supplies none. No fake edges or inferred joins. Current edge DTO lacks the full
canonical mutation target/revision/proposal authority needed for relation correction/forget;
this leaf does not fabricate it. Existing list forget remains available; graph reopens/fresh
reads reflect actual suppression. Controlled audit/source-body reading and complete relation
correction UX remain original required follow-up, not marked complete by this viewer.

## Wire and ownership

Operation primary.memory.graph over existing verified HUMAN human_memory_request;
request_id stays transport-only, no invented SDK Run. Request {primary_ref, node_limit?,
edge_limit?}; integer (not bool) limits1..200/1..400, defaults80/160. No caller principal,
subject, authorization or graph content fields accepted. Resolve actual runtime principal
and owned primary; validate view.subject; late verified connection fence before response.
Response {primary_ref, view_ref, generated_at, source_payload_hash, nodes, edges,
truncated:{nodes:bool,edges:bool}}. Node/edge records derive solely from that public view;
exclude redacted nodes, then edges whose endpoints are excluded/truncated. Limit text and
source refs on the server; node/source hashes refer to SDK source records, not a claim that
truncated Host JSON has the original SDK content hash. Output sizes are bounded; current
SDK graph collection is a full scan, not claimed bounded computational work.

React graph component + request owner subscribe to actual bound port and verified owner.
Read only when active/ready; disconnect/rechallenge/owner change/privacy invalidation clears
canvas, details and pending reads immediately. Old response cannot repopulate a new epoch.
Reconnect/focus/manual refresh performs fresh reads; no polling and no persisted graph cache.
Existing CognitiveRequests action IDs remain parent-owned and unchanged across graph tabs.
Rendering uses bounded local layout and escaped text, with keyboard/text alternative.
Suppress/correct never optimistically mutates canonical graph data. No new Agent tool path.

## Decisive oracle before implementation

| Case | Expected | Original mapping |
| --- | --- | --- |
| G1 | Real public SDK mutation creates canonical nodes + actual relation; authenticated Host graph returns exact endpoints once, relation not duplicated as node. Empty/redacted/limited data never invents nodes/edges. | HM-AC-6/HM-S12 |
| G2 | Existing public suppression of endpoint (and relation through a public test mutation/SDK authority) removes affected edge; close/reopen does not revive it. | HM-AC-1/6 |
| G3 | Wrong subject/primary, malformed limits/authority fields, rebind during slow graph read reject. UI old response after disconnect/owner/privacy change cannot restore details/canvas. | HM-AC-1/7 |
| G4 | Actual Cytoscape local renderer: graph/text views, label/type filter, node/edge selection, zoom/pan/fit; keyboard reaches same details; resize/unmount cleans canvas/listeners. Local browser layout proof separate from native/program proof. | S6 Task3 |
| G5 | Viewer sends only HUMAN graph reads, never queue/model/context payloads; graph source remains public canonical projection, not a frontend knowledge store. Existing forget unknown ACK tests remain green. | HM-AC-6/7 |
| G6 | Isolated npm dependency directory, exact version+lock, typecheck/focused frontend/backend checks and local Vite bundle. No18120/native/paidProvider/push/tag. | HM-AC-8 scoped engineering |

Three dependent tasks: read API+public-fixture proof; React/request/parent integration;
local renderer verification+independent review+architecture/handoff. No program machine
finalize or full AC pass at this leaf endpoint. Full native/provider/corpus and relation
correction/audit requirements remain separately pending under the unchanged parent plan.

## Actual invalidation producers and final boundary

Main injects one MemoryDisplayInvalidation instance through the existing HUMAN service
factory and into the actual MemoryAnalysisLane. No new service registry slot or SDK ledger.
Explicit suppress attempts emit human_memory_changed with empty payload after SDK return,
including unknown ACK exceptions. Actual public runner APPLIED emits the same hint; this
includes successful no_mutation and is not a claim of cognitive writes. Idle emits nothing.
Notification is best effort, bounded500ms; errors cannot undo/retry the SDK operation.
Its process-local generation advances before broadcast. Graph API captures the server-owned
generation before dispatch and rechecks AFTER its last async identity fence. Same-binding
slow-read/forget and final-fence/forget races reject the old response even if broadcast fails.
This is not a durable SDK global privacy epoch or a serializable cross-process send guarantee.

Frontend also subscribes to actual CognitiveRequests pending and matched-forget lifecycle:
a forget already started before graph mounting blocks reads; unknown remains blocked until
resolved, without new action IDs. Updates clear old canvas/details and invalidate in-flight
reads; reopening always reads again. A selected detail is bound to the exact current view.
G3 additionally requires actual producer suppression/ACKloss and SDK runner REVISE/no_mutation
proof, final-boundary scheduling counterexample, and pending-forget→switch-tab control.
