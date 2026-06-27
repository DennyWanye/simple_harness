# Plan — userdata/config 路径与安装目录绑定修复 + 空 Bearer 护栏

> 日期：2026-06-28 ｜ 触发：装机版(F:\deskpet)聊天报 `Illegal header value b'Bearer '`
> 状态：**待执行**（root cause 已锁定，Phase 0 诊断待用户在 F:\deskpet 跑一条确认精确分支）

---

## 1. 现象

装机版（自定义目录 `F:\deskpet`，Administrator）对话报：

```
Illegal header value b'Bearer ' — LocalProtocolError
```

backend.log 同时出现：`relay_provider_ensured key_fp=4dce0997`、`p5s2_provider_chain_resolve_failed sid=... err=no LLM provider configured`、多条 `Bearer` 错误。重启后依旧。

---

## 2. Root cause（已锁定，三层证据）

### 2.1 不是 keychain bug
- `main.py:6815`：`_api_key = _registry.resolve_api_key(_entry.id) or "ollama"`。若 keychain 读不出，relay provider 会发 **`Bearer ollama`**，**不可能**是空 `Bearer `。
- `relay_provider_ensured key_fp=4dce0997`（`relay_provider_ops.py:63` 的 `key_fingerprint(reg.resolve_api_key(...))`）证明**登录那次** keychain 读写都正常。
- ⟹ 空 `Bearer ` 只能来自 **legacy 兜底路径**（`get_chain()` 抛 `NoProviderConfiguredError` 后 fallback）。

### 2.2 真因：registry 读的 config.toml 里没有 enabled 的 relay-cloud endpoint
- 空 Bearer ⟹ `resolve_provider_for_session` → `registry.get_chain()` 抛 `NoProviderConfiguredError`（`provider_registry.py:642`，无 enabled 项）⟹ `main.py:6835` 捕获 → log `p5s2_provider_chain_resolve_failed` → `_provider_chain=None` → 退 legacy 空 key。
- relay-cloud 这行 endpoint **不是出厂 bundle config.toml 自带的**，是**登录时** `ensure_relay_provider`→`add_provider`→`_persist_to_toml` 才写进 `<user_data>/config.toml` 的。

### 2.3 路径会在会话间漂移（"安装目录绑定"问题）
两套 userdata 解析**条件不一致**，且 backend 自己独立解析（Tauri 没钉 env）：

| | Rust `paths.rs::portable_userdata_dir` | Python `paths.py::_portable_userdata_dir` |
|---|---|---|
| 锚点 | `current_exe()`=`F:\deskpet\deskpet.exe` → parent `F:\deskpet` | `sys.executable`=`F:\deskpet\backend\…exe` → parent `F:\deskpet\backend`，name=="backend" 故 candidates_up=`(1,2,0)` |
| 判定 | `ud.is_dir()` —— **要求目录已存在**，否则回落 `%AppData%` | **主动 `mkdir`+探针写**，candidates_up=2 时甚至会落到 `F:\userdata`(盘邻居) |
| 创建时机 | `lib.rs:162` 在 `.setup()` 里才 `create_dir_all(<install>/userdata)` | 调用即建 |

- `process_manager.rs::spawn_once`（148-219）只注了 `DESKPET_CLOUD_API_KEY`/`PYTHONIOENCODING`/`PYTHONUNBUFFERED`，**没注 `DESKPET_USER_DATA_DIR`** ⟹ Python 完全独立解析。
- ⟹ 登录会话 vs 重启会话，因 `userdata/` 存在性时序 + 两套判定差异，**"portable vs %AppData%" 的结论可能翻转**：provider 写进 A 目录的 config.toml，重启从 B 目录读 → endpoint "丢失"。
- 用户确实在 `F:\deskpet\userdata\config.toml` 找到了 relay-cloud provider，但运行中的 backend 极可能读的是**另一个** config.toml（或同目录但该行 disabled/malformed —— Phase 0 区分）。

