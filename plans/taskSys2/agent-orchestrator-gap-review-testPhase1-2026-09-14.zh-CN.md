# 最新代码与完整设想的三项核心差距，以及现在该做的测试

**核查日期：2026-09-14（Asia/Tokyo）｜基线：SDK b0fa2ec028668a268998e5efa306d66b06bb297d**

## 结论

**现在应进入“评测驱动的完善”，不宜继续按完整设想的模块清单无差别扩建。**

框架已不再处于缺少 Mission/DAG/Verifier 的阶段。Phase3 仓库记录为 46 SOURCE PASS / 0 OPEN / 2 用户暂缓的打包验收；这证明的是指定验收范围被维护者记录为收敛，不是整份 Agent 操作系统设想已全部实现，也不是本轮独立执行了这些测试。[S01][S02]

排除 CI、打包、发布，本报告建议关注三个部分：

| 部分 | 差距的实质 | 是否现在补 |
|---|---|---|
| 1. 可信知识与 Context 利用 | 代码 Claim 的晋级仍有宽泛判据；跨分支检索和摘要是保守启发式 | **先补最小防误判检查；检索优化通过对照测试决定** |
| 2. 搜索、算力分配与历史学习 | 能动态执行，但策略仍是规则，尚未有足够证据证明优于强单 Agent | **现在开始对照测试；暂不引入重型学习/MCTS** |
| 3. 领域、工具与验收的可扩展性 | 主要打通代码/文本与工作区操作，尚非完整跨应用个人助手 | **现在用 AppWorld 适配作为一个完整增量，不同时接所有业务** |

这三项分别对应原完整设计 §10–14、§7–9/18/23/28、§14.2/21/24。现有 BaseAgent、Task/Attempt、Proposal/Commit、预算、持久执行继续复用；不接入用户长期记忆。

---

## 1. 范围与事实等级

### 1.1 最新代码，而非旧计划状态

- SDK main：`b0fa2ec028668a268998e5efa306d66b06bb297d`。
- Service main：`47f372adc641d8d3516599dd21cb94cf5955d6a7`。其配置仍写 0.3.12 与较旧的固定 SDK wheel；本报告不据此推断 App 实际混装，也不将发布问题列为核心差距。[S17]
- Host `DennyWanye/simple_harness`：本轮连接读取返回 404，不能直接核查 Host 源码或本地变更。SDK 维护记录写 Host 生产源身份 `18ff5d24`，只能作为间接证据。[S01]
- 目标依据：用户上传《Agent 编排层完整设计方案》（31 章）和已给出的 Phase3/测试方案。完整设计中的所有领域和未来学习能力不是 Phase3 46 条验收的同一范围。

已做：读取最新 Git ref、定向源码、关键调用链与测试结论记录；核查官方外部基准协议。

未做：完整仓库静态扫描、实际 SDK pytest、Host 原生 UI、付费模型、完整 benchmark、对原始 ignored 测试日志的独立复核。文中的测试用例与新接口均是建议，并未实施。

### 1.2 不应继续当作“缺失”的能力

候选 FIRST/COMPARE、失败片段、动态 TaskGraphChange、文档来源/Assessment、真实测试后让 Critic 读取结果、长 Context 冻结、物理资源准入、冷恢复等已具有实现或范围明确的源码验收记录。不要重复旧报告中“P3.3 只做了 A”的诊断。[S01][S02][S03][S13][S14][S15]

但须区分：有模块 → 正常路径运行 → 故障规则通过 → 多任务集有效 → 对外可复现。当前已跨过前几层的部分门槛，后两层不能由一份内部清单替代。

### 1.3 为什么不报一个 90% 完成率

原文对研究算法、领域覆盖、规模和模型效果没有给出统一加权分母。用文件数量、实现类数量或 46/48 计算“整套设想的完成百分比”会失真。

更精确的状态判断：**可测试的编排原型和有限领域闭环已经成立；跨领域泛化、知识使用质量和同预算编排收益仍需补充实现与外部证据。**

