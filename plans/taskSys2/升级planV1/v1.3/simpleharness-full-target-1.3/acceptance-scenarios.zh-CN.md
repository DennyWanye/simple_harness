# FULL-TARGET-1.3 验收场景

**90项拟实施规范，全部NOT_RUN；runner_node_id需在真实项目测试落地后填写。不是90项已通过。v1.2 对 P1/P2 的 19 条做了逐条细化（标 v1.2）；v1.3 把附件 TG 的场景 A/B/C 与崩溃切点并入现有条目（标 TG）。**

## T001 · 稳定 BaseAgent 身份，多轮输出不等于关闭（v1.2 细化）
工作包：P1；需求：R01；层：contract_and_integration；状态：NOT_RUN。

前置：
- 语义版本 full-target-v1；fixture provider 脚本化同一 agent 的 3 轮输出；隔离 orchestrator.db 与 execution.db。

步骤：
- 同一 agent_id 连续提交 3 次输入；第 2 次完成后杀掉进程并重启。
- 重启后查询该 agent 的持久结果与 journal。
- 第 3 次输入继续处理。

断言：
- 同一agent多轮并经历进程重启，身份/历史/已交付结果不重复
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- agent_id/turn_id 序列
- 重启前后 journal hash
- 事件 id 列表
- 已交付结果去重计数

## T002 · 单用户主Agent，子Agent弹性创建但物理并发有界
工作包：P4；需求：R02；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 主入口稳定，子执行可替换，增加数量不能突破真实槽位
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T003 · Task、Attempt、AgentTurn、外部操作身份分离（v1.2 细化）
工作包：P1；需求：R03；层：contract_and_integration；状态：NOT_RUN。

前置：
- 一个 Task 含一次 fixture 外部操作；hierarchical Mission。

步骤：
- Attempt 1 执行外部操作，取得 operation_id。
- 以 replace 改图产生后继 Task 并执行 Attempt 2。
- 让 Attempt 2 的 AgentTurn 正常结束但 Task 未验收。

断言：
- 改图重试不重置外部动作身份，完成AgentTurn不完成Task
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- operation ledger 行（operation_id 不变）
- task/attempt/turn 三张状态快照
- 事件 id 列表

## T004 · 复合/原子Task语义，不把抽象目标当Worker工作直接派发（v1.2 细化）
工作包：P2；需求：R04；层：contract_and_integration；状态：NOT_RUN。

前置：
- code 域种子方法库已注册；一个 compound Goal；语义版本 full-target-v1。

步骤：
- 提交 Mission，不做细化，直接调用 allocate()。
- 执行一次细化得到 primitive 叶子后再调用 allocate()。

断言：
- 复合节点在细化前不能进入普通执行队列
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 两次 frontier 快照（第一次不含 compound）
- EligiblePrimitiveTask 构造日志
- task_semantics 旁表行

## T005 · 版本化参数化MethodContract与适用前提（v1.2 细化）
工作包：P2；需求：R05；层：contract_and_integration；状态：NOT_RUN。

前置：
- 同一 MethodContract；两组合法参数、一组错类型参数、一组使前提为 FALSE 的观察。

步骤：
- 对四组输入分别调用 assess_method 与 ground_method。

断言：
- 同一方法对不同参数实例化，错误参数类型与不满足前提被拒
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 四份 ApplicabilityReport（区分 TYPE_ERROR 与 PRECONDITION_FALSE）
- 两份 MethodInstanceDraft hash
- 无数据库写入的断言

## T006 · 世界状态区分已知真/假/未知/有争议（v1.2 细化）
工作包：P1；需求：R06；层：contract_and_integration；状态：NOT_RUN。

前置：
- 一个谓词观察器脚本化返回 UNKNOWN；一个高风险 primitive 的前提依赖该谓词。

步骤：
- 细化目标。
- 观察器改返回 TRUE 后再次细化。

断言：
- 未知前提只能触发取证或标记假设，不被当真执行风险动作
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 第一次细化产生的取证任务记录
- 高风险任务在第一次未派发的 frontier 快照
- 第二次派发事件