### 2.4 次生 bug：空 key 直接拼 Bearer 头 → 崩
- `main.py:6087` `_headers["Authorization"] = f"Bearer {_api_key}"`，`_api_key` 空时拼出非法头 `Bearer ` → httpx `LocalProtocolError`，把 chat/facts/classifier 全刷崩，且报错对用户不可读。
- legacy `local_llm`（`main.py:281`）key 来自占位符/keychain，frozen 下退化为空/占位符。

### 2.5 旁证（非本 bug 但需顺手硬化）
- `deskpet-backend.spec` 的 hiddenimports **没有显式列 `keyring` / `keyring.backends.Windows` / `win32ctypes.core`**。keyring 后端走 entry-point 动态加载，PyInstaller 静态分析看不到 → frozen 版**有概率**整个 keyring 失效。本机现在能用（`key_fp` 证明），但属定时炸弹，一并钉死。

---

## 3. 修复目标

1. **单一事实源**：userdata/config 路径**唯一地绑定到安装目录**，Rust/Python 同一答案，跨会话恒定不漂移。
2. **自愈**：已经把 config 写错地方的存量装机（如本用户）无需重登即可恢复。
3. **优雅失败**：无可用 provider / 空 key 时给"请重新登录"可读错误，绝不拼空 `Bearer ` 崩溃。
4. **可观测**：启动日志一眼看出解析到哪、加载了几个 endpoint、几个 enabled。

---

## 4. Phase 0 —— 精确分支确认（用户在 F:\deskpet 跑，30 秒）

目的：区分 2.3「路径漂移」vs「同目录但 disabled/malformed」，并为修复后验证留基线。

```powershell
# A. 看 backend 实际加载了哪个 config（对比登录期 vs 最近一次重启期）
Select-String -Path "F:\deskpet\userdata\logs\backend.log" -Pattern "config_loaded" | Select-Object -Last 5
# B. dump 那个 config.toml 的 endpoints 段（enabled?）
Get-Content "F:\deskpet\userdata\config.toml" | Select-String -Pattern "endpoints|id =|enabled|base_url|relay" -Context 0,1
# C. 有没有第二个 config.toml 在 AppData（漂移铁证）
Get-Content "$env:APPDATA\deskpet\config.toml" -ErrorAction SilentlyContinue | Select-String "endpoints|relay|enabled"
Test-Path "$env:APPDATA\deskpet\config.toml"
```

**判读**：
- A 的 `path=`/`user_data_dir=` 若**两期不同** → 2.3 漂移坐实，重点 Phase 1+2。
- A 两期相同但 B 显示 endpoint `enabled = false`/字段缺失 → 重点 Phase 3 自愈 + 复查 logout 误触发。
- C 存在且有 endpoints → 漂移铁证（provider 落在 AppData，运行读 portable）。

> 修复设计对**所有**分支都鲁棒，Phase 0 仅用于确认 + 验证，不阻塞编码。

---

## 5. 修复方案（到代码级）

### Phase 1 — Python 侧路径确定化 + 记忆化（`backend/paths.py`）

**1a. `user_data_dir()` 记忆化 + env 永远优先**（防进程内漂移）
```python
_USER_DATA_DIR_CACHE: Path | None = None

def user_data_dir() -> Path:
    global _USER_DATA_DIR_CACHE
    if _USER_DATA_DIR_CACHE is not None:
        return _USER_DATA_DIR_CACHE
    resolved = _resolve_user_data_dir()      # 原解析逻辑搬进来
    _USER_DATA_DIR_CACHE = resolved
    return resolved
```
> 同样给 `_portable_userdata_dir()` 加一次性 cache（成功结果），避免每次 probe-write 抖动。

**1b. 去掉危险的盘邻居回落**（`_portable_userdata_dir`）
- 现 `candidates_up=(1,2,0)`，steps_up=2 会落到 `F:\userdata`（盘根邻居），是脏数据源。
- 改为：name=="backend" → **只认 `<install_root>/userdata`**（1 级上）；standalone → 只认 `<exe_dir>/userdata`。不可写时 **`logger.error` 大声报** 再显式回落 `%AppData%`（不再静悄悄落到盘邻居）。
```python
def _portable_userdata_dir() -> Path | None:
    base = _install_dir()
    if base is None:
        return None
    root = base.parent if base.name.lower() == "backend" else base
    candidate = root / "userdata"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".deskpet-write-probe"
        probe.write_bytes(b""); probe.unlink()
    except OSError as e:
        logger.error("portable_userdata_unwritable dir=%s err=%s — falling back to AppData", candidate, e)
        return None
    return candidate
```

