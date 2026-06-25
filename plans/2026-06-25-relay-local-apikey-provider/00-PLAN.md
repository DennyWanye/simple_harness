# 优化 Plan — relay 登录后自动配置「本地长期 apikey + 可统一管理的 LLM provider」

> **状态**：📋 草案，待 review/执行（2026-06-25 立项）
> **路线决策**：**B（用户 2026-06-25 拍板）** —— relay 侧新增「长期 key」端点，客户端登录后调它铸一把
> 绑定账号、不轮换、可吊销的长期 key，存本地 keychain，并把它**收编进 LLMProviderRegistry**，
> 让 relay LLM 以一条**正常 registry provider** 的身份出现在设置面板里，和手填 provider 一起被增删改/排序/启停。
> **前置依赖**：relay 服务必须先实现 [`01-relay-api-contract.md`](./01-relay-api-contract.md) 里定义的端点（外部依赖，本仓库这边代码全部按该契约写好、可用 mock 先行联调）。
> **关联**：根因诊断见 [`plans/2026-06-25-relay-cloud-key-sync-followup.md`](../2026-06-25-relay-cloud-key-sync-followup.md)；STATUS §5 P1。

---

## 0. TL;DR（一句话）

现在 relay 登录走一条**旁路**（`update_cloud_config` 热替换单例 `local_llm`，轮换 key 不落盘、前端虚拟显示一条 readonly 行），跟用户手填 provider 的 `LLMProviderRegistry` **永不汇合**，且 backend 启动时读的 `deskpet-cloud-llm` keychain slot 跟 relay 登录账号**永久脱节**（P1）。本 plan 把 relay 收编进 registry：**登录 → 铸一把长期 key（relay 新端点）→ 存进 registry keychain → 作为一条 `source="relay"` 的正常 provider 出现在设置里**。一并修掉脱节 P1 与「换 key 必须重启 backend」。

---

## 1. 现状（代码级，全部已实证）

### 1.1 两条互不汇合的 provider 路径

| 维度 | 路径 A：手填 provider（registry） | 路径 B：relay 旁路（现状） |
|---|---|---|
| 数据落点 | `config.toml [[llm.endpoints]]` + keychain `deskpet`/`provider.<id>`（Python `keyring`）| 仅 `llm_runtime.json`（base_url/model，**不含 key**）+ 运行中 `local_llm` 单例内存 |
| key 存储 | OS keychain，`resolve_api_key()` 按需读（同库自洽）| **不存**（`persist_key:false`，轮换 tsk_xxx 绝不落盘）|
| agent 怎么用 | `resolve_provider_for_session` → `_provider_chain`（priority 升序）→ **每个 chat 请求现场重建 provider** | registry 为空 → `_provider_chain=None` → AgentLoop fallback 到 `local_llm` 单例旁路 |
| 设置面板 | 完整 CRUD（`settings_providers_*` WS 协议）| 仅前端虚拟一条 `__relay__:` readonly 行（`SettingsProviders.tsx:42-92`，**不进 registry**）|
| 增删改 | ✅ 全套 | ❌ 不可编辑/删除/排序 |

**关键文件锚点**：
- registry：`backend/llm/provider_registry.py`（`LLMProviderRegistry`：`add_provider`/`update_provider`/`remove_provider`/`reorder`/`set_enabled`/`get_chain`/`resolve_api_key`；`ProviderEntry` dataclass `:114`；keychain service=`"deskpet"` account=`"provider.<id>"` `:75-80`；toml writer `_format_providers_section` `:208`；loader `_load_from_toml` `:298`；迁移模板 `_migrate_legacy_provider_config` `:610`）。
- WS CRUD handler：`backend/main.py:5758-5937`（`settings_providers_add/update/remove/reorder/probe_models`）+ `_broadcast_providers_changed` `:5785`。
- chain 选择：`backend/main.py:6650-6708`（registry 空 → `_provider_chain=None`）；session 级 `backend/llm/resolution.py:79-167 resolve_provider_for_session`。
- relay 旁路：`tauri-app/src/auth/relayProviderBridge.ts`（`applyOnce` → `updateCloudConfig("",{...persist_key:false})` `:99-106`；`recoverFromKeyInvalid` `:123`）→ `backend/main.py:4154-4243 update_cloud_config`（热替换 `local_llm` `:4200`，按 `persist_key` 决定是否写 `llm_runtime.json` `:4211`）。
- 前端虚拟行：`SettingsProviders.tsx:42-92 relayListToChinzy` + `isRelayProvider()`（id 前缀 `__relay__:` + priority -1000 + readonly 渲染 `SortableRow:304,360-394`）。
- 前端 provider 类型：`SettingsProviders.tsx:96-110 Provider`；store `:200-208 useProvidersStore`（后端镜像，单向数据流，无 localStorage）。

