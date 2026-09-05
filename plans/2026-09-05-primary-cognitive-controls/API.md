# Authenticated cognitive read/forget API

Last updated: 2026-09-05. Local candidate; frontend/native and natural-language correction are not completed by this slice.

Production HumanMemoryHostServiceFactory lazily resolves the actual V7 runtime.
Only the existing signed HUMAN control dispatcher exposes these operations; no Agent tool,
legacy fact ID or SDK-private database access was added. SDK graph is display-only.

- `primary.memory.list`: `{primary_ref, limit?:1..50, cursor?:memory_id}` returns
  `{primary_ref, items, next_cursor}`. Items include exact memory_id/revision/hash,
  label/status/can_forget. The SDK graph scan remains unbounded; only output is
  paginated/bounded. Redacted nodes are omitted.
- `primary.memory.forget`: `{primary_ref, action_id, memory_id, expected_revision,
  expected_content_hash}` returns only an exact public SDK durable result:
  `{primary_ref, action_id, memory_id, status:"applied", evidence_ref, directive_ref, decision_hash}`.
  Transport request_id can change; action_id and the target tuple cannot.

The target must be currently visible, forgettable and match the supplied revision/hash
before the first Host action. Suppression addresses the complete memory identity,
not a cross-database revision compare-and-swap. Replaying an exact dedicated action
uses its original committed_at and SDK request ID even if the target is already hidden.
A concurrent same action committed between initial lookup and view is recognized by
a second exact Host lookup; missing view alone never grants replay.

Actual connection revocation is checked after slow reads, by the action writer and
again under the shared connection barrier through public SDK suppression. Host recovery
ingress is checked there too. A committed action followed by rebind remains durable
with zero unauthorized SDK writes; fresh authenticated retry can complete it. No
automatic undo, physical delete, new analysis job or Provider call is created.

## Verification

Installed exact Memory0.6.7/Harness0.7.2, real SQLite and signed connection fixture.
The initial memory uses the existing public SDK materialization fixture, not real model analysis.

- Initial6 tests: replay/reopen, before-SDK failure, SDK commit/lost ACK, two real
  rebind boundaries, stale/foreign/malformed selection.
- Initial combined46 passed20.92s across new API, action evidence, runtime/history,
  Human API/fence and primary read API. This was before frontend wire alignment.
- Final wire9 passed5.53s:6 signed cognitive cases plus3 runtime integration cases.
  The actual forget API now filters old page/detail and the next actual
  ProductProviderAdapter outbound request after reopen. Original archived bytes
  stay intact; exactly one new explicit action is admitted. MockTransport is
  deterministic; this is not native or external Provider evidence.
- Added interleaving exact-action convergence:1 passed0.58s. Ruff imports/format only afterward.

Commands use `PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-candidate/venv/bin/python -m pytest ... -q -p no:cacheprovider`.
Final wire files: `backend/tests/memory/test_primary_cognitive_controls.py` and
`backend/tests/memory/test_primary_runtime_api_integration.py`; concurrency selection
`test_same_action_committed_between_initial_lookup_and_view_converges`.
Earlier combined additionally selected `test_primary_cognitive_evidence.py`,
`test_human_memory_api_and_fence.py`, `test_primary_read_api.py`.
Raw evidence stays ignored under `.local-test-evidence/2026-09-05/primary-candidate/`.

- `cognitive-controls-first.log` SHA256 `5d74200beef62467473abcde0fb62d828d7aa622c81dcb5863519f5899999042`
- `cognitive-controls-combined.log` SHA256 `1ea1e06100fec4de09ca938130fcece197d73170337ee6579102e5227e66a9df`
- `cognitive-wire-combined.log` SHA256 `e646907bb9586d4e9f841118b2c0634afb4f4e673bdd8e53bd0e97755cbf2879`
- `cognitive-concurrent.log` SHA256 `8afe2471606046b386cbefd7224d475f9d94ddb804d914ca3a5b3472c14d8f5c`

## Independent review and final oracle

Independent review accepted506d98f3 within the stated source-history outbound scope.
This does not prove a real typed-recall selected binding reaching outbound and later
being forgotten; that separate claim remains untested here. Final narrow correction
adds the missing required primary_ref to the foreign-subject oracle, asserts the actual
subject-mismatch code, bounds status text and checks the returned SDK effective_at.
All7 cognitive API tests passed3.36s. Raw `cognitive-final-api.log` SHA256 `5aa8d79f16e8a16a9cfda086c2af4fdeb6393fcdb57ea75c3ce1ef2954074970`.
