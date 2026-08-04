# Execution results

> Updated: 2026-07-19
> Status: COMPLETE — T11/T12/T13 and all test/UI gates passed; the release identity fixture is included in the controlled release commit.

## 2026-07-19 repository consolidation gate

- All useful production, test, architecture, plan and testcase changes from the
  former worktrees were retained in one staged closure. Patch-equivalent extra
  worktrees, their merged local branches, caches, generated reports, runtime
  userdata and unrelated mesh/GPU drafts were removed; only `master` and the
  main worktree remain.
- Frontend Vitest passed `822` tests, `npm run build:relay` passed TypeScript and
  Vite production compilation, and `cargo check` passed.
- The final backend full-order run reached `5312 passed, 16 skipped` with one
  process-global image endpoint resolver isolation failure. The test fixture now
  clears that resolver before and after every case; the collection-order
  reproduction (`test_main_task_scope_wiring` + the complete image config suite)
  passed `20 passed`. The original 13-failure repair set passed `28 passed,
  1 skipped` after its timing, override, migration and integration-fixture
  contracts were corrected.
- Three independent read-only audits passed code/import closure, architecture
  consistency and retention/credential hygiene. `git diff --cached --check`,
  PowerShell parser validation and v4/v5 workflow imports passed.

## 2026-07-19 GLM-5.2 1M context verification

- Both the relay alias `sf-glm-5.2` and canonical id `zai-org/GLM-5.2` are
  temporarily pinned to a 1,000,000-token nominal context in the per-model
  runtime table. The effective ceiling is 950,000 and compaction starts at
  750,000; provider-advertised metadata remains the intended long-term owner.
- Isolated model/context regression passed `58 passed`; `py_compile` passed.
- Fresh source-Tauri startup resolved `sf-glm-5.2` at 1,000,000 tokens, and the
  real Context usage dialog showed `0 / 950,000`, `compact @ 750k`, and
  `sweet @ 384k`.
- Computer Use submitted SC-STATS-2 through the real UI. Run
  `8109026887b64b309723c004e35c85af` completed under `deep_research@v6` with
  all four relay requests on `sf-glm-5.2` returning HTTP 200, with no DeepSeek
  outbound and no HTTP 402. SessionDB and delivery
  records contain one final answer (`140828 万人`, `954 万人`, NBS citation),
  one artifact, and all required durable deliveries completed in one attempt.
- Incremental evidence is in [`evidence/glm52-live-20260719/`](./evidence/glm52-live-20260719/).

## 2026-07-18 final completion

- New research runs default to immutable `deep_research/v6`; v1-v5 remain historical/recovery compatibility paths.
- `relay-cloud` defaults to `deepseek-v4-pro`; startup and real chat resolved a 1,000,000-token context and selected that model. HTTP 402 is external insufficient-balance state, not a routing failure.
- Final release-identity completed run `e58b02815a514577a2a9df2789c6978f` returned localized `140828 万人` and `954 万人` with exactly-once delivery; three earlier healthy-network runs (`492bdf52...`, `7eb29c9f...`, `a895f7e8...`) remain the timing sample set.
- Final release-identity partial run `2ed15e0095ee41fa95e7bb04b297d652` published only 2019 births `1465 万人`, committed `answer_status=partial`, and did not invent total population.
- After restart, real generate-now run `eeab90bab2d64cf7ab076a0d6514f765` received a genuine double click; its sole command `9582bb5c288aec6bbaf452fb88405c6e1527629b6ebde7713ef7292fdd8d046c` traversed accepted/observed/settled/consumed and produced one insufficient-evidence terminal answer.
- Same-userdata restart (`tauri-final14.stderr.log`) restored the final-identity partial session, reported `recovered_deliveries=0`, and left no duplicate cards, deliveries, or active historical controls.
- Timing: end-to-end p50 17099.262ms, p95 18942.344ms, max 19147.131ms; page p50 10976.872ms, p95 11405.159ms, max 11452.746ms; zero timeout/cancel/budget violations. See [`timing-calibration.json`](./timing-calibration.json).
- Release gates: backend `815 passed`; frontend `822/822 tests passed`; TypeScript/Vite production build passed; Rust `73 passed` and cargo check passed. Focused v5 recovery evidence is `18 passed`; v6 continuation/recovery evidence is `26 passed`.
- Evidence: [`evidence/t13-release-20260718/`](./evidence/t13-release-20260718/).

The sections below preserve earlier Q1/A2 checkpoints as execution history; their former “remaining” statements are superseded by this final section.

## 2026-07-18 Q1 vertical-slice gate

The approved quick-validation slice is now green for the original scenario:

`深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。`

