// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! P3-S8 — Rust-side mirror of `backend/paths.py`.
//!
//! Both the Tauri supervisor and the Python backend need to agree on
//! *where* user data lives (`%AppData%\deskpet\`) so the UI can open
//! log / data directories without round-tripping through the backend.
//! We intentionally re-derive the paths here instead of asking the
//! backend: the "open log dir" button must work **even when the backend
//! refuses to start** (that's precisely when the user most needs it).
//!
//! Priority (per env var):
//!   user_data_dir  → explicit env || stable preference || portable || `%AppData%\deskpet`
//!   user_log_dir   → `$DESKPET_USER_LOG`  || `<user_data>\logs`
//!   user_models_dir → `$DESKPET_MODEL_ROOT` || `%LocalAppData%\deskpet\models`
//!
//! The `resolve_*_with` variants accept an env-lookup closure and a
//! pair of base-dir overrides (AppData / LocalAppData) so tests can
//! exercise every branch without mutating real process environment.

use std::path::{Path, PathBuf};

pub type EnvLookup<'a> = &'a dyn Fn(&str) -> Option<String>;

const USER_DATA_PREFERENCE_ENV: &str = "DESKPET_USER_DATA_PREFERENCE_FILE";
const USER_DATA_PREFERENCE_DIR: &str = "simple-harness-bootstrap";
const USER_DATA_PREFERENCE_FILE: &str = "user-data-dir";

#[derive(Debug, Clone)]
pub struct BaseDirs {
    pub app_data: Option<PathBuf>,      // %AppData% (Roaming)
    pub local_app_data: Option<PathBuf>, // %LocalAppData%
}

impl BaseDirs {
    /// Resolve base dirs from the real OS environment.
    ///
    /// 平台对齐 Python 侧 `backend/paths.py`（platformdirs, roaming=True）：
    /// Windows `%AppData%` / macOS `~/Library/Application Support` /
    /// Linux `$XDG_DATA_HOME`（缺省 `~/.local/share`）。调用方统一再
    /// join("deskpet")，保证 Rust 与 Python 落同一个用户数据目录
    /// （否则 onboarding 标记 / device_id 与 config/db 分家 — mac 上
    /// 曾因只读 APPDATA 直接返回 None，onboarding 向导永远弹出）。
    #[cfg(all(not(test), windows))]
    pub fn from_env() -> Self {
        Self {
            app_data: std::env::var("APPDATA").ok().map(PathBuf::from),
            local_app_data: std::env::var("LOCALAPPDATA").ok().map(PathBuf::from),
        }
    }

    #[cfg(all(not(test), target_os = "macos"))]
    pub fn from_env() -> Self {
        let base = std::env::var("HOME").ok().map(|h| {
            PathBuf::from(h).join("Library").join("Application Support")
        });
        Self { app_data: base.clone(), local_app_data: base }
    }

    #[cfg(all(not(test), unix, not(target_os = "macos")))]
    pub fn from_env() -> Self {
        let home = std::env::var("HOME").ok().map(PathBuf::from);
        let data = std::env::var("XDG_DATA_HOME")
            .ok()
            .filter(|s| !s.is_empty())
            .map(PathBuf::from)
            .or_else(|| home.map(|h| h.join(".local").join("share")));
        Self { app_data: data.clone(), local_app_data: data }
    }

    #[cfg(test)]
    pub fn from_env() -> Self {
        // Under cfg(test) we never want to touch real %AppData%.
        Self { app_data: None, local_app_data: None }
    }
}

fn resolve_with(
    env_key: &str,
    env_lookup: EnvLookup<'_>,
    fallback: Option<PathBuf>,
) -> Option<PathBuf> {
    if let Some(v) = env_lookup(env_key).filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    fallback
}

/// Portable-mode userdata dir: when running from a frozen install, data
/// lives in `<install>/userdata/` next to the exe (mirrors Python
/// `backend/paths.py::_portable_userdata_dir`). Returns None in dev mode
/// (current_exe isn't in the install layout) or when userdata/ is not writable.
///
/// Layout: `<install>/deskpet.exe` + `<install>/userdata/`; the backend
/// exe sits at `<install>/backend/deskpet-backend.exe`, so we also check
/// one level up when the exe's parent is named "backend".
fn portable_userdata_dir_from_exe(exe: &Path) -> Option<PathBuf> {
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

#[cfg(not(test))]
fn portable_userdata_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    portable_userdata_dir_from_exe(&exe)
}

#[cfg(test)]
fn portable_userdata_dir() -> Option<PathBuf> {
    // 测试下不探测真实 current_exe(避免 test runner 旁碰巧的 userdata 干扰
    // 现有单测的 %AppData% 期望)。
    None
}

fn user_data_preference_path_with(
    base: &BaseDirs,
    env_lookup: EnvLookup<'_>,
) -> Option<PathBuf> {
    if let Some(v) = env_lookup(USER_DATA_PREFERENCE_ENV).filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    base.app_data
        .as_ref()
        .map(|p| p.join(USER_DATA_PREFERENCE_DIR).join(USER_DATA_PREFERENCE_FILE))
}

