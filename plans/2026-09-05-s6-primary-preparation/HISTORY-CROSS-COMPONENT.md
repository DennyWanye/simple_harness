# Primary runtime / public history / memory-only forgetting

2026-09-05. Fixed runtime/API base `888efe0c`; isolated installed Memory0.6.6 wheel
`381d85437537ae1f58f04b84e8332b0774d1e3aff2c6529b3824126071135361`, Harness0.7.2.
No source overlay, external Provider, native UI, main pin change or program gate claim.

The existing two runtime-to-public-API cases now use the real public Memory manager.
A new ordinary terminal has complete source proof. The deliberately old scoped observer
retains its original USER and hides the generated group lacking proof; archives are not
rewritten. This legacy observer test uses a deterministic Provider directly and does not
prove initial-scoped admission through the production Provider guard, which remains P1.

The third case runs two ordinary turns through ProductProviderAdapter and real SDK/Host
SQLite, with a deterministic HTTP MockTransport and the current dependency guard. The
second actual outbound contains the first source. It ingests the original Host S1 pair,
materializes one memory via the public mutation API, and suppresses only that memory ID.
The public page then retains the independent USER, hides the original USER and both
dependent assistant responses, and denies all three saved detail refs. Host page revision
and original evidence-envelope bytes stay unchanged. After closing/reopening Host/SDK/
Memory, no completed invocation replays; the same read filtering holds. The next actual
adapter request includes the independent and current USER, with none of the three hidden
texts. This proves cross-component behavior for this source chain, not model recall quality.

Command from this tree:

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-history/venv066/bin/python -m pytest backend/tests/memory/test_primary_runtime_api_integration.py -q -p no:cacheprovider
```

**3 passed in 2.51s**. Raw ignored log
`.local-test-evidence/2026-09-05/primary-history/runtime-api-memory-combined.log`, SHA-256
`657b1e3ad1e0cd9c3ab032ed29b580e716a78fc082f3d3517dffc07f7ce070a6`.
Two preparation failures were fixture composition mistakes, not product reds: the reused
mutation helper hardcoded a different actor, then the test manager lacked Host evidence
authority. The helper now uses its supplied principal; the test supplies real
HostEvidenceAuthority, including after reopen. Both earlier logs remain ignored.

Initial-scoped/ResumePackage source completeness, independent short-horizon integration,
exact current native SDK candidate, approval UI and full preference correction/forgetting
remain separate acceptance obligations. Independent review of this cross-component test
is pending; the runtime candidate itself is not a complete production replacement.
