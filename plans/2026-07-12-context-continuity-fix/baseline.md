# Baseline

- 工作树在本任务开始前已有大量用户修改；本任务只触碰 plan 中列出的窄范围文件。
- 已确认缺陷：`SessionDB.get_messages` 使用 ASC + LIMIT，长会话取得最早 N 条。
- 已确认风险：当前 user message 在 assemble 前落库，随后又被 `build_messages` 追加。
- 已确认风险：MemoryComponent 的 L1/L2/L3 共用一个组件超时边界。
- 已确认风险：chat/task wildcard 工具面包含 `generate_image`。
- 事件 session `c8648934-bb0a-409d-9966-3ca41f575c56` 没有图片 tool call、receipt 或产物路径。

