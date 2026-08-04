// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

//! WebView2 permission handler — auto-grants microphone (and camera) to the
//! embedded WebView2 so `navigator.mediaDevices.getUserMedia` works inside the
//! desktop-pet window without any user prompt.
//!
//! Windows-only. No-op on other platforms.

/// Privileged Companion IPC is scoped to a real Tauri window label.
/// The renderer never supplies this label; the command receives an injected
/// `WebviewWindow` and checks this table before signing.
///
/// 2026-08-04 Workbench UI 改版（WB-4 前置 / behavior-contract B5）：
/// message-panel 窗口已并入主窗，companion_action 的合法窗口标签从
/// "message-panel" 迁移为 "main"。同一迁移必须五处同步（本白名单 /
/// Python ALLOWED_WINDOW_SCOPES / control_ingress expected_window_label /
/// SQL CHECK 迁移 007 / 前端 controlWs 常量），缺一处即
/// window_control_scope_denied 静默降级。
pub fn authorize_window_control_scope(label: &str, scope: &str) -> Result<(), String> {
    match (label, scope) {
        ("main", "identity_bind") | ("main", "companion_action") => Ok(()),
        _ => Err(format!("window_control_scope_denied:{label}:{scope}")),
    }
}

#[cfg(target_os = "windows")]
pub fn grant_media_permissions(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_CAMERA,
        COREWEBVIEW2_PERMISSION_KIND_MICROPHONE, COREWEBVIEW2_PERMISSION_STATE_ALLOW,
    };
    use webview2_com::PermissionRequestedEventHandler;

    window.with_webview(|webview| {
        unsafe {
            let controller = webview.controller();
            let core = match controller.CoreWebView2() {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("[permissions] failed to get CoreWebView2: {e:?}");
                    return;
                }
            };

            let handler =
                PermissionRequestedEventHandler::create(Box::new(|_sender, args| {
                    if let Some(args) = args {
                        let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                        args.PermissionKind(&mut kind)?;
                        if kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE
                            || kind == COREWEBVIEW2_PERMISSION_KIND_CAMERA
                        {
                            args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                            eprintln!(
                                "[permissions] auto-granted media permission (kind={:?})",
                                kind
                            );
                        }
                    }
                    Ok(())
                }));

            let mut token: i64 = 0;
            if let Err(e) = core.add_PermissionRequested(&handler, &mut token) {
                eprintln!("[permissions] add_PermissionRequested failed: {e:?}");
            } else {
                eprintln!("[permissions] PermissionRequested handler installed");
            }
        }
    })?;
    Ok(())
}

#[cfg(not(target_os = "windows"))]
pub fn grant_media_permissions(_window: &tauri::WebviewWindow) -> tauri::Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::authorize_window_control_scope;

    // 2026-08-04 Workbench 改版断言翻转：companion_action 迁 main，
    // message-panel 窗口已删除（绑定 acceptance「only-add 显式删除例外」）。
    #[test]
    fn privileged_scopes_are_label_bound() {
        assert!(authorize_window_control_scope("main", "identity_bind").is_ok());
        assert!(authorize_window_control_scope("main", "companion_action").is_ok());
        assert!(authorize_window_control_scope("message-panel", "companion_action").is_err());
        assert!(authorize_window_control_scope("message-panel", "identity_bind").is_err());
        assert!(authorize_window_control_scope("code-panel", "companion_action").is_err());
        assert!(authorize_window_control_scope("main", "general").is_err());
    }
}
