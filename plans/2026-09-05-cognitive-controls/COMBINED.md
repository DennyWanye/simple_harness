# Primary cognitive panel composition

2026-09-05. Local candidate only; native/full remember-use-correct-forget still pending.

Combined the four reviewed-scope frontend files from4eba6acb with the actual
PrimaryChatView and backend6a71bcf6. Main controller exposes verifiedOwnerKey only
when its current signed profile-bound connection is ready. The parent retains
CognitiveRequests across panel close/show/reconnect; changing verified owner clears
old actions. Full process restart still cannot recover those in-memory action IDs.

The normal Primary header now has a Memory button, without a disabled feature flag.
The panel displays actual cognitive labels, not legacy facts. It is unmounted when
chat is hidden or current identity/state is unavailable. Exact successful forget
ACK calls PrimaryController.refreshLatest synchronously: old history/detail unmount
before any new read completes. No old controlWS/session/fact endpoint is used.

## Validation

Main frontend dependency directory was initially absent. The first npx attempts
did not execute project Vitest/TypeScript; stopped that npm process and retained
the missing-dependency logs. Linked the existing main node_modules only after
verifying all three checkout package-lock.json SHA256 equal
5d3c7991ad3e8e9ee11464dcdebd260dfd22e35bc606e97b2683d79c39a66f16. No main dependency install/lock modification.

Final commands from tauri-app, using explicit project binaries:

```sh
./node_modules/.bin/vitest run src/views/PrimaryChatView.test.tsx src/primary/controller.test.ts
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/eslint src/primary/controller.ts src/primary/controller.test.ts src/views/PrimaryChatView.tsx src/views/PrimaryChatView.test.tsx
```

24 passed (Vitest2.1.9), typecheck and directed lint passed. Added actual parent/panel
composition case: bound owner → list → click forget → exact ACK → displayed history
retracted while all follow-up reads are held. Existing disconnect case also checks
verified owner key disappears and global-ready cannot restore it. The leaf13 cases
remain separately recorded in FRONTEND-CONTRACT; not rerun or added to this count.

Raw evidence under `.local-test-evidence/2026-09-05/primary-candidate/`:
- `cognitive-view-combined.log` SHA256 `23eb26f21d2f99d6696182257e5912ea5861822f1fe0b982d980e6afab68d702`.
- `cognitive-view-typecheck.log` SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- `cognitive-view-lint.log` SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
