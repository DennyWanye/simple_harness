# 优化 Plan — relay 登录后自动配置「本地 apikey + 可统一管理的 LLM provider」

> **状态**：📋 草案 v6（2026-06-25 立项；v1-v4 = 路线 B「relay 加 `POST /v1/keys`」+ R1/R2/R3 三轮对抗；v5 = 对齐中转站「`/v1/providers` 复用」handoff 重写；**v6 = R5 挑战 v5 改动部分后修订** —— 补 3 BLOCKER：inflight 单槽 dedup 会把 force 合并进 reuse、login 不载缓存、缺「换 key⇒必镜像」不变式；+ 2 MAJOR：既有 `AccountSettingsPanel` CNY 余额与新 USD `balance_minor` 双源冲突、`classify_relay_error` 真要扩参收 code）
> **路线（v5 定）**：**不再要自定义长期 key 端点**。中转站把 `/v1/providers` 改成**复用三态**（默认复用返 `api_key:null`、`?rotate=force` 才重签），device key 自此**稳定**。我方 = 实现中转站三任务（A 持久化 device_id / B 缓存+复用 device key / C 显示账号余额）**＋ 把这把稳定 key 收编进 `LLMProviderRegistry`**，让它在设置面板与手填 provider 一起被正常管理。
> **权威契约**：[`02-relay-handoff-device-key-reuse.md`](./02-relay-handoff-device-key-reuse.md)（中转站 handoff 原文留档）。任务对齐 + 联调 + rollout：[`01-relay-api-contract.md`](./01-relay-api-contract.md)。
> **关联**：根因 [`plans/2026-06-25-relay-cloud-key-sync-followup.md`](../2026-06-25-relay-cloud-key-sync-followup.md)；STATUS §5 P1。
> **两点已拍板**（用户 2026-06-25）：① device_id **维持文件存储**（已稳定、非秘密、零风险），不迁 keyring；② key **单一真相源 = 前端缓存 `deskpet-relay/device_key`**，registration 每次 sync **幂等镜像**进 registry slot `deskpet/provider.relay-cloud`，杜绝漂移。

---

## 0. TL;DR

中转站 handoff 把 device key 从「每调 `/v1/providers` 即换」改成「**复用**」（有 active key 就返 `api_key:null` 让你用本地缓存），device key 自此稳定。这**正好**消除了我们 P1「脱节」的成因，也让「收编成可管理 provider」有了稳定 key 可存。
v5 = 落地中转站**三任务**（A device_id 持久化〔**现状已做**〕/ B 缓存+复用+prefix 自愈+force / C 账号 email+余额+测试号标识）**＋ 把稳定 key 镜像收编进 registry**（前端缓存权威、幂等镜像到后端 chain 读的 slot）。错误处理用中转站新给的结构化 `INSUFFICIENT_BALANCE`；401/缓存失配走 `?rotate=force` 自愈。**外部依赖只剩「中转站灰度开 `DEVICE_KEY_REUSE_ENABLED` flag」，且必须晚于我方发版**（rollout §见下）。

---

## 1. 现状（代码级，已实证 ✓）

### 1.1 两条互不汇合的 provider 路径（不变）
- 路径 A 手填 → `config.toml [[llm.endpoints]]` + keychain `deskpet/provider.<id>`（Python `keyring`）→ chat 每请求 `resolve_api_key()` 取明文构造 provider（`main.py:6684`）。
- 路径 B relay 旁路 → `relayProviderBridge` 经 Tauri IPC `updateCloudConfig()` 热替换单例 `local_llm`，key 不落 registry、前端虚拟 readonly 行（`SettingsProviders.tsx:42-92`）。
- ✅ chain 消费侧确实调 `resolve_api_key` 取**明文**（`main.py:6684`/`5707`/`3038`）；redact 只在 `to_public_dict()`（`provider_registry.py:165`）。「收编进 registry → chain 用真 key」主干不崩。

### 1.2 device key / device_id / 账号 现状（v5 关键 —— 中转站三任务已做多少）
| 中转站任务 | DeskPet 现状（实证）| 结论 |
|---|---|---|
| **A** device_id 持久化 | `src-tauri/src/device.rs`：`ensure_device_id_in`(`:39`)/`get_or_create_device_id`(`:84`) 已把 UUID 存稳定文件 `<user_data>/device_id`(`:15,34`)，校验+幂等（测试 `:134` 证两次同值）| **几乎已做**（文件持久、跨重启不变）；按拍板①**维持文件**，不迁 keyring |
| **B** 缓存 device key 明文 | keyring slot `deskpet-relay/device_key` 已存在，bindings `setRelayDeviceKey`/`getRelayDeviceKey`/`deleteRelayDeviceKey`(`RelayAuthAdapter.ts:85-87`)齐；`fetchProvidersInternal({rotate})`(`:394`) rotate 模式 `:418-422` 已缓存 key、non-rotate `:424-426` 已知「null 不覆盖缓存」| **半成品**：缓存设施全在；缺「复用三态 + 冷启动载缓存 + prefix 失配自愈 + force」|
| **C** 显示账号 email+余额 | `currentUser()` 在；`/v1/usage/summary` 已返 `balance.amount_minor`（`types.ts:80`，测试 `RelayAuthAdapter.test.ts:342`）；**`RelayEdition.tsx` 无任何 email/balance 展示**（grep 空）| **数据半有、展示全缺** → 新 UI |

