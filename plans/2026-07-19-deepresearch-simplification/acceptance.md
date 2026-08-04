# 验收标准：DeepResearch 简化编排与调研 Spike

## 范围

- 包含：新调研默认使用简化编排：主 Agent 拆分子方向、每个子方向交给独立子代理、主 Agent 检查子结果并对异常项给出修复指令后继续、全部有效结果到齐后统一分析并按现有 DeepResearch 报告模板交付。
- 包含：保留 durable run、进度、取消、最终报告、引用、Artifact 与历史读取能力；旧 workflow 版本继续只读/恢复兼容。
- 明确不包含：继续扩展 v6 的专题硬编码事实链、为每类问题新增专用 stage、删除旧版本数据或重写通用 workflow 内核。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|---|---|---|---|
| AC-1 | 主 Agent 拆题 | 收到调研方向后，主 Agent 生成 2–6 个互不重复且合计覆盖原问题的子方向；拆题失败时给出有界 fallback，而不是整条 run 崩溃。 | 必须 |
| AC-2 | 独立子代理调研 | 每个子方向由一个独立 research 子代理执行；运行记录可按 parent run、child id、子方向关联，且子代理只接收原问题、自己的子方向、报告质量要求和必要反馈。 | 必须 |
| AC-3 | 主 Agent 监控 | 主 Agent 收集每个子代理的结构化结果，并区分 valid、retryable、insufficient、fatal；未完成或不合格的子结果不能直接进入最终综合。 | 必须 |
| AC-4 | 诊断后续跑 | retryable 子结果由主 Agent 生成针对性诊断/补充指令，再交给同一子方向继续处理；每个子方向有明确重试上限，耗尽后诚实标记证据不足。 | 必须 |
| AC-5 | 统一综合 | 所有子方向达到终态后，主 Agent 只基于合格子结果和明确的不足项统一分析，按现有 DeepResearch 报告模板生成一份报告，包含摘要、分主题发现、跨主题综合、局限与引用。 | 必须 |
| AC-6 | 简化生产链路 | 新调研默认进入新的 immutable workflow 版本；主链的业务阶段可直接对应“拆题 → 子代理调研/续跑 → 汇总”，不再要求经过 v6 的多套专题 pipeline 才能完成普通调研。 | 必须 |
| AC-7 | 用户可见与兼容 | Session 仍使用单张进度卡并最终交付唯一报告/Artifact；取消、重启恢复、历史旧 run 读取和旧版本 continuation 不被破坏。 | 必须 |
| AC-8 | 可观测性 | 日志/trace 至少能证明拆出的子方向、每个 child 的状态与 attempt、主 Agent 的重试诊断、join 完成和最终综合；不得记录完整敏感页面正文。 | 必须 |
| AC-9 | 调研过程可见 | `plan` 完成后，同一张 DeepResearch 进度卡展示 2–6 个实际子方向；每个方向显示 queued/running/retrying/valid/insufficient 之一、当前 attempt/上限和已接纳来源数。重试 attempt 不得被误计为新的子方向，最终状态与 backend child record 一致。 | 必须 |
| AC-10 | 报告文件操作 | 最终消息除报告正文外必须渲染真正的报告 Artifact 卡，显示文件名/类型，并提供“打开”“另存为”“在文件夹中显示”和“复制路径”操作；至少通过真实 UI 点击验证打开、另存为和在文件夹中显示，失败必须给出可见反馈。 | 必须 |
| AC-11 | 保存目录兼容 | 新 v7 报告继续保存到既有 `DeepResearch` 报告目录，文件名包含可识别主题与 run id；Artifact payload 中的 canonical path 必须指向该文件，不能只生成无路径的 `research_report` 文本卡。 | 必须 |

## 非功能 / 边界

