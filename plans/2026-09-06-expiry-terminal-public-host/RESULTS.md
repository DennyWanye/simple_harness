# Host H077 public terminal recovery — results 2026-09-06

Fixed product `ebb81b6f7183e6e6d0c2ae6714dcc1f0367e1038`, base762af1ab. **3 unique new controls PASS**, consisting of one real cold-stack recovery plus two error-dispatch negatives. No lease10/analysis14 repeat, no model/native, no original userdata recovery.

| Batch | Result | Resource cleanup | Raw log SHA256 |
|---|---|---|---|
| r1 | 2 PASS / 1 FAIL in3.58s | PG73051 exit1; remaining[]; peak256176KiB | `ee59caaf2e80ee48bb017ed35923c78fe78a337cb500bc24721e13bc70226124` |
| r2 | 1 PASS / 2 deselected in3.47s; only cold failure rerun | PG73465 exit0; remaining[]; peak214656KiB | `517aa105145ec9aaadcb76d1829987b15d8e5769c340cc5d9536800e387d5272` |

Raw root: `.local-test-evidence/2026-09-06/expiry-terminal-public-host` (ignored). Each batch retains command.log/resource.json and its independent basetemp.

## Actual cold path

An isolated child used preserved installed H075 (exact version and loaded path checked) with real Host primary admission, public authorization challenge/response, and a shortened test deadline. SDK genuinely reached expired/failed without terminal proof; the real Host queue then accepted Stop. The child fully closed its Host Runtime and SDK stack. No decision/terminal rows were forged or removed.

The parent created a genuinely new Host/SDK077 stack over those same test stores with exact installed wheel/version/directURL verification. It reclaimed the expired Host lease, retained the same Host Run and SDK Run, used SDK public eligibility/recovery/terminal APIs, and committed a real Host FAILED terminal at generation+1. A second new stack returned identical terminal evidence and no pending work. There were zero additional Provider calls and no old Context re-preparation. Persisted Host identity was compared against SDK public terminal; existing helper also rejects altered event ID/hash.

Initial cold failure occurred **after** SDK recovery and Host durable terminal commit: the new process lacked the old tool registration, so cleanup raised KeyError. The fix only skips an exact missing registry lookup. The existing-record terminal cleanup remains outside the catch; no old grant/registration is synthesized, no SDK artifact changes were needed.

## Evidence limits

- Two negative controls test dispatch of ambiguous/source-invalid public errors without requesting recovery. They do not prove full foreign/stale Host production binding scenarios.
- The pre-recovery Host ownership read and SDK recovery transaction are not atomic across databases. If Host ownership changes between them, SDK may still append a valid historical terminal; the existing Host settlement transaction independently refuses a stale owner/generation. No stronger cross-store guarantee is claimed.
- These tests used the existing tiny Python/generic dependencies plus explicit installed077 and M616 targets. Candidate verification used the actual077 wheel identity. This is not a new full environment or an M617/main/native combination.
- Original r6 userdata has not been restored. Its separate SDK copy evidence and077 installed gate remain in the SDK artifact report; no merging/recovery/native action was performed by this leaf.
- Closure/background guard investigation remains paused; graph root cause remains unproved and no graph source was changed.

## Re-run only when changed

Run `backend/tests/execution/test_expiry_terminal_public_recovery.py` via the default shared resource runner. Supply `H077_IDENTITY_JSON`, `H077_LEGACY_075_TARGET`, and `H077_MEMORY_TARGET`; the ignored run_tests.py preserves the exact executed loading/command. Do not reuse this source-overlay result as production pin verification.

## Merge scope

- `backend/deskpet/sdk_adapters/composition.py`: public exact metadata, explicit SDK recovery.
- `backend/deskpet/execution/foreground_runtime.py`: narrowly dispatched bound recovery; existing terminal settlement fence retained.
- `backend/deskpet/execution/foreground_runtime_ports.py`: absent process-local registration cleanup.
- Two new test files and this contract/results plus architecture facts.
- SDK077 frozen sourcec29af669, wheel60f7fb16 unchanged; Host main/pins were not changed here.
