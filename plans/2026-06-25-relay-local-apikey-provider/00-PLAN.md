# 优化 Plan — relay 登录后自动配置「本地长期 apikey + 可统一管理的 LLM provider」

> **状态**：📋 草案 v4（2026-06-25 立项；v2=R1 修订；v3=R2 修订；**v4=R3 收敛验证后修订** — 修最后 1 BLOCKER：WI-5 error_class 注入落点（异常就地转 ErrorEvent，永不回 `main.py:6684`）→ 改在抛出处 `_map_error`（转实例方法+provider 带 is_relay）注入；+ 4 接线 MAJOR：WS 行号回正 `:5758`、`setPetError` 经回调注入、`restoreSession` 在 `RelayEdition.tsx:73` 上抛、`App.tsx:942` 无自动重发上下文已据实标注、`relayAdapter` 经 `SettingsPanel` 再下传）。**R1+R2+R3 共 8 BLOCKER + 15 MAJOR + 5 MINOR 全部消化。**
> **路线决策**：**B（用户 2026-06-25 拍板）** —— relay 侧新增「长期 key」端点，客户端登录后铸一把绑定账号、不轮换、可吊销的长期 key，存本地 keychain，并**收编进 LLMProviderRegistry**，让 relay LLM 以一条**正常 registry provider** 出现在设置面板，与手填 provider 一起增删改/排序/启停。
> **前置依赖**：relay 必须先实现 [`01-relay-api-contract.md`](./01-relay-api-contract.md) 的端点（外部 P0；本仓库代码全部按契约写好，可 mock 先行）。
> **关联**：根因 [`plans/2026-06-25-relay-cloud-key-sync-followup.md`](../2026-06-25-relay-cloud-key-sync-followup.md)；STATUS §5 P1。

---

## 0. TL;DR

relay 登录现走**旁路**（`update_cloud_config` 热替换单例 `local_llm`，轮换 key 不落盘、前端虚拟 readonly 行），与手填 provider 的 `LLMProviderRegistry` **永不汇合**，且 backend spawn 时读的 `deskpet-cloud-llm` slot 与 relay 账号**永久脱节**（P1）。本 plan 把 relay 收编进 registry：**登录 → 铸长期 key（relay 新端点）→ 存 registry keychain → 作为 `source="relay"` 的正常 provider 出现在设置里**。
**幂等四问**（行在 + 账号一致[`user.id`] + 本地 key 在 + base_url/models 最新）+ **registration 本地缓存**（防异步 store TOCTOU）+ **recover 熔断**（防死循环）统一解决多账号串号 / keychain 丢失不自愈 / relay 换域名僵死 / 自动重铸风暴；并补「重置 key」自助入口、并发串行化、channel 注入、WS/authedJson/error_class 代码级修正。

---

## 1. 现状（代码级，全部已实证 ✓；行号经 R2 复核校正）

### 1.1 两条互不汇合的 provider 路径

| 维度 | 路径 A：手填（registry） | 路径 B：relay 旁路（现状） |
|---|---|---|
| 数据落点 | `config.toml [[llm.endpoints]]` + keychain `deskpet`/`provider.<id>`（Python `keyring`）| 仅 `llm_runtime.json`（base_url/model，**不含 key**）+ 运行中 `local_llm` 单例内存 |
| key 存储 | OS keychain，`resolve_api_key()` 按需读 | **不存**（`persist_key:false`，轮换 tsk_xxx 绝不落盘）|
| agent 怎么用 | `resolve_provider_for_session` → `get_chain()` → **每个 chat 请求现场 `resolve_api_key()` 取明文构造 provider**（`main.py:6684` `_api_key=_registry.resolve_api_key(_entry.id) or "ollama"`）| registry 空 → `_provider_chain=None` → fallback `local_llm` 旁路 |
| 设置面板 | 完整 CRUD（`settings_providers_*` WS）| 前端虚拟一条 `__relay__:` readonly 行（`SettingsProviders.tsx:42-92`，**不进 registry**）|

> **✅ 核心假设成立**（R1+R2 实证）：chain 消费侧确实调 `resolve_api_key` 取**明文** key 注入 provider（`main.py:6684`/`5707`/`3038`）；redact 只在 `to_public_dict()`（`provider_registry.py:165`）与前端镜像。「relay 进 registry → 经 chain 用真 key」主干**不崩**。