---

## 2. 差距一：可信知识能否被正确验证、检索并复用

### 2.1 直接代码事实

`memory/claims.py::grade_claim()` 对文档域调用 `_grade_document()`；其他域进入 `_legacy_grade_claim()`。代码分支只拿 claim id、evidence 和验证记录，不检查自然语言 claim 内容本身。[S07]

`covering_target()` 依赖路径相等或目录前缀。在 `_legacy_grade_claim()` 中，一个 `pytest:` 引用匹配已通过的测试目标，即可返回 VERIFIED。`parse_evidence()` 中 `tool-run:`/`knowledge:` 可直接被标为 trusted；函数本身不做记录解引用。[S07]

这不是未使用的辅助函数：`CommitService._grade_and_project()` 的非文档分支实际调用它，随后将 `updated.content` 写入 VERIFIED KnowledgeRecord；后续仍有冲突等限制，但没有在此建立主张内容与具体测试断言的对应证明。[S08]

独立 Critic、规则层及真实测试仍然存在，当前还已将 code_test 实际执行安排在 Critic 前，让其看到测试结果。因此，**本报告不是声称伪造主张必然穿透全部系统**；它指出自动知识晋级边界不足以独立确保声明范围。完整端到端误判率尚未测试。[S14]

另外，Mission Blackboard 的 `context/retrieval.py` 用英文词/中文单字集合的重叠系数衡量 relevance，再与可信度、DAG 接近度、新旧和复用次数加权；默认最多 12 条。它不是语义 embedding 检索。[S09]

`context/compression.py` 的 `compress()` 是确定性状态投影：Task goal 截到 120 字符，accepted_summary 截到 200 字符。其来源引用、争议状态、hash 版本有价值，但这不是保留全部关键条件的语义压缩。[S10][S11]

**不要混淆**：基础 Agent 自己的短期历史能力与编排 Blackboard 的检索是不同层；上述发现不表示项目完全没有向量检索。大窗口能够容纳更多内容，也不能证明挑选的材料最相关。

### 2.2 与原设想的差距

原文要求 VERIFIED 的作用是让其他 Agent 可以依赖；要求知识保留条件与 provenance，Context 检索考虑多种信息，并让综合结果再次验收。若晋级范围宽、摘要漏掉限定条件，错误就可能由一个局部候选传播为团队共同依据。

举例：一份测试只验证加法，却让主张“对所有输入都线程安全”获得相同自动等级，这是需要拒绝的晋级。若某段第 220 字才写“仅在单线程条件下成立”，只看前 200 字的摘要不能继承无条件结论。后一个例子是风险装置，不是本轮实际观察到的线上输出。

### 2.3 现在应该补的最小闭环

**先加负控，再发布新证据语义；不要重写整个 Verifier。**

1. 新增只在测试/新 Mission 显式选择的知识分级 policy。
2. 将“某产物 hash 下的指定检查通过”建模为可机器核验的观察记录，保存 checker/version、criterion、输入 hash、execution receipt、范围。
3. 任意自由主张不能仅靠测试路径晋级。Critic 可以评价，但不能代替缺失的外部依据；无充分外部支撑的语义结论保持 SUPPORTED/待核查。
4. `tool-run:` 与 `knowledge:` 必须解引用合法存在、同域授权、版本适用的记录；字符串前缀不是证据。
5. 旧 Mission 和旧回执保留原始解释；新消费路径对 legacy 知识显示原依据与限制，必要时重审，不改写历史让它假装通过新规则。
6. 当 schema/策略变化时冻结新版本，不放宽既有权限、UNKNOWN 和外部动作规则。

建议落点：`memory/claims.py`、`verification/assessments.py` 与实际 EvidenceResolver、`orchestrator/commit_service.py::_grade_and_project`、`governance/domains.py`、知识消费视图。新类型名称须在实施时核对既有合同，不能因为本文写了概念就声称已有 API。

### 2.4 Context 优化采取测试驱动

