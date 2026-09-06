# Current-use six-cell delivery

2026-09-06. Independent `feat/typed-recall-context-use-full`, base `fbebdaff`.
Executor/oracle source `96ae15e2fbd639505cd283f40d83b04ef67d2d82` received Dirac's
read-only pre-test scoped ACCEPT. Test-only follow-up `24799e99` updates the exact
transport input schema; runtime/oracle bytes are unchanged. This leaf is not merged
into the main combination. Original dirty runner WIP and Carver source10 are untouched.

## Actual result

Formal Run `80e32957e5174b0fa8f752eddae06e31`: **6 OBSERVED; 4 PASS /0 FAIL /2 BLOCKED**.
The other **395 cells were not selected**, including all source10. The complete401
inventory remains in the report (4 PASS/397 BLOCKED), but that is not401 execution.
Do not combine this run with historical182/219 or Carver's separate source evidence
and call the mixture a new full401 result. No self-check, model, native or Provider call.

| Original cell | Formal verdict | Actual decisive evidence |
|---|---|---|
| current-use/authority:suppression | PASS | Two actual heads recalled; public epoch3→4, unchanged policy; stale old use rejects; unaffected item remains. |
| current-use/context:suppression-first | PASS | Suppression commits before first use; exact RECALL_AUTHORITY_STALE exception returns no receipt/payload. |
| current-use/context:new-provider-attempt | PASS | Original receipt rejects attempt-only change; actual fresh authorization for attempt-2 yields its own receipt; after suppression a third fresh attempt rejects. |
| current-use/context:wrong-snapshot | PASS | Invalid manifest rejects in DTO; valid reversed two-fragment manifest with same attempt/time rejects at actual Memory use with exact RECALL_CONTEXT_USE_IDEMPOTENCY_CONFLICT. |
| current-use/context:receipt-first | BLOCKED | Actual grant precedes suppression and replays exactly after reopen; new attempt rejects. Harness reservation/exact-once consumption witness remains absent. |
| current-use/context:duplicate-same-provider-attempt | BLOCKED | Identical public request replays before and after suppression/reopen. A replayed receipt is not proof of no second physical consumption. |

All six reached complete through public owner registration, two real S1 admissions,
CREATE plans, receipt views, ordinary typed recall, two whole-item pages, two public
Context fragments, use/suppression, close/reopen, fresh recall and historical replay.
The independent oracle reconstructs domain hashes from the public canonical wires,
verifies exact source payloads and bindings, the two-item manifest, frozen clocks,
epoch increment and one unaffected item. Receipt/hash/attempt-replay positive checks
apply only to the four scenarios that first authorize; the two suppression-first
scenarios instead prove absence of an authorized receipt. No expected SDK result was
generated from a product result. See [CONTRACT](CONTRACT.md) for the pre-execution oracle.

Both remaining cells use the precise blocker
`CURRENT_USE_HARNESS_RESERVATION_EXACT_ONCE_WITNESS_UNVERIFIED` (reported as ORACLE_GAP,
with narrower classification `harness_consumer_witness_unverified`). The independent
new-continuation cell remains unimplemented and unselected; it was not exchanged for
authority:suppression. Original S3 §5.4 places that binding at Harness reservation,
not the Memory DTO. H073 public-port investigation and Dirac's exact-wheel review
did not find built-in receipt→reservation consumption. Adapter-local counters or
an unknown-DTO-field rejection would not close it. No new Memory defect was found
in this batch. S3/program/full401 remain incomplete.

## Tests and resource facts

- First batch, source96: **4 passed /10 failed, 2.22s**. The new actual six-scenario
  executor plus21 oracle tamper controls is **one test**, not21 extra product cells.
  Three canonical/domain/original-obligation neighbors passed. All10 failures shared
  the transport test's stale exact input-key list, which omitted the new context_use
  construction inputs. This original failure log is retained.
- Only that test expectation changed: exact top-level fields plus exact context_use
  fields and two seed shapes; no broad acceptance of arbitrary fields. Retry at247:
  **10 passed, 0.22s**. This is a separate transport regression result, not a product score.
- Formal six-cell command at247, no observe mode: **4/0/2** above. Pins match exactly;
  no source adapter/overlay. Borrowed owner installed consumer runs isolated `-I` and
  validates **H164/M72 package files against wheel bytes**. This is not a new install
  belonging to this tree. H073/M0613 fixtures and acceptance files are unchanged.

All commands use main `scripts/run_resource_bounded.py` at145baed3, its default shared
OS lock, 2048MiB/180s. No lock override or concurrency. Each group has
`remaining_group_members=[]`, `stop_reason=null`, `cleanup_error=null`.

