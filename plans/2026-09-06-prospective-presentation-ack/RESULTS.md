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

`a7-r8` did not execute pytest: launcher invocation omitted the Python executable,
runner returned125/FileNotFoundError with group_id=null/remaining[], peak0. This is
an agent command error, not product evidence. No retry while main native takes
priority. The correct command retains `<python> -I -B run_a7.py`; ab5aca99 remains
NOT_RUN against the two actual-runtime failures.

`a7-r9` after subject fix: 2 FAIL/7 deselected in2.85s, PG95502
exit1/remaining[], peak395296KiB. Real enqueue and SDK Run start now succeed.
Both actual Provider guards reject before suppression/no_recall with exact
`s5c_occurrence_actual_handoff_missing`: the reused old foreground fixture never
installed production typed Context-use authority/coordinator, so public Context-use
view is unavailable. This is not a privacy negative PASS. Fixture gains optional
real ProductTypedContextUseAuthority + ProductProviderInvocationCoordinator plus
snapshot typed authority, as main already composes, preserving public handoff
verification (no fabricated view/no relaxed guard). New fix NOT_RUN; slot released
Hegel, who subsequently completed his four red retries and released the slot.

## Actual runtime r10 — source86380353

**2 PASS / 7 deselected**, only the two outstanding runtime failures rerun.
Real H076/M617 public APIs, production Host current reader, typed-context-use
coordinator, ProductProviderAdapter + deterministic HTTP transport, ProductToolsAdapter
and actual SDK terminal evidence. No models/native.

- Three real unACKed Runs: three committed presentations, same actual occurrence,
  exact production NoRecallBlockedError and FAILED terminals, no exit, one overdue.
- Fourth Run actual ACK tool: one receipt, Host ACK + original Run actual terminal
  settle. Third-Run pre-ACK rebuild and ACK-afterward public Memory/full runtime
  reopen preserve original receipt, empty queue produces no extra HTTP calls.
- Late Memory-only suppression: the same actual handoff request first passes the
  real physical guard; public suppress then changes current visibility, exact cause
  s5c_occurrence_current_read_changed rejects, FAILED and zero HTTP calls, no repeat.
- Five prior transaction controls fromr6 not rerun; no summing retries into suite
  or full A7 completeness. Five explicit Context routes, exact terminal commit-fault
  recovery, foreign principal and full derived-history inheritance remain separate.

PG95773 exit0/remaining[], cleanupnull, peak397120KiB, elapsed6.444s; minDisk4202MiB.
Slot released. r10 source delta and results submitted for Dirac limited review.
Whole-app startup dispatch<=49 still separate main-owner seam; direct fixture
stack rebuilding is not app startup acceptance.
- `a7-r7/command.log` SHA256 `67df6ade2793c9171636312ca8f1aa93b790bef93c6b39e73db524465ec37531`
- `a7-r7/resource.json` SHA256 `22a6fc7e2ae0d5216e827391e0acb7b8612800c2b567500f3e863c5a84df0d6e`
- `a7-r7/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r8/command.log` SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- `a7-r8/resource.json` SHA256 `dc374f55c829890e3c55c526e28af7bbed51512f07a279a5b6fe6a1cb4deb1f3`
- `a7-r9/command.log` SHA256 `8001c5ad74408006f4f8d3fb1a7bdd3b518594ad9cda3358931bd5143e1d55d4`
- `a7-r9/resource.json` SHA256 `4ad31e6e11c0ac337a144a0830ba2031c067bdac691cbe42e7d706813e8d0f0d`
- `a7-r9/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r10/command.log` SHA256 `34bf1c0b159f3519d342632dc33ba272552ef6385b7426d1501d8a3f808b5454`
- `a7-r10/resource.json` SHA256 `7d3ca70c98295ef434ab80630cd4d4616362cabc5c3c0e4c58044f9ecf7ba14e`
- `a7-r10/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`

## ACK terminal recovery (4438ceaf / 409dcc60)

a7-r11: 1 PASS / 1 FAIL / 9 deselected, 5.53s. after_commit passed; before_commit
recovered the real completed SDK Run but failed process-local tool authority release
with KeyError. Production startup restores only recoverable SDK Runs, so completed
Run authority is correctly absent. 409dcc60 makes foreground release lookup absence
idempotent after the checked Host terminal TX; cleanup/listener errors still propagate.
It does not authenticate terminal state or recreate active authority.

