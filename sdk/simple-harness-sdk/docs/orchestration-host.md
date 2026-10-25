<!--
SPDX-FileCopyrightText: 2026 DennyWanye
SPDX-License-Identifier: Apache-2.0
-->

# 任务编排接入说明（Python 应用）

`agent_orchestrator.host` 是外部 Python 应用使用任务编排的唯一入口：建任务 → 分层规划 → 执行 →
独立审阅 → 交付产物，全在 SDK 里。应用只提供模型配置、数据目录和一个身份。

模型：DeepSeek V4.1 用官方分词器精确计数（有思考池）；别的 OpenAI 协议模型（Luna / Sol 这类，
2026-10-21 起）用按字节的上界计数器——可以多算不可以少算，预算会消耗得快一些，不开思考池。见第 2a 节。

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
        auto_mode=True,  # 自动授权每轮规划、自动确认完成要求、自动代批操作
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

## 2a. 模型：DeepSeek 与其他 OpenAI 协议模型

```python
model = ModelConfig(
    base_url="https://relay.example.com/v1",
    model="gpt-6.1-sol",            # 任何 OpenAI 协议端点上的模型
    api_key="<密钥>",
    fallback_model="gpt-5.6-luna",  # 可选：首选传输层失败时换它
    relay_margin_floor_tokens=8192, # 可选：线路前言边际起点
    thinking="disabled",
)
```

| 项目 | DeepSeek（`deepseek-flash` / `deepseek-v4.1-flash`） | 其他模型 |
|---|---|---|
| 输入计数 | 官方分词器精确计数；转发线路加学到的工具前言边际 | 把线路上发出的 JSON 按 UTF-8 字节数计（字节级 BPE 的 token 数 ≤ 字节数，硬上界），再加线路前言边际（起点 `relay_margin_floor_tokens`，默认 8192；设得太小第一次学习之前会少算线路前言；按"报告用量 − 本机计数"只升不降，落盘在数据目录 `relay-margin/<主机>.json`；边际不进池身份） |
| 输出与结算 | 按模型报告的真实用量；报告缺失记"未知"，不当零 | 同左 |
| 执行池 | 池编号不含模型名 | 池编号含模型名（`…-gpt-6-1-sol-v1`）：同一数据目录换模型会开新的池，旧池的数据留着不再用 |
| 思考池 | 有 | 没有 |
| 误差 | 精确 | 输入上界通常是真实用量的 2～4 倍；预算会早一点碰上限 |

**回退**（`fallback_model`）：首选模型限流（429）或服务端错误（5xx）时换备用——这两种内核本来就当作确定失败、
不计费；每个请求最多换一次，换过之后同一次运行（同一个工具循环）的后续请求直接走备用，新运行回到首选。
超时、断连可能已经计费、已经输出了一部分：不换，内核记"结果不明"并按上界占住预算。请求被拒（4xx）、
认证、预算、取消也不换。回执里的
`model` 规范成首选名（内核按它核对回显、信任用量），实际模型、原因、请求号、用量记在数据目录
`model-calls.jsonl`，`host.model_calls(limit=)` 读取。重试只在这里做一次：网关别再重试，否则次数相乘。

图片：不接多模态提供方；应用把"看图"做成只读工具（`DomainTool`）交给 SDK 调。

## 2b. 快速聊天（不进任务编排）

一句话一个回合，直接答：没有规划器、没有审阅员。用的是同一个引擎（执行池里的工具循环、工具网关、
预算账本），只是跑在一个单独的聊天执行池上（数据目录 `chat/`，池编号带 `-chat-`）。

```python
reply = await host.chat({
    "session_id": "chat-1",          # 一个会话一个 Agent；第二句能看到第一句
    "text": "今天家里哪台设备没电了？",
    "idempotency_key": "chat-1-7",   # 同一键再发：原样返回，不再调模型
    "tools": ["device_status"],      # 可选：只给这几个已注册的只读工具；默认全部。会话第一句定下，后面不能换
    "require_tool": False,           # 可选：必须先查工具；首轮没查就提醒一次，仍不查如实返回 tool_not_used
})
# reply = {"text", "state": "committed"|"failed", "error", "turn_id", "agent_id", "session_id",
#          "tool_calls": [{"tool", "call_id", "outcome"}], "model_calls", "model",
#          "usage": {"input_tokens", "output_tokens", "unknown"}, "nudged", "tool_not_used",
#          "history_reset"（见下）, "total_ms", "first_output_ms"（给了 on_delta 才有，见 §2c）}
await host.chat_cancel({"session_id": "chat-1"})   # 取消这个会话正在跑的回合
host.chat_history("chat-1", limit=50)              # 台账 chat-turns.jsonl 里这个会话的回合
```

