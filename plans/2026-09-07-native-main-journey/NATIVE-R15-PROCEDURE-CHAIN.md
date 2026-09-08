# 原生 r15：Procedure 真实使用链（Host main 188394b1，M0.6.26，DeepSeek，窗口 override 32000）

日期：2026-09-08 14:09–14:20；证据：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-08/native-r15-188394b1/primary-ui-eth0v9u9`；工作区 `/Users/taiwan/SimpleHarnessWorkSpace/task-7d406974-8595-5760-bc09-8b2d7cce4886`。
前置修复：`bcd3bb15`（绑定响应回显 bound_steps + next_action、可操作拒绝文案、步骤全部成功后允许普通工具调用、预派发拒绝不再使整组终态不可表示）。驱动：`scripts/native/r15_driver.sh`（发送确认 + Run 终态 + 等待异步分析落库）。

| 步 | 结果 |
|---|---|
| 1 | ✅「松柏记录」落库 ACTIVE（100 s，含异步分析） |
| 2 | ✅「云杉归档」draft（82 s） |
| 3 | ✅ 普通改写，无召回 |
| 4 | ✅ `procedure_discover` 返回草稿，未执行 |
| 5 | **执行链 ✅ / 核对阶段 ❌（模型侧）**：`context_route(create_new)` → `tool_search/describe/activate`（write_file、workspace_prepare）→ `workspace_prepare` → `procedure_use` 绑定 1 次 → **两步 `write_file` 逐字按绑定参数执行**（`procedure_use_effects` 步 1、2 均 succeeded）→ 工作区出现 `record.txt` 与 `backup.txt`，内容完全一致（"2026-09-08 松柏九月任务记录"，各 35 字节）→ 随后模型为读回文件调用 `tool_search` 时反复发出空参数 `{}`（本 Run 29 次工具调用中 13 次空参数：tool_search 6、write_file 4、tool_activate 2、workspace_prepare 1）→ `react_max_turns_exceeded` |

## 判定

- HM-AC-5 Procedure 真实使用链的 Host 侧全部通过：发现 → 绑定冻结 → 逐字有序执行 → 效果落审计 → 绑定完成后允许后续普通调用（r14 裁决一生效：tool_search 在步骤完成后成功 6 次，未被 `procedure_use_already_complete` 拒绝）。
- 未闭合的只剩"做完读出来核对"这一句：DeepSeek 在多轮工具调用后发出空参数调用的倾向（r14 亦 9 次）使 Run 在核对前耗尽轮次。这是 provider 侧行为；Host 已把每次拒绝文案改为可操作。下一步用 luna（若稳定）或加 Host 侧"空参数连续 N 次即回复用户"的止损策略（记入 followup F09）。
- 与 r14 对比：r14 绑定后 0 步执行；r15 两步执行并落盘。
