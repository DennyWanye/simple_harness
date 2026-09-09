# HM-TO-A6 第 11 次整跑结果（2026-09-09 14:45，Host 26fd6f6f 源码 + bundle 3f30a17b，Memory 0.6.37，flash 关闭 thinking，窗口 32000）

24 轮走完；`a6_verify.py`：**PASS 16 / FAIL 1 / BLOCKED 1 / INCONCLUSIVE 0**（事件 AC + 验证器口径修正后复算；原判 12/5/1）（`RUN-11-a6-verify.json`；证据 `.local-test-evidence/2026-09-09/native-a6-run11/primary-ui-9izlp1ao`）。与第 10 次（14/2/2）相比：三处修复被证实，同时 s5c 修复让 NC-3 首次被真实计量而暴露新缺陷，另有两处验证器口径需修。

| 项 | 第 10 次 | 第 11 次 | 说明 |
|---|---|---|---|
| A6-1 | PASS | PASS | 22 Run 全终态 |
| A6-2 | FAIL | **BLOCKED** | 读门（F-Z1）生效但 fixture 在任务根外，读越界未接绑定提案 → F-Z1b（修中） |
| A6-3 | FAIL | FAIL | 组装超限 **0**、线上门只拦下 T17（W-c 修中）；FAIL 来自「后 8 轮峰值 / 前 8 轮 = 2.27×」——T17 的 18 KB 用户原文进入受保护区推高峰值，该比例规则应扣除当轮用户消息（验证器口径，待改） |
| A6-4 | PASS | PASS | — |
| A6-5 | INCONCLUSIVE | **FAIL（但有实质进展）** | **goal.set 首次成功、README/STATUS 首次进入 bounded 形态（16383 字节）**；FAIL 因 EVIDENCE 视图 `event_count` 151 ≠ 归档 176 行 → 事件 AC（修中） |
| A6-6 | PASS | PASS | applies_to 知识边 |
| A6-7 | PASS | **FAIL** | 两条多 revision 记忆之一是 Prospective 的 `triggered` 生命周期推进，无 evolution 边——验证器应豁免生命周期推进（口径，待改） |
| A6-8 | INCONCLUSIVE | **PASS** | 争议组 1，T22 要求确认（且 T21 Run 不再被门拦下） |
| A6-9 / A6-10 | PASS | PASS | 遗忘争议端点后 14→12 节点、1→0 边，关表重开一致 |
| A6-11 | PASS | **FAIL** | memory_id 命中 37 次且不止在工具回执里——需按消息角色归因（疑为 Prospective 触发提醒卡或争议通知带出的 id；口径/缺陷待定） |
| A6-12 | PASS | PASS | 85/85 |
| NC-1 | PASS | PASS | T3 no_recall |
| NC-3 | PASS | **FAIL** | 「以后有机会我想学画画」被分析车道提成 **带虚构时间触发器的 Prospective**（trigger_at 距发言仅数分钟）并在旅程中触发；此前几次「通过」只因 s5c 回归让注册全失败 → **事件 AE**（修中） |
| NC-2/4/5/6 | PASS | PASS | — |

## 本次证实的修复

| 事件 | 证据 |
|---|---|
| 事件 U | T17 `task_scope_update goal.set` 成功，README/STATUS bounded |
| 事件 AA | T18 12 次调用完成，无 `recall_context_use_authority_stale` |
| W-b | T17 携带量 0（不再重复计数） |
| 事件 Z（收尾指令） | 组装超限 0 次 |
| F-Z1 | 读门按路径判定，越界拒绝（fail-closed 正确） |
| 0.6.37 + 事件 V | A6-8/NC-4 连续两次通过 |

## 新派/进行中

F-Z1b（读越界 → 绑定提案）、W-c（CJK 估算 + 实测比例）、事件 AE、事件 AC、X-2（后端 RSS 终值 4.7 GB）、验证器口径三处（A6-3 比例扣当轮用户消息、A6-7 豁免生命周期推进、A6-11 按角色归因）。