| 项目 | 做法 |
|---|---|
| 身份 | Agent 由宿主当事人建；模型参数里的身份不算数；工具拿到的 `ToolCallContext.mission_id` 是 `None`、`attempt_id` 是 `chat-<会话号>` |
| 工具 | 只有应用注册的只读工具；有副作用的事模型只能说"这要建一个任务"（由应用拿去建任务） |
| 上限 | `HostSettings.chat_max_model_calls`（默认 8）、`chat_max_tool_calls`（默认 8）、`chat_turn_seconds`（默认 180）；到了上限这一回合按失败返回 |
| 预算 | 每回合先在全局月账上预留"模型调用上限 ×（窗口 + 输出上限）"，结束按模型报告的真实用量结清；报告缺失的调用按预留上限结清；月配额不够直接拒（`budget_exhausted`），不调模型 |
| 记录 | SDK 执行库里有完整的模型调用与工具记录；宿主另记 `chat-turns.jsonl` |
| 宿主中途关掉 | 在途回合的预留按上限结清（启动时也会把遗留的结清）；那次模型调用结果丢了的 Agent 不能再用（内核如实记"结果不明"，不重放），这个会话换一个新 Agent 接着聊，那一句回执 `history_reset=true`，之前的对话它看不到 |
| 用量 | 线路报 0/0 或没报的调用记"未知"，按预留上限结清（可以多算不可以少算） |
| 不做 | 聊天里不做审阅——要把关的事走任务；应用自己保存界面上的对话 |

被拒的请求抛 `ChatRequestError`（`code`：`invalid_request` / `chat_tools_frozen` / `budget_exhausted` /
`chat_unavailable` / `chat_timeout`）。

## 2c. 逐字输出（正在生成的文字）

片段只是预览；终稿以持久记录为准（聊天回执的 `text`、任务的快照与产物）。

```python
# 聊天：只看本回合
reply = await host.chat(body, on_delta=lambda piece: ui.append(piece["text"]))
#   piece = {"session_id", "turn_id", "generation", "seq", "text"}；reply["first_output_ms"] 是首段正文到达时间

# 任务与聊天都看：全局订阅
stop = host.output_stream.subscribe(lambda event: ...)   # 返回退订函数
```

事件格式 v1（加字段不升版本，改字段含义才升）：

| 字段 | 说明 |
|---|---|
| `v` | 1 |
| `kind` | `piece`（一段正文）/ `end`（这次模型调用结束）/ `error`（这次调用抛错） |
| `scope` | `chat`（带 `session_id`、`turn_id`）/ `mission`（带 `mission_id`、`subject_id`、`view`——执行者绑定了工作区时能对上；规划器、审阅员的调用只有运行号）/ `unknown` |
| `run_id` | 运行号（一个聊天会话或一次尝试的 Agent） |
| `generation` | 一次模型调用的请求号；换了就是新一次生成，从头拼，不会把两次生成拼在一起 |
| `seq` | 片段在这次 generation 里的序号，从 1 递增；`end` / `error` 带最后一个序号 |
| `text` | 片段正文（只有用户可见正文；思考内容与工具调用参数不发） |
| `finish` / `tool_calls` | `end`：结束原因、这次调用带了几个工具调用 |
| `error` | `error`：错误码 |

规则：
- 去重键 `(generation, seq)`；片段不落盘、不重放（保留期 0、没有分页）。
- 断线重连后读持久记录，再从新的 generation 开始拼。
- 审阅否决或重写的尝试，它的片段只是草稿；终稿只认验收通过的产物。
- 订阅者抛异常只记日志，不影响模型调用。


## 2d. 会话（文本主入口）

最后更新：2026-10-25。本节文本会话接口从 SDK opt.217 提供；桌面端仍使用既有接口。

新接入方用 `Session`：同一个对象接收文字、委派后台任务、读取结果和回答人工请求。
`OrchestrationHost` 的既有方法保留，供高级接入和诊断使用。语音不在本阶段。

```python
import asyncio
from agent_orchestrator.host import Session


async def run(root, principal, model, tools, render, last_seen=0):
    session = await Session.open(root, principal, model, tools=tools, session_id="main")

    async def display():
        async for event in session.events(after=last_seen):
            await render(event)

    reader = asyncio.create_task(display())
    try:
        reply = await session.send("帮我整理照片，生成相册说明文件", key="input-1")
        await reader
    finally:
        reader.cancel()
        await session.close()
```

`root`、`session_id`、当事人与工具配置保持相同，再次 `open` 就是恢复。`session_id` 默认
`default`，只接受字母、数字、下划线和连字符，最长 128 字符。一个数据目录同时由一个 Host
实例持有；如需隔离用户，分别给数据目录。`model`、`tools`、`connectors` 和其他 Host 组装选项
沿用前面各节。SDK 自己启动任务驱动与事件泵，接入方不用写 `drain` 或轮询循环。

| 接口 | 返回与行为 |
|---|---|
| `await Session.open(root, principal, model, tools=(), connectors=(), policy=SessionPolicy(), session_id="default", **host_options)` | 打开或恢复；当事人与租户绑定在持久台账中 |
| `await session.send(text, idempotency_key=...)` | 回执含 `turn_id/state/text/tool_calls/usage`，`engine_turn_id` 是底层聊天回合号；也可写 `key=`，同一键与相同输入原样回放 |
| `session.events(after=0)` | 持续异步迭代；补读持久事件并接收当前正文片段 |
| `session.respond(request_id, decision, version, **fields)` | 同步人工回答；规划问题可给 `answer/nonce/attach_as_source`，审批可给 `reason/note` |
| `await session.interrupt()` | 只停正在回答的文字回合，任务继续 |
| `session.tasks()` / `session.task(task_id)` | 本会话关联任务与权威快照；不调用模型，额度耗尽仍可读 |
| `session.cancel_task(task_id)` | 接受 mission 号或 delegation 号；创建中先记录取消意图，恢复创建后继续取消；终态不倒改 |
| `session.inbox()` / `session.ack(event_id)` | 查看结果；只有已认证的人能确认收到，模型没有 ack 命令 |
| `session.history(limit=50)` / `session.budget()` | 已提交对话及预算引用；会话账不再结算一遍费用 |
| `await session.close()` | 关闭文字引擎和驱动，保留任务、会话与结果；重开后继续 |

