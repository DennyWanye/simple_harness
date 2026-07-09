# Testcase：file_glob 扫描降噪与加速

## 测试策略

- 被测对象：后端 `file_glob` 工具 handler。
- 路由：自动化 pytest。无 UI 改动，不需要 windows-mcp。
- 回归命令：

```powershell
cd F:\projects\deskpet\backend
python -m pytest tests/test_deskpet_tools_file.py -q
```

## 用例

| ID | 覆盖 AC | 步骤 | 预期结果 |
|----|---------|------|----------|
| TC-1 | AC-1, AC-4 | 在临时 workspace 创建普通 `keep/a.md`，以及 `node_modules/pkg/hidden.md`、`__pycache__/hidden.md`、`.uv-cache/hidden.md` 后执行 `file_glob {"pattern":"**/*.md"}`。 | 结果只包含 `keep/a.md`；`skipped_count > 0`；`skipped_dirs` 包含被跳过目录。 |
| TC-2 | AC-1, AC-4 | 在临时 workspace 创建 `backend/assets/model/hidden.md` 和 `backend/src/visible.md` 后执行 `file_glob {"pattern":"**/*.md"}`。 | 结果包含 `backend/src/visible.md`，不包含 `backend/assets/model/hidden.md`；跳过元数据包含 `backend/assets`。 |
| TC-3 | AC-2 | 创建 `a.md`、`dir/b.md`、`c.txt` 后执行既有 `file_glob {"pattern":"**/*.md"}`。 | 返回 `["a.md", "dir/b.md"]`，路径为正斜杠且排序稳定。 |
| TC-4 | AC-3 | 对不存在 root 执行 `file_glob {"pattern":"*", "root":"no-such-dir"}`。 | 返回 `{"matches":[],"count":0}`。 |
| TC-5 | AC-3 | 对逃逸 root 执行 `file_glob {"pattern":"*", "root":"../.."}`。 | 返回 `{"error":"path outside workspace","retriable":false}`。 |
| TC-6 | AC-2, AC-3 | 对显式 root `node_modules` 执行 `file_glob {"pattern":"**/*.md","root":"node_modules"}`。 | 用户明确指定的沙箱内 root 仍可访问，返回其中 `.md` 文件，且 `skipped_count == 0`。 |
| TC-7 | AC-2 | 执行 `file_glob {"pattern":"*.md"}`，同时根目录和子目录都存在 `.md` 文件。 | 只返回根目录 `.md`，非递归 pattern 不递归。 |
| TC-8 | AC-5 | 检查本次 diff 不包含前端 UI 文件。 | 测试策略保持 pytest，无需 windows-mcp。 |

## 幂等性审查

- 位置：`backend/deskpet/tools/file_tools.py::_handle_file_glob`
- 重复触发副作用：无。只读目录遍历，不写文件、不写数据库、不改全局状态。
- 已处理标记：不适用。
- 持久化：不适用。
- 失败重试：I/O 异常仍返回 retriable error，不会记录“已处理”状态。
- 幂等测试：TC-1/TC-3 可重复执行，预期结果不变。
