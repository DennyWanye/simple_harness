# 行为契约（已批准）

## 术语和关系

- **Session**：一段对话及其模型绑定的容器；一个 Session 可以先后包含多个 Root Run。
- **Root Run**：一次用户请求的最终结果 authority；同一 Root Run 可以创建零个或多个 Child Run。
- **Child Run**：Root Run 委派的工作流或子任务；它的失败不等于 Root Run 必然失败。
- **Session 附属调用**：为当前 Session/Root 服务的抽取、分类、改写、检查、匹配等 LLM 调用。
- **系统维护调用**：不代表任何单一 Session 的跨 Session 整理、索引或维护任务。
- **公开运行视图**：从真实 execution ledger 确定性生成、给用户看的聚合结果；它不是第二套执行状态机。

实体关系：`Session 1 ── N Root Run 1 ── N Child Run`。模型绑定属于 Session/Root 请求上下文，不属于窗口或全局变量。

## Before / After

| ID | 现在 | 修改后 |
|----|------|--------|
| BC-1 | 主 Agent 通常使用 Session 选择模型，但部分附属调用直接读取全局模型；显式绑定失效时还会静默回退全局模型。 | 当前 Session 的主调用和全部 Session 附属调用只使用该 Session 的绑定；显式绑定失效时给出明确错误并停止该次调用，不换成别的模型。只有从未绑定模型的 Session 才使用全局默认链。 |
| BC-2 | 某些附属调用遇到 401/402 可能重复请求，并把失败扩散到主 Run。 | 错误按真实影响范围隔离：凭据错误覆盖该 provider 配置，账户额度错误覆盖该 provider 账户，模型不存在只覆盖该模型，限流覆盖对应 workload，网络/5xx 覆盖 endpoint；冷态或恢复探测时同一范围只发一个物理请求。非关键附属能力按既定策略降级，主 Run 继续；不可省略的用户语义失败由 Root Run 明确恢复或结束，不能伪装成功。 |
| BC-3 | Context Usage 可能把旧模型/窗口与最新 token 样本拼在一起；没有样本时猜全局模型。 | Context Usage 只返回一个带 `source/version/sample_id` 的一致状态：实测、压缩后状态或仅模型绑定；没有证据时显示未知，不猜。 |
| BC-4 | Inspector 用固定 LIMIT 截取账本，前端不知道记录是否完整；前端再从零散事件猜阶段。 | 后端分页读取完整事实并返回 `total/cursor/truncated/projection_complete`；后端确定性生成少量语义阶段，前端只负责展示。 |
| BC-5 | Child Run 失败和 Root Run 后续完成并排出现，用户容易把局部失败理解成整个任务失败。 | Root Run 仍是最终结果 authority；只有真实的 child failure → FailureReport → replacement Attempt → later root completion 因果链完整时，公开视图显示“已接管并完成”。原始 Child Run 失败记录保留。 |
| BC-6 | 工具卡片可能直接消费 `prepared/outcome`，命令、正文和超长输出占据页面且可能泄露敏感内容。 | 公共工具详情默认拒绝原始字段；每种工具只输出紧凑动作摘要、状态和有界脱敏结果。结果默认收起，可展开；未知工具只显示名称/状态/引用，不显示原始参数或结果。 |
| BC-7 | 每条 provider/tool/event 都可能成为图节点，长任务形成几十或上百节点。 | 顶层只显示实际出现的少量语义阶段；阶段内按需展开步骤、工具、失败和恢复。工作流有明确总步数时显示 `n/m`，通用 ReAct 未知总数时不伪造总步数。 |

## 保留

- Session、Root Run、Child Run 的现有持久化身份和 execution ledger 原始终态。
- 用户停止/取消的语义，以及晚到事件不能越过 Root terminal fence 的规则。
- 未绑定模型的新旧 Session 使用全局默认 provider chain 的兼容行为。
- 原始技术账本供后端诊断和审计使用，但不再直接作为公共 UI payload。
- 不公开隐藏 reasoning；只展示模型已经输出的用户可见说明、计划、动作和工具事实。

## 删除

- 显式 Session 模型绑定失效后静默切换到其他模型。
- Session 附属调用直接读取 `local_llm or cloud_llm` 的生产绕行。
- 没有完整性标记的硬 LIMIT，以及前端依据文案猜根结果/阶段/安全字段。
- 公共 UI 直接展开原始 `prepared_json/outcome_json`。

## 改变

- Inspector WebSocket 公共 contract 升级为版本化、可分页、可恢复的 read model。
- Context Usage 改成耐重启的确定性 reducer 输出。
- 运行图从逐事件流水改成“阶段 → 步骤/工具/失败恢复”的分层视图。
- Session 附属 LLM 的调用接口必须携带显式 workload 和 Session/Root 身份。

## 批准状态

2026-08-03 15:52:26 +08:00，用户回复“全部同意”，批准 BC-1～BC-7 及
BC-STALE-001、BC-PUBLIC-002、BC-GRAPH-003、BC-CONTEXT-004。批准时的契约 hash、来源范围和
原始消息记录见 [`approval.md`](./approval.md)。

## 既有行为变更授权（已批准）

| behavior_change_id | 旧行为 / oracle | 新行为 | 作用域 | 有效期 |
|--------------------|---------------|--------|--------|--------|
| BC-STALE-001 | `test_pinned_to_deleted_provider_falls_back_to_chain` 等测试要求“显式绑定 provider 被删除后改走 global chain”；设置删除流程会清除该 binding。 | provider 被删除/禁用、model 失效或 provider incarnation 不一致时，保留显式 binding provenance并 fail closed；只有原本未绑定的 Session 才走 global chain。旧测试改名并反转为“不可静默 fallback”，同时新增 unbound 兼容测试。 | 切片 A 的 Session provider resolution、设置删除流程及其 black-box testcase；不改变未绑定 Session。 | 本计划两个切片完成并生成有效 gate receipt 前；后续改变需新授权。 |
| BC-PUBLIC-002 | Inspector/消息组件测试允许或断言公共响应直接包含 provider input/output/policy 和 tool prepared/outcome；折叠仅是 UI 状态。 | 公共 contract 删除所有 raw provider/tool payload，只返回 default-deny 的有界脱敏投影；原始数据仍保留在 durable ledger 并由内部诊断测试验证。需反转 `backend/tests/companion/test_provider_dispatch.py`、`test_public_tool_projection.py` 中的公共断言，以及 `AgentActivityMessage.test.tsx`、`HarnessInspectorPanel.test.tsx` 的 raw UI fixture；不得删除 ledger 原始数据测试。 | 切片 B 的 Inspector/消息公共 contract 和列出的测试；不改变内部 durable ledger。 | 同上。 |
| BC-GRAPH-003 | `HarnessInspectorPanel.test.tsx` 等测试把 provider/tool/terminal 逐事件记录作为默认运行图节点。 | 默认运行图只渲染后端 semantic phases；逐事件事实保留在可展开“技术记录”。测试改为断言阶段、步骤、工具归属和完整性，不再把 raw feed 数量当图节点数量。 | 切片 B 的运行图默认视图；不删除技术记录入口。 | 同上。 |
| BC-CONTEXT-004 | Context Usage 冷恢复测试允许从有限历史中拼出模型/窗口和最新 token，或无样本时显示全局 0/window。 | 冷恢复只读 materialized authority；无完整样本时返回 Session binding-only/unknown，不拼字段、不伪造窗口。旧 history 仍可在明细图中查看。 | 切片 A 的 Context Usage 公共响应及相关测试。 | 同上。 |
