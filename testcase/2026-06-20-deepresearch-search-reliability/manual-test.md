# 手工测试文档 — deepresearch §6.0 搜索可靠性改造（真机 windows-mcp E2E）

> 适用对象：真人测试员 / windows-mcp 子代理，在**真实桌宠 App** 上**模拟鼠标点击 + 键盘输入**执行。
> 被测改动：`backend/deskpet/tools/research_sources.py`（A 直连权威源）+ `backend/deskpet/tools/search_provider.py`（B bing-cdp / C SearXNG / D 硬化 + 引擎降级队列 + 观测侧信道）+ `backend/deskpet/tools/research_tools.py`（直连源 wiring + route/coverage 观测 + no_results 兜底）。
> 单测状态：132 绿（仅证明"代码加载到了"，**不证明"用户用得了"**）。本文档要补的是**真机 E2E**。
>
> 证据归档目录：`plans/manual-results-2026-06-20-deepresearch-6.0/screenshots/`
> 测试日期：2026-06-20 ｜ 分支：master

---

## 0. 改造背景与本次真机验证的「最高优先级」

§6.0 把 deepresearch 检索从「裸 Bing/DDG SERP 抓取（会被 IP 级封禁）」改造成多路线：

| 路线 | 名称 | 默认状态 | 机理 |
|---|---|---|---|
| **A** | 直连权威源 | **默认开** | 综述/背景→Wikipedia；论文/技术选型→arXiv；财报→巨潮 cninfo（A股不命中→SEC EDGAR 兜底）；国标→openstd。**bypass SERP，免封禁。** `direct_source_types` 默认 `["cninfo","openstd","wikipedia","arxiv"]`（s2/wikidata 默认 off）。 |
| **B** | 浏览器渲染搜索 `bing-cdp` | opt-in | 系统 Edge 无头（CDP）渲染 Bing SERP，绕 HTTP 封禁。需 config `[research].search_engines` 含 `"bing-cdp"`。captcha 软封 → `bing_cdp_captcha_suspected` 记入 `report.errors`。 |
| **C** | SearXNG | opt-in（需 Docker + `searxng_url`） | 自托管聚合搜索。 |
| **D** | SERP 抓取硬化 `serp_hardening` | 默认 off | 轮换 UA / 失败冷却 / 结果缓存 / 退避。 |

**Phase 0 spike 病灶（必须牢记的对照基线）**：spike 实测——免费 Bing/DDG 在连续负载下被 **IP 级封禁** → **13 个研究里 11 个拿到 0 来源**。

> 🎯 **本次真机验证的核心价值 = 即便 SERP 被封，直连源（A）仍能拿到权威源。**
> 因此**最重要的 PASS 条件**（见 TC-A1 / TC-A2）：
> 1. **连续多次研究不再全部 0 来源**（对照 spike 的 11/13 失败）；
> 2. 报告引用里**出现 `wikipedia.org` / `arxiv.org` / `cninfo` 等直连域名**。

---

## 1. 🔒 环境前置（HARD GATE — 不达标即「测了等于白测」）

### 1.1 App 启动方式（必须跑 master 的 backend，不能跑 frozen exe）

> ⚠️ 项目坑 #7/#8（见项目 CLAUDE.md）：**不要手动起 backend**（会和 Tauri 自管的 backend 双占 8100 端口）；**不要手动起第二个 vite**。只给 **Tauri 进程**注入 env，让它自己 spawn backend + vite。

1. keychain 已有 relay 凭据（免登录，直接进桌宠主界面）。若弹 onboarding 登录窗，按项目 CLAUDE.md「开发期登录测试账号」流程登录后再继续。
2. 启动命令（PowerShell，给 **Tauri** 注入 env 后跑 `tauri dev`）：

```powershell
$env:DESKPET_BACKEND_DIR = "G:\projects\deskpet\backend"
$env:DESKPET_PYTHON      = "G:\projects\deskpet\backend\.venv\Scripts\python.exe"
$env:DESKPET_DEV_MODE    = "1"
# 把 tauri dev 的 stdout/stderr 重定向到 log（backend structlog 走 stderr → inherit → 落这里）
cd G:\projects\deskpet\tauri-app
npx tauri dev *> "G:\projects\deskpet\plans\manual-results-2026-06-20-deepresearch-6.0\tauri-dev.log"
```

（若你的端口隔离方案不同，按需加 `DESKPET_BACKEND_PORT` / `DESKPET_VITE_PORT`，但**只注给 Tauri 进程**。）

### 1.2 ✅ HARD GATE：确认跑的是 Dev python 而非 Bundled exe

启动后**立刻** grep 启动日志：

```powershell
Select-String -Path "G:\projects\deskpet\plans\manual-results-2026-06-20-deepresearch-6.0\tauri-dev.log" -Pattern "backend_launch"
```

