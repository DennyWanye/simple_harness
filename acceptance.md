# 验收标准：Agent 执行时间线与上下文隔离

## 范围

### 包含

- 在 simple_harness 主消息页提供可打开/关闭的 Agent 执行时间线。
- 按真实 Run 顺序展示公开阶段、工具调用、工具结果、验证结果和终态。
- 阶段、工具输入、工具结果和技术记录支持独立折叠；内容经过公开投影、脱敏和有界截断。
- 为每类数据声明是否进入模型上下文，并在代码、持久化投影和测试中保持一致。
- 在实时运行、历史重载、重启恢复和异常/乱序事件下保持 UI 与 durable Run 状态一致。

### 明确不包含

- 不展示模型隐藏 reasoning token、`reasoning_content` 或未经公开的思维链。
- 不把 Codex/simple_harness 开发者自己的终端操作日志自动搬进用户任务时间线。
- 不把 UI 时间线、Inspector 技术记录或历史审计详情自动注入当前/后续模型上下文。
- 不建立第二套 Run owner、状态机或持久化 authority。

## 数据边界（必须逐条满足）

| 数据类别 | 用户可见性 | 是否进入模型上下文 | 持久化/使用规则 |
|---|---|---:|---|
| 系统指令、Persona、工具目录、权限策略、用户消息 | 不一定全部显示 | 是 | 由现有 Context OS/TurnPreparer 管理 |
| 当前 Run 中模型实际收到的用户消息、公开 assistant 文本、工具调用和工具结果 | 部分显示 | 是（仅按当前 Run 的既有模型协议） | 继续按现有模型消息契约工作 |
| 时间线阶段标题、状态、时间、耗时、进度、终态 | 是 | 否 | 仅写入/读取公开 activity projection，标记 `context_visibility=exclude` |
| 工具调用的 UI 摘要、脱敏参数和有界结果预览 | 是，可折叠 | 否（UI 投影本身不注入） | 来源事实可来自当前 Run 的 tool call/result，但展示投影不得回灌模型 |
| Harness Inspector 技术记录、原始事件名、correlation、错误码、诊断信息 | 默认隐藏，按需显示 | 否 | 只读 durable ledger 投影，default-deny、脱敏、有界 |
| 模型隐藏 reasoning / reasoning token / `<think>` 内容 | 否 | 否 | 不进入公开消息、activity projection 或后续上下文 |
| 未来新一轮用户消息的上下文 | 是/否取决于既有产品消息 | 只允许现有 Session history、memory/context 规则决定 | 不得因为用户看过时间线而自动追加时间线内容 |

> 关键区分：工具结果可能在当前执行轮中作为模型协议要求被模型读取；但“时间线里的摘要/详情”永远不是新的上下文来源，也不会因展示而被再次注入当前或下一轮模型请求。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-1 | 实时执行时间线 | 通过真实主消息入口触发一次会调用工具的任务；任务执行期间，Inspector 按发生顺序显示阶段、工具调用、工具结果、验证和终态，当前节点状态与 Run 终态一致 | 必须 |
| AC-2 | 折叠与详情 | 点击阶段可独立展开/收起；工具输入和结果可分别展开；长内容有明确截断提示；敏感字段不显示 | 必须 |
| AC-3 | 上下文隔离 | 对同一 Run 抓取模型出站 payload/上下文快照，证明 activity、Inspector、技术记录和 UI 投影没有作为新增消息或 system/context fragment 注入；下一轮 follow-up 不携带这些 UI 投影 | 必须 |
| AC-4 | 历史与恢复 | 完成、失败、取消的 Run 重新打开会话或重启桌面端后，时间线可恢复；不把终态重新显示为 running，也不串到其他 Run | 必须 |
| AC-5 | 异常事件容错 | 对乱序、重复、缺字段、超长文本、工具拒调/失败事件，前端保持可渲染；不重复计数、不伪造成功、不因一条坏事件崩溃 | 必须 |
| AC-6 | 旧行为回归 | 原有消息、工具轨迹、Artifact、权限卡、停止/空闲状态、Session 切换继续工作；隐藏时间线不影响 Agent 执行和最终回复 | 必须 |
| AC-7 | 真人可用性 | Windows/macOS 桌面真实点击测试中，用户能找到入口、看到当前步骤、展开工具详情、关闭面板并继续聊天；截图和日志证明动作走真实 UI，不使用 WebSocket 直注代替 | 必须 |

