# Plan：file_glob 扫描降噪与加速

## 关联验收标准
- 覆盖 AC-1, AC-2, AC-3, AC-4, AC-5。

## 背景与实现细节调研

当前 `backend/deskpet/tools/file_tools.py::_handle_file_glob` 直接调用 `root.glob(pattern)` 并收集所有命中文件。该实现保留了 pathlib glob 语义，但在递归 pattern 进入 workspace 时会遍历 `node_modules`、`__pycache__`、`.uv-cache`、`backend/assets` 等已在 `.gitignore` 标记的重型/生成目录。对于 DeskPet 这种会把模型、构建产物、缓存和手测产物放在开发树附近的项目，这会放大 agent 工具调用耗时和结果噪声。

本次不接入完整 `.gitignore` 解析器，原因是 `file_glob` 运行在 DeskPet runtime workspace 中，不一定是 Git checkout；完整解析会引入更多路径语义与依赖成本。采用保守的内置目录名/相对路径跳过表，覆盖常见大目录，同时保留所有普通文件匹配能力。

## 文件影响清单
| 文件 | 职责 | 本次改动 |
|------|------|----------|
| `backend/deskpet/tools/file_tools.py` | workspace 文件工具实现 | 为 `file_glob` 增加递归遍历 helper，默认剪枝生成/缓存/模型目录，并返回跳过元数据。 |
| `backend/tests/test_deskpet_tools_file.py` | file 工具单元测试 | 增加跳过目录、跳过元数据、既有 glob 兼容和沙箱保护回归测试。 |
| `testcase/2026-07-09-file-tool-scan-optimization/manual-test.md` | 长期 testcase | 记录本次自动化测试用例与预期结果。 |
| `testcase/index.md` | testcase 索引 | 登记本次 testcase。 |
| `STATUS/status.md` | 全局状态 | 完成后追加里程碑并同步模块状态。 |

## 任务清单（按依赖排序）

### Task 1 — 增加忽略规则与递归 glob helper [覆盖 AC-1, AC-2, AC-4]
- 改动文件：`backend/deskpet/tools/file_tools.py`
- 现状：`_handle_file_glob` 直接 `for p in root.glob(pattern)`，不区分目录类型，返回 `matches/count`。
- 修改方式：
  - 新增 `_DEFAULT_GLOB_SKIP_DIR_NAMES`，包含 `.git`, `.hg`, `.svn`, `node_modules`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.uv-cache`, `.venv`, `venv`, `target`, `dist`, `build`, `coverage`。
  - 新增 `_DEFAULT_GLOB_SKIP_REL_PARTS`，覆盖 `backend/assets`, `backend/models`, `backend/dist-msi`, `backend/dist-portable`, `tauri-app/node_modules`, `tauri-app/coverage`。
  - 新增 `_should_skip_glob_dir(path, workspace)` 返回规范化 relative path 是否应剪枝。
  - 新增 `_iter_glob_matches(root, pattern, workspace)`，对于包含 `**` 的递归 pattern 使用 `os.walk` 剪枝目录，再用 `Path.match` 判断文件/目录是否命中；非递归 pattern 保留 `Path.glob` 兼容语义，并过滤已跳过目录下的命中。
  - 显式 `root` 指向默认跳过目录时不剪枝该 root 本身，保持“用户指定的沙箱内 root 仍可访问”；只在从祖先目录递归扫入重型目录时跳过。
  - `_handle_file_glob` 返回 `matches/count/skipped_dirs/skipped_count`；`skipped_dirs` 为去重、排序、workspace-relative、正斜杠目录路径；`skipped_count` 为唯一跳过目录数量；没有跳过目录时 `skipped_dirs=[]`、`skipped_count=0`。
- 验证：新增单测创建被忽略目录下的 `.md` 文件和普通 `.md` 文件，断言只返回普通文件并暴露 skipped metadata。
- 依赖：无。

### Task 2 — 补自动化测试 [覆盖 AC-1, AC-2, AC-3, AC-4, AC-5]
- 改动文件：`backend/tests/test_deskpet_tools_file.py`
- 现状：已有 glob happy path、missing root、escaping root 测试。
- 修改方式：
  - 扩展 `test_glob_finds_files` 兼容新增 metadata。
  - 新增 `test_glob_skips_generated_and_heavy_dirs_by_default`。
  - 新增 `test_glob_skips_nested_backend_assets_relative_path`。
  - 新增 `test_glob_allows_explicit_root_inside_default_skipped_dir`，锁定显式 root 兼容边界。
  - 新增 `test_glob_root_parameter_preserves_recursive_pathlib_semantics` 与 `test_glob_non_recursive_pattern_stays_non_recursive`，覆盖 `root` 参数和非递归语义。
  - 保留 `test_glob_missing_root_returns_empty` 与 `test_glob_rejects_escaping_root`，必要时适配新增 metadata 不影响 error/empty 形态。
- 验证：`& 'F:\projects\deskpet\backend\.venv\Scripts\python.exe' -m pytest tests/test_deskpet_tools_file.py -q`。
- 依赖：Task 1。

### Task 3 — 文档与状态回写 [覆盖 AC-5]
- 改动文件：`testcase/2026-07-09-file-tool-scan-optimization/manual-test.md`, `testcase/index.md`, `STATUS/status.md`。
- 现状：没有本优化的 testcase 与状态记录。
- 修改方式：
  - testcase 写明自动化路由、步骤、命令、预期。
  - testcase index 增加一行本测试范围。
  - `STATUS/status.md` 在工具层模块说明或最近里程碑中追加本次 file_glob 优化，顶部日期保持 2026-07-09。
- 验证：文档路径存在，且 testcase 覆盖全部 AC。
- 依赖：Task 1、Task 2。

## 测试策略
- 被测对象为后端库函数/工具 handler，无 UI 改动。
- 路由：自动化 pytest。
- 命令：`cd backend; python -m pytest tests/test_deskpet_tools_file.py -q`。