**当前 rotate 语义错配**（v5 必改）：现 `fetchProvidersInternal` 是二态 —— `listProviders()`→`rotate:true`→`/v1/providers`（默认，**现在=每次换 key**）；`listProvidersUsingCache()`→`rotate:false`→`/v1/providers?rotate=false`（404 时 fallback rotate:true `:382`）。中转站 flag 开后，**默认 `/v1/providers` 变成复用（可能返 null）**，但现 `:418` 逻辑「null 就不缓存、也不回退到缓存」→ 冷启动若拿 null 且 `this.deviceKey` 未从 keyring 载入 → **无 key 可用**。且**无 `?rotate=force` 路径**。`Provider` 类型(`types.ts:62`)有 `api_key?:string|null`(`:69`)但**无 `prefix` 字段**。

### 1.3 脱节 P1（v5 顺手关闭）
旧成因（device_key 每调即换、旧的吊销、且从不写 backend）在中转站复用模式 + 我方收编后**双重消失**：key 稳定（复用）+ 走 registry 按需读（绕开 spawn 固定 env）。

### 1.4 registry 自洽性（不变）
registry key 写读同走 Python `keyring`（service `deskpet`）→ 自洽；WI-7 `survives_restart` 钉死。

### 1.5 复核过的后端锚点（沿用 v4 实证）
- `provider_registry.py`：`ProviderEntry` `:114`；`add_provider` `:416`（构造 `:457`）；`update_provider` `:522`（`elif k=="api_key_ref"` `:566`）；`get_chain` `:579`（无 enabled 抛 `NoProviderConfiguredError` `:587`）；`resolve_api_key` `:408`；keychain service/account `:75-80`；writer `_format_providers_section` `:208`；loader 构造 `:337`；无 asyncio.Lock 靠 WS 串行（`:284`）。
- `main.py` WS handler：`elif msg_type in (...)` **`:5758`**；`_reg` **`:5774`**；`_broadcast_providers_changed` **`:5785`**；`settings_providers_add` 子分支 **`:5810`**；错误内联 `ws.send_json({"type":"settings_providers_error","payload":{"reason":..,"detail":..}})`；WS 单连接 `while True: await ws.receive_json()` 串行。
- chat 用 key：`main.py:6684` `_api_key=_registry.resolve_api_key(_entry.id) or "ollama"`；provider 构造在 `:6685` 区（`_entry.source` 在作用域）。
- error_class 链路：`LLMProviderError.__init__` 已支持 `error_class`/`status_code`（`errors.py:21-39`，`:34` 有 WI-R5 注释预留 relay 分类）；`LLMAuthError`(`:63`) 现硬编码 `status_code=401` 不收 error_class；`_map_error`(`openai_adapter.py:98`，`@staticmethod`，经 `self._map_error` 调用 `:218/:312`) 401/403→`LLMAuthError` 但 error_class 恒 None；`classify_relay_error(status,body)`(`relay_errors.py:40`) 已写好但**零调用**。`ErrorEvent` 发射处 `getattr(exc,"error_class","")` 只读透传 → 前端 `App.tsx:942` + `relayErrorText.ts`。
- 前端：control WS channel 只在 `App.tsx` 闭包（`getControlChannel` `:550`）；全局 zustand `useProvidersStore=create()` `:205`（`.getState().providers` React 外可读）；relay 旁路 `relayProviderBridge.ts`（inflight 范本 `:40-89`、`recoverFromKeyInvalid` `:123`，注意现网**不自动重发 chat**）；`RelayAuthAdapter`：`authedJson<T>(method,path,body?,extraHeaders={})` `:552`（refresh-on-401）、`currentUser():User|null` `:168`、`User` 无 `sub`（有 `id`/`email`，`types.ts:20`）、`fetchProvidersInternal` `:394`、`restoreSession` `:180`、`logout` `:321`。
- Rust：`secrets.rs` `set_cloud_api_key` `:75`、relay 三 slot get/set/delete、`clear_all_relay_secrets` `:174`。

---

## 2. 目标（验收口径）

1. relay edition 登录后，设置面板「LLM Providers」**自动出现** relay provider（id=`relay-cloud`，name「中转站 · chinzy」），与手填同列、可启停/排序/选 default_model；key readonly + 「relay」徽章 + 「重置 key」按钮。
2. device key **稳定复用**：冷启动从 keyring 载缓存；`/v1/providers` 返 `api_key:null` 用缓存；prefix 失配/401/缓存丢 → `?rotate=force` 自愈。**不再每启动换 key**（配合中转站 flag）。
3. key **存本地**（前端 `deskpet-relay/device_key` 权威），**幂等镜像**进 registry `deskpet/provider.relay-cloud`（chain 读它）；换 key 下一回合生效，无需重启；P1 关闭。
4. **登录态显示当前账号 email + 余额 + 测试账号标识**（防登错号，本次报障真因）。
5. 余额不足 → 据结构化 `INSUFFICIENT_BALANCE` 弹「余额不足，请充值」（不误导重登）；key 失效 → 自动 `force` 重签（带熔断防风暴）。
6. `X-Device-Id` 跨重启不变（现状已满足，加回归守）。
7. manual edition **零行为变化**（edition+flag 双门，OFF 字节级 BC + 前端不挂载）。

---

## 3. 架构（v5 数据流）