## T007 · 方法生成、验证、注册、检索和淘汰闭环（v1.2 细化）
工作包：P2；需求：R07；层：contract_and_integration；状态：NOT_RUN。

前置：
- 方法库为空；fixture provider 脚本化一份合法 method_proposal 与一份引用不存在 Operator 的提案。

步骤：
- 触发 MethodSynthesizer 并提交两份提案。
- 对合法提案请求晋级到 ADMITTED。

断言：
- 空方法库能提出新方法，经审阅/验证后执行；不能在线自我升级权限
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- registry 状态行（合法：STRUCTURALLY_VALID→TRIAL_ADMITTED；非法：REJECTED 含缺能力说明）
- admission receipt
- 晋级请求返回 PROMOTION_NOT_AVAILABLE 的回执（P2 范围）

## T008 · 递归HTN与任意部分序；展开后的执行图无环（v1.2 细化）
工作包：P2；需求：R08；层：contract_and_integration；状态：NOT_RUN。

前置：
- 递归方法（问题规模 n→n-1）；Obligation 燃料 5。

步骤：
- 以 n=3 细化并编译。
- 以 n=10 细化。
- 对编译结果做环检测。

断言：
- 方法可递归，有限实例展开保留部分序；超预算返回有界未完成而非伪称无解
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- n=3 的展开树与部分序约束
- n=10 的 BOUND_REACHED 回执与已展开结构
- 燃料计数（按 obligation）
- 环检测输出

## T009 · 按能力与风险决定直做/分解/取证/重规划
工作包：P3；需求：R09；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 同目标可随反馈细化不同深度；不以不可逆操作试探可行性
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T010 · AND子成果与OR方法路线分离（v1.2 细化）
工作包：P2；需求：R10；层：contract_and_integration；状态：NOT_RUN。

前置：
- Goal 有 OR 方法 MA/MB；MA 的前提被观察否定。

步骤：
- 细化并选择方法。
- 执行 MB 叶子至根 Resolution。

断言：
- 一条方法已满足根目标时，未选路线不阻塞完成
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 方法实例状态（MA 未派发）
- 根 Resolution 记录
- MA 未阻塞完成的事件序列

## T011 · 共享子目标及适用性证明（v1.2 细化）
工作包：P2；需求：R11；层：contract_and_integration；状态：NOT_RUN。

前置：
- MA/MB 共享子目标 C（相同参数、scope、输入版本）与 C'（不同 scope）。

步骤：
- 编译两条方法。
- 取消 MA。

断言：
- 共享成果只执行/付费一次；参数或权限不同不能强行合并
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 物化任务表（C 一次，C' 不合并）
- 消费者引用表
- 预算记账（C 只扣一次）
- 取消 MA 后 C 仍保留的快照

## T012 · 父目标组合判据与显式GoalResolution（v1.2 细化）
工作包：P2；需求：R12；层：contract_and_integration；状态：NOT_RUN。

前置：
- 方法叶子全部 COMPLETED；根要求 R3 无对应子成果。

步骤：
- 提交 GoalReview。
- 补充覆盖 R3 的子目标并完成后再次提交。

断言：
- 叶子局部全通过但缺根要求时，父目标不得完成
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 第一次拒绝回执（ROOT_REQUIREMENT_UNCOVERED）
- 第二次形成的 GoalResolution
- 要求覆盖映射

## T013 · 结构化观察、诊断与建议分别存储
工作包：P3；需求：R13；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 失败理由与实际工具证据绑定；诊断不直接写成事实
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T014 · 局部计划修复，尽量保留有效执行与产物
工作包：P3；需求：R14；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- B方法失效仅修复其语义影响闭包，A/C有效成果不重做
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T015 · DATA/ORDER/ASSUMPTION边分离（v1.2 细化）
工作包：P1；需求：R15；层：contract_and_integration；状态：NOT_RUN。

前置：
- A→C 为 DATA 边，B→C 为 ORDER 边；C 已有 Acceptance。

