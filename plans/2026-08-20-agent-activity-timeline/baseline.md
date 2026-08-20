# Baseline

Baseline HEAD: `4b82e3b1e6a76e3bb90f57125013a9ca68d2d430`

The existing repository is large; phase-2 baseline uses the repository's shard
runner and records pre-existing failures in the existing known-failures ledger.
The focused baseline for this slice is:

- Frontend: `cd tauri-app && npm test -- --run src/components/AgentActivityMessage.test.tsx src/chat/HarnessInspectorPanel.test.tsx src/stores/harnessPublicSnapshotStore.test.ts`
- Frontend type check: `cd tauri-app && npm run typecheck` (or the repository's configured equivalent)
- Backend: `cd backend && pytest -q tests/test_harness_public_read_service.py tests/test_public_projection_migrations.py`
- Python compile: `python -m compileall -q backend/deskpet backend/main.py`

The baseline commands must be run and their exact results appended before Phase 3 implementation.
