# 对接对齐 + 联调 checklist + rollout（DeskPet ↔ 中转站）

> **本文件历史**：v1-v4 这里是我方设想的 `POST /v1/keys` 长期 key 端点契约。中转站 2026-06-25 handoff 选了**不同方案**（`/v1/providers` 复用三态，见 [`02-relay-handoff-device-key-reuse.md`](./02-relay-handoff-device-key-reuse.md)）→ 本文件**改写**为：我方三任务 ↔ 中转站 handoff 的对齐表 + 联调 checklist + rollout 节奏。实现细节见 [`00-PLAN.md`](./00-PLAN.md)。

---

## 1. 任务对齐：中转站要的 3 件事 ↔ 我方 WI

| 中转站任务 | 我方 WI | 现状 | 备注 |
|---|---|---|---|
| A 持久化 `X-Device-Id` | WI-A | **几乎已做**（`device.rs` 文件持久、跨重启稳定）| **拍板①维持文件**，不迁 keyring；联调时主动告知中转站「已稳定，故不迁 keyring」 |
| B 缓存 device key 明文 + 处理 `api_key:null` + prefix 自愈 + force | WI-B | 半成品（缓存 slot+bindings 全在，缺复用三态/冷启动载缓存/prefix 自愈/force）| 核心改造 |
| C 显示账号 email+余额+测试号标识 | WI-C | 数据半有（`/v1/usage/summary` 有 balance），展示全缺 | 治本次报障真因（登错测试账号）|
| （额外，我方需求）收编成可统一管理的 provider | WI-1/2/3/4 | 全新 | 中转站不做这件事，是用户「设置里统一管理」的核心交付 |

## 2. 我方依赖中转站的字段/行为（验收时对齐）
- `/v1/providers` 复用三态：默认复用返 `api_key:null`、`?rotate=false` 只读、`?rotate=force` 强制重签（handoff §3）。
- 每 provider 响应带 `prefix`（key 前 8 位）→ 我方 prefix 失配自愈靠它。
- `/v1/me` 新增 `balance_minor`(USD-cents) + `is_test_account`(bool)。
- 余额不足 403 结构化 `{"code":"INSUFFICIENT_BALANCE","message":"...","balance_minor":N}`。
- feature flag `DEVICE_KEY_REUSE_ENABLED`（默认关，灰度）。

## 2.1 需向中转站澄清的问题
- [ ] **币种**：`/v1/me.balance_minor`(USD-cents, $756.14) 与既有 `/v1/usage/summary.balance.amount_minor`(我方 UI 现按 CN¥ 渲染 ¥713.17) 是**同一钱包的不同币种展示**，还是两个独立账户？（我方 `AccountSettingsPanel.tsx` 已有 CNY 余额 UI，需据答复收口单一余额源，避免双币种并存——见 `00-PLAN.md` WI-C / R5-M1）
- [ ] **401 结构**：余额不足是 403 `{code:INSUFFICIENT_BALANCE}`；401（key 失效）的 body 结构是什么（有无 `code`）？我方 `classify_relay_error` 据此精确分类。
- [ ] **prefix 长度**：响应 `prefix` 是固定前 N 位（handoff 写「前 8 位」但示例 `tsk_553d694a` 是 12 字符）？确认我方 `cached.startsWith(prefix)` 失配判定的 prefix 取值。

## 3. 联调 checklist（对照中转站 handoff §6）
- [ ] **任务 A**：多次重启抓包，`X-Device-Id` 恒为同一 UUID。
- [ ] **任务 B**：首发/force 落盘明文；再启 `api_key:null` 能从缓存取出并成功 `/v1/chat/completions`。
- [ ] **任务 B 自愈**：手动清/改本地缓存 key → 客户端 prefix 失配/缺失 → `?rotate=force` 自动恢复。
- [ ] **任务 C**：登录后界面显示 email+余额；测试账号显徽章。
- [ ] **结构化错误**：余额 0 账号发请求 → 客户端识别 `INSUFFICIENT_BALANCE` 弹充值。
- [ ] **收编**（我方额外）：登录后设置面板出现 relay-cloud 行、可启停/排序/重置 key；chat 走 registry chain（日志 `key_fp`）。
- [ ] **回归**：flag 关闭（阶段 0）现有流程全部正常、无变化。

## 4. Rollout 节奏（顺序不可反，详见 `00-PLAN.md §6`）
1. 阶段 0：中转站 flag 默认关 → 我方现在即可做 WI-1~6（不依赖 flag）。
2. 阶段 1：我方完成 WI-A~7（尤其 WI-B 缓存+复用）发版 → **告知中转站版本号/时间**。
3. 阶段 2：中转站对已升级客户端灰度开 flag → 我方复用相关 E2E 此时完整可测。
4. 阶段 3：全量开。
> ⚠️ 断点铁律：WI-B 未发版前开 flag → 旧客户端复用拿 null+没缓存 → 不可用。阶段 2/3 必晚于阶段 1。

## 5. 联调资源（中转站提供）
- base `https://chinzy.com`；OpenAPI `https://chinzy.com/v1/openapi.json`；Swagger `/v1/docs`；Postman `postman/token-relay-deskpet.postman_collection.json`；测试账号 `deskpettest+...@qq.com`（注意余额 + 任务 C 标识）。
