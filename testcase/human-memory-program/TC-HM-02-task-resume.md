---
id: TC-HM-02
purpose: Verify a distant TaskScope is discovered as a candidate then exactly opened and completely resumed
status: active
surface: desktop-ui
type: hybrid
obligations: [HM-TO-A3, HM-TO-A4, HM-TO-A6, HM-TO-A7, HM-TO-R7, HM-TO-R8, HM-S4-TO-VALUE, HM-S4-TO-AUTHORITY]
tags: [human-memory, taskscope, search, exact-open, resume]
entrypoint: primary conversation, task_scope_search, and task_scope_open
revision: 3
---

# TC-HM-02 — 久远任务发现与完整恢复

## 前置

- 在同一主对话中交错完成相似任务 A/B；A 至少包含目标、操作、一次修改、一次取消及原因、文件/commit/test 状态、checkpoint 和未完成下一步。
- A 已离开最近目录；冷重启并更换新的 Agent root run。

## 步骤与预期

| 步骤 | 真人操作/探针 | 预期结果 |
|---:|---|---|
| 1 | 输入自然口语“继续以前的 A”，不提供 exact ID。 | 主模型可调用长期 `task_scope_search`；结果只是 permission-first 候选，不切 scope、不授予项目 effect。 |
| 2 | 当 A/B 名称仍有歧义时选择 A。 | Host 以 exact task_scope_id `task_scope_open` canonical Archive；active cursor 和 binding revision 只在确认后更新。 |
| 3 | 检查 ResumePackage 与 README/STATUS/checkpoint。 | 恢复 A 的 goal/phase/completed/changed/cancelled reason/next action/roots/repo/files/commit/tests，不混入 B；物化视图可从 canonical facts 重建。 |
| 4 | 在工作目录或依赖版本制造 drift，再请求继续。 | Agent 先报告 drift 和受影响步骤，不盲目沿用旧环境执行。 |
| 5 | 删除物化视图后重建并冷重启再次打开 A。 | canonical state、evidence refs 和 resume oracle 相同；任务档案不依赖 Session 或 Markdown 存活。 |

## S4 Host 自动化子 lane（required）

- 仅通过公开 Host API/facade 在 fresh isolated data dir 创建同一 subject 下的相似任务 A/B；禁止私有 import、直连 SQL 或读取实现内部状态。
- 向 A 追加 100,000 条 canonical events，其中包含 goal、completed、changed、cancelled reason、next action、evidence refs 和 checkpoint；向 B 的 title/snippet/candidate metadata 写入独有 poison canary。
- 记录 search 前的 active cursor、binding revision 和 tool-authority refs；删除可重建的 projection/search cache，冷重启 Host，以 fixed typed query 先搜索再 exact open A。
- 候选返回阶段必须保持 cursor/binding/tool authority byte-identical；wrong principal 和 stale revision 稳定 fail closed；只有 exact A ID 可组装 ResumePackage，query/snippet/candidate poison canary 不得进入 A ResumePackage、binding 或 tool grant。
- 断言 ResumePackage 不超过 24 KiB，B canary 零混入，checkpoint drift 显式报告，且所有超限 canonical facts/evidence refs 均可由稳定 page refs 恢复。
- 本子 lane 不输入自然语言，不要求主模型路由或 UI；它只证明 S4 Host 的 candidate→exact-open→bounded-resume 行为。

## 决定性证据

- search candidates、确认、exact open、Archive/checkpoint/read-view hashes、ResumePackage、A/B canary 零混入、进程 incarnation 与文件/Git/test 状态。S4 原始输出只写 ignored `.local-test-evidence/`。