### 1.2 relay 凭据现状（实证）

- 登录 `POST /v1/auth/login` → `access_token`(JWT,1h) + `refresh_token`(30d, rotate) → keychain slot `deskpet-relay`/`access_token`、`/refresh_token`（`RelayAuthAdapter.ts:209-231,475-488`；Rust `secrets.rs:123-151`）。
- `device_key`(tsk_xxx) 经 `GET /v1/providers`（rotate 模式）后续获取 → keychain `deskpet-relay`/`device_key`（`RelayAuthAdapter.ts:405-426`；`secrets.rs:153-166`）。
- **device_key 会轮换且旧的立刻吊销**：每次 `GET /v1/providers` 作废上一把、明文返回新的（整合指南 `plans/DESKPET-INTEGRATION-GUIDE.md:64,752`）。事件驱动（冷启动/401 恢复/手动 reset），**无定时器**。
- relay **没有**「程序化创建长期不变 key」的端点；「手动 API key」只能在 web console（`/console/devices`）人工管理 → **这正是路线 B 要 relay 新增的端点**。

### 1.3 脱节 P1（要一并修掉）

`deskpet-cloud-llm` keychain slot 的唯一写点是 `SettingsPanel` 手填（`secrets.rs:74 set_cloud_api_key`，前端唯一调用点 `SettingsPanel.tsx`）。relay 登录/rotate **从不写**该 slot。backend spawn 时 `process_manager.rs:203` 读它注入 `DESKPET_CLOUD_API_KEY` env（**spawn 后固定，运行中不可热更**）。`backend/config.py:61 resolve_cloud_api_key` 只读该 env。→ backend 长期用最初手填的旧账号 key，relay 登录的实际账号感知不到 → 旧账号耗尽即静默 403。

> **本 plan 如何顺手修掉 P1**：relay 收编进 registry 后，relay 流量走 `resolve_provider_for_session` → `registry.resolve_api_key("relay-cloud")` **每请求按需读 keychain**，**完全绕开** spawn-time 固定 env 与 `deskpet-cloud-llm` slot。换账号/换 key 只改 registry keychain slot，下一个 chat 回合即生效，**无需重启 backend**。`deskpet-cloud-llm` slot + `DESKPET_CLOUD_API_KEY` env 从此只服务 manual/legacy 路径。

### 1.4 一个让路线 B 成立的关键自洽性

registry 的 key **写**（`_keychain_save`，`provider_registry.py:376`）和**读**（`_keychain_load`/`resolve_api_key`，`:390-412`）都走**同一个 Python `keyring`**（service=`"deskpet"`）。followup §3.2 观测到的「写后秒读是新值、重启变回旧值」诡异是 **win32cred 脚本(LOCAL_MACHINE) ↔ keyring-rs(ENTERPRISE) 跨库跨 scope** 引起的；registry 路径写读同库 → **自洽**。
⚠️ 但仍需在 WI-7 加一条**重启持久化验证**（写 → 重启 backend → 读回一致），把这个自洽性钉成回归测试，杜绝 Python `keyring` Windows backend persist scope 万一与预期不符。

---

## 2. 目标（验收口径）

