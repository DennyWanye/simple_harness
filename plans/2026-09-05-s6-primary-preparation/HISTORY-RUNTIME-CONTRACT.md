# Primary history current-use contract — 2026-09-05

> Current source-index contract (2026-09-05, runtime `e31c6efd`):
> [SOURCE-MIGRATION-CONTRACT](../2026-09-05-primary-effect-sources/SOURCE-MIGRATION-CONTRACT.md)
> supersedes the earlier reservation-only search/page-in enumeration and pre-fix
> completion statements below. Global v47 records exact SDK effect identities at
> real primary handler entry, including unscoped calls; public `read_effect`
> verifies results, and producer prefixes use the exact indexed sequence.
> `harness_evidence_reservations` remains scoped evidence/watermark state, not the
> complete dependency index. The 8e896472 independent late-forget P1 was reproduced
> and fixed; independent review of e31c6efd is required before integration.
> Earlier investigation and test results below are retained as historical stages.


Authorized continuation of EXECUTION-REVISION-2026-09-05, AC1/AC7. Host base
87c42b43 in an isolated worktree; original program thresholds and raw archives stay
intact. SDK source a96a5008 exposes current batch visibility; installed candidate
verification remains separate. No main cutover or program completion is implied.

1. UI and the foreground history reader share a Host-owned policy adapter. It
   verifies original Host S1 envelope/receipt hashes, subject and primary, gathers
   complete evidence/typed-recall dependencies, and calls the public SDK batch
   endpoint. All dependencies must be visible. It never reads SDK private SQL.
   Cold original USER evidence remains eligible before asynchronous analysis.
2. Primary context preparation filters complete historical causal groups before
   budgeting. A new immutable start metadata field binds the exact evidence refs
   and hashes actually retained. Draft/claim/prepare lineage changes retain their
   existing rejection semantics. No unrelated historical turn or fake execution
   identity may stand in for a missing source.
3. New terminal observation payloads carry `visibility_dependencies` with
   `schema_version: 1`, `evidence: [{evidence_id,envelope_hash}]`, and
   `recall: [{result_id,result_hash,item_id,item_hash}]`. Include the real current
   USER source and every inherited causal group. Typed recall dependencies come
   from real SDK tool results anchored by the Host's exact context-route receipt,
   not assistant claims or arbitrary text matching. These are source bindings,
   not another permission grant. Existing observations are never rewritten.
4. A pre-contract assistant/tool group without complete dependency proof fails
   closed for ordinary reuse/display; its original USER source is independently
   eligible. The original append-only archives remain available to their existing
   audit authority. This isolated candidate is not a migration of production data.
5. Recheck complete retained dependencies after slow reads and at each physical
   Provider adapter entry. A late denial prevents an outbound call using stale
   frozen content; it does not mutate an already hashed request or replay a prior
   allow receipt. Current actual request disclosure is used. A completed check is
   an observation, not a lock held over remote execution; already-sent requests
   cannot be recalled. Caller failure must be classified as definitely not sent.
6. The separate short-horizon API currently lacks a public exact history binding.
   Unsupported source carriers must remain explicitly unverifiable; never invent
   a typed result, silently drop a dependency, or infer a public binding from SDK
   private tables. Full memory-loop acceptance remains pending that source gap.

Decisive acceptance: real Host/Harness/Memory SQLite original USER -> derived
memory -> MEMORY-only suppression removes the original source and dependent
assistant from the public page/detail and the next Provider context; unrelated
dialogue survives; cold USER before analysis still renders; late suppression
after preparation prevents physical Provider entry; wrong hash/cross-owner and
missing dependencies fail closed; normal reopen retains source identities with
no replay. Test source layers, installed wheels and native UI separately. Raw
evidence remains ignored; update ARCHITECTURE with only the verified boundaries.

## Runtime concrete interface proposal — 2026-09-05, base 284ea40b

For coordinator/Popper review before implementation:

- `FrozenContextAuthority.visibility_dependencies` is optional immutable JSON.
  Primary preparation supplies strict v1 proof; absent means unverifiable, not an
  empty allow. `start_payload.context_metadata.visibility_dependencies` stores
  the same proof inside the existing hashed start request. Evidence roots are
  the exact admitted USER id/hash and each retained historical group source
  id/hash after whole-group filtering and budgeting. Parent terminal roots keep
  their recursive closure; no copying unrelated historical sources.
