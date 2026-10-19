<!--
SPDX-FileCopyrightText: 2026 DennyWanye
SPDX-License-Identifier: Apache-2.0
-->

# Simple Harness SDK

Simple Harness 桌面产品内嵌的两层运行时，可以单独装进别的 Python 应用：

| 包 | 做什么 | 接入说明 |
|---|---|---|
| `simple_harness` | 智能体运行：模型调用循环、工具执行、授权、SQLite 持久化、崩溃后恢复 | [`docs/quickstart.md`](docs/quickstart.md)、[`docs/integration-guide.md`](docs/integration-guide.md) |
| `agent_orchestrator` | 任务编排：建任务 → 分层规划 → 执行 → 独立审阅 → 交付产物；入口 `agent_orchestrator.host` | [`docs/orchestration-host.md`](docs/orchestration-host.md) |

可运行的例子：[`examples/minimal-consumer`](examples/minimal-consumer)（智能体运行）、
[`examples/orchestration-host`](examples/orchestration-host)（任务编排，`--scripted` 不联网也能跑）。

## 安装

没有发到 PyPI。在本目录构建安装包，装到你的应用环境：

```bash
uv build
pip install "dist/simple_harness_sdk-<版本>-py3-none-any.whl[deepseek-tokenizer]"
```

版本号在 `src/simple_harness/version.py`。构建、冻结、验收与分发的正式流程见
[`docs/build-and-release.md`](docs/build-and-release.md)。

## 边界

- 应用只许从 `simple_harness/public_api.py`、`agent_orchestrator/public_api.py` 列出的公开模块导入；
  别的模块是内部实现，随时会变。
- 任务编排这一版只支持 DeepSeek 模型（每个执行池要一个认证用量计数器）。
- 架构事实与持久化边界在 [`ARCHITECTURE/index.md`](ARCHITECTURE/index.md)；一致性测试在
  [`docs/conformance.md`](docs/conformance.md)。

License: Apache-2.0（见 `LICENSES/`、`NOTICE`）。
