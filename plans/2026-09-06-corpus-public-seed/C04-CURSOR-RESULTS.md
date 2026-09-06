# C04 setup and same-timestamp cursor recovery

Last updated: 2026-09-07. Product d3a580be; test helper fix d25fe2f6.
Fixed source reviewed by Dirac with no definite P1 before execution; final evidence review pending.

C04 original20 setup: b380f999 first batch19PASS/1FAIL (8.31s); only C04-12 rerun after product fix and passed. Not one same-source full20 run. No threshold/input/gold changes. All setup authority is synthetic fixture/public SDK, not model quality. 240 quality executions remain0.

Public probe of copies of the original failing databases confirmed r2 registration065a… before old registration31c… and cursor invalidationbd47… at timestamp1788660000. Both old entries were HostACKed but SDK outbox remainedpending. New r2 was missing in Host. Original DB bytes unchanged; no signal/worker/provider calls.

The consumer now revisits the inclusive current timestamp boundary using a finite paging budget. Scan continuation survives successive calls within a pass; EOF opens the next boundary pass. Existing indexed source-key maximum is separate from the original latest-consumption cursor CAS. Explicit same-timestamp admission appends the original sequence/hash format, preserving accepted-registration pagination and terminal/cursor joins. No DDL, old receipt/hash rewrite, clock increment, or full-history polling.

Limit: after the high water advances from T to T2, subsequent backfill at T remains outside this guarantee. Public outbox has no append sequence or Host-unACKed filter. This is not arbitrary late-arrival completeness.

## New execution

- probe-r1: exit0, PG49582,0.65s,117296KiB,remaining[],cleanupnull. Public M618 read on copied databases.
- fix-r1 (d3a580be):3PASS/2FAIL,2.90s, PG50570exit1,3.45s resource,189360KiB,remaining[]. Actual C04-12, boundary paging/reopen/exact-source rejection and stale-CAS passed. Two terminal controls failed before business execution because the old helper proxied missing constructor options to an uninitialized manager.
- fix-r2 (d25fe2f6): only those two failures,2PASS/2.51s, PG50677exit0,3.008s resource,190736KiB,remaining[],cleanupnull. Constructor defaults supplied in the new test only; product unchanged. Five unique new controls passed across batches; three green controls not repeated.

Terminal controls use a real public legacy7.2 catalog initialization, public mutation/timer, public7.3 upgrade and actual Manager settlement receipt. The higher Host cursor row is explicitly scripted: tests establish store atomicity/reopen/exact replay and zero additional signal calls, not full consumer terminal traversal or historical binary M616 execution. Actual C04-12 uses the public SDK registration/invalidation chain and production source/consumer, preserving r1 and producing ACKed r2; old timer grant is absent.

Installed target: primary-candidate/.local-test-evidence/2026-09-06/primary-078618/installed (H078/M618); no SDK overlay, native, model, or real Provider. Existing primary-m0615 venv interpreter with isolated launcher run_batch.py. Default run_resource_bounded.py shared lock,2048MiB/180s, unchanged disk controls.

Command selectors: fix-r1 uses tests/memory/test_s5c_same_timestamp_cursor.py plus tests/quality/test_corpus_c04_prepare.py::test_c04_actual_public_time_setup[C04-12]. fix-r2 selects only test_real_terminal_receipt_late_key_atomic_reopen. Original19/old terminal baseline not rerun.

## Local raw evidence

All paths below are ignored beneath this worktree; no raw added to Git.

- `.local-test-evidence/2026-09-07/c04-cursor/probe-r1/command.log` SHA256 `ff14cd0cd2dd87932cc87d3c909cdfecc85d7699b99d0b6a656e1587554bd943`
- `.local-test-evidence/2026-09-07/c04-cursor/probe-r1/resource.json` SHA256 `5f63d71cb5abf9a0599c2973155dcad2b9bfcc4f7a0bf72ab70fe50e7e5e30e6`
- `.local-test-evidence/2026-09-07/c04-cursor/fix-r1/command.log` SHA256 `97ca423d48264cd97b261915a80f1961bec4e1159c775ff59963b0ea77ca1ee6`
- `.local-test-evidence/2026-09-07/c04-cursor/fix-r1/resource.json` SHA256 `64bed45e9f5f1297515ba20409010193efd2cb6476e61bfafa21a73b18086704`
- `.local-test-evidence/2026-09-07/c04-cursor/fix-r2/command.log` SHA256 `6e15604428c2efc9e2f5689f4892acc27be3ff21bf44715637d907fb4e0a9e90`
- `.local-test-evidence/2026-09-07/c04-cursor/fix-r2/resource.json` SHA256 `49d5b5e1848540103e9a1d2b7a20617b110ac5dd80c41661aeb455d8baf31c60`
