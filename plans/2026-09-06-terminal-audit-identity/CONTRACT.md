# Host exact SDK terminal audit binding

2026-09-06. Approved parent operation-audit scope; base3dd76242. This leaf
consumes the successor SDK public terminal proof; no SDK private SQL or new grant.

Current Host terminal audit validates its own receipt and the SDK Run/state, but
does not compare the SDK terminal event/payload. New public metadata makes that
comparison possible. Reuse read_primary_terminal_identity_tx under one read-only
Host transaction: it validates the actual primary/scoped observation and unwraps
the raw SDK event identity. Host envelope hash is never the SDK payload hash.
The public RunTerminalAuditEvidenceV1 must match this exact event/payload/state,
bound SDK Run and the audited Host receipt before any page is persisted.

Missing/mismatched proof is unavailable, never enumerated. Actual disconnection,
source corruption and slow-reader ownership rules remain intact. RULE_VERSION
advances to terminal-run-v2 so old v1 unavailable/enumerated jobs remain history
and cannot silently satisfy the stronger rule. No existing jobs/pages are rewritten.

Decisive installed oracle: actual Host runtime completion with committed-turn
terminal metadata, real public SDK pages, exact Host identity positive; wrong raw
event, wrong payload, Host envelope hash and foreign Run negative; close/reopen
preserves proof and does not dispatch business again. Previous partial pages and
v1 jobs remain intact. The fixed successor wheel is installed in this isolated leaf; source
implementation alone is not an installed or native acceptance claim.


## Implemented installed boundary — 2026-09-06

This leaf pins exact H073 locally. The shared Host raw SDK normalization remains
unchanged, including legacy scoped ExecutionEvidence unwrapping. Public typed
matches(event_id, payload_hash, state) is checked for every accepted page after
slow-read Host source revalidation; a wrong namespace or missing proof is unavailable.
No private SDK SQL, new authority or business redispatch is introduced.

The non-null oracle uses actual ForegroundConversationEntrypoint + public MemoryManager
with real SQLite. Public StartSnapshot supplies the accepted root turn ID;
MemoryOutboxRepository.read().committed_turn() validates its actual bytes/hash and
public memory.outbox.created binds the same hash. No claim/dispatch is made to observe it.
SDK terminal metadata does not expose raw JSON: the real outbox and immutable created
receipt prove the non-null producer branch under SDK's same-transaction contract;
Host full raw terminal payload identity is checked independently. Pending memory outbox
is not a claim of physically applied Memory mutation. The completed runtime, saved page
prefix, exact proof and unchanged outbox survive close/reopen with Provider count1.

Faults replace the typed public reader result (wrong payload/record/envelope namespace,
ref/state/missing proof), or change the source resolver to another real Host DB during
an in-flight read. Original source evidence and guards remain intact. Legacy positives
use the actual scoped observer capability without the new public messages reader;
no evidence is deleted/restamped to impersonate an old terminal. Old v1 pending,
unavailable, partial and enumerated jobs/attempts/pages remain unchanged when v2 runs.

H073 separately records Provider failure and its failed driver interval. Findings
must deduplicate provider head/transition sources within the actual Provider operation,
while retaining the different driver operation. Two error findings are not two calls.
See RESULTS.md for exact installation, selective tests, original failures and resource
bounds. No native/full-suite or whole-program every-operation completion claim.
