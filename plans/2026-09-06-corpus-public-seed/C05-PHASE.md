# C05 actual-main phase contract

2026-09-07. Source-only successor from main `79db8669`; **NOT_RUN**. Earlier f780/r3 proof is retained, not repeated or promoted to current H0710/main acceptance. Main owns all resource scheduling; no model/server/test started here.

## First consumer: 04 / 09 / 14 / 20

Hegel owns `corpus_scoring_session`/dispatcher and main constructor passthrough. This leaf owns C05-specific modules and history reader. No change to shared dispatcher or product SDK pins.

1. Create isolated main userdata and an explicit trusted **configured workspace root** under its fixture directory. Both main route binding store and binding append authority must receive the same root; userdata alone does not isolate the production default workspace. No HOME override.
2. `TaskSetupHttpProvider(model=actual_model)` -> `await start()` -> `registration()`. Register the returned distinct process-only provider before setup; main resolver, ProductProviderAdapter, exact guard and SDK driver remain intact. HTTP is local, bounded, setup-only, no external model/gold. `arm` verifies frozen setup/operational fixture text; every planned tool must be actually offered by the real wire request. Local HTTP request hash is not represented as an SDK Run/request/receipt identity.
3. Construct `C05PhaseHistoryReader(path=HostDB, subject=actualsubject, delegate=actual PrimaryHistoryStore)` once and pass through main's optional constructor seam. Setup reads use the ordinary delegate. No authority object is replaced later.
4. For each frozen scope, call existing `prepare_scope_archive(..., provider=http_provider, drive=drive_exact_queued, disclosure_context_resolver=resolver)`. After actual enqueue it calls `http_provider.bind_queued(queued)`. The drive closure reads `http_provider.queued`; it must execute this exact queue item with normal runtime and exact setup decisions, not enqueue another USER. `resolver(*, run_id, turn_id)` awaits actual Host `resolve_current_disclosure` after real route IDs exist. Static disclosure_context and resolver are mutually exclusive. No fixture-reader identity in main.
5. `C05SetupApproval(ingress, stack, transport, ledger, binding_store, expected_configured_root, persist)` is callable with `service, queued`. It compares actual public pending decision Run/turn/generation/version/nonce/call_id/name/full arguments to this setup's actual emitted fixture call. It verifies the configured root and exact routed root for scoped actions; only the original create route and concrete discovery/marker/update calls qualify. No allallow, truncated argument preview, fake challenge, policy mode mutation or reuse of C01's read-only memory approver. Unknown waits stay blocked. Manual directory challenges are not silently approved by this tool-decision driver.
6. `prepare_scope_archive` re-reads actual SDK effect/terminal, Host source binding, material marker bytes and ordinary disclosure. Capture each returned immutable archive. Per-arm `current_request_hashes` and `planned_calls`; lifetime `attempts` and `request_hashes` describe only fixture HTTP observations, not source authority. Provider source/binding trace remains the SDK's actual public output.
7. After all setup terminals settle and the main resolver reports no active fixture Run, `await phase_reader.freeze(archives=..., stack=actualstack, primary_ref=actualprimary)`. This validates complete setup prefix and actual terminal bindings. Retain original source archive; do not copy only USER evidence to a fresh scoring DB.
8. Switch next Run's provider through the existing public registry/session binding reconciliation to real scoring Provider. Retired fixture binding stays resolvable for old audit. Enqueue only the original scoring text with `scope_ref=None` for these first four cases; the setup target must not be a preselected scoring target. Teardown closes the local server and owned connection tasks.

Exact initial-scoped/effect approval for the **scoring** phase is a separate contract; this setup approver is never used to grant a model-generated task action. No actual-main success is asserted by source compilation.

## Additional public runtime code in this successor

`TaskRuntimeLane` binds actual service/provider/drive/stack/policy/disclosure and one Host store. `prepare_task_case` calls the real source producer for each label. `TaskSourceReader.open/page` consumes real Host public search/open and production disclosure, never raw snippet/README as ordinary model input. Search receipt and transformed view are explicitly different objects.

