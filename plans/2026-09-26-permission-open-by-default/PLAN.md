# 权限改造：默认放行 + 核心文件会话内申请 + 永不卡住

日期：2026-09-26　版本：第 1 版（草案，待子代理挑战）

## 1. 用户要求（2026-09-26 原话要点）

- 主对话和编排：除少数核心文件外，其他都可以用。
- 核心文件：在会话窗口里向用户申请，同意后就可以用。
- **重点：不能因为权限卡住**，让用户拿不到想要的结果。

## 2. 现状（2026-09-26 真机日志 + 代码调研）

权限模式默认 `auto`，按 2026-09-07 的决定已经不弹框。真正卡住主对话的是另外几层"范围限制"：

| 位置 | 现在的规则 | 真机表现 |
|---|---|---|
| `deskpet/sdk_adapters/read_gate.py` `WorkspaceReadGate.verify` | 读类工具（`PROJECT_READ_TOOL_NAMES`，如 list_directory、file_read、glob、grep）必须先有已绑定的任务工作区，路径必须在绑定根或配置的工作区里 | `workspace_read_denied reason=path_outside_workspace_root`（2026-09-26 01:29:39） |
| `deskpet/tools/file_tools.py` `_resolve_within_workspace` | 拒绝绝对盘符路径、UNC、`..`、工作区外的路径 | 工作区外的文件读写一律失败 |
| `agent/write_scope.py` `write_scope_check` | 写入必须在 scope_root 内（write_file / edit_file / run_shell 的写命令） | 同上 |
| `deskpet/tools/office_paths.py` 输出路径解析 | 只允许临时目录、应用输出目录，或用户用选择框授权过的目录 | `excel_create ... output directory is not authorized — ask the user to pick a destination folder`（01:34:33） |
| `deskpet/sdk_adapters/effect_gate.py` | PROJECT_EFFECT 工具必须带当前任务路由凭证，绑定版本要一致 | 日志里 `context_route ... task_scope_source_stale` 两次 |
| 编排（SDK `tool_gateway.py`） | 工作者只能读写本次尝试的工作区 | 不会卡住，只是拒绝 |

另外，manual 模式下需要确认时，弹框只在面板可见时显示，而且没有主动过期，所以存在"一直等"的风险（`PrimaryRunPanel.tsx:61`、SDK 被动过期）。

**没有任何统一的"核心文件"清单。**

## 3. 核心设计

### 决定一：一个判定函数，两种结果

新文件 `backend/deskpet/permissions/protected_paths.py`：

```python
def classify(path: str | Path, op: Literal["read", "write"]) -> Literal["allow", "ask"]
```

- 路径先 `expanduser()` + `resolve()`（跟随符号链接），再和清单比较。
- 清单（默认值，可在 `config.toml [permissions] protected_extra = [...]` 追加）：

| 类别 | 路径 | 读 | 写/删 |
|---|---|---|---|
| 凭证与密钥 | `~/.ssh`、`~/.aws`、`~/.gnupg`、`~/.kube`、`~/.docker/config.json`、`~/.netrc`、`~/.grok`、`~/Library/Keychains`、浏览器 Cookie 库、任何名为 `.env` / `.env.*` 的文件、本应用的 `llm_runtime*.json` | 申请 | 申请 |
| 本应用自身 | 应用安装包（`*.app` 为 SimpleHarness 的）、Host 源码根（`backend/` 所在仓库）、应用数据目录下的 `config.toml` 与 `data/`（各 SQLite 库） | 放行 | 申请 |
| 系统目录 | macOS `/System`、`/usr`（不含 `/usr/local`）、`/bin`、`/sbin`、`/etc`、`/private/etc`、`/Library`；Windows 沿用 `office_paths._system_roots()` | 放行 | 申请 |

- 其余路径一律 `allow`。

### 决定二：申请不阻塞（"先拒绝 + 会话里点允许 + 重试"）

对 `ask` 的调用：
1. 工具**立即**返回拒绝，模型收到明确说明：「`<路径>` 是受保护文件，已在会话里向用户申请；用户同意后再调用一次，或先做其他不涉及它的事」。运行不会进入等待。
2. 同时向会话推送一张卡片（新消息 `protected_path_request`：`{request_id, session_id, path, op, tool}`），卡片按钮：「允许这一次」「本会话都允许」「拒绝」。
3. 用户点允许 → 记一条授权（`session_id + 规范路径 + op`；"这一次"只放行下一次匹配调用）。模型重试时放行。
4. 用户点允许后，如果模型已经说完话结束了本轮，卡片上提示「已允许，请让它继续」。不自动续跑，避免意外副作用。

这和现有 F-Z1b 的"不在同一次调用里放行，重试再过"一致，任何情况下都不会一直等。

### 决定三：放宽的位置（主对话）

| 位置 | 改法 |
|---|---|
| `file_tools._resolve_within_workspace` | 允许绝对路径和 `~`，相对路径仍按工作区解析；`..` 解析后按真实路径判断，不再一律拒绝。解析后调 `classify` |
| `write_scope.write_scope_check` | 不再以 scope_root 为边界，改为 `classify(path, "write")` |
| `office_paths` 输出路径 | 去掉"必须在已授权目录"的要求，保留 `classify(target, "write")` |
| `read_gate.WorkspaceReadGate.verify` | 不再要求先绑定任务工作区；路径不在任何绑定根里时直接按 `classify(path, "read")` 放行或申请，不再走绑定提议 |
| `effect_gate` | **第一轮不改**。先确认真机上写类工具被它挡住的具体情况（第 0 步），再决定 |

