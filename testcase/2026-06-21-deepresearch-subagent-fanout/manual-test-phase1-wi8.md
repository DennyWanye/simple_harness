# Phase 1 WI-8 DeepResearch 落盘与索引手工测试文档（windows-mcp）

> **被测功能**：WI-8，所有 `deepresearch` 报告落安装目录 `DeepResearch/`，并维护 `DeepResearch/index.md` 总索引。  
> **范围**：Tauri dev 真机 UI E2E；dev 模式落点、index 首建/倒序/链接/列值、中文 UTF-8、特殊字符转义、artifact 可点、env 覆盖、非 AppData 落点。  
> **目的**：供真人或 windows-mcp 通过鼠标点击、键盘输入、剪贴板粘贴执行，不使用 WebSocket/import/脚本回放替代 UI 证据。  
> **用例数**：9 个正式 TC + 1 个 best-effort 边界(TC-WI8-10) + 1 个环境硬门禁(ENV-WI8-00)。  
> **是否需 windows-mcp**：需要。所有发起 deepresearch 的步骤必须模拟人工点击与输入。  
> **测试日期**：2026-06-21。  
> **证据目录建议**：`G:\projects\deskpet\plans\manual-results-2026-06-21-wi8-deepresearch\`。

---

## 0. 实现事实基线

执行者不需要读源码才能执行，但判定 PASS/FAIL 时以这些事实为准：

| 事实 | 判定点 |
|---|---|
| `paths.deepresearch_dir()` 解析顺序 | `DESKPET_DEEPRESEARCH_DIR` 覆盖 → frozen 安装根 `DeepResearch/` → dev repo 根 `G:\projects\deskpet\DeepResearch\` → `~/DeskPet/DeepResearch` 兜底；不会回落 `user_data_dir()`/AppData |
| `_save_report()` | 写入 `<DeepResearch>/<slug>-<ts>.md`，UTF-8，头部含“调研覆盖 **N 个来源** 来自 **M 个独立域名**” |
| `_INDEX_HEADER` | `# DeepResearch 报告索引` + 说明 + 表头 `| 日期 | 主题 | 文件 | 来源数 | 域名数 | 模式 | 子问题 |` + 分隔行 |
| `_update_deepresearch_index()` | `DeepResearch/index.md` 不存在时首建；新行插到表格分隔行后；文件列为 `[filename](filename)` 相对链接；同一文件名已存在则不重复插入；UTF-8 读写；主题 `|` 替换为 `/`，`\r`/`\n` 替换为空格 |
| `_handle_deepresearch()` | 保存成功后返回 `path`，并返回 `artifacts=[{kind:"file", path:"...", mime:"text/markdown", title:"..."}]`，聊天里应出现可点击 artifact 卡片 |
| 打包应用边界 | 本文在 dev 跑，能证明 dev 分支和 env 覆盖；frozen 安装根分支需打包安装场景验证，本文标为 env-limited，不把 dev 结果伪装成打包结论 |

---

## 1. 真机环境前置

### 1.1 清理旧进程

只清进程，不删除用户数据：

```powershell
taskkill /F /IM deskpet.exe 2>$null
taskkill /F /IM deskpet-backend.exe 2>$null
Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*deskpet*' } | Stop-Process -Force -ErrorAction SilentlyContinue
```

### 1.2 启动 Tauri dev（不要手动起 backend）

必须让 Tauri spawn 本树 backend。不要单独运行 `python main.py`，不要单独运行 `npm run dev:relay`。

```powershell
$ROOT = "G:\projects\deskpet"
$RESULT = "$ROOT\plans\manual-results-2026-06-21-wi8-deepresearch"
New-Item -ItemType Directory -Force "$RESULT\screenshots" | Out-Null

$env:DESKPET_BACKEND_DIR = "$ROOT\backend"
$env:DESKPET_PYTHON      = "$ROOT\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
Remove-Item Env:\DESKPET_DEEPRESEARCH_DIR -ErrorAction SilentlyContinue

cd "$ROOT\tauri-app"
npx tauri dev 2>&1 | Tee-Object -FilePath "$RESULT\tauri-dev.log"
```

