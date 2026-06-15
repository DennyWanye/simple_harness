# Crawl4AI JS 渲染抓取层 — 实施 PLAN

> **状态**: 📋 规划中（已过两轮评审迭代，详见文末「评审迭代记录」）
> **目标**: 给 deep-research 抓取链路加一级 **Crawl4AI 真浏览器渲染**兜底，治 trafilatura/Jina
> 都拿不下的 JS/SPA/强反爬站，输出 LLM 友好的干净 Markdown。
> **仓库**: https://github.com/unclecode/crawl4ai （pip `crawl4ai`，Python 3.10+，底层 Playwright）
> **最后更新**: 2026-06-16

> ⚠️ **评审关键前置（务必先读）**：本仓库**已存在** `backend/deskpet/tools/browser_use_tool.py`
> —— 一套 browser-use + Playwright + Chromium 的浏览器自动化工具，已经把 Crawl4AI 要面对的
> 几乎所有难题（**懒 import / flag-off 零开销 / 后台线程 + `asyncio.run` 避开 backend 事件循环 /
> 首次使用才下 Chromium / 长任务 job 化**）解过一遍。**本 plan 不应另起炉灶，必须复用它的成熟模式**。
> 同时本仓库已有 `model_provisioner.py`（首启下载基建），但它**走自建 COS 直下、明确放弃了
> hf-mirror 等第三方镜像**（实测不稳）—— 这直接推翻本 plan 早期"PLAYWRIGHT_DOWNLOAD_HOST 指
> 国内镜像"的乐观假设。详见文末评审记录与下方各章已就地修订处。

---

## 1. 背景与目标

deep-research 现有抓取链路（`research_tools.default_extract`）是**两级降级**：

```
httpx 直抓 HTML → trafilatura 抽正文(SOTA,但只读静态 HTML)
   ↓ 正文 < 300 字(疑似 JS 空壳) 且 [research].jina_reader=on
r.jina.ai 二级兜底(JS 渲染,但国外服务,中国大陆需代理)
```

**缺口**：现代 SPA / JS 重渲染站，原始 HTML 是空壳，trafilatura 抽不到；Jina 是国外站，
裸中国用户连不上。→ 这类站在调研里**直接丢源**，报告退化（真机查特斯拉时 9 源含多个空壳/垃圾页）。

**本 plan 目标**：引入 **Crawl4AI** 作为**本地真浏览器渲染**抓取层 —— 用 Playwright 跑完页面 JS
再抽正文，中国可用（本地渲染不依赖国外服务），输出干净 Markdown 直接喂 synth。

---

## 2. 核心约束与关键决策（先定，否则方案跑偏）

| 约束 | 决策 |
|---|---|
| **体积**：Crawl4AI 底层 Playwright + Chromium ≈ **100~160MB**，与 NSIS 瘦包定位冲突 | **绝不进默认安装包**，且**不进 PyInstaller spec 的 `hiddenimports`/`collect_all`**（见下 ★frozen 约束）。复用 [`nsis-model-externalization`](../2026-06-05-nsis-model-externalization/PLAN.md) + `model_provisioner.py` 的「首启外置下载」模式：基础包不含 Chromium，用户**显式开启**后首次使用时下载 |
| **★ frozen backend 不可 import（一轮新增 — 硬约束）**：生产 backend 是 PyInstaller 冻结 exe（`backend/deskpet-backend.spec`），**没有 site-packages**，未列入 `hiddenimports` 的包**根本 import 不到**。spec 现在**没有** `crawl4ai`/`playwright`/`browser_use`。 | **接受并写明**：crawl4ai 在**冻结生产包里直接不可用**（`crawl4ai_available()` 永远 False → 自动降级）。它**只在 dev / 源码运行模式**（`DESKPET_BACKEND_DIR` 指向 worktree、跑 `.venv` python）下可用。**若要让正式用户用上**，需另起一项工作：把 crawl4ai 作为**外置 wheel + 运行时 `pip install` 到 user 目录并 `sys.path` 注入**（类似 model 外置，但对象是 Python 包不是数据文件），**本期不做，列为 follow-up（见 §10）**。WI-0 必须实测确认冻结包行为。 |
| **中国可达**：Playwright 默认从 `playwright.azureedge.net` 下 Chromium，中国慢/不稳 | ⚠️ **不要轻信第三方镜像**：本仓 `model_provisioner.py` 明确记录"hf-mirror 等第三方镜像实测不稳，已改自建 COS 直下"。**首选方案 = 复用系统已装 Chrome/Edge（CDP / channel），免下载**；下载 Chromium 仅作兜底，且 `PLAYWRIGHT_DOWNLOAD_HOST` 国内镜像可用性**列为 WI-0 必须验证项**，不可用则**走自建 COS 托管 Chromium 包**（对齐既有模型基建），不靠不可控第三方。 |
| **opt-in**：默认关，避免普通用户被几百 MB 吓到 | `[research].crawl4ai = false`（出厂默认）。开关 + 首启下载双门。注意：出厂 `config.toml` 当前**没有 `[research]` 段**，真实默认来自代码 `_research_raw().get("crawl4ai", False)`；config.toml 里写注释是给用户看的文档，**不是**默认值来源。 |
| **降级**：浏览器没装好 / 渲染失败 / 超时 | best-effort：任何失败 → 回落现有 trafilatura/Jina 结果，绝不让抓取整体崩。对齐 `research_sources.py`「失败返空、绝不抛」先例。 |
| **不阻塞 + 超时预算（一轮强化）**：浏览器渲染慢（秒级） | 仅在 trafilatura 抽空/过短时**才触发**（和 Jina 同条件）；独立超时。**★ 关键风险**：`research_run` 工具上限已是 **300s 且 deep 档慢网区实测逼近**（commit `ee38fc4`，TC-P2-03 曾 180s 超时丢源）；`default_extract` 对所有候选 URL **并发**执行 → crawl4ai 会**同时起多个浏览器页**。必须设 **全局并发上限（信号量）** + **单次 research 内 crawl4ai 触发次数上限**，否则 deep 档叠加 crawl4ai 会直接顶穿 300s（见 R4 已升级为必决项）。 |
| **★ 浏览器实例模型（一轮：R4 从"待定"升为必决）** | crawl4ai `AsyncWebCrawler` 起浏览器开销大。决策：**进程内单例 crawler + 复用 browser context，页级并发用 `asyncio.Semaphore(≤2)` 限流**；不每 URL 新建（太慢），也不无上限并发（OOM/顶穿超时）。生命周期挂在适配器模块级，懒启动、随进程退出。 |

