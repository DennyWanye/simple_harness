# HM-TO-A6 第 8 次尝试（2026-09-09，deepseek-v4-flash，Host 243369c0，窗口钉 32000）

证据目录：`.local-test-evidence/2026-09-09/native-a6-run8/primary-ui-*`（gitignored）。驱动：`scripts/native/a6_driver.sh`（FIFO 暂停、确认发送、Run 终态等待、分析批等待）。

## 逐轮状态

| 轮 | 结果 | 备注 |
|---|---|---|
| T1–T4 | COMPLETED | 事件 R 修复后 scope-source 误判未再出现 |
| T5 | COMPLETED | 大结果落在预算内（f161f5a4 校准 + 降级顺序生效，`pages_forced=1`） |
| T6 | COMPLETED | 模型主动 `context_page_in` ×2 |
| T7–T10 | COMPLETED | — |
| **T11** | **FAILED `sdk_context_budget_exceeded`** | 见下 |
| T12–T15 | COMPLETED | 提供方报告 prompt≈29171 token（高于估算预算 26752，估算并未过度保守） |
| T16（UI） | 记录 | 关系图：5 条记忆，0 条关系；操作记录面板已打开并显示工具行 |
| T17+ | 进行中 | T23/T24 为 `@UI@` 轮 |

## T11 事故（新缺陷 F-E2，已派独立子代理修复）

日志：`planned=27015 effective=26752 protected=7771 tool_schemas=5898 groups=1 ratio=1.65 protected_messages=1873 open_group=19244 full_trim=True`

回执（`run_context_snapshot_receipts.source_revisions`）：`pages_forced=1`、`groups_trimmed_for_budget=2`、`budget_headroom` 6228→4304→2408→1528→抛出。

判断：降级顺序（强制分页所有可分页体 → 裁剪已闭合分组 → 抛出）已全部做完，**当前 Run 的开放分组单独占 19244 token**，仅超 263 token。最后一次请求 22 条消息、12 条工具结果共 39.9 KB，最大的全是控制类工具（从不分页）：

| 工具 | 字节 |
|---|---|
| task_scope_search | 11915 |
| context_route（第 1 次） | 6559 |
| context_route（第 2 次） | 3987 |
| context_page_in ×4+ | 各约 2250 |
| tool_search ×5 | 各约 2000 |

这正是事件 E 备忘录留下的 F-E2：控制类结果在同一 Run 内线性累积且不受同 Run 上限约束。修复方向由子代理按证据裁定并写 `DECISION-F-E2-CONTROL-RESULT-BOUND.md`：被后续同族控制调用取代的旧结果压成带回执 id 的短桩；`context_page_in` 结果可按 page_id 重取，超出允许量的旧页压桩；回执新增 `control_results_stubbed` 供 `a6_verify.py` 佐证；最新一轮控制结果保持原文。

对验收的影响：A6-3（同 Run 动态换页不失败）本次判 FAIL；第 9 次尝试需带 F-E2 修复重跑。

## 新发现：分析关系候选全部不可用（事件 S，已派独立子代理修复）

T18 时 `cognitive_relations` 仍为 0 行；`native.log` 有 17 条 `memory.analysis_relation_candidates_unavailable`（每个分析批一条），`error_type=KeyError`、`error_message='fb154920-9038-5bd4-8a4f-5c6875c2464a'`，记录器 `deskpet.memory.semantic_correction`。即分析协议 v8 的关系候选构建在查某个成员/头 id 时抛 KeyError，整批关系被降级为无。A6-6 本次判 FAIL；修复方向：候选构建必须解析计划引用的每个成员，不可解析的成员按逐成员原因码跳过而非整批丢弃；备忘录 `DECISION-S-RELATION-KEYERROR.md`。

## 新发现：召回超时复发（T19–T22，6 次 `context_route_recall_timeout`）

`native.log` UTC 19:59–20:00（本地 03:59–04:00）连续 6 次 `product_tool.failed tool=context_route code=context_route_recall_timeout`，每次约 2 s（1000 ms deadline + 审计）。T19 更正「Python 3.13」因此未产生 supersede（头仍 rev 1、内容 "Python 3.12"），T21 未产生争议（conf=0），T22 与 T24 重发都直接答 3.13 而未要求用户确认——A6-7、A6-8、NC-4 本次不能判 PASS。手动重发（04:04）未再超时。

假设：Host 仍钉 Memory 0.6.31，尚未含 0.6.33 的 `sqlite_tx.begin_transaction` 修复（被取消的 BEGIN 遗留孤儿事务 → 之后每次前台 recall 等锁直至 DEADLINE_EXCEEDED），与本次「触发后连续 6 次全超时、稍后自愈」的形状吻合。第 9 次尝试前钉 0.6.33（带阶段名的 DEADLINE_EXCEEDED 可直接证实或推翻）。
