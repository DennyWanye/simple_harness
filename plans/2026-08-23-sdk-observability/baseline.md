# 执行前绿色基线

日期：2026-08-23

| 仓库 | HEAD | 命令 | 结果 |
|---|---|---|---|
| simple-harness-sdk | `8b5c66581260dca682bc5781ab7af56f76144bb1` | `uv run pytest -q` | `1379 passed, 2 skipped, 14 warnings in 26.02s` |
| simple-harness-memory-sdk | `aaa49dbe27babe1e3a4af6f89d0bf06cff0080d9` | `uv run pytest -q` | `200 passed, 7 skipped in 10.77s` |
| simple_harness Host | `e08a397aef18b14028706fe9986332b542493233` | `cd backend && uv run pytest -q tests/sdk_adapters tests/test_diagnostic_memory_privacy.py tests/test_observability.py` | `207 passed in 11.89s` |

三个仓库在执行前均无代码脏改；Host 仅有本轮新建的计划/验收文档。当前没有已知失败签名。Host 完整分片基线与 surface smoke 在验证轨按项目 runner 执行。