**关键锚点**（R2 复核：✅=已核对准确，⚠️=已校正行号）：
- registry `backend/llm/provider_registry.py`：`ProviderEntry` `:114-136`；`add_provider` `:416`（构造 entry `:457-466`）；`update_provider` `:522`（`elif k=="api_key_ref"` `:566`）；`get_chain` `:579`（filter enabled、无 enabled 抛 `NoProviderConfiguredError` `:587`）；`resolve_api_key` `:408`；`get_entry` `:592`/`_find_index` `:600` 可复用；keychain service=`"deskpet"`/account=`"provider.<id>"` `:75-80`；writer `_format_providers_section` `:208`；loader 构造 `:337`；docstring 明写「ws handler serializes, no asyncio.Lock」`:284-288`；构造 + `service_context.register("provider_registry",...)` `main.py:329-344`。
- WS CRUD handler `backend/main.py`（✅ R3+grep 实证行号）：`elif msg_type in ("settings_providers_list_request","settings_providers_add",...)` 在 **`:5758`**；分支内 `_reg=service_context.get("provider_registry")` **`:5774`**、局部 `async def _broadcast_providers_changed` **`:5785`**、子分支 `elif msg_type=="settings_providers_add"` **`:5810`**；错误内联 `await ws.send_json({"type":"settings_providers_error","payload":{"reason":...,"detail":...}})`（`:5827` 等，✅ 用 `reason`/`detail`）。消息结构 `msg_type=raw.get("type")`、`_payload=raw.get("payload",{})`。WS 是**单连接 `while True: await ws.receive_json()`** → 同连接消息串行 await。
- chain 选择 `main.py:6650-6708`；session 级 `resolution.py:79-167`。
- relay 旁路 `relayProviderBridge.ts`：`applyOnce`→Tauri IPC `updateCloudConfig()`**非 WS** `:99-106`；**inflight+pending 串行化范本** `:40-89`；`recoverFromKeyInvalid` `:123`（注意：现网它**不自动重发 chat**，故不自激励）。
- control WS channel **只活在 `App.tsx` 闭包**：`const {..., getChannel: getControlChannel}=useWebSocket()` `:550`；发消息 `getControlChannel().send({type,payload})`（`:563/:633/:1419`）。`RelayEdition.tsx`/adapter 单例触达不到。
- relay 错误前端消费链路**已全通**：`LLMProviderError.error_class`（`errors.py:39`）→ `ErrorEvent.error_class`（`agent_loop.py:380`）→ fwd（`agent_loop.py:1403`/`main.py:7591`）→ `App.tsx:942` `if(p.error_class==="relay_key_invalid"&&relayAdapter)` + `relayErrorText.ts:39`。**字段名 `error_class`（非 `reason`）。⚠️ 注入侧未实现（见 WI-5）。**
- 前端虚拟行 `SettingsProviders.tsx:42-92 relayListToChinzy`；全局 zustand `useProvidersStore=create()` **`:205`**（`useProvidersStore.getState().providers` React 外可读 ✅）；`SortableRow` readonly 态（`useSortable({disabled:readonly})` `:306`、readonly 分支只渲染「登录账户面板管理」文本 `:391-394`=**全禁**）。
- relay adapter `RelayAuthAdapter.ts`：✅ `authedJson<T>(method,path,body?,extraHeaders={})` `:552`（401→`refreshSession()`→单次重试→抛 `RelayApiError` `:580-592`）；字段 `user` `:135`/`deviceId` `:136`/`deviceName` `:137`；✅ **`currentUser():User|null` 已存在 `:168`**（不用新加 getter）；`User` 类型（`types.ts:20-32`）= `id`(server-issued stable account id)/`email`/`username?`/`role?`/`plan?` — **无 `sub`**；`deviceId` 稳定登出不重置 `:654`；`restoreSession` `:180`；`logout()` `:321`（内部 `localLogout` `:649` 清 user/deviceKey + `clearAllRelaySecrets` `:656`）；`listProvidersUsingCache(opts:{failOnMissing?})` `:370`，`DEVICE_KEY_MISSING && !failOnMissing` 才 fallback rotate `:378-383`。
- Rust `secrets.rs`：`set_cloud_api_key` **`:75`**、`clear_all_relay_secrets` `:174`。
- error_class 注入侧现状（⚠️ **真 TODO**）：`openai_adapter.py:97-128 _map_error`（`@staticmethod`）把 401/403 映射成 `LLMAuthError`（`:113-114`）但 **`error_class` 恒 None、且只读 `status_code` 不读 body**；`classify_relay_error(status,body)`（`relay_errors.py:40`，`TODO(M6)` `:15`）**已写好但全仓零调用**。

### 1.2 relay 凭据现状

登录 `POST /v1/auth/login` → access_token(JWT,1h)+refresh_token(30d,rotate)；device_key 经 `GET /v1/providers`（rotate）后续取，**每调一次作废上一把**（`DESKPET-INTEGRATION-GUIDE.md:64,752`），事件驱动无定时器。relay **无**「程序化创建长期 key」端点（手动 key 仅 console 人工管理）→ 即 P0 要新增。

### 1.3 脱节 P1（顺手修掉）

`deskpet-cloud-llm` slot 唯一写点是 `SettingsPanel` 手填；relay 登录/rotate 从不写它；backend spawn 时 `process_manager.rs:203` 读它注入 `DESKPET_CLOUD_API_KEY` env（**spawn 后固定**），`config.py:61 resolve_cloud_api_key` 只读该 env → backend 长期用旧账号 key → 静默 403。
> **本 plan 修掉 P1**：relay 进 registry 后走 `resolve_provider_for_session` → `resolve_api_key("relay-cloud")` **每请求按需读 keychain**，绕开固定 env 与 cloud-llm slot；换 key 下一回合生效，无需重启。cloud-llm slot + env 自此仅服务 manual/legacy。

### 1.4 自洽性（+ 钉死回归）

registry 的 key 写（`_keychain_save` `:376`）读（`resolve_api_key` `:408`）都走**同一个 Python `keyring`**（service `"deskpet"`）→ 自洽。followup §3.2 诡异是 win32cred(LOCAL_MACHINE)↔keyring-rs(ENTERPRISE) **跨库跨 scope**，registry 路径不沾。⚠️ 仍以 WI-7 `test_..._survives_restart` 钉死。

---

## 2. 目标（验收口径）

1. relay edition 登录后（首次/restore），设置面板「LLM Providers」**自动出现** relay provider（id=`relay-cloud`，name「中转站 · chinzy」），与手填同列。
2. key 是 relay 新端点铸的**长期、不轮换、绑定账号、可吊销**的 key，**存本地 keychain**（`deskpet/provider.relay-cloud`），记录账号指纹 `account_ref=user.id`。
3. **正常管理**：可启停、拖拽排序（决定默认）、选 default_model；key 字段 readonly + 「relay」徽章 + **可点「重置/重新铸 key」按钮**。删除受限（防误删，改「重置」语义）。
4. 聊天/办公技能走这条 registry provider（经 chain），不再走 `local_llm` 旁路；P1 消失，换账号无需重启。
5. key 吊销（401）→**自动重铸（带熔断）**；额度不足（403 credit）→ 前端**明确「余额不足，请充值」**（不误导「重新登录」），不再静默「变笨」。
6. **幂等四问健壮**：登录/restore/重启 不重复铸 key；账号变 / 本地 key 丢 / relay base_url/models 变 时**自愈**（重铸或刷新），且不因异步竞态误重铸、不因持续失败死循环。
7. manual edition **零行为变化**（edition + flag 双门，OFF=字节级 BC，golden-file 钉死，前端整段不挂载）。

