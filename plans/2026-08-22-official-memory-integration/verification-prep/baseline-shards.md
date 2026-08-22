# 大仓 baseline shards 引用

复用仓库根 `baseline-shards.json` 与 `scripts/baseline_runner.py`，不在本轨复制或重写分片。

## Required shard groups

- Backend 字母分片：`backend-a`、`backend-b`、`backend-c`、`backend-d-f`、`backend-g-l`、
  `backend-m-r`、`backend-s-z`。
- Backend 专项目录：`backend-capabilities`、`backend-companion`、`backend-harness-simplification`、
  `backend-sdk-adapters`、`root-tests`。
- Frontend：`frontend-vitest`、`frontend-typecheck`、`frontend-lint`、`frontend-build`。
- Rust：`rust-test`、`rust-check`。

## 判定

- phase-2 HEAD/命令/exit/耗时与既有 failure fingerprint 是回归比较基准。
- 新引入失败必须修复；已有 failure 只能按相同 fingerprint 识别，不能笼统写“基线本来就红”。
- exact wheels 接入后至少跑 affected + critical；因启动装配、共享 runtime 与正式 release 均受影响，
  最终还需完整 baseline shards + FULL_SURFACE_SMOKE。
- runner 日志与 state 属原始证据，放 `.local-test-evidence` 或 gate run 的忽略目录，不提交 Git。
