# Host terminal audit consumer handoff

2026-09-05. Product implementation eaccab33c8bf29b85cd585a16192e31ac198c66e +
correctness fix 3e911c14fb29cab4d3906eb3f65c45bbf30ae499;
base ed5642bf27b4013672c31c7d0050a49d77863c96. Branch feat/host-operation-audit,
worktree simple_harness-host-operation-audit. Parent PLAN remains incomplete.

## Integration surface

- `backend/deskpet/operation_audit/composition.py`:
  `await compose_terminal_audit(state_path, *, stack_getter, subject,
  audit_path=None, start=True, page_size=128, poll_seconds=5)` returns one
  `TerminalAuditConsumer`. Default sidecar is `operation-audit.db`; never reuse state.db.
- Actual `main._activate_terminal_operation_audit()` starts and registers that lane
  during foreground composition. SQLite/filesystem initialization failure registers
  `terminal_operation_audit_status=storage_unavailable`; foreground stays available.
  Restart retries initialization; no business execution retry is requested by audit.
- `ForegroundRuntimeExecutionAuthority(..., terminal_audit_wake=consumer.wake)` invokes
  the optional synchronous wake after real durable terminal commit. Wake failure is
  non-authoritative; startup/periodic anti-join discovers missed terminal notifications.
- Shutdown awaits consumer close before SDK close. Await cancellation preserves the
  started attempt; SDK's read worker may finish independently without a Host page ACK.
- Trusted in-process diagnostics: `await consumer.store.coverage()` reports producer
  scope, job states, persistent source rejections and no usage/cost aggregation;
  `await consumer.store.inspect(job_id)` reads saved job/attempt/page/finding/support rows.
  These are not remotely authorized UI endpoints; inspect is full per-job diagnostic,
  not bounded pagination for external users. It must not be exposed directly to HUMAN/Agent.

## Required dependency and identity

Success path needs package-root RunOperationAuditPageV1 plus public
`ready.client.open_run_operation_audit` / `read_run_operation_audit_page`.
Tests pin Harness SOURCE fd4a78557ea72cd76875d3ed4ee02d99b4f03cd2 (public immutable
snapshot/pages with SDK-owned independent read-only connection offload). Source archived
locally into ignored `sdk-fd4a/src`; no Host thread touches the SDK writer connection.
Harness0.7.2 installed compatibility proves explicit capability_unavailable, not successful
new audit. Memory0.6.9 is installed in the independent test venv; no Memory carrier is
implemented here. No SDK source, versions, manifests or wheels changed by this leaf.

## Recovery, findings, limits

Started precedes public read; page, findings, support links, settlement and opaque cursor
commit in one fenced Host transaction. Expired worker cannot commit over a winner.
A crash before saved first page preserves unknown and superseded_by and opens under a new
snapshot_generation. After a page is selected, resume uses only that snapshot/cursor.
SDK missing/corrupt snapshot and inconsistent Host cursor/frontier become unavailable;
never silently select live data. Host resume checks frontier page checksum/header and page count. Before declaring
`enumerated`, it additionally streams all saved pages of that Run in the final transaction:
Host input checksum, preserved SDK page-hash field, continuous index, fixed snapshot/header,
per-page/total DTO counts and cursor boundaries must agree. One page is held in memory;
this is one final per-Run pass, not a full audit database scan on every iteration. Invalid
older pages stay preserved with job unavailable/journal_page_invalid, never live repaired.
This detects stored corruption against saved bindings; it does not claim defense against
an owner coherently rewriting all Host/SDK authorities.

Source rejection persists only fingerprint/owner hash/closed code/time. Identical bad input
is excluded so later valid sources progress; repaired facts have a different fingerprint.
Historical rejection remains in coverage diagnostics. No SDK-private SQL; Host reads only
its own terminal/admission/head and own audit sidecar. Return/page/tick sizes are bounded;
Host discovery/aggregate scans and initial SDK snapshot work are not claimed constant-cost.

Findings use actual owning operation + owner + rule/version + terminal job; source hash is
support, not identity. Public effect_id takes precedence over its provider correlation:
a tool effect is not the Provider attempt that requested it. Historical error observations
remain evidence; current unresolved/missing-usage rules apply only to head records.
Usage stays per-source reported metadata. No totals for calls, tokens, costs, durations or
retry attempts; processed_operations means public DTO rows. No absent usage/cost becomes 0.

`enumerated` only means all pages of that selected public snapshot persisted. Original
snapshot metadata/history gaps remain; legacy history cannot be backfilled by this consumer.
Capability/unavailable jobs retain their result and are not automatically reopened after
an SDK upgrade; a later explicit audit revision/recovery leaf must authorize re-evaluation.
Memory carrier/invocation correlation, Service boundaries, other producers, external audit
read API and complete per-operation coverage remain separate work. No native/paid Provider
or installed successor acceptance in this leaf. No program/full-audit gate receipt.

## Verification

Dirac scoped fixed ACCEPT on3e911c14; original nonlast-page corruption P1 independently
retested closed. See journal.md for exact commands, evidence hashes and scoped results. Tests run actual
Host foreground service/Runtime + SQLite + deterministic provider + public SDK client.
Main factory is executed from the exact checked-in function AST to avoid booting unrelated
products; actual Runtime terminal behavior is real. This is composition proof, not app boot.
A controlled public projection fixture repeats usage/error across head/settle/reconciliation
and separate pages; it is explicitly not claimed as a real SDK reconciliation event.

## Newly reported producer gap (not added to this leaf)

Main reproduced `late S1 → forget → late queue` under retained ignored basetemp.
`draft_lineage` raises `primary_context_dependencies_not_visible`; the old USER causes
zero new Provider calls. Main then queried durable rows: late turn/HostRun **CLAIMED**,
`sdk_run_id=NULL`, three HostRuns, only the original two succeeded Provider invocations.
This supersedes the earlier unverified QUEUED inference. Trace:
`_drive_once:645 → _drive_claimed:697 → PrimaryContext.prepare:112`.
Main-supplied evidence (not independently rerun by this leaf):
`.local-test-evidence/2026-09-05/primary-candidate/late-enqueue-state-01/retained-queue-summary.json`,
SHA-256 `e4f9629435cc210941fb1aebb857d9e245eacab739e326b5c55578d8b68abddf`.

A preRun preparation denied/blocked operation requires its own durable Host producer:
terminal discovery cannot prove its absence simply because no SDK Run was created.
Main/Dirac are implementing narrow typed pre-SDK rejection → real HostRun FAILED /
turn SETTLED with an actual transition proof, without fabricated SDK terminal/closure/
outbox or DDL. That repair and producer remain separate from this terminal leaf.