---

## 3. 架构（路线 B 数据流，v3）

```
              ┌────────────── App.tsx（持 getControlChannel + relayAdapter；edition==="relay"&&relayAdapter 才挂载）──────────────┐
登录/restore  │  relayProviderRegistration.attach(getControlChannel)   ← 注入 channel（修 BLOCKER；manual 不挂载）              │
              │  on 'login'(reason=login) / restoreSession.ok(reason=restore) / error_class=relay_key_invalid(→recover)          │
              │  on settings_providers_error{reason:key_missing, provider_id===relay-cloud} → recover                            │
              └───────────────────────────────────────────────────┬───────────────────────────────────────────────────────────┘
                                                                   ▼
  RelayProviderRegistration.ensure(adapter,{reason})   ← 单例 + inflight 串行化(范本 relayProviderBridge.ts:40-89) + 本地缓存 + recover 熔断
   │ 幂等四问（读 registration **本地缓存** lastEnsured，非异步 store；修 TOCTOU）：
   │   ① 缓存有 relay-cloud?  ② lastEnsured.account_ref == currentUser().id?  ③ 本地 key 在(缓存标记)?  ④ base_url/models 最新?
   │   reason=restore 且 ①②③ 全是 → 直接 no-op（**不打 relay**）；reason=login/recover 或 ②③ 缺 → 拉 meta + 走铸 key；④ 缺 → 仅 ensure-update
   ▼ 需铸 key 时
   adapter.ensureLocalApiKey({force})  ──authedJson("POST","/v1/keys",{purpose,label,force},{X-Device-Id})─▶ relay（自带 refresh-on-401）
   ▼                                                              ◀──{key:tsk_live, key_id}─────────────────────────────────────┘
   getControlChannel().send({type:"settings_providers_ensure", payload:{
       id:"relay-cloud", source:"relay", account_ref:currentUser().id, name, base_url, models, default_model, api_key:tsk_live }})
   写 lastEnsured 本地缓存（account_ref + keyPresent=true）← 同步写，下次 ensureOnce 立即可见（修 TOCTOU）
   │
   ▼ backend main.py WS（msg_type / raw.payload / 内联 send_json；日志 redact api_key）
   LLMProviderRegistry.ensure_provider(payload) → keyring.set_password("deskpet","provider.relay-cloud",tsk_live)
       + config.toml upsert（id/source/account_ref/api_key_ref，priority 去重规整）+ _broadcast_providers_changed()
   │
   ▼ 每个 chat 请求：resolve_provider_for_session → get_chain() → resolve_api_key("relay-cloud")（按需读，即时生效）

错误：chat 收 relay 401 → (WI-5: chat handler 对 entry.source=="relay" 调 classify_relay_error) → ErrorEvent{error_class:relay_key_invalid}
        → App.tsx:942 → recover（熔断：60s 内 ≥2 次仍 401 则停 + setPetError「key 反复失效，请重登/查余额」）→ 重发 chat
      chat 收 403 credit → error_class:insufficient_balance → 前端「余额不足，请充值」（仅充值入口，不提重登）
```

---

## 4. 工作项（WI，逐个含「改哪个文件 / 加什么代码 / 测什么」）

> 依赖：P0（外部）→ WI-1 → WI-2 → WI-3 → WI-4 → WI-5 → WI-6 → WI-7。门控 flag `[features].relay_managed_provider`（relay edition 默认 ON；manual inert）。

### P0（外部 relay 侧）—— 见 `01-relay-api-contract.md`
`POST /v1/keys`（按 `(account,deviceId,purpose)` 幂等铸长期 key，可 force）+ `GET /v1/keys` + `DELETE /v1/keys/{id}`。本仓库按契约写好，relay 未上线时真实联调标 `BLOCKED-ON-RELAY`，mock 单测覆盖。

### WI-1 —— backend：`ProviderEntry` 加 `source`+`account_ref`；`ensure_provider` 幂等 upsert（key 缺失强铸 + priority 去重）

**文件**：`backend/llm/provider_registry.py`

1. **加两字段**（`ProviderEntry` `:114-136`，默认值保 BC）：`source: str = "user"`、`account_ref: str = ""`。
2. **writer**（`_format_providers_section` `:208-233`）在 `enabled` 行后，**仅非默认才 emit**（配合 WI-7 golden 字节不变）：
   `if e.source!="user": out.append(f'source = "{_escape_toml_string(e.source)}"')`；`if e.account_ref: out.append(f'account_ref = "{_escape_toml_string(e.account_ref)}"')`。
3. **loader**（`:337` 构造处）：`source=str(raw.get("source","user")), account_ref=str(raw.get("account_ref",""))`。
4. **add_provider**（`:457-466`）：构造 entry 加 `source/account_ref` 透传。
5. **update_provider**（`:566 elif k=="api_key_ref"` 前）：加 `elif k=="source": entry.source=str(v)`、`elif k=="account_ref": entry.account_ref=str(v)`。
6. **新增 `class KeyMissingError(RuntimeError)`**（带 provider_id）。
7. **新增 `ensure_provider`**（幂等 + key 缺失强铸 + priority 去重，修 NB3）：
   ```python
   async def ensure_provider(self, fields: dict[str, Any]) -> ProviderEntry:
       pid = fields.get("id",""); _validate_provider_id(pid)
       idx = self._find_index(pid)
       if idx is None:
           # 首次抢默认：priority 严格小于所有现存（NB3 防撞号），随后规整唯一
           if self._entries:
               fields.setdefault("priority", min(e.priority for e in self._entries) - 1)
           else:
               fields.setdefault("priority", 1)
           entry = await self.add_provider(fields)
           self._normalize_priorities()           # 重排 1..N 唯一稳定
           return entry
       # 已存在：本地 key 丢失且本次未带 key → 抛 KeyMissingError（上层 recover force）
       if not fields.get("api_key") and self.resolve_api_key(pid) is None:
           raise KeyMissingError(pid)
       patch = {k: fields[k] for k in
                ("name","base_url","models","default_model","source","account_ref","api_key")
                if k in fields and fields[k] is not None}
       return await self.update_provider(pid, **patch)

   def _normalize_priorities(self) -> None:
       """按当前 priority 升序稳定重排成 1..N 唯一，再 persist。撞号消歧。"""
       for i, e in enumerate(sorted(self._entries, key=lambda e: e.priority), start=1):
           e.priority = i
       self._persist_to_toml()
   ```