步骤：
- A 的输出版本变化。
- B 的输出版本变化。

断言：
- 数据变化只使消费者结果失效；纯先后边不传播内容失效
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- A 变化后 C 的失效事件
- B 变化后 C 无失效的 validity 快照
- 影响闭包报告

## T016 · 计划读集、原子激活、在途工作generation
工作包：P3；需求：R16；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 无关图变化可重验证合并；相关旧结果不能接受为新版本
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T017 · 完成历史不改写，当前验收有效性可失效
工作包：P3；需求：R17；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 撤销依据使当前Resolution失效，但历史完成和已发生动作保留
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T018 · 真正的Plan-space搜索、frontier与可恢复搜索状态
工作包：P4；需求：R18；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 多个方法计划可选择、剪枝、恢复，不只是同一Task多次Prompt
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T019 · Best-first/Beam与有模型时的模拟lookahead
工作包：P4；需求：R19；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 搜索策略在同一接口下运行；无Simulator明确禁用rollout，不执行真实副作用
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T020 · 分层Manager、独立scope与权限代次
工作包：P4；需求：R20；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 组Manager只能提本scope变更，根接管不误杀合法子执行
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T021 · 失败片段复用与跨方法综合再验证
工作包：P4；需求：R21；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 多个失败方案的有效片段经独立检查组成新候选并重验
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T022 · 角色作为搜索偏置而非名称，支持多样性约束
工作包：P4；需求：R22；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 同任务不同角色/上下文策略可观测，避免只多开同prompt
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T023 · 真实进展、剩余不确定性与边际价值信号
工作包：P8；需求：R23；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 评分来自被验证的覆盖/反例/解锁/成本，不能把次数当知识
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T024 · 按Task/Method分配角色、模型、候选与预算
工作包：P4；需求：R24；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 不同分支的资源可动态不同，并保留最终验证/交付预算
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T025 · 稳定Obligation阻止拆分/改名/换角色刷重试与预算（v1.2 细化）
工作包：P1；需求：R25；层：contract_and_integration；状态：NOT_RUN。

前置：
- Obligation O 下 Task T1 已失败 2 次。

步骤：
- T1 改名为 T2。
- T2 换角色。
- O 换方法后再失败 1 次。

断言：
- 同一责任的重命名、替代、不同方法共享一份内容失败账
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- obligation 账本行（失败 3、额度累计不重置）
- 各 Task 与 O 的绑定关系

## T026 · 现实Provider容量、上下文上界与预算尾部一致
工作包：P4；需求：R26；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 共享物理模型的不同profile合计不超槽位；方法扩展不挪用受保护尾额
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T027 · 验证积压触发真实背压、探索保底、抗振荡
工作包：P4；需求：R27；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 慢Verifier抑制扩展且后续恢复，不造成低分支永远饥饿
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T028 · 独立语义Verifier与结构/权限检查分工
工作包：P6；需求：R28；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- Worker不能自审；格式合法不等于内容正确；不必要的LLM调用不替代机械校验
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T029 · 条件化Knowledge及多重Justification（v1.2 细化）
工作包：P1；需求：R29；层：contract_and_integration；状态：NOT_RUN。

前置：
- Knowledge K 条件 α；两份独立支持集 J1、J2。

步骤：
- 在无 α 情境查询 K。
- 撤回 J1。
- 撤回 J2。

断言：
- 条件α下的结论不能直接用于无α情境，存在多个支持时逐一检查
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 无 α 情境的拒绝回执
- 撤 J1 后 K 仍有效的 justification 快照
- 撤 J2 后的失效传播事件