先建立三类回归：同义改写能否找到相关知识、编号/路径精确命中、摘要末尾条件与相反证据是否可回读。保留原有 Mission/角色/来源权限与过期过滤。

若证实词面召回漏检，再增加 `RetrievalPort` 组合候选：精确 ID + 全文检索 + 注入式 embedding；融合后保留 DAG 距离、可信范围、版本、新旧和 token 预算。向量相似度不能改知识可信等级。

若摘要截断漏掉决策所需条件，增加来源绑定的结构化工作摘要：目标、条件、已知事实、未决假设、失败路线、下一阻塞。授权与实际动作状态仍从权威记录读取，模型摘要不能覆盖。

先让关键状态常驻，不用一口气扩建完整用户记忆、图数据库或百万向量平台。

### 2.5 最小验收（拟新增）

| ID | 输入/故障 | 通过要求 |
|---|---|---|
| K01 | 与 Claim 无关的测试通过 | Claim 不能因此 VERIFIED |
| K02 | 不存在的 tool-run/knowledge 引用 | 不算有效证据，不泄漏别的 tenant/Mission |
| K03 | 同一测试在另一个产物版本通过 | 不批准当前版本 |
| K04 | 200 字后出现适用条件 | 不将截断摘要作为无条件事实 |
| K05 | 同义改写、跨中英文查询 | 测量相关源召回，不强制非相关项填满 |
| K06 | 来源撤回/知识被取代 | 新请求不继续无标签使用，冻结请求恢复遵守当前披露规则 |

K01–K03 是正式知识可靠性的优先检查；K04–K06 不得用一次纯函数结果冒充端到端系统结论。

---

## 3. 差距二：有受控搜索，却还未证明“算力花得值”

### 3.1 当前不是没有搜索能力

`candidate_selection.py` 已支持 FIRST_VERIFIED 与 COMPARE_THEN_SYNTHESIZE，且有严格的版本、预算、deadline、候选身份和回执。当前最多 3 候选、1 次综合，tie-break 与 deadline 策略固定。这个限制是合理的早期控制，不因数量小就算 bug。[S03]

Allocator 的 `progress_signal` 主要来自失败比例，`uncertainty=0.5**tries`；ModelRouter 依据角色/任务类型、失败次数、升级与 fallback 规则选模型。代码没有将这些近似指标冒称训练好的价值模型。[S04][S05]

`governance/learning.py` 已有 rules-v1：从可信历史比较统计量，最多调整两个权重、每次 0.05；另一条规则按默认 profile 首次失败记录建议升级路由。它明确是启发式规则，不是学习型预测模型；不足样本时不晋级。[S06]

因此正确描述是“有安全的策略改进骨架和有限启发式”，不是“根本没有学习功能”，也不是“已实现原设想的学习型调度效果”。

### 3.2 最新实测记录能说明什么

仓库固定 v15 对照记录（本轮没有复跑原实验）：

| 策略 | 结果 | 模型调用 | 总 token | 时间秒 |
|---|---|---:|---:|---:|
| FIRST | strict PASS / COMPLETED | 76 | 645304 | 423.613 |
| COMPARE | strict PASS / COMPLETED | 112 | 1273253 | 605.314 |

COMPARE 的 token 为 FIRST 的约 1.97 倍，时间约 1.43 倍。记录同时证明真实候选读取、综合 Attempt、Manager 改依赖和失败片段复用发生；但这只是一道固定任务的一组对照，不是强单 Agent 对照。[S02]

它证明机制可以运转，不证明 COMPARE 普遍优于或差于 FIRST。价格仍为 UNPRICED，token 不能直接换算成账单差。历史失败必须保留，不能重复运行直到得到好看的配对。

### 3.3 当前决策

**先测试，后扩算法。** 保留 FIRST 作为可用默认，COMPARE 保持显式选择。不要因完整设想写了学习、Group Manager、MCTS 就立即扩建。

