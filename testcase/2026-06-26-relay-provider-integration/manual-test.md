# relay 本地 apikey + provider 收编 集成真测（C+D+E）— windows-mcp 手测文档

> **被测 plan**: [`plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md`](../../plans/2026-06-25-relay-local-apikey-provider/00-PLAN.md)（WI-B/WI-3/WI-4/WI-5 **集成**：device key 复用三态 → registration 镜像 → 设置面板受限三态 → 结构化错误）
> **中转站权威契约**: [`02-relay-handoff-device-key-reuse.md`](../../plans/2026-06-25-relay-local-apikey-provider/02-relay-handoff-device-key-reuse.md)（device key 复用三态 / `DEVICE_KEY_REUSE_ENABLED` flag / `INSUFFICIENT_BALANCE` code / 测试账号）
> **关联前序手测**: Phase A（WI-C 账户面板 ¥→USD）`testcase/2026-06-26-relay-provider-phaseA-wiC/` · Phase B（WI-1+WI-2 后端 registry 地基）`testcase/2026-06-26-relay-provider-phaseB-registry/`
> **本文档定位**: Phase A/B 各只测了**单 WI 切片**；本文档测**集成主干**——relay 登录后**自动收编进 `LLMProviderRegistry` 作为可统一管理的 provider，聊天经它走 chinzy**（用户核心需求）。
> **最后更新**: 2026-06-26
> **执行状态**: Lead 已于 2026-06-26 windows-mcp 真机做过 TC-1/TC-2/TC-3（见 §3 结果表 ✅ PASS + 证据），env-limited 项见 §4。

---

## 0. 被测范围 / 目的 / 通过判据

### 0.1 被测范围（集成 C+D+E）
relay 登录态下，端到端串起以下四条改动链路（核对源码锚点）：

| 链路 | 关键实现锚点（已核对源码） |
|---|---|
| **收编（C）** | 前端 `tauri-app/src/auth/relayProviderRegistration.ts`（`ensureOnce` :71 → `ch.send({type:"settings_providers_ensure", payload:{id:"relay-cloud", source:"relay", account_ref, name:"中转站 · chinzy", api_key:<device key>}})` :103-115）→ 后端 `backend/main.py:5940` `await ensure_relay_provider(_reg, _payload)` → `backend/llm/relay_provider_ops.py:38 ensure_relay_provider`（只收 `source=="relay"`、日志 `relay_provider_ensured ... key_fp=` :64 只打 sha256[:8]）→ registry 写出 `config.toml [[llm.endpoints]] id="relay-cloud" source="relay" account_ref=...` |
| **UI 受限三态（D）** | `tauri-app/src/components/SettingsProviders.tsx`：`isRelayProvider`（:93 判 `source==="relay"`）→ relay 行 = 蓝徽章 `provider-relay-badge`（:376）+ default_model select + 启用 checkbox + 可拖拽 + 「🔄 重置 key」`provider-reset-key-btn-relay-cloud`（:446，替代删除按钮）。入口：Toolbar 齿轮 `settings-toggle`（`Toolbar.tsx:130`）→ 设置面板「LLM Providers」 |
| **聊天路由（E）** | `resolve_provider_for_session` → registry `get_chain()` → `relay-cloud` → `https://chinzy.com/v1/chat/completions`。后端日志硬证：`p5s2_chain_resolved ... n_entries=1 models=['gpt-5.5']` + `httpx ... POST https://chinzy.com/v1/chat/completions "HTTP/1.1 200 OK"` |
| **结构化错误（WI-5）** | `backend/llm/relay_errors.py classify_relay_error` → `errors.py LLMAuthError(error_class=...)` → 前端 `relayErrorText.ts`（:24 `insufficient_balance`→「账号余额不足，请充值」/ :26 `relay_key_invalid`→重签）。403 `INSUFFICIENT_BALANCE` / 402 嵌套 `error.code` / 401 `INVALID_TOKEN`·`EXPIRED_TOKEN` |
| **门控 flag** | 前端 `relayConfig.ts:34 RELAY_MANAGED_PROVIDER = true` · 后端 `config.py:484 relay_managed_provider: bool = True`（relay edition 默认 ON；manual inert，字节级 BC） |