会话只用新池 `…-chat-v2-c1-<配置哈希>`，执行库在 `sessions/<session_id>/chat/`。
旧 `chat()` 的 v1 编号、提示词、工具名单与行为保留。`SessionPolicy` 默认每回合 16 次模型调用、
32 次工具调用、180 秒；配置与工具名在会话建立时冻结，改配置请使用新会话号。

模型可见 `delegate_task`、`task_status`、`task_cancel`，以及原生执行层已有的
`session_history_read/search` 和宿主只读 `DomainTool`。委派属于 SDK 控制命令，不能注册成
应用工具。历史工具参数沿用原生合同：回读给 `seq_from`，搜索给 `query`，均支持游标分页。
模型不传当事人、租户、会话或输入回合号。

委派参数只有 `goal/success_criteria/sources/reason`；成功条件 1～12 条、资料最多 8 份（各含
`path/content`），网关另有整体参数大小限制。SDK 统一规范化 Unicode、目标空白和资料路径，
用“会话号 + 已持久提交的输入回合号 + 规范化目标哈希”派生幂等键；完整规范另算哈希。
同意图换 `call_id` 仍回原任务，成功条件或资料变了则拒绝；新输入回合同目标可建新任务。
默认同时只有一个在途任务。创建走现有 `door/create_mission[_with_sources]`，自动授权和审批
由原有 Host 职责处理，委派回执不等任务完成。

事件信封如下；`turn_id/delegation_id/mission_id/request_id` 按事件适用范围出现。

```json
{"schema_version":1,"event_id":"session-event-...","session_id":"main","seq":7,
 "kind":"result_ready","mission_id":"mission-...","delegation_id":"delegation-...",
 "payload":{"source":"task_result","instruction":false,"status":"COMPLETED"}}
```

| `kind` | 持久与补读规则 |
|---|---|
| `text_delta` | 正文预览，含 `text/generation/turn_id`；不保存正文、不重放；慢订阅者片段队列有界，终稿仍从回执读取 |
| `input_committed` / `turn_committed` | 输入已持久提交 / 回复已提交；失败回复走 `error` |
| `delegated` | 已完成 delegation 与 mission 关联 |
| `task_progress` | 任务事件的白名单摘要，只带类型、序号、时间和关联号；人工评论可带短摘要，内部 payload 不透传 |
| `needs_response` | 人工问题或审批；含 `request_id/version/request_kind` 与可显示的提问内容 |
| `result_ready` | 任务终态进入 inbox；按终态事件号去重 |
| `error` | 稳定错误码，不把底层异常文字交给模型 |

所有事件的 `seq` 在会话内递增，正文片段也占序号，所以补读可见空隙。只将已收到的事件序号作为
下一次 `after`；正文是预览，终稿以 `turn_committed.payload.text` 为准。任务泵只读已有 mission
事件序号，变化时补读并投影。inbox、`result_ready` 和任务补读游标同事务提交，重启后至少一次
读取、按终态事件号幂等应用。下一次输入把尚未应用的结果冻结成明确标注的“任务结果资料”，
同键重试使用同一份资料；只有回复成功提交才确认结果已应用，失败或取消仍待回传。确认收到与模型读取分开，结果不会自行触发模型或新任务。

预算只有原有聊天月账和任务账。委派额外预留一整回合上限用于结果回传（与冻结的 Agent
调用上限一致）；首次实际回传尝试领取该预留，按真实用量结清；新的回合或 Agent 换代使用独立且持久绑定的预算主体，不会重复使用已结清额度。启动时按本会话的尝试关联与原生回合事实补结算：用量只取该回合的原生调用序号区间，不把同一 Agent 的后续回合算到旧主体；终态且用量已知的按实耗，结果未知的按预留上限，重复恢复不重复收费；尚未领取的结果额度及其他会话的回传预留保持原状。会话只保存账本位置和任务引用。
没有额度也可以调用 `tasks/task/inbox` 读取事实。

恢复台账是 `sessions/<session_id>/session.sqlite3`（SQLite WAL、FULL 同步），保存输入、回复、
关联、inbox、游标和事件序号；底层 Agent 沿用聊天 generation 机制。结果不明的模型调用不重放；
换代时将最近已提交历史作为资料补给新 Agent。未完成输入须先用原键重试，避免新意图顶掉它。
委派记录已存在但 mission 关联未写完时，用同一派生键恢复创建；取消意图不会因此丢失。确定性拒绝且权威库证明未创建任务时，终结该意图并释放预留；结果不明或任务已存在则继续恢复。回传准入确实无预算时保存失败回执并解除挂起输入，结果仍待应用。