a7-r12: 5 PASS / 9 deselected, 4.27s. One actual before_commit retry, three new
release boundary controls, and one accidentally selected old ACK unit control
(the latter is not new coverage). after_commit was not rerun. PG97749 exit0,
remaining[], cleanupnull, peak399904KiB; slot released. Exact SDK terminal event/hash,
ACK record/hash, unique settled fact and unchanged physical send count are asserted.
Dirac fixed-source 409dcc60 / 4438ceaf and r12 limited ACCEPT.
No full-app52/native evidence. Derived-history withdrawal, five routes and foreign
principal remain incomplete; subsequent source WIP is not covered by this acceptance.
Command uses the existing installed H076/M617 launcher with both occurrence runtime
and release test files, selection `before_commit or cold_terminal_release or present_authority_cleanup`.

- `a7-r11/command.log` SHA256 `95d5101ae408a1dc06afa025a2edd1b4734830a10103094f9ab97cf8b01937d9`
- `a7-r11/resource.json` SHA256 `28e1adfb15fc9ce6ab984e886b6ca8006400f39d7dd068796af9ca761813d2c7`
- `a7-r11/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r12/command.log` SHA256 `6481ecf66446eca1b944bcd24848872e0d811e790db55bba0ddfa9abfaf506a8`
- `a7-r12/resource.json` SHA256 `e6ec6d9ad6359408b9eebe8e0476a787df8a716fa25030b6deb468ae4729a4b6`
- `a7-r12/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`

## New original-source / history controls

- a7-r13 source37ff071f: 1 PASS/7 deselected3.91s, PG98592exit0/remaining[],
  peak392096KiB. Real ACK terminal and next derived turn are visible before
  MEMORY-only suppression; both histories are filtered afterward, public Memory
  reopen retains withdrawal and next physical Provider request has no canary while
  the independent current USER remains. Original no_recall refusals stay intact.
- a7-r14 source049330e0: 1 PASS/7 deselected1.78s, PG98894exit0/remaining[],
  peak392176KiB. Real source positive reaches exact receipt; foreign principal/owner
  and substituted receipt ID reject at their named checks. Late EVIDENCE-only
  suppression rejects before any HTTP send at source_not_visible (not merely
  missing SDK handoff or an unrelated capability).

Both use the installed H076/M617 launcher and exactly the named new test node +
matching -k. No old green controls reran. No native/model. Source review pending;
five route execution controls are separate and were not included in these passes.

- `a7-r13/command.log` SHA256 `7dc5e54e1bafe9e46f1ac73e10dd1cfbd26a32d43c47f742e89d5c1fc6966da5`
- `a7-r13/resource.json` SHA256 `4b9d3801221bb81511c673bfb19c53663c888d8ff95f9bee1ea58de0d59abf8e`
- `a7-r13/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r14/command.log` SHA256 `ed0e04e8da13c5cf66ece366c648da41180d431c7508ef6710eea006227548ea`
- `a7-r14/resource.json` SHA256 `62c71924f694dd9d3836764370e855a8774083cb737de2e9ad23a9c3aea155c9`
- `a7-r14/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`

## Five actual routes and successful unACKed Runs

- r15 (20639ef8): 5FAIL/7deselect8.43s, PG99084exit1. Combined test adapter
  lost process-local Run authority. Production main already binds it.
- r16 (621a64d3): 5FAIL/7deselect8.46s, PG99240exit1. Combined test adapter
  also lost the frozen execution-identity mapping. No product gate was weakened.
- r17 (3fef4770): 5PASS/7deselect10.67s, PG99406exit0/remaining[],
  peak415344KiB. Actual production direct_standalone, memory_standalone,
  continue_active, resume_existing, create_new each reaches ACK then exact SDK
  completed/Host settled. Public typed recall and real workspace binding are
  exercised; no fake route receipt or fake TaskScope. Direct also refuses a
  foreign-principal ACK through actual current-disclosure reader with zero journal
  writes. This is deterministic HTTP transport, not native or external action.
- r18: default lock BUSY75, no child.
- r19: agent command omitted interpreter, runner125/FileNotFoundError, no child.
  This is a command mistake, not a product failure.
- r20 (18066453, test6dbc51ba): 1PASS/7deselect3.68s, PG99816exit0/remaining[],
  peak399808KiB/minDisk4957MiB. Three real explicit direct routes complete without
  ACK; presentations count3/uniqueoverdue1 and still pending. Fourth Run ACK and
  same-Run terminal settle. Real production reconcile retains original no_recall
  mandatory rule. Shared exact52 initializer from main is exercised by enqueue.

