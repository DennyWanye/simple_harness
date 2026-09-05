# Primary integration — 2026-09-05

Isolated branch `feat/human-memory-primary-integration`, main base `c183fe70`.
Combined runtime `74425388`, API `fbc026a0` + `dc83c306`, frontend `829e21d7`.
No main checkout cutover, release, or program completion claim.

## Runtime / public API verification

Real installed Harness 0.7.2 + actual Host/SDK SQLite + deterministic Provider: new unscoped and genuine legacy scoped terminal receipts feed the same public page/detail API; close/reopen preserves message references and performs no additional Provider call. Changed raw SDK event ID/hash is rejected. No historical evidence is deleted to create legacy fixtures. Suppression policy is explicitly injected in this cross-component test; public Memory-backend behavior is covered separately by the API suite, not inferred from this fixture.

Command from this checkout:

```sh
PYTHONPATH="$PWD/backend" /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/memory/test_primary_runtime_api_integration.py backend/tests/memory/test_primary_read_api.py backend/tests/memory/test_primary_control_binding.py backend/tests/execution/test_primary_foreground_runtime.py -q -p no:cacheprovider
```

Result: **51 passed in 27.63s**. Raw log remains ignored at `.local-test-evidence/2026-09-05/primary-integration/runtime-api-first.log`. SHA-256: `bc763642d0bc3b03a0b0b977a0550ba81c69820095018c692aec9caa4a7ef378`.

## Native ordinary chat and restart observation

Actual native candidate: Host `87c42b43ab14363f6e5dc90df041327c930e583f`, including frontend retry correction `e84cade2`; `SimpleHarness Primary P18120.app`, binary SHA-256 `b7c556a527a40629ba721d38399533e6ff61beb285238c825f1e6c6cf658d307`. Frontend backend port was compiled as 18120; Tauri itself launches this checkout's Python backend. The dedicated runtime environment installs the unchanged exact integration-vendor SDK wheels (Harness 0.7.2, Memory 0.6.3, Service 0.3.12), borrowing other dependencies from the main environment. It is not an independently resolved dependency lock.

Through native coordinates/input, sent “请只回复：主对话链路正常。” and observed the real gpt-5.5 response “主对话链路正常。”, cleared draft, and idle queue. Host Run `72ae10bb-2921-5b1d-bb7d-0e5bf2e5fcce` completed; TaskScope count remained zero. Foreground Provider call took 2993 ms; the separate analysis call took 10333 ms and its batch/job reached applied before quitting. These are single-run timings, not a performance distribution or memory quality acceptance.

After normal native quit, relaunched the identical binary/source with the same user data. Native AX showed both previous messages. A subsequent read-only ledger comparison confirmed unchanged identities and counts: one foreground invocation, one analysis invocation, one applied job. No completed invocation was resent. The next native capture/input was blocked by the Mac lock; the second-turn Provider history-use case remains **PENDING**, not passed.

Two preceding carrier failures are retained: `primary-ui-j475lsei` failed SDK installed-origin validation; `primary-ui-0p2sx1j1` had an incorrect frontend compiled port and was closed without a test message. Correct SDK installation origins and a rebuild with port 18120 preceded the successful run. No unrelated port-8100 process was stopped.

Raw evidence stays under the main checkout's ignored `.local-test-evidence/2026-09-05/human-memory-resume/`:

| Relative evidence | SHA-256 |
| --- | --- |
| `primary-ui-u3ajfchy/04-first-response.png` | `8a07824af5bc94bc7d61f09ad598bd3727ecdeb3c203840f2113beb9a3b9f07a` |
| `primary-ui-u3ajfchy/04-first-response.ax.txt` | `44ae72e630217c82ced0b84620a929cf7dfb2c22416a148a1a413f70257fbe24` |
| `primary-ui-u3ajfchy/first-turn-ledger.json` | `da0cfd5a1668e33f0e036bc0addfa5fac69eb87dcfa1e1c8ace4436d63cf0bdf` |
| `primary-ui-u3ajfchy/before-restart-jobs.json` | `6de18bd77c96c4658ff98b30e43cc37fe8678d1bb9dd5a6afaceb1ca2a01132b` |
| `primary-ui-h_39qzgm/restart-no-replay.json` | `bbae8336d128b385a1d5b136dd509b364b03d5a7228cde1f92aa91e70b1e49a1` |

## Remaining boundaries

- Full history suppression still lacks Memory-owned reverse lineage and a batch visibility snapshot; a separate SDK candidate is being implemented. Existing source-level filtering is not a complete memory-forget proof.
- `create_new` needs a real per-task child root and active unscoped binding authority. Existing dynamic runtime proof covers resume_existing into a genuinely pre-bound scope, not new project creation.
- Manual binding UI, attachments/slash/realtime, task/artifact/context inspection and original program quality gates remain incomplete.
- The old provider-unknown root and gate failures remain historical evidence; this run does not replace them.