- 并发：子代理可有界并行；同一子方向最多一个 active attempt；join 必须等待所有子方向进入终态。
- 重试：默认每个子方向总共最多 2 次尝试（首轮 + 最多 1 次补救）；不得无限循环或把失败静默吞成成功。
- 证据：最终报告中的事实必须能追溯到子代理返回的来源；无可用证据时使用 `insufficient_evidence`，不得编造。
- 默认开启：通过测试后新简化 workflow 立即成为新调研默认版本；旧版本仅做兼容。
- 性能：spike 应在现有 DeepResearch 用户可接受超时边界内结束，并输出分阶段耗时。
- UI 一致性：DeepResearch 子方向面板是 parent workflow 的业务进度，不复用“子代理开发”通用 attempt 计数；历史消息重新打开后仍可看到最终方向状态与文件操作卡。

## 测试场景矩阵

| scenario_id | input_class | exact_input | primary_risk | gate_type | required | manual_required | terminal_expectation | quality_bar |
|---|---|---|---|---|---|---|---|---|
| S-1 | 技术比较决策 | 帮我调研一下 2026 年本地 AI 编程助手的主流技术路线、代表产品、隐私取舍和适用团队，最后给我选型建议。 | 拆题覆盖、跨主题综合与建议 | positive-value | 否（后续回归候选） | 是 | completed + 唯一非空报告 | 至少 3 个有效子方向、每个有效方向有来源、结论能说明取舍而非资料拼接 |
| S-2 | 政策与市场 | 帮我调研中国低空经济目前的政策、产业链、代表企业和主要风险，给出未来两年的观察指标。 | 时效性、异构来源和风险分析 | positive-value | 否（后续回归候选） | 是 | completed 或有明确不足的 partial | 至少覆盖政策/产业/企业/风险中的 3 类，关键结论有引用，明确时间边界 |
| S-3 | 开源技术生态 | 请对 Tokio 异步运行时做深度调研，覆盖架构、核心组件、适用场景、常见陷阱，并给出选型建议。 | 拆题、异常续跑与统一综合 | positive-value | 是 | 是 | completed，或在重试耗尽后给出明确不足的 partial；唯一非空报告 | 至少 3 个有效子方向，覆盖架构/组件、适用场景、陷阱与选型建议，引用可追溯且不足项不伪造 |
| S-4 | 冷门/低证据 | 调研一个没有公开资料的虚构项目 DeskPet Quantum Research Cloud 2027 年市场份额。 | 诚实降级与重试止损 | negative-safety | 否（后续回归候选） | 是 | insufficient_evidence 或 partial，不得伪造份额 | 明确说明缺少公开证据、列出已尝试方向，不生成虚构数字 |

## 完成的定义

- AC-1 至 AC-8 均有代码、自动化测试或真实运行证据。
- 先完成至少一个正向自然语言 spike，报告经人工检查达到对应 quality bar；再做必要回归。
- 对输入敏感链路完成本轮唯一 required 场景 S-3 的真实入口验证；其余场景明确保留为后续回归候选，不计入这次“快速修改 + 一个 spike”的完成门槛。
- AC-9 至 AC-11 必须使用用户刚发送的 S-3 run 或修复后的等价新 run 做真实 UI 验证；文件按钮不得只用单测或协议调用代替点击。
- 同步更新 `ARCHITECTURE/DeepResearch.md`、`ARCHITECTURE/PROJECT_STATUS.md` 与 `testcase/index.md`。

## 2026-07-20 最终验收状态

- **本轮范围 PASS**：AC-1～AC-11 全部有自动化或真实 UI 证据，唯一 required 场景 S-3 已从真实
  Tauri 入口完成；engine completed 与 3 valid / 1 insufficient 的业务 partial 分开记录。
- AC-9～AC-11 的 root run 为 `9c42a6a145304123979f8ec6ae25cba0`：4 个稳定方向、attempt 2
  原位更新、标准 file Artifact 四项操作、既有 `DeepResearch` 目录保存和同 userdata 重启恢复均 PASS。
- S-1、S-2、S-4 在本文件中始终标记为非 required 后续候选，不计入本轮“快速修改 + 一个 spike”
  的完成度，也没有用 S-3 的重复运行冒充多个 distinct 场景。
- 兑现表、hash、日志与人工报告 review 见
  [`spike/result.md`](./spike/result.md)；逐步真人操作见
  [`testcase`](../../testcase/2026-07-19-deepresearch-simplification/README.md)。
