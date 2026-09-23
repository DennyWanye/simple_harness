# 第一阶段：基础 Agent 类（BaseAgent）与独立短期记忆

版本：BA-v1.0  
日期：2026-09-10  
状态：**代码级实施规范；不是已合入 SDK 的补丁，也不宣称已经通过实际 SDK 集成测试。**

## 0. 本次范围与依据

本计划依据用户新上传的《Agent 编排层完整设计方案》，并按照本次新要求收敛：

> 先从现有执行内核演进出一个可批量创建的基础 Agent 类；每个 Agent 具有独立生命周期、工具执行能力、有界动态 Context，以及仅属于自己的短期记忆。对外不再使用 Run 作为产品对象名称。暂不接入用户记忆，不实现上层 Agent 编排。

### 0.1 与新文档的关系

| 新文档章节 | 本阶段采用的部分 | 本阶段不实施的部分 |
|---|---|---|
| §9 Role 与 Model Router | instructions、模型配置引用、工具权限上限 | 自动选角色、自动升级模型、搜索多样性控制器 |
| §10 Context Builder 与 Retrieval | 给当前 Agent 组装相关输入、执行历史、限制和输出要求 | Task DAG 距离、共享 Verified Knowledge、跨分支检索 |
| §12 Agent Runtime 与生命周期 | 可替换执行者、工具循环、持久结果 | 把每次故障都转成新的业务 Attempt |
| §13 Agent 输出协议 | 结构化结果、状态、产物引用和成本事实 | 强制要求 mission_id/task_id/attempt_id |
| §16–18 持久化与并发 | 短事务、幂等、租约、费用与基本并发限制 | Mission／Task 分层调度与研究资源分配 |
| §20–21 工作区和工具 | 每 Agent 的受控工作区、工具网关 | 跨分支产物合并、生产部署编排 |
| §28 分阶段路线 | 本阶段作为该完整路线的前置基础 | 不把原文“第一阶段”全部纳入本次 |

原文 §12 将 Agent 描述为可替换执行单元，本计划将“可替换”落实在物理执行者：Agent 的持久身份在关闭前可以恢复；不要求每个 Agent 都长期常驻。原文 §16.4 的“Lease 过期 → 新建 Attempt”不直接用于恢复底层模型／工具动作：本阶段首先恢复同一 AgentTurn，核对未完成动作，再由明确的新输入决定是否开展新工作。这是本次为底层安全提出的细化，不是声称原文已定义此规则。

### 0.2 本轮核对的代码基线

仓库：`DennyWanye/simple-harness-sdk`  
main：`fd12e7dd7122786865ba61c19c48855a8eadcd8c`

本轮直接复读：

- `runtime/context.py` 1–215：load／append；追加后保存整个 messages 和 append_receipts 快照。
- `runtime/drivers/react.py` 250–320：默认最终响应映射到 COMPLETED 和 ConversationTurnOutput。
- `runtime/kernel.py` 190–256：DriverResult 的对话输出只允许绑定 COMPLETED。
- `runtime/consumer_adapter.py` 110–180：Memory 是可选注入；不配置 Memory 并不自动实现本计划的 Agent 语义。
- `runtime/drivers/react_loop.py` 360–430：新 Provider 请求准备和 Context authority 插槽。

其余修改位置沿用本会话此前已经读取的执行链，但仍需在每个 PR 开工时定位方法并核对调用者。没有修改远端仓库，没有执行真实 SDK 测试，没有检查本次范围外的 Host 实现。

## 1. 最终定义

### 1.1 BaseAgent 是什么

**BaseAgent 是一个可寻址、可配置、可恢复的逻辑 Agent 实例。**它拥有稳定的 `agent_id`、能力配置、输入队列、本轮执行状态、自己的短期记忆和结构化输出记录。

- 不是一份模型权重：多个 Agent 可以共用同一个模型服务。
- 不是一条 HTTP 请求：一次工作可以调用模型和工具多次。
- 不是一个永久占着线程的进程：等待时只需要持久记录。
- 不是用户的 Mission 或 Task：本阶段不管理目标分解。
- 不是一个运行时对象的 Python 引用：对象释放不代表持久 Agent 被删除。

### 1.2 三个概念足够

| 概念 | 含义 |
|---|---|
| `AgentConfig` | instructions、模型引用、工具上限、Context 策略、执行限制等不可变配置 |
| `BaseAgent` | 一个独立实例的对外 handle，通过共享 Runtime 管理真实状态 |
| `AgentTurn` | 接收一个普通输入后的一次有限处理，可包含多次模型／工具调用及等待恢复 |

AgentTurn 是上一版 Activation 在本阶段的对外简化称呼。它不是新 Agent，不等于一次 LLM turn，也不是原文上层业务 Attempt。SDK 内部可沿用 activation 实现名称，不必再建重复实体。

### 1.3 生命周期约定

```text
创建 Agent → IDLE
                │ 收到输入
                ▼
             RUNNING
                ├── 模型／工具循环
                ├── WAITING：本轮等待授权或 UNKNOWN 核对 → 恢复本轮
                └── 结果提交 → IDLE（可以继续接收下一条输入）

明确关闭 → CLOSING → CLOSED
内核完整性故障且无法安全继续 → FAILED（要求显式恢复或新建）
```

