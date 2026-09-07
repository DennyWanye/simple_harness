# Cognitive controls frontend leaf

2026-09-05. Frontend-only delivery on `0c10cd95` (base `15009a6e` plus main's
action-evidence callback). Main owns every backend entry, callback, test, and
PrimaryChatView integration. The earlier uncommitted backend draft/tests remain
in this worktree for main to reuse; they are not part of this commit.

## Frozen wire

Use the existing authenticated `PrimaryPort` and `human_memory_request` protocol.
`request_id` correlates one transport attempt; `action_id` identifies the durable
forget action and stays identical across explicit retries. No fake session/Run.

- `primary.memory.list`: `{primary_ref, limit:20, cursor?:memory_id}`.
  Result `{primary_ref, items, next_cursor}`. Each item has `memory_id`, positive
  safe-integer `revision`, `content_hash` (64 lowercase hex), `label`, `status`,
  `can_forget`. The client validates at most50 items, labels512 Unicode codepoints,
  status64, IDs512 characters; `next_cursor` must be null or the last displayed ID.
  Pages replace one another. These are response bounds, not a bounded SDK graph scan
  or a snapshot-paging claim. Graph content stays in this USER display component.
  The panel displays the label, not raw IDs, revisions, hashes or SDK status strings.
- `primary.memory.forget`: `{primary_ref, action_id, memory_id,
  expected_revision, expected_content_hash}`.
  Accepted result requires exact `primary_ref/action_id/memory_id`, `status:'applied'`,
  nonempty `directive_ref/evidence_ref`, and a valid `decision_hash`.
  Main backend `506d98f3` supplies these seven fields, without `view/refresh_required`;
  the frontend always requests a fresh page after the matching ACK.

Stable pre-admission rejections `primary_memory_target_stale`,
`primary_memory_request_invalid`, and `primary_memory_action_invalid` clear an action
only if it has no earlier unknown attempt. Identity/primary binding failures keep
the exact action. A later stable rejection cannot erase an earlier uncertain write.

The later simplified `idempotency_key/outcome:'forgotten'` proposal was withdrawn;
it is not this frontend contract.

## Parent integration

```tsx
const [cognitiveRequests] = useState(() => new CognitiveRequests());
<PrimaryMemoryPanel
  port={actualBoundPrimaryPort}
  primaryRef={actualPrimaryRef}
  verifiedOwnerKey={verifiedOwnerKey}
  ready={visible && actualSocketBound}
  requests={cognitiveRequests}
  onForgotten={clearHistoryAndDetailThenRefresh}
  onClose={closeMemoryPanel}
/>
```

`verifiedOwnerKey` is the existing `${profile_id}:${profile_generation}` key from
the actual confirmed bound connection. Global `identity_status.ready` is not
authorization. Parent must update props on verified bind/rebind and retain the
same requests instance above conditional mounting. `requests` and `onClose` are
optional component props, but parent retention is required for the integrated
unknown-ACK/remount guarantee. Hiding clears displayed items and suspends reads.
Verified owner/primary replacement clears old actions; same-owner reconnect keeps
exact payload/action IDs. No automatic mutation replay, polling, legacy fallback,
physical deletion, undo, or browser storage ledger is introduced.

`onForgotten?: () => void` runs synchronously only after an exact ACK, before the
next memory read is awaited. Parent must synchronously invalidate history/detail
and their pending reads, then initiate a fresh authenticated refresh. A synchronous
callback error cannot turn the confirmed forget into unknown. The callback should
own errors from any asynchronous refresh it starts. Unknown/mismatched/revoked ACK
does not call it. Full process restart does not retain in-memory action IDs; backend
exact replay support is a separate property, not UI recovery of lost IDs.

## Focused validation

Commands run from `tauri-app`, all exit0:

```sh
./node_modules/.bin/vitest run src/primary/cognitiveRequests.test.ts src/components/PrimaryMemoryPanel.test.tsx --maxWorkers=1 --reporter=dot
./node_modules/.bin/tsc -b --noEmit
./node_modules/.bin/eslint src/primary/cognitiveRequests.ts src/primary/cognitiveRequests.test.ts src/components/PrimaryMemoryPanel.tsx src/components/PrimaryMemoryPanel.test.tsx
```

13 tests pass: label display with canonical identity kept in the request; explicit
click; request/ACK binding; privacy
invalidation and late reads; unknown→same-owner rechallenge→original-action retry;
new verified owner reset; stale rejection after prior uncertainty; actual component
unmount/remount; parent invalidation before fresh-read completion; callback failure;
ACK queued immediately before auth revocation. Read and write clients are separate,
so privacy refresh cannot discard a mutation ACK.

Raw logs remain ignored under `.local-test-evidence/2026-09-05/cognitive-controls/`:

- `frontend-frozen-wire.log`: SHA-256 `69fbceddadac81df1543423b17d565992618e3ca3c4ddb76c8c3941a49b78c6d`.
- `typecheck-frozen-wire.log`: empty successful log, SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- `lint-frozen-wire.log`: empty successful log, same SHA-256.

This is frontend protocol/component evidence, not real signed HUMAN/backend
composition, SDK suppression/outbound proof, native visual proof, or loop2 PASS.
Main owns those integration and acceptance boundaries. No native/Provider was run.