fn read_user_data_preference_with(
    base: &BaseDirs,
    env_lookup: EnvLookup<'_>,
) -> Option<String> {
    let path = user_data_preference_path_with(base, env_lookup)?;
    std::fs::read_to_string(path)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

pub fn user_data_dir_with_preference(
    base: &BaseDirs,
    env_lookup: EnvLookup<'_>,
    preference: Option<&str>,
) -> Option<PathBuf> {
    user_data_dir_with_candidates(
        base,
        env_lookup,
        preference,
        portable_userdata_dir(),
    )
}

fn user_data_dir_with_candidates(
    base: &BaseDirs,
    env_lookup: EnvLookup<'_>,
    preference: Option<&str>,
    portable: Option<PathBuf>,
) -> Option<PathBuf> {
    // 1. DESKPET_USER_DATA_DIR — 与 Python backend/paths.py 统一的 env 名
    //    (历史 bug: Rust 读 DESKPET_USER_DATA、Python 读 DESKPET_USER_DATA_DIR,
    //    名字不一致 → device_id/onboarding 和 config/db 落到不同目录)。
    if let Some(v) = env_lookup("DESKPET_USER_DATA_DIR").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    // 2. 兼容旧 DESKPET_USER_DATA(无 _DIR) — 老用户可能设过,别破坏。
    if let Some(v) = env_lookup("DESKPET_USER_DATA").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    // 3. 跨平台稳定偏好。偏好文件放在用户数据目录之外，目录迁移时不会把
    //    指向新目录的指针一起搬走。
    if let Some(v) = preference.filter(|s| !s.trim().is_empty()) {
        return Some(PathBuf::from(v.trim()));
    }
    // 4. portable: frozen 安装时 <install>/userdata(与 Python portable 一致)。
    //    稳定偏好优先，否则 debug 目录里残留的 target/debug/userdata 会把
    //    用户在设置页明确选择的新目录静默压回旧路径。
    if let Some(p) = portable {
        return Some(p);
    }
    // 5. classic: %AppData%\deskpet。
    base.app_data.as_ref().map(|p| p.join("deskpet"))
}

pub fn user_data_dir_with(base: &BaseDirs, env_lookup: EnvLookup<'_>) -> Option<PathBuf> {
    user_data_dir_with_preference(base, env_lookup, None)
}

pub fn user_log_dir_with(base: &BaseDirs, env_lookup: EnvLookup<'_>) -> Option<PathBuf> {
    if let Some(v) = env_lookup("DESKPET_USER_LOG").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    user_data_dir_with(base, env_lookup).map(|p| p.join("logs"))
}

pub fn user_models_dir_with(base: &BaseDirs, env_lookup: EnvLookup<'_>) -> Option<PathBuf> {
    if let Some(v) = env_lookup("DESKPET_MODEL_ROOT").filter(|s| !s.is_empty()) {
        return Some(PathBuf::from(v));
    }
    // portable: <install>/userdata/models(与 Python paths.py 一致)。
    if let Some(p) = portable_userdata_dir() {
        return Some(p.join("models"));
    }
    base.local_app_data.as_ref().map(|p| p.join("deskpet").join("models"))
}

// ---- Public convenience wrappers reading real env ----
// Note: BaseDirs::from_env() returns empty under cfg(test), so these
// helpers safely no-op in unit-test binaries instead of leaking into
// the real %AppData%.

fn real_env(k: &str) -> Option<String> { std::env::var(k).ok() }

pub fn user_data_dir() -> Option<PathBuf> {
    let base = BaseDirs::from_env();
    let preference = read_user_data_preference_with(&base, &real_env);
    user_data_dir_with_preference(&base, &real_env, preference.as_deref())
}

pub fn user_data_preference() -> Option<String> {
    read_user_data_preference_with(&BaseDirs::from_env(), &real_env)
}

pub fn write_user_data_preference(value: &Path) -> std::io::Result<()> {
    let base = BaseDirs::from_env();
    let path = user_data_preference_path_with(&base, &real_env)
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::NotFound, "app data unavailable"))?;
    write_user_data_preference_at(&path, value)
}

fn write_user_data_preference_at(path: &Path, value: &Path) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(path, value.to_string_lossy().as_bytes())
}

pub fn user_log_dir() -> Option<PathBuf> {
    user_log_dir_with(&BaseDirs::from_env(), &real_env)
}

pub fn user_models_dir() -> Option<PathBuf> {
    user_models_dir_with(&BaseDirs::from_env(), &real_env)
}

