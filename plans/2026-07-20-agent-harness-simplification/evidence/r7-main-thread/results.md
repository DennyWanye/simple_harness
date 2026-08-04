# R7 主消息线程真人验收结果

> 日期：2026-07-22
> 范围：桌宠 → 点击“消息” → 主消息页主线程。Code 工作台的多任务并行专项不在本轮范围内。
> 结论：**S-1～S-7 全部 PASS。**
>
> 逐步点击动作和预期见 [R7 主消息线程真人测试用例](../../../../testcase/2026-07-22-harness-main-thread-r7/manual-test.md)。

| 场景 | 结论 | 真实 UI 证据 | 关键事实 |
|---|---|---|---|
| S-1 普通只读解释 | PASS | [最终结果](./screenshots/s1-final-same-session-pass.png) | 真实读取工作区文件，只读路径未产生写 Effect，回复准确解释测试失败原因。 |
| S-2 同线程追问 | PASS | [第一次回答](./screenshots/s2a-final-same-session-pass.png)、[同线程追问](./screenshots/s2b-final-same-session-pass.png) | 同一主线程理解“它”指 Harness/Kernel，前后 turn 独立且上下文连续。 |
| S-3 DeepResearch | PASS | [实时进度](./screenshots/s3-a5-live-progress.png)、[最终产物卡](./screenshots/s3-a5-final-artifact-card.png) | durable workflow 有真实进度，最终报告、Artifact 与 terminal 唯一。 |
| S-4 PPT 审批 | PASS | [等待审批](./screenshots/s4-a2-outline-awaiting-approval.png)、[同一任务获批](./screenshots/s4-a2-approved-same-run.png)、[最终 PPT](./screenshots/s4-a2-final-ppt-artifact.png) | 大纲确认经前端 → backend → Kernel 的 durable decision fence 恢复同一 run，确认后才生成文件。 |
| S-5 重启恢复 | PASS | [停止前](./screenshots/s5-pre-stop-run-347edd1a.png)、[恢复同一任务](./screenshots/s5-resumed-same-run-347edd1a.png)、[恢复后终态产物](./screenshots/s5-recovered-terminal-artifact.png) | 运行中停止应用并重启后恢复同一 run，最终只有一个 terminal 和一个 Artifact。 |
| S-6 停止与后续隔离 | PASS | [忙时禁止重复提交](./screenshots/s6-main-thread-input-blocked.jpg)、[取消后正常追问](./screenshots/s6-cancel-then-followup-normal.jpg) | 主线程忙时第二次提交被 UI 阻止；取消完成后新短问题正常执行，active root 始终不超过 1。 |
| S-7 工具失败诚实投影 | PASS | [成功 receipt](./screenshots/s7-file-read-success-receipt.jpg)、[失败 receipt](./screenshots/s7-file-read-failure-receipt.jpg) | 当前成功 receipt 正确显示；下一次缺失文件读取显示失败，未复用旧成功 receipt，也未显示绿色成功。 |

## 自动化与发布门

- Harness：`512 passed, 4 xfailed`。
- Workflow：`703 passed`。
- 完整 backend：`5736 passed, 19 skipped, 9 deselected, 4 xfailed`。
- 前端：TypeScript、Vite production build、完整 Vitest 均通过。
- Rust：`cargo test` 73 passed，`cargo check` 通过。
- parity census：141/141，unmapped=0；authority audit：DML/run map/supervisor/presenter 均为唯一 owner。
- 最终 R6 复杂度门：raw/adjusted/core/Kernel=`33,633/33,124/5,948/896`，Kernel 公开操作=6，unknown=0。
- 最终 G7 性能门：`r7-benchmark.json` comparison 11/11 PASS；chat local-start p95=0.323ms，controlled TTFT p95=16.0327ms（对 R0 +0.11%），event-loop p99=15ms，20-session throughput=21,892.537 runs/s（对 R0 -6.35%），workflow=3 transactions/505 bytes；100 个 token delta 的 execution SQLite writes=0；128 个 active run 的 metadata=7,512 bytes/run（保守包含每 run 1-byte token payload）；10,000 个真实 Kernel/UoW 生命周期的 terminal rows/events/closes 均为 10,000，completed-run strong refs=0，RSS delta=0，RSS-after 对 R0 -0.38%。
- last-mile：7/7 checks PASS，`DECISION: SHIP`；报告位于 `plans/2026-05-23-tool-last-mile-upgrade/manual-results-2026-07-22T155135Z/acceptance.json`。

## 边界与清理

- Code 模式复用相同单任务语义；后续只需专项验证多任务并行、任务隔离、单独取消和结果汇聚。
- 中间 NO-SHIP 运行分别暴露 Node 路径/超时配置与 Playwright idle test 时序问题；配置和 flaky 测试均已修复，最终严格重跑 SHIP。
- 真机 Tauri 树清理 20 个精确匹配进程，释放约 9535.73 MB；两次中间 last-mile 清理各 4 个精确匹配进程，释放约 1540.08 MB 与 1560.92 MB。最终发布门后未发现匹配残留。