公共状态由执行状态与当前 AgentTurn 投影出来，不额外维护一份可独立修改的生命周期真相。可排队但尚未进入模型调用的处理，可以显示 RUNNING 加 `stage=queued`；精确阶段由 TurnSnapshot 提供。

- 一次回复完成不关闭 Agent。
- 一次 Turn 业务执行失败不默认永久杀死 Agent；Turn 记录失败，Agent 可回到 IDLE，等待新输入。
- 工具结果未知时不得直接允许下一条普通输入以新身份重做同一动作。
- `AgentRuntime.shutdown()` 停止本进程并释放控制权，不批量关闭 Agent。
- `BaseAgent.close()` 是持久生命周期操作，禁止接收新的普通输入；未完成动作按原核对协议收敛。
- `close()` 不等于擦除历史。历史删除是独立、明确授权的存储管理操作。

## 2. 明确排除的内容

本阶段不实现：Mission、Task DAG、Planner、Manager、Search Controller、Allocator、Blackboard、自动 Verifier Pipeline、跨 Agent 共享记忆、自动子 Agent 委派、用户画像和用户长期记忆。

现有 Workflow 如需使用，仅通过现有合法工具桥暴露；本阶段不新建 Workflow 编排器。旧 workflow.spawn 若依赖旧 checkpoint／父子协议，默认不在新 Agent catalogue 暴露，除非补齐适配测试。

不引入 MemoryManager，不调用用户 recall／append／mutation，不使用空实现假装通过原生产 Memory authority 检查。身份、文件权限、工具授权不因排除用户记忆而取消。

## 3. 最小架构

```text
                       应用代码
                          │
                  AgentRuntime.create_many
                  （创建工厂 + 执行服务）
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
       BaseAgent A     BaseAgent B     BaseAgent C
       独立短期记忆     独立短期记忆     独立短期记忆
       独立输入／输出   独立输入／输出   独立输入／输出
          └───────────────┼───────────────┘
                          │
                  共享的确定性执行池
          同一 Agent 串行，不同 Agent 有限并行
                          │
           现有 Provider／Effect／Lease／UoW
                          │
                 模型服务与受控工具
```

`AgentRuntime` 不是另一位大模型主控，也不做研究优先级决策。它只负责工厂、排队、公平限流、恢复和资源生命周期。

共享：数据库管理器、模型客户端、embedding 客户端、工具注册表、执行池。

隔离：Agent 的配置绑定、输入队列、Context 选择、session 历史、工作笔记、执行预算、输出和工作目录。

一个配置模板生成 100 个实例，不意味着创建 100 份模型权重、100 个数据库服务，或同时启动 100 次推理。

## 4. 对外 API

下面都是**拟实现接口**，当前 SDK 不存在这些导入；不得将示例当作已可在现有版本运行的程序。

### 4.1 配置合同

```python
@dataclass(frozen=True, slots=True)
class ContextPolicy:
    policy_id: str
    max_input_tokens: int
    output_reserve_tokens: int
    safety_margin_tokens: int
    recent_history_soft_tokens: int
    retrieved_history_soft_tokens: int
    summary_soft_tokens: int

@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_pending_inputs: int
    max_model_calls_per_turn: int
    max_tool_calls_per_turn: int
    turn_deadline_seconds: float
    lifetime_cost_limit_micros: int | None

@dataclass(frozen=True, slots=True)
class AgentConfig:
    name: str
    instructions: str
    model_profile_ref: str
    tool_names: tuple[str, ...]
    context: ContextPolicy
    limits: AgentLimits
    short_memory_mode: Literal['hybrid'] = 'hybrid'
```

所有数字拒绝 bool、负数、NaN；嵌套配置不可变；限制文本与数组大小；在规范编码后形成 config_hash。配置里不出现 user_memory、Mission 或 Task 的必填字段。

`model_profile_ref` 在准入时解析成固定模型／tokenizer／chat-template／deployment 能力绑定。模型名字不等于部署窗口。第一版不在活跃 Turn 内自动换模型或改配置；需要变化时在安全边界显式批准新的版本，或者新建 Agent。

### 4.2 BaseAgent 方法

| 方法 | 语义 |
|---|---|
| `submit(input, *, input_id)` | 先持久接收，返回 AgentTurnReceipt；同身份同内容返回旧回执 |
| `wait_turn(turn_id, *, timeout=None)` | 等待已持久化结果；超时携带原回执，不取消真实工作 |
| `ask(text, *, input_id, timeout=None)` | submit + wait_turn 的方便包装 |
| `get_result(turn_id)` | 读取已提交结果或明确 pending／blocked 状态 |
| `status()` | 当前 Agent 与 Turn 的只读快照 |
| `cancel_turn(turn_id, *, command_id)` | 显式取消当前处理；不删除历史和费用，不自动重试 UNKNOWN |
| `close(*, command_id)` | 关闭实例；默认等现有处理安全收敛，拒收新普通输入 |
| `memory.search(query, *, limit)` | 只搜索当前 Agent 已授权的短期历史 |
| `memory.read(record_ref, *, range)` | 精确读取当前 Agent 的原始记录或产物片段 |

Agent 接收用户文本或可信调用方提供的 `HostInput`；原始来源写入信封，不让模型自填 sender/owner。工具或历史引用不会因为被召回而变成新的用户指令。

### 4.3 AgentRuntime 方法