> **本质**：把 Crawl4AI 做成"**可选的、外置的、best-effort 的、且当前仅 dev 可用**的第三级抓取兜底"，
> 而不是默认链路。这样既拿到它的 JS 渲染能力，又不违背"单机桌宠 + 瘦包 + 中国用户"定位。
> **不要假装它在正式冻结包里能跑** —— 这是本 plan 第一轮评审揪出的最大认知偏差。

---

## 3. 架构与接入点

### 3.1 抓取链路（升级后，三级降级）

```
httpx 直抓 → trafilatura(主)
   ↓ 正文 < _JINA_MIN_CHARS(300)
[research].crawl4ai=on 且浏览器就绪 → Crawl4AI 渲染抽 Markdown   ← 本 plan 新增
   ↓ 仍不行 / crawl4ai 关 / 浏览器没装
[research].jina_reader=on → Jina Reader(国外,代理)
   ↓
返回现有最好结果(哪怕短)
```

> **次序决策**：Crawl4AI（本地、中国可用）排在 Jina（国外、需代理）**之前** —— 中国用户优先用能直连的。

### 3.2 代码接入点（最小侵入）

> **强制复用范本**：新建 `research_crawl4ai.py` 时，**对照 `browser_use_tool.py` 抄它的成熟约定** ——
> 懒 import（flag-off 进程零开销）、`_is_enabled()` 走配置、库/浏览器缺失给清晰 hint 不抛、
> 失败 best-effort。它甚至把"长任务用后台线程 + `asyncio.run` 避开 backend 主事件循环"都示范了。

