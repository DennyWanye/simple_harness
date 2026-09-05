# Primary SDK authorization candidate results

## Portal layout candidate after 1862e383

Primary-only body portal plus explicit visible-chat gating; fixed-modal focus uses preventScroll.
The existing exact decision target, authenticated port, ACK/unknown handling and permission defaults
are unchanged. Only four frontend product files changed; backend/SDK/pins and loop2 untouched.

Actual WebKit 26.5 (Playwright webkit2311) loads production Workbench, PrimaryChatView,
PrimaryRunPanel and PermissionPopup with the real CSS. The fixture replaces transport only and
intercepts outbound network, uses synthetic decisions, and never starts App/backend/Provider.
1000x700 and 800x560 both pass screenshot + viewport geometry + elementFromPoint + ordinary
mouse hit-testing for expanded details, scrolling, containing-block stress, refresh, remount,
reconnect, hidden/show view and two sequential decisions. The narrow successor screenshot was
also inspected by the implementer. This is browser layout evidence, not independent/native acceptance.

Before the fix, hidden-view Escape issued a deny despite the popup being invisible. Separately,
a deliberate translateZ(0) on the real Run scroll container placed the allow button below the viewport
(top744 in height700; top604 in height560), with hit=false. Both controls pass after the fix.
The baseline without this stress transform did NOT reproduce the native disappearance: native
compositor trigger remains unproven and its visual FAIL is not overwritten. Main owns rebuilding
the integrated candidate and verifying visible mouse interaction on a fresh authorized request.

Focused frontend59 passed, installed existing backend decision18 passed, typecheck and targeted
eslint passed. No full suite or new remote Provider run. Browser scripts live in
`tauri-app/tests/browser/`; this fixture is not part of the production entry or build.

```sh
# repository root; Python must have Playwright and its installed WebKit
/Users/denny/projects/simple_harness/backend/.venv/bin/python tauri-app/tests/browser/primary-permission-layout.py --output .local-test-evidence/2026-09-05/primary-permission-layout/green
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-decisions-native/venv/bin/python -m pytest backend/tests/execution/test_primary_decisions.py -q --tb=short --show-capture=no -o log_cli=false
# tauri-app directory
./node_modules/.bin/vitest run src/primary src/views/PrimaryChatView.test.tsx src/components/PermissionPopup.test.tsx src/components/WorkbenchShell.test.tsx src/hooks/usePermissionRequests.test.tsx --maxWorkers=1 --reporter=dot
./node_modules/.bin/tsc -b --noEmit
./node_modules/.bin/eslint src/components/PermissionPopup.tsx src/components/WorkbenchShell.tsx src/primary/PrimaryRunPanel.tsx src/primary/PrimaryRunPanel.test.tsx src/views/PrimaryChatView.tsx
```

Raw evidence remains ignored under this tree `.local-test-evidence/2026-09-05/`:

| Relative evidence | SHA-256 |
| --- | --- |
| primary-permission-layout-red4.log (hidden Escape red) | `79a5a1edf27a9355a3a5078c229896a181f3ea0061d4cd4143cb73f1ea1d9f5c` |
| primary-permission-layout-red-containment.log | `1b9886b48e67fb72553335d41ac891914fb9852d46734b2aac46e750007fc636` |
| primary-permission-layout-green.log | `88b89b57379bb19402a1d53325f408a3460d35a7c6d4ed93b100634d15a8ad9c` |
| primary-permission-layout/frontend.log | `fcadbf989e285a911e88ea0d37950b0982f5fb9b4fd0ac4ba3370b9442056962` |
| primary-permission-layout/backend-decisions.log | `36403792e8269f8497b9e7f3030749f28f68e4556fa22f3b55d528b007c96ba5` |
| primary-permission-layout/typecheck.log; lint.log (both empty, exit0) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| primary-permission-layout/green/webkit/800x560-next-expanded.png | `845e1c36367920ab1987c3602e1a75b3f3dbea20e23de2260bddf9b523955b6c` |

2026-09-05. Isolated base 5da24d6f67331aeb190f5cb23defd2863674d670.

