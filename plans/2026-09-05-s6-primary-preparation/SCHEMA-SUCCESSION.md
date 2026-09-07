# Host schema succession decision

2026-09-05. Main implementation decision under approved execution-order revision.

Current production state.db isv46. The paused S5c candidate e2702006/6c8ebddd has an
unpublished prospective-actionsv47 migration and initializer46→47. Original A11 records that
candidate/version; its historical contract/results remain immutable and are not current proof.

Primary unscoped search/page-in needs a durable exact-effect source index before the next
Provider request and before terminal dependency recording. Independent fixed8e896472 review
proved a leak when relying on TaskScope-only reservations: a search before any route has no
reservation, and late public source suppression was not checked for the second request.

Chosen ordering: Primary index owns the next linear migration46→47. S5c must be deliberately
rebased/re-numbered47→48 when integrated later; it must not auto-enable as a hidden dependency
of Primary. Do not create a direct46→48 jump, reuse conflicting migration filenames, or silently
change schema without the normal migration discipline. Old S5c initializer must never run on
newv47 assuming its own tables exist.

Main owns the later S5c migration/initializer adaptation and a versioned A11 mapping retaining
original behavior requirements: fresh startup, real upgrade, rollback/downgrade rejection,
reopen/idempotence and no duplicate presentation. New receipts must bind the actual candidate
and revised implementation mapping; oldv47 S5c evidence does not make either new migration
PASS. No active gate ledger, original AC, or old raw evidence is rewritten by this decision.

Immediate Primary migration owner: Carver. Required focused checks: real46 upgrade with
existing facts unchanged; exact new effect/source index, transaction rollback, idempotent
reopen, unsupported future version rejected before writes. S5c follow-up remains OPEN.
