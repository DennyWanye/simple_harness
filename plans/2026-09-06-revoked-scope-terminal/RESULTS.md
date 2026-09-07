# Revoked unclosed scope: fixed results

Updated 2026-09-06. Existing H077/M616 installed targets + Host source. No SDK, main, schema or original userdata change. Branch feat/primary-revoked-scope-terminal, base75cd51a5.

## Baseline, before product change

fffcdadc composed the actual ClosureFallback class used by main into the real runtime constructor. No pre-closing task_scope_update was added. r1 False: overall **1 FAIL / 1 deselected, 4.09s**; only final source-body-observation assertion failed.

Earlier assertions completed: actual public write/page/revoke caused SDK FAILED and zero additional physical send; the existing fallback wrote pending without Provider/mutation use; scope stayed dirty at closing watermark0/canonical revision1; Host committed FAILED/SETTLED; cold stack reused exact pending and SDK terminal without retransmission; caller could not promote it to COMPLETED; an independent next input completed. This corrects the earlier extrapolation: current-r3 omitted the production fallback and does not establish main terminal deadlock.

The remaining baseline failure observed one unnecessary source-bearing _scope_observation_tx construction after revocation. No outbound disclosure was observed. This is not a claim of exfiltration or main liveness failure. PG4439 exited1/remaining[]/cleanup_error=null, slot released.

## Fixed source: 2 unique controls passed

Productc6af1ac4 only avoids model observation construction for non-COMPLETED settlement and reports an existing pending debt as pending instead of already_closed. Receipt derivation, idempotency hashes, pending watermark, semantic mutation and Host terminal gates are unchanged. Tests also require real before-terminal-commit crash/cold reuse and original USER text absence on the next request.

Fixed108e5428/productc6af1ac4, r2: **2 PASS / 7.75s**, consisting only of the original False red and the new True crash control. False preserved the real unclosed-scope flow; True interrupted Host terminal.before_commit after pending persisted, then rebuilt the runtime/SDK stack. Both retained the same actual SDK failed terminal, pending identity and dirty debt without retransmission, refused promotion to COMPLETED, completed a new independent input without original USER text, and constructed zero source observation bodies after revocation. True returned pending again during recovery, never falsely already_closed.

PG4841 exited0, elapsed8.82s, peak389680KiB, minDisk4726MiB, remaining=[] and cleanup_error=null. Default shared lock released. No old page/analysis/SDK identity suite was repeated. Original r1 failure remains.

The production change is only the non-success observation ordering and accurate pending replay status. Main already composes the real fallback; this is not a new terminal authority or semantic completion path. Pending receipt and Host terminal still commit in separate existing transactions; the crash control proves idempotent reuse, not atomicity across them. H077/M616 installed-target/Host-source/HTTP MockTransport evidence only; main H078/M618 composition and native remain separate. Dirac final source/results review requested.

Raw baseline evidence (ignored, local):
- `.local-test-evidence/2026-09-06/revoked-scope-terminal/r1/command.log` SHA256 `93338db4b3b9b78917de89312ee0aa980aba13f77c132f1a398c7c72560eda4b`
- `.local-test-evidence/2026-09-06/revoked-scope-terminal/r1/resource.json` SHA256 `0f0c38a6b46c89b1ee6fdabf28fc2647c9ed6e1adff73af8b164d98c32ed800f`

Raw fixed evidence (ignored, local):
- `.local-test-evidence/2026-09-06/revoked-scope-terminal/r2/command.log` SHA256 `fe0213b56f8a22a2a3250ab99aa406b3c3b24281fe6dd26f4fc9f90dcb2cfa60`
- `.local-test-evidence/2026-09-06/revoked-scope-terminal/r2/resource.json` SHA256 `c9e49f104394f72d3134de3488717ce9545c7efb96e3c05d31e8a35b2519574c`

Exact minimal rerun (only if changed; use fresh evidence/basetemp paths):

```sh
.local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --evidence-dir .local-test-evidence/2026-09-06/revoked-scope-terminal/<new-run> -- .local-test-evidence/2026-09-06/typed-use-primary/venv074614/bin/python -I -B .local-test-evidence/2026-09-06/revoked-scope-terminal/run_tests.py .local-test-evidence/2026-09-06/revoked-scope-terminal/<new-temp>
```

Dirac final scoped source/results ACCEPT for fixed53940598 (productc6af1ac4); merged into the primary candidate. Current H078/M618 composition and native remain separate from this leaf.
