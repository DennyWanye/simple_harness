# TaskGraph production acceptance (in progress)

These cases run on the product deployment (`agent_orchestrator.testing.product_world`,
via `production_fixture.enabled_world`): the TaskGraph is bound when the Mission is
created, the Assurance lane and the native pools are the product's, and the deployment
duties sign the completion mapping and planning grants in auto mode. The seed plan is
committed by real loop rounds (proposal, independent method review, adoption); only the
model replies are scripted (`LayeredScriptedProvider`). There is no fabricated grant,
APPLIED record or deployment acceptance.

Run with a frozen installed TaskGraph wheel whose package-owned deployment
manifest verifies. Do not prepend the working `src` directory to `PYTHONPATH`,
replace the deployment reader, or mark missing deployment evidence as a skip.
Use a new ignored `.local-test-evidence/<date>/<run>/` directory for each run's
pytest `--basetemp`, logs and JUnit; pytest may delete an existing basetemp.

Current coverage is only the named subcases below, not all 42 plan scenarios.

| File | Intended assertions | Scope still missing |
|---|---|---|
| `test_production_seed.py` | Binding in the creation transaction, original planning dispatch/Commit, exact history, repeatable read without events or Attempts | Full A01 authorization negative matrix, C01 fault points and replay conflicts |
| `test_read_history_protocol.py` | Historical view cannot execute, offline projection, original APPLIED/receipt, tenant refusal, creation-time enable receipt replay, epoch/notice rollback | Two changed revisions and restart; corrupted sources; complete A04 validity producer/dispatch path |

| `test_commit_atomicity.py` | Original APPLIED/record/followup write aborts, exact committed reply replay and changed-byte refusal | Concurrent competing Commit and full C01/C02 matrix |
| `test_process_recovery.py`, `crash_seed.py` | True process exit inside/after plan Commit, before Attempt intent insert, after physical Worker turn; trusted startup assembly and original usage import | Full service/action recovery and UNKNOWN resolution are separate |

The journal records executed results and hashes. Adding a test here does not mark
its scenario PASS. The remaining S/D/A/R/C/V, mutation/stateful, real-model and
native-UI gates stay open.