```
       ┌──────────── App.tsx（getControlChannel + relayAdapter；edition==="relay"&&relayAdapter 才挂载）────────────┐
登录    │ relayProviderRegistration.attach(getControlChannel, onFatal=setPetError)                                   │
restore │ on login(reason=login)/restoreSession.ok(restore)/error_class=relay_key_invalid(→recover)                 │
        │ on settings_providers_error{reason:key_missing,provider_id===relay-cloud} → recover                       │
        └───────────────────────────────────────────┬────────────────────────────────────────────────────────────┘
                                                     ▼
  RelayProviderRegistration.ensure(adapter,{reason})  ← 单例 + inflight 串行化 + 本地缓存(防 TOCTOU) + recover 熔断
   │ ① 冷启动：adapter 启动时 this.deviceKey ??= await getRelayDeviceKey()（载入缓存，复用模式才有 key 可用）
   │ ② 取 key：adapter.syncDeviceKey({force?})  ──┐
   ▼                                              ▼
   adapter.syncDeviceKey：
     - reason=restore 且缓存在+账号同 → 直接用 this.deviceKey（不打 relay）
     - 否则 GET /v1/providers（复用三态）：api_key 有 → 缓存覆盖(setRelayDeviceKey)；api_key:null → 用 this.deviceKey；
       prefix 失配 / null 且无缓存 / 401 → GET /v1/providers?rotate=force（拿新明文+缓存）
     → 返回「当前有效 device key 明文」+ prefix
   │（device key 权威落点 = 前端 keyring deskpet-relay/device_key）
   ▼ 幂等镜像进 registry（拍板②）
   getControlChannel().send({type:"settings_providers_ensure", payload:{
       id:"relay-cloud", source:"relay", account_ref:currentUser().id,
       name:"中转站 · chinzy", base_url, models, default_model, api_key:<当前 device key> }})
   写 lastEnsured 本地缓存(account_ref + keyPresent)；
   │
   ▼ backend WS（msg_type/raw.payload/内联 send_json；日志只打 key_fp）
   LLMProviderRegistry.ensure_provider → keyring.set_password("deskpet","provider.relay-cloud",key)
       + config.toml upsert(id/source/account_ref) + priority 去重 + _broadcast_providers_changed()
   │
   ▼ 每个 chat：resolve_provider_for_session → get_chain() → resolve_api_key("relay-cloud")（按需读，即时生效）

错误：chat 收 relay 403 {code:INSUFFICIENT_BALANCE} → ErrorEvent{error_class:insufficient_balance,balance_minor} → 前端「余额不足，请充值」
      chat 收 relay 401 → ErrorEvent{error_class:relay_key_invalid} → App.tsx:942 → recover（syncDeviceKey force；60s≥2 次仍失败→熔断+桌宠提示）→ 重发由用户/可选自动

账号显示（任务C）：登录后 /v1/me → {email, balance_minor, is_test_account} → RelayEdition/主界面醒目展示 + 测试号徽章
```

---

## 4. 工作项（WI）

> 依赖：WI-A（device_id，几乎已done）→ WI-B（device key 复用/缓存/自愈）→ WI-1（registry 字段/ensure）→ WI-2（WS）→ WI-3（registration 镜像）→ WI-4（UI 管理）→ WI-C（账号余额展示）→ WI-5（结构化错误）→ WI-6（flag/登出/清理）→ WI-7（测试+E2E）。
> 门控 flag `[features].relay_managed_provider`（relay edition 默认 ON；manual inert）。**注意中转站侧另有 `DEVICE_KEY_REUSE_ENABLED`，rollout §见 §6。**

### WI-A —— device_id 持久化（任务 A，现状已满足，仅补确认 + 告知 relay）
- **维持** `device.rs` 文件存储（拍板①）。不迁 keyring。
- 加回归测试 `test_device_id_stable_across_calls`（已有等价 `device.rs:134`，确认覆盖即可）。
- **联调动作**：告知中转站「device_id 已文件持久化、跨重启稳定，满足任务 A 的稳定性要求，故不迁 keyring」（见 `01` checklist）。

### WI-B —— device key 复用三态 + 冷启动载缓存 + prefix 自愈 + force（任务 B，核心改造）
**文件** `RelayAuthAdapter.ts`、`types.ts`。

1. **`Provider` 类型加 `prefix?: string`**（`types.ts:62`，中转站每 provider 返 key 前缀 `tsk_xxxxxxxx`；实证无字段冲突）。
2. **`fetchProvidersInternal` 改三态 + 返回 prefix + ⚠️按 mode 分槽 dedup（修 B1）**：现 `:402` 是**单槽 `inflightProviders`**——三态下会把 `force`（要拿新明文）错误合并进在途 `reuse`（返 null 不缓存）→ force 失效。**必须改**：
   ```ts
   // 字段：private inflightByMode = new Map<"reuse"|"meta", Promise<...>>();  // force 永不 dedup
   private async fetchProvidersInternal(opts: { mode: "reuse"|"meta"|"force" })
       : Promise<{ providers: Provider[]; keyPrefix: string }> {
     if (!this.accessToken) return { providers: [], keyPrefix: "" };
     if (opts.mode !== "force") {                          // reuse/meta 可 dedup；force 绝不（语义=我就要换）
       const live = this.inflightByMode.get(opts.mode); if (live) return live;
     }
     const run = (async () => {
       const path = opts.mode === "meta" ? "/v1/providers?rotate=false"
                  : opts.mode === "force" ? "/v1/providers?rotate=force" : "/v1/providers";
       const data = await this.authedJson<ProvidersResponse>("GET", path, undefined,
                      { "X-Device-Id": this.deviceId ?? "", "X-Device-Name": this.deviceName ?? "" });
       const providers = data.providers ?? [];
       const keyPrefix = providers.find(p => p.prefix)?.prefix ?? "";   // 响应自带 prefix(省一次 meta 往返, 修 M3)
       if (opts.mode !== "meta") {
         const fresh = providers.find(p => p.api_key)?.api_key ?? null;
         if (fresh) { await this.setDeviceKey(fresh); }   // 唯一换 key 出口(修 B3，见步4)
         // reuse 返 null → 保留缓存(复用本意)
       }
       this.emit({ type: "providers-updated", providers });
       return { providers, keyPrefix };
     })();
     if (opts.mode !== "force") this.inflightByMode.set(opts.mode, run);
     try { return await run; } finally { if (opts.mode !== "force") this.inflightByMode.delete(opts.mode); }
   }
   ```
   ⚠️ 现有调用点 `listProviders()`(`:350`)/`listProvidersUsingCache()`(`:370`) 全部改走新签名（`.providers`）。