1. relay edition 登录后（首次或 restore），设置面板「LLM Providers」里**自动出现**一条 relay provider（id=`relay-cloud`，name 如「中转站 · chinzy」），和手填 provider 同列。
2. 这条 provider 的 key 是 relay 新端点铸的**长期、不轮换、绑定账号、可吊销**的 key，**存在本地 keychain**（`deskpet`/`provider.relay-cloud`）。
3. 它能被**正常管理**：可启停、可拖拽排序（决定默认）、可选 default_model；**key 字段 readonly + 「relay」徽章**（用户不手填 key，由登录自动铸；删除走「登出/重置」语义而非误删）。
4. 聊天/办公技能**走这条 registry provider**（经 chain），不再走 `local_llm` 旁路；脱节 P1 消失，换账号无需重启。
5. key 被吊销（relay 401/INVALID）或额度不足（403 credit）时：**自动重铸**（401）或**给前端明确提示**（403「余额不足/登录失效」），不再静默降级「变笨」。
6. **幂等**：每次登录/restore **不重复铸 key**（registry 已有 `relay-cloud` 即跳过），避免 relay 侧 key 堆积。
7. manual edition **零行为变化**（全程 flag/edition 门控，OFF=字节级 BC）。

---

## 3. 架构（路线 B 数据流）

```
登录成功 (RelayAuthAdapter.persistTokens)            relay 服务
  │  emit 'login'                                       │
  ▼                                                     │
relayProviderRegistration.ensure()                      │
  │  ① registry 已有 relay-cloud? ──是──> 跳过(幂等)     │
  │  否                                                 │
  ▼                                                     │
  adapter.ensureLocalApiKey()  ──POST /v1/keys────────▶ │ 铸长期 key（绑定 account+deviceId，
  │  (Bearer access_token)     ◀──{key:tsk_live_xxx}─── │  idempotent；已存在则返回同一把)
  ▼
  control WS: settings_providers_ensure
      {id:'relay-cloud', source:'relay', base_url, models, default_model, api_key:tsk_live_xxx}
  │
  ▼ backend/main.py WS handler
  LLMProviderRegistry.ensure_provider(...)
      • Python keyring.set_password('deskpet','provider.relay-cloud', tsk_live_xxx)   ← 存本地
      • config.toml [[llm.endpoints]] upsert (id=relay-cloud, source='relay', api_key_ref)
      • _broadcast_providers_changed()  ──▶ 所有 control WS ──▶ 前端 store 刷新 → 设置面板出现该行
  │
  ▼ 之后每个 chat 请求
  resolve_provider_for_session → get_chain() → registry.resolve_api_key('relay-cloud')
      （每请求按需读 keychain；换 key 下一回合即生效，无需重启）
```

吊销/额度恢复：chat 收到 relay 401 → 前端 `relay_key_invalid` → `registration.recover()`（`ensureLocalApiKey({force:true})` 重铸 → 再 `settings_providers_ensure` 更新同一 id 的 key）→ 重发该 chat。403 credit → backend 发 actionable ErrorEvent → 前端提示充值。

---

## 4. 工作项（WI，逐个含「改哪个文件 / 加什么代码 / 测什么」）

> 依赖序：P0（relay 外部）→ WI-1（registry 数据模型）→ WI-2（WS 协议）→ WI-3（前端铸 key+推送）→ WI-4（UI 渲染）→ WI-5（chain/错误）→ WI-6（清理旧旁路）→ WI-7（测试+真机 E2E）。
> 门控：新增 config flag `[features].relay_managed_provider`（默认按 edition：relay edition ON、manual 永远 inert）。

### P0（外部，relay 侧）—— 见 `01-relay-api-contract.md`

relay 必须新增 `POST /v1/keys`（铸长期 key，幂等）、`GET /v1/keys`（列）、`DELETE /v1/keys/{id}`（吊销）。语义：绑定 `(account, deviceId, purpose="deskpet-local")`，**不轮换**，可在 console 吊销；铸出 `tsk_live_xxx`。**本仓库代码全部按此契约写好**，relay 未上线前 WI-3 的真实联调标记 `BLOCKED-ON-RELAY`，用 mock 单测覆盖。

### WI-1 —— backend：`ProviderEntry.source` 字段 + `ensure_provider()` 幂等 upsert

**文件**：`backend/llm/provider_registry.py`

1. **加字段**（`ProviderEntry` `:114-136`）：
   ```python
   source: str = "user"   # "user" | "relay" —— relay-managed 由登录自动铸/刷 key
   ```
   - `to_public_dict()`（`:165-174`）天然带上（`asdict`）；前端据此渲染 readonly+徽章。
   - `_format_providers_section`（`:208-233`）**新增一行** emit：在 `enabled` 行后加
     `out.append(f'source = "{_escape_toml_string(e.source)}"')`（仅当 `e.source != "user"` 时写，省默认值保持 diff 干净）。
   - `_load_from_toml`（`:330-341`）构造 `ProviderEntry` 时加 `source=str(raw.get("source", "user"))`。
   - `add_provider`（`:457-466`）构造 entry 时加 `source=str(fields.get("source", "user"))`。
