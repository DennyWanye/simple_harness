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

New native frontend build succeeded (`SimpleHarness Primary 0905.app`), using frontend commit `18f85332` in this combined branch. Runtime source is externally selected at launch; launch record binds exact checkout HEAD and binary SHA-256. Native UI/provider validation has not yet run.

## Remaining boundaries

- Full history suppression still lacks Memory-owned reverse lineage and a batch visibility snapshot; a separate SDK candidate is being implemented. Existing source-level filtering is not a complete memory-forget proof.
- `create_new` needs a real per-task child root and active unscoped binding authority. Existing dynamic runtime proof covers resume_existing into a genuinely pre-bound scope, not new project creation.
- Manual binding UI, attachments/slash/realtime, task/artifact/context inspection and original program quality gates remain incomplete.
- The old provider-unknown root and gate failures remain historical evidence; this run does not replace them.
