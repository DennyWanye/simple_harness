# A7 review controls — 2026-09-06

Scope: service initialization, bounded public inbox transport, exact snapshot codec.
Not full A7 presentation/ACK/physical-provider acceptance. No models/native.

Fixed production source: cbce797d (ab40886c + 0167ad76 + ACK schema compatibility).
Dirac ab40886c/904b3c69 limited source ACCEPT; successor differences pending review.

| Batch | Result | Meaning |
|---|---|---|
| a7-r2 | 2 PASS / 1 FAIL | cap + codec; factory old M616 pin vs installed M617 blocked |
| a7-r3 | 1 PASS / 1 FAIL | recomputed-receipt strict codec; actual schema ordering defect |
| a7-r4 | 1 FAIL | actual Harness rejects unsupported pattern schema keyword |
| a7-r5 | 1 PASS | real factory first build + same52 reopen namespace + ACK catalog |

No pass-count summing across retries. Original reds retained. Cap is synthetic page transport, not3200 actual SDK entries.
The local launcher supplies fixed reviewed H076/M617 identities to unchanged real version/hash/origin verifiers; no product pins changed.
Only selected factory/cap/codec controls ran. Other previously NOT_RUN A7 controls remain NOT_RUN.
All groups88056/88342/88507/88737 exited; remaining[] and cleanup_error=null. Maximum433024KiB. Shared slot released.

Command: shared `scripts/run_resource_bounded.py --rss-mib 2048 --seconds 180 -- <existing-python> -I -B <local>/run_a7.py <batch> tests/test_provider_runtime_refresh.py::test_human_epoch_composition_registers_three_authorities -k <selection>`.
Selections r2=`human_epoch_composition or current_reader_cap or snapshot_rollback`; r3 drops cap; r4/r5=factory only.
Raw remains ignored under `.local-test-evidence/2026-09-06/prospective-timer/`.

New batch evidence hashes:
- `a7-r2/command.log` SHA256 `76e8816c24120108ee8d078d27e8477f6132e133a4dab68b1963998e75aed5c4`
- `a7-r2/resource.json` SHA256 `4831f2e876101b5664217f19bc4fadb09ff5e40b9a651e7db37486a88eee5669`
- `a7-r2/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r3/command.log` SHA256 `3ee6aa2d1ce0d200de4c2c77f13712062ff64de5b8dc35c94a945e4f97af9e02`
- `a7-r3/resource.json` SHA256 `dfe925db287a83d05c32483f2437d180cc9b1a8b7f3b0d33fbd8f49aab957bdb`
- `a7-r3/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r4/command.log` SHA256 `c2820307861e8357601c849fc952197a021d906c3c5acfeeebc6e0940041434e`
- `a7-r4/resource.json` SHA256 `8fa9d73f6c52c6f99655272d5e94e15256191e08f90ad25972a10f1dfe9bdc1b`
- `a7-r4/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r5/command.log` SHA256 `3974de7443beebd4eb72ef69c0ed167b4b4e4cee5c06cfaacda6b857ee0ab7cf`
- `a7-r5/resource.json` SHA256 `c27181387d2d8da2d8dce2f2badcc59cb64c4efce6fcbb7405054ae40649de12`
- `a7-r5/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`

## Core first batch a7-r6 (71ddcc79)

5 PASS / 2 FAIL / 2 deselected in6.86s; PG93287 exit1/remaining[],
peak402704KiB, cleanupnull. Five first-execution transaction/ACK controls passed:
three unACKed presentations/overdue and fourth ACK+unit terminal, two ACK
commit-fault replays, real exception guard invocation, competing ACK one winner
and exact wrong-terminal rejection. These still use fixture Run IDs, not actual
Harness completion evidence. Two new actual-runtime parameters both stopped in
fixture catalog hashing of the SDK frozen mappingproxy; no runtime acceptance.
The fixture now uses public thaw_json for the actual SDK tool schema, preserving
its exact content before Host canonical hash. Only these two failures need retry.
Shared slot released to Hegel. No old green rerun.
- `a7-r6/command.log` SHA256 `ee19aee83c4ee941c00cbb692640a340cb3f12698aec30997efb04495c2fb58e`
- `a7-r6/resource.json` SHA256 `6bfc79eb1945caa68fa50a255fc94df3f17a827495b21458e2af1240c58d06a2`
- `a7-r6/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`

`a7-r7` only two runtime retries: 2 FAIL/7 deselected in2.17s. PG93590
exit1/remaining[], peak404592KiB, cleanupnull. Both now get through catalog/SDK
startup and fail actual `service.enqueue_turn -> initialize_subject`: older
program initializer rejects schema52. Successor validation belongs at the shared
subject initializer, not only typed authority; that exact52 validator branch is
moved there, restoring typed authority to the shared call. No DDL/default schema
version change, unknown epochs still rejected. This new fix is NOT_RUN.
Main-owner startup dispatch still requires explicit exact52 compatibility review:
its default inspect maximum remains old target. This leaf does not silently
widen the schema dispatcher or claim whole app restart acceptance.
