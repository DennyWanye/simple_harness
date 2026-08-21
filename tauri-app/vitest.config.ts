// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * Vitest configuration.
 *
 * jsdom 全局环境（Window / PointerEvent / localStorage）。
 *
 * T4 (workbench-ui)：pet-anim/pet-state 桌宠套件整删，coverage include
 * 改 scope 到工作台新增组件（WorkbenchShell/Sidebar/views）。
 * 门槛值按新范围重校（G2 审计裁决，2026-08-05）：原 80/70 系 pet-anim 纯逻辑
 * 模块校准值；新范围含 ChatView 这类重集成组件（正确性由 6 条集成测试 +
 * phase-4 真机 18 用例矩阵承接，行覆盖不是其主要质量门）。取实测安全线
 * lines/statements 70、functions 55、branches 60，防回归滑坡；
 * ChatView 单元覆盖提升列入后续任务，阈值届时上调。
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
    // Keep the plan's deterministic `--maxWorkers=1` gate compatible with
    // Vitest 2/Tinypool on high-core hosts (whose computed minimum can exceed
    // the CLI maximum otherwise).
    poolOptions: { forks: { minForks: 1 } },
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
        lines: 70,
        branches: 60,
        functions: 55,
        statements: 70,
      },
      reporter: ['text', 'json-summary'],
    },
  },
})
