# Host terminal identity installed leaf results

Last updated: 2026-09-06. Base 3dd76242c5fb33e23cfaf82e4105f5125b5040d1.
Isolated worktree simple_harness-terminal-audit-identity; coordinator WIP retained and
completed here. No SDK source change, rebuild, push, main runtime switch or native run.
Dirac independently scoped ACCEPT fixed source756011caffe1837875934bf458f053a8685eb701;
no leaf P0/P1. Review used fixed source and existing installed evidence only.

## Installed inputs

- Harness0.7.3 wheel SHA256 `1a9ed5c95e6cddd4e0ccd85124320a6001008740a53213712fc89f3467cb4cd7`.
- Fixed SDK buildsource `0282fa982995b24bc893fdf6bed69d2caacd6587`.
- Existing isolated venv uses installed site-packages, no SDK overlay. All164 package
  files byte-equal the vendored wheel; Host candidate manifest verification passes.
- Memory0.6.12 installed, unchanged; no Memory or Service audit expansion.
- Original frozen072 wheel remains unchanged. Vendored073 and its small identity
  manifest are the product dependency, not raw test evidence.

## Selective validation (overlapping runs, do not add totals)

Evidence base `.local-test-evidence/2026-09-06/terminal-audit-identity/`.
`PYTHONPATH=backend`; Python is `<base>/venv/bin/python`.

| Stage | Actual result | Exit | Boundary |
|---|---|---|---|
| Coordinator's existing two modules | 22 passed,2 failed /15.80s |1| Both stopped on v1 rule assertion; original retained |
| First new identity tests before resource pause |15 passed /10.65s|0|12 namespace/ref/state/missing negatives across ordinary+legacy,2 actual positive,1 non-null/reopen |
| Resource-admitted single-process group |21 passed,2 failed /14.27s|1|New21 identity cases + two changed existing findings cases |
| Necessary two failed-case retest |2 passed /1.61s|0|Only late-source fixture and actual Provider/driver grouping, no green baseline rerun |

Commands for the admitted group (the saved wrapper adds a 180s deadline, a 2GiB
own-process-tree RSS stop and rejects local model imports):

```sh
PYTHONPATH=backend <base>/venv/bin/python <base>/bounded_pytest.py backend/tests/operation_audit/test_terminal_identity.py backend/tests/operation_audit/test_audit_recovery.py::test_findings_bind_actual_provider_operation_and_unknown_pricing -q -p no:cacheprovider
PYTHONPATH=backend <base>/venv/bin/python <base>/narrow_pytest.py backend/tests/operation_audit/test_terminal_identity.py::test_late_host_source_change_rejects_next_page_preserving_saved_prefix 'backend/tests/operation_audit/test_audit_recovery.py::test_findings_bind_actual_provider_operation_and_unknown_pricing[True]' --basetemp=<base>/narrow-tmp -q -p no:cacheprovider
```

Resource logs: group PID8828 peak150480KiB, narrow PID9024 peak138080KiB; both
exited, no stop threshold, no surviving owned test/backend/model process. Single
pytest worker, no xdist. Deterministic Provider adapter and development Memory fixture;
no local embedding model, paid Provider, native App, model download or build.
A runtime import guard rejects torch/transformers/sentence_transformers/mlx/
onnxruntime/llama_cpp; real SQLite/public APIs and production Host adapters still run.

The first new late-source fixture tried to alter a protected completed Host head;
`foreground_run_transition_invalid` correctly rejected that setup. Final fixture
selects a separate real Host DB during the read, leaving both original ledgers intact.
The old findings test assumed one global error; H073 actually supplies provider.invoke
(head+transition for the same invocation) and a distinct failed runtime.driver boundary.
Final exact assertions check both owners, their per-owner source hashes and stable
provider_request_rejected code, while actual Provider calls remain1. No product
error or assertion is hidden by allowing arbitrary failures or broadening counts.

All selected final cases have passing evidence via the group plus the two narrow
repairs; the complete unchanged two-module baseline was not rerun. This is an installed
terminal identity slice, not a full Host suite/native/every-operation completion.

## Decisive boundaries

- Ordinary and real legacy scoped completion both match exact raw SDK terminal proof;
  original Host evidence hashes unchanged. Host envelope and SDK record hash cannot
  substitute for SDK payload hash; wrong ref/state/missing proof persists zero pages.
- A different real Run's public proof fails both whole-page and relabelled proof checks.
- Late source substitution rejects the next page, preserves earlier pages/cursor, and
  makes no extra Provider call. Independent foreign setup's one call is counted separately.
- Real non-null committed-turn public head/created receipt and exact terminal proof
  survive stack/DB reopen, continue the same cursor, keep outbox unchanged and Provider1.
  This proves durable turn production; it does not claim dispatch/application to Memory.
- New v2 jobs do not reuse or modify old v1 pending/unavailable/partial/enumerated
  journals. The version-key fixture uses existing journal APIs with actual public SDK
  pages; it does not pretend to rerun an old SDK producer.

## Local raw evidence hashes

Logs/DBs/watchdog remain ignored. Resource-admitted temp DBs were copied to
`bounded-tmp-retained/`; original failures are not overwritten. No raw log is committed.

- `existing-installed.log` SHA256 `75deb9c6642361da40212778e46efaf5a030d141af2abcd51215e5c8e5cf864d`.
- `identity-first.log` SHA256 `3cf2cbe4b772e90ad369dec1a25a16ad48f61f3c7451170658ac49c8a151e3de`.
- `identity-bounded.log` SHA256 `2b080ac1b75302f7e80b2598c36ccaf168e78cfefbf3572801c35874c8d3b7a7`.
- `identity-bounded-resource.json` SHA256 `79164fb68ad1dca6755b63aced66e3628842e4e57e70a55011bd6bf7125b71e0`.
- `identity-narrow.log` SHA256 `0f756cfd33f42f4e339eace28b1a1cfc7d666f94c80b547be73f165308a0ea9a`.
- `identity-narrow-resource.json` SHA256 `8098b53a33e328f8f486f44152acfee3585fb95fbe71915a2464c4c667d508f2`.
- `installed-identity.json` SHA256 `20316e125be720228e229d05871d30e06fdc64a76b838216f3523120625d0b39`.


## Independent review closure

Dirac checked fixed756011ca, the seven evidence hashes, exact vendored wheel/manifest
and existing164-file installed identity proof using git/text/shasum only. No Python,
pytest, model, build or database probe was repeated. Public typed proof, one Host
read-only source/identity transaction, legacy raw SDK unwrapping, exact page matching
and v1/v2 journal separation are scoped accepted. No native/full-suite or whole-program
completion follows from this review.

Precise late-source evidence limit: the test switches Host source DB before
PublicAuditReader returns, and the existing second sources.verify rejects it with
source_binding_invalid. It does not independently exercise source replacement after
every await inside the new verify_terminal_page. The same-transaction implementation
was reviewed; those individual windows are not reported runtime-tested. No additional
test is required for this accepted leaf.