| 错误码 | 处理 |
|---|---|
| `idempotency_conflict` | 同一输入键改了文字，或同一委派意图改了完整规范；确认新意图后换输入键 |
| `busy` | 已有一个在途委派，先查询或取消 |
| `turn_pending` | 先重试未完成的原输入键 |
| `permission_denied` | 任务、人工请求不属于本会话，或不是人工身份 |
| `stale_request` | 请求版本、绑定、有效期或待答状态已变化，重新读取请求 |
| `session_config_frozen` | 会话策略或工具名单变化，恢复原配置或使用新会话号 |
| `invalid_request` / `invalid_arguments` | 输入或命令参数不合规 |
| `session_closed` | 重开会话后继续 |
| `budget_exhausted` / `chat_unavailable` / `chat_timeout` | 沿用聊天与 Host 的错误语义；任务事实读取仍可用 |

审批的 `version` 由 SDK 对当前审批内容计算，规划问题沿用原有版本号。接入方原样回传事件中的
版本，不自行生成。版本核对与 `decide/answer_planning_question` 在同一任务库写事务内完成，
身份和请求绑定字段不能由调用参数覆盖。

## 2e. 语音绑定（`[realtime]` 可选扩展，opt.218 候选）

最后更新：2026-10-25。本节是候选接口；离线语音桥测试通过，Service SDK 0.3.13
候选轮子的联合契约、真线路及仓库外安装尚未验收，不能视为阶段 3a 完成或正式发版。

`Session` 继续是唯一业务前门，Service SDK 负责媒体连接与工具结果回写循环，不反向依赖
编排层。文本路径不要求安装 Service SDK。安装元数据声明了 `[realtime]`；私有候选组合
须按任务指定顺序安装：先以 `--no-deps` 安装已核对哈希的 Service SDK 0.3.13 轮子，
再安装 opt.218 候选轮子，最后安装 `httpx websockets prompt-toolkit`。上游私有依赖解析
与 lock 文件尚待联合安装核验，不应拿公开同版本包替代指定候选。

```python
from agent_orchestrator.host import VoiceProfile

# make_service_client(dispatcher) returns a configured Service SDK client.
# The client owns automatic tool dispatch and result/followup acknowledgements.
binding = await session.attach_voice(
    audio_io, VoiceProfile(client_factory=make_service_client)
)
handle = await session.announce("任务已经完成，请查看结果。")
playback = await handle.wait()
await binding.barge_in()
await binding.detach()
```

`client_factory` 接收本次 generation 的 dispatcher，返回配置好的 Service SDK 客户端；
桥调用客户端的 `open(request)`。提供方、凭据铸造、传输与适配器由接入方组装，密钥只从
进程环境读取，不传给会话台账。工厂每次重连重新组装客户端，旧 dispatcher 会拒绝新一代调用。
Service SDK 0.3.13 的具体工厂装配及工具自动循环仍须在指定候选轮子上核验。

| 接口 | 行为 |
|---|---|
| `await session.attach_voice(audio_io, profile)` | 返回 `VoiceBinding`；包含 `binding_id`、持久递增的 `generation` 和 `negotiation`；同会话只允许一个语音绑定 |
| `await session.announce(text)` | 返回 `SpeechHandle`；等待用户说完、模型回复结束及上一段实际播放完后，按路线 a 注入资料并触发回复 |
| `await handle.wait()` | 回执包含状态、实际播放 ms、接收音频 ms、生成结束和传输结束标志；等待者取消不取消播报 |
| `await binding.barge_in()` | 清本地播放队列；仅对仍在生成的已知 response 发取消，不取消后台任务 |
| `await binding.detach()` | 关闭媒体连接、停止音频读取并释放设备，保留业务会话、任务和历史 |
| `await session.close()` | 同一挂断路径，然后关闭文本引擎及 Host 驱动 |

音频协议只有三个方法。输入为 16 kHz、单声道、s16le PCM，输出为 24 kHz、单声道、s16le：

| 方法 | 合同 |
|---|---|
| `await audio_io.read_pcm() -> bytes 或 None` | 返回 PCM 帧，`None` 为输入结束；取消等待时释放输入设备 |
| `await audio_io.write_pcm(frame)` | PCM 字节写入播放队列；`b""` 清队列；`None` 释放输出设备；清队列不倒退播放时钟 |
| `audio_io.played_audio_ms() -> int` | 单调累计的实际播放位置，不能用已接收或已排队字节代替 |

桥自动从会话内置控制命令及宿主只读 `DomainTool` 生成目录；不改旧池的工具说明。
`SessionReady.tools_acknowledged` 必须为真，否则报告 `unsupported` 并释放仲裁。
工具入口校验目录、参数 schema、会话输入归属、工具次数和调用幂等；可信上下文由 SDK 填入。
幂等键含业务会话、语音输入 item_id 和 call_id；委派继续用既有规范化目标去重和任务准入。
委派只等受理回执和任务号，不等待执行完成。主动结果播报不授权创建或取消任务。

用户转写、助手转写、工具调用和回执写入原 `session.sqlite3`，文本下一回合把新提交的语音
历史作为资料补入，即使文字 Agent 没有换代也能读取。语音事件使用同一 `schema_version=1`
及全局递增序号，额外带 `binding_id/generation`，按适用范围带 `response_id/item_id/call_id`。
原始 PCM 和音频 base64 不写入台账或事件流。

**公开限制与语义：**