## 非功能 / 边界

- **安全/隐私**：UI 展示只使用 public projection；文件内容、完整命令参数、token、cookie、API key、密码和 provider 私密响应不得直接展示。
- **一致性**：同一 canonical `root_run_id` 下的事件必须归属同一时间线；晚到结果不能复活已终止 Run。
- **幂等**：相同 event identity 重复到达只保留一条；乱序事件按 durable sequence/稳定 identity 归约。
- **性能**：时间线采用有界窗口和内部滚动；长结果不得撑爆消息页；详情按需加载。
- **兼容**：旧历史记录缺少新字段时显示明确的“记录不完整/旧账本”状态，不虚构步骤。
- **可审计**：必须能从 UI 条目追溯到稳定 run/event/tool reference，但普通用户默认不看到内部 ID。

## LLM 行为变异清单

本功能由 LLM tool call/公开进度 payload 驱动端侧投影，因此必须覆盖：

- 事件乱序：先到结果、后到调用；UI 仍按稳定 identity 归并。
- 事件重复：同一调用/结果重复发送；UI 不重复计数或生成两条步骤。
- Schema 违约：缺少状态、工具名、阶段标题或 identity；UI 使用安全降级，不伪造成功。
- 超长文本：工具参数/结果超过展示上限；UI 截断并保留“已安全截断”提示。
- 拒不调用工具：模型直接回答；时间线显示无工具步骤的正常完成，而不是一直等待。

## 测试场景矩阵

| scenario_id | input_class | exact_input | primary_risk | gate_type | required | manual_required | terminal_expectation | quality_bar |
|---|---|---|---|---|---:|---:|---|---|
| S-1 | 工具驱动的文件/命令任务 | “读取项目中的一个公开测试文件，运行一个只读检查，然后告诉我结果。” | 真实工具链和实时时间线 | positive-value | 是 | 是 | completed + 非空有效回复 + 时间线完整 | 阶段顺序、工具调用/结果和终态均可读 |
| S-2 | 无工具的普通问答 | “用一句话解释什么是 durable run。” | 无工具路径不被错误显示为卡住 | positive-value | 是 | 是 | completed + 非空回复 | 时间线可显示正常完成且无伪造工具步骤 |
| S-3 | 工具失败/低证据任务 | “读取一个确定不存在的文件，并诚实说明结果。” | 失败证据与诚实降级 | negative-safety | 是 | 是 | failed 或 completed_with_recovery，不能伪造成功 | UI 显示失败工具与最终可理解说明 |
| S-4 | 长上下文 follow-up | 同一会话完成至少 10 轮普通对话后，再执行 S-1 并追问结果 | 时间线不进入后续 context、跨轮不污染 | positive-value | 是 | 是 | 两次独立 Run 均正确收尾 | 出站 payload 不含 activity/Inspector 投影 |
| S-5 | 冷启动/历史恢复 | 清理或新建 userdata，首次进入主消息页后执行 S-1；重启后重新打开该会话 | stateful init、持久化恢复 | stateful-init | 是 | 是 | 功能页可用，历史时间线可恢复 | 不串 Run，不把终态复活为 running |

## 测试义务矩阵

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---|---|---|---|---|---|
| TO-A1 | delivery | AC-1 | REAL-TIMELINE | S-1 真人桌面运行并检查截图/日志 | 证明主功能真实可达 |
| TO-A2 | delivery | AC-2 | PUBLIC-PROJECTION | 前端组件/快照测试 + S-1 展开输入/结果 | 证明折叠和详情边界 |
| TO-A3 | delivery | AC-3 | CONTEXT-LEAK | 出站 payload 断言 + follow-up S-4 | 证明显示数据不进入上下文 |
| TO-A4 | delivery | AC-4 | RECOVERY-MISMATCH | 重启/历史恢复自动化 + S-5 真人验证 | 证明 durable projection 一致 |
| TO-A5 | delivery | AC-5 | MALFORMED-EVENT | 乱序/重复/缺字段/超长 fixture 测试 | 证明 LLM payload 容错 |
| TO-A6 | delivery | AC-6 | REGRESSION | 受影响前端/后端回归 + full-surface smoke | 证明既有入口不退化 |
| TO-A7 | delivery | AC-7 | REAL-UI | MCP 真人点击/输入/截图/日志 | 证明用户真的能操作 |
| TO-R1 | change-risk | AC-1, AC-3 | CROSS-LAYER-CONTRACT | backend event schema 与 frontend parser 契约测试 | 防字段单位/身份漂移 |
| TO-R2 | change-risk | AC-3 | CONTEXT-AUTHORITY | Context assembler 禁止读取 activity projection 的静态/运行时检查 | 防隐式回灌 |
| TO-R3 | change-risk | AC-4, AC-5 | IDEMPOTENCY | 相同 event identity 重放和终态晚到测试 | 防重复/复活 |

