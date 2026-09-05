# Forget ACK lifetime repair — scoped handoff

2026-09-06. Base `cf4d8e0a`, isolated `feat/primary-forget-ack` at
`/Users/denny/projects/simple_harness-forget-ack`.

## Confirmed source defect and correction

The real production suppression path awaits `display_invalidation.changed()` before
returning its exact receipt. `human_memory_changed` clears controller content state;
PrimaryChatView previously conditioned the panel on that state. Its unmount disposed
CognitiveRequests' write correlation and incremented lifetime. A valid same-owner ACK
therefore became unknown; retry could repeat the same invalidation/unmount loop.

Only controller and PrimaryChatView product files change. `primaryRef` is an opaque
identity acquired from a validated state/history pair in the current epoch. It is
retained with actual verified readiness in one reads-only invalidation update; it
is cleared on real authority invalidation. The parent mounts the memory controls
using that identity and actual bound readiness, independently of history read state.
Old state/messages/details still retract immediately; memory/graph readers consume
their existing invalidation events. No old read content or cached authorization is
retained. SDK ACK fields, action IDs, unknown semantics and backend are unchanged.

## Verification layers

- Actual React parent/controller/boundPort/CognitiveRequests/Panel composition with
  deferred protocol-fixture responses: first decisive test **1 red / 6 controls**
  before product edits. Expanded counterfactual original-source run **3 red / 6
  controls**. Later test-only refinement additionally completes the outstanding
  empty-list reply and explicitly refreshes before checking graph stays blocked.
- Candidate focused suite **47 passed** (parent9/controller21/cognitive11/graph
  request5/graph lifecycle1). Covers invalidation before first ACK, real 15s timeout
  then identical action/different transport ID retry across another invalidation,
  synchronous history/detail retraction, fresh graph read after confirmation,
  disconnect/rechallenge/owner-change late ACK rejection, wrong-action ACK retaining
  pending despite a fresh empty list and blocking graph. This is React/protocol
  integration, not a new native/Provider or backend/SDK acceptance.
- Typecheck, focused ESLint and production build PASS; existing bundle-size warning
  remains. No package/pin changes. Independent npm directory copied from the previous
  isolated layout tree, not a symlink to main.

Commands (from tauri-app): `npm test -- src/views/PrimaryChatView.test.tsx src/primary/controller.test.ts src/primary/cognitiveRequests.test.ts src/components/PrimaryMemoryGraph.test.tsx src/primary/graphRequests.test.ts`; `npm run typecheck`; `npm run build`;
`./node_modules/.bin/eslint src/primary/controller.ts src/views/PrimaryChatView.tsx src/views/PrimaryChatView.test.tsx`.

## Read-only native Host evidence

Main's native source9b57/M0612 observation is independent: canvas interaction passed,
then the UI remained unknown after Forget/retry. `08-forget-blocked-full.png` was
visually inspected and shows the graph's unconfirmed-forget message. The06/07 AX files
are incremental no-change records, not complete accessibility trees; all raw originals
remain unchanged under main `.local-test-evidence/2026-09-05/human-memory-resume/primary-ui-q9xgypw1/`.

Read-only Host S1 query located the latest action:
`evidence_id=5d7d287b-4d10-51bd-82a9-60ec0fc5db2e`,
target `cognitive-memory-d438b6f707f59c9b1013d12142baf35a58eb935bd282fa30f6883e3ed65d7fc7`,
revision1, first committed_at `1788626399.11184`, domain `host-cognitive-action/forget/v1`.
The source state.db had no WAL, and whole-file hashes matched before/after the
immutable/read-only SELECT. No DB writes/copies, SDK private queries or native actions.
This establishes Host admission, **not** UI receipt delivery or a new SDK success.
The UI action UUID is hashed in this domain and cannot be reconstructed from that row.
The fix retains existing in-memory action behavior; it adds no restart recovery ledger.

New raw evidence stays ignored under `.local-test-evidence/2026-09-06/forget-ack/`:

| Artifact | SHA-256 |
|---|---|
| `red.log` | `5db448f0411a18f2ae49765a5f17f67615132834ce80cfcc32f9a8bbd700fab8` |
| `red-final.log` | `4bdbfe58b1be6d3cb06931e1a57e963ce075fe960825ec3cc3437aaf1f938575` |
| `focused.log` | `ed571c83ba44a6730522cf846471e431fe31c86a5e2b8e84129f6b911fd622fc` |
| `build.log` | `33120339da0b37374cf16fc6136df9b7623cfa9a08bc4278e38287f7b03ef436` |
| `host-action-readonly.json` | `b4a08476b800312a50bfe64cf49072f7c0fd1d212eccab6a95e41cd477eedd5a` |

## Review / integration boundary

Dirac fixed-byte review of `c64d6efaf5706c1be0c8e2b2b903f7633923f82d`: scoped
source/React protocol integration ACCEPT, no leaf P0/P1. Both product files match
the reviewed bytes; all five indexed artifact hashes and final parent9 result were
independently checked. No suite/native/Provider rerun by the reviewer. Main owns cherry-pick/exact native
build and final Forget ACK interaction. Do not infer confirmation from the missing
memory/history, clear pending by hand, recreate the already-forgotten source, or
claim HM/program PASS. The seven-node label overlap remains a separate readability
P2. HUMAN audit grant is paused, with uncommitted draft source/tests preserved in
`simple_harness-audit-access`; it is not complete or verified.
