# corr_372WMDD4NA1FE2V7JG87S0X37J：工具续答协议错误的客户端事件序列说明

日期：2026-10-29。来自：Simple Harness SDK 侧。给：CHINZY / TokenSeller。
答复 CHINZY 10-29《请 SDK 侧补充：工具续答协议错误的原始事件序列》。
本文可独立转发。不含密钥、音频、转写正文、工具参数或工具结果正文。

## 0. 回执

```text
目标 correlation：corr_372WMDD4NA1FE2V7JG87S0X37J
材料性质：原故障原件（2026-10-26 13:53～13:55 +08:00 那次运行的 events.jsonl，逐行导出，未重跑）
脱敏文件位置或链接：同目录 corr_372WMDD4NA1FE2V7JG87S0X37J.events.redacted.jsonl（331 行）
  https://github.com/DennyWanye/simple_harness/tree/main/plans/2026-10-24-unified-session-realtime/给chinzy-corr_372WMDD4NA1FE2V7JG87S0X37J
原始时区与时间范围：本机 UTC+8。原件只有相对毫秒 t_ms（monotonic）。起算时刻 T0 = 2026-10-26T13:53:40.061+08:00（见第 2 节）。导出窗口 13:54:47.6 ～ 13:54:58.6
记录层级、完整性与缺失项：四个层级（见第 3 节）。该连接从 session.open 到 tokenseller.error 完整，无截断、无丢帧。缺失项见第 6 节
实际 SDK / Host 提交：Service SDK 0.3.20 候选轮子（源码 dada251，与正式 v0.3.20 f34caa3 的中继客户端代码相同）；编排 SDK 0.13.0.dev20260925+opt.218 候选（Host 仓库 sdk/simple-harness-sdk）；驱动脚本 driver.py SHA-256 27b69d32…94f5；用例名 A10-voice-task，第 2 次运行
工具结果事件：function_call_output（带 call_id）。没有发 message/user/input_text
response.create 实际发送次数、触发层和时点：1 次。Service SDK 工具回写循环自动发送。时点 t_ms=77187（13:54:57.248），在收到 function_call_output 的 conversation.item.created 之后 0～1 ms
已观察到的 response / item / call 关联：见第 4 节
是否还有其他连接或重连，如何区分：同一 binding 下还有连接 0（corr_303MNS2QE8AGPH0YXH8CP1T15S），只发了 session.open 即收到 busy，60 秒退避后开连接 1。两条连接的记录都带 correlation 与 connection 序号，导出文件只含连接 1 的线上帧（见第 5 节）
```

## 1. 一句话结论

这条连接上，客户端只发了一次 `response.create`。它由 Service SDK 在收到 `function_call_output` 的 `conversation.item.created` 之后自动发出。
在它之前，上游已经靠服务端 VAD（`create_response=true`）自己建了第一轮 response `resp_JiAU2xtaSlmoZZsV4Pevz`，并在该轮里发出工具调用。
第二轮 `resp_VEENwVghPJqj8dgWtOVpb` 开始后 57 ms，客户端收到 `tokenseller.error`。客户端全程 `generation=1`。CHINZY 日志里的 `generation=2` 不是客户端的值。

## 2. 时间换算

- 原件每行只有 `t_ms`，是驱动进程 `time.monotonic()` 自起点的毫秒数。
- 起点 T0 取 `events.jsonl` 的文件创建时刻 2026-10-26T13:53:40.061+08:00。第一行 `t_ms=0` 在文件创建后立刻写入。
- 校验：最后一行 `t_ms=78550` 换算为 13:54:58.611；文件最后修改时刻为 13:54:58.613。差 2 ms。
- 导出文件每行加 `x_t_abs_local` = T0 + t_ms。它是推导值，不是原始采集值。
- 与 CHINZY 服务端时钟对照：

