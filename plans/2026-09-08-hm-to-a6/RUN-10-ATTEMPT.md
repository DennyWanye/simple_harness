# HM-TO-A6 第 10 次整跑（2026-09-09 10:25 起，Host 43a8f835 源码 + bundle 60ab03a1，Memory 0.6.37，flash **关闭 thinking**（`reasoning_mode = "fast"`），窗口 32000）

证据：`.local-test-evidence/2026-09-09/native-a6-run10/primary-ui-*`。短旅程 ①②③④ 已分别验证 F-E3/W（组装超限归零）、T/F-S1b（A6-6 PASS）、V/0.6.37（A6-8/NC-4 PASS）；本次整跑作验收并覆盖 T16/T23/T24 面板步骤。

| 轮 | 结果 | 备注 |
|---|---|---|
| T1–T5 | COMPLETED | 关闭 thinking 后每轮 8–28 s |
| **T6** | **FAILED** | `tool_activate builtin:read_file` 被拒 `tool_unavailable / workspace_unscoped`，拒绝文案自相矛盾（让模型再去激活 read_file、不提 `workspace_prepare`）；模型随后 16 次 `tool_search` + 8 次 `context_page_in`，组装预算 `planned=28494 open_group=20574 full_trim=True` 抛出 → `react_termination_limits`。最后请求里 20 条助手消息 20.5 KB（工具调用回显，不可裁剪）、17 条分页摘要 9.6 KB、8 条省略通知 5.3 KB。→ **事件 Z**（拒绝文案给出可执行下一步；预算见底时注入一次「收尾作答」指令而非失败），已派子代理 |
| T7–T9 | COMPLETED | T8 读参照件 B 成功（33 s） |