8. **helper** `def get_account_ref(self, pid) -> str|None`（供 WS handler/测试）。

> **并发**（R2-A 已证）：`add/update_provider` 无 Lock 但靠「WS 单连接串行 await（`main.py:4654`）+ 前端 inflight」两层串行 → 安全。

**测试**（`test_provider_registry.py`）：source/account_ref roundtrip；user 行不写这两行；ensure 幂等/保留 reorder/更新 key/更新 base_url+models；首次抢默认 priority；**`test_ensure_default_with_disabled_manual_rows_no_priority_collision`**（NB3）；`test_ensure_raises_key_missing_when_keychain_empty`（B-C2）。

### WI-2 —— backend：WS `settings_providers_ensure` + `settings_providers_relay_logout`（行号已校正）

**文件**：`backend/main.py`

1. 把 `"settings_providers_ensure"` 与 `"settings_providers_relay_logout"` **加进 `:5758` 的 `elif msg_type in (...)` 元组**（否则拿不到分支内 `_reg` `:5774`/`_broadcast` `:5785`）。
2. CRUD 子分支区（`:5810` 同级）**新增**：
   ```python
   elif msg_type == "settings_providers_ensure":
       _payload = raw.get("payload", {}) or {}
       if _payload.get("source") not in ("relay",):
           await ws.send_json({"type":"settings_providers_error",
               "payload":{"reason":"ensure_only_managed","detail":"source=relay only"}})
       else:
           try:
               _entry = await _reg.ensure_provider(_payload)
               logger.info("relay_provider_ensured id=%s account_ref=%s base_url=%s key_fp=%s",  # 不打明文(M-C6/NB5)
                           _entry.id, _entry.account_ref, _entry.base_url, _key_fp(_reg.resolve_api_key(_entry.id)))
               await _broadcast_providers_changed()
           except KeyMissingError as _e:
               await ws.send_json({"type":"settings_providers_error",
                   "payload":{"reason":"key_missing","detail":str(_e),"provider_id":_payload.get("id")}})
   elif msg_type == "settings_providers_relay_logout":   # WI-6 登出清 key（B-C1）
       try:
           await _reg.update_provider("relay-cloud", enabled=False, account_ref="")
           _reg._keychain_delete("relay-cloud")          # A 的长期 key 绝不留给下一账号
           await _broadcast_providers_changed()
       except KeyError: pass
   ```
   `_key_fp(k)` = `None if not k else hashlib.sha256(k.encode()).hexdigest()[:8]`（指纹，供 NB5 验收，不泄明文）。
3. 鉴权复用现有 control WS shared-secret（同 `settings_providers_add`）；确认 handler 区无全量 `logger.debug(raw)` 泄 payload，有则对这两类屏蔽 `api_key`。

**测试**（`test_settings_providers_ws.py`）：ensure 出现在 list（source=relay+account_ref+key redacted）；拒 source!=relay；key_missing 回 error；广播被调；**handler 永不打明文 key**（caplog 断言只有 fp）；relay_logout 删 key+disable。

### WI-3 —— 前端：铸 key + channel 注入 + 幂等四问(本地缓存) + 并发串行化 + recover 熔断 + restore 不打 relay（核心）

**文件（改）** `RelayAuthAdapter.ts`、`App.tsx`；**新增** `relayProviderRegistration.ts`。

1. **`RelayAuthAdapter` 新增**（走 `authedJson` 复用 refresh-on-401）：
   ```ts
   async ensureLocalApiKey(opts?: { force?: boolean }): Promise<{ key: string; keyId: string }> {
     const d = await this.authedJson<{ key: string; key_id: string }>(
       "POST", "/v1/keys",
       { purpose: "deskpet-local", label: this.deviceName ?? "", force: !!opts?.force },
       { "X-Device-Id": this.deviceId ?? "" });          // 401→authedJson 自动 refresh；否则抛 RelayApiError
     return { key: d.key, keyId: d.key_id };
   }
   /** 仅取 base_url/models，绝不轮换 device_key（失败返 null，不静默 rotate；修 NB4） */
   async fetchRelayProviderMeta(): Promise<Provider | null> {
     try { const ps = await this.listProvidersUsingCache({ failOnMissing: true }); return ps[0] ?? null; }
     catch { return null; }                               // 不 fallback 到 rotate 版
   }
   ```
   （`currentUser()` 已存在 `:168`，直接用。）