- ✅ **必须看到**：`[backend_launch] Dev python=... backend_dir=G:\projects\deskpet\backend`
- ❌ **若看到** `[backend_launch] Bundled exe=...` → 跑的是旧 frozen 构建，**不含本次改动，立即停测、修 env 重启**。

> 截图归档：`screenshots/00-env-gate-dev-python.png`（截到 `Dev python=` 那行）。**Gate 不通过 → 整轮作废。**

### 1.3 backend 日志（判定证据来源）

- backend structlog **全走 stderr → Tauri `Stdio::inherit()` → 落进上面重定向的 `tauri-dev.log`**。所有 grep 判定都对这个文件做。
- 关键 grep 词（来自代码事实）：
  - `direct:` — 直连源单源异常（`research_tools.py:1250` 形如 `direct:wikipedia:'...': <exc>`，**无此行 = 直连源没抛异常**，正常）。
  - `bing_cdp_captcha_suspected` — bing-cdp 吃软封（`search_provider.py:607`，会 `log.warning` + 进 errors）。
  - `[backend_launch]` — 启动解释器判定。
  - `os error 10048` — 端口双占（出现说明你违规手动起了 backend）。

### 1.4 报告落盘位置（核心证据文件）

报告写到 **`<user_data>/OutPut/Research/<slug>-<时间戳>.md`**（`research_tools.py:_save_report` → `paths.output_dir("Research")`）。