- 通话期间文字 `send()` 在仲裁锁上按调用顺序排队，挂断后才由文本引擎处理；首版排队是进程内等待，进程退出后调用者须用原幂等键重试。关 App 会释放等待并返回 `session_closed`。
- 结果和需审批事项进入播报队列；需审批与失败结果优先。普通内部进度不逐条朗读。播报、读取结果与人工 `ack/respond` 分离，不替用户审批。
- 路线 a 使用 `append_message(..., trigger_response=True)`；历史和播放事实使用同接口并设为不触发回复。桥不发送重复 `session.update`，不使用只含 instructions 的更新，也不依赖 Qwen 不支持的带外生成。
- Qwen 打断后服务端仍可能保存完整助手回复，服务端历史不代表用户实际听到。未播内容及本地实际播放位置持久保留，在下一语音回合或重连时作为资料注入，不自动重播；未播完不等于已听到或已批准。
- `generated`、`transmission_finished`、`received_audio_ms` 与 `played_audio_ms` 分别记账。不能把 `ResponseFinished` 或音频到达当作播放完成。首版不能从播放毫秒数可靠推导准确的文字断点，因此保留整段文本加播放事实。
- `VoiceProfile` 可配置重连次数、指数退避、单连接及总通话秒数、每次回复 token 估算和总 token 额度。额度在打开媒体连接前向现有月账预留；有未知用量或进程中断时按上限结算，重开幂等收敛。它是 token/时间限制，不是提供方货币计费承诺。
- 重连保留业务 `session_id`，递增 generation，注入最近已提交历史，绝不重放 PCM。挂断/重连前排队但被打断的播报保留为未播资料。

验收现状、原始证据相对索引及未完成项见仓库根目录
`plans/2026-10-24-unified-session-realtime/阶段3a-记录.md`。

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
（Linux）开不了新的用户命名空间也没有能力位、脱离进程树的后台进程会被收掉、输出与 CPU 有上限。全部通过才启用；结果在 `status()["sandbox"]`
（`ok`、`kind`、没通过时的 `reason`），也写在数据目录的 `sandbox-probe.json`。

| 系统 | 隔离方法 | 前提 |
|---|---|---|
| macOS | Seatbelt（`sandbox-exec`） | 系统自带 |
| Linux | bubblewrap：新的用户/进程/网络/IPC 命名空间，根目录为空且只读，只读挂解释器白名单；可写的只有本次副本和草稿目录（`/dev/shm` 也落在草稿目录，多进程能用） | 装 `bwrap` 0.8 以上；内核允许普通用户开用户命名空间 |
| 其他 | 无 | — |

Linux 要点：

- Ubuntu 23.10 起（含 24.04）AppArmor 默认不让普通程序开用户命名空间，要装一份只放行 `bwrap` 的规则：
  `python -m agent_orchestrator.host.sandbox_doctor --apparmor-profile` 打印规则文件和安装命令（Ubuntu 24.10
  起系统自带同样的规则）。SDK 不改任何系统设置。
- 在 systemd 服务里跑可以带 `NoNewPrivileges=yes`（bubblewrap 不用 setuid）。
- 进程归属按进程命名空间：命名空间的第一个进程一结束，内核就把里面的进程全部结束；宿主被杀，
  `--die-with-parent` 让隔离区一起结束；重启后按"开机号 + 第一个进程启动时刻 + 命名空间"认领，对不上的不碰。
- 内存与进程数是软限制（运行中采样，超了就收掉）；CPU 时间与文件大小是硬限制。没用 cgroup。
- 隔离区分两步建：外层 bwrap 只建用户命名空间（并禁止里面再建），内层 bwrap 再建进程、网络、IPC、
  UTS 命名空间。有的内核（例如 Droidian 4.14）同一次同时建用户与进程命名空间后挂 `/proc` 报
  `Device or resource busy`，分两步就能建。隔离区里的身份是用户命名空间里的 0 号（映射到宿主上
  启动它的普通用户），没有能力位，再 exec 也拿不回；启动检查的"特权"一项实测这一点。
- 宿主给 bwrap 设"不再提权"（`no_new_privs`），系统装的是 setuid 版 bwrap 也按普通用户方式运行；
  systemd 服务里另写 `NoNewPrivileges=yes` 也一样。
- 内核没有 pidfd（5.3 以前，如 4.14）时，收尾改为"先核对进程还在这次的进程命名空间里，再发信号"。
  命名空间在收尾结束前一直被钉住，编号不会被别的隔离区复用。

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

### x86_64 与 NixOS

- x86_64（Ubuntu 24.04，内核 6.8，glibc 2.39，bubblewrap 0.9.0）已实测：启动检查 9 项全过，带 `run_tests` 与 `pytest:` 核对的任务 COMPLETED，越界读取被拒（2026-10-21，本机 QEMU 虚拟机）。做法与 ARM 机完全相同。
- NixOS（按打包方式写的要点，未实测）：
  - `bwrap` 装在 `/run/current-system/sw/bin/bwrap`（`environment.systemPackages = [ pkgs.bubblewrap ]`），不是 setuid；宿主按 `PATH` 找得到即可。
  - 用户命名空间：`boot.kernel.sysctl."kernel.unprivileged_userns_clone" = 1`（NixOS 默认允许）；NixOS 没有 Ubuntu 那条 AppArmor 限制。
  - `/nix/store` 要只读挂进隔离区（Python 解释器与轮子都在里面）：现在的两层做法外层 `--dev-bind / /`、内层 `--cap-drop ALL` 已经覆盖，不用另加 `--ro-bind`。
  - `/etc/ssl`、`/etc/resolv.conf` 不需要（隔离区断网）。
  - 需要的内核功能：user / pid / net / ipc / uts 命名空间、`no_new_privs`、pidfd（5.3+；没有时退回命名空间核对）。