- 07: `scoring_scope_ref` is actual C ID; `enqueue_scoring` uses it and checks actual admission receipt. C is not inferred from last-created scope.
- 08: completed scope is opened through public read-only archive; state and prior SDK terminal identity remain exact.
- 10/11: query includes every original setup title as a complete unicode61 token; actual public FTS page/cursor order must be B,A. No cursor fabrication, result reordering or rank-threshold change.
- 12: lanes must share one Host DB and have distinct actual subjects. B's owner must successfully open it; self exact-open must reject with permission_denied and actual search must include A/exclude B. A string owner label is not authority. Actual-main foreign-principal composition remains unverified.
- 18: initial and preselection revision are separate actual Runs. Per-Run closure idempotency keys avoid reusing the initial scope mutation plan. `before_selection()` is a predeclared between-user-turn fixture action, never a response to gold/model choice. It requires actual revision/source change and re-disclosed new resume. `add_completed_phase` validates that actual mutation archive and uses exact-turn filtering, preserving earlier scoring turns across pages. Cross-process phase restart proof is not implemented here.
- 16–19 neutral missing titles stay explicitly synthetic. 16's shared month/detail disclosure, and 17/19's initial named root are not waived. Do not append a second root after create: actual BindingRootResolver rejects multiple roots. Only read-back checking exists until the initial-root selection seam is agreed. Unsupported source obligations reject scoring admission; denominator unchanged.

## Product fields still needing a distinct source contract

Dates, historical title alias and shared project cannot be stuffed into goal or inferred from gold. Proposed minimal direction: existing append-only Host mutation producer supplies typed metadata events and ordinary disclosure verifies its real S1/plan/receipt plus current policy; no parallel ledger or synthetic old evidence. This is not implemented or accepted in this phase. First named root must be selected before first append, then original binding authority performs unchanged owner/generation/root checks. Main and Dirac are coordinating that narrow seam; no multi-root bypass here.

No additional tests run. Subsequent necessary controls must use main's complete candidate/current H0710 target; old H079 r3 controls remain historical evidence.

## Fixed dispatcher observations and pending controls

`read_candidate_events(*, path, subject, sdk_run_id, stack, policy)` returns a tuple of
`{kind: task_candidates_visible, sdk_run_id, effect_id, call_id, actual_result_hash, visible_count, visible_sources}`.
Every event is exact Host-indexed/public SDK terminal search output, with complete candidate shape,
source/hash verification and current disclosure policy plus a post-read same-context check.
No indexed search, unfinished search, real SDK failed/rejected with error_code or a successful empty candidate list returns no event;
malformed/mismatched source, stale disclosure or >256 indexed searches raises (unverifiable), not empty success.
The hash covers actual public result value, not a ToolTerminalReceipt or host journal hash.
This schedules the original scripted followup only; it grants no selection/resume permission.

`TaskRuntimeLane` accepts exactly one setup disclosure input (legacy static or async resolver);
actual-main resolver lanes also require `read_context_resolver()` returning the currently bound
scoring/setup disclosure for each public read. `TaskSourceReader` checks it before/after the awaited
ordinary policy. No source-time disclosure is silently substituted for scoring context.

HTTP `current_responses` records turn_ref, wire_request_hash, response_id, response_body_hash and
call_ids for each locally prepared response. Response ID is `c05-fixture-<exact request bytes SHA256>`;
this lets the consumer join the actual public provider response identity to its local wire hash.
A prepared response does not prove physical delivery/SDK acceptance; actual SDK trace must match.
`C05SetupApproval(*, ingress, stack, transport, ledger, binding_store, expected_configured_root, persist)`
is awaited as `approval(service=actual_service, queued=actual_queue_receipt)`.

