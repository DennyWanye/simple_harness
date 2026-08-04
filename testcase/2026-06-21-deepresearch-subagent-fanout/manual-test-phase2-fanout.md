# Phase 2 DeepResearch 子代理 fan-out 手工测试文档（windows-mcp）

> **被测功能**：Phase 2 fan-out（plan WI-1~6 / §5），`deepresearch` 在外层 plan 拆出多个子问题后，为每个子问题调度一个 research 子代理，最后由主线程统一综合成一份报告。  
> **范围**：Tauri dev 真机 UI E2E；flag ON/OFF 两态、fan-out 触发、前端子代理进度、统一报告、背压、递归守门、300s 预算、DeepResearch/index 模式列。  
> **目的**：供真人或 windows-mcp 通过真实鼠标点击、键盘输入、剪贴板粘贴执行；禁止用 WebSocket/import/脚本回放替代 UI 触发或 UI 证据。  
> **用例数**：1 个环境硬门禁 + 7 个正式 TC（TC-F1~TC-F7，其中 TC-F5 含两个 flag-off 子场景）。  
> **是否需 windows-mcp**：需要。所有发起 `deepresearch` 的步骤必须模拟人工点击与输入。  
> **测试日期**：2026-06-21。  
> **证据目录建议**：`G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\`。

---

## 0. 实现事实基线

执行者不需要读源码才能操作，但 PASS/FAIL 判定以这些实现事实为准：

| 事实 | 判定点 |
|---|---|
| fan-out 入口 | `deepresearch()` 在 plan 后判断：`scheduler is not None`、`_depth == 0`、子问题数 `>= fanout_min_subquestions`、`[research].subagent_fanout=true` 才进入 `_run_subagent_fanout()` |
| scheduler 构造 | 只有 `[features].subagent_driver=true` 时 backend 启动才构造 `SubagentScheduler`；否则 `scheduler=None`，即使 `[research].subagent_fanout=true` 也只能走 flat |
| 子代理 run_id | 每个子问题通过 `scheduler.run(kind="research", run_id="<sid>.dr-<i>", task_id="dr-<i>")` 调度 |
| backend 日志锚点 | 子代理真正进入 running 时写 `subagent_scheduled kind=research run_id=<sid>.dr-<i> task_id=dr-<i>` |
| 前端进度 | scheduler 发送 `subagent_progress`，前端复用 `SubagentProgressPanel`，标题显示 `🤖 子代理并发`，行内显示 `research`、`dr-i`、`排队中/运行中/全部完成` |
| 递归守门 | fan-out 内层子调查调用 `deepresearch(..., scheduler=None, _depth=1, skip_plan=True)`，不应再触发二次 fan-out |
| coverage | fan-out 报告 coverage 含 `mode: "fanout"` 与 `subagent_fanout.enabled/n_subagents/n_completed/n_failed/waves/per_subrun_timeout_s/per_subquestion` |
| index 模式列 | `DeepResearch/index.md` 的模式列按 `coverage.subagent_fanout` 是否存在写 `fanout`；无该块写 `flat` |
| 预算 | `_DEEPRESEARCH_TOOL_TIMEOUT=300s`，fan-out 根据并发与 waves 给子调查设置 per-subrun timeout，目标是最坏 wall-clock 不破 300s |

---

## 1. 环境前置

### 1.1 只清旧进程，不删用户数据

```powershell
taskkill /F /IM deskpet.exe 2>$null
taskkill /F /IM deskpet-backend.exe 2>$null
Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*deskpet*' } | Stop-Process -Force -ErrorAction SilentlyContinue
```

### 1.2 找到真实 config.toml

backend 通过 `resolve_config_path()` 读取配置，优先级是：

1. `DESKPET_CONFIG` 环境变量指向的文件。
2. user-data 下的 `config.toml`，dev 下通常是 `%APPDATA%\deskpet\config.toml`。
3. bundle/dev 默认配置。

用 backend 解释器探测真实路径：

```powershell
$ROOT = "G:\projects\deskpet"
$PY = "$ROOT\backend\.venv\Scripts\python.exe"
& $PY -c "import sys; sys.path.insert(0, r'G:\projects\deskpet\backend'); import config; print(config.resolve_config_path())"
```

记录输出为 `$CFG`。若输出路径不存在，不要静默创建到别处；先确认是否设置了 `DESKPET_CONFIG`，再按项目约定让 backend seed user config 或人工确认路径。

### 1.3 配置 flag ON

编辑 `$CFG`，确保至少包含：

```toml
[features]
subagent_driver = true