| 事件 | 客户端换算时刻 | CHINZY 服务端时刻 | 差 |
|---|---|---|---|
| 连接就绪（客户端收到 `tokenseller.session.created`） | 13:54:49.051 | 13:54:49.532 | 客户端早 0.48 s |
| 第一轮 `response.done`（客户端收到） | 13:54:57.188 | 13:54:56.904 | 客户端晚 0.28 s |
| 协议失败（客户端收到 `tokenseller.error`） | 13:54:58.211 | 13:54:58.636 | 客户端早 0.43 s |

两边时钟有零点几秒的偏差，方向不一致。请只用相对顺序和 correlation/event_id 对齐，不用绝对时刻对齐。
同一毫秒内的行按原始写入顺序保留。`x_line` 是原件 1 起算的行号。

## 3. 记录层级

同一个线上帧在导出文件里通常出现两行。按 `x_layer` 区分：

| x_layer | x_direction | 采集位置 | 原件 kind |
|---|---|---|---|
| `websocket_text_send` | client_to_relay | `RelayWebSocketTransport.send_text` 包装层，对实际发出的 JSON 文本取白名单字段。这是"实际发送" | `wire_boundary` direction=send |
| `websocket_text_receive` | relay_to_client | `receive_text` 包装层，对实际收到的 JSON 文本取白名单字段。这是"实际收到" | `wire_boundary` direction=receive |
| `service_sdk_adapter_encode_client_event` | client_to_relay | Service SDK 适配器编码客户端事件时，早于 send_text。记录类型、event_id、item 类型/角色/call_id、音频 base64 长度 | `wire_client` |
| `service_sdk_adapter_decode_server_event` | relay_to_client | Service SDK 适配器解码服务端事件时，晚于 receive_text | `wire_server` |
| `service_sdk_event_to_bridge` | internal | Service SDK 交给语音桥的事件（ResponseStarted、ToolCallRequested 等） | `service_event` |
| `host_session_event` | internal | 编排 SDK Session 对外事件 | `session_event` |
| `driver` | internal | 驱动脚本自己的记录（开线尝试、录音、音频队列、结果） | 其余 kind |

白名单（`wire_boundary.fields`）：`type / event_id / response_id / item_id / call_id / correlation / code / message_class / related_event_id / name / role / status / fatal / retryable / retry_after_ms / output_index / content_index`，以及 `item`、`response`、`part` 的同类子字段。`known_field_names` 列出原帧里被白名单命中的顶层字段名。`field_types` 记录错误帧字段的类型。`redacted_fields` 为空表示没有字段因含疑似凭据被抹掉。
不在白名单内的字段（如 `audio`、`delta`、`transcript`、`output`、`arguments`、`instructions`）原件就没有采集。音频只有 `audio_base64_length`。
`trigger_event_type` 是驱动按 `related_event_id` 匹配到的客户端事件类型；匹配不到时填最近一次发送的类型，并以 `trigger_matched=false` 标明。它不是服务端给的因果关系。

## 4. 该连接的关键顺序

全部时刻为 UTC+8，由 T0 推导。