## T030 · 依赖失效贯通知识、摘要、方法和验收
工作包：P3；需求：R30；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 来源/要求变化沿具名读依赖传播，摘要缓存同步失效
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T031 · Agent短期记忆按token预算取最近完整交互
工作包：P5；需求：R31；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 不同tokenizer计数有效；角色/工具/召回计入输入，输出另预留
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T032 · 所有保留历史均可检索，不能只搜最近2000条
工作包：P5；需求：R32；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 旧记录远超2000后仍能由精确ID/词面/语义找回
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T033 · 共享知识混合检索与带条件的分层摘要
工作包：P5；需求：R33；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 同义query可找回旧知识，关键条件不因200字符截断消失
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T034 · Context按scope/用途隔离，已冻结请求不可重组
工作包：P5；需求：R34；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 新知识只影响新请求；检索结果不再次入库冒充新事实
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T035 · 父子任务交接、typed inbox、澄清与回执
工作包：P4；需求：R35；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 父代理改要求以版本化事件通知，子代理消费并确认而非共享可变聊天
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T036 · Proposal/Commit唯一逻辑写入（v1.2 细化）
工作包：P1；需求：R36；层：contract_and_integration；状态：NOT_RUN。

前置：
- hierarchical Mission；一份合法 PlanRevisionProposal。

步骤：
- 尝试绕过 plan_commits 直接写 method_instances 表。
- 通过 commit_plan_revision 提交同一提案。

断言：
- 方法、任务图、预算、事件在一个提交协议内，无旁路写正式状态
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 旁路写入被拒的断言
- 一次事务内写入的方法/图/预算/事件清单
- 事件与投影一致性对比

## T037 · 所有关键业务投影可由事件与受控基线重建
工作包：P7；需求：R37；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 清空新模式派生投影可重建完整图/知识/预算/责任/计时器，不只状态字段
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T038 · 重放不执行工具，恢复不复活租约或已批准旧权限
工作包：P7；需求：R38；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- replay零Provider调用；恢复重新核验运行与授权，不重发未知动作
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T039 · execution/orchestrator双库有可靠收据与去重
工作包：P7；需求：R39；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 任一提交间隙崩溃不丢结果、不双结算，不声称跨库ACID
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T040 · 外部Operation跨Attempt稳定、UNKNOWN先核对
工作包：P6；需求：R40；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 改HTN方法/重新执行前核对已发生外部行为，不靠task_id重置
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T041 · 补偿是独立授权任务，不是回滚模型搜索树
工作包：P6；需求：R41；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 邮件已发后放弃分支仍保留事实；补偿需单独批准并可失败
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T042 · 长期Commitment与有限ExecutionCycle
工作包：P7；需求：R42；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 周期任务不把一轮成功当长期承诺关闭，下一周期有独立身份和预算
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T043 · 持久定时、时区、错过周期策略
工作包：P7；需求：R43；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 休眠/重启后按skip/latest/bounded-all/human策略补触发且无重复
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T044 · 要求版本变更与独立重新验收
工作包：P3；需求：R44；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 新目标仅由授权变更命令产生；旧验收不能批准新需求
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T045 · 单主助手跨Mission通知Outbox与收执
工作包：P9；需求：R45；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 任务完成后用户可断线补收，通知重试不重复任务
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T046 · 同一Mission内不同Task使用不同领域能力
工作包：P6；需求：R46；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 代码+资料+SQL/网页可组合，不能把所有非code任务当document
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T047 · 实际形式化检查，不只formal_check枚举
工作包：P6；需求：R47；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- Lean验收指定命题和环境，拒绝sorry/未授权公理；LLM评语不能代替kernel结果
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T048 · 能力注册/可达/健康/授权彼此分开
工作包：P6；需求：R48；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 任务规划前看到当前能力；未装工具拒绝而不是伪造PASS
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T049 · Windows/Linux/macOS执行语义隔离
工作包：P6；需求：R49；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 每平台实际回收进程/限制网络文件，不能仅凭TS/Python或进程ID宣称隔离
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T050 · 最小权限、证据来源与提示注入隔离
工作包：P6；需求：R50；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 恶意来源不能借方法定义、共享记忆、建议工具扩大权限
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T051 · 完整Trace/贡献链/版本/费用归因
工作包：P8；需求：R51；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 能追到方法、参数、输入版本、上下文选材和失败分支真实成本
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T052 · 独立外部评测而非内部Verifier自报
工作包：P9；需求：R52；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- IPC HTN和端到端任务分别评分；同模型同预算对照含全角色成本
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T053 · 学习型路由/分解/优先级的可训练实现与校准
工作包：P8；需求：R53；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 真实数据训练产物可复现，保留集校准；不得把rules-v1叫训练模型
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T054 · 策略离线评测/审批/回退，禁止在线自提升
工作包：P8；需求：R54；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 新方法或路由未准入不能变部署默认；无收益明确不晋级
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T055 · 用户长期记忆保持当前排除范围
工作包：P9；需求：R55；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 不构造Memory SDK依赖，不把Worker历史沉淀成用户事实
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T056 · Workflow仍是工具层可调用能力
工作包：P6；需求：R56；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 新增HTN只规划Task/Method；不把Workflow变成另一种Agent权威
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T057 · 关键契约类型/边界校验/事务归属检查（v1.2 细化）
工作包：P1；需求：R57；层：contract_and_integration；状态：NOT_RUN。

