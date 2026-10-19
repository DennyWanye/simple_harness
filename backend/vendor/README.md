# 内嵌 SDK 轮子

这里只放当前在用的 Harness SDK 候选轮子和它的候选清单，各一个文件。

- 身份（版本号、轮子与清单的 SHA-256、源码提交）只写在 `deskpet/sdk_adapters/sdk_candidate.py`；
  启动时按它逐字节核对，对不上就拒绝启动。
- 换版本只走 `scripts/release_sdk_opt.sh <旧 opt 号> <新 opt 号> ...`：它构建新轮子放进这里、
  删掉旧的一对、改 `sdk_candidate.py` 与 `pyproject.toml`、跑钉版测试。
- 2026-10-19 依赖清理：Service SDK（`simple-harness-service-sdk`）已不再使用，轮子删除；
  历史版本的轮子一并删除，需要时从 git 历史取。
