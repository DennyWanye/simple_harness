# 完整目标需求与源码差距矩阵

基线：`61a85eb7e8c003fa894497090f5de893419ebbd1`。分类是本轮源码证据，不是通过率。

| ID | 要求 | 来源 | 当前分类 | 目标与验收 | 工作包 |
|---|---|---|---|---|---|
| FULL-01 | 参数化方法与可执行操作模型 | D1§7; D3:HTN | NOT_REPRESENTED_IN_AUDITED_CONTRACT（S01,S02） | MethodDefinition/OperatorDefinition/Binding/Predicate AST 成为一等合同，不依赖业务类型 if/else；AT-001 | W1 |
| FULL-02 | 方法分解、AND–OR与任务执行关系分离 | D1§6; D2:ADR05; D3:AND-OR | NOT_REPRESENTED_IN_AUDITED_CONTRACT（S02,S03,S05） | Goal→Method→Goal 与执行 DATA/CONTROL 边独立；OR失败不意味着根失败；AT-002 | W1 |
| FULL-03 | 有来源和有效期的规划世界状态 | D1§10–11; D3:HTN applicability | PARTIAL（S07,S09） | Observation/Assumption 四态、来源、范围、时效，不以模型预期效果充当观察；AT-003 | W1 |
| FULL-04 | 未知方法生成与受控准入 | D1§7; D3:MethodContract | PARTIAL（S01,S17） | LLM产生MethodProposal，结构/范围/前提/组合审查后才能实例化；AT-004 | W1 |
| FULL-05 | 复合目标的组合满足与形式见证 | D1§6/14/24; D2:ADR09 | PARTIAL（S04,S14） | 父目标显式覆盖和组合验收；有分解见证，不使用最后叶子替代；AT-005 | W1 |
| FULL-06 | 持久化的细化前沿与替代方法候选 | D1§7.3/8; D3:ADaPT | PARTIAL（S05,S06） | PlanSearchState中保存候选、拒绝原因、有限搜索预算，崩溃后可继续；AT-006 | W2 |
| FULL-07 | 证据变化驱动最小影响修复 | D1§6.2/7.2; D2:ADR06 | PARTIAL（S03,S17） | 按相关read-set和支持索引修复最近方法范围，保留无关成果；AT-007 | W2 |
| FULL-08 | 共享子目标的语义去重 | D1§6.1/11; D3:Method共享 | PARTIAL（S02,S03） | 只读成果按全合同/输入/作用域复用，副作用不按文本去重；AT-008 | W2 |
| FULL-09 | 方法回退与现实后果区分 | D1§17/21; D2:ADR10/13 | PARTIAL（S13,S17） | 换方法先fence和核对；补偿是新授权操作，不回滚世界；AT-009 | W2 |
| FULL-10 | 结构化执行反馈闭环 | D1§13; D3:ExecutionFeedback | PARTIAL（S17） | 观察/诊断/建议分离，阻塞、失败、缺前提和需求变化不同处理；AT-010 | W2 |
| FULL-11 | 逐任务角色与模型组合分配 | D1§8.2/9/29.2 | PARTIAL（S06） | AllocationBundle包含角色组合、模型、额度、物理资源与依据；AT-011 | W7 |
| FULL-12 | 可校准的进展与不确定性估计 | D1§18.6/19 | PARTIAL（S06） | 从真实证据/成功义务/成本估计，不将0.5**tries当概率；AT-012 | W7 |
| FULL-13 | 开放多路线搜索与安全模拟 | D1§7.3; D3:AND-OR search | PARTIAL（S05,S06） | best-first/beam/Best-of-N/综合策略；MCTS仅可信无副作用模拟；AT-013 | W7 |
| FULL-14 | 责任累计和分层额度守恒 | D1§18.2–18.4; D2:ADR08 | PARTIAL（S06,S13,S17） | LogicalObligation跨替代沿用，支出树与DAG分开，共享工作不重复计费；AT-014 | W2 |
| FULL-15 | 物理资源、公平与真实背压 | D1§8.3/18.5 | SOURCE_PRESENT_BOUNDED（S06,S17） | 现有约束保留；新HTN/Manager/验证全部纳入共享容量与队列；AT-015 | W7 |
| FULL-16 | 有验证范围的条件知识 | D1§11.1/14.3 | SOURCE_PRESENT_BOUNDED（S09,S10） | 保留scoped observations，扩展条件/有效期/领域语义Review；AT-016 | W3 |
| FULL-17 | 证据和Acceptance真值维护 | D1§11.2/14.4; D2:ADR07 | PARTIAL（S07,S09,S13） | 支持关系反向索引、当前适用性撤回、已完成历史保持；AT-017 | W3 |
| FULL-18 | 通用片段与多产物综合 | D1§11.3/20.3 | PARTIAL（S04,S05,S17） | 片段有类型、假设和证据；组合新产物独立验收；AT-018 | W3 |
| FULL-19 | 组内/全局条件摘要 | D1§11/24.12 | PARTIAL（S08,S16） | 结构摘要+带来源语义摘要；关键限制不被字符截断丢失；AT-019 | W3 |
| FULL-20 | 冲突与反证优先处理 | D1§14.4 | SOURCE_PRESENT_BOUNDED（S04,S14） | 同条件同主体冲突才仲裁；各方法与下游均消费正确冲突状态；AT-020 | W3 |
| FULL-21 | 稳定BaseAgent与可恢复多轮 | D2:logical owner; D3:BaseAgent | SOURCE_PRESENT_BOUNDED（S15,S19） | 保留稳定身份/turn结果/等待；不在新HTN层重造Run；AT-021 | W4 |
| FULL-22 | 独立短期历史与有界窗口 | D3:AgentSessionMemory | SOURCE_PRESENT_BOUNDED（S16,S20） | 原文Journal、完整组、检索与回读；不把向量库当权威；AT-022 | W3 |
| FULL-23 | HTN角色相关的Context组装 | D1§10; D3:subagent context | PARTIAL（S07,S16,S17） | 方法/前提/组合/失败/依赖和合法工具作为受控选材；AT-023 | W3 |
| FULL-24 | 模型请求的选材、版本与恢复一致 | D1§16/17; D3:ContextManifest | SOURCE_PRESENT_BOUNDED（S15,S16,S17） | 扩展manifest到方法/条件/索引版本，冻结请求不重选历史；AT-024 | W3 |
| FULL-25 | Context容量评测与语义压缩 | D1§10/11; D3:256K configurable | PARTIAL（S08,S16） | 窗口为上限，校准模板计数和质量；语义摘要保留来源与限制；AT-025 | W3 |
| FULL-26 | 单用户Main与多Mission责任 | D2:ADR01; D3:personal assistant | UNVERIFIED_PRODUCT_WIRING（S15,S17,H0） | Main身份/任务总览/持久义务，非一个永久活着的协程；AT-026 | W4 |
| FULL-27 | 每Mission MainWork和GroupManager | D1§7.3（分层管理）; D2:ADR01 | PARTIAL（S04,S17,H0） | Scope/epoch/冲突合并，分层摘要；只有统一Commit写入；AT-027 | W4 |
| FULL-28 | 类型化Agent消息与回执 | D2:communication; D3:BaseAgent messaging | PARTIAL（S15,S19） | Inbox/Outbox、因果/权限/版本/去重；收到与执行完成分开；AT-028 | W4 |
| FULL-29 | 用户补充指令、暂停与接管 | D1§22; D2:ADR06 | PARTIAL（S15,S17,H0） | RequirementsRevision和在途工作收敛，旧manager不能继续提交；AT-029 | W4 |
| FULL-30 | 批量Agent创建与资源隔离 | D1§12/17; D3:batch BaseAgent | SOURCE_PRESENT_BOUNDED（S15,S19） | 复用现有创建与限流；子代理数量可扩展但不突破授权；AT-030 | W4 |
| FULL-31 | 领域注册和不同验证handler | D1§14.2 | SOURCE_PRESENT_BOUNDED（S10,S14） | 保留五个域入口；逐项确认真实适配，缺能力不得假通过；AT-031 | W5 |
| FULL-32 | 同Mission混合领域组合 | D1§14.2/20/21; D3:generic tasks | PARTIAL（S10,S13） | CompositeDomainBinding/TaskDomainBinding和跨域数据语义适配；AT-032 | W5 |
| FULL-33 | 通用Operator和动态能力发现 | D1§9/21; D3:HTN operators | PARTIAL（S10,S17） | I/O/前置/读写/核对合同；registered不等于authorized；AT-033 | W5 |
| FULL-34 | 数学SQL网页科学等参考执行 | D1§14.2/21 | NOT_VERIFIED_IN_AUDITED_PATH（S10,S14） | 实际参考适配及正负案例，非registry占位；Workflow保持工具层；AT-034 | W5 |
| FULL-35 | 形式证明与非形式结论分级 | D1§14.1–14.2; D2:ADR02 | PARTIAL（S14） | Verifier用证明内核/实验作证据，报告具体范围；不统称已证明；AT-035 | W5 |
| FULL-36 | 完整业务事件重建 | D1§16; D2:ADR04 | PARTIAL（S12,S18） | 新业务对象完整事件语义；原始执行ledger独立回读；AT-036 | W6 |
| FULL-37 | 预算/意图/任务图的可恢复投影 | D2:full state replay | EXPLICITLY_OUTSIDE_CURRENT_REPLAY（S12,S18） | 旧Replay排除字段补入新coverage，未知过去不填假记录；AT-037 | W6 |
| FULL-38 | 冻结历史和schema迁移 | D1§17/23; D2:old events | SOURCE_PRESENT_BOUNDED（S10,S12,S18） | 新planning binding/codec，legacy baseline anchor，原始字节不改；AT-038 | W6 |
| FULL-39 | 外部操作幂等及恢复核对 | D1§17/21; D2:ADR10/13 | SOURCE_PRESENT_BOUNDED（S13,S17） | 新HTN方法切换/长期cycle不绕过旧operation identity；AT-039 | W6 |
| FULL-40 | 跨平台可运行执行语义 | D3:Linux/Windows; D1§20/21 | UNVERIFIED_PLATFORM_COVERAGE（S17,H0） | Host平台探测/沙箱/进程树/文件/凭证的实际完整切片；AT-040 | W5 |
| FULL-41 | 跨年承诺与有限履约周期 | D2:ADR12 | NOT_CONFIRMED_IN_AUDITED_PATH（S13,S17,H0） | Commitment/Cycle显式责任，不无限延长单Mission；AT-041 | W6 |
| FULL-42 | 持久定时器和停机补做 | D2:§20 | NOT_CONFIRMED_IN_AUDITED_PATH（S17,H0） | 到期队列、时区DST、catch_up_policy、去重和最大补做；AT-042 | W6 |
| FULL-43 | 要求变化与局部有效性 | D1§6/17; D2:ADR06/07 | PARTIAL（S03,S13） | 根要求修订、read-set影响域、旧验收与新要求不混用；AT-043 | W2 |
| FULL-44 | 完成通知与用户读取分离 | D1§23/24; D2:delivery | PARTIAL（S15,S17,H0） | durable delivery receipt和界面新鲜度，重通知不重执行；AT-044 | W4 |
| FULL-45 | 长期保留/删除/备份权限 | D2:§backup/deletion; D1§16/21 | UNVERIFIED_FULL_COVERAGE（S12,S18,H0） | 恢复旧备份先应用删除撤权；CAS引用/敏感内容生命周期明确；AT-045 | W6 |
| FULL-46 | 唯一提交与最小权限 | D1§15/17.5/21 | SOURCE_PRESENT_BOUNDED（S13,S17） | 新planner/solver/学习器均只提案，Runtime独立硬门；AT-046 | W5 |
| FULL-47 | 授权与输入在handoff重查 | D1§21; D2:ADR07/10 | SOURCE_PRESENT_BOUNDED（S13,S17） | 计划前提验证不替代当前权限；新旧执行者fence与read-set；AT-047 | W2 |
| FULL-48 | 独立Verifier/人工仲裁 | D1§14/22; D2:ADR02 | SOURCE_PRESENT_BOUNDED（S14,S17） | 关键内容必需独立Review；检查工具不替代开放内容验收，冲突不投票；AT-048 | W5 |
| FULL-49 | 提示注入与跨Agent污染防护 | D1§10.2/21.3 | PARTIAL（S07,S10,S14） | 摘要/知识/方法/消息全部带来源，外部内容不能改变authority；AT-049 | W5 |
| FULL-50 | 规划与收尾预算完整计量 | D1§18; D3:Critic/Judge budget | SOURCE_PRESENT_BOUNDED（S06,S13,S17） | HTN生成/审查/求解/综合/最终Judge预算同账本，不重复保留；AT-050 | W7 |
| FULL-51 | 参数化方法学习与反例修订 | D3:Method learning; D1§28.4 | NOT_IMPLEMENTED_BY_RULES_V1（S11） | 实际候选归纳/测试/批准/退役，原版本保持；AT-051 | W7 |
| FULL-52 | 训练型优先级与ModelRouter | D1§28.4 | PARTIAL（S06,S11） | 实际训练/校准产物、行为范围和模型注册，不仅硬权重更新；AT-052 | W7 |
| FULL-53 | 角色Prompt信誉与群体稳定性 | D1§9/28.4 | PARTIAL（S11） | 分任务难度/模型版本，受保护角色与安全门不随信誉取消；AT-053 | W7 |
| FULL-54 | 外部效果评测与对照 | D1§23.4; D3:recognized benchmarks | SOURCE_PRESENT_BOUNDED（S10,S11,S22） | 检查现有adapter正式规则；新增HTN强基线、消融、独立grader和真实成本；AT-054 | W7 |
| FULL-55 | 知识贡献和因果结论区分 | D1§23.3/18.6 | PARTIAL（S11） | lineage表明使用关系，增益以配对/受控实验验证，不编造因果；AT-055 | W7 |
| FULL-56 | 跨端公开契约与类型边界 | D1§26; D3:critical contract hardening | PARTIAL（S02,S03,S13,H0） | SDK内部typed领域与Host公开schema分开，当前Store收不到坏消息；AT-056 | W4 |
| FULL-57 | UI展示方法/前提/修复解释 | D1§23/31; D3:task tree UI | UNVERIFIED_PRODUCT_WIRING（H0） | 四图不同视图，显示选择和依据，不把树组件当HTN；AT-057 | W4 |
| FULL-58 | 端到端故障和反例测试 | D1§30; D2:failure matrix | SOURCE_PRESENT_BOUNDED（S17,S22） | 扩展到HTN/共享/方法学习/长期义务；所有验收本轮未执行；AT-058 | W6 |
| FULL-59 | 独立HTN规划验证与国际基准 | D3:generic HTN proof; D1§23 | NEW_REQUIRED_VALIDATION（R5,R8） | HDDL支持范围、分解见证、外部validator与未见域，不能只测图无环；AT-059 | W7 |
| FULL-60 | 需求不漂移与完整定义 | D1§1–31; D2:ADRs; D3:current request | SCOPE_DRIFT_IDENTIFIED（D4） | 每项保留需求→代码→验收；取消延期须明确批准，不以阶段清单替代目标；AT-060 | W1 |