2. **新增 `ensure_provider`**（idempotent upsert，relay 专用）：
   ```python
   async def ensure_provider(self, fields: dict[str, Any]) -> ProviderEntry:
       """幂等：id 已存在则更新可变字段(base_url/models/default_model/api_key/enabled)
       并【保留】用户的 priority 与排序；不存在则 add_provider。relay 登录自动配置用。"""
       provider_id = fields.get("id", "")
       _validate_provider_id(provider_id)
       idx = self._find_index(provider_id)
       if idx is None:
           return await self.add_provider(fields)   # 含 source 透传
       # 已存在 → 只更新内容，不动 priority/enabled(除非显式给)
       patch = {k: fields[k] for k in ("name","base_url","models","default_model","api_key","source")
                if k in fields and fields[k] is not None}
       return await self.update_provider(provider_id, **patch)
   ```
   - `update_provider`（`:522`）需补一个 `source` 分支：`elif k == "source": entry.source = str(v)`。
3. **`get_chain`/`resolve_api_key` 不变**（source 不影响选择逻辑）。

**测试**（`backend/tests/test_provider_registry.py` 增）：
- `test_source_field_roundtrips_toml`（写 source="relay" → reload → 仍是 relay；user 默认不写 source 行）。
- `test_ensure_provider_idempotent`（连调 2 次同 id：第 1 次 add、第 2 次 update key 不报 duplicate；priority 保持）。
- `test_ensure_provider_preserves_user_reorder`（ensure 不覆盖用户 reorder 后的 priority）。
- `test_ensure_provider_updates_key`（第 2 次带新 api_key → keychain 更新）。

### WI-2 —— backend：WS 协议 `settings_providers_ensure` + 广播

**文件**：`backend/main.py`（`settings_providers_*` handler 区 `:5758-5937`）

1. 在 handler 分发里**新增 case** `settings_providers_ensure`：
   ```python
   elif mtype == "settings_providers_ensure":
       reg = service_context.get("provider_registry")
       payload = msg.get("provider") or {}
       # 安全门：本通道只接受 source in {"relay"}（防误用 ensure 绕过 add 的校验）
       if payload.get("source") not in ("relay",):
           await _send_provider_error(ws, "ensure only accepts managed providers"); return
       entry = await reg.ensure_provider(payload)
       await _broadcast_providers_changed(...)   # 复用 :5785
   ```
   - 复用现有 control WS shared-secret 鉴权（同 `settings_providers_add`）。
   - 复用 `_broadcast_providers_changed`（`:5785-5802`）让所有 control WS（含设置面板）刷新。
2. **不**为 relay-managed 暴露 `remove`/手填 `api_key` 的特殊路径（删除/重置走 WI-3 登出语义）。

**测试**（`backend/tests/test_settings_providers_ws.py` 或新建）：
- `test_ensure_relay_provider_appears_in_list`（发 ensure → list_providers 含 relay-cloud + source=relay + key redacted）。
- `test_ensure_rejects_non_relay_source`（source=user 经 ensure 通道被拒）。
- `test_ensure_broadcasts_providers_changed`（mock 广播被调用）。

### WI-3 —— 前端：登录后铸长期 key + 推送 registry（核心）

**文件（新增）**：`tauri-app/src/auth/relayProviderRegistration.ts`
**文件（改）**：`tauri-app/src/auth/RelayAuthAdapter.ts`、`tauri-app/src/components/RelayEdition.tsx`、控制 WS 客户端