前置：
- TaskCritic 与 MissionJudge 两个身份；一份 JSON 合法但 budget_account 指向错误账户的提案。

步骤：
- 以 TaskCritic 身份提交该提案。

断言：
- TaskCritic/MissionJudge身份不混，JSON合法不跳过预算归属
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- 拒绝回执（WRONG_ACCOUNT）
- 类型校验通过但事务归属失败的两段日志

## T058 · Host与UI共享协议/后端唯一状态投影
工作包：P9；需求：R58；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 坏消息标协议失败而非UNKNOWN，保留旧画面且标stale
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T059 · 停止、反复重规划、死锁和饥饿治理
工作包：P4；需求：R59；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 计划震荡/反复换名计入同一责任预算；探索和年龄不突破硬约束
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T060 · 隐私、冷热归档、恢复manifest与删除不复活
工作包：P7；需求：R60；层：contract_and_integration；状态：NOT_RUN。

前置：
- 固定新语义版本、当前源码基线、隔离数据库/工具世界。
- 准备满足该项的正常输入与一份破坏其约束的输入；沿实际公开/内部边界进入。

步骤：
- 按工作包完成整条生产调用链，不绕过Commit或直接修改结果表。
- 执行需求指定正常路径，再执行相应失败/重复/变更路径。
- 比较事件、Current State、输入/产物/预算与用户可观察结果。

断言：
- 按保留策略重建，删除/tombstone不被旧快照索引复活；不记录密钥/隐藏推理
- 记录实际执行入口、固定版本和证据；未实现或未部署不能标PASS。

证据：
- input_manifest
- event_ids
- before_after_state
- artifact_or_receipt_hashes
- failure_diagnostics

## T061 · 空方法库不是固定类型模板（v1.2 细化）（TG）
工作包：P2；需求：R04,R05,R07,R08；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 将预写领域模板禁用，只提供已注册低层能力和两组未见参数。

步骤：
- 输入一个需三项能力组合的新目标。
- 让方法生成器提出方法，经独立审阅/结构检查后在隔离域执行。
- 换参数与措辞再执行，不能根据题目ID选择硬编码树。

断言：
- 保存Method定义、参数Schema、实例绑定、检查记录与实际结果。
- 无可用能力时明确不可执行，不虚构工具。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome
- 确定性版本：方法提案由 fixture provider 脚本化；真实模型版本按 §21.5 Grok 协议执行

附件 TG：附件 TG 场景 A：同一引擎生成、执行并验收一个层次计划；两域方法只靠数据注册；不同 method 中同名文件不混合；含最小持久 Commit 与重启。

## T062 · 部分序不被暗中线性化（v1.2 细化）
工作包：P2；需求：R08,R15；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 方法中A/B无依赖，C同时消费A/B；另设ORDER-only边。

步骤：
- 编译为Task网络并运行。
- 对PANDA适配执行模型/计划/分解见证校验。

