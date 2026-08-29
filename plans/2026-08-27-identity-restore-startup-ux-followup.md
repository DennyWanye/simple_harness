# 启动身份恢复 UI 体验优化 follow-up

> 状态：待排期
> 优先级：P2
> 范围：启动阶段消息输入区与 Companion identity-ready 链路的用户体验；不删除身份一致性门禁。

## 问题

应用启动时，消息输入框会先被禁用并显示“正在恢复身份…”。当前身份已经是零网络 I/O 的本地
profile，这个文案容易让用户误以为系统正在重新登录、访问远端账户，且每次启动可感知的等待会造成
“为什么总要恢复身份”的困惑。

## 优化目标

1. 保留本地 profile、Session owner、Companion Runtime 和窗口签名凭据的一致性校验。
2. 将正常的短暂初始化做成低打扰过渡；不要把正常冷启动描述成“恢复身份”。
3. 只有超过明确时延阈值后才展示可感知状态，并使用准确文案，例如“正在准备会话…”。
4. 超时或失败时不能永久禁用输入框而没有解释；应展示结构化原因、重试入口和诊断关联信息。
5. 测量并拆分 backend ready、control WebSocket、challenge/sign、profile bind、identity-ready 各阶段耗时，
   先以证据判断应并行化、缓存还是仅调整视觉反馈。

## 验收边界

- 暖启动和冷启动分别记录阶段耗时与 UI 可感知等待时间。
- 正常快速路径不出现“正在恢复身份…”，输入框在门禁开放后立即可用。
- 慢路径展示准确、非账户化的初始化提示；失败路径可重试且不会无限等待。
- React 重挂载、Vite HMR、WebSocket 重连及 backend 重启仍保持 fail-closed，不因体验优化绕过
  `IdentityReadyGate`。
- 自动化覆盖状态延迟显示、ready、超时、失败与重试；最终通过当前平台真实 UI 冷启动/重连点击验收。

## 当前代码锚点

- `tauri-app/src/views/ChatView.tsx`：`companionIdentityReady` 控制输入框禁用和占位文案。
- `backend/deskpet/companion/control_ingress.py`：本地身份快照、challenge 与可信绑定入口。
- `backend/deskpet/companion/identity_gate.py`：身份就绪门禁。
- `ARCHITECTURE/COMPANION_GROWTH.md`：身份状态重放、durable owner 与 Runtime 边界事实。
