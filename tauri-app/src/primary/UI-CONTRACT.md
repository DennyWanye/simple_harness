# Primary UI — first value loop (2026-09-05)

Authority: Memory S6 Task 1 plus EXECUTION-REVISION-2026-09-05 sequence 1.
Backend wire details are owned by Dirac; this document records frontend obligations.

- One persistent primary entry; no reachable Session catalog/create/switch/delete.
- Reuse App ControlChannel identity_bind socket and existing buildIdentityBind flow. Only this connection’s companion_profile_bound enables primary.open → primary.state → primary.messages.page. Global identity_status is not a lease. Current-connection bound cache handles late mounts and is cleared before reconnect listeners, unbind or rechallenge.
- Requests use top-level type/request_id/operation/request. Only an exact successful queue.enqueue receipt clears the submitted draft/attachments. Socket send is not acceptance. Errors preserve the draft.
- A timeout/disconnect after send is an unknown result. No automatic mutation replay. Explicit retry preserves delivery_key and submitted content. A different draft cannot silently replace an unresolved delivery.
- Reconnect drops old read payloads/cursors/details and pending correlations, waits for fresh identity, then refetches. Suppression/privacy invalidation clears visible content before refetch. No local durable history cache or synthetic chat_v2 session.
- State/control uses exact run_ref+generation, never blind current-run stop. ACK means command accepted, not run terminated.
- Reads are bounded, backend scan window ten turns, frontend requests at most20 messages (server hard cap50). Refresh replaces the page. Older pages/details are explicit and bounded. Event notifications and finite follow-up reads refresh status; exhausted budget offers manual refresh.
- Reuse global Provider settings, permission popup and text-attachment input. The primary header links to global settings; it does not call legacy session_set_model. Unwired capabilities must fail explicitly; do not silently drop attachments, execute slash commands as text, or label model changes saved without ACK.

Test oracle before implementation: delayed/error/wrong-ID ACK; double-send guard; timeout and reconnect with zero automatic enqueue replay; stable delivery key on explicit retry; identity/privacy invalidation against late replies; bounded reads and targeted controls; normal durable user/final display; no Session management entry; model/attachment unavailable means no false success. Frontend tests/typecheck only in this worktree; native App/provider and whole-program acceptance remain main-owned.

## Frozen read contract and remaining integration

Dirac owns `plans/2026-09-05-s6-primary-preparation/PRIMARY-API.md` in the primary-api worktree. History operations are `primary.messages.page` and `primary.messages.detail`, both with primary_ref. Page items use message_ref/turn_ref/run_ref/delivery_key/role/text/has_more/total_chars, roles user/assistant/tool/artifact; next_cursor may exist on an empty filtered page. Preview1024 Unicode codepoints, detail chunks4096, UI replaces each detail chunk (does not accumulate unbounded text). State revision is opaque and is not a privacy epoch; every refresh/detail calls the server again. queued_count_truncated means “at least N”.

`human_memory_changed` immediately invalidates visible read data and old read requests, then refetches. Pending enqueue/control ACK correlations survive read invalidation. Disconnect/owner changes cancel them without replay. Up to12 fallback reads at2s while active; real change events, focus and manual refresh renew the budget. Exhaustion never fabricates a failed/terminal run.

Runtime events use current_run.sdk_run_ref + execution_session_ref, never primary_ref as a Session. Permission pending-list explicitly targets the real execution session; original request/decision/nonce/version are echoed. Stop pins the run/generation visible when the button was rendered. Project directory selection reuses the existing card but says submitted/waiting, because current response_applied is only a backend log. Carver/main must verify actual TaskScope manual-binding/route interaction, permission recovery and event delivery; legacy directory callbacks are not proof of the new SDK path. Tool activity is live bounded metadata (last20); durable tool/artifact public text comes from primary history; full artifact/context/TaskScope inspector and primary attachment/slash/realtime integrations remain unfinished. Existing global settings/skills/artifact-library navigation is retained. No App/provider or whole S6 acceptance is claimed.

## Handoff blocker and verification

Unknown enqueue status belongs to the delivery across attempts. A later local send failure or explicit rejection cannot clear an earlier unknown delivery key; only its valid ACK or a verified owner change resolves it. A first definitely-unsent attempt still permits an edited draft. This uncertainty is local state, never an extra queue.enqueue wire field. Regression cases cover both retry failures and the first-unsent negative control.

UNVERIFIED: actual TaskScope manual-binding/context-route project interaction. Legacy project_directory_request is live-only: if used, early mapping/remount/reconnect can lose the decision (conditional P1). Carver confirmed dynamic context_route.create_new does not emit that card. Manual append returns context_route_binding_authorization_required; the real path to wire is binding.manual.propose(scope_ref,root) → binding.manual.decide(challenge_ref,decision) → route.resume_existing. Main explicitly paused legacy pending API expansion. This Manual/route UI is not implemented or production-tested. No new pending API or recovery claim is made. This package is not first-project-task acceptance.

Commands (from tauri-app):
- `./node_modules/.bin/vitest run src/primary src/views/PrimaryChatView.test.tsx src/code-panel/InputBar.primary.test.tsx src/code-panel/InputBar.chat.test.tsx src/code-panel/__tests__/InputBar.slash.test.tsx src/components/WorkbenchShell.test.tsx src/ws/ControlChannel.test.ts src/hooks/usePermissionRequests.test.tsx --maxWorkers=1`
- `./node_modules/.bin/tsc -b --noEmit`

The aggregate run before the final permission-component test was100 passed; PrimaryRunPanel.test.tsx separately1 passed. Logs live only in ignored `.local-test-evidence/2026-09-05/primary-ui/`. Same-owner timeout→rechallenge→bound→same-key retry and control receipt negative outcomes are covered. Actual UI/provider, runtime pending-directory recovery, and whole-program gates remain unverified.

Evidence SHA-256 (relative to the ignored directory above):

- `focused-final.log`: `d81d7cc3530fc8abc5330c4fe0b1a0e6189a5265272bafa882b209b17545c1be`
- `permission-ui.log`: `dcad05689332639774fe1a224e27881c747f8b7fb5af545becfb4f35d8e00670`
- `typecheck-final.log`: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- `lint-new-final.log`: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- `lint-baseline.log`: `719efea2d9d4be170750cdbe5c85b319e03c74353182b54d15ecc87edca03b1b`
- `lint-current.log`: `91066c3305062bc866fff87b5eb12a67ab0ddbebf62f3c058eda0d9ef97e65fa`
