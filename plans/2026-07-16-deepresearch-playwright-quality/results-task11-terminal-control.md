# Task 11 — terminal projection / versioned actions / control integration

Status: **PASS for this backend sub-slice** (2026-07-16, Win11-only plan scope).

## Delivered

- Added an injected `TerminalProjectionRegistry` keyed by
  `(workflow_name, workflow_version, capability)`.
- Moved the complete v4 terminal validator out of `native.py`; the v4 public
  payload shape remains unchanged.
- Added strict v5 delivery, quality, coverage, and action projection. Unknown
  versions/capabilities fail closed, and engine `status` remains separate from
  `delivery_status`.
- Added the immutable v4/v5 action matrix and generic `workflow_run_action`
  IPC route while retaining the legacy v4 retry route.
- Added service/launcher integration for `generate_now`, `cancel_settle`,
  `continue_research`, and versioned retry. Generate-now uses the repository's
  atomic run-version/head/brief checkpoint CAS. Continue uses the authoritative
  `workflow.final.delivery_status` and repository snapshot/pin validation.
- Added `DurableResearchControlPort` projection and thin latest-head CAS
  delegates for `accepted -> observed -> settled -> consumed`, including
  durable cancel-settle marker recovery.
- Enforced v5 terminal delivery cardinality and suppressed report/artifact
  intents for `insufficient_evidence`; the single engine-owned
  `workflow.final` includes both engine status and validated delivery status.

## Verification

```text
python -m pytest \
  backend/tests/test_workflow_terminal_projection.py \
  backend/tests/test_workflow_delivery_pipeline.py \
  backend/tests/test_workflow_deep_research_v5_loop.py \
  backend/tests/test_workflow_service.py \
  backend/tests/test_workflow_ipc.py \
  backend/tests/test_workflow_research_repository.py \
  backend/tests/test_workflow_native_engine.py \
  backend/tests/test_workflow_product_delivery.py \
  backend/tests/test_workflow_launcher.py -q

90 passed in 9.75s
```

`git diff --check` passed for the owned files. Ruff is not installed in the
backend virtual environment, so no Ruff result is claimed.

## Remaining integration owned by later/parallel slices

- Inject `DurableResearchControlPort` from the production v5 context factory
  in `main.py` (Task 14; outside this slice's ownership).
- Let the v5 node/control integration invoke `settle()` and `consume()` at the
  final durable checkpoints (parallel graph slice).
- Wire the generic action IPC messages and pending/accepted/error states into
  the frontend (Task 13).
- Exercise these actions through the real desktop/WebSocket/product delivery
  stack during Gate F.

No Hyper-V, VM, ISO, reboot, global browser cache, or non-Win11 dependency was
introduced.