## Assurance contract 摘要

- Profile：`standard`
- 受保护资产：用户会话内容、provider 凭据、工具参数/结果、Run 状态、模型上下文边界、持久化审计记录。
- 可信假设：本地 OS 与开发者账户可信；现有 canonical run/event authority 和 public redactor 可用；测试 provider/桌面环境按测试配置提供。
- 范围内失败/对手：错误目标 Run、乱序/重复/缺字段/超长事件、工具失败、事件投影失败、跨 Run 串线、UI 把 excluded 数据回灌上下文、敏感字段误展示。
- 明确范围外：宿主 OS 被攻陷、provider 本身泄漏未进入本地投影的数据、未经批准的远程协作者读取本机原始数据库。
- 最大可接受影响：单条 activity 丢失或降级只能导致“记录不完整”；不得改变 Agent 终态、不得重复执行工具、不得泄漏凭据、不得把 excluded 数据加入模型请求。

## 完成的定义

- AC-1 至 AC-7 全部有 required PASS 证据。
- 所有 delivery/change-risk obligation 有对应 PASS testcase。
- activity 数据分类、`context_visibility=exclude` 标记、public projection 和模型 context assembler 的边界文档同步完成。
- 自动化测试、类型检查、构建、全表面冒烟和真实桌面测试均完成；架构事实源已更新。

## 2026-08-20 增量验收：运行中思考区与完成后折叠

### 用户确认的交互口径

- 展示的是可公开的工作叙述与执行摘要，不展示 provider 隐藏 reasoning token、原始
  `reasoning_content` 或未经公开的 Chain-of-Thought。
- Agent 运行且存在公开工作叙述时，思考区默认展开并随新叙述更新。
- Run 完成、失败或取消后，思考区自动折叠为“耗时 X”；用户点击标题可再次展开，重复点击可收起。
- 历史会话恢复时，终态思考区默认折叠；仍在运行的思考区默认展开。

### 增量功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-8 | SDK 公开思考投影 | SDK Run 的公开 assistant 工作叙述能生成 durable `reasoning_summary`；工具前叙述、工具观察与状态摘要按 canonical Run 投影，`context_visibility=exclude`，不把隐藏 CoT 或 UI 摘要加入模型上下文 | 必须 |
| AC-9 | 思考区展开/自动折叠 | 真实任务运行时思考区默认展开；Run 终态后自动折叠并显示可读耗时；点击可展开查看全部公开叙述，再次点击可收起；刷新/重启后的终态保持默认折叠 | 必须 |

### 边界与 LLM 行为变异

- 无公开工作叙述：不渲染空思考区，工具卡和最终回复照常显示。
- 重复/乱序摘要：按 `summary_id` 幂等更新，不生成重复思考区，不把终态重新展开为 running。
- 缺少开始时间或终态：耗时安全降级为“思考中”或“已完成”，不得显示负数/NaN。
- 超长公开叙述：沿用有界 public summary；折叠容器内部可滚动，不撑破消息页。
- Provider 返回隐藏 `reasoning_content`：只允许转换为无内容 activity 信号，不直接进入可展开正文。

### 适用性

- `input_sensitive=false`：验收目标是确定性的 UI 展开状态、终态折叠和公开投影边界；不以回答语义质量判定。
- `llm_payload_driven=true`：公开 assistant/tool 事件驱动 reasoning summary 与折叠状态，必须覆盖重复、乱序、缺字段、超长和拒不输出叙述。
- `stateful_init=true`：历史恢复依赖 SessionDB hydration，必须覆盖重启后终态默认折叠。

