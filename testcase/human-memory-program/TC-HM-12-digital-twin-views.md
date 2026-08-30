---
id: TC-HM-12
purpose: Verify bounded TaskScope read views and a traceable display-only digital twin projection
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A6, HM-TO-A7, HM-TO-R3, HM-TO-R5, HM-TO-R7]
tags: [human-memory, digital-twin, knowledge-graph, read-views, display-only]
entrypoint: TaskScope inspector and digital twin view
revision: 1
---

# TC-HM-12 — 六阅读视图与展示型数字孪生体

## 前置

- 一个 TaskScope 依次生成 1k/10k/100k canonical events；形成偏好、目标、关系、程序记忆，随后纠正一项并逻辑遗忘一项。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 打开 README/PLAN/STATUS/DECISIONS/RESUME/EVIDENCE 六阅读视图。 | README <=16 KiB、STATUS <=12 KiB、Resume <=24 KiB；超限细节拆到有稳定 ref 的页，canonical facts/evidence refs 不丢。 |
| 2 | 删除物化阅读视图并从 canonical raw/events/state/checkpoints 重建。 | 重建后的语义字段、refs 和 hash oracle 一致；Markdown 不是事实 authority。 |
| 3 | 打开数字孪生体知识图谱。 | 节点/关系清晰可读且每个可见结论可定位 evidence、epistemic/status；不要求独立图数据库。 |
| 4 | 检查纠正与遗忘后的图谱。 | 只显示新 active 值；suppressed 项退出普通图谱，派生重建也不复活。 |
| 5 | 在图谱开启/关闭两种状态下重复同一问题和工具任务。 | RecallDecision、Provider Context、回答与工具行为不因图谱开关改变；Context 中不存在仅由图谱生成的数据。 |
| 6 | 从 TaskScope/turn 导出 route→invocation→proposal→validation→binding/mutation→recall/projection trace。 | trace 完整、只读、不覆盖 lineage；普通导出遵守 suppression，密封读取需独立 AuditAccessDecision。 |

## 决定性证据

- 六视图字节数与 page refs、rebuild hashes、图谱 UI 截图、evidence links、开关前后 Context/decision/tool diff 和 trace export。