**1c. sentinel 固化绑定**：首次成功解析后落 `<install>/userdata/.deskpet-portable`；后续启动若 sentinel 存在则**无条件**绑定该目录（用户意图：钉死安装目录），即便某次 probe 抖动也不漂走。

### Phase 2 — Rust 单一事实源 + 注入 env（`tauri-app/src-tauri`）★核心

让 **Rust 解析一次** userdata 并**通过 env 传给 backend**，Python 走 priority-1 直接用 → 双解析彻底归一。

**2a. `paths.rs::portable_userdata_dir`** 改成「**create 再返回**」（与 Python 对齐，不再要求预先存在）：
```rust
fn portable_userdata_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let parent = exe.parent()?;
    let root = if parent.file_name()?.to_string_lossy().eq_ignore_ascii_case("backend") {
        parent.parent()?.to_path_buf()
    } else { parent.to_path_buf() };
    let ud = root.join("userdata");
    // 探针写：可写才认 portable，否则 None→%AppData%
    if std::fs::create_dir_all(&ud).is_ok() {
        let probe = ud.join(".deskpet-write-probe");
        if std::fs::write(&probe, b"").is_ok() { let _ = std::fs::remove_file(&probe); return Some(ud); }
    }
    None
}
```

**2b. `process_manager.rs::spawn_once`** 在注 env 处加一行（与 `DESKPET_CLOUD_API_KEY` 同段）：
```rust
if let Some(ud) = crate::paths::user_data_dir() {
    cmd.env("DESKPET_USER_DATA_DIR", ud.to_string_lossy().to_string());
}
```
> Python `paths.py:193` 已 priority-1 读 `DESKPET_USER_DATA_DIR` → backend 不再独立解析 → Rust=Python=同一目录。device_id/onboarding/config/db 全部归一。

**2c.** `lib.rs:160-164` 的预创建保留（无害），但不再是正确性依赖。

### Phase 3 — 存量装机自愈迁移（`backend/config.py`）

`resolve_config_path()` 内、`seed_user_config_if_missing()` 之后加一步 `_recover_orphaned_endpoints()`：

- 规则：canonical `<user_data>/config.toml` 若 **缺 `[[llm.endpoints]]` 或全 disabled**，扫已知候选位（`%AppData%\deskpet\config.toml`、`<install>/userdata\config.toml`、盘邻居 `<drive>\userdata\config.toml`）。
- 找到**含 enabled endpoints** 的，把其 `[[llm.endpoints]]` 段并入 canonical（tomlkit，仅补 endpoints，保留 canonical 其余内容 + 写 `.pre-recover-bak`），`logger.info("endpoints_recovered_from=%s count=%d", src, n)`。
- keychain 是系统级（非路径绑定），key 本就找得到，无需迁移。
- 幂等：canonical 已有 enabled endpoints 则 no-op。

### Phase 4 — 空 key 护栏 + 可读错误

**4a. 直连头**（`main.py:6087` 一带）：拼头前判空
```python
if not (_api_key or "").strip():
    raise EmptyApiKeyError(provider_id=getattr(_entry, "id", "?"))
_headers["Authorization"] = f"Bearer {_api_key}"
```
（Phase 0 后确认 6087 的 `_api_key` 来源；若该处实际不空，则重心在 4b legacy 路径。）

**4b. legacy 兜底不再发空 key**（`main.py:6835` fallback 段 / `OpenAICompatibleProvider`）：
- `get_chain()` 失败回退时，若 legacy `local_llm` 的 key 为空/占位符（`{"", "from-keychain", "from-env", "your-key-here"}`），**不**发请求，改 emit 一条可操作 error 事件给前端：`reason="no_provider_or_login_required"`，文案"未检测到可用的 LLM 提供方，请重新登录后重试"。
- 新增 `EmptyApiKeyError`（`llm/provider_registry.py` 或 `llm/keys.py`），chat handler 捕获 → 同上 error 事件，不抛 `LocalProtocolError`。