- `PrimaryHistoryStore` accepts the shared `PrimaryHistoryPolicy`. Ordinary reads
  check the actual terminal source. A missing/denied complete terminal proof
  cannot release assistant/tool content; the original USER is checked separately
  and remains eligible, including cold evidence before analysis. Corrupt source
  identity remains corrupt rather than a legacy bypass. Recheck after public
  settled SDK reads; the final preparation check covers only retained content.
- Popper's `check_evidence_ids(db, primary_ref, evidence_ids, disclosure_context)`
  handles roots already in Host storage. Runtime additionally needs
  `check_dependencies(db, primary_ref, dependencies, disclosure_context)->bool`
  on the same policy: pending Run recall has no terminal observation yet. It
  must merge evidence closures and actual recall four-tuples in one public batch,
  validate expected hashes, reject incomplete/oversized proofs, and never mint a
  fake observation. This interface request was sent directly to Popper.
- Stack provides a public-source dependency reader using public start snapshot,
  current Context and tool invocation facts. For memory_standalone results, only
  real typed `result_id/result_hash/item_id/item_hash` accompanied by exact Host
  accepted route receipt (same SDK Run/call/effect/recall refs) may propagate.
  The projection function must preserve these DTO identities in addition to the
  current fragment ref/payload hash. Assistant text is never parsed as proof.
  Short-horizon content without a supported public binding makes completeness
  unverifiable; it is not silently removed from dependency accounting.
- Terminal observer passes the rebuilt proof to record_terminal_observation;
  existing append-only observations are not amended. Explicitly register the
  existing host-primary-runtime-v1 Host filter policy in production support.
- ProductProviderAdapter accepts an optional async pre-invoke guard. Main's
  binding resolver binds the actual SDK Run id into that guard. At every real
  adapter entry it reads public start/current tool source facts, validates the
  current request's disclosure and complete dependency closure, then checks the
  shared policy immediately before delegate invocation. Denial is a typed SDK
  definitely-not-sent, non-retryable failure; no mutation of a hashed request,
  no cached allow replay or private SDK SQL. Legacy/non-primary paths need an
  explicit applicability test against trusted Host Run binding, not metadata
  absence interpreted as unrestricted primary permission.

Verification will separate real Host/Harness/Memory SQLite tests from installed
0.6.6 wheel and native Provider/UI evidence. Required cases remain those above;
late denial additionally asserts zero physical transport calls and no unknown
handoff/retry. Scope: runtime/context/terminal/composition, Provider adapter and
main guard wiring, recall identity projection/policy registration, focused tests
and architecture. Shared helper/API files and presenter files are other owners'.

### Independent review tightening

Dirac's two contract P1s are accepted. Typed item identity means the actual
`result_item_hash`, never `public_payload_hash`; preserve it while the public
execute result is in hand. Missing typed fields or unsupported short-horizon
carrier rejects ordinary outbound use as well as later reuse, including new
recall produced within this Run before its next Provider request.

Preflight denial will use a Host-specific `ProviderRequestRejectedError`
subclass, scoped strictly before delegate invocation. Harness already records
hand_off before adapter.invoke, so the expected durable outcome is FAILED with
zero physical delegate calls, not a fictional persisted not_sent state or
CONFIRMED_NOT_STARTED reconciliation. Existing failures after delegate entry,
including ambiguous transport handoff, retain their existing classification.


## Implemented review slice and verified boundaries

Runtime proof is deep-frozen in FrozenContextAuthority, included in the prepared
snapshot lineage and SDK start metadata. Primary history uses shared policy after
public SDK transcript reads, filters complete groups before budgets, and falls
back only to each independently verified original USER. New observations carry
current USER plus retained parent terminal roots and all newly consumed typed
recall bindings. Existing observations are never rewritten. The terminal's cold
S1 pair is eligible without asynchronous Memory ingestion; host-primary-runtime-v1
is explicitly registered in production filter policies.

The dependency reader enumerates this Run's Host memory_standalone route receipts
and uses public uow.read_start_snapshot/read_effect. It retains consumed recall
regardless of which tool text a later request contains. Host receipt equals the
actual SDK tool-result receipt; SDK Run/effect/raw_call_id are checked in their
own namespaces. Public payloads are thawed at the projection boundary, preserving
result_item_hash separately from public_payload_hash. Unsupported/missing carriers
reject before physical send. Main's resolver binds its actual binding.run_id into
the guard. Request disclosure is freshly Host-authored for the current local
principal, TASK_EXECUTION and actual request_id; arbitrary metadata supplies no
authority. Missing Host binding for primary metadata rejects as corruption, not
legacy bypass. No epoch/allow caching or mutation of hashed Provider requests.