Initial candidate evidence below is automated fixtures only; the subsequent native section records actual App/Provider results. SDK pins remain unchanged.
Independent review pending at initial candidate commit. Main owns native acceptance.

## Verification

- Backend affected suite: 66 passed in 37.26s.
- Final production authorization fixture: 18 passed in 13.80s.
- Frontend affected suite: 35 passed.
- Typecheck and new Python module/test ruff: passed.

Commands (from repository root unless stated):

```sh
PYTHONPATH=backend /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/execution/test_primary_decisions.py backend/tests/execution/test_primary_foreground_runtime.py backend/tests/memory/test_primary_control_binding.py backend/tests/memory/test_primary_read_api.py -q --tb=short --show-capture=no -o log_cli=false
PYTHONPATH=backend /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/execution/test_primary_decisions.py -q --tb=short --show-capture=no -o log_cli=false
# tauri-app working directory
./node_modules/.bin/vitest run src/primary src/views/PrimaryChatView.test.tsx --reporter=dot
./node_modules/.bin/tsc -b --noEmit
```

The final focused run followed formatting and the legacy-bypass production entry regression.
66 and 18 overlap; do not add their counts. The fixture uses real production policy,
authorization saga and SDK decision/physical file path, plus deterministic Provider and
signed in-memory connection request scope. See CONTRACT.md for exact controls and limits.

## Ignored local evidence

All files remain in this worktree under `.local-test-evidence/2026-09-05/primary-decisions/`.
No request/nonce/version payload dump or screenshots were recorded.

| File | SHA-256 |
| --- | --- |
| backend.log | `0e62d4be678ccebe6d26359a2ee5c6f8159b18704e9160813e57cdf25b5a33c9` |
| production-focused.log | `5eaf6bd66c954fd9ede19afaa847e4c14b5e9e0a243148d06d65b19c0c982c99` |
| frontend.log | `1e0480abc1699d25d0191c48b4bfca90fef4c382cad202775668a9bd2cddbfa9` |
| typecheck.log | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

## Integration boundaries

- Add Carver post-BOUND_WAITING state notification for prompt late authorization hydration.
- Public SDK exact read/decide ports reused; existing Host open-list SQL unchanged. Return <=32 is not a claim of bounded underlying scan.
- Native approval/deny/Stop still main-owned; prior cancelled native Run is not replayed.
- Stopped-history and history-visibility runtime/source work remain separate.

## Follow-up to 794beaa8

A read refresh after approval could change readSequence and swallow the approval timeout.
The decisive new test failed before correction. Mutation completion now checks channel
lifetime independently of the read sequence. Frontend affected suite **36 passed**;
typecheck and targeted eslint passed. Existing main legacy signal/permission controls
**2 passed**. No backend product change in this follow-up.

| Local file | SHA-256 |
| --- | --- |
| frontend-after-refresh-timeout.log | `488cf904b58ac3e6a30b12b27ad8270fbf5855842f3970cdf0e7b8ada828a8c7` |
| typecheck-after-refresh-timeout.log | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| lint.log | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| legacy-controls.log | `baf26f288c0f3253629d61abe5c8fa0d1679f04fe13978ec05a347cc1d6d68b2` |

## Final independent AI review and Stage1 handoff

Independent ephemeral read-only reviewer, fixed product source `81eeb8b0d37005e0321a8ef06c4edf6a4964f92f`
against `5da24d6f67331aeb190f5cb23defd2863674d670`: **Scoped ACCEPT**, no reproducible
in-scope P0/P1/P2. It reviewed code/test proof rather than repeating suites. The reviewer
explicitly verified the refresh/uncertain response correction and excluded native/full-gate claims.
Dirac was occupied with main's other review; this independent process completed this review.
Local `independent-review.md` SHA-256 `b26f968f841323ed3b116f49ddf8c59b72d5de8d289f0fd8078d4f7384eb22c4`. Raw reviewer process output is ignored.

