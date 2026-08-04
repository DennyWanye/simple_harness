# A2 回炉：真实 Godot fixture oracle 校准

日期：2026-08-03

## 触发原因

Task 9 首次将计划里的固定 oracle 跑到真实 `HarnessPublicReadService` 时，发现计划把旧 UI 中的
106 条 activity 记录误写成完整 public-fact 数量。按 phase-3 A2，执行线停止了 synthetic
fixture 提交，没有用合成来源冒充真实 Root。

## 只读证据与代码修复

- 来源 Session：`2e69be7e-0b16-4bfb-a774-d61e588c5ec3`。
- 来源 Root：SHA-256 身份以 provenance 保存；文档只保留前缀 `a3a63c99...`。
- workflow.db 全程只读；旧 state.db 只复制到精确临时目录后迁移，复验结束已移入回收站。
- 找到并修复：execution source stream 被按 fact kind 拆开；旧 Root 的完整恢复链因缺新格式
  terminal parent ref 被错误降为普通 completed；provider-call/effect 在阶段内重复显示。
- 修复提交：`43dbc4a8`。
- 聚焦联测：149 passed；真实 Root 复跑无 `causal_cycle` 或 incomplete 诊断。

## 修订后的冻结 oracle

- 366 个完整 public facts；不能丢弃其中 260 条去迁就旧 UI 数字。
- 6 个实际出现的顶层阶段，符合 AC-SRV-6 的 6～8 阶段质量门；不能制造不存在的阶段凑 7。
- 29 个唯一逻辑工具，其中 23 个 shell；provider-call/effect 在语义视图只出现一次。
- child 原始 failed 保留；FailureReport、failure set、replacement Attempt 和后续 root terminal
  共同证明 `completed_with_recovery`。
- `projection_complete=true`，完整分页 stable ID 无重复无遗漏。
- fixture generator 两次运行 byte-identical，provenance 固定 source-root hash、tested code SHA、
  read cut、source/kind counts、generator version、fixture SHA-256，secret scan 为零。

## 范围影响

不修改 acceptance、BC-1～BC-7 或产品行为目标。原 106-record 测试仅保留为七类 taxonomy 的
合成单元覆盖；真实 Task 9 oracle 和 AC 追溯改用 366-fact fixture。

## Challenger

独立 challenger 确认该修订不降低质量门，并要求最终合并 HEAD 对同一 Root 再做一次只读复跑。
结论：`VERDICT: PASS`。
