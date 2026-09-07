# Protocol attack mapping frozen before Memory 0.6.5 execution

2026-09-05. User explicitly clarified the independently reviewed narrow API contract.
This mapping preserves the one existing cell `unsupported-replay/request-hash:protocol_version`;
no cell or threshold is added, removed or substituted.

| Frozen attack identity | Earlier runner label | Actual new public input | Required rejection |
|---|---|---|---|
| request protocol version 4 -> 5 | `recall-v5` | `harness_protocol=5`, exact Python `int` | unsupported version at pre-DB protocol admission |

`harness_protocol="recall-v5"` is a different type-invalid input. Its TypeError/type-stage
rejection cannot satisfy this unsupported-version cell. If such a type regression is tested,
it is bridge/SDK regression evidence only, not an additional or replacement acceptance cell.

The request's accepted default remains integer4, with unchanged v4 request hash/domain.
The new optional keyword must not be passed to legacy backend ports for default Manager calls.
The original request label is retained as lineage; all remaining 13 mutations and bindings
remain unchanged. The no-input 0.6.4 candidate continues BLOCKED for this cell.

For 0.6.5, before execution freeze the exact public receipt DTO/stage/reason once supplied by
its producer; require immutable invocation binding, actual input integer5, unsupported-version
reason, candidate-query false/count0, and no durable state change. Do not accept a DB-fault,
corruption, timeout or cancellation receipt as idempotency rejection. The 13 existing attacks
may consume only the producer's reviewed type/ownership/narrowing/exact idempotency scopes.

## Exact witness mapping inspected before 0.6.5 candidate execution

Producer code inspected read-only at source30743bb (candidate wheel not yet executed):
`TypedRecallRejectionV1` is frozen, with exact fields schema_version/invocation_id/request_hash/
context_hash/plan_hash/stage/reason/candidate_query_started/candidate_query_count.

| Attack | stage | exception | reason | request_hash |
|---|---|---|---|---|
| protocol_version integer5 | protocol | MemoryValidationError | typed_recall_protocol_unsupported | null (pre-admission) |
| principal_id | ownership | MemoryOwnershipConflict | typed_recall_subject_not_owned | E(request with actual actor2) |
| context_hash | narrowing | ValueError | RecallPlan context_hash differs | E(actual invalid-narrowing request wire) |
| remaining11 | idempotency | MemoryIdempotencyConflict | IDEMPOTENCY_CONFLICT | E(actual request wire) |

Counts remain false/0 only for these actual pre-candidate rejection paths. Context/plan hashes
must bind their full actual inputs in all four rows. Each invocation_id must be unique in the
run; reject missing/duplicate/wrong-stage/reason/hash/count/incorrect public DTO type or mutable
witness. A receipt is an invocation observation, not durable authority; unchanged protected state
must still be independently proven. No receipt for DB fault/corruption/timeout/cancel or later
candidate failure means BLOCKED, never inferred zero reads. `remaining11` includes run/context
revision, plan id/reasons, disclosure/recipient/purpose and four budget mutations.
