// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// chat 车道画廊夹具：Markdown 文本等固定数据（不连后端）。
// 画廊固定"现在" = 2026-10-01T08:00:00Z（NOW）。
export const NOW = 1790841600000;

// ── Markdown 文本（表格/列表/代码块/链接/行内代码） ─────────────────
export const MARKDOWN_RICH = [
  "## 调研结论",
  "",
  "这是**加粗**与`行内代码`，还有 [官网链接](https://example.com/docs) 和 [本地报告](/Users/demo/report.pdf)。",
  "",
  "- 第一条要点",
  "- 第二条要点",
  "",
  "1. 有序一",
  "2. 有序二",
  "",
  "| 指标 | 数值 |",
  "| --- | --- |",
  "| 命中 | 12 |",
  "| 超时 | 1 |",
  "",
  "```python",
  "def hello(name):",
  '    return f"你好 {name}"',
  "```",
  "",
  "```",
  "无语言代码块",
  "```",
].join("\n");