- 若启动注了 `DESKPET_USER_DATA_DIR` → `<该目录>\OutPut\Research\`。
- 否则经典模式 → `%AppData%\deskpet\OutPut\Research\`。

每个用例测完，用资源管理器或 PowerShell 找到**最新**那个 `.md` 读正文：

```powershell
$dir = "$env:APPDATA\deskpet\OutPut\Research"   # 若注了 DESKPET_USER_DATA_DIR 改成对应路径
Get-ChildItem $dir -Filter *.md | Sort-Object LastWriteTime -Desc | Select-Object -First 1 | ForEach-Object { $_.FullName }
```

> ⚠️ 落盘条件（代码事实 `research_tools.py:1760`）：`report.report_md AND report.citations` 都非空才落盘 + 才出 ArtifactCard。**全 0 来源时不落盘、不出卡片**（这恰是 TC-X1 兜底用例要验的）。

### 1.5 真输入纪律（windows-mcp / SendInput / 剪贴板）

> WebView2 / Chromium **不响应老式 `mouse_event` API** → 必须用 `SendInput`（圣杯，见全局 CLAUDE.md）。中文输入 **SendKeys 不支持 IME** → 必须走 **剪贴板（Clipboard.SetText 中文 + Ctrl+V）**。

- **每个动作前先在对话里 declare**：`坐标=(x,y) [物理像素] | 动作=click/type/paste | 期望=...`。
- 点击优先级：`SetCursorPos(x,y)` + `SendInput(LEFTDOWN/UP)` → windows-mcp `Click(label=...)`（用 Snapshot 出的 label）→ `App switch` 聚焦后再点。
- 中文输入：STA Runspace + `[System.Windows.Forms.Clipboard]::SetText("中文")` + 先 Click 输入框聚焦 → Ctrl+V → Enter。
- DPI 错位 → `SetProcessDpiAwareness(2)` 用 physical pixel。
- **每个动作后截图**存 `screenshots/<case-id>-<step>.png`。
- **失败 retry ≥ 3 次不同 workaround** 才能标「环境受限」；跳过任何 case 须显式声明 + 给理由 + **等用户确认**。

### 1.6 触发方式（统一对话指令）

桌宠主界面输入框输入：**`帮我深度调研 <主题>`**（"深度调研/深度研究/研究…做成报告" 触发 `deepresearch` 工具；"快速查一下" 会走 `web_search` ≠ 本工具，不要用）。
研究是重任务，**单次可能耗时 30s–3min**，触发后**用截图轮询**等 ArtifactCard / 助手回复出现，**不要中途打断**。

---

## 2. 用例总览

| ID | 维度 | 类型 | 优先级 | 简述 |
|---|---|---|---|---|
| TC-A1 | A 直连源生效 | 正常·核心 | ⭐核心 | 综述主题 → 报告引用出现 wikipedia.org / arxiv.org |
| TC-A2 | spike 病灶修复 | 正常·核心 | 🔴最关键 | 连续 4 个不同主题 → 不再全 0 来源 |
| TC-A3 | 财报→cninfo 直连回归 | 回归 | 高 | 宁德时代财报 → 引用含 cninfo 巨潮 PDF |
| TC-A4 | 通用主题默认覆盖率 | 诚实边界 | 高 | 无直连意图主题 → 默认是否仍可能 0 源(暴露 bing-cdp 该不该默认开) |
| TC-O1 | 观测字段 | 观测 | 高 | coverage.route.direct_sources_hit + elapsed_ms_per_stage.direct + n_dropped_by_reason.direct_source_empty |
| TC-B1 | B bing-cdp（config 启用后） | opt-in | 中 | engines_hit 含 bing-cdp；captcha → errors 含 bing_cdp_captcha_suspected |
| TC-X1 | 全失败兜底 | 异常 | 🔴一票否决 | 极端无结果主题 → no_results 模板 + errors 如实 + App 不崩 |
| TC-D1 | 下游 PPT 链路 | 回归 | 中 | 研究 X 做成 PPT 仍通 |
| TC-C1 | C SearXNG | 环境受限 | 低 | 无 Docker → 标 env-limited + 给开启步骤 |
| TC-D2 | D serp_hardening | 环境受限 | 低 | 默认 off → 标 env-limited + 给开启步骤 |

> 执行顺序建议：**先 TC-A2（连续 4 主题，一次性产出 A1/A3/O1 的大部分证据）**，再补 TC-X1 / TC-B1 / TC-D1，最后标注 TC-C1/TC-D2。

---

## 3. 详细用例

---

### TC-A1 — A 直连源生效（综述类 → wikipedia + arxiv）⭐核心

**目的**：验证综述/背景类主题命中 `wikipedia`（`_WIKIPEDIA_KW` 含「综述/概述/背景」）+ `arxiv`（`_ACADEMIC_KW` 含「综述/research/survey」），报告引用出现这两个直连域名。

**前置**：§1 全部满足；config `[research]` 用代码默认（`direct_source_types` 注释 = 默认 `["cninfo","openstd","wikipedia","arxiv"]`，**无需改 config**）。

**主题**：`RAG 评估方法综述`（含「综述」→ 同时命中 wikipedia + arxiv + semantic_scholar，但 s2 默认 off 不返回）。

**精确步骤**：
1. `declare: 坐标=(桌宠输入框中心) | 动作=click | 期望=输入框聚焦光标闪烁`。SetCursorPos→SendInput 点击输入框。截图 `screenshots/TC-A1-01-focus.png`。
2. `declare: 动作=paste "帮我深度调研 RAG 评估方法综述" | 期望=文本落入输入框`。Clipboard.SetText 中文 → Ctrl+V。截图 `TC-A1-02-typed.png`。
3. `declare: 动作=key Enter | 期望=研究开始，桌宠进入「思考/检索」态`。截图 `TC-A1-03-running.png`。
4. **轮询等待**（每 ~15s 截一张），直到助手回复 + **ArtifactCard 出现**（可点开的报告卡片）。截图 `TC-A1-04-artifactcard.png`。
5. 点开 ArtifactCard（或按 §1.4 找最新 `.md`），读报告正文与引用区。截图 `TC-A1-05-report.png`。
6. grep 报告 `.md`：
   ```powershell
   $f = (Get-ChildItem "$env:APPDATA\deskpet\OutPut\Research" -Filter *.md | Sort LastWriteTime -Desc)[0].FullName
   Select-String -Path $f -Pattern "wikipedia\.org|arxiv\.org"
   ```
7. grep backend log 确认无直连源异常炸链：
   ```powershell
   Select-String -Path "...\tauri-dev.log" -Pattern "direct:wikipedia|direct:arxiv"
   ```

**预期**：
- ArtifactCard 渲染出报告；报告正文有 `[^n]` 引用 + 文末引用列表。
- 引用列表/正文里**至少出现一个 `wikipedia.org`（zh 或 en）链接**，且**理想情况出现 `arxiv.org`** 链接。
- backend log **没有** `direct:wikipedia` / `direct:arxiv` 异常行（有少量 `direct:` 异常但仍有其它直连源命中也可接受）。

**判定依据（PASS/FAIL）**：
- **PASS**：报告非全 0 来源，且引用中出现 `wikipedia.org`（arxiv.org 出现更佳；arxiv 偶发网络波动只命中 wikipedia 也算 PASS，但须在报告里附 log 说明）。
- **FAIL**：报告是 no_results 模板（「未能找到可用的来源」）/ 无 ArtifactCard / 引用里既无 wikipedia 也无 arxiv 也无任何直连域名。

**证据要求**：5 张截图 + report `.md` 路径 + 两条 grep 输出（含命中行 + timestamp）。

---

### TC-A2 — spike 病灶修复验证（连续 4 主题不再全 0）🔴最关键

**目的**：复现 spike 的「连续负载」场景，验证 **§6.0 后不再 11/13 全 0**。这是整个改造价值的硬证据。

**前置**：§1 全部满足；config 默认（直连源默认开）。**4 个主题连续跑、中间不重启 App**（模拟连续负载，让免费 SERP 有被封风险，凸显直连源兜底价值）。

**4 个主题（覆盖 4 类直连意图）**：
| 序号 | 主题 | 命中直连源（按 `direct_source_for`） | 期望直连域名 |
|---|---|---|---|
| ① 财报 | `宁德时代 2024 年财报营收和净利润` | cninfo（「财报/营收/净利润」）；A股不中→EDGAR 兜底 | cninfo.com.cn |
| ② 学术 | `扩散模型最新研究论文进展` | arxiv（「研究/论文」） | arxiv.org |
| ③ 综述 | `向量数据库技术综述与背景介绍` | wikipedia（「综述/背景/介绍」）+ arxiv | wikipedia.org / arxiv.org |
| ④ 技术选型 | `RAG 系统检索器技术选型对比` | arxiv（「技术选型」） | arxiv.org |

**精确步骤（每个主题重复）**：
1. `declare: 动作=click 输入框 | 期望=聚焦`。SetCursorPos→SendInput。
2. `declare: 动作=paste "帮我深度调研 <主题①…④>" → Enter | 期望=研究开始`。Clipboard 中文 → Ctrl+V → Enter。截图 `TC-A2-<n>-01-typed.png`。
3. 轮询等待 ArtifactCard / 回复。截图 `TC-A2-<n>-02-result.png`。
4. **等该主题结果完全出来后**再发下一个（连续、不重启）。
5. 4 个全跑完后，按 §1.4 列出最近 4 个 `.md`，逐个 grep 直连域名 + 统计「有源 vs 0 源」：
   ```powershell
   $dir="$env:APPDATA\deskpet\OutPut\Research"
   Get-ChildItem $dir -Filter *.md | Sort LastWriteTime -Desc | Select -First 4 | ForEach-Object {
     $hit = Select-String -Path $_.FullName -Pattern "wikipedia\.org|arxiv\.org|cninfo\.com\.cn|sec\.gov|openstd"
     "{0} :: 直连命中={1}" -f $_.Name, ($hit.Count)
   }
   ```
6. 对**没落盘 .md** 的主题（=全 0 来源未触发落盘），去 log 看是否走了 no_results：
   ```powershell
   Select-String -Path "...\tauri-dev.log" -Pattern "no search results|plan_fallback"
   ```

**预期**：
- **4 个主题中，至少 3 个非 0 来源**（直连源主题 ①②③④ 都应稳定有直连源；普通 SERP 即便被封，直连源仍兜底）。
- **绝不出现「4 个全 0」**（那等于 spike 病灶未修，直接判整个改造 FAIL）。
- 至少主题 ①（cninfo/edgar）、②③④（arxiv/wikipedia）各自的报告引用里出现对应直连域名。

**判定依据（PASS/FAIL）**：
- **PASS**：≥3/4 主题有源，且**没有出现全 0**；直连域名在对应主题报告中可见。
- **PARTIAL（需用户裁定）**：恰好 2/4 有源——记录是哪两个失败、贴 log（区分「直连源网络波动」vs「直连源根本没被调用」：后者要看 `direct_source_for` 是否返空，属代码问题）。
- **FAIL**：≤1/4 有源 / 出现全 0 / 直连源主题（如②arxiv）连续 0 源且 log 无 `direct:` 网络异常（说明直连根本没接上）。

**证据要求**：每主题 ≥2 截图（共 ≥8）+ 4 个 `.md` 路径 + 「有源/0源」统计输出 + 任何 0 源主题的 log 佐证。

> 📌 这是**对照 spike 11/13 失败的核心回归**。报告里必须明确写出「本轮 N/4 有源（spike 基线 2/13 有源）」的对比结论。

---

### TC-A3 — 财报 → cninfo 巨潮直连回归（高）

**目的**：验证财报类命中 `cninfo`（`_CNINFO_KW` 含「财报/年报/营收/净利润」），走巨潮 `hisAnnouncement/query` + topSearch 解析公司 + pypdf 抽年报正文；A股不命中才 fallback EDGAR。

**前置**：§1；config 默认。

**主题**：`宁德时代 2024 年年度报告主要财务数据`（「年度报告/财务」→ cninfo；`_cninfo_category` 命中年报类 → 摘要置顶 + 抽「主要会计数据」表）。

**精确步骤**：
1. `declare: 动作=click 输入框→paste 主题→Enter | 期望=研究开始`。Clipboard 中文 + Ctrl+V + Enter。截图 `TC-A3-01-typed.png`。
2. 轮询等 ArtifactCard（cninfo 要下 PDF + pypdf 抽取，**可能较慢，最多等 ~3min**）。截图 `TC-A3-02-result.png`。
3. 读最新报告 `.md`，grep：
   ```powershell
   $f=(Get-ChildItem "$env:APPDATA\deskpet\OutPut\Research" -Filter *.md|Sort LastWriteTime -Desc)[0].FullName
   Select-String -Path $f -Pattern "cninfo\.com\.cn|巨潮|static\.cninfo"
   ```
4. 看正文是否含营收/净利润等财务数字（pypdf 抽到「主要会计数据」表的体现）。截图 `TC-A3-03-report.png`。

**预期**：
- 报告引用含 `static.cninfo.com.cn/...pdf`（巨潮公告 PDF 链接），title 形如「宁德时代 …年度报告(摘要)」。
- 正文出现该公司财务数字（PDF 抽取成功）；即便 PDF 抽取失败，也应退化为元数据「（巨潮资讯公告，PDF：…）」仍带 cninfo 链接。

**判定依据**：
- **PASS**：引用出现 cninfo 域名（`cninfo.com.cn`）。
- **PASS（次优）**：cninfo 当次网络不通 → fallback 命中 EDGAR（`sec.gov`），引用出现 sec.gov + 美元财务数据 → 仍算直连源回归 PASS，但须注明「A股 cninfo 未通走 EDGAR 兜底」。
- **FAIL**：报告无 cninfo 也无 sec.gov，且 log 无 `direct:cninfo` 异常（说明 cninfo 根本没被路由——检查 `direct_source_for("...财报...")` 是否返 `["cninfo"]`）。

**证据要求**：3 截图 + `.md` 路径 + cninfo/edgar grep 输出。

---

### TC-A4 — 通用主题(无直连意图)默认配置覆盖率(诚实边界测·高)

**目的（评估补强）**：TC-A1/A2 的主题**都路由到直连源**（财报/学术/综述/技术选型），等于"挑了能过的题"。但 §6.0-A 只兜底**有直连意图**的主题；**通用主题（无 wikipedia/arxiv/cninfo/openstd 意图）默认仍只走 bing/ddg 裸 SERP**（因 bing-cdp 默认 opt-in 不在默认队列）。本例诚实暴露：**默认配置对通用主题是否仍可能 0 来源**（=spike 病灶对这类题是否仍在）。

**前置**：§1；**config 默认**（不开 bing-cdp）；**紧接 TC-A2 之后连续跑**（让 SERP 已有负载/可能已被封，最能暴露问题）。

**主题（刻意无直连意图）**：`直播带货高退货率的主要成因`（无「综述/背景/介绍/是什么」→ 不命中 wikipedia；无「论文/研究/技术选型」→ 不命中 arxiv；无「财报/年报/营收」→ 不命中 cninfo；无「国标/标准」→ 不命中 openstd。`direct_source_for` 应返 `[]` → 纯靠 SERP）。

**精确步骤**：
1. （先确认路由）grep 该主题 log 看 `direct_sources_hit` 是否空 / 有无 `direct:` 行——预期直连源**未命中**。
2. `declare: 动作=click→paste "帮我深度调研 直播带货高退货率的主要成因"→Enter | 期望=纯 SERP 路径`。Clipboard+Ctrl+V+Enter。截图 `TC-A4-01-typed.png`。
3. 轮询等结果。截图 `TC-A4-02-result.png`。
4. 看报告是否有源 / 是否 no_results；grep log：
   ```powershell
   Select-String -Path "...\tauri-dev.log" -Pattern "no search results|engines_hit|direct_sources_hit"
   ```

**预期 / 判定（这是诚实边界，不是实现 PASS/FAIL）**：
- **结果 1（SERP 当次没被封 → 有源）**：记 ✅「通用主题本次有源（SERP 未被封）」，但**注明**：这不代表 SERP 不会被封，只是本次没触发。
- **结果 2（SERP 被封 → 0 源 no_results）**：记 ⚠️「**已知边界暴露**：通用主题默认配置下 SERP 被封即 0 源，§6.0-A 直连兜底不覆盖此类题」。**这不算 §6.0 实现 FAIL**（实现按 plan：bing-cdp 默认 opt-in），但**是产品决策点** → 需用户裁决「bing-cdp 是否应进默认队列」或「通用主题也加宽直连意图」。
- **加测（验证 B 能补这个洞）**：若结果 2 发生，**临时开 bing-cdp**（同 TC-B1 改 config）复跑同一主题 → 若这次有源（engines_hit 含 bing-cdp）→ 证明 **B 正是这个边界的解**，强化"bing-cdp 默认开"的决策依据。测后还原 config。

**证据要求**：路由 grep（证直连未命中）+ 2 截图 + 有源/0源 log + （若触发加测）开 bing-cdp 前后对比。

> 📌 这条是整套测试里**唯一可能暴露"默认修复不彻底"**的用例——TC-A2 证明直连源类题已修，TC-A4 诚实回答"通用类题默认是否还脆弱"。两者合起来才是完整的 spike 病灶覆盖评估。

---

### TC-O1 — 观测字段（route / 阶段耗时 / 丢弃原因）（高）

**目的**：验证 §6.0.2 观测落地——`coverage.route.direct_sources_hit` 有值、`coverage.elapsed_ms_per_stage.direct` 存在、`coverage.n_dropped_by_reason.direct_source_empty` 存在。

**关键事实**：`coverage` 是 **deepresearch 工具返回 JSON 的字段**（`research_tools.py:_observability_coverage` → `report.as_dict()` → tool result）。它**不一定单独打成一行 stderr 日志**，所以观测证据走两条路：
- **路径 A（首选）**：在 backend log 里 grep 工具返回 payload（tool result JSON 会被 agent loop 记录/回传），找 `direct_sources_hit` / `elapsed_ms_per_stage` / `direct_source_empty` 字样。
- **路径 B（间接但确凿）**：直连源被调用的**行为证据**——报告引用出现直连域名（=`direct_sources_hit` 非空的等价外显）；以及 log 中若出现 `direct:<src>:` 异常行=直连阶段确实执行了。

**前置**：复用 **TC-A1 或 TC-A2③**（综述主题，直连源命中最多）刚跑完的那一轮，不必重新触发。

**精确步骤**：
1. grep backend log 找观测字段：
   ```powershell
   Select-String -Path "...\tauri-dev.log" -Pattern "direct_sources_hit|elapsed_ms_per_stage|direct_source_empty|engines_hit"
   ```
2. 若 ArtifactCard / 工具结果面板能展开原始 JSON，截图 coverage 区块。截图 `TC-O1-01-coverage.png`。
3. 对照该轮报告引用（直连域名出现 = direct_sources_hit 有值的外显）。截图 `TC-O1-02-report-citations.png`。

**预期**：
- log / 工具结果 JSON 中出现 `route` 含 `direct_sources_hit`（如 `["wikipedia","arxiv"]`）、`engines_hit`（如 `["bing"]`）。
- 含 `elapsed_ms_per_stage` 且其中有 `direct` 键（值为整数毫秒，直连阶段执行过则 >0）。
- 含 `n_dropped_by_reason` 且其中有 `direct_source_empty` 键（值为整数；某直连源整源空时 +1）。

**判定依据**：
- **PASS**：上述三组字段名在 log/JSON 中可见，**或**（路径 B）报告引用确有直连域名 + log 有直连阶段执行痕迹，二者满足其一即 PASS（首选路径 A）。
- **FAIL**：既 grep 不到任何观测字段名，报告也无任何直连域名（说明直连阶段没跑或观测没接）。

**证据要求**：grep 输出（含字段名 + timestamp）+ 1–2 截图。

> 若工具结果 JSON 在 UI/log 中完全不可见而只能靠路径 B：**显式声明走了路径 B**，并说明这是「行为等价证据」（直连域名出现 ⟺ direct_sources_hit 非空），不是绕过。

---

### TC-B1 — B bing-cdp（config 启用后）（中）

**目的**：验证启用 `bing-cdp` 引擎后，连续研究 `engines_hit` 含 `bing-cdp`；若吃软封 → `report.errors` 含 `bing_cdp_captcha_suspected`（**与「真无结果」区分**）。

**前置改 config**（`backend/userdata/config.toml` 第 197 行已登记注释项，取消注释）：
1. 用 **Edit/Write 工具**（**勿用 PowerShell 改中文 config**，会 mojibake）把第 197 行
   `# search_engines = ["bing-cdp", "bing", "duckduckgo"]`
   改为
   `search_engines = ["bing-cdp", "bing", "duckduckgo"]`