1. **`RelayAuthAdapter` 新增**铸 key 方法（按 `01-contract` 端点）：
   ```ts
   async ensureLocalApiKey(opts?: { force?: boolean }): Promise<{ key: string; keyId: string }> {
     const res = await this.fetchImpl(`${this.baseUrl}/v1/keys`, {
       method: "POST",
       headers: { Authorization: `Bearer ${this.accessToken}`, "Content-Type": "application/json",
                  "X-Device-Id": this.deviceId },
       body: JSON.stringify({ purpose: "deskpet-local", label: this.deviceName, force: !!opts?.force }),
     });
     if (!res.ok) throw new RelayError(...);            // 401→需重登；403→额度；其它→重试
     const d = await res.json();                         // { key, key_id, ... }
     return { key: d.key, keyId: d.key_id };
   }
   ```
   - 复用既有 `fetchImpl`/`RelayError`/refresh-on-401 机制（`RelayAuthAdapter.ts` 既有封装）。
2. **新模块 `relayProviderRegistration.ts`**（单例，替代 `relayProviderBridge` 的「推 key 给后端」职责）：
   ```ts
   const RELAY_PROVIDER_ID = "relay-cloud";
   export class RelayProviderRegistration {
     async ensure(adapter: RelayAuthAdapter, opts?: { force?: boolean }) {
       // 幂等：除非 force，registry 已有 relay-cloud 就跳过（不重复铸 key）
       if (!opts?.force && (await this.registryHas(RELAY_PROVIDER_ID))) return;
       const { key } = await adapter.ensureLocalApiKey({ force: opts?.force });
       const providers = await adapter.listProviders();          // 取 base_url + models（rotate=false，不动 device_key）
       const p = providers[0];
       await sendControl({ type: "settings_providers_ensure", provider: {
         id: RELAY_PROVIDER_ID, source: "relay",
         name: "中转站 · chinzy",
         base_url: p.base_url, models: p.models.map(m=>m.id), default_model: pickModel(p),
         api_key: key, enabled: true,
       }});
     }
     async recover(adapter) { await this.ensure(adapter, { force: true }); } // 401 重铸
   }
   ```
   - `registryHas`：查前端 providers store（已是后端镜像）是否含该 id（避免每次登录都打 relay）。
   - `sendControl`：经现有 control WS（与 `settings_providers_add` 同一通道）发消息。
   - **注意**：`listProviders()` 这里要用 **rotate=false** 路径（`RelayAuthAdapter.ts:370 listProvidersUsingCache`）只取 base_url/models，**不触发 device_key 轮换**（我们已不依赖 device_key）。
3. **挂载触发点**（`RelayEdition.tsx` / `App.tsx`）：
   - 登录成功（`login` 事件，`RelayEdition.tsx:113-119` 现挂 `refreshProviders` 处）→ 改调 `registration.ensure(adapter)`。
   - restore session（`RelayAuthAdapter.ts:180-207 restoreSession` 成功）→ `registration.ensure(adapter)`（幂等，已有则跳过）。
   - 聊天层收到 `relay_key_invalid`（现 `App.tsx:942` 调 `recoverFromKeyInvalid`）→ 改调 `registration.recover(adapter)` → 成功后重发 chat。
4. **退役 `relayProviderBridge` 的 `update_cloud_config` 推送**：保留文件但 ensure 路径接管；旁路仅作 flag OFF 时的回退（见 WI-6）。

**测试**（`tauri-app/src/auth/__tests__/relayProviderRegistration.test.ts` 新建，vitest）：
- `ensure_mints_and_pushes_when_absent`（registry 无 relay-cloud → 调 /v1/keys（mock）→ 发 settings_providers_ensure）。
- `ensure_skips_when_present`（registry 已有 → 不调 /v1/keys）。
- `recover_remints_with_force`（force=true → /v1/keys body force:true）。
- `ensure_uses_rotate_false`（断言不触发 device_key 轮换 / 不调 setRelayDeviceKey）。

### WI-4 —— 前端 UI：relay-managed provider 渲染（用 `source` 取代 id 前缀启发式）

**文件**：`tauri-app/src/components/SettingsProviders.tsx`、`AddProviderModal.tsx`

1. **`Provider` 类型**（`:96-110`）加 `source?: "user" | "relay"`（来自后端 list）。
2. **退役虚拟行**：删/收敛 `relayListToChinzy`（`:42-92`）+ merge（`:504-509`）—— relay 现在是**真 registry 行**，无需前端虚拟。`isRelayProvider(p)` 改判 `p.source === "relay"`（取代 `id.startsWith("__relay__:")`）。
3. **readonly 渲染**（`SortableRow`）：`source==="relay"` →
   - 显示「relay」徽章（复用 `:360-376` 既有徽章）；
   - **禁用删除**（`handleDelete` 对 relay 行：改为「登出/重置」语义按钮，或直接隐藏删除，提示「由登录管理」）；
   - **key 字段 readonly**（`AddProviderModal` 编辑该行时 api_key 输入禁用，显示「由登录自动铸」）；
   - **允许**：启停（enabled）、拖拽排序（priority/默认）、选 default_model。
