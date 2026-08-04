// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// P4-S21 commands — frontend↔backend IPC bridge for things webview
// can't do directly (mixed-content fetch, app exit).
//
// `update_cloud_config` is the dramatic one: in release builds the
// webview origin is `https://tauri.localhost`, and `fetch("http://...")`
// is silently blocked as mixed content. The `Settings → Save` UX broke
// because of this. Solution: route the POST through Rust, which has no
// mixed-content notion and already knows the SHARED_SECRET. Frontend
// gets a clean Promise back via `invoke()`.
//
// `app_exit` is trivial — the toolbar Quit button calls it. We can't
// use `window.close()` from the renderer because, well, the renderer's
// process is the app. Going through the AppHandle ensures backend
// supervisor teardown via the existing WindowEvent::Destroyed handler.

use serde_json::Value;
use std::time::Duration;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

use crate::process_manager::BackendProcess;

fn backend_http_client() -> Result<reqwest::Client, reqwest::Error> {
    reqwest::Client::builder()
        // This client only talks to the loopback FastAPI backend. System
        // proxies can hijack 127.0.0.1 and return empty 502 responses.
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
}

/// POST /config/cloud on behalf of the frontend.
///
/// We deliberately **don't** accept the secret as a parameter — Rust
/// already has it in `BackendProcess::shared_secret`, and exposing it
/// to the renderer just to round-trip back is pointless attack surface.
/// We also pin the URL to localhost so a compromised renderer can't
/// redirect the call to an attacker-controlled host.
#[tauri::command]
pub async fn update_cloud_config(
    state: State<'_, BackendProcess>,
    update: Value,
) -> Result<Value, String> {
    let secret = state
        .shared_secret_clone()
        .ok_or_else(|| "backend not running yet".to_string())?;
    let port = state.port();

    let url = format!("http://127.0.0.1:{}/config/cloud", port);
    let client = backend_http_client().map_err(|e| format!("reqwest client init: {e}"))?;

    let resp = client
        .post(&url)
        .header("X-Shared-Secret", &secret)
        .json(&update)
        .send()
        .await
        .map_err(|e| format!("backend POST {url} failed: {e}"))?;

    let status = resp.status();
    let body: Value = resp
        .json()
        .await
        .map_err(|e| format!("backend response not JSON: {e}"))?;

    if !status.is_success() {
        // Surface the backend-rendered error so the frontend can show it.
        let err_text = body
            .get("detail")
            .and_then(|d| d.as_str())
            .map(String::from)
            .unwrap_or_else(|| body.to_string());
        return Err(format!("backend {}: {}", status.as_u16(), err_text));
    }

    Ok(body)
}

/// Quit the app gracefully. Closing the main window triggers the
/// existing WindowEvent::Destroyed handler in lib.rs which kills the
/// backend supervisor — `app.exit(0)` does the same end-to-end.
#[tauri::command]
pub fn app_exit(app: AppHandle) {
    if let Some(state) = app.try_state::<BackendProcess>() {
        state.kill_child();
    }
    app.exit(0);
}

#[cfg(test)]
mod tests {
    use super::backend_http_client;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::thread;
    use std::time::Duration;

    fn serve_once(response: &'static str) -> std::io::Result<(u16, thread::JoinHandle<()>)> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        listener.set_nonblocking(true)?;
        let port = listener.local_addr()?.port();
        let handle = thread::spawn(move || {
            let deadline = std::time::Instant::now() + Duration::from_secs(5);
            loop {
                match listener.accept() {
                    Ok((mut stream, _)) => {
                        let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
                        let mut buf = [0_u8; 1024];
                        let _ = stream.read(&mut buf);
                        let _ = stream.write_all(response.as_bytes());
                        return;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                        if std::time::Instant::now() >= deadline {
                            return;
                        }
                        thread::sleep(Duration::from_millis(20));
                    }
                    Err(_) => return,
                }
            }
        });
        Ok((port, handle))
    }