A production ProductProviderAdapter preflight rejection is
PrimaryHistoryDisclosureRejected(ProviderRequestRejectedError). Real Harness
records FAILED with zero physical transport calls; after-send ambiguous errors
remain UNKNOWN with one physical entry. Replay of a settled denied turn does not
invoke Provider again. This is not a persisted not_sent status.

### Tests and dependencies

Shared helper is read-only exact ea315525, loaded from an ignored git-show copy
for isolated runtime tests; its API/service commit has not been merged into this
worktree. Main factory injection requires that commit when combining. Memory
0.6.6 wheel source9ec5943, SHA256
381d85437537ae1f58f04b84e8332b0774d1e3aff2c6529b3824126071135361 is unpacked under
ignored evidence and imported ahead of the unchanged main venv Memory0.6.3.
This verifies the exact wheel code with Host tests, not a replacement of the main
installation. Harness0.7.2 remains the installed public runtime. No App/Provider,
SDK/pin/main checkout edits or private SDK SQL. Main's c283e51c three explicit
control inventory declarations have been synchronized manually; PERSONA remains
pending the coordinator's exact integration patch.

```sh
PYTHONPATH=backend:.local-test-evidence/2026-09-05/primary-history/memory066:.local-test-evidence/2026-09-05/primary-history /Users/denny/projects/simple_harness/backend/.venv/bin/python -m pytest backend/tests/execution/test_primary_foreground_runtime.py backend/tests/execution/test_primary_create_new_runtime.py backend/tests/execution/test_primary_history_outbound.py backend/tests/execution/test_foreground_runtime.py backend/tests/sdk_adapters/test_primary_provider_preflight.py backend/tests/sdk_adapters/test_product_host_ports.py backend/tests/sdk_adapters/test_provider_timeout_is_a_safety_net.py -q -p no:cacheprovider -p history_test_bootstrap
```

**94 passed in32.78s, exit0**, narrowly scoped. Includes real Host/Harness/Memory
SQLite USER→analysis→typed recall→production context_route→next Provider, strict
four-tuple negatives, unsupported-short carrier injection, and late MEMORY-only
suppression before second send. The short injection proves rejection, not a new
supported short-horizon public binding. Memory-only suppression API+next-Provider
combined acceptance remains coordinator-owned test_primary_runtime_api_integration.py.

### Open P1: existing pre-scoped ResumePackage ingress

These94 greens do not clear an independently reproduced functional regression:
TaskScopeForegroundContextPort.prepare still emits no complete dependencies for
its audited ResumePackage. Applying the new production guard to an existing
pre-scoped queue entry rejects its first Provider call. A real service-created
scope/root + scoped queue + actual Harness/ProductProviderAdapter probe expects
COMPLETED/one send but observes FAILED/zero sends: **1 failed in1.09s, exit1**.
This is a P1 requiring source-carrier/admission-contract coordination, not an
acceptable documented exemption. Do not invent USER-only proof covering other
scoped content, bypass by scope/metadata, merge into production, or call this a
complete replaceable candidate. The earlier dynamic route execution tests do not establish historical carrier completeness. Dirac has the exact red probe; scope resolution needs coordinator alignment while runtime implementation remains
this owner; API/Provider/native combined gates remain separate. Full program/privacy completion
is not claimed. Unsupported independent short-horizon remains a separate gap.

Raw evidence indexes (ignored, no raw files staged):
- `.local-test-evidence/2026-09-05/primary-history/preflight-red.log` SHA256 `121e19e14adc59d4a6f118b06095e32d69edf4da5994ff35358ba01ad6f1ea19`
- `.local-test-evidence/2026-09-05/primary-history/preflight-green.log` SHA256 `c91dad03511eb111dba9f317cdb7962c64ec389c7d259de488e167c78aac1b31`
- `.local-test-evidence/2026-09-05/primary-history/candidate-final.log` SHA256 `f11236485dabbf8d603240c602955cac903646a4c225867e6eeaa3e52ceff251`
- `.local-test-evidence/2026-09-05/primary-history/outbound-final.log` SHA256 `1d38cbf2c0417905649daf4e15d09be702c288f9eb9c2c38c2dcf40bc7d5e8aa`
- `.local-test-evidence/2026-09-05/primary-history/scoped-entry-red.log` SHA256 `dddf77b61bb9c64628f4bbc157e8d091bead854b7660d70f5cb15a74bc6e9eaf`
- `.local-test-evidence/2026-09-05/primary-history/scoped_entry_probe.py` SHA256 `6561ab548a2018a4f4d21e5a97a81b65a855878d544a6286f6560da27e4e0945`

