# Search Gateway + DeepResearch 执行前基线

- 日期：2026-07-14（Asia/Shanghai）
- Git HEAD（首次运行时）：`e4a3bdc7`；中断续跑时 master 已前进到 `5014d253 feat(agent): ship durable workflows and context OS core`。
- 分支：`master`
- 工作区：执行前已有 `391` 个 tracked/untracked 变更条目；这些内容视为用户基线，不 reset/stash，不纳入本功能完成声明。
- 计划状态：`finalized`；acceptance 共 31 条 AC，31/31 可追溯到 WI。

## 1. 后端全量 pytest

命令（项目根）：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
```

结果：**既有红基线**，`4345 passed, 21 failed, 14 skipped, 10 deselected`，耗时 `723.44s`。

既有失败：

- `test_hybrid_router.py::test_router_with_real_providers_routes_to_local_when_healthy`
- `test_image_config_section.py::test_llm_base_url_fallback_is_read`
- `test_model_catalog.py` 2 项 context-window 断言
- `test_openai_compatible.py::test_integration_ollama_v1_roundtrip`
- `test_outcome_verifier.py::test_t10_4_git_diff_skipped_when_not_git_repo`
- `test_p4s21_context_bundle_history.py` 3 项
- `test_p4s22_web_search.py` 2 项（本计划必须红→绿）
- `test_v3_remaining_wi.py` 2 项 cache invalidation
- `test_workflow_eval_cli.py` 8 项；其中当前 `_NodeObserver.node_finished()` 不接受 `attributes` 是一条已有失败链

首次从 `backend/` 子目录运行时，`test_context_os_payload.py` 因根目录 `scripts.e2e` 不在 import path 而 collection error；该次无效调用未计入基线，已按仓库约定从项目根重跑完整套件。

## 2. 本计划聚焦后端套件

命令：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_search_provider.py backend\tests\test_p4s22_web_search.py backend\tests\test_workflow_native_engine.py backend\tests\test_workflow_deep_research_graph.py backend\tests\test_workflow_progress.py -q
```

结果：**预期红基线**，`57 passed, 2 failed`，耗时 `15.34s`。

红证据：

- `test_web_search_parses_results`：期望 3 条，实际 0 条。
- `test_web_search_caps_at_max_results`：期望 1 条，实际 0 条。

这两项对应现有同步 `web_search → search_provider.search()` 跳过默认 async/CDP provider 的问题；WI-4 必须让同一测试转绿。

## 3. 前端

### TypeScript + Vite build

```powershell
node.exe node_modules\typescript\bin\tsc -b
node.exe node_modules\vite\bin\vite.js build
```

结果：**PASS**。Vite `1393 modules transformed`；只有既有 dynamic-import/chunk-size warning。

### Vitest 全量

```powershell
node.exe node_modules\vitest\vitest.mjs run
```

结果：**PASS**，`75 test files, 782 tests passed`，耗时约 `22.73s`。

### ESLint

```powershell
node.exe node_modules\eslint\bin\eslint.js .
```

结果：**既有红基线**，`1920 problems (1903 errors, 17 warnings)`；错误横跨大量既有文件。实施要求：本计划新增/修改文件不增加 lint 错误，最终全局结果不得劣于该数值。

## 4. Rust/Tauri

```powershell
cargo test
```

结果：**PASS**，`71 passed, 0 failed`；只有既有 dead-code/linker warnings。

`cargo fmt -- --check` 无法运行：当前 stable toolchain 未安装 `rustfmt` component。该项记录为环境不可用，不算代码失败；本计划不修改 Rust 文件。

## 5. 基线判定

- 可进入实施，但必须显式区分既有红项与本计划回归。
- 频繁门禁：本计划搜索/workflow 聚焦 pytest + 前端聚焦 Vitest/tsc。
- 阶段末门禁：后端全量 pytest、前端全量 Vitest/build、Rust test；全局 ESLint 不得劣于 `1903 errors / 17 warnings`，且本计划触及文件必须无新增 lint。
- 本计划完成条件包含：两项 `test_p4s22_web_search.py` 从红转绿；所有新 Search Gateway、FetchExtract、DeepResearch v2、outbox/recovery、UI 分组测试全绿。

## 6. 中断续跑校准

- 续跑时上述大批 workflow/context 工作区内容已由外部流程提交为 `5014d253`；本计划目标源码在该 HEAD 上为 clean，剩余 tracked dirty 仅 7 个无关文件。
- 在 `5014d253` 上重跑 §2 完全复现 `57 passed, 2 failed`，耗时 `15.35s`；两项失败与首次基线相同。
- 因目标源码内容与已测试工作区一致，首次全量/前端/Rust结果仍作为基线；阶段门结束时会在最终 HEAD 重新跑全量套件。