2. **重启 App**（按 §1.1；先 `taskkill /F /IM deskpet.exe` + 杀 vite，再重启注 env），重新过 §1.2 Dev-python Gate。截图 `TC-B1-00-config.png`（改后的 config 行）。
3. 确认系统有 **Edge**（bing-cdp 用系统 Edge 无头 CDP 渲染）。

**精确步骤**：
1. 连续触发 **2–3 个**主题（如 `量子计算最新进展` / `大语言模型对齐方法`，让 SERP 有负载、可能触发 bing-cdp 渲染路径）。每个：click→paste→Enter→轮询。截图 `TC-B1-0n-result.png`。
2. grep log：
   ```powershell
   Select-String -Path "...\tauri-dev.log" -Pattern "engines_hit|bing-cdp|bing_cdp_captcha_suspected"
   ```

**预期 / 判定（分支）**：
- **分支 1（bing-cdp 命中）PASS**：log/coverage `engines_hit` 含 `bing-cdp`，报告有 SERP 来源 + 直连源。
- **分支 2（吃 captcha 软封）PASS**：log 出现 `bing_cdp_captcha_suspected`（`search_provider.py:607` warning），且 **`report.errors` 含该串**（`research_tools.py:1119` 把 search 侧错误 append 进 errors），**App 不崩、仍靠直连源/其它引擎出报告**。这正是「软封被如实记录、不与真无结果混淆」的正确行为。
- **FAIL**：改了 config 但 `engines_hit` 从不含 bing-cdp 且 log 无任何 bing-cdp 痕迹（说明 bing-cdp 引擎没被纳入队列——检查 config 是否生效/Edge 是否可用）；或 captcha 发生却**没**记 `bing_cdp_captcha_suspected`（软封被吞）。

