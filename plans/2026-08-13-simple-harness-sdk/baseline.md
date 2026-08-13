# Execution baseline — Simple Harness SDK extraction

> Baseline captured: 2026-08-14 (Asia/Shanghai)  
> Product HEAD: `122ec55989f8a77e023aeb44ba1b4dae1b694269`  
> `origin/main`: `122ec55989f8a77e023aeb44ba1b4dae1b694269`  
> Scope: execution-before-business-code baseline; finalized plan/architecture documents and baseline tooling are present, SDK implementation is not.

## Environment

- macOS ARM64
- Python `3.12.13` (`backend/.venv`; SDK clean-install matrix must separately cover required Python 3.11)
- Node `v25.9.0`, npm `11.12.1`
- cargo `1.95.0`, rustc `1.95.0`
- Test files discovered before sharding: `742`

## Runner and evidence

- Runner: `scripts/baseline_runner.py`
- Shards: `baseline-shards.json`
- Known-failure signatures: `baseline-known-failures.json`
- Authoritative state: `.local-test-evidence/2026-08-13/sdk-extraction-baseline-full/baseline-state.json`
- Raw logs: `.local-test-evidence/2026-08-13/sdk-extraction-baseline-full/logs/`
- Preflight run (used `--maxfail=10`, not authoritative):
  `.local-test-evidence/2026-08-13/sdk-extraction-baseline/`
- Raw state/log files are gitignored local evidence and must not be committed.

Canonical command:

```bash
backend/.venv/bin/python scripts/baseline_runner.py \
  --run-dir .local-test-evidence/2026-08-13/sdk-extraction-baseline-full \
  --resume --heartbeat-seconds 20
```

The authoritative complete run ended with `8 passed` shards and `9 known-failure` shards. A resume with the frozen signatures exits `0`; this means “no failure beyond the disclosed execution-before baseline”, not that the repository was fully green.

## Shard results

| Shard | Baseline result | Summary |
|---|---|---|
| backend-a | KNOWN FAILURE | 105 passed, 2 failed |
| backend-b | PASS | 53 passed |
| backend-c | KNOWN FAILURE | 471 passed, 4 failed |
| backend-d-f | KNOWN FAILURE | 1365 passed, 20 failed, 9 skipped |
| backend-g-l | PASS | 347 passed, 1 skipped |
| backend-m-r | KNOWN FAILURE | 1911 passed, 12 failed, 26 skipped |
| backend-s-z | KNOWN FAILURE | 1483 passed, 13 failed, 2 skipped |
| backend-capabilities | KNOWN FAILURE | 262 passed, 4 failed |
| backend-companion | PASS | 648 passed, 10 skipped |
| backend-harness-simplification | KNOWN FAILURE | 919 passed, 2 failed |
| root-tests | KNOWN FAILURE | 1 failed, 1 setup error; live backend fixture/service absent |
| frontend-vitest | PASS | 558 passed |
| frontend-typecheck | PASS | `tsc -b --noEmit` exit 0 |
| frontend-lint | KNOWN FAILURE | 160 errors, 5 warnings |
| frontend-build | PASS | `tsc -b && vite build` exit 0 |
| rust-test | PASS | 79 passed |
| rust-check | PASS | exit 0; existing warnings only |

Backend aggregate: 7564 passed, 57 failed, 48 skipped. These failures predate SDK business implementation and are not an allowance for new failures.

## Exact known backend/root failures

### backend-a

- `test_product_loop_factory_rehydrates_run_scoped_provider_and_fences`
- `test_pinned_agent_reach_channel_contract`

### backend-c

- `test_companion_mkdir_command_outside_workspace_rejected`
- `test_companion_write_outside_workspace_rejected`
- `test_path_normalization_forward_back_slash_same`
- `test_path_normalization_windows_casing`

### backend-d-f

- `test_end_to_end_three_layer_recall`
- `test_chunker_idempotent_re_chunk`
- `test_chunker_persist_long_multiple`
- `test_chunker_persist_single_short`
- `test_chunker_total`
- `test_llm_rewriter_strips_wrapping_quotes`
- `test_t0_7_write_into_temp_allowed`
- `TestP4WireIn::test_skills_list_reports_builtins`
- `test_deep_research_skill_mentions_source_packs_and_scrapling_first`
- four `test_read_rejects_escaping_paths[...]` cases
- two `test_write_rejects_escaping_paths[...]` cases
- `test_build_agent_ephemeral_falls_back_when_unset`
- `test_build_agent_ephemeral_uses_configured_model`
- `test_ts4_10_ci_sh_skips_strict_on_unrelated_change`
- `test_ts4_9_ci_sh_triggers_strict_on_retriever_change`
- `test_checked_manifest_is_canonical_and_current`

### backend-m-r

- `test_t5_4_short_query_is_rewritten`
- `test_t10_5_build_skipped_when_no_python_files`
- `test_clear_binding_removes_row`
- `test_tool_call_args_logged_with_length_and_parse_status`
- `test_specs_and_tauri_have_single_packaging_owner`
- seven `test_ppt_full_page_workflow.py` failures recorded in the machine state

### backend-s-z

- two `test_window_capture_rejects_non_workspace_relative_names[...]` cases
- four Windows native keyboard cases
- seven `test_workflow_eval_cli.py` cases recorded in the machine state

### backend-capabilities

- `test_platform_installs_missing_godot_detector_idempotently_and_invokes_it`
- `test_preparer_emits_real_hashed_fixtures_and_launch_environment`
- `test_preparer_is_idempotent_until_a_fixture_is_contaminated`
- `test_preparer_rejects_photo_outputs_or_stale_user_data`

### backend-harness-simplification

- `test_current_harness_state_has_an_explicit_bounded_expansion`
- `test_r45_migration_fixture_locks_current_adjustment`

### root-tests

- `tests/e2e/test_chat_flow.py::test_health`: no async pytest plugin/config at root invocation
- `tests/e2e/test_chat_flow.py::test_chat_flow`: required `secret` live-service fixture absent

### frontend-lint

- Existing full-tree lint reports 160 errors and 5 warnings, principally `no-explicit-any`,
  `react-hooks/set-state-in-effect`, and `react-refresh/only-export-components`. The exact output is local raw evidence; SDK changes may not increase this surface.

## Flaky observation

The preflight run observed `backend/tests/test_browser_use_tool.py::test_start_returns_running_then_done` fail, while the authoritative full run passed all 53 backend-b tests. It is recorded as an execution-before flaky observation, not added to the stable known-failure allowlist. A later recurrence must be diagnosed and may not be silently accepted.

## Baseline policy for execution

1. Any new failure node or changed known-failure fingerprint is a regression until proven otherwise.
2. Passing an existing failed test is welcome; the stable signature must then be removed rather than kept as an allowance.
3. SDK-specific suites must be fully green; this product baseline does not waive any SDK AC.
4. Before final completion, validation must target a clean committed HEAD and rerun every affected shard plus the full-surface smoke.
