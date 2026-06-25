# Followup — relay 登录不同步 backend cloud-llm key（账号脱节）

> **状态**：📋 待后续 sprint 处理（用户 2026-06-25 拍板：先记录，本轮先用手填的固定 key 续测）
> **严重度**：P1（生产隐患——backend 可能长期用一个已耗尽/错误账号的 key，用户充值的账号根本没接上）
> **发现**：2026-06-25 WI-5(b) 默认配置真机重跑期间，relay 全模型 403 → 深挖发现是账号脱节，非真没钱。

---

## 1. 现象

跑流水线真测时 relay（chinzy.com）对**所有**模型返 `403 {"code":"FORBIDDEN","message":"Available USD credit is too low to accept relay traffic."}`。但用户确认充值的账号 `2437844785@qq.com` **有额度**。

## 2. 根因（代码级实证）

backend 调 LLM 的 key 来自 **keychain slot `default.deskpet-cloud-llm`**（`process_manager.rs::spawn_once` 调 `secrets::get_cloud_api_key()` 读 SERVICE=`deskpet-cloud-llm`/USER=`default`，注入 `DESKPET_CLOUD_API_KEY` env；`backend/config.py::resolve_cloud_api_key` 读该 env）。

| keychain slot | token | 账号 | relay 测试 |
|---|---|---|---|
| `default.deskpet-cloud-llm`（**backend 用**）| `tsk_10ae24…`（很久前手动设的固定 key）| **旧账号** | 403 credit too low（小额度被真测耗尽）|
| `device_key.deskpet-relay`（relay session）| `tsk_22365a…`（**会 rotate**）| `2437844785@qq.com`（有额度）| 200 |

**两个是不同账号。** 关键缺口：**relay 登录 / token refresh 流程从不把 device_key 写进 `cloud-llm` slot**：
- `OnboardingWizard.tsx` / `App.tsx` / `RelayAuthAdapter.ts` 全程**不调** `setCloudApiKey`（grep 实证；`set_cloud_api_key` 唯一调用点是 `SettingsPanel.tsx` 手动填 key）。
- `RelayAuthAdapter` 会 rotate `device_key`（`:420 this.deviceKey = key; setRelayDeviceKey(key)`），但 rotate 后**只写 `device_key.deskpet-relay`，不更新 `cloud-llm`**。
- `secrets.rs::get_cloud_api_key()` 只读 `deskpet-cloud-llm`、**无 fallback 到 device_key**。

→ 结果：`cloud-llm` 自最初一次手动设值后就**冻结**，与 relay 登录的实际账号**永久脱节**。用户换账号 / 充值新账号，backend 完全感知不到，继续用旧账号那个 key 直到它耗尽 → 静默 403。

## 3. 为什么手动同步 keychain 也扛不住（已试）

试过 win32cred 把 `cloud-llm` ← 当前 device_key（`sync_cloud_key_from_device.py`），但：
1. **device_key 会 rotate 且旧的被吊销**：backend boot 读到 `tsk_d18f73`、3 分钟后 relay refresh 把它 rotate 成 `tsk_22365a` 并吊销旧的 → backend 调用 401。session device_key 是临时凭证，不能当 backend 的长期 key。
2. **Persist scope 坑**：win32cred 默认写 `CRED_PERSIST_LOCAL_MACHINE`，keyring-rs 读/写 `CRED_PERSIST_ENTERPRISE`，跨 scope 写可能不被 backend 读到的那条覆盖（观测到写后秒读是新值、重启后又变回旧值的诡异）。手填 key 要走 `SettingsPanel`→`set_cloud_api_key`（keyring-rs，同 scope）才稳。

## 4. 修复方向（后续 sprint）

**核心：让 backend 的 cloud-llm key 跟随 relay 登录的实际账号。** 候选：

1. **（推荐）登录/refresh 后同步 cloud-llm**：relay 登录成功 + 每次 device_key rotate 后，把 device_key（或专门换一个**非 rotate 的长期 API key**）写进 `cloud-llm` slot（调 `set_cloud_api_key`，keyring-rs 同 scope）。需确认 relay 是否提供"长期 API key"vs"会过期的 session key"——若 device_key 本身会 rotate 吊销，backend 不能直接用它，要：
   - 要么 relay 端发一个**长期 key** 给 backend 用；
   - 要么 backend 也走 access_token + 自动 refresh（但 access_token 是 JWT，relay 的 `/v1/chat/completions` 不收 JWT 当 API key，实测 401 INVALID_TOKEN）。
2. **device_key rotate 时把运行中 backend 的 key 也热更新**：现在 backend env 在 spawn 时固定，rotate 后不会更新 → 即便同步了 keychain，运行中的 backend 还用旧 key。需 backend 支持运行时重载 cloud key（如监听一个 IPC / 文件 / 信号），或 rotate 后重启 backend。
3. **缺额度时给清晰信号**：现在 backend 调用 403 后只在 log 里 warning，用户侧无感知（流水线 safe-fail 降级裸 ReAct，体验是"变笨"而非"报错")。应在 403 credit / 401 invalid 时给前端一个明确提示（"账号余额不足/登录失效，请重新登录或充值"）。

## 5. 关联

- 与 `intent_triage` safe-fail 修复（commit 16758f8b）正交：那个修的是"LLM 调用失败时不要误短路"，本 followup 修的是"为什么 LLM 调用会失败（用错账号的 key）"。
- 真测证据：`plans/manual-results-2026-06-25-problem-pipeline-prod/`（probe_deepseek_now.py / probe_token_compare.py / sync_cloud_key_from_device.py）。

## 6. 本轮临时绕过（2026-06-25）

用户在根目录 `.env` 放了一个**固定可用的 key**（`DESKPET_CLOUD_API_KEY`），手工写进 `cloud-llm` slot 续跑 WI-5(b) 真测。本 followup 不在本轮修，留后续 sprint。