### 0.2 目的
验证用户核心需求**真在桌宠 UI 链路成立**：「relay 登录 → 自动出现一个可统一管理的 provider（与手填同列、可启停/排序/选默认模型/重置 key）→ 聊天确实走它打到 chinzy → 真回复」。不是只跑单测、也不是 WS 直注/`import` 查 registry。

### 0.3 通过判据
- ★ **TC-1**：登录后 relay-cloud **自动收编**进 registry —— `config.toml` 出现 `id="relay-cloud" source="relay" account_ref=...` + 后端日志 `relay_provider_ensured ... key_fp=`（非手填、非旁路）。
- ★ **TC-2**：设置面板 relay-cloud 行显示为 **relay 受限三态** —— 蓝徽章 `provider-relay-badge` + 「🔄 重置 key」按钮（非删除）+ 默认模型下拉 + 启用✓ + 可拖拽。
- ★ **TC-3**：聊天**走 registry chain(relay-cloud)** —— 日志 `p5s2_chain_resolved n_entries=1 models=['gpt-5.5']` + `POST https://chinzy.com/v1/chat/completions "HTTP/1.1 200 OK"` + 桌宠真回复（**非** relayProviderBridge 旁路）。
- env-limited 项（§4）依赖中转站灰度 flag / 特殊账号条件，**诚实标注未真测**，不计入通过门，但给出可复跑步骤。

### 0.4 真测纪律（HARD，引全局 `~/.claude/knowledge-base/windows-mcp-e2e.md`）
- ⛔ **禁**：`ws://127.0.0.1:8100/*` WebSocket 直注 `settings_providers_ensure`、`import ensure_relay_provider`/读 registry、`cmdkey /list` 查 keychain、`last_mile_smoke.py`/pytest —— 全是协议层/脚本/间接证据，**不替代**真模拟点击。pytest 仅作 §5 旁证。
- ✅ **每个 TC**：Snapshot/Screenshot → 真坐标点击 / 真输入 → 截图 → `config.toml` 真字节 + tauri-dev.log grep 判定。动作前 declare `坐标=(x,y)|动作=|期望=`；失败 retry ≥3 次不同 workaround 才能标「环境受限」。
- ✅ **中文输入 workaround**：STA Runspace + `Clipboard.SetText("中文")` + Ctrl+V；先 Click 输入框聚焦再粘贴。
- ⚠️ **worktree/dev 环境**：必设 `DESKPET_BACKEND_DIR=<worktree>/backend` + `DESKPET_PYTHON=<主.venv python>`，日志确认 `[backend_launch] Dev python=... backend_dir=<worktree>` 才是测了改动代码；见 `DESKPET_BACKEND_PORT`/`DESKPET_USER_DATA_DIR`/`DESKPET_DEV_MODE`。**不要**手动起 backend（端口双占）、**不要**手动再起 vite（双 vite 抢 strictPort）。

### 0.5 dev 登录前置
1. 启动 Tauri（不手动起 backend/vite）→ onboarding 登录窗。
2. 用 `LOCAL-DEV-CREDENTIALS.md`（gitignored）账号登录 → relay 下发 device key → keychain `deskpet-relay/device_key`。
3. ⚠️ dev 账号是 `deskpettest+...@qq.com` 测试号，**余额常态 $0** → 与 TC-1/2/3 收编+管理+路由无关（聊天仍能跑通直到撞余额门），但 §4 余额不足项可顺带观察。
4. backend 自动从 keychain 读 + registration 镜像进 registry。

---

## 1. 真机锚点速查表

