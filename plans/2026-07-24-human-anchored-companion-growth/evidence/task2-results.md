# Task 2 验证结果：稳定人类身份与默认开启配置

日期：2026-07-25（Asia/Shanghai）

## 自动化

- Python 全量相关组合：`98 passed`。
- 最终改动后聚焦复跑：
  - `backend/tests/companion/test_profiles.py`
  - `backend/tests/companion/test_window_control_credentials.py`
  - 结果：`17 passed`。
- 前端最终聚焦：
  - `src/auth/companionIdentityBridge.test.ts`
  - `src/auth/windowControlCredential.test.ts`
  - `src/code-panel/InputBar.chat.test.tsx`
  - 结果：`14 passed`，随后 `tsc -b` 通过。
- Rust 全套：`78 passed`。其中既有 loopback proxy 用例首次出现一次时序性失败，单例复跑及
  全套复跑均通过；Task 2 signer/canonical/permission 相关用例无失败。

## 真实 Tauri 主消息页

使用当前 checkout 源码启动，日志明确出现：

- `[backend_launch] Dev python=... backend_dir=F:\projects\deskpet\backend`
- `growth_authority_ready phase=legacy generation=1`
- Relay `/v1/me` 返回 HTTP 200。

为了不修改用户目录中残留的旧 durable run，最终验收使用精确隔离的
`F:\projects\deskpet\.tmp\task2-userdata`。真实 Rust/Tauri/WebView/backend 链路得到：

- `main / identity_bind`：active，`last_seq=1`，首命令
  `companion_profile_bind`；
- `message-panel / companion_action`：active，`last_seq=1`，首命令
  `companion_action_ready`；
- 两条 lease 的 connection id、control epoch、challenge 和 seq 独立；
- profile binding 为 `ready`，generation=1，binding epoch=1；
- 主消息页真实 UI 输入框从身份恢复门恢复为“和桌宠说点什么…”，未出现
  `companion_control_rejected` 重试风暴。

此前同一当前源码链路已在主消息页真实点击输入并发送
“请只回复：身份通道测试通过”，backend 完成真实 provider 调用并记录
`chat_v2_final_send_completed`，最终正文 8 字节。

## 进程清理

- 旧 Tauri 树：18 个精确 PID，清理前 private memory 9171.2 MiB，`survivor=0`。
- 重试 Tauri 树：12 个精确 PID，清理前 private memory 1172.2 MiB，`survivor=0`。
- 最终隔离 Tauri 树：17 个精确 PID，清理前 private memory 9943.5 MiB，
  `survivor=0`。
- 当前源码 Vite 树：2 个精确 PID，清理前 private memory 295.7 MiB，
  `survivor=0`。
- 清理后 8100/5173 无监听者。

以上清理均按 root PID、父子关系、创建时间、命令行与配置路径枚举后执行，没有按进程名广杀。