- Real source-Tauri run: `1b91f6d7a6044cfaad6f9b9b909485b0`, `deep_research@v6`, `completed` in 5.08s.
- Answer: 2024 year-end population `140828万人`; 2024 births `954万人`.
- Citation: the fetchable National Bureau of Statistics URL with the `www.stats.gov.cn` host.
- Timing: compile 58ms; official archive discovery 2.78s; target fetch about 1.10s; collect-pages 4.14s; extraction 153ms; assess/render 354ms.
- Exactly-once persistence after restart: 1 user message, 1 `final_assistant`, 1 `artifact_card`, 1 completed run, 0 non-delivered deliveries; startup reported `recovered_deliveries=0`.
- UI recovery: the message panel restores the artifact envelope as an ArtifactCard instead of raw JSON; the final answer remains visible below it after restart.
- Evidence: [`evidence/q1-20260718-r9/final-ui-before-restart.jpg`](./evidence/q1-20260718-r9/final-ui-before-restart.jpg), [`evidence/q1-20260718-r9/final-ui-after-restart.jpg`](./evidence/q1-20260718-r9/final-ui-after-restart.jpg), and the r9 `metrics.jsonl` / Tauri logs.

Automated gates after the final fixes:

- v6, official-source and fetch suites: `91 passed`.
- v5, continuation, recovery, runtime and delivery compatibility: `281 passed`.
- message/artifact frontend suites: `30 passed`.
- `npm run build:relay`: TypeScript and Vite build passed (warnings only).

Scope boundary: this proves Q1 only. T11/T12/T13 and the plan's later
iteration/optimization work were not executed. v5 remains the default; v6 was
activated only through the isolated development override.

## Completed implementation boundaries

| Boundary | Result | Evidence |
|---|---|---|
| Existing v5/workflow/delivery/timing foundations | PASS | 39 focused tests |
| Existing retrieval/effect foundations | PASS | 75 focused tests |
| T1 generic runtime registry, activation gate, workflow schema v4 | PASS | 95 focused tests, 1 existing warning |
| T2 strict v6 contracts and official-exact-fact compiler | PASS | 16 focused tests + compileall |
| Q1 scalar extraction and deterministic report prototype | PASS (prototype only) | 6 focused tests; full T3/T4 contracts remain open |
| T5 official-source resolver and durable deadline primitives | PASS | 22 focused tests + py_compile |
| Cross-module v6 contracts/runtime/source/deadline | PASS | 47 tests in one process |
| Q1 ref-only native graph skeleton | PASS (offline only) | ref-only checkpoint/native projection tests; semantic T4/T8 gates remain open |
| Workflow registry registration of v6 | PASS | 18 graph/bootstrap/dependency/runtime tests |

## Remaining production gates

Q1 now uses registered body/locator refs, exact fact-batch provenance closure,
integrity-gated terminal commit, durable delivery, source-Tauri UI input, and a
same-directory restart proof. These no longer block the Q1 slice.

The overall v6 plan is intentionally incomplete: later scenario families,
iteration/optimization work, and default-version promotion remain outside this
quick-validation delivery. The completion audit must remain red for the full plan
until those explicit work items are executed and verified.

## 2026-07-18 T11/T12 A2 rework checkpoint

Green baseline before extension: the currently collected v6 contract/compiler/evidence/runtime/Q1/source/fetch suites passed `79 passed in 11.81s` (the earlier handoff's `91` used a broader command; this command's collection is 79 and had no failures).

Reusable implementation spikes completed before the production-integration audit:

- four intent compilers plus fan-out route policy: `24 passed` in the combined compiler/contracts/fan-out slice;
- generic deterministic assessment and typed report renderers: assessment/report suites passed independently (`13` and `17` tests), and the combined pure T11 contract run passed `46 passed in 1.20s`;
- provider-neutral retrieval lanes plus exact-source backward compatibility: runtime/official-source/fetch slice passed `37 passed in 5.30s`;
- Q1 evidence composite identity/sort regression remained green (`15 passed`).

These results are deliberately classified as spikes, not T11 completion. The A2 production audit found that the active v6 graph still routed only the Q1 exact-fact path, had no durable generic candidate/fact/inference source, discarded route provenance before admission, and had no v6-specific durable LLM effect identity/budget path. The T12 audit also found hard-coded v5 repository/service behavior, caller-key-derived child identity, no continuation head operations, incomplete server-side closure validation, undefined 900-second semantics, and no exact fault/recovery/retention algorithm.

The live-code spikes established two planning facts: `DurableResearchCallEffectAdapter` currently emits the hard-coded v5 policy `deep-research-v5-llm-at-most-once` and tool spec `deep-research-v5-llm-v1`, so v6 must use an explicitly parameterized profile while preserving v5 prepared bytes; UUIDv5 over the fixed continuation namespace plus canonical `{parent_run_id,policy_version}` is deterministic and excludes caller keys. `plan.md` T11/T12 and `v6-contracts.md` §§6.2–6.3 were reopened to freeze those production contracts before Phase 3 resumes.

Phase 2 rework then ran 13 incremental challenger rounds (within `MAX_ROUNDS=15`). Rounds 1–9 and 12 exposed and closed code-level ambiguity; the final focused T11 review (Round 10), focused T12 review (Round 11), and final integration review (Round 13) all ended `VERDICT: PASS`. The revised plan is intentionally left `draft/A2-rework-awaiting-user-review`; no further production implementation is authorized by the skill until the user reviews the materially expanded contract.
