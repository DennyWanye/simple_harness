# Windows 真机验收结果

> 日期：2026-07-14
> 方式：Tauri 源码启动 + Windows Computer Use 真点击/真输入 + backend 日志
> 判定：**PARTIAL / BLOCKED**

## 启动环境

- Tauri 进程自行启动唯一 Vite 与 backend；未手动占用 8100/5173。
- 日志确认：`[backend_launch] Dev python=F:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=F:\projects\deskpet\backend`。
- 日志确认：`search_gateway_ready enabled=True providers=['baidu','duckduckgo','google-cdp','bing-cdp']`。
- 最新重启确认：`workflow_service_ready ... recovered_deliveries=0`；此前带遗留 outbox 的重启确认 `recovered_deliveries=12`。
- `tauri-dev-restart3` 曾因 5173 orphan Vite 冲突失败；清理该精确进程后 `restart4` 正常启动。这不是产品功能失败。

原始 dev 日志和隔离 userdata 留在本机证据目录，因可能包含会话元数据/本机路径，不纳入提交；本报告只摘录安全判定信号。

## 用例结果

| TC | 判定 | 结果 |
|---|---|---|
| W01 快速搜索 | PARTIAL | 真实坐标/输入链路曾触发 `web_search`，UI 返回 5 个真实官方来源，行为通过；本轮未把结果截图持久化到仓库，因此不满足完整证据 DoD |
| W02 逐步可视化 | BLOCKED | 首次真实 v2 run `0569642d9f1a4987913d711badc84b8e` 进入工作流，但暴露 completed delivery validator 与 async semantic scorer 两个问题；代码均已修复并有自动化。当前重测在 LLM 前被 relay `401 INVALID_TOKEN` 阻塞 |
| W03 重启恢复 | PARTIAL | 真重启观察到 `recovered_deliveries=12`；新测试覆盖 SessionDB 成功/websocket 失败及 orphaned `delivering` claim 恢复。无法在有效 LLM run 运行中完成 UI 前后截图闭环 |
| W04 最终交付 | BLOCKED | 当前无有效 LLM token，不能生成真实引用报告与 Markdown Artifact |
| W05 并发隔离 | BLOCKED | 当前无有效 LLM token，不能启动两个真实 run |

## 本轮真实 UI 检查

1. 获取 Desktop Pet 窗口状态，确认消息输入框、发送按钮、已连接状态和工具栏均可访问。
2. 真点击消息入口并重新抓取窗口状态；未使用 websocket 注入、DOM 注入或脚本回放替代 UI。
3. 未执行登录，也未读取、写入或变更任何凭据。

## 阻塞证据与处理

- backend 对新 LLM 请求返回 `401 INVALID_TOKEN`。
- 该问题属于当前登录会话/relay 外部状态；本计划未获授权自动填写登录凭据。
- 按验收纪律，W02/W04/W05 标 `BLOCKED`，W03 标 `PARTIAL`，不因自动化通过而改写成真机 PASS。

## 恢复测试的下一步

用户重新登录后，从 W01 开始保存动作前/动作后截图，并依次完成 W02～W05；运行中重启时继续使用 `DESKPET_BACKEND_DIR=F:\projects\deskpet\backend`，确认日志仍显示 Dev backend 而不是 bundled exe。
