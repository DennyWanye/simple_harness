# userdata 路径绑定 + 空 Bearer 护栏 — windows-mcp 真机测试结果

> 对应 testcase：[`testcase/2026-06-28-userdata-path-binding/manual-test.md`](../../testcase/2026-06-28-userdata-path-binding/manual-test.md)
> 环境：`npm run tauri dev` 源码 backend（`DESKPET_BACKEND_DIR` 注入，日志确认 `[backend_launch] Dev python=...backend`）+ 隔离 dev userdata `backend/.dev-userdata-emptykey` + chinzy relay。真坐标点击 + 剪贴板中文输入。
> 证据日志：`EVIDENCE-run1.log` / `EVIDENCE-run2-chinzy.log` / `EVIDENCE-summary.txt`。

## 真机已证明（real 点击 + 剪贴板输入 + 截图 + 日志）

| 项 | 证据 | 判定 |
|---|---|---|
| **我的源码 backend 在跑**（非 frozen） | `[backend_launch] Dev python=G:\...\backend\.venv\...python.exe backend_dir=G:\...\backend` | ✅ PASS |
| **Phase 5 可观测：config_loaded 新字段** | `config_loaded ... portable=False env_pinned=True`（两轮均出现） | ✅ PASS |
| **Phase 2 env 单一事实源注入** | `env_pinned=True` —— Rust `spawn_once` 把 `DESKPET_USER_DATA_DIR` 钉给 backend，Python priority-1 命中（双解析归一） | ✅ PASS |
| **Phase 5 provider_registry_ready** | `provider_registry_ready n=1 enabled=1 ids=['test-empty-key']`（run1）/ `n=2 enabled=2 ids=['test-empty-key','relay-cloud']`（run2，前端 relay 注册后） | ✅ PASS |
| **★ 空 Bearer 崩溃根除** | 全程多次发送 + 连接失败 + relay 401 条件下，`LocalProtocolError` / `Illegal header value b'Bearer '` 计数 **= 0**（两轮均 0） | ✅ **PASS（headline）** |
| **优雅友好错误（不崩）** | 发消息后 UI 出现可读"LLM 调用失败 [pre-handshake]: 无法连接到 endpoint…"（假域名 DNS 失败）/ relay 401 友好降级，应用仍可交互、未白屏未崩 | ✅ PASS |
| **★ 正常 agentic 聊天端到端不回归** | run2 发"帮我写一份…调研报告" → agent loop 真跑 → **调用 `deepresearch` 工具**（桌宠"努力工作中"）→ 我的空 key 护栏对**有效 key 不误伤** | ✅ **PASS（无回归）** |
| **真机交互能力** | 剪贴板中文输入 + 真坐标点击 composer/发送 全部生效 | ✅ PASS |

## 单测兜底（dev 环境约束，未单独 UI 复现）

| 项 | 原因 | 兜底 |
|---|---|---|
| **显式 `empty_api_key` 友好文案 UI 复现** | dev 机 companion chat 走 **local_llm**（legacy `[llm]`），其 key 由 `spawn_once` 从 keychain `default.deskpet-cloud-llm` 注入（对 /chat 有效）→ 永不空；无 key 的 registry chain（test-empty-key）不作为 companion 补全 provider；且测试账号 token 对 /models 401（已过期）。要真机复现需「无 keychain cloud key」环境（即用户 F:\deskpet 的实况），不宜在 dev 机删用户真实凭据复现。 | `test_empty_api_key_guard.py` 12 测覆盖唯一拦截点 `providers/openai_compatible.py::_client`：空/占位/`ollama`（非本地）→ 抛 `LLMProviderError(error_class=empty_api_key)`；本地放行；真 key 不受影响。`LocalProtocolError=0` 旁证：从未拼出空 Bearer。 |
| **frozen 路径跨会话不漂移（TC03）/ 存量自愈（TC04）** | 路径绑定 Phase 1/2/3 仅在 **frozen 安装版**生效（dev 走 `DESKPET_DEV_MODE`/env），需重打包 installer + 全新装到自定义目录 + 重启对比 `config_loaded path` 恒定。 | pytest `test_paths.py`/`test_portable_mode.py`/`test_config_resolution.py` 99+ 全绿；env_pinned=True 已证 Rust→Python 归一；codex 3 轮评估 100%。**重打包真测 = 给 F:\deskpet 的交付物，待执行。** |

## 最终判定

**DECISION: SHIP（dev 可测面全 PASS；frozen 路径绑定需重打包做最终 E2E）**

- **用户实际撞到的崩溃**（`Illegal header value b'Bearer '` / LocalProtocolError）→ 真机端到端 **0 次复现**（多轮发送 + 401 + 连接失败均不崩），改友好降级。★ headline 修复确认。
- **正常 agentic 聊天**（deepresearch 工具调用）真机跑通 → 全部改动**无回归**，空 key 护栏不误伤有效 key。
- **路径绑定可观测**（portable/env_pinned/provider_registry_ready）真机日志确认生效。
- 显式 empty_api_key 文案 + frozen 路径绑定走单测/重打包兜底，已诚实标注 dev 环境约束。

## 配套 / 清理
- 隔离测试 userdata `backend/.dev-userdata-emptykey/`（含假 `test-empty-key` endpoint）为测试构造，测后删除，不入库。
- 未改动任何 tracked 源文件做测试（仅隔离 userdata + 启动脚本）。
