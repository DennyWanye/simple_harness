# 中转站 handoff 留档 — 设备 key 生命周期改造（权威契约，2026-06-25 收到）

> 这是 Token Relay 后端团队 2026-06-25 给的正式 handoff，**取代**本目录早先 `01` 里我方设想的 `POST /v1/keys` 路线。
> 生产 base：`https://chinzy.com`。本文件为实现者留档；我方对接计划见 [`00-PLAN.md`](./00-PLAN.md)，任务对齐见 [`01-relay-api-contract.md`](./01-relay-api-contract.md)。

## 核心约束（决定一切）
- 设备 key（`tsk_xxx`）在 relay 侧**哈希存储，不存明文**（同密码）。一把 key 签发后明文返回一次即丢弃，**永远无法第二次吐明文**。
- 现状 `/v1/providers` 每次调用都新签发一把（作废上一把），叠加部分客户端每次启动换 device_id → 单账号攒了 ~3949 把 key。

## relay 的修复：`/v1/providers` 改「复用」三态（feature flag `DEVICE_KEY_REUSE_ENABLED` 灰度，默认关）
| 调用 | 行为 | 何时用 |
|---|---|---|
| `GET /v1/providers`（默认，不带 rotate）| **复用**：有 active key → `api_key:null`（你用本地缓存）；无 → 签发新的返回明文 | 正常每次启动/刷新模型列表 |
| `GET /v1/providers?rotate=false` | 只读元数据：有 active → `api_key:null`；无 → `404 DEVICE_KEY_MISSING` | 纯验证当前 key 是否还有效 |
| `GET /v1/providers?rotate=force` | **强制重签**：无条件签发新 + 作废旧，返回明文 | key 泄漏/缓存失配/本地丢缓存 |

- `api_key:null` **不是错误**，是「我没明文给你（从不存明文），你用本地缓存的那把」。→ 客户端**必须缓存首次/force 拿到的明文**。
- 响应每个 provider 带 `prefix`（key 前 8 位，如 `tsk_553d694a`）；本地缓存 key 前缀与 `prefix` 不一致 = 缓存已非 active → 走 `?rotate=force`。
- 两个 provider（openai/anthropic）**共享同一把 api_key**，取任一即可。

## 要 DeskPet 做的 3 件事
- **任务 A**：持久化 `X-Device-Id`（一台安装一个 UUID，跨重启/升级不变）。⚠️ 不要进程内随机、不要每启动新生成、不要用主机名/MAC。
- **任务 B**：缓存设备 key 明文；`api_key:null` 时用缓存；prefix 失配 → `?rotate=force`；复用说有但本地没缓存 → `?rotate=force` 自愈。
- **任务 C**：登录态显示当前账号 **email + 余额**，测试账号给明显标记（本次报障真因 = 登成了余额 $0 的测试账号 `deskpettest+...@qq.com`，非后端 bug）。

## 服务端响应字段变化
- `/v1/me` 新增：`balance_minor`(USD-cents，75614=$756.14)、`is_test_account`(bool，email 以 `deskpettest+` 开头为 true)。
- 余额不足的 403 结构化：`{"code":"INSUFFICIENT_BALANCE","message":"...","balance_minor":0}`。据 `code==="INSUFFICIENT_BALANCE"` 弹「余额不足，请充值」。

## 上线协调（顺序不可反）
- 阶段 0（现在）：服务端代码合并，flag 默认关 → 线上行为不变，现有客户端继续可用。
- 阶段 1：DeskPet 完成 A+B+C 发版 → **告知 relay 版本号/时间**。
- 阶段 2：relay 对已升级客户端**灰度开** flag，观察 key 表停增 + 无「无 key」报错。
- 阶段 3：全量开。
- ⚠️ 断点风险：A/B（尤其缓存明文）未就绪前开 flag → 旧客户端复用时拿 null + 本地没存 → 不可用。故阶段 2/3 必晚于阶段 1。
- relay 另会**独立**先上后端兜底：每账号 active 设备 key 上限默认 5，超了淘汰最旧（不依赖我方改动，先摁住表爆炸；不影响单设备正常用）。

## 联调资源
- base `https://chinzy.com`；契约 `https://chinzy.com/v1/openapi.json`；Swagger `/v1/docs`；Postman `postman/token-relay-deskpet.postman_collection.json`；测试账号 `deskpettest+...@qq.com`（注意余额，见任务 C 标识）。