```python
async def create(config: AgentConfig, *, creation_key: str) -> BaseAgent: ...
async def create_many(configs: Sequence[AgentConfig], *, batch_key: str) -> tuple[BaseAgent, ...]: ...
async def open(agent_id: AgentId) -> BaseAgent: ...
async def shutdown() -> None: ...
```

共享构建配置需要明确最大实例数、每批数量、Provider 并发、工具并发、全局待处理上限和索引队列上限。数值由部署设置，不声称固定数量适合所有设备。

### 4.4 使用示例

```python
# 目标 API，需按本计划实现；ports 是调用方注入的真实模型、工具、权限和计数接口。
async with build_agent_runtime(runtime_config, ports) as runtime:
    config = AgentConfig(
        name='code-helper',
        instructions='在获准的隔离工作区分析代码，结论区分证据与推测。',
        model_profile_ref='local-code-profile-v1',
        tool_names=('read_file', 'search_code', 'run_tests'),
        context=context_policy,
        limits=agent_limits,
    )
    agents = await runtime.create_many([config] * 10, batch_key='demo-batch-01')

    receipt = await agents[0].submit('分析 Context 的重复写入问题。', input_id='request-01')
    result = await agents[0].wait_turn(receipt.turn_id)

    # 同一个 Agent，使用自己的历史继续处理；没有创建新实例。
    followup = await agents[0].ask('根据刚才的发现提出修改建议。', input_id='request-02')
```

普通生成输出是 AgentTurnResult，不是 ConversationTurnOutput 强制绑定整个生命周期结束。结果包含 turn_id、状态、公开输出、产物引用、可读限制、usage 引用；不强制输出 Mission/Task 字段，也不在本阶段把 outcome 升格为 VERIFIED。

## 5. 批量创建的可靠语义

### 5.1 创建不是执行

先验证整批配置与资源配额；在一个短事务中保存批次回执、实例配置和稳定身份；提交后构造轻量 handle。

- `batch_key` 作用域为当前已认证 owner/runtime namespace。
- 规范化 configs 的顺序、配置 hash 和数量进入 batch fingerprint。
- 同 key 同 fingerprint：按原顺序返回原 Agent IDs。
- 同 key 不同内容：`batch_identity_conflict`。
- 一批中任一配置不合法：整批不创建。
- 限制批次大小，避免长时间占有 SQLite writer。
- 创建阶段不得调用 LLM、embedding 或任何外部工具。
- 原子创建提交后进程中断，重试通过批次回执取回原实例。

### 5.2 执行隔离与限流

同一 Agent 最多一个开放 Turn 有效执行者；其他输入持久排队。租约过期恢复同一 Turn。不同 Agent 可有限并行。

Provider 并发限制放在即将调用模型的位置。等待授权、等待索引或 Agent IDLE 不占模型槽位。UNKNOWN 尚无法确认外部请求结束时，保留对应 provider-in-flight 配额或降级额度，不能无限补发新请求。

除了调用数，可配置由部署校准的 in-flight token／KV 预算。不能将“100 个 IDLE Agent”和“100 个 256K 请求同时推理”当成同一个容量指标。

V1 采用简单 FIFO 加 Agent 间轮转公平策略，不实现学习型 Scheduler。持久队列是事实，内存 ready queue 可以重建。

## 6. 短期记忆：只有一个 AgentSessionMemory

### 6.1 定义

本阶段一个 BaseAgent 只有一个逻辑短期 session，以 `agent_id` 隔离。`AgentSessionMemory` 是该 Agent 在生命周期内已接收、公开生成、执行观察和工作笔记的集合。

不包含用户跨 Agent 长期画像，不读取其他 Agent 的私有历史，不自动把模型猜测保存为用户事实。

**短期是归属和用途，不等于 RAM-only。**Agent 休眠或进程重启后，尚未结束的工作仍可读取自己的记录。

### 6.2 四种表示，不是四个相互复制的记忆库

```text
AgentSessionMemory
    ├── Journal：原始记录／原文引用，增量持久化
    ├── RecentView：最近完整交互的有界投影
    ├── WorkingNotes：当前输入约束、真实执行状态、来源绑定的可选摘要
    └── HistoryIndex：词面 + 向量，用来找到 Journal／Artifact 原文
```

RecentView、WorkingNotes 和检索结果共同构成本轮 Context；它们不是事实替代品。

- 用户当前明确限制从被接收的输入和配置读取。
- 动作成功／UNKNOWN 从真实 ledger 读取。
- 模型工作笔记标为 derived，带源记录引用，不作为权限依据。
- 记录先保存，再让旧内容退出 Context。
- 召回片段不重新写成一份新的原始对话并再次 embedding。

### 6.3 SQLite 与向量不是二选一

建议第一版：SQLite 保存增量原文引用、词面索引、向量及其模型版本；Python 检索适配器按当前 Agent 范围进行向量排序。可选 sqlite-vec 只作为可替换 adapter，不作为首版协议依赖。

FTS5 是关键词检索能力，不自动等于语义检索。必须注入实际 EmbeddingPort 才能验收 hybrid 路径；HashEmbedder 或词频 mock 不能代表真实语义质量。

使用 FTS5 时核查运行环境支持。中文检索采用经测试的分词输入或字符 n-gram 索引，不假设 unicode61 自动得到中文词边界；trigram 对短于三个字符的检索有局限，需要精确标识符与短词的专门路径。[W02]

物理部署默认共享一个 execution DB；短期检索可同库使用派生表，以减少跨库提交复杂度。外置索引以后再加，但只能经 durable job/receipt 同步，不参与 Agent 成功状态的第二份权威写入。

