# Provider 推理能力调研与会话级 Thinking 设计

> 创建日期：2026-08-21
> 状态：**调研完成，方向已获用户认可；实现与测试延期到后续任务**
> 关联计划：[`../2026-08-20-sdk-thinking-process/plan.md`](../2026-08-20-sdk-thinking-process/plan.md)

## 1. 背景与目标

现有 App 已能把 canonical Run 的公开工作进度和工具调用聚合为可折叠的“思考过程”，但这不等于
Provider 返回的模型推理。当前需要进一步解决三个问题：

1. 会话“模型与参数”窗口中的 `Default / Thinking / Fast` 必须真正控制该会话的模型请求；
2. App 需要识别不同 Provider/模型的推理协议，不能把 DeepSeek 的请求字段硬编码给所有模型；
3. UI 只能在确实收到模型推理信号后展示由其派生的公开“思考摘要”，不能把任务名、工具名或固定
   兜底句伪装成模型思考。

本文件只记录调研事实、设计决策和后续执行边界。本轮不修改业务代码，不宣称功能已经上线。

## 2. 结论摘要

### 2.1 `thinking.enabled` 不是跨 Provider 的通用协议

`thinking: {"type": "enabled"}` 只对明确支持这一请求格式的模型有意义。App 必须建立
**Provider + model capability adapter**，按具体模型生成请求参数。

| Provider / 模型 | Thinking 模式 | 正确请求方式 | 推理响应字段 | 能否关闭 |
|---|---|---|---|---|
| DeepSeek `deepseek-v4-pro` / `deepseek-v4-flash` | 可切换 | `thinking.type=enabled/disabled`；Thinking 可配 `reasoning_effort=high/max` | `reasoning_content` | 可以 |
| Kimi `kimi-k3` | 永远推理 | **不要传 `thinking`**；用顶层 `reasoning_effort=low/high/max`，默认 `max` | `reasoning_content` **可能返回** | 不可以 |
| Kimi `kimi-k2.7-code` | 永远推理 | 不需要传 `thinking`；传 `disabled` 会报错 | `reasoning_content` | 不可以 |
| Kimi `kimi-k2.6` | 默认推理、可切换 | `thinking.type=enabled/disabled`；可用 `thinking.keep=all` 保留历史推理 | `reasoning_content` | 可以 |
| Kimi `kimi-k2.5` | 默认推理、可切换 | `thinking.type=enabled/disabled` | `reasoning_content` | 可以 |
| 未知 OpenAI-compatible 模型 | 未知 | 不猜测、不发送 Provider 私有字段 | 运行时探测 | 未知 |

### 2.2 开启 Thinking 不等于 App 可以无条件假定“必有非空文字”

- DeepSeek 官方文档描述：思考模式下，每轮先输出 `reasoning_content`，再输出 `content`；当前
  `deepseek-v4-pro` / `deepseek-v4-flash` 支持该模式。因此在**官方端点、正确模型、成功响应、未被
  中转站改写**的条件下，App 应预期收到推理内容。
- 但 DeepSeek API schema 把 `reasoning_content` 定义为 nullable；工程上仍需处理字段缺失、空字符串、
  输出截断、模型 ID 不匹配、第三方中转站丢字段和 Provider 协议变化。
- Kimi K3 官方文档的措辞是“always reasons”但 `reasoning_content` **may be returned**。K3 不接受
  `thinking.enabled`，因此不能用是否成功发送这个参数来判断是否会拿到推理文字。

因此产品契约应是：

> Thinking/always-thinking 模式表示“请求或选择了模型推理能力”；只有收到非空
> `reasoning_content` 后，才进入“模型推理已返回”分支。字段为空时显示诚实状态，不生成伪推理。

### 2.3 UI 展示的是“基于真实推理的公开摘要”，不是原始隐藏 CoT

推荐三类标签，避免混淆来源：

| 数据来源 | UI 标签 | 行为 |
|---|---|---|
| 非空 `reasoning_content` 派生的有界公开摘要 | `思考摘要 · <模型>` | 运行时展开，终态自动折叠，可点击重看 |
| 模型主动写入普通 `content` 的行动说明 | `工作说明` | 可与工具卡一起进入 Run 分组 |
| 工具调用与结果 | 真实工具名/结果卡 | 如实展示，不称为模型推理 |

禁止事项：

- 不直接把原始私有 CoT 投影到 UI、SessionDB 或下一轮普通 conversation context；
- 不根据“当前任务 + 工具名”额外生成一段文字并称为模型思考；
- 不重复显示固定兜底句；
- 未收到推理时不得伪造摘要。

未收到非空推理时，建议 UI 显示一次有状态的系统说明：

> `<模型> 本轮未返回可展示的思考摘要`

随后继续显示真实工具调用、工具结果和最终答复。该说明是系统状态，不是模型思考。

