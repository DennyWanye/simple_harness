# Agent Activity Timeline Testcases

本组用例验证 Agent 执行记录的真实 UI 可达性、公开投影边界、历史恢复和异常降级。
原始截图、桌面日志和诊断文件只保存在 `.local-test-evidence/`，不提交 Git。

| 用例 | 绑定义务 | 方式 | 状态 |
|---|---|---|---|
| S-1 工具驱动任务 | TO-A1, TO-A2, TO-A7 | macOS computer-use 真人 UI | NOT_RUN（锁屏） |
| S-2 无工具问答 | TO-A5, TO-A6, TO-A7 | macOS computer-use 真人 UI | NOT_RUN（锁屏） |
| S-3 工具失败 | TO-A5, TO-A7 | macOS computer-use 真人 UI | NOT_RUN（锁屏） |
| S-4 长上下文 follow-up | TO-A3, TO-R2 | macOS computer-use + 出站 payload 脚本断言 | NOT_RUN（锁屏） |
| S-5 冷启动/历史恢复 | TO-A4, TO-A7 | macOS computer-use 真人 UI | NOT_RUN（锁屏） |
| S-6 malformed/replay | TO-A5, TO-R3 | deterministic fixture/script | PASS |
| S-7 契约与旧行为回归 | TO-R1, TO-A6 | contract + full-surface smoke | PASS |