Minimum next controls (NOT_RUN): actual-main 04/09/14/20 setup/phase consumer, exact wrong-turn/args
approval denial, configuration switch during candidate policy, C18 real second revision and interleaved
history, actual 07/08/10/11/12 source readbacks. Existing f780 five and prefix three are not repeated.
17/19 named-first-root and source metadata remain explicitly unclosed; no claims of 20 READY.

Static followup: Hegel found the initial HTTP TOOL conversion omitted actual wire tool_call_id.
It now constructs public CallId from that exact field and preserves name; missing/invalid IDs reject,
never synthesized. Added one unrun codec control. Installed H0710 kernel's actual tool_authorization
producer records `request.call_id` from prepared.call.call_id (and exact arguments/tool_name/nonce);
C05SetupApproval uses that real field, not a tool_call_id alias. This was code reading, not runtime proof.

Dirac corrected the call identity namespace: decision.request.call_id is the SDK internal ID,
not the physical Provider raw ID. Approval now requires stack, reads the actual public effect by
request.effect_id, checks Run/effect/internal call/tool/full args, then matches effect.raw_call_id
against this queued setup's actual HTTP plan. No inferred ID transformation or name-only permission.
Missing indexed effect or terminal result now raises; real nonterminal is pending, SDK rejected/failed
with stable error code yields no candidate, unknown/partial/malformed success fails unverifiable.
An arbitrary error key is not accepted as proof of a terminal failure. New source remains NOT_RUN.

Main first current-H0710 batch: 07/08/TOOL codec passed, 10/11 failed before second-page access
(3PASS/2FAIL, 9.44s, PG83131 exit1/remaining=[]). Existing raw remains in main
.local-test-evidence/2026-09-07/corpus-c05-runtime-source/r1/command.log.
Read-only existing Host DB diagnosis: unicode61 plus exact quoted query produced zero matches for
旧书/海报. Complete original titles (both, not selected target) match both actual documents.
C10 actual BM25: B=-1.7151424287856072e-06, A=-1.67007299270073e-06.
C11 equal BM25=-1.6923076923076922e-06 and equal scope-local source_sequence=3;
the unchanged opaque task_scope_id ascending tie-break orders B,A in these original fixtures.
No IDs or content were selected/changed to manufacture that order. Creation order alone does not
establish ranking; corrected prior claim. Helper now issues both complete original setup titles and
still requires real B,A pages; different authority/IDs that change the order remain an explicit failure.
This diagnosis used mode=ro on existing Host SQLite only, not public consumer acceptance or new tests.
Only original 10/11 red selectors need main rerun; 07/08/TOOL and old green cases are retained.

Consumer delta: visible_sources contains only the exact (task_scope_id, source_id, source_hash)
triples whose existing verification and current policy check succeeded in that event read. It is
not a selection grant. The consumer must re-read for freshness and compare an actual final-choice
request's arguments; hidden candidates are not returned here. Existing actual_result_hash still
covers the whole unchanged public SDK result, and visible_count counts these returned members.
Source-only, no additional test run.

Pending protocol correction after main's first real waiting control: EffectRecord does not exist before
REQUIRE_USER resolves. Use `verify_pending_call` from corpus_c05_approval (see
[C05-AUTHORITY-CONTROLS](C05-AUTHORITY-CONTROLS.md)) for both setup/scoring approvers;
the previous pending read_primary_dependency_facts mapping is superseded. This does not change settled
candidate-event reading, which still requires actual indexed effects/results. No SDK bump or private SQL.

Visibility classification correction: the existing boolean policy collapses read failures and denial
into False. A nonempty candidate with False or missing disclosed fields now raises
`c05_candidate_visibility_unverifiable`; no typed suppression reason exists here to classify it as a
confirmed hidden/empty result. Only actual successful candidates=[] is true zero; the consumer must
report this new error as OBSERVATION_FAILED, not FOLLOWUP_UNMET. Hegel owns the new negative control.
