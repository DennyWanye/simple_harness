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
pip install "dist/simple_harness_sdk-<版本>-py3-none-any.whl"
```

只装这一个包：pytest（核对任务要求）与 DeepSeek 计数配方（`deepseek-recipe`）随它一起装；钉死的
DeepSeek 分词文件在宿主第一次启动时自动下载到数据目录（哈希核对，之后不再下载）；也可以自己传
`tokenizer_path=`。向量检索可选：另装 `onnxruntime`、`tokenizers`、`numpy`，并把 BGE-M3 INT8 模型
目录经 `models_dir=` 传入；不装就按词检索。

要求 Python 3.11 以上。`pytest` 随 SDK 一起装：模型写的测试与核对代码在隔离区里用宿主自己的解释器跑，
所以要装在同一个虚拟环境。隔离区 macOS 用系统自带的 Seatbelt，Linux 用 bubblewrap（要先装，见 3a 节）。

## 2. 最短程序

```python
import asyncio
from pathlib import Path

from agent_orchestrator.host import ModelConfig, OrchestrationHost, Principal


async def main() -> None:
    host = await OrchestrationHost.open(
        Path("./orchestration-data"),
        principal=Principal("user-1", "我"),
        model=ModelConfig(
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            api_key="<你的密钥>",
        ),
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
| 沙箱探测 | 每次启动重新证明隔离（macOS Seatbelt / Linux bubblewrap，见 3a 节）；过了才给 `run_tests` 工具与 `pytest:` 要求 |
| 执行池 | 每个上下文档（256K / 512K / 600K）一个池，开思考时另有思考池；每池一个认证计数器 |
| 任务部署 | 规划世界（`deployment.goal_world`）、执行图、保证通道（独立审阅） |
| 门面 | 所有命令走 `MissionControlV1`，身份固定为 `principal`，请求体永远不能冒名 |

启动后 `host.state` 是 `available`；`host.status()` 给出模型、沙箱、发布、运行档、预算默认与全局月配额。

## 3a. 隔离区（macOS / Linux）

模型写的代码（`run_tests` 工具、`pytest:` 要求、核对代码）只在隔离区里跑。宿主每次启动都实际检查一遍：
读不到 HOME 和别处的临时文件、写不出工作目录、连不上宿主的端口与 Unix 套接字、碰不到宿主进程、
脱离进程树的后台进程会被收掉、输出与 CPU 有上限。全部通过才启用；结果在 `status()["sandbox"]`
（`ok`、`kind`、没通过时的 `reason`），也写在数据目录的 `sandbox-probe.json`。

| 系统 | 隔离方法 | 前提 |
|---|---|---|
| macOS | Seatbelt（`sandbox-exec`） | 系统自带 |
| Linux | bubblewrap：新的用户/进程/网络/IPC 命名空间，根目录为空且只读，只读挂解释器白名单；可写的只有本次副本和草稿目录（`/dev/shm` 也落在草稿目录，多进程能用） | 装 `bwrap`（0.8 以上更好）；内核允许普通用户开用户命名空间 |
| 其他 | 无 | — |

Linux 要点：

- Ubuntu 23.10 起（含 24.04）AppArmor 默认不让普通程序开用户命名空间，要装一份只放行 `bwrap` 的规则：
  `python -m agent_orchestrator.host.sandbox_doctor --apparmor-profile` 打印规则文件和安装命令（Ubuntu 24.10
  起系统自带同样的规则）。SDK 不改任何系统设置。
- 在 systemd 服务里跑可以带 `NoNewPrivileges=yes`（bubblewrap 不用 setuid）。
- 进程归属按进程命名空间：命名空间的第一个进程一结束，内核就把里面的进程全部结束；宿主被杀，
  `--die-with-parent` 让隔离区一起结束；重启后按"开机号 + 第一个进程启动时刻 + 命名空间"认领，对不上的不碰。
- 内存与进程数是软限制（运行中采样，超了就收掉）；CPU 时间与文件大小是硬限制。没用 cgroup。

在要部署的机器上先跑检查命令，它和宿主启动时做的是同一套检查，输出一份 JSON（系统、内核、C 库、
Python、bwrap 版本、用户命名空间相关设置、每一项检查结果），通过退出码 0：

```bash
python -m agent_orchestrator.host.sandbox_doctor
```

要换跑模型代码的解释器或加只读路径，用 `SandboxOptions`：

```python
from agent_orchestrator.host import OrchestrationHost, SandboxOptions

host = await OrchestrationHost.open(
    root,
    ...,
    sandbox=SandboxOptions(
        interpreter="/opt/app/venv/bin/python",  # 默认是宿主自己的解释器
        extra_read_paths=("/opt/app/deps",),     # 别放 HOME 或数据目录：检查会判不通过
        bwrap="/usr/bin/bwrap",                  # 默认在 PATH 里找
    ),
)
```

应用有自己的隔离方法时，可以传 `SandboxOptions(executor=...)`：对象实现
`agent_orchestrator.runtime.sandbox.SandboxExecutorPort` 且 `isolated=True`。SDK 对它做同一套检查，通过了才用，
不看它自己怎么说。网络一项检查的是连不上宿主的回环端口、Unix 套接字和本机局域网地址；能不能连到外网，
检查证明不了，由应用负责。它的重启后回收也由应用负责。

没有隔离区时宿主照常启动：`file:` 要求照样核对（直接看文件在不在）；`pytest:` 要求建任务时就被拒
（`local_tests_disabled`，原因带上）；模型拿不到 `run_tests` 工具。

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

## 4a. 注册应用自己的工具

应用把自己的**只读**能力（查设备状态、搜 NAS、列应用……）交给宿主，模型在执行步骤时就能调用：

```python
from agent_orchestrator.host import DomainTool, OrchestrationHost, ToolCallContext

async def device_status(arguments, context: ToolCallContext):
    # context 由 SDK 填写：mission_id、attempt_id、call_id（稳定调用号）、principal_id、tenant_id；
    # 模型参数里的同名字段不算数。用它做你自己的授权判断。
    status = await my_device_client.status(arguments["device"])
    return {"device": arguments["device"], "battery": status.battery}   # JSON 对象

tool = DomainTool(
    name="device_status",                      # 小写标识符；不能与内置工具同名
    schema={                                   # 参数的 JSON Schema；模型读的说明写在顶层 description
        "type": "object",
        "description": "读一台设备的当前状态（电量、网络）",
        "properties": {"device": {"type": "string", "description": "设备名"}},
        "required": ["device"],
        "additionalProperties": False,
    },
    invoke=device_status,                      # 普通函数（线程里跑）或 async def（事件循环上跑）
    timeout_seconds=30,
)
host = await OrchestrationHost.open(root, principal=..., model=..., tools=[tool])
```

| 规则 | 说明 |
|---|---|
| 只收只读工具 | `read_only=False` 在构造时被拒。有副作用的操作走连接器（有审批、去重号、对账），见下一版说明 |
| 注册时就拒 | 名字不合法、与内置工具或技能工具同名、重复注册、schema 不是对象或没写 `description`、超时不是正数 |
| 参数校验 | 模型给的参数先按 schema 校验，不合格不会调到你的函数 |
| 串行 | 同一任务内的工具调用一个接一个（每任务一把锁），任务之间并行 |
| 忙 | 上一次超时的调用还没结束、锁还占着：这次最多等 `timeout_seconds`，然后判"失败"（`tool_busy`），不发起调用 |
| 超时 | 记为"失败"（`tool_timeout`）交回模型，模型可以再调；**SDK 不自动重试**。async 工具会被取消；普通函数停不下来，锁等它真正结束才放（事件里 `still_running: true`） |
| 被重启打断 | 只读工具外部什么都没改，重启后这次调用按"失败"（`tool_interrupted`）交回模型，并补一条失败事件收尾，不会把步骤挂在"不确定"上 |
| 异常 | 记为"失败"，只记异常类型名，异常文字不回给模型、不进事件（可能带凭据） |
| 返回值 | 必须是规范 JSON 对象（列表别用元组，不要 NaN/Infinity，键必须是字符串），序列化后不超过 1 MB；否则记为"失败"（`tool_result_invalid`） |
| async 工具 | 跑在宿主的事件循环上：里面不要做阻塞操作（会卡住整个宿主，包括租约续期） |
| 换工具集合 | 同一数据目录换了工具集合照常启动；在途任务再调已删的工具会被拒并记 `ToolCallRejected` 事件，任务继续 |

每次调用在任务事件流（`events(mission_id, ...)`）里留下：

| 事件 | 何时 | 载荷 |
|---|---|---|
| `ExternalToolCallStarted` | 拿到锁、真正发起调用时（等锁时被取消或判忙的不算开始） | `tool`、`call_key`（= `context.call_id`）、`argument_keys` |
| `ExternalToolCallSucceeded` | 成功 | 上面几项 + `result`（32 KB 以内原样；更大时只留预览并 `truncated: true`）、`duration_ms` |
| `ExternalToolCallFailed` | 抛异常、结果不合格、超时、忙、被重启打断 | `error_code`（`tool_failed` / `tool_result_invalid` / `tool_timeout` / `tool_busy` / `tool_interrupted`）、`error_type`、超时时 `still_running` |
| `ExternalToolCallUnknown` | 这一步被取消时调用还在进行 | `error_code`（`tool_cancelled`） |
| `ToolCallRejected` | 没授权、参数不合格、预算用完 | `tool`、`reason`、`stage` |

事件按 `seq` 排序，`events(mission_id, after_seq=上次的 through_seq)` 续读；同一调用的事件共用 `call_key`，
可以拿它去重；同一调用号有多条结束事件时（极少见：记完结果后、交回模型前进程崩溃，重启对账又补一条），以 `seq` 最大的那条为准。`on_write` 只是"有新写入"的提醒，内容从事件续读。可运行的例子（用公开模块 `agent_orchestrator.host.offline` 的脚本模型，不联网）见
`examples/orchestration-host/tools_readonly.py`（同步工具）与 `tools_async.py`（异步工具：抛错一次、超时一次、再成功）。

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
| `取 DeepSeek 分词文件失败…` | 第一次启动要联网下载分词文件；或自己下载后传 `tokenizer_path` |
| 任务停在"等授权" | `auto_mode=False` 且没人调 `planning_authorization` |
| `status()["sandbox"]["ok"]` 为假，原因含 `bubblewrap is not installed` | Linux 没装 `bwrap` |
| 原因含 `AppArmor restricts unprivileged user namespaces` | Ubuntu 23.10+ 要装 bwrap 的 AppArmor 规则（见 3a 节） |
| 原因含 `kernel.unprivileged_userns_clone=0` 或 `max_user_namespaces=0` | 内核不许普通用户开用户命名空间，要系统管理员放开 |
| `local_tests_disabled` | 这台机器没有可用的隔离区，任务不能带 `pytest:` 要求 |
