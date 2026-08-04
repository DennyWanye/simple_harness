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

## 2.1 三澄清 —— 中转站已答复（2026-06-25）
- ✅ **Q1 币种**：**同一个钱包、同字段、同值、单位 USD-cents**（`WalletAccount.balanceMinor`）。钱包早已 USD 本位，`/v1/usage/summary.balance.currency==="USD"`。**我方 `AccountSettingsPanel.tsx` 的 `¥713.17` 是陈旧 bug**（写死 ¥ + 旧值）→ 收口单一源 `/v1/usage/summary.balance`、读 `balance.currency` 渲染美元、删 CN¥ 写死。**今天即可做、不依赖中转站**（中转站点名先并行做）。→ `00-PLAN.md` WI-C。
- ✅ **Q2 错误结构**：统一信封 `{code,message,request_id}`。401 鉴权 `code∈{INVALID_TOKEN,EXPIRED_TOKEN}`→重签。余额不足**两个表面**：① 403 软门统一信封 `INSUFFICIENT_BALANCE`+`balance_minor`（**PR-1 前仍是 `FORBIDDEN`**，过渡期同时接受）；② 402 预扣 OpenAI 透传 `body.error.code==="insufficient_balance"`（小写嵌套）。其它码 `RATE_LIMITED`(429)/`UPSTREAM_*`(502/503)/`DEVICE_KEY_MISSING`(404)。→ `00-PLAN.md` WI-5。
- ✅ **Q3 prefix**：前 **12 字符**（`key.slice(0,12)`，如 `tsk_553d694a`），`startsWith` 直接成立。⚠️ `/v1/providers` **现无此字段**（随 PR-6 上线）→ 上线前用回落 `null+无缓存→?rotate=force`。→ `00-PLAN.md` WI-B。

## 2.2 中转站 action items（其 P0 排期）
| 中转站改动 | PR | 对我方影响 |
|---|---|---|
| `INSUFFICIENT_BALANCE` 入白名单 + BalanceGuard 结构化 + `balance_minor` | PR-1 | 上线前 403 仍回 `FORBIDDEN`，我方过渡期双接受 |
| `/v1/me` 加 `balance_minor`+`is_test_account` | PR-1 | WI-C 第 2/3 点依赖 |
| `/v1/providers` 每 provider 加 `prefix` | PR-6/WS1-A | WI-B prefix 精细自愈依赖；未上线走回落 |
| 复用三态 + `DEVICE_KEY_REUSE_ENABLED` flag | PR-6 | 灰度待我方 WI-B 发版 |
| active key 上限兜底（不依赖我方）| PR-2 | 先行止血，无需我方配合 |

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