**4c. openai_adapter**：`OpenAICompatibleProvider` 发请求前若 `self._api_key` 空 → 抛 `EmptyApiKeyError`（统一拦截点，防任何路径漏网）。

### Phase 5 — 可观测性

- `main.py:101 config_loaded` 扩展字段：`portable=<bool>`、`env_pinned=<DESKPET_USER_DATA_DIR 是否设>`、`n_endpoints`、`n_enabled`（registry 构造后补一条 `provider_registry_ready ids=[...] enabled=[...]`）。
- `LLMProviderRegistry.__init__` 末尾 log 加载到的 ids + enabled 数。

### Phase 6 — keyring frozen 硬化（`backend/deskpet-backend.spec`）

hiddenimports 追加：
```python
hiddenimports += collect_submodules("keyring")
hiddenimports += ["keyring.backends.Windows", "win32ctypes.core", "win32ctypes.pywin32"]
```
> 消除 frozen 版 keyring 后端静默缺失风险（旁证 2.5）。

---

## 6. 测试

### 6.1 pytest（不需重打包，纯逻辑）
- `test_paths.py`：① portable 确定化（mock `sys.frozen`+executable → 唯一 `<install>/userdata`，不落盘邻居）；② 记忆化（改 env 后同进程仍返回首解析）；③ `DESKPET_USER_DATA_DIR` env 永远优先；④ 不可写时回落 AppData 且 log error。
- `test_config_resolution.py` 增：`_recover_orphaned_endpoints` —— canonical 无 endpoints + AppData 有 enabled → 迁移成功、幂等、备份生成、canonical 其余内容不变。
- 新 `test_empty_api_key_guard.py`：空/占位符 key → `EmptyApiKeyError`；chat fallback emit `no_provider_or_login_required` 而非 `LocalProtocolError`；非空 key 不受影响（BC）。
- 全量回归：`backend/tests` 绿。

### 6.2 Rust 单测
- `paths.rs`：`portable_userdata_dir` create-then-return（可注入 exe 路径的 `*_with` 变体）；不可写 → None。
- `process_manager`：抽 `build_backend_env()` 纯函数，断言含 `DESKPET_USER_DATA_DIR`（spawn 本身难单测）。

### 6.3 真机（最终证据 —— 需重打包）
- `scripts/build_backend.ps1` + Tauri NSIS 重打 installer → **全新装到 `F:\deskpet`** → 登录 → **重启** → 对话「你好」成功（无空 Bearer）。
- 验证 `config_loaded` 登录期 vs 重启期 `path=`/`user_data_dir=` **完全一致**；`provider_registry_ready enabled>=1`。
- 存量自愈：保留旧 AppData orphan config 装新版 → 首启 log `endpoints_recovered_from=...` → 不重登即可聊。
- 走项目真测纪律（windows-mcp 真点真输入 + 截图 + 抓 tauri-dev/backend log）。

> ⚠️ 真机验证必须**重打包**（装机版跑 frozen exe，DESKPET_BACKEND_DIR 覆盖是 dev 手段）。pytest/Rust 单测先行兜住逻辑；重打包前用户用 env 兜底（`DESKPET_CLOUD_API_KEY`）继续测应用其余功能。

---

## 7. 执行顺序与风险

1. Phase 0 诊断（用户，并行）→ 确认分支。
2. Phase 1+4+5+6（纯 Python/spec，先落，pytest 验）。
3. Phase 3 自愈（Python，pytest 验）。
4. Phase 2（Rust，cargo test）。
5. 重打包 → 真机验收 → STATUS 更新。

**风险**：
- Phase 2 改 Rust 需 `cargo build` + 重打包，迭代重。→ 故 Python 侧（1/3/4）独立成立，即使不动 Rust 也能靠"记忆化+去盘邻居+自愈+env 优先"大幅收敛；Phase 2 是根治双解析的最后一锤。
- 自愈迁移误并入**他人/过期** endpoints？→ 仅扫固定候选位 + 仅当 canonical 无 enabled 时触发 + 写备份 + 幂等，低风险；keychain key 对不上则后续 4c 友好报错而非崩。