2. **新模块 `relayProviderRegistration.ts`**（单例 + inflight 写死 + 本地缓存 + 熔断）：
   ```ts
   const RELAY_PROVIDER_ID = "relay-cloud";
   type EnsureReason = "login" | "restore" | "recover";
   export class RelayProviderRegistration {
     private getChannel: (() => Channel | null) | null = null;
     private onFatal: ((msg: string) => void) | null = null;   // 注入桌宠错误提示(setPetError 不可 import)
     private inflight: Promise<void> | null = null;
     private lastEnsured: { accountRef: string; keyPresent: boolean } | null = null;  // 本地缓存(修 TOCTOU/NB2)
     private recoverHits: number[] = [];                                              // 熔断时间戳(修 NB1)
     attach(getChannel: () => Channel | null, onFatal: (msg: string) => void) {
       this.getChannel = getChannel; this.onFatal = onFatal;   // App.tsx 把 setPetError 包成回调传入
     }

     ensure(adapter: RelayAuthAdapter, reason: EnsureReason = "login", force = false): Promise<void> {
       const p = (this.inflight ?? Promise.resolve())
         .then(() => this.ensureOnce(adapter, reason, force),
               () => this.ensureOnce(adapter, reason, force));
       this.inflight = p;
       p.finally(() => { if (this.inflight === p) this.inflight = null; });   // 写死链尾(修 NB8)
       return p;
     }

     recover(adapter: RelayAuthAdapter): Promise<void> {
       const now = Date.now();
       this.recoverHits = this.recoverHits.filter(t => now - t < 60_000);
       if (this.recoverHits.length >= 2) {                                     // 60s 内 ≥2 次仍触发 → 熔断
         this.recoverHits = [];
         this.onFatal?.("中转站 key 反复失效，请重新登录或检查余额");        // 停止自动重发(修 NB1)
         return Promise.resolve();
       }
       this.recoverHits.push(now);
       return this.ensure(adapter, "recover", true);
     }

     private async ensureOnce(adapter: RelayAuthAdapter, reason: EnsureReason, force: boolean) {
       const acct = adapter.currentUser()?.id ?? "";                           // 账号指纹=user.id(修 B1)
       const cached = this.lastEnsured;
       const accountOk = !!cached && cached.accountRef === acct && cached.keyPresent;
       if (reason === "restore" && accountOk && !force) return;                // restore 直接 no-op，不打 relay(修 NB4)
       const meta = await adapter.fetchRelayProviderMeta();
       if (!meta) return;                                                      // relay 无 provider / 拉失败 → 静默
       const models = (meta.models ?? []).map(m => m.id);
       if (!models.length) { console.warn("[reg] empty models"); return; }     // 修 m3
       const needMint = force || !accountOk;                                   // 账号不一致/无缓存/force → 重铸
       let key: string | undefined;
       if (needMint) key = (await adapter.ensureLocalApiKey({ force })).key;
       const payload: any = { id: RELAY_PROVIDER_ID, source: "relay", account_ref: acct,
         name: "中转站 · chinzy", base_url: meta.base_url, models, default_model: pickModel(meta) };
       if (key) payload.api_key = key;
       const ch = this.getChannel?.(); if (!ch) { console.warn("[reg] no channel"); return; }
       ch.send({ type: "settings_providers_ensure", payload });
       this.lastEnsured = { accountRef: acct, keyPresent: true };              // 同步写缓存(修 NB2)
     }
     /** 登出：清缓存（backend 删 key 由 WI-6 logout 消息负责） */
     onLogout() { this.lastEnsured = null; this.recoverHits = []; }
   }
   export const relayProviderRegistration = new RelayProviderRegistration();
   ```
   - `setPetError` 复用现有桌宠错误提示（grep `setPetError`/`RelayEdition` 错误条；无则经 channel 发一个 toast 事件）。
3. **挂载（`edition==="relay" && relayAdapter` 才挂；修 NB6）**：
   - 启动早期（`App.tsx`，`getControlChannel` + `setPetError`@`:720` 都在作用域）：
     `if (edition==="relay" && relayAdapter) relayProviderRegistration.attach(getControlChannel, (m)=>setPetError(m))`。
   - **`login` 事件**：在 `App.tsx` 订阅 `relayAdapter.on("login",...)`（adapter 是 EventEmitter）→ `void relayProviderRegistration.ensure(relayAdapter, "login")`。
   - **`restoreSession` 成功**：现由 **`RelayEdition.tsx:73`** 处理登录态恢复 → 在该成功分支**经回调上抛到 App.tsx**（给 `RelayEdition` 加 `onAuthed?(reason)` prop）→ App.tsx 调 `ensure(relayAdapter,"restore")`。（不把 ensure 放进 RelayEdition，保持触发集中在 App.tsx。）
   - **替换 `App.tsx:943`** `relayProviderBridge.recoverFromKeyInvalid(...)` → `relayProviderRegistration.recover(relayAdapter)`。⚠️ **R3 实证：`:940-941` 注释明示这里现在并不自动重发 chat**——故本 plan**不依赖"沿用现有重发"**；recover 成功后由**用户重发**（最小改动），或后续增量再做"recover 成功→自动重发 lastUserMessage"（标记为可选优化，非本 plan 必须）。熔断在 recover 内，持续失败即停 + 桌宠提示。
   - control WS 分发处（与 `providers_changed` 同级）监听 `settings_providers_error` 且 `payload.reason==="key_missing" && payload.provider_id===RELAY_PROVIDER_ID && relayAdapter` → `recover(relayAdapter)`（B-C2 自愈）。
   - 登出钩子（`App.tsx` 监听 `relayAdapter.on("logout")` 或登出按钮）→ `relayProviderRegistration.onLogout()` + 经 channel 发 `settings_providers_relay_logout`（WI-6）。
4. **退役旧桥推送**：flag ON 由 registration 接管；flag OFF 保留旧 `relayProviderBridge`（WI-6 BC）。

**测试**（`relayProviderRegistration.test.ts`，mock adapter+channel）：
- absent→mint+send；缓存命中→skip；account 切换（`currentUser().id` 变）→force 铸+新 account_ref（B-C1）；④变→只 ensure 不铸（M-C4）；
- **`recover_gives_up_after_2_failures_in_60s`**（NB1）；**`ensure_twice_back_to_back_before_broadcast_mints_once`**（读本地缓存，NB2）；
- `restore_with_good_cache_does_not_call_relay`（NB4）；`fetchRelayProviderMeta_never_rotates`；并发串行化铸一次；空 models abort。

### WI-4 —— 前端 UI：relay 「受限可编辑」三态 + 「重置 key」按钮（分支重构）

**文件** `SettingsProviders.tsx`、`AddProviderModal.tsx`。