**证据要求**：config 改动截图 + 2–3 结果截图 + bing-cdp/captcha grep 输出。

> **测后还原**：把第 197 行改回注释态（`# search_engines = ...`），重启确认恢复默认队列。截图 `TC-B1-99-revert.png`。

---

### TC-X1 — 全失败兜底（🔴一票否决）

**目的**：极端无结果主题下，仍出 `no_results` 模板 + `errors` 如实，**App 不崩、不白屏、不卡死**。

**关键事实**：全 0 来源时 `research_tools.py:1125` 走 `_no_results_template`（正文「未能找到可用的来源（搜索失败或抓取失败）。已尝试以下子问题：…」），且**因无 citations 不落盘、不出 ArtifactCard**（`:1760`）；`errors` 含 `"no search results"`。

**前置**：§1；config 默认。

**主题（构造无结果）**：`zxqwlkjhgfdsa9988 不存在的乱码主题没有任何来源`（纯乱码，SERP 0 命中；`direct_source_for` 返 `[]` 不触发直连）。

**精确步骤**：
1. `declare: 动作=click→paste 乱码主题→Enter | 期望=研究跑完后给「找不到来源」类回复`。Clipboard + Ctrl+V + Enter。截图 `TC-X1-01-typed.png`。
2. 轮询等回复。截图 `TC-X1-02-result.png`。
3. 观察：助手是否给出「未能找到可用的来源」类文案？**桌宠是否仍可正常交互**（再点一下、再问一句普通话能回）？截图 `TC-X1-03-still-alive.png`。
4. grep log：
   ```powershell
   Select-String -Path "...\tauri-dev.log" -Pattern "no search results|未能找到可用的来源|Traceback|Unhandled|panic"
   ```