先利用已有 `OrchestratorConfig.ablations`：`graph_changes`、`blackboard`、`critic`。安全、权限、预算、幂等及确定性门槛不可消融。静态多 Agent 不是强单 Agent，必须另有基线。[S15]

拟议对照四臂：

- S：强单 BaseAgent，同模型、工具、资料、窗口和预算，允许多轮检索、自查与修复。
- R：相同总预算内的单 Agent 多次尝试及自己选择，不能让官方隐藏分数帮助选结果。
- D：静态多 Agent，初始 DAG 固定，其余合理执行能力保留。
- F：完整编排，动态图、知识复用、独立审阅等全部计费。

先测任务成功率、错误宣布完成率、每成功任务成本、延迟、无关重做、复用贡献、停止原因；再决定是否调整候选数、分解时机、预算和路由。

“去掉某层后反而更好”也是真实发现，不能只发布有利的消融。使用另一模型的结果应单列，避免把模型升级当成编排收益。

### 3.4 什么时候才值得训练策略

先有跨任务族、未污染、完整记录失败和费用的样本，并有独立保留集，再训练成功概率、预期成本或路由建议。多次重跑一个任务不等于多个独立任务。

不要只用“位于最终成功路径”判断贡献：找到反例、排除错误路线可能没有直接进入最终产物，但仍有价值；反过来，保留下来的成果也可能存在选择偏差。现有 rules-v1 是启发式，不提供因果归因。是否采用新策略，须以离线对照/人工批准和可回退配置决定，不允许在线自改核心约束。[S06]

---

## 4. 差距三：可用领域与执行/验证接口尚窄

### 4.1 已有能力与边界

`runtime/tool_gateway.py` 当前默认 Worker gateway 提供 workspace_read_file、workspace_write_file、workspace_list、run_tests。受控发布连接器属于另外的动作路径；这不是说整个 SDK 总共只能有四个工具。[S12]

`governance/domains.py` 注册表主要是 code-v1 与 doc-research-v1；文档后继画像已到版本 9，但输入仍限定 md/txt/csv/json 等文本，不是任意 PDF/网页/企业应用能力。[S13]

`VerifierRouter.verify()` 中还存在 `document = actual_domain is not None and actual_domain.id != CODE_PROFILE.id` 的二分分派；仅往 DOMAINS 加一个 AppWorld 或 SQL 名称，不足以获得正确的新领域语义。[S14]

原文 §14.2/21 的 SQL、网页、科学实验和企业流程，是更广的目标。当前接口层可扩展并不等于这些实际适配都已完成。

### 4.2 现在补一个“评测环境适配闭环”

首选 AppWorld：它模拟跨应用的日常事务，有公开论文、环境、状态评分与榜单，适合让开发同时产生外部可比证据。[W01–W03]

拟新增且尚不存在：

- `evals/adapters/appworld.py`：每个评测 episode 初始化一个官方环境，把公开用户任务映射到 Mission；结束后保存官方环境状态。
- 受控工具适配：复用现有 SDK effect/权限/预算入口，转发允许的官方 API/MCP 调用，不把原始数据库或隐藏管理接口给 Agent。
- 显式领域 handler：把非 code 即 document 的二分改为注册表/策略能力匹配；原 code/doc 配置和历史解释保持不变。
- 结果收集与外部评分：内部 Verifier 仅用正常可见的观察；官方 ground_truth、隐藏测试、最终评测器不对 Planner/Worker/Verifier 暴露。

AppWorld 的官方 API/MCP 支持不同框架接入，不需要更换你的 Orchestrator。采用 MCP 时要显式调用 `world.save()` 保存环境状态；不同独立任务/实验使用不同环境与服务端口。一个 episode 内的所有 Worker 操作同一真实任务状态，不各自改一份 DB 后挑最好的副本。[W02]

不要赋予搜索器回滚 AppWorld 世界的额外能力，除非按官方规则声明为不同实验；这种能力不是现实幂等。并行计划可以存在，针对同一状态的写入由网关明确序列化或按受支持冲突协议处理。

