# C05 actual-main terminal control/physical source dispatch

2026-09-07. Product delta and one new control are source-only / NOT_RUN.

Main's C05-04 first actual-main attempt issued 7 real local HTTP requests, completed marker/closure
and SDK run.complete, then failed Host terminal production with primary_message_scope_source_mismatch.
Original raw: main `.local-test-evidence/2026-09-07/corpus-c05-main-phase/r1/`.
PG85016 exit1, 11.462s, remaining=[]; other four phase cases were not run.

Mode=ro inspection of the existing Host DB established that source event
`effect:effect-2b24244e81f6a3702124f57c0f2b48cf6ea143a5c7949d65c5db2a0ca08b7754`
was the actual context_route control ledger import (raw setup-1), not a physical ToolInvocationFact.
Its actual payload carries raw_call_id/verdict/decision_id/proposal_hash; no internal call_id/effect_state.
Both legitimate producers share kind=tool_invocation. The v3 reader treated every such receipt as the
physical subtype and failed the absent internal call/state comparison. The later physical tool receipts,
including write_file, have the expected internal call/state fields. This is a production source-dispatch
error, not a reason to change USER scope, fabricate terminal, skip permissions or restamp old evidence.

Delta in primary_message_v3.py retains the complete existing shared event/receipt/reservation/owner
checks. Only an exact control payload with tool_name=context_route, actual fact raw ID, reservation
without physical tool_name, and the matching real context_route_tool_invocations row qualifies.
The entire invocation payload hash and the full echoed public payload must match; unknown/missing/
damaged sources reject. A verified control yields no Scope proof, as the existing v3 contract requires.
All other inputs retain the original internal call/tool/state checks and physical receipt proof.
No DDL, SDK change, evidence mutation, latest-route inference or new authority.

New selector, NOT_RUN:
`backend/tests/memory/test_procedure_scope_sources.py::test_real_route_control_ledger_has_no_physical_scope_and_mismatch_rejects`

This narrow producer/parser control uses actual Host ledger ingestion and actual physical ToolInvocationFact
ingestion, then verifies control=None and physical Scope retained. Wrong raw call/Run/tool and an unavailable/
changed ledger read reject. The read-wrapper negative does not alter business SQL or triggers and is not
claimed as actual on-disk corruption. Main's original C05-04 actual-main red is the physical integration
oracle; main schedules its retry and the as-yet-unrun cases. No old green tests rerun here.
