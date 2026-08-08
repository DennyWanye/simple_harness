# r8 聊天链路堵点最终定位与解除（2026-08-08 08:46–08:50Z）

## 结论

**真凶不是 relay token，也不是钥匙串**——是验证员自己在 Browser 面板留下的
`localhost:5173` 标签页（前端跑在纯浏览器里，无 Tauri 环境）。

## 机制

1. 浏览器里的前端拿不到 Tauri 注入的共享密钥 → 以 `secret=dev` 连
   `/ws/control`，且无法调 `get_window_control_credential`（Tauri command）
   → 发出的 `companion_profile_bind` 无 credential 字段。
2. 后端 `control_ingress.py:1104` 拒之：`window_credential_required`，
   且 `rechallenge=True` → 触发重挑战，**把真 Tauri 窗口的控制连接一起踢掉**。
3. 浏览器端立即重连重试 → 无限循环。日志累计 **50097 条
   companion_control_rejected**（近 200 条里 199 条 window_credential_required），
   `/ws/control` 上 `secret=dev` 与真实密钥连接交替出现。
4. 副作用即此前观察到的症状：工具结果帧丢失（excel 生成了但产物卡片不渲染）、
   发送窗口抖动、"Cannot call send once a close message has been sent"。

## 解除动作与证据

- 08:46:30Z 关闭 Browser 面板 tab-1（localhost:5173）。
- **最后一条拒绝 timestamp=2026-08-08T08:46:30.803550Z**，其后零新增
  （持续观测 >3 分钟）；最后的控制连接为真实密钥连接，正常 accepted。
- 08:50:28Z 真机 Tauri 应用内点击发送探针「风暴修复探针：请回复两个字——通了」
  → run_id=**81073b07e49359fdbfe7dc7562d0d982**（react_loop
  harness_provider_scope_pinned/unpinned 成对出现）→ 回包「通了」气泡渲染。
  截图：probe-after3-win.png（发出）、probe-after4-win.png（回包）。

## 修正此前误诊

- 「等 macOS 钥匙串授权」：错，本链路不弹钥匙串。
- 「必须 relay 重新登录才能续跑」：错，本地身份模式聊天链路完整可用。
  relay token 过期（/v1/me 401）真实存在但只影响 relay 登录态，不阻塞 r8。

## 教训

浏览器道辅助诊断结束后必须**立即关闭指向 dev 端口的标签页**——
单控制租约语义下，一个假客户端能把真客户端踢成永久抖动。
