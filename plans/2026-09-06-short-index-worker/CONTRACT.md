# Short indexing in the existing production lane

2026-09-06 · base fe006f59 · no schema, ledger, SDK or model-protocol change.

Oracle: original complete-group source contract, recent-ten exclusion, five-day
retention and selected-source final checks remain unchanged. Outbox delivered is
required for original USER lineage; analysis materialization/APPLIED is not.

One MemoryAnalysisLane runs outbox → short step → analysis. Short-local rejection
does not prevent analysis. Production composes it by default with the same runtime
and conversation authority. No second task/manager, no durable success watermark.

Scan all Host turn identities with keyset pagination (16 turns maximum per step),
fixed upper sequence per cycle; do not filter pending/unfinished out of the cursor.
Wrap at the upper bound and poll before rescanning. Low-sequence late-delivered or
late-terminal groups must be revisited even while new turns arrive. Whole-group
validation uses existing exact two-message producer proof. Invalid local groups
advance scan position but never get a success entry; global namespace/DB failures
are unavailable, not successful groups. Registration writes do not hold Host read TX.

Only actual public USER ingest/source-only admission/registration ACKs followed by
successful public projection permit bounded in-memory confirmation, keyed by exact
group registration refs under the pinned namespace and current manager generation.
Reopen clears optimization state. Partial/unknown commits replay exact refs. Cache
eviction only adds work. Cancellation propagates; shutdown waits for lane termination
before closing the shared runtime after other production borrowers stop.

Cost inspection before implementation: frozen Memory0612 sqlite_v5.py:1973–2165
rebuild prepares history-source context, fetches ALL subject registrations joined to
evidence, validates/groups/sorts in Python, checks candidate suppressions, then fetches
existing chunks and calculates manifests. It has no LIMIT/continuation API. Even
unchanged projection writes an audit receipt. Host paging is NOT an end-to-end cost
bound or a P99 claim. Rebuild at most once after a page's writes, with periodic expiry
maintenance and timeout/backoff; local timeout is not a preemptive SQLite/CPU bound.
A public incremental projection successor remains separate; frozen0612 is untouched.
Host existing UNIQUE(subject,enqueue_sequence)/FIFO index and UNIQUE(run.turn_id)
support keyset lookup; verify EXPLAIN on a real test database, no new index/DDL.

Decisive tests, before any completion claim:

- Real11 completed groups + outbox → production lane tick creates eligible early
  hit, recent10 excluded; no direct fixture reconcile used to produce that index.
- Low seq undelivered on first pass, higher groups delivered; wrap then actual old
  delivery must index. Fixed upper ignores arrivals until next cycle.
- Bad/missing child, unfinished, multi-message tool groups cannot partially index
  or starve next legal group. Global namespace failure still lets analysis run.
- Failure after admission/registration and before/after projection ACK: no premature
  cache confirmation; real store reopen/replay preserves refs and USER lineage.
- Cache bounded/eviction/reopen, one start task, cancellation/close, periodic upkeep,
  and analysis failure do not disable subsequent short steps.

Use only serialized deterministic tests under coordinator slot, 180s/2GiB watchdog;
no model/native. Source/ARCH/results and independent review remain required.
