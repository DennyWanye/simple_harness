# HM-TO-A6 第 8 次尝试结果（2026-09-09 04:10，Host 243369c0，Memory 0.6.31，deepseek-v4-flash，窗口 32000）

24 轮全部走完（T16/T23/T24 为面板操作，T24 手动重发 22 轮问题）；`a6_verify.py` 判定 **PASS 10 / FAIL 3 / INCONCLUSIVE 5 / BLOCKED 0**（JSON：`RUN-08-a6-verify.json`；原始证据 `.local-test-evidence/2026-09-09/native-a6-run8/primary-ui-a_tg7ppv`，SHA 见 JSON）。

| 项 | 判定 | 关键数字 | 归因 / 下一步 |
|---|---|---|---|
| A6-1 20+ 轮动态组装 | PASS | 22 Run 全终态、receipt ordinal 连续 | — |
| A6-2 大结果分页 | FAIL | reference_id=0、成功翻页 7、ALPHA 锚点未命中 | 回执 `pages_forced=1` 但请求内无分页引用——待 F-E2 子代理一并核对分页形态与验证器字段（F-E1） |
| A6-3 预算内有界 | FAIL | T11 `sdk_context_budget_exceeded`；后 8 轮峰值 13029 / 前 8 轮 7371 = 1.77×（上限 1.6×） | F-E2：同 Run 控制类结果压桩 |
| A6-4 因果链 | PASS | 87 次请求无孤立 tool 消息，7 次裁剪均从头部丢组 | — |
| A6-5 README/STATUS 超限拆分 | INCONCLUSIVE | README bounded 修订 0 | T17 flash 未把超长目标逐字写入 goal.set（模型行为）；按 plan 第 4 节可用多条 decision.record 顶 PLAN 重试 |
| A6-6 关系记忆 | INCONCLUSIVE | cognitive_relations=0 | 事件 S：分析关系候选 KeyError（每批） |
| A6-7 纠正 supersede | INCONCLUSIVE | 无第 2 个 revision | T19/T20 召回超时 ×2，更正未落 supersede（0.6.31 无 0.6.33 孤儿事务修复） |
| A6-8 争议 | INCONCLUSIVE | conflict_groups=0 | 同上（T21 召回超时 ×2） |
| A6-9 普通投影过滤 | PASS | nodes=5 edges=0 与 DB 复算一致 | — |
| A6-10 遗忘 + 关闭重开 | INCONCLUSIVE | 关系行 0，遗忘无关系对象（记忆遗忘本身成功：suppression 1，列表 6→5） | 依赖 A6-6 |
| A6-11 图谱不入 Context | PASS | 87 次请求 0 命中，T16/T24 增量 0 | — |
| A6-12 指纹重放 | PASS | 87/87 | — |
| NC-1/2/3/5/6 | PASS | — | — |
| NC-4 争议期不用旧值 | FAIL | T22 未要求确认 | 争议未建立（同 A6-8） |

## 本次暴露的三个主流程缺陷（均已派独立 Opus 子代理）

1. **F-E2**：同 Run 内控制类工具结果不可分页、线性累积（T11 超 263 token）。
2. **事件 S**：`semantic_correction` 关系候选构建 KeyError（21 批全部），关系永远为 0。
3. **召回超时复发**（6 次，T19–T22）：假设为 0.6.31 缺 0.6.33 的 `sqlite_tx.begin_transaction` 修复；第 9 次前钉 0.6.33，用阶段名 DEADLINE_EXCEEDED 证实。

## 第 9 次尝试前置条件

F-E2 + 事件 S 合入 main → Host 钉 Memory 0.6.33（或 0.6.34）→ 重建 bundle → 无并发 corpus/agent 测试占用 venv → 白天有人值守。

## A6-2 补充归因（2026-09-09 04:30）

`execution_effects` 显示 flash 本次从未调用 `read_file`，两份 fixture（40 003 / 47 670 字节）都用 `run_shell cat` 读取，结果被 shell 工具的输出上限截到 17 950 / 21 322 字节（含 JSON 包装），请求中没有任何 >16 KiB 的 tool 消息，分页路径从未触发，ANCHOR-ALPHA 也因截断未被模型看到。这是模型选工具的行为差异（第 5 次用 `read_file` 时 A6-2 通过），不是 Host 缺陷。第 9 次驱动的 T6/T8 提示改为「用 read_file 工具（不要用 shell 命令）一次读出全文」，以确保走大结果分页路径（`a6_driver.sh` 已改并提交）。

## F-E2 合入（2026-09-09 05:05，`566011b7`）与子代理对 T11 诊断的三点更正

- `tool_search` 不是控制类工具且本次已全部被分页（4 条 `primary_settled_effect_v1`）；控制类实际为 `context_route` ×3（11 573 B）+ `task_scope_search`（12 389 B）= 41 078 B 工具字节中的 80.4%。
- 修复形态：每 Run 控制类结果字节配额、最旧优先压成固定成本的**省略通知**（`primary_control_result_elided_v1`，约 160 token），不是分页引用（可执行证明不能变成可读回的准入令牌）；`context_route` 结果永不省略（`typed_context_use` 从请求体重读它）；最新一轮控制结果保持原文。事故形状离线复算：planned 27 016 → 19 545，余量 −263 → +7 207。
- 估算器备注：T8/T9 估算稳定高出真实 4.5%，T11 的 `planned=27015` 折算真实约 25 857 < 26 752，本次抛出很可能是**估算假阳性**（归 Incident N / F-TOK-6，未在 F-E2 内处理）。
- 评审 MUST-FIX 已修：受保护轮次改从全部因果来源推导（否则只含旧 `task_scope_search` 的 Run 永远不省略）。遗留 F-E2b–f 见备忘录 `DECISION-F-E2-CONTROL-RESULT-BOUND.md`。
