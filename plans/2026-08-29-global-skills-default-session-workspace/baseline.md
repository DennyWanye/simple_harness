# Phase 2 基线

- Evidence root: `.local-test-evidence/2026-08-29/global-skills-default-workspace-baseline/`
- Runner: `backend/.venv/bin/python scripts/baseline_runner.py --run-dir ... --known-failures verification/baseline-known-failures.json --resume`
- Result: 13 shards passed; 4 pre-existing failures were reproduced and fingerprinted before feature implementation.
- Passed: backend alpha shards except recorded `backend-m-r`, capabilities, companion, frontend Vitest/typecheck/build, Rust test/check.
- Existing failure `backend-m-r`: `test_process_list_with_query`, reproduced 3/3 and unrelated to this scope.
- Existing failure `backend-sdk-adapters`: eight failures, seven caused by the already-dirty manifest advertising `skill_install` without a registered handler and one conformance error; reproduced 3/3. This task must eliminate the Skill-related failures rather than preserve them.
- Existing `root-tests`: repository root has no collected tests under its configured command.
- Existing `frontend-lint`: 169 repository-wide problems (163 errors, 6 warnings) on the dirty entry state; task-touched files may not introduce additional lint errors.
- Environment repair only: `uv sync --project backend --frozen --extra dev` restored lock-pinned SDK wheels after local venv drift; no product source was changed by that repair.
- Authority: `baseline-state.json` and per-shard logs are ignored raw evidence. `verification/baseline-known-failures.json` is task-local and does not modify the repository-wide known-failure registry.
# 2026-08-29 Auto Skill 安装 / Run 收敛增量基线

- 命令：`backend/.venv/bin/python -m pytest -q backend/tests/sdk_adapters/test_tool_authority.py backend/tests/capabilities/test_project_skill_install_service.py backend/tests/capabilities/test_global_skill_install.py backend/tests/test_permission_mode_migration.py backend/tests/test_extreme_scenarios.py`
- 结果：`71 passed, 1 warning in 37.41s`。
- 已知缺口：现有测试未覆盖 Auto policy 下 `skill_install` 必须 stage intent/生成自动批准 receipt，也未证明 frontend forced reset 会收敛 durable SDK Run；现场干净实例已复现这两条缺口。
