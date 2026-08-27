# 执行前基线（2026-08-28）

- HEAD: `362e51496d06fe14f8cfdc1909f25381ee427e3b`
- Runner: `/Users/denny/.codex/skills/plan-test/scripts/baseline_runner.py`
- Manifest: `baseline-shards.json`
- State: `.local-test-evidence/2026-08-28/chat-skill-install-baseline/state.json`
- 结果：17 片中 15 PASS，2 known-red，0 new-fail。
- known-red：`root-tests:exit=1`、`frontend-lint:exit=1`，均命中执行前
  `baseline-known-failures.json` 既有签名；不把它们冒充为本次引入。
- Backend alpha shards、Capability、Companion、SDK adapters、Vitest、TypeScript、frontend build、
  Rust test/check 全绿。
- 首次 runner 因 state 目录未预建，在 `backend-a` PASS 后以 `FileNotFoundError`
  退出；建立 ignored evidence 目录后重跑完成，该失败是 runner 输出目录前置条件，
  不是应用测试失败。
