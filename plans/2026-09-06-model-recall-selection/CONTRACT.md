# 模型记忆类型选择接线

2026-09-06。原始 HM-AC-4/7/8 的有界接线修复，不使用 plan-test 系列技能。

问题：context_route 只接受查询文本，HumanMemoryV7Runtime 固定选择 semantic/episode/procedure，不能把这三种 Host 默认类型计为模型选择。

当前范围：memory_standalone 工具要求模型显式提交非空、去重的 memory_types（四种长期类型的子集）。Host 决定可用类型、可信身份/披露及预算；SDK执行的RecallPlan仅请求模型所选类型，不自动扩成三类。显式长期类型选择不隐式查询短期索引。原有内部直接调用未提供选择时仍标为Host默认路径，不作为模型预测证据。

验收：
- 真实工具→Host runtime→已安装Memory公共接口，观察到模型只选semantic时plan只含semantic；episode/prospective和组合同理。
- 缺失、空、重复、未知/非字符串类型在第一次Memory操作前拒绝，并保留工具拒绝记录；不能把类型参数伪装成身份、用途或预算覆盖。
- 成功路由的既有Host审计记录保存经验证的类型枚举及model_proposal来源，并与实际Run/call/effect及完整proposal_hash绑定。查询正文/任意参数不复制到detail。原proposal只留哈希承诺；这不是公共SDK审计页能回读完整原proposal/plan的承诺。测试即时观察实际public plan验证透传，不能冒称为持久审计回读。模型不能依靠额外无关类型混入Context；所选类型不同的幂等重放不共用错误结果。
- 受影响既有前台召回/最终出站依赖用例继续验证；短期调度/来源接线、Procedure适用性及Prospective调度独立保留，不能冒称已闭合。

测试单进程，无本地模型或真实Provider，资源受限执行。实际结果及历史失败见 RESULTS.md；持久类型投影仅覆盖成功路由，失败/超时attempt的完整类型投影和原质量评估runner独立待补。