- 缺依赖时（`bwrap` 找不到、版本 < 0.8、开不了用户命名空间）：`status()["sandbox"]` 写明原因，`code_execution="off"`，`run_tests` 等按名拒绝；**不会悄悄关掉隔离继续跑**。

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
| 人工批准 | `decide(request_id, 决定, ...)`，决定按卡的 `kind`：动作 / 改源 / 发布卡用 `"approve"` 或 `"reject"`（驳回必须带 `reason`，原因会随 `action_results.decision_note` 交给提议的那一步）；`kind="review"`（审阅员判不了、等人复核结果）用 `"review_pass"` / `"review_fail"`（`note=`）；结果不明等人裁定的用 `"arbitrate"`（`ruling=`、`basis=`）。用错会被拒并说明是哪种卡 |
| 确认完成要求 | `approve_operation_completion_spec(body)`（自动模式下由 SDK 代签，每条 `action:` 配一个效果） |
| 看操作走到哪 | `operation_intent_status(intent_id)`；快照里 `operation_workspace.intents` |
| 中途提议的动作 | `step_actions(mission_id)`（4c 节） |
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
| 只收只读工具 | `read_only=False` 在构造时被拒。有副作用的操作走连接器（有审批、去重号、对账），见 4b、4c 节 |
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

## 4b. 注册应用自己的连接器（有副作用的操作）

发消息、移动文件、改设置这类会改变外部世界的操作，不给模型当工具调。两种来源：建任务时写成要求
（本节：系统在内容做完后按顺序执行：准备申请单 → 独立审阅 → 审批 → 执行 → 按回执核对 → 结果审阅），
或执行中由模型提议（4c 节）。

```python
from agent_orchestrator.runtime.connectors import OperationSpec, Receipt, params_hash

class Photos:
    name = "photos"                      # 要求里写 action:photos.<操作>:<目标>
    supports_idempotency = True          # 同一个 idempotency_key 只执行一次
    supports_reconciliation = True       # 能按 idempotency_key 查（lookup）
    lookup_authority = "authoritative"   # 执行与记账原子、记录不会被回滚；L2 以上必须是它
    lookup_fenced = True                 # 查询为空后这个键再来的执行一律拒绝（见下表）
    operations = {
        "move": OperationSpec("move", "L2", ("to",), description="把照片移到 to"),
        "share": OperationSpec("share", "L2", ("with",), always_needs_approval=True),
    }
    def normalize_target(self, target): return target.strip()
    async def execute(self, operation, target, params, *, idempotency_key) -> Receipt: ...
    def lookup(self, idempotency_key) -> Receipt | None: ...

host = await OrchestrationHost.open(
    root, principal=..., model=...,
    settings=HostSettings(max_action_level="L2"),   # 注册连接器必须写明最高风险等级
    connectors=[Photos()],
)
host.create_mission({
    "goal": "把 a.jpg 移到归档",
    "success_criteria": [
        "file:NOTES.md",
        {"statement": "action:photos.move:/photos/a.jpg", "params": {"to": "/archive/a.jpg"}},
    ],
    "idempotency_key": "move-1",
})
```

| 规则 | 说明 |
|---|---|
| 参数在建任务时写下 | `{"statement", "params"}`；只有 `action:` 要求能带参数。缺必填参数、不是规范 JSON、像密钥都被拒。参数随要求版本记账，系统照原样执行，模型改不了 |
| 引用一份产物 | 参数里写 `{"$artifact": "工作区路径"}`，执行时换成 `{artifact_path, content_hash, size, storage_uri}`；一个操作最多引用一份 |
| 自动模式 | 系统确认完成要求、准备申请单、审阅，并代批（记成 `HOST_AUTO_PERMISSION`，事件执行者是主机） |
| 总要人批 | `always_needs_approval=True` 的操作自动模式也不代批，在 `approvals()` 里等人 `decide(..., "approve")` |
| 手动模式 | 完成要求、审批都等人 |
| 风险等级与批准人数 | L0/L1 自动执行；L2、L3 都只要一个人批准（单人部署，用户 2026-10-21 定；原计划的 L3 双人批准不用） |
| 注册时就拒 | 重名（含内置 `file_publish`）、没有操作、只读操作（只读走工具）、不能去重或不能查询（`delivery="at_most_once"` 的除外，见 4c 节）、`lookup_authority` 没写 |
| `kind="event"` | 每次执行都产生新事实的操作（付款、追加）；随注册启用 |
| `execute` / `lookup` | 普通函数在线程里跑；`async def` 在宿主事件循环上跑，里面不能阻塞。`execute` 里不能"等人"，要人批就用审批 |
| 明确拒绝 | 抛 `ConnectorRejected`：记"失败"，没有生效 |
| 结果不明 | 抛别的异常、超时、回执不合格（不是 `Receipt`、不是规范 JSON、超过 64 KB）：记"不明"，去 `lookup` 核对 |
| 回执 | `Receipt` 的键、连接器、操作、目标、参数哈希必须和这次执行一致，`applied=True` |
| 异常文字 | 截到 500 字；像密钥就只记异常类型名 |

