# Explicit semantic correction — executable leaf contract

2026-09-05. Base9ec0ec97; isolated feat/human-memory-semantic-correction.
Implementation candidate; focused public SDK checks recorded in RESULTS.md. No production/native completion claim.

## Original acceptance remains

Original Memory plan `EXECUTION-REVISION-2026-09-05.md` stage2 retains HM-AC1/2/4/7/8:
remember→use→correct→forget, authenticated evidence, append-only history, typed recall,
actual lineage and replay, old value must not reappear. HM-AC6 display-only TwinGraph
must not enter Agent input. This leaf implements explicit semantic correction only;
UI, immediate/high-priority scheduling, all cognitive types, full corpus and native
acceptance remain under coordinator and are not waived.

## Fixed SDK hypotheses to test

Installed Harness0.7.2 + Memory0.6.7 public ports are the compatibility target.
Actual selected semantic recall item supplies source_ref=memory_id, source_revision,
source_content_hash and public_payload_hash; item_id is NOT memory_id. Result/item
hashes are preserved. SDK operation is REVISE (new revision of same memory), not
UPDATE. SUPERSEDE only retires a target; it is not a replacement-create shortcut.
Recall eligibility does not grant mutation authority: REVISE must carry an exact
MemoryActionAuthorityRef resolved by the Host's durable authority port. Test missing
authority→NEEDS_USER_CONFIRMATION before the real positive.

## Minimal flow

1. Executor resolves existing actual analysis outbox/lineage and original S1 items.
2. New SemanticCorrectionAuthority uses public execute_typed_recall with semantic
   type only, bounded full-text query from current evidence, authenticated subject /
   disclosure / original evidence refs. No TwinGraph, no SDK private SQL.
3. Before Provider, persist a real recall observation snapshot in the existing Host
   append-only evidence store. This RUNTIME_EVENT is input audit data, NOT a new
   USER assertion, mutation support, or Memory-ingestion event. No outbox/job is
   generated. Bind snapshot to analysis evidence-set identity and exact context /
   plan / execution/result/item hashes. No extra schema or replay ledger.
4. Model sees bounded actual semantic payloads with Host candidate keys. New v2
   action `revise_semantic` must select an issued candidate key and cite an exact,
   unique current USER quote. Host preserves exact subject/predicate slot and
   validates a unique eligible target; ambiguous/truncated/confirmation candidates,
   arbitrary IDs, wrong type/slot/revision/hash reject without overwriting.
5. Host compiles REVISE with ExistingMemoryTarget from the stored actual candidate,
   explicit-user-correction span, and unchanged analysis plan identity/base_revision.
   An exact durable action authority binds plan/operation/target/new S1 evidence.
   SDK still performs current target/revision/suppression and atomic apply checks.
6. Fresh public visibility check before Provider and before granting mutation.
   A settled response recovered without an envelope uses its original durable
   candidate snapshot, never a new candidate list. Old v1 CREATE responses remain
   readable; normal successful envelopes retain existing zero-call replay.

## Coordinator wiring (main/HumanService/API owned elsewhere)

Construct one `SemanticCorrectionAuthority(db_path, manager_getter=..., principal_getter=...)`
with lazy current manager/principal getters. Inject it as
`HostMemoryAnalysisExecutor(..., semantic_correction_authority=authority)` and pass
same object to public `build_human_memory_v7(..., memory_action_authority=authority)`.
HumanMemoryV7Runtime must forward that public builder kwarg (coordinator integration).
No UI confirmation round is added for an unambiguous explicit current USER correction;
ambiguity returns a stable rejection/no-mutation rather than manufacturing approval.
Absent integration does not authorize correction; existing CREATE remains compatible.

## Required leaf tests

Real public manager SQLite: CREATE original→typed recall actual target→current S1
correction→public REVISE committed→new value recalled, same ID/new revision; original
S1 unchanged. Close/reopen/durable-response recovery and duplicate replay with no
extra Provider/apply; wrong candidate/type/slot/hash/target, ambiguity, stale revision,
suppression after recall, missing action authority and invalid quote reject. Provider
is deterministic test adapter; no paid Provider/native/full suite.

