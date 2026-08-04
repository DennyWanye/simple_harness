# Follow-up — DeepResearch Top N 报告完整性与 partial 主卡可见性

> **状态**：🟡 待后续优化，当前仅登记问题，未实施新功能
> **严重度**：P1（流程可完成并交付文件，但报告可能不满足用户显式数量要求，且主卡会弱化 partial）
> **发现时间**：2026-07-20 真实 UI 调研复核
> **当前已发布基线**：`deep_research/v7`，提交 `961c7d34` 已推送到私有 `origin/master`

## 1. 复现与现象

- Session：`a4bb7275-9fed-41cf-8542-d8eb908d539f`
- Run：`9ed2660a9b2b4d348a9498995a63f0e6`
- 用户请求：`帮我调研一下，现在最新的前十个AI大模型是什么，及其公司和特点`
- 工作流事实：`deep_research/v7` 六节点全部 succeeded，4 个子方向中 2 个 valid、2 个在第 2 次尝试后仍 insufficient，业务状态为 `partial`。
- 报告事实：报告成功保存到既有 `DeepResearch/` 目录，文件卡及打开/另存为等操作链正常；但正文没有明确列出 10 个不同模型，未满足“前十个模型 + 公司 + 特点”的核心交付要求。
- UI 事实：子方向、状态、attempt 和来源数均正确显示；主进度卡仍显示引擎“已完成 / 100%”，没有在同一视觉层级明确显示“报告部分完成”，容易被理解为报告质量 100%。

## 2. 根因

1. v7 `normalize/plan` 没有把“前十 / Top N / 列出 N 个”抽成持久化的显式交付约束；通用 plan prompt 只负责拆成不同角度。
2. child 的 `valid` 门只检查报告非空、存在 citation、脚注引用合法；没有检查 N 个不同命名项，也没有检查逐项字段和逐项引用覆盖。
3. final synth prompt 要求统一报告与合法引用，但没有可机器判定的 Top N 后置条件；最终业务状态只依据“是否有可用子报告 / 是否存在 insufficient child”。
4. v7 `business_status` 已进入 report/final-assistant envelope，但没有通过版本化 `terminal_public` 进入 durable `workflow.final` 主卡投影；前端进度徽标和 100% 只消费 engine status。

## 3. 版本边界

- `deep_research/v7` 已发布并声明 immutable。改变 durable state、child validity 或 terminal semantics 不能原地修改 v7。
- 后续应新增 immutable `deep_research/v8`，复用 v7 的六节点简单 manager graph；v1–v7 保持历史读取、恢复和 checkpoint 身份不变。
- 本 follow-up 不支持恢复 v5/v6 的复杂 brief/多轮大图，也不包含搜索供应商改造。

## 4. 后续优化候选范围（未实施）

1. 服务端确定性识别 2–50 范围内的自然语言显式数量，避免把年份、版本号误判为 N。
2. manager 拆题时保证至少一个子方向承担“列齐 N 个不同对象 + 用户要求字段 + 逐项引用”。
3. child 结果验收增加“不同命名项数量 + 逐项合法脚注”门；不满足时沿用现有最多一次 continuation 补救，仍不足则 `insufficient`。
4. final synth 后执行同一套确定性审计；首次不足时最多进行一次仅基于已有子报告和 citation 的修复，不允许为凑数量编造对象或来源。
5. 枚举门或任一必要方向未满足时业务终态为 `partial`；无可用证据时为 `insufficient_evidence`；只有全部门槛通过才为 `completed`。
6. 为 v8 增加有界 `terminal_public` 投影，让主卡同时呈现“流程已完成”和“报告完整 / 报告部分完成 / 证据不足”。
7. 保持 `DeepResearch/` 保存路径、唯一文件卡、打开/另存为/在文件夹中显示/复制路径及历史恢复行为不变。

## 5. 后续验收要求

- 正向一：真实输入“最新的前十个 AI 大模型及公司和特点”；若业务状态为 completed，报告必须有不少于 10 个不同模型项且每项带引用。
- 正向二：跨域输入“盘点 5 个适合小团队的开源 RAG 框架，分别说维护方、优点和局限”；验证数量、多字段和英文缩写不会丢失。
- 对照：无显式数量的普通调研不得被错误套用 Top N 门。
- 负向：对无公开资料的 12 项清单请求不得编造凑数，必须诚实 partial/insufficient。
- 真实 UI 必须验证业务终态徽标、子方向原位重试、报告内容、既有文件操作及重启恢复；自动化需覆盖数量解析、Markdown 条目审计、v8 terminal projection、乱序重放和 v1–v7 兼容。

## 6. 关联材料

- 初步验收草稿：[`2026-07-20-deepresearch-enumeration-quality/acceptance.md`](2026-07-20-deepresearch-enumeration-quality/acceptance.md)
- 当前架构差距：[`2026-07-20-deepresearch-enumeration-quality/architecture-baseline.md`](2026-07-20-deepresearch-enumeration-quality/architecture-baseline.md)
- v7 已交付实现与测试：[`2026-07-19-deepresearch-simplification/`](2026-07-19-deepresearch-simplification/)
