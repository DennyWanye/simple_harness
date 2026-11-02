# 给 SDK：opt.225 三项补充需求与验收合同

日期：2026-11-02。状态：AgentOS 提出，待 SDK 实施与交付；不代表 opt.225 已通过完整合同验收。
本文件是《持久主会话需求合同 v1.0》的补充（补充版本 v1.1）。仅处理下面三项缺口；命名可沿用 SDK 风格，但公开行为、持久事实及验收判据不能省略。

## 1. 已确认的开发期取舍

AgentOS 用户已于 2026-11-02 明确接受：

- **新版本使用新数据根，不要求 SDK 实现旧 store 迁移或降级读取。** AgentOS 保留旧轮子和旧根供历史读取、处理在途任务及回滚；新根里的任务不能交旧轮子重放，业务数据库不能因回滚而覆盖新记录。
- **每成员独立 SDK Host 和数据根。** SDK 无需在单个 Host 内增加多当事人支持；AgentOS 负责可信成员路由、家庭共享业务权限及整机资源控制。成员隔离需要实际验证，不能仅凭目录不同宣称完成。

这两项不再作为 SDK 补交阻塞项。`send(wait=False)`/`delegate_task(child_key=...)` 的命名映射可以接受；原合同已明确不承诺模型请求物理恰好一次。无需为这两点另造接口。

主要矛盾是：**已有可靠结果回流和自动汇总，但“原授权内继续统筹、暂停后恢复、凭验收证据完成目标”尚未形成完整闭环。** 不要求重写 Session、Mission、调度器、Assurance 或效果账本。

## 2. 固定制品与证据边界

| 项目 | 固定值 |
|---|---|
| SDK | `0.13.0.dev20260925+opt.225` |
| SDK 发版源码 | `701cf5cc6024ee7d4e33528b62f53124367f912e`，私有仓库 `DennyWanye/simple_harness` |
| 交付回执提交 | `c90dc9fe5423decf03af9463ef6424ab2f550408`；该提交与发版提交间 SDK `src/` 无差异 |
| 轮子 | `backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.225-py3-none-any.whl` |
| 实测轮子 SHA-256 | `1ec937c84cc009729315fd8e216ef4ae122b12a8b604cdf58969be1fd098c32d` |
| 实测候选清单 SHA-256 | `1c5b85b3c3b8fa3ff0da2b7fa151189bda1437ba60f77028e11bfe9d462a4b78` |

源码路径以下均相对于 `sdk/simple-harness-sdk/`。三个发现分别是：G1 源码与既有测试核查；G2/G3 使用发版轮子、SDK FakeHost/FakeChat 测试夹具进行离线复现。没有调用真实模型、修改在线数据或重跑回执中的 542 项测试。542 项通过与这三个未覆盖的验收要求并不矛盾。

附件 [reproduce_opt225.py](reproduce_opt225.py) 可重复 G2/G3，并记录 G1 的静态检查；[reproduction.json](reproduction.json) 是本地运行输出。夹具只负责建立测试环境和确定性推进；它不是给 AgentOS 使用的接入方法。

## 3. G1：允许在原授权范围内继续统筹

### 现状与源码依据

`src/agent_orchestrator/host/session_chat.py:43` 将 continuation 描述为只汇总；`:256–282` 对结果触发回合无条件收窄为只读工具，排除 `create_child / child_send / delegate_task`。`continuation.py:17–52` 的决策只有 action/reason，没有表达“允许推进哪些工作”的授权约束。