断言：
- 保留允许的并行性；数据边绑定输出；不支持的导出特性明确拒绝。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T063 · OR成功不被另一条未采用方法阻塞（v1.2 细化）（TG）
工作包：P2；需求：R10,R12；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 同一目标两条合法方法，其中一条可完成。

步骤：
- 执行第一条取得有效根Resolution；第二条未启动或失败。

断言：
- 根目标按被接受方案完成，不要求历史所有节点成功。
- 仍未核对的外部动作另有处理义务，不能被终态吞掉。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG 场景 A：OR 未选路线不阻塞根 Resolution；根完成由显式 Resolution 而非末端叶子推断。

## T064 · 未知不是假更不是成功（TG）
工作包：P3；需求：R06,R09,R13；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 方法前提查询返回无结论，而非明确FALSE。

步骤：
- 请求推进。
- 执行新取证任务将状态变为TRUE或FALSE。

断言：
- 未知期间不执行受前提约束的高风险操作。
- 根据真实新结果继续或换方法，LLM断言不能代替证据。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG 场景 B：A 前提被受控工具证据否定（不是文字抱怨）；UNKNOWN 不当假也不当成功。

## T065 · 共享C的A分支退出不重做C（TG）
工作包：P3；需求：R11,R14,R25；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- C已接受，被A/B两个方法共同消费。

步骤：
- 由证据使A失效。
- 提交相关修复并继续B。

断言：
- C执行调用数和成本不增加，仍被B引用。
- A残留操作按原身份收敛。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG 场景 B：共享 C 的只读合同已验证；切 B 保留 C；B 缺 D 则追加 D；旧 A 费用保留。

## T066 · DATA与ORDER失效范围不同（TG）
工作包：P3；需求：R15,R16,R17；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- D消费C输出；O只需C先完成且不读其数据。

步骤：
- 创建C的新数据版本。
- 计算当前读依赖闭包并提交。

断言：
- D需要新结果或复审；O不因纯先后关系自动重做。
- 读依赖覆盖未知时记录保守失效原因。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG 场景 B：ORDER 祖先文件不自动混入 I2' 的 InputManifest；DATA 变化才使消费者失效。

## T067 · 迟到Review不能批准新要求（TG）
工作包：P3；需求：R16,R17；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- Verifier正在审阅旧R1/I1/A1绑定。

步骤：
- 用户授权改为R2；相关计划已切换。
- 提交旧Review。

断言：
- 旧Review保存为历史，不产生R2当前Resolution。
- 已发生费用正常结算。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG 场景 B：旧 Review 不批准 B；组合产物不兼容被拒绝。

## T068 · 两个Manager冲突读集（TG）
工作包：P3；需求：R16,R36；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 同scope两提案读取同一义务/方法版本。

步骤：
- 并发提交不同方法替换。

断言：
- 至多一个按该版本生效；另一个冲突或重新验证，不Lost Update。
- 无关scope更新可以通过合法重验而不是全局一律失败。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG 场景 C：两个 Manager 在同一 base 提出变更，分别验证无冲突合并与合并后成环拒绝；消费者创建与取消竞争。

## T069 · 不断换名不能洗掉失败预算（v1.2 细化）
工作包：P1；需求：R25,R36,R59；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 同一义务内容失败已达到限制。

步骤：
- 改Task名、Agent角色、Method名并再次请求分配。

断言：
- 稳定Obligation费用/失败计数不重置。
- 授权新增范围与简单重试明确区分。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T070 · 搜索重启保留frontier
工作包：P4；需求：R18,R19；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 隔离Simulator中有多个候选节点，部分已评估。

步骤：
- 在frontier持久提交后终止控制进程。
- 重启只恢复搜索。

断言：
- 原节点不重复扣已结算模拟成本，策略版本一致。
- 不调用现实副作用。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T071 · 模拟器缺失时拒绝MCTS实操
工作包：P4；需求：R19,R40；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 所选域未注册Simulator，但有真实外部写工具。

步骤：
- 请求simulated_uct策略。