3. **冷启动载缓存（修 B2：login 也要载，不止 restore）**：把 `this.deviceKey ??= await this.bindings.getRelayDeviceKey()` 挂在**所有进入登录态的入口** —— `restoreSession`(`:180`，现已有 `:188`)**＋ `login()`(`:209`) / `register()` 成功后**（现网 login 全程不设 deviceKey，换号/新登首聊会无 key）。
4. **单一换 key 出口 + 不变式（修 B3）**：新增私有 `private async setDeviceKey(key){ this.deviceKey=key; await this.bindings.setRelayDeviceKey(key); }` 为**唯一**写 key 处（现 `:420-421` 内联收编进它）。**不变式：换 key ⇒ 必触发 registration 镜像**——所有 device key 刷新只经 `syncDeviceKey`→`registration.once`→`ch.send(ensure)`；**退役** `relayProviderBridge.recoverFromKeyInvalid`（`:123`）等任何「绕过 registration 直接刷 key」的旁路（WI-6 flag off 才留）。验收：grep 全仓 `setRelayDeviceKey`/`fetchProvidersInternal({mode:"force"})` 调用点，逐个确认都在 ensure 镜像同步链上。
5. **新增 `syncDeviceKey`（统一取「当前有效 key」+ 自愈；省掉冗余 meta，修 M3/M4）**：
   ```ts
   async syncDeviceKey(opts?: { force?: boolean }): Promise<{ key: string; prefix: string } | null> {
     this.deviceKey ??= await this.bindings.getRelayDeviceKey();          // 冷启动兜底
     if (opts?.force) {
       const r = await this.fetchProvidersInternal({ mode: "force" });    // 拿新明文(已落缓存)
       return this.deviceKey ? { key: this.deviceKey, prefix: r.keyPrefix } : null;
     }
     const r = await this.fetchProvidersInternal({ mode: "reuse" });      // 复用：有明文→落缓存；null→保留
     const serverPrefix = r.keyPrefix;                                    // 响应自带，无需再 meta
     const cached = this.deviceKey;
     const mismatch = !!cached && !!serverPrefix && !cached.startsWith(serverPrefix);
     if (!cached || mismatch) {                                           // 缺/失配 → force 自愈
       const f = await this.fetchProvidersInternal({ mode: "force" });
       return this.deviceKey ? { key: this.deviceKey, prefix: f.keyPrefix } : null;
     }
     return { key: cached, prefix: serverPrefix };
   }
   /** registration 取 base_url/models 用，不轮换 */
   async fetchRelayProviderMeta(): Promise<Provider | null> {
     try { return (await this.fetchProvidersInternal({ mode: "meta" })).providers[0] ?? null; } catch { return null; }
   }
   ```
   （取代 v4 的 `ensureLocalApiKey`/`POST /v1/keys`；正常 sync = 1 次 relay 调用，自愈 = 2 次。）

**测试**（vitest）：reuse 返 null→用缓存；首发/force 返明文→落 keyring；prefix 失配→force；冷启动 `getRelayDeviceKey` 载入；**login() 也载缓存**（B2）；无缓存+null→force；**`force` 并发不被 reuse 合并**（B1：mock 在途 reuse 时发 force，断言真打 `?rotate=force`）；meta 不覆盖缓存；**grep 守 `setRelayDeviceKey` 单一出口**（B3）。

### WI-1 —— backend：`ProviderEntry` 加 `source`+`account_ref`；`ensure_provider` 幂等 upsert（key 缺失强铸 + priority 去重）
（与 v4 一致，未受 handoff 影响）
- `ProviderEntry`(`:114`) 加 `source:str="user"`、`account_ref:str=""`；writer(`:208`)仅非默认才 emit；loader(`:337`)/`add_provider`(`:457`) 透传；`update_provider`(`:566` 前)加 `source`/`account_ref` 分支。
- `class KeyMissingError(RuntimeError)`；`ensure_provider`（不存在→add[registry 空时 priority 严格小于现存]，存在→update 可变字段；key 丢失且未带 key→抛 KeyMissingError）；`_normalize_priorities`（重排 1..N 唯一）。
- **测试**：source/account_ref roundtrip；user 行不写；ensure 幂等/保留 reorder/更新 key/更新 base_url+models；priority 不撞号；key_missing 抛错。