SQLite 允许多读者但一次只有一个 writer，故保持写事务短小；不要在事务中 await LLM／embedding。[W03]

### 6.4 存储保留

- 开放 Agent 的短期数据不按进程寿命删除。
- `close` 默认只关闭接收和执行，不立即删除。
- 存储配额到达时，先清理可重建派生缓存或明确返回资源压力；不静默删掉唯一源记录。
- 可以配置关闭后的保留期限，但删除前必须检查未决效果、投递、引用与审计要求。
- 不提供模型可调用的清空全部记忆工具，避免其掩盖动作历史或预算消耗。

## 7. 固定工作 Context 预算，内部动态分配

### 7.1 不设一个普遍“最佳 256K”

本阶段固定的是经模型配置指定的 `max_input_tokens`，不是每次必须填满的长度。上一轮图中的 32K／128K／256K 都是配置或说明示例，不是已验证的最佳容量。

选择工作预算需要针对实际模型、量化、模板、任务集以及并发做实验。Context 工程强调有限窗口中的高信号信息选择，并不提供所有模型通用的最佳 token 数。[W01]

### 7.2 完整预算公式

```text
B_input = min(
    context_policy.max_input_tokens,
    deployment.max_input_tokens（若单列），
    deployment.max_total_tokens - output_reserve - safety_margin
)
```

若所选部署不采用输入输出共享窗口，由 ProviderCapabilities adapter 明确给出有效输入限制，而不是套错减法。所有模板 token、工具 schema、当前输入、必要工具消息、摘要和召回都进入实际计数。

使用真实模型计数器或经核准的上界计数。不能用“字符数/4”当通用计数。更换 tokenizer／模板后失效相关计数缓存。

### 7.3 无用户记忆的 32K 输入示例

> K 在本例表示 1024 tokens。这是待校准起点，不是最优承诺。输出与安全余量不包括在下表的 32K 输入中。

| 内容 | 示例软预算 |
|---|---:|
| 角色与核心规则 | 2K |
| 当前目标、必要状态与摘要 | 4K |
| 当前输入 | 2K |
| 本次工具定义 | 4K |
| 最近完整交互（含必要工具返回） | 12K |
| AgentSession 历史召回 | 8K |
| 合计 | 32K |

当前输入超过其软预算时，可以借用可选历史份额；角色规则、当前原始请求与必要工具协议不是被随意裁剪的内容。必需材料超过整个输入上限时，在发送前产生 `context_required_content_too_large`，提供明确的分块／扩大配置处理，不静默截断。

“最近多少轮”不是固定 10/20 轮。选择最新连续、闭合的 ProtocolGroup 后缀，直到预算用完；一个长 Turn 内的旧闭合组也可以退出窗口，不等待整个 Turn 结束。

### 7.4 每个模型步骤的装配算法

1. 读取当前配置绑定、Turn 输入与执行状态；这是精确读取，不依赖相似度。
2. 构建必要部分：instructions、当前目标／限制、当前输入、未结束工具协议、已批准工具定义。
3. 从 Journal 读取有界的近期完整组，而非先读取完整历史再裁剪。
4. 用当前 query、子问题、错误码和文件标识在同 Agent 的历史中检索。
5. 读取候选原文，验证 agent_id、来源、hash、可见性和索引版本。
6. 融合关键词与向量结果；去除近期窗口已有内容、重复片段和不适用的旧版本。
7. 按剩余预算装入摘要、相关历史和近期组；保留工具调用／结果配对。
8. 用真实请求渲染再次计数；必要时只缩减可选内容并再次校验。
9. 保存选择清单、源记录 high-watermark、query hash、索引世代、最终 request fingerprint。
10. 将冻结请求交给既有 ProviderInvocationCoordinator。

相同 Provider request 恢复时不重新检索。若材料撤权而请求尚未交接，拒绝发送并走显式重新规划身份；不在原 request_id 下悄悄换一份 Context。

### 7.5 压缩与大工具结果

旧闭合组原文先在 Journal／Artifact 中可回读。退出热窗口后，建立索引任务。可在接近热历史预算时提前轮换到较低水位，减少每一步摘要抖动；具体阈值属于可测策略。

大型工具结果完整保存为已授权产物；给模型传 return code、必要观察和回读引用。不可修改真实 effect result 或把失败结果改写成成功。

摘要优先采用结构化、确定性状态；语义摘要可选。摘要调用也走现有 Provider 账本并计入当前 Agent 的成本。摘要失败时记录降级；不用“摘要的摘要”无限替代源记录。

### 7.6 query 与召回

自动 query = 当前输入 + 当前阶段／问题 + 明确实体引用。第一版不引入一个无限递归的 Query Agent。

检索路径：精确 ID／路径 → 词面候选 + 向量候选 → 去重与排序 → 原文读取 → token 有界打包。

- 召回不足可以返回零项。
- 需要“全部记录”时使用顺序分页，不拿 top-k 当全集。
- 索引未追上 source high-watermark 时报告 `index_partial`，允许精确／词面降级；不把缺索引当无历史。
- embedding 服务不可用时不能删除唯一正文；需要语义检索的任务可以阻塞等待或明确降级，由 policy 决定。
- 模型只能调用当前 Agent 绑定的 `session_history.search/read`，参数里没有任意 agent_id。

