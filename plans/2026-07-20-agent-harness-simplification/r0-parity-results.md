# R0 product-turn parity census

Date: 2026-07-20

Base commit: `4d38979e`

Scope: legacy Text `_run_chat`, Voice, AutoResume, workflow product delivery,
permission restoration, waiters/cancellation, persistence and codify.  This
slice changes no production behavior.

## Frozen census

```powershell
F:\projects\deskpet\backend\.venv\Scripts\python.exe scripts/acceptance/harness_parity_census.py --check
```

Result: `HARNESS_PARITY_CENSUS: PASS items=141 unmapped=0`.

The JSON fixture records one stable row per AST callsite with
`callsite/source_hash/kind/count/capability`.  Every inventory capability also
freezes its legacy precondition, input fields, output frames, side effects,
ordering, error/cancel semantics, intended owner and test evidence.  A selected
`send_json` whose event contract cannot be resolved makes generation fail.

Counts by kind:

- event type: 41
- WebSocket/peer send: 57
- SessionDB sink: 12
- vector sink: 5
- file/receipt sink: 1
- Context OS sink: 9
- waiter: 5
- cancellation: 3
- codify: 7
- permission restore: 1

## Behavior observations

`test_product_turn_parity.py` runs old production components instead of using
source-string assertions for behavior.  It directly observes:

- Voice user/final peer broadcast with effective session remap;
- emotion/action tag projection and streaming tag removal from spoken text;
- Voice codify scheduling without blocking the foreground turn;
- AutoResume system-hint injection, audit-before-dispatch and started event;
- permission auto-mode persistence across a reconstructed gate;
- workflow artifact persistence before the live publisher is called.

The census AST remains a coverage gate for the nested Text `_run_chat` owner;
it is not presented as behavioral equivalence evidence.

## Frozen TurnInput surface

`text`, `session_id`, `request_id`, `turn_id`, `venue`, `mode`,
`memory_policy`, `explicit_new`, `attachment_blocks`, `provider_ref`,
`capability_ref`, and `workspace_ref`.

R2 must reject or preserve all fields; adapters may not silently discard one.
