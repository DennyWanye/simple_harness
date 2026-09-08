# HM-TO-A6 尝试 3 结果（2026-09-08 13:15–14:06，DeepSeek，窗口 override 32000）

> 非正式判定：本次运行途中修复了历史读取器（`933df61e`）并同 userdata 续跑，且 T7/T8/T10/T15/T22/T23 因下列 Host 缺陷或模型行为失败；正式判定待事件 A–J 修复合入后的第 4 次完整运行。证据：`.local-test-evidence/2026-09-08/native-a6-b3682fe1/{primary-ui-xmqudtzt,primary-ui-hv9k7ncq,merged-attempt3}`，核对脚本输出 `merged-attempt3/a6-verify.json`。

## 核对脚本判定（`scripts/native/a6_verify.py`）

| 项 | 判定 | 说明 |
|---|---|---|
| A6-1 20+ 轮动态组装 | PASS | 23 个 Run 全部终态，receipt ordinal 连续 |
| A6-2 大 tool result 分页 | BLOCKED | 模型全程用 `run_shell` 分块读文件（单条 <16 KiB），从未走 `read_file`/`context_page_in` |
| A6-3 预算内有界 | **FAIL** | 单 Run 内 25 次调用、29 条工具结果累计 64 KB，input_tokens 峰值 44378 > 预算 26752（事件 E） |
| A6-4 裁剪不破坏因果链 | PASS | 183 次请求无孤立 tool 消息，3 次裁剪均从头部整组丢弃 |
| A6-5 README/STATUS 超限拆分 | INCONCLUSIVE | 18 KB 消息前端发送无声丢弃（事件 G）；`goal.set`/`decision.record` 被 `nothing_to_close` 拒绝（事件 C） |
| A6-6 同 plan 建节点 + relation | INCONCLUSIVE | T15 提取成语义声明（值为"我前面说的 Python 环境"）而非 applies_to 关系；Run 因 `RECALL_AUTHORITY_STALE` 失败（事件 F） |
| A6-7 纠正 supersede | INCONCLUSIVE | T20 提案被 host-analysis-validator/v3 整体拒绝，原因不可见（事件 H） |
| A6-8 争议 contested | INCONCLUSIVE | T21 含糊说法被判无新事实（事件 H 裁决口径） |
| A6-9 / A6-10 | INCONCLUSIVE | 无关系行可遗忘；`memory_forget` 工具处理器异常 18 次（事件 J） |
| A6-11 图谱不入 Context | 脚本 FAIL，人工核为 PASS | 脚本把 T24 的"手动重发第 22 轮"18 次 provider 调用算进 UI 窗口；纯 UI 部分实测 T16 165→165、T24 165→165，关系 id/结构键命中 0 |
| A6-12 指纹重放 | INCONCLUSIVE（实质 PASS） | 183/183 重放全等；1 个 Run 行数不匹配为活库快照时序 |
| NC-1/2/3/6 | PASS | no_recall、闲聊不建 scope、模糊愿望不产生前瞻、无凭据形状 |
| NC-4 争议期不用旧值 | FAIL | 争议未建立，模型直接引用 3.12 |
| NC-5 UI 不新增调用 | 同 A6-11 | 纯 UI 部分 delta=0 |

另：T19 跨 15 轮召回 **通过**——请求中无第 2 轮原文（历史组 0），模型走 `context_route` 类型化召回，答出「外接硬盘 / 校对归档」并引用原话。

## 本次暴露的事件与处理

| 事件 | 现象 | 处理 |
|---|---|---|
| A | standalone 路由下调用 `run_shell` → `sdk_task_execution_route_authority_missing` 判整 Run 失败 | 子代理修复中 |
| B | 活动任务内模型反复 `task_scope_search`/空参数 `task_scope_update` | 子代理评估发现面文案 |
| C | `task_scope_update(goal.set/decision.record)` 被 `nothing_to_close` 拒绝 | 子代理裁决（require_dirty 口径） |
| D | 一次畸形工具参数 → `ProviderProtocolError` 判整 Run 失败 | 子代理实现一次重试 |
| E | 同 Run 内工具结果累积无上限 | 子代理实现 Run 内有界分页 |
| F | 并发分析写入使召回授权过期 → `RECALL_AUTHORITY_STALE` 判整 Run 失败 | 子代理实现重收集 |
| G | 18 KB 用户消息前端无声丢弃 | 子代理定位 |
| H | 明确纠正被校验器整体拒绝且原因不可见；含糊矛盾不成争议 | 子代理定位与裁决 |
| I | 类型化召回 5–6 次超时（3.4–4.8 s） | 子代理只诊断，出耗时分解 |
| J | `memory_forget` 工具处理器异常，公共文案"Tool execution failed." | 子代理修复 |

模型侧观察（DeepSeek）：空参数调用频繁（`{}`）；偏好 `run_shell` 而非 `read_file`/`context_page_in`；关系提取退化为字面值声明。第 4 次运行仍用 DeepSeek（luna 不稳），但以上 Host 缺陷修复后再评估是否需换模型验证 A6-6/7/8。
