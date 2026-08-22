// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! WI-02 (beta-100) — diagnostic feedback bundle.
//!
//! The "反馈" button in the toolbar calls `build_diagnostic_bundle`.
//! We gather only explicit, bounded SDK observability exports into a staging
//! dir, write a redacted `meta.json` + the user's bounded note, then zip it
//! with the platform-native archive tool (no new Rust dep).
//!
//! **Privacy contract — enforced here and by tests:**
//! - The bundle NEVER contains the API key. `llm_runtime.json` is NOT
//!   copied verbatim; only a redacted `{base_url, model}` pair goes
//!   into `meta.json`.
//! - `meta.json` is built field-by-field from a fixed allow-list — there
//!   is no "copy the whole config" path.
//! - OS credential store is never read.
//! - Ambient logs, crash reports, metrics, databases, outboxes and product
//!   content stores are never copied into the bundle.
//!
//! Failure philosophy: a missing source dir is recorded as `"missing"`
//! in `meta.json` and skipped — we never abort the whole bundle just
//! because one input is absent.

use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::{command, AppHandle};

use crate::paths;

const SDK_OBSERVABILITY_PER_FILE_MAX: u64 = 1_048_576;
const SDK_OBSERVABILITY_TOTAL_MAX: u64 = 2_621_440;
const USER_NOTE_MAX_BYTES: usize = 65_536;
const SDK_OBSERVABILITY_FILES: &[&str] = &[
    "sdk-observability-events.jsonl",
    "sdk-observability-events.jsonl.1",
    "sdk-observability-events.jsonl.2",
    "sdk-observability-ring.json",
    "sdk-observability-snapshot.json",
];

#[derive(Debug, Serialize)]
pub struct DiagnosticBundle {
    pub zip_path: String,
    pub size_bytes: u64,
    /// Per-source collection status, e.g. {"crash_reports": "ok", ...}.
    pub collected: std::collections::BTreeMap<String, String>,
}

fn timestamp() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn is_sensitive_storage_path(path: &Path) -> bool {
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    name == "state.db"
        || name == "memory.db"
        || name.contains("execution")
        || name.ends_with(".sqlite")
        || name.ends_with(".sqlite3")
        || name.ends_with(".db-wal")
        || name.ends_with(".db-shm")
        || name.contains("product_memory_outbox")
}

/// Recursively copy `src` dir into `dst` dir. Best-effort: returns the
/// number of files copied, swallows per-file errors.
fn copy_dir(src: &Path, dst: &Path) -> usize {
    let mut n = 0;
    if std::fs::create_dir_all(dst).is_err() {
        return 0;
    }
    let entries = match std::fs::read_dir(src) {
        Ok(e) => e,
        Err(_) => return 0,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if is_sensitive_storage_path(&path) {
            continue;
        }
        let name = entry.file_name();
        let target = dst.join(&name);
        if path.is_dir() {
            n += copy_dir(&path, &target);
        } else if std::fs::copy(&path, &target).is_ok() {
            n += 1;
        }
    }
    n
}

/// Copy the N most-recently-modified files from `src` into `dst`.
fn copy_recent_files(src: &Path, dst: &Path, keep: usize) -> usize {
    if std::fs::create_dir_all(dst).is_err() {
        return 0;
    }
    let mut files: Vec<(PathBuf, std::time::SystemTime)> = match std::fs::read_dir(src) {
        Ok(rd) => rd
            .flatten()
            .filter(|e| e.path().is_file())
            .filter(|e| !is_sensitive_storage_path(&e.path()))
            .filter_map(|e| {
                let m = e.metadata().ok()?.modified().ok()?;
                Some((e.path(), m))
            })
            .collect(),
        Err(_) => return 0,
    };
    files.sort_by(|a, b| b.1.cmp(&a.1)); // newest first
    let mut n = 0;
    for (path, _) in files.into_iter().take(keep) {
        if let Some(fname) = path.file_name() {
            if std::fs::copy(&path, dst.join(fname)).is_ok() {
                n += 1;
            }
        }
    }
    n
}

