# S-4 长上下文 follow-up

**绑定**: TO-A3 (AC-3), TO-R2 (context authority)
**状态**: NOT_RUN（macOS 锁屏，待用户解锁后真人复测）

1. 在同一 Session 完成至少 10 轮固定普通对话（短问候、事实问答、中文/英文混合各至少一轮），再执行一次 S-1 的工具任务；记录第一个 root_run_id。
   预期：第一个长上下文 Run 正确创建、执行和完成。
2. 独立发送第二次同义但不同措辞的工具任务，记录第二个 root_run_id；两次均保存截图、脱敏 backend 日志和 `.local-test-evidence/<date>/S-4/` 索引。
   预期：两个 root run identity 独立且均正确收尾。
3. 对第二个 Run 抓取真实 provider 出站 payload 与下一轮 follow-up payload（使用项目既有 payload capture 命令/日志，不读取 UI DOM；凭据、token、消息正文按证据纪律脱敏）。
   预期：payload 只包含既有模型协议消息和允许的 Session history；不含 `activity_items`、Inspector details、diagnostics、correlation 或 hidden reasoning。
4. 关闭并重新打开执行记录，再关闭 Inspector 继续聊天。
   预期：显示状态与 Run 一致，UI 投影没有被追加为新消息，面板关闭后 follow-up 仍可发送。
