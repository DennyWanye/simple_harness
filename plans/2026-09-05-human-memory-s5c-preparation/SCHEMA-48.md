# S5c schema successor: Primary47 -> isolated48

2026-09-05. Mapping ID `A11-schema48/v1`. Local preparation, not S5c runtime completion.

The approved original A7/A8/A11 and all program acceptance requirements remain.
The coordinator allocated delivered Primary exact source identities to global47.
The unpublished S5c prospective47 allocation therefore becomes48; this changes
only the schema sequence and its explicit initializer/test expectations.

| Historical S5c allocation | Successor | Required evidence |
|---|---|---|
| Production base46 | Actual Primary source-index47 | Exact normal46->47 receipts/DDL retained |
| Explicit46->S5c47 | Explicit47->S5c48 | Existing primary data/chain unchanged, one extra S5c receipt |
| s5c/039_prospective_memory_actions_v47.sql | s5c/040_prospective_memory_actions_v48.sql | New exact file/checksum; old hash is not reused |
| reopen47 validates S5c | reopen48 validates full Primary47 chain and S5c48 | Real DDL/registry/fences, old receipts preserved |
| Ordinary initializer rejects unpublished S5c47 | Ordinary Primary47 initializer rejects isolated S5c48 | No main.py/startup activation until S5c complete |
| Historical old-S5c47 data | Rejected as incompatible, unchanged | Never confused with actual Primary47 or stamped as migrated |

Historical documents and test outcomes below retain their original47 statements.
This mapping supersedes their schema numbering only; it does not rewrite the original
A11 text, stable IDs, thresholds, old results or active machine gate. No global skip
from46 straight to48: the ordinary initializer first installs/validates Primary47.

All four S5c tables, registration/consumer, canonical action domains and occurrence
semantics remain as the original6c8ebddd/e2702006. Scheduler/presentation/ack/settle,
actual USER immediate/forget integration and production restart acceptance are still
unfinished. The current primary-memory forget action uses its own existing S1
callback; this preparatory journal does not replace it or duplicate the live API.

## Validation

Source on base54aa2f88. Installed exact Memory0.6.7/Harness0.7.2 from coordinator
venv; no SDK source overlay. No native, model or production startup.

```sh
PYTHONPATH=backend /Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest backend/tests/memory/test_s5c_store.py backend/tests/memory/test_s5c_consumer.py backend/tests/memory/test_s5c_consumer_sdk.py -q -p no:cacheprovider
```

43 passed12.15s. Covers actual47 base/48 reopen/rollback/active-writer rejection,
canonical raw preservation, store gates and public SDK registration receipt replay.
Separately built an actual old unpublished47 database with the clean e2702006
checkout's initializer, then tried this successor: stable rejection and identical
whole database SHA256; no fake pragma-only legacy fixture. Both logs are local.

Original A11 decision source SHA256 `d7ca07d8cec362422485da0aa15dfe380c927e291da1566d0ef480d80bbdb8e3`.
Original S5c47 SQL SHA256 `b8d8036c070d0ccf81ac63628c6eb1b8d7953f925508e870fe4efc7e46de6268`; successor48 SQL `9f4b760f144655345d8b376baf8ae371d6a61354c8fc5753922f057b92e5ae48`.
Local `.local-test-evidence/2026-09-05/s5c-schema48/combined-first.log` SHA256 `70d77abe2db0b91f8ece3f55642c25f18138551de54120bfba998dadcbc61f93`.
Local `.local-test-evidence/2026-09-05/s5c-schema48/old-s5c47-rejection.log` SHA256 `cacacbe33c32f69b7af2142cf43f613ab388b993cd9766576145087d906bf0e8`.
