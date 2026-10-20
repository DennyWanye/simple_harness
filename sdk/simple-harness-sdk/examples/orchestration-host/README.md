<!--
SPDX-FileCopyrightText: 2026 DennyWanye
SPDX-License-Identifier: Apache-2.0
-->

# 任务编排接入示例

一个和桌面端无关的 Python 程序，只装 `simple-harness-sdk`，用 `agent_orchestrator.host` 跑完一个任务
（写一份 NOTES.md）。接入说明见 [`docs/orchestration-host.md`](../../docs/orchestration-host.md)。

```bash
# 不联网：脚本化模型，只证明接线
python demo.py --scripted

# 真实模型（DeepSeek）
SH_BASEURL=https://api.deepseek.com/v1 SH_APIKEY=... SH_MODEL=deepseek-flash \
SH_TOKENIZER=./deepseek-v41-tokenizer.json python demo.py --root ./orchestration-data
```

经转发线路（本机网关）时加 `SH_COMPATIBLE_HOSTS=127.0.0.1`；`http://` 端点自动放行私网明文。

其它例子（都不联网，用脚本模型）：

```bash
python tools_readonly.py   # 注册一个只读工具
python tools_async.py      # 异步工具：抛错一次、超时一次、再成功
python tools_effect.py     # 应用连接器：自动模式代批执行一次；"总要人批"的操作等人点批准
```