## Intent and recovery refinement after independent contract challenge

A model choosing revise plus a true quote is insufficient authority. Before model
output, Host derives intent independently from a CURRENT USER complete sentence:
`Correct my <predicate with underscores rendered as spaces> from <actual old value> to <new value>.`
or `请把记忆里的<Host Chinese slot alias>从<actual old value>改成<new value>。`.
Only subject_entity=user:self and an exact actual candidate slot are supported.
Leading quotation, third-party/reporting text, hypothetical/negative/history prefixes
and any unrecognized form do not grant intent. These bounded grammar forms are the
implemented leaf scope, not completion of unrestricted natural-language/program AC.
The Host intent commits evidence ID/envelope hash/item/unique quote range plus
subject/predicate/old/new value; compiler and authority issuer both verify it.
Preserve candidate qualifiers/classification rather than letting the model mutate
other fields. Multiple same-slot candidates reject even if the model selects one.

The first candidate snapshot is immutable under the analysis evidence-set identity;
there is no 'latest batch snapshot'. Before the existing post-turn attempt reserve /
handoff, a separate atomic append binds exact attempt ID/request hash/input bytes
hash/candidate snapshot ID+hash. An orphan binding is not a Provider-send fact.
The actual response is append-only bound to that same attempt/input/candidate hash.
Derivation loads this exact input record; it cannot issue a new recall or replace
expired candidates. It verifies fresh source visibility, then issues a current exact
mutation authority. A committed SDK apply with lost Host ACK reuses the original
plan/receipt; an unapplied plan with stale target/suppression/expiry rejects. Existing
successful delivery envelopes are replayed first without Provider calls.

Required negative: model force-selects revise with a real quoted USER statement
that lacks Host-recognized correction intent → zero issued action authority and
zero REVISE/cognitive write. Include third-party, hypothetical, negative and historical
variants, and preserve all original source bytes. Repeat ACK-loss and response-only
recovery as distinct stages, not a single generic retry test.

## Chinese product wording (2026-09-05 refinement)

Positive complete sentence: `请把记忆里的饮品偏好从无糖乌龙茶改成柠檬水。`
The original actual USER may be `我的默认饮品偏好是无糖乌龙茶`.
Host aliases are `饮品偏好` and `默认饮品偏好`, mapped only to actual typed
semantic predicates `preferred_drink`, `drink_preference`, `default_drink`,
`default_drink_preference`, `饮品偏好`, `默认饮品偏好`, with subject `user:self`.
No caller target ID, model-invented alias or graph display is accepted. Unknown
predicates/slots, other phrasings, omitted old values, multiple sentences, quoted,
negative, hypothetical and reported commands do not authorize. Multiple candidates
matching one sentence reject, including distinct predicates sharing a Host alias.
This is deliberately bounded explicit language support, not general NL completion.

Chinese old-value substring extraction is used ONLY as a full-text retrieval hint
because the installed SQLite tokenizer does not segment a whole Chinese sentence.
A quote or negative can retrieve an actual candidate; independent fullmatch intent,
compiler and issuer checks still reject mutation. Fresh source visibility is checked
again immediately before delegate invocation; a Host ProviderRequestRejectedError
subclass is thrown before transport on denial. Sent timeout/cancel taxonomy is untouched.

## Canonical naming for new supported-slot CREATE

The v2 analysis system prompt now specifies `subject_entity=user:self` and
`predicate=drink_preference` for newly created user drink/default-drink preferences.
The value must come from evidence; no particular drink is preselected. This is a
product vocabulary convention. General CREATE remains freeform; old candidates are
not renamed and REVISE must preserve their actual predicate. No new alias, target
lookup or grant is introduced. Model compliance still requires the future native loop.
