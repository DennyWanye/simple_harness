# Run 1 结果索引

日期：2026-08-20

- S-6：PASS。`agent_activity_timeline_smoke.py` 覆盖公开投影边界、`<think>` 移除、敏感字段剔除、详情截断、重复 identity 去重、终态冲突 fail-closed 和 ContextFragment 拒绝 excluded projection。
- S-7：PASS。后端 19 项 projection 测试、前端 9 个回归文件 70 项测试、TypeScript typecheck 和 production build 均通过。
- S-1 至 S-5：NOT_RUN。Computer Use 调用返回“Mac is locked and automatic unlock could not unlock it”；没有使用 DOM、WebSocket 或脚本回放替代真人 UI 证据。

原始日志保存在 gate run 的 ignored `verification/run-1/artifacts/` 路径；截图、桌面原始日志和凭据不提交 Git。
