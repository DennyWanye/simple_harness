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

### 阶段 4：取消 default 会话 ✅ 已完成
- backend：删除 `RESERVED_DEFAULT_SESSION_ID` 及四处特判；
  `bind_session_owner_if_absent` 里为 COMP-01 加的 default 自愈分支**一并删除**
  （载体没了，自愈无意义）；`clear` 对所有会话统一走"退役"语义；
  `_advance_reserved_session_scope` helper 随之删除（无调用方）
- 前端：`App.tsx` / `SessionList.tsx` / `ChatView.tsx` / `sessionsStore.ts`
  去掉 `DEFAULT_SESSION_ID` 常量与相关分支；`activeSid` 初值改为 `""`（空态）
- 会话列表为空时：ChatView 显示空态，引导「新建会话」
- 删除确认弹窗去掉「清空默认话题/清空」分支，统一「删除会话/删除」

**实现口径修正（相对原计划）**：

1. **空态发送不走 `session_create` 两步**，而是**一步** `chat_v2` 带
   `new_session: true` + `session_id: ""`。理由：后端 `_resolve_chat_task_scope`
   在 `explicit_new` 时本来就派 uuid 新 sid，随后回推 `session_switched`
   （侧栏据此切过去）+ `chat_v2_user_echo`（用户气泡落进新会话）。
   两步方案要多一次往返、还得自己处理"建好了但消息没发出去"的中间态。
   已实测：从未绑定的 base sid 继承 provider binding 是干净 no-op，不抛异常。
2. **`activeSid` 用 `""` 而非 `null`**：`SessionListProps.activeSid` / `ChatViewProps`
   全链路签名是 `string`，改 `null` 要动十几处类型；`""` 同样是明确的空态哨兵。
3. **删当前会话后落到剩下最近的一条**，一条不剩才进空态 —— 比直接进空态少一次
   用户动作，且不复活任何固定 sid。
4. **顺带清掉的 default 残留**（原计划未列）：
   - `topicDisplayLabel` 的 `isDefault` 参数与「默认话题」标签
   - `pet_focus_sid` 排除 `"default"` 的分支（桌宠聚焦评分）
   - `controlWs.ts` 重连回灌时硬拉一次 `"default"` 会话
   - `main.py` todo_write 的 `session_id_resolver=lambda: "default"`
     → 改 `None`，拿不到身份就明确报错，不写进幽灵会话
   - `identity.py` 的 `relay_human_identity` + `relay` 命名空间（阶段 3 后已无调用方）

**已知保留（不属于本次范围，属"同名不同物"）**：
`task_scope.py` 的 `_initial_peer_group` 把 `{"default", "message-panel-main"}`
映射到名为 `"default"` 的**广播组**，以及 `ControlChannel.ts` 身份桥接
socket 的 `session_id=default` **传输参数**。二者都不是会话，不建库行、
不进侧栏；仅是内部分组/连接注册键。改名有回归面而无行为收益。

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

⇒ 落到实现（阶段 4 实际口径）：`activeSid` 初值为 `""`；发送时若 `activeSid` 为空，
InputBar 发一条 `chat_v2 { session_id: "", new_session: true, text }`，由后端派
uuid 新 sid 并回推 `session_switched` + `chat_v2_user_echo`。**不得**复活任何固定 sid。
（原写的"先 `session_create` 再投递"两步方案改为一步，理由见阶段 4 的实现口径修正。）

---

## 风险

| 风险 | 处理 |
|---|---|
| 删 relay 波及 companion 身份绑定 | local 路径已存在且被实测走通（`legacy_local_profile`），但需真机复验 |
| `default` 特判删除后既有会话绑定失效 | 用户已确认老会话全删，不做迁移 |
| relay 文件被非 auth 模块引用 | 删除前先 grep 反向引用，逐个断开 |
| 审计遗留项 r9 仍 FAIL | 本改造**不替代** r9 收尾；F1/F2 仍需单独处理，但 F3(AUTH-01) 预期随本改造消失 |

---

## 阶段 5 验证结果（2026-08-09 真机）

