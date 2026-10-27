# 任务书（给 Codex）：阶段 2——Service SDK 0.3.13（Realtime 工具循环、上下文注入、改钉版）

日期：2026-10-25。发包人：Simple Harness 主代理（Claude）。执行：Codex 桌面版。检查：Claude。
本文自包含。**真线路测试暂不做**：TokenSeller 中继目前连不上阿里云（见同目录 `交接-给chinzy-中继阿里云上游不可用-2026-10-25.md`），等中继修好后由 Claude 用已写好的探针跑；本阶段先把代码、离线测试、文档、钉版做完。

## 0. 项目规则（硬性）

1. 回复、文档、提交说明全部用中文。
2. 仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-service-sdk`（git，远程 `DennyWanye/simple-harness-service-sdk`，当前 0.3.12，源码 `47f372a`）。这是独立仓库，和编排 SDK 仓库分开，**两边不要互相改**。
3. 只跑改动文件对应的测试；不许自行跑全量回归（这个仓库全套 337 条，12 秒，主体完成后跑一次整套是允许的，开发中只跑相关）。
4. 该仓库自己的规范照旧：`tests/public-api.json` 公开接口清单、`compatibility-bom.json`、`check_architecture.py`、ruff/mypy 严格。禁止全局 ignore/noqa 或删断言替代修复。
5. 密钥不进代码、测试、文档、日志。
6. 按仓库的发版流程出候选（`scripts/build_candidate.py`、`docs/build-and-release.md`），私有分发，不传公开仓库。

## 1. 背景

要在语音里让模型调用工具（委派、查任务、取消），任务完成后还要把结果播报出来。阿里云官方文档已核实：Qwen3.5-Omni-Flash-Realtime 支持 `session.update.tools` → `response.function_call_arguments.done` → `conversation.item.create(function_call_output)` → `response.create`。官方客户端事件页**没有**列出文本 `message` item、`response.create` 的 `instructions` 参数、`conversation.item.truncate`，所以"上下文注入"哪条路能走要靠真线路试验，代码上先把接口留好并对不支持的路明确报 `unsupported`。

另外两个 SDK 的依赖冲突已证明只是钉版问题：Service SDK 0.3.12 的代码在编排 SDK 核心 `simple_harness_sdk-0.13.0.dev20260925+opt.216` 上 335/337 测试通过，失败的两条是 BOM 钉版与 mypy 未装（记录：`simple_harness/plans/2026-10-24-unified-session-realtime/依赖组合试验-2026-10-25.md`）。

现状代码（`src/simple_harness_service/realtime/`）：
- `contracts.py`：`RealtimeOpenRequest` 没有工具目录字段；`RealtimeFeature.TOOL_CALLING`、`ToolCallRequested`、`ToolCallState` 已有。
- `adapters/qwen_omni.py`：`session_update()` 不下发 `tools`；`tool_result()`、`followup_response()` 已写好；**`_validate_session_ack()` 把 `session.updated` 的字段集写死为 8 个键，回显里多一个 `tools` 就判 `protocol_error`**——必须放开。
- `adapters/qwen_wire.py`：客户端事件白名单已含 `session.update`、`conversation.item.create`、`response.create`、`response.cancel`；服务端白名单含函数调用相关事件。
- `session.py`：`submit_tool_result()` 已实现"发结果 → 等 `conversation.item.created` 回执 → 发 `response.create`"；`_send_runtime_event()` 私有。
- `local_channel.py`：产品侧本地通道（`call.start/stop/audio_ack/barge_in…`），已有输出 ack。
- `pyproject.toml`：钉 `simple-harness-sdk @ https://github.com/DennyWanye/simple-harness-sdk/releases/download/v0.6.2/...`；`memory` extra 钉 Memory SDK 0.5.2。

## 2. 要做的事