`run_shell` 的写路径抽取已有，结果交给 `classify`。

### 决定四：编排

- 编排工作者的**写入**保留在本次尝试的工作区里：结果要经过验证后才正式交付，交付物由界面"查看产物"读取或另存。这不会让用户拿不到结果。
- 工作者的**读取**放开：`workspace_read_file` / `workspace_list` 允许读工作区外的路径，按 `classify(path, "read")` 判断。`ask` 时直接拒绝，模型收到说明，并在任务详情里显示一条提示。编排是后台自动运行，不能停下来等人。
- 这部分改在 SDK 的 `tool_gateway`，作为第二批，不和主对话一起上线。

## 4. 分步

0. **测现状（半小时）**：在真机数据目录上跑三句话："列出 ~/Desktop 的文件"、"读 ~/Documents 下的某个 md"、"在桌面生成一个 Excel"，记录每一步被谁挡住（日志关键词：`workspace_read_denied`、`tool_failed`、`effect_gate_`）。
1. `protected_paths.py` + 表格测试（清单每类至少一条、符号链接指向受保护路径、`..` 绕行、大小写）。
2. 授权存储与会话卡片：后端 `protected_path_request` 推送 + `protected_path_decision` 处理，前端卡片组件挂在会话窗口里（不依赖面板是否可见）。
3. 四处放宽（决定三）逐个改，每处配测试：工作区外路径放行、受保护路径得到拒绝加卡片、授权后重试放行。
4. 真机：重复第 0 步的三句话，全部一次拿到结果；再试一次读 `~/.ssh/config`，看到卡片，点允许后重试成功，点拒绝后模型继续回答。
5. 第二批：编排读取放开（SDK）。

## 5. 不做 / 风险

- 不改 auto/manual 两种模式本身；manual 模式的逐次确认照旧。
- 风险：放开后模型能改用户的任何非核心文件。这是用户明确要求的；审计照旧记录每次调用。
- 风险：`effect_gate` 与任务路由体系较重，第一轮不动，只看第 0 步结果决定。

## 6. 第 2 版：按挑战意见修订后的实际做法（2026-09-26 已实现）

- **判定**：`backend/deskpet/permissions/protected_paths.py`。
  - 顺序：**凭证类**（`~/.ssh`、`~/.aws`、`~/.gnupg`、`~/.kube`、`~/.grok`、`~/.config/gcloud`、钥匙串、`~/.docker/config.json`、`~/.netrc`、名为 `.env*` / `llm_runtime*.json` / `id_rsa*` / Cookie 库的文件）无论在哪都要申请（读写都要）。
  - 其次，工作区、应用输出目录、临时目录、任务已绑定的根一律放行（开发模式下工作区在源码目录里，必须先放行）。
  - 最后，"本应用自身"（源码仓库、安装包、`config.toml`、`data/`）和系统目录（macOS 的 `/System` `/usr`（除 `/usr/local`）`/bin` `/sbin` `/etc` `/Library`，外加 Windows 系统目录）写入要申请，读取放行。
  - 相对路径永远按工作区解析，**不按后端进程目录**（否则会被当成"本应用自身"）。
- **中央检查点**：`deskpet/sdk_adapters/tools.py` `ProductEffectExecutor.execute`。每次工具调用首次执行前，从参数里取出路径（`path` / `destination` / `output_path` / `cwd` 等，以及 shell 命令里的写入目标）逐个判定：
  - 没有授权：立即返回 `protected_path_requires_user`，同时推送卡片。
  - 有授权：本次调用把这个路径登记为已放行，各工具自己的检查也认这个放行。
- **各工具的边界全部换成同一个判定**：
  - `agent/write_scope.write_scope_check` 增加 `op` 参数；
  - os_tools 的 `read_file` / `list_directory` / `run_shell`（cwd 和写入目标）/ `move_file` / `download_file`；
  - `file_tools` 的 file_read / file_write / glob / grep；
  - `office_paths` 的 `resolve_for_read` / `resolve_for_write`（相对输出路径按工作区解析）；
  - `read_gate`：不再要求先路由或绑定，路径在绑定根之外也放行，只把绑定根或工作区作为相对路径的基准。
- **卡片**：
  - 后端 `main.py` 启动时设置 `set_notifier(_broadcast_control)`，处理 `protected_path_decision`。
  - 前端 `hooks/useProtectedPathRequests.ts` + `components/ProtectedPathCard.tsx` 挂在 `App.tsx`，与"外部操作"对话框同一层，所以不依赖面板是否可见。卡片右下角浮动，不遮挡界面。
- **测试**：
  - 新增 `tests/permissions/test_protected_paths.py`（23 个）和 `test_open_paths_tools.py`（7 个），前端 `ProtectedPathCard.test.tsx`。
  - 删除 43 个断言旧"工作区边界"的测试，文件末尾留有说明。
  - 两个 Windows 系统目录测试改为只在 Windows 上运行。
  - `test_tool_catalog` 的写入围栏改用受保护路径。
- **本轮未做**：
  - 编排读取放开（决定四，需要改 SDK 并重新钉版本）；
  - `effect_gate` 在未路由时的提示文案；
  - 配置文件追加清单；
  - `read_gate` 里已不再调用的绑定提议代码的清理。
