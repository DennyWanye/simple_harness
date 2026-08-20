# S-5 冷启动与历史恢复

**绑定**: TO-A4 (AC-4), TO-A7 (AC-7)
**状态**: NOT_RUN（macOS 锁屏，待用户解锁后真人复测）

1. 使用隔离的全新 userdata 目录启动桌面应用，完成首次登录（不在截图/日志保存凭据），再进入主消息页；记录冷启动截图和日志。
   预期：首次登录后冷路径可进入功能页，服务初始化完成。
2. 分别执行一个成功、一个确定失败、一个用户取消的 Run，记录三个独立 root_run_id；每次保存终态截图。
   预期：执行记录分别显示 completed、failed/recovered、cancelled，不互相串线。
3. 退出并重新启动桌面应用，重新打开该 Session/Run；对三个 Run 各截图并检查 durable public projection。
   预期：三种终态均可恢复；不串到其他 Run，晚到事件不能将任何终态复活为 running。
4. 关闭 Inspector 后继续发送消息。
   预期：历史恢复不影响后续聊天。
