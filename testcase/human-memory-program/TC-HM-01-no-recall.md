---
id: TC-HM-01
purpose: Verify a context-sufficient request records no_recall and performs no short-horizon or long-term query
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A4, HM-TO-R4, S5A-TO-NORECALL, S5A-TO-RECALL]
tags: [human-memory, no-recall, latency, audit]
entrypoint: primary conversation and context_route
revision: 1
---

# TC-HM-01 — 当前上下文充分，不需要记忆

## 前置

- 全新隔离数据目录，唯一永久主对话；记录真实主模型 root run、turn 和 Provider invocation count。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 输入一句待改写文本。 | turn 原始证据先提交；开始一个 foreground root Run。 |
| 2 | 立即输入“把我刚才这句话改得更简洁一点。” | 主模型通过同一流式链路提出或收敛为 `direct_standalone` / `no_recall`。 |
| 3 | 检查 route/RecallDecision 和 SDK 指标。 | `outcome=no_recall` 可审计；short-horizon 和四类长期库查询计数都为 0。 |
| 4 | 检查 Provider 调用链与最终回答。 | 不发生工具调用后的第二次模型续推理；只用当前上下文完成有效改写，不创建 TaskScope。 |

## 决定性证据

- UI 输入/回答、root/turn/invocation ID、RecallDecision、Memory SDK 查询计数、Provider invocation count、TaskScope count。
- 仅“回答正确”但无法证明零查询和单生成链不能判 PASS。
