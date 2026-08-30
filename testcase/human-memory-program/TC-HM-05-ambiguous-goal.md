---
id: TC-HM-05
purpose: Verify an ambiguous wish is a semantic goal and never a scheduled prospective intention
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A5, HM-TO-R6]
tags: [human-memory, semantic-goal, prospective, negative-safety]
entrypoint: primary conversation
revision: 1
---

# TC-HM-05 — 模糊愿望不生成提醒

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 输入“以后有机会我想学画画。” | 可提取为 Semantic Goal；因缺少明确行动触发条件，不得成为 pending Prospective。 |
| 2 | 注入一个错误地提出 pending reminder 的 LLM plan。 | 确定性验证降级或拒绝该 operation，记录稳定 reason code；无 scheduler registration。 |
| 3 | 重启 Worker 和 Host，并推进 fake clock。 | 不产生提醒、trigger candidate 或外部动作；原始证据仍可审计。 |

## 失败判定

- 任何 reminder/pending Prospective、静默补全日期或越权外部动作均为 FAIL。
