// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

use tauri::Manager;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
#[cfg(target_os = "windows")]
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

mod artifact_ops;
mod backend_launch;
mod commands;
mod control_command_canonical;
mod crash_reports;
mod device;
mod diagnostics;
#[cfg(target_os = "windows")]
mod gpu_check;
mod job_object;
mod onboarding;
mod paths;
mod process_manager;
mod secrets;
mod user_data;
mod webview_permissions;
mod window_geometry;

use process_manager::BackendProcess;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Hook Rust panics before doing anything else so even early-init
    // failures leave a trace in crash_reports/.
    crash_reports::install_panic_hook();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        // P3-S2: dialog plugin for the "no NVIDIA GPU" fatal-error path.
        .plugin(tauri_plugin_dialog::init())
        // WI-T1.3 last-mile: clipboard for artifact_copy_path command.
        .plugin(tauri_plugin_clipboard_manager::init())
        // W5 (R17): self-update — endpoints + pubkey live in tauri.conf.json.
        // On first launch the plugin fetches latest.json; if it advertises a
        // newer version the built-in dialog prompts the user.
        .plugin(tauri_plugin_updater::Builder::new().build())
        // W5 (R17): opt-in login autostart. Pass an empty args slice so we
        // don't inject anything surprising into the user's shell.
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .manage(BackendProcess::new())
        .manage(window_geometry::ResizeDebouncer::new())
        .invoke_handler(tauri::generate_handler![
            process_manager::start_backend,
            process_manager::stop_backend,
            process_manager::is_backend_running,
            process_manager::get_shared_secret,
            process_manager::get_window_control_credential,
            // P3-S8: startup error surfacing for the frontend dialog.
            process_manager::get_startup_error,
            process_manager::clear_startup_error,
            // P3-S8/S9: user-data filesystem commands (open log dir,
            // open AppData dir, purge everything).
            // WI-T1.3 last-mile artifact actions (PRD §3 D3)
            artifact_ops::artifact_open,
            artifact_ops::artifact_show_in_folder,
            artifact_ops::artifact_copy_path,
            artifact_ops::artifact_save_as,
            // 2026-08-04 Workbench 改版 (WB-7): ArtifactsView 产物列表。
            artifact_ops::list_artifacts,
            user_data::open_log_dir,
            user_data::open_app_data_dir,
            user_data::purge_user_data,
            // 2026-05-21: data-dir relocator (Settings → 数据目录)
            user_data::get_data_dir_setting,
            user_data::set_data_dir_preference,
            user_data::move_data_dir_contents,
            // P2-1-S3: cloud LLM API key commands; UI invokes these from
            // SettingsPanel.
            secrets::set_cloud_api_key,
            secrets::get_cloud_api_key,
            secrets::delete_cloud_api_key,
            secrets::has_cloud_api_key,
            // W2 (relay integration): per-credential slots for the
            // closed-source RelayAuthAdapter. OSS build still registers
            // them — they're harmless if no UI ever invokes them.
            secrets::set_relay_access_token,
            secrets::get_relay_access_token,
            secrets::delete_relay_access_token,
            secrets::set_relay_refresh_token,
            secrets::get_relay_refresh_token,
            secrets::delete_relay_refresh_token,
            secrets::set_relay_device_key,
            secrets::get_relay_device_key,
            secrets::delete_relay_device_key,
            secrets::clear_all_relay_secrets,
            // W2: stable device id for the relay's X-Device-Id header.
            device::get_or_create_device_id,
            device::get_default_device_name,
            // P4-S21 #1: HTTP proxy from frontend to backend, bypasses
            // webview's https→http mixed-content block.
            commands::update_cloud_config,
            // P4-S21 #7: graceful Quit invoked by Toolbar ⏻ button.
            commands::app_exit,
            // P4-S22: native folder picker for Code mode entry.
            commands::open_directory_dialog,
            // WI-01 (beta-100): first-run onboarding state.
            onboarding::onboarding_status,
            onboarding::onboarding_complete,
            // WI-02 (beta-100): diagnostic feedback bundle.
            diagnostics::build_diagnostic_bundle,
            // 2026-05-26: 桌宠主窗 resize 持久化。
            window_geometry::get_saved_window_geometry,
            window_geometry::set_window_geometry,
        ])
        .setup(|app| {
            // P3-S2: NVIDIA precheck — Windows only. The Windows Phase-3
            // contract is CUDA-only (faster-whisper fp16 latency targets),
            // so on Windows we bail before the Python backend ever spawns
            // if no NVIDIA GPU is present. On macOS there is no NVML/CUDA;
            // the backend runs the CPU/MPS tier instead, so no precheck.
            #[cfg(target_os = "windows")]
            if let Err(e) = gpu_check::detect_nvidia_gpu() {
                eprintln!("[setup] gpu_check failed: {e:?}");
                let msg = gpu_check::format_user_message(&e);
                app.dialog()
                    .message(msg)
                    .title("硬件不支持")
                    .kind(MessageDialogKind::Error)
                    .buttons(MessageDialogButtons::Ok)
                    .blocking_show();
                app.handle().exit(1);
                // Still return Ok — the exit above will terminate the
                // process; returning Err here would just print a panic
                // trace on top of the dialog the user already saw.
                return Ok(());
            }

            // Auto-grant microphone permission on the main WebView2 so
            // getUserMedia works inside the desktop-pet window.
            if let Some(win) = app.get_webview_window("main") {
                if let Err(e) = webview_permissions::grant_media_permissions(&win) {
                    eprintln!("[setup] grant_media_permissions failed: {e:?}");
                }
                // 2026-05-26: 应用上次拉的尺寸；2026-06-02 起也恢复位置 + 显示器。
                // 读不到（首启 / 损坏）就走 tauri.conf.json 默认，无副作用。
                window_geometry::apply_saved_geometry(&win);
            }

            // P4-S22+ portable mode: pre-create `<install>/userdata/`
            // so the user can drop files there from a fresh install
            // (browse to it via a "Open data folder" tray entry,
            // future feature). Backend's `paths.py` will also lazily
            // mkdir on first access if this fails — strictly best-
            // effort here.
            if let Ok(exe) = std::env::current_exe() {
                if let Some(install_dir) = exe.parent() {
                    let _ = std::fs::create_dir_all(install_dir.join("userdata"));
                }
            }

            // P4-S21 #7: system tray icon with Show/Hide/Quit menu.
            // Without this the only way to surface a hidden main window
            // is to kill the process.
            // 2026-08-04 Workbench 改版（T14/WB-1, B10）：文案去桌宠品牌
            // （行为不变，只改字符串；tray id "deskpet-tray" 是标识符不动）。
            let tray_result = (|| -> tauri::Result<()> {
                let show_item = MenuItem::with_id(app, "show", "显示主窗", true, None::<&str>)?;
                let hide_item = MenuItem::with_id(app, "hide", "隐藏主窗", true, None::<&str>)?;
                let quit_item = MenuItem::with_id(app, "quit", "退出 Simple Harness", true, None::<&str>)?;
                let tray_menu = Menu::with_items(app, &[&show_item, &hide_item, &quit_item])?;
                TrayIconBuilder::with_id("deskpet-tray")
                    .icon(app.default_window_icon().cloned().expect("icon set in tauri.conf.json"))
                    .tooltip("Simple Harness")
                    .menu(&tray_menu)
                    .on_menu_event(|app, event| match event.id.as_ref() {
                        "show" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                        "hide" => {
                            if let Some(w) = app.get_webview_window("main") {
                                let _ = w.hide();
                            }
                        }
                        "quit" => {
                            if let Some(state) = app.try_state::<BackendProcess>() {
                                state.kill_child();
                            }
                            app.exit(0);
                        }
                        _ => {}
                    })
                    .build(app)?;
                Ok(())
            })();
            if let Err(e) = tray_result {
                eprintln!("[setup] tray initialization skipped: {e:?}");
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // 2026-05-26: 桌宠主窗 resize → 防抖落盘。
            if let tauri::WindowEvent::Resized(size) = event {
                eprintln!("[window_geometry] Resized event label={} size={}x{}", window.label(), size.width, size.height);
                if window.label() == "main" {
                    if let Some(deb) = window.try_state::<window_geometry::ResizeDebouncer>() {
                        deb.on_resize(window, *size);
                    } else {
                        eprintln!("[window_geometry] ResizeDebouncer state NOT FOUND");
                    }
                }
            }
            // 2026-06-02: 桌宠主窗 move（用户拖动）→ 防抖落盘位置，
            // 让桌宠记住所在显示器，下次启动恢复（多屏副屏放置）。
            if let tauri::WindowEvent::Moved(_pos) = event {
                if window.label() == "main" {
                    if let Some(deb) = window.try_state::<window_geometry::ResizeDebouncer>() {
                        deb.on_move(window);
                    }
                }
            }
            if let tauri::WindowEvent::Destroyed = event {
                // Only the main window's destroy means "quit the app";
                // any transient window (native dialogs, etc.) is a
                // non-event for the supervisor.
                if window.label() != "main" {
                    return;
                }
                if let Some(state) = window.try_state::<BackendProcess>() {
                    state.kill_child();
                }
                // 2026-08-04 Workbench 改版：应用只剩 main 一个窗口，
                // 关主窗 = 退出整个 app。保留显式 exit(0)：确保进程立刻
                // 结束 → job object 关闭 → python backend 被 OS 连带终止，
                // 不给 8100 端口残留留任何窗口期。
                window.app_handle().exit(0);
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        // 2026-08-06 r5 S16：Cmd+Q（macOS app terminate）不触发 main 窗口
        // Destroyed，红钮/托盘之外的第三条退出路径会把 backend 留成孤儿
        // （PPID=1，8100 继续 LISTEN）。RunEvent::Exit 是所有退出路径
        // （Cmd+Q / exit(0) / 托盘 quit）的统一必经点，在这里兜底
        // kill_child；Destroyed/托盘里的显式 kill 保留作先行路径。
        .run(|app_handle, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<BackendProcess>() {
                    state.kill_child();
                }
            }
        });
}
