// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { createRoot } from "react-dom/client";
import "./index.css";
import { applyTheme, watchSystemTheme } from "./theme/applyTheme";
import "./main.styles.css";

// 设计令牌变量表必须先于任何组件渲染注入（深浅两套镜像）。
applyTheme();
watchSystemTheme();

// 2026-09-25 UI 全量点击：macOS 的 WebView 会对输入框做自动更正/首字母大写
// （模型名 "abc" 失焦后变成 "Abc"，判据 "pytest:" 也可能被改）。本程序的输入
// 大量是标识符、路径、模型名和判据，一律关掉自动更正、自动大写和拼写检查。
document.addEventListener(
  "focusin",
  (event) => {
    const el = event.target;
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      el.setAttribute("autocorrect", "off");
      el.setAttribute("autocapitalize", "off");
      el.spellcheck = false;
    }
  },
  true,
);

// 真 UI 验证 route — `#/slashtest` 独立渲染 InputBar 让真浏览器
// E2E 能真触发 / 命令 autocomplete 状态机（无需 Tauri invoke 创 session）.
const isSlashTest = window.location.hash.startsWith("#/slashtest");

// 底色已由 applyTheme() 按当前主题刷好（冷启动不闪色），这里不再写死。

// StrictMode intentionally stays disabled because duplicate effect mounts open
// duplicate WebSocket connections and repeat heavyweight media initialization.
const root = createRoot(document.getElementById("root")!);

if (isSlashTest) {
  // v2 真 UI 验证 — 独立渲染 InputBar 让浏览器 E2E 真触发 / 命令补全.
  import("./code-panel/SlashTestHarness")
    .then(({ SlashTestHarness }) => root.render(<SlashTestHarness />))
    .catch((e) => {
      console.error("[main] slashtest load failed:", e);
      root.render(<div className="sh-main-1">Failed to load slashtest: {String(e)}</div>);
    });
} else {
  // Pet shell eagerly imports — needs to start fast.
  import("./App").then(({ default: App }) => root.render(<App />));
}