[research]
subagent_fanout = true
fanout_min_subquestions = 2
fanout_max_subquestions = 6
```

背压用例 TC-F4 需要临时确认并发 cap，可先保持默认：

```toml
[agent.concurrency]
global_concurrency = 4

[agent.concurrency.lane_caps]
research = 2
```

注意：`features.subagent_driver=true` 和 `[research].subagent_fanout=true` 两者缺一不可。每次改 `$CFG` 后必须重启 Tauri dev，因为 backend 进程内有配置缓存。

### 1.4 启动 Tauri dev

必须让 Tauri spawn 本树 backend。不要单独运行 `python main.py`，不要单独运行 `npm run dev:relay`。

```powershell
$ROOT = "G:\projects\deskpet"
$RESULT = "$ROOT\plans\manual-results-2026-06-21-fanout-phase2"
New-Item -ItemType Directory -Force "$RESULT\screenshots" | Out-Null

$env:DESKPET_BACKEND_DIR = "$ROOT\backend"
$env:DESKPET_PYTHON      = "$ROOT\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
Remove-Item Env:\DESKPET_DEEPRESEARCH_DIR -ErrorAction SilentlyContinue

cd "$ROOT\tauri-app"
npx tauri dev 2>&1 | Tee-Object -FilePath "$RESULT\tauri-dev-fanout-on.log"
```

dev relay 应自动登录。若出现登录窗口，按本地 dev 凭据手动登录；截图前避开账号密码。

### 1.5 环境硬门禁 ENV-FANOUT-00

**case ID**：ENV-FANOUT-00  
**declare**：`坐标=(x_app,y_app)|动作=click|期望=DeskPet 主窗口已打开，聊天输入框可聚焦`

**操作步骤**

1. windows-mcp 截图，确认 DeskPet 主窗口可见。
2. 鼠标点击聊天输入框或主窗口空白处，确认窗口响应。
3. 检查启动日志：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\tauri-dev-fanout-on.log"
Select-String -Path $LOG -Pattern "backend_launch|subagent_driver_ready"
```

**期望**

- 必须看到 `[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=...\backend`。
- 不得看到 `[backend_launch] Bundled exe=...`。
- 必须看到 `subagent_driver_ready global=... lanes=...`，且 lanes 里 research cap 可识别（默认通常为 `research: 2`）。

**判定证据**

- 截图：`screenshots\ENV-FANOUT-00-main-window.png`
- 文本：`ENV-FANOUT-00-backend-launch-and-driver.txt`

**PASS/FAIL**

- PASS：主窗口可交互，Dev python 正确，`subagent_driver_ready` 出现。
- FAIL：跑到 Bundled exe、未构造 subagent driver、无法登录、主窗口不可交互。失败后停止后续 TC。

---

## 2. 通用 UI 操作纪律

1. 每个动作前先在记录里写 declare 行：`坐标=(x,y)|动作=click/type/paste/key|期望=...`。
2. 坐标必须是执行时截图定位出的物理像素；本文 `(x_in,y_in)`、`(x_send,y_send)`、`(x_art,y_art)` 都是占位。
3. 中文输入必须走剪贴板：`[System.Windows.Forms.Clipboard]::SetText("<中文 prompt>")` 后点击输入框并 `Ctrl+V`。
4. 发送可以按 Enter 或点击发送按钮，记录实际动作。
5. 发起 `deepresearch` 必须由 UI 真实点击+输入完成；禁止用 WebSocket、Python import、后端 API、脚本回放直接调用工具来充当 UI 证据。
6. PowerShell 只用于启动、grep log、查看 `DeepResearch/` 文件和保存证据。
7. 每个 deepresearch 最长观察 300s。超过 300s 且 UI 无正常报告时，按该 TC 的 timeout 规则判定。

---

## 3. 测试用例

### TC-F1：fan-out 触发与 coverage 观测