结果不明时怎么核对：

| 连接器 | `lookup` 找到回执 | `lookup` 为空 |
|---|---|---|
| 权威且封口（`lookup_fenced=True`） | 按回执结案 | 证明这次没生效：系统换新键重做，迟到的原请求会被封口拒掉，不会生效两次 |
| 权威不封口 | 按回执结案 | 证明不了（原请求可能还在路上）：停下等人裁定（`resolve_unknown`） |
| 非权威 | 按回执结案 | 等人裁定；这类连接器只能做 L0/L1 |

可运行的例子：`examples/orchestration-host/tools_effect.py`（自动代批执行一次；"总要人批"等人点批准）。

## 4c. 步骤中途提议动作（执行时才知道要做什么）

建任务时写不出来的动作——搜完照片才知道归档哪几张、看了屏幕才知道点哪里——由执行步骤的模型在
中途提议，系统检查、批准、执行后把结果交回同一步，模型再决定下一步。连接器还是 4b 节那一套。

```python
host.create_mission({
    "goal": "找出长颈鹿照片并归档，写 NOTES.md 说明做了什么",
    "success_criteria": ["file:NOTES.md"],
    "allowed_actions": ["photos.move"],      # 执行中可以提议的动作名单（连接器.操作）
    "idempotency_key": "giraffe-1",
})
```

一次提议怎么走：

| 步 | 谁 | 做什么 |
|---|---|---|
| 提议 | 模型 | 结果块里写 `action_request: {connector, operation, target, params, reason}`，`outcome` 为 `blocked`；这次尝试到此结束，等批准不占模型 |
| 整理 | 连接器 `prepare`（可选） | 应用自己的检查（路径、角色、设备能力）和参数规范化；抛 `ConnectorRejected` 就是拒绝 |
| 冻结 | SDK | 与这次结果同一个事务写动作行：整理后的参数、哈希、风险等级；批准绑定这一份 |
| 批准 | SDK / 人 | 名单内：自动模式代批，"总要人批"的等人；**名单外的一律要人批**，审批卡 `summary.out_of_scope=true` |
| 执行 | SDK | 交接前再 `prepare` 一次，和冻结的不一致不执行（记 FAILED，原因 `prepare_mismatch`）；然后按 4b 节交接、回执、结果不明核对 |
| 续做 | SDK | 动作结案（成功、失败、被拒、过期、取消、人裁定）后给同一步开新尝试，工作包 `action_results` 里是结构化结果；不问规划器。结果不明在人裁定前不续做 |

规则：

- 中途动作是完成既定目标的手段，不改完成要求；要改要求只能 `amend_requirements`。
- 模型看到的动作目录（`available_actions`）来自注册的连接器：说明、参数说明、风险、要不要人批、在不在名单内。
- 一次提议 = 一个逻辑动作（动作号由任务、结果、连接器、操作、目标算出）；同一结果重复提交不重复建。
- 上限：`HostSettings(max_step_actions_per_mission=20)`，持久、跨尝试与重启累计；超了的提议记 `StepActionRefused`，模型下次看得到。中途动作不占要求动作的交接上限（`max_action_handoffs_per_mission`）。每次提议都结束一次尝试、续做再开一次，所以也受任务预算 `max_attempts`（默认 12）约束：提议多的任务把它调大。
- 内置的文件发布（`file_publish`）不能中途提议，只走建任务时写的要求。
- 形状、名单、连接器、必填参数、风险等级在门口核；判断"该不该做"归模型与批准的人。
- 取消任务：没交接的提议取消；已交接的照样对账。

**只交接一次的动作**（桌面点击这类既不能去重也不能查询的）：连接器声明 `delivery = "at_most_once"`，
`lookup` 可以不实现。每个动作只交接一次，超时或断连记"结果不明"，**从不重发**，直接等人
`resolve_unknown(action_key, "succeeded"/"failed", basis=...)`；裁定后这一步续做。

读取：`host.step_actions(mission_id)` 列出这个任务里提议过的动作；`approvals(mission_id)` 里等批的卡；
事件 `StepActionRequested`、`StepActionRefused`、`ActionHandedOff`、`ActionSucceeded`、`ActionFailed`、
`ActionOutcomeUnknown`、`ActionReconciled`。可运行的例子：`examples/orchestration-host/tools_step_action.py`。

## 4d. 看计划、执行某步、跳过、停止

```python
plan = host.plan(mission_id)
# {"plan_revision": 3, "steps": [{"occurrence_id", "task_id", "form", "version", "status", "paused",
#                                 "goal", "phase", "readiness", "reason_codes"}, ...], "edges": [...]}
host.takeover(task_id, "skip", basis="用户说不要这一步", expected_version=step["version"], command_id="click-7")
host.takeover(task_id, "run_now", basis="用户点了现在执行")
host.takeover(task_id, "stop", basis="…")            # 停这一步与整个任务
host.cancel(mission_id)                              # 停整个任务
host.taskgraph.reads.why_not_ready(mission_id, occurrence_id)   # 没就绪的原因
```