断言：
- 返回明确策略不可用；真实工具调用数为0。
- 只有经用户/策略授权才能切换另一种执行策略。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T072 · 失败片段合成不是晋级整份失败成果
工作包：P4；需求：R21,R12；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- A/B整体失败但各有一份可证有效片段。

步骤：
- 引用片段生成新综合候选。
- 植入组合接口错误并独立验收。

断言：
- 片段来源保留，失败候选整体不VERIFIED。
- 组合错误被拒，修复后才能形成根Resolution。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T073 · 协调者接管不误杀合法子执行
工作包：P4；需求：R20,R26,R35；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 组Manager活跃，子任务合同/权限无变化。

步骤：
- 使Manager租约过期并替换其epoch。

断言：
- 旧Manager不能Commit，新Manager读正式状态。
- 有效子执行继续，非无差别全树取消。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T074 · 共享模型两profile与慢Verifier
工作包：P4；需求：R24,R26,R27；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 两个profile指向同一物理模型，容量K=2。

步骤：
- 并行两Mission并故意放慢Verifier。

断言：
- 全角色handoff合计不超过K；上游实际减速。
- 积压缓解后按低水位恢复，必要验证尾额未挪用。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T075 · 超过2000条后的三种历史定位
工作包：P5；需求：R32,R34；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 在Journal第20条写关键记录，之后追加至少5000条合法记录并索引。

步骤：
- 按精确seq查询。
- 按独特关键词查询。
- 按保留集同义问题语义查询。

断言：
- 三种路径均有可核验候选覆盖；命中原记录与hash。
- 索引故障时披露范围，不声称不存在。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T076 · 长记录末尾限制不得截没
工作包：P5；需求：R31,R33,R34；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 关键限制在一段超过200字符资料末尾，随后多轮压缩。

步骤：
- 生成分支/全局摘要并让下游执行相关任务。

断言：
- 限制在必要Context或精确状态约束中可见。
- 不因省略条件造成未经许可执行；保留来源。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T077 · 跨scope与删除缓存复活
工作包：P5；需求：R32,R34,R60；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- A/B两个Mission有相似私有材料；A材料随后删除。

步骤：
- B语义查询。
- 从旧索引/旧快照恢复后再查询A。

断言：
- B不得读A正文或引用存在性。
- 已删除内容不从旧向量/摘要复活；历史完整性限制可见。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T078 · 冻结请求与新索引竞争
工作包：P5；需求：R31,R34；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- Provider请求已冻结但尚未收到响应。

步骤：
- 更新摘要和索引版本。
- 重启恢复该请求。

断言：
- 恢复使用原选择/请求指纹，不因检索新结果换输入。
- 下一新请求可使用新版本。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T079 · 多理由有效性不是单链删除
工作包：P3；需求：R29,R30；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 命题K有两组独立充分证据J1/J2。

步骤：
- 撤回J1。
- 再撤回J2。

断言：
- J1撤回后若J2仍充分且无反证冲突则K可用。
- 全部支持失效后相关方法/Acceptance/摘要失效。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T080 · 循环互证不创造事实
工作包：P3；需求：R29,R30；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- A只引用B，B只引用A，无外部有效支持。

步骤：
- 申请将A/B升为可依赖Knowledge。

断言：
- 拒绝循环自证或标无法确定；图内相互引用不被当独立证据。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T081 · 同Mission跨域和指定定理身份
工作包：P6；需求：R46,R47,R28；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 任务包括CSV/SQL分析、Python产物、文档及Lean命题。

步骤：
- 运行合法版本。
- 提交可编译但命题被换成True的Lean版本。
- 提交含未获准占位/公理版本。

断言：
- 每Task走对应能力且父权限不扩大。
- 错误命题和未允许占位不能因编译成功被批准。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T082 · 发出后回执丢失再换方法
工作包：P6；需求：R40,R41,R50；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 模拟真实写连接器支持幂等查询，已执行发送但丢响应。

步骤：
- 让Manager切换另一方法再次需要同一次发送。