### 4.3 授权与外部评分的分界

虚拟支付/邮件仅在官方隔离模拟环境中进行，不连接真实个人账号。评测环境授权策略显式固定，所有实验臂一致；不因看到隐藏测试期望就补权限或人工代做。

若保留额外审批而 benchmark 没有这种交互，必须如实记录阻塞/失败或声明自定义设置，不能跳过题目后仍报官方完整成绩。先实现可解释的 eval-domain，而不是关闭生产安全边界让测试勉强跑通。

### 4.4 完成标准

官方 dev 的至少几个场景能由整个 Mission/Task/Agent 链完成；未知工具/越权管理端点被拒绝；环境结束状态由官方 evaluator 评估。适配器不改答案、不写参考 API 序列、不喂标准测试结果。安装官方软件成功，只代表环境准备好，不代表你的框架已通过。

---

## 5. 现在就开始的具体测试顺序

### 5.1 与既有 E0–E5 测试计划的关系

继续使用已交付的《Phase3 完成后的测试与效果验证计划》，不创建另一套竞争计划。

1. E0 改为可直接冻结当前源码、依赖与配置，不要求先制作安装包。冻结只是为了知道测了什么，不是 CI/发布工程。
2. E1/E2 复用已有 46 项 SOURCE 范围证据，核对候选是否仍在相同 scope；补本报告 K01–K03、权限和外部评分器校准等门槛。
3. E3/E4 开发集适配与小样本对照现在开展；安全限定于隔离评测环境。
4. E5 负载/恢复先保持原有限规模，不因 bench 得分好就扩大生产权限和并发。

### 5.2 国际研究基准的选择

| 基准 | 认可依据 | 本次用途 | 成绩边界 |
|---|---|---|---|
| AppWorld | ACL 2024 论文、公开实现与正式榜单 | 个人助手多应用任务能力与 S/R/D/F 对照 | 按官方数据划分、TGC/SGC 与提交规则 |
| AgentDojo | NeurIPS 2024 Datasets and Benchmarks Track、公开自定义 pipeline | 外部数据和工具提示注入、安全与效用 | 同时报正常任务效用、攻击下效用、攻击成功率 |
| Gaia2 / ARE | Meta 公开研究基准及运行/判分协议 | 动态事件、时间、歧义适应的研究对照 | 默认官方路径与 custom-harness 严格区分，投稿资格先核对 |

这是国际公开研究评测，不是统一能力认证。不存在通用“过 80 分就证明所有 Agent 编排合格”的规定。[W01][W03][W04][W06]

### 5.3 AppWorld 环境准备（官方命令，非框架接线）

在隔离的 Python 3.11+ 评测环境中，核对当前官方支持后固定版本：

```bash
python -m pip install appworld
appworld install
appworld download data
appworld verify tests
appworld verify tasks
```

版本锁定在开发接线后立即保存；正式比较使用同一版本。以上 verify 验证的是官方环境及其验证程序，不是 Simple Harness。[W02]

接下来由新 runner 调用你的完整编排。**不能运行默认 AppWorld agent，只更换模型参数，再宣称测到了你的 Orchestrator。**

官方命令 `appworld evaluate <experiment_name> <dataset_name>` 用来对已经产生的官方实验输出评分；内部工作结果必须正确写入官方环境，否则该命令无从评价。

开发使用 train/dev。正式 test_normal/test_challenge 只能用于测试聚合成绩，不做逐题查看和提示调参；正式榜单按两个 split 打包为加密 bundle，不能公开明文 test 输出。[W02][W03]

### 5.4 第一次对照规模（本项目建议，不是官方规定）

先 5 个 dev 场景校准适配和评分；然后预先选定 12 个不同任务的 dev 试验集，S/R/D/F 四臂各重复 2 次：96 次 episode。

这只用于接线、发现故障和估算方差，不宣称统计显著，也不计算不完整任务组的正式 SGC。按官方完整协议运行时，应包含规定的组和 split。