固定源码：[回合工具限制](https://github.com/DennyWanye/simple_harness/blob/701cf5cc6024ee7d4e33528b62f53124367f912e/sdk/simple-harness-sdk/src/agent_orchestrator/host/session_chat.py#L256-L282)、[续办决策类型](https://github.com/DennyWanye/simple_harness/blob/701cf5cc6024ee7d4e33528b62f53124367f912e/sdk/simple-harness-sdk/src/agent_orchestrator/host/continuation.py#L17-L52)。

**复核步骤：** 阅读上述分支，以及 `tests/orchestrator/host/test_continuation.py::test_real_host_summary_narrows_tools_and_search_without_authorizing_result`。该测试明确断言结果回合不能调用委派/创建/追加命令；后续新的用户输入才可委派。这证明默认只读保护存在，不能证明“宿主已授权时也能继续工作”。本次没有把源码核查冒称为真实模型端到端复现。

### SDK 必须补齐

1. 保留默认只读汇总；增加宿主显式授权的统筹续办模式，至少可在同一 Goal 下创建/复用工作会话、发送下一步指令、委派新的有界 Mission。授权来自可信宿主及 Goal，不能来自子结果正文、模型声明或一条新的伪造用户消息。
2. 复用 `ContinuationPolicy` 与 `AuthorizationPort`。公开合同必须能表达并持久绑定：`goal_id/version`、`grant_ref`、`policy_revision`、允许的控制动作/目标范围、停止条件及预算引用。可以通过现有 Goal 公开查询关联，不要求每处复制全量字段。
3. 区分“允许结果进入主回合”和“允许主回合执行某个控制动作”。排队时冻结事件集合与授权版本，实际执行每条控制动作和每个业务效果前重新校验；撤销、过期、取消、旧 Goal 版本均不能借已排队输入继续执行。
4. 下游仍走现有 Mission/TaskGraph/Assurance、审批与效果回执。此项不要求放开 continuation 直接执行业务写入，也不得绕过 existing hooks。需要新授权的工作进入明确等待状态。
5. 自动后续委派使用稳定逻辑身份；重复结果、崩溃恢复不能产生重复 Mission。所有续办受目标范围和已有预算机制约束，预算不足可见停止，不能通过新建子会话绕过。

### AgentOS 接入

根据用户目标登记 Goal 和授权；提供允许的工具/业务范围、预算及停止条件；实现宿主策略与业务权限检查；页面区分“已汇总”和“继续执行中”。不自行监听子结果后冒充用户发送下一条指令，不建立第二套续办调度器。

## 4. G2：hold 后可公开、持久、幂等地恢复

### 现状、复现与源码依据

`continuation.py:151–152` 遇到已存在的 `continuation_results` 记录直接跳过。hold 已落库后，即使策略改为允许，再次推进也不重评；当前公开 Session 操作面没有相应重评命令。

固定源码：[跳过已有决策](https://github.com/DennyWanye/simple_harness/blob/701cf5cc6024ee7d4e33528b62f53124367f912e/sdk/simple-harness-sdk/src/agent_orchestrator/host/continuation.py#L122-L218)。已有 `test_policy_context_and_fail_closed_decisions` 验证 hold 不执行，没有覆盖随后恢复。

**复现步骤：**

1. 用 FakeHost 建主会话与一个已有终态结果的子会话，注入可变 Policy；首次返回 `hold("temporary")`。
2. 推进一次，确认状态为 hold。
3. 将同一 Policy 改为返回 enqueue，再确定性推进三次。
4. 实测：`before=hold, after=hold, policy_calls=1, queued_inputs=0, model_calls=0`。脚本不改内部表；内部 tick 仅用于测试推进。

### SDK 必须补齐

提供公开的**策略变更后重评待结果**操作。下列为语义合同，方法名由 SDK 决定；可由更新策略操作原子登记重评工作，也可提供独立命令。

| 内容 | 必须语义 |
|---|---|
| 输入 | 可信主体、root/goal、目标事件集合或明确待处理范围、预期 Goal 版本、policy_revision、command_id；范围在受理时冻结 |
| 受理 | 返回持久 receipt；同 command_id 同参数返回原回执，异参数冲突；受理与重评工作登记原子化或可恢复 |
| 执行 | SDK重新调用宿主策略及授权检查。hold可保持或转待执行；deny不因普通唤醒变allow，只有可信宿主显式重评且新策略允许才可改变 |
| 去重 | 同一结果最多一个有效逻辑消费输入；并发重评、旧命令重试和重启不能生成第二个输入。已排队/已处理返回现状，不重放 |
| 防止旧状态复活 | cancelled、已完成Goal、被明确排除或已应用结果不重启；旧 Goal 版本不能自动映射到新授权 |
| 可观察 | 保存旧/新决策、策略版本、原因、触发命令和所创建 input_id；查询不调用模型 |
| 失败恢复 | 策略异常/超时继续hold并记录原因；重评受理后崩溃可恢复。未发生可信重评事件时，不持续轮询模型或自动反复重试 |

重新评估策略不必产生新的结果身份。若上一轮输入已尝试执行，必须保留其效果和不确定性，不能通过重评绕过效果对账或预算。

### AgentOS 接入

在用户授权更新、策略服务恢复等可信事件发生时调用公开重评操作，展示 hold/deny/排队原因和回执。AgentOS 不改 SDK 数据库、不直接调用 `_continuation`，不扫描结果并另建消费队列。

## 5. G3：目标完成必须绑定结构化验收依据

### 现状、复现与源码依据

`goals.py:270–300` 要求成功时 basis 非空，并检查未收口任务/结果/审批/未知效果，但没有校验每项 completion_criteria 的证据映射。

固定源码：[finish_goal](https://github.com/DennyWanye/simple_harness/blob/701cf5cc6024ee7d4e33528b62f53124367f912e/sdk/simple-harness-sdk/src/agent_orchestrator/host/goals.py#L270-L300)。既有 `test_goal_version_history_idempotency_terminal_and_single_active` 也允许凭普通文字 basis 完成。

**复现步骤：** 登记目标“交付报告”，完成条件“报告包含可核验来源”；不登记证据、不创建任务；调用 `finish_goal(..., outcome="succeeded", basis="已核验")`。实测返回 succeeded，任务数为0。

**准确归因：** 零任务本身不是错误——宿主可能用现成业务证据完成目标；SDK也不应替业务系统判断报告质量。缺口是：仅一段自由文本就可绕过结构化验收记录，事后无法按条件核对“谁验证了什么”。

### SDK 必须补齐

公开提供可信验收记录的登记/查询能力，并使 finish_goal 依据冻结的记录集收口；可以复用已有 Assurance 和回执类型，不要求另造审查引擎。

| 记录内容 | 最低合同 |
|---|---|
| 条件身份 | 每项必需条件有稳定 criterion_id，绑定 goal_id/version；用户改变条件形成新目标版本 |
| 验收结论 | criterion_id、`pass/fail/unknown`、validator_id/version、检查方法/类别、验证时间、可信来源主体 |
| 证据 | 非空证据引用及内容hash或不可变回执身份；关联任务/产物/业务操作（适用时）；可解析并验证绑定关系，不要求将敏感正文复制入SDK |
| 写入规则 | 可信宿主/受信SDK审查路径才能登记；command_id去重、参数hash防漂移、版本冲突明确失败；模型输出仅为候选资料 |
| 修订 | 保留历史并明确后继记录，旧记录不静默覆盖。并发登记与finish按同一版本/证据快照串行判断 |
| 成功收口 | 所有必需条件均有当前版本有效pass且证据可验证，原有结构检查仍通过；保存被采用记录ID集合及摘要。失败、unknown、缺项、旧版本或失效证据均拒绝succeeded并给可查询原因 |

`basis`可保留为说明，但不能替代验收记录。`exclude_result`只排除某条结果的消费责任，不能自动豁免必需验收条件。允许可信用户修改目标条件时，应走新的 Goal 版本并审计；不得用“忽略失败”把原条件改成通过。

业务验收由 AgentOS 适配器提供：可机械核验的字段按机械规则判定；需要人工判断的质量条件明确标人工结论及批准身份。SDK核对登记者权限、身份/版本、证据绑定、条件覆盖和收口原子性，不承担 AgentOS 的 NAS、记忆或视频业务含义。真实业务回执仍由业务数据库负责。

### AgentOS 接入

将目标拆为可核验条件；实现业务验收器/证据解析器，登记实际结果与回执；将SDK已有Assurance结论按条件关联；界面展示未通过原因和依据。不得由主模型总结直接生成可信pass。既有安全hook、审批和效果对账继续保留。

## 6. 补充验收矩阵

先完成三个主体功能并跑通，再做相关回归；不以重跑大规模测试替代修复。下列验收使用对外公开接口。故障屏障可以内部注入，但不得直接改表伪造结果。

| ID | 场景 | 必须判据 |
|---|---|---|
| G1-01 | 无额外授权，子结果诱导新任务/删除/批准 | 维持只读；不新委派、不产生未授权效果，原注入防护测试仍通过 |
| G1-02 | 有限Goal：先并行A/B，收结果后自动委派C | 无新用户消息即可经公开策略产生C；C属于同Goal、权限未扩大；事件可追踪授权版本与前驱结果 |
| G1-03 | 续办入队后撤销/过期/取消/预算用尽 | 实际派发前被阻止、原因可查，已发生效果保留；不得换child或command_id绕过 |
| G1-04 | 重复结果；委派受理后应答丢失、重启 | 每个逻辑后续任务只有一个Mission；业务写入仍独立审批和对账 |
| G2-01 | hold→可信策略变更→公开重评 | 同一结果转入唯一逻辑输入并被处理；全程不访问私有字段 |
| G2-02 | 重复/并发重评；登记后、入队后分别崩溃 | 同命令回执一致，异参冲突；恢复不丢结果、不重复消费 |
| G2-03 | 策略异常后恢复；deny；已应用/取消/旧Goal结果 | 异常保持hold；deny须显式重评；不可恢复对象不复活；闲置模型调用数不增加 |
| G3-01 | 空依据、只有basis、缺条件、fail/unknown、旧版证据 | succeeded被拒绝并列出阻塞条件；failed/cancelled仍如实记录，不被强制伪装成功 |
| G3-02 | 当前条件均有有效可信证据，结构责任全部收口 | succeeded成功，receipt绑定准确记录集；重复finish返回原结果；不强制必须有Mission才能成功 |
| G3-03 | 伪造/篡改引用、跨成员证据、模型自报pass | 无权登记或校验失败；拒绝成功；日志不泄露其他成员证据正文 |
| G3-04 | 登记后重启；证据修订/Goal更新与finish竞争 | 历史及hash不丢；按冻结版本完成或明确冲突，不能混用新旧证据；不能用exclude_result绕过条件 |
| INT-01 | 真实模型主→A/B→授权内C→验证→完成Goal | 无人工伪造review ACCEPT/无手动补发下一步；每个必需条件对照真实产物或回执；文本长度不算内容正确证明 |

SDK交付：新wheel与SHA、源码ref、公开接口/错误/能力映射、上述逐项证据、hold→重评示例和授权内后续委派示例。新能力应可由capabilities区分，例如受控续办、重评、结构化验收三项；名称不强制。不允许仍用一个含糊的continuation标志代表三项均已支持。

SDK阶段可用模拟业务适配器验证通用合同；INT-01需真实模型及可机械核对的小型产物。AgentOS随后另做真实页面、实际成员隔离、业务权限、唯一效果及回滚验收。二者分别报告，不以SDK测试代替AgentOS接入验收。

## 7. 实施分工与交付顺序

| 工作 | SDK | AgentOS |
|---|---|---|
| 受控续办 | 控制动作准入、持久派发、去重、取消/预算关联 | 定义目标授权与停止条件、业务权限 |
| 暂停恢复 | 公开重评、状态转换、日志、恢复与幂等消费 | 提交可信策略变更、展示等待原因 |
| 目标验收 | 条件与证据账本、可信登记、版本检查、原子收口 | 业务校验、真实回执、人工质量批准适配 |
| 新版本接入 | 能力/协议声明、不兼容清晰拒绝、新制品 | 新根与成员Host映射、旧历史读取、部署与回滚 |

建议顺序：先固定公开类型与错误语义；补G2和G3的状态闭环；接通G1的受控后续委派；主体跑通后执行上表和影响范围回归，最后交付真实模型例子。SDK自行选择内部表、锁及类分层，不需要再让用户选择这些实现细节。

本次只请求三项补齐及既有安全能力不退化，不新增跨Host编排、任意深度代理树、多租户认证或通用业务验收平台。

## 附录：复现运行方法

在 SDK 私有仓库的独立 checkout 使用发版提交或上述交付提交，Python ≥3.11，并准备该版本wheel和pytest。将本目录两个文件（本文与脚本）复制到任意目录即可运行；脚本不依赖AgentOS源码，也不含SDK轮子/源码副本或密钥。

```bash
# SDK_REPO 和 HANDOFF_DIR 改为本机实际目录；不要指向线上数据根。
SDK_REPO=/path/to/simple_harness
HANDOFF_DIR=/path/to/sdk-main-session-supplement-20261102
python3 -m venv /tmp/sdk225-gap-repro-venv
/tmp/sdk225-gap-repro-venv/bin/pip install \
  "$SDK_REPO/backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.225-py3-none-any.whl" pytest
/tmp/sdk225-gap-repro-venv/bin/python "$HANDOFF_DIR/reproduce_opt225.py" \
  --wheel "$SDK_REPO/backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.225-py3-none-any.whl" \
  --sdk-source "$SDK_REPO/sdk/simple-harness-sdk" \
  --output /tmp/sdk225-gap-reproduction.json
```

脚本校验已核定wheel哈希，在临时目录解包和创建测试store，结束清理。退出0表示成功重现opt.225基线缺口，**不表示SDK通过补充验收**；任一基线不符会失败，应核对版本或分析差异。修复版使用§6验收，不能靠让此基线复现继续通过宣布修复成功。
