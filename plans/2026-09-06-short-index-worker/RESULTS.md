# Short worker bounded delivery

2026-09-06 · isolated feat/short-index-worker · base fe006f59. No main or SDK
artifact edits. Fixed production source
`426db3bba60cc6287915c4add0f1aa55b82c3b6e` received Dirac scoped ACCEPT, no new
P0/P1. The later commit adds only tests and documentation; production bytes stay
at that reviewed source. This is not complete S3/S6/program acceptance.

Current production code adds short step by default to the existing unique
MemoryAnalysisLane, after outbox and before analysis. Whole Host turn identities
are keyset-paged with a fixed upper per sweep,16 maximum; whole-group proofs are
unchanged. Only public registration ACKs followed by projection success enter a
256-entry memory-only confirmation cache. Namespace is the authority's verified
primary/epoch pin; replacing the manager resets cursor/cache/maintenance state.
Low seq pending/invalid groups advance the scan position but never successful
confirmation; sweep wrap revisits them. No new DDL, ledger or durable watermark.

Cancellation propagates. A local short error is recorded separately and does not
prevent analysis; an analysis failure does not disable the next short step.
The existing lane start/close owns the only background task; main closes v7 after
foreground/audit/Runtime borrowers stop, separately from the legacy SessionDB owner.

## Tests so far (separate batches)

- First: **1 PASS,1 FAIL/4.52s**, PID14724 exit1, peak164784KiB. Failure was the new
  late-delivery fixture omitting required `approved_fresh_lane`; exact log retained.
- Corrected worker batch: **8 PASS/6.20s**, PID14762 exit0, peak154320KiB. Real11
  groups through lane/public SDK, low-seq claim held while ten newer outboxes
  delivered, fixed upper with a newly enqueued incomplete turn, real reclaim and
  wrap, four lost-ACK points/reopen, bounded cache/eviction, cancellation/single task.
- Added controls: **3 PASS/4.81s**, PID14811 exit0, peak142032KiB. Actual disposable
  Host child deletion → first group rejected/ten later registered; analysis failure
  followed by another successful short step, maintenance/reopen, original missing
  child negative. `-k` deselected26 unrelated cases; they were NOT executed here.
- Final necessary neighbors: **6 PASS/9.30s**, PID15300 exit0, peak363920KiB.
  Real tool four-message group plus unfinished turn rejected while the later
  ordinary group registers; actual public projection ACK followed by cancellation
  leaves no false in-memory confirmation, and real reopen replays the same index.
  Actual durable analysis controls and main activation also pass; main's lane
  shares the production runtime/authority by default. Counts above are separate
  scoped batches, not an additive quality score.

Indexing tests use real foreground/outbox/source/public Memory stores. Their
IdleAnalysis control isolates scheduler behavior and does not prove analysis job
completion; final adjacent actual durable analysis tests provide that separate
evidence. The close control pauses a replacement step and proves lane task cleanup;
the additional real projection control cancels AFTER its ACK, not in the middle
of SQLite commit. No real
model/native/physical Provider request or independent installed identity is claimed.
Borrowed interpreter is main combination's
`.local-test-evidence/2026-09-05/primary-candidate/venv/bin/python`.
Every run was supervised at180s/2GiB. Slot was released after PID14811 for main
WebKit, then explicitly reacquired for the last batch. After PID15300 exited,
the slot was released directly to Carver. No test processes remain from this leaf.

Rerun serially from this worktree root with the borrowed interpreter as `$PY`,
only when the coordinator assigns the resource slot:

```sh
$PY -m pytest backend/tests/memory/test_short_index_worker.py -q
$PY -m pytest backend/tests/memory/test_memory_display_producers.py backend/tests/memory/test_primary_short_ingestion.py::test_marked_missing_child_replay_rejects_without_repair -q
```

Original split: controls ran the first8 worker cases; adjacent selected
`-k 'missing_child or analysis_failure'` across worker/display/ingestion files;
final ran the worker tool/unfinished and post-projection cancellation cases plus
the whole display-producers file. Original watchdog script/logs/process receipts
remain ignored; no source-wide/full suite rerun.

Ignored evidence `.local-test-evidence/2026-09-06/short-index-worker/`:

| Log | SHA256 |
|---|---|
| first.log | 53e043b9503f7d2a30dc8f0180fb87d91c70c798715360d623fa5a10c84aaf67 |
| controls.log | 5b6be7feccc0da99ba3a2900914afc31bb3522cdfd3f19f034f86e07b1f06a97 |
| adjacent.log | dc3e42f93a347398d3881e23cb447b07261810a72a9fa6fde7fed5917f0fd34f |
| final.log | 8fed2b272e47567c65ce5ac44ac1e9140f3acfca3226f8a0bc3a301ecbd00f8d |
| final.process.json | 2f80c9dc75a7025371261d0e4a5e3a03fa3764ab1be60c004faaa2f33a43ab1c |

## Cost and remaining boundary

Memory0612 `sqlite_v5.py:1973–2165` fetches all subject registrations, groups/sorts
them, checks suppression and existing chunks, then writes a full projection audit.
It has no public incremental continuation. Page16 limits Host registrations, NOT
total SDK work, RAM, CPU or P99. Async timeouts are not preemptive compute limits.
The public incremental SDK projection gap remains open; this leaf does not alter
SDK bytes or original quality/retention thresholds. The Host keyset core query used
an existing index on the actual11group database; large history/join-cost evidence
has not been collected.

`confirmed` means public registration plus projection ACK, not retained chunk or
recall hit. Per-operation5s cancellation and next-page fairness do not make an
uninterruptible SDK compute segment bounded. Periodic projection maintenance
defaults to60s; suppression/final-read safety still uses current public gates.
There is no separate persistent retry/quarantine ledger. Local bad groups are
revisited each finite cycle; projection/global failures are reported to the lane
and retried through its existing polling/job cadence, not a new backoff authority.

New model short request protocol and multi-message/tool source production remain
out of scope. The two-message restriction stays fail closed; selected-hit source
filtering/final fresh policy remain mandatory. No complete S3/S6/program claim.