4. **AddProviderModal**（`:23-32 ProviderDraft`）：编辑 relay 行时 `id`/`base_url`/`api_key` 禁改，仅 `default_model`/`enabled` 可改。

**测试**（vitest）：relay 行渲染徽章 + 删除禁用 + key readonly；user 行不受影响。

### WI-5 —— backend：chain 默认位 + 401/403 actionable 错误

**文件**：`backend/main.py`（chain resolve `:6650-6708`、chat 错误处理）、`backend/llm/resolution.py`

1. **默认位**：`ensure_provider` 首次 add 时 priority = `len(self._entries)+1`（沿用 `add_provider` `:464`）。若希望 relay 首次即默认（registry 原为空时），在 WI-1 `ensure_provider` 的 add 分支里：registry 为空 → priority=1。**但绝不覆盖用户后来的 reorder**（幂等 update 分支不动 priority）。文档化此规则。
2. **401/403 surfacing**（修 followup §4③）：backend 调 relay 返：
   - **401 INVALID_TOKEN/key**：chat handler 发 `ErrorEvent{reason:"relay_key_invalid"}` → 前端 `registration.recover()`（已有通道，WI-3）。
   - **403 credit too low**：发 `ErrorEvent{reason:"relay_credit_low", messageः":"账号余额不足，请充值或重新登录"}` → 前端弹明确提示（带充值入口 `relayConfig.ts` 的 console URL）。
   - 落点：`OpenAICompatibleProvider` 调用异常分类处（agent_loop fallback 链 `agent_loop.py:1216-1274` 之上，或 provider 适配层 HTTP 状态码映射）。**新增**状态码→reason 映射，避免现在「只 log warning 后 safe-fail 降级」。

**测试**：
- `test_chain_prefers_relay_when_registry_was_empty`。
- `test_relay_401_emits_key_invalid_event` / `test_relay_403_emits_credit_low_event`（mock provider 抛对应 HTTP 错）。

### WI-6 —— 清理：退役 relay 旁路 + 文档化 slot 归属

1. **flag OFF 回退**：`[features].relay_managed_provider=false` 时，保留旧 `relayProviderBridge.update_cloud_config` 旁路（字节级 BC）。ON 时走 registry。
2. **文档**：在 `ARCHITECTURE.md §关键设计决策.3`（manual vs relay 表）与 `backend/config.py` 注释里写明：`deskpet-cloud-llm` slot + `DESKPET_CLOUD_API_KEY` env 自此**仅服务 manual/legacy**；relay 走 registry `provider.relay-cloud`。
3. **登出清理**：`clear_all_relay_secrets`（`secrets.rs:173`）登出时，**同时**经 WS 让 backend `remove_provider("relay-cloud")` 或置 `enabled=false`（决策：置 disabled，保留排序；重登 ensure 重新启用 + 刷新 key）。

### WI-7 —— 测试矩阵 + 真机 E2E

**单测**：WI-1~5 各自单测全绿 + 关 flag 跑全套 pytest/vitest **不回归**（BC）。
**持久化回归（钉住 1.4 自洽性）**：`test_relay_provider_key_survives_restart` —— ensure 写 key → 新建 registry 实例（模拟重启）→ `resolve_api_key("relay-cloud")` 读回一致。
**真机 windows-mcp E2E**（CLAUDE.md HARD：真坐标点击 + 真输入 + 截图 + 抓 backend 日志）：
- **E2E-1**：relay 登录 → 打开设置「LLM Providers」→ **截图确认** relay-cloud 行自动出现 + relay 徽章 + key readonly。
- **E2E-2**：发一条聊天 → backend 日志确认走 `provider_chain`（`relay-cloud`）而非 `local_llm` 旁路 + 真回复。
- **E2E-3**：重启 app → relay-cloud 行仍在、**未重复铸 key**（relay 侧 key 列表无新增；幂等）。
- **E2E-4**（BLOCKED-ON-RELAY 直到 P0 上线）：在 console 吊销 key → 聊天触发 401 → 自动重铸 → 聊天恢复（截图 + 日志）。
- **E2E-5**：余额耗尽账号 → 403 → 前端弹「余额不足」明确提示（非静默变笨）。