### WI-2 —— backend：WS `settings_providers_ensure` + `settings_providers_relay_logout`
（与 v4 一致）
- `"settings_providers_ensure"`/`"settings_providers_relay_logout"` 加进 `:5758` 元组；CRUD 子分支(`:5810` 同级)加两 case：ensure（source 必须 relay，调 `ensure_provider`，日志 `key_fp=_key_fp(...)` 不打明文，KeyMissingError→回 `{reason:key_missing}`）；relay_logout（`update_provider(enabled=False,account_ref="")`+`_keychain_delete("relay-cloud")`+广播）。`_key_fp(k)=None if not k else sha256(k)[:8]`。
- **测试**：ensure 出现在 list（source/account_ref/key redacted）；拒非 relay；key_missing 回 error；handler 永不打明文（caplog）；relay_logout 删 key+disable。

### WI-3 —— 前端：registration 把稳定 key 镜像进 registry（拍板② + 幂等四问 + 熔断；替代 v4 铸 key）
**文件（新增）** `relayProviderRegistration.ts`；**改** `App.tsx`、`RelayEdition.tsx`。

```ts
const RELAY_PROVIDER_ID = "relay-cloud";
type EnsureReason = "login" | "restore" | "recover";
export class RelayProviderRegistration {
  private getChannel: (() => Channel|null)|null = null;
  private onFatal: ((m:string)=>void)|null = null;
  private inflight: Promise<void>|null = null;
  private lastEnsured: { accountRef:string; keyPresent:boolean }|null = null;  // 防 TOCTOU(读自己缓存非异步 store)
  private recoverHits: number[] = [];
  attach(getChannel:()=>Channel|null, onFatal:(m:string)=>void){ this.getChannel=getChannel; this.onFatal=onFatal; }

  ensure(adapter:RelayAuthAdapter, reason:EnsureReason="login", force=false): Promise<void> {
    const p=(this.inflight??Promise.resolve()).then(()=>this.once(adapter,reason,force),()=>this.once(adapter,reason,force));
    this.inflight=p; p.finally(()=>{ if(this.inflight===p) this.inflight=null; }); return p;       // 链尾写死
  }
  recover(adapter:RelayAuthAdapter): Promise<void> {
    const now=Date.now(); this.recoverHits=this.recoverHits.filter(t=>now-t<60000);
    if(this.recoverHits.length>=2){ this.recoverHits=[]; this.onFatal?.("中转站 key 反复失效，请重新登录或检查余额"); return Promise.resolve(); }
    this.recoverHits.push(now); return this.ensure(adapter,"recover",true);
  }
  private async once(adapter:RelayAuthAdapter, reason:EnsureReason, force:boolean){
    const acct=adapter.currentUser()?.id ?? "";
    const ok=!!this.lastEnsured && this.lastEnsured.accountRef===acct && this.lastEnsured.keyPresent;
    if(reason==="restore" && ok && !force){ return; }                          // restore 命中 → 不打 relay
    const synced=await adapter.syncDeviceKey({ force: force || !ok });          // 取/自愈 当前稳定 key
    if(!synced){ console.warn("[reg] no device key"); return; }
    const meta=(await adapter.fetchRelayProviderMeta?.()) ?? null;             // base_url/models（不轮换）
    const models=(meta?.models??[]).map(m=>m.id); if(!models.length){ console.warn("[reg] empty models"); return; }
    const ch=this.getChannel?.(); if(!ch){ console.warn("[reg] no channel"); return; }
    ch.send({ type:"settings_providers_ensure", payload:{ id:RELAY_PROVIDER_ID, source:"relay", account_ref:acct,
      name:"中转站 · chinzy", base_url:meta!.base_url, models, default_model:pickModel(meta!), api_key:synced.key }});
    this.lastEnsured={ accountRef:acct, keyPresent:true };
  }
  onLogout(){ this.lastEnsured=null; this.recoverHits=[]; }
}
export const relayProviderRegistration = new RelayProviderRegistration();
```
- `fetchRelayProviderMeta()` = `RelayAuthAdapter` 薄封装 `fetchProvidersInternal({mode:"meta"})` 取 base_url/models（不轮换）。
- **挂载（`edition==="relay"&&relayAdapter` 才挂；防 NB6）**：App.tsx 启动 `attach(getControlChannel,(m)=>setPetError(m))`；`relayAdapter.on("login")`→`ensure(...,"login")`；`RelayEdition.tsx:73` restore 成功经新 `onAuthed("restore")` prop 上抛 App.tsx→`ensure(...,"restore")`；替换 `App.tsx:943`→`recover(relayAdapter)`（⚠️ 现网不自动重发 chat，recover 成功后**用户重发**，自动重发列可选后续）；监听 `settings_providers_error{reason:key_missing,provider_id===relay-cloud}`→`recover`；登出→`onLogout()`+发 `settings_providers_relay_logout`。

**测试**：absent→sync+send；restore 缓存命中不打 relay；account 切换→force+新 account_ref；back-to-back 读本地缓存只 sync 一次；recover 60s≥2 熔断；并发串行化。

### WI-4 —— 前端 UI：relay「受限可编辑」三态 + 「重置 key」按钮
（与 v4 一致）
- `Provider` 加 `source?:"user"|"relay"; account_ref?:string`；退役虚拟行 `relayListToChinzy`(`:42-92`)；`isRelayProvider` 改判 `source==="relay"`。
- `SortableRow` relay 态：可拖拽排序 + relay 徽章 + 保留 enable/default_model + 删除替换为「🔄 重置 key」→ `onResetKey`→`relayProviderRegistration.recover(relayAdapter)`（force 重签）。`relayAdapter` 现传 `SettingsPanel`(`:2233`)→需经 SettingsPanel 再下传一层到 `SettingsProviders`。
- `AddProviderModal` relay draft 禁 id/base_url/api_key。
- **测试**：relay 行徽章+可拖拽+可启停+重置按钮调 recover；user 行不变。

