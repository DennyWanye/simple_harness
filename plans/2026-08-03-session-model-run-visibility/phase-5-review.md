# Phase 5 收尾审查（2026-08-03）

## 幂等性审查

| 命中位置 | 重复副作用 / 已处理判断 / 持久化 / 失败重试 / 测试 | 结论 |
|---|---|---|
| `backend/deskpet/execution/provider_invocations.py` + `execution_provider_invocations` | invocation id、claim epoch 和 durable terminal outcome 共同去重；状态持久化到 `workflow.db`；未开始传输的失败可按新 attempt 重试，已开始/未知不会盲重发。`test_provider_dispatch.py` 和 `test_run_kernel.py` 覆盖重复 claim、传输前故障及恢复。 | 低风险，PASS |
| `backend/deskpet/execution/tool_completion_latch_script.py` + `execution_effects` | latch 只消费一次；effect id、handoff state、completion disposition 和 reconcile receipt 持久化；取消后的晚到结果只迁移一次且不回送 Driver。S-SRV-5 formal ledger 与 RunKernel/tool executor 回归覆盖重启、重复对账和失败重试边界。 | 低风险，PASS |
| `backend/deskpet/execution/provider_fault_script.py` | 规则在锁内按 occurrence 消费并绑定可信 child identity；重启会从规则文件得到新测试运行的规则状态，但正式产品未设置该 DEV env；失败 invocation durable audit 保留。S-SRV-3 证明 root 不命中、child 传输前命中一次。 | DEV-only，PASS |
| `tauri-app/src/stores/sessionsStore.ts` | 历史加载按 reconciliation key 一对一消耗 live 行，不以文本集合粗暴去重；真实重复消息、跨 Run 同文案仍保留。`sessionsStore.test.ts` 覆盖 live→durable 替换与 multiset 语义。 | 低风险，PASS |
| `ContextCompactedEvent` canonical 链 | `source_event_id` 作为 durable 去重键；入口把确切 measured sample ID 冻结进 durable request，producer 写入 `based_on_sample_id`，presenter 只按 Session + sample ID 精确读取，不按时间猜测。确定性结果 sample ID 写入后续 React 工具 boundary，重启后连续 compaction 不退回入口样本。同一压缩事件即使重放，也只归约为同一个 sample/RunEvent。 | 低风险，PASS |
| `project_directory_select(use_existing)` | 原生选择器返回的目录本身就是项目根；后端忽略模型建议子目录名，前端不显示该输入，恢复仍返回同一根目录。 | 低风险，PASS |

## 语义等价审查

- S-SRV-4 的 401 与 402 是同一 required scenario 的两个 retry run，不计作两个 distinct scenario。
- S-SRV-5 的 Session A/B 是一个并发场景中的两个独立 Root，不计作两个 distinct scenario。
- S-SRV-3 的早期复跑只作为历史证据；最终判定锚定正式 Root `573cf15ecffb561493b2590bcb785368`、child `child-b5d46585b00301f30c2761e3eb23c53e` 和 `s3-child-main-formal-ref`。
- 五个 required scenario 均来自 acceptance 冻结矩阵，没有把改写、continuation 或同题重跑冒充新增类别。

## 状态一致性审查

- `manual-test.md`：S-SRV-1～5 均已回写 PASS、真实 Root、终态和证据；当前有效 Root 为
  S-SRV-1 `32f9e2fe01f5568d9ca56ef0b17b4ada`、S-SRV-2
  `04999765753a5342aa9f7b4619b0fd38`、S-SRV-3
  `573cf15ecffb561493b2590bcb785368`、S-SRV-4-401
  `e6acd3abbe85519ca1f6736329bb3aa3`、S-SRV-4-402
  `11dac2d13ae954ddae8c3cde9b3658cf`、S-SRV-5 A/B
  `9789eee4324a57129c020b522cd9a33d` / `6300620adab9532fb14d5efe5e6834e1`。
- `testcase/index.md`：本组状态为 `FROZEN / EXECUTED PASS（2026-08-03）`。
- slice A/B gate 将在最终提交 HEAD 上重新初始化，避免沿用 final5 目录中旧 Root 与旧 testcase hash；
  只有新账本 audit/finalize 为 PASS 后才作为最终交付门禁。
- testcase 只发生结果回写，步骤与预期未改；两个 gate 均登记 `BC-RESULTS-007` 和新旧 hash。

结论：代码、真实 UI 与 DB 证据未发现重复副作用或语义重复计数；旧 gate receipt 已明确作废，
最终状态以提交 HEAD 上新生成的 gate run-dir 为准。