Runner commands select the new five-route test only for r15-r17; r20 selects only
`test_three_legal_route_presentations_then_fourth_run_ack`. r17 green parameters
were not rerun. All processes cleared; shared slot released to main native.

Integration: main startup commits e21f6e47/6809c22b/f2e524b3/88e9106b were
cherry-picked unchanged; 18066453 removes our redundant namespace52 special case
in favor of their shared initializer. No main factory hunk from another owner was
overwritten. H076/M617 installed proof stays separate from main H078/native.
New source/aggregate evidence independent review is pending; earlier 409dcc60 ACK
release acceptance remains scoped. A7 native journey and event-trigger receipt
source are not covered by these tests; no reminder action is inferred from ACK.

- `a7-r15/command.log` SHA256 `fe0e266b0162dd4edfe56c0aa50422bcf70fe414618a9aecea71f1987ebd3090`
- `a7-r15/resource.json` SHA256 `401f6c64d2bd2573457ab29879416460f5611ba4428fd38564da3a3aca6461e0`
- `a7-r15/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r16/command.log` SHA256 `a39be558760d77ccc117852f2de5e2488854891b33f62fc61c4ecfc232b69b2c`
- `a7-r16/resource.json` SHA256 `7aa7d9d343b77449e85b082f49f159105c24e9b1f2b122a2aa6766092c90b4d4`
- `a7-r16/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r17/command.log` SHA256 `98a89b385424f6a3f379983fa8672cfa49c035338b33e6c63d6626bf06ee1f10`
- `a7-r17/resource.json` SHA256 `53ec7e8aee7995ae659641881256dca0b599132f39934bb6700babacf2c14d82`
- `a7-r17/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r19/command.log` SHA256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- `a7-r19/resource.json` SHA256 `5706dc10da77699b7b788ee6e1738ffb0e607f8d5eea1490935d99c10eb72615`
- `a7-r20/command.log` SHA256 `3ba43022ad4058bc6e5e1731b789b049f1a98ebd5973b9c3c963213ee9a50b22`
- `a7-r20/resource.json` SHA256 `1044fcbd31aef5a4c66c98c132c0486d7787f9e8517810c229d8ac62de3a2e13`
- `a7-r20/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`

## Dirac disclosure-generation P1 — actual red / targeted green

Dirac rejected the new e9d6bb9e/37ff source closure: source policy could await a
slow public Memory check against G1 while an authenticated Host configure committed
G2, then return without revalidating the original Run binding. Previous inbox
recheck ran before this new await. This was a missing existing fence, not a request
for a cross-database lock or replacement authorization.

- Test source f312d95c / r21: 1FAIL7deselect1.83s, PG1381exit1/remaining[],
  peak404000KiB. Actual Memory source snapshot is visible under G1, the verified
  control API commits G2, then the suspended checker returns its real result.
  Old guard physically sends once, failing the zero-send oracle. Original Host DB
  also retains generations1 and2 (read-only verification, no SDK SQL).
- Fix e858d98f / r22: only the same new race, 1PASS7deselect1.80s,
  PG1540exit0/remaining[], peak404304KiB/minDisk4895MiB. Re-resolve the SAME Run
  and request_id after the await; stale rejects and a successful read must match
  the original context exactly. G2 is not substituted into G1's request.
  The test asserts actual generation2, one real source check, binding_stale, SDK
  failed, zero HTTP sends and zero retry sends. Owned coordination task is released
  and joined in finally. Existing stable G1/history/five routes were not rerun.

Independent fixed-delta review requested; prior whole-source status remains blocked
until that response. No native/model, shared slot released. Same installed H076/M617
launcher, exact test node `test_real_occurrence_source_checker_disclosure_generation_race`.

- `a7-r21/command.log` SHA256 `ca0b81fcd64efed5092beed7cdfa75e8804c10d77acc5dc1667c669711a09089`
- `a7-r21/resource.json` SHA256 `9f6f89e398a84b138aba12f0c6d9f9d1c2412961eae16385048a1cd41624bb61`
- `a7-r21/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
- `a7-r22/command.log` SHA256 `45aa9d6dd818a106e70ffd5db002bc65e619e9860b9e7f5be5e5d12f13ad9de6`
- `a7-r22/resource.json` SHA256 `ef4bb8a6c0a63a4ab7f78fb6bcd063f60ce92b98812375bba598b289ccc6f07f`
- `a7-r22/identity.json` SHA256 `6efc30ddb9838c238fa7dddd1ebcae3dd89b736033f5cefee4f11f63b9d2f7ba`
