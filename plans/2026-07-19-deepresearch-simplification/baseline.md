# 2026-07-20 增量实施绿色基线

## 后端 focused baseline

```powershell
cd F:\projects\deskpet\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_workflow_progress.py tests/test_workflow_product_delivery.py tests/test_deepresearch_simplified_v7.py tests/test_deepresearch_subagent_fanout.py
```

结果：`61 passed in 11.69s`。

## 前端 focused baseline

```powershell
cd F:\projects\deskpet\tauri-app
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' .\node_modules\vitest\vitest.mjs run src/components/workflow/WorkflowProgressGroup.test.tsx src/code-panel/ws.chat.test.ts src/code-panel/ArtifactCard.test.ts
```

结果：`3 files passed, 34 tests passed`。

说明：系统 PATH 中没有 `npm`；bundled pnpm 首次运行因供应链策略阻止 `esbuild@0.21.5` build script，未将其计为产品基线失败。直接使用仓库现有 `node_modules` 与 bundled Node 运行 Vitest 后全绿。
