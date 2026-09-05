# Next primary candidate environment

2026-09-05. Isolated branch feat/human-memory-primary-candidate from6f634048.
This prepares a subsequent combined runtime; it does not replace the main checkout
or the previous P18120 native environment, and it has not been launched natively.

Memory0.6.7 source fa6badd086d089e3e1752df45993ce0ccba98377 is vendored unchanged,
SHA-256 7dd224c29923ab1346a78bb8529d9426559bb1e3a38b8c0964f57cc8687e9c3d.
Its separate SDK source/wheel/installed consumer already passed independent review.
Harness0.7.2 and Service0.3.12 retain their exact existing artifacts. sdk_candidate.py
and pyproject.toml point to this Memory candidate; old vendored wheels/manifests remain.

The tracked uv.lock was stale against pre-existing pyproject requirements: it still
selected Memory0.5.2, Harness0.7.1 and transformers4.57.6. uv lock regenerated it with
Python3.12.13 (416 packages). Initial offline resolution lacked required cached registry
metadata; one online metadata resolution succeeded in5.48s. sentence-transformers was
then resolved to the already exercised5.7.0, avoiding an incidental6.0.1 upgrade.
A final offline uv lock --check passes. The11 changed/added package names were checked
explicitly: both SDK changes plus click/hf-xet/huggingface-hub/qwen-vl-utils/
sentence-transformers/shellingham/tokenizers/transformers/typer. All nine non-SDK
versions equal the main environment used by earlier native tests. This command did
not sync or alter the main Python environment, download model weights, or claim all
416 packages were installed and tested on every supported platform.

An own ignored venv installs all three exact wheels from THIS checkout's backend/vendor;
other dependencies use the main site-packages via .pth. Production origin/hash checks
therefore refer to the actual candidate wheel paths. Focused verification:

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/sdk_adapters/test_sdk_candidate.py backend/tests/test_provider_runtime_refresh.py -q -p no:cacheprovider
uv lock --check --offline --python /Users/denny/projects/simple_harness/backend/.venv/bin/python --project backend
```

**21 passed in2.73s**, one existing event-loop deprecation warning; offline lock check
passed. Full history/ResumePackage, short producer and authorization UI candidates must
still be combined and verified before the next native run. No program/machine gate PASS.
Raw files stay ignored under `.local-test-evidence/2026-09-05/primary-candidate/`:

| File | SHA-256 |
| --- | --- |
| candidate-composition.log | 23f6435b8137672fef7ba3609555ef7746cdc4f3f48eb44bd450e74b0ac80f44 |
| lock-check.log | 0c669d29a4335b09a390175701b75c13d29b6c452bcd323267ee264cbac1ca13 |
| dependency-delta.json | afb1a2c7c3a029946b5463d99e1a1c54334df5ed472eb60de1942f9f3fbf533d |

## First fixed history integration

Merged fixed history/helper7dcfce8b into dependency candidate1f9c008d. Production source
merged automatically; conflicts were limited to retaining both sets of architecture
facts and the newer shared runtime fixtures/guarded CREATE_NEW test parameters. No
agent's uncommitted source work was included. Memory0.6.7 remains installed from this
checkout's vendor. Runtime→public API memory-only forgetting/reopen/outbound, v2 helper,
actual production composition, late Provider denial/unknown classification and actual
CREATE_NEW binding/effect tests: **42 passed in24.61s**, one existing deprecation warning.

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/memory/test_primary_runtime_api_integration.py backend/tests/memory/test_primary_short_visibility.py backend/tests/test_provider_runtime_refresh.py backend/tests/execution/test_primary_history_outbound.py backend/tests/execution/test_primary_create_new_runtime.py -q -p no:cacheprovider
```

Raw history-combined.log SHA-256 `1aab8744f752fbcbbdc1e593e5ce013d583b2a78ce4b437cff76206e652f3e7b`. The old unproved ResumePackage
currently rejects use; initial-scoped functionality remains an open P1. Source-bound
resume restoration, real authorization UI and short production registration are still
separate in-progress branches.42green does not close these obligations or permit a
whole-product completion claim; native app has not been started at this combination.
