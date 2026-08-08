# WBUI-DEF-COMP-01 —— default 会话 owner 绑定停在旧 profile 纪元，消息永久被拒

发现于：r9 / WBUI-S18-session-delete-edge 步骤2（2026-08-09）
严重度：中高 —— default 会话永久不可用（含跨重启），UI 无恢复入口；
但仅影响 default 一个会话，其余会话与新建会话正常。

## 现象
向 default 会话发任何消息 → 错误卡片：
  companion_identity_not_ready — companion_session_owner_rebind_forbidden
重发同败；重启后同败（r9-c-hot.log 2 次 + r9-c-hot2.log 1 次
companion_chat_blocked_identity_not_ready session_id='default'）。

## 根因证据（state.db companion_session_owners）
  default 行:  binding_epoch=1  status=active  scope_version=4  updated_at=r8 时段
  当前新会话:  binding_epoch=3
  中间记录:    8edd2ffa… legacy_local_profile epoch=2（S08 步骤7 基线试跑时段）
companion profile 纪元迁移 1→2(legacy)→3(relay 重绑) 时，普通会话走"新建即新纪元"，
而 default 是常驻会话、owner 行早已存在且 status=active——迁移逻辑没有把它推进到新纪元。
之后任何消息进 default 触发 owner rebind，被 rebind_forbidden 拒绝。

## 与 S18 删除链路的关系
无因果。删除"当前打开的会话"后 UI fallback 到 default，才第一次向 default 发消息使其显形。
切到任何其他会话立即正常（实测 C 会话真往返成功）。

## 复现
1. 让 companion profile 发生纪元迁移（如登录态 legacy→relay 切换各一次）
2. 向 default 会话发一条消息 → 必现被拒

## 修复方向（建议，未实施）
- 纪元迁移时把 status=active 的既有 owner 行（尤其 default）一并迁移/重签到新纪元；或
- rebind_forbidden 对 default 这类系统常驻会话放行一次 rebind（带审计日志）；或
- 消息路径检测到 owner 纪元落后时自动做一次安全重绑而非硬拒。
修复涉及 companion 身份/纪元机制核心，未混入本轮任何已提交修复。
