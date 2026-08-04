# Context OS V1 执行前绿色基线

> 锁定时间：2026-07-13（Asia/Shanghai）  
> HEAD：`e4a3bdc7528066e7d6a290004eeeb2eee5e5edf6`  
> 工作树：执行前已有 288 个 tracked/untracked 状态项；这些均视为用户既有改动，不纳入本功能回退或清理。

## Backend focused baseline

```powershell
backend\.venv\Scripts\python.exe -m pytest `
  backend/tests/test_deskpet_context_assembler.py `
  backend/tests/test_context_manager_for_session.py `
  backend/tests/test_deskpet_tools_registry.py `
  backend/tests/test_deskpet_agent_loop.py -q
```

结果：`89 passed in 1.29s`。

## Frontend type baseline

仓库 `package.json` 没有 `typecheck` script，且 ambient `npm` 不在 PATH；实际权威命令为：

```powershell
cd tauri-app
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' `
  'node_modules\typescript\bin\tsc' -b
```

结果：exit code `0`。

## 回归判定

- 上述 89 个 backend focused tests 不得新增失败。
- 前端 `tsc -b` 不得新增错误。
- 更宽测试中的执行前既有红线必须单独记录；不得把既有 288 项工作树状态误归因或擅自清理。
