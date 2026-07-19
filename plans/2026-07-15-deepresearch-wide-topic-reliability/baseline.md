# 绿色基线（2026-07-15）

- anchor：`0117ad764f593d018392332541d475a1d52a07ac`
- Python：`3.11.9`（`backend/.venv`）
- Node：`24.14.0`（Codex bundled runtime）
- 工作树：大范围 dirty；其中 Search Gateway、DeepResearch v3、progress UI 与 ARCHITECTURE 已有上一轮未提交实现。执行本计划时只编辑 acceptance/plan 明确列出的文件，并在每个 gate 前复核 diff，不能覆盖无关用户改动。

## 后端聚焦基线

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_search_gateway_provider_statuses.py backend/tests/test_search_gateway_routing.py backend/tests/test_workflow_deep_research_v3.py backend/tests/test_workflow_recovery.py -q
```

结果：`35 passed in 5.18s`。

## 前端聚焦基线

当前 PowerShell 没有 ambient `npm`；`pnpm.cmd` wrapper 会因已忽略的 `esbuild@0.21.5` build script 在依赖状态检查阶段退出。未修改依赖或批准 build script，直接使用已安装项目依赖与 Codex bundled Node 执行 Vitest：

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' `
  'node_modules/vitest/vitest.mjs' run `
  src/stores/sessionsStore.test.ts `
  src/components/workflow/WorkflowProgressGroup.test.tsx `
  src/components/MessageStreamPanel.workflow.test.tsx
```

结果：`3 files / 47 tests passed`。

## 固定失败事实

- Session：`16bbb4ce-c282-4b25-b630-7400b6be25c1`
- run：`ed254c0673d04771bbb2901fed462741`
- 5 个子问题、13 条 query；25 次真实上游调用、76 个 routing decision；0 candidates/passages/claims/citations。
- 失败时仍投递 ArtifactCard 与完整 no-results Markdown，是本轮 negative delivery 基线。
- 相同运行配置的新 Gateway 进程重放单个子问题可由 DuckDuckGo 获得 10 条结果，证明题目并非天然不可搜索。

原始 topic/query/URL/body 不复制进自动化 fixture、progress、benchmark 或提交证据；fixture 只保留结构化 provider 行为与计数。