## 8. 生命周期内的执行与结果提交

### 8.1 一条输入只有一个持久 Turn

`submit` 在原子操作中验证主体、输入大小、配置和队列额度，按 `(agent_id, input_id)` 去重；保存 Turn、输入 Journal 记录及 ready 标记。

同 input_id 不同规范内容是冲突。重发网络请求不是新的 Turn。ask 超时不代表取消，调用方可以用原 turn_id 继续查询。

### 8.2 一次模型请求的身份不能因新 Turn 或压缩而冲突

采用下列一种固定方案，本计划选第一种：

```text
内部 request_id = agent_id + turn_id + local_model_step + purpose
purpose = execution / summary
```

同 Turn 恢复沿用身份；新 Turn 的 local step 可从 1 起，但 turn_id 不同。旧 ledger 引用通过内部 codec 转换，不手工覆盖历史 invocation。

工具 effect 身份同样包含持久 Turn 与模型调用身份。单 Agent 成本总额由实际 ledger 汇总，Turn 限制是局部护栏，不能重置 lifetime limit。

### 8.3 结果提交不能依赖一个易失 callback

```text
模型响应完成
    ↓
规范化 AgentTurnResult
    ↓
RESULT_PENDING：冻结结果、hash、checkpoint、输入绑定
    ↓
同一 execution DB 事务：
  确认输入消费
  提交公开输出和产物引用
  写本轮 completion receipt／必要 outbox
  将 Turn 标记 COMMITTED
  Agent 有下一输入则 QUEUED，否则 IDLE
    ↓
唤醒等待结果的调用者
```

公开 Output 存在不要求整个 Agent 终止。已提交结果重读不调用 LLM。RESULT_PENDING 恢复先提交，不从 ready 重新生成。

当前默认 ReAct 把最终响应映射为 RunState.COMPLETED，且 DriverResult 约束 conversation_output 只能出现于 COMPLETED。因此必须增加新的 AgentTurn 结果载体并分流，不是直接复用旧 conversation_output。[S02][S03]

### 8.4 UNKNOWN、取消和关闭

原来模型／工具的 UNKNOWN 与 reconciliation 逻辑保留。改变 API 名称不能把未知动作重做一遍。

取消 Turn 提交 control generation，阻止旧实例的新 handoff；已发生调用仍可结算费用和结果事实。Turn 进入 CANCELLED 不代表外部现实已回滚。

close 先进入 CLOSING，拒收普通输入；默认 drain 当前工作。显式取消模式需要 cancel_turn 合同，未决外部动作不能被当作“已安全清理”。无法及时结束时返回可查询的 closing receipt，不无限阻塞调用方。

## 9. 在现有代码上怎样实现，而不是另起炉灶

### 9.1 对外改名，内部保留历史协议

- 新公共入口导出 BaseAgent、AgentRuntime、AgentConfig、AgentTurnReceipt、AgentTurnResult。
- AgentId 是新公共标识；内部可一对一映射到现有 RunId，首版使用同一 opaque ID 文本并在 codec 中转换。
- 旧 `runs` 表、run_id 字段、请求／effect 账本和旧 API 保留；不做全仓字符串 Run→Agent 替换。
- 新实例显式绑定 `api_mode=base_agent_v1`。旧 Start/Continue/Cancel 入口必须拒绝错误模式，避免把新 Agent 当 unmanaged Run。
- 这不是让每条消息创建一个隐藏临时 Run 的包装器：一个 BaseAgent 持续绑定同一个旧执行身份，多个 AgentTurn 是其内部记录。

### 9.2 新增模块（拟议路径）

```text
src/simple_harness/agents/
    __init__.py          # 公共导出
    base.py              # 轻量 BaseAgent handle
    config.py            # 不可变配置、版本和校验
    contracts.py         # 输入、Turn、结果、错误类型
    runtime.py           # 共享工厂／队列／恢复服务
    codec.py             # AgentId 与旧执行身份的一对一桥
    execution.py         # AgentTurn 执行适配，复用 ReAct 核心
    completion.py        # stage / finalize 的策略入口
    ports.py             # tokenizer、embedding、短期可见性等注入
    context/
        composer.py
        budget.py
        protocol_groups.py
        working_notes.py
    memory/
        session.py       # 当前 Agent 绑定的唯一短期记忆 facade
        journal.py
        lexical.py
        vectors.py
        retrieval.py
        index_jobs.py

src/simple_harness/execution/sqlite/base_agent/
    schema.py
    commands.py          # create_many、submit、control 的事务 helper
    turns.py             # claim、checkpoint、stage、finalize
    history.py           # Journal 与窗口选择清单
    indexes.py           # 有界派生索引读写
```

逻辑可以拆模块，但不是多个服务。交易 helper 接受外层 connection，不自行再次开启事务。

### 9.3 现有文件的具体修改表

