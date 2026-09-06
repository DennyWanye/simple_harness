# Explicit semantic correction leaf — review candidate

2026-09-05; base9ec0ec97, isolated feat/human-memory-semantic-correction.
No main/Runtime/HumanService/API/SDK/pin changes. Coordinator owns production wiring.

## Result and executable scope

**32 passed in 19.13s, exit0**: 20 leaf cases plus 12 adjacent analysis cases.
Real installed Memory0.6.7/Harness0.7.2 public MemoryManager builder and SQLite,
actual Host S1/outbox/job/analysis delivery and public typed recall/mutation.
Provider is deterministic; existing terminal/binding fixture uses Host test ports.
This is not native, real Provider, full Harness foreground or full program acceptance.

The supported Chinese sentence is `请把记忆里的饮品偏好从无糖乌龙茶改成柠檬水。`.
Original `我的默认饮品偏好是无糖乌龙茶` is first materialized, then its actual
public typed target is revised with unchanged ID/new revision. Quoted and negative
commands retain the old value and issue zero action grants. Host slot aliases and
unsupported forms are enumerated in [CONTRACT](CONTRACT.md#chinese-product-wording-2026-09-05-refinement).
Chinese full-sentence retrieval first failed to select a candidate; old-value extraction
is now a retrieval hint only. It cannot grant intent.

Coverage includes missing action authority→NEEDS_USER_CONFIRMATION, exact REVISE,
original S1 unchanged, close/reopen, response-before-envelope recovery after recall
context expiry with zero resend, committed public mutation receipt replay after
reopen/authority expiry with no extra revision, wrong candidate/quote/slot/qualifiers,
CREATE bypass, same-slot/cross-alias ambiguity, unknown Chinese slot, ordinary/quoted/
hypothetical/negative/historical statements, actual memory suppression after recall,
and late suppression between first check and delegate (delegate0, failed, not unknown).
A forced SDK internal apply→job-finalize crash has not been injected; committed public
receipt replay and Host response recovery are separate verified boundaries. No claim
that every fault point, arbitrary stale concurrent plan, or unrestricted NL AC is covered.

## Red evidence and adjacent compatibility

`original-compiler-red.log`: 1failed0.97s, exit1. An ignored pytest plugin loads the
exact base9ec0ec97 compile_operation, adapting only the added candidates keyword;
actual CREATE→recall→correction flow never enters REVISE/action authority (0 vs expected1).
This is an old-compiler counterfactual in the new harness, not an entire old-tree run.
The active implementation passes that case and the focused group.

An adjacent test initially failed solely on its four exact v1 version strings
(25passed/1failed); they now assert the four new v2 strings. Existing source/schema/
paraphrase/recovery assertions were retained. No testcase dropped or skipped.

## Command and raw evidence

From this tree, `PYTHONPATH=backend` and Python:
`/Users/denny/projects/simple_harness-primary-history/.local-test-evidence/2026-09-05/primary-history/venv067/bin/python`

```text
-m pytest backend/tests/memory/test_semantic_correction.py backend/tests/memory/test_analysis_response_unusable.py backend/tests/sdk_adapters/test_s5b_acceptance_matrix.py::test_analysis_executor_three_key_lookup_zero_second_provider_call backend/tests/sdk_adapters/test_s5b_acceptance_matrix.py::test_analysis_proposal_span_derivation_rejects_paraphrase -q -p no:cacheprovider
```

Raw files remain ignored in `.local-test-evidence/2026-09-05/semantic-correction/`:

| File | SHA-256 |
|---|---|
| original-compiler-red.log | 7c178af6aed6003919a3d3e6c4747414b7ea53991e7338103a5065420bee30ad |
| chinese-three2.log | 47bf13a973f0bebcbb31443898674bbbc8416c707b548daf90b14f7e6dc693ca |
| focused-final2.log | 9a2db2edf9ac39eb15df16a4987fbc4928e2c604fe343bbc317ecf2fda3cb543 |

Independent contract98f3ae6b review was limited ACCEPT; fixed source review is pending.
Chinese natural wording is a subsequent contract refinement. Original HM AC and native
remember/use/correct/forget loop remain required; production main integration is not claimed.

## Canonical naming follow-up and independent review

Dirac fixed800ff419 review: scoped ACCEPT, no blocking P0/P1; additional independent
disabled→enabled CREATE response recovery probe **2 passed1.38s**, zero new Provider.
Review report is local to primary-api tree:
`.local-test-evidence/2026-09-05/semantic-800ff419-review/REVIEW.md`,
SHA256 `3983ee235d6d3d66fcce20ead909ff97d7bc996229337a649825ae8d1b336c2d`.

A separate follow-up adds the supported-slot naming convention to the actual v2
system prompt. Chinese fixtures now use `drink_preference`, with a distinct existing
`preferred_drink` candidate for alias-collision rejection. Command: same Python and
`PYTHONPATH=backend`, `-m pytest backend/tests/memory/test_semantic_correction.py -k zh_ -q -p no:cacheprovider`.
**5 passed,15 deselected,5.01s,exit0**. The selector excludes the English cases;
no testcase was removed. `canonical-chinese.log` SHA256
`93b490a5c2051f0b40556528818a78a1ed38d3a406e96f46d623a098f493bd9e`.
These deterministic public-SDK tests verify the canonical slot's real mutation path,
not a real model following the new prompt. Alias/intent/authority code is unchanged.
# Production composition, 2026-09-05

Candidate integrated reviewed sources800ff419 and588c58ec as d0cce501/5238227b.
The main startup now uses `compose_human_memory_runtime`: one lazy action authority
is bound to both the analysis executor and the public Memory SDK builder. The
semantic regression harness uses this same production factory, with only the
clock/transport/public backend construction made deterministic; it no longer
injects an authority separately in its own builder.

Combined semantic20 + cognitive API7 + typed-only barrier2 first yielded26PASS,
3FAIL in23.58s. The three failures were a removed test import
`HostEvidenceAuthority` after the successful revision assertions. Restoring that
import and rerunning only those affected valid/recover/zh_valid scenarios yielded
3PASS,17deselected in3.28s. Original red output is retained. This covers29 distinct
cases across the two executions, not a single all-green29 run. New production
factory Ruff and main.py compile checks pass. Current installed067, deterministic
Provider/SQLite only; native model wording compliance and069 migration remain
separate pending gates.

Raw local evidence under `.local-test-evidence/2026-09-05/primary-candidate/`:

- `semantic-production-combined.log`: SHA256 a3a2891c38924a55e793916c9f1f5cd1b3a638c6e91757bda6383810552500e2
- `semantic-production-fixed-import.log`: SHA256 36a56e2ca2fb782d539789dfedb79b93188984c62a42b17de5870283c3a9a031
