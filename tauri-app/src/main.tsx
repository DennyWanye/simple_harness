// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { createRoot } from 'react-dom/client'
import './index.css'
import { BACKEND_PORT } from './backendPort'

// WI-T1.7 last-mile: 全局 metric emit sink。
// ArtifactCard 按钮点击会调 window.__deskpet_metrics_emit；这里 wire 到
// backend POST /metrics/event，最终落地 <user_data>/metrics.jsonl。
// 无 backend 时静默丢（dev/test 友好），不破 UI。
declare global {
  interface Window {
    __deskpet_metrics_emit?: (event: string, payload: Record<string, unknown>) => void;
  }
}
window.__deskpet_metrics_emit = (event: string, payload: Record<string, unknown>) => {
  try {
    void fetch(`http://127.0.0.1:${BACKEND_PORT}/metrics/event`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event, detail: payload }),
      // fire-and-forget — 不阻 UI
      keepalive: true,
    }).catch(() => { /* silent — backend down or offline */ });
  } catch {
    // Safari old / restricted env — never break UI
  }
};

// v2 WI-T2-B 真 UI 验证 route — `#/slashtest` 独立渲染 InputBar 让真浏览器
// E2E 能真触发 / 命令 autocomplete 状态机（无需 Tauri invoke 创 session）.
const isSlashTest = window.location.hash.startsWith('#/slashtest');

// The Workbench is an opaque desktop window. Paint a deterministic background
// before React mounts so cold startup never flashes a transparent shell.
document.body.style.backgroundColor = '#0f1218';
document.documentElement.style.backgroundColor = '#0f1218';

// StrictMode intentionally stays disabled because duplicate effect mounts open
// duplicate WebSocket connections and repeat heavyweight media initialization.
const root = createRoot(document.getElementById('root')!);

if (isSlashTest) {
  // v2 真 UI 验证 — 独立渲染 InputBar 让浏览器 E2E 真触发 / 命令补全.
  document.body.style.backgroundColor = '#0f1218';
  import('./code-panel/SlashTestHarness')
    .then(({ SlashTestHarness }) => root.render(<SlashTestHarness />))
    .catch((e) => {
      console.error('[main] slashtest load failed:', e);
      root.render(
        <div style={{ padding: 20, color: '#f87171' }}>
          Failed to load slashtest: {String(e)}
        </div>,
      );
    });
} else {
  // Pet shell eagerly imports — needs to start fast.
  import('./App').then(({ default: App }) => root.render(<App />));
}