### 增量测试义务矩阵

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---|---|---|---|---|---|
| TO-A8 | delivery | AC-8 | — | SDK tool run 产生 durable public reasoning summary，且上下文可见性为 exclude | 直接证明公开思考投影接通且不泄漏隐藏 CoT |
| TO-A9 | delivery | AC-9 | — | 组件测试覆盖 running 展开、terminal 自动折叠、点击再展开与历史恢复 | 直接证明参考图交互 |
| TO-R4 | change-risk | AC-8 | CONTEXT-LEAK | Provider/Session 出站上下文不包含 reasoning summary 或 reasoning_content | 跨层接线不能污染下一轮模型上下文 |
| TO-R5 | change-risk | AC-9 | STALE-UI | 重复/乱序/缺时间 fixture 不产生重复、负耗时或终态复活 | 防 LLM 事件变异破坏折叠状态 |
| TO-R6 | change-risk | AC-8, AC-9 | REAL-UI | Computer Use 真实发送工具任务，运行中截图展开，完成后截图折叠，再点击展开 | 证明生产入口和真实交互可达 |

## 2026-08-21 增量验收：SDK Context 单一事实源与旧链路退役

### 主要矛盾与范围

当前生产文字入口已切到 SDK Runtime，但出站消息由 `_assemble_sdk_messages` 临时组装，Context
弹窗和用量采样仍读取旧 AgentLoop/ContextAssembler 的 registry、facts 和 `last_usage`。因此同一轮
同时存在“实际 Provider Context”“旧弹窗估算”和“空的 durable usage”三种相互矛盾的事实。

本增量包含：审计所有用户可达的 SDK 文字入口及其恢复路径；建立一次准备、同一快照同时驱动
Provider 请求、Context Inspector 和 durable usage；恢复既有产品规则要求的 Persona、Memory、Skill、
附件、项目上下文和历史过滤；使工具目录与真实 SDK catalog 一致；删除或封闭仍可误用的旧读路径。

明确不包含：重新启用已关闭的 Voice；改变 Memory 的权限/召回策略；公开隐藏 CoT；把 UI activity、
Harness 技术记录、artifact 卡或 `context_visibility=exclude` 投影加入模型上下文；修改 Provider 的
计费价格或外部 API 契约。

### 增量功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-10 | Context 单一准备 authority | 每个 fresh SDK Run 只调用一次产品 Context preparation；其冻结结果同时成为 SDK RunStart、Provider 首轮请求和 Inspector 构成的事实源，不再由入口层另组一套消息 | 必须 |
| AC-11 | 产品 Context 内容完整 | 按既有策略应注入的 Persona/system、Memory、Skill、Session 历史、当前用户消息、附件和项目/任务快照全部出现在真实 Provider 请求；未命中的可选项明确为空而不是伪造 | 必须 |
| AC-12 | Context 隔离与同 Run 协议 | fresh Run 只读取 `conversation` allowlist；activity/reasoning summary/artifact/技术投影不进入后续 Run；同一 Run 的 assistant tool call、必要的私有 DeepSeek `reasoning_content` 和 tool result 仅按 Provider 协议进入后续 tool turn | 必须 |
| AC-13 | 真实 Provider 用量权威 | 每个成功 SDK Provider attempt 的实际 input/output/cache usage、实际 provider/model、canonical root/request/attempt identity 被幂等写入 Context usage history；取消/unknown/失败不伪造测量；重启后可恢复 | 必须 |
| AC-14 | 工具目录一致 | Provider 请求、SDK executor 和 Inspector 使用同一个冻结 product catalog；工具名、数量及完整 schema token 构成一致，不再读取 legacy V2 registry 或用 `count × 60` 伪装真实值 | 必须 |
| AC-15 | Context UI 如实展示 | 顶部 ring 显示最近真实测量与实际模型；弹窗展示最后一次冻结请求的真实构成、历史变化和“估算/实测”差异；无测量、旧记录或 Provider 不返回 usage 时明确标注来源和缺失，不显示 `(no model yet)` 假象 | 必须 |
| AC-16 | 旧链路残留审计与封闭 | 对聊天入口、SDK ingress、Context 弹窗、用量持久化、恢复/水合、工具目录和附件路径做引用审计；所有仍请求旧 AgentLoop/legacy registry/facts probe 的生产分支要么切到新 authority，要么证明仅为测试/兼容并不可被生产入口到达 | 必须 |
| AC-17 | 回归、恢复与多会话隔离 | 普通问答、工具多轮、附件/项目、长历史、取消、Provider 缺 usage、Session 切换和完整重启均保持正确；Context 快照和 usage 不跨 Session/Run 串线，不影响停止、最终回复或工具执行 | 必须 |

