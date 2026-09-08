# HM-TO-A6 第 9 次尝试（2026-09-09 05:15 起，Host dbf967fc，Memory 0.6.34，deepseek-v4-flash，窗口钉 32000）

证据：`.local-test-evidence/2026-09-09/native-a6-run9/primary-ui-8whts2lo`。带入：F-E2 控制类结果压桩、事件 S 关系候选、s5c 迁移链、0.6.33 召回事务原语、0.6.34 向量相对余量、驱动 T6/T8 强制 `read_file`。

## 前 16 轮（进行中）

| 轮 | 结果 | 备注 |
|---|---|---|
| T1–T5 | COMPLETED | T5 107 s |
| **T6** | **FAILED** 预算超限 | 32 次工具调用：tool_search ×13、tool_describe ×7、task_scope_search ×5、context_page_in ×4；最后请求 51 KB，其中分页摘要 13 条 26 KB |
| T7 | COMPLETED | — |
| **T8** | **FAILED** 预算超限 | 26 次调用：tool_search ×13；分页摘要 14 条 28 KB |
| T9–T10 | COMPLETED | — |
| **T11** | **FAILED** | 14 次调用；context_route 原文 2 条 11 KB + 分页摘要 4 条 8 KB |
| T12 | COMPLETED | — |
| **T13** | **FAILED** 预算超限（驱动重试一次） | 25 次调用：tool_search ×16、context_page_in ×7；分页摘要 16 条 32 KB |
| T14–T15 | COMPLETED | — |
| T16（UI） | 记录 | 图谱 7 条记忆 0 条关系；操作记录面板可见工具行 |

到 T16 为止：`context_route_recall_timeout` **0 次**（第 8 次为 6 次；0.6.33 孤儿事务假设成立的初步证据）、`analysis_relation_candidates_unavailable` **0 次**（事件 S 生效，改为具名 `relation_procedure_applicability_absent`）、`sdk_context_budget_exceeded` **3 次**。

## 新缺陷归因

1. **F-E3（分页摘要固定成本）**：`primary_settled_effect_v1` 摘要每条约 2.0 KB（≈456 token）且与原文大小无关；flash 在一个 Run 内为找文件工具连发 13–16 次 `tool_search`（原文各 1.7–6 KB），摘要反而比原文大，16 条即 32 KB，把 26752 的预算（受保护 8.6 K）挤爆。F-E2 压桩已生效（`control_results_stubbed` 5–6、`control_stubs_forced` 3–4）但控制类只占小头；`context_route` 原文不可省略（F-E2b）。→ 已派 Opus 子代理：摘要缩到约 100–150 token、摘要不比原文小则不分页、回执记账。
2. **A6-6 设计前提**：旅程从不产生/使用 Procedure 记忆，v8 有序策略在「无流程候选」时把 T15 解析成语义声明（分支 ②），关系永远不会被提出；关系候选通道只报 `relation_procedure_applicability_absent`。→ 已派 Opus 子代理（事件 T）：让「点名流程 + 绑定已下发事实候选」的陈述走「新建流程节点 + applies_to 指向事实端点」的关系形态，真实模型复测后再定，必要时改 00-PLAN 的 A6-6 行。
3. F-S1b（Procedure 端点在 `check_history_visibility` 里恒 stale）由另一子代理跨仓库处理（SDK 0.6.36 + Host 解除扣留）。