### 2026-09-05 Dynamic ResumePackage leak correction

Dirac's real None→context_route.resume_existing probe exposed a separate P1:
the actual ResumePackage reached the second physical Provider call while the
proof still covered only the current USER. This tree reproduced **1 failed in
1.27s, exit1**, without modifying ledger facts or calling an external Provider.
The runtime now enumerates every durable route and checks its exact public SDK
effect/receipt. A consumed ResumePackage with no complete source proof rejects
before delegate entry; a subsequent route cannot erase that dependency. The
second invocation settles FAILED/primary_history_disclosure_rejected, with no
second physical send and no replay. This closes this leak, **not** the existing
scoped functional regression or full ResumePackage provenance. task_scope_search
and other historical carriers still need a completeness audit; no overall privacy
or production acceptance is claimed.

Focused command (same fixed066/helper PYTHONPATH/bootstrap as above):
`python -m pytest backend/tests/execution/test_primary_dynamic_resume_visibility.py backend/tests/execution/test_primary_history_outbound.py backend/tests/execution/test_primary_create_new_runtime.py -q -p no:cacheprovider -p history_test_bootstrap`

**21 passed in18.08s, exit0**. Includes actual AUTO create_new→workspace effect→
terminal with fresh dependency guard at all seven deterministic Provider entries;
this is SQLite/Harness coverage, not native/real Provider evidence. build signature
is unchanged. PERSONA is synchronized exactly from coordinator5da24d6f; the three
c283e51c projectless safe declarations remain. API integration testcase belongs
to the coordinator and is not edited here.

Ignored evidence:
- `dynamic-resume-red.log` SHA256 `5dde12ebab202f6c123c1bb8fe1885a5f8c9a0525339eea416f1d0d0c8657714`
- `dynamic-route-final.log` SHA256 `033ca18c6a51bde7001adfac78915e015839c242991baeeaf8f4ef83715e09f6`
Both under `.local-test-evidence/2026-09-05/primary-history/`.

### Fixed source composition and installed interpreter

`ea315525` is now cherry-picked as `2645d8b2`; only the three ARCH documents
conflicted and both runtime/API status sections were retained. The tracked tree
contains primary_visibility.py and the actual history_visibility_checker API.
Earlier94/21 results used a fixed helper loading plugin and must not be described
as a standalone startable tree. Reverification without that plugin: **71 passed
in39.38s, exit0**, on the21 runtime cases above plus test_primary_visibility.py,
test_primary_read_api.py and test_primary_control_binding.py. Log
`combined-no-helper-overlay.log`, SHA256
`c7722facb0054576cfd8d4b9b021015161630cb883aa71b035665ebc513e92ab`.

