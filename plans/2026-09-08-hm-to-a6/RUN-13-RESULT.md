# HM-TO-A6 第 13 次整跑结果（2026-09-09 14:09–14:42，Host 源码 `1373aec0`+docs、bundle 3f30a17b、Memory 0.6.38、flash 关 thinking、窗口 32000、`--memory-probe-light`）

24 轮走完；`a6_verify.py`：**PASS 12 / INCONCLUSIVE 4 / BLOCKED 1 / FAIL 0**（`RUN-13-a6-verify.json` / `.md`；证据 `.local-test-evidence/2026-09-09/native-a6-run13/primary-ui-hwbyjnym`）。历次：第 12 次 14/1/3。

## 结论

- 首次通过：**A6-3（预算内有界）**、A6-6、A6-7 连续通过；A6-9/A6-10（遗忘 + 关闭重开）、A6-11/12、NC-1/2/3/5/6 通过。
- 未闭合 5 项全部由**同一根因**牵连：Host 短时索引组注册超时 → 同一证据信封无限重放（事件 AK 在两轮旅程重启段定位的缺陷），本次证明它**在全新 userdata 的首轮就会发生**：`memory.evidence_ingestion_replayed` 自 06:09:31Z（T1 后 40 s）到 06:41:42Z 共 **136 次，仅 1 个信封**。后果：T8/T11/T13/T15/T18–T21 都需要第二次发送；T22 两次发送均无新 Run head（`send_failed`）；手动重发 T22 时主对话显示「等待主对话就绪」，run 内 `context_route_recall_timeout` ×2。
  - A6-1 INCONCLUSIVE：终态 Run 21 < 22（T22 未跑）。
  - A6-2 BLOCKED、A6-5 INCONCLUSIVE：T17 在 goal.set 两步流程撑爆窗口后 run.fail（事件 AL），后续 README/STATUS 未进入 bounded 形态。
  - A6-8 / NC-4 INCONCLUSIVE：争议组与 contested revision 已成，但 T22 未跑到。
- T17 失败根因（事件 AL）：模型首次 `task_scope_update` 时拿不到当前轮 evidence id，按指引"先发一次、被拒后按公布 id 重发"，第一步 6256 token 的被拒调用留在上下文，第二步 wire=26072+carry=1104 > effective=26752 → `sdk_provider_wire_input_budget_exceeded`。修复分支见 `DECISION-AL-GOAL-SET-TWO-STEP.md`。
- 手动 UI：T16 图谱 6 记忆/1 关系；T23 对 Procedure「秋分资料整理这套校对流程」点忘记 → `suppression_directives=1`、`suppression_targets=1`，图 9→8 记忆、边 1（唯一边是 contests，目标关系「流程→Python 环境」本次未成边）；T24 关闭重开 8/1，与 DB 复算一致。
- 内存探针（light）：后端 RSS 峰值 5.0 GB（T17，含 26 KB 响应与 8 KB 披露），T22 后回落 2.4–2.6 GB；探针 `rss_delta_kb` 最大单跳 1.7 GB 出现在 T13–T15（分页读文件段）。X2-F2 结论：增长与大 tool result 页缓存 + 分析 outbox 同步相关，非持续泄漏（回落）。

## 用户决定（2026-09-09 14:45）

用户将对 memory 与 task 任务编排做大改：涉及这两块的大量测试不再进行；优先交付**主流程跑通的版本**。因此本计划剩余的 A6 定向复验（AL/T17 短旅程）、两轮旅程 run2、Manual run7、401 矩阵重钉、语料剩余适配均**停止**，只合入已完成的修复（AK、AH-2、AL）、重建 bundle，并做一次主流程冒烟（启动 → 若干轮 → 同 userdata 重启 → 继续对话）。