---

## 5. 风险 / 未决（每条带处置）

| # | 风险 | 处置 |
|---|---|---|
| R1 | **relay 端点未上线**（P0 外部依赖）| 本仓库按 `01-contract` 写死，mock 单测先行；WI-3 真联调标 BLOCKED-ON-RELAY；relay 上线即可联调，无代码缺口。 |
| R2 | Python `keyring` Windows backend persist scope 万一异常（followup §3.2 阴影）| WI-7 `test_..._survives_restart` 钉死；真机 E2E-3 重启复验。registry 写读同库已自洽，风险低。 |
| R3 | **key 幂等**：登录/restore 反复铸 key 堆积 relay | WI-3 `registryHas` 跳过 + P0 端点本身幂等（同 account+deviceId+purpose 返同一把）双保险。 |
| R4 | 多设备：每设备一把长期 key | P0 端点按 `(account, deviceId)` 隔离铸 key；与现 device_key 的 `X-Device-Id` 模型一致。 |
| R5 | 用户既登录 relay 又手填 provider → 谁默认 | priority 决定；ensure 不覆盖用户 reorder。文档化「拖到顶=默认」。 |
| R6 | relay-cloud 行被用户误删 | WI-4 禁删（改登出语义）；即便删了，下次登录 ensure 重建（幂等）。 |
| R7 | flag/edition 门控遗漏致 manual 回归 | WI-6 flag + edition 双门；关 flag 全套测试 BC 守。 |

---

## 6. 执行顺序与「100% 可执行」判据

1. P0 契约（`01-contract`）交付 relay 团队（外部并行）。
2. 本仓库：WI-1 → WI-2 →（WI-3 mock 联调）→ WI-4 → WI-5 → WI-6 → WI-7 单测全绿。
3. relay P0 上线 → WI-3/E2E-4 真联调 → 真机 E2E 全 PASS → 更新 STATUS §3「relay 登录集成」行 + §4 里程碑 + 关闭 §5 P1。

**「100% 可执行」= 上述每个 WI 的「改哪个文件/加什么代码/测什么」三元组都已具体到函数与行锚，唯一外部 gap 是 P0（relay 端点），且该 gap 已被 `01-contract` 契约 + mock 完全隔离。** 子代理对抗挑战须把任何「这里其实还得查/还得定」的模糊点补成上述三元组级别才算收敛。

---

## 7. 变更文件清单（落地时一览）

- `backend/llm/provider_registry.py`（WI-1：source 字段 + ensure_provider + writer/loader/update 补 source）
- `backend/main.py`（WI-2：settings_providers_ensure handler；WI-5：401/403 actionable error；chain 默认位文档）
- `backend/llm/resolution.py`（WI-5：默认位规则，如需）
- `tauri-app/src/auth/RelayAuthAdapter.ts`（WI-3：ensureLocalApiKey）
- `tauri-app/src/auth/relayProviderRegistration.ts`（WI-3：新模块）
- `tauri-app/src/auth/relayProviderBridge.ts`（WI-6：降级为 flag-off 回退）
- `tauri-app/src/components/RelayEdition.tsx`、`App.tsx`（WI-3：触发点改挂 registration）
- `tauri-app/src/components/SettingsProviders.tsx`、`AddProviderModal.tsx`（WI-4：source 渲染、退役虚拟行）
- `backend/config.py` / `config.toml`（WI-6：flag `[features].relay_managed_provider` + 注释）
- `ARCHITECTURE.md`（WI-6：manual vs relay 表更新）
- 测试：`backend/tests/test_provider_registry.py`、`test_settings_providers_ws.py`、`backend/tests/test_relay_*`、`tauri-app/src/auth/__tests__/relayProviderRegistration.test.ts`
- 真机：`testcase/2026-06-25-relay-local-apikey-provider/manual-test.md` + `plans/manual-results-2026-06-25-relay-provider/`
