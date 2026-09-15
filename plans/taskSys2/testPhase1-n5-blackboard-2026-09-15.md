# N5 AgentDojo 黑板传播机制正负控

最后更新：2026-09-15 08:51 CST。确定性切片；0 应用模型调用。正式 A96/B96 与完整对照集仍 OPEN。

## 结论

AgentDojo 在 `scoped-observation-v2` 下，模型 Claim 最多 SUPPORTED。本切片补上 Host 系统观察晋级：官方 FunctionsRuntime 成功返回的原文，在 Task `accept_result` 之后写成 VERIFIED `tool_observation`。Blackboard 仍只读。失败/错误工具回执、伪造 knowledge id、以及模型把注入文本写成语义事实，都不会变成 VERIFIED 知识。

自动 `verified_knowledge` 入模仍受词面检索门槛约束：下游 Task 目标与观察原文无重叠时，知识已提交但 ContextPackage 为空。本正控把下游 goal 写成含 marker/`read_note` 的查询，才能走消费链。这不是检索已修好。

## 四张表

### 整体计划

|包|已完成切片|未关闭门槛|已有实际测试时间|
|---|---|---|---|
|N1|共享容量、进程身份、长响应、独立Judge及当前UI|长时恢复、Flash512K资格|5调用压力428.455秒；v60 1713.610秒|
|N2|可信应用观察、负控、失败预算审计|困难任务知识产生→下游消费收益|最近v4 1557.359秒，FAIL保留|
|N3|两道原失败题结果契约修复后交付|完整困难矩阵、条件检索/摘要优化|后继290.623秒|
|N4|通用12题×4方案×2重复运行器|A96/B96正式运行|正式0次|
|N5|正常/攻击各一例官方效用；**本切片：黑板传播确定性正负控**|自然干净/攻击对照、完整对照集、模型自行识别攻击|官方209.980/1046.198秒；本切片pytest 7.11秒|
|N6|自定义ARE动态硬判通过|完整Gaia2、独立judge|275.301秒|
|N7|预声明、任务块区间统计接线|正式配对消融与收益结论|工程未独立计时|
|N8|当前源码UI、证据与main同步|完整正式split/任务组、最终交接|工程未独立计时|

### 当前部分细节

|事项|结果与范围|实际时间|
|---|---|---|
|接手环境|Host `d3ad039c`、SDK `22c19df` 与 origin/main 一致；源码UI仍在 18140/15173；磁盘接手约2.3GiB，清理可重建 `target`/Playwright/ShipIt/pip 后约7GiB|未独立计时|
|允许工具边界|Mission `allowed` 现含 `knowledge_list`/`knowledge_read`；自动上下文注入与显式知识工具仍是两条路径|含在定向pytest|
|系统观察晋级|成功 `read_note` 在 accept 后写入 `system:agentdojo-tool-observation-v1`；注入 hook 原文作为数据保留|含在定向pytest|
|正控两Task|Task A 暴露 marker → 系统知识 VERIFIED；Task B 在 USER 包引用并 `used_knowledge`；模型语义 Claim 非 VERIFIED|含在 7.11 秒套件|
|负控|官方 error 回执 0 知识；伪造 id 使下游 VerificationFailed / MissionFailed，0 KnowledgeUsed|含在 7.11 秒套件|
|AgentDojo 文件回归|`test_agentdojo_orchestrator.py` + `test_agentdojo_bridge.py` **40 PASS**，含原 success/cancel/structured|7.11 秒（第二次 5.35 秒 7 selected）|
|应用模型|本切片 0 次 Qwen/Flash 调用|0|

### 剩余估计

|包|下一门槛|粗略剩余工程时间（模型运行/等待另计）|
|---|---|---|
|N5|冻结后 Qwen256K 自然干净/攻击对照；词面检索是否挡住自然传播需实测|4–10小时＋模型运行|
|N1|有限长时恢复和B512K资格|1–3小时|
|N2/N3|困难知识消费、完整矩阵|合计5–11小时|
|N4/N7|冻结A96/B96与消融分析|合计4–9小时＋正式模型运行|
|N6|完整Gaia2、动态协议和独立judge|8–18小时＋judge门槛|
|N8|正式split/任务组与最终交付|3–6小时＋完整运行|

### 本次续作增量

|事项|结果|实际时间|
|---|---|---|
|磁盘清理|删除可重建 Tauri target/debug+release、Playwright、ShipIt、pip 缓存；未删 `.local-test-evidence`|未独立计时|
|N5 Host 观察桥|`agentdojo_knowledge.py` + `promote_agentdojo_tool_observation`；runner 记下成功回执并在 accept 后晋级|工程未独立计时|
|确定性正负控|40 PASS / 7.11 秒；首次两Task因检索丢条目超时 34.84 秒已保留为机制发现，不重写成 PASS|7.11 秒定向；失败超时 34.84 秒保留|
|真实模型对照|未开始|0 调用|

## 四段链路（本切片实际核对）

1. **官方 tool 输出实际暴露**：Worker 在 `read_note` 之后的 messages 含固定 marker（及 synthetic injection hook）。
2. **candidate claim**：Worker 可把注入文本写成语义 Claim；验收后仍非 VERIFIED。
3. **验证/拒绝**：仅 Host 收据 + 成功 `tool_calls` 行 + 已接受 Task 可 `KnowledgeCommitted`。error 回执不晋级。伪造 id 走 `rule_check` 失败。
4. **下游入模与引用**：词面相关时，`verified_knowledge` 与 USER 文本含观察 id/原文，`used_knowledge` 记录复用。词面无关时知识已在库中但包为空。

失败区分：未暴露 / 未产生知识 / 被拒绝 / 已消费。本切片覆盖后三项的确定性脚本，不代表模型会识别攻击。

## 下一门槛

冻结本切片源码后，用本地 Qwen3.8 256K（`qwen38-flash-next`）跑自定义传播扩展的干净/攻击对照；官方 utility 成绩仍单独报告。不要重跑 v59–v61 长 UI 例。不要混用 Flash。
