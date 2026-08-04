// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Vitest configuration.
 *
 * jsdom 全局环境（Window / PointerEvent / localStorage）。
 *
 * T4 (workbench-ui)：pet-anim/pet-state 桌宠套件整删，coverage include
 * 改 scope 到工作台新增组件（WorkbenchShell/Sidebar/views）——原
 * pet-anim Sprint 的门槛值沿用（lines ≥ 80%, branches ≥ 70%）。
 * setupFiles 迁至 src/test/_setup.ts（内容不变：per-test localStorage
 * 清理 + Node 残缺 localStorage 替换）。
 *
 * Kept separate from vite.config.ts because vitest@2 bundles its own Vite
 * which conflicts with the project's vite@8 plugin types when imported
 * into a single file.
 */
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    setupFiles: ['./src/test/_setup.ts'],
    coverage: {
      provider: 'v8',
      include: [
        'src/components/WorkbenchShell.tsx',
        'src/components/Sidebar.tsx',
        'src/views/**',
      ],
      thresholds: {
        lines: 80,
        branches: 70,
        functions: 80,
        statements: 80,
      },
      reporter: ['text', 'json-summary'],
    },
  },
})
