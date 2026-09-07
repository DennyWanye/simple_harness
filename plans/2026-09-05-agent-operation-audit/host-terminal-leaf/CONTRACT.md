# Host terminal Run audit leaf

2026-09-05. plan-status: finalized (scope authorized in current user request).
Base ed5642bf27b4013672c31c7d0050a49d77863c96. Parent ../PLAN.md remains authority.
Execution: one owner, sequential contract → vertical implementation → focused proof/review.

## Scope and preservation

Terminal foreground Runs are discovered from real Host terminal receipts and immutable
admission/Run binding. A dedicated Host-owned audit database stores tasks, started/settled
reader attempts, immutable safe pages and deterministic findings. It is derived audit
state, never a new business execution ledger. No modification to existing business
receipts, Host schema version, SDK/wheel/pin, authorization or execution defaults.
The finished consumer starts ON from actual main composition and wakes after durable
foreground terminal. Memory invocation/carrier and other Run producers are next leaves;
this is not full operation coverage, installed-successor or native acceptance.

## Durable discovery without coupling business commits

The main factory owns one audit.db alongside state.db. A bounded-return Host-owned
read-only query finds terminal receipts absent from that audit database (both are Host
stores; no SDK SQL). New terminal callback only wakes the lane; startup and periodic
anti-join discovery recover commit-before-notification crashes without timestamp/highwater
assumptions or full in-memory inventories. Each source validates terminal receipt hash,
column/JSON identity and actual admission/subject/SDK binding. Database owners are trusted;
no claim to detect coordinated rewriting of all authorities.
No new cross-database write transaction or added failure condition on Host terminal commit.
Discovery/result sizes are bounded; underlying Host index scans are not claimed O(limit).

## Frozen public pagination and attempts

Only use public ready.client.open_run_operation_audit(RunId,page_size=...) and
read_run_operation_audit_page(RunId,cursor=...). Missing capabilities produce explicit
coverage unavailable, not empty/success. Old bounded reader is not a fallback.
Persist started before each SDK read. The first successful page transaction stores
snapshot hash, opaque cursor, page index/hash/header and findings together. Subsequent
reads require that same Run/snapshot/header and next index. Store never parses/modifies
opaque cursors. A missing/corrupt snapshot stays unavailable: never open a replacement
and append it into the old job. An interrupted open without a persisted first page
retains its unknown attempt with abandoned_unsaved_read; a new explicit audit generation
may open again, linking superseded_by. This is a repeatable read/derived snapshot, not
a business effect; no assertion that the prior open never executed. No old page can
be mixed because none was selected. User challenged and approved this recovery refinement
before tests on2026-09-05. Interrupted later reads resume
from the persisted cursor; repeated read is safe and never resends execution/effects.

Each actual reader invocation has a new attempt, journal settlement and page/cursor/findings
commit atomically. A single bounded lease/CAS fences concurrent consumers. Expired worker
cannot overwrite a winner. No Host database transaction stays open across SDK awaits.
A crash after page commit and before notification finds the already advanced cursor.
A stopped lane preserves unfinished journal facts; shutdown is bounded.

## Findings and privacy

Rules are deterministic and versioned. Finding identity binds task/rule version/real
SDK owning operation ID (explicit provider_invocation_id/effect_id relation where supplied);
source hash is supporting evidence, not part of finding identity. Multiple projections/pages
retain their source links but insert one finding per owner operation/rule version. Current
unresolved/usage rules inspect heads only; historical transition is not current state. Owner component is
a closed Host mapping (Harness Provider/effect/etc.), not an untrusted label. Findings
are observations/proposed investigation, never self-authorized code changes.
Only public safe SDK operation DTO metadata is retained, plus trusted opaque Host/SDK
Run refs and hashes. Never store request payloads, raw exception strings, authorization
material, audio or reasoning. No LLM calls for auditing. Usage remains reported optional
values per source; no physical call count, token total or cost aggregate is produced. SDK
operation DTO enumeration counts are not physical attempts. Unknown attribution stays unknown.
Priced cost remains null without actual price provenance, never zero by default.
Coverage metadata is retained verbatim from validated public DTOs; enumeration finished
is not full historical/producer coverage. Audit reader calls do not audit themselves.

Malformed source rows are quarantined by a Host-only fingerprint of their complete selected
binding facts and a closed code. The anti-join skips the identical rejected input so a bad
prefix cannot starve valid successors. Changed facts are eligible for revalidation; old
rejections remain visible. Rejection is coverage unavailable, never successful enumeration.