**case ID**：TC-F1  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=发起一个会拆出至少 2 个子问题的 deepresearch，并触发 research 子代理 fan-out`

**前置**

- 已通过 ENV-FANOUT-00。
- `$CFG` 中 `features.subagent_driver=true` 且 `[research].subagent_fanout=true`。

**操作步骤**

1. 截图定位聊天输入框 `(x_in,y_in)`。
2. 点击输入框。
3. 剪贴板粘贴：

```text
帮我深度调研 2025 年钠离子电池产业现状、政策环境、技术路线、供应链、主要公司和商业化风险，输出带引用的 Markdown 报告
```

4. 按 Enter 发送。
5. 等待出现运行状态和最终报告或 artifact 卡片，最长 300s。
6. 完成后采集日志证据：

```powershell
$ROOT = "G:\projects\deskpet"
$RESULT = "$ROOT\plans\manual-results-2026-06-21-fanout-phase2"
$LOG = "$RESULT\tauri-dev-fanout-on.log"

Select-String -Path $LOG -Pattern "subagent_scheduled kind=research run_id=.*\.dr-[0-9]+"
Select-String -Path $LOG -Pattern '"mode":\s*"fanout"|subagent_fanout|n_subagents|n_completed|waves|per_subrun_timeout_s|fanout'
```

7. 采集落盘和索引证据：

```powershell
$DR = "G:\projects\deskpet\DeepResearch"
$latest = Get-ChildItem $DR -Filter *.md | Where-Object Name -ne "index.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$latest.FullName
Get-Content $latest.FullName -Encoding UTF8 -TotalCount 60
Get-Content "$DR\index.md" -Encoding UTF8 -TotalCount 12
```

**期望**

- backend log 出现至少 2 条 `subagent_scheduled kind=research run_id=<sid>.dr-0/1/...`。
- run_id 后缀从 `.dr-0` 开始，至少包含 `.dr-0` 和 `.dr-1`。
- 最终工具结果或日志可见 `mode` 为 `fanout`，且有 `subagent_fanout` 块，包含 `n_subagents`、`n_completed`、`waves`。
- 报告正常返回，无 `tool_timeout`。
- `DeepResearch/index.md` 最新行模式列为 `fanout`。

**判定证据**

- 截图：`screenshots\TC-F1-running-or-result.png`
- 截图：`screenshots\TC-F1-artifact-or-report.png`
- 文本：`TC-F1-subagent-scheduled.txt`
- 文本：`TC-F1-coverage-fanout.txt`
- 文本：`TC-F1-index-top.txt`
- 文本：`TC-F1-report-head.txt`

**PASS/FAIL**

- PASS：UI 真实触发；log 中 research 子代理数 `N >= 2`；最终报告完成；coverage/index 均显示 fan-out。
- FAIL：没有 `subagent_scheduled`、只有 1 个子代理、报告 timeout、index 最新行不是 `fanout`、或只能通过非 UI 调用证明。

---

### TC-F2：前端子代理并发进度面板

**case ID**：TC-F2  
**declare**：`坐标=(x_in,y_in)|动作=observe + screenshot|期望=消息面板出现“🤖 子代理并发”，并显示 research 子代理 running/queued 状态`

**前置**

- 可复用 TC-F1 的运行过程；若 TC-F1 已结束且未截图到进度面板，则重新发起同一 prompt。

**操作步骤**

1. 在 deepresearch 发送后立即观察主消息面板顶部或消息流区域。
2. 如果看到 `🤖 子代理并发` 面板，截图。
3. 若面板折叠，点击展开箭头一次。
4. 在运行中阶段连续截图 2 次，间隔约 5~10s。
5. 等完成后再截图一次，确认终态。

**期望**

- 面板标题为 `🤖 子代理并发`。
- 运行中显示 `运行中 N/M` 或类似活动摘要。
- 行内可见 kind 为 `research`，task id 为 `dr-0`、`dr-1` 等。
- 默认并发 cap 下至少能看到 running 行；若 TC-F4 调低 research cap，则应同时看到部分 `排队中` 和部分 `运行中`。
- 完成后行状态进入 `全部完成` 或终态，不应永久卡在 queued/running。

**判定证据**

- 截图：`screenshots\TC-F2-progress-running-1.png`
- 截图：`screenshots\TC-F2-progress-running-2.png`
- 截图：`screenshots\TC-F2-progress-completed.png`
- 文本：`TC-F2-subagent-progress-log.txt`：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\tauri-dev-fanout-on.log"
Select-String -Path $LOG -Pattern "subagent_scheduled kind=research"
```

**PASS/FAIL**

- PASS：前端真实显示 `🤖 子代理并发`，至少 2 个 research 子代理行，状态随运行推进。
- FAIL：log 已证明 fan-out 但 UI 完全没有进度面板；或面板出现但行永久卡住，完成后不归位。