1. `Provider` 类型（`:96-110`）加 `source?: "user"|"relay"; account_ref?: string`。
2. 退役虚拟行：删/收敛 `relayListToChinzy`（`:42-92`）+ merge（`:504-509`）；`isRelayProvider(p)` 改判 `p.source==="relay"`。
3. **`SortableRow` 三态**（取代「user 全功能 / relay 全禁」二态）：relay 态 = `useSortable({disabled:false})`（**可拖拽排序**）+ 「relay」徽章（复用 `:360-376`）+ **保留** enable checkbox + **保留** default_model 下拉 + **删除按钮替换为「🔄 重置 key」** → `onResetKey(p.id)`；编辑进 `AddProviderModal` 时 id/base_url/api_key 禁改。即旧 `:391-394` 全禁文本分支**重写**为受限可编辑。
4. `onResetKey` → 上层 `relayProviderRegistration.recover(relayAdapter)`（force 重铸+覆盖+ensure 更新）。⚠️ R3 实证 `relayAdapter` 现传入的是 **`SettingsPanel`（`:2233`）**，`SettingsProviders` 是其子组件 → 需把 `relayAdapter`（或一个 `onResetRelayKey` 回调）经 `SettingsPanel` **再下传一层**到 `SettingsProviders`。
5. `AddProviderModal`（`ProviderDraft` `:23-32`）加 source 感知，relay draft 禁 id/base_url/api_key，提示「由登录自动铸，点『重置 key』刷新」。

**测试**（vitest）：relay 行徽章+可拖拽+可启停+重置按钮+点重置调 recover；user 行不变。

### WI-5 —— backend：error_class 在**异常抛出处**注入（修 R3-BLOCKER：消费侧拿不到异常）+ chain 空态

> **R3 实证的架构纠正**：LLM 调用异常**就地**在执行栈被 `except LLMProviderError` 捕获 → 转 `ErrorEvent(error_class=getattr(exc,"error_class",""))` → `return` 生成器，**永不冒泡回 `main.py:6684`**（那里只构造 provider）。所以 v3「在消费侧 catch 后分类」**落点错**。正解：**在异常构造处（`_map_error`）就把 `error_class` 填好**——基础设施已就绪：`LLMProviderError.__init__` 已支持 `error_class`（`errors.py:28,39`，且 `:34-39` 有 WI-R5 注释明示就是为 relay 分类预留），`ErrorEvent` 发射处已 `getattr(exc,"error_class","")` 只读透传。

**文件** `backend/llm/openai_adapter.py`、`backend/llm/errors.py`、`backend/llm/relay_errors.py`、`backend/main.py`（provider 构造处 `:6685`）。

1. **`_map_error` 改实例方法 + provider 带 relay 标记**（解 R2-M1 staticmethod 无上下文）：
   - `openai_adapter.py:98 _map_error` 现为 `@staticmethod`，但**调用点 `:218`/`:312` 已是 `self._map_error(exc)`** → 去掉 `@staticmethod`、签名 `def _map_error(self, exc)` 即可，零调用点改动。
   - `OpenAICompatibleProvider.__init__` 加 `is_relay: bool = False`；在 `main.py:6685` 构造该 provider 时传 `is_relay=(_entry.source=="relay")`（此处 `_entry` 在作用域）；派生 provider（supervisor/reranker/ephemeral）默认 False。
2. **`_map_error` 内分类**（401/403 分支）：
   ```python
   status = getattr(getattr(exc, "response", None), "status_code", None)
   body = getattr(getattr(exc, "response", None), "text", "") or ""
   if status in (401, 403):
       ec = classify_relay_error(status, body) if self._is_relay else None   # relay-only
       return LLMAuthError(str(exc), provider=self.name, status_code=status, error_class=ec)
   ```
3. **扩 `LLMAuthError.__init__`**（`errors.py:66`，现硬编码 `status_code=401` 不收 error_class）：
   ```python
   def __init__(self, message, *, provider=None, status_code=401, error_class=None):
       super().__init__(message, provider=provider, status_code=status_code,
                        retriable=False, error_class=error_class)
   ```
   `classify_relay_error(status, body)`（`relay_errors.py:40`，现零调用=死代码）自此被真实调用，返回 `"relay_key_invalid"`(401) / `"insufficient_balance"`(403 余额关键词)。
4. **403 文案**：`insufficient_balance` → 前端「账号余额不足，请充值」+ 充值入口（`relayConfig.ts` console URL），**不提「重新登录」**（修 m2）。
5. **chain 空态**（M-C1）：chat handler 捕获 `NoProviderConfiguredError` 处，若唯一 provider 是 disabled 的 relay-cloud → 发 `ErrorEvent(error_class="relay_logged_out")`（前端「已登出，重新登录中转站后恢复」）。前端 `relayErrorText.ts` + `App.tsx:942` 增这两个新 error_class 分支。

**测试**：`test_map_error_relay_401_sets_relay_key_invalid` / `_403_sets_insufficient_balance`；**`test_map_error_non_relay_401_no_error_class`**（`is_relay=False` 不分类，不误伤 manual）；`test_classify_relay_error_now_called`（守死代码复活）；`test_disabled_relay_only_maps_logged_out`。

### WI-6 —— 清理：旁路降级 + 登出清 key（串入 inflight，修 NB7）+ 文档

1. **flag** `[features].relay_managed_provider`（`config.py`+`config.toml`；relay edition 默认 true，manual inert）；前端同名门控；OFF 保留旧 `relayProviderBridge` 旁路（字节级 BC）。
2. **登出清 key（修 B-C1 + NB7 竞态）**：登出时 `relayProviderRegistration.onLogout()`（清缓存）**并把 logout 串进 registration 的 inflight 链尾**（确保任何在途 recover 先完成、再发 logout，避免在途 recover 又铸 A 的 key）：给 registration 加 `logout(channelSend)` 方法，内部 `this.inflight = (this.inflight ?? Promise.resolve()).finally(()=>channelSend({type:"settings_providers_relay_logout"}))`。backend `settings_providers_relay_logout`（WI-2）置 disabled + 删 key + 清 account_ref。重登 → ensure 幂等四问：行在但 key 已删（③缺，且本地缓存已清）→ 走铸 key → 新账号干净 key。
3. **文档** `ARCHITECTURE.md §3`（manual vs relay 表）+ `config.py` 注释：`deskpet-cloud-llm` slot + `DESKPET_CLOUD_API_KEY` env 自此仅服务 manual/legacy。