| 现有文件／符号 | 修改方式 | 必须保留 |
|---|---|---|
| `runtime/kernel.py::Runtime` | 注入可选 Agent lifecycle service；启动／关闭时管理其 pumps；恢复时分流新模式 | 旧 ordinary／host_control 的既有约束 |
| `runtime/kernel.py::_drive` | 对 base_agent_v1 使用 AgentTurn binding 和受验证 driver；AgentTurnResult 分支先于旧 terminal 分支返回 | 策略／工具指纹、lease／fence、受控 handoff |
| `runtime/kernel.py::DriverResult` | 增加互斥的 typed agent_turn_outcome，或建立内部 union；其与 legacy conversation_output 不得共存 | 旧 conversation_output 只能 COMPLETED 的规则保持旧模式 |
| `runtime/drivers/react.py` | 将可复用执行结果构造提取为内部 helper；新 adapter 返回本轮结果，不默认关 Agent | legacy ReAct 最终结果语义不变 |
| `runtime/drivers/react_loop.py` | 注入 Turn-scoped checkpoint／identity／Context port；只在新模式使用 RESULT_PENDING | 工具 gate、UNKNOWN、必要 Context-use 校验 |
| `runtime/react_checkpoint.py` | 保留旧 namespace；新模式以 `(agent_id,turn_id)` 管理 checkpoint 和初始锚 | 不覆盖旧 anchor |
| `runtime/termination.py` | 新模式区分 Turn 本地计数、Agent 总费用；等待策略单独定义 | restart 不重置计数与费用 |
| `runtime/context.py` | 旧实现继续服务旧 Run；新 Agent 走增量 Journal + bounded Context 适配 | 不对旧完整快照直接 truncate |
| `execution/context_authority.py` | 增加受版本约束的 AgentTurn Context 请求／选择载体；无用户记忆时仅验证本 Agent 来源与权限 | 不用 metadata 假造 authority |
| `runtime/start_snapshot.py` | 新 typed AgentStartSnapshot / 解码分派，带 config、agent identity 和 no-user-memory profile | 不改变旧快照字节或放松原 host_control |
| `runtime/drivers/start_mode.py` | 新 mode 显式选择 AgentExecutionDriver，未配置就拒绝 | 不自动落入 ordinary |
| `execution/command_ingress.py` | 验证新旧 API mode，不能把新 Agent 当 unmanaged | 旧幂等和 namespace identity |
| `execution/dispatch.py` | 沿用唯一 Provider 调用路径；注入 Turn 身份、用途和预算链接 | 稳定 request、费用预留和 UNKNOWN |
| `tools/executor.py` | 仅补 AgentTurn／控制代次校验和新结果观察适配 | 已发生 effect 的真实结果不可篡改 |
| `execution/sqlite/uow.py` | 暴露 facade，委派给 base_agent 事务 helper | 现有单一 Database transaction owner |
| `execution/sqlite/schema.py` | 根据当前版本分配一份明确迁移并冻结旧 descriptor | 不重写历史 migration checksum |
| 根包 `__init__.py` | 可选导出 BaseAgent 等，重依赖按需导入 | import 时不打开数据库、不下载模型 |

这些方法的最终签名以固定 commit 为准。若开工时 main 改动，先做差异检查再修订计划，不能靠搜索替换套补丁。

### 9.4 不依赖用户记忆的装配

新增 `build_agent_runtime(config, ports)`，基于下层 build_runtime／RuntimePorts 和新 Agent service 组合。显式不注入 `agent_memory`、不启用 conversation_memory、也不创建 memory outbox 消费器。

保留 authorization、provider reconciliation、tool reconciliation、Context source visibility 等必要 ports。现有 `build_production_runtime` 若要求 Memory，保留其旧合同，不传入假的 MemoryManager 绕过检查。

Context 管理代码不得 import simple_harness_memory。新 shared embedding adapter 来自 ports，而不是借用 MemoryManager 内部 private 字段。

不实现 Task DAG 不等于删除现有 TaskScope 的资源授权。如果某个 project-effect 工具仍要求 TaskExecution authority，必须提供真实受控实现；否则新 catalogue 不暴露它。不可用无条件 ALLOW 补上这一缺口。

Service SDK 的新传输接口、Host UI 与完整编排层接入可后续做；本阶段先通过 Harness SDK 公共 Python API 使用和测试。不能因此宣称 Host 已完成生产升级。

## 10. 数据最小集合与权威边界

### 10.1 新领域记录

| 记录 | 关键字段／约束 |
|---|---|
| `base_agent_bindings_v1` | agent_id PK，对应唯一旧执行身份；owner_scope、api_mode、config_ref/hash、control_generation；不复制另一份独立生命周期状态 |
| `base_agent_creation_batches_v1` | owner_scope + batch_key UNIQUE、payload_hash、已创建 ID 序列、receipt |
| `base_agent_turns_v1` | agent_id、turn_id、input_id、input_hash、seq、phase、lease binding、result_ref/hash；UNIQUE(agent_id,input_id)、UNIQUE(agent_id,seq) |
| `base_agent_turn_checkpoints_v1` | turn_id、revision、phase、payload_ref/hash、lease epoch |
| `base_agent_turn_results_v1` | turn_id UNIQUE、result_hash、output_refs、commit receipt、usage refs |
| `base_agent_session_journal_v1` | agent_id、seq、source identity、kind、protocol_group_id、content/ref/hash、provenance、visibility、created_at；每个原始输入幂等 |
| `base_agent_context_selections_v1` | provider_request_id UNIQUE、turn_id、source_highwater、selected_refs、query_hash、index_generation、token_count、policy_hash、request_hash |
| `base_agent_session_summaries_v1` | agent_id、scope、覆盖源段、source_hash、summary_ref/hash、生成请求引用、validity；不保存权威许可 |
| `base_agent_index_jobs_v1` | 源记录 hash + embedding_generation 幂等；pending/claimed/done/error、lease 和次数 |
| `base_agent_session_vectors_v1` | agent_id、chunk_ref、source_hash、模型版本、维度、向量；不能混用不同 embedding 世代 |