5. 确认 `OutPut\Research` **没有**为该乱码主题新增 `.md`（无 citations 不落盘）。

**预期**：
- 助手回复体现 no_results 模板语义（「未能找到可用的来源」/「请稍后重试」）。
- log 含 `no search results`（或 `plan_fallback`），**无** `Traceback`/`panic`/`Unhandled`。
- App 仍能继续对话（不崩、不白屏）。
- 该主题无新 `.md` 落盘（符合「无 citations 不落盘」契约）。

**判定依据（一票否决）**：
- **PASS**：no_results 文案出现 + App 存活 + errors 如实（log 有 `no search results`）+ 无崩溃栈。
- **FAIL（一票否决整改造）**：App 崩溃/白屏/卡死 / 抛未捕获异常 / 给出**编造的**来源（无中生有引用）。

**证据要求**：3 截图（含「仍能交互」证明）+ 崩溃/no_results grep 输出 + 「无新 .md」确认。

---

### TC-D1 — 下游 PPT 链路不破（回归·中）

**目的**：验证 §6.0 改动后，「研究 → 做成 PPT」的链路仍通（research 产物能喂给 `ppt_create`）。

**前置**：§1；config 默认；relay/LLM 链路可用（PPT 需 LLM 生成大纲）。

**主题**：`帮我深度调研 钠离子电池技术现状并做成 PPT`（含「做成 PPT」→ 研究后接 ppt_create）。

