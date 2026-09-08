# HM-TO-A6 尝试 6（2026-09-09 03:00，Host c9384422，Memory 0.6.31，deepseek-v4-flash，窗口 32000）— 第 5 轮中止

- T1–T4 全部 COMPLETED（flash 每轮 14–21 s）。
- T5「找能读本地文件的工具」：6 次 `tool_search` 后 Run 以 `sdk_context_budget_exceeded` 失败：`planned=28519 effective=26752 protected=9782 tool_schemas=7249 groups=2 ratio=2.01`；同轮 provider 实际 input_tokens 仅 19491。回执显示同 Run 分页只到 2 页、历史组未裁剪就直接失败关闭。
- 根因：事件 N 的校准对 flash 用了保守默认倍率 2.01（实测约 1.46），且预算超限时 Host 直接抛异常而非"先把可分页的全部分页、再裁历史组、只剩受保护部分仍超才失败"；受保护部分中工具 schema 占 7249 估算 token。
- 处理：中止本次；子代理在 `token-budget-reconcile` 工作树修复（flash 校准、降级顺序、受保护成本压缩、8192 档测试）；修复合入后做第 7 次。Manual 旅程与语料重测按用户"主流程优先"要求排在 A6 之后/并行。
- 附：Memory 0.6.32 pin 因语料重测批次占用 Host 树而延后（批次 worker 每例校验 pin）。