`npm run build` passed (existing chunk-size warning only). Local `frontend-build.log` SHA-256
`756289f7848e2bd4d36ff9191f275daa57fd4ab1103c33848c13ef79bc9c1018`. No native App/Provider operation was performed.

Stage1 integration: `794beaa8` then `81eeb8b0`; this final handoff commit changes documentation only.
Add Carver `61436ccc0c9ffba1eb468412640a3ff6a9c41805`: after durable `record_reconciliation`,
call `_notify_state_changed()` only for `BOUND_WAITING`. Its runtime/test patch passes
`git apply --check` against this candidate. It has not been merged into this owner branch;
no history branch is required for the Stage1 patch. Main owns a fresh native Run on its
previous isolated data; no cancelled/unknown Run replay, privacy/forget or cutover claim.

## Native project run on dfdaec4a — function passed, dialog layout open

Real native App and remote gpt-5.5, exact installed Harness0.7.2 / Memory0.6.3 / Service0.3.12.
Source `dfdaec4a3951043a689f861f5df2fcdf77124b77`, App binary SHA-256
`f29afbc5509ee4af4d0d4ed2c0c195eef6b3d23037d2dc1f6e57ec3613e66860`.
WAITING notification combined; runtime regression 16 passed in 3.34s. Native debug App build passed.

A NEW natural project request was submitted through native text input and Send. The previous stopped
Run was not replayed. Native AX clicks expanded and approved exact context_route(create_new) and
later task_scope_update. Host `e53d0e6b-108d-5383-8026-0d87f3ecf7f2` COMPLETED;
SDK `product-sdk-2eb6601d68486c87fdd6d659a09b794e8c749ee543d1ebf76ab112f2f2d65c17` completed,
two decisions allowed, 11 successful physical foreground Provider invocations. Three failed activation
attempts (one missing arguments, two wrong describe nonce) recovered within this same Run; these are
not clean single-shot tool activation proof. The model recorded task completion and returned the file location.

Actual Scope `e43aa74e-95b2-57bd-a372-c9bd5c127e7b`, title primary-ui-0905.
Actual file `/Users/denny/SimpleHarnessWorkSpace/task-e43aa74e-95b2-57bd-a372-c9bd5c127e7b/primary-ui-0905/README.md`
contains exactly 25 bytes `PRIMARY_NATIVE_PROJECT_OK`, SHA-256
`b2161b533e476855bee6884c82eaeaf492723fb8bea2d96774f69cc904090c82`.
UI final reply and idle status captured. All six accumulated Memory jobs applied before normal App exit 0.

**Open visual defect:** initial authorization modal appeared visibly, but later Primary dialogs were
present/clickable through native AX while absent in screenshots. Native file/terminal behavior passed;
visible authorization usability has NOT passed. Do not count this as complete Stage1, privacy, .067 or full-program acceptance.

Evidence stays ignored in main checkout `.local-test-evidence/2026-09-05/human-memory-resume/primary-ui-ox31zoi3/`:

| File | SHA-256 |
| --- | --- |
| 01-authorization.png | 333fa9e4a6fcf7e3b4db835afbb18e3594c054b3928f6901668d73c7850ea4d1 |
| 02-authorization-details.png | a7cf65d3ea5e8ef6ebf7c93e6aa1465a226924fd85a0a7639fa0377245e45b9a |
| 04-file-authorization.png | a7cf65d3ea5e8ef6ebf7c93e6aa1465a226924fd85a0a7639fa0377245e45b9a |
| 05-completed.png | c0f76a7bf5ccdf6e511b8365e7960ed01a3af4beb5e76c3ea89ac870148f9853 |
| native-project-ledger.json | da337efc3e64bbf31b7b2a7cd27488e4b1e74534bdadcd014fd90c9d4e70cc8d |

Current worktree build/regression logs stay ignored in `.local-test-evidence/2026-09-05/primary-decisions-native/`:

- waiting-combined.log: `453b4f84b3f55d4ad11efa13ad1a5b92aff5ee90da8c718afa213d088164b8b4`.
- native-build.log: `4f9920f994e859864608aa10ecb82babdb6a6725e36e6f1034bbe237a5b3ab18`.
