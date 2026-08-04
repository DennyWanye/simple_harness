# DeepResearch v5 副作用幂等审查

> 审查状态：**PENDING — 已定义问题与必测断言，尚未对最终生产代码逐项签字**

本表按“重复调用、已处理判断、持久化、失败可重试、对应测试”五问审查。

| 场景/位置 | 重复副作用风险 | 已处理与持久化预期 | 失败重试预期 | 必测用例 | 当前 |
|---|---|---|---|---|---|
| `workflow_run_action` generate-now / cancel-settle | 重复决策、重复报告/Artifact | run-version/head/brief checkpoint CAS；decision 状态持久化 accepted→observed→settled→consumed | 未接受/未提交的失败不得被误标 consumed | TC-AUTO-NOW、TC-UI-NOW | PARTIAL：单次真实主链 PASS；重复/恢复分支 PENDING |
| continue-research child creation | 双 child、覆盖 parent report | parent head + snapshot/pin CAS；lineage immutable | child 创建失败可安全重试，不留下半个 child | TC-AUTO-CONTINUE、TC-UI-CONTINUE | PENDING |
| stage progress/bubble 投影 | 重连/重放刷屏 | stable key=`run:stage_instance`，stage_instance 来自 activation/invocation identity 并持久化 | failed event 可被后续合法重试实例区分 | TC-UI-RECONNECT | PENDING |
| terminal delivery/report/artifact 写入 | 多个终态或多个 Artifact | workflow.final 单 owner；business delivery cardinality=1 | insufficient 不产伪报告；提交前失败允许恢复 | TC-AUTO-NOW、TC-UI-NOW | PARTIAL：run `6ea6a3a4...` 唯一 insufficient 终态；重复/恢复分支 PENDING |
| rescue/repair loop counter 与 gap item | 重放重复计数、重复查询健康维度 | committed effect/item identity 去重；checkpoint 单 writer | 失败 work item 不标 completed，仍可重试 | TC-AUTO-RESCUE、TC-AUTO-PLATEAU | PENDING |
| LLM/Search/Browser durable effect | 崩溃后重复出站或重复计费 | opaque effect identity + EXECUTE/REUSE/UNCERTAIN、ledger reservation/settlement 持久化 | UNCERTAIN fail-closed；明确未执行可重试 | Gate F effect/recovery tests | PENDING |
| workflow.db schema/backfill | 重启重复迁移/重复写配置 | schema version + transaction；只补缺省值，显式 override 不改 | transaction rollback 后可重跑 | TC-AUTO-COMPAT、TC-INSTALL-03 | PENDING |
| snapshot retention/GC/reconcile | 重复删除、删除仍可达证据 | reachability + pin + dry-run/reconcile；删除标记持久化 | 未完成删除可 reconcile，不误删 parent/child 所需 blob | TC-AUTO-CONTINUE | PENDING |
| Playwright cancel/idle shutdown | 重复关闭、误杀无关浏览器 | context ownership 与 PID+create-time 精确树；close 可重入 | partial driver start 被取消时也清理；后续 render 可重建 | TC-AUTO-PW、TC-INSTALL-04 | PENDING |

## 签字出口

每行必须补最终 `file:line`、自动化测试名和执行证据后才能从 PENDING 改为 PASS。
任何一行回答不了五问都视为风险，必须补逻辑或测试，不能仅在报告中接受。
