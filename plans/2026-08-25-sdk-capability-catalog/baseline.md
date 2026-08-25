# 实施前基线（2026-08-25）

## 身份

- Host HEAD：`3c678e71d811b0951620dbff9fb0b3d9e6989e67`；工作树包含用户此前已要求的模型选择 UI 改动、
  本任务 acceptance/architecture/plan/oracle，不将这些现有改动误认成本任务实现。
- SDK source HEAD：`f4fd99cd68f5a5cccf80f13bc33161a256f30c74`；只修改了本任务架构文档，尚未修改 SDK 业务代码。

## SDK full

- 命令：`cd /Users/denny/projects/simple-harness-sdk && uv run pytest -q`
- 结果：`1444 passed, 2 skipped, 14 warnings`，exit 0，24.67s。
- `uv run ruff check src tests`：PASS。
- `uv run mypy src/simple_harness/runtime src/simple_harness/execution src/simple_harness/tools`：当前基线
  17 个既有类型错误；本任务新增/修改的公共 API 与 release-owned 文件必须 focused mypy 零新增，最终仍以
  同一 broad 命令的错误签名比对，不把既有红宣称为绿。

## Host 分片 baseline

- 命令：`python3 scripts/baseline_runner.py --config baseline-shards.json --run-dir
  .local-test-evidence/2026-08-25/sdk-capability-catalog/baseline-host --known-failures
  baseline-known-failures.json --heartbeat-seconds 30`；既有失败确认后按 runner 正式入口使用
  `--resume --accept-current-failures` 记录签名并完成剩余分片。
- 原始 state：`.local-test-evidence/2026-08-25/sdk-capability-catalog/baseline-host/baseline-state.json`。
- 结果：15 shards passed，2 known-failure，0 unexpected-failure。
- 既有失败：
  - `root-tests`：`tests/e2e/test_chat_flow.py` 缺 `secret` fixture 且 async test 未配置插件；不是本次改动引入。
  - `frontend-lint`：仓库既有全局 ESLint debt（含 `App.tsx`、多个 code-panel/components 文件的
    `no-explicit-any`、Fast Refresh、hooks 规则等）；当前功能涉及的 lint 需用 focused lint/新增代码零新红
    与最终相同签名比对，不能把全局红宣称为绿。
- 已通过：backend a-z、capabilities、companion、SDK adapters、Vitest、TypeScript、frontend build、
  Rust test、Rust check。
