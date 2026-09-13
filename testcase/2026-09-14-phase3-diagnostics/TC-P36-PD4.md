---
id: TC-P36-PD4
purpose: Verify native failed Mission diagnostics and repeat local support export survive a cold restart without replaying work
status: active
surface: desktop-ui
type: hybrid
obligations: [P36-A02, P36-A03, P36-A08, PD4]
tags: [phase3, diagnostics, privacy, cold-start, idempotency]
entrypoint: task orchestration Mission details
revision: 1
---

# TC-P36-PD4 — Failed Mission diagnostics and cold support export

Precondition: committed source snapshot and isolated userdata, unchanged n4-bad-quote material, one Tauri-owned backend/Vite. Use native UI actions; ledger captures supplement rather than replace UI evidence.

| Step | Action | Expected |
|---|---|---|
| 1 | Create the controlled document Mission through the native form and file picker. | Bad quote is rejected; Mission failure and actual verification layers are visible. |
| 2 | Read diagnostics and export twice using buttons. | Current Mission only; failure is not presented as successful delivery; unknown/unpriced/reserved accounting is explicit; report bytes/hash stable. |
| 3 | Inspect the report receipt visually. | Complete long path/hash wraps within the panel. |
| 4 | Quit normally, start the identical snapshot with original userdata, reopen the same Mission and read/export. | Original failure, context/runtime identity and support hash persist; no model rehandoff or durable task/usage/event mutations. |
| 5 | Compare selected ledger table hashes and verify support content. | Stable table hashes; report excludes raw source/goal/artifact bytes, credentials and unrelated history. |

Results and exact source/evidence hashes: [P36 diagnostics results](../../plans/2026-09-12-phase3-host-g/p36-diagnostics-plan.md). Raw files remain ignored. Authorization/canary, late response and Mission switching cases are also covered by `backend/tests/orchestration/test_diagnostics_contract.py`, `test_mission_diagnostics.py`, and `tauri-app/src/views/MissionDiagnostics.test.tsx`. This deterministic UI scenario does not substitute for real-provider document-quality acceptance.
