# Baseline: Durable Graph Workflows And Trace

Recorded on 2026-07-10 before workflow runtime implementation. Existing failures are regression comparison points, not waivers for new failures.

## Backend

- Command: `backend/.venv/Scripts/python.exe -m pytest`
- Result: **3730 passed, 9 failed, 14 skipped, 10 deselected** in 708.24s (3753 selected).
- Existing failures:
  - `test_agent_loop_compaction_wiring.py::test_context_manager_compact_at_overrides_static_compressor_threshold`
  - `test_agent_loop_compaction_wiring.py::test_real_usage_feedback_overrides_low_estimate`
  - `test_agent_parallel.py::test_two_subagents_run_concurrently`
  - `test_hybrid_router.py::test_router_with_real_providers_routes_to_local_when_healthy`
  - `test_openai_compatible.py::test_integration_ollama_v1_roundtrip`
  - `test_outcome_verifier.py::test_t10_4_git_diff_skipped_when_not_git_repo`
  - `test_p4s22_web_search.py::test_web_search_parses_results`
  - `test_p4s22_web_search.py::test_web_search_caps_at_max_results`
  - `test_tools_emit_artifacts.py::test_e2e_ppt_artifacts_in_envelope`
- Environment-sensitive causes observed include an unavailable/incompatible local Ollama endpoint, Git absent from the test process PATH, live search behavior, and a PPT artifact integration fixture. The compaction and concurrency failures are also present before this feature.

## Frontend

- Vitest: **71 files passed, 753 tests passed**.
  - Command: bundled Node running `node_modules/vitest/vitest.mjs run`.
- TypeScript: **passed**.
  - Command: bundled Node running `node_modules/typescript/bin/tsc -b`.
- Production build: **passed** (1385 modules transformed).
  - Command: bundled Node running `node_modules/vite/bin/vite.js build`.
  - Existing warnings: ineffective dynamic import for `edgeWatcher.ts` and a chunk over 500 kB.
- ESLint: **failed with 824 problems (815 errors, 9 warnings)**.
  - A large portion comes from generated JavaScript under `src-tauri/target/**` being scanned.
  - Existing source findings include hook state updates in effects, explicit `any`, refs read during render, and unused values.

## Rust

- `cargo check --manifest-path tauri-app/src-tauri/Cargo.toml`: **passed** in 1m 03s.
- Existing warning: `src/paths.rs::resolve_with` is unused.

## Regression Rule

- New focused workflow tests must be green.
- Existing suites must return to at least the status above, with no new failure attributable to this implementation.
- TypeScript and production build must remain green.
- Rust check and real UI tests are required before final completion.