每个 episode 重新初始化任务环境、Mission 库、短期记忆和检索索引。不能让开发答案或其他测试结果进入后续任务。参数、预算、是否使用缓存及试验顺序预先冻结；同模型同物理容量，所有角色与失败消耗都计入。

当前仓库 v15 FIRST/COMPARE 不是四臂实验中的 S/R；不能把 FIRST 当成单 Agent，不能用内置 PASS 作为外部标签。[S02]

### 5.5 AgentDojo 怎么测

实现包官方 `BasePipelineElement` 接口，把完整 Orchestrator 作为一个自定义 pipeline。将其公开工具 runtime 通过受控适配交给系统；不要只是调用库中默认模型 pipeline。[W05]

对相同任务比较干净输入和官方攻击；研究扩展另测“外部文本 → Worker Claim → 摘要 → Blackboard → 下游 Agent”。扩展任务/攻击单独报告，不与官方配置混成一个分数。

至少输出正常成功率、攻击下成功率、攻击成功率、拒绝率与成本。全部拒绝不是有用的防护；没有观察到攻击成功也不是普遍安全证明。[W04][W07]

### 5.6 Gaia2 怎么测

它适合测动态性，但官方 guide 明确 `default` 是其提供并建议用于 gaia2 evaluation 的 Agent。使用你的编排要实现 custom agent 接口，与官方 default 结果分开报告；是否进同一榜单先按最新规则确认，不保证自动符合赛道。[W06]

固定情景、时钟/生成时间设置、资源与独立 judge；不能暂停环境等 Manager。不要开启 oracle 获得标准动作。judge 若有独立模型费用，单列评分成本，不把评分调用算成被测 Agent 的能力。

### 5.7 主要判据

主结果用外部任务评分。另报错误宣布完成率、整体 token、真实费用（未知不得写零）、工具次数、总时长、人工介入、恢复与副作用违规。

`每成功任务成本 = 全部成功和失败 episode 的系统总成本 / 成功 episode 数`。成功数为零时记未定义/无成功，而不是零成本。

重复同题要按 task/任务族聚合或配对 bootstrap；不能把同一题的重跑当成独立新题。预先确定实用提升门槛；样本不足可报告无定论，不反复看分数后重跑直到显著。

一次 Mission 内 N 个候选属于同一次系统运行，其全部消耗计费；pass@k/全 k 次成功的可靠性指标是跨独立运行的另一个统计，不混用。

---

## 6. 优先级裁决

| 工作 | 现在做 | 先不做 |
|---|---|---|
| 可信知识 | 代码晋级与悬空引用负控、必要时严格新策略；旧依据明确标识 | 任意自然语言自动升 VERIFIED、批量改写历史 |
| Context | 精确引用/条件保留/同义检索试验；实际请求 token 与角色材料观察 | 一上来换全套记忆架构、假设 512K 就最优 |
| 搜索策略 | 四臂开发集对照，复用已有 ablations、Trace 与预算 | 无数据先训练 Router、扩大几十候选、上 MCTS |
| 领域覆盖 | AppWorld 一个端到端评测适配与明确第三领域分派 | 同时承诺浏览器/SQL/支付/网页/PDF 全部完成 |
| 发布设施 | 不纳入本报告核心增量；源码身份仍须可追踪 | 为了 benchmark 强制先做 CI/安装器/发版 |

**建议执行顺序：最小知识负控与必要收口 → AppWorld 环境和完整适配 → 96 次开发试验 → AgentDojo → 按错误归因选择检索或搜索改动 → 冻结后正式公开评测。**

这不是等待完全理想化系统才测试，而是用测试决定还有哪些差距值得花成本弥补。

---

## 附录：来源、证据与可复核范围

D1：用户附件《Agent 编排层完整设计方案》，原文 §7–11、§14、§18、§21、§23、§28。
D2：用户已提供《Phase3 完成后的测试与效果验证计划》，E0–E5。本报告调整启动方式，不替换其任务定义。