| 元素 | 定位 | testid / 文案 |
|---|---|---|
| 设置入口 | Toolbar 齿轮 | `settings-toggle`（`Toolbar.tsx:130`） |
| 设置面板 | dialog | `aria-label="设置"` → `<h3>LLM Providers` → `settings-providers` |
| relay 行 | provider 行 | `provider-row-relay-cloud` |
| relay 徽章 | 行内蓝徽章 | `provider-relay-badge`（文案 `relay`） |
| 重置 key 按钮 | 行内（替代删除） | `provider-reset-key-btn-relay-cloud`（🔄） |
| 默认模型下拉 | 行内 select | default_model（默认 `gpt-5.5`） |
| 启用 checkbox | 行内 | enabled |
| 拖拽手柄 | 行首 | ⠿ |
| config.toml | 落盘实证 | `backend/userdata/config.toml` → `id = "relay-cloud"` 块 |
| backend 日志 | tauri dev 重定向 log | `relay_provider_ensured` / `p5s2_chain_resolved` / `chinzy.com/v1/chat/completions` |

---

## 2. 真测结果表（Lead 2026-06-26 windows-mcp 真机已执行）

> 证据目录（gitignored，本地）: `plans/manual-results-2026-06-26-relay-provider-wiC/`

| TC | 一句话 | ★ | 状态 | 证据 |
|---|---|---|---|---|
| **TC-1** | 登录后 relay-cloud 自动收编进 registry（config.toml + 日志） | ★ | ✅ **PASS** | `screenshots/PhaseCD-relay-cloud-managed-row.png`；`backend/userdata/config.toml:480-489`（`id="relay-cloud" source="relay" account_ref="cmoztk8nl0000po07jjr8wvu8"`）；日志 `relay_provider_ensured` |
| **TC-2** | 设置面板 relay-cloud = relay 受限三态（徽章 + 重置 key 按钮） | ★ | ✅ **PASS** | `screenshots/PhaseCD-relay-cloud-managed-row.png` + `screenshots/PhaseCD-relay-managed-full-row-reset-btn.png`（行 = 「中转站 · chinzy」+ relay 蓝徽章 + 默认 `gpt-5.5` 下拉 + 启用✓ + 编辑 + 🔄重置 key） |
| **TC-3** | 聊天走 registry chain(relay-cloud) → chinzy 200 OK → 真回复 | ★ | ✅ **PASS** | `screenshots/PhaseCDE-chat-via-relay-cloud-sent.png`；`EVIDENCE-chat-routes-relay-cloud.txt`（`p5s2_chain_resolved ... n_entries=1 models=['gpt-5.5']` + 多条 `POST https://chinzy.com/v1/chat/completions "HTTP/1.1 200 OK"`） |
| TC-4 | device key 复用（`api_key:null` 用缓存，不每登换 key） | ★ | ⏸ **env-limited** | 见 §4.1（需中转站 PR-6 开 `DEVICE_KEY_REUSE_ENABLED`） |
| TC-5 | 401 自愈（key 吊销 → `?rotate=force` 重铸） | — | ⏸ **env-limited** | 见 §4.2（需 console 吊销 key 构造） |
| TC-6 | 403 余额不足友好提示 | — | ⏸ **env-limited** | 见 §4.3（需 $0 账号 + 中转站 PR-1 上线 `INSUFFICIENT_BALANCE`） |
| TC-7 | 多账号切换不串号（account_ref 跟随） | — | ⏸ **env-limited** | 见 §4.4（需第二个 relay 账号） |
| TC-8 | 重置 key 按钮真点 → force 轮换 | — | ⏸ **env-limited** | 见 §4.5（可真测但触发 force 轮换，建议单独验） |

**集成门结论（2026-06-26）**：★3 必过项（TC-1/TC-2/TC-3）**全 PASS** —— 用户核心需求「relay 自动收编为可管理 provider + 聊天走它打 chinzy」真机成立。env-limited 5 项依赖中转站灰度 / 特殊账号，诚实标注未真测。

---

## 3. 可真测 TC（已 PASS，含可复跑步骤）

### ★ TC-1 — 登录后 relay-cloud 自动收编进 registry ✅ PASS

**前提**：onboarding 登录成功（§0.5）。