## 3. 官方资料核实

### 3.1 DeepSeek

截至 2026-08-21，官方文档给出的当前模型为 `deepseek-v4-pro` 和
`deepseek-v4-flash`，两者支持思考与非思考模式。OpenAI-compatible 请求通过：

```json
{
  "model": "deepseek-v4-pro",
  "thinking": {"type": "enabled"},
  "reasoning_effort": "high"
}
```

在 OpenAI Python SDK 中，`thinking` 需放进 `extra_body`。思考模式默认启用，普通请求默认 effort
为 `high`；复杂 Agent 请求可使用 `max`。思考内容通过与 `content` 同级的
`reasoning_content` 返回。

工具调用的重要协议要求：含 tool call 的 assistant turn 所返回的完整 `reasoning_content` 必须在
后续请求中原样回传，否则 API 可能返回 400。该回传是 Provider 协议状态，不等于允许进入公开 UI
或长期对话记忆。

官方资料：

- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek Model List](https://api-docs.deepseek.com/api/list-models/)

### 3.2 Kimi

截至 2026-08-21，Kimi 官方文档列出了 `kimi-k3`、`kimi-k2.7-code`、`kimi-k2.6`、
`kimi-k2.5` 等模型。它们不能共用一个 Thinking 参数模板：

- `kimi-k3`：始终推理并始终启用 Preserved Thinking；不支持 `thinking` 参数；只能通过顶层
  `reasoning_effort=low/high/max` 调整强度，默认 `max`；官方写明 `reasoning_content` 可能返回。
- `kimi-k2.7-code`：始终推理，不能关闭；历史 assistant 的 `reasoning_content` 必须保留。
- `kimi-k2.6`：默认开启，可用 `thinking.type=disabled` 关闭；`thinking.keep=all` 可开启
  Preserved Thinking。
- `kimi-k2.5`：默认开启，可关闭，但不支持 Preserved Thinking。

Kimi 对多步工具调用同样要求在一个任务/工具循环内保留并回传完整 reasoning content。官方建议
thinking model 使用 streaming，并给足输出 token 预算，以降低长推理被截断或网络超时的风险。

官方资料：

- [Kimi Thinking Models](https://platform.kimi.ai/docs/guide/use-thinking-models)
- [Kimi Create Chat Completion](https://platform.kimi.ai/docs/api/chat)

## 4. 本地代码现状（2026-08-21，只读审计）

### 4.1 Provider 已具备本地持久化能力

Provider registry 已将 endpoint 元数据写入本地 `config.toml` 的 `[[llm.endpoints]]`；API key 不以
明文保存在配置中，而是通过 `api_key_ref` 存入 macOS Keychain。相关实现位于：

- `backend/llm/provider_registry.py`
- `backend/llm/provider_config.py`

本机当前开发 profile 已存在并启用用户此前录入的 DeepSeek Provider，包含两个当前模型 ID 和默认
模型。因此“每次都要重新输入”不是 CRUD 缺失，而更可能来自测试时启动了不同 bundle、不同
user-data root 或两个 App 实例。

后续应确保：

1. 正式 App 使用唯一、稳定的 canonical user-data root；
2. 升级不覆盖已有 `[[llm.endpoints]]`；
3. dev/bundle profile 切换有明确标识或迁移；
4. API key 始终留在 Keychain，不复制到日志、计划或测试证据。

### 4.2 会话模型参数已经持久化，但尚未贯通 SDK Provider 请求

前端 `tauri-app/src/code-panel/ChangeModelModal.tsx` 已提供 `Default / Thinking / Fast`，并把选择
写入 Session 的 `model_params`；SessionDB 也能持久化该字段。

当前断点在后端：

- `backend/llm/code_params.py` 仍偏向把 Thinking 映射为 `reasoning_effort`；
- DeepSeek capability 当前未允许 effort，导致相关参数又被剥离；
- SDK Runtime 构造 `ProductProviderAdapter` 时只传了 `provider_id + model`，没有把 Session
  `model_params` 和冻结后的 reasoning mode 传给 Provider adapter；
- 现有实验代码存在对官方 DeepSeek 强制开启 Thinking 的倾向，这会绕过会话窗口的 authority。

结论：UI 看起来能选择 Thinking/Fast，但该选择目前不是 SDK Run 出站请求的可靠事实源。

## 5. 后续实现契约（已认可，尚未执行）

### 5.1 单一 authority

每个 Run 启动时，从会话模型选择窗口解析并冻结：

```text
(provider_id, model_id, reasoning_mode, reasoning_effort)
```

Run 执行中不得因 Provider 默认值、设置页修改或 registry 热刷新而漂移。设置页负责 Provider 和默认
模型；会话窗口负责该 Session/Run 的模型与推理模式。

### 5.2 统一内部模式，Provider 专用出站映射

内部只保存语义，不直接保存某家的 JSON：

```text
reasoning_mode = default | thinking | fast
reasoning_effort = default | low | high | max
```

Provider adapter 再按 `(provider family, model id, capabilities)` 映射：

| 会话选择 | DeepSeek V4 | Kimi K3 | Kimi K2.6 | 未知模型 |
|---|---|---|---|---|
| Default | 省略开关，遵循模型默认 | 省略 `thinking`，省略或使用模型默认 effort | 省略开关 | 不发送私有参数 |
| Thinking | `thinking.enabled` + `reasoning_effort=high` | 不传 `thinking`；`reasoning_effort=high` 或产品指定强度 | `thinking.enabled` | 仅在能力声明支持时发送 |
| Fast | `thinking.disabled` | **不支持关闭**；UI 禁用 Fast 或明确降级到 `reasoning_effort=low` | `thinking.disabled` | 仅在能力声明支持时发送 |

Kimi K3 的 Fast 不能偷偷解释成“关闭思考”。推荐在会话窗口把 Fast 显示为不可用；如果产品希望提供
低延迟档，应另命名为“低推理强度”，并明确映射为 `reasoning_effort=low`。

### 5.3 响应处理状态机

```text
请求 Thinking / 模型 always-thinking
        |
        +-- 收到非空 reasoning_content
        |      -> Provider 私有状态保存/工具循环回传
        |      -> 生成有界公开摘要
        |      -> context_visibility=exclude
        |      -> UI: 思考摘要 · 模型名
        |
        +-- 字段缺失或为空
               -> 不生成摘要、不使用固定兜底
               -> UI: 本轮未返回可展示的思考摘要
               -> 工具卡与最终答复照常显示
```

公开摘要必须来源于实际收到的 reasoning content；原文只存在于当前 Provider tool loop 所需的最小
私有状态中，并按不同 Provider 的 preserved-thinking 规则回传。摘要失败不得阻断工具执行和最终答复。

### 5.4 Capability registry 最低字段

后续实现建议为每个模型声明：

```text
reasoning_behavior: toggleable | always_on | unsupported | unknown
reasoning_request_style: thinking_object | effort_only | none
reasoning_response_fields: [reasoning_content, ...]
reasoning_efforts: [low, high, max]
preserve_reasoning: tool_loop | all_turns | not_required
fast_semantics: disable_reasoning | lower_effort | unsupported
```

静态 catalog 只描述已确认的官方模型；自定义中转站/未知模型默认 unknown，并通过实际响应能力探测
补充观测，但不能因为某次空响应就永久判定“不支持”。

## 6. 后续工作分解（未开始）

1. 删除“仅根据任务和工具名生成说明”的实验路径，避免伪思考。
2. 把 Session `model_params` 贯通到 SDK Runtime 和 Provider adapter，并冻结为 Run binding。
3. 实现 DeepSeek V4、Kimi K3/K2.7/K2.6/K2.5 capability adapters。
4. 正确解析 streaming/non-streaming `reasoning_content`，并遵守各 Provider 的工具循环回传规则。
5. 基于真实 reasoning 生成有界公开摘要；空字段走诚实状态，不使用内容兜底。
6. 会话窗口按模型能力禁用/重命名不成立的模式，例如 Kimi K3 的 Fast。
7. 固化 Provider user-data root 与升级保留行为，排除双实例/profile 漂移。
8. 增加协议 fixture、真实 Provider 集成测试、重启 hydration 回归和 Computer Use 真机 E2E。
9. 测试全绿后，同次交付更新 `ARCHITECTURE/` 对应事实源和 `PROJECT_STATUS.md`。

## 7. 验收草案（后续计划需细化）

- DeepSeek V4 + Session Thinking：真实出站含正确开关；非空 reasoning 驱动公开摘要；工具调用可继续，
  不出现缺少 reasoning replay 的 400。
- DeepSeek V4 + Session Fast：真实出站关闭 Thinking；UI 不伪造思考摘要。
- Kimi K3：出站不含 `thinking`，reasoning effort 按会话配置；Fast 不被误表示为关闭推理。
- Kimi K2.6：Thinking/Fast 分别映射 enabled/disabled。
- Provider 返回空/缺失 reasoning：只显示一次诚实状态，工具和最终回复正常。
- 原始 reasoning 不进入公开 SessionDB 正文、普通 conversation context、日志或测试证据。
- 重启后 Provider、默认模型和 Session 模型参数保持；不同 App 实例不会读写不同 profile 而让用户重填。

## 8. 明确不在本轮执行

- 不修改任何业务代码或测试代码；
- 不启动 App，不发起真实 Provider 调用；
- 不改变当前 Provider 配置；
- 不提交或推送现有工作区中的其他未提交修改；
- 不更新 `ARCHITECTURE/`，因为本轮没有完成并通过测试的新生产能力。
