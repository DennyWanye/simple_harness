# Native Workflow Final Audit Addendum

Date: 2026-07-11

## Final implementation evidence

- Workflow regression: `295 passed`.
- Frontend: `tsc -b` passed; focused workflow, trace, session, and PPT outline tests: `48 passed`.
- Terminal checkpoint, run status, business events, and delivery rows commit in one SQLite transaction.
- Conditional routes are checkpointed before frontier promotion and reused after restart.
- Pending and due-failed deliveries are redelivered by a wakeup/deadline dispatcher with a 30-second safety interval.
- Native node spans include engine metadata and reconcile `succeeded_pending` crash survivors to `OK`.
- Human decisions are materialized as durable `workflow.decision` events in the interrupt transaction.

## Windows Computer Use addendum

The existing completed PPT run still restored one `12/12` progress card and one PPTX attachment after restart. A second live run was driven to `6/12 waiting`; after restarting DeskPet, its previously open decision was recovered from the durable outbox as an actionable PPT outline card with Confirm, Modify, and Cancel controls. The Confirm button was clicked through Windows Computer Use. That development run was then correctly blocked by implementation-hash protection because the workflow definition had changed during the test; this is expected fail-closed behavior and did not affect the already completed end-to-end run.

## Fault injection coverage

- Task result committed before return: handler result reused.
- Conditional route committed before frontier: selector not called again.
- Terminal frontier committed before return: run/event/delivery remain complete and idempotent.
- Launch preflight failure: failed run and final outbox commit atomically.
- Node result committed before span finish: trace reconciler closes the surviving running span as `OK`.
