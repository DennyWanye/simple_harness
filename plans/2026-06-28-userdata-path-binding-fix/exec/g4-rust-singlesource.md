# 任务 G4 — Rust 侧 userdata 单一事实源 + 注入 env（Phase 2，核心）

你是编码 Expert（Rust）。仓库根：`G:\projects\deskpet`，crate 在 `tauri-app\src-tauri`。只改下列文件，**不要 git commit**。
背景见 `G:\projects\deskpet\plans\2026-06-28-userdata-path-binding-fix\00-PLAN.md` §2.3 + §5 Phase 2。

## 问题
Rust 和 Python 各有一套 portable userdata 解析，**判定条件不一致**：
- Rust `paths.rs::portable_userdata_dir`（约 69-85 行）要求 `<install>/userdata` **已存在**（`is_dir()`）才认 portable，否则回落 `%AppData%`。
- Python `backend/paths.py` **主动 mkdir** 创建。
- 且 `process_manager.rs::spawn_once` 给 backend 注了 `DESKPET_CLOUD_API_KEY` 等却**没注 `DESKPET_USER_DATA_DIR`** → Python 完全独立解析。

跨会话 + 目录存在性时序差异 → 两边/两次解析到不同 userdata → relay-cloud provider 写一个 config.toml、重启从另一个读 → 丢失。

## 修复目标
让 **Rust 解析一次** userdata 并通过 env **传给 backend**，Python（`backend/paths.py:193` 已 priority-1 读 `DESKPET_USER_DATA_DIR`）直接用 → 双解析归一，永不漂移。

## 1. `tauri-app\src-tauri\src\paths.rs` — `portable_userdata_dir`（非 test cfg，约 69-85）改成 create-then-return
先读现有实现。把"要求已存在"改成"主动创建 + 探针写验证可写"，与 Python 对齐：
```rust
#[cfg(not(test))]
fn portable_userdata_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let parent = exe.parent()?;
    let root = if parent
        .file_name()
        .map(|n| n.to_string_lossy().eq_ignore_ascii_case("backend"))
        .unwrap_or(false)
    {
        parent.parent()?.to_path_buf()
    } else {
        parent.to_path_buf()
    };
    let ud = root.join("userdata");
    if std::fs::create_dir_all(&ud).is_ok() {
        let probe = ud.join(".deskpet-write-probe");
        if std::fs::write(&probe, b"").is_ok() {
            let _ = std::fs::remove_file(&probe);
            return Some(ud);
        }
    }
    None
}
```
保持 `#[cfg(test)]` 版（返回 None）不变。`user_data_dir_with` 的优先级链（env → portable → AppData）不变。

## 2. `tauri-app\src-tauri\src\process_manager.rs` — `spawn_once` 注入 env
先读 `spawn_once`（约 148-219），找到已设 `.env("PYTHONIOENCODING", ...)` / `DESKPET_CLOUD_API_KEY` 的位置。在同段加入（用 Rust 解析出的唯一 userdata，覆盖 backend 的独立解析）：
```rust
// 路径单一事实源：把 Rust 解析出的 userdata 钉给 backend，
// 防 Rust/Python 双解析漂移（config.toml/state.db 落不同目录的根因）。
if let Some(ud) = crate::paths::user_data_dir() {
    let _ = std::fs::create_dir_all(&ud);
    cmd.env("DESKPET_USER_DATA_DIR", ud.to_string_lossy().to_string());
}
```
（确认 `crate::paths::user_data_dir()` 可见；若 `cmd` 变量名不同则按实际。`DESKPET_USER_DATA_DIR` 名称必须与 Python `backend/paths.py` priority-1 读的完全一致。）

## 3. 测试
- `paths.rs`：若已有可注入 exe 路径的 `*_with` 测试结构，加用例验证 portable 解析（create-then-return）。若现有测试架构不便注入 `current_exe`，至少新增/保留一个针对 `user_data_dir_with` 的用例：`DESKPET_USER_DATA_DIR` 设置时永远优先返回该值。**不得破坏现有 Rust 测试。**
- 如果给 `spawn_once` 注 env 难以单测（spawn 本身要起进程），不强求单测；但要保证编译通过。

## 验收（自己跑通）
```
cd G:\projects\deskpet\tauri-app\src-tauri
cargo build 2>&1 | tail -30
cargo test --lib 2>&1 | tail -40
```
`cargo build` 必须成功（这是最关键验收，因为装机版靠它重打包）。现有 `cargo test` 不回归。完成后输出改动摘要 + build/test 结果。若 cargo 因环境（缺 toolchain）跑不动，明确说明并保证代码语法/类型正确。