**精确步骤**：
1. `declare: 动作=click→paste 主题→Enter | 期望=先研究后生成 .pptx`。Clipboard + Ctrl+V + Enter。截图 `TC-D1-01-typed.png`。
2. 轮询等待（研究 + PPT 生成，**可能 3–5min**）。截图 `TC-D1-02-running.png`。
3. 等出现 **PPT ArtifactCard**（.pptx 文件卡片）。截图 `TC-D1-03-pptcard.png`。
4. 确认 `OutPut\PPT\` 下有新 `.pptx`：
   ```powershell
   Get-ChildItem "$env:APPDATA\deskpet\OutPut\PPT" -Filter *.pptx | Sort LastWriteTime -Desc | Select -First 1
   ```
5. （可选）双击打开 .pptx 确认非空、有研究内容。截图 `TC-D1-04-pptx-open.png`。

**预期 / 判定**：
- **PASS**：生成新 `.pptx` + PPT ArtifactCard 出现 + 内容来自该研究主题。
- **FAIL**：研究环节炸链导致 PPT 没生成 / .pptx 损坏打不开 / 链路报错。

**证据要求**：3–4 截图 + .pptx 路径。

---

### TC-C1 — C SearXNG（环境受限·低）

**目的**：登记 SearXNG 路线；真机若无 Docker SearXNG 实例则**诚实标 env-limited**，并给出「如何开启验证」步骤。

**代码事实**：`search_provider.py:_searxng_url()` 读 `[research].searxng_url`；为空则即便队列含 `searxng` 也被剔除（`:121`）。

**如何开启验证（供有环境者）**：
1. Docker 跑 SearXNG：`docker run -d -p 8888:8080 searxng/searxng`（需 SearXNG 开启 JSON 输出格式）。
2. Edit config：
   `searxng_url = "http://127.0.0.1:8888/search"`，
   `search_engines = ["searxng", "bing", "duckduckgo"]`。
3. 重启 App（过 §1.2 Gate）。
4. 触发任一研究，grep log `engines_hit` 是否含 `searxng`、报告是否有 SearXNG 来源。

**默认判定**：
- 无 Docker/SearXNG 实例 → **SKIP（env-limited）**：显式声明「本机无 SearXNG 自托管实例，按代码 `searxng_url` 为空时该引擎被自动剔除」，附上面开启步骤，**等用户确认**是否需要补环境。
- 有环境 → 按上面步骤跑，PASS = `engines_hit` 含 searxng。

**证据要求**：声明 + （若 skip）config `searxng_url` 为空的截图。

---

### TC-D2 — D serp_hardening（环境受限·低）

**目的**：登记 SERP 抓取硬化；默认 off（旧行为），标 env-limited + 给开启步骤。

**代码事实**：`search_provider.py:_serp_hardening()` 读 `[research].serp_hardening`（默认 False）。开启后启用：轮换 UA（`_headers_for_request`）、失败冷却（2 次失败 → 5min cooldown）、结果缓存（2min TTL）、退避重试（2 次）。

**如何开启验证**：
1. Edit config：`serp_hardening = true`。
2. 重启 App（过 §1.2 Gate）。
3. 连续对**同一主题**触发 2 次研究——第 2 次应命中**结果缓存**（更快返回、相同来源）；可对比两次耗时（`elapsed_ms_per_stage.search`）。
4. （可选）观察某引擎连续失败后是否进 cooldown（log `cooling down`）。

**默认判定**：
- 默认 off → **SKIP（按设计默认关）**：声明「serp_hardening 出厂默认 off，本轮按默认验证旧行为路径」，附开启步骤，等用户确认是否需开启专测。
- 若开启专测 → PASS = 第二次同主题更快/命中缓存 或 log 出现 cooling-down 行为。

**证据要求**：声明 +（若测）两次耗时对比 / cooldown grep。

---

## 4. 汇总报告格式（每个 case 必填）

```
case:     TC-XX
主题:     "<研究主题>"
坐标:     (x, y) [物理像素] — 关键动作点
动作:     click 输入框 → Clipboard "<中文主题>" → Ctrl+V → Enter
截图:     screenshots/TC-XX-*.png
report:   <OutPut\Research\xxx-时间戳.md 绝对路径>（或：无落盘=全0来源/兜底）
log 证据: <grep 命中行，含 timestamp 与关键字 direct:/engines_hit/cninfo/no search results 等>
判定:     PASS / FAIL / PARTIAL / RETRY-N / SKIP(env-limited，带理由+等确认)
```

### 4.1 全局收敛判定（整改造 GO / NO-GO）

| 条件 | 要求 |
|---|---|
| 环境 Gate | §1.2 Dev python ✅（否则全轮作废） |
| 🔴 TC-A2 | ≥3/4 主题有源，**绝无全 0**（对照 spike 2/13）→ 这是 GO 的硬门 |
| ⭐ TC-A1 | 综述报告引用出现 wikipedia.org（arxiv 更佳） |
| 高 TC-A4 | 通用主题默认覆盖率诚实记录(有源/0源)；若 0 源暴露 bing-cdp 默认开决策点 |
| 🔴 TC-X1 | 全失败兜底 App 不崩 + no_results 如实 + 不编造来源（一票否决） |
| 高 TC-A3 / TC-O1 | cninfo（或 EDGAR 兜底）回归 + 观测字段可见 |
| 中 TC-B1 / TC-D1 | bing-cdp 命中或 captcha 如实；PPT 链路通 |
| 低 TC-C1 / TC-D2 | 诚实标 env-limited + 给开启步骤 |

> **NO-GO 触发**：TC-A2 出现全 0（病灶未修）/ TC-X1 崩溃或编造来源 / 环境 Gate 跑的是 Bundled exe。任一即整改造判 NO-GO。

### 4.2 测试纪律自检（提交报告前逐条勾）

- [ ] 每个动作前都 declare 了坐标/动作/期望
- [ ] 中文输入走剪贴板（非 SendKeys），点击走 SendInput（非 mouse_event）
- [ ] 每个 case 截图齐全、存到 `plans/manual-results-2026-06-20-deepresearch-6.0/screenshots/`
- [ ] 判定依据来自报告 `.md` 内容 + log grep（**不是** pytest / WebSocket 直连 / import 查内部状态）
- [ ] SKIP 的 case 都给了具体环境受限理由 + 等用户确认
- [ ] TC-B1 测后已把 config 还原为注释态
- [ ] 报告里写明了「本轮 N/4 有源 vs spike 基线」的对照结论
```
