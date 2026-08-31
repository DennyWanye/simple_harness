---
id: TC-HM-03
purpose: Verify explicit correction supersedes stale memory while ambiguous conflict remains contested
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A2, HM-TO-A4, HM-TO-R2, HM-TO-R3]
tags: [human-memory, correction, conflict, supersede]
entrypoint: primary conversation and recall
revision: 3
---

# TC-HM-03 — 事实变化与含糊冲突

## 前置

- 已有 active 用户事实“常用 Python 3.11”，其证据和 revision 已记录。
- 故障 seam、runner 与终态 oracle 以 `fixtures/fault-matrix.json` 的 `claim-supersede` lane 为准；fixture SHA-256
  `c67881ae20a3f6b442f1ac46db9e6e9a472edc3ec09079e8a42ef219e9b6bc6b`。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 输入“我现在常用的是 Python 3.12。” | 明确更新产生新 revision；旧值保留 lineage 但退出 active 普通召回。 |
| 2 | 对 supersede 事务逐关键写点注入故障并重放。 | 始终是 all-old 或 all-new，不出现双 active、断 lineage 或半审计。 |
| 3 | 输入含糊相反说法“有时项目好像还是那个旧版本”。 | 相关 claim 标为 contested/需澄清，不静默把 3.11 恢复为 active。 |
| 4 | 请求创建依赖 Python 版本的项目文件。 | 只有明确 active 3.12 可用；若含糊冲突影响动作正确性，系统先向用户确认。 |

## 决定性证据

- claim revision 图、evidence refs、transaction/replay receipt、RecallDecision、最终 ContextSnapshot 与工具执行前确认。
