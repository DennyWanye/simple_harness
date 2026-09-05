# Cytoscape HUMAN graph display leaf — scoped results

2026-09-05. Base bf8f9f7d; only graph source is delivered for main integration and for
cherry-picking to coordinator's043c native candidate. No audit/default/pin change.
Cytoscape3.34.2 installed independently in this tree; no shared main node_modules writes.
Tests import installed Harness0.7.2/Memory0.6.10 from candidate's venv, Host from this tree.
No Memory source overlay, private SDK SQL, remote Provider or native process was used.

## Results

- Backend24 PASS: public canonical graph/relation; exact endpoint output; endpoint and relation
  suppression/reopen; bounds/authority/rebind; actual suppression ACKloss notification;
  same-binding slow-read and final-identity-boundary suppression races; real SDK analysis
  runner CREATE→REVISE, no_mutation, notification failure, idle/reopen; actual main lane
  activation consumes the same instance carried by a real HumanMemoryHostServiceFactory.
  Existing cognitive controls7 included, preserving real action admission/replay.
- Frontend21 PASS: GraphRequests5, graph lifecycle1, actual Cytoscape2, existing cognitive11
  and panel2. HOST_GRAPH_FIXTURE explicitly set: conditional actual API→Cytoscape case ran,
  not skipped. Unknown forget before/after graph mount, identity changes and stale replies
  covered. Typecheck via npm build and standalone typecheck passed; focused ESLint passed.
- Vite production bundle PASS, existing large chunk warning retained. No CDN fetch/runtime
  dependency; exact package and integrity are locked in package-lock.json.
- Actual in-app browser1280×720 loaded real components/Cytoscape with exported real API
  before/after snapshots (transport seam). Canvas mouse(356,451) selected actual semantic
  node; zoom and fit visibly worked; scroll exposed actual details. Loading actual SDK
  suppression result removed semantic endpoint/relation and selected details, leaving the
  real procedure node. Initial overlapping label corrected to side labels and rechecked.
  This is API-fixture browser renderer proof, NOT native/live-model relation creation.

## Commands

From repo root (PY points to existing read-only installed candidate venv):

```sh
PYTHONPATH=backend "$PY" -m pytest backend/tests/memory/test_primary_memory_graph.py backend/tests/memory/test_memory_display_producers.py backend/tests/memory/test_primary_cognitive_controls.py -q --basetemp=.local-test-evidence/2026-09-05/cytoscape-ui/pytest-final
HOST_GRAPH_FIXTURE="$PWD/.local-test-evidence/2026-09-05/cytoscape-ui/actual-api-graph.json" PYTHONPATH=backend "$PY" -m pytest backend/tests/memory/test_primary_memory_graph.py::test_actual_public_graph_endpoint_has_real_relation_and_bounded_closed_endpoints -q --basetemp=.local-test-evidence/2026-09-05/cytoscape-ui/pytest-export
```

From tauri-app (set HOST_GRAPH_FIXTURE to that absolute ignored JSON):

```sh
npm ci --ignore-scripts --no-audit --no-fund
npm test -- --run src/primary/graphRequests.test.ts src/components/PrimaryMemoryGraph.test.tsx src/components/MemoryGraphCanvas.test.ts src/primary/cognitiveRequests.test.ts src/components/PrimaryMemoryPanel.test.tsx
npm run typecheck
npm run build
```

## Boundaries / handoff

Graph is default available inside primary cognitive panel after verified binding. It is
USER display only. Output/text counts are bounded; SDK get_twin_graph_view still full-scans.
No invented Task/Person/Goal/Evidence nodes or relation mutation targets. Direct relation
correction/forget and full controlled evidence audit remain original-plan follow-up.
User's original native node/select/zoom/forget verification is coordinator-owned pending;
empty production graph must stay empty until actual new analysis materializes it.
No HM-AC6/full program/401 cells/native relation PASS claim.

Dirac independent WIP review found final-boundary P1: retained original probe red, moved
stamp check after last API await, original probe rerun accepted. Frontend pending/owner
and llm_inference fixes narrowly accepted in WIP; fixed source final review requested.
Full suites are deliberately not rerun. Ruff on changed source reports two pre-existing
human_memory_service findings (BLE001 and unsorted __all__), not modified by this leaf.

## Local evidence

All raw remains at repo root `.local-test-evidence/2026-09-05/cytoscape-ui/` (ignored).
Browser harness moved out of tauri-app; no raw evidence staged. No auth material in fixture.

| File | SHA-256 |
| --- | --- |
| `backend-final.log` | `b27dafa4bb2351a5e7a0dd2de2a73f948636b516cc1ccf4e6a92da25e1083d1f` |
| `frontend-final.log` | `4760655170e0da7c1f89005d1cfe0660f1c7e8014893fd6d99f01f14216b3f29` |
| `build-final.log` | `253d65373e98e30d42330bfb8f642821b19539cf62ca0a7349e065bc7e8e39cd` |
| `eslint-final.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `actual-api-graph.json` | `0fbe7c6dbb1fcd60dc1551d4bfc493bc108607cf5f5e95203dce4b2f316dfd98` |
| `actual-api-graph.forgotten.json` | `b26182f2b22ffb818e4ce313496cb3c01a9626830936585f229caba75350d43c` |
| `01-node-selected.png` | `607389f202ec1443ed7332cb6a92d5f84fd397bbf2ca400504f565aa90e2a9aa` |
| `02-zoom.png` | `7cec5ef77cf388c3aae7bea4fd5c7a1b293edf9cd1d232c1bad0d00d6a4f2172` |
| `03-details.png` | `0eddb0d0d8479046fce2d3c4aafff4638a26e19c37c99d98b6e62c4a937a8712` |
| `04-suppressed.png` | `ff815d392996e67a94359507cc76f786ed448b7f8a0d5eae6c70f1b485835d6d` |