**declare**
- `坐标=(齿轮 settings-toggle)|动作=click|期望=设置面板弹出，含「LLM Providers」段`
- `动作=读 backend/userdata/config.toml|期望=出现 [[llm.endpoints]] id="relay-cloud" source="relay" account_ref=<非空>`
- `动作=grep tauri-dev.log|期望=relay_provider_ensured ... key_fp=<sha256[:8]>（仅指纹，无明文 key）`

**步骤**
1. 登录完成后，Screenshot 确认进入桌宠主界面。
2. 点 Toolbar 齿轮 `settings-toggle` → 滚到「LLM Providers」。
3. 读 `backend/userdata/config.toml`，定位 `relay-cloud` 块。
4. grep tauri dev log（backend structlog 走 stderr → inherit → 落 tauri dev log）`relay_provider_ensured`。

**期望 / 通过判据**
- `config.toml` 含（实证已采集）：
  ```toml
  id = "relay-cloud"
  name = "中转站 · chinzy"
  base_url = "https://chinzy.com/v1"
  default_model = "gpt-5.5"
  api_key_ref = "deskpet.provider.relay-cloud"
  source = "relay"
  account_ref = "cmoztk8nl0000po07jjr8wvu8"
  ```
- 日志 `relay_provider_ensured` 出现且只打 `key_fp=`（不打明文，防泄漏）。
- ⛔ 防假绿：必须是登录后**自动**出现（registration 推送），非手填；手填路径**不会**触发 `relay_provider_ensured`。

**真测结果**：✅ **PASS**。证据 `screenshots/PhaseCD-relay-cloud-managed-row.png` + `backend/userdata/config.toml:480-489`（实采 `source="relay" account_ref="cmoztk8nl0000po07jjr8wvu8"`）。

---

### ★ TC-2 — 设置面板 relay-cloud 显示为 relay 受限三态 ✅ PASS

**declare**
- `坐标=(provider-row-relay-cloud)|动作=Screenshot|期望=行内有蓝色「relay」徽章 provider-relay-badge`
- `坐标=(行内操作区)|动作=Screenshot|期望=出现「🔄 重置 key」provider-reset-key-btn-relay-cloud，且无「删除」按钮`
- `期望=行可拖拽（⠿ 手柄）+ default_model 下拉(gpt-5.5) + 启用 checkbox(✓)`

**步骤**
1. 设置面板「LLM Providers」找到 `provider-row-relay-cloud` 行。
2. Screenshot 放大行内：核对徽章文案=`relay`、操作区有 🔄 重置 key（替代删除）、默认模型下拉、启用勾选、行首拖拽手柄。
3. （对照）若同列有手填 provider，其无 `provider-relay-badge`、有删除按钮 —— 证两态区分（`isRelayProvider` 判 `source==="relay"`）。

**期望 / 通过判据**
- relay 行三态完整：蓝徽章 + 重置 key 按钮（**不是**删除）+ default_model 可选 + 可启停 + 可拖拽。
- ⛔ 防假绿：relay 行**不能**出现明文 api_key 输入框（key readonly）；删除按钮被「🔄 重置 key」替换。

**真测结果**：✅ **PASS**。证据 `screenshots/PhaseCD-relay-cloud-managed-row.png` + `screenshots/PhaseCD-relay-managed-full-row-reset-btn.png`（行 = 「中转站 · chinzy」+ relay 蓝徽章 + 默认 `gpt-5.5` 下拉 + 启用✓ + 编辑 + 🔄重置 key 按钮，全要素齐）。

---

### ★ TC-3 — 聊天走 registry chain(relay-cloud) → chinzy 200 OK → 真回复 ✅ PASS

**declare**
- `坐标=(聊天输入框)|动作=Click 聚焦 + Clipboard 粘贴「你好，简单介绍下你自己」|期望=消息发出`
- `动作=grep tauri-dev.log|期望=p5s2_chain_resolved ... n_entries=1 models=['gpt-5.5']（chain 唯一一行 relay-cloud）`
- `动作=grep tauri-dev.log|期望=POST https://chinzy.com/v1/chat/completions "HTTP/1.1 200 OK"`
- `期望=桌宠气泡出现真回复（非报错、非旁路）`

