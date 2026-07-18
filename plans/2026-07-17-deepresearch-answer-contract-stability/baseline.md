# DeepResearch v6 执行前基线

> 锁定时间：2026-07-18（Asia/Shanghai）  
> Git HEAD：`0117ad764f593d018392332541d475a1d52a07ac`  
> 工作树：高度 dirty；所有结果均基于用户当前工作树。未 reset/clean/checkout，未安装或批准新依赖。

## 1. 有效基线命令

| 范围 | 命令 | 结果 | 耗时 |
|---|---|---|---:|
| Backend full | `backend/.venv/Scripts/python.exe -m pytest -q --disable-warnings --maxfail=20 backend/tests`（仓库根目录） | **RED**：4995 passed、13 failed、14 skipped、9 deselected | 917.16s |
| Frontend tests | bundled Node 执行 `node_modules/vitest/vitest.mjs run` | **GREEN**：77 files、817 tests passed | 15.05s（tool wall 16.2s） |
| Frontend type/build | bundled Node 执行 `typescript/bin/tsc -b` 后 `vite/bin/vite.js build` | **GREEN**；仅 chunk/dynamic-import warnings | 5.5s |
| Frontend lint（项目脚本等价范围） | bundled Node 执行 `eslint .` | **RED**：扫描 `src-tauri/target/debug/backend/_internal` 生成/打包文件，出现大量第三方 lint 错误 | 55.5s |
| Frontend lint（仅源码补充） | bundled Node 执行 `eslint src` | **RED**：264 problems（254 errors、10 warnings） | 5.8s |
| Rust | `cargo check` in `tauri-app/src-tauri` | **GREEN**；1 个既有 dead-code warning (`paths.rs::resolve_with`) | 1.0s |

Node 使用：`C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`。直接调用仓库已有 tool entrypoints，避免 pnpm 自动 install/approve-builds 改变依赖状态。

## 2. Backend 既有红项（实现前）

1. `test_agent_parallel.py::test_two_subagents_run_concurrently`：计时阈值 0.18s，实际 0.275s。
2. `test_hybrid_router.py::test_router_with_real_providers_routes_to_local_when_healthy`：实际路由 cloud，预期 local。
3. `test_image_config_section.py::test_llm_base_url_fallback_is_read`：读到 `https://chinzy.com/v1`，未采用 fixture config。
4. `test_model_catalog.py::test_model_context_window_is_per_model_not_uniform`：gpt-5.5 当前 1,000,000，断言 400,000。
5. `test_model_catalog.py::test_build_catalog_carries_context_window`：同上。
6. `test_openai_compatible.py::test_integration_ollama_v1_roundtrip`：本地 Ollama models 健康但 chat endpoint 404。
7. `test_p4s21_context_bundle_history.py::test_memory_component_promotes_l2_to_meta_l2_history`：旧 stub 缺 `time_remaining_ms()`。
8. `test_p4s21_context_bundle_history.py::test_memory_component_skips_empty_or_unknown_role_rows`：同上。
9. `test_p4s21_context_bundle_history.py::test_memory_component_text_block_no_longer_includes_l2`：同上。
10. `test_playwright_renderer_pool.py::test_real_task0_browser_renders_dynamic_page_with_isolated_contexts`：建立 context 前 deadline timeout。
11. `test_playwright_renderer_pool.py::test_real_browser_crash_restarts_once_and_preserves_cleanup`：发现 2 个 owned browser roots，预期 1。
12. `test_playwright_renderer_pool.py::test_idle_shutdown_reaps_browser_and_driver_children`：idle 后 `_playwright` 未清空。
13. `test_relay_llm_bridge.py::test_t3_3_persist_key_true_writes_key_regression`：runtime 未持久化测试 API key。

这些失败发生在 v6 实现前。Phase 3/4 不能把它们冒充本任务回归；但本任务触及 context、Playwright/fetch、delivery 周边，必须对相应失败做“同测试重跑 + scoped 新测试”比对，若错误形态/数量扩大则视为回归。

## 3. Frontend lint 既有红项

- `eslint .` 的主要噪声来自 `src-tauri/target/debug/backend/_internal` 被纳入扫描；它不是本任务可修改的第三方/构建输出。
- `eslint src` 仍有 254 个既有 error，集中于 React 19 hooks refs/set-state-in-effect、`no-explicit-any`、unused vars、prefer-const 等。
- Phase 3 的增量门禁：本任务修改文件必须单文件 lint 无新增错误；全量源码 lint 与本基线比较不得增加 error/warning 数。全项目 lint 清债不扩入本 DeepResearch 任务。

## 4. 无效尝试（不计产品红项）

- 从 `backend/` 目录运行 pytest，4 个 `scripts.*` import 在 collection 失败；改为仓库根目录后全量套件有效执行。
- 直接 `pnpm test/build/lint` 触发 bundled pnpm 自动 install，并因 `ERR_PNPM_IGNORED_BUILDS esbuild@0.21.5` 中止；没有进入测试。未运行 `pnpm approve-builds`，随后改用 bundled Node 直接执行现有工具。
- 直接执行 `.cmd` tool entrypoints 时 shell PATH 无 `node`；改为 bundled Node 后有效执行。

## 5. 执行后比较规则

- Backend：目标相关 scoped tests 全绿；full suite 的既有 13 项不得增加或改变为本任务导致的新失败。
- Frontend：vitest 817 基线 tests 与新增 tests 全绿；`tsc -b + vite build` 继续绿；修改文件 lint 无新增问题，全源码总数不高于 254/10。
- Rust：`cargo check` 继续绿，不新增 warning。
- 只有上述自动化与 T13 真实 UI evidence 同时满足，才允许把 v6 默认 ON 并回写 `ARCHITECTURE/` 完成状态。
