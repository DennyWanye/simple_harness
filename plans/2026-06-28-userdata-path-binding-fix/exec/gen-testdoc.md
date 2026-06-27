# 任务 — 生成详细手工测试文档（windows-mcp 真机用）

你是测试设计 Expert。仓库根：`G:\projects\deskpet`。**只生成一个 Markdown 测试文档文件**，写到：
`G:\projects\deskpet\testcase\2026-06-28-userdata-path-binding\manual-test.md`
不要改其他文件、不跑 git。

## 被测改动（本次修复）
背景 plan：`plans/2026-06-28-userdata-path-binding-fix/00-PLAN.md`。修复装机版 config.toml 路径跨会话漂移 + 空 Bearer 崩溃。落地点：
- **Phase 1** `backend/paths.py`：portable userdata 解析确定化（只认 `<install>/userdata`，去掉盘邻居回落）+ 记忆化 + `.deskpet-portable` sentinel 固化绑定 + 不可写 logger.error 回落 AppData。
- **Phase 2** `tauri-app/src-tauri`：Rust 解析一次 userdata，`spawn_once` 注入 `DESKPET_USER_DATA_DIR` 给 backend（priority-1），双解析归一。
- **Phase 3** `backend/config.py`：`_recover_orphaned_endpoints` 启动自愈——canonical config 无可用 endpoint 时从 AppData/安装目录等候选位迁回 `[[llm.endpoints]]`，写 `.pre-recover-bak`，幂等不抛。
- **Phase 4** `backend/providers/openai_compatible.py`：`_client` 空/占位符 key（含 `ollama`，非本地 endpoint）抛友好 `LLMProviderError(error_class="empty_api_key")`，不再拼非法 `Bearer ` 崩 httpx；localhost 放行。chain 全失败时 error_class 透传 `ErrorEvent`。
- **Phase 5** 可观测：`config_loaded` 加 `portable`/`env_pinned`；`provider_registry_ready n/enabled/ids`。
- **Phase 6** spec 钉死 keyring frozen 后端。

## 关键根因（测试要能验出来）
空 `Bearer ` 来自 legacy 兜底：`get_chain()` 无 enabled provider → 回退 legacy 空 key。根因是 registry 读的 config.toml 路径 ≠ provider 写入的路径（Rust/Python 双解析 + 未注 env 致跨会话漂移）。relay-cloud endpoint 是登录时才写进 config.toml。

## 文档要求
覆盖率要能测出各种 bug 和边界。每个 testcase 含：**前置/步骤(真坐标点击+真输入)/预期/判定证据(截图+日志 grep 关键字)**。明确标注哪些需 **frozen 装机版**（路径绑定只在 frozen 生效）、哪些 **dev 模式(npx tauri dev)** 即可、哪些 **单测兜底**。必含：

1. **空 key 友好错误（★ 用户实际撞到的崩溃，dev 可测）**：制造无可用 key（如 keychain 无 relay key / 置占位符）→ 真机发消息 → 预期出现可读"请重新登录/配置 provider"类提示，**不出现** `Illegal header value b'Bearer '` / LocalProtocolError / 整轮崩。日志判据：`empty_api_key` / 不再有 `LocalProtocolError`。
2. **正常聊天不回归（dev）**：有效 key → 发消息 → 正常流式回复。
3. **路径跨会话不漂移（★ frozen 装机版）**：装到自定义目录（如 F:\deskpet）→ 登录 → 完全退出 → 重启 → 聊天成功。日志判据：登录期 vs 重启期 `config_loaded` 的 `path=`/`user_data_dir=` **完全一致** + `env_pinned=True` + `provider_registry_ready enabled>=1`。
4. **存量自愈（frozen 或构造）**：canonical 无 endpoints 但 AppData 有 → 启动出现 `endpoints_recovered_from src=... count=N` + 不重登可聊 + 生成 `.pre-recover-bak`。
5. **boot 可观测（任意）**：`config_loaded portable=.. env_pinned=..` + `provider_registry_ready` 能在日志看到。
6. **边界**：① 本地 ollama（localhost + key=ollama）放行能正常用；② 云端 + ollama 占位被拦友好报错；③ sentinel 存在时即便 userdata 偶发不可写仍绑定；④ 安装目录不可写 → 日志 `portable_userdata_unwritable` 回落 AppData；⑤ chain 全 provider 失败 → ErrorEvent 带 error_class。标注哪些可真机、哪些单测兜底。

## 真机纪律（写进文档头部）
严格模拟人工：每 case 先 declare `坐标=(x,y)|动作=click/type|期望=...`；截图存 `plans/manual-results-2026-06-28-userdata-path/screenshots/`；中文输入用剪贴板 Ctrl+V；backend 日志在 tauri-dev log（UTF-16）。失败 retry ≥3 不同 workaround 才可标"环境受限"。

## 输出
生成结构化 Markdown：标题/环境/真机纪律/§ 各 TC 表 + 判定汇总表 + DECISION 占位。中文。文件路径见上。生成后输出文档大纲摘要。