**步骤**
1. 主界面聊天框 Click 聚焦，Clipboard 粘中文消息（中文 workaround），回车发送。
2. 等回复出现，Screenshot 气泡。
3. grep tauri dev log：`p5s2_chain_resolved` + `chinzy.com/v1/chat/completions`。

**期望 / 通过判据**
- `p5s2_chain_resolved ... n_entries=1 models=['gpt-5.5']` —— chain 解析到**唯一**的 relay-cloud（证走 registry 而非 relayProviderBridge 旁路）。
- 多条 `POST https://chinzy.com/v1/chat/completions "HTTP/1.1 200 OK"`。
- 桌宠真回复可见。
- ⛔ 防假绿：必须看到 `p5s2_chain_resolved` 走 chain，**不是** `local_llm` 热替换旁路；`n_entries=1` 证收编后 chain 里就这一把 relay provider。

**真测结果**：✅ **PASS**。证据 `screenshots/PhaseCDE-chat-via-relay-cloud-sent.png` + `EVIDENCE-chat-routes-relay-cloud.txt`：
```
event='p5s2_chain_resolved sid=... n_entries=1 models=['gpt-5.5'] ...'
POST https://chinzy.com/v1/chat/completions "HTTP/1.1 200 OK"   (×多条)
```

---

## 4. env-limited TC（诚实标注·未真测·依赖中转站灰度 / 特殊条件）

> 以下项**未在 2026-06-26 真机执行**。理由 = 依赖中转站 PR 灰度 flag、console 构造、特殊账号等当前不可控条件。给出可复跑步骤，待条件就绪后补真测。**不计入集成通过门**。

### 4.1 TC-4 — device key 复用（`api_key:null` 用缓存，不每登换 key）★
- **env-limited 理由**：复用三态需中转站 **PR-6 开 `DEVICE_KEY_REUSE_ENABLED` flag**（handoff §10，默认关）。当前过渡态 `/v1/providers` 仍**每调即新签**→ 每次登录 device key 轮换 + registry 镜像随之刷一把新 key（功能正常，但 key 抖动、贡献 key 表增长）。flag 开后 reuse 命中返 `api_key:null`、客户端用本地缓存、同把复用、抖动消失。
- **复跑步骤（flag 开后）**：① 记当前 `deskpet-relay/device_key` 前缀 / 后端日志 `key_fp`；② 重启 app 重登 → ③ 期望同 prefix / 同 `key_fp`，relay 后端 key 表不新增（`GET /v1/providers` 返 `api_key:null`，客户端命中缓存）。

### 4.2 TC-5 — 401 自愈（key 吊销 → `?rotate=force` 重铸）
- **env-limited 理由**：需在中转站 console **主动吊销当前 device key** 构造 401 `INVALID_TOKEN`/`EXPIRED_TOKEN`。dev 无 console 吊销权限。
- **复跑步骤**：① console 吊销当前 key；② 桌宠发一条聊天 → 期望后端 `classify_relay_error` 映射 `RELAY_KEY_INVALID` → 前端 `App.tsx` recover → `syncDeviceKey({force:true})` 打 `GET /v1/providers?rotate=force` 拿新明文 → registration 重镜像 → 重发后 200 OK；③ 60s 内≥2 次仍失败 → 熔断 + 桌宠提示「中转站 key 反复失效，请重新登录或检查余额」（`relayProviderRegistration.ts:64`）。

### 4.3 TC-6 — 403 余额不足友好提示
- **env-limited 理由**：需 **$0 余额账号** + 中转站 **PR-1 上线结构化 `INSUFFICIENT_BALANCE` code**（PR-1 前是 `FORBIDDEN` 过渡态）。dev 测试号虽常态 $0，但 PR-1 未上线时 403 体为 `FORBIDDEN`，前端文案路径需过渡判定，未稳定真测。
- **复跑步骤**：① 用 $0 账号聊天触发 403；② 期望后端 `classify_relay_error` → `INSUFFICIENT_BALANCE` → 前端 `relayErrorText.ts:24` 弹「账号余额不足，请充值：<RECHARGE_URL>」（**不**提示重登）；③ 持续触发不刷屏。