所有源码结论只适用于冻结 commit 与已读取函数，不宣称 Host、其他分支、ignored 原生证据已独立验证。仓库内的测试数字明确标为作者记录。

下列来源索引同时保存在 `sources.json`。

### 源码与仓库记录

- [S01] `plans/2026-09-12-phase3/HANDOFF.md`；读取范围：1-105（返回文本后段截断；当前顶部完整）。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/plans/2026-09-12-phase3/HANDOFF.md`
- [S02] `plans/2026-09-12-phase3/p34/v15-real-pair-review.md`；读取范围：完整返回。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/plans/2026-09-12-phase3/p34/v15-real-pair-review.md`
- [S03] `src/agent_orchestrator/planning/candidate_selection.py`；读取范围：完整返回。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/planning/candidate_selection.py`
- [S04] `src/agent_orchestrator/scheduling/allocator.py`；读取范围：1-245。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/scheduling/allocator.py`
- [S05] `src/agent_orchestrator/runtime/model_router.py`；读取范围：1-250。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/runtime/model_router.py`
- [S06] `src/agent_orchestrator/governance/learning.py`；读取范围：1-260。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/governance/learning.py`
- [S07] `src/agent_orchestrator/memory/claims.py`；读取范围：1-225。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/memory/claims.py`
- [S08] `src/agent_orchestrator/orchestrator/commit_service.py`；读取范围：1500-1885。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/orchestrator/commit_service.py`
- [S09] `src/agent_orchestrator/context/retrieval.py`；读取范围：1-230。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/context/retrieval.py`
- [S10] `src/agent_orchestrator/context/compression.py`；读取范围：完整返回。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/context/compression.py`
- [S11] `src/agent_orchestrator/memory/summaries.py`；读取范围：完整返回。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/memory/summaries.py`
- [S12] `src/agent_orchestrator/runtime/tool_gateway.py`；读取范围：1-215。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/runtime/tool_gateway.py`
- [S13] `src/agent_orchestrator/governance/domains.py`；读取范围：1-125、250-485。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/governance/domains.py`
- [S14] `src/agent_orchestrator/verification/verifier_router.py`；读取范围：1-220。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/verification/verifier_router.py`
- [S15] `src/agent_orchestrator/runtime/assembly.py`；读取范围：1-220、260-465。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/src/agent_orchestrator/runtime/assembly.py`
- [S16] `ARCHITECTURE/ORCHESTRATOR.md`；读取范围：当前顶部及返回的部分历史记录；不是全篇。
  `https://github.com/DennyWanye/simple-harness-sdk/blob/b0fa2ec028668a268998e5efa306d66b06bb297d/ARCHITECTURE/ORCHESTRATOR.md`
- [S17] `pyproject.toml`；读取范围：1-45。
  `https://github.com/DennyWanye/simple-harness-service-sdk/blob/47f372adc641d8d3516599dd21cb94cf5955d6a7/pyproject.toml`

### 官方外部基准

- [W01] AppWorld paper, ACL 2024。
  `https://arxiv.org/abs/2407.18901`
- [W02] AppWorld official environment, evaluation, restrictions, MCP。
  `https://github.com/StonyBrookNLP/appworld`
- [W03] AppWorld official leaderboard submission。
  `https://github.com/StonyBrookNLP/appworld-leaderboard`
- [W04] AgentDojo official project and NeurIPS 2024 citation。
  `https://github.com/ethz-spylab/agentdojo`
- [W05] AgentDojo custom pipeline contract。
  `https://agentdojo.spylab.ai/concepts/agent_pipeline/`
- [W06] Gaia2/ARE official benchmarking guide。
  `https://facebookresearch.github.io/meta-agents-research-environments/user_guide/benchmarking.html`
- [W07] AgentDojo paper。
  `https://arxiv.org/abs/2406.13352`

### 本次实际执行

仅进行了只读资料检索、静态源码分析、文件生成与数字比值计算；没有运行上述 K01–K06、96 次 episode、真实模型或官方 benchmark。没有修改远端仓库。