**测试**：`test_relay_logout_disables_and_deletes_key`；`test_inflight_recover_during_logout_does_not_resurrect_key`（NB7：在途 recover + logout 串行 → 最终 key 已删）；`test_relogin_after_logout_remints`。

### WI-7 —— 测试矩阵 + BC + 真机 E2E

**单测**：WI-1~6 各自全绿 + 关 flag 全套 pytest/vitest 不回归。
**BC golden（M-C5）**：`test_manual_edition_toml_byte_identical`（manual config 经加 source/account_ref 字段的 writer 前后逐字节相同）；前端 `test_manual_edition_does_not_attach_registration`（NB6：`edition!=="relay"` 不 attach/不注册监听）。
**持久化（钉 1.4）**：`test_relay_provider_key_survives_restart`。
**真机 windows-mcp E2E**（HARD：真点击+真输入+截图+抓 backend 日志）：
- E2E-1 登录→设置面板出现 relay-cloud 行（徽章+key readonly+「重置 key」按钮）截图。
- E2E-2 聊天→日志确认走 `provider_chain`(relay-cloud) 非 `local_llm` + **日志打 `key_fp=` 指纹** + 真回复。
- E2E-3 重启 app→行仍在、**未重复铸 key**（relay key 列表无新增）+ **restore 路径无 relay `/v1/keys`/`/v1/providers` 调用**（NB4，抓网络/日志）。
- **E2E-4 多账号**（B-C1 + NB5 客观验收）：登出 A → 登录 B → 行 `account_ref` 变 B + **chat 日志 `key_fp` == B 的 key 指纹 ≠ A 的**（证不用 A 额度，非仅"看起来换了"）；条件允许时 relay mock/console 侧断言 A usage 不增。
- E2E-5 本地 key 丢失自愈（B-C2）：清 `provider.relay-cloud` slot → 发消息 → backend 回 key_missing → 自动重铸恢复。
- E2E-6 重置按钮（B-C3）：点「重置 key」→ 铸新 key（`key_fp` 变）→ 聊天用新 key。
- E2E-7（BLOCKED-ON-RELAY）console 吊销 key → 401 → 自动重铸 → 恢复；**持续 401 → 熔断后停刷屏 + 桌宠提示**（NB1）。
- E2E-8 额度耗尽账号 → 403 → 前端「余额不足，请充值」（非静默、非误导重登）。

---

## 5. 风险 / 未决（v3）

| # | 风险 | 处置 |
|---|---|---|
| R1 | relay 端点未上线（P0）| 按 `01-contract` 写死 + mock；真联调标 BLOCKED-ON-RELAY，无代码缺口。 |
| R2 | Python keyring Windows persist scope（followup §3.2 阴影）| WI-7 survives_restart + E2E-3 复验；写读同库自洽，风险低。 |
| R3 | key 幂等堆积 | 幂等四问 + **registration 本地缓存（修 TOCTOU）** + P0 端点 (account,deviceId,purpose) 幂等。 |
| R4 | 多设备一设备一把长期 key | P0 按 (account,deviceId) 隔离铸。 |
| R5 | manual+relay 默认归属 / priority 撞号 | relay 首次 priority 严格小于现存 + `_normalize_priorities` 去重（NB3）；尊重后续 reorder。 |
| R6 | relay 行误删 | WI-4 禁删改「重置」；删了下次登录 ensure ①缺→重建。 |
| R7 | flag/edition OFF 回归 | WI-6 双门 + WI-7 golden 字节 + manual 不挂载断言。 |
| R8 | 长期 key 明文经 WS/日志泄漏 | control WS loopback+shared-secret；WI-2 日志只打 `key_fp` sha256[:8]，**绝不明文**（caplog 测）。 |
| R9 | backend 独立重启（非 app）| toml 行+keychain 持久 → registry 重建；前端缓存在但 backend 已重读，行+key 在→幂等 no-op。 |
| **R10** | **自动 recover 死循环/风暴（NB1）** | recover 熔断：60s 内 ≥2 次仍 401 → 停止自动重发 + 桌宠提示；E2E-7 验。 |
| **R11** | **幂等读异步 store TOCTOU 误重铸（NB2）** | ensureOnce 读 registration **本地缓存** lastEnsured（send 后同步写），不读异步 store 镜像；back-to-back 测钉死。 |

---

## 6. 执行顺序与「100% 可执行」判据

1. P0 契约交付 relay（外部并行）。2. 本仓库 WI-1→…→WI-7 单测+golden 全绿。3. relay P0 上线 → WI-3/E2E-4~8 真联调 → 真机全 PASS → 更新 STATUS §3「relay 登录集成」+ §4 里程碑 + 关闭 §5 P1。

**判据**：每个 WI 的「文件:行号 + 加什么代码 + 测什么」三元组具体到函数/字段/调用；唯一外部 gap 是 P0（`01-contract`+mock 隔离）。**R1（3 BLOCKER+6 MAJOR+3 MINOR）与 R2（4 BLOCKER+5 MAJOR+2 MINOR）全部消化**，含两条最硬的 v2 新洞（recover 熔断 R10、本地缓存防 TOCTOU R11）。

---

## 7. 变更文件清单

