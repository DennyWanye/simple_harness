// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // WI-R1 (beta-100 付费版) — auth edition resolution.
  void mode
  const envEdition = process.env.VITE_AUTH_EDITION
  const define: Record<string, string> = {}
  if (envEdition) {
    define['import.meta.env.VITE_AUTH_EDITION'] = JSON.stringify(envEdition)
  }

  // Parallel-dev port isolation (git worktree support).
  const vitePort = Number(process.env.DESKPET_VITE_PORT) || 5173
  const backendPort = Number(process.env.DESKPET_BACKEND_PORT) || 8100
  define['import.meta.env.VITE_BACKEND_PORT'] = JSON.stringify(
    String(backendPort),
  )

  return {
    plugins: [react()],
    define,

    // Prevent vite from obscuring rust errors
    clearScreen: false,
    server: {
      port: vitePort,
      strictPort: true,
      watch: {
        // Tell vite to ignore watching `src-tauri`
        ignored: ['**/src-tauri/**'],
      },
    },
  }
})
