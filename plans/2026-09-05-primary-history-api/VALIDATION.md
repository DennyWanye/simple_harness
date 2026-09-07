# Primary history API validation — 2026-09-05

Base `87c42b43`; isolated branch `feat/human-memory-primary-history-api`.
This is implementation-owner verification, pending independent Dirac/main review.
No app, external server, Provider, background runner, main SDK pin/environment or archive mutation.

## Candidate and commands

Own ignored venv: `.local-test-evidence/2026-09-05/primary-history-api/venv`.
Memory0.6.6 source `9ec59438663272cd9e208de5d2e7b85bf0f40a5a`, wheel
`381d85437537ae1f58f04b84e8332b0774d1e3aff2c6529b3824126071135361`.
Harness0.7.2 wheel `53bded3fea87168e5d2ad9e49fea5f99e1c1edb1d6077b2a52dd62716692f9ed`.
Both packages installed inside the own venv. Memory62 Python files source/wheel/installed,
Harness145 Python files wheel/installed byte-equal. Other Host test dependencies reused
read-only via an own-venv .pth referencing Host site-packages; no source SDK path injection.

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-history-api/venv/bin/python -m pytest -q \
  -o cache_dir=.local-test-evidence/2026-09-05/primary-history-api/pytest-cache \
  backend/tests/memory/test_primary_read_api.py \
  backend/tests/memory/test_primary_visibility.py \
  backend/tests/memory/test_primary_control_binding.py
```

**50 passed in21.44s**. Public Manager + real Host/Memory SQLite lifecycle validates cold USER,
ingested without analysis, public semantic mutation, memory-only forgetting, inherited terminal,
real typed recall exact item commitment, wrong commitment, reopen and unchanged Host raw bytes.
Remaining tests cover paging/detail/exact controls/durable ACK, malformed proofs/foreign primary/
limits/response shape, legacy generated group omission, whole-page late checking, actual signed
in-process WS disclosure/request_id and reconnect during the slow batch. No SDK private DB reads
in the new lifecycle test. Transcript/binding projections in API fixtures are injected; real
runtime composition remains Carver's separate acceptance, not implied by these tests.

Earlier focused run44passed/1failed was a test archive SELECT ambiguous column, corrected to
qualify the Host table; public lifecycle then passed. Initial source-overlay test run25passed/
4failed required adapting the existing resolver fixture to the new public callback and deferring
missing active mapping error until after fresh suppression (hidden active source stays hidden).
No green baseline claim from these earlier failures.

New helper/read model/command API/new tests ruff clean; seven diagnostics in the service and two
existing test files were reproduced at base87c42 (BLE001/RUF022/UP034/RUF059), unchanged in scope.
`git diff --check` clean. No whole backend regression or production/UI acceptance claimed.

## Integration obligations

- Carver owns injection selecting true principal, actual complete terminal/context dependencies,
  current Run recall capture and pre-physical-provider late check with correct rejection semantics.
- No valid short-horizon carrier in this candidate: runtime must reject reuse/outbound, not merely
  hide terminal UI. Hegel's separately designed extension is not represented as installed support.
- Whole dependency union <=256, depth64/edges4096. Turn/return bounds do not claim bounded SDK
  transcript traversal or SQLite execution cost. No cache reuse by epoch/hash/time.
- Existing snapshots are observations at the check; no atomic lock across future network sends.
- Pending independent review and combined native/provider verification; no gate/ledger updated.

Raw files are ignored under `.local-test-evidence/2026-09-05/primary-history-api/`:

| Evidence | SHA-256 |
|---|---|
| `identity.log` | `f86828a337c5fe82d2c61de5094fdb6353f280214359a3ccd6745227ef2a2681` |
| `final-focused.log` | `ec11772f6de855339929d52dce0f8851aa43c3be87266767f9684ece000580bf` |
| `new-code-lint.log` | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| `base-lint.json` | `be73aeb09a7e96a55aafd2a870f76d874c5462b3f4e7363d19cd44ac40d3270d` |
| `focused.log` | `9086eb05a864f8c2f58ca4c055aed456a9eb58b69ee76ec4cbeb92db912bad4e` |
| `lifecycle.log` | `96a8cb52be1ed2c3302d2e9f6528eca38c1cf7c2462ec7201e826ec440278f9d` |
| `visibility-control.log` | `a4ceda2178bb3bf85d4703017a107a5103690b7e564cb37ad9da96b852347c6b` |
