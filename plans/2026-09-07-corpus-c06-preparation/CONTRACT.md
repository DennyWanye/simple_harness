# C06 public preparation — source only

Base c5b55387; existing tree, feat/corpus-c06-preparation. No SDK modification,
scoring/session change, tests, model calls, installation or resource work.
All new controls **NOT_RUN**. This is not a 240 quality run.

## Source and minimal vertical group

Original `06-cross-scope.md` under Memory's
`plans/2026-08-29-human-memory-digital-twin/quality/recall-corpus-candidate/review-zh/successor-12x20/`.
Only twenty exact setup lines were extracted into `corpus_c06.SETUPS`, each with
UTF-8 SHA256. Compiler accepts only case ID, exact setup and offset-bearing
scenario clock. No gold/initial/followup accepted or loaded.

First group C06-02/03/04 has one personal Semantic claim and one low-risk
Procedure. Exact individual source quotes, original step order, no invented
relation. Fixture interpretation: the authored existing fixed procedures are
ACTIVE; this is an explicit setup mutation, not inference from a model or
permission to execute their actions. Current applicability/consent remain
production runtime responsibilities. Personal scope is owner scope, **not** a
TaskScope filter or an authorization to disclose to another recipient.

## Dedicated interface for actual main integration

`compile_c06_setup(case_id, setup_text, *, scenario_clock) -> C06Batch`

`prepare_c06_setup(*, path, manager, principal, authority_ref, batch,
 delivery_authority) -> dict(case_id, setup_hash, source_pair, labels, plan,
 outcome, fixture_executions)`

Pass the actual main MemoryManager/principal and existing Host evidence store.
Pass `SetupFixtureDeliveryAuthority` to the public builder at construction;
preparation binds it once. No runtime authority replacement. Real Host S1
append/readback, public owner registration/ingestion and DurableMemoryJobRunner
APPLIED bind the exact request/evidence/claim head to the delivered mutation.
Original source is synthetic fixture evidence, not an invented completed Task.
Labels are SDK-created IDs/revision/payload hashes from public graph readback;
source identity uses its public evidence/span hashes, not quote hash alone.
Graph is acceptance-only and MUST NOT be put in model history/prompt/recall.
Readback tolerates unrelated existing memories but rejects ambiguous matches.

Hegel owns shared scoring/session. Integration must use actual current
principal/disclosure, current input admission, real new Task/runtime and public
typed recall/provenance/visibility. Do not reset all history or copy setup into
current messages. Current implementation deliberately does not claim that
preparation proves cross-scope retrieval, non-SELF permission or quality.

## Twenty-case inventory (not denominator reduction)

- 02/03/04: executable preparation source and three parameterized public controls;
  source/reopen/foreign-owner/same-text-other-S1 checks. NOT_RUN.
- 05–16: next explicit semantic/procedure mappings; preserve description-only,
  confirmation and personal/public restrictions. No executable mapping yet.
- 01: actual old Task source and new Task boundary required; standalone S1 cannot
  masquerade as that provenance.
- 17: additional real public Episode, not a dropped distractor. Common synthetic
  past-time convention may fill missing precision, labelled as fixture time.
- 18: actual finance-only Procedure applicability distinct from global P1.
- 19: actual offline current environment and online-plugin requirement; an
  arbitrary filter or fabricated environment receipt cannot prove this.
- 20: authored conditional branches must remain conditional, not specialize from
  the scoring input or gold. Public payload representation still to implement.

These are remaining adapter/runtime work, not claims that SDK lacks capability.
No broad fallback, fake receipt, private SQL or weakened disclosure authority.

## Main-only first selectors

`backend/tests/quality/test_corpus_c06_preparation.py::test_public_c06_mixed_job_exact_source_reopen_and_foreign_owner`
(02/03/04) and `::test_c06_exact_setup_and_twenty_case_denominator`.
Use current installed target; source stage has executed none of them. No C01–C05
reruns required. Foreign-owner control exercises real public Manager storage,
but is explicitly not a non-SELF physical Provider disclosure test.
