# Current-use: two real items and independent authority checks

2026-09-06. Base fbebdaff; scope is the existing six context-use executor cells.
Fixture revision8,401 IDs, thresholds and H073/M0613 pins are unchanged. No plan-test
skills/machine gate, SDK source/SQL/product test helpers, model or native execution.
Source review precedes testing; then only the assigned default shared lock slot,
2GiB/180s and the existing installed consumer. No new venv/build.

Frozen `typed-recall-v3.json`: context_use_cases has six definitions, including
new-continuation. The current implemented set instead has five context cases plus
authority:suppression. Do not silently replace an ID or claim the absent
context:new-continuation cell has run. All six existing cells must retain their
individual obligations. Continuation is a Harness consumption binding; its absence
from the Memory request does not block every Memory authority scenario.

| Existing cell | Decisive trace and independent verdict |
|---|---|
| context:suppression-first | Two-item result/page/snapshot first, actual suppression commits, original request denied stale with zero receipt; fresh one-item recall exposes only the unaffected item. |
| authority:suppression | Public before/after recall authority_epoch increases exactly one, policy hash unchanged, historical recall replay exact; new use of the old two-item result rejects. |
| context:receipt-first | Original two-item use receipt commits before suppression; same request replays unchanged after suppression/reopen; new attempts reject; exact original snapshot is validated by public receipt API. |
| context:duplicate-same-provider-attempt | Repeated identical request before and after suppression/reopen returns identical receipt/hash. Replay alone is not a second-consumption counter; changed-snapshot attack is separately executed in wrong-snapshot. |
| context:new-provider-attempt | Old receipt rejects changed attempt via public validate_request; before suppression a genuinely new authorization yields a different receipt; after suppression a fresh attempt cannot use the stale result. |
| context:wrong-snapshot | Only manifest hash mutation rejects at strict request DTO; also a syntactically valid changed fragment manifest under the same attempt reaches public authorization and must not replay the old grant. Neither rejection is fabricated from cell/hash differences. |

Inputs are compiled from frozen timing/run/turn/attempt definitions, not expected
SDK output. Seed two uncontested semantic memories using the incumbent payload;
second subject_entity is the fixed input `context-use-secondary` so both contain
the frozen query predicate but have distinct semantic identities. Freeze this
construction independently in the oracle; two mutable values of the same semantic
key would incorrectly test conflict groups instead. Both go through public S1,
mutation and receipt-view APIs. Their actual IDs/hashes are bound back to these
independently checked sources, never copied into expected gold.

The result must contain both exact sources and two real items; request all items
and page one whole item at a time. Independently verify every result/page/fragment/
snapshot/request/use-receipt hash using approved E=H(C({domain,payload})), full
subject/run/turn/attempt links, cardinality, ordered membership, authority epoch,
policy and times. Suppress one exact memory, then prove the unaffected item remains.
Original fixture numeric clocks are retained; epoch40/literal receipt hashes are
legacy commitments under the existing approved_oracle rebind, not product inputs.

Original S3 §5.4 explicitly gives Memory only run/turn/provider-attempt; Harness
provider reservation must consume/bind its receipt for each continuation/attempt.
Public root introspection on installed H073/M0613 (read-only, no product operation)
finds `RecallContextUseAuthorizationRequestV1` and receipt bind attempt/snapshot but
have no continuation field. `RecallContextUseReceiptV1.validate_request(request)`
exists; it does not expose a durable consumption count. Absence of continuation
on the Memory DTO is intentional under this plan, NOT an SDK bug. Strict decoding
of an extra continuation_id would only prove schema rejection, so is not used as
an execution or acceptance witness. Do not alias continuation to attempt,
turn, fragment ID or an arbitrary string, or synthesize SDK results.

The authority:suppression, suppression-first, new-provider-attempt and wrong-snapshot
cells can PASS their original Memory-side obligations after the independent checks
execute; receipt-first/duplicate remain BLOCKED on
`CURRENT_USE_HARNESS_RESERVATION_EXACT_ONCE_WITNESS_UNVERIFIED` even if all Memory
assertions pass. Dirac's independent read of exact H073 found no built-in receipt
consumer on ConsumerRuntimePorts/provider reservation. Supplying a custom provider
and counting our own authorizations would test our adapter, not that missing
reservation witness. This is a scoped Harness-consumer gap, not a Memory defect.
The separate new-continuation cell is not silently counted among these six.
No generic pending blocker is
removed until the corresponding independent assertions really execute and pass;
unsupported obligations get precise gaps, not an unconditional PASS. A genuine
unexpected public rejection/hash/epoch mismatch is FAIL, not hidden by these gaps.

## Pre-execution rejection oracle (Dirac challenge incorporated)

- Receipt validation changes only provider_attempt_id; requested_at and every other
  field remain identical. Exact public rejection is ValueError /
  `receipt request_hash differs`; changing time too could conceal a missing attempt binding.
- Wrong manifest only: strict DTO rejects ValueError /
  `snapshot_manifest_hash differs from fragment bindings`.
- Valid reversed two-fragment manifest, independently recomputed hash, same original
  attempt and time: actual public use must reject MemoryIdempotencyConflict /
  `RECALL_CONTEXT_USE_IDEMPOTENCY_CONFLICT`. This pins the existing M0613 same-attempt
  contract and realizes the original SNAPSHOT_BINDING_MISMATCH obligation. Unrelated
  STALE, ownership or generic idempotency reasons do not count.
- Real first/second authorizations precede suppression. A third fresh attempt after
  suppression must reject STALE; testing attempt-2 there would incorrectly demand
  rejection of an already committed exact grant in the new-provider-attempt cell.
- Independent negative tests corrupt page requests/membership/bytes, item/receipt
  hashes, epoch/policy, attempt-only inputs/reasons, stale/changed-manifest rejection,
  suppression scope, commit order, unaffected-item visibility and durable replay.
  These are bridge/oracle regression tests, not additional product cells.
