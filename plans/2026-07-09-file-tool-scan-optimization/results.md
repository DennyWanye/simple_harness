# Results：file_glob 扫描降噪与加速

## 验证命令

```powershell
cd F:\projects\deskpet\backend
& 'F:\projects\deskpet\backend\.venv\Scripts\python.exe' -m py_compile deskpet\tools\file_tools.py
& 'F:\projects\deskpet\backend\.venv\Scripts\python.exe' -m pytest tests/test_deskpet_tools_file.py -q
& 'F:\projects\deskpet\backend\.venv\Scripts\python.exe' -m pytest tests/test_deskpet_tools_registry.py tests/test_deskpet_tools_search.py -q
```

## 结果

```text
py_compile passed
33 passed in 0.87s
31 passed in 0.80s
```

`git` is not available in the current PATH (`where.exe git` reports no match), so UI non-regression was checked by source directory timestamps: latest `tauri-app/src` and `tauri-app/src-tauri/src` source edits are from 2026-07-08, while this slice's touched source file is `backend/deskpet/tools/file_tools.py` on 2026-07-09.

## 可追溯矩阵

| AC | 代码/文档证据 | 测试证据 | 状态 |
|----|---------------|----------|------|
| AC-1 默认跳过重型生成目录 | `backend/deskpet/tools/file_tools.py` `_DEFAULT_GLOB_SKIP_DIR_NAMES`, `_DEFAULT_GLOB_SKIP_REL_PARTS`, `_iter_glob_matches()` | `test_glob_skips_generated_and_heavy_dirs_by_default`, `test_glob_skips_nested_backend_assets_relative_path` | ✅ |
| AC-2 保留源码匹配能力 | `_iter_glob_matches()` 非递归保留 `Path.glob`；递归保留 workspace-relative 输出排序 | `test_glob_finds_files`, `test_glob_root_parameter_preserves_recursive_pathlib_semantics`, `test_glob_directory_prefixed_recursive_pattern_matches_from_workspace_root`, `test_glob_non_recursive_pattern_stays_non_recursive` | ✅ |
| AC-3 显式 root 仍受沙箱保护 | `_resolve_within_workspace()` 未改；显式 skipped root 不剪枝 | `test_glob_rejects_escaping_root`, `test_glob_missing_root_returns_empty`, `test_glob_allows_explicit_root_inside_default_skipped_dir` | ✅ |
| AC-4 可观测的跳过信息 | `_handle_file_glob()` 返回 `skipped_dirs` / `skipped_count` | 上述 glob skip 测试断言 metadata | ✅ |
| AC-5 无 UI 回归风险 | 仅改后端工具、测试和文档；testcase 标注 pytest 路由 | py_compile + pytest；无前端文件改动 | ✅ |

## 幂等性审查

`file_glob` 只读目录并返回 JSON，不写文件、不写数据库、不修改全局状态。重复执行同一 workspace 与 pattern 的结果稳定；无“遍历 + 写副作用”风险。
