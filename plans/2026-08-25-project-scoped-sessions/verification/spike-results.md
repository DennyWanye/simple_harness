# Plan spike 结果（2026-08-25）

这些是 `plan-bs` 阶段的可丢弃技术验证，不是业务实现或验收证据。

## H-1：filesystem identity

- 方法：在 macOS 临时目录创建真实目录与 symlink，以
  `sha256(platform + st_dev + st_ino)` 计算 identity，再执行同卷 rename。
- 结果：真实路径与 symlink identity 相同；rename 前后 identity 相同。
- 结论：该 identity 足以支持本计划的 symlink 去重和同卷 relocation；跨卷/网络盘仍保持
  exploratory，不自动认领。

## H-2：500 Projects × 200 Sessions 读模型

- 方法：临时 SQLite 建立 `projects/sessions/session_project_bindings` 与计划中的核心索引，插入
  500 个 Project、100,000 个 Session；执行完整 join、排序并在 Python 中组装 Project→Sessions，
  预热后采样 25 次。
- 结果：median 71.50 ms，p95 72.85 ms，max 73.51 ms。
- 结论：本机后端查询和映射满足 p95 ≤ 200 ms 的量级假设。UI 不应一次渲染 100,000 个 DOM 节点；
  实现任务仍须加入折叠分组/虚拟化或分页，并在真实协议层复测。

## H-3：projectless fail-closed 切换点

- 静态证据：`backend/deskpet/tool_catalog/providers.py::_execution_context()` 在 metadata 中同时缺少
  `workspace` 与 `write_scope_root` 时，会调用 `resolve_workspace_root()` 注入全局 workspace；
  `backend/main.py` fresh Run 还会读取 latest-session project context。
- 结论：只新增 binding 表不足以满足 AC-4。计划必须同时切断 latest-Run 和 global fallback，并以
  projectless tool admission + 最终执行上下文无根拒绝形成两层 fail-closed。
- wiring spike：执行
  `backend/.venv/bin/python -m pytest -q backend/tests/sdk_adapters/test_product_host_ports.py::test_tool_adapter_contextvar_is_concurrent_and_fail_closed backend/tests/sdk_adapters/test_dynamic_mcp_projection.py`，
  结果 `3 passed in 0.35s`。它证明 `ProductToolsAdapter` 能并发隔离当前 `ToolContext`，动态 MCP wrapper 能
  使用 injected `execution_context_getter`；因此新实现可在 app-private seam 通过 `run_id` 解析 durable Run
  authority，而无需修改公开 Harness SDK。该证据只证明 seam 可复用，不证明尚未实现的 projectless catalog
  过滤已通过验收。

## H-4：原生目录选择器复用

- 静态证据：`tauri-app/src-tauri/src/commands.rs::open_directory_dialog()` 已实现带 main window parent 的
  native folder picker；`tauri-app/src/components/MessageStreamPanel.tsx::ProjectDirectoryCard` 已通过
  `invoke("open_directory_dialog")` 使用该命令。
- 结论：Project 注册 UI 可复用现有 command；无需新增 Rust picker 或依赖，只需新增注册预览/确认协议和前端入口。
