# HM-TO-A6 第 10 次整跑（2026-09-09 10:25 起，Host 43a8f835 源码 + bundle 60ab03a1，Memory 0.6.37，flash **关闭 thinking**（`reasoning_mode = "fast"`），窗口 32000）

证据：`.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-*`。短旅程 ①②③④ 已分别验证 F-E3/W（组装超限归零）、T/F-S1b（A6-6 PASS）、V/0.6.37（A6-8/NC-4 PASS）；本次整跑作验收并覆盖 T16/T23/T24 面板步骤。

| 轮 | 结果 | 备注 |
|---|---|---|
| T1–T5 | COMPLETED | 关闭 thinking 后每轮 8–28 s |
| **T6** | **FAILED** | `tool_activate builtin:read_file` 被拒 `tool_unavailable / workspace_unscoped`，拒绝文案自相矛盾（让模型再去激活 read_file、不提 `workspace_prepare`）；模型随后 16 次 `tool_search` + 8 次 `context_page_in`，组装预算 `planned=28494 open_group=20574 full_trim=True` 抛出 → `react_termination_limits`。最后请求里 20 条助手消息 20.5 KB（工具调用回显，不可裁剪）、17 条分页摘要 9.6 KB、8 条省略通知 5.3 KB。→ **事件 Z**（拒绝文案给出可执行下一步；预算见底时注入一次「收尾作答」指令而非失败），已派子代理 |
| T7–T9 | COMPLETED | T8 读参照件 B 成功（33 s） |
| T10–T15 | COMPLETED | T15 分析批（v9）落地 `applies_to` 关系 |
| T16（UI） | 记录 | 图谱 **7 条记忆、1 条关系**（知识边首次在整跑 UI 出现） |
| **T17** | **FAILED** | `context_route` 参数无效（模型侧）后，线上门 `floor=26857 > 26752`（仅超 105）：关闭 thinking 时携带量回落到 `output_tokens`，把已在请求内的工具参数回显重复计数 → **W-b**（已派子代理） |
| **T18** | **FAILED** `recall_context_use_authority_stale` | 12 次调用（3 次 context_route、5 次 run_shell 读视图）后 SDK 驱动以 `RecallContextUseAuthorityStale` 失败：T17 的分析批在 T18 Run 内推进了召回权威 epoch，用途围栏按 0.6.29 逐来源复验后判 stale，但事件 F 的「重新收集」路径未接住 → **事件 AA**（待派：Run 内权威过期应重收集而非整 Run 失败） |
| T19 | COMPLETED | — |
