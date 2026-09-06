# Atomic USER evidence and foreground admission

Last updated: 2026-09-05. Independently reviewed, scoped source ACCEPT.
This prerequisite does not complete duplicate-source suppression.

The previous service committed S1 evidence, then opened a different transaction
to enqueue it. A crash or forget between those writes could give an old source a
later queue sequence. Queue time alone could therefore not prove a new assertion.

The production service now supplies its exact evidence/receipt pair to the queue
store. Under one BEGIN IMMEDIATE and existing ingress fence, the store validates
bindings, checks whether S1 already exists, appends S1, assigns the immutable queue
sequence and inserts the turn. Only a real first insertion in this transaction
adds schema_version2/source_admission=atomic-evidence-and-turn/v1 to turn_json.
Caller text cannot supply that marker. The turn hash binds this request/marker;
the sequence remains an immutable row fact, not a field claimed to be in that hash.

An already durable S1 source remains version1 even when queued through the new
API. Existing turns replay their original format/hash/receipt; retries never
upgrade legacy facts. A supplied pair is fully verified even on existing-turn
replay. Existing read-model and runtime preparation paths accept the new shape.
There is no new table, migration, flag or Provider call.

Oracle before repair:2FAIL1PASS/0.99s. After implementation, three fault boundaries
(after S1 insertion, before commit, after commit), queue regressions and actual
SDK terminal → public history → reopen integration:32PASS3deselect/8.81s.
Four mismatched pair bindings plus receipt forgery and original controls:
9PASS/2.15s. These totals overlap and are not added. Tightening the receipt-error
oracle first failed on the expected error spelling; corrected to the existing
sanitization_receipt_verification_failed code,1PASS8deselect/0.40s. Test-only ruff
passes. Independent review checked all three changed files without rerunning.

Commands use dedicated candidate Python `-m pytest` over:
`backend/tests/memory/test_primary_atomic_source_admission.py`,
`backend/tests/execution/test_foreground_queue.py`, and
`backend/tests/memory/test_primary_runtime_api_integration.py -k 'not memory_only_forget'`.
The receipt tightening rerun used `-k existing_turn` on the new atomic test file.

Local ignored evidence under `.local-test-evidence/2026-09-05/primary-candidate/`:

- atomic-source-admission-red.log: da93a3cee4b03ac35b1d900bf6dce5df33a0b337ca35a6437f41f0959b7bd923
- atomic-source-admission-combined.log: 577069b23ef22ed8d692a684c13cb30df6d236d227dfaa15db3dbea3aaf0e6f5
- atomic-source-negative.log: e5628cfaa9cfe04f7bd342bd660218b2644686f345af729c691969fd13427720
- atomic-source-receipt-oracle.log: 56993c76eed4118cb1d75f78c75b87cccbe0db1ad402c8999499cf6735ffa369
- atomic-source-receipt-exact.log: 372c6f518775fb3912214caa10577c43b103a94c4290ae13b81606610eca5950

Remaining gates: verified namespace/origin/cut public integration, all-revision
duplicate suppression, typed/short/mutation and final physical-request checks,
successor artifact verification and real native retest. The two duplicate-source
counterexamples remain red; old native v1 forget actions have no proven cutoff.