---

### TC-F3：统一报告、全局连续引用、DeepResearch/index fanout

**case ID**：TC-F3  
**declare**：`坐标=(x_art,y_art)|动作=click artifact + inspect report|期望=最终报告是跨子问题统一分析，不是 N 份子报告机械拼接`

**前置**

- TC-F1 已产生一份 fan-out 报告和 artifact。

**操作步骤**

1. 点击 TC-F1 的 artifact 卡片，打开最新 Markdown 报告。
2. 截图报告开头、正文中部和引用区。
3. PowerShell 采集最新报告和 index：

```powershell
$DR = "G:\projects\deskpet\DeepResearch"
$latest = Get-ChildItem $DR -Filter *.md | Where-Object Name -ne "index.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $latest.FullName -Encoding UTF8 -TotalCount 120
Select-String -Path $latest.FullName -Pattern "\[\^1\]|\[\^2\]|\[\^3\]|## References|## 引用|统一|综合|对比|结论"
Get-Content "$DR\index.md" -Encoding UTF8 -TotalCount 12
```

4. 采集 coverage/cite_check 证据：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\tauri-dev-fanout-on.log"
Select-String -Path $LOG -Pattern "cite_check_ok|cite_missing|cite_unused|subagent_fanout|fanout"
```

**期望**

- 报告是一份统一主题报告，有跨政策/技术/供应链/公司/风险等维度的综合分析和结论。
- 不应只是按 `dr-0`、`dr-1`、`dr-2` 或“子报告 1/子报告 2”机械拼接。
- 引用编号全局连续，例如正文和脚注使用 `[^1]`、`[^2]`、`[^3]`，不应在多个子报告内重复从 `[^1]` 开始。
- 引用自检通过：coverage 中 `cite_check_ok=true`，`cite_missing=[]`；若 log 不打印完整 coverage，则报告中不得有悬空引用，且最终 artifact 正常落盘。
- `DeepResearch/index.md` 最新行模式列为 `fanout`。

**判定证据**

- 截图：`screenshots\TC-F3-report-head.png`
- 截图：`screenshots\TC-F3-report-analysis.png`
- 截图：`screenshots\TC-F3-report-citations.png`
- 文本：`TC-F3-report-snippets.txt`
- 文本：`TC-F3-cite-check-and-coverage.txt`
- 文本：`TC-F3-index-top.txt`

**PASS/FAIL**

- PASS：报告统一综合、引用全局连续、引用自检无明显失败、index 最新行是 `fanout`。
- FAIL：输出是 N 份子报告拼接、引用编号冲突或悬空、index 模式列不是 `fanout`、报告未落 `DeepResearch/`。

---

### TC-F4：背压与 queued 后晋升（best-effort）

**case ID**：TC-F4  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=子问题数大于 research 并发 cap 时，部分子代理 queued，随后晋升 running 并全部完成`

**前置**

1. 关闭当前 Tauri dev。
2. 临时编辑 `$CFG`，将 research lane cap 调低到 1，确保更容易观察 queued：

```toml
[features]
subagent_driver = true

[research]
subagent_fanout = true
fanout_min_subquestions = 2
fanout_max_subquestions = 6

[agent.concurrency]
global_concurrency = 2

[agent.concurrency.lane_caps]
research = 1
```

3. 重启 Tauri dev，日志写到独立文件：

```powershell
$ROOT = "G:\projects\deskpet"
$RESULT = "$ROOT\plans\manual-results-2026-06-21-fanout-phase2"
$env:DESKPET_BACKEND_DIR = "$ROOT\backend"
$env:DESKPET_PYTHON      = "$ROOT\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
cd "$ROOT\tauri-app"
npx tauri dev 2>&1 | Tee-Object -FilePath "$RESULT\tauri-dev-fanout-backpressure.log"
```

4. 确认 gate：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\tauri-dev-fanout-backpressure.log"
Select-String -Path $LOG -Pattern "backend_launch|subagent_driver_ready"
```

**操作步骤**

1. 点击输入框。
2. 剪贴板粘贴：

```text
帮我深度调研 2025 年钠离子电池产业，从政策、上游材料、电芯技术、储能应用、两轮车应用、主要企业、成本曲线和商业化风险八个角度展开，输出带引用 Markdown 报告
```

3. 按 Enter 发送。
4. 运行中观察 `🤖 子代理并发` 面板；在看到 `排队中` 和 `运行中` 同时存在时截图。
5. 每 10~15s 截图一次，直到 queued 行晋升或全部完成。
6. 完成后采集日志：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\tauri-dev-fanout-backpressure.log"
Select-String -Path $LOG -Pattern "subagent_scheduled kind=research run_id=.*\.dr-[0-9]+"
Select-String -Path $LOG -Pattern "subagent_fanout|n_subagents|n_completed|waves|fanout"
```

