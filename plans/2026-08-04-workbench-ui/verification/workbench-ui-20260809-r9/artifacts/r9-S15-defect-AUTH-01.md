# WBUI-DEF-AUTH-01 —— backend 缓存过期 access_token，未绑定 profile 永久卡「正在恢复身份…」

发现于：r9 / WBUI-S15-empty-states 步骤1 后（2026-08-09）
严重度：中高 —— 触发时该 profile 的 companion 身份永远不 ready，聊天不可用，
UI 无任何错误提示或恢复入口；但已绑定过的 profile（如 cold-A）不受影响。

## 触发条件
keyring 里的 relay access_token 过期（本例约 1 小时后），且该 user-data 目录的
companion profile 尚未完成首次绑定（全新目录 / 从未聊过天的目录）。

## 实测证据链
1. r9-s01-cold.log：companion_profile_bind 每 30s 被拒
   code=trusted_relay_identity_unavailable，GET /v1/me → 401 ×11
2. 同一时段 cold-A（已绑定）无影响；1 小时前 cold-A 日志里 /v1/me → 200
3. 打开账户面板 → 前端 RelayAuthAdapter 401→refresh→写回 keyring（面板拉到实时
   余额数据 = 前端链路通）→ backend **继续 401**（不重读 keyring）
4. 重启应用 → backend 重读 keyring 拿到新 token → /v1/me 200 OK → 身份就绪

## 根因
backend/deskpet/companion/control_ingress.py:992 一带：`_access_token_provider()`
提供的 token 在 backend 进程生命周期内不随 keyring 更新；401 时既不触发 refresh
也不重读，异常统一吞成 trusted_relay_identity_unavailable 无限重试。

## 修复方向（建议，未实施）
- 401 时让 `_access_token_provider()` 重读 keyring 一次再试（前端可能已刷新）；
- 仍 401 则通过控制通道通知前端触发 refresh（前端已有完整 401→refresh 实现）；
- 超过 N 次失败后在 UI 呈现「登录已过期，请重新登录」而非永久「正在恢复身份…」。

## 与 WBUI-DEF-COMP-01 的关系
独立缺陷。COMP-01 是 default 会话 owner 纪元遗留（绑定成功后的会话级问题）；
本缺陷是绑定本身完成不了（token 生命周期问题）。
