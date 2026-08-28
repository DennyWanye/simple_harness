# Host-control SDK 0.6.2 offline downgrade

This is a destructive recovery procedure, not a normal application startup path. It coordinates the ProductState database and the SDK execution database so a verifier Run cannot be recovered as an ordinary ReAct Run by an older Host.

## Preconditions

1. While the current Host is still running, close external ingress and enumerate every ProductState verification attempt. Query its exact `expected_run_id` through the current SDK, durably cancel or fail every nonterminal Run, wait for terminal settlement, and prove it is absent from SDK recovery enumeration.
2. Export `host-control-run-dispositions-v1` JSON containing the exact Run/session/request/turn identities plus `terminal=true` and `recoverable=false`. Do not manufacture this file from ProductState alone.
3. Stop the SDK runtime through its lifecycle owner, then stop the whole application. Confirm no backend or desktop process remains.
4. Provide an isolated Python environment containing the exact pinned `simple-harness-sdk==0.6.2`. The gate checks the installed distribution version itself.
5. Select the verified content-addressed ProductState pre-v3 backup and its matching sidecar. Never select an incomplete generation.

Example disposition file:

```json
{"schema":"host-control-run-dispositions-v1","runs":[{"run_id":"...","session_id":"...","request_id":"...","turn_id":"...","state":"completed","terminal":true,"recoverable":false}]}
```

Run from the repository root:

```bash
python scripts/host_control_downgrade.py --product-database /absolute/userdata/product-state.sqlite3 --execution-database /absolute/userdata/sdk-execution.sqlite3 --product-pre-v3-backup /absolute/userdata/product-state.sqlite3.pre-v3.<sha256>.sqlite3 --run-dispositions /absolute/evidence/run-dispositions.json --sdk-062-python /absolute/sdk-0.6.2-venv/bin/python --confirm-ingress-closed --confirm-app-stopped
```

The gate takes content-addressed backups of both live databases, validates Product v3 and the exact pre-v3 v2 candidate, verifies every attempt-to-Run identity, and asks the isolated 0.6.2 process to open the execution backup twice with zero recoverable Runs. Any missing, ambiguous, mismatched, nonterminal, recoverable, locked, corrupt, unsupported, or version-mismatched state blocks before Product restoration.

If and only if preserving the current execution database is impossible because 0.6.2 cannot reopen it, a recovery operator may explicitly add `--allow-execution-quarantine`. This moves the entire stopped execution database to an immutable quarantine artifact before restoring Product v2. It never deletes selected rows and it does not create a fresh database; starting 0.6.2 with a fresh execution database is a separate, explicit acceptance decision. Keep every generated sidecar and artifact until recovery is closed.