    fn with_proxy_env<T>(proxy: &str, f: impl FnOnce() -> T) -> T {
        let old_http = std::env::var("HTTP_PROXY").ok();
        let old_https = std::env::var("HTTPS_PROXY").ok();
        let old_all = std::env::var("ALL_PROXY").ok();
        let old_no_proxy = std::env::var("NO_PROXY").ok();
        let old_no_proxy_lower = std::env::var("no_proxy").ok();

        unsafe {
            std::env::set_var("HTTP_PROXY", proxy);
            std::env::set_var("HTTPS_PROXY", proxy);
            std::env::set_var("ALL_PROXY", proxy);
            std::env::remove_var("NO_PROXY");
            std::env::remove_var("no_proxy");
        }

        let result = f();

        unsafe {
            restore_env("HTTP_PROXY", old_http);
            restore_env("HTTPS_PROXY", old_https);
            restore_env("ALL_PROXY", old_all);
            restore_env("NO_PROXY", old_no_proxy);
            restore_env("no_proxy", old_no_proxy_lower);
        }

        result
    }

    unsafe fn restore_env(key: &str, value: Option<String>) {
        match value {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        }
    }

    #[test]
    fn backend_http_client_bypasses_proxy_for_loopback_backend() {
        let target_response = concat!(
            "HTTP/1.1 200 OK\r\n",
            "Content-Type: application/json\r\n",
            "Content-Length: 11\r\n",
            "\r\n",
            "{\"ok\":true}"
        );
        let proxy_response = concat!(
            "HTTP/1.1 502 Bad Gateway\r\n",
            "Content-Length: 0\r\n",
            "\r\n"
        );

        let (target_port, target_handle) = serve_once(target_response).expect("target server");
        let (proxy_port, proxy_handle) = serve_once(proxy_response).expect("proxy server");
        let proxy_url = format!("http://127.0.0.1:{proxy_port}");

        let body = with_proxy_env(&proxy_url, || {
            tauri::async_runtime::block_on(async {
                backend_http_client()
                    .expect("client")
                    .get(format!("http://127.0.0.1:{target_port}/health"))
                    .send()
                    .await
                    .expect("request")
                    .text()
                    .await
                    .expect("body")
            })
        });

        drop(TcpStream::connect(("127.0.0.1", target_port)));
        drop(TcpStream::connect(("127.0.0.1", proxy_port)));
        let _ = target_handle.join();
        let _ = proxy_handle.join();

        assert_eq!(body, "{\"ok\":true}");
    }
}

// 2026-08-04 Workbench UI 改版（WB-2）：message-panel 独立窗口删除，
// 其 4 个 Tauri command（open/close/dock/toggle_message_panel）与
// dock_message_panel_impl / emit_panel_visibility 一并移除
//（acceptance「only-add 显式删除例外」）。

/// Open a native folder picker and return the selected absolute path.
/// Settings and workspace selection share this single native dialog.
#[tauri::command]
pub async fn open_directory_dialog(app: AppHandle) -> Result<Option<String>, String> {
    let mut dialog = app.dialog().file().set_title("选择项目文件夹");
    // Keep the native picker owned by the main window. An unowned Windows
    // IFileDialog can open behind the WebView (or be treated as immediately
    // cancelled), leaving the card looking unresponsive.
    if let Some(parent) = app.get_webview_window("main") {
        dialog = dialog.set_parent(&parent);
    }
    // rfd's blocking API owns the full native dialog lifecycle. Run it on the
    // blocking pool so the Tauri UI/event loop remains free to present and
    // service the modal window.
    let selected = tauri::async_runtime::spawn_blocking(move || {
        dialog.blocking_pick_folder()
    })
    .await
    .map_err(|error| format!("directory picker worker failed: {error}"))?;
    Ok(selected
        .and_then(|path| path.into_path().ok())
        .map(|path| path.to_string_lossy().to_string()))
}
