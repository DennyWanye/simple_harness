# HM-TO-A6 尝试 5 结果（2026-09-08 23:20 – 09-09 00:33，Host f7b14325 + Memory 0.6.31，DeepSeek，窗口 32000）

证据：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-a6-run5/primary-ui-htxhf38f`（单进程 24 轮；第 7 轮因旧驱动重发跑了两次；第 9 轮起驱动改为"发送前等上一 Run 终态"）。核对脚本：**PASS 11 / FAIL 1 / BLOCKED 1 / INCONCLUSIVE 5**（尝试 4 为 6/7/0/5）。

## 通过项（11）

A6-4 因果链、A6-7 纠正 supersede（rev 2 + amends）、A6-8 争议（争议组 1，T22 与 T24 重发均要求用户确认、列出现有值与挑战值）、A6-11 图谱不入 Context（118 次请求 0 命中，T16/T24 增量 0）、A6-12 指纹重放 118/118；负控 NC-1..6 全部通过（含 NC-4 争议期不直接用旧值）。

事件 K 的效果：第 6 轮 12 次工具调用空参数 0 次（尝试 4 同轮 10/28）；全程仅 1 次空参数。事件 O 的效果：T22 回答"处于争议状态…请你确认"。事件 M 的效果：记忆列表页正常、争议头可遗忘（T23 UI 遗忘成功，suppression 1，图谱 14→13 条记忆，关闭/重开后仍 13 条记忆 1 条关系）。

## 未通过 / 未判定项及根因

| 项 | 判定 | 根因 / 下一步 |
|---|---|---|
| A6-6 relation | FAIL | 本次后端早于事件 L（分析协议 v8）合入；下次运行验证 |
| A6-2 分页 | BLOCKED | 模型仍以 `run_shell` 分块读文件（单条 <16 KiB）；T13 在同 Run 内改路由后直呼 `run_shell` 触发事件 A 的残留（SDK 屏障，Host 只能在 describe/activate 阶段拦截）→ 记 followup，需 Harness SDK 屏障改动 |
| A6-1 / A6-3 | INCONCLUSIVE | T6、T17 触及 SDK 默认 900 s 墙钟：DeepSeek 对大输入每次调用 45–160 s；事件 Q 把上限设为显式并给长输入余量 |
| A6-5 视图超限 | INCONCLUSIVE | T17 18 KB goal.set 因墙钟失败未入档 |
| A6-9 / A6-10 | INCONCLUSIVE（人工核 A6-10 PASS） | UI 观测：遗忘后节点从图谱消失、关系行 append-only 未减少、关闭重开不复活；A6-9 争议期图谱仍显示 1 条关系（amends/contests 之一），普通投影对争议边的过滤口径需与 S3 契约核对 |

## 结论

Host 侧结构性问题已清空；本次未通过项全部有明确归因：L 未上线（已合入）、墙钟余量（Q 修复中）、模型读文件方式（分块 + 路由漂移，事件 A 残留在 SDK）。第 6 次运行条件：L/N/Q/P 合入后，用新验证包 `b7b5bcd0+`。
