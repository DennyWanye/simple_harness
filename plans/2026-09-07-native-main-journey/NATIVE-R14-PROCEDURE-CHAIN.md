# 原生 r14：Procedure 真实使用链（DeepSeek，Host main 7cec5249，M0.6.26）

日期：2026-09-08 10:29–10:38（解锁屏幕、真实桌面 UI）
Provider：`deepseek-v4-pro`（luna 在 r13 连续 502，按用户决定切换；`--env-file` 记录 provider.kind=explicit）
证据：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-7cec5249/primary-ui-ah1zxqx1`（首进程）、`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-7cec5249/primary-ui-qcewdx7j`（重启进程，native.log 含步 5）；bundle `com.dennywanye.simpleharness.verify09077cec5249p18120`

## 结果

| 步 | 内容 | 结果 |
|---|---|---|
| 1 | 存"松柏记录"流程（两步：写 record.txt、再写 backup.txt） | ✅ ACTIVE / UNBOUND |
| 2 | 存"云杉归档"草稿 | ✅ draft |
| 3 | 只查草稿状态 | ✅ `procedure_discover` 返回 draft，未执行 |
| 4 | 发现已采用流程 | ✅ 命中 ACTIVE"松柏记录" |
| 5 | 新建本地任务"松柏九月"，按流程执行并核对两文件 | ❌ Run `failed`，终因 `react_max_turns_exceeded`；工作区目录为空 |

步 5 观测（execution-v6 `provider_invocations` 25 次、audit `procedure_use` 66 条）：

1. `context_route` create_new → TaskScope `6735f3da…`；`tool_search` → `tool_activate`（file_write、workspace_prepare 激活成功；其间 1 次 `catalog_describe_nonce_invalid`、1 次空参数）→ `workspace_prepare` 返回 `/Users/taiwan/SimpleHarnessWorkSpace/task-6735f3da-…`。
2. `procedure_use` 绑定成功（procedure_uses=1）：步骤参数 `{"path":"record.txt","content":"2026-09-08 记录\\n按「松柏记录」流程执行。"}`；返回 `execution_authorized:false`。
3. 模型随即调用 `file_write` 时改了内容（`"2026-09-08 松柏九月·当日记录"`）→ 两次 `procedure_call_not_bound_step`（参数哈希与绑定步不一致，公共文案仅"Procedure use was rejected before execution."，未说明期望的步/工具/参数）。
4. 模型自述"my file_write calls weren't matching the bound steps, let me re-bind"→ 再次 `procedure_use`（新参数）→ 2 次 `procedure_same_run_changed_use`（同 Run 绑定不可变，符合 S3 Task 3 契约）。
5. 其间 DeepSeek 发出 **9 次空参数 `{}` 工具调用**（procedure_use ×7、file_write ×2、tool_activate ×1 → `missing_required_argument`），最终耗尽 react 轮次预算。

## 判定

- 契约层正确：绑定即冻结、步调用必须逐字匹配、同 Run 不可换绑，全部按设计拒绝，无越权执行；审计完整。
- 产品链 **FAIL**，根因为 **模型侧可执行性**：(a) 绑定响应没有回显"接下来必须逐字调用的步"，且 `execution_authorized:false` 易被读成"未授权"；(b) `procedure_call_not_bound_step` / `procedure_same_run_changed_use` 的公共文案不可操作；(c) DeepSeek 在长工具描述 + 多轮失败后倾向发空参数调用（模型行为，Host 只能通过更短的纠错文案降低概率）。
- 处置：Host 修复 (a)(b)（回显 bound_steps + 明确 next_action、可操作的拒绝文案），不放宽契约；修后 r15 重跑。裁决见 `DECISION-PROCEDURE-USE-CHAIN.md` 追加节。
