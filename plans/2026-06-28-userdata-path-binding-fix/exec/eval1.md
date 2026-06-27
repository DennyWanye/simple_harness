# 评估任务 — 核对 plan 完成度（只读，不许改文件）

你是独立审查 Expert（只读）。仓库根：`G:\projects\deskpet`。**禁止修改任何文件、禁止 git 操作**，只读代码 + 输出评估。

## 背景
plan 文件：`G:\projects\deskpet\plans\2026-06-28-userdata-path-binding-fix\00-PLAN.md`。
目标：修复装机版 config.toml 路径跨会话漂移（relay-cloud provider 写一个目录、读另一个目录 → get_chain 空 → legacy 空 key 拼出非法 `Bearer ` 崩溃）+ 把 userdata 单一事实源绑定到安装目录 + 空 key 护栏。

## 你的任务
逐条核对 plan §5 的 Phase 1~6 是否**真正落地到代码**（不是看测试绿就算数，要读实现确认语义正确）。逐 Phase 给「完成度 %」+「缺口清单」。重点核查以下"真做了没"：

### Phase 1（`backend/paths.py`）
- `_portable_userdata_dir()` 是否去掉了 `candidates_up=(1,2,0)` 的盘邻居回落，改为只认 `<install_root>/userdata`（backend 布局）/ `<exe_dir>/userdata`（standalone）？
- 是否记忆化（模块级 cache + `reset_path_cache()`）？
- sentinel `.deskpet-portable` 固化绑定是否实现（存在即无条件返回）？
- 不可写时是否 `logger.error` 后回落 AppData（而非盘邻居）？

### Phase 2（`tauri-app/src-tauri/src/paths.rs` + `process_manager.rs`）
- `portable_userdata_dir` 是否改成 create-then-return（不再要求目录预先存在）？
- `spawn_once` 是否把 Rust 解析的 userdata 注入 `DESKPET_USER_DATA_DIR` 给 backend？env 名是否与 Python `backend/paths.py` priority-1 读的**完全一致**？
- `cargo build` 是否能过（你可只读判断语法/类型，不必真跑）？

### Phase 3（`backend/config.py`）
- `_recover_orphaned_endpoints` 是否实现并 **wire 进 `resolve_config_path`**（seed 之后调用）？
- 触发条件是否正确（canonical 无 enabled endpoints 才迁）？候选源是否含 AppData + frozen 安装目录 + 盘邻居？是否排除 canonical 自身？
- 是否写 `.pre-recover-bak` 备份 + 幂等 + 全程不抛（异常只 warning）？

### Phase 4（空 key 护栏）★最易做错
- **关键**：main.py 真正用的聊天 provider 是 `from providers.openai_compatible import OpenAICompatibleProvider`。护栏是否加在**这个**类的出站 client 构造点（`providers/openai_compatible.py::_client`），而不是只加在没人用的 `llm/openai_adapter.py`？
- `_client` 是否在拼 `Authorization: Bearer {api_key}` **之前**对空/占位符 key（非本地 endpoint）抛错，阻止非法空 Bearer 头发出？
- 本地 ollama / localhost 是否放行？真 key 是否不受影响？
- 是否复用现有 `LLMProviderError`(error_class) 让 chat 层 surface 友好消息？

### Phase 5（可观测）
- `main.py` `config_loaded` 日志是否加了 `portable` / `env_pinned`？
- `provider_registry.py` 构造是否加了 `provider_registry_ready`（n/enabled/ids）日志？

### Phase 6（`backend/deskpet-backend.spec`）
- 是否显式钉死 keyring + Windows 后端 hiddenimports（`keyring.backends.Windows` / `win32ctypes.*`），且对缺失依赖容错（spec 仍能加载）？

## 输出格式（结构化）
对每个 Phase：`Phase N: <完成度%> — <一句话结论>` + 若 <100% 列具体缺口（文件:行 + 缺什么）。
最后给：`总体完成度: <%>` + `阻断性缺口（必须补）: [...]` + `非阻断建议: [...]`。
诚实严格——做错地方（如护栏加错模块）必须扣分点名。只读，跑 `grep`/读文件即可，别改东西。
