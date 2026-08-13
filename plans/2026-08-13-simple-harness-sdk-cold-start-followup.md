# Follow-up: Simple Harness SDK 消费端首次登录 cold-start

> 登记日期：2026-08-13  
> 来源：SR-9  
> 优先级：P2  
> 当前状态：DEFERRED / NOT RUN；不阻塞 `2026-08-13-simple-harness-sdk` release unit

## 为什么要测

该场景验证的是产品首次启动的初始化竞态，不是历史 Run 兼容。清除桌面应用开发数据后，登录态、
Provider、Tool catalog、Profile/Workflow catalog、control channel 和 SDK Runtime 可能异步就绪；若
顺序有缺陷，用户首次登录后的第一条请求可能错误显示 unavailable/degraded，只有暖重启后才恢复。

## 与当前 SDK release 的边界

- 当前 release 继续硬验收 clean wheel 安装、纯净 import、显式 `build/start/close`、schema v1
  首次创建与 close/reopen。
- 当前 release 继续在已有有效开发登录态下真实运行 Simple Harness SDK-S1..S5，并自动化运行
  SDK-S6..S7；不得用本 follow-up 跳过三个官方 Workflow 的真实消费验收。
- SDK 不拥有 onboarding/auth 或远程登录态；本次提取也不修改这些产品模块，因此产品级首次登录
  cold-start 单独排期。

## 后续验收场景 SDK-FU-CS1

1. 将截图、日志与状态快照写入 `.local-test-evidence/<date>/sdk-cold-start/`，不得提交原始证据。
2. 记录现有测试配置后，清除受控的桌面应用开发数据；不得删除源码、仓库或未核实的用户目录。
3. 全新启动唯一 Tauri-managed backend/vite，不预启动第二个 backend 或 Vite。
4. 完成首次登录；可由 Computer Use 在界面中粘贴 `LOCAL-DEV-CREDENTIALS.md` 的开发测试凭据，
   但不得在日志、截图、报告或 Git 中暴露凭据。若出现验证码、2FA、系统级授权或凭据不可用，
   保持 NOT RUN 并请用户接管，不得伪造 PASS。
5. 不重启应用，直接发送 SDK-S2 只读 Tool 请求，再执行一个官方 Workflow 请求。
6. 断言 Provider、Tool catalog、Profile catalog 和 `agent.general` 已就绪；请求完成且无
   cached unavailable/degraded、catalog generation 过期或“重启一次才恢复”。
7. 做一次暖重启对照，但暖重启结果不能替代第 5 步冷路径结果。

## 完成条件

- SDK-FU-CS1 真实 UI root run 达到 PASS，并有脱敏 primary evidence 索引与 SHA-256。
- 若发现初始化竞态，建立独立修复 plan；修复前本 follow-up 保持 OPEN/BLOCKED，不回写为已完成。
- 通过测试的同一次交付更新 `ARCHITECTURE/PROJECT_STATUS.md` 和相关认证/Provider 架构事实源。