| Invocation | PGID | Exit | Elapsed | Peak KiB |
|---|---:|---:|---:|---:|
| tests-r1 | 31787 | 1 | 2.375s | 83392 |
| tests-r2-transport | 31892 | 0 | 0.452s | 44240 |
| formal6 | 31945 | 3 | 1.529s | 138544 |

Exit3 is the honest remaining/unselected-cell status, not an execution crash.
All test processes exited and the slot was explicitly released to main. No builds,
new venv, frozen artifact updates, other-tree writes or evidence deletion.

## Reproduction

Use a fresh evidence directory each time and obtain the coordinator's test slot.
The interpreter and wheels below already exist; do not recreate them or add PYTHONPATH.

```sh
consumer=/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/venv/bin/python
bounded=/Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py
base=testcase/human-memory-program
evidence=.local-test-evidence/2026-09-06/context-use-full/reproduce

"$consumer" -B "$bounded" --evidence-dir "$evidence-tests" --rss-mib 2048 --seconds 180 -- /usr/bin/env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$consumer" -B -m pytest \
  "$base/tests/test_typed_recall_context_use_bridge.py" \
  "$base/tests/test_typed_recall_a2_oracle.py::test_canonical_source_known_answer_and_domain_distinction" \
  "$base/tests/test_typed_recall_a2_oracle.py::test_frozen_full_public_vectors_use_json_domain_and_state_keeps_nul" \
  "$base/tests/test_typed_recall_a2_oracle.py::test_revision_preserves_all_original_scenarios_thresholds_and_negatives" \
  "$base/tests/test_typed_recall_bridge.py::test_two_layer_dispatch_retains_exact_inventory_and_failures" -q -p no:cacheprovider

"$consumer" -B "$bounded" --evidence-dir "$evidence-resource" --rss-mib 2048 --seconds 180 -- /usr/bin/env -u PYTHONPATH "$consumer" -B "$base/runners/run_typed_recall_public_consumer.py" \
  --consumer-python "$consumer" --artifact-dir "$evidence-public" \
  --harness-wheel /Users/denny/projects/simple-harness-sdk-operation-audit/.local-test-evidence/2026-09-06/run-operation-audit-073/build1/simple_harness_sdk-0.7.3-py3-none-any.whl \
  --harness-wheel-sha256 1a9ed5c95e6cddd4e0ccd85124320a6001008740a53213712fc89f3467cb4cd7 \
  --harness-source-commit 0282fa982995b24bc893fdf6bed69d2caacd6587 \
  --memory-wheel /Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/build1/simple_harness_memory_sdk-0.6.13-py3-none-any.whl \
  --memory-wheel-sha256 33fcc494f0cb8c9f358e411b372d4dcdc423725563dda6f5b96684f5fa1ffd62 \
  --memory-source-commit f2a6a706c5e3407e896ada3bd9e735cd9c0b77fd \
  --cell current-use/authority:suppression --cell current-use/context:suppression-first \
  --cell current-use/context:receipt-first --cell current-use/context:duplicate-same-provider-attempt \
  --cell current-use/context:new-provider-attempt --cell current-use/context:wrong-snapshot --child-timeout 120
```

## Local evidence index

All paths below are relative to this tree's ignored
`.local-test-evidence/2026-09-06/context-use-full/`; raw files are not committed.

| File | SHA256 |
|---|---|
| tests-r1/command.log | 41425010d8014bad662f3d512b6de595344586b92f1ad5c68167b3168f6ab423 |
| tests-r1/resource.json | 967425699831b9891b79d37b7309c91650c151a3548f2d1a53458f50b7ee20c6 |
| tests-r2-transport/command.log | e173149ae4a7d07bf2a0a59558af27878c827af0bd7e82bda6c485ccd7a63e9f |
| tests-r2-transport/resource.json | f50429a0024222f7c43893f071b58f40794ad621f758f1a65584e99a7fb75160 |
| formal6-resource/command.log | 54cad2c52510f3c087a0dab2eed49385a023fd0fe25a2dcaf10e4a9312ffb21e |
| formal6-resource/resource.json | e04a54107bb4910720518894492288f2896e3a7482c5496179d74951487316f2 |
| formal6/bridge-summary.json | 9ec122660099ceba1b09c03e7e8b615d884f7c5a6c82efbb41017cfd7c294161 |
| formal6/public-observations.json | da0bae5447a3061364ec98a2501e846f2005f198bb8bb8b3f8b96d936dc1a6c1 |
| formal6/public-request.json | 52b076f727c71f360dc39f464d2703834a06854877b1d68464f3184c64c44108 |
| formal6/public-runtime.json | 708b103fc5c648ebb82a188e4c2399180ef66a3d155b6da3312c53c5b3765339 |

Integration note: retain both new context-use and Carver source-oracle entries in
the bridge execution-code hash manifest when combining. The A2 dispatch changes
are disjoint (context-use vs assess_source). No frozen fixture/pin merge is needed.
