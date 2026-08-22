# Phase 2 绿色基线

<!-- baseline-status: locked-with-known-failures -->
<!-- locked-at: 2026-08-22 -->

## simple-harness-sdk

- HEAD: `869c76f2050b5f492b4edee68f4ce2400030b832`
- 规模：122 个测试文件，低于分片阈值。
- Python 3.11/3.12/3.13：各 `1325 passed, 2 skipped`；skip 均为需要 consumer host 参数的
  conformance 用例。
- build + provenance + exact-wheel install/import：PASS。
- ruff、mypy、source provenance、REUSE：PASS。
- 基线命令未改变 tracked working tree。

## simple-harness-memory-sdk

- HEAD: `87820fe2c4cdde21c3a9356ca461b93fe00aadcb`
- 规模：24 个测试文件、pytest collect 160，低于分片阈值。
- `uv run --frozen pytest -q`：`154 passed, 6 skipped`，PASS。
- ruff、build、README quickstart：PASS。
- `uv run --frozen mypy src tests`：既有红，`17 errors in 5 files`。
  - `Row | None` 被直接索引；
  - readonly DTO 与要求 settable attribute 的 `_RecallQueryLike`/`_IntentLike` Protocol 不匹配；
  - 测试中的字符串 enum 参数类型不匹配。
- 本 Program 不得新增 mypy 错误；因会直接改相关契约，目标是在对应 slice 内清零这些既有错误。
- 基线命令未改变 tracked working tree。

## simple_harness

- HEAD: `8d0741a7be447110b80f7df6c15c38d2788dd884`
- 规模：602 个测试文件，使用 `scripts/baseline_runner.py` + `baseline-shards.json`；总 shard
  duration `278.776s`。
- 14 个 shard PASS：backend 主分片合计 `5711 passed, 47 skipped, 1 deselected`；Vitest
  `617 passed`；TypeScript typecheck/build PASS；Rust `80 passed`，cargo check PASS。
- 3 个已登记既有失败：
  - `backend-harness-simplification` 指向已不存在目录，0 tests/rc4；
  - `root-tests` 缺 async pytest plugin，1 failed + 1 error；
  - `frontend-lint` 165 errors + 6 warnings，指纹与 known failures 一致。
- 1 个实施前 unexpected failure：
  `backend/tests/sdk_adapters/test_sdk_context_cutover_gates.py::test_sp2_prepared_snapshot_start_and_first_request_are_exact`。
  provider frozen message 含 `metadata={source: memory, trust: untrusted_data}`，execution persisted
  input message 丢 metadata；`173 passed, 1 failed`，指纹
  `1db4ddb3773fe249c83208b97f0de020b7424e7d32848d5ff3805140dde319fd`。
- 该 unexpected failure 落在 S1/S6 Context authority 范围，必须随正式改造修绿；不能算后续新增回归。
- 原始临时 runner state/log：`/tmp/simple-harness-baseline-20260822.aOVIUA`（非 Git 证据）。
- 基线命令未改变 tracked working tree，也未启动 UI/真实 LLM或读取凭据。

## 回归判定

- Harness SDK：不得低于全绿基线。
- Memory SDK：不得增加上述 17 个 mypy 错误，且计划触及的 Protocol/DTO 错误必须清零。
- simple_harness：不得新增失败；上述 Context metadata failure 必须清零。其余三个 known failure保留
  指纹比较，并在本 Program 可安全修复时一并清理。