| x_line | t_ms | 时刻 | 方向 | 事件 | 关联 |
|---|---|---|---|---|---|
| 10 | 68307 | 13:54:48.368 | → | `tokenseller.session.open` | `ctl_open_71a36c8d157b4ec48addafc764f04b4c` |
| 11 | 68990 | 13:54:49.051 | ← | `tokenseller.session.created` | related = 上一行 |
| 13 | 68993 | 13:54:49.054 | → | `session.update`（完整 session，1 次） | `event_9c1a055e346d43498b41195598dc456a` |
| 14 | 69275 | 13:54:49.336 | ← | `session.updated` | session 顶层键：audio, id, input_audio_transcription, instructions, modalities, tools, turn_detection, voice |
| 19～ | 69291～ | | → | `input_audio_buffer.append` ×67，每帧 100 ms 16 kHz PCM，无 commit / clear | |
| 42 | 70280 | 13:54:50.341 | ← | `input_audio_buffer.speech_started` | item `item_Ovj2AavcukAlaWhHLUYsX` |
| 186 | 75553 | 13:54:55.614 | ← | `input_audio_buffer.speech_stopped` | 同 item |
| 211 | 75798 | 13:54:55.859 | ← | `conversation.item.created`（**role=assistant**, type=message, status=in_progress, content_types=[input_audio]） | 同 item。这是用户输入音频的 item，角色却是 assistant |
| 213 | 75798 | 13:54:55.859 | ← | `conversation.item.input_audio_transcription.completed` | 同 item |
| 228 | 76650 | 13:54:56.711 | ← | `response.created` | `resp_JiAU2xtaSlmoZZsV4Pevz`。客户端没有发 `response.create`，由服务端 VAD 建 |
| 231 | 76651 | 13:54:56.712 | ← | `response.output_item.added` | resp_JiAU，output_index 0 |
| 233 | 76651 | | ← | `conversation.item.created`（type=function_call, in_progress） | `item_HcCgdNRsGm9G1548Iy31Y` |
| 235 | 76652 | 13:54:56.713 | ← | `response.function_call_arguments.done` | resp_JiAU / item_HcCg / `call_f4fc4adfe3e6439d96257ce2` |
| 238 | 76652 | | ← | `response.output_item.done` | resp_JiAU |
| 239～240 | 76655 | 13:54:56.716 | → | `conversation.item.create`，item = {type: function_call_output, call_id: call_f4fc…} | `event_b966e7794fa447fb9e17002ab97ba760`。**早于第一轮 response.done 472 ms** |
| 245 | 77127 | 13:54:57.188 | ← | `response.done`，status=completed | resp_JiAU |
| 248 | 77187 | 13:54:57.248 | ← | `conversation.item.created`（type=function_call_output, completed） | `item_DQ9BRXtSXo7p2qSDHaQpm`，call_f4fc… |
| 250～251 | 77187～77188 | 13:54:57.248 | → | `response.create`（**唯一一次**） | `event_bacb1efeb0e148b9a8e64d4d74d6c81f` |
| 255 | 78093 | 13:54:58.154 | ← | `response.created` | `resp_VEENwVghPJqj8dgWtOVpb` |
| 258 | 78093 | | ← | `response.output_item.added` | resp_VEEN，output_index 0，item `item_AEQjh7CMG44Bp3KUNVN8V`（message, assistant, in_progress） |
| 260 | 78094 | | ← | `conversation.item.created` | item_AEQj |
| 262 | 78098 | | ← | `response.content_part.added`，part.type=audio | resp_VEEN / item_AEQj / output 0 / content 0 |
| 265～309 | 78098～78100 | | ← | `response.audio_transcript.delta` ×15 | 同上 |
| 327 | 78149 | 13:54:58.210 | ← | `response.audio.delta`，base64 长 20480（15360 字节 PCM） | 同上 |
| 330 | 78150 | 13:54:58.211 | ← | **`tokenseller.error`** code=protocol_error, message_class=upstream_protocol_error, fatal=true, retryable=false, related_event_id=null, retry_after_ms=null | `ctl_error_ddab4120165b43d282ce6c3758e35c18` |

错误之后客户端没有再发任何帧。没有收到 resp_VEEN 的 `response.done`、`response.audio.done` 或 `response.audio_transcript.done`。没有收到上游 `error` 事件。连接随后关闭。

## 5. 运行说明

