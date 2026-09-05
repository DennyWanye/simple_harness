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
