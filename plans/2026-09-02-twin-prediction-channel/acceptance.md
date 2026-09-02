# 验收标准：数字孪生预测通道（v1：会话任务请求源）

<!-- flow-tier: FULL（LLM 参与写入持久记忆并影响后续决策，输入语义敏感）；MACHINE_GATE 不启用（无权限/支付/schema迁移/公共API/不可逆副作用等高外部性条件），完成记账 = journal -->

## 矛盾分析（全文档的骨架）

- **主要矛盾**：用户再次遇到同类任务时，Agent 能否基于历史处理经历给出用户认可的方法建议（"上次用 A 方法处理了 B 事件，这次 B 又来了"）。
- **产生原因**：Agent 目前每个会话都从零开始，用户的处理习惯不沉淀、不复用；现有记忆系统已有 canonical Procedure / `applies_to` / recall lane 等全部原语（S5a 已接入 host），但没有任何组件把"一次任务处理经历"结构化抽取成可检索的 episode，也没有预测读路径消费它；展示型数字孪生图被 TC-HM-12 硬约束为 display-only，不能承担决策职能。
- **解决方向**：在 simple_harness host 层新建预测通道——任务结束时以结构化运行时痕迹为骨架 + LLM 补一句情境摘要抽取 episode，经公开 strict-atomic API 写入 canonical Procedure + `applies_to` + evidence；新任务发起时经 recall lane 检索相似 episode，按频次/时近/outcome 加权评分，按置信度分级作用于决策（低→注入 context，高→显式建议待确认，永不静默执行）。
- **最小验证动作**：一条端到端最短链——完成一次真实任务（触发 episode 落库）→ 发起同类任务 → recall 返回该 episode 且建议命中方法 A（一个脚本或一次会话可判定 PASS/FAIL）。
- **矛盾的主要方面**：episode 写侧抽取质量——方法链与 outcome 必须 100% 来自结构化痕迹（零幻觉），情境摘要必须可检索；写侧数据脏了，读侧一切预测都是空转。第一仗打写侧。

## 范围

- **包含**：会话任务请求事件源；episode 抽取（结构痕迹骨架 + LLM 情境摘要）；canonical 写入（Procedure + `applies_to` + evidence，公开 strict-atomic API）；向量索引（canonical state 的可重建投影）；预测读路径（recall lane + 频次/时近/outcome 评分）；置信度分级作用；采纳/覆盖反馈回写；suppression/erasure fence 贯穿预测全路径。
- **明确不包含**：UI 操作行为源、外部触发事件源（后续 slice）；展示孪生图 `get_twin_graph_view` 的任何改动（display-only 约束原样保持）；memory-sdk 内部/schema 改动（只消费公开 API）；序列级预测模型（v3 远期）；S6 级 UI 大改（建议呈现用最小可用形态）。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 矛盾地位 | 优先级 |
|----|--------|-------------------|----------|--------|
| AC-1 | episode 抽取 | 任务会话正常完成后自动生成 episode：情境摘要、方法链（skill/tool 调用序列）、outcome（隐式信号：正常完成且无重试/纠正=成功）、时间、source refs。方法链与 outcome 100% 取自结构化运行时痕迹，与实际执行记录逐字段一致；LLM 仅生成情境摘要 | 决定性 | 必须 |
| AC-2 | canonical 写入与索引可重建 | episode 经公开 strict-atomic API 落 Procedure + `applies_to` + evidence，一个 receipt 覆盖；删除向量索引后可从 canonical state 完整重建，重建前后检索结果一致 | 决定性 | 必须 |
| AC-3 | 预测读路径 | 发起同类任务时经 recall lane 检索出历史 episode，评分含频次、时近、outcome 三信号，输出带置信度与 source refs 的方法建议（= 最小验证动作） | 决定性 | 必须 |
| AC-4 | 置信度分级作用 | 低置信仅注入 recall context 供 Agent 权衡；高置信显式建议 + 预填方案待用户确认；任何置信度下均不静默自动执行 | 决定性 | 必须 |
| AC-5 | 反馈回写 | 用户采纳建议记为正向证据；覆盖建议（改用 C 方法）后，同类情境的后续预测转向 C（经 supersedes/amends 演化关系） | 次要 | 必须 |
| AC-6 | suppression/erasure fence | 被 forget/suppress 的 procedure 在任何置信度下不再出现于预测建议或注入 context（fail-closed）；erasure 后重放不复活 | 次要 | 必须 |
| AC-7 | display-only 不变量 | 预测通道启用前后，`get_twin_graph_view` 输出与 TC-HM-12 步骤 8 探针（RecallDecision/rank/ContextSnapshot 对孪生图开关的零影响断言）行为不变 | 次要 | 必须 |
| AC-8 | 抽取降级 | LLM 摘要失败/超时时，episode 仍以结构痕迹落库（摘要字段缺省），不阻塞 committed-turn 链路 | 次要 | 必须 |