/// Read `llm_runtime.json` and return ONLY the non-secret fields.
/// The `api_key` field is dropped — never copied into the bundle.
fn redacted_provider_info(data_dir: &Path) -> serde_json::Value {
    let runtime = data_dir.join("llm_runtime.json");
    let parsed = std::fs::read_to_string(&runtime)
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok());
    match parsed {
        Some(v) => serde_json::json!({
            "base_url": v.get("base_url").and_then(|x| x.as_str()).unwrap_or(""),
            "model": v.get("model").and_then(|x| x.as_str()).unwrap_or(""),
            // NB: api_key intentionally absent — privacy contract.
            "has_api_key": v.get("api_key")
                .and_then(|x| x.as_str())
                .map(|s| !s.is_empty())
                .unwrap_or(false),
        }),
        None => serde_json::json!({"base_url": "", "model": "", "has_api_key": false}),
    }
}

fn dir_file_size(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

fn copy_sdk_observability(log_dir: &Path, staging: &Path) -> String {
    let destination = staging.join("sdk-observability");
    if std::fs::create_dir_all(&destination).is_err() {
        return "degraded:create_failed".into();
    }
    let mut total = 0_u64;
    let mut copied = 0_usize;
    let mut degraded = false;
    for name in SDK_OBSERVABILITY_FILES {
        let source = log_dir.join(name);
        let metadata = match std::fs::symlink_metadata(&source) {
            Ok(value) => value,
            Err(_) => continue,
        };
        if !metadata.file_type().is_file() || metadata.len() > SDK_OBSERVABILITY_PER_FILE_MAX {
            degraded = true;
            continue;
        }
        let bytes = match std::fs::read(&source) {
            Ok(value) => value,
            Err(_) => {
                degraded = true;
                continue;
            }
        };
        if contains_observability_canary(&bytes) {
            degraded = true;
            continue;
        }
        let next_total = total.saturating_add(metadata.len());
        if next_total > SDK_OBSERVABILITY_TOTAL_MAX {
            degraded = true;
            continue;
        }
        if std::fs::copy(&source, destination.join(name)).is_ok() {
            total = next_total;
            copied += 1;
        } else {
            degraded = true;
        }
    }
    if copied == 0 && degraded {
        "degraded:0:0".into()
    } else if copied == 0 {
        "missing".into()
    } else if degraded {
        format!("degraded:{copied}:{total}")
    } else {
        format!("ok:{copied}:{total}")
    }
}

fn contains_observability_canary(bytes: &[u8]) -> bool {
    const DENIED: &[&[u8]] = &[
        b"CANARY_",
        b"sk-CANARY",
        b"Bearer CANARY",
        b"\"authorization\"",
        b"\"cookie\"",
        b"\"token\"",
        b"\"password\"",
        b"\"content\"",
        b"\"body\"",
        b"\"exception\"",
    ];
    DENIED
        .iter()
        .any(|needle| bytes.windows(needle.len()).any(|window| window == *needle))
}

/// Build the diagnostic zip. `user_note` is the free-text problem
/// description from the feedback panel.
#[command]
pub fn build_diagnostic_bundle(
    app: AppHandle,
    user_note: String,
) -> Result<DiagnosticBundle, String> {
    let _ = &app; // version pulled below; keep handle for future use
    let data_dir =
        paths::user_data_dir().ok_or_else(|| "cannot resolve user data dir".to_string())?;
    let log_dir = paths::user_log_dir().unwrap_or_else(|| data_dir.join("logs"));

    let ts = timestamp();
    let staging = std::env::temp_dir().join(format!("deskpet-feedback-{ts}"));
    std::fs::create_dir_all(&staging).map_err(|e| format!("create staging dir failed: {e}"))?;

    let mut collected: std::collections::BTreeMap<String, String> =
        std::collections::BTreeMap::new();

    // Ambient logs/crash reports can contain provider or user content. They
    // are intentionally excluded; the SDK files below are the sole event
    // source admitted to the bundle.
    collected.insert("crash_reports".into(), "excluded".into());
    collected.insert("logs".into(), "excluded".into());

    // SDK observability is collected from an exact allow-list. Never walk the
    // product data directory or infer related DB/outbox/content files.
    collected.insert(
        "sdk_observability".into(),
        copy_sdk_observability(&log_dir, &staging),
    );

    collected.insert("metrics".into(), "excluded".into());

    // --- user note ---------------------------------------------------
    let note_bytes = user_note.as_bytes();
    let note_end = note_bytes.len().min(USER_NOTE_MAX_BYTES);
    let note_end = (0..=note_end)
        .rev()
        .find(|index| user_note.is_char_boundary(*index))
        .unwrap_or(0);
    let _ = std::fs::write(staging.join("user_note.txt"), &user_note[..note_end]);

    // --- meta.json (REDACTED, allow-list only) -----------------------
    let state_db_size = dir_file_size(&data_dir.join("data").join("state.db"));
    let workflow_db_size = dir_file_size(&data_dir.join("data").join("workflow.db"));
    let app_version = app
        .config()
        .version
        .clone()
        .unwrap_or_else(|| "unknown".to_string());
    let meta = serde_json::json!({
        "app_version": app_version,
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "state_db_bytes": state_db_size,
        "workflow_db_bytes": workflow_db_size,
        "provider": redacted_provider_info(&data_dir),  // api_key dropped
        "generated_at": ts,
        "note_len": user_note.chars().count(),
    });
    std::fs::write(
        staging.join("meta.json"),
        serde_json::to_string_pretty(&meta).unwrap_or_else(|_| "{}".into()),
    )
    .map_err(|e| format!("write meta.json failed: {e}"))?;

    // --- archive with the platform-native tool (no new Rust dep) ------
    let zip_path = std::env::temp_dir().join(format!("deskpet-feedback-{ts}.zip"));
    let zip_str = zip_path.to_string_lossy().to_string();
    #[cfg(target_os = "windows")]
    let output = {
        let staging_glob = format!("{}\\*", staging.to_string_lossy());
        let ps = format!(
            "Compress-Archive -Path '{}' -DestinationPath '{}' -Force",
            staging_glob, zip_str,
        );
        std::process::Command::new("powershell")
            .args(["-NoProfile", "-NonInteractive", "-Command", &ps])
            .output()
    };
    #[cfg(target_os = "macos")]
    let output = std::process::Command::new("ditto")
        .args(["-c", "-k", "--sequesterRsrc", "--keepParent"])
        .arg(&staging)
        .arg(&zip_path)
        .output();
    #[cfg(all(unix, not(target_os = "macos")))]
    let output = std::process::Command::new("zip")
        .current_dir(&staging)
        .args(["-q", "-r"])
        .arg(&zip_path)
        .arg(".")
        .output();
    let output = output.map_err(|e| format!("compress failed to spawn: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "diagnostic archive failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }

    let size = dir_file_size(&zip_path);

    // Reveal in the native file manager (best-effort).
    #[cfg(target_os = "windows")]
    let _ = std::process::Command::new("explorer")
        .args(["/select,", &zip_str])
        .spawn();
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open")
        .arg("-R")
        .arg(&zip_path)
        .spawn();
    #[cfg(all(unix, not(target_os = "macos")))]
    let _ = std::process::Command::new("xdg-open")
        .arg(zip_path.parent().unwrap_or_else(|| Path::new("/tmp")))
        .spawn();

    Ok(DiagnosticBundle {
        zip_path: zip_str,
        size_bytes: size,
        collected,
    })
}

#[cfg(test)]
mod sdk_observability_tests {
    use super::*;

    fn temp_root(label: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "simple-harness-diagnostics-{label}-{}-{}",
            std::process::id(),
            timestamp()
        ));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn sdk_observability_copy_is_allowlisted_and_excludes_content_stores() {
        let root = temp_root("allowlist");
        let source = root.join("logs");
        let staging = root.join("staging");
        std::fs::create_dir_all(&source).unwrap();
        std::fs::write(
            source.join("sdk-observability-events.jsonl"),
            b"{\"safe\":true}\n",
        )
        .unwrap();
        for forbidden in [
            "memory.db",
            "state.db",
            "product_memory_outbox.json",
            "content.json",
        ] {
            std::fs::write(source.join(forbidden), b"CANARY_MEMORY_BODY").unwrap();
        }

        assert!(copy_sdk_observability(&source, &staging).starts_with("ok:"));
        let copied = staging.join("sdk-observability");
        assert!(copied.join("sdk-observability-events.jsonl").is_file());
        for forbidden in [
            "memory.db",
            "state.db",
            "product_memory_outbox.json",
            "content.json",
        ] {
            assert!(!copied.join(forbidden).exists());
        }
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn sdk_observability_copy_degrades_for_oversized_and_missing_files() {
        let root = temp_root("bounds");
        let source = root.join("logs");
        let staging = root.join("staging");
        std::fs::create_dir_all(&source).unwrap();
        assert_eq!(copy_sdk_observability(&source, &staging), "missing");
        std::fs::write(
            source.join("sdk-observability-ring.json"),
            vec![b'x'; SDK_OBSERVABILITY_PER_FILE_MAX as usize + 1],
        )
        .unwrap();
        assert_eq!(copy_sdk_observability(&source, &staging), "degraded:0:0");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn sdk_observability_copy_rejects_canary_files_without_blocking_safe_files() {
        let root = temp_root("canary");
        let source = root.join("logs");
        let staging = root.join("staging");
        std::fs::create_dir_all(&source).unwrap();
        std::fs::write(
            source.join("sdk-observability-events.jsonl"),
            b"{\"event_name\":\"safe\"}\n",
        )
        .unwrap();
        std::fs::write(
            source.join("sdk-observability-snapshot.json"),
            b"{\"value\":\"CANARY_MEMORY_BODY_secret\"}",
        )
        .unwrap();
        assert!(copy_sdk_observability(&source, &staging).starts_with("degraded:1:"));
        assert!(staging
            .join("sdk-observability/sdk-observability-events.jsonl")
            .is_file());
        assert!(!staging
            .join("sdk-observability/sdk-observability-snapshot.json")
            .exists());
        let _ = std::fs::remove_dir_all(root);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn redacted_provider_drops_api_key() {
        let tmp = std::env::temp_dir().join(format!("dpd-test-{}", timestamp()));
        fs::create_dir_all(&tmp).unwrap();
        fs::write(
            tmp.join("llm_runtime.json"),
            r#"{"base_url":"https://x/v1","model":"gpt-5","api_key":"sk-SECRET-zzz"}"#,
        )
        .unwrap();
        let info = redacted_provider_info(&tmp);
        let s = serde_json::to_string(&info).unwrap();
        // The actual key value must never appear.
        assert!(
            !s.contains("sk-SECRET-zzz"),
            "api_key value leaked into meta!"
        );
        // The `api_key` *field* (quoted key name) must be absent — note
        // `has_api_key` is a different, allowed boolean field, so we
        // match the exact quoted token `"api_key"`.
        assert!(
            !s.contains("\"api_key\""),
            "api_key field leaked into meta!"
        );
        assert!(s.contains("https://x/v1"));
        assert!(s.contains("\"has_api_key\":true"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn redacted_provider_missing_file_is_safe() {
        let tmp = std::env::temp_dir().join(format!("dpd-test-missing-{}", timestamp()));
        let info = redacted_provider_info(&tmp);
        assert_eq!(info.get("base_url").unwrap().as_str().unwrap(), "");
        assert_eq!(info.get("has_api_key").unwrap().as_bool().unwrap(), false);
    }

    #[test]
    fn copy_recent_keeps_newest_n() {
        let src = std::env::temp_dir().join(format!("dpd-src-{}", timestamp()));
        let dst = std::env::temp_dir().join(format!("dpd-dst-{}", timestamp()));
        fs::create_dir_all(&src).unwrap();
        for i in 0..6 {
            fs::write(src.join(format!("log{i}.txt")), format!("line {i}")).unwrap();
        }
        let n = copy_recent_files(&src, &dst, 3);
        assert_eq!(n, 3);
        let kept = fs::read_dir(&dst).unwrap().count();
        assert_eq!(kept, 3);
        let _ = fs::remove_dir_all(&src);
        let _ = fs::remove_dir_all(&dst);
    }

    #[test]
    fn diagnostic_copy_excludes_agent_storage_and_sidecars() {
        let src = std::env::temp_dir().join(format!("dpd-sensitive-src-{}", timestamp()));
        let dst = std::env::temp_dir().join(format!("dpd-sensitive-dst-{}", timestamp()));
        fs::create_dir_all(&src).unwrap();
        for name in [
            "state.db",
            "state.db-wal",
            "memory.db",
            "execution-v1.sqlite3",
            "safe.log",
        ] {
            fs::write(src.join(name), "PRIVATE-CANARY").unwrap();
        }
        assert_eq!(copy_dir(&src, &dst), 1);
        assert!(dst.join("safe.log").is_file());
        assert!(!dst.join("state.db").exists());
        assert!(!dst.join("memory.db").exists());
        assert!(!dst.join("execution-v1.sqlite3").exists());
        let _ = fs::remove_dir_all(&src);
        let _ = fs::remove_dir_all(&dst);
    }
}