### 非功能与数据边界

- **单次准备**：Context preparation 是可审计的冻结事实；Inspector 不得为了显示再次执行 Memory
  recall、Skill disclosure 或其他可能产生不同结果的动态准备。
- **幂等与顺序**：provider attempt 使用稳定 source identity；重复 settle 不重复采样，多轮 tool
  attempt 按实际完成时间展示，晚到旧 attempt 不覆盖更新的 authority。
- **隐私**：Inspector 只显示有界分类、计数和脱敏预览；API key、cookie、隐藏 reasoning、完整
  工具参数/结果及私密附件内容不得进入公开投影或测试报告。
- **性能**：不得为了弹窗查询重新序列化全部历史；Provider payload 大小不因双写而增加；工具 schema
  token 可在冻结请求上计算并缓存。
- **兼容**：没有 usage 的 Provider、迁移前 binding-only Session 和旧历史必须可打开；显示明确的
  `unavailable/legacy incomplete`，不得补造 0-token measured sample。
- **分片交付**：实施计划必须拆为不超过三个高风险子系统的垂直 slice，各 slice 单独过自动化与
  affected-surface smoke；最后再做跨 slice 真机与 full-surface gate。

### 适用性

- `input_sensitive=true`：Persona、Memory、历史、附件、项目快照和工具 schema 会随用户输入及 Session
  状态改变，必须用语义不等价输入验证真实出站质量。
- `llm_payload_driven=true`：模型 tool call、usage 缺失/异常和多轮结果直接驱动 durable usage 与 UI，
  必须覆盖乱序、重复、schema 缺失、超长载荷和拒不调用工具。
- `stateful_init=true`：Provider registry、Session binding、Memory/Skill 服务和 SDK Runtime 均为异步
  注册并依赖本地持久化，必须覆盖隔离 userdata 冷启动及完整重启恢复。

### LLM / Provider 行为变异清单

- **乱序**：旧 attempt usage 晚于新 attempt 到达；历史保留两条，当前 authority 仍指向最新稳定顺序。
- **重复**：相同 attempt settle/重放多次；只生成一个 sample，不重复累计或画点。
- **Schema 违约**：Provider 缺 `usage`、缺 model、usage 字段非整数或 tool call 缺公开进度；主回复和
  工具状态可继续安全收束，UI 标注 usage unavailable，不制造 measured=0。
- **超长载荷**：长历史、完整工具 schema、长附件和长工具结果触发既有预算/截断策略；Inspector
  有界渲染，Provider 请求与 UI 均不崩溃。
- **拒不调用工具**：模型直接回答工具型请求；产生一次真实文本 attempt usage，UI 不伪造 tool
  message，Context 仍能在终态展示实际构成。

### 增量测试场景矩阵

| scenario_id | input_class | exact_input | primary_risk | gate_type | required | manual_required | terminal_expectation | quality_bar |
|---|---|---|---|---|---:|---:|---|---|
| CTX-1 | 普通问答与 Persona/历史 follow-up | “根据我们刚才聊过的内容，用两句话告诉我你记得我的哪项偏好；不需要调用工具。” | prepared history/persona 与无工具 usage | positive-value | 是 | 是 | completed + 非空回答 + measured usage | 回答引用本会话真实既有内容，Inspector 与真实请求角色/数量一致 |
| CTX-2 | Memory 与长历史 | 在同一会话完成至少 10 轮后输入：“我之前说过住在哪里、喜欢喝什么？不确定就直说。” | Memory/历史预算、长上下文和隔离 | positive-value | 是 | 是 | completed + 非空回答 + history sample | 只回答确有记录的事实，不引用 activity/tool UI 文案；长会话为独立 root run |
| CTX-3 | 附件/项目工具任务 | “读取我刚附上的这个公开文本文件，并告诉我第一行；需要的话使用文件工具。” | 附件、项目快照、工具 schema 与多轮 usage | positive-value | 是 | 是 | completed + 正确首行 + ≥2 attempt samples（若调用工具） | Provider 首轮能看到附件引用/内容预算，工具结果正确，Inspector 构成与实际请求一致 |
| CTX-4 | 失败与隔离对抗 | “读取 `/definitely-not-present/context-canary.txt`；找不到就明确说明，不要猜。” | tool failure、无伪造成功、excluded 投影不回灌 | negative-safety | 是 | 是 | completed/failed 可解释 + usage 有据可查 | 不虚构文件内容；下一 fresh Run 的出站 payload 不含失败卡/公开进度摘要 |
| CTX-5 | 冷启动与重启恢复 | 隔离 userdata 首次配置 Provider 后执行 CTX-1，再完整退出重启并打开 Context 弹窗 | stateful init、provider/model binding、durable hydration | stateful-init | 是 | 是 | 首次调用可用，重启后测量/模型/历史仍在 | 不需第二次设置；ring 与弹窗恢复同一 sample，不跨会话串线 |