### WI-C —— 前端：登录态显示账号 email + 余额 + 测试账号标识（任务 C，复用既有面板，修 M1 双余额源冲突）
> ⚠️ **R5 实证**：仓库**已有** `tauri-app/src/auth/AccountSettingsPanel.tsx`，已渲染「账户余额/钱包余额」用 `/v1/usage/summary` 的 `balance.amount_minor` 按 **CN¥**（`formatCny` `:63`，¥713.17）。中转站新 `/v1/me.balance_minor` 是 **USD-cents**（$756.14）。**绝不能再加第二个 USD 余额** → 会出现「¥713.17」「$756.14」双源双币种打架（`feedback_cross_layer_contract`）。
1. **先与中转站澄清币种**（`01` checklist 加一问）：`/v1/me.balance_minor`(USD) 与 `/v1/usage/summary.balance.amount_minor`(CNY) 是**同一个钱包的不同币种展示**，还是两个账？据答复定唯一余额源。
2. **`User` 类型加** `balance_minor?: number`、`is_test_account?: boolean`（`types.ts:20`）。`fetchMe()`(`:539`) 取 `/v1/me` 自动带上。
3. **复用 `AccountSettingsPanel`**（不新建余额 UI）：
   - **email + 测试账号徽章**：在 `AccountSettingsPanel` 现有账号区 + 登录后主界面账号入口，显示 `currentUser().email`；`is_test_account` → 显眼「测试账号」徽章（红/黄）。
   - **余额**：按第 1 点澄清结果**收口到单一源 + 单一币种**（推荐：若同一钱包，统一用 `usage_summary` 既有 CNY 渲染，`/v1/me.balance_minor` 仅作 `is_test_account` 判定与一致性校验；若两个账，明确各自标签）。不在 RelayEdition 另起一个 USD 余额。
4. **余额刷新挂点（修「没落地的 TODO」）**：登录成功 + 收到 `insufficient_balance` 错误时，调既有 `usage_summary`/`fetchMe` 刷新并更新 `AccountSettingsPanel`/账号入口显示（明确挂在这两个真实事件，不是泛泛"周期"）。
**测试**（vitest）：渲染 email；测试账号显徽章；余额单一源单一币种（无 USD/CNY 并存）；余额 0 显警示。

### WI-5 —— backend：结构化 `INSUFFICIENT_BALANCE` + 401 自愈（比 v4 更简单，中转站给了 code）
**文件** `openai_adapter.py`、`errors.py`、`relay_errors.py`、`main.py:6685`。
1. `_map_error`(`openai_adapter.py:98`) 去 `@staticmethod`→实例方法（调用点 `:218/:312` 已 `self._map_error`，零改）；`OpenAICompatibleProvider.__init__` 加 `is_relay:bool=False`，`main.py:6685` 构造时传 `is_relay=(_entry.source=="relay")`（派生 provider 默认 False）。
2. `_map_error` 401/403 分支：`resp=getattr(exc,"response",None)`；**优先取结构化 code**：`body=getattr(exc,"body",None)`（openai SDK `APIStatusError.body` 是 parsed JSON dict）→ `code=body.get("code") if isinstance(body,dict) else None`；`text=getattr(resp,"text","")` 作 fallback。`if self._is_relay: ec=classify_relay_error(status, text, code=code)`（见 3）→ `LLMAuthError(str(exc), provider=self.name, status_code=status, error_class=ec)`。⚠️ **status_code 必须透传真实值（403 不能压成 401）**，否则前端分不清 `insufficient_balance`(403) vs `relay_key_invalid`(401)。
3. **`classify_relay_error`(`relay_errors.py:11`) 扩签名 + 接电**（现 `(status_code, body_text="")` 纯文本启发式 `:23-31`，余额先判）：加第三参 `code: str|None=None`，**优先按 code**：`if code=="INSUFFICIENT_BALANCE": return INSUFFICIENT_BALANCE`，否则回落现有「402/text 含 insufficient → INSUFFICIENT_BALANCE；401/403 → RELAY_KEY_INVALID」（注：中转站结构化 body 文本本就含 `insufficient`，现有 `_BALANCE_HINTS` 已能命中，但显式 code 更稳）。该函数现**零调用=死代码**，本 WI 让 `_map_error` 真调它。`LLMAuthError.__init__`(`errors.py:66`) 现写死 `status_code=401` 不收 `error_class` → **扩参** `def __init__(self,message,*,provider=None,status_code=401,error_class=None)` 转基类（基类 `:21-39` 已支持 `status_code`/`error_class`）。返回值前端映射：`INSUFFICIENT_BALANCE`→`insufficient_balance`、`RELAY_KEY_INVALID`→`relay_key_invalid`（与 `error_class` 字符串对齐 `App.tsx:942`）。
4. **前端**：`insufficient_balance`→「账号余额不足，请充值」+ 充值入口（`relayConfig.ts` console URL）+ 显示 `balance_minor`，**不提重登**；`relay_key_invalid`→`recover`（WI-3，syncDeviceKey force）；chain 空态唯一 disabled relay→`relay_logged_out`「已登出，重新登录后恢复」。`relayErrorText.ts`+`App.tsx:942` 增分支。
**测试**：relay 403 `INSUFFICIENT_BALANCE`→insufficient_balance；relay 401→relay_key_invalid；manual 401 不被映射；`classify_relay_error` 真被调；disabled-relay→logged_out。