FTS5 是 Journal／chunk 的派生结构；内容与索引一致性通过同事务或可重建 job 保证。readiness 可以从 Turn queue 重建，首版可先不再单独增加大型 scheduler 表。

### 10.2 一个权威，不做双写猜测

Agent 生命周期沿用执行内核权威行；Turn、结果、Journal 与唤醒记录由同一 SDK UoW 提交。索引丢失不等于原始工作记录丢失，索引也不能将 Agent 标记成功。

若 Artifact 原文存文件系统，先写临时文件、校验 hash、原子发布，事务只提交可读取的 immutable ref；清理未引用孤儿有单独策略。不得先写结果行引用不存在的文件。

网络调用、模型摘要和向量计算均在事务之外进行。

Turn 的 deadline 从首次持久准入开始计时，是否包含业务等待必须由 policy 明确；首版默认是总截止时间，恢复不延长它。到期停止新动作并核对在途动作，不以“Agent 一生总年龄”判断一次 Turn 是否超时。Agent 生命周期本身不沿用旧 ReAct 的短时 wall-clock 限制。

## 11. 迁移与发布

1. 核对实际源码 commit、旧 schema、现有 tests 和新包导出冲突。
2. 本会话未合入的旧方案曾建议 schema 10；本阶段不能同时使用两份互不兼容的 v10。以 actual source 为准统一编号。当前从已知 v9 起步时可将本阶段作为唯一 v10 候选。
3. 保留旧表、旧 run_id、旧事件与 Provider/Effect 身份；新表初始为空。
4. 做一致备份，在副本执行 migration、integrity_check、foreign_key_check 和旧 schema manifest 对照。
5. 新模式默认关闭；旧 API 在迁移副本通过回归后才启用新的 BaseAgent 工厂。
6. 不把旧 COMPLETED 实例改为 IDLE；需要 BaseAgent 的应用新建实例，历史导入需要明确来源而非自动继承。
7. 先 source tests，再安装所构建 exact wheel 执行无 Memory SDK 的 conformance。
8. 回滚时不丢弃新发生的真实动作事实；不能只恢复旧数据库再重做模型／工具。已有新数据的环境需保留可读取版本或执行已验证的前向恢复。

## 12. 开发工作包

| PR | 目标 | 主要交付 | 完成条件 |
|---|---|---|---|
| A0 | 基线与范围锁定 | 旧行为回归、schema/exports 检查、无用户记忆装配规范 | 没有把新 API 误当当前已有；无跨仓盲目改动 |
| A1 | BaseAgent 与批量工厂 | 配置、ID、幂等 create/create_many/open、单一权威映射 | 创建不调用模型；重复批次返回同一组实例 |
| A2 | 多 Turn 生命周期 | submit/wait/get/cancel/close、队列、RESULT_PENDING/finalize | 两次问答同一 Agent；重启后能提交旧结果 |
| A3 | 有界 Context 与 Journal | 增量历史、完整组选择、真实计数、工具输出外置 | Context 不超预算，历史可精确回读 |
| A4 | AgentSession 混合召回 | EmbeddingPort、FTS、向量、query、世代与降级 | 实测语义命中；严格 Agent 隔离，无重复回灌 |
| A5 | 并发与恢复 | 公平限流、队列配额、故障注入、取消／UNKNOWN、migration | 同实例串行，多实例受控；重复执行受约束 |
| A6 | exact-wheel 与文档 | 稳定导出、示例、测试证据、发布说明 | 没装 Memory SDK 也能运行完整基础 Agent 路径 |

不是完成 A1 就宣布已交付“完整基础 Agent”。本阶段至少要完成 A0–A6 的对应门槛，再进入上层编排。

## 13. 验收清单（本轮未执行，实施时必须产生证据）

### API 与创建

- BA01：create_many 创建独立 IDs，所有实例互不共享可变 Context。
- BA02：创建 100 个 IDLE Agent 不发出 100 次 Provider 请求，不复制模型服务。
- BA03：同 batch_key 同内容重试返回原 IDs；不同内容明确冲突。
- BA04：一项配置不合法，整批不留下部分成功实例。
- BA05：open 只打开已存在 Agent；错误 owner 或不存在身份不泄露内容。
- BA06：旧 Run API 对新模式拒绝，旧普通用户调用不受新模式影响。

### 生命周期

- BA07：第一次回答提交以后仍可处理第二次输入。
- BA08：同 Agent 两条并发输入顺序处理，不并发写 Context。
- BA09：ask 超时／调用方取消等待不自动取消底层 Turn。
- BA10：普通 Turn 失败不自动清空 session 或 lifetime 费用。
- BA11：授权和 UNKNOWN 恢复保持同 Turn 与相同动作身份。
- BA12：close 拒收新输入；runtime.shutdown 不关闭全部逻辑 Agent。

### Context 与历史

- BA13：真实 tokenizer 下最终完整请求始终在输入预算内。
- BA14：工具 schema、角色说明、召回内容都计入预算。
- BA15：完整工具协议组不被任意拆开；当前必要消息不可静默丢弃。
- BA16：必需内容超预算时调用前报错，不碰巧交给 Provider 拒绝。
- BA17：长 Turn 内能够轮换旧闭合组，不要求先结束整个 Turn。
- BA18：原文先保存，Context 裁减后可以精确回读。
- BA19：Context 每轮不再复制全部历史；追加体量随新记录近似线性增长（分别报告索引／快照成本）。
- BA20：召回片段不被再次写成新的原始历史。
- BA21：摘要有来源；模型工作笔记不覆盖真实执行状态或授权。
- BA22：更换模型／tokenizer／模板不沿用不适用的计数结果。