- `backend/llm/provider_registry.py`（WI-1：source/account_ref + ensure_provider + KeyMissingError + _normalize_priorities + writer/loader/update）
- `backend/main.py`（WI-2：ensure + relay_logout handler + `_key_fp`；WI-5：relay-only error_class 映射 + 空态）
- `backend/llm/openai_adapter.py`（WI-5：`_map_error` 附 status_code+response_body）
- `backend/llm/errors.py`（WI-5：`LLMAuthError` 带 status_code/response_body；`LLMProviderError.error_class` 已存在）
- `backend/llm/relay_errors.py`（WI-5：`classify_relay_error` 接电，去 TODO 死代码）
- `backend/config.py` / `config.toml`（WI-6：flag + 注释）
- `tauri-app/src/auth/RelayAuthAdapter.ts`（WI-3：ensureLocalApiKey/fetchRelayProviderMeta）
- `tauri-app/src/auth/relayProviderRegistration.ts`（WI-3：新模块，单例+inflight+本地缓存+熔断）
- `tauri-app/src/auth/relayProviderBridge.ts`（WI-6：降级为 flag-off 回退）
- `tauri-app/src/App.tsx`（WI-3：attach[edition 门] + 触发点 + key_missing 监听 + recover 替换 + 登出钩子）
- `tauri-app/src/components/SettingsProviders.tsx` / `AddProviderModal.tsx`（WI-4：source 三态 + 重置按钮 + 退役虚拟行）
- `tauri-app/src/components/RelayEdition.tsx`（WI-3：登录态回调上抛）
- `ARCHITECTURE.md`（WI-6：manual vs relay 表）
- 测试：`test_provider_registry.py`、`test_settings_providers_ws.py`、`test_relay_provider_errors.py`、`relayProviderRegistration.test.ts`、`SettingsProviders.relay.test.tsx`
- 真机：`testcase/2026-06-25-relay-local-apikey-provider/manual-test.md` + `plans/manual-results-2026-06-25-relay-provider/`

---

## 附. 修订记录

**v2（R1 双 lens）修了**：channel 注入（sendControl 虚构）/ WS handler 真实结构 / authedJson refresh / readonly 三态 / error_class 字段名 / 幂等四问雏形（账号+key+域名）/ 重置按钮 / 并发串行化 / BC golden / 明文泄漏风险 / 等 12 项。

**v3（R2 双 lens）修了**：
| R2 缺陷 | v3 处置 |
|---|---|
| BLOCKER `user.sub` 不存在 | 全文 → `user.id`（`User` 无 sub，`currentUser()` 已存在 `:168`）|
| BLOCKER WI-5 文件名错(`openai_compatible`→`openai_adapter`)+ error_class 注入是空壳 TODO | WI-5 钉死 `_map_error:97-128` 附 body + `classify_relay_error` 接电 |
| BLOCKER WI-5 `_map_error` staticmethod 无 entry.source | 映射上提到 chat 消费侧 `main.py:6684`（知 `_entry.source`）|
| BLOCKER 自动 recover 无熔断（死循环）| recover 60s/≥2 次熔断 + 桌宠提示（R10）|
| BLOCKER 幂等读异步 store TOCTOU | registration 本地缓存 lastEnsured，send 后同步写（R11）|
| MAJOR priority 撞号 | 首次严格小于现存 + `_normalize_priorities` |
| MAJOR restore 每次打 relay + catch 误轮换 device_key | reason=restore 缓存命中直接 no-op；fetchRelayProviderMeta 失败返 null 不 rotate |
| MAJOR E2E-4「不用 A 额度」无客观验收 | 日志 `key_fp` 指纹比对 + relay usage 断言（NB5）|
| MAJOR manual inert 前端门未写死 | App.tsx `edition==="relay"&&relayAdapter` 才挂载 + key_missing 监听 guard（NB6）|
| MINOR 登出 vs 在途 recover 竞态 | logout 串入 inflight 链尾（NB7）|
| MINOR inflight 链尾未写死 | `if(this.inflight===p) this.inflight=null`（NB8，范本 `relayProviderBridge.ts:40-89`）|
| MINOR 行号漂移 | secrets `:75`（WS 行号见 v4 回正）|

**v4（R3 收敛验证）修了**：
| R3 缺陷 | v4 处置 |
|---|---|
| BLOCKER WI-5 落点错（异常就地转 ErrorEvent，`error_class` 在发射处只读，永不回 `main.py:6684`）| 改在**抛出处** `openai_adapter._map_error`（去 `@staticmethod`→实例方法，调用点 `self._map_error` 零改）注入；provider 构造带 `is_relay=(_entry.source=="relay")`（`main.py:6685`）；`LLMAuthError.__init__` 扩 `status_code/error_class`（基类 `errors.py:28` 已支持）；`classify_relay_error` 接电 |
| MAJOR WS 行号 v3 反而错 | 回正 `:5758/:5774/:5785/:5810`（grep 实证）|
| MAJOR `setPetError` 不可 import 进 .ts 模块 | `attach(getChannel, onFatal)` 注入回调，App.tsx 传 `(m)=>setPetError(m)` |
| MAJOR `restoreSession` 在 `RelayEdition.tsx:73` 非 App.tsx | `RelayEdition` 加 `onAuthed(reason)` prop 上抛 → App.tsx 触发 ensure |
| MAJOR `App.tsx:942` 无自动重发上下文（注释明示不重发）| 据实标注：recover 成功后用户重发（最小改动），自动重发列为可选后续 |
| MAJOR `relayAdapter` 传 `SettingsPanel` 非 `SettingsProviders` | 经 `SettingsPanel` 再下传一层（或 `onResetRelayKey` 回调）|

> **收敛声明**：R3 已逐项核验 v3 全部 R2 修复落地（仅 WI-5 落点 + 接线偏差需 v4 修正，已修），并确认 `_key_fp` 无泄漏、`onLogout`+inflight 无新竞态、priority 规整无负值隐患、login 路径不会重复铸 key（`needMint=force||!accountOk`，同账号缓存命中→false）。v4 后**无残留 BLOCKER，唯一外部 gap = P0（relay 端点，已被 `01-contract`+mock 隔离）**，判定**100% 可执行**。
