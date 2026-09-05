# Primary SDK authorization candidate results

2026-09-05. Isolated base 5da24d6f67331aeb190f5cb23defd2863674d670.

Automated fixtures only. No App, real Provider, native database or SDK/pin changes.
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