/// Ensure `path` exists, creating parent dirs as needed. No-op if Some
/// already points to an existing directory. Returns the path back for
/// chaining in the Tauri command handlers.
#[allow(dead_code)]
pub fn ensure_dir(path: &Path) -> std::io::Result<()> {
    if path.is_dir() {
        return Ok(());
    }
    std::fs::create_dir_all(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env_map(pairs: &'static [(&'static str, &'static str)]) -> impl Fn(&str) -> Option<String> {
        move |k: &str| {
            pairs
                .iter()
                .find(|(kk, _)| *kk == k)
                .map(|(_, v)| v.to_string())
        }
    }

    fn env_empty() -> impl Fn(&str) -> Option<String> {
        |_: &str| None
    }

    fn base_win() -> BaseDirs {
        BaseDirs {
            app_data: Some(PathBuf::from("C:/Users/U/AppData/Roaming")),
            local_app_data: Some(PathBuf::from("C:/Users/U/AppData/Local")),
        }
    }

    #[test]
    fn user_data_dir_defaults_to_appdata_deskpet() {
        let env = env_empty();
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("C:/Users/U/AppData/Roaming/deskpet"));
    }

    #[test]
    fn user_data_preference_write_is_stable_and_trim_safe() {
        let root = std::env::temp_dir().join(format!(
            "simple-harness-paths-test-{}",
            std::process::id()
        ));
        let preference_file = root.join("bootstrap").join("user-data-dir");
        let chosen = root.join("chosen-data");
        write_user_data_preference_at(&preference_file, &chosen).unwrap();
        assert_eq!(std::fs::read_to_string(&preference_file).unwrap(), chosen.to_string_lossy());
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn user_data_dir_env_override_wins() {
        let env = env_map(&[("DESKPET_USER_DATA", "D:/custom/deskpet")]);
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("D:/custom/deskpet"));
    }

    #[test]
    fn user_data_dir_prefers_dir_suffix_env() {
        // 统一后: DESKPET_USER_DATA_DIR(与 Python 一致)优先于旧 DESKPET_USER_DATA。
        let env = env_map(&[
            ("DESKPET_USER_DATA_DIR", "D:/new/userdata"),
            ("DESKPET_USER_DATA", "E:/old/deskpet"),
        ]);
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("D:/new/userdata"));
    }

    #[test]
    fn portable_userdata_dir_creates_install_userdata_for_backend_exe() {
        let root = std::env::temp_dir().join(format!(
            "deskpet-portable-test-{}",
            std::process::id()
        ));
        let backend_dir = root.join("backend");
        std::fs::create_dir_all(&backend_dir).unwrap();
        let exe = backend_dir.join("deskpet-backend.exe");

        let out = portable_userdata_dir_from_exe(&exe).unwrap();

        assert_eq!(out, root.join("userdata"));
        assert!(out.is_dir());

        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn user_data_dir_falls_back_to_legacy_env() {
        // 只设旧 DESKPET_USER_DATA 时仍兼容(不破坏老用户)。
        let env = env_map(&[("DESKPET_USER_DATA", "E:/old/deskpet")]);
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("E:/old/deskpet"));
    }

    #[test]
    fn user_data_dir_empty_env_treated_as_unset() {
        let env = env_map(&[("DESKPET_USER_DATA", "")]);
        let out = user_data_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("C:/Users/U/AppData/Roaming/deskpet"));
    }

    #[test]
    fn user_data_dir_uses_stable_preference_after_env_and_portable() {
        let env = env_empty();
        let out = user_data_dir_with_preference(
            &base_win(),
            &env,
            Some("F:/simple-harness-test-profile"),
        )
        .unwrap();
        assert_eq!(out, PathBuf::from("F:/simple-harness-test-profile"));
    }

    #[test]
    fn explicit_dir_env_wins_over_stable_preference() {
        let env = env_map(&[("DESKPET_USER_DATA_DIR", "D:/externally-pinned")]);
        let out = user_data_dir_with_preference(
            &base_win(),
            &env,
            Some("F:/saved-preference"),
        )
        .unwrap();
        assert_eq!(out, PathBuf::from("D:/externally-pinned"));
    }

    #[test]
    fn stable_preference_wins_over_portable_candidate() {
        let env = env_empty();
        let out = user_data_dir_with_candidates(
            &base_win(),
            &env,
            Some("F:/saved-preference"),
            Some(PathBuf::from("C:/debug/userdata")),
        )
        .unwrap();
        assert_eq!(out, PathBuf::from("F:/saved-preference"));
    }

    #[test]
    fn user_log_dir_nests_under_user_data() {
        let env = env_empty();
        let out = user_log_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("C:/Users/U/AppData/Roaming/deskpet/logs"));
    }

    #[test]
    fn user_log_dir_independent_env_override() {
        let env = env_map(&[("DESKPET_USER_LOG", "E:/logs")]);
        let out = user_log_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("E:/logs"));
    }

    #[test]
    fn user_models_dir_defaults_to_local_app_data() {
        let env = env_empty();
        let out = user_models_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("C:/Users/U/AppData/Local/deskpet/models"));
    }

    #[test]
    fn user_models_dir_env_override() {
        let env = env_map(&[("DESKPET_MODEL_ROOT", "F:/models")]);
        let out = user_models_dir_with(&base_win(), &env).unwrap();
        assert_eq!(out, PathBuf::from("F:/models"));
    }

    #[test]
    fn missing_app_data_returns_none() {
        let base = BaseDirs { app_data: None, local_app_data: None };
        let env = env_empty();
        assert!(user_data_dir_with(&base, &env).is_none());
        assert!(user_log_dir_with(&base, &env).is_none());
        assert!(user_models_dir_with(&base, &env).is_none());
    }
}
