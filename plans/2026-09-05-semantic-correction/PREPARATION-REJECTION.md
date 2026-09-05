# Pre-SDK preparation rejection and queue progress

2026-09-05. Oracle before implementation. Actual late-enqueue counterexample is
FAIL: current USER correctly denied, but real Host Run remains CLAIMED with no
SDK binding; the driver raises and later work cannot progress. Two earlier Runs
and their two Provider calls are unchanged. This is not a pre-claim QUEUED state.

Retained local Host-state summary:
`.local-test-evidence/2026-09-05/primary-candidate/late-enqueue-state-01/retained-queue-summary.json`,
SHA256 e4f9629435cc210941fb1aebb857d9e245eacab739e326b5c55578d8b68abddf.
The actual trace is _drive_once → _drive_claimed → PrimaryContext.prepare.

Implement a typed rejection only for a validated public Memory snapshot that
specifically denies this Run's actual current USER source. Historical-group drift,
missing policy, infrastructure errors and generic exceptions cannot mint this
rejection. Preserve the snapshot metadata/hash, exact source binding and closed
reason; no raw USER content in the rejection receipt.

Use the existing real Host admission. In one ingress/owner/generation fenced
transaction, require CLAIMED, no SDK binding, no start intent, and exact candidate
identity. Append a versioned preparation-rejection variant to the existing
append-only Run transition ledger, change Host Run to FAILED, settle its turn,
and close the lease. Existing schema supports these states. Do not create a fake
SDK Run/terminal, context, closure, analysis job or terminal receipt. Exact retry
returns the same immutable rejection receipt. Once SDK start may have happened,
this path must reject and leave existing reconciliation semantics intact.

Decisive checks: old late USER adds no Provider/SDK execution; actual Host Run
FAILED and turn SETTLED; subsequent independent USER actually executes once;
same-key retry/reopen does not repeat rejection/effects; crash before/after commit,
stale lease and already-started cases preserve correct state; unrelated/transient
errors remain distinguishable; original S1 archives and earlier failed evidence
remain intact. Independent review is required before this is called complete.

## Implemented and independently reviewed successor

The typed rejection is now wired through the actual runtime and read projection.
The existing append-only transition contains a nested v1 rejection and its public
snapshot metadata. Both settlement and subsequent reads recompute the current
USER binding from actual Host S1; a self-consistent snapshot from another USER
cannot settle this Run. The runtime also checks all seven claimed identity fields
before calling the Store. Exact retry validates the original final state and
source again. No DDL or SDK terminal/outbox row is added.

The independent real two-source counterexample initially accepted a denial for
an unrelated new message. It now raises preparation_rejection_source_binding_mismatch,
leaving all current rows unchanged; replay of the actual old rejection still works.
The actual late-enqueue runtime counterexample now settles its Host Run as FAILED,
allows the following independent Run, and filters the old source on page/detail/
reopen/provider preparation. Only a newly admitted atomic source can establish new
post-cut input. Old v1 actions with no verified cut remain explicitly unverifiable.

Validation: source overlay queue/runtime/API combination56PASS23.07s; final nine
rejection controls plus six actual runtime API cases15PASS13.68s after identity
strengthening. Counts overlap. The final installed0.6.10 combination is86PASS42.87s
including startup identity, composition, semantic correction, typed barrier,
cognitive actions and source authority. Provider adapters are deterministic;
these are not native or live-provider evidence. Independent source and installed
artifact reviews returned scoped ACCEPT. Historical native FAIL remains recorded.

Commands use the dedicated candidate Python: `-m pytest` over
`backend/tests/execution/test_preparation_rejection.py`, `test_foreground_queue.py`,
`test_foreground_runtime.py`; `backend/tests/memory/test_primary_runtime_api_integration.py`,
`test_history_source_authority.py`, `test_semantic_correction.py`,
`test_cognitive_typed_barrier.py`, `test_primary_cognitive_controls.py`; and
`backend/tests/sdk_adapters/test_composition.py`, `test_sdk_candidate.py`.
The86 installed combination omits queue/runtime unit suites already covered above;
no PYTHONPATH overlay was used for that installed run.

Ignored local evidence at `.local-test-evidence/2026-09-05/primary-candidate/`:

- preparation-rejection-combined-overlay.log: bc91a51fae12f7148374c00a3b26c32b706fff98334bfb8c55bdf27e2e207ac1

- preparation-rejection-final-overlay.log: e8d56e758242050260097ac553a42a47fd8e3ec7cb6c4cda54b21ed025ac566a

- sdk0610-installed-combined.log: 0b62f1e7d1693c2408fe07b5336e4b370d816db312fdf47176533075593253b3
