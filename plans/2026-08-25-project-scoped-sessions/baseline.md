# 实施前基线（2026-08-25）

## 范围与版本

- 代码基线 HEAD：`91d22247947c152c1bf5393a840553b6172628cc`
- 分支：`main`（项目约定直接开发）
- 测试文件：603；按仓库根 `baseline-shards.json` 的 17 个 shard 执行。
- 原始状态：`.local-test-evidence/2026-08-25/project-scoped-sessions-baseline/baseline-state.json`
- 原始日志：`.local-test-evidence/2026-08-25/project-scoped-sessions-baseline/logs/`
- 已有无关未跟踪文件 `backend/vendor/simple_harness_sdk-0.6.1-py3-none-any.whl` 不属于本 release，保持未改。

## 命令

```bash
backend/.venv/bin/python scripts/baseline_runner.py \
  --config baseline-shards.json \
  --run-dir .local-test-evidence/2026-08-25/project-scoped-sessions-baseline \
  --known-failures baseline-known-failures.json \
  --heartbeat-seconds 20

# backend-m-r 暴露新的既有失败后，向用户如实报告并独立复现，再登记精确 fingerprint 续跑：
backend/.venv/bin/python scripts/baseline_runner.py \
  --config baseline-shards.json \
  --run-dir .local-test-evidence/2026-08-25/project-scoped-sessions-baseline \
  --known-failures baseline-known-failures.json \
  --resume --accept-current-failures --heartbeat-seconds 20
```

## 结果

- 14 shards PASS：backend a/b/c/d-f/g-l/s-z、capabilities、companion、SDK adapters、frontend vitest、
  frontend typecheck/build、Rust test/check。
- 3 shards 为精确登记的实施前既有红；本 release 的回归门要求其 fingerprint 不变且不得新增红：
  1. `backend-m-r`：`backend/tests/test_process_list_error.py::test_process_list_with_query`，查询 python 时
     返回至少一个名称不含 python 的进程；独立复跑仍失败。fingerprint
     `2cafccf058b55f9b8196a314206a00cbed14dac610ebfbbc1c0cd5c2503add0f`。
  2. `root-tests`：`tests/e2e/test_chat_flow.py` 缺 `secret` fixture/async pytest driver，原有 fingerprint
     `3fcba2d77f91b5e5567d88d474c866d0c9be68990b0ad8dad522ba970668e0db`。
  3. `frontend-lint`：仓库既有 React hooks/explicit-any/refresh 等 lint 债，原有 fingerprint
     `2ba1535992aa9b9c3446f177d5fda2ba40c31ddfd0db487e162e6e0a863551d6`。

## 判定

基线不是字面全绿，而是“14 PASS + 3 个已披露且冻结 fingerprint 的既有红”。它们均先于本功能实现，
不得在交付时归为本功能回归，也不得用来掩盖任何新增失败。受影响测试、类型检查、构建和真实 UI 仍须全绿。