**期望**

- 前端进度面板至少一度同时出现 1 个 `运行中` research 子代理和 1 个或多个 `排队中` research 子代理。
- backend log 中 `.dr-0/.dr-1/...` 的 `subagent_scheduled` 按 cap 分批出现，而不是所有子代理同一时刻全部 running。
- 最终 `n_completed + n_failed == n_subagents`；正常网络下应尽量全部 completed。
- 报告正常完成，无子代理丢失、无面板永久 queued。

**best-effort 边界**

如果 LLM 实际只拆出 2 个子问题，或者运行太快导致 windows-mcp 没截到 queued，可用本 TC prompt 重试一次。两次仍截不到 queued，但日志和 coverage 显示 `waves > 1`，可标 `PASS-BEHAVIOR / UI-QUEUE-NOT-CAPTURED`；若 `waves == 1`，标 `ENV-LIMITED` 并说明未触发背压。

**判定证据**

- 截图：`screenshots\TC-F4-progress-queued-running.png`
- 截图：`screenshots\TC-F4-progress-promoted.png`
- 截图：`screenshots\TC-F4-progress-completed.png`
- 文本：`TC-F4-subagent-scheduled.txt`
- 文本：`TC-F4-coverage-waves.txt`

**PASS/FAIL**

- PASS：真实 UI 触发；观察到 queued 后晋升，或 coverage `waves > 1` 且最终完成；无丢失。
- FAIL：超 cap 时仍无任何排队/分批迹象、最终子代理丢失、面板卡 queued、报告 timeout。

**收尾**

本 TC 后把 research cap 恢复默认或记录当前值，避免影响后续 TC：

```toml
[agent.concurrency]
global_concurrency = 4

[agent.concurrency.lane_caps]
research = 2
```

---

### TC-F5：flag OFF 回归，缺任一 flag 都走 flat

**case ID**：TC-F5  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=关闭任一必要 flag 后，同问题走 flat，无 research subagent_scheduled`

**前置**

- 使用与 TC-F1 相同或高度相似的 prompt。
- 每个子场景都用独立 log 文件，避免旧的 scheduled 行污染判定。

#### TC-F5A：`[research].subagent_fanout=false`

1. 关闭 Tauri dev。
2. 编辑 `$CFG`：

```toml
[features]
subagent_driver = true

[research]
subagent_fanout = false
```

3. 重启，日志写到 `tauri-dev-fanout-off-research.log`：

```powershell
$ROOT = "G:\projects\deskpet"
$RESULT = "$ROOT\plans\manual-results-2026-06-21-fanout-phase2"
$env:DESKPET_BACKEND_DIR = "$ROOT\backend"
$env:DESKPET_PYTHON      = "$ROOT\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
cd "$ROOT\tauri-app"
npx tauri dev 2>&1 | Tee-Object -FilePath "$RESULT\tauri-dev-fanout-off-research.log"
```

4. UI 粘贴并发送：

```text
帮我深度调研 2025 年钠离子电池产业现状、政策环境、技术路线、供应链、主要公司和商业化风险，输出带引用的 Markdown 报告
```

5. 完成后检查：

```powershell
$ROOT = "G:\projects\deskpet"
$RESULT = "$ROOT\plans\manual-results-2026-06-21-fanout-phase2"
$LOG = "$RESULT\tauri-dev-fanout-off-research.log"
$DR = "$ROOT\DeepResearch"
Select-String -Path $LOG -Pattern "subagent_scheduled kind=research"
Select-String -Path $LOG -Pattern "subagent_fanout|fanout"
Get-Content "$DR\index.md" -Encoding UTF8 -TotalCount 12
```

**期望**：`subagent_scheduled kind=research` 无输出；最终报告正常；最新 index 行模式列为 `flat`。

#### TC-F5B：`features.subagent_driver=false`

1. 关闭 Tauri dev。
2. 编辑 `$CFG`：

```toml
[features]
subagent_driver = false