1. **原始日志**：本机（SDK 侧开发机，macOS）`.local-test-evidence/2026-10-25/unified-session/phase3/live8/A10-voice-task-run2/events.jsonl`，339 行。导出第 9～339 行（连接 1 的开线尝试到结果）。第 1～8 行是运行头和连接 0。连接 0（corr_303MNS2QE8AGPH0YXH8CP1T15S）只发了一次 `tokenseller.session.open`，t_ms=4305 收到 `tokenseller.error code=busy / capacity_busy, retryable=true`，退避 60 秒后开连接 1。连接 0 没有发 session.update，没有发音频。导出文件里第 331、339 行（driver 层的诊断汇总与结果）提到 corr_303，那是汇总，不是连接 1 的线上帧。
2. **完整性**：该目录在 git 忽略区，10-26 以来没有改动。原件是逐帧 flush 写入，无截断。连接 1 的每一个线上帧在 `websocket_text_*` 层都有记录；`adapter_*` 层对 append/delta 同样逐帧记录。没有丢帧迹象（append 发送 67 帧与 `websocket_text_send` 67 条一致）。
3. **版本**：Service SDK 0.3.20 候选轮子 SHA-256 f1e091bf…（源码 dada251）。编排 SDK opt.218 候选（`opened` 行记录 `harness=0.13.0.dev20260925+opt.218, service=0.3.20`）。是原故障原件，不是新复现。
4. **工具结果**：真实 `function_call_output`，`call_id=call_f4fc4adfe3e6439d96257ce2`，output 是工具 `photo_inventory` 的只读结果（正文未采集）。没有发 `message/user/input_text`。本连接没有做文字回灌。
5. **谁发的 `response.create`**：Service SDK 工具回写循环（`realtime/session.py` 的 `submit_tool_result`）：发 `function_call_output` → 等它的 `conversation.item.created` 确认 → 立刻发 `followup_response()` 即 `response.create`。驱动脚本和编排层都没有手动发 `response.create`。第一轮 response 由服务端 VAD 触发（`turn_detection = semantic_vad, threshold 0.5, silence_duration_ms 800, create_response true, interrupt_response true`）。
6. **session.update 内容**：完整 session，1 次。客户端发送的字段：modalities [text, audio]、voice、audio.input pcm 16000、audio.output pcm 24000、input_audio_transcription.model qwen3-asr-flash-realtime、turn_detection（同上）、instructions（正文未采集）、tools 5 个 function（delegate_task, task_status, task_cancel, device_status, photo_inventory；描述正文已脱敏，参数 schema 保留）。服务端 `session.updated` 回显的 tools 与客户端发送的不逐字相等（`catalogue_equal=false`），但 5 个工具名、参数名、必填项一致，桥判就绪。
7. **没有发生的事**：没有 `input_audio_buffer.commit / clear`，没有 `response.cancel`，没有第二次 `session.update`，没有重连，没有同 key 新绑定。错误后客户端释放绑定并结束用例。

## 6. 缺失项（原件未采集，不补造）

- 所有帧的正文：`audio`、`delta`、`transcript`、`output`、`arguments`、`instructions`。
- `tokenseller.error` 的 `message` 字段：原帧没有这个字段（`known_field_names` 里没有 message）。
- `response.created` / `response.done` 的 `response` 子对象只采集了 `id` 和 `status`，没有 `status_details`、`usage`、`output`。
- `conversation.item.created` 的 `previous_item_id`：未采集。
- 服务端事件的服务端时间戳：Qwen 原生事件不带，中继帧也没有。
- 原件 `null` 是原始值：字段在原帧里存在且为 null（如 `related_event_id: null`）。字段不存在时 `known_field_names` 里不出现该名字。空字符串保留为 `""`（如 `service_event.code: ""`）。

## 7. 我们看到的两个可疑点（只陈述，不下结论）

- 第 211 行：用户输入音频的 item 被标成 `role=assistant`。这与 10-26 我们在 0.3.19 上报告过的"同 item 角色矛盾"一致。0.3.20 已按输入来源判定身份，不再因此拒绝。
- 第 239 行：`function_call_output` 在第一轮 `response.done` 之前 472 ms 发出。Service SDK 收到 `response.function_call_arguments.done` 就提交工具结果，不等 `response.done`。不知道上游是否要求等。