### WI-6 —— flag/登出清 key（串 inflight）/ 文档
（与 v4 一致）flag `[features].relay_managed_provider`（OFF 保留旧旁路，字节 BC）；登出 `onLogout()`+logout 串 inflight 链尾（防在途 recover 复活 A 的 key）+ backend `settings_providers_relay_logout` 删 key；`ARCHITECTURE.md §3` + `config.py` 注释（cloud-llm slot+env 仅 manual/legacy）。

### WI-7 —— 测试矩阵 + BC + 真机 E2E
- 单测各 WI 全绿 + 关 flag 全套不回归；**BC golden** `test_manual_edition_toml_byte_identical` + 前端 `manual_edition_does_not_attach_registration`；持久化 `test_relay_provider_key_survives_restart`；device_id 稳定回归。
- **真机 windows-mcp E2E**（HARD：真点击+真输入+截图+抓 backend 日志）：
  - E2E-1 登录→设置面板出现 relay-cloud 行（徽章+key readonly+重置按钮）+ **登录区显示 email+余额+测试号徽章**（任务 C）。
  - E2E-2 聊天→日志走 `provider_chain`(relay-cloud) 非旁路 + `key_fp=` + 真回复。
  - E2E-3 重启 app→行在、**device key 复用不变**（同 prefix/同 key_fp，relay key 表不增）+ device_id 同 UUID。
  - E2E-4 多账号：登出 A→登录 B→`account_ref` 变 B + chat `key_fp`==B≠A（证不用 A 额度）。
  - E2E-5 缓存失配自愈：手动改/清 `deskpet-relay/device_key`→下次 sync prefix 失配/缺→`?rotate=force` 重签恢复。
  - E2E-6 重置按钮→force 重签（key_fp 变）→聊天用新 key。
  - E2E-7 余额 0 测试账号→403 `INSUFFICIENT_BALANCE`→前端「余额不足，请充值」（非静默）；持续 401→熔断停刷屏。
  - ⚠️ E2E-2~5 的「复用」行为需中转站 **flag 开**后才完整可测（见 §6）；flag 关时退化为现状（每调换 key），E2E 仍应跑通「收编+管理+账号显示」这些不依赖 flag 的部分。

---

## 5. 风险 / 未决（v5）

| # | 风险 | 处置 |
|---|---|---|
| R1 | 中转站 flag 未开前「复用」不生效 | flag 关=现状（每调换 key），我方代码两态兼容（有明文就缓存、null 就用缓存）；rollout §6 先发版后开 flag。 |
| R2 | Python keyring persist scope | WI-7 survives_restart + E2E-3 复验；registry 写读同库自洽。 |
| R3 | key 漂移（前端缓存 vs registry 两份）| 拍板②：前端权威 + 每次 sync 幂等镜像；ensure 幂等自校正。 |
| R4 | 5-key 上限淘汰致缓存 key 被吊销 | prefix 失配/401→`?rotate=force` 自愈 + recover 熔断。 |
| R5 | 多账号串号 | account_ref=user.id + 幂等②校验 + 登出删 key。 |
| R6 | recover 死循环/风暴 | 60s≥2 次熔断 + 桌宠提示（不自动无限重发）。 |
| R7 | 幂等读异步 store TOCTOU | registration 本地缓存 lastEnsured，send 后同步写。 |
| R8 | 明文 key 经 WS/日志泄漏 | WS loopback+shared-secret；日志只 `key_fp` sha256[:8]。 |
| R9 | flag/edition OFF 回归 | 双门 + golden + manual 不挂载断言。 |
| R10 | 登出 vs 在途 recover 复活 key | logout 串 inflight 链尾。 |
| R11 | rollout 顺序反了（flag 先于发版）| §6 明确：阶段 1 发版 + 告知版本 → 阶段 2/3 才开 flag；断点风险写死。 |

---

## 6. Rollout 协调（与中转站，顺序不可反）

按中转站 handoff §5：
1. **阶段 0（现在）**：中转站代码合并、`DEVICE_KEY_REUSE_ENABLED` **默认关** → 线上不变，现客户端可用。**我方现在就能做** WI-1~6（收编/UI/账号显示/错误）——这些**不依赖 flag**，「收编成可管理 provider + 显示账号 + 结构化错误」全部生效。⚠️ **如实记**（修 M5）：flag 关时 `syncDeviceKey` 默认走 reuse 调，但中转站 reuse 是 flag 开后才有 —— flag 关时 `/v1/providers` 仍**每调即新签**→ 每次登录 device key 变 → registry 镜像随之更新一把新 key（**功能正常，但 key 抖动 + 我方阶段0 发版仍 +1 key/登录贡献表增长**）。靠中转站「每账号 5 key 上限淘汰」纯后端兜底摁住，**阶段 2 开 flag 后抖动消失**（reuse 命中即返 null、复用同把）。
2. **阶段 1**：我方完成 WI-A~7（尤其 **WI-B 缓存+复用**）发版 → **告知中转站版本号/时间**（`01` checklist）。
3. **阶段 2**：中转站对已升级客户端**灰度开** flag → 我方 E2E-2~5 复用相关项此时才完整可测；观察 key 表停增 + 无「无 key」报错。
4. **阶段 3**：全量开。
> ⚠️ **断点铁律**：WI-B（缓存明文）未发版前若开 flag，旧客户端复用拿 null+本地没存→不可用。故阶段 2/3 必晚于阶段 1。中转站另有「每账号 5 key 上限淘汰」纯后端兜底，先摁表爆炸，不依赖我方。