[research]
subagent_fanout = true
```

3. 重启，日志写到 `tauri-dev-fanout-off-driver.log`。
4. 发送同一 prompt。
5. 完成后检查：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\tauri-dev-fanout-off-driver.log"
$DR = "G:\projects\deskpet\DeepResearch"
Select-String -Path $LOG -Pattern "subagent_driver_ready|subagent_scheduled kind=research"
Select-String -Path $LOG -Pattern "subagent_fanout|fanout"
Get-Content "$DR\index.md" -Encoding UTF8 -TotalCount 12
```

**期望**：不应出现 `subagent_driver_ready`；不应出现 `subagent_scheduled kind=research`；最终报告正常；最新 index 行模式列为 `flat`。

**判定证据**

- 截图：`screenshots\TC-F5A-flat-result.png`
- 截图：`screenshots\TC-F5B-flat-result.png`
- 文本：`TC-F5A-no-subagent-scheduled.txt`
- 文本：`TC-F5B-no-driver-no-subagent-scheduled.txt`
- 文本：`TC-F5A-index-flat.txt`
- 文本：`TC-F5B-index-flat.txt`

**PASS/FAIL**

- PASS：两个子场景都由 UI 真实触发；任一必要 flag 关闭时都无 research 子代理调度，报告正常，index 最新行为 `flat`。
- FAIL：flag off 后仍有 `subagent_scheduled kind=research`，或报告失败，或 index 仍写 `fanout`。

**收尾**

TC-F5 后恢复 fan-out ON：

```toml
[features]
subagent_driver = true

[research]
subagent_fanout = true
```

并重启 Tauri dev，后续 TC 使用 `tauri-dev-fanout-on-2.log`。

---

### TC-F6：递归守门，内层子调查不二次 fan-out

**case ID**：TC-F6  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter|期望=外层 fan-out 内部的 dr-* 子调查不再产生 dr-* 嵌套 fan-out`

**前置**

- 已恢复 `features.subagent_driver=true` 与 `[research].subagent_fanout=true`。
- 使用独立 log：`tauri-dev-fanout-on-2.log`。

**操作步骤**

1. 点击输入框。
2. 剪贴板粘贴：

```text
帮我深度调研 2026 年桌面 AI Agent 产品的长期记忆、文件系统权限、浏览器自动化、子代理并发和本地知识库设计，输出带引用 Markdown 报告
```

3. 按 Enter 发送并等待完成。
4. 采集日志：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\tauri-dev-fanout-on-2.log"
Select-String -Path $LOG -Pattern "subagent_scheduled kind=research run_id=.*\.dr-[0-9]+"
Select-String -Path $LOG -Pattern "subagent_scheduled kind=research run_id=.*\.dr-[0-9]+\.dr-[0-9]+"
Select-String -Path $LOG -Pattern "subagent_fanout|n_subagents|n_completed|waves|fanout"
```

**期望**

- 有外层 `subagent_scheduled kind=research run_id=<sid>.dr-0/1/...`。
- 不得出现嵌套 run_id，例如 `<sid>.dr-0.dr-0`、`<sid>.dr-1.dr-2`。
- scheduled 数量应接近外层子问题数，不应指数级增加。
- 最终报告完成，coverage 只有外层 `subagent_fanout` 观测。

**判定证据**

- 截图：`screenshots\TC-F6-fanout-result.png`
- 文本：`TC-F6-top-level-scheduled.txt`
- 文本：`TC-F6-no-nested-scheduled.txt`
- 文本：`TC-F6-coverage.txt`

**PASS/FAIL**

- PASS：有外层 fan-out，嵌套 grep 无输出，报告正常完成。
- FAIL：出现 `.dr-i.dr-j` 嵌套 scheduled；scheduled 数量明显爆炸；报告因递归/timeout 失败。

---

### TC-F7：depth-1 / 预算，wall-clock 不破 300s tool timeout

**case ID**：TC-F7  
**declare**：`坐标=(x_in,y_in)|动作=click + clipboard-paste + Enter + stopwatch|期望=fan-out deepresearch 在 300s 内正常返回，不出现 tool_timeout`

**前置**

- fan-out ON。
- 网络和 relay 可用；若 relay 或搜索源异常导致环境失败，需要记录为 ENV-LIMITED，不得改用后端直接调用。

**操作步骤**

1. 准备秒表或记录 PowerShell 时间；不要用脚本触发 UI。
2. 点击输入框并粘贴：