九项验收 **全部通过**。证据在 `verification/artifacts/`（截图 p5-01…p5-17 + `phase5-cold.log`）。

| # | 验收项 | 结果 | 证据 |
|---|---|---|---|
| 1 | 冷启动无登录墙 | ✅ | p5-01：出现的是 onboarding 欢迎页，非登录窗 |
| 2 | Provider 区初始为空、引导添加 | ✅ | p5-06：onboarding 第 2 步即 baseUrl+model+key 表单 |
| 3 | 从 baseUrl 探测模型 | ✅ | `GET https://www.chinzy.com/v1/models 200 OK` |
| 4 | 真实 LLM 往返 | ✅ | `POST .../v1/chat/completions 200 OK`；p5-14 回复「手动provider通」 |
| 5 | 会话列表初始为空、无 `default` | ✅ | p5-08；空态直发新建 uuid 会话 `94d4012d-…` |
| 6 | 删除会话无「清空默认话题」分支 | ✅ | p5-15：弹窗为「删除会话」/「取消·删除」 |
| 7 | relay 只剩注释无活代码 | ✅ | 三处活代码已清（见下） |
| 8 | tsc / vitest / cargo 全绿 | ✅ | `tsc -b` 干净、553 passed、cargo check 0 error |
| 9 | 重启后 provider 与会话保持 | ✅ | p5-17：`registry ids=['primary']`、会话与删除均保持 |

### 真机抓到 5 个缺陷（单测全绿但真机崩），均已修 + 补非空洞回归

| # | 症状 | 根因 | 归属 |
|---|---|---|---|
| 1 | backend 起不来，React 树打爆 | ChatView `messages` 兜底写成 `?? []`，zustand 按引用比对 ⇒ 快照恒变 ⇒ 无限重渲 | 阶段 4 引入 |
| 2 | 输入框永久「正在恢复身份…」 | `ManualAuthAdapter` 恒返回占位用户 ⇒ 前端仍声明 `mode:"relay"` ⇒ 后端 local 权威判 `auth_snapshot_mismatch` | 阶段 3 漏改 |
| 3 | 聊天打到错误 provider（402） | 出厂 `config.toml` 仍带 `relay-cloud` endpoint（priority 1） | 阶段 3 漏删 |
| 4 | 空态直发 `UnboundLocalError` | `session_db` 只在 `session_provider_get/set` 分支赋值；空态不发 hydration ⇒ 该名从未绑定 | 既存潜伏，被阶段 4 空态引爆 |
| 5 | `no LLM provider configured` | onboarding 写 `llm_runtime.json`，聊天链路只认 provider registry，两条路不通 | relay 移除后断链 |

缺陷 5 的修法：启动时 `_seed_registry_from_runtime_overrides`（registry 为空才补，不覆盖用户多 provider 配置）
+ `/config/cloud` 写入时同步 upsert。`llm_runtime.json` 保留明文（用户决策：便于手改）。

### 过程中值得记的三件事

1. **`_recover_orphaned_endpoints` 让"全新 profile"从来不干净** —— 它会从默认 user data 目录
   （`~/Library/Application Support/deskpet`）把 endpoint 复制进测试 profile，删一次回来一次。
   真冷启动测试必须用 `DESKPET_CONFIG` 绕开（launch-cold.sh 已固化）。
2. **一条测试把 bug 钉死了** —— `companionIdentityBridge.test.ts` 断言 `mode:"relay"`，
   是 relay 时代写的；阶段 2/3 删 relay 时没动它，于是它一路绿着掩护缺陷 2。
3. **helper 插到了 `@app.post` 装饰器和处理函数之间** —— 路由套错函数，10 条既有测试转 422。
   baseline 逐条 diff 当场抓到；只看"有没有失败"会漏。

### 已知既存债务（非本次引入）

- `tests/test_p5s2_ipc_providers.py` 从第 4 个用例起全部挂死，须 `--ignore` 才能跑完整链；
  在改造前的 `729f981` 上同样挂（已开卡片跟踪）。
