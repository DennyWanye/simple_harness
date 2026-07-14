# 验收标准：file_glob 扫描降噪与加速

## 范围
- 包含：后端内置 `file_glob` 工具在递归匹配时默认跳过常见重型/生成目录，减少扫描耗时和结果噪声；补充单元测试、测试用例与状态文档。
- 明确不包含：改造 `file_read` / `file_write` / `file_grep` 行为；接入完整 `.gitignore` 解析器；改变 workspace 沙箱边界；改动前端 UI。

## 功能验收条款
| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| AC-1 | 默认跳过重型生成目录 | 当 workspace 下存在 `node_modules`、`__pycache__`、`.uv-cache`、`backend/assets` 等目录且执行 `file_glob` 的递归 pattern 时，返回结果不包含这些目录内文件。 | 必须 |
| AC-2 | 保留源码匹配能力 | 当匹配普通源码/文档文件时，`file_glob` 仍返回 workspace-relative、正斜杠路径，排序稳定，既有 `**/*.md` 用例不回归。 | 必须 |
| AC-3 | 显式 root 仍受沙箱保护 | 当 `root` 指向不存在目录时返回空；当 `root` 试图逃逸 workspace 时仍返回 `path outside workspace`，不得为了优化削弱安全边界。 | 必须 |
| AC-4 | 可观测的跳过信息 | 当有目录被跳过时，`file_glob` JSON 返回中包含可选的 `skipped_dirs` 与 `skipped_count`，用于诊断扫描结果为何少于原始文件数。 | 必须 |
| AC-5 | 无 UI 回归风险 | 本次无 UI 改动；测试策略走自动化 pytest，不要求 windows-mcp。 | 必须 |

## 非功能 / 边界
- 错误态：非法 pattern 或底层 I/O 错误仍返回原有 JSON error 形态。
- 幂等：重复执行 `file_glob` 不产生文件写入、状态变更或累计副作用。
- 性能：递归扫描应避免进入默认忽略目录；测试用例用“忽略目录中存在匹配文件但结果排除”作为可重复断言。
- 兼容：保留 `pattern` 与 `root` 参数；不引入新必填参数；非递归/普通匹配行为保持兼容。

## 完成的定义（DoD 摘要）
- 全部“必须”条款通过自动化测试。
- `backend/tests/test_deskpet_tools_file.py` 覆盖忽略目录、保留普通匹配、沙箱保护和跳过元数据。
- `plans/`、`testcase/`、`STATUS/status.md` 已同步。