### 检索与隔离

- BA23：Agent A 的相似秘密不能出现在 Agent B 的搜索和模型输入里。
- BA24：中文语义改写和英文函数／路径查询均有实测用例。
- BA25：FTS 不支持、embedding 不可用、索引滞后均有明确可见降级。
- BA26：索引模型维度或版本变更不混算旧向量。
- BA27：相似但不相关内容允许零召回，不强制凑 top-k。
- BA28：相同 query 重试的冻结请求保留原选择，不随索引更新悄悄变化。
- BA29：需要所有历史时分页读取，不用相似度 top-k 代替全集。

### 可靠性与资源

- BA30：RESULT_PENDING 后 kill 进程，恢复不会重复生成结果。
- BA31：finalize 任意事务点异常不会形成输入已确认但结果丢失。
- BA32：输入到达与 IDLE 转换竞争不丢失唤醒。
- BA33：旧 lease 实例迟到，不能发生新 handoff 或覆盖新提交。
- BA34：不同 Turn 计数重启不撞请求 ID；同 Turn 恢复不重复预留费用。
- BA35：多 Agent 不超过模型／工具／索引并发与队列限制。
- BA36：低成本 session_history 查询与摘要也有可观察成本或调用限制。
- BA37：数据库副本迁移后旧 API、旧快照与旧 ledger 回归通过。
- BA38：基础包安装不要求 simple-harness-memory-sdk；测试 spy 证明未调用任何用户记忆入口。
- BA39：外部工具响应丢失时不盲重试，取消不宣称撤销现实动作。
- BA40：源码测试与 exact-wheel 测试分别留证，不能把 mock 测试结果说成生产部署通过。

## 14. 第一条端到端演示

```text
创建 Agent A 与 Agent B
    ↓
分别处理不同材料
    ↓
Agent A 对话／工具记录超过工作 Context 预算
    ↓
旧闭合组退出窗口，仍可回读并进入本 Agent 索引
    ↓
A 接到“刚才那个错误为什么发生”的新输入
    ↓
在本 Agent session 找回相关原文；B 的材料不可见
    ↓
故障点重启运行时
    ↓
恢复同一处理或提交已经保存的结果
    ↓
A 再次接收新输入，仍然是同一个基础 Agent
```

演示使用实际模型 tokenizer 和真实 embedding；另外保留确定性 mock 测试以注入故障。两类报告分开，不把其中一个的通过当作另一个已经通过。

## 15. 未来与编排层接缝（仅留接口，不实施）

未来上层 Scheduler 可调用 `create/submit/get_result`；Worker、Main、Verifier 只是不同 AgentConfig。上层 Result Envelope 可以包住 AgentTurnResult，但不让基础类依赖 Mission 或 Task 的状态机。

上层负责：选择谁做什么、判断业务完成、管理共享知识。基础类负责：当前实例如何可靠执行、记住自己的过程并提交输出。

默认不向模型暴露自动 create_many 权限。批量创建由应用／未来编排器调用，避免基础 Agent 自行无限繁殖。

## 16. 来源和证据

### 用户文档

[D01] 当前上传：`agent-orchestration-layer-complete-design.md`，重点 §9–13、§16–21、§27–28。保留原文件，不覆盖其完整编排设计。本计划只是按用户最新范围抽取前置基础。

### 固定源码（本轮直接读取）

[S01] `runtime/context.py`：
https://github.com/DennyWanye/simple-harness-sdk/blob/fd12e7dd7122786865ba61c19c48855a8eadcd8c/src/simple_harness/runtime/context.py

[S02] `runtime/drivers/react.py`：
https://github.com/DennyWanye/simple-harness-sdk/blob/fd12e7dd7122786865ba61c19c48855a8eadcd8c/src/simple_harness/runtime/drivers/react.py

[S03] `runtime/kernel.py`：
https://github.com/DennyWanye/simple-harness-sdk/blob/fd12e7dd7122786865ba61c19c48855a8eadcd8c/src/simple_harness/runtime/kernel.py

[S04] `runtime/consumer_adapter.py`：
https://github.com/DennyWanye/simple-harness-sdk/blob/fd12e7dd7122786865ba61c19c48855a8eadcd8c/src/simple_harness/runtime/consumer_adapter.py

[S05] `runtime/drivers/react_loop.py`：
https://github.com/DennyWanye/simple-harness-sdk/blob/fd12e7dd7122786865ba61c19c48855a8eadcd8c/src/simple_harness/runtime/drivers/react_loop.py

### 外部工程参考（非用户原文结论）

[W01] Anthropic, Effective context engineering for AI agents：有限 Context、选择与压缩的工程思路。
https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

[W02] SQLite FTS5 官方文档：FTS、tokenizers、trigram 与扩展行为。
https://www.sqlite.org/fts5.html

[W03] SQLite WAL 官方文档：多读者／单 writer 及部署限制。
https://www.sqlite.org/wal.html

以上参考并不证明示例 32K 是最佳窗口，也不证明本计划在用户部署上已经通过。
