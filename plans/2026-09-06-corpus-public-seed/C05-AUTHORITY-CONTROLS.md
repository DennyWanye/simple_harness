# C05 approval and evidence controls — prepared, NOT_RUN

2026-09-07. Main owns execution and complete current H0710/M619/S0313 installed target.
No SDK overlay, model, HTTP server, native, new environment or resource process was started.
This source does not repeat the five already-tested runtime/codec cases or original setup greens.

Exact selectors (run together once under main's default bounded shared runner):

- `backend/tests/quality/test_corpus_c05_authority.py::test_actual_pending_approval_rejects_foreign_turn_and_wrong_args_before_allow`
- `backend/tests/quality/test_corpus_c05_authority.py::test_real_empty_search_differs_from_missing_effect_and_terminal_result`

First selector uses actual Host queue/runtime + installed SDK + production PreparedAuthorizationRuntime,
authorization saga and verified companion connection. A deterministic setup Provider records its actual
raw call before the SDK creates an internal call/decision. The test asserts those IDs differ. A foreign
queued turn cannot borrow the actual pending; a changed, never-emitted argument cannot authorize it.
Both leave original public decision unchanged and produce zero approval attempts. Restoring the original
recorded call allows that exact pending and logs both namespaces. No invented decision, allallow or
permission relaxation. This uses the public SDK composition fixture, not full main HTTP/dispatcher proof;
Hegel/main's actual-main batch remains separate. Its request hash is explicitly the fixture Provider DTO
hash, not an observed HTTP request.

Second selector obtains a real empty `task_scope_search` result through actual production handler and
checks the Host effect index is nonempty. Only then unavailable-reader negatives return no effect or
hide the actual terminal result. Both must raise different stable errors; the real empty list remains an
empty event tuple. Original public fact is still unchanged afterward. No SDK-private SQL or corrupted
business rows. These negative adapters model evidence unavailability, not actual storage corruption.

While preparing these controls, source call inspection found WorkspaceBindingAuthorityStore has no
`verify_route_receipt` method. Scoped approval now reads existing `exact_receipt` using the immutable
Host route's exact scope/revision/receipt ID/hash and retains `verify_effect_authority` root checking.
No new port, ledger, fake receipt or live-head substitution. This fix still needs the main actual scoped
marker approval path; the first selector proves the initial create pending boundary only.

The existing candidate effect ID/Run/tool checks were moved before the nonterminal branch, as Dirac
suggested. No added test matrix for that mechanical order change. Syntax-only checks are not test PASS.

## Pending first-run failure and public predecessor proof

Main r1: 1PASS/1FAIL1.89s, PG83853 exit1/remaining=[]; actual empty/missing reader control
passed and must not be repeated. Original log remains main
`.local-test-evidence/2026-09-07/corpus-c05-authority/r1/command.log`.
The pending decision genuinely exists but REQUIRE_USER is raised before SDK prepare_effect.
The failed expectation that every pending already has an EffectRecord is withdrawn.

Successor `verify_pending_call(*, stack, ingress, sdk_run_id, decision_id, request)` returns
`PendingCallProof`, never an invented EffectRecord. It reads the existing Host public trace facade,
which reads SDK public provider records and operation audit. Unique requested/waiting boundary facts
must agree on Run snapshot, opaque effect/internal-call references, name, raw-call domain hash and
actual turn/call ordinals. Their immutable sequence order is checked. The matching proposal must bind
the exact successful public provider invocation/response hash/request; the actual decoded response's
call ordinal supplies full raw ID/name/arguments. SDK public audit_reference/audit_hash functions verify
the opaque domains; no internal ID derivation or database writes.

H0710's boundary DTO has request_hash=None despite that field being in its canonical producer payload.
Dirac selected direct equality between two real public sources: the OPEN decision's full arguments and
the actual Provider response's full arguments. Audit proves the unique requested/waiting call association.
The missing audit request hash is explicitly **not_exported**, not a validated intent hash. We do not
reconstruct a private producer payload, fill missing DTO fields or write candidate evidence. The original
decision request's own hash is logged as its fingerprint only.
Finally the actual decision must still equal its original open version/request/nonce before returning;
existing respond_primary_decision remains the authority for any later race.

The original pending selector now explicitly asserts actual read_effect is None, then checks the public
predecessor proof has different raw/internal IDs before exercising the same foreign-turn/wrong-args
negatives and exact allow. Only this red selector needs re-execution on main's fixed current target.
The actual-main consumer must also use this one helper; no settled-effect fallback or tool-name-only grant.