| 文件 | 改动 |
|---|---|
| `backend/deskpet/tools/research_crawl4ai.py`（新） | `async crawl4ai_extract(url, *, timeout) -> Optional[dict]`：封装**进程内单例** `AsyncWebCrawler`（见 §2 R4 决策）的 `arun`，取 `result.markdown`/`result.cleaned_html`；用模块级 `asyncio.Semaphore(2)` 限并发；best-effort 返 None。库/浏览器缺失时**静默不可用**。**懒 import crawl4ai**（顶层不 import，否则 flag-off 也付出 import 代价 + 在冻结包里直接 ImportError 噪音）。 |
| `research_tools.py` `default_extract` | trafilatura 短 → 若 `_crawl4ai_enabled()` 且 `crawl4ai_available()`，调 `crawl4ai_extract`；取更长正文，`extractor="crawl4ai"`。**次序**：crawl4ai 在 jina **之前**（见 §3.1）。 |
| `research_tools.py` | 加 `_crawl4ai_enabled()`（走 `_research_raw().get("crawl4ai", False)`，**禁止** `_cfg.config.raw`，对齐 commit `2b7baa1`）+ 复用适配器里的 `crawl4ai_available()`（探 import + 浏览器就绪，**进程缓存**，避免每 URL 重探）。 |
| **★ asyncio 事件循环（一轮新增）** | `default_extract` 是被 backend **运行中的事件循环** await 的（`research_run` → `_gather_safe`）。crawl4ai 的 `AsyncWebCrawler.arun` 是协程，**可以直接 `await`**，**不需要**像 `browser_use_tool.py` 那样开线程 + `asyncio.run`（那是为"从同步 tool handler 起长任务"而设）。WI-0 必须实测确认：crawl4ai 内部不会 `asyncio.run()`/新建并关闭 loop（那会破坏宿主 loop）。若它真这么干 → 退回"后台线程 + 独立 loop"模式（抄 browser_use_tool）。 |
| `model_provisioner.py`（**复用现有，不新建目录**） | 首启/首次开启时下载 Chromium。**注意 plan 早期写的 `provisioning/` 目录不存在**，真实模块是 `backend/deskpet/model_provisioner.py`，下载走自建 COS（非镜像）。Chromium 包托管到 COS（对齐 `DEFAULT_CDN_BASE` 布局），进度复用 `ProvisionStatus` + `model_provision_status` control-WS 事件，前端复用首启进度卡片。 |
| `config.toml` `[research]` | 加 `crawl4ai = false` 注释行（**文档作用**，真实默认在代码）；可选 `crawl4ai_browser`（system/chromium）、`crawl4ai_timeout`。 |
| `pyproject`/依赖 | `crawl4ai` 列为**可选 extra**（`pip install deskpet[crawl4ai]`），不进基础依赖，**不进 spec hiddenimports**（保持冻结包不变 → 满足 DoD 字节级 flag-off）。 |

---

## 4. 分阶段任务（WI）

### WI-(-1) WebView 渲染 POC GATE（二轮新增）→ ✅ **已跑，PASS（2026-06-16）**

**实测结论（证据 `.tmp/webview_poc_cdp.py`，系统 Edge 149 = WebView2 引擎，经 CDP 渲染）**：

