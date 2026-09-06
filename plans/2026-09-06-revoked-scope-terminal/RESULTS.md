# Revoked unclosed scope: baseline and pending verification

Updated 2026-09-06. Existing H077/M616 installed targets + Host source. No SDK, main, schema or original userdata change. Branch feat/primary-revoked-scope-terminal, base75cd51a5.

## Baseline, before product change

fffcdadc composed the actual ClosureFallback class used by main into the real runtime constructor. No pre-closing task_scope_update was added. r1 False: overall **1 FAIL / 1 deselected, 4.09s**; only final source-body-observation assertion failed.

Earlier assertions completed: actual public write/page/revoke caused SDK FAILED and zero additional physical send; the existing fallback wrote pending without Provider/mutation use; scope stayed dirty at closing watermark0/canonical revision1; Host committed FAILED/SETTLED; cold stack reused exact pending and SDK terminal without retransmission; caller could not promote it to COMPLETED; an independent next input completed. This corrects the earlier extrapolation: current-r3 omitted the production fallback and does not establish main terminal deadlock.

The remaining baseline failure observed one unnecessary source-bearing _scope_observation_tx construction after revocation. No outbound disclosure was observed. This is not a claim of exfiltration or main liveness failure. PG4439 exited1/remaining[]/cleanup_error=null, slot released.

## Fixed source, not yet tested

Productc6af1ac4 only avoids model observation construction for non-COMPLETED settlement and reports an existing pending debt as pending instead of already_closed. Receipt derivation, idempotency hashes, pending watermark, semantic mutation and Host terminal gates are unchanged. Tests also require real before-terminal-commit crash/cold reuse and original USER text absence on the next request.

Original False red and one new True crash control await the shared test slot; main PG4516 owns the actual Procedure batch. No lock retry or parallel tests. Source review pending; this leaf is not yet complete.

Raw baseline evidence (ignored, local):
- `.local-test-evidence/2026-09-06/revoked-scope-terminal/r1/command.log` SHA256 `93338db4b3b9b78917de89312ee0aa980aba13f77c132f1a398c7c72560eda4b`
- `.local-test-evidence/2026-09-06/revoked-scope-terminal/r1/resource.json` SHA256 `0f0c38a6b46c89b1ee6fdabf28fc2647c9ed6e1adff73af8b164d98c32ed800f`
