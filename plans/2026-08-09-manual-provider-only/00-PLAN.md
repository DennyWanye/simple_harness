# 2026-08-09 登录方式改造：只保留手动 LLM Provider

> 用户决策（2026-08-09）：
> 1. 删除 relay 登录方式，**只允许手动输入 baseUrl + apiKey**，通过 baseUrl 处理
> 2. 老会话全部删除、不需迁移；**取消 `default` 会话概念**，所有会话都必须新建
> 3. relay 代码**删除**（不是断开接线）

---

## What（做什么）

把认证/provider 链路从「relay 托管账号 + 自动注入 relay-cloud endpoint」改为
「用户自填 provider（baseUrl + apiKey），身份走本地 profile」，并移除保留会话 `default`。

## Why（为什么）

- relay 登录墙 + token 生命周期是本轮两个真缺陷的共同根源
  （`WBUI-DEF-AUTH-01` backend 缓存过期 token 卡死、`WBUI-DEF-COMP-01` default 纪元遗留）
- `default` 保留会话是 COMP-01 的载体：它"永不退役、只清内容"的特殊性正是 bug 温床
- 手动 provider 链路**已完整存在**（添加/编辑表单、baseUrl 探测模型、后端 add/remove/probe 消息），
  本次是拆除叠加层而非新建能力

---

## 现状事实（已核实，非推断）

| 事实 | 位置 |
|---|---|
| auth 是 adapter 模式，`ManualAuthAdapter` 已是 OSS 默认 | `tauri-app/src/auth/index.ts:50-56` |
| relay 仅在 `--mode relay` 加载 `.env.relay` 时启用 | `tauri-app/.env.relay:9` |
| 手动 provider CRUD 已齐全 | `SettingsProviders.tsx`「+ 添加」`:904`、probe `:870` |
| 后端消息齐全 | `settings_providers_add/remove/probe_models/...` |
| key 不存明文，走 keychain 引用 | `config.toml` → `api_key_ref = "deskpet.provider.<id>"` |
| 无 relay endpoint 时身份自动落 local | `control_ingress.py:963` → `load_or_create_local_identity()` |
| `default` 保留会话四处特判 | `session_db.py:173/949/968/3672` |
| 前端 default 硬编码 | `App.tsx:53`、`SessionList.tsx:39`、`ChatView.tsx:92`、`sessionsStore.ts:1564/2482` |

---

## How（怎么做）

### 阶段 1：数据清场
- 删除 `.testenv/` 下测试 profile 的全部会话数据（用户已确认老对话不要）
- 保留 profile 目录结构与 provider 配置骨架

### 阶段 2：删除 relay（前端）
- 删除文件：`RelayAuthAdapter.ts` / `RelayAuthModal.tsx` / `RelayEdition.tsx` /
  `RelayApiError.ts` / `relayConfig.ts` / `relayErrorText.ts` / `relayProviderBridge.ts` /
  `relayProviderRegistration.ts` + 各自 `.test.*`
- `auth/index.ts`：`AuthEdition` 去掉 `"relay"`，`buildAdapter` 去掉 relay 分支
- `AccountSettingsPanel.tsx`：删除（账号概念随 relay 消失）；「更多」菜单去掉入口
- `SettingsProviders.tsx`：删除 `isRelayProvider` 特殊化——所有 provider 一视同仁
  可编辑 baseUrl + apiKey，去掉「重置 key」relay 专属路径
- onboarding：去掉登录步骤
- `.env.relay` / `.env.relay.local` / `package.json` 的 `dev:relay`/`build:relay` 移除
- `tauri.conf.json` 的 `beforeDevCommand` 改回 `npm run dev`

### 阶段 3：删除 relay（后端）
- 不再自动注入 `relay-cloud` endpoint
- 不再从 keychain 读 relay 专属 key（`get_cloud_api_key` 的 relay 路径）
- `control_ingress.py`：删除 relay 身份分支（`/v1/me` 调用），只保留 local
- `settings_providers_relay_logout` 消息移除

### 阶段 4：取消 default 会话
- backend：删除 `RESERVED_DEFAULT_SESSION_ID` 及四处特判；
  `bind_session_owner_if_absent` 里为 COMP-01 加的 default 自愈分支**一并删除**
  （载体没了，自愈无意义）；`clear` 对所有会话统一走"退役"语义
- 前端：`App.tsx` / `SessionList.tsx` / `ChatView.tsx` / `sessionsStore.ts`
  去掉 `DEFAULT_SESSION_ID` 常量与相关分支；`activeSid` 初值改为 `null`
- 会话列表为空时：ChatView 显示空态，引导「新建会话」
- 删除确认弹窗去掉「清空默认话题/清空」分支，统一「删除会话/删除」

### 阶段 5：验证
- `npx tsc --noEmit` + `npx vitest run` + `cargo test` + `cargo check`
- backend pytest（相关套件）
- 真机 E2E：全新 profile 冷启动 → 无登录墙 → 添加 provider（**用户自行输入 apiKey**）
  → 新建会话 → 真实往返 → 重启保持

---

## 验收标准

1. 全新 user-data 冷启动，**不出现任何登录窗/登录墙**
2. 设置页 Provider 区初始为空，文案引导添加第一个 provider
3. 「+ 添加」可填 baseUrl + apiKey；填完能从 baseUrl 探测到模型列表
4. 添加后能新建会话并完成一次**真实 LLM 往返**
5. 会话列表初始为空，**不存在名为 `default` 的会话**；所有会话均由用户新建
6. 删除会话时无「清空默认话题」这类特殊分支
7. 仓库内 `grep -ri relay tauri-app/src backend/deskpet` 只剩注释/历史说明，无活代码
8. tsc / vitest / cargo test / cargo check 全绿
9. 重启后 provider 配置与会话均保持

---

## 设计决策（用户 2026-08-09 已定：选 A）

**Q：会话列表为空时，用户直接在输入框打字发送，应该怎样？**

**A（已采纳）**：**自动新建一个会话再发送**。符合"所有会话都新建"——新建的是真会话
（uuid sid），不是保留的 `default`；现有空态文案「或直接在右侧输入框发消息」的承诺保留。

⇒ 落到实现：`activeSid` 初值为 `null`；发送时若 `activeSid == null` 则先走
`session_create` 拿到新 sid，再把消息投递到该 sid。**不得**复活任何固定 sid。

---

## 风险

| 风险 | 处理 |
|---|---|
| 删 relay 波及 companion 身份绑定 | local 路径已存在且被实测走通（`legacy_local_profile`），但需真机复验 |
| `default` 特判删除后既有会话绑定失效 | 用户已确认老会话全删，不做迁移 |
| relay 文件被非 auth 模块引用 | 删除前先 grep 反向引用，逐个断开 |
| 审计遗留项 r9 仍 FAIL | 本改造**不替代** r9 收尾；F1/F2 仍需单独处理，但 F3(AUTH-01) 预期随本改造消失 |