---

## 7. 变更文件清单

- `backend/llm/provider_registry.py`（WI-1）· `backend/main.py`（WI-2 + WI-5 映射 + WI-6）· `backend/llm/openai_adapter.py`（WI-5 `_map_error` 实例化+is_relay）· `backend/llm/errors.py`（WI-5 LLMAuthError 扩参）· `backend/llm/relay_errors.py`（WI-5 按 code 分类+接电）· `backend/config.py`/`config.toml`（WI-6 flag）
- `tauri-app/src/auth/types.ts`（WI-B `Provider.prefix`；WI-C `User.balance_minor`/`is_test_account`）· `RelayAuthAdapter.ts`（WI-B 三态 rotate+syncDeviceKey+冷启动载缓存+fetchRelayProviderMeta；WI-C fetchMe 带新字段）· `relayProviderRegistration.ts`（WI-3 新模块）· `relayProviderBridge.ts`（WI-6 降级回退）· `App.tsx`（WI-3 attach/触发/监听/recover；WI-C 账号区）· `SettingsProviders.tsx`/`AddProviderModal.tsx`（WI-4）· `RelayEdition.tsx`（WI-3 onAuthed 上抛；WI-C email+余额+测试号展示）
- `src-tauri/src/device.rs`（WI-A 维持文件，仅补测试确认）
- `ARCHITECTURE.md`（WI-6）
- 测试：`test_provider_registry.py`、`test_settings_providers_ws.py`、`test_relay_provider_errors.py`、`relayProviderRegistration.test.ts`、`RelayAuthAdapter.deviceKeyReuse.test.ts`、`SettingsProviders.relay.test.tsx`、`RelayEdition.account.test.tsx`
- 真机：`testcase/2026-06-25-relay-local-apikey-provider/manual-test.md` + `plans/manual-results-2026-06-25-relay-provider/`

---

## 附. 修订记录
- **v1-v4**：路线 B「relay 加 `POST /v1/keys` 长期端点」+ R1/R2/R3 三轮对抗，消化 8 BLOCKER+15 MAJOR+5 MINOR（channel 注入/WS 结构/authedJson/account_ref=user.id/error_class 抛出处注入/recover 熔断/TOCTOU 本地缓存/priority 去重/readonly 三态/重置按钮/BC golden 等）。
- **v5（中转站 handoff 对齐）**：路线从「自定义长期端点」改为「`/v1/providers` 复用三态」。**保留**全部收编/UI/account_ref/熔断/TOCTOU/priority/BC 设计（WI-1/2/3/4/6/7 主体）；**改写** key 来源 = WI-A（device_id 已 done）+ WI-B（复用/缓存/prefix 自愈/force）取代铸 key；**简化** WI-5；**新增** WI-C（账号显示）+ §6 rollout。两点拍板：device_id 维持文件、key 前端缓存权威+幂等镜像。
- **v6（R5 挑战 v5 改动部分）修了**：
| R5 缺陷 | v6 处置 |
|---|---|
| BLOCKER `inflightProviders` 单槽 dedup（`:402`）把 force 合并进在途 reuse → force 失效 | 按 mode 分槽 `inflightByMode`，**force 永不 dedup** |
| BLOCKER `login()`/`register()` 不载 `this.deviceKey`，冷启动只挂 restoreSession → 新登/换号首聊无 key | 冷启动载缓存挂到**所有登录态入口**（login/register/restore）|
| BLOCKER 缺「换 key⇒必镜像」不变式 → 前端缓存换了但 registry 陈旧 → chat 旧 key → 401 死循环 | 单一换 key 出口 `setDeviceKey`；所有刷新经 `syncDeviceKey→registration→ensure`；退役 bridge 旁路；grep 验收 |
| MAJOR 既有 `AccountSettingsPanel`(CNY `amount_minor`) 与新 `/v1/me.balance_minor`(USD) 双源冲突 | WI-C 复用既有面板、先与中转站澄清币种、收口单一余额源，不加第二个 USD 余额 |
| MAJOR `classify_relay_error` 真要扩参收 `code`、`_map_error` 取 `exc.body` dict、`LLMAuthError` 别压扁 403→401 | WI-5 步2/3 明确扩签名 + `exc.body` + status_code 透传 |
| MINOR 一次 sync 打 relay 2-3 次（冗余 meta）| `fetchProvidersInternal` 返回带 `keyPrefix`，删 meta 往返，正常 sync 1 次 |
| MINOR flag 关阶段 registry 镜像抖动未承认 | §6.1 如实记 + 5-key 兜底 |

> **收敛声明**：v6 后 v5 改动部分（WI-B/WI-C/WI-5/镜像/rollout）3 BLOCKER + 2 MAJOR 全部消化；收编主体（WI-1/2/3/4/6）在 v1-v4 已三轮收敛。**唯一外部 gap = 中转站灰度开 `DEVICE_KEY_REUSE_ENABLED`，且必须晚于我方发版（§6 断点铁律）。**