| 站点 | 原始 httpx 正文 | 系统引擎渲染后正文 | 结果 |
|---|---|---|---|
| quotes.toscrape.com/**js/**（JS 金标准） | 29 字（空壳） | **1071 字** | ✅ 救回 +1042 |
| quotes.toscrape.com/（静态对照） | 1161 字 | 1161 字 | ≈ 不变（不破坏静态站） |
| book.douban.com/latest（**真实中文 JS 站**） | 44 字（空壳） | **1244 字**（豆瓣书单真内容） | ✅ 救回 +1200 |

→ **"复用系统浏览器引擎渲染 JS、零下载"路线成立**，中文站直连有效，不伤静态站。

> **★ POC 暴露出一条比 Tauri-WebView-eval 和 Crawl4AI 都更优的实现路径 —— CDP-连系统-Edge**：
> POC 用的不是 Tauri 内嵌 webview，而是**直接启动系统已装 Edge 无头 + CDP（`websockets` 库，venv 已有）**
> `Runtime.evaluate` 取渲染后 `outerHTML`。这条路同时打赢三家：
> - **vs Crawl4AI**：不下 100MB Chromium（用系统 Edge）；**且只依赖 `websockets`(纯 Python，可进 spec hiddenimports) → 正式冻结包也能用**，没有"dev-only"硬伤
> - **vs Tauri-WebView-eval**：**不用动 Rust / 不用重编译**，纯 Python 后端适配器，好建好测
> - 中国友好：本地 Edge 直连，无国外服务
>
> **代价/待解**：① 依赖系统装了 Edge（Win10/11 必装 ✓；Mac/Linux 需另议）② 当前 POC 每页冷启 10-17s（含每次新起无头进程 + 固定 4s 等待）→ 实现时必须**单例常驻无头浏览器 + 复用、调优等待**，把单页压到 2-4s。

**判定回写用户**：GATE PASS。建议把"**系统引擎渲染**"做成抓取首选兜底档，**优先用 CDP-系统-Edge 实现**（零体积 + 正式包可用 + 不动 Rust）；Crawl4AI 与 Tauri-WebView-eval 都降为备选。**等用户拍板后再开实现。**

### WI-0 决策 spike + 可行性 GATE（先做，**未通过不得进 WI-1**）
> 一轮升级：从"1 步 spike"升为**硬 GATE**。下列每条都要有**实测证据**（命令输出/截图/日志），
> 任一不通过 → 暂停本 plan、回写结论、等用户决策（可能转向 §10 的 WebView POC）。

1. **JS 渲染能力**：dev `.venv` 里 `pip install crawl4ai` + `crawl4ai-setup`，跑 ≥2 个已知 **JS/SPA 真实站**（候选：`https://spa.smashing.work/`、某 React 渲染新闻站、特斯拉中国官网产品页之类原 HTML 空壳的），确认 `result.markdown` 拿到正文且明显比 trafilatura 多。
2. **★ frozen 包行为**：在**当前冻结的 `deskpet-backend.exe`** 里实测 `import crawl4ai` → 预期 ImportError（证实"冻结包不可用"约束）。据此确认本期范围 = **仅 dev 可用**，并把"外置 wheel 让正式包可用"明确推到 §10 follow-up。
3. **★ asyncio 共存**：写最小脚本，在一个**已运行的事件循环**里 `await AsyncWebCrawler().arun(...)`，确认 crawl4ai **不**内部 `asyncio.run()`/关闭宿主 loop。若冲突 → 记录，改走"后台线程 + 独立 loop"（抄 `browser_use_tool.py`）。
4. **浏览器获取路径**（按优先级实测命中率）：
   - (a) **复用系统 Chrome/Edge**（`BrowserConfig(channel="chrome"/"msedge")` 或 CDP attach）能否**免下载** 直接渲染；
   - (b) 下载 Chromium：量**实际体积 + 首装耗时**；实测 `PLAYWRIGHT_DOWNLOAD_HOST` 指国内镜像是否真能下下来（**带着"`model_provisioner` 已因镜像不稳改自建 COS"的怀疑去测**）。
5. **超时预算**：量单 URL crawl4ai 渲染 P50/P90 耗时（冷启 + 热复用各一组）→ 反推 deep 档（最多 16 URL）叠加后总耗时，验证 §2 的并发上限是否够把总时间压在 300s 内。
- **产出**：可行性结论 + 选定浏览器来源（system 优先/COS 兜底）+ 确认 asyncio 模式 + 超时预算表 + 「本期 dev-only」范围确认。

### WI-1 适配器 `research_crawl4ai.py`
- **进程内单例** `AsyncWebCrawler`（懒启动，模块级持有，进程退出时 best-effort 关闭）。
- `crawl4ai_extract(url, *, timeout=20)`：`async with _SEM:`（模块级 `asyncio.Semaphore(2)` 限页并发）→ `arun(url, config=...)` → 返 `{text, title, markdown}`。
- 库未装 / 浏览器缺失 / 超时 / 异常 → 返 None（best-effort，不抛；对齐 `research_sources.py`）。
- `crawl4ai_available() -> bool`：探 import + 浏览器就绪，结果**进程缓存**（避免每次重探）。**懒 import**（顶层不 import crawl4ai）。
- **冻结包内**：`import crawl4ai` 失败 → `crawl4ai_available()` 缓存 False + **一次性** debug 日志（不刷屏）。

### WI-2 provisioning（外置下载，复用 `model_provisioner.py`）
- **不新建 `provisioning/` 目录**（plan 早期笔误，该目录不存在）。在 `model_provisioner.py` 体系里加一个"chromium"条目或同构的小 provisioner：`[research].crawl4ai=on` 且浏览器未就绪 → 触发下载。
- **下载源优先级**（一轮修正）：① 复用系统 Chrome/Edge（免下载，首选）；② 自建 COS 托管的 Chromium 包（对齐 `DEFAULT_CDN_BASE` + `manifest.json` 布局，可控）；③ `PLAYWRIGHT_DOWNLOAD_HOST` 第三方镜像（仅当 WI-0 实测稳定才用，否则**不列入**）。
- 进度复用 `ProvisionStatus` state 机 + `model_provision_status` control-WS 事件 + 前端首启进度卡片。
- 下载失败 → `crawl4ai_available()=False` → 自动降级，不卡。

### WI-3 pipeline 接入 `default_extract`
- trafilatura 短（< `_JINA_MIN_CHARS`，现值 300）→ 先试 crawl4ai（若 on+ready），再试 jina。
- **取更长正文**（与 jina 同逻辑：`len(new) > len(text)` 才替换），标 `extractor="crawl4ai"`。
- **★ 触发去重（二轮：R5/R6 落地）**：crawl4ai 命中并替换后，**不再**触发 jina（避免一个空壳站连跑两个重型兜底，超时翻倍）。即：`if 短 and crawl4ai: 试 crawl4ai; if 仍短 and jina_enabled: 试 jina`。
- **★ 误触发收敛（二轮：R5 落地）**：`<300字` 阈值会让"正文本就短的正常静态站"也走重渲染。增设两道闸：① **仅当 trafilatura 抽到 `< _JINA_MIN_CHARS` 且原始 HTML 体积足够大（如 `>20KB`，暗示是被 JS 藏起来的富页面而非真·短页）才触发** crawl4ai；② **单次 research 内 crawl4ai 触发计数上限**（如 ≤4 次/research），超了就只走 jina/原结果，护住 300s 预算。
- 保留 `ai_generated`/`mojibake` 过滤：crawl4ai 路径下 `is_ai_generated` 要扫**渲染后的 `result.cleaned_html`/html**（trafilatura 路径扫的是原始 HTML，两者锚点不同，测试要分别覆盖）。

### WI-4 config + 开关
- `_crawl4ai_enabled()` 走统一 `_research_raw()`（注意：本仓刚修过 `[research]` 开关读取 bug，commit `2b7baa1`，务必走 `_research_raw()` 不要再写 `_cfg.config.raw`；真实默认在代码 `False`）。
- 出厂 `config.toml` 加 `crawl4ai = false` + 注释说明体积/首启下载（文档作用，见 §2）。

### WI-5 测试（二轮强化：可验证 + 可证伪）
- **单测（mock，不联网）**：
  - `crawl4ai_extract`：注入一个 **fake AsyncWebCrawler**（`arun` 返回带 `.markdown` 的对象）→ 验返 `{text,title,markdown}`；`arun` 抛/超时 → 返 None 不抛；`import crawl4ai` patch 成 ImportError → `crawl4ai_available()=False` 且静默。**mock 方式**：`monkeypatch` 适配器模块内的懒 import 符号（或注入一个 `_crawler_factory`），**不要**真起浏览器。
  - `default_extract` 接入：注入 `extract` 不行（它是被测对象）→ 用 `respx`/mock httpx 让 trafilatura 抽到 `<300字` + 大 HTML，patch `crawl4ai_extract` 返长文 → 断言 `extractor=="crawl4ai"` 且 jina **未被调用**（触发去重）；patch crawl4ai 返 None → 断言回落到 jina 或原结果。
  - 误触发闸：trafilatura 抽到短文 + **小** HTML（<20KB）→ 断言 crawl4ai **不**被调用。
  - 触发计数上限：构造一次 research 多个空壳 URL → 断言 crawl4ai 调用次数 ≤ 上限。
- **live smoke**（`@pytest.mark.live`，不进默认 CI；需 dev `.venv` + 浏览器就绪）：选**确定性 JS 站**真跑 —— 首选 `https://spa.smashing.work/`（公开 SPA demo，原 HTML 空壳，渲染后有正文，稳定）或 WI-0 验证过的站；断言 `result.markdown` 长度 > trafilatura，**避免选会改版/被墙/需登录的站**。
- **★ 字节级 flag-off 基线（二轮：DoD#1 可验证化）**：写 `tests/test_crawl4ai_flag_off.py` —— flag-off（默认）下对同一 mock URL 跑 `default_extract`，断言：① 返回 dict **逐字段等于**未引入本功能前的结果（`extractor=="trafilatura"`）；② **断言 `sys.modules` 里没有 `crawl4ai`**（证明零 import）；③ `AsyncWebCrawler` 工厂调用次数 == 0。这是把"字节级不变"变成自动化可证伪断言。
- 回归：`backend && python -m pytest tests/ -k "research or search or scoring or extract" -v` 全绿。

### WI-6 真机 E2E（windows-mcp，按项目纪律）
> **★ 前置（二轮强化）**：真机测的是 worktree 代码，必须按项目 CLAUDE.md「坑 #8」设
> `DESKPET_BACKEND_DIR=<worktree>/backend` + `DESKPET_PYTHON=<装了 crawl4ai 的 .venv>`，
> 且日志确认 `[backend_launch] Dev python=... backend_dir=<worktree>`（否则跑的是旧冻结 exe，
> 冻结包里 crawl4ai 不可用 → 测了等于白测，会假性 FAIL）。crawl4ai 须先在该 `.venv` 装好 + `crawl4ai-setup`。
- 开 `[research].crawl4ai=true` 重启桌宠 → 深度调研一个"内容靠 JS 渲染"的主题 → 报告引用里出现该 JS 站正文（原来抓空壳丢源）。
- 关 `crawl4ai` → 同主题回落原行为（证明开关 + 降级）。
- **★ 可观测证据锚点（二轮明确）**：适配器命中时 emit 结构化日志 `event="crawl4ai_extract" url=... chars=... ms=...`（structlog → stderr → tauri dev log）；pipeline 替换时该源 `extractor="crawl4ai"` 出现在 `research_run` 返回的 citation/coverage 或日志里。E2E PASS 判定 = **grep 到该锚点 + 报告引用里确有该 JS 站**，而非仅"报告看起来更全"。
- 截图 + 抓日志归档 `plans/manual-results-2026-06-16-crawl4ai/`，按项目报告格式（坐标/动作/截图/log 证据/判定）。

---

## 5. 配置项（`config.toml [research]`）

> 注意：出厂 `config.toml` 目前**没有 `[research]` 段**，真实默认值来自代码
> `_research_raw().get("crawl4ai", False)`。下面的 toml 是给用户/文档看的样例，
> 写进出厂 config 是文档作用，不改变代码默认。

```toml
[research]
# Crawl4AI 本地真浏览器渲染抓取(治 JS/SPA 空壳站)。默认关:底层 Playwright+Chromium
# ~100-160MB,不进基础安装包。⚠️ 当前仅【源码/dev 运行模式】可用,冻结安装包暂不可用
# (未列入 PyInstaller spec,见 §10 follow-up)。中国可用(本地渲染)。
crawl4ai = false
# 浏览器来源: "system"(复用已装 Chrome/Edge,免下载,首选) | "chromium"(走自建 COS 下载)
crawl4ai_browser = "system"
# 单页渲染超时(秒),超时降级
crawl4ai_timeout = 20
```

---

## 6. 降级矩阵（best-effort，任何一格失败都不崩）

| 情况 | 行为 |
|---|---|
| `crawl4ai=false`（默认） | 完全跳过，走现有 trafilatura/Jina，**零 import** |
| **冻结安装包内（一轮新增）** | `import crawl4ai` 必失败 → `crawl4ai_available()=False` → 静默跳过（正式用户当前永远走此格，符合"dev-only"范围） |
| `crawl4ai=true` 但库没装（dev 没装 extra） | `crawl4ai_available()=False` → 跳过 + 一次性日志提示 |
| `crawl4ai=true` 但 Chromium 没下好 | 触发下载（system 优先 → COS 兜底）；下载失败 → 跳过降级 |
| 渲染超时/异常 | 返 None → 回落 jina/trafilatura 结果 |
| **单次 research 触发已达上限（一轮新增）** | 不再调 crawl4ai，剩余空壳走 jina/原结果（护 300s 预算） |
| crawl4ai 命中并替换 | **跳过 jina**（触发去重，避免双重型兜底叠加超时） |
| `crawl4ai_browser=system` 但用户没装 Chrome/Edge | 尝试 COS 下载 chromium，仍失败则跳过降级 |

---

## 7. 验收标准（DoD）

1. `crawl4ai=false`（出厂）时：**抓取链路字节级不变** —— 由 `test_crawl4ai_flag_off.py` 自动断言（返回 dict 逐字段相等 + `sys.modules` 无 `crawl4ai` + 工厂调用 0 次，见 WI-5），不靠肉眼。
2. `crawl4ai=true` + 浏览器就绪（**dev 模式**）：JS 空壳站能抓到正文 —— 真机 E2E **grep 到 `event="crawl4ai_extract"` 日志锚点** + 报告引用里确有该 JS 站（原来丢的源）。
3. 任何失败路径都**降级不崩**（库缺/浏览器缺/超时/异常/触发超限）—— 单测覆盖每条降级矩阵格子。
4. `[research].crawl4ai` 开关真生效（走 `_research_raw()`，复测开/关行为不同 + on 但 mock 不可用时也跳过）。
5. 单测 + 回归全绿；真机 windows-mcp E2E 截图 + 日志归档（确认跑的是 worktree backend，非冻结 exe）。
6. 基础 NSIS 安装包体积**不因本功能增大**：spec 未加 `crawl4ai`/`playwright`，`backend/dist` 构建产物大小与上一版一致（diff `du` 截图）。
7. **超时不退化**：deep 档 + crawl4ai on 的真机一次完整跑 **不触发 `research_run` 300s tool_timeout**（抓 wall-clock，对照 R4 并发上限 + 触发计数生效）。
8. **范围诚实**：plan 与代码注释明确标注"本期 crawl4ai 仅 dev 可用，正式冻结包不可用"，不误导用户以为装了正式包就能用。

---

## 8. 风险与待定问题（评审后状态）

- **R1 体积/价值比 → ★ 升级为 WI-(-1) 前置 GATE（二轮）**：Crawl4AI 背 Chromium（100-160MB）+ 在冻结包里**当前根本用不了**（只 dev 可用）→ 对"让正式用户受益"这个目标，**性价比存疑**。本仓已有 `browser_use_tool.py` 证明 Tauri 路线之外还有 browser-use 路线，且 Tauri 自带 WebView2 是**零体积**的同能力候选。
  - **决策（二轮）**：在 WI-0 之前先做一个 **0.5 天 WebView 渲染 POC** —— 用 Tauri 现成 WebView2 加载一个 JS 站、`eval` 抓 `document.body.innerText`/`outerHTML` 回传 backend，看能否拿到渲染后正文。
  - **GATE 判据**：若 WebView POC 能稳定拿到正文 → **强烈建议把它做成首选档**（零体积、正式包可用、中国天然可用），Crawl4AI 降为 dev-only 高级档；若 WebView POC 受限（跨进程 eval 回传链路复杂/拿不全 SPA 内容）→ 按本 plan 推进 Crawl4AI。**用户已指定方向是 Crawl4AI，故此 GATE 不阻断，但结论要回写给用户做最终取舍**（不是闷头实现 dev-only 的重方案）。
- **R2 中国下载 Chromium**：第三方镜像（`PLAYWRIGHT_DOWNLOAD_HOST`）**默认不信任**（`model_provisioner` 已因镜像不稳改自建 COS）。WI-0 实测，不稳即走 system 浏览器 + COS 托管 Chromium。
- **R3 system 浏览器 CDP**：中国 Windows 很多人只有 360/QQ 浏览器或纯 WebView2，`crawl4ai_browser=system` 命中 Chrome/Edge channel 的比例需 WI-0 量；命中率低则更印证 R1 的 WebView2 路线（WebView2 是 Win10+ 几乎必装的系统组件，命中率远高于独立 Chrome/Edge）。**这条本身就是 WebView 方案更优的论据。**
- **R4 异步/资源 → 已定（一轮）**：单例 crawler + 复用 context + `Semaphore(≤2)` 页级限流（见 §2 决策表）；不每 URL 新建，不无上限并发。WI-0 量耗时验证预算。
- **R5 触发频率 → 已定（二轮）**：`<300字` 阈值加"原始 HTML 须 >20KB"+"单次 research 触发计数上限"双闸，避免正常短页误触发 + 护超时预算（见 WI-3）。
- **R6 与 Jina 次序/共存 → 已定（一轮）**：crawl4ai 在 jina 之前；crawl4ai 命中即**跳过** jina（触发去重，不双跑）。

---

## 9. 关键来源
- Crawl4AI: https://github.com/unclecode/crawl4ai （AsyncWebCrawler / BrowserConfig / CDP 复用系统浏览器）
- 现有抓取链路: `backend/deskpet/tools/research_tools.py::default_extract`（trafilatura + Jina 两级；`_JINA_MIN_CHARS=300`）
- **★ 必读范本（一轮发现）**: `backend/deskpet/tools/browser_use_tool.py` —— 已有的 browser-use+Playwright+Chromium 工具，懒 import / flag-off 零开销 / 后台线程+`asyncio.run` / 首用下 Chromium / job 化 全都示范过
- 外置下载基建（**真实模块**）: `backend/deskpet/model_provisioner.py`（`ProvisionStatus` + COS 直下 + `model_provision_status` control-WS 事件）；**不是** `provisioning/` 目录
- 外置下载先例: [`nsis-model-externalization`](../2026-06-05-nsis-model-externalization/PLAN.md)
- 冻结打包: `backend/deskpet-backend.spec`（`hiddenimports`，**未含** crawl4ai/playwright/browser_use → 冻结包不可 import）
- research_run 工具超时已 300s 且 deep 档慢网逼近: commit `ee38fc4`（TC-P2-03 实证）
- `[research]` 开关读取(务必走 `_research_raw()`): commit `2b7baa1`
- 直连源 best-effort 降级先例: `backend/deskpet/tools/research_sources.py`（cninfo/openstd/edgar 失败返空不抛）
- 轻量替代(R1 评审重点): 复用 Tauri WebView2/WebKit 渲染（`eval`），零体积、正式包可用

---

## 10. Follow-up（本期不做，但必须显式记录）

- **F1 让正式冻结包也能用 crawl4ai**：把 crawl4ai + 依赖作为**外置 wheel 包**，首次开启时运行时
  `pip install --target <user_dir>` 并 `sys.path` 注入（类比 model 外置，但对象是 Python 包）。
  涉及离线 wheel 镜像、ABI/平台匹配、`sys.path` 卫生，复杂度高 → 单列一期。**在此之前，crawl4ai 仅 dev 可用。**
- **F2 WebView2 渲染档**：若 R1 的 WebView POC 通过，做成 `crawl4ai_browser=webview`（实为 Tauri WebView eval 渲染，绕开 Chromium）零体积档，正式包可用 —— 很可能比 crawl4ai 本体更该优先落地。

---

## 评审迭代记录

> 评审方法：对照真实代码（非凭空），两轮对抗式挑战 + 就地修订。不推翻"做 Crawl4AI"的总方向，
> 目标是**强化落地性 + 暴露真风险**。

### 第 1 轮 — 可行性与硬伤

读了 `research_tools.py`(default_extract/_research_raw/_handle_research_run 及 300s 注册)、
`browser_use_tool.py`、`model_provisioner.py`、`deskpet-backend.spec`、`research_sources.py`、
`nsis-model-externalization/PLAN.md`，并实查了 venv（playwright 包未装、无 ms-playwright Chromium、
browser_use 已装）后，揪出的关键问题与改动：

1. **冻结包根本 import 不到 crawl4ai（最大认知偏差）**。生产 backend 是 PyInstaller 冻结 exe，
   无 site-packages，spec 的 `hiddenimports` 没有 crawl4ai/playwright/browser_use。
   → 改：在 §2 加「★ frozen 不可 import」硬约束，明确**本期 crawl4ai 仅 dev 可用**，正式包用上需 F1 外置 wheel（§10）；
   DoD#8 要求 plan/注释诚实标注；降级矩阵加"冻结包内"格子；WI-0 加实测项 #2、WI-6 加"确认非冻结 exe"前置。
2. **已有 `browser_use_tool.py` 把所有难题解过了**，plan 却当全新问题处理（"待定/虚"）。
   → 改：顶部加「评审关键前置」指向它；§3.2 加「强制复用范本」；§9 列为必读范本。asyncio/懒 import/下 Chromium 全部对齐它。
3. **下载镜像假设过于乐观**。`model_provisioner` 注释明确"hf-mirror 等第三方镜像实测不稳，已改自建 COS"。
   → 改：§2 + WI-2 把下载源优先级改为 **system 浏览器免下载（首选）→ 自建 COS 托管 Chromium → 第三方镜像（仅 WI-0 实测稳才用）**；R2 改为默认不信任镜像。
4. **超时预算会被顶穿**。research_run 工具上限已 300s 且 deep 档慢网实测逼近（ee38fc4）；
   default_extract 对所有候选 URL **并发** → crawl4ai 会同时起多个浏览器页。
   → 改：§2 把 R4 从"待定"升为**必决**（单例 crawler + `Semaphore(≤2)`）；加"单次 research 触发计数上限"；
   WI-0 加超时预算实测项；DoD#7 要求真机不触发 300s 超时。
5. **asyncio 共存没说清**。default_extract 在宿主事件循环里被 await。
   → 改：§3.2 加 asyncio 决策（直接 await，不需开线程；但 WI-0 #3 必须实测 crawl4ai 不内部 `asyncio.run`，冲突则退回线程模式）。
6. **`provisioning/` 目录不存在**（plan 笔误）+ **出厂 config.toml 无 `[research]` 段**（默认在代码）。
   → 改：WI-2 改指 `model_provisioner.py`；§2/§5/WI-4 澄清 config 默认来源是代码不是 toml。

### 第 2 轮 — 完成度与落地细节

在第 1 轮修订稿上再挑测试可验证性、DoD 可证伪、可观测证据、误触发/去重落地、R1 取舍：

1. **DoD#1「字节级不变」无法肉眼证伪** → 改：WI-5 新增 `test_crawl4ai_flag_off.py` 自动断言
   （返回 dict 逐字段相等 + `sys.modules` 无 crawl4ai + 工厂调用 0 次）；DoD#1 改为引用该测试。
2. **单测「mock 怎么写」太虚 + live smoke 选站不确定** → 改：WI-5 写清 monkeypatch 懒 import 符号/注入工厂的 mock 方式、
   每条降级矩阵都要单测、live smoke 钉死选确定性公开 SPA（`spa.smashing.work`），避免选会改版/被墙/需登录的站。
3. **误触发与去重只是 R5/R6 问句，没落地** → 改：WI-3 落地双闸（HTML>20KB 才触发 + 单次 research 触发计数上限）
   + crawl4ai 命中即跳过 jina（触发去重，防双重型兜底叠加超时），并加对应单测。
4. **真机 E2E 没有明确"PASS 锚点"** → 改：WI-6 钉死结构化日志锚点 `event="crawl4ai_extract"`，
   PASS = grep 到锚点 + 报告确含该 JS 站；并补 worktree backend 前置（防测到旧冻结 exe）。
5. **R1（WebView 更轻替代）应升级为前置判定而非脚注** → 改：R1 升级为 WI-(-1) 的 **0.5 天 WebView POC GATE**，
   结论回写用户做最终取舍（鉴于 crawl4ai 当前 dev-only + 重，WebView2 零体积且正式包可用，性价比可能反超）；
   新增 §10 F1/F2 follow-up 记录"正式包可用"与"WebView 档"两条未来线。
