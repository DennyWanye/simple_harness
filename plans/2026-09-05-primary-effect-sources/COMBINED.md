# Primary source/auth/action combination

Last updated: 2026-09-05. Local candidate only; no main cutover.

Combined candidate 48968c8f with reviewed history e31c6efda3f4f4b3f6696d4f670b59250c280a58.
Only merge conflict was the foreground test fixture signature; preserved both
`authorization_factory` and `page_in_store` parameters. Product code merged unchanged.

The original independent unscoped-search/late-forget counterexample now passes on
exact e31c6efd (1 passed / 5.37s). Independent review report SHA-256:
4b7501599e6d600b3eebbe37cfe8406870f744d66666fef11a88ff60725abae8.
Review and log remain in primary-api local evidence `e31c6efd-fixed-review/`.

## Combined verification

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest \
 backend/tests/execution/test_primary_decisions.py \
 backend/tests/execution/test_unscoped_search_late_forget.py \
 backend/tests/execution/test_primary_page_in_sources.py \
 backend/tests/execution/test_scope_disclosure_runtime.py \
 backend/tests/memory/test_primary_runtime_api_integration.py \
 backend/tests/memory/test_primary_effect_sources_v47.py \
 backend/tests/memory/test_primary_cognitive_evidence.py -q -p no:cacheprovider
```

51 passed / 65.32s. Actual SQLite, installed exact Memory0.6.7/Harness0.7.2;
deterministic Provider transport. No native/external Provider execution in this run.
Raw evidence `.local-test-evidence/2026-09-05/primary-candidate/scope-auth-action-combined.log`
SHA-256 `01bce5b9b329c9b00c494d66f5c486f816b8b83d79169f549b0c1d9c2896d5a9`. The independent cognitive callback review accepted 48968c8f
within its four-test Host evidence scope; actual target/suppression/UI wiring remains open.

## Native evidence remains scoped

The separate decisions tree completed native project/file creation at dfdaec4a
(run ox31zoi3), then visible expanded authorization and real mouse approval at
f9cbb7c8 (run qb9nzyda). Recorded in owner docs commits1862e383/b336bed6.
The latter created an empty project in two successful foreground Provider calls;
it did not exercise this combined Memory0.6.7/schema47 candidate. Existing native
visual blocker is closed for that expanded-current-card path, not all layouts or
all S6 controls. No full program or privacy completion claim.

## Remaining boundaries

Generic page-in source projection, selected short-source production indexing,
existing Memory DB upgrade, actual cognitive read/correct/forget UI, full gates
and genuine human240 freeze remain separate work. S5c must use next global48,
with explicit versioned A11 mapping; historical47 evidence is not reused.