```text
帮我深度调研 2025 到 2026 年端侧 AI Agent 的市场格局、技术栈、隐私安全、成本结构、商业模式和开源生态，要求输出可执行建议和带引用 Markdown 报告
```

3. 发送瞬间记录开始时间，截图：`screenshots\TC-F7-start.png`。
4. 等最终报告或 artifact 出现，记录结束时间，截图：`screenshots\TC-F7-finished.png`。
5. 采集日志和报告：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-21-fanout-phase2\tauri-dev-fanout-on-2.log"
Select-String -Path $LOG -Pattern "subagent_scheduled kind=research|subagent_fanout|per_subrun_timeout_s|waves|tool_timeout|timeout"

$DR = "G:\projects\deskpet\DeepResearch"
$latest = Get-ChildItem $DR -Filter *.md | Where-Object Name -ne "index.md" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$latest.FullName
Get-Content $latest.FullName -Encoding UTF8 -TotalCount 80
```

**期望**

- 从 UI 发送到报告/artifact 出现的 wall-clock `<= 300s`。
- log/report 不出现 `tool_timeout`。
- 如果 coverage 可见，`subagent_fanout.per_subrun_timeout_s` 和 `waves` 有值，报告正常综合返回。
- 不因 depth-1 子调查导致二次 fan-out 或 timeout。

**判定证据**

- 截图：`screenshots\TC-F7-start.png`
- 截图：`screenshots\TC-F7-finished.png`
- 文本：`TC-F7-wall-clock.txt`
- 文本：`TC-F7-timeout-grep.txt`
- 文本：`TC-F7-report-head.txt`

**PASS/FAIL**

- PASS：真实 UI 触发，300s 内完成，无 `tool_timeout`，报告正常。
- FAIL：超过 300s 未完成、出现 `tool_timeout`、报告缺失、或因递归 fan-out 爆炸导致 timeout。

---

## 4. 证据归档格式

每个 case 在 `RESULTS.md` 追加：

```text
case: TC-Fx
flag: subagent_driver=<true/false>, subagent_fanout=<true/false>, research_lane_cap=<n>
坐标: (x_in,y_in), (x_send,y_send), (x_art,y_art)
动作: click 输入框 -> Clipboard 粘贴 -> Ctrl+V -> Enter/点击发送 -> 等待 -> 点击 artifact
截图: screenshots\TC-Fx-*.png
log: <tauri-dev-*.log grep 输出，含 subagent_scheduled / coverage / timeout>
report: <DeepResearch 最新 .md 绝对路径>
index: <index.md 最新 1-3 行，确认 mode=fanout/flat>
判定: PASS / FAIL / ENV-LIMITED / PASS-BEHAVIOR
备注: 如失败，写清是否重试、网络/relay/LLM 是否异常、是否被 best-effort 边界影响
```

---

## 5. 用例索引与覆盖边界

| ID | 覆盖点 | 一票判定 |
|---|---|---|
| ENV-FANOUT-00 | Dev python、scheduler 构造、relay 可用 | 必须看到 Dev python 与 `subagent_driver_ready` |
| TC-F1 | fan-out 触发、run_id、coverage、index fanout | `subagent_scheduled` 数量 `>=2`，index mode=`fanout` |
| TC-F2 | 前端 `SubagentProgressPanel` | UI 出现 `🤖 子代理并发` 和 research/dr-i 行 |
| TC-F3 | 统一报告、全局引用、引用自检、DeepResearch 落盘 | 报告不是 N 份拼接，引用不冲突，index mode=`fanout` |
| TC-F4 | 背压 queued/running/晋升 | queued 后晋升或 coverage `waves>1`，最终不丢 |
| TC-F5 | flag OFF 回归，两 flag 缺一不可 | 任一 flag 关闭都无 research scheduled，index mode=`flat` |
| TC-F6 | 递归守门 | 无 `.dr-i.dr-j` 嵌套 scheduled |
| TC-F7 | depth-1 / 300s 预算 | 300s 内正常报告，无 `tool_timeout` |

覆盖边界：本文验证 Tauri dev 真机路径，不证明 frozen 安装包路径；coverage JSON 是否可在 log 直接 grep 取决于当前 agent loop 记录粒度，若 log 不打印完整 coverage，必须用 UI 工具结果、报告正文、`DeepResearch/index.md` 和 scheduler 日志共同佐证，不得改用 import/WebSocket 直接调工具补证。
