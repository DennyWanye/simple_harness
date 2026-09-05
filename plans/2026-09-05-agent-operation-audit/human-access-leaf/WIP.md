# HUMAN audit access — isolated source WIP

Historical checkpoint, superseded by [current source verification](RESULTS.md).
The pending tests described below subsequently passed; retain this record as the
memory-emergency pause state, not the current acceptance result.

Last updated: 2026-09-06. Tree `simple_harness-audit-access`, base54156f1e,
branch `feat/human-memory-audit-access`. No source fixed commit yet.

Production source now composes the public audit authority into HumanMemoryV7Runtime,
routes primary.audit.open/page/close through the signed HUMAN service, persists
Host S1 grant actions and requested/saved/unknown page deliveries, and fences the
actual WS sender. Runtime close revokes in-memory grants. UI adds an explicit
metadata entry to PrimaryMemoryPanel, with bounded pages and original-action retry.
No main/native tree, SDK pin, model or terminal-audit identity code changed.

## Verification state

- Actual installed Memory0.6.12/Harness0.7.2 in this tree's isolated venv; no overlay.
- Original entry red: operation unavailable. After implementation: 1 passed, 0.83s,
  peak110416 KiB. This is the real signed scope + public SDK + SQLite path.
- Subsequent controls: 10 passed / 1 failed, 4.09s, peak125120 KiB. The failure
  entered a stale request scope after rebinding and was rejected before the intended
  final-sender check. Test now enters while current, then rebinds before send;
  this correction is not yet rerun.
- Additional exact budget/public snapshot controls and frontend tests are written
  but unrun. No frontend dependency install, typecheck, build, browser or native run.
- Dirac read-only review found a real initialization P1: blanket profile_bound
  revocation rejected boundPrimaryPort's cached replay. Source now distinguishes
  the current cached receipt from a new bound frame. Actual ControlChannel plus
  PrimaryChatView/MemoryPanel/AuditPanel regressions are written, not yet run.
- Dirac has not granted fixed-source ACCEPT. No full audit/program claim.

## Bound replay source review

Dirac read-only follow-up verified the actual mechanism: ControlChannel JSON-decodes
each new frame, stores that same object in latestMessages and fans it out;
boundPrimaryPort returns that cached object unchanged on subscription. Matching
cached replay therefore preserves readiness, while a new bound frame clears the
prior capability even for unchanged owner/epoch fields. No signature or SDK
authority is inferred from JavaScript object identity.

Independent pre-review reports the P1 implementation gap addressed and no second
confirmed P0/P1. The actual Channel/parent/panel oracle is written for initial cache
replay, fresh same-owner frame, rechallenge/global-ready, global owner change and
same/different-owner reconnect. No test was run after the slot was released.
Counterfactual old-code red/new-code green and final fixed-source review remain due.

Test slot released after pytest PID9726 exited; PID9647 had already exited.
No subsequent test group started. Main owns the slot until explicitly returned.

## Evidence index

All raw output is ignored beneath
`.local-test-evidence/2026-09-05/human-audit-access/`.
Synthetic challenge representation was redacted from the failed test traceback.

| File | SHA-256 |
| --- | --- |
| entry-purpose-fixed.log | 1d4a11fc90a7086c182cc06bc43bebeb59a36928082944ef103c86041aa4cf49 |
| authority-controls.log | ad5e6cbc28d78321d748511f2b45006162630d38c7de2fdb04369e57aa9808e3 |
| authority-controls-resources.json | 4e1710f3f5ce687dfe4ccbb593d683aae7020dd247e76edb8a19664eccf56557 |

Only the metadata projection is bounded; initial SDK snapshot scan cost is not
claimed bounded by the displayed page size. Physical calls/usage/cost are not
aggregated; absent usage/cost is not zero. This entry does not complete original
all-operation coverage, other producers, audit reasoning or native acceptance.
