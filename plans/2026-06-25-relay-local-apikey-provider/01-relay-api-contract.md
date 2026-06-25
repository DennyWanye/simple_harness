# relay 侧 API 契约 — 长期本地 API key 端点（路线 B 的前置 P0）

> **给谁**：relay（Token Relay / chinzy.com）服务团队。
> **为什么**：DeskPet 客户端要在登录后**铸一把绑定账号、不轮换、可吊销的长期 API key**，存本地 keychain 供 backend
> 长期使用（取代会轮换吊销的 `device_key`）。现有 `GET /v1/providers` 的 device_key 每调一次就作废上一把
> （`DESKPET-INTEGRATION-GUIDE.md:64,752`），不能当 backend 的稳定凭据。
> **本契约一旦实现，DeskPet 客户端（已按此写好）即可联调。** 与现有 `device_key` 机制并存、不冲突。

---

## 1. 语义要求（硬）

1. **长期、不轮换**：铸出的 key 不因任何其它调用（尤其 `GET /v1/providers`）而失效；只在显式 `DELETE` 或用户在 console 吊销、或账号停用时失效。
2. **绑定账号 + 设备**：key 绑定 `(account, deviceId, purpose)`。`deviceId` 来自请求头 `X-Device-Id`（与现有 device_key 同一套 deviceId 模型，`GUIDE:65`）。
3. **幂等**：同 `(account, deviceId, purpose)` 重复 `POST` **默认返回同一把已存在的 key**（不新建、不堆积），除非 `force=true` 才吊销旧的并铸新的。这条对客户端「每次登录都可能调一次」至关重要，避免 key 爆炸。
4. **鉴权**：用 `Authorization: Bearer <access_token>`（登录拿到的 JWT）。**不是** device_key、不是旧 API key。
5. **可吊销**：console 设备页可单独吊销；`DELETE /v1/keys/{id}` 程序化吊销。吊销后该 key 调 LLM 返 401。
6. **明文只在创建响应里返回一次**（与 device_key 一致，`GUIDE:64`）；`GET /v1/keys` 列表只返回**前缀/掩码 + key_id + 元数据**，不回明文。
7. **可用于全部 LLM 路径**：该 key 能直接打 `POST /v1/chat/completions`、`/v1/images/generations`、`/anthropic/v1/messages` 等（与 device_key 鉴权等价；推荐 `relay-openai` 兼容路径，`GUIDE:316`）。

---

## 2. 端点

### 2.1 `POST /v1/keys` — 铸 / 取长期 key（幂等）

**请求**
```
POST /v1/keys
Authorization: Bearer <access_token>
X-Device-Id: <deviceId>            # 与 /v1/providers 同一 deviceId
Content-Type: application/json

{
  "purpose": "deskpet-local",      # 固定值，区分用途命名空间
  "label": "DennyWanye-PC",        # 可选，给 console 展示用（deviceName）
  "force": false                    # true=吊销旧的并铸新的（401 重铸用）
}
```

**响应 200（新建或幂等返回已存在）**
```json
{
  "key": "tsk_live_9f3a...",        // 明文，仅此一次返回
  "key_id": "klk_abc123",           // 稳定 id，用于 DELETE / 列表
  "purpose": "deskpet-local",
  "device_id": "uuid...",
  "label": "DennyWanye-PC",
  "created_at": "2026-06-25T12:00:00Z",
  "rotated": false                  // force=true 时为 true（提示已吊销旧 key）
}
```

**错误**（沿用 §4.1 错误信封）
- `401 INVALID_TOKEN`：access_token 失效 → 客户端走 refresh 后重试；refresh 也失败 → 要求重登。
- `403 FORBIDDEN`：账号停用/无权限。
- `429`：限流（带 `Retry-After`）。

> **幂等实现建议**：服务端按 `(account_id, device_id, purpose)` 唯一约束存 key；`POST` 命中已存在且未吊销 → 直接回那把（明文需可重出，或：若不能重出明文，则改为「已存在则不回明文、要求客户端先 DELETE 再建」——**但更推荐能重出明文的幂等**，否则客户端丢了 key（keychain 被清）就无法恢复，只能 force 重铸）。请在回复里确认你们能否「幂等重出明文」。

### 2.2 `GET /v1/keys` — 列出本账号的长期 key（掩码）

```
GET /v1/keys?purpose=deskpet-local
Authorization: Bearer <access_token>
```
```json
{ "keys": [
  { "key_id": "klk_abc123", "masked": "tsk_live_…f3a", "purpose": "deskpet-local",
    "device_id": "uuid...", "label": "DennyWanye-PC", "created_at": "...", "last_used_at": "..." }
] }
```

### 2.3 `DELETE /v1/keys/{key_id}` — 吊销

```
DELETE /v1/keys/klk_abc123
Authorization: Bearer <access_token>
```
- 200 → 已吊销；该 key 后续调 LLM 返 401。客户端登出/「重置 key」时调。

---

## 3. 与现有机制的关系

- **与 `device_key` 并存**：现有 `GET /v1/providers` 的轮换 device_key 机制**不动**；长期 key 是新增的、并行的凭据类别。DeskPet 改用长期 key 后，`GET /v1/providers` 仅用于取 `base_url`/`models`（可继续支持 `?rotate=false` 只读不轮换，`DESKPET-INTEGRATION-REPLY.md:82`）。
- **console**：设备页已支持「设备 key 与手动 API key 分页管理」（`GUIDE:66`）——本契约即把「手动 API key」**API 化**（程序可铸/列/删），并加 `purpose=deskpet-local` 命名空间区分自动铸的本地 key。
- **余额/usage**：长期 key 的用量并入账号总额（与 device_key 同账号同池），`GET /v1/usage/summary` 口径不变。

---

## 4. 需 relay 团队确认的点（回复本契约时勾选）

- [ ] `POST /v1/keys` 能否**幂等重出明文**（§2.1 建议）？若否，客户端需在 keychain 丢失时 `force` 重铸——可接受但请确认。
- [ ] 端点路径用 `/v1/keys` 还是你们既有命名（如 `/v1/api-keys` / `/v1/device-keys/persistent`）？给最终路径。
- [ ] `purpose` 命名空间是否支持？还是用 `label` 约定？
- [ ] 长期 key 是否真**完全不受** `GET /v1/providers` 轮换影响（务必确认，这是路线 B 的命门）。
- [ ] 限流/配额是否与 device_key 一致。
- [ ] E2E 联调时给一个 staging 账号 + 可吊销 key 的测试路径（覆盖 §2.1 force 重铸 + §2.3 吊销 → 401 → 客户端自动重铸）。

---

## 5. 客户端侧已按此契约写好的消费点（供联调对照）

- `RelayAuthAdapter.ensureLocalApiKey({force})` → `POST /v1/keys`（见 `00-PLAN.md` WI-3）。
- `relayProviderRegistration.ensure/recover` → 铸 key 后经 control WS `settings_providers_ensure` 写进 `LLMProviderRegistry`（id=`relay-cloud`，keychain `deskpet/provider.relay-cloud`）。
- 401（key 吊销）→ `recover()` → `force:true` 重铸。403（额度）→ 前端「余额不足」提示。