断言：
- 先核对原Operation，实际发送次数1。
- 补偿若需要独立审批与新操作，不把搜索回退当撤回。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T083 · 只保留事件基线与CAS重建新模式业务（TG）
工作包：P7；需求：R37,R38,R39；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 生成包含改图、费用、Review、timer与操作的完整测试Mission。

步骤：
- 清空可重建投影，保留事件/Genesis/执行账本/CAS。
- 禁外部副作用重建。

断言：
- 计划结构、义务、预算、有效性和等待与原快照一致。
- Provider/工具调用新增0；活性以新核对为准。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG 场景 C / 崩溃切点：提交前后 SIGKILL；重建新图投影不调用 Provider；下游只因真实 DATA/支持变化重判。

## T084 · 旧历史缺字段不补造全覆盖（TG）
工作包：P7；需求：R37,R60；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 旧库事件不包含完整预算/图等细节。

步骤：
- 运行旧Replay及新导入评估。

断言：
- 报告明确coverage与cutover基线，不声称早期历史可完全重建。
- 未知关键事件阻止相关流自动写操作。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG §11.2：旧记录缺证据时采用标注覆盖范围的 legacy 基线，不伪造完整过去。

## T085 · 跨时区休眠多周期补触发
工作包：P7；需求：R42,R43,R45；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 长期承诺含固定时区规则，暂停跨过多个触发点及夏令时边界。

步骤：
- 分别按skip/latest/bounded-catchup/ask-user恢复。

断言：
- 实际生成的cycle_key与预定策略一致，重启重复不多发。
- 一次周期成功不关闭长期承诺，用户补收通知不再执行任务。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T086 · 备份少一个CAS或执行账本水位（TG）
工作包：P7；需求：R38,R39,R60；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 一致恢复manifest包含两库及CAS引用。

步骤：
- 破坏一份artifact或提供不匹配execution快照。

断言：
- 恢复保持副作用关闭并明确缺项；不通过重跑未确认操作伪造完整。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

附件 TG：附件 TG §11.3 崩溃切点：Commit 后派发前恢复原 Outbox；SDK 已收工作未回执按 creation_key 找回；方法替代但旧动作 UNKNOWN 时禁止替代动作重复执行。

## T087 · API可用性不能当业务质量奖励
工作包：P8；需求：R23,R51,R53；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 某模型总返回200但外部评测失败；另一模型可完成任务。

步骤：
- 构建训练集并训练路由/排序候选。

断言：
- 正确区分传输成功、内容验收、外部评分与未知。
- 报告全角色成本和样本支持，不用200回复率冒充成功率。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T088 · 学习方法不能在线扩大授权
工作包：P8；需求：R53,R54；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 学习器提出更高分的方法，同时请求超出scope工具或增加安全例外。

步骤：
- 尝试注册/晋级。

断言：
- 安全/授权边界拒绝；其他候选仍按离线保留集比较。
- 没有收益不晋级，但训练/校准能力可被独立验证。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T089 · UI坏消息与业务UNKNOWN分离
工作包：P9；需求：R45,R58；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 真实Host显示合法任务快照，并有完成通知待确认。

步骤：
- 注入坏schema消息、重复完成通知、丢失创建响应。

断言：
- 界面保留合法数据并标stale，协议错不变任务UNKNOWN。
- 重试复用命令/通知身份；不得再次启动业务工作。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome

## T090 · 整体效果评测与范围完整性
工作包：P9；需求：R52,R55,R59；层：cross_component_end_to_end；状态：NOT_RUN。

前置：
- 固定保留任务族、相同模型/资源、独立官方或外部评分器。

步骤：
- 运行强单Agent/静态多Agent/完整架构并做HTN与知识组件消融。
- 核对59项必需要求与R55排除规则。

断言：
- 任务族隔离，全部角色/失败费用入账，未运行明确标NOT_RUN。
- 无质量收益如实报告；不能删HTN需求换更好成绩或偷偷接用户Memory。

证据：
- frozen_manifest
- actual_trace
- query_or_plan_binding
- budget_and_operation_ledger
- independent_outcome