## 非功能 / 边界

- **幂等**：同一 committed turn 重放不产生重复 episode（复用 turn receipt 去重语义）。
- **性能**：episode 抽取异步于回合关键路径（LLM 摘要不增加用户可感知延迟）；预测检索遵守现有 recall budget。
- **空态**：无历史 episode 时零建议、零报错、零空转提示。
- **兼容**：不改 memory-sdk schema；不动既有 recall lane 合约，预测作为消费方接入。

## Assurance 摘要

- Profile：standard（默认）
- 受保护资产：canonical memory state 完整性；用户隐私（suppressed/erased 内容不外泄）；既有 recall/孪生图行为不回归。
- 可信假设：开发者账户与 OS；Memory/Harness SDK 公开 API 合约；Agent 运行时结构化痕迹的真实性。
- 范围内失败：LLM 摘要幻觉污染检索；表面相似任务误建议；fence 泄漏；索引与 canonical 漂移。
- 最大可接受影响：建议错误但用户可一键拒绝；**不可接受**：静默执行错误方法、泄漏被遗忘内容、污染 canonical state。

## 测试场景矩阵（输入语义敏感，required）

| scenario_id | input_class | exact_input（自然用户语言） | 矛盾地位 | gate_type | required | terminal_expectation | quality_bar |
|-------------|------------|------------------------------|----------|-----------|----------|----------------------|-------------|
| TS-1 | 同类任务复现 | 先"帮我把这份周报整理成表格"（用方法A完成），隔轮再发同类请求 | 决定性 | positive-value | yes | 建议命中方法 A，带置信度与来源 | 建议在用户视角明显对应上次做法 |
| TS-2 | 表面相似语义不同 | "帮我把这份周报发给张三"（与 TS-1 共享"周报"字面但意图不同） | 决定性 | negative-safety | yes | 不给出方法 A 的高置信建议 | 无误导性建议 |
| TS-3 | 覆盖后转向 | TS-1 建议出现后用户说"不要用A，用C来做"，再复现同类任务 | 次要 | positive-value | yes | 预测转向 C | 不再顶着用户纠正推 A |
| TS-4 | 遗忘后复现 | 用户要求忘掉该 procedure 后再发同类请求 | 次要 | negative-safety | yes | 零相关建议，context 零泄漏 | fail-closed |
| TS-5 | 冷启动空态 | 全新用户数据目录首次发任务请求 | 次要 | negative-safety | yes | 正常处理，无建议无报错 | 无空转噪音 |

## 完成的定义（DoD 摘要）

- 主要矛盾对应的决定性 AC（AC-1~AC-4）实测达成，最小验证动作链 PASS——此项 FAIL 时其余 PASS 不能救场。
- 全部"必须"条款通过对应测试；TC-HM-12 相关探针无回归；ARCHITECTURE 文档已同步。
- 完成记录 = 一页 journal（含终态行），无机器 receipt。