CTX-1 至 CTX-4 至少执行两个独立完整真实 LLM root runs，其中 CTX-2 必须位于 ≥10 轮历史会话；
CTX-5 使用隔离 userdata，不破坏用户现有 Provider 配置。

### 增量测试义务矩阵

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---|---|---|---|---|---|
| TO-A10 | delivery | AC-10 | — | prepared snapshot identity 同时出现在 RunStart、Provider request 与 Inspector fixture | 直接证明单一事实源 |
| TO-A11 | delivery | AC-11 | — | Persona/Memory/Skill/history/current/attachment/project 参数化出站契约测试 + CTX-1/2/3 | 证明产品 Context 内容恢复完整 |
| TO-A12 | delivery | AC-12 | — | conversation allowlist、exclude 负向 canary、同 Run tool/reasoning 协议测试 | 证明上下文隔离且不破坏 Provider 协议 |
| TO-A13 | delivery | AC-13 | — | 真实 SDK response usage → SessionDB history/state；success/unknown/cancel/missing usage 矩阵 | 证明 durable usage 权威正确 |
| TO-A14 | delivery | AC-14 | — | catalog snapshot 的工具名/schema 与 Provider request、executor、Inspector 三方精确相等 | 证明工具目录没有 legacy 分叉 |
| TO-A15 | delivery | AC-15 | — | ring/modal 组件与 WS hydration 测试 + CTX-1/3/5 真人点击 | 证明用户看到真实模型、用量和构成 |
| TO-A16 | delivery | AC-16 | — | 生产入口引用图 + legacy symbol denylist/wiring test | 证明旧读路径不可达而不是仅口头弃用 |
| TO-A17 | delivery | AC-17 | — | 多 Session、重复/乱序、长历史、重启、停止回归矩阵 | 证明共享 Context 基础设施无回归 |
| TO-R7 | change-risk | AC-10, AC-13 | DUAL-AUTHORITY | 同一 Provider attempt 的 request fingerprint、usage sample 和 UI sample lineage 可关联 | 防再次产生三套事实 |
| TO-R8 | change-risk | AC-11, AC-12 | CONTEXT-LEAK | 敏感/exclude/其他 Session canary 在真实 request_json 中均不存在 | 修改准备链路涉及隐私边界 |
| TO-R9 | change-risk | AC-13, AC-17 | IDEMPOTENCY-ORDER | settle 重放与晚到 attempt 的 reducer/DB 事务测试 | 共享 durable state 必须防重复和倒退 |
| TO-R10 | change-risk | AC-14, AC-17 | CATALOG-DRIFT | product catalog generation 变化与 runtime refresh 测试 | 工具 registry 是共享运行基础设施 |
| TO-R11 | change-risk | AC-15, AC-17 | HYDRATION-REGRESSION | Session 切换、断线重连、完整重启后 UI 权威一致 | 前后端水合契约受影响 |

### 本增量完成定义

- AC-10 至 AC-17 全部有 required PASS 证据，所有 TO-A10…A17、TO-R7…R11 均有绑定 testcase。
- 实际 Provider request、SDK usage ledger、Session Context authority 和 UI 展示共享可追溯 lineage。
- 旧入口审计无未解释的生产可达 legacy Context 读取；保留兼容代码均有不可达/只读边界测试。
- 自动化、类型检查、构建、critical/affected/full-surface smoke、至少两次真实 LLM Run、长历史与
  隔离 userdata 冷启动全部通过；ARCHITECTURE 事实源同步，最终以 plan-test gate receipt 为准。
