<!--
SPDX-FileCopyrightText: 2026 DennyWanye
SPDX-License-Identifier: Apache-2.0
-->

# 任务编排接入说明（Python 应用）

`agent_orchestrator.host` 是外部 Python 应用使用任务编排的唯一入口：建任务 → 分层规划 → 执行 →
独立审阅 → 交付产物，全在 SDK 里。应用只提供模型配置、数据目录和一个身份。

这一版只支持 DeepSeek 模型（2026-10-19 用户决定）：编排的每个执行池都要一个经过认证的用量计数器，
目前只有 DeepSeek V4.1 有。别的模型没有计数器，宿主起不来，原因写在 `reason` 里。

## 1. 安装

SDK 没有发到 PyPI。在 SDK 目录构建安装包，再装到你的应用环境：

```bash
cd sdk/simple-harness-sdk
uv build
pip install "dist/simple_harness_sdk-<版本>-py3-none-any.whl[deepseek-tokenizer]"
```

`deepseek-tokenizer` 扩展装的是 DeepSeek 官方的计数配方（`deepseek-recipe`）。向量检索可选：另装
`onnxruntime`、`tokenizers`、`numpy`，并把 BGE-M3 INT8 模型目录经 `models_dir=` 传入；不装就按词检索。

要求 Python 3.11 以上。`pytest` 随 SDK 一起装：任务的 `file:` / `pytest:` 要求都在沙箱里用宿主自己的
解释器跑 pytest 核对，所以要装在同一个虚拟环境。沙箱（跑模型写的代码、核对要求）目前只在 macOS 上可用；
别的系统宿主照常启动，只是不跑模型写的代码、要求也核对不了。

## 2. 最短程序

```python
import asyncio
from pathlib import Path

from agent_orchestrator.host import ModelConfig, OrchestrationHost, Principal
from agent_orchestrator.host.tokenizer import ensure_deepseek_tokenizer


async def main() -> None:
    tokenizer = ensure_deepseek_tokenizer(Path("./deepseek-v41-tokenizer.json"))  # 没有就下载
    host = await OrchestrationHost.open(
        Path("./orchestration-data"),
        principal=Principal("user-1", "我"),
        model=ModelConfig(
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            api_key="<你的密钥>",
        ),
        tokenizer_path=tokenizer,
        auto_mode=True,  # 自动授权每轮规划、自动确认纯内容要求
    )
    try:
        receipt = host.create_mission(
            {
                "goal": "写一份简短的中文说明，介绍三种家庭省电办法，保存为 NOTES.md。",
                "success_criteria": ["file:NOTES.md", "NOTES.md 列出三种办法，每种一句话"],
                "idempotency_key": "notes-1",
            }
        )
        final = await host.run_until_settled(receipt["mission_id"])
        mission = final["snapshot"]["mission"]
        print(mission["status"], mission.get("stop_reason"))
        for artifact in final["snapshot"]["artifacts"]:
            print(artifact["path"], host.artifact_read(artifact["id"])["content"])
    finally:
        await host.close()


asyncio.run(main())
```

完整可运行的例子在 [`examples/orchestration-host/demo.py`](../examples/orchestration-host/demo.py)；
加 `--scripted` 不联网也能跑通（用 SDK 自带的脚本化模型）。

## 3. 入口做了什么

`OrchestrationHost.open(root, ...)` 按产品（桌面端）的接法把编排拼起来：

| 步骤 | 说明 |
|---|---|
| 实例锁 | 一个数据目录同一时刻只许一个进程用（文件锁，进程死了内核释放） |
| 部署验收门 | 安装包里的部署清单必须是 `VALIDATED`（发版脚本写的）；`require_acceptance=False` 可跳过，只用于测试 |
| 发布连接器 | `HostSettings(publish_dir=...)` 给了存在且支持硬链接的目录，任务才能带 `action:file_publish.publish:` 要求 |
| 沙箱探测 | 每次启动重新证明隔离；过了才给 `run_tests` 工具与 `pytest:` 要求 |
| 执行池 | 每个上下文档（256K / 512K / 600K）一个池，开思考时另有思考池；每池一个认证计数器 |
| 任务部署 | 规划世界（`deployment.goal_world`）、执行图、保证通道（独立审阅） |
| 门面 | 所有命令走 `MissionControlV1`，身份固定为 `principal`，请求体永远不能冒名 |

