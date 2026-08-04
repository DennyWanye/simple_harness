# 最终签核评估（只读，禁止改文件/git）

你是独立审查 Expert（只读）。仓库根：`G:\projects\deskpet`。**只评估，绝不修改文件、不跑 git/pytest/cargo**（按源码语义判断即可）。

plan：`plans/2026-06-28-userdata-path-binding-fix/00-PLAN.md`（修装机版 config.toml 路径跨会话漂移 + 空 Bearer 崩溃）。

历经 3 轮评估 + 补缺，现做**最终签核**。逐条核对下列**全部**已落地且语义正确：

1. **Phase 1** `backend/paths.py`：`_portable_userdata_dir` 去盘邻居回落（只认 `<install>/userdata`）+ `_PORTABLE_CACHE` 记忆化 + `.deskpet-portable` sentinel 固化 + 不可写 logger.error 回落；`user_data_dir` `_USER_DATA_DIR_CACHE` 记忆化（env 仍每次优先）；`reset_path_cache` 清两个 cache。
2. **Phase 2** `tauri-app/src-tauri`：`paths.rs::portable_userdata_dir` create-then-return；`process_manager.rs::spawn_once` 注入 `DESKPET_USER_DATA_DIR`（与 Python priority-1 env 名一致）。
3. **Phase 3** `backend/config.py`：`_recover_orphaned_endpoints` wire 进 `resolve_config_path`；canonical 健康判定要求 enabled+base_url；候选源**逐个 try/catch**（坏 TOML 不阻断后续）；写 `.pre-recover-bak`；幂等；全程不抛。
4. **Phase 4** `backend/providers/openai_compatible.py`：`_UNUSABLE_API_KEYS` 含 `"ollama"`；`_client` 对非本地 endpoint + 空/占位/ollama key 抛 `LLMProviderError(error_class="empty_api_key")`，本地放行，拼 Authorization 之前；`backend/agent/agent_loop.py` chain 全失败 ErrorEvent 带 `error_class`。
5. **Phase 5**：`main.py config_loaded` 有 `portable`/`env_pinned`；`provider_registry.py` 构造有 `provider_registry_ready`。
6. **Phase 6** `backend/deskpet-backend.spec`：keyring + Windows 后端 hiddenimports 容错钉死。

输出：每 Phase `Phase N: <%>` + 一句话；最后 `总体完成度: <%>` + `仍有阻断缺口: [...]`（无则 []）。只读。