An independent local interpreter is now available at
`.local-test-evidence/2026-09-05/primary-history/venv066/bin/python`.
It offline-installs the exact Memory0.6.6 wheel381d8543 and reads existing Host
pytest/Harness dependencies through a .pth pointing to the main venv site-packages.
Main packages were not modified; this is not a fully standalone dependency install.
The new interpreter imports Memory from its own site-packages. No helper or Memory
source PYTHONPATH overlay is required:

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-history/venv066/bin/python -m pytest backend/tests/execution/test_primary_dynamic_resume_visibility.py backend/tests/execution/test_primary_history_outbound.py -q -p no:cacheprovider
```

**10 passed in7.24s, exit0**, log `installed066-smoke.log` in the same ignored
evidence directory. Coordinator-owned runtime/API integration test was modified
concurrently and was neither edited nor staged by this owner. Remaining scoped,
other historical carrier and short functionality gaps still block production;
these tests do not claim a native app startup or real Provider validation.

### Origin correction and mandatory adjacent contracts

Independent review rejected f1ecd984: its ALL-route effect interpretation treated
Host no_recall synthetic markers as tool effects. The existing ordinary/reopen
history testcase consequently hid the actual assistant. 21/71/10 earlier greens
did not cover this direct dependency and do not clear that regression.

The correction keeps all durable decisions but reads actual SDK effects only for
context_tool. Host no_recall must equal the exact production marker/UUID/Run/turn/
direct_standalone/no-scope/no-refs receipt; it cannot cover arbitrary historical
content. host_initial is also not a tool effect and still requires the unresolved
scoped source contract (no scope exemption). Unknown origin rejects.

Installed066/no-overlay adjacent command:
```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-history/venv066/bin/python -m pytest backend/tests/execution/test_primary_foreground_runtime.py backend/tests/execution/test_primary_dynamic_resume_visibility.py backend/tests/execution/test_primary_history_outbound.py backend/tests/execution/test_primary_create_new_runtime.py backend/tests/sdk_adapters/test_primary_provider_preflight.py backend/tests/sdk_adapters/test_provider_timeout_is_a_safety_net.py .local-test-evidence/2026-09-05/primary-history/scoped_entry_probe.py -q -p no:cacheprovider
```
**41 passed, 1 failed in30.65s, exit1**. Sole failure is the correct initial-scoped
expectation (COMPLETED/one physical send versus actual FAILED/zero). Ordinary None
completion/inheritance/reopen, dynamic resume denial, typed recall binding and
short rejection, late denied versus post-send unknown, and create_new effects
pass. This is explicitly NOT an overall PASS or a releasable candidate. The
coordinator's concurrent API integration testcase is not part of this command.
Log `adjacent-contracts.log`, SHA256
`e0f8fdaae8fbd06a0856322639dcbe44d8129373084e0218c9f6ff888ef01a51`.

### 067 runtime bridge and new-message composition (2026-09-05)

Scope restoration is fixed at8e896472 (separate review). WAITING invalidation is
61436ccc, unit recovery + actual primary32green. New-only terminal message S1
hook3918b55d formally consumes producer7fe6b178 as9e2be493; high-level verifier
64dea62c is c53effc6, and marker/prior-read-only hook is c6533e6e. Existing terminal
observations are never backfilled; marked new eligible groups missing children
raise rather than repair. Observer/message writes are atomic, not the later
foreground terminal receipt transaction. Hegel owns11group/registration/fault
acceptance. This tree's new-message/runtime/API adjacent33passed45.03s.

Runtime now accepts closed proof v1 and v2. Non-short newly built proof may remain
v1; an explicitly supplied v2 (including empty short list) is never downgraded.
Exact short is `(audit_id,chunk_ref,content_hash)`; actual UTF8 payload bytes and
route recall_refs must match. No conversion to typed result/item identity. Scope
manifests, initial start, inherited terminal and producer snapshots preserve all
short entries and original schema semantics. No old archive/hash rewriting.

RecallLanes.short_history_dependencies accepts the Host indexing service's actual
roots proof, freezes it deeply, and projects a separate history_source_dependencies
next to each exact short history_binding. Missing Host roots rejects ordinary
outbound use. The SDK triple does not magically prove complete Host selected-item
lineage; Hegel's initial conservative all-indexed-roots contract is a separate
production integration, not claimed complete by these parser/negative tests.
Main has not yet injected an indexing-service getter into HumanMemoryV7Runtime.

The DTO transport test found a real mutable-source alias (1failed6passed before
fix); the frozen copy fix plus codec, actual SQLite/SDK forged-audit/byte/source
denials, existing scope/primary and shared API tests yielded:

```sh
PYTHONPATH=backend .local-test-evidence/2026-09-05/primary-history/venv067/bin/python -m pytest backend/tests/execution/test_primary_dependency_v2.py backend/tests/execution/test_primary_history_outbound.py backend/tests/execution/test_scope_disclosure_runtime.py backend/tests/execution/test_primary_foreground_runtime.py backend/tests/memory/test_primary_short_visibility.py backend/tests/memory/test_primary_runtime_api_integration.py -q -p no:cacheprovider
```

**60 passed in54.31s, exit0**. This is not a real selected Host short-pipeline
positive or native Provider/UI result. Shared helper/API7dcfce8b and coordinator
2299ef55 tests were consumed unchanged. Exact independently installed Memory0.6.7
sourcefa6badd086d089e3e1752df45993ce0ccba98377, wheel SHA256
7dd224c29923ab1346a78bb8529d9426559bb1e3a38b8c0964f57cc8687e9c3d;
interpreter imports its own067 site-packages and only borrows Host test/Harness
packages read-only via.pth. No SDK/source PYTHONPATH overlay or main install edits.
066 earlier results remain explicitly066; they are not067 evidence.

Ignored logs under `.local-test-evidence/2026-09-05/primary-history/`:
- runtime-v2-codec.log (alias red) SHA256 7c236ec52f9f94e49ad3f0767cdb0efac4ab7276fa8dff02f58770483d7d0679
- runtime-v2-combined.log SHA256 b9bcd48e8682f149a2598e84e3932d63447f9e13f2895dba413e52996c9e5e79
