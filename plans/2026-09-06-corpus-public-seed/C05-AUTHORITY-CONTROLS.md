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
