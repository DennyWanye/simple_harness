# 执行前基线

> 日期：2026-07-15
> HEAD：`0117ad764f593d018392332541d475a1d52a07ac`
> 工作树：存在大量用户既有改动；本轮只触碰计划列出的 Search/DeepResearch/进度 UI、测试和 ARCHITECTURE 文档。

## 聚焦后端

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_search_gateway_routing.py backend/tests/test_search_gateway_production_wiring.py backend/tests/test_deepresearch_claim_support.py backend/tests/test_workflow_deep_research_v2.py backend/tests/test_workflow_progress.py -q
```

结果：`47 passed in 5.94s`。

## 聚焦前端

当前 PowerShell 没有 ambient `npm`，bundled `pnpm.cmd` 又因 `ERR_PNPM_IGNORED_BUILDS esbuild@0.21.5` 在依赖状态检查阶段退出；这不是项目测试红项。使用工作区 bundled Node 直接运行已安装的 Vitest：

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' .\node_modules\vitest\vitest.mjs run src/components/workflow/WorkflowProgressGroup.test.tsx src/components/MessageStreamPanel.workflow.test.tsx
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' .\node_modules\vitest\vitest.mjs run src/stores/sessionsStore.test.ts
```

结果：UI `2 files / 14 passed`；store `1 file / 27 passed`。

## TypeScript / lint

Bundled Node 运行 `node_modules/typescript/bin/tsc -b --pretty false`：PASS（0 errors）。

全目录 ESLint 会扫描 `src-tauri/target/debug/backend/_internal` 冻结构建产物以及既有 pet animation 代码，当前基线为 `1936 problems (1919 errors, 17 warnings)`；这属于既有 lint 范围污染，不作为本轮新增代码的放行标准。本轮必须对实际修改的 TS/TSX 文件运行 scoped ESLint 并保持 0 error，同时保证全目录数量不增加。

## 已知全量基线

可重复命令：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q --junitxml=plans/2026-07-15-search-quality-cooldown-support/backend-full-junit.xml
Set-Location tauri-app
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' .\node_modules\vitest\vitest.mjs run
```

上一轮同一 HEAD 祖先上的最终全量证据为：前端 `77 files / 800 tests`；后端 `4440 passed / 10 existing failures / 14 skipped / 9 deselected`。10 个既有失败 node id 为：

1. `tests.test_agent_parallel::test_two_subagents_run_concurrently`
2. `tests.test_hybrid_router::test_router_with_real_providers_routes_to_local_when_healthy`
3. `tests.test_image_config_section::test_llm_base_url_fallback_is_read`
4. `tests.test_model_catalog::test_model_context_window_is_per_model_not_uniform`
5. `tests.test_model_catalog::test_build_catalog_carries_context_window`
6. `tests.test_openai_compatible::test_integration_ollama_v1_roundtrip`
7. `tests.test_outcome_verifier::test_t10_4_git_diff_skipped_when_not_git_repo`
8. `tests.test_p4s21_context_bundle_history::test_memory_component_promotes_l2_to_meta_l2_history`
9. `tests.test_p4s21_context_bundle_history::test_memory_component_skips_empty_or_unknown_role_rows`
10. `tests.test_p4s21_context_bundle_history::test_memory_component_text_block_no_longer_includes_l2`

本轮收尾必须重新跑当前全量；上述失败不得扩张，新增范围内失败必须为 0。

## Immutable DeepResearch v2 manifest 基线

v3 实施前固定以下当前值，回归必须逐字段相等：

- `definition_hash=c0fb5261da2cf31dabd2131b44b963947444d2dcbd03a4c5bbc0a63f286dfa80`
- `state_hash=f3ce983c7a7245fed2e17084c71f94cb01223235486ac459c382c47bc9cf4352`
- `prompt_hash=a75393ad1c463a64c237c5d1579c2d91163ab9e0c34c842150206a5d26f7db7f`
- `policy_hash=dea7495161b674d0bd8c913361856c644e5758c4f03f58e58c9ca9db5b1b4a4a`
- `callable_source_hash=ef57fee8617ded6550a1cc47c65b22cdb8adc8aeaf4c7c564f6af5fc9f6b7031`
- `dependency_lock_hash=1013a7b449853880a44dd4219d1fe8090dff38055fd4a42d6902887693e4c8ef`
- `implementation_bundle_hash=a5c1626cccf41351bdc9531f735f42f53278eb426c5462f1217c3692a022b9e2`
