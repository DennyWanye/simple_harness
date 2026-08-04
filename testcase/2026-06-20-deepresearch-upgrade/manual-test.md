# DeepResearch 升级 — 手工测试文档（windows-mcp 真机模拟）

> **被测改动**：deepresearch Phase 1（`research_run` → `deepresearch` 更名）+ Phase 2（recency 真 bug 修复 + coverage 可观测字段）。
> **执行方式**：真人 / windows-mcp 在真实 DeskPet 桌宠 App 上**模拟鼠标点击 + 键盘输入**执行，不允许用 WebSocket / pytest / import 内部模块 当 UI 测试证据（见项目 CLAUDE.md「手工测试纪律 HARD CONSTRAINT」）。
> **证据归档**：截图存 `G:\projects\deskpet\plans\manual-results-2026-06-20-deepresearch\screenshots\`，命名 `<case-id>.png`（如 `R1-1-tool-name.png`）。
> **最后更新**：2026-06-20

---

## 0. 被测改动事实基线（判定依据来源，便于复核）

执行者无需读代码，但判定 PASS/FAIL 时以下事实是依据来源：

| 事项 | 事实 | 出处 |
|---|---|---|
| LLM 可见工具名 | `deepresearch`（旧名 `research_run` 仅保留为 Python 别名，**LLM 永远不应再调 `research_run`**） | `backend/deskpet/tools/research_tools.py` `_RESEARCH_SCHEMA["name"]="deepresearch"` + `_register_deepresearch_tool()` 注册名 `"deepresearch"` |
| web_search 描述引导 | 「要【深度调研出带引用的报告】请改用 deepresearch」 | `backend/deskpet/tools/code_tools/registration.py:128` |
| SKILL 触发词 | 深度调研 / 调研报告 / 综述 / 技术选型 / 竞品 / 政策分析 | `deep-research/SKILL.md` |
| PPT 串联引导 | 「先调 deep-research skill / `deepresearch` 工具」 | `ppt-generate/SKILL.md:117` |
| 报告落盘路径 | `<user_data>/OutPut/Research/<slug>-<ts>.md`（含「调研覆盖 N 个来源…」头部） | `_save_report()` |
| recency 修复 | `default_extract` 现回填 `date` 字段 → 激活 `score_recency` 新鲜度维度 | `research_tools.py` default_extract 返回含 `"date": date` |
| 可观测字段 | `coverage` 含 `route` / `mode` / `n_dropped_by_reason{ai_generated,low_quality,mojibake,too_short}` / `elapsed_ms_per_stage{plan,search,fetch,score,synth}` | `_observability_coverage()` + `deepresearch()` coverage 拼装 |
| depth 档位预设 | light=(3子问题,2URL,8段,1轮) / standard=(5,4,12,1,默认) / deep=(6,5,16,**2轮**) | `_DEPTH_PRESETS` |
| deep 第二轮日志标签 | gather label = `"search2"` / `"extract2"`；coverage `rounds=2` | `deepresearch()` reflection round |
| 工具超时 | deepresearch 注册 `timeout_seconds=300.0` | `_register_deepresearch_tool()` |

---

## 1. 环境启动（前置章节 — 每次测试前必做一次）

> 目标：让 Tauri 跑**本仓库 master backend 的代码**（含本次改动），而不是旧 frozen exe；且免重新登录（keychain 已有 relay 凭据）。

### 1.1 清理孤儿进程（防端口双占 / 旧进程）

```powershell
taskkill /F /IM deskpet.exe 2>$null
taskkill /F /IM deskpet-backend.exe 2>$null
Get-Process node -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*deskpet*' } | Stop-Process -Force -ErrorAction SilentlyContinue
```

### 1.2 确认 keychain 已有凭据（免登录）

- 桌宠首启会走 onboarding 登录；若之前登录过，Windows DPAPI keychain 已存 `tsk_xxx` + `key_xxx`，本轮应**直接进主界面、不弹登录窗**。
- 若弹了登录窗：从 `G:\projects\deskpet\LOCAL-DEV-CREDENTIALS.md`（gitignored）读账号密码，**模拟点击输入框 → 输入 → 点登录**，等 relay 下发 key → keychain 写入 → 关 onboarding。
  - ⚠️ 截图前先关 onboarding 窗，**不要截到账号密码**。

### 1.3 用 worktree/master backend 启动（坑 #7/#8/#9 — 必须遵守）

- **不要**手动 `python main.py` 起 backend（会占 8100 导致 Tauri `os error 10048`）。
- **不要**手动 `npm run dev:relay`（会和 tauri 自带 vite 抢端口）。
- 只给 **Tauri 进程**注入 env，让它自己 spawn backend：

```powershell
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON      = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
# 端口用 main 默认 8100 / 5173；若占用再换 8300/5373
cd G:\projects\deskpet\tauri-app
npx tauri dev 2>&1 | Tee-Object -FilePath "G:\projects\deskpet\plans\manual-results-2026-06-20-deepresearch\tauri-dev.log"
```

### 1.4 验证跑的是 worktree 代码（HARD GATE — 不过这条后面全白测）

- grep tauri dev log，**必须**看到 dev python 行，**不能**看到 bundled exe 行：

```powershell
Select-String -Path "G:\projects\deskpet\plans\manual-results-2026-06-20-deepresearch\tauri-dev.log" -Pattern "backend_launch"
```

- ✅ 期望：`[backend_launch] Dev python=...\backend\.venv\Scripts\python.exe backend_dir=...\backend`
- ❌ 若出现：`[backend_launch] Bundled exe=...` → 跑的是旧 frozen，**停止测试**，回 1.3 修 env 重启。
- 证据：截图主界面（桌宠出现）存 `env-00-boot.png` + 保存上面 grep 输出到 `env-00-backend-launch.txt`。

### 1.5 日志监看入口（后续所有用例共用）

backend structlog 全走 stderr → Tauri `Stdio::inherit()` → 落进 `tauri-dev.log`。后续用例「backend log 证据」一律 grep 这个文件：

```powershell
$LOG = "G:\projects\deskpet\plans\manual-results-2026-06-20-deepresearch\tauri-dev.log"
```

---

## 2. 对话操作通用步骤（每个研究类用例复用）

每个触发研究的用例，「精确步骤」里的「**发起对话**」均指以下动作序列（windows-mcp 真模拟）：

1. **截图**抓当前桌宠状态（记录基线）。
2. **定位对话输入框**：Snapshot/Screenshot 找到桌宠聊天输入框坐标 `(x_in, y_in)`。
3. **declare**：`坐标=(x_in,y_in) | 动作=click 聚焦输入框 | 期望=光标进入输入框`。
4. **真点击聚焦**：`SetCursorPos(x_in,y_in)` + SendInput LEFTDOWN/UP（WebView2 不吃老式 mouse_event，用 SendInput）。
5. **中文输入用剪贴板**：STA Runspace `[Clipboard]::SetText("<prompt>")` → `Ctrl+V`（SendKeys 不支持中文 IME）。
6. **回车发送**：SendKeys `{ENTER}`（或点发送按钮）。
7. **等待**：deepresearch 重型工具，standard 档可能 30–120s，deep 档可能逼近 300s。用 `WaitFor` 轮询「ArtifactCard 出现」或对话出现报告/完成提示，**最长等 300s**。
8. **截图**抓最终结果（ArtifactCard / 报告气泡）。

---

## 3. 测试用例

### 维度 A — 更名生效（核心，Phase 1）

#### TC-R1-1 — 触发深度调研，backend 实际调用工具名为 `deepresearch`

- **前置**：§1 环境就绪且 §1.4 确认跑 worktree 代码；keychain 有 relay key（真 LLM 链路可用）。
- **精确步骤**：
  1. 按 §2 发起对话，prompt 输入：`帮我深度调研 Rust 异步运行时 Tokio 的架构与竞品对比`
  2. 等待研究完成（最长 300s）。
  3. grep backend log 确认实际被调用的工具名：
     ```powershell
     Select-String -Path $LOG -Pattern "deepresearch|research_run"
     ```
- **预期**：
  - 出现 `deepresearch` 相关日志（工具被调度 / coverage / 落盘路径）。
  - **绝不**出现 LLM 调 `research_run`（旧名）的记录。
  - 对话最终出现 ArtifactCard（可点开的研究报告卡片）。
- **判定依据（PASS）**：log 中工具名是 `deepresearch` 且无 `research_run` 调用 + ArtifactCard 渲染成功。
- **判定依据（FAIL）**：log 出现模型调用 `research_run`；或工具名解析不到；或卡片未渲染。
- **证据**：`R1-1-artifactcard.png` + `R1-1-log-toolname.txt`（grep 输出，含 timestamp）。

#### TC-R1-2 — web_search 描述引导改用 deepresearch（路由引导文案生效）

- **前置**：同上。
- **精确步骤**：
  1. 按 §2 发起对话：`帮我查一下 Tokio 最新稳定版本号是多少`（这是快查，应走 web_search）。
  2. 观察该轮是否走轻量 web_search 而非 deepresearch（见 TC-E2-1 路由用例）。
  3. 此用例侧重确认 web_search 仍可用、且不报「research_run 不存在」类错误。
- **预期**：快查正常返回，不触发重型 deepresearch；无任何关于旧名 `research_run` 的报错。
- **判定依据（PASS）**：返回事实答案 + log 无 `research_run`/工具不存在错误。
- **证据**：`R1-2-quicksearch.png` + `R1-2-log.txt`。

---

### 维度 B — 研究 E2E happy path（Phase 0 质量基线同时采集）

#### TC-R2-1 — standard 档技术主题，出带 [^n] 引用的 Markdown + 落盘文件

- **前置**：§1 就绪。
- **精确步骤**：
  1. 按 §2 发起对话：`帮我深度调研 向量数据库选型：Milvus、Qdrant、pgvector 的对比`（不指定 depth → 默认 standard）。
  2. 等完成（记录开始/结束时间，算耗时）。
  3. 点开 ArtifactCard，肉眼看报告正文。
  4. 检查落盘文件：
     ```powershell
     Get-ChildItem "$env:DESKPET_USER_DATA_DIR\OutPut\Research" -Filter *.md | Sort-Object LastWriteTime -Descending | Select-Object -First 3
     ```
     （若不知 user data dir，从 log grep `OutPut/Research` 路径行。）
- **预期**：
  - 报告是结构化 Markdown（含 TL;DR / Background / Current state 等），正文有 `[^n]` 内联脚注，底部有「## 引用」脚注列表。
  - `OutPut/Research/<slug>-<ts>.md` 新文件存在，开头含「> 调研覆盖 **N 个来源** 来自 **M 个独立域名**」。
- **判定依据（PASS）**：报告含 ≥1 个 `[^n]` 引用 + 引用列表 + 落盘 md 文件存在且头部正确。
- **判定依据（FAIL）**：报告无任何引用 / 引用列表为空 / 未落盘 / 报告是 no_results 模板（「未能找到可用的来源」）。
- **基线采集**：记录耗时（秒）、来源数 N、域名数 M、主观质量（1-5 分）到 `R2-1-baseline.txt`。
- **证据**：`R2-1-report.png`（报告正文带脚注）+ `R2-1-file.txt`（落盘文件列表 + 头部 head 10 行）。

---

### 维度 C — deep 档反思迭代（Phase 0/V8）

#### TC-R3-1 — deep 档触发第二轮补搜（log 出现 search2）

- **前置**：§1 就绪。注意 deep 档慢，最长等 300s。
- **精确步骤**：
  1. 按 §2 发起对话，**显式要 deep**：`用 deep 深度调研 2026 年固态电池产业化进展与主要厂商路线`
     （若桌宠不识别「deep」，改说「做一份最深入的调研报告」让 SKILL 走 deep）。
  2. 等完成。
  3. grep log 找第二轮证据：
     ```powershell
     Select-String -Path $LOG -Pattern "search2|extract2"
     ```
  4. 点开报告，grep 落盘文件头部找 `rounds`：
     ```powershell
     Select-String -Path $LOG -Pattern '"rounds": 2|rounds=2'
     ```
- **预期**：log 出现 `search2`（或 `extract2`）任务标签 **或** coverage `rounds=2`；报告来源数普遍 ≥ standard 档。
- **判定依据（PASS）**：出现 search2/extract2 **或** rounds=2 任一硬证据。
- **判定依据（FAIL）**：deep 档跑完但 rounds 恒为 1 且无 search2（说明反思轮没触发）；或 300s 内 tool_timeout 半途丢源。
- **备注**：若 LLM 在 gap 分析时判定「coverage 已足够」会合法返回 `[]` 不补搜（rounds=1）。此情况**不算 FAIL**，但需在报告里注明「本次 gap 返回空，建议换覆盖更稀疏的主题复测」并**等用户确认**。
- **证据**：`R3-1-log-search2.txt` + `R3-1-report.png`。

---

### 维度 D — recency 生效（Phase 2 修复点）

#### TC-R4-1 — 时效性主题倾向近期源

- **前置**：§1 就绪。
- **精确步骤**：
  1. 按 §2 发起对话，选**强时效**主题：`帮我深度调研 2026 年最新的开源大模型发布与能力对比`
     （主题含「2026 最新」→ `infer_topic_velocity` 应判为高 velocity → recency 维度权重上升）。
  2. 等完成，点开报告。
  3. 看引用列表里源的发布日期倾向（标题/URL 是否偏近期），并 grep log 的 velocity：
     ```powershell
     Select-String -Path $LOG -Pattern "topic_velocity|velocity="
     ```
- **预期**：
  - coverage `topic_velocity` 非默认低档（应为 fast/high 类）。
  - 报告引用整体倾向近期来源（对比修复前 date 恒空 → recency 维度失效、近期源不被偏好）。
- **判定依据（PASS）**：log 显示该主题 velocity 偏高 + 报告引用含可见近期内容（不是清一色老资料）。
- **判定依据（FAIL）**：velocity 字段缺失；或引用明显全是旧源且报告未体现「最新」。
- **备注**：recency 是「打分维度激活」非「硬过滤」，判定看**倾向**不看绝对。需主观判断时记录依据并保留截图。
- **证据**：`R4-1-report-citations.png`（引用列表）+ `R4-1-log-velocity.txt`。

---

### 维度 E — 可观测字段（Phase 2 新增）

#### TC-R5-1 — coverage 含 n_dropped_by_reason / elapsed_ms_per_stage / route / mode 且有值

- **前置**：跑过 §3 任一研究用例（复用 TC-R2-1 的那次即可，无需重跑）。
- **精确步骤**：
  1. grep backend log 找 coverage / 可观测字段：
     ```powershell
     Select-String -Path $LOG -Pattern "n_dropped_by_reason|elapsed_ms_per_stage|n_dropped|elapsed_ms|route=|\"route\""
     ```
  2. 若 log 未直接打印整个 coverage dict，则检查落盘报告头部 + 同时确认工具返回 JSON 含这些键（从 ArtifactCard 详情 / log 的 tool_result 片段）。
- **预期**：能看到以下字段且**有值**（不是恒 0 / 缺失）：
  - `route`（如 `ddg`）、`mode`（light/standard/deep）。
  - `n_dropped_by_reason` 含 4 个键：`ai_generated` / `low_quality` / `mojibake` / `too_short`（值为整数，可能为 0，但键必须齐）。
  - `elapsed_ms_per_stage` 含 5 个键：`plan` / `search` / `fetch` / `score` / `synth`，且至少 `search`/`fetch`/`synth` > 0。
- **判定依据（PASS）**：4 个 drop 原因键齐全 + 5 个 stage 计时键齐全且非全零 + route/mode 有值。
- **判定依据（FAIL）**：任一字段缺失；或 elapsed 全为 0（说明计时没接上）；或 n_dropped_by_reason 不是按原因细分的 4 键结构。
- **证据**：`R5-1-coverage-fields.txt`（grep 输出，需可见 4 drop 键 + 5 stage 键）。

#### TC-R5-2 — 强制触发 drop，验证 n_dropped_by_reason 计数器**真的会增**（不只是键存在）

> 评估补强：TC-R5-1 允许 drop 值全为 0，无法证明 Phase 2 新增的计数器逻辑真在累加。本例用易出**字典站/自媒体转帖**的中文主题强制制造 drop。

- **前置**：§1 就绪。
- **精确步骤**：
  1. 按 §2 发起对话，选会被拆成单字、易命中字典/词义站的主题：`帮我深度调研 茅台 的品牌与产能`（"茅台"易被搜成"茅"/"台"单字 → 命中字典站；中文结果易混入搜狐/百家号自媒体转帖）。
  2. 等完成。
  3. grep log 看 drop 计数 + drop 事件：
     ```powershell
     Select-String -Path $LOG -Pattern "n_dropped_by_reason|dropped_low_quality|dropped_ai_generated|dropped_mojibake|low_quality.*[1-9]"
     ```
- **预期**：`n_dropped_by_reason` 中**至少一项 > 0**（最可能是 `low_quality` 字典站 或 `ai_generated` 自媒体 AI 声明页），且 log 有对应 `dropped_*` 事件行。
- **判定依据（PASS）**：n_dropped_by_reason 某原因键值 ≥ 1，且与 log 里 `dropped_*` 事件条数一致（计数器=事件数）。
- **判定依据（FAIL）**：明明 log 有 `dropped_low_quality`/`dropped_ai_generated` 事件，但 `n_dropped_by_reason` 对应键仍为 0（说明计数器没接上 drop 分支）。
- **备注**：若该主题本次恰好没产生任何 drop（搜索结果都干净），换"特斯拉"/"小米"等更易出单字字典页的主题复测；连续 2 个主题都 0 drop 再标「无法触发，环境受限」并记录。
- **证据**：`R5-2-drop-count.txt`（需同时见 n_dropped_by_reason 值 + dropped_* 事件行）。

---

### 维度 F — 下游 PPT 链路不破

#### TC-R6-1 — “研究 X 然后做成 PPT”按新名 deepresearch 引导且链路通

- **前置**：§1 就绪；python-pptx 可用（否则 PPT 会走 markdown_fallback，属合法降级）。
- **精确步骤**：
  1. 按 §2 发起对话：`帮我研究一下 RISC-V 生态现状，然后做成一份 PPT`
  2. 等完成（这条最久，研究 + PPT 两段，最长各等到超时）。
  3. grep log 确认链路：
     ```powershell
     Select-String -Path $LOG -Pattern "deepresearch|ppt_create|markdown_fallback|research_run"
     ```
- **预期**：
  - 先调 `deepresearch`（**不是** research_run）→ 拿到 report_md/citations。
  - 再调 PPT 生成 → 出 `.pptx` 文件 + ArtifactCard，或（python-pptx 缺）`markdown_fallback` 合法降级。
- **判定依据（PASS）**：log 调用链含 `deepresearch` 后接 PPT 生成 + 产出 .pptx（或合法 fallback）+ 无 `research_run`。
- **判定依据（FAIL）**：链路中出现 `research_run`；或 PPT 步骤报「找不到研究工具」；或研究产物未传给 PPT。
- **证据**：`R6-1-ppt-card.png` + `R6-1-log-chain.txt`。

---

### 维度 G — 边界 / 异常

#### TC-E1-1 — 空 / 无意义主题

- **前置**：§1 就绪。
- **精确步骤**：按 §2 发起对话：`帮我深度调研 asdfghjkl 这个`（无意义串）。等返回。
- **预期**：不崩溃。返回 no_results 模板（「未能找到可用的来源…已尝试以下子问题」）或带 `errors` 字段的报告；桌宠正常给出「没查到」类回复。
- **判定依据（PASS）**：App 不崩 + backend 不抛未捕获异常（log 无 traceback 顶到顶层）+ 给出可读的「无结果」回复。
- **判定依据（FAIL）**：App 崩溃 / 卡死 / log 出现未捕获 traceback / 桌宠无任何回复。
- **证据**：`E1-1-noresults.png` + `E1-1-log.txt`。

#### TC-E1-2 — 明显查不到结果 / 网络受限降级

- **前置**：§1 就绪。可选：临时断网或拔代理制造网络受限（若做，需在报告注明）。
- **精确步骤**：
  1. 按 §2 发起对话：`帮我深度调研 一个根本不存在的虚构产品 Zorblax9000 的技术规格`
  2. （可选）网络受限场景：搜索阶段断网后发同一 prompt。
  3. grep log 看 errors / 降级：
     ```powershell
     Select-String -Path $LOG -Pattern "no search results|no usable passages|errors|extract:|search:"
     ```
- **预期**：报告**仍出**（no_results 模板或 passages-only fallback），`errors` 字段记录失败原因（如 `no search results` / `extract:...`），不假装成功。
- **判定依据（PASS）**：有报告产出 + errors 字段如实记录 + 无崩溃。
- **判定依据（FAIL）**：无任何产出 / 崩溃 / 编造出看似有引用实则虚假的结论。
- **证据**：`E1-2-degraded.png` + `E1-2-log-errors.txt`。

---

### 维度 H — 路由正确性（快查不误触发重型工具）

#### TC-E2-1 — 简单事实问题不触发 deepresearch

- **前置**：§1 就绪。
- **精确步骤**：
  1. 按 §2 发起对话：`MCP 是什么？`（一句话快查，**不应**触发 deepresearch）。
  2. 观察响应速度（快查应秒级~十几秒，远快于 deepresearch 的 30–300s）。
  3. grep log 确认未走重型工具：
     ```powershell
     Select-String -Path $LOG -Pattern "deepresearch|web_search"
     ```
- **预期**：模型用 `web_search`（或直接回答），**不**调 `deepresearch`；不产生 `OutPut/Research/*.md` 落盘文件。
- **判定依据（PASS）**：log 无 `deepresearch` 调用 + 无新 Research 落盘 + 快速返回简短答案。
- **判定依据（FAIL）**：简单问题误触发 deepresearch（log 出现 deepresearch 调用 / 出现 Research 落盘 / 等了几十秒）。
- **证据**：`E2-1-quickanswer.png` + `E2-1-log-norepeat.txt`。

#### TC-E2-2 — 调研类触发词正确触发 deepresearch

- **前置**：§1 就绪。
- **精确步骤**：分别测两条触发词，各发一条、各看 log 是否调 `deepresearch`：
  1. `帮我写一份关于边缘计算的技术综述`（触发词「综述」）。
  2. `做个 React vs Vue 的技术选型分析`（触发词「技术选型」）。
- **预期**：两条都触发 `deepresearch`（重型路径），产出带引用报告 + 落盘。
- **判定依据（PASS）**：两条 log 均出现 `deepresearch` 调用 + ArtifactCard。
- **判定依据（FAIL）**：触发词未触发重型工具（被当快查处理 → 漏召回）。
- **证据**：`E2-2-survey.png` / `E2-2-techselect.png` + `E2-2-log.txt`。

---

### 维度 I — 既有能力回归（Phase 2 改动不破坏旧路径）

#### TC-R7-1 — 中文一手源直连（财报→巨潮）仍正常，且 recency 改动未破坏 direct_sources

> 评估补强：Phase 2 改了打分/抽取返回结构，需回归验证「财报类主题触发巨潮 cninfo 直连」这条受影响路径（该路径硬编码 recency=8.0、跳过长度门）仍工作。

- **前置**：§1 就绪；`[research].direct_sources` 默认开。
- **精确步骤**：
  1. 按 §2 发起对话，选 A 股上市公司财报主题：`帮我深度调研 宁德时代 2024 年财报的关键经营数据`
  2. 等完成，点开报告看引用来源。
  3. grep log 看是否走了直连源：
     ```powershell
     Select-String -Path $LOG -Pattern "cninfo|direct:|巨潮|edgar|direct_source"
     ```
- **预期**：报告引用中出现巨潮（cninfo.com.cn）公告类一手源（或 log 有 `cninfo`/`direct:` 命中）；报告正常生成、未因 Phase 2 改动报错。
- **判定依据（PASS）**：log 出现 cninfo/direct 直连命中 **或** 引用列表含 cninfo 来源；报告正常出 + 无 traceback。
- **判定依据（FAIL）**：direct_sources 路径报错/抛异常；或财报主题完全没走直连且报告质量明显塌（说明 Phase 2 改动破坏了该路径）。
- **备注**：若巨潮当时无命中（公司名解析问题）会 EDGAR 兜底或正常 web 源，不强制要求 cninfo，但**不得报错**。
- **证据**：`R7-1-report-cninfo.png` + `R7-1-log-direct.txt`。

---

## 4. 证据归档说明

- **截图目录**：`G:\projects\deskpet\plans\manual-results-2026-06-20-deepresearch\screenshots\`
- **log / 文本证据目录**：同上父目录（`...\plans\manual-results-2026-06-20-deepresearch\`），含 `tauri-dev.log` + 各 `*.txt` grep 输出。
- **命名规范**：`<case-id>-<简述>.png` / `<case-id>-<简述>.txt`（如 `R1-1-log-toolname.txt`）。
- **每个用例报告格式**（汇总写到本目录 `RESULTS.md`）：
  ```
  case:    TC-R1-1
  坐标:    (x_in, y_in) [物理像素]
  动作:    click 输入框 → Clipboard "帮我深度调研 ..." → Ctrl+V → Enter
  截图:    screenshots/R1-1-artifactcard.png
  log证据: <grep deepresearch 命中行，含 timestamp> / 无 research_run
  判定:    PASS / FAIL / RETRY-N / SKIP（带理由）
  ```
- **失败重试纪律**：任一 case 失败须用 ≥3 种不同 workaround 重试（SetCursorPos+SendInput / Snapshot label click / App switch 聚焦后再 click；中文输入失败换剪贴板 STA Runspace）后才能标「环境受限」，且 SKIP 须等用户确认。
- ⚠️ **截图脱敏**：截任何含 onboarding 窗的画面前先关窗，不要截到账号密码。

---

## 5. 用例索引（共 14 例）

| ID | 维度 | 标题 | 一票关键判定 |
|---|---|---|---|
| TC-R1-1 | A 更名核心 | 实际调用名为 deepresearch | log 有 deepresearch 无 research_run |
| TC-R1-2 | A 更名核心 | web_search 引导/无旧名报错 | 无 research_run 不存在错误 |
| TC-R2-1 | B happy path | standard 出带 [^n] 报告 + 落盘 | ≥1 引用 + Research/*.md 存在 |
| TC-R3-1 | C deep 迭代 | deep 档第二轮补搜 | search2 或 rounds=2 |
| TC-R4-1 | D recency | 时效主题倾向近期源 | velocity 偏高 + 近期引用 |
| TC-R5-1 | E 可观测 | coverage 新字段有值 | 4 drop 键 + 5 stage 键齐全非全零 |
| TC-R5-2 | E 可观测 | 强制 drop 验证计数器真增 | n_dropped 某键≥1 且=log 事件数 |
| TC-R6-1 | F PPT 链路 | 研究→PPT 按新名串联 | deepresearch→PPT 通 无 research_run |
| TC-R7-1 | I 既有回归 | 中文一手源直连(财报→巨潮)不破 | cninfo/direct 命中 + 不报错 |
| TC-E1-1 | G 边界 | 空/无意义主题 | 不崩 + 无结果可读回复 |
| TC-E1-2 | G 异常 | 查不到/网络受限降级 | 报告仍出 + errors 字段 |
| TC-E2-1 | H 路由 | 快查不误触发重型 | 无 deepresearch 调用 + 无落盘 |
| TC-E2-2 | H 路由 | 调研触发词正确触发 | 两条均调 deepresearch |
| (env) | 前置 | 跑 worktree 代码校验 | log 有 Dev python 无 Bundled exe |

**覆盖维度清单**：A 更名生效（核心）/ B 研究 E2E happy path（含质量基线）/ C deep 反思迭代 / D recency 生效 / E 可观测字段（含**强制 drop 验证计数器**）/ F 下游 PPT 链路 / G 边界+异常（空主题/查不到/网络降级）/ H 路由正确性（快查不误触发 + 触发词正确触发）/ I 既有能力回归（中文一手源直连）。
