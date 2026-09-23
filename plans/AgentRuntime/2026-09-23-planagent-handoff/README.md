# PlanAgent 交接入口

请先阅读 `HANDOFF-PlanAgent-ARP-1.0.md`，再读原始 `simpleharness-agent-runtime-plane-1.0-2026-09-22.zip`。

用户已明确：暂不适配其他 Agent Runtime（包括 Pi）。只优化原生 Runtime 增强计划，收敛 handoff 中 Q01–Q20，不开发业务代码。

交回新的完整计划包与逐项决定；可以使用 `DECISIONS-TEMPLATE.md`，不要把模板的待决状态解释为已关闭。`SOURCE-INDEX.json` 给出这次静态盘点的路径和文件 hash，不表示当前全部工作树或生产行为已验证。

可复制给 PlanAgent 的消息：

> 请按附件 HANDOFF-PlanAgent-ARP-1.0.md 优化 ARP 计划。用户已排除 Pi/其他 Runtime 适配；保留原生 AgentRuntime、Context、临时会话索引、Capability/Tool/Skill 及现有 HTN/TaskGraph/Assurance/OPS。一次性处理 Q01–Q20，区分已经证明的冲突、待补协议和源码映射；产出完整自洽的新计划、Schema/SQL/DTO/接口/来源/验收映射以及逐项 DECISIONS。不要实施产品代码，不先跑大批量测试，不把普通实现命名再次转问用户。无本地源码访问的部分保持 UNVERIFIED 并给精确核验表，不造路径/hash/来源回执。

随交付 ZIP 含原始计划 ZIP；仓库目录本身不复制原包或原始测试证据。完整性由 `DELIVERY-MANIFEST.json` 校验，manifest 不对自身作循环 hash。