### 4.4 TC-7 — 多账号切换不串号（account_ref 跟随）
- **env-limited 理由**：需**第二个 relay 账号**。当前仅一个 dev 测试号。
- **复跑步骤**：① 登录 A → 记 `config.toml account_ref` + 聊天 `key_fp`；② 登出 A（触发 `settings_providers_relay_logout` disable + 清 account_ref + 删 key）→ 登录 B；③ 期望 `account_ref` 变为 B 的 user.id + 聊天 `key_fp`==B≠A（证用 B 的额度，不串 A）。

### 4.5 TC-8 — 重置 key 按钮真点 → force 轮换
- **env-limited 理由（可真测，但有副作用）**：点 `provider-reset-key-btn-relay-cloud` 会触发 `recover` → `syncDeviceKey({force:true})` → `?rotate=force` **真轮换** device key（作废旧把）。属破坏性操作，建议**单独**安排真测（不与 TC-1~3 同跑，避免轮换污染复用观察）。
- **复跑步骤**：① 记当前 `key_fp`；② 点 🔄 重置 key 按钮；③ 期望日志出现 `?rotate=force` 调用 + 新 `key_fp`（≠旧）+ registration 重镜像 + 后续聊天用新 key 200 OK。

---

## 5. 后端单测旁证（仅辅助，不替代真测）

> 引全局纪律：pytest/vitest **不算** UI 证据，仅证后端/前端逻辑层正确，作集成真测的旁证。

| 套件 | 通过数 | 覆盖 |
|---|---|---|
| auth（前端 vitest） | 127 passed | `relayProviderRegistration` ensure/restore 缓存命中/account 切换 force/recover 熔断/串行化；`RelayAuthAdapter` 三态 rotate + syncDeviceKey + 冷启动载缓存 |
| registry（后端 pytest） | 39 passed | `ProviderEntry` source/account_ref roundtrip + writer 默认抑制 + `ensure_provider` 幂等 upsert + `KeyMissingError` + priority 去重 + WS `ensure`/`relay_logout` |
| components（前端 vitest） | 114 passed | `SettingsProviders.relay` 徽章/重置按钮/可拖拽/可启停；user 行不变 |
| errors（后端 pytest） | 14 passed | `classify_relay_error` 403 `INSUFFICIENT_BALANCE` / 402 嵌套 `error.code` / 403 `FORBIDDEN` 过渡 / 401 `INVALID_TOKEN`·`EXPIRED_TOKEN` → relay_key_invalid；`RATE_LIMITED`/`DEVICE_KEY_MISSING` 不误判 |
| relayErrorText（前端 vitest） | 5 passed | `insufficient_balance`→余额不足文案 / `relay_key_invalid`→重签文案映射 |

**说明**：真测门只认 §2/§3 的真机证据；§5 仅用于解释 env-limited 项（TC-5/6/7）的逻辑层已被单测覆盖，待环境就绪补真机即可。

---

## 附. 证据清单（plans/manual-results-2026-06-26-relay-provider-wiC/，gitignored 本地）

- `screenshots/PhaseCD-relay-cloud-managed-row.png` — TC-1/TC-2：设置面板 relay-cloud 受限三态行。
- `screenshots/PhaseCD-relay-managed-full-row-reset-btn.png` — TC-2：全行 + 🔄重置 key 按钮特写。
- `screenshots/PhaseCDE-chat-via-relay-cloud-sent.png` — TC-3：聊天发送。
- `EVIDENCE-chat-routes-relay-cloud.txt` — TC-3：`p5s2_chain_resolved n_entries=1 models=['gpt-5.5']` + 多条 `chinzy.com/v1/chat/completions 200 OK`。
- `tauri-dev.log` / `tauri-dev2.log` — 完整 backend 日志（含 `relay_provider_ensured`）。
- 落盘实证：`backend/userdata/config.toml:480-489`（`id="relay-cloud" source="relay" account_ref="cmoztk8nl0000po07jjr8wvu8"`）。