1. **工具目录**：`RealtimeOpenRequest.tools`（function schema 列表，总字节受 `limits.tool_payload_bytes` 约束，schema 校验）；Qwen 适配器 `session_update()` 下发 `tools`；`_validate_session_ack()` 允许并校验回显的 `tools`（与下发的一致才算 ready）；`SessionReady` 带 `tools_acknowledged: bool`。回显缺 `tools` 时按 `unsupported` 失败，不静默。
2. **工具循环**：只接 `response.function_call_arguments.done`（完整参数），delta 只记诊断；`ToolDispatcher` 协议（宿主/上层注入，`async def dispatch(call: ToolCallRequested) -> str`），`ManagedRealtimeSession` 可选自动循环：收到调用 → 校验 JSON 与 schema → dispatch → `submit_tool_result()`；旧 generation 的调用拒绝；重连重放去重；参数畸形/超限回工具错误而不是执行。
3. **上下文注入接口**（两个原语，provider 中立）：`append_message(text, *, role="user"|"system", trigger_response: bool)` 与 `update_instructions(text, *, trigger_response: bool)`。Qwen 适配器分别编码为 `conversation.item.create(message/input_text)` + 可选 `response.create`、第二次 `session.update(instructions)` + 可选 `response.create`。上游回 `error` 时映射为 `unsupported` 并记诊断。"带外生成"（OpenAI 的 `conversation:"none"`）Qwen 没有，接口留 `out_of_band_response()` 但 Qwen 适配器直接 `unsupported`。
4. **播放回执**：`local_channel` 的输出 ack 补"实际播放位置（ms）"字段；过期 generation 的帧不再下发；`ResponseFinished` 之外增加"已发送音频字节/时长"的统计可查。
5. **改钉版**：Harness 依赖改为私有 opt 轮子：`simple-harness-sdk==0.13.0.dev20260925+opt.216`（PEP 440 不允许带本地版本号的 `>=`，用 `==`），安装说明写清用 `--find-links` 指向私有轮子目录（Host 仓库 `simple_harness/backend/vendor/`）；`compatibility-bom.json` 与 `test_bom_architecture.py` 相应更新（Harness 版本、`execution_schema` 按 opt.216 实际值）；`memory` extra 保持。装好后 `simple_harness`、`agent_orchestrator`、`simple_harness_service` 三者能同环境导入。
6. **OpenAI 适配器**：conformance fixture 不动；新接口在 OpenAI 适配器上给出对应编码（`conversation.item.create`、`response.create(instructions)`、`conversation: "none"`），只做离线 fixture。
7. **测试**（离线）：工具目录下发与回显校验；工具循环（含畸形参数、重复 call_id、旧 generation）；两条注入原语的编码与 `unsupported` 映射；播放回执；BOM；`public-api.json` 更新。
8. **版本**：0.3.13，CHANGELOG，`INTEGRATION_STATUS.md`，候选制品与 SHA-256。

## 3. 交付物

1. 候选轮子与 SHA-256、源码提交号；私有分发。
2. 接口说明：新字段/方法、错误码、`unsupported` 的情形、安装组合（两个 SDK 怎么装到一个环境）。
3. 记录：`simple_harness/plans/2026-10-24-unified-session-realtime/阶段2-记录.md`（做了什么、测试清单与结果、未做的真线路项、剩余限制）。
4. 做完在本文末尾追加"完成于 <时间>，版本 0.3.13，提交 <sha>"，并通知 Claude 检查。

## 4. 验收（Claude 检查；真线路项等中继恢复后由 Claude 跑）

| 编号 | 场景 | 判定 |
|---|---|---|
| A06 | 真线路语音工具闭环 | 上游 `session.updated` 回显 tools；真实语音模型发起调用；回写结果并续答（等线路） |
| A07 | 三轮录音对话 | 三轮转写、回复文本与音频完成，连接可继续（等线路） |
| A12 | 超时/不支持/坏消息 | 按阶段报失败；工具参数分片/重复/畸形不误执行；能力缺失不伪装通过；无无限重试（离线可测） |
| A14 部分 | 两包同环境安装 | 新虚拟环境按说明装两个 SDK，三个包能导入，Service SDK 测试通过 |
| A15 | 质量与发布 | 格式/lint/严格类型/公开接口清单/BOM 通过 |