dev 自动登录使用 relay/keychain。若弹出登录窗口，按项目本地凭据手动登录；截图前关闭或避开任何包含账号密码的窗口。

### 1.3 环境硬门禁 ENV-WI8-00

**case ID**：ENV-WI8-00  
**declare**：`坐标=(x_app,y_app)|动作=click|期望=DeskPet 主窗口已打开，聊天输入框可聚焦`

**操作步骤**

1. windows-mcp 截图，确认 DeskPet 主窗口可见。
2. 鼠标点击主窗口空白处或聊天输入框，确认窗口响应。
3. 在 PowerShell 检查 Tauri dev log：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-wi8-deepresearch\tauri-dev.log"
Select-String -Path $LOG -Pattern "backend_launch"
```

**期望**

- 必须看到 `[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=...\backend`。
- 不得看到 `[backend_launch] Bundled exe=...`。

**判定证据**

- 截图：`screenshots\ENV-WI8-00-main-window.png`
- 文本：`ENV-WI8-00-backend-launch.txt`，保存 grep 输出。

**PASS/FAIL**

- PASS：主窗口可交互，log 明确是 Dev python。
- FAIL：跑到 Bundled exe、无法登录、主窗口不可交互。失败后停止后续所有 TC。

---

## 2. 通用 UI 操作纪律

每个 TC 发起对话时都按以下方式执行：

1. 用截图定位聊天输入框坐标，记为 `(x_in,y_in)`；定位发送按钮坐标，记为 `(x_send,y_send)`；定位 artifact 卡片坐标，记为 `(x_art,y_art)`。
2. declare 行必须记录实际坐标。文档里的 `(x_in,y_in)` 是占位，执行时替换成实测物理像素。
3. 中文输入必须用剪贴板：`[Clipboard]::SetText("<prompt>")` 后 `Ctrl+V`，不要依赖 IME 逐字输入。
4. 发送用 Enter 或点击发送按钮，二选一；记录实际动作。
5. deepresearch 最长等待 300s。等待期间只观察 UI，不用 WebSocket/import/脚本触发工具。
6. shell 命令只用于启动、grep log、查看文件系统证据；不能替代 UI 触发。

---

## 3. 测试用例

### TC-WI8-01：happy path，dev 模式报告落 repo 根 DeepResearch

**case ID**：TC-WI8-01  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=发起 deepresearch，并在完成后出现可点击 artifact 卡片`

**前置**

- 已通过 ENV-WI8-00。
- 未设置 `DESKPET_DEEPRESEARCH_DIR`。

**操作步骤**

1. 截图定位输入框。
2. 点击输入框。
3. 剪贴板粘贴：

```text
帮我深度调研 2026 年 Windows 桌面 AI Agent 应用的本地文件落盘与索引设计，输出带引用的 Markdown 报告
```

4. 按 Enter 发送。
5. 等待完成，看到 deepresearch 正文或 artifact 卡片。
6. 点击 artifact 卡片一次，确认可以打开报告。
7. 在 PowerShell 采集文件证据：

```powershell
$ROOT = "G:\projects\deskpet"
$DR = "$ROOT\DeepResearch"
Get-ChildItem $DR -Filter *.md | Sort-Object LastWriteTime -Descending | Select-Object -First 5 FullName,LastWriteTime,Length
Get-Content (Get-ChildItem $DR -Filter *.md | Where-Object Name -ne "index.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName -Encoding UTF8 -TotalCount 12
Select-String -Path "$ROOT\plans\manual-results-2026-06-21-wi8-deepresearch\tauri-dev.log" -Pattern "DeepResearch|artifacts|deepresearch|research report"
```

**期望**

- 新报告路径为 `G:\projects\deskpet\DeepResearch\<slug>-<ts>.md`。
- 文件不是 `G:\projects\deskpet\backend\...` 下的临时文件。
- 文件不在 `<user_data>\OutPut\Research\`。
- 文件不在 `C:\Users\...\AppData\...`。
- 报告头部含“调研覆盖 **N 个来源** 来自 **M 个独立域名**”。
- UI 中 artifact 卡片可点击打开。

**判定证据**

- 截图：`screenshots\TC-WI8-01-artifact-card.png`
- 截图：`screenshots\TC-WI8-01-opened-report.png`
- 文本：`TC-WI8-01-file-list.txt`
- 文本：`TC-WI8-01-report-head.txt`
- 文本：`TC-WI8-01-log.txt`

**PASS/FAIL**

- PASS：UI 触发成功，artifact 可打开，最新 `.md` 明确落在 repo 根 `DeepResearch\`。
- FAIL：无 artifact、无落盘文件、落到 `OutPut\Research` 或 AppData、报告为空或不是 Markdown。

---

### TC-WI8-02：index.md 首次自动创建，表头完全正确

**case ID**：TC-WI8-02  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=DeepResearch/index.md 不存在时自动创建并含标准表头`

**前置**

- 为保证“首次创建”可重复验证，执行前只重命名旧索引，不删除报告：

```powershell
$DR = "G:\projects\deskpet\DeepResearch"
New-Item -ItemType Directory -Force $DR | Out-Null
if (Test-Path "$DR\index.md") {
  Rename-Item "$DR\index.md" ("index.pre-wi8-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".md.bak")
}
```

**操作步骤**

1. 截图定位输入框。
2. 点击输入框，粘贴：

```text
帮我深度调研 DeepResearch 索引文件首次创建时应该包含哪些字段，要求输出带引用报告
```

3. 发送并等待完成。
4. 采集 index 内容：

```powershell
$IDX = "G:\projects\deskpet\DeepResearch\index.md"
Get-Content $IDX -Encoding UTF8 -TotalCount 12
Select-String -Path "G:\projects\deskpet\plans\manual-results-2026-06-21-wi8-deepresearch\tauri-dev.log" -Pattern "DeepResearch|index|deepresearch"
```

**期望**

`index.md` 自动创建，开头必须包含：

```markdown
# DeepResearch 报告索引

运行时自动生成的 DeepResearch 报告总索引，新报告按倒序插入。

| 日期 | 主题 | 文件 | 来源数 | 域名数 | 模式 | 子问题 |
|---|---|---|---|---|---|---|
```

表格分隔行下一行是本次新报告记录。

**判定证据**

- 截图：`screenshots\TC-WI8-02-artifact-card.png`
- 文本：`TC-WI8-02-index-head.txt`
- 文本：`TC-WI8-02-log.txt`

**PASS/FAIL**

- PASS：`index.md` 首次生成，标题、说明、表头、分隔行完全匹配，且已有一条报告记录。
- FAIL：未生成 index、表头缺列、乱码、分隔行缺失、新报告不在表格内。

---

### TC-WI8-03：第二份报告倒序插入，文件列是可点击相对链接，列值正确

**case ID**：TC-WI8-03  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=第二份报告行插入表头后，旧报告下移`

**前置**

- TC-WI8-02 已产生一条 index 记录。

**操作步骤**

1. 记录当前 `index.md` 表格前 5 行。
2. 点击输入框，粘贴：

```text
帮我深度调研 Windows 应用运行时生成 Markdown 报告时如何设计可点击相对链接索引，输出带引用报告
```

3. 发送并等待完成。
4. 采集 index：

```powershell
$IDX = "G:\projects\deskpet\DeepResearch\index.md"
Get-Content $IDX -Encoding UTF8 -TotalCount 16
```

5. 在文件资源管理器或 Markdown 预览器中打开 `G:\projects\deskpet\DeepResearch\index.md`，点击最新行的文件链接。

**期望**

- 最新报告行位于 `|---|---|...|` 分隔行正下方。
- TC-WI8-02 的旧报告行下移一行。
- 文件列形如 `[xxx-179xxxxxxx.md](xxx-179xxxxxxx.md)`，链接不含绝对路径。
- 链接可从 `index.md` 打开同目录报告文件。
- 来源数、域名数、子问题数为数字。
- 扁平 deepresearch 的模式列**必须为 `flat`**（实现已用 `subagent_fanout` 块是否存在判定，不会把档位 `standard`/`deep`/`light` 泄漏到模式列——此 bug 已在手测设计阶段修复并加单测 `test_index_mode_column_is_flat_not_depth`）。若模式列出现 `standard`/`deep`/`light` → FAIL（回归）。

**判定证据**

- 截图：`screenshots\TC-WI8-03-index-opened.png`
- 截图：`screenshots\TC-WI8-03-relative-link-opened.png`
- 文本：`TC-WI8-03-index-top.txt`

**PASS/FAIL**

- PASS：倒序正确、链接相对且可点、列值正确、mode 为 `flat`。
- FAIL：新行追加到底部、链接为绝对路径、链接不可点、列错位、mode 非 `flat`。

---

### TC-WI8-04：幂等，同一报告文件不重复出现在 index

**case ID**：TC-WI8-04  
**declare**：`坐标=(x_art,y_art)|动作=click 多次打开同一 artifact|期望=index 中同一文件名只出现 1 次`

**边界说明**

纯 UI 不应通过 import/WebSocket 强行调用 `_update_deepresearch_index(report_path, ...)`。本手测只验证用户可触达路径：同一 artifact 多次打开、等待、切换窗口，不应导致同一文件名重复出现在 index。严格 helper 幂等由 TG-6 单元测试覆盖，不能拿脚本触发当 UI 证据。

**操作步骤**

1. 使用 TC-WI8-03 最新生成的 artifact。
2. 点击同一 artifact 卡片 3 次；每次打开后回到 DeskPet。
3. 等待 10 秒，不发起新对话。
4. 采集最新报告文件名和 index 中出现次数：

```powershell
$DR = "G:\projects\deskpet\DeepResearch"
$latest = Get-ChildItem $DR -Filter *.md | Where-Object Name -ne "index.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$latest.Name
(Select-String -Path "$DR\index.md" -Pattern ([regex]::Escape($latest.Name))).Count
Get-Content "$DR\index.md" -Encoding UTF8 -TotalCount 20
```

**期望**

- 同一文件名在 `index.md` 中出现次数为 1。
- 多次打开 artifact 不产生新行。
- 没有 `.md.tmp` 残留。

**判定证据**

- 截图：`screenshots\TC-WI8-04-same-artifact-clicked.png`
- 文本：`TC-WI8-04-index-count.txt`

**PASS/FAIL**

- PASS：最新文件名出现次数为 1，且无临时文件残留。
- FAIL：同一文件名出现 2 次或更多，或 index 表格被重复插入。

---

### TC-WI8-05：中文主题 slug 与 index 行 UTF-8 不乱码

**case ID**：TC-WI8-05  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=中文主题生成中文 slug，index UTF-8 可读`

**操作步骤**

1. 点击输入框，粘贴：

```text
帮我深度调研 中国桌面端 AI 助手在隐私、本地模型、知识库索引方面的产品设计趋势，输出带引用报告
```

2. 发送并等待完成。
3. 点击 artifact 打开报告。
4. 采集文件名和 index：

```powershell
$DR = "G:\projects\deskpet\DeepResearch"
Get-ChildItem $DR -Filter *.md | Where-Object Name -ne "index.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 3 Name,FullName
Get-Content "$DR\index.md" -Encoding UTF8 -TotalCount 20
```

**期望**

- 最新报告文件名 slug 可读，不出现 `????`、`���`、`%E4%` 大段 URL 编码或 mojibake。
- `index.md` 中主题列中文可读。
- 文件内容 UTF-8 可读，报告正文中文不乱码。

**判定证据**

- 截图：`screenshots\TC-WI8-05-opened-chinese-report.png`
- 文本：`TC-WI8-05-file-and-index.txt`

**PASS/FAIL**

- PASS：中文文件名、index 主题、报告正文均可读。
- FAIL：任一处出现明显乱码、问号替代、表格因编码损坏。

---

### TC-WI8-06：主题中的 `|` 与换行被转义，不破坏 Markdown 表格

**case ID**：TC-WI8-06  
**declare**：`坐标=(x_in,y_in)|动作=click + multiline clipboard-paste + Enter|期望=index 主题列转义特殊字符且表格仍为 7 列`

**操作步骤**

1. 点击输入框。
2. 剪贴板粘贴以下多行文本，保留换行：

```text
请把 deepresearch 的 topic 尽量原样设为：RAG | Agent
安全治理，然后深度调研这个主题，输出带引用报告
```

3. 发送并等待完成。
4. 采集 index 最新行并检查列数：

```powershell
$IDX = "G:\projects\deskpet\DeepResearch\index.md"
$lines = Get-Content $IDX -Encoding UTF8
$topData = $lines | Where-Object { $_ -match '^\| ' -and $_ -notmatch '^\| 日期 ' -and $_ -notmatch '^\|---' } | Select-Object -First 1
$topData
($topData.ToCharArray() | Where-Object { $_ -eq '|' }).Count
Get-Content $IDX -Encoding UTF8 -TotalCount 12
```

**期望**

- 最新主题列里的原始 `|` 被 `/` 替代，或至少不会新增表格列。
- 原始换行被替换为空格，不把一条记录拆成两行。
- 最新数据行仍是 7 列 Markdown 表格：行首和行尾分隔符计入时，`|` 字符数应为 8。
- 表头后的下一行仍只有一条最新记录，不出现断裂半行。

**判定证据**

- 截图：`screenshots\TC-WI8-06-index-special-topic.png`
- 文本：`TC-WI8-06-index-row.txt`

**PASS/FAIL**

- PASS：表格未破坏，主题转义可读，`|` 字符数符合 7 列结构。
- FAIL：主题里的 `|` 让表格多列、换行拆行、最新行错位或链接列错位。

---

### TC-WI8-07：artifact 卡片 path 指向 DeepResearch 的 .md，且可点开

**case ID**：TC-WI8-07  
**declare**：`坐标=(x_art,y_art)|动作=click artifact 卡片|期望=打开 DeepResearch 下的 Markdown 报告`

**操作步骤**

1. 使用任一已完成 deepresearch 对话的 artifact 卡片。
2. 鼠标移动到卡片上，截图记录卡片标题。
3. 点击卡片。
4. 确认打开的是 `.md` 文件，路径包含 `G:\projects\deskpet\DeepResearch\`。
5. 采集 log：

```powershell
Select-String -Path "G:\projects\deskpet\plans\manual-results-2026-06-21-wi8-deepresearch\tauri-dev.log" -Pattern "artifacts|mime|text/markdown|DeepResearch"
```

**期望**

- artifact 标题是报告文件名。
- 打开目标是 `DeepResearch\*.md`。
- 文件不是 `index.md`，而是本次报告正文。
- log 或 UI 详情能证明 artifact path 指向 `DeepResearch`。

**判定证据**

- 截图：`screenshots\TC-WI8-07-artifact-before-click.png`
- 截图：`screenshots\TC-WI8-07-artifact-opened-file.png`
- 文本：`TC-WI8-07-log-artifact.txt`

**PASS/FAIL**

- PASS：卡片可点开，目标为 `DeepResearch\*.md`。
- FAIL：卡片不可点、打开失败、打开旧 `OutPut\Research` 文件、path 不是 Markdown。

---

### TC-WI8-08：负向落点，报告不进入 C 盘 AppData 或旧 OutPut/Research

**case ID**：TC-WI8-08  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=新报告只出现在 repo 根 DeepResearch`

**操作步骤**

1. 执行前记录旧路径当前最新时间：

```powershell
$OLD1 = "$env:APPDATA\deskpet\OutPut\Research"
$OLD2 = "G:\projects\deskpet\backend\OutPut\Research"
Get-ChildItem $OLD1 -Filter *.md -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3 FullName,LastWriteTime
Get-ChildItem $OLD2 -Filter *.md -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3 FullName,LastWriteTime
```

2. 点击输入框，粘贴：

```text
帮我深度调研 Windows 应用为什么不应该把用户可见报告藏在 AppData，输出带引用报告
```

3. 发送并等待完成。
4. 采集新旧路径：

```powershell
$DR = "G:\projects\deskpet\DeepResearch"
Get-ChildItem $DR -Filter *.md | Sort-Object LastWriteTime -Descending | Select-Object -First 5 FullName,LastWriteTime
Get-ChildItem "$env:APPDATA\deskpet\OutPut\Research" -Filter *.md -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 5 FullName,LastWriteTime
Get-ChildItem "G:\projects\deskpet\backend\OutPut\Research" -Filter *.md -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 5 FullName,LastWriteTime
```

**期望**

- 本次时间窗口内的新 `.md` 只出现在 `G:\projects\deskpet\DeepResearch\`。
- `C:\Users\<user>\AppData\Roaming\deskpet\OutPut\Research` 没有本次新文件。
- `backend\OutPut\Research` 没有本次新文件。

**判定证据**

- 截图：`screenshots\TC-WI8-08-artifact-card.png`
- 文本：`TC-WI8-08-path-negative.txt`

**PASS/FAIL**

- PASS：新文件只在 repo 根 `DeepResearch`，旧路径无本次新增。
- FAIL：任何本次报告落入 AppData 或旧 `OutPut\Research`。

---

### TC-WI8-09：`DESKPET_DEEPRESEARCH_DIR` env 覆盖生效

**case ID**：TC-WI8-09  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=报告与 index 落到 env 指定目录`

**前置**

此 TC 需要重启 Tauri dev。关闭当前 Tauri dev 后，用 env 覆盖启动：

```powershell
taskkill /F /IM deskpet.exe 2>$null
taskkill /F /IM deskpet-backend.exe 2>$null

$ROOT = "G:\projects\deskpet"
$RESULT = "$ROOT\plans\manual-results-2026-06-21-wi8-deepresearch"
$OVERRIDE = "$ROOT\tmp\wi8-deepresearch-env-override"
New-Item -ItemType Directory -Force $OVERRIDE | Out-Null

$env:DESKPET_BACKEND_DIR = "$ROOT\backend"
$env:DESKPET_PYTHON      = "$ROOT\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
$env:DESKPET_DEEPRESEARCH_DIR = $OVERRIDE

cd "$ROOT\tauri-app"
npx tauri dev 2>&1 | Tee-Object -FilePath "$RESULT\tauri-dev-env-override.log"
```

**操作步骤**

1. 重复 ENV-WI8-00 的 Dev python 门禁，但 log 文件换成 `tauri-dev-env-override.log`。
2. 点击输入框，粘贴：

```text
帮我深度调研 环境变量覆盖运行时报告目录的设计风险，输出带引用报告
```

3. 发送并等待完成。
4. 采集覆盖目录：

```powershell
$OVERRIDE = "G:\projects\deskpet\tmp\wi8-deepresearch-env-override"
Get-ChildItem $OVERRIDE -Force | Sort-Object LastWriteTime -Descending | Select-Object Name,FullName,LastWriteTime,Length
Get-Content "$OVERRIDE\index.md" -Encoding UTF8 -TotalCount 12
Select-String -Path "G:\projects\deskpet\plans\manual-results-2026-06-21-wi8-deepresearch\tauri-dev-env-override.log" -Pattern "backend_launch|DeepResearch|deepresearch"
```

**期望**

- 新报告 `.md` 与 `index.md` 均落在 `G:\projects\deskpet\tmp\wi8-deepresearch-env-override\`。
- 本次报告不落默认 `G:\projects\deskpet\DeepResearch\`。
- artifact 打开的是 env 覆盖目录下的 `.md`。

**判定证据**

- 截图：`screenshots\TC-WI8-09-artifact-env-override.png`
- 截图：`screenshots\TC-WI8-09-opened-env-file.png`
- 文本：`TC-WI8-09-override-list-and-index.txt`
- 文本：`TC-WI8-09-log.txt`

**PASS/FAIL**

- PASS：报告、index、artifact path 全部指向 env 覆盖目录。
- FAIL：env 设置后仍落默认目录，或 index 未随覆盖目录生成。

**收尾**

本 TC 后如继续跑默认目录测试，关闭 Tauri dev 并执行：

```powershell
Remove-Item Env:\DESKPET_DEEPRESEARCH_DIR -ErrorAction SilentlyContinue
```

再按 1.2 默认方式重启。

---

## 4. 打包路径诚实边界

本手测在 Tauri dev 下执行，只能证明：

- dev 分支落 `G:\projects\deskpet\DeepResearch\`。
- env 覆盖分支落 `DESKPET_DEEPRESEARCH_DIR`。
- UI artifact 与 index 行指向真实落盘文件。
- 不回落旧 `OutPut\Research` 或 C 盘 AppData。

不能仅凭 dev 证明 frozen 打包应用一定落“安装目录/DeepResearch”。frozen 分支需要额外安装包验证：

- per-user 非 C 盘可写安装：期望 `<install_root>\DeepResearch\`。
- Program Files 不可写安装：期望 fallback 到 `~\DeskPet\DeepResearch` 并有 warning；仍不得落 `%AppData%\deskpet\OutPut\Research`。

若本轮只执行 dev，打包路径结论必须登记为 `ENV-LIMITED`，不能写成 PASS。

---

## 4.1 边界（best-effort，UI 难稳定触发）

- **TC-WI8-10（no-citations 不落盘/不进 index）**：`_handle_deepresearch` 仅在 `report.report_md and report.citations` 都非空时才 `_save_report` + 更新 index。若某次调研全失败（0 源，返回 no_results 模板、citations 为空），**不应**在 `DeepResearch/` 新增 .md，也**不应**给 index 加行。
  - 触发难点：需真实"全失败"（断网/被封），UI 不易稳定复现 → 标 **ENV-LIMITED/best-effort**；该保护由单测覆盖更可靠（`test_save_report_*` + handler 保存条件）。
  - 若恰好遇到一次"未找到来源"的真机回复：核对该次**没有**新 .md、index **无**新增行 → 记 PASS；否则 FAIL（脏数据/空报告入索引）。

---

## 5. 证据归档格式

每个 TC 在 `RESULTS.md` 追加以下格式：

```text
case: TC-WI8-xx
坐标: (x_in,y_in), (x_send,y_send), (x_art,y_art)
动作: click 输入框 -> Clipboard 粘贴 -> Ctrl+V -> Enter/点击发送 -> 等待 -> 点击 artifact
截图: screenshots\TC-WI8-xx-xxx.png
文件证据: <最新报告完整路径>；index.md 前 N 行
log证据: <grep 关键行，含 backend_launch / DeepResearch / artifacts>
判定: PASS / FAIL / ENV-LIMITED
备注: 如失败，写清是否重试、是否网络/LLM 环境限制
```

失败重试纪律：同一 TC 最多用两种人工 workaround 重试，例如重新聚焦输入框、改用发送按钮、重启 Tauri dev。不能改代码、不能用 import/WebSocket 直接调用工具来让 UI 用例“通过”。

---

## 6. 用例索引

| ID | 覆盖点 | 一票判定 |
|---|---|---|
| ENV-WI8-00 | Tauri dev 跑本树 backend | log 有 Dev python，无 Bundled exe |
| TC-WI8-01 | happy path、dev repo 根落盘、artifact 可点 | 新 `.md` 在 `G:\projects\deskpet\DeepResearch\` |
| TC-WI8-02 | `index.md` 首次自动创建 | 标题、说明、表头、分隔行完全正确 |
| TC-WI8-03 | 第二份倒序、相对链接、列值 | 最新行在表头后，链接可点，mode 为 `flat` |
| TC-WI8-04 | 幂等症状 | 同一文件名在 index 只出现 1 次 |
| TC-WI8-05 | 中文 slug 与 UTF-8 | 文件名、index、正文均不乱码 |
| TC-WI8-06 | `|`/换行转义 | 表格仍为 7 列，主题不破坏行 |
| TC-WI8-07 | artifact path | 卡片打开 `DeepResearch\*.md` |
| TC-WI8-08 | 非 C 盘 AppData/旧路径 | 本次报告不进 AppData 和 `OutPut\Research` |
| TC-WI8-09 | env 覆盖 | 报告和 index 落 env 指定目录 |
| TC-WI8-10 | no-citations 不落盘/不进 index（best-effort/ENV-LIMITED） | 全失败回复不新增 .md、index 无新行 |

