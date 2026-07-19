# DeepResearch 宽主题可靠性证据索引

最终事实以最后 renderer/quality gate 代码之后、同一 Tauri/backend 进程中的两轮原题为准：

- `consecutive-success-run1.json`：run `43e851a0d1264bcd8ecfe90f258c5afa` 工作流/delivery PASS。
- `report-quality-43e851a0d1264bcd8ecfe90f258c5afa.json`：5 项、5 引用、当前 17 项专业报告质量检查全 PASS。
- `consecutive-success-run2.json`：run `59975eb1a85a409e97e0277dcfe4a705` 工作流/delivery PASS。
- `report-quality-59975eb1a85a409e97e0277dcfe4a705.json`：5 项、5 引用、当前 17 项专业报告质量检查全 PASS。
- `success-tauri.stderr.log`：两轮最新 Tauri/backend 真实运行日志。
- `backend-full-postaudit-junit.xml`：最后代码后端全量 `4643 passed / 14 skipped / 9 deselected / 10 known failures`，失败集合未新增。
- `manual-failure-4e6d1b0b.json`：隔离失败 fail-closed 与安全 retry 证据。

`manual-success-66720bcf.json`、`old-report-quality-rejected.json`、`report-quality-37c53...`、`report-quality-f3e4...` 及其他更早文件只保留为实现迭代和失败基线，不能用作最终内容质量 PASS。