| 命令 | 做什么 | 拒绝 |
|---|---|---|
| `skip` | 步骤记 CANCELLED（**不算成功**），开着的尝试取消；规划器下一轮在 `human_takeovers` 里看到这条记录，剩下的目标怎么办由它判断 | 已完成 / 已失败 / 已取消的；BLOCKED 的（它随上级目标结束）；有已交出、结果未明的外部动作的 |
| `run_now` | 优先级置顶、解除暂停；调度下一轮先挑它 | 不是 READY 的（原因用 `why_not_ready` 看） |
| `expected_version` | 看计划时拿到的步骤 `version`；变了就拒（`stale_decision`） | |
| `command_id` | 同一命令号第二次原样返回第一次的记录，不重复执行 | 同一命令号用在别的步骤或别的动作上会被拒 |

停止后旧审批卡批准不会再唤醒动作（交接前复核"任务还在跑"）；审批卡与规划提问都持久，重启后还在。

## 4e. 子 Agent、多成员、外部内容、工具集更新、大结果

| 事 | 做法 |
|---|---|
| 子 Agent | 编排里的"子 Agent"就是计划里的子步骤：规划器把目标拆成子目标，每个叶子步骤是一个独立 Agent。工具 = 任务 ∩ 步骤 ∩ 角色 ∩ 部署（只缩不放）；预算从任务总账扣；层数、扩展次数、候选数都在计划预算里。每个步骤提议的动作各自审批：批准了上一步的动作不等于批准下一步的 |
| 多成员 | 一人一个宿主（各自数据目录、各自 `Principal`）；对方的任务号在自己这里一律 `not_found`；同一个数据目录第二个宿主起不来（实例锁） |
| 资料与技能 | 资料 `register_source` / `supersede_source` / `revoke_source` 带版本；技能目录按安装版本钉死 |
| 外部内容 | 任务里 `untrusted_sources` 标出的路径当作数据；网页、NAS、MCP 回来的内容里写的"批准 / 允许"不产生任何审批——授权只认宿主与 SDK 给的身份 |
| 工具集更新 | 任务建立时冻结工具名单：换工具集重开宿主，旧任务仍按旧名单（名单外的调用被拒成事件），新任务用新名单；在途的待批审批卡与未结清动作都在，批准后照常执行 |
| 超过 32 KB 的工具结果 | 完整记录进执行库；模型拿到预览 + `journal_read_back.seq`，用 `session_history_read(seq_from=seq, seq_to=seq, max_bytes=65536)` 回读：一页压到单条工具结果预算内（页不会再被截成预览），`has_more=true` 带 `next_cursor` 读下一页，拼起来就是原文。工作区大文件 `workspace_read_file` 自动分页（`next_offset` + `expected_sha256`） |

## 5. 要求怎么写

- 每行一条，普通文字就行；规划器按它拆步骤，审阅员按它审。
- `file:路径`：工作区里要有这个文件。
- `pytest: 路径`：在沙箱里跑测试（需要沙箱可用）。
- `action:file_publish.publish:路径`：完成后发布到授权目录；入口自动补一条 `file:路径`。
- `action:连接器.操作:目标`（可带参数）：完成后由应用的连接器执行，见 4b 节。
- 请求里任何像密钥的文本都会被拒（`secret_rejected`）。

## 6. 等人的时候

人要把关时用 `auto_mode=False`（或传一个每轮现读的函数）。这时规划每轮要 `planning_authorization`、
完成要求要 `approve_operation_completion_spec`、操作要 `decide`、规划器的提问要 `answer_planning_question`。
自动模式下只有标了 `always_needs_approval` 的操作等人。
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
| `HostUnavailable: 当前模型没有可用的用量计数器` | 没给 `model=` 也没给 `provider=`/`counter=` |
| DeepSeek 模型却按上界计数 | 模型名不是 `deepseek-flash` / `deepseek-v4.1-flash`，或主机既不是官方也没写进 `compatible_hosts`：这时按"其他模型"处理 |
| `另一个实例正在使用编排目录` | 同目录已有进程在用 |
| `执行图部署验收门没有通过` | 安装包不是发版脚本产出的；重新按 `docs/build-and-release.md` 构建 |
| `DeepSeek tokenizer bytes differ…` | 分词文件不是钉死的那份；用 `ensure_deepseek_tokenizer` 重新取 |
| `取 DeepSeek 分词文件失败…` | 第一次启动要联网下载分词文件；或自己下载后传 `tokenizer_path` |
| 任务停在"等授权" | `auto_mode=False` 且没人调 `planning_authorization` |
| `status()["sandbox"]["ok"]` 为假，原因含 `bubblewrap is not installed` | Linux 没装 `bwrap` |
| 原因含 `AppArmor restricts unprivileged user namespaces` | Ubuntu 23.10+ 要装 bwrap 的 AppArmor 规则（见 3a 节） |
| 原因含 `kernel.unprivileged_userns_clone=0` 或 `max_user_namespaces=0` | 内核不许普通用户开用户命名空间，要系统管理员放开 |
| `local_tests_disabled` | 这台机器没有可用的隔离区，任务不能带 `pytest:` 要求 |