启动后 `host.state` 是 `available`；`host.status()` 给出模型、沙箱、发布、运行档、预算默认与全局月配额。

## 4. 操作一览

| 要做的事 | 方法 |
|---|---|
| 建任务 | `create_mission({"goal", "success_criteria": [...], "idempotency_key", "budget"?, "runtime_profile_id"?})` |
| 建任务并附资料 | `create_mission_with_sources({"mission": {...}, "sources": [{"path", "content", "kind", "idempotency_key"}]})` |
| 资料登记／换版本／撤回 | `source_command("register"/"supersede"/"revoke", body)` |
| 列表／详情／事件 | `missions(limit=)`、`snapshot(mission_id)`、`events(mission_id, after_seq=, limit=)` |
| 等人的事 | `pending_planning_authorizations()`、`approvals(mission_id)`、快照里的 `planning_questions` |
| 授权本轮规划 | `planning_authorization({"operation": "issue", "request_id": ...})`（自动模式下 SDK 代签） |
| 回答规划提问 | `answer_planning_question({"decision_id", "answer", "expected_version", "nonce"})` |
| 人工批准 | `decide(request_id, "approve"/"reject", reason=..., note=...)` |
| 确认完成要求 | `approve_operation_completion_spec(body)`（自动模式下纯内容要求由 SDK 代签） |
| 改要求 | `amend_requirements({"mission_id", "changes": [{"op": "add"/"rewrite"/"remove"/"goal", ...}]})` |
| 取消／接管 | `cancel(mission_id)`、`takeover(task_id, action, basis=)`、`resolve_unknown(action_key, outcome, basis=)` |
| 取产物 | `artifact_read(artifact_id)`：只认产物编号，读前重算哈希，正文最多 256 KB |
| 驱动 | `run_until_settled(mission_id, timeout=)`（跑到结束）、`run_forever()`（常驻）、`run_once()` / `drain()`（跑到空闲） |

被拒绝的请求抛 `HostRequestError`，`code` 稳定：`invalid_request`、`not_found`、`conflict`、`secret_rejected`、
`local_tests_disabled`、`action_criteria_disabled`、`orchestration_unavailable`、`orchestration_degraded`、
`sdk_refused` 等。

## 5. 要求怎么写

- 每行一条，普通文字就行；规划器按它拆步骤，审阅员按它审。
- `file:路径`：工作区里要有这个文件。
- `pytest: 路径`：在沙箱里跑测试（需要沙箱可用）。
- `action:file_publish.publish:路径`：完成后发布到授权目录；入口自动补一条 `file:路径`。
- 请求里任何像密钥的文本都会被拒（`secret_rejected`）。

## 6. 等人的时候

人要把关时用 `auto_mode=False`（或传一个每轮现读的函数）。这时规划每轮要 `planning_authorization`、
纯内容要求要 `approve_operation_completion_spec`、规划器的提问要 `answer_planning_question`。
`run_until_settled` 等人时也在等，所以把关要在另一个协程里做，或者改用 `run_once()` 自己轮。

## 7. 进度

SDK 没有推送。定时调 `events(mission_id, after_seq=上次的 through_seq)` 读新事件，或读 `snapshot`。
任务结束时 `notify=` 回调收到一条通知（至少一次）。

## 8. 数据目录

`root` 下有编排库 `orchestrator.db`、执行账本、每次尝试的隔离工作区、产物内容、沙箱探测报告与
转发线路学到的计数边际。目录不要放在符号链接下，也不要和发布目录重叠。

## 9. 常见问题

| 现象 | 原因 |
|---|---|
| `HostUnavailable: 编排只支持 DeepSeek…` | 模型名不是 `deepseek-flash` / `deepseek-v4.1-flash`，或主机既不是官方也没写进 `compatible_hosts` |
| `另一个实例正在使用编排目录` | 同目录已有进程在用 |
| `执行图部署验收门没有通过` | 安装包不是发版脚本产出的；重新按 `docs/build-and-release.md` 构建 |
| `DeepSeek tokenizer bytes differ…` | 分词文件不是钉死的那份；用 `ensure_deepseek_tokenizer` 重新取 |
| `DeepSeek 端点缺钉死的分词文件…` | 配了 DeepSeek 但没传 `tokenizer_path`；先 `ensure_deepseek_tokenizer` 再传 |
| 任务停在"等授权" | `auto_mode=False` 且没人调 `planning_authorization` |
